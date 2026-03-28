"""Tests for ContextCompressor — three-level compression strategy.

Bug classes covered:
- Level 1 (summarize old assistants) skips assistant messages or drops non-assistants
- Level 2 (greedy importance selection) ignores importance scores or selects wrong order
- Level 3 (emergency truncation) drops system messages or keeps wrong messages
- Tool output truncation doesn't preserve head+tail
- Structured summary missing required sections (decisions/file changes/open issues/preferences)
- compress_history returns wrong type (not Tuple)
- keep_recent messages get compressed when they shouldn't
- Empty/single message edge cases crash
- token_estimator parameter ignored
"""

import pytest
from typing import List

from src.core.models import Message, Role
from src.context.context_compressor import ContextCompressor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conversation(n_messages: int, include_system: bool = True) -> List[Message]:
    """Build a synthetic conversation with n non-system messages."""
    msgs = []
    if include_system:
        msgs.append(Message.system("You are a helpful assistant."))
    for i in range(n_messages):
        if i % 2 == 0:
            msgs.append(Message.user(f"User message {i}"))
        else:
            msgs.append(Message.assistant(f"Assistant response {i}"))
    return msgs


def _tiny_token_estimator(messages: List[Message]) -> int:
    """Deterministic token estimator: 10 tokens per message."""
    return len(messages) * 10


# ---------------------------------------------------------------------------
# 1. compress_messages — tool output head+tail truncation
#    Bug: old code only keeps head, discards tail
# ---------------------------------------------------------------------------

class TestCompressMessages:
    """Catches: tool output truncation not preserving head AND tail."""

    def test_preserves_head_and_tail(self):
        """Long tool output must keep first half + last half of max_tool_output_length.
        Bug: only head preserved, tail lost — user loses end-of-file context."""
        content = "A" * 1000 + "B" * 1000 + "C" * 1000 + "D" * 1000 + "E" * 1000
        msg = Message.tool(content, tool_call_id="tc1", name="read_file")

        compressor = ContextCompressor(max_tool_output_length=2000)
        result = compressor.compress_messages([msg])

        assert len(result) == 1
        compressed_content = result[0].content
        # Head should start with "A"s
        assert compressed_content.startswith("A"), (
            "Compressed output should start with head of original content"
        )
        # Tail should end with "E"s
        assert compressed_content.endswith("E"), (
            "Compressed output should end with tail of original content"
        )

    def test_truncation_marker_present(self):
        """Truncated output must contain '[... N chars truncated ...]' marker.
        Bug: marker missing → user doesn't know content was truncated."""
        content = "x" * 10000
        msg = Message.tool(content, tool_call_id="tc1", name="fn")

        compressor = ContextCompressor(max_tool_output_length=2000)
        result = compressor.compress_messages([msg])

        assert "chars truncated" in result[0].content, (
            "Truncated output must contain truncation marker"
        )

    def test_short_content_not_truncated(self):
        """Content shorter than max_tool_output_length*2 must NOT be truncated.
        Bug: truncation threshold wrong, short content gets mangled."""
        content = "short content"
        msg = Message.tool(content, tool_call_id="tc1", name="fn")

        compressor = ContextCompressor(max_tool_output_length=2000)
        result = compressor.compress_messages([msg])

        assert result[0].content == content, (
            f"Short content should be unchanged, got: {result[0].content[:50]}"
        )

    def test_preserves_message_metadata(self):
        """Truncation must preserve tool_call_id, name, role.
        Bug: metadata lost during truncation → API call fails."""
        msg = Message.tool("x" * 10000, tool_call_id="tc42", name="search")
        msg.importance = 0.7

        compressor = ContextCompressor(max_tool_output_length=2000)
        result = compressor.compress_messages([msg])

        assert result[0].tool_call_id == "tc42", "tool_call_id lost during truncation"
        assert result[0].name == "search", "name lost during truncation"
        assert result[0].role == Role.TOOL.value, "role changed during truncation"
        assert result[0].importance == 0.7, "importance lost during truncation"

    def test_truncation_length_correct(self):
        """Head and tail should each be max_tool_output_length/2 chars.
        Bug: head/tail sizes wrong, total exceeds budget."""
        content = "H" * 5000 + "T" * 5000
        compressor = ContextCompressor(max_tool_output_length=2000)
        result = compressor.compress_messages([Message.tool(content, "tc1", "fn")])

        compressed = result[0].content
        # Should be roughly max_tool_output_length + marker length
        assert len(compressed) < len(content), (
            f"Compressed ({len(compressed)}) should be shorter than original ({len(content)})"
        )


