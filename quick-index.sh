#!/bin/bash
# quick-index.sh — Incremental index WITH embeddings
# Runs every 15 min via cron to keep Recall current

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR" || exit 1

log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $*"; }

# Source OpenAI key
export OPENAI_API_KEY="$(grep OPENAI_API_KEY ~/.bashrc | cut -d'"' -f2)"

log "Starting quick-index..."
TOTAL_INDEXED=0
TOTAL_ERRORS=0

# Index local archives + active sessions
OUTPUT=$(python3 index.py --source ~/.openclaw/agents-archive/ --include-active --incremental --embeddings 2>&1)
INDEXED=$(echo "$OUTPUT" | grep -oP 'Indexed: \K\d+')
ERRORS=$(echo "$OUTPUT" | grep -oP 'Errors: \K\d+')
[ -n "$INDEXED" ] && TOTAL_INDEXED=$((TOTAL_INDEXED + INDEXED))
[ -n "$ERRORS" ] && TOTAL_ERRORS=$((TOTAL_ERRORS + ERRORS))

# Index VPS archives (synced hourly by sync-archives.sh)
if [ -d ~/.openclaw/agents-archive-vps ]; then
    OUTPUT=$(python3 index.py --source ~/.openclaw/agents-archive-vps/ --incremental --embeddings 2>&1)
    INDEXED=$(echo "$OUTPUT" | grep -oP 'Indexed: \K\d+')
    ERRORS=$(echo "$OUTPUT" | grep -oP 'Errors: \K\d+')
    [ -n "$INDEXED" ] && TOTAL_INDEXED=$((TOTAL_INDEXED + INDEXED))
    [ -n "$ERRORS" ] && TOTAL_ERRORS=$((TOTAL_ERRORS + ERRORS))
fi

# Index laptop Claude Code sessions (synced by laptop cron)
if [ -d ~/.claude/projects-laptop ]; then
    OUTPUT=$(python3 index.py --source ~/.claude/projects-laptop/ --incremental --embeddings 2>&1)
    INDEXED=$(echo "$OUTPUT" | grep -oP 'Indexed: \K\d+')
    ERRORS=$(echo "$OUTPUT" | grep -oP 'Errors: \K\d+')
    [ -n "$INDEXED" ] && TOTAL_INDEXED=$((TOTAL_INDEXED + INDEXED))
    [ -n "$ERRORS" ] && TOTAL_ERRORS=$((TOTAL_ERRORS + ERRORS))
fi

# Index Grok sessions (synced from VPS every 5 min)
if [ -d ~/.openclaw/agents-grok-sessions ]; then
    OUTPUT=$(python3 index.py --source ~/.openclaw/agents-grok-sessions/ --incremental --embeddings 2>&1)
    INDEXED=$(echo "$OUTPUT" | grep -oP 'Indexed: \K\d+')
    ERRORS=$(echo "$OUTPUT" | grep -oP 'Errors: \K\d+')
    [ -n "$INDEXED" ] && TOTAL_INDEXED=$((TOTAL_INDEXED + INDEXED))
    [ -n "$ERRORS" ] && TOTAL_ERRORS=$((TOTAL_ERRORS + ERRORS))
fi

# Index Chat sessions (synced from VPS every 5 min)
if [ -d ~/.openclaw/agents-chat-sessions ]; then
    OUTPUT=$(python3 index.py --source ~/.openclaw/agents-chat-sessions/ --incremental --embeddings 2>&1)
    INDEXED=$(echo "$OUTPUT" | grep -oP 'Indexed: \K\d+')
    ERRORS=$(echo "$OUTPUT" | grep -oP 'Errors: \K\d+')
    [ -n "$INDEXED" ] && TOTAL_INDEXED=$((TOTAL_INDEXED + INDEXED))
    [ -n "$ERRORS" ] && TOTAL_ERRORS=$((TOTAL_ERRORS + ERRORS))
fi

# Index local Claude Code sessions
if [ -d ~/.claude/projects ]; then
    OUTPUT=$(python3 index.py --source ~/.claude/projects/ --incremental --embeddings 2>&1)
    INDEXED=$(echo "$OUTPUT" | grep -oP 'Indexed: \K\d+')
    ERRORS=$(echo "$OUTPUT" | grep -oP 'Errors: \K\d+')
    [ -n "$INDEXED" ] && TOTAL_INDEXED=$((TOTAL_INDEXED + INDEXED))
    [ -n "$ERRORS" ] && TOTAL_ERRORS=$((TOTAL_ERRORS + ERRORS))
fi

if [ "$TOTAL_INDEXED" -gt 0 ] || [ "$TOTAL_ERRORS" -gt 0 ]; then
    log "Done: indexed=$TOTAL_INDEXED errors=$TOTAL_ERRORS"
else
    log "Done: nothing new to index"
fi
