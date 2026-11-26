"""Kryptografische SHA-256-Provenienz fuer Dateien und Fertigungs-/Restaurierungs-Artefakte."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union

MANIFEST_SCHEMA_VERSION = "1.0"


def _to_bytes(content: Union[bytes, str]) -> bytes:
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    raise TypeError(f"Artefakt-Inhalt muss bytes oder str sein, nicht {type(content).__name__}")


def sha256_bytes(content: Union[bytes, str]) -> str:
    """Berechnet den deterministischen SHA-256-Hex-Hash fuer Bytes oder UTF-8-Text."""
    return hashlib.sha256(_to_bytes(content)).hexdigest()


def sha256_file(path: Union[str, Path], *, chunk_size: int = 1024 * 1024) -> str:
    """Berechnet den SHA-256-Hash einer Datei speicherschonend per Streaming."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file_checksum(path: Union[str, Path], expected_sha256: str) -> bool:
    """Prueft fail-closed, ob eine Datei exakt der erwarteten SHA-256-Pruefsumme entspricht."""
    target = Path(path)
    if not target.is_file():
        return False
    return sha256_file(target).lower() == expected_sha256.strip().lower()


def build_provenance_manifest(
    artifacts: Mapping[str, Union[bytes, str]],
    metadata: Mapping[str, Any],
    *,
    domain: str = "generic",
    schema_version: str = MANIFEST_SCHEMA_VERSION,
    generated_at_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Erstellt ein deterministisches SHA-256-Provenienz-Manifest fuer beliebige Artefakte."""
    timestamp = generated_at_utc or datetime.now(timezone.utc).isoformat()
    normalized_meta = dict(metadata)

    artifact_entries: Dict[str, Dict[str, Any]] = {}
    total_size_bytes = 0
    for name in sorted(artifacts.keys()):
        raw = _to_bytes(artifacts[name])
        size = len(raw)
        total_size_bytes += size
        artifact_entries[name] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": size,
        }

    canonical_core = json.dumps(
        {
            "schema_version": schema_version,
            "domain": domain,
            "metadata": normalized_meta,
            "artifacts": artifact_entries,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    return {
        "schema_version": schema_version,
        "domain": domain,
        "generated_at_utc": timestamp,
        "metadata": normalized_meta,
        "artifact_count": len(artifact_entries),
        "total_size_bytes": total_size_bytes,
        "artifacts": artifact_entries,
        "manifest_sha256": hashlib.sha256(canonical_core).hexdigest(),
    }
