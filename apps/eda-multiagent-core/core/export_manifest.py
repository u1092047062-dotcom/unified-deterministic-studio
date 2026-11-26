"""SHA-256-Export-Provenienz-Manifest fuer Hardware-Fertigungsdaten.

Uebertraegt das kryptografische Provenienz-Muster auf EDA-Exports
(KiCad .kicad_pcb, 3D-STEP, OpenSCAD-Gehaeuse, JLCPCB BOM & CPL).
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Dict, Mapping, Optional, Union

from core.schema import BoardState

MANIFEST_SCHEMA_VERSION = "1.0"


def summarize_board(board: BoardState) -> Dict[str, Any]:
    """Extrahiert deterministische Board-Metadaten fuer ein Export-Manifest."""
    return {
        "width_mm": float(board.width),
        "height_mm": float(board.height),
        "component_count": len(board.components),
        "net_count": len(board.nets),
        "trace_count": len(board.traces),
        "via_count": len(board.vias),
        "copper_pour_count": len(board.copper_pours),
        "drc_error_count": len(board.errors),
    }


def _to_bytes(content: Union[bytes, str]) -> bytes:
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    raise TypeError(f"Artefakt-Inhalt muss bytes oder str sein, nicht {type(content).__name__}")


def build_export_manifest(
    artifacts: Mapping[str, Union[bytes, str]],
    board_summary: Mapping[str, Any],
    *,
    generated_at_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Erstellt ein deterministisches SHA-256-Provenienz-Manifest fuer Export-Artefakte.

    Parameter:
        artifacts: Mapping von Artefakt-Dateinamen auf Rohbytes oder UTF-8-Text.
        board_summary: Metadaten des Boards (Abmessungen, Bauteil-, Netz-, Via-, DRC-Zahlen).
        generated_at_utc: Optionaler UTC-Zeitstempel (ISO-8601); standardmaessig aktuelle UTC-Zeit.
    """
    timestamp = generated_at_utc or datetime.now(timezone.utc).isoformat()
    normalized_summary = dict(board_summary)

    artifact_entries: Dict[str, Dict[str, Any]] = {}
    total_size_bytes = 0
    for name in sorted(artifacts.keys()):
        raw = _to_bytes(artifacts[name])
        digest = hashlib.sha256(raw).hexdigest()
        size = len(raw)
        total_size_bytes += size
        artifact_entries[name] = {
            "sha256": digest,
            "size_bytes": size,
        }

    # Deterministischer Gesamt-Hash ueber Artefakt-Hashes und Board-Summary (ohne Zeitstempel)
    canonical_core = json.dumps(
        {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "board_summary": normalized_summary,
            "artifacts": artifact_entries,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    manifest_sha256 = hashlib.sha256(canonical_core).hexdigest()

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at_utc": timestamp,
        "board_summary": normalized_summary,
        "artifact_count": len(artifact_entries),
        "total_size_bytes": total_size_bytes,
        "artifacts": artifact_entries,
        "manifest_sha256": manifest_sha256,
    }
