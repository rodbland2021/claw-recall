#!/usr/bin/env python3
"""
DB Deduplication module for Claw Recall.

All queries are designed for efficiency on large DBs (4 GB+):
- Uses GROUP BY / HAVING for duplicate detection (no full table scan in Python)
- Uses window functions (ROW_NUMBER) for keeping the oldest copy
- WAL mode + busy_timeout for safe concurrent access
- Indexes added if missing
"""

import sqlite3
import re
import json
import logging
import unicodedata
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# Snapshot cache for instant page loads
_CACHE_DIR = Path(__file__).parent.parent.parent / 'data'
_CACHE_FILE = _CACHE_DIR / 'cleanup_cache.json'
_CACHE_MAX_AGE_SECONDS = 600  # 10 minutes

# Noise patterns — repetitive status messages that pollute search results.
# Each pattern is (compiled_regex, description) for the UI.
NOISE_PATTERNS = [
    (re.compile(r'^HEARTBEAT_OK$'), 'Heartbeat ping'),
    (re.compile(r'^NO_REPLY$'), 'No-reply marker'),
    (re.compile(r'^Read HEARTBEAT\.md'), 'Heartbeat instruction'),
    (re.compile(r'^You are running a boot check'), 'Boot check prompt'),
    (re.compile(r'Gateway restart(?:ed|ing)\b.*(?:back online|reconnect)', re.IGNORECASE), 'Gateway restart status'),
    (re.compile(r'^Gateway is back up'), 'Gateway back online'),
    (re.compile(r'^Gateway restarted — back online'), 'Gateway restart notice'),
    (re.compile(r'OpenClaw Health Check Report'), 'Health check webhook'),
    (re.compile(r'^SECURITY NOTICE: The following content is from an EXTERNAL'), 'External content wrapper'),
    (re.compile(r'^If BOOT\.md asks you to send a message'), 'Boot instruction footer'),
    (re.compile(r'^If nothing needs attention.*reply with ONLY: NO_REPLY', re.DOTALL), 'Boot no-reply instruction'),
]


