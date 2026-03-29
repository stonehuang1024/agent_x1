"""Tool scheduler implementation."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional

from src.core.tool import ToolRegistry, Tool
from src.core.events import EventBus, AgentEvent
from src.util.logger import truncate_for_log, set_session_id, _thread_local
from .models import ToolCallRecord, ToolExecutionState

logger = logging.getLogger(__name__)

# Maximum output length before truncation
MAX_OUTPUT_LENGTH = 30000


class ToolScheduler:
    """Schedules and executes tool calls."""
    
    def __init__(self, tool_registry: ToolRegistry, max_parallel: int = 5, event_bus: Optional["EventBus"] = None):
        self.tool_registry = tool_registry
        self.max_parallel = max_parallel
        self.event_bus = event_bus
        self._executor = ThreadPoolExecutor(max_workers=max_parallel)
    
    def _emit(self, event_type: AgentEvent, **kwargs):
        """Emit an event safely via EventBus."""
        if not self.event_bus:
            return
        try:
            self.event_bus.emit(event_type, **kwargs)
        except Exception as e:
            logger.debug(f"EventBus emit error: {e}")

    @staticmethod
    def _execute_in_thread(tool: Tool, arguments: Dict[str, Any], session_id: Optional[str]) -> str:
        """Execute tool in worker thread with session_id propagation."""
        # Propagate session_id from main thread to this worker thread
        if session_id:
            set_session_id(session_id)
        return tool.execute(arguments)
    
    async def schedule(self, records: List[ToolCallRecord]) -> List[ToolCallRecord]:
        """Execute multiple tool calls."""
        tool_names = [r.tool_name for r in records]
        logger.debug(
            "[ToolScheduler] Scheduling | tool_count=%d | tools=%s | parallel=False",
            len(records), tool_names
        )
        for record in records:
            await self._execute_with_retry(record)
        return records
    
    async def _execute_with_retry(self, record: ToolCallRecord) -> ToolCallRecord:
        """Execute a tool call with automatic retry on failure."""
        await self.execute(record)
        
        while record.can_retry():
            record.retry_count += 1
            logger.info(
                f"Retrying tool {record.tool_name} "
                f"(attempt {record.retry_count + 1}/{record.max_retries + 1})"
            )
            # Reset state for retry
            record.state = ToolExecutionState.PENDING
            record.error_message = None
            record.started_at = None
            record.completed_at = None
            record.duration_ms = 0.0
            await self.execute(record)
        
        return record
    
    def _validate_arguments(self, tool: Tool, arguments: Dict[str, Any]) -> Optional[str]:
        """
        Validate tool arguments against schema.
        
        Returns:
            Error message if validation fails, None if valid.
        """
        schema = getattr(tool, 'schema', None) or getattr(tool, 'parameters', None)
        if not schema:
            return None
        
        required = []
        if isinstance(schema, dict):
            required = schema.get('required', [])
        
        missing = [p for p in required if p not in arguments]
        if missing:
            return f"Missing required parameters: {', '.join(missing)}"
        
        return None
    
    async def execute(self, record: ToolCallRecord) -> ToolCallRecord:
        """Execute a single tool call with timing, validation, and timeout."""
        try:
            # Validation phase
            record.state = ToolExecutionState.VALIDATING
            tool = self.tool_registry.get(record.tool_name)
            
            if not tool:
                record.mark_error(f"Tool '{record.tool_name}' not found")
                return record
            
            # Validate arguments
            validation_error = self._validate_arguments(tool, record.arguments)
            if validation_error:
                record.mark_error(validation_error)
                return record
            
            # Execution phase with timing
            record.mark_started()
            
            # DEBUG: Executing
            logger.debug(
                "[ToolScheduler] Executing | name=%s | call_id=%s | arguments_preview=\"%s\"",
                record.tool_name, record.id, truncate_for_log(str(record.arguments))
            )
            
            # Execute in thread pool with timeout
            # Capture session_id from main thread to propagate to worker thread
            current_session_id = getattr(_thread_local, 'session_id', None)
            # Priority: ToolCallRecord.timeout_seconds > Tool.timeout_seconds > GLOBAL_DEFAULT_TIMEOUT
            effective_timeout = record.timeout_seconds if record.timeout_seconds > 0 else tool.get_effective_timeout()
            loop = asyncio.get_event_loop()
            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        self._executor,
                        self._execute_in_thread,
                        tool, record.arguments, current_session_id
                    ),
                    timeout=effective_timeout
                )
            except asyncio.TimeoutError:
                record.mark_error(
                    f"Tool '{record.tool_name}' timed out after {effective_timeout}s"
                )
                return record
            
            # Truncate if too long
            if len(result) > MAX_OUTPUT_LENGTH:
                result = result[:MAX_OUTPUT_LENGTH] + "\n... [truncated]"
                record.output_truncated = True
            
            record.mark_success(result)
            
            # DEBUG: Completed
            logger.debug(
                "[ToolScheduler] Completed | name=%s | call_id=%s | status=success | duration=%.0fms | output_length=%d | output_preview=\"%s\"",
                record.tool_name, record.id, record.duration_ms,
                len(result), truncate_for_log(result)
            )
            
            logger.info(
                f"Tool {record.tool_name} completed in {record.duration_ms:.0f}ms"
            )
            # Note: TOOL_SUCCEEDED/TOOL_FAILED events are emitted by AgentLoop
            # with enriched data payloads (arguments, result_preview, etc.)
            
        except Exception as e:
            logger.error(
                "[ToolScheduler] Failed | name=%s | call_id=%s | error=%s | duration=%.0fms",
                record.tool_name, record.id, str(e), record.duration_ms or 0
            )
            logger.exception(f"Tool execution failed: {e}")
            record.mark_error(str(e))
        
        return record


class ParallelToolScheduler(ToolScheduler):
    """Tool scheduler with parallel execution."""
    
    async def schedule(self, records: List[ToolCallRecord]) -> List[ToolCallRecord]:
        """Execute tools in parallel."""
        semaphore = asyncio.Semaphore(self.max_parallel)
        
        async def bounded_execute(record):
            async with semaphore:
                return await self.execute(record)
        
        tasks = [bounded_execute(r) for r in records]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                records[i].mark_error(str(result))
        
        return records
