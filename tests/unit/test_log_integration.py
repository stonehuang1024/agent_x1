"""
Integration tests for LogIntegration (src/util/log_integration.py).

Bug targets:
- Event subscription: handlers not registered, events silently dropped
- Event dispatch: wrong handler called for event type
- Data extraction: missing or wrong fields from event payload
- TokenTracker integration: LLM/tool calls not recorded in tracker
- StructuredLogger integration: events not persisted to JSONL
- ActivityStream integration: display output missing for events
- Session finalization: summary not generated on session end
- Error resilience: one handler failure doesn't break other handlers
"""

import io
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.core.events import EventBus, AgentEvent
from src.util.display import ConsoleDisplay
from src.util.activity_stream import ActivityStream
from src.util.structured_log import StructuredLogger
from src.util.token_tracker import TokenTracker
from src.util.log_integration import LogIntegration


def make_full_stack(tmpdir: str = None):
    """Create a complete logging stack for testing."""
    output = io.StringIO()
    display = ConsoleDisplay(color=False, stream=output)
    stream = ActivityStream(display=display, verbose=False)
    tracker = TokenTracker()

    slog = None
    if tmpdir:
        slog = StructuredLogger(session_dir=tmpdir, session_id="test-integration")

    event_bus = EventBus()
    integration = LogIntegration(
        display=display,
        activity_stream=stream,
        structured_logger=slog,
        token_tracker=tracker,
    )
    integration.setup(event_bus)

    return event_bus, display, stream, tracker, slog, output


class TestEventSubscription:
    """Verify all expected events are subscribed."""

    def test_llm_events_subscribed(self):
        """Bug: LLM events not subscribed, LLM activity invisible to user."""
        event_bus = EventBus()
        display = ConsoleDisplay(color=False, stream=io.StringIO())
        stream = ActivityStream(display=display)
        integration = LogIntegration(display=display, activity_stream=stream)
        integration.setup(event_bus)

        # Verify handlers registered
        assert AgentEvent.LLM_CALL_STARTED in event_bus._handlers
        assert len(event_bus._handlers[AgentEvent.LLM_CALL_STARTED]) > 0
        assert AgentEvent.LLM_CALL_COMPLETED in event_bus._handlers
        assert len(event_bus._handlers[AgentEvent.LLM_CALL_COMPLETED]) > 0

    def test_tool_events_subscribed(self):
        """Bug: tool events not subscribed, tool activity invisible."""
        event_bus = EventBus()
        display = ConsoleDisplay(color=False, stream=io.StringIO())
        stream = ActivityStream(display=display)
        integration = LogIntegration(display=display, activity_stream=stream)
        integration.setup(event_bus)

        assert AgentEvent.TOOL_CALLED in event_bus._handlers
        assert AgentEvent.TOOL_SUCCEEDED in event_bus._handlers
        assert AgentEvent.TOOL_FAILED in event_bus._handlers

    def test_session_events_subscribed(self):
        """Bug: session events not subscribed, no summary on session end."""
        event_bus = EventBus()
        display = ConsoleDisplay(color=False, stream=io.StringIO())
        stream = ActivityStream(display=display)
        integration = LogIntegration(display=display, activity_stream=stream)
        integration.setup(event_bus)

        assert AgentEvent.SESSION_COMPLETED in event_bus._handlers
        assert AgentEvent.SESSION_FAILED in event_bus._handlers


