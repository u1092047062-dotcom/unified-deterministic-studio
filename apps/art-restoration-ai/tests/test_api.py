from fastapi.testclient import TestClient
from app.main import app
from PIL import Image
from io import BytesIO

client = TestClient(app)


def _project_with_image():
    project = client.post("/projects", params={"name": "API test"})
    pid = project.json()["project_id"]
    buffer = BytesIO()
    Image.new("RGB", (160, 140), "white").save(buffer, format="PNG")
    buffer.seek(0)
    upload = client.post(f"/projects/{pid}/source", files={"file": ("source.png", buffer, "image/png")})
    assert upload.status_code == 200
    return pid


def test_capabilities_and_source_analysis():
    caps = client.get("/capabilities")
    assert caps.status_code == 200
    assert caps.json()["cloud"]["dispatch_enabled"] is False
    pid = _project_with_image()
    analysis = client.post(f"/projects/{pid}/analyze")
    assert analysis.status_code == 200
    assert analysis.json()["source"]["width"] == 160


def test_tile_plan_and_identity_pipeline():
    pid = _project_with_image()
    plan = client.post(f"/projects/{pid}/tiles/plan", json={"tile_size": 128, "overlap": 16})
    assert plan.status_code == 200
    assert plan.json()["count"] > 1
    run = client.post(f"/projects/{pid}/restoration/run", json={"tile_size": 128, "overlap": 16})
    assert run.status_code == 200
    assert run.json()["quality_metrics"]["exact_pixel_identity"] is True


def test_unconfigured_ai_adapter_is_not_run():
    pid = _project_with_image()
    run = client.post(f"/projects/{pid}/restoration/run", json={"adapter": "Real-ESRGAN"})
    assert run.status_code == 409


def test_background_job_endpoint_and_resume_guard():
    pid = _project_with_image()
    queued = client.post(f"/projects/{pid}/restoration/jobs", json={"tile_size": 128, "overlap": 16})
    assert queued.status_code == 200
    job_id = queued.json()["job_id"]
    # TestClient runs background tasks before returning the response.
    state = client.get(f"/projects/{pid}/jobs/{job_id}")
    assert state.status_code == 200
    assert state.json()["status"] == "completed"
    resume = client.post(f"/projects/{pid}/restoration/jobs/{job_id}/resume")
    assert resume.status_code == 409


def test_original_upload_cannot_be_overwritten():
    pid = _project_with_image()
    buffer = BytesIO()
    Image.new("RGB", (20, 20), "black").save(buffer, format="PNG")
    buffer.seek(0)
    response = client.post(f"/projects/{pid}/source", files={"file": ("again.png", buffer, "image/png")})
    assert response.status_code == 409


def test_resource_endpoints_preserve_quality_and_budget_gate():
    plan = client.get("/resources/plan", params={"vram_gb": 4, "quality_profile": "museum_fidelity"})
    assert plan.status_code == 200
    assert plan.json()["tile_size"] == 512
    assert plan.json()["allow_quality_downgrade"] is False
    estimate = client.post("/resources/cost-estimate", json={"runtime_seconds": 3600, "hourly_rate": 2, "budget": 1})
    assert estimate.status_code == 200
    assert estimate.json()["allowed"] is False
