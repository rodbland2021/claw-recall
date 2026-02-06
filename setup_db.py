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
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    session_id UNINDEXED,
    message_id UNINDEXED,
    agent_id UNINDEXED,
    content='messages',
    content_rowid='id'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content, session_id, message_id, agent_id)
    SELECT NEW.id, NEW.content, NEW.session_id, NEW.id, 
           (SELECT agent_id FROM sessions WHERE id = NEW.session_id);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, session_id, message_id, agent_id)
    VALUES('delete', OLD.id, OLD.content, OLD.session_id, OLD.id, NULL);
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

-- Entities table for extracted mentions (future enhancement)
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    entity_type TEXT NOT NULL,  -- person, project, decision, url, etc.
    entity_value TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_value ON entities(entity_value);

-- Index tracking to avoid re-indexing
CREATE TABLE IF NOT EXISTS index_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT UNIQUE NOT NULL,
    file_size INTEGER,
    file_mtime DATETIME,
    indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    message_count INTEGER,
    status TEXT DEFAULT 'complete'
);

CREATE INDEX IF NOT EXISTS idx_index_log_file ON index_log(source_file);
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
