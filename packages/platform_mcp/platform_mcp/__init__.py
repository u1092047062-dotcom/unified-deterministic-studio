"""Model Context Protocol (MCP) JSON-RPC 2.0 Server fuer Unified Deterministic Studio."""

from platform_mcp.server import MCP_PROTOCOL_VERSION, handle_jsonrpc_request, list_mcp_tools

__all__ = ["MCP_PROTOCOL_VERSION", "handle_jsonrpc_request", "list_mcp_tools"]
