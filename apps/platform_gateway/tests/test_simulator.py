from io import BytesIO
from pathlib import Path
import sys

from fastapi.testclient import TestClient
from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import gateway


@pytest.fixture(autouse=True)
def _isolated_sim(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_SIM_DIR", str(tmp_path / "sim"))
    client = TestClient(gateway)
    client.post("/sim/reset")
    yield


def test_simulator_ui_and_fail_closed_before_account_setup():
    client = TestClient(gateway)

    page = client.get("/simulator")
    assert page.status_code == 200
    assert "Interaktiver End-to-End Plattform-Simulator" in page.text

    # Vor Account-Setup muessen geschuetzte Pfade fail-closed abweisen
    tg_blocked = client.post("/sim/telegram/command", json={"command": "/status"})
    assert tg_blocked.status_code == 409

    demo = client.post("/sim/media/demo")
    assert demo.status_code == 200
    sid = demo.json()["session_id"]
    proc_blocked = client.post("/sim/media/process", json={"session_id": sid})
    assert proc_blocked.status_code == 409


def test_simulator_photo_upload_processing_budget_gate_and_provenance():
    client = TestClient(gateway)

    # Alle Accounts in einem Schritt simulieren
    setup = client.post("/sim/accounts/setup", json={"activate_all": True, "runpod_budget_usd": 5.0})
    assert setup.status_code == 200
    assert setup.json()["accounts"]["runpod"]["connected"] is True
    assert setup.json()["accounts"]["telegram"]["connected"] is True
    assert setup.json()["accounts"]["checkpoints"]["verified"] is True

    # Echtes PNG hochladen
    buf = BytesIO()
    Image.new("RGB", (320, 240), (90, 120, 160)).save(buf, format="PNG")
    buf.seek(0)
    up = client.post("/sim/media/upload", files={"file": ("kunst.png", buf, "image/png")})
    assert up.status_code == 200
    up_data = up.json()
    sid = up_data["session_id"]
    assert up_data["media_type"] == "photo"
    assert len(up_data["original_sha256"]) == 64

    # Budget-Gate greift real (Budget = 0.0 auf bezahlter GPU -> 402 Payment/Budget blocked)
    blocked = client.post(
        "/sim/media/process",
        json={"session_id": sid, "budget_override_usd": 0.0, "gpu_override": "RTX_4090_24GB"},
    )
    assert blocked.status_code == 402

    # Erfolgreicher Durchlauf mit 2x Upscale-Simulation
    done = client.post(
        "/sim/media/process",
        json={"session_id": sid, "mode": "upscale_2x", "tile_size": 256, "overlap": 32},
    )
    assert done.status_code == 200
    body = done.json()
    assert body["status"] == "completed_simulated"
    assert body["quality_metrics"]["output_dimensions"] == [640, 480]
    assert len(body["manifest_sha256"]) == 64

    assert client.get(f"/sim/media/{sid}/original").status_code == 200
    assert client.get(f"/sim/media/{sid}/result").status_code == 200
    prov = client.get(f"/sim/media/{sid}/provenance")
    assert prov.status_code == 200
    assert prov.json()["manifest_sha256"] == body["manifest_sha256"]


def test_simulator_video_upload_telegram_console_and_eda_run():
    client = TestClient(gateway)
    client.post("/sim/accounts/setup", json={"activate_all": True})

    # Video-Upload simulieren (synthetischer MP4-Header)
    fake_mp4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + (b"\x00" * 4096)
    up = client.post("/sim/media/upload", files={"file": ("clip.mp4", BytesIO(fake_mp4), "video/mp4")})
    assert up.status_code == 200
    sid = up.json()["session_id"]
    assert up.json()["media_type"] == "video"

    proc = client.post("/sim/media/process", json={"session_id": sid, "mode": "restoration_enhance"})
    assert proc.status_code == 200
    assert proc.json()["media_type"] == "video"
    assert client.get(f"/sim/media/{sid}/result").status_code == 200

    # Telegram-Befehle inkl. Fail-Closed-Guardrails testen
    st = client.post("/sim/telegram/command", json={"command": "/status"})
    assert st.status_code == 200
    assert "TerraRemoteEngine" in st.json()["reply"]

    scr = client.post("/sim/telegram/command", json={"command": "/screen"})
    assert scr.status_code == 200
    assert "FAIL-CLOSED" in scr.json()["reply"]

    kill_res = client.post("/sim/telegram/command", json={"command": "/kill 1234"})
    assert kill_res.status_code == 200
    assert "FAIL-CLOSED" in kill_res.json()["reply"]

    # EDA-End-to-End-Simulation
    eda = client.post("/sim/eda/run", json={"template_id": "esp32_i2c_sensor"})
    assert eda.status_code == 200
    assert eda.json()["pipeline_status"] == "OK"
    assert len(eda.json()["gerber_files"]) == 4
    assert "PASS" in eda.json()["kicad_cli_signoff"]
