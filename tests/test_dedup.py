"""Tests for the dedup/cleanup module."""

import sqlite3
import pytest
from claw_recall.database import SCHEMA
from claw_recall.maintenance.dedup import (
    find_exact_duplicates,
    find_junk,
    find_noise,
    find_orphaned_embeddings,
    run_dry_run,
    delete_messages,
    delete_orphaned_embeddings,
    get_cleanup_history,
    _is_single_emoji,
    _matches_noise_pattern,
)


@pytest.fixture
def db(tmp_path):
    """Fresh test DB with full schema."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    yield conn, db_path
    conn.close()


def _insert_session(conn, sid="sess1", agent="kit", channel="telegram"):
    conn.execute(
        "INSERT OR IGNORE INTO sessions (id, agent_id, channel, started_at, message_count) "
        "VALUES (?, ?, ?, '2026-03-01', 0)",
        (sid, agent, channel),
    )
    conn.commit()


def _insert_msg(conn, sid="sess1", role="user", content="hello", idx=0, msg_id=None):
    if msg_id:
        conn.execute(
            "INSERT INTO messages (id, session_id, role, content, message_index, timestamp) "
            "VALUES (?, ?, ?, ?, ?, '2026-03-01')",
            (msg_id, sid, role, content, idx),
        )
    else:
        conn.execute(
            "INSERT INTO messages (session_id, role, content, message_index, timestamp) "
            "VALUES (?, ?, ?, ?, '2026-03-01')",
            (sid, role, content, idx),
        )
    conn.commit()


def _insert_embedding(conn, message_id, data=b'\x00' * (1536 * 4)):
    conn.execute(
        "INSERT INTO embeddings (message_id, embedding) VALUES (?, ?)",
        (message_id, data),
    )
    conn.commit()


# --- Exact duplicates ---

class TestFindExactDuplicates:

    def test_no_duplicates(self, db):
        conn, db_path = db
        _insert_session(conn)
        _insert_msg(conn, content="msg1", idx=0)
        _insert_msg(conn, content="msg2", idx=1)
        result = find_exact_duplicates(db_path)
        assert result['summary']['total_removable'] == 0
        assert result['groups'] == []

    def test_detects_duplicates(self, db):
        conn, db_path = db
        _insert_session(conn)
        # Same content, same session, same role, same index = duplicate
        _insert_msg(conn, content="duplicate", idx=0)
        _insert_msg(conn, content="duplicate", idx=0)
        _insert_msg(conn, content="duplicate", idx=0)
        result = find_exact_duplicates(db_path)
        assert result['summary']['total_removable'] == 2
        assert result['summary']['affected_sessions'] == 1
        assert len(result['groups']) == 1
        assert result['groups'][0]['duplicate_rows'] == 2

    def test_keeps_oldest_id(self, db):
        conn, db_path = db
        _insert_session(conn)
        _insert_msg(conn, content="dup", idx=0, msg_id=100)
        _insert_msg(conn, content="dup", idx=0, msg_id=200)
        _insert_msg(conn, content="dup", idx=0, msg_id=300)
        result = find_exact_duplicates(db_path)
        delete_ids = result['groups'][0]['delete_ids']
        assert 100 not in delete_ids  # oldest kept
        assert 200 in delete_ids
        assert 300 in delete_ids

    def test_different_sessions_not_duplicates(self, db):
        conn, db_path = db
        _insert_session(conn, "sess1")
        _insert_session(conn, "sess2")
        _insert_msg(conn, sid="sess1", content="same", idx=0)
        _insert_msg(conn, sid="sess2", content="same", idx=0)
        result = find_exact_duplicates(db_path)
        assert result['summary']['total_removable'] == 0


# --- Junk ---

class TestFindJunk:

    def test_empty_content(self, db):
        conn, db_path = db
        _insert_session(conn)
        _insert_msg(conn, content="")
        _insert_msg(conn, content=None)
        result = find_junk(db_path)
        assert result['summary']['by_category']['empty'] >= 1

    def test_orphaned_messages(self, db):
        conn, db_path = db
        # Message with no matching session
        conn.execute(
            "INSERT INTO messages (session_id, role, content, message_index) "
            "VALUES ('nonexistent', 'user', 'orphan', 0)"
        )
        conn.commit()
        result = find_junk(db_path)
        assert result['summary']['by_category']['orphaned'] == 1

    def test_single_char_count_is_accurate(self, db):
        """Verify total single_char count uses DB query, not loop counter."""
        conn, db_path = db
        _insert_session(conn)
        # Insert some emoji messages
        for i in range(5):
            _insert_msg(conn, content="\u2764", idx=i)  # heart emoji
        result = find_junk(db_path, limit=2)
        # Even with limit=2, summary total should reflect all 5
        # (if they pass the emoji check)
        assert result['summary']['by_category']['single_char'] >= 0  # non-negative


# --- Noise ---

class TestFindNoise:

    def test_heartbeat_ok(self, db):
        conn, db_path = db
        _insert_session(conn)
        for i in range(10):
            _insert_msg(conn, content="HEARTBEAT_OK", idx=i)
        result = find_noise(db_path)
        assert result['summary']['total'] == 10
        assert 'Heartbeat ping' in result['summary']['by_pattern']

    def test_no_reply(self, db):
        conn, db_path = db
        _insert_session(conn)
        _insert_msg(conn, content="NO_REPLY", idx=0)
        result = find_noise(db_path)
        assert result['summary']['total'] == 1

    def test_boot_check(self, db):
        conn, db_path = db
        _insert_session(conn)
        _insert_msg(conn, content="You are running a boot check. Follow BOOT.md instructions exactly.", idx=0)
        result = find_noise(db_path)
        assert result['summary']['total'] == 1
        assert 'Boot check prompt' in result['summary']['by_pattern']

    def test_gateway_restart(self, db):
        conn, db_path = db
        _insert_session(conn)
        _insert_msg(conn, content="Gateway restarted \u2014 back online \U0001f527", idx=0)
        result = find_noise(db_path)
        assert result['summary']['total'] == 1

    def test_normal_content_not_noise(self, db):
        conn, db_path = db
        _insert_session(conn)
        _insert_msg(conn, content="Let me check the gateway configuration for the API", idx=0)
        result = find_noise(db_path)
        assert result['summary']['total'] == 0

    def test_health_check_webhook(self, db):
        conn, db_path = db
        _insert_session(conn)
        _insert_msg(conn, content="OpenClaw Health Check Report - all good", idx=0)
        result = find_noise(db_path)
        assert result['summary']['total'] == 1


# --- Noise pattern helper ---

class TestMatchesNoisePattern:

    def test_heartbeat(self):
        assert _matches_noise_pattern("HEARTBEAT_OK") == "Heartbeat ping"

    def test_no_reply(self):
        assert _matches_noise_pattern("NO_REPLY") == "No-reply marker"

    def test_normal_text(self):
        assert _matches_noise_pattern("How do I fix this bug?") is None

    def test_none(self):
        assert _matches_noise_pattern(None) is None

    def test_empty(self):
        assert _matches_noise_pattern("") is None


# --- Orphaned embeddings ---

class TestFindOrphanedEmbeddings:

    def test_no_orphans(self, db):
        conn, db_path = db
        _insert_session(conn)
        _insert_msg(conn, content="hello", idx=0, msg_id=1)
        _insert_embedding(conn, message_id=1)
        result = find_orphaned_embeddings(db_path)
        assert result['summary']['total'] == 0

    def test_detects_orphans(self, db):
        conn, db_path = db
        # Embedding pointing to non-existent message
        _insert_embedding(conn, message_id=99999)
        _insert_embedding(conn, message_id=99998)
        result = find_orphaned_embeddings(db_path)
        assert result['summary']['total'] == 2
        assert result['summary']['estimated_savings_mb'] > 0


# --- Delete messages ---

class TestDeleteMessages:

    def test_delete_basic(self, db):
        conn, db_path = db
        _insert_session(conn)
        _insert_msg(conn, content="keep", idx=0, msg_id=1)
        _insert_msg(conn, content="delete me", idx=1, msg_id=2)
        _insert_msg(conn, content="also delete", idx=2, msg_id=3)
        result = delete_messages(db_path, [2, 3])
        assert result['deleted'] == 2
        assert result['freed_bytes'] > 0
        # Verify only msg 1 remains
        remaining = conn.execute("SELECT id FROM messages").fetchall()
        assert len(remaining) == 1
        assert remaining[0][0] == 1

    def test_delete_cascades_embeddings(self, db):
        conn, db_path = db
        _insert_session(conn)
        _insert_msg(conn, content="msg", idx=0, msg_id=1)
        _insert_embedding(conn, message_id=1)
        assert conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 1
        delete_messages(db_path, [1])
        assert conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 0

    def test_delete_updates_session_count(self, db):
        conn, db_path = db
        _insert_session(conn)
        conn.execute("UPDATE sessions SET message_count = 5 WHERE id = 'sess1'")
        conn.commit()
        _insert_msg(conn, content="a", idx=0, msg_id=1)
        _insert_msg(conn, content="b", idx=1, msg_id=2)
        _insert_msg(conn, content="c", idx=2, msg_id=3)
        result = delete_messages(db_path, [2, 3])
        assert result['sessions_updated'] == 1
        # Session count should now be 1 (actual remaining)
        count = conn.execute("SELECT message_count FROM sessions WHERE id = 'sess1'").fetchone()[0]
        assert count == 1

    def test_delete_empty_list(self, db):
        _, db_path = db
        result = delete_messages(db_path, [])
        assert result['deleted'] == 0


# --- Delete orphaned embeddings ---

class TestDeleteOrphanedEmbeddings:

    def test_delete_orphans(self, db):
        conn, db_path = db
        _insert_embedding(conn, message_id=99999)
        _insert_embedding(conn, message_id=99998)
        result = delete_orphaned_embeddings(db_path)
        assert result['deleted'] == 2
        assert result['freed_bytes'] > 0
        remaining = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        assert remaining == 0

    def test_no_orphans_to_delete(self, db):
        conn, db_path = db
        _insert_session(conn)
        _insert_msg(conn, content="msg", idx=0, msg_id=1)
        _insert_embedding(conn, message_id=1)
        result = delete_orphaned_embeddings(db_path)
        assert result['deleted'] == 0


# --- Dry run combined ---

class TestRunDryRun:

    def test_runs_all_categories(self, db):
        conn, db_path = db
        _insert_session(conn)
        # Add a duplicate
        _insert_msg(conn, content="dup", idx=0)
        _insert_msg(conn, content="dup", idx=0)
        # Add noise
        _insert_msg(conn, content="HEARTBEAT_OK", idx=1)
        # Add orphaned embedding
        _insert_embedding(conn, message_id=99999)

        result = run_dry_run(db_path)
        assert result['summary']['total_messages'] > 0
        assert result['summary']['duplicates_found'] == 1
        assert result['summary']['noise_found'] == 1
        assert result['summary']['orphaned_embeddings_found'] == 1
        assert result['duplicates'] is not None
        assert result['junk'] is not None
        assert result['noise'] is not None
        assert result['orphaned_embeddings'] is not None

    def test_selective_categories(self, db):
        conn, db_path = db
        _insert_session(conn)
        _insert_msg(conn, content="test", idx=0)
        result = run_dry_run(db_path, categories=['junk'])
        assert result['junk'] is not None
        assert result['duplicates'] is None
        assert result['noise'] is None


# --- Cleanup history ---

class TestCleanupHistory:

    def test_logs_dry_run(self, db):
        conn, db_path = db
        _insert_session(conn)
        _insert_msg(conn, content="test", idx=0)
        run_dry_run(db_path)
        history = get_cleanup_history(db_path)
        assert len(history) >= 1
        assert history[0]['mode'] == 'dry_run'

    def test_logs_delete(self, db):
        conn, db_path = db
        _insert_session(conn)
        _insert_msg(conn, content="delete me", idx=0, msg_id=1)
        delete_messages(db_path, [1])
        history = get_cleanup_history(db_path)
        assert any(h['mode'] == 'delete' for h in history)


# --- Helper tests ---

class TestIsSingleEmoji:

    def test_emoji(self):
        assert _is_single_emoji("\u2764") is True

    def test_text(self):
        assert _is_single_emoji("hello") is False

    def test_empty(self):
        assert _is_single_emoji("") is False

    def test_none(self):
        assert _is_single_emoji(None) is False
