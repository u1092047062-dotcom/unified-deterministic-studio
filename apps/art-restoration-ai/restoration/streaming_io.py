"""Speicherschonendes Streaming-Kachel-I/O (pyvips-Backend mit Pillow-Fallback).

Ermoeglicht auf 8-GB-Workstations das kachelweise Lesen grosser Bilder,
ohne die gesamte unkomprimierte Pixelmatrix permanent im Arbeitsspeicher zu halten.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Dict, Tuple

from PIL import Image


def detect_streaming_backend() -> Dict[str, object]:
    has_pyvips = importlib.util.find_spec("pyvips") is not None
    return {
        "pyvips_available": has_pyvips,
        "active_backend": "pyvips-sequential" if has_pyvips else "pillow-bounded",
        "supports_gigapixel_streaming": has_pyvips,
    }


class StreamingTileReader:
    """Liest beliebige Bildkacheln deterministisch ueber pyvips oder Pillow."""

    def __init__(self, image_path: Path | str) -> None:
        self.image_path = Path(image_path)
        if not self.image_path.is_file():
            raise FileNotFoundError(f"Bilddatei nicht gefunden: {self.image_path}")
        self._info = detect_streaming_backend()

    @property
    def backend_name(self) -> str:
        return str(self._info["active_backend"])

    def dimensions(self) -> Tuple[int, int]:
        if self._info["pyvips_available"]:
            import pyvips

            vimg = pyvips.Image.new_from_file(str(self.image_path), access="sequential")
            return int(vimg.width), int(vimg.height)
        with Image.open(self.image_path) as im:
            return int(im.width), int(im.height)

    def read_tile(self, x: int, y: int, width: int, height: int) -> Image.Image:
        if width <= 0 or height <= 0 or x < 0 or y < 0:
            raise ValueError("Ungueltige Kachelkoordinaten")

        if self._info["pyvips_available"]:
            import pyvips

            vimg = pyvips.Image.new_from_file(str(self.image_path), access="random")
            region = vimg.crop(x, y, width, height)
            png_bytes = region.write_to_buffer(".png")
            from io import BytesIO

            with Image.open(BytesIO(png_bytes)) as im:
                return im.copy()

        with Image.open(self.image_path) as im:
            return im.crop((x, y, x + width, y + height)).copy()

    @staticmethod
    def estimate_tile_ram_mb(tile_size: int, channels: int = 3, bytes_per_channel: int = 4) -> float:
        """Schaetzt den Arbeitsspeicherbedarf einer einzelnen Float32-Kachel in MB."""
        return round((tile_size * tile_size * channels * bytes_per_channel) / (1024.0 * 1024.0), 3)
