"""Runtime data models for Agent execution loop."""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any
import uuid

import src.core.tool as _tool_module

logger = logging.getLogger(__name__)


class AgentState(Enum):
    IDLE = "idle"
    ASSEMBLING_CONTEXT = "assembling_context"
    WAITING_FOR_LLM = "waiting_for_llm"
    EXECUTING_TOOLS = "executing_tools"
    COMPACTING = "compacting"
    COMPLETED = "completed"
    ERROR = "error"


class ToolExecutionState(Enum):
    PENDING = "pending"
    VALIDATING = "validating"
    EXECUTING = "executing"
    SUCCESS = "success"
    ERROR = "error"


@dataclass
class ToolCallRecord:
    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    state: ToolExecutionState = ToolExecutionState.PENDING
    result: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: float = 0.0
    output_truncated: bool = False
    retry_count: int = 0
    max_retries: int = 2
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    timeout_seconds: int = 0  # 0 means use GLOBAL_DEFAULT_TIMEOUT at runtime

    def get_effective_timeout(self) -> int:
        """Return effective timeout, falling back to global default."""
        return self.timeout_seconds if self.timeout_seconds > 0 else _tool_module.GLOBAL_DEFAULT_TIMEOUT

    def mark_started(self):
        """Mark the tool call as started."""
        self.started_at = datetime.now()
        self.state = ToolExecutionState.EXECUTING

    def mark_success(self, result: str):
        """Mark the tool call as successful."""
        self.completed_at = datetime.now()
        self.state = ToolExecutionState.SUCCESS
        self.result = result
        if self.started_at:
            self.duration_ms = (self.completed_at - self.started_at).total_seconds() * 1000

    def mark_error(self, error: str):
        """Mark the tool call as failed."""
        self.completed_at = datetime.now()
        self.state = ToolExecutionState.ERROR
        self.error_message = error
        if self.started_at:
            self.duration_ms = (self.completed_at - self.started_at).total_seconds() * 1000

    def can_retry(self) -> bool:
        """Check if the tool call can be retried."""
        return self.retry_count < self.max_retries and self.state == ToolExecutionState.ERROR


@dataclass
class AgentConfig:
    max_iterations: int = 50
    max_parallel_tools: int = 5
    default_tool_timeout: int = 0  # 0 means use GLOBAL_DEFAULT_TIMEOUT
    loop_detection_window: int = 6
    loop_similarity_threshold: float = 0.85
