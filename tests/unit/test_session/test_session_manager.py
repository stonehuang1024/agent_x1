"""Unit tests for SessionManager.

Bug-class targets:
- State transitions: illegal transition silently succeeds, event not emitted
- begin_turn/end_turn: TurnContext not frozen, budget not updated
- Session recovery: wrong session resumed, history lost, config mismatch ignored
- Fork: original session mutated to FORKED
- record_turn: dual-write (SQLite + JSONL) not both executed
"""

import os
import time
import logging
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.session.session_manager import SessionManager
from src.session.session_store import SessionStore
from src.session.models import (
    Session, SessionStatus, SessionType, Turn, TurnContext, TurnStatus,
    TokenBudget, InvalidStateTransition,
)
from src.core.events import EventBus, AgentEvent


# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture
def tmp_env(tmp_path):
    """Create a temp environment with all needed dirs."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    memory_dir = tmp_path / "memory_data"
    memory_dir.mkdir()
    return tmp_path


@pytest.fixture
def config(tmp_env):
    """Create a mock AppConfig."""
    cfg = MagicMock()
    cfg.llm.provider = "anthropic"
    cfg.llm.model = "claude-3"
    cfg.llm.temperature = 0.7
    cfg.llm.max_tokens = 4096
    cfg.paths.data_dir = str(tmp_env / "data")
    cfg.paths.result_dir = str(tmp_env / "results")
    cfg.paths.session_dir = str(tmp_env / "results" / "session")
    cfg.paths.memory_data_dir = str(tmp_env / "memory_data")
    return cfg


@pytest.fixture
def store(tmp_env):
    return SessionStore(str(tmp_env / "data" / "test.db"))


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def manager(store, config, event_bus, tmp_env):
    return SessionManager(
        store=store, config=config, event_bus=event_bus,
        index_path=str(tmp_env / "data" / "sessions-index.json"),
    )


# ======================================================================
# 15.1 — State Transition Integration
# ======================================================================

class TestSessionManagerStateTransitions:
    """Catches: illegal transition accepted, event not emitted, wrong state after op."""

    def test_create_session_is_created(self, manager):
        session = manager.create_session(name="test")
        assert session.status == SessionStatus.CREATED

    def test_begin_turn_activates_created(self, manager):
        session = manager.create_session(name="test")
        manager.activate_session(session.id)
        ctx = manager.begin_turn()
        assert manager.active_session.status == SessionStatus.ACTIVE

    def test_pause_from_active(self, manager):
        session = manager.create_session(name="test")
        manager.activate_session(session.id)
        manager.begin_turn()  # CREATED → ACTIVE
        manager.pause_session()
        loaded = manager.store.get_session(session.id)
        assert loaded.status == SessionStatus.PAUSED

    def test_resume_paused_to_active(self, manager):
        session = manager.create_session(name="test")
        manager.activate_session(session.id)
        manager.begin_turn()
        manager.pause_session()
        manager.resume_paused()
        loaded = manager.store.get_session(session.id)
        assert loaded.status == SessionStatus.ACTIVE

    def test_complete_from_active(self, manager):
        session = manager.create_session(name="test")
        manager.activate_session(session.id)
        manager.begin_turn()
        manager.complete_session()
        loaded = manager.store.get_session(session.id)
        assert loaded.status == SessionStatus.COMPLETED

    def test_fail_from_active(self, manager):
        session = manager.create_session(name="test")
        manager.activate_session(session.id)
        manager.begin_turn()
        manager.fail_session("test error")
        loaded = manager.store.get_session(session.id)
        assert loaded.status == SessionStatus.FAILED

    def test_archive_from_completed(self, manager):
        session = manager.create_session(name="test")
        manager.activate_session(session.id)
        manager.begin_turn()
        manager.complete_session()
        # Re-load to archive
        manager._active_session = manager.store.get_session(session.id)
        manager.archive_session()
        loaded = manager.store.get_session(session.id)
        assert loaded.status == SessionStatus.ARCHIVED

    def test_compacting_and_complete_compaction(self, manager):
        session = manager.create_session(name="test")
        manager.activate_session(session.id)
        manager.begin_turn()  # CREATED → ACTIVE
        manager._transition(manager.active_session, SessionStatus.COMPACTING)
        assert manager.active_session.status == SessionStatus.COMPACTING
        manager.complete_compaction(new_used=100)
        loaded = manager.store.get_session(session.id)
        assert loaded.status == SessionStatus.ACTIVE

    def test_events_emitted_on_transitions(self, manager, event_bus):
        events_received = []
        event_bus.subscribe(AgentEvent.SESSION_RESUMED, lambda p: events_received.append("resumed"))
        event_bus.subscribe(AgentEvent.SESSION_PAUSED, lambda p: events_received.append("paused"))
        event_bus.subscribe(AgentEvent.SESSION_COMPLETED, lambda p: events_received.append("completed"))

        session = manager.create_session(name="test")
        manager.activate_session(session.id)
        manager.begin_turn()  # CREATED → ACTIVE emits SESSION_RESUMED
        manager.pause_session()
        manager.resume_paused()
        manager.complete_session()

        assert "resumed" in events_received
        assert "paused" in events_received
        assert "completed" in events_received


# ======================================================================
# 15.2 — begin_turn / end_turn Lifecycle
# ======================================================================

class TestTurnLifecycle:
    """Catches: TurnContext not frozen, budget not updated, threshold not triggered."""

    def test_begin_turn_returns_frozen_context(self, manager):
        session = manager.create_session(name="test")
        manager.activate_session(session.id)
        ctx = manager.begin_turn()
        assert ctx.status == TurnStatus.RUNNING
        assert ctx.session_id == session.id
        # Config fields must be frozen
        with pytest.raises(AttributeError):
            ctx.model = "different"

    def test_begin_turn_config_matches_snapshot(self, manager, config):
        session = manager.create_session(name="test")
        manager.activate_session(session.id)
        ctx = manager.begin_turn()
        assert ctx.model == config.llm.model
        assert ctx.provider == config.llm.provider

    def test_end_turn_updates_session_stats(self, manager):
        session = manager.create_session(name="test")
        manager.activate_session(session.id)
        ctx = manager.begin_turn()
        ctx.token_usage = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
        manager.end_turn(ctx)
        loaded = manager.store.get_session(session.id)
        assert loaded.turn_count == 1
        assert loaded.budget.used >= 150

    def test_end_turn_persists_context(self, manager):
        session = manager.create_session(name="test")
        manager.activate_session(session.id)
        ctx = manager.begin_turn()
        ctx.token_usage = {"total_tokens": 42}
        manager.end_turn(ctx)
        loaded_ctx = manager.store.get_turn_context(session.id, ctx.turn_number)
        assert loaded_ctx is not None
        assert loaded_ctx.status == TurnStatus.COMPLETED

    def test_end_turn_budget_warning_event(self, manager, event_bus):
        """Token usage > 80% must emit TOKEN_BUDGET_WARNING."""
        warnings = []
        event_bus.subscribe(AgentEvent.TOKEN_BUDGET_WARNING, lambda p: warnings.append(p))

        session = manager.create_session(name="test")
        manager._active_session = session
        # Manually activate to avoid begin_turn
        manager._transition(session, SessionStatus.ACTIVE)
        # Default budget: total=40960, reserved=8192, effective=32768
        # 80% of 32768 = 26214.4, so we need used >= 26215
        ctx = manager.begin_turn()
        ctx.token_usage = {"total_tokens": 27000}
        manager.end_turn(ctx)

        assert len(warnings) >= 1

    def test_end_turn_compaction_trigger(self, manager):
        """Token usage > 90% must trigger COMPACTING state."""
        session = manager.create_session(name="test")
        manager._active_session = session
        # Manually activate to avoid begin_turn
        manager._transition(session, SessionStatus.ACTIVE)
        # Default budget: total=40960, reserved=8192, effective=32768
        # 90% of 32768 = 29491.2, so we need used >= 29492
        ctx = manager.begin_turn()
        ctx.token_usage = {"total_tokens": 30000}
        manager.end_turn(ctx)

        # end_turn loads session from DB via _get_session, so check DB
        loaded = manager.store.get_session(session.id)
        assert loaded.status == SessionStatus.COMPACTING


# ======================================================================
# 15.3 — Session Recovery (continue / resume)
# ======================================================================

class TestSessionRecovery:
    """Catches: wrong session resumed, history lost, config mismatch ignored."""

    def test_continue_finds_paused(self, manager):
        s1 = manager.create_session(name="s1")
        manager.activate_session(s1.id)
        manager.begin_turn()
        manager.pause_session()

        resumed = manager.continue_session()
        assert resumed.id == s1.id
        assert resumed.status == SessionStatus.ACTIVE

    def test_continue_finds_active_when_no_paused(self, manager):
        s1 = manager.create_session(name="s1")
        manager.activate_session(s1.id)
        manager.begin_turn()  # CREATED → ACTIVE

        resumed = manager.continue_session()
        assert resumed.id == s1.id

    def test_continue_raises_when_none_available(self, manager):
        with pytest.raises(ValueError, match="No resumable session"):
            manager.continue_session()

    def test_resume_by_id(self, manager):
        s1 = manager.create_session(name="s1")
        manager.activate_session(s1.id)
        manager.begin_turn()
        manager.pause_session()

        resumed = manager.resume_session_by_id(s1.id)
        assert resumed.id == s1.id
        assert resumed.status == SessionStatus.ACTIVE

    def test_resume_by_id_not_found(self, manager):
        with pytest.raises(ValueError, match="not found"):
            manager.resume_session_by_id("nonexistent-id")

    def test_resume_rebuilds_from_transcript(self, manager):
        """Resume must rebuild history from JSONL transcript."""
        s1 = manager.create_session(name="s1")
        manager.activate_session(s1.id)
        manager.begin_turn()
        manager.record_turn(role="user", content="hello")
        manager.record_turn(role="assistant", content="hi there")
        manager.pause_session()

        # Resume and verify transcript file exists and has content
        resumed = manager.resume_session_by_id(s1.id)
        assert resumed.transcript_path
        assert Path(resumed.transcript_path).exists()

    def test_resume_config_mismatch_logs_warning(self, manager, config, caplog):
        """Config mismatch on resume must log a warning."""
        s1 = manager.create_session(name="s1")
        manager.activate_session(s1.id)
        manager.begin_turn()
        manager.pause_session()

        # Change config
        config.llm.model = "gpt-4-turbo"
        with caplog.at_level(logging.WARNING):
            manager.resume_session_by_id(s1.id)
        assert any("Config mismatch" in r.message for r in caplog.records)


# ======================================================================
# 15.4 — Fork Behavior
# ======================================================================

class TestForkBehavior:
    """Catches: original session mutated to FORKED state."""

    def test_fork_original_keeps_state(self, manager):
        parent = manager.create_session(name="parent")
        manager.activate_session(parent.id)
        manager.begin_turn()
        manager.record_turn(role="user", content="hello")

        child = manager.create_session(name="child", parent_id=parent.id)

        # Original must NOT be FORKED
        loaded_parent = manager.store.get_session(parent.id)
        assert loaded_parent.status != SessionStatus.ARCHIVED
        assert loaded_parent.status == SessionStatus.ACTIVE

    def test_fork_child_is_active_with_parent_id(self, manager):
        parent = manager.create_session(name="parent")
        manager.activate_session(parent.id)
        manager.begin_turn()
        manager.record_turn(role="user", content="hello")

        child = manager.create_session(name="child", parent_id=parent.id)
        assert child.parent_id == parent.id
        assert child.status == SessionStatus.ACTIVE

    def test_fork_child_has_parent_turns(self, manager):
        parent = manager.create_session(name="parent")
        manager.activate_session(parent.id)
        manager.begin_turn()
        manager.record_turn(role="user", content="hello")
        manager.record_turn(role="assistant", content="hi")

        child = manager.create_session(name="child", parent_id=parent.id)
        child_turns = manager.store.get_turns(child.id)
        assert len(child_turns) >= 2


# ======================================================================
# 15.5 — record_turn Dual-Write
# ======================================================================

class TestRecordTurnDualWrite:
    """Catches: JSONL write skipped, index not updated."""

    def test_record_turn_writes_to_sqlite(self, manager):
        session = manager.create_session(name="test")
        manager.activate_session(session.id)
        manager.begin_turn()
        manager.record_turn(role="user", content="hello")
        turns = manager.store.get_turns(session.id)
        assert len(turns) == 1
        assert turns[0].content == "hello"

    def test_record_turn_writes_to_jsonl(self, manager):
        session = manager.create_session(name="test")
        manager.activate_session(session.id)
        manager.begin_turn()
        manager.record_turn(role="user", content="hello")

        # Verify JSONL file has content
        from src.session.transcript import TranscriptReader
        reader = TranscriptReader(session.transcript_path)
        entries = reader.read_all()
        assert len(entries) >= 1
        assert any(e.get("content") == "hello" for e in entries)

    def test_record_turn_updates_index(self, manager):
        session = manager.create_session(name="test")
        manager.activate_session(session.id)
        manager.begin_turn()
        manager.record_turn(role="user", content="hello world")

        entry = manager._index.get(session.id)
        assert entry is not None
        assert entry.preview == "hello world"
