import pytest

from core.board_synthesizer import build_template_board
from core.gerber_export import detect_kicad_cli, export_gerber_bundle, export_gerber_with_manifest
from core.pipeline_v2 import DesignPipeline


def test_gerber_and_excellon_export_structure_and_determinism():
    board = DesignPipeline().run(build_template_board("esp32_i2c_sensor")).board
    b1 = export_gerber_bundle(board)
    b2 = export_gerber_bundle(board)
    assert b1 == b2
    assert set(b1.keys()) == {"F_Cu.gtl", "B_Cu.gbl", "Edge_Cuts.gm1", "board.drl"}

    for layer_name in ("F_Cu.gtl", "B_Cu.gbl", "Edge_Cuts.gm1"):
        text = b1[layer_name]
        assert "%FSLAX46Y46*%" in text
        assert "%MOMM*%" in text
        assert text.strip().endswith("M02*")

    assert "D03*" in b1["F_Cu.gtl"]
    assert "D01*" in b1["Edge_Cuts.gm1"]

    drill = b1["board.drl"]
    assert drill.startswith("M48\n")
    assert "METRIC,TZ" in drill
    assert "T1C" in drill
    assert drill.strip().endswith("M30")


def test_gerber_endpoint_and_kicad_cli_probe():
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from server.server import app

    cli = detect_kicad_cli()
    assert "available" in cli

    client = TestClient(app)
    resp = client.post("/export/gerber")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["files"].keys()) == {"F_Cu.gtl", "B_Cu.gbl", "Edge_Cuts.gm1", "board.drl"}
    assert body["manifest"]["artifact_count"] == 4
    assert len(body["manifest"]["manifest_sha256"]) == 64

    direct = export_gerber_with_manifest(DesignPipeline().run(build_template_board("rp2040_power_node")).board)
    assert direct["manifest"]["artifact_count"] == 4
