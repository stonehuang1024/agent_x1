"""
Tests for ConsoleDisplay (src/util/display.py).

Bug targets:
- Color output when terminal doesn't support it (garbled output)
- NO_COLOR env var not respected
- Icon prefixes missing or wrong for each message type
- Verbose/debug mode gating broken (messages leak in non-verbose mode)
- BrokenPipeError crashes the application instead of being silently handled
- session_summary formatting errors with edge values (zero, very large)
"""

import io
import os
from unittest.mock import patch, MagicMock

import pytest

from src.util.display import ConsoleDisplay, Colors, Icons


class TestColorDetection:
    """Verify color auto-detection logic."""

    def test_no_color_when_not_tty(self):
        """Bug: colors enabled on non-TTY stream (e.g., piped output),
        producing garbled ANSI escape codes in log files."""
        stream = io.StringIO()  # StringIO has no isatty
        display = ConsoleDisplay(stream=stream)
        assert not display.color_enabled, (
            "Colors enabled on non-TTY stream — "
            "piped output will contain raw ANSI escape codes"
        )

    def test_no_color_env_var_respected(self):
        """Bug: NO_COLOR env var (https://no-color.org/) ignored,
        violating the standard."""
        stream = MagicMock()
        stream.isatty = MagicMock(return_value=True)

        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            display = ConsoleDisplay(stream=stream)
            assert not display.color_enabled, (
                "NO_COLOR env var set but colors still enabled — "
                "violates no-color.org standard"
            )

    def test_dumb_terminal_no_color(self):
        """Bug: TERM=dumb not handled, ANSI codes sent to dumb terminal."""
        stream = MagicMock()
        stream.isatty = MagicMock(return_value=True)

        with patch.dict(os.environ, {"TERM": "dumb"}, clear=False):
            # Remove NO_COLOR if present
            env = os.environ.copy()
            env.pop("NO_COLOR", None)
            env["TERM"] = "dumb"
            with patch.dict(os.environ, env, clear=True):
                display = ConsoleDisplay(stream=stream)
                assert not display.color_enabled, (
                    "Colors enabled on TERM=dumb — terminal cannot render ANSI"
                )

    def test_explicit_color_override(self):
        """Bug: explicit color=True/False parameter ignored."""
        stream = io.StringIO()
        display_on = ConsoleDisplay(color=True, stream=stream)
        display_off = ConsoleDisplay(color=False, stream=stream)

        assert display_on.color_enabled is True
        assert display_off.color_enabled is False


class TestIconPrefixes:
    """Verify each message type uses the correct icon prefix."""

    def setup_method(self):
        self.stream = io.StringIO()
        self.display = ConsoleDisplay(color=False, stream=self.stream)

    def _get_output(self) -> str:
        return self.stream.getvalue()

    def test_status_uses_gear_icon(self):
        """Bug: wrong icon for status messages, confusing user."""
        self.display.status("processing")
        assert Icons.STATUS in self._get_output()

    def test_success_uses_check_icon(self):
        self.display.success("done")
        assert Icons.SUCCESS in self._get_output()

    def test_error_uses_cross_icon(self):
        self.display.error("failed")
        assert Icons.ERROR in self._get_output()

    def test_warning_uses_warning_icon(self):
        self.display.warning("caution")
        assert Icons.WARNING in self._get_output()

    def test_info_uses_info_icon(self):
        self.display.info("note")
        assert Icons.INFO in self._get_output()

    def test_tool_start_uses_wrench_icon(self):
        self.display.tool_start("read_file")
        assert Icons.TOOL in self._get_output()

    def test_tool_end_success_uses_check_icon(self):
        self.display.tool_end("read_file", success=True)
        assert Icons.SUCCESS in self._get_output()

    def test_tool_end_failure_uses_cross_icon(self):
        self.display.tool_end("read_file", success=False)
        assert Icons.ERROR in self._get_output()


class TestMessageContent:
    """Verify message content is correctly formatted."""

    def setup_method(self):
        self.stream = io.StringIO()
        self.display = ConsoleDisplay(color=False, stream=self.stream)

    def test_status_message_appears_in_output(self):
        """Bug: message text silently dropped."""
        self.display.status("Step 3 of 10")
        assert "Step 3 of 10" in self.stream.getvalue()

    def test_error_message_appears_in_output(self):
        self.display.error("Connection refused")
        assert "Connection refused" in self.stream.getvalue()

    def test_tool_start_shows_tool_name_and_summary(self):
        """Bug: tool name or summary missing from output."""
        self.display.tool_start("read_file", summary='path="/src/main.py"')
        output = self.stream.getvalue()
        assert "read_file" in output
        assert "/src/main.py" in output

    def test_tool_end_shows_duration(self):
        """Bug: duration not displayed, user can't identify slow tools."""
        self.display.tool_end("read_file", success=True, duration_ms=1234.5)
        output = self.stream.getvalue()
        assert "1235ms" in output or "1234ms" in output

    def test_llm_stats_shows_token_counts(self):
        """Bug: token counts not formatted or missing."""
        self.display.llm_stats(
            input_tokens=1234, output_tokens=567,
            duration_s=2.3, tool_calls=3
        )
        output = self.stream.getvalue()
        assert "1,234" in output, "Input tokens not comma-formatted"
        assert "567" in output
        assert "2.3s" in output
        assert "3 tool calls" in output


