#!/usr/bin/env python3
"""
Recall Web Interface — Search conversations and files via browser.
Rewritten 2026-02-21 with auto-search, context expansion, updated agents.
"""

from flask import Flask, render_template, request, jsonify
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from recall import unified_search
from search import DB_PATH
import re

# Shared SQL filter for valid agent sessions (excludes hex IDs, internal, noise)
VALID_AGENT_FILTER = """
    s.message_count > 2
    AND LENGTH(s.agent_id) BETWEEN 2 AND 14
    AND s.agent_id NOT LIKE 'agent:%'
    AND s.agent_id NOT GLOB '[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]*'
    AND s.agent_id NOT IN ('boot', 'acompact', 'compact')
"""


def _safe_int(value, default, lo=None, hi=None):
    """Parse an int from a request param, with bounds clamping."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def generate_deep_link(content: str) -> str | None:
    """
    Extract platform info and generate a deep link to the original message.
    Returns None if no link can be generated.
    """
    msg_match = re.search(r'\[message_id:\s*(\d+)\]', content)
    if not msg_match:
        return None
    message_id = msg_match.group(1)

    discord_match = re.search(r'\[Discord.*?channel id:(\d+)', content)
    if discord_match:
        channel_id = discord_match.group(1)
        return f"https://discord.com/channels/@me/{channel_id}/{message_id}"

    return None


app = Flask(__name__, template_folder=str(Path(__file__).resolve().parent / 'templates'))


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/agents')
def agents_endpoint():
    """Return list of agents with session counts (for dynamic pills/dropdown)."""
    days = _safe_int(request.args.get('days', '14'), 14, lo=0)
    try:
        conn = sqlite3.connect(str(DB_PATH))
        sql = f"""
            SELECT CASE WHEN s.agent_id = 'Kit' THEN 'main' ELSE s.agent_id END as norm_agent,
                   COUNT(*) as cnt
            FROM sessions s
            WHERE {VALID_AGENT_FILTER}
        """
        params = []
        if days > 0:
            sql += " AND s.started_at >= datetime('now', ?)"
            params.append(f"-{days} days")
        sql += " GROUP BY norm_agent ORDER BY cnt DESC"
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return jsonify({agent: count for agent, count in rows})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/search')
def search_endpoint():
    query = request.args.get('q', '')
    semantic = request.args.get('semantic', 'false').lower() == 'true'
    agent = request.args.get('agent', '') or None
    files_only = request.args.get('files_only', 'false').lower() == 'true'
    convos_only = request.args.get('convos_only', 'false').lower() == 'true'

    if not query:
        return jsonify({"error": "No query provided"})

    days = _safe_int(request.args.get('days', '0'), 0, lo=0)

    try:
        results = unified_search(
            query=query,
            agent=agent,
            semantic=semantic,
            files_only=files_only,
            convos_only=convos_only,
            days=days if days > 0 else None,
            limit=20
        )
    except Exception as e:
        return jsonify({"error": f"Search failed: {e}", "conversations": [], "files": [], "summary": ""}), 500

    # Post-process conversation results: add deep links, session_id, message_id
    for convo in results.get("conversations", []):
        full_content = convo.pop("fullContent", convo.get("content", ""))
        convo["deepLink"] = generate_deep_link(full_content)

        # Resolve session_id and message_id for context expansion
        if "session_id" not in convo:
            _enrich_convo_with_session(convo)

    return jsonify(results)


def _enrich_convo_with_session(convo: dict):
    """Look up session_id and message id for a conversation result, for context expansion."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        content_prefix = (convo.get("content") or "")[:200]
        if not content_prefix:
            return

        cursor = conn.execute("""
            SELECT m.id, m.session_id, m.message_index
            FROM messages m
            JOIN sessions s ON m.session_id = s.id
            WHERE m.content LIKE ? AND m.role = ?
            LIMIT 1
        """, (content_prefix + '%', convo.get("role", "")))

        row = cursor.fetchone()
        if row:
            convo["message_id"] = row[0]
            convo["session_id"] = row[1]
        conn.close()
    except Exception as e:
        print(f"[recall-web] enrich error: {e}")


