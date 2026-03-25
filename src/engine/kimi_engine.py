"""
Kimi Engine Module - OpenAI-compatible API implementation.

This engine uses Kimi's OpenAI-compatible API endpoint:
- Base URL: https://api.moonshot.cn/v1
- Endpoint: /chat/completions
- Auth: Bearer token

Supports both legacy Kimi models and the latest K2 series.
"""

import json
import logging
import requests
from typing import Dict, List, Any, Optional

from .base import BaseEngine, EngineConfig, ProviderType
from ..core.models import Message, Role
from ..core.tool import Tool

logger = logging.getLogger(__name__)


class KimiEngine(BaseEngine):
    """
    Kimi LLM Engine using OpenAI-compatible API.
    
    This engine implements the BaseEngine interface for Kimi's
    OpenAI-compatible API. It supports tool calling, multi-turn
    conversations, and streaming responses.
    
    Example:
        config = EngineConfig(
            provider=ProviderType.KIMI,
            api_key="your-api-key",
            model="kimi-latest"
        )
        engine = KimiEngine(config)
        
        # Register tools
        engine.register_tool(weather_tool)
        
        # Chat
        response = engine.chat("What's the weather in Beijing?")
    
    Attributes:
        config: EngineConfig with provider=KIMI
        tools: Dictionary of registered Tool instances
        messages: Conversation history as Message objects
    """
    
    def __init__(self, config: EngineConfig):
        """
        Initialize the Kimi Engine.
        
        Args:
            config: Engine configuration. Must have provider=KIMI
            
        Raises:
            ValueError: If provider is not KIMI
        """
        if config.provider != ProviderType.KIMI:
            raise ValueError(f"KimiEngine requires provider=KIMI, got {config.provider}")
        
        super().__init__(config)
        logger.info(f"[KimiEngine] Initialized with model: {config.model}")
        logger.info(f"[KimiEngine] Base URL: {config.base_url}")
    
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
        logger.info(f"[KimiEngine] Registered tool: {tool.name}")
    
    def chat(self, user_input: str) -> str:
        """
        Process a user message and return the final assistant response.
        
        This method orchestrates the conversation flow:
        1. Add user message to history
        2. Loop up to max_iterations:
           - Call LLM with current history and tools
           - If tool calls: execute them and add results to history
           - If no tool calls: return the response content
        
        Args:
            user_input: The user's message
            
        Returns:
            The assistant's response text
        """
        logger.info("=" * 60)
        logger.info(f"[KimiEngine] User input: {user_input[:100]}...")
        logger.info(f"[KimiEngine] Registered tools: {list(self.tools.keys())}")
        
        self.messages.append(Message(role=Role.USER.value, content=user_input))
        
        iteration = 0
        while iteration < self.config.max_iterations:
            iteration += 1
            logger.info(f"[KimiEngine] --- Iteration {iteration}/{self.config.max_iterations} ---")
            
            response = self._call_llm()
            assistant_msg = self._parse_response(response)
            self.messages.append(assistant_msg)
            
            finish_reason = response.get("choices", [{}])[0].get("finish_reason", "unknown")
            logger.info(f"[KimiEngine] LLM finish_reason: {finish_reason}")
            
            if assistant_msg.tool_calls:
                logger.info(f"[KimiEngine] LLM requested {len(assistant_msg.tool_calls)} tool call(s)")
                tool_results = self._execute_tools(assistant_msg.tool_calls)
                for result in tool_results:
                    self.messages.append(result)
                continue
            else:
                logger.info("[KimiEngine] LLM provided direct response")
                return assistant_msg.content or ""
        
        logger.warning("[KimiEngine] Max iterations reached")
        return "Maximum number of tool call iterations reached."
    
    def _call_llm(self) -> Dict[str, Any]:
        """
        Make an API call to the Kimi LLM service.
        
        Returns:
            Raw API response dictionary
        """
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json"
        }
        
        system_msg = Message(role=Role.SYSTEM.value, content=self.get_effective_system_prompt())
        all_messages = [system_msg] + self.messages
        
        effective_tools = self.get_effective_tools()
        tools = [tool.get_schema() for tool in effective_tools.values()]
        
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "messages": [msg.to_dict() for msg in all_messages],
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens
        }
        
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        
        logger.info(f"[KimiEngine] API Request: {len(all_messages)} messages, {len(tools)} tools")
        
        try:
            response = requests.post(
                f"{self.config.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.config.timeout
            )
            response.raise_for_status()
            
            resp_json = response.json()
            usage = resp_json.get("usage", {})
            logger.info(f"[KimiEngine] Tokens - Prompt: {usage.get('prompt_tokens', 'N/A')}, "
                       f"Completion: {usage.get('completion_tokens', 'N/A')}, "
                       f"Total: {usage.get('total_tokens', 'N/A')}")
            
            return resp_json
            
        except requests.RequestException as e:
            logger.error(f"[KimiEngine] API request failed: {e}")
            return {
                "error": True,
                "message": str(e),
                "choices": [{
                    "message": {"role": "assistant", "content": f"API Error: {str(e)}"}
                }]
            }
    
    def _parse_response(self, response: Dict[str, Any]) -> Message:
        """
        Parse the LLM API response into a Message object.
        
        Args:
            response: The raw JSON response from the API
            
        Returns:
            A Message object representing the LLM's response
        """
        if response.get("error"):
            return Message(
                role=Role.ASSISTANT.value,
                content=f"Error: {response.get('message', 'Unknown error')}"
            )
        
        try:
            choice = response["choices"][0]
            message = choice["message"]
            
            return Message(
                role=Role.ASSISTANT.value,
                content=message.get("content"),
                tool_calls=message.get("tool_calls")
            )
        except (KeyError, IndexError) as e:
            return Message(
                role=Role.ASSISTANT.value,
                content=f"Failed to parse response: {str(e)}"
            )
    
    def _execute_tools(self, tool_calls: List[Dict[str, Any]]) -> List[Message]:
        """
        Execute a list of tool calls and return result messages.
        
        Args:
            tool_calls: List of tool call dictionaries from LLM
            
        Returns:
            List of tool result messages
        """
        results: List[Message] = []
        
        logger.info(f"[KimiEngine] Executing {len(tool_calls)} tool call(s)")
        
        for i, call in enumerate(tool_calls, 1):
            call_id = call.get("id", "unknown")
            function = call.get("function", {})
            tool_name = function.get("name", "unknown")
            arguments = function.get("arguments", "{}")
            
            logger.info(f"[KimiEngine] Tool {i}/{len(tool_calls)}: {tool_name}({arguments})")
            
            if tool_name in self.tools:
                tool = self.tools[tool_name]
                try:
                    output = tool.execute(arguments)
                    result_preview = output[:200] + "..." if len(output) > 200 else output
                    logger.info(f"[KimiEngine] Tool {i} result: {result_preview}")
                except Exception as e:
                    output = json.dumps({"error": str(e)}, ensure_ascii=False)
                    logger.error(f"[KimiEngine] Tool {i} error: {e}")
            else:
                output = json.dumps({"error": f"Tool '{tool_name}' not found"}, ensure_ascii=False)
                logger.error(f"[KimiEngine] Unknown tool: {tool_name}")
            
            tool_result = Message(
                role=Role.TOOL.value,
                content=output,
                tool_call_id=call_id,
                name=tool_name
            )
            results.append(tool_result)
        
        logger.info(f"[KimiEngine] Completed {len(results)} tool call(s)")
        return results
