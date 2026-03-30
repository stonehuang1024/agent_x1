"""Tests for compression awareness: SystemReminder injection and recall tool.

Verifies:
- SystemReminderBuilder injects compressedContext guidance when has_compression=True
- recall_compressed_messages tool returns correct data or friendly errors
- Tool schema is well-formed
- TOOL_CATEGORIES includes 'context'
"""

import json
import math
from unittest.mock import patch

import pytest

from src.core.models import Message
from src.context.system_reminder import SystemReminderBuilder
from src.context.compression_archive import CompressionArchive
from src.tools.context_tools import (
    recall_compressed_messages,
    set_archive_instance,
    RECALL_COMPRESSED_MESSAGES_TOOL,
    CONTEXT_TOOLS,
)
from src.tools.tool_registry import TOOL_CATEGORIES


# -----------------------------------------------------------------------
# SystemReminderBuilder — compression awareness
# -----------------------------------------------------------------------

class TestSystemReminderCompression:

    def test_no_compression_omits_section(self):
        builder = SystemReminderBuilder()
        result = builder.build("hello", has_compression=False)
        assert "compressedContext" not in result

    def test_has_compression_injects_section(self):
        builder = SystemReminderBuilder()
        result = builder.build("hello", has_compression=True)
        assert "compressedContext" in result
        assert "recall_compressed_messages" in result
        assert "archive_id" in result

    def test_default_has_compression_is_false(self):
        builder = SystemReminderBuilder()
        result = builder.build("hello")
        assert "compressedContext" not in result

    def test_compression_section_inside_system_reminder_tags(self):
        builder = SystemReminderBuilder()
        result = builder.build("hello", has_compression=True)
        # compressedContext should appear between <system-reminder> tags
        sr_start = result.index("<system-reminder>")
        sr_end = result.index("</system-reminder>")
        compressed_pos = result.index("compressedContext")
        assert sr_start < compressed_pos < sr_end


# -----------------------------------------------------------------------
# recall_compressed_messages tool
# -----------------------------------------------------------------------

class TestRecallTool:

    def setup_method(self):
        self.archive = CompressionArchive()
        set_archive_instance(self.archive)

    def teardown_method(self):
        set_archive_instance(None)

    def test_recall_existing_archive(self):
        msgs = [
            Message(role="user", content="What is X?"),
            Message(role="assistant", content="X is a variable."),
        ]
        aid = self.archive.archive(msgs, "llm_summary", (0, 1))
        result = recall_compressed_messages(aid)
        assert "[Archive Recall]" in result
        assert aid in result
        assert "messages=2" in result
        assert "What is X?" in result
        assert "X is a variable." in result

    def test_recall_nonexistent_archive(self):
        result = recall_compressed_messages("nonexistent-id")
        parsed = json.loads(result)
        assert "error" in parsed
        assert "not found" in parsed["error"].lower()

    def test_recall_no_archive_instance(self):
        set_archive_instance(None)
        result = recall_compressed_messages("any-id")
        parsed = json.loads(result)
        assert "error" in parsed
        assert "not available" in parsed["error"].lower()

    def test_recall_metadata_header_format(self):
        msgs = [Message(role="tool", content="x" * 100, name="read_file")]
        aid = self.archive.archive(msgs, "prune", (5, 5))
        result = recall_compressed_messages(aid)
        header = result.split("\n\n")[0]
        assert "archive_id=" in header
        assert "messages=1" in header
        assert "original_tokens=" in header
        assert "showing_tokens=" in header


# -----------------------------------------------------------------------
# Tool schema and registration
# -----------------------------------------------------------------------

class TestToolRegistration:

    def test_tool_schema_has_required_fields(self):
        schema = RECALL_COMPRESSED_MESSAGES_TOOL.get_schema()
        assert schema["type"] == "function"
        func = schema["function"]
        assert func["name"] == "recall_compressed_messages"
        assert "archive_id" in func["parameters"]["properties"]
        assert "archive_id" in func["parameters"]["required"]

    def test_tool_is_readonly(self):
        assert RECALL_COMPRESSED_MESSAGES_TOOL.is_readonly is True

    def test_context_tools_list(self):
        assert len(CONTEXT_TOOLS) == 1
        assert CONTEXT_TOOLS[0].name == "recall_compressed_messages"

    def test_tool_categories_has_context(self):
        assert "context" in TOOL_CATEGORIES
        assert TOOL_CATEGORIES["context"]["label"] == "Context Management"
