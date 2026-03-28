"""Tests for ContextWindow — token estimation precision and budget management.

Bug classes covered:
- Token estimation wildly inaccurate (old len//4+10 vs new len/3.5)
- tool_calls JSON not counted in token estimation
- Per-message overhead (4 tokens) missing
- Priming overhead (3 tokens) missing
- add() does not write token_count back to Message
- reset() leaves stale state
- fits() boundary off-by-one
- add() modifies state even when budget exceeded
- should_warn() / should_compress() threshold logic inverted
"""

import math
import json
import pytest

from src.core.models import Message, Role
from src.context.context_window import ContextWindow, ContextBudget, _CHARS_PER_TOKEN, _MSG_OVERHEAD, _PRIMING_OVERHEAD


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_budget(max_tokens=1000, reserve=100):
    return ContextBudget(max_tokens=max_tokens, reserve_tokens=reserve)


def _make_window(max_tokens=1000, reserve=100):
    return ContextWindow(_make_budget(max_tokens, reserve))


def _expected_text_tokens(text: str) -> int:
    """Expected tokens for a text string using the design formula."""
    return math.ceil(len(text) / _CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# 1. Token estimation precision
#    Bug: old formula (len//4+10) diverges from design spec (len/3.5)
# ---------------------------------------------------------------------------

class TestTokenEstimation:
    """Catches: estimation formula deviating from design spec."""

    def test_pure_text_estimation_uses_3_5_ratio(self):
        """Token estimate for pure text must use len(content)/3.5, not len//4+10.
        Bug: if old formula is still in place, estimates will be systematically wrong."""
        content = "a" * 350  # 350 chars → 350/3.5 = 100 tokens + overhead
        msg = Message.user(content)
        window = _make_window(max_tokens=10000)

        estimated = window.estimate_tokens([msg])
        expected_content_tokens = _expected_text_tokens(content)
        expected_total = _PRIMING_OVERHEAD + _MSG_OVERHEAD + expected_content_tokens

        assert estimated == expected_total, (
            f"Expected {expected_total} tokens (priming={_PRIMING_OVERHEAD} + "
            f"overhead={_MSG_OVERHEAD} + content={expected_content_tokens}), "
            f"got {estimated}. Is the old len//4+10 formula still in use?"
        )

    def test_estimation_includes_per_message_overhead(self):
        """Each message must add _MSG_OVERHEAD (4) tokens for role+formatting.
        Bug: overhead missing → underestimate → budget overrun."""
        msg = Message.user("")  # empty content
        window = _make_window()

        estimated = window.estimate_tokens([msg])
        # Empty content → 0 content tokens, but still overhead + priming
        assert estimated >= _PRIMING_OVERHEAD + _MSG_OVERHEAD, (
            f"Empty message estimated at {estimated} tokens, but should be at least "
            f"{_PRIMING_OVERHEAD + _MSG_OVERHEAD} (priming + per-msg overhead)"
        )

    def test_estimation_includes_priming_overhead(self):
        """Conversation must include _PRIMING_OVERHEAD (3) tokens.
        Bug: priming missing → systematic underestimate."""
        window = _make_window()
        # Two identical messages
        msg = Message.user("hello")
        est_one = window.estimate_tokens([msg])
        est_two = window.estimate_tokens([msg, msg])

        # Difference should be exactly one message's worth (no extra priming)
        diff = est_two - est_one
        single_msg_tokens = _MSG_OVERHEAD + _expected_text_tokens("hello")
        assert diff == single_msg_tokens, (
            f"Adding a second message should add {single_msg_tokens} tokens, "
            f"but diff was {diff}. Priming overhead may be counted per-message."
        )

    def test_tool_calls_json_counted(self):
        """tool_calls field must be serialized to JSON and counted.
        Bug: tool_calls ignored → massive underestimate for assistant messages."""
        tool_calls = [{"id": "call_1", "function": {"name": "read_file", "arguments": '{"path": "/foo/bar.py"}'}}]
        msg_with_tc = Message.assistant(content=None, tool_calls=tool_calls)
        msg_without_tc = Message.assistant(content=None, tool_calls=None)

        window = _make_window()
        est_with = window.estimate_tokens([msg_with_tc])
        est_without = window.estimate_tokens([msg_without_tc])

        tc_json_len = len(json.dumps(tool_calls))
        expected_tc_tokens = math.ceil(tc_json_len / _CHARS_PER_TOKEN)

        assert est_with > est_without, (
            f"Message with tool_calls ({est_with}) should estimate higher than "
            f"without ({est_without}). tool_calls JSON not being counted."
        )
        assert est_with - est_without == expected_tc_tokens, (
            f"tool_calls token difference should be {expected_tc_tokens}, "
            f"got {est_with - est_without}"
        )

    def test_estimate_single_no_priming(self):
        """estimate_single() must NOT include priming overhead.
        Bug: if priming is included per-message, add() double-counts."""
        msg = Message.user("test content")
        window = _make_window()

        single = window.estimate_single(msg)
        batch = window.estimate_tokens([msg])

        assert single == batch - _PRIMING_OVERHEAD, (
            f"estimate_single ({single}) should equal estimate_tokens ({batch}) "
            f"minus priming ({_PRIMING_OVERHEAD})"
        )


# ---------------------------------------------------------------------------
# 2. add() writes token_count to Message
#    Bug: token_count stays at 0 after add(), breaking downstream scoring
# ---------------------------------------------------------------------------

class TestAddWritesTokenCount:
    """Catches: add() not setting Message.token_count."""

    def test_add_sets_token_count_on_each_message(self):
        """After add(), every Message.token_count must be > 0.
        Bug: token_count stays at default 0, breaking importance-based compression."""
        msgs = [
            Message.system("You are a helpful assistant."),
            Message.user("Hello world"),
        ]
        window = _make_window(max_tokens=10000)

        assert all(m.token_count == 0 for m in msgs), "Precondition: token_count starts at 0"

        result = window.add(msgs)
        assert result is True

        for msg in msgs:
            assert msg.token_count > 0, (
                f"Message.token_count should be set after add(), "
                f"but got {msg.token_count} for role={msg.role}"
            )

    def test_add_token_count_matches_estimate_single(self):
        """token_count written by add() must match estimate_single() for each message.
        Bug: add() uses a different formula than estimate_single()."""
        msg = Message.user("Some content for estimation")
        window = _make_window(max_tokens=10000)

        expected = window.estimate_single(msg)
        window.add([msg])

        assert msg.token_count == expected, (
            f"token_count ({msg.token_count}) != estimate_single ({expected})"
        )


# ---------------------------------------------------------------------------
# 3. reset() clears all state
#    Bug: stale _current_usage or _message_counts after reset
# ---------------------------------------------------------------------------

class TestReset:
    """Catches: reset() leaving stale state that corrupts next build cycle."""

    def test_reset_clears_usage_and_counts(self):
        """After reset(), _current_usage must be 0 and _message_counts empty."""
        window = _make_window(max_tokens=10000)
        window.add([Message.user("some content")])

        assert window._current_usage > 0, "Precondition: usage should be > 0 after add"

        window.reset()

        assert window._current_usage == 0, (
            f"_current_usage should be 0 after reset, got {window._current_usage}"
        )
        assert window._message_counts == [], (
            f"_message_counts should be empty after reset, got {window._message_counts}"
        )

    def test_reset_allows_full_budget_reuse(self):
        """After reset(), the full budget should be available again.
        Bug: reset doesn't clear usage → second build cycle has reduced budget."""
        budget = _make_budget(max_tokens=200, reserve=50)
        window = ContextWindow(budget)

        # Fill most of the budget
        big_msg = Message.user("x" * 500)
        window.add([big_msg])
        remaining_before = window.remaining()

        window.reset()

        assert window.remaining() == budget.available_for_context, (
            f"After reset, remaining should be {budget.available_for_context}, "
            f"got {window.remaining()}"
        )


# ---------------------------------------------------------------------------
# 4. fits() boundary conditions
#    Bug: off-by-one allows one extra token, or rejects exactly-fitting messages
# ---------------------------------------------------------------------------

class TestFitsBoundary:
    """Catches: off-by-one in fits() comparison."""

    def test_fits_exactly_at_budget(self):
        """Messages that exactly fill the budget must fit (<=, not <).
        Bug: strict < comparison rejects messages that exactly fit."""
        window = _make_window(max_tokens=200, reserve=0)
        # We need to craft a message whose estimate exactly equals 200
        # estimate = priming(3) + overhead(4) + ceil(len/3.5)
        # We want 200 = 3 + 4 + ceil(len/3.5) → ceil(len/3.5) = 193 → len = 193*3.5 = 675.5 → len=675 gives ceil(675/3.5)=193
        content = "a" * 675
        msg = Message.user(content)
        est = window.estimate_tokens([msg])

        # Adjust budget to exactly match
        window.budget.max_tokens = est
        window.budget.reserve_tokens = 0

        assert window.fits([msg]), (
            f"Message with {est} tokens should fit in budget of {est}. "
            f"fits() may use < instead of <="
        )

    def test_does_not_fit_one_over(self):
        """Messages exceeding budget by 1 token must NOT fit."""
        window = _make_window(max_tokens=100, reserve=0)
        # Fill to capacity
        msg = Message.user("a" * 350)  # ~100+ tokens
        est = window.estimate_tokens([msg])
        window.budget.max_tokens = est - 1  # 1 less than needed

        assert not window.fits([msg]), (
            f"Message needing {est} tokens should NOT fit in budget of {est - 1}"
        )


# ---------------------------------------------------------------------------
# 5. add() rejects over-budget without modifying state
#    Bug: add() partially modifies state before checking budget
# ---------------------------------------------------------------------------

class TestAddRejectsOverBudget:
    """Catches: add() corrupting state on rejection."""

    def test_add_returns_false_over_budget(self):
        """add() must return False when messages exceed remaining budget."""
        window = _make_window(max_tokens=50, reserve=0)
        big_msg = Message.user("x" * 500)  # Way over budget

        result = window.add([big_msg])
        assert result is False

    def test_add_does_not_modify_state_on_rejection(self):
        """When add() returns False, _current_usage must be unchanged.
        Bug: usage incremented before budget check, leaving inconsistent state."""
        window = _make_window(max_tokens=50, reserve=0)
        usage_before = window._current_usage
        counts_before = list(window._message_counts)

        big_msg = Message.user("x" * 500)
        window.add([big_msg])

        assert window._current_usage == usage_before, (
            f"_current_usage changed from {usage_before} to {window._current_usage} "
            f"after rejected add()"
        )
        assert window._message_counts == counts_before, (
            f"_message_counts changed after rejected add()"
        )

    def test_add_does_not_write_token_count_on_rejection(self):
        """When add() returns False, Message.token_count must NOT be modified.
        Bug: token_count written before budget check."""
        window = _make_window(max_tokens=50, reserve=0)
        msg = Message.user("x" * 500)
        assert msg.token_count == 0

        window.add([msg])

        assert msg.token_count == 0, (
            f"token_count should remain 0 after rejected add(), got {msg.token_count}"
        )


# ---------------------------------------------------------------------------
# 6. should_warn() and should_compress() thresholds
#    Bug: comparison direction inverted, or wrong threshold used
# ---------------------------------------------------------------------------

class TestThresholds:
    """Catches: threshold logic bugs (inverted comparison, wrong threshold)."""

    def test_should_warn_at_threshold(self):
        """should_warn() must return True when utilization >= warning_threshold.
        Bug: uses > instead of >=, missing the exact threshold."""
        budget = ContextBudget(max_tokens=1000, reserve_tokens=0, warning_threshold=0.8)
        window = ContextWindow(budget)

        # Manually set usage to exactly 80%
        window._current_usage = 800
        assert window.should_warn(), (
            f"should_warn() should be True at exactly 80% utilization"
        )

    def test_should_not_warn_below_threshold(self):
        """should_warn() must return False below warning_threshold."""
        budget = ContextBudget(max_tokens=1000, reserve_tokens=0, warning_threshold=0.8)
        window = ContextWindow(budget)
        window._current_usage = 799

        assert not window.should_warn(), (
            f"should_warn() should be False at 79.9% utilization"
        )

    def test_should_compress_at_critical(self):
        """should_compress() must return True when utilization >= critical_threshold."""
        budget = ContextBudget(max_tokens=1000, reserve_tokens=0, critical_threshold=0.95)
        window = ContextWindow(budget)
        window._current_usage = 950

        assert window.should_compress(), (
            f"should_compress() should be True at exactly 95% utilization"
        )

    def test_should_not_compress_below_critical(self):
        """should_compress() must return False below critical_threshold."""
        budget = ContextBudget(max_tokens=1000, reserve_tokens=0, critical_threshold=0.95)
        window = ContextWindow(budget)
        window._current_usage = 949

        assert not window.should_compress(), (
            f"should_compress() should be False at 94.9% utilization"
        )

    def test_warn_and_compress_use_different_thresholds(self):
        """should_warn and should_compress must use DIFFERENT thresholds.
        Bug: both accidentally use the same threshold field."""
        budget = ContextBudget(
            max_tokens=1000, reserve_tokens=0,
            warning_threshold=0.5, critical_threshold=0.9
        )
        window = ContextWindow(budget)
        window._current_usage = 600  # 60% — above warn, below compress

        assert window.should_warn(), "60% should trigger warn (threshold=0.5)"
        assert not window.should_compress(), "60% should NOT trigger compress (threshold=0.9)"


# ---------------------------------------------------------------------------
# 7. ContextBudget.available_for_context
#    Bug: reserve_tokens not subtracted from max_tokens
# ---------------------------------------------------------------------------

class TestContextBudget:
    """Catches: available_for_context not accounting for reserve."""

    def test_available_subtracts_reserve(self):
        """available_for_context must equal max_tokens - reserve_tokens."""
        budget = ContextBudget(max_tokens=128000, reserve_tokens=8192)
        assert budget.available_for_context == 128000 - 8192, (
            f"available_for_context should be {128000 - 8192}, "
            f"got {budget.available_for_context}"
        )

    def test_reserve_tokens_respected_by_add(self):
        """add() must respect reserve_tokens — cannot use the full max_tokens.
        Bug: add() compares against max_tokens instead of available_for_context."""
        budget = ContextBudget(max_tokens=100, reserve_tokens=50)
        window = ContextWindow(budget)

        # Create a message that fits in 100 but not in 50
        msg = Message.user("a" * 175)  # ~50+ content tokens + overhead
        est = window.estimate_tokens([msg])

        if est > 50 and est <= 100:
            result = window.add([msg])
            assert result is False, (
                f"Message needing {est} tokens should NOT fit in available budget of 50 "
                f"(max=100, reserve=50). add() may be using max_tokens instead of available."
            )
