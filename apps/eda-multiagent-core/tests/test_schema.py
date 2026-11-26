import json

from core.schema import BoardState, Component, DesignRules, Layer, Net, Pin, PinType, Trace, Via


def test_pin_position_optional():
    pin = Pin(id="1", name="A", pin_type=PinType.PASSIVE)
    assert pin.x is None and pin.y is None
    pin2 = Pin(id="2", name="B", pin_type=PinType.PASSIVE, x=1.5, y=2.5)
    assert (pin2.x, pin2.y) == (1.5, 2.5)


def test_component_body_defaults():
    comp = Component(id="C1", name="X", category="R", footprint="0805")
    assert comp.body_w is None and comp.body_h is None


def test_via_defaults_use_layer_enum():
    via = Via(net_id="N1", x=10.0, y=20.0)
    assert via.from_layer == Layer.TOP
    assert via.to_layer == Layer.BOTTOM
    assert via.diameter == 0.6
    # str-Enum serialisiert als einfacher String
    assert json.loads(via.model_dump_json())["from_layer"] == "TOP"


def test_board_state_roundtrip_with_vias():
    board = BoardState()
    board.nets["N1"] = Net(id="N1", name="n1", nodes=[("A", "1"), ("B", "2")])
    board.traces.append(Trace(net_id="N1", layer=Layer.BOTTOM,
                              path=[(0.0, 0.0), (1.0, 1.0)]))
    board.vias.append(Via(net_id="N1", x=0.5, y=0.5))

    data = json.loads(board.model_dump_json())
    restored = BoardState.model_validate(data)
    assert restored.vias[0].net_id == "N1"
    assert restored.traces[0].layer == "BOTTOM"
    assert restored.nets["N1"].nodes == [("A", "1"), ("B", "2")]


def test_design_rules_defaults():
    rules = DesignRules()
    assert rules.min_clearance == 0.2
    assert rules.min_trace_width == 0.2
    assert rules.min_via_diameter == 0.6
