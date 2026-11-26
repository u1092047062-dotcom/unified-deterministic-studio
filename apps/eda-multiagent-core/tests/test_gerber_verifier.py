import pytest

from core.board_synthesizer import build_template_board
from core.gerber_export import export_gerber_bundle
from core.gerber_verifier import validate_gerber_layer, verify_and_render_gerbers
from core.pipeline_v2 import DesignPipeline


def test_gerber_verifier_validates_and_renders_exported_bundle():
    board = DesignPipeline().run(build_template_board("esp32_i2c_sensor")).board
    bundle = export_gerber_bundle(board)
    report = verify_and_render_gerbers(bundle)

    assert report["valid"] is True
    assert report["error_count"] == 0
    assert "<svg" in report["preview_svg"]
    assert report["layers"]["F_Cu.gtl"]["flash_count"] >= 4
    assert report["layers"]["Edge_Cuts.gm1"]["segment_count"] == 4
    assert report["layers"]["board.drl"]["drill_hits"] >= 1


def test_gerber_verifier_flags_undefined_aperture_and_missing_eof():
    bad = validate_gerber_layer("bad.gtl", "%FSLAX46Y46*%\n%MOMM*%\nD99*\nX0Y0D01*\n")
    assert bad["valid"] is False
    assert any("Undefinierte Apertur D99" in e for e in bad["errors"])
    assert any("M02*" in e for e in bad["errors"])


def test_gerber_verify_and_parts_search_endpoints():
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from server.server import app

    client = TestClient(app)
    gv = client.post("/export/gerber/verify")
    assert gv.status_code == 200
    assert gv.json()["valid"] is True
    assert "<svg" in gv.json()["preview_svg"]

    ps = client.get("/parts/search?q=ESP32")
    assert ps.status_code == 200
    assert ps.json()["count"] >= 1
