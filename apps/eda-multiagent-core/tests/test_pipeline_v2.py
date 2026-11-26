import pytest

from core.schema import BoardState, Component, DesignRules, Net, Pin, PinType
from core.router_v2 import NegotiatedPathfinder
from core.pipeline_v2 import DesignPipeline, PipelineResult, STATUS_DRC_FAILED, STATUS_OK, STATUS_SOLVER_FAILED
from core.solver import MCUPackage, PinRequirement


# ---------- Helfer ----------

def _two_net_board() -> BoardState:
    board = BoardState(width=40.0, height=20.0)
    board.components["U1"] = Component(
        id="U1", name="U1", category="IC", footprint="TEST",
        x=5.0, y=10.0, body_w=2.0, body_h=2.0,
        pins={
            "1": Pin(id="1", name="A", pin_type=PinType.OUTPUT, x=4.0, y=10.0),
            "2": Pin(id="2", name="B", pin_type=PinType.OUTPUT, x=4.0, y=12.0),
        },
    )
    board.components["R1"] = Component(
        id="R1", name="R1", category="R", footprint="TEST",
        x=35.0, y=10.0, body_w=2.0, body_h=2.0,
        pins={
            "1": Pin(id="1", name="A", pin_type=PinType.PASSIVE, x=36.0, y=10.0),
            "2": Pin(id="2", name="B", pin_type=PinType.PASSIVE, x=36.0, y=12.0),
        },
    )
    board.nets["N1"] = Net(id="N1", name="n1", nodes=[("U1", "1"), ("R1", "1")])
    board.nets["N2"] = Net(id="N2", name="n2", nodes=[("U1", "2"), ("R1", "2")])
    return board


def _mcu_sat() -> MCUPackage:
    return MCUPackage(name="ESP32", pin_capabilities={1: ["SDA"], 2: ["SCL"], 3: ["SDA", "GPIO"]})


def _mcu_unsat() -> MCUPackage:
    # SDA und SCL müssten beide auf Pin 1 -> Distinct erzwingt UNSAT
    return MCUPackage(name="ESP32", pin_capabilities={1: ["SDA", "SCL"]})


# ---------- Erfolgspfad ----------

def test_pipeline_success_first_attempt():
    board = _two_net_board()
    result = DesignPipeline().run(board)

    assert result.status == STATUS_OK
    assert result.attempts_used == 1
    assert result.board.errors == []
    assert len(result.board.traces) >= 2
    # Eingabe-Board bleibt unverändert (Pipeline arbeitet auf Kopie)
    assert board.traces == [] and board.vias == []


def test_pipeline_without_nets_is_ok():
    result = DesignPipeline().run(BoardState(width=20.0, height=20.0))
    assert result.status == STATUS_OK
    assert result.attempts_used == 1
    assert result.board.traces == []


def test_pipeline_records_pinout():
    result = DesignPipeline(
        mcu=_mcu_sat(),
        requirements=[PinRequirement(function_name="SDA", signal_type="I2C"),
                      PinRequirement(function_name="SCL", signal_type="I2C")],
    ).run(_two_net_board())

    assert result.status == STATUS_OK
    assert result.pinout is not None
    assert set(result.pinout) == {"SDA", "SCL"}
    assert any("Pinout-Multiplexing" in line for line in result.report)


def test_pipeline_applies_design_rules():
    # min_trace_width 0.5 zwingt den Router, breitere Traces zu setzen.
    board = _two_net_board()
    pipeline = DesignPipeline(rules=DesignRules(min_trace_width=0.5))
    result = pipeline.run(board)
    assert result.status == STATUS_OK
    assert all(t.width >= 0.5 for t in result.board.traces)


# ---------- Feedback-Loop ----------

def test_pipeline_escalates_after_drc_failure(monkeypatch):
    real_solve = NegotiatedPathfinder.solve_ripup_and_reroute
    calls = []

    def flaky(self, max_iterations=10):
        calls.append(max_iterations)
        traces, vias = real_solve(self, max_iterations=max_iterations)
        if len(calls) == 1:
            # Erster Versuch produziert absichtlich einen DRC-Verstoß.
            for t in traces:
                t.width = 0.05
        return traces, vias

    monkeypatch.setattr(NegotiatedPathfinder, "solve_ripup_and_reroute", flaky)
    result = DesignPipeline(escalation=[(6, 0.5), (12, 0.5)]).run(_two_net_board())

    assert result.status == STATUS_OK
    assert result.attempts_used == 2
    assert calls == [6, 12]
    assert result.board.errors == []
    assert any("Versuch 1" in line for line in result.report)


