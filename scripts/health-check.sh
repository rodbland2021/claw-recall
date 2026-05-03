#!/bin/bash
# health-check.sh — Claw Recall service health monitoring
#
# Monitors MCP server, web API, and indexing pipeline.
# Designed to run via cron (e.g. every 15 min). Alerts via a configurable
# script on failure, or logs for manual review if no alert script is set.
#
# All configuration is via environment variables — no hardcoded paths or IPs.
# Set them in your crontab entry or source from a .env file.
#
# Required env vars:
#   CLAW_RECALL_DB             Path to SQLite database
#
# Optional env vars:
#   CLAW_RECALL_MCP_URL        MCP server health endpoint (default: http://127.0.0.1:8766/health)
#   CLAW_RECALL_WEB_URL        Web API status endpoint (default: http://127.0.0.1:8765/status)
#   CLAW_RECALL_ALERT_SCRIPT   Path to alert script (receives: title, message, priority)
#   CLAW_RECALL_LOG            Log file path (default: /tmp/claw-recall-health.log)
#   CLAW_RECALL_STATE_FILE     State file for alert dedup (default: /tmp/claw-recall-health-state.json)
#   CLAW_RECALL_EMB_GAP_THRESHOLD  Embedding gap alert threshold (default: 400000)
#   CLAW_RECALL_SESSION_DIRS   Colon-separated session directories to check for indexing
#                              (default: ~/.openclaw/agents-archive/:~/.openclaw/agents/:~/.claude/projects/:~/.codex/sessions/)
#
# Example crontab entry:
#   */15 * * * * CLAW_RECALL_MCP_URL=http://10.0.0.1:8766/health \
#     CLAW_RECALL_WEB_URL=http://127.0.0.1:8765/status \
#     CLAW_RECALL_DB=/path/to/convo_memory.db \
#     /bin/bash /path/to/claw-recall/scripts/health-check.sh 2>/dev/null

set -euo pipefail

# --- Configuration ---
MCP_URL="${CLAW_RECALL_MCP_URL:-http://127.0.0.1:8766/health}"
WEB_URL="${CLAW_RECALL_WEB_URL:-http://127.0.0.1:8765/status}"
DB_PATH="${CLAW_RECALL_DB:-$HOME/convo_memory.db}"
ALERT_SCRIPT="${CLAW_RECALL_ALERT_SCRIPT:-}"
LOG="${CLAW_RECALL_LOG:-/tmp/claw-recall-health.log}"
STATE_FILE="${CLAW_RECALL_STATE_FILE:-/tmp/claw-recall-health-state.json}"
EMB_GAP_THRESHOLD="${CLAW_RECALL_EMB_GAP_THRESHOLD:-400000}"
SESSION_DIRS="${CLAW_RECALL_SESSION_DIRS:-$HOME/.openclaw/agents-archive/:$HOME/.openclaw/agents/:$HOME/.claude/projects/:$HOME/.codex/sessions/}"

log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $1" >> "$LOG"; }

# Keep log file from growing forever
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt 2000 ]; then
    tail -500 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
fi

FAILURES=""

# ── CHECK 1: MCP server availability (CRITICAL) ──
# Remote agents connect to this. If it's down, no agent can use Recall.

# 1a. Service running? (skip if systemctl not available — e.g. Docker, macOS)
if command -v systemctl &>/dev/null; then
    if ! systemctl is-active --quiet claw-recall-mcp.service 2>/dev/null; then
        FAILURES="${FAILURES}[CRITICAL] MCP service not running\n"
        log "FAIL: claw-recall-mcp.service not active"
    else
        log "OK: claw-recall-mcp.service active"
    fi
fi

# 1b. MCP /health endpoint responds?
# The /health endpoint returns JSON with status and uptime. During the first
# 60 seconds after startup it returns {"status": "warming_up"} — this is
# normal (embedding cache building) and should NOT trigger a restart.
MCP_RESPONSE=$(curl -sf --connect-timeout 5 --max-time 5 "$MCP_URL" 2>/dev/null || echo '{}')
MCP_STATUS=$(echo "$MCP_RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")

if [ -z "$MCP_STATUS" ]; then
    # No response at all — server is down
    FAILURES="${FAILURES}[CRITICAL] MCP server not responding at $MCP_URL\n"
    log "FAIL: MCP /health not responding"
elif [ "$MCP_STATUS" = "warming_up" ]; then
    # Server just started, embedding cache is building — this is expected
    log "OK: MCP server warming up (embedding cache building)"
elif [ "$MCP_STATUS" = "ok" ]; then
    log "OK: MCP server healthy"
else
    FAILURES="${FAILURES}[CRITICAL] MCP server returned unexpected status: $MCP_STATUS\n"
    log "FAIL: MCP /health returned status=$MCP_STATUS"
fi

# ── CHECK 2: Web API availability (CRITICAL) ──
# Needed for /recent, /search, session viewer.

if command -v systemctl &>/dev/null; then
    if ! systemctl is-active --quiet claw-recall-web.service 2>/dev/null; then
        FAILURES="${FAILURES}[CRITICAL] Web API service not running\n"
        log "FAIL: claw-recall-web.service not active"
    else
        log "OK: claw-recall-web.service active"
    fi
fi

