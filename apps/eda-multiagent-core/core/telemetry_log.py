"""Strukturiertes Pipeline- und Server-Logging fuer eda-multiagent-core.

Nutzt ausschliesslich die Python-Standardbibliothek:
- Rotierendes Text-Log: <log_dir>/eda_pipeline.log (max. 5 MB, 3 Backups)
- Strukturierte JSONL-Laufdaten: <log_dir>/eda_runs.jsonl
- Fail-Closed Secret-Redaktion: Tokens, API-Keys und Zugangsdaten werden
  niemals im Klartext protokolliert.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Dict, Optional

DEFAULT_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
DEFAULT_BACKUP_COUNT = 3

_SENSITIVE_KEY_FRAGMENTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "cookie",
    "private_key",
)

_SECRET_VALUE_PATTERNS = (
    # Telegram bot token: <digits>:<30+ chars>
    re.compile(r"\b\d{7,12}:[A-Za-z0-9_-]{20,}\b"),
    # Generic Bearer / OpenAI / GitHub tokens
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
)


def _redact_string(value: str) -> str:
    sanitized = value
    for pattern in _SECRET_VALUE_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized


def sanitize_payload(data: Any) -> Any:
    """Entfernt potenzielle Secrets und Roh-Tokens rekursiv aus Log-Daten."""
    if isinstance(data, dict):
        cleaned: Dict[str, Any] = {}
        for key, val in data.items():
            key_str = str(key)
            key_lower = key_str.lower()
            if any(frag in key_lower for frag in _SENSITIVE_KEY_FRAGMENTS):
                cleaned[key_str] = "[REDACTED]"
            else:
                cleaned[key_str] = sanitize_payload(val)
        return cleaned
    if isinstance(data, (list, tuple)):
        return [sanitize_payload(item) for item in data]
    if isinstance(data, str):
        return _redact_string(data)
    return data


def resolve_log_dir(log_dir: Optional[os.PathLike[str] | str] = None) -> Path:
    """Ermittelt das Log-Verzeichnis aus Parameter, EDA_LOG_DIR oder Projekt-Default."""
    if log_dir is not None:
        return Path(log_dir)
    env_dir = os.environ.get("EDA_LOG_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    return Path(__file__).resolve().parent.parent / "logs"


def _parse_log_level(level: Optional[str] = None) -> int:
    raw = (level or os.environ.get("EDA_LOG_LEVEL", "INFO")).strip().upper()
    return getattr(logging, raw, logging.INFO)


class TelemetryLogger:
    """Schreibt rotierende Pipeline-Logs und strukturierte JSONL-Laufdaten."""

    def __init__(
        self,
        log_dir: Optional[os.PathLike[str] | str] = None,
        *,
        level: Optional[str] = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
    ) -> None:
        self.log_dir = resolve_log_dir(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "eda_pipeline.log"
        self.jsonl_file = self.log_dir / "eda_runs.jsonl"
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._lock = threading.Lock()

        logger_name = f"eda.telemetry.{id(self)}"
        self._logger = logging.getLogger(logger_name)
        self._logger.setLevel(_parse_log_level(level))
        self._logger.propagate = False

        self._handler = RotatingFileHandler(
            filename=str(self.log_file),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        formatter.converter = time.gmtime
        self._handler.setFormatter(formatter)
        self._logger.addHandler(self._handler)

    def _append_jsonl(self, record: Dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with self.jsonl_file.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def log_pipeline_run(
        self,
        *,
        status: str,
        attempts: int,
        duration_ms: float,
        components: int = 0,
        nets: int = 0,
        traces: int = 0,
        vias: int = 0,
        drc_errors: int = 0,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Protokolliert einen DesignPipeline-Lauf im Text-Log und als JSONL."""
        record: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "pipeline_run",
            "status": str(status),
            "attempts": int(attempts),
            "duration_ms": round(float(duration_ms), 2),
            "components": int(components),
            "nets": int(nets),
            "traces": int(traces),
            "vias": int(vias),
            "drc_errors": int(drc_errors),
        }
        if extra:
            record["extra"] = sanitize_payload(extra)

        self._append_jsonl(record)

        summary = (
            f"pipeline_run status={record['status']} attempts={record['attempts']} "
            f"duration_ms={record['duration_ms']:.2f} components={record['components']} "
            f"nets={record['nets']} traces={record['traces']} vias={record['vias']} "
            f"drc_errors={record['drc_errors']}"
        )
        if record["status"] in ("OK", "IN_PROGRESS"):
            self._logger.info(_redact_string(summary))
        else:
            self._logger.warning(_redact_string(summary))

        return record

    def log_event(
        self,
        event: str,
        *,
        status: str = "OK",
        duration_ms: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Protokolliert ein Server- oder Export-Ereignis im Text-Log und als JSONL."""
        clean_details = sanitize_payload(details) if details else {}
        record: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": _redact_string(str(event)),
            "status": _redact_string(str(status)),
        }
        if duration_ms is not None:
            record["duration_ms"] = round(float(duration_ms), 2)
        if clean_details:
            record["details"] = clean_details

        self._append_jsonl(record)

        msg = f"event={record['event']} status={record['status']}"
        if duration_ms is not None:
            msg += f" duration_ms={record['duration_ms']:.2f}"
        if clean_details:
            msg += f" details={json.dumps(clean_details, ensure_ascii=False, sort_keys=True)}"

        if status.upper() in ("OK", "INFO", "CONNECTED", "DISCONNECTED"):
            self._logger.info(_redact_string(msg))
        else:
            self._logger.error(_redact_string(msg))

        return record

    def close(self) -> None:
        """Schliesst Dateihandles sauber (wichtig unter Windows fuer Tests/Rotation)."""
        with self._lock:
            self._logger.removeHandler(self._handler)
            self._handler.flush()
            self._handler.close()


_DEFAULT_LOCK = threading.Lock()
_DEFAULT_LOGGER: Optional[TelemetryLogger] = None
_DEFAULT_LOG_DIR: Optional[Path] = None


def get_telemetry_logger() -> TelemetryLogger:
    """Liefert den prozessweiten TelemetryLogger (reagiert auf Aenderungen an EDA_LOG_DIR)."""
    global _DEFAULT_LOGGER, _DEFAULT_LOG_DIR
    desired_dir = resolve_log_dir()
    with _DEFAULT_LOCK:
        if _DEFAULT_LOGGER is None or _DEFAULT_LOG_DIR != desired_dir:
            if _DEFAULT_LOGGER is not None:
                _DEFAULT_LOGGER.close()
            _DEFAULT_LOGGER = TelemetryLogger(log_dir=desired_dir)
            _DEFAULT_LOG_DIR = desired_dir
        return _DEFAULT_LOGGER


def reset_default_logger() -> None:
    """Schliesst und verwirft den globalen Default-Logger (fuer isolierte Tests)."""
    global _DEFAULT_LOGGER, _DEFAULT_LOG_DIR
    with _DEFAULT_LOCK:
        if _DEFAULT_LOGGER is not None:
            _DEFAULT_LOGGER.close()
        _DEFAULT_LOGGER = None
        _DEFAULT_LOG_DIR = None


def record_pipeline_run(
    *,
    status: str,
    attempts: int,
    duration_ms: float,
    components: int = 0,
    nets: int = 0,
    traces: int = 0,
    vias: int = 0,
    drc_errors: int = 0,
    extra: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Fail-safe Wrapper fuer Pipeline-Aufrufe; bricht bei I/O-Fehlern niemals die Pipeline ab."""
    try:
        return get_telemetry_logger().log_pipeline_run(
            status=status,
            attempts=attempts,
            duration_ms=duration_ms,
            components=components,
            nets=nets,
            traces=traces,
            vias=vias,
            drc_errors=drc_errors,
            extra=extra,
        )
    except OSError:
        return None


def record_server_event(
    event: str,
    *,
    status: str = "OK",
    duration_ms: Optional[float] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Fail-safe Wrapper fuer FastAPI-/WebSocket-Ereignisse."""
    try:
        return get_telemetry_logger().log_event(
            event=event,
            status=status,
            duration_ms=duration_ms,
            details=details,
        )
    except OSError:
        return None
