"""
Tests for universal Tool timeout and output truncation safety mechanisms.

Covers:
- Timeout triggers and defaults
- Output truncation and marking
- Normal execution unaffected
- Exception handling preserved
- JSON parse error handling
- Schema metadata helpers
"""

import json
import time
import pytest

from src.core.tool import (
    Tool,
    GLOBAL_DEFAULT_TIMEOUT,
    GLOBAL_DEFAULT_MAX_OUTPUT,
)


# ---------------------------------------------------------------------------
# Helper functions used as tool targets
# ---------------------------------------------------------------------------

def _fast_func(x: int = 1) -> dict:
    """Returns immediately."""
    return {"result": x * 2}


def _slow_func(seconds: float = 5.0) -> dict:
    """Sleeps for *seconds* then returns."""
    time.sleep(seconds)
    return {"result": "done"}


def _large_output_func(size: int = 100000) -> dict:
    """Returns a dict whose JSON repr is at least *size* chars."""
    return {"data": "x" * size}


def _error_func() -> dict:
    """Always raises."""
    raise RuntimeError("intentional error")


def _bad_serialize_func() -> object:
    """Returns an object that is not JSON-serializable."""
    return object()


# ---------------------------------------------------------------------------
# Tool fixtures
# ---------------------------------------------------------------------------

def _make_tool(func, timeout_seconds=None, max_output_chars=None, is_readonly=False, name=None):
    return Tool(
        name=name or func.__name__,
        description="test tool",
        parameters={"type": "object", "properties": {}, "required": []},
        func=func,
        timeout_seconds=timeout_seconds,
        max_output_chars=max_output_chars,
        is_readonly=is_readonly,
    )


# ---------------------------------------------------------------------------
# Tests — Timeout
# ---------------------------------------------------------------------------

class TestToolTimeout:
    def test_timeout_triggers(self):
        """A slow function that exceeds its timeout returns an error JSON."""
        tool = _make_tool(_slow_func, timeout_seconds=1)
        result = json.loads(tool.execute('{"seconds": 5.0}'))
        assert "error" in result
        assert "timed out" in result["error"]
        assert result["timeout_seconds"] == 1

    def test_timeout_default_used_when_none(self):
        """When timeout_seconds is None, effective timeout is GLOBAL_DEFAULT."""
        tool = _make_tool(_fast_func)
        assert tool.get_effective_timeout() == GLOBAL_DEFAULT_TIMEOUT

    def test_explicit_timeout_respected(self):
        tool = _make_tool(_fast_func, timeout_seconds=42)
        assert tool.get_effective_timeout() == 42

    def test_normal_execution_unaffected(self):
        """A fast function executes normally and returns correct result."""
        tool = _make_tool(_fast_func, timeout_seconds=10)
        result = json.loads(tool.execute('{"x": 7}'))
        assert result == {"result": 14}


# ---------------------------------------------------------------------------
# Tests — Output Truncation
# ---------------------------------------------------------------------------

class TestToolOutputTruncation:
    def test_output_truncated_and_marked(self):
        """Output exceeding max_output_chars is truncated with a marker."""
        tool = _make_tool(_large_output_func, max_output_chars=500)
        output = tool.execute('{"size": 100000}')
        assert len(output) < 100000
        assert "[OUTPUT TRUNCATED" in output

    def test_output_default_used_when_none(self):
        tool = _make_tool(_fast_func)
        assert tool.get_effective_max_output() == GLOBAL_DEFAULT_MAX_OUTPUT

    def test_explicit_max_output_respected(self):
        tool = _make_tool(_fast_func, max_output_chars=999)
        assert tool.get_effective_max_output() == 999

    def test_small_output_not_truncated(self):
        """Output within limits is returned unchanged (no truncation marker)."""
        tool = _make_tool(_fast_func, max_output_chars=10000)
        output = tool.execute('{"x": 1}')
        assert "[OUTPUT TRUNCATED" not in output
        result = json.loads(output)
        assert result == {"result": 2}


# ---------------------------------------------------------------------------
# Tests — Error Handling
# ---------------------------------------------------------------------------

class TestToolErrorHandling:
    def test_exception_returns_error_json(self):
        tool = _make_tool(_error_func, timeout_seconds=5)
        result = json.loads(tool.execute("{}"))
        assert "error" in result
        assert "intentional error" in result["details"]

    def test_json_parse_error(self):
        tool = _make_tool(_fast_func)
        result = json.loads(tool.execute("NOT_JSON"))
        assert "error" in result
        assert "Invalid JSON" in result["error"]

    def test_bad_arguments_type_error(self):
        tool = _make_tool(_fast_func, timeout_seconds=5)
        result = json.loads(tool.execute('{"unknown_param": 1}'))
        assert "error" in result

    def test_serialization_failure(self):
        tool = _make_tool(_bad_serialize_func, timeout_seconds=5)
        result = json.loads(tool.execute("{}"))
        assert "error" in result
        assert "serialize" in result["error"].lower() or "serialize" in result.get("details", "").lower()


# ---------------------------------------------------------------------------
# Tests — Schema / Metadata
# ---------------------------------------------------------------------------

class TestToolMetadata:
    def test_is_readonly_default_false(self):
        tool = _make_tool(_fast_func)
        assert tool.is_readonly is False

    def test_is_readonly_set_true(self):
        tool = _make_tool(_fast_func, is_readonly=True)
        assert tool.is_readonly is True

    def test_schema_structure(self):
        tool = _make_tool(_fast_func, name="my_tool")
        schema = tool.get_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "my_tool"

    def test_effective_helpers(self):
        tool = _make_tool(_fast_func, timeout_seconds=30, max_output_chars=8000)
        assert tool.get_effective_timeout() == 30
        assert tool.get_effective_max_output() == 8000


# ---------------------------------------------------------------------------
# Tests — Backward Compatibility
# ---------------------------------------------------------------------------

class TestToolBackwardCompat:
    def test_old_style_construction(self):
        """Tool constructed without new params still works."""
        tool = Tool(
            name="compat_test",
            description="test",
            parameters={"type": "object", "properties": {}, "required": []},
            func=_fast_func,
        )
        result = json.loads(tool.execute('{"x": 3}'))
        assert result == {"result": 6}
        assert tool.timeout_seconds is None
        assert tool.max_output_chars is None
        assert tool.is_readonly is False
