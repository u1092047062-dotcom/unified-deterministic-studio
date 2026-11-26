import hashlib
import json
from pathlib import Path

from PIL import Image
import pytest

from restoration.checkpoints import CheckpointSpec
from restoration.engine import RestorationEngine
from restoration.spandrel_adapter import (
    SpandrelModelAdapter,
    load_openmodeldb_manifest,
    spandrel_runtime_status,
)


class _DummySpandrelDescriptor:
    scale = 1
    architecture = "NAFNet-TestMock"

    def process_pil(self, img: Image.Image) -> Image.Image:
        return img.copy()


def test_spandrel_adapter_rejects_unverified_hash_and_runs_verified_checkpoint(tmp_path: Path):
    status = spandrel_runtime_status()
    assert "NAFNet" in status["supported_architectures"]

    ckpt = tmp_path / "nafnet_clean.safetensors"
    payload = b"verified-nafnet-weights-bytes"
    ckpt.write_bytes(payload)
    good_hash = hashlib.sha256(payload).hexdigest()

    # Manipulierter Hash muss fail-closed abgewiesen werden
    bad_spec = CheckpointSpec("NAFNet", ckpt.name, "0" * 64, "MIT", "v1")
    with pytest.raises(ValueError, match="SHA-256"):
        SpandrelModelAdapter(ckpt, bad_spec, base_dir=tmp_path, descriptor_loader=lambda _p: _DummySpandrelDescriptor())

    # Verifizierter Hash laeuft vollstaendig durch die echte RestorationEngine!
    good_spec = CheckpointSpec("NAFNet", ckpt.name, good_hash, "MIT", "v1")
    adapter = SpandrelModelAdapter(
        ckpt,
        good_spec,
        base_dir=tmp_path,
        descriptor_loader=lambda _p: _DummySpandrelDescriptor(),
    )
    assert adapter.enabled is True
    assert adapter.verified_sha256 == good_hash

    src = tmp_path / "source.png"
    out = tmp_path / "restored.png"
    Image.new("RGB", (160, 120), (40, 80, 120)).save(src)
    res = RestorationEngine(tmp_path / "work").run(src, out, tile_size=128, overlap=16, adapter=adapter, job_id="spandrel-1")
    assert res["status"] == "completed"
    assert res["adapter"] == "spandrel:NAFNet"
    assert res["quality_metrics"]["exact_pixel_identity"] is True


def test_load_openmodeldb_manifest(tmp_path: Path):
    mpath = tmp_path / "openmodeldb.json"
    mpath.write_text(
        json.dumps(
            {
                "models": [
                    {
                        "model_name": "4x-UltraSharp",
                        "filename": "4x-UltraSharp.safetensors",
                        "expected_sha256": "a" * 64,
                        "license": "CC-BY-4.0",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    specs = load_openmodeldb_manifest(mpath)
    assert len(specs) == 1
    assert specs[0].model_name == "4x-UltraSharp"
