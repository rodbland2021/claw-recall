#!/usr/bin/env python3
"""
Convo Memory — Database Setup
Creates the SQLite database with FTS5 full-text search.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "convo_memory.db"

SCHEMA = """
-- Sessions table: metadata about each conversation
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    channel TEXT,
    channel_id TEXT,
    started_at DATETIME,
    ended_at DATETIME,
    message_count INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    source_file TEXT,
    indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    is_archived BOOLEAN DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent_id);
CREATE INDEX IF NOT EXISTS idx_sessions_channel ON sessions(channel);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);

-- Messages table: individual messages from conversations
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,  -- user, assistant, system, tool_use, tool_result
    content TEXT,
    timestamp DATETIME,
    message_index INTEGER,
    token_count INTEGER,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);

-- Full-text search virtual table (FTS5)
-- Only indexes content; agent_id comes from sessions JOIN at query time
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content='messages',
    content_rowid='id'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (NEW.id, NEW.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content)
    VALUES('delete', OLD.id, OLD.content);
END;

-- Embeddings table for semantic search
CREATE TABLE IF NOT EXISTS embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    embedding BLOB NOT NULL,  -- Serialized numpy array
    model TEXT DEFAULT 'text-embedding-3-small',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

CREATE INDEX IF NOT EXISTS idx_embeddings_message ON embeddings(message_id);

-- Index tracking to avoid re-indexing (supports incremental indexing via last_byte_offset)
CREATE TABLE IF NOT EXISTS index_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT UNIQUE NOT NULL,
    file_size INTEGER,
    file_mtime DATETIME,
    indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    message_count INTEGER,
    last_byte_offset INTEGER DEFAULT 0,
    status TEXT DEFAULT 'complete'
);

CREATE INDEX IF NOT EXISTS idx_index_log_file ON index_log(source_file);

-- Thoughts table: quick-captured notes from agents, CLI, HTTP, MCP, Telegram
CREATE TABLE IF NOT EXISTS thoughts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'cli',  -- cli, http, mcp, telegram
    agent TEXT,                           -- which agent captured it
    metadata TEXT DEFAULT '{}',           -- JSON: topics, people, type, etc.
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_thoughts_source ON thoughts(source);
CREATE INDEX IF NOT EXISTS idx_thoughts_agent ON thoughts(agent);
CREATE INDEX IF NOT EXISTS idx_thoughts_created ON thoughts(created_at DESC);

-- FTS5 for keyword search on thoughts
CREATE VIRTUAL TABLE IF NOT EXISTS thoughts_fts USING fts5(
    content, metadata, source, agent,
    content='thoughts', content_rowid='id'
);

-- Keep thoughts FTS in sync
CREATE TRIGGER IF NOT EXISTS thoughts_ai AFTER INSERT ON thoughts BEGIN
    INSERT INTO thoughts_fts(rowid, content, metadata, source, agent)
    VALUES (new.id, new.content, new.metadata, new.source, new.agent);
END;

CREATE TRIGGER IF NOT EXISTS thoughts_au AFTER UPDATE ON thoughts BEGIN
    INSERT INTO thoughts_fts(thoughts_fts, rowid, content, metadata, source, agent)
    VALUES ('delete', old.id, old.content, old.metadata, old.source, old.agent);
    INSERT INTO thoughts_fts(rowid, content, metadata, source, agent)
    VALUES (new.id, new.content, new.metadata, new.source, new.agent);
END;

CREATE TRIGGER IF NOT EXISTS thoughts_ad AFTER DELETE ON thoughts BEGIN
    INSERT INTO thoughts_fts(thoughts_fts, rowid, content, metadata, source, agent)
    VALUES ('delete', old.id, old.content, old.metadata, old.source, old.agent);
END;

-- Embeddings for thoughts (separate from message embeddings)
CREATE TABLE IF NOT EXISTS thought_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thought_id INTEGER NOT NULL,
    embedding BLOB NOT NULL,  -- Serialized numpy array (1536-dim float32)
    model TEXT DEFAULT 'text-embedding-3-small',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (thought_id) REFERENCES thoughts(id)
);

CREATE INDEX IF NOT EXISTS idx_thought_embeddings_thought ON thought_embeddings(thought_id);

-- Capture log: tracks what's been ingested from external sources (Gmail, Drive, Slack)
CREATE TABLE IF NOT EXISTS capture_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,      -- 'gmail', 'drive', 'slack'
    source_id TEXT NOT NULL,        -- message_id, doc_id, channel+ts
    account TEXT,                   -- 'personal', 'rbs'
    thought_id INTEGER,             -- FK to thoughts.id
    source_modified TEXT,           -- modifiedTime for Drive docs (detect updates)
    captured_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_type, source_id, account)
);

CREATE INDEX IF NOT EXISTS idx_capture_log_source ON capture_log(source_type, account);
"""


def setup_database(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Create database and tables."""
    print(f"Setting up database at: {db_path}")
    
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    
    # Verify tables
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in cursor.fetchall()]
    print(f"✅ Created tables: {', '.join(tables)}")
    
    return conn


def get_db_stats(db_path: Path = DB_PATH) -> dict:
    """Get database statistics."""
    if not db_path.exists():
        return {"exists": False}
    
    conn = sqlite3.connect(db_path)
    stats = {"exists": True}
    
    stats["sessions"] = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    stats["messages"] = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    stats["embeddings"] = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    try:
        stats["thoughts"] = conn.execute("SELECT COUNT(*) FROM thoughts").fetchone()[0]
        stats["thought_embeddings"] = conn.execute("SELECT COUNT(*) FROM thought_embeddings").fetchone()[0]
    except sqlite3.OperationalError:
        stats["thoughts"] = 0
        stats["thought_embeddings"] = 0
    stats["agents"] = conn.execute("SELECT DISTINCT agent_id FROM sessions").fetchall()
    stats["agents"] = [a[0] for a in stats["agents"]]
    stats["file_size_mb"] = round(db_path.stat().st_size / 1024 / 1024, 2)
    
    conn.close()
    return stats


if __name__ == "__main__":
    conn = setup_database()
    conn.close()
    
    stats = get_db_stats()
    print(f"\n📊 Database stats:")
    print(f"   Sessions: {stats.get('sessions', 0)}")
    print(f"   Messages: {stats.get('messages', 0)}")
    print(f"   Embeddings: {stats.get('embeddings', 0)}")
    print(f"   Size: {stats.get('file_size_mb', 0)} MB")
