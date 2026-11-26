import math

import pytest
from shapely.geometry import Point, Polygon

from core.copper_pour import CopperPourGenerator
from core.schema import BoardState, Component, CopperPour, Net, Pin, PinType, Trace, Via


# ---------- Helfer ----------

def _board() -> BoardState:
    board = BoardState(width=40.0, height=30.0)
    board.nets["GND"] = Net(id="GND", name="GND", nodes=[])
    return board


def _has_pour_at(pours, x, y) -> bool:
    """True, wenn mindestens eine Region den Punkt abdeckt."""
    return any(Polygon(p.outer, p.holes).covers(Point(x, y)) for p in pours)


def _single(pours) -> CopperPour:
    assert len(pours) == 1
    return pours[0]


# ---------- Grundfläche ----------

def test_full_board_without_obstacles():
    pours = CopperPourGenerator().generate(_board())
    pour = _single(pours)
    assert pour.net_id == "GND"
    assert pour.layer == "BOTTOM"
    corners = set(pour.outer)
    assert corners == {(0.0, 0.0), (40.0, 0.0), (40.0, 30.0), (0.0, 30.0)}
    assert pour.holes == []
    assert Polygon(pour.outer, pour.holes).is_valid


def test_keepout_around_foreign_bottom_trace():
    board = _board()
    board.traces.append(Trace(net_id="N1", layer="BOTTOM", width=0.25,
                              path=[(5.0, 15.0), (35.0, 15.0)]))
    pours = CopperPourGenerator().generate(board)
    pour = _single(pours)
    # Auf der fremden Bahn und in der Clearance: kein Kupfer
    assert not _has_pour_at([pour], 20.0, 15.0)
    # Außerhalb der Clearance: Kupfer
    assert _has_pour_at([pour], 20.0, 13.0)
    assert _has_pour_at([pour], 20.0, 17.0)
    # Aussparung als Loch im Polygon
    assert len(pour.holes) >= 1


def test_top_layer_traces_are_ignored_on_bottom_pour():
    board = _board()
    board.traces.append(Trace(net_id="N1", layer="TOP", width=0.25,
                              path=[(5.0, 15.0), (35.0, 15.0)]))
    pours = CopperPourGenerator().generate(board)
    assert _has_pour_at(pours, 20.0, 15.0)


def test_own_net_traces_are_not_keepouts():
    board = _board()
    board.traces.append(Trace(net_id="GND", layer="BOTTOM", width=0.25,
                              path=[(5.0, 15.0), (35.0, 15.0)]))
    pours = CopperPourGenerator().generate(board)
    assert _has_pour_at(pours, 20.0, 15.0)


def test_foreign_via_gets_keepout():
    board = _board()
    board.vias.append(Via(net_id="N1", x=20.0, y=15.0))
    pours = CopperPourGenerator().generate(board)
    assert not _has_pour_at(pours, 20.0, 15.0)
    assert _has_pour_at(pours, 20.0, 16.5)


# ---------- Thermal Reliefs ----------

def test_thermal_relief_for_gnd_via():
    board = _board()
    board.vias.append(Via(net_id="GND", x=20.0, y=24.0))
    pours = CopperPourGenerator().generate(board)
    pour = _single(pours)

    # Anti-Pad r = 0.3 + 0.3 = 0.6 mm -> 4 Quadrant-Aussparungen
    assert len(pour.holes) == 4
    # Speichenachse innerhalb des Anti-Pads: Kupfer (Verbindung)
    assert _has_pour_at([pour], 20.45, 24.0)
    assert _has_pour_at([pour], 20.0, 24.45)
    # Diagonale im Anti-Pad (zwischen den Speichen): kein Kupfer
    assert not _has_pour_at([pour], 20.40, 24.40)
    assert not _has_pour_at([pour], 19.60, 24.40)


def test_thermal_relief_for_gnd_pad():
    board = _board()
    board.components["U1"] = Component(
        id="U1", name="U1", category="IC", footprint="TEST", x=19.0, y=23.0,
        pins={"7": Pin(id="7", name="GND", pin_type=PinType.GROUND, x=20.0, y=24.0)},
    )
    board.nets["GND"].nodes = [("U1", "7")]
    pours = CopperPourGenerator().generate(board)
    pour = _single(pours)

    # Pad-Radius 0.5 -> Anti-Pad 0.8 mm
    assert len(pour.holes) == 4
    assert _has_pour_at([pour], 20.60, 24.0)
    assert not _has_pour_at([pour], 20.55, 24.55)


def test_pads_without_net_or_position_are_skipped():
    board = _board()
    board.components["U1"] = Component(
        id="U1", name="U1", category="IC", footprint="TEST", x=19.0, y=23.0,
        pins={"7": Pin(id="7", name="GND", pin_type=PinType.GROUND)},  # keine Position
    )
    board.nets["GND"].nodes = [("U1", "7"), ("GHOST", "1")]
    pours = CopperPourGenerator().generate(board)
    assert _single(pours).holes == []


def test_pour_net_without_thermal_points():
    board = BoardState(width=20.0, height=20.0)  # kein GND im Board
    pours = CopperPourGenerator().generate(board)
    pour = _single(pours)
    assert pour.holes == []
    assert len(pour.outer) == 4


# ---------- Qualität ----------

def test_pour_geometry_is_valid():
    board = _board()
    board.traces.append(Trace(net_id="N1", layer="BOTTOM", width=0.25,
                              path=[(5.0, 15.0), (35.0, 15.0)]))
    board.vias.append(Via(net_id="GND", x=20.0, y=24.0))
    board.vias.append(Via(net_id="N1", x=10.0, y=8.0))
    for pour in CopperPourGenerator().generate(board):
        assert Polygon(pour.outer, pour.holes).is_valid


def test_copper_pour_is_deterministic():
    board = _board()
    board.traces.append(Trace(net_id="N1", layer="BOTTOM", width=0.25,
                              path=[(5.0, 15.0), (35.0, 15.0)]))
    board.vias.append(Via(net_id="GND", x=20.0, y=24.0))
    a = [p.model_dump_json() for p in CopperPourGenerator().generate(board)]
    b = [p.model_dump_json() for p in CopperPourGenerator().generate(board)]
    assert a == b


def test_multi_polygon_boards_yield_multiple_regions():
    board = _board()
    # Fremde BOTTOM-Bahn teilt das Board in zwei Hälften
    board.traces.append(Trace(net_id="N1", layer="BOTTOM", width=4.0,
                              path=[(0.0, 15.0), (40.0, 15.0)]))
    pours = CopperPourGenerator().generate(board)
    assert len(pours) == 2
    assert all(len(p.outer) >= 3 for p in pours)
