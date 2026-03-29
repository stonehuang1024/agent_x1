"""Unit tests for JSONL Transcript engine.

Bug-class targets:
- TranscriptWriter: data not flushed to disk, closed writer silently drops data
- TranscriptReader: crash on corrupt JSONL, missing file raises instead of empty list
- rebuild_history_from_transcript: field mapping errors, unsorted output
"""

import json
import logging
import time
import pytest
from pathlib import Path

from src.session.transcript import (
    TranscriptWriter, TranscriptReader, rebuild_history_from_transcript,
)


# ======================================================================
# 11.1 — TranscriptWriter
# ======================================================================

class TestTranscriptWriter:
    """Catches: data loss on crash, silent drop after close, JSONL format corruption."""

    def test_single_entry_persisted(self, tmp_path):
        path = tmp_path / "t.jsonl"
        with TranscriptWriter(path) as w:
            w.append({"role": "user", "content": "hello"})
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["role"] == "user"
        assert entry["content"] == "hello"

    def test_multiple_entries_correct_count(self, tmp_path):
        path = tmp_path / "t.jsonl"
        with TranscriptWriter(path) as w:
            for i in range(5):
                w.append({"index": i})
        lines = [l for l in path.read_text().strip().split("\n") if l]
        assert len(lines) == 5
        for i, line in enumerate(lines):
            assert json.loads(line)["index"] == i

    def test_flush_to_disk_after_each_append(self, tmp_path):
        """Data must be readable by an independent file handle after each append."""
        path = tmp_path / "t.jsonl"
        w = TranscriptWriter(path)
        w.append({"msg": "first"})
        # Read with a completely independent handle
        content = path.read_text()
        assert "first" in content
        w.append({"msg": "second"})
        content = path.read_text()
        assert "second" in content
        w.close()

    def test_auto_injects_timestamp(self, tmp_path):
        path = tmp_path / "t.jsonl"
        before = time.time()
        with TranscriptWriter(path) as w:
            w.append({"role": "user"})
        after = time.time()
        entry = json.loads(path.read_text().strip())
        assert "timestamp" in entry
        assert before <= entry["timestamp"] <= after

    def test_preserves_existing_timestamp(self, tmp_path):
        path = tmp_path / "t.jsonl"
        with TranscriptWriter(path) as w:
            w.append({"role": "user", "timestamp": 12345.0})
        entry = json.loads(path.read_text().strip())
        assert entry["timestamp"] == 12345.0

    def test_closed_writer_raises(self, tmp_path):
        path = tmp_path / "t.jsonl"
        w = TranscriptWriter(path)
        w.close()
        with pytest.raises(RuntimeError, match="closed"):
            w.append({"msg": "should fail"})

    def test_context_manager_closes(self, tmp_path):
        path = tmp_path / "t.jsonl"
        with TranscriptWriter(path) as w:
            pass
        with pytest.raises(RuntimeError):
            w.append({"msg": "after with"})

    def test_unicode_content(self, tmp_path):
        path = tmp_path / "t.jsonl"
        with TranscriptWriter(path) as w:
            w.append({"content": "你好世界 🌍 émojis"})
        entry = json.loads(path.read_text().strip())
        assert entry["content"] == "你好世界 🌍 émojis"

    def test_newline_in_content_does_not_break_jsonl(self, tmp_path):
        """Content with newlines must be escaped by json.dumps, keeping one entry per line."""
        path = tmp_path / "t.jsonl"
        with TranscriptWriter(path) as w:
            w.append({"content": "line1\nline2\nline3"})
            w.append({"content": "next"})
        lines = [l for l in path.read_text().strip().split("\n") if l]
        assert len(lines) == 2
        assert json.loads(lines[0])["content"] == "line1\nline2\nline3"

    def test_append_mode_preserves_existing(self, tmp_path):
        """Opening a writer on an existing file must append, not overwrite."""
        path = tmp_path / "t.jsonl"
        with TranscriptWriter(path) as w:
            w.append({"msg": "first"})
        with TranscriptWriter(path) as w:
            w.append({"msg": "second"})
        lines = [l for l in path.read_text().strip().split("\n") if l]
        assert len(lines) == 2


# ======================================================================
# 11.2 — TranscriptReader
# ======================================================================

