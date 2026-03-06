# Changelog

All notable changes to Claw Recall are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/). Versioning follows [Semantic Versioning](https://semver.org/).

## [2.0.0] — 2026-03-06

Major release: full rewrite of search, MCP integration, and external source capture.

### Added
- **MCP Integration** — stdio + SSE servers for direct agent access
- **Semantic search** — OpenAI embedding-based similarity search alongside FTS5 keyword search
- **Web UI** — browser-based search interface with conversation viewer
- **REST API** — full HTTP API for all search and capture operations
- **External source capture** — Gmail, Google Drive, Slack indexing
- **Multi-agent support** — 14+ agents indexed with per-agent filtering
- **Thought capture** — persistent insights that survive compaction
- **Activity browsing** — cross-agent activity summaries
- **browse_recent** — full transcript recovery (no search query needed)
- **Integration test suite** — `tests/test_recall.sh` (17 assertions across 13 endpoints)
- **Embedding backfill** — cron-based background embedding generation for historical messages
- **Use cases documentation** — 7 real-world examples in README

### Changed
- Search engine rewritten: memory-efficient vector loading (2.3GB steady vs previous 4.8GB peak)
- Agent detection improved: supports OpenClaw agent IDs, Claude Code sessions, custom names
- Database schema: WAL mode, optimized indexes for FTS5 + embedding lookups

### Fixed
- Text selection in web UI no longer triggers card collapse
- Agent filter now works consistently across all endpoints

## [1.0.0] — 2025-12-01

Initial release: conversation indexing and keyword search.

### Added
- SQLite FTS5 full-text search across agent conversations
- File watcher for automatic `.jsonl` session indexing
- CLI interface (`recall` command)
- Basic agent name detection
