"""
Unified Logging Configuration for Agent X1.

Provides standardized logging with:
- Timestamp (ISO format with milliseconds)
- Process ID
- Thread ID  
- File name and line number
- Log level
- Clear separation between fixed fields and message content

Usage:
    from src.util.logger import get_logger
    
    logger = get_logger(__name__)
    logger.info("Processing data")
    logger.error("Error occurred", exc_info=True)
"""

import logging
import os
import sys
import threading
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional


# ANSI color codes for console output
class LogColors:
    """ANSI color codes for log levels."""
    RESET = "\033[0m"
    DEBUG = "\033[36m"      # Cyan
    INFO = "\033[32m"       # Green
    WARNING = "\033[33m"    # Yellow
    ERROR = "\033[31m"      # Red
    CRITICAL = "\033[35m"   # Magenta
    
    # For fixed fields
    TIMESTAMP = "\033[90m"  # Gray
    PROCESS = "\033[94m"    # Light Blue
    LOCATION = "\033[95m"   # Light Magenta


class StandardFormatter(logging.Formatter):
    """
    Standard log formatter with fixed fields and message separation.
    
    Format:
    [TIMESTAMP] [PID:PROCESS_ID] [TID:THREAD_ID] [LEVEL] [FILE:LINE] | MESSAGE
    
    Example:
    [2025-12-08 08:30:00.123] [PID:12345] [TID:67890] [INFO ] [order_flow.py:85] | Computing agent direction...
    """
    
    # Format string with clear field separation (includes session_id)
    FORMAT = (
        "[%(asctime)s] "
        "[sid:%(session_id)s] "
        "[%(process)d] "
        "[%(thread)d] "
        "[%(levelname)-5s] "
        "[%(filename)s:%(lineno)d] "
        "| %(message)s"
    )
    
    DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
    
    def __init__(self, use_colors: bool = False):
        """
        Initialize formatter.
        
        Args:
            use_colors: Whether to use ANSI colors (for console output)
        """
        super().__init__(fmt=self.FORMAT, datefmt=self.DATE_FORMAT)
        self.use_colors = use_colors
        self._level_colors = {
            logging.DEBUG: LogColors.DEBUG,
            logging.INFO: LogColors.INFO,
            logging.WARNING: LogColors.WARNING,
            logging.ERROR: LogColors.ERROR,
            logging.CRITICAL: LogColors.CRITICAL,
        }
    
    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        """Format timestamp with milliseconds."""
        ct = datetime.fromtimestamp(record.created)
        if datefmt:
            s = ct.strftime(datefmt)
        else:
            s = ct.strftime(self.DATE_FORMAT)
        # Add milliseconds
        s = f"{s}.{int(record.msecs):03d}"
        return s
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record with optional colors."""
        # Ensure session_id is present (fallback if SessionIdFilter not applied)
        if not hasattr(record, "session_id"):
            record.session_id = "--------"
        
        # Ensure levelname is padded
        record.levelname = record.levelname.ljust(5)
        
        if self.use_colors and sys.stdout.isatty():
            # Apply colors
            level_color = self._level_colors.get(record.levelno, LogColors.RESET)
            
            # Format the message first
            formatted = super().format(record)
            
            # Apply colors to different parts
            # This is a simplified approach - color the whole line based on level
            return f"{level_color}{formatted}{LogColors.RESET}"
        
        return super().format(record)


class JSONFormatter(logging.Formatter):
    """
    JSON formatter for structured logging (useful for log aggregation systems).
    
    Output format:
    {"timestamp": "...", "level": "...", "process_id": ..., "thread_id": ..., 
     "file": "...", "line": ..., "logger": "...", "message": "..."}
    """
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        import json
        
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )[:-3],  # ISO format with milliseconds
            "level": record.levelname,
            "session_id": getattr(record, "session_id", "--------"),
            "process_id": record.process,
            "thread_id": record.thread,
            "file": record.filename,
            "line": record.lineno,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_data, ensure_ascii=False)


# Thread-local storage for session_id
_thread_local = threading.local()


class SessionIdFilter(logging.Filter):
    """
    Logging filter that injects session_id into every LogRecord.
    
    Uses threading.local() to store the current session_id so that
    each thread can have its own session context.
    """
    
    def filter(self, record: logging.LogRecord) -> bool:
        """Inject session_id attribute into the log record."""
        sid = getattr(_thread_local, "session_id", None)
        record.session_id = sid[:8] if sid else "--------"
        return True


def set_session_id(sid: str) -> None:
    """
    Set the current session_id for the calling thread.
    
    This will be injected into all subsequent log records from this thread.
    
    Args:
        sid: The session ID string
    """
    _thread_local.session_id = sid


def clear_session_id() -> None:
    """Clear the current session_id for the calling thread."""
    _thread_local.session_id = None


def get_current_session_id() -> Optional[str]:
    """Get the current session_id for the calling thread."""
    return getattr(_thread_local, "session_id", None)


def truncate_for_log(text: str, max_len: int = 500) -> str:
    """
    Truncate text for log output, appending total length if truncated.
    
    Args:
        text: The text to potentially truncate
        max_len: Maximum length before truncation (default 500)
        
    Returns:
        Original text if short enough, or truncated text with length annotation
    """
    if not text:
        return ""
    text_str = str(text)
    if len(text_str) <= max_len:
        return text_str
    return f"{text_str[:max_len]}... [{len(text_str)} chars total]"


def mask_sensitive(value: str, visible_prefix: int = 3, visible_suffix: int = 4) -> str:
    """
    Mask sensitive values like API keys for safe logging.
    
    Args:
        value: The sensitive string to mask
        visible_prefix: Number of characters to show at the start
        visible_suffix: Number of characters to show at the end
        
    Returns:
        Masked string like 'sk-...XXXX'
    """
    if not value or len(value) <= visible_prefix + visible_suffix:
        return "***"
    return f"{value[:visible_prefix]}...{value[-visible_suffix:]}"


# Global configuration
_logging_configured = False
_log_level = logging.DEBUG
_log_dir: Optional[Path] = None
_bound_session_prefix: Optional[str] = None  # First 8 chars of session_id for log filename

# Log level name mapping for environment variable support
_LEVEL_NAME_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _resolve_log_level(level: int) -> int:
    """
    Resolve log level, with environment variable override.
    
    The AGENT_X1_LOG_LEVEL environment variable takes precedence over
    the programmatic level argument.
    
    Args:
        level: Default log level
        
    Returns:
        Resolved log level
    """
    env_level = os.environ.get("AGENT_X1_LOG_LEVEL", "").upper()
    if env_level and env_level in _LEVEL_NAME_MAP:
        return _LEVEL_NAME_MAP[env_level]
    return level


def _daily_log_namer(default_name: str) -> str:
    """
    Custom namer for TimedRotatingFileHandler.
    
    Converts default format: agent_x1.log.20260329
    To desired format:      agent_x1_20260329.log
    
    Args:
        default_name: The default rotated filename (e.g., '/path/agent_x1.log.20260329')
        
    Returns:
        Renamed filename (e.g., '/path/agent_x1_20260329.log')
    """
    # default_name looks like: /path/to/agent_x1.log.20260329
    # We want:                 /path/to/agent_x1_20260329.log
    dir_name = os.path.dirname(default_name)
    base_name = os.path.basename(default_name)
    
    # Split: "agent_x1.log.20260329" -> parts around the date suffix
    parts = base_name.rsplit(".", 1)
    if len(parts) == 2:
        name_with_ext = parts[0]  # "agent_x1.log"
        date_suffix = parts[1]    # "20260329"
        # Split name_with_ext: "agent_x1.log" -> "agent_x1" + ".log"
        name_parts = name_with_ext.rsplit(".", 1)
        if len(name_parts) == 2:
            return os.path.join(dir_name, f"{name_parts[0]}_{date_suffix}.{name_parts[1]}")
    
    return default_name


def setup_logging(
    level: int = logging.DEBUG,
    log_dir: Optional[str] = None,
    log_to_file: bool = True,
    log_to_console: bool = True,
    use_colors: bool = True,
    use_json: bool = False,
    backup_count: int = 30,
) -> None:
    """
    Setup global logging configuration.
    
    This should be called once at application startup.
    The AGENT_X1_LOG_LEVEL environment variable can override the level parameter.
    
    Log files are rotated daily at midnight with filenames like:
    agent_x1_20260329.log, agent_x1_error_20260329.log
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory for log files (default: ./logs)
        log_to_file: Whether to log to file
        log_to_console: Whether to log to console
        use_colors: Whether to use colors in console output
        use_json: Whether to use JSON format for file output
        backup_count: Number of daily backup files to keep (default 30 days)
    """
    global _logging_configured, _log_level, _log_dir
    
    if _logging_configured:
        return
    
    # Resolve level with env var override
    level = _resolve_log_level(level)
    _log_level = level
    
    # Determine log directory
    if log_dir:
        _log_dir = Path(log_dir)
    else:
        # Default to project root/logs
        project_root = Path(__file__).parent.parent
        _log_dir = project_root / "logs"
    
    _log_dir.mkdir(parents=True, exist_ok=True)
    
    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Remove existing handlers and filters
    root_logger.handlers.clear()
    for f in root_logger.filters[:]:
        root_logger.removeFilter(f)
    
    # Create SessionIdFilter — will be added to each handler
    session_filter = SessionIdFilter()
    
    # Console handler — always INFO level to avoid flooding terminal
    if log_to_console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(logging.INFO)
        console_formatter = StandardFormatter(use_colors=use_colors)
        console_handler.setFormatter(console_formatter)
        console_handler.addFilter(session_filter)
        root_logger.addHandler(console_handler)
    
    # File handler — uses TimedRotatingFileHandler for daily rotation
    if log_to_file:
        # Main log file: agent_x1.log (rotated to agent_x1.log.YYYYMMDD)
        log_file = _log_dir / "agent_x1.log"
        
        file_handler = TimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=backup_count,
            encoding="utf-8",
        )
        # Use date suffix format YYYYMMDD
        file_handler.suffix = "%Y%m%d"
        file_handler.namer = _daily_log_namer
        file_handler.setLevel(logging.DEBUG)
        
        if use_json:
            file_formatter = JSONFormatter()
        else:
            file_formatter = StandardFormatter(use_colors=False)
        
        file_handler.setFormatter(file_formatter)
        file_handler.addFilter(session_filter)
        root_logger.addHandler(file_handler)
        
        # Error-only log file: agent_x1_error.log (also daily rotation)
        error_file = _log_dir / "agent_x1_error.log"
        error_handler = TimedRotatingFileHandler(
            error_file,
            when="midnight",
            interval=1,
            backupCount=backup_count,
            encoding="utf-8",
        )
        error_handler.suffix = "%Y%m%d"
        error_handler.namer = _daily_log_namer
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(file_formatter)
        error_handler.addFilter(session_filter)
        root_logger.addHandler(error_handler)
    
    # Suppress noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    
    _logging_configured = True
    
    # Log that logging is configured (INFO so it's visible)
    logger = logging.getLogger(__name__)
    logger.info(
        f"Logging configured: level={logging.getLevelName(level)}, "
        f"log_dir={_log_dir}, "
        f"file_handler=TimedRotatingFileHandler(daily), "
        f"backup_count={backup_count}"
    )
    if log_to_file:
        logger.info(f"Log file: {_log_dir / 'agent_x1.log'}")
        logger.info(f"Error log file: {_log_dir / 'agent_x1_error.log'}")


def bind_session_to_log(session_id: str) -> Optional[Path]:
    """
    Bind a session ID to the log file names, isolating logs per session.
    
    After calling this function, the main log file becomes:
        agent_x1_{yyyymmdd_HHMMSS}_{session_prefix}.log
    And the error log file becomes:
        agent_x1_error_{yyyymmdd_HHMMSS}_{session_prefix}.log
    
    The timestamp indicates when the session was bound (i.e. when the
    Agent session started), making it easy to identify log files by
    creation time.
    
    This solves the multi-instance log pollution problem where multiple
    Agent instances write to the same log file.
    
    Must be called AFTER setup_logging() and AFTER session_id is available.
    Calling multiple times with the same session_id is a no-op.
    
    Args:
        session_id: The full session ID string (first 8 chars will be used)
        
    Returns:
        Path to the new main log file, or None if binding failed
    """
    global _bound_session_prefix
    
    if not session_id:
        return None
    
    prefix = session_id[:8]
    
    # Idempotent: skip if already bound to this session
    if _bound_session_prefix == prefix:
        # Return the existing log file path (find it by pattern)
        if _log_dir:
            for f in sorted(_log_dir.glob(f"agent_x1_*_{prefix}.log")):
                return f
        return None
    
    if not _logging_configured or not _log_dir:
        logging.getLogger(__name__).warning(
            "[Logger] bind_session_to_log called before setup_logging, ignored"
        )
        return None
    
    # Generate timestamp for the log filename
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    new_main_log = _log_dir / f"agent_x1_{ts}_{prefix}.log"
    new_error_log = _log_dir / f"agent_x1_error_{ts}_{prefix}.log"
    
    # Ensure log directory exists (it may have been cleaned up in tests)
    try:
        _log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        logging.getLogger(__name__).warning(
            "[Logger] bind_session_to_log: log_dir %s cannot be created, skipping", _log_dir
        )
        return None
    
    root_logger = logging.getLogger()
    
    for handler in root_logger.handlers:
        if not isinstance(handler, TimedRotatingFileHandler):
            continue
        
        old_path = handler.baseFilename
        old_name = os.path.basename(old_path)
        
        # Determine if this is the main log or error log
        if "error" in old_name:
            new_path = str(new_error_log)
        else:
            new_path = str(new_main_log)
        
        # Close the old file stream
        try:
            handler.close()
        except Exception:
            pass
        
        # Update the handler to point to the new file
        handler.baseFilename = os.path.abspath(new_path)
        try:
            handler.stream = handler._open()
        except OSError as e:
            logging.getLogger(__name__).warning(
                "[Logger] bind_session_to_log: failed to open %s: %s", new_path, e
            )
            return None
    
    _bound_session_prefix = prefix
    
    log = logging.getLogger(__name__)
    log.info(
        f"[Logger] Log files bound to session {prefix} (ts={ts}): "
        f"main={new_main_log}, error={new_error_log}"
    )
    
    return new_main_log


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the standard configuration.
    
    This is the primary way to get a logger in the application.
    If setup_logging() hasn't been called, it will be called with defaults.
    
    Args:
        name: Logger name (typically __name__)
        
    Returns:
        Configured logger instance
        
    Usage:
        from src.util.logger import get_logger
        
        logger = get_logger(__name__)
        logger.info("Processing started")
        logger.debug("Debug info: %s", data)
        logger.error("Error occurred", exc_info=True)
    """
    global _logging_configured
    
    if not _logging_configured:
        # Auto-configure with defaults
        setup_logging()
    
    return logging.getLogger(name)


