#!/usr/bin/env python3
"""
Recall Web Interface -- Search conversations and files via browser.
Rewritten 2026-02-21 with auto-search, context expansion, updated agents.
"""

from flask import Flask, render_template, request, jsonify
import sqlite3
import os
import logging
from pathlib import Path

from claw_recall.database import get_db
from claw_recall.config import DB_PATH
from claw_recall.cli import unified_search
from claw_recall.search.engine import cache_status, preload_embedding_cache, resolve_agent
import re

# Shared SQL filter for valid agent sessions (excludes hex IDs, internal, noise)
VALID_AGENT_FILTER = """
    s.message_count > 2
    AND LENGTH(s.agent_id) BETWEEN 2 AND 14
    AND s.agent_id NOT LIKE 'agent:%'
    AND s.agent_id NOT GLOB '[0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F]*'
    AND s.agent_id NOT IN ('boot', 'acompact', 'compact')
    AND s.id NOT LIKE 'boot-%'
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
    # Search only first 500 chars to avoid ReDoS on large content
    head = content[:500]
    msg_match = re.search(r'\[message_id:\s*(\d+)\]', head)
    if not msg_match:
        return None
    message_id = msg_match.group(1)

    discord_match = re.search(r'\[Discord[^\]]{0,80}channel id:(\d+)', head)
    if discord_match:
        channel_id = discord_match.group(1)
        return f"https://discord.com/channels/@me/{channel_id}/{message_id}"

    return None


# Templates are in the repo root's templates/ dir
_REPO_DIR = Path(__file__).resolve().parent.parent.parent
app = Flask(__name__, template_folder=str(_REPO_DIR / 'templates'))
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB for session files

REMOTE_INDEX_TEMP_DIR = '/tmp/claw-recall-remote'
log = logging.getLogger('claw-recall-web')


@app.after_request
def _add_security_headers(response):
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'"
    return response


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/status')
def status_endpoint():
    """Observability endpoint: embedding cache state + DB stats."""
    info = cache_status()
    try:
        with get_db() as conn:
            info["db_messages"] = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            info["db_embeddings"] = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
            info["db_sessions"] = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            try:
                info["db_thoughts"] = conn.execute("SELECT COUNT(*) FROM thoughts").fetchone()[0]
                info["db_thought_embeddings"] = conn.execute("SELECT COUNT(*) FROM thought_embeddings").fetchone()[0]
            except Exception as e:
                log.debug(f"Thought tables not available: {e}")
                info["db_thoughts"] = 0
    except Exception as e:
        info["db_error"] = str(e)
    return jsonify(info)


@app.route('/health')
def health_endpoint():
    """Health check endpoint for monitoring and orchestration."""
    try:
        with get_db() as conn:
            sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            embeddings = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
            last_indexed = conn.execute(
                "SELECT MAX(started_at) FROM sessions"
            ).fetchone()[0]
        return jsonify({
            "status": "ok",
            "db": {"connected": True, "sessions": sessions, "embeddings": embeddings},
            "last_indexed": last_indexed,
            "cache": cache_status(),
        })
    except Exception as e:
        log.error(f"Health check failed: {e}")
        return jsonify({"status": "error", "db": {"connected": False}}), 503


@app.route('/agents')
def agents_endpoint():
    """Return list of agents with session counts (for dynamic pills/dropdown)."""
    days = _safe_int(request.args.get('days', '14'), 14, lo=0)
    try:
        with get_db() as conn:
            sql = f"""
                SELECT s.agent_id as norm_agent,
                       COUNT(*) as cnt
                FROM sessions s
                WHERE {VALID_AGENT_FILTER}
            """
            params = []
            if days > 0:
                sql += " AND COALESCE(s.ended_at, s.started_at) >= datetime('now', ?)"
                params.append(f"-{days} days")
            sql += " GROUP BY norm_agent ORDER BY cnt DESC"
            rows = conn.execute(sql, params).fetchall()
            return jsonify({agent: count for agent, count in rows})
    except Exception as e:
        return jsonify({"error": "Internal error"}), 500


@app.route('/capture', methods=['POST'])
def capture_endpoint():
    """Capture a thought via HTTP API."""
    try:
        data = request.get_json(silent=True) or {}
        content = data.get('content', '').strip()
        if not content:
            return jsonify({"error": "content is required"}), 400

        from claw_recall.capture.thoughts import capture_thought
        result = capture_thought(
            content=content,
            source=data.get('source', 'http'),
            agent=data.get('agent'),
            metadata=data.get('metadata'),
        )
        if "error" in result:
            return jsonify(result), 500
        return jsonify(result), 201
    except Exception as e:
        return jsonify({"error": "Internal error"}), 500


@app.route('/capture/poll', methods=['POST'])
def capture_poll_endpoint():
    """Trigger a capture poll for external sources (Gmail, Drive)."""
    try:
        data = request.get_json(silent=True) or {}
        source = data.get('source', 'all')
        account = data.get('account')
        limit = _safe_int(data.get('limit', '50'), 50, lo=1, hi=200)

        from claw_recall.capture.sources import poll_gmail, poll_drive
        results = {}

        if source in ('gmail', 'all'):
            results['gmail'] = poll_gmail(account=account, limit=limit)
        if source in ('drive', 'all'):
            results['drive'] = poll_drive(account=account, limit=limit)

        return jsonify(results), 200
    except Exception as e:
        return jsonify({"error": "Internal error"}), 500


@app.route('/capture/status')
def capture_status_endpoint():
    """Get capture log statistics."""
    try:
        from claw_recall.capture.sources import capture_status
        return jsonify(capture_status())
    except Exception as e:
        return jsonify({"error": "Internal error"}), 500


def _extract_path_suffix(source_path: str) -> str:
    """Extract the significant path suffix for agent detection.

    Preserves the path structure that extract_session_metadata() uses
    to identify the agent.

    Examples:
        /home/user/.claude/projects/-test/abc.jsonl -> .claude/projects/-test/abc.jsonl
        /home/user/.openclaw/agents/main/sessions/x.jsonl -> .openclaw/agents/main/sessions/x.jsonl
    """
    for marker in ['.claude/projects', '.openclaw/agents', '.openclaw/agents-archive']:
        idx = source_path.find(marker)
        if idx >= 0:
            return source_path[idx:]
    return os.path.basename(source_path)


@app.route('/index-session', methods=['POST'])
def index_session_endpoint():
    """Accept a session file from a remote watcher and index it.

    Expects multipart/form-data with:
      - file: the .jsonl session file
      - source_path: original path on the source machine (for de-duplication)
    """
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400

    uploaded = request.files['file']
    source_path = request.form.get('source_path', '')
    filename = uploaded.filename or 'unknown.jsonl'

    if not filename.endswith('.jsonl'):
        return jsonify({"error": "Only .jsonl files accepted"}), 400
    if not source_path:
        return jsonify({"error": "source_path is required"}), 400

    # Reconstruct path structure for agent detection
    path_suffix = _extract_path_suffix(source_path)
    temp_dir = os.path.join(REMOTE_INDEX_TEMP_DIR, os.path.dirname(path_suffix))
    os.makedirs(temp_dir, exist_ok=True)
    temp_filepath = Path(os.path.join(temp_dir, filename))

    try:
        uploaded.save(str(temp_filepath))

        from claw_recall.indexing.indexer import index_session_file
        with get_db() as conn:
            result = index_session_file(
                temp_filepath, conn,
                generate_embeds=False,
                source_file_override=source_path,
            )
            log.info(f"index-session: {filename} -> {result.get('status')} "
                     f"({result.get('messages', 0)} msgs, agent={result.get('agent', '?')})")
            return jsonify(result), 200
    except Exception as e:
        log.error(f"index-session error for {filename}: {e}")
        return jsonify({"error": "Internal error"}), 500
    finally:
        if temp_filepath.exists():
            temp_filepath.unlink()
        try:
            os.removedirs(temp_dir)
        except OSError:
            pass


@app.route('/index-local', methods=['POST'])
def index_local_endpoint():
    """Index a local file that was rsync'd to VPS (for oversized files).

    Expects JSON: {"filepath": "/tmp/claw-recall-remote/...", "source_path": "/home/user/..."}
    """
    data = request.get_json(silent=True) or {}
    filepath_str = data.get('filepath', '')
    source_path = data.get('source_path', '')

    if not filepath_str or not source_path:
        return jsonify({"error": "filepath and source_path required"}), 400

    filepath = Path(filepath_str).resolve()
    staging = Path(REMOTE_INDEX_TEMP_DIR).resolve()
    if not filepath.exists():
        return jsonify({"error": "File not found"}), 404
    try:
        filepath.relative_to(staging)
    except ValueError:
        return jsonify({"error": "filepath must be within staging directory"}), 403

    try:
        from claw_recall.indexing.indexer import index_session_file
        with get_db(busy_timeout=60000) as conn:
            result = index_session_file(
                filepath, conn,
                generate_embeds=False,
                source_file_override=source_path,
            )
            log.info(f"index-local: {filepath.name} -> {result.get('status')} "
                     f"({result.get('messages', 0)} msgs, agent={result.get('agent', '?')})")
            return jsonify(result), 200
    except Exception as e:
        log.error(f"index-local error for {filepath.name}: {e}", exc_info=True)
        return jsonify({"error": "Processing failed"}), 500
    finally:
        # Clean up staging file to prevent /tmp from filling up (runs on success AND error)
        try:
            filepath.unlink(missing_ok=True)
        except OSError as cleanup_err:
            log.warning(f"Could not clean up staging file {filepath}: {cleanup_err}")


@app.route('/thoughts')
def thoughts_endpoint():
    """List or search captured thoughts."""
    query = request.args.get('q', '')
    limit = _safe_int(request.args.get('limit', '20'), 20, lo=1, hi=100)

    if query:
        from claw_recall.search.engine import search_thoughts
        semantic = request.args.get('semantic', 'false').lower() == 'true'
        agent = request.args.get('agent', '') or None
        results = search_thoughts(query=query, agent=agent, semantic=semantic, limit=limit)
        return jsonify({
            "thoughts": [
                {
                    "id": r.thought_id,
                    "content": r.content[:500],
                    "source": r.source,
                    "agent": r.agent,
                    "metadata": r.metadata,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "score": round(r.score, 3),
                }
                for r in results
            ]
        })

    from claw_recall.capture.thoughts import list_thoughts
    thoughts = list_thoughts(
        limit=limit,
        source=request.args.get('source') or None,
        agent=request.args.get('agent') or None,
    )
    return jsonify({"thoughts": thoughts})


@app.route('/search')
def search_endpoint():
    query = request.args.get('q', '')[:2000]
    semantic = request.args.get('semantic', 'false').lower() == 'true'
    agent = resolve_agent(request.args.get('agent', '') or None)
    source = request.args.get('source', '') or None
    files_only = request.args.get('files_only', 'false').lower() == 'true'
    convos_only = request.args.get('convos_only', 'false').lower() == 'true'

    if not query:
        return jsonify({"error": "No query provided"}), 400

    days = _safe_int(request.args.get('days', '0'), 0, lo=0)

    try:
        results = unified_search(
            query=query,
            agent=agent,
            semantic=semantic,
            files_only=files_only,
            convos_only=convos_only,
            days=days if days > 0 else None,
            limit=20,
            source=source,
        )
    except Exception as e:
        return jsonify({"error": f"Search failed: {e}", "conversations": [], "files": [], "summary": ""}), 500

    # Post-process conversation results: add deep links
    for convo in results.get("conversations", []):
        full_content = convo.pop("fullContent", convo.get("content", ""))
        convo["deepLink"] = generate_deep_link(full_content)

    return jsonify(results)


@app.route('/context')
def context_endpoint():
    """Return surrounding messages for a given message in a session."""
    session_id = request.args.get('session_id', '')
    message_id = request.args.get('message_id', '')
    radius = _safe_int(request.args.get('radius', '5'), 5, lo=1, hi=100)

    if not session_id:
        return jsonify({"error": "session_id required"}), 400

    if not message_id:
        return jsonify({"error": "message_id required"}), 400

    try:
        with get_db() as conn:
            cursor = conn.execute(
                "SELECT message_index FROM messages WHERE id = ? AND session_id = ?",
                (_safe_int(message_id, 0, lo=0), session_id)
            )

            row = cursor.fetchone()
            if not row:
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

            return jsonify({
                "session_id": session_id,
                "messages": messages,
                "target_index": target_index,
                "has_more_before": loaded_min > min_idx,
                "has_more_after": loaded_max < max_idx_all,
            })

    except Exception as e:
        return jsonify({"error": "Internal error"}), 500


@app.route('/activity')
def activity_endpoint():
    """Browse recent agent conversations -- no search query needed."""
    agent = resolve_agent(request.args.get('agent', '') or None)
    days = _safe_int(request.args.get('days', '14'), 14, lo=0)
    limit = _safe_int(request.args.get('limit', '30'), 30, lo=0, hi=100)

    try:
        with get_db() as conn:
            conn.row_factory = sqlite3.Row

            sql = f"""
                SELECT s.id, s.agent_id, s.started_at, s.ended_at, s.message_count,
                       (SELECT content FROM messages m WHERE m.session_id = s.id AND m.role = 'user' ORDER BY m.message_index ASC LIMIT 1) as first_user_msg,
                       (SELECT content FROM messages m WHERE m.session_id = s.id AND m.role = 'assistant' ORDER BY m.message_index DESC LIMIT 1) as last_assistant_msg
                FROM sessions s
                WHERE {VALID_AGENT_FILTER}
            """
            params = []
            if agent:
                sql += " AND s.agent_id = ? COLLATE NOCASE"
                params.append(agent)
            if days > 0:
                sql += " AND COALESCE(s.ended_at, s.started_at) >= datetime('now', ?)"
                params.append(f"-{days} days")
            sql += " ORDER BY COALESCE(s.ended_at, s.started_at) DESC LIMIT ?"
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
                    "ended_at": r['ended_at'],
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
                count_sql += " AND COALESCE(s.ended_at, s.started_at) >= datetime('now', ?)"
                count_params.append(f"-{days} days")
            count_sql += " GROUP BY s.agent_id ORDER BY cnt DESC"
            agent_counts = {r[0]: r[1] for r in conn.execute(count_sql, count_params).fetchall()}

            return jsonify({
                "sessions": sessions,
                "agent_counts": agent_counts,
                "total": len(sessions),
            })

    except Exception as e:
        return jsonify({"error": "Internal error"}), 500


@app.route('/recent')
def recent_endpoint():
    """Get full transcript of recent conversations -- last N minutes of messages."""
    agent = resolve_agent(request.args.get('agent', '') or None)
    minutes = _safe_int(request.args.get('minutes', '30'), 30, lo=1, hi=120)

    try:
        with get_db() as conn:
            sql = f"""
                SELECT s.id as session_id, s.agent_id, m.role, m.content,
                       m.timestamp, m.message_index, s.message_count
                FROM messages m
                JOIN sessions s ON m.session_id = s.id
                WHERE m.timestamp >= datetime('now', ?)
                  AND {VALID_AGENT_FILTER}
            """
            params: list = [f"-{minutes} minutes"]
            if agent:
                sql += " AND s.agent_id = ? COLLATE NOCASE"
                params.append(agent)
            sql += " ORDER BY m.timestamp ASC, m.message_index ASC LIMIT 500"

            rows = conn.execute(sql, params).fetchall()

            # Group by session
            sessions_map: dict = {}
            session_order: list = []
            for session_id, agent_id, role, content, timestamp, msg_idx, total_count in rows:
                if session_id not in sessions_map:
                    sessions_map[session_id] = {
                        "session_id": session_id,
                        "agent": agent_id,
                        "messages": [],
                        "total_session_messages": total_count or 0,
                    }
                    session_order.append(session_id)
                content_text = content or ""
                if role == "tool_result" and len(content_text) > 500:
                    content_text = content_text[:500] + "..."
                sessions_map[session_id]["messages"].append({
                    "role": role,
                    "content": content_text,
                    "timestamp": timestamp,
                    "message_index": msg_idx,
                })

            sessions_list = [sessions_map[sid] for sid in session_order]
            total_msgs = sum(len(s["messages"]) for s in sessions_list)

            return jsonify({
                "sessions": sessions_list,
                "total_sessions": len(sessions_list),
                "total_messages": total_msgs,
                "minutes": minutes,
                "agent_filter": agent,
            })

    except Exception as e:
        return jsonify({"error": "Internal error"}), 500


@app.route('/session')
def session_endpoint():
    """Return messages for a session, with optional windowed loading."""
    session_id = request.args.get('session_id', '')
    if not session_id:
        return jsonify({"error": "session_id required"}), 400

    around = request.args.get('around', None)
    window = _safe_int(request.args.get('window', '30'), 30, lo=1, hi=60)

    try:
        with get_db() as conn:
            sess = conn.execute(
                "SELECT id, agent_id, started_at, message_count FROM sessions WHERE id = ?",
                (session_id,)
            ).fetchone()
            if not sess:
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
        return jsonify({"error": "Internal error"}), 500


from claw_recall.maintenance.dedup import run_dry_run, delete_messages as _delete_messages


@app.route('/cleanup')
def cleanup_page():
    return render_template('cleanup.html')


@app.route('/api/cleanup/dry-run', methods=['POST'])
def cleanup_dry_run():
    try:
        result = run_dry_run(str(DB_PATH))
        return jsonify(result)
    except Exception as e:
        logging.exception("Dry-run failed")
        return jsonify({"error": str(e)}), 500


@app.route('/api/cleanup/delete', methods=['POST'])
def cleanup_delete():
    try:
        data = request.get_json(force=True)
        message_ids = data.get('message_ids', [])
        if not isinstance(message_ids, list):
            return jsonify({"error": "message_ids must be a list"}), 400
        message_ids = [int(x) for x in message_ids]
        result = _delete_messages(str(DB_PATH), message_ids)
        return jsonify(result)
    except Exception as e:
        logging.exception("Delete failed")
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', '-p', type=int, default=8765, help='Port to run on')
    parser.add_argument('--host', default='127.0.0.1', help='Host to bind to')
    args = parser.parse_args()

    print(f"Recall Web Interface running at http://localhost:{args.port}")
    # Preload embedding cache in background to avoid cold-start latency
    preload_embedding_cache()
    app.run(host=args.host, port=args.port, debug=False)
