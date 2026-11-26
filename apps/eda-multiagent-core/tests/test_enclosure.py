import re

import pytest

from core.enclosure_gen import generate_openscad_case
from core.schema import BoardState, Component


# ---------- Helfer ----------

def _comp(cid: str, x: float, y: float, *, category: str = "resistor",
          footprint: str = "0805", name: str = None, body_w: float = 2.0,
          body_h: float = 1.25) -> Component:
    return Component(id=cid, name=name or cid, category=category, footprint=footprint,
                     x=x, y=y, body_w=body_w, body_h=body_h)


def _board(*comps: Component, width: float = 100.0, height: float = 80.0) -> BoardState:
    board = BoardState(width=width, height=height)
    for comp in comps:
        board.components[comp.id] = comp
    return board


def _variable(scad: str, name: str) -> str:
    m = re.search(rf"^{name}\s*=\s*([^;]+);", scad, re.MULTILINE)
    assert m, f"Variable {name} fehlt im SCAD-Code"
    return m.group(1).strip()


# ---------- Außenmaße ----------

def test_outer_dimensions_board_plus_clearance_and_walls():
    board = _board(_comp("R1", 50.0, 40.0))
    scad = generate_openscad_case(board, wall_thickness=2.0, clearance=0.4)
    # inner = 100 + 2*0.4 = 100.8; outer = inner + 2*2 = 104.8
    assert _variable(scad, "board_w") == "100"
    assert _variable(scad, "inner_w") == "board_w + 2 * clearance"
    assert _variable(scad, "outer_w") == "inner_w + 2 * wall"
    # Symbolische Kette prüfen (OpenSCAD rechnet beim Öffnen):
    assert "outer_w  = inner_w + 2 * wall;" in scad
    assert "inner_w  = board_w + 2 * clearance;" in scad


def test_parameters_appear_as_declared_variables():
    scad = generate_openscad_case(_board(_comp("R1", 50.0, 40.0)),
                                  wall_thickness=2.5, clearance=0.6,
                                  height_padding=4.0, lip_height=2.0)
    assert _variable(scad, "wall") == "2.5"
    assert _variable(scad, "clearance") == "0.6"
    assert _variable(scad, "height_padding") == "4"
    assert _variable(scad, "lip_height") == "2"


# ---------- Schraubdome ----------

def test_mounting_posts_at_hole_coordinates():
    board = _board(
        _comp("MountingHole1", 10.0, 10.0, category="mechanical",
              footprint="MountingHole_2.7mm", body_w=2.7, body_h=2.7),
        _comp("MountingHole2", 90.0, 70.0, category="mechanical",
              footprint="MountingHole_2.7mm", body_w=2.7, body_h=2.7),
        _comp("R1", 50.0, 40.0),
    )
    scad = generate_openscad_case(board)
    calls = re.findall(r"mounting_post\(([-\d.]+), ([-\d.]+)\);", scad)
    assert [("10", "10"), ("90", "70")] == calls  # exakte Koordinaten, sortiert


def test_no_posts_without_mounting_holes():
    scad = generate_openscad_case(_board(_comp("R1", 50.0, 40.0)))
    assert not re.search(r"^\s*mounting_post\(", scad, re.MULTILINE)
    assert "keine MountingHole-Komponenten" in scad


def test_insert_hole_variables_m2_5_and_m3():
    scad = generate_openscad_case(_board(_comp("R1", 50.0, 40.0)))
    assert _variable(scad, "insert_d_m2_5") == "3.5"
    assert _variable(scad, "insert_d_m3") == "4.3"
    assert _variable(scad, "insert_hole_d") == "insert_d_m2_5"


# ---------- Port-Ausschnitte ----------

def test_port_cutout_for_edge_connector():
    board = _board(
        # USB-C an der rechten Kante: Körper ragt bis x=101.47 (über die Kante)
        _comp("J1", 97.0, 40.0, category="connector", footprint="USB-C-16P",
              body_w=8.94, body_h=7.35),
        _comp("R1", 50.0, 40.0),
    )
    scad = generate_openscad_case(board)
    m = re.search(r"port_right\(([-\d.]+), ([-\d.]+)\); // J1", scad)
    assert m, "Port-Cutout an der rechten Wand fehlt"
    y0, y1 = float(m.group(1)), float(m.group(2))
    # Breite = Körper + 2*Clearance = 7.35 + 0.8
    assert y1 - y0 == pytest.approx(7.35 + 2 * 0.4)


