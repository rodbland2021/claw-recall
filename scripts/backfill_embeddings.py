#!/usr/bin/env python3
"""
Claw Recall — Embedding Backfill (cron-safe)

Generates embeddings for messages that don't have them yet.
Processes in small batches to avoid memory issues and API rate limits.

Usage:
    python3 scripts/backfill_embeddings.py              # Default: 500 messages per run
    python3 scripts/backfill_embeddings.py --limit 2000  # Custom batch size
    python3 scripts/backfill_embeddings.py --quiet       # Suppress output (for cron)
"""

import sys
import os
import sqlite3
import argparse
import numpy as np
from pathlib import Path

# Allow imports from project root when run from scripts/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.path.insert(0, str(Path(__file__).parent))
from index import EMBEDDING_MODEL, EMBEDDING_BATCH_SIZE, MIN_CONTENT_LENGTH

DB_PATH = Path(__file__).parent / "convo_memory.db"
if not DB_PATH.exists():
    DB_PATH = Path.home() / "shared" / "convo-memory" / "convo_memory.db"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=500, help='Max messages to embed per run')
    parser.add_argument('--quiet', action='store_true', help='Suppress output')
    args = parser.parse_args()

    try:
        from openai import OpenAI
    except ImportError:
        print("OpenAI not installed")
        return

    client = OpenAI()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    # Count gap
    gap = conn.execute("""
        SELECT COUNT(*) FROM messages m
        LEFT JOIN embeddings e ON e.message_id = m.id
        WHERE e.id IS NULL AND LENGTH(m.content) >= ?
    """, (MIN_CONTENT_LENGTH,)).fetchone()[0]

    if gap == 0:
        if not args.quiet:
            print("All eligible messages have embeddings")
        conn.close()
        return

    # Fetch batch (oldest first to catch up chronologically)
    rows = conn.execute("""
        SELECT m.id, m.content FROM messages m
        LEFT JOIN embeddings e ON e.message_id = m.id
        WHERE e.id IS NULL AND LENGTH(m.content) >= ?
        ORDER BY m.id ASC
        LIMIT ?
    """, (MIN_CONTENT_LENGTH, args.limit)).fetchall()

    if not args.quiet:
        print(f"Embedding {len(rows)} / {gap} remaining messages...")

    embedded = 0
    for i in range(0, len(rows), EMBEDDING_BATCH_SIZE):
        batch = rows[i:i + EMBEDDING_BATCH_SIZE]
        texts = [content[:2000] for _, content in batch]

        try:
            response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
            for (mid, _), item in zip(batch, response.data):
                emb = np.array(item.embedding, dtype=np.float32)
                conn.execute(
                    "INSERT INTO embeddings (message_id, embedding, model) VALUES (?, ?, ?)",
                    (mid, emb.tobytes(), EMBEDDING_MODEL)
                )
                embedded += 1
        except Exception as e:
            if not args.quiet:
                print(f"Batch error: {e}")
            break

        if (i + EMBEDDING_BATCH_SIZE) % 200 == 0:
            conn.commit()

    conn.commit()
    conn.close()

    if not args.quiet:
        print(f"Done: {embedded} embedded, {gap - embedded} remaining")


if __name__ == "__main__":
    main()
