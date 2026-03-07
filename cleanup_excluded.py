#!/usr/bin/env python3
"""
Claw Recall — Cleanup Excluded Sessions

Reads exclude.conf and removes any already-indexed sessions whose source
filename matches an exclusion pattern. Run after updating exclude.conf to
purge historical noise from the database.

Usage:
    python3 cleanup_excluded.py              # Delete matching sessions
    python3 cleanup_excluded.py --dry-run    # Preview what would be deleted
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from index import DB_PATH, is_excluded, _load_exclude_patterns


def main():
    parser = argparse.ArgumentParser(description="Remove excluded sessions from Claw Recall database")
    parser.add_argument('--dry-run', action='store_true', help="Preview deletions without modifying the database")
    parser.add_argument('--db', type=str, default=None, help="Path to database (default: auto-detect)")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else DB_PATH
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    patterns = _load_exclude_patterns()
    if not patterns:
        print("No exclusion patterns found. Copy exclude.conf.example to exclude.conf and customize it.")
        sys.exit(0)

    print(f"Exclusion patterns: {patterns}")
    print(f"Database: {db_path}")
    print()

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")

    # Find all sessions whose source file matches an exclusion pattern
    rows = conn.execute(
        "SELECT id, agent_id, source_file, message_count FROM sessions"
    ).fetchall()

    to_delete = []
    for session_id, agent_id, source_file, msg_count in rows:
        if source_file and is_excluded(Path(source_file)):
            to_delete.append((session_id, agent_id, source_file, msg_count))

    if not to_delete:
        print("No matching sessions found. Database is clean.")
        conn.close()
        return

    print(f"Found {len(to_delete)} sessions to remove:\n")
    total_messages = 0
    for session_id, agent_id, source_file, msg_count in to_delete:
        fname = Path(source_file).name if source_file else session_id
        total_messages += msg_count or 0
        print(f"  [{agent_id:>10}]  {fname}  ({msg_count or 0} msgs)")

    print(f"\nTotal: {len(to_delete)} sessions, {total_messages} messages")

    if args.dry_run:
        print("\n--dry-run: No changes made.")
        conn.close()
        return

    # Delete in order: embeddings -> messages -> index_log -> sessions
    session_ids = [s[0] for s in to_delete]
    placeholders = ','.join(['?'] * len(session_ids))

    embed_count = conn.execute(
        f"DELETE FROM embeddings WHERE message_id IN (SELECT id FROM messages WHERE session_id IN ({placeholders}))",
        session_ids
    ).rowcount

    msg_count = conn.execute(
        f"DELETE FROM messages WHERE session_id IN ({placeholders})",
        session_ids
    ).rowcount

    # Clean up index_log by source_file
    source_files = [s[2] for s in to_delete if s[2]]
    if source_files:
        sf_placeholders = ','.join(['?'] * len(source_files))
        conn.execute(
            f"DELETE FROM index_log WHERE source_file IN ({sf_placeholders})",
            source_files
        )

    conn.execute(
        f"DELETE FROM sessions WHERE id IN ({placeholders})",
        session_ids
    )

    conn.commit()
    conn.close()

    print(f"\nDeleted: {len(session_ids)} sessions, {msg_count} messages, {embed_count} embeddings")
    print("Done.")


if __name__ == "__main__":
    main()
