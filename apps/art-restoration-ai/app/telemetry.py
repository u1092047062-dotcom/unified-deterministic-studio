"""Strukturiertes rotierendes Logging & JSONL-Telemetrie fuer ART-Restoration-AI.

Nutzt das gemeinsame platform_core.telemetry-Modul (aus dem EDA-Stack uebernommen),
mit rotierendem art_pipeline.log (5 MB, 3 Backups), art_runs.jsonl und Secret-Redaktion.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import threading
from typing import Any, Dict, Optional

_PACKAGES_DIR = Path(__file__).resolve().parents[3] / "packages" / "platform_core"
if _PACKAGES_DIR.is_dir() and str(_PACKAGES_DIR) not in sys.path:
    sys.path.insert(0, str(_PACKAGES_DIR))

from platform_core.telemetry import PlatformTelemetryLogger

_LOCK = threading.Lock()
_LOGGER: Optional[PlatformTelemetryLogger] = None
_LOG_DIR: Optional[Path] = None


def resolve_art_log_dir() -> Path:
    env_dir = os.environ.get("ART_LOG_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    return Path(__file__).resolve().parent.parent / "logs"


def get_art_logger() -> PlatformTelemetryLogger:
    global _LOGGER, _LOG_DIR
    desired = resolve_art_log_dir()
    with _LOCK:
        if _LOGGER is None or _LOG_DIR != desired:
            if _LOGGER is not None:
                _LOGGER.close()
            _LOGGER = PlatformTelemetryLogger(
                desired,
                domain="art-restoration-ai",
                log_file_name="art_pipeline.log",
                jsonl_file_name="art_runs.jsonl",
                level=os.environ.get("ART_LOG_LEVEL", "INFO"),
            )
            _LOG_DIR = desired
        return _LOGGER


def reset_art_logger() -> None:
    global _LOGGER, _LOG_DIR
    with _LOCK:
        if _LOGGER is not None:
            _LOGGER.close()
        _LOGGER = None
        _LOG_DIR = None


def record_art_event(
    event: str,
    *,
    status: str = "OK",
    duration_ms: Optional[float] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    try:
        return get_art_logger().log_event(
            event, status=status, duration_ms=duration_ms, details=details
        )
    except OSError:
        return None
