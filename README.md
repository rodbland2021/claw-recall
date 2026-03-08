# Claw Recall

[![Tests](https://github.com/rodbland2021/claw-recall/actions/workflows/test.yml/badge.svg)](https://github.com/rodbland2021/claw-recall/actions/workflows/test.yml)
[![Discord](https://img.shields.io/discord/1479309142060695664?color=5865F2&logo=discord&logoColor=white&label=Discord)](https://discord.gg/4wGTVa9Bt6)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-2.2.0-blue)](CHANGELOG.md)

**Persistent, searchable memory for AI agents.** When context compaction erases what your agent was just working on, Claw Recall brings it back.

```bash
# Agent lost context? Get the full transcript back instantly.
./recall recent --agent butler --minutes 30

# What did we decide about the API last week?
./recall "API rate limit decision" --since 7d

# Agent A needs to know what Agent B figured out yesterday
./recall "database schema migration" --agent atlas --since 2d
```

Claw Recall indexes all your agent conversations into a searchable SQLite database with full-text and semantic search. It also captures Gmail, Google Drive, and Slack — giving every agent access to a shared memory that survives compaction, restarts, and context limits.

**[Quick Start](#quick-start)** | **[How It Works](#how-it-works)** | **[MCP Tools](#mcp-tools)** | **[CLI](#cli-reference)** | **[REST API](#rest-api)** | **[Full Guide](docs/guide.md)** | **[Community](#community)**

[Changelog](CHANGELOG.md) | [Discord](https://discord.gg/4wGTVa9Bt6) | [Contributing](CONTRIBUTING.md)

---

## Prerequisites

- **Python 3.10+** — check with `python3 --version`
- **pip** — usually bundled with Python (`pip3 --version` to check)
- **Git** — to clone the repo
- **SQLite 3.35+** — bundled with Python on most systems
- **OpenAI API key** — optional, only needed for semantic search. Keyword search works without it.

> **Windows users:** Claw Recall works on Linux, macOS, and WSL (Windows Subsystem for Linux). If you're on Windows, run everything inside WSL — native Windows is not supported.

---

## Quick Start

### Step 1: Clone and install

```bash
git clone https://github.com/rodbland2021/claw-recall.git
cd claw-recall
pip install -r requirements.txt
```

> **Tip:** Use a virtual environment to avoid package conflicts:
> ```bash
> python3 -m venv venv
> source venv/bin/activate   # On WSL/macOS/Linux
> pip install -r requirements.txt
> ```

### Step 2: Index your conversations

Tell Claw Recall where your agent conversations are stored:

| Platform | Session files live at |
|----------|----------------------|
| **OpenClaw** | `~/.openclaw/agents-archive/` (completed) and `~/.openclaw/agents/` (active) |
| **Claude Code** | `~/.claude/projects/` |

Run the indexer on your session directory:

```bash
# OpenClaw users:
python3 -m claw_recall.indexing.indexer --source ~/.openclaw/agents-archive/ --incremental

# Claude Code users:
python3 -m claw_recall.indexing.indexer --source ~/.claude/projects/ --incremental
```

You should see output like:
```
Indexed 42 sessions, 1,847 messages (skipped 0 already-indexed)
```

> **Optional:** Add `--embeddings` to the command above to enable semantic search (requires an `OPENAI_API_KEY` — see [Configuration](#configuration)).

### Step 3: Search

The `./recall` CLI wrapper is a shortcut that runs the search tool:

```bash
# Make it executable (first time only)
chmod +x ./recall

# Search your indexed conversations
./recall "what did we discuss about the API"
```

You should see matching messages with agent names, timestamps, and context.

> **How `./recall` works:** It's a small bash script that runs `python3 -m claw_recall.cli` for you. If you prefer, you can always use `python3 -m claw_recall.cli "your query"` directly.

### Step 4: Start the web UI (optional)

```bash
python3 -m claw_recall.api.web --host 127.0.0.1 --port 8765
```

Open **http://127.0.0.1:8765** in your browser. You should see a search interface where you can browse conversations, filter by agent, and expand context around results.

### Step 5: Connect your agent via MCP (optional)

This is the key step — it gives your AI agent direct access to Claw Recall's memory tools. See the [MCP Tools](#mcp-tools) section below.

---

## How It Works

```
Session Files (.jsonl)  ──→  indexer  ──→  SQLite DB  ──→  CLI / REST API / MCP
Gmail / Drive / Slack   ──→  capture  ──↗                  (agents query here)
Manual notes            ──→  capture  ──↗
```

Claw Recall watches your agent session files, indexes every message into SQLite with FTS5 full-text search, and optionally generates embeddings for semantic search. Three access layers let agents query from anywhere:

| Transport | Use Case | Start Command |
|-----------|----------|---------------|
| **MCP stdio** | Agents on the same machine | `python3 -m claw_recall.api.mcp_stdio` |
| **MCP SSE** | Agents on remote machines | `python3 -m claw_recall.api.mcp_sse` |
| **REST API** | Scripts, web UI, HTTP clients | `python3 -m claw_recall.api.web` |

---

## MCP Tools

[MCP (Model Context Protocol)](https://modelcontextprotocol.io/) is the standard way AI agents discover and use tools. Claw Recall exposes 8 tools via MCP — once connected, your agent can search conversations, capture insights, and recover context automatically.

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

Use this when your agent runs **on the same machine** as Claw Recall. The agent communicates directly through stdin/stdout — no network needed.

**Claude Code** — add to `~/.claude.json` (create the file if it doesn't exist):

```json
{
  "mcpServers": {
    "claw-recall": {
      "command": "python3",
      "args": ["-m", "claw_recall.api.mcp_stdio"],
      "env": { "PYTHONPATH": "/home/youruser/claw-recall" }
    }
  }
}
```

**Replace `/home/youruser/claw-recall`** with the actual path where you cloned the repo (run `pwd` inside the claw-recall directory to find it).

After saving the file, **restart Claude Code** for the tools to appear. You can verify by asking Claude Code: *"What MCP tools do you have access to?"* — it should list `search_memory`, `browse_recent`, etc.

**OpenClaw** — add to your agent config (typically `~/.openclaw/openclaw.json` or `~/.openclaw/agents/<agent>/agent/config.json`):

```json
{
  "mcpServers": {
    "claw-recall": {
      "command": "python3",
      "args": ["-m", "claw_recall.api.mcp_stdio"],
      "env": { "PYTHONPATH": "/home/youruser/claw-recall" }
    }
  }
}
```

**Other MCP clients** — any client that supports the MCP stdio transport uses the same JSON structure. Check your client's documentation for where to put MCP server configs.

### Connect a Remote Agent (SSE)

Use this when your agent runs on a **different machine** from Claw Recall (e.g., Claw Recall is on a VPS, your agent is on your laptop).

**Step 1:** Start the SSE server on the Claw Recall machine:
```bash
python3 -m claw_recall.api.mcp_sse
```

**Step 2:** Connect your agent to it:

**Claude Code:**
```bash
claude mcp add --transport sse -s user claw-recall "http://your-server:8766/sse"
# Replace 'your-server' with the hostname or IP of the machine running Claw Recall
# Then restart Claude Code
```

**OpenClaw / mcporter / other MCP clients:**
```json
{
  "mcpServers": {
    "claw-recall": {
      "url": "http://your-server:8766/sse"
    }
  }
}
```

**Replace `your-server`** with the IP address or hostname of the machine running Claw Recall. If both machines are on a VPN like Tailscale, use the Tailscale IP.

> **Tip:** Claude Code stores MCP configs in `~/.claude.json` — not `~/.claude/settings.json`. If tools don't appear after restart, check for project-level overrides under `projects.<path>.mcpServers`.

### Keep It Running After Reboot

If you started the SSE server or web API manually, it will stop when you close your terminal or reboot. To make it persistent:

**Option A: systemd (recommended for servers)** — creates a service that auto-starts on boot:
```bash
# Create the service file (run once)
sudo tee /etc/systemd/system/claw-recall-web.service << 'EOF'
[Unit]
Description=Claw Recall Web API
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/claw-recall
ExecStart=/usr/bin/python3 -m claw_recall.api.web --host 127.0.0.1 --port 8765
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Replace YOUR_USERNAME with your Linux username, then:
sudo systemctl daemon-reload
sudo systemctl enable --now claw-recall-web

# Check it's running:
sudo systemctl status claw-recall-web
```

Do the same for the MCP SSE server and file watcher. See the [Full Guide — Production Deployment](docs/guide.md#production-deployment) for complete service files for all three services.

**Option B: screen/tmux (quick and simple):**
```bash
screen -S claw-recall
python3 -m claw_recall.api.web --host 127.0.0.1 --port 8765
# Press Ctrl+A, then D to detach. Reattach later with: screen -r claw-recall
```

**Option C: crontab @reboot (no root needed):**
```bash
crontab -e
# Add this line:
@reboot cd /home/YOUR_USERNAME/claw-recall && python3 -m claw_recall.api.web --host 127.0.0.1 --port 8765 >> /tmp/claw-recall.log 2>&1
```

---

## Configuration

Copy the example environment file and edit it:

```bash
cp .env.example .env
# Edit .env with your settings (OPENAI_API_KEY, paths, etc.)
```

The `./recall` CLI and the `scripts/` tools automatically read from `.env`. For systemd services, point to it with `EnvironmentFile=/path/to/claw-recall/.env`.

Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | — | Enables semantic search (~$0.02 per 30K messages). Not needed for keyword search. |
| `CLAW_RECALL_DB` | `./convo_memory.db` | SQLite database path |
| `CLAW_RECALL_WEB_HOST` | `127.0.0.1` | Web API bind address |
| `CLAW_RECALL_WEB_PORT` | `8765` | Web API port |

See the [Full Guide — Configuration](docs/guide.md#configuration) for all environment variables including embedding models, SSE settings, and health checks.

---

## CLI Reference

```bash
# Search (auto-detects keyword vs semantic)
./recall "deployment issue"
./recall "PROJ42" --keyword
./recall "budget decisions" --agent atlas --since 2h

# Browse recent transcripts (no query needed)
./recall recent --agent butler --minutes 30

# Capture a thought
./recall capture "API rate limit is 100/min"

# Date range + JSON output
./recall "deployment" --from 2026-02-15 --to 2026-02-17 --json
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

Start: `python3 -m claw_recall.api.web --host 127.0.0.1 --port 8765`

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/search?q=...` | GET | Unified search |
| `/recent?minutes=30&agent=kit` | GET | Full transcript |
| `/health` | GET | Quick health check |
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

## Search Modes

| Query Type | Auto-Detected Mode | Example |
|-----------|-------------------|---------|
| Short terms, IDs | Keyword (FTS5) | `"PROJ42"`, `"act_12345"` |
| Questions | Semantic (embeddings) | `"what did we discuss about playbooks"` |
| Quoted phrases | Keyword | `"exact error message"` |
| File paths | Keyword | `~/repos/my-project/` |

Force a mode with `--keyword` or `--semantic`. Works without an OpenAI key — keyword search uses SQLite FTS5 with no external dependencies.

---

## Quick Troubleshooting

| Problem | Fix |
|---------|-----|
| `./recall: Permission denied` | Run `chmod +x ./recall` |
| `ModuleNotFoundError: claw_recall` | Set `PYTHONPATH` to the repo directory, or run from inside the repo |
| MCP tools not appearing | Restart your agent after editing the config. Check the config file path (Claude Code uses `~/.claude.json`). |
| Search returns nothing | Make sure you indexed first (Step 2). Check with `curl http://127.0.0.1:8765/status` |
| Semantic search not working | Set `OPENAI_API_KEY` in `.env` and re-index with `--embeddings` |
| SSE server stops when terminal closes | Use systemd, screen, or `@reboot` cron — see [Keep It Running](#keep-it-running-after-reboot) |

See the [Full Guide — Troubleshooting](docs/guide.md#troubleshooting) for detailed solutions.

---

## More Documentation

The **[Full Guide](docs/guide.md)** covers everything for advanced setup and operations:

- [Data Ingestion](docs/guide.md#data-ingestion) — real-time watching, cron indexing, remote machines, external sources
- [Agent Names](docs/guide.md#agent-names) — detection, display names, customization
- [Building Shared Knowledge](docs/guide.md#building-shared-knowledge) — capture patterns for multi-agent teams
- [Configuration](docs/guide.md#configuration) — all environment variables
- [Local Embeddings](docs/guide.md#using-local-embeddings) — Ollama, vLLM, HuggingFace (free, no API key)
- [Production Deployment](docs/guide.md#production-deployment) — systemd services, health monitoring, cron jobs
- [Database Schema](docs/guide.md#database-schema) — tables and structure
- [Project Structure](docs/guide.md#project-structure) — package layout and module reference
- [Troubleshooting](docs/guide.md#troubleshooting) — common issues and fixes
- [Testing](docs/guide.md#testing) — running the test suite

---

## Community

- [Discord](https://discord.gg/4wGTVa9Bt6) — setup help, feature requests, show off your config
- [GitHub Issues](https://github.com/rodbland2021/claw-recall/issues) — bugs and feature requests
- [Contributing Guide](CONTRIBUTING.md) — how to help

## Support

Claw Recall is a solo-maintained project. Donations go directly toward hosting costs, development time, and keeping the Discord community running. Even a small contribution helps — and honestly, knowing people find the tool useful enough to support makes the late nights worth it.

- **Star this repo** to help others find it
- **Report bugs** via [GitHub Issues](https://github.com/rodbland2021/claw-recall/issues)
- [Buy Me a Coffee](https://buymeacoffee.com/rodbland)
- Make a Bitcoin donation — `bc1q5ggxp0wrgcnn07hkjdhwqtxmsfejqh329djhqz`

## License

MIT — Use freely, modify as needed.

Built for the [OpenClaw](https://github.com/openclaw/openclaw) community.
