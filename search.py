#!/usr/bin/env python3
"""
Convo Memory — Search Interface
Search past conversations using keywords or semantic similarity.
"""

import sqlite3
import threading
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List
from dataclasses import dataclass

# Optional: OpenAI for semantic search
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

DB_PATH = Path(__file__).parent / "convo_memory.db"
EMBEDDING_MODEL = "text-embedding-3-small"

# Cached embedding matrix for vectorized semantic search
_embedding_cache = {
    "matrix": None,       # np.ndarray of shape (N, dim)
    "metadata": None,     # list of (message_id, session_id, agent_id, channel, role, content, timestamp)
    "norms": None,        # precomputed row norms
    "count": 0,           # row count when cache was built
    "filters_hash": None, # hash of filter params to invalidate on filter change
}
_embedding_lock = threading.Lock()


@dataclass
class SearchResult:
    """A search result from the conversation database."""
    session_id: str
    agent_id: str
    channel: str
    role: str
    content: str
    timestamp: Optional[datetime]
    score: float
    context_before: List[str] = None
    context_after: List[str] = None


def keyword_search(
    conn: sqlite3.Connection,
    query: str,
    agent: Optional[str] = None,
    channel: Optional[str] = None,
    days: Optional[int] = None,
    limit: int = 20,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None
) -> List[SearchResult]:
    """Search using FTS5 full-text search."""

    # Build FTS query — AND all words so results must contain every term
    # Strip quotes to prevent FTS5 syntax errors from user input
    words = [w.replace('"', '') for w in query.split() if w.replace('"', '')]
    if not words:
        return []
    fts_query = " AND ".join(f'"{word}"' for word in words)

    sql = """
        SELECT
            m.id,
            m.session_id,
            m.role,
            m.content,
            m.timestamp,
            s.agent_id,
            s.channel,
            bm25(messages_fts) as score
        FROM messages_fts fts
        JOIN messages m ON fts.rowid = m.id
        JOIN sessions s ON m.session_id = s.id
        WHERE messages_fts MATCH ?
    """
    params = [fts_query]

    if agent:
        sql += " AND s.agent_id = ?"
        params.append(agent)

    if channel:
        sql += " AND s.channel = ?"
        params.append(channel)

    if days:
        cutoff = datetime.now() - timedelta(days=days)
        sql += " AND m.timestamp >= ?"
        params.append(cutoff)

    if date_from:
        sql += " AND m.timestamp >= ?"
        params.append(date_from.isoformat())

    if date_to:
        sql += " AND m.timestamp <= ?"
        params.append(date_to.isoformat())

    sql += " ORDER BY bm25(messages_fts) ASC LIMIT ?"  # bm25 is negative; lower = better match
    params.append(limit)
    
    cursor = conn.execute(sql, params)
    
    results = []
    for row in cursor.fetchall():
        results.append(SearchResult(
            session_id=row[1],
            agent_id=row[5],
            channel=row[6],
            role=row[2],
            content=row[3],
            timestamp=datetime.fromisoformat(row[4]) if row[4] else None,
            score=row[7]
        ))
    
    return results


def _build_embedding_cache(conn: sqlite3.Connection, agent=None, channel=None,
                            days=None, date_from=None, date_to=None):
    """Load embeddings into a numpy matrix for vectorized search.

    Caches the matrix so subsequent queries don't re-read from SQLite.
    """
    global _embedding_cache

    # Build filter hash to detect when we need to rebuild
    filter_key = f"{agent}|{channel}|{days}|{date_from}|{date_to}"

    # Check if current row count matches cache
    row_count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    if (_embedding_cache["matrix"] is not None
            and _embedding_cache["count"] == row_count
            and _embedding_cache["filters_hash"] == filter_key):
        return  # Cache is still valid

    sql = """
        SELECT m.id, m.session_id, s.agent_id, s.channel, m.role, m.content, m.timestamp, e.embedding
        FROM embeddings e
        JOIN messages m ON e.message_id = m.id
        JOIN sessions s ON m.session_id = s.id
        WHERE 1=1
    """
    params = []
    if agent:
        sql += " AND s.agent_id = ?"
        params.append(agent)
    if channel:
        sql += " AND s.channel = ?"
        params.append(channel)
    if days:
        cutoff = datetime.now() - timedelta(days=days)
        sql += " AND m.timestamp >= ?"
        params.append(cutoff)
    if date_from:
        sql += " AND m.timestamp >= ?"
        params.append(date_from.isoformat())
    if date_to:
        sql += " AND m.timestamp <= ?"
        params.append(date_to.isoformat())

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        _embedding_cache = {"matrix": np.empty((0, 0)), "metadata": [], "norms": np.empty(0),
                            "count": row_count, "filters_hash": filter_key}
        return

    metadata = []
    embeddings_list = []
    for row in rows:
        metadata.append(row[:7])  # (id, session_id, agent_id, channel, role, content, timestamp)
        embeddings_list.append(np.frombuffer(row[7], dtype=np.float32))

    matrix = np.vstack(embeddings_list)  # shape: (N, dim)
    norms = np.linalg.norm(matrix, axis=1)  # precompute for cosine similarity

    _embedding_cache = {
        "matrix": matrix,
        "metadata": metadata,
        "norms": norms,
        "count": row_count,
        "filters_hash": filter_key,
    }


