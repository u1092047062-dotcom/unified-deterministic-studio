"""build123d / OCCT-kompatibler Gehaeuse-Adapter & deterministischer Geometrie-Validator (Phase 3).

Kombiniert testgetriebene CAD-DFM-Geometriepruefung (Wandstaerke, PCB-Spiel,
Fillet-Invarianten, Montage-Dome-Kollisionspruefung, Volumen-/Masseabschaetzung)
mit einem deterministischen `build123d`-Python-Skriptgenerator fuer direkte
B-Rep-STEP-Ausgabe, sobald optionales `build123d` lokal installiert ist.
"""

from __future__ import annotations

import importlib.util
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from core.enclosure_gen import BOARD_THICKNESS, BODY_HEIGHT, _edge_ports, _is_mounting_hole
from core.schema import BoardState

MIN_WALL_MM = 1.2
MIN_CLEARANCE_MM = 0.3
PLA_DENSITY_G_PER_CM3 = 1.24


class EnclosureParams(BaseModel):
    wall_thickness_mm: float = Field(default=2.4, gt=0.0, le=20.0)
    pcb_clearance_mm: float = Field(default=0.6, ge=0.0, le=10.0)
    internal_height_mm: float = Field(default=18.0, gt=0.0, le=200.0)
    lid_lip_mm: float = Field(default=1.5, ge=0.0, le=10.0)
    fillet_radius_mm: float = Field(default=1.0, ge=0.0, le=10.0)
    mounting_post_diameter_mm: float = Field(default=7.0, ge=2.0, le=20.0)


def detect_occt_backend() -> Dict[str, Any]:
    """Erkennt optional installierte Python-OCCT-Backends (build123d / cadquery)."""
    b123d = importlib.util.find_spec("build123d") is not None
    cq = importlib.util.find_spec("cadquery") is not None
    active = "build123d" if b123d else ("cadquery" if cq else "analytic-geometry-validator")
    return {
        "build123d_available": b123d,
        "cadquery_available": cq,
        "active_backend": active,
    }


