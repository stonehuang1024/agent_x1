"""
Anthropic Engine Module - Anthropic-style API implementation.

This engine uses Kimi's Anthropic-compatible API endpoint:
- Base URL: https://api.kimi.com/coding/
- Endpoint: /v1/messages
- Auth: x-api-key header
- Requires anthropic-version header
"""

import json
import logging
import requests
import time
from typing import Dict, List, Any, Optional

from .base import BaseEngine, EngineConfig, ProviderType
from ..core.models import Message, Role
from ..core.tool import Tool
from ..core.session_manager import get_session_manager, SessionManager

logger = logging.getLogger(__name__)


class AnthropicEngine(BaseEngine):
    """
    Kimi LLM Engine using Anthropic-compatible API.
    
    This engine implements the BaseEngine interface for Kimi's
    Anthropic-compatible API. It converts between internal message
    format and Anthropic's message format.
    
    Example:
        config = EngineConfig(
            provider=ProviderType.ANTHROPIC,
            api_key="your-api-key",
            model="kimi-k2.5"
        )
        engine = AnthropicEngine(config)
        
        # Register tools
        engine.register_tool(weather_tool)
        
        # Chat
        response = engine.chat("What's the weather in Beijing?")
    
    Attributes:
        config: EngineConfig with provider=ANTHROPIC
        tools: Dictionary of registered Tool instances
        messages: Conversation history as Message objects
    """
    
    def __init__(self, config: EngineConfig):
        """
        Initialize the Anthropic Engine.
        
        Args:
            config: Engine configuration. Must have provider=ANTHROPIC
            
        Raises:
            ValueError: If provider is not ANTHROPIC
        """
        if config.provider != ProviderType.ANTHROPIC:
            raise ValueError(f"AnthropicEngine requires provider=ANTHROPIC, got {config.provider}")
        
        super().__init__(config)
        logger.info(f"[AnthropicEngine] Initialized with model: {config.model}")
        logger.info(f"[AnthropicEngine] Base URL: {config.base_url}")
    
    def register_tool(self, tool: Tool) -> None:
        """
        Register a tool for use in LLM conversations.
        
        Args:
            tool: The Tool instance to register
            
        Raises:
            ValueError: If a tool with the same name is already registered
        """
        if tool.name in self.tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        
        self.tools[tool.name] = tool
        logger.info(f"[AnthropicEngine] Registered tool: {tool.name}")
    
    def _truncate_messages(self, max_messages: int = 20) -> None:
        """
        Truncate message history to prevent API payload from growing too large.
        Keeps system context (first message if system) and most recent messages.
        
        Args:
            max_messages: Maximum messages to keep (default 20)
        """
        if len(self.messages) <= max_messages:
            return
        
        # Keep system message if present at start, then most recent messages
        keep_count = max_messages - 1
        self.messages = self.messages[:1] + self.messages[-keep_count:]
        logger.info(f"[AnthropicEngine] Truncated messages to {len(self.messages)} (was {len(self.messages) + keep_count - 1})")
    
    def chat(self, user_input: str) -> str:
        """
        Process a user message and return the final assistant response.
        
        Args:
            user_input: The user's message
            
        Returns:
            The assistant's response text
        """
        logger.info("=" * 60)
        logger.info(f"[AnthropicEngine] User input: {user_input[:100]}...")
        logger.info(f"[AnthropicEngine] Registered tools: {list(self.tools.keys())}")
        
        # Truncate message history before adding new message
        self._truncate_messages(max_messages=20)
        
        self.messages.append(Message(role=Role.USER.value, content=user_input))
        
        iteration = 0
        self._current_chat_iterations = 0
        session_manager = get_session_manager()
        if session_manager:
            session_manager.record_operation_step(f"User query: {user_input[:80]}...")
        
        while iteration < self.config.max_iterations:
            iteration += 1
            self._current_chat_iterations = iteration
            logger.info(f"[AnthropicEngine] --- Iteration {iteration}/{self.config.max_iterations} ---")
            
            anthropic_msgs = self._convert_to_anthropic_format(self.messages)
            effective_tools = self.get_effective_tools()
            anthropic_tools = self._convert_tools_to_anthropic(effective_tools)
            
            # Track timing for session logging
            start_time = time.time()
            response = self._call_anthropic_api(anthropic_msgs, anthropic_tools)
            duration_ms = (time.time() - start_time) * 1000
            
            # Log to session manager
            if session_manager:
                session_manager.log_llm_interaction(
                    iteration=iteration,
                    messages=anthropic_msgs,
                    tools=anthropic_tools,
                    response=response,
                    duration_ms=duration_ms
                )
            
            if response.get("error"):
                error_msg = f"API Error: {response.get('message', 'Unknown error')}"
                logger.error(f"[AnthropicEngine] {error_msg}")
                return error_msg
            
            assistant_msg = self._parse_anthropic_response(response)
            self.messages.append(assistant_msg)
            
            stop_reason = response.get("stop_reason", "unknown")
            logger.info(f"[AnthropicEngine] Stop reason: {stop_reason}")
            
            usage = response.get("usage", {})
            logger.info(f"[AnthropicEngine] Tokens - Input: {usage.get('input_tokens', 'N/A')}, "
                       f"Output: {usage.get('output_tokens', 'N/A')}, "
                       f"Total: {usage.get('total_tokens', 'N/A')}")
            
            if assistant_msg.tool_calls:
                logger.info(f"[AnthropicEngine] LLM requested {len(assistant_msg.tool_calls)} tool call(s)")
                tool_results = self._execute_tools(assistant_msg.tool_calls)
                # Record tool execution in session
                if session_manager:
                    for tc in assistant_msg.tool_calls:
                        tool_name = tc.get("function", {}).get("name", "unknown")
                        session_manager.record_operation_step(f"Executed tool: {tool_name}")
                for result in tool_results:
                    self.messages.append(result)
                continue
            else:
                logger.info("[AnthropicEngine] LLM provided direct response")
                return assistant_msg.content or ""
        
        logger.warning("[AnthropicEngine] Max iterations reached")
        return "Maximum iterations reached."
    
    def _convert_to_anthropic_format(self, messages: List[Message]) -> List[Dict[str, Any]]:
        """
        Convert internal Message format to Anthropic format.
        
        Args:
            messages: List of Message objects
            
        Returns:
            List of Anthropic-formatted message dictionaries
        """
        anthropic_msgs = []
        
        for msg in messages:
            if msg.role == Role.SYSTEM.value:
                continue
            
            if msg.role == Role.USER.value:
                anthropic_msgs.append({
                    "role": "user",
                    "content": msg.content
                })
            
            elif msg.role == Role.ASSISTANT.value:
                content = []
                
                if msg.content:
                    content.append({
                        "type": "text",
                        "text": msg.content
                    })
                
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        content.append({
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": tc.get("function", {}).get("name", ""),
                            "input": json.loads(tc.get("function", {}).get("arguments", "{}"))
                        })
                
                anthropic_msgs.append({
                    "role": "assistant",
                    "content": content
                })
            
            elif msg.role == Role.TOOL.value:
                anthropic_msgs.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id,
                        "content": msg.content
                    }]
                })
        
        return anthropic_msgs
    
    def _convert_tools_to_anthropic(
        self, tools: Optional[Dict[str, "Tool"]] = None
    ) -> List[Dict[str, Any]]:
        """
        Convert tools to Anthropic format.
        
        Args:
            tools: Specific tool dict to convert. Defaults to all registered tools.
        
        Returns:
            List of Anthropic-formatted tool schemas
        """
        source = tools if tools is not None else self.tools
        anthropic_tools = []
        
        for tool in source.values():
            anthropic_tool = {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.parameters
            }
            anthropic_tools.append(anthropic_tool)
        
        return anthropic_tools
    
    def _call_llm(self) -> Dict[str, Any]:
        """
        Implement abstract method from BaseEngine.
        Converts internal messages to Anthropic format and calls API.
        """
        anthropic_msgs = self._convert_to_anthropic_format(self.messages)
        effective_tools = self.get_effective_tools()
        anthropic_tools = self._convert_tools_to_anthropic(effective_tools)
        return self._call_anthropic_api(anthropic_msgs, anthropic_tools)
    
    def _parse_response(self, response: Dict[str, Any]) -> Message:
        """
        Implement abstract method from BaseEngine.
        Parses Anthropic API response to Message.
        """
        return self._parse_anthropic_response(response)
    
    def _call_anthropic_api(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Make API call to Anthropic-style Kimi endpoint.
        
        Args:
            messages: Anthropic-formatted messages
            tools: Anthropic-formatted tools
            
        Returns:
            Raw API response dictionary
        """
        base_url = self.config.base_url.rstrip('/')
        if base_url.endswith('/v1'):
            url = f"{base_url}/messages"
        else:
            url = f"{base_url}/v1/messages"
        
        headers = {
            "x-api-key": self.config.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }
        
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "system": self.get_effective_system_prompt(),
            "messages": messages
        }
        
        if tools:
            payload["tools"] = tools
        
        logger.info(f"[AnthropicEngine] API Request: {len(messages)} messages, {len(tools)} tools")
        
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.config.timeout
            )
            
            logger.info(f"[AnthropicEngine] API Response: {response.status_code}")
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"[AnthropicEngine] API Error: {response.status_code} - {response.text[:500]}")
                return {
                    "error": True,
                    "status_code": response.status_code,
                    "message": response.text
                }
                
        except requests.RequestException as e:
            logger.error(f"[AnthropicEngine] Request failed: {e}")
            return {
                "error": True,
                "message": str(e)
            }
    
    def _parse_anthropic_response(self, response: Dict[str, Any]) -> Message:
        """
        Parse Anthropic response to Message.
        
        Args:
            response: Raw Anthropic API response
            
        Returns:
            Message object
        """
        content_blocks = response.get("content", [])
        
        text_content = ""
        tool_calls = []
        
        for block in content_blocks:
            block_type = block.get("type")
            
            if block_type == "text":
                text_content += block.get("text", "")
            
            elif block_type == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {}))
                    }
                })
        
        return Message(
            role=Role.ASSISTANT.value,
            content=text_content,
            tool_calls=tool_calls if tool_calls else None
        )
    
    def _execute_tools(self, tool_calls: List[Dict[str, Any]]) -> List[Message]:
        """
        Execute tool calls and return results.
        
        Args:
            tool_calls: List of tool call dictionaries
            
        Returns:
            List of tool result messages
        """
        results: List[Message] = []
        
        logger.info(f"[AnthropicEngine] Executing {len(tool_calls)} tool call(s)")
        
        for i, call in enumerate(tool_calls, 1):
            call_id = call.get("id", "unknown")
            function = call.get("function", {})
            tool_name = function.get("name", "unknown")
            arguments = function.get("arguments", "{}")
            
            logger.info(f"[AnthropicEngine] Tool {i}/{len(tool_calls)}: {tool_name}({arguments})")
            
            if tool_name in self.tools:
                tool = self.tools[tool_name]
                try:
                    output = tool.execute(arguments)
                    logger.info(f"[AnthropicEngine] Tool {i} result: {output[:200]}...")
                except Exception as e:
                    output = json.dumps({"error": str(e)})
                    logger.error(f"[AnthropicEngine] Tool {i} error: {e}")
            else:
                output = json.dumps({"error": f"Tool '{tool_name}' not found"})
                logger.error(f"[AnthropicEngine] Unknown tool: {tool_name}")
            
            tool_result = Message(
                role=Role.TOOL.value,
                content=output,
                tool_call_id=call_id,
                name=tool_name
            )
            results.append(tool_result)
        
        return results
