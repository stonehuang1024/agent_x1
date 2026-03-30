"""Tests for Prune mechanism (_prune_large_outputs) and LLM summary compression.

Tests verify the contract: prune only affects tool messages outside the
protect window that exceed the token threshold, and LLM summary respects
all four trigger conditions.
"""

import math
from unittest.mock import MagicMock

import pytest

from src.core.models import Message, Role
from src.context.context_compressor import ContextCompressor
from src.context.compression_archive import CompressionArchive


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _big_tool_msg(chars: int = 20000, index: int = 0) -> Message:
    """Create a tool message with *chars* characters of content."""
    return Message(
        role=Role.TOOL.value,
        content="x" * chars,
        tool_call_id=f"call_{index}",
        name="read_file",
    )


def _small_tool_msg(index: int = 0) -> Message:
    return Message(
        role=Role.TOOL.value,
        content="small output",
        tool_call_id=f"call_{index}",
        name="grep",
    )


# -----------------------------------------------------------------------
# Prune — basic behaviour
# -----------------------------------------------------------------------

class TestPruneLargeOutputs:

    def test_big_tool_msg_outside_window_is_pruned(self):
        comp = ContextCompressor(prune_minimum_tokens=100, prune_protect_window=2)
        msgs = [_big_tool_msg(5000, 0), _small_tool_msg(1), _small_tool_msg(2)]
        result = comp._prune_large_outputs(msgs, keep_recent=2)
        assert result[0].compression_state == "pruned"
        assert "pruned" in result[0].content
        # Recent messages untouched
        assert result[1].content == "small output"
        assert result[2].content == "small output"

    def test_big_tool_msg_inside_protect_window_not_pruned(self):
        comp = ContextCompressor(prune_minimum_tokens=100, prune_protect_window=4)
        msgs = [_big_tool_msg(5000, 0), _small_tool_msg(1)]
        result = comp._prune_large_outputs(msgs, keep_recent=2)
        # Both within protect window (2 msgs, window=4)
        assert result[0].compression_state == "original"

    def test_small_tool_msg_not_pruned(self):
        comp = ContextCompressor(prune_minimum_tokens=100, prune_protect_window=1)
        msgs = [_small_tool_msg(0), _small_tool_msg(1), _small_tool_msg(2)]
        result = comp._prune_large_outputs(msgs, keep_recent=1)
        for m in result:
            assert m.compression_state == "original"

    def test_non_tool_msg_not_pruned(self):
        comp = ContextCompressor(prune_minimum_tokens=10, prune_protect_window=1)
        big_assistant = Message(role=Role.ASSISTANT.value, content="x" * 5000)
        msgs = [big_assistant, _small_tool_msg(1)]
        result = comp._prune_large_outputs(msgs, keep_recent=1)
        assert result[0].compression_state == "original"

    def test_summarized_msg_not_pruned(self):
        comp = ContextCompressor(prune_minimum_tokens=10, prune_protect_window=1)
        msg = Message(
            role=Role.TOOL.value,
            content="x" * 5000,
            tool_call_id="c1",
            compression_state="summarized",
        )
        msgs = [msg, _small_tool_msg(1), _small_tool_msg(2)]
        result = comp._prune_large_outputs(msgs, keep_recent=1)
        assert result[0].compression_state == "summarized"

    def test_pruned_format_has_head_and_tail(self):
        comp = ContextCompressor(
            prune_minimum_tokens=10,
            prune_protect_window=1,
            prune_preview_chars=5,
        )
        content = "ABCDE" + "x" * 5000 + "VWXYZ"
        msg = Message(role=Role.TOOL.value, content=content, tool_call_id="c1")
        msgs = [msg, _small_tool_msg(1)]
        result = comp._prune_large_outputs(msgs, keep_recent=1)
        assert result[0].content.startswith("ABCDE")
        assert result[0].content.endswith("VWXYZ")

    def test_archive_called_when_provided(self):
        archive = CompressionArchive()
        comp = ContextCompressor(prune_minimum_tokens=10, prune_protect_window=1)
        msgs = [_big_tool_msg(5000, 0), _small_tool_msg(1)]
        comp._prune_large_outputs(msgs, keep_recent=1, archive=archive)
        assert archive.get_archive_count() == 1

    def test_archive_none_does_not_crash(self):
        comp = ContextCompressor(prune_minimum_tokens=10, prune_protect_window=1)
        msgs = [_big_tool_msg(5000, 0), _small_tool_msg(1)]
        result = comp._prune_large_outputs(msgs, keep_recent=1, archive=None)
        assert result[0].compression_state == "pruned"

    def test_original_list_not_mutated(self):
        comp = ContextCompressor(prune_minimum_tokens=10, prune_protect_window=1)
        msgs = [_big_tool_msg(5000, 0), _small_tool_msg(1)]
        original_content = msgs[0].content
        comp._prune_large_outputs(msgs, keep_recent=1)
        assert msgs[0].content == original_content


