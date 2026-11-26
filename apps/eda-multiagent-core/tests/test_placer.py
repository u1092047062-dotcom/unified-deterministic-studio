import math

import pytest

from core.placer import DEFAULT_BODY, ForceDirectedPlacer, PlacementResult, net_wirelength
from core.schema import BoardState, Component, Net, Pin, PinType
from core.spatial_drc import HighSpeedDRC


# ---------- Helfer ----------

def _comp(cid: str, x: float, y: float, w: float = 4.0, h: float = 4.0,
          pin_offset=None) -> Component:
    pins = {}
    if pin_offset is not None:
        pins["1"] = Pin(id="1", name="P1", pin_type=PinType.PASSIVE,
                        x=x + pin_offset[0], y=y + pin_offset[1])
    return Component(id=cid, name=cid, category="IC", footprint="TEST",
                     x=x, y=y, body_w=w, body_h=h, pins=pins)


def _positions(board: BoardState) -> dict:
    return {cid: (c.x, c.y) for cid, c in board.components.items()}


def _gap_violations(board: BoardState, min_gap: float) -> list:
    """Paare, deren Footprints näher als min_gap liegen (auf beiden Achsen)."""
    comps = sorted(board.components.values(), key=lambda c: c.id)
    bad = []
    for i in range(len(comps)):
        for j in range(i + 1, len(comps)):
            a, b = comps[i], comps[j]
            gap_x = abs(a.x - b.x) - (a.body_w + b.body_w) / 2.0
            gap_y = abs(a.y - b.y) - (a.body_h + b.body_h) / 2.0
            if gap_x < min_gap and gap_y < min_gap:
                bad.append((a.id, b.id))
    return bad


def _within_bounds(board: BoardState, margin: float) -> bool:
    return all(margin + c.body_w / 2.0 <= c.x <= board.width - margin - c.body_w / 2.0
               and margin + c.body_h / 2.0 <= c.y <= board.height - margin - c.body_h / 2.0
               for c in board.components.values())


# ---------- Anziehung (Hooke) ----------

def test_attraction_pulls_connected_components_together():
    board = BoardState(width=100.0, height=80.0)
    board.components["U1"] = _comp("U1", 10.0, 40.0)
    board.components["R1"] = _comp("R1", 90.0, 40.0)
    board.nets["N1"] = Net(id="N1", name="n1", nodes=[("U1", "1"), ("R1", "1")])

    result = ForceDirectedPlacer().place(board)

    assert result.wirelength_after < result.wirelength_before
    (u, r) = _positions(result.board).values()
    d_after = math.hypot(u[0] - r[0], u[1] - r[1])
    # Federn ziehen auf Ruhe-Länge (~12 mm), fern von den 80 mm Startabstand
    assert d_after < 30.0
    # Abstoßung verhindert das Zusammenquetschen
    assert d_after >= 4.0 + 1.0 - 0.1
    assert result.residual_overlap == pytest.approx(0.0, abs=1e-9)


def test_multi_pin_net_uses_mst_and_connects_all():
    board = BoardState(width=100.0, height=80.0)
    board.components["A"] = _comp("A", 10.0, 10.0)
    board.components["B"] = _comp("B", 90.0, 10.0)
    board.components["C"] = _comp("C", 50.0, 70.0)
    board.nets["BUS"] = Net(id="BUS", name="bus",
                            nodes=[("A", "1"), ("B", "1"), ("C", "1")])
    result = ForceDirectedPlacer().place(board)
    # Gesamtlinienlänge sinkt gegenüber ~80 + 64 + 56 = 200 mm deutlich
    assert result.wirelength_after < 100.0
    assert _gap_violations(result.board, 1.0) == []


def test_wirelength_metric_matches_mst_sum():
    board = BoardState(width=100.0, height=80.0)
    board.components["A"] = _comp("A", 0.0, 0.0)
    board.components["B"] = _comp("B", 30.0, 0.0)
    board.components["C"] = _comp("C", 30.0, 40.0)
    board.nets["N1"] = Net(id="N1", name="n1", nodes=[("A", "1"), ("B", "1"), ("C", "1")])
    # MST: A-B (30) + B-C (40)
    assert net_wirelength(board) == pytest.approx(70.0)


# ---------- Abstoßung ----------

def test_repulsion_spreads_coincident_components():
    board = BoardState(width=100.0, height=80.0)
    for cid in ("U1", "U2", "U3", "U4"):
        board.components[cid] = _comp(cid, 50.0, 40.0)

    result = ForceDirectedPlacer().place(board)

    assert _gap_violations(result.board, 1.0) == []
    assert result.residual_overlap == pytest.approx(0.0, abs=1e-9)
    assert _within_bounds(result.board, 2.0)


