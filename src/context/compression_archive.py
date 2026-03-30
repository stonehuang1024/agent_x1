"""Archive for compressed messages — stores originals for later recall."""

import json
import logging
import math
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.core.models import Message

logger = logging.getLogger(__name__)

# Characters-per-token ratio (same as context_window.py)
_CHARS_PER_TOKEN = 3.5
_MSG_OVERHEAD = 4


def _estimate_tokens_for_messages(messages: List[Message]) -> int:
    """Quick token estimate without importing the full ContextWindow."""
    total = 0
    for msg in messages:
        content = msg.content or ""
        total += math.ceil(len(content) / _CHARS_PER_TOKEN) + _MSG_OVERHEAD
    return total


class CompressionArchive:
    """Stores original messages that were replaced by compression.

    Supports both file-backed (JSONL) and in-memory-only modes.
    """

    def __init__(
        self,
        session_dir: Optional[Path] = None,
        recall_max_tokens: int = 4000,
    ):
        self._session_dir = session_dir
        self._recall_max_tokens = recall_max_tokens
        self._index: Dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def archive(
        self,
        messages: List[Message],
        compression_type: str,
        turn_range: Tuple[int, int],
    ) -> str:
        """Store *messages* and return an ``archive_id``."""
        archive_id = str(uuid.uuid4())
        entry = {
            "archive_id": archive_id,
            "turn_range": list(turn_range),
            "messages": [msg.to_dict() for msg in messages],
            "compressed_at": datetime.now().isoformat(),
            "compression_type": compression_type,
            "original_token_count": _estimate_tokens_for_messages(messages),
        }

        # Persist to JSONL
        if self._session_dir is not None:
            try:
                path = Path(self._session_dir)
                path.mkdir(parents=True, exist_ok=True)
                archive_file = path / "compression_archive.jsonl"
                with open(archive_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception as exc:
                logger.warning(
                    "Failed to persist archive %s: %s", archive_id, exc
                )
        else:
            logger.warning(
                "No session_dir configured — archive %s stored in memory only",
                archive_id,
            )

        # Always keep in-memory index
        self._index[archive_id] = entry
        return archive_id

    def recall(
        self,
        archive_id: str,
        max_tokens: Optional[int] = None,
    ) -> List[Message]:
        """Retrieve original messages for *archive_id*.

        Returns an empty list when the id is not found.
        """
        effective_max = max_tokens if max_tokens is not None else self._recall_max_tokens
        entry = self._index.get(archive_id)

        # Fallback: scan JSONL file
        if entry is None and self._session_dir is not None:
            entry = self._scan_jsonl(archive_id)

        if entry is None:
            logger.warning("Archive '%s' not found", archive_id)
            return []

        try:
            messages = [Message.from_dict(d) for d in entry["messages"]]
        except Exception as exc:
            logger.warning("Failed to deserialise archive %s: %s", archive_id, exc)
            return []

        # Token-budget truncation (head + tail)
        total_tokens = _estimate_tokens_for_messages(messages)
        if total_tokens > effective_max and len(messages) > 2:
            # Keep roughly half from head and half from tail
            keep_count = max(2, len(messages) // 4)
            head = messages[:keep_count]
            tail = messages[-keep_count:]
            return head + tail

        return messages

    def has_archives(self) -> bool:
        """Return True if at least one archive entry exists."""
        return len(self._index) > 0

    def get_archive_count(self) -> int:
        """Return the total number of archive entries."""
        return len(self._index)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scan_jsonl(self, archive_id: str) -> Optional[dict]:
        """Scan the JSONL file for *archive_id*."""
        try:
            archive_file = Path(self._session_dir) / "compression_archive.jsonl"
            if not archive_file.exists():
                return None
            with open(archive_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("Corrupt JSONL line in archive file")
                        continue
                    if entry.get("archive_id") == archive_id:
                        # Cache for future lookups
                        self._index[archive_id] = entry
                        return entry
        except Exception as exc:
            logger.warning("Failed to scan archive file: %s", exc)
        return None