# -----------------------------------------------------------------------
# LLM Summary — trigger conditions
# -----------------------------------------------------------------------

class TestLLMSummaryShouldTrigger:

    def _compressor(self, **kwargs):
        defaults = dict(
            min_summary_tokens=100,
            min_summary_interval=2,
            llm_caller=lambda prompt: "<state_snapshot>summary</state_snapshot>",
        )
        defaults.update(kwargs)
        return ContextCompressor(**defaults)

    def test_below_warning_threshold_skips(self):
        comp = self._compressor()
        msgs = [Message(role="user", content="x" * 1000)] * 10
        assert comp._should_llm_summarize(msgs, utilization=0.5, warning_threshold=0.8) is False

    def test_above_warning_threshold_triggers(self):
        comp = self._compressor()
        msgs = [Message(role="user", content="x" * 1000)] * 10
        assert comp._should_llm_summarize(msgs, utilization=0.85, warning_threshold=0.8) is True

    def test_insufficient_interval_skips(self):
        comp = self._compressor(min_summary_interval=20)
        comp._last_summary_msg_count = 5
        msgs = [Message(role="user", content="x" * 1000)] * 10
        assert comp._should_llm_summarize(msgs, utilization=0.85, warning_threshold=0.8) is False

    def test_insufficient_tokens_skips(self):
        comp = self._compressor(min_summary_tokens=999999)
        msgs = [Message(role="user", content="short")] * 10
        assert comp._should_llm_summarize(msgs, utilization=0.85, warning_threshold=0.8) is False

    def test_already_executed_this_rebuild_skips(self):
        comp = self._compressor()
        comp._summary_executed_this_rebuild = True
        msgs = [Message(role="user", content="x" * 1000)] * 10
        assert comp._should_llm_summarize(msgs, utilization=0.85, warning_threshold=0.8) is False

    def test_reset_rebuild_state_clears_flag(self):
        comp = self._compressor()
        comp._summary_executed_this_rebuild = True
        comp.reset_rebuild_state()
        assert comp._summary_executed_this_rebuild is False


# -----------------------------------------------------------------------
# LLM Summary — execution
# -----------------------------------------------------------------------

