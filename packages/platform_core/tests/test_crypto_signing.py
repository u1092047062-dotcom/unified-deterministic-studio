"""Tests fuer RFC-8032 Ed25519, asymmetrisch signierte C2PA-PNGs & Merkle-Bundles (Phase 5)."""

from __future__ import annotations

from io import BytesIO
from PIL import Image

from platform_core.c2pa_credentials import build_c2pa_manifest
from platform_core.crypto_signing import (
    build_transparency_bundle,
    detect_hardware_trust_providers,
    ed25519_public_key,
    embed_signed_png_c2pa,
    generate_ed25519_keypair,
    sign_ed25519,
    sign_manifest_ed25519,
    verify_ed25519,
    verify_manifest_ed25519,
    verify_signed_png_c2pa,
    verify_transparency_bundle,
)


def test_rfc8032_official_ed25519_test_vector_1():
    """Beweist bit-exakte Konformitaet mit RFC 8032 Abschnitt 7.1 Testvektor 1."""
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    expected_pk = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    expected_sig = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )

    pk = ed25519_public_key(seed)
    assert pk == expected_pk

    sig = sign_ed25519(seed, b"")
    assert sig == expected_sig
    assert verify_ed25519(pk, b"", sig) is True
    assert verify_ed25519(pk, b"tampered", sig) is False


def test_signed_c2pa_png_and_merkle_transparency_bundle():
    kp = generate_ed25519_keypair()
    buf = BytesIO()
    Image.new("RGB", (48, 32), (18, 92, 140)).save(buf, format="PNG")
    raw_png = buf.getvalue()

    manifest = build_c2pa_manifest(
        "museum-ed25519.png",
        source_sha256="c" * 64,
        actions=[{"action": "c2pa.edited", "softwareAgent": "Unified Studio"}],
        ai_disclosed=False,
        simulated=False,
    )
    signed_png = embed_signed_png_c2pa(
        raw_png, manifest, kp["private_seed_hex"], signer_identity="archivist@museum.local"
    )

    report = verify_signed_png_c2pa(signed_png)
    assert report["valid"] is True
    assert report["pixel_integrity_ok"] is True
    assert report["asymmetric_trust"]["signature_present"] is True
    assert report["asymmetric_trust"]["signature_valid"] is True
    assert report["asymmetric_trust"]["public_key_hex"] == kp["public_key_hex"]

    # Manifest-Signatur & Sigstore-Merkle-Transparenz-Bundle pruefen
    signed_m = sign_manifest_ed25519({"artifact": "board.step", "drc_errors": 0}, kp["private_seed_hex"])
    assert verify_manifest_ed25519(signed_m)["signature_valid"] is True

    bundle = build_transparency_bundle("d" * 64, signed_m)
    assert verify_transparency_bundle(bundle)["valid"] is True

    # Manipulation am Manifest invalidiert Bundle sofort
    bundle["signed_manifest"]["drc_errors"] = 5
    assert verify_transparency_bundle(bundle)["valid"] is False
    assert detect_hardware_trust_providers()["ed25519_rfc8032_builtin"] is True
