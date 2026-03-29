"""Unit tests for session data models.

Bug-class targets:
- State transition matrix: illegal transitions silently succeed
- TurnContext freeze: config mutation after freeze goes undetected
- Serialization: round-trip loses data or crashes on old schemas
- TokenBudget: threshold math off-by-one or division-by-zero
"""

import time
import pytest
from datetime import datetime

from src.session.models import (
    Session, SessionStatus, SessionType, Turn, TurnContext, TurnStatus,
    TokenBudget, SessionIndexEntry, InvalidStateTransition, VALID_TRANSITIONS,
)


# ======================================================================
# 10.1 — State Transition Matrix
# ======================================================================

class TestStateTransitionMatrix:
    """Catches: illegal transitions accepted, legal transitions rejected."""

    @pytest.mark.parametrize("from_status,to_status", [
        (SessionStatus.CREATED, SessionStatus.ACTIVE),
        (SessionStatus.CREATED, SessionStatus.FAILED),
        (SessionStatus.CREATED, SessionStatus.ARCHIVED),
        (SessionStatus.ACTIVE, SessionStatus.PAUSED),
        (SessionStatus.ACTIVE, SessionStatus.COMPACTING),
        (SessionStatus.ACTIVE, SessionStatus.COMPLETED),
        (SessionStatus.ACTIVE, SessionStatus.FAILED),
        (SessionStatus.ACTIVE, SessionStatus.ARCHIVED),
        (SessionStatus.PAUSED, SessionStatus.ACTIVE),
        (SessionStatus.PAUSED, SessionStatus.COMPLETED),
        (SessionStatus.PAUSED, SessionStatus.FAILED),
        (SessionStatus.PAUSED, SessionStatus.ARCHIVED),
        (SessionStatus.COMPACTING, SessionStatus.ACTIVE),
        (SessionStatus.COMPACTING, SessionStatus.FAILED),
        (SessionStatus.COMPLETED, SessionStatus.ACTIVE),
        (SessionStatus.COMPLETED, SessionStatus.ARCHIVED),
        (SessionStatus.FAILED, SessionStatus.ACTIVE),
        (SessionStatus.FAILED, SessionStatus.ARCHIVED),
    ])
    def test_legal_transitions_accepted(self, from_status, to_status):
        """Every entry in VALID_TRANSITIONS must be accepted without exception."""
        session = Session(status=from_status)
        result = session.validate_transition(to_status)
        assert result is True, (
            f"{from_status.value} → {to_status.value} should be legal but was rejected"
        )

    @pytest.mark.parametrize("from_status,to_status", [
        (SessionStatus.ARCHIVED, SessionStatus.ACTIVE),
        (SessionStatus.ARCHIVED, SessionStatus.PAUSED),
        (SessionStatus.ARCHIVED, SessionStatus.COMPLETED),
        (SessionStatus.CREATED, SessionStatus.COMPLETED),
        (SessionStatus.CREATED, SessionStatus.PAUSED),
        (SessionStatus.CREATED, SessionStatus.COMPACTING),
        (SessionStatus.COMPACTING, SessionStatus.PAUSED),
        (SessionStatus.COMPACTING, SessionStatus.COMPLETED),
        (SessionStatus.COMPACTING, SessionStatus.ARCHIVED),
        (SessionStatus.COMPLETED, SessionStatus.PAUSED),
        (SessionStatus.COMPLETED, SessionStatus.COMPACTING),
        (SessionStatus.COMPLETED, SessionStatus.FAILED),
        (SessionStatus.FAILED, SessionStatus.PAUSED),
        (SessionStatus.FAILED, SessionStatus.COMPACTING),
        (SessionStatus.FAILED, SessionStatus.COMPLETED),
    ])
    def test_illegal_transitions_rejected(self, from_status, to_status):
        """Transitions not in VALID_TRANSITIONS must raise InvalidStateTransition."""
        session = Session(status=from_status)
        with pytest.raises(InvalidStateTransition) as exc_info:
            session.validate_transition(to_status)
        assert exc_info.value.from_status == from_status
        assert exc_info.value.to_status == to_status

    def test_same_state_is_noop(self):
        """Same-state transition returns False (no-op), no exception."""
        for status in SessionStatus:
            session = Session(status=status)
            result = session.validate_transition(status)
            assert result is False, f"Same-state {status.value} should return False"

    def test_completed_to_active_for_resume(self):
        """--resume must be able to reactivate a COMPLETED session."""
        session = Session(status=SessionStatus.COMPLETED)
        assert session.validate_transition(SessionStatus.ACTIVE) is True

    def test_failed_to_active_for_retry(self):
        """Retry must be able to reactivate a FAILED session."""
        session = Session(status=SessionStatus.FAILED)
        assert session.validate_transition(SessionStatus.ACTIVE) is True

    def test_archived_is_terminal(self):
        """ARCHIVED has no outgoing transitions at all."""
        session = Session(status=SessionStatus.ARCHIVED)
        for target in SessionStatus:
            if target == SessionStatus.ARCHIVED:
                continue
            with pytest.raises(InvalidStateTransition):
                session.validate_transition(target)

    def test_transition_matrix_covers_all_statuses(self):
        """Every SessionStatus must appear as a key in VALID_TRANSITIONS."""
        for status in SessionStatus:
            assert status in VALID_TRANSITIONS, (
                f"{status.value} missing from VALID_TRANSITIONS"
            )

    def test_exception_contains_session_id(self):
        """InvalidStateTransition must carry the session_id for debugging."""
        session = Session(status=SessionStatus.ARCHIVED)
        session.id = "test-session-123"
        with pytest.raises(InvalidStateTransition) as exc_info:
            session.validate_transition(SessionStatus.ACTIVE)
        assert "test-session-123" in str(exc_info.value)


