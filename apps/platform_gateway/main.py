"""Unified Deterministic Studio – Lokales Control-Hub & Gateway (127.0.0.1:8080).

Mountet beide Domaenen-APIs sauber unter einem gemeinsamen lokalen Einstiegspunkt:
- /eda/*  -> apps/eda-multiagent-core (FastAPI + WebSocket + Three.js CAD Viewer)
- /art/*  -> apps/art-restoration-ai (Quality-First Media Restoration Control Plane)
- /health -> Aggregierter Plattform-Status
- /api/overview -> Ressourcenplan, EDA-Templates, Checkpoint-Status & Telemetrie-Vorschau
- /       -> Zero-Dependency Cockpit UI
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

REPO_ROOT = Path(__file__).resolve().parents[2]
for extra_path in (
    REPO_ROOT / "packages" / "platform_core",
    REPO_ROOT / "packages" / "platform_mcp",
    REPO_ROOT / "apps" / "eda-multiagent-core",
    REPO_ROOT / "apps" / "art-restoration-ai",
):
    p_str = str(extra_path)
    if extra_path.is_dir() and p_str not in sys.path:
        sys.path.insert(0, p_str)

from fastapi import Body
from platform_core.resource_guard import plan_resources
from platform_core.telemetry import sanitize_payload
from platform_mcp.server import handle_jsonrpc_request, list_mcp_tools

from core.board_synthesizer import list_templates
from core.gerber_export import detect_kicad_cli
from core.telemetry_log import resolve_log_dir as resolve_eda_log_dir
from server.server import app as eda_app

from app.main import app as art_app
from app.telemetry import resolve_art_log_dir
from restoration.checkpoints import inspect_checkpoints_dir
from simulator import sim_router

gateway = FastAPI(
    title="Unified Deterministic Studio – Platform Gateway",
    version="1.0.0",
    description="Lokales Control-Hub fuer EDA Multi-Agent Core, ART Restoration AI und Interaktiven Simulator (127.0.0.1).",
)

gateway.include_router(sim_router)
gateway.mount("/eda", eda_app)
gateway.mount("/art", art_app)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _tail_jsonl(path: Path, limit: int = 5) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    records: List[Dict[str, Any]] = []
    for line in lines:
        try:
            records.append(sanitize_payload(json.loads(line)))
        except json.JSONDecodeError:
            continue
    return records


@gateway.get("/health")
def gateway_health() -> JSONResponse:
    return JSONResponse(
        content={
            "status": "ok",
            "service": "unified-deterministic-studio-gateway",
            "version": gateway.version,
            "mounts": {
                "eda": "/eda",
                "art": "/art",
            },
        }
    )


@gateway.get("/api/overview")
def gateway_overview(ram_gb: float = 8.0, vram_gb: float = 4.0) -> JSONResponse:
    resource_plan = plan_resources(
        ram_gb=ram_gb, vram_gb=vram_gb, quality_profile="deterministic_strict"
    ).__dict__
    eda_jsonl = resolve_eda_log_dir() / "eda_runs.jsonl"
    art_jsonl = resolve_art_log_dir() / "art_runs.jsonl"
    return JSONResponse(
        content={
            "platform": "Unified Deterministic Studio",
            "version": gateway.version,
            "bind_policy": "127.0.0.1 only (fail-closed)",
            "resource_plan": resource_plan,
            "eda": {
                "viewer_url": "/eda/",
                "docs_url": "/eda/docs",
                "templates": list_templates(),
                "kicad_cli": detect_kicad_cli(),
                "recent_runs": _tail_jsonl(eda_jsonl, limit=5),
            },
            "art": {
                "docs_url": "/art/docs",
                "checkpoints": inspect_checkpoints_dir(),
                "recent_runs": _tail_jsonl(art_jsonl, limit=5),
            },
        }
    )


@gateway.get("/", include_in_schema=False)
def cockpit_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@gateway.get("/simulator", include_in_schema=False)
def simulator_ui() -> FileResponse:
    return FileResponse(STATIC_DIR / "simulator.html")


@gateway.get("/mcp/tools")
def mcp_tools_list() -> JSONResponse:
    return JSONResponse(content={"tools": list_mcp_tools()})


@gateway.post("/mcp")
def mcp_jsonrpc_endpoint(payload: Dict[str, Any] = Body(...)) -> JSONResponse:
    return JSONResponse(content=handle_jsonrpc_request(payload))


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("PLATFORM_GATEWAY_HOST", "127.0.0.1")
    port = int(os.environ.get("PLATFORM_GATEWAY_PORT", "8080"))
    uvicorn.run(gateway, host=host, port=port)
