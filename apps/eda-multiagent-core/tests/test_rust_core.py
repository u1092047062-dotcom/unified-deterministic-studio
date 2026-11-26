import json
import time

import pytest

core_fast = pytest.importorskip("core_fast")

from core.schema import BoardState, Component, Net, Pin, PinType
from core.router_v2 import NegotiatedPathfinder


# ---------- Helfer ----------

def _two_net_board() -> BoardState:
    board = BoardState(width=40.0, height=20.0)
    board.components["U1"] = Component(id="U1", name="U1", category="IC", footprint="T",
                                       x=5.0, y=10.0, body_w=2.0, body_h=2.0,
                                       pins={"1": Pin(id="1", name="A", pin_type=PinType.OUTPUT, x=4.0, y=10.0),
                                             "2": Pin(id="2", name="B", pin_type=PinType.OUTPUT, x=4.0, y=12.0)})
    board.components["R1"] = Component(id="R1", name="R1", category="R", footprint="T",
                                       x=35.0, y=10.0, body_w=2.0, body_h=2.0,
                                       pins={"1": Pin(id="1", name="A", pin_type=PinType.PASSIVE, x=36.0, y=10.0),
                                             "2": Pin(id="2", name="B", pin_type=PinType.PASSIVE, x=36.0, y=12.0)})
    board.nets["N1"] = Net(id="N1", name="n1", nodes=[("U1", "1"), ("R1", "1")])
    board.nets["N2"] = Net(id="N2", name="n2", nodes=[("U1", "2"), ("R1", "2")])
    return board


def _pair(board: BoardState, pitch: float = 1.0):
    """Zwei Router über demselben Board: Rust-Fast-Path und Python-Fallback."""
    fast = NegotiatedPathfinder(board, pitch=pitch)
    assert fast.fast, "core_fast ist gebaut, aber der Router nutzt den Fallback"
    slow = NegotiatedPathfinder(board, pitch=pitch)
    slow.fast = False
    return fast, slow


def _add_wall(router: NegotiatedPathfinder, x: int, gap=None, layer=None):
    for gy in range(router.ny):
        if gap is not None and gap[0] <= gy <= gap[1]:
            continue
        for l in range(2):
            if layer is None or l == layer:
                router.static_obstacles.add((x, gy, l))
    router._sync_obstacle_bits()


# ---------- Backend ----------

def test_router_prefers_rust_backend():
    fast, _ = _pair(_two_net_board())
    assert fast.fast is True


def test_fallback_flag_disables_rust():
    _, slow = _pair(_two_net_board())
    assert slow.fast is False


# ---------- Differentialtests: Rust muss Python bitidentisch sein ----------

def test_parity_open_field():
    fast, slow = _pair(_two_net_board())
    assert fast.route_net_negotiated("N1", (4.0, 10.0), (36.0, 10.0), 0.5) == \
           slow.route_net_negotiated("N1", (4.0, 10.0), (36.0, 10.0), 0.5)


def test_parity_wall_with_gap():
    fast, slow = _pair(_two_net_board())
    _add_wall(fast, 20, gap=(15, 17))
    _add_wall(slow, 20, gap=(15, 17))
    assert fast.route_net_negotiated("N1", (4.0, 10.0), (36.0, 10.0), 0.5) == \
           slow.route_net_negotiated("N1", (4.0, 10.0), (36.0, 10.0), 0.5)


def test_parity_forced_via():
    fast, slow = _pair(_two_net_board())
    _add_wall(fast, 20, layer=0)
    _add_wall(slow, 20, layer=0)
    pf = fast.route_net_negotiated("N1", (4.0, 10.0), (36.0, 10.0), 0.5)
    pp = slow.route_net_negotiated("N1", (4.0, 10.0), (36.0, 10.0), 0.5)
    assert pf == pp
    assert {l for _, _, l in pf} == {0, 1}, "Szenario muss einen Layerwechsel enthalten"


def test_parity_with_congestion_and_history():
    fast, slow = _pair(_two_net_board())
    # Fremdnetz belegt eine Barriere von Zellen -> sharing-Zwang
    for x in range(10, 26):
        fast._occ_add((x, 10, 0), "OTHER")
        slow._occ_add((x, 10, 0), "OTHER")
    # Verhandlungsgedächtnis auf identischen Indizes setzen
    for router in (fast, slow):
        for x in range(0, 40, 7):
            router._history[router._node_idx((x, 5, 0))] = 2.5
            router._history[router._node_idx((x, 15, 1))] = 1.25

    assert fast.route_net_negotiated("N1", (4.0, 10.0), (36.0, 10.0), 0.5) == \
           slow.route_net_negotiated("N1", (4.0, 10.0), (36.0, 10.0), 0.5)
    # Erhöhter Congestion-Druck ändert die Gleichung - bleibt identisch
    assert fast.route_net_negotiated("N1", (4.0, 10.0), (36.0, 10.0), 4.0) == \
           slow.route_net_negotiated("N1", (4.0, 10.0), (36.0, 10.0), 4.0)


