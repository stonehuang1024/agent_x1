"""Tests for ContextAssembler — 8-layer context injection model.

Bug classes covered:
- Layer missing from build output (e.g. Global CLAUDE.md layer never constructed)
- Layer priority ordering wrong (user message not last, system not first)
- cache_control not set on static layers (breaks Prompt Caching)
- cache_control incorrectly set on dynamic layers (wastes cache slots)
- CONTEXT_ASSEMBLED event not emitted or missing required fields
- CONTEXT_COMPRESSED event not emitted when compression triggers
- Optional dependency (MemoryController=None) causes crash instead of skip
- Required layer over budget not compressed, just dropped
- system-reminder not injected into user message
- Message reordering puts user message before history
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
from typing import List

from src.core.models import Message, Role
from src.core.events import EventBus, AgentEvent, EventPayload
from src.context.context_assembler import ContextAssembler, ContextLayer
from src.context.context_window import ContextBudget
from src.context.context_compressor import ContextCompressor


# ---------------------------------------------------------------------------
# Fixtures — mock dependencies
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_session_manager():
    """SessionManager mock with no active session (no history)."""
    sm = MagicMock()
    sm.active_session = None
    sm.get_history.return_value = []
    return sm


@pytest.fixture
def mock_session_manager_with_history():
    """SessionManager mock with active session and history."""
    sm = MagicMock()
    session = MagicMock()
    session.id = "test-session-id"
    sm.active_session = session

    # Create mock Turn objects
    turn1 = MagicMock()
    turn1.role = "user"
    turn1.content = "Previous question"
    turn1.tool_calls = None
    turn1.tool_call_id = None

    turn2 = MagicMock()
    turn2.role = "assistant"
    turn2.content = "Previous answer"
    turn2.tool_calls = None
    turn2.tool_call_id = None

    sm.get_history.return_value = [turn1, turn2]
    return sm


@pytest.fixture
def mock_prompt_provider():
    """PromptProvider that returns a fixed system prompt."""
    pp = MagicMock()
    pp.build_system_prompt.return_value = "You are a helpful coding assistant."
    return pp


@pytest.fixture
def mock_memory_controller():
    """MemoryController that returns mock memories."""
    mc = MagicMock()
    mem1 = MagicMock()
    mem1.content = "User prefers Python over JavaScript."
    mc.retrieve_relevant.return_value = [mem1]
    return mc


@pytest.fixture
def event_bus():
    """Fresh EventBus instance."""
    return EventBus()


# ---------------------------------------------------------------------------
# 1. Layer construction — all 8 layers present
#    Bug: a layer is never constructed, missing from output
# ---------------------------------------------------------------------------

class TestLayerConstruction:
    """Catches: layers missing from build output."""

    def test_system_prompt_layer_present(self, mock_session_manager, mock_prompt_provider):
        """Layer 1 (system prompt) must be present when PromptProvider is given.
        Bug: system prompt layer skipped."""
        assembler = ContextAssembler(
            session_manager=mock_session_manager,
            prompt_provider=mock_prompt_provider,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("Hello")

        system_msgs = [m for m in result if m.role == Role.SYSTEM.value]
        assert any("helpful coding assistant" in (m.content or "") for m in system_msgs), (
            "System prompt content not found in output. "
            f"System messages: {[m.content[:50] for m in system_msgs]}"
        )

    def test_user_message_layer_present(self, mock_session_manager):
        """Layer 8 (user message) must always be present.
        Bug: user message dropped."""
        assembler = ContextAssembler(session_manager=mock_session_manager)
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("Fix the login bug")

        user_msgs = [m for m in result if m.role == Role.USER.value]
        assert len(user_msgs) >= 1, "User message layer missing from output"
        # User message should contain the original input
        assert any("Fix the login bug" in (m.content or "") for m in user_msgs), (
            "Original user input not found in user message"
        )

    def test_system_reminder_injected_into_user_message(self, mock_session_manager):
        """User message must contain <system-reminder> tags.
        Bug: system-reminder injection skipped."""
        assembler = ContextAssembler(session_manager=mock_session_manager)
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("Hello world")

        user_msgs = [m for m in result if m.role == Role.USER.value]
        assert len(user_msgs) >= 1
        user_content = user_msgs[-1].content or ""
        assert "<system-reminder>" in user_content, (
            f"<system-reminder> tag not found in user message. "
            f"Content starts with: '{user_content[:80]}'"
        )
        assert "</system-reminder>" in user_content
        assert "Hello world" in user_content

    def test_skills_layer_present_when_provided(self, mock_session_manager):
        """Layer 5 (skills) must be present when skill_context is given.
        Bug: skill_context parameter ignored."""
        assembler = ContextAssembler(session_manager=mock_session_manager)
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("test", skill_context="Use pytest for testing")

        system_msgs = [m for m in result if m.role == Role.SYSTEM.value]
        assert any("pytest for testing" in (m.content or "") for m in system_msgs), (
            "Skill context not found in output"
        )

    def test_memory_layer_present_when_controller_given(
        self, mock_session_manager, mock_memory_controller
    ):
        """Layer 6 (memory) must be present when MemoryController is given.
        Bug: memory_controller.retrieve_relevant never called."""
        assembler = ContextAssembler(
            session_manager=mock_session_manager,
            memory_controller=mock_memory_controller,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("What language should I use?")

        mock_memory_controller.retrieve_relevant.assert_called_once()
        system_msgs = [m for m in result if m.role == Role.SYSTEM.value]
        assert any("Python over JavaScript" in (m.content or "") for m in system_msgs), (
            "Retrieved memory content not found in output"
        )

    def test_history_layer_present_when_session_active(
        self, mock_session_manager_with_history
    ):
        """Layer 7 (history) must be present when session has history.
        Bug: history loading skipped or returns empty."""
        assembler = ContextAssembler(
            session_manager=mock_session_manager_with_history,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("New question")

        contents = [m.content or "" for m in result]
        assert any("Previous question" in c for c in contents), (
            "History messages not found in output"
        )


# ---------------------------------------------------------------------------
# 2. Graceful degradation — None dependencies
#    Bug: None dependency causes AttributeError instead of skip
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    """Catches: None dependencies causing crashes."""

    def test_no_prompt_provider(self, mock_session_manager):
        """PromptProvider=None must not crash; system prompt layer skipped.
        Bug: AttributeError on None.build_system_prompt()."""
        assembler = ContextAssembler(
            session_manager=mock_session_manager,
            prompt_provider=None,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("Hello")

        # Should still have user message
        assert any(m.role == Role.USER.value for m in result)

    def test_no_memory_controller(self, mock_session_manager):
        """MemoryController=None must not crash; memory layer skipped.
        Bug: AttributeError on None.retrieve_relevant()."""
        assembler = ContextAssembler(
            session_manager=mock_session_manager,
            memory_controller=None,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("Hello")

        assert any(m.role == Role.USER.value for m in result)

    def test_no_event_bus(self, mock_session_manager):
        """EventBus=None must not crash; events silently skipped.
        Bug: AttributeError on None.emit()."""
        assembler = ContextAssembler(
            session_manager=mock_session_manager,
            event_bus=None,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("Hello")

        assert any(m.role == Role.USER.value for m in result)

    def test_no_project_path(self, mock_session_manager):
        """project_path=None must not crash; sub-dir layer skipped.
        Bug: Path operations on None."""
        assembler = ContextAssembler(
            session_manager=mock_session_manager,
            project_path=None,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("Hello")

        assert any(m.role == Role.USER.value for m in result)


# ---------------------------------------------------------------------------
# 3. cache_control — static vs dynamic layers
#    Bug: cache_control set on dynamic layers or missing from static
# ---------------------------------------------------------------------------

class TestCacheControl:
    """Catches: cache_control marking bugs."""

    def test_system_prompt_has_cache_control(
        self, mock_session_manager, mock_prompt_provider
    ):
        """System prompt (static, cacheable) must have cache_control set.
        Bug: cache_control not applied to static layers."""
        assembler = ContextAssembler(
            session_manager=mock_session_manager,
            prompt_provider=mock_prompt_provider,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("test")

        system_msgs = [m for m in result if m.role == Role.SYSTEM.value
                       and "helpful coding assistant" in (m.content or "")]
        assert len(system_msgs) >= 1, "System prompt not found"
        assert system_msgs[0].cache_control == {"type": "ephemeral"}, (
            f"System prompt cache_control should be {{'type': 'ephemeral'}}, "
            f"got {system_msgs[0].cache_control}"
        )

    def test_user_message_no_cache_control(self, mock_session_manager):
        """User message (dynamic) must NOT have cache_control.
        Bug: cache_control applied to all messages indiscriminately."""
        assembler = ContextAssembler(session_manager=mock_session_manager)
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("test")

        user_msgs = [m for m in result if m.role == Role.USER.value]
        assert len(user_msgs) >= 1
        assert user_msgs[-1].cache_control is None, (
            f"User message should NOT have cache_control, "
            f"got {user_msgs[-1].cache_control}"
        )

    def test_skill_layer_has_cache_control(self, mock_session_manager):
        """Skills layer (static, cacheable) must have cache_control.
        Bug: skills treated as dynamic."""
        assembler = ContextAssembler(session_manager=mock_session_manager)
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("test", skill_context="Use black formatter")

        skill_msgs = [m for m in result if m.role == Role.SYSTEM.value
                      and "black formatter" in (m.content or "")]
        assert len(skill_msgs) >= 1
        assert skill_msgs[0].cache_control == {"type": "ephemeral"}, (
            f"Skills layer should have cache_control, got {skill_msgs[0].cache_control}"
        )


# ---------------------------------------------------------------------------
# 4. Message ordering — system first, user last
#    Bug: user message appears before history, or system after history
# ---------------------------------------------------------------------------

class TestMessageOrdering:
    """Catches: message ordering violations."""

    def test_system_messages_come_first(
        self, mock_session_manager, mock_prompt_provider
    ):
        """System messages must be at the beginning of the result.
        Bug: system messages interleaved with history."""
        assembler = ContextAssembler(
            session_manager=mock_session_manager,
            prompt_provider=mock_prompt_provider,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("test")

        if not result:
            pytest.skip("No messages returned")

        # Find first non-system message
        first_non_system_idx = None
        for i, msg in enumerate(result):
            if msg.role != Role.SYSTEM.value:
                first_non_system_idx = i
                break

        if first_non_system_idx is not None:
            # All messages before first_non_system should be system
            for i in range(first_non_system_idx):
                assert result[i].role == Role.SYSTEM.value, (
                    f"Message at index {i} should be system, got {result[i].role}"
                )

    def test_user_message_is_last(self, mock_session_manager):
        """The user message (with system-reminder) must be the last message.
        Bug: user message placed before history or tool outputs."""
        assembler = ContextAssembler(session_manager=mock_session_manager)
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("My question")

        assert len(result) >= 1
        last_msg = result[-1]
        assert last_msg.role == Role.USER.value, (
            f"Last message should be user, got {last_msg.role}. "
            f"Content: '{(last_msg.content or '')[:50]}'"
        )
        assert "My question" in (last_msg.content or ""), (
            "Last message should contain the user's input"
        )

    def test_history_between_system_and_user(
        self, mock_session_manager_with_history, mock_prompt_provider
    ):
        """History messages must appear between system and user messages.
        Bug: history placed after user message."""
        assembler = ContextAssembler(
            session_manager=mock_session_manager_with_history,
            prompt_provider=mock_prompt_provider,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("New question")

        if len(result) < 3:
            pytest.skip("Not enough messages for ordering test")

        # Last message should be user
        assert result[-1].role == Role.USER.value
        # First message should be system
        assert result[0].role == Role.SYSTEM.value


# ---------------------------------------------------------------------------
# 5. EventBus integration
#    Bug: events not emitted, or missing required fields
# ---------------------------------------------------------------------------

class TestEventBusIntegration:
    """Catches: events not emitted or with wrong payload."""

    def test_context_assembled_event_emitted(self, mock_session_manager, event_bus):
        """CONTEXT_ASSEMBLED event must be emitted after build().
        Bug: event emission code never reached."""
        events_received = []

        def handler(payload: EventPayload):
            events_received.append(payload)

        event_bus.subscribe(AgentEvent.CONTEXT_ASSEMBLED, handler)

        assembler = ContextAssembler(
            session_manager=mock_session_manager,
            event_bus=event_bus,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            assembler.build("test")

        assert len(events_received) == 1, (
            f"Expected 1 CONTEXT_ASSEMBLED event, got {len(events_received)}"
        )

    def test_context_assembled_event_has_required_fields(
        self, mock_session_manager, event_bus
    ):
        """CONTEXT_ASSEMBLED event must contain total_tokens and layer_count.
        Bug: event payload missing required fields."""
        events_received = []

        def handler(payload: EventPayload):
            events_received.append(payload)

        event_bus.subscribe(AgentEvent.CONTEXT_ASSEMBLED, handler)

        assembler = ContextAssembler(
            session_manager=mock_session_manager,
            event_bus=event_bus,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            assembler.build("test")

        assert len(events_received) == 1
        data = events_received[0].data
        assert "total_tokens" in data, (
            f"CONTEXT_ASSEMBLED event missing 'total_tokens'. Data: {data}"
        )
        assert "layer_count" in data, (
            f"CONTEXT_ASSEMBLED event missing 'layer_count'. Data: {data}"
        )
        assert data["total_tokens"] > 0, "total_tokens should be > 0"
        assert data["layer_count"] >= 1, "layer_count should be >= 1"

    def test_no_event_bus_does_not_crash(self, mock_session_manager):
        """When event_bus is None, build() must not crash.
        Bug: unconditional event_bus.emit() call."""
        assembler = ContextAssembler(
            session_manager=mock_session_manager,
            event_bus=None,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            # Should not raise
            result = assembler.build("test")

        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 6. Token budget — layer skipping and compression
#    Bug: non-required layer not skipped, or required layer dropped
# ---------------------------------------------------------------------------

class TestTokenBudget:
    """Catches: budget management bugs."""

    def test_optional_layer_skipped_when_over_budget(self, mock_session_manager):
        """Non-required layers must be skipped when budget is insufficient.
        Bug: over-budget optional layer force-included, breaking budget."""
        assembler = ContextAssembler(
            session_manager=mock_session_manager,
            max_tokens=200,  # Very tight budget
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build(
                "test",
                skill_context="A" * 10000,  # Huge skill context
            )

        # User message (required) should still be present
        user_msgs = [m for m in result if m.role == Role.USER.value]
        assert len(user_msgs) >= 1, "Required user message should be present"

    def test_window_reset_called_each_build(self, mock_session_manager):
        """window.reset() must be called at the start of each build().
        Bug: stale token usage from previous build corrupts budget."""
        assembler = ContextAssembler(
            session_manager=mock_session_manager,
            max_tokens=10000,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            # First build
            assembler.build("first call")
            usage_after_first = assembler.window._current_usage

            # Second build should reset
            assembler.build("second call")

        # After second build, usage should be similar to first
        # (not accumulated from both builds)
        assert assembler.window._current_usage <= usage_after_first * 1.5, (
            f"Token usage after second build ({assembler.window._current_usage}) "
            f"is much higher than first ({usage_after_first}). "
            f"window.reset() may not be called."
        )


# ---------------------------------------------------------------------------
# 7. Edge cases
#    Bug: empty input, very long input, special characters
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Catches: crashes on unusual inputs."""

    def test_empty_user_input(self, mock_session_manager):
        """Empty user input must not crash.
        Bug: empty string causes downstream issues."""
        assembler = ContextAssembler(session_manager=mock_session_manager)
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("")

        assert isinstance(result, list)
        assert len(result) >= 1  # At least user message

    def test_very_long_user_input(self, mock_session_manager):
        """Very long user input must not crash (may be truncated by budget).
        Bug: memory error or infinite loop."""
        assembler = ContextAssembler(
            session_manager=mock_session_manager,
            max_tokens=50000,
        )
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build("x" * 100000)

        assert isinstance(result, list)

    def test_special_characters_in_input(self, mock_session_manager):
        """Special characters must not crash the assembler.
        Bug: XML parsing of system-reminder breaks on special chars."""
        assembler = ContextAssembler(session_manager=mock_session_manager)
        with patch("src.memory.project_memory.ProjectMemoryLoader"):
            result = assembler.build('<script>alert("xss")</script>\n\x00\n🔥')

        assert isinstance(result, list)
        user_msgs = [m for m in result if m.role == Role.USER.value]
        assert len(user_msgs) >= 1


