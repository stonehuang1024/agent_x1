"""Memory system controller."""

import logging
from typing import List, Optional
from datetime import datetime

from .models import EpisodicMemory, SemanticMemory, EpisodicType, SemanticCategory
from .memory_store import MemoryStore

logger = logging.getLogger(__name__)


class MemoryController:
    """High-level memory management interface."""
    
    def __init__(self, store: MemoryStore):
        self.store = store
    
    # Storage methods
    def record_decision(
        self, session_id: str, decision: str,
        importance: float = 0.8, turn_number: Optional[int] = None
    ) -> EpisodicMemory:
        memory = EpisodicMemory(
            session_id=session_id, type=EpisodicType.DECISION,
            content=decision, importance=importance, turn_number=turn_number
        )
        return self.store.store_episodic(memory)
    
    def record_action(
        self, session_id: str, action: str,
        importance: float = 0.5, turn_number: Optional[int] = None
    ) -> EpisodicMemory:
        memory = EpisodicMemory(
            session_id=session_id, type=EpisodicType.ACTION,
            content=action, importance=importance, turn_number=turn_number
        )
        return self.store.store_episodic(memory)
    
    def record_outcome(
        self, session_id: str, outcome: str, success: bool = True,
        turn_number: Optional[int] = None
    ) -> EpisodicMemory:
        memory = EpisodicMemory(
            session_id=session_id, type=EpisodicType.OUTCOME,
            content=outcome, importance=0.9 if success else 0.7,
            turn_number=turn_number
        )
        return self.store.store_episodic(memory)
    
    def record_error(
        self, session_id: str, error: str,
        turn_number: Optional[int] = None
    ) -> EpisodicMemory:
        memory = EpisodicMemory(
            session_id=session_id, type=EpisodicType.ERROR,
            content=error, importance=0.8, turn_number=turn_number
        )
        return self.store.store_episodic(memory)
    
    def record_insight(
        self, session_id: str, insight: str,
        turn_number: Optional[int] = None
    ) -> EpisodicMemory:
        memory = EpisodicMemory(
            session_id=session_id, type=EpisodicType.INSIGHT,
            content=insight, importance=0.9, turn_number=turn_number
        )
        return self.store.store_episodic(memory)
    
    def store_preference(
        self, key: str, value: str,
        confidence: float = 0.8, source_session: Optional[str] = None
    ) -> SemanticMemory:
        memory = SemanticMemory(
            category=SemanticCategory.PREFERENCE,
            key=key, value=value,
            confidence=confidence, source_session=source_session
        )
        return self.store.store_semantic(memory)
    
    def store_convention(
        self, key: str, value: str,
        confidence: float = 0.9, source_session: Optional[str] = None
    ) -> SemanticMemory:
        memory = SemanticMemory(
            category=SemanticCategory.CONVENTION,
            key=key, value=value,
            confidence=confidence, source_session=source_session
        )
        return self.store.store_semantic(memory)
    
    # Retrieval methods
    def retrieve_relevant(
        self, query: str, session_id: Optional[str] = None,
        top_k: int = 5, include_semantic: bool = True
    ) -> List[EpisodicMemory]:
        results = []
        
        # Search episodic
        episodic = self.store.search_episodic(
            query=query, session_id=session_id, limit=top_k
        )
        for mem in episodic:
            mem.touch()
            self.store.update_access(mem.id)
            results.append(mem)
        
        # Include semantic if requested
        if include_semantic:
            semantic = self.store.search_semantic(query=query, limit=top_k)
            for sem in semantic:
                # Wrap as episodic for unified handling
                wrapper = EpisodicMemory(
                    id=f"sem:{sem.key}", session_id="semantic",
                    type=EpisodicType.NOTE,
                    content=f"{sem.key}: {sem.value}",
                    importance=sem.confidence
                )
                results.append(wrapper)
        
        results.sort(key=lambda m: m.importance, reverse=True)
        return results[:top_k]
    
    def get_preferences(self) -> List[SemanticMemory]:
        return self.store.search_semantic(category=SemanticCategory.PREFERENCE)
    
    def get_conventions(self) -> List[SemanticMemory]:
        return self.store.search_semantic(category=SemanticCategory.CONVENTION)
    
    # Maintenance
    def cleanup_expired(self, threshold: float = 0.05) -> int:
        now = datetime.now()
        old_memories = self.store.get_old_memories(days=7)
        
        deleted = 0
        for mem in old_memories:
            if mem.retention_score(now) < threshold:
                self.store.delete_episodic(mem.id)
                deleted += 1
        
        if deleted:
            logger.info(f"Cleaned up {deleted} expired memories")
        return deleted
    
    def summarize_session(self, session_id: str) -> str:
        memories = self.store.search_episodic(
            session_id=session_id, query="", limit=100
        )
        important = [m for m in memories if m.importance >= 0.7]
        important.sort(key=lambda m: m.created_at)
        
        if not important:
            return "No significant memories from this session."
        
        lines = ["## Session Highlights", ""]
        for mem in important:
            emoji = {
                EpisodicType.DECISION: "🎯",
                EpisodicType.ACTION: "⚡",
                EpisodicType.OUTCOME: "✅",
                EpisodicType.ERROR: "❌",
                EpisodicType.INSIGHT: "💡",
                EpisodicType.NOTE: "📝"
            }.get(mem.type, "•")
            lines.append(f"{emoji} {mem.content}")
        
        return "\n".join(lines)
