#!/usr/bin/env python3
"""
Convo Memory — Session Indexer
Parses OpenClaw session .jsonl files and indexes them into the database.
"""

import argparse
import json
import uuid
import sqlite3
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Generator
import numpy as np

# Optional: OpenAI for embeddings
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

DB_PATH = Path(__file__).parent / "convo_memory.db"
DEFAULT_ARCHIVE_PATH = Path.home() / ".openclaw" / "agents-archive"
DEFAULT_SESSIONS_PATH = Path.home() / ".openclaw" / "agents"

# Embedding settings
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_BATCH_SIZE = 20  # Keep small to stay within 8192 token limit per API call
MIN_CONTENT_LENGTH = 20  # Skip very short messages for embeddings


def parse_session_file(filepath: Path) -> Generator[dict, None, None]:
    """Parse a .jsonl session file and yield messages."""
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                data['_line_num'] = line_num
                yield data
            except json.JSONDecodeError as e:
                # Skip malformed lines
                continue


# Canonical agent name mapping — normalizes internal IDs to display names
AGENT_NAME_MAP = {
    'main': 'Kit',
    'kit': 'Kit',
    'claude': 'Claude',
    'claude-code': 'CC',
    'cc': 'CC',
    'cc-vps': 'CC-VPS',
    'gemini': 'gemini',
    'cyrus': 'cyrus',
    'damian': 'damian',
    'hale': 'hale',
    'arthur': 'arthur',
    'roman': 'roman',
    'sterling': 'sterling',
    'conrad': 'conrad',
    'elara': 'elara',
    'grok': 'grok',
    'chat': 'chat',
}

# Known OpenClaw agent slot names (used to validate filename-parsed agent IDs)
KNOWN_AGENTS = set(AGENT_NAME_MAP.keys())


def _normalize_agent_id(raw_id: str) -> str:
    """Normalize a raw agent ID to its canonical display name."""
    return AGENT_NAME_MAP.get(raw_id.lower(), raw_id)


def _is_hex_id(s: str) -> bool:
    """Check if a string looks like a hex session ID fragment (not an agent name)."""
    if len(s) < 6:
        return False
    try:
        int(s, 16)
        return s.lower() not in KNOWN_AGENTS
    except ValueError:
        return False