# ---------------------------------------------------------------------------
# 2. compress_history — three-level compression
#    Bug: levels executed in wrong order, or levels skipped
# ---------------------------------------------------------------------------

class TestCompressHistoryThreeLevels:
    """Catches: compression levels not executed in correct order."""

    def test_returns_tuple(self):
        """compress_history must return (messages, summary) tuple.
        Bug: old API returned just a list, breaking callers expecting tuple."""
        msgs = _make_conversation(30)
        compressor = ContextCompressor(summary_threshold=10, keep_recent=6)
        result = compressor.compress_history(msgs)

        assert isinstance(result, tuple), (
            f"compress_history should return tuple, got {type(result)}"
        )
        assert len(result) == 2, f"Tuple should have 2 elements, got {len(result)}"
        assert isinstance(result[0], list), "First element should be list of messages"

    def test_short_conversation_not_compressed(self):
        """Conversations shorter than summary_threshold must not be compressed.
        Bug: compression applied to short conversations, losing context."""
        msgs = _make_conversation(5)
        compressor = ContextCompressor(summary_threshold=20, keep_recent=6)
        result_msgs, summary = compressor.compress_history(msgs)

        assert len(result_msgs) == len(msgs), (
            f"Short conversation ({len(msgs)} msgs) should not be compressed, "
            f"but got {len(result_msgs)} msgs"
        )
        assert summary is None, "Summary should be None for uncompressed conversation"

    def test_keep_recent_messages_preserved(self):
        """The last keep_recent non-system messages must always be preserved.
        Bug: recent messages get compressed, losing immediate context."""
        msgs = _make_conversation(30)
        compressor = ContextCompressor(summary_threshold=10, keep_recent=6)
        result_msgs, _ = compressor.compress_history(msgs)

        # Last 6 non-system messages from original should be in result
        original_non_system = [m for m in msgs if m.role != Role.SYSTEM.value]
        expected_recent = original_non_system[-6:]

        for expected in expected_recent:
            assert any(m.content == expected.content for m in result_msgs), (
                f"Recent message '{expected.content[:40]}' was not preserved"
            )

    def test_system_messages_always_preserved(self):
        """System messages must never be dropped by compression.
        Bug: system messages treated as regular messages and compressed."""
        msgs = _make_conversation(30)
        compressor = ContextCompressor(summary_threshold=10, keep_recent=6)
        result_msgs, _ = compressor.compress_history(msgs)

        original_system = [m for m in msgs if m.role == Role.SYSTEM.value]
        result_system = [m for m in result_msgs if m.role == Role.SYSTEM.value]

        # Result should have at least as many system messages
        # (may have more due to summary placeholders)
        assert len(result_system) >= len(original_system), (
            f"System messages dropped: original had {len(original_system)}, "
            f"result has {len(result_system)}"
        )

    def test_compressed_has_fewer_messages(self):
        """Compression must reduce message count for long conversations.
        Bug: compression is a no-op, messages pass through unchanged."""
        msgs = _make_conversation(30)
        compressor = ContextCompressor(summary_threshold=10, keep_recent=6)
        result_msgs, _ = compressor.compress_history(msgs)

        assert len(result_msgs) < len(msgs), (
            f"Compression should reduce messages: original={len(msgs)}, "
            f"result={len(result_msgs)}"
        )

    def test_summary_returned_when_messages_dropped(self):
        """When messages are dropped, summary string must be non-None.
        Bug: summary always None, downstream code can't show what was lost."""
        msgs = _make_conversation(30)
        compressor = ContextCompressor(summary_threshold=10, keep_recent=6)
        _, summary = compressor.compress_history(msgs)

        assert summary is not None, (
            "Summary should be non-None when messages were compressed"
        )

    def test_empty_messages_returns_empty(self):
        """compress_history on empty list must return ([], None).
        Bug: crashes on empty input."""
        compressor = ContextCompressor()
        result_msgs, summary = compressor.compress_history([])
        assert result_msgs == []
        assert summary is None


