"""
Claw Recall — Centralized Configuration

All configuration constants consolidated from db.py, index.py, web.py, mcp_server_sse.py.
Modules import from here instead of defining their own copies.
"""

import json
import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
# REPO_DIR is the claw-recall/ root (parent of claw_recall/ package)
REPO_DIR = Path(__file__).parent.parent

DB_PATH = Path(os.environ.get("CLAW_RECALL_DB", str(REPO_DIR / "convo_memory.db")))

DEFAULT_ARCHIVE_PATH = Path.home() / ".openclaw" / "agents-archive"
DEFAULT_SESSIONS_PATH = Path.home() / ".openclaw" / "agents"

EXCLUDE_CONF_PATH = REPO_DIR / "exclude.conf"
AGENTS_JSON_PATH = REPO_DIR / "agents.json"

# ── Embedding settings ─────────────────────────────────────────────────────────
EMBEDDING_MODEL = os.environ.get("CLAW_RECALL_EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_BATCH_SIZE = int(os.environ.get("CLAW_RECALL_EMBEDDING_BATCH", "20"))
EMBEDDING_DIM = int(os.environ.get("CLAW_RECALL_EMBEDDING_DIM", "1536"))
MIN_CONTENT_LENGTH = int(os.environ.get("CLAW_RECALL_MIN_CONTENT_LENGTH", "20"))

# ── Web server settings ────────────────────────────────────────────────────────
WEB_HOST = os.environ.get("CLAW_RECALL_WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("CLAW_RECALL_WEB_PORT", "8765"))

# ── MCP SSE server settings ────────────────────────────────────────────────────
MCP_SSE_HOST = os.environ.get("MCP_SSE_HOST", "0.0.0.0")
MCP_SSE_PORT = int(os.environ.get("MCP_SSE_PORT", "8766"))

# ── Watcher directories ────────────────────────────────────────────────────────
WATCH_DIRS = [
    Path.home() / ".openclaw" / "agents",
    Path.home() / ".openclaw" / "agents-archive",
    Path.home() / ".claude" / "projects",
]

# ── Agent name mapping ─────────────────────────────────────────────────────────

def _load_agent_names() -> dict:
    """Load agent name mapping from agents.json.

    Returns a dict mapping lowercase slot IDs to display names.
    """
    if AGENTS_JSON_PATH.exists():
        try:
            data = json.loads(AGENTS_JSON_PATH.read_text())
            return {k.lower(): v for k, v in data.get("agent_names", {}).items()}
        except Exception:
            pass
    return {}


AGENT_NAME_MAP = _load_agent_names()