def test_repulsion_respects_min_gap_parameter():
    board = BoardState(width=100.0, height=80.0)
    board.components["A"] = _comp("A", 20.0, 40.0)
    board.components["B"] = _comp("B", 21.0, 40.0)  # überlappend gestartet
    result = ForceDirectedPlacer(min_gap=2.0).place(board)
    assert _gap_violations(result.board, 2.0) == []


def test_components_stay_within_board_bounds():
    board = BoardState(width=30.0, height=20.0)
    for cid in ("U1", "U2", "U3", "U4", "U5", "U6"):
        board.components[cid] = _comp(cid, 15.0, 10.0)

    result = ForceDirectedPlacer().place(board)

    assert _within_bounds(result.board, 2.0)


# ---------- Pins & Schnittstellen ----------

def test_pins_follow_their_component():
    board = BoardState(width=100.0, height=80.0)
    board.components["U1"] = _comp("U1", 20.0, 20.0, pin_offset=(2.0, 1.0))
    board.components["R1"] = _comp("R1", 80.0, 60.0, pin_offset=(-2.0, -1.0))
    board.nets["N1"] = Net(id="N1", name="n1", nodes=[("U1", "1"), ("R1", "1")])

    result = ForceDirectedPlacer().place(board)
    u = result.board.components["U1"]
    r = result.board.components["R1"]

    assert u.pins["1"].x - u.x == pytest.approx(2.0)
    assert u.pins["1"].y - u.y == pytest.approx(1.0)
    assert r.pins["1"].x - r.x == pytest.approx(-2.0)
    assert r.pins["1"].y - r.y == pytest.approx(-1.0)


def test_placement_is_deterministic():
    def run() -> PlacementResult:
        board = BoardState(width=100.0, height=80.0)
        board.components["U1"] = _comp("U1", 50.0, 40.0)
        board.components["U2"] = _comp("U2", 50.0, 40.0)
        board.components["U3"] = _comp("U3", 50.0, 40.0)
        board.components["R1"] = _comp("R1", 90.0, 40.0)
        board.nets["N1"] = Net(id="N1", name="n1", nodes=[("U1", "1"), ("R1", "1")])
        return ForceDirectedPlacer().place(board)

    a, b = run(), run()
    assert a.board.model_dump_json() == b.board.model_dump_json()
    assert (a.iterations, a.wirelength_before, a.wirelength_after,
            a.residual_overlap) == (b.iterations, b.wirelength_before,
                                    b.wirelength_after, b.residual_overlap)


def test_input_board_is_not_mutated():
    board = BoardState(width=100.0, height=80.0)
    board.components["U1"] = _comp("U1", 20.0, 20.0, pin_offset=(2.0, 1.0))
    board.components["R1"] = _comp("R1", 80.0, 60.0, pin_offset=(-2.0, -1.0))
    board.nets["N1"] = Net(id="N1", name="n1", nodes=[("U1", "1"), ("R1", "1")])
    snapshot = board.model_copy(deep=True)

    ForceDirectedPlacer().place(board)

    assert board.model_dump_json() == snapshot.model_dump_json()


def test_empty_board_and_single_component():
    empty = ForceDirectedPlacer().place(BoardState(width=50.0, height=50.0))
    assert empty.iterations == 0
    assert empty.wirelength_after == 0.0

    board = BoardState(width=50.0, height=50.0)
    board.components["U1"] = _comp("U1", 25.0, 25.0)
    single = ForceDirectedPlacer().place(board)
    assert single.board.components["U1"].x == pytest.approx(25.0)
    assert single.board.components["U1"].y == pytest.approx(25.0)


def test_placer_output_passes_body_drc():
    board = BoardState(width=100.0, height=80.0)
    for cid in ("U1", "U2", "U3", "U4"):
        board.components[cid] = _comp(cid, 50.0, 40.0)

    result = ForceDirectedPlacer().place(board)
    errors = HighSpeedDRC.check_board(result.board)
    assert [e for e in errors if e.rule_id == HighSpeedDRC.RULE_BODY_OVERLAP] == []


def test_default_body_for_components_without_dimensions():
    board = BoardState(width=100.0, height=80.0)
    c = Component(id="X", name="X", category="IC", footprint="TEST", x=50.0, y=40.0)
    board.components["X"] = c
    result = ForceDirectedPlacer(min_gap=1.0).place(board)
    # Kein Crash; Position bleibt (eindeutig platziert)
    assert result.board.components["X"].x == pytest.approx(50.0)
    assert DEFAULT_BODY == (4.0, 4.0)
