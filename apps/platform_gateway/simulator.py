"""Voll funktionsfaehiger interaktiver End-to-End-Simulator fuer Unified Deterministic Studio.

Simuliert transparent und lokal (ohne echte externe Accounts, Cloud-Kosten oder
echte Bot-Tokens) alle manuellen/Account-abhaengigen Schritte:
- RunPod.io Cloud-GPU Account, Pod-Provisioning & echtes Budget-Gate
- Telegram @BotFather Setup & interaktive TerraRemoteEngine-Befehlskonsole
- KI-Checkpoint-Verifikation (Real-ESRGAN, SwinIR, Topaz Desktop Handoff)
- Echter Foto- & Video-Upload mit realem SHA-256, realem Tile-Planning,
  sichtbarer Vorher/Nachher-Simulationsverarbeitung und Provenienz-Manifest
- EDA-End-to-End-Durchlauf mit echtem Routing/Gerber + simuliertem KiCad-Signoff

Alle simulierten Ausgaben und IDs sind strikt als [SIMULATION] markiert.
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
from pydantic import BaseModel, Field

from platform_core.provenance import build_provenance_manifest, sha256_file
from platform_core.resource_guard import (
    enforce_budget_gate,
    estimate_compute_cost,
    plan_resources,
)

from core.board_synthesizer import build_template_board, list_templates
from core.export_manifest import build_export_manifest, summarize_board
from core.gerber_export import export_gerber_bundle
from core.kicad_export import export_kicad_pcb
from core.pipeline_v2 import DesignPipeline
from core.telemetry_log import resolve_log_dir as resolve_eda_log_dir

from app.telemetry import resolve_art_log_dir
from restoration.tiling import plan_tiles

PHOTO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
MAX_SIM_UPLOAD_BYTES = 150 * 1024 * 1024  # 150 MB

GPU_CATALOG: Dict[str, Dict[str, Any]] = {
    "RTX_4090_24GB": {"label": "NVIDIA RTX 4090 (24 GB VRAM)", "vram_gb": 24.0, "hourly_usd": 0.44},
    "A40_48GB": {"label": "NVIDIA A40 (48 GB VRAM)", "vram_gb": 48.0, "hourly_usd": 0.39},
    "L4_24GB": {"label": "NVIDIA L4 (24 GB VRAM)", "vram_gb": 24.0, "hourly_usd": 0.34},
    "LOCAL_CPU_8GB": {"label": "Lokale 8-GB-Workstation (kein Cloud-GPU)", "vram_gb": 4.0, "hourly_usd": 0.0},
}


def resolve_sim_dir() -> Path:
    env_dir = os.environ.get("PLATFORM_SIM_DIR", "").strip()
    if env_dir:
        base = Path(env_dir)
    else:
        base = Path(__file__).resolve().parents[2] / "logs" / "simulator_sandbox"
    base.mkdir(parents=True, exist_ok=True)
    return base


class AccountSetupRequest(BaseModel):
    runpod_connected: Optional[bool] = None
    runpod_gpu: Optional[str] = None
    runpod_budget_usd: Optional[float] = Field(default=None, ge=0.0, le=1000.0)
    telegram_connected: Optional[bool] = None
    telegram_alerts_enabled: Optional[bool] = None
    allow_remote_screen_capture: Optional[bool] = None
    allow_remote_process_termination: Optional[bool] = None
    checkpoints_verified: Optional[bool] = None
    topaz_licensed_handoff: Optional[bool] = None
    kicad_cli_simulated: Optional[bool] = None
    activate_all: bool = False


class MediaProcessRequest(BaseModel):
    session_id: str = Field(min_length=4, max_length=64)
    model: str = Field(default="Real-ESRGAN [SIM]")
    quality_profile: str = Field(default="museum_fidelity")
    mode: str = Field(default="restoration_enhance")  # restoration_enhance | upscale_2x | archival_denoise
    tile_size: int = Field(default=512, ge=128, le=2048)
    overlap: int = Field(default=48, ge=0, le=256)
    gpu_override: Optional[str] = None
    budget_override_usd: Optional[float] = Field(default=None, ge=0.0, le=1000.0)


class TelegramSimCommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=128)


class EdaSimRunRequest(BaseModel):
    template_id: str = Field(default="esp32_i2c_sensor")


class PlatformSimulator:
    """Kapselt den gesamten Simulator-Zustand sauber im lokalen Sandbox-Verzeichnis."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> Dict[str, Any]:
        self.accounts: Dict[str, Any] = {
            "runpod": {
                "connected": False,
                "account_id": None,
                "pod_id": None,
                "gpu": "RTX_4090_24GB",
                "budget_usd": 5.0,
                "simulated_spend_usd": 0.0,
            },
            "telegram": {
                "connected": False,
                "bot_handle": None,
                "allowed_user_id": None,
                "alerts_enabled": True,
                "allow_remote_screen_capture": False,
                "allow_remote_process_termination": False,
            },
            "checkpoints": {
                "verified": False,
                "models": ["Real-ESRGAN [SIM]", "SwinIR [SIM]", "Topaz Photo AI [SIM]"],
            },
            "topaz": {
                "licensed_handoff_ready": False,
            },
            "kicad_cli": {
                "simulated_ready": False,
                "version": "8.0.6-sim",
            },
        }
        self.telegram_feed: List[Dict[str, str]] = [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sender": "system",
                "text": "Simulator bereit. Nutze den Setup-Assistenten oder klicke auf 'Alle Accounts simulieren'.",
            }
        ]
        self.sessions: Dict[str, Dict[str, Any]] = {}
        return self.get_state()

    def _push_telegram_alert(self, text: str) -> None:
        if self.accounts["telegram"]["connected"] and self.accounts["telegram"]["alerts_enabled"]:
            self.telegram_feed.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "sender": "bot",
                    "text": text,
                }
            )
            if len(self.telegram_feed) > 60:
                self.telegram_feed = self.telegram_feed[-60:]

    def get_state(self) -> Dict[str, Any]:
        return {
            "mode": "SIMULATION_SANDBOX",
            "disclaimer": (
                "Alle Account-Verbindungen, Cloud-GPU-Pods, KI-Gewichte und Telegram-Alerts "
                "laufen lokal im Simulator ohne echte externe Zugangsdaten oder Kosten."
            ),
            "gpu_catalog": GPU_CATALOG,
            "accounts": self.accounts,
            "telegram_feed": self.telegram_feed[-20:],
            "sessions": [
                {
                    "session_id": sid,
                    "media_type": s["media_type"],
                    "filename": s["filename"],
                    "status": s["status"],
                    "size_bytes": s["size_bytes"],
                    "original_sha256": s["original_sha256"],
                }
                for sid, s in self.sessions.items()
            ],
        }

    def configure_accounts(self, req: AccountSetupRequest) -> Dict[str, Any]:
        if req.activate_all:
            req.runpod_connected = True
            req.telegram_connected = True
            req.checkpoints_verified = True
            req.topaz_licensed_handoff = True
            req.kicad_cli_simulated = True

        rp = self.accounts["runpod"]
        if req.runpod_gpu is not None:
            if req.runpod_gpu not in GPU_CATALOG:
                raise HTTPException(422, f"Unbekannte GPU '{req.runpod_gpu}'")
            rp["gpu"] = req.runpod_gpu
        if req.runpod_budget_usd is not None:
            rp["budget_usd"] = round(float(req.runpod_budget_usd), 4)
        if req.runpod_connected is not None:
            rp["connected"] = req.runpod_connected
            if req.runpod_connected:
                rp["account_id"] = rp["account_id"] or f"SIM-RUNPOD-{uuid.uuid4().hex[:6].upper()}"
                rp["pod_id"] = rp["pod_id"] or f"sim-pod-{uuid.uuid4().hex[:8]}"
            else:
                rp["pod_id"] = None

        tg = self.accounts["telegram"]
        if req.telegram_connected is not None:
            tg["connected"] = req.telegram_connected
            if req.telegram_connected:
                tg["bot_handle"] = "@UnifiedStudioSimBot"
                tg["allowed_user_id"] = "SIM-USER-10001"
                self._push_telegram_alert(
                    "🟢 [SIM] TerraRemoteEngine verbunden. Watchlist: EDA + ART + Topaz aktiv."
                )
        if req.telegram_alerts_enabled is not None:
            tg["alerts_enabled"] = req.telegram_alerts_enabled
        if req.allow_remote_screen_capture is not None:
            tg["allow_remote_screen_capture"] = req.allow_remote_screen_capture
        if req.allow_remote_process_termination is not None:
            tg["allow_remote_process_termination"] = req.allow_remote_process_termination

        if req.checkpoints_verified is not None:
            self.accounts["checkpoints"]["verified"] = req.checkpoints_verified
        if req.topaz_licensed_handoff is not None:
            self.accounts["topaz"]["licensed_handoff_ready"] = req.topaz_licensed_handoff
        if req.kicad_cli_simulated is not None:
            self.accounts["kicad_cli"]["simulated_ready"] = req.kicad_cli_simulated

        return self.get_state()

    def _session_dir(self, session_id: str) -> Path:
        if session_id not in self.sessions:
            raise HTTPException(404, f"Simulator-Session '{session_id}' nicht gefunden")
        return Path(self.sessions[session_id]["dir"])

    def register_media_bytes(self, filename: str, raw_bytes: bytes) -> Dict[str, Any]:
        if not raw_bytes:
            raise HTTPException(400, "Leere Datei hochgeladen")
        if len(raw_bytes) > MAX_SIM_UPLOAD_BYTES:
            raise HTTPException(413, "Datei ueberschreitet das Simulator-Limit (150 MB)")

        suffix = Path(filename or "upload.png").suffix.lower()
        if suffix in PHOTO_EXTENSIONS:
            media_type = "photo"
        elif suffix in VIDEO_EXTENSIONS:
            media_type = "video"
        else:
            allowed = ", ".join(sorted(PHOTO_EXTENSIONS | VIDEO_EXTENSIONS))
            raise HTTPException(415, f"Nicht unterstuetztes Format '{suffix}'. Erlaubt: {allowed}")

        sid = f"sim-{uuid.uuid4().hex[:10]}"
        sdir = resolve_sim_dir() / sid
        sdir.mkdir(parents=True, exist_ok=True)
        orig_path = sdir / f"original{suffix}"
        orig_path.write_bytes(raw_bytes)
        orig_sha = sha256_file(orig_path)

        metadata: Dict[str, Any] = {
            "filename": Path(filename).name,
            "suffix": suffix,
            "size_bytes": len(raw_bytes),
        }

        if media_type == "photo":
            try:
                with Image.open(orig_path) as im:
                    im.load()
                    metadata["width"] = im.width
                    metadata["height"] = im.height
                    metadata["mode"] = im.mode
                    metadata["format"] = im.format or suffix.lstrip(".").upper()
                    metadata["megapixels"] = round((im.width * im.height) / 1_000_000.0, 3)
                    # Web-kompatible Original-Vorschau fuer den Browser-Slider speichern
                    preview_orig = sdir / "preview_original.png"
                    im.convert("RGB").save(preview_orig, format="PNG")
            except Exception as exc:
                orig_path.unlink(missing_ok=True)
                raise HTTPException(400, f"Bilddatei konnte nicht gelesen werden: {exc}")
        else:
            # Video-Metadaten sicher & abhaengigkeitsfrei schaetzen (ohne ffmpeg-Zwang)
            est_mb = len(raw_bytes) / (1024 * 1024)
            est_seconds = max(3.0, round(est_mb * 4.5, 1))
            est_fps = 30
            metadata["estimated_duration_s"] = est_seconds
            metadata["estimated_fps"] = est_fps
            metadata["estimated_frames"] = int(est_seconds * est_fps)
            metadata["estimated_resolution"] = "1920x1080 (geschaetzt)"

        session = {
            "session_id": sid,
            "dir": str(sdir),
            "media_type": media_type,
            "filename": Path(filename).name,
            "suffix": suffix,
            "original_path": str(orig_path),
            "result_path": None,
            "original_sha256": orig_sha,
            "size_bytes": len(raw_bytes),
            "metadata": metadata,
            "status": "uploaded",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "last_run": None,
        }
        self.sessions[sid] = session
        return {
            "session_id": sid,
            "media_type": media_type,
            "filename": session["filename"],
            "size_bytes": len(raw_bytes),
            "original_sha256": orig_sha,
            "metadata": metadata,
            "original_url": f"/sim/media/{sid}/original",
        }

    def create_demo_photo(self) -> Dict[str, Any]:
        """Erzeugt ein realistisches, leicht verrauschtes/weichgezeichnetes Test-Gemaelde/Foto."""
        w, h = 640, 420
        rng = np.random.default_rng(42)
        y_grid, x_grid = np.mgrid[0:h, 0:w]
        # Warmer Sonnenuntergang + Architektur-Silhouetten + feines Rauschen
        r = np.clip(45 + (y_grid / h) * 185 + 25 * np.sin(x_grid / 60.0), 0, 255)
        g = np.clip(35 + (y_grid / h) * 115 + 20 * np.cos(y_grid / 50.0), 0, 255)
        b = np.clip(90 + (1.0 - y_grid / h) * 120, 0, 255)
        arr = np.stack([r, g, b], axis=-1)
        noise = rng.normal(0, 14, size=arr.shape)
        noisy = np.clip(arr + noise, 0, 255).astype(np.uint8)

        img = Image.fromarray(noisy, mode="RGB")
        draw = ImageDraw.Draw(img)
        # Geometrische Teststrukturen (feine Linien & Kreise zur Schaerfe-Beurteilung)
        draw.ellipse((430, 55, 560, 185), fill=(245, 210, 120), outline=(255, 240, 190), width=3)
        draw.rectangle((60, 220, 180, 420), fill=(32, 42, 68), outline=(120, 140, 180), width=2)
        draw.rectangle((195, 170, 310, 420), fill=(44, 54, 82), outline=(140, 160, 200), width=2)
        for wx in range(80, 165, 24):
            for wy in range(245, 390, 30):
                draw.rectangle((wx, wy, wx + 12, wy + 16), fill=(240, 205, 110))
        img = img.filter(ImageFilter.GaussianBlur(radius=1.1))

        buf = BytesIO()
        img.save(buf, format="PNG")
        return self.register_media_bytes("demo_kunstwerk_testbild.png", buf.getvalue())

    def process_media(self, req: MediaProcessRequest) -> Dict[str, Any]:
        sdir = self._session_dir(req.session_id)
        session = self.sessions[req.session_id]
        started = time.perf_counter()

        gpu_key = req.gpu_override or self.accounts["runpod"]["gpu"]
        if gpu_key not in GPU_CATALOG:
            raise HTTPException(422, f"Unbekannte GPU '{gpu_key}'")
        gpu_info = GPU_CATALOG[gpu_key]
        budget = (
            req.budget_override_usd
            if req.budget_override_usd is not None
            else self.accounts["runpod"]["budget_usd"]
        )

        # Fail-Closed Pruefung der simulierten Voraussetzungen
        if gpu_key != "LOCAL_CPU_8GB" and not self.accounts["runpod"]["connected"]:
            raise HTTPException(
                409,
                "Simulierter RunPod.io-Account ist noch nicht verbunden. Bitte zuerst im Setup-Assistenten aktivieren.",
            )
        if not self.accounts["checkpoints"]["verified"] and "identity" not in req.model.lower():
            raise HTTPException(
                409,
                "Simulierte KI-Checkpoints sind noch nicht SHA-256-verifiziert. Bitte im Setup-Assistenten verifizieren.",
            )

        steps: List[Dict[str, Any]] = []
        steps.append(
            {
                "stage": "1. Original-Integritaet (SHA-256)",
                "status": "PASS",
                "detail": f"Unveraenderliches Original verifiziert ({session['original_sha256'][:16]}...)",
            }
        )

        resource_plan = plan_resources(
            ram_gb=8.0, vram_gb=gpu_info["vram_gb"], quality_profile=req.quality_profile
        ).__dict__
        steps.append(
            {
                "stage": "2. Quality-First Resource Guard",
                "status": "PASS",
                "detail": (
                    f"Tile={resource_plan['tile_size']}px, Concurrency={resource_plan['concurrency']}, "
                    f"allow_quality_downgrade={resource_plan['allow_quality_downgrade']}"
                ),
            }
        )

        if session["media_type"] == "photo":
            w = session["metadata"]["width"]
            h = session["metadata"]["height"]
            tiles = plan_tiles(w, h, req.tile_size, req.overlap)
            est_runtime_s = max(4.0, len(tiles) * (2.5 if req.mode == "upscale_2x" else 1.4))
            est_cost = estimate_compute_cost(est_runtime_s, gpu_info["hourly_usd"])
            budget_check = enforce_budget_gate(est_cost, budget)

            steps.append(
                {
                    "stage": "3. RunPod.io Budget- & Cost-Gate [SIM]",
                    "status": "PASS" if budget_check["allowed"] else "BLOCKED",
                    "detail": f"GPU={gpu_info['label']}, Geschaetzt=${est_cost:.6f} / Budget=${budget:.4f} – {budget_check['reason']}",
                }
            )
            if not budget_check["allowed"]:
                raise HTTPException(402, f"Budget-Gate gestoppt (Quality-First): {budget_check['reason']}")

            # Sichtbare, deterministische Simulations-Bearbeitung des echten hochgeladenen Fotos
            with Image.open(session["original_path"]) as src_im:
                src_rgb = src_im.convert("RGB")
                if req.mode == "upscale_2x":
                    # Begrenze maximale Vorschau-Ausgabe fuer 8 GB RAM
                    scale = 2 if (w * h <= 2_500_000) else 1
                    worked = src_rgb.resize((w * scale, h * scale), Image.Resampling.LANCZOS)
                    worked = worked.filter(ImageFilter.UnsharpMask(radius=1.4, percent=135, threshold=2))
                    worked = ImageEnhance.Contrast(worked).enhance(1.08)
                elif req.mode == "archival_denoise":
                    worked = src_rgb.filter(ImageFilter.MedianFilter(size=3))
                    worked = worked.filter(ImageFilter.UnsharpMask(radius=1.1, percent=110, threshold=3))
                    worked = ImageEnhance.Color(worked).enhance(1.04)
                else:  # restoration_enhance
                    worked = src_rgb.filter(ImageFilter.MedianFilter(size=3))
                    worked = worked.filter(ImageFilter.UnsharpMask(radius=1.5, percent=155, threshold=2))
                    worked = ImageEnhance.Contrast(worked).enhance(1.12)
                    worked = ImageEnhance.Color(worked).enhance(1.10)

                result_path = sdir / "simulated_restored.png"
                worked.save(result_path, format="PNG")
                out_w, out_h = worked.size

                # Echte Qualitaetsmetriken (PSNR / MSE nach Rueckskalierung auf Originalgroesse)
                src_arr = np.asarray(src_rgb, dtype=np.float32)
                cmp_img = worked.resize((w, h), Image.Resampling.LANCZOS) if (out_w, out_h) != (w, h) else worked
                dst_arr = np.asarray(cmp_img, dtype=np.float32)
                mse = float(np.mean((src_arr - dst_arr) ** 2))
                psnr = float("inf") if mse == 0 else round(float(10.0 * np.log10((255.0 ** 2) / mse)), 2)

            result_sha = sha256_file(result_path)
            steps.append(
                {
                    "stage": "4. Kachel-Orchestrierung & Feather-Stitching [SIM]",
                    "status": "PASS",
                    "detail": f"{len(tiles)} Kachel(n) mit {req.model} im Modus '{req.mode}' verarbeitet ({out_w}x{out_h}px)",
                }
            )
            quality_metrics = {
                "source_dimensions": [w, h],
                "output_dimensions": [out_w, out_h],
                "tiles_processed": len(tiles),
                "mse_after_downsample": round(mse, 3),
                "psnr_db": psnr if np.isfinite(psnr) else "infinity",
                "simulation_notice": "Sichtbare lokale Simulations-Vorschau (kein echtes neuronales KI-Modell).",
            }
        else:
            # Video-Pipeline-Simulation mit echter Datei-SHA256, Kosten-Gate & visueller Frame-Vorschaukarte
            est_frames = int(session["metadata"]["estimated_frames"])
            est_runtime_s = max(12.0, est_frames * (0.12 if req.mode == "upscale_2x" else 0.06))
            est_cost = estimate_compute_cost(est_runtime_s, gpu_info["hourly_usd"])
            budget_check = enforce_budget_gate(est_cost, budget)

            steps.append(
                {
                    "stage": "3. RunPod.io Video-Batch Budget-Gate [SIM]",
                    "status": "PASS" if budget_check["allowed"] else "BLOCKED",
                    "detail": (
                        f"{est_frames} Frames auf {gpu_info['label']} -> "
                        f"Geschaetzt=${est_cost:.6f} / Budget=${budget:.4f}"
                    ),
                }
            )
            if not budget_check["allowed"]:
                raise HTTPException(402, f"Budget-Gate gestoppt (Quality-First): {budget_check['reason']}")

            # Erzeuge eine reale visuelle Video-Simulations-Reportkarte (PNG) + behalte Original-Video fuer Browser-Player
            result_path = sdir / "simulated_video_preview.png"
            card = Image.new("RGB", (760, 260), (15, 23, 42))
            draw = ImageDraw.Draw(card)
            draw.rectangle((16, 16, 368, 244), fill=(30, 41, 59), outline=(100, 116, 139), width=2)
            draw.rectangle((392, 16, 744, 244), fill=(20, 48, 66), outline=(56, 189, 248), width=2)
            draw.text((34, 34), "ORIGINAL VIDEO STREAM", fill=(148, 163, 184))
            draw.text((34, 72), f"Datei: {session['filename'][:34]}", fill=(226, 232, 240))
            draw.text((34, 102), f"Groesse: {round(session['size_bytes']/1024, 1)} KB", fill=(226, 232, 240))
            draw.text((34, 132), f"Frames (geschaetzt): {est_frames}", fill=(226, 232, 240))
            draw.text((34, 162), f"SHA-256: {session['original_sha256'][:20]}...", fill=(148, 163, 184))

            draw.text((410, 34), "SIMULIERTE RESTAURIERUNG", fill=(56, 189, 248))
            draw.text((410, 72), f"Modell: {req.model}", fill=(226, 232, 240))
            draw.text((410, 102), f"Modus: {req.mode}", fill=(226, 232, 240))
            draw.text((410, 132), f"GPU: {gpu_key}", fill=(34, 197, 94))
            draw.text((410, 162), f"Sim. Kosten: ${est_cost:.5f} USD", fill=(34, 197, 94))
            draw.text((410, 198), "[SIMULATION - KEIN ECHTER GPU-ENCODE]", fill=(250, 204, 21))
            card.save(result_path, format="PNG")
            result_sha = sha256_file(result_path)

            steps.append(
                {
                    "stage": "4. Frame-Batch-Restaurierung & Audio-Muxing [SIM]",
                    "status": "PASS",
                    "detail": f"{est_frames} Frames im Modus '{req.mode}' auf Pod {self.accounts['runpod']['pod_id'] or 'lokal'} simuliert",
                }
            )
            quality_metrics = {
                "estimated_frames": est_frames,
                "estimated_duration_s": session["metadata"]["estimated_duration_s"],
                "video_playback_supported": session["suffix"] in {".mp4", ".webm"},
                "simulation_notice": "Video-Frame-Pipeline simuliert; Originaldatei bleibt unveraendert und abspielbar.",
            }

        self.accounts["runpod"]["simulated_spend_usd"] = round(
            self.accounts["runpod"]["simulated_spend_usd"] + est_cost, 6
        )

        # Echtes kryptografisches Provenienz-Manifest fuer den Simulationslauf
        manifest = build_provenance_manifest(
            {
                f"original{session['suffix']}": Path(session["original_path"]).read_bytes(),
                result_path.name: result_path.read_bytes(),
            },
            metadata={
                "session_id": req.session_id,
                "media_type": session["media_type"],
                "model": req.model,
                "mode": req.mode,
                "gpu": gpu_key,
                "simulated": True,
                "estimated_cost_usd": est_cost,
            },
            domain="platform-simulator",
        )
        manifest_path = sdir / "provenance_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
        steps.append(
            {
                "stage": "5. SHA-256 Provenienz & Telegram-Benachrichtigung",
                "status": "PASS",
                "detail": f"Manifest-SHA256: {manifest['manifest_sha256'][:16]}... ({duration_ms} ms)",
            }
        )

        self._push_telegram_alert(
            f"✅ [SIM] {session['media_type'].upper()}-Job abgeschlossen: {session['filename']} | "
            f"Modell: {req.model} | Kosten: ${est_cost:.5f} | Manifest: {manifest['manifest_sha256'][:12]}..."
        )

        run_summary = {
            "session_id": req.session_id,
            "media_type": session["media_type"],
            "status": "completed_simulated",
            "duration_ms": duration_ms,
            "estimated_cost_usd": est_cost,
            "gpu": gpu_info["label"],
            "resource_plan": resource_plan,
            "quality_metrics": quality_metrics,
            "steps": steps,
            "original_sha256": session["original_sha256"],
            "result_sha256": result_sha,
            "manifest_sha256": manifest["manifest_sha256"],
            "original_url": f"/sim/media/{req.session_id}/original",
            "result_url": f"/sim/media/{req.session_id}/result",
            "provenance_url": f"/sim/media/{req.session_id}/provenance",
        }
        session["result_path"] = str(result_path)
        session["status"] = "completed_simulated"
        session["last_run"] = run_summary
        return run_summary

    def run_telegram_command(self, raw_cmd: str) -> Dict[str, Any]:
        cmd = raw_cmd.strip()
        tg = self.accounts["telegram"]
        if not tg["connected"]:
            raise HTTPException(
                409,
                "Simulierter Telegram-Bot ist noch nicht eingerichtet. Bitte im Setup-Assistenten aktivieren.",
            )

        self.telegram_feed.append(
            {"timestamp": datetime.now(timezone.utc).isoformat(), "sender": "user", "text": cmd}
        )

        parts = cmd.split()
        verb = parts[0].lower()
        arg = parts[1].lower() if len(parts) > 1 else ""

        if verb == "/status":
            reply = (
                f"🟢 [SIM] TerraRemoteEngine v1.2.0 aktiv.\n"
                f"📡 Push-Alerts: {'aktiviert' if tg['alerts_enabled'] else 'pausiert'}\n"
                f"☁️ RunPod-Sim: {'verbunden (' + self.accounts['runpod']['gpu'] + ')' if self.accounts['runpod']['connected'] else 'getrennt'}"
            )
        elif verb == "/stats":
            reply = (
                "🖥 [SIM] HARDWARE TELEMETRIE\n"
                "⚙️ CPU: 18.4% | 🧠 RAM: 4120 / 7998 MB (52%)\n"
                "🔒 Cargo-Schutz: --jobs 2 | D:\\MEINE_PROJEKTE\\.cargo_target_cache"
            )
        elif verb == "/ps":
            reply = (
                "📋 [SIM] WATCHLIST-PROZESSE\n"
                "python.exe (uvicorn Gateway) | eda-cad-studio.exe | rust-pc-remote.exe\n"
                "Beenden lokal gesperrt (ALLOW_REMOTE_PROCESS_TERMINATION=false)."
            )
        elif verb == "/tail" and arg in ("eda", "art"):
            log_path = (
                resolve_eda_log_dir() / "eda_pipeline.log"
                if arg == "eda"
                else resolve_art_log_dir() / "art_pipeline.log"
            )
            if log_path.is_file():
                lines = log_path.read_text(encoding="utf-8").splitlines()[-6:]
                reply = f"📄 [SIM] Letzte Zeilen aus {log_path.name}:\n" + ("\n".join(lines) or "(leer)")
            else:
                reply = f"ℹ️ [SIM] Logdatei '{log_path.name}' enthaelt noch keine Eintraege (fuehre erst einen Job aus)."
        elif verb == "/getlog" and arg in ("eda", "art"):
            reply = f"📎 [SIM] Read-Only Log-Export fuer '{arg}' validiert (keine '..'-Traversal, keine .env-Dateien)."
        elif verb == "/alerts" and arg in ("on", "off"):
            tg["alerts_enabled"] = arg == "on"
            reply = "🔔 [SIM] Push-Alerts AKTIVIERT." if tg["alerts_enabled"] else "🔕 [SIM] Push-Alerts PAUSIERT."
        elif verb == "/screen":
            if not tg["allow_remote_screen_capture"]:
                reply = (
                    "🔒 [FAIL-CLOSED] Fern-Screenshots sind standardmaessig gesperrt "
                    "(ALLOW_REMOTE_SCREEN_CAPTURE=false)."
                )
            else:
                reply = "📸 [SIM] Fern-Screenshot freigegeben – simuliertes Desktop-PNG erzeugt."
        elif verb == "/kill":
            if not tg["allow_remote_process_termination"]:
                reply = (
                    "🔒 [FAIL-CLOSED] Remote-Prozessbeendigung ist standardmaessig gesperrt "
                    "(ALLOW_REMOTE_PROCESS_TERMINATION=false)."
                )
            else:
                reply = f"🎯 [SIM] Prozess-Signal fuer PID {arg or '?'} simuliert (kein echter Prozess beendet)."
        else:
            reply = (
                "ℹ️ [SIM] Verfuegbare Befehle: /status, /stats, /ps, /tail eda, /tail art, "
                "/getlog eda, /getlog art, /alerts on, /alerts off, /screen, /kill 1234"
            )

        self.telegram_feed.append(
            {"timestamp": datetime.now(timezone.utc).isoformat(), "sender": "bot", "text": reply}
        )
        return {"command": cmd, "reply": reply, "telegram_feed": self.telegram_feed[-20:]}

    def run_eda_simulation(self, template_id: str) -> Dict[str, Any]:
        started = time.perf_counter()
        try:
            board = build_template_board(template_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc))

        result = DesignPipeline().run(board)
        gerbers = export_gerber_bundle(result.board)
        kicad_text = export_kicad_pcb(result.board)
        manifest = build_export_manifest(
            {"board.kicad_pcb": kicad_text, **gerbers},
            summarize_board(result.board),
        )
        duration_ms = round((time.perf_counter() - started) * 1000.0, 2)

        kicad_sim_ok = self.accounts["kicad_cli"]["simulated_ready"]
        self._push_telegram_alert(
            f"🔧 [SIM] EDA-Lauf '{template_id}': {result.status} | "
            f"Traces: {len(result.board.traces)} | DRC-Fehler: {len(result.board.errors)} | "
            f"Manifest: {manifest['manifest_sha256'][:12]}..."
        )
        return {
            "template_id": template_id,
            "pipeline_status": result.status,
            "attempts_used": result.attempts_used,
            "duration_ms": duration_ms,
            "board_summary": manifest["board_summary"],
            "manifest_sha256": manifest["manifest_sha256"],
            "gerber_files": sorted(gerbers.keys()),
            "kicad_cli_signoff": (
                "PASS [SIM kicad-cli 8.0.6-sim: 0 DRC / 0 Unconnected]"
                if kicad_sim_ok
                else "SKIPPED (im Setup-Assistenten aktivierbar)"
            ),
            "report": result.report,
        }


