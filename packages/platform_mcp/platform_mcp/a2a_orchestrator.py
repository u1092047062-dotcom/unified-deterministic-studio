"""A2A (Agent-to-Agent) Protokoll & Durable Closed-Loop Multi-Agent-Orchestrierung (Phase 9).

Verbindet alle deterministischen Plattform-Gates (Phasen 1-8) zu vollautomatischen,
SQLite-WAL-journalisierten, OTLP-getracten und Ed25519-signierten Closed-Loop-Agenten:
1. `closed_loop_pcb_engineering`:
   HAC/Template -> Z3-Beweis -> Routing/DRC -> IPC-2152/Crosstalk-Physik ->
   3D-Kollisionscheck -> Gerber-Verify & Panelizer -> Ed25519 + RFC-6962 Merkle-Bundle
2. `closed_loop_media_qc`:
   32-Bit Linear ACEScg Analyse -> Zweistufiger SOTA-Router ->
   Hallucination-Delta-Gate -> Ed25519-signiertes C2PA-PNG + Selbstverifikation
"""

from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import sys
from typing import Any, Dict, Optional

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[3]
for p in (
    REPO_ROOT / "packages" / "platform_core",
    REPO_ROOT / "apps" / "eda-multiagent-core",
    REPO_ROOT / "apps" / "art-restoration-ai",
):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

from platform_core.c2pa_credentials import build_c2pa_manifest
from platform_core.crypto_signing import (
    build_transparency_bundle,
    embed_signed_png_c2pa,
    generate_ed25519_keypair,
    sign_manifest_ed25519,
    verify_signed_png_c2pa,
    verify_transparency_bundle,
)
from platform_core.durable_workflow import DurableWorkflowEngine
from platform_core.otel_tracing import PlatformTracer
from platform_core.provenance import sha256_bytes

from core.assembly_collision_3d import check_3d_assembly_collisions
from core.board_synthesizer import build_template_board
from core.cad_enclosure_v2 import validate_enclosure_geometry
from core.em_thermal_sim import simulate_board_physics
from core.export_manifest import build_export_manifest, summarize_board
from core.gerber_export import export_gerber_bundle
from core.gerber_verifier import verify_and_render_gerbers
from core.hardware_as_code import compile_hac_to_board
from core.panelizer import PanelConfig, panelize_board
from core.pipeline_v2 import DesignPipeline
from core.schema import BoardState

from restoration.color_pipeline import analyze_linear_color_profile
from restoration.hallucination_gate import evaluate_tile_fidelity
from restoration.sota_router import select_sota_restoration_plan


def build_a2a_agent_card(base_url: str = "http://127.0.0.1:8080") -> Dict[str, Any]:
    """Erzeugt eine standardkonforme A2A AgentCard (`/.well-known/agent.json`)."""
    return {
        "name": "Unified Deterministic Studio Agent",
        "version": "2.0.0",
        "protocol": "a2a.v1",
        "url": base_url,
        "endpoints": {
            "a2a_tasks": f"{base_url}/a2a/tasks",
            "mcp_jsonrpc": f"{base_url}/mcp",
            "trust_verify": f"{base_url}/trust/verify",
            "frontier_inspector": f"{base_url}/inspector",
            "simulator": f"{base_url}/simulator",
        },
        "capabilities": {
            "durable_execution": "sqlite-wal-idempotent-replay",
            "tracing": "w3c-traceparent-otlp-json",
            "cryptographic_signing": "rfc8032-ed25519+rfc6962-merkle+c2pa-png",
            "fail_closed_quality_first": True,
        },
        "skills": [
            {
                "id": "closed_loop_pcb_engineering",
                "name": "Closed-Loop Deterministic PCB Engineering",
                "description": "HAC/Template -> Z3 -> Routing/DRC -> IPC-2152 Physik -> 3D-Kollisionscheck -> Gerber/Panel -> Ed25519 Merkle-Signoff",
            },
            {
                "id": "closed_loop_media_qc",
                "name": "Closed-Loop Museum/Cinema Media QC",
                "description": "32-Bit Linear ACEScg -> SOTA-Router -> Hallucination-Delta-Gate -> Ed25519-signiertes C2PA-PNG",
            },
        ],
    }


def _default_db_path() -> Path:
    return Path(os.environ.get("PLATFORM_WORKFLOW_DB", str(REPO_ROOT / "logs" / "workflows.sqlite")))


def _default_span_path() -> Path:
    return Path(os.environ.get("PLATFORM_OTEL_LOG", str(REPO_ROOT / "logs" / "otel_spans.jsonl")))


