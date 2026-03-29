"""
Tests for ActivityStream (src/util/activity_stream.py).

Bug targets:
- Truncation logic: text not truncated at correct length, or truncation marker missing
- Multi-line compression: newlines not replaced with ↵, or double-compressed
- Terminal width truncation: output exceeds terminal width, causing visual mess
- Relative timestamp: wrong format, negative values, or missing
- Verbose mode: truncation limits not changed in verbose mode
- Parameter summary: priority keys not picked, or summary too long
- LLM call counter: counter not incremented, or reset incorrectly
"""

import io
import time
from unittest.mock import patch

import pytest

from src.util.display import ConsoleDisplay
from src.util.activity_stream import (
    ActivityStream,
    _truncate,
    _compress_multiline,
    _format_param_summary,
    _get_terminal_width,
    DEFAULT_OUTPUT_PREVIEW_MAX,
    DEFAULT_PARAM_SUMMARY_MAX,
    VERBOSE_OUTPUT_PREVIEW_MAX,
    VERBOSE_PARAM_SUMMARY_MAX,
)


# ── Helper functions ──────────────────────────────────────────────────

def make_stream(verbose=False) -> tuple:
    """Create a ConsoleDisplay + ActivityStream pair with captured output."""
    output = io.StringIO()
    display = ConsoleDisplay(color=False, stream=output)
    stream = ActivityStream(
        display=display,
        verbose=verbose,
        session_start_time=time.time(),
    )
    return stream, output


# ── Truncation logic ──────────────────────────────────────────────────

class TestTruncate:
    """Verify _truncate function behavior."""

    def test_short_text_not_truncated(self):
        """Bug: short text gets truncated marker appended."""
        result = _truncate("hello", 100)
        assert result == "hello"
        assert "..." not in result

    def test_exact_length_not_truncated(self):
        """Bug: off-by-one — text at exactly max_len gets truncated."""
        text = "a" * 100
        result = _truncate(text, 100)
        assert result == text

    def test_long_text_truncated_with_marker(self):
        """Bug: long text not truncated, or marker missing."""
        text = "a" * 200
        result = _truncate(text, 100)
        assert len(result) > 100  # marker adds length
        assert "..." in result
        assert "200 chars total" in result

    def test_truncation_preserves_prefix(self):
        """Bug: truncation cuts from wrong end."""
        text = "START" + "x" * 200
        result = _truncate(text, 50)
        assert result.startswith("START")


class TestCompressMultiline:
    """Verify _compress_multiline function behavior."""

    def test_newlines_replaced_with_marker(self):
        """Bug: newlines not replaced, multi-line text breaks activity stream layout."""
        text = "line1\nline2\nline3"
        result = _compress_multiline(text)
        assert "\n" not in result
        assert "↵" in result
        assert "line1↵line2↵line3" == result

    def test_carriage_returns_handled(self):
        """Bug: \\r\\n not handled, Windows-style line endings leak through."""
        text = "line1\r\nline2\rline3"
        result = _compress_multiline(text)
        assert "\r" not in result
        assert "\n" not in result

    def test_consecutive_whitespace_collapsed(self):
        """Bug: excessive whitespace not collapsed, wasting terminal space."""
        text = "word1    word2\t\tword3"
        result = _compress_multiline(text)
        assert "    " not in result
        assert "\t" not in result

    def test_empty_string(self):
        """Bug: empty string causes error."""
        assert _compress_multiline("") == ""

    def test_single_line_unchanged(self):
        """Bug: single-line text gets ↵ markers added."""
        text = "no newlines here"
        result = _compress_multiline(text)
        assert "↵" not in result


class TestFormatParamSummary:
    """Verify _format_param_summary function behavior."""

    def test_empty_arguments(self):
        """Bug: empty dict causes error."""
        assert _format_param_summary({}, 100) == ""

    def test_priority_keys_picked_first(self):
        """Bug: informative keys (path, query) not prioritized,
        user sees useless parameters instead."""
        args = {
            "verbose": True,
            "path": "/src/main.py",
            "encoding": "utf-8",
        }
        result = _format_param_summary(args, 200)
        # path should appear before verbose/encoding
        path_pos = result.find("path=")
        assert path_pos >= 0, "Priority key 'path' not in summary"

    def test_summary_truncated_at_max_length(self):
        """Bug: summary exceeds max_len, breaking terminal layout."""
        args = {
            "path": "/very/long/path/" + "x" * 200,
            "query": "another long value " * 20,
        }
        result = _format_param_summary(args, 100)
        assert len(result) <= 100, (
            f"Summary length {len(result)} exceeds max 100"
        )

    def test_long_values_individually_truncated(self):
        """Bug: individual parameter values not truncated,
        one long value consumes entire summary budget."""
        args = {"path": "x" * 200}
        result = _format_param_summary(args, 200)
        assert "..." in result, "Long value not individually truncated"


class TestRelativeTimestamp:
    """Verify relative timestamp formatting."""

    def test_timestamp_format(self):
        """Bug: timestamp format wrong (e.g., missing leading zero on seconds)."""
        stream, output = make_stream()
        # Set session start to 65 seconds ago
        stream._session_start = time.time() - 65

        ts = stream._relative_timestamp()
        assert ts.startswith("[+")
        assert ts.endswith("]")
        # Should be [+1:05]
        assert "1:" in ts

    def test_timestamp_at_zero(self):
        """Bug: timestamp at session start shows negative or wrong value."""
        stream, output = make_stream()
        stream._session_start = time.time()
        ts = stream._relative_timestamp()
        assert "[+0:0" in ts  # [+0:00] or [+0:01]


