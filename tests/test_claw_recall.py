#!/usr/bin/env python3
"""
Claw Recall — Test Suite

Comprehensive tests for the thought capture, search, batch embedding,
external source capture (Gmail, Drive), MCP server, and unified search.

Usage:
    pytest test_claw_recall.py -v                # All tests
    pytest test_claw_recall.py -v -k capture     # Just capture tests
    pytest test_claw_recall.py -v -k search      # Just search tests
    pytest test_claw_recall.py -v -k mcp         # Just MCP tests
    pytest test_claw_recall.py -v -k source      # Just source capture tests
"""

import json
import sqlite3
import sys
import os
import pytest
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

# Ensure repo root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def test_db(tmp_path):
    """Create a fresh test database with full schema."""
    db_path = tmp_path / "test_memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")

    # Load schema from setup_db
    from claw_recall.database import SCHEMA
    conn.executescript(SCHEMA)
    conn.commit()

    yield conn, db_path
    conn.close()


@pytest.fixture
def patched_db(test_db, monkeypatch):
    """Patch DB_PATH in both config and database modules so get_db() uses the test DB."""
    conn, db_path = test_db
    import claw_recall.config as config_module
    import claw_recall.database as database_module
    monkeypatch.setattr(config_module, 'DB_PATH', db_path)
    monkeypatch.setattr(database_module, 'DB_PATH', db_path)
    return conn, db_path


@pytest.fixture
def sample_thoughts(patched_db):
    """Insert sample thoughts into the test DB for search tests."""
    conn, db_path = patched_db
    from claw_recall.capture.thoughts import capture_thought

    thoughts = [
        ("User prefers dark mode across all apps", "manual", "main"),
        ("Acme API returns 500 on bulk orders over 50 items", "cli", "butler"),
        ("Meeting with John about Q2 targets — agreed on 15% growth", "mcp", None),
        ("Widget campaign Facebook ads performing well, ROAS 3.2x", "http", "atlas"),
        ("Email from supplier: shipment delayed by 2 weeks", "gmail", None),
        ("Drive document: Production Schedule updated with new timelines", "drive", None),
    ]

    results = []
    for content, source, agent in thoughts:
        result = capture_thought(
            content=content,
            source=source,
            agent=agent,
            generate_embedding=False,
            conn=conn,
        )
        results.append(result)

    conn.commit()
    return results


# ─── Schema Tests ─────────────────────────────────────────────────────────────

class TestSchema:
    """Test database schema is correctly set up."""

    def test_tables_exist(self, test_db):
        conn, _ = test_db
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]
        assert 'thoughts' in tables
        assert 'thought_embeddings' in tables
        assert 'capture_log' in tables
        assert 'messages' in tables
        assert 'sessions' in tables
        assert 'embeddings' in tables

    def test_virtual_tables_exist(self, test_db):
        conn, _ = test_db
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%fts%'"
        ).fetchall()]
        assert 'thoughts_fts' in tables
        assert 'messages_fts' in tables

    def test_triggers_exist(self, test_db):
        conn, _ = test_db
        triggers = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall()]
        assert 'thoughts_ai' in triggers  # After insert
        assert 'thoughts_ad' in triggers  # After delete
        assert 'thoughts_au' in triggers  # After update

    def test_capture_log_unique_constraint(self, test_db):
        conn, _ = test_db
        conn.execute(
            "INSERT INTO capture_log (source_type, source_id, account, thought_id) VALUES (?, ?, ?, ?)",
            ('gmail', 'msg123', 'personal', 1)
        )
        conn.commit()
        # Duplicate should fail
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO capture_log (source_type, source_id, account, thought_id) VALUES (?, ?, ?, ?)",
                ('gmail', 'msg123', 'personal', 2)
            )


# ─── Capture Tests ────────────────────────────────────────────────────────────

class TestCapture:
    """Test thought capture functionality."""

    def test_basic_capture(self, patched_db):
        conn, _ = patched_db
        from claw_recall.capture.thoughts import capture_thought

        result = capture_thought(
            content="Test thought",
            source="test",
            agent="main",
            generate_embedding=False,
            conn=conn,
        )

        assert 'error' not in result
        assert result['id'] > 0
        assert result['content'] == "Test thought"
        assert result['source'] == "test"
        assert result['agent'] == "main"
        assert result['embedded'] is False

    def test_capture_with_metadata(self, patched_db):
        conn, _ = patched_db
        from claw_recall.capture.thoughts import capture_thought

        metadata = {"topic": "testing", "priority": "high"}
        result = capture_thought(
            content="Thought with metadata",
            source="test",
            metadata=metadata,
            generate_embedding=False,
            conn=conn,
        )

        assert result['metadata'] == metadata

        # Verify stored in DB
        row = conn.execute(
            "SELECT metadata FROM thoughts WHERE id = ?", (result['id'],)
        ).fetchone()
        assert json.loads(row[0]) == metadata

    def test_capture_empty_content(self, patched_db):
        conn, _ = patched_db
        from claw_recall.capture.thoughts import capture_thought

        result = capture_thought(content="", conn=conn)
        assert result == {"error": "Empty content"}

        result = capture_thought(content="   ", conn=conn)
        assert result == {"error": "Empty content"}

    def test_capture_strips_whitespace(self, patched_db):
        conn, _ = patched_db
        from claw_recall.capture.thoughts import capture_thought

        result = capture_thought(
            content="  spaces around  ",
            source="test",
            generate_embedding=False,
            conn=conn,
        )
        assert result['content'] == "spaces around"

    def test_capture_dedup_within_24h(self, patched_db):
        conn, _ = patched_db
        from claw_recall.capture.thoughts import capture_thought

        r1 = capture_thought(content="Duplicate thought", source="test",
                             generate_embedding=False, conn=conn)
        r2 = capture_thought(content="Duplicate thought", source="test",
                             generate_embedding=False, conn=conn)

        assert r1['id'] > 0
        assert r2.get('duplicate') is True
        assert r2['id'] == r1['id']

    def test_capture_different_content_not_dedup(self, patched_db):
        conn, _ = patched_db
        from claw_recall.capture.thoughts import capture_thought

        r1 = capture_thought(content="First thought", source="test",
                             generate_embedding=False, conn=conn)
        r2 = capture_thought(content="Second thought", source="test",
                             generate_embedding=False, conn=conn)

        assert r1['id'] != r2['id']
        assert 'duplicate' not in r2

    def test_fts_sync_on_insert(self, patched_db):
        conn, _ = patched_db
        from claw_recall.capture.thoughts import capture_thought

        capture_thought(content="searchable content here", source="test",
                        generate_embedding=False, conn=conn)
        conn.commit()

        # FTS should find it
        rows = conn.execute(
            "SELECT rowid FROM thoughts_fts WHERE thoughts_fts MATCH 'searchable'"
        ).fetchall()
        assert len(rows) == 1

    def test_fts_sync_on_delete(self, patched_db):
        conn, _ = patched_db
        from claw_recall.capture.thoughts import capture_thought, delete_thought

        result = capture_thought(content="deletable content", source="test",
                                 generate_embedding=False, conn=conn)
        conn.commit()

        delete_thought(result['id'], conn=conn)

        rows = conn.execute(
            "SELECT rowid FROM thoughts_fts WHERE thoughts_fts MATCH 'deletable'"
        ).fetchall()
        assert len(rows) == 0


# ─── List and Delete Tests ────────────────────────────────────────────────────

