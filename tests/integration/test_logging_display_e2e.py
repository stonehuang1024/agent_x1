"""
End-to-end integration tests for the logging & display system.

These tests simulate realistic agent execution scenarios — various
prompt patterns that trigger different event sequences — and verify
that the ENTIRE logging/display pipeline produces correct output.

Unlike unit tests that test individual modules in isolation, these
tests wire up the full stack (EventBus → LogIntegration → ActivityStream
+ StructuredLogger + TokenTracker + ConsoleDisplay) and verify the
emergent behavior of the integrated system.

Bug targets:
- Event data lost between modules (EventBus emits but downstream misses)
- Ordering bugs: events arrive out of order, display garbled
- State accumulation: counters drift across multi-turn conversations
- File I/O: structured log incomplete after complex session
- Display coherence: user sees confusing or missing output for real scenarios
- Session summary: statistics wrong after mixed success/failure flows
"""

import io
import json
import tempfile
import time
from pathlib import Path
from typing import Dict, Any, List

import pytest

from src.core.events import EventBus, AgentEvent
from src.util.display import ConsoleDisplay
from src.util.activity_stream import ActivityStream
from src.util.structured_log import StructuredLogger, LogEventType
from src.util.token_tracker import TokenTracker
from src.util.log_integration import LogIntegration


# ── Test infrastructure ───────────────────────────────────────────────

class FullStack:
    """
    Complete logging/display stack for integration testing.
    
    Wires up all components exactly as main.py does, but with
    captured output for assertion.
    """
    
    def __init__(self, tmpdir: str, verbose: bool = False, debug: bool = False):
        self.tmpdir = tmpdir
        self.output = io.StringIO()
        self.display = ConsoleDisplay(
            color=False, stream=self.output,
            verbose=verbose, debug=debug,
        )
        self.activity_stream = ActivityStream(
            display=self.display,
            verbose=verbose or debug,
            session_start_time=time.time(),
        )
        self.tracker = TokenTracker()
        self.slog = StructuredLogger(
            session_dir=tmpdir,
            session_id="integration-test",
            buffer_size=1,  # Flush every event for test reliability
        )
        self.event_bus = EventBus()
        self.integration = LogIntegration(
            display=self.display,
            activity_stream=self.activity_stream,
            structured_logger=self.slog,
            token_tracker=self.tracker,
        )
        self.integration.setup(self.event_bus)
    
    def emit(self, event_type: AgentEvent, **kwargs):
        """Emit an event through the full pipeline."""
        self.event_bus.emit(event_type, **kwargs)
    
    @property
    def display_text(self) -> str:
        """Get all display output as text."""
        return self.output.getvalue()
    
    @property
    def log_events(self) -> List[Dict[str, Any]]:
        """Read and parse all structured log events."""
        self.slog._flush()
        log_file = Path(self.tmpdir) / "session_log.jsonl"
        if not log_file.exists():
            return []
        lines = log_file.read_text().strip().split("\n")
        return [json.loads(line) for line in lines if line.strip()]
    
    def close(self):
        """Close the structured logger."""
        self.slog.close()


def make_stack(tmpdir: str, **kwargs) -> FullStack:
    return FullStack(tmpdir, **kwargs)


# ── Scenario 1: Simple text-only LLM response ────────────────────────

class TestScenarioTextOnlyResponse:
    """
    Simulate: User asks a question → LLM returns text, no tool calls.
    
    This is the simplest prompt pattern. Verifies the baseline works.
    """
    
    def test_text_response_full_pipeline(self):
        """Bug: simple text response doesn't flow through all subsystems,
        user sees nothing or partial output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            # Simulate: LLM request
            stack.emit(AgentEvent.LLM_CALL_STARTED,
                       iteration=1, message_count=3)
            
            # Simulate: LLM responds with text only
            stack.emit(AgentEvent.LLM_CALL_COMPLETED,
                       iteration=1,
                       input_tokens=500,
                       output_tokens=200,
                       duration_s=1.5,
                       tool_call_count=0,
                       content_preview="The answer to your question is 42.")
            
            # Verify display output
            text = stack.display_text
            assert "LLM Request #1" in text, "LLM request not shown"
            assert "LLM Response #1" in text, "LLM response not shown"
            assert "500" in text, "Input tokens not shown"
            assert "200" in text, "Output tokens not shown"
            assert "1.5s" in text, "Duration not shown"
            
            # Verify tracker
            assert stack.tracker.total_input_tokens == 500
            assert stack.tracker.total_output_tokens == 200
            assert stack.tracker.llm_call_count == 1
            assert stack.tracker.tool_call_count == 0
            
            # Verify structured log
            events = stack.log_events
            llm_events = [e for e in events if e["event_type"] == "llm_call"]
            assert len(llm_events) == 1, (
                f"Expected 1 llm_call event, got {len(llm_events)}"
            )
            assert llm_events[0]["data"]["input_tokens"] == 500
            
            stack.close()
    
    def test_text_response_content_preview_shown(self):
        """Bug: content preview not displayed for text-only responses,
        user can't see what the LLM said in the activity stream."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            stack.emit(AgentEvent.LLM_CALL_STARTED,
                       iteration=1, message_count=3)
            stack.emit(AgentEvent.LLM_CALL_COMPLETED,
                       iteration=1,
                       input_tokens=100, output_tokens=50,
                       duration_s=0.5, tool_call_count=0,
                       content_preview="Hello, I can help you with that.")
            
            text = stack.display_text
            # Content preview should appear for text-only responses
            assert "Hello" in text, (
                "Content preview not shown for text-only response — "
                "user can't see what the LLM said"
            )
            stack.close()


