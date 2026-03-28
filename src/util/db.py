"""Database utilities for SQLite management and migrations."""

import sqlite3
import logging
from pathlib import Path
from typing import Optional, Any, Dict, List
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages SQLite database connections and migrations."""
    
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection: Optional[sqlite3.Connection] = None
        
        # Initialize on first use
        self._ensure_db()
    
    def _ensure_db(self):
        """Ensure database exists with proper settings."""
        with self._get_connection() as conn:
            # WAL mode for better concurrency
            conn.execute("PRAGMA journal_mode=WAL")
            # Enable foreign keys
            conn.execute("PRAGMA foreign_keys=ON")
            # Synchronous mode for safety/performance balance
            conn.execute("PRAGMA synchronous=NORMAL")
            
            self._run_migrations(conn)
    
    def _run_migrations(self, conn: sqlite3.Connection):
        """Run all pending migration scripts."""
        migrations_dir = Path(__file__).parent.parent.parent / "data" / "migrations"
        
        if not migrations_dir.exists():
            logger.warning(f"Migrations directory not found: {migrations_dir}")
            return
        
        # Get list of migration files
        migration_files = sorted(migrations_dir.glob("*.sql"))
        
        for migration_file in migration_files:
            try:
                with open(migration_file, 'r', encoding='utf-8') as f:
                    sql = f.read()
                
                conn.executescript(sql)
                conn.commit()
                logger.info(f"Applied migration: {migration_file.name}")
                
            except Exception as e:
                logger.error(f"Failed to apply migration {migration_file.name}: {e}")
                raise
    
    @contextmanager
    def _get_connection(self):
        """Get a database connection with proper row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def get_connection(self) -> sqlite3.Connection:
        """
        Get a new database connection.
        
        Caller is responsible for closing.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def execute(
        self,
        sql: str,
        parameters: tuple = (),
        commit: bool = True
    ) -> sqlite3.Cursor:
        """
        Execute a SQL query.
        
        Returns the cursor for result access.
        """
        with self._get_connection() as conn:
            cursor = conn.execute(sql, parameters)
            if commit:
                conn.commit()
            return cursor
    
    def executemany(
        self,
        sql: str,
        parameters_list: List[tuple],
        commit: bool = True
    ) -> sqlite3.Cursor:
        """Execute a SQL query multiple times."""
        with self._get_connection() as conn:
            cursor = conn.executemany(sql, parameters_list)
            if commit:
                conn.commit()
            return cursor
    
    def fetchone(self, sql: str, parameters: tuple = ()) -> Optional[Dict[str, Any]]:
        """Fetch a single row as dict."""
        cursor = self.execute(sql, parameters, commit=False)
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    
    def fetchall(self, sql: str, parameters: tuple = ()) -> List[Dict[str, Any]]:
        """Fetch all rows as list of dicts."""
        cursor = self.execute(sql, parameters, commit=False)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    
    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists."""
        result = self.fetchone(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        return result is not None
    
    def get_table_info(self, table_name: str) -> List[Dict[str, Any]]:
        """Get column info for a table."""
        return self.fetchall(f"PRAGMA table_info({table_name})")
    
    def close(self):
        """Close any persistent connection."""
        if self._connection:
            self._connection.close()
            self._connection = None


# Global instance for convenience
_db_manager: Optional[DatabaseManager] = None


def get_db_manager(db_path: Optional[str] = None) -> DatabaseManager:
    """Get or create the global database manager."""
    global _db_manager
    
    if _db_manager is None:
        if db_path is None:
            # Use default path
            from src.core.config import get_paths
            paths = get_paths()
            db_path = paths.data_dir / "agent_x1.db"
        
        _db_manager = DatabaseManager(str(db_path))
    
    return _db_manager


def reset_db_manager():
    """Reset the global database manager (for testing)."""
    global _db_manager
    if _db_manager:
        _db_manager.close()
    _db_manager = None
