"""End-to-end integration test for the 5-Phase compression pipeline.

Simulates a 20+ step conversation, verifying:
- Progressive compression triggers at correct utilization levels
- 5-Phase pipeline executes in order with correct skip logic
- LLM summary and strategy compression coordinate properly
- CompressionArchive stores and recalls correctly
- Compression awareness injected in system prompt when needed
- Config propagation from ContextConfig to all components
"""

import logging
import math
from unittest.mock import MagicMock, patch

import pytest

from src.core.config import ContextConfig
from src.core.models import Message, Role
from src.core.events import AgentEvent, EventBus
from src.context.context_assembler import ContextAssembler
from src.context.context_window import ContextBudget, ContextWindow, CompressionLevel
from src.context.context_compressor import ContextCompressor
from src.context.compression_archive import CompressionArchive
from src.context.system_reminder import SystemReminderBuilder
from src.tools.context_tools import (
    recall_compressed_messages,
    set_archive_instance,
)


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _make_assembler(
    max_tokens: int = 4000,
    llm_caller=None,
    event_bus=None,
) -> ContextAssembler:
    """Build a minimal ContextAssembler with a tight budget for testing."""
    mock_sm = MagicMock()
    mock_sm.active_session = None
    mock_sm.get_recent_turns.return_value = []

    cfg = ContextConfig(
        context_window_tokens=max_tokens,
        reserve_tokens=max(1, max_tokens // 10),
        soft_threshold=0.7,
        warning_threshold=0.8,
        critical_threshold=0.95,
        max_tool_output_length=200,
        max_assistant_output_length=300,
        keep_recent=4,
        min_keep_recent=2,
        min_summary_tokens=100,
        min_summary_interval=2,
        summary_threshold=5,
        prune_minimum_tokens=50,
        prune_protect_window=4,
        prune_preview_chars=20,
        recall_max_tokens=2000,
        frequent_summary_warning_count=3,
    )

    assembler = ContextAssembler(
        session_manager=mock_sm,
        context_config=cfg,
        event_bus=event_bus,
    )
    # Inject LLM caller into compressor
    if llm_caller is not None:
        assembler.compressor.llm_caller = llm_caller

    return assembler


def _simulate_conversation(n_turns: int, tool_output_size: int = 500) -> list:
    """Generate a realistic conversation with user/assistant/tool messages."""
    msgs = []
    for i in range(n_turns):
        msgs.append(Message(role="user", content=f"Step {i}: do something"))
        msgs.append(Message(
            role="assistant",
            content=f"I'll execute step {i}. " + "detail " * 50,
            tool_calls=[{"id": f"call_{i}", "function": {"name": "read_file", "arguments": "{}"}}],
        ))
        msgs.append(Message(
            role="tool",
            content=f"result_{i}_" + "x" * tool_output_size,
            tool_call_id=f"call_{i}",
            name="read_file",
        ))
    return msgs


# -----------------------------------------------------------------------
# Pipeline phase execution
# -----------------------------------------------------------------------

class TestPipelinePhaseExecution:

    def test_phase2_truncation_always_runs(self):
        """Phase 2 truncation runs even at low utilization (proactive)."""
        # Use large prune_minimum_tokens so prune doesn't eat the messages,
        # leaving them for Phase 2 truncation
        mock_sm = MagicMock()
        mock_sm.active_session = None
        mock_sm.get_recent_turns.return_value = []
        cfg = ContextConfig(
            context_window_tokens=50000,
            reserve_tokens=4096,
            max_tool_output_length=200,
            max_assistant_output_length=300,
            keep_recent=4,
            min_keep_recent=2,
            prune_minimum_tokens=99999,  # disable prune
            prune_protect_window=4,
            prune_preview_chars=20,
        )
        assembler = ContextAssembler(session_manager=mock_sm, context_config=cfg)
        msgs = _simulate_conversation(8, tool_output_size=2000)
        result = assembler.rebuild(msgs)
        # Old tool messages should be truncated
        old_tools = [m for m in result if m.role == "tool" and "truncated" in (m.content or "")]
        assert len(old_tools) > 0, "Phase 2 should truncate old tool outputs proactively"

    def test_phase4_skipped_when_utilization_low(self):
        """LLM summary should NOT trigger when utilization is below warning."""
        mock_llm = MagicMock(return_value="summary")
        assembler = _make_assembler(max_tokens=100000, llm_caller=mock_llm)
        msgs = _simulate_conversation(5, tool_output_size=100)
        assembler.rebuild(msgs)
        mock_llm.assert_not_called()

    def test_phase4_triggers_when_utilization_high(self):
        """LLM summary triggers when utilization exceeds warning threshold."""
        mock_llm = MagicMock(return_value="## Decisions\n- Used React")
        # Use a tight budget with high prune threshold so prune doesn't reduce much
        mock_sm = MagicMock()
        mock_sm.active_session = None
        mock_sm.get_recent_turns.return_value = []
        cfg = ContextConfig(
            context_window_tokens=1800,
            reserve_tokens=100,
            soft_threshold=0.7,
            warning_threshold=0.8,
            critical_threshold=0.95,
            max_tool_output_length=5000,  # high — don't truncate
            max_assistant_output_length=5000,  # high — don't truncate
            keep_recent=2,
            min_keep_recent=2,
            min_summary_tokens=50,
            min_summary_interval=1,
            summary_threshold=2,
            prune_minimum_tokens=99999,  # disable prune
            prune_protect_window=100,
            prune_preview_chars=20,
        )
        assembler = ContextAssembler(session_manager=mock_sm, context_config=cfg)
        assembler.compressor.llm_caller = mock_llm
        msgs = _simulate_conversation(8, tool_output_size=200)
        assembler.rebuild(msgs)
        mock_llm.assert_called_once()

    def test_phase5_emergency_on_critical(self):
        """Phase 5 emergency compression triggers at critical utilization."""
        # No LLM caller — so Phase 4 can't reduce, forcing Phase 5
        assembler = _make_assembler(max_tokens=1500)
        msgs = _simulate_conversation(8, tool_output_size=300)
        result = assembler.rebuild(msgs)
        # Emergency compression should reduce the output significantly
        tokens = assembler.window.estimate_tokens(result)
        original_tokens = assembler.window.estimate_tokens(msgs)
        assert tokens < original_tokens, "Emergency compression should reduce tokens"


# -----------------------------------------------------------------------
# Compression state tracking
# -----------------------------------------------------------------------

class TestCompressionStateTracking:

    def test_pruned_messages_have_correct_state(self):
        """Messages exceeding prune_minimum_tokens outside protect window get pruned."""
        # Use large tool outputs and low prune threshold
        mock_sm = MagicMock()
        mock_sm.active_session = None
        mock_sm.get_recent_turns.return_value = []
        cfg = ContextConfig(
            context_window_tokens=50000,
            reserve_tokens=4096,
            prune_minimum_tokens=30,  # very low — prune aggressively
            prune_protect_window=4,
            prune_preview_chars=20,
        )
        assembler = ContextAssembler(session_manager=mock_sm, context_config=cfg)
        msgs = _simulate_conversation(10, tool_output_size=2000)
        result = assembler.rebuild(msgs)
        pruned = [m for m in result if getattr(m, "compression_state", "") == "pruned"]
        assert len(pruned) > 0, "Some messages should be pruned"

    def test_truncated_messages_have_correct_state(self):
        # Disable prune so Phase 2 truncation can set compression_state
        mock_sm = MagicMock()
        mock_sm.active_session = None
        mock_sm.get_recent_turns.return_value = []
        cfg = ContextConfig(
            context_window_tokens=50000,
            reserve_tokens=4096,
            max_tool_output_length=200,
            max_assistant_output_length=300,
            keep_recent=4,
            min_keep_recent=2,
            prune_minimum_tokens=99999,  # disable prune
            prune_protect_window=4,
            prune_preview_chars=20,
        )
        assembler = ContextAssembler(session_manager=mock_sm, context_config=cfg)
        msgs = _simulate_conversation(8, tool_output_size=2000)
        result = assembler.rebuild(msgs)
        truncated = [m for m in result if getattr(m, "compression_state", "") == "truncated"]
        assert len(truncated) > 0, "Some messages should be truncated"

    def test_summarized_messages_not_re_compressed(self):
        """Messages with compression_state='summarized' should not be pruned or truncated."""
        assembler = _make_assembler(max_tokens=50000)
        summary_msg = Message(
            role="system",
            content="Previous summary " + "x" * 5000,
            compression_state="summarized",
        )
        msgs = [summary_msg] + _simulate_conversation(5, tool_output_size=100)
        result = assembler.rebuild(msgs)
        # The summary message should still be present and unchanged
        summaries = [m for m in result if m.compression_state == "summarized"]
        assert len(summaries) >= 1


# -----------------------------------------------------------------------
# Archive and recall
# -----------------------------------------------------------------------

class TestArchiveAndRecall:

    def test_archive_populated_after_prune(self):
        assembler = _make_assembler(max_tokens=50000)
        msgs = _simulate_conversation(10, tool_output_size=2000)
        assembler.rebuild(msgs)
        assert assembler._archive.has_archives(), "Archive should have entries after pruning"

    def test_recall_tool_returns_original_content(self):
        assembler = _make_assembler(max_tokens=50000)
        msgs = _simulate_conversation(10, tool_output_size=2000)
        assembler.rebuild(msgs)

        # Get an archive_id from the pruned messages
        set_archive_instance(assembler._archive)
        try:
            pruned = [m for m in assembler.rebuild(msgs)
                      if "archive_id=" in (m.content or "")]
            if pruned:
                import re
                match = re.search(r"archive_id=([a-f0-9-]+)", pruned[0].content)
                if match:
                    aid = match.group(1)
                    result = recall_compressed_messages(aid)
                    assert "[Archive Recall]" in result
        finally:
            set_archive_instance(None)


# -----------------------------------------------------------------------
# Compression awareness in system prompt
# -----------------------------------------------------------------------

class TestCompressionAwareness:

    def test_no_compression_no_guidance(self):
        builder = SystemReminderBuilder()
        result = builder.build("hello", has_compression=False)
        assert "compressedContext" not in result

    def test_compression_injects_guidance(self):
        builder = SystemReminderBuilder()
        result = builder.build("hello", has_compression=True)
        assert "compressedContext" in result
        assert "recall_compressed_messages" in result


# -----------------------------------------------------------------------
# Config propagation
# -----------------------------------------------------------------------

class TestConfigPropagation:

    def test_context_config_flows_to_compressor(self):
        cfg = ContextConfig(
            max_tool_output_length=999,
            keep_recent=7,
            prune_minimum_tokens=12345,
        )
        mock_sm = MagicMock()
        mock_sm.active_session = None
        mock_sm.get_recent_turns.return_value = []
        assembler = ContextAssembler(session_manager=mock_sm, context_config=cfg)
        assert assembler.compressor.max_tool_output_length == 999
        assert assembler.compressor.keep_recent == 7
        assert assembler.compressor.prune_minimum_tokens == 12345

    def test_context_config_flows_to_window(self):
        cfg = ContextConfig(
            context_window_tokens=200000,
            reserve_tokens=8192,
            soft_threshold=0.6,
        )
        mock_sm = MagicMock()
        mock_sm.active_session = None
        mock_sm.get_recent_turns.return_value = []
        assembler = ContextAssembler(session_manager=mock_sm, context_config=cfg)
        assert assembler.window.budget.max_tokens == 200000
        assert assembler.window.budget.reserve_tokens == 8192
        assert assembler.window.budget.soft_threshold == 0.6

    def test_backward_compat_max_tokens_init(self):
        """Old-style max_tokens init should still work."""
        mock_sm = MagicMock()
        mock_sm.active_session = None
        mock_sm.get_recent_turns.return_value = []
        assembler = ContextAssembler(session_manager=mock_sm, max_tokens=64000)
        assert assembler.window.budget.max_tokens == 64000


# -----------------------------------------------------------------------
# Event emission
# -----------------------------------------------------------------------

class TestEventEmission:

    def test_pipeline_completed_event_emitted(self):
        bus = EventBus()
        events_received = []
        bus.subscribe(AgentEvent.COMPRESSION_PIPELINE_COMPLETED, lambda p: events_received.append(p))

        assembler = _make_assembler(max_tokens=50000, event_bus=bus)
        msgs = _simulate_conversation(8, tool_output_size=800)
        assembler.rebuild(msgs)

        assert len(events_received) >= 1, "COMPRESSION_PIPELINE_COMPLETED event should be emitted"
        payload = events_received[0]
        # Event payload has data dict with phase_stats, original_tokens, etc.
        assert "phase_stats" in payload.data
        assert "original_tokens" in payload.data
        assert "final_tokens" in payload.data


# -----------------------------------------------------------------------
# Consecutive summary warning
# -----------------------------------------------------------------------

class TestConsecutiveSummaryWarning:

    def test_consecutive_summaries_trigger_warning(self, caplog):
        """After N consecutive LLM summaries, a warning should be logged."""
        mock_llm = MagicMock(return_value="## Summary\n- done")
        # Tight budget, disable prune/truncate so LLM summary always triggers
        mock_sm = MagicMock()
        mock_sm.active_session = None
        mock_sm.get_recent_turns.return_value = []
        cfg = ContextConfig(
            context_window_tokens=1800,
            reserve_tokens=100,
            soft_threshold=0.7,
            warning_threshold=0.8,
            critical_threshold=0.95,
            max_tool_output_length=5000,
            max_assistant_output_length=5000,
            keep_recent=2,
            min_keep_recent=2,
            min_summary_tokens=50,
            min_summary_interval=1,
            summary_threshold=2,
            prune_minimum_tokens=99999,
            prune_protect_window=100,
            prune_preview_chars=20,
            frequent_summary_warning_count=3,
        )
        assembler = ContextAssembler(session_manager=mock_sm, context_config=cfg)
        assembler.compressor.llm_caller = mock_llm
        msgs = _simulate_conversation(8, tool_output_size=200)

        with caplog.at_level(logging.WARNING):
            for _ in range(4):
                assembler.compressor.reset_rebuild_state()
                assembler.compressor._last_summary_msg_count = 0
                assembler.rebuild(msgs)

        warning_msgs = [r for r in caplog.records if "Frequent LLM summarization" in r.message]
        assert len(warning_msgs) >= 1, "Should warn about frequent LLM summarization"
