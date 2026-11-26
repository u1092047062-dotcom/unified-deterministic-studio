import math
import re

import pytest

from core.step_export import BOARD_THICKNESS, BODY_HEIGHT, generate_step
from core.schema import BoardState, Component


# ---------- Helfer ----------

def _entities(step_text: str) -> dict:
    """#ID -> Entity-Körper (ohne Semikolon), ein Entity je Zeile."""
    result = {}
    for line in step_text.splitlines():
        m = re.fullmatch(r"#(\d+) = (.+);", line.strip())
        if m:
            result[int(m.group(1))] = m.group(2)
    return result


def _find(entities: dict, needle: str) -> list:
    return [eid for eid, body in entities.items() if needle in body]


def _points(step_text: str) -> list:
    # STEP-Reals enden mit '.', daher pro Gruppe den Punkt am Ende abtrennen
    return [tuple(float(v.rstrip(".")) for v in m.groups())
            for m in re.finditer(r"CARTESIAN_POINT\('',\(([-\d.]+),([-\d.]+),([-\d.]+)\)\)", step_text)]


def _demo_board() -> BoardState:
    board = BoardState(width=100.0, height=80.0)
    board.components["U1"] = Component(id="U1", name="U1", category="MCU", footprint="QFN-32",
                                       x=30.0, y=40.0, body_w=5.0, body_h=5.0)
    board.components["R1"] = Component(id="R1", name="R1", category="Resistor", footprint="0805",
                                       x=70.0, y=40.0, body_w=2.0, body_h=1.25, rotation=90.0)
    board.components["R2"] = Component(id="R2", name="R2", category="Resistor", footprint="0805",
                                       x=20.0, y=15.0)  # ohne body -> Default 4x4
    return board


# ---------- Datei-Grundgerüst ----------

def test_file_skeleton():
    text = generate_step(_demo_board())
    assert text.startswith("ISO-10303-21;\n")
    assert text.rstrip().endswith("END-ISO-10303-21;")
    assert "HEADER;" in text and "DATA;" in text and "ENDSEC;" in text
    assert "AUTOMOTIVE_DESIGN" in text
    assert "SI_UNIT(.MILLI.,.METRE.)" in text  # Millimeter als Längeneinheit
    assert "UNCERTAINTY_MEASURE_WITH_UNIT" in text


def test_solids_count_board_plus_components():
    entities = _entities(generate_step(_demo_board()))
    solids = _find(entities, "MANIFOLD_SOLID_BREP(")
    assert len(solids) == 1 + 3  # Board-Slab + drei Bauteilkörper
    assert len(_find(entities, "ADVANCED_BREP_SHAPE_REPRESENTATION(")) == 1
    # Je Körper: 8 Vertex, 12 Edge, 6 Face
    assert len(_find(entities, "VERTEX_POINT(")) == 4 * 8
    assert len(_find(entities, "EDGE_CURVE(")) == 4 * 12
    assert len(_find(entities, "ADVANCED_FACE(")) == 4 * 6


def test_each_edge_referenced_twice_with_opposite_orientation():
    entities = _entities(generate_step(_demo_board()))
    edge_ids = set(_find(entities, "EDGE_CURVE("))
    references = {}
    for eid, body in entities.items():
        m = re.fullmatch(r"ORIENTED_EDGE\('',\*,\*,#(\d+),\.(T|F)\.\)", body)
        if m:
            references.setdefault(int(m.group(1)), []).append(m.group(2) == "T")
    assert set(references) == edge_ids
    for edge_id, orientations in references.items():
        assert len(orientations) == 2, f"Kante #{edge_id} nicht genau 2x referenziert"
        assert orientations[0] != orientations[1], \
            f"Kante #{edge_id} nicht gegensinnig orientiert"


# ---------- Geometrie ----------

def test_board_slab_dimensions():
    text = generate_step(_demo_board())
    points = [p for p in _points(text)]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    assert min(xs) == 0.0 and max(xs) == 100.0
    assert min(ys) == 0.0 and max(ys) == 80.0
    assert min(zs) == -BOARD_THICKNESS and max(zs) == BODY_HEIGHT


