# Changelog

All notable changes to Claw Recall are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/). Versioning follows [Semantic Versioning](https://semver.org/).

---

## [2.2.0] — 2026-03-08

### Added
- **Secret redaction** — API keys, OAuth tokens, passwords, and other sensitive values are automatically stripped from content before it enters the database. Covers all ingestion paths: session indexing, thought capture, Gmail, Drive, Slack, and HTTP endpoints.
- `redact_secrets()` function in `config.py` with 18 built-in pattern categories (Google OAuth, Tailscale, AWS, Slack, GitHub, OpenAI, Anthropic, Stripe, SSH keys, connection strings, and more)
- Custom redaction patterns via `redact_patterns.conf` (one regex per line, auto-loaded at startup)
- `scripts/redact_historical.py` migration script for scanning and cleaning existing database records (supports dry-run mode)

---

## [2.1.1] — 2026-03-08

### Fixed
- Web UI broken after v2.1.0 package refactor — `_REPO_DIR` in `web.py` resolved one level too shallow (`claw_recall/` instead of repo root), causing `TemplateNotFound: index.html` on every request

---

## [2.1.0] — 2026-03-08

Package refactor: all code consolidated into `claw_recall/` Python package with proper subpackages.

### Changed

**Package Structure**
- All source code moved from root-level `.py` files into `claw_recall/` package with 4 subpackages: `search/`, `capture/`, `indexing/`, `api/`
- All components now invoked via `python3 -m claw_recall.xxx` instead of `python3 filename.py`
- Config centralized in `claw_recall/config.py` — single source of truth for DB_PATH, embedding settings, agent name mappings
- Database connection management in `claw_recall/database.py` with `get_db()` context manager
- Systemd service files updated to use module execution
- CLI wrapper (`recall`) updated to call `python3 -m claw_recall.cli`

**Documentation**
- README rewritten for beginners — numbered Quick Start steps, verification at each stage, exact MCP config file paths for Claude Code and OpenClaw, "Keep It Running" section (systemd/screen/cron), Quick Troubleshooting table
- Prerequisites section moved before Quick Start with platform notes (WSL/Linux/macOS)
- MCP section explains what MCP is, what stdio vs SSE means, where config files go
- Comprehensive installation/operations guide split into `docs/guide.md`
- Guide Production Deployment section rewritten with step-by-step systemd setup
- CONTRIBUTING.md updated with correct test commands
- Internal reference doc (`claw-recall-reference`) updated with package layout

**Root Cleanup**
- 14 root-level Python files removed (replaced by package modules)
- Scripts moved to `scripts/` directory
- Tests moved to `tests/` directory

### Fixed
- mcporter MCP stdio config updated to reference new package module path
- All 123 tests updated for new import paths and passing

---

## [2.0.0] — 2026-03-06

Major release: MCP integration, external source capture, SSE transport, health monitoring, and production hardening.

### Added

**MCP Integration**
- MCP stdio server for local agent access (now `claw_recall/api/mcp_stdio.py`)
- MCP SSE/HTTP server for remote agent access (now `claw_recall/api/mcp_sse.py`)
- 8 tools: `search_memory`, `search_thoughts`, `capture_thought`, `browse_recent`, `browse_activity`, `memory_stats`, `poll_sources`, `capture_source_status`
- Integration test suite covering all MCP endpoints (17 assertions across 13 endpoints)

**External Source Capture**
- Gmail indexing with full email body extraction and PDF attachment parsing
- Google Drive document indexing with noise filtering
- Slack message capture
- Historical backfill support (`--backfill --days 90`)
- Sent mail scanning (not just inbox)

**Thought Capture**
- Persistent insights that survive compaction
- Searchable via dedicated endpoint or unified search
- Capture via CLI, API, or MCP tool

**Real-Time Indexing**
- inotify-based file watcher with 5-second debounce (`watcher.py`)
- Remote machine watcher via HTTP push (`cc-session-watcher.py`)
- Incremental indexing (only processes new messages when files grow)
- rsync fallback for oversized session files

**Health Monitoring**
- `scripts/health-check.sh` monitors MCP SSE, Web API, and watcher services
- Context-aware indexing check (only alerts when modified files exist)
- Embedding gap monitoring
- Configurable alerting via external script

**Infrastructure**
- Production systemd service files (watcher, web, SSE)
- `/health` endpoint for service monitoring
- CSP and security headers on web UI
- CONTRIBUTING.md with development guidelines
- `recent` CLI subcommand for quick transcript access
- Agent alias resolution via `agents.json` config
- Pool-based BTC donation address rotation

### Fixed
- Shell injection vulnerability in `.env` loading
- `--json` flag no longer outputs banner before JSON
- WSL agent misattribution via HTTP push
- FTS schema alignment with production database
- Atomic state writes (prevents corruption on crash)
- Staging file cleanup on indexing errors
- Removed all PII and internal agent names from public repo
- Removed bashrc API key grep from shell scripts

---

## [1.3.0] — 2026-03-04

Web UI overhaul and critical memory leak fix.

### Fixed
- **Memory leak in embedding cache** — reduced from 5.5GB peak to 123MB steady state
- Dead schema references cleaned up
- Hex ID handling in cache keys
- Cache TTL enforcement (was never expiring)

### Changed
- Web UI: auto-semantic detection, URL state persistence, keyboard shortcuts, larger viewport
- Database hardening: TTL enforcement, systemd integration, bounded queries, WAL observability
- LIKE enrichment for partial matches

### Security
- Credentials include on all API fetch calls
- Thread safety improvements across web and search layers

---

## [1.2.0] — 2026-02-27

Major quality pass. Merged evolved production code back into repo and ran 3 rounds of Principal Architect review.

### Changed
- Search engine rewritten: vectorized semantic search, memory-efficient embedding loading
- FTS5 search hardened (edge cases with special characters, empty queries)
- Web interface extracted to HTML template with dynamic agent pills/dropdown
- CSS aligned with FBA Dashboard styling playbook
- DRY refactor across search and indexing code

### Security
- Thread safety audit and fixes across all concurrent access points
- Input validation hardened on all endpoints

---

## [1.1.0] — 2026-02-15

Semantic search and Claude Code support.

### Added
- **Semantic search** via OpenAI embeddings alongside FTS5 keyword search
- **Claude Code session support** — auto-detects CC's different JSON format
- Embedding backfill system for historical messages (cron-safe, incremental)
- Cross-installation rsync sync for multi-machine setups
- `--since` flag for time-based filtering (`60m`, `2h`, `3d`)
- Live session re-indexing (index active sessions, not just archives)
- Agent tagging: Claude Code sessions tagged as terminal vs telegram

### Fixed
- Embedding truncation tuned: 8000 → 6000 → 2000 chars (optimal for search relevance vs cost)
- Embedding batch size reduced from 100 to 20 (reliability)
- Agent ID detection for UUID session filenames
- Handling for agents-archive-vps and CC sub-agent filename patterns

---

## [1.0.0] — 2026-02-06

Initial release: conversation indexing and keyword search.

### Added
- SQLite FTS5 full-text search across agent conversations
- File watcher for automatic `.jsonl` session indexing
- CLI interface (`recall.py`)
- Basic agent name detection from file paths
- Multi-agent cross-search with agent labels in results
- Web UI with conversation viewer
- README with bot usage examples, setup guide, and roadmap
- Active session indexing and OpenClaw cron configuration
- Install script to auto-configure agent TOOLS.md
