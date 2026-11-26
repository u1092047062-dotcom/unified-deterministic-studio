import pytest

from core.job_runner import EDAJobRunner
from core.schema import BoardState
from core.telemetry_log import reset_default_logger


@pytest.fixture(autouse=True)
def _reset_logs():
    yield
    reset_default_logger()


def test_eda_health_and_capabilities_endpoints():
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from server.server import app

    client = TestClient(app)
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["service"] == "eda-multiagent-core"

    caps = client.get("/capabilities?ram_gb=7.82")
    assert caps.status_code == 200
    body = caps.json()
    assert body["resource_plan"]["cargo_build_jobs"] == 2
    assert body["resource_plan"]["allow_quality_downgrade"] is False
    assert "sha256-manifest" in body["exporters"]


def test_eda_jobs_endpoint_persists_and_retrieves_provenance(tmp_path, monkeypatch):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient
    from server.server import app

    monkeypatch.setenv("EDA_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("EDA_LOG_DIR", str(tmp_path / "logs"))
    reset_default_logger()

    client = TestClient(app)
    created = client.post("/jobs")
    assert created.status_code == 200
    job = created.json()
    assert job["status"] == "completed"
    assert job["domain"] == "eda"
    assert len(job["config_sha256"]) == 64
    assert len(job["manifest_sha256"]) == 64
    assert set(job["artifacts"].keys()) == {
        "board.kicad_pcb",
        "board.step",
        "case.scad",
        "bom.csv",
        "cpl.csv",
    }

    fetched = client.get(f"/jobs/{job['job_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["manifest_sha256"] == job["manifest_sha256"]

    assert client.get("/jobs/missing-job-999").status_code == 404
    assert client.get("/jobs/bad..id").status_code == 400


def test_eda_job_runner_deterministic_config_hash(tmp_path):
    runner = EDAJobRunner(jobs_dir=tmp_path)
    board = BoardState(width=50.0, height=40.0)
    r1 = runner.submit_and_run(board, job_id="eda-det-1")
    r2 = runner.submit_and_run(board, job_id="eda-det-2")

    assert r1["config_sha256"] == r2["config_sha256"]
    assert r1["manifest_sha256"] == r2["manifest_sha256"]
    assert len(runner.list_jobs()) == 2
