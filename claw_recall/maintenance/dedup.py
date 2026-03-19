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
import unicodedata
from pathlib import Path


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_indexes(conn: sqlite3.Connection):
    """Create indexes needed for dedup queries if they don't exist."""
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_session_role_index
        ON messages(session_id, role, message_index)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_session_id
        ON messages(session_id)
    """)
    conn.commit()


def _is_single_emoji(text: str) -> bool:
    """Return True if text is a single emoji character (or simple emoji sequence)."""
    if not text:
        return False
    text = text.strip()
    if len(text) > 10:
        return False
    # Check if all chars are emoji/symbol category
    for char in text:
        cat = unicodedata.category(char)
        if cat not in ('So', 'Sm', 'Sk', 'Mn') and ord(char) < 127:
            return False
    return len(text) > 0


def find_exact_duplicates(db_path: str, limit: int = 200) -> dict:
    """
    Find messages duplicated within the SAME session (same content + session_id +
    role + message_index appearing more than once). Keeps the oldest (lowest id).

    Root cause: sessions re-indexed without cleaning old rows, or watcher + cron
    both indexing the same file. Typically produces exactly 2 copies per message.

    Returns:
        {
            groups: [{content_preview, role, session_id, message_index, count, keep_id, delete_ids, bytes_saved}],
            summary: {total_groups, total_removable, estimated_savings_mb, affected_sessions}
        }
    """
    conn = _connect(db_path)
    try:
        _ensure_indexes(conn)

        # Get overall summary first (fast with index)
        summary_cur = conn.execute("""
            SELECT COUNT(*) as total_groups,
                   SUM(cnt - 1) as total_removable,
                   COUNT(DISTINCT session_id) as affected_sessions
            FROM (
                SELECT session_id, COUNT(*) as cnt
                FROM messages
                GROUP BY session_id, role, message_index, content
                HAVING cnt > 1
            )
        """)
        summary_row = summary_cur.fetchone()
        total_groups = summary_row['total_groups'] or 0
        total_removable = summary_row['total_removable'] or 0
        affected_sessions = summary_row['affected_sessions'] or 0

        # Get top duplicate groups by count, aggregated per SESSION
        # (show per-session summary, not per-message-index to avoid overwhelming the UI)
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
                GROUP BY session_id, role, message_index, content
                HAVING COUNT(*) > 1
            )
            GROUP BY session_id
            ORDER BY extra_rows DESC
            LIMIT ?
        """, (limit,))

        groups = []
        total_bytes_saved = 0

        for row in cur.fetchall():
            sid = row['session_id']

            # Get session metadata for context
            sess = conn.execute(
                "SELECT agent_id, channel, started_at, message_count FROM sessions WHERE id = ?",
                (sid,)
            ).fetchone()

            # Get the actual delete IDs for this session (keep lowest id per group)
            del_cur = conn.execute("""
                SELECT id FROM messages
                WHERE session_id = ?
                  AND id NOT IN (
                      SELECT MIN(id) FROM messages
                      WHERE session_id = ?
                      GROUP BY role, message_index, content
                  )
            """, (sid, sid))
            delete_ids = [r['id'] for r in del_cur.fetchall()]

            # Get a sample message for preview
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

        # Empty / NULL content
        cur = conn.execute("""
            SELECT id, content, role, session_id
            FROM messages
            WHERE content IS NULL OR TRIM(content) = ''
            LIMIT ?
        """, (limit,))
        for row in cur.fetchall():
            items.append({
                'id': row['id'],
                'content': row['content'] or '',
                'role': row['role'],
                'session_id': row['session_id'],
                'category': 'empty',
            })
            by_category['empty'] += 1

        # Orphaned messages (no matching session)
        remaining = limit - len(items)
        if remaining > 0:
            cur = conn.execute("""
                SELECT m.id, m.content, m.role, m.session_id
                FROM messages m
                LEFT JOIN sessions s ON m.session_id = s.id
                WHERE s.id IS NULL
                LIMIT ?
            """, (remaining,))
            for row in cur.fetchall():
                items.append({
                    'id': row['id'],
                    'content': (row['content'] or '')[:200],
                    'role': row['role'],
                    'session_id': row['session_id'],
                    'category': 'orphaned',
                })
                by_category['orphaned'] += 1

        # Count total orphaned (for summary, not limited)
        orphan_total_cur = conn.execute("""
            SELECT COUNT(*) as cnt
            FROM messages m
            LEFT JOIN sessions s ON m.session_id = s.id
            WHERE s.id IS NULL
        """)
        total_orphaned = orphan_total_cur.fetchone()['cnt']

        # Single emoji (flagged only)
        remaining = limit - len(items)
        if remaining > 0:
            cur = conn.execute("""
                SELECT id, content, role, session_id
                FROM messages
                WHERE LENGTH(content) BETWEEN 1 AND 10
                  AND content IS NOT NULL
                LIMIT ?
            """, (remaining * 5,))  # fetch more, filter in Python
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

        # Summary counts
        empty_total_cur = conn.execute("""
            SELECT COUNT(*) as cnt FROM messages
            WHERE content IS NULL OR TRIM(content) = ''
        """)
        total_empty = empty_total_cur.fetchone()['cnt']

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


def run_dry_run(db_path: str, categories: list | None = None) -> dict:
    """
    Run all detection passes and return combined results.

    Args:
        db_path: Path to SQLite DB
        categories: List of categories to run ('duplicates', 'junk'). None = all.

    Returns:
        Combined results dict with duplicates, junk, and summary.
    """
    if categories is None:
        categories = ['duplicates', 'junk']

    result = {
        'duplicates': None,
        'junk': None,
        'summary': {
            'total_messages': 0,
            'total_sessions': 0,
            'duplicates_found': 0,
            'junk_found': 0,
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

    if 'duplicates' in categories:
        dup_result = find_exact_duplicates(db_path)
        result['duplicates'] = dup_result
        result['summary']['duplicates_found'] = dup_result['summary']['total_removable']
        result['summary']['estimated_savings_mb'] += dup_result['summary']['estimated_savings_mb']

    if 'junk' in categories:
        junk_result = find_junk(db_path)
        result['junk'] = junk_result
        result['summary']['junk_found'] = junk_result['summary']['total']

    return result


def delete_messages(db_path: str, message_ids: list) -> dict:
    """
    Delete specified messages by ID, plus orphaned embeddings.
    Batches deletes in chunks of 500 to avoid SQLite variable limits.

    Args:
        db_path: Path to SQLite DB
        message_ids: List of integer message IDs to delete

    Returns:
        {deleted: int, freed_bytes: int}
    """
    if not message_ids:
        return {'deleted': 0, 'freed_bytes': 0}

    conn = _connect(db_path)
    BATCH = 500
    try:
        freed_bytes = 0
        deleted = 0

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

            # Delete embeddings first
            conn.execute(
                f"DELETE FROM embeddings WHERE message_id IN ({placeholders})",
                batch
            )

            # Delete messages
            cur = conn.execute(
                f"DELETE FROM messages WHERE id IN ({placeholders})",
                batch
            )
            deleted += cur.rowcount

        conn.commit()
        return {'deleted': deleted, 'freed_bytes': freed_bytes}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