def test_no_port_for_mid_board_connector():
    board = _board(
        _comp("J1", 50.0, 40.0, category="connector", footprint="USB-C-16P",
              body_w=8.94, body_h=7.35),
    )
    scad = generate_openscad_case(board)
    # Aufrufe stehen eingerückt in tray_walls(); Moduldefinitionen bei Spalte 0
    assert not re.search(r"^\s+port_(right|left|top|bottom)\(", scad, re.MULTILINE)


def test_no_port_for_non_connector_at_edge():
    board = _board(_comp("R1", 97.0, 40.0))  # Widerstand an der Kante
    scad = generate_openscad_case(board)
    assert not re.search(r"^\s+port_(right|left|top|bottom)\(", scad, re.MULTILINE)


def test_port_cutout_all_four_sides():
    board = _board(
        _comp("J_R", 97.0, 40.0, category="connector", body_w=8.94, body_h=7.35),
        _comp("J_L", 3.0, 40.0, category="connector", body_w=8.94, body_h=7.35),
        _comp("J_T", 50.0, 77.0, category="connector", body_w=8.94, body_h=7.35),
        _comp("J_B", 50.0, 3.0, category="connector", body_w=8.94, body_h=7.35),
    )
    scad = generate_openscad_case(board)
    for side in ("right", "left", "top", "bottom"):
        assert re.search(rf"port_{side}\(", scad), f"Cutout {side} fehlt"


# ---------- Tray/Lid-Struktur ----------

def test_tray_and_lid_modules_with_lip_joint():
    scad = generate_openscad_case(_board(_comp("R1", 50.0, 40.0)))
    for module in ("module mounting_post", "module bottom_tray", "module top_lid",
                   "module tray_lip"):
        assert module in scad
    # Feder am Tray, Falz-Rand am Deckel
    assert "tray_lip()" in scad
    assert "rim_w = lip_w + lid_fit;" in scad
    assert _variable(scad, "tray_h") == "wall + board_t + comp_max_h + height_padding"


def test_lid_is_placed_beside_tray_for_printing():
    scad = generate_openscad_case(_board(_comp("R1", 50.0, 40.0)))
    assert "translate([outer_w + 10, 0, 0]) top_lid();" in scad


# ---------- Syntax & Determinismus ----------

def test_scad_braces_and_parens_balanced():
    scad = generate_openscad_case(_board(
        _comp("MountingHole1", 10.0, 10.0, category="mechanical",
              footprint="MountingHole_2.7mm", body_w=2.7, body_h=2.7),
        _comp("J1", 97.0, 40.0, category="connector", footprint="USB-C-16P",
              body_w=8.94, body_h=7.35),
    ))
    # Kommentare entfernen, dann Balance zählen
    code = "\n".join(line.split("//")[0] for line in scad.splitlines())
    assert code.count("{") == code.count("}")
    assert code.count("(") == code.count(")")
    assert code.count("[") == code.count("]")


def test_scad_statements_terminated_and_modules_present():
    scad = generate_openscad_case(_board(_comp("R1", 50.0, 40.0)))
    for token in ("module mounting_post(x, y)", "module bottom_tray()", "module top_lid()"):
        assert token in scad
    # Deklarierte Variablen je Zeile mit Semikolon
    for line in scad.splitlines():
        stripped = line.split("//")[0].strip()
        if re.fullmatch(r"[a-z_0-9]+ = .+", stripped) and "module" not in stripped:
            assert stripped.endswith(";"), f"Anweisung ohne Semikolon: {stripped}"


def test_generation_is_deterministic():
    board = _board(
        _comp("MountingHole1", 10.0, 10.0, category="mechanical",
              footprint="MountingHole_2.7mm", body_w=2.7, body_h=2.7),
        _comp("J1", 97.0, 40.0, category="connector", footprint="USB-C-16P",
              body_w=8.94, body_h=7.35),
    )
    assert generate_openscad_case(board) == generate_openscad_case(board)


# ---------- Endpunkt ----------

def test_enclosure_endpoint_returns_case_scad():
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from server.server import app
    client = TestClient(app)
    response = client.post("/export/enclosure")
    assert response.status_code == 200
    assert "case.scad" in response.headers["content-disposition"]
    assert response.headers["content-type"].startswith("application/x-openscad")
    assert "module bottom_tray()" in response.text


def test_enclosure_endpoint_accepts_board_state_body():
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from server.server import app
    client = TestClient(app)
    board = _board(
        _comp("MountingHole1", 10.0, 10.0, category="mechanical",
              footprint="MountingHole_2.7mm", body_w=2.7, body_h=2.7),
        width=100.0, height=80.0,
    )
    response = client.post("/export/enclosure", json=board.model_dump())
    assert response.status_code == 200
    assert "mounting_post(10, 10);" in response.text