def validate_enclosure_geometry(board: BoardState, params: EnclosureParams | None = None) -> Dict[str, Any]:
    """Fuehrt deterministische DFM- und Kollisionspruefungen fuer das PCB-Gehaeuse aus."""
    p = params or EnclosureParams()
    inner_w = round(float(board.width) + 2.0 * p.pcb_clearance_mm, 4)
    inner_h = round(float(board.height) + 2.0 * p.pcb_clearance_mm, 4)
    outer_w = round(inner_w + 2.0 * p.wall_thickness_mm, 4)
    outer_h = round(inner_h + 2.0 * p.wall_thickness_mm, 4)
    outer_z = round(p.internal_height_mm + 2.0 * p.wall_thickness_mm, 4)

    min_required_height = round(BOARD_THICKNESS + BODY_HEIGHT + 4.0, 4)
    checks: List[Dict[str, Any]] = []
    errors: List[str] = []

    def _record(name: str, passed: bool, detail: str) -> None:
        checks.append({"check": name, "passed": passed, "detail": detail})
        if not passed:
            errors.append(f"{name}: {detail}")

    _record(
        "min_wall_thickness",
        p.wall_thickness_mm >= MIN_WALL_MM,
        f"wall={p.wall_thickness_mm:.2f} mm (min {MIN_WALL_MM:.2f} mm)",
    )
    _record(
        "min_pcb_clearance",
        p.pcb_clearance_mm >= MIN_CLEARANCE_MM,
        f"clearance={p.pcb_clearance_mm:.2f} mm (min {MIN_CLEARANCE_MM:.2f} mm)",
    )
    _record(
        "fillet_below_wall_thickness",
        p.fillet_radius_mm < p.wall_thickness_mm,
        f"fillet={p.fillet_radius_mm:.2f} mm muss kleiner als wall={p.wall_thickness_mm:.2f} mm sein",
    )
    _record(
        "cavity_height_clearance",
        p.internal_height_mm >= min_required_height,
        f"internal_height={p.internal_height_mm:.2f} mm (min {min_required_height:.2f} mm)",
    )

    # Montage-Dome muessen vollstaendig im Innenhohlraum liegen (keine Kollision mit Aussenwand)
    post_r = p.mounting_post_diameter_mm / 2.0
    posts = [
        (comp.id, float(comp.x), float(comp.y))
        for comp in sorted(board.components.values(), key=lambda c: c.id)
        if _is_mounting_hole(comp)
    ]
    post_collisions: List[str] = []
    for cid, px, py in posts:
        if (
            px - post_r < -p.pcb_clearance_mm
            or px + post_r > float(board.width) + p.pcb_clearance_mm
            or py - post_r < -p.pcb_clearance_mm
            or py + post_r > float(board.height) + p.pcb_clearance_mm
        ):
            post_collisions.append(cid)
    _record(
        "mounting_posts_inside_cavity",
        len(post_collisions) == 0,
        f"{len(posts)} Schraubdome geprueft; Kollisionen={post_collisions}",
    )

    ports = _edge_ports(board, p.wall_thickness_mm, p.pcb_clearance_mm)
    invalid_ports = [comp_id for _, a0, a1, comp_id in ports if a1 <= a0]
    _record(
        "edge_port_cutouts_valid",
        len(invalid_ports) == 0,
        f"{len(ports)} Wanddurchbrueche geprueft; ungueltig={invalid_ports}",
    )

    outer_vol_cm3 = (outer_w * outer_h * outer_z) / 1000.0
    inner_vol_cm3 = (inner_w * inner_h * p.internal_height_mm) / 1000.0
    shell_vol_cm3 = max(0.0, outer_vol_cm3 - inner_vol_cm3)

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "checks": checks,
        "params": p.model_dump(),
        "dimensions_mm": {
            "board_w": float(board.width),
            "board_h": float(board.height),
            "inner_w": inner_w,
            "inner_h": inner_h,
            "inner_z": p.internal_height_mm,
            "outer_w": outer_w,
            "outer_h": outer_h,
            "outer_z": outer_z,
        },
        "mounting_post_count": len(posts),
        "edge_port_count": len(ports),
        "inner_volume_cm3": round(inner_vol_cm3, 3),
        "shell_volume_cm3": round(shell_vol_cm3, 3),
        "estimated_pla_mass_g": round(shell_vol_cm3 * PLA_DENSITY_G_PER_CM3, 2),
        "occt_backend": detect_occt_backend(),
    }


def generate_build123d_script(board: BoardState, params: EnclosureParams | None = None) -> str:
    """Erzeugt ein deterministisches, ausfuehrbares build123d-Python-Skript fuer nativen STEP-Export."""
    report = validate_enclosure_geometry(board, params)
    dims = report["dimensions_mm"]
    p = report["params"]
    return (
        '"""Deterministisches build123d-Gehaeuse-Skript (generiert von eda-multiagent-core)."""\n'
        "from build123d import BuildPart, Box, Mode, export_step, fillet\n\n"
        f"OUTER_W = {dims['outer_w']}\n"
        f"OUTER_H = {dims['outer_h']}\n"
        f"OUTER_Z = {dims['outer_z']}\n"
        f"INNER_W = {dims['inner_w']}\n"
        f"INNER_H = {dims['inner_h']}\n"
        f"INNER_Z = {dims['inner_z']}\n"
        f"WALL = {p['wall_thickness_mm']}\n"
        f"FILLET_R = {p['fillet_radius_mm']}\n\n"
        "with BuildPart() as enclosure:\n"
        "    Box(OUTER_W, OUTER_H, OUTER_Z)\n"
        "    if FILLET_R > 0:\n"
        "        fillet(enclosure.edges(), radius=FILLET_R)\n"
        "    Box(INNER_W, INNER_H, INNER_Z, mode=Mode.SUBTRACT)\n\n"
        'if __name__ == "__main__":\n'
        '    export_step(enclosure.part, "enclosure_v2.step")\n'
    )
