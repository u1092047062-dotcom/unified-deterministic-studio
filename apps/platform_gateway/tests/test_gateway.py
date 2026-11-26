from pathlib import Path
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import gateway


def test_gateway_health_cockpit_and_overview():
    client = TestClient(gateway)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["mounts"] == {"eda": "/eda", "art": "/art"}

    index = client.get("/")
    assert index.status_code == 200
    assert "Unified Deterministic Studio" in index.text

    overview = client.get("/api/overview?ram_gb=7.82&vram_gb=4.0")
    assert overview.status_code == 200
    body = overview.json()
    assert body["resource_plan"]["cargo_build_jobs"] == 2
    assert body["resource_plan"]["allow_quality_downgrade"] is False
    assert len(body["eda"]["templates"]) == 3
    assert body["art"]["checkpoints"]["verified_count"] == 0


def test_gateway_mounts_both_eda_and_art_apis(tmp_path, monkeypatch):
    monkeypatch.setenv("EDA_JOBS_DIR", str(tmp_path / "eda_jobs"))
    monkeypatch.setenv("EDA_LOG_DIR", str(tmp_path / "eda_logs"))
    client = TestClient(gateway)

    eda_health = client.get("/eda/health")
    assert eda_health.status_code == 200
    assert eda_health.json()["service"] == "eda-multiagent-core"

    art_health = client.get("/art/health")
    assert art_health.status_code == 200
    assert art_health.json()["service"] == "art-restoration-ai"

    eda_job = client.post("/eda/jobs")
    assert eda_job.status_code == 200
    assert eda_job.json()["status"] == "completed"
    assert len(eda_job.json()["manifest_sha256"]) == 64
