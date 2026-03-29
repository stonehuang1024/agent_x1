"""Tests for AnthropicEngine — API error logging and message format conversion.

Bug classes covered:
- call_llm logs only status code on error, discarding response body
  (makes 400 errors impossible to diagnose)
- _convert_to_anthropic_format produces tool_result with tool_use_id=None
  when tool_call_id is missing from Message (causes Anthropic API 400)
"""

import json
import logging
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from src.core.models import Message, Role
from src.engine.anthropic_engine import AnthropicEngine
from src.engine.base import EngineConfig, ProviderType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def engine_config():
    """Minimal AnthropicEngine config for unit tests."""
    return EngineConfig(
        provider=ProviderType.ANTHROPIC,
        api_key="test-key-000",
        base_url="https://test.example.com",
        model="test-model",
        max_tokens=1024,
        temperature=0.0,
        timeout=10,
    )


@pytest.fixture
def engine(engine_config):
    """AnthropicEngine instance with test config."""
    return AnthropicEngine(engine_config)


# ---------------------------------------------------------------------------
# 1. call_llm error logging — response body must be captured
#    Bug: only status code was logged, response body discarded
# ---------------------------------------------------------------------------

class TestCallLlmErrorLogging:
    """Catches: call_llm discarding response body on non-200 status codes."""

    def test_400_error_logs_response_body(self, engine, caplog):
        """When API returns 400, the response body must appear in the log.
        Bug: logger.error only included status_code, not response.text,
        making it impossible to diagnose format errors like missing tool_use_id."""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"type":"error","error":{"type":"invalid_request_error","message":"tool_use_id is required"}}'
        mock_response.content = mock_response.text.encode()
        mock_response.json.return_value = json.loads(mock_response.text)

        with patch("requests.post", return_value=mock_response):
            with caplog.at_level(logging.ERROR):
                result = engine.call_llm(
                    messages=[Message.user("test")],
                    system_prompt="You are helpful",
                )

        assert result["finish_reason"] == "error"
        assert "400" in result["content"]

        # The critical check: response body must be in the log
        error_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_logs) >= 1, "No ERROR log emitted for 400 response"
        error_text = error_logs[0].getMessage()
        assert "tool_use_id" in error_text or "invalid_request" in error_text, (
            f"Response body not included in error log: {error_text!r}. "
            "Without the body, 400 errors are impossible to diagnose."
        )

    def test_500_error_logs_response_body(self, engine, caplog):
        """Server errors (500) must also log the response body."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = '{"error":"internal server error"}'
        mock_response.content = mock_response.text.encode()

        with patch("requests.post", return_value=mock_response):
            with caplog.at_level(logging.ERROR):
                result = engine.call_llm(
                    messages=[Message.user("test")],
                    system_prompt="sys",
                )

        assert result["finish_reason"] == "error"
        error_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("internal server error" in r.getMessage() for r in error_logs), (
            "500 response body not logged"
        )

    def test_error_response_body_truncated_at_500_chars(self, engine, caplog):
        """Very long error bodies must be truncated to avoid log flooding."""
        long_body = "x" * 1000
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = long_body
        mock_response.content = long_body.encode()

        with patch("requests.post", return_value=mock_response):
            with caplog.at_level(logging.ERROR):
                engine.call_llm(
                    messages=[Message.user("test")],
                    system_prompt="sys",
                )

        error_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_logs) >= 1
        logged_text = error_logs[0].getMessage()
        # The body should be truncated — logged text should be < 1000 chars of body
        # (response.text[:500] in the implementation)
        assert len(logged_text) < 800, (
            f"Error body not truncated: logged {len(logged_text)} chars"
        )


# ---------------------------------------------------------------------------
# 2. _convert_to_anthropic_format — tool_use_id must not be None
#    Bug: when tool_call_id is None, Anthropic API rejects with 400
# ---------------------------------------------------------------------------

class TestConvertToAnthropicFormat:
    """Catches: tool_result blocks with tool_use_id=None."""

    def test_tool_message_with_tool_call_id(self, engine):
        """Tool messages with tool_call_id must produce valid tool_result blocks."""
        messages = [
            Message.user("Do something"),
            Message.assistant(
                content="Using tool",
                tool_calls=[{"id": "call_123", "function": {"name": "read_file", "arguments": '{"path":"/tmp/x"}'}}],
            ),
            Message.tool(content="file data", tool_call_id="call_123", name="read_file"),
        ]

        result = engine._convert_to_anthropic_format(messages)

        # Find the tool_result block
        tool_result_msgs = [
            m for m in result
            if m["role"] == "user" and isinstance(m["content"], list)
            and any(b.get("type") == "tool_result" for b in m["content"])
        ]
        assert len(tool_result_msgs) == 1
        tool_result = tool_result_msgs[0]["content"][0]
        assert tool_result["tool_use_id"] == "call_123", (
            f"tool_use_id should be 'call_123', got {tool_result['tool_use_id']!r}"
        )

    def test_tool_message_without_tool_call_id_produces_none(self, engine):
        """Tool messages with tool_call_id=None produce tool_use_id=None.
        This is the bug scenario — _load_history used to drop tool_call_id,
        resulting in None here, which Anthropic API rejects with 400."""
        messages = [
            Message(role=Role.TOOL.value, content="result", tool_call_id=None),
        ]

        result = engine._convert_to_anthropic_format(messages)

        assert len(result) == 1
        tool_result = result[0]["content"][0]
        # This documents the current behavior — tool_use_id will be None
        # The fix is in _load_history, not here
        assert tool_result["tool_use_id"] is None, (
            "Expected None when tool_call_id is not set"
        )

    def test_assistant_tool_use_ids_match_tool_result_ids(self, engine):
        """The id in tool_use blocks must match tool_use_id in tool_result blocks.
        This is the end-to-end linkage that Anthropic API validates."""
        messages = [
            Message.user("Run tools"),
            Message.assistant(
                content="",
                tool_calls=[
                    {"id": "call_A", "function": {"name": "tool_a", "arguments": "{}"}},
                    {"id": "call_B", "function": {"name": "tool_b", "arguments": "{}"}},
                ],
            ),
            Message.tool(content="result_a", tool_call_id="call_A", name="tool_a"),
            Message.tool(content="result_b", tool_call_id="call_B", name="tool_b"),
            Message.assistant(content="Done"),
        ]

        result = engine._convert_to_anthropic_format(messages)

        # Extract tool_use ids from assistant message
        assistant_msg = result[1]  # index 0 is user, 1 is assistant
        tool_use_ids = {
            b["id"] for b in assistant_msg["content"] if b["type"] == "tool_use"
        }

        # Extract tool_result ids
        tool_result_ids = set()
        for msg in result:
            if msg["role"] == "user" and isinstance(msg["content"], list):
                for block in msg["content"]:
                    if block.get("type") == "tool_result":
                        tool_result_ids.add(block["tool_use_id"])

        assert tool_use_ids == tool_result_ids == {"call_A", "call_B"}, (
            f"tool_use ids {tool_use_ids} don't match tool_result ids {tool_result_ids}. "
            "Anthropic API requires these to match."
        )

    def test_system_messages_excluded(self, engine):
        """System messages must be excluded from Anthropic message format."""
        messages = [
            Message.system("You are helpful"),
            Message.user("Hello"),
        ]

        result = engine._convert_to_anthropic_format(messages)

        assert len(result) == 1
        assert result[0]["role"] == "user"