def test_component_sits_on_top_surface():
    text = generate_step(_demo_board())
    points = _points(text)
    # U1 bei (30,40), 5x5: Eckpunkte (27.5..32.5, 37.5..42.5) in beiden Höhen
    for corner in ((27.5, 37.5, 0.0), (32.5, 42.5, 0.0), (27.5, 37.5, BODY_HEIGHT),
                   (32.5, 42.5, BODY_HEIGHT)):
        assert corner in points, f"Ecke {corner} fehlt"


def test_rotation_is_applied():
    # R1: 2x1.25 bei (70,40), rotation=90 -> lokal (1,0) -> (0,1)
    text = generate_step(_demo_board())
    points = _points(text)
    # Rotierte Ecke: lokal (1, 0.625) -> (-0.625, 1) relativ -> (69.375, 41.0)
    assert (69.375, 41.0, 0.0) in points
    assert (69.375, 41.0, BODY_HEIGHT) in points
    # Unrotierte Ecke (71.0, 40.625) darf nicht existieren
    assert (71.0, 40.625, 0.0) not in points


def test_default_body_for_components_without_dimensions():
    text = generate_step(_demo_board())
    points = _points(text)
    # R2 ohne body_w/h -> 4x4 bei (20,15): Ecken (18..22, 13..17)
    for corner in ((18.0, 13.0, 0.0), (22.0, 17.0, BODY_HEIGHT)):
        assert corner in points


def test_component_without_lcsc_is_still_in_step():
    # Anders als BOM/CPL: STEP zeigt Mechanik unabhängig von der Katalogreferenz
    board = BoardState(width=40.0, height=40.0)
    board.components["X1"] = Component(id="X1", name="X1", category="misc", footprint="FOO",
                                       x=20.0, y=20.0)
    entities = _entities(generate_step(board))
    assert len(_find(entities, "MANIFOLD_SOLID_BREP(")) == 2


# ---------- Robustheit ----------

def test_export_is_deterministic():
    a = generate_step(_demo_board())
    b = generate_step(_demo_board())
    assert a == b


def test_entity_ids_are_sequential_and_unique():
    entities = _entities(generate_step(_demo_board()))
    assert sorted(entities) == list(range(1, len(entities) + 1))


def test_product_structure_is_linked():
    text = generate_step(_demo_board())
    entities = _entities(text)
    assert len(_find(entities, "PRODUCT_DEFINITION_SHAPE(")) == 1
    assert len(_find(entities, "SHAPE_DEFINITION_REPRESENTATION(")) == 1
    # BREP ist über SHAPE_DEFINITION_REPRESENTATION an das Produkt gebunden
    sdr = entities[_find(entities, "SHAPE_DEFINITION_REPRESENTATION(")[0]]
    brep_ref = int(re.search(r"#(\d+)\)$", sdr).group(1))
    assert "ADVANCED_BREP_SHAPE_REPRESENTATION" in entities[brep_ref]


# ---------- Endpunkt ----------

def test_step_endpoint_serves_demo_pipeline():
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from server.server import app
    client = TestClient(app)
    response = client.post("/export/step")
    assert response.status_code == 200
    assert "board.step" in response.headers["content-disposition"]
    assert response.text.startswith("ISO-10303-21;")
    # Demo-Board + Pipeline: Board-Slab + U1 + R1
    entities = _entities(response.text)
    assert len(_find(entities, "MANIFOLD_SOLID_BREP(")) == 3


def test_step_endpoint_accepts_board_state_body():
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from server.server import app
    client = TestClient(app)
    board = BoardState(width=40.0, height=40.0)
    board.components["X1"] = Component(id="X1", name="X1", category="misc", footprint="FOO",
                                       x=20.0, y=20.0)
    response = client.post("/export/step", json=board.model_dump())
    assert response.status_code == 200
    entities = _entities(response.text)
    assert len(_find(entities, "MANIFOLD_SOLID_BREP(")) == 2
