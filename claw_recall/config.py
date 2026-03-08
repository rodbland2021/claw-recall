"""
Claw Recall — Centralized Configuration

All configuration constants consolidated from db.py, index.py, web.py, mcp_server_sse.py.
Modules import from here instead of defining their own copies.
"""

import json
import logging
import os
import re
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


# ── Secret redaction ──────────────────────────────────────────────────────────
# Patterns that match sensitive values (API keys, passwords, tokens, etc.).
# Each tuple: (compiled_regex, group_index_of_secret_value).
# The secret value group is replaced with [REDACTED]; surrounding context is kept.

_REDACT_PLACEHOLDER = "[REDACTED]"

# Additional patterns can be loaded from redact_patterns.conf (one regex per line).
REDACT_PATTERNS_PATH = REPO_DIR / "redact_patterns.conf"

_log = logging.getLogger("claw-recall")


def _build_redaction_patterns() -> list[tuple[re.Pattern, int]]:
    """Build the list of compiled (regex, secret_group_index) tuples."""
    # Each entry: (pattern_string, group_index_that_captures_the_secret)
    raw: list[tuple[str, int]] = [
        # ── Google OAuth ──
        (r'(GOCSPX-[A-Za-z0-9_-]{20,})', 1),
        (r'(\d{6,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com)', 1),

        # ── Tailscale keys ──
        (r'(tskey-(?:api|auth|client)-[A-Za-z0-9-]{20,})', 1),

        # ── AWS keys ──
        (r'(AKIA[0-9A-Z]{16})', 1),
        (r'(?i)(aws.{0,10}secret.{0,10}(?:key|access).{0,5}[=:]\s*["\']?)([A-Za-z0-9/+=]{30,})', 2),

        # ── Generic API keys / tokens (key=value or key: value) ──
        (r'(?i)(?:api[_-]?key|api[_-]?secret|secret[_-]?key|access[_-]?token|auth[_-]?token|bearer)\s*[=:]\s*["\']?([A-Za-z0-9_\-./+=]{20,})', 1),

        # ── Bearer tokens in headers ──
        (r'(?i)(?:Authorization|X-Agent-Token)["\']?\s*[=:]\s*["\']?(?:Bearer\s+)?([A-Za-z0-9_\-./+=]{20,})', 1),

        # ── Password patterns ──
        (r'(?i)(?:password|passwd|pass|pwd)\s*[=:]\s*["\']?(\S{6,})', 1),

        # ── OAuth cookie secrets (base64 with = padding, may use URL-safe chars) ──
        (r'(?i)(?:COOKIE_SECRET|cookie.secret)\s*[=:]\s*["\']?([A-Za-z0-9+/=_-]{20,})', 1),

        # ── SSH private keys ──
        (r'(-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----)', 1),

        # ── Slack tokens ──
        (r'(xox[bpras]-[A-Za-z0-9-]{10,})', 1),

        # ── GitHub tokens ──
        (r'(gh[ps]_[A-Za-z0-9]{30,})', 1),
        (r'(github_pat_[A-Za-z0-9_]{30,})', 1),

        # ── OpenAI keys ──
        (r'(sk-[A-Za-z0-9]{20,})', 1),

        # ── Anthropic keys ──
        (r'(sk-ant-[A-Za-z0-9_-]{20,})', 1),

        # ── Stripe keys ──
        (r'(sk_(?:test|live)_[A-Za-z0-9]{20,})', 1),
        (r'(pk_(?:test|live)_[A-Za-z0-9]{20,})', 1),

        # ── Sendgrid ──
        (r'(SG\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,})', 1),

        # ── Connection strings with passwords ──
        (r'(?i)(://[^:]+):([^@]{6,})@', 2),

        # ── Custom patterns from redact_patterns.conf ──
    ]

    # Load additional patterns from conf file
    if REDACT_PATTERNS_PATH.exists():
        for line in REDACT_PATTERNS_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                try:
                    compiled = re.compile(line)
                    raw.append((line, 1))
                except re.error as e:
                    _log.warning(f"Invalid redaction pattern in conf: {line!r} — {e}")

    compiled_patterns = []
    for pattern_str, group_idx in raw:
        try:
            compiled_patterns.append((re.compile(pattern_str), group_idx))
        except re.error as e:
            _log.warning(f"Failed to compile redaction pattern: {pattern_str!r} — {e}")

    return compiled_patterns


REDACTION_PATTERNS = _build_redaction_patterns()


def redact_secrets(text: str) -> str:
    """Replace secret values in text with [REDACTED].

    Applies all patterns from REDACTION_PATTERNS. Returns the text
    with only the secret portions replaced — surrounding context is preserved.
    """
    if not text:
        return text
    for pattern, group_idx in REDACTION_PATTERNS:
        def _replacer(m, gi=group_idx):
            # Replace only the captured secret group, keep the rest
            full = m.group(0)
            secret = m.group(gi)
            return full.replace(secret, _REDACT_PLACEHOLDER, 1)
        text = pattern.sub(_replacer, text)
    return text
