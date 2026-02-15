#!/bin/bash
# quick-index.sh — Incremental index WITH embeddings
# Runs every 15 min via cron to keep Recall current

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR" || exit 1

# Source OpenAI key
export OPENAI_API_KEY="$(grep OPENAI_API_KEY ~/.bashrc | cut -d'"' -f2)"

# Index local archives + active sessions
python3 index.py --source ~/.openclaw/agents-archive/ --include-active --incremental --embeddings 2>/dev/null

# Index VPS archives (synced hourly by sync-archives.sh)
if [ -d ~/.openclaw/agents-archive-vps ]; then
    python3 index.py --source ~/.openclaw/agents-archive-vps/ --incremental --embeddings 2>/dev/null
fi

# Index laptop Claude Code sessions (synced by laptop cron)
if [ -d ~/.claude/projects-laptop ]; then
    python3 index.py --source ~/.claude/projects-laptop/ --incremental --embeddings 2>/dev/null
fi

# Index local Claude Code sessions too
if [ -d ~/.claude/projects ]; then
    python3 index.py --source ~/.claude/projects/ --incremental --embeddings 2>/dev/null
fi
