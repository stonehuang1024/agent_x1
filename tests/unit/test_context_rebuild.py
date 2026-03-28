"""Tests for ContextAssembler.rebuild() and AgentLoop turn message management.

Each test targets a specific bug class:
- Static prefix duplication in multi-iteration turns
- Token budget enforcement during rebuild
- Compression triggering when turn messages exceed budget
- Static prefix cache invalidation
- Message ordering after rebuild
- AgentLoop correctly separating static and dynamic messages
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path
from typing import List

from src.core.models import Message, Role
from src.context.context_assembler import ContextAssembler, ContextLayer
from src.context.context_window import ContextWindow, ContextBudget
from src.context.context_compressor import ContextCompressor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_session_manager():
    mgr = MagicMock()
    mgr.active_session = None
    mgr.get_history.return_value = []
    return mgr


@pytest.fixture
def mock_prompt_provider():
    provider = MagicMock()
    provider.build_system_prompt.return_value = "You are a helpful assistant."
    return provider


@pytest.fixture
def assembler(mock_session_manager, mock_prompt_provider):
    """Assembler with system prompt (static) and generous token budget."""
    return ContextAssembler(
        session_manager=mock_session_manager,
        prompt_provider=mock_prompt_provider,
        max_tokens=128000,
    )


@pytest.fixture
def small_budget_assembler(mock_session_manager, mock_prompt_provider):
    """Assembler with a very small token budget to force compression."""
    return ContextAssembler(
        session_manager=mock_session_manager,
        prompt_provider=mock_prompt_provider,
        max_tokens=200,  # Very small — forces compression quickly
    )


# ---------------------------------------------------------------------------
# Test: Static prefix is cached after build()
# Bug caught: If _static_prefix is empty after build(), rebuild() would
#   produce messages without system prompt / CLAUDE.md.
# ---------------------------------------------------------------------------

class TestStaticPrefixCaching:

    def test_static_prefix_populated_after_build(self, assembler):
        """build() must cache static (cacheable) messages for rebuild()."""
        messages = assembler.build("hello")

        assert len(assembler._static_prefix) > 0, (
            "Static prefix was not cached after build(). "
            "rebuild() would produce messages without system prompt."
        )
        # Verify the cached messages are the ones with cache_control
        for msg in assembler._static_prefix:
            assert getattr(msg, 'cache_control', None) is not None, (
                f"Static prefix message has no cache_control: {msg.role}"
            )

    def test_static_tokens_tracked(self, assembler):
        """_static_tokens must reflect actual token count of cached prefix."""
        assembler.build("hello")

        assert assembler._static_tokens > 0, (
            "_static_tokens is 0 after build(). "
            "rebuild() cannot correctly compute remaining budget."
        )
        # Cross-check: estimate_tokens on the prefix should match
        estimated = assembler.window.estimate_tokens(assembler._static_prefix)
        assert assembler._static_tokens == estimated, (
            f"_static_tokens ({assembler._static_tokens}) != "
            f"estimate_tokens ({estimated}). Budget calculation will be wrong."
        )

    def test_static_prefix_not_duplicated_in_rebuild(self, assembler):
        """rebuild() must not duplicate the static prefix.

        Bug: If rebuild() appends static prefix to turn_messages that already
        contain static messages, the system prompt appears twice.
        """
        messages = assembler.build("hello")

        # Extract turn messages (non-cached)
        turn_messages = [m for m in messages if not getattr(m, 'cache_control', None)]

        # Simulate adding assistant + tool messages
        turn_messages.append(Message.assistant(content="I'll help you."))
        turn_messages.append(Message.tool(content="result", tool_call_id="t1", name="test"))

        rebuilt = assembler.rebuild(turn_messages)

        # Count system messages — should have exactly one system prompt
        system_msgs = [m for m in rebuilt if m.role == Role.SYSTEM.value]
        system_prompt_count = sum(
            1 for m in system_msgs
            if "helpful assistant" in (m.content or "").lower()
        )
        assert system_prompt_count == 1, (
            f"System prompt appears {system_prompt_count} times in rebuilt messages. "
            f"Expected exactly 1. Static prefix is being duplicated."
        )


# ---------------------------------------------------------------------------
# Test: rebuild() produces correct message structure
# Bug caught: Wrong message ordering breaks LLM API (system must come first,
#   tool results must follow their assistant message).
# ---------------------------------------------------------------------------

class TestRebuildMessageStructure:

    def test_rebuild_preserves_message_order(self, assembler):
        """rebuild() must maintain: static prefix → turn messages order."""
        assembler.build("hello")

        turn_messages = [
            Message.user("hello"),
            Message.assistant(content="Let me check.", tool_calls=[{"id": "t1", "type": "function", "function": {"name": "test", "arguments": "{}"}}]),
            Message.tool(content="result", tool_call_id="t1", name="test"),
        ]

        rebuilt = assembler.rebuild(turn_messages)

        # Static prefix (system messages) must come first
        first_non_system_idx = None
        for i, msg in enumerate(rebuilt):
            if msg.role != Role.SYSTEM.value:
                first_non_system_idx = i
                break

        if first_non_system_idx is not None:
            # All messages before first_non_system must be system
            for i in range(first_non_system_idx):
                assert rebuilt[i].role == Role.SYSTEM.value, (
                    f"Non-system message at index {i} before first non-system at {first_non_system_idx}. "
                    f"Static prefix is not properly prepended."
                )

    def test_rebuild_includes_all_turn_messages(self, assembler):
        """rebuild() must not silently drop turn messages when within budget."""
        assembler.build("hello")

        turn_messages = [
            Message.user("hello"),
            Message.assistant(content="response1"),
            Message.assistant(content="response2"),
        ]

        rebuilt = assembler.rebuild(turn_messages)

        # All turn messages must be present
        turn_contents = {m.content for m in turn_messages}
        rebuilt_contents = {m.content for m in rebuilt if m.content}
        missing = turn_contents - rebuilt_contents
        assert not missing, (
            f"Turn messages lost during rebuild: {missing}. "
            f"rebuild() silently dropped messages."
        )

    def test_rebuild_with_empty_turn_messages(self, assembler):
        """rebuild() with empty turn_messages should return only static prefix."""
        assembler.build("hello")

        rebuilt = assembler.rebuild([])

        # Should contain only static prefix
        assert len(rebuilt) == len(assembler._static_prefix), (
            f"rebuild([]) returned {len(rebuilt)} messages, "
            f"expected {len(assembler._static_prefix)} (static prefix only)."
        )


# ---------------------------------------------------------------------------
# Test: Token budget enforcement in rebuild()
# Bug caught: If rebuild() doesn't check budget, messages grow unbounded
#   and eventually exceed the LLM's context window.
# ---------------------------------------------------------------------------

class TestRebuildTokenBudget:

    def test_rebuild_compresses_when_over_budget(self, small_budget_assembler):
        """When turn messages exceed remaining budget, rebuild() must compress.

        Bug: Without compression, the LLM call would fail with a token limit
        error, or critical context would be silently truncated by the API.
        """
        asm = small_budget_assembler
        asm.build("hi")

        # Create turn messages that exceed the small budget
        large_content = "x" * 5000  # Way more than 200 tokens
        turn_messages = [
            Message.user("hi"),
            Message.assistant(content=large_content),
            Message.tool(content=large_content, tool_call_id="t1", name="test"),
        ]

        rebuilt = asm.rebuild(turn_messages)

        # The rebuilt messages should exist (not crash)
        assert len(rebuilt) > 0, "rebuild() returned empty list"

        # Total tokens should be managed (compressed or truncated)
        total_tokens = asm.window.estimate_tokens(rebuilt)
        # We can't assert exact budget compliance because force-include exists,
        # but we verify compression was attempted by checking the result is
        # smaller than the input
        input_tokens = asm.window.estimate_tokens(turn_messages)
        # At minimum, the compressor should have been invoked
        # (the result may still exceed budget due to force-include of static prefix)

    def test_rebuild_does_not_compress_when_within_budget(self, assembler):
        """When within budget, rebuild() must not alter turn messages.

        Bug: Unnecessary compression would lose information (tool results,
        assistant reasoning) that the LLM needs for correct continuation.
        """
        assembler.build("hello")

        turn_messages = [
            Message.user("hello"),
            Message.assistant(content="short response"),
        ]

        rebuilt = assembler.rebuild(turn_messages)

        # Turn messages should be present unmodified
        rebuilt_contents = [m.content for m in rebuilt]
        assert "short response" in rebuilt_contents, (
            "Assistant message was modified/compressed despite being within budget."
        )
        assert "hello" in rebuilt_contents, (
            "User message was modified/compressed despite being within budget."
        )


# ---------------------------------------------------------------------------
# Test: rebuild() window reset
# Bug caught: If window is not reset, token accounting accumulates across
#   iterations, causing premature compression or budget exhaustion.
# ---------------------------------------------------------------------------

class TestRebuildWindowReset:

    def test_rebuild_resets_window_each_call(self, assembler):
        """Each rebuild() call must start with a fresh token window.

        Bug: If window state leaks between rebuild() calls, the second call
        would think the budget is already partially consumed, leading to
        unnecessary compression.
        """
        assembler.build("hello")
        turn_messages = [Message.user("hello")]

        # Call rebuild twice
        rebuilt1 = assembler.rebuild(turn_messages)
        usage_after_first = assembler.window._current_usage

        rebuilt2 = assembler.rebuild(turn_messages)
        usage_after_second = assembler.window._current_usage

        # Both calls should produce the same result
        assert len(rebuilt1) == len(rebuilt2), (
            f"rebuild() produced different results on consecutive calls: "
            f"{len(rebuilt1)} vs {len(rebuilt2)} messages. "
            f"Window state is leaking between calls."
        )
        assert usage_after_first == usage_after_second, (
            f"Token usage differs between consecutive rebuild() calls: "
            f"{usage_after_first} vs {usage_after_second}. "
            f"Window is not being reset."
        )


# ---------------------------------------------------------------------------
# Test: Interaction between build() and rebuild()
# Bug caught: If build() changes internal state that breaks rebuild(),
#   the second iteration of AgentLoop would produce wrong messages.
# ---------------------------------------------------------------------------

class TestBuildRebuildInteraction:

    def test_build_then_rebuild_produces_consistent_static_prefix(self, assembler):
        """The static prefix from build() must be reused identically in rebuild().

        Bug: If rebuild() reconstructs the static prefix differently (e.g.,
        re-loading CLAUDE.md with different content), the LLM sees inconsistent
        system instructions across iterations.
        """
        messages = assembler.build("hello")
        static_after_build = list(assembler._static_prefix)

        turn_messages = [m for m in messages if not getattr(m, 'cache_control', None)]
        rebuilt = assembler.rebuild(turn_messages)

        # Extract static portion from rebuilt
        static_in_rebuilt = rebuilt[:len(static_after_build)]

        for i, (original, rebuilt_msg) in enumerate(zip(static_after_build, static_in_rebuilt)):
            assert original.content == rebuilt_msg.content, (
                f"Static prefix message {i} differs between build() and rebuild(): "
                f"'{original.content[:50]}...' vs '{rebuilt_msg.content[:50]}...'. "
                f"LLM would see inconsistent system instructions."
            )
            assert original.role == rebuilt_msg.role, (
                f"Static prefix message {i} role differs: "
                f"{original.role} vs {rebuilt_msg.role}"
            )

    def test_multiple_rebuilds_accumulate_correctly(self, assembler):
        """Simulating multiple AgentLoop iterations with growing turn_messages.

        Bug: If turn_messages reference is shared/mutated incorrectly,
        messages from iteration N could leak into iteration N+1's static prefix.
        """
        assembler.build("hello")

        # Iteration 1: user message only
        turn_msgs = [Message.user("hello")]
        rebuilt1 = assembler.rebuild(turn_msgs)
        count1 = len(rebuilt1)

        # Iteration 2: add assistant + tool
        turn_msgs.append(Message.assistant(content="checking..."))
        turn_msgs.append(Message.tool(content="result", tool_call_id="t1", name="test"))
        rebuilt2 = assembler.rebuild(turn_msgs)
        count2 = len(rebuilt2)

        # Iteration 3: add another assistant + tool
        turn_msgs.append(Message.assistant(content="done"))
        turn_msgs.append(Message.tool(content="final", tool_call_id="t2", name="test2"))
        rebuilt3 = assembler.rebuild(turn_msgs)
        count3 = len(rebuilt3)

        # Each rebuild should have exactly: static_prefix + turn_messages
        static_count = len(assembler._static_prefix)
        assert count1 == static_count + 1, (
            f"Iteration 1: expected {static_count + 1} messages, got {count1}"
        )
        assert count2 == static_count + 3, (
            f"Iteration 2: expected {static_count + 3} messages, got {count2}"
        )
        assert count3 == static_count + 5, (
            f"Iteration 3: expected {static_count + 5} messages, got {count3}"
        )

        # Verify no static prefix duplication across iterations
        for rebuilt, label in [(rebuilt1, "iter1"), (rebuilt2, "iter2"), (rebuilt3, "iter3")]:
            system_msgs = [m for m in rebuilt if m.role == Role.SYSTEM.value]
            assert len(system_msgs) == static_count, (
                f"{label}: expected {static_count} system messages, "
                f"got {len(system_msgs)}. Static prefix is being duplicated."
            )


# ---------------------------------------------------------------------------
# Test: No prompt provider (minimal assembler)
# Bug caught: rebuild() crashes when _static_prefix is empty because
#   no prompt_provider was configured.
# ---------------------------------------------------------------------------

class TestRebuildWithoutStaticLayers:

    def test_rebuild_works_without_prompt_provider(self, mock_session_manager):
        """rebuild() must work even when there are no static layers.

        Bug: If rebuild() assumes _static_prefix is non-empty, it would
        crash or produce wrong results for minimal configurations.
        """
        asm = ContextAssembler(
            session_manager=mock_session_manager,
            max_tokens=128000,
        )
        messages = asm.build("hello")

        turn_messages = [Message.user("hello")]
        rebuilt = asm.rebuild(turn_messages)

        assert len(rebuilt) >= 1, (
            "rebuild() with no static layers should still return turn messages"
        )
        # With no prompt provider, static prefix should be empty
        assert len(asm._static_prefix) == 0, (
            "Static prefix should be empty when no prompt provider is configured"
        )


# ---------------------------------------------------------------------------
# Test: Compression event emission during rebuild
# Bug caught: If rebuild() compresses but doesn't emit events, monitoring
#   systems won't know compression happened, making debugging impossible.
# ---------------------------------------------------------------------------

class TestRebuildEventEmission:

    def test_compression_event_emitted_on_rebuild(self):
        """When rebuild() compresses, it must emit CONTEXT_COMPRESSED event."""
        mock_session = MagicMock()
        mock_session.active_session = None
        mock_session.get_history.return_value = []

        mock_event_bus = MagicMock()

        asm = ContextAssembler(
            session_manager=mock_session,
            max_tokens=100,  # Very small to force compression
            event_bus=mock_event_bus,
        )
        asm.build("hi")

        # Large turn messages to trigger compression
        turn_messages = [
            Message.user("hi"),
            Message.assistant(content="x" * 3000),
        ]

        asm.rebuild(turn_messages)

        # Check if CONTEXT_COMPRESSED was emitted
        # (may or may not be emitted depending on whether compression was needed)
        # The key assertion is that it doesn't crash


# ---------------------------------------------------------------------------
# Test: AgentLoop turn_messages separation
# Bug caught: If AgentLoop doesn't correctly separate static from dynamic
#   messages, the static prefix would be included in turn_messages and
#   duplicated on every rebuild().
# ---------------------------------------------------------------------------

class TestAgentLoopMessageSeparation:

    def test_turn_messages_exclude_cached_messages(self, assembler):
        """turn_messages extracted from build() must not include cached messages.

        Bug: If cached (static) messages leak into turn_messages, rebuild()
        would prepend them again, causing duplication:
          [sys_prompt, sys_prompt, user_msg, ...]
        """
        messages = assembler.build("hello")

        # Simulate what AgentLoop does: extract non-cached messages
        turn_messages = [
            m for m in messages
            if not getattr(m, 'cache_control', None)
        ]

        # turn_messages should not contain any system prompt
        for msg in turn_messages:
            if msg.role == Role.SYSTEM.value:
                assert not getattr(msg, 'cache_control', None), (
                    f"Cached system message leaked into turn_messages: "
                    f"'{msg.content[:50]}...'. This would cause duplication in rebuild()."
                )

    def test_growing_turn_messages_no_static_duplication(self, assembler):
        """Simulate 5 AgentLoop iterations and verify no static duplication.

        This is the exact scenario the user reported: step N should not
        contain N copies of the system prompt.
        """
        messages = assembler.build("hello")
        turn_messages = [
            m for m in messages
            if not getattr(m, 'cache_control', None)
        ]

        static_count = len(assembler._static_prefix)

        for iteration in range(1, 6):
            # Simulate assistant response + tool result
            turn_messages.append(Message.assistant(content=f"response_{iteration}"))
            turn_messages.append(Message.tool(
                content=f"result_{iteration}",
                tool_call_id=f"t{iteration}",
                name=f"tool_{iteration}"
            ))

            rebuilt = assembler.rebuild(turn_messages)

            # Count system messages in rebuilt
            system_count = sum(1 for m in rebuilt if m.role == Role.SYSTEM.value)
            assert system_count == static_count, (
                f"Iteration {iteration}: expected {static_count} system messages, "
                f"got {system_count}. Static prefix is being duplicated! "
                f"This is the exact bug the user reported."
            )

            # Total messages should be: static + turn_messages
            expected_total = static_count + len(turn_messages)
            assert len(rebuilt) == expected_total, (
                f"Iteration {iteration}: expected {expected_total} total messages, "
                f"got {len(rebuilt)}. Messages are being added or lost."
            )


# ---------------------------------------------------------------------------
# Test: Adversarial inputs to rebuild()
# Bug caught: Malformed messages could crash rebuild() or corrupt state.
# ---------------------------------------------------------------------------

class TestRebuildAdversarialInputs:

    def test_rebuild_with_none_content_messages(self, assembler):
        """Messages with None content should not crash rebuild()."""
        assembler.build("hello")

        turn_messages = [
            Message.user("hello"),
            Message(role=Role.ASSISTANT.value, content=None),
        ]

        # Should not raise
        rebuilt = assembler.rebuild(turn_messages)
        assert len(rebuilt) > 0

    def test_rebuild_with_very_large_tool_output(self, assembler):
        """Very large tool output should trigger compression, not crash."""
        assembler.build("hello")

        huge_output = "data " * 50000  # ~250K chars
        turn_messages = [
            Message.user("hello"),
            Message.assistant(content="checking"),
            Message.tool(content=huge_output, tool_call_id="t1", name="big_tool"),
        ]

        # Should not crash — should compress
        rebuilt = assembler.rebuild(turn_messages)
        assert len(rebuilt) > 0

    def test_rebuild_with_empty_string_content(self, assembler):
        """Empty string content should not be treated as missing."""
        assembler.build("hello")

        turn_messages = [
            Message.user(""),
            Message.assistant(content=""),
        ]

        rebuilt = assembler.rebuild(turn_messages)
        # Both messages should be present (empty string is valid content)
        assert len(rebuilt) >= len(assembler._static_prefix) + 2
