#!/bin/bash
# quick-index.sh — Incremental index WITH embeddings (REMOTE MACHINE version)
# For VPS cron, use hooks/quick-index.sh instead.
# This version indexes remote-specific directories (VPS archives, laptop sessions).

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR" || exit 1

log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $*"; }

# Use OPENAI_API_KEY from environment (set via systemd EnvironmentFile, .env, or export)
if [ -z "$OPENAI_API_KEY" ] && [ -f .env ]; then
    OPENAI_API_KEY=$(grep -m1 '^OPENAI_API_KEY=' .env | cut -d= -f2-)
    export OPENAI_API_KEY
fi

log "Starting quick-index..."
TOTAL_INDEXED=0
TOTAL_ERRORS=0

# Index local archives + active sessions
OUTPUT=$(python3 -m claw_recall.indexing.indexer --source ~/.openclaw/agents-archive/ --include-active --incremental --embeddings 2>&1)
INDEXED=$(echo "$OUTPUT" | grep -oP 'Indexed: \K\d+')
ERRORS=$(echo "$OUTPUT" | grep -oP 'Errors: \K\d+')
[ -n "$INDEXED" ] && TOTAL_INDEXED=$((TOTAL_INDEXED + INDEXED))
[ -n "$ERRORS" ] && TOTAL_ERRORS=$((TOTAL_ERRORS + ERRORS))

# Index VPS archives (synced hourly by sync-archives.sh)
if [ -d ~/.openclaw/agents-archive-vps ]; then
    OUTPUT=$(python3 -m claw_recall.indexing.indexer --source ~/.openclaw/agents-archive-vps/ --incremental --embeddings 2>&1)
    INDEXED=$(echo "$OUTPUT" | grep -oP 'Indexed: \K\d+')
    ERRORS=$(echo "$OUTPUT" | grep -oP 'Errors: \K\d+')
    [ -n "$INDEXED" ] && TOTAL_INDEXED=$((TOTAL_INDEXED + INDEXED))
    [ -n "$ERRORS" ] && TOTAL_ERRORS=$((TOTAL_ERRORS + ERRORS))
fi

# Index laptop Claude Code sessions (synced by laptop cron)
if [ -d ~/.claude/projects-laptop ]; then
    OUTPUT=$(python3 -m claw_recall.indexing.indexer --source ~/.claude/projects-laptop/ --incremental --embeddings 2>&1)
    INDEXED=$(echo "$OUTPUT" | grep -oP 'Indexed: \K\d+')
    ERRORS=$(echo "$OUTPUT" | grep -oP 'Errors: \K\d+')
    [ -n "$INDEXED" ] && TOTAL_INDEXED=$((TOTAL_INDEXED + INDEXED))
    [ -n "$ERRORS" ] && TOTAL_ERRORS=$((TOTAL_ERRORS + ERRORS))
fi

# Index Grok sessions (synced from VPS every 5 min)
if [ -d ~/.openclaw/agents-grok-sessions ]; then
    OUTPUT=$(python3 -m claw_recall.indexing.indexer --source ~/.openclaw/agents-grok-sessions/ --incremental --embeddings 2>&1)
    INDEXED=$(echo "$OUTPUT" | grep -oP 'Indexed: \K\d+')
    ERRORS=$(echo "$OUTPUT" | grep -oP 'Errors: \K\d+')
    [ -n "$INDEXED" ] && TOTAL_INDEXED=$((TOTAL_INDEXED + INDEXED))
    [ -n "$ERRORS" ] && TOTAL_ERRORS=$((TOTAL_ERRORS + ERRORS))
fi

# Index Chat sessions (synced from VPS every 5 min)
if [ -d ~/.openclaw/agents-chat-sessions ]; then
    OUTPUT=$(python3 -m claw_recall.indexing.indexer --source ~/.openclaw/agents-chat-sessions/ --incremental --embeddings 2>&1)
    INDEXED=$(echo "$OUTPUT" | grep -oP 'Indexed: \K\d+')
    ERRORS=$(echo "$OUTPUT" | grep -oP 'Errors: \K\d+')
    [ -n "$INDEXED" ] && TOTAL_INDEXED=$((TOTAL_INDEXED + INDEXED))
    [ -n "$ERRORS" ] && TOTAL_ERRORS=$((TOTAL_ERRORS + ERRORS))
fi

# Index local Claude Code sessions
if [ -d ~/.claude/projects ]; then
    OUTPUT=$(python3 -m claw_recall.indexing.indexer --source ~/.claude/projects/ --incremental --embeddings 2>&1)
    INDEXED=$(echo "$OUTPUT" | grep -oP 'Indexed: \K\d+')
    ERRORS=$(echo "$OUTPUT" | grep -oP 'Errors: \K\d+')
    [ -n "$INDEXED" ] && TOTAL_INDEXED=$((TOTAL_INDEXED + INDEXED))
    [ -n "$ERRORS" ] && TOTAL_ERRORS=$((TOTAL_ERRORS + ERRORS))
fi

# Index local Codex CLI sessions
if [ -d ~/.codex/sessions ]; then
    OUTPUT=$(python3 -m claw_recall.indexing.indexer --source ~/.codex/sessions/ --incremental --embeddings 2>&1)
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
