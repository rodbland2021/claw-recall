#!/usr/bin/env python3
"""
Claw Recall — Unified search across conversations AND markdown files.

This is the main entry point for agents to search memory.
Searches both:
1. Conversation history (convo-memory database)
2. Markdown files across all agent workspaces

Auto-detects when semantic search is appropriate based on query structure.

Usage:
    ./recall.py "what did we discuss about playbooks"
    ./recall.py "product launch" --agent main
    ./recall.py "video editing workflow" --files-only
    ./recall.py "Facebook ads" --convos-only
    ./recall.py "act_12345" --keyword          # Force keyword search
"""

import re
import argparse
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add current dir to path
sys.path.insert(0, str(Path(__file__).parent))

from search import search_conversations, SearchResult, deduplicate_results, OPENAI_AVAILABLE, search_thoughts, ThoughtResult
from search_files import search_files, FileMatch


def parse_since(value: str) -> float:
    """Parse a --since value like '60m', '2h', '3d' into fractional days."""
    m = re.match(r'^(\d+(?:\.\d+)?)\s*(m|min|mins|minutes?|h|hrs?|hours?|d|days?)$', value.strip().lower())
    if not m:
        raise argparse.ArgumentTypeError(
            f"Invalid --since format: '{value}'. Use e.g. '60m', '2h', '3d'"
        )
    num = float(m.group(1))
    unit = m.group(2)[0]  # first char: m, h, or d
    if unit == 'm':
        return num / 1440  # minutes to days
    elif unit == 'h':
        return num / 24  # hours to days
    else:
        return num


