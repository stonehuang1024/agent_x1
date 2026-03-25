"""
Session Manager Module - Track and manage agent sessions.

Provides session directory creation, LLM interaction logging,
and session history tracking for analysis and debugging.
"""

import os
import time
import json
import atexit
import signal
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict


@dataclass
class LLMCallRecord:
    """Record of a single LLM API call."""
    iteration: int
    timestamp: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    duration_ms: float
    stop_reason: str
    tool_calls_count: int


@dataclass
class SessionSummary:
    """Summary of a completed session."""
    session_name: str
    start_time: str
    end_time: str
    total_duration_minutes: float
    main_content: str
    operation_steps: List[str]
    llm_calls: List[LLMCallRecord]
    total_llm_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int


class SessionManager:
    """
    Manages session directories and logging for the agent.
    
    Creates organized session directories under results/session/ and
    logs all LLM interactions for debugging and analysis.
    
    Usage:
        manager = SessionManager("my_session")
        manager.start_session()
        
        # During operation
        manager.log_llm_interaction(prompt, response, metadata)
        manager.record_operation_step("Fetched data from API")
        
        # On exit (automatic or manual)
        manager.end_session("Completed data analysis task")
    """
    
    def __init__(
        self,
        session_name: Optional[str] = None,
        base_results_dir: str = "results",
        memory_data_dir: str = "memory_data"
    ):
        """
        Initialize session manager.
        
        Args:
            session_name: Optional name for the session (auto-generated if None)
            base_results_dir: Base directory for results
            memory_data_dir: Directory for history tracking
        """
        self.session_name = session_name or self._generate_session_name()
        self.timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.session_dir_name = f"{self.session_name}_{self.timestamp}"
        
        self.base_results_dir = Path(base_results_dir).expanduser().resolve()
        self.session_dir = self.base_results_dir / "session" / self.session_dir_name
        self.memory_data_dir = Path(memory_data_dir).expanduser().resolve()
        
        self.session_llm_file: Optional[Path] = None
        self.history_file = self.memory_data_dir / "history_session.md"
        
        self.start_time: Optional[datetime] = None
        self.operation_steps: List[str] = []
        self.llm_calls: List[LLMCallRecord] = []
        self.session_active = False
        
        # Register cleanup handlers
        atexit.register(self._on_exit)
        self._setup_signal_handlers()
    
    def _generate_session_name(self) -> str:
        """Generate a default session name."""
        return f"session_{datetime.now().strftime('%H%M%S')}"
    
    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        for sig in [signal.SIGTERM, signal.SIGINT]:
            try:
                signal.signal(sig, self._signal_handler)
            except (ValueError, OSError):
                # May not work in all environments (e.g., Windows, threads)
                pass
    
    def _signal_handler(self, signum, frame) -> None:
        """Handle shutdown signals."""
        self._on_exit()
        # Re-raise to allow normal exit
        signal.default_int_handler(signum, frame)
    
    def _on_exit(self) -> None:
        """Cleanup on exit - write session summary."""
        if self.session_active:
            self.end_session("Session ended (exit/kill signal)")
    
    def start_session(self) -> Path:
        """
        Create session directory and initialize logging.
        
        Returns:
            Path to the session directory
        """
        # Create directories
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.memory_data_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize session_llm.md
        self.session_llm_file = self.session_dir / "session_llm.md"
        self._write_session_llm_header()
        
        self.start_time = datetime.now()
        self.session_active = True
        
        return self.session_dir
    
    def _write_session_llm_header(self) -> None:
        """Write header to session_llm.md file."""
        header = f"""# Session LLM Log

**Session:** {self.session_dir_name}  
**Started:** {self.timestamp}

---

"""
        with open(self.session_llm_file, 'w', encoding='utf-8') as f:
            f.write(header)
    
    def log_llm_interaction(
        self,
        iteration: int,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        response: Dict[str, Any],
        duration_ms: float
    ) -> None:
        """
        Log a complete LLM interaction to session_llm.md.
        
        Args:
            iteration: Current iteration number
            messages: Messages sent to LLM
            tools: Tools available to LLM
            response: Complete LLM response
            duration_ms: API call duration in milliseconds
        """
        if not self.session_active or not self.session_llm_file:
            return
        
        timestamp = datetime.now().strftime("%H:%M:%S")
        usage = response.get("usage", {})
        
        # Extract key info
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        total_tokens = usage.get("total_tokens", input_tokens + output_tokens)
        stop_reason = response.get("stop_reason", "unknown")
        
        # Count tool calls
        tool_calls_count = 0
        content_blocks = response.get("content", [])
        for block in content_blocks:
            if block.get("type") == "tool_use":
                tool_calls_count += 1
        
        # Record for summary
        record = LLMCallRecord(
            iteration=iteration,
            timestamp=timestamp,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            duration_ms=duration_ms,
            stop_reason=stop_reason,
            tool_calls_count=tool_calls_count
        )
        self.llm_calls.append(record)
        
        # Format the log entry
        log_entry = f"""

## LLM Call {iteration} [{timestamp}]

### Request

**Messages ({len(messages)}):**

```json
{json.dumps(messages, indent=2, ensure_ascii=False)[:3000]}
```

**Tools ({len(tools)}):** {', '.join(t.get('name', 'unknown') for t in tools[:10])}

### Response

**Stop Reason:** {stop_reason}

**Usage:**
- Input Tokens: {input_tokens}
- Output Tokens: {output_tokens}
- Total Tokens: {total_tokens}
- Duration: {duration_ms:.2f}ms

**Content:**

```json
{json.dumps(response, indent=2, ensure_ascii=False)[:5000]}
```

---

"""
        
        with open(self.session_llm_file, 'a', encoding='utf-8') as f:
            f.write(log_entry)
    
    def record_operation_step(self, step: str) -> None:
        """
        Record a major operation step for the session summary.
        
        Args:
            step: Description of the operation performed
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.operation_steps.append(f"[{timestamp}] {step}")
    
    def get_session_directory(self) -> Optional[Path]:
        """Get the current session directory path."""
        return self.session_dir if self.session_active else None
    
    def end_session(self, main_content: str = "Session completed") -> None:
        """
        End the session and write summary to history_session.md.
        
        Args:
            main_content: Brief description of the session's main content/purpose
        """
        if not self.session_active:
            return
        
        end_time = datetime.now()
        duration = (end_time - self.start_time).total_seconds() / 60.0 if self.start_time else 0
        
        # Calculate totals
        total_input = sum(c.input_tokens for c in self.llm_calls)
        total_output = sum(c.output_tokens for c in self.llm_calls)
        
        summary = SessionSummary(
            session_name=self.session_dir_name,
            start_time=self.start_time.strftime("%Y-%m-%d %H:%M:%S") if self.start_time else "unknown",
            end_time=end_time.strftime("%Y-%m-%d %H:%M:%S"),
            total_duration_minutes=duration,
            main_content=main_content,
            operation_steps=self.operation_steps,
            llm_calls=self.llm_calls,
            total_llm_calls=len(self.llm_calls),
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_tokens=total_input + total_output
        )
        
        # Write to history_session.md
        self._write_history_summary(summary)
        
        # Finalize session_llm.md
        if self.session_llm_file:
            with open(self.session_llm_file, 'a', encoding='utf-8') as f:
                f.write(f"\n\n## Session Summary\n\n")
                f.write(f"**Status:** {main_content}\n")
                f.write(f"**Total LLM Calls:** {len(self.llm_calls)}\n")
                f.write(f"**Total Tokens:** {total_input + total_output}\n")
                f.write(f"**Duration:** {duration:.2f} minutes\n")
        
        self.session_active = False
    
    def _write_history_summary(self, summary: SessionSummary) -> None:
        """Write session summary to history_session.md."""
        # Ensure directory exists
        self.memory_data_dir.mkdir(parents=True, exist_ok=True)
        
        # Format LLM calls table
        calls_table = "| Iteration | Time | Input | Output | Total | Duration | Stop Reason | Tools |\n"
        calls_table += "|-----------|------|-------|--------|-------|----------|-------------|-------|\n"
        for call in summary.llm_calls:
            calls_table += f"| {call.iteration} | {call.timestamp} | {call.input_tokens} | {call.output_tokens} | {call.total_tokens} | {call.duration_ms:.0f}ms | {call.stop_reason} | {call.tool_calls_count} |\n"
        
        # Format operation steps
        steps_text = "\n".join(f"{i+1}. {step}" for i, step in enumerate(summary.operation_steps[:20]))
        if len(summary.operation_steps) > 20:
            steps_text += f"\n... ({len(summary.operation_steps) - 20} more steps)"
        
        history_entry = f"""

