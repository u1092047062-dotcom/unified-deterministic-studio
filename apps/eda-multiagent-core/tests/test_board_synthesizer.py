import pytest

from core.board_synthesizer import (
    ComponentSpec,
    DesignSpec,
    NetSpec,
    PinSpec,
    build_template_board,
    list_templates,
    synthesize_board,
)
from core.pipeline_v2 import DesignPipeline, STATUS_OK
from core.schema import PinType


def test_all_builtin_templates_synthesize_and_pass_pipeline():
    templates = list_templates()
    assert len(templates) == 3
    for item in templates:
        board = build_template_board(item["id"])
        result = DesignPipeline().run(board)
        assert result.status == STATUS_OK
        assert result.board.errors == []
        assert len(result.board.traces) >= 1


def test_synthesize_board_validates_net_and_duplicate_ids():
    bad_net = DesignSpec(
        components=[
            ComponentSpec(
                id="U1",
                name="MCU",
                category="MCU",
                footprint="QFN-32",
                pins=[PinSpec(id="1", name="A", pin_type=PinType.OUTPUT)],
            )
        ],
        nets=[NetSpec(id="N1", name="N1", nodes=[("U1", "1"), ("MISSING", "1")])],
    )
    with pytest.raises(ValueError, match="unbekannte Komponente"):
        synthesize_board(bad_net)

    dup_comp = DesignSpec(
        components=[
            ComponentSpec(id="R1", name="R1", category="Resistor", footprint="0805"),
            ComponentSpec(id="R1", name="R1b", category="Resistor", footprint="0805"),
        ]
    )
    with pytest.raises(ValueError, match="Doppelte Komponenten-ID"):
        synthesize_board(dup_comp)


def test_design_synthesize_endpoint_and_websocket_template():
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from server.server import app

    client = TestClient(app)
    tpl_resp = client.get("/templates")
    assert tpl_resp.status_code == 200
    assert len(tpl_resp.json()["templates"]) == 3

    spec_payload = {
        "name": "sensor-mini",
        "width": 60.0,
        "height": 40.0,
        "components": [
            {
                "id": "U1",
                "name": "ESP32",
                "category": "MCU",
                "footprint": "QFN-32",
                "value": "ESP32-WROOM-32E",
                "pins": [
                    {"id": "1", "name": "SDA", "pin_type": "OUTPUT"},
                    {"id": "2", "name": "GND", "pin_type": "GROUND"},
                ],
            },
            {
                "id": "R1",
                "name": "R_10k",
                "category": "Resistor",
                "footprint": "0805",
                "value": "10k",
                "pins": [{"id": "1", "name": "A", "pin_type": "PASSIVE"}],
            },
        ],
        "nets": [
            {"id": "N_SDA", "name": "SDA", "nodes": [["U1", "1"], ["R1", "1"]]},
            {"id": "GND", "name": "GND", "nodes": [["U1", "2"]]},
        ],
    }
    resp = client.post("/design/synthesize", json=spec_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == STATUS_OK
    assert data["board"]["errors"] == []

    with client.websocket_connect("/ws/cad") as ws:
        ws.send_json({"template": "rp2040_power_node"})
        msg = ws.receive_json()
        assert msg["report"]["status"] == STATUS_OK
        assert "U1" in msg["board"]["components"]
