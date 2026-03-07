# Claw Recall — Full Guide

Comprehensive documentation for installation, configuration, deployment, and operations.

For a quick overview, see the [README](../README.md).

---

## Table of Contents

- [Data Ingestion](#data-ingestion)
- [Agent Names](#agent-names)
- [Building Shared Knowledge](#building-shared-knowledge)
- [Configuration](#configuration)
- [Using Local Embeddings](#using-local-embeddings)
- [Production Deployment](#production-deployment)
- [Database Schema](#database-schema)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [Testing](#testing)

---

## Data Ingestion

### Conversation Sessions

Claw Recall indexes `.jsonl` session files from two agent platforms:

- **OpenClaw** — `~/.openclaw/agents/` (active) and `~/.openclaw/agents-archive/` (completed)
- **Claude Code** — `~/.claude/projects/` (auto-detected by path and JSON structure)

**Real-time indexing** (recommended):
```bash
python3 -m claw_recall.indexing.watcher   # Uses inotify — indexes on every file change
```

**Cron-based indexing** (alternative):
```bash
*/15 * * * * cd /path/to/claw-recall && python3 -m claw_recall.indexing.indexer --source ~/.openclaw/agents-archive/ --incremental --embeddings
```

**Remote machine indexing** — for agents on a different machine, the watcher script monitors local session files and pushes them to the Claw Recall server via HTTP:
```bash
pip3 install watchdog requests
python3 scripts/cc_session_watcher.py
```

Configure the watcher with environment variables:
| Variable | Default | Description |
|----------|---------|-------------|
| `RECALL_SSH_LOCAL_PORT` | `18765` | Local port for SSH tunnel |
| `RECALL_SSH_REMOTE_HOST` | `127.0.0.1` | Remote bind address |
| `RECALL_SSH_REMOTE_PORT` | `8765` | Remote Claw Recall port |
| `RECALL_SSH_HOST` | `your-server` | SSH host for tunnel |

### External Sources

```bash
python3 -m claw_recall.capture.sources gmail           # Poll Gmail
python3 -m claw_recall.capture.sources drive           # Poll Google Drive
python3 -m claw_recall.capture.sources slack           # Poll Slack
python3 -m claw_recall.capture.sources all             # Everything
python3 -m claw_recall.capture.sources status          # Show capture statistics
python3 -m claw_recall.capture.sources gmail --backfill --days 90   # Historical import
```

### Backfilling

Already have agent conversations from before Claw Recall? Import them:

```bash
# Index all archived sessions (with embeddings for semantic search)
python3 -m claw_recall.indexing.indexer --source ~/.openclaw/agents-archive/ --embeddings

# Incremental re-index (safe to run repeatedly — skips already-indexed files)
python3 -m claw_recall.indexing.indexer --source ~/.openclaw/agents-archive/ --incremental --embeddings

# Backfill embeddings for messages that were indexed without them
python3 scripts/backfill_embeddings.py --limit 2000
```

### Session Exclusion

To skip noisy or unwanted session files during indexing, create an `exclude.conf` file:

```bash
cp exclude.conf.example exclude.conf
# Edit exclude.conf — one glob pattern per line
```

To remove already-indexed sessions that match your exclusion patterns:
```bash
python3 scripts/cleanup_excluded.py --dry-run   # Preview what would be removed
python3 scripts/cleanup_excluded.py              # Actually remove them
```

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
./recall "deployment" --agent main     # Resolves to display name
./recall "deployment" --agent Butler   # Direct match
```

---

## Building Shared Knowledge

Agents should proactively capture insights whenever they discover something useful. This builds a shared knowledge base that every agent can search:

```bash
# Agent discovers a database gotcha
./recall capture "SQLite PRAGMA journal_mode=WAL must be set before any concurrent reads"

# Agent finds an API limitation
./recall capture "Rate limit on /api/search is 60 req/min — batch requests for bulk data"

# Via MCP
mcp__claw-recall__capture_thought content="pytest session-scoped fixtures share state — use function scope for isolation" agent="my-agent"
```

**Capture:** Reusable insights, working solutions, gotchas, API discoveries, tool limitations.
**Skip:** Session-specific minutiae, temporary state, things already in documentation.

---

## Configuration

All settings are configured via environment variables. Store them in `.env` or a systemd `EnvironmentFile`.

### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | Enables semantic search (~$0.02 per 30K messages) |
| `CLAW_RECALL_DB` | `./convo_memory.db` | SQLite database path |
| `CLAW_RECALL_AGENT_DIRS` | — | Colon-separated agent workspace dirs for file search |
| `CLAW_RECALL_REMOTE_HOME` | — | Remote machine home dir (for agent detection in HTTP-pushed sessions) |

### Embedding Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAW_RECALL_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model name |
| `CLAW_RECALL_EMBEDDING_DIM` | `1536` | Embedding dimensions |
| `CLAW_RECALL_EMBEDDING_BATCH` | `20` | Batch size for embedding API calls |
| `CLAW_RECALL_MIN_CONTENT_LENGTH` | `20` | Minimum message length to embed |

### Server Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAW_RECALL_WEB_HOST` | `127.0.0.1` | Web API bind address |
| `CLAW_RECALL_WEB_PORT` | `8765` | Web API port |
| `MCP_SSE_HOST` | `0.0.0.0` | MCP SSE bind address |
| `MCP_SSE_PORT` | `8766` | MCP SSE port |
| `MCP_SSE_ALLOWED_HOSTS` | — | Comma-separated additional allowed origins for SSE |

### Health Check Settings

The health check script (`scripts/health-check.sh`) is configured via environment variables passed in the cron job:

| Variable | Description |
|----------|-------------|
| `CLAW_RECALL_SSE_URL` | URL to test MCP SSE endpoint |
| `CLAW_RECALL_WEB_URL` | URL to test Web API endpoint |
| `CLAW_RECALL_DB` | Database path for index freshness check |
| `CLAW_RECALL_ALERT_SCRIPT` | Path to alert script (receives title + message args) |

---

## Using Local Embeddings

Any OpenAI-compatible embedding endpoint works — Ollama, vLLM, or text-embeddings-inference:

```bash
export OPENAI_BASE_URL="http://localhost:11434/v1"  # Ollama
export OPENAI_API_KEY="not-needed"                    # Required by SDK but unused
export CLAW_RECALL_EMBEDDING_MODEL="nomic-embed-text"
export CLAW_RECALL_EMBEDDING_DIM="768"
```

Common models:

| Model | Dimensions | Provider |
|-------|-----------|----------|
| text-embedding-3-small | 1536 | OpenAI (default) |
| nomic-embed-text | 768 | Ollama |
| mxbai-embed-large | 1024 | Ollama |
| all-MiniLM-L6-v2 | 384 | HuggingFace / TEI |

> **Note:** If you change the embedding model after indexing, run `python3 scripts/backfill_embeddings.py` to regenerate embeddings with the new model. Existing embeddings from a different model will produce poor semantic search results.

---

## Production Deployment

Run as systemd services for always-on operation. Three services cover the full stack:

```bash
sudo systemctl enable --now claw-recall-watcher claw-recall-web claw-recall-mcp
```

| Service | What It Runs | Port |
|---------|-------------|------|
| `claw-recall-watcher` | Real-time file indexing via inotify | — |
| `claw-recall-web` | REST API + web UI | 8765 |
| `claw-recall-mcp` | MCP SSE server for remote agents | 8766 |

### Example Service Files

**`/etc/systemd/system/claw-recall-web.service`:**
```ini
[Unit]
Description=Claw Recall Web API
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/claw-recall
EnvironmentFile=/etc/claw-recall.env
ExecStart=/usr/bin/python3 -m claw_recall.api.web --host 127.0.0.1 --port 8765
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/claw-recall-mcp.service`:**
```ini
[Unit]
Description=Claw Recall MCP SSE Server
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/claw-recall
EnvironmentFile=/etc/claw-recall.env
ExecStart=/usr/bin/python3 -m claw_recall.api.mcp_sse
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/claw-recall-watcher.service`:**
```ini
[Unit]
Description=Claw Recall Session File Watcher
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/claw-recall
EnvironmentFile=/etc/claw-recall.env
ExecStart=/usr/bin/python3 -m claw_recall.indexing.watcher
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**`/etc/claw-recall.env`:**
```bash
OPENAI_API_KEY=sk-your-key-here
CLAW_RECALL_REMOTE_HOME=/home/remote-user/
```

### Health Monitoring

```bash
# Check service health (MCP SSE, Web API, watcher, indexing pipeline)
bash scripts/health-check.sh

# Run via cron (every 15 minutes)
*/15 * * * * CLAW_RECALL_SSE_URL=http://localhost:8766/sse CLAW_RECALL_WEB_URL=http://localhost:8765/status /bin/bash /path/to/claw-recall/scripts/health-check.sh
```

### Recommended Cron Jobs

```bash
# External source polling
*/15 * * * * cd /path/to/claw-recall && python3 -m claw_recall.capture.sources gmail --quiet
*/30 * * * * cd /path/to/claw-recall && python3 -m claw_recall.capture.sources slack --quiet
0 */2 * * *  cd /path/to/claw-recall && python3 -m claw_recall.capture.sources drive --quiet

# Backfill any messages missing embeddings
*/30 * * * * cd /path/to/claw-recall && python3 scripts/backfill_embeddings.py --limit 2000 --quiet

# Health check
*/15 * * * * /bin/bash /path/to/claw-recall/scripts/health-check.sh
```

---

## Database Schema

SQLite with WAL mode. Created automatically on first use.

| Table | Purpose |
|-------|---------|
| `sessions` | Conversation metadata (agent, timestamps, source file) |
| `messages` | Individual messages with FTS5 full-text index |
| `embeddings` | Semantic vectors (1536-dim default, float32) |
| `thoughts` | Captured notes, emails, documents with FTS5 index |
| `thought_embeddings` | Thought semantic vectors |
| `capture_log` | External source tracking (prevents re-ingestion) |
| `index_log` | Session file indexing tracking (prevents re-indexing) |

---

## Project Structure

```
claw-recall/
  recall                         # Bash CLI wrapper
  requirements.txt               # Python dependencies
  agents.json.example            # Agent name mapping template
  exclude.conf.example           # Session exclusion template
  claw_recall/                   # Python package (all source code)
    config.py                    #   Settings: DB path, embedding config, server ports
    database.py                  #   Connection manager, schema initialization
    cli.py                       #   CLI entry point (search, recent, capture)
    search/
      engine.py                  #   Keyword (FTS5) + semantic (cosine) search
      files.py                   #   Markdown file search across agent workspaces
    capture/
      thoughts.py                #   Thought capture with embeddings
      sources.py                 #   Gmail, Google Drive, Slack polling
    indexing/
      indexer.py                 #   Session file indexer (.jsonl -> DB)
      watcher.py                 #   Real-time watchdog daemon (inotify)
    api/
      web.py                     #   Flask HTTP API + web UI (port 8765)
      mcp_stdio.py               #   MCP server — stdio transport (local agents)
      mcp_sse.py                 #   MCP server — SSE/HTTP transport (remote agents)
  scripts/
    cc_session_watcher.py        #   Remote machine watcher (push via HTTP)
    backfill_embeddings.py       #   Batch embed messages missing embeddings
    cleanup_excluded.py          #   Remove excluded sessions from DB
    health-check.sh              #   Service health monitoring
    quick-index.sh               #   Manual re-index script
  hooks/
    quick-index.sh               #   Hook-triggered incremental index
    full-index.sh                #   Full re-index of all archives
  tests/
    test_claw_recall.py          #   123 unit tests
  templates/                     #   Web UI Jinja templates
  docs/                          #   Documentation and screenshots
```

### Module Execution

All components are invoked as Python modules, not script files:

| Component | Command |
|-----------|---------|
| CLI | `python3 -m claw_recall.cli "query"` (or `./recall "query"`) |
| Web API | `python3 -m claw_recall.api.web --host 127.0.0.1 --port 8765` |
| MCP stdio | `python3 -m claw_recall.api.mcp_stdio` |
| MCP SSE | `python3 -m claw_recall.api.mcp_sse` |
| Indexer | `python3 -m claw_recall.indexing.indexer --source /path --incremental --embeddings` |
| Watcher | `python3 -m claw_recall.indexing.watcher` |
| Source capture | `python3 -m claw_recall.capture.sources gmail` |

---

## Troubleshooting

### Database not found
The database is created automatically when you first run any command. If you see a "database not found" error, check the `CLAW_RECALL_DB` environment variable — it may point to a non-existent path.

### MCP tools not appearing in Claude Code
1. Verify the config is in `~/.claude.json` (not `~/.claude/settings.json`)
2. Check the SSE server is running: `curl -s --max-time 3 http://your-server:8766/sse`
3. Restart Claude Code after adding/changing MCP configs
4. Check for project-level overrides in `~/.claude.json` under `projects.<path>.mcpServers`

### Search returns no results
1. Check the database has data: `curl http://localhost:8765/status`
2. Try keyword mode explicitly: `./recall "query" --keyword`
3. For agent-filtered searches, use display names (from `agents.json`), not internal slot IDs

### Watcher not indexing
1. Check the service is running: `sudo systemctl status claw-recall-watcher`
2. Check logs: `sudo journalctl -u claw-recall-watcher -n 30`
3. Manual test: `python3 -m claw_recall.indexing.indexer --source ~/.openclaw/agents-archive/ --incremental --embeddings`

### Remote watcher not pushing
1. Check the process: `ps aux | grep cc_session_watcher`
2. Check the SSH tunnel: the watcher manages its own tunnel
3. Test the VPS endpoint: `curl http://your-server:8765/index-session` should return 400 "No file provided"

### Semantic search not working
1. Check `OPENAI_API_KEY` is set (or `OPENAI_BASE_URL` for local models)
2. Check embedding count: `curl http://localhost:8765/status` — `db_embeddings` should be > 0
3. If embeddings are missing, run: `python3 scripts/backfill_embeddings.py --limit 2000`

---

## Testing

```bash
cd /path/to/claw-recall
python3 -m pytest tests/test_claw_recall.py -v              # All 123 tests
python3 -m pytest tests/test_claw_recall.py -v -k browse     # Browse recent tests
python3 -m pytest tests/test_claw_recall.py -v -k capture    # Capture tests
python3 -m pytest tests/test_claw_recall.py -v -k search     # Search tests
python3 -m pytest tests/test_claw_recall.py -v -k mcp        # MCP tests
python3 -m pytest tests/test_claw_recall.py -v -k source     # Source capture tests
python3 -m pytest tests/test_claw_recall.py -v -k watcher    # Watcher helper tests
```

No external services needed — tests use an isolated in-memory database.
