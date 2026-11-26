"""Tests fuer C2PA Content Credentials & manipulationssichere PNG-Einbettung."""

from __future__ import annotations

from io import BytesIO
from PIL import Image

from platform_core.c2pa_credentials import (
    build_c2pa_manifest,
    detect_c2pa_backend,
    embed_png_content_credentials,
    read_png_content_credentials,
    verify_png_content_credentials,
)


def _sample_png(color: tuple[int, int, int] = (40, 120, 200)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, format="PNG")
    return buf.getvalue()


def test_c2pa_png_embed_read_and_verify_roundtrip():
    backend = detect_c2pa_backend()
    assert backend["lossless_png_embedding"] is True

    raw_png = _sample_png()
    manifest = build_c2pa_manifest(
        "museum-restoration-test.png",
        source_sha256="a" * 64,
        actions=[{"action": "c2pa.edited", "softwareAgent": "ART Restoration AI"}],
        ai_disclosed=True,
        simulated=False,
        extra_metadata={"quality_profile": "museum_fidelity"},
    )
    signed_png = embed_png_content_credentials(raw_png, manifest)

    # Muss fuer Standard-Bildbetrachter (Pillow) 100% lesbar & pixel-identisch bleiben
    with Image.open(BytesIO(signed_png)) as im:
        assert im.size == (64, 48)
        assert im.getpixel((10, 10)) == (40, 120, 200)

    extracted = read_png_content_credentials(signed_png)
    assert extracted is not None
    assert extracted["title"] == "museum-restoration-test.png"
    assert extracted["ai_disclosed"] is True

    report = verify_png_content_credentials(signed_png)
    assert report["valid"] is True
    assert report["claim_integrity_ok"] is True
    assert report["pixel_integrity_ok"] is True
    assert report["reason"] == "verified"


def test_c2pa_detects_missing_manifest_and_pixel_tampering():
    plain_png = _sample_png()
    missing = verify_png_content_credentials(plain_png)
    assert missing["valid"] is False
    assert missing["reason"] == "no_c2pa_manifest_found"

    manifest = build_c2pa_manifest(
        "tamper-check.png",
        source_sha256="b" * 64,
        actions=[{"action": "digital.simulation"}],
        ai_disclosed=True,
        simulated=True,
    )
    signed_png = embed_png_content_credentials(plain_png, manifest)

    # Pixel manipulieren, aber den alten eingebetteten Manifest-Hash (via erneutem Schreiben ohne Re-Sign) pruefen:
    # Wir erstellen ein geaendertes Bild und uebertragen den alten iTXt-Chunk direkt vor IEND
    tampered_img = _sample_png(color=(41, 120, 200))
    idx_itxt = signed_png.find(b"iTXt")
    chunk_start = idx_itxt - 4
    iend_start = signed_png.rfind(b"IEND") - 4
    c2pa_raw_chunk = signed_png[chunk_start:iend_start]

    tampered_iend = tampered_img.rfind(b"IEND") - 4
    forged_png = tampered_img[:tampered_iend] + c2pa_raw_chunk + tampered_img[tampered_iend:]

    tampered_report = verify_png_content_credentials(forged_png)
    assert tampered_report["valid"] is False
    assert tampered_report["claim_integrity_ok"] is True
    assert tampered_report["pixel_integrity_ok"] is False
    assert tampered_report["reason"] == "pixel_tampering_detected"