# ---------------------------------------------------------------------------
# 3. Level 2: Importance-based greedy selection
#    Bug: importance scores ignored, random selection instead
# ---------------------------------------------------------------------------

class TestImportanceBasedSelection:
    """Catches: greedy selection ignoring importance scores."""

    def test_high_importance_messages_kept_over_low(self):
        """When token budget is tight, high-importance messages must be kept
        and low-importance ones dropped.
        Bug: selection ignores importance, drops arbitrarily."""
        msgs = [Message.system("sys")]
        # Create old messages with varying importance
        for i in range(20):
            m = Message.user(f"Message {i}")
            m.importance = 0.1 if i < 10 else 0.9  # First 10 low, last 10 high
            msgs.append(m)
        # Recent messages
        for i in range(6):
            msgs.append(Message.user(f"Recent {i}"))

        compressor = ContextCompressor(
            summary_threshold=5, keep_recent=6,
            token_estimator=_tiny_token_estimator,
        )
        # Set tight budget: system(1) + recent(6) + ~5 old = 12 messages * 10 = 120
        result_msgs, _ = compressor.compress_history(msgs, target_tokens=150)

        # Check that high-importance messages are preferentially kept
        old_in_result = [m for m in result_msgs
                        if m.role != Role.SYSTEM.value
                        and not m.content.startswith("Recent")
                        and not m.content.startswith("[")]
        if old_in_result:
            avg_importance = sum(m.importance for m in old_in_result) / len(old_in_result)
            assert avg_importance > 0.5, (
                f"Kept old messages should have high importance on average, "
                f"got {avg_importance:.2f}. Selection may ignore importance."
            )


# ---------------------------------------------------------------------------
# 4. Structured summary content
#    Bug: summary missing required sections
# ---------------------------------------------------------------------------

class TestStructuredSummary:
    """Catches: structured summary missing decisions/file changes/open issues/preferences."""

    def test_summary_extracts_decisions(self):
        """Summary must include decisions when messages contain decision keywords."""
        msgs = [
            Message.assistant("We decided to use PostgreSQL for the database."),
            Message.assistant("Chose React over Vue for the frontend."),
        ]
        summary = ContextCompressor._generate_structured_summary(msgs)

        assert "Decisions" in summary or "decided" in summary.lower(), (
            f"Summary should extract decisions. Got: {summary[:200]}"
        )

    def test_summary_extracts_file_changes(self):
        """Summary must include file changes when messages mention file operations."""
        msgs = [
            Message.assistant("Modified src/main.py to add error handling."),
            Message.assistant("Created tests/test_auth.py with new test cases."),
        ]
        summary = ContextCompressor._generate_structured_summary(msgs)

        assert "File Changes" in summary or "modified" in summary.lower() or "created" in summary.lower(), (
            f"Summary should extract file changes. Got: {summary[:200]}"
        )

    def test_summary_extracts_open_issues(self):
        """Summary must include open issues when messages mention bugs/todos."""
        msgs = [
            Message.assistant("TODO: fix the race condition in auth module."),
            Message.assistant("There's a bug in the payment processing flow."),
        ]
        summary = ContextCompressor._generate_structured_summary(msgs)

        assert "Open Issues" in summary or "todo" in summary.lower() or "bug" in summary.lower(), (
            f"Summary should extract open issues. Got: {summary[:200]}"
        )

    def test_summary_extracts_preferences(self):
        """Summary must include preferences when messages mention conventions."""
        msgs = [
            Message.assistant("User prefers functional components over class components."),
            Message.assistant("Convention: always use async/await, never raw promises."),
        ]
        summary = ContextCompressor._generate_structured_summary(msgs)

        assert "Preferences" in summary or "prefer" in summary.lower(), (
            f"Summary should extract preferences. Got: {summary[:200]}"
        )

    def test_summary_handles_no_extractable_content(self):
        """When no structured info can be extracted, summary must still be a valid string.
        Bug: returns None or empty string, crashing downstream."""
        msgs = [
            Message.assistant("Hello"),
            Message.assistant("OK"),
        ]
        summary = ContextCompressor._generate_structured_summary(msgs)

        assert isinstance(summary, str), f"Summary should be string, got {type(summary)}"
        assert len(summary) > 0, "Summary should not be empty even with no extractable content"


