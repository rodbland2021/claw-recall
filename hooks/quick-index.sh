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

index_dir() {
    local DIR="$1"
    local LABEL="$2"
    local EXTRA="$3"
    if [ ! -d "$DIR" ]; then return; fi
    OUTPUT=$(python3 index.py --source "$DIR" --incremental --embeddings $EXTRA 2>&1)
    INDEXED=$(echo "$OUTPUT" | grep -oP 'Indexed: \K\d+')
    ERRORS=$(echo "$OUTPUT" | grep -oP 'Errors: \K\d+')
    [ -n "$INDEXED" ] && TOTAL_INDEXED=$((TOTAL_INDEXED + INDEXED))
    [ -n "$ERRORS" ] && TOTAL_ERRORS=$((TOTAL_ERRORS + ERRORS))
}

# Kit's own archives + active sessions
index_dir ~/.openclaw/agents-archive/ "Kit archives" "--include-active"

# Claude (local OpenClaw) archives — synced hourly by sync-archives.sh
index_dir ~/.openclaw/agents-archive-claude/ "Claude archives"

# Claude Code (desktop) archives — synced hourly by sync-archives.sh
index_dir ~/.openclaw/agents-archive-cc/ "CC archives"

# Grok sessions
index_dir ~/.openclaw/agents-grok-sessions/ "Grok sessions"

# Chat sessions
index_dir ~/.openclaw/agents-chat-sessions/ "Chat sessions"

if [ "$TOTAL_INDEXED" -gt 0 ] || [ "$TOTAL_ERRORS" -gt 0 ]; then
    log "Done: indexed=$TOTAL_INDEXED errors=$TOTAL_ERRORS"
else
    log "Done: nothing new to index"
fi
