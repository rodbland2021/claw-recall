#!/bin/bash
# quick-index.sh — Fast incremental index (no embeddings)
# Run frequently (every 30 min) to keep keyword search current
# Embeddings can be added later with: python3 index.py --embeddings

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR" && python3 index.py --source ~/.openclaw/agents-archive/ --include-active 2>/dev/null
