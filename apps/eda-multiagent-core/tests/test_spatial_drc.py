import math

import pytest

from core.schema import BoardState, Component, DesignRules, Trace
from core.spatial_drc import HighSpeedDRC, SpatialHashIndex


# ---------- Punkt-zu-Strecke ----------

def test_dist_point_to_seg_perpendicular():
    d = SpatialHashIndex._dist_point_to_seg((0.5, 1.0), (0.0, 0.0), (1.0, 0.0))
    assert math.isclose(d, 1.0)


def test_dist_point_to_seg_endpoint_clamp():
    d = SpatialHashIndex._dist_point_to_seg((2.0, 0.0), (0.0, 0.0), (1.0, 0.0))
    assert math.isclose(d, 1.0)


def test_dist_point_to_seg_degenerate():
    d = SpatialHashIndex._dist_point_to_seg((3.0, 4.0), (0.0, 0.0), (0.0, 0.0))
    assert math.isclose(d, 5.0)


# ---------- Strecke-zu-Strecke ----------

def test_dist_seg_seg_parallel_offset():
    d = SpatialHashIndex._dist_seg_seg((0.0, 0.0), (1.0, 0.0), (0.0, 0.5), (1.0, 0.5))
    assert math.isclose(d, 0.5)


def test_dist_seg_seg_crossing_is_zero():
    d = SpatialHashIndex._dist_seg_seg((0.0, 0.0), (2.0, 2.0), (0.0, 2.0), (2.0, 0.0))
    assert d == 0.0


def test_dist_seg_seg_collinear_overlap_is_zero():
    d = SpatialHashIndex._dist_seg_seg((0.0, 0.0), (2.0, 0.0), (1.0, 0.0), (3.0, 0.0))
    assert d == 0.0


def test_dist_seg_seg_disjoint_diagonal():
    d = SpatialHashIndex._dist_seg_seg((0.0, 0.0), (1.0, 0.0), (3.0, 0.0), (3.0, 4.0))
    assert math.isclose(d, 2.0)


# ---------- Index ----------

def test_index_detects_crossing_from_any_point():
    idx = SpatialHashIndex(cell_size=1.0)
    idx.insert_segment("N1", (0.0, 10.0), (10.0, 10.0))
    idx.insert_segment("N2", (0.0, 0.0), (10.0, 20.0))  # schneidet N1 bei (5, 10)

    assert idx.query_segment_clearance("N1", (0.0, 10.0), (10.0, 10.0), 0.2) == ["N2"]
    # query_clearance am Schnittpunkt findet N2 ebenfalls
    assert idx.query_clearance("N1", (5.0, 10.0), 0.2) == ["N2"]


def test_index_ignores_own_net_and_far_segments():
    idx = SpatialHashIndex(cell_size=1.0)
    idx.insert_segment("N1", (0.0, 0.0), (5.0, 0.0))
    idx.insert_segment("N2", (0.0, 50.0), (5.0, 50.0))

    assert idx.query_segment_clearance("N1", (0.0, 0.0), (5.0, 0.0), 0.2) == []
    # eigenes Netz wird ignoriert
    assert idx.query_clearance("N1", (1.0, 0.0), 0.2) == []


def test_index_rect_query():
    idx = SpatialHashIndex(cell_size=1.0)
    idx.insert_rect("U1", (5.0, 5.0), 4.0, 2.0)

    assert idx.query_rect_violations((0.0, 5.0), (10.0, 5.0)) == ["U1"]      # durch den Body
    assert idx.query_rect_violations((0.0, 0.0), (10.0, 0.0)) == []          # weit weg
    assert idx.query_rect_violations((6.0, 5.0), (12.0, 5.0)) == ["U1"]      # Startpunkt im Body


# ---------- Board-DRC ----------

def _board_with_crossing_traces() -> BoardState:
    board = BoardState()
    board.traces.append(Trace(net_id="N1", path=[(10.0, 10.0), (20.0, 10.0)]))
    board.traces.append(Trace(net_id="N2", path=[(15.0, 5.0), (15.0, 15.0)]))
    return board


