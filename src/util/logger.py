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
    from src.core.logger import get_logger
    
    logger = get_logger(__name__)
    logger.info("Processing data")
    logger.error("Error occurred", exc_info=True)
"""
import sys
from loguru import logger as __inside_rename_logger

import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
from typing import Optional

# 移除默认的 handler
__inside_rename_logger.remove()

# 配置日志格式，包含时间、进程号、线程号、文件名和行号
log_format = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>PID:{process}</cyan> | "
    "<magenta>TID:{thread}</magenta> | "
    "<blue>{file}</blue>:<cyan>{line}</cyan> | "
    "<level>{message}</level>"
)

# 添加控制台输出 handler
__inside_rename_logger.add(
    sys.stderr,
    format=log_format,
    level="DEBUG",
    colorize=True,
    diagnose=True
)





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
    
    # Format string with clear field separation
    FORMAT = (
        "[%(asctime)s] "
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


# Global configuration
_logging_configured = False
_log_level = logging.INFO
_log_dir: Optional[Path] = None


def setup_logging(
    level: int = logging.INFO,
    log_dir: Optional[str] = None,
    log_to_file: bool = True,
    log_to_console: bool = True,
    use_colors: bool = True,
    use_json: bool = False,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
) -> None:
    """
    Setup global logging configuration.
    
    This should be called once at application startup.
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory for log files (default: ./logs)
        log_to_file: Whether to log to file
        log_to_console: Whether to log to console
        use_colors: Whether to use colors in console output
        use_json: Whether to use JSON format for file output
        max_bytes: Max size of each log file before rotation
        backup_count: Number of backup files to keep
    """
    global _logging_configured, _log_level, _log_dir
    
    if _logging_configured:
        return
    
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
    
    # Remove existing handlers
    root_logger.handlers.clear()
    
    # Console handler
    if log_to_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_formatter = StandardFormatter(use_colors=use_colors)
        console_handler.setFormatter(console_formatter)
        root_logger.addHandler(console_handler)
    
    # File handler
    if log_to_file:
        log_file = _log_dir / "agent_x1.log"
        
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        
        if use_json:
            file_formatter = JSONFormatter()
        else:
            file_formatter = StandardFormatter(use_colors=False)
        
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)
        
        # Also create an error-only log file
        error_file = _log_dir / "agent_x1_error.log"
        error_handler = RotatingFileHandler(
            error_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(file_formatter)
        root_logger.addHandler(error_handler)
    
    # Suppress noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    
    _logging_configured = True
    
    # Log that logging is configured
    logger = logging.getLogger(__name__)
    logger.debug(f"Logging configured: level={logging.getLevelName(level)}, log_dir={_log_dir}")


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
        from src.core.logger import get_logger
        
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


#xlog = __inside_rename_logger
xlog = get_logger(__name__) 
