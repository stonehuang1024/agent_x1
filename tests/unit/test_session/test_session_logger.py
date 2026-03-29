"""Unit tests for SessionLogger.

Bug-class targets:
- LLM interaction: data not written to markdown or transcript
- Summary: wrong stats, file not created
- Resource management: crash after close
"""

import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from src.session.session_logger import SessionLogger
from src.session.transcript import TranscriptWriter, TranscriptReader
from src.session.models import Session, SessionStatus


def _make_session(**kwargs):
    defaults = dict(name="test-session", status=SessionStatus.COMPLETED, turn_count=5)
    defaults.update(kwargs)
    return Session(**defaults)


# ======================================================================
# 16.1 — LLM Interaction Logging
# ======================================================================

class TestSessionLoggerLLM:
    """Catches: data not written to markdown or transcript."""

    def test_log_llm_writes_markdown(self, tmp_path):
        logger = SessionLogger(session_dir=tmp_path, session_id="test-id")
        logger.log_llm_interaction(
            iteration=1,
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            response={"content": "hello"},
            usage={"input_tokens": 10, "output_tokens": 5},
            duration_ms=100.0,
            stop_reason="end_turn",
        )
        logger.close()

        md_content = (tmp_path / "session_llm.md").read_text()
        assert "LLM Call 1" in md_content
        assert "Input Tokens: 10" in md_content
        assert "end_turn" in md_content

    def test_log_llm_writes_transcript(self, tmp_path):
        transcript_path = tmp_path / "transcript.jsonl"
        writer = TranscriptWriter(transcript_path)
        logger = SessionLogger(
            session_dir=tmp_path, session_id="test-id",
            transcript_writer=writer,
        )
        logger.log_llm_interaction(
            iteration=1,
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            response={},
            usage={"input_tokens": 10, "output_tokens": 5},
            duration_ms=100.0,
            stop_reason="end_turn",
        )
        logger.close()
        writer.close()

        reader = TranscriptReader(transcript_path)
        entries = reader.read_all()
        assert len(entries) >= 1
        assert entries[0]["type"] == "llm_interaction"
        assert entries[0]["iteration"] == 1

    def test_multiple_calls_tracked(self, tmp_path):
        logger = SessionLogger(session_dir=tmp_path, session_id="test-id")
        for i in range(3):
            logger.log_llm_interaction(
                iteration=i + 1,
                messages=[], tools=[], response={},
                usage={"input_tokens": 10, "output_tokens": 5},
                duration_ms=50.0, stop_reason="end_turn",
            )
        assert len(logger._llm_calls) == 3
        logger.close()


# ======================================================================
# 16.2 — Session Summary
# ======================================================================

class TestSessionLoggerSummary:
    """Catches: wrong stats, file not created."""

    def test_generate_summary_content(self, tmp_path):
        # Create memory_data dir where generate_summary expects it
        memory_dir = tmp_path.parent / "memory_data"
        memory_dir.mkdir(exist_ok=True)

        session_dir = tmp_path
        logger = SessionLogger(session_dir=session_dir, session_id="test-id")
        logger.log_llm_interaction(
            iteration=1, messages=[], tools=[], response={},
            usage={"input_tokens": 100, "output_tokens": 50},
            duration_ms=200.0, stop_reason="end_turn",
        )
        logger.record_activity("Read file foo.py")
        logger.record_activity("Wrote file bar.py")

        session = _make_session()
        summary = logger.generate_summary(session)

        assert "Total LLM Calls:** 1" in summary
        assert "Total Input Tokens:** 100" in summary
        assert "Total Output Tokens:** 50" in summary
        assert "Read file foo.py" in summary
        assert "Wrote file bar.py" in summary
        logger.close()

    def test_summary_writes_file(self, tmp_path):
        logger = SessionLogger(session_dir=tmp_path, session_id="test-id")
        session = _make_session()
        logger.generate_summary(session)
        assert (tmp_path / "session_summary.md").exists()
        logger.close()

    def test_summary_appends_to_history(self, tmp_path):
        # generate_summary looks for memory_data at session_dir.parent.parent / "memory_data"
        # So: sess_dir.parent.parent = tmp_path / "results"
        # history_dir = tmp_path / "results" / "memory_data"
        sess_dir = tmp_path / "results" / "session" / "test_sess"
        sess_dir.mkdir(parents=True)
        history_dir = tmp_path / "results" / "memory_data"
        history_dir.mkdir(parents=True)

        logger = SessionLogger(session_dir=sess_dir, session_id="test-id")
        session = _make_session()
        logger.generate_summary(session)
        logger.close()

        history_file = history_dir / "history_session.md"
        assert history_file.exists()
        content = history_file.read_text()
        # Should contain legacy-format fields
        assert "## Session: test-id" in content
        assert "**Period:**" in content
        assert "**Duration:**" in content
        assert "### LLM Statistics" in content
        assert "### LLM Call Details" in content


