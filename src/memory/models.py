"""Memory system data models."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List
import uuid
import math


class EpisodicType(Enum):
    """Types of episodic memories (session events)."""
    DECISION = "decision"      # Key decision made
    ACTION = "action"          # Action taken
    OUTCOME = "outcome"        # Result/outcome
    ERROR = "error"            # Error encountered
    NOTE = "note"              # General note
    INSIGHT = "insight"        # Key insight learned


class SemanticCategory(Enum):
    """Categories of semantic memory (long-term facts)."""
    PREFERENCE = "preference"    # User preferences
    CONVENTION = "convention"   # Coding conventions
    FACT = "fact"               # Known facts
    PATTERN = "pattern"         # Recognized patterns
    PROCEDURE = "procedure"     # Learned procedures


@dataclass
class EpisodicMemory:
    """Session-scoped event memory."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    session_id: str = ""
    type: EpisodicType = EpisodicType.NOTE
    content: str = ""
    
    # Scoring
    importance: float = 0.5  # 0-1
    access_count: int = 0
    
    # Timing
    created_at: datetime = field(default_factory=datetime.now)
    last_accessed: datetime = field(default_factory=datetime.now)
    
    # Context
    turn_number: Optional[int] = None
    context_json: str = "{}"  # Additional context
    
    def touch(self):
        """Update last accessed time."""
        self.last_accessed = datetime.now()
        self.access_count += 1
    
    def retention_score(self, now: Optional[datetime] = None) -> float:
        """
        Calculate retention score based on forgetting curve.
        Higher = more likely to be retained.
        """
        if now is None:
            now = datetime.now()
        
        age_days = (now - self.created_at).total_seconds() / 86400
        
        # Ebbinghaus forgetting curve variant
        base_retention = self.importance
        decay_factor = 0.3 / max(self.importance, 0.1)
        
        return base_retention * math.exp(-decay_factor * age_days)


@dataclass
class SemanticMemory:
    """Long-term fact/convention memory."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    category: SemanticCategory = SemanticCategory.FACT
    key: str = ""           # Lookup key
    value: str = ""         # Memory content
    
    # Confidence
    confidence: float = 0.8   # 0-1 certainty level
    verification_count: int = 0
    
    # Source tracking
    source_session: Optional[str] = None
    source_turn: Optional[int] = None
    
    # Timing
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    def update(self, new_value: str, confidence: float):
        """Update memory with new information."""
        self.value = new_value
        self.confidence = confidence
        self.updated_at = datetime.now()
        self.verification_count += 1


@dataclass
class ProjectMemoryFile:
    """Discovered PROJECT.md or CLAUDE.md file."""
    path: str = ""
    content: str = ""
    last_modified: datetime = field(default_factory=datetime.now)
    scope: str = "project"  # 'global', 'project', 'parent', or 'subdir'
    metadata: Dict[str, Any] = field(default_factory=dict)  # YAML Front Matter data
    
    @property
    def summary(self) -> str:
        """Generate a short summary."""
        lines = self.content.split('\n')[:5]
        return '\n'.join(lines)
