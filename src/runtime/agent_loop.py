"""Main Agent execution loop - unified implementation."""

import json
import logging
import time
from typing import Optional, List, Dict, Any

from src.core.models import Message, Role
from src.core.events import EventBus, AgentEvent
from src.core.session_manager import get_session_manager as get_legacy_session_manager
from src.session.session_manager import SessionManager
from src.context.context_assembler import ContextAssembler
from src.engine.base import BaseEngine

from .models import AgentState, ToolCallRecord, AgentConfig
from .tool_scheduler import ToolScheduler
from .loop_detector import LoopDetector

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
        event_bus: Optional[EventBus] = None
    ):
        self.engine = engine
        self.session_manager = session_manager
        self.context_assembler = context_assembler
        self.tool_scheduler = tool_scheduler
        self.loop_detector = loop_detector
        self.config = config
        self.event_bus = event_bus
        
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
                
                # 2. Call LLM
                self._transition(AgentState.WAITING_FOR_LLM)
                self._emit(AgentEvent.LLM_CALL_STARTED, iteration=iteration)
                llm_response = await self._call_llm(messages, iteration, step_start_time)
                self._emit(AgentEvent.LLM_CALL_COMPLETED, iteration=iteration)
                
                # 3. Handle tool calls or return result
                if llm_response.get("tool_calls"):
                    self._transition(AgentState.EXECUTING_TOOLS)
                    
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
                    
                    # Execute tools
                    await self.tool_scheduler.schedule(tool_records)
                    
                    # Emit tool events
                    for record in tool_records:
                        if record.state.value == "success":
                            self._emit(AgentEvent.TOOL_SUCCEEDED, tool_name=record.tool_name, duration_ms=record.duration_ms)
                        elif record.state.value == "error":
                            self._emit(AgentEvent.TOOL_FAILED, tool_name=record.tool_name, error=record.error_message)
                    
                    # Check for errors
                    errors = [r for r in tool_records if r.state.value == "error"]
                    if errors:
                        self._consecutive_errors += 1
                        if self._consecutive_errors >= 3:
                            raise RuntimeError(f"Too many errors: {errors[0].error_message}")
                    else:
                        self._consecutive_errors = 0
                    
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
                    
                    # Loop detection
                    self.loop_detector.record(tool_records)
                    is_looping, warning = self.loop_detector.detect()
                    if is_looping:
                        self._emit(AgentEvent.LOOP_DETECTED, warning=warning)
                        turn_messages.append(Message.system(warning))
                    
                    continue
                
                else:
                    # Final response
                    final_response = llm_response.get("content", "")
                    break
            
            else:
                final_response = "Maximum iterations reached."
                logger.warning(final_response)
            
            # Record final turn in session
            self._record_turn(user_input, final_response)
            
            self._transition(AgentState.COMPLETED)
            return final_response
            
        except Exception as e:
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
        response = self.engine.call_llm(
            messages=messages,
            tools=self.tool_scheduler.tool_registry.get_all_tools()
        )
        duration_ms = (time.time() - call_start) * 1000
        
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
        
        # Log to session_llm.md via legacy session manager
        self._log_to_session_llm(messages, tool_calls, response, iteration, duration_ms)
        
        # Build detailed log message
        logger.info("")
        tool_call_info = f"Tool Calls ({len(tool_names)}): {', '.join(tool_names)}" if tool_names else "Tool Calls: None"
        logger.info(f"[AgentLoop] Step {iteration} Complete | Duration: {self._format_duration(step_duration)} | Total: {self._format_duration(total_runtime)} | Tokens: {input_tokens:,}→{output_tokens:,}({input_tokens + output_tokens:,}) | {tool_call_info}")
        logger.info("")
        
        return response
    
    def _log_to_session_llm(
        self,
        messages: List[Message],
        tool_calls: List[Dict[str, Any]],
        response: Dict[str, Any],
        iteration: int,
        duration_ms: float,
    ):
        """Log LLM interaction to session_llm.md via legacy session manager.

        The legacy ``SessionManager`` (``src.core.session_manager``) owns the
        ``session_llm.md`` file.  This method bridges the new ``AgentLoop``
        architecture to that logging facility so that every LLM call is
        recorded with its input messages, tool list, and response.
        """
        try:
            legacy_sm = get_legacy_session_manager()
            if not legacy_sm:
                return

            # Convert Message objects to dicts for JSON serialization
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

            # Convert tool_calls to tool-name list for the tools param
            tools_available = []
            all_tools = self.tool_scheduler.tool_registry.get_all_tools()
            if all_tools:
                for name in all_tools:
                    tools_available.append({"name": name})

            # Build a response dict compatible with legacy log format
            # Legacy expects: usage, stop_reason, content (list of blocks)
            usage = response.get("usage", {})
            content_text = response.get("content", "")
            content_blocks = [{"type": "text", "text": content_text}]
            if tool_calls:
                for tc in tool_calls:
                    func_info = tc.get("function", {})
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": func_info.get("name", ""),
                        "input": func_info.get("arguments", "{}"),
                    })

            legacy_response = {
                "usage": {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                },
                "stop_reason": response.get("finish_reason", "unknown"),
                "content": content_blocks,
            }

            legacy_sm.log_llm_interaction(
                iteration=iteration,
                messages=msg_dicts,
                tools=tools_available,
                response=legacy_response,
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.debug(f"Failed to log LLM interaction to session_llm.md: {e}")
    
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
        
        self.session_manager.record_turn(
            role="user", content=user_input, token_count=0
        )
        self.session_manager.record_turn(
            role="assistant", content=final_response, token_count=0
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
            self.session_manager.record_turn(
                role=role,
                content=content or "",
                token_count=0,
                tool_calls=tool_calls,
                tool_call_id=tool_call_id,
            )
        except Exception as e:
            logger.debug(f"Failed to record intermediate turn: {e}")
