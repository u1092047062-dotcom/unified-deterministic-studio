"""Atomarer, Config-Hash-gesicherter Job-Store (aus ART-Restoration-AI verallgemeinert).

Garantiert:
- Atomare JSON-Schreibvorgaenge (Write-to-Temp + Replace), keine korrupten Teilzustaende.
- Unveraenderlicher Konfigurations-Hash pro Job-ID: Resume mit geaenderter Eingabe wird abgewiesen.
- Einheitliches Job-Schema fuer EDA- und Media-Restoration-Pipelines.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Mapping, Optional

_SAFE_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    """Schreibt JSON atomar ueber eine temporaere Nachbardatei."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def compute_config_hash(config: Mapping[str, Any]) -> str:
    """Berechnet einen deterministischen SHA-256-Hash ueber eine Job-Konfiguration."""
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or ".." in job_id or not _SAFE_JOB_ID_RE.match(job_id):
        raise ValueError(f"Ungueltige Job-ID: {job_id!r}")
    return job_id


class JobStore:
    """Dateibasierter, atomarer Job-Status- und Provenienz-Speicher."""

    def __init__(self, root_dir: Path | str) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _job_path(self, job_id: str) -> Path:
        clean_id = _validate_job_id(job_id)
        candidate = (self.root_dir / f"{clean_id}.json").resolve()
        if self.root_dir.resolve() not in candidate.parents:
            raise ValueError("Job-Pfad verlaesst den konfigurierten JobStore")
        return candidate

    def create_job(
        self,
        job_id: str,
        *,
        domain: str,
        config: Mapping[str, Any],
        status: str = "queued",
        initial_payload: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        path = self._job_path(job_id)
        if path.exists():
            raise FileExistsError(f"Job '{job_id}' existiert bereits")
        now = datetime.now(timezone.utc).isoformat()
        record: Dict[str, Any] = {
            "job_id": job_id,
            "domain": domain,
            "status": status,
            "config": dict(config),
            "config_sha256": compute_config_hash(config),
            "created_at": now,
            "updated_at": now,
        }
        if initial_payload:
            record.update(dict(initial_payload))
        atomic_write_json(path, record)
        return record

    def load_job(self, job_id: str) -> Dict[str, Any]:
        path = self._job_path(job_id)
        if not path.is_file():
            raise FileNotFoundError(f"Job '{job_id}' nicht gefunden")
        return json.loads(path.read_text(encoding="utf-8"))

    def validate_resume(self, job_id: str, config: Mapping[str, Any]) -> Dict[str, Any]:
        """Prueft fail-closed, ob ein Job mit exakt identischer Konfiguration fortgesetzt werden darf."""
        state = self.load_job(job_id)
        if state.get("status") == "completed":
            raise ValueError("Abgeschlossener Job muss nicht fortgesetzt werden")
        expected_hash = state.get("config_sha256")
        actual_hash = compute_config_hash(config)
        if expected_hash != actual_hash:
            raise ValueError(
                "Konfigurations-Hash weicht ab; Resume mit geaenderten Parametern ist gesperrt"
            )
        return state

    def update_job(self, job_id: str, updates: Mapping[str, Any]) -> Dict[str, Any]:
        path = self._job_path(job_id)
        state = self.load_job(job_id)
        protected = {"job_id", "config", "config_sha256", "created_at"}
        for key, value in updates.items():
            if key in protected:
                raise ValueError(f"Geschuetztes Job-Feld '{key}' darf nicht ueberschrieben werden")
            state[key] = value
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(path, state)
        return state

    def list_jobs(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        files = sorted(
            self.root_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]
        results: List[Dict[str, Any]] = []
        for path in files:
            try:
                results.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return results
