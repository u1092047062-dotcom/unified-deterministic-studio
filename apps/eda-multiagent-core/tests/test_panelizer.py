"""Tests fuer den KiKit-kompatiblen PCB-Panelizer (Phase 3)."""

from __future__ import annotations

import pytest

from core.board_synthesizer import build_template_board
from core.gerber_verifier import verify_and_render_gerbers
from core.panelizer import PanelConfig, panelize_board


def test_panelize_vcut_geometry_gerber_and_manifest():
    board = build_template_board("esp32_i2c_sensor")
    cfg = PanelConfig(rows=2, cols=3, rail_width_mm=5.0, spacing_mm=0.0, cut_mode="vcut")
    panel = panelize_board(board, cfg)

    assert panel["board_count"] == 6
    assert panel["panel_width_mm"] == pytest.approx(2 * 5.0 + 3 * board.width)
    assert panel["panel_height_mm"] == pytest.approx(2 * 5.0 + 2 * board.height)
    assert len(panel["tooling_holes"]) == 4
    assert len(panel["fiducials"]) == 3
    assert len(panel["vcut_lines"]) >= 4
    assert panel["manifest"]["artifact_count"] == 2

    # Erzeugte Panel-Gerber & Excellon-Drill muessen den strukturellen Phase-1-Verifier bestehen!
    verification = verify_and_render_gerbers(panel["files"])
    assert verification["valid"] is True
    assert verification["layers"]["panel_tooling.drl"]["drill_hits"] == 4
    assert "<svg" in verification["preview_svg"]


def test_panelize_mousebites_generates_breakout_drills_and_guards_spacing():
    board = build_template_board("esp32_i2c_sensor")
    with pytest.raises(ValueError, match="1.6 mm"):
        panelize_board(board, PanelConfig(rows=2, cols=2, spacing_mm=0.5, cut_mode="mousebites"))

    panel = panelize_board(board, PanelConfig(rows=2, cols=2, rail_width_mm=5.0, spacing_mm=2.0, cut_mode="mousebites"))
    assert panel["board_count"] == 4
    assert panel["mousebite_drill_count"] == 20  # 4 Stege x 5 Bohrungen
    verification = verify_and_render_gerbers(panel["files"])
    assert verification["valid"] is True
    assert verification["layers"]["panel_tooling.drl"]["drill_hits"] == 24  # 4 Tooling + 20 Mousebites
