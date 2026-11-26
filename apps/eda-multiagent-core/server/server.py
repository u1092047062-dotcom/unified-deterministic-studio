import os
from pathlib import Path
import sys
import time
from typing import Optional

from fastapi import Body, FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

_PLATFORM_CORE = Path(__file__).resolve().parents[3] / "packages" / "platform_core"
if _PLATFORM_CORE.is_dir() and str(_PLATFORM_CORE) not in sys.path:
    sys.path.insert(0, str(_PLATFORM_CORE))

from platform_core.resource_guard import plan_resources

from core.schema import BoardState, Component, Net, Pin, PinType, Via
from core.pipeline_v2 import DesignPipeline
from core.board_synthesizer import DesignSpec, build_template_board, list_templates, synthesize_board
from core.kicad_export import export_kicad_pcb
from core.gerber_export import detect_kicad_cli, export_gerber_bundle, export_gerber_with_manifest
from core.gerber_verifier import verify_and_render_gerbers
from core.part_library import DEFAULT_LIBRARY
from core.manufacturing_export import generate_jlcpcb_bom, generate_jlcpcb_cpl
from core.step_export import generate_step
from core.enclosure_gen import generate_openscad_case
from core.assembly_collision_3d import check_3d_assembly_collisions
from core.cad_enclosure_v2 import EnclosureParams, generate_build123d_script, validate_enclosure_geometry
from core.em_thermal_sim import generate_openems_fdtd_script, simulate_board_physics
from core.hardware_as_code import compile_hac_to_board
from core.kicad9_ipc import build_kicad9_ipc_sync_payload, detect_kicad9_ipc
from core.panelizer import PanelConfig, panelize_board
from core.export_manifest import build_export_manifest, summarize_board
from core.job_runner import EDAJobRunner
from core.telemetry_log import record_server_event

app = FastAPI(title="EDA Core Server", version="1.1.0")

# Pipeline-Graph einmalig kompilieren; invoke ist zustandslos.
PIPELINE = DesignPipeline()


def _job_runner() -> EDAJobRunner:
    return EDAJobRunner(pipeline=PIPELINE)

def build_demo_board() -> BoardState:
    board = BoardState()
    board.components["U1"] = Component(
        id="U1", name="ESP32", category="MCU", footprint="QFN-32",
        value="ESP32-WROOM-32E",
        x=30.0, y=40.0, body_w=5.0, body_h=5.0,
        pins={
            "1": Pin(id="1", name="GPIO21_SDA", pin_type=PinType.OUTPUT, x=27.5, y=42.5),
            "2": Pin(id="2", name="GPIO22_SCL", pin_type=PinType.OUTPUT, x=27.5, y=40.0),
            "3": Pin(id="3", name="GND", pin_type=PinType.GROUND, x=27.5, y=37.5),
        },
    )
    board.components["R1"] = Component(
        id="R1", name="R_10k", category="Resistor", footprint="0805",
        value="10k",
        x=70.0, y=40.0, body_w=2.0, body_h=1.25,
        pins={
            "1": Pin(id="1", name="A", pin_type=PinType.PASSIVE, x=69.0, y=40.0),
            "2": Pin(id="2", name="B", pin_type=PinType.PASSIVE, x=71.0, y=40.0),
        },
    )
    board.nets["NET_SDA"] = Net(id="NET_SDA", name="I2C_SDA", nodes=[("U1", "1"), ("R1", "1")])
    board.nets["NET_SCL"] = Net(id="NET_SCL", name="I2C_SCL", nodes=[("U1", "2"), ("R1", "2")])
    board.nets["GND"] = Net(id="GND", name="GND", nodes=[("U1", "3")])
    board.vias.append(Via(net_id="GND", x=85.0, y=15.0))
    return board

@app.websocket("/ws/cad")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    record_server_event("ws_cad_connect", status="CONNECTED")
    try:
        while True:
            payload = await websocket.receive_json()
            started = time.perf_counter()
            if isinstance(payload, dict) and payload.get("board"):
                input_board = BoardState.model_validate(payload["board"])
            elif isinstance(payload, dict) and payload.get("spec"):
                input_board = synthesize_board(DesignSpec.model_validate(payload["spec"]))
            elif isinstance(payload, dict) and payload.get("template"):
                input_board = build_template_board(str(payload["template"]))
            else:
                input_board = build_demo_board()

            result = PIPELINE.run(input_board)
            duration_ms = (time.perf_counter() - started) * 1000.0
            record_server_event(
                "ws_cad_run",
                status=result.status,
                duration_ms=duration_ms,
                details={
                    "attempts": result.attempts_used,
                    "drc_errors": len(result.board.errors),
                },
            )

            await websocket.send_json({
                "board": result.board.model_dump(),
                "report": {
                    "status": result.status,
                    "attempts": result.attempts_used,
                    "log": result.report,
                },
            })
    except WebSocketDisconnect:
        record_server_event("ws_cad_disconnect", status="DISCONNECTED")

