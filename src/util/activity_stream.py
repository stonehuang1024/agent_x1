"""
Live Activity Stream for Agent X1.

Provides real-time display of agent actions during task execution,
including LLM requests/responses, tool calls, and their results.

The ActivityStream formats and truncates activity entries, delegating
actual output to ConsoleDisplay.

Usage:
    from src.util.display import ConsoleDisplay
    from src.util.activity_stream import ActivityStream
    
    display = ConsoleDisplay(verbose=False)
    stream = ActivityStream(display=display, verbose=False)
    
    stream.llm_request(iteration=3, message_count=12)
    stream.llm_response(iteration=3, input_tokens=1234, output_tokens=567,
                        duration_s=2.3, tool_call_count=3)
    stream.tool_start("read_file", {"path": "/src/main.py"})
    stream.tool_success("read_file", duration_ms=120, output="import os...")
"""

import os
import time
from typing import Any, Dict, Optional

from src.util.display import Colors, ConsoleDisplay, Icons


# ── Truncation defaults ──────────────────────────────────────────────

# Normal mode — show at least 5 lines of meaningful content
DEFAULT_PARAM_SUMMARY_MAX = 300
DEFAULT_OUTPUT_PREVIEW_MAX = 500
DEFAULT_ERROR_PREVIEW_MAX = 500
DEFAULT_LLM_TEXT_PREVIEW_MAX = 600
DEFAULT_MAX_PREVIEW_LINES = 8

# Verbose mode — show more detail
VERBOSE_PARAM_SUMMARY_MAX = 600
VERBOSE_OUTPUT_PREVIEW_MAX = 1500
VERBOSE_ERROR_PREVIEW_MAX = 1000
VERBOSE_LLM_TEXT_PREVIEW_MAX = 2000
VERBOSE_MAX_PREVIEW_LINES = 20


def _truncate(text: str, max_len: int) -> str:
    """
    Truncate text to max_len characters, appending truncation marker.
    
    Args:
        text: Text to truncate
        max_len: Maximum character length
        
    Returns:
        Truncated text with marker if needed
    """
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"... [{len(text):,} chars total]"


def _compress_multiline(text: str) -> str:
    """
    Compress multi-line text into a single line.
    
    Replaces newlines with visible ↵ markers and collapses
    consecutive whitespace.
    
    Args:
        text: Multi-line text
        
    Returns:
        Single-line compressed text
    """
    # Replace various newline patterns
    compressed = text.replace("\r\n", "↵").replace("\r", "↵").replace("\n", "↵")
    # Collapse consecutive whitespace (but keep ↵ markers)
    import re
    compressed = re.sub(r"[ \t]+", " ", compressed)
    return compressed


def _format_multiline_preview(text: str, max_chars: int, max_lines: int, indent: str = "    │ ") -> list:
    """
    Format a multi-line text preview as indented lines.
    
    Instead of compressing everything into one line, preserves the
    original line structure (up to max_lines) so the user can read
    actual content.
    
    Args:
        text: Original text (may contain newlines)
        max_chars: Maximum total characters to show
        max_lines: Maximum number of lines to show
        indent: Prefix for each continuation line
        
    Returns:
        List of formatted lines (each already prefixed with indent)
    """
    if not text:
        return []
    
    lines = text.splitlines()
    result = []
    chars_used = 0
    
    for i, line in enumerate(lines):
        if i >= max_lines:
            remaining_lines = len(lines) - i
            result.append(f"{indent}... [{remaining_lines} more lines, {len(text):,} chars total]")
            break
        
        # Truncate individual long lines
        if chars_used + len(line) > max_chars:
            remaining = max_chars - chars_used
            if remaining > 20:
                result.append(f"{indent}{line[:remaining]}...")
            result.append(f"{indent}... [{len(text):,} chars total]")
            break
        
        result.append(f"{indent}{line}")
        chars_used += len(line)
    
    return result


