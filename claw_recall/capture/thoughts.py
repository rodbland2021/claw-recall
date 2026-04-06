#!/usr/bin/env python3
"""
Claw Recall — Thought Capture Module

Shared write path for capturing thoughts from CLI, HTTP, MCP, and Telegram.
Stores thoughts with optional embeddings for semantic search.
"""

import json
import hashlib
import sqlite3
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Optional

from claw_recall.database import get_db
from claw_recall.config import DB_PATH, EMBEDDING_MODEL, MIN_CONTENT_LENGTH, redact_secrets

# Optional: OpenAI for embeddings
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# Module-level client -- reused across calls to avoid per-call instantiation
_openai_client = None

def _get_openai_client() -> Optional['OpenAI']:
    """Get or create a reusable OpenAI client."""
    global _openai_client
    if not OPENAI_AVAILABLE:
        return None
    import os
    if not os.environ.get('OPENAI_API_KEY'):
        return None
    if _openai_client is None:
        _openai_client = OpenAI()
    return _openai_client


def _generate_embedding(text: str, client: Optional['OpenAI'] = None) -> Optional[np.ndarray]:
    """Generate a single embedding for the given text."""
    if not OPENAI_AVAILABLE or client is None:
        return None
    try:
        response = client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=[text[:2000]]  # Truncate to ~1500 tokens
        )
        return np.array(response.data[0].embedding, dtype=np.float32)
    except Exception as e:
        print(f"Embedding error: {e}")
        return None


def capture_thought(
    content: str,
    source: str = 'cli',
    agent: str = None,
    metadata: dict = None,
    generate_embedding: bool = True,
    conn: sqlite3.Connection = None,
) -> dict:
    """
    Capture a thought into Claw Recall.

    Args:
        content: The thought text to capture
        source: Origin -- 'cli', 'http', 'mcp', 'telegram'
        agent: Which agent captured it (e.g., 'main', 'cyrus')
        metadata: Optional JSON-serializable dict of tags/topics
        generate_embedding: Whether to generate an embedding (requires OpenAI)
        conn: Optional existing DB connection (creates one if not provided)

    Returns:
        dict with {id, content, source, agent, created_at} on success,
        or {error: str} on failure
    """
    content = content.strip()
    if not content:
        return {"error": "Empty content"}

    # Redact secrets before storing
    content = redact_secrets(content)

    metadata_json = json.dumps(metadata or {})

    def _do_capture(c):
        # Dedup: check if identical content was captured recently (last 24h)
        existing = c.execute(
            """SELECT id FROM thoughts
               WHERE content = ? AND created_at >= datetime('now', '-1 day')
               LIMIT 1""",
            (content,)
        ).fetchone()
        if existing:
            return {"id": existing[0], "content": content, "source": source,
                    "agent": agent, "duplicate": True, "created_at": datetime.now().isoformat()}

        cursor = c.execute(
            "INSERT INTO thoughts (content, source, agent, metadata) VALUES (?, ?, ?, ?)",
            (content, source, agent, metadata_json)
        )
        thought_id = cursor.lastrowid

        # Generate and store embedding
        embed_stored = False
        if generate_embedding and len(content) >= MIN_CONTENT_LENGTH:
            openai_client = _get_openai_client()
            embedding = _generate_embedding(content, openai_client)
            if embedding is not None:
                c.execute(
                    "INSERT INTO thought_embeddings (thought_id, embedding, model) VALUES (?, ?, ?)",
                    (thought_id, embedding.tobytes(), EMBEDDING_MODEL)
                )
                embed_stored = True

        c.commit()

        return {
            "id": thought_id,
            "content": content,
            "source": source,
            "agent": agent,
            "metadata": metadata or {},
            "embedded": embed_stored,
            "created_at": datetime.now().isoformat(),
        }

    try:
        if conn is not None:
            return _do_capture(conn)
        with get_db() as c:
            return _do_capture(c)
    except Exception as e:
        return {"error": str(e)}


