"""
Convo Memory — Python API for Agents

Easy import for any agent to search conversation history.

Usage:
    from convo_memory import recall, recall_semantic
    
    # Quick keyword search
    results = recall("LYFER campaign")
    
    # Semantic search (finds related concepts)
    results = recall_semantic("what did we decide about Facebook ads")
    
    # With filters
    results = recall("playbook", agent="cyrus", days=7)
"""

from pathlib import Path
import sys

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from search import search_conversations, SearchResult
from typing import List, Optional


def recall(
    query: str,
    agent: Optional[str] = None,
    channel: Optional[str] = None,
    days: Optional[int] = None,
    limit: int = 5
) -> List[dict]:
    """
    Quick keyword search of conversation history.
    
    Args:
        query: Keywords to search for
        agent: Filter by agent (main, cyrus, damian, etc.)
        channel: Filter by channel (telegram, discord, slack, etc.)
        days: Only search last N days
        limit: Max results (default 5)
    
    Returns:
        List of dicts with: agent, channel, role, content, timestamp, score
    """
    results = search_conversations(
        query=query,
        agent=agent,
        channel=channel,
        days=days,
        semantic=False,
        limit=limit
    )
    return _results_to_dicts(results)


def recall_semantic(
    query: str,
    agent: Optional[str] = None,
    channel: Optional[str] = None,
    days: Optional[int] = None,
    limit: int = 5
) -> List[dict]:
    """
    Semantic search — finds conceptually related messages.
    
    Args:
        query: Natural language query (e.g., "what did we decide about X")
        agent: Filter by agent
        channel: Filter by channel
        days: Only search last N days
        limit: Max results (default 5)
    
    Returns:
        List of dicts with: agent, channel, role, content, timestamp, score
    """
    results = search_conversations(
        query=query,
        agent=agent,
        channel=channel,
        days=days,
        semantic=True,
        limit=limit
    )
    return _results_to_dicts(results)


def _results_to_dicts(results: List[SearchResult]) -> List[dict]:
    """Convert SearchResult objects to simple dicts."""
    return [
        {
            "agent": r.agent_id,
            "channel": r.channel,
            "role": r.role,
            "content": r.content[:1000],
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "score": round(r.score, 3)
        }
        for r in results
    ]


# For testing
if __name__ == "__main__":
    import json
    
    # Test keyword search
    print("=== Keyword Search ===")
    results = recall("LYFER campaign", limit=3)
    print(json.dumps(results, indent=2, default=str))
    
    print("\n=== Semantic Search ===")
    results = recall_semantic("what did we decide about Facebook ads", limit=3)
    print(json.dumps(results, indent=2, default=str))
