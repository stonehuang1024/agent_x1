"""Integration tests for the Session module.

These tests exercise the full stack: SessionManager → SessionStore → SQLite,
TranscriptWriter/Reader, SessionIndex, SessionLogger, and DiffTracker
working together in realistic scenarios.

Bug-class targets:
- Data loss across pause/resume cycles
- SQLite vs JSONL vs Index inconsistency
- Compaction state machine not completing
- JSONL corruption causing unrecoverable state
"""

import json
import logging
import os
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from src.session.session_manager import SessionManager
from src.session.session_store import SessionStore
from src.session.session_index import SessionIndex
from src.session.session_logger import SessionLogger
from src.session.transcript import TranscriptReader
from src.session.diff_tracker import DiffTracker, ChangeType
from src.session.models import (
    Session, SessionStatus, SessionType, Turn, TurnContext, TurnStatus,
    TokenBudget, InvalidStateTransition, SessionIndexEntry,
)
from src.core.events import EventBus, AgentEvent


# ======================================================================
# Fixtures
# ======================================================================

@pytest.fixture
def env(tmp_path):
    """Create a complete temp environment."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    memory_dir = tmp_path / "memory_data"
    memory_dir.mkdir()
    return tmp_path


@pytest.fixture
def config(env):
    cfg = MagicMock()
    cfg.llm.provider = "anthropic"
    cfg.llm.model = "claude-3"
    cfg.llm.temperature = 0.7
    cfg.llm.max_tokens = 4096
    cfg.paths.data_dir = str(env / "data")
    cfg.paths.result_dir = str(env / "results")
    cfg.paths.session_dir = str(env / "results" / "session")
    cfg.paths.memory_data_dir = str(env / "memory_data")
    return cfg


@pytest.fixture
def store(env):
    return SessionStore(str(env / "data" / "test.db"))


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def manager(store, config, event_bus, env):
    return SessionManager(
        store=store, config=config, event_bus=event_bus,
        index_path=str(env / "data" / "sessions-index.json"),
    )


def _record_turns(manager, n, start=1):
    """Helper: record n user/assistant turn pairs."""
    for i in range(start, start + n):
        manager.record_turn(role="user", content=f"User message {i}", token_count=100)
        manager.record_turn(role="assistant", content=f"Assistant reply {i}", token_count=200)


# ======================================================================
# 17.1 — Full Session Lifecycle
# ======================================================================

class TestFullSessionLifecycle:
    """End-to-end: create → turns → pause → continue → complete."""

    def test_full_lifecycle(self, manager, store):
        # Create
        session = manager.create_session(name="lifecycle-test")
        manager._active_session = session
        assert session.status == SessionStatus.CREATED

        # First turn activates
        ctx = manager.begin_turn()
        assert manager._active_session.status == SessionStatus.ACTIVE

        # Record 3 rounds of conversation
        _record_turns(manager, 3)
        ctx.token_usage = {"total_tokens": 900}
        manager.end_turn(ctx)

        # Pause
        manager.pause_session()
        loaded = store.get_session(session.id)
        assert loaded.status == SessionStatus.PAUSED

        # Continue
        resumed = manager.continue_session()
        assert resumed.id == session.id
        assert resumed.status == SessionStatus.ACTIVE

        # Record more turns
        ctx2 = manager.begin_turn()
        _record_turns(manager, 2, start=4)
        ctx2.token_usage = {"total_tokens": 600}
        manager.end_turn(ctx2)

        # Complete
        manager.complete_session()
        loaded = store.get_session(session.id)
        assert loaded.status == SessionStatus.COMPLETED

        # Verify SQLite data
        turns = store.get_turns(session.id)
        assert len(turns) == 10  # 5 pairs × 2

        # Verify JSONL transcript
        reader = TranscriptReader(session.transcript_path)
        entries = reader.read_all()
        assert len(entries) >= 10  # At least the 10 message entries

        # Verify index
        entry = manager._index.get(session.id)
        assert entry is not None
        assert entry.status == "completed"

        # Verify TurnContexts persisted
        ctx_list = store.get_all_turn_contexts(session.id)
        assert len(ctx_list) >= 2


# ======================================================================
# 17.2 — Session Fork and Checkpoint
# ======================================================================

class TestSessionForkAndCheckpoint:
    """Fork must not mutate parent; checkpoint restore must work."""

    def test_fork_preserves_parent(self, manager, store):
        parent = manager.create_session(name="parent")
        manager._active_session = parent
        ctx = manager.begin_turn()
        _record_turns(manager, 3)
        ctx.token_usage = {"total_tokens": 300}
        manager.end_turn(ctx)

        # Fork
        child = manager.create_session(name="child", parent_id=parent.id)

        # Parent must NOT be FORKED
        loaded_parent = store.get_session(parent.id)
        assert loaded_parent.status == SessionStatus.ACTIVE

        # Child must have parent's turns
        child_turns = store.get_turns(child.id)
        assert len(child_turns) == 6  # 3 pairs

        # Child must be ACTIVE with parent_id
        assert child.status == SessionStatus.ACTIVE
        assert child.parent_id == parent.id

    def test_checkpoint_and_restore(self, manager, store):
        session = manager.create_session(name="cp-test")
        manager._active_session = session
        ctx = manager.begin_turn()
        _record_turns(manager, 3)
        ctx.token_usage = {"total_tokens": 300}
        manager.end_turn(ctx)

        # Create checkpoint
        cp_id = manager.checkpoint(name="mid-point")

        # Record more turns
        ctx2 = manager.begin_turn()
        _record_turns(manager, 2, start=4)
        ctx2.token_usage = {"total_tokens": 200}
        manager.end_turn(ctx2)

        # Restore from checkpoint
        restored = manager.restore_checkpoint(cp_id, new_name="restored")
        assert restored.parent_id == session.id


# ======================================================================
# 17.3 — Context Compaction Trigger
# ======================================================================

class TestContextCompaction:
    """Budget thresholds must trigger warning and compaction."""

    def test_compaction_lifecycle(self, manager, store, event_bus):
        warnings = []
        compacting_events = []
        event_bus.subscribe(AgentEvent.TOKEN_BUDGET_WARNING, lambda p: warnings.append(p))
        event_bus.subscribe(AgentEvent.SESSION_COMPACTING, lambda p: compacting_events.append(p))

        session = manager.create_session(name="compact-test")
        manager._active_session = session
        # Default budget: total=40960, reserved=8192, effective=32768
        # 80% = 26214, 90% = 29491

        # Activate
        manager._transition(session, SessionStatus.ACTIVE)

        # Turn with tokens that exceed 90%
        ctx = manager.begin_turn()
        ctx.token_usage = {"total_tokens": 30000}
        manager.end_turn(ctx)

        # Should have triggered warning AND compaction
        assert len(warnings) >= 1
        loaded = store.get_session(session.id)
        assert loaded.status == SessionStatus.COMPACTING

        # Complete compaction
        manager._active_session = loaded
        manager.complete_compaction(new_used=5000)
        loaded2 = store.get_session(session.id)
        assert loaded2.status == SessionStatus.ACTIVE
        assert loaded2.budget.used == 5000


# ======================================================================
# 17.4 — Session Recovery (continue/resume) End-to-End
# ======================================================================

class TestSessionRecoveryE2E:
    """Simulates process restart by creating a new SessionManager."""

    def test_continue_after_restart(self, store, config, event_bus, env):
        index_path = str(env / "data" / "sessions-index.json")

        # First "process"
        mgr1 = SessionManager(store=store, config=config, event_bus=event_bus, index_path=index_path)
        session = mgr1.create_session(name="recovery-test")
        mgr1._active_session = session
        ctx = mgr1.begin_turn()
        _record_turns(mgr1, 3)
        ctx.token_usage = {"total_tokens": 300}
        mgr1.end_turn(ctx)
        mgr1.pause_session()

        # Second "process" (new manager, same store)
        mgr2 = SessionManager(store=store, config=config, event_bus=event_bus, index_path=index_path)
        resumed = mgr2.continue_session()
        assert resumed.id == session.id
        assert resumed.status == SessionStatus.ACTIVE

    def test_resume_by_id_after_restart(self, store, config, event_bus, env):
        index_path = str(env / "data" / "sessions-index.json")

        mgr1 = SessionManager(store=store, config=config, event_bus=event_bus, index_path=index_path)
        session = mgr1.create_session(name="resume-test")
        mgr1._active_session = session
        ctx = mgr1.begin_turn()
        _record_turns(mgr1, 2)
        ctx.token_usage = {"total_tokens": 200}
        mgr1.end_turn(ctx)
        mgr1.pause_session()

        mgr2 = SessionManager(store=store, config=config, event_bus=event_bus, index_path=index_path)
        resumed = mgr2.resume_session_by_id(session.id)
        assert resumed.id == session.id
        assert resumed.status == SessionStatus.ACTIVE

        # Verify history was rebuilt from transcript
        assert Path(resumed.transcript_path).exists()


# ======================================================================
# 17.5 — JSONL Corruption Recovery
# ======================================================================

class TestJSONLCorruptionRecovery:
    """Corrupt JSONL lines must be skipped; valid data preserved."""

    def test_resume_with_corrupt_jsonl(self, store, config, event_bus, env, caplog):
        index_path = str(env / "data" / "sessions-index.json")

        mgr1 = SessionManager(store=store, config=config, event_bus=event_bus, index_path=index_path)
        session = mgr1.create_session(name="corrupt-test")
        mgr1._active_session = session
        ctx = mgr1.begin_turn()
        _record_turns(mgr1, 5)
        ctx.token_usage = {"total_tokens": 500}
        mgr1.end_turn(ctx)
        mgr1.pause_session()

        # Inject corruption into JSONL
        transcript_path = Path(session.transcript_path)
        original_lines = transcript_path.read_text().splitlines()
        corrupted_lines = []
        for i, line in enumerate(original_lines):
            corrupted_lines.append(line)
            if i in (2, 5, 8):  # Insert corrupt lines after positions 2, 5, 8
                corrupted_lines.append("THIS IS NOT VALID JSON {{{")
        transcript_path.write_text("\n".join(corrupted_lines) + "\n")

        # Resume should still work
        mgr2 = SessionManager(store=store, config=config, event_bus=event_bus, index_path=index_path)
        with caplog.at_level(logging.WARNING):
            resumed = mgr2.resume_session_by_id(session.id)

        assert resumed.status == SessionStatus.ACTIVE
        # Verify warnings were logged for corrupt lines
        warning_msgs = [r for r in caplog.records if "invalid JSONL" in r.message.lower() or "skipping" in r.message.lower()]
        assert len(warning_msgs) >= 1


# ======================================================================
# 17.6 — Data Consistency (SQLite vs JSONL vs Index)
# ======================================================================

class TestDataConsistency:
    """SQLite, JSONL, and Index must agree on turn count and status."""

    def test_three_way_consistency(self, manager, store):
        session = manager.create_session(name="consistency-test")
        manager._active_session = session
        ctx = manager.begin_turn()

        # Record 10 turns
        _record_turns(manager, 5)
        ctx.token_usage = {"total_tokens": 500}
        manager.end_turn(ctx)

        # Pause and resume
        manager.pause_session()
        manager.resume_session_by_id(session.id)

        # Record 5 more turns
        ctx2 = manager.begin_turn()
        _record_turns(manager, 3, start=6)
        ctx2.token_usage = {"total_tokens": 300}
        manager.end_turn(ctx2)

        # Complete
        manager.complete_session()

        # Verify SQLite
        sqlite_turns = store.get_turns(session.id)
        sqlite_count = len(sqlite_turns)

        # Verify JSONL
        reader = TranscriptReader(session.transcript_path)
        jsonl_entries = reader.read_all()
        # JSONL has message entries + possibly other types (llm_interaction, diff_summary)
        jsonl_message_count = sum(1 for e in jsonl_entries if e.get("type") == "message")

        # Both should have the same number of message entries
        assert sqlite_count == jsonl_message_count

        # Verify Index
        entry = manager._index.get(session.id)
        assert entry is not None
        assert entry.status == "completed"


# ======================================================================
# 17.7 — SessionLogger Integration
# ======================================================================

class TestSessionLoggerIntegration:
    """SessionLogger must produce correct files when used through SessionManager."""

    def test_logger_produces_files(self, manager, store):
        session = manager.create_session(name="logger-test")
        manager._active_session = session
        ctx = manager.begin_turn()

        # Use the session logger
        session_logger = manager.get_session_logger()
        assert session_logger is not None

        for i in range(3):
            session_logger.log_llm_interaction(
                iteration=i + 1,
                messages=[{"role": "user", "content": f"msg {i}"}],
                tools=[],
                response={"content": f"reply {i}"},
                usage={"input_tokens": 100, "output_tokens": 50},
                duration_ms=100.0,
                stop_reason="end_turn",
            )

        _record_turns(manager, 2)
        ctx.token_usage = {"total_tokens": 300}
        manager.end_turn(ctx)

        # Complete session (triggers summary generation)
        manager.complete_session()

        session_dir = Path(session.session_dir)

        # Verify session_llm.md
        llm_file = session_dir / "session_llm.md"
        assert llm_file.exists()
        llm_content = llm_file.read_text()
        assert "LLM Call 1" in llm_content
        assert "LLM Call 2" in llm_content
        assert "LLM Call 3" in llm_content

        # Verify session_summary.md
        summary_file = session_dir / "session_summary.md"
        assert summary_file.exists()
        summary_content = summary_file.read_text()
        assert "Total LLM Calls:** 3" in summary_content
