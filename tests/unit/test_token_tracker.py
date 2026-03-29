"""
Tests for TokenTracker (src/util/token_tracker.py).

Bug targets:
- Cumulative counters: tokens not accumulated correctly across calls
- Zero/boundary values: zero tokens, very large values cause overflow
- Statistics calculation: averages wrong, division by zero
- Reset: counters not fully cleared
- Property accessors: return stale or wrong values
"""

import time

import pytest

from src.util.token_tracker import TokenTracker


class TestCumulativeCounters:
    """Verify cumulative token counting accuracy."""

    def test_single_llm_call_recorded(self):
        """Bug: single call not recorded, counters stay at zero."""
        tracker = TokenTracker()
        tracker.record_llm_call(input_tokens=100, output_tokens=50, duration_s=1.0)

        assert tracker.total_input_tokens == 100
        assert tracker.total_output_tokens == 50
        assert tracker.total_tokens == 150
        assert tracker.llm_call_count == 1

    def test_multiple_llm_calls_accumulated(self):
        """Bug: only last call's tokens stored, previous calls overwritten."""
        tracker = TokenTracker()
        tracker.record_llm_call(input_tokens=100, output_tokens=50, duration_s=1.0)
        tracker.record_llm_call(input_tokens=200, output_tokens=100, duration_s=2.0)
        tracker.record_llm_call(input_tokens=300, output_tokens=150, duration_s=1.5)

        assert tracker.total_input_tokens == 600, (
            f"Expected 600 input tokens, got {tracker.total_input_tokens} — "
            f"tokens not accumulated across calls"
        )
        assert tracker.total_output_tokens == 300
        assert tracker.total_tokens == 900
        assert tracker.llm_call_count == 3

    def test_tool_calls_counted(self):
        """Bug: tool call counter not incremented."""
        tracker = TokenTracker()
        tracker.record_tool_call("read_file", duration_ms=100, success=True)
        tracker.record_tool_call("write_file", duration_ms=200, success=True)
        tracker.record_tool_call("bash", duration_ms=50, success=False)

        assert tracker.tool_call_count == 3

    def test_tool_success_failure_separated(self):
        """Bug: success/failure counts not tracked separately."""
        tracker = TokenTracker()
        tracker.record_tool_call("read_file", duration_ms=100, success=True)
        tracker.record_tool_call("write_file", duration_ms=200, success=True)
        tracker.record_tool_call("bash", duration_ms=50, success=False)

        stats = tracker.get_stats()
        assert stats["tool_success_count"] == 2
        assert stats["tool_failure_count"] == 1


class TestBoundaryValues:
    """Verify behavior with edge-case values."""

    def test_zero_tokens(self):
        """Bug: zero tokens cause error or are treated as missing."""
        tracker = TokenTracker()
        tracker.record_llm_call(input_tokens=0, output_tokens=0, duration_s=0.0)

        assert tracker.total_tokens == 0
        assert tracker.llm_call_count == 1

    def test_very_large_tokens(self):
        """Bug: large token values cause integer overflow or formatting error."""
        tracker = TokenTracker()
        tracker.record_llm_call(
            input_tokens=10_000_000,
            output_tokens=5_000_000,
            duration_s=30.0,
        )

        assert tracker.total_tokens == 15_000_000
        stats = tracker.get_stats()
        assert stats["total_tokens"] == 15_000_000

    def test_zero_duration(self):
        """Bug: zero duration causes division by zero in rate calculations."""
        tracker = TokenTracker()
        tracker.record_llm_call(input_tokens=100, output_tokens=50, duration_s=0.0)
        tracker.record_tool_call("test", duration_ms=0.0, success=True)

        # Should not raise
        stats = tracker.get_stats()
        assert stats["llm_call_count"] == 1


