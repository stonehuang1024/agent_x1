"""Tests for CompressionState, Message.compression_state, and CompressionArchive.

Focus: archive round-trip fidelity, edge cases (missing id, corrupt file),
and Message serialisation of the new field.
"""

import json
import re
from pathlib import Path

import pytest

from src.core.models import Message
from src.context.compression_state import CompressionState
from src.context.compression_archive import CompressionArchive


# -----------------------------------------------------------------------
# CompressionState enum
# -----------------------------------------------------------------------

class TestCompressionState:

    def test_four_members(self):
        assert len(CompressionState) == 4

    def test_values(self):
        assert CompressionState.ORIGINAL.value == "original"
        assert CompressionState.TRUNCATED.value == "truncated"
        assert CompressionState.PRUNED.value == "pruned"
        assert CompressionState.SUMMARIZED.value == "summarized"


# -----------------------------------------------------------------------
# Message.compression_state
# -----------------------------------------------------------------------

class TestMessageCompressionState:

    def test_default_is_original(self):
        msg = Message(role="user", content="hi")
        assert msg.compression_state == "original"

    def test_to_dict_omits_original(self):
        msg = Message(role="user", content="hi")
        d = msg.to_dict()
        assert "compression_state" not in d

    def test_to_dict_includes_non_original(self):
        msg = Message(role="tool", content="data", compression_state="truncated")
        d = msg.to_dict()
        assert d["compression_state"] == "truncated"

    def test_from_dict_reads_compression_state(self):
        d = {"role": "tool", "content": "x", "compression_state": "pruned"}
        msg = Message.from_dict(d)
        assert msg.compression_state == "pruned"

    def test_from_dict_defaults_to_original(self):
        d = {"role": "user", "content": "hello"}
        msg = Message.from_dict(d)
        assert msg.compression_state == "original"


# -----------------------------------------------------------------------
# CompressionArchive — archive & recall
# -----------------------------------------------------------------------

class TestCompressionArchiveBasic:

    def test_archive_returns_uuid(self):
        archive = CompressionArchive()
        msgs = [Message(role="tool", content="big output")]
        aid = archive.archive(msgs, "prune", (0, 0))
        # UUID4 format
        assert re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            aid,
        )

    def test_recall_returns_original_messages(self):
        archive = CompressionArchive()
        msgs = [Message(role="tool", content="hello world", name="grep")]
        aid = archive.archive(msgs, "truncation", (1, 1))
        recalled = archive.recall(aid)
        assert len(recalled) == 1
        assert recalled[0].content == "hello world"
        assert recalled[0].name == "grep"

    def test_recall_unknown_id_returns_empty(self):
        archive = CompressionArchive()
        assert archive.recall("nonexistent-id") == []

    def test_has_archives_false_initially(self):
        archive = CompressionArchive()
        assert archive.has_archives() is False

    def test_has_archives_true_after_archive(self):
        archive = CompressionArchive()
        archive.archive([Message(role="tool", content="x")], "prune", (0, 0))
        assert archive.has_archives() is True

    def test_get_archive_count(self):
        archive = CompressionArchive()
        assert archive.get_archive_count() == 0
        archive.archive([Message(role="tool", content="a")], "prune", (0, 0))
        archive.archive([Message(role="tool", content="b")], "prune", (1, 1))
        assert archive.get_archive_count() == 2


# -----------------------------------------------------------------------
# CompressionArchive — JSONL persistence
# -----------------------------------------------------------------------

class TestCompressionArchiveJSONL:

    def test_jsonl_file_created(self, tmp_path):
        archive = CompressionArchive(session_dir=tmp_path)
        archive.archive([Message(role="tool", content="data")], "prune", (0, 0))
        jsonl = tmp_path / "compression_archive.jsonl"
        assert jsonl.exists()

    def test_jsonl_contains_valid_json(self, tmp_path):
        archive = CompressionArchive(session_dir=tmp_path)
        archive.archive([Message(role="tool", content="data")], "prune", (0, 0))
        jsonl = tmp_path / "compression_archive.jsonl"
        lines = jsonl.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert "archive_id" in entry
        assert entry["compression_type"] == "prune"
        assert entry["turn_range"] == [0, 0]
        assert len(entry["messages"]) == 1

    def test_recall_from_jsonl_after_memory_cleared(self, tmp_path):
        """Simulate a fresh CompressionArchive reading from existing JSONL."""
        archive1 = CompressionArchive(session_dir=tmp_path)
        msgs = [Message(role="tool", content="important data")]
        aid = archive1.archive(msgs, "truncation", (2, 5))

        # New instance — memory index is empty
        archive2 = CompressionArchive(session_dir=tmp_path)
        recalled = archive2.recall(aid)
        assert len(recalled) == 1
        assert recalled[0].content == "important data"

    def test_corrupt_jsonl_returns_empty(self, tmp_path):
        """A corrupt line should not crash recall."""
        jsonl = tmp_path / "compression_archive.jsonl"
        jsonl.write_text("this is not json\n")
        archive = CompressionArchive(session_dir=tmp_path)
        assert archive.recall("any-id") == []


# -----------------------------------------------------------------------
# CompressionArchive — memory-only mode
# -----------------------------------------------------------------------

class TestCompressionArchiveMemoryOnly:

    def test_archive_without_session_dir(self):
        archive = CompressionArchive(session_dir=None)
        msgs = [Message(role="tool", content="data")]
        aid = archive.archive(msgs, "prune", (0, 0))
        # Should still work in memory
        recalled = archive.recall(aid)
        assert len(recalled) == 1

    def test_recall_max_tokens_truncation(self):
        """When total tokens exceed max_tokens, result is truncated."""
        archive = CompressionArchive(recall_max_tokens=10)
        # Create many messages to exceed the token limit
        msgs = [Message(role="tool", content="x" * 500) for _ in range(10)]
        aid = archive.archive(msgs, "prune", (0, 9))
        recalled = archive.recall(aid)
        # Should return fewer messages than archived
        assert len(recalled) < 10
        assert len(recalled) > 0
