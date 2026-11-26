import json

import pytest

from core.kicad_export import DEFAULT_PAD_SIZE, KICAD_VERSION, export_kicad_pcb
from core.schema import BoardState, Component, Net, Pin, PinType, Trace, Via


# ---------- Minimaler S-Expression-Parser (Syntax-Nachweis) ----------

def _tokenize(text: str):
    tokens = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "(" or c == ")":
            tokens.append(c)
            i += 1
        elif c == '"':
            i += 1
            buf = []
            escapes = {'"': '"', "\\": "\\", "n": "\n", "r": "\r", "t": "\t"}
            while i < n:
                if text[i] == "\\" and i + 1 < n:
                    buf.append(escapes.get(text[i + 1], text[i + 1]))
                    i += 2
                elif text[i] == '"':
                    i += 1
                    break
                else:
                    buf.append(text[i])
                    i += 1
            else:
                raise ValueError("unterminisierter String")
            tokens.append(("STR", "".join(buf)))
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n()"':
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def _parse(tokens, pos=0):
    if tokens[pos] == "(":
        node, pos = [], pos + 1
        while tokens[pos] != ")":
            value, pos = _parse(tokens, pos)
            node.append(value)
        return node, pos + 1
    token = tokens[pos]
    if isinstance(token, tuple):
        return token[1], pos + 1
    return token, pos + 1


def parse_sexpr(text: str) -> list:
    tokens = _tokenize(text)
    node, pos = _parse(tokens)
    assert pos == len(tokens), "nicht alle Tokens konsumiert"
    return node


def find_all(node, head):
    return [child for child in node[1:] if isinstance(child, list) and child and child[0] == head]


def find_one(node, head):
    hits = find_all(node, head)
    assert len(hits) == 1, f"erwartet genau ein <{head}>, gefunden {len(hits)}"
    return hits[0]


# ---------- Test-Board ----------

def _demo_board() -> BoardState:
    board = BoardState(width=100.0, height=80.0)
    board.components["U1"] = Component(
        id="U1", name="ESP32", category="MCU", footprint="QFN-32",
        x=30.0, y=40.0, body_w=5.0, body_h=5.0,
        pins={"1": Pin(id="1", name="SDA", pin_type=PinType.OUTPUT, x=27.5, y=42.5)},
    )
    board.components["R1"] = Component(
        id="R1", name="R_10k", category="Resistor", footprint="0805",
        x=70.0, y=40.0, body_w=2.0, body_h=1.25, value="10k",
        pins={"1": Pin(id="1", name="A", pin_type=PinType.PASSIVE, x=69.0, y=40.0)},
    )
    board.nets["NET_SDA"] = Net(id="NET_SDA", name="I2C_SDA", nodes=[("U1", "1"), ("R1", "1")])
    board.traces.append(Trace(net_id="NET_SDA", layer="TOP", width=0.25,
                              path=[(27.5, 42.5), (50.0, 42.5), (69.0, 40.0)]))
    board.vias.append(Via(net_id="NET_SDA", x=50.0, y=42.5))
    return board


# ---------- Struktur & Syntax ----------

def test_export_is_valid_s_expression_with_v8_header():
    doc = parse_sexpr(export_kicad_pcb(_demo_board()))
    assert doc[0] == "kicad_pcb"
    version = find_one(doc, "version")
    assert version[1] == KICAD_VERSION == "20240108"  # KiCad-v8-Formatstempel
    generator = find_one(doc, "generator")
    assert generator[1] == "eda-multiagent-core"
    gen_version = find_one(doc, "generator_version")
    assert gen_version[1] == "1.0"
    general = find_one(doc, "general")
    assert find_one(general, "thickness")[1] == "1.6"


def test_layers_block_contains_required_layers():
    doc = parse_sexpr(export_kicad_pcb(_demo_board()))
    layers = find_one(doc, "layers")
    names = {child[1] for child in layers[1:]}
    assert {"F.Cu", "B.Cu", "Edge.Cuts", "F.SilkS", "F.Mask", "F.Paste", "F.Fab"} <= names
    # Signal-Layer-Eintrag: (0 "F.Cu" signal)
    f_cu = next(c for c in layers[1:] if c[1] == "F.Cu")
    assert f_cu == ["0", "F.Cu", "signal"]


