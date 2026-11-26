from io import BytesIO
from pathlib import Path
import sys
import zipfile

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
    assert "100 % Interaktiver Simulator" in page.text

    assert client.post("/sim/telegram/command", json={"command": "/status"}).status_code == 409

    demo = client.post("/sim/media/demo")
    assert demo.status_code == 200
    sid = demo.json()["session_id"]
    assert client.post("/sim/media/process", json={"session_id": sid}).status_code == 409


def test_simulator_photo_tiles_interrupt_resume_and_topaz_handoff():
    client = TestClient(gateway)
    client.post("/sim/accounts/setup", json={"activate_all": True, "runpod_budget_usd": 5.0})

    buf = BytesIO()
    Image.new("RGB", (400, 300), (90, 120, 160)).save(buf, format="PNG")
    buf.seek(0)
    up = client.post("/sim/media/upload", files={"file": ("kunst.png", buf, "image/png")})
    assert up.status_code == 200
    sid = up.json()["session_id"]

    # Kachel-Overlay testen
    tp = client.post(f"/sim/media/{sid}/tile-plan?tile_size=256&overlap=32")
    assert tp.status_code == 200
    assert tp.json()["tile_count"] >= 4
    assert client.get(f"/sim/media/{sid}/tile-overlay").status_code == 200

    # Budget-Gate blockt bei $0.00
    blocked = client.post(
        "/sim/media/process",
        json={"session_id": sid, "budget_override_usd": 0.0, "gpu_override": "RTX_4090_24GB"},
    )
    assert blocked.status_code == 402

    # Unterbrechung + Config-Hash-Sperre + erfolgreiches Resume
    inter = client.post(
        "/sim/media/process",
        json={"session_id": sid, "tile_size": 256, "overlap": 32, "simulate_interrupt": True},
    )
    assert inter.status_code == 200
    assert inter.json()["status"] == "interrupted"

    # Resume mit geaenderter Kachelgroesse wird fail-closed abgewiesen (409)
    bad_resume = client.post("/sim/media/resume", json={"session_id": sid, "tile_size": 512})
    assert bad_resume.status_code == 409

    # Resume mit identischer Config schliesst sauber ab
    ok_resume = client.post("/sim/media/resume", json={"session_id": sid})
    assert ok_resume.status_code == 200
    assert ok_resume.json()["status"] == "completed_simulated"
    assert ok_resume.json()["quality_metrics"]["resumed_from_tile"] >= 1
    assert client.get(f"/sim/media/{sid}/result").status_code == 200
    assert client.get(f"/sim/media/{sid}/provenance").status_code == 200

    # Kompletter Topaz Desktop Handoff (Export -> Import)
    texp = client.post("/sim/topaz/export", json={"session_id": sid})
    assert texp.status_code == 200
    assert texp.json()["status"] == "exported_waiting_import"

    timp = client.post("/sim/topaz/import", json={"session_id": sid})
    assert timp.status_code == 200
    assert timp.json()["status"] == "imported_verified"
    assert len(timp.json()["imported_sha256"]) == 64


def test_simulator_eda_z3_svg_7_downloads_and_unsat_conflict():
    client = TestClient(gateway)
    client.post("/sim/accounts/setup", json={"activate_all": True})

    # SAT-Lauf mit Z3 + SVG + 7 Downloads
    eda = client.post(
        "/sim/eda/run",
        json={"template_id": "esp32_i2c_sensor", "run_z3_pinout": True, "force_z3_conflict": False},
    )
    assert eda.status_code == 200
    body = eda.json()
    assert body["pipeline_status"] == "OK"
    assert set(body["pinout"].keys()) == {"SDA", "SCL"}
    assert "<svg" in body["svg"]
    assert set(body["download_urls"].keys()) == {
        "board.kicad_pcb",
        "board.step",
        "case.scad",
        "bom.csv",
        "cpl.csv",
        "gerbers.zip",
        "export_manifest.json",
    }

    # Echter Download von gerbers.zip pruefen
    gz = client.get(body["download_urls"]["gerbers.zip"])
    assert gz.status_code == 200
    with zipfile.ZipFile(BytesIO(gz.content)) as zf:
        assert set(zf.namelist()) == {"F_Cu.gtl", "B_Cu.gbl", "Edge_Cuts.gm1", "board.drl"}

    # Z3-UNSAT-Konflikt-Simulation
    unsat = client.post(
        "/sim/eda/run",
        json={"template_id": "esp32_i2c_sensor", "run_z3_pinout": True, "force_z3_conflict": True},
    )
    assert unsat.status_code == 200
    assert unsat.json()["pipeline_status"] == "SOLVER_FAILED"


def test_simulator_video_telegram_touch_dashboard_process_alerts_and_security():
    client = TestClient(gateway)

    # 1-Klick Vollautomatische Komplett-Demo testen
    demo = client.post("/sim/auto-demo")
    assert demo.status_code == 200
    assert demo.json()["status"] == "full_auto_demo_completed"

    # Video-Upload
    fake_mp4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + (b"\x00" * 4096)
    up = client.post("/sim/media/upload", files={"file": ("clip.mp4", BytesIO(fake_mp4), "video/mp4")})
    assert up.status_code == 200
    sid = up.json()["session_id"]
    assert client.post("/sim/media/process", json={"session_id": sid}).status_code == 200

    # Telegram 2-Stufen-Shutdown + /abort + Path-Traversal-Sperre
    ask = client.post("/sim/telegram/callback", json={"action": "ask_shutdown"})
    assert ask.status_code == 200
    assert ask.json()["pending_confirmation"] == "shutdown"

    conf = client.post("/sim/telegram/callback", json={"action": "confirm_power"})
    assert conf.status_code == 200
    assert "geplant" in conf.json()["reply"]

    ab = client.post("/sim/telegram/command", json={"command": "/abort"})
    assert ab.status_code == 200
    assert "abgebrochen" in ab.json()["reply"]

    trav = client.post("/sim/telegram/command", json={"command": "/tail ../.env"})
    assert trav.status_code == 200
    assert "FAIL-CLOSED BLOCKIERT" in trav.json()["reply"]

    # Prozess-Watchlist Event
    pev = client.post("/sim/telegram/process-event", json={"process_name": "tpai.exe", "action": "start"})
    assert pev.status_code == 200
    assert pev.json()["is_priority"] is True

    # Live Secret-Scanner
    syn_token = "998877665" + ":" + ("B" * 30)
    scan = client.post("/sim/qa/secret-scan", json={"sample_text": f"token={syn_token}"})
    assert scan.status_code == 200
    assert scan.json()["clean"] is False
    assert "telegram_bot_token" in scan.json()["secret_types_detected"]
    assert syn_token not in scan.json()["redacted_preview"]
