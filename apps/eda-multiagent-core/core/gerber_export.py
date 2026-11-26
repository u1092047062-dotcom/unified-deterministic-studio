"""Deterministischer RS-274X-Gerber- & Excellon-Drill-Exporter fuer eda-multiagent-core.

Erzeugt ohne externe KiCad-Abhaengigkeit fertigungsfaehige Gerber-Layer:
- F_Cu.gtl (Top-Kupfer: Traces + SMD-Pads)
- B_Cu.gbl (Bottom-Kupfer: Traces)
- Edge_Cuts.gm1 (Platinenkontur)
- board.drl (Excellon-Bohrdatei fuer Vias)
Zusaetzlich optionale Erkennung von kicad-cli fuer externe Gegenpruefungen.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Dict, List, Tuple

from core.export_manifest import build_export_manifest, summarize_board
from core.schema import BoardState, Layer

DEFAULT_PAD_DIAMETER_MM = 0.8
OUTLINE_WIDTH_MM = 0.15


def _mm_to_gerber(val_mm: float) -> int:
    """Konvertiert Millimeter in Gerber 4.6-Ganzzahlkoordinaten (1e-6 mm)."""
    return int(round(val_mm * 1_000_000))


def _gerber_header(layer_name: str, apertures: List[Tuple[int, float]]) -> List[str]:
    lines = [
        f"G04 eda-multiagent-core RS-274X Gerber Layer: {layer_name}*",
        "%FSLAX46Y46*%",
        "%MOMM*%",
        "%LPD*%",
    ]
    for d_code, diameter_mm in apertures:
        lines.append(f"%ADD{d_code}C,{diameter_mm:.4f}*%")
    return lines


def _export_copper_layer(board: BoardState, target_layer: str, include_pads: bool) -> str:
    traces = [t for t in board.traces if t.layer.upper() == target_layer.upper() and len(t.path) >= 2]

    widths = sorted({round(float(t.width), 4) for t in traces})
    if include_pads:
        if DEFAULT_PAD_DIAMETER_MM not in widths:
            widths.append(DEFAULT_PAD_DIAMETER_MM)
            widths.sort()
    if not widths:
        widths = [0.25]

    aperture_map: Dict[float, int] = {w: 10 + i for i, w in enumerate(widths)}
    apertures = [(code, w) for w, code in aperture_map.items()]

    lines = _gerber_header(target_layer, apertures)
    lines.append("G01*")

    # Traces als lineare Zuege (D02 Move -> D01 Draw)
    for trace in traces:
        code = aperture_map[round(float(trace.width), 4)]
        lines.append(f"D{code}*")
        x0, y0 = trace.path[0]
        lines.append(f"X{_mm_to_gerber(x0)}Y{_mm_to_gerber(y0)}D02*")
        for x, y in trace.path[1:]:
            lines.append(f"X{_mm_to_gerber(x)}Y{_mm_to_gerber(y)}D01*")

    # SMD-Pads auf F.Cu als Flashes (D03)
    if include_pads:
        pad_code = aperture_map[DEFAULT_PAD_DIAMETER_MM]
        lines.append(f"D{pad_code}*")
        for comp_id in sorted(board.components.keys()):
            comp = board.components[comp_id]
            for pin_id in sorted(comp.pins.keys()):
                pin = comp.pins[pin_id]
                px = float(pin.x) if pin.x is not None else float(comp.x)
                py = float(pin.y) if pin.y is not None else float(comp.y)
                lines.append(f"X{_mm_to_gerber(px)}Y{_mm_to_gerber(py)}D03*")

    lines.append("M02*")
    return "\n".join(lines) + "\n"


def _export_edge_cuts(board: BoardState) -> str:
    w = _mm_to_gerber(board.width)
    h = _mm_to_gerber(board.height)
    lines = _gerber_header("Edge.Cuts", [(10, OUTLINE_WIDTH_MM)])
    lines.extend(
        [
            "G01*",
            "D10*",
            "X0Y0D02*",
            f"X{w}Y0D01*",
            f"X{w}Y{h}D01*",
            f"X0Y{h}D01*",
            "X0Y0D01*",
            "M02*",
        ]
    )
    return "\n".join(lines) + "\n"


def _export_excellon_drill(board: BoardState) -> str:
    vias = sorted(board.vias, key=lambda v: (round(v.diameter, 4), round(v.x, 4), round(v.y, 4)))
    drills = sorted({round(max(float(v.diameter) * 0.5, 0.2), 4) for v in vias})
    tool_map = {d: i + 1 for i, d in enumerate(drills)}

    lines = [
        "M48",
        "; eda-multiagent-core Excellon Drill File",
        "METRIC,TZ",
    ]
    for drill_mm, tool_num in tool_map.items():
        lines.append(f"T{tool_num}C{drill_mm:.4f}")
    lines.append("%")
    lines.append("G90")
    lines.append("G05")

    current_tool = None
    for via in vias:
        drill_mm = round(max(float(via.diameter) * 0.5, 0.2), 4)
        tool_num = tool_map[drill_mm]
        if tool_num != current_tool:
            lines.append(f"T{tool_num}")
            current_tool = tool_num
        lines.append(f"X{via.x:.4f}Y{via.y:.4f}")

    lines.append("M30")
    return "\n".join(lines) + "\n"


def export_gerber_bundle(board: BoardState) -> Dict[str, str]:
    """Erzeugt alle RS-274X-Gerber-Layer und die Excellon-Bohrdatei als deterministisches Dict."""
    return {
        "F_Cu.gtl": _export_copper_layer(board, Layer.TOP.value, include_pads=True),
        "B_Cu.gbl": _export_copper_layer(board, Layer.BOTTOM.value, include_pads=False),
        "Edge_Cuts.gm1": _export_edge_cuts(board),
        "board.drl": _export_excellon_drill(board),
    }


def export_gerber_with_manifest(board: BoardState) -> Dict[str, object]:
    """Erzeugt Gerber-/Drill-Dateien zusammen mit deterministischem SHA-256-Provenienz-Manifest."""
    files = export_gerber_bundle(board)
    manifest = build_export_manifest(files, summarize_board(board))
    return {
        "files": files,
        "manifest": manifest,
    }


def detect_kicad_cli() -> Dict[str, object]:
    """Prueft rein lesend, ob kicad-cli lokal im PATH verfuegbar ist."""
    exe = shutil.which("kicad-cli")
    if not exe:
        return {"available": False, "path": None, "version": None}
    try:
        res = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        version = res.stdout.strip() or res.stderr.strip() or None
        return {"available": res.returncode == 0, "path": exe, "version": version}
    except OSError:
        return {"available": False, "path": exe, "version": None}