class TestTranscriptReader:
    """Catches: crash on corrupt data, missing file exception, empty-line handling."""

    def test_read_valid_file(self, tmp_path):
        path = tmp_path / "t.jsonl"
        entries = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        reader = TranscriptReader(path)
        result = reader.read_all()
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    def test_skips_invalid_lines(self, tmp_path, caplog):
        """Invalid JSON lines must be skipped with a warning, not crash."""
        path = tmp_path / "t.jsonl"
        path.write_text(
            '{"valid": 1}\n'
            'NOT JSON\n'
            '{"valid": 2}\n'
            '{broken\n'
            '{"valid": 3}\n'
        )
        reader = TranscriptReader(path)
        with caplog.at_level(logging.WARNING):
            result = reader.read_all()
        assert len(result) == 3
        assert all(e["valid"] in (1, 2, 3) for e in result)
        # Should have logged warnings for the 2 invalid lines
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warning_msgs) >= 2

    def test_empty_file_returns_empty(self, tmp_path):
        path = tmp_path / "t.jsonl"
        path.write_text("")
        reader = TranscriptReader(path)
        assert reader.read_all() == []

    def test_missing_file_returns_empty(self, tmp_path):
        path = tmp_path / "nonexistent.jsonl"
        reader = TranscriptReader(path)
        assert reader.read_all() == []

    def test_blank_lines_skipped(self, tmp_path):
        path = tmp_path / "t.jsonl"
        path.write_text('\n\n{"a": 1}\n\n{"b": 2}\n\n')
        reader = TranscriptReader(path)
        result = reader.read_all()
        assert len(result) == 2

    def test_count_entries(self, tmp_path):
        path = tmp_path / "t.jsonl"
        path.write_text('{"a":1}\n{"b":2}\n{"c":3}\n')
        reader = TranscriptReader(path)
        assert reader.count_entries() == 3

    def test_read_range(self, tmp_path):
        """read_range uses 1-based inclusive indexing."""
        path = tmp_path / "t.jsonl"
        lines = [json.dumps({"i": i}) for i in range(10)]
        path.write_text("\n".join(lines) + "\n")
        reader = TranscriptReader(path)
        # Lines 3-5 (1-based inclusive) → items with i=2,3,4
        result = reader.read_range(3, 5)
        assert len(result) == 3
        assert [e["i"] for e in result] == [2, 3, 4]


# ======================================================================
# 11.3 — rebuild_history_from_transcript
# ======================================================================

class TestRebuildHistory:
    """Catches: field mapping errors, unsorted output, crash on missing fields."""

    def test_rebuild_maps_fields_correctly(self, tmp_path):
        path = tmp_path / "t.jsonl"
        entries = [
            {"role": "user", "content": "hello", "turn_number": 1, "token_count": 10},
            {"role": "assistant", "content": "hi", "turn_number": 2, "token_count": 20,
             "tool_calls": [{"id": "tc1"}]},
        ]
        path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        turns = rebuild_history_from_transcript(path)
        assert len(turns) == 2
        assert turns[0].role == "user"
        assert turns[0].content == "hello"
        assert turns[0].turn_number == 1
        assert turns[1].tool_calls == [{"id": "tc1"}]

    def test_missing_fields_use_defaults(self, tmp_path):
        path = tmp_path / "t.jsonl"
        path.write_text('{"role": "user"}\n')
        turns = rebuild_history_from_transcript(path)
        assert len(turns) == 1
        assert turns[0].content == ""
        assert turns[0].turn_number == 0
        assert turns[0].token_count == 0

    def test_sorted_by_turn_number(self, tmp_path):
        path = tmp_path / "t.jsonl"
        entries = [
            {"role": "assistant", "turn_number": 3},
            {"role": "user", "turn_number": 1},
            {"role": "tool", "turn_number": 2},
        ]
        path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        turns = rebuild_history_from_transcript(path)
        assert [t.turn_number for t in turns] == [1, 2, 3]

    def test_empty_file_returns_empty(self, tmp_path):
        path = tmp_path / "t.jsonl"
        path.write_text("")
        assert rebuild_history_from_transcript(path) == []

    def test_invalid_lines_skipped(self, tmp_path):
        path = tmp_path / "t.jsonl"
        path.write_text('{"role":"user","turn_number":1}\nGARBAGE\n{"role":"assistant","turn_number":2}\n')
        turns = rebuild_history_from_transcript(path)
        assert len(turns) == 2

    def test_metadata_preserved(self, tmp_path):
        path = tmp_path / "t.jsonl"
        path.write_text('{"role":"user","turn_number":1,"metadata":{"source":"cli"}}\n')
        turns = rebuild_history_from_transcript(path)
        assert turns[0].metadata == {"source": "cli"}