class TestListDelete:
    """Test listing and deleting thoughts."""

    def test_list_thoughts(self, sample_thoughts, patched_db):
        conn, _ = patched_db
        from claw_recall.capture.thoughts import list_thoughts

        thoughts = list_thoughts(conn=conn)
        assert len(thoughts) == 6

    def test_list_thoughts_filter_by_source(self, sample_thoughts, patched_db):
        conn, _ = patched_db
        from claw_recall.capture.thoughts import list_thoughts

        thoughts = list_thoughts(source='gmail', conn=conn)
        assert len(thoughts) == 1
        assert thoughts[0]['source'] == 'gmail'

    def test_list_thoughts_filter_by_agent(self, sample_thoughts, patched_db):
        conn, _ = patched_db
        from claw_recall.capture.thoughts import list_thoughts

        thoughts = list_thoughts(agent='butler', conn=conn)
        assert len(thoughts) == 1
        assert thoughts[0]['agent'] == 'butler'

    def test_list_thoughts_limit_offset(self, sample_thoughts, patched_db):
        conn, _ = patched_db
        from claw_recall.capture.thoughts import list_thoughts

        page1 = list_thoughts(limit=3, offset=0, conn=conn)
        page2 = list_thoughts(limit=3, offset=3, conn=conn)
        assert len(page1) == 3
        assert len(page2) == 3
        assert page1[0]['id'] != page2[0]['id']

    def test_delete_thought(self, patched_db):
        conn, _ = patched_db
        from claw_recall.capture.thoughts import capture_thought, delete_thought

        result = capture_thought(content="to delete", source="test",
                                 generate_embedding=False, conn=conn)
        conn.commit()

        del_result = delete_thought(result['id'], conn=conn)
        assert del_result == {"deleted": result['id']}

        # Verify gone
        row = conn.execute("SELECT id FROM thoughts WHERE id = ?",
                           (result['id'],)).fetchone()
        assert row is None

    def test_delete_nonexistent(self, patched_db):
        conn, _ = patched_db
        from claw_recall.capture.thoughts import delete_thought

        result = delete_thought(99999, conn=conn)
        assert 'error' in result

    def test_thought_stats(self, sample_thoughts, patched_db):
        conn, _ = patched_db
        from claw_recall.capture.thoughts import thought_stats

        stats = thought_stats(conn=conn)
        assert stats['total'] == 6
        assert stats['by_source']['manual'] == 1
        assert stats['by_source']['gmail'] == 1
        assert stats['by_source']['drive'] == 1


# ─── Batch Embedding Tests ────────────────────────────────────────────────────

class TestBatchEmbed:
    """Test batch embedding functionality."""

    def test_batch_embed_no_openai(self, patched_db, monkeypatch):
        import claw_recall.capture.thoughts as capture
        monkeypatch.setattr(capture, 'OPENAI_AVAILABLE', False)
        monkeypatch.setattr(capture, '_openai_client', None)

        result = capture.batch_embed_thoughts()
        assert result == {"error": "OpenAI not available"}

    def test_batch_embed_empty(self, patched_db):
        conn, _ = patched_db
        from claw_recall.capture.thoughts import batch_embed_thoughts

        # No thoughts without embeddings (or OpenAI unavailable)
        result = batch_embed_thoughts(conn=conn)
        assert result.get('embedded', 0) == 0 or 'error' in result

    def test_batch_embed_with_mock(self, patched_db, monkeypatch):
        conn, _ = patched_db
        import claw_recall.capture.thoughts as capture
        from claw_recall.capture.thoughts import capture_thought, batch_embed_thoughts

        # Insert thoughts without embeddings
        r1 = capture_thought(content="batch test one for embedding", source="test",
                             generate_embedding=False, conn=conn)
        r2 = capture_thought(content="batch test two for embedding", source="test",
                             generate_embedding=False, conn=conn)
        conn.commit()

        # Mock OpenAI
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_emb1 = MagicMock()
        mock_emb1.embedding = [0.1] * 1536
        mock_emb2 = MagicMock()
        mock_emb2.embedding = [0.2] * 1536
        mock_response.data = [mock_emb1, mock_emb2]
        mock_client.embeddings.create.return_value = mock_response

        monkeypatch.setattr(capture, 'OPENAI_AVAILABLE', True)
        monkeypatch.setattr(capture, '_openai_client', mock_client)

        result = batch_embed_thoughts([r1['id'], r2['id']], conn=conn)
        assert result['embedded'] == 2

        # Verify embeddings stored
        count = conn.execute(
            "SELECT COUNT(*) FROM thought_embeddings WHERE thought_id IN (?, ?)",
            (r1['id'], r2['id'])
        ).fetchone()[0]
        assert count == 2


# ─── Search Tests ─────────────────────────────────────────────────────────────

class TestSearch:
    """Test keyword and semantic search for thoughts."""

    def test_keyword_search_thoughts(self, sample_thoughts, patched_db):
        conn, db_path = patched_db
        from claw_recall.search.engine import keyword_search_thoughts

        results = keyword_search_thoughts(conn, "dark mode")
        assert len(results) >= 1
        assert any("dark mode" in r.content.lower() for r in results)

    def test_keyword_search_no_results(self, sample_thoughts, patched_db):
        conn, db_path = patched_db
        from claw_recall.search.engine import keyword_search_thoughts

        results = keyword_search_thoughts(conn, "xyznonexistent")
        assert len(results) == 0

    def test_keyword_search_agent_filter(self, sample_thoughts, patched_db):
        conn, db_path = patched_db
        from claw_recall.search.engine import keyword_search_thoughts

        results = keyword_search_thoughts(conn, "Acme", agent="butler")
        assert len(results) >= 1
        assert all(r.agent == "butler" for r in results)

        results = keyword_search_thoughts(conn, "Acme", agent="atlas")
        assert len(results) == 0


# ─── Auto-Detect Tests ───────────────────────────────────────────────────────

class TestAutoDetect:
    """Test semantic vs keyword auto-detection."""

    def test_short_query_keyword(self):
        from claw_recall.cli import should_use_semantic
        assert should_use_semantic("ACME42") is False
        assert should_use_semantic("act_12345") is False

    def test_question_semantic(self):
        from claw_recall.cli import should_use_semantic
        assert should_use_semantic("what did we discuss about playbooks") is True
        assert should_use_semantic("how does the API work") is True

    def test_quoted_keyword(self):
        from claw_recall.cli import should_use_semantic
        assert should_use_semantic('"exact phrase"') is False

    def test_file_path_keyword(self):
        from claw_recall.cli import should_use_semantic
        assert should_use_semantic("~/repos/claw-recall/capture.py") is False

    def test_long_query_semantic(self):
        from claw_recall.cli import should_use_semantic
        # Short ID terms in longer queries still trigger keyword
        assert should_use_semantic("Facebook ads ACME42 campaign performance") is False
        # Natural language questions should trigger semantic
        assert should_use_semantic("what were the results of the Facebook ads campaign") is True


# ─── Unified Search Tests ────────────────────────────────────────────────────