def test_parity_own_net_sharing_is_free():
    fast, slow = _pair(_two_net_board())
    # Das eigene Netz belegt Zellen (MST-Vorwärtspfad) -> darf kostenlos kreuzen
    for x in range(4, 36):
        fast._occ_add((x, 10, 0), "N1")
        slow._occ_add((x, 10, 0), "N1")
    assert fast.route_net_negotiated("N1", (4.0, 10.0), (36.0, 10.0), 0.5) == \
           slow.route_net_negotiated("N1", (4.0, 10.0), (36.0, 10.0), 0.5)


def test_parity_unroutable_returns_empty():
    fast, slow = _pair(_two_net_board())
    _add_wall(fast, 20)
    _add_wall(slow, 20)
    assert fast.route_net_negotiated("N1", (4.0, 10.0), (36.0, 10.0), 0.5) == []
    assert slow.route_net_negotiated("N1", (4.0, 10.0), (36.0, 10.0), 0.5) == []


def test_parity_full_solve():
    fast, slow = _pair(_two_net_board())
    t_fast = fast.solve_ripup_and_reroute(max_iterations=10)
    t_slow = slow.solve_ripup_and_reroute(max_iterations=10)
    assert [t.model_dump() for t in t_fast[0]] == [t.model_dump() for t in t_slow[0]]
    assert [v.model_dump() for v in t_fast[1]] == [v.model_dump() for v in t_slow[1]]


# ---------- Rust-Grenzfälle ----------

def test_invalid_buffer_lengths_raise():
    with pytest.raises(ValueError):
        core_fast.route_negotiated_fast(
            4, 4, 0, 0, 3, 3,
            obstacles=b"\x00" * 2,          # zu kurz (Bedarf 2 Bytes? (4*4*2+7)//8 = 4)
            occ_count=b"\x00" * 32,
            occ_self=b"\x00" * 32,
            history_bytes=b"\x00" * (32 * 8),
            p_fac=0.5, via_cost=5.0,
        )


def test_out_of_bounds_terminals_return_empty():
    result = core_fast.route_negotiated_fast(
        4, 4, 0, 0, 9, 9,
        obstacles=b"\x00" * 4,
        occ_count=b"\x00" * 32,
        occ_self=b"\x00" * 32,
        history_bytes=b"\x00" * (32 * 8),
        p_fac=0.5, via_cost=5.0,
    )
    assert result == []


# ---------- Leistung ----------

def test_rust_backend_is_faster():
    board = BoardState(width=100.0, height=80.0)
    board.components["A"] = Component(id="A", name="A", category="IC", footprint="T", x=5.0, y=40.0,
                                      pins={"1": Pin(id="1", name="A", pin_type=PinType.OUTPUT, x=4.0, y=40.0)})
    board.components["B"] = Component(id="B", name="B", category="R", footprint="T", x=95.0, y=40.0,
                                      pins={"1": Pin(id="1", name="A", pin_type=PinType.PASSIVE, x=96.0, y=40.0)})
    board.nets["N1"] = Net(id="N1", name="n1", nodes=[("A", "1"), ("B", "1")])

    fast, slow = _pair(board, pitch=0.5)
    _add_wall(fast, 100, gap=(60, 70))
    _add_wall(slow, 100, gap=(60, 70))

    n = 10
    fast.route_net_negotiated("N1", (4.0, 40.0), (96.0, 40.0), 0.5)  # Warmup
    slow.route_net_negotiated("N1", (4.0, 40.0), (96.0, 40.0), 0.5)
    t0 = time.perf_counter()
    for _ in range(n):
        fast.route_net_negotiated("N1", (4.0, 40.0), (96.0, 40.0), 0.5)
    t_rust = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(n):
        slow.route_net_negotiated("N1", (4.0, 40.0), (96.0, 40.0), 0.5)
    t_py = time.perf_counter() - t0

    print(f"\nRust: {t_rust / n * 1000:.2f} ms/Route, Python: {t_py / n * 1000:.2f} ms/Route, "
          f"Speedup: {t_py / t_rust:.1f}x")
    assert t_rust < t_py
