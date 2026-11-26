"""KiKit-inspirierter deterministischer PCB-Panelizer (Phase 3).

Erzeugt fertigungsfaehige Nutzen-Geometrien (V-Cut oder Mousebites) inkl.:
- Berechneter Nutzen-Aussenmasse mit umlaufenden Fertigungsraendern (Rails)
- 4 standardisierten Tooling-Holes (z. B. JLCPCB 1.152 mm)
- 3 asymmetrischen Global-Fiducials fuer fehlersichere SMT-Bestueckung
- Deterministischer RS-274X-Panel-Kontur (`Panel_Edge_Cuts.gm1`) und
  Excellon-Tooling-Bohrdatei (`panel_tooling.drl`) inkl. SHA-256-Manifest.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Tuple

from pydantic import BaseModel, Field

from core.export_manifest import build_export_manifest, summarize_board
from core.gerber_export import OUTLINE_WIDTH_MM, _gerber_header, _mm_to_gerber
from core.schema import BoardState


class PanelConfig(BaseModel):
    rows: int = Field(default=2, ge=1, le=20)
    cols: int = Field(default=2, ge=1, le=20)
    rail_width_mm: float = Field(default=5.0, ge=0.0, le=25.0)
    spacing_mm: float = Field(default=2.0, ge=0.0, le=20.0)
    cut_mode: Literal["vcut", "mousebites"] = Field(default="vcut")
    tooling_hole_diameter_mm: float = Field(default=1.152, ge=0.5, le=5.0)
    fiducial_diameter_mm: float = Field(default=1.0, ge=0.5, le=3.0)


def _draw_rect_gerber(x0: float, y0: float, w: float, h: float) -> List[str]:
    gx0, gy0 = _mm_to_gerber(x0), _mm_to_gerber(y0)
    gx1, gy1 = _mm_to_gerber(x0 + w), _mm_to_gerber(y0 + h)
    return [
        f"X{gx0}Y{gy0}D02*",
        f"X{gx1}Y{gy0}D01*",
        f"X{gx1}Y{gy1}D01*",
        f"X{gx0}Y{gy1}D01*",
        f"X{gx0}Y{gy0}D01*",
    ]


def _build_panel_edge_cuts(
    panel_w: float,
    panel_h: float,
    instances: List[Dict[str, Any]],
    board_w: float,
    board_h: float,
    vcut_lines: List[Dict[str, Any]],
) -> str:
    lines = _gerber_header("Panel.Edge.Cuts", [(10, OUTLINE_WIDTH_MM)])
    lines.append("G01*")
    lines.append("D10*")
    # Aeusserer Nutzen-Rahmen
    lines.extend(_draw_rect_gerber(0.0, 0.0, panel_w, panel_h))
    # Einzelplatinen-Konturen innerhalb des Nutzens
    for inst in instances:
        lines.extend(_draw_rect_gerber(inst["x_offset_mm"], inst["y_offset_mm"], board_w, board_h))
    # V-Cut-Ritzlinien ueber die gesamte Panel-Laenge/-Breite
    for vline in vcut_lines:
        lines.append(f"X{_mm_to_gerber(vline['x1'])}Y{_mm_to_gerber(vline['y1'])}D02*")
        lines.append(f"X{_mm_to_gerber(vline['x2'])}Y{_mm_to_gerber(vline['y2'])}D01*")
    lines.append("M02*")
    return "\n".join(lines) + "\n"


def _build_panel_excellon(
    tooling_holes: List[Tuple[float, float]],
    tooling_dia: float,
    mousebite_holes: List[Tuple[float, float]],
    mousebite_dia: float = 0.5,
) -> str:
    lines = [
        "M48",
        "; eda-multiagent-core Panel Tooling & Breakout Drill File",
        "METRIC,TZ",
    ]
    tools: List[Tuple[int, float, List[Tuple[float, float]]]] = []
    if tooling_holes:
        tools.append((1, round(tooling_dia, 4), tooling_holes))
    if mousebite_holes:
        tools.append((len(tools) + 1, round(mousebite_dia, 4), mousebite_holes))

    for t_num, dia, _ in tools:
        lines.append(f"T{t_num}C{dia:.4f}")
    lines.append("%")
    lines.append("G90")
    lines.append("G05")

    for t_num, _, coords in tools:
        lines.append(f"T{t_num}")
        for x, y in coords:
            lines.append(f"X{x:.4f}Y{y:.4f}")

    lines.append("M30")
    return "\n".join(lines) + "\n"


def panelize_board(board: BoardState, config: PanelConfig | None = None) -> Dict[str, Any]:
    """Berechnet einen deterministischen Fertigungsnutzen inkl. Gerber/Excellon und SHA-256-Manifest."""
    cfg = config or PanelConfig()
    if cfg.cut_mode == "mousebites" and cfg.spacing_mm < 1.6:
        raise ValueError("Mousebites erfordern mindestens 1.6 mm Platinenabstand (spacing_mm >= 1.6)")

    bw = float(board.width)
    bh = float(board.height)
    rail = float(cfg.rail_width_mm)
    gap = float(cfg.spacing_mm)

    panel_w = round(2.0 * rail + cfg.cols * bw + max(0, cfg.cols - 1) * gap, 4)
    panel_h = round(2.0 * rail + cfg.rows * bh + max(0, cfg.rows - 1) * gap, 4)

    instances: List[Dict[str, Any]] = []
    idx = 0
    for r in range(cfg.rows):
        for c in range(cfg.cols):
            idx += 1
            x_off = round(rail + c * (bw + gap), 4)
            y_off = round(rail + r * (bh + gap), 4)
            instances.append(
                {
                    "index": idx,
                    "row": r,
                    "col": c,
                    "x_offset_mm": x_off,
                    "y_offset_mm": y_off,
                }
            )

    # 4 Tooling-Holes und 3 asymmetrische Fiducials auf den Fertigungsraendern
    tooling_holes: List[Tuple[float, float]] = []
    fiducials: List[Dict[str, float]] = []
    if rail >= 2.0:
        inset = round(rail / 2.0, 4)
        tooling_holes = [
            (inset, inset),
            (round(panel_w - inset, 4), inset),
            (inset, round(panel_h - inset, 4)),
            (round(panel_w - inset, 4), round(panel_h - inset, 4)),
        ]
        fid_offset = round(min(inset + 4.0, panel_w / 3.0), 4)
        fiducials = [
            {"id": "FID1", "x_mm": fid_offset, "y_mm": inset, "diameter_mm": cfg.fiducial_diameter_mm},
            {"id": "FID2", "x_mm": round(panel_w - fid_offset, 4), "y_mm": inset, "diameter_mm": cfg.fiducial_diameter_mm},
            {"id": "FID3", "x_mm": fid_offset, "y_mm": round(panel_h - inset, 4), "diameter_mm": cfg.fiducial_diameter_mm},
        ]

    vcut_lines: List[Dict[str, Any]] = []
    mousebite_holes: List[Tuple[float, float]] = []

    if cfg.cut_mode == "vcut":
        # Vertikale Ritzlinien
        x_coords = sorted({round(rail + c * (bw + gap), 4) for c in range(cfg.cols)} |
                          {round(rail + c * (bw + gap) + bw, 4) for c in range(cfg.cols)})
        for x in x_coords:
            if 0.0 < x < panel_w:
                vcut_lines.append({"axis": "vertical", "x1": x, "y1": 0.0, "x2": x, "y2": panel_h})
        # Horizontale Ritzlinien
        y_coords = sorted({round(rail + r * (bh + gap), 4) for r in range(cfg.rows)} |
                          {round(rail + r * (bh + gap) + bh, 4) for r in range(cfg.rows)})
        for y in y_coords:
            if 0.0 < y < panel_h:
                vcut_lines.append({"axis": "horizontal", "x1": 0.0, "y1": y, "x2": panel_w, "y2": y})
    else:
        # Mousebite-Bohrungscluster (je 5 x 0.5 mm Bohrungen im 0.8 mm Raster) zwischen benachbarten Boards
        pitch = 0.8
        for r in range(cfg.rows):
            cy = round(rail + r * (bh + gap) + bh / 2.0, 4)
            for c in range(cfg.cols - 1):
                seam_x = round(rail + c * (bw + gap) + bw + gap / 2.0, 4)
                for k in range(-2, 3):
                    mousebite_holes.append((seam_x, round(cy + k * pitch, 4)))
        for c in range(cfg.cols):
            cx = round(rail + c * (bw + gap) + bw / 2.0, 4)
            for r in range(cfg.rows - 1):
                seam_y = round(rail + r * (bh + gap) + bh + gap / 2.0, 4)
                for k in range(-2, 3):
                    mousebite_holes.append((round(cx + k * pitch, 4), seam_y))

    files = {
        "Panel_Edge_Cuts.gm1": _build_panel_edge_cuts(panel_w, panel_h, instances, bw, bh, vcut_lines),
        "panel_tooling.drl": _build_panel_excellon(tooling_holes, cfg.tooling_hole_diameter_mm, mousebite_holes),
    }
    manifest = build_export_manifest(
        files,
        {
            **summarize_board(board),
            "panel_rows": cfg.rows,
            "panel_cols": cfg.cols,
            "panel_cut_mode": cfg.cut_mode,
            "panel_width_mm": panel_w,
            "panel_height_mm": panel_h,
        },
    )

    return {
        "config": cfg.model_dump(),
        "panel_width_mm": panel_w,
        "panel_height_mm": panel_h,
        "board_count": len(instances),
        "board_instances": instances,
        "tooling_holes": [{"x_mm": x, "y_mm": y, "diameter_mm": cfg.tooling_hole_diameter_mm} for x, y in tooling_holes],
        "fiducials": fiducials,
        "vcut_lines": vcut_lines,
        "mousebite_drill_count": len(mousebite_holes),
        "files": files,
        "manifest": manifest,
    }