# ── Scenario 2: Single tool call ──────────────────────────────────────

class TestScenarioSingleToolCall:
    """
    Simulate: User asks to read a file → LLM calls read_file → success.
    
    The most common tool-use pattern.
    """
    
    def test_single_tool_call_full_pipeline(self):
        """Bug: tool call events don't propagate through all subsystems."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            # Step 1: LLM request
            stack.emit(AgentEvent.LLM_CALL_STARTED,
                       iteration=1, message_count=5)
            
            # Step 2: LLM responds with tool call
            stack.emit(AgentEvent.LLM_CALL_COMPLETED,
                       iteration=1,
                       input_tokens=1200, output_tokens=80,
                       duration_s=2.0, tool_call_count=1,
                       content_preview="")
            
            # Step 3: Tool called
            stack.emit(AgentEvent.TOOL_CALLED,
                       tool_name="read_file",
                       arguments={"path": "/src/main.py", "encoding": "utf-8"})
            
            # Step 4: Tool succeeds
            stack.emit(AgentEvent.TOOL_SUCCEEDED,
                       tool_name="read_file",
                       duration_ms=85,
                       result_preview='import sys\nimport os\n\ndef main():',
                       result_length=2500)
            
            # Verify display shows the full flow
            text = stack.display_text
            assert "LLM Request #1" in text
            assert "LLM Response #1" in text
            assert "1 tool calls" in text, "Tool call count not shown in LLM response"
            assert "read_file" in text, "Tool name not shown"
            assert "/src/main.py" in text, "Tool argument not shown"
            assert "85ms" in text, "Tool duration not shown"
            
            # Verify tracker counts both LLM and tool
            assert stack.tracker.llm_call_count == 1
            assert stack.tracker.tool_call_count == 1
            stats = stack.tracker.get_stats()
            assert stats["tool_success_count"] == 1
            assert stats["tool_failure_count"] == 0
            
            # Verify structured log has both events
            events = stack.log_events
            event_types = [e["event_type"] for e in events]
            assert "llm_call" in event_types
            assert "tool_call" in event_types
            
            tool_events = [e for e in events if e["event_type"] == "tool_call"]
            assert tool_events[0]["data"]["tool_name"] == "read_file"
            assert tool_events[0]["data"]["success"] is True
            
            stack.close()
    
    def test_tool_arguments_with_special_characters(self):
        """Bug: tool arguments with special characters (quotes, newlines,
        unicode) corrupt the display or structured log."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            stack.emit(AgentEvent.TOOL_CALLED,
                       tool_name="write_file",
                       arguments={
                           "path": "/tmp/test file (1).py",
                           "content": 'print("hello\\nworld")\n# 中文注释',
                       })
            
            stack.emit(AgentEvent.TOOL_SUCCEEDED,
                       tool_name="write_file",
                       duration_ms=50,
                       result_preview="File written successfully",
                       result_length=25)
            
            # Display should not crash
            text = stack.display_text
            assert "write_file" in text
            
            # Structured log should have valid JSON
            events = stack.log_events
            tool_events = [e for e in events if e["event_type"] == "tool_call"]
            assert len(tool_events) >= 1
            # Verify the JSON was valid (we got here without json.loads error)
            
            stack.close()


# ── Scenario 3: Multiple parallel tool calls ─────────────────────────

