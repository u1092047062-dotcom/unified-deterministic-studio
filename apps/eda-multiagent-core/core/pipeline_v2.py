"""LangGraph Orchestrator: Pinout-Multiplexing -> Platzierung -> Routing -> DRC -> Feedback-Loop.

Deterministische Pipeline: Jeder Knoten ruft ausschließlich die mathe-
matischen Kernmodule auf (solver.py, placer.py, router_v2.py,
spatial_drc.py). Bei DRC-Verstößen eskaliert die Schleife deterministisch
über einen Eskalationsplan (mehr Router-Iterationen, feineres Raster);
es gibt keine heuristischen Freitext-Entscheidungen.
"""
import math
import time
from typing import Dict, List, Optional, Tuple, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from core.copper_pour import CopperPourGenerator
from core.diff_router import DiffPairRouter
from core.part_library import DEFAULT_LIBRARY, PartLibrary, PartNotFoundError, apply_part_to_component
from core.placer import ForceDirectedPlacer
from core.router_v2 import NegotiatedPathfinder
from core.schema import BoardState, DesignRules
from core.solver import MCUPackage, PinMultiplexSolver, PinRequirement
from core.spatial_drc import HighSpeedDRC
from core.telemetry_log import record_pipeline_run

STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_OK = "OK"
STATUS_SOLVER_FAILED = "SOLVER_FAILED"
STATUS_DRC_FAILED = "DRC_FAILED"

DEFAULT_ESCALATION: List[Tuple[int, float]] = [(6, 0.5), (12, 0.5), (24, 0.25)]

class PipelineState(TypedDict, total=False):
    board: BoardState
    pinout: Optional[Dict[str, int]]
    attempts: int
    report: List[str]
    status: str
    best_board: Optional[BoardState]
    best_errors: float

class PipelineResult(BaseModel):
    status: str
    board: BoardState
    attempts_used: int
    report: List[str] = Field(default_factory=list)
    pinout: Optional[Dict[str, int]] = None

