"""
Core Module - Foundation of the Agent System.

Provides fundamental components for the LLM agent:
- Models: Message structures and role enumerations
- Tool: Tool wrapper and registry for function calling
- Config: Configuration management
- Logger: Standardized logging

Usage:
    from src.core import Message, Role, Tool, AppConfig, get_logger
    
    # Create message
    msg = Message(role=Role.USER.value, content="Hello")
    
    # Configure
    config = AppConfig()
    
    # Log
    logger = get_logger(__name__)
    logger.info("Starting")
"""

from .models import Message, Role
from .tool import Tool, ToolRegistry
from .config import (
    AppConfig,
    LLMConfig,
    PathConfig,
    ToolSafetyConfig,
    ProviderType,
    load_config,
    create_default_config_file
)
from .edit_manager import (
    DiffParser,
    SearchEngine,
    EditApplier,
    FileEditingGuard,
    get_edit_guard,
    reset_edit_guard,
)
from ..util.logger import (
    get_logger,
    setup_logging,
    set_log_level,
    get_log_dir,
    bind_session_to_log,
    debug,
    info,
    warning,
    error,
    critical
)

__all__ = [
    # Models
    "Message",
    "Role",
    # Tool
    "Tool",
    "ToolRegistry",
    # Config
    "AppConfig",
    "LLMConfig",
    "PathConfig",
    "ToolSafetyConfig",
    "ProviderType",
    "load_config",
    "create_default_config_file",
    # EditManager
    "DiffParser",
    "SearchEngine",
    "EditApplier",
    "FileEditingGuard",
    "get_edit_guard",
    "reset_edit_guard",
    # Logger
    "get_logger",
    "setup_logging",
    "set_log_level",
    "get_log_dir",
    "bind_session_to_log",
    "debug",
    "info",
    "warning",
    "error",
    "critical",
]
