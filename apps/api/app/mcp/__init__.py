"""Optional official MCP transport; importing this package needs no SDK."""

from app.mcp.server import GatewayOptions, build_mcp_gateway

__all__ = ["GatewayOptions", "build_mcp_gateway"]