def test_nets_are_declared_numbered_from_one():
    doc = parse_sexpr(export_kicad_pcb(_demo_board()))
    nets = find_all(doc, "net")
    assert ["net", "0", ""] in nets                     # Netz 0 = kein Netz
    assert ["net", "1", "I2C_SDA"] in nets
    assert len(nets) == 2


def test_edge_cuts_outline_four_lines_with_extents():
    doc = parse_sexpr(export_kicad_pcb(_demo_board()))
    edge_lines = [g for g in find_all(doc, "gr_line")
                  if any(isinstance(c, list) and c and c[0] == "layer" and c[1] == "Edge.Cuts"
                         for c in g)]
    assert len(edge_lines) == 4
    starts = {tuple(float(v) for v in g[1][1:3]) for g in edge_lines}
    assert starts == {(0.0, 0.0), (100.0, 0.0), (100.0, 80.0), (0.0, 80.0)}


# ---------- Footprints & Pads ----------

def test_footprints_with_reference_value_and_body_fab():
    doc = parse_sexpr(export_kicad_pcb(_demo_board()))
    fps = find_all(doc, "footprint")
    assert len(fps) == 2
    u1 = next(fp for fp in fps if fp[1] == "eda-core:QFN-32")
    props = {p[1]: p[2] for p in find_all(u1, "property")}
    assert props == {"Reference": "U1", "Value": "ESP32"}
    r1 = next(fp for fp in fps if fp[1] == "eda-core:0805")
    props_r1 = {p[1]: p[2] for p in find_all(r1, "property")}
    assert props_r1["Value"] == "10k"  # comp.value hat Vorrang vor comp.name
    # Body-Outline auf F.Fab: 4 fp_lines
    fab_lines = [l for l in find_all(u1, "fp_line")
                 if any(isinstance(c, list) and c and c[0] == "layer" and c[1] == "F.Fab"
                        for c in l)]
    assert len(fab_lines) == 4


def test_pad_positions_are_y_flipped_and_net_mapped():
    doc = parse_sexpr(export_kicad_pcb(_demo_board()))
    fps = find_all(doc, "footprint")
    u1 = next(fp for fp in fps if fp[1] == "eda-core:QFN-32")
    # Footprint-At: (30, 80-40) = (30, 40)
    at = find_one(u1, "at")
    assert [float(v) for v in at[1:3]] == [30.0, 40.0]
    # Pad 1: Welt (27.5, 42.5) -> relativ (27.5-30, -(42.5-40)) = (-2.5, -2.5)
    pad = find_one(u1, "pad")
    assert pad[1] == "1" and pad[2] == "smd" and pad[3] == "roundrect"
    pad_at = find_one(pad, "at")
    assert [float(v) for v in pad_at[1:3]] == [-2.5, -2.5]
    size = find_one(pad, "size")
    assert [float(v) for v in size[1:3]] == list(DEFAULT_PAD_SIZE)
    net = find_one(pad, "net")
    assert [str(v) for v in net[1:]] == ["1", "I2C_SDA"]
    pintype = find_one(pad, "pintype")
    assert pintype[1] == "output"
    r1 = next(fp for fp in fps if fp[1] == "eda-core:0805")
    r1_pad = find_one(r1, "pad")
    # Welt (69, 40) -> relativ (-1.0, 0.0)
    assert [float(v) for v in find_one(r1_pad, "at")[1:3]] == [-1.0, 0.0]
    assert find_one(r1_pad, "pintype")[1] == "passive"


def test_pin_without_net_has_no_net_token():
    board = _demo_board()
    board.components["U2"] = Component(id="U2", name="X", category="IC", footprint="TEST",
                                       x=10.0, y=10.0,
                                       pins={"9": Pin(id="9", name="NC", pin_type=PinType.INPUT,
                                                      x=11.0, y=10.0)})
    doc = parse_sexpr(export_kicad_pcb(board))
    u2 = next(fp for fp in find_all(doc, "footprint") if fp[1] == "eda-core:TEST")
    pad = find_one(u2, "pad")
    assert find_all(pad, "net") == []
    assert find_one(pad, "pintype")[1] == "input"


# ---------- Traces & Vias ----------