class TestScenarioParallelToolCalls:
    """
    Simulate: LLM requests 3 tools in parallel → mixed success/failure.
    
    Tests the parallel batch display and mixed result handling.
    """
    
    def test_parallel_tools_full_pipeline(self):
        """Bug: parallel tool calls not displayed correctly,
        or tracker counts wrong with mixed results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            # LLM request
            stack.emit(AgentEvent.LLM_CALL_STARTED,
                       iteration=1, message_count=8)
            
            # LLM responds with 3 tool calls
            stack.emit(AgentEvent.LLM_CALL_COMPLETED,
                       iteration=1,
                       input_tokens=3000, output_tokens=300,
                       duration_s=3.5, tool_call_count=3)
            
            # Tool calls (simulating parallel batch)
            stack.emit(AgentEvent.TOOL_CALLED,
                       tool_name="read_file",
                       arguments={"path": "/src/a.py"})
            stack.emit(AgentEvent.TOOL_CALLED,
                       tool_name="read_file",
                       arguments={"path": "/src/b.py"})
            stack.emit(AgentEvent.TOOL_CALLED,
                       tool_name="grep_search",
                       arguments={"query": "def main", "path": "/src"})
            
            # Results: 2 success, 1 failure
            stack.emit(AgentEvent.TOOL_SUCCEEDED,
                       tool_name="read_file",
                       duration_ms=50,
                       result_preview="# File a.py content",
                       result_length=100)
            stack.emit(AgentEvent.TOOL_SUCCEEDED,
                       tool_name="read_file",
                       duration_ms=75,
                       result_preview="# File b.py content",
                       result_length=200)
            stack.emit(AgentEvent.TOOL_FAILED,
                       tool_name="grep_search",
                       duration_ms=120,
                       error="Permission denied: /src")
            
            # Verify display
            text = stack.display_text
            assert "3 tool calls" in text, "Parallel tool count not shown"
            assert "read_file" in text
            assert "grep_search" in text
            assert "Permission denied" in text, "Error message not shown"
            
            # Verify tracker: 3 tool calls, 2 success, 1 failure
            assert stack.tracker.tool_call_count == 3
            stats = stack.tracker.get_stats()
            assert stats["tool_success_count"] == 2
            assert stats["tool_failure_count"] == 1
            
            # Verify structured log has all 3 tool events
            events = stack.log_events
            tool_events = [e for e in events if e["event_type"] == "tool_call"]
            assert len(tool_events) == 3, (
                f"Expected 3 tool_call events, got {len(tool_events)}"
            )
            success_events = [e for e in tool_events if e["data"].get("success") is True]
            failure_events = [e for e in tool_events if e["data"].get("success") is False]
            assert len(success_events) == 2
            assert len(failure_events) == 1
            
            stack.close()


# ── Scenario 4: Multi-iteration tool loop ─────────────────────────────

class TestScenarioMultiIterationLoop:
    """
    Simulate: LLM calls tool → reads result → calls another tool → reads → responds.
    
    This is the typical agentic loop: multiple LLM iterations with
    tool calls in between.
    """
    
    def test_multi_iteration_accumulates_correctly(self):
        """Bug: token counts or tool counts reset between iterations,
        session totals are wrong."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            # Iteration 1: LLM → tool call
            stack.emit(AgentEvent.LLM_CALL_STARTED,
                       iteration=1, message_count=3)
            stack.emit(AgentEvent.LLM_CALL_COMPLETED,
                       iteration=1,
                       input_tokens=1000, output_tokens=100,
                       duration_s=1.0, tool_call_count=1)
            stack.emit(AgentEvent.TOOL_CALLED,
                       tool_name="read_file",
                       arguments={"path": "/src/config.py"})
            stack.emit(AgentEvent.TOOL_SUCCEEDED,
                       tool_name="read_file",
                       duration_ms=60,
                       result_preview="config data...",
                       result_length=500)
            
            # Iteration 2: LLM → another tool call
            stack.emit(AgentEvent.LLM_CALL_STARTED,
                       iteration=2, message_count=6)
            stack.emit(AgentEvent.LLM_CALL_COMPLETED,
                       iteration=2,
                       input_tokens=2000, output_tokens=150,
                       duration_s=1.5, tool_call_count=1)
            stack.emit(AgentEvent.TOOL_CALLED,
                       tool_name="write_file",
                       arguments={"path": "/src/output.py", "content": "# generated"})
            stack.emit(AgentEvent.TOOL_SUCCEEDED,
                       tool_name="write_file",
                       duration_ms=30,
                       result_preview="File written",
                       result_length=12)
            
            # Iteration 3: LLM → final text response
            stack.emit(AgentEvent.LLM_CALL_STARTED,
                       iteration=3, message_count=9)
            stack.emit(AgentEvent.LLM_CALL_COMPLETED,
                       iteration=3,
                       input_tokens=3000, output_tokens=200,
                       duration_s=2.0, tool_call_count=0,
                       content_preview="I've completed the task.")
            
            # Verify cumulative tracker
            assert stack.tracker.llm_call_count == 3, (
                f"Expected 3 LLM calls, got {stack.tracker.llm_call_count}"
            )
            assert stack.tracker.total_input_tokens == 6000, (
                f"Expected 6000 input tokens (1000+2000+3000), "
                f"got {stack.tracker.total_input_tokens}"
            )
            assert stack.tracker.total_output_tokens == 450, (
                f"Expected 450 output tokens (100+150+200), "
                f"got {stack.tracker.total_output_tokens}"
            )
            assert stack.tracker.tool_call_count == 2
            
            # Verify display shows all 3 LLM calls
            text = stack.display_text
            assert "LLM Request #1" in text
            assert "LLM Request #2" in text
            assert "LLM Request #3" in text
            assert "LLM Response #1" in text
            assert "LLM Response #2" in text
            assert "LLM Response #3" in text
            
            # Verify structured log has all events in order
            events = stack.log_events
            llm_events = [e for e in events if e["event_type"] == "llm_call"]
            tool_events = [e for e in events if e["event_type"] == "tool_call"]
            assert len(llm_events) == 3
            assert len(tool_events) == 2
            
            stack.close()
    
    def test_llm_call_counter_increments_across_iterations(self):
        """Bug: LLM call counter resets between iterations,
        all responses show #1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            for i in range(1, 6):
                stack.emit(AgentEvent.LLM_CALL_STARTED,
                           iteration=i, message_count=i * 3)
                stack.emit(AgentEvent.LLM_CALL_COMPLETED,
                           iteration=i,
                           input_tokens=100 * i, output_tokens=50 * i,
                           duration_s=0.5 * i, tool_call_count=0)
            
            text = stack.display_text
            for i in range(1, 6):
                assert f"LLM Request #{i}" in text, (
                    f"LLM Request #{i} not found — counter may have reset"
                )
                assert f"LLM Response #{i}" in text
            
            stack.close()


# ── Scenario 5: LLM failure and recovery ─────────────────────────────

class TestScenarioLLMFailure:
    """
    Simulate: LLM call fails (rate limit, timeout) → retry → success.
    """
    
    def test_llm_failure_displayed_and_logged(self):
        """Bug: LLM failure not shown to user or not logged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            # LLM request
            stack.emit(AgentEvent.LLM_CALL_STARTED,
                       iteration=1, message_count=5)
            
            # LLM fails
            stack.emit(AgentEvent.LLM_CALL_FAILED,
                       iteration=1,
                       error="Rate limit exceeded. Please retry after 30s.")
            
            text = stack.display_text
            assert "Rate limit exceeded" in text, (
                "LLM failure error message not shown to user"
            )
            assert "failed" in text.lower(), (
                "LLM failure not indicated in display"
            )
            
            # Verify structured log records the failure
            events = stack.log_events
            llm_events = [e for e in events if e["event_type"] == "llm_call"]
            assert len(llm_events) >= 1
            failure_events = [e for e in llm_events if e["data"].get("success") is False]
            assert len(failure_events) >= 1, (
                "LLM failure not recorded in structured log"
            )
            
            stack.close()
    
    def test_llm_failure_then_success_tracker_correct(self):
        """Bug: failed LLM call counted in token totals,
        inflating the session statistics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            # Attempt 1: fails
            stack.emit(AgentEvent.LLM_CALL_STARTED,
                       iteration=1, message_count=5)
            stack.emit(AgentEvent.LLM_CALL_FAILED,
                       iteration=1, error="Timeout")
            
            # Attempt 2: succeeds
            stack.emit(AgentEvent.LLM_CALL_STARTED,
                       iteration=1, message_count=5)
            stack.emit(AgentEvent.LLM_CALL_COMPLETED,
                       iteration=1,
                       input_tokens=1000, output_tokens=500,
                       duration_s=2.0, tool_call_count=0)
            
            # Only the successful call should count in tracker
            assert stack.tracker.llm_call_count == 1, (
                f"Expected 1 LLM call (only success), "
                f"got {stack.tracker.llm_call_count} — "
                f"failed call may have been counted"
            )
            assert stack.tracker.total_input_tokens == 1000
            assert stack.tracker.total_output_tokens == 500
            
            stack.close()


# ── Scenario 6: Tool failure ─────────────────────────────────────────

class TestScenarioToolFailure:
    """
    Simulate: Tool call fails with various error types.
    """
    
    def test_tool_timeout_displayed(self):
        """Bug: tool timeout not shown as error to user."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            stack.emit(AgentEvent.TOOL_CALLED,
                       tool_name="bash",
                       arguments={"command": "sleep 300"})
            stack.emit(AgentEvent.TOOL_FAILED,
                       tool_name="bash",
                       duration_ms=30000,
                       error="Tool 'bash' timed out after 30s")
            
            text = stack.display_text
            assert "timed out" in text, "Timeout error not shown"
            assert "bash" in text
            assert "30000ms" in text or "30s" in text
            
            stack.close()
    
    def test_tool_not_found_displayed(self):
        """Bug: tool-not-found error not shown clearly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            stack.emit(AgentEvent.TOOL_CALLED,
                       tool_name="nonexistent_tool",
                       arguments={})
            stack.emit(AgentEvent.TOOL_FAILED,
                       tool_name="nonexistent_tool",
                       duration_ms=1,
                       error="Tool 'nonexistent_tool' not found")
            
            text = stack.display_text
            assert "nonexistent_tool" in text
            assert "not found" in text
            
            stack.close()
    
    def test_tool_error_with_long_traceback(self):
        """Bug: long error traceback floods the display."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            long_traceback = "Traceback (most recent call last):\n" + \
                "\n".join([f'  File "module_{i}.py", line {i*10}, in func_{i}' 
                           for i in range(50)]) + \
                "\nValueError: something went wrong"
            
            stack.emit(AgentEvent.TOOL_FAILED,
                       tool_name="complex_tool",
                       duration_ms=500,
                       error=long_traceback)
            
            text = stack.display_text
            # Error should be shown but truncated
            assert "complex_tool" in text
            assert "something went wrong" in text or "Traceback" in text
            # Should not contain all 50 stack frames
            assert text.count("module_") < 50, (
                "Long traceback not truncated — floods the display"
            )
            
            stack.close()


