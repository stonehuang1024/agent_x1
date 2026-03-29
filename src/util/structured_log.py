"""
Structured Log Storage Module for Agent X1.

Provides JSONL-based structured logging for session events,
enabling post-hoc analysis, debugging, and auditing.

Events are written as JSON Lines to session_log.jsonl, with
buffered I/O to avoid blocking the main execution flow.

Usage:
    from src.util.structured_log import StructuredLogger, LogEventType
    
    slog = StructuredLogger(session_dir="/path/to/session", session_id="abc123")
    slog.log(LogEventType.TOOL_CALL, {"tool_name": "read_file", "duration_ms": 120})
    slog.close()
"""

import json
import logging
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


class LogEventType(str, Enum):
    """Structured log event types."""
    TOOL_CALL = "tool_call"
    LLM_CALL = "llm_call"
    FILE_CHANGE = "file_change"
    SHELL_EXEC = "shell_exec"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    TURN_COMPLETE = "turn_complete"


class StructuredLogger:
    """
    JSONL-based structured logger for session events.
    
    Writes events as JSON Lines to a session log file, with
    automatic file rotation and buffered I/O.
    
    Args:
        session_dir: Directory to store log files
        session_id: Unique session identifier
        max_bytes: Maximum log file size before rotation (default: 10MB)
        backup_count: Number of rotated files to keep (default: 5)
        buffer_size: Number of events to buffer before flushing (default: 10)
    """
    
    def __init__(
        self,
        session_dir: str,
        session_id: str,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
        buffer_size: int = 10,
    ):
        self._session_dir = Path(session_dir)
        self._session_id = session_id
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._buffer_size = buffer_size
        
        # Ensure directory exists
        self._session_dir.mkdir(parents=True, exist_ok=True)
        
        # Log file path
        self._log_file = self._session_dir / "session_log.jsonl"
        
        # Internal state
        self._buffer: List[str] = []
        self._event_count = 0
        self._session_start_time = time.time()
        self._events_summary: List[Dict[str, Any]] = []  # For session summary
        self._closed = False
        
        # Open file handle with buffered writing
        self._file_handle = open(self._log_file, "a", encoding="utf-8", buffering=1)
        
        # Log session start
        self.log(LogEventType.SESSION_START, {
            "session_id": session_id,
            "session_dir": str(session_dir),
            "start_time": datetime.now(timezone.utc).isoformat(),
        })
    
    def _now_iso(self) -> str:
        """Get current timestamp in ISO format with millisecond precision."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    
    def log(self, event_type: LogEventType, data: Dict[str, Any]) -> None:
        """
        Log a structured event.
        
        Args:
            event_type: Type of the event
            data: Event payload data
        """
        if self._closed:
            logger.warning("Attempted to log to closed StructuredLogger")
            return
        
        event = {
            "timestamp": self._now_iso(),
            "event_type": event_type.value,
            "session_id": self._session_id,
            "data": data,
        }
        
        # Track for summary generation
        self._events_summary.append({
            "timestamp": event["timestamp"],
            "event_type": event_type.value,
            "summary": self._extract_summary(event_type, data),
        })
        
        self._event_count += 1
        
        # Serialize and buffer
        line = json.dumps(event, ensure_ascii=False, default=str)
        self._buffer.append(line)
        
        # Flush if buffer is full
        if len(self._buffer) >= self._buffer_size:
            self._flush()
        
        # Check rotation
        self._check_rotation()
    
    def _extract_summary(self, event_type: LogEventType, data: Dict[str, Any]) -> str:
        """Extract a brief summary from event data for the session summary."""
        if event_type == LogEventType.TOOL_CALL:
            tool_name = data.get("tool_name", "unknown")
            success = data.get("success", True)
            duration = data.get("duration_ms", 0)
            status = "✅" if success else "❌"
            return f"{status} {tool_name} ({duration:.0f}ms)"
        
        elif event_type == LogEventType.LLM_CALL:
            input_t = data.get("input_tokens", 0)
            output_t = data.get("output_tokens", 0)
            duration = data.get("duration_s", 0)
            return f"🤖 LLM {input_t:,}→{output_t:,} tokens ({duration:.1f}s)"
        
        elif event_type == LogEventType.FILE_CHANGE:
            path = data.get("file_path", "unknown")
            change_type = data.get("change_type", "unknown")
            return f"📄 {change_type}: {path}"
        
        elif event_type == LogEventType.SHELL_EXEC:
            command = data.get("command", "")[:80]
            exit_code = data.get("exit_code", -1)
            return f"💻 $ {command} (exit: {exit_code})"
        
        elif event_type == LogEventType.TURN_COMPLETE:
            turn = data.get("turn_number", 0)
            tool_count = data.get("tool_call_count", 0)
            return f"Turn {turn} completed ({tool_count} tool calls)"
        
        elif event_type == LogEventType.SESSION_START:
            return "Session started"
        
        elif event_type == LogEventType.SESSION_END:
            reason = data.get("reason", "normal")
            return f"Session ended ({reason})"
        
        return str(event_type.value)
    
    def _flush(self) -> None:
        """Flush buffered events to disk."""
        if not self._buffer or self._closed:
            return
        
        try:
            for line in self._buffer:
                self._file_handle.write(line + "\n")
            self._file_handle.flush()
            self._buffer.clear()
        except (IOError, OSError) as e:
            logger.error(f"Failed to flush structured log: {e}")
    
    def _check_rotation(self) -> None:
        """Check if log file needs rotation based on size."""
        try:
            if self._log_file.exists() and self._log_file.stat().st_size > self._max_bytes:
                self._rotate()
        except OSError:
            pass
    
    def _rotate(self) -> None:
        """Rotate log files."""
        self._flush()
        
        try:
            # Close current file
            self._file_handle.close()
            
            # Rotate existing backup files
            for i in range(self._backup_count - 1, 0, -1):
                src = self._session_dir / f"session_log.{i}.jsonl"
                dst = self._session_dir / f"session_log.{i + 1}.jsonl"
                if src.exists():
                    src.rename(dst)
            
            # Rename current to .1
            backup = self._session_dir / "session_log.1.jsonl"
            if self._log_file.exists():
                self._log_file.rename(backup)
            
            # Remove oldest if exceeds backup count
            oldest = self._session_dir / f"session_log.{self._backup_count + 1}.jsonl"
            if oldest.exists():
                oldest.unlink()
            
            # Reopen file
            self._file_handle = open(self._log_file, "a", encoding="utf-8", buffering=1)
            
        except (IOError, OSError) as e:
            logger.error(f"Failed to rotate structured log: {e}")
            # Try to reopen anyway
            self._file_handle = open(self._log_file, "a", encoding="utf-8", buffering=1)
    
    def generate_session_summary(
        self,
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
        llm_calls: int = 0,
        tool_calls: int = 0,
    ) -> str:
        """
        Generate a human-readable session summary markdown file.
        
        Args:
            total_input_tokens: Total input tokens consumed
            total_output_tokens: Total output tokens consumed
            llm_calls: Total LLM API calls
            tool_calls: Total tool calls
            
        Returns:
            Path to the generated summary file
        """
        duration = time.time() - self._session_start_time
        minutes = int(duration // 60)
        seconds = int(duration % 60)
        
        total_tokens = total_input_tokens + total_output_tokens
        
        lines = [
            f"# Session Summary",
            f"",
            f"**Session ID:** {self._session_id}",
            f"**Duration:** {minutes}m {seconds}s",
            f"**Events:** {self._event_count}",
            f"",
            f"## Token Consumption",
            f"",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Input Tokens | {total_input_tokens:,} |",
            f"| Output Tokens | {total_output_tokens:,} |",
            f"| Total Tokens | {total_tokens:,} |",
            f"| LLM Calls | {llm_calls} |",
            f"| Tool Calls | {tool_calls} |",
            f"",
            f"## Activity Timeline",
            f"",
        ]
        
        for event in self._events_summary:
            ts = event["timestamp"]
            # Extract time portion only
            if "T" in ts:
                ts = ts.split("T")[1].replace("Z", "")
            lines.append(f"- `{ts}` {event['summary']}")
        
        lines.append("")
        
        summary_content = "\n".join(lines)
        summary_path = self._session_dir / "session_summary.md"
        
        try:
            summary_path.write_text(summary_content, encoding="utf-8")
            logger.debug(f"Session summary written to {summary_path}")
        except (IOError, OSError) as e:
            logger.error(f"Failed to write session summary: {e}")
        
        return str(summary_path)
    
    def close(self) -> None:
        """Flush remaining buffer and close the log file."""
        if self._closed:
            return
        
        self._flush()
        
        try:
            self._file_handle.close()
        except (IOError, OSError):
            pass
        
        self._closed = True
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
