from __future__ import annotations

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from pathlib import Path
import json
import shutil
import sys
import time
import uuid

_PLATFORM_CORE = Path(__file__).resolve().parents[3] / "packages" / "platform_core"
if _PLATFORM_CORE.is_dir() and str(_PLATFORM_CORE) not in sys.path:
    sys.path.insert(0, str(_PLATFORM_CORE))

from platform_core.c2pa_credentials import (
    build_c2pa_manifest,
    detect_c2pa_backend,
    embed_png_content_credentials,
    verify_png_content_credentials,
)
from platform_core.provenance import build_provenance_manifest

from app.config import DATA_DIR, WORK_DIR, MAX_UPLOAD_MB, ALLOWED_EXTENSIONS
from app.storage import sha256_file, inspect_image
from app.telemetry import record_art_event
from app.topaz import create_topaz_handoff, import_topaz_result, detect_topaz_installations
from app.cloud import cloud_status
from restoration.checkpoints import inspect_checkpoints_dir
from restoration.engine import RestorationEngine
from restoration.spandrel_adapter import spandrel_runtime_status
from restoration.streaming_io import detect_streaming_backend
from restoration.tiling import plan_tiles
from restoration.adapters import available_adapters, IdentityValidationAdapter
from restoration.model_registry import MODEL_REGISTRY
from restoration.profiles import PROFILES
from resource_manager.policy import choose_resources
from resource_manager.costs import estimate_gpu_cost, enforce_budget

app = FastAPI(title="ART Restoration AI", version="1.0.0.dev1", description="Quality-first restoration orchestration; AI checkpoints and cloud workers are opt-in and separately verified.")
DATA_DIR.mkdir(parents=True, exist_ok=True)
WORK_DIR.mkdir(parents=True, exist_ok=True)


class TilePlanRequest(BaseModel):
    tile_size: int = Field(default=1024, ge=128, le=8192)
    overlap: int = Field(default=96, ge=0, le=2047)


class RestorationRequest(BaseModel):
    tile_size: int = Field(default=1024, ge=128, le=8192)
    overlap: int = Field(default=96, ge=0, le=2047)
    quality_profile: str = Field(default="museum_fidelity", min_length=1, max_length=64)
    adapter: str = Field(default="identity-validation-only")


class CostEstimateRequest(BaseModel):
    runtime_seconds: float = Field(ge=0)
    hourly_rate: float = Field(ge=0)
    budget: float | None = Field(default=None, ge=0)


def project_dir(project_id: str) -> Path:
    if not project_id or not project_id.replace("-", "").isalnum() or len(project_id) > 64:
        raise HTTPException(400, "Ungültige Projekt-ID")
    p = (DATA_DIR / project_id).resolve()
    if DATA_DIR not in p.parents:
        raise HTTPException(400, "Ungültiger Projektpfad")
    p.mkdir(parents=True, exist_ok=True)
    return p


def source_record(project: Path) -> dict:
    manifest = project / "source_manifest.json"
    if not manifest.is_file():
        raise HTTPException(404, "Kein Originalbild hochgeladen")
    record = json.loads(manifest.read_text(encoding="utf-8"))
    source = (project / record["file"]).resolve()
    if project not in source.parents or not source.is_file():
        raise HTTPException(409, "Originaldatei fehlt oder Manifest ist ungültig")
    if sha256_file(source) != record["sha256"]:
        raise HTTPException(409, "Original-Prüfsumme stimmt nicht; Verarbeitung gestoppt")
    return {**record, "absolute_path": source}