# ======================================================================
# 16.3 — Resource Management
# ======================================================================

class TestSessionLoggerResourceManagement:
    """Catches: crash after close, file handles leaked."""

    def test_close_sets_flag(self, tmp_path):
        logger = SessionLogger(session_dir=tmp_path, session_id="test-id")
        assert not logger.closed
        logger.close()
        assert logger.closed

    def test_log_after_close_silent(self, tmp_path):
        """Logging after close must not crash (silently ignored)."""
        logger = SessionLogger(session_dir=tmp_path, session_id="test-id")
        logger.close()
        # Must not raise
        logger.log_llm_interaction(
            iteration=1, messages=[], tools=[], response={},
            usage={}, duration_ms=0, stop_reason="end_turn",
        )
        logger.record_activity("should be ignored")

    def test_context_manager(self, tmp_path):
        with SessionLogger(session_dir=tmp_path, session_id="test-id") as sl:
            sl.record_activity("test")
        assert sl.closed

    def test_double_close_no_error(self, tmp_path):
        logger = SessionLogger(session_dir=tmp_path, session_id="test-id")
        logger.close()
        logger.close()  # Must not raise


# ======================================================================
# 16.4 — User Query Logging
# ======================================================================

class TestSessionLoggerUserQuery:
    """Catches: user prompt not recorded or truncated."""

    def test_log_user_query_stores_full_prompt(self, tmp_path):
        logger = SessionLogger(session_dir=tmp_path, session_id="test-id")
        long_prompt = "A" * 5000
        logger.log_user_query(long_prompt)
        assert len(logger._user_queries) == 1
        assert logger._user_queries[0] == long_prompt
        # Operation step should be truncated
        assert len(logger._operation_steps) == 1
        assert "User query:" in logger._operation_steps[0]
        logger.close()

    def test_user_query_appears_in_summary(self, tmp_path):
        sess_dir = tmp_path / "results" / "session" / "test_sess"
        sess_dir.mkdir(parents=True)
        (tmp_path / "results" / "memory_data").mkdir(parents=True)

        logger = SessionLogger(session_dir=sess_dir, session_id="test-id")
        full_prompt = "Please analyze this complex dataset with multiple parameters"
        logger.log_user_query(full_prompt)

        session = _make_session()
        summary = logger.generate_summary(session)

        assert "### User Prompt" in summary
        assert full_prompt in summary
        logger.close()

    def test_multiple_queries_all_recorded(self, tmp_path):
        logger = SessionLogger(session_dir=tmp_path, session_id="test-id")
        logger.log_user_query("First query")
        logger.log_user_query("Second query")
        assert len(logger._user_queries) == 2
        logger.close()

    def test_log_user_query_after_close_silent(self, tmp_path):
        logger = SessionLogger(session_dir=tmp_path, session_id="test-id")
        logger.close()
        logger.log_user_query("should be ignored")
        assert len(logger._user_queries) == 0


# ======================================================================
# 16.5 — Tool Call Name List in LLM Call Details
# ======================================================================

