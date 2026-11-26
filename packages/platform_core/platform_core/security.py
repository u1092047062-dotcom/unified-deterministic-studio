"""Fail-Closed Pfad-Kanonisierung und statischer Secret-Scanner."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable, List, Optional, Tuple

_SECRET_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("telegram_bot_token", re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b")),
    ("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)


def validate_safe_path(
    raw_path: str | Path,
    *,
    base_dir: Optional[Path] = None,
    allowed_extensions: Optional[Iterable[str]] = None,
    must_exist: bool = False,
) -> Path:
    """Validiert Dateipfade gegen Path-Traversal, .env-Zugriffe und unerlaubte Endungen."""
    raw_str = str(raw_path).strip()
    if not raw_str:
        raise ValueError("Pfad darf nicht leer sein")

    candidate = Path(raw_str)
    if ".." in candidate.parts:
        raise ValueError("Relative Pfadnavigation ('..') ist nicht zulaessig")

    name_lower = candidate.name.lower()
    if name_lower == ".env" or name_lower.startswith(".env."):
        raise ValueError("Zugriff auf .env-Konfigurationsdateien ist gesperrt")

    if allowed_extensions is not None:
        normalized = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in allowed_extensions}
        if candidate.suffix.lower() not in normalized:
            raise ValueError(f"Dateiendung '{candidate.suffix}' ist nicht zulaessig")

    resolved = candidate.resolve()
    if base_dir is not None:
        base_resolved = Path(base_dir).resolve()
        if resolved != base_resolved and base_resolved not in resolved.parents:
            raise ValueError("Aufgeloester Pfad verlaesst das erlaubte Basisverzeichnis")

    if must_exist and not resolved.is_file():
        raise FileNotFoundError(f"Datei existiert nicht: {resolved}")

    return resolved


def scan_text_for_secrets(text: str) -> List[str]:
    """Liefert eine Liste erkannter Secret-Muster-Namen im uebergebenen Text."""
    hits: List[str] = []
    for name, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            hits.append(name)
    return hits
