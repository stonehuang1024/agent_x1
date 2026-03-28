"""SQLite storage for memory system."""

import sqlite3
import json
import logging
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from .models import EpisodicMemory, SemanticMemory, EpisodicType, SemanticCategory

logger = logging.getLogger(__name__)


class MemoryStore:
    """SQLite persistence for memories."""
    
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self._ensure_tables()
    
    def _ensure_tables(self):
        """Initialize memory tables if not exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS episodic_memory (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    type TEXT,
                    content TEXT,
                    importance REAL DEFAULT 0.5,
                    access_count INTEGER DEFAULT 0,
                    created_at REAL,
                    last_accessed REAL,
                    turn_number INTEGER,
                    context_json TEXT DEFAULT '{}'
                );
                
                CREATE INDEX IF NOT EXISTS idx_episodic_session 
                    ON episodic_memory(session_id);
                CREATE INDEX IF NOT EXISTS idx_episodic_type 
                    ON episodic_memory(type);
                CREATE INDEX IF NOT EXISTS idx_episodic_importance 
                    ON episodic_memory(importance DESC);
                
                CREATE TABLE IF NOT EXISTS semantic_memory (
                    id TEXT PRIMARY KEY,
                    category TEXT,
                    key TEXT UNIQUE,
                    value TEXT,
                    confidence REAL DEFAULT 0.8,
                    verification_count INTEGER DEFAULT 0,
                    source_session TEXT,
                    source_turn INTEGER,
                    created_at REAL,
                    updated_at REAL
                );
                
                CREATE INDEX IF NOT EXISTS idx_semantic_category 
                    ON semantic_memory(category);
                CREATE INDEX IF NOT EXISTS idx_semantic_key 
                    ON semantic_memory(key);
                
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    content, tokenize='porter'
                );
            """)
            conn.commit()
    
    # Episodic operations
    def store_episodic(self, memory: EpisodicMemory) -> EpisodicMemory:
        """Store an episodic memory."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO episodic_memory 
                    (id, session_id, type, content, importance, access_count,
                     created_at, last_accessed, turn_number, context_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    memory.id, memory.session_id, memory.type.value,
                    memory.content, memory.importance, memory.access_count,
                    memory.created_at.timestamp(), memory.last_accessed.timestamp(),
                    memory.turn_number, memory.context_json
                )
            )
            conn.commit()
        return memory
    
    def get_episodic(self, memory_id: str) -> Optional[EpisodicMemory]:
        """Retrieve episodic memory by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM episodic_memory WHERE id = ?",
                (memory_id,)
            ).fetchone()
            return self._row_to_episodic(row) if row else None
    
    def search_episodic(
        self,
        query: str,
        session_id: Optional[str] = None,
        types: Optional[List[EpisodicType]] = None,
        limit: int = 10
    ) -> List[EpisodicMemory]:
        """Search episodic memories."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            sql = "SELECT * FROM episodic_memory WHERE content LIKE ?"
            params = [f"%{query}%"]
            
            if session_id:
                sql += " AND session_id = ?"
                params.append(session_id)
            
            if types:
                placeholders = ','.join(['?'] * len(types))
                sql += f" AND type IN ({placeholders})"
                params.extend([t.value for t in types])
            
            sql += " ORDER BY importance DESC, created_at DESC LIMIT ?"
            params.append(limit)
            
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_episodic(row) for row in rows]
    
    def update_access(self, memory_id: str) -> None:
        """Update access count and timestamp."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE episodic_memory 
                    SET access_count = access_count + 1,
                        last_accessed = ?
                    WHERE id = ?""",
                (datetime.now().timestamp(), memory_id)
            )
            conn.commit()
    
    def get_old_memories(self, days: int = 30) -> List[EpisodicMemory]:
        """Get memories older than N days."""
        cutoff = datetime.now().timestamp() - (days * 86400)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM episodic_memory WHERE created_at < ?",
                (cutoff,)
            ).fetchall()
            return [self._row_to_episodic(row) for row in rows]
    
    def delete_episodic(self, memory_id: str) -> None:
        """Delete an episodic memory."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM episodic_memory WHERE id = ?", (memory_id,))
            conn.commit()
    
    # Semantic operations
    def store_semantic(self, memory: SemanticMemory) -> SemanticMemory:
        """Store or update semantic memory."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO semantic_memory 
                    (id, category, key, value, confidence, verification_count,
                     source_session, source_turn, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        confidence = excluded.confidence,
                        verification_count = verification_count + 1,
                        updated_at = excluded.updated_at""",
                (
                    memory.id, memory.category.value, memory.key,
                    memory.value, memory.confidence, memory.verification_count,
                    memory.source_session, memory.source_turn,
                    memory.created_at.timestamp(), memory.updated_at.timestamp()
                )
            )
            conn.commit()
        return memory
    
    def get_semantic(self, key: str) -> Optional[SemanticMemory]:
        """Get semantic memory by key."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM semantic_memory WHERE key = ?",
                (key,)
            ).fetchone()
            return self._row_to_semantic(row) if row else None
    
    def search_semantic(
        self,
        category: Optional[SemanticCategory] = None,
        query: Optional[str] = None,
        limit: int = 10
    ) -> List[SemanticMemory]:
        """Search semantic memories."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            sql = "SELECT * FROM semantic_memory WHERE 1=1"
            params = []
            
            if category:
                sql += " AND category = ?"
                params.append(category.value)
            
            if query:
                sql += " AND (key LIKE ? OR value LIKE ?)"
                params.extend([f"%{query}%", f"%{query}%"])
            
            sql += " ORDER BY confidence DESC, updated_at DESC LIMIT ?"
            params.append(limit)
            
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_semantic(row) for row in rows]
    
    # Helpers
    def _row_to_episodic(self, row: sqlite3.Row) -> EpisodicMemory:
        return EpisodicMemory(
            id=row['id'],
            session_id=row['session_id'],
            type=EpisodicType(row['type']),
            content=row['content'],
            importance=row['importance'],
            access_count=row['access_count'],
            created_at=datetime.fromtimestamp(row['created_at']),
            last_accessed=datetime.fromtimestamp(row['last_accessed']),
            turn_number=row['turn_number'],
            context_json=row['context_json']
        )
    
    def _row_to_semantic(self, row: sqlite3.Row) -> SemanticMemory:
        return SemanticMemory(
            id=row['id'],
            category=SemanticCategory(row['category']),
            key=row['key'],
            value=row['value'],
            confidence=row['confidence'],
            verification_count=row['verification_count'],
            source_session=row['source_session'],
            source_turn=row['source_turn'],
            created_at=datetime.fromtimestamp(row['created_at']),
            updated_at=datetime.fromtimestamp(row['updated_at'])
        )
