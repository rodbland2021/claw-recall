#!/usr/bin/env python3
"""
Convo Memory — Search Interface
Search past conversations using keywords or semantic similarity.
"""

import gc
import sqlite3
import time as _time
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
# IMPORTANT: metadata stores only IDs (not content) to avoid multi-GB memory.
# Content is looked up from DB only for top-K results after scoring.
_CACHE_TTL_SECONDS = 14400  # 4 hours — clear cache after extended inactivity
_embedding_cache = {
    "matrix": None,       # np.ndarray of shape (N, dim)
    "msg_ids": None,      # np.ndarray of message IDs (for content lookup after scoring)
    "metadata": None,     # list of (message_id, session_id, agent_id, channel, role, timestamp) — NO content
    "norms": None,        # precomputed row norms
    "count": 0,           # row count when cache was built
    "filters_hash": None, # hash of filter params to invalidate on filter change
    "last_access": 0,     # monotonic timestamp of last use
}
_embedding_lock = threading.Lock()


def _clear_embedding_cache():
    """Release the embedding cache to free memory."""
    global _embedding_cache
    _embedding_cache = {
        "matrix": None, "msg_ids": None, "metadata": None, "norms": None,
        "count": 0, "filters_hash": None, "last_access": 0,
    }
    gc.collect()


def cache_status() -> dict:
    """Return observability info about the embedding cache."""
    with _embedding_lock:
        matrix = _embedding_cache["matrix"]
        if matrix is None:
            return {"loaded": False, "rows": 0, "memory_mb": 0, "ttl_seconds": _CACHE_TTL_SECONDS,
                    "idle_seconds": None, "filters_hash": None}
        rows = matrix.shape[0]
        mem_mb = round((matrix.nbytes + _embedding_cache["norms"].nbytes
                        + _embedding_cache["msg_ids"].nbytes) / 1024 / 1024, 1)
        last = _embedding_cache["last_access"]
        idle = round(_time.monotonic() - last, 1) if last > 0 else None
        return {
            "loaded": True,
            "rows": rows,
            "memory_mb": mem_mb,
            "ttl_seconds": _CACHE_TTL_SECONDS,
            "idle_seconds": idle,
            "filters_hash": _embedding_cache["filters_hash"],
        }


def _cache_janitor():
    """Background thread that clears the embedding cache after TTL expires."""
    while True:
        _time.sleep(60)
        with _embedding_lock:
            if (_embedding_cache["matrix"] is not None
                    and _embedding_cache["last_access"] > 0
                    and (_time.monotonic() - _embedding_cache["last_access"]) > _CACHE_TTL_SECONDS):
                _clear_embedding_cache()


# Start the janitor thread (daemon — dies with the process)
_janitor = threading.Thread(target=_cache_janitor, daemon=True, name="embedding-cache-janitor")
_janitor.start()


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
    message_id: int = None
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
            score=row[7],
            message_id=row[0],
        ))

    return results