class TestSessionSummary:
    """Verify session summary formatting with edge cases."""

    def setup_method(self):
        self.stream = io.StringIO()
        self.display = ConsoleDisplay(color=False, stream=self.stream)

    def test_session_summary_with_zero_values(self):
        """Bug: division by zero or formatting error with zero tokens/calls."""
        self.display.session_summary(
            total_duration_s=0,
            total_input_tokens=0,
            total_output_tokens=0,
            llm_calls=0,
            tool_calls=0,
        )
        output = self.stream.getvalue()
        assert "0" in output
        assert "Session Summary" in output

    def test_session_summary_with_large_values(self):
        """Bug: large numbers not formatted with commas, hard to read."""
        self.display.session_summary(
            total_duration_s=3661,  # 1h 1m 1s
            total_input_tokens=1_234_567,
            total_output_tokens=890_123,
            llm_calls=42,
            tool_calls=156,
        )
        output = self.stream.getvalue()
        assert "1,234,567" in output or "1234567" in output
        assert "42" in output
        assert "156" in output

    def test_session_summary_duration_formatting(self):
        """Bug: duration shows raw seconds instead of human-readable format."""
        self.display.session_summary(
            total_duration_s=125,  # 2m 5s
            total_input_tokens=100,
            total_output_tokens=50,
            llm_calls=1,
            tool_calls=0,
        )
        output = self.stream.getvalue()
        assert "2m" in output, "Duration not formatted as minutes"


class TestVerboseDebugModes:
    """Verify verbose/debug mode gating."""

    def test_verbose_info_hidden_in_normal_mode(self):
        """Bug: verbose messages leak in normal mode, cluttering output."""
        stream = io.StringIO()
        display = ConsoleDisplay(verbose=False, stream=stream, color=False)
        display.verbose_info("detailed info")
        assert stream.getvalue() == "", (
            "verbose_info produced output in non-verbose mode — "
            "user will see unwanted debug information"
        )

    def test_verbose_info_shown_in_verbose_mode(self):
        """Bug: verbose messages suppressed even in verbose mode."""
        stream = io.StringIO()
        display = ConsoleDisplay(verbose=True, stream=stream, color=False)
        display.verbose_info("detailed info")
        assert "detailed info" in stream.getvalue()

    def test_debug_info_hidden_in_verbose_mode(self):
        """Bug: debug messages shown in verbose (non-debug) mode."""
        stream = io.StringIO()
        display = ConsoleDisplay(verbose=True, debug=False, stream=stream, color=False)
        display.debug_info("debug detail")
        assert stream.getvalue() == "", (
            "debug_info produced output in verbose-only mode"
        )

    def test_debug_info_shown_in_debug_mode(self):
        stream = io.StringIO()
        display = ConsoleDisplay(debug=True, stream=stream, color=False)
        display.debug_info("debug detail")
        assert "debug detail" in stream.getvalue()

    def test_debug_implies_verbose(self):
        """Bug: debug=True doesn't set verbose=True, so verbose_info
        messages are hidden even in debug mode."""
        display = ConsoleDisplay(debug=True)
        assert display.verbose is True, (
            "debug=True should imply verbose=True"
        )


class TestBrokenPipeHandling:
    """Verify BrokenPipeError doesn't crash the application."""

    def test_broken_pipe_silently_handled(self):
        """Bug: BrokenPipeError propagates up and crashes the agent
        when output is piped to a process that exits early (e.g., head)."""
        stream = MagicMock()
        stream.write = MagicMock(side_effect=BrokenPipeError)
        stream.isatty = MagicMock(return_value=False)

        display = ConsoleDisplay(stream=stream, color=False)
        # Should not raise
        display.status("test message")
        display.error("test error")
        display.success("test success")

    def test_os_error_silently_handled(self):
        """Bug: OSError on write crashes the agent."""
        stream = MagicMock()
        stream.write = MagicMock(side_effect=OSError("I/O error"))
        stream.isatty = MagicMock(return_value=False)

        display = ConsoleDisplay(stream=stream, color=False)
        display.status("test message")  # Should not raise


class TestColorOutput:
    """Verify ANSI color codes are applied correctly."""

    def test_error_uses_red_color(self):
        """Bug: error messages not visually distinct from normal messages."""
        stream = io.StringIO()
        display = ConsoleDisplay(color=True, stream=stream)
        display.error("critical failure")
        output = stream.getvalue()
        assert Colors.RED in output, (
            "Error message does not contain red ANSI code — "
            "errors will not be visually distinct"
        )

    def test_no_ansi_codes_when_color_disabled(self):
        """Bug: ANSI codes present even when color is disabled,
        producing garbled output in non-terminal contexts."""
        stream = io.StringIO()
        display = ConsoleDisplay(color=False, stream=stream)
        display.error("test error")
        display.success("test success")
        display.warning("test warning")
        output = stream.getvalue()
        assert "\033[" not in output, (
            f"ANSI escape codes found in color-disabled output: {repr(output[:100])}"
        )
