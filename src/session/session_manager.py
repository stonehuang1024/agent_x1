"""Session lifecycle management.

Unified session manager that owns the full lifecycle: creation, turn
management, transcript persistence, index maintenance, pause/resume,
compaction, and graceful shutdown.
"""

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Callable, Dict, Any

from .models import (
    Session, SessionStatus, SessionType, Turn, Checkpoint,
    TokenBudget, SessionSummary, TurnContext, TurnStatus,
    InvalidStateTransition, SessionIndexEntry,
)
from .session_store import SessionStore
from .transcript import TranscriptWriter, TranscriptReader, rebuild_history_from_transcript
from .session_index import SessionIndex
from .session_logger import SessionLogger
from src.core.config import AppConfig
from src.core.events import EventBus, AgentEvent
from src.util.logger import set_session_id, clear_session_id, bind_session_to_log

logger = logging.getLogger(__name__)


class SessionManager:
    """
    High-level session lifecycle management.

    Provides convenient API for creating, resuming, and managing sessions.
    Integrates JSONL transcript, session index, and session logger.
    """

    def __init__(
        self,
        store: SessionStore,
        config: AppConfig,
        event_bus: Optional[EventBus] = None,
        index_path: Optional[str] = None,
    ):
        self.store = store
        self.config = config
        self.event_bus = event_bus
        self._active_session: Optional[Session] = None
        self._state_change_callbacks: List[Callable[[Session, SessionStatus, SessionStatus], None]] = []

        # Transcript writers keyed by session_id
        self._transcript_writers: Dict[str, TranscriptWriter] = {}

        # Session loggers keyed by session_id
        self._session_loggers: Dict[str, SessionLogger] = {}

        # Session index
        default_index = str(Path(config.paths.data_dir) / "sessions-index.json") if hasattr(config, 'paths') else "data/sessions-index.json"
        self._index = SessionIndex(index_path or default_index)

    # ========== Callback Registration ==========

    def on_state_change(
        self,
        callback: Callable[[Session, SessionStatus, SessionStatus], None]
    ):
        """Register a callback for session state changes."""
        self._state_change_callbacks.append(callback)

    def _notify_state_change(self, session: Session, old: SessionStatus, new: SessionStatus):
        """Notify all registered callbacks of state change."""
        for callback in self._state_change_callbacks:
            try:
                callback(session, old, new)
            except Exception as e:
                logger.error("State change callback error: %s", e)

        # Emit EventBus event
        self._emit_session_event(session, old, new)

    def _emit_session_event(self, session: Session, old: SessionStatus, new: SessionStatus):
        """Emit session state change event via EventBus."""
        if not self.event_bus:
            return
        try:
            event_map = {
                SessionStatus.CREATED: AgentEvent.SESSION_CREATED,
                SessionStatus.ACTIVE: AgentEvent.SESSION_RESUMED,
                SessionStatus.PAUSED: AgentEvent.SESSION_PAUSED,
                SessionStatus.COMPLETED: AgentEvent.SESSION_COMPLETED,
                SessionStatus.FAILED: AgentEvent.SESSION_FAILED,
                SessionStatus.ARCHIVED: AgentEvent.SESSION_ARCHIVED,
            }
            # Add COMPACTING mapping if the event exists
            if hasattr(AgentEvent, 'SESSION_COMPACTING'):
                event_map[SessionStatus.COMPACTING] = AgentEvent.SESSION_COMPACTING

            event_type = event_map.get(new)
            if event_type:
                self.event_bus.emit(
                    event_type,
                    session_id=session.id,
                    old_status=old.value,
                    new_status=new.value
                )
        except Exception as e:
            logger.debug("EventBus emit error: %s", e)

    # ========== Session Creation ==========

    def create_session(
        self,
        name: Optional[str] = None,
        parent_id: Optional[str] = None,
        working_dir: Optional[str] = None,
        session_dir: Optional[str] = None,
    ) -> Session:
        """
        Create a new session.

        Args:
            name: Optional session name
            parent_id: Optional parent session ID for forking
            working_dir: Working directory (defaults to current)
            session_dir: Optional pre-created session directory to reuse

        Returns:
            The created Session
        """
        # Validate parent if provided
        parent = None
        if parent_id:
            parent = self.store.get_session(parent_id)
            if not parent:
                raise ValueError(f"Parent session {parent_id} not found")

        # Create session directory
        if session_dir:
            session_dir_path = Path(session_dir)
        else:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            dir_name = f"{name}_{timestamp}" if name else f"session_{timestamp}"
            # Sanitize directory name
            dir_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in dir_name)
            session_dir_path = Path(self.config.paths.session_dir) / dir_name
        session_dir_path.mkdir(parents=True, exist_ok=True)

        # Create subdirectories
        (session_dir_path / "diffs").mkdir(exist_ok=True)
        (session_dir_path / "artifacts").mkdir(exist_ok=True)

        # Build config snapshot
        provider_val = self.config.llm.provider
        if hasattr(provider_val, 'value'):
            provider_val = provider_val.value

        config_snapshot = {
            "provider": provider_val,
            "model": self.config.llm.model,
            "temperature": self.config.llm.temperature,
            "max_tokens": self.config.llm.max_tokens,
        }

        # Transcript path
        transcript_path = str(session_dir_path / "transcript.jsonl")

        session = Session(
            parent_id=parent_id,
            name=name,
            status=SessionStatus.CREATED,
            config_snapshot=config_snapshot,
            budget=TokenBudget(
                total=self.config.llm.max_tokens * 10,
                reserved=8192,
                used=0
            ),
            working_dir=working_dir or os.getcwd(),
            session_dir=str(session_dir_path),
            transcript_path=transcript_path,
        )

        self.store.create_session(session)

        # Initialize transcript writer
        writer = TranscriptWriter(transcript_path)
        self._transcript_writers[session.id] = writer

        # Initialize session logger
        memory_data_dir = str(Path(self.config.paths.memory_data_dir))
        session_logger = SessionLogger(
            session_dir=session_dir_path,
            session_id=session.id,
            transcript_writer=writer,
            memory_data_dir=memory_data_dir,
        )
        self._session_loggers[session.id] = session_logger

        # If fork, copy parent turns
        if parent:
            turns = self.store.get_turns(parent_id)
            for turn in turns:
                turn.id = 0
                turn.session_id = session.id
                self.store.add_turn(turn)
                # Also write to transcript
                writer.append({
                    "type": "message",
                    "role": turn.role,
                    "content": turn.content,
                    "tool_calls": turn.tool_calls,
                    "tool_call_id": turn.tool_call_id,
                    "turn_number": turn.turn_number,
                    "token_count": turn.token_count,
                    "metadata": turn.metadata,
                })
            session.turn_count = len(turns)
            session.status = SessionStatus.ACTIVE
            self.store.update_session(session)
            # Parent keeps its current status (no longer forced to FORKED)

        # Update index
        self._update_index(session)

        # Bind session_id to logging context
        set_session_id(session.id)
        # Bind session to log file for per-session log isolation
        bind_session_to_log(session.id)

        logger.info("Created session %s (name=%s, parent=%s)", session.id[:8], name, parent_id)
        logger.info(
            "[Session] Created | id=%s | name=%s | working_dir=%s | session_dir=%s | budget_total=%d",
            session.id[:8], name, session.working_dir, session.session_dir, session.budget.total
        )
        return session

    # ========== Turn Context Lifecycle ==========

    def begin_turn(self, session_id: Optional[str] = None) -> TurnContext:
        """Begin a new turn, creating a frozen TurnContext.

        If the session is in CREATED state, it is automatically
        transitioned to ACTIVE.
        """
        session = self._get_session(session_id)

        # Auto-activate on first turn
        if session.status == SessionStatus.CREATED:
            self._transition(session, SessionStatus.ACTIVE)

        # Build TurnContext from session config snapshot
        snap = session.config_snapshot
        ctx = TurnContext(
            session_id=session.id,
            turn_number=session.turn_count + 1,
            working_dir=session.working_dir,
            model=snap.get("model", ""),
            provider=snap.get("provider", ""),
            temperature=snap.get("temperature", 0.0),
            max_tokens=snap.get("max_tokens", 0),
            approval_policy=snap.get("approval_policy", ""),
            behavior_settings=snap.get("behavior_settings", {}),
            started_at=time.time(),
            status=TurnStatus.RUNNING,
        )
        ctx.freeze()

        # DEBUG: Turn started
        logger.debug(
            "[Session] Turn started | session_id=%s | turn_number=%d",
            session.id[:8], ctx.turn_number
        )

        # Persist
        self.store.save_turn_context(ctx)

        # Emit event
        if self.event_bus:
            self.event_bus.emit(
                AgentEvent.TURN_STARTED,
                session_id=session.id,
                turn_number=ctx.turn_number,
            )

        return ctx

    def end_turn(
        self,
        turn_context: TurnContext,
        diff_summary: Optional[Dict[str, Any]] = None,
    ) -> None:
        """End a turn, updating stats and checking budget thresholds."""
        session = self._get_session(turn_context.session_id)

        # Finalise the TurnContext
        if turn_context.error is not None:
            turn_context.fail(turn_context.error)
        else:
            turn_context.complete(
                token_usage=turn_context.token_usage,
                tool_call_records=turn_context.tool_call_records,
                latency_stats=turn_context.latency_stats,
            )

        # DEBUG: Turn ended
        duration_ms = (turn_context.completed_at - turn_context.started_at) * 1000 if turn_context.completed_at and turn_context.started_at else 0
        logger.debug(
            "[Session] Turn ended | session_id=%s | turn_number=%d | token_usage=%s | duration=%.0fms",
            turn_context.session_id[:8], turn_context.turn_number,
            turn_context.token_usage.get('total_tokens', 0), duration_ms
        )

        # Persist updated context
        self.store.update_turn_context(turn_context)

        # Update session stats
        session.turn_count += 1
        total_tokens = turn_context.token_usage.get("total_tokens", 0)
        session.budget.used += total_tokens

        # Write diff summary to transcript
        if diff_summary:
            writer = self._get_transcript_writer(session.id)
            if writer:
                writer.append({"type": "diff_summary", **diff_summary})

        # Emit turn event
        if self.event_bus:
            event = (
                AgentEvent.TURN_COMPLETED
                if turn_context.status == TurnStatus.COMPLETED
                else AgentEvent.TURN_FAILED
            )
            self.event_bus.emit(
                event,
                session_id=session.id,
                turn_number=turn_context.turn_number,
            )

        # Token budget checks
        if session.budget.needs_warning():
            if self.event_bus and hasattr(AgentEvent, 'TOKEN_BUDGET_WARNING'):
                self.event_bus.emit(
                    AgentEvent.TOKEN_BUDGET_WARNING,
                    session_id=session.id,
                    utilization=session.budget.utilization_rate,
                )

        if session.budget.needs_compaction():
            if session.status == SessionStatus.ACTIVE:
                self._transition(session, SessionStatus.COMPACTING)

        self.store.update_session(session)
        self._update_index(session)

    def complete_compaction(
        self,
        session_id: Optional[str] = None,
        new_used: int = 0,
    ) -> None:
        """Complete a compaction cycle, resetting token usage."""
        session = self._get_session(session_id)
        if session.status != SessionStatus.COMPACTING:
            raise InvalidStateTransition(session.status, SessionStatus.ACTIVE, session.id)
        session.budget.reset_used(new_used)
        self._transition(session, SessionStatus.ACTIVE)

    # ========== Session Activation ==========

    def resume_session(self, session_id: str) -> Session:
        """Resume an existing session (legacy API, kept for compatibility)."""
        session = self.store.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        if session.status == SessionStatus.ARCHIVED:
            raise InvalidStateTransition(SessionStatus.ARCHIVED, SessionStatus.ACTIVE, session_id)

        if session.status not in (SessionStatus.ACTIVE,):
            self._transition(session, SessionStatus.ACTIVE)

        self._active_session = session

        # Bind session_id to logging context
        set_session_id(session.id)
        # Bind session to log file for per-session log isolation
        bind_session_to_log(session.id)

        logger.info("Resumed session %s", session.id[:8])
        return session

    def activate_session(self, session_id: str) -> Session:
        """Activate a session as the current working session."""
        session = self.resume_session(session_id)
        self._active_session = session
        return session

    @property
    def active_session(self) -> Optional[Session]:
        """Get the currently active session."""
        return self._active_session

    def deactivate(self):
        """Deactivate the current session without changing its state."""
        if self._active_session:
            logger.info("Deactivated session %s", self._active_session.id[:8])
            self._active_session = None

    # ========== Session Recovery (continue / resume) ==========

    def continue_session(self) -> Session:
        """Resume the most recent PAUSED or ACTIVE session (``--continue``)."""
        entry = self._index.get_latest(status="paused")
        if not entry:
            entry = self._index.get_latest(status="active")
        if not entry:
            raise ValueError(
                "No resumable session found. Use --resume <id> or start a new session."
            )

        session = self.store.get_session(entry.session_id)
        if not session:
            raise ValueError(f"Session {entry.session_id} not found in store")

        return self._resume_internal(session)

    def resume_session_by_id(self, session_id: str) -> Session:
        """Resume a specific session by ID (``--resume <id>``)."""
        session = self.store.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        return self._resume_internal(session)

    def _resume_internal(self, session: Session) -> Session:
        """Internal session recovery logic.

        1. Config mismatch check
        2. History rebuild from JSONL (fallback to SQLite)
        3. State transition → ACTIVE
        4. Re-initialise transcript writer
        5. Set as active session
        """
        # Step 1 — config check
        current_snap = self._build_config_snapshot()
        for key in ("provider", "model", "temperature", "max_tokens"):
            old_val = session.config_snapshot.get(key)
            new_val = current_snap.get(key)
            if old_val != new_val:
                logger.warning(
                    "Config mismatch for '%s': session used %s, current is %s",
                    key, old_val, new_val,
                )

        # Step 2 — history rebuild
        turns: List[Turn] = []
        if session.transcript_path and Path(session.transcript_path).exists():
            turns = rebuild_history_from_transcript(session.transcript_path)
        else:
            turns = self.store.get_turns(session.id)

        # Step 3 — state transition
        if session.status != SessionStatus.ACTIVE:
            self._transition(session, SessionStatus.ACTIVE)

        # Step 4 — re-init transcript writer
        if session.transcript_path:
            writer = TranscriptWriter(session.transcript_path)
            self._transcript_writers[session.id] = writer

            # Re-init session logger
            memory_data_dir = str(Path(self.config.paths.memory_data_dir))
            session_logger = SessionLogger(
                session_dir=session.session_dir,
                session_id=session.id,
                transcript_writer=writer,
                memory_data_dir=memory_data_dir,
            )
            self._session_loggers[session.id] = session_logger

        # Step 5 — set active
        self._active_session = session

        # Bind session_id to logging context
        set_session_id(session.id)
        # Bind session to log file for per-session log isolation
        bind_session_to_log(session.id)

        # Step 6 — log
        logger.info(
            "Resumed session %s: %d turns, %d tokens used",
            session.id[:8], len(turns), session.budget.used,
        )

        return session

    # ========== State Transitions ==========

    def _transition(self, session: Session, new_status: SessionStatus):
        """Transition session state with strict validation."""
        old_status = session.status

        # validate_transition returns False for same-state (no-op)
        if not session.validate_transition(new_status):
            return

        session.status = new_status
        session.updated_at = datetime.now()

        if new_status in (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.ARCHIVED):
            session.ended_at = datetime.now()

        self.store.update_session(session)
        self._notify_state_change(session, old_status, new_status)
        self._update_index(session)
        logger.info("Session %s: %s -> %s", session.id[:8], old_status.value, new_status.value)
        logger.info(
            "[Session] Status changed | id=%s | from=%s | to=%s",
            session.id[:8], old_status.value, new_status.value
        )

    def pause_session(self, session_id: Optional[str] = None):
        """Pause a session (from ACTIVE or COMPACTING)."""
        session = self._get_session(session_id)
        if session.status in (SessionStatus.ACTIVE, SessionStatus.COMPACTING):
            self._transition(session, SessionStatus.PAUSED)
            # Close transcript writer
            self._close_transcript_writer(session.id)

    def resume_paused(self, session_id: Optional[str] = None):
        """Resume a paused session."""
        session = self._get_session(session_id)
        if session.status == SessionStatus.PAUSED:
            self._transition(session, SessionStatus.ACTIVE)

    def complete_session(self, session_id: Optional[str] = None):
        """Mark a session as completed."""
        session = self._get_session(session_id)
        self._transition(session, SessionStatus.COMPLETED)

        # Generate summary and close resources
        self._finalize_session(session)

        if self._active_session and self._active_session.id == session.id:
            self.deactivate()

        # Clear session_id from logging context
        clear_session_id()

    def fail_session(self, error: str, session_id: Optional[str] = None):
        """Mark a session as failed."""
        session = self._get_session(session_id)
        session.error_count += 1
        self._transition(session, SessionStatus.FAILED)

        self._finalize_session(session)
        logger.error("Session %s failed: %s", session.id[:8], error)

        if self._active_session and self._active_session.id == session.id:
            self.deactivate()

    def archive_session(self, session_id: Optional[str] = None):
        """Archive a session."""
        session = self._get_session(session_id)
        self._transition(session, SessionStatus.ARCHIVED)

        if self._active_session and self._active_session.id == session.id:
            self.deactivate()

        # Clear session_id from logging context
        clear_session_id()

    def _get_session(self, session_id: Optional[str]) -> Session:
        """Get session by ID or use active session."""
        if session_id:
            session = self.store.get_session(session_id)
            if not session:
                raise ValueError(f"Session {session_id} not found")
            return session

        if not self._active_session:
            raise ValueError("No active session")

        return self._active_session

    # ========== Turn Recording ==========

    def record_turn(
        self,
        role: str,
        content: str,
        token_count: int = 0,
        tool_calls: Optional[List[Dict]] = None,
        tool_call_id: Optional[str] = None,
        latency_ms: float = 0.0,
        importance: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> Turn:
        """Record a conversation turn (dual-write: SQLite + JSONL)."""
        session = self._get_session(session_id)

        turn = Turn(
            session_id=session.id,
            turn_number=session.turn_count + 1,
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            token_count=token_count,
            importance=importance,
            metadata=metadata or {},
            latency_ms=latency_ms,
        )

        # SQLite write
        self.store.add_turn(turn)

        # JSONL transcript write
        writer = self._get_transcript_writer(session.id)
        if writer:
            writer.append({
                "type": "message",
                "role": role,
                "content": content,
                "tool_calls": tool_calls,
                "tool_call_id": tool_call_id,
                "turn_number": turn.turn_number,
                "token_count": token_count,
                "metadata": metadata or {},
                "session_id": session.id,
            })

        # Update session stats
        session.add_turn(token_count)
        if role == "assistant":
            session.llm_call_count += 1
        elif role == "tool":
            session.tool_call_count += 1

        self.store.update_session(session)
        self._update_index(session, preview=content[:100] if role == "user" else "")

        return turn

    def get_history(
        self,
        session_id: Optional[str] = None,
        recent_n: Optional[int] = None
    ) -> List[Turn]:
        """Get conversation history."""
        session = self._get_session(session_id)

        if recent_n:
            return self.store.get_recent_turns(session.id, recent_n)

        return self.store.get_turns(session.id)

    # ========== Session Logger Access ==========

    def get_session_logger(self, session_id: Optional[str] = None) -> Optional[SessionLogger]:
        """Get the SessionLogger for a session."""
        sid = session_id or (self._active_session.id if self._active_session else None)
        if sid:
            return self._session_loggers.get(sid)
        return None

    def get_output_dir(self, name: str, session_id: Optional[str] = None) -> str:
        """Get a session-scoped output directory for a given task/skill name.

        All session artifacts (downloads, datasets, code, etc.) should be
        saved under this directory so that each session is self-contained.

        The directory is created at ``{session_dir}/output_{name}/`` and is
        automatically created if it does not exist.

        Args:
            name: Task or skill name (e.g. "rankmixer", "stock_analysis").
            session_id: Optional session ID (defaults to active session).

        Returns:
            Absolute path to the output directory.

        Raises:
            ValueError: If no active session or session has no session_dir.
        """
        session = self._get_session(session_id)
        if not session.session_dir:
            raise ValueError(f"Session {session.id[:8]} has no session_dir")

        # Sanitize name for filesystem
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        output_dir = Path(session.session_dir) / f"output_{safe_name}"
        output_dir.mkdir(parents=True, exist_ok=True)
        return str(output_dir)

    # ========== Checkpoint Management ==========

    def checkpoint(self, name: Optional[str] = None, session_id: Optional[str] = None) -> str:
        """Create a checkpoint for the current session state."""
        session = self._get_session(session_id)

        turns = self.store.get_turns(session.id)
        messages = [
            {
                "role": t.role,
                "content": t.content,
                "tool_calls": t.tool_calls,
                "tool_call_id": t.tool_call_id,
                "turn_number": t.turn_number,
            }
            for t in turns
        ]

        cp = Checkpoint(
            session_id=session.id,
            name=name or f"Checkpoint at turn {session.turn_count}",
            turn_number=session.turn_count,
            messages_snapshot=messages,
            budget_snapshot=TokenBudget(
                total=session.budget.total,
                reserved=session.budget.reserved,
                used=session.budget.used
            )
        )

        self.store.create_checkpoint(cp)
        logger.info("Created checkpoint %s for session %s", cp.id[:8], session.id[:8])
        logger.debug(
            "[Session] Checkpoint | session_id=%s | checkpoint_id=%s | turn_number=%d",
            session.id[:8], cp.id[:8], cp.turn_number
        )
        return cp.id

    def list_checkpoints(self, session_id: Optional[str] = None) -> List[Checkpoint]:
        """List checkpoints for a session."""
        session = self._get_session(session_id)
        return self.store.list_checkpoints(session.id)

    def restore_checkpoint(self, checkpoint_id: str, new_name: Optional[str] = None) -> Session:
        """Restore from a checkpoint (creates a fork)."""
        cp = self.store.get_checkpoint(checkpoint_id)
        if not cp:
            raise ValueError(f"Checkpoint {checkpoint_id} not found")

        new_session = self.create_session(
            name=new_name or f"Restored from {cp.name}",
        )
        # Record lineage without triggering fork copy
        new_session.parent_id = cp.session_id

        new_session.budget = TokenBudget(
            total=cp.budget_snapshot.total,
            reserved=cp.budget_snapshot.reserved,
            used=cp.budget_snapshot.used
        )

        for idx, msg in enumerate(cp.messages_snapshot, start=1):
            turn = Turn(
                session_id=new_session.id,
                turn_number=msg.get("turn_number") or idx,
                role=msg.get("role", ""),
                content=msg.get("content", ""),
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id")
            )
            self.store.add_turn(turn)
            # Also write to transcript
            writer = self._get_transcript_writer(new_session.id)
            if writer:
                writer.append({
                    "type": "message",
                    "role": turn.role,
                    "content": turn.content,
                    "turn_number": turn.turn_number,
                })

        new_session.turn_count = cp.turn_number
        new_session.status = SessionStatus.ACTIVE
        self.store.update_session(new_session)

        logger.info("Restored checkpoint %s to session %s", checkpoint_id[:8], new_session.id[:8])
        logger.info(
            "[Session] Forked | parent_id=%s | child_id=%s | inherited_turns=%d",
            cp.session_id[:8], new_session.id[:8], len(cp.messages_snapshot)
        )
        return new_session

    # ========== Budget Management ==========

    def update_token_usage(self, used: int, session_id: Optional[str] = None):
        """Update token usage for a session."""
        session = self._get_session(session_id)
        session.budget.used += used
        session.touch()
        self.store.update_session(session)

    def get_remaining_budget(self, session_id: Optional[str] = None) -> int:
        """Get remaining token budget."""
        session = self._get_session(session_id)
        return session.budget.available

    def is_budget_exceeded(self, session_id: Optional[str] = None) -> bool:
        """Check if budget is exceeded."""
        return self.get_remaining_budget(session_id) <= 0

    # ========== Listing and Queries ==========

    def list_sessions(
        self,
        status: Optional[SessionStatus] = None,
        limit: int = 20
    ) -> List[SessionSummary]:
        """List sessions as summaries."""
        sessions = self.store.list_sessions(status=status, limit=limit)

        summaries = []
        for s in sessions:
            preview = ""
            recent = self.store.get_recent_turns(s.id, 3)
            for t in reversed(recent):
                if t.role == "user":
                    preview = t.content[:100]
                    break

            summaries.append(SessionSummary(
                id=s.id,
                name=s.name,
                status=s.status,
                turn_count=s.turn_count,
                created_at=s.created_at,
                updated_at=s.updated_at,
                preview=preview
            ))

        return summaries

    def get_session_stats(self, session_id: str) -> Dict[str, Any]:
        """Get detailed statistics for a session."""
        session = self.store.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        return {
            "id": session.id,
            "name": session.name,
            "status": session.status.value,
            "turn_count": session.turn_count,
            "token_budget": {
                "total": session.budget.total,
                "reserved": session.budget.reserved,
                "used": session.budget.used,
                "available": session.budget.available,
                "utilization": f"{session.budget.utilization_rate:.1%}"
            },
            "llm_calls": session.llm_call_count,
            "tool_calls": session.tool_call_count,
            "errors": session.error_count,
            "duration_minutes": session.total_duration_ms / 60000,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat()
        }

    # ========== Maintenance ==========

    def archive_old_sessions(self, days: int = 30) -> int:
        """Archive sessions inactive for more than N days."""
        cutoff = datetime.now() - timedelta(days=days)

        count = 0
        with self.store._get_connection() as conn:
            rows = conn.execute(
                """SELECT id FROM sessions
                   WHERE status NOT IN ('archived', 'completed', 'failed')
                   AND updated_at < ?""",
                (cutoff.timestamp(),)
            ).fetchall()

            for row in rows:
                session = self.store.get_session(row['id'])
                if session:
                    self._transition(session, SessionStatus.ARCHIVED)
                    count += 1

        logger.info("Archived %d old sessions", count)
        return count

    def cleanup_archived(self, days: int = 90) -> int:
        """Permanently delete archived sessions older than N days."""
        cutoff = datetime.now() - timedelta(days=days)

        with self.store._get_connection() as conn:
            cursor = conn.execute(
                """DELETE FROM sessions
                   WHERE status = 'archived'
                   AND updated_at < ?""",
                (cutoff.timestamp(),)
            )
            conn.commit()
            count = cursor.rowcount

        logger.info("Cleaned up %d archived sessions", count)
        return count

    # ========== Internal Helpers ==========

    def _build_config_snapshot(self) -> Dict[str, Any]:
        """Build a config snapshot from current AppConfig."""
        provider_val = self.config.llm.provider
        if hasattr(provider_val, 'value'):
            provider_val = provider_val.value
        return {
            "provider": provider_val,
            "model": self.config.llm.model,
            "temperature": self.config.llm.temperature,
            "max_tokens": self.config.llm.max_tokens,
        }

    def _get_transcript_writer(self, session_id: str) -> Optional[TranscriptWriter]:
        """Get the transcript writer for a session."""
        writer = self._transcript_writers.get(session_id)
        if writer and not writer.closed:
            return writer
        return None

    def _close_transcript_writer(self, session_id: str) -> None:
        """Close and remove the transcript writer for a session."""
        writer = self._transcript_writers.pop(session_id, None)
        if writer and not writer.closed:
            writer.close()

    def _update_index(self, session: Session, preview: str = "") -> None:
        """Update the session index entry."""
        entry = SessionIndexEntry.from_session(session, preview=preview)
        self._index.update(entry)

    def _finalize_session(self, session: Session) -> None:
        """Close resources and generate summary for a finished session."""
        # Generate summary via session logger
        session_logger = self._session_loggers.get(session.id)
        if session_logger and not session_logger.closed:
            try:
                session_logger.generate_summary(session)
            except Exception as e:
                logger.warning("Failed to generate session summary: %s", e)
            session_logger.close()
            del self._session_loggers[session.id]

        # Close transcript writer
        self._close_transcript_writer(session.id)

        # Update index
        self._update_index(session)


# Convenience function
def get_default_manager(config: Optional[AppConfig] = None) -> SessionManager:
    """Get the default SessionManager."""
    from .session_store import get_default_store
    from src.core.config import load_config

    store = get_default_store()
    cfg = config or load_config()
    return SessionManager(store, cfg)