def _build_embedding_cache(conn: sqlite3.Connection, agent=None, channel=None,
                            days=None, date_from=None, date_to=None):
    """Load embeddings into a numpy matrix for vectorized search.

    Caches the matrix so subsequent queries don't re-read from SQLite.
    Content is NOT stored — only message IDs and light metadata.
    Content is looked up from DB only for the top-K results after scoring.

    Memory budget: ~2.3GB for 376K embeddings (matrix + norms).
    Old version also stored content strings (~150MB) and had 3-copy peak (~4.8GB).
    New version: ~2.3GB steady, ~2.3GB peak (stream directly into pre-allocated array).
    """
    global _embedding_cache

    # Build filter hash to detect when we need to rebuild
    filter_key = f"{agent}|{channel}|{days}|{date_from}|{date_to}"

    # Check if current row count matches cache + TTL hasn't expired
    row_count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    if (_embedding_cache["matrix"] is not None
            and _embedding_cache["count"] == row_count
            and _embedding_cache["filters_hash"] == filter_key):
        _embedding_cache["last_access"] = _time.monotonic()
        return  # Cache is still valid

    # Count rows for this filter set to pre-allocate array
    count_sql = """
        SELECT COUNT(*)
        FROM embeddings e
        JOIN messages m ON e.message_id = m.id
        JOIN sessions s ON m.session_id = s.id
        WHERE 1=1
    """
    data_sql = """
        SELECT m.id, m.session_id, s.agent_id, s.channel, m.role, m.timestamp, e.embedding
        FROM embeddings e
        JOIN messages m ON e.message_id = m.id
        JOIN sessions s ON m.session_id = s.id
        WHERE 1=1
    """
    params = []
    if agent:
        count_sql += " AND s.agent_id = ?"
        data_sql += " AND s.agent_id = ?"
        params.append(agent)
    if channel:
        count_sql += " AND s.channel = ?"
        data_sql += " AND s.channel = ?"
        params.append(channel)
    if days:
        cutoff = datetime.now() - timedelta(days=days)
        count_sql += " AND m.timestamp >= ?"
        data_sql += " AND m.timestamp >= ?"
        params.append(cutoff)
    if date_from:
        count_sql += " AND m.timestamp >= ?"
        data_sql += " AND m.timestamp >= ?"
        params.append(date_from.isoformat())
    if date_to:
        count_sql += " AND m.timestamp <= ?"
        data_sql += " AND m.timestamp <= ?"
        params.append(date_to.isoformat())

    n_rows = conn.execute(count_sql, params).fetchone()[0]
    if n_rows == 0:
        _embedding_cache = {
            "matrix": np.empty((0, 0)), "msg_ids": np.empty(0, dtype=np.int64),
            "metadata": [], "norms": np.empty(0),
            "count": row_count, "filters_hash": filter_key,
            "last_access": _time.monotonic(),
        }
        return

    # Pre-allocate numpy array — avoids the 3-copy peak from fetchall + list + vstack
    EMB_DIM = 1536  # text-embedding-3-small
    matrix = np.empty((n_rows, EMB_DIM), dtype=np.float32)
    msg_ids = np.empty(n_rows, dtype=np.int64)
    metadata = []  # (message_id, session_id, agent_id, channel, role, timestamp) — NO content

    cursor = conn.execute(data_sql, params)
    i = 0
    for row in cursor:
        if i >= n_rows:
            break  # safety: cursor returned more rows than COUNT predicted
        # row: (m.id, m.session_id, s.agent_id, s.channel, m.role, m.timestamp, e.embedding)
        msg_ids[i] = row[0]
        metadata.append(row[:6])  # everything except embedding blob
        matrix[i] = np.frombuffer(row[6], dtype=np.float32)
        i += 1

    # Trim if cursor returned fewer rows than COUNT (shouldn't happen, but be safe)
    if i < n_rows:
        matrix = matrix[:i]
        msg_ids = msg_ids[:i]

    norms = np.linalg.norm(matrix, axis=1)

    # Release old cache before assigning new one
    old_matrix = _embedding_cache.get("matrix")
    _embedding_cache = {
        "matrix": matrix,
        "msg_ids": msg_ids,
        "metadata": metadata,
        "norms": norms,
        "count": row_count,
        "filters_hash": filter_key,
        "last_access": _time.monotonic(),
    }
    del old_matrix
    gc.collect()


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
    """Search using vectorized embedding similarity (numpy matrix multiply).

    Content is NOT stored in the embedding cache to save ~150MB+ of memory.
    Instead, we score all embeddings first, then look up content only for the
    top-K matches from the database.
    """

    if not OPENAI_AVAILABLE or openai_client is None:
        print("⚠️  Semantic search requires OpenAI. Falling back to keyword search.")
        return keyword_search(conn, query, agent, channel, days, limit, date_from=date_from, date_to=date_to)

    # Generate query embedding (outside lock — network I/O)
    response = openai_client.embeddings.create(model=EMBEDDING_MODEL, input=[query])
    q_emb = np.array(response.data[0].embedding, dtype=np.float32)
    q_norm = np.linalg.norm(q_emb)

    with _embedding_lock:
        # Check TTL — clear stale cache to free memory
        if (_embedding_cache["matrix"] is not None
                and _embedding_cache["last_access"] > 0
                and (_time.monotonic() - _embedding_cache["last_access"]) > _CACHE_TTL_SECONDS):
            _clear_embedding_cache()

        # Build/refresh the embedding matrix cache
        _build_embedding_cache(conn, agent, channel, days, date_from, date_to)

        if _embedding_cache["matrix"] is None or len(_embedding_cache["metadata"]) == 0:
            return []

        # Vectorized cosine similarity: dot(matrix, query) / (norms * q_norm)
        matrix = _embedding_cache["matrix"]
        norms = _embedding_cache["norms"]
        metadata = _embedding_cache["metadata"]
        msg_ids = _embedding_cache["msg_ids"]

    similarities = matrix @ q_emb / (norms * q_norm + 1e-10)

    # Get top candidates (more than limit for dedup headroom)
    top_k = min(limit * 3, len(similarities))
    top_indices = np.argpartition(similarities, -top_k)[-top_k:]
    top_indices = top_indices[np.argsort(similarities[top_indices])[::-1]]

    # Collect candidate message IDs and their metadata
    MIN_SIMILARITY = 0.45
    candidates = []
    for idx in top_indices:
        sim = float(similarities[idx])
        if sim < MIN_SIMILARITY:
            break
        meta = metadata[idx]
        # meta: (message_id, session_id, agent_id, channel, role, timestamp)
        candidates.append((int(msg_ids[idx]), meta, sim))
        if len(candidates) >= limit * 3:
            break

    if not candidates:
        return []

    # Batch-fetch content for top candidates from DB (not cached in memory)
    candidate_ids = [c[0] for c in candidates]
    placeholders = ",".join(["?"] * len(candidate_ids))
    content_rows = conn.execute(
        f"SELECT id, content FROM messages WHERE id IN ({placeholders})",
        candidate_ids
    ).fetchall()
    content_map = {row[0]: row[1] for row in content_rows}

    # Build results with dedup
    seen = set()
    results = []
    for msg_id, meta, sim in candidates:
        content = content_map.get(msg_id, "")
        role = meta[4]
        fingerprint = f"{role}:{content[:200]}"
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        results.append(SearchResult(
            session_id=meta[1],
            agent_id=meta[2],
            channel=meta[3],
            role=role,
            content=content,
            timestamp=datetime.fromisoformat(meta[5]) if meta[5] else None,
            score=sim,
            message_id=msg_id,
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


def preload_embedding_cache():
    """Preload the embedding cache in a background thread.

    Call this on service start to eliminate cold-start latency on first
    semantic search. Loads the full matrix (~2.3GB) without any filters.
    """
    def _preload():
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute("PRAGMA journal_mode=WAL")
            with _embedding_lock:
                _build_embedding_cache(conn)
            conn.close()
            rows = _embedding_cache.get("matrix")
            if rows is not None:
                print(f"[search] Embedding cache preloaded: {rows.shape[0]} embeddings")
            else:
                print("[search] Embedding cache preload: no embeddings found")
        except Exception as e:
            print(f"[search] Embedding cache preload failed: {e}")

    t = threading.Thread(target=_preload, daemon=True, name="embedding-preload")
    t.start()


if __name__ == "__main__":
    # Use claw-recall CLI instead — this module is a library
    print("Use the claw-recall CLI for searching. This module is imported as a library.")
    raise SystemExit(1)
