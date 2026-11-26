import hashlib
import json

import pytest

from core.export_manifest import (
    MANIFEST_SCHEMA_VERSION,
    build_export_manifest,
    summarize_board,
)
from core.schema import BoardState, Component, Net, Pin, PinType, Via


def _sample_board() -> BoardState:
    board = BoardState(width=80.0, height=60.0)
    board.components["U1"] = Component(
        id="U1",
        name="ESP32",
        category="MCU",
        footprint="QFN-32",
        value="ESP32-WROOM-32E",
        lcsc_id="C701344",
        x=30.0,
        y=30.0,
        body_w=5.0,
        body_h=5.0,
        pins={"1": Pin(id="1", name="SDA", pin_type=PinType.OUTPUT, x=28.0, y=30.0)},
    )
    board.components["R1"] = Component(
        id="R1",
        name="R1",
        category="Resistor",
        footprint="0805",
        value="10k",
        lcsc_id="C17414",
        x=50.0,
        y=30.0,
        body_w=2.0,
        body_h=1.25,
        pins={"1": Pin(id="1", name="A", pin_type=PinType.PASSIVE, x=49.0, y=30.0)},
    )
    board.nets["N1"] = Net(id="N1", name="SDA", nodes=[("U1", "1"), ("R1", "1")])
    board.vias.append(Via(net_id="N1", x=40.0, y=30.0))
    return board


def test_build_export_manifest_computes_deterministic_sha256_hashes():
    artifacts = {
        "bom.csv": "Comment,Designator\r\n10k,R1\r\n",
        "board.bin": b"\x00\x01\x02\xff",
    }
    summary = summarize_board(_sample_board())

    m1 = build_export_manifest(artifacts, summary, generated_at_utc="2026-11-26T12:00:00+00:00")
    m2 = build_export_manifest(
        dict(reversed(list(artifacts.items()))),
        summary,
        generated_at_utc="2026-11-26T12:05:00+00:00",
    )

    assert m1["schema_version"] == MANIFEST_SCHEMA_VERSION
    assert m1["artifact_count"] == 2
    assert m1["artifacts"]["bom.csv"]["sha256"] == hashlib.sha256(
        artifacts["bom.csv"].encode("utf-8")
    ).hexdigest()
    assert m1["artifacts"]["bom.csv"]["size_bytes"] == len(artifacts["bom.csv"].encode("utf-8"))
    assert m1["artifacts"]["board.bin"]["sha256"] == hashlib.sha256(
        artifacts["board.bin"]
    ).hexdigest()
    assert m1["artifacts"]["board.bin"]["size_bytes"] == 4
    assert m1["total_size_bytes"] == m1["artifacts"]["bom.csv"]["size_bytes"] + 4
    # Reihenfolge-unabhaengiger und zeitstempel-unabhaengiger Inhalts-Hash
    assert m1["manifest_sha256"] == m2["manifest_sha256"]
    assert m1["board_summary"]["width_mm"] == 80.0
    assert m1["board_summary"]["height_mm"] == 60.0
    assert m1["board_summary"]["component_count"] == 2
    assert m1["board_summary"]["net_count"] == 1
    assert m1["board_summary"]["via_count"] == 1
    assert m1["board_summary"]["drc_error_count"] == 0


def test_build_export_manifest_rejects_invalid_artifact_type():
    with pytest.raises(TypeError):
        build_export_manifest({"bad": 12345}, {})  # type: ignore[dict-item]


def test_export_manifest_endpoint_serves_demo_board():
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from server.server import app

    client = TestClient(app)
    response = client.post("/export/manifest")
    assert response.status_code == 200
    data = response.json()

    expected_artifacts = {"board.kicad_pcb", "board.step", "case.scad", "bom.csv", "cpl.csv"}
    assert set(data["artifacts"].keys()) == expected_artifacts
    assert data["artifact_count"] == 5
    for name in expected_artifacts:
        entry = data["artifacts"][name]
        assert len(entry["sha256"]) == 64
        assert entry["size_bytes"] > 0
    assert len(data["manifest_sha256"]) == 64
    assert data["board_summary"]["component_count"] >= 2
    assert "T" in data["generated_at_utc"]


def test_export_manifest_endpoint_accepts_board_state_and_is_reproducible():
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from server.server import app

    client = TestClient(app)
    payload = json.loads(_sample_board().model_dump_json())

    r1 = client.post("/export/manifest", json=payload)
    r2 = client.post("/export/manifest", json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 200
    d1, d2 = r1.json(), r2.json()

    assert d1["manifest_sha256"] == d2["manifest_sha256"]
    assert d1["artifacts"] == d2["artifacts"]
    assert d1["board_summary"]["width_mm"] == 80.0
    assert d1["board_summary"]["height_mm"] == 60.0
    assert d1["board_summary"]["component_count"] == 2