# ======================================================================
# 10.2 — TurnContext Freeze Semantics
# ======================================================================

class TestTurnContextFreeze:
    """Catches: config mutation after freeze, runtime fields blocked after freeze."""

    def test_config_fields_mutable_before_freeze(self):
        ctx = TurnContext(model="gpt-4")
        ctx.model = "claude-3"
        assert ctx.model == "claude-3"

    def test_config_fields_frozen_after_freeze(self):
        """All config fields must raise AttributeError after freeze()."""
        ctx = TurnContext(
            model="gpt-4", working_dir="/tmp", temperature=0.5,
            max_tokens=1000, provider="openai",
        )
        ctx.freeze()
        config_fields = [
            "model", "working_dir", "temperature", "max_tokens",
            "provider", "turn_number", "session_id", "tool_configs",
            "approval_policy", "behavior_settings",
        ]
        for field_name in config_fields:
            with pytest.raises(AttributeError, match="Cannot modify frozen config field"):
                setattr(ctx, field_name, "new_value")

    def test_runtime_fields_mutable_after_freeze(self):
        """Runtime fields must remain writable after freeze()."""
        ctx = TurnContext()
        ctx.freeze()
        # These must NOT raise
        ctx.token_usage = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
        ctx.error = "something went wrong"
        ctx.status = TurnStatus.FAILED
        ctx.completed_at = time.time()
        ctx.tool_call_records = [{"tool": "test"}]
        ctx.latency_stats = {"total_ms": 100.0}
        assert ctx.error == "something went wrong"

    def test_complete_sets_status_and_timestamp(self):
        ctx = TurnContext(status=TurnStatus.RUNNING)
        ctx.freeze()
        before = time.time()
        ctx.complete(token_usage={"total_tokens": 42})
        after = time.time()
        assert ctx.status == TurnStatus.COMPLETED
        assert ctx.completed_at is not None
        assert before <= ctx.completed_at <= after
        assert ctx.token_usage["total_tokens"] == 42

    def test_fail_sets_error_and_status(self):
        ctx = TurnContext(status=TurnStatus.RUNNING)
        ctx.freeze()
        ctx.fail("timeout")
        assert ctx.status == TurnStatus.FAILED
        assert ctx.error == "timeout"
        assert ctx.completed_at is not None

    def test_round_trip_serialization(self):
        """to_dict → from_dict must preserve all fields."""
        ctx = TurnContext(
            session_id="sess-1", turn_number=3, model="claude-3",
            provider="anthropic", temperature=0.7, max_tokens=4096,
            tool_configs=["read_file", "write_file"],
            approval_policy="auto", behavior_settings={"verbose": True},
        )
        ctx.token_usage = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
        ctx.error = "test error"
        ctx.complete()

        data = ctx.to_dict()
        restored = TurnContext.from_dict(data)

        assert restored.session_id == ctx.session_id
        assert restored.turn_number == ctx.turn_number
        assert restored.model == ctx.model
        assert restored.temperature == ctx.temperature
        assert restored.tool_configs == ctx.tool_configs
        assert restored.token_usage == ctx.token_usage
        assert restored.status == TurnStatus.COMPLETED

    def test_double_complete_overwrites_timestamp(self):
        """Calling complete() twice should update completed_at."""
        ctx = TurnContext()
        ctx.complete()
        first_ts = ctx.completed_at
        time.sleep(0.01)
        ctx.complete()
        assert ctx.completed_at >= first_ts


# ======================================================================
# 10.3 — Session Serialization
# ======================================================================

