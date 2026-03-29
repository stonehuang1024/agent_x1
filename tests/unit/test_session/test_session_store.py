"""Unit tests for SessionStore (database persistence layer).

Bug-class targets:
- New fields: agent_id/session_type/transcript_path lost on read/write
- TurnContext CRUD: JSON fields corrupted on round-trip
- Schema migration: duplicate migration crashes, old data destroyed
- Turn metadata: lost on serialization
"""

import json
import time
import pytest
from pathlib import Path

from src.session.session_store import SessionStore
from src.session.models import (
    Session, SessionStatus, SessionType, Turn, TurnContext, TurnStatus, TokenBudget,
)


@pytest.fixture
def store(tmp_path):
    """Create a fresh SessionStore with a temp database."""
    db_path = str(tmp_path / "test.db")
    return SessionStore(db_path)


def _make_session(**kwargs):
    defaults = dict(
        name="test-session",
        working_dir="/tmp",
        session_dir=str(Path("/tmp") / "sessions" / "test"),
    )
    defaults.update(kwargs)
    return Session(**defaults)


# ======================================================================
# 14.1 — New Field Read/Write
# ======================================================================

class TestSessionStoreNewFields:
    """Catches: new fields silently dropped on INSERT/UPDATE/SELECT."""

    def test_create_with_new_fields(self, store):
        session = _make_session(
            agent_id="agent-1",
            session_type=SessionType.DELEGATED,
            transcript_path="/tmp/transcript.jsonl",
        )
        store.create_session(session)
        loaded = store.get_session(session.id)
        assert loaded is not None
        assert loaded.agent_id == "agent-1"
        assert loaded.session_type == SessionType.DELEGATED
        assert loaded.transcript_path == "/tmp/transcript.jsonl"

    def test_create_without_new_fields_uses_defaults(self, store):
        session = _make_session()
        store.create_session(session)
        loaded = store.get_session(session.id)
        assert loaded.agent_id is None
        assert loaded.session_type == SessionType.PRIMARY
        assert loaded.transcript_path == ""

    def test_update_new_fields(self, store):
        session = _make_session()
        store.create_session(session)
        session.agent_id = "agent-2"
        session.session_type = SessionType.DELEGATED
        session.transcript_path = "/new/path.jsonl"
        store.update_session(session)
        loaded = store.get_session(session.id)
        assert loaded.agent_id == "agent-2"
        assert loaded.session_type == SessionType.DELEGATED
        assert loaded.transcript_path == "/new/path.jsonl"


# ======================================================================
# 14.2 — TurnContext CRUD
# ======================================================================

class TestSessionStoreTurnContext:
    """Catches: JSON fields corrupted, missing TurnContext returns wrong value."""

    def _make_ctx(self, session_id="s1", turn_number=1, **kwargs):
        defaults = dict(
            session_id=session_id, turn_number=turn_number,
            model="claude-3", provider="anthropic",
            temperature=0.7, max_tokens=4096,
            working_dir="/tmp",
            tool_configs=["read_file", "write_file"],
            approval_policy="auto",
            behavior_settings={"verbose": True},
            status=TurnStatus.COMPLETED,
        )
        defaults.update(kwargs)
        return TurnContext(**defaults)

    def test_save_and_get(self, store):
        session = _make_session()
        store.create_session(session)
        ctx = self._make_ctx(session_id=session.id)
        ctx.token_usage = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
        ctx.tool_call_records = [{"tool": "read_file", "duration_ms": 50}]
        ctx.latency_stats = {"total_ms": 200.0, "llm_ms": 150.0, "tool_ms": 50.0}
        store.save_turn_context(ctx)

        loaded = store.get_turn_context(session.id, 1)
        assert loaded is not None
        assert loaded.model == "claude-3"
        assert loaded.temperature == 0.7
        assert loaded.tool_configs == ["read_file", "write_file"]
        assert loaded.behavior_settings == {"verbose": True}
        assert loaded.token_usage["total_tokens"] == 150
        assert loaded.tool_call_records[0]["tool"] == "read_file"
        assert loaded.latency_stats["total_ms"] == 200.0

    def test_update_turn_context(self, store):
        session = _make_session()
        store.create_session(session)
        ctx = self._make_ctx(session_id=session.id, status=TurnStatus.RUNNING)
        store.save_turn_context(ctx)

        ctx.status = TurnStatus.COMPLETED
        ctx.token_usage = {"total_tokens": 999}
        ctx.completed_at = time.time()
        store.update_turn_context(ctx)

        loaded = store.get_turn_context(session.id, 1)
        assert loaded.status == TurnStatus.COMPLETED
        assert loaded.token_usage["total_tokens"] == 999
        assert loaded.completed_at is not None

    def test_get_all_turn_contexts_sorted(self, store):
        session = _make_session()
        store.create_session(session)
        for i in [3, 1, 2]:
            store.save_turn_context(self._make_ctx(session_id=session.id, turn_number=i))
        contexts = store.get_all_turn_contexts(session.id)
        assert [c.turn_number for c in contexts] == [1, 2, 3]

    def test_get_nonexistent_returns_none(self, store):
        assert store.get_turn_context("nonexistent", 1) is None


# ======================================================================
# 14.3 — Schema Migration
# ======================================================================

class TestSessionStoreMigration:
    """Catches: duplicate migration crash, old data destroyed."""

    def test_fresh_db_has_all_tables(self, store):
        """A fresh store must have sessions, turns, checkpoints, turn_contexts tables."""
        conn = store._get_connection()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        assert "sessions" in tables
        assert "turns" in tables
        assert "checkpoints" in tables
        assert "turn_contexts" in tables
        assert "schema_migrations" in tables

    def test_idempotent_migration(self, tmp_path):
        """Creating two stores on the same DB must not crash (migrations are idempotent)."""
        db_path = str(tmp_path / "test.db")
        store1 = SessionStore(db_path)
        session = _make_session()
        store1.create_session(session)

        store2 = SessionStore(db_path)
        loaded = store2.get_session(session.id)
        assert loaded is not None
        assert loaded.name == session.name

    def test_migration_version_recorded(self, store):
        conn = store._get_connection()
        cursor = conn.execute("SELECT version FROM schema_migrations ORDER BY version")
        versions = [row[0] for row in cursor.fetchall()]
        assert len(versions) >= 1  # At least migration 002


# ======================================================================
# 14.4 — Turn Metadata
# ======================================================================

class TestSessionStoreTurnMetadata:
    """Catches: metadata field lost on INSERT/SELECT."""

    def test_turn_with_metadata(self, store):
        session = _make_session()
        store.create_session(session)
        turn = Turn(
            session_id=session.id, turn_number=1, role="user",
            content="hello", metadata={"source": "cli", "tags": ["test"]},
        )
        store.add_turn(turn)
        turns = store.get_turns(session.id)
        assert len(turns) == 1
        assert turns[0].metadata == {"source": "cli", "tags": ["test"]}

    def test_turn_without_metadata_defaults_empty(self, store):
        session = _make_session()
        store.create_session(session)
        turn = Turn(
            session_id=session.id, turn_number=1, role="user", content="hi",
        )
        store.add_turn(turn)
        turns = store.get_turns(session.id)
        assert turns[0].metadata == {}