_indexes_ensured = False


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-16384")       # 16MB page cache (up from default 2MB)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_indexes(conn: sqlite3.Connection):
    """Create indexes needed for dedup queries if they don't exist. Runs once per process."""
    global _indexes_ensured
    if _indexes_ensured:
        return
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_session_role_index
        ON messages(session_id, role, message_index)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_session_id
        ON messages(session_id)
    """)
    conn.commit()
    _indexes_ensured = True


def _ensure_cleanup_runs_table(conn: sqlite3.Connection):
    """Create cleanup_runs table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cleanup_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ran_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            mode TEXT NOT NULL,
            categories TEXT,
            duplicates_found INTEGER DEFAULT 0,
            junk_found INTEGER DEFAULT 0,
            noise_found INTEGER DEFAULT 0,
            orphaned_embeddings_found INTEGER DEFAULT 0,
            deleted INTEGER DEFAULT 0,
            freed_bytes INTEGER DEFAULT 0
        )
    """)
    conn.commit()


def _is_single_emoji(text: str) -> bool:
    """Return True if text is a single emoji character (or simple emoji sequence)."""
    if not text:
        return False
    text = text.strip()
    if len(text) > 10:
        return False
    for char in text:
        cat = unicodedata.category(char)
        if cat not in ('So', 'Sm', 'Sk', 'Mn') and ord(char) < 127:
            return False
    return len(text) > 0


def _matches_noise_pattern(content: str) -> str | None:
    """Return the description if content matches a noise pattern, else None."""
    if not content:
        return None
    for pattern, desc in NOISE_PATTERNS:
        if pattern.search(content):
            return desc
    return None


def find_exact_duplicates(db_path: str, limit: int = 200) -> dict:
    """
    Find messages duplicated within the SAME session (same content + session_id +
    role + message_index appearing more than once). Keeps the oldest (lowest id).

    Root cause: sessions re-indexed without cleaning old rows, or watcher + cron
    both indexing the same file. Typically produces exactly 2 copies per message.

    Returns:
        {
            groups: [{session_id, agent, channel, started_at, expected_messages,
                      actual_messages, duplicate_rows, sample_preview, sample_role,
                      delete_ids, bytes_saved}],
            summary: {total_groups, total_removable, estimated_savings_mb, affected_sessions}
        }
    """
    conn = _connect(db_path)
    try:
        _ensure_indexes(conn)

        # Single scan: summary + per-session breakdown in one query.
        # GROUP BY session_id, role, message_index (without content) is 13x faster
        # because SQLite doesn't have to compare full text content strings.
        # Re-indexed duplicates always share the same message_index, so content
        # comparison is redundant.
        cur = conn.execute("""
            SELECT
                session_id,
                COUNT(*) as dup_messages,
                SUM(extra) as extra_rows,
                SUM(bytes_wasted) as bytes_wasted
            FROM (
                SELECT
                    session_id,
                    COUNT(*) - 1 as extra,
                    LENGTH(content) * (COUNT(*) - 1) as bytes_wasted
                FROM messages
                GROUP BY session_id, role, message_index
                HAVING COUNT(*) > 1
            )
            GROUP BY session_id
            ORDER BY extra_rows DESC
        """)
        all_sessions = cur.fetchall()

        # Derive summary from the full result set
        total_groups = sum(r['dup_messages'] for r in all_sessions)
        total_removable = sum(r['extra_rows'] for r in all_sessions)
        affected_sessions = len(all_sessions)

        groups = []
        total_bytes_saved = 0

        for row in all_sessions[:limit]:
            sid = row['session_id']

            sess = conn.execute(
                "SELECT agent_id, channel, started_at, message_count FROM sessions WHERE id = ?",
                (sid,)
            ).fetchone()

            del_cur = conn.execute("""
                SELECT id FROM messages
                WHERE session_id = ?
                  AND id NOT IN (
                      SELECT MIN(id) FROM messages
                      WHERE session_id = ?
                      GROUP BY role, message_index
                  )
            """, (sid, sid))
            delete_ids = [r['id'] for r in del_cur.fetchall()]

            sample = conn.execute("""
                SELECT SUBSTR(content, 1, 100) as preview, role
                FROM messages WHERE session_id = ? AND content IS NOT NULL
                ORDER BY message_index LIMIT 1
            """, (sid,)).fetchone()

            bytes_saved = row['bytes_wasted'] or 0
            total_bytes_saved += bytes_saved

            groups.append({
                'session_id': sid,
                'agent': sess['agent_id'] if sess else 'unknown',
                'channel': sess['channel'] if sess else 'unknown',
                'started_at': sess['started_at'] if sess else None,
                'expected_messages': sess['message_count'] if sess else 0,
                'actual_messages': (sess['message_count'] or 0) + row['extra_rows'] if sess else row['extra_rows'],
                'duplicate_rows': row['extra_rows'],
                'sample_preview': sample['preview'] if sample else '',
                'sample_role': sample['role'] if sample else '',
                'delete_ids': delete_ids,
                'bytes_saved': bytes_saved,
            })

        estimated_savings_mb = round(total_bytes_saved / (1024 * 1024), 2)

        return {
            'groups': groups,
            'summary': {
                'total_groups': total_groups,
                'total_removable': total_removable,
                'estimated_savings_mb': estimated_savings_mb,
                'affected_sessions': affected_sessions,
            }
        }
    finally:
        conn.close()


def find_cross_session_duplicates(db_path: str, similarity: str = 'exact', limit: int = 200) -> dict:
    """
    Find messages with identical or near-identical content across DIFFERENT sessions.

    Root cause: same conversation indexed from active session file, archive copy,
    and/or backup — producing 2-10 copies of every message across session IDs.

    Similarity tiers:
        'exact'  (1.0)  — identical content, different session_id
        'high'   (0.95) — first 500 chars normalized match
        'medium' (0.85) — first 200 chars normalized match

    Returns:
        {
            groups: [{content_preview, role, copies, sessions, keep_id, delete_ids, bytes_saved}],
            summary: {total_groups, total_removable, estimated_savings_mb, similarity}
        }
    """
    conn = _connect(db_path)
    try:
        # Choose grouping expression based on similarity tier
        if similarity == 'medium':
            group_expr = "LOWER(SUBSTR(TRIM(content), 1, 200))"
            score = 0.85
        elif similarity == 'high':
            group_expr = "LOWER(SUBSTR(TRIM(content), 1, 500))"
            score = 0.95
        else:  # exact
            group_expr = "content"
            score = 1.0

        # Summary: count groups and removable messages
        summary_row = conn.execute(f"""
            SELECT COUNT(*) as groups, SUM(total - 1) as removable,
                   SUM(bytes * (total - 1)) as bytes_wasted
            FROM (
                SELECT COUNT(*) as total,
                       AVG(LENGTH(content)) as bytes
                FROM messages
                WHERE content IS NOT NULL AND LENGTH(content) > 20
                GROUP BY {group_expr}
                HAVING COUNT(DISTINCT session_id) >= 2
            )
        """).fetchone()
        total_groups = summary_row['groups'] or 0
        total_removable = summary_row['removable'] or 0
        bytes_wasted = summary_row['bytes_wasted'] or 0

        # Get top groups for display — NO per-group sub-queries for speed
        cur = conn.execute(f"""
            SELECT {group_expr} as fingerprint,
                   SUBSTR(content, 1, 200) as preview,
                   role,
                   COUNT(*) as copies,
                   COUNT(DISTINCT session_id) as sessions,
                   MIN(id) as keep_id,
                   SUM(LENGTH(content)) as total_bytes
            FROM messages
            WHERE content IS NOT NULL AND LENGTH(content) > 20
            GROUP BY {group_expr}
            HAVING COUNT(DISTINCT session_id) >= 2
            ORDER BY copies DESC
            LIMIT ?
        """, (limit,))

        groups = []
        for row in cur.fetchall():
            groups.append({
                'content_preview': row['preview'],
                'role': row['role'],
                'copies': row['copies'],
                'sessions': row['sessions'],
                'keep_id': row['keep_id'],
                'fingerprint': row['fingerprint'][:200] if similarity != 'exact' else None,
                'bytes_saved': row['total_bytes'] - (row['total_bytes'] // row['copies']),
                'score': score,
            })

        estimated_savings_mb = round(bytes_wasted / (1024 * 1024), 2)

        return {
            'groups': groups,
            'summary': {
                'total_groups': total_groups,
                'total_removable': total_removable,
                'estimated_savings_mb': estimated_savings_mb,
                'similarity': similarity,
                'score': score,
            }
        }
    finally:
        conn.close()


def _count_cross_session_duplicates(db_path: str) -> dict:
    """Fast count-only query for cross-session duplicates. No per-group detail."""
    conn = _connect(db_path)
    try:
        row = conn.execute("""
            SELECT COUNT(*) as groups, SUM(total - 1) as removable,
                   SUM(avg_bytes * (total - 1)) as bytes_wasted
            FROM (
                SELECT COUNT(*) as total, AVG(LENGTH(content)) as avg_bytes
                FROM messages
                WHERE content IS NOT NULL AND LENGTH(content) > 20
                GROUP BY content
                HAVING COUNT(DISTINCT session_id) >= 2
            )
        """).fetchone()
        return {
            'total_groups': row['groups'] or 0,
            'total_removable': row['removable'] or 0,
            'estimated_savings_mb': round((row['bytes_wasted'] or 0) / (1024 * 1024), 2),
        }
    finally:
        conn.close()


def find_junk(db_path: str, limit: int = 500) -> dict:
    """
    Find junk/noise messages:
    - Empty or whitespace-only content
    - Orphaned messages (session_id not in sessions table)
    - Single emoji responses (flagged, not auto-selected)

    Returns:
        {
            items: [{id, content, role, session_id, category}],
            summary: {total, by_category: {empty, orphaned, single_char}}
        }
    """
    conn = _connect(db_path)
    try:
        items = []
        by_category = {'empty': 0, 'orphaned': 0, 'single_char': 0}

        # Combined: get empty items + count in one query using window function
        cur = conn.execute("""
            SELECT id, content, role, session_id, COUNT(*) OVER() as total_cnt
            FROM messages
            WHERE content IS NULL OR TRIM(content) = ''
            LIMIT ?
        """, (limit,))
        empty_rows = cur.fetchall()
        total_empty = empty_rows[0]['total_cnt'] if empty_rows else 0
        for row in empty_rows:
            items.append({
                'id': row['id'],
                'content': row['content'] or '',
                'role': row['role'],
                'session_id': row['session_id'],
                'category': 'empty',
            })
            by_category['empty'] += 1

        # Combined: orphaned items + count in one query
        remaining = limit - len(items)
        if remaining > 0:
            cur = conn.execute("""
                SELECT m.id, m.content, m.role, m.session_id,
                       COUNT(*) OVER() as total_cnt
                FROM messages m
                LEFT JOIN sessions s ON m.session_id = s.id
                WHERE s.id IS NULL
                LIMIT ?
            """, (remaining,))
            orphan_rows = cur.fetchall()
            total_orphaned = orphan_rows[0]['total_cnt'] if orphan_rows else 0
        else:
            total_orphaned = conn.execute("""
                SELECT COUNT(*) as cnt FROM messages m
                LEFT JOIN sessions s ON m.session_id = s.id WHERE s.id IS NULL
            """).fetchone()['cnt']
            orphan_rows = []

        for row in orphan_rows:
            items.append({
                'id': row['id'],
                'content': (row['content'] or '')[:200],
                'role': row['role'],
                'session_id': row['session_id'],
                'category': 'orphaned',
            })
            by_category['orphaned'] += 1

        # Single emoji — small sample only, no full DB count scan
        remaining = limit - len(items)
        if remaining > 0:
            cur = conn.execute("""
                SELECT id, content, role, session_id
                FROM messages
                WHERE LENGTH(content) BETWEEN 1 AND 10
                  AND content IS NOT NULL
                LIMIT ?
            """, (remaining * 5,))
            emoji_added = 0
            for row in cur.fetchall():
                if _is_single_emoji(row['content']):
                    items.append({
                        'id': row['id'],
                        'content': row['content'],
                        'role': row['role'],
                        'session_id': row['session_id'],
                        'category': 'single_char',
                    })
                    by_category['single_char'] += 1
                    emoji_added += 1
                    if emoji_added >= remaining:
                        break

        total = total_empty + total_orphaned + by_category['single_char']

        return {
            'items': items,
            'summary': {
                'total': total,
                'by_category': {
                    'empty': total_empty,
                    'orphaned': total_orphaned,
                    'single_char': by_category['single_char'],
                }
            }
        }
    finally:
        conn.close()


def find_noise(db_path: str, limit: int = 500) -> dict:
    """
    Find repetitive status/noise messages across ALL sessions.

    Targets: HEARTBEAT_OK, boot check prompts, gateway restart notices,
    health check webhooks, NO_REPLY markers, etc.

    Uses SQL LIKE pre-filters to avoid scanning all 1M+ messages in Python.

    Returns:
        {
            items: [{id, content, role, session_id, pattern_desc, category}],
            summary: {total, by_pattern: {pattern_desc: count}}
        }
    """
    conn = _connect(db_path)
    try:
        by_pattern = {}
        items = []

        # Step 1: Count totals using lightweight query (id + content only, no extra columns)
        like_clauses = " OR ".join(f"content LIKE ?" for _ in SQL_LIKE_FILTERS)
        count_cur = conn.execute(
            f"SELECT id, content FROM messages WHERE content IS NOT NULL AND ({like_clauses})",
            SQL_LIKE_FILTERS
        )
        # Use fetchone() loop to avoid loading all 40K rows into memory at once
        all_noise_ids = []
        while True:
            row = count_cur.fetchone()
            if row is None:
                break
            desc = _matches_noise_pattern(row['content'])
            if desc:
                by_pattern[desc] = by_pattern.get(desc, 0) + 1
                if len(all_noise_ids) < limit:
                    all_noise_ids.append(row['id'])

        total = sum(by_pattern.values())

        # Step 2: Fetch display details only for the limited sample
        if all_noise_ids:
            placeholders = ','.join('?' * len(all_noise_ids))
            detail_cur = conn.execute(
                f"SELECT id, SUBSTR(content, 1, 300) as content_preview, content, role, session_id "
                f"FROM messages WHERE id IN ({placeholders})",
                all_noise_ids
            )
            for row in detail_cur.fetchall():
                desc = _matches_noise_pattern(row['content'])
                items.append({
                    'id': row['id'],
                    'content': row['content_preview'],
                    'role': row['role'],
                    'session_id': row['session_id'],
                    'pattern_desc': desc or 'noise',
                    'category': 'noise',
                })

        return {
            'items': items,
            'summary': {
                'total': total,
                'by_pattern': by_pattern,
            }
        }
    finally:
        conn.close()


def find_orphaned_embeddings(db_path: str, limit: int = 500) -> dict:
    """
    Find embeddings whose message_id doesn't exist in the messages table.

    Each orphaned embedding wastes ~6KB (1536 dims × 4 bytes float32).

    Returns:
        {
            items: [{embedding_id, message_id}],
            summary: {total, estimated_savings_mb}
        }
    """
    conn = _connect(db_path)
    try:
        # Total count
        total = conn.execute("""
            SELECT COUNT(*) as cnt FROM embeddings e
            LEFT JOIN messages m ON e.message_id = m.id
            WHERE m.id IS NULL
        """).fetchone()['cnt']

        # Get sample for display
        cur = conn.execute("""
            SELECT e.id as embedding_id, e.message_id
            FROM embeddings e
            LEFT JOIN messages m ON e.message_id = m.id
            WHERE m.id IS NULL
            LIMIT ?
        """, (limit,))
        items = [{'embedding_id': r['embedding_id'], 'message_id': r['message_id']}
                 for r in cur.fetchall()]

        # 1536 dims × 4 bytes × total rows + overhead
        estimated_bytes = total * (1536 * 4 + 50)
        estimated_savings_mb = round(estimated_bytes / (1024 * 1024), 2)

        return {
            'items': items,
            'summary': {
                'total': total,
                'estimated_savings_mb': estimated_savings_mb,
            }
        }
    finally:
        conn.close()


def run_dry_run(db_path: str, categories: list | None = None) -> dict:
    """
    Run all detection passes and return combined results.

    Args:
        db_path: Path to SQLite DB
        categories: List of categories to run. None = all.
            Options: 'duplicates', 'junk', 'noise', 'orphaned_embeddings'

    Returns:
        Combined results dict with all detection results and summary.
    """
    if categories is None:
        categories = ['duplicates', 'junk', 'noise', 'orphaned_embeddings', 'similar']

    import concurrent.futures

    result = {
        'duplicates': None,
        'junk': None,
        'noise': None,
        'orphaned_embeddings': None,
        'similar_count': None,
        'summary': {
            'total_messages': 0,
            'total_sessions': 0,
            'duplicates_found': 0,
            'junk_found': 0,
            'noise_found': 0,
            'orphaned_embeddings_found': 0,
            'similar_found': 0,
            'estimated_savings_mb': 0,
        }
    }

    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) as cnt FROM messages").fetchone()
        result['summary']['total_messages'] = row['cnt']
        row = conn.execute("SELECT COUNT(*) as cnt FROM sessions").fetchone()
        result['summary']['total_sessions'] = row['cnt']
    finally:
        conn.close()

    # Run all detection passes in parallel — each opens its own WAL connection
    futures = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        if 'duplicates' in categories:
            futures['duplicates'] = pool.submit(find_exact_duplicates, db_path)
        if 'junk' in categories:
            futures['junk'] = pool.submit(find_junk, db_path)
        if 'noise' in categories:
            futures['noise'] = pool.submit(find_noise, db_path)
        if 'orphaned_embeddings' in categories:
            futures['orphaned_embeddings'] = pool.submit(find_orphaned_embeddings, db_path)
        if 'similar' in categories:
            futures['similar'] = pool.submit(_count_cross_session_duplicates, db_path)

    if 'duplicates' in futures:
        dup_result = futures['duplicates'].result()
        result['duplicates'] = dup_result
        result['summary']['duplicates_found'] = dup_result['summary']['total_removable']
        result['summary']['estimated_savings_mb'] += dup_result['summary']['estimated_savings_mb']

    if 'junk' in futures:
        junk_result = futures['junk'].result()
        result['junk'] = junk_result
        result['summary']['junk_found'] = junk_result['summary']['total']

    if 'noise' in futures:
        noise_result = futures['noise'].result()
        result['noise'] = noise_result
        result['summary']['noise_found'] = noise_result['summary']['total']

    if 'orphaned_embeddings' in futures:
        orphan_result = futures['orphaned_embeddings'].result()
        result['orphaned_embeddings'] = orphan_result
        result['summary']['orphaned_embeddings_found'] = orphan_result['summary']['total']
        result['summary']['estimated_savings_mb'] += orphan_result['summary']['estimated_savings_mb']

    if 'similar' in futures:
        similar_count = futures['similar'].result()
        result['similar_count'] = similar_count
        result['summary']['similar_found'] = similar_count['total_removable']
        result['summary']['estimated_savings_mb'] += similar_count['estimated_savings_mb']

    # Log the dry run
    _log_cleanup_run(db_path, 'dry_run', result['summary'])

    # Cache the result for instant page loads
    _save_cache(result)

    return result


def _save_cache(result: dict):
    """Save dry-run result to disk for instant page loads."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache = {'timestamp': datetime.utcnow().isoformat(), 'result': result}
        tmp = _CACHE_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(cache, default=str))
        tmp.rename(_CACHE_FILE)
    except Exception as e:
        log.warning(f"Failed to save cleanup cache: {e}")


