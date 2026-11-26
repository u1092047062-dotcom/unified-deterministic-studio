from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app.main import app
from restoration.streaming_io import StreamingTileReader, detect_streaming_backend


def test_streaming_tile_reader_reads_exact_regions(tmp_path: Path):
    img_path = tmp_path / "large_test.png"
    im = Image.new("RGB", (300, 200), (10, 20, 30))
    im.putpixel((150, 100), (250, 10, 5))
    im.save(img_path)

    reader = StreamingTileReader(img_path)
    assert reader.dimensions() == (300, 200)
    tile = reader.read_tile(100, 50, 100, 100)
    assert tile.size == (100, 100)
    assert tile.getpixel((50, 50)) == (250, 10, 5)
    assert reader.estimate_tile_ram_mb(512) == 3.0

    with pytest.raises(ValueError):
        reader.read_tile(-1, 0, 10, 10)


def test_io_backend_endpoint():
    info = detect_streaming_backend()
    assert info["active_backend"] in {"pyvips-sequential", "pillow-bounded"}

    client = TestClient(app)
    resp = client.get("/io/backend")
    assert resp.status_code == 200
    assert resp.json()["active_backend"] == info["active_backend"]
