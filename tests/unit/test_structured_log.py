"""
Tests for StructuredLogger (src/util/structured_log.py).

Bug targets:
- JSONL format: invalid JSON lines, missing fields, wrong encoding
- Session summary: missing or malformed markdown, wrong statistics
- File rotation: rotation not triggered, old files not cleaned up
- Buffered writes: events lost when buffer not flushed
- Closed logger: writing to closed logger causes crash
- Concurrent writes: buffer corruption under rapid writes
"""

import json
import tempfile
import time
from pathlib import Path

import pytest

from src.util.structured_log import StructuredLogger, LogEventType


class TestJSONLFormat:
    """Verify JSONL output format correctness."""

    def test_each_line_is_valid_json(self):
        """Bug: malformed JSON lines make log file unparseable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            slog = StructuredLogger(session_dir=tmpdir, session_id="test-001")
            slog.log(LogEventType.TOOL_CALL, {"tool_name": "read_file", "duration_ms": 120})
            slog.log(LogEventType.LLM_CALL, {"input_tokens": 100, "output_tokens": 50})
            slog.close()

            log_file = Path(tmpdir) / "session_log.jsonl"
            assert log_file.exists(), "Log file not created"

            lines = log_file.read_text().strip().split("\n")
            for i, line in enumerate(lines):
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as e:
                    pytest.fail(
                        f"Line {i+1} is not valid JSON: {e}\n"
                        f"Content: {line[:200]}"
                    )

    def test_required_fields_present(self):
        """Bug: required fields (timestamp, event_type, session_id, data) missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            slog = StructuredLogger(session_dir=tmpdir, session_id="test-002")
            slog.log(LogEventType.TOOL_CALL, {"tool_name": "test"})
            slog.close()

            log_file = Path(tmpdir) / "session_log.jsonl"
            lines = log_file.read_text().strip().split("\n")

            # Skip the SESSION_START event (first line), check the TOOL_CALL
            for line in lines:
                obj = json.loads(line)
                assert "timestamp" in obj, f"Missing 'timestamp' in: {obj}"
                assert "event_type" in obj, f"Missing 'event_type' in: {obj}"
                assert "session_id" in obj, f"Missing 'session_id' in: {obj}"
                assert "data" in obj, f"Missing 'data' in: {obj}"

    def test_session_id_matches(self):
        """Bug: session_id not propagated to log entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            slog = StructuredLogger(session_dir=tmpdir, session_id="my-session-xyz")
            slog.log(LogEventType.TOOL_CALL, {"tool_name": "test"})
            slog.close()

            log_file = Path(tmpdir) / "session_log.jsonl"
            for line in log_file.read_text().strip().split("\n"):
                obj = json.loads(line)
                assert obj["session_id"] == "my-session-xyz", (
                    f"Wrong session_id: {obj['session_id']}"
                )

    def test_event_type_is_string_value(self):
        """Bug: event_type stored as enum repr instead of string value."""
        with tempfile.TemporaryDirectory() as tmpdir:
            slog = StructuredLogger(session_dir=tmpdir, session_id="test-003")
            slog.log(LogEventType.TOOL_CALL, {"tool_name": "test"})
            slog.close()

            log_file = Path(tmpdir) / "session_log.jsonl"
            lines = log_file.read_text().strip().split("\n")
            # Find the TOOL_CALL event
            tool_events = [
                json.loads(l) for l in lines
                if "tool_call" in l and "session_start" not in l
            ]
            assert len(tool_events) > 0
            assert tool_events[0]["event_type"] == "tool_call", (
                f"event_type is '{tool_events[0]['event_type']}', "
                f"expected 'tool_call' — enum not serialized as string"
            )

    def test_timestamp_is_iso_format(self):
        """Bug: timestamp in wrong format, breaking log analysis tools."""
        with tempfile.TemporaryDirectory() as tmpdir:
            slog = StructuredLogger(session_dir=tmpdir, session_id="test-004")
            slog.log(LogEventType.TOOL_CALL, {"tool_name": "test"})
            slog.close()

            log_file = Path(tmpdir) / "session_log.jsonl"
            for line in log_file.read_text().strip().split("\n"):
                obj = json.loads(line)
                ts = obj["timestamp"]
                assert "T" in ts, f"Timestamp not ISO format: {ts}"
                assert ts.endswith("Z"), f"Timestamp missing Z suffix: {ts}"

    def test_unicode_in_data_preserved(self):
        """Bug: Unicode characters in data corrupted by ensure_ascii=True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            slog = StructuredLogger(session_dir=tmpdir, session_id="test-005")
            slog.log(LogEventType.TOOL_CALL, {
                "tool_name": "test",
                "output": "中文输出 🔧 émojis"
            })
            slog.close()

            log_file = Path(tmpdir) / "session_log.jsonl"
            content = log_file.read_text(encoding="utf-8")
            assert "中文输出" in content, "Unicode characters corrupted"
            assert "🔧" in content