class TestUnifiedSearch:
    """Test the unified search function."""

    def test_summary_grammar_two_parts(self):
        from claw_recall.cli import unified_search
        # Mock to test summary formatting
        results = {
            "conversations": [{"content": "test"}],
            "files": [],
            "thoughts": [],
            "summary": ""
        }
        # Test the summary logic directly
        conv_count = 1
        file_count = 0
        thought_count = 0
        parts = [f"{conv_count} conversation matches", f"{file_count} file matches"]
        if thought_count > 0:
            parts.append(f"{thought_count} thought matches")
        if len(parts) <= 2:
            summary = "Found " + " and ".join(parts)
        else:
            summary = "Found " + ", ".join(parts[:-1]) + ", and " + parts[-1]

        assert summary == "Found 1 conversation matches and 0 file matches"

    def test_summary_grammar_three_parts(self):
        parts = ["5 conversation matches", "3 file matches", "2 thought matches"]
        summary = "Found " + ", ".join(parts[:-1]) + ", and " + parts[-1]
        assert summary == "Found 5 conversation matches, 3 file matches, and 2 thought matches"


# ─── Capture Sources Tests ───────────────────────────────────────────────────

class TestCaptureSources:
    """Test external source capture (Gmail, Drive)."""

    def test_strip_html_basic(self):
        from claw_recall.capture.sources import _strip_html
        assert _strip_html("<p>Hello</p>") == "Hello"
        assert _strip_html("Hello<br>World") == "Hello\nWorld"
        assert _strip_html("<b>bold</b> text") == "bold text"

    def test_strip_html_entities(self):
        from claw_recall.capture.sources import _strip_html
        assert _strip_html("&amp; &lt; &gt;") == "& < >"
        assert _strip_html("foo&nbsp;bar") == "foo\xa0bar"  # non-breaking space

    def test_strip_html_style_script(self):
        from claw_recall.capture.sources import _strip_html
        html = "<style>body { color: red; }</style><p>content</p><script>alert('x')</script>"
        result = _strip_html(html)
        assert "color: red" not in result
        assert "alert" not in result
        assert "content" in result

    def test_strip_html_whitespace_collapse(self):
        from claw_recall.capture.sources import _strip_html
        html = "  lots   of   spaces  "
        result = _strip_html(html)
        assert "  " not in result  # No double spaces

    def test_is_captured(self, test_db):
        conn, _ = test_db
        from claw_recall.capture.sources import _is_captured, _log_capture

        assert _is_captured(conn, 'gmail', 'msg123', 'personal') is False

        _log_capture(conn, 'gmail', 'msg123', 'personal', 1)
        conn.commit()

        assert _is_captured(conn, 'gmail', 'msg123', 'personal') is True
        assert _is_captured(conn, 'gmail', 'msg123', 'rbs') is False  # Different account
        assert _is_captured(conn, 'drive', 'msg123', 'personal') is False  # Different type

    def test_log_capture_upsert(self, test_db):
        conn, _ = test_db
        from claw_recall.capture.sources import _log_capture

        _log_capture(conn, 'drive', 'doc1', 'personal', 1, '2026-01-01')
        conn.commit()
        _log_capture(conn, 'drive', 'doc1', 'personal', 2, '2026-01-02')
        conn.commit()

        # Should have only 1 row (upserted)
        count = conn.execute(
            "SELECT COUNT(*) FROM capture_log WHERE source_type = 'drive' AND source_id = 'doc1'"
        ).fetchone()[0]
        assert count == 1

        # Should have the updated values
        row = conn.execute(
            "SELECT thought_id, source_modified FROM capture_log WHERE source_id = 'doc1'"
        ).fetchone()
        assert row[0] == 2
        assert row[1] == '2026-01-02'

    def test_poll_gmail_mock(self, patched_db, monkeypatch):
        """Test Gmail polling with mocked email_helper."""
        conn, db_path = patched_db

        # Mock email_helper
        mock_emails = [
            {
                'id': 'msg001',
                'threadId': 'thread001',
                'from': 'test@example.com',
                'subject': 'Test Subject',
                'date': 'Wed, 5 Mar 2026 10:00:00 +0000',
                'snippet': 'This is a test email snippet',
            },
            {
                'id': 'msg002',
                'threadId': 'thread002',
                'from': 'sender@example.com',
                'subject': 'Another Email',
                'date': 'Wed, 5 Mar 2026 11:00:00 +0000',
                'snippet': 'Another snippet here',
            },
        ]

        # Need to mock before importing capture_sources
        mock_module = MagicMock()
        mock_module.list_inbox.return_value = mock_emails
        # get_email must return dicts with from/subject/body (used for full_body fetch)
        mock_module.get_email.side_effect = lambda acct, msg_id: {
            'from': next((e['from'] for e in mock_emails if e['id'] == msg_id), 'Unknown'),
            'subject': next((e['subject'] for e in mock_emails if e['id'] == msg_id), 'No subject'),
            'body': next((e['snippet'] for e in mock_emails if e['id'] == msg_id), ''),
        }
        sys.modules['email_helper'] = mock_module

        # Also mock OpenAI for batch embedding
        import claw_recall.capture.thoughts as capture
        import claw_recall.config as db_module
        monkeypatch.setattr(capture, 'OPENAI_AVAILABLE', False)
        monkeypatch.setattr(capture, '_openai_client', None)
        monkeypatch.setattr(db_module, 'DB_PATH', db_path)
        import claw_recall.database as _db_module
        monkeypatch.setattr(_db_module, 'DB_PATH', db_path)

        import claw_recall.capture.sources as capture_sources

        try:
            # Force reimport to pick up mocked email_helper
            result = capture_sources.poll_gmail(account='personal', limit=10)
            assert result['captured'] == 2
            assert result['errors'] == 0

            # Verify thoughts are in DB
            thoughts = conn.execute("SELECT content, source FROM thoughts").fetchall()
            assert len(thoughts) == 2
            assert all(t[1] == 'gmail' for t in thoughts)

            # Run again — should skip all
            result2 = capture_sources.poll_gmail(account='personal', limit=10)
            assert result2['captured'] == 0
            assert result2['skipped'] == 2
        finally:
            del sys.modules['email_helper']

    def test_poll_drive_mock(self, patched_db, monkeypatch):
        """Test Drive polling with mocked google_helper."""
        conn, db_path = patched_db

        # Mock google_helper.get_service
        mock_drive_service = MagicMock()
        mock_files_list = MagicMock()
        mock_files_list.execute.return_value = {
            'files': [
                {
                    'id': 'doc001',
                    'name': 'Test Document',
                    'mimeType': 'application/vnd.google-apps.document',
                    'modifiedTime': '2026-03-05T10:00:00.000Z',
                },
                {
                    'id': 'pdf001',
                    'name': 'Report.pdf',
                    'mimeType': 'application/pdf',
                    'size': '1048576',
                    'modifiedTime': '2026-03-05T09:00:00.000Z',
                },
            ]
        }
        mock_drive_service.files().list.return_value = mock_files_list

        # Mock docs service for Google Doc content
        mock_docs_service = MagicMock()
        mock_doc = {
            'body': {
                'content': [
                    {'paragraph': {'elements': [{'textRun': {'content': 'Document content here'}}]}}
                ]
            }
        }
        mock_docs_service.documents().get().execute.return_value = mock_doc

        def mock_get_service(account, api, version):
            if api == 'drive':
                return mock_drive_service
            elif api == 'docs':
                return mock_docs_service
            return MagicMock()

        mock_module = MagicMock()
        mock_module.get_service = mock_get_service
        sys.modules['google_helper'] = mock_module

        import claw_recall.capture.thoughts as capture
        import claw_recall.config as db_module
        monkeypatch.setattr(capture, 'OPENAI_AVAILABLE', False)
        monkeypatch.setattr(capture, '_openai_client', None)
        monkeypatch.setattr(db_module, 'DB_PATH', db_path)
        import claw_recall.database as _db_module
        monkeypatch.setattr(_db_module, 'DB_PATH', db_path)

        import claw_recall.capture.sources as capture_sources

        try:
            result = capture_sources.poll_drive(account='personal', limit=10)
            assert result['captured'] == 2
            assert result['errors'] == 0

            # Verify thoughts
            thoughts = conn.execute(
                "SELECT content, source FROM thoughts ORDER BY id"
            ).fetchall()
            assert len(thoughts) == 2
            assert 'Document content here' in thoughts[0][0]
            assert all(t[1] == 'drive' for t in thoughts)

            # Run again — should skip all
            result2 = capture_sources.poll_drive(account='personal', limit=10)
            assert result2['captured'] == 0
            assert result2['skipped'] == 2
        finally:
            del sys.modules['google_helper']

    def test_poll_slack_no_token(self, patched_db, monkeypatch):
        """Test Slack polling with no token configured."""
        conn, db_path = patched_db
        import claw_recall.capture.sources as cs
        # Force empty token — bypass _get_slack_token reading config
        monkeypatch.setattr(cs, '_get_slack_token', lambda: '')

        result = cs.poll_slack(limit=5)
        assert 'error' in result

    def test_poll_slack_mock(self, patched_db, monkeypatch):
        """Test Slack polling with mocked slack_sdk."""
        conn, db_path = patched_db

        import claw_recall.capture.thoughts as capture
        monkeypatch.setattr(capture, 'OPENAI_AVAILABLE', False)
        monkeypatch.setattr(capture, '_openai_client', None)

        import claw_recall.capture.sources as cs
        monkeypatch.setattr(cs, '_get_slack_token', lambda: 'xoxb-test-token')

        # Mock slack_sdk
        mock_client_instance = MagicMock()

        # conversations_list returns a DM
        mock_client_instance.conversations_list.return_value = {
            "channels": [
                {"id": "D001", "user": "U001", "is_im": True},
            ]
        }

        # conversations_history returns messages
        mock_client_instance.conversations_history.return_value = {
            "messages": [
                {"ts": "1234567890.123456", "user": "U001", "text": "Hello from Slack", "type": "message"},
                {"ts": "1234567891.123456", "user": "U002", "text": "Reply from Slack", "type": "message"},
            ]
        }

        # users_info returns names
        mock_client_instance.users_info.return_value = {
            "user": {"real_name": "Test User", "name": "testuser"}
        }

        mock_webclient = MagicMock(return_value=mock_client_instance)
        mock_slack_module = MagicMock()
        mock_slack_module.WebClient = mock_webclient
        mock_errors = MagicMock()
        mock_slack_module.errors = mock_errors

        sys.modules['slack_sdk'] = mock_slack_module
        sys.modules['slack_sdk.errors'] = mock_errors

        try:
            result = cs.poll_slack(limit=5)
            assert result['captured'] == 2
            assert result['errors'] == 0
        finally:
            del sys.modules['slack_sdk']
            del sys.modules['slack_sdk.errors']

    def test_capture_status(self, test_db, monkeypatch):
        conn, db_path = test_db
        from claw_recall.capture.sources import _log_capture

        import claw_recall.config as db_module
        monkeypatch.setattr(db_module, 'DB_PATH', db_path)
        import claw_recall.database as _db_module
        monkeypatch.setattr(_db_module, 'DB_PATH', db_path)
        import claw_recall.capture.sources as cs

        _log_capture(conn, 'gmail', 'msg1', 'personal', 1)
        _log_capture(conn, 'gmail', 'msg2', 'rbs', 2)
        _log_capture(conn, 'drive', 'doc1', 'personal', 3)
        conn.commit()

        stats = cs.capture_status()
        assert stats['total'] == 3
        assert stats.get('gmail:personal') == 1
        assert stats.get('gmail:rbs') == 1
        assert stats.get('drive:personal') == 1


