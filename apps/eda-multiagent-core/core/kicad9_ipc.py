"""KiCad 9 IPC API (Protobuf + NNG) Bridge & Sync-Payload-Generator (Phase 7)."""

from __future__ import annotations

import importlib.util
import os
from typing import Any, Dict

from core.export_manifest import summarize_board
from core.gerber_export import detect_kicad_cli
from core.schema import BoardState


def detect_kicad9_ipc() -> Dict[str, Any]:
    """Prueft rein lesend auf `kicad-python` (`kipy`) und lokale KiCad-9-IPC-Umgebung."""
    kipy_installed = importlib.util.find_spec("kipy") is not None
    cli = detect_kicad_cli()
    socket_hint = (
        r"\\.\pipe\kicad\api.sock" if os.name == "nt" else "/tmp/kicad/api.sock"
    )
    return {
        "kipy_installed": kipy_installed,
        "kicad_cli": cli,
        "ipc_transport": "NNG + Protocol Buffers (KiCad 9 IPC API)",
        "default_socket": socket_hint,
        "live_session_ready": bool(kipy_installed and cli.get("available")),
    }


def build_kicad9_ipc_sync_payload(board: BoardState) -> Dict[str, Any]:
    """Baut einen deterministischen, Protobuf-kompatiblen KiCad-9-Board-Sync-Payload."""
    return {
        "protocol": "kicad.ipc.v9.board_sync",
        "board_outline_mm": {"width": float(board.width), "height": float(board.height)},
        "summary": summarize_board(board),
        "footprints": [
            {
                "reference": cid,
                "footprint": board.components[cid].footprint,
                "value": board.components[cid].value,
                "position_nm": {
                    "x": int(round(board.components[cid].x * 1_000_000)),
                    "y": int(round(board.components[cid].y * 1_000_000)),
                },
                "rotation_deg": float(board.components[cid].rotation),
            }
            for cid in sorted(board.components.keys())
        ],
        "tracks": [
            {
                "net": tr.net_id,
                "layer": "F.Cu" if tr.layer.upper() == "TOP" else "B.Cu",
                "width_nm": int(round(tr.width * 1_000_000)),
                "points_nm": [
                    {"x": int(round(x * 1_000_000)), "y": int(round(y * 1_000_000))}
                    for x, y in tr.path
                ],
            }
            for tr in board.traces
        ],
        "vias": [
            {
                "net": v.net_id,
                "position_nm": {
                    "x": int(round(v.x * 1_000_000)),
                    "y": int(round(v.y * 1_000_000)),
                },
                "diameter_nm": int(round(v.diameter * 1_000_000)),
            }
            for v in board.vias
        ],
    }