def get_cached_dry_run() -> dict | None:
    """Return cached dry-run result if fresh enough, else None."""
    try:
        if not _CACHE_FILE.exists():
            return None
        cache = json.loads(_CACHE_FILE.read_text())
        ts = datetime.fromisoformat(cache['timestamp'])
        age = (datetime.utcnow() - ts).total_seconds()
        if age > _CACHE_MAX_AGE_SECONDS:
            return None
        result = cache['result']
        result['cached'] = True
        result['cache_age_seconds'] = int(age)
        return result
    except Exception as e:
        log.warning(f"Failed to read cleanup cache: {e}")
        return None


def get_all_noise_ids(db_path: str) -> list:
    """Return all message IDs matching noise patterns. For bulk delete."""
    conn = _connect(db_path)
    try:
        like_clauses = " OR ".join(f"content LIKE ?" for _ in SQL_LIKE_FILTERS)
        cur = conn.execute(
            f"SELECT id, content FROM messages WHERE content IS NOT NULL AND ({like_clauses})",
            SQL_LIKE_FILTERS
        )
        ids = []
        for row in cur.fetchall():
            if _matches_noise_pattern(row['content']):
                ids.append(row['id'])
        return ids
    finally:
        conn.close()


def get_cross_session_delete_ids(db_path: str, similarity: str = 'exact') -> list:
    """Return all cross-session duplicate message IDs for bulk delete.

    Keeps the oldest (MIN id) per group, returns all others.
    """
    conn = _connect(db_path)
    try:
        if similarity == 'medium':
            group_expr = "LOWER(SUBSTR(TRIM(content), 1, 200))"
        elif similarity == 'high':
            group_expr = "LOWER(SUBSTR(TRIM(content), 1, 500))"
        else:
            group_expr = "content"

        # Single query: find all IDs that are NOT the MIN(id) per content group
        cur = conn.execute(f"""
            SELECT id FROM messages
            WHERE content IS NOT NULL AND LENGTH(content) > 20
              AND id NOT IN (
                  SELECT MIN(id) FROM messages
                  WHERE content IS NOT NULL AND LENGTH(content) > 20
                  GROUP BY {group_expr}
              )
              AND {group_expr} IN (
                  SELECT {group_expr} FROM messages
                  WHERE content IS NOT NULL AND LENGTH(content) > 20
                  GROUP BY {group_expr}
                  HAVING COUNT(DISTINCT session_id) >= 2
              )
        """)
        return [r['id'] for r in cur.fetchall()]
    finally:
        conn.close()