class DesignPipeline:
    """Kompiliert den Feedback-Graph einmal und führt ihn pro `run` aus.

    assign_pinout -> place -> route -> drc -> (retry -> route | end)
    """

    def __init__(self,
                 mcu: Optional[MCUPackage] = None,
                 requirements: Optional[List[PinRequirement]] = None,
                 rules: Optional[DesignRules] = None,
                 max_attempts: int = 3,
                 escalation: Optional[List[Tuple[int, float]]] = None,
                 placer: Optional[ForceDirectedPlacer] = None,
                 enable_placement: bool = True,
                 pour_generator: Optional[CopperPourGenerator] = None,
                 enable_copper_pour: bool = True,
                 part_library: Optional[PartLibrary] = None,
                 enable_part_resolution: bool = True,
                 diff_router: Optional[DiffPairRouter] = None,
                 enable_diff_routing: bool = True):
        self.mcu = mcu
        self.requirements = requirements or []
        self.rules = rules or DesignRules()
        self.escalation = escalation or DEFAULT_ESCALATION
        # Der Eskalationsplan begrenzt die Versuchszahl konsistent.
        self.max_attempts = min(max_attempts, len(self.escalation))
        self.placer = placer if placer is not None else ForceDirectedPlacer()
        self.enable_placement = enable_placement
        self.pour_generator = pour_generator if pour_generator is not None else CopperPourGenerator()
        self.enable_copper_pour = enable_copper_pour
        self.part_library = part_library if part_library is not None else DEFAULT_LIBRARY
        self.enable_part_resolution = enable_part_resolution
        self.diff_router = diff_router if diff_router is not None else DiffPairRouter()
        self.enable_diff_routing = enable_diff_routing
        self.graph = self._build()

    # ---------- Graph ----------

    def _build(self):
        graph = StateGraph(PipelineState)
        # Dockingpunkt des LLM-Synthesizers: dessen BoardState-Entwurf landet
        # hier und wird auf reale Katalogteile normalisiert, bevor Z3 und
        # Router mit physikalischen Maßen arbeiten.
        graph.add_node("part_resolution_node", self._node_part_resolution)
        graph.add_node("assign_pinout", self._node_assign_pinout)
        graph.add_node("place", self._node_place)
        graph.add_node("route", self._node_route)
        graph.add_node("diff_pairs", self._node_diff_pairs)
        graph.add_node("pour", self._node_pour)
        graph.add_node("drc", self._node_drc)
        graph.add_edge(START, "part_resolution_node")
        graph.add_edge("part_resolution_node", "assign_pinout")
        graph.add_edge("assign_pinout", "place")
        graph.add_edge("place", "route")
        graph.add_edge("route", "diff_pairs")
        graph.add_edge("diff_pairs", "pour")
        graph.add_edge("pour", "drc")
        graph.add_conditional_edges("drc", self._decide, {"retry": "route", "end": END})
        return graph.compile()

    # ---------- Knoten ----------

    def _node_part_resolution(self, state: PipelineState) -> Dict:
        if state["status"] != STATUS_IN_PROGRESS or not self.enable_part_resolution:
            return {}
        board = state["board"]
        report = list(state["report"])
        resolved, warnings = 0, 0
        for comp in board.components.values():
            if comp.lcsc_id and comp.mpn:
                resolved += 1  # bereits vom Synthesizer referenziert
                continue
            try:
                part = self.part_library.resolve_part(
                    comp.category, comp.value or comp.name, comp.footprint)
            except PartNotFoundError:
                warnings += 1
                report.append(f"Teile-Auflösung: {comp.id} ({comp.category}) nicht auflösbar "
                              f"- bleibt ohne Katalogreferenz")
                continue
            apply_part_to_component(part, comp)
            resolved += 1
        report.append(f"Teile-Auflösung: {resolved} Bauteile mit LCSC-Referenz versehen"
                      + (f", {warnings} unaufgelöst" if warnings else ""))
        return {"report": report}

    def _node_assign_pinout(self, state: PipelineState) -> Dict:
        if self.mcu is None or not self.requirements:
            return {}
        pinout = PinMultiplexSolver.solve_pinout(self.mcu, self.requirements)
        report = list(state["report"])
        if pinout is None:
            report.append("Pinout-Solver: keine zulässige Belegung (UNSAT) - Abbruch.")
            return {"pinout": None, "status": STATUS_SOLVER_FAILED, "report": report}
        report.append(f"Pinout-Multiplexing: {len(pinout)} Funktionen zugeteilt: {pinout}")
        return {"pinout": pinout, "report": report}

    def _node_place(self, state: PipelineState) -> Dict:
        if state["status"] != STATUS_IN_PROGRESS or not self.enable_placement:
            return {}
        placement = self.placer.place(state["board"])
        report = list(state["report"])
        report.append(f"Platzierung: {len(placement.board.components)} Bauteile, "
                      f"Netzlänge {placement.wirelength_after:.1f} mm "
                      f"(vorher {placement.wirelength_before:.1f} mm), "
                      f"Restüberlappung {placement.residual_overlap:.2f} mm")
        return {"board": placement.board, "report": report}

    def _node_route(self, state: PipelineState) -> Dict:
        if state["status"] != STATUS_IN_PROGRESS:
            return {}
        attempt = state["attempts"]
        max_iterations, pitch = self.escalation[attempt]
        board = state["board"]

        router = NegotiatedPathfinder(board, pitch=pitch, trace_width=self.rules.min_trace_width)
        board.traces, board.vias = router.solve_ripup_and_reroute(max_iterations=max_iterations)

        report = list(state["report"])
        report.append(f"Versuch {attempt + 1}: pitch={pitch}, max_iterations={max_iterations} "
                      f"-> {len(board.traces)} Traces, {len(board.vias)} Vias")
        return {"attempts": attempt + 1, "report": report}

    def _node_diff_pairs(self, state: PipelineState) -> Dict:
        if state["status"] != STATUS_IN_PROGRESS or not self.enable_diff_routing:
            return {}
        lines = self.diff_router.route_pairs(state["board"])
        if not lines:
            return {}
        return {"report": list(state["report"]) + lines}

    def _node_pour(self, state: PipelineState) -> Dict:
        if state["status"] != STATUS_IN_PROGRESS or not self.enable_copper_pour:
            return {}
        board = state["board"]
        pours = self.pour_generator.generate(board)
        board.copper_pours = pours
        vertices = sum(len(p.outer) + sum(len(h) for h in p.holes) for p in pours)
        report = list(state["report"])
        report.append(f"Kupferfläche ({self.pour_generator.net_id}, {self.pour_generator.layer}): "
                      f"{len(pours)} Region(en), {vertices} Stützpunkte, "
                      f"{sum(len(p.holes) for p in pours)} Aussparungen")
        return {"report": report}

    def _node_drc(self, state: PipelineState) -> Dict:
        board = state["board"]
        board.errors = HighSpeedDRC.check_board(board, self.rules)

        report = list(state["report"])
        report.append(f"Versuch {state['attempts']}: DRC meldet {len(board.errors)} Fehler")

        update: Dict = {"report": report}
        if len(board.errors) < state["best_errors"]:
            update["best_errors"] = len(board.errors)
            update["best_board"] = board.model_copy(deep=True)
        return update

    def _decide(self, state: PipelineState) -> str:
        if state["status"] != STATUS_IN_PROGRESS:
            return "end"
        if not state["board"].errors:
            return "end"
        if state["attempts"] >= self.max_attempts:
            return "end"
        return "retry"

    # ---------- API ----------

    def run(self, board: BoardState) -> PipelineResult:
        started = time.perf_counter()
        state: PipelineState = {
            "board": board.model_copy(deep=True),
            "pinout": None,
            "attempts": 0,
            "report": [],
            "status": STATUS_IN_PROGRESS,
            "best_board": None,
            "best_errors": math.inf,
        }
        try:
            final = self.graph.invoke(state)
        except Exception as exc:
            duration_ms = (time.perf_counter() - started) * 1000.0
            record_pipeline_run(
                status="ERROR",
                attempts=0,
                duration_ms=duration_ms,
                components=len(board.components),
                nets=len(board.nets),
                traces=len(board.traces),
                vias=len(board.vias),
                drc_errors=len(board.errors),
                extra={"error_type": type(exc).__name__},
            )
            raise

        result_board = final["best_board"] or final["board"]
        report = list(final["report"])
        if final["status"] == STATUS_SOLVER_FAILED:
            status = STATUS_SOLVER_FAILED
        elif not result_board.errors:
            status = STATUS_OK
            report.append(f"Erfolg nach {final['attempts']} Versuch(en).")
        else:
            status = STATUS_DRC_FAILED
            report.append(f"Abbruch nach {final['attempts']} Versuch(en); "
                          f"bestes Ergebnis enthält {len(result_board.errors)} DRC-Verstöße.")

        duration_ms = (time.perf_counter() - started) * 1000.0
        record_pipeline_run(
            status=status,
            attempts=final["attempts"],
            duration_ms=duration_ms,
            components=len(result_board.components),
            nets=len(result_board.nets),
            traces=len(result_board.traces),
            vias=len(result_board.vias),
            drc_errors=len(result_board.errors),
        )

        return PipelineResult(
            status=status,
            board=result_board,
            attempts_used=final["attempts"],
            report=report,
            pinout=final["pinout"],
        )