@app.route('/context')
def context_endpoint():
    """Return surrounding messages for a given message in a session."""
    session_id = request.args.get('session_id', '')
    message_id = request.args.get('message_id', '')
    radius = _safe_int(request.args.get('radius', '5'), 5, lo=1, hi=100)

    if not session_id:
        return jsonify({"error": "session_id required"}), 400

    try:
        conn = sqlite3.connect(str(DB_PATH))

        if message_id:
            cursor = conn.execute(
                "SELECT message_index FROM messages WHERE id = ? AND session_id = ?",
                (_safe_int(message_id, 0, lo=0), session_id)
            )
        else:
            return jsonify({"error": "message_id required"}), 400

        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({"messages": [], "error": "Message not found"})

        target_index = row[0]

        cursor = conn.execute("""
            SELECT id, role, content, message_index, timestamp
            FROM messages
            WHERE session_id = ?
              AND message_index >= ?
              AND message_index <= ?
            ORDER BY message_index ASC
        """, (session_id, (target_index or 0) - radius, (target_index or 0) + radius))

        messages = []
        for r in cursor.fetchall():
            messages.append({
                "id": r[0],
                "role": r[1],
                "content": (r[2] or "")[:1000],
                "message_index": r[3],
                "timestamp": r[4],
                "is_match": r[3] == target_index
            })

        min_idx = conn.execute(
            "SELECT MIN(message_index) FROM messages WHERE session_id = ?",
            (session_id,)
        ).fetchone()[0] or 0
        max_idx_all = conn.execute(
            "SELECT MAX(message_index) FROM messages WHERE session_id = ?",
            (session_id,)
        ).fetchone()[0] or 0

        loaded_min = messages[0]["message_index"] if messages else 0
        loaded_max = messages[-1]["message_index"] if messages else 0

        conn.close()
        return jsonify({
            "session_id": session_id,
            "messages": messages,
            "target_index": target_index,
            "has_more_before": loaded_min > min_idx,
            "has_more_after": loaded_max < max_idx_all,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/activity')
def activity_endpoint():
    """Browse recent agent conversations — no search query needed."""
    agent = request.args.get('agent', '') or None
    days = _safe_int(request.args.get('days', '14'), 14, lo=0)
    limit = _safe_int(request.args.get('limit', '30'), 30, lo=0, hi=100)

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        sql = f"""
            SELECT s.id, s.agent_id, s.started_at, s.message_count,
                   (SELECT content FROM messages m WHERE m.session_id = s.id AND m.role = 'user' ORDER BY m.message_index ASC LIMIT 1) as first_user_msg,
                   (SELECT content FROM messages m WHERE m.session_id = s.id AND m.role = 'assistant' ORDER BY m.message_index DESC LIMIT 1) as last_assistant_msg
            FROM sessions s
            WHERE {VALID_AGENT_FILTER}
        """
        params = []
        if agent:
            sql += " AND s.agent_id = ?"
            params.append(agent)
        if days > 0:
            sql += " AND s.started_at >= datetime('now', ?)"
            params.append(f"-{days} days")
        sql += " ORDER BY s.started_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()

        sessions = []
        for r in rows:
            first_msg = (r['first_user_msg'] or '')[:500]
            last_msg = (r['last_assistant_msg'] or '')[:500]
            sessions.append({
                "session_id": r['id'],
                "agent": r['agent_id'],
                "started_at": r['started_at'],
                "message_count": r['message_count'],
                "first_user_message": first_msg,
                "last_assistant_message": last_msg,
            })

        count_sql = f"""
            SELECT s.agent_id, COUNT(*) as cnt
            FROM sessions s
            WHERE {VALID_AGENT_FILTER}
        """
        count_params = []
        if days > 0:
            count_sql += " AND s.started_at >= datetime('now', ?)"
            count_params.append(f"-{days} days")
        count_sql += " GROUP BY s.agent_id ORDER BY cnt DESC"
        agent_counts = {r[0]: r[1] for r in conn.execute(count_sql, count_params).fetchall()}

        conn.close()
        return jsonify({
            "sessions": sessions,
            "agent_counts": agent_counts,
            "total": len(sessions),
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/session')
def session_endpoint():
    """Return messages for a session, with optional windowed loading."""
    session_id = request.args.get('session_id', '')
    if not session_id:
        return jsonify({"error": "session_id required"}), 400

    around = request.args.get('around', None)
    window = _safe_int(request.args.get('window', '30'), 30, lo=1, hi=60)

    try:
        conn = sqlite3.connect(str(DB_PATH))
        sess = conn.execute(
            "SELECT id, agent_id, started_at, message_count FROM sessions WHERE id = ?",
            (session_id,)
        ).fetchone()
        if not sess:
            conn.close()
            return jsonify({"error": "Session not found", "messages": []})

        total = sess[3] or 0

        if around is not None:
            center = _safe_int(around, 0, lo=0)
            half = window // 2
            low = max(0, center - half)
            high = center + half

            rows = conn.execute("""
                SELECT role, content, message_index, timestamp
                FROM messages
                WHERE session_id = ? AND message_index >= ? AND message_index <= ?
                ORDER BY message_index ASC
            """, (session_id, low, high)).fetchall()

            min_idx = conn.execute(
                "SELECT MIN(message_index) FROM messages WHERE session_id = ?",
                (session_id,)
            ).fetchone()[0] or 0
            max_idx = conn.execute(
                "SELECT MAX(message_index) FROM messages WHERE session_id = ?",
                (session_id,)
            ).fetchone()[0] or 0
            has_before = low > min_idx
            has_after = high < max_idx
        else:
            rows = conn.execute("""
                SELECT role, content, message_index, timestamp
                FROM messages
                WHERE session_id = ?
                ORDER BY message_index ASC
                LIMIT ?
            """, (session_id, window)).fetchall()

            max_idx = conn.execute(
                "SELECT MAX(message_index) FROM messages WHERE session_id = ?",
                (session_id,)
            ).fetchone()[0] or 0
            has_before = False
            last_loaded = rows[-1][2] if rows else 0
            has_after = last_loaded < max_idx

        messages = []
        for r in rows:
            content_text = r[1] or ""
            if r[0] == 'tool_result' and len(content_text) > 500:
                content_text = content_text[:500] + "..."
            messages.append({
                "role": r[0],
                "content": content_text,
                "message_index": r[2],
                "timestamp": r[3],
            })

        conn.close()
        return jsonify({
            "session_id": sess[0],
            "agent": sess[1],
            "started_at": sess[2],
            "message_count": sess[3],
            "messages": messages,
            "has_more_before": has_before if around is not None else False,
            "has_more_after": has_after,
            "total_messages": total,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', '-p', type=int, default=8765, help='Port to run on')
    parser.add_argument('--host', default='100.82.195.86', help='Host to bind to (Tailscale IP)')
    args = parser.parse_args()

    print(f"Recall Web Interface running at http://localhost:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)
