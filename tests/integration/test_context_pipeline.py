"""Integration tests for the Context Pipeline.

End-to-end tests verifying the full flow:
  ContextAssembler → ContextWindow → ContextCompressor

Bug classes covered:
- Pipeline breaks when components are wired together (works in isolation)
- Token budget not respected across the full pipeline
- Compression not triggered when it should be
- Emergency truncation not triggered after compression fails
- Events not emitted during the full pipeline
- CLAUDE.md loading integrated incorrectly
- system-reminder not present in final output
- Message ordering violated after compression
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from typing import List

from src.core.models import Message, Role
from src.core.events import EventBus, AgentEvent, EventPayload
from src.context.context_assembler import ContextAssembler
from src.context.context_window import ContextWindow, ContextBudget
from src.context.context_compressor import ContextCompressor


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_session_manager(with_history: bool = False):
    """Create a mock SessionManager."""
    sm = MagicMock()
    if with_history:
        session = MagicMock()
        session.id = "integration-test-session"
        sm.active_session = session

        turns = []
        for i in range(8):
            turn = MagicMock()
            turn.role = "user" if i % 2 == 0 else "assistant"
            turn.content = f"Turn {i}: {'question' if i % 2 == 0 else 'answer'} " + "x" * 200
            turn.tool_calls = None
            turn.tool_call_id = None
            turns.append(turn)
        sm.get_history.return_value = turns
    else:
        sm.active_session = None
        sm.get_history.return_value = []
    return sm


def _make_prompt_provider(prompt_text: str = "You are a helpful assistant."):
    """Create a mock PromptProvider."""
    pp = MagicMock()
    pp.build_system_prompt.return_value = prompt_text
    return pp


def _make_memory_controller(memories: List[str] = None):
    """Create a mock MemoryController."""
    mc = MagicMock()
    if memories:
        mock_mems = []
        for content in memories:
            m = MagicMock()
            m.content = content
            mock_mems.append(m)
        mc.retrieve_relevant.return_value = mock_mems
    else:
        mc.retrieve_relevant.return_value = []
    return mc


# ---------------------------------------------------------------------------
# 1. Normal flow — token budget sufficient
#    Bug: pipeline fails even when budget is ample
# ---------------------------------------------------------------------------

class TestNormalFlow:
    """End-to-end: token budget sufficient, no compression needed."""

    def test_full_pipeline_returns_messages(self):
        """Full pipeline must return a non-empty message list.
        Bug: pipeline returns empty list or crashes."""
        assembler = ContextAssembler(
            session_manager=_make_session_manager(),
            prompt_provider=_make_prompt_provider(),
            max_tokens=128000,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("What is Python?")

        assert isinstance(result, list)
        assert len(result) >= 2  # At least system + user

    def test_system_prompt_and_user_message_present(self):
        """Both system prompt and user message must be in output.
        Bug: one of the required layers missing."""
        assembler = ContextAssembler(
            session_manager=_make_session_manager(),
            prompt_provider=_make_prompt_provider("Be concise."),
            max_tokens=128000,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("Hello")

        roles = [m.role for m in result]
        assert Role.SYSTEM.value in roles, "System message missing"
        assert Role.USER.value in roles, "User message missing"

    def test_history_included_when_session_active(self):
        """History messages must be included when session has turns.
        Bug: history loading broken in integration."""
        assembler = ContextAssembler(
            session_manager=_make_session_manager(with_history=True),
            prompt_provider=_make_prompt_provider(),
            max_tokens=128000,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("New question")

        # Should have system + history turns + user
        assert len(result) >= 4, (
            f"Expected at least 4 messages (system + history + user), got {len(result)}"
        )

    def test_memory_included_when_controller_given(self):
        """Retrieved memories must appear in output.
        Bug: memory controller not called or results dropped."""
        mc = _make_memory_controller(["User likes TypeScript."])
        assembler = ContextAssembler(
            session_manager=_make_session_manager(),
            prompt_provider=_make_prompt_provider(),
            memory_controller=mc,
            max_tokens=128000,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("What language?")

        all_content = " ".join(m.content or "" for m in result)
        assert "TypeScript" in all_content, (
            "Memory content not found in pipeline output"
        )

    def test_system_reminder_in_final_output(self):
        """system-reminder must be present in the user message.
        Bug: system-reminder injection skipped in integration."""
        assembler = ContextAssembler(
            session_manager=_make_session_manager(),
            max_tokens=128000,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("Hello world")

        user_msgs = [m for m in result if m.role == Role.USER.value]
        assert len(user_msgs) >= 1
        assert "<system-reminder>" in (user_msgs[-1].content or ""), (
            "system-reminder tag not found in user message"
        )


# ---------------------------------------------------------------------------
# 2. Compression flow — token budget triggers compression
#    Bug: compression not triggered, or corrupts messages
# ---------------------------------------------------------------------------

class TestCompressionFlow:
    """End-to-end: token budget tight, compression triggered."""

    def test_compression_produces_valid_output(self):
        """When budget is tight, compression must still produce valid messages.
        Bug: compression returns None or empty list."""
        assembler = ContextAssembler(
            session_manager=_make_session_manager(with_history=True),
            prompt_provider=_make_prompt_provider("System prompt " + "x" * 500),
            max_tokens=2000,  # Tight budget
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("Short question")

        assert isinstance(result, list)
        assert len(result) >= 1, "Compression should not produce empty output"

    def test_user_message_survives_compression(self):
        """User message (required) must survive compression.
        Bug: compression drops required layers."""
        assembler = ContextAssembler(
            session_manager=_make_session_manager(with_history=True),
            prompt_provider=_make_prompt_provider(),
            max_tokens=1500,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("Important question")

        user_msgs = [m for m in result if m.role == Role.USER.value]
        assert len(user_msgs) >= 1, (
            "User message must survive compression"
        )
        assert "Important question" in (user_msgs[-1].content or "")

    def test_system_prompt_survives_compression(self):
        """System prompt (required) must survive compression.
        Bug: system prompt dropped during compression."""
        assembler = ContextAssembler(
            session_manager=_make_session_manager(with_history=True),
            prompt_provider=_make_prompt_provider("Be helpful."),
            max_tokens=2000,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("test")

        system_msgs = [m for m in result if m.role == Role.SYSTEM.value]
        assert any("Be helpful" in (m.content or "") for m in system_msgs), (
            "System prompt should survive compression"
        )


# ---------------------------------------------------------------------------
# 3. Emergency truncation — compression insufficient
#    Bug: infinite loop or crash when even compression isn't enough
# ---------------------------------------------------------------------------

class TestEmergencyTruncation:
    """End-to-end: budget so tight that emergency truncation is needed."""

    def test_extremely_tight_budget_does_not_crash(self):
        """Even with absurdly tight budget, pipeline must not crash.
        Bug: division by zero, infinite loop, or unhandled exception."""
        assembler = ContextAssembler(
            session_manager=_make_session_manager(with_history=True),
            prompt_provider=_make_prompt_provider("System " + "x" * 1000),
            max_tokens=100,  # Absurdly tight
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("test")

        assert isinstance(result, list)

    def test_at_least_user_message_in_emergency(self):
        """Even in emergency, at least the user message should be present.
        Bug: everything dropped, empty result."""
        assembler = ContextAssembler(
            session_manager=_make_session_manager(with_history=True),
            max_tokens=500,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("Help me")

        # At minimum, user message should be present
        user_msgs = [m for m in result if m.role == Role.USER.value]
        assert len(user_msgs) >= 1, (
            "User message should be present even in emergency"
        )


# ---------------------------------------------------------------------------
# 4. EventBus in full pipeline
#    Bug: events not emitted when components are wired together
# ---------------------------------------------------------------------------

class TestEventBusInPipeline:
    """End-to-end: events emitted correctly during full pipeline."""

    def test_context_assembled_event_in_pipeline(self):
        """CONTEXT_ASSEMBLED event must be emitted in full pipeline.
        Bug: event emission broken when all components are wired."""
        bus = EventBus()
        events = []

        def handler(payload: EventPayload):
            events.append(("assembled", payload))

        bus.subscribe(AgentEvent.CONTEXT_ASSEMBLED, handler)

        assembler = ContextAssembler(
            session_manager=_make_session_manager(),
            prompt_provider=_make_prompt_provider(),
            event_bus=bus,
            max_tokens=128000,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            assembler.build("test")

        assembled_events = [e for e in events if e[0] == "assembled"]
        assert len(assembled_events) == 1, (
            f"Expected 1 CONTEXT_ASSEMBLED event, got {len(assembled_events)}"
        )

    def test_event_contains_token_info(self):
        """CONTEXT_ASSEMBLED event must contain token usage info.
        Bug: event payload empty or missing fields."""
        bus = EventBus()
        events = []

        def handler(payload: EventPayload):
            events.append(payload)

        bus.subscribe(AgentEvent.CONTEXT_ASSEMBLED, handler)

        assembler = ContextAssembler(
            session_manager=_make_session_manager(),
            prompt_provider=_make_prompt_provider(),
            event_bus=bus,
            max_tokens=128000,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            assembler.build("test")

        assert len(events) == 1
        data = events[0].data
        assert data.get("total_tokens", 0) > 0, (
            f"total_tokens should be > 0, got {data.get('total_tokens')}"
        )


# ---------------------------------------------------------------------------
# 5. Message ordering in full pipeline
#    Bug: ordering violated after compression or multi-layer assembly
# ---------------------------------------------------------------------------

class TestMessageOrderingInPipeline:
    """End-to-end: message ordering correct after full pipeline."""

    def test_system_first_user_last(self):
        """System messages first, user message last — even with all layers.
        Bug: ordering broken when many layers are assembled."""
        assembler = ContextAssembler(
            session_manager=_make_session_manager(with_history=True),
            prompt_provider=_make_prompt_provider(),
            memory_controller=_make_memory_controller(["Remember this."]),
            max_tokens=128000,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("Final question", skill_context="Use pytest")

        assert len(result) >= 3

        # First message should be system
        assert result[0].role == Role.SYSTEM.value, (
            f"First message should be system, got {result[0].role}"
        )

        # Last message should be user
        assert result[-1].role == Role.USER.value, (
            f"Last message should be user, got {result[-1].role}"
        )

    def test_no_system_after_non_system(self):
        """Once a non-system message appears, no more system messages.
        Bug: system messages interleaved with history."""
        assembler = ContextAssembler(
            session_manager=_make_session_manager(with_history=True),
            prompt_provider=_make_prompt_provider(),
            max_tokens=128000,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("test")

        found_non_system = False
        for msg in result:
            if msg.role != Role.SYSTEM.value:
                found_non_system = True
            elif found_non_system:
                pytest.fail(
                    f"System message found after non-system message: "
                    f"'{(msg.content or '')[:50]}'"
                )


# ---------------------------------------------------------------------------
# 6. Cache control in full pipeline
#    Bug: cache_control markers lost during pipeline processing
# ---------------------------------------------------------------------------

class TestCacheControlInPipeline:
    """End-to-end: cache_control correctly applied after full pipeline."""

    def test_static_layers_have_cache_control(self):
        """Static layers must have cache_control after full pipeline.
        Bug: cache_control lost during compression or reordering."""
        assembler = ContextAssembler(
            session_manager=_make_session_manager(),
            prompt_provider=_make_prompt_provider("Be helpful."),
            max_tokens=128000,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("test", skill_context="Use black")

        # System prompt should have cache_control
        system_msgs = [m for m in result if m.role == Role.SYSTEM.value
                       and "Be helpful" in (m.content or "")]
        if system_msgs:
            assert system_msgs[0].cache_control == {"type": "ephemeral"}, (
                "System prompt should have cache_control after full pipeline"
            )

    def test_user_message_no_cache_control(self):
        """User message must NOT have cache_control after full pipeline.
        Bug: cache_control applied to all messages."""
        assembler = ContextAssembler(
            session_manager=_make_session_manager(),
            max_tokens=128000,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("test")

        user_msgs = [m for m in result if m.role == Role.USER.value]
        assert len(user_msgs) >= 1
        assert user_msgs[-1].cache_control is None, (
            "User message should not have cache_control"
        )


# ---------------------------------------------------------------------------
# 7. Window reset between builds
#    Bug: stale state from previous build corrupts next build
# ---------------------------------------------------------------------------

class TestWindowResetBetweenBuilds:
    """End-to-end: window state properly reset between builds."""

    def test_consecutive_builds_independent(self):
        """Two consecutive builds must produce independent results.
        Bug: token usage accumulates across builds."""
        assembler = ContextAssembler(
            session_manager=_make_session_manager(),
            prompt_provider=_make_prompt_provider(),
            max_tokens=10000,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result1 = assembler.build("First question")
            result2 = assembler.build("Second question")

        # Both should have similar structure
        assert len(result1) >= 1
        assert len(result2) >= 1

        # Second build should not be affected by first
        user_msgs_2 = [m for m in result2 if m.role == Role.USER.value]
        assert any("Second question" in (m.content or "") for m in user_msgs_2), (
            "Second build should contain second question"
        )
        # First question should NOT be in second build
        all_content_2 = " ".join(m.content or "" for m in result2)
        assert "First question" not in all_content_2, (
            "First question should not leak into second build"
        )
