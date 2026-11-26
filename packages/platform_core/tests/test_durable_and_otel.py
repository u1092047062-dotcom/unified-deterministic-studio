"""Tests fuer DurableWorkflowEngine (SQLite WAL) & OpenTelemetry OTLP-Tracer (Phase 4)."""

from __future__ import annotations

import pytest

from platform_core.durable_workflow import (
    BudgetCircuitBreakerError,
    ConfigMismatchError,
    DurableWorkflowEngine,
)
from platform_core.otel_tracing import PlatformTracer, parse_traceparent


def test_durable_workflow_replays_completed_steps_after_crash(tmp_path):
    engine = DurableWorkflowEngine(tmp_path / "workflows.sqlite")
    cfg = {"template": "esp32_i2c_sensor", "quality_profile": "museum_fidelity"}
    wf = engine.start_or_resume(
        name="closed-loop-demo", domain="eda", config=cfg, workflow_id="wf-crash-test", budget_usd=2.0
    )
    assert wf["status"] == "running"

    calls = {"step1": 0, "step2": 0}

    def step1():
        calls["step1"] += 1
        return {"pinout": {"SDA": "GPIO21"}}

    def step2_flaky():
        calls["step2"] += 1
        if calls["step2"] == 1:
            raise RuntimeError("Simulated GPU spot preemption")
        return {"routed_traces": 4}

    r1 = engine.run_step("wf-crash-test", "solve_pinout", step1, estimated_cost_usd=0.10)
    assert r1["replayed"] is False
    assert calls["step1"] == 1

    # Schritt 2 stuerzt beim ersten Versuch ab
    with pytest.raises(RuntimeError, match="preemption"):
        engine.run_step("wf-crash-test", "route_board", step2_flaky, estimated_cost_usd=0.25, max_retries=1)

    assert engine.get_workflow("wf-crash-test")["status"] == "failed"

    # Config-Hash-Sperre lehnt geaenderte Konfiguration bei Resume strikt ab
    with pytest.raises(ConfigMismatchError):
        engine.start_or_resume(
            name="closed-loop-demo",
            domain="eda",
            config={"template": "different"},
            workflow_id="wf-crash-test",
        )

    # Neustart mit identischer Konfiguration: step1 wird aus dem Journal REPLAYED (nicht neu ausgefuehrt!)
    engine.start_or_resume(name="closed-loop-demo", domain="eda", config=cfg, workflow_id="wf-crash-test")
    r1_replay = engine.run_step("wf-crash-test", "solve_pinout", step1, estimated_cost_usd=0.10)
    assert r1_replay["replayed"] is True
    assert calls["step1"] == 1  # exakt einmal ausgefuehrt!

    r2 = engine.run_step("wf-crash-test", "route_board", step2_flaky, estimated_cost_usd=0.25)
    assert r2["replayed"] is False
    assert r2["output"] == {"routed_traces": 4}

    final_wf = engine.complete_workflow("wf-crash-test")
    assert final_wf["status"] == "completed"
    assert final_wf["spent_usd"] == pytest.approx(0.35)


def test_durable_workflow_budget_circuit_breaker(tmp_path):
    engine = DurableWorkflowEngine(tmp_path / "budget.sqlite")
    engine.start_or_resume(
        name="gpu-batch", domain="art", config={"model": "SeedVR2"}, workflow_id="wf-budget", budget_usd=0.50
    )
    engine.run_step("wf-budget", "tile_1", lambda: {"ok": True}, estimated_cost_usd=0.30)

    with pytest.raises(BudgetCircuitBreakerError):
        engine.run_step("wf-budget", "tile_2", lambda: {"ok": True}, estimated_cost_usd=0.30)

    state = engine.get_workflow("wf-budget")
    assert state["status"] == "budget_halted"
    assert state["spent_usd"] == pytest.approx(0.30)


def test_otel_tracer_w3c_traceparent_otlp_export_and_secret_redaction(tmp_path):
    span_file = tmp_path / "spans.jsonl"
    tracer = PlatformTracer("unified-studio-test", span_log_path=span_file)
    fake_secret = "sk-" + ("A" * 24)

    with tracer.start_span("root_pipeline", attributes={"api_key": fake_secret, "board": "esp32"}) as root:
        parsed = parse_traceparent(root["traceparent"])
        assert parsed is not None
        assert parsed["trace_id"] == root["traceId"]
        with tracer.start_span(
            "child_solver",
            trace_id=root["traceId"],
            parent_span_id=root["spanId"],
            attributes={"drc_errors": 0},
        ):
            pass

    otlp = tracer.export_otlp_json()
    spans = otlp["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(spans) == 2
    assert fake_secret not in span_file.read_text(encoding="utf-8")
    assert len(PlatformTracer.read_recent_spans(span_file)) == 2
