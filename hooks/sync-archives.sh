#!/bin/bash
# Sync archived session files between Claude (local) and Kit (VPS)
# so both recall databases have full cross-agent search.
# Runs hourly via cron.

LOGFILE="/tmp/recall-sync.log"
log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $1" >> "$LOGFILE"; }

log "Starting archive sync..."

# VPS → Local (Kit's archives)
rsync -az --timeout=60 vps:~/.openclaw/agents-archive/ ~/.openclaw/agents-archive-vps/ >> "$LOGFILE" 2>&1
RC1=$?
if [ $RC1 -eq 0 ]; then
    log "VPS → Local: OK"
else
    log "VPS → Local: FAILED (exit=$RC1)"
fi

# Local → VPS (Claude's archives)
rsync -az --timeout=60 ~/.openclaw/agents-archive/ vps:~/.openclaw/agents-archive-claude/ >> "$LOGFILE" 2>&1
RC2=$?
if [ $RC2 -eq 0 ]; then
    log "Local → VPS: OK"
else
    log "Local → VPS: FAILED (exit=$RC2)"
fi

log "Sync done (exit codes: VPS→Local=$RC1, Local→VPS=$RC2)"