SIMULATOR = PlatformSimulator()
sim_router = APIRouter(prefix="/sim", tags=["Simulator"])


@sim_router.get("/state")
def sim_state() -> JSONResponse:
    return JSONResponse(content=SIMULATOR.get_state())


@sim_router.post("/reset")
def sim_reset() -> JSONResponse:
    return JSONResponse(content=SIMULATOR.reset())


@sim_router.post("/accounts/setup")
def sim_setup_accounts(req: AccountSetupRequest) -> JSONResponse:
    return JSONResponse(content=SIMULATOR.configure_accounts(req))


@sim_router.post("/media/demo")
def sim_create_demo_photo() -> JSONResponse:
    return JSONResponse(content=SIMULATOR.create_demo_photo())


@sim_router.post("/media/upload")
async def sim_upload_media(file: UploadFile = File(...)) -> JSONResponse:
    raw = await file.read()
    return JSONResponse(content=SIMULATOR.register_media_bytes(file.filename or "upload.png", raw))


@sim_router.post("/media/process")
def sim_process_media(req: MediaProcessRequest) -> JSONResponse:
    return JSONResponse(content=SIMULATOR.process_media(req))


@sim_router.get("/media/{session_id}/original")
def sim_get_original(session_id: str) -> FileResponse:
    sdir = SIMULATOR._session_dir(session_id)
    session = SIMULATOR.sessions[session_id]
    if session["media_type"] == "photo":
        preview = sdir / "preview_original.png"
        if preview.is_file():
            return FileResponse(preview, media_type="image/png")
    return FileResponse(Path(session["original_path"]))


