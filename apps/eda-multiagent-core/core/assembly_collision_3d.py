"""3D-Baugruppen-Interferenz- & Freiraumpruefung (Platine + Bauteile + Gehaeuse/Deckel) (Phase 8).

Prueft mathematisch vor 3D-Druck / CNC-Fertigung, dass:
1. Keine zwei Bauteil-3D-Koerper sich schneiden (board_assembly 3D AABB-Check).
2. Kein Bauteil seitlich mit Gehaeuse-Innenwaenden kollidiert (ausser gezielten Edge-Ports).
3. Alle Bauteile unterhalb der Deckel-Innenkante inkl. Sicherheitsabstand (Z-Clearance) bleiben.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from core.cad_enclosure_v2 import EnclosureParams, detect_occt_backend
from core.enclosure_gen import BOARD_THICKNESS, _body_size, _is_connector, _is_mounting_hole
from core.schema import BoardState, Component


def estimate_component_height_mm(comp: Component) -> float:
    """Deterministische 3D-Bauhoehen-Schaetzung nach Bauteil-Kategorie & Footprint."""
    hay = f"{comp.category} {comp.footprint} {comp.name}".lower()
    if _is_mounting_hole(comp):
        return 0.1
    if "usb" in hay or "connector" in hay or "header" in hay:
        return 3.8
    if "electrolytic" in hay or "inductor" in hay:
        return 6.0
    if "esp32" in hay or "mcu" in hay or "qfn" in hay or "tqfp" in hay:
        return 2.8
    if "0402" in hay or "0603" in hay or "0805" in hay or "resistor" in hay or "capacitor" in hay:
        return 0.9
    return 1.8


def _aabb_overlap_3d(
    a: Tuple[float, float, float, float, float, float],
    b: Tuple[float, float, float, float, float, float],
    eps: float = 1e-4,
) -> bool:
    return (
        a[0] + eps < b[3] and b[0] + eps < a[3]
        and a[1] + eps < b[4] and b[1] + eps < a[4]
        and a[2] + eps < b[5] and b[2] + eps < a[5]
    )


def check_3d_assembly_collisions(
    board: BoardState,
    *,
    enclosure: Optional[EnclosureParams] = None,
    standoff_mm: float = 2.0,
    min_lid_clearance_mm: float = 0.5,
    component_heights_mm: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Prueft 3D-Kollisionen zwischen Bauteilen, Platinen-Hohlraum und Gehaeuse-Deckel."""
    enc = enclosure or EnclosureParams()
    overrides = component_heights_mm or {}

    pcb_z0 = float(standoff_mm)
    pcb_z1 = round(pcb_z0 + BOARD_THICKNESS, 4)
    cavity_x0 = -enc.pcb_clearance_mm
    cavity_x1 = float(board.width) + enc.pcb_clearance_mm
    cavity_y0 = -enc.pcb_clearance_mm
    cavity_y1 = float(board.height) + enc.pcb_clearance_mm
    lid_ceiling_z = round(enc.internal_height_mm - enc.lid_lip_mm, 4)

    boxes: List[Dict[str, Any]] = []
    collisions: List[Dict[str, Any]] = []
    min_z_clearance = lid_ceiling_z - pcb_z1

    for cid in sorted(board.components.keys()):
        comp = board.components[cid]
        bw, bh = _body_size(comp)
        bz = float(overrides.get(cid, estimate_component_height_mm(comp)))
        x0 = round(float(comp.x) - bw / 2.0, 4)
        x1 = round(float(comp.x) + bw / 2.0, 4)
        y0 = round(float(comp.y) - bh / 2.0, 4)
        y1 = round(float(comp.y) + bh / 2.0, 4)
        z0 = pcb_z1
        z1 = round(pcb_z1 + bz, 4)

        box_entry = {
            "id": cid,
            "name": comp.name,
            "aabb_mm": [x0, y0, z0, x1, y1, z1],
            "height_mm": bz,
        }
        boxes.append(box_entry)

        z_clr = round(lid_ceiling_z - z1, 4)
        min_z_clearance = min(min_z_clearance, z_clr)
        if z_clr < min_lid_clearance_mm:
            collisions.append(
                {
                    "type": "LID_Z_COLLISION",
                    "components": [cid],
                    "detail": f"Bauteil '{cid}' (Top Z={z1:.2f} mm) kollidiert mit Deckel-Innenkante ({lid_ceiling_z:.2f} mm, Spiel={z_clr:.2f} mm < {min_lid_clearance_mm:.2f} mm)",
                }
            )

        if not _is_connector(comp):
            if x0 < cavity_x0 or x1 > cavity_x1 or y0 < cavity_y0 or y1 > cavity_y1:
                collisions.append(
                    {
                        "type": "WALL_XY_COLLISION",
                        "components": [cid],
                        "detail": f"Bauteil '{cid}' ragt ueber den Gehaeuse-Innenraum [{cavity_x0:.1f}..{cavity_x1:.1f}] x [{cavity_y0:.1f}..{cavity_y1:.1f}] hinaus",
                    }
                )

    # Paarweise 3D-Bauteil-Kollisionspruefung
    for i in range(len(boxes)):
        a = boxes[i]
        for j in range(i + 1, len(boxes)):
            b = boxes[j]
            if _aabb_overlap_3d(tuple(a["aabb_mm"]), tuple(b["aabb_mm"])):  # type: ignore[arg-type]
                collisions.append(
                    {
                        "type": "COMPONENT_3D_OVERLAP",
                        "components": [a["id"], b["id"]],
                        "detail": f"3D-Volumenueberlappung zwischen '{a['id']}' und '{b['id']}'",
                    }
                )

    return {
        "collision_free": len(collisions) == 0,
        "collision_count": len(collisions),
        "collisions": collisions,
        "min_lid_z_clearance_mm": round(min_z_clearance, 4),
        "lid_ceiling_z_mm": lid_ceiling_z,
        "pcb_z_span_mm": [pcb_z0, pcb_z1],
        "component_3d_boxes": boxes,
        "occt_backend": detect_occt_backend(),
    }
