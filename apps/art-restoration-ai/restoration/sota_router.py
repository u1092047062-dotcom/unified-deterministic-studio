"""Zweistufiger SOTA-Modell-Router (Zero-Hallucination NAFNet/HAT vs. SeedVR2/SUPIR) (Phase 6).

Trennt strikt zwischen:
- Stufe A (Forensik / Museum / Zero-Hallucination): NAFNet + HAT (Hybrid Attention Transformer)
- Stufe B (High-Speed Video / Schwere Schaeden, nur bei explizitem Opt-In):
  SeedVR2 (One-Step Diffusion Transformer, FP8/NVFP4 + BlockSwap) & SUPIR.
"""

from __future__ import annotations

from typing import Any, Dict

ZERO_HALLUCINATION_PROFILES = {"museum_fidelity", "forensic_strict", "archival", "deterministic_strict"}


def select_sota_restoration_plan(
    *,
    quality_profile: str = "museum_fidelity",
    media_type: str = "photo",
    vram_gb: float = 24.0,
    damage_level: str = "moderate",
    allow_generative: bool = False,
) -> Dict[str, Any]:
    """Waehlt deterministisch Architektur, Quantisierung, Farbraum & Hallucination-Gate-Schwellen."""
    profile = quality_profile.strip().lower()
    mtype = media_type.strip().lower()
    dmg = damage_level.strip().lower()

    # Fail-Closed: Museums-/Forensik-Profile erlauben niemals ungepruefte generative Halluzination
    if profile in ZERO_HALLUCINATION_PROFILES or not allow_generative:
        model = "HAT-L (Hybrid Attention Transformer)" if dmg in {"moderate", "severe"} else "NAFNet-SIDD-width64"
        return {
            "tier": "tier_a_zero_hallucination",
            "primary_model": model,
            "secondary_denoise_model": "NAFNet-SIDD-width64",
            "generative_allowed": False,
            "inference_steps": 1,
            "precision": "fp32_linear_acescg" if vram_gb >= 12.0 else "fp16_tiled_acescg",
            "working_color_space": "acescg_ap1_float32",
            "hallucination_gate": {"enabled": True, "min_ssim": 0.92, "max_edge_drift": 0.15},
            "policy_reason": "Quality-First Zero-Hallucination erzwungen (keine generativen Erfindungen).",
        }

    # Stufe B1: Video oder konservatives Detail-Upscaling -> SeedVR2 One-Step DiT
    if mtype == "video" or dmg != "severe":
        use_7b = vram_gb >= 24.0
        quant = "bf16" if vram_gb >= 40.0 else ("fp8_blockswap" if vram_gb >= 16.0 else "nvfp4_blockswap")
        return {
            "tier": "tier_b_onestep_dit",
            "primary_model": "SeedVR2-7B (Apache-2.0 One-Step DiT)" if use_7b else "SeedVR2-3B (Apache-2.0 One-Step DiT)",
            "fallback_model": "HAT-L (Zero-Hallucination Fallback)",
            "generative_allowed": True,
            "inference_steps": 1,
            "precision": quant,
            "working_color_space": "acescg_ap1_float32",
            "hallucination_gate": {"enabled": True, "min_ssim": 0.85, "max_edge_drift": 0.25},
            "policy_reason": "One-Step DiT (SeedVR2) fuer zeitlich konsistentes Video/Detail-Upscaling mit Pflicht-Hallucination-Gate.",
        }

    # Stufe B2: Extrem zerstoerte Altfotos -> SUPIR + Pflicht-Hallucination-Gate
    return {
        "tier": "tier_b_semantic_recovery",
        "primary_model": "SUPIR-v0Q (Semantic Latent Diffusion)" if vram_gb >= 16.0 else "SeedVR2-3B (VRAM-guarded)",
        "fallback_model": "NAFNet-SIDD-width64",
        "generative_allowed": True,
        "inference_steps": 25 if vram_gb >= 16.0 else 1,
        "precision": "fp16_tiled" if vram_gb >= 16.0 else "nvfp4_blockswap",
        "working_color_space": "acescg_ap1_float32",
        "hallucination_gate": {"enabled": True, "min_ssim": 0.80, "max_edge_drift": 0.30},
        "policy_reason": "Semantische Rekonstruktion schwerer Schaeden mit automatischem Fallback bei Kanten-Drift.",
    }
