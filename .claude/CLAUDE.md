# Claw Recall — Project Instructions

## Documentation Freshness Rule (MANDATORY)

**Before creating any PR**, check whether your changes affect documentation:

1. If you **renamed, moved, or deleted** any file, module, function, CLI flag, or endpoint:
   - Search `README.md`, `docs/guide.md`, `CONTRIBUTING.md`, and `CHANGELOG.md` for references
   - Search the internal reference doc: `~/clawd/reference/claw-recall-reference.md`
   - Update any stale references in the same PR

2. If you **added** a new feature, endpoint, CLI flag, or MCP tool:
   - Add it to the appropriate section in `README.md` (if user-facing) or `docs/guide.md` (if operational)
   - Update the internal reference doc if it covers that area

3. If you **changed behavior** of an existing feature:
   - Check whether any doc describes the old behavior and update it

**Quick scan command:**
```bash
# After making changes, check what docs reference the files you touched:
git diff --name-only HEAD~1 | xargs -I{} basename {} | xargs -I{} grep -rn {} README.md docs/ CONTRIBUTING.md
```

## Code Standards

- All modules invoked as `python3 -m claw_recall.xxx`, never as script files
- Tests: `python3 -m pytest tests/test_claw_recall.py -v`
- This is a **public repo** — never commit internal IPs, hostnames, paths, API keys, or agent names
- All changes go through PRs — never push directly to master
- Version in `VERSION` file — update when releasing

## Package Layout

```
claw_recall/           # All source code
  config.py            # Settings (DB_PATH, embedding config, etc.)
  database.py          # Connection manager
  cli.py               # CLI entry point
  search/engine.py     # Search engine
  search/files.py      # File search
  capture/thoughts.py  # Thought capture
  capture/sources.py   # Gmail/Drive/Slack
  indexing/indexer.py   # Session indexer
  indexing/watcher.py   # File watcher
  api/web.py           # Flask REST API
  api/mcp_stdio.py     # MCP stdio server
  api/mcp_sse.py       # MCP SSE server
```