def extract_session_metadata(filepath: Path) -> dict:
    """Extract session metadata from filename and directory path.

    Resolution order (most reliable first):
    1. Directory path — agents/{name}/sessions/ or agents-archive-{name}/
    2. Filename prefix — agent-{name}-{channel}-... format
    3. UUID detection — Claude Code sessions in .claude/projects/
    4. Fallback — first filename part if it's a known agent name
    """
    filename = filepath.name
    path_str = str(filepath)

    metadata = {
        'source_file': path_str,
        'agent_id': 'unknown',
        'channel': 'unknown',
        'channel_id': None,
    }

    # === PHASE 1: Path-based detection (most reliable) ===
    # Specific archive dirs checked BEFORE the generic archive pattern

    # WSL detection: /home/rodbland/ = WSL machine, 'main' agent = Claude (not Kit)
    is_wsl = '/home/rodbland/' in path_str

    # Pattern: /agents/{agent_name}/sessions/{file}.jsonl (active sessions)
    agents_match = re.search(r'/agents/([^/]+)/sessions/', path_str)
    if agents_match:
        raw = agents_match.group(1)
        if is_wsl and raw == 'main':
            metadata['agent_id'] = 'Claude'
        else:
            metadata['agent_id'] = _normalize_agent_id(raw)
        metadata['channel'] = 'direct'

    # Pattern: /agents-archive-cc/ (synced CC desktop archive)
    if metadata['agent_id'] == 'unknown':
        if '/agents-archive-cc/' in path_str:
            metadata['agent_id'] = 'CC'
            metadata['channel'] = 'terminal'

    # Pattern: /agents-archive-claude/ (synced Claude archive)
    if metadata['agent_id'] == 'unknown':
        if '/agents-archive-claude/' in path_str:
            metadata['agent_id'] = 'Claude'
            metadata['channel'] = 'direct'

    # Pattern: /agents-archive-vps/ (synced VPS archive — these are Kit's)
    if metadata['agent_id'] == 'unknown':
        if '/agents-archive-vps/' in path_str:
            metadata['agent_id'] = 'Kit'
            metadata['channel'] = 'direct'

    # Pattern: /agents-archive/{agent_name}/{file} (generic archived, agent subdirs)
    if metadata['agent_id'] == 'unknown':
        archive_subdir = re.search(r'/agents-archive/([^/]+)/', path_str)
        if archive_subdir:
            metadata['agent_id'] = _normalize_agent_id(archive_subdir.group(1))
            metadata['channel'] = 'direct'

    # Pattern: /agents-grok-sessions/ or /agents-chat-sessions/
    if metadata['agent_id'] == 'unknown':
        grok_match = re.search(r'/agents-grok-sessions/', path_str)
        chat_match = re.search(r'/agents-chat-sessions/', path_str)
        if grok_match:
            metadata['agent_id'] = 'grok'
            metadata['channel'] = 'direct'
        elif chat_match:
            metadata['agent_id'] = 'chat'
            metadata['channel'] = 'direct'

    # Pattern: .claude/projects/ (Claude Code sessions)
    # /home/clawdbot/.claude/projects/ = CC-VPS (Claude Code on VPS)
    # /home/rodbland/.claude/projects/ = CC (Claude Code on WSL)
    if metadata['agent_id'] == 'unknown':
        if '.claude/projects' in path_str:
            if '/home/clawdbot/' in path_str:
                metadata['agent_id'] = 'CC-VPS'
            else:
                metadata['agent_id'] = 'CC'
            metadata['channel'] = 'terminal'
            # Check if tagged as telegram session
            session_id = filepath.stem
            marker = filepath.parent / 'telegram-sessions.json'
            if marker.exists():
                try:
                    tg_sessions = json.loads(marker.read_text())
                    if session_id in tg_sessions:
                        metadata['channel'] = 'telegram'
                except:
                    pass

    # === PHASE 2: Filename-based detection ===

    # Strip .deleted.* suffix for parsing
    clean_name = re.sub(r'\.deleted\.\S+$', '', filename)
    parts = clean_name.replace('.jsonl', '').split('-')

    if parts[0] == 'agent' and len(parts) >= 2:
        # OpenClaw format: agent-{agent_id}-{channel}-...
        raw_agent = parts[1]
        if metadata['agent_id'] == 'unknown' or metadata['agent_id'] in ('Kit', 'Claude'):
            # Filename agent overrides only if it's a known agent and path gave us a generic answer
            # BUT: on WSL, 'main' = Claude, not Kit — don't let filename override back
            if raw_agent.lower() in KNOWN_AGENTS:
                if is_wsl and raw_agent == 'main':
                    metadata['agent_id'] = 'Claude'
                else:
                    metadata['agent_id'] = _normalize_agent_id(raw_agent)
        # Always extract channel from filename if available
        if len(parts) >= 3:
            metadata['channel'] = parts[2]
        if len(parts) >= 5 and parts[2] in ('discord', 'slack', 'telegram'):
            metadata['channel_id'] = '-'.join(parts[3:]) if parts[2] == 'discord' else parts[3]

    # UUID filename in agents/ dir — path already resolved agent above
    # UUID filename in .claude/projects/ — path already resolved to CC above
    # Only need to handle UUID filenames with no path match
    if metadata['agent_id'] == 'unknown':
        session_id = filepath.stem
        try:
            uuid.UUID(session_id)
            metadata['agent_id'] = 'CC'
            metadata['channel'] = 'terminal'
        except ValueError:
            pass

    # === PHASE 3: Fallback — first filename part ===
    if metadata['agent_id'] == 'unknown' and parts:
        raw = parts[0].lower()
        if raw in KNOWN_AGENTS:
            if is_wsl and raw == 'main':
                metadata['agent_id'] = 'Claude'
            else:
                metadata['agent_id'] = _normalize_agent_id(raw)
        elif _is_hex_id(parts[0]):
            # Hex ID fragment — this is what we're trying to avoid
            # Try to infer from parent directory name
            parent = filepath.parent.name
            if parent.lower() in KNOWN_AGENTS:
                metadata['agent_id'] = _normalize_agent_id(parent)
            else:
                metadata['agent_id'] = 'unknown'

    # Final safety: reject hex-looking agent IDs
    if _is_hex_id(metadata['agent_id']):
        metadata['agent_id'] = 'unknown'

    return metadata