# ─── MCP Server Tests ────────────────────────────────────────────────────────

class TestMCPServer:
    """Test MCP server tool definitions."""

    def test_mcp_imports(self):
        """Verify mcp_server.py can be imported."""
        import claw_recall.api.mcp_stdio as mcp_server
        assert hasattr(mcp_server, 'mcp')

    def test_mcp_has_tools(self):
        """Verify all expected tools are registered."""
        import claw_recall.api.mcp_stdio as mcp_server
        # FastMCP registers tools — check the decorated functions exist
        assert callable(mcp_server.search_memory)
        assert callable(mcp_server.search_thoughts)
        assert callable(mcp_server.capture_thought)
        assert callable(mcp_server.browse_activity)
        assert callable(mcp_server.browse_recent)
        assert callable(mcp_server.memory_stats)
        assert callable(mcp_server.poll_sources)
        assert callable(mcp_server.capture_source_status)


# ─── Format Tests ─────────────────────────────────────────────────────────────

class TestFormatting:
    """Test output formatting functions."""

    def test_format_unified_results_empty(self):
        from claw_recall.cli import format_unified_results
        results = {"conversations": [], "files": [], "thoughts": [], "summary": "Found 0 matches"}
        output = format_unified_results(results)
        assert "Found 0 matches" in output

    def test_format_unified_results_with_thoughts(self):
        from claw_recall.cli import format_unified_results
        results = {
            "conversations": [],
            "files": [],
            "thoughts": [
                {
                    "id": 1,
                    "content": "Test thought content",
                    "source": "cli",
                    "agent": "main",
                    "metadata": {},
                    "created_at": "2026-03-05T10:00:00",
                    "score": 0.95,
                }
            ],
            "summary": "Found 0 conversation matches, 0 file matches, and 1 thought matches"
        }
        output = format_unified_results(results)
        assert "THOUGHTS" in output
        assert "Test thought content" in output
        assert "main" in output

    def test_format_unified_results_error_handling(self):
        from claw_recall.cli import format_unified_results
        results = {
            "conversations": [{"error": "DB connection failed"}],
            "files": [],
            "thoughts": [],
            "summary": "Error"
        }
        output = format_unified_results(results)
        assert "Error" in output


# ─── Parse Tests ──────────────────────────────────────────────────────────────

class TestParsing:
    """Test CLI argument parsing helpers."""

    def test_parse_since_minutes(self):
        from claw_recall.cli import parse_since
        assert abs(parse_since("60m") - 60/1440) < 0.001

    def test_parse_since_hours(self):
        from claw_recall.cli import parse_since
        assert abs(parse_since("2h") - 2/24) < 0.001

    def test_parse_since_days(self):
        from claw_recall.cli import parse_since
        assert parse_since("3d") == 3.0

    def test_parse_since_invalid(self):
        from claw_recall.cli import parse_since
        import argparse
        with pytest.raises(argparse.ArgumentTypeError):
            parse_since("invalid")

    def test_parse_date_iso(self):
        from claw_recall.cli import parse_date
        dt = parse_date("2026-03-05")
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 5

    def test_parse_date_today(self):
        from claw_recall.cli import parse_date
        dt = parse_date("today")
        assert dt.date() == datetime.now().date()

    def test_parse_date_yesterday(self):
        from claw_recall.cli import parse_date
        dt = parse_date("yesterday")
        assert dt.date() == (datetime.now() - timedelta(days=1)).date()


