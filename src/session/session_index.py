"""Session index manager for fast session lookup and listing.

Maintains a JSON index file (``sessions-index.json``) that mirrors the
authoritative data in the SQLite store but allows O(1) lookups and
listings without database queries.
"""

import fcntl
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from src.session.models import Session, SessionIndexEntry

logger = logging.getLogger(__name__)


class SessionIndex:
    """Manages a JSON-based session index file with file-locking.

    The index is loaded into memory on construction and written back
    atomically (via a temp file + ``os.replace``) on every mutation.
    """

    def __init__(self, index_path: Union[str, Path] = "data/sessions-index.json") -> None:
        self._path = Path(index_path)
        self._entries: Dict[str, SessionIndexEntry] = {}
        self._load()

    # ------------------------------------------------------------------
    # Public CRUD
    # ------------------------------------------------------------------

    def update(self, entry: SessionIndexEntry) -> None:
        """Insert or update an index entry, then persist."""
        self._entries[entry.session_id] = entry
        self._save()
        logger.debug(
            "[Session] Index updated | session_id=%s | status=%s | turn_count=%d",
            entry.session_id[:8], entry.status, entry.turn_count
        )

    def remove(self, session_id: str) -> None:
        """Remove an entry by session_id (no-op if missing)."""
        if session_id in self._entries:
            del self._entries[session_id]
            self._save()

    def get(self, session_id: str) -> Optional[SessionIndexEntry]:
        """Return a single entry or ``None``."""
        return self._entries.get(session_id)

    def list_all(self, status: Optional[str] = None) -> List[SessionIndexEntry]:
        """Return all entries, optionally filtered by status.

        Results are sorted by ``updated_at`` descending (most recent first).
        """
        entries = list(self._entries.values())
        if status is not None:
            entries = [e for e in entries if e.status == status]
        entries.sort(key=lambda e: e.updated_at, reverse=True)
        return entries

    def get_latest(self, status: Optional[str] = None) -> Optional[SessionIndexEntry]:
        """Return the most recently updated entry (optionally filtered)."""
        entries = self.list_all(status=status)
        return entries[0] if entries else None

    # ------------------------------------------------------------------
    # Rebuild / Validate
    # ------------------------------------------------------------------

    def rebuild_from_store(self, store: Any) -> None:
        """Rebuild the index from the authoritative SessionStore.

        Args:
            store: A ``SessionStore`` instance with a ``list_sessions()`` method.
        """
        self._entries.clear()
        sessions: List[Session] = store.list_sessions()
        for session in sessions:
            entry = SessionIndexEntry.from_session(session)
            self._entries[entry.session_id] = entry
        self._save()
        logger.info("Rebuilt session index with %d entries", len(self._entries))

    def validate(self) -> bool:
        """Check whether the index file exists and is parseable.

        Returns:
            ``True`` if the index is healthy, ``False`` otherwise.
        """
        if not self._path.exists():
            logger.warning("Session index file does not exist: %s", self._path)
            return False
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                logger.warning("Session index is not a JSON array")
                return False
            return True
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Session index validation failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Internal I/O (with file locking)
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load the index from disk.  Initialises to empty on any error."""
        if not self._path.exists():
            self._entries = {}
            return

        try:
            with open(self._path, "r", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    data = json.load(f)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

            if isinstance(data, list):
                for item in data:
                    try:
                        entry = SessionIndexEntry.from_dict(item)
                        self._entries[entry.session_id] = entry
                    except Exception:
                        continue
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load session index, starting empty: %s", e)
            self._entries = {}

    def _save(self) -> None:
        """Atomically persist the index to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(".tmp")

        serialized = [entry.to_dict() for entry in self._entries.values()]

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    json.dump(serialized, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

            os.replace(str(tmp_path), str(self._path))
        except OSError as e:
            logger.error("Failed to save session index: %s", e)
            # Clean up temp file on failure
            if tmp_path.exists():
                tmp_path.unlink()