@app.post("/export/kicad")
def export_kicad_endpoint(board: Optional[BoardState] = Body(default=None)) -> Response:
    """Exportiert ein Board als KiCad-v8-Datei.

    Ohne Body: Demo-Board inkl. Pipeline-Lauf (Platzierung, Routing, DRC).
    Mit BoardState-JSON: Export genau dieses Boards.
    """
    started = time.perf_counter()
    if board is None:
        board = PIPELINE.run(build_demo_board()).board
    content = export_kicad_pcb(board)
    record_server_event(
        "export_kicad",
        duration_ms=(time.perf_counter() - started) * 1000.0,
        details={"bytes": len(content.encode("utf-8"))},
    )
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="board.kicad_pcb"'},
    )

@app.post("/export/step")
def export_step_endpoint(board: Optional[BoardState] = Body(default=None)) -> Response:
    """Exportiert Platine + Bauteilkörper als 3D-STEP-Modell (AP214).

    Ohne Body: Demo-Board inkl. Pipeline-Lauf; mit BoardState-JSON: dieses Board.
    """
    started = time.perf_counter()
    if board is None:
        board = PIPELINE.run(build_demo_board()).board
    content = generate_step(board)
    record_server_event(
        "export_step",
        duration_ms=(time.perf_counter() - started) * 1000.0,
        details={"bytes": len(content.encode("utf-8"))},
    )
    return Response(
        content=content,
        media_type="application/step",
        headers={"Content-Disposition": 'attachment; filename="board.step"'},
    )

@app.post("/export/enclosure")
def export_enclosure_endpoint(board: Optional[BoardState] = Body(default=None)) -> Response:
    """Erzeugt ein parametrisches OpenSCAD-Gehäuse (Tray + Deckel).

    Ohne Body: Demo-Board inkl. Pipeline-Lauf; mit BoardState-JSON: dieses Board.
    """
    started = time.perf_counter()
    if board is None:
        board = PIPELINE.run(build_demo_board()).board
    content = generate_openscad_case(board)
    record_server_event(
        "export_enclosure",
        duration_ms=(time.perf_counter() - started) * 1000.0,
        details={"bytes": len(content.encode("utf-8"))},
    )
    return Response(
        content=content,
        media_type="application/x-openscad",
        headers={"Content-Disposition": 'attachment; filename="case.scad"'},
    )

def _assembly_board(board: Optional[BoardState]) -> BoardState:
    """Board-Quelle für Fertigungsdaten: übergebenes Board oder Demo-Pipeline."""
    return board if board is not None else PIPELINE.run(build_demo_board()).board

@app.get("/export/bom")
def export_bom_endpoint(board: Optional[BoardState] = Body(default=None)) -> Response:
    """Stückliste (BOM) als JLCPCB-CSV-Download (bom.csv).

    Ohne Body: Demo-Board inkl. Pipeline-Lauf; mit BoardState-JSON: dieses Board.
    """
    started = time.perf_counter()
    content = generate_jlcpcb_bom(_assembly_board(board))
    record_server_event(
        "export_bom",
        duration_ms=(time.perf_counter() - started) * 1000.0,
        details={"bytes": len(content.encode("utf-8"))},
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="bom.csv"'},
    )

@app.get("/export/cpl")
def export_cpl_endpoint(board: Optional[BoardState] = Body(default=None)) -> Response:
    """Bestückdaten (CPL/Pick & Place) als JLCPCB-CSV-Download (cpl.csv)."""
    started = time.perf_counter()
    content = generate_jlcpcb_cpl(_assembly_board(board))
    record_server_event(
        "export_cpl",
        duration_ms=(time.perf_counter() - started) * 1000.0,
        details={"bytes": len(content.encode("utf-8"))},
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="cpl.csv"'},
    )

@app.get("/health")
def health_endpoint() -> JSONResponse:
    return JSONResponse(
        content={
            "status": "ok",
            "service": "eda-multiagent-core",
            "version": app.version,
            "platform": "unified-deterministic-studio",
        }
    )


