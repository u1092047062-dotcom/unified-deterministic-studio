"""Nativer Model Context Protocol (MCP) JSON-RPC 2.0 Server (stdio & HTTP).

Exponiert ausschliesslich deterministische, verifizierte Plattform-Werkzeuge:
- eda_list_templates
- eda_run_template (Routing + Spatial-DRC + RS-274X-Gerber-Verifikation + SHA-256-Manifest)
- eda_search_parts (lokaler JLCPCB/LCSC-Katalog)
- art_plan_tiles (deterministische Kachelgeometrie)
- art_resource_plan (Quality-First RAM/VRAM + Budget-Gate)
- platform_scan_secrets (Fail-Closed Secret-Scanner + Redaktion)
- platform_status (Backend-Erkennung: KiCad-CLI, pygerber, Spandrel, pyvips)
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, Callable, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[3]
for p in (
    REPO_ROOT / "packages" / "platform_core",
    REPO_ROOT / "apps" / "eda-multiagent-core",
    REPO_ROOT / "apps" / "art-restoration-ai",
):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

from platform_core.resource_guard import enforce_budget_gate, estimate_compute_cost, plan_resources
from platform_core.security import scan_text_for_secrets
from platform_core.telemetry import sanitize_payload

from core.assembly_collision_3d import check_3d_assembly_collisions
from core.board_synthesizer import build_template_board, list_templates
from core.em_thermal_sim import simulate_board_physics
from core.export_manifest import build_export_manifest, summarize_board
from core.gerber_export import detect_kicad_cli, export_gerber_bundle
from core.gerber_verifier import detect_pygerber, verify_and_render_gerbers
from core.hardware_as_code import compile_hac_to_board
from core.kicad_export import export_kicad_pcb
from core.part_library import DEFAULT_LIBRARY
from core.pipeline_v2 import DesignPipeline

from restoration.sota_router import select_sota_restoration_plan
from restoration.spandrel_adapter import spandrel_runtime_status
from restoration.streaming_io import detect_streaming_backend
from restoration.tiling import plan_tiles
from platform_mcp.a2a_orchestrator import run_closed_loop_pcb_agent

MCP_PROTOCOL_VERSION = "2024-11-05"


def _tool_eda_list_templates(_args: Dict[str, Any]) -> Dict[str, Any]:
    return {"templates": list_templates()}


def _tool_eda_run_template(args: Dict[str, Any]) -> Dict[str, Any]:
    template_id = str(args.get("template_id", "esp32_i2c_sensor"))
    board = build_template_board(template_id)
    result = DesignPipeline().run(board)
    gerbers = export_gerber_bundle(result.board)
    gerber_check = verify_and_render_gerbers(gerbers)
    manifest = build_export_manifest(
        {"board.kicad_pcb": export_kicad_pcb(result.board), **gerbers},
        summarize_board(result.board),
    )
    return {
        "template_id": template_id,
        "status": result.status,
        "attempts_used": result.attempts_used,
        "board_summary": manifest["board_summary"],
        "manifest_sha256": manifest["manifest_sha256"],
        "gerber_valid": gerber_check["valid"],
        "gerber_errors": gerber_check["error_count"],
    }


def _tool_eda_search_parts(args: Dict[str, Any]) -> Dict[str, Any]:
    q = str(args.get("query", ""))
    cat = args.get("category")
    limit = int(args.get("limit", 10))
    parts = DEFAULT_LIBRARY.search_parts(q, category=str(cat) if cat else None, limit=limit)
    return {"count": len(parts), "parts": [p.model_dump() for p in parts]}


def _tool_art_plan_tiles(args: Dict[str, Any]) -> Dict[str, Any]:
    width = int(args["width"])
    height = int(args["height"])
    tile_size = int(args.get("tile_size", 512))
    overlap = int(args.get("overlap", 64))
    tiles = plan_tiles(width, height, tile_size, overlap)
    return {"count": len(tiles), "tiles": [t.__dict__ for t in tiles]}


def _tool_art_resource_plan(args: Dict[str, Any]) -> Dict[str, Any]:
    ram_gb = float(args.get("ram_gb", 8.0))
    vram_gb = float(args.get("vram_gb", 4.0))
    runtime_s = float(args.get("runtime_seconds", 60.0))
    hourly = float(args.get("hourly_rate", 0.44))
    budget = args.get("budget_usd")
    plan = plan_resources(ram_gb=ram_gb, vram_gb=vram_gb).__dict__
    cost = estimate_compute_cost(runtime_s, hourly)
    gate = enforce_budget_gate(cost, float(budget) if budget is not None else None)
    return {"resource_plan": plan, "estimated_cost_usd": cost, "budget_gate": gate}


def _tool_platform_scan_secrets(args: Dict[str, Any]) -> Dict[str, Any]:
    text = str(args.get("text", ""))
    hits = scan_text_for_secrets(text)
    redacted = sanitize_payload({"text": text})["text"]
    return {"clean": len(hits) == 0, "findings": hits, "redacted": redacted}


def _tool_platform_status(_args: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "platform": "unified-deterministic-studio",
        "kicad_cli": detect_kicad_cli(),
        "pygerber": detect_pygerber(),
        "spandrel": spandrel_runtime_status(),
        "streaming_io": detect_streaming_backend(),
    }


def _tool_eda_compile_hac(args: Dict[str, Any]) -> Dict[str, Any]:
    res = compile_hac_to_board(str(args.get("source", "")))
    return {
        "compiled": res["compiled"],
        "proof": res["proof"],
        "component_count": len(res["board"].components) if res["board"] else 0,
    }


def _tool_eda_simulate_physics(args: Dict[str, Any]) -> Dict[str, Any]:
    template_id = str(args.get("template_id", "esp32_i2c_sensor"))
    routed = DesignPipeline().run(build_template_board(template_id)).board
    return simulate_board_physics(routed, copper_oz=float(args.get("copper_oz", 1.0)))


def _tool_eda_check_3d_assembly(args: Dict[str, Any]) -> Dict[str, Any]:
    template_id = str(args.get("template_id", "esp32_i2c_sensor"))
    return check_3d_assembly_collisions(build_template_board(template_id))


def _tool_art_route_sota(args: Dict[str, Any]) -> Dict[str, Any]:
    return select_sota_restoration_plan(
        quality_profile=str(args.get("quality_profile", "museum_fidelity")),
        media_type=str(args.get("media_type", "photo")),
        vram_gb=float(args.get("vram_gb", 24.0)),
        damage_level=str(args.get("damage_level", "moderate")),
        allow_generative=bool(args.get("allow_generative", False)),
    )


def _tool_agent_closed_loop_pcb(args: Dict[str, Any]) -> Dict[str, Any]:
    return run_closed_loop_pcb_agent(
        template_id=str(args.get("template_id", "esp32_i2c_sensor")),
        hac_source=args.get("hac_source"),
        workflow_id=args.get("workflow_id"),
    )


_TOOLS: Dict[str, Dict[str, Any]] = {
    "eda_list_templates": {
        "description": "Listet alle deterministischen EDA-Platinen-Templates auf.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _tool_eda_list_templates,
    },
    "eda_run_template": {
        "description": "Fuehrt Routing, DRC, RS-274X-Gerber-Verifikation und SHA-256-Manifest fuer ein EDA-Template aus.",
        "inputSchema": {
            "type": "object",
            "properties": {"template_id": {"type": "string", "default": "esp32_i2c_sensor"}},
        },
        "handler": _tool_eda_run_template,
    },
    "eda_search_parts": {
        "description": "Durchsucht die lokale JLCPCB/LCSC-SQLite-Bauteilbibliothek.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "default": ""},
                "category": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
        },
        "handler": _tool_eda_search_parts,
    },
    "art_plan_tiles": {
        "description": "Berechnet einen deterministischen Kachelplan fuer grosse Bilder.",
        "inputSchema": {
            "type": "object",
            "required": ["width", "height"],
            "properties": {
                "width": {"type": "integer"},
                "height": {"type": "integer"},
                "tile_size": {"type": "integer", "default": 512},
                "overlap": {"type": "integer", "default": 64},
            },
        },
        "handler": _tool_art_plan_tiles,
    },
    "art_resource_plan": {
        "description": "Berechnet Quality-First Ressourcenplan und Budget-Gate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ram_gb": {"type": "number", "default": 8.0},
                "vram_gb": {"type": "number", "default": 4.0},
                "runtime_seconds": {"type": "number", "default": 60.0},
                "hourly_rate": {"type": "number", "default": 0.44},
                "budget_usd": {"type": "number"},
            },
        },
        "handler": _tool_art_resource_plan,
    },
    "platform_scan_secrets": {
        "description": "Prueft Text fail-closed auf offene Tokens/Keys und liefert eine redigierte Vorschau.",
        "inputSchema": {
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        },
        "handler": _tool_platform_scan_secrets,
    },
    "platform_status": {
        "description": "Liefert den Verfuegbarkeitsstatus optionaler Backends (KiCad-CLI, pygerber, Spandrel, pyvips).",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _tool_platform_status,
    },
    "eda_compile_hac": {
        "description": "Beweist Hardware-as-Code-Elektrik via Z3-SMT und kompiliert bei SAT in BoardState.",
        "inputSchema": {
            "type": "object",
            "required": ["source"],
            "properties": {"source": {"type": "string"}},
        },
        "handler": _tool_eda_compile_hac,
    },
    "eda_simulate_physics": {
        "description": "Fuehrt IPC-2152-Thermik, PDN-IR-Drop, Crosstalk und S-Parameter-Pre-Signoff aus.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "template_id": {"type": "string", "default": "esp32_i2c_sensor"},
                "copper_oz": {"type": "number", "default": 1.0},
            },
        },
        "handler": _tool_eda_simulate_physics,
    },
    "eda_check_3d_assembly": {
        "description": "Prueft 3D-Baugruppen-Interferenzen (Platine + Bauteile + Gehaeuse/Deckel).",
        "inputSchema": {
            "type": "object",
            "properties": {"template_id": {"type": "string", "default": "esp32_i2c_sensor"}},
        },
        "handler": _tool_eda_check_3d_assembly,
    },
    "art_route_sota": {
        "description": "Waehlt deterministisch Stufe A (NAFNet/HAT) oder Stufe B (SeedVR2/SUPIR) inkl. Hallucination-Gate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "quality_profile": {"type": "string", "default": "museum_fidelity"},
                "media_type": {"type": "string", "default": "photo"},
                "vram_gb": {"type": "number", "default": 24.0},
                "allow_generative": {"type": "boolean", "default": False},
            },
        },
        "handler": _tool_art_route_sota,
    },
    "agent_closed_loop_pcb": {
        "description": "Startet den dauerhaft journalisierten, Ed25519-signierten Closed-Loop-PCB-Agenten.",
        "inputSchema": {
            "type": "object",
            "properties": {"template_id": {"type": "string", "default": "esp32_i2c_sensor"}},
        },
        "handler": _tool_agent_closed_loop_pcb,
    },
}


def list_mcp_tools() -> List[Dict[str, Any]]:
    return [
        {
            "name": name,
            "description": spec["description"],
            "inputSchema": spec["inputSchema"],
        }
        for name, spec in _TOOLS.items()
    ]


def handle_jsonrpc_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Verarbeitet eine einzelne MCP JSON-RPC 2.0 Anfrage deterministisch."""
    req_id = payload.get("id")
    method = payload.get("method", "")
    params = payload.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "serverInfo": {"name": "unified-deterministic-studio-mcp", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": list_mcp_tools()},
        }

    if method == "tools/call":
        tool_name = str(params.get("name", ""))
        tool_args = params.get("arguments") or {}
        spec = _TOOLS.get(tool_name)
        if spec is None:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": f"Unbekanntes MCP-Tool: {tool_name}"},
            }
        try:
            handler: Callable[[Dict[str, Any]], Dict[str, Any]] = spec["handler"]
            result = handler(tool_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
                    ],
                    "structuredContent": result,
                    "isError": False,
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Fehler: {exc}"}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unbekannte MCP-Methode: {method}"},
    }


def serve_stdio() -> None:
    """Startet den MCP-Server im Standard-stdio-Modus (eine JSON-RPC-Nachricht pro Zeile)."""
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        else:
            resp = handle_jsonrpc_request(req)
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    serve_stdio()
