# Changelog

All notable changes to Claw Recall are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/). Versioning follows [Semantic Versioning](https://semver.org/).

## [2.0.0] — 2026-03-06

Major release: 4 weeks of intensive development. Full rewrite of search, MCP integration, external source capture, web UI, and production hardening. 85 commits, 3 Principal Architect reviews.

### Added

**MCP Integration**
- MCP stdio server for local agent access (`mcp_server.py`)
- MCP SSE/HTTP server for remote agent access (`mcp_server_sse.py`)
- 8 tools: `search_memory`, `search_thoughts`, `capture_thought`, `browse_recent`, `browse_activity`, `memory_stats`, `poll_sources`, `capture_source_status`
- Integration test suite covering all MCP endpoints (17 assertions across 13 endpoints)

**Semantic Search**
- OpenAI embedding-based similarity search alongside FTS5 keyword search
- Auto-detection: short terms and IDs use keyword, questions use semantic
- Embedding backfill system for historical messages (cron-safe, incremental)
- Support for local embedding models (Ollama, vLLM, text-embeddings-inference)
- Configurable embedding dimensions and model names

**External Source Capture**
- Gmail indexing with full email body extraction and PDF attachment parsing
- Google Drive document indexing with noise filtering
- Slack message capture
- Historical backfill support (`--backfill --days 90`)
- Sent mail scanning (not just inbox)
- Capture status tracking and polling via API

**Web UI**
- Browser-based search interface with conversation viewer
- Agent filtering with dynamic pills/dropdown
- Auto-semantic detection toggle
- URL state persistence (shareable search links)
- Keyboard shortcuts for power users
- Thought and data source rendering
- Source filter (conversations, files, thoughts)
- Browser-local timezone conversion (was showing UTC)
- Text selection no longer triggers card collapse
- Styled to match FBA Dashboard design system

**REST API**
- Full HTTP API: `/search`, `/recent`, `/capture`, `/thoughts`, `/status`, `/agents`, `/activity`, `/context`, `/session`
- `/index-session` endpoint for remote file push
- `/index-local` endpoint for local path indexing
- `/health` endpoint for monitoring
- CSP and security headers

**Multi-Agent Support**
- 14+ agents indexed with per-agent filtering
- Claude Code session support (different JSON format, auto-detected)
- Agent alias resolution (`main` resolves to display name via `agents.json`)
- Case-insensitive agent filtering
- Cross-agent activity summaries (`browse_activity`)

**Context Recovery**
- `browse_recent` — full transcript recovery without search query
- `recent` CLI subcommand for quick transcript access
- `--since` flag for time-based filtering (`60m`, `2h`, `3d`)

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
- `health-check.sh` monitors MCP SSE, Web API, and watcher services
- Context-aware indexing check (only alerts when modified files exist)
- Embedding gap monitoring
- Configurable alerting via external script

**Infrastructure**
- Production systemd service files (watcher, web, SSE)
- Environment file for secrets management
- Pool-based BTC donation address rotation (weekly)
- CONTRIBUTING.md with development guidelines

### Changed
- Search engine fully rewritten: vectorized semantic search, memory-efficient embedding cache
- Memory usage reduced from 5.5GB peak to 123MB steady state (embedding cache fix)
- Database: WAL mode, optimized indexes for FTS5 + embedding lookups
- Agent detection improved: supports OpenClaw agent IDs, Claude Code sessions, UUID filenames, sub-agent patterns
- Agent name mapping moved to external `agents.json` config (no hardcoded names)
- Embedding truncation tuned: 8000 → 6000 → 2000 chars (optimal for search relevance vs cost)
- Embedding batch size reduced from 100 to 20 (reliability over speed)
- Large-file debounce removed (incremental indexing made it unnecessary)
- Stop word filtering in search results
- Backend thought exclusion from conversation search

### Fixed
- Memory leak in embedding cache (5.5GB → 123MB idle)
- Text selection in web UI no longer triggers card collapse
- Agent filter works consistently across all endpoints
- Case-insensitive agent filtering in search queries
- WSL agent misattribution via HTTP push
- FTS schema alignment with production database
- `--json` flag no longer outputs banner before JSON
- Shell injection vulnerability in `.env` loading
- Stale SSH tunnel cleanup in remote watcher
- Atomic state writes (prevents corruption on crash)
- Staging file cleanup on indexing errors
- Auto-semantic detection edge cases

### Security
- Removed all PII from public repository
- Removed bashrc API key grep from shell scripts
- Removed internal agent names from public repo
- Added CSP and security headers to web UI
- Fixed shell injection in `.env` loading
- Cold cache fallback (no crash on empty database)

### Development
- 3 Principal Architect reviews with systematic bug fixes
- 90/90 test pass rate
- Thread safety improvements across all services
- Credentials include on all API fetch calls

## [1.0.0] — 2026-02-06

Initial release: conversation indexing and keyword search.

### Added
- SQLite FTS5 full-text search across agent conversations
- File watcher for automatic `.jsonl` session indexing
- CLI interface (`recall.py`)
- Basic agent name detection from file paths
- Multi-agent cross-search with agent labels in results
- README with bot usage examples, web UI docs, and CLI reference
- Active session indexing and OpenClaw cron configuration
- Cross-installation rsync sync documentation