# ---------------------------------------------------------------------------
# 8. ContextLayer dataclass
#    Bug: cacheable field missing or wrong default
# ---------------------------------------------------------------------------

class TestContextLayerModel:
    """Catches: ContextLayer field regressions."""

    def test_cacheable_defaults_to_false(self):
        """ContextLayer.cacheable must default to False.
        Bug: defaults to True, causing all layers to be cached."""
        layer = ContextLayer(name="test", priority=50)
        assert layer.cacheable is False, (
            f"cacheable should default to False, got {layer.cacheable}"
        )

    def test_required_defaults_to_false(self):
        """ContextLayer.required must default to False."""
        layer = ContextLayer(name="test", priority=50)
        assert layer.required is False

    def test_messages_defaults_to_empty_list(self):
        """ContextLayer.messages must default to empty list."""
        layer = ContextLayer(name="test", priority=50)
        assert layer.messages == []


# ---------------------------------------------------------------------------
# 9. rebuild() — proactive tool output truncation
#    Bug: large tool outputs repeated verbatim on every LLM call
# ---------------------------------------------------------------------------

def _make_assembler_with_static_prefix(mock_session_manager, max_tokens=128000):
    """Helper: create assembler and call build() to populate static prefix."""
    assembler = ContextAssembler(
        session_manager=mock_session_manager,
        max_tokens=max_tokens,
    )
    with patch("src.memory.project_memory.ProjectMemoryLoader"):
        assembler.build("initial query")
    return assembler


