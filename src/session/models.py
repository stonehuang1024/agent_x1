"""Session module data models."""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List
import uuid


class SessionStatus(Enum):
    """Session lifecycle states."""
    CREATED = "created"       # Initial state, not yet started
    ACTIVE = "active"         # Currently in use
    PAUSED = "paused"         # Temporarily suspended
    COMPACTING = "compacting"  # Compressing context
    COMPLETED = "completed"   # Normal termination
    FAILED = "failed"         # Error termination
    ARCHIVED = "archived"     # Soft deleted
    FORKED = "forked"         # Was forked into new session


@dataclass
class TokenBudget:
    """Token budget tracking for a session."""
    total: int = 128000           # Total budget (e.g., 128k context)
    reserved: int = 8192          # Reserved for response
    used: int = 0                 # Already consumed
    
    @property
    def available(self) -> int:
        """Actual available budget for input."""
        return max(0, self.total - self.reserved - self.used)
    
    @property
    def utilization_rate(self) -> float:
        """Current utilization 0-1."""
        if self.total <= self.reserved:
            return 1.0
        return self.used / (self.total - self.reserved)
    
    def to_dict(self) -> Dict[str, int]:
        return {
            "total": self.total,
            "reserved": self.reserved,
            "used": self.used,
            "available": self.available
        }


@dataclass
class Session:
    """Session data model."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_id: Optional[str] = None
    name: Optional[str] = None
    status: SessionStatus = SessionStatus.CREATED
    
    # Timestamps
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    ended_at: Optional[datetime] = None
    
    # Configuration snapshot at creation
    config_snapshot: Dict[str, Any] = field(default_factory=dict)
    
    # Token budget
    budget: TokenBudget = field(default_factory=TokenBudget)
    
    # Statistics
    turn_count: int = 0
    total_duration_ms: float = 0.0
    llm_call_count: int = 0
    tool_call_count: int = 0
    error_count: int = 0
    
    # Paths
    working_dir: str = ""         # CWD at creation
    session_dir: str = ""         # Dedicated session directory
    
    # Runtime state (not persisted)
    is_dirty: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        data = asdict(self)
        data['status'] = self.status.value
        data['budget'] = self.budget.to_dict()
        data['created_at'] = self.created_at.timestamp()
        data['updated_at'] = self.updated_at.timestamp()
        data['ended_at'] = self.ended_at.timestamp() if self.ended_at else None
        del data['is_dirty']  # Don't persist runtime state
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Session':
        """Deserialize from dictionary."""
        # Restore enums
        data['status'] = SessionStatus(data['status'])
        
        # Restore timestamps
        data['created_at'] = datetime.fromtimestamp(data['created_at'])
        data['updated_at'] = datetime.fromtimestamp(data['updated_at'])
        if data.get('ended_at'):
            data['ended_at'] = datetime.fromtimestamp(data['ended_at'])
        
        # Restore budget
        budget_data = data.pop('budget', {})
        data['budget'] = TokenBudget(**budget_data)
        
        # Filter to valid fields
        valid_fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid_fields)
    
    def touch(self):
        """Update modification time."""
        self.updated_at = datetime.now()
        self.is_dirty = True
    
    def add_turn(self, token_count: int):
        """Record a new turn."""
        self.turn_count += 1
        self.budget.used += token_count
        self.touch()


@dataclass
class Turn:
    """Single conversation turn record."""
    id: int = 0
    session_id: str = ""
    turn_number: int = 0
    
    # Message content
    role: str = ""                          # system/user/assistant/tool
    content: str = ""
    
    # Tool calls (for assistant messages)
    tool_calls: Optional[List[Dict]] = None
    tool_call_id: Optional[str] = None      # For tool role messages
    
    # Metadata
    token_count: int = 0
    importance: float = 0.5                   # 0-1 for compression decisions
    
    # Performance
    latency_ms: float = 0.0
    
    created_at: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'session_id': self.session_id,
            'turn_number': self.turn_number,
            'role': self.role,
            'content': self.content,
            'tool_calls': self.tool_calls,
            'tool_call_id': self.tool_call_id,
            'token_count': self.token_count,
            'importance': self.importance,
            'latency_ms': self.latency_ms,
            'created_at': self.created_at.timestamp()
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Turn':
        data = data.copy()
        data['created_at'] = datetime.fromtimestamp(data['created_at'])
        valid_fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid_fields)


@dataclass
class Checkpoint:
    """Session checkpoint for fork/restore."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    name: Optional[str] = None
    
    turn_number: int = 0
    messages_snapshot: List[Dict] = field(default_factory=list)
    budget_snapshot: TokenBudget = field(default_factory=TokenBudget)
    
    created_at: datetime = field(default_factory=datetime.now)
    
    @property
    def description(self) -> str:
        """Generate human-readable description."""
        return f"Checkpoint at turn {self.turn_number}: {self.name or 'unnamed'}"


@dataclass
class SessionSummary:
    """Lightweight session summary for listings."""
    id: str = ""
    name: Optional[str] = None
    status: SessionStatus = SessionStatus.CREATED
    turn_count: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    preview: str = ""
