"""JSONL Transcript engine for session history persistence.

Provides append-only write and tolerant read for JSONL transcript files.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from src.session.models import Turn

logger = logging.getLogger(__name__)


class TranscriptWriter:
    """Append-only writer for JSONL transcript files.

    Each call to append() writes one JSON line and immediately flushes
    to disk via fsync, ensuring crash-safe persistence.
    """

    def __init__(self, file_path: Union[str, Path]) -> None:
        self._path = Path(file_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "a", encoding="utf-8")
        self._closed = False

    def append(self, entry: Dict[str, Any]) -> None:
        """Append a single entry as one JSON line.

        Automatically injects a ``timestamp`` field if the entry does not
        already contain one.

        Raises:
            RuntimeError: If the writer has been closed.
        """
        if self._closed:
            raise RuntimeError("TranscriptWriter is closed")

        if "timestamp" not in entry:
            entry = {**entry, "timestamp": time.time()}

        line = json.dumps(entry, ensure_ascii=False)
        self._file.write(line + "\n")
        self._file.flush()
        os.fsync(self._file.fileno())
        
        logger.debug(
            "[Session] Transcript write | entry_type=%s | content_length=%d",
            entry.get("type", "unknown"), len(line)
        )

    def close(self) -> None:
        """Close the underlying file handle."""
        if not self._closed:
            self._file.close()
            self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    # Context-manager protocol
    def __enter__(self) -> "TranscriptWriter":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class TranscriptReader:
    """Tolerant reader for JSONL transcript files.

    Invalid lines are skipped with a warning; missing files return empty
    results rather than raising exceptions.
    """

    def __init__(self, file_path: Union[str, Path]) -> None:
        self._path = Path(file_path)

    def read_all(self) -> List[Dict[str, Any]]:
        """Read all valid entries from the JSONL file.

        Returns:
            List of parsed dictionaries.  Invalid lines are skipped.
        """
        if not self._path.exists():
            return []

        entries: List[Dict[str, Any]] = []
        with open(self._path, "r", encoding="utf-8") as f:
            for line_number, raw_line in enumerate(f, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning("Skipping invalid JSONL line %d: %s", line_number, e)
        return entries

    def read_range(self, start_line: int, end_line: int) -> List[Dict[str, Any]]:
        """Read entries within a specific line range (1-based, inclusive).

        Args:
            start_line: First line to include (1-based).
            end_line: Last line to include (1-based, inclusive).

        Returns:
            List of parsed dictionaries within the range.
        """
        if not self._path.exists():
            return []

        entries: List[Dict[str, Any]] = []
        with open(self._path, "r", encoding="utf-8") as f:
            for line_number, raw_line in enumerate(f, start=1):
                if line_number < start_line:
                    continue
                if line_number > end_line:
                    break
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except (json.JSONDecodeError, ValueError) as e:
                    logger.warning("Skipping invalid JSONL line %d: %s", line_number, e)
        return entries

    def count_entries(self) -> int:
        """Return the number of valid entries in the file."""
        return len(self.read_all())


def rebuild_history_from_transcript(path: Union[str, Path]) -> List[Turn]:
    """Rebuild a list of Turn objects from a JSONL transcript file.

    Only entries that look like conversation messages (have a ``role``
    field) are converted.  Invalid lines are silently skipped.

    Returns:
        Sorted list of Turn objects ordered by ``turn_number``.
    """
    reader = TranscriptReader(path)
    entries = reader.read_all()

    turns: List[Turn] = []
    for entry in entries:
        # Only convert message-type entries (those with a role)
        if "role" not in entry:
            continue
        turn = Turn(
            session_id=entry.get("session_id", ""),
            turn_number=entry.get("turn_number", 0),
            role=entry.get("role", ""),
            content=entry.get("content", ""),
            tool_calls=entry.get("tool_calls"),
            tool_call_id=entry.get("tool_call_id"),
            token_count=entry.get("token_count", 0),
            metadata=entry.get("metadata", {}),
        )
        turns.append(turn)

    turns.sort(key=lambda t: t.turn_number)
    return turns
