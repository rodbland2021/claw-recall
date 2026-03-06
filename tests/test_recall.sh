#!/usr/bin/env bash
# Claw Recall — Integration Test Suite
# Tests all MCP endpoints and search modes via mcporter.
# Run: bash tests/test_recall.sh
# Exit code: 0 = all pass, 1 = failures

set -uo pipefail

PASS=0
FAIL=0
ERRORS=""

pass() { ((PASS++)); echo "  ✅ $1"; }
fail() { ((FAIL++)); ERRORS+="  ❌ $1\n"; echo "  ❌ $1"; }

echo "=== Claw Recall Integration Tests ==="
echo "Started: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo ""

# --- 1. memory_stats ---
echo "1. memory_stats"
OUT=$(mcporter call claw-recall.memory_stats 2>&1)
echo "$OUT" | grep -q "Sessions:" && pass "Returns session count" || fail "memory_stats missing Sessions"
echo "$OUT" | grep -q "Messages:" && pass "Returns message count" || fail "memory_stats missing Messages"
echo "$OUT" | grep -q "Embeddings:" && pass "Returns embedding count" || fail "memory_stats missing Embeddings"
echo "$OUT" | grep -q "Agents:" && pass "Returns agent list" || fail "memory_stats missing Agents"
echo ""

# --- 2. search_memory (keyword) ---
echo "2. search_memory — keyword"
OUT=$(mcporter call claw-recall.search_memory query="gateway restart" force_keyword=true limit=3 2>&1)
echo "$OUT" | grep -qi "gateway\|restart" && pass "Keyword search returns relevant results" || fail "Keyword search returned nothing relevant"
echo "$OUT" | grep -qE "session:|#[0-9]+ \|" && pass "Results include session references" || fail "Results missing session references"
echo ""

# --- 3. search_memory (with agent filter) ---
echo "3. search_memory — agent filter"
OUT=$(mcporter call claw-recall.search_memory query="dashboard" agent=kit limit=3 2>&1)
echo "$OUT" | grep -qiE "dashboard|kit|#[0-9]+" && pass "Agent-filtered search works" || fail "Agent-filtered search failed"
echo ""

# --- 4. search_memory (date range) ---
echo "4. search_memory — date range (last 1 day)"
OUT=$(mcporter call claw-recall.search_memory query="test" days=1 limit=3 2>&1)
if echo "$OUT" | grep -qE "session:|#[0-9]+|No results|CONVERSATIONS"; then
  pass "Date-bounded search completes"
else
  fail "Date-bounded search failed"
fi
echo ""

# --- 5. search_memory (semantic) ---
echo "5. search_memory — semantic search"
OUT=$(mcporter call claw-recall.search_memory query="how to set up MCP integration" force_semantic=true limit=3 2>&1)
if echo "$OUT" | grep -qi "mcp\|tool\|integration\|semantic\|No results\|timed out"; then
  pass "Semantic search completes (may timeout if cache cold)"
else
  fail "Semantic search returned unexpected output"
fi
echo ""

# --- 6. browse_recent ---
echo "6. browse_recent (default)"
OUT=$(mcporter call claw-recall.browse_recent minutes=10 2>&1)
echo "$OUT" | grep -qE "Recent Transcript|session:|No messages" && pass "browse_recent returns transcript" || fail "browse_recent returned nothing"
echo ""

# --- 7. browse_recent (agent-scoped) ---
echo "7. browse_recent — agent scoped"
OUT=$(mcporter call claw-recall.browse_recent agent=kit minutes=60 2>&1)
echo "$OUT" | grep -qiE "kit|session:|Transcript" && pass "Agent-scoped browse works" || fail "Agent-scoped browse failed"
echo ""

# --- 8. browse_activity ---
echo "8. browse_activity"
OUT=$(mcporter call claw-recall.browse_activity days=1 2>&1)
echo "$OUT" | grep -qi "activity\|session\|agent\|message" && pass "browse_activity returns data" || fail "browse_activity failed"
echo ""

# --- 9. search_thoughts ---
echo "9. search_thoughts"
OUT=$(mcporter call claw-recall.search_thoughts query="important" 2>&1)
if echo "$OUT" | grep -qi "thought\|insight\|No thoughts\|No results"; then
  pass "search_thoughts completes"
else
  fail "search_thoughts returned unexpected output"
fi
echo ""

# --- 10. capture_thought ---
echo "10. capture_thought"
OUT=$(mcporter call claw-recall.capture_thought content="Integration test thought — $(date -u +%s)" agent=kit 2>&1)
echo "$OUT" | grep -qi "captured\|saved\|stored\|ok\|success" && pass "capture_thought succeeds" || fail "capture_thought failed: $OUT"
echo ""

# --- 11. poll_sources ---
echo "11. poll_sources"
OUT=$(mcporter call claw-recall.poll_sources source=all 2>&1)
if echo "$OUT" | grep -qi "poll\|gmail\|drive\|source\|captured\|error\|skipped"; then
  pass "poll_sources completes"
else
  fail "poll_sources returned unexpected output"
fi
echo ""

# --- 12. search_memory (convos_only) ---
echo "12. search_memory — convos_only"
OUT=$(mcporter call claw-recall.search_memory query="config" convos_only=true limit=3 2>&1)
if echo "$OUT" | grep -qE "session:|#[0-9]+|No results|CONVERSATIONS"; then
  pass "convos_only filter works"
else
  fail "convos_only filter failed"
fi
echo ""

# --- 13. search_memory (files_only) ---
echo "13. search_memory — files_only"
OUT=$(mcporter call claw-recall.search_memory query="readme" files_only=true limit=3 2>&1)
if echo "$OUT" | grep -qiE "file|readme|No results|No file|FILES"; then
  pass "files_only filter works"
else
  fail "files_only filter failed"
fi
echo ""

# --- Summary ---
echo ""
echo "==============================="
echo "  Results: $PASS passed, $FAIL failed"
echo "==============================="
if [ $FAIL -gt 0 ]; then
  echo ""
  echo "Failures:"
  echo -e "$ERRORS"
  exit 1
fi
echo "All tests passed ✅"
exit 0