# ── Scenario 7: Session lifecycle ─────────────────────────────────────

class TestScenarioSessionLifecycle:
    """
    Simulate: Complete session from start to finish with summary.
    """
    
    def test_complete_session_generates_correct_summary(self):
        """Bug: session summary statistics don't match actual activity."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            # Simulate a realistic session
            # Turn 1: LLM + 2 tools
            stack.emit(AgentEvent.LLM_CALL_STARTED, iteration=1, message_count=3)
            stack.emit(AgentEvent.LLM_CALL_COMPLETED, iteration=1,
                       input_tokens=1500, output_tokens=200,
                       duration_s=2.0, tool_call_count=2)
            stack.emit(AgentEvent.TOOL_CALLED, tool_name="read_file",
                       arguments={"path": "/src/a.py"})
            stack.emit(AgentEvent.TOOL_SUCCEEDED, tool_name="read_file",
                       duration_ms=50, result_preview="content", result_length=100)
            stack.emit(AgentEvent.TOOL_CALLED, tool_name="write_file",
                       arguments={"path": "/src/b.py", "content": "new"})
            stack.emit(AgentEvent.TOOL_SUCCEEDED, tool_name="write_file",
                       duration_ms=30, result_preview="ok", result_length=2)
            
            # Turn 2: LLM + 1 failed tool
            stack.emit(AgentEvent.LLM_CALL_STARTED, iteration=2, message_count=8)
            stack.emit(AgentEvent.LLM_CALL_COMPLETED, iteration=2,
                       input_tokens=3000, output_tokens=100,
                       duration_s=1.5, tool_call_count=1)
            stack.emit(AgentEvent.TOOL_CALLED, tool_name="bash",
                       arguments={"command": "make test"})
            stack.emit(AgentEvent.TOOL_FAILED, tool_name="bash",
                       duration_ms=5000, error="Exit code 1: test failed")
            
            # Turn 3: LLM final response
            stack.emit(AgentEvent.LLM_CALL_STARTED, iteration=3, message_count=11)
            stack.emit(AgentEvent.LLM_CALL_COMPLETED, iteration=3,
                       input_tokens=4000, output_tokens=300,
                       duration_s=3.0, tool_call_count=0,
                       content_preview="The tests failed because...")
            
            # Session completed
            stack.emit(AgentEvent.SESSION_COMPLETED)
            
            # Verify display shows session summary
            text = stack.display_text
            assert "Session Summary" in text, "Session summary not displayed"
            
            # Verify summary file exists
            summary_file = Path(tmpdir) / "session_summary.md"
            assert summary_file.exists(), "Summary file not created"
            summary_content = summary_file.read_text()
            assert "Session Summary" in summary_content
            
            # Verify tracker totals match what we emitted
            assert stack.tracker.total_input_tokens == 8500  # 1500+3000+4000
            assert stack.tracker.total_output_tokens == 600  # 200+100+300
            assert stack.tracker.llm_call_count == 3
            assert stack.tracker.tool_call_count == 3  # 2 success + 1 failure
            stats = stack.tracker.get_stats()
            assert stats["tool_success_count"] == 2
            assert stats["tool_failure_count"] == 1
            
            stack.close()
    
    def test_session_failed_still_generates_summary(self):
        """Bug: session failure prevents summary generation,
        losing all session data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            # Some activity before failure
            stack.emit(AgentEvent.LLM_CALL_STARTED, iteration=1, message_count=3)
            stack.emit(AgentEvent.LLM_CALL_COMPLETED, iteration=1,
                       input_tokens=500, output_tokens=100,
                       duration_s=1.0, tool_call_count=0)
            
            # Session fails
            stack.emit(AgentEvent.SESSION_FAILED,
                       error="Out of memory: context too large")
            
            text = stack.display_text
            assert "Out of memory" in text, "Failure reason not shown"
            assert "Session Summary" in text, (
                "Session summary not shown after failure — "
                "user loses all session statistics"
            )
            
            # Summary file should still be created
            summary_file = Path(tmpdir) / "session_summary.md"
            assert summary_file.exists(), (
                "Summary file not created after session failure"
            )
            
            stack.close()


