"""Unit tests for DiffTracker.

Bug-class targets:
- Change aggregation: CREATED+DELETED not cancelled, RENAMED leaves stale entry
- Summary stats: wrong totals, missing changes
- save_diff: file not written, wrong format
"""

import json
import pytest
from pathlib import Path

from src.session.diff_tracker import DiffTracker, ChangeType, FileChange


class TestDiffTrackerRecording:
    """Catches: wrong aggregation logic, stale entries after rename."""

    def test_single_create(self):
        dt = DiffTracker()
        dt.record_change("foo.py", ChangeType.CREATED, lines_added=10)
        changes = dt.get_changes()
        assert len(changes) == 1
        assert changes[0].path == "foo.py"
        assert changes[0].change_type == ChangeType.CREATED
        assert changes[0].lines_added == 10

    def test_multiple_modifications_aggregate(self):
        """Same file modified twice → single entry with accumulated lines."""
        dt = DiffTracker()
        dt.record_change("foo.py", ChangeType.MODIFIED, lines_added=5, lines_removed=2)
        dt.record_change("foo.py", ChangeType.MODIFIED, lines_added=3, lines_removed=1)
        changes = dt.get_changes()
        assert len(changes) == 1
        assert changes[0].lines_added == 8
        assert changes[0].lines_removed == 3

    def test_create_then_delete_cancels(self):
        """File created then deleted in same turn → no net change."""
        dt = DiffTracker()
        dt.record_change("foo.py", ChangeType.CREATED, lines_added=10)
        dt.record_change("foo.py", ChangeType.DELETED)
        changes = dt.get_changes()
        assert len(changes) == 0

    def test_create_then_modify_stays_created(self):
        """CREATED + MODIFIED = still CREATED (file is new)."""
        dt = DiffTracker()
        dt.record_change("foo.py", ChangeType.CREATED, lines_added=10)
        dt.record_change("foo.py", ChangeType.MODIFIED, lines_added=5, lines_removed=2)
        changes = dt.get_changes()
        assert len(changes) == 1
        assert changes[0].change_type == ChangeType.CREATED
        assert changes[0].lines_added == 15

    def test_rename_removes_old_path(self):
        """RENAMED must remove old_path entry and create new_path entry."""
        dt = DiffTracker()
        dt.record_change("old.py", ChangeType.MODIFIED, lines_added=5)
        dt.record_change("new.py", ChangeType.RENAMED, old_path="old.py")
        changes = dt.get_changes()
        paths = {c.path for c in changes}
        assert "old.py" not in paths
        assert "new.py" in paths

    def test_reset_clears_all(self):
        dt = DiffTracker()
        dt.record_change("a.py", ChangeType.CREATED, lines_added=1)
        dt.record_change("b.py", ChangeType.MODIFIED, lines_added=2)
        dt.reset()
        assert dt.get_changes() == []


class TestDiffTrackerSummary:
    """Catches: wrong totals in summary."""

    def test_summary_stats(self):
        dt = DiffTracker()
        dt.record_change("a.py", ChangeType.CREATED, lines_added=10)
        dt.record_change("b.py", ChangeType.MODIFIED, lines_added=5, lines_removed=3)
        dt.record_change("c.py", ChangeType.DELETED, lines_removed=20)
        summary = dt.get_summary()
        assert summary["files_changed"] == 3
        assert summary["total_additions"] == 15
        assert summary["total_deletions"] == 23

    def test_summary_empty(self):
        dt = DiffTracker()
        summary = dt.get_summary()
        assert summary["files_changed"] == 0
        assert summary["total_additions"] == 0
        assert summary["total_deletions"] == 0


class TestDiffTrackerSaveDiff:
    """Catches: file not written, wrong filename format."""

    def test_save_diff_creates_file(self, tmp_path):
        dt = DiffTracker()
        dt.record_change("foo.py", ChangeType.CREATED, lines_added=10)
        dt.save_diff(tmp_path, turn_number=1)
        diff_file = tmp_path / "turn_001.diff"
        assert diff_file.exists()
        content = diff_file.read_text()
        assert "foo.py" in content

    def test_save_diff_turn_number_formatting(self, tmp_path):
        dt = DiffTracker()
        dt.record_change("x.py", ChangeType.MODIFIED)
        dt.save_diff(tmp_path, turn_number=42)
        assert (tmp_path / "turn_042.diff").exists()

    def test_save_diff_empty_tracker(self, tmp_path):
        """Saving with no changes should still create a file (empty summary)."""
        dt = DiffTracker()
        dt.save_diff(tmp_path, turn_number=1)
        assert (tmp_path / "turn_001.diff").exists()
