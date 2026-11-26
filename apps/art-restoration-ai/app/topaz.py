from pathlib import Path
from datetime import datetime, timezone
import json
import os
import shutil
import uuid

from app.storage import sha256_file, inspect_image


def detect_topaz_installations() -> list[dict]:
    candidates = [
        ("Topaz Photo AI", os.getenv("TOPAZ_PHOTO_AI_EXE") or r"C:\Program Files\Topaz Labs LLC\Topaz Photo AI\tpai.exe"),
        ("Topaz Gigapixel AI", os.getenv("TOPAZ_GIGAPIXEL_EXE") or r"C:\Program Files\Topaz Labs LLC\Topaz Gigapixel AI\gigapixel.exe"),
    ]
    results = []
    for name, raw_path in candidates:
        path = Path(raw_path)
        exists = path.is_file()
        version = None
        signature = None
        digest = None
        if exists:
            digest = sha256_file(path)
            if os.name == "nt":
                try:
                    import subprocess
                    command = "(Get-Item -LiteralPath $args[0]).VersionInfo.FileVersion"
                    completed = subprocess.run(
                        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command, str(path)],
                        capture_output=True, text=True, timeout=8, check=False
                    )
                    if completed.returncode == 0:
                        version = completed.stdout.strip() or None
                    sig_command = "(Get-AuthenticodeSignature -LiteralPath $args[0]).Status"
                    sig = subprocess.run(
                        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", sig_command, str(path)],
                        capture_output=True, text=True, timeout=8, check=False
                    )
                    if sig.returncode == 0:
                        signature = sig.stdout.strip() or None
                except Exception as exc:
                    signature = f"Prüfung nicht verfügbar: {type(exc).__name__}"
        results.append({
            "name": name, "path": str(path), "exists": exists, "version": version,
            "sha256": digest, "authenticode_status": signature,
            "integration": "manual_handoff", "automation_enabled": False
        })
    return results


def create_topaz_handoff(project: Path, tool: str) -> dict:
    manifest_path = project / "source_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("Kein Originalbild vorhanden")
    source_record = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = project / source_record["file"]
    if not source.is_file() or sha256_file(source) != source_record["sha256"]:
        raise ValueError("Originaldatei fehlt oder SHA-256 stimmt nicht – Übergabe gestoppt")
    handoff = project / "topaz" / "export" / str(uuid.uuid4())
    handoff.mkdir(parents=True, exist_ok=False)
    target = handoff / source.name
    shutil.copy2(source, target)
    manifest = {
        "workflow": "manual_topaz_handoff", "tool_requested": tool,
        "created_at": datetime.now(timezone.utc).isoformat(), "source_file": target.name,
        "source_sha256": sha256_file(target), "original_sha256": source_record["sha256"],
        "instructions": "Kopie in der regulär lizenzierten Topaz-Desktopanwendung bearbeiten; Ergebnis separat exportieren und importieren.",
        "automation": "disabled_without_vendor_authorized_interface"
    }
    (handoff / "handoff_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"handoff_directory": str(handoff), "image_copy": str(target), "manifest": manifest}


def import_topaz_result(project: Path, result: Path, tool: str, version: str | None = None) -> dict:
    meta = inspect_image(result)
    record = {
        "tool_label": tool, "tool_version": version,
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "file": str(result.relative_to(project)), "sha256": sha256_file(result),
        "metadata": meta, "provenance": "externally_processed_by_operator_in_topaz",
        "historical_authenticity_verified": False
    }
    log = project / "topaz" / "imports.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record