def _format_param_summary(arguments: Dict[str, Any], max_len: int) -> str:
    """
    Format tool arguments into a brief summary string.
    
    Picks the most informative parameter (path, query, command, etc.)
    and formats it as key="value".
    
    Args:
        arguments: Tool call arguments dict
        max_len: Maximum summary length
        
    Returns:
        Formatted parameter summary
    """
    if not arguments:
        return ""
    
    # Priority keys that are most informative
    priority_keys = [
        "path", "file", "filename", "file_path",
        "query", "command", "url", "name",
        "content", "text", "message",
    ]
    
    parts = []
    
    # First, add priority keys
    for key in priority_keys:
        if key in arguments:
            val = str(arguments[key])
            val = _compress_multiline(val)
            if len(val) > 120:
                val = val[:117] + "..."
            parts.append(f'{key}="{val}"')
    
    # Then add remaining keys (up to a limit)
    for key, value in arguments.items():
        if key not in priority_keys and len(parts) < 5:
            val = str(value)
            val = _compress_multiline(val)
            if len(val) > 80:
                val = val[:77] + "..."
            parts.append(f'{key}="{val}"')
    
    summary = ", ".join(parts)
    if len(summary) > max_len:
        summary = summary[:max_len - 3] + "..."
    
    return summary


def _get_terminal_width() -> int:
    """Get the current terminal width, with a safe default."""
    try:
        return os.get_terminal_size().columns
    except (ValueError, OSError):
        return 120  # safe default