def semantic_search(
    conn: sqlite3.Connection,
    query: str,
    agent: Optional[str] = None,
    channel: Optional[str] = None,
    days: Optional[int] = None,
    limit: int = 20,
    openai_client: Optional['OpenAI'] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None
) -> List[SearchResult]:
    """Search using vectorized embedding similarity (numpy matrix multiply)."""

    if not OPENAI_AVAILABLE or openai_client is None:
        print("⚠️  Semantic search requires OpenAI. Falling back to keyword search.")
        return keyword_search(conn, query, agent, channel, days, limit, date_from=date_from, date_to=date_to)

    # Generate query embedding (outside lock — network I/O)
    response = openai_client.embeddings.create(model=EMBEDDING_MODEL, input=[query])
    q_emb = np.array(response.data[0].embedding, dtype=np.float32)
    q_norm = np.linalg.norm(q_emb)

    with _embedding_lock:
        # Build/refresh the embedding matrix cache
        _build_embedding_cache(conn, agent, channel, days, date_from, date_to)

        if _embedding_cache["matrix"] is None or len(_embedding_cache["metadata"]) == 0:
            return []

        # Vectorized cosine similarity: dot(matrix, query) / (norms * q_norm)
        matrix = _embedding_cache["matrix"]
        norms = _embedding_cache["norms"]
        metadata = _embedding_cache["metadata"]

    similarities = matrix @ q_emb / (norms * q_norm + 1e-10)

    # Get top candidates (more than limit for dedup headroom)
    top_k = min(limit * 3, len(similarities))
    top_indices = np.argpartition(similarities, -top_k)[-top_k:]
    top_indices = top_indices[np.argsort(similarities[top_indices])[::-1]]

    # Build results with dedup
    MIN_SIMILARITY = 0.45
    seen = set()
    results = []
    for idx in top_indices:
        sim = float(similarities[idx])
        if sim < MIN_SIMILARITY:
            break
        meta = metadata[idx]
        # meta: (id, session_id, agent_id, channel, role, content, timestamp)
        fingerprint = f"{meta[4]}:{meta[5][:200]}"
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        results.append(SearchResult(
            session_id=meta[1],
            agent_id=meta[2],
            channel=meta[3],
            role=meta[4],
            content=meta[5],
            timestamp=datetime.fromisoformat(meta[6]) if meta[6] else None,
            score=sim,
        ))
        if len(results) >= limit:
            break

    return results


def get_context(
    conn: sqlite3.Connection,
    session_id: str,
    message_index: int,
    context_size: int = 2
) -> tuple[List[str], List[str]]:
    """Get surrounding messages for context."""
    
    cursor = conn.execute("""
        SELECT role, content FROM messages 
        WHERE session_id = ? AND message_index < ?
        ORDER BY message_index DESC LIMIT ?
    """, (session_id, message_index, context_size))
    before = [f"[{r[0]}] {r[1][:200]}" for r in reversed(cursor.fetchall())]
    
    cursor = conn.execute("""
        SELECT role, content FROM messages 
        WHERE session_id = ? AND message_index > ?
        ORDER BY message_index ASC LIMIT ?
    """, (session_id, message_index, context_size))
    after = [f"[{r[0]}] {r[1][:200]}" for r in cursor.fetchall()]
    
    return before, after


def deduplicate_results(results: List[SearchResult]) -> List[SearchResult]:
    """Remove duplicate messages that appear in multiple session snapshots."""
    seen_content = set()
    unique = []
    for r in results:
        fingerprint = f"{r.role}:{r.content[:500]}"
        if fingerprint not in seen_content:
            seen_content.add(fingerprint)
            unique.append(r)
    return unique


# Python API for agents
def search_conversations(
    query: str,
    agent: Optional[str] = None,
    channel: Optional[str] = None,
    days: Optional[int] = None,
    semantic: bool = False,
    limit: int = 10,
    db_path: Path = DB_PATH,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None
) -> List[SearchResult]:
    """
    Search conversation history.

    Args:
        query: Search query
        agent: Filter by agent (kit, cyrus, etc.)
        channel: Filter by channel (telegram, discord, etc.)
        days: Only search last N days
        semantic: Use semantic search instead of keyword
        limit: Maximum results to return
        db_path: Path to database
        date_from: Only search from this datetime onwards
        date_to: Only search up to this datetime

    Returns:
        List of SearchResult objects
    """
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(db_path)

    openai_client = None
    if semantic and OPENAI_AVAILABLE:
        openai_client = OpenAI()

    if semantic:
        results = semantic_search(conn, query, agent, channel, days, limit, openai_client, date_from=date_from, date_to=date_to)
    else:
        results = keyword_search(conn, query, agent, channel, days, limit, date_from=date_from, date_to=date_to)

    conn.close()
    return results


if __name__ == "__main__":
    # Use claw-recall CLI instead — this module is a library
    print("Use the claw-recall CLI for searching. This module is imported as a library.")
    raise SystemExit(1)
