#!/usr/bin/env python3
"""
One-time migration: scan all messages and thoughts in the database,
redact any secrets found, and report what was changed.

Usage:
    python3 -m scripts.redact_historical                    # Dry run (default)
    python3 -m scripts.redact_historical --apply            # Apply changes
    python3 -m scripts.redact_historical --apply --db /path/to/db
"""

import argparse
import sqlite3
import sys
from pathlib import Path

# Ensure repo root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from claw_recall.config import DB_PATH, redact_secrets


def redact_table(conn: sqlite3.Connection, table: str, id_col: str, content_col: str,
                 apply: bool = False) -> dict:
    """Scan a table and redact secrets in the content column.

    Returns stats: {scanned, redacted, sample_ids}.
    """
    cursor = conn.execute(f"SELECT {id_col}, {content_col} FROM {table}")
    stats = {"scanned": 0, "redacted": 0, "sample_ids": []}
    batch = []

    while True:
        rows = cursor.fetchmany(5000)
        if not rows:
            break
        for row_id, content in rows:
            stats["scanned"] += 1
            if not content:
                continue
            redacted = redact_secrets(content)
            if redacted != content:
                stats["redacted"] += 1
                if len(stats["sample_ids"]) < 20:
                    stats["sample_ids"].append(row_id)
                if apply:
                    batch.append((redacted, row_id))

        # Apply in batches
        if apply and batch:
            conn.executemany(
                f"UPDATE {table} SET {content_col} = ? WHERE {id_col} = ?",
                batch
            )
            conn.commit()
            batch = []

    return stats


def main():
    parser = argparse.ArgumentParser(description="Redact historical secrets from Claw Recall DB")
    parser.add_argument("--apply", action="store_true", help="Actually update the database (default: dry run)")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Database path")
    args = parser.parse_args()

    mode = "APPLYING CHANGES" if args.apply else "DRY RUN (use --apply to write)"
    print(f"\n{'='*60}")
    print(f"  Claw Recall — Historical Secret Redaction")
    print(f"  Mode: {mode}")
    print(f"  Database: {args.db}")
    print(f"{'='*60}\n")

    conn = sqlite3.connect(str(args.db))
    conn.execute("PRAGMA journal_mode=WAL")

    # Redact messages table
    print("Scanning messages table...")
    msg_stats = redact_table(conn, "messages", "id", "content", apply=args.apply)
    print(f"  Scanned: {msg_stats['scanned']:,}")
    print(f"  Redacted: {msg_stats['redacted']:,}")
    if msg_stats["sample_ids"]:
        print(f"  Sample IDs: {msg_stats['sample_ids'][:10]}")

    # Redact thoughts table
    print("\nScanning thoughts table...")
    try:
        thought_stats = redact_table(conn, "thoughts", "id", "content", apply=args.apply)
        print(f"  Scanned: {thought_stats['scanned']:,}")
        print(f"  Redacted: {thought_stats['redacted']:,}")
        if thought_stats["sample_ids"]:
            print(f"  Sample IDs: {thought_stats['sample_ids'][:10]}")
    except sqlite3.OperationalError as e:
        print(f"  Skipped (table may not exist): {e}")
        thought_stats = {"scanned": 0, "redacted": 0}

    total = msg_stats["redacted"] + thought_stats["redacted"]
    print(f"\n{'='*60}")
    print(f"  Total records with secrets: {total:,}")
    if not args.apply and total > 0:
        print(f"  Run with --apply to redact them.")
    elif args.apply and total > 0:
        print(f"  Successfully redacted {total:,} records.")
    else:
        print(f"  No secrets found — database is clean.")
    print(f"{'='*60}\n")

    conn.close()


if __name__ == "__main__":
    main()
