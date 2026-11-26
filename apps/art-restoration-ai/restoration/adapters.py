from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from PIL import Image


class ModelNotConfigured(RuntimeError):
    pass


class RestorationAdapter:
    name = "abstract"
    scale = 1
    enabled = False

    def restore(self, image: Image.Image, **kwargs) -> Image.Image:
        raise NotImplementedError


class IdentityValidationAdapter(RestorationAdapter):
    """Deterministic no-op used to validate tiling/stitching; it does not enhance images."""
    name = "identity-validation-only"
    enabled = True

    def restore(self, image: Image.Image, **kwargs) -> Image.Image:
        return image.copy()


class RealESRGANAdapter(RestorationAdapter):
    name = "Real-ESRGAN"
    def restore(self, image, **kwargs):
        raise ModelNotConfigured("Real-ESRGAN ist deaktiviert: geprüfte Gewichte und GPU-Worker fehlen")


class SwinIRAdapter(RestorationAdapter):
    name = "SwinIR"
    def restore(self, image, **kwargs):
        raise ModelNotConfigured("SwinIR ist deaktiviert: geprüfte Gewichte und GPU-Worker fehlen")


@dataclass(frozen=True)
class AdapterStatus:
    name: str
    available: bool
    mode: str
    reason: str


def available_adapters() -> list[AdapterStatus]:
    return [
        AdapterStatus("identity-validation-only", True, "validation", "Prüft Pipeline ohne Bildverbesserung"),
        AdapterStatus("Real-ESRGAN", False, "super_resolution", "Checkpoint/Worker nicht eingerichtet"),
        AdapterStatus("SwinIR", False, "restoration", "Checkpoint/Worker nicht eingerichtet"),
        AdapterStatus("Topaz Photo AI", False, "manual-desktop-handoff", "Desktop-Workflow; keine bestätigte API/CLI-Automation"),
        AdapterStatus("Topaz Gigapixel AI", False, "manual-desktop-handoff", "Desktop-Workflow; keine bestätigte API/CLI-Automation"),
    ]