def _job_file(project: Path, job_id: str) -> Path:
    try:
        uuid.UUID(job_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(400, "Ungültige Job-ID")
    jobs = project / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    return jobs / f"{job_id}.json"


def _execute_restoration(project_id: str, job_id: str, request: RestorationRequest) -> None:
    started = time.perf_counter()
    project = project_dir(project_id)
    job_path = _job_file(project, job_id)
    state = json.loads(job_path.read_text(encoding="utf-8"))
    state.update({"status": "running"})
    job_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        record = source_record(project)
        output = project / "outputs" / f"restored-{job_id}.png"
        result = RestorationEngine(WORK_DIR / project_id).run(
            record["absolute_path"], output, tile_size=request.tile_size,
            overlap=request.overlap, adapter=IdentityValidationAdapter(),
            job_id=job_id, quality_profile=request.quality_profile
        )
        state.update(result)
        state["status"] = "completed"
        record_art_event(
            "restoration_job",
            status="COMPLETED",
            duration_ms=(time.perf_counter() - started) * 1000.0,
            details={
                "project_id": project_id,
                "job_id": job_id,
                "tiles_total": result.get("tiles_total"),
                "adapter": request.adapter,
            },
        )
    except Exception as exc:
        state.update({"status": "failed", "error": str(exc)})
        record_art_event(
            "restoration_job",
            status="ERROR",
            duration_ms=(time.perf_counter() - started) * 1000.0,
            details={"project_id": project_id, "job_id": job_id, "error_type": type(exc).__name__},
        )
    job_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/health")
def health():
    return {"status": "ok", "service": "art-restoration-ai", "version": app.version, "mode": "development"}


@app.get("/capabilities")
def capabilities():
    return {
        "quality_policy": "quality-first; resource optimization must not lower model quality",
        "adapters": [a.__dict__ for a in available_adapters()],
        "models": [m.__dict__ for m in MODEL_REGISTRY],
        "quality_profiles": PROFILES,
        "topaz": detect_topaz_installations(),
        "cloud": cloud_status(),
        "limitations": ["Identity adapter validates tiling/stitching only; it does not enhance images.", "Topaz integration uses manual licensed desktop handoff; no unsupported GUI/API automation.", "AI checkpoints and cloud workers are not enabled by default."]
    }


@app.get("/resources/plan")
def resource_plan(vram_gb: float = 4.0, quality_profile: str = "museum_fidelity"):
    try:
        return choose_resources(vram_gb, quality_profile).__dict__
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/resources/cost-estimate")
def resource_cost_estimate(request: CostEstimateRequest):
    estimated = estimate_gpu_cost(request.runtime_seconds, request.hourly_rate)
    gate = enforce_budget(estimated, request.budget)
    return {"estimated_gpu_cost": estimated, "currency": "USD (hourly_rate input basis)", **gate}


@app.post("/projects")
def create_project(name: str = "Restaurierungsprojekt"):
    if not name.strip() or len(name) > 160:
        raise HTTPException(400, "Projektname muss 1–160 Zeichen enthalten")
    pid = str(uuid.uuid4())
    p = project_dir(pid)
    (p / "project.json").write_text(json.dumps({"id": pid, "name": name.strip(), "status": "created"}, ensure_ascii=False, indent=2), encoding="utf-8")
    record_art_event("project_created", status="OK", details={"project_id": pid})
    return {"project_id": pid, "name": name.strip()}


@app.post("/projects/{project_id}/source")
async def upload_source(project_id: str, file: UploadFile = File(...)):
    p = project_dir(project_id)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, "Nicht unterstütztes Bildformat")
    target = p / "originals" / f"original{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise HTTPException(409, "Ein Original ist bereits vorhanden; Originale werden nicht überschrieben")
    size = 0
    try:
        with target.open("xb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_MB * 1024 * 1024:
                    raise HTTPException(413, "Datei überschreitet Upload-Limit")
                out.write(chunk)
        meta = inspect_image(target)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    except Exception:
        target.unlink(missing_ok=True)
        raise HTTPException(400, "Datei ist kein lesbares oder zulässiges Bild")
    record = {"file": str(target.relative_to(p)), "sha256": sha256_file(target), "bytes": size, **meta}
    (p / "source_manifest.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


@app.get("/projects/{project_id}/source")
def get_source(project_id: str):
    record = source_record(project_dir(project_id))
    record.pop("absolute_path", None)
    return record


@app.post("/projects/{project_id}/analyze")
def analyze_source(project_id: str):
    record = source_record(project_dir(project_id))
    return {"source": {k: v for k, v in record.items() if k != "absolute_path"}, "analysis": {"megapixels": record["width"] * record["height"] / 1_000_000, "requires_tiling": record["width"] > 2048 or record["height"] > 2048, "preservation": "Original remains unchanged; no enhancement has been applied."}}


@app.post("/projects/{project_id}/tiles/plan")
def tile_plan(project_id: str, request: TilePlanRequest):
    record = source_record(project_dir(project_id))
    try:
        tiles = plan_tiles(record["width"], record["height"], request.tile_size, request.overlap)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"width": record["width"], "height": record["height"], "tile_size": request.tile_size, "overlap": request.overlap, "count": len(tiles), "tiles": [t.__dict__ for t in tiles]}


@app.post("/projects/{project_id}/restoration/run")
def run_restoration(project_id: str, request: RestorationRequest):
    if request.adapter != "identity-validation-only":
        raise HTTPException(409, "Dieser Adapter ist noch nicht aktiviert. Keine nicht geprüften KI-Gewichte werden ausgeführt.")
    project = project_dir(project_id)
    record = source_record(project)
    output = project / "outputs" / "pipeline-validation.png"
    job_id = str(uuid.uuid4())
    try:
        result = RestorationEngine(WORK_DIR / project_id).run(
            record["absolute_path"], output, tile_size=request.tile_size,
            overlap=request.overlap, adapter=IdentityValidationAdapter(),
            job_id=job_id, quality_profile=request.quality_profile
        )
    except Exception as e:
        raise HTTPException(500, f"Pipeline-Validierung fehlgeschlagen: {e}")
    job_path = project / "jobs"
    job_path.mkdir(parents=True, exist_ok=True)
    (job_path / f"{job_id}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


@app.post("/projects/{project_id}/restoration/jobs")
def create_restoration_job(project_id: str, request: RestorationRequest, background_tasks: BackgroundTasks):
    if request.adapter != "identity-validation-only":
        raise HTTPException(409, "Dieser Adapter ist noch nicht aktiviert. Keine nicht geprüften KI-Gewichte werden ausgeführt.")
    project = project_dir(project_id)
    source_record(project)
    job_id = str(uuid.uuid4())
    job_path = _job_file(project, job_id)
    job_path.write_text(json.dumps({
        "job_id": job_id, "status": "queued", "adapter": request.adapter,
        "quality_profile": request.quality_profile, "tile_size": request.tile_size,
        "overlap": request.overlap
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    background_tasks.add_task(_execute_restoration, project_id, job_id, request)
    return {"job_id": job_id, "status": "queued", "status_url": f"/projects/{project_id}/jobs/{job_id}"}


@app.post("/projects/{project_id}/restoration/jobs/{job_id}/resume")
def resume_restoration_job(project_id: str, job_id: str, background_tasks: BackgroundTasks):
    project = project_dir(project_id)
    job_path = _job_file(project, job_id)
    if not job_path.is_file():
        raise HTTPException(404, "Job nicht gefunden")
    state = json.loads(job_path.read_text(encoding="utf-8"))
    if state.get("status") == "completed":
        raise HTTPException(409, "Abgeschlossene Jobs müssen nicht fortgesetzt werden")
    request = RestorationRequest(
        adapter=state.get("adapter", "identity-validation-only"),
        quality_profile=state.get("quality_profile", "museum_fidelity"),
        tile_size=state.get("tile_size", 1024), overlap=state.get("overlap", 96)
    )
    if request.adapter != "identity-validation-only":
        raise HTTPException(409, "Job-Adapter ist nicht aktiviert")
    state["status"] = "queued"
    job_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    background_tasks.add_task(_execute_restoration, project_id, job_id, request)
    return {"job_id": job_id, "status": "queued", "resuming": True}


@app.get("/projects/{project_id}/jobs/{job_id}")
def get_job(project_id: str, job_id: str):
    p = project_dir(project_id)
    path = p / "jobs" / f"{job_id}.json"
    if not path.is_file():
        raise HTTPException(404, "Job nicht gefunden")
    return json.loads(path.read_text(encoding="utf-8"))


@app.post("/projects/{project_id}/topaz/export")
def topaz_export(project_id: str, tool: str = "Topaz Gigapixel AI"):
    try:
        return create_topaz_handoff(project_dir(project_id), tool)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/projects/{project_id}/topaz/import")
async def topaz_import(project_id: str, file: UploadFile = File(...), tool: str = "Topaz", version: str | None = None):
    p = project_dir(project_id)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, "Nicht unterstütztes Bildformat")
    incoming = p / "topaz" / "imports"
    incoming.mkdir(parents=True, exist_ok=True)
    dest = incoming / f"topaz-result-{uuid.uuid4()}{suffix}"
    size = 0
    try:
        with dest.open("xb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_MB * 1024 * 1024:
                    raise HTTPException(413, "Datei überschreitet Upload-Limit")
                out.write(chunk)
        return import_topaz_result(p, dest, tool, version)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"Importprüfung fehlgeschlagen: {e}")


@app.get("/checkpoints/status")
def checkpoints_status():
    """Liefert den Fail-Closed-Verifikationsstatus lokaler KI-Checkpoints."""
    return {**inspect_checkpoints_dir(), "spandrel_runtime": spandrel_runtime_status()}


@app.get("/io/backend")
def io_backend_status():
    """Liefert den Status des Streaming-Kachel-I/O-Backends (pyvips vs. pillow-bounded)."""
    return detect_streaming_backend()


@app.get("/c2pa/status")
def c2pa_status():
    """Liefert den Status des C2PA Content-Credentials-Backends."""
    return detect_c2pa_backend()


@app.post("/projects/{project_id}/c2pa/embed")
def embed_project_c2pa(project_id: str, ai_disclosed: bool = False):
    """Bettet C2PA Content Credentials verlustfrei in die validierte PNG-Ausgabe des Projekts ein."""
    project = project_dir(project_id)
    record = source_record(project)
    output_png = project / "outputs" / "pipeline-validation.png"
    if not output_png.is_file():
        raise HTTPException(404, "Noch keine PNG-Ausgabe vorhanden; zuerst Restaurierung ausfuehren")
    manifest = build_c2pa_manifest(
        title=f"project-{project_id}-output.png",
        source_sha256=record["sha256"],
        actions=[
            {"action": "c2pa.opened", "softwareAgent": "ART Restoration AI"},
            {"action": "c2pa.edited", "softwareAgent": "ART Restoration AI"},
        ],
        ai_disclosed=ai_disclosed,
        simulated=False,
        extra_metadata={"project_id": project_id},
    )
    signed_bytes = embed_png_content_credentials(output_png.read_bytes(), manifest)
    output_png.write_bytes(signed_bytes)
    return verify_png_content_credentials(signed_bytes)


@app.get("/projects/{project_id}/c2pa/verify")
def verify_project_c2pa(project_id: str):
    """Verifiziert die eingebetteten C2PA Content Credentials der Projekt-Ausgabe."""
    project = project_dir(project_id)
    output_png = project / "outputs" / "pipeline-validation.png"
    if not output_png.is_file():
        raise HTTPException(404, "Keine PNG-Ausgabe fuer C2PA-Pruefung gefunden")
    return verify_png_content_credentials(output_png.read_bytes())


@app.get("/projects/{project_id}/provenance")
def project_provenance(project_id: str):
    """Erstellt ein deterministisches SHA-256-Provenienz-Manifest fuer ein ART-Projekt."""
    project = project_dir(project_id)
    artifacts: dict[str, bytes] = {}
    for rel in ("project.json", "source_manifest.json"):
        p = project / rel
        if p.is_file():
            artifacts[rel] = p.read_bytes()

    jobs_dir = project / "jobs"
    job_count = 0
    if jobs_dir.is_dir():
        for jf in sorted(jobs_dir.glob("*.json")):
            artifacts[f"jobs/{jf.name}"] = jf.read_bytes()
            job_count += 1

    topaz_dir = project / "topaz"
    if topaz_dir.is_dir():
        for tf in sorted(topaz_dir.rglob("*.json")):
            rel_key = tf.relative_to(project).as_posix()
            artifacts[rel_key] = tf.read_bytes()

    if not artifacts:
        raise HTTPException(404, "Projekt enthaelt noch keine Provenienzdaten")

    return build_provenance_manifest(
        artifacts,
        metadata={"project_id": project_id, "job_count": job_count},
        domain="art-restoration-ai",
    )
