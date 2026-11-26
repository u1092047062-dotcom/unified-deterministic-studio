import csv
import io

import pytest

from core.manufacturing_export import (BOM_HEADER, CPL_HEADER, generate_jlcpcb_bom,
                                       generate_jlcpcb_cpl)
from core.schema import BoardState, Component


# ---------- Helfer ----------

def _comp(cid: str, x: float, y: float, lcsc: str = "C17414", value: str = None,
          name: str = None, footprint: str = "0805", rotation: float = 0.0) -> Component:
    return Component(id=cid, name=name or cid, category="resistor", footprint=footprint,
                     x=x, y=y, rotation=rotation, value=value, lcsc_id=lcsc)


def _board(*comps: Component, width: float = 100.0, height: float = 80.0) -> BoardState:
    board = BoardState(width=width, height=height)
    for comp in comps:
        board.components[comp.id] = comp
    return board


def _rows(csv_text: str) -> list:
    return list(csv.reader(io.StringIO(csv_text)))


# ---------- BOM ----------

def test_bom_header():
    board = _board(_comp("R1", 10.0, 10.0))
    rows = _rows(generate_jlcpcb_bom(board))
    assert rows[0] == BOM_HEADER == ["Comment", "Designator", "Footprint", "LCSC Part #"]


def test_bom_contents_and_fallback_comment():
    board = _board(
        _comp("R1", 10.0, 10.0, lcsc="C17414", value="10k"),
        _comp("U1", 40.0, 40.0, lcsc="C701344", footprint="ESP32-WROOM-32E"),
        _comp("R2", 20.0, 20.0, lcsc="C17513"),  # kein value -> Name als Comment
    )
    rows = _rows(generate_jlcpcb_bom(board))
    by_designator = {row[1]: row for row in rows[1:]}
    assert by_designator["R1"] == ["10k", "R1", "0805", "C17414"]
    assert by_designator["U1"] == ["U1", "U1", "ESP32-WROOM-32E", "C701344"]
    assert by_designator["R2"] == ["R2", "R2", "0805", "C17513"]


def test_bom_excludes_components_without_lcsc_id():
    board = _board(
        _comp("R1", 10.0, 10.0),
        _comp("X1", 30.0, 30.0, lcsc=None),
    )
    rows = _rows(generate_jlcpcb_bom(board))
    assert len(rows) == 2  # nur Header + R1
    assert rows[1][1] == "R1"


def test_bom_natural_designator_sort():
    board = _board(
        _comp("R10", 10.0, 10.0, lcsc="C17513"),
        _comp("R2", 20.0, 20.0, lcsc="C17513"),
        _comp("C1", 30.0, 30.0, lcsc="C15850"),
        _comp("U1", 40.0, 40.0, lcsc="C701344"),
    )
    rows = _rows(generate_jlcpcb_bom(board))
    designators = [row[1] for row in rows[1:]]
    assert designators == ["C1", "R2", "R10", "U1"]


def test_bom_quotes_values_with_comma():
    board = _board(_comp("R1", 10.0, 10.0, value="Widerstand, 1%"))
    rows = _rows(generate_jlcpcb_bom(board))
    assert rows[1][0] == "Widerstand, 1%"
    text = generate_jlcpcb_bom(board)
    assert '"Widerstand, 1%"' in text


def test_bom_deterministic_and_empty_board():
    board = _board(_comp("R1", 10.0, 10.0), _comp("R2", 20.0, 20.0, lcsc="C17513"))
    assert generate_jlcpcb_bom(board) == generate_jlcpcb_bom(board)
    # leer bzw. ohne LCSC-Referenz: nur Header
    assert generate_jlcpcb_bom(BoardState()) == ",".join(BOM_HEADER) + "\r\n"


# ---------- CPL ----------

def test_cpl_header():
    board = _board(_comp("R1", 10.0, 10.0))
    rows = _rows(generate_jlcpcb_cpl(board))
    assert rows[0] == CPL_HEADER == ["Designator", "Val", "Package", "Mid X", "Mid Y",
                                     "Rotation", "Layer"]


def test_cpl_coordinates_are_center_origin():
    # Board 100x80 -> Ursprung (50, 40); Komponente bei (30, 40) -> (-20, 0)
    board = _board(_comp("R1", 30.0, 40.0, value="10k"))
    rows = _rows(generate_jlcpcb_cpl(board))
    assert rows[1] == ["R1", "10k", "0805", "-20", "0", "0", "top"]


def test_cpl_rotation_and_compact_numbers():
    board = _board(_comp("R1", 75.25, 12.5, rotation=90.0))
    rows = _rows(generate_jlcpcb_cpl(board))
    assert rows[1][3] == "25.25"
    assert rows[1][4] == "-27.5"
    assert rows[1][5] == "90"
    assert rows[1][6] == "top"


def test_cpl_excludes_components_without_lcsc_id():
    board = _board(_comp("R1", 10.0, 10.0), _comp("X1", 30.0, 30.0, lcsc=None))
    rows = _rows(generate_jlcpcb_cpl(board))
    assert len(rows) == 2
    assert rows[1][0] == "R1"


def test_cpl_deterministic_and_sorted():
    board = _board(
        _comp("R10", 10.0, 10.0, lcsc="C17513"),
        _comp("R2", 20.0, 20.0, lcsc="C17513"),
    )
    text = generate_jlcpcb_cpl(board)
    assert text == generate_jlcpcb_cpl(board)
    designators = [row[0] for row in _rows(text)[1:]]
    assert designators == ["R2", "R10"]


def test_cpl_empty_board_is_header_only():
    rows = _rows(generate_jlcpcb_cpl(BoardState()))
    assert rows == [CPL_HEADER]


# ---------- Endpunkte ----------

def test_bom_endpoint_serves_demo_pipeline():
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from server.server import app
    client = TestClient(app)
    response = client.get("/export/bom")
    assert response.status_code == 200
    assert "bom.csv" in response.headers["content-disposition"]
    assert response.headers["content-type"].startswith("text/csv")
    rows = _rows(response.text)
    assert rows[0] == BOM_HEADER
    designators = {row[1] for row in rows[1:]}
    # Demo-Board läuft durch die Pipeline -> Teile sind aufgelöst
    assert {"U1", "R1"} <= designators


def test_cpl_endpoint_serves_demo_pipeline():
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from server.server import app
    client = TestClient(app)
    response = client.get("/export/cpl")
    assert response.status_code == 200
    assert "cpl.csv" in response.headers["content-disposition"]
    rows = _rows(response.text)
    assert rows[0] == CPL_HEADER
    by_designator = {row[0]: row for row in rows[1:]}
    assert by_designator["R1"][1] == "10k"      # Kommentar aus dem Wert
    assert by_designator["R1"][2] == "0805"
    assert by_designator["R1"][6] == "top"


def test_cpl_endpoint_accepts_board_state_body():
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from server.server import app
    client = TestClient(app)
    board = _board(_comp("R9", 60.0, 70.0, lcsc="C17673", value="4k7"),
                   width=80.0, height=60.0)
    response = client.request("GET", "/export/cpl", json=board.model_dump())
    assert response.status_code == 200
    rows = _rows(response.text)
    assert rows[1] == ["R9", "4k7", "0805", "20", "40", "0", "top"]