class TestLLMEventDispatch:
    """Verify LLM events are correctly dispatched to all subsystems."""

    def test_llm_completed_updates_tracker(self):
        """Bug: LLM completion not recorded in TokenTracker,
        session statistics will be wrong."""
        event_bus, display, stream, tracker, slog, output = make_full_stack()

        event_bus.emit(
            AgentEvent.LLM_CALL_COMPLETED,
            iteration=1,
            input_tokens=1000,
            output_tokens=500,
            duration_s=2.0,
            tool_call_count=3,
            content_preview="Hello world",
        )

        assert tracker.total_input_tokens == 1000, (
            f"Tracker input tokens: {tracker.total_input_tokens}, expected 1000 — "
            f"LLM_CALL_COMPLETED not dispatched to TokenTracker"
        )
        assert tracker.total_output_tokens == 500
        assert tracker.llm_call_count == 1

    def test_llm_completed_writes_to_activity_stream(self):
        """Bug: LLM completion not shown in activity stream."""
        event_bus, display, stream, tracker, slog, output = make_full_stack()

        event_bus.emit(
            AgentEvent.LLM_CALL_STARTED,
            iteration=1,
            message_count=10,
        )
        event_bus.emit(
            AgentEvent.LLM_CALL_COMPLETED,
            iteration=1,
            input_tokens=1000,
            output_tokens=500,
            duration_s=2.0,
            tool_call_count=0,
        )

        text = output.getvalue()
        assert "LLM" in text, "LLM activity not shown in output"

    def test_llm_completed_writes_to_structured_log(self):
        """Bug: LLM completion not persisted to JSONL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            event_bus, display, stream, tracker, slog, output = make_full_stack(tmpdir)

            event_bus.emit(
                AgentEvent.LLM_CALL_COMPLETED,
                iteration=1,
                input_tokens=1000,
                output_tokens=500,
                duration_s=2.0,
                tool_call_count=0,
            )
            slog.close()

            log_file = Path(tmpdir) / "session_log.jsonl"
            content = log_file.read_text()
            assert "llm_call" in content, (
                "LLM call event not found in structured log"
            )

    def test_llm_failed_shows_error(self):
        """Bug: LLM failure not displayed to user."""
        event_bus, display, stream, tracker, slog, output = make_full_stack()

        event_bus.emit(
            AgentEvent.LLM_CALL_FAILED,
            iteration=1,
            error="Rate limit exceeded",
        )

        text = output.getvalue()
        assert "Rate limit" in text or "failed" in text.lower(), (
            "LLM failure not shown to user"
        )


class TestToolEventDispatch:
    """Verify tool events are correctly dispatched."""

    def test_tool_succeeded_updates_tracker(self):
        """Bug: tool success not recorded in tracker."""
        event_bus, display, stream, tracker, slog, output = make_full_stack()

        event_bus.emit(
            AgentEvent.TOOL_SUCCEEDED,
            tool_name="read_file",
            duration_ms=120,
            result_preview="import os",
            result_length=500,
        )

        assert tracker.tool_call_count == 1
        stats = tracker.get_stats()
        assert stats["tool_success_count"] == 1

    def test_tool_failed_updates_tracker(self):
        """Bug: tool failure not recorded in tracker."""
        event_bus, display, stream, tracker, slog, output = make_full_stack()

        event_bus.emit(
            AgentEvent.TOOL_FAILED,
            tool_name="read_file",
            duration_ms=50,
            error="File not found",
        )

        assert tracker.tool_call_count == 1
        stats = tracker.get_stats()
        assert stats["tool_failure_count"] == 1

    def test_tool_called_shows_in_activity_stream(self):
        """Bug: tool start not shown in activity stream."""
        event_bus, display, stream, tracker, slog, output = make_full_stack()

        event_bus.emit(
            AgentEvent.TOOL_CALLED,
            tool_name="read_file",
            arguments={"path": "/src/main.py"},
        )

        text = output.getvalue()
        assert "read_file" in text, "Tool call not shown in activity stream"

    def test_tool_succeeded_writes_to_structured_log(self):
        """Bug: tool success not persisted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            event_bus, display, stream, tracker, slog, output = make_full_stack(tmpdir)

            event_bus.emit(
                AgentEvent.TOOL_SUCCEEDED,
                tool_name="read_file",
                duration_ms=120,
                result_preview="content",
                result_length=100,
            )
            slog.close()

            log_file = Path(tmpdir) / "session_log.jsonl"
            content = log_file.read_text()
            assert "tool_call" in content
            assert "read_file" in content


