"""Tests fuer die 3D-Baugruppen-Kollisionspruefung (Phase 8)."""

from __future__ import annotations

from core.assembly_collision_3d import check_3d_assembly_collisions
from core.board_synthesizer import build_template_board
from core.cad_enclosure_v2 import EnclosureParams
from core.schema import Component


def test_3d_assembly_passes_clean_board_and_detects_lid_and_component_collisions():
    board = build_template_board("esp32_i2c_sensor")
    ok = check_3d_assembly_collisions(board)
    assert ok["collision_free"] is True
    assert ok["min_lid_z_clearance_mm"] > 5.0

    # Zu niedriges Gehaeuse + ueberlappendes Bauteil muessen beide erkannt werden
    board.components["OVERLAP_TEST"] = Component(
        id="OVERLAP_TEST",
        name="TallCap",
        category="capacitor",
        footprint="Electrolytic",
        x=board.components["U1"].x,
        y=board.components["U1"].y,
        body_w=6.0,
        body_h=6.0,
    )
    low_enc = EnclosureParams(internal_height_mm=7.5, lid_lip_mm=1.5)
    bad = check_3d_assembly_collisions(board, enclosure=low_enc)
    assert bad["collision_free"] is False
    types = {c["type"] for c in bad["collisions"]}
    assert "LID_Z_COLLISION" in types
    assert "COMPONENT_3D_OVERLAP" in types
