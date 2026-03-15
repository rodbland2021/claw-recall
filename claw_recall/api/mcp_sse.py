#!/usr/bin/env python3
"""
Claw Recall — MCP Server (Streamable HTTP transport)

Runs the same MCP server as mcp_stdio.py but over Streamable HTTP transport
instead of stdio. This is the recommended MCP transport -- each tool call is a
self-contained HTTP request (no persistent SSE session, no initialization race).

Usage:
    python3 -m claw_recall.api.mcp_sse                           # Default: 0.0.0.0:8766
    MCP_SSE_HOST=10.0.0.1 python3 -m claw_recall.api.mcp_sse    # Custom host
    MCP_SSE_PORT=9000 python3 -m claw_recall.api.mcp_sse         # Custom port

MCP client config:
    {
      "mcpServers": {
        "claw-recall": {
          "url": "http://<your-host>:8766/mcp"
        }
      }
    }

Environment variables:
    MCP_SSE_HOST        Host to bind to (default: 0.0.0.0)
    MCP_SSE_PORT        Port to bind to (default: 8766)
    MCP_SSE_ALLOWED_HOSTS  Extra allowed hosts, comma-separated (e.g. "10.0.0.5:*")
"""
import os

from claw_recall.config import MCP_SSE_HOST, MCP_SSE_PORT

# Import the mcp instance with all tools already registered
from claw_recall.api.mcp_stdio import mcp

if __name__ == "__main__":
    host = os.environ.get("MCP_SSE_HOST", MCP_SSE_HOST)
    port = int(os.environ.get("MCP_SSE_PORT", str(MCP_SSE_PORT)))

    # Override host/port on the settings object before run()
    mcp.settings.host = host
    mcp.settings.port = port

    # Allow connections from any host when binding to 0.0.0.0 (Tailscale, LAN, etc.)
    # The server is not exposed to the internet -- Tailscale handles access control.
    if host == "0.0.0.0":
        allowed = ["*:*"]
    else:
        allowed = [f"{host}:*", "127.0.0.1:*", "localhost:*"]

    extra = os.environ.get("MCP_SSE_ALLOWED_HOSTS", "")
    if extra:
        allowed.extend(h.strip() for h in extra.split(",") if h.strip())

    mcp.settings.transport_security.allowed_hosts = allowed
    mcp.settings.transport_security.allowed_origins = ["*"]

    # Preload embedding cache in background to avoid cold-start latency.
    # Without this, the first semantic search after startup (or after 4h idle)
    # takes ~100s to rebuild the cache, during which searches return empty.
    from claw_recall.search.engine import preload_embedding_cache
    preload_embedding_cache()

    print(f"Claw Recall MCP (Streamable HTTP) running at http://{host}:{port}/mcp")
    mcp.run(transport="streamable-http")
