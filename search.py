#!/usr/bin/env python3
"""
Convo Memory — Search Interface
Search past conversations using keywords or semantic similarity.
"""

import argparse
import sqlite3
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
    days: Optional[float] = None,
    limit: int = 20
) -> List[SearchResult]:
    """Search using FTS5 full-text search."""
    
    # Build FTS query
    # Escape special characters and add wildcards
    fts_query = ' '.join(f'"{word}"' for word in query.split())
    
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
    
    sql += " ORDER BY score LIMIT ?"
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


def semantic_search(
    conn: sqlite3.Connection,
    query: str,
    agent: Optional[str] = None,
    channel: Optional[str] = None,
    days: Optional[int] = None,
    limit: int = 20,
    openai_client: Optional['OpenAI'] = None
) -> List[SearchResult]:
    """Search using embedding similarity."""
    
    if not OPENAI_AVAILABLE or openai_client is None:
        print("⚠️  Semantic search requires OpenAI. Falling back to keyword search.")
        return keyword_search(conn, query, agent, channel, days, limit)
    
    # Generate query embedding
    response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=[query]
    )
    query_embedding = np.array(response.data[0].embedding, dtype=np.float32)
    
    # Build query for messages with embeddings
    sql = """
        SELECT 
            m.id,
            m.session_id,
            m.role,
            m.content,
            m.timestamp,
            s.agent_id,
            s.channel,
            e.embedding
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
    
    cursor = conn.execute(sql, params)
    
    # Calculate similarities
    scored_results = []
    for row in cursor.fetchall():
        embedding = np.frombuffer(row[7], dtype=np.float32)
        similarity = np.dot(query_embedding, embedding) / (
            np.linalg.norm(query_embedding) * np.linalg.norm(embedding)
        )
        
        scored_results.append((similarity, SearchResult(
            session_id=row[1],
            agent_id=row[5],
            channel=row[6],
            role=row[2],
            content=row[3],
            timestamp=datetime.fromisoformat(row[4]) if row[4] else None,
            score=float(similarity)
        )))
    
    # Sort by similarity and return top results
    scored_results.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored_results[:limit]]


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
        # Use first 500 chars + role as fingerprint
        fingerprint = f"{r.role}:{r.content[:500]}"
        if fingerprint not in seen_content:
            seen_content.add(fingerprint)
            unique.append(r)
    
    return unique


def format_results(results: List[SearchResult], verbose: bool = False) -> str:
    """Format search results for display."""
    if not results:
        return "No results found."
    
    # Deduplicate before displaying
    results = deduplicate_results(results)
    
    output = []
    for i, r in enumerate(results, 1):
        ts = r.timestamp.strftime("%Y-%m-%d %H:%M") if r.timestamp else "unknown"
        content_preview = r.content[:300] + "..." if len(r.content) > 300 else r.content
        
        output.append(f"\n{'='*60}")
        output.append(f"#{i} | Agent: {r.agent_id} | Channel: {r.channel} | {ts}")
        output.append(f"Score: {r.score:.3f} | Session: {r.session_id[:20]}...")
        output.append(f"{'='*60}")
        output.append(f"[{r.role}] {content_preview}")
        
        if verbose and r.context_before:
            output.append("\n--- Context Before ---")
            for ctx in r.context_before:
                output.append(f"  {ctx}")
        
        if verbose and r.context_after:
            output.append("\n--- Context After ---")
            for ctx in r.context_after:
                output.append(f"  {ctx}")
    
    return '\n'.join(output)


# Python API for agents
def search_conversations(
    query: str,
    agent: Optional[str] = None,
    channel: Optional[str] = None,
    days: Optional[float] = None,
    semantic: bool = False,
    limit: int = 10,
    db_path: Path = DB_PATH
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
        results = semantic_search(conn, query, agent, channel, days, limit, openai_client)
    else:
        results = keyword_search(conn, query, agent, channel, days, limit)
    
    conn.close()
    return results


def main():
    parser = argparse.ArgumentParser(description='Search conversation history')
    parser.add_argument('query', nargs='+', help='Search query')
    parser.add_argument('--agent', '-a', help='Filter by agent ID')
    parser.add_argument('--channel', '-c', help='Filter by channel')
    parser.add_argument('--days', '-d', type=int, help='Only search last N days')
    parser.add_argument('--semantic', '-s', action='store_true', help='Use semantic search')
    parser.add_argument('--limit', '-n', type=int, default=10, help='Max results')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show context')
    parser.add_argument('--db', type=Path, default=DB_PATH, help='Database path')
    
    args = parser.parse_args()
    query = ' '.join(args.query)
    
    if not args.db.exists():
        print(f"❌ Database not found: {args.db}")
        print("Run setup_db.py and index.py first")
        return
    
    conn = sqlite3.connect(args.db)
    
    openai_client = None
    if args.semantic and OPENAI_AVAILABLE:
        openai_client = OpenAI()
    
    print(f"🔍 Searching: '{query}'")
    if args.agent:
        print(f"   Agent: {args.agent}")
    if args.channel:
        print(f"   Channel: {args.channel}")
    if args.days:
        print(f"   Last {args.days} days")
    print(f"   Mode: {'semantic' if args.semantic else 'keyword'}")
    
    if args.semantic:
        results = semantic_search(conn, query, args.agent, args.channel, args.days, args.limit, openai_client)
    else:
        results = keyword_search(conn, query, args.agent, args.channel, args.days, args.limit)
    
    print(format_results(results, args.verbose))
    print(f"\n📊 Found {len(results)} results")
    
    conn.close()


if __name__ == "__main__":
    main()
