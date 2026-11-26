from pathlib import Path
from PIL import Image
import numpy as np
import pytest

from restoration.engine import RestorationEngine
from restoration.adapters import IdentityValidationAdapter


def test_identity_engine_roundtrip_is_pixel_exact(tmp_path: Path):
    source = tmp_path / "source.png"
    output = tmp_path / "out.png"
    arr = np.zeros((150, 190, 3), dtype=np.uint8)
    arr[:, :95] = (220, 30, 10)
    arr[:, 95:] = (10, 40, 220)
    Image.fromarray(arr).save(source)
    result = RestorationEngine(tmp_path / "work").run(
        source, output, tile_size=96, overlap=16,
        adapter=IdentityValidationAdapter(), job_id="test-job"
    )
    assert result["status"] == "completed"
    assert result["quality_metrics"]["exact_pixel_identity"] is True
    with Image.open(output) as actual:
        assert np.array_equal(np.asarray(actual), arr)


def test_engine_resumes_completed_tiles(tmp_path: Path):
    source = tmp_path / "source.png"
    Image.new("RGB", (180, 120), "navy").save(source)
    engine = RestorationEngine(tmp_path / "work")
    first = engine.run(source, tmp_path / "out1.png", tile_size=96, overlap=16, job_id="resume")
    second = engine.run(source, tmp_path / "out2.png", tile_size=96, overlap=16, job_id="resume")
    assert first["tiles_total"] == second["tiles_completed"]
    assert second["quality_metrics"]["exact_pixel_identity"] is True


def test_engine_rejects_changed_job_configuration(tmp_path: Path):
    source = tmp_path / "source.png"
    Image.new("RGB", (180, 120), "navy").save(source)
    engine = RestorationEngine(tmp_path / "work")
    engine.run(source, tmp_path / "out1.png", tile_size=96, overlap=16, job_id="fixed")
    with pytest.raises(ValueError):
        engine.run(source, tmp_path / "out2.png", tile_size=96, overlap=8, job_id="fixed")


def test_16bit_grayscale_is_not_silently_converted(tmp_path: Path):
    source = tmp_path / "source16.png"
    output = tmp_path / "out16.png"
    arr = np.linspace(0, 65535, 120 * 140, dtype=np.uint16).reshape(120, 140)
    Image.fromarray(arr).save(source)
    result = RestorationEngine(tmp_path / "work16").run(
        source, output, tile_size=128, overlap=16,
        adapter=IdentityValidationAdapter(), job_id="test-16bit"
    )
    assert result["quality_metrics"]["exact_pixel_identity"] is True
    with Image.open(output) as actual:
        assert actual.mode == "I;16"
        assert np.array_equal(np.asarray(actual), arr)
