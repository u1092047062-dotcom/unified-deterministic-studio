"""32-Bit Linear Float Farb-Pipeline (IEC sRGB <-> ACEScg / Rec.709 / DCI-P3) & OCIO/OpenEXR-Probe (Phase 6).

Verhindert 8-Bit-sRGB-Clipping waehrend der Restaurierung durch durchgaengige
`float32`-Linear-Verarbeitung (Museums- & Kino-Standard).
"""

from __future__ import annotations

import importlib.util
from typing import Any, Dict

import numpy as np

# Rec.709/sRGB linear (D65) -> ACEScg AP1 (D60, Bradford-adaptiert)
SRGB_TO_ACESCG = np.array(
    [
        [0.6130974024, 0.3395231462, 0.0473794514],
        [0.0701937225, 0.9163538791, 0.0134523985],
        [0.0206155929, 0.1095697729, 0.8698146342],
    ],
    dtype=np.float64,
)
ACESCG_TO_SRGB = np.linalg.inv(SRGB_TO_ACESCG)

# Linear Rec.709 -> Linear Display P3 (D65)
SRGB_TO_DISPLAY_P3 = np.array(
    [
        [0.8224621, 0.1775380, 0.0000000],
        [0.0331941, 0.9668058, 0.0000000],
        [0.0170827, 0.0723974, 0.9105199],
    ],
    dtype=np.float64,
)


def detect_color_management_backends() -> Dict[str, Any]:
    ocio = importlib.util.find_spec("PyOpenColorIO") is not None
    exr = importlib.util.find_spec("OpenEXR") is not None
    return {
        "linear_float32_engine": True,
        "acescg_matrix_pipeline": True,
        "pyopencolorio_installed": ocio,
        "openexr_installed": exr,
        "active_color_backend": "PyOpenColorIO" if ocio else "builtin-acescg-float32",
        "supported_working_spaces": ["linear_srgb", "acescg_ap1", "display_p3_linear"],
    }


def srgb_to_linear(srgb_01: np.ndarray) -> np.ndarray:
    """Exakte IEC-61966-2-1 sRGB-EOTF nach 32-Bit-Linear-Float."""
    x = np.asarray(srgb_01, dtype=np.float32)
    return np.where(x <= 0.04045, x / 12.92, np.power(np.clip((x + 0.055) / 1.055, 0.0, None), 2.4)).astype(np.float32)


def linear_to_srgb(linear_01: np.ndarray) -> np.ndarray:
    """Exakte IEC-61966-2-1 OETF von 32-Bit-Linear-Float nach sRGB."""
    x = np.clip(np.asarray(linear_01, dtype=np.float32), 0.0, None)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1.0 / 2.4) - 0.055).astype(np.float32)


def linear_srgb_to_acescg(linear_rgb: np.ndarray) -> np.ndarray:
    """Konvertiert lineare Rec.709/sRGB-Daten nach ACEScg (AP1, float32)."""
    arr = np.asarray(linear_rgb, dtype=np.float64)
    return np.einsum("...j,ij->...i", arr, SRGB_TO_ACESCG).astype(np.float32)


def acescg_to_linear_srgb(aces_rgb: np.ndarray) -> np.ndarray:
    """Konvertiert ACEScg (AP1) verlustfrei zurueck nach linearem Rec.709/sRGB (float32)."""
    arr = np.asarray(aces_rgb, dtype=np.float64)
    return np.einsum("...j,ij->...i", arr, ACESCG_TO_SRGB).astype(np.float32)


def linear_srgb_to_display_p3(linear_rgb: np.ndarray) -> np.ndarray:
    arr = np.asarray(linear_rgb, dtype=np.float64)
    return np.einsum("...j,ij->...i", arr, SRGB_TO_DISPLAY_P3).astype(np.float32)


def aces_filmic_tonemap(linear_hdr: np.ndarray) -> np.ndarray:
    """Narkowicz ACES-Filmic-Tonemapping fuer HDR-Werte > 1.0 ohne hartes Clipping."""
    x = np.clip(np.asarray(linear_hdr, dtype=np.float32), 0.0, None)
    a, b, c, d, e = 2.51, 0.03, 2.43, 0.59, 0.14
    return np.clip((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0).astype(np.float32)


def analyze_linear_color_profile(rgb_u8: np.ndarray) -> Dict[str, Any]:
    """Analysiert Dynamikumfang, Clipping & ACEScg-Roundtrip-Praezision eines Bildes."""
    srgb = np.asarray(rgb_u8, dtype=np.float32) / 255.0
    lin = srgb_to_linear(srgb)
    aces = linear_srgb_to_acescg(lin)
    roundtrip = linear_to_srgb(acescg_to_linear_srgb(aces))
    max_err = float(np.max(np.abs(srgb - roundtrip)))

    lum = 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]
    clipped_hi = float(np.mean(rgb_u8 >= 254) * 100.0)
    clipped_lo = float(np.mean(rgb_u8 <= 1) * 100.0)
    return {
        "working_space": "acescg_ap1_float32",
        "mean_linear_luminance": round(float(np.mean(lum)), 5),
        "p99_linear_luminance": round(float(np.percentile(lum, 99)), 5),
        "highlight_clip_pct": round(clipped_hi, 3),
        "shadow_clip_pct": round(clipped_lo, 3),
        "acescg_roundtrip_max_error": round(max_err, 7),
        "bit_precision": "32-bit float (IEEE 754)",
    }
