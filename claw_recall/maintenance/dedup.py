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
        CREATE INDEX IF NOT EXISTS idx_messages_content_role
        ON messages(content, role)
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


def find_exact_duplicates(db_path: str, limit: int = 1000) -> dict:
    """
    Find groups of messages with identical (content, role), keeping the oldest (lowest id).

    Returns:
        {
            groups: [{content_preview, role, count, keep_id, delete_ids, total_bytes}],
            summary: {total_groups, total_removable, estimated_savings_mb}
        }
    """
    conn = _connect(db_path)
    try:
        _ensure_indexes(conn)

        # Step 1: find (content, role) pairs with duplicates, ordered by count desc
        # Grab the keep_id (min id) and aggregate delete candidate IDs in the query
        # Using a subquery to limit to top N groups efficiently
        cur = conn.execute("""
            SELECT
                role,
                SUBSTR(content, 1, 200) AS content_preview,
                COUNT(*) AS cnt,
                MIN(id) AS keep_id,
                SUM(LENGTH(COALESCE(content, ''))) AS total_bytes
            FROM messages
            WHERE content IS NOT NULL AND content != ''
            GROUP BY content, role
            HAVING COUNT(*) > 1
            ORDER BY COUNT(*) DESC
            LIMIT ?
        """, (limit,))

        rows = cur.fetchall()

        total_groups = 0
        total_removable = 0

        # Step 2: for each group, get the delete IDs (all ids except keep_id)
        # We do this with a targeted query per group using keep_id
        # To avoid N+1 being too slow, we batch via a single pass using ROW_NUMBER
        # But given limit=1000, N+1 with indexed queries is acceptable here.

        groups = []
        summary_cur = conn.execute("""
            SELECT COUNT(*) as total_groups,
                   SUM(cnt - 1) as total_removable
            FROM (
                SELECT COUNT(*) as cnt
                FROM messages
                WHERE content IS NOT NULL AND content != ''
                GROUP BY content, role
                HAVING COUNT(*) > 1
            )
        """)
        summary_row = summary_cur.fetchone()
        total_groups = summary_row['total_groups'] or 0
        total_removable = summary_row['total_removable'] or 0

        for row in rows:
            keep_id = row['keep_id']
            role = row['role']
            content_preview = row['content_preview']

            # Get IDs to delete (all except keep_id) — use index on (content, role)
            # We reconstruct the exact content from a single row read
            exact_cur = conn.execute(
                "SELECT content FROM messages WHERE id = ?", (keep_id,)
            )
            exact_row = exact_cur.fetchone()
            if not exact_row:
                continue
            exact_content = exact_row['content']

            del_cur = conn.execute("""
                SELECT id FROM messages
                WHERE content = ? AND role = ? AND id != ?
                LIMIT 5000
            """, (exact_content, role, keep_id))
            delete_ids = [r['id'] for r in del_cur.fetchall()]

            bytes_per_msg = len((exact_content or '').encode('utf-8'))
            total_bytes = bytes_per_msg * (row['cnt'])

            groups.append({
                'content_preview': (content_preview or '')[:100],
                'role': role,
                'count': row['cnt'],
                'keep_id': keep_id,
                'delete_ids': delete_ids,
                'total_bytes': total_bytes,
            })

        estimated_savings_mb = round(
            sum(g['total_bytes'] * (g['count'] - 1) / g['count'] for g in groups) / (1024 * 1024), 2
        )

        return {
            'groups': groups,
            'summary': {
                'total_groups': total_groups,
                'total_removable': total_removable,
                'estimated_savings_mb': estimated_savings_mb,
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

    Args:
        db_path: Path to SQLite DB
        message_ids: List of integer message IDs to delete

    Returns:
        {deleted: int, freed_bytes: int}
    """
    if not message_ids:
        return {'deleted': 0, 'freed_bytes': 0}

    conn = _connect(db_path)
    try:
        # Estimate bytes before deletion
        placeholders = ','.join('?' * len(message_ids))
        cur = conn.execute(
            f"SELECT SUM(LENGTH(COALESCE(content, ''))) as total_bytes FROM messages WHERE id IN ({placeholders})",
            message_ids
        )
        row = cur.fetchone()
        freed_bytes = row['total_bytes'] or 0

        # Delete orphaned embeddings first (FK integrity)
        conn.execute(
            f"DELETE FROM embeddings WHERE message_id IN ({placeholders})",
            message_ids
        )

        # Delete messages
        cur = conn.execute(
            f"DELETE FROM messages WHERE id IN ({placeholders})",
            message_ids
        )
        deleted = cur.rowcount

        # Clean up any embeddings whose message_id no longer exists (belt+suspenders)
        conn.execute("""
            DELETE FROM embeddings
            WHERE message_id NOT IN (SELECT id FROM messages)
        """)

        conn.commit()
        return {'deleted': deleted, 'freed_bytes': freed_bytes}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