class TestLLMSummarizeExecution:

    def test_normal_summary_replaces_old_messages(self):
        mock_llm = MagicMock(return_value="## Decisions\n- Used React")
        comp = ContextCompressor(
            min_summary_tokens=10,
            min_summary_interval=1,
            llm_caller=mock_llm,
            keep_recent=2,
        )
        system = Message.system("You are helpful")
        old_msgs = [
            Message(role="user", content="x" * 500),
            Message(role="assistant", content="y" * 500),
            Message(role="user", content="z" * 500),
            Message(role="assistant", content="w" * 500),
        ]
        recent = [
            Message(role="user", content="latest question"),
            Message(role="assistant", content="latest answer"),
        ]
        all_msgs = [system] + old_msgs + recent

        result = comp._llm_summarize(
            all_msgs, keep_recent=2, utilization=0.85, warning_threshold=0.8
        )
        mock_llm.assert_called_once()
        # Should have: system + summary_msg + 2 recent
        assert len(result) == 4
        # Summary message should contain metadata
        summary_msg = result[1]
        assert "<compression_metadata>" in summary_msg.content
        assert "recall_compressed_messages" in summary_msg.content
        assert summary_msg.compression_state == "summarized"

    def test_llm_failure_falls_back_to_placeholder(self):
        mock_llm = MagicMock(side_effect=RuntimeError("API timeout"))
        comp = ContextCompressor(
            min_summary_tokens=10,
            min_summary_interval=1,
            llm_caller=mock_llm,
            keep_recent=2,
        )
        system = Message.system("sys")
        msgs = [system] + [
            Message(role="user", content="x" * 500),
            Message(role="assistant", content="y" * 500),
            Message(role="user", content="a"),
            Message(role="assistant", content="b"),
        ]
        result = comp._llm_summarize(
            msgs, keep_recent=2, utilization=0.85, warning_threshold=0.8
        )
        # Should still return a valid message list (fallback)
        assert len(result) > 0
        # The summary_executed flag should NOT be set on failure
        assert comp._summary_executed_this_rebuild is False

    def test_summarized_messages_not_re_summarized(self):
        mock_llm = MagicMock(return_value="summary")
        comp = ContextCompressor(
            min_summary_tokens=10,
            min_summary_interval=1,
            llm_caller=mock_llm,
            keep_recent=1,
        )
        already_summarized = Message(
            role="system",
            content="previous summary",
            compression_state="summarized",
        )
        old_user = Message(role="user", content="x" * 500)
        recent = Message(role="user", content="latest")
        msgs = [already_summarized, old_user, recent]

        result = comp._llm_summarize(
            msgs, keep_recent=1, utilization=0.85, warning_threshold=0.8
        )
        # The already_summarized message should be preserved
        preserved = [m for m in result if m.content == "previous summary"]
        assert len(preserved) == 1

    def test_archive_stores_originals(self):
        mock_llm = MagicMock(return_value="summary text")
        archive = CompressionArchive()
        comp = ContextCompressor(
            min_summary_tokens=10,
            min_summary_interval=1,
            llm_caller=mock_llm,
            keep_recent=1,
        )
        system = Message.system("sys")
        msgs = [system] + [
            Message(role="user", content="x" * 500),
            Message(role="assistant", content="y" * 500),
            Message(role="user", content="latest"),
        ]
        comp._llm_summarize(
            msgs, keep_recent=1, archive=archive,
            utilization=0.85, warning_threshold=0.8,
        )
        assert archive.get_archive_count() == 1

    def test_no_llm_caller_returns_original(self):
        comp = ContextCompressor(
            min_summary_tokens=10,
            min_summary_interval=1,
            llm_caller=None,
            keep_recent=2,
        )
        msgs = [Message(role="user", content="x" * 500)] * 6
        result = comp._llm_summarize(
            msgs, keep_recent=2, utilization=0.85, warning_threshold=0.8
        )
        assert result == msgs


# -----------------------------------------------------------------------
# Build summary prompt
# -----------------------------------------------------------------------

class TestBuildSummaryPrompt:

    def test_prompt_contains_state_snapshot_format(self):
        comp = ContextCompressor()
        msgs = [Message(role="user", content="hello")]
        prompt = comp._build_summary_prompt(msgs)
        assert "<state_snapshot>" in prompt
        assert "## Decisions" in prompt
        assert "[user]: hello" in prompt

    def test_prompt_caps_message_length(self):
        comp = ContextCompressor()
        msgs = [Message(role="user", content="x" * 5000)]
        prompt = comp._build_summary_prompt(msgs)
        # Content should be capped at 2000 chars
        assert "x" * 2001 not in prompt


# -----------------------------------------------------------------------
# Backward compatibility
# -----------------------------------------------------------------------

class TestCompressorBackwardCompat:

    def test_old_style_init_still_works(self):
        comp = ContextCompressor(
            max_tool_output_length=500,
            summary_threshold=10,
            keep_recent=3,
            low_importance_threshold=0.3,
        )
        assert comp.max_tool_output_length == 500
        assert comp.keep_recent == 3
        # New fields should have defaults
        assert comp.prune_minimum_tokens == 5000
        assert comp.max_assistant_output_length == 3000

    def test_context_config_init(self):
        from src.core.config import ContextConfig
        cfg = ContextConfig(
            max_tool_output_length=2000,
            keep_recent=6,
            prune_minimum_tokens=10000,
        )
        comp = ContextCompressor(context_config=cfg)
        assert comp.max_tool_output_length == 2000
        assert comp.keep_recent == 6
        assert comp.prune_minimum_tokens == 10000