class TestBufferFlush:
    """Verify buffered write behavior."""

    def test_events_flushed_on_close(self):
        """Bug: events in buffer lost when close() is called without flush."""
        with tempfile.TemporaryDirectory() as tmpdir:
            slog = StructuredLogger(
                session_dir=tmpdir, session_id="test-buf",
                buffer_size=100  # Large buffer, won't auto-flush
            )
            slog.log(LogEventType.TOOL_CALL, {"tool_name": "test1"})
            slog.log(LogEventType.TOOL_CALL, {"tool_name": "test2"})
            slog.close()

            log_file = Path(tmpdir) / "session_log.jsonl"
            lines = log_file.read_text().strip().split("\n")
            # Should have SESSION_START + 2 TOOL_CALL events
            assert len(lines) >= 3, (
                f"Expected at least 3 events, got {len(lines)} — "
                f"buffer not flushed on close"
            )

    def test_auto_flush_at_buffer_size(self):
        """Bug: auto-flush not triggered at buffer_size threshold."""
        with tempfile.TemporaryDirectory() as tmpdir:
            slog = StructuredLogger(
                session_dir=tmpdir, session_id="test-autoflush",
                buffer_size=3  # Small buffer
            )
            # SESSION_START is event 1, then add 2 more to hit buffer_size=3
            slog.log(LogEventType.TOOL_CALL, {"tool_name": "test1"})
            slog.log(LogEventType.TOOL_CALL, {"tool_name": "test2"})

            # Buffer should have been flushed by now
            log_file = Path(tmpdir) / "session_log.jsonl"
            content = log_file.read_text()
            assert len(content.strip().split("\n")) >= 3, (
                "Auto-flush not triggered at buffer_size threshold"
            )
            slog.close()


class TestClosedLogger:
    """Verify behavior after close()."""

    def test_log_after_close_does_not_crash(self):
        """Bug: logging to closed StructuredLogger raises exception,
        crashing the agent during shutdown."""
        with tempfile.TemporaryDirectory() as tmpdir:
            slog = StructuredLogger(session_dir=tmpdir, session_id="test-closed")
            slog.close()

            # Should not raise
            slog.log(LogEventType.TOOL_CALL, {"tool_name": "test"})

    def test_double_close_does_not_crash(self):
        """Bug: calling close() twice raises exception."""
        with tempfile.TemporaryDirectory() as tmpdir:
            slog = StructuredLogger(session_dir=tmpdir, session_id="test-dblclose")
            slog.close()
            slog.close()  # Should not raise


class TestSessionSummary:
    """Verify session summary generation."""

    def test_summary_file_created(self):
        """Bug: summary file not created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            slog = StructuredLogger(session_dir=tmpdir, session_id="test-summary")
            slog.log(LogEventType.TOOL_CALL, {"tool_name": "read_file", "success": True, "duration_ms": 120})
            slog.log(LogEventType.LLM_CALL, {"input_tokens": 1000, "output_tokens": 500, "duration_s": 2.0})

            path = slog.generate_session_summary(
                total_input_tokens=1000,
                total_output_tokens=500,
                llm_calls=1,
                tool_calls=1,
            )
            slog.close()

            summary_file = Path(path)
            assert summary_file.exists(), "Session summary file not created"
            content = summary_file.read_text()
            assert "Session Summary" in content
            assert "1,000" in content or "1000" in content

    def test_summary_contains_activity_timeline(self):
        """Bug: activity timeline missing from summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            slog = StructuredLogger(session_dir=tmpdir, session_id="test-timeline")
            slog.log(LogEventType.TOOL_CALL, {"tool_name": "read_file", "success": True, "duration_ms": 100})

            path = slog.generate_session_summary(
                total_input_tokens=0, total_output_tokens=0,
                llm_calls=0, tool_calls=1,
            )
            slog.close()

            content = Path(path).read_text()
            assert "Activity Timeline" in content
            assert "read_file" in content

    def test_summary_with_zero_stats(self):
        """Bug: zero values cause formatting errors in summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            slog = StructuredLogger(session_dir=tmpdir, session_id="test-zero")
            path = slog.generate_session_summary(
                total_input_tokens=0, total_output_tokens=0,
                llm_calls=0, tool_calls=0,
            )
            slog.close()

            content = Path(path).read_text()
            assert "0" in content


class TestContextManager:
    """Verify context manager protocol."""

    def test_context_manager_flushes_on_exit(self):
        """Bug: context manager doesn't flush, events lost."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with StructuredLogger(session_dir=tmpdir, session_id="test-ctx") as slog:
                slog.log(LogEventType.TOOL_CALL, {"tool_name": "test"})

            log_file = Path(tmpdir) / "session_log.jsonl"
            lines = log_file.read_text().strip().split("\n")
            assert len(lines) >= 2, "Events lost after context manager exit"

    def test_context_manager_handles_exception(self):
        """Bug: exception in with block prevents flush/close."""
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                with StructuredLogger(session_dir=tmpdir, session_id="test-exc") as slog:
                    slog.log(LogEventType.TOOL_CALL, {"tool_name": "test"})
                    raise ValueError("test error")
            except ValueError:
                pass

            log_file = Path(tmpdir) / "session_log.jsonl"
            assert log_file.exists(), "Log file missing after exception"
            lines = log_file.read_text().strip().split("\n")
            assert len(lines) >= 2, "Events lost after exception in context manager"


class TestSessionStartEvent:
    """Verify automatic SESSION_START event."""

    def test_session_start_logged_on_init(self):
        """Bug: SESSION_START not logged, session timeline has no start marker."""
        with tempfile.TemporaryDirectory() as tmpdir:
            slog = StructuredLogger(session_dir=tmpdir, session_id="test-start")
            slog.close()

            log_file = Path(tmpdir) / "session_log.jsonl"
            lines = log_file.read_text().strip().split("\n")
            first_event = json.loads(lines[0])
            assert first_event["event_type"] == "session_start", (
                f"First event is '{first_event['event_type']}', "
                f"expected 'session_start'"
            )
