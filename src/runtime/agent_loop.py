"""Main Agent execution loop - unified implementation."""

import json
import logging
import time
from typing import Optional, List, Dict, Any

from src.core.models import Message, Role
from src.core.events import EventBus, AgentEvent
from src.session.session_manager import SessionManager
from src.session.session_logger import SessionLogger
from src.session.diff_tracker import DiffTracker, ChangeType
from src.context.context_assembler import ContextAssembler
from src.engine.base import BaseEngine

from .models import AgentState, ToolCallRecord, AgentConfig
from .tool_scheduler import ToolScheduler
from .loop_detector import LoopDetector

from src.util.display import ConsoleDisplay
from src.util.activity_stream import ActivityStream
from src.util.logger import truncate_for_log

logger = logging.getLogger(__name__)


class AgentLoop:
    """
    Unified Agent execution loop.
    
    Orchestrates context assembly, LLM calls, tool execution,
    loop detection, and state management.
    """
    
    def __init__(
        self,
        engine: BaseEngine,
        session_manager: SessionManager,
        context_assembler: ContextAssembler,
        tool_scheduler: ToolScheduler,
        loop_detector: LoopDetector,
        config: AgentConfig,
        event_bus: Optional[EventBus] = None,
        display: Optional[ConsoleDisplay] = None,
        activity_stream: Optional[ActivityStream] = None,
    ):
        self.engine = engine
        self.session_manager = session_manager
        self.context_assembler = context_assembler
        self.tool_scheduler = tool_scheduler
        self.loop_detector = loop_detector
        self.config = config
        self.event_bus = event_bus
        self.display = display
        self.activity_stream = activity_stream
        self.session_logger: Optional[SessionLogger] = None
        self.diff_tracker: Optional[DiffTracker] = None
        
        self._state = AgentState.IDLE
        self._consecutive_errors = 0
        self._loop_start_time: Optional[float] = None
    
    @property
    def state(self) -> AgentState:
        return self._state
    
    def _emit(self, event_type: AgentEvent, **kwargs):
        """Emit an event safely via EventBus."""
        if not self.event_bus:
            return
        try:
            session_id = None
            if self.session_manager.active_session:
                session_id = self.session_manager.active_session.id
            self.event_bus.emit(event_type, session_id=session_id, **kwargs)
        except Exception as e:
            logger.debug(f"EventBus emit error: {e}")

    def _transition(self, new_state: AgentState, reason: str = ""):
        """Transition to new state."""
        old_state = self._state
        self._state = new_state
        logger.debug(f"State: {old_state.value} -> {new_state.value} ({reason})")
        self._emit(AgentEvent.STATE_CHANGED, old_state=old_state.value, new_state=new_state.value, reason=reason)
    
    async def run(self, user_input: str) -> str:
        """
        Execute one complete turn.
        
        Architecture:
          - First iteration: ``context_assembler.build()`` constructs the full
            8-layer context.  The static prefix (system prompt, CLAUDE.md, etc.)
            is cached internally by the assembler.
          - Subsequent iterations: only the *turn messages* (user message,
            assistant responses, tool results accumulated during this turn) are
            tracked.  ``context_assembler.rebuild(turn_messages)`` prepends the
            cached static prefix and applies compression when the token budget
            is exceeded.
        
        This eliminates the previous redundancy where the static prefix was
        duplicated inside the growing ``messages`` list on every iteration.
        """
        iteration = 0
        # turn_messages holds only the dynamic messages for the current turn:
        #   [user_msg, assistant_msg, tool_result, assistant_msg, tool_result, ...]
        # The static prefix (system prompt, CLAUDE.md, etc.) is managed by
        # context_assembler and prepended via rebuild().
        turn_messages: List[Message] = []
        self._loop_start_time = time.time()
        
        # Log user query to session logger (full prompt, not truncated)
        self._log_user_query(user_input)
        
        # DEBUG: Loop started
        all_tools = self.tool_scheduler.tool_registry.get_all_tools()
        tool_count = len(all_tools) if all_tools else 0
        system_prompt_length = 0
        if hasattr(self.context_assembler, 'system_prompt'):
            system_prompt_length = len(self.context_assembler.system_prompt or '')
        session_id = self.session_manager.active_session.id if self.session_manager.active_session else 'N/A'
        logger.debug(
            "[AgentLoop] Loop started | session_id=%s | max_iterations=%d | tool_count=%d | system_prompt_length=%d",
            session_id[:8] if session_id != 'N/A' else session_id,
            self.config.max_iterations, tool_count, system_prompt_length
        )
        
        try:
            while iteration < self.config.max_iterations:
                iteration += 1
                step_start_time = time.time()
                total_runtime = step_start_time - self._loop_start_time
                
                # Log step header with separator
                logger.info("")
                logger.info("=" * 70)
                logger.info(f"[AgentLoop] STEP {iteration}/{self.config.max_iterations} | Total Runtime: {self._format_duration(total_runtime)}")
                logger.info("=" * 70)
                
                # DEBUG: Iteration started
                input_tokens_est = self.context_assembler.window.estimate_tokens(turn_messages) if turn_messages else 0
                logger.debug(
                    "[AgentLoop] Iteration #%d started | elapsed=%.1fs | message_count=%d | estimated_tokens=%d",
                    iteration, total_runtime, len(turn_messages), input_tokens_est
                )
                
                # 1. Assemble context
                self._transition(AgentState.ASSEMBLING_CONTEXT, f"iteration {iteration}")
                if iteration == 1:
                    # First iteration: full 8-layer build
                    messages = self.context_assembler.build(user_input)
                    # Extract turn_messages (non-static, non-cached messages)
                    # These are the messages that will grow during the turn
                    turn_messages = [
                        m for m in messages
                        if not getattr(m, 'cache_control', None)
                    ]
                else:
                    # Subsequent iterations: rebuild with cached static prefix
                    # + accumulated turn messages (which now include new
                    # assistant/tool messages from previous iteration)
                    messages = self.context_assembler.rebuild(turn_messages)
                
                # DEBUG: Context assembled
                logger.debug(
                    "[AgentLoop] Context assembled | message_count=%d | estimated_tokens=%d",
                    len(messages), self.context_assembler.window.estimate_tokens(messages)
                )
                
                # 2. Call LLM
                self._transition(AgentState.WAITING_FOR_LLM)
                
                # Estimate input tokens for display
                input_tokens_estimate = self.context_assembler.window.estimate_tokens(messages)
                self._emit(AgentEvent.LLM_CALL_STARTED, iteration=iteration, message_count=len(messages), input_tokens=input_tokens_estimate)
                
                llm_response = await self._call_llm(messages, iteration, step_start_time)
                
                # 3. Handle API errors, tool calls, or return result
                if llm_response.get("finish_reason") == "error":
                    # API error (e.g. 504 timeout, 400 bad request) —
                    # do NOT treat as final response.  Retry up to 2 times
                    # with the same context, then give up gracefully.
                    error_content = llm_response.get("content", "Unknown API error")
                    self._consecutive_errors += 1
                    if self._consecutive_errors < 3:
                        logger.warning(
                            "[AgentLoop] LLM API error (attempt %d/3): %s — retrying",
                            self._consecutive_errors, error_content,
                        )
                        # Small backoff before retry
                        import asyncio
                        await asyncio.sleep(min(2 ** self._consecutive_errors, 10))
                        continue
                    else:
                        logger.error(
                            "[AgentLoop] LLM API error persists after 3 attempts: %s",
                            error_content,
                        )
                        final_response = (
                            f"I encountered a persistent API error and could not complete "
                            f"the request. Error: {error_content}"
                        )
                        break

                if llm_response.get("tool_calls"):
                    self._consecutive_errors = 0
                    self._transition(AgentState.EXECUTING_TOOLS)
                    
                    # DEBUG: Log each tool call
                    for idx, tc in enumerate(llm_response["tool_calls"]):
                        func_info_dbg = tc.get("function", {})
                        tc_name = func_info_dbg.get("name", "")
                        tc_args = func_info_dbg.get("arguments", "{}")
                        tc_args_str = tc_args if isinstance(tc_args, str) else json.dumps(tc_args)
                        logger.debug(
                            "[AgentLoop] Tool call #%d | name=%s | arguments_length=%d | arguments_preview=\"%s\"",
                            idx + 1, tc_name, len(tc_args_str), truncate_for_log(tc_args_str)
                        )
                    
                    # Parse tool calls from LLM response
                    tool_records = []
                    for tc in llm_response["tool_calls"]:
                        func_info = tc.get("function", {})
                        raw_args = func_info.get("arguments", "{}")
                        parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        tool_records.append(ToolCallRecord(
                            id=tc.get("id", ""),
                            tool_name=func_info.get("name", ""),
                            arguments=parsed_args
                        ))
                    
                    # Append assistant message (with tool_calls) to turn_messages
                    assistant_msg = Message.assistant(
                        content=llm_response.get("content"),
                        tool_calls=llm_response["tool_calls"]
                    )
                    turn_messages.append(assistant_msg)
                    
                    # Record assistant turn in session for persistence
                    self._record_intermediate_turn(
                        role="assistant",
                        content=llm_response.get("content", ""),
                        tool_calls=llm_response["tool_calls"],
                    )
                    
                    # Emit TOOL_CALLED events before execution
                    for record in tool_records:
                        self._emit(
                            AgentEvent.TOOL_CALLED,
                            tool_name=record.tool_name,
                            arguments=record.arguments,
                        )
                    
                    # Mark parallel batch if multiple tools
                    if len(tool_records) > 1 and self.activity_stream:
                        self.activity_stream.parallel_batch(len(tool_records))
                    
                    # Execute tools
                    await self.tool_scheduler.schedule(tool_records)
                    
                    # Emit tool events with enriched data payloads
                    for record in tool_records:
                        if record.state.value == "success":
                            result_text = record.result or ""
                            self._emit(
                                AgentEvent.TOOL_SUCCEEDED,
                                tool_name=record.tool_name,
                                arguments=record.arguments,
                                duration_ms=record.duration_ms,
                                result_preview=result_text[:1500],
                                result_length=len(result_text),
                            )
                        elif record.state.value == "error":
                            self._emit(
                                AgentEvent.TOOL_FAILED,
                                tool_name=record.tool_name,
                                arguments=record.arguments,
                                duration_ms=record.duration_ms,
                                error=record.error_message or "Unknown error",
                            )
                    
                    # Check for errors
                    errors = [r for r in tool_records if r.state.value == "error"]
                    if errors:
                        self._consecutive_errors += 1
                        if self._consecutive_errors >= 3:
                            raise RuntimeError(f"Too many errors: {errors[0].error_message}")
                    else:
                        self._consecutive_errors = 0
                    
                    # DEBUG: Log tool results
                    for idx, record in enumerate(tool_records):
                        result_text = record.result or record.error_message or ""
                        status = record.state.value
                        logger.debug(
                            "[AgentLoop] Tool result #%d | name=%s | status=%s | duration=%.0fms | output_length=%d | output_preview=\"%s\"",
                            idx + 1, record.tool_name, status,
                            record.duration_ms or 0, len(result_text),
                            truncate_for_log(result_text)
                        )
                    
                    # Add tool results to turn_messages and record in session
                    for record in tool_records:
                        tool_content = record.result or record.error_message or ""
                        tool_msg = Message.tool(
                            content=tool_content,
                            tool_call_id=record.id,
                            name=record.tool_name
                        )
                        turn_messages.append(tool_msg)
                        
                        self._record_intermediate_turn(
                            role="tool",
                            content=tool_content,
                            tool_call_id=record.id,
                        )
                    
                    # Log tool results to session_llm.md
                    self._log_tool_results(iteration, tool_records)
                    
                    # Record tool execution as activity steps
                    self._log_tool_activity(tool_records)
                    
                    # Loop detection
                    self.loop_detector.record(tool_records)
                    is_looping, warning = self.loop_detector.detect()
                    if is_looping:
                        self._emit(AgentEvent.LOOP_DETECTED, warning=warning)
                        turn_messages.append(Message.system(warning))
                    
                    continue
                
                else:
                    # Final response — reset error counter
                    self._consecutive_errors = 0
                    final_response = llm_response.get("content", "")
                    break
            
            else:
                final_response = "Maximum iterations reached."
                logger.warning(final_response)
            
            # Record final turn in session
            self._record_turn(user_input, final_response)
            
            # DEBUG: Loop completed
            total_duration = time.time() - self._loop_start_time
            logger.debug(
                "[AgentLoop] Loop completed | total_iterations=%d | total_duration=%.1fs | final_reason=%s",
                iteration, total_duration,
                "max_iterations" if iteration >= self.config.max_iterations else "completed"
            )
            
            self._transition(AgentState.COMPLETED)
            return final_response
            
        except Exception as e:
            logger.error(
                "[AgentLoop] LLM call failed | iteration=%d | error=%s | will_retry=False",
                iteration, str(e)
            )
            logger.exception("Agent loop failed")
            self._emit(AgentEvent.LLM_CALL_FAILED, error=str(e))
            self._transition(AgentState.ERROR, str(e))
            raise
    
    def run_sync(self, user_input: str) -> str:
        """Synchronous wrapper."""
        import asyncio
        return asyncio.run(self.run(user_input))
    
    async def _call_llm(self, messages: List[Message], iteration: int, step_start_time: float) -> Dict[str, Any]:
        """Call LLM and return response with detailed logging."""
        step_elapsed = time.time() - step_start_time
        total_elapsed = time.time() - self._loop_start_time
        
        call_start = time.time()
        
        # DEBUG: LLM request
        all_tools_for_call = self.tool_scheduler.tool_registry.get_all_tools()
        logger.debug(
            "[AgentLoop] LLM request | model=%s | message_count=%d | has_tools=%s | temperature=%s | max_tokens=%s",
            getattr(self.engine, 'model', 'unknown'), len(messages),
            bool(all_tools_for_call), getattr(self.engine, 'temperature', 'N/A'),
            getattr(self.engine, 'max_tokens', 'N/A')
        )
        
        response = self.engine.call_llm(
            messages=messages,
            tools=self.tool_scheduler.tool_registry.get_all_tools()
        )
        duration_ms = (time.time() - call_start) * 1000
        duration_s = duration_ms / 1000.0
        
        # Calculate timing
        step_duration = time.time() - step_start_time
        total_runtime = time.time() - self._loop_start_time
        
        # Extract usage info
        usage = response.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        
        # Extract tool calls
        tool_calls = response.get("tool_calls") or []
        tool_names = []
        for tc in tool_calls:
            func_info = tc.get("function", {})
            name = func_info.get("name", "")
            if name:
                tool_names.append(name)
        
        # Extract content preview for activity stream (enough for multi-line display)
        content_text = response.get("content", "")
        content_preview = content_text[:2000] if content_text else ""
        
        # DEBUG: LLM response
        logger.debug(
            "[AgentLoop] LLM response | input_tokens=%d | output_tokens=%d | duration=%.1fs | finish_reason=%s | has_content=%s | tool_call_count=%d",
            input_tokens, output_tokens, duration_s,
            response.get("finish_reason", "unknown"),
            bool(content_text), len(tool_calls)
        )
        
        # DEBUG: LLM content preview
        if content_text:
            logger.debug(
                "[AgentLoop] LLM content preview | length=%d | first_500_chars=\"%s\"",
                len(content_text), truncate_for_log(content_text)
            )
        
        # Emit LLM_CALL_COMPLETED with enriched data
        self._emit(
            AgentEvent.LLM_CALL_COMPLETED,
            iteration=iteration,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_s=duration_s,
            tool_call_count=len(tool_calls),
            content_preview=content_preview,
            stop_reason=response.get("finish_reason", "unknown"),
        )
        
        # Log to session logger (replaces legacy session_llm.md logging)
        self._log_to_session_logger(messages, tool_calls, response, iteration, duration_ms)
        
        # Build detailed log message
        logger.info("")
        tool_call_info = f"Tool Calls ({len(tool_names)}): {', '.join(tool_names)}" if tool_names else "Tool Calls: None"
        logger.info(f"[AgentLoop] Step {iteration} Complete | Duration: {self._format_duration(step_duration)} | Total: {self._format_duration(total_runtime)} | Tokens: {input_tokens:,}→{output_tokens:,}({input_tokens + output_tokens:,}) | {tool_call_info}")
        logger.info("")
        
        return response
    
    def _log_to_session_logger(
        self,
        messages: List[Message],
        tool_calls: List[Dict[str, Any]],
        response: Dict[str, Any],
        iteration: int,
        duration_ms: float,
    ):
        """Log LLM interaction via the unified SessionLogger."""
        sl = self.session_logger or self.session_manager.get_session_logger()
        if not sl:
            return
        try:
            msg_dicts = []
            for m in messages:
                d: Dict[str, Any] = {"role": m.role}
                if m.content:
                    d["content"] = m.content
                if m.tool_calls:
                    d["tool_calls"] = m.tool_calls
                if m.tool_call_id:
                    d["tool_call_id"] = m.tool_call_id
                if m.name:
                    d["name"] = m.name
                msg_dicts.append(d)

            tools_available = []
            all_tools = self.tool_scheduler.tool_registry.get_all_tools()
            if all_tools:
                for name in all_tools:
                    tools_available.append({"name": name})

            usage = response.get("usage", {})
            sl.log_llm_interaction(
                iteration=iteration,
                messages=msg_dicts,
                tools=tools_available,
                response=response,
                usage=usage,
                duration_ms=duration_ms,
                stop_reason=response.get("finish_reason", "unknown"),
            )
        except Exception as e:
            logger.debug("Failed to log LLM interaction: %s", e)
    
    def _log_user_query(self, user_input: str):
        """Log the full user query via the unified SessionLogger."""
        sl = self.session_logger or self.session_manager.get_session_logger()
        if not sl:
            return
        try:
            sl.log_user_query(user_input)
        except Exception as e:
            logger.debug("Failed to log user query: %s", e)

    def _log_tool_activity(self, tool_records: list):
        """Record tool executions as activity steps in the session logger."""
        sl = self.session_logger or self.session_manager.get_session_logger()
        if not sl:
            return
        try:
            for record in tool_records:
                sl.record_activity(f"Executed tool: {record.tool_name}")
        except Exception as e:
            logger.debug("Failed to log tool activity: %s", e)

    def _log_tool_results(self, iteration: int, tool_records: list):
        """Log tool execution results via the unified SessionLogger."""
        sl = self.session_logger or self.session_manager.get_session_logger()
        if not sl:
            return
        try:
            results = []
            for record in tool_records:
                results.append({
                    "tool_name": record.tool_name,
                    "tool_call_id": record.id,
                    "arguments": record.arguments,
                    "result": record.result or "",
                    "duration_ms": record.duration_ms or 0,
                    "success": record.state.value == "success",
                    "error": record.error_message or "",
                })
            sl.log_tool_results(iteration=iteration, tool_results=results)
        except Exception as e:
            logger.debug("Failed to log tool results: %s", e)

    def _format_duration(self, seconds: float) -> str:
        """Format duration in human-readable form."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m{secs:02d}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h{minutes:02d}m"
    
    def _record_turn(self, user_input: str, final_response: str):
        """Record the user input and final assistant response in session history."""
        session = self.session_manager.active_session
        if not session:
            return
        
        # Estimate token counts for better tracking
        user_tokens = self._estimate_token_count(user_input)
        assistant_tokens = self._estimate_token_count(final_response)
        
        self.session_manager.record_turn(
            role="user", content=user_input, token_count=user_tokens
        )
        self.session_manager.record_turn(
            role="assistant", content=final_response, token_count=assistant_tokens
        )

    def _record_intermediate_turn(
        self,
        role: str,
        content: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        tool_call_id: Optional[str] = None,
    ):
        """Record intermediate assistant/tool messages during tool-use iterations.

        This ensures the session history captures the full conversation flow
        (assistant with tool_calls, tool results) so that context can be
        properly reconstructed on session resume.
        """
        session = self.session_manager.active_session
        if not session:
            return
        try:
            token_count = self._estimate_token_count(content or "", tool_calls)
            self.session_manager.record_turn(
                role=role,
                content=content or "",
                token_count=token_count,
                tool_calls=tool_calls,
                tool_call_id=tool_call_id,
            )
        except Exception as e:
            logger.debug(f"Failed to record intermediate turn: {e}")

    def _estimate_token_count(
        self,
        content: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """Estimate token count for a message content + optional tool_calls."""
        import math
        chars_per_token = 3.5
        msg_overhead = 4
        tokens = msg_overhead + math.ceil(len(content) / chars_per_token)
        if tool_calls:
            try:
                tc_json = json.dumps(tool_calls)
                tokens += math.ceil(len(tc_json) / chars_per_token)
            except (TypeError, ValueError):
                pass
        return tokens
