import math

import pytest

from core.diff_router import (DiffPairRouter, _insert_trombone, _offset_polyline,
                              _path_length, _split_polarity, find_differential_pairs,
                              tune_lengths)
from core.schema import BoardState, Component, Net, Pin, PinType
from core.spatial_drc import SpatialHashIndex


# ---------- Helfer ----------

def _pair_board() -> BoardState:
    board = BoardState(width=40.0, height=20.0)
    board.components["U1"] = Component(
        id="U1", name="U1", category="IC", footprint="TEST", x=5.0, y=11.5,
        body_w=2.0, body_h=2.0,
        pins={"1": Pin(id="1", name="DP", pin_type=PinType.OUTPUT, x=4.0, y=10.0),
              "2": Pin(id="2", name="DN", pin_type=PinType.OUTPUT, x=4.0, y=9.5)})
    board.components["R1"] = Component(
        id="R1", name="R1", category="R", footprint="TEST", x=35.0, y=11.5,
        body_w=2.0, body_h=2.0,
        pins={"1": Pin(id="1", name="DP", pin_type=PinType.PASSIVE, x=36.0, y=10.0),
              "2": Pin(id="2", name="DN", pin_type=PinType.PASSIVE, x=36.0, y=9.5)})
    board.nets["USB_DP"] = Net(id="USB_DP", name="USB_DP", nodes=[("U1", "1"), ("R1", "1")])
    board.nets["USB_DN"] = Net(id="USB_DN", name="USB_DN", nodes=[("U1", "2"), ("R1", "2")])
    return board


def _point_to_polyline_distance(point, polyline) -> float:
    return min(SpatialHashIndex._dist_point_to_seg(point, a, b)
               for a, b in zip(polyline, polyline[1:]))


def _flatten(trace) -> list:
    return [(p[0], p[1]) for p in trace.path]


# ---------- Paar-Erkennung ----------

@pytest.mark.parametrize("name,expected", [
    ("USB_DP", ("USB", 1)), ("USB_DN", ("USB", -1)),
    ("DIFF_P", ("DIFF", 1)), ("DIFF_N", ("DIFF", -1)),
    ("ETH+", ("ETH", 1)), ("ETH-", ("ETH", -1)),
    ("GND", None), ("SDA", None), ("P", None), ("DP", None),
])
def test_split_polarity(name, expected):
    assert _split_polarity(name) == expected


def test_find_differential_pairs():
    board = BoardState(width=40.0, height=20.0)
    board.nets["USB_DP"] = Net(id="USB_DP", name="USB_DP", nodes=[])
    board.nets["USB_DN"] = Net(id="USB_DN", name="USB_DN", nodes=[])
    board.nets["ETH+"] = Net(id="ETH+", name="ETH+", nodes=[])
    board.nets["ETH-"] = Net(id="ETH-", name="ETH-", nodes=[])
    board.nets["GND"] = Net(id="GND", name="GND", nodes=[])
    board.nets["SDA"] = Net(id="SDA", name="SDA", nodes=[])
    pairs = find_differential_pairs(board)
    assert [(p.name, n.name) for p, n in pairs] == [("ETH+", "ETH-"), ("USB_DP", "USB_DN")]


def test_unmatched_polarity_is_no_pair():
    board = BoardState(width=10.0, height=10.0)
    board.nets["CLK_P"] = Net(id="CLK_P", name="CLK_P", nodes=[])
    assert find_differential_pairs(board) == []


# ---------- Offset-Geometrie ----------

def test_offset_straight_line_exact_spacing():
    line = [(0.0, 10.0), (32.0, 10.0)]
    off = _offset_polyline(line, 0.5, side=-1, pitch=0.25)
    assert all(abs(y - 9.5) < 1e-9 for _, y in off)
    assert off[0][0] == 0.0 and abs(off[-1][0] - 32.0) < 1e-9


def test_offset_sides_are_mirror_images():
    line = [(0.0, 10.0), (32.0, 10.0)]
    left = _offset_polyline(line, 0.5, side=1, pitch=0.25)
    right = _offset_polyline(line, 0.5, side=-1, pitch=0.25)
    assert left[0][1] == 10.5 and right[0][1] == 9.5


# ---------- Längenabgleich (Trombone) ----------

def test_tune_lengths_inserts_exact_missing_length():
    # WorldNode: (x, y, layer)
    path_a = [(0.0, 10.0, 0), (40.0, 10.0, 0)]     # 40 mm
    path_b = [(0.0, 8.0, 0), (36.0, 8.0, 0)]       # 36 mm
    a, b, added = tune_lengths(path_a, path_b, tolerance=0.1)
    assert added == pytest.approx(4.0)
    assert abs(_path_length(a) - _path_length(b)) <= 0.1
    # Mäander ist in der kürzeren Leitung (b) gelandet
    assert _path_length(b) == pytest.approx(40.0, abs=0.1)


