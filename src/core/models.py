"""
Core Data Models - Message structures and enumerations.

This module defines the fundamental data structures used throughout
the agent system for LLM communication and tool interactions.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Dict, List, Optional, Any


class Role(Enum):
    """
    Enumeration of message roles in LLM conversations.
    
    Standard conversation flow:
        1. SYSTEM: Sets assistant behavior and capabilities
        2. USER: Human input/queries
        3. ASSISTANT: Model responses (may include tool_calls)
        4. TOOL: Tool execution results (referenced by tool_call_id)
    """
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    """
    Single message in conversation history.
    
    Compatible with OpenAI/Kimi API format. Supports both regular
    content messages and tool-related messages.
    
    Attributes:
        role: Message role (system/user/assistant/tool)
        content: Text content (optional for tool calls)
        tool_calls: Tool calls from assistant
        tool_call_id: Unique ID for tool result messages
        name: Tool name (for tool messages)
        
    Example:
        # User message
        Message(role=Role.USER.value, content="What's the weather?")
        
        # Assistant with tool call
        Message(
            role=Role.ASSISTANT.value,
            tool_calls=[{"id": "call_1", "function": {"name": "get_weather", ...}}]
        )
        
        # Tool result
        Message(
            role=Role.TOOL.value,
            content='{"temperature": 22}',
            tool_call_id="call_1",
            name="get_weather"
        )
    """
    role: str
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to API-compatible dictionary.
        
        Returns:
            Dictionary with None values removed
        """
        result: Dict[str, Any] = {"role": self.role}
        
        if self.content is not None:
            result["content"] = self.content
        if self.tool_calls is not None:
            result["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            result["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            result["name"] = self.name
            
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """
        Create Message from dictionary.
        
        Args:
            data: Dictionary with message fields
            
        Returns:
            New Message instance
        """
        return cls(
            role=data.get("role", Role.USER.value),
            content=data.get("content"),
            tool_calls=data.get("tool_calls"),
            tool_call_id=data.get("tool_call_id"),
            name=data.get("name")
        )
