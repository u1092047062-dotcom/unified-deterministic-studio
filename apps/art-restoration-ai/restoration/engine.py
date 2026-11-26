from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import numpy as np
from PIL import Image

from restoration.adapters import IdentityValidationAdapter, RestorationAdapter
from restoration.tiling import plan_tiles
from restoration.stitching import stitch_tiles
from app.storage import sha256_file, inspect_image
from app.config import MAX_LOCAL_ENGINE_PIXELS


class QualityGateError(RuntimeError):
    pass


def _atomic_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _metrics(source: Path, output: Path) -> dict:
    with Image.open(source) as a, Image.open(output) as b:
        if a.mode == "I;16" and b.mode == "I;16":
            src = np.asarray(a, dtype=np.float64)
            dst = np.asarray(b, dtype=np.float64)
            peak = 65535.0
        else:
            src = np.asarray(a.convert("RGB"), dtype=np.float32)
            dst = np.asarray(b.convert("RGB"), dtype=np.float32)
            peak = 255.0
        if dst.shape[:2] != src.shape[:2]:
            b_resized = b.resize((a.width, a.height), Image.Resampling.LANCZOS)
            if a.mode == "I;16" and b_resized.mode == "I;16":
                dst = np.asarray(b_resized, dtype=np.float64)
            else:
                dst = np.asarray(b_resized.convert("RGB"), dtype=np.float32)
        mse = float(np.mean((src - dst) ** 2))
        psnr = float("inf") if mse == 0 else float(10 * np.log10((peak ** 2) / mse))
        return {
            "source_sha256": sha256_file(source),
            "output_sha256": sha256_file(output),
            "source_dimensions": [a.width, a.height],
            "output_dimensions": [b.width, b.height],
            "mse_after_downsample": mse,
            "psnr_db_after_downsample": psnr if np.isfinite(psnr) else "infinity",
            "exact_pixel_identity": bool(mse == 0 and b.size == a.size),
            "warning": "Metriken messen numerische Ähnlichkeit, nicht historische Authentizität oder wahrgenommene Restaurierungsqualität."
        }


class RestorationEngine:
    def __init__(self, work_root: Path):
        self.work_root = work_root

    def run(
        self,
        source_path: Path,
        output_path: Path,
        *,
        tile_size: int = 1024,
        overlap: int = 96,
        adapter: RestorationAdapter | None = None,
        job_id: str = "job",
        quality_profile: str = "museum_fidelity",
    ) -> dict:
        adapter = adapter or IdentityValidationAdapter()
        if not adapter.enabled:
            raise ValueError(f"Adapter {adapter.name} ist deaktiviert")
        source_path, output_path = Path(source_path), Path(output_path)
        source_metadata = inspect_image(source_path)
        if source_metadata.get("bits_per_sample") and source_metadata["bits_per_sample"] > 8 and source_metadata["mode"] != "I;16":
            raise ValueError("16-Bit-Farbmodus wird in diesem Build nicht sicher unterstützt; Original bleibt erhalten und es erfolgt keine automatische 8-Bit-Konvertierung.")
        with Image.open(source_path) as im:
            if im.width * im.height > MAX_LOCAL_ENGINE_PIXELS:
                raise ValueError(f"Lokaler Validierungslauf auf {MAX_LOCAL_ENGINE_PIXELS:,} Pixel begrenzt; für größere Bilder ist ein geprüfter Cloud-/Streaming-Worker erforderlich.")
            im.load()
            original = im.copy()
        if original.mode not in {"RGB", "RGBA", "L", "I;16"}:
            raise ValueError(f"Farbraum/Bit-Tiefe {original.mode} wird nicht automatisch umgewandelt. Erst farbverwalteten Konvertierungsschritt festlegen.")
        source_info = dict(original.info)
        tiles = plan_tiles(original.width, original.height, tile_size, overlap)
        job_dir = self.work_root / job_id
        tile_dir = job_dir / "tiles"
        tile_dir.mkdir(parents=True, exist_ok=True)
        state_path = job_dir / "state.json"
        config = {
            "source_sha256": sha256_file(source_path), "tile_size": tile_size,
            "overlap": overlap, "adapter": adapter.name, "quality_profile": quality_profile,
            "source_dimensions": [original.width, original.height]
        }
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("config") != config:
                raise ValueError("Vorhandener Job hat andere Eingabe/Parameter; neue job_id verwenden")
        else:
            state = {"job_id": job_id, "status": "running", "config": config, "completed_tiles": [], "created_at": datetime.now(timezone.utc).isoformat()}
            _atomic_json(state_path, state)

        processed: dict[int, Image.Image] = {}
        for tile in tiles:
            tile_path = tile_dir / f"tile_{tile.index:06d}.png"
            if tile_path.exists() and tile.index in state["completed_tiles"]:
                with Image.open(tile_path) as saved:
                    processed[tile.index] = saved.copy()
                continue
            crop = original.crop((tile.x, tile.y, tile.x + tile.width, tile.y + tile.height))
            result = adapter.restore(crop, tile=tile, job_id=job_id, quality_profile=quality_profile)
            if not isinstance(result, Image.Image):
                raise TypeError("Adapter muss ein PIL.Image zurückgeben")
            result.save(tile_path, format="PNG")
            processed[tile.index] = result.copy()
            state["completed_tiles"] = sorted(set(state["completed_tiles"] + [tile.index]))
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            _atomic_json(state_path, state)

        if adapter.scale != 1:
            raise NotImplementedError("Scale != 1 wird erst mit einem validierten Upscale-Adapter freigeschaltet")
        save_kwargs = {}
        if source_info.get("icc_profile") and original.mode in {"RGB", "RGBA"}:
            save_kwargs["icc_profile"] = source_info["icc_profile"]
        if source_info.get("dpi"):
            save_kwargs["dpi"] = source_info["dpi"]
        stitch_tiles((original.width, original.height), tiles, processed, output_path, scale=1, output_mode=original.mode, save_kwargs=save_kwargs)
        metrics = _metrics(source_path, output_path)
        state["status"] = "completed"
        state["completed_at"] = datetime.now(timezone.utc).isoformat()
        state["output"] = str(output_path)
        state["quality_metrics"] = metrics
        _atomic_json(state_path, state)
        return {
            "job_id": job_id, "status": "completed", "adapter": adapter.name,
            "quality_profile": quality_profile, "tiles_total": len(tiles),
            "tiles_completed": len(state["completed_tiles"]), "output": str(output_path),
            "output_metadata": inspect_image(output_path), "quality_metrics": metrics,
            "resume_state": str(state_path),
            "processing_note": "Identity validation only; no AI enhancement was applied."
        }
