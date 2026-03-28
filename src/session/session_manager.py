"""Session lifecycle management."""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Callable, Dict, Any

from .models import Session, SessionStatus, Turn, Checkpoint, TokenBudget, SessionSummary
from .session_store import SessionStore
from src.core.config import AppConfig
from src.core.events import EventBus, AgentEvent

logger = logging.getLogger(__name__)


class SessionManager:
    """
    High-level session lifecycle management.
    
    Provides convenient API for creating, resuming, and managing sessions.
    """
    
    def __init__(self, store: SessionStore, config: AppConfig, event_bus: Optional[EventBus] = None):
        self.store = store
        self.config = config
        self.event_bus = event_bus
        self._active_session: Optional[Session] = None
        self._state_change_callbacks: List[Callable[[Session, SessionStatus, SessionStatus], None]] = []
    
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
                logger.error(f"State change callback error: {e}")
        
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
                SessionStatus.FORKED: AgentEvent.SESSION_FORKED,
            }
            event_type = event_map.get(new)
            if event_type:
                self.event_bus.emit(
                    event_type,
                    session_id=session.id,
                    old_status=old.value,
                    new_status=new.value
                )
        except Exception as e:
            logger.debug(f"EventBus emit error: {e}")
    
    # ========== Session Creation ==========
    
    def create_session(
        self,
        name: Optional[str] = None,
        parent_id: Optional[str] = None,
        working_dir: Optional[str] = None
    ) -> Session:
        """
        Create a new session.
        
        Args:
            name: Optional session name
            parent_id: Optional parent session ID for forking
            working_dir: Working directory (defaults to current)
        
        Returns:
            The created Session
        """
        import os
        
        # Validate parent if provided
        parent = None
        if parent_id:
            parent = self.store.get_session(parent_id)
            if not parent:
                raise ValueError(f"Parent session {parent_id} not found")
        
        # Create session directory
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        session_dir = Path(self.config.paths.result_dir) / f"session_{timestamp}"
        session_dir.mkdir(parents=True, exist_ok=True)
        
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
        
        session = Session(
            parent_id=parent_id,
            name=name,
            status=SessionStatus.CREATED,
            config_snapshot=config_snapshot,
            budget=TokenBudget(
                total=self.config.llm.max_tokens * 10,  # Rough estimate
                reserved=8192,
                used=0
            ),
            working_dir=working_dir or os.getcwd(),
            session_dir=str(session_dir)
        )
        
        self.store.create_session(session)
        
        # If fork, copy parent turns
        if parent:
            turns = self.store.get_turns(parent_id)
            for turn in turns:
                turn.id = 0
                turn.session_id = session.id
                self.store.add_turn(turn)
            session.turn_count = len(turns)
            session.status = SessionStatus.ACTIVE
            self.store.update_session(session)
            
            # Mark parent as forked
            old_status = parent.status
            parent.status = SessionStatus.FORKED
            self.store.update_session(parent)
            self._notify_state_change(parent, old_status, SessionStatus.FORKED)
        
        logger.info(f"Created session {session.id[:8]} (name={name}, parent={parent_id})")
        return session
    
    # ========== Session Activation ==========
    
    def resume_session(self, session_id: str) -> Session:
        """Resume an existing session."""
        session = self.store.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        if session.status == SessionStatus.ARCHIVED:
            raise ValueError(f"Session {session_id} is archived")
        
        # Warn if config mismatch
        if session.status not in (SessionStatus.ACTIVE, SessionStatus.PAUSED):
            old_status = session.status
            session.status = SessionStatus.ACTIVE
            self.store.update_session(session)
            self._notify_state_change(session, old_status, SessionStatus.ACTIVE)
        
        logger.info(f"Resumed session {session.id[:8]}")
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
            logger.info(f"Deactivated session {self._active_session.id[:8]}")
            self._active_session = None
    
    # ========== State Transitions ==========
    
    def _transition(self, session: Session, new_status: SessionStatus):
        """Internal method to transition session state."""
        old_status = session.status
        if old_status == new_status:
            return
        
        session.status = new_status
        session.updated_at = datetime.now()
        
        if new_status in (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.ARCHIVED):
            session.ended_at = datetime.now()
        
        self.store.update_session(session)
        self._notify_state_change(session, old_status, new_status)
        logger.info(f"Session {session.id[:8]}: {old_status.value} -> {new_status.value}")
    
    def pause_session(self, session_id: Optional[str] = None):
        """Pause a session."""
        session = self._get_session(session_id)
        if session.status == SessionStatus.ACTIVE:
            self._transition(session, SessionStatus.PAUSED)
    
    def resume_paused(self, session_id: Optional[str] = None):
        """Resume a paused session."""
        session = self._get_session(session_id)
        if session.status == SessionStatus.PAUSED:
            self._transition(session, SessionStatus.ACTIVE)
    
    def complete_session(self, session_id: Optional[str] = None):
        """Mark a session as completed."""
        session = self._get_session(session_id)
        self._transition(session, SessionStatus.COMPLETED)
        
        if self._active_session and self._active_session.id == session.id:
            self.deactivate()
    
    def fail_session(self, error: str, session_id: Optional[str] = None):
        """Mark a session as failed."""
        session = self._get_session(session_id)
        session.error_count += 1
        self._transition(session, SessionStatus.FAILED)
        logger.error(f"Session {session.id[:8]} failed: {error}")
        
        if self._active_session and self._active_session.id == session.id:
            self.deactivate()
    
    def archive_session(self, session_id: Optional[str] = None):
        """Archive a session."""
        session = self._get_session(session_id)
        self._transition(session, SessionStatus.ARCHIVED)
        
        if self._active_session and self._active_session.id == session.id:
            self.deactivate()
    
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
        session_id: Optional[str] = None
    ) -> Turn:
        """Record a conversation turn."""
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
            latency_ms=latency_ms
        )
        
        self.store.add_turn(turn)
        session.add_turn(token_count)
        
        # Update statistics
        if role == "assistant":
            session.llm_call_count += 1
        elif role == "tool":
            session.tool_call_count += 1
        
        self.store.update_session(session)
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
    
    # ========== Checkpoint Management ==========
    
    def checkpoint(self, name: Optional[str] = None, session_id: Optional[str] = None) -> str:
        """Create a checkpoint for the current session state."""
        session = self._get_session(session_id)
        
        # Get current turns
        turns = self.store.get_turns(session.id)
        messages = [
            {
                "role": t.role,
                "content": t.content,
                "tool_calls": t.tool_calls,
                "tool_call_id": t.tool_call_id
            }
            for t in turns
        ]
        
        checkpoint = Checkpoint(
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
        
        self.store.create_checkpoint(checkpoint)
        logger.info(f"Created checkpoint {checkpoint.id[:8]} for session {session.id[:8]}")
        return checkpoint.id
    
    def list_checkpoints(self, session_id: Optional[str] = None) -> List[Checkpoint]:
        """List checkpoints for a session."""
        session = self._get_session(session_id)
        return self.store.list_checkpoints(session.id)
    
    def restore_checkpoint(self, checkpoint_id: str, new_name: Optional[str] = None) -> Session:
        """Restore from a checkpoint (creates a fork)."""
        checkpoint = self.store.get_checkpoint(checkpoint_id)
        if not checkpoint:
            raise ValueError(f"Checkpoint {checkpoint_id} not found")
        
        # Create new session as fork
        new_session = self.create_session(
            name=new_name or f"Restored from {checkpoint.name}",
            parent_id=checkpoint.session_id
        )
        
        # Restore budget
        new_session.budget = TokenBudget(
            total=checkpoint.budget_snapshot.total,
            reserved=checkpoint.budget_snapshot.reserved,
            used=checkpoint.budget_snapshot.used
        )
        
        # Restore turns from snapshot
        for msg in checkpoint.messages_snapshot:
            turn = Turn(
                session_id=new_session.id,
                turn_number=msg.get("turn_number", 0),
                role=msg.get("role", ""),
                content=msg.get("content", ""),
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id")
            )
            self.store.add_turn(turn)
        
        new_session.turn_count = checkpoint.turn_number
        self.store.update_session(new_session)
        
        logger.info(f"Restored checkpoint {checkpoint_id[:8]} to session {new_session.id[:8]}")
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
            # Get preview from latest user message
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
        
        logger.info(f"Archived {count} old sessions")
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
        
        logger.info(f"Cleaned up {count} archived sessions")
        return count


# Convenience function
def get_default_manager(config: Optional[AppConfig] = None) -> SessionManager:
    """Get the default SessionManager."""
    from .session_store import get_default_store
    from src.core.config import load_config
    
    store = get_default_store()
    cfg = config or load_config()
    return SessionManager(store, cfg)
