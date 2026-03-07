#!/bin/bash
# Full index with embeddings (slower, enables semantic search)
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR" && python3 -m claw_recall.indexing.indexer --source ~/.openclaw/agents-archive/ --embeddings