STATUS_CODE=$(curl -sf -o /dev/null -w "%{http_code}" --connect-timeout 5 --max-time 10 "$WEB_URL" 2>/dev/null || echo "000")
if [ "$STATUS_CODE" != "200" ]; then
    FAILURES="${FAILURES}[CRITICAL] Web API returned HTTP $STATUS_CODE\n"
    log "FAIL: Web API HTTP $STATUS_CODE"
else
    log "OK: Web API HTTP 200"

    # 2b. Search actually returns results? (catches stale-process bugs)
    # Use a common non-stopword that should always have hits. "the" is an FTS5
    # stop word and returns 0 results for keyword search.
    SEARCH_URL="${WEB_URL%/status}/search?q=error&limit=1&force_keyword=true"
    SEARCH_RESULT=$(curl -sf --connect-timeout 5 --max-time 15 "$SEARCH_URL" 2>/dev/null || echo '{"conversations":[],"files":[]}')
    # Count both conversations and files — either having results means search works
    CONVO_COUNT=$(echo "$SEARCH_RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('conversations',[]))+len(d.get('files',[])))" 2>/dev/null || echo "0")
    if [ "$CONVO_COUNT" -eq 0 ]; then
        FAILURES="${FAILURES}[CRITICAL] Search returns 0 results — service may need restart\n"
        log "FAIL: Search returned 0 results (stale process?)"
        # Auto-restart only the web service (not MCP — that kills agent sessions)
        if command -v systemctl &>/dev/null; then
            sudo systemctl restart claw-recall-web 2>/dev/null
            log "AUTO-RESTART: claw-recall-web restarted"
        fi
    else
        log "OK: Search returning results ($CONVO_COUNT)"
    fi
fi

# ── CHECK 3: Indexing pipeline health (IMPORTANT) ──
# Only alert if there are session files that SHOULD have been indexed but weren't.

if command -v systemctl &>/dev/null; then
    if ! systemctl is-active --quiet claw-recall-watcher.service 2>/dev/null; then
        FAILURES="${FAILURES}[WARN] Watcher service not running — new sessions won't be indexed\n"
        log "FAIL: claw-recall-watcher.service not active"
    else
        log "OK: claw-recall-watcher.service active"
    fi
fi

if [ -f "$DB_PATH" ]; then
    RECENT_INDEX=$(sqlite3 "$DB_PATH" "SELECT MAX(indexed_at) FROM index_log WHERE indexed_at > datetime('now', '-2 hours')" 2>/dev/null || echo "")

    # Count recently modified session files across configured directories
    RECENT_SESSION_FILES=0
    IFS=':' read -ra DIRS <<< "$SESSION_DIRS"
    for dir in "${DIRS[@]}"; do
        expanded_dir=$(eval echo "$dir")
        if [ -d "$expanded_dir" ]; then
            count=$(find "$expanded_dir" -name "*.jsonl" -mmin -120 2>/dev/null | wc -l)
            RECENT_SESSION_FILES=$((RECENT_SESSION_FILES + count))
        fi
    done

    if [ "$RECENT_SESSION_FILES" -gt 0 ] && [ -z "$RECENT_INDEX" ]; then
        FAILURES="${FAILURES}[WARN] $RECENT_SESSION_FILES session files modified in last 2h but no indexing activity\n"
        log "FAIL: $RECENT_SESSION_FILES files modified but no recent indexing"
    else
        log "OK: Indexing pipeline healthy (files=$RECENT_SESSION_FILES, recent_index=${RECENT_INDEX:-none})"
    fi

    # Check embedding gap
    EMB_GAP=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM messages m LEFT JOIN embeddings e ON e.message_id = m.id WHERE e.id IS NULL AND LENGTH(m.content) >= 50" 2>/dev/null || echo "0")
    if [ "$EMB_GAP" -gt "$EMB_GAP_THRESHOLD" ]; then
        FAILURES="${FAILURES}[WARN] Embedding gap: $EMB_GAP messages without embeddings\n"
        log "WARN: Embedding gap $EMB_GAP"
    else
        log "OK: Embedding gap $EMB_GAP"
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
    SINCE_LAST=$((NOW - LAST_TIME))
    # Alert on new failure, or re-alert every 2 hours for persistent failures
    if [ "$FAILURE_HASH" != "$LAST_HASH" ] || [ "$SINCE_LAST" -gt 7200 ]; then
        log "ALERT: Sending notification"
        ALERT_MSG=$(echo -e "$FAILURES")
        if [ -n "$ALERT_SCRIPT" ] && [ -f "$ALERT_SCRIPT" ]; then
            PRIORITY=0
            if echo -e "$FAILURES" | grep -q "CRITICAL"; then
                PRIORITY=1
            fi
            bash "$ALERT_SCRIPT" "Claw Recall Alert" "$ALERT_MSG" "$PRIORITY" 2>/dev/null || true
        else
            log "ALERT (no alert script configured): $ALERT_MSG"
        fi
        python3 -c "import json,sys; json.dump({'hash':sys.argv[1],'epoch':int(sys.argv[2]),'time':sys.argv[3]},open(sys.argv[4],'w'))" "$FAILURE_HASH" "$NOW" "$(date -u -Iseconds)" "$STATE_FILE"
    else
        log "SUPPRESSED: Same failure, last alert ${SINCE_LAST}s ago"
    fi
else
    if [ -f "$STATE_FILE" ]; then
        rm "$STATE_FILE"
        log "RECOVERED: All checks passed — cleared failure state"
    else
        log "OK: All checks passed"
    fi
fi
