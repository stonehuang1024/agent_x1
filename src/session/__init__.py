"""Session management module."""

from .models import (
    Session,
    SessionStatus,
    SessionType,
    Turn,
    Checkpoint,
    TokenBudget,
    SessionSummary,
    TurnContext,
    TurnStatus,
    SessionIndexEntry,
    InvalidStateTransition,
    VALID_TRANSITIONS,
)
from .session_store import SessionStore, get_default_store
from .session_manager import SessionManager, get_default_manager
from .transcript import TranscriptWriter, TranscriptReader, rebuild_history_from_transcript
from .session_index import SessionIndex
from .session_logger import SessionLogger
from .diff_tracker import DiffTracker, FileChange, ChangeType

__all__ = [
    # Models
    "Session",
    "SessionStatus",
    "SessionType",
    "Turn",
    "Checkpoint",
    "TokenBudget",
    "SessionSummary",
    "TurnContext",
    "TurnStatus",
    "SessionIndexEntry",
    "InvalidStateTransition",
    "VALID_TRANSITIONS",
    # Store
    "SessionStore",
    "get_default_store",
    # Manager
    "SessionManager",
    "get_default_manager",
    # Transcript
    "TranscriptWriter",
    "TranscriptReader",
    "rebuild_history_from_transcript",
    # Index
    "SessionIndex",
    # Logger
    "SessionLogger",
    # Diff
    "DiffTracker",
    "FileChange",
    "ChangeType",
]
