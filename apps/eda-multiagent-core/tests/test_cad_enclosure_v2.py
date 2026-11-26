"""Tests fuer den build123d/OCCT Gehaeuse-Geometrie-Validator (Phase 3)."""

from __future__ import annotations

from core.board_synthesizer import build_template_board
from core.cad_enclosure_v2 import (
    EnclosureParams,
    detect_occt_backend,
    generate_build123d_script,
    validate_enclosure_geometry,
)
from core.schema import Component


def test_enclosure_validator_passes_default_params_and_generates_build123d_script():
    board = build_template_board("esp32_i2c_sensor")
    report = validate_enclosure_geometry(board)

    assert report["valid"] is True
    assert report["errors"] == []
    assert report["shell_volume_cm3"] > 0
    assert report["estimated_pla_mass_g"] > 0
    assert report["occt_backend"]["active_backend"] in {
        "build123d",
        "cadquery",
        "analytic-geometry-validator",
    }

    script = generate_build123d_script(board)
    assert "from build123d import" in script
    assert "export_step" in script


def test_enclosure_validator_rejects_bad_wall_fillet_and_post_collision():
    board = build_template_board("esp32_i2c_sensor")
    # MountingHole direkt auf der Kante -> Schraubdom (r=3.5 mm) ragt aus dem Innenraum
    board.components["H1"] = Component(
        id="H1",
        name="MountingHole_Edge",
        category="MountingHole",
        footprint="MountingHole_3.2mm",
        x=0.5,
        y=0.5,
    )
    bad = EnclosureParams(
        wall_thickness_mm=0.8,
        pcb_clearance_mm=0.1,
        fillet_radius_mm=1.2,
        internal_height_mm=5.0,
    )
    report = validate_enclosure_geometry(board, bad)
    assert report["valid"] is False
    joined = " | ".join(report["errors"])
    assert "min_wall_thickness" in joined
    assert "min_pcb_clearance" in joined
    assert "fillet_below_wall_thickness" in joined
    assert "cavity_height_clearance" in joined
    assert "mounting_posts_inside_cavity" in joined
    assert detect_occt_backend()["active_backend"]