def list_thoughts(
    limit: int = 20,
    offset: int = 0,
    source: str = None,
    agent: str = None,
    conn: sqlite3.Connection = None,
) -> list[dict]:
    """List thoughts in reverse chronological order."""
    def _do_list(c):
        sql = "SELECT id, content, source, agent, metadata, created_at FROM thoughts WHERE 1=1"
        params = []
        if source:
            sql += " AND source = ?"
            params.append(source)
        if agent:
            sql += " AND agent = ?"
            params.append(agent)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = c.execute(sql, params).fetchall()
        return [
            {
                "id": r[0],
                "content": r[1],
                "source": r[2],
                "agent": r[3],
                "metadata": json.loads(r[4]) if r[4] else {},
                "created_at": r[5],
            }
            for r in rows
        ]

    try:
        if conn is not None:
            return _do_list(conn)
        with get_db() as c:
            return _do_list(c)
    except Exception as e:
        return [{"error": str(e)}]


def delete_thought(thought_id: int, conn: sqlite3.Connection = None) -> dict:
    """Delete a thought by ID."""
    def _do_delete(c):
        c.execute("DELETE FROM thought_embeddings WHERE thought_id = ?", (thought_id,))
        cursor = c.execute("DELETE FROM thoughts WHERE id = ?", (thought_id,))
        c.commit()
        if cursor.rowcount == 0:
            return {"error": f"Thought {thought_id} not found"}
        return {"deleted": thought_id}

    try:
        if conn is not None:
            return _do_delete(conn)
        with get_db() as c:
            return _do_delete(c)
    except Exception as e:
        return {"error": str(e)}


def batch_embed_thoughts(thought_ids: list[int] = None, conn: sqlite3.Connection = None) -> dict:
    """Batch-generate embeddings for thoughts that don't have them yet.

    Uses OpenAI batch embedding API (up to 2048 inputs per call).
    Much faster than per-thought embedding for bulk captures.
    """
    client = _get_openai_client()
    if client is None:
        return {"error": "OpenAI not available"}

    def _do_batch(c):
        if thought_ids:
            placeholders = ','.join('?' * len(thought_ids))
            rows = c.execute(
                f"""SELECT t.id, t.content FROM thoughts t
                    LEFT JOIN thought_embeddings te ON te.thought_id = t.id
                    WHERE t.id IN ({placeholders}) AND te.id IS NULL
                      AND LENGTH(t.content) >= ?""",
                thought_ids + [MIN_CONTENT_LENGTH]
            ).fetchall()
        else:
            rows = c.execute(
                """SELECT t.id, t.content FROM thoughts t
                   LEFT JOIN thought_embeddings te ON te.thought_id = t.id
                   WHERE te.id IS NULL AND LENGTH(t.content) >= ?""",
                (MIN_CONTENT_LENGTH,)
            ).fetchall()

        if not rows:
            return {"embedded": 0, "total": 0}

        BATCH_SIZE = 2048
        total_embedded = 0

        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            texts = [r[1][:2000] for r in batch]
            ids = [r[0] for r in batch]

            try:
                response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
                for j, emb_data in enumerate(response.data):
                    embedding = np.array(emb_data.embedding, dtype=np.float32)
                    c.execute(
                        "INSERT INTO thought_embeddings (thought_id, embedding, model) VALUES (?, ?, ?)",
                        (ids[j], embedding.tobytes(), EMBEDDING_MODEL)
                    )
                total_embedded += len(batch)
            except Exception as e:
                print(f"Batch embedding error: {e}")

        c.commit()
        return {"embedded": total_embedded, "total": len(rows)}

    try:
        if conn is not None:
            return _do_batch(conn)
        with get_db() as c:
            return _do_batch(c)
    except Exception as e:
        return {"error": str(e)}


def thought_stats(conn: sqlite3.Connection = None) -> dict:
    """Get statistics about captured thoughts."""
    def _do_stats(c):
        total = c.execute("SELECT COUNT(*) FROM thoughts").fetchone()[0]
        embedded = c.execute("SELECT COUNT(*) FROM thought_embeddings").fetchone()[0]
        by_source = dict(c.execute(
            "SELECT source, COUNT(*) FROM thoughts GROUP BY source"
        ).fetchall())
        by_agent = dict(c.execute(
            "SELECT COALESCE(agent, 'none'), COUNT(*) FROM thoughts GROUP BY agent"
        ).fetchall())
        return {"total": total, "embedded": embedded, "by_source": by_source, "by_agent": by_agent}

    try:
        if conn is not None:
            return _do_stats(conn)
        with get_db() as c:
            return _do_stats(c)
    except Exception as e:
        return {"error": str(e)}
