"""SQLite-WAL Durable Execution Engine mit Kosten-Circuit-Breaker (Phase 4).

Inspiriert von Temporal / Restate:
- Persistiert jeden Workflow-Schritt atomar in einem SQLite-WAL-Journal.
- Bei Absturz oder Neustart werden bereits abgeschlossene Schritte deterministisch
  aus dem Journal wiedergegeben (exakt-einmalige Ausfuehrung pro Schritt-Name).
- Erzwingt Config-Hash-Konsistenz bei Resume und einen harten Kosten-Circuit-Breaker
  (Quality-First: bei Budgetueberschreitung wird sauber pausiert, nie Qualitaet gesenkt).
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable, Dict, List, Optional
import uuid

from platform_core.job_store import compute_config_hash
from platform_core.telemetry import sanitize_payload


class BudgetCircuitBreakerError(RuntimeError):
    """Wird ausgeloest, wenn ein Schritt das harte Workflow-Budget ueberschreiten wuerde."""


class ConfigMismatchError(ValueError):
    """Wird ausgeloest, wenn ein Resume mit abweichender Konfiguration versucht wird."""


class DurableWorkflowEngine:
    """Fehlertolerante, lokal persistierte Durable-Execution-Engine (SQLite WAL)."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=FULL;")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    config_sha256 TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    budget_usd REAL,
                    spent_usd REAL NOT NULL DEFAULT 0.0,
                    trace_id TEXT,
                    created_at_utc TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL,
                    error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS workflow_steps (
                    workflow_id TEXT NOT NULL,
                    step_name TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    input_sha256 TEXT NOT NULL,
                    output_json TEXT,
                    cost_usd REAL NOT NULL DEFAULT 0.0,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    duration_ms REAL NOT NULL DEFAULT 0.0,
                    error_message TEXT,
                    updated_at_utc TEXT NOT NULL,
                    PRIMARY KEY (workflow_id, step_name),
                    FOREIGN KEY (workflow_id) REFERENCES workflows(workflow_id)
                );
                """
            )

    def start_or_resume(
        self,
        *,
        name: str,
        domain: str,
        config: Dict[str, Any],
        workflow_id: Optional[str] = None,
        budget_usd: Optional[float] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Startet einen neuen Workflow oder nimmt einen bestehenden nach Config-Hash-Pruefung wieder auf."""
        wid = workflow_id or f"wf-{uuid.uuid4().hex[:12]}"
        safe_cfg = sanitize_payload(config)
        cfg_hash = compute_config_hash(safe_cfg)
        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workflows WHERE workflow_id = ?", (wid,)
            ).fetchone()
            if row is not None:
                if row["config_sha256"] != cfg_hash:
                    raise ConfigMismatchError(
                        f"Config-Hash-Sperre fuer Workflow '{wid}': "
                        f"erwartet {row['config_sha256'][:12]}, erhalten {cfg_hash[:12]}"
                    )
                if row["status"] != "completed":
                    conn.execute(
                        "UPDATE workflows SET status = 'running', updated_at_utc = ?, error_message = NULL WHERE workflow_id = ?",
                        (now, wid),
                    )
                return self.get_workflow(wid)

            conn.execute(
                """
                INSERT INTO workflows (
                    workflow_id, name, domain, config_sha256, config_json,
                    status, budget_usd, spent_usd, trace_id, created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, 0.0, ?, ?, ?)
                """,
                (
                    wid,
                    name,
                    domain,
                    cfg_hash,
                    json.dumps(safe_cfg, ensure_ascii=False, sort_keys=True),
                    budget_usd,
                    trace_id,
                    now,
                    now,
                ),
            )
        return self.get_workflow(wid)

    def run_step(
        self,
        workflow_id: str,
        step_name: str,
        fn: Callable[[], Any],
        *,
        input_payload: Optional[Dict[str, Any]] = None,
        estimated_cost_usd: float = 0.0,
        max_retries: int = 1,
    ) -> Dict[str, Any]:
        """Fuehrt einen Schritt deterministisch exakt einmal aus; bei Replay wird das Journal-Ergebnis geliefert."""
        input_hash = compute_config_hash(sanitize_payload(input_payload or {"step": step_name}))
        with self._connect() as conn:
            wf = conn.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
            if wf is None:
                raise KeyError(f"Unbekannter Workflow '{workflow_id}'")

            existing = conn.execute(
                "SELECT * FROM workflow_steps WHERE workflow_id = ? AND step_name = ?",
                (workflow_id, step_name),
            ).fetchone()
            if existing is not None and existing["status"] == "completed":
                return {
                    "step_name": step_name,
                    "replayed": True,
                    "attempts": existing["attempts"],
                    "cost_usd": existing["cost_usd"],
                    "output": json.loads(existing["output_json"]) if existing["output_json"] else None,
                }

            # Harter Kosten-Circuit-Breaker vor Ausfuehrung
            budget = wf["budget_usd"]
            spent = float(wf["spent_usd"])
            if budget is not None and (spent + float(estimated_cost_usd)) > float(budget) + 1e-9:
                msg = (
                    f"Budget-Circuit-Breaker bei Schritt '{step_name}': "
                    f"bisher=${spent:.4f} + Schritt=${estimated_cost_usd:.4f} > Budget=${float(budget):.4f}"
                )
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "UPDATE workflows SET status = 'budget_halted', error_message = ?, updated_at_utc = ? WHERE workflow_id = ?",
                    (msg, now, workflow_id),
                )
                conn.commit()
                raise BudgetCircuitBreakerError(msg)

            seq_row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS m FROM workflow_steps WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchone()
            seq = int(existing["seq"]) if existing is not None else int(seq_row["m"]) + 1

        last_exc: Optional[Exception] = None
        attempts = 0
        started = time.perf_counter()
        result: Any = None
        for attempt in range(1, max(1, int(max_retries)) + 1):
            attempts = attempt
            try:
                result = fn()
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc

        duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        now = datetime.now(timezone.utc).isoformat()

        if last_exc is not None:
            err_text = str(last_exc)
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO workflow_steps (
                        workflow_id, step_name, seq, status, input_sha256,
                        output_json, cost_usd, attempts, duration_ms, error_message, updated_at_utc
                    ) VALUES (?, ?, ?, 'failed', ?, NULL, 0.0, ?, ?, ?, ?)
                    """,
                    (workflow_id, step_name, seq, input_hash, attempts, duration_ms, err_text, now),
                )
                conn.execute(
                    "UPDATE workflows SET status = 'failed', error_message = ?, updated_at_utc = ? WHERE workflow_id = ?",
                    (f"{step_name}: {err_text}", now, workflow_id),
                )
            raise last_exc

        safe_out = sanitize_payload(result)
        out_json = json.dumps(safe_out, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO workflow_steps (
                    workflow_id, step_name, seq, status, input_sha256,
                    output_json, cost_usd, attempts, duration_ms, error_message, updated_at_utc
                ) VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    workflow_id,
                    step_name,
                    seq,
                    input_hash,
                    out_json,
                    float(estimated_cost_usd),
                    attempts,
                    duration_ms,
                    now,
                ),
            )
            conn.execute(
                "UPDATE workflows SET spent_usd = spent_usd + ?, updated_at_utc = ? WHERE workflow_id = ?",
                (float(estimated_cost_usd), now, workflow_id),
            )

        return {
            "step_name": step_name,
            "replayed": False,
            "attempts": attempts,
            "cost_usd": float(estimated_cost_usd),
            "duration_ms": duration_ms,
            "output": safe_out,
        }

    def complete_workflow(self, workflow_id: str) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE workflows SET status = 'completed', updated_at_utc = ?, error_message = NULL WHERE workflow_id = ?",
                (now, workflow_id),
            )
        return self.get_workflow(workflow_id)

    def get_workflow(self, workflow_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            wf = conn.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
            if wf is None:
                raise KeyError(f"Unbekannter Workflow '{workflow_id}'")
            steps = conn.execute(
                "SELECT * FROM workflow_steps WHERE workflow_id = ? ORDER BY seq ASC",
                (workflow_id,),
            ).fetchall()
            return {
                "workflow_id": wf["workflow_id"],
                "name": wf["name"],
                "domain": wf["domain"],
                "config_sha256": wf["config_sha256"],
                "config": json.loads(wf["config_json"]),
                "status": wf["status"],
                "budget_usd": wf["budget_usd"],
                "spent_usd": round(float(wf["spent_usd"]), 6),
                "trace_id": wf["trace_id"],
                "created_at_utc": wf["created_at_utc"],
                "updated_at_utc": wf["updated_at_utc"],
                "error_message": wf["error_message"],
                "steps": [
                    {
                        "seq": s["seq"],
                        "step_name": s["step_name"],
                        "status": s["status"],
                        "input_sha256": s["input_sha256"],
                        "cost_usd": s["cost_usd"],
                        "attempts": s["attempts"],
                        "duration_ms": s["duration_ms"],
                        "error_message": s["error_message"],
                        "output": json.loads(s["output_json"]) if s["output_json"] else None,
                    }
                    for s in steps
                ],
            }

    def list_workflows(self, limit: int = 25) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT workflow_id, name, domain, status, budget_usd, spent_usd, updated_at_utc "
                "FROM workflows ORDER BY updated_at_utc DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
            return [dict(r) for r in rows]
