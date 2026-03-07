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

from claw_recall.database import get_db
from claw_recall.config import DB_PATH, EMBEDDING_MODEL, EMBEDDING_DIM, AGENT_NAME_MAP

# Optional: OpenAI for semantic search
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# Agent name mapping — use centralized config (single source of truth)
_AGENT_ALIASES = AGENT_NAME_MAP

def resolve_agent(agent: Optional[str]) -> Optional[str]:
    """Resolve an agent name/alias to the display name stored in the DB."""
    if not agent:
        return agent
    return _AGENT_ALIASES.get(agent.lower(), agent)

# Backward compatibility alias
_resolve_agent = resolve_agent

# Common English stop words to drop from FTS keyword queries
_STOP_WORDS = frozenset({
    'a', 'an', 'the', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'shall', 'can', 'need', 'dare', 'ought',
    'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from', 'as',
    'into', 'about', 'between', 'through', 'during', 'before', 'after',
    'above', 'below', 'up', 'down', 'out', 'off', 'over', 'under',
    'and', 'but', 'or', 'nor', 'not', 'so', 'yet', 'both', 'either',
    'neither', 'each', 'every', 'all', 'any', 'few', 'more', 'most',
    'other', 'some', 'such', 'no', 'only', 'own', 'same', 'than',
    'too', 'very', 'just', 'because', 'if', 'when', 'where', 'while',
    'that', 'this', 'these', 'those', 'i', 'me', 'my', 'we', 'our',
    'you', 'your', 'he', 'him', 'his', 'she', 'her', 'it', 'its',
    'they', 'them', 'their', 'what', 'which', 'who', 'whom',
})

# Cached embedding matrix for vectorized semantic search
# IMPORTANT: metadata stores only IDs (not content) to avoid multi-GB memory.
# Content is looked up from DB only for top-K results after scoring.
_CACHE_TTL_SECONDS = 14400  # 4 hours -- clear cache after extended inactivity
_embedding_cache = {
    "matrix": None,       # np.ndarray of shape (N, dim)
    "msg_ids": None,      # np.ndarray of message IDs (for content lookup after scoring)
    "metadata": None,     # list of (message_id, session_id, agent_id, channel, role, timestamp) -- NO content
    "norms": None,        # precomputed row norms
    "count": 0,           # row count when cache was built
    "filters_hash": None, # hash of filter params to invalidate on filter change
    "last_access": 0,     # monotonic timestamp of last use
}
_embedding_lock = threading.Lock()
_preload_in_progress = False


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


# Start the janitor thread (daemon -- dies with the process)
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
    agent = resolve_agent(agent)

    # Build FTS query -- AND all content words (drop stop words for better recall)
    words = [w.replace('"', '') for w in query.split() if w.replace('"', '')]
    words = [w for w in words if w.lower() not in _STOP_WORDS]
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
        sql += " AND s.agent_id = ? COLLATE NOCASE"
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
    Content is NOT stored -- only message IDs and light metadata.
    Content is looked up from DB only for the top-K results after scoring.

    Memory budget: ~2.3GB for 376K embeddings (matrix + norms).
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
        count_sql += " AND s.agent_id = ? COLLATE NOCASE"
        data_sql += " AND s.agent_id = ? COLLATE NOCASE"
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

    # Pre-allocate numpy array -- avoids the 3-copy peak from fetchall + list + vstack
    matrix = np.empty((n_rows, EMBEDDING_DIM), dtype=np.float32)
    msg_ids = np.empty(n_rows, dtype=np.int64)
    metadata = []  # (message_id, session_id, agent_id, channel, role, timestamp) -- NO content

    cursor = conn.execute(data_sql, params)
    i = 0
    for row in cursor:
        if i >= n_rows:
            break  # safety: cursor returned more rows than COUNT predicted
        # row: (m.id, m.session_id, s.agent_id, s.channel, m.role, m.timestamp, e.embedding)
        try:
            matrix[i] = np.frombuffer(row[6], dtype=np.float32)
        except Exception as e:
            print(f"[search] Skipping corrupted embedding for msg {row[0]}: {e}")
            continue
        msg_ids[i] = row[0]
        metadata.append(row[:6])  # everything except embedding blob
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
    agent = resolve_agent(agent)

    if not OPENAI_AVAILABLE or openai_client is None:
        print("Semantic search requires OpenAI. Falling back to keyword search.")
        return keyword_search(conn, query, agent, channel, days, limit, date_from=date_from, date_to=date_to)

    # If embedding cache is still loading from startup, fall back gracefully
    if _preload_in_progress and _embedding_cache["matrix"] is None:
        print("Embedding cache loading -- using keyword search. Retry shortly for semantic.")
        return keyword_search(conn, query, agent, channel, days, limit, date_from=date_from, date_to=date_to)

    # Generate query embedding (outside lock -- network I/O)
    response = openai_client.embeddings.create(model=EMBEDDING_MODEL, input=[query])
    q_emb = np.array(response.data[0].embedding, dtype=np.float32)
    q_norm = np.linalg.norm(q_emb)

    with _embedding_lock:
        # Check TTL -- clear stale cache to free memory
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


@dataclass
class ThoughtResult:
    """A search result from the thoughts table."""
    thought_id: int
    content: str
    source: str
    agent: Optional[str]
    metadata: dict
    created_at: Optional[datetime]
    score: float


