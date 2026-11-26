"""Voll funktionsfaehiger 100%-Paritaets-Simulator fuer Unified Deterministic Studio.

Bildet saemtliche realen Projektfunktionen ohne externe Registrierungen,
Cloud-Kosten oder echte Bot-Tokens vollstaendig lokal ab:
1. Account-/System-Setup: RunPod.io GPU + Budget-Gate, Telegram @BotFather +
   Fail-Closed Toggles, SHA-256 KI-Checkpoints, Topaz Desktop Handoff, KiCad-CLI.
2. Foto- & Video-Studio (ART-Paritaet):
   - Echter Upload (Foto/Video) + unveraenderliches Original + realer SHA-256
   - Visuelles Kachel-Grid-Overlay (echtes plan_tiles())
   - Unterbrechung & atomares Resume mit Config-Hash-Sperre
   - Kompletter Topaz-Handoff-Zyklus (Export -> Bearbeiten/Simulieren -> Import)
   - Sichtbare Vorher/Nachher-Restaurierung + echte PSNR/MSE + Download-Endpunkte
3. EDA-Hardware-Studio (EDA-Paritaet):
   - Echter Z3-Pinout-Multiplex-Solver (SAT & UNSAT-Demo)
   - Templates + Custom DesignSpec Synthesizer + echtes Routing/DRC
   - Live-PCB-SVG-Rendering (Board, Bauteile, TOP/BOTTOM-Traces, Vias)
   - Echte Downloads fuer alle 7 Fertigungsartefakte (KiCad, STEP, OpenSCAD,
     JLCPCB BOM & CPL, gerbers.zip, export_manifest.json)
4. TerraRemoteEngine-Simulator:
   - Interaktives Touch-Dashboard inkl. 2-stufiger Shutdown/Reboot-Bestaetigung + /abort
   - Prozess-Watchlist-Event-Simulator (EDA + ART + Topaz + System)
   - Fail-Closed Security-Checks (Path-Traversal ../.env, /screen, /kill, Secret-Scanner)
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import time
import uuid
import zipfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
from pydantic import BaseModel, Field

from platform_core.job_store import compute_config_hash
from platform_core.provenance import build_provenance_manifest, sha256_file
from platform_core.resource_guard import (
    enforce_budget_gate,
    estimate_compute_cost,
    plan_resources,
)
from platform_core.security import scan_text_for_secrets, validate_safe_path
from platform_core.telemetry import sanitize_payload

from core.board_synthesizer import DesignSpec, build_template_board, list_templates, synthesize_board
from core.enclosure_gen import generate_openscad_case
from core.export_manifest import build_export_manifest, summarize_board
from core.gerber_export import export_gerber_bundle
from core.kicad_export import export_kicad_pcb
from core.manufacturing_export import generate_jlcpcb_bom, generate_jlcpcb_cpl
from core.pipeline_v2 import DesignPipeline
from core.schema import BoardState
from core.solver import MCUPackage, PinMultiplexSolver, PinRequirement
from core.step_export import generate_step
from core.telemetry_log import resolve_log_dir as resolve_eda_log_dir

from app.telemetry import resolve_art_log_dir
from restoration.tiling import plan_tiles

PHOTO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}
MAX_SIM_UPLOAD_BYTES = 150 * 1024 * 1024

GPU_CATALOG: Dict[str, Dict[str, Any]] = {
    "RTX_4090_24GB": {"label": "NVIDIA RTX 4090 (24 GB VRAM)", "vram_gb": 24.0, "hourly_usd": 0.44},
    "A40_48GB": {"label": "NVIDIA A40 (48 GB VRAM)", "vram_gb": 48.0, "hourly_usd": 0.39},
    "L4_24GB": {"label": "NVIDIA L4 (24 GB VRAM)", "vram_gb": 24.0, "hourly_usd": 0.34},
    "LOCAL_CPU_8GB": {"label": "Lokale 8-GB-Workstation (kein Cloud-GPU)", "vram_gb": 4.0, "hourly_usd": 0.0},
}

DEFAULT_WATCHLIST = {
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "taskmgr.exe",
    "regedit.exe",
    "eda-cad-studio.exe",
    "python.exe",
    "uvicorn.exe",
    "cargo.exe",
    "kicad-cli.exe",
    "openscad.exe",
    "tpai.exe",
    "gigapixel.exe",
}


def resolve_sim_dir() -> Path:
    env_dir = os.environ.get("PLATFORM_SIM_DIR", "").strip()
    if env_dir:
        base = Path(env_dir)
    else:
        base = Path(__file__).resolve().parents[2] / "logs" / "simulator_sandbox"
    base.mkdir(parents=True, exist_ok=True)
    return base


def render_board_svg(board: BoardState) -> str:
    """Rendert ein deterministisches, skalierbares 2D-PCB-SVG (Platine, Bauteile, Traces, Vias)."""
    w = max(float(board.width), 10.0)
    h = max(float(board.height), 10.0)
    pad = 4.0
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-pad} {-pad} {w + 2*pad} {h + 2*pad}" '
        'style="width:100%;height:auto;background:#071318;border-radius:10px;">',
        f'<rect x="0" y="0" width="{w}" height="{h}" rx="1.2" fill="#0d3b2e" stroke="#22c55e" stroke-width="0.45"/>',
    ]

    # Kupferflaechen (subtle)
    for pour in board.copper_pours:
        if pour.outer:
            pts = " ".join(f"{x:.2f},{h - y:.2f}" for x, y in pour.outer)
            parts.append(
                f'<polygon points="{pts}" fill="rgba(56,189,248,0.10)" stroke="rgba(56,189,248,0.28)" stroke-width="0.18"/>'
            )

    # Traces (BOTTOM blau, TOP rot/amber)
    for trace in board.traces:
        if len(trace.path) < 2:
            continue
        pts = " ".join(f"{x:.2f},{h - y:.2f}" for x, y in trace.path)
        color = "#f97316" if trace.layer.upper() == "TOP" else "#38bdf8"
        stroke_w = max(float(trace.width), 0.35)
        parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="{stroke_w:.2f}" '
            'stroke-linecap="round" stroke-linejoin="round"/>'
        )

    # Bauteile & Pads
    for cid in sorted(board.components.keys()):
        comp = board.components[cid]
        bw = float(comp.body_w or 3.0)
        bh = float(comp.body_h or 2.0)
        cx, cy = float(comp.x), h - float(comp.y)
        parts.append(
            f'<rect x="{cx - bw/2:.2f}" y="{cy - bh/2:.2f}" width="{bw:.2f}" height="{bh:.2f}" '
            'rx="0.3" fill="#1e293b" stroke="#e2e8f0" stroke-width="0.25"/>'
        )
        parts.append(
            f'<text x="{cx:.2f}" y="{cy - bh/2 - 0.8:.2f}" fill="#f8fafc" font-size="2.2" '
            f'text-anchor="middle" font-family="monospace">{cid}</text>'
        )
        for pid in sorted(comp.pins.keys()):
            pin = comp.pins[pid]
            px = float(pin.x) if pin.x is not None else float(comp.x)
            py = h - (float(pin.y) if pin.y is not None else float(comp.y))
            parts.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="0.55" fill="#fbbf24"/>')

    # Vias
    for via in board.vias:
        vx, vy = float(via.x), h - float(via.y)
        r = max(float(via.diameter) / 2.0, 0.4)
        parts.append(f'<circle cx="{vx:.2f}" cy="{vy:.2f}" r="{r:.2f}" fill="#eab308" stroke="#0f172a" stroke-width="0.22"/>')

    parts.append("</svg>")
    return "".join(parts)


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
    mode: str = Field(default="restoration_enhance")
    tile_size: int = Field(default=512, ge=128, le=2048)
    overlap: int = Field(default=48, ge=0, le=256)
    gpu_override: Optional[str] = None
    budget_override_usd: Optional[float] = Field(default=None, ge=0.0, le=1000.0)
    simulate_interrupt: bool = False


class MediaResumeRequest(BaseModel):
    session_id: str = Field(min_length=4, max_length=64)
    tile_size: Optional[int] = Field(default=None, ge=128, le=2048)
    overlap: Optional[int] = Field(default=None, ge=0, le=256)


class TopazHandoffRequest(BaseModel):
    session_id: str = Field(min_length=4, max_length=64)
    tool: str = Field(default="Topaz Photo AI 4.1.0 [SIM]")


class TelegramSimCommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=128)


class TelegramCallbackRequest(BaseModel):
    action: str = Field(min_length=1, max_length=64)


class ProcessEventSimRequest(BaseModel):
    process_name: str = Field(min_length=1, max_length=80)
    action: str = Field(default="start")  # start | stop


class EdaSimRunRequest(BaseModel):
    template_id: Optional[str] = Field(default="esp32_i2c_sensor")
    spec: Optional[DesignSpec] = None
    run_z3_pinout: bool = True
    force_z3_conflict: bool = False


class SecretScanSimRequest(BaseModel):
    sample_text: str = Field(min_length=1, max_length=4000)


class PlatformSimulator:
    """Vollstaendige, lokale End-to-End-Simulation aller Plattform-Workflows."""

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
                "pending_confirmation": None,
                "scheduled_power_action": None,
            },
            "checkpoints": {
                "verified": False,
                "models": [
                    {"name": "Real-ESRGAN [SIM]", "sha256": "a1b2c3d4e5f6" + ("0" * 52), "license": "BSD-3-Clause"},
                    {"name": "SwinIR [SIM]", "sha256": "b2c3d4e5f6a1" + ("1" * 52), "license": "Apache-2.0"},
                    {"name": "Topaz Photo AI [SIM]", "sha256": "c3d4e5f6a1b2" + ("2" * 52), "license": "Commercial EULA (Desktop Handoff)"},
                ],
            },
            "topaz": {
                "licensed_handoff_ready": False,
                "detected_apps": ["Topaz Photo AI 4.1.0 [SIM]", "Topaz Gigapixel AI 8.4.4 [SIM]"],
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
                "text": "Simulator gestartet. Klicke auf 'Alle Accounts simulieren' oder jede Status-Pille einzeln.",
            }
        ]
        self.audit_log: List[str] = [
            f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] [INIT] ProcessMonitor Watchlist aktiv (13 Zielprozesse)"
        ]
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.eda_runs: Dict[str, Dict[str, Any]] = {}
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
            if len(self.telegram_feed) > 80:
                self.telegram_feed = self.telegram_feed[-80:]

    def get_state(self) -> Dict[str, Any]:
        return {
            "mode": "SIMULATION_SANDBOX",
            "gpu_catalog": GPU_CATALOG,
            "accounts": self.accounts,
            "telegram_feed": self.telegram_feed[-25:],
            "audit_log_tail": self.audit_log[-10:],
            "sessions": [
                {
                    "session_id": sid,
                    "media_type": s["media_type"],
                    "filename": s["filename"],
                    "status": s["status"],
                    "size_bytes": s["size_bytes"],
                    "original_sha256": s["original_sha256"],
                    "topaz_status": s.get("topaz_status", "idle"),
                }
                for sid, s in self.sessions.items()
            ],
            "eda_runs": [
                {
                    "run_id": rid,
                    "template_id": r["template_id"],
                    "pipeline_status": r["pipeline_status"],
                    "manifest_sha256": r["manifest_sha256"],
                }
                for rid, r in self.eda_runs.items()
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
                    "🟢 [SIM] TerraRemoteEngine verbunden. Watchlist (EDA + ART + Topaz) & Touch-Dashboard aktiv."
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
                    im.convert("RGB").save(sdir / "preview_original.png", format="PNG")
            except Exception as exc:
                orig_path.unlink(missing_ok=True)
                raise HTTPException(400, f"Bilddatei konnte nicht gelesen werden: {exc}")
        else:
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
            "tile_overlay_path": None,
            "original_sha256": orig_sha,
            "size_bytes": len(raw_bytes),
            "metadata": metadata,
            "status": "uploaded",
            "topaz_status": "idle",
            "topaz_handoff": None,
            "resume_state": None,
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
        w, h = 640, 420
        rng = np.random.default_rng(42)
        y_grid, x_grid = np.mgrid[0:h, 0:w]
        r = np.clip(45 + (y_grid / h) * 185 + 25 * np.sin(x_grid / 60.0), 0, 255)
        g = np.clip(35 + (y_grid / h) * 115 + 20 * np.cos(y_grid / 50.0), 0, 255)
        b = np.clip(90 + (1.0 - y_grid / h) * 120, 0, 255)
        arr = np.stack([r, g, b], axis=-1)
        noisy = np.clip(arr + rng.normal(0, 14, size=arr.shape), 0, 255).astype(np.uint8)

        img = Image.fromarray(noisy, mode="RGB")
        draw = ImageDraw.Draw(img)
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

    def build_tile_overlay(self, session_id: str, tile_size: int = 256, overlap: int = 32) -> Dict[str, Any]:
        sdir = self._session_dir(session_id)
        session = self.sessions[session_id]
        if session["media_type"] != "photo":
            raise HTTPException(400, "Kachel-Overlay ist nur fuer Fotos verfuegbar")

        w, h = session["metadata"]["width"], session["metadata"]["height"]
        tiles = plan_tiles(w, h, tile_size, overlap)
        with Image.open(session["original_path"]) as im:
            canvas = im.convert("RGB")
            draw = ImageDraw.Draw(canvas)
            for t in tiles:
                draw.rectangle(
                    (t.x, t.y, t.x + t.width - 1, t.y + t.height - 1),
                    outline=(56, 189, 248),
                    width=2,
                )
                draw.text((t.x + 6, t.y + 6), f"T{t.index} ({t.width}x{t.height})", fill=(250, 204, 21))
            overlay_path = sdir / "tile_overlay.png"
            canvas.save(overlay_path, format="PNG")

        session["tile_overlay_path"] = str(overlay_path)
        return {
            "session_id": session_id,
            "tile_size": tile_size,
            "overlap": overlap,
            "tile_count": len(tiles),
            "tiles": [t.__dict__ for t in tiles],
            "overlay_url": f"/sim/media/{session_id}/tile-overlay",
        }

    def process_media(self, req: MediaProcessRequest, *, resuming_from: Optional[int] = None) -> Dict[str, Any]:
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

        if gpu_key != "LOCAL_CPU_8GB" and not self.accounts["runpod"]["connected"]:
            raise HTTPException(
                409,
                "Simulierter RunPod.io-Account ist nicht verbunden. Bitte im Setup aktivieren.",
            )
        if not self.accounts["checkpoints"]["verified"] and "identity" not in req.model.lower():
            raise HTTPException(
                409,
                "Simulierte KI-Checkpoints sind nicht SHA-256-verifiziert. Bitte im Setup aktivieren.",
            )

        steps: List[Dict[str, Any]] = [
            {
                "stage": "1. Original-Integritaet (SHA-256)",
                "status": "PASS",
                "detail": f"Unveraenderliches Original verifiziert ({session['original_sha256'][:16]}...)",
            }
        ]

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
            w, h = session["metadata"]["width"], session["metadata"]["height"]
            tiles = plan_tiles(w, h, req.tile_size, req.overlap)
            config = {
                "source_sha256": session["original_sha256"],
                "tile_size": req.tile_size,
                "overlap": req.overlap,
                "model": req.model,
                "mode": req.mode,
                "quality_profile": req.quality_profile,
                "gpu": gpu_key,
            }
            cfg_hash = compute_config_hash(config)

            est_runtime_s = max(4.0, len(tiles) * (2.5 if req.mode == "upscale_2x" else 1.4))
            est_cost = estimate_compute_cost(est_runtime_s, gpu_info["hourly_usd"])
            budget_check = enforce_budget_gate(est_cost, budget)

            steps.append(
                {
                    "stage": "3. RunPod.io Budget- & Cost-Gate [SIM]",
                    "status": "PASS" if budget_check["allowed"] else "BLOCKED",
                    "detail": f"GPU={gpu_info['label']}, Geschaetzt=${est_cost:.6f} / Budget=${budget:.4f}",
                }
            )
            if not budget_check["allowed"]:
                raise HTTPException(402, f"Budget-Gate gestoppt (Quality-First): {budget_check['reason']}")

            # Simulierte Unterbrechung nach der Haelfte der Kacheln zum Testen von atomarem Resume!
            if req.simulate_interrupt and len(tiles) >= 1:
                done_count = max(1, len(tiles) // 2)
                resume_state = {
                    "status": "interrupted",
                    "config": config,
                    "config_sha256": cfg_hash,
                    "tiles_total": len(tiles),
                    "tiles_completed": done_count,
                    "request": req.model_dump(),
                    "interrupted_at_utc": datetime.now(timezone.utc).isoformat(),
                }
                (sdir / "resume_state.json").write_text(
                    json.dumps(resume_state, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                session["resume_state"] = resume_state
                session["status"] = "interrupted"
                steps.append(
                    {
                        "stage": "4. Kachel-Unterbrechung (Resume-Checkpoint gespeichert)",
                        "status": "INTERRUPTED",
                        "detail": (
                            f"Nach {done_count}/{len(tiles)} Kacheln sicher pausiert. "
                            f"Config-Hash gesperrt ({cfg_hash[:12]}...) – jetzt mit 'Resume fortsetzen' weitermachen!"
                        ),
                    }
                )
                self._push_telegram_alert(
                    f"⚠️ [SIM] Job pausiert nach {done_count}/{len(tiles)} Kacheln ({session['filename']}). Resume bereit."
                )
                return {
                    "session_id": req.session_id,
                    "media_type": "photo",
                    "status": "interrupted",
                    "tiles_completed": done_count,
                    "tiles_total": len(tiles),
                    "config_sha256": cfg_hash,
                    "steps": steps,
                }

            with Image.open(session["original_path"]) as src_im:
                src_rgb = src_im.convert("RGB")
                if req.mode == "upscale_2x":
                    scale = 2 if (w * h <= 2_500_000) else 1
                    worked = src_rgb.resize((w * scale, h * scale), Image.Resampling.LANCZOS)
                    worked = worked.filter(ImageFilter.UnsharpMask(radius=1.4, percent=135, threshold=2))
                    worked = ImageEnhance.Contrast(worked).enhance(1.08)
                elif req.mode == "archival_denoise":
                    worked = src_rgb.filter(ImageFilter.MedianFilter(size=3))
                    worked = worked.filter(ImageFilter.UnsharpMask(radius=1.1, percent=110, threshold=3))
                    worked = ImageEnhance.Color(worked).enhance(1.04)
                else:
                    worked = src_rgb.filter(ImageFilter.MedianFilter(size=3))
                    worked = worked.filter(ImageFilter.UnsharpMask(radius=1.5, percent=155, threshold=2))
                    worked = ImageEnhance.Contrast(worked).enhance(1.12)
                    worked = ImageEnhance.Color(worked).enhance(1.10)

                result_path = sdir / "simulated_restored.png"
                worked.save(result_path, format="PNG")
                out_w, out_h = worked.size

                src_arr = np.asarray(src_rgb, dtype=np.float32)
                cmp_img = worked.resize((w, h), Image.Resampling.LANCZOS) if (out_w, out_h) != (w, h) else worked
                dst_arr = np.asarray(cmp_img, dtype=np.float32)
                mse = float(np.mean((src_arr - dst_arr) ** 2))
                psnr = float("inf") if mse == 0 else round(float(10.0 * np.log10((255.0 ** 2) / mse)), 2)

            result_sha = sha256_file(result_path)
            resumed_note = (
                f" (nahtlos fortgesetzt ab Kachel {resuming_from}/{len(tiles)})"
                if resuming_from is not None
                else ""
            )
            steps.append(
                {
                    "stage": "4. Kachel-Orchestrierung & Feather-Stitching [SIM]",
                    "status": "PASS",
                    "detail": f"{len(tiles)} Kachel(n) mit {req.model} verarbeitet ({out_w}x{out_h}px){resumed_note}",
                }
            )
            quality_metrics = {
                "source_dimensions": [w, h],
                "output_dimensions": [out_w, out_h],
                "tiles_processed": len(tiles),
                "resumed_from_tile": resuming_from,
                "mse_after_downsample": round(mse, 3),
                "psnr_db": psnr if np.isfinite(psnr) else "infinity",
                "simulation_notice": "Sichtbare lokale Simulations-Vorschau (kein echtes neuronales KI-Modell).",
            }
            session["resume_state"] = None
        else:
            est_frames = int(session["metadata"]["estimated_frames"])
            est_runtime_s = max(12.0, est_frames * (0.12 if req.mode == "upscale_2x" else 0.06))
            est_cost = estimate_compute_cost(est_runtime_s, gpu_info["hourly_usd"])
            budget_check = enforce_budget_gate(est_cost, budget)

            steps.append(
                {
                    "stage": "3. RunPod.io Video-Batch Budget-Gate [SIM]",
                    "status": "PASS" if budget_check["allowed"] else "BLOCKED",
                    "detail": f"{est_frames} Frames auf {gpu_info['label']} -> ${est_cost:.6f} / ${budget:.4f}",
                }
            )
            if not budget_check["allowed"]:
                raise HTTPException(402, f"Budget-Gate gestoppt (Quality-First): {budget_check['reason']}")

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
            draw.text((410, 198), "[SIMULATION - LIVE-FILTER IM PLAYER]", fill=(250, 204, 21))
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
                "simulation_notice": "Video-Pipeline simuliert; Live-Filter im Video-Player umschaltbar.",
            }

        self.accounts["runpod"]["simulated_spend_usd"] = round(
            self.accounts["runpod"]["simulated_spend_usd"] + est_cost, 6
        )

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
        (sdir / "provenance_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
        steps.append(
            {
                "stage": "5. SHA-256 Provenienz & Telegram-Benachrichtigung",
                "status": "PASS",
                "detail": f"Manifest-SHA256: {manifest['manifest_sha256'][:16]}... ({duration_ms} ms)",
            }
        )

        self._push_telegram_alert(
            f"✅ [SIM] {session['media_type'].upper()}-Job fertig: {session['filename']} | "
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

    def resume_media(self, req: MediaResumeRequest) -> Dict[str, Any]:
        self._session_dir(req.session_id)
        session = self.sessions[req.session_id]
        state = session.get("resume_state")
        if not state or state.get("status") != "interrupted":
            raise HTTPException(409, "Kein pausierter Job zum Fortsetzen vorhanden")

        saved_req = dict(state["request"])
        saved_req["simulate_interrupt"] = False

        # Config-Hash-Pruefung (Fail-Closed bei geaenderter Kachelkonfiguration!)
        check_cfg = dict(state["config"])
        if req.tile_size is not None:
            check_cfg["tile_size"] = req.tile_size
        if req.overlap is not None:
            check_cfg["overlap"] = req.overlap
        if compute_config_hash(check_cfg) != state["config_sha256"]:
            raise HTTPException(
                409,
                "Config-Hash weicht vom pausierten Job ab! Resume mit geaenderten Parametern ist fail-closed gesperrt.",
            )

        return self.process_media(
            MediaProcessRequest(**saved_req),
            resuming_from=int(state["tiles_completed"]),
        )

    def topaz_export_sim(self, req: TopazHandoffRequest) -> Dict[str, Any]:
        sdir = self._session_dir(req.session_id)
        session = self.sessions[req.session_id]
        if not self.accounts["topaz"]["licensed_handoff_ready"]:
            raise HTTPException(409, "Topaz Handoff ist im Setup-Assistenten noch deaktiviert.")

        # Re-Verify Original SHA-256 vor Export (wie in echtem app/topaz.py!)
        current_sha = sha256_file(Path(session["original_path"]))
        if current_sha != session["original_sha256"]:
            raise HTTPException(409, "Original-SHA-256 stimmt nicht ueberein; Export abgebrochen.")

        handoff_dir = sdir / "topaz_handoff"
        handoff_dir.mkdir(exist_ok=True)
        copy_path = handoff_dir / f"working_copy{session['suffix']}"
        copy_path.write_bytes(Path(session["original_path"]).read_bytes())

        manifest = {
            "handoff_id": f"TOPAZ-SIM-{uuid.uuid4().hex[:8].upper()}",
            "tool": req.tool,
            "source_sha256": current_sha,
            "working_copy_sha256": sha256_file(copy_path),
            "exported_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "exported_waiting_import",
            "policy": "Original bleibt unveraendert; Ergebnis wird separat re-importiert und gehasht.",
        }
        (handoff_dir / "handoff_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        session["topaz_handoff"] = manifest
        session["topaz_status"] = "exported"
        self._push_telegram_alert(
            f"📤 [SIM] Topaz-Handoff exportiert ({req.tool}) | SHA-256: {current_sha[:12]}..."
        )
        return manifest

    def topaz_import_sim(self, req: TopazHandoffRequest) -> Dict[str, Any]:
        sdir = self._session_dir(req.session_id)
        session = self.sessions[req.session_id]
        if not session.get("topaz_handoff"):
            raise HTTPException(409, "Zuerst Topaz-Handoff exportieren, bevor ein Ergebnis importiert wird.")

        # Simuliere das re-importierte Topaz-Ergebnis und verifiziere Provenienz
        if session["media_type"] == "photo":
            with Image.open(session["original_path"]) as im:
                enhanced = im.convert("RGB").filter(
                    ImageFilter.UnsharpMask(radius=1.8, percent=170, threshold=2)
                )
                enhanced = ImageEnhance.Contrast(enhanced).enhance(1.14)
                imported_path = sdir / "topaz_imported_result.png"
                enhanced.save(imported_path, format="PNG")
        else:
            imported_path = sdir / "topaz_imported_result.png"
            card = Image.new("RGB", (640, 180), (15, 23, 42))
            draw = ImageDraw.Draw(card)
            draw.text((24, 30), f"TOPAZ VIDEO IMPORT VERIFIZIERT [SIM]: {req.tool}", fill=(34, 197, 94))
            draw.text((24, 75), f"Quelle: {session['filename']}", fill=(226, 232, 240))
            draw.text((24, 115), f"Original-SHA256: {session['original_sha256'][:24]}...", fill=(148, 163, 184))
            card.save(imported_path, format="PNG")

        imported_sha = sha256_file(imported_path)
        record = {
            **session["topaz_handoff"],
            "status": "imported_verified",
            "imported_at_utc": datetime.now(timezone.utc).isoformat(),
            "imported_sha256": imported_sha,
            "result_url": f"/sim/media/{req.session_id}/result",
        }
        session["result_path"] = str(imported_path)
        session["topaz_handoff"] = record
        session["topaz_status"] = "imported_verified"
        self._push_telegram_alert(
            f"📥 [SIM] Topaz-Ergebnis importiert & SHA-256 verifiziert ({imported_sha[:12]}...)"
        )
        return record

    def run_telegram_command(self, raw_cmd: str) -> Dict[str, Any]:
        cmd = raw_cmd.strip()
        tg = self.accounts["telegram"]
        if not tg["connected"]:
            raise HTTPException(409, "Telegram-Bot ist noch nicht verbunden. Bitte im Setup aktivieren.")

        self.telegram_feed.append(
            {"timestamp": datetime.now(timezone.utc).isoformat(), "sender": "user", "text": cmd}
        )

        parts = cmd.split()
        verb = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        arg_low = arg.lower()

        if verb == "/menu":
            reply = "🎛 [SIM] TERRA REMOTE DASHBOARD geoeffnet – nutze die Touch-Buttons unten!"
        elif verb == "/status":
            reply = (
                f"🟢 [SIM] TerraRemoteEngine v1.2.0 aktiv.\n"
                f"📡 Push-Alerts: {'aktiviert' if tg['alerts_enabled'] else 'pausiert'}\n"
                f"☁️ RunPod-Sim: {'verbunden (' + self.accounts['runpod']['gpu'] + ')' if self.accounts['runpod']['connected'] else 'getrennt'}\n"
                f"🔒 Screen: {tg['allow_remote_screen_capture']} | Kill: {tg['allow_remote_process_termination']}"
            )
        elif verb == "/stats":
            reply = (
                "🖥 [SIM] HARDWARE TELEMETRIE\n"
                "⚙️ CPU: 18.4% | 🧠 RAM: 4120 / 7998 MB (52%)\n"
                "🔌 Strom: Netzbetrieb | Cargo-Schutz: --jobs 2"
            )
        elif verb == "/ps":
            reply = (
                "📋 [SIM] TOP PROZESSE (WATCHLIST)\n"
                "PID 4102 | 312MB | python.exe (Gateway)\n"
                "PID 5810 | 148MB | eda-cad-studio.exe\n"
                "PID 6120 |  19MB | rust-pc-remote.exe\n"
                f"Beenden: {'FREIGEGEBEN' if tg['allow_remote_process_termination'] else 'GESPERRT (ALLOW_REMOTE_PROCESS_TERMINATION=false)'}"
            )
        elif verb in ("/tail", "/getlog"):
            if arg_low in ("audit", "debug", "eda", "art"):
                if arg_low == "audit":
                    reply = "📄 [SIM] process_audit.log:\n" + "\n".join(self.audit_log[-6:])
                elif arg_low == "debug":
                    reply = "📄 [SIM] bot_debug.log:\n[INFO] TerraRemoteEngine v1.2.0 Dispatcher running\n[INFO] Watchlist initialized (13 entries)"
                else:
                    log_path = (
                        resolve_eda_log_dir() / "eda_pipeline.log"
                        if arg_low == "eda"
                        else resolve_art_log_dir() / "art_pipeline.log"
                    )
                    if log_path.is_file():
                        lines = log_path.read_text(encoding="utf-8").splitlines()[-6:]
                        reply = f"📄 [SIM] {log_path.name}:\n" + ("\n".join(lines) or "(leer)")
                    else:
                        reply = f"ℹ️ [SIM] '{log_path.name}' ist noch leer (starte zuerst einen EDA- oder ART-Lauf)."
            else:
                # Echte Path-Traversal / .env-Sicherheitspruefung demonstrieren!
                try:
                    validate_safe_path(arg or ".env", allowed_extensions={".log", ".jsonl", ".txt"})
                    reply = "⚠️ Ungueltiges Logfile! Nutzung: /tail audit | debug | eda | art"
                except ValueError as exc:
                    reply = f"🛡️ [FAIL-CLOSED BLOCKIERT] {exc}"
        elif verb == "/alerts" and arg_low in ("on", "off"):
            tg["alerts_enabled"] = arg_low == "on"
            reply = "🔔 [SIM] Push-Alerts AKTIVIERT." if tg["alerts_enabled"] else "🔕 [SIM] Push-Alerts PAUSIERT."
        elif verb == "/screen":
            if not tg["allow_remote_screen_capture"]:
                reply = "🔒 [FAIL-CLOSED] Fern-Screenshots sind gesperrt (ALLOW_REMOTE_SCREEN_CAPTURE=false)."
            else:
                reply = "📸 [SIM] Fern-Screenshot freigegeben – simuliertes PNG-Livebild erstellt."
        elif verb == "/kill":
            if not tg["allow_remote_process_termination"]:
                reply = "🔒 [FAIL-CLOSED] Remote-Prozessbeendigung ist gesperrt (ALLOW_REMOTE_PROCESS_TERMINATION=false)."
            else:
                reply = f"🎯 [SIM] Prozess-Signal fuer PID {arg or '?'} simuliert (kein echter Prozess beendet)."
        elif verb in ("/shutdown", "/reboot"):
            reply = "⚠️ [2-STUFEN-SCHUTZ] Bitte im Touch-Dashboard unten ausloesen und explizit bestaetigen."
        elif verb == "/abort":
            if tg["scheduled_power_action"]:
                prev = tg["scheduled_power_action"]
                tg["scheduled_power_action"] = None
                reply = f"✅ [SIM] Geplantes '{prev}' erfolgreich abgebrochen."
            else:
                reply = "ℹ️ [SIM] Kein geplantes Herunterfahren/Neustarten aktiv."
        else:
            reply = "ℹ️ Befehle: /menu, /status, /stats, /ps, /tail audit|debug|eda|art, /alerts on|off, /screen, /kill 1234, /abort"

        self.telegram_feed.append(
            {"timestamp": datetime.now(timezone.utc).isoformat(), "sender": "bot", "text": reply}
        )
        return {"command": cmd, "reply": reply, "telegram_feed": self.telegram_feed[-25:]}

    def handle_telegram_callback(self, action: str) -> Dict[str, Any]:
        tg = self.accounts["telegram"]
        if not tg["connected"]:
            raise HTTPException(409, "Telegram-Bot ist noch nicht verbunden.")

        if action == "act_stats":
            return self.run_telegram_command("/stats")
        if action == "act_screen":
            return self.run_telegram_command("/screen")
        if action == "act_lock":
            reply = "🔒 [SIM] LockWorkStation() an Windows uebergeben (Simulator: Bildschirm bleibt entsperrt)."
        elif action == "act_monoff":
            reply = "🌑 [SIM] Ghost-Mode (Display aus) simuliert."
        elif action == "act_sleep":
            reply = "💤 [SIM] Standby (SetSuspendState) simuliert."
        elif action == "ask_shutdown":
            tg["pending_confirmation"] = "shutdown"
            reply = "⚠️ [SICHERHEITSABFRAGE] Windows 11 in 30s herunterfahren? Klicke auf 'Ja, bestaetigen' oder 'Abbrechen'."
        elif action == "ask_reboot":
            tg["pending_confirmation"] = "reboot"
            reply = "⚠️ [SICHERHEITSABFRAGE] System in 30s neu starten? Klicke auf 'Ja, bestaetigen' oder 'Abbrechen'."
        elif action == "confirm_power":
            pending = tg["pending_confirmation"]
            if not pending:
                reply = "ℹ️ Keine ausstehende Sicherheitsabfrage."
            else:
                tg["scheduled_power_action"] = pending
                tg["pending_confirmation"] = None
                reply = f"🛑 [SIM] {pending.upper()} fuer 30s geplant (nur Simulation). Nutze /abort zum Abbrechen."
        elif action == "act_cancel":
            tg["pending_confirmation"] = None
            reply = "❌ [SIM] Sicherheitsabfrage abgebrochen – zurueck im Dashboard."
        else:
            reply = f"ℹ️ [SIM] Touch-Aktion '{action}' verarbeitet."

        self.telegram_feed.append(
            {"timestamp": datetime.now(timezone.utc).isoformat(), "sender": "bot", "text": reply}
        )
        return {
            "action": action,
            "reply": reply,
            "pending_confirmation": tg["pending_confirmation"],
            "telegram_feed": self.telegram_feed[-25:],
        }

    def simulate_process_event(self, req: ProcessEventSimRequest) -> Dict[str, Any]:
        pname = req.process_name.strip()
        is_priority = pname.lower() in DEFAULT_WATCHLIST
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        pid = 7000 + (abs(hash(pname)) % 1999)
        tag = "START" if req.action.lower() == "start" else "STOP"
        line = f"[{tag}] PID: {pid} | Programm: {pname} | Zeit: {ts} | Priority: {is_priority}"
        self.audit_log.append(line)

        if is_priority:
            icon = "⚠️ ALERT: Kritischer Prozess gestartet!" if tag == "START" else "ℹ️ Prozess beendet:"
            self._push_telegram_alert(f"{icon}\nProgramm: {pname}\nPID: {pid}\nZeit: {ts}")

        return {
            "event": line,
            "is_priority": is_priority,
            "audit_log_tail": self.audit_log[-10:],
            "telegram_feed": self.telegram_feed[-25:],
        }

    def run_eda_simulation(self, req: EdaSimRunRequest) -> Dict[str, Any]:
        started = time.perf_counter()
        if req.spec is not None:
            try:
                board = synthesize_board(req.spec)
            except ValueError as exc:
                raise HTTPException(422, str(exc))
            label = req.spec.name
        else:
            tpl = req.template_id or "esp32_i2c_sensor"
            try:
                board = build_template_board(tpl)
            except ValueError as exc:
                raise HTTPException(422, str(exc))
            label = tpl

        # Echter Z3-Pinout-Multiplex-Solver (SAT oder gezielter UNSAT-Konflikttest!)
        mcu = None
        requirements: List[PinRequirement] = []
        if req.run_z3_pinout:
            if req.force_z3_conflict:
                mcu = MCUPackage(name="ESP32-Conflict", pin_capabilities={1: ["SDA", "SCL"]})
            else:
                mcu = MCUPackage(
                    name="ESP32-WROOM-32E",
                    pin_capabilities={
                        21: ["SDA", "GPIO"],
                        22: ["SCL", "GPIO"],
                        17: ["UART_TX", "GPIO"],
                        16: ["UART_RX", "GPIO"],
                    },
                )
            requirements = [
                PinRequirement(function_name="SDA", signal_type="I2C"),
                PinRequirement(function_name="SCL", signal_type="I2C"),
            ]

        pipeline = DesignPipeline(mcu=mcu, requirements=requirements)
        result = pipeline.run(board)

        kicad_pcb = export_kicad_pcb(result.board)
        step_data = generate_step(result.board)
        scad_data = generate_openscad_case(result.board)
        bom_csv = generate_jlcpcb_bom(result.board)
        cpl_csv = generate_jlcpcb_cpl(result.board)
        gerbers = export_gerber_bundle(result.board)

        # In-Memory ZIP fuer die 4 Gerber/Excellon-Dateien
        zip_buf = BytesIO()
        with zipfile.ZipFile(zip_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for gname, gcontent in gerbers.items():
                zf.writestr(gname, gcontent)
        gerber_zip_bytes = zip_buf.getvalue()

        all_artifacts = {
            "board.kicad_pcb": kicad_pcb,
            "board.step": step_data,
            "case.scad": scad_data,
            "bom.csv": bom_csv,
            "cpl.csv": cpl_csv,
            **gerbers,
        }
        manifest = build_export_manifest(all_artifacts, summarize_board(result.board))
        manifest_json = json.dumps(manifest, ensure_ascii=False, indent=2)
        duration_ms = round((time.perf_counter() - started) * 1000.0, 2)

        run_id = f"eda-sim-{uuid.uuid4().hex[:8]}"
        svg_markup = render_board_svg(result.board)

        self.eda_runs[run_id] = {
            "run_id": run_id,
            "template_id": label,
            "pipeline_status": result.status,
            "manifest_sha256": manifest["manifest_sha256"],
            "downloads": {
                "board.kicad_pcb": (kicad_pcb.encode("utf-8"), "text/plain; charset=utf-8"),
                "board.step": (step_data.encode("utf-8"), "application/step"),
                "case.scad": (scad_data.encode("utf-8"), "application/x-openscad"),
                "bom.csv": (bom_csv.encode("utf-8"), "text/csv; charset=utf-8"),
                "cpl.csv": (cpl_csv.encode("utf-8"), "text/csv; charset=utf-8"),
                "gerbers.zip": (gerber_zip_bytes, "application/zip"),
                "export_manifest.json": (manifest_json.encode("utf-8"), "application/json"),
            },
        }

        kicad_sim_ok = self.accounts["kicad_cli"]["simulated_ready"]
        self._push_telegram_alert(
            f"🔧 [SIM] EDA-Lauf '{label}': {result.status} | "
            f"Z3-Pinout: {result.pinout or 'UNSAT'} | Traces: {len(result.board.traces)} | "
            f"DRC: {len(result.board.errors)} | Manifest: {manifest['manifest_sha256'][:12]}..."
        )
        return {
            "run_id": run_id,
            "template_id": label,
            "pipeline_status": result.status,
            "pinout": result.pinout,
            "attempts_used": result.attempts_used,
            "duration_ms": duration_ms,
            "board_summary": manifest["board_summary"],
            "manifest_sha256": manifest["manifest_sha256"],
            "gerber_files": sorted(gerbers.keys()),
            "download_urls": {
                name: f"/sim/eda/{run_id}/download/{name}"
                for name in self.eda_runs[run_id]["downloads"].keys()
            },
            "svg": svg_markup,
            "kicad_cli_signoff": (
                "PASS [SIM kicad-cli 8.0.6-sim: 0 DRC / 0 Unconnected]"
                if (kicad_sim_ok and result.status == "OK")
                else ("FAIL [SIM kicad-cli: Pipeline nicht OK]" if kicad_sim_ok else "SKIPPED (im Setup aktivierbar)")
            ),
            "report": result.report,
        }

    def run_secret_scan_sim(self, text: str) -> Dict[str, Any]:
        hits = scan_text_for_secrets(text)
        redacted = sanitize_payload({"input": text})["input"]
        return {
            "secret_types_detected": hits,
            "clean": len(hits) == 0,
            "redacted_preview": redacted,
        }

    def run_full_auto_demo(self) -> Dict[str, Any]:
        """Fuehrt einen kompletten End-to-End-Probelauf ueber ALLE Plattform-Subsysteme aus."""
        self.configure_accounts(AccountSetupRequest(activate_all=True, runpod_budget_usd=5.0))
        demo_photo = self.create_demo_photo()
        sid = demo_photo["session_id"]
        tile_info = self.build_tile_overlay(sid, tile_size=256, overlap=32)
        media_result = self.process_media(
            MediaProcessRequest(session_id=sid, mode="restoration_enhance", tile_size=256, overlap=32)
        )
        eda_result = self.run_eda_simulation(EdaSimRunRequest(template_id="esp32_i2c_sensor"))
        self.simulate_process_event(ProcessEventSimRequest(process_name="eda-cad-studio.exe", action="start"))
        return {
            "status": "full_auto_demo_completed",
            "state": self.get_state(),
            "photo_session": demo_photo,
            "tile_overlay": tile_info,
            "media_result": media_result,
            "eda_result": eda_result,
        }


SIMULATOR = PlatformSimulator()
sim_router = APIRouter(prefix="/sim", tags=["Simulator"])


@sim_router.get("/state")
def sim_state() -> JSONResponse:
    return JSONResponse(content=SIMULATOR.get_state())


@sim_router.post("/reset")
def sim_reset() -> JSONResponse:
    return JSONResponse(content=SIMULATOR.reset())


@sim_router.post("/auto-demo")
def sim_auto_demo() -> JSONResponse:
    return JSONResponse(content=SIMULATOR.run_full_auto_demo())


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


@sim_router.post("/media/{session_id}/tile-plan")
def sim_tile_plan(session_id: str, tile_size: int = 256, overlap: int = 32) -> JSONResponse:
    return JSONResponse(content=SIMULATOR.build_tile_overlay(session_id, tile_size=tile_size, overlap=overlap))


@sim_router.get("/media/{session_id}/tile-overlay")
def sim_get_tile_overlay(session_id: str) -> FileResponse:
    SIMULATOR._session_dir(session_id)
    path = SIMULATOR.sessions[session_id].get("tile_overlay_path")
    if not path or not Path(path).is_file():
        raise HTTPException(404, "Noch kein Kachel-Overlay erzeugt")
    return FileResponse(Path(path), media_type="image/png")


@sim_router.post("/media/process")
def sim_process_media(req: MediaProcessRequest) -> JSONResponse:
    return JSONResponse(content=SIMULATOR.process_media(req))


@sim_router.post("/media/resume")
def sim_resume_media(req: MediaResumeRequest) -> JSONResponse:
    return JSONResponse(content=SIMULATOR.resume_media(req))


@sim_router.post("/topaz/export")
def sim_topaz_export(req: TopazHandoffRequest) -> JSONResponse:
    return JSONResponse(content=SIMULATOR.topaz_export_sim(req))


@sim_router.post("/topaz/import")
def sim_topaz_import(req: TopazHandoffRequest) -> JSONResponse:
    return JSONResponse(content=SIMULATOR.topaz_import_sim(req))


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


@sim_router.post("/telegram/callback")
def sim_telegram_callback(req: TelegramCallbackRequest) -> JSONResponse:
    return JSONResponse(content=SIMULATOR.handle_telegram_callback(req.action))


@sim_router.post("/telegram/process-event")
def sim_process_event(req: ProcessEventSimRequest) -> JSONResponse:
    return JSONResponse(content=SIMULATOR.simulate_process_event(req))


@sim_router.post("/eda/run")
def sim_eda_run(req: EdaSimRunRequest) -> JSONResponse:
    return JSONResponse(content=SIMULATOR.run_eda_simulation(req))


@sim_router.get("/eda/{run_id}/download/{artifact_name}")
def sim_eda_download(run_id: str, artifact_name: str) -> Response:
    run = SIMULATOR.eda_runs.get(run_id)
    if not run or artifact_name not in run["downloads"]:
        raise HTTPException(404, f"Artefakt '{artifact_name}' nicht gefunden")
    raw_bytes, media_type = run["downloads"][artifact_name]
    return Response(
        content=raw_bytes,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{artifact_name}"'},
    )


@sim_router.get("/eda/templates")
def sim_eda_templates() -> JSONResponse:
    return JSONResponse(content={"templates": list_templates()})


@sim_router.post("/qa/secret-scan")
def sim_secret_scan(req: SecretScanSimRequest) -> JSONResponse:
    return JSONResponse(content=SIMULATOR.run_secret_scan_sim(req.sample_text))
