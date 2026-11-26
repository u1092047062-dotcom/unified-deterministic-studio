"""Tests fuer A2A Closed-Loop Multi-Agent-Orchestrierung & Frontier-MCP-Tools (Phase 9)."""

from __future__ import annotations

from platform_mcp.a2a_orchestrator import (
    build_a2a_agent_card,
    dispatch_a2a_task,
    run_closed_loop_media_qc_agent,
    run_closed_loop_pcb_agent,
)
from platform_mcp.server import handle_jsonrpc_request, list_mcp_tools


def test_a2a_closed_loop_pcb_and_media_agents_end_to_end(tmp_path):
    db = tmp_path / "a2a_wf.sqlite"
    spans = tmp_path / "a2a_spans.jsonl"

    card = build_a2a_agent_card()
    assert card["protocol"] == "a2a.v1"
    assert len(card["skills"]) == 2

    pcb = run_closed_loop_pcb_agent(
        template_id="esp32_i2c_sensor", workflow_id="wf-pcb-1", db_path=db, span_log_path=spans
    )
    assert pcb["status"] == "completed"
    assert pcb["pipeline_status"] == "OK"
    assert pcb["drc_errors"] == 0
    assert pcb["physics_passed"] is True
    assert pcb["assembly_3d_collision_free"] is True
    assert pcb["enclosure_dfm_valid"] is True
    assert pcb["gerber_valid"] is True
    assert pcb["trust_verified"] is True
    assert pcb["steps_count"] == 5

    # Zweiter Aufruf mit gleicher workflow_id prueft idempotentes Durable-Replay!
    pcb_replay = dispatch_a2a_task(
        {"skill": "closed_loop_pcb_engineering", "input": {"template_id": "esp32_i2c_sensor", "workflow_id": "wf-pcb-1"}},
        db_path=db,
        span_log_path=spans,
    )
    assert pcb_replay["workflow_id"] == "wf-pcb-1"
    assert pcb_replay["trust_verified"] is True

    media = run_closed_loop_media_qc_agent(
        quality_profile="museum_fidelity", workflow_id="wf-media-1", db_path=db, span_log_path=spans
    )
    assert media["status"] == "completed"
    assert media["sota_tier"] == "tier_a_zero_hallucination"
    assert media["hallucination_gate_passed"] is True
    assert media["c2pa_ed25519_verified"] is True
    assert spans.is_file()


def test_frontier_mcp_toolset_has_12_tools():
    tools = list_mcp_tools()
    assert len(tools) >= 12
    resp = handle_jsonrpc_request(
        {
            "jsonrpc": "2.0",
            "id": 77,
            "method": "tools/call",
            "params": {"name": "art_route_sota", "arguments": {"quality_profile": "museum_fidelity"}},
        }
    )
    assert resp["result"]["isError"] is False
    assert resp["result"]["structuredContent"]["tier"] == "tier_a_zero_hallucination"
