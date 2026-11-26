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
from core.kicad_export import export_kicad_pcb
from core.manufacturing_export import generate_jlcpcb_bom, generate_jlcpcb_cpl
from core.step_export import generate_step
from core.enclosure_gen import generate_openscad_case
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
            await websocket.receive_json()
            started = time.perf_counter()
            result = PIPELINE.run(build_demo_board())
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
            "exporters": ["kicad-v8", "step-ap214", "openscad-enclosure", "jlcpcb-bom", "jlcpcb-cpl", "sha256-manifest"],
            "resource_plan": resource_plan,
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