@app.get("/capabilities")
def capabilities_endpoint(ram_gb: float = 8.0) -> JSONResponse:
    try:
        resource_plan = plan_resources(
            ram_gb=ram_gb, vram_gb=0.0, quality_profile="deterministic_strict"
        ).__dict__
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return JSONResponse(
        content={
            "service": "eda-multiagent-core",
            "policy": "deterministic-solvers; quality-first without DRC relaxation",
            "solvers": ["z3-pin-multiplex", "force-directed-placer", "pathfinder-astar", "spatial-hash-drc"],
            "exporters": ["kicad-v8", "rs274x-gerber", "excellon-drill", "gerber-verifier", "panelizer-vcut-mousebites", "step-ap214", "openscad-enclosure", "build123d-enclosure-validator", "jlcpcb-bom", "jlcpcb-cpl", "sha256-manifest"],
            "templates": [t["id"] for t in list_templates()],
            "kicad_cli": detect_kicad_cli(),
            "resource_plan": resource_plan,
        }
    )


@app.get("/templates")
def list_templates_endpoint() -> JSONResponse:
    return JSONResponse(content={"templates": list_templates()})


@app.post("/design/synthesize")
def synthesize_design_endpoint(spec: DesignSpec) -> JSONResponse:
    """Synthetisiert ein DesignSpec deterministisch in BoardState + Pipeline-Lauf."""
    started = time.perf_counter()
    try:
        board = synthesize_board(spec)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    result = PIPELINE.run(board)
    record_server_event(
        "design_synthesize",
        status=result.status,
        duration_ms=(time.perf_counter() - started) * 1000.0,
        details={"name": spec.name, "drc_errors": len(result.board.errors)},
    )
    return JSONResponse(
        content={
            "status": result.status,
            "attempts_used": result.attempts_used,
            "report": result.report,
            "board": result.board.model_dump(),
        }
    )


@app.post("/export/gerber")
def export_gerber_endpoint(board: Optional[BoardState] = Body(default=None)) -> JSONResponse:
    """Exportiert RS-274X-Gerber-Layer (F_Cu, B_Cu, Edge_Cuts) + Excellon-Drill inkl. SHA-256-Manifest."""
    started = time.perf_counter()
    target_board = _assembly_board(board)
    bundle = export_gerber_with_manifest(target_board)
    record_server_event(
        "export_gerber",
        duration_ms=(time.perf_counter() - started) * 1000.0,
        details={"manifest_sha256": bundle["manifest"]["manifest_sha256"]},
    )
    return JSONResponse(content=bundle)


@app.post("/export/gerber/verify")
def verify_gerber_endpoint(board: Optional[BoardState] = Body(default=None)) -> JSONResponse:
    """Exportiert und prueft RS-274X-Gerber + Excellon strukturell inkl. Composite-SVG."""
    target_board = _assembly_board(board)
    files = export_gerber_bundle(target_board)
    report = verify_and_render_gerbers(files)
    return JSONResponse(content=report)


@app.get("/parts/search")
def search_parts_endpoint(q: str = "", category: Optional[str] = None, limit: int = 25) -> JSONResponse:
    """Durchsucht den lokalen LCSC/JLCPCB-SQLite-Katalog."""
    parts = DEFAULT_LIBRARY.search_parts(q, category=category, limit=limit)
    return JSONResponse(content={"count": len(parts), "parts": [p.model_dump() for p in parts]})


@app.post("/export/panel")
def export_panel_endpoint(
    rows: int = 2,
    cols: int = 2,
    rail_width_mm: float = 5.0,
    spacing_mm: float = 2.0,
    cut_mode: str = "vcut",
    board: Optional[BoardState] = Body(default=None),
) -> JSONResponse:
    """Erzeugt einen deterministischen Fertigungsnutzen (V-Cut / Mousebites) inkl. Gerber/Excellon."""
    target_board = _assembly_board(board)
    try:
        cfg = PanelConfig(
            rows=rows,
            cols=cols,
            rail_width_mm=rail_width_mm,
            spacing_mm=spacing_mm,
            cut_mode=cut_mode,  # type: ignore[arg-type]
        )
        panel = panelize_board(target_board, cfg)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return JSONResponse(content=panel)


@app.post("/export/enclosure/validate")
def validate_enclosure_endpoint(
    wall_thickness_mm: float = 2.4,
    pcb_clearance_mm: float = 0.6,
    internal_height_mm: float = 18.0,
    fillet_radius_mm: float = 1.0,
    include_build123d_script: bool = True,
    board: Optional[BoardState] = Body(default=None),
) -> JSONResponse:
    """Validiert Gehaeuse-DFM-Invarianten und liefert optional ein ausfuehrbares build123d-Skript."""
    target_board = _assembly_board(board)
    params = EnclosureParams(
        wall_thickness_mm=wall_thickness_mm,
        pcb_clearance_mm=pcb_clearance_mm,
        internal_height_mm=internal_height_mm,
        fillet_radius_mm=fillet_radius_mm,
    )
    report = validate_enclosure_geometry(target_board, params)
    if include_build123d_script:
        report["build123d_script"] = generate_build123d_script(target_board, params)
    return JSONResponse(content=report)