---

## Session: {summary.session_name}

**Period:** {summary.start_time} → {summary.end_time}  
**Duration:** {summary.total_duration_minutes:.2f} minutes  
**Summary:** {summary.main_content}

### Operation Steps

{steps_text}

### LLM Statistics

- **Total LLM Calls:** {summary.total_llm_calls}
- **Total Input Tokens:** {summary.total_input_tokens}
- **Total Output Tokens:** {summary.total_output_tokens}
- **Total Tokens:** {summary.total_tokens}

### LLM Call Details

{calls_table}

---

"""
        
        # Write to file (append mode)
        with open(self.history_file, 'a', encoding='utf-8') as f:
            f.write(history_entry)


# Global instance for easy access
_global_session_manager: Optional[SessionManager] = None


def get_session_manager() -> Optional[SessionManager]:
    """Get the global session manager instance."""
    return _global_session_manager


def init_session_manager(session_name: Optional[str] = None) -> SessionManager:
    """
    Initialize and start the global session manager.
    
    Args:
        session_name: Optional session name
        
    Returns:
        Initialized SessionManager instance
    """
    global _global_session_manager
    _global_session_manager = SessionManager(session_name)
    _global_session_manager.start_session()
    return _global_session_manager


def end_current_session(main_content: str = "Session ended") -> None:
    """End the current global session."""
    global _global_session_manager
    if _global_session_manager:
        _global_session_manager.end_session(main_content)
        _global_session_manager = None
