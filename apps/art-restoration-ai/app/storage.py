from __future__ import annotations

import hashlib
from pathlib import Path
from PIL import Image, ImageStat
from app.config import MAX_IMAGE_PIXELS


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def inspect_image(path: Path) -> dict:
    path = Path(path)
    with Image.open(path) as im:
        if im.width <= 0 or im.height <= 0 or im.width * im.height > MAX_IMAGE_PIXELS:
            raise ValueError(f"Bild überschreitet Pixel-Limit ({MAX_IMAGE_PIXELS})")
        im.verify()
    with Image.open(path) as im:
        width, height, mode, image_format = im.width, im.height, im.mode, im.format
        bits = None
        try:
            raw_bits = im.tag_v2.get(258) if hasattr(im, "tag_v2") else im.info.get("bitdepth")
            if isinstance(raw_bits, (tuple, list)):
                bits = max(int(v) for v in raw_bits)
            elif raw_bits is not None:
                bits = int(raw_bits)
        except Exception:
            bits = None
        sample = im
        sample.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        if sample.mode in {"I;16", "I", "F"}:
            mean_values = [round(float(ImageStat.Stat(sample).mean[0]), 3)]
        else:
            stat = ImageStat.Stat(sample.convert("RGB"))
            mean_values = [round(v, 3) for v in stat.mean[:3]]
        return {
            "format": image_format, "width": width, "height": height,
            "mode": mode, "bands": list(im.getbands()), "bits_per_sample": bits,
            "megapixels": round((width * height) / 1_000_000, 3),
            "sample_mean": mean_values, "statistics_sample": "thumbnail_max_1024",
            "icc_profile_present": bool(im.info.get("icc_profile")),
            "file_bytes": path.stat().st_size,
        }