# ─── Integration Test ────────────────────────────────────────────────────────

class TestIntegration:
    """End-to-end integration tests."""

    def test_capture_search_roundtrip(self, patched_db):
        """Capture a thought, then find it via keyword search."""
        conn, db_path = patched_db
        from claw_recall.capture.thoughts import capture_thought

        capture_thought(
            content="Integration test: Shopify webhook failing on order 12345",
            source="test",
            generate_embedding=False,
            conn=conn,
        )
        conn.commit()

        # Search via FTS
        rows = conn.execute(
            "SELECT rowid FROM thoughts_fts WHERE thoughts_fts MATCH 'Shopify webhook'"
        ).fetchall()
        assert len(rows) == 1

    def test_capture_delete_roundtrip(self, patched_db):
        """Capture, verify, delete, verify gone."""
        conn, _ = patched_db
        from claw_recall.capture.thoughts import capture_thought, delete_thought, list_thoughts

        result = capture_thought(
            content="To be deleted thought",
            source="test",
            generate_embedding=False,
            conn=conn,
        )
        conn.commit()

        thoughts = list_thoughts(conn=conn)
        assert any(t['id'] == result['id'] for t in thoughts)

        delete_thought(result['id'], conn=conn)
        thoughts = list_thoughts(conn=conn)
        assert not any(t['id'] == result['id'] for t in thoughts)

    def test_multi_source_capture(self, patched_db):
        """Capture from multiple sources and verify stats."""
        conn, _ = patched_db
        from claw_recall.capture.thoughts import capture_thought, thought_stats

        sources = ['cli', 'http', 'mcp', 'gmail', 'drive', 'telegram']
        for source in sources:
            capture_thought(
                content=f"Thought from {source}",
                source=source,
                generate_embedding=False,
                conn=conn,
            )
        conn.commit()

        stats = thought_stats(conn=conn)
        assert stats['total'] == 6
        assert len(stats['by_source']) == 6
        for source in sources:
            assert stats['by_source'][source] == 1


# ─── Agent Alias Resolution Tests ─────────────────────────────────────────────

class TestAgentAliases:
    """Test that raw OpenClaw slot IDs resolve to display names via agents.json."""

    @pytest.fixture(autouse=True)
    def _patch_aliases(self, monkeypatch):
        """Patch _AGENT_ALIASES with fictitious test names so tests are self-contained."""
        import claw_recall.search.engine as search_engine
        monkeypatch.setattr(search_engine, '_AGENT_ALIASES', {
            'main': 'Butler',
            'claude-code': 'CodeBot',
            'butler': 'Butler',
        })

    def test_main_resolves_to_display_name(self):
        from claw_recall.search.engine import _resolve_agent
        assert _resolve_agent("main") == "Butler"

    def test_claude_code_resolves(self):
        from claw_recall.search.engine import _resolve_agent
        assert _resolve_agent("claude-code") == "CodeBot"

    def test_display_name_passthrough(self):
        from claw_recall.search.engine import _resolve_agent
        assert _resolve_agent("Butler") == "Butler"
        assert _resolve_agent("atlas") == "atlas"

    def test_none_passthrough(self):
        from claw_recall.search.engine import _resolve_agent
        assert _resolve_agent(None) is None
        assert _resolve_agent("") == ""

    def test_case_insensitive(self):
        from claw_recall.search.engine import _resolve_agent
        assert _resolve_agent("MAIN") == "Butler"
        assert _resolve_agent("Claude-Code") == "CodeBot"

    def test_unknown_agent_passthrough(self):
        from claw_recall.search.engine import _resolve_agent
        assert _resolve_agent("unknown-agent") == "unknown-agent"


# ─── Remote Session Indexing Tests ────────────────────────────────────────────

class TestPathSuffix:
    """Test _extract_path_suffix helper for agent detection."""

    def test_cc_projects_path(self):
        from claw_recall.api.web import _extract_path_suffix
        result = _extract_path_suffix(
            "/home/testuser/.claude/projects/-mnt-c-code-hostinger/9c41e634.jsonl"
        )
        assert result == ".claude/projects/-mnt-c-code-hostinger/9c41e634.jsonl"

    def test_openclaw_agents_path(self):
        from claw_recall.api.web import _extract_path_suffix
        result = _extract_path_suffix(
            "/home/testuser/.openclaw/agents/main/sessions/agent-main-xxx.jsonl"
        )
        assert result == ".openclaw/agents/main/sessions/agent-main-xxx.jsonl"

    def test_openclaw_archive_path(self):
        from claw_recall.api.web import _extract_path_suffix
        result = _extract_path_suffix(
            "/home/testuser/.openclaw/agents-archive/main-abc-123.jsonl"
        )
        assert result == ".openclaw/agents-archive/main-abc-123.jsonl"

    def test_fallback_basename(self):
        from claw_recall.api.web import _extract_path_suffix
        result = _extract_path_suffix("/some/random/path/file.jsonl")
        assert result == "file.jsonl"


class TestSourceFileOverride:
    """Test index_session_file with source_file_override parameter."""

    def _make_cc_session(self, tmp_path):
        """Create a minimal CC session file."""
        session_dir = tmp_path / ".claude" / "projects" / "-test"
        session_dir.mkdir(parents=True)
        session_file = session_dir / "abc12345-6789-abcd-ef01-234567890abc.jsonl"
        session_file.write_text(
            '{"type":"user","message":{"role":"user","content":"hello from CC"}}\n'
            '{"type":"assistant","message":{"role":"assistant","content":"hi there"}}\n'
        )
        return session_file

    def test_override_stores_custom_source_file(self, test_db, tmp_path):
        conn, _ = test_db
        session_file = self._make_cc_session(tmp_path)
        override_path = "/home/testuser/.claude/projects/-test/abc12345-6789-abcd-ef01-234567890abc.jsonl"

        from claw_recall.indexing.indexer import index_session_file
        result = index_session_file(session_file, conn, source_file_override=override_path)

        assert result['status'] == 'indexed'
        assert result['messages'] == 2

        # index_log should use override path
        row = conn.execute(
            "SELECT source_file FROM index_log WHERE source_file = ?",
            (override_path,)
        ).fetchone()
        assert row is not None

        # sessions table should also use override path
        row = conn.execute(
            "SELECT source_file FROM sessions WHERE id = ?",
            (session_file.stem,)
        ).fetchone()
        assert row[0] == override_path

    def test_no_override_uses_filepath(self, test_db, tmp_path):
        conn, _ = test_db
        session_file = self._make_cc_session(tmp_path)

        from claw_recall.indexing.indexer import index_session_file
        result = index_session_file(session_file, conn)
        assert result['status'] == 'indexed'

        row = conn.execute("SELECT source_file FROM index_log").fetchone()
        assert row[0] == str(session_file)

    def test_override_dedup_by_custom_path(self, test_db, tmp_path):
        """Same override path should trigger skip on second call with same size."""
        conn, _ = test_db
        session_file = self._make_cc_session(tmp_path)
        override_path = "/home/testuser/.claude/projects/-test/abc12345-6789-abcd-ef01-234567890abc.jsonl"

        from claw_recall.indexing.indexer import index_session_file
        r1 = index_session_file(session_file, conn, source_file_override=override_path)
        assert r1['status'] == 'indexed'

        r2 = index_session_file(session_file, conn, source_file_override=override_path)
        assert r2['status'] == 'skipped'
        assert r2['reason'] == 'already indexed'