class ActivityStream:
    """
    Real-time activity stream for displaying agent actions.
    
    Formats and outputs live activity entries showing LLM requests,
    tool calls, and their results. Handles text truncation and
    compression for compact display.
    
    Args:
        display: ConsoleDisplay instance for actual output
        verbose: If True, show more detailed information
        session_start_time: Session start timestamp (default: now)
    """
    
    def __init__(
        self,
        display: ConsoleDisplay,
        verbose: bool = False,
        session_start_time: Optional[float] = None,
    ):
        self._display = display
        self._verbose = verbose
        self._session_start = session_start_time or time.time()
        self._llm_call_counter = 0
        
        # Truncation limits based on mode
        if verbose:
            self._param_max = VERBOSE_PARAM_SUMMARY_MAX
            self._output_max = VERBOSE_OUTPUT_PREVIEW_MAX
            self._error_max = VERBOSE_ERROR_PREVIEW_MAX
            self._llm_text_max = VERBOSE_LLM_TEXT_PREVIEW_MAX
            self._max_preview_lines = VERBOSE_MAX_PREVIEW_LINES
        else:
            self._param_max = DEFAULT_PARAM_SUMMARY_MAX
            self._output_max = DEFAULT_OUTPUT_PREVIEW_MAX
            self._error_max = DEFAULT_ERROR_PREVIEW_MAX
            self._llm_text_max = DEFAULT_LLM_TEXT_PREVIEW_MAX
            self._max_preview_lines = DEFAULT_MAX_PREVIEW_LINES
    
    def _relative_timestamp(self) -> str:
        """
        Get relative timestamp since session start.
        
        Returns:
            Formatted string like [+0:32] or [+5:07]
        """
        elapsed = time.time() - self._session_start
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        return f"[+{minutes}:{seconds:02d}]"
    
    def _emit(self, icon: str, message: str, color: str = Colors.RESET) -> None:
        """
        Emit an activity stream entry with timestamp, icon, and message.
        
        Args:
            icon: Emoji icon prefix
            message: Formatted message text
            color: ANSI color code
        """
        timestamp = self._relative_timestamp()
        
        # Build the full line
        full_line = f"{timestamp} {icon}  {message}"
        
        # Apply color and write
        self._display.raw(
            self._display._colorize(full_line, color)
        )
    
    def _emit_lines(self, lines: list, color: str = Colors.RESET) -> None:
        """
        Emit multiple continuation lines (for multi-line previews).
        
        Args:
            lines: List of pre-formatted lines
            color: ANSI color code
        """
        for line in lines:
            self._display.raw(
                self._display._colorize(line, color)
            )
    
    # ── LLM activity entries ──────────────────────────────────────────
    
    def llm_request(self, iteration: int, message_count: int, input_tokens: int = 0) -> None:
        """
        Display LLM request entry.
        
        Format: [+M:SS] 🤖 LLM Request #N | X messages | Y tokens | sending...
        
        Args:
            iteration: Current iteration number
            message_count: Number of messages being sent
            input_tokens: Estimated input token count
        """
        self._llm_call_counter += 1
        msg = f"LLM Request #{self._llm_call_counter} | {message_count} messages | {input_tokens:,} tokens | sending..."
        self._emit(Icons.LLM, msg, Colors.CYAN)
    
    def llm_response(
        self,
        iteration: int,
        input_tokens: int,
        output_tokens: int,
        duration_s: float,
        tool_call_count: int = 0,
        content_preview: str = "",
    ) -> None:
        """
        Display LLM response entry.
        
        Format:
            [+M:SS] 🤖 LLM Response #N | X→Y tokens | Z.Zs | N tool calls
                │ line 1 of content...
                │ line 2 of content...
                │ ...
        
        Args:
            iteration: Current iteration number
            input_tokens: Input token count
            output_tokens: Output token count
            duration_s: Response duration in seconds
            tool_call_count: Number of tool calls in response
            content_preview: Optional text content preview
        """
        parts = [
            f"LLM Response #{self._llm_call_counter}",
            f"{input_tokens:,}→{output_tokens:,} tokens",
            f"{duration_s:.1f}s",
        ]
        
        if tool_call_count > 0:
            parts.append(f"{tool_call_count} tool calls")
        
        msg = " | ".join(parts)
        self._emit(Icons.LLM, msg, Colors.BLUE)
        
        # Show content preview as multi-line block if available
        # Always show at least a few lines of content, even with tool calls
        preview_lines = _format_multiline_preview(
            content_preview,
            max_chars=self._llm_text_max,
            max_lines=self._max_preview_lines,
        )
        if preview_lines:
            self._emit_lines(preview_lines, Colors.DIM)    
    # ── Tool activity entries ─────────────────────────────────────────
    
    def tool_start(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> None:
        """
        Display tool call start entry.
        
        Format:
            [+M:SS] 🔧 Tool: tool_name
                │ path="/src/main.py"
                │ content="line1↵line2↵..."
        
        Args:
            tool_name: Name of the tool being called
            arguments: Tool call arguments (will be summarized)
        """
        self._emit(Icons.TOOL, f"Tool: {tool_name}", Colors.CYAN)
        
        # Show arguments as multi-line detail block
        if arguments:
            arg_lines = self._format_arguments_block(arguments)
            if arg_lines:
                self._emit_lines(arg_lines, Colors.DIM)
    
    def _format_arguments_block(self, arguments: Dict[str, Any]) -> list:
        """
        Format tool arguments as a multi-line indented block.
        
        Shows each argument on its own line for readability.
        
        Args:
            arguments: Tool call arguments dict
            
        Returns:
            List of formatted lines
        """
        if not arguments:
            return []
        
        indent = "    │ "
        lines = []
        
        # Priority keys first
        priority_keys = [
            "path", "file", "filename", "file_path",
            "query", "command", "url", "name",
        ]
        
        shown_keys = set()
        
        for key in priority_keys:
            if key in arguments:
                val = str(arguments[key])
                if len(val) > 200:
                    val = val[:197] + "..."
                lines.append(f'{indent}{key}="{val}"')
                shown_keys.add(key)
        
        # Then remaining keys
        for key, value in arguments.items():
            if key in shown_keys:
                continue
            val = str(value)
            # For long values (like content), show multi-line preview
            if len(val) > 200:
                first_lines = val[:300].splitlines()
                preview = first_lines[0] if first_lines else val[:200]
                lines.append(f'{indent}{key}="{preview}..." [{len(val):,} chars]')
            else:
                val_display = _compress_multiline(val)
                lines.append(f'{indent}{key}="{val_display}"')
            
            if len(lines) >= self._max_preview_lines:
                remaining = len(arguments) - len(shown_keys) - (len(lines) - len(shown_keys))
                if remaining > 0:
                    lines.append(f"{indent}... [{remaining} more params]")
                break
        
        return lines
    
    def tool_success(
        self,
        tool_name: str,
        duration_ms: float,
        output: str = "",
        output_length: Optional[int] = None,
    ) -> None:
        """
        Display tool success entry.
        
        Format:
            [+M:SS] ✅ tool_name (120ms) | 2,500 chars
                │ line 1 of output...
                │ line 2 of output...
                │ ...
        
        Args:
            tool_name: Name of the tool
            duration_ms: Execution time in milliseconds
            output: Tool output text (will be truncated)
            output_length: Total output length (if different from len(output))
        """
        total_len = output_length or len(output or "")
        header = f"{tool_name} ({duration_ms:.0f}ms)"
        if total_len > 0:
            header += f" | {total_len:,} chars"
        
        self._emit(Icons.SUCCESS, header, Colors.GREEN)
        
        # Show output as multi-line preview
        if output:
            preview_lines = _format_multiline_preview(
                output,
                max_chars=self._output_max,
                max_lines=self._max_preview_lines,
            )
            if preview_lines:
                self._emit_lines(preview_lines, Colors.DIM)
    
    def tool_failure(
        self,
        tool_name: str,
        duration_ms: float,
        error_message: str = "",
    ) -> None:
        """
        Display tool failure entry.
        
        Format:
            [+M:SS] ❌ tool_name FAILED (120ms)
                │ error line 1...
                │ error line 2...
                │ ...
        
        Args:
            tool_name: Name of the tool
            duration_ms: Execution time in milliseconds
            error_message: Error message text (will be truncated)
        """
        header = f"{tool_name} FAILED ({duration_ms:.0f}ms)"
        self._emit(Icons.ERROR, header, Colors.RED)
        
        # Show error as multi-line preview
        if error_message:
            error_lines = _format_multiline_preview(
                error_message,
                max_chars=self._error_max,
                max_lines=self._max_preview_lines,
            )
            if error_lines:
                self._emit_lines(error_lines, Colors.RED)
    
    # ── Batch and special entries ─────────────────────────────────────
    
    def parallel_batch(self, tool_count: int) -> None:
        """
        Display parallel batch start marker.
        
        Format: [+M:SS] ⚙️ Parallel batch [N tools]
        
        Args:
            tool_count: Number of tools in the parallel batch
        """
        msg = f"Parallel batch [{tool_count} tools]"
        self._emit(Icons.STATUS, msg, Colors.CYAN)
    
    def loop_detected(self, message: str) -> None:
        """
        Display loop detection warning.
        
        Format: [+M:SS] ⚠️ Loop detected: ...
        
        Args:
            message: Loop detection details
        """
        msg = f"Loop detected: {message}"
        self._emit(Icons.WARNING, msg, Colors.YELLOW)
    
    def step(self, step_num: int, max_steps: int, description: str = "") -> None:
        """
        Display iteration step marker.
        
        Format: [+M:SS] ⚙️ Step N/max | description
        
        Args:
            step_num: Current step number
            max_steps: Maximum number of steps
            description: Optional step description
        """
        parts = [f"Step {step_num}/{max_steps}"]
        if description:
            parts.append(description)
        
        msg = " | ".join(parts)
        self._emit(Icons.STATUS, msg, Colors.CYAN)
    
    def reset_counters(self) -> None:
        """Reset internal counters (e.g., LLM call counter) for a new session."""
        self._llm_call_counter = 0
        self._session_start = time.time()
