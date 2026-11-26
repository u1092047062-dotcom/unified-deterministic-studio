from pathlib import Path
from PIL import Image
from app.topaz import create_topaz_handoff, import_topaz_result
from app.storage import sha256_file
import json

def test_topaz_manual_roundtrip(tmp_path):
    original = tmp_path / "originals" / "source.png"
    original.parent.mkdir()
    Image.new("RGB", (32, 24), "white").save(original)
    (tmp_path / "source_manifest.json").write_text(json.dumps({
        "file": "originals/source.png", "sha256": sha256_file(original)
    }))
    result = create_topaz_handoff(tmp_path, "Topaz Gigapixel")
    assert result["manifest"]["automation"] == "disabled_without_vendor_authorized_interface"
    imported = tmp_path / "topaz" / "imports" / "result.png"
    imported.parent.mkdir(parents=True)
    Image.new("RGB", (64, 48), "white").save(imported)
    record = import_topaz_result(tmp_path, imported, "Topaz Gigapixel")
    assert record["metadata"]["width"] == 64
    assert record["historical_authenticity_verified"] is False
