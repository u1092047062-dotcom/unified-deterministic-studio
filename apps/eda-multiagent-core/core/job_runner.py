"""Atomarer, Provenienz-gesicherter EDA-Job-Runner (Muster aus ART-Restoration-AI + platform_core).

Fuehrt deterministische DesignPipeline-Laeufe mit:
- atomarer JSON-Persistenz (Write-to-Temp + Replace),
- unveraenderlichem Input-Konfigurations-Hash (SHA-256),
- vollstaendigem Export-Provenienz-Manifest (KiCad, STEP, OpenSCAD, BOM, CPL)
aus, ohne core/schema.py oder die deterministische Pipeline zu veraendern.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

_PACKAGES_DIR = Path(__file__).resolve().parents[3] / "packages" / "platform_core"
if _PACKAGES_DIR.is_dir() and str(_PACKAGES_DIR) not in sys.path:
    sys.path.insert(0, str(_PACKAGES_DIR))

from platform_core.job_store import JobStore
from platform_core.provenance import sha256_bytes

from core.enclosure_gen import generate_openscad_case
from core.export_manifest import build_export_manifest, summarize_board
from core.kicad_export import export_kicad_pcb
from core.manufacturing_export import generate_jlcpcb_bom, generate_jlcpcb_cpl
from core.pipeline_v2 import DesignPipeline
from core.schema import BoardState
from core.step_export import generate_step
from core.telemetry_log import record_server_event


def resolve_eda_jobs_dir(jobs_dir: Optional[os.PathLike[str] | str] = None) -> Path:
    if jobs_dir is not None:
        return Path(jobs_dir)
    env_dir = os.environ.get("EDA_JOBS_DIR", "").strip()
    if env_dir:
        return Path(env_dir)
    return Path(__file__).resolve().parent.parent / "logs" / "jobs"


class EDAJobRunner:
    """Persistiert und prueft EDA-Design-Laeufe ueber platform_core.JobStore."""

    def __init__(
        self,
        jobs_dir: Optional[os.PathLike[str] | str] = None,
        pipeline: Optional[DesignPipeline] = None,
    ) -> None:
        self.store = JobStore(resolve_eda_jobs_dir(jobs_dir))
        self.pipeline = pipeline or DesignPipeline()

    def submit_and_run(
        self,
        board: BoardState,
        *,
        job_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        jid = job_id or f"eda-{uuid.uuid4().hex[:12]}"
        input_hash = sha256_bytes(board.model_dump_json())
        config = {
            "input_board_sha256": input_hash,
            "width_mm": float(board.width),
            "height_mm": float(board.height),
            "input_components": len(board.components),
            "input_nets": len(board.nets),
        }
        self.store.create_job(jid, domain="eda", config=config, status="running")

        started = time.perf_counter()
        try:
            result = self.pipeline.run(board)
            artifacts = {
                "board.kicad_pcb": export_kicad_pcb(result.board),
                "board.step": generate_step(result.board),
                "case.scad": generate_openscad_case(result.board),
                "bom.csv": generate_jlcpcb_bom(result.board),
                "cpl.csv": generate_jlcpcb_cpl(result.board),
            }
            manifest = build_export_manifest(artifacts, summarize_board(result.board))
            duration_ms = round((time.perf_counter() - started) * 1000.0, 2)

            final_record = self.store.update_job(
                jid,
                {
                    "status": "completed",
                    "pipeline_status": result.status,
                    "attempts_used": result.attempts_used,
                    "duration_ms": duration_ms,
                    "board_summary": manifest["board_summary"],
                    "manifest_sha256": manifest["manifest_sha256"],
                    "artifacts": manifest["artifacts"],
                    "report": result.report,
                },
            )
            record_server_event(
                "eda_job_completed",
                status=result.status,
                duration_ms=duration_ms,
                details={
                    "job_id": jid,
                    "manifest_sha256": manifest["manifest_sha256"],
                    "drc_errors": len(result.board.errors),
                },
            )
            return final_record
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
            self.store.update_job(
                jid,
                {
                    "status": "failed",
                    "duration_ms": duration_ms,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            record_server_event(
                "eda_job_failed",
                status="ERROR",
                duration_ms=duration_ms,
                details={"job_id": jid, "error_type": type(exc).__name__},
            )
            raise

    def get_job(self, job_id: str) -> Dict[str, Any]:
        return self.store.load_job(job_id)

    def list_jobs(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        return self.store.list_jobs(limit=limit)