class TestStatistics:
    """Verify statistics calculations."""

    def test_average_llm_duration(self):
        """Bug: average duration calculated wrong."""
        tracker = TokenTracker()
        tracker.record_llm_call(input_tokens=100, output_tokens=50, duration_s=1.0)
        tracker.record_llm_call(input_tokens=100, output_tokens=50, duration_s=3.0)

        stats = tracker.get_stats()
        assert abs(stats["avg_llm_duration_s"] - 2.0) < 0.01, (
            f"Average duration {stats['avg_llm_duration_s']} != 2.0"
        )

    def test_average_tokens_per_call(self):
        """Bug: average tokens per call wrong."""
        tracker = TokenTracker()
        tracker.record_llm_call(input_tokens=100, output_tokens=50, duration_s=1.0)
        tracker.record_llm_call(input_tokens=200, output_tokens=100, duration_s=2.0)

        stats = tracker.get_stats()
        # Total: 450 tokens, 2 calls → 225 avg
        assert abs(stats["avg_tokens_per_call"] - 225.0) < 0.01

    def test_stats_with_no_calls(self):
        """Bug: get_stats with zero calls causes division by zero."""
        tracker = TokenTracker()
        stats = tracker.get_stats()

        assert stats["llm_call_count"] == 0
        assert stats["avg_llm_duration_s"] == 0.0
        assert stats["avg_tokens_per_call"] == 0.0
        assert stats["total_tokens"] == 0

    def test_session_duration_increases(self):
        """Bug: session_duration_s returns 0 or negative."""
        tracker = TokenTracker()
        time.sleep(0.05)  # Small sleep to ensure measurable duration

        stats = tracker.get_stats()
        assert stats["session_duration_s"] > 0, (
            "Session duration is not positive"
        )

    def test_get_stats_returns_all_expected_keys(self):
        """Bug: missing keys in stats dict causes KeyError in consumers."""
        tracker = TokenTracker()
        stats = tracker.get_stats()

        expected_keys = [
            "total_input_tokens", "total_output_tokens", "total_tokens",
            "llm_call_count", "tool_call_count",
            "tool_success_count", "tool_failure_count",
            "session_duration_s", "avg_llm_duration_s", "avg_tokens_per_call",
        ]
        for key in expected_keys:
            assert key in stats, (
                f"Missing key '{key}' in stats — "
                f"consumers will get KeyError"
            )


class TestReset:
    """Verify reset clears all state."""

    def test_reset_clears_all_counters(self):
        """Bug: reset doesn't clear all counters, stale data persists."""
        tracker = TokenTracker()
        tracker.record_llm_call(input_tokens=100, output_tokens=50, duration_s=1.0)
        tracker.record_tool_call("test", duration_ms=100, success=True)

        tracker.reset()

        assert tracker.total_input_tokens == 0
        assert tracker.total_output_tokens == 0
        assert tracker.total_tokens == 0
        assert tracker.llm_call_count == 0
        assert tracker.tool_call_count == 0

        stats = tracker.get_stats()
        assert stats["tool_success_count"] == 0
        assert stats["tool_failure_count"] == 0

    def test_reset_clears_detailed_records(self):
        """Bug: reset clears counters but not detailed records,
        causing memory leak over long sessions."""
        tracker = TokenTracker()
        for i in range(100):
            tracker.record_llm_call(input_tokens=100, output_tokens=50, duration_s=1.0)

        tracker.reset()
        assert len(tracker._llm_calls) == 0, "LLM call records not cleared"
        assert len(tracker._tool_calls) == 0, "Tool call records not cleared"


class TestPropertyAccessors:
    """Verify property accessors return correct values."""

    def test_properties_match_stats(self):
        """Bug: properties return different values than get_stats()."""
        tracker = TokenTracker()
        tracker.record_llm_call(input_tokens=100, output_tokens=50, duration_s=1.0)
        tracker.record_tool_call("test", duration_ms=100, success=True)

        stats = tracker.get_stats()
        assert tracker.total_input_tokens == stats["total_input_tokens"]
        assert tracker.total_output_tokens == stats["total_output_tokens"]
        assert tracker.total_tokens == stats["total_tokens"]
        assert tracker.llm_call_count == stats["llm_call_count"]
        assert tracker.tool_call_count == stats["tool_call_count"]
