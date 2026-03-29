"""
Token Consumption Tracker for Agent X1.

Maintains session-level cumulative statistics for LLM token usage,
API call counts, and tool call counts.

Usage:
    from src.util.token_tracker import TokenTracker
    
    tracker = TokenTracker()
    tracker.record_llm_call(input_tokens=1234, output_tokens=567, duration_s=2.3)
    tracker.record_tool_call(tool_name="read_file", duration_ms=120, success=True)
    
    stats = tracker.get_stats()
    print(f"Total tokens: {stats['total_tokens']:,}")
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class LLMCallRecord:
    """Record of a single LLM API call."""
    input_tokens: int
    output_tokens: int
    duration_s: float
    timestamp: float
    tool_call_count: int = 0


@dataclass
class ToolCallRecord:
    """Record of a single tool call."""
    tool_name: str
    duration_ms: float
    success: bool
    timestamp: float


class TokenTracker:
    """
    Session-level token consumption and call statistics tracker.
    
    Tracks:
    - Per-call and cumulative LLM token usage (input/output)
    - LLM API call count and durations
    - Tool call count and durations
    - Session total elapsed time
    
    Thread-safety: This class is NOT thread-safe. If used from
    multiple threads, external synchronization is required.
    """
    
    def __init__(self):
        self._session_start = time.time()
        
        # Cumulative counters
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._llm_call_count: int = 0
        self._tool_call_count: int = 0
        self._tool_success_count: int = 0
        self._tool_failure_count: int = 0
        
        # Detailed records (kept for potential analysis)
        self._llm_calls: List[LLMCallRecord] = []
        self._tool_calls: List[ToolCallRecord] = []
    
    def record_llm_call(
        self,
        input_tokens: int,
        output_tokens: int,
        duration_s: float,
        tool_call_count: int = 0,
    ) -> None:
        """
        Record a completed LLM API call.
        
        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            duration_s: Call duration in seconds
            tool_call_count: Number of tool calls in the response
        """
        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens
        self._llm_call_count += 1
        
        self._llm_calls.append(LLMCallRecord(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_s=duration_s,
            timestamp=time.time(),
            tool_call_count=tool_call_count,
        ))
    
    def record_tool_call(
        self,
        tool_name: str,
        duration_ms: float,
        success: bool = True,
    ) -> None:
        """
        Record a completed tool call.
        
        Args:
            tool_name: Name of the tool
            duration_ms: Execution time in milliseconds
            success: Whether the tool call succeeded
        """
        self._tool_call_count += 1
        if success:
            self._tool_success_count += 1
        else:
            self._tool_failure_count += 1
        
        self._tool_calls.append(ToolCallRecord(
            tool_name=tool_name,
            duration_ms=duration_ms,
            success=success,
            timestamp=time.time(),
        ))
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get current cumulative statistics.
        
        Returns:
            Dictionary with all tracked statistics:
            - total_input_tokens: Cumulative input tokens
            - total_output_tokens: Cumulative output tokens
            - total_tokens: Sum of input + output
            - llm_call_count: Number of LLM API calls
            - tool_call_count: Number of tool calls
            - tool_success_count: Number of successful tool calls
            - tool_failure_count: Number of failed tool calls
            - session_duration_s: Elapsed time since tracker creation
            - avg_llm_duration_s: Average LLM call duration
            - avg_tokens_per_call: Average total tokens per LLM call
        """
        elapsed = time.time() - self._session_start
        total_tokens = self._total_input_tokens + self._total_output_tokens
        
        # Calculate averages
        avg_llm_duration = 0.0
        avg_tokens_per_call = 0.0
        if self._llm_call_count > 0:
            avg_llm_duration = sum(c.duration_s for c in self._llm_calls) / self._llm_call_count
            avg_tokens_per_call = total_tokens / self._llm_call_count
        
        return {
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_tokens": total_tokens,
            "llm_call_count": self._llm_call_count,
            "tool_call_count": self._tool_call_count,
            "tool_success_count": self._tool_success_count,
            "tool_failure_count": self._tool_failure_count,
            "session_duration_s": elapsed,
            "avg_llm_duration_s": avg_llm_duration,
            "avg_tokens_per_call": avg_tokens_per_call,
        }
    
    @property
    def total_input_tokens(self) -> int:
        """Get cumulative input tokens."""
        return self._total_input_tokens
    
    @property
    def total_output_tokens(self) -> int:
        """Get cumulative output tokens."""
        return self._total_output_tokens
    
    @property
    def total_tokens(self) -> int:
        """Get cumulative total tokens (input + output)."""
        return self._total_input_tokens + self._total_output_tokens
    
    @property
    def llm_call_count(self) -> int:
        """Get total LLM API call count."""
        return self._llm_call_count
    
    @property
    def tool_call_count(self) -> int:
        """Get total tool call count."""
        return self._tool_call_count
    
    @property
    def session_duration_s(self) -> float:
        """Get elapsed session duration in seconds."""
        return time.time() - self._session_start
    
    def reset(self) -> None:
        """Reset all counters and records."""
        self._session_start = time.time()
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._llm_call_count = 0
        self._tool_call_count = 0
        self._tool_success_count = 0
        self._tool_failure_count = 0
        self._llm_calls.clear()
        self._tool_calls.clear()
