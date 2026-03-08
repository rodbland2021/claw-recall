#!/usr/bin/env python3
"""
One-time migration: scan all messages and thoughts in the database,
redact any secrets found, and report what was changed.

Usage:
    python3 scripts/redact_historical.py                    # Dry run (default)
    python3 scripts/redact_historical.py --apply            # Apply changes
    python3 scripts/redact_historical.py --apply --db /path/to/db
"""

import argparse
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

# Ensure repo root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from claw_recall.config import DB_PATH, redact_secrets, SECRET_PATTERNS


def count_secret_types(content: str) -> Counter:
    """Count how many of each secret type are found in content."""
    counts = Counter()
    for name, pattern in SECRET_PATTERNS.items():
        matches = re.findall(pattern, content, re.IGNORECASE)
        if matches:
            counts[name] = len(matches)
    return counts


def redact_table(conn: sqlite3.Connection, table: str, id_col: str, content_col: str,
                 apply: bool = False) -> dict:
    """Scan a table and redact secrets in the content column.

    Returns stats: {scanned, redacted, sample_ids, secret_types}.
    """
    cursor = conn.execute(f"SELECT {id_col}, {content_col} FROM {table}")
    stats = {"scanned": 0, "redacted": 0, "sample_ids": [], "secret_types": Counter()}
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
                # Count what types of secrets were found
                stats["secret_types"] += count_secret_types(content)
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

    all_secret_types = Counter()

    # Redact messages table
    print("Scanning messages table...")
    msg_stats = redact_table(conn, "messages", "id", "content", apply=args.apply)
    print(f"  Scanned: {msg_stats['scanned']:,}")
    print(f"  Records with secrets: {msg_stats['redacted']:,}")
    if msg_stats["sample_ids"]:
        print(f"  Sample IDs: {msg_stats['sample_ids'][:10]}")
    all_secret_types += msg_stats["secret_types"]

    # Redact thoughts table
    print("\nScanning thoughts table...")
    try:
        thought_stats = redact_table(conn, "thoughts", "id", "content", apply=args.apply)
        print(f"  Scanned: {thought_stats['scanned']:,}")
        print(f"  Records with secrets: {thought_stats['redacted']:,}")
        if thought_stats["sample_ids"]:
            print(f"  Sample IDs: {thought_stats['sample_ids'][:10]}")
        all_secret_types += thought_stats["secret_types"]
    except sqlite3.OperationalError as e:
        print(f"  Skipped (table may not exist): {e}")
        thought_stats = {"scanned": 0, "redacted": 0}

    total_records = msg_stats["redacted"] + thought_stats["redacted"]
    total_secrets = sum(all_secret_types.values())

    print(f"\n{'='*60}")
    print(f"  Summary")
    print(f"{'='*60}")
    print(f"  Records containing secrets: {total_records:,}")
    print(f"  Total secrets found: {total_secrets:,}")
    
    if all_secret_types:
        print(f"\n  Secrets by type:")
        for secret_type, count in all_secret_types.most_common():
            print(f"    - {secret_type}: {count:,}")

    print(f"\n{'='*60}")
    if not args.apply and total_records > 0:
        print(f"  Run with --apply to redact {total_secrets:,} secrets from {total_records:,} records.")
    elif args.apply and total_records > 0:
        print(f"  Successfully redacted {total_secrets:,} secrets from {total_records:,} records.")
    else:
        print(f"  No secrets found — database is clean.")
    print(f"{'='*60}\n")

    conn.close()


if __name__ == "__main__":
    main()