@app.post("/export/assembly/check-3d")
def check_3d_assembly_endpoint(
    internal_height_mm: float = 18.0,
    pcb_clearance_mm: float = 0.6,
    board: Optional[BoardState] = Body(default=None),
) -> JSONResponse:
    """Prueft 3D-Interferenzen zwischen Platine, Bauteilen und Gehaeuse-Deckel."""
    target_board = _assembly_board(board)
    enc = EnclosureParams(internal_height_mm=internal_height_mm, pcb_clearance_mm=pcb_clearance_mm)
    return JSONResponse(content=check_3d_assembly_collisions(target_board, enclosure=enc))


@app.post("/design/compile-hac")
def compile_hac_endpoint(payload: dict = Body(...)) -> JSONResponse:
    """Kompiliert Hardware-as-Code (Text-Source) nach formalem Z3-Elektrik-Beweis in BoardState."""
    source = str(payload.get("source", ""))
    if not source.strip():
        raise HTTPException(status_code=422, detail="Feld 'source' (HAC-Quellcode) darf nicht leer sein")
    try:
        res = compile_hac_to_board(source)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not res["compiled"]:
        return JSONResponse(status_code=409, content={"compiled": False, "proof": res["proof"]})
    return JSONResponse(
        content={"compiled": True, "proof": res["proof"], "board": res["board"].model_dump()}
    )


@app.post("/simulate/physics")
def simulate_physics_endpoint(
    copper_oz: float = 1.0,
    ambient_c: float = 25.0,
    include_openems_script: bool = False,
    board: Optional[BoardState] = Body(default=None),
) -> JSONResponse:
    """Fuehrt IPC-2152-Thermik-, IR-Drop-, Crosstalk- & S-Parameter-Pre-Signoff durch."""
    target_board = _assembly_board(board)
    report = simulate_board_physics(target_board, copper_oz=copper_oz, ambient_c=ambient_c)
    if include_openems_script:
        report["openems_script"] = generate_openems_fdtd_script(target_board)
    return JSONResponse(content=report)


@app.get("/kicad9/ipc/status")
def kicad9_ipc_status_endpoint() -> JSONResponse:
    """Liefert den Status der KiCad-9-IPC-API-Bridge inkl. Beispiel-Sync-Payload."""
    target_board = _assembly_board(None)
    return JSONResponse(
        content={
            "ipc": detect_kicad9_ipc(),
            "sync_preview": build_kicad9_ipc_sync_payload(target_board),
        }
    )


@app.post("/jobs")
def create_eda_job_endpoint(board: Optional[BoardState] = Body(default=None)) -> JSONResponse:
    """Fuehrt einen atomar persistierten, SHA-256-gesicherten EDA-Pipeline-Job aus."""
    target = board if board is not None else build_demo_board()
    record = _job_runner().submit_and_run(target)
    return JSONResponse(content=record)


@app.get("/jobs/{job_id}")
def get_eda_job_endpoint(job_id: str) -> JSONResponse:
    try:
        record = _job_runner().get_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return JSONResponse(content=record)


@app.post("/export/manifest")
def export_manifest_endpoint(board: Optional[BoardState] = Body(default=None)) -> JSONResponse:
    """Erzeugt ein SHA-256-Provenienz-Manifest fuer alle Fertigungs-Exports."""
    started = time.perf_counter()
    target_board = _assembly_board(board)
    artifacts = {
        "board.kicad_pcb": export_kicad_pcb(target_board),
        "board.step": generate_step(target_board),
        "case.scad": generate_openscad_case(target_board),
        "bom.csv": generate_jlcpcb_bom(target_board),
        "cpl.csv": generate_jlcpcb_cpl(target_board),
    }
    manifest = build_export_manifest(artifacts, summarize_board(target_board))
    record_server_event(
        "export_manifest",
        duration_ms=(time.perf_counter() - started) * 1000.0,
        details={
            "artifact_count": manifest["artifact_count"],
            "manifest_sha256": manifest["manifest_sha256"],
        },
    )
    return JSONResponse(content=manifest)

frontend_dir = Path(__file__).resolve().parent.parent / "frontend"

@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(frontend_dir / "cad_viewer_v2.html")

app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("EDA_SERVER_HOST", "127.0.0.1")
    port = int(os.environ.get("EDA_SERVER_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