TOOL_RESULT_ROLES = {'toolResult', 'tool_result', 'tool-result'}
CC_SYSTEM_TAG_RE = re.compile(
    r'<(?:system-reminder|local-command-\w+|command-\w+)[^>]*>.*?'
    r'</(?:system-reminder|local-command-\w+|command-\w+)>',
    re.DOTALL
)


def _parse_timestamp(entry: dict) -> Optional[datetime]:
    """Extract timestamp from a session entry."""
    ts_str = entry.get('timestamp')
    if ts_str is None:
        return None
    try:
        if isinstance(ts_str, str):
            return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        if isinstance(ts_str, (int, float)):
            return datetime.fromtimestamp(ts_str / 1000)
    except (ValueError, OSError, OverflowError):
        pass
    return None


def _extract_text(raw_content) -> Optional[str]:
    """Extract plain text from a message's content field (string or list of parts)."""
    if isinstance(raw_content, str):
        return raw_content
    if isinstance(raw_content, list):
        parts = [p.get('text', '') for p in raw_content
                 if isinstance(p, dict) and p.get('type') == 'text']
        return ' '.join(parts) if parts else None
    return None


def _try_timestamp_from_content(content: str) -> Optional[datetime]:
    """Try to extract timestamp from message text like [2026-02-06 10:25 GMT+11]."""
    ts_match = re.search(r'\[(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})', content)
    if ts_match:
        try:
            return datetime.strptime(f"{ts_match.group(1)} {ts_match.group(2)}", "%Y-%m-%d %H:%M")
        except ValueError:
            pass
    return None


def extract_messages(filepath: Path, start_offset: int = 0, start_index: int = 0):
    """Extract messages from a session file (OpenClaw, Claude Code, or legacy format).

    Args:
        start_offset: Byte offset to start reading from (for incremental indexing).
        start_index: Starting message_index for new messages.

    Returns:
        (messages, first_timestamp, last_timestamp, end_byte_offset)
    """
    messages = []
    first_timestamp = None
    last_timestamp = None
    end_offset = start_offset

    # Use binary mode so f.tell() returns reliable byte offsets
    with open(filepath, 'rb') as f:
        if start_offset > 0:
            f.seek(start_offset)

        while True:
            raw_line = f.readline()
            if not raw_line:
                break  # EOF
            if not raw_line.endswith(b'\n'):
                break  # Partial line at EOF — skip, will be picked up next time
            end_offset = f.tell()

            try:
                line = raw_line.decode('utf-8', errors='replace').strip()
            except Exception:
                continue
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            entry_type = entry.get('type')
            role = None
            raw_content = None
            is_cc = False

            if entry_type == 'message':
                # OpenClaw: {"type": "message", "message": {...}}
                msg = entry.get('message', {})
                role = msg.get('role')
                raw_content = msg.get('content', '')
            elif entry_type in ('user', 'assistant') and 'message' in entry:
                # Claude Code: {"type": "user"/"assistant", "message": {"role": ..., "content": ...}}
                msg = entry.get('message', {})
                role = msg.get('role', entry_type)
                raw_content = msg.get('content', '')
                is_cc = True
            elif 'role' in entry and 'content' in entry and entry_type is None:
                # Legacy: {"role": "user", "content": "..."}
                role = entry.get('role')
                raw_content = entry.get('content', '')
            else:
                continue

            if role in TOOL_RESULT_ROLES:
                continue

            content = _extract_text(raw_content)
            if not content or not content.strip():
                continue
            content = content.strip()

            # Strip CC system tags
            if is_cc:
                content = CC_SYSTEM_TAG_RE.sub('', content).strip()
                if not content:
                    continue

            timestamp = _parse_timestamp(entry)
            if timestamp is None:
                timestamp = _try_timestamp_from_content(content)

            messages.append({
                'role': role,
                'content': content,
                'timestamp': timestamp,
                'message_index': start_index + len(messages)
            })

            if timestamp:
                if first_timestamp is None:
                    first_timestamp = timestamp
                last_timestamp = timestamp

    return messages, first_timestamp, last_timestamp, end_offset