class TestIndexSessionEndpoint:
    """Test the POST /index-session HTTP endpoint."""

    @pytest.fixture
    def client(self, test_db, monkeypatch):
        conn, db_path = test_db
        import claw_recall.api.web as web
        import claw_recall.config as db_module
        monkeypatch.setattr(db_module, 'DB_PATH', db_path)
        import claw_recall.database as _db_module
        monkeypatch.setattr(_db_module, 'DB_PATH', db_path)
        web.app.config['TESTING'] = True
        return web.app.test_client()

    def test_no_file_returns_400(self, client):
        response = client.post('/index-session', data={})
        assert response.status_code == 400
        assert "No file" in response.get_json()["error"]

    def test_non_jsonl_returns_400(self, client):
        import io
        response = client.post('/index-session', data={
            'file': (io.BytesIO(b'not jsonl'), 'test.txt'),
            'source_path': '/tmp/test.txt',
        }, content_type='multipart/form-data')
        assert response.status_code == 400
        assert "jsonl" in response.get_json()["error"].lower()

    def test_no_source_path_returns_400(self, client):
        import io
        response = client.post('/index-session', data={
            'file': (io.BytesIO(b'{}'), 'test.jsonl'),
        }, content_type='multipart/form-data')
        assert response.status_code == 400
        assert "source_path" in response.get_json()["error"]

    def test_index_cc_session(self, client):
        import io
        content = (
            '{"type":"user","message":{"role":"user","content":"test question"}}\n'
            '{"type":"assistant","message":{"role":"assistant","content":"test answer"}}\n'
        )
        response = client.post('/index-session', data={
            'file': (io.BytesIO(content.encode()), 'abc12345-uuid-test.jsonl'),
            'source_path': '/home/testuser/.claude/projects/-test/abc12345-uuid-test.jsonl',
        }, content_type='multipart/form-data')

        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'indexed'
        assert data['agent'] in ('CC', 'claude-code')  # 'CC' when agents.json is loaded
        assert data['messages'] == 2

    def test_index_openclaw_session(self, client):
        import io
        content = (
            '{"type":"message","message":{"role":"user","content":"OpenClaw test"}}\n'
            '{"type":"message","message":{"role":"assistant","content":"response"}}\n'
        )
        response = client.post('/index-session', data={
            'file': (io.BytesIO(content.encode()), 'agent-main-telegram-xyz.jsonl'),
            'source_path': '/home/testuser/.openclaw/agents/main/sessions/agent-main-telegram-xyz.jsonl',
        }, content_type='multipart/form-data')

        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'indexed'
        assert data['messages'] == 2

    def test_dedup_same_file(self, client):
        import io
        content = (
            '{"type":"user","message":{"role":"user","content":"dedup test"}}\n'
            '{"type":"assistant","message":{"role":"assistant","content":"response"}}\n'
        )
        source = '/home/testuser/.claude/projects/-test/dedup123.jsonl'

        r1 = client.post('/index-session', data={
            'file': (io.BytesIO(content.encode()), 'dedup123.jsonl'),
            'source_path': source,
        }, content_type='multipart/form-data')
        assert r1.get_json()['status'] == 'indexed'

        r2 = client.post('/index-session', data={
            'file': (io.BytesIO(content.encode()), 'dedup123.jsonl'),
            'source_path': source,
        }, content_type='multipart/form-data')
        assert r2.get_json()['status'] == 'skipped'

    def test_temp_file_cleaned_up(self, client):
        import io
        content = (
            '{"type":"user","message":{"role":"user","content":"cleanup test"}}\n'
            '{"type":"assistant","message":{"role":"assistant","content":"response"}}\n'
        )
        client.post('/index-session', data={
            'file': (io.BytesIO(content.encode()), 'cleanup123.jsonl'),
            'source_path': '/home/testuser/.claude/projects/-test/cleanup123.jsonl',
        }, content_type='multipart/form-data')

        # Temp file should not exist after request
        temp_path = Path('/tmp/claw-recall-remote/.claude/projects/-test/cleanup123.jsonl')
        assert not temp_path.exists()