def get_all_junk_ids(db_path: str) -> list:
    """Return all junk message IDs (empty + orphaned + single emoji). For bulk delete."""
    conn = _connect(db_path)
    try:
        ids = []
        # Empty / NULL content
        cur = conn.execute(
            "SELECT id FROM messages WHERE content IS NULL OR TRIM(content) = ''"
        )
        ids.extend(r['id'] for r in cur.fetchall())

        # Orphaned messages (no parent session)
        cur = conn.execute("""
            SELECT m.id FROM messages m
            LEFT JOIN sessions s ON m.session_id = s.id
            WHERE s.id IS NULL
        """)
        ids.extend(r['id'] for r in cur.fetchall())

        # Single emoji messages
        cur = conn.execute("""
            SELECT id, content FROM messages
            WHERE LENGTH(content) BETWEEN 1 AND 10
              AND content IS NOT NULL
        """)
        for row in cur.fetchall():
            if _is_single_emoji(row['content']):
                ids.append(row['id'])

        return ids
    finally:
        conn.close()


# SQL LIKE prefixes for noise pre-filtering (shared between find_noise and get_all_noise_ids)
SQL_LIKE_FILTERS = [
    "HEARTBEAT_OK",
    "NO_REPLY",
    "Read HEARTBEAT%",
    "You are running a boot check%",
    "Gateway restart%",
    "Gateway is back%",
    "%OpenClaw Health Check Report%",
    "SECURITY NOTICE: The following content%",
    "If BOOT.md asks%",
    "If nothing needs attention%",
]


