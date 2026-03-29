"""
EventBus Log Integration Layer for Agent X1.

Bridges the EventBus event system with the logging/display subsystems.
Subscribes to relevant events and dispatches them to:
- ActivityStream (real-time user display)
- StructuredLogger (persistent JSONL storage)
- TokenTracker (cumulative statistics)
- ConsoleDisplay (status updates)

Usage:
    from src.util.log_integration import LogIntegration
    
    integration = LogIntegration(
        display=display,
        activity_stream=stream,
        structured_logger=slog,
        token_tracker=tracker,
    )
    integration.setup(event_bus)
"""

import logging
from typing import Optional

from src.core.events import AgentEvent, EventBus, EventPayload
from src.util.activity_stream import ActivityStream
from src.util.display import ConsoleDisplay
from src.util.structured_log import LogEventType, StructuredLogger
from src.util.token_tracker import TokenTracker


logger = logging.getLogger(__name__)


class LogIntegration:
    """
    Bridge between EventBus and the logging/display subsystems.
    
    Subscribes to EventBus events and dispatches them to the
    appropriate logging and display components.
    
    Args:
        display: ConsoleDisplay for user-facing output
        activity_stream: ActivityStream for real-time activity display
        structured_logger: StructuredLogger for persistent JSONL storage (optional)
        token_tracker: TokenTracker for cumulative statistics (optional)
    """
    
    def __init__(
        self,
        display: ConsoleDisplay,
        activity_stream: ActivityStream,
        structured_logger: Optional[StructuredLogger] = None,
        token_tracker: Optional[TokenTracker] = None,
    ):
        self._display = display
        self._stream = activity_stream
        self._slog = structured_logger
        self._tracker = token_tracker
    
    def setup(self, event_bus: EventBus) -> None:
        """
        Register all event handlers on the given EventBus.
        
        This is the single entry point to wire up all logging integrations.
        
        Args:
            event_bus: The EventBus instance to subscribe to
        """
        # LLM events
        event_bus.subscribe(AgentEvent.LLM_CALL_STARTED, self._on_llm_call_started)
        event_bus.subscribe(AgentEvent.LLM_CALL_COMPLETED, self._on_llm_call_completed)
        event_bus.subscribe(AgentEvent.LLM_CALL_FAILED, self._on_llm_call_failed)
        
        # Tool events
        event_bus.subscribe(AgentEvent.TOOL_CALLED, self._on_tool_called)
        event_bus.subscribe(AgentEvent.TOOL_SUCCEEDED, self._on_tool_succeeded)
        event_bus.subscribe(AgentEvent.TOOL_FAILED, self._on_tool_failed)
        
        # Session events
        event_bus.subscribe(AgentEvent.SESSION_COMPLETED, self._on_session_completed)
        event_bus.subscribe(AgentEvent.SESSION_FAILED, self._on_session_failed)
        
        # Loop events
        event_bus.subscribe(AgentEvent.LOOP_DETECTED, self._on_loop_detected)
        event_bus.subscribe(AgentEvent.LOOP_ITERATION, self._on_loop_iteration)
        
        # State events
        event_bus.subscribe(AgentEvent.STATE_CHANGED, self._on_state_changed)
        
        # Turn events
        event_bus.subscribe(AgentEvent.TURN_COMPLETED, self._on_turn_completed)
        
        logger.debug("LogIntegration: all event handlers registered")
    
    # ── LLM event handlers ────────────────────────────────────────────
    
    def _on_llm_call_started(self, payload: EventPayload) -> None:
        """Handle LLM call started event."""
        data = payload.data or {}
        iteration = data.get("iteration", 0)
        message_count = data.get("message_count", 0)
        input_tokens = data.get("input_tokens", 0)
        
        self._stream.llm_request(
            iteration=iteration,
            message_count=message_count,
            input_tokens=input_tokens,
        )
    
    def _on_llm_call_completed(self, payload: EventPayload) -> None:
        """Handle LLM call completed event."""
        data = payload.data or {}
        iteration = data.get("iteration", 0)
        input_tokens = data.get("input_tokens", 0)
        output_tokens = data.get("output_tokens", 0)
        duration_s = data.get("duration_s", 0.0)
        tool_call_count = data.get("tool_call_count", 0)
        content_preview = data.get("content_preview", "")
        
        # Activity stream display
        self._stream.llm_response(
            iteration=iteration,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_s=duration_s,
            tool_call_count=tool_call_count,
            content_preview=content_preview,
        )
        
        # Token tracking
        if self._tracker:
            self._tracker.record_llm_call(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_s=duration_s,
                tool_call_count=tool_call_count,
            )
        
        # Structured logging
        if self._slog:
            self._slog.log(LogEventType.LLM_CALL, {
                "iteration": iteration,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "duration_s": duration_s,
                "tool_call_count": tool_call_count,
                "stop_reason": data.get("stop_reason", ""),
            })
    
    def _on_llm_call_failed(self, payload: EventPayload) -> None:
        """Handle LLM call failed event."""
        data = payload.data or {}
        error_msg = data.get("error", "Unknown LLM error")
        iteration = data.get("iteration", 0)
        
        self._display.error(f"LLM call failed (iteration {iteration}): {error_msg}")
        
        if self._slog:
            self._slog.log(LogEventType.LLM_CALL, {
                "iteration": iteration,
                "error": error_msg,
                "success": False,
            })
    
    # ── Tool event handlers ───────────────────────────────────────────
    
    def _on_tool_called(self, payload: EventPayload) -> None:
        """Handle tool called event (before execution)."""
        data = payload.data or {}
        tool_name = data.get("tool_name", "unknown")
        arguments = data.get("arguments", {})
        
        self._stream.tool_start(tool_name=tool_name, arguments=arguments)
    
    def _on_tool_succeeded(self, payload: EventPayload) -> None:
        """Handle tool succeeded event."""
        data = payload.data or {}
        tool_name = data.get("tool_name", "unknown")
        duration_ms = data.get("duration_ms", 0.0)
        result_preview = data.get("result_preview", "")
        result_length = data.get("result_length", 0)
        
        # Activity stream display
        self._stream.tool_success(
            tool_name=tool_name,
            duration_ms=duration_ms,
            output=result_preview,
            output_length=result_length,
        )
        
        # Token tracking
        if self._tracker:
            self._tracker.record_tool_call(
                tool_name=tool_name,
                duration_ms=duration_ms,
                success=True,
            )
        
        # Structured logging
        if self._slog:
            log_data = {
                "tool_name": tool_name,
                "duration_ms": duration_ms,
                "success": True,
                "result_length": result_length,
            }
            # Add specific data for file/shell tools
            if data.get("file_path"):
                log_data["file_path"] = data["file_path"]
                log_data["change_type"] = data.get("change_type", "unknown")
            if data.get("command"):
                log_data["command"] = data["command"]
                log_data["exit_code"] = data.get("exit_code", 0)
            
            self._slog.log(LogEventType.TOOL_CALL, log_data)
    
    def _on_tool_failed(self, payload: EventPayload) -> None:
        """Handle tool failed event."""
        data = payload.data or {}
        tool_name = data.get("tool_name", "unknown")
        duration_ms = data.get("duration_ms", 0.0)
        error_message = data.get("error", "Unknown error")
        
        # Activity stream display
        self._stream.tool_failure(
            tool_name=tool_name,
            duration_ms=duration_ms,
            error_message=error_message,
        )
        
        # Token tracking
        if self._tracker:
            self._tracker.record_tool_call(
                tool_name=tool_name,
                duration_ms=duration_ms,
                success=False,
            )
        
        # Structured logging
        if self._slog:
            self._slog.log(LogEventType.TOOL_CALL, {
                "tool_name": tool_name,
                "duration_ms": duration_ms,
                "success": False,
                "error": error_message,
            })
    
    # ── Session event handlers ────────────────────────────────────────
    
    def _on_session_completed(self, payload: EventPayload) -> None:
        """Handle session completed event."""
        self._finalize_session(reason="completed")
    
    def _on_session_failed(self, payload: EventPayload) -> None:
        """Handle session failed event."""
        data = payload.data or {}
        error = data.get("error", "Unknown error")
        self._display.error(f"Session failed: {error}")
        self._finalize_session(reason=f"failed: {error}")
    
    def _finalize_session(self, reason: str = "completed") -> None:
        """Finalize session: generate summary and display stats."""
        # Get stats from tracker
        if self._tracker:
            stats = self._tracker.get_stats()
            
            # Display session summary
            self._display.session_summary(
                total_duration_s=stats["session_duration_s"],
                total_input_tokens=stats["total_input_tokens"],
                total_output_tokens=stats["total_output_tokens"],
                llm_calls=stats["llm_call_count"],
                tool_calls=stats["tool_call_count"],
            )
            
            # Generate structured log summary
            if self._slog:
                self._slog.log(LogEventType.SESSION_END, {
                    "reason": reason,
                    **stats,
                })
                self._slog.generate_session_summary(
                    total_input_tokens=stats["total_input_tokens"],
                    total_output_tokens=stats["total_output_tokens"],
                    llm_calls=stats["llm_call_count"],
                    tool_calls=stats["tool_call_count"],
                )
                self._slog.close()
        else:
            # No tracker, just log end
            if self._slog:
                self._slog.log(LogEventType.SESSION_END, {"reason": reason})
                self._slog.close()
        
        # Close display log file mirror
        self._display.close_log_file()
    
    # ── Loop event handlers ───────────────────────────────────────────
    
    def _on_loop_detected(self, payload: EventPayload) -> None:
        """Handle loop detection event."""
        data = payload.data or {}
        message = data.get("message", "Repeated pattern detected")
        self._stream.loop_detected(message)
    
    def _on_loop_iteration(self, payload: EventPayload) -> None:
        """Handle loop iteration event (step progress)."""
        data = payload.data or {}
        step = data.get("iteration", 0)
        max_steps = data.get("max_iterations", 0)
        description = data.get("description", "")
        
        self._stream.step(step_num=step, max_steps=max_steps, description=description)
    
    # ── State event handlers ──────────────────────────────────────────
    
    def _on_state_changed(self, payload: EventPayload) -> None:
        """Handle state change event."""
        data = payload.data or {}
        new_state = data.get("new_state", "")
        message = data.get("message", "")
        
        if message:
            self._display.status(f"{new_state}: {message}")
        elif new_state:
            self._display.status(new_state)
    
    # ── Turn event handlers ───────────────────────────────────────────
    
    def _on_turn_completed(self, payload: EventPayload) -> None:
        """Handle turn completed event."""
        data = payload.data or {}
        turn_number = payload.turn_number or data.get("turn_number", 0)
        tool_call_count = data.get("tool_call_count", 0)
        user_input_summary = data.get("user_input_summary", "")
        
        if self._slog:
            self._slog.log(LogEventType.TURN_COMPLETE, {
                "turn_number": turn_number,
                "tool_call_count": tool_call_count,
                "user_input_summary": user_input_summary,
            })
