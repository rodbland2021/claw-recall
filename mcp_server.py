"""Backward-compatible wrapper — imports from claw_recall.api.mcp_stdio."""
from claw_recall.api.mcp_stdio import *  # noqa: F401,F403

if __name__ == "__main__":
    from claw_recall.api.mcp_stdio import mcp
    mcp.run()