def keyword_search_thoughts(
    conn: sqlite3.Connection,
    query: str,
    agent: Optional[str] = None,
    source: Optional[str] = None,
    days: Optional[int] = None,
    limit: int = 10,
) -> List[ThoughtResult]:
    """Search thoughts using FTS5 full-text search."""
    import json as _json
    words = [w.replace('"', '') for w in query.split() if w.replace('"', '')]
    words = [w for w in words if w.lower() not in _STOP_WORDS]
    if not words:
        return []
    fts_query = " AND ".join(f'"{word}"' for word in words)

    sql = """
        SELECT t.id, t.content, t.source, t.agent, t.metadata, t.created_at,
               bm25(thoughts_fts) as score
        FROM thoughts_fts fts
        JOIN thoughts t ON fts.rowid = t.id
        WHERE thoughts_fts MATCH ?
    """
    params = [fts_query]
    if agent:
        sql += " AND t.agent = ? COLLATE NOCASE"
        params.append(agent)
    if source:
        sql += " AND t.source = ?"
        params.append(source)
    if days:
        cutoff = datetime.now() - timedelta(days=days)
        sql += " AND t.created_at >= ?"
        params.append(cutoff.isoformat())
    sql += " ORDER BY bm25(thoughts_fts) ASC LIMIT ?"
    params.append(limit)

    results = []
    for row in conn.execute(sql, params).fetchall():
        try:
            meta = _json.loads(row[4]) if row[4] else {}
        except Exception:
            meta = {}
        results.append(ThoughtResult(
            thought_id=row[0], content=row[1], source=row[2], agent=row[3],
            metadata=meta,
            created_at=datetime.fromisoformat(row[5]) if row[5] else None,
            score=row[6],
        ))
    return results


def semantic_search_thoughts(
    conn: sqlite3.Connection,
    query: str,
    agent: Optional[str] = None,
    source: Optional[str] = None,
    days: Optional[int] = None,
    limit: int = 10,
    openai_client: Optional['OpenAI'] = None,
) -> List[ThoughtResult]:
    """Search thoughts using embedding similarity. Small dataset -- no cache needed."""
    import json as _json
    if not OPENAI_AVAILABLE or openai_client is None:
        return keyword_search_thoughts(conn, query, agent, source, days, limit)

    # Generate query embedding
    response = openai_client.embeddings.create(model=EMBEDDING_MODEL, input=[query])
    q_emb = np.array(response.data[0].embedding, dtype=np.float32)
    q_norm = np.linalg.norm(q_emb)

    # Load all thought embeddings (small dataset, no caching needed)
    sql = """
        SELECT t.id, t.content, t.source, t.agent, t.metadata, t.created_at, te.embedding
        FROM thought_embeddings te
        JOIN thoughts t ON te.thought_id = t.id
        WHERE 1=1
    """
    params = []
    if agent:
        sql += " AND t.agent = ? COLLATE NOCASE"
        params.append(agent)
    if source:
        sql += " AND t.source = ?"
        params.append(source)
    if days:
        cutoff = datetime.now() - timedelta(days=days)
        sql += " AND t.created_at >= ?"
        params.append(cutoff.isoformat())

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return []

    # Compute cosine similarity
    candidates = []
    for row in rows:
        try:
            emb = np.frombuffer(row[6], dtype=np.float32)
            sim = float(np.dot(emb, q_emb) / (np.linalg.norm(emb) * q_norm + 1e-10))
            if sim >= 0.45:
                try:
                    meta = _json.loads(row[4]) if row[4] else {}
                except Exception:
                    meta = {}
                candidates.append(ThoughtResult(
                    thought_id=row[0], content=row[1], source=row[2], agent=row[3],
                    metadata=meta,
                    created_at=datetime.fromisoformat(row[5]) if row[5] else None,
                    score=sim,
                ))
        except Exception:
            continue

    candidates.sort(key=lambda x: x.score, reverse=True)
    return candidates[:limit]


def search_thoughts(
    query: str,
    agent: Optional[str] = None,
    source: Optional[str] = None,
    days: Optional[int] = None,
    semantic: bool = False,
    limit: int = 10,
    db_path: Path = DB_PATH,
) -> List[ThoughtResult]:
    """Search captured thoughts. High-level API."""
    if not db_path.exists():
        return []

    with get_db(db_path) as conn:
        openai_client = None
        if semantic and OPENAI_AVAILABLE:
            openai_client = OpenAI()

        if semantic:
            return semantic_search_thoughts(conn, query, agent, source, days, limit, openai_client)
        else:
            return keyword_search_thoughts(conn, query, agent, source, days, limit)


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

    with get_db(db_path) as conn:
        openai_client = None
        if semantic and OPENAI_AVAILABLE:
            openai_client = OpenAI()

        if semantic:
            return semantic_search(conn, query, agent, channel, days, limit, openai_client, date_from=date_from, date_to=date_to)
        else:
            return keyword_search(conn, query, agent, channel, days, limit, date_from=date_from, date_to=date_to)


def preload_embedding_cache():
    """Preload the embedding cache in a background thread.

    Call this on service start to eliminate cold-start latency on first
    semantic search. Loads the full matrix (~2.3GB) without any filters.
    While preloading, semantic searches fall back to keyword search.
    """
    global _preload_in_progress

    def _preload():
        global _preload_in_progress
        _preload_in_progress = True
        try:
            with get_db() as conn:
                with _embedding_lock:
                    _build_embedding_cache(conn)
                    rows = _embedding_cache.get("matrix")
            if rows is not None:
                print(f"[search] Embedding cache preloaded: {rows.shape[0]} embeddings")
            else:
                print("[search] Embedding cache preload: no embeddings found")
        except Exception as e:
            print(f"[search] Embedding cache preload failed: {e}")
        finally:
            _preload_in_progress = False

    t = threading.Thread(target=_preload, daemon=True, name="embedding-preload")
    t.start()


if __name__ == "__main__":
    # Use claw-recall CLI instead -- this module is a library
    print("Use the claw-recall CLI for searching. This module is imported as a library.")
    raise SystemExit(1)