def delete_messages(db_path: str, message_ids: list) -> dict:
    """
    Delete specified messages by ID, plus their embeddings.
    Batches deletes in chunks of 500 to avoid SQLite variable limits.
    After deletion: updates session message counts and checks FTS5 integrity.

    Args:
        db_path: Path to SQLite DB
        message_ids: List of integer message IDs to delete

    Returns:
        {deleted: int, freed_bytes: int, sessions_updated: int}
    """
    if not message_ids:
        return {'deleted': 0, 'freed_bytes': 0, 'sessions_updated': 0}

    conn = _connect(db_path)
    BATCH = 500
    try:
        freed_bytes = 0
        deleted = 0
        affected_sessions = set()

        # Collect affected session IDs before deleting
        for i in range(0, len(message_ids), BATCH):
            batch = message_ids[i:i + BATCH]
            placeholders = ','.join('?' * len(batch))
            cur = conn.execute(
                f"SELECT DISTINCT session_id FROM messages WHERE id IN ({placeholders})",
                batch
            )
            for row in cur.fetchall():
                affected_sessions.add(row['session_id'])

        # Delete in batches
        for i in range(0, len(message_ids), BATCH):
            batch = message_ids[i:i + BATCH]
            placeholders = ','.join('?' * len(batch))

            # Estimate bytes
            cur = conn.execute(
                f"SELECT SUM(LENGTH(COALESCE(content, ''))) as total_bytes "
                f"FROM messages WHERE id IN ({placeholders})",
                batch
            )
            row = cur.fetchone()
            freed_bytes += row['total_bytes'] or 0

            # Delete embeddings first (FK)
            conn.execute(
                f"DELETE FROM embeddings WHERE message_id IN ({placeholders})",
                batch
            )

            # Delete messages (FTS5 trigger handles messages_fts)
            cur = conn.execute(
                f"DELETE FROM messages WHERE id IN ({placeholders})",
                batch
            )
            deleted += cur.rowcount

        # Update session message counts
        sessions_updated = 0
        for sid in affected_sessions:
            actual = conn.execute(
                "SELECT COUNT(*) as cnt FROM messages WHERE session_id = ?",
                (sid,)
            ).fetchone()['cnt']
            conn.execute(
                "UPDATE sessions SET message_count = ? WHERE id = ?",
                (actual, sid)
            )
            sessions_updated += 1

        conn.commit()

        # FTS5 integrity check (non-fatal)
        try:
            conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('integrity-check')")
        except Exception as e:
            log.warning(f"FTS5 integrity check failed, rebuilding: {e}")
            try:
                conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
                conn.commit()
            except Exception as e2:
                log.error(f"FTS5 rebuild failed: {e2}")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Log after closing main connection to avoid lock contention
    _log_cleanup_run(db_path, 'delete', {
        'deleted': deleted,
        'freed_bytes': freed_bytes,
    })

    return {
        'deleted': deleted,
        'freed_bytes': freed_bytes,
        'sessions_updated': sessions_updated,
    }


