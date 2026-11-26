"""Fail-Closed Checkpoint-Integritaetspruefung fuer ART-Restoration-AI.

Verhindert die Ausfuehrung ungepruefter oder manipulierter KI-Gewichte:
Jeder Checkpoint muss exakt gegen seine hinterlegte SHA-256-Pruefsumme
verifiziert werden, bevor ein Adapter aktiviert werden darf.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Dict, List, Sequence

_PACKAGES_DIR = Path(__file__).resolve().parents[3] / "packages" / "platform_core"
if _PACKAGES_DIR.is_dir() and str(_PACKAGES_DIR) not in sys.path:
    sys.path.insert(0, str(_PACKAGES_DIR))

from platform_core.provenance import sha256_file
from platform_core.security import validate_safe_path

ALLOWED_CHECKPOINT_EXTENSIONS = {".pth", ".pt", ".onnx", ".safetensors", ".ckpt"}


@dataclass(frozen=True)
class CheckpointSpec:
    model_name: str
    filename: str
    expected_sha256: str
    license_name: str
    revision: str = "unpinned"


def resolve_checkpoints_dir() -> Path:
    env_dir = os.environ.get("ART_CHECKPOINTS_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    return Path(__file__).resolve().parent.parent / "checkpoints"


def verify_checkpoint_file(
    checkpoint_path: Path,
    expected_sha256: str,
    *,
    base_dir: Path | None = None,
) -> Dict[str, object]:
    """Prueft eine einzelne Checkpoint-Datei fail-closed gegen SHA-256 und Pfadregeln."""
    expected_clean = expected_sha256.strip().lower()
    if len(expected_clean) != 64 or any(c not in "0123456789abcdef" for c in expected_clean):
        return {
            "verified": False,
            "reason": "Ungueltige erwartete SHA-256-Pruefsumme (64 Hex-Zeichen erforderlich)",
            "actual_sha256": None,
        }

    try:
        safe_path = validate_safe_path(
            checkpoint_path,
            base_dir=base_dir,
            allowed_extensions=ALLOWED_CHECKPOINT_EXTENSIONS,
            must_exist=True,
        )
    except (ValueError, FileNotFoundError) as exc:
        return {
            "verified": False,
            "reason": str(exc),
            "actual_sha256": None,
        }

    actual = sha256_file(safe_path).lower()
    if actual != expected_clean:
        return {
            "verified": False,
            "reason": "SHA-256-Pruefsumme stimmt nicht ueberein; Ausfuehrung gesperrt",
            "actual_sha256": actual,
        }

    return {
        "verified": True,
        "reason": "SHA-256 verifiziert",
        "actual_sha256": actual,
        "size_bytes": safe_path.stat().st_size,
        "path": str(safe_path),
    }


def inspect_checkpoints_dir(
    checkpoints_dir: Path | None = None,
    specs: Sequence[CheckpointSpec] = (),
) -> Dict[str, object]:
    """Inspiziert das lokale Checkpoint-Verzeichnis rein lesend und fail-closed."""
    root = checkpoints_dir if checkpoints_dir is not None else resolve_checkpoints_dir()
    entries: List[Dict[str, object]] = []
    verified_count = 0

    for spec in specs:
        candidate = root / spec.filename
        check = verify_checkpoint_file(candidate, spec.expected_sha256, base_dir=root)
        if check["verified"]:
            verified_count += 1
        entries.append(
            {
                "model_name": spec.model_name,
                "filename": spec.filename,
                "license": spec.license_name,
                "revision": spec.revision,
                **check,
            }
        )

    return {
        "checkpoints_dir": str(root),
        "dir_exists": root.is_dir(),
        "configured_specs": len(specs),
        "verified_count": verified_count,
        "all_verified": len(specs) > 0 and verified_count == len(specs),
        "entries": entries,
    }
