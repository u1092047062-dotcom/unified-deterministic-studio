"""Universeller Spandrel-Modell-Adapter fuer ART-Restoration-AI (chaiNNer-org/spandrel).

Ermoeglicht das Laden von 40+ Open-Source-Restaurierungsarchitekturen
(Real-ESRGAN, SwinIR, NAFNet, HAT, DAT, SCUNet) ueber eine einzige Schnittstelle,
strikt geschuetzt durch Fail-Closed SHA-256-Checkpoint-Verifikation.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
from PIL import Image

from restoration.adapters import ModelNotConfigured, RestorationAdapter
from restoration.checkpoints import CheckpointSpec, verify_checkpoint_file


def spandrel_runtime_status() -> Dict[str, object]:
    """Prueft ohne harte Importfehler, ob spandrel & torch lokal verfuegbar sind."""
    has_torch = importlib.util.find_spec("torch") is not None
    has_spandrel = importlib.util.find_spec("spandrel") is not None
    return {
        "torch_installed": has_torch,
        "spandrel_installed": has_spandrel,
        "ready_for_verified_weights": has_torch and has_spandrel,
        "supported_architectures": ["Real-ESRGAN", "SwinIR", "NAFNet", "HAT", "DAT", "SCUNet"],
    }


def load_openmodeldb_manifest(manifest_path: Path | str) -> List[CheckpointSpec]:
    """Laedt eine OpenModelDB-kompatible JSON-Checkpoint-Liste mit gepinnten SHA-256-Hashes."""
    path = Path(manifest_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("models", data) if isinstance(data, dict) else data
    specs: List[CheckpointSpec] = []
    for item in entries:
        specs.append(
            CheckpointSpec(
                model_name=str(item["model_name"]),
                filename=str(item["filename"]),
                expected_sha256=str(item["expected_sha256"]),
                license_name=str(item.get("license", "verify-before-use")),
                revision=str(item.get("revision", "pinned")),
            )
        )
    return specs


class SpandrelModelAdapter(RestorationAdapter):
    """Fail-Closed Adapter: laedt Gewichte erst nach bestandener SHA-256-Pruefung."""

    def __init__(
        self,
        checkpoint_path: Path | str,
        spec: CheckpointSpec,
        *,
        base_dir: Optional[Path] = None,
        device: str = "cpu",
        descriptor_loader: Optional[Callable[[Path], Any]] = None,
    ) -> None:
        self.checkpoint_path = Path(checkpoint_path)
        self.spec = spec
        self.device = device
        self.name = f"spandrel:{spec.model_name}"

        verification = verify_checkpoint_file(
            self.checkpoint_path, spec.expected_sha256, base_dir=base_dir
        )
        if not verification["verified"]:
            raise ValueError(f"Checkpoint-Pruefung fehlgeschlagen: {verification['reason']}")

        self.verified_sha256 = str(verification["actual_sha256"])

        if descriptor_loader is not None:
            self._descriptor = descriptor_loader(self.checkpoint_path)
        else:
            status = spandrel_runtime_status()
            if not status["ready_for_verified_weights"]:
                raise ModelNotConfigured(
                    "Spandrel/PyTorch sind in dieser Umgebung nicht installiert. "
                    "Installiere optional `[gpu]` + `spandrel`, um verifizierte Checkpoints auszufuehren."
                )
            from spandrel import ModelLoader

            self._descriptor = ModelLoader().load_from_file(self.checkpoint_path)
            self._descriptor.eval()
            if hasattr(self._descriptor, "to"):
                self._descriptor.to(device)

        self.scale = int(getattr(self._descriptor, "scale", 1))
        self.architecture = str(getattr(self._descriptor, "architecture", spec.model_name))
        self.enabled = True

    def restore(self, image: Image.Image, **kwargs) -> Image.Image:
        # Wenn ein injizierter/leichtgewichtiger Descriptor direkt PIL.Image akzeptiert:
        if hasattr(self._descriptor, "process_pil"):
            out = self._descriptor.process_pil(image)
            if not isinstance(out, Image.Image):
                raise TypeError("Spandrel-Descriptor muss ein PIL.Image liefern")
            return out

        import torch

        rgb = image.convert("RGB")
        arr = np.asarray(rgb, dtype=np.float32) / 255.0
        tensor = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0).to(self.device)
        with torch.no_grad():
            pred = self._descriptor(tensor)
        out_arr = (
            pred.squeeze(0).permute(1, 2, 0).clamp(0.0, 1.0).cpu().numpy() * 255.0
        ).round().astype(np.uint8)
        return Image.fromarray(out_arr, mode="RGB")
