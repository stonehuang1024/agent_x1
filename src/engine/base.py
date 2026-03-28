"""
Base Engine Module - Abstract interface for LLM engines.

Provides the foundational abstraction layer that all LLM engine implementations
must follow. This enables seamless switching between different providers
(OpenAI, Anthropic, Kimi, etc.) via configuration.

Architecture:
    BaseEngine (abstract)
    ├── KimiEngine (OpenAI-compatible API)
    ├── AnthropicEngine (Anthropic-style API)
    └── [Future: OpenAIEngine, GeminiEngine, etc.]
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Any, Optional, Callable, TYPE_CHECKING
from enum import Enum

if TYPE_CHECKING:
    from ..skills.context_manager import SkillContextManager


class ProviderType(Enum):
    """Supported LLM provider types."""
    KIMI = "kimi"                    # OpenAI-compatible Kimi API
    ANTHROPIC = "anthropic"         # Anthropic-style Kimi API
    OPENAI = "openai"               # Native OpenAI API
    GEMINI = "gemini"               # Google Gemini API


@dataclass
class EngineConfig:
    """
    Universal configuration for LLM engines.
    
    This dataclass provides a standardized configuration interface
    that works across all engine implementations. Each engine can
    extend this with provider-specific settings.
    
    Attributes:
        provider: The LLM provider type
        api_key: Authentication key
        base_url: API endpoint URL
        model: Model identifier
        temperature: Sampling temperature (0.0 - 2.0)
        max_tokens: Maximum tokens in response
        timeout: Request timeout in seconds
        max_iterations: Max tool call rounds per query
        system_prompt: Default system prompt
    """
    provider: ProviderType = ProviderType.KIMI
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout: int = 600
    max_iterations: int = 5
    system_prompt: str = (
        "You are a helpful AI assistant with access to tools. "
        "Use the available tools when needed to provide accurate information. "
        "Always respond in the same language as the user's query."
    )
    
    def __post_init__(self):
        """Set default base URLs if not provided."""
        if not self.base_url:
            if self.provider == ProviderType.KIMI:
                self.base_url = "https://api.moonshot.cn/v1"
            elif self.provider == ProviderType.ANTHROPIC:
                self.base_url = "https://api.kimi.com/coding/"
            elif self.provider == ProviderType.OPENAI:
                self.base_url = "https://api.openai.com/v1"
        
        if not self.model:
            if self.provider == ProviderType.KIMI:
                self.model = "kimi-latest"
            elif self.provider == ProviderType.ANTHROPIC:
                self.model = "kimi-k2.5"
            elif self.provider == ProviderType.OPENAI:
                self.model = "gpt-4"


class BaseEngine(ABC):
    """
    Abstract base class for all LLM engines.
    
    This class defines the interface that all engine implementations
    must follow, ensuring consistent behavior regardless of the
    underlying LLM provider.
    
    Usage:
        engine = SomeEngine(config)
        engine.register_tool(tool)
        response = engine.chat("Hello")
    
    Attributes:
        config: Engine configuration
        tools: Dictionary of registered tools
        messages: Conversation history
    """
    
    def __init__(self, config: EngineConfig):
        """
        Initialize the engine with configuration.
        
        Args:
            config: Engine configuration containing API credentials and settings
        """
        self.config = config
        self.tools: Dict[str, "Tool"] = {}
        self.messages: List["Message"] = []
        self._system_prompt: str = config.system_prompt
        self._skill_context: Optional["SkillContextManager"] = None
        self._tool_categories: Dict[str, str] = {}
    
    @property
    def system_prompt(self) -> str:
        """Get the current system prompt."""
        return self._system_prompt
    
    @system_prompt.setter
    def system_prompt(self, value: str) -> None:
        """Set the system prompt."""
        self._system_prompt = value
    
    @property
    def skill_context(self) -> Optional["SkillContextManager"]:
        """Get the skill context manager."""
        return self._skill_context
    
    def set_skill_context(self, ctx: "SkillContextManager") -> None:
        """Attach a SkillContextManager to this engine."""
        self._skill_context = ctx
    
    def set_tool_categories(self, categories: Dict[str, str]) -> None:
        """Set tool-name -> category mapping for skill-based tool filtering."""
        self._tool_categories = categories
    
    def get_effective_system_prompt(self) -> str:
        """Build the system prompt with skill context layers if available."""
        if self._skill_context:
            return self._skill_context.build_system_prompt(self._system_prompt)
        return self._system_prompt
    
    def get_effective_tools(self) -> Dict[str, "Tool"]:
        """Return tools filtered by active skill policy, or all tools."""
        if self._skill_context:
            return self._skill_context.filter_tools(self.tools, self._tool_categories)
        return self.tools
    
    @abstractmethod
    def register_tool(self, tool: "Tool") -> None:
        """
        Register a tool for use in LLM conversations.
        
        Args:
            tool: The Tool instance to register
            
        Raises:
            ValueError: If a tool with the same name is already registered
        """
        pass
    
    def unregister_tool(self, name: str) -> None:
        """
        Remove a registered tool by name.
        
        Args:
            name: The name of the tool to remove
        """
        self.tools.pop(name, None)
    
    def clear_history(self) -> None:
        """Clear the conversation history."""
        self.messages.clear()
    
    def get_conversation_history(self) -> List[Dict[str, Any]]:
        """
        Get the current conversation history.
        
        Returns:
            List of message dictionaries
        """
        return [msg.to_dict() for msg in self.messages]
    
    @abstractmethod
    def call_llm(
        self,
        messages: List["Message"],
        tools: Optional[Dict[str, "Tool"]] = None,
        system_prompt: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Make a single LLM call with messages and optional tools.
        
        This is the new interface for AgentLoop - it performs a single
        LLM invocation without any internal loop logic.
        
        Args:
            messages: List of conversation messages
            tools: Optional dict of tools to make available
            system_prompt: Optional override for system prompt
            
        Returns:
            Response dict with 'content', 'tool_calls', 'usage', etc.
        """
        pass
    
    @abstractmethod
    def chat(self, user_input: str) -> str:
        """
        Process a user message and return the final assistant response.
        
        DEPRECATED: This method will be replaced by AgentLoop.
        Currently maintained for backward compatibility.
        
        Args:
            user_input: The user's message
            
        Returns:
            The assistant's response text
        """
        pass
    
    @abstractmethod
    def _call_llm(self) -> Dict[str, Any]:
        """
        Internal: Make an API call to the LLM service.
        
        Returns:
            Raw API response dictionary
        """
        pass
    
    @abstractmethod
    def _parse_response(self, response: Dict[str, Any]) -> "Message":
        """
        Internal: Parse the LLM API response into a Message object.
        
        Args:
            response: The raw JSON response from the API
            
        Returns:
            A Message object representing the LLM's response
        """
        pass
    
    @abstractmethod
    def _execute_tools(self, tool_calls: List[Dict[str, Any]]) -> List["Message"]:
        """
        Internal: Execute a list of tool calls and return result messages.
        
        Args:
            tool_calls: List of tool call dictionaries from LLM
            
        Returns:
            List of tool result messages
        """
        pass
