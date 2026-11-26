"""End-to-End Gateway-Integrationstest ueber alle Frontier-Phasen 4 bis 9."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sys

from fastapi.testclient import TestClient
from PIL import Image

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
from platform_core.c2pa_credentials import build_c2pa_manifest  # noqa: E402
from platform_core.crypto_signing import embed_signed_png_c2pa, generate_ed25519_keypair  # noqa: E402


client = TestClient(gateway)


def test_frontier_phases4_to_9_gateway_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_WORKFLOW_DB", str(tmp_path / "gw_wf.sqlite"))
    monkeypatch.setenv("PLATFORM_OTEL_LOG", str(tmp_path / "gw_spans.jsonl"))

    # Phase 8: Frontier Inspector UI
    ins = client.get("/inspector")
    assert ins.status_code == 200
    assert "Frontier WebGPU" in ins.text

    # Phase 5: Trust Status & Ed25519-signiertes C2PA-PNG ueber /trust/verify pruefen
    ts = client.get("/trust/status")
    assert ts.status_code == 200
    assert ts.json()["hardware_trust"]["ed25519_rfc8032_builtin"] is True

    kp = generate_ed25519_keypair()
    buf = BytesIO()
    Image.new("RGB", (32, 32), (10, 80, 160)).save(buf, format="PNG")
    signed_png = embed_signed_png_c2pa(
        buf.getvalue(),
        build_c2pa_manifest("gw-test.png", source_sha256="e" * 64, actions=[{"action": "c2pa.created"}]),
        kp["private_seed_hex"],
    )
    tv = client.post("/trust/verify", files={"file": ("signed.png", signed_png, "image/png")})
    assert tv.status_code == 200
    assert tv.json()["valid"] is True
    assert tv.json()["asymmetric_trust"]["signature_valid"] is True

    # Phase 6: ACEScg Color & SOTA Router
    assert client.get("/art/color/status").json()["linear_float32_engine"] is True
    sr = client.post("/art/sota/route", json={"quality_profile": "cinema", "media_type": "video", "vram_gb": 24, "allow_generative": True})
    assert sr.status_code == 200
    assert "SeedVR2" in sr.json()["primary_model"]

    # Phase 7 & 8: Physics Sim, 3D Assembly, KiCad 9 IPC
    assert client.post("/eda/simulate/physics").status_code == 200
    assert client.post("/eda/export/assembly/check-3d").json()["collision_free"] is True
    assert client.get("/eda/kicad9/ipc/status").status_code == 200

    # Phase 9 & 4: A2A AgentCard -> Closed-Loop Task -> Durable Workflows & OTLP Traces abfragen
    card = client.get("/.well-known/agent.json")
    assert card.status_code == 200
    assert card.json()["protocol"] == "a2a.v1"

    task = client.post(
        "/a2a/tasks",
        json={"skill": "closed_loop_pcb_engineering", "input": {"template_id": "esp32_i2c_sensor"}},
    )
    assert task.status_code == 200
    wid = task.json()["workflow_id"]
    assert task.json()["trust_verified"] is True

    wf_resp = client.get(f"/workflows/{wid}")
    assert wf_resp.status_code == 200
    assert wf_resp.json()["status"] == "completed"

    tr_resp = client.get("/telemetry/traces")
    assert tr_resp.status_code == 200
    assert len(tr_resp.json()["spans"]) >= 1