# ── Scenario 8: Loop detection ────────────────────────────────────────

class TestScenarioLoopDetection:
    """
    Simulate: Agent enters a loop, loop detection fires.
    """
    
    def test_loop_detection_warning_visible(self):
        """Bug: loop detection warning not visible to user,
        agent keeps looping silently."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            # Simulate repetitive tool calls
            for i in range(5):
                stack.emit(AgentEvent.LLM_CALL_STARTED,
                           iteration=i+1, message_count=3+i*2)
                stack.emit(AgentEvent.LLM_CALL_COMPLETED,
                           iteration=i+1,
                           input_tokens=1000, output_tokens=50,
                           duration_s=1.0, tool_call_count=1)
                stack.emit(AgentEvent.TOOL_CALLED,
                           tool_name="read_file",
                           arguments={"path": "/src/same_file.py"})
                stack.emit(AgentEvent.TOOL_SUCCEEDED,
                           tool_name="read_file",
                           duration_ms=50,
                           result_preview="same content",
                           result_length=100)
            
            # Loop detected
            stack.emit(AgentEvent.LOOP_DETECTED,
                       message="Tool read_file called 5 times with identical arguments")
            
            text = stack.display_text
            assert "Loop detected" in text
            assert "identical arguments" in text
            
            # Verify all 5 iterations were tracked
            assert stack.tracker.llm_call_count == 5
            assert stack.tracker.tool_call_count == 5
            
            stack.close()


# ── Scenario 9: Extreme/adversarial inputs ────────────────────────────

class TestScenarioAdversarialInputs:
    """
    Simulate: Edge cases that could break the pipeline.
    """
    
    def test_zero_token_llm_response(self):
        """Bug: zero tokens cause division by zero or display error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            stack.emit(AgentEvent.LLM_CALL_STARTED,
                       iteration=1, message_count=0)
            stack.emit(AgentEvent.LLM_CALL_COMPLETED,
                       iteration=1,
                       input_tokens=0, output_tokens=0,
                       duration_s=0.0, tool_call_count=0)
            
            # Should not crash
            text = stack.display_text
            assert "LLM Response #1" in text
            
            # Session complete with zero stats
            stack.emit(AgentEvent.SESSION_COMPLETED)
            text = stack.display_text
            assert "Session Summary" in text
            
            stack.close()
    
    def test_very_large_token_counts(self):
        """Bug: very large token counts cause overflow or formatting error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            stack.emit(AgentEvent.LLM_CALL_STARTED,
                       iteration=1, message_count=100)
            stack.emit(AgentEvent.LLM_CALL_COMPLETED,
                       iteration=1,
                       input_tokens=10_000_000,
                       output_tokens=5_000_000,
                       duration_s=120.0,
                       tool_call_count=0)
            
            text = stack.display_text
            assert "10,000,000" in text, "Large input tokens not comma-formatted"
            assert "5,000,000" in text, "Large output tokens not comma-formatted"
            
            assert stack.tracker.total_tokens == 15_000_000
            
            stack.close()
    
    def test_unicode_in_tool_output(self):
        """Bug: Unicode characters in tool output corrupt display or log."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            stack.emit(AgentEvent.TOOL_CALLED,
                       tool_name="read_file",
                       arguments={"path": "/src/中文文件.py"})
            stack.emit(AgentEvent.TOOL_SUCCEEDED,
                       tool_name="read_file",
                       duration_ms=50,
                       result_preview="# 这是中文注释 🔧 émojis ñ ü",
                       result_length=100)
            
            text = stack.display_text
            assert "中文文件" in text, "Unicode filename not shown"
            
            # Verify structured log preserves Unicode
            events = stack.log_events
            tool_events = [e for e in events if e["event_type"] == "tool_call"]
            assert len(tool_events) >= 1
            
            stack.close()
    
    def test_empty_tool_name(self):
        """Bug: empty tool name causes crash or confusing display."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            # Should not crash
            stack.emit(AgentEvent.TOOL_CALLED,
                       tool_name="",
                       arguments={})
            stack.emit(AgentEvent.TOOL_SUCCEEDED,
                       tool_name="",
                       duration_ms=0,
                       result_preview="",
                       result_length=0)
            
            # Should not crash
            assert stack.tracker.tool_call_count == 1
            
            stack.close()
    
    def test_very_long_tool_output(self):
        """Bug: very long tool output not truncated in display,
        flooding the terminal."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            long_output = "x" * 10000
            stack.emit(AgentEvent.TOOL_SUCCEEDED,
                       tool_name="bash",
                       duration_ms=100,
                       result_preview=long_output,
                       result_length=10000)
            
            text = stack.display_text
            # Display should truncate the output
            assert len(text) < 5000, (
                f"Display output is {len(text)} chars — "
                f"long tool output not truncated"
            )
            
            stack.close()
    
    def test_rapid_fire_events(self):
        """Bug: rapid event emission causes buffer corruption or lost events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            # Emit 50 events rapidly
            for i in range(25):
                stack.emit(AgentEvent.TOOL_CALLED,
                           tool_name=f"tool_{i}",
                           arguments={"index": i})
                stack.emit(AgentEvent.TOOL_SUCCEEDED,
                           tool_name=f"tool_{i}",
                           duration_ms=i * 10,
                           result_preview=f"result_{i}",
                           result_length=10)
            
            # All 25 tool calls should be tracked
            assert stack.tracker.tool_call_count == 25, (
                f"Expected 25 tool calls, got {stack.tracker.tool_call_count} — "
                f"events lost during rapid emission"
            )
            
            # All events should be in structured log
            events = stack.log_events
            tool_events = [e for e in events if e["event_type"] == "tool_call"]
            assert len(tool_events) == 25, (
                f"Expected 25 tool_call events in log, got {len(tool_events)}"
            )
            
            stack.close()
    
    def test_events_with_none_data_fields(self):
        """Bug: None values in event data cause TypeError in formatting."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            # Emit events with None values where strings are expected
            stack.emit(AgentEvent.TOOL_CALLED,
                       tool_name="test",
                       arguments=None)
            stack.emit(AgentEvent.TOOL_SUCCEEDED,
                       tool_name="test",
                       duration_ms=50,
                       result_preview=None,
                       result_length=None)
            stack.emit(AgentEvent.LLM_CALL_COMPLETED,
                       iteration=1,
                       input_tokens=100,
                       output_tokens=50,
                       duration_s=1.0,
                       tool_call_count=0,
                       content_preview=None)
            
            # Should not crash
            assert stack.tracker.tool_call_count == 1
            assert stack.tracker.llm_call_count == 1
            
            stack.close()


