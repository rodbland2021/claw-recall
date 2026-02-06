#!/usr/bin/env python3
"""
Search markdown files across all agent workspaces.
Complements convo-memory by searching persistent docs, not just conversations.
"""

import argparse
import os
import re
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass
from functools import lru_cache
import time

# Simple cache for file contents
_file_cache: Dict[str, Tuple[float, List[str]]] = {}
_CACHE_TTL = 300  # 5 minutes

# Agent workspaces to search
AGENT_DIRS = [
    Path("/home/clawdbot/clawd"),
    Path("/home/clawdbot/clawd-cyrus"),
    Path("/home/clawdbot/clawd-arthur"),
    Path("/home/clawdbot/clawd-damian"),
    Path("/home/clawdbot/clawd-hale"),
    Path("/home/clawdbot/clawd-roman"),
    Path("/home/clawdbot/clawd-conrad"),
    Path("/home/clawdbot/clawd-elara"),
    Path("/home/clawdbot/clawd-sterling"),
    Path("/home/clawdbot/shared"),
]

# Directories to skip
SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', 'venv', 'videos', 'tmp'}

# File patterns to search
FILE_PATTERNS = ['*.md', '*.txt']  # Removed json for speed


def _get_file_lines(filepath: Path) -> List[str]:
    """Get file lines with caching."""
    path_str = str(filepath)
    mtime = filepath.stat().st_mtime
    
    # Check cache
    if path_str in _file_cache:
        cached_mtime, cached_lines = _file_cache[path_str]
        if cached_mtime == mtime:
            return cached_lines
    
    # Read and cache
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        _file_cache[path_str] = (mtime, lines)
        return lines
    except Exception:
        return []


@dataclass
class FileMatch:
    """A search result from file search."""
    path: str
    agent: str
    line_num: int
    line: str
    context_before: List[str]
    context_after: List[str]
    score: float = 1.0


def get_agent_from_path(path: Path) -> str:
    """Extract agent name from file path."""
    path_str = str(path)
    if '/clawd-' in path_str:
        match = re.search(r'/clawd-(\w+)/', path_str)
        if match:
            return match.group(1)
    elif '/clawd/' in path_str:
        return 'main'
    elif '/shared/' in path_str:
        return 'shared'
    return 'unknown'


def search_files(
    query: str,
    agent: Optional[str] = None,
    file_type: Optional[str] = None,
    limit: int = 20,
    context_lines: int = 2
) -> List[FileMatch]:
    """
    Search markdown/text files for a query string.
    
    Args:
        query: Search string (case-insensitive)
        agent: Filter by agent (main, cyrus, etc.)
        file_type: Filter by extension (md, txt, json)
        limit: Max results
        context_lines: Lines of context before/after match
    
    Returns:
        List of FileMatch objects
    """
    results = []
    query_lower = query.lower()
    query_words = query_lower.split()
    
    # Determine which directories to search
    search_dirs = AGENT_DIRS
    if agent:
        if agent == 'main':
            search_dirs = [Path("/home/clawdbot/clawd")]
        elif agent == 'shared':
            search_dirs = [Path("/home/clawdbot/shared")]
        else:
            search_dirs = [Path(f"/home/clawdbot/clawd-{agent}")]
    
    # Determine file patterns
    patterns = FILE_PATTERNS
    if file_type:
        patterns = [f'*.{file_type}']
    
    for base_dir in search_dirs:
        if not base_dir.exists():
            continue
            
        for pattern in patterns:
            for filepath in base_dir.rglob(pattern):
                # Skip excluded directories
                if any(skip in filepath.parts for skip in SKIP_DIRS):
                    continue
                
                try:
                    lines = _get_file_lines(filepath)
                    if not lines:
                        continue
                    
                    for i, line in enumerate(lines):
                        line_lower = line.lower()
                        
                        # Check if all query words are in the line
                        if all(word in line_lower for word in query_words):
                            # Get context
                            start = max(0, i - context_lines)
                            end = min(len(lines), i + context_lines + 1)
                            
                            context_before = [l.rstrip() for l in lines[start:i]]
                            context_after = [l.rstrip() for l in lines[i+1:end]]
                            
                            # Calculate relevance score (more matching words = higher)
                            word_matches = sum(1 for word in query_words if word in line_lower)
                            score = word_matches / len(query_words)
                            
                            results.append(FileMatch(
                                path=str(filepath),
                                agent=get_agent_from_path(filepath),
                                line_num=i + 1,
                                line=line.rstrip(),
                                context_before=context_before,
                                context_after=context_after,
                                score=score
                            ))
                            
                            if len(results) >= limit * 2:  # Get extra for dedup
                                break
                                
                except Exception as e:
                    continue
    
    # Sort by score and deduplicate
    results.sort(key=lambda x: -x.score)
    
    # Deduplicate by content
    seen = set()
    unique = []
    for r in results:
        fingerprint = r.line[:100]
        if fingerprint not in seen:
            seen.add(fingerprint)
            unique.append(r)
            if len(unique) >= limit:
                break
    
    return unique


def format_results(results: List[FileMatch], verbose: bool = False) -> str:
    """Format search results for display."""
    if not results:
        return "No results found in markdown files."
    
    output = []
    for i, r in enumerate(results, 1):
        # Shorten path for display
        short_path = r.path.replace('/home/clawdbot/', '~/')
        
        output.append(f"\n{'='*60}")
        output.append(f"#{i} | Agent: {r.agent} | {short_path}:{r.line_num}")
        output.append(f"{'='*60}")
        
        if verbose and r.context_before:
            for ctx in r.context_before:
                output.append(f"  {ctx[:100]}")
        
        output.append(f"→ {r.line[:200]}")
        
        if verbose and r.context_after:
            for ctx in r.context_after:
                output.append(f"  {ctx[:100]}")
    
    return '\n'.join(output)


# Python API
def search_docs(query: str, agent: Optional[str] = None, limit: int = 10) -> List[dict]:
    """
    Quick search of markdown files across all agents.
    
    Args:
        query: Keywords to search for
        agent: Filter by agent (main, cyrus, etc.)
        limit: Max results
    
    Returns:
        List of dicts with: path, agent, line_num, line, score
    """
    results = search_files(query, agent=agent, limit=limit)
    return [
        {
            "path": r.path.replace('/home/clawdbot/', '~/'),
            "agent": r.agent,
            "line_num": r.line_num,
            "line": r.line[:500],
            "score": round(r.score, 3)
        }
        for r in results
    ]


def main():
    parser = argparse.ArgumentParser(description='Search markdown files across agent workspaces')
    parser.add_argument('query', nargs='+', help='Search query')
    parser.add_argument('--agent', '-a', help='Filter by agent (main, cyrus, shared, etc.)')
    parser.add_argument('--type', '-t', help='File type (md, txt, json)')
    parser.add_argument('--limit', '-n', type=int, default=10, help='Max results')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show context')
    
    args = parser.parse_args()
    query = ' '.join(args.query)
    
    print(f"🔍 Searching files for: '{query}'")
    if args.agent:
        print(f"   Agent: {args.agent}")
    
    results = search_files(
        query,
        agent=args.agent,
        file_type=args.type,
        limit=args.limit
    )
    
    print(format_results(results, args.verbose))
    print(f"\n📊 Found {len(results)} results")


if __name__ == "__main__":
    main()
