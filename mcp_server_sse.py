"""Backward-compatible wrapper — imports from claw_recall.api.mcp_sse."""
import os
from claw_recall.config import MCP_SSE_HOST, MCP_SSE_PORT
from claw_recall.api.mcp_stdio import mcp

if __name__ == "__main__":
    host = os.environ.get("MCP_SSE_HOST", MCP_SSE_HOST)
    port = int(os.environ.get("MCP_SSE_PORT", str(MCP_SSE_PORT)))
    mcp.settings.host = host
    mcp.settings.port = port
    if host == "0.0.0.0":
        allowed = ["*:*"]
    else:
        allowed = [f"{host}:*", "127.0.0.1:*", "localhost:*"]
    extra = os.environ.get("MCP_SSE_ALLOWED_HOSTS", "")
    if extra:
        allowed.extend(h.strip() for h in extra.split(",") if h.strip())
    mcp.settings.transport_security.allowed_hosts = allowed
    mcp.settings.transport_security.allowed_origins = ["*"]
    print(f"Claw Recall MCP (Streamable HTTP) running at http://{host}:{port}/mcp")
    mcp.run(transport="streamable-http")
