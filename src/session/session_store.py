"""SQLite persistence layer for Session management."""

import sqlite3
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from .models import (
    Session, SessionStatus, SessionType, Turn, Checkpoint,
    TokenBudget, TurnContext, TurnStatus,
)

logger = logging.getLogger(__name__)


class SessionStore:
    """
    SQLite persistence layer for sessions.

    Handles all database operations for session CRUD,
    turn recording, checkpoint management, and turn context persistence.
    Uses versioned migrations for schema evolution.
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_tables()

    def _ensure_tables(self):
        """Run pending database migrations in version order."""
        migrations_dir = Path(__file__).parent.parent.parent / "data" / "migrations"

        with sqlite3.connect(str(self.db_path)) as conn:
            # Ensure schema_migrations table exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at REAL NOT NULL,
                    description TEXT
                )
            """)
            conn.commit()

            # Determine already-applied versions
            rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
            applied_versions = {row[0] for row in rows}

            # Discover migration files: NNN_*.sql
            if not migrations_dir.exists():
                logger.warning("Migrations directory not found: %s", migrations_dir)
                return

            migration_files: List[tuple] = []
            for f in sorted(migrations_dir.glob("*.sql")):
                match = re.match(r"^(\d+)_", f.name)
                if match:
                    version = int(match.group(1))
                    migration_files.append((version, f))

            # Execute pending migrations in order
            for version, filepath in sorted(migration_files, key=lambda x: x[0]):
                if version in applied_versions:
                    continue
                try:
                    with open(filepath, "r") as mf:
                        sql = mf.read()
                    conn.executescript(sql)
                    # Record migration (may already be recorded by the script itself)
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?)",
                            (version, time.time(), filepath.stem),
                        )
                    except sqlite3.IntegrityError:
                        pass
                    conn.commit()
                    logger.info("Applied migration %03d: %s", version, filepath.name)
                except Exception as e:
                    logger.error("Failed to apply migration %03d (%s): %s", version, filepath.name, e)
                    raise

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ========== Session CRUD ==========

    def create_session(self, session: Session) -> Session:
        """Create a new session in the database."""
        start = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """INSERT INTO sessions (
                    id, parent_id, name, status, created_at, updated_at,
                    config_snapshot, token_budget_total, token_budget_reserved,
                    working_dir, session_dir,
                    agent_id, session_type, transcript_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    session.session_dir,
                    session.agent_id,
                    session.session_type.value,
                    session.transcript_path,
                )
            )
            conn.commit()
            duration_ms = (time.time() - start) * 1000
            logger.debug(
                "[SessionStore] SQL | operation=INSERT | session_id=%s | duration=%.1fms",
                session.id[:8], duration_ms
            )
            logger.info("Created session %s", session.id[:8])
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
        start = time.time()

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
                    error_count = ?,
                    agent_id = ?,
                    session_type = ?,
                    transcript_path = ?
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
                    session.agent_id,
                    session.session_type.value,
                    session.transcript_path,
                    session.id
                )
            )
            conn.commit()
            session.is_dirty = False
        duration_ms = (time.time() - start) * 1000
        logger.debug(
            "[SessionStore] SQL | operation=UPDATE | session_id=%s | duration=%.1fms",
            session.id[:8], duration_ms
        )

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
        """Delete a session (soft or hard)."""
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
                    tool_calls, tool_call_id, token_count, importance,
                    latency_ms, created_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    turn.created_at.timestamp(),
                    json.dumps(turn.metadata),
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
            params: list = [session_id]

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

    # ========== TurnContext Operations ==========

    def save_turn_context(self, ctx: TurnContext) -> None:
        """Persist a TurnContext to the database."""
        with self._get_connection() as conn:
            conn.execute(
                """INSERT INTO turn_contexts (
                    session_id, turn_number, working_dir, model, provider,
                    temperature, max_tokens, tool_configs, approval_policy,
                    behavior_settings, token_usage, tool_call_records,
                    latency_stats, error, started_at, completed_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ctx.session_id,
                    ctx.turn_number,
                    ctx.working_dir,
                    ctx.model,
                    ctx.provider,
                    ctx.temperature,
                    ctx.max_tokens,
                    json.dumps(ctx.tool_configs),
                    ctx.approval_policy,
                    json.dumps(ctx.behavior_settings),
                    json.dumps(ctx.token_usage),
                    json.dumps(ctx.tool_call_records),
                    json.dumps(ctx.latency_stats),
                    ctx.error,
                    ctx.started_at,
                    ctx.completed_at,
                    ctx.status.value,
                )
            )
            conn.commit()

    def update_turn_context(self, ctx: TurnContext) -> None:
        """Update an existing TurnContext."""
        with self._get_connection() as conn:
            conn.execute(
                """UPDATE turn_contexts SET
                    token_usage = ?,
                    tool_call_records = ?,
                    latency_stats = ?,
                    error = ?,
                    completed_at = ?,
                    status = ?
                WHERE session_id = ? AND turn_number = ?""",
                (
                    json.dumps(ctx.token_usage),
                    json.dumps(ctx.tool_call_records),
                    json.dumps(ctx.latency_stats),
                    ctx.error,
                    ctx.completed_at,
                    ctx.status.value,
                    ctx.session_id,
                    ctx.turn_number,
                )
            )
            conn.commit()

    def get_turn_context(self, session_id: str, turn_number: int) -> Optional[TurnContext]:
        """Retrieve a single TurnContext."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM turn_contexts WHERE session_id = ? AND turn_number = ?",
                (session_id, turn_number)
            ).fetchone()
            if not row:
                return None
            return self._row_to_turn_context(row)

    def get_all_turn_contexts(self, session_id: str) -> List[TurnContext]:
        """Retrieve all TurnContexts for a session, ordered by turn_number."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM turn_contexts WHERE session_id = ? ORDER BY turn_number ASC",
                (session_id,)
            ).fetchall()
            return [self._row_to_turn_context(row) for row in rows]

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
        row_dict = dict(row)

        # Read new fields with backward-compatible defaults
        agent_id = row_dict.get('agent_id', None)
        session_type_val = row_dict.get('session_type', 'primary')
        transcript_path = row_dict.get('transcript_path', '')

        return Session(
            id=row_dict['id'],
            parent_id=row_dict['parent_id'],
            name=row_dict['name'],
            status=SessionStatus(row_dict['status']),
            agent_id=agent_id,
            session_type=SessionType(session_type_val) if session_type_val else SessionType.PRIMARY,
            transcript_path=transcript_path or '',
            created_at=datetime.fromtimestamp(row_dict['created_at']),
            updated_at=datetime.fromtimestamp(row_dict['updated_at']),
            ended_at=datetime.fromtimestamp(row_dict['ended_at']) if row_dict.get('ended_at') else None,
            config_snapshot=json.loads(row_dict['config_snapshot']),
            budget=TokenBudget(
                total=row_dict['token_budget_total'],
                reserved=row_dict['token_budget_reserved'],
                used=row_dict['token_budget_used']
            ),
            turn_count=row_dict['turn_count'],
            total_duration_ms=row_dict['total_duration_ms'],
            llm_call_count=row_dict['llm_call_count'],
            tool_call_count=row_dict['tool_call_count'],
            error_count=row_dict['error_count'],
            working_dir=row_dict['working_dir'],
            session_dir=row_dict['session_dir']
        )

    def _row_to_turn(self, row: sqlite3.Row) -> Turn:
        """Convert a database row to Turn."""
        row_dict = dict(row)
        metadata_raw = row_dict.get('metadata', '{}')
        metadata = json.loads(metadata_raw) if metadata_raw else {}

        return Turn(
            id=row_dict['id'],
            session_id=row_dict['session_id'],
            turn_number=row_dict['turn_number'],
            role=row_dict['role'],
            content=row_dict['content'],
            tool_calls=json.loads(row_dict['tool_calls']) if row_dict['tool_calls'] else None,
            tool_call_id=row_dict['tool_call_id'],
            token_count=row_dict['token_count'],
            importance=row_dict['importance'],
            metadata=metadata,
            latency_ms=row_dict['latency_ms'],
            created_at=datetime.fromtimestamp(row_dict['created_at'])
        )

    def _row_to_turn_context(self, row: sqlite3.Row) -> TurnContext:
        """Convert a database row to TurnContext."""
        row_dict = dict(row)
        ctx = TurnContext(
            session_id=row_dict['session_id'],
            turn_number=row_dict['turn_number'],
            working_dir=row_dict['working_dir'],
            model=row_dict['model'],
            provider=row_dict['provider'],
            temperature=row_dict['temperature'],
            max_tokens=row_dict['max_tokens'],
            tool_configs=json.loads(row_dict['tool_configs']),
            approval_policy=row_dict['approval_policy'],
            behavior_settings=json.loads(row_dict['behavior_settings']),
            started_at=row_dict['started_at'],
            status=TurnStatus(row_dict['status']),
        )
        ctx.token_usage = json.loads(row_dict['token_usage'])
        ctx.tool_call_records = json.loads(row_dict['tool_call_records'])
        ctx.latency_stats = json.loads(row_dict['latency_stats'])
        ctx.error = row_dict['error']
        ctx.completed_at = row_dict['completed_at']
        return ctx

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