def generate_embeddings(texts: list[str], client: Optional['OpenAI'] = None) -> list[np.ndarray]:
    """Generate embeddings for a list of texts."""
    if not OPENAI_AVAILABLE or client is None:
        return [None] * len(texts)
    
    embeddings = []
    for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        batch = texts[i:i + EMBEDDING_BATCH_SIZE]
        try:
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=batch
            )
            for item in response.data:
                embeddings.append(np.array(item.embedding, dtype=np.float32))
        except Exception as e:
            print(f"⚠️  Embedding error: {e}")
            embeddings.extend([None] * len(batch))
    
    return embeddings


_schema_migrated = False


def _ensure_incremental_schema(conn):
    """One-time migration: add last_byte_offset column for incremental indexing."""
    global _schema_migrated
    if _schema_migrated:
        return
    try:
        conn.execute("SELECT last_byte_offset FROM index_log LIMIT 0")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE index_log ADD COLUMN last_byte_offset INTEGER DEFAULT 0")
        conn.commit()
    _schema_migrated = True


def index_session_file(
    filepath: Path,
    conn: sqlite3.Connection,
    generate_embeds: bool = False,
    openai_client: Optional['OpenAI'] = None,
    source_file_override: str = None,
) -> dict:
    """Index a single session file into the database.

    Supports incremental indexing: if a file has grown since last index,
    only new lines are parsed and inserted (no DELETE + full re-insert).

    Args:
        source_file_override: If provided, use this path for de-duplication
            in index_log and sessions instead of the local filepath.
            Used by the /index-session endpoint for remote files.
    """
    _ensure_incremental_schema(conn)
    canonical_source = source_file_override or str(filepath)
    current_size = filepath.stat().st_size

    # Check if already indexed
    cursor = conn.execute(
        "SELECT id, file_size, message_count, last_byte_offset FROM index_log WHERE source_file = ?",
        (canonical_source,)
    )
    existing = cursor.fetchone()

    if existing:
        old_id, old_size, old_msg_count, old_offset = existing
        old_msg_count = old_msg_count or 0
        old_offset = old_offset or 0

        if old_size == current_size:
            return {'status': 'skipped', 'reason': 'already indexed'}

        session_id = filepath.stem

        # File grew AND we have a valid offset → incremental indexing
        if current_size > old_size and old_offset > 0:
            metadata_path = Path(source_file_override) if source_file_override else filepath
            metadata = extract_session_metadata(metadata_path)

            new_messages, _, last_ts, end_offset = extract_messages(
                filepath, start_offset=old_offset, start_index=old_msg_count
            )

            if not new_messages:
                # File grew but no new parseable messages (whitespace, system lines, partial writes)
                conn.execute(
                    "UPDATE index_log SET file_size = ?, last_byte_offset = ? WHERE id = ?",
                    (current_size, end_offset, old_id)
                )
                conn.commit()
                return {'status': 'skipped', 'reason': 'no new messages'}

            # INSERT only new messages
            for msg in new_messages:
                conn.execute("""
                    INSERT INTO messages (session_id, role, content, timestamp, message_index)
                    VALUES (?, ?, ?, ?, ?)
                """, (session_id, msg['role'], msg['content'], msg['timestamp'], msg['message_index']))

            total_messages = old_msg_count + len(new_messages)

            # UPDATE session metadata
            conn.execute(
                "UPDATE sessions SET ended_at = ?, message_count = ? WHERE id = ?",
                (last_ts, total_messages, session_id)
            )

            # UPDATE index_log
            stat = filepath.stat()
            conn.execute("""
                UPDATE index_log SET file_size = ?, file_mtime = ?, message_count = ?, last_byte_offset = ?
                WHERE id = ?
            """, (stat.st_size, datetime.fromtimestamp(stat.st_mtime), total_messages, end_offset, old_id))

            conn.commit()

            return {
                'status': 'indexed',
                'session_id': session_id,
                'agent': metadata['agent_id'],
                'messages': len(new_messages),
                'total_messages': total_messages,
                'incremental': True,
                'embeddings': 0
            }

        # File shrunk or no stored offset → full re-index (delete old data, fall through)
        conn.execute("DELETE FROM embeddings WHERE message_id IN (SELECT id FROM messages WHERE session_id = ?)", (session_id,))
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.execute("DELETE FROM index_log WHERE id = ?", (old_id,))

    # --- Full indexing (new file or after reset) ---
    metadata_path = Path(source_file_override) if source_file_override else filepath
    metadata = extract_session_metadata(metadata_path)

    messages, first_ts, last_ts, end_offset = extract_messages(filepath)

    if not messages:
        return {'status': 'skipped', 'reason': 'no messages'}

    session_id = filepath.stem

    # Insert session
    conn.execute("""
        INSERT OR REPLACE INTO sessions
        (id, agent_id, channel, channel_id, started_at, ended_at, message_count, source_file)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        session_id,
        metadata['agent_id'],
        metadata['channel'],
        metadata.get('channel_id'),
        first_ts,
        last_ts,
        len(messages),
        canonical_source
    ))

    # Insert messages
    message_ids = []
    for msg in messages:
        cursor = conn.execute("""
            INSERT INTO messages (session_id, role, content, timestamp, message_index)
            VALUES (?, ?, ?, ?, ?)
        """, (
            session_id,
            msg['role'],
            msg['content'],
            msg['timestamp'],
            msg['message_index']
        ))
        message_ids.append(cursor.lastrowid)

    # Generate and store embeddings if requested
    embed_count = 0
    if generate_embeds and openai_client:
        embed_candidates = [
            (mid, msg['content'])
            for mid, msg in zip(message_ids, messages)
            if len(msg['content']) >= MIN_CONTENT_LENGTH
        ]

        if embed_candidates:
            texts = [c[1][:2000] for c in embed_candidates]
            embeddings = generate_embeddings(texts, openai_client)

            for (mid, _), embedding in zip(embed_candidates, embeddings):
                if embedding is not None:
                    conn.execute("""
                        INSERT INTO embeddings (message_id, embedding, model)
                        VALUES (?, ?, ?)
                    """, (mid, embedding.tobytes(), EMBEDDING_MODEL))
                    embed_count += 1

    # Log indexing with byte offset for incremental next time
    stat = filepath.stat()
    conn.execute("""
        INSERT INTO index_log (source_file, file_size, file_mtime, message_count, last_byte_offset)
        VALUES (?, ?, ?, ?, ?)
    """, (canonical_source, stat.st_size, datetime.fromtimestamp(stat.st_mtime), len(messages), end_offset))

    conn.commit()

    return {
        'status': 'indexed',
        'session_id': session_id,
        'agent': metadata['agent_id'],
        'messages': len(messages),
        'embeddings': embed_count
    }


def index_directory(
    source_dir: Path,
    conn: sqlite3.Connection,
    generate_embeds: bool = False,
    openai_client: Optional['OpenAI'] = None
) -> dict:
    """Index all session files in a directory."""
    
    results = {
        'indexed': 0,
        'skipped': 0,
        'errors': 0,
        'total_messages': 0,
        'total_embeddings': 0
    }
    
    # Find all .jsonl files (skip subagent sessions — they're fragments with hex IDs)
    session_files = [f for f in source_dir.glob("**/*.jsonl")
                     if '/subagents/' not in str(f)]
    print(f"Found {len(session_files)} session files")
    
    for filepath in session_files:
        try:
            result = index_session_file(filepath, conn, generate_embeds, openai_client)
            
            if result['status'] == 'indexed':
                results['indexed'] += 1
                results['total_messages'] += result['messages']
                results['total_embeddings'] += result.get('embeddings', 0)
                print(f"  ✅ {filepath.name}: {result['messages']} msgs, {result.get('embeddings', 0)} embeds")
            else:
                results['skipped'] += 1
                
        except Exception as e:
            results['errors'] += 1
            print(f"  ❌ {filepath.name}: {e}")
    
    return results


def backfill_embeddings(conn: sqlite3.Connection, openai_client: 'OpenAI') -> dict:
    """Generate embeddings for messages that don't have them yet."""
    
    # Find messages without embeddings (that are long enough to be worth embedding)
    cursor = conn.execute("""
        SELECT m.id, m.content
        FROM messages m
        LEFT JOIN embeddings e ON e.message_id = m.id
        WHERE e.id IS NULL AND LENGTH(m.content) >= ?
        ORDER BY m.id
    """, (MIN_CONTENT_LENGTH,))
    
    candidates = cursor.fetchall()
    
    if not candidates:
        print("✅ All eligible messages already have embeddings")
        return {'backfilled': 0, 'skipped': 0}
    
    print(f"🔄 Backfilling embeddings for {len(candidates)} messages...")
    
    backfilled = 0
    for i in range(0, len(candidates), EMBEDDING_BATCH_SIZE):
        batch = candidates[i:i + EMBEDDING_BATCH_SIZE]
        texts = [content[:2000] for _, content in batch]  # ~1500 tokens, safe for 8192 limit
        
        try:
            response = openai_client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=texts
            )
            for (mid, _), item in zip(batch, response.data):
                embedding = np.array(item.embedding, dtype=np.float32)
                conn.execute("""
                    INSERT INTO embeddings (message_id, embedding, model)
                    VALUES (?, ?, ?)
                """, (mid, embedding.tobytes(), EMBEDDING_MODEL))
                backfilled += 1
        except Exception as e:
            print(f"⚠️  Embedding batch error: {e}")
        
        if (i + EMBEDDING_BATCH_SIZE) % 500 == 0:
            conn.commit()
            print(f"   Progress: {min(i + EMBEDDING_BATCH_SIZE, len(candidates))}/{len(candidates)}")
    
    conn.commit()
    
    # Count how many still lack embeddings (too short)
    skipped = conn.execute("""
        SELECT COUNT(*) FROM messages m
        LEFT JOIN embeddings e ON e.message_id = m.id
        WHERE e.id IS NULL
    """).fetchone()[0]
    
    return {'backfilled': backfilled, 'skipped': skipped}