# ── Scenario 10: Verbose mode differences ─────────────────────────────

class TestScenarioVerboseMode:
    """
    Verify verbose mode shows more detail than normal mode.
    """
    
    def test_verbose_shows_more_tool_output(self):
        """Bug: verbose mode doesn't actually show more output,
        making --verbose flag useless."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Normal mode
            stack_normal = make_stack(tmpdir + "/normal", verbose=False)
            Path(tmpdir + "/normal").mkdir(parents=True, exist_ok=True)
            stack_normal.slog = StructuredLogger(
                session_dir=tmpdir + "/normal",
                session_id="normal", buffer_size=1)
            
            # Verbose mode
            stack_verbose = make_stack(tmpdir + "/verbose", verbose=True)
            Path(tmpdir + "/verbose").mkdir(parents=True, exist_ok=True)
            stack_verbose.slog = StructuredLogger(
                session_dir=tmpdir + "/verbose",
                session_id="verbose", buffer_size=1)
            
            # Same event to both
            medium_output = "x" * 250  # Between normal and verbose limits
            
            for stack in [stack_normal, stack_verbose]:
                stack.emit(AgentEvent.TOOL_SUCCEEDED,
                           tool_name="read_file",
                           duration_ms=50,
                           result_preview=medium_output,
                           result_length=250)
            
            normal_text = stack_normal.display_text
            verbose_text = stack_verbose.display_text
            
            # Verbose should show more of the output
            assert len(verbose_text) >= len(normal_text), (
                "Verbose mode doesn't show more output than normal mode"
            )
            
            stack_normal.close()
            stack_verbose.close()


# ── Scenario 11: State change events ─────────────────────────────────

class TestScenarioStateChanges:
    """
    Simulate: Agent state transitions during execution.
    """
    
    def test_state_changes_displayed(self):
        """Bug: state changes not shown to user, agent appears frozen."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            stack.emit(AgentEvent.STATE_CHANGED,
                       new_state="assembling_context",
                       message="Building 8-layer context")
            stack.emit(AgentEvent.STATE_CHANGED,
                       new_state="waiting_for_llm",
                       message="")
            stack.emit(AgentEvent.STATE_CHANGED,
                       new_state="executing_tools",
                       message="Running 3 tools")
            
            text = stack.display_text
            assert "assembling_context" in text
            assert "Building 8-layer context" in text
            assert "executing_tools" in text
            
            stack.close()


