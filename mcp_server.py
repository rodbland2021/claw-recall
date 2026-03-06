#!/usr/bin/env python3
"""
Claw Recall — MCP Server (stdio transport)

FastMCP server exposing memory search and capture tools.
Runs over stdio for local MCP clients. For remote access, use mcp_server_sse.py.

Usage:
    python3 mcp_server.py                    # stdio mode (for MCP clients)

MCP client config (stdio):
    {
      "mcpServers": {
        "claw-recall": {
          "command": "python3",
          "args": ["/path/to/claw-recall/mcp_server.py"]
        }
      }
    }

For SSE/HTTP transport (remote agents), see mcp_server_sse.py.
"""

import sys
import json
from pathlib import Path

# Ensure repo is on path
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Claw Recall", instructions="""
Claw Recall is a searchable AI memory system — indexed conversation messages
and captured thoughts across all agents.

Use search_memory for finding past conversations and thoughts.
Use browse_recent to get full transcripts of recent conversations (no search query needed).
Use capture_thought to save important information for future recall.
""")


@mcp.tool()
def search_memory(
    query: str,
    agent: str = "",
    force_semantic: bool = False,
    force_keyword: bool = False,
    files_only: bool = False,
    convos_only: bool = False,
    days: int = 0,
    limit: int = 10,
) -> str:
    """Search all memory: conversations, captured thoughts, and markdown files.

    Auto-detects whether to use semantic (meaning-based) or keyword search.
    Use force_semantic or force_keyword to override.

    Args:
        query: Search text (natural language or keywords)
        agent: Filter by agent name (main/kit, cyrus, damian, etc.)
        force_semantic: Force semantic search (for conceptual questions)
        force_keyword: Force keyword search (for exact terms, IDs)
        files_only: Only search markdown files
        convos_only: Only search conversations (skip files)
        days: Limit to last N days (0 = all time)
        limit: Max results per category
    """
    from recall import unified_search, format_unified_results

    # None = auto-detect, True = semantic, False = keyword
    semantic = None
    if force_semantic:
        semantic = True
    elif force_keyword:
        semantic = False

    results = unified_search(
        query=query,
        agent=agent or None,
        semantic=semantic,
        files_only=files_only,
        convos_only=convos_only,
        days=float(days) if days > 0 else None,
        limit=limit,
    )
    return format_unified_results(results)


@mcp.tool()
def search_thoughts(
    query: str,
    agent: str = "",
    semantic: bool = False,
    limit: int = 10,
) -> str:
    """Search only captured thoughts (not conversations or files).

    Args:
        query: Search text
        agent: Filter by agent
        semantic: Use semantic search
        limit: Max results
    """
    from search import search_thoughts as _search_thoughts

    results = _search_thoughts(
        query=query,
        agent=agent or None,
        semantic=semantic,
        limit=limit,
    )
    if not results:
        return "No matching thoughts found."

    lines = [f"Found {len(results)} thought(s):\n"]
    for i, r in enumerate(results, 1):
        ts = r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "unknown"
        lines.append(f"#{i} [{r.source}] {ts} — {r.content[:300]}")
    return "\n".join(lines)


@mcp.tool()
def capture_thought(
    content: str,
    source: str = "mcp",
    agent: str = "",
) -> str:
    """Capture a thought, note, or piece of information into memory.

    Args:
        content: The thought or note to capture
        source: Origin (mcp, manual, observation)
        agent: Which agent is capturing this
    """
    from capture import capture_thought as _capture

    result = _capture(
        content=content,
        source=source,
        agent=agent or None,
    )
    if "error" in result:
        return f"Error: {result['error']}"
    embedded = "with embedding" if result.get("embedded") else "without embedding"
    return f"Captured thought #{result['id']} ({embedded})"


@mcp.tool()
def browse_activity(
    agent: str = "",
    days: int = 14,
    limit: int = 10,
) -> str:
    """Browse recent agent conversation activity.

    Args:
        agent: Filter by agent name
        days: How many days back to look
        limit: Max sessions to return
    """
    import sqlite3
    from search import DB_PATH

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

    sql = """
        SELECT s.agent_id, s.started_at, s.message_count,
               (SELECT content FROM messages m WHERE m.session_id = s.id
                AND m.role = 'user' ORDER BY m.message_index ASC LIMIT 1) as first_msg
        FROM sessions s
        WHERE s.message_count > 2
          AND LENGTH(s.agent_id) BETWEEN 2 AND 14
          AND s.agent_id NOT GLOB '[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]*'
    """
    params = []
    if agent:
        sql += " AND s.agent_id = ? COLLATE NOCASE"
        params.append(agent)
    if days > 0:
        sql += " AND s.started_at >= datetime('now', ?)"
        params.append(f"-{days} days")
    sql += " ORDER BY s.started_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    if not rows:
        return "No recent activity found."

    lines = [f"Recent activity ({len(rows)} sessions):\n"]
    for row in rows:
        agent_id, started, msg_count, first_msg = row
        first_msg = (first_msg or "")[:150]
        lines.append(f"- [{agent_id}] {started} ({msg_count} msgs): {first_msg}")
    return "\n".join(lines)


@mcp.tool()
def browse_recent(
    agent: str = "",
    minutes: int = 30,
) -> str:
    """Get the full transcript of recent conversations — the last N minutes of actual messages.

    This is NOT a search tool. It returns ALL messages chronologically, grouped by session.
    Primary use case: recovering context after compaction or restart.

    Args:
        agent: Filter by agent name (kit, cyrus, claude, cc, etc.). Empty = all agents.
        minutes: How many minutes back to look (default 30, max 120)
    """
    import sqlite3
    from search import DB_PATH

    minutes = max(1, min(minutes, 120))

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

    sql = """
        SELECT s.agent_id, s.id as session_id, m.role, m.content, m.timestamp, m.message_index, s.message_count
        FROM messages m
        JOIN sessions s ON m.session_id = s.id
        WHERE m.timestamp >= datetime('now', ?)
          AND s.message_count > 2
          AND LENGTH(s.agent_id) BETWEEN 2 AND 14
          AND s.agent_id NOT LIKE 'agent:%'
          AND s.agent_id NOT GLOB '[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]*'
          AND s.agent_id NOT IN ('boot', 'acompact', 'compact')
    """
    params: list = [f"-{minutes} minutes"]
    if agent:
        sql += " AND s.agent_id = ? COLLATE NOCASE"
        params.append(agent)
    sql += " ORDER BY m.timestamp ASC, m.message_index ASC LIMIT 500"

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    if not rows:
        return f"No messages found in the last {minutes} minutes."

    # Group by session
    sessions: dict = {}
    for agent_id, session_id, role, content, timestamp, msg_idx, total_count in rows:
        if session_id not in sessions:
            sessions[session_id] = {"agent": agent_id, "messages": [], "first_ts": timestamp, "total": total_count or 0}
        # Truncate based on role
        content = content or ""
        if role == "tool_result" and len(content) > 300:
            content = content[:300] + "..."
        elif role == "assistant" and len(content) > 2000:
            content = content[:2000] + "..."
        sessions[session_id]["messages"].append((role, content, timestamp))

    total_msgs = sum(len(s["messages"]) for s in sessions.values())
    lines = [f"=== Recent Transcript (last {minutes} min, {total_msgs} messages across {len(sessions)} session(s)) ===\n"]

    for sid, info in sessions.items():
        shown = len(info["messages"])
        total = info["total"]
        count_note = f" ({shown} of {total} msgs)" if shown < total else f" ({total} msgs)"
        lines.append(f"--- {info['agent']}{count_note} | {info['first_ts']} | session:{sid[:12]} ---")
        for role, content, ts in info["messages"]:
            lines.append(f"[{role}] {content}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
def poll_sources(
    source: str = "all",
    account: str = "",
    limit: int = 50,
) -> str:
    """Poll external sources (Gmail, Google Drive, Slack) for new content to capture.

    Args:
        source: Which source to poll — 'gmail', 'drive', 'slack', or 'all'
        account: Account to poll — 'personal', 'rbs', or '' for both
        limit: Max items to check per account
    """
    from capture_sources import poll_gmail, poll_drive, poll_slack

    lines = []
    if source in ('gmail', 'all'):
        g = poll_gmail(account=account or None, limit=limit)
        lines.append(f"Gmail: {g['captured']} captured, {g['skipped']} skipped, {g['errors']} errors")
    if source in ('drive', 'all'):
        d = poll_drive(account=account or None, limit=limit)
        lines.append(f"Drive: {d['captured']} captured, {d.get('updated', 0)} updated, "
                      f"{d['skipped']} skipped, {d['errors']} errors")
    if source in ('slack', 'all'):
        s = poll_slack(limit=limit)
        if 'error' in s:
            lines.append(f"Slack: {s['error']}")
        else:
            lines.append(f"Slack: {s['captured']} captured, {s['skipped']} skipped, "
                          f"{s['errors']} errors ({s['channels']} channels)")
    return "\n".join(lines) if lines else "No sources polled."


@mcp.tool()
def capture_source_status() -> str:
    """Get statistics about captured external sources (Gmail, Drive, etc.)."""
    from capture_sources import capture_status
    stats = capture_status()
    lines = [f"Total captured: {stats.get('total', 0)}"]
    for key, count in sorted(stats.items()):
        if key not in ('total', 'latest'):
            lines.append(f"  {key}: {count}")
    if stats.get('latest'):
        lines.append("\nLatest captures:")
        for key, ts in stats['latest'].items():
            lines.append(f"  {key}: {ts}")
    return "\n".join(lines)


@mcp.tool()
def memory_stats() -> str:
    """Get statistics about the memory database."""
    from setup_db import get_db_stats
    from search import cache_status

    stats = get_db_stats()
    cache = cache_status()

    lines = [
        "Claw Recall Memory Stats:",
        f"  Sessions: {stats.get('sessions', 0):,}",
        f"  Messages: {stats.get('messages', 0):,}",
        f"  Embeddings: {stats.get('embeddings', 0):,}",
        f"  Thoughts: {stats.get('thoughts', 0):,}",
        f"  Thought embeddings: {stats.get('thought_embeddings', 0):,}",
        f"  DB size: {stats.get('file_size_mb', 0)} MB",
        f"  Agents: {', '.join(stats.get('agents', []))}",
        f"\nEmbedding cache:",
        f"  Loaded: {cache.get('loaded', False)}",
        f"  Rows: {cache.get('rows', 0):,}",
        f"  Memory: {cache.get('memory_mb', 0)} MB",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
