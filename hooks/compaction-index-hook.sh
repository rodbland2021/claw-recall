#!/bin/bash
# compaction-index-hook.sh v2
# Detects new archive files (created by compaction events) and triggers
# an immediate recall DB index. Runs every minute via system crontab.
#
# Fix: Instead of timestamp comparison (which has race conditions with
# find -newer), we track known archive files by count. If the count
# increases, a new archive was created.

ARCHIVE_DIR="$HOME/.openclaw/agents-archive"
RECALL_DIR="$HOME/repos/claw-recall"
COUNT_FILE="/tmp/recall-archive-count"
LOGFILE="/tmp/recall-compaction-hook.log"

# Count current archive files
CURRENT_COUNT=$(find "$ARCHIVE_DIR" -name "*.jsonl" -type f 2>/dev/null | wc -l)

# Read previous count
PREV_COUNT=0
[ -f "$COUNT_FILE" ] && PREV_COUNT=$(cat "$COUNT_FILE" 2>/dev/null)

# Save current count
echo "$CURRENT_COUNT" > "$COUNT_FILE"

# If count hasn't increased, nothing to do
[ "$CURRENT_COUNT" -le "$PREV_COUNT" ] && exit 0

NEW_FILES=$((CURRENT_COUNT - PREV_COUNT))

# New archives found — run the indexer
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Compaction detected: $NEW_FILES new archive file(s) (was $PREV_COUNT, now $CURRENT_COUNT) — triggering index" >> "$LOGFILE"

# Source API key
export OPENAI_API_KEY=$(grep OPENAI_API_KEY ~/.bashrc 2>/dev/null | head -1 | cut -d= -f2 | tr -d '"' | tr -d "'")

if [ -z "$OPENAI_API_KEY" ]; then
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] ERROR: OPENAI_API_KEY not found" >> "$LOGFILE"
    exit 1
fi

# Run quick-index (indexes archives + active sessions, skips already-indexed)
cd "$RECALL_DIR" && bash hooks/quick-index.sh >> "$LOGFILE" 2>&1
