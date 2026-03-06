#!/usr/bin/env python3
"""
Claw Recall — Database Connection Management

Shared context manager for SQLite connections with WAL mode and busy timeout.
All modules should use get_db() instead of raw sqlite3.connect().
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("CLAW_RECALL_DB", str(Path(__file__).parent / "convo_memory.db")))

# Embedding configuration — shared across all modules
EMBEDDING_MODEL = os.environ.get("CLAW_RECALL_EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_BATCH_SIZE = int(os.environ.get("CLAW_RECALL_EMBEDDING_BATCH", "20"))
EMBEDDING_DIM = int(os.environ.get("CLAW_RECALL_EMBEDDING_DIM", "1536"))
MIN_CONTENT_LENGTH = int(os.environ.get("CLAW_RECALL_MIN_CONTENT_LENGTH", "20"))


@contextmanager
def get_db(db_path=None, busy_timeout=30000):
    """Open a WAL-mode SQLite connection with guaranteed cleanup.

    Usage:
        with get_db() as conn:
            conn.execute("SELECT ...")
    """
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout}")
    try:
        yield conn
    finally:
        conn.close()
