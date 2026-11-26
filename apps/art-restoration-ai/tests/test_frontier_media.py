"""Tests fuer 32-Bit Linear ACEScg, SOTA-Router & Hallucination-Delta-Gate (Phase 6)."""

from __future__ import annotations

from io import BytesIO
import numpy as np
from PIL import Image

from restoration.color_pipeline import (
    aces_filmic_tonemap,
    acescg_to_linear_srgb,
    analyze_linear_color_profile,
    detect_color_management_backends,
    linear_srgb_to_acescg,
    linear_to_srgb,
    srgb_to_linear,
)
from restoration.hallucination_gate import evaluate_tile_fidelity, render_delta_heatmap_png
from restoration.sota_router import select_sota_restoration_plan


def test_acescg_32bit_linear_roundtrip_precision_and_tonemap():
    assert detect_color_management_backends()["linear_float32_engine"] is True
    rng = np.random.default_rng(42)
    img_u8 = rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8)
    srgb = img_u8.astype(np.float32) / 255.0

    lin = srgb_to_linear(srgb)
    aces = linear_srgb_to_acescg(lin)
    back = linear_to_srgb(acescg_to_linear_srgb(aces))
    assert float(np.max(np.abs(srgb - back))) < 1e-5

    hdr = np.array([[[0.0, 1.0, 2.0, 8.0]]], dtype=np.float32)
    tm = aces_filmic_tonemap(hdr)
    assert 0.7 < float(tm[0, 0, 1]) < float(tm[0, 0, 2]) < 1.0
    assert float(tm[0, 0, 3]) <= 1.0

    profile = analyze_linear_color_profile(img_u8)
    assert profile["acescg_roundtrip_max_error"] < 1e-5


def test_hallucination_delta_gate_passes_clean_denoise_and_blocks_hallucination():
    # Strukturierte Referenzkachel (Schachbrett + Verlauf)
    y, x = np.mgrid[0:64, 0:64]
    base = np.stack([(x * 3) % 255, (y * 3) % 255, ((x + y) * 2) % 255], axis=-1).astype(np.uint8)
    clean_mild = np.clip(base.astype(np.int16) + 2, 0, 255).astype(np.uint8)

    ok_report = evaluate_tile_fidelity(base, clean_mild)
    assert ok_report["passed"] is True
    assert ok_report["verdict"] == "PASS_FIDELITY"

    # Starke generative Halluzination (invertierter/fremder Bildinhalt)
    hallucinated = 255 - base
    fail_report = evaluate_tile_fidelity(base, hallucinated)
    assert fail_report["passed"] is False
    assert fail_report["verdict"] == "FALLBACK_TO_ZERO_HALLUCINATION"
    assert len(fail_report["violations"]) >= 1

    heatmap_png = render_delta_heatmap_png(base, hallucinated)
    with Image.open(BytesIO(heatmap_png)) as hm:
        assert hm.size == (64, 64)


def test_two_tier_sota_router_enforces_zero_hallucination_on_museum_profile():
    museum = select_sota_restoration_plan(
        quality_profile="museum_fidelity", media_type="photo", vram_gb=24.0, allow_generative=True
    )
    assert museum["tier"] == "tier_a_zero_hallucination"
    assert museum["generative_allowed"] is False

    video = select_sota_restoration_plan(
        quality_profile="cinema_upscale", media_type="video", vram_gb=24.0, allow_generative=True
    )
    assert video["tier"] == "tier_b_onestep_dit"
    assert "SeedVR2" in video["primary_model"]
    assert video["inference_steps"] == 1

    severe = select_sota_restoration_plan(
        quality_profile="recovery", media_type="photo", vram_gb=24.0, damage_level="severe", allow_generative=True
    )
    assert "SUPIR" in severe["primary_model"]