class TestSessionFinalization:
    """Verify session end triggers summary generation."""

    def test_session_completed_generates_summary(self):
        """Bug: session completion doesn't generate summary file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            event_bus, display, stream, tracker, slog, output = make_full_stack(tmpdir)

            # Record some activity
            tracker.record_llm_call(input_tokens=100, output_tokens=50, duration_s=1.0)
            tracker.record_tool_call("test", duration_ms=100, success=True)

            event_bus.emit(AgentEvent.SESSION_COMPLETED)

            summary_file = Path(tmpdir) / "session_summary.md"
            assert summary_file.exists(), (
                "Session summary not generated on SESSION_COMPLETED"
            )

    def test_session_completed_shows_summary_in_display(self):
        """Bug: session summary not shown to user."""
        event_bus, display, stream, tracker, slog, output = make_full_stack()

        tracker.record_llm_call(input_tokens=100, output_tokens=50, duration_s=1.0)
        event_bus.emit(AgentEvent.SESSION_COMPLETED)

        text = output.getvalue()
        assert "Session Summary" in text, (
            "Session summary not displayed to user on completion"
        )

    def test_session_failed_shows_error(self):
        """Bug: session failure not displayed to user."""
        event_bus, display, stream, tracker, slog, output = make_full_stack()

        event_bus.emit(
            AgentEvent.SESSION_FAILED,
            error="Out of memory",
        )

        text = output.getvalue()
        assert "Out of memory" in text or "failed" in text.lower()


class TestLoopDetection:
    """Verify loop detection events are displayed."""

    def test_loop_detected_shows_warning(self):
        """Bug: loop detection warning not shown to user."""
        event_bus, display, stream, tracker, slog, output = make_full_stack()

        event_bus.emit(
            AgentEvent.LOOP_DETECTED,
            message="repeated pattern in last 5 iterations",
        )

        text = output.getvalue()
        assert "Loop detected" in text
        assert "repeated pattern" in text


class TestMissingOptionalComponents:
    """Verify graceful behavior when optional components are None."""

    def test_no_tracker_does_not_crash(self):
        """Bug: None tracker causes AttributeError on event dispatch."""
        output = io.StringIO()
        display = ConsoleDisplay(color=False, stream=output)
        stream = ActivityStream(display=display)
        event_bus = EventBus()

        integration = LogIntegration(
            display=display,
            activity_stream=stream,
            structured_logger=None,
            token_tracker=None,
        )
        integration.setup(event_bus)

        # Should not raise
        event_bus.emit(
            AgentEvent.LLM_CALL_COMPLETED,
            iteration=1, input_tokens=100, output_tokens=50,
            duration_s=1.0, tool_call_count=0,
        )
        event_bus.emit(
            AgentEvent.TOOL_SUCCEEDED,
            tool_name="test", duration_ms=100,
            result_preview="ok", result_length=2,
        )

    def test_no_structured_logger_does_not_crash(self):
        """Bug: None structured_logger causes AttributeError."""
        output = io.StringIO()
        display = ConsoleDisplay(color=False, stream=output)
        stream = ActivityStream(display=display)
        tracker = TokenTracker()
        event_bus = EventBus()

        integration = LogIntegration(
            display=display,
            activity_stream=stream,
            structured_logger=None,
            token_tracker=tracker,
        )
        integration.setup(event_bus)

        # Should not raise
        event_bus.emit(
            AgentEvent.LLM_CALL_COMPLETED,
            iteration=1, input_tokens=100, output_tokens=50,
            duration_s=1.0, tool_call_count=0,
        )
        event_bus.emit(AgentEvent.SESSION_COMPLETED)


class TestMissingEventData:
    """Verify graceful handling of events with missing data fields."""

    def test_llm_completed_with_missing_fields(self):
        """Bug: missing fields in event data cause KeyError."""
        event_bus, display, stream, tracker, slog, output = make_full_stack()

        # Emit with minimal data — should not crash
        event_bus.emit(AgentEvent.LLM_CALL_COMPLETED)
        event_bus.emit(AgentEvent.LLM_CALL_STARTED)
        event_bus.emit(AgentEvent.TOOL_CALLED)
        event_bus.emit(AgentEvent.TOOL_SUCCEEDED)
        event_bus.emit(AgentEvent.TOOL_FAILED)

    def test_tool_succeeded_with_empty_data(self):
        """Bug: empty data dict causes crash."""
        event_bus, display, stream, tracker, slog, output = make_full_stack()
        event_bus.emit(AgentEvent.TOOL_SUCCEEDED)
        # Should not crash, tracker should still count
        assert tracker.tool_call_count == 1