@sim_router.get("/media/{session_id}/result")
def sim_get_result(session_id: str) -> FileResponse:
    SIMULATOR._session_dir(session_id)
    result_path = SIMULATOR.sessions[session_id].get("result_path")
    if not result_path or not Path(result_path).is_file():
        raise HTTPException(404, "Noch kein Simulationsergebnis vorhanden")
    return FileResponse(Path(result_path), media_type="image/png")


@sim_router.get("/media/{session_id}/provenance")
def sim_get_provenance(session_id: str) -> JSONResponse:
    sdir = SIMULATOR._session_dir(session_id)
    manifest_path = sdir / "provenance_manifest.json"
    if not manifest_path.is_file():
        raise HTTPException(404, "Noch kein Provenienz-Manifest erzeugt")
    return JSONResponse(content=json.loads(manifest_path.read_text(encoding="utf-8")))


@sim_router.post("/telegram/command")
def sim_telegram_command(req: TelegramSimCommandRequest) -> JSONResponse:
    return JSONResponse(content=SIMULATOR.run_telegram_command(req.command))


@sim_router.post("/eda/run")
def sim_eda_run(req: EdaSimRunRequest) -> JSONResponse:
    return JSONResponse(content=SIMULATOR.run_eda_simulation(req.template_id))


@sim_router.get("/eda/templates")
def sim_eda_templates() -> JSONResponse:
    return JSONResponse(content={"templates": list_templates()})
