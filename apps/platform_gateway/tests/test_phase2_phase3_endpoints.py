"""End-to-End Integrationstests ueber das Platform Gateway fuer Phasen 1, 2 und 3."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
for p in (
    ROOT / "packages" / "platform_core",
    ROOT / "packages" / "platform_mcp",
    ROOT / "apps" / "eda-multiagent-core",
    ROOT / "apps" / "art-restoration-ai",
    ROOT / "apps" / "platform_gateway",
):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from main import gateway  # noqa: E402


client = TestClient(gateway)


def test_phase1_phase2_phase3_gateway_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_SIM_DIR", str(tmp_path / "sim_sandbox"))
    client.post("/sim/reset")

    # Phase 1: Gerber-Verifier & LCSC Parts Search ueber /eda
    gv = client.post("/eda/export/gerber/verify")
    assert gv.status_code == 200
    assert gv.json()["valid"] is True
    assert "<svg" in gv.json()["preview_svg"]

    ps = client.get("/eda/parts/search", params={"q": "10k"})
    assert ps.status_code == 200
    assert ps.json()["count"] >= 1

    # Phase 2: MCP Server ueber /mcp/tools & POST /mcp + ART Spandrel/Streaming-I/O Status
    mcp_tools = client.get("/mcp/tools")
    assert mcp_tools.status_code == 200
    assert len(mcp_tools.json()["tools"]) >= 7

    mcp_rpc = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": "eda_list_templates", "arguments": {}},
        },
    )
    assert mcp_rpc.status_code == 200
    assert mcp_rpc.json()["result"]["isError"] is False

    io_resp = client.get("/art/io/backend")
    assert io_resp.status_code == 200
    assert io_resp.json()["active_backend"] in {"pyvips", "pillow-bounded"}

    ck_resp = client.get("/art/checkpoints/status")
    assert ck_resp.status_code == 200
    assert "spandrel_runtime" in ck_resp.json()

    # Phase 3: Panelizer, build123d Enclosure Validator, ART C2PA Status & Simulator C2PA Embedding
    panel = client.post("/eda/export/panel", params={"rows": 2, "cols": 2, "cut_mode": "vcut"})
    assert panel.status_code == 200
    assert panel.json()["board_count"] == 4
    assert len(panel.json()["tooling_holes"]) == 4

    enc = client.post("/eda/export/enclosure/validate")
    assert enc.status_code == 200
    assert enc.json()["valid"] is True
    assert "from build123d import" in enc.json()["build123d_script"]

    c2pa_st = client.get("/art/c2pa/status")
    assert c2pa_st.status_code == 200
    assert c2pa_st.json()["lossless_png_embedding"] is True

    # Simulator Auto-Demo pruefen inkl. automatisch eingebetteter C2PA Content Credentials
    demo = client.post("/sim/auto-demo")
    assert demo.status_code == 200
    session_id = demo.json()["photo_session"]["session_id"]
    sim_c2pa = client.get(f"/sim/media/{session_id}/c2pa")
    assert sim_c2pa.status_code == 200
    body = sim_c2pa.json()
    assert body["valid"] is True
    assert body["pixel_integrity_ok"] is True
    assert body["claim_integrity_ok"] is True
    assert body["simulated"] is True
    assert body["ai_disclosed"] is True