def test_pipeline_reports_persistent_drc_failure(monkeypatch):
    real_solve = NegotiatedPathfinder.solve_ripup_and_reroute

    def always_broken(self, max_iterations=10):
        traces, vias = real_solve(self, max_iterations=max_iterations)
        for t in traces:
            t.width = 0.05
        return traces, vias

    monkeypatch.setattr(NegotiatedPathfinder, "solve_ripup_and_reroute", always_broken)
    result = DesignPipeline(escalation=[(6, 0.5), (12, 0.5)]).run(_two_net_board())

    assert result.status == STATUS_DRC_FAILED
    assert result.attempts_used == 2
    assert result.board.errors, "Bestes Ergebnis muss die Verstöße enthalten"
    assert any("Abbruch" in line for line in result.report)


def test_pipeline_keeps_best_attempt(monkeypatch):
    real_solve = NegotiatedPathfinder.solve_ripup_and_reroute
    calls = []

    def worse_then_worst(self, max_iterations=10):
        calls.append(max_iterations)
        traces, vias = real_solve(self, max_iterations=max_iterations)
        # Versuch 1: 1 Verstoß; Versuch 2: alle Traces verstoßen (schlechter).
        # Die Pipeline muss das Ergebnis von Versuch 1 behalten.
        broken = len(traces) if len(calls) >= 2 else 1
        for t in traces[:broken]:
            t.width = 0.05
        return traces, vias

    monkeypatch.setattr(NegotiatedPathfinder, "solve_ripup_and_reroute", worse_then_worst)
    result = DesignPipeline(escalation=[(6, 0.5), (12, 0.5)]).run(_two_net_board())

    assert result.status == STATUS_DRC_FAILED
    assert result.attempts_used == 2
    assert len(result.board.errors) == 1  # bestes Ergebnis, nicht das letzte


# ---------- Solver-Ast ----------

def test_pipeline_solver_failure_stops_before_routing():
    board = _two_net_board()
    result = DesignPipeline(
        mcu=_mcu_unsat(),
        requirements=[PinRequirement(function_name="SDA", signal_type="I2C"),
                      PinRequirement(function_name="SCL", signal_type="I2C")],
    ).run(board)

    assert result.status == STATUS_SOLVER_FAILED
    assert result.pinout is None
    assert result.attempts_used == 0
    assert result.board.traces == []
    assert any("UNSAT" in line for line in result.report)


# ---------- Platzierung ----------

def test_pipeline_places_coincident_components_before_routing():
    board = BoardState(width=40.0, height=20.0)
    board.components["U1"] = Component(id="U1", name="U1", category="IC", footprint="TEST",
                                       x=20.0, y=10.0,
                                       pins={"1": Pin(id="1", name="A", pin_type=PinType.OUTPUT,
                                                      x=22.0, y=11.0)})
    board.components["R1"] = Component(id="R1", name="R1", category="R", footprint="TEST",
                                       x=20.0, y=10.0,
                                       pins={"1": Pin(id="1", name="A", pin_type=PinType.PASSIVE,
                                                      x=18.0, y=9.0)})
    board.nets["N1"] = Net(id="N1", name="n1", nodes=[("U1", "1"), ("R1", "1")])

    result = DesignPipeline().run(board)

    assert result.status == STATUS_OK
    assert any("Platzierung" in line for line in result.report)
    # Zusammenliegende Bauteile wurden auseinandergefahren
    (u, r) = (result.board.components["U1"], result.board.components["R1"])
    assert (u.x, u.y) != (r.x, r.y)
    assert len(result.board.traces) >= 1


def test_pipeline_can_skip_placement():
    board = _two_net_board()
    result = DesignPipeline(enable_placement=False).run(board)
    assert result.status == STATUS_OK
    assert not any("Platzierung" in line for line in result.report)


# ---------- Kupferflächen ----------

def test_pipeline_generates_copper_pour():
    result = DesignPipeline().run(_two_net_board())
    assert result.status == STATUS_OK
    assert len(result.board.copper_pours) >= 1
    pour = result.board.copper_pours[0]
    assert pour.net_id == "GND"
    assert pour.layer == "BOTTOM"
    assert any("Kupferfläche" in line for line in result.report)


def test_pipeline_can_skip_copper_pour():
    result = DesignPipeline(enable_copper_pour=False).run(_two_net_board())
    assert result.board.copper_pours == []
    assert not any("Kupferfläche" in line for line in result.report)


# ---------- Determinismus ----------

def test_pipeline_is_deterministic():
    def snapshot() -> PipelineResult:
        return DesignPipeline(
            mcu=_mcu_sat(),
            requirements=[PinRequirement(function_name="SDA", signal_type="I2C"),
                          PinRequirement(function_name="SCL", signal_type="I2C")],
        ).run(_two_net_board())

    a, b = snapshot(), snapshot()
    assert a.status == b.status
    assert a.attempts_used == b.attempts_used
    assert a.report == b.report
    assert a.board.model_dump_json() == b.board.model_dump_json()
