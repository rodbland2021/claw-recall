#!/usr/bin/env python3
"""
Claw Recall — MCP Server (SSE/HTTP transport)

Runs the same MCP server as mcp_server.py but over HTTP (SSE transport)
instead of stdio. This allows remote agents to connect directly via HTTP.

Usage:
    python3 mcp_server_sse.py                           # Default: 0.0.0.0:8766
    MCP_SSE_HOST=10.0.0.1 python3 mcp_server_sse.py    # Custom host
    MCP_SSE_PORT=9000 python3 mcp_server_sse.py         # Custom port

MCP client config:
    {
      "mcpServers": {
        "claw-recall": {
          "url": "http://<your-host>:8766/sse"
        }
      }
    }

Environment variables:
    MCP_SSE_HOST    Host to bind to (default: 0.0.0.0)
    MCP_SSE_PORT    Port to bind to (default: 8766)
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Import the mcp instance with all tools already registered
from mcp_server import mcp

if __name__ == "__main__":
    host = os.environ.get("MCP_SSE_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_SSE_PORT", "8766"))

    # Override host/port on the settings object before run()
    mcp.settings.host = host
    mcp.settings.port = port

    # Allow connections from the bound host + common local addresses.
    # Add more allowed hosts via MCP_SSE_ALLOWED_HOSTS env var (comma-separated).
    allowed = [f"{host}:*", "127.0.0.1:*", "localhost:*"]
    extra = os.environ.get("MCP_SSE_ALLOWED_HOSTS", "")
    if extra:
        allowed.extend(h.strip() for h in extra.split(",") if h.strip())
    mcp.settings.transport_security.allowed_hosts = allowed
    mcp.settings.transport_security.allowed_origins = [f"http://{h}" for h in allowed]

    print(f"Claw Recall MCP (SSE) running at http://{host}:{port}/sse")
    mcp.run(transport="sse")
