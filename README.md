# Claw Recall

[![Tests](https://github.com/rodbland2021/claw-recall/actions/workflows/test.yml/badge.svg)](https://github.com/rodbland2021/claw-recall/actions/workflows/test.yml)
[![Discord](https://img.shields.io/discord/1479309142060695664?color=5865F2&logo=discord&logoColor=white&label=Discord)](https://discord.gg/4wGTVa9Bt6)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-2.1.0-blue)](CHANGELOG.md)

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

## Quick Start

```bash
git clone https://github.com/rodbland2021/claw-recall.git
cd claw-recall
pip install -r requirements.txt

# Index your conversations
python3 -m claw_recall.indexing.indexer --source ~/.openclaw/agents-archive/ --incremental --embeddings

# Search
./recall "what did we discuss about the API integration"

# Start the web UI + REST API
python3 -m claw_recall.api.web --host 127.0.0.1 --port 8765
```

The database is created automatically on first use. No setup step needed.

**Requirements:** Python 3.10+, SQLite 3.35+ (bundled with Python). Optional: OpenAI API key for semantic search.

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
      "args": ["-m", "claw_recall.api.mcp_stdio"],
      "env": { "PYTHONPATH": "/path/to/claw-recall" }
    }
  }
}
```

### Connect a Remote Agent (SSE)

Start the SSE server on the Claw Recall machine:
```bash
python3 -m claw_recall.api.mcp_sse
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

## More Documentation

The **[Full Guide](docs/guide.md)** covers everything you need for installation and operations:

- [Data Ingestion](docs/guide.md#data-ingestion) — indexing conversations, external sources, backfilling
- [Agent Names](docs/guide.md#agent-names) — detection, display names, customization
- [Configuration](docs/guide.md#configuration) — all environment variables
- [Local Embeddings](docs/guide.md#using-local-embeddings) — Ollama, vLLM, HuggingFace
- [Production Deployment](docs/guide.md#production-deployment) — systemd services, health monitoring
- [Building Shared Knowledge](docs/guide.md#building-shared-knowledge) — capture patterns for multi-agent teams
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
