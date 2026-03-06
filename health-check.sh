#!/bin/bash
# health-check.sh — Claw Recall service health monitoring
# Runs every 15 min via VPS cron. Alerts via Pushover on failure.
#
# Two checks:
# 1. CRITICAL: MCP SSE server is available and responding to queries
# 2. IMPORTANT: Indexing pipeline is working (only alerts if there ARE
#    unindexed session files — won't false-alarm during quiet periods)

set -euo pipefail

PUSHOVER_SCRIPT="$HOME/clawd/scripts/pushover.sh"
STATE_FILE="/tmp/claw-recall-health-state.json"
SSE_URL="http://100.82.195.86:8766/sse"
WEB_URL="http://172.17.0.1:8765/status"
DB_PATH="$HOME/shared/convo-memory/convo_memory.db"
LOG="/tmp/claw-recall-health.log"

log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $1" >> "$LOG"; }

# Keep log file from growing forever
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt 2000 ]; then
    tail -500 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
fi

FAILURES=""

# ── CHECK 1: MCP SSE server availability (CRITICAL) ──
# This is what WSL agents connect to. If this is down, agents can't use Recall.

# 1a. Service running?
if ! systemctl is-active --quiet claw-recall-mcp.service; then
    FAILURES="${FAILURES}[CRITICAL] MCP SSE service not running\n"
    log "FAIL: claw-recall-mcp.service not active"
else
    log "OK: claw-recall-mcp.service active"
fi

# 1b. SSE endpoint actually responds? (GET /sse returns 200 with SSE stream)
# Note: SSE is streaming so curl always hits max-time (exit 28). That's expected.
# Use subshell to prevent set -e from killing us on non-zero curl exit.
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 --max-time 3 "$SSE_URL" 2>/dev/null || true)
# If curl couldn't connect at all, http_code will be 000

if [ "$HTTP_CODE" != "200" ]; then
    FAILURES="${FAILURES}[CRITICAL] MCP SSE endpoint returned HTTP $HTTP_CODE (expected 200)\n"
    log "FAIL: SSE endpoint HTTP $HTTP_CODE"
else
    log "OK: SSE endpoint HTTP 200"
fi

# 1c. Web API responds? (needed for /recent, /search, session viewer)
if ! systemctl is-active --quiet claw-recall-web.service; then
    FAILURES="${FAILURES}[CRITICAL] Web API service not running\n"
    log "FAIL: claw-recall-web.service not active"
else
    STATUS_CODE=$(curl -sf -o /dev/null -w "%{http_code}" --connect-timeout 5 --max-time 10 "$WEB_URL" 2>/dev/null || echo "000")
    if [ "$STATUS_CODE" != "200" ]; then
        FAILURES="${FAILURES}[CRITICAL] Web API /status returned HTTP $STATUS_CODE\n"
        log "FAIL: Web API HTTP $STATUS_CODE"
    else
        log "OK: Web API HTTP 200"
    fi
fi

# ── CHECK 2: Indexing pipeline health (IMPORTANT) ──
# Only alert if there are session files that SHOULD have been indexed but weren't.
# Won't false-alarm during quiet periods when nobody's talking.

# 2a. Watcher service running?
if ! systemctl is-active --quiet claw-recall-watcher.service; then
    FAILURES="${FAILURES}[WARN] Watcher service not running — new sessions won't be indexed\n"
    log "FAIL: claw-recall-watcher.service not active"
else
    log "OK: claw-recall-watcher.service active"
fi

# 2b. Check if there are recently modified .jsonl files that haven't been indexed.
# Find session files modified in last 2 hours, then check if the most recent
# index_log entry is older than 2 hours. If so, indexing may be stuck.
if [ -f "$DB_PATH" ]; then
    RECENT_INDEX=$(sqlite3 "$DB_PATH" "SELECT MAX(indexed_at) FROM index_log WHERE indexed_at > datetime('now', '-2 hours')" 2>/dev/null)
    RECENT_SESSION_FILES=$(find ~/.openclaw/agents-archive/ ~/.openclaw/agents/ -name "*.jsonl" -mmin -120 2>/dev/null | wc -l)

    if [ "$RECENT_SESSION_FILES" -gt 0 ] && [ -z "$RECENT_INDEX" ]; then
        FAILURES="${FAILURES}[WARN] $RECENT_SESSION_FILES session files modified in last 2h but no indexing activity\n"
        log "FAIL: $RECENT_SESSION_FILES files modified but no recent indexing"
    else
        log "OK: Indexing pipeline healthy (files=$RECENT_SESSION_FILES, recent_index=${RECENT_INDEX:-none})"
    fi
fi

# ── ALERTING ──
if [ -n "$FAILURES" ]; then
    FAILURE_HASH=$(echo -e "$FAILURES" | md5sum | cut -d' ' -f1)
    LAST_HASH=""
    LAST_TIME=0
    if [ -f "$STATE_FILE" ]; then
        LAST_HASH=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('hash',''))" 2>/dev/null || true)
        LAST_TIME=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('epoch',0))" 2>/dev/null || echo 0)
    fi

    NOW=$(date +%s)
    # Alert on new failure, or re-alert every 2 hours for persistent failures
    SINCE_LAST=$((NOW - LAST_TIME))
    if [ "$FAILURE_HASH" != "$LAST_HASH" ] || [ "$SINCE_LAST" -gt 7200 ]; then
        log "ALERT: Sending notification"
        ALERT_MSG=$(echo -e "$FAILURES")
        if [ -f "$PUSHOVER_SCRIPT" ]; then
            # Priority 1 for CRITICAL, 0 for WARN-only
            PRIORITY=0
            if echo -e "$FAILURES" | grep -q "CRITICAL"; then
                PRIORITY=1
            fi
            bash "$PUSHOVER_SCRIPT" "Claw Recall Alert" "$ALERT_MSG" "$PRIORITY" 2>/dev/null || true
        fi
        python3 -c "import json; json.dump({'hash':'$FAILURE_HASH','epoch':$NOW,'time':'$(date -u -Iseconds)'},open('$STATE_FILE','w'))"
    else
        log "SUPPRESSED: Same failure, last alert ${SINCE_LAST}s ago"
    fi
else
    # Clear state on success
    if [ -f "$STATE_FILE" ]; then
        rm "$STATE_FILE"
        log "RECOVERED: All checks passed — cleared failure state"
    else
        log "OK: All checks passed"
    fi
fi
