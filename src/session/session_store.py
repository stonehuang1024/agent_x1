"""SQLite persistence layer for Session management."""

import sqlite3
import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from .models import Session, SessionStatus, Turn, Checkpoint, TokenBudget

logger = logging.getLogger(__name__)


class SessionStore:
    """
    SQLite persistence layer for sessions.
    
    Handles all database operations for session CRUD,
    turn recording, and checkpoint management.
    """
    
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_tables()
    
    def _ensure_tables(self):
        """Ensure database tables exist."""
        # Run the migration script
        migrations_dir = Path(__file__).parent.parent.parent / "data" / "migrations"
        migration_file = migrations_dir / "001_init.sql"
        
        if migration_file.exists():
            with sqlite3.connect(self.db_path) as conn:
                with open(migration_file, 'r') as f:
                    conn.executescript(f.read())
                conn.commit()
                logger.debug("Database initialized")
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    # ========== Session CRUD ==========
    
    def create_session(self, session: Session) -> Session:
        """Create a new session in the database."""
        with self._get_connection() as conn:
            conn.execute(
                """INSERT INTO sessions (
                    id, parent_id, name, status, created_at, updated_at,
                    config_snapshot, token_budget_total, token_budget_reserved,
                    working_dir, session_dir
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session.id,
                    session.parent_id,
                    session.name,
                    session.status.value,
                    session.created_at.timestamp(),
                    session.updated_at.timestamp(),
                    json.dumps(session.config_snapshot),
                    session.budget.total,
                    session.budget.reserved,
                    session.working_dir,
                    session.session_dir
                )
            )
            conn.commit()
            logger.info(f"Created session {session.id[:8]}")
        return session
    
    def get_session(self, session_id: str) -> Optional[Session]:
        """Retrieve a session by ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?",
                (session_id,)
            ).fetchone()
            
            if not row:
                return None
            
            return self._row_to_session(row)
    
    def update_session(self, session: Session) -> None:
        """Update an existing session."""
        session.touch()
        
        with self._get_connection() as conn:
            conn.execute(
                """UPDATE sessions SET
                    parent_id = ?,
                    name = ?,
                    status = ?,
                    updated_at = ?,
                    ended_at = ?,
                    config_snapshot = ?,
                    token_budget_used = ?,
                    turn_count = ?,
                    total_duration_ms = ?,
                    llm_call_count = ?,
                    tool_call_count = ?,
                    error_count = ?
                WHERE id = ?""",
                (
                    session.parent_id,
                    session.name,
                    session.status.value,
                    session.updated_at.timestamp(),
                    session.ended_at.timestamp() if session.ended_at else None,
                    json.dumps(session.config_snapshot),
                    session.budget.used,
                    session.turn_count,
                    session.total_duration_ms,
                    session.llm_call_count,
                    session.tool_call_count,
                    session.error_count,
                    session.id
                )
            )
            conn.commit()
            session.is_dirty = False
    
    def list_sessions(
        self,
        status: Optional[SessionStatus] = None,
        limit: int = 20,
        offset: int = 0
    ) -> List[Session]:
        """List sessions with optional filtering."""
        with self._get_connection() as conn:
            if status:
                rows = conn.execute(
                    """SELECT * FROM sessions 
                       WHERE status = ? 
                       ORDER BY updated_at DESC 
                       LIMIT ? OFFSET ?""",
                    (status.value, limit, offset)
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM sessions 
                       WHERE status != 'archived'
                       ORDER BY updated_at DESC 
                       LIMIT ? OFFSET ?""",
                    (limit, offset)
                ).fetchall()
            
            return [self._row_to_session(row) for row in rows]
    
    def delete_session(self, session_id: str, hard: bool = False) -> None:
        """
        Delete a session.
        
        Args:
            hard: If True, physically delete. Otherwise, mark as archived.
        """
        with self._get_connection() as conn:
            if hard:
                conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            else:
                conn.execute(
                    """UPDATE sessions 
                       SET status = 'archived', ended_at = ? 
                       WHERE id = ?""",
                    (datetime.now().timestamp(), session_id)
                )
            conn.commit()
    
    # ========== Turn Operations ==========
    
    def add_turn(self, turn: Turn) -> Turn:
        """Add a turn record."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO turns (
                    session_id, turn_number, role, content,
                    tool_calls, tool_call_id, token_count, importance, latency_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    turn.session_id,
                    turn.turn_number,
                    turn.role,
                    turn.content,
                    json.dumps(turn.tool_calls) if turn.tool_calls else None,
                    turn.tool_call_id,
                    turn.token_count,
                    turn.importance,
                    turn.latency_ms,
                    turn.created_at.timestamp()
                )
            )
            turn.id = cursor.lastrowid
            conn.commit()
        return turn
    
    def get_turns(
        self,
        session_id: str,
        from_turn: Optional[int] = None,
        to_turn: Optional[int] = None
    ) -> List[Turn]:
        """Get turns for a session with optional range filtering."""
        with self._get_connection() as conn:
            query = "SELECT * FROM turns WHERE session_id = ?"
            params = [session_id]
            
            if from_turn is not None:
                query += " AND turn_number >= ?"
                params.append(from_turn)
            
            if to_turn is not None:
                query += " AND turn_number <= ?"
                params.append(to_turn)
            
            query += " ORDER BY turn_number ASC"
            
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_turn(row) for row in rows]
    
    def get_recent_turns(self, session_id: str, n: int = 10) -> List[Turn]:
        """Get the most recent N turns."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """SELECT * FROM turns 
                   WHERE session_id = ? 
                   ORDER BY turn_number DESC 
                   LIMIT ?""",
                (session_id, n)
            ).fetchall()
            return [self._row_to_turn(row) for row in reversed(rows)]
    
    def get_turn_count(self, session_id: str) -> int:
        """Get the total number of turns in a session."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM turns WHERE session_id = ?",
                (session_id,)
            ).fetchone()
            return row[0] if row else 0
    
    def update_turn_importance(self, turn_id: int, importance: float) -> None:
        """Update a turn's importance score."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE turns SET importance = ? WHERE id = ?",
                (importance, turn_id)
            )
            conn.commit()
    
    # ========== Checkpoint Operations ==========
    
    def create_checkpoint(self, checkpoint: Checkpoint) -> Checkpoint:
        """Create a checkpoint."""
        with self._get_connection() as conn:
            conn.execute(
                """INSERT INTO checkpoints (
                    id, session_id, name, turn_number, messages_snapshot,
                    budget_total, budget_reserved, budget_used, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    checkpoint.id,
                    checkpoint.session_id,
                    checkpoint.name,
                    checkpoint.turn_number,
                    json.dumps(checkpoint.messages_snapshot),
                    checkpoint.budget_snapshot.total,
                    checkpoint.budget_snapshot.reserved,
                    checkpoint.budget_snapshot.used,
                    checkpoint.created_at.timestamp()
                )
            )
            conn.commit()
        return checkpoint
    
    def get_checkpoint(self, checkpoint_id: str) -> Optional[Checkpoint]:
        """Get a checkpoint by ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM checkpoints WHERE id = ?",
                (checkpoint_id,)
            ).fetchone()
            
            if not row:
                return None
            
            return self._row_to_checkpoint(row)
    
    def list_checkpoints(self, session_id: str) -> List[Checkpoint]:
        """List all checkpoints for a session."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """SELECT * FROM checkpoints 
                   WHERE session_id = ? 
                   ORDER BY created_at DESC""",
                (session_id,)
            ).fetchall()
            return [self._row_to_checkpoint(row) for row in rows]
    
    def delete_checkpoint(self, checkpoint_id: str) -> None:
        """Delete a checkpoint."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM checkpoints WHERE id = ?", (checkpoint_id,))
            conn.commit()
    
    # ========== Helper Methods ==========
    
    def _row_to_session(self, row: sqlite3.Row) -> Session:
        """Convert a database row to Session."""
        return Session(
            id=row['id'],
            parent_id=row['parent_id'],
            name=row['name'],
            status=SessionStatus(row['status']),
            created_at=datetime.fromtimestamp(row['created_at']),
            updated_at=datetime.fromtimestamp(row['updated_at']),
            ended_at=datetime.fromtimestamp(row['ended_at']) if row['ended_at'] else None,
            config_snapshot=json.loads(row['config_snapshot']),
            budget=TokenBudget(
                total=row['token_budget_total'],
                reserved=row['token_budget_reserved'],
                used=row['token_budget_used']
            ),
            turn_count=row['turn_count'],
            total_duration_ms=row['total_duration_ms'],
            llm_call_count=row['llm_call_count'],
            tool_call_count=row['tool_call_count'],
            error_count=row['error_count'],
            working_dir=row['working_dir'],
            session_dir=row['session_dir']
        )
    
    def _row_to_turn(self, row: sqlite3.Row) -> Turn:
        """Convert a database row to Turn."""
        return Turn(
            id=row['id'],
            session_id=row['session_id'],
            turn_number=row['turn_number'],
            role=row['role'],
            content=row['content'],
            tool_calls=json.loads(row['tool_calls']) if row['tool_calls'] else None,
            tool_call_id=row['tool_call_id'],
            token_count=row['token_count'],
            importance=row['importance'],
            latency_ms=row['latency_ms'],
            created_at=datetime.fromtimestamp(row['created_at'])
        )
    
    def _row_to_checkpoint(self, row: sqlite3.Row) -> Checkpoint:
        """Convert a database row to Checkpoint."""
        return Checkpoint(
            id=row['id'],
            session_id=row['session_id'],
            name=row['name'],
            turn_number=row['turn_number'],
            messages_snapshot=json.loads(row['messages_snapshot']),
            budget_snapshot=TokenBudget(
                total=row['budget_total'],
                reserved=row['budget_reserved'],
                used=row['budget_used']
            ),
            created_at=datetime.fromtimestamp(row['created_at'])
        )


# Convenience function
def get_default_store() -> SessionStore:
    """Get the default SessionStore instance."""
    from src.core.config import load_config
    config = load_config()
    db_path = Path(config.paths.data_dir) / "agent_x1.db"
    return SessionStore(str(db_path))