class TestRebuildToolOutputTruncation:
    """Catches: large tool outputs repeated verbatim on every rebuild() call,
    wasting tokens and causing the pattern:
      step1: [user]
      step2: [user, asst, LARGE_TOOL_RESULT]
      step3: [user, asst, LARGE_TOOL_RESULT, asst, LARGE_TOOL_RESULT_2]
    where LARGE_TOOL_RESULT is sent in full every time.
    """

    def test_old_large_tool_output_is_truncated(self, mock_session_manager):
        """Old tool results exceeding max_tool_output_length must be truncated.
        Bug: rebuild() passes all turn_messages verbatim without truncation,
        causing a 48KB tool result to be sent on every subsequent LLM call."""
        assembler = _make_assembler_with_static_prefix(mock_session_manager)

        # Simulate a realistic multi-step scenario where the first tool result
        # (a large PDF read) is followed by several more tool calls.
        # With default keep_recent=6, we need >6 messages after the large result
        # to push it outside the recent window.
        large_content = "x" * 10000  # Well above default max_tool_output_length (2000)
        turn_messages = [
            Message.user("Do something"),
            Message.assistant(content="Let me read that file", tool_calls=[
                {"id": "call_1", "function": {"name": "read_file", "arguments": "{}"}}
            ]),
            Message.tool(content=large_content, tool_call_id="call_1", name="read_file"),
            # Add enough subsequent messages to push call_1 beyond keep_recent=6
            Message.assistant(content="Step 2", tool_calls=[
                {"id": "call_2", "function": {"name": "write_file", "arguments": "{}"}}
            ]),
            Message.tool(content="written", tool_call_id="call_2", name="write_file"),
            Message.assistant(content="Step 3", tool_calls=[
                {"id": "call_3", "function": {"name": "run_script", "arguments": "{}"}}
            ]),
            Message.tool(content="script output", tool_call_id="call_3", name="run_script"),
            Message.assistant(content="Step 4", tool_calls=[
                {"id": "call_4", "function": {"name": "analyze", "arguments": "{}"}}
            ]),
            Message.tool(content="analysis done", tool_call_id="call_4", name="analyze"),
            Message.assistant(content="Step 5", tool_calls=[
                {"id": "call_5", "function": {"name": "finalize", "arguments": "{}"}}
            ]),
            Message.tool(content="finalized", tool_call_id="call_5", name="finalize"),
        ]

        result = assembler.rebuild(turn_messages)

        # Find the old tool result (call_1) in the output
        tool_msgs = [m for m in result if m.tool_call_id == "call_1"]
        assert len(tool_msgs) == 1, "Old tool result should still be present"

        old_tool_content = tool_msgs[0].content or ""
        assert len(old_tool_content) < len(large_content), (
            f"Old tool result ({len(old_tool_content)} chars) was NOT truncated. "
            f"Original was {len(large_content)} chars. "
            f"This means every subsequent LLM call sends the full content, "
            f"wasting ~{len(large_content) // 4} tokens per call."
        )
        assert "truncated" in old_tool_content.lower(), (
            "Truncated tool output should contain a truncation marker"
        )

    def test_recent_tool_output_preserved_intact(self, mock_session_manager):
        """Recent tool results (within keep_recent window) must NOT be truncated.
        Bug: truncation applied to all tool results including the most recent,
        causing the LLM to lose context for the current step."""
        assembler = _make_assembler_with_static_prefix(mock_session_manager)
        # Use a compressor with keep_recent=4 for predictable boundary
        assembler.compressor = ContextCompressor(
            max_tool_output_length=2000,
            keep_recent=4,
        )

        large_recent = "RECENT_DATA_" + "y" * 8000
        turn_messages = [
            Message.user("Do something"),
            # Old messages (will be beyond keep_recent boundary)
            Message.assistant(content="Step 1", tool_calls=[
                {"id": "call_old", "function": {"name": "read_file", "arguments": "{}"}}
            ]),
            Message.tool(content="old_data_" + "x" * 5000, tool_call_id="call_old", name="read_file"),
            # Recent messages (within keep_recent=4 window)
            Message.assistant(content="Step 2", tool_calls=[
                {"id": "call_recent", "function": {"name": "read_file", "arguments": "{}"}}
            ]),
            Message.tool(content=large_recent, tool_call_id="call_recent", name="read_file"),
            Message.assistant(content="Now processing", tool_calls=[
                {"id": "call_3", "function": {"name": "analyze", "arguments": "{}"}}
            ]),
            Message.tool(content="analysis complete", tool_call_id="call_3", name="analyze"),
        ]

        result = assembler.rebuild(turn_messages)

        # The recent tool result should be intact
        recent_tool = [m for m in result if m.tool_call_id == "call_recent"]
        assert len(recent_tool) == 1
        assert recent_tool[0].content == large_recent, (
            f"Recent tool result was truncated! "
            f"Expected {len(large_recent)} chars, got {len(recent_tool[0].content or '')}. "
            f"Bug: truncation boundary is wrong, recent messages are being truncated."
        )

    def test_token_savings_are_significant(self, mock_session_manager):
        """rebuild() must produce measurably fewer tokens than raw turn_messages.
        Bug: truncation logic exists but threshold is too high to trigger,
        or truncation produces output nearly as large as the original."""
        assembler = _make_assembler_with_static_prefix(mock_session_manager)

        # Simulate 5 iterations of tool calls, each with a large result
        turn_messages = [Message.user("Complex task")]
        for i in range(5):
            turn_messages.append(Message.assistant(
                content=f"Step {i+1}",
                tool_calls=[{"id": f"call_{i}", "function": {"name": "read_file", "arguments": "{}"}}]
            ))
            turn_messages.append(Message.tool(
                content=f"result_{i}_" + "data" * 2000,  # ~8000 chars each
                tool_call_id=f"call_{i}",
                name="read_file",
            ))

        original_tokens = assembler.window.estimate_tokens(turn_messages)
        result = assembler.rebuild(turn_messages)

        # Extract only the turn portion (exclude static prefix)
        turn_portion = [m for m in result if not getattr(m, 'cache_control', None)]
        rebuilt_tokens = assembler.window.estimate_tokens(turn_portion)

        savings_pct = 1.0 - (rebuilt_tokens / max(original_tokens, 1))
        assert savings_pct > 0.2, (
            f"Token savings only {savings_pct:.1%}. "
            f"Original: {original_tokens}, Rebuilt: {rebuilt_tokens}. "
            f"Expected >20% savings from truncating old tool outputs."
        )

    def test_small_tool_outputs_not_truncated(self, mock_session_manager):
        """Tool outputs smaller than max_tool_output_length must not be truncated.
        Bug: all tool outputs truncated regardless of size, losing useful
        short results like 'File created successfully'."""
        assembler = _make_assembler_with_static_prefix(mock_session_manager)

        small_content = "File created at /tmp/test.py"
        turn_messages = [
            Message.user("Create a file"),
            Message.assistant(content="Creating", tool_calls=[
                {"id": "call_1", "function": {"name": "write_file", "arguments": "{}"}}
            ]),
            Message.tool(content=small_content, tool_call_id="call_1", name="write_file"),
            # Add enough recent messages to push call_1 beyond keep_recent
            Message.assistant(content="Step 2", tool_calls=[
                {"id": "call_2", "function": {"name": "read_file", "arguments": "{}"}}
            ]),
            Message.tool(content="file contents", tool_call_id="call_2", name="read_file"),
            Message.assistant(content="Step 3", tool_calls=[
                {"id": "call_3", "function": {"name": "analyze", "arguments": "{}"}}
            ]),
            Message.tool(content="analysis done", tool_call_id="call_3", name="analyze"),
            Message.assistant(content="Step 4", tool_calls=[
                {"id": "call_4", "function": {"name": "run", "arguments": "{}"}}
            ]),
            Message.tool(content="run complete", tool_call_id="call_4", name="run"),
        ]

        result = assembler.rebuild(turn_messages)

        old_tool = [m for m in result if m.tool_call_id == "call_1"]
        assert len(old_tool) == 1
        assert old_tool[0].content == small_content, (
            f"Small tool output was incorrectly truncated. "
            f"Expected '{small_content}', got '{old_tool[0].content}'. "
            f"Bug: truncation applied to all old tool outputs regardless of size."
        )

    def test_assistant_messages_never_truncated(self, mock_session_manager):
        """Assistant messages must never be truncated by _truncate_old_tool_outputs.
        Bug: truncation applied to all old messages, not just tool results."""
        assembler = _make_assembler_with_static_prefix(mock_session_manager)

        long_assistant_content = "Here is my detailed analysis:\n" + "explanation " * 500
        turn_messages = [
            Message.user("Explain something"),
            Message.assistant(content=long_assistant_content),
            # Enough recent messages to push the assistant msg beyond keep_recent
            Message.assistant(content="Step 2", tool_calls=[
                {"id": "call_1", "function": {"name": "tool1", "arguments": "{}"}}
            ]),
            Message.tool(content="result1", tool_call_id="call_1", name="tool1"),
            Message.assistant(content="Step 3", tool_calls=[
                {"id": "call_2", "function": {"name": "tool2", "arguments": "{}"}}
            ]),
            Message.tool(content="result2", tool_call_id="call_2", name="tool2"),
            Message.assistant(content="Step 4", tool_calls=[
                {"id": "call_3", "function": {"name": "tool3", "arguments": "{}"}}
            ]),
            Message.tool(content="result3", tool_call_id="call_3", name="tool3"),
        ]

        result = assembler.rebuild(turn_messages)

        # Find the long assistant message
        long_asst = [m for m in result if m.role == Role.ASSISTANT.value
                     and m.content and "detailed analysis" in m.content]
        assert len(long_asst) == 1, "Long assistant message should still be present"
        assert long_asst[0].content == long_assistant_content, (
            f"Assistant message was truncated! "
            f"Expected {len(long_assistant_content)} chars, "
            f"got {len(long_asst[0].content or '')}. "
            f"Bug: truncation applied to assistant messages, not just tool results."
        )

    def test_truncation_preserves_head_and_tail(self, mock_session_manager):
        """Truncated tool output must preserve head and tail for context.
        Bug: truncation only keeps head, losing the end of the output
        which often contains the final result or error message."""
        assembler = _make_assembler_with_static_prefix(mock_session_manager)

        head_marker = "HEAD_MARKER_START"
        tail_marker = "TAIL_MARKER_END"
        large_content = head_marker + ("x" * 10000) + tail_marker

        turn_messages = [
            Message.user("Read file"),
            Message.assistant(content="Reading", tool_calls=[
                {"id": "call_1", "function": {"name": "read_file", "arguments": "{}"}}
            ]),
            Message.tool(content=large_content, tool_call_id="call_1", name="read_file"),
            # Recent messages to push call_1 beyond boundary
            Message.assistant(content="s2", tool_calls=[
                {"id": "c2", "function": {"name": "t2", "arguments": "{}"}}
            ]),
            Message.tool(content="r2", tool_call_id="c2", name="t2"),
            Message.assistant(content="s3", tool_calls=[
                {"id": "c3", "function": {"name": "t3", "arguments": "{}"}}
            ]),
            Message.tool(content="r3", tool_call_id="c3", name="t3"),
            Message.assistant(content="s4", tool_calls=[
                {"id": "c4", "function": {"name": "t4", "arguments": "{}"}}
            ]),
            Message.tool(content="r4", tool_call_id="c4", name="t4"),
        ]

        result = assembler.rebuild(turn_messages)

        old_tool = [m for m in result if m.tool_call_id == "call_1"]
        assert len(old_tool) == 1
        truncated = old_tool[0].content or ""
        assert head_marker in truncated, (
            f"Head of tool output not preserved. "
            f"Bug: truncation doesn't keep the beginning of the output."
        )
        assert tail_marker in truncated, (
            f"Tail of tool output not preserved. "
            f"Bug: truncation only keeps head, losing the end which often "
            f"contains the final result or error message."
        )

    def test_rebuild_with_empty_turn_messages(self, mock_session_manager):
        """rebuild() with empty turn_messages must not crash.
        Bug: index error or division by zero in truncation logic."""
        assembler = _make_assembler_with_static_prefix(mock_session_manager)
        result = assembler.rebuild([])
        assert isinstance(result, list)

    def test_rebuild_with_single_message(self, mock_session_manager):
        """rebuild() with a single message must not crash or truncate it.
        Bug: off-by-one in boundary calculation truncates the only message."""
        assembler = _make_assembler_with_static_prefix(mock_session_manager)
        msg = Message.user("Just one message")
        result = assembler.rebuild([msg])
        user_msgs = [m for m in result if m.role == Role.USER.value]
        assert any("Just one message" in (m.content or "") for m in user_msgs)

    def test_tool_call_id_preserved_after_truncation(self, mock_session_manager):
        """Truncated tool messages must retain their tool_call_id.
        Bug: truncation creates a new Message without copying tool_call_id,
        breaking the tool_call_id → tool_result linkage required by the API."""
        assembler = _make_assembler_with_static_prefix(mock_session_manager)

        turn_messages = [
            Message.user("task"),
            Message.assistant(content="step1", tool_calls=[
                {"id": "call_ABC123", "function": {"name": "read_file", "arguments": "{}"}}
            ]),
            Message.tool(content="x" * 10000, tool_call_id="call_ABC123", name="read_file"),
            # Recent messages
            Message.assistant(content="s2", tool_calls=[
                {"id": "c2", "function": {"name": "t2", "arguments": "{}"}}
            ]),
            Message.tool(content="r2", tool_call_id="c2", name="t2"),
            Message.assistant(content="s3", tool_calls=[
                {"id": "c3", "function": {"name": "t3", "arguments": "{}"}}
            ]),
            Message.tool(content="r3", tool_call_id="c3", name="t3"),
            Message.assistant(content="s4", tool_calls=[
                {"id": "c4", "function": {"name": "t4", "arguments": "{}"}}
            ]),
            Message.tool(content="r4", tool_call_id="c4", name="t4"),
        ]

        result = assembler.rebuild(turn_messages)

        truncated_tool = [m for m in result if m.tool_call_id == "call_ABC123"]
        assert len(truncated_tool) == 1, (
            "Truncated tool message lost its tool_call_id. "
            "Bug: _maybe_truncate_tool_msg creates a new Message without "
            "copying tool_call_id, breaking API linkage."
        )
        assert truncated_tool[0].name == "read_file", (
            "Truncated tool message lost its name attribute."
        )

    def test_rebuild_does_not_mutate_input(self, mock_session_manager):
        """rebuild() must not mutate the input turn_messages list.
        Bug: in-place truncation modifies the caller's message objects,
        causing data loss in the AgentLoop's turn_messages accumulator."""
        assembler = _make_assembler_with_static_prefix(mock_session_manager)

        original_content = "x" * 10000
        tool_msg = Message.tool(content=original_content, tool_call_id="call_1", name="read_file")

        turn_messages = [
            Message.user("task"),
            Message.assistant(content="step1", tool_calls=[
                {"id": "call_1", "function": {"name": "read_file", "arguments": "{}"}}
            ]),
            tool_msg,
            Message.assistant(content="s2", tool_calls=[
                {"id": "c2", "function": {"name": "t2", "arguments": "{}"}}
            ]),
            Message.tool(content="r2", tool_call_id="c2", name="t2"),
            Message.assistant(content="s3", tool_calls=[
                {"id": "c3", "function": {"name": "t3", "arguments": "{}"}}
            ]),
            Message.tool(content="r3", tool_call_id="c3", name="t3"),
            Message.assistant(content="s4", tool_calls=[
                {"id": "c4", "function": {"name": "t4", "arguments": "{}"}}
            ]),
            Message.tool(content="r4", tool_call_id="c4", name="t4"),
        ]

        original_len = len(turn_messages)
        assembler.rebuild(turn_messages)

        # Input list must not be modified
        assert len(turn_messages) == original_len, "rebuild() modified input list length"
        assert tool_msg.content == original_content, (
            f"rebuild() mutated the original tool message content! "
            f"Expected {len(original_content)} chars, got {len(tool_msg.content or '')}. "
            f"Bug: in-place truncation modifies caller's data."
        )

    def test_over_budget_triggers_full_compression(self, mock_session_manager):
        """When turn_messages exceed budget even after truncation, full
        compression pipeline must be invoked.
        Bug: only proactive truncation applied, no fallback to compress_history
        when budget is still exceeded."""
        assembler = _make_assembler_with_static_prefix(
            mock_session_manager, max_tokens=500  # Very tight budget
        )

        # Create messages that exceed the tiny budget even after truncation
        turn_messages = [Message.user("task")]
        for i in range(10):
            turn_messages.append(Message.assistant(
                content=f"Step {i}" + " detail" * 100,
                tool_calls=[{"id": f"c{i}", "function": {"name": "tool", "arguments": "{}"}}]
            ))
            turn_messages.append(Message.tool(
                content="result " * 500,
                tool_call_id=f"c{i}",
                name="tool",
            ))

        # Should not crash — must fall through to full compression
        result = assembler.rebuild(turn_messages)
        assert isinstance(result, list)
        assert len(result) > 0, "rebuild() returned empty list on over-budget"