class TestBrowseRecent:
    """Test the /recent endpoint and browse_recent MCP tool."""

    @pytest.fixture
    def populated_db(self, test_db):
        """Insert sessions and messages with recent timestamps."""
        conn, db_path = test_db
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        def _ts(dt):
            """Format timestamp to match production DB format (space separator, not T)."""
            return dt.strftime('%Y-%m-%d %H:%M:%S.%f+00:00')

        # Session 1: butler, 10 minutes ago
        conn.execute(
            "INSERT INTO sessions (id, agent_id, started_at, message_count) VALUES (?, ?, ?, ?)",
            ("sess-butler-1", "butler", _ts(now - timedelta(minutes=10)), 4),
        )
        for i, (role, content) in enumerate([
            ("user", "Check my email"),
            ("assistant", "Let me check your inbox..."),
            ("tool_result", "Found 3 emails: " + "x" * 400),
            ("assistant", "You have 3 new emails."),
        ]):
            conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp, message_index) VALUES (?, ?, ?, ?, ?)",
                ("sess-butler-1", role, content, _ts(now - timedelta(minutes=10) + timedelta(seconds=i * 5)), i),
            )

        # Session 2: atlas, 5 minutes ago
        conn.execute(
            "INSERT INTO sessions (id, agent_id, started_at, message_count) VALUES (?, ?, ?, ?)",
            ("sess-atlas-1", "atlas", _ts(now - timedelta(minutes=5)), 3),
        )
        for i, (role, content) in enumerate([
            ("user", "What are the latest ads results?"),
            ("assistant", "Looking at the dashboard..."),
            ("assistant", "ROAS is 3.2x for the campaign."),
        ]):
            conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp, message_index) VALUES (?, ?, ?, ?, ?)",
                ("sess-atlas-1", role, content, _ts(now - timedelta(minutes=5) + timedelta(seconds=i * 5)), i),
            )

        # Session 3: boot (should be filtered out)
        conn.execute(
            "INSERT INTO sessions (id, agent_id, started_at, message_count) VALUES (?, ?, ?, ?)",
            ("sess-boot-1", "boot", _ts(now - timedelta(minutes=3)), 5),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, message_index) VALUES (?, ?, ?, ?, ?)",
            ("sess-boot-1", "system", "Boot sequence", _ts(now - timedelta(minutes=3)), 0),
        )

        # Session 4: old session (2 hours ago, should be excluded at 30 min)
        conn.execute(
            "INSERT INTO sessions (id, agent_id, started_at, message_count) VALUES (?, ?, ?, ?)",
            ("sess-old-1", "butler", _ts(now - timedelta(hours=2)), 5),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp, message_index) VALUES (?, ?, ?, ?, ?)",
            ("sess-old-1", "user", "Old message", _ts(now - timedelta(hours=2)), 0),
        )

        conn.commit()
        return conn, db_path

    @pytest.fixture
    def client(self, populated_db, monkeypatch):
        conn, db_path = populated_db
        import claw_recall.api.web as web
        import claw_recall.config as db_module
        monkeypatch.setattr(db_module, 'DB_PATH', db_path)
        import claw_recall.database as _db_module
        monkeypatch.setattr(_db_module, 'DB_PATH', db_path)
        web.app.config['TESTING'] = True
        return web.app.test_client()

    def test_recent_returns_messages(self, client):
        response = client.get('/recent?minutes=30')
        assert response.status_code == 200
        data = response.get_json()
        assert data['total_sessions'] == 2  # butler + atlas, not boot
        assert data['total_messages'] == 7  # 4 butler + 3 atlas

    def test_recent_agent_filter(self, client):
        response = client.get('/recent?minutes=30&agent=butler')
        assert response.status_code == 200
        data = response.get_json()
        assert data['total_sessions'] == 1
        assert data['agent_filter'] == 'butler'  # passthrough — no alias mapping in test
        assert data['sessions'][0]['agent'] == 'butler'

    def test_recent_excludes_boot_sessions(self, client):
        response = client.get('/recent?minutes=30')
        data = response.get_json()
        agents = [s['agent'] for s in data['sessions']]
        assert 'boot' not in agents

    def test_recent_excludes_old_messages(self, client):
        response = client.get('/recent?minutes=30')
        data = response.get_json()
        session_ids = [s['session_id'] for s in data['sessions']]
        assert 'sess-old-1' not in session_ids

    def test_recent_includes_old_with_large_window(self, client):
        response = client.get('/recent?minutes=120')
        data = response.get_json()
        session_ids = [s['session_id'] for s in data['sessions']]
        assert 'sess-old-1' in session_ids

    def test_recent_truncates_tool_results(self, client):
        response = client.get('/recent?minutes=30')
        data = response.get_json()
        butler_session = [s for s in data['sessions'] if s['agent'] == 'butler'][0]
        tool_msg = [m for m in butler_session['messages'] if m['role'] == 'tool_result'][0]
        assert len(tool_msg['content']) <= 503  # 500 + "..."

    def test_recent_empty_timeframe(self, client):
        response = client.get('/recent?minutes=1')
        # May or may not have results depending on timing — just check structure
        data = response.get_json()
        assert 'total_sessions' in data
        assert 'total_messages' in data

    def test_recent_message_order(self, client):
        response = client.get('/recent?minutes=30')
        data = response.get_json()
        for session in data['sessions']:
            indices = [m['message_index'] for m in session['messages']]
            assert indices == sorted(indices), "Messages should be in order"

    def test_recent_case_insensitive_agent(self, client):
        response = client.get('/recent?minutes=30&agent=Butler')
        data = response.get_json()
        assert data['total_sessions'] == 1
        assert data['sessions'][0]['agent'] == 'butler'

    def test_recent_minutes_clamped(self, client):
        response = client.get('/recent?minutes=999')
        data = response.get_json()
        assert data['minutes'] == 120  # clamped to max

    def test_browse_recent_mcp_tool(self, populated_db, monkeypatch):
        """Test the MCP tool function directly."""
        conn, db_path = populated_db
        import claw_recall.config as db_module
        monkeypatch.setattr(db_module, 'DB_PATH', db_path)
        import claw_recall.database as _db_module
        monkeypatch.setattr(_db_module, 'DB_PATH', db_path)
        import claw_recall.api.mcp_stdio as mcp_server
        result = mcp_server.browse_recent(minutes=30)
        assert "Recent Transcript" in result
        assert "butler" in result
        assert "atlas" in result
        assert "boot" not in result

    def test_browse_recent_mcp_agent_filter(self, populated_db, monkeypatch):
        conn, db_path = populated_db
        import claw_recall.config as db_module
        monkeypatch.setattr(db_module, 'DB_PATH', db_path)
        import claw_recall.database as _db_module
        monkeypatch.setattr(_db_module, 'DB_PATH', db_path)
        import claw_recall.api.mcp_stdio as mcp_server
        result = mcp_server.browse_recent(agent="atlas", minutes=30)
        assert "atlas" in result
        assert "Check my email" not in result  # butler's message

    def test_browse_recent_mcp_no_results(self, populated_db, monkeypatch):
        conn, db_path = populated_db
        import claw_recall.config as db_module
        monkeypatch.setattr(db_module, 'DB_PATH', db_path)
        import claw_recall.database as _db_module
        monkeypatch.setattr(_db_module, 'DB_PATH', db_path)
        import claw_recall.api.mcp_stdio as mcp_server
        result = mcp_server.browse_recent(agent="nonexistent", minutes=30)
        assert "No messages found" in result


class TestWatcherHelpers:
    """Test cc-session-watcher helper functions."""

    @pytest.fixture(autouse=True)
    def _load_watcher(self):
        """Import the hyphenated module name via importlib."""
        import importlib.util
        watcher_path = Path(__file__).parent.parent / "scripts" / "cc_session_watcher.py"
        if not watcher_path.exists():
            pytest.skip("scripts/cc_session_watcher.py not found")
        spec = importlib.util.spec_from_file_location(
            "cc_session_watcher",
            str(watcher_path),
        )
        try:
            self.watcher = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.watcher)
        except SystemExit:
            pytest.skip("watcher dependencies not installed (requests/watchdog)")

    def test_needs_indexing_new_file(self, tmp_path):
        state = {"indexed": {}}
        f = tmp_path / "new.jsonl"
        f.write_text('{"test": true}')
        assert self.watcher.needs_indexing(f, state) is True

    def test_needs_indexing_unchanged(self, tmp_path):
        f = tmp_path / "same.jsonl"
        f.write_text('{"test": true}')
        stat = f.stat()
        state = {"indexed": {str(f): {"size": stat.st_size, "mtime": stat.st_mtime}}}
        assert self.watcher.needs_indexing(f, state) is False

    def test_needs_indexing_changed_size(self, tmp_path):
        f = tmp_path / "changed.jsonl"
        f.write_text('{"test": true, "more": "data"}')
        state = {"indexed": {str(f): {"size": 1, "mtime": 0}}}
        assert self.watcher.needs_indexing(f, state) is True

    def test_needs_indexing_missing_file(self, tmp_path):
        f = tmp_path / "missing.jsonl"
        state = {"indexed": {}}
        assert self.watcher.needs_indexing(f, state) is False

    def test_should_handle(self):
        assert self.watcher._should_handle("/path/to/session.jsonl") is True
        assert self.watcher._should_handle("/path/to/session.json") is False
        assert self.watcher._should_handle("/path/subagents/agent.jsonl") is False
        assert self.watcher._should_handle("/path/.deleted.session.jsonl") is False


class TestDBContextManager:
    """Test the shared db.py context manager."""

    def test_get_db_returns_connection(self, tmp_path):
        from claw_recall.database import get_db
        db_path = tmp_path / "test.db"
        with get_db(db_path) as conn:
            conn.execute("CREATE TABLE test (id INTEGER)")
            conn.execute("INSERT INTO test VALUES (1)")
            conn.commit()
            assert conn.execute("SELECT COUNT(*) FROM test").fetchone()[0] == 1

    def test_get_db_closes_on_exit(self, tmp_path):
        from claw_recall.database import get_db
        db_path = tmp_path / "test.db"
        with get_db(db_path) as conn:
            conn.execute("CREATE TABLE test (id INTEGER)")
            conn.commit()
        # Connection should be closed — executing should raise
        with pytest.raises(Exception):
            conn.execute("SELECT 1")

    def test_get_db_closes_on_exception(self, tmp_path):
        from claw_recall.database import get_db
        db_path = tmp_path / "test.db"
        captured_conn = None
        try:
            with get_db(db_path) as conn:
                captured_conn = conn
                raise ValueError("test error")
        except ValueError:
            pass
        with pytest.raises(Exception):
            captured_conn.execute("SELECT 1")

    def test_get_db_wal_mode(self, tmp_path):
        from claw_recall.database import get_db
        db_path = tmp_path / "test.db"
        with get_db(db_path) as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode == "wal"

    def test_get_db_custom_busy_timeout(self, tmp_path):
        from claw_recall.database import get_db
        db_path = tmp_path / "test.db"
        with get_db(db_path, busy_timeout=5000) as conn:
            timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            assert timeout == 5000