def test_tune_lengths_noop_within_tolerance():
    path_a = [(0.0, 10.0, 0), (40.0, 10.0, 0)]
    path_b = [(0.0, 8.0, 0), (39.95, 8.0, 0)]
    a, b, added = tune_lengths(path_a, path_b, tolerance=0.1)
    assert added == 0.0
    assert b == path_b


def test_insert_trombone_requires_straight_run_space():
    path = [(0.0, 0.0, 0), (1.0, 0.0, 0)]  # Lauf kürzer als 2*seg+Marge
    tuned, ok = _insert_trombone(path, 4.0, hint=(0.0, 1.0), seg=1.0,
                                 min_amp=0.02, max_amp=2.0)
    assert not ok and tuned == path


# ---------- Parallelführung ----------

def test_parallel_routing_with_constant_spacing():
    board = _pair_board()
    router = DiffPairRouter(spacing=0.5, trace_width=0.2)
    report = router.route_pairs(board)

    assert any("parallel geführt" in line for line in report)
    traces = {t.net_id: t for t in board.traces}
    assert "USB_DP" in traces and "USB_DN" in traces

    p_line = _flatten(traces["USB_DP"])
    n_line = _flatten(traces["USB_DN"])
    # Termini der Minus-Leitung erreicht?
    assert math.isclose(n_line[0][0], 4.0, abs_tol=0.1)
    assert math.isclose(n_line[0][1], 9.5, abs_tol=0.1)
    assert math.isclose(n_line[-1][0], 36.0, abs_tol=0.1)
    # Parallele Führung: Abstand entlang des geraden Hauptlaufs = spacing
    distances = [_point_to_polyline_distance((x, 10.0), n_line) for x in (6.0, 10.0, 14.0)]
    assert all(0.4 <= d <= 0.6 for d in distances)
    # Längen von Plus- und Minus-Leitung identisch (keine Escape-Asymmetrie)
    assert abs(_path_length([(x, y, 0) for x, y in p_line])
               - _path_length([(x, y, 0) for x, y in n_line])) <= 0.1


def test_routing_is_deterministic():
    a = _pair_board()
    b = _pair_board()
    DiffPairRouter().route_pairs(a)
    DiffPairRouter().route_pairs(b)
    ta = sorted(t.model_dump_json() for t in a.traces)
    tb = sorted(t.model_dump_json() for t in b.traces)
    assert ta == tb


def test_fallback_when_offset_corridors_blocked():
    board = _pair_board()
    # Blockiert BEIDE Offset-Korridore (y = 9.5 und y = 10.5) bei x = 20,
    # ohne die Plus-Leitung (y = 10.0) zu treffen:
    board.components["BLK1"] = Component(id="BLK1", name="BLK1", category="IC",
                                         footprint="TEST", x=20.0, y=9.25,
                                         body_w=2.0, body_h=1.0)
    board.components["BLK2"] = Component(id="BLK2", name="BLK2", category="IC",
                                         footprint="TEST", x=20.0, y=10.75,
                                         body_w=2.0, body_h=1.0)
    report = DiffPairRouter(spacing=0.5, trace_width=0.2).route_pairs(board)

    assert any("Fallback" in line for line in report)
    traces = {t.net_id: t for t in board.traces}
    assert "USB_DP" in traces and "USB_DN" in traces
    # Minus-Leitung wurde unabhängig geroutet und erreicht ihre Termini
    n_line = _flatten(traces["USB_DN"])
    assert math.isclose(n_line[0][0], 4.0, abs_tol=0.1)
    assert math.isclose(n_line[0][1], 9.5, abs_tol=0.1)
    assert math.isclose(n_line[-1][0], 36.0, abs_tol=0.1)
    assert math.isclose(n_line[-1][1], 9.5, abs_tol=0.1)


# ---------- Pipeline-Integration ----------

def test_pipeline_routes_differential_pair():
    from core.pipeline_v2 import DesignPipeline, STATUS_OK
    result = DesignPipeline(enable_placement=False).run(_pair_board())
    assert result.status == STATUS_OK
    assert any("Diff-Paar USB_DP/USB_DN" in line and "parallel geführt" in line
               for line in result.report)
    traces = {t.net_id for t in result.board.traces}
    assert {"USB_DP", "USB_DN"} <= traces
    # DRC nach dem Pair-Routing: sauber (Abstand 0.5 > Clearance 0.2)
    assert result.board.errors == []


def test_pipeline_diff_routing_can_be_disabled():
    from core.pipeline_v2 import DesignPipeline
    result = DesignPipeline(enable_placement=False, enable_diff_routing=False).run(_pair_board())
    assert not any("Diff-Paar" in line for line in result.report)
