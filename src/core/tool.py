"""
Tool Definition Module - LLM-callable tool wrapper.

Provides the Tool class for transforming Python functions into
LLM-callable tools with JSON Schema parameter definitions.
"""

import json
from typing import Dict, Callable, Any, Optional, List
from inspect import signature, Parameter


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
        func: Callable
    ):
        """
        Initialize Tool instance.
        
        Args:
            name: Unique tool identifier
            description: LLM-visible description
            parameters: JSON Schema for parameters
            func: Function to execute
        """
        self.name = name
        self.description = description
        self.parameters = parameters
        self.func = func

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
        Execute tool with JSON arguments.
        
        Args:
            arguments: JSON string with function arguments
            
        Returns:
            JSON string with result or error
            
        Example:
            result = tool.execute('{"location": "Beijing", "unit": "celsius"}')
            # Returns: '{"temperature": 22, "condition": "晴"}'
        """
        try:
            args: Dict[str, Any] = json.loads(arguments)
            result: Any = self.func(**args)
            return json.dumps(result, ensure_ascii=False)

        except json.JSONDecodeError as e:
            return json.dumps({
                "error": "Invalid JSON arguments",
                "details": str(e)
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

    def get_schema(self) -> Dict[str, Any]:
        """
        Get complete tool schema for LLM registration.
        
        Returns:
            Tool schema dictionary
        """
        return self.schema


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
