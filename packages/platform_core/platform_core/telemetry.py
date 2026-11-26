"""Gemeinsames rotierendes Logging & JSONL-Telemetrie mit Fail-Closed Secret-Redaktion."""

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

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
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
    re.compile(r"\b\d{7,12}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


def redact_string(value: str) -> str:
    sanitized = value
    for pattern in _SECRET_VALUE_PATTERNS:
        sanitized = pattern.sub("[REDACTED]", sanitized)
    return sanitized


def sanitize_payload(data: Any) -> Any:
    """Entfernt potenzielle Secrets, Tokens und Zugangsdaten rekursiv."""
    if isinstance(data, dict):
        cleaned: Dict[str, Any] = {}
        for key, val in data.items():
            key_str = str(key)
            if any(frag in key_str.lower() for frag in _SENSITIVE_KEY_FRAGMENTS):
                cleaned[key_str] = "[REDACTED]"
            else:
                cleaned[key_str] = sanitize_payload(val)
        return cleaned
    if isinstance(data, (list, tuple)):
        return [sanitize_payload(item) for item in data]
    if isinstance(data, str):
        return redact_string(data)
    return data


class PlatformTelemetryLogger:
    """Schreibt rotierende Text-Logs und strukturierte JSONL-Laufdaten fuer jede Domain-App."""

    def __init__(
        self,
        log_dir: os.PathLike[str] | str,
        *,
        domain: str = "platform",
        log_file_name: str = "pipeline.log",
        jsonl_file_name: str = "runs.jsonl",
        level: str = "INFO",
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
    ) -> None:
        self.domain = domain
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / log_file_name
        self.jsonl_file = self.log_dir / jsonl_file_name
        self._lock = threading.Lock()

        self._logger = logging.getLogger(f"platform.telemetry.{domain}.{id(self)}")
        self._logger.setLevel(getattr(logging, level.strip().upper(), logging.INFO))
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

    def log_event(
        self,
        event: str,
        *,
        status: str = "OK",
        duration_ms: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        clean_details = sanitize_payload(details) if details else {}
        record: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "domain": self.domain,
            "event": redact_string(str(event)),
            "status": redact_string(str(status)),
        }
        if duration_ms is not None:
            record["duration_ms"] = round(float(duration_ms), 2)
        if clean_details:
            record["details"] = clean_details

        self._append_jsonl(record)

        msg = f"domain={self.domain} event={record['event']} status={record['status']}"
        if duration_ms is not None:
            msg += f" duration_ms={record['duration_ms']:.2f}"
        if clean_details:
            msg += f" details={json.dumps(clean_details, ensure_ascii=False, sort_keys=True)}"

        if status.upper() in ("OK", "INFO", "COMPLETED", "QUEUED", "CONNECTED", "DISCONNECTED"):
            self._logger.info(redact_string(msg))
        elif status.upper() in ("WARN", "WARNING", "DRC_FAILED"):
            self._logger.warning(redact_string(msg))
        else:
            self._logger.error(redact_string(msg))

        return record

    def close(self) -> None:
        with self._lock:
            self._logger.removeHandler(self._handler)
            self._handler.flush()
            self._handler.close()