def delete_orphaned_embeddings(db_path: str) -> dict:
    """
    Delete embeddings whose message_id doesn't exist in messages table.

    Returns:
        {deleted: int, freed_bytes: int}
    """
    conn = _connect(db_path)
    try:
        # Count first
        total = conn.execute("""
            SELECT COUNT(*) as cnt FROM embeddings e
            LEFT JOIN messages m ON e.message_id = m.id
            WHERE m.id IS NULL
        """).fetchone()['cnt']

        if total == 0:
            return {'deleted': 0, 'freed_bytes': 0}

        # Estimate bytes (1536 dims × 4 bytes + row overhead)
        freed_bytes = total * (1536 * 4 + 50)

        # Delete
        cur = conn.execute("""
            DELETE FROM embeddings WHERE id IN (
                SELECT e.id FROM embeddings e
                LEFT JOIN messages m ON e.message_id = m.id
                WHERE m.id IS NULL
            )
        """)
        deleted = cur.rowcount
        conn.commit()

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    _log_cleanup_run(db_path, 'delete_orphaned_embeddings', {
        'deleted': deleted,
        'freed_bytes': freed_bytes,
    })

    return {'deleted': deleted, 'freed_bytes': freed_bytes}


def _log_cleanup_run(db_path: str, mode: str, summary: dict):
    """Log a cleanup run to the cleanup_runs table."""
    conn = _connect(db_path)
    try:
        _ensure_cleanup_runs_table(conn)
        conn.execute("""
            INSERT INTO cleanup_runs (mode, duplicates_found, junk_found,
                noise_found, orphaned_embeddings_found, deleted, freed_bytes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            mode,
            summary.get('duplicates_found', 0),
            summary.get('junk_found', 0),
            summary.get('noise_found', 0),
            summary.get('orphaned_embeddings_found', 0),
            summary.get('deleted', 0),
            summary.get('freed_bytes', 0),
        ))
        conn.commit()
    except Exception as e:
        log.warning(f"Failed to log cleanup run: {e}")
    finally:
        conn.close()


def get_cleanup_history(db_path: str, limit: int = 20) -> list:
    """Return recent cleanup runs."""
    conn = _connect(db_path)
    try:
        _ensure_cleanup_runs_table(conn)
        cur = conn.execute("""
            SELECT * FROM cleanup_runs
            ORDER BY ran_at DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