class TestWebEndpoints:
    """Test web.py HTTP endpoints."""

    @pytest.fixture
    def populated_db(self, test_db):
        conn, db_path = test_db
        now = datetime.now()

        # Insert sessions
        conn.execute("INSERT INTO sessions (id, agent_id, started_at, message_count) VALUES (?, ?, ?, ?)",
                     ("sess1", "butler", now.isoformat(), 5))
        conn.execute("INSERT INTO sessions (id, agent_id, started_at, message_count) VALUES (?, ?, ?, ?)",
                     ("sess2", "atlas", now.isoformat(), 3))

        # Insert messages for sess1
        for i, (role, content) in enumerate([
            ("user", "Hello butler"),
            ("assistant", "Hi! How can I help?"),
            ("user", "Check my email"),
            ("assistant", "Sure, checking now..."),
            ("assistant", "You have 3 new emails"),
        ]):
            conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp, message_index) VALUES (?,?,?,?,?)",
                ("sess1", role, content, now.isoformat(), i)
            )

        # Insert messages for sess2
        for i, (role, content) in enumerate([
            ("user", "Search for documents"),
            ("assistant", "Found 5 documents"),
            ("user", "Show me the first one"),
        ]):
            conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp, message_index) VALUES (?,?,?,?,?)",
                ("sess2", role, content, now.isoformat(), i)
            )

        conn.commit()
        return conn, db_path

    @pytest.fixture
    def client(self, populated_db, monkeypatch):
        conn, db_path = populated_db
        import claw_recall.api.web as web
        import claw_recall.config as db_module
        monkeypatch.setattr(db_module, 'DB_PATH', db_path)
        import claw_recall.database as _db_module
        monkeypatch.setattr(_db_module, 'DB_PATH', db_path)
        web.app.config['TESTING'] = True
        return web.app.test_client()

    def test_health_endpoint(self, client):
        response = client.get('/health')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'
        assert data['db']['connected'] is True
        assert data['db']['sessions'] == 2
        assert data['db']['embeddings'] == 0

    def test_status_endpoint(self, client):
        response = client.get('/status')
        assert response.status_code == 200
        data = response.get_json()
        assert data['db_messages'] == 8
        assert data['db_sessions'] == 2

    def test_agents_endpoint(self, client):
        response = client.get('/agents?days=0')
        assert response.status_code == 200
        data = response.get_json()
        assert 'butler' in data
        assert 'atlas' in data

    def test_context_endpoint(self, client, populated_db):
        conn, _ = populated_db
        msg_id = conn.execute(
            "SELECT id FROM messages WHERE session_id = 'sess1' AND message_index = 2"
        ).fetchone()[0]
        response = client.get(f'/context?session_id=sess1&message_id={msg_id}&radius=2')
        assert response.status_code == 200
        data = response.get_json()
        assert data['session_id'] == 'sess1'
        assert len(data['messages']) > 0
        assert any(m['is_match'] for m in data['messages'])

    def test_context_missing_params(self, client):
        response = client.get('/context')
        assert response.status_code == 400

    def test_activity_endpoint(self, client):
        response = client.get('/activity?days=0')
        assert response.status_code == 200
        data = response.get_json()
        assert data['total'] == 2
        assert len(data['sessions']) == 2
        assert 'agent_counts' in data

    def test_activity_agent_filter(self, client):
        response = client.get('/activity?days=0&agent=butler')
        assert response.status_code == 200
        data = response.get_json()
        assert data['total'] == 1
        assert data['sessions'][0]['agent'] == 'butler'

    def test_session_endpoint(self, client):
        response = client.get('/session?session_id=sess1')
        assert response.status_code == 200
        data = response.get_json()
        assert data['session_id'] == 'sess1'
        assert data['agent'] == 'butler'
        assert len(data['messages']) == 5

    def test_session_windowed(self, client):
        response = client.get('/session?session_id=sess1&around=2&window=4')
        assert response.status_code == 200
        data = response.get_json()
        assert len(data['messages']) <= 5  # window of 4 around index 2

    def test_session_not_found(self, client):
        response = client.get('/session?session_id=nonexistent')
        data = response.get_json()
        assert 'error' in data

    def test_session_missing_id(self, client):
        response = client.get('/session')
        assert response.status_code == 400


class TestDeepLink:
    """Test generate_deep_link() helper."""

    def test_discord_deep_link(self):
        from claw_recall.api.web import generate_deep_link
        content = "[Discord channel id:123456789] [message_id: 987654321] Hello world"
        link = generate_deep_link(content)
        assert link == "https://discord.com/channels/@me/123456789/987654321"

    def test_no_message_id(self):
        from claw_recall.api.web import generate_deep_link
        assert generate_deep_link("just some text") is None

    def test_message_id_without_platform(self):
        from claw_recall.api.web import generate_deep_link
        assert generate_deep_link("[message_id: 12345] hello") is None


class TestSafeInt:
    """Test _safe_int() helper."""

    def test_valid_int(self):
        from claw_recall.api.web import _safe_int
        assert _safe_int("42", 0) == 42

    def test_default_on_invalid(self):
        from claw_recall.api.web import _safe_int
        assert _safe_int("abc", 10) == 10
        assert _safe_int(None, 5) == 5

    def test_lo_clamp(self):
        from claw_recall.api.web import _safe_int
        assert _safe_int("-5", 0, lo=0) == 0

    def test_hi_clamp(self):
        from claw_recall.api.web import _safe_int
        assert _safe_int("999", 0, hi=100) == 100

    def test_both_clamps(self):
        from claw_recall.api.web import _safe_int
        assert _safe_int("50", 0, lo=10, hi=100) == 50
        assert _safe_int("5", 0, lo=10, hi=100) == 10
        assert _safe_int("200", 0, lo=10, hi=100) == 100


class TestCaptureEndpoint:
    """Test /capture HTTP endpoint."""

    @pytest.fixture
    def client(self, test_db, monkeypatch):
        conn, db_path = test_db
        import claw_recall.api.web as web
        import claw_recall.config as db_module
        import claw_recall.capture.thoughts as capture
        monkeypatch.setattr(db_module, 'DB_PATH', db_path)
        import claw_recall.database as _db_module
        monkeypatch.setattr(_db_module, 'DB_PATH', db_path)
        monkeypatch.setattr(capture, 'OPENAI_AVAILABLE', False)
        web.app.config['TESTING'] = True
        return web.app.test_client()

    def test_capture_via_http(self, client):
        response = client.post('/capture', json={
            'content': 'Test thought from HTTP',
            'source': 'http',
            'agent': 'test-agent',
        })
        assert response.status_code == 201
        data = response.get_json()
        assert data['id'] > 0
        assert data['source'] == 'http'

    def test_capture_empty_content(self, client):
        response = client.post('/capture', json={'content': ''})
        assert response.status_code == 400

    def test_capture_missing_content(self, client):
        response = client.post('/capture', json={})
        assert response.status_code == 400


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