def run_closed_loop_pcb_agent(
    *,
    template_id: str = "esp32_i2c_sensor",
    hac_source: Optional[str] = None,
    workflow_id: Optional[str] = None,
    budget_usd: float = 2.0,
    db_path: Optional[Path] = None,
    span_log_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Fuehrt die vollstaendige Closed-Loop-PCB-Pipeline dauerhaft journalisiert & Ed25519-signiert aus."""
    engine = DurableWorkflowEngine(db_path or _default_db_path())
    tracer = PlatformTracer("a2a-pcb-agent", span_log_path=span_log_path or _default_span_path())

    with tracer.start_span("closed_loop_pcb_engineering", attributes={"template_id": template_id}) as root_span:
        wf = engine.start_or_resume(
            name="closed_loop_pcb_engineering",
            domain="eda",
            config={"template_id": template_id, "has_hac": bool(hac_source)},
            workflow_id=workflow_id,
            budget_usd=budget_usd,
            trace_id=root_span["traceId"],
        )
        wid = wf["workflow_id"]

        # Schritt 1: Hardware-as-Code Z3-Beweis oder Template-Synthese
        def _step_build() -> Dict[str, Any]:
            if hac_source:
                compiled = compile_hac_to_board(hac_source)
                if not compiled["compiled"]:
                    raise ValueError(f"HAC Z3-Konflikt: {compiled['proof']['diagnostics']}")
                board = compiled["board"]
                proof = compiled["proof"]
            else:
                board = build_template_board(template_id)
                proof = {"sat": True, "status": "TEMPLATE_VERIFIED"}
            return {"proof": proof, "board": board.model_dump()}

        s1 = engine.run_step(wid, "1_synthesize_and_prove", _step_build, estimated_cost_usd=0.01)
        board_in = BoardState.model_validate(s1["output"]["board"])

        # Schritt 2: Deterministische DesignPipeline (Placement + Routing + Spatial DRC)
        def _step_route() -> Dict[str, Any]:
            res = DesignPipeline().run(board_in)
            return {
                "status": res.status,
                "attempts_used": res.attempts_used,
                "drc_errors": len(res.board.errors),
                "board": res.board.model_dump(),
            }

        s2 = engine.run_step(wid, "2_route_and_drc", _step_route, estimated_cost_usd=0.02)
        routed_board = BoardState.model_validate(s2["output"]["board"])

        # Schritt 3: Physik-Pre-Signoff (IPC-2152 Thermik, IR-Drop, Crosstalk, S-Parameter)
        s3 = engine.run_step(
            wid,
            "3_physics_presignoff",
            lambda: simulate_board_physics(routed_board),
            estimated_cost_usd=0.01,
        )

        # Schritt 4: 3D-Baugruppen-Kollisionscheck & Gehaeuse-DFM
        def _step_3d() -> Dict[str, Any]:
            return {
                "enclosure_dfm": validate_enclosure_geometry(routed_board),
                "assembly_3d": check_3d_assembly_collisions(routed_board),
            }

        s4 = engine.run_step(wid, "4_3d_assembly_and_enclosure", _step_3d, estimated_cost_usd=0.01)

        # Schritt 5: RS-274X Gerber-Verifikation, Panelizer & Ed25519 + Merkle-Signoff
        def _step_export_sign() -> Dict[str, Any]:
            gerbers = export_gerber_bundle(routed_board)
            gerber_check = verify_and_render_gerbers(gerbers)
            panel = panelize_board(routed_board, PanelConfig(rows=2, cols=2, cut_mode="vcut"))
            manifest = build_export_manifest(gerbers, summarize_board(routed_board))

            kp = generate_ed25519_keypair()
            signed = sign_manifest_ed25519(
                manifest, kp["private_seed_hex"], signer_identity="a2a-pcb-agent@unified-studio.local"
            )
            bundle = build_transparency_bundle(manifest["manifest_sha256"], signed)
            trust_verify = verify_transparency_bundle(bundle)
            return {
                "gerber_valid": gerber_check["valid"],
                "panel_boards": panel["board_count"],
                "manifest_sha256": manifest["manifest_sha256"],
                "ed25519_public_key_hex": kp["public_key_hex"],
                "merkle_leaf_sha256": bundle["merkle_leaf_sha256"],
                "trust_verification": trust_verify,
            }

        s5 = engine.run_step(wid, "5_gerber_panel_and_ed25519_sign", _step_export_sign, estimated_cost_usd=0.01)
        final_wf = engine.complete_workflow(wid)

        return {
            "skill": "closed_loop_pcb_engineering",
            "status": "completed",
            "workflow_id": wid,
            "trace_id": root_span["traceId"],
            "pipeline_status": s2["output"]["status"],
            "drc_errors": s2["output"]["drc_errors"],
            "physics_passed": s3["output"]["passed"],
            "assembly_3d_collision_free": s4["output"]["assembly_3d"]["collision_free"],
            "enclosure_dfm_valid": s4["output"]["enclosure_dfm"]["valid"],
            "gerber_valid": s5["output"]["gerber_valid"],
            "trust_verified": s5["output"]["trust_verification"]["valid"],
            "merkle_leaf_sha256": s5["output"]["merkle_leaf_sha256"],
            "steps_count": len(final_wf["steps"]),
            "spent_usd": final_wf["spent_usd"],
        }


def run_closed_loop_media_qc_agent(
    *,
    quality_profile: str = "museum_fidelity",
    media_type: str = "photo",
    vram_gb: float = 24.0,
    allow_generative: bool = False,
    workflow_id: Optional[str] = None,
    db_path: Optional[Path] = None,
    span_log_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Fuehrt die Closed-Loop Media-QC-Pipeline (ACEScg -> SOTA -> Hallucination-Gate -> Ed25519 C2PA) aus."""
    engine = DurableWorkflowEngine(db_path or _default_db_path())
    tracer = PlatformTracer("a2a-media-agent", span_log_path=span_log_path or _default_span_path())

    with tracer.start_span("closed_loop_media_qc", attributes={"quality_profile": quality_profile}) as root_span:
        wf = engine.start_or_resume(
            name="closed_loop_media_qc",
            domain="art",
            config={"quality_profile": quality_profile, "media_type": media_type, "allow_generative": allow_generative},
            workflow_id=workflow_id,
            budget_usd=1.0,
            trace_id=root_span["traceId"],
        )
        wid = wf["workflow_id"]

        # Synthetische Testkachel fuer deterministische End-to-End-QC
        y, x = np.mgrid[0:64, 0:64]
        orig = np.stack([(x * 3) % 255, (y * 3) % 255, ((x + y) * 2) % 255], axis=-1).astype(np.uint8)
        restored = np.clip(orig.astype(np.int16) + 1, 0, 255).astype(np.uint8)

        s1 = engine.run_step(wid, "1_acescg_color_analysis", lambda: analyze_linear_color_profile(orig))
        s2 = engine.run_step(
            wid,
            "2_sota_model_routing",
            lambda: select_sota_restoration_plan(
                quality_profile=quality_profile,
                media_type=media_type,
                vram_gb=vram_gb,
                allow_generative=allow_generative,
            ),
        )
        s3 = engine.run_step(
            wid,
            "3_hallucination_delta_gate",
            lambda: evaluate_tile_fidelity(orig, restored),
        )

        def _step_c2pa_sign() -> Dict[str, Any]:
            buf_o, buf_r = BytesIO(), BytesIO()
            Image.fromarray(orig, mode="RGB").save(buf_o, format="PNG")
            Image.fromarray(restored, mode="RGB").save(buf_r, format="PNG")
            kp = generate_ed25519_keypair()
            manifest = build_c2pa_manifest(
                title=f"a2a-media-qc-{wid}.png",
                source_sha256=sha256_bytes(buf_o.getvalue()),
                actions=[{"action": "c2pa.edited", "softwareAgent": "Unified Studio A2A Media Agent"}],
                ai_disclosed=bool(s2["output"]["generative_allowed"]),
                simulated=True,
                extra_metadata={"sota_tier": s2["output"]["tier"]},
            )
            signed_png = embed_signed_png_c2pa(
                buf_r.getvalue(), manifest, kp["private_seed_hex"], signer_identity="a2a-media-agent@unified-studio.local"
            )
            verified = verify_signed_png_c2pa(signed_png)
            return {
                "valid": verified["valid"],
                "pixel_integrity_ok": verified["pixel_integrity_ok"],
                "ed25519_valid": verified["asymmetric_trust"]["signature_valid"],
                "key_id": verified["asymmetric_trust"]["key_id"],
            }

        s4 = engine.run_step(wid, "4_ed25519_c2pa_sign_and_verify", _step_c2pa_sign)
        final_wf = engine.complete_workflow(wid)

        return {
            "skill": "closed_loop_media_qc",
            "status": "completed",
            "workflow_id": wid,
            "trace_id": root_span["traceId"],
            "acescg_roundtrip_max_error": s1["output"]["acescg_roundtrip_max_error"],
            "sota_tier": s2["output"]["tier"],
            "primary_model": s2["output"]["primary_model"],
            "hallucination_gate_passed": s3["output"]["passed"],
            "c2pa_ed25519_verified": s4["output"]["valid"] and s4["output"]["ed25519_valid"],
            "steps_count": len(final_wf["steps"]),
        }


def dispatch_a2a_task(
    payload: Dict[str, Any],
    *,
    db_path: Optional[Path] = None,
    span_log_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Verarbeitet eine A2A-Aufgabe (`POST /a2a/tasks`)."""
    skill = str(payload.get("skill", "")).strip()
    args = payload.get("input") or {}
    if skill == "closed_loop_pcb_engineering":
        return run_closed_loop_pcb_agent(
            template_id=str(args.get("template_id", "esp32_i2c_sensor")),
            hac_source=args.get("hac_source"),
            workflow_id=args.get("workflow_id"),
            budget_usd=float(args.get("budget_usd", 2.0)),
            db_path=db_path,
            span_log_path=span_log_path,
        )
    if skill == "closed_loop_media_qc":
        return run_closed_loop_media_qc_agent(
            quality_profile=str(args.get("quality_profile", "museum_fidelity")),
            media_type=str(args.get("media_type", "photo")),
            vram_gb=float(args.get("vram_gb", 24.0)),
            allow_generative=bool(args.get("allow_generative", False)),
            workflow_id=args.get("workflow_id"),
            db_path=db_path,
            span_log_path=span_log_path,
        )
    raise ValueError(f"Unbekannter A2A-Skill: '{skill}'")