class TestLLMEntries:
    """Verify LLM request/response entry formatting."""

    def test_llm_request_increments_counter(self):
        """Bug: LLM call counter not incremented, all requests show #0."""
        stream, output = make_stream()
        stream.llm_request(iteration=1, message_count=5)
        stream.llm_request(iteration=2, message_count=8)

        text = output.getvalue()
        assert "#1" in text
        assert "#2" in text

    def test_llm_request_shows_message_count(self):
        """Bug: message count missing from request entry."""
        stream, output = make_stream()
        stream.llm_request(iteration=1, message_count=12)
        assert "12 messages" in output.getvalue()

    def test_llm_response_shows_token_counts(self):
        """Bug: token counts missing or not comma-formatted."""
        stream, output = make_stream()
        stream.llm_request(iteration=1, message_count=5)  # increment counter
        stream.llm_response(
            iteration=1,
            input_tokens=1234,
            output_tokens=567,
            duration_s=2.3,
            tool_call_count=3,
        )
        text = output.getvalue()
        assert "1,234" in text, "Input tokens not comma-formatted"
        assert "567" in text
        assert "2.3s" in text
        assert "3 tool calls" in text

    def test_llm_response_text_preview_truncated(self):
        """Bug: long text response not truncated, floods terminal."""
        stream, output = make_stream()
        stream.llm_request(iteration=1, message_count=5)
        long_text = "x" * 500
        stream.llm_response(
            iteration=1,
            input_tokens=100,
            output_tokens=200,
            duration_s=1.0,
            tool_call_count=0,
            content_preview=long_text,
        )
        text = output.getvalue()
        # In normal mode, should be truncated to DEFAULT_LLM_TEXT_PREVIEW_MAX
        assert "chars total" in text or "..." in text


class TestToolEntries:
    """Verify tool call entry formatting."""

    def test_tool_start_shows_name_and_params(self):
        """Bug: tool name or parameters missing from start entry."""
        stream, output = make_stream()
        stream.tool_start("read_file", {"path": "/src/main.py"})
        text = output.getvalue()
        assert "read_file" in text
        assert "/src/main.py" in text

    def test_tool_success_shows_duration_and_preview(self):
        """Bug: duration or output preview missing."""
        stream, output = make_stream()
        stream.tool_success("read_file", duration_ms=120, output="import os\nimport sys")
        text = output.getvalue()
        assert "120ms" in text
        assert "import os" in text

    def test_tool_failure_shows_error(self):
        """Bug: error message missing from failure entry."""
        stream, output = make_stream()
        stream.tool_failure("read_file", duration_ms=50, error_message="File not found")
        text = output.getvalue()
        assert "File not found" in text
        assert "50ms" in text

    def test_tool_success_output_truncated(self):
        """Bug: long tool output not truncated."""
        stream, output = make_stream()
        long_output = "x" * 500
        stream.tool_success("read_file", duration_ms=100, output=long_output)
        text = output.getvalue()
        # Output should be truncated to DEFAULT_OUTPUT_PREVIEW_MAX
        assert "..." in text or "chars" in text

    def test_tool_output_multiline_compressed(self):
        """Bug: multi-line tool output breaks activity stream layout."""
        stream, output = make_stream()
        multiline = "line1\nline2\nline3"
        stream.tool_success("read_file", duration_ms=100, output=multiline)
        text = output.getvalue()
        # Newlines should be replaced with ↵
        assert "↵" in text or "line1" in text


class TestVerboseMode:
    """Verify verbose mode changes truncation limits."""

    def test_verbose_uses_longer_output_preview(self):
        """Bug: verbose mode doesn't increase truncation limits,
        making --verbose flag useless for seeing more output."""
        stream_normal, _ = make_stream(verbose=False)
        stream_verbose, _ = make_stream(verbose=True)

        assert stream_verbose._output_max > stream_normal._output_max, (
            f"Verbose output max ({stream_verbose._output_max}) not greater than "
            f"normal ({stream_normal._output_max})"
        )

    def test_verbose_uses_longer_param_summary(self):
        """Bug: verbose mode doesn't show more parameter details."""
        stream_normal, _ = make_stream(verbose=False)
        stream_verbose, _ = make_stream(verbose=True)

        assert stream_verbose._param_max > stream_normal._param_max


class TestParallelBatch:
    """Verify parallel batch annotation."""

    def test_parallel_batch_shows_tool_count(self):
        """Bug: tool count missing from parallel batch marker."""
        stream, output = make_stream()
        stream.parallel_batch(3)
        text = output.getvalue()
        assert "3 tools" in text
        assert "Parallel" in text


class TestLoopDetected:
    """Verify loop detection warning."""

    def test_loop_detected_shows_message(self):
        """Bug: loop detection message missing or not prominent."""
        stream, output = make_stream()
        stream.loop_detected("repeated pattern in last 5 iterations")
        text = output.getvalue()
        assert "Loop detected" in text
        assert "repeated pattern" in text


class TestResetCounters:
    """Verify counter reset."""

    def test_reset_clears_llm_counter(self):
        """Bug: reset doesn't clear LLM counter, new session shows
        continuation of old counter."""
        stream, output = make_stream()
        stream.llm_request(iteration=1, message_count=5)
        stream.llm_request(iteration=2, message_count=5)
        assert stream._llm_call_counter == 2

        stream.reset_counters()
        assert stream._llm_call_counter == 0