# ---------------------------------------------------------------------------
# _load_history — Turn → Message conversion fidelity
#   Bug: tool messages lost tool_call_id when loaded from session store,
#   causing Anthropic API 400 errors on multi-turn conversations.
# ---------------------------------------------------------------------------

def _make_mock_turn(role, content, tool_calls=None, tool_call_id=None):
    """Create a mock Turn object matching session_store.Turn fields."""
    turn = MagicMock()
    turn.role = role
    turn.content = content
    turn.tool_calls = tool_calls
    turn.tool_call_id = tool_call_id
    # Turn model does NOT have a 'name' attribute — getattr should return None
    if hasattr(turn, 'name'):
        del turn.name
    return turn


class TestLoadHistoryToolCallId:
    """Catches: _load_history dropping tool_call_id on tool messages.

    Root cause: the else branch in _load_history created Message(role, content)
    without passing tool_call_id, so Anthropic API received tool_use_id=None
    and returned 400.
    """

    def test_tool_message_preserves_tool_call_id(self, mock_session_manager):
        """Tool messages loaded from history must retain tool_call_id.
        Bug: tool messages went through the generic else branch which
        created Message(role=role, content=content) without tool_call_id."""
        sm = mock_session_manager
        session = MagicMock()
        session.id = "sess-001"
        sm.active_session = session

        sm.get_history.return_value = [
            _make_mock_turn("user", "Do something"),
            _make_mock_turn(
                "assistant", "I'll use a tool",
                tool_calls=[{"id": "call_abc", "function": {"name": "read_file", "arguments": "{}"}}],
            ),
            _make_mock_turn("tool", "file contents here", tool_call_id="call_abc"),
            _make_mock_turn("assistant", "Here is the result"),
        ]

        assembler = ContextAssembler(session_manager=sm, max_tokens=100000)
        messages = assembler._load_history()

        tool_msgs = [m for m in messages if m.role == Role.TOOL.value]
        assert len(tool_msgs) == 1, "Expected exactly one tool message in history"
        assert tool_msgs[0].tool_call_id == "call_abc", (
            f"tool_call_id lost during _load_history: got {tool_msgs[0].tool_call_id!r}. "
            "This causes Anthropic API 400 because tool_use_id becomes None."
        )

    def test_multiple_tool_messages_each_preserve_tool_call_id(self, mock_session_manager):
        """When history has multiple tool results, each must keep its own tool_call_id.
        Bug: all tool messages lost their tool_call_id in the generic else branch."""
        sm = mock_session_manager
        session = MagicMock()
        session.id = "sess-002"
        sm.active_session = session

        sm.get_history.return_value = [
            _make_mock_turn("user", "Run two tools"),
            _make_mock_turn(
                "assistant", "",
                tool_calls=[
                    {"id": "call_1", "function": {"name": "tool_a", "arguments": "{}"}},
                    {"id": "call_2", "function": {"name": "tool_b", "arguments": "{}"}},
                ],
            ),
            _make_mock_turn("tool", "result_a", tool_call_id="call_1"),
            _make_mock_turn("tool", "result_b", tool_call_id="call_2"),
            _make_mock_turn("assistant", "Done"),
        ]

        assembler = ContextAssembler(session_manager=sm, max_tokens=100000)
        messages = assembler._load_history()

        tool_msgs = [m for m in messages if m.role == Role.TOOL.value]
        assert len(tool_msgs) == 2
        ids = {m.tool_call_id for m in tool_msgs}
        assert ids == {"call_1", "call_2"}, (
            f"tool_call_ids not preserved: got {ids}. "
            "Each tool_result must reference its corresponding tool_use."
        )

    def test_tool_message_role_is_tool_not_user(self, mock_session_manager):
        """Tool messages must have role='tool', not be coerced to 'user'.
        Bug: the else branch mapped unknown roles to USER, so tool messages
        became user messages, breaking the assistant→tool→assistant sequence."""
        sm = mock_session_manager
        session = MagicMock()
        session.id = "sess-003"
        sm.active_session = session

        sm.get_history.return_value = [
            _make_mock_turn("tool", "some result", tool_call_id="call_xyz"),
        ]

        assembler = ContextAssembler(session_manager=sm, max_tokens=100000)
        messages = assembler._load_history()

        assert len(messages) == 1
        assert messages[0].role == Role.TOOL.value, (
            f"Tool message role was coerced to {messages[0].role!r}. "
            "The else branch incorrectly mapped 'tool' to 'user'."
        )

    def test_assistant_with_tool_calls_preserves_tool_calls(self, mock_session_manager):
        """Assistant messages with tool_calls must preserve the tool_calls list.
        This was already handled correctly but verify it still works."""
        sm = mock_session_manager
        session = MagicMock()
        session.id = "sess-004"
        sm.active_session = session

        tc = [{"id": "call_99", "function": {"name": "search", "arguments": '{"q":"test"}'}}]
        sm.get_history.return_value = [
            _make_mock_turn("assistant", "Searching...", tool_calls=tc),
        ]

        assembler = ContextAssembler(session_manager=sm, max_tokens=100000)
        messages = assembler._load_history()

        assert len(messages) == 1
        assert messages[0].role == Role.ASSISTANT.value
        assert messages[0].tool_calls == tc

    def test_plain_assistant_message_no_tool_calls(self, mock_session_manager):
        """Plain assistant messages (no tool_calls) must not have tool_calls set."""
        sm = mock_session_manager
        session = MagicMock()
        session.id = "sess-005"
        sm.active_session = session

        sm.get_history.return_value = [
            _make_mock_turn("assistant", "Just a text reply"),
        ]

        assembler = ContextAssembler(session_manager=sm, max_tokens=100000)
        messages = assembler._load_history()

        assert len(messages) == 1
        assert messages[0].role == Role.ASSISTANT.value
        assert messages[0].content == "Just a text reply"
        assert messages[0].tool_calls is None

    def test_user_message_preserved(self, mock_session_manager):
        """User messages must be preserved with correct role and content."""
        sm = mock_session_manager
        session = MagicMock()
        session.id = "sess-006"
        sm.active_session = session

        sm.get_history.return_value = [
            _make_mock_turn("user", "Hello world"),
        ]

        assembler = ContextAssembler(session_manager=sm, max_tokens=100000)
        messages = assembler._load_history()

        assert len(messages) == 1
        assert messages[0].role == Role.USER.value
        assert messages[0].content == "Hello world"

    def test_full_tool_use_cycle_ordering(self, mock_session_manager):
        """A complete user→assistant(tool_use)→tool(result)→assistant cycle
        must be loaded in the correct order with all fields intact.
        Bug: tool messages lost tool_call_id, breaking the cycle."""
        sm = mock_session_manager
        session = MagicMock()
        session.id = "sess-007"
        sm.active_session = session

        sm.get_history.return_value = [
            _make_mock_turn("user", "What's the weather?"),
            _make_mock_turn(
                "assistant", "Let me check",
                tool_calls=[{"id": "call_w1", "function": {"name": "get_weather", "arguments": '{"city":"Beijing"}'}}],
            ),
            _make_mock_turn("tool", '{"temp":22}', tool_call_id="call_w1"),
            _make_mock_turn("assistant", "It's 22°C in Beijing."),
        ]

        assembler = ContextAssembler(session_manager=sm, max_tokens=100000)
        messages = assembler._load_history()

        assert len(messages) == 4
        roles = [m.role for m in messages]
        assert roles == ["user", "assistant", "tool", "assistant"], (
            f"Message ordering wrong: {roles}"
        )
        # Verify the tool_use → tool_result linkage
        assert messages[1].tool_calls[0]["id"] == "call_w1"
        assert messages[2].tool_call_id == "call_w1", (
            "tool_call_id linkage broken: assistant's tool_use id doesn't match "
            "tool message's tool_call_id"
        )

    def test_no_active_session_returns_empty(self, mock_session_manager):
        """When no session is active, _load_history must return empty list."""
        sm = mock_session_manager
        sm.active_session = None

        assembler = ContextAssembler(session_manager=sm, max_tokens=100000)
        messages = assembler._load_history()

        assert messages == []

    def test_exception_in_get_history_returns_empty(self, mock_session_manager):
        """If get_history raises, _load_history must return empty list, not crash."""
        sm = mock_session_manager
        session = MagicMock()
        session.id = "sess-err"
        sm.active_session = session
        sm.get_history.side_effect = RuntimeError("DB connection lost")

        assembler = ContextAssembler(session_manager=sm, max_tokens=100000)
        messages = assembler._load_history()

        assert messages == [], "Exception should be caught, not propagated"

    def test_tool_call_id_survives_full_build_pipeline(self, mock_session_manager, mock_prompt_provider):
        """End-to-end: tool_call_id must survive through build() → _load_history()
        → _reorder_messages() and appear in the final message list.
        Bug: tool_call_id was lost in _load_history, so it was None in the
        final output sent to the LLM API."""
        sm = mock_session_manager
        session = MagicMock()
        session.id = "sess-e2e-001"
        sm.active_session = session

        sm.get_history.return_value = [
            _make_mock_turn("user", "Previous question"),
            _make_mock_turn(
                "assistant", "Using tool",
                tool_calls=[{"id": "call_e2e", "function": {"name": "read_file", "arguments": "{}"}}],
            ),
            _make_mock_turn("tool", "file data", tool_call_id="call_e2e"),
            _make_mock_turn("assistant", "Here's the answer"),
        ]

        assembler = ContextAssembler(
            session_manager=sm,
            prompt_provider=mock_prompt_provider,
            max_tokens=100000,
        )
        result = assembler.build(user_input="New question")

        tool_msgs = [m for m in result if m.role == Role.TOOL.value]
        assert len(tool_msgs) == 1, (
            f"Expected 1 tool message in build() output, got {len(tool_msgs)}"
        )
        assert tool_msgs[0].tool_call_id == "call_e2e", (
            f"tool_call_id lost through full build() pipeline: "
            f"got {tool_msgs[0].tool_call_id!r}. "
            "This causes Anthropic API 400 on multi-turn conversations."
        )
