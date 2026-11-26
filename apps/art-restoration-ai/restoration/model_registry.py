from dataclasses import dataclass


@dataclass(frozen=True)
class ModelSpec:
    name: str
    role: str
    code_license: str
    weights_license: str
    enabled: bool = False
    weights_verified: bool = False
    notes: str = ""


# Models stay disabled until the exact source revision, weights, license, hash,
# runtime dependencies and GPU worker are reviewed and pinned.
MODEL_REGISTRY = [
    ModelSpec("Real-ESRGAN", "super_resolution", "BSD-3-Clause (upstream; verify exact revision)", "separate review required", notes="No checkpoint configured"),
    ModelSpec("SwinIR", "restoration", "verify upstream revision", "separate review required", notes="No checkpoint configured"),
    ModelSpec("GFPGAN", "face_restoration", "verify upstream revision", "separate review required", notes="Optional face-specific stage; not enabled by default"),
    ModelSpec("Topaz Photo AI", "desktop_enhancement", "commercial EULA", "vendor-managed", notes="Manual desktop handoff; no API/CLI rights assumed"),
    ModelSpec("Topaz Gigapixel AI", "desktop_upscale", "commercial EULA", "vendor-managed", notes="Manual desktop handoff; no API/CLI rights assumed"),
    ModelSpec("libvips", "large_image_io", "LGPL-2.1-or-later", "not applicable", notes="Optional large-image I/O backend"),
]