def main():
    parser = argparse.ArgumentParser(description='Index OpenClaw session files')
    parser.add_argument('--source', type=Path, default=DEFAULT_ARCHIVE_PATH,
                        help='Source directory containing session files')
    parser.add_argument('--include-active', action='store_true',
                        help='Also index active sessions (not just archive)')
    parser.add_argument('--embeddings', action='store_true',
                        help='Generate embeddings for semantic search')
    parser.add_argument('--db', type=Path, default=DB_PATH,
                        help='Database path')
    parser.add_argument('--incremental', action='store_true',
                        help='Only index new files (skip already indexed)')
    parser.add_argument('--quick', action='store_true',
                        help='Quick mode: only index files modified in last 20 minutes')

    args = parser.parse_args()
    
    # Setup database
    if not args.db.exists():
        from setup_db import setup_database
        conn = setup_database(args.db)
    else:
        conn = sqlite3.connect(args.db)
    
    # Setup OpenAI client if embeddings requested
    openai_client = None
    if args.embeddings:
        if OPENAI_AVAILABLE:
            openai_client = OpenAI()
            print("✅ OpenAI client ready for embeddings")
        else:
            print("⚠️  OpenAI not available, skipping embeddings")
    
    # Quick mode: only index recently modified files across all dirs
    if args.quick:
        import time as _time
        cutoff = _time.time() - (20 * 60)  # 20 minutes ago
        results = {'indexed': 0, 'skipped': 0, 'errors': 0, 'total_messages': 0, 'total_embeddings': 0}
        all_dirs = [args.source, DEFAULT_SESSIONS_PATH]
        for scan_dir in all_dirs:
            if not scan_dir.exists():
                continue
            for filepath in scan_dir.glob("**/*.jsonl"):
                if '/subagents/' in str(filepath):
                    continue
                if filepath.stat().st_mtime < cutoff:
                    continue
                try:
                    r = index_session_file(filepath, conn, args.embeddings, openai_client)
                    if r['status'] == 'indexed':
                        results['indexed'] += 1
                        results['total_messages'] += r['messages']
                        print(f"  ✅ {filepath.name}: {r['messages']} msgs")
                    else:
                        results['skipped'] += 1
                except Exception as e:
                    results['errors'] += 1
                    print(f"  ❌ {filepath.name}: {e}")
        conn.close()
        if results['indexed'] > 0:
            print(f"\n📊 Quick index: {results['indexed']} files indexed, {results['total_messages']} messages")
        return

    print(f"\n📂 Indexing: {args.source}")
    results = index_directory(args.source, conn, args.embeddings, openai_client)

    # Also index active sessions if requested
    if args.include_active:
        print(f"\n📂 Indexing active sessions: {DEFAULT_SESSIONS_PATH}")
        for agent_dir in DEFAULT_SESSIONS_PATH.iterdir():
            if agent_dir.is_dir():
                sessions_dir = agent_dir / "sessions"
                if sessions_dir.exists():
                    r = index_directory(sessions_dir, conn, args.embeddings, openai_client)
                    results['indexed'] += r['indexed']
                    results['skipped'] += r['skipped']
                    results['errors'] += r['errors']
                    results['total_messages'] += r['total_messages']
                    results['total_embeddings'] += r['total_embeddings']
    
    # Backfill embeddings for any previously-indexed messages that lack them
    if args.embeddings and openai_client:
        print(f"\n🔄 Checking for messages missing embeddings...")
        backfill = backfill_embeddings(conn, openai_client)
        results['total_embeddings'] += backfill['backfilled']
        if backfill['backfilled'] > 0:
            print(f"   Backfilled: {backfill['backfilled']} embeddings")
        if backfill['skipped'] > 0:
            print(f"   Skipped: {backfill['skipped']} (too short, <{MIN_CONTENT_LENGTH} chars)")
    
    conn.close()
    
    print(f"\n📊 Results:")
    print(f"   Indexed: {results['indexed']} sessions")
    print(f"   Skipped: {results['skipped']} (already indexed or empty)")
    print(f"   Errors: {results['errors']}")
    print(f"   Total messages: {results['total_messages']}")
    print(f"   Total embeddings: {results['total_embeddings']}")


if __name__ == "__main__":
    main()
