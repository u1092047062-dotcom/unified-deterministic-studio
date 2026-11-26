from __future__ import annotations

from pathlib import Path
from typing import Iterable
import numpy as np
from PIL import Image

from restoration.tiling import Tile


def _axis_weight(length: int, before: int, after: int, dtype=np.float32) -> np.ndarray:
    w = np.ones(length, dtype=dtype)
    if before > 0:
        n = min(before, length)
        ramp = np.linspace(1.0 / (n + 1), 1.0, n, dtype=dtype)
        w[:n] = np.minimum(w[:n], ramp)
    if after > 0:
        n = min(after, length)
        ramp = np.linspace(1.0, 1.0 / (n + 1), n, dtype=dtype)
        w[-n:] = np.minimum(w[-n:], ramp)
    return w


def feather_weights(tile: Tile) -> np.ndarray:
    wx = _axis_weight(tile.width, tile.overlap_left, tile.overlap_right)
    wy = _axis_weight(tile.height, tile.overlap_top, tile.overlap_bottom)
    return wy[:, None] * wx[None, :]


def stitch_tiles(
    image_size: tuple[int, int],
    tiles: Iterable[Tile],
    processed_tiles: dict[int, Image.Image],
    output_path: Path,
    *,
    scale: int = 1,
    output_mode: str = "RGB",
    save_kwargs: dict | None = None,
) -> dict:
    """Feather-blend same-scale or integer-upscaled tiles into an output image."""
    width, height = image_size
    if scale < 1:
        raise ValueError("scale muss mindestens 1 sein")
    channels = 4 if output_mode == "RGBA" else (1 if output_mode in {"L", "I;16"} else 3)
    shape = (height * scale, width * scale, channels) if channels > 1 else (height * scale, width * scale)
    accum = np.zeros(shape, dtype=np.float64)
    weights = np.zeros((height * scale, width * scale), dtype=np.float64)

    for tile in tiles:
        if tile.index not in processed_tiles:
            raise ValueError(f"Kachel {tile.index} fehlt")
        arr = np.asarray(processed_tiles[tile.index].convert(output_mode), dtype=np.float64)
        expected = (tile.height * scale, tile.width * scale)
        if arr.shape[:2] != expected:
            raise ValueError(f"Kachel {tile.index}: Ausgabegröße {arr.shape[:2]} statt {expected}")
        base = feather_weights(tile)
        weight = np.repeat(np.repeat(base, scale, axis=0), scale, axis=1)
        x0, y0 = tile.x * scale, tile.y * scale
        x1, y1 = x0 + tile.width * scale, y0 + tile.height * scale
        if channels > 1:
            accum[y0:y1, x0:x1, :] += arr * weight[:, :, None]
        else:
            accum[y0:y1, x0:x1] += arr * weight
        weights[y0:y1, x0:x1] += weight

    if np.any(weights == 0):
        raise RuntimeError("Stitching erzeugte nicht abgedeckte Pixel")
    if channels > 1:
        result = accum / weights[:, :, None]
    else:
        result = accum / weights
    result = np.clip(np.rint(result), 0, 65535 if output_mode == "I;16" else 255)
    if output_mode == "I;16":
        result = result.astype(np.uint16)
    else:
        result = result.astype(np.uint8)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(result).save(output_path, **(save_kwargs or {}))
    return {"output": str(output_path), "width": width * scale, "height": height * scale, "scale": scale}