def test_check_board_finds_clearance_violation():
    errors = HighSpeedDRC.check_board(_board_with_crossing_traces(), DesignRules(min_clearance=0.2))
    rule_ids = {e.rule_id for e in errors}
    assert HighSpeedDRC.RULE_CLEARANCE in rule_ids
    violation = next(e for e in errors if e.rule_id == HighSpeedDRC.RULE_CLEARANCE)
    assert set(violation.affected_nets) == {"N1", "N2"}


def test_check_board_finds_component_collision():
    board = _board_with_crossing_traces()
    board.components["U1"] = Component(id="U1", name="X", category="MCU", footprint="QFN",
                                       x=15.0, y=10.0, body_w=4.0, body_h=4.0)
    errors = HighSpeedDRC.check_board(board)
    assert any(e.rule_id == HighSpeedDRC.RULE_COMPONENT_COLLISION for e in errors)


def test_check_board_clean_board_has_no_errors():
    board = BoardState()
    board.traces.append(Trace(net_id="N1", path=[(0.0, 0.0), (5.0, 0.0)]))
    board.traces.append(Trace(net_id="N2", path=[(0.0, 5.0), (5.0, 5.0)]))
    assert HighSpeedDRC.check_board(board) == []


def test_check_board_flags_narrow_trace():
    board = _board_with_crossing_traces()
    board.traces[0].width = 0.1
    errors = HighSpeedDRC.check_board(board, DesignRules(min_trace_width=0.2))
    assert any(e.rule_id == "DRC_TRACE_WIDTH" for e in errors)


# ---------- Body-zu-Body-Überlappung ----------

def _comp_with_body(cid: str, x: float, y: float) -> Component:
    return Component(id=cid, name=cid, category="IC", footprint="TEST",
                     x=x, y=y, body_w=4.0, body_h=4.0)


def test_body_overlap_detected():
    board = BoardState()
    board.components["A"] = _comp_with_body("A", 10.0, 10.0)
    board.components["B"] = _comp_with_body("B", 12.0, 10.0)  # 2 mm Durchdringung
    errors = HighSpeedDRC.check_board(board)
    hits = [e for e in errors if e.rule_id == HighSpeedDRC.RULE_BODY_OVERLAP]
    assert len(hits) == 1
    assert set(hits[0].affected_components) == {"A", "B"}


def test_touching_bodies_are_legal():
    board = BoardState()
    board.components["A"] = _comp_with_body("A", 10.0, 10.0)
    board.components["B"] = _comp_with_body("B", 14.0, 10.0)  # Kante an Kante
    errors = HighSpeedDRC.check_board(board)
    assert [e for e in errors if e.rule_id == HighSpeedDRC.RULE_BODY_OVERLAP] == []


def test_distant_bodies_are_clean():
    board = BoardState()
    board.components["A"] = _comp_with_body("A", 10.0, 10.0)
    board.components["B"] = _comp_with_body("B", 60.0, 60.0)
    assert HighSpeedDRC.check_board(board) == []


# ---------- Impedanz ----------

def test_microstrip_impedance_50_ohm_reference():
    # FR4, 1.6 mm: ~2.9 mm Leiterbahnbreite ergibt klassisch ~50 Ohm.
    z = HighSpeedDRC.calculate_microstrip_impedance(2.9, dielectric_height=1.6, er=4.5)
    assert abs(z - 50.0) < 2.0


def test_microstrip_impedance_degenerate_width_clamps_to_zero():
    # w >> h verlässt den Gültigkeitsbereich -> physikalische Grenze 0 Ohm.
    assert HighSpeedDRC.calculate_microstrip_impedance(100.0, dielectric_height=0.1) == 0.0


def test_microstrip_impedance_rejects_invalid_input():
    with pytest.raises(ValueError):
        HighSpeedDRC.calculate_microstrip_impedance(0.0)
    with pytest.raises(ValueError):
        HighSpeedDRC.calculate_microstrip_impedance(0.25, dielectric_height=0.0)