# ── Scenario 12: Turn completion events ───────────────────────────────

class TestScenarioTurnCompletion:
    """
    Simulate: Turn completion events logged to structured log.
    """
    
    def test_turn_completion_logged(self):
        """Bug: turn completion not logged, session replay impossible."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            stack.emit(AgentEvent.TURN_COMPLETED,
                       turn_number=1,
                       tool_call_count=3,
                       user_input_summary="How do I fix the bug?")
            
            events = stack.log_events
            turn_events = [e for e in events if e["event_type"] == "turn_complete"]
            assert len(turn_events) == 1, (
                "Turn completion not logged to structured log"
            )
            assert turn_events[0]["data"]["turn_number"] == 1
            assert turn_events[0]["data"]["tool_call_count"] == 3
            
            stack.close()


# ── Scenario 13: Loop iteration (step progress) ──────────────────────

class TestScenarioLoopIteration:
    """
    Simulate: Step progress events during agent loop.
    """
    
    def test_step_progress_displayed(self):
        """Bug: step progress not shown, user doesn't know which iteration."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            stack.emit(AgentEvent.LOOP_ITERATION,
                       iteration=3, max_iterations=10,
                       description="Processing tool results")
            
            text = stack.display_text
            assert "3" in text
            assert "10" in text
            
            stack.close()


