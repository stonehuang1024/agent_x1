"""
Console Display Module for Agent X1.

Provides a unified output channel for user-facing information,
completely separated from developer logging (Logger).

This module handles all visual output to the terminal including:
- Semantic icon prefixes (⚙️, ✅, ❌, ⚠️, etc.)
- ANSI color output with graceful degradation
- Verbose/debug mode switching

Usage:
    from src.util.display import ConsoleDisplay
    
    display = ConsoleDisplay(verbose=False)
    display.status("Processing step 3...")
    display.success("File created successfully")
    display.error("Connection failed")
"""

import os
import re
import sys
from pathlib import Path
from typing import Optional


class Colors:
    """ANSI color codes for semantic display output."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    
    # Semantic colors
    RED = "\033[31m"        # Error
    GREEN = "\033[32m"      # Success
    YELLOW = "\033[33m"     # Warning
    BLUE = "\033[34m"       # Statistics
    MAGENTA = "\033[35m"    # Session
    CYAN = "\033[36m"       # Status/Progress
    WHITE = "\033[37m"      # User input
    GRAY = "\033[90m"       # Dim/timestamp


class Icons:
    """Semantic icon prefixes for different message types."""
    STATUS = "⚙️"
    SUCCESS = "✅"
    ERROR = "❌"
    WARNING = "⚠️"
    INFO = "ℹ️"
    TOOL = "🔧"
    LLM = "🤖"
    STATS = "📊"
    SESSION = "📋"
    USER = "👤"


class ConsoleDisplay:
    """
    Unified user-facing display output channel.
    
    All user-visible terminal output should go through this class.
    This is completely separate from the Logger (developer debug logs).
    
    Args:
        verbose: If True, show more detailed information
        debug: If True, show debug-level information (implies verbose)
        color: If True, use ANSI colors. If None, auto-detect terminal support.
        stream: Output stream (default: sys.stderr)
    """
    
    def __init__(
        self,
        verbose: bool = False,
        debug: bool = False,
        color: Optional[bool] = None,
        stream=None,
    ):
        self.verbose = verbose or debug
        self.debug_mode = debug
        self._stream = stream or sys.stderr
        self._log_file = None  # Optional file mirror for activity output
        self._ansi_re = re.compile(r'\x1b\[[0-9;]*m')  # ANSI escape stripper
        
        # Auto-detect color support
        if color is None:
            self._color_enabled = self._detect_color_support()
        else:
            self._color_enabled = color
    
    def _detect_color_support(self) -> bool:
        """Detect if the terminal supports ANSI colors."""
        # Check if output is a TTY
        if not hasattr(self._stream, "isatty") or not self._stream.isatty():
            return False
        
        # Check for NO_COLOR environment variable (https://no-color.org/)
        if os.environ.get("NO_COLOR") is not None:
            return False
        
        # Check TERM environment variable
        term = os.environ.get("TERM", "")
        if term == "dumb":
            return False
        
        return True
    
    @property
    def color_enabled(self) -> bool:
        """Whether color output is enabled."""
        return self._color_enabled
    
    def _colorize(self, text: str, color: str) -> str:
        """Apply ANSI color to text if colors are enabled."""
        if self._color_enabled:
            return f"{color}{text}{Colors.RESET}"
        return text
    
    def _write(self, message: str) -> None:
        """Write a message to the output stream and optional log file."""
        try:
            self._stream.write(message + "\n")
            self._stream.flush()
        except (BrokenPipeError, OSError):
            # Silently ignore broken pipe errors
            pass
        
        # Mirror to log file (ANSI-stripped)
        if self._log_file:
            try:
                clean = self._ansi_re.sub('', message)
                self._log_file.write(clean + "\n")
                self._log_file.flush()
            except (BrokenPipeError, OSError):
                pass
    
    def _format_message(self, icon: str, text: str, color: str) -> str:
        """Format a message with icon and optional color."""
        colored_text = self._colorize(text, color)
        return f"{icon}  {colored_text}"
    
    # ── Core display methods ──────────────────────────────────────────
    
    def status(self, message: str) -> None:
        """Display a status/progress message. (⚙️ cyan)"""
        self._write(self._format_message(Icons.STATUS, message, Colors.CYAN))
    
    def success(self, message: str) -> None:
        """Display a success message. (✅ green)"""
        self._write(self._format_message(Icons.SUCCESS, message, Colors.GREEN))
    
    def error(self, message: str) -> None:
        """Display an error message. (❌ red bold)"""
        color = f"{Colors.BOLD}{Colors.RED}" if self._color_enabled else Colors.RED
        self._write(self._format_message(Icons.ERROR, message, color))
    
    def warning(self, message: str) -> None:
        """Display a warning message. (⚠️ yellow)"""
        self._write(self._format_message(Icons.WARNING, message, Colors.YELLOW))
    
    def info(self, message: str) -> None:
        """Display an informational message. (ℹ️ default)"""
        self._write(self._format_message(Icons.INFO, message, Colors.RESET))
    
    # ── Specialized display methods ───────────────────────────────────
    
    def tool_start(self, tool_name: str, summary: str = "") -> None:
        """
        Display tool execution start.
        
        Args:
            tool_name: Name of the tool being called
            summary: Brief parameter summary
        """
        if summary:
            msg = f"{tool_name} → {summary}"
        else:
            msg = tool_name
        self._write(self._format_message(Icons.TOOL, msg, Colors.CYAN))
    
    def tool_end(
        self,
        tool_name: str,
        success: bool = True,
        duration_ms: Optional[float] = None,
        summary: str = "",
    ) -> None:
        """
        Display tool execution result.
        
        Args:
            tool_name: Name of the tool
            success: Whether the tool succeeded
            duration_ms: Execution time in milliseconds
            summary: Brief result summary
        """
        icon = Icons.SUCCESS if success else Icons.ERROR
        color = Colors.GREEN if success else Colors.RED
        
        parts = [tool_name]
        if duration_ms is not None:
            parts.append(f"({duration_ms:.0f}ms)")
        if summary:
            parts.append(f"→ {summary}")
        
        msg = " ".join(parts)
        self._write(self._format_message(icon, msg, color))
    
    def llm_stats(
        self,
        input_tokens: int,
        output_tokens: int,
        duration_s: float,
        tool_calls: int = 0,
    ) -> None:
        """
        Display LLM call statistics.
        
        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            duration_s: Duration in seconds
            tool_calls: Number of tool calls in the response
        """
        parts = [
            f"Tokens: {input_tokens:,}→{output_tokens:,}",
            f"{duration_s:.1f}s",
        ]
        if tool_calls > 0:
            parts.append(f"{tool_calls} tool calls")
        
        msg = " | ".join(parts)
        self._write(self._format_message(Icons.STATS, msg, Colors.BLUE))
    
    def session_summary(
        self,
        total_duration_s: float,
        total_input_tokens: int,
        total_output_tokens: int,
        llm_calls: int,
        tool_calls: int,
    ) -> None:
        """
        Display session summary at the end of a session.
        
        Args:
            total_duration_s: Total session duration in seconds
            total_input_tokens: Total input tokens consumed
            total_output_tokens: Total output tokens consumed
            llm_calls: Total number of LLM API calls
            tool_calls: Total number of tool calls
        """
        # Format duration
        minutes = int(total_duration_s // 60)
        seconds = int(total_duration_s % 60)
        if minutes > 0:
            duration_str = f"{minutes}m {seconds}s"
        else:
            duration_str = f"{seconds}s"
        
        total_tokens = total_input_tokens + total_output_tokens
        
        self._write("")  # blank line separator
        self._write(self._format_message(
            Icons.SESSION,
            self._colorize("Session Summary", f"{Colors.BOLD}{Colors.MAGENTA}") if self._color_enabled else "Session Summary",
            Colors.RESET,
        ))
        
        summary_lines = [
            f"  Duration:    {duration_str}",
            f"  Tokens:      {total_tokens:,} ({total_input_tokens:,} in → {total_output_tokens:,} out)",
            f"  LLM calls:   {llm_calls}",
            f"  Tool calls:  {tool_calls}",
        ]
        
        for line in summary_lines:
            self._write(self._colorize(line, Colors.DIM))
        
        self._write("")  # blank line separator
    
    def user_input(self, message: str) -> None:
        """Display user input echo. (👤 white)"""
        self._write(self._format_message(Icons.USER, message, Colors.WHITE))
    
    def raw(self, message: str) -> None:
        """Write a raw message without any formatting."""
        self._write(message)
    
    def set_log_file(self, path: str) -> None:
        """
        Start mirroring all display output to a file.
        
        Args:
            path: Path to the log file (will be created/appended)
        """
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._log_file = open(path, 'a', encoding='utf-8')
    
    def close_log_file(self) -> None:
        """Close the log file mirror if open."""
        if self._log_file:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None
    
    def blank_line(self) -> None:
        """Write a blank line."""
        self._write("")
    
    def separator(self, char: str = "─", width: int = 60) -> None:
        """Write a visual separator line."""
        line = char * width
        self._write(self._colorize(line, Colors.DIM))
    
    def verbose_info(self, message: str) -> None:
        """
        Display information only in verbose mode.
        
        Args:
            message: Message to display (only shown if verbose=True)
        """
        if self.verbose:
            self._write(self._format_message(Icons.INFO, message, Colors.DIM))
    
    def debug_info(self, message: str) -> None:
        """
        Display information only in debug mode.
        
        Args:
            message: Message to display (only shown if debug=True)
        """
        if self.debug_mode:
            self._write(self._colorize(f"  [DEBUG] {message}", Colors.GRAY))
