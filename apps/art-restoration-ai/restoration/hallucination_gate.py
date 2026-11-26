"""Forensisches Hallucination-Delta-Gate mit SSIM, Sobel-Kanten-Drift & Heatmap (Phase 6).

Vermisst jede restaurierte Kachel deterministisch gegen das unveraenderte Original.
Ueberschreitet ein generatives Modell den zulaessigen Struktur- oder Kanten-Drift,
verweigert das Gate die Freigabe und erzwingt einen automatischen Fallback auf
Stufe A (Zero-Hallucination: NAFNet / HAT).
"""

from __future__ import annotations

from io import BytesIO
from typing import Any, Dict, List

import numpy as np
from PIL import Image


def _to_luma(rgb: np.ndarray) -> np.ndarray:
    arr = np.asarray(rgb, dtype=np.float32)
    return 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]


def _sobel_magnitude(luma: np.ndarray) -> np.ndarray:
    """Deterministischer 3x3-Sobel-Kantenbetrag in reinem NumPy."""
    if luma.shape[0] < 3 or luma.shape[1] < 3:
        return np.zeros_like(luma, dtype=np.float32)
    p = np.pad(luma, ((1, 1), (1, 1)), mode="edge")
    gx = (
        -p[:-2, :-2] + p[:-2, 2:]
        - 2.0 * p[1:-1, :-2] + 2.0 * p[1:-1, 2:]
        - p[2:, :-2] + p[2:, 2:]
    )
    gy = (
        -p[:-2, :-2] - 2.0 * p[:-2, 1:-1] - p[:-2, 2:]
        + p[2:, :-2] + 2.0 * p[2:, 1:-1] + p[2:, 2:]
    )
    return np.hypot(gx, gy).astype(np.float32)


def _compute_ssim(luma_a: np.ndarray, luma_b: np.ndarray) -> float:
    """Berechnet einen stabilen blockbasierten SSIM-Wert im Bereich [-1, 1]."""
    c1 = (0.01 * 255.0) ** 2
    c2 = (0.03 * 255.0) ** 2
    h, w = luma_a.shape
    bh = max(4, min(16, h))
    bw = max(4, min(16, w))
    scores: List[float] = []
    for y in range(0, h, bh):
        for x in range(0, w, bw):
            pa = luma_a[y : y + bh, x : x + bw]
            pb = luma_b[y : y + bh, x : x + bw]
            mu_a = float(np.mean(pa))
            mu_b = float(np.mean(pb))
            var_a = float(np.var(pa))
            var_b = float(np.var(pb))
            cov = float(np.mean((pa - mu_a) * (pb - mu_b)))
            num = (2.0 * mu_a * mu_b + c1) * (2.0 * cov + c2)
            den = (mu_a * mu_a + mu_b * mu_b + c1) * (var_a + var_b + c2)
            scores.append(num / den if den > 0 else 1.0)
    return float(np.mean(scores)) if scores else 1.0


def evaluate_tile_fidelity(
    original_rgb: np.ndarray,
    restored_rgb: np.ndarray,
    *,
    min_ssim: float = 0.85,
    max_edge_drift: float = 0.25,
    max_color_shift: float = 18.0,
) -> Dict[str, Any]:
    """Prueft eine Kachel auf strukturelle Treue und erkennt generative Halluzinationen."""
    orig = np.asarray(original_rgb, dtype=np.float32)
    rest = np.asarray(restored_rgb, dtype=np.float32)
    if orig.shape != rest.shape:
        # Auf Originalgroesse zurueckprojizieren fuer exakten Strukturvergleich
        pil_rest = Image.fromarray(np.clip(rest, 0, 255).astype(np.uint8), mode="RGB").resize(
            (orig.shape[1], orig.shape[0]), Image.Resampling.LANCZOS
        )
        rest = np.asarray(pil_rest, dtype=np.float32)

    diff = np.abs(orig - rest)
    mse = float(np.mean(diff ** 2))
    psnr = float("inf") if mse == 0.0 else round(float(10.0 * np.log10((255.0 ** 2) / mse)), 3)

    luma_o = _to_luma(orig)
    luma_r = _to_luma(rest)
    ssim = round(_compute_ssim(luma_o, luma_r), 5)

    edge_o = _sobel_magnitude(luma_o)
    edge_r = _sobel_magnitude(luma_r)
    edge_norm = float(np.mean(edge_o) + np.mean(edge_r) + 1.0)
    edge_drift = round(float(np.mean(np.abs(edge_o - edge_r)) / edge_norm), 5)

    color_shift = round(float(np.mean(diff)), 4)
    p99_pixel_delta = round(float(np.percentile(diff, 99)), 3)

    violations: List[str] = []
    if ssim < min_ssim:
        violations.append(f"ssim={ssim:.4f} < min_ssim={min_ssim:.4f}")
    if edge_drift > max_edge_drift:
        violations.append(f"edge_drift={edge_drift:.4f} > max_edge_drift={max_edge_drift:.4f}")
    if color_shift > max_color_shift:
        violations.append(f"color_shift={color_shift:.2f} > max_color_shift={max_color_shift:.2f}")

    passed = len(violations) == 0
    return {
        "passed": passed,
        "verdict": "PASS_FIDELITY" if passed else "FALLBACK_TO_ZERO_HALLUCINATION",
        "recommended_fallback_tier": None if passed else "tier_a_zero_hallucination (NAFNet/HAT)",
        "metrics": {
            "psnr_db": psnr if np.isfinite(psnr) else "infinity",
            "ssim": ssim,
            "edge_drift": edge_drift,
            "mean_color_shift": color_shift,
            "p99_pixel_delta": p99_pixel_delta,
        },
        "thresholds": {
            "min_ssim": min_ssim,
            "max_edge_drift": max_edge_drift,
            "max_color_shift": max_color_shift,
        },
        "violations": violations,
    }


def render_delta_heatmap_png(original_rgb: np.ndarray, restored_rgb: np.ndarray) -> bytes:
    """Rendert eine False-Color-Differenz-Heatmap (Blau=identisch, Gelb=Detail, Rot=starker Drift)."""
    orig = np.asarray(original_rgb, dtype=np.float32)
    rest = np.asarray(restored_rgb, dtype=np.float32)
    if orig.shape != rest.shape:
        pil_rest = Image.fromarray(np.clip(rest, 0, 255).astype(np.uint8), mode="RGB").resize(
            (orig.shape[1], orig.shape[0]), Image.Resampling.LANCZOS
        )
        rest = np.asarray(pil_rest, dtype=np.float32)

    # Normalisierte Pixel-Abweichung [0..1] (ab Delta 64/255 volle Rot-Saettigung)
    mag = np.clip(np.mean(np.abs(orig - rest), axis=-1) / 64.0, 0.0, 1.0)
    r = np.clip(2.0 * mag - 0.5, 0.0, 1.0) * 255.0
    g = np.clip(1.0 - np.abs(2.0 * mag - 1.0), 0.0, 1.0) * 255.0
    b = np.clip(1.0 - 2.0 * mag, 0.0, 1.0) * 255.0
    heat = np.stack([r, g, b], axis=-1).astype(np.uint8)

    buf = BytesIO()
    Image.fromarray(heat, mode="RGB").save(buf, format="PNG")
    return buf.getvalue()
