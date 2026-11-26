from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from platform_mcp import MCP_PROTOCOL_VERSION, handle_jsonrpc_request, list_mcp_tools


def test_mcp_initialize_and_list_tools():
    init = handle_jsonrpc_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION

    listed = handle_jsonrpc_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in listed["result"]["tools"]}
    assert {
        "eda_list_templates",
        "eda_run_template",
        "eda_search_parts",
        "art_plan_tiles",
        "art_resource_plan",
        "platform_scan_secrets",
        "platform_status",
    } <= names
    assert len(list_mcp_tools()) >= 7


def test_mcp_tool_calls_eda_art_and_security():
    eda = handle_jsonrpc_request(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "eda_run_template", "arguments": {"template_id": "rp2040_power_node"}},
        }
    )
    sc = eda["result"]["structuredContent"]
    assert sc["status"] == "OK"
    assert sc["gerber_valid"] is True
    assert len(sc["manifest_sha256"]) == 64

    art = handle_jsonrpc_request(
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "art_resource_plan",
                "arguments": {"ram_gb": 7.82, "vram_gb": 4.0, "runtime_seconds": 3600, "hourly_rate": 1.0, "budget_usd": 0.5},
            },
        }
    )
    assert art["result"]["structuredContent"]["budget_gate"]["allowed"] is False

    syn_key = "sk-" + ("z" * 24)
    sec = handle_jsonrpc_request(
        {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {"name": "platform_scan_secrets", "arguments": {"text": f"key={syn_key}"}},
        }
    )
    assert sec["result"]["structuredContent"]["clean"] is False
    assert syn_key not in sec["result"]["structuredContent"]["redacted"]

    unknown = handle_jsonrpc_request(
        {"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {"name": "does_not_exist"}}
    )
    assert unknown["error"]["code"] == -32602