class TestSessionLoggerToolCallNames:
    """Catches: tool_call_name_list missing or wrong in LLM Call Details."""

    def test_tool_names_tracked_in_llm_calls(self, tmp_path):
        logger = SessionLogger(session_dir=tmp_path, session_id="test-id")
        response_with_tools = {
            "content": "I'll help",
            "tool_calls": [
                {"id": "tc1", "function": {"name": "read_file", "arguments": "{}"}},
                {"id": "tc2", "function": {"name": "write_file", "arguments": "{}"}},
            ],
        }
        logger.log_llm_interaction(
            iteration=1, messages=[], tools=[],
            response=response_with_tools,
            usage={"input_tokens": 10, "output_tokens": 5},
            duration_ms=100.0, stop_reason="tool_use",
        )
        assert logger._llm_calls[0]["tool_call_names"] == ["read_file", "write_file"]
        assert logger._llm_calls[0]["tool_calls_count"] == 2
        logger.close()

    def test_tool_names_in_summary_table(self, tmp_path):
        sess_dir = tmp_path / "results" / "session" / "test_sess"
        sess_dir.mkdir(parents=True)
        (tmp_path / "results" / "memory_data").mkdir(parents=True)

        logger = SessionLogger(session_dir=sess_dir, session_id="test-id")
        response_with_tools = {
            "content": "",
            "tool_calls": [
                {"id": "tc1", "function": {"name": "search_arxiv", "arguments": "{}"}},
            ],
        }
        logger.log_llm_interaction(
            iteration=1, messages=[], tools=[],
            response=response_with_tools,
            usage={"input_tokens": 10, "output_tokens": 5},
            duration_ms=100.0, stop_reason="tool_use",
        )
        session = _make_session()
        summary = logger.generate_summary(session)

        # Table header must include Tool Names column
        assert "Tool Names" in summary
        # Table row must include the tool name
        assert "search_arxiv" in summary
        logger.close()

    def test_no_tool_calls_empty_names(self, tmp_path):
        logger = SessionLogger(session_dir=tmp_path, session_id="test-id")
        logger.log_llm_interaction(
            iteration=1, messages=[], tools=[],
            response={"content": "hello"},
            usage={"input_tokens": 10, "output_tokens": 5},
            duration_ms=100.0, stop_reason="end_turn",
        )
        assert logger._llm_calls[0]["tool_call_names"] == []
        assert logger._llm_calls[0]["tool_calls_count"] == 0
        logger.close()


# ======================================================================
# 16.6 — History Session Format Alignment
# ======================================================================

class TestSessionLoggerHistoryFormat:
    """Catches: history_session.md format not matching legacy format."""

    def test_history_has_legacy_format_fields(self, tmp_path):
        sess_dir = tmp_path / "results" / "session" / "test_sess"
        sess_dir.mkdir(parents=True)
        history_dir = tmp_path / "results" / "memory_data"
        history_dir.mkdir(parents=True)

        logger = SessionLogger(session_dir=sess_dir, session_id="session_20260329_100000")
        logger.log_user_query("Test prompt for history format")
        logger.log_llm_interaction(
            iteration=1, messages=[{"role": "user", "content": "hi"}],
            tools=[{"name": "tool1"}],
            response={"content": "hello", "tool_calls": [
                {"id": "tc1", "function": {"name": "tool1", "arguments": "{}"}}
            ]},
            usage={"input_tokens": 100, "output_tokens": 50},
            duration_ms=200.0, stop_reason="tool_use",
        )
        logger.record_activity("Executed tool: tool1")

        session = _make_session()
        logger.generate_summary(session)
        logger.close()

        history_file = history_dir / "history_session.md"
        content = history_file.read_text()

        # Verify all legacy format sections are present
        assert "## Session: session_20260329_100000" in content
        assert "**Period:**" in content
        assert "**Duration:**" in content
        assert "**Summary:**" in content
        assert "### User Prompt" in content
        assert "Test prompt for history format" in content
        assert "### Operation Steps" in content
        assert "### LLM Statistics" in content
        assert "Total LLM Calls:** 1" in content
        assert "Total Input Tokens:** 100" in content
        assert "Total Output Tokens:** 50" in content
        assert "Total Tokens:** 150" in content
        assert "### LLM Call Details" in content
        assert "| Iteration | Time | Input | Output | Total | Duration" in content
        assert "Tool Names" in content
        assert "tool1" in content

    def test_summary_file_matches_history_format(self, tmp_path):
        """session_summary.md should have the same format as history_session.md."""
        sess_dir = tmp_path / "results" / "session" / "test_sess"
        sess_dir.mkdir(parents=True)
        (tmp_path / "results" / "memory_data").mkdir(parents=True)

        logger = SessionLogger(session_dir=sess_dir, session_id="test-id")
        logger.log_user_query("My test prompt")
        logger.log_llm_interaction(
            iteration=1, messages=[], tools=[],
            response={"content": "ok"},
            usage={"input_tokens": 50, "output_tokens": 25},
            duration_ms=100.0, stop_reason="end_turn",
        )

        session = _make_session()
        logger.generate_summary(session)
        logger.close()

        summary_content = (sess_dir / "session_summary.md").read_text()
        assert "## Session: test-id" in summary_content
        assert "**Period:**" in summary_content
        assert "### User Prompt" in summary_content
        assert "My test prompt" in summary_content
        assert "### LLM Statistics" in summary_content
        assert "### LLM Call Details" in summary_content