def set_log_level(level: int) -> None:
    """
    Change the global log level at runtime.
    
    Args:
        level: New logging level
    """
    global _log_level
    _log_level = level
    
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    for handler in root_logger.handlers:
        # Don't change error handler level
        if handler.level != logging.ERROR:
            handler.setLevel(level)


def get_log_dir() -> Optional[Path]:
    """Get the current log directory."""
    return _log_dir


def is_configured() -> bool:
    """Check if logging has been configured."""
    return _logging_configured


def reset_logging() -> None:
    """
    Reset logging configuration. Useful for testing.
    
    This clears all handlers, filters, and resets the configured flag,
    allowing setup_logging() to be called again.
    """
    global _logging_configured, _log_level, _log_dir, _bound_session_prefix
    
    root_logger = logging.getLogger()
    # Close all handlers before clearing to release file locks
    for h in root_logger.handlers:
        try:
            h.close()
        except Exception:
            pass
    root_logger.handlers.clear()
    # Clear all filters (including SessionIdFilter)
    for f in root_logger.filters[:]:
        root_logger.removeFilter(f)
    
    # Clear thread-local session_id
    clear_session_id()
    
    _logging_configured = False
    _log_level = logging.DEBUG
    _log_dir = None
    _bound_session_prefix = None


# Convenience functions for quick logging without getting a logger first
def debug(msg: str, *args, **kwargs) -> None:
    """Log a debug message."""
    get_logger("agent_x1").debug(msg, *args, **kwargs)


def info(msg: str, *args, **kwargs) -> None:
    """Log an info message."""
    get_logger("agent_x1").info(msg, *args, **kwargs)


def warning(msg: str, *args, **kwargs) -> None:
    """Log a warning message."""
    get_logger("agent_x1").warning(msg, *args, **kwargs)


def error(msg: str, *args, **kwargs) -> None:
    """Log an error message."""
    get_logger("agent_x1").error(msg, *args, **kwargs)


def critical(msg: str, *args, **kwargs) -> None:
    """Log a critical message."""
    get_logger("agent_x1").critical(msg, *args, **kwargs)


# For backwards compatibility with existing code that uses logging.basicConfig
def configure_basic_logging(level: int = logging.INFO) -> None:
    """
    Configure basic logging - replacement for logging.basicConfig().
    
    This provides a drop-in replacement for existing code that uses
    logging.basicConfig() but with our standardized format.
    """
    setup_logging(level=level)


# Backward-compatible module-level logger instance
xlog = get_logger(__name__)