# ── Scenario 14: Structured log integrity after complex session ───────

class TestScenarioStructuredLogIntegrity:
    """
    Verify structured log file integrity after a complex session.
    """
    
    def test_all_events_have_consistent_session_id(self):
        """Bug: some events have wrong or missing session_id."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            # Emit various events
            stack.emit(AgentEvent.LLM_CALL_STARTED, iteration=1, message_count=3)
            stack.emit(AgentEvent.LLM_CALL_COMPLETED, iteration=1,
                       input_tokens=100, output_tokens=50,
                       duration_s=1.0, tool_call_count=1)
            stack.emit(AgentEvent.TOOL_CALLED, tool_name="test", arguments={})
            stack.emit(AgentEvent.TOOL_SUCCEEDED, tool_name="test",
                       duration_ms=50, result_preview="ok", result_length=2)
            stack.emit(AgentEvent.SESSION_COMPLETED)
            
            events = stack.log_events
            for event in events:
                assert event["session_id"] == "integration-test", (
                    f"Event {event['event_type']} has wrong session_id: "
                    f"{event['session_id']}"
                )
            
            stack.close()
    
    def test_events_have_monotonic_timestamps(self):
        """Bug: timestamps not monotonically increasing,
        breaking timeline analysis."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            for i in range(10):
                stack.emit(AgentEvent.TOOL_CALLED,
                           tool_name=f"tool_{i}", arguments={})
                stack.emit(AgentEvent.TOOL_SUCCEEDED,
                           tool_name=f"tool_{i}",
                           duration_ms=10, result_preview="ok", result_length=2)
            
            events = stack.log_events
            timestamps = [e["timestamp"] for e in events]
            
            for i in range(1, len(timestamps)):
                assert timestamps[i] >= timestamps[i-1], (
                    f"Timestamp not monotonic at index {i}: "
                    f"{timestamps[i-1]} > {timestamps[i]}"
                )
            
            stack.close()
    
    def test_every_event_line_is_valid_json(self):
        """Bug: buffer corruption produces invalid JSON lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            # Emit many events with various data types
            for i in range(20):
                stack.emit(AgentEvent.TOOL_CALLED,
                           tool_name=f"tool_{i}",
                           arguments={"index": i, "flag": True, "value": 3.14})
                stack.emit(AgentEvent.TOOL_SUCCEEDED,
                           tool_name=f"tool_{i}",
                           duration_ms=i * 5.5,
                           result_preview=f"result with 'quotes' and \"double quotes\"",
                           result_length=50)
            
            stack.emit(AgentEvent.SESSION_COMPLETED)
            stack.close()
            
            # Read raw file and verify each line
            log_file = Path(tmpdir) / "session_log.jsonl"
            raw_lines = log_file.read_text().strip().split("\n")
            
            for i, line in enumerate(raw_lines):
                try:
                    json.loads(line)
                except json.JSONDecodeError as e:
                    pytest.fail(
                        f"Line {i+1} is invalid JSON: {e}\n"
                        f"Content: {line[:200]}"
                    )


# ── Scenario 15: File change and shell exec enriched data ────────────

class TestScenarioEnrichedToolData:
    """
    Simulate: Tool events with enriched data (file paths, shell commands).
    """
    
    def test_file_change_data_logged(self):
        """Bug: file_path and change_type not captured in structured log."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            stack.emit(AgentEvent.TOOL_SUCCEEDED,
                       tool_name="write_file",
                       duration_ms=30,
                       result_preview="File written",
                       result_length=12,
                       file_path="/src/new_module.py",
                       change_type="create")
            
            events = stack.log_events
            tool_events = [e for e in events if e["event_type"] == "tool_call"]
            assert len(tool_events) >= 1
            
            data = tool_events[0]["data"]
            assert data.get("file_path") == "/src/new_module.py", (
                "file_path not captured in structured log"
            )
            assert data.get("change_type") == "create", (
                "change_type not captured in structured log"
            )
            
            stack.close()
    
    def test_shell_command_data_logged(self):
        """Bug: shell command and exit_code not captured in structured log."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stack = make_stack(tmpdir)
            
            stack.emit(AgentEvent.TOOL_SUCCEEDED,
                       tool_name="bash",
                       duration_ms=5000,
                       result_preview="All tests passed",
                       result_length=20,
                       command="python -m pytest tests/",
                       exit_code=0)
            
            events = stack.log_events
            tool_events = [e for e in events if e["event_type"] == "tool_call"]
            assert len(tool_events) >= 1
            
            data = tool_events[0]["data"]
            assert data.get("command") == "python -m pytest tests/", (
                "Shell command not captured in structured log"
            )
            assert data.get("exit_code") == 0, (
                "Exit code not captured in structured log"
            )
            
            stack.close()
