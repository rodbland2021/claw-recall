#!/bin/bash
# Quick incremental index (no embeddings for speed)
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR" && python index.py --source ~/.openclaw/agents-archive/