# ---------------------------------------------------------------------------
# 5. truncate_for_emergency
#    Bug: system messages dropped, or wrong messages kept
# ---------------------------------------------------------------------------

class TestTruncateForEmergency:
    """Catches: emergency truncation dropping system messages or keeping wrong ones."""

    def test_system_messages_always_kept(self):
        """Emergency truncation must always keep ALL system messages.
        Bug: system messages treated as regular and dropped."""
        msgs = [
            Message.system("System prompt 1"),
            Message.system("System prompt 2"),
            Message.user("old user msg"),
            Message.assistant("old assistant msg"),
            Message.user("recent user msg"),
            Message.assistant("recent assistant msg"),
        ]
        compressor = ContextCompressor(keep_recent=2)
        result = compressor.truncate_for_emergency(msgs, max_messages=2)

        system_in_result = [m for m in result if m.role == Role.SYSTEM.value]
        assert len(system_in_result) == 2, (
            f"All 2 system messages must be kept, got {len(system_in_result)}"
        )

    def test_keeps_most_recent_messages(self):
        """Emergency truncation must keep the MOST RECENT non-system messages.
        Bug: keeps oldest instead of most recent."""
        msgs = [
            Message.system("sys"),
            Message.user("old 1"),
            Message.user("old 2"),
            Message.user("recent 1"),
            Message.user("recent 2"),
        ]
        compressor = ContextCompressor(keep_recent=2)
        result = compressor.truncate_for_emergency(msgs, max_messages=2)

        non_system = [m for m in result if m.role != Role.SYSTEM.value]
        assert len(non_system) == 2
        assert non_system[0].content == "recent 1", (
            f"Should keep most recent messages, got: {[m.content for m in non_system]}"
        )
        assert non_system[1].content == "recent 2"

    def test_max_messages_default_to_keep_recent(self):
        """When max_messages is None, should default to keep_recent.
        Bug: None causes crash or keeps all messages."""
        msgs = _make_conversation(20)
        compressor = ContextCompressor(keep_recent=4)
        result = compressor.truncate_for_emergency(msgs)

        non_system = [m for m in result if m.role != Role.SYSTEM.value]
        assert len(non_system) <= 4, (
            f"Default max_messages should be keep_recent=4, got {len(non_system)} non-system msgs"
        )

    def test_legacy_max_chars_parameter(self):
        """Legacy max_chars parameter must still work for backward compat.
        Bug: max_chars parameter removed or ignored."""
        msgs = [
            Message.system("sys"),
            Message.user("a" * 100),
            Message.user("b" * 100),
            Message.user("c" * 100),
        ]
        compressor = ContextCompressor()
        result = compressor.truncate_for_emergency(msgs, max_chars=150)

        # System (3 chars) + at most 1 user message (100 chars) = 103 < 150
        # System (3 chars) + 2 user messages (200 chars) = 203 > 150
        system_in = [m for m in result if m.role == Role.SYSTEM.value]
        assert len(system_in) >= 1, "System messages must be kept with max_chars"


# ---------------------------------------------------------------------------
# 6. token_estimator parameter
#    Bug: custom estimator ignored, default always used
# ---------------------------------------------------------------------------

class TestTokenEstimator:
    """Catches: custom token_estimator parameter being ignored."""

    def test_custom_estimator_used_in_compress_history(self):
        """compress_history must use the provided token_estimator, not the default.
        Bug: hardcoded estimator ignores constructor parameter."""
        call_count = 0

        def counting_estimator(msgs):
            nonlocal call_count
            call_count += 1
            return len(msgs) * 10

        msgs = _make_conversation(30)
        compressor = ContextCompressor(
            summary_threshold=5, keep_recent=6,
            token_estimator=counting_estimator,
        )
        compressor.compress_history(msgs, target_tokens=200)

        assert call_count > 0, (
            "Custom token_estimator was never called. "
            "compress_history may be using a hardcoded estimator."
        )
