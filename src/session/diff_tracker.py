"""Turn-level diff tracking for file changes.

Tracks file creations, modifications, deletions and renames within a
single turn and produces an aggregated summary.
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ChangeType(Enum):
    """Type of file change."""
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


@dataclass
class FileChange:
    """Record of a single file change."""
    path: str
    change_type: ChangeType
    lines_added: int = 0
    lines_removed: int = 0
    old_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "change_type": self.change_type.value,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
            "old_path": self.old_path,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FileChange":
        data = data.copy()
        data["change_type"] = ChangeType(data["change_type"])
        return cls(**data)


class DiffTracker:
    """Aggregates file changes within a single turn.

    Intelligent merging rules:
    - CREATED + MODIFIED → stays CREATED (with accumulated line counts)
    - CREATED + DELETED → record removed entirely (net zero)
    - RENAMED removes the old-path record and creates a new-path record
    """

    def __init__(self) -> None:
        self._changes: Dict[str, FileChange] = {}

    def record_change(
        self,
        path: str,
        change_type: ChangeType,
        lines_added: int = 0,
        lines_removed: int = 0,
        old_path: Optional[str] = None,
    ) -> None:
        """Record a file change, merging with any existing record for the same path."""

        if change_type == ChangeType.RENAMED:
            # Remove old path record if it exists
            if old_path and old_path in self._changes:
                del self._changes[old_path]
            self._changes[path] = FileChange(
                path=path,
                change_type=ChangeType.RENAMED,
                lines_added=lines_added,
                lines_removed=lines_removed,
                old_path=old_path,
            )
            return

        existing = self._changes.get(path)
        if existing is None:
            self._changes[path] = FileChange(
                path=path,
                change_type=change_type,
                lines_added=lines_added,
                lines_removed=lines_removed,
            )
            return

        # Merge logic
        if existing.change_type == ChangeType.CREATED and change_type == ChangeType.DELETED:
            # Created then deleted → net zero
            del self._changes[path]
            return

        if existing.change_type == ChangeType.CREATED and change_type == ChangeType.MODIFIED:
            # Created then modified → still CREATED
            existing.lines_added += lines_added
            existing.lines_removed += lines_removed
            return

        # Default: accumulate counts, take latest change_type
        existing.lines_added += lines_added
        existing.lines_removed += lines_removed
        existing.change_type = change_type

    def get_changes(self) -> List[FileChange]:
        """Return all tracked file changes."""
        return list(self._changes.values())

    def get_summary(self) -> Dict[str, Any]:
        """Return an aggregated summary of all changes."""
        changes = self.get_changes()
        return {
            "files_changed": len(changes),
            "total_additions": sum(c.lines_added for c in changes),
            "total_deletions": sum(c.lines_removed for c in changes),
            "changes": [c.to_dict() for c in changes],
        }

    def save_diff(self, output_dir: Path, turn_number: int) -> None:
        """Persist the diff summary to a file in *output_dir*."""
        output_dir.mkdir(parents=True, exist_ok=True)
        diff_path = output_dir / f"turn_{turn_number:03d}.diff"
        summary = self.get_summary()
        with open(diff_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

    def reset(self) -> None:
        """Clear all tracked changes."""
        self._changes.clear()