# ======================================================================
# 16.7 — Tool Result Logging
# ======================================================================

class TestSessionLoggerToolResults:
    """Catches: tool results not logged or truncation broken."""

    def test_log_tool_results_writes_to_llm_md(self, tmp_path):
        logger = SessionLogger(session_dir=tmp_path, session_id="test-id")
        logger.log_tool_results(
            iteration=1,
            tool_results=[{
                "tool_name": "read_file",
                "tool_call_id": "tc1",
                "arguments": {"path": "/tmp/test.py"},
                "result": "file content here",
                "duration_ms": 50.0,
                "success": True,
                "error": "",
            }],
        )
        logger.close()

        md_content = (tmp_path / "session_llm.md").read_text()
        assert "read_file" in md_content
        assert "file content here" in md_content
        assert "/tmp/test.py" in md_content

    def test_tool_result_truncation(self, tmp_path):
        logger = SessionLogger(session_dir=tmp_path, session_id="test-id")
        long_result = "X" * 5000
        logger.log_tool_results(
            iteration=1,
            tool_results=[{
                "tool_name": "big_tool",
                "tool_call_id": "tc1",
                "arguments": {},
                "result": long_result,
                "duration_ms": 100.0,
                "success": True,
                "error": "",
            }],
        )
        logger.close()

        md_content = (tmp_path / "session_llm.md").read_text()
        assert "truncated" in md_content
        assert "5000 chars" in md_content

    def test_multiple_tool_results_separate(self, tmp_path):
        logger = SessionLogger(session_dir=tmp_path, session_id="test-id")
        logger.log_tool_results(
            iteration=1,
            tool_results=[
                {
                    "tool_name": "tool_a",
                    "tool_call_id": "tc1",
                    "arguments": {"a": 1},
                    "result": "result_a",
                    "duration_ms": 50.0,
                    "success": True,
                    "error": "",
                },
                {
                    "tool_name": "tool_b",
                    "tool_call_id": "tc2",
                    "arguments": {"b": 2},
                    "result": "result_b",
                    "duration_ms": 75.0,
                    "success": False,
                    "error": "timeout",
                },
            ],
        )
        logger.close()

        md_content = (tmp_path / "session_llm.md").read_text()
        assert "Tool 1: `tool_a`" in md_content
        assert "Tool 2: `tool_b`" in md_content
        assert "result_a" in md_content
        assert "result_b" in md_content
        assert "timeout" in md_content

    def test_log_tool_results_after_close_silent(self, tmp_path):
        logger = SessionLogger(session_dir=tmp_path, session_id="test-id")
        logger.close()
        # Must not raise
        logger.log_tool_results(iteration=1, tool_results=[{"tool_name": "x"}])
