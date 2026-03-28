"""
Tool Definition Module - LLM-callable tool wrapper.

Provides the Tool class for transforming Python functions into
LLM-callable tools with JSON Schema parameter definitions.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Dict, Callable, Any, Optional, List
from inspect import signature, Parameter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global safety defaults — fallback when a tool does not specify its own limits.
# These values are initialized from ToolSafetyConfig at startup via
# ``configure_tool_defaults()``.  Until that call, the hardcoded fallbacks
# below are used.
# ---------------------------------------------------------------------------
GLOBAL_DEFAULT_TIMEOUT = 120        # 2 minutes
GLOBAL_DEFAULT_MAX_OUTPUT = 50000   # ~50K chars ≈ 12K tokens


def configure_tool_defaults(
    default_timeout: int = 120,
    default_max_output: int = 50000,
) -> None:
    """Set module-level tool safety defaults from configuration.

    Called once at startup (from ``main.py``) after the config file has
    been loaded.  This avoids hardcoding values while keeping the simple
    module-level constant pattern that ``Tool.execute`` already uses.
    """
    global GLOBAL_DEFAULT_TIMEOUT, GLOBAL_DEFAULT_MAX_OUTPUT
    GLOBAL_DEFAULT_TIMEOUT = default_timeout
    GLOBAL_DEFAULT_MAX_OUTPUT = default_max_output
    logger.info(
        f"[Tool] Global defaults configured: timeout={default_timeout}s, "
        f"max_output={default_max_output} chars"
    )


class Tool:
    """
    Wrapper for Python functions as LLM-callable tools.
    
    Handles:
    1. JSON Schema generation for parameters
    2. Tool execution with argument parsing
    3. Error handling and result serialization
    
    Attributes:
        name: Tool identifier for LLM calls
        description: Human-readable description
        parameters: JSON Schema for parameters
        func: Python function to execute
        schema: Complete tool schema for LLM
        
    Example:
        def get_weather(location: str, unit: str = "celsius") -> dict:
            return {"temp": 22, "condition": "sunny"}
        
        tool = Tool(
            name="get_weather",
            description="Get current weather for a location",
            parameters={
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
                },
                "required": ["location"]
            },
            func=get_weather
        )
    """

    def __init__(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        func: Callable,
        timeout_seconds: Optional[int] = None,
        max_output_chars: Optional[int] = None,
        is_readonly: bool = False,
    ):
        """
        Initialize Tool instance.
        
        Args:
            name: Unique tool identifier
            description: LLM-visible description
            parameters: JSON Schema for parameters
            func: Function to execute
            timeout_seconds: Max execution time (None = GLOBAL_DEFAULT_TIMEOUT)
            max_output_chars: Max output JSON chars (None = GLOBAL_DEFAULT_MAX_OUTPUT)
            is_readonly: Whether tool is idempotent / read-only
        """
        self.name = name
        self.description = description
        self.parameters = parameters
        self.func = func
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars
        self.is_readonly = is_readonly

        # Build complete schema for LLM registration
        self.schema: Dict[str, Any] = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters
            }
        }

    def execute(self, arguments: str) -> str:
        """
        Execute tool with JSON arguments, enforcing timeout and output limits.
        
        Args:
            arguments: JSON string with function arguments
            
        Returns:
            JSON string with result or error
            
        Example:
            result = tool.execute('{"location": "Beijing", "unit": "celsius"}')
            # Returns: '{"temperature": 22, "condition": "晴"}'
        """
        try:
            if isinstance(arguments, dict):
                args: Dict[str, Any] = arguments
            else:
                args: Dict[str, Any] = json.loads(arguments)
        except json.JSONDecodeError as e:
            return json.dumps({
                "error": "Invalid JSON arguments",
                "details": str(e)
            }, ensure_ascii=False)

        timeout = self.timeout_seconds or GLOBAL_DEFAULT_TIMEOUT

        # --- Timeout-protected execution ---
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.func, **args)
                try:
                    result: Any = future.result(timeout=timeout)
                except FuturesTimeoutError:
                    logger.warning(f"[Tool] '{self.name}' timed out after {timeout}s")
                    return json.dumps({
                        "error": f"Tool '{self.name}' timed out after {timeout}s",
                        "timeout_seconds": timeout,
                        "tool": self.name
                    }, ensure_ascii=False)
        except TypeError as e:
            return json.dumps({
                "error": "Invalid function arguments",
                "details": str(e)
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({
                "error": "Tool execution failed",
                "details": str(e),
                "tool": self.name
            }, ensure_ascii=False)

        # --- Serialization ---
        try:
            output = json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            output = json.dumps({
                "error": "Failed to serialize tool result",
                "details": str(e),
                "tool": self.name
            }, ensure_ascii=False)

        # --- Output truncation ---
        max_chars = self.max_output_chars or GLOBAL_DEFAULT_MAX_OUTPUT
        if len(output) > max_chars:
            logger.warning(
                f"[Tool] '{self.name}' output truncated: {len(output)} -> {max_chars} chars"
            )
            output = output[:max_chars] + f'\n... [OUTPUT TRUNCATED at {max_chars} chars]'

        return output

    def get_schema(self) -> Dict[str, Any]:
        """
        Get complete tool schema for LLM registration.
        
        Returns:
            Tool schema dictionary
        """
        return self.schema

    def get_effective_timeout(self) -> int:
        """Return the effective timeout in seconds."""
        return self.timeout_seconds or GLOBAL_DEFAULT_TIMEOUT

    def get_effective_max_output(self) -> int:
        """Return the effective max output chars."""
        return self.max_output_chars or GLOBAL_DEFAULT_MAX_OUTPUT


class ToolRegistry:
    """
    Registry for managing multiple tools.
    
    Provides centralized tool management with registration,
    lookup, and batch operations.
    
    Example:
        registry = ToolRegistry()
        registry.register(weather_tool)
        registry.register(calc_tool)
        
        schemas = registry.get_all_schemas()
        tool = registry.get("get_weather")
    """
    
    def __init__(self):
        """Initialize empty tool registry."""
        self._tools: Dict[str, Tool] = {}
    
    def register(self, tool: Tool) -> None:
        """
        Register a tool.
        
        Args:
            tool: Tool instance to register
            
        Raises:
            ValueError: If tool name already registered
        """
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already registered")
        self._tools[tool.name] = tool
    
    def unregister(self, name: str) -> None:
        """
        Remove a tool by name.
        
        Args:
            name: Tool name to remove
        """
        self._tools.pop(name, None)
    
    def get(self, name: str) -> Optional[Tool]:
        """
        Get tool by name.
        
        Args:
            name: Tool name
            
        Returns:
            Tool instance or None
        """
        return self._tools.get(name)
    
    def get_all_schemas(self) -> List[Dict[str, Any]]:
        """
        Get schemas for all registered tools.
        
        Returns:
            List of tool schemas
        """
        return [tool.get_schema() for tool in self._tools.values()]

    def get_all_tools(self) -> Dict[str, Tool]:
        """
        Get all registered tools as a dictionary.
        
        Returns:
            Dictionary mapping tool names to Tool instances
        """
        return dict(self._tools)
    
    def list_tools(self) -> List[str]:
        """
        Get list of registered tool names.
        
        Returns:
            List of tool names
        """
        return list(self._tools.keys())
    
    def clear(self) -> None:
        """Remove all tools from registry."""
        self._tools.clear()
    
    def __len__(self) -> int:
        """Return number of registered tools."""
        return len(self._tools)
    
    def __contains__(self, name: str) -> bool:
        """Check if tool name is registered."""
        return name in self._tools