class TestSessionSerialization:
    """Catches: data loss on round-trip, crash on old data without new fields."""

    def test_round_trip_with_new_fields(self):
        session = Session(
            name="test", agent_id="agent-1",
            session_type=SessionType.DELEGATED,
            transcript_path="/tmp/transcript.jsonl",
        )
        data = session.to_dict()
        restored = Session.from_dict(data)
        assert restored.agent_id == "agent-1"
        assert restored.session_type == SessionType.DELEGATED
        assert restored.transcript_path == "/tmp/transcript.jsonl"

    def test_backward_compat_missing_new_fields(self):
        """Old data without agent_id/session_type/transcript_path must not crash."""
        old_data = {
            "id": "old-session",
            "parent_id": None,
            "name": "legacy",
            "status": "active",
            "created_at": datetime.now().timestamp(),
            "updated_at": datetime.now().timestamp(),
            "ended_at": None,
            "config_snapshot": {},
            "budget": {"total": 128000, "reserved": 8192, "used": 0, "available": 119808},
            "turn_count": 5,
            "total_duration_ms": 0.0,
            "llm_call_count": 0,
            "tool_call_count": 0,
            "error_count": 0,
            "working_dir": "/tmp",
            "session_dir": "/tmp/session",
        }
        session = Session.from_dict(old_data)
        assert session.agent_id is None
        assert session.session_type == SessionType.PRIMARY
        assert session.transcript_path == ""

    def test_session_type_enum_serialization(self):
        for st in SessionType:
            session = Session(session_type=st)
            data = session.to_dict()
            assert data["session_type"] == st.value
            restored = Session.from_dict(data)
            assert restored.session_type == st

    def test_index_entry_from_session(self):
        session = Session(
            name="test", status=SessionStatus.ACTIVE,
            agent_id="agent-1", session_type=SessionType.DELEGATED,
        )
        entry = SessionIndexEntry.from_session(session, preview="hello world")
        assert entry.session_id == session.id
        assert entry.status == "active"
        assert entry.preview == "hello world"
        assert entry.agent_id == "agent-1"
        assert entry.session_type == "delegated"

    def test_index_entry_round_trip(self):
        entry = SessionIndexEntry(
            session_id="s1", name="test", status="paused",
            created_at=1000.0, updated_at=2000.0, turn_count=5,
            preview="hi", working_dir="/tmp", session_dir="/tmp/s",
            agent_id="a1", session_type="primary",
        )
        data = entry.to_dict()
        restored = SessionIndexEntry.from_dict(data)
        assert restored.session_id == entry.session_id
        assert restored.turn_count == entry.turn_count
        assert restored.agent_id == entry.agent_id


# ======================================================================
# 10.4 — TokenBudget Edge Cases
# ======================================================================

class TestTokenBudgetEdgeCases:
    """Catches: division-by-zero, negative available, threshold off-by-one."""

    def test_zero_total_utilization_is_one(self):
        """total=0 means fully utilized (avoid division by zero)."""
        budget = TokenBudget(total=0, reserved=0, used=0)
        assert budget.utilization_rate == 1.0

    def test_used_exceeds_total_available_is_zero(self):
        """available must never be negative."""
        budget = TokenBudget(total=100, reserved=10, used=200)
        assert budget.available == 0

    def test_reserved_exceeds_total_utilization_is_one(self):
        budget = TokenBudget(total=100, reserved=200, used=0)
        assert budget.utilization_rate == 1.0

    def test_warning_threshold_boundary(self):
        """needs_warning at exactly 80% must return True."""
        # effective = 1000 - 0 = 1000; used = 800 → rate = 0.8
        budget = TokenBudget(total=1000, reserved=0, used=800, warning_threshold=0.8)
        assert budget.needs_warning() is True
        budget.used = 799
        assert budget.needs_warning() is False

    def test_compaction_threshold_boundary(self):
        """needs_compaction at exactly 90% must return True."""
        budget = TokenBudget(total=1000, reserved=0, used=900, compaction_threshold=0.9)
        assert budget.needs_compaction() is True
        budget.used = 899
        assert budget.needs_compaction() is False

    def test_is_exhausted(self):
        budget = TokenBudget(total=100, reserved=10, used=90)
        assert budget.is_exhausted() is True
        budget.used = 89
        assert budget.is_exhausted() is False

    def test_reset_used(self):
        budget = TokenBudget(total=1000, reserved=100, used=800)
        budget.reset_used(200)
        assert budget.used == 200
        assert budget.available == 700

    def test_budget_from_dict_ignores_computed_fields(self):
        """from_dict must not choke on 'available' (a computed property)."""
        data = {"total": 1000, "reserved": 100, "used": 500, "available": 400}
        budget = TokenBudget.from_dict(data)
        assert budget.total == 1000
        assert budget.used == 500


# ======================================================================
# Turn metadata
# ======================================================================

class TestTurnMetadata:
    """Catches: metadata field lost on serialization."""

    def test_turn_round_trip_with_metadata(self):
        turn = Turn(
            session_id="s1", turn_number=1, role="user",
            content="hello", metadata={"source": "cli", "tags": ["test"]},
        )
        data = turn.to_dict()
        assert data["metadata"] == {"source": "cli", "tags": ["test"]}
        restored = Turn.from_dict(data)
        assert restored.metadata == {"source": "cli", "tags": ["test"]}

    def test_turn_from_dict_defaults_metadata(self):
        """Old data without metadata must default to empty dict."""
        data = {
            "id": 1, "session_id": "s1", "turn_number": 1,
            "role": "user", "content": "hi", "tool_calls": None,
            "tool_call_id": None, "token_count": 0, "importance": 0.5,
            "latency_ms": 0.0, "created_at": datetime.now().timestamp(),
        }
        turn = Turn.from_dict(data)
        assert turn.metadata == {}