def parse_date(value: str) -> 'datetime':
    """Parse a date string. Accepts: YYYY-MM-DD, YYYY-MM-DD HH:MM, 'today', 'yesterday'.
    Returns a (datetime, bool) tuple where bool indicates if time was explicitly given."""
    from datetime import datetime, timedelta
    v = value.strip().lower()
    if v == 'today':
        return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if v == 'yesterday':
        return (datetime.now() - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    # Try with time first
    try:
        return datetime.strptime(value.strip(), '%Y-%m-%d %H:%M')
    except ValueError:
        pass
    # Date only
    try:
        return datetime.strptime(value.strip(), '%Y-%m-%d')
    except ValueError:
        pass
    raise argparse.ArgumentTypeError(
        f"Invalid date: '{value}'. Use YYYY-MM-DD, 'YYYY-MM-DD HH:MM', 'today', or 'yesterday'"
    )


def should_use_semantic(query: str) -> bool:
    """
    Auto-detect if semantic search is more appropriate for this query.
    
    Use KEYWORD search for:
    - Short queries (1-2 words) that look like names/IDs
    - Queries with IDs, account numbers, technical identifiers
    - Quoted exact phrases
    - File paths, URLs
    - Code snippets
    
    Use SEMANTIC search for:
    - Natural language questions
    - Longer conceptual queries (4+ words)
    - Abstract concepts without specific identifiers
    """
    query_lower = query.lower().strip()
    words = query_lower.split()
    
    # Force keyword for quoted phrases (user wants exact match)
    if query.startswith('"') and query.endswith('"'):
        return False
    
    # Force keyword for IDs, account numbers, technical patterns
    id_patterns = [
        r'act_\d+',           # Facebook ad account IDs
        r'\d{10,}',           # Long numbers (IDs)
        r'UC[a-zA-Z0-9_-]+',  # YouTube channel IDs
        r'[a-f0-9]{24,}',     # MongoDB-style IDs
        r'\d+\.\d+\.\d+',     # Version numbers, IPs
    ]
    for pattern in id_patterns:
        if re.search(pattern, query):
            return False
    
    # Force keyword for file paths
    if '/' in query or query.endswith('.md') or query.endswith('.py'):
        return False
    
    # Short queries (1-2 words) → keyword unless they're question words
    if len(words) <= 2:
        question_starters = {'what', 'how', 'why', 'when', 'where', 'who', 'which'}
        if not any(w in question_starters for w in words):
            return False
    
    # Natural language questions → semantic
    question_patterns = [
        r'^(what|how|why|when|where|who|which)\b',
        r'\?$',
        r'(did we|have we|was there|were there)',
        r'(discuss|talk|mention|say about)',
    ]
    for pattern in question_patterns:
        if re.search(pattern, query_lower):
            return True
    
    # Default: keyword (don't auto-semantic just because query is long)
    return False


def unified_search(
    query: str,
    agent: str = None,
    semantic: bool = None,  # None = auto-detect
    files_only: bool = False,
    convos_only: bool = False,
    limit: int = 10,
    days: float = None,
    date_from = None,
    date_to = None,
    source: str = None,
) -> dict:
    """
    Search both conversations and files in parallel.
    
    Returns:
        {
            "conversations": [...],
            "files": [...],
            "summary": "Found X conversation matches and Y file matches"
        }
    """
    results = {
        "conversations": [],
        "files": [],
        "thoughts": [],
        "summary": ""
    }

    def search_captured_thoughts():
        try:
            # Map source filter to thought source type
            thought_source = source if source in ('gmail', 'drive', 'slack') else None
            use_semantic = semantic if semantic is not None else should_use_semantic(query)
            thought_results = search_thoughts(
                query=query,
                agent=agent,
                source=thought_source,
                semantic=use_semantic,
                days=int(days) if days else None,
                limit=limit,
            )
            # Fallback: if semantic returned 0 and wasn't forced, retry keyword
            if not thought_results and use_semantic and semantic is None:
                thought_results = search_thoughts(
                    query=query,
                    agent=agent,
                    source=thought_source,
                    semantic=False,
                    days=int(days) if days else None,
                    limit=limit,
                )
            return [
                {
                    "id": r.thought_id,
                    "content": r.content[:500],
                    "source": r.source,
                    "agent": r.agent,
                    "metadata": r.metadata,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "score": round(r.score, 3) if r.score is not None else 0,
                }
                for r in thought_results
            ]
        except Exception as e:
            return [{"error": str(e)}]

    def search_convos():
        try:
            # Auto-detect semantic vs keyword if not explicitly set
            use_semantic = semantic if semantic is not None else should_use_semantic(query)

            convo_results = search_conversations(
                query=query,
                agent=agent,
                semantic=use_semantic,
                limit=limit,
                days=days,
                date_from=date_from,
                date_to=date_to
            )

            # Fallback: if semantic returned 0 results and wasn't forced, retry with keyword
            if not convo_results and use_semantic and semantic is None:
                convo_results = search_conversations(
                    query=query,
                    agent=agent,
                    semantic=False,
                    limit=limit,
                    days=days,
                    date_from=date_from,
                    date_to=date_to
                )

            return [
                {
                    "agent": r.agent_id,
                    "channel": r.channel,
                    "role": r.role,
                    "content": r.content[:500],
                    "fullContent": r.content[:500],
                    "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                    "score": round(r.score, 3),
                    "session_id": r.session_id,
                    "message_id": r.message_id,
                }
                for r in deduplicate_results(convo_results)
            ]
        except Exception as e:
            return [{"error": str(e)}]
    
    def search_docs():
        try:
            file_results = search_files(
                query=query,
                agent=agent,
                limit=limit
            )
            return [
                {
                    "path": r.path.replace(str(Path.home()) + '/', '~/'),
                    "agent": r.agent,
                    "line_num": r.line_num,
                    "line": r.line[:300],
                    "score": round(r.score, 3)
                }
                for r in file_results
            ]
        except Exception as e:
            return [{"error": str(e)}]
    
    # Run searches in parallel
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {}

        if not files_only:
            futures['convos'] = executor.submit(search_convos)
            if not convos_only:
                futures['thoughts'] = executor.submit(search_captured_thoughts)
        if not convos_only:
            futures['files'] = executor.submit(search_docs)

        for key, future in futures.items():
            try:
                result_data = future.result(timeout=30)
                if key == 'convos':
                    results["conversations"] = result_data
                elif key == 'thoughts':
                    results["thoughts"] = result_data
                else:
                    results["files"] = result_data
            except Exception as e:
                results[key if key != 'convos' else 'conversations'] = [{"error": str(e)}]

    # Summary
    conv_count = len([r for r in results["conversations"] if "error" not in r])
    file_count = len([r for r in results["files"] if "error" not in r])
    thought_count = len([r for r in results["thoughts"] if "error" not in r])
    parts = [f"{conv_count} conversation matches", f"{file_count} file matches"]
    if thought_count > 0:
        parts.append(f"{thought_count} thought matches")
    if len(parts) <= 2:
        results["summary"] = "Found " + " and ".join(parts)
    else:
        results["summary"] = "Found " + ", ".join(parts[:-1]) + ", and " + parts[-1]
    
    return results


def format_unified_results(results: dict, verbose: bool = False) -> str:
    """Format unified search results for display."""
    output = []
    
    # Conversations
    if results["conversations"]:
        output.append("\n" + "="*60)
        output.append("📝 CONVERSATIONS")
        output.append("="*60)
        
        for i, r in enumerate(results["conversations"][:5], 1):
            if "error" in r:
                output.append(f"  Error: {r['error']}")
                continue
            ts = r.get('timestamp', 'unknown')[:16] if r.get('timestamp') else 'unknown'
            content = r['content'][:150] + "..." if len(r.get('content', '')) > 150 else r.get('content', '')
            output.append(f"\n#{i} | {r['agent']} | {r['channel']} | {ts}")
            output.append(f"   [{r['role']}] {content}")
    
    # Thoughts
    if results.get("thoughts"):
        output.append("\n" + "="*60)
        output.append("💭 THOUGHTS")
        output.append("="*60)

        for i, r in enumerate(results["thoughts"][:5], 1):
            if "error" in r:
                output.append(f"  Error: {r['error']}")
                continue
            ts = r.get('created_at', 'unknown')[:16] if r.get('created_at') else 'unknown'
            content = r['content'][:150] + "..." if len(r.get('content', '')) > 150 else r.get('content', '')
            src = r.get('source', '?')
            agent = r.get('agent', '?')
            output.append(f"\n#{i} | {agent} via {src} | {ts}")
            output.append(f"   {content}")

    # Files
    if results["files"]:
        output.append("\n" + "="*60)
        output.append("📁 FILES")
        output.append("="*60)

        for i, r in enumerate(results["files"][:5], 1):
            if "error" in r:
                output.append(f"  Error: {r['error']}")
                continue
            output.append(f"\n#{i} | {r['agent']} | {r['path']}:{r['line_num']}")
            output.append(f"   → {r['line'][:150]}")

    output.append(f"\n📊 {results['summary']}")
    
    return '\n'.join(output)


def _run_capture(args):
    """Handle the 'capture' subcommand."""
    from capture import capture_thought
    content = ' '.join(args.text)
    result = capture_thought(
        content=content,
        source=args.source,
        agent=args.agent,
    )
    if "error" in result:
        print(f"❌ {result['error']}")
        raise SystemExit(1)
    print(f"✅ Captured thought #{result['id']}")
    if result.get('embedded'):
        print(f"   Embedded: yes")
    print(f"   Source: {result['source']}")
    if result.get('agent'):
        print(f"   Agent: {result['agent']}")


def _run_recent(args):
    """Handle the 'recent' subcommand — full transcript of last N minutes."""
    import sqlite3
    from search import DB_PATH

    minutes = max(1, min(args.minutes, 120))
    agent = args.agent

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")

    sql = """
        SELECT s.agent_id, s.id, m.role, m.content, m.timestamp, m.message_index, s.message_count
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
        print(f"No messages found in the last {minutes} minutes.")
        return

    sessions: dict = {}
    for agent_id, session_id, role, content, timestamp, msg_idx, total_count in rows:
        if session_id not in sessions:
            sessions[session_id] = {"agent": agent_id, "messages": [], "first_ts": timestamp, "total": total_count or 0}
        content = content or ""
        if role == "tool_result" and len(content) > 300:
            content = content[:300] + "..."
        elif role == "assistant" and len(content) > 2000:
            content = content[:2000] + "..."
        sessions[session_id]["messages"].append((role, content, timestamp))

    total_msgs = sum(len(s["messages"]) for s in sessions.values())
    print(f"=== Recent Transcript (last {minutes} min, {total_msgs} messages across {len(sessions)} session(s)) ===\n")

    for sid, info in sessions.items():
        shown = len(info["messages"])
        total = info["total"]
        count_note = f" ({shown} of {total} msgs)" if shown < total else f" ({total} msgs)"
        print(f"--- {info['agent']}{count_note} | {info['first_ts']} | session:{sid[:12]} ---")
        for role, content, ts in info["messages"]:
            print(f"[{role}] {content}")
        print()


def main():
    parser = argparse.ArgumentParser(
        prog='claw-recall',
        description='🦞 Claw Recall — Search conversations AND files across all agents',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    claw-recall "what did we discuss about playbooks"   # Auto: semantic
    claw-recall "PROJ42"                                  # Auto: keyword (short)
    claw-recall "act_12345" --keyword                    # Force keyword
    claw-recall "video editing" --agent atlas
    claw-recall "quarterly report" --files-only
    claw-recall "deployment" --since 60m                  # Last 60 minutes
    claw-recall "refactor" --since 2h --agent main        # Last 2 hours, specific agent
    claw-recall "budget" --from 2026-02-15 --to 2026-02-17   # Date range
    claw-recall capture "Always use dark mode"            # Capture a thought
    claw-recall capture "API rate limit is 100/min" --source manual --agent butler
        """
    )

    # Check if first arg is "capture" subcommand
    if len(sys.argv) > 1 and sys.argv[1] == 'capture':
        cap_parser = argparse.ArgumentParser(prog='claw-recall capture',
            description='Capture a thought into Claw Recall')
        cap_parser.add_argument('text', nargs='+', help='Thought text to capture')
        cap_parser.add_argument('--source', default='cli', help='Source (cli, manual, http, telegram)')
        cap_parser.add_argument('--agent', '-a', help='Agent name')
        cap_args = cap_parser.parse_args(sys.argv[2:])
        _run_capture(cap_args)
        return

    # Check if first arg is "recent" subcommand
    if len(sys.argv) > 1 and sys.argv[1] == 'recent':
        rec_parser = argparse.ArgumentParser(prog='claw-recall recent',
            description='Get full transcript of recent conversations (last N minutes)')
        rec_parser.add_argument('--agent', '-a', help='Filter by agent name')
        rec_parser.add_argument('--minutes', '-m', type=int, default=30, help='Minutes back (default 30, max 120)')
        rec_args = rec_parser.parse_args(sys.argv[2:])
        _run_recent(rec_args)
        return

    parser.add_argument('query', nargs='+', help='Search query')
    parser.add_argument('--agent', '-a', help='Filter by agent display name (or slot ID, auto-resolved)')

    # Mutually exclusive: semantic vs keyword (default: auto-detect)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument('--semantic', '-s', action='store_true', help='Force semantic search')
    mode_group.add_argument('--keyword', '-k', action='store_true', help='Force keyword search')

    parser.add_argument('--files-only', '-f', action='store_true', help='Only search files')
    parser.add_argument('--convos-only', '-c', action='store_true', help='Only search conversations')
    parser.add_argument('--since', type=parse_since, help='Only search recent messages (e.g. 60m, 2h, 3d)')
    parser.add_argument('--from', dest='date_from', type=parse_date, help='Start date (YYYY-MM-DD, "today", "yesterday")')
    parser.add_argument('--to', dest='date_to', type=parse_date, help='End date (YYYY-MM-DD, "today", "yesterday")')
    parser.add_argument('--limit', '-n', type=int, default=10, help='Max results per category')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show more context')
    parser.add_argument('--json', '-j', action='store_true', help='Output as JSON')

    args = parser.parse_args()
    query = ' '.join(args.query)
    
    # Determine semantic mode: explicit flag or auto-detect
    if args.semantic:
        semantic = True
        mode_str = "semantic (forced)"
    elif args.keyword:
        semantic = False
        mode_str = "keyword (forced)"
    else:
        semantic = None  # Auto-detect
        auto_result = should_use_semantic(query)
        mode_str = f"{'semantic' if auto_result else 'keyword'} (auto)"
    
    # If --to is a date-only (midnight), bump to end of day so the full day is included
    if args.date_to and args.date_to.hour == 0 and args.date_to.minute == 0 and args.date_to.second == 0:
        args.date_to = args.date_to.replace(hour=23, minute=59, second=59)

    print(f"🦞 Claw Recall: '{query}'")
    print(f"   Mode: {mode_str}")
    if args.agent:
        from search import _resolve_agent
        resolved = _resolve_agent(args.agent)
        if resolved != args.agent:
            print(f"   Agent: {args.agent} → {resolved}")
            if args.agent.lower() == 'main':
                print(f"   Note: 'main' is ambiguous in multi-machine setups — use the display name directly for precision")
        else:
            print(f"   Agent: {args.agent}")
    if args.since:
        # Convert fractional days back to human-readable for display
        mins = args.since * 1440
        if mins < 60:
            print(f"   Since: last {int(mins)} minutes")
        elif mins < 1440:
            print(f"   Since: last {mins/60:.1f} hours")
        else:
            print(f"   Since: last {args.since:.1f} days")
    if args.date_from:
        print(f"   From: {args.date_from.strftime('%Y-%m-%d %H:%M')}")
    if args.date_to:
        print(f"   To: {args.date_to.strftime('%Y-%m-%d %H:%M')}")
    if args.files_only:
        print(f"   Scope: files only")
    elif args.convos_only:
        print(f"   Scope: conversations only")
    else:
        print(f"   Scope: conversations + files")

    results = unified_search(
        query=query,
        agent=args.agent,
        semantic=semantic,
        files_only=args.files_only,
        convos_only=args.convos_only,
        days=args.since,
        date_from=args.date_from,
        date_to=args.date_to,
        limit=args.limit
    )
    
    if args.json:
        import json
        print(json.dumps(results, indent=2, default=str))
    else:
        print(format_unified_results(results, args.verbose))


if __name__ == "__main__":
    main()
