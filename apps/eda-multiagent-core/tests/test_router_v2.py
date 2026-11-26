import math

from core.schema import BoardState, Component, Net, Pin, PinType, Via
from core.router_v2 import NegotiatedPathfinder


def _comp(comp_id: str, x: float, y: float, pin_name: str, pin_x: float, pin_y: float) -> Component:
    return Component(
        id=comp_id, name=comp_id, category="IC", footprint="TEST",
        x=x, y=y, body_w=2.0, body_h=2.0,
        pins={"1": Pin(id="1", name=pin_name, pin_type=PinType.OUTPUT, x=pin_x, y=pin_y)},
    )


def _board(width: float = 40.0, height: float = 20.0) -> BoardState:
    return BoardState(width=width, height=height)


def _two_component_board() -> BoardState:
    board = _board()
    board.components["U1"] = _comp("U1", 5.0, 10.0, "IO1", 4.0, 10.0)
    board.components["R1"] = _comp("R1", 35.0, 10.0, "IO1", 36.0, 10.0)
    board.nets["N1"] = Net(id="N1", name="n1", nodes=[("U1", "1"), ("R1", "1")])
    return board


def test_simple_route_connects_pin_terminals():
    board = _two_component_board()
    router = NegotiatedPathfinder(board, pitch=1.0)
    traces, vias = router.solve_ripup_and_reroute(max_iterations=5)

    assert len(traces) >= 1
    n1_paths = [t.path for t in traces if t.net_id == "N1"]
    assert n1_paths, "Netz N1 wurde nicht geroutet"
    all_points = [p for path in n1_paths for p in path]
    starts = all_points[0]
    ends = all_points[-1]
    assert math.isclose(starts[0], 4.0) and math.isclose(starts[1], 10.0)
    assert math.isclose(ends[0], 36.0) and math.isclose(ends[1], 10.0)
    assert vias == []  # freie Bahn -> kein Layerwechsel


def test_two_nets_do_not_share_cells():
    board = _two_component_board()
    board.components["U1"].pins["2"] = Pin(id="2", name="IO2", pin_type=PinType.OUTPUT, x=4.0, y=12.0)
    board.components["R1"].pins["2"] = Pin(id="2", name="IO2", pin_type=PinType.OUTPUT, x=36.0, y=12.0)
    board.nets["N2"] = Net(id="N2", name="n2", nodes=[("U1", "2"), ("R1", "2")])

    router = NegotiatedPathfinder(board, pitch=1.0)
    router.solve_ripup_and_reroute(max_iterations=10)

    shared = {cell: nets for cell, nets in router.occupancy.items() if len(nets) > 1}
    assert not shared, f"Konfliktzellen nach Verhandlung: {shared}"


def test_multi_pin_net_connects_all_terminals():
    board = _board()
    board.components["U1"] = _comp("U1", 5.0, 10.0, "BUS", 4.0, 10.0)
    board.components["R1"] = _comp("R1", 35.0, 5.0, "BUS", 36.0, 5.0)
    board.components["C1"] = _comp("C1", 20.0, 18.0, "BUS", 21.0, 18.0)
    board.nets["BUS"] = Net(id="BUS", name="bus",
                            nodes=[("U1", "1"), ("R1", "1"), ("C1", "1")])

    router = NegotiatedPathfinder(board, pitch=1.0)
    traces, _ = router.solve_ripup_and_reroute(max_iterations=5)

    terminals = {(4.0, 10.0), (36.0, 5.0), (21.0, 18.0)}
    routed_points = {p for t in traces if t.net_id == "BUS" for p in t.path}
    for terminal in terminals:
        assert any(math.isclose(p[0], terminal[0]) and math.isclose(p[1], terminal[1])
                   for p in routed_points), f"Terminal {terminal} nicht verbunden"


def test_route_avoids_wall_through_gap():
    board = _two_component_board()
    router = NegotiatedPathfinder(board, pitch=1.0)

    # Wand bei x=20, Lücke bei y=15..17
    for gy in range(router.ny):
        if 15 <= gy <= 17:
            continue
        for l in range(2):
            router.static_obstacles.add((20, gy, l))

    traces, _ = router.solve_ripup_and_reroute(max_iterations=5)
    assert traces, "Kein Pfad gefunden"

    all_cells = [router._to_grid(x, y) for t in traces for x, y in t.path]
    assert any(gx == 20 and 15 <= gy <= 17 for gx, gy in all_cells), \
        "Pfad nutzt die Wandlücke nicht"


def test_forced_via_crosses_to_bottom_layer():
    board = _two_component_board()
    router = NegotiatedPathfinder(board, pitch=1.0)

    # Vollständige Wand auf TOP erzwingt den Layerwechsel.
    for gy in range(router.ny):
        router.static_obstacles.add((20, gy, 0))

    traces, vias = router.solve_ripup_and_reroute(max_iterations=5)

    assert len(vias) >= 1, "Erwarteter Layerwechsel fehlt"
    layers_used = {t.layer for t in traces}
    assert "BOTTOM" in layers_used


def test_unroutable_net_returns_empty_without_crash():
    board = _two_component_board()
    router = NegotiatedPathfinder(board, pitch=1.0)
    # Beide Layer komplett vermauern
    for gx in range(router.nx):
        for gy in range(router.ny):
            for l in range(2):
                router.static_obstacles.add((gx, gy, l))

    traces, vias = router.solve_ripup_and_reroute(max_iterations=2)
    assert traces == [] and vias == []


def test_router_is_deterministic():
    results = []
    for _ in range(2):
        board = _two_component_board()
        board.components["U1"].pins["2"] = Pin(id="2", name="IO2", pin_type=PinType.OUTPUT, x=4.0, y=12.0)
        board.components["R1"].pins["2"] = Pin(id="2", name="IO2", pin_type=PinType.OUTPUT, x=36.0, y=12.0)
        board.nets["N2"] = Net(id="N2", name="n2", nodes=[("U1", "2"), ("R1", "2")])
        router = NegotiatedPathfinder(board, pitch=1.0)
        traces, vias = router.solve_ripup_and_reroute(max_iterations=5)
        results.append(([t.model_dump() for t in traces], [v.model_dump() for v in vias]))
    assert results[0] == results[1]


def test_fixed_vias_survive_routing_and_block_cells():
    board = _two_component_board()
    board.vias.append(Via(net_id="NET_SDA", x=20.0, y=10.0, diameter=0.6))
    router = NegotiatedPathfinder(board, pitch=1.0)
    traces, vias = router.solve_ripup_and_reroute(max_iterations=5)

    # Fixe Via bleibt im Ergebnis
    assert any(v.x == 20.0 and v.y == 10.0 and v.net_id == "NET_SDA" for v in vias)
    # Ihre Zelle ist belegt, sodass andere Netze Platz lassen
    assert "NET_SDA" in router.occupancy[(20, 10, 0)]
    assert "NET_SDA" in router.occupancy[(20, 10, 1)]


def test_mst_edges_span_all_points():
    points = [(0.0, 0.0), (10.0, 0.0), (5.0, 5.0)]
    edges = NegotiatedPathfinder._mst_edges(points)
    assert len(edges) == 2
    connected = set()
    for a, b in edges:
        connected.update((a, b))
    assert connected == {0, 1, 2}
