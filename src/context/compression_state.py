"""Compression state tracking for messages."""

from enum import Enum


class CompressionState(Enum):
    """Tracks the compression status of a message.

    State transitions (see requirements state-machine diagram):
        ORIGINAL  -> TRUNCATED  (Phase 2: Truncate)
        ORIGINAL  -> PRUNED     (Phase 1: Prune)
        ORIGINAL  -> SUMMARIZED (Phase 4: LLM Summary)
        TRUNCATED -> SUMMARIZED (Phase 4: LLM Summary)
        PRUNED    -> SUMMARIZED (Phase 4: LLM Summary)
        SUMMARIZED -> (terminal — no further compression)
    """
    ORIGINAL = "original"
    TRUNCATED = "truncated"
    PRUNED = "pruned"
    SUMMARIZED = "summarized"
