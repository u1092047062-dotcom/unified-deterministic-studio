from io import BytesIO
from pathlib import Path
import hashlib

from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from restoration.checkpoints import CheckpointSpec, inspect_checkpoints_dir, verify_checkpoint_file


def test_checkpoint_verifier_fail_closed_and_sha256_match(tmp_path: Path):
    weights = tmp_path / "model_v1.safetensors"
    payload = b"deterministic-checkpoint-bytes"
    weights.write_bytes(payload)
    good_hash = hashlib.sha256(payload).hexdigest()

    ok = verify_checkpoint_file(weights, good_hash, base_dir=tmp_path)
    assert ok["verified"] is True
    assert ok["actual_sha256"] == good_hash

    bad = verify_checkpoint_file(weights, "0" * 64, base_dir=tmp_path)
    assert bad["verified"] is False

    traversal = verify_checkpoint_file(tmp_path / ".." / "escape.pth", good_hash, base_dir=tmp_path)
    assert traversal["verified"] is False

    report = inspect_checkpoints_dir(
        tmp_path,
        specs=[
            CheckpointSpec("TestModel", "model_v1.safetensors", good_hash, "MIT", "v1.0"),
            CheckpointSpec("MissingModel", "missing.pth", good_hash, "BSD-3", "v1.0"),
        ],
    )
    assert report["verified_count"] == 1
    assert report["all_verified"] is False


def test_checkpoints_and_project_provenance_endpoints():
    client = TestClient(app)

    cp = client.get("/checkpoints/status")
    assert cp.status_code == 200
    assert cp.json()["verified_count"] == 0

    project = client.post("/projects", params={"name": "Provenance Test"})
    pid = project.json()["project_id"]

    buf = BytesIO()
    Image.new("RGB", (64, 64), "teal").save(buf, format="PNG")
    buf.seek(0)
    assert client.post(f"/projects/{pid}/source", files={"file": ("src.png", buf, "image/png")}).status_code == 200
    assert client.post(f"/projects/{pid}/restoration/run", json={"tile_size": 128, "overlap": 16}).status_code == 200

    prov = client.get(f"/projects/{pid}/provenance")
    assert prov.status_code == 200
    body = prov.json()
    assert body["domain"] == "art-restoration-ai"
    assert "source_manifest.json" in body["artifacts"]
    assert body["metadata"]["job_count"] >= 1
    assert len(body["manifest_sha256"]) == 64
