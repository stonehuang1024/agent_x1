"""Unit tests for Session Index manager.

Bug-class targets:
- CRUD: update overwrites wrong entry, remove crashes on missing id
- Query: list_all not sorted, get_latest returns wrong entry
- Rebuild: index file corruption causes crash instead of graceful recovery
- File locking: concurrent writes corrupt index
"""

import json
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from src.session.session_index import SessionIndex
from src.session.models import SessionIndexEntry


def _make_entry(session_id: str, status: str = "active", updated_at: float = None, **kwargs):
    return SessionIndexEntry(
        session_id=session_id,
        name=kwargs.get("name", f"session-{session_id}"),
        status=status,
        created_at=kwargs.get("created_at", time.time()),
        updated_at=updated_at or time.time(),
        turn_count=kwargs.get("turn_count", 0),
        preview=kwargs.get("preview", ""),
        working_dir=kwargs.get("working_dir", "/tmp"),
        session_dir=kwargs.get("session_dir", f"/tmp/{session_id}"),
        agent_id=kwargs.get("agent_id"),
        session_type=kwargs.get("session_type", "primary"),
    )


# ======================================================================
# 12.1 — Index CRUD
# ======================================================================

class TestSessionIndexCRUD:
    """Catches: wrong entry updated, crash on missing id, data loss on save."""

    def test_update_inserts_new_entry(self, tmp_path):
        idx = SessionIndex(tmp_path / "index.json")
        entry = _make_entry("s1")
        idx.update(entry)
        assert idx.get("s1") is not None
        assert idx.get("s1").session_id == "s1"

    def test_update_overwrites_existing(self, tmp_path):
        idx = SessionIndex(tmp_path / "index.json")
        idx.update(_make_entry("s1", turn_count=0))
        idx.update(_make_entry("s1", turn_count=5))
        assert idx.get("s1").turn_count == 5

    def test_update_does_not_affect_other_entries(self, tmp_path):
        idx = SessionIndex(tmp_path / "index.json")
        idx.update(_make_entry("s1", turn_count=1))
        idx.update(_make_entry("s2", turn_count=2))
        idx.update(_make_entry("s1", turn_count=10))
        assert idx.get("s2").turn_count == 2

    def test_remove_deletes_entry(self, tmp_path):
        idx = SessionIndex(tmp_path / "index.json")
        idx.update(_make_entry("s1"))
        idx.remove("s1")
        assert idx.get("s1") is None

    def test_remove_nonexistent_no_error(self, tmp_path):
        idx = SessionIndex(tmp_path / "index.json")
        idx.remove("nonexistent")  # Must not raise

    def test_get_returns_none_for_missing(self, tmp_path):
        idx = SessionIndex(tmp_path / "index.json")
        assert idx.get("missing") is None

    def test_list_all_returns_all(self, tmp_path):
        idx = SessionIndex(tmp_path / "index.json")
        for i in range(5):
            idx.update(_make_entry(f"s{i}"))
        assert len(idx.list_all()) == 5

    def test_persistence_across_instances(self, tmp_path):
        """Data must survive creating a new SessionIndex on the same file."""
        path = tmp_path / "index.json"
        idx1 = SessionIndex(path)
        idx1.update(_make_entry("s1", turn_count=42))
        del idx1
        idx2 = SessionIndex(path)
        assert idx2.get("s1").turn_count == 42


# ======================================================================
# 12.2 — Index Query
# ======================================================================

class TestSessionIndexQuery:
    """Catches: wrong sort order, status filter misses entries."""

    def test_list_all_sorted_by_updated_at_desc(self, tmp_path):
        idx = SessionIndex(tmp_path / "index.json")
        idx.update(_make_entry("old", updated_at=1000.0))
        idx.update(_make_entry("mid", updated_at=2000.0))
        idx.update(_make_entry("new", updated_at=3000.0))
        result = idx.list_all()
        assert [e.session_id for e in result] == ["new", "mid", "old"]

    def test_list_all_filters_by_status(self, tmp_path):
        idx = SessionIndex(tmp_path / "index.json")
        idx.update(_make_entry("s1", status="paused"))
        idx.update(_make_entry("s2", status="active"))
        idx.update(_make_entry("s3", status="paused"))
        result = idx.list_all(status="paused")
        assert len(result) == 2
        assert all(e.status == "paused" for e in result)

    def test_get_latest_returns_most_recent(self, tmp_path):
        idx = SessionIndex(tmp_path / "index.json")
        idx.update(_make_entry("old", updated_at=1000.0))
        idx.update(_make_entry("new", updated_at=3000.0))
        latest = idx.get_latest()
        assert latest.session_id == "new"

    def test_get_latest_with_status_filter(self, tmp_path):
        idx = SessionIndex(tmp_path / "index.json")
        idx.update(_make_entry("s1", status="active", updated_at=3000.0))
        idx.update(_make_entry("s2", status="paused", updated_at=2000.0))
        idx.update(_make_entry("s3", status="paused", updated_at=1000.0))
        latest = idx.get_latest(status="paused")
        assert latest.session_id == "s2"

    def test_get_latest_empty_returns_none(self, tmp_path):
        idx = SessionIndex(tmp_path / "index.json")
        assert idx.get_latest() is None

    def test_get_latest_no_match_returns_none(self, tmp_path):
        idx = SessionIndex(tmp_path / "index.json")
        idx.update(_make_entry("s1", status="active"))
        assert idx.get_latest(status="paused") is None


# ======================================================================
# 12.3 — Index Rebuild and Validation
# ======================================================================

class TestSessionIndexRebuildValidation:
    """Catches: crash on corrupt file, rebuild produces wrong data."""

    def test_validate_valid_file(self, tmp_path):
        path = tmp_path / "index.json"
        idx = SessionIndex(path)
        idx.update(_make_entry("s1"))
        assert idx.validate() is True

    def test_validate_corrupt_json(self, tmp_path):
        path = tmp_path / "index.json"
        path.write_text("NOT VALID JSON {{{")
        idx = SessionIndex(path)
        assert idx.validate() is False

    def test_validate_missing_file(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        idx = SessionIndex(path)
        assert idx.validate() is False

    def test_corrupt_file_loads_as_empty(self, tmp_path):
        """Corrupt index file must not crash; should initialize as empty."""
        path = tmp_path / "index.json"
        path.write_text("CORRUPT DATA")
        idx = SessionIndex(path)
        assert len(idx.list_all()) == 0

    def test_rebuild_from_store(self, tmp_path):
        """rebuild_from_store must populate index from store data."""
        path = tmp_path / "index.json"
        idx = SessionIndex(path)

        # Create a mock store
        mock_store = MagicMock()
        from src.session.models import Session, SessionStatus
        sessions = [
            Session(name="s1", status=SessionStatus.ACTIVE),
            Session(name="s2", status=SessionStatus.PAUSED),
        ]
        mock_store.list_sessions.return_value = sessions

        idx.rebuild_from_store(mock_store)
        all_entries = idx.list_all()
        assert len(all_entries) == 2
        ids = {e.session_id for e in all_entries}
        assert sessions[0].id in ids
        assert sessions[1].id in ids