def test_traces_exported_as_segments_with_y_flip():
    doc = parse_sexpr(export_kicad_pcb(_demo_board()))
    segments = find_all(doc, "segment")
    assert len(segments) == 2  # 3 Pfadpunkte -> 2 Segmente
    first = segments[0]
    start = [float(v) for v in find_one(first, "start")[1:3]]
    end = [float(v) for v in find_one(first, "end")[1:3]]
    # Welt (27.5, 42.5) -> KiCad (27.5, 37.5); (50, 42.5) -> (50, 37.5)
    assert start == [27.5, 37.5]
    assert end == [50.0, 37.5]
    assert float(find_one(first, "width")[1]) == 0.25
    assert find_one(first, "layer")[1] == "F.Cu"
    # Segment-Netze tragen nur die Nummer (wie echte KiCad-Files)
    assert [str(v) for v in find_one(first, "net")[1:]] == ["1"]


def test_via_exported_with_drill_and_layers():
    doc = parse_sexpr(export_kicad_pcb(_demo_board()))
    via = find_one(doc, "via")
    at = [float(v) for v in find_one(via, "at")[1:3]]
    assert at == [50.0, 37.5]  # 80 - 42.5
    assert float(find_one(via, "size")[1]) == 0.6
    assert float(find_one(via, "drill")[1]) == 0.3
    assert find_one(via, "layers")[1:] == ["F.Cu", "B.Cu"]
    assert [str(v) for v in find_one(via, "net")[1:]] == ["1"]


def test_bottom_layer_traces_go_to_b_cu():
    board = _demo_board()
    board.traces.append(Trace(net_id="NET_SDA", layer="BOTTOM", width=0.3,
                              path=[(0.0, 0.0), (1.0, 0.0)]))
    doc = parse_sexpr(export_kicad_pcb(board))
    bottom = [s for s in find_all(doc, "segment")
              if find_one(s, "layer")[1] == "B.Cu"]
    assert len(bottom) == 1
    assert float(find_one(bottom[0], "width")[1]) == 0.3


# ---------- Robustheit ----------

def test_special_characters_are_escaped_and_roundtrip():
    board = _demo_board()
    board.nets["NET_WEIRD"] = Net(id="NET_WEIRD", name='SCL "2" \\tick',
                                  nodes=[("U1", "1"), ("R1", "1")])
    doc = parse_sexpr(export_kicad_pcb(board))
    names = {n[2] for n in find_all(doc, "net") if n[1] != "0"}
    assert 'SCL "2" \\tick' in names


def test_export_is_deterministic():
    a = export_kicad_pcb(_demo_board())
    b = export_kicad_pcb(_demo_board())
    assert a == b
    assert "uuid" in a  # UUIDs sind Teil des deterministischen Formats


def test_empty_board_still_exports():
    doc = parse_sexpr(export_kicad_pcb(BoardState(width=40.0, height=30.0)))
    assert doc[0] == "kicad_pcb"
    assert find_all(doc, "footprint") == []
    assert len([g for g in find_all(doc, "gr_line")]) == 4
    assert ["net", "0", ""] in find_all(doc, "net")


def test_kiutils_parses_export_as_kicad_board():
    pytest.importorskip("kiutils")
    from kiutils.board import Board
    from kiutils.utils import sexpr
    exp = sexpr.parse_sexp(export_kicad_pcb(_demo_board()))
    board = Board.from_sexpr(exp)
    assert board.version == int(KICAD_VERSION)  # kiutils wandelt in int
    assert len(board.footprints) == 2
    # traceItems enthält Segmente und Vias:
    assert sum(type(t).__name__ == "Segment" for t in board.traceItems) == 2
    assert sum(type(t).__name__ == "Via" for t in board.traceItems) == 1
    assert {net.name for net in board.nets if net.name} == {"I2C_SDA"}


# ---------- Endpunkt ----------

def test_endpoint_exports_demo_board():
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from server.server import app
    client = TestClient(app)
    response = client.post("/export/kicad")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert "board.kicad_pcb" in response.headers["content-disposition"]
    doc = parse_sexpr(response.text)
    assert doc[0] == "kicad_pcb"
    # Demo-Board läuft durch die Pipeline: Platzierung + Routing + DRC
    assert len(find_all(doc, "footprint")) == 2
    assert len(find_all(doc, "segment")) >= 2


def test_endpoint_exports_given_board_state():
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from server.server import app
    client = TestClient(app)
    payload = json.loads(_demo_board().model_dump_json())
    response = client.post("/export/kicad", json=payload)
    assert response.status_code == 200
    doc = parse_sexpr(response.text)
    assert ["net", "1", "I2C_SDA"] in find_all(doc, "net")
    assert len(find_all(doc, "via")) == 1
