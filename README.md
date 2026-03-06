# Claw Recall

[![Tests](https://github.com/rodbland2021/claw-recall/actions/workflows/test.yml/badge.svg)](https://github.com/rodbland2021/claw-recall/actions/workflows/test.yml)
[![Discord](https://img.shields.io/discord/1479309142060695664?color=5865F2&logo=discord&logoColor=white&label=Discord)](https://discord.gg/4wGTVa9Bt6)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-2.0.0-blue)](CHANGELOG.md)

**Persistent, searchable memory for AI agents.** When context compaction erases what your agent was just working on, Claw Recall brings it back.

```bash
# Agent lost context? Get the full transcript back instantly.
recall --recent --agent butler --minutes 30

# What did we decide about the API last week?
recall -q "API rate limit decision" --days 7

# Agent A needs to know what Agent B figured out yesterday
recall -q "database schema migration" --agent atlas --days 2
```

Claw Recall indexes all your agent conversations into a searchable SQLite database with full-text and semantic search. It also captures Gmail, Google Drive, and Slack — giving every agent access to a shared memory that survives compaction, restarts, and context limits.

**[Quick Start](#quick-start)** | **[MCP Tools](#mcp-tools)** | **[CLI](#cli-reference)** | **[REST API](#rest-api)** | **[Data Ingestion](#data-ingestion)** | **[Search Modes](#search-modes)** | **[Configuration](#configuration)** | **[Production Deployment](#production-deployment)** | **[Testing](#testing)**

[Changelog](CHANGELOG.md) | [Discord](https://discord.gg/4wGTVa9Bt6) | [Contributing](CONTRIBUTING.md)

---

## Quick Start

```bash
git clone https://github.com/rodbland2021/claw-recall.git
cd claw-recall
pip install -r requirements.txt

# Create the database
python3 setup_db.py

# Index your conversations
python3 index.py --source ~/.openclaw/agents-archive/ --incremental --embeddings

# Search
./recall.py search "what did we discuss about the API integration"

# Start the web UI + REST API
python3 web.py --host 127.0.0.1 --port 8765
```

**Requirements:** Python 3.10+, SQLite 3.35+ (bundled with Python). Optional: OpenAI API key for semantic search.

---

## How It Works

```
Session Files (.jsonl)  ──→  index.py  ──→  SQLite DB  ──→  CLI / REST API / MCP
Gmail / Drive / Slack   ──→  capture_sources.py  ──↗         (agents query here)
Manual notes            ──→  capture.py  ─────────↗
```

Claw Recall watches your agent session files, indexes every message into SQLite with FTS5 full-text search, and optionally generates embeddings for semantic search. Three access layers let agents query from anywhere:

| Transport | Use Case | Start Command |
|-----------|----------|---------------|
| **MCP stdio** | Agents on the same machine | `python3 mcp_server.py` |
| **MCP SSE** | Agents on remote machines | `python3 mcp_server_sse.py` |
| **REST API** | Scripts, web UI, HTTP clients | `python3 web.py` |

---

## MCP Tools

Claw Recall exposes 8 tools via the [Model Context Protocol](https://modelcontextprotocol.io/):

| Tool | What It Does |
|------|-------------|
| **`search_memory`** | Search ALL sources in one call — conversations, thoughts (Gmail/Drive/Slack), and files. Auto-detects keyword vs semantic. |
| **`browse_recent`** | Full transcript of the last N minutes. The go-to tool for context recovery after compaction. |
| **`capture_thought`** | Save an insight so any agent can find it later. |
| `search_thoughts` | Search captured thoughts only. |
| `browse_activity` | Session summaries across agents. |
| `poll_sources` | Trigger Gmail/Drive/Slack polling. |
| `memory_stats` | Database statistics. |
| `capture_source_status` | External source capture stats. |

### Connect a Local Agent (stdio)

```json
{
  "mcpServers": {
    "claw-recall": {
      "command": "python3",
      "args": ["/path/to/claw-recall/mcp_server.py"]
    }
  }
}
```

### Connect a Remote Agent (SSE)

Start the SSE server on the Claw Recall machine:
```bash
MCP_SSE_HOST=0.0.0.0 python3 mcp_server_sse.py
```

**Claude Code:**
```bash
claude mcp add --transport sse -s user claw-recall "http://your-server:8766/sse"
# Restart Claude Code for it to take effect
```

**Other MCP clients** (OpenClaw, mcporter, etc.):
```json
{
  "mcpServers": {
    "claw-recall": {
      "url": "http://your-server:8766/sse"
    }
  }
}
```

> **Tip:** Claude Code stores MCP configs in `~/.claude.json` — not `~/.claude/settings.json`. If tools don't appear after restart, check for project-level overrides under `projects.<path>.mcpServers`.

---

## CLI Reference

```bash
# Search (auto-detects keyword vs semantic)
./recall.py search "deployment issue"
./recall.py search "PROJ42" --keyword
./recall.py search "budget decisions" --agent atlas --since 2h

# Browse recent transcripts (no query needed)
./recall.py recent --agent butler --minutes 30

# Capture a thought
./recall.py capture "API rate limit is 100/min"

# Date range + JSON output
./recall.py search "deployment" --from 2026-02-15 --to 2026-02-17 --json
```

| Flag | Description |
|------|-------------|
| `--agent` / `-a` | Filter by agent name |
| `--semantic` / `-s` | Force semantic search |
| `--keyword` / `-k` | Force keyword search |
| `--since` | Recency filter: `60m`, `2h`, `3d` |
| `--from` / `--to` | Date range: `YYYY-MM-DD` |
| `--files-only` / `-f` | Search markdown files only |
| `--convos-only` / `-c` | Search conversations only |
| `--limit` / `-n` | Max results per category |
| `--json` / `-j` | Output as JSON |

---

## REST API

Start: `python3 web.py --host 127.0.0.1 --port 8765`

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/search?q=...` | GET | Unified search |
| `/recent?minutes=30&agent=kit` | GET | Full transcript |
| `/capture` | POST | Capture a thought |
| `/capture/poll` | POST | Trigger external source poll |
| `/capture/status` | GET | Capture stats |
| `/thoughts` | GET | List/search thoughts |
| `/status` | GET | System status |
| `/agents` | GET | Agent list with session counts |
| `/activity` | GET | Recent session summaries |
| `/context` | GET | Surrounding messages |
| `/session` | GET | Full session (windowed) |
| `/index-session` | POST | Upload + index a session file |
| `/index-local` | POST | Index a local file by path |

The web UI at the root URL provides search, conversation browsing, agent filtering, and context expansion.

---

## Data Ingestion

### Conversation Sessions

Claw Recall indexes `.jsonl` session files from two agent platforms:

- **OpenClaw** — `~/.openclaw/agents/` (active) and `~/.openclaw/agents-archive/` (completed)
- **Claude Code** — `~/.claude/projects/` (auto-detected by path and JSON structure)

**Real-time indexing** (recommended):
```bash
python3 watcher.py   # Uses inotify — indexes on every file change
```

**Cron-based indexing** (alternative):
```bash
*/15 * * * * cd /path/to/claw-recall && python3 index.py --source ~/.openclaw/agents-archive/ --incremental --embeddings
```

**Remote machine indexing** — for agents on a different machine:
```bash
pip3 install watchdog requests
python3 cc-session-watcher.py   # Watches local files, pushes to server via HTTP
```

### External Sources

```bash
python3 capture_sources.py gmail           # Poll Gmail
python3 capture_sources.py drive           # Poll Google Drive
python3 capture_sources.py slack           # Poll Slack
python3 capture_sources.py all             # Everything
python3 capture_sources.py gmail --backfill --days 90   # Historical import
```

### Backfilling

Already have agent conversations from before Claw Recall? Import them:

```bash
# Index all archived sessions (with embeddings for semantic search)
python3 index.py --source ~/.openclaw/agents-archive/ --embeddings

# Incremental re-index (safe to run repeatedly — skips already-indexed files)
python3 index.py --source ~/.openclaw/agents-archive/ --incremental --embeddings

# Backfill embeddings for messages that were indexed without them
python3 scripts/backfill_embeddings.py --limit 2000
```

---

## Building Shared Knowledge

Agents should proactively capture insights whenever they discover something useful. This builds a shared knowledge base that every agent can search:

```bash
# Agent discovers a database gotcha
recall --capture "SQLite PRAGMA journal_mode=WAL must be set before any concurrent reads"

# Agent finds an API limitation
recall --capture "Rate limit on /api/search is 60 req/min — batch requests for bulk data"

# Via MCP
mcp__claw-recall__capture_thought content="pytest session-scoped fixtures share state — use function scope for isolation" agent="my-agent"
```

**Capture:** Reusable insights, working solutions, gotchas, API discoveries, tool limitations.
**Skip:** Session-specific minutiae, temporary state, things already in documentation.

---

## Agent Names

Claw Recall detects agents from session file paths:

| Path Pattern | Agent |
|-------------|-------|
| `~/.claude/projects/` | Claude Code → "CC" |
| `~/.openclaw/agents/<slot>/sessions/` | OpenClaw → slot name |
| `~/.openclaw/agents-archive/<slot>-*.jsonl` | OpenClaw → slot name |

Customize display names in `agents.json`:

```bash
cp agents.json.example agents.json
```

```json
{
    "agent_names": {
        "main": "Butler",
        "assistant": "Helper",
        "claude-code": "CC"
    }
}
```

Both slot IDs and display names work in search queries:
```bash
./recall.py search "deployment" --agent main     # Resolves to display name
./recall.py search "deployment" --agent Butler    # Direct match
```

---

## Search Modes

| Query Type | Auto-Detected Mode | Example |
|-----------|-------------------|---------|
| Short terms, IDs | Keyword (FTS5) | `"PROJ42"`, `"act_12345"` |
| Questions | Semantic (embeddings) | `"what did we discuss about playbooks"` |
| Quoted phrases | Keyword | `"exact error message"` |
| File paths | Keyword | `~/repos/my-project/` |

Force a mode with `--keyword` or `--semantic`.

### Using Local Embeddings

Any OpenAI-compatible endpoint works — Ollama, vLLM, or text-embeddings-inference:

```bash
export OPENAI_BASE_URL="http://localhost:11434/v1"  # Ollama
export OPENAI_API_KEY="not-needed"                    # Required by SDK but unused
```

Update the model name in `index.py` (line 30) and `search.py` (line 25), and the dimension in `search.py` (line 275). Common models:

| Model | Dimensions | Provider |
|-------|-----------|----------|
| text-embedding-3-small | 1536 | OpenAI (default) |
| nomic-embed-text | 768 | Ollama |
| mxbai-embed-large | 1024 | Ollama |
| all-MiniLM-L6-v2 | 384 | HuggingFace / TEI |

---

## Configuration

| Variable | Description |
|----------|-------------|
| `OPENAI_API_KEY` | Enables semantic search (~$0.02 per 30K messages) |
| `CLAW_RECALL_DB` | Custom SQLite database path (default: `./convo_memory.db`) |
| `CLAW_RECALL_AGENT_DIRS` | Colon-separated agent workspace dirs for file search |
| `CLAW_RECALL_REMOTE_HOME` | Remote machine home dir (for agent detection) |
| `MCP_SSE_HOST` / `MCP_SSE_PORT` | SSE server bind address (default: `0.0.0.0:8766`) |
| `OPENAI_BASE_URL` | Override for local embedding models |

See [docs/configuration.md](docs/) for the full list including health check and SSH tunnel settings.

---

## Production Deployment

Run as systemd services for always-on operation. Three services cover the full stack:

```bash
# Enable all three
sudo systemctl enable --now claw-recall-watcher claw-recall-web claw-recall-sse
```

| Service | What It Runs |
|---------|-------------|
| `claw-recall-watcher` | Real-time file indexing via inotify |
| `claw-recall-web` | REST API + web UI (port 8765) |
| `claw-recall-sse` | MCP SSE server for remote agents (port 8766) |

Example service files are in the [docs/](docs/) directory. Keep secrets in `/etc/claw-recall.env`:
```bash
OPENAI_API_KEY=sk-...
CLAW_RECALL_REMOTE_HOME=/home/remote-user/
```

### Health Monitoring

```bash
# Check service health (MCP SSE, Web API, watcher, indexing pipeline)
bash scripts/health-check.sh

# Run via cron
*/15 * * * * /bin/bash /path/to/claw-recall/scripts/health-check.sh
```

---

## Database Schema

SQLite with WAL mode. Six tables:

| Table | Purpose |
|-------|---------|
| `sessions` | Conversation metadata |
| `messages` | Individual messages (FTS5 indexed) |
| `embeddings` | Semantic vectors (1536-dim default) |
| `thoughts` | Captured notes, emails, documents |
| `capture_log` | External source tracking |
| `index_log` | Session file indexing tracking |

---

## Project Structure

| File | Purpose |
|------|---------|
| `recall.py` | CLI — search, browse, capture |
| `search.py` | Search engine (FTS5 + semantic) |
| `search_files.py` | Markdown file search |
| `index.py` | Session indexer + embeddings |
| `watcher.py` | Real-time local file watcher |
| `cc-session-watcher.py` | Remote machine watcher |
| `web.py` | Flask REST API + web UI |
| `mcp_server.py` | MCP server (stdio) |
| `mcp_server_sse.py` | MCP server (SSE/HTTP) |
| `capture_sources.py` | Gmail / Drive / Slack polling |
| `setup_db.py` | Database schema + migrations |
| `scripts/health-check.sh` | Service health monitoring |

---

## Testing

```bash
python3 -m pytest tests/test_claw_recall.py -v           # All tests
python3 -m pytest tests/test_claw_recall.py -k search     # Search tests
python3 -m pytest tests/test_claw_recall.py -k mcp        # MCP tests
```

---

## Community

- [Discord](https://discord.gg/4wGTVa9Bt6) — setup help, feature requests, show off your config
- [GitHub Issues](https://github.com/rodbland2021/claw-recall/issues) — bugs and feature requests
- [Contributing Guide](CONTRIBUTING.md) — how to help

## Support

If Claw Recall is useful to you:

- **Star this repo** to help others find it
- **Report bugs** via [GitHub Issues](https://github.com/rodbland2021/claw-recall/issues)
- **Buy Me a Coffee** at [buymeacoffee.com/rodbland](https://buymeacoffee.com/rodbland)

**Bitcoin:**
```
bc1q5ggxp0wrgcnn07hkjdhwqtxmsfejqh329djhqz
```

## License

MIT — Use freely, modify as needed.

Built for the [OpenClaw](https://github.com/openclaw/openclaw) community.
