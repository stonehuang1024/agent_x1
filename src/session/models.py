"""Session module data models."""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List, Set
import time
import uuid


class SessionStatus(Enum):
    """Session lifecycle states."""
    CREATED = "created"         # Initial state, not yet started
    ACTIVE = "active"           # Currently in use
    PAUSED = "paused"           # Temporarily suspended
    COMPACTING = "compacting"   # Compressing context
    COMPLETED = "completed"     # Normal termination
    FAILED = "failed"           # Error termination
    ARCHIVED = "archived"       # Soft deleted


class SessionType(Enum):
    """Session type for multi-agent support."""
    PRIMARY = "primary"         # Main session
    DELEGATED = "delegated"     # Sub-agent delegated session


class TurnStatus(Enum):
    """Turn execution status."""
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class InvalidStateTransition(Exception):
    """Raised when an invalid session state transition is attempted."""

    def __init__(self, from_status: SessionStatus, to_status: SessionStatus, session_id: str = ""):
        self.from_status = from_status
        self.to_status = to_status
        self.session_id = session_id
        super().__init__(str(self))

    def __str__(self) -> str:
        return (
            f"Invalid transition: {self.from_status.value} → {self.to_status.value} "
            f"for session {self.session_id}"
        )


@dataclass
class TokenBudget:
    """Token budget tracking for a session."""
    total: int = 128000           # Total budget (e.g., 128k context)
    reserved: int = 8192          # Reserved for response
    used: int = 0                 # Already consumed
    warning_threshold: float = 0.8    # Emit warning at this utilization
    compaction_threshold: float = 0.9  # Trigger compaction at this utilization

    @property
    def available(self) -> int:
        """Actual available budget for input."""
        return max(0, self.total - self.reserved - self.used)

    @property
    def utilization_rate(self) -> float:
        """Current utilization 0-1."""
        effective = self.total - self.reserved
        if effective <= 0:
            return 1.0
        return min(self.used / effective, 1.0)

    def needs_warning(self) -> bool:
        """Check if token usage has reached the warning threshold."""
        return self.utilization_rate >= self.warning_threshold

    def needs_compaction(self) -> bool:
        """Check if token usage has reached the compaction threshold."""
        return self.utilization_rate >= self.compaction_threshold

    def is_exhausted(self) -> bool:
        """Check if token budget is fully exhausted."""
        return self.available <= 0

    def reset_used(self, new_used: int) -> None:
        """Reset used tokens after compaction."""
        self.used = new_used

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total,
            "reserved": self.reserved,
            "used": self.used,
            "available": self.available,
            "warning_threshold": self.warning_threshold,
            "compaction_threshold": self.compaction_threshold,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TokenBudget':
        """Deserialize from dictionary."""
        valid_keys = {'total', 'reserved', 'used', 'warning_threshold', 'compaction_threshold'}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


# State transition matrix: maps each status to the set of valid target statuses
VALID_TRANSITIONS: Dict[SessionStatus, Set[SessionStatus]] = {
    SessionStatus.CREATED:    {SessionStatus.ACTIVE, SessionStatus.FAILED, SessionStatus.ARCHIVED},
    SessionStatus.ACTIVE:     {SessionStatus.PAUSED, SessionStatus.COMPACTING, SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.ARCHIVED},
    SessionStatus.PAUSED:     {SessionStatus.ACTIVE, SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.ARCHIVED},
    SessionStatus.COMPACTING: {SessionStatus.ACTIVE, SessionStatus.FAILED},
    SessionStatus.COMPLETED:  {SessionStatus.ACTIVE, SessionStatus.ARCHIVED},   # ACTIVE for --resume
    SessionStatus.FAILED:     {SessionStatus.ACTIVE, SessionStatus.ARCHIVED},   # ACTIVE for retry
    SessionStatus.ARCHIVED:   set(),                                              # Terminal state
}


@dataclass
class Session:
    """Session data model."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_id: Optional[str] = None
    name: Optional[str] = None
    status: SessionStatus = SessionStatus.CREATED

    # Multi-agent extension fields
    agent_id: Optional[str] = None
    session_type: SessionType = SessionType.PRIMARY
    transcript_path: str = ""

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

    def validate_transition(self, new_status: SessionStatus) -> bool:
        """Validate and check if a state transition is allowed.

        Args:
            new_status: The target status to transition to.

        Returns:
            True if the transition is valid and should proceed.
            False if same-state transition (no-op).

        Raises:
            InvalidStateTransition: If the transition is not allowed.
        """
        if self.status == new_status:
            return False

        allowed = VALID_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidStateTransition(self.status, new_status, self.id)

        return True

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        data = asdict(self)
        data['status'] = self.status.value
        data['session_type'] = self.session_type.value
        data['budget'] = self.budget.to_dict()
        data['created_at'] = self.created_at.timestamp()
        data['updated_at'] = self.updated_at.timestamp()
        data['ended_at'] = self.ended_at.timestamp() if self.ended_at else None
        del data['is_dirty']  # Don't persist runtime state
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Session':
        """Deserialize from dictionary."""
        data = data.copy()

        # Restore enums
        data['status'] = SessionStatus(data['status'])

        # Restore session_type with backward compatibility
        session_type_val = data.pop('session_type', 'primary')
        data['session_type'] = SessionType(session_type_val)

        # Backward compatibility for new fields
        data.setdefault('agent_id', None)
        data.setdefault('transcript_path', '')

        # Restore timestamps
        data['created_at'] = datetime.fromtimestamp(data['created_at'])
        data['updated_at'] = datetime.fromtimestamp(data['updated_at'])
        if data.get('ended_at'):
            data['ended_at'] = datetime.fromtimestamp(data['ended_at'])

        # Restore budget
        budget_data = data.pop('budget', {})
        if isinstance(budget_data, dict):
            data['budget'] = TokenBudget.from_dict(budget_data)
        else:
            data['budget'] = TokenBudget()

        # Remove runtime fields
        data.pop('is_dirty', None)

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
    metadata: Dict[str, Any] = field(default_factory=dict)

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
            'metadata': self.metadata,
            'latency_ms': self.latency_ms,
            'created_at': self.created_at.timestamp()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Turn':
        data = data.copy()
        data['created_at'] = datetime.fromtimestamp(data['created_at'])
        data.setdefault('metadata', {})
        valid_fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid_fields)


class TurnContext:
    """Per-turn execution snapshot with freeze semantics.

    Config fields are frozen after freeze() is called and cannot be modified.
    Runtime fields can always be updated.
    """

    # Fields that become immutable after freeze
    _CONFIG_FIELDS = frozenset({
        'turn_number', 'session_id', 'working_dir', 'model', 'provider',
        'temperature', 'max_tokens', 'tool_configs', 'approval_policy',
        'behavior_settings',
    })

    # Fields that remain mutable after freeze
    _RUNTIME_FIELDS = frozenset({
        'token_usage', 'tool_call_records', 'latency_stats',
        'error', 'started_at', 'completed_at', 'status', '_frozen',
    })

    def __init__(
        self,
        session_id: str = "",
        turn_number: int = 0,
        working_dir: str = "",
        model: str = "",
        provider: str = "",
        temperature: float = 0.0,
        max_tokens: int = 0,
        tool_configs: Optional[List[str]] = None,
        approval_policy: str = "",
        behavior_settings: Optional[Dict[str, Any]] = None,
        started_at: Optional[float] = None,
        status: TurnStatus = TurnStatus.RUNNING,
    ):
        # Use object.__setattr__ to bypass our custom __setattr__ during init
        object.__setattr__(self, '_frozen', False)

        # Config fields
        self.session_id = session_id
        self.turn_number = turn_number
        self.working_dir = working_dir
        self.model = model
        self.provider = provider
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.tool_configs = tool_configs if tool_configs is not None else []
        self.approval_policy = approval_policy
        self.behavior_settings = behavior_settings if behavior_settings is not None else {}

        # Runtime fields
        self.token_usage: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self.tool_call_records: List[Dict[str, Any]] = []
        self.latency_stats: Dict[str, float] = {"total_ms": 0.0, "llm_ms": 0.0, "tool_ms": 0.0}
        self.error: Optional[str] = None
        self.started_at: float = started_at if started_at is not None else time.time()
        self.completed_at: Optional[float] = None
        self.status: TurnStatus = status

    def __setattr__(self, name: str, value: Any) -> None:
        if self._frozen and name in self._CONFIG_FIELDS:
            raise AttributeError(f"Cannot modify frozen config field: {name}")
        object.__setattr__(self, name, value)

    def freeze(self) -> None:
        """Freeze config fields, making them immutable."""
        object.__setattr__(self, '_frozen', True)

    def complete(
        self,
        token_usage: Optional[Dict[str, int]] = None,
        tool_call_records: Optional[List[Dict[str, Any]]] = None,
        latency_stats: Optional[Dict[str, float]] = None,
    ) -> None:
        """Mark turn as completed with final stats."""
        self.status = TurnStatus.COMPLETED
        self.completed_at = time.time()
        if token_usage is not None:
            self.token_usage = token_usage
        if tool_call_records is not None:
            self.tool_call_records = tool_call_records
        if latency_stats is not None:
            self.latency_stats = latency_stats

    def fail(self, error: str) -> None:
        """Mark turn as failed with error info."""
        self.status = TurnStatus.FAILED
        self.error = error
        self.completed_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            'session_id': self.session_id,
            'turn_number': self.turn_number,
            'working_dir': self.working_dir,
            'model': self.model,
            'provider': self.provider,
            'temperature': self.temperature,
            'max_tokens': self.max_tokens,
            'tool_configs': list(self.tool_configs),
            'approval_policy': self.approval_policy,
            'behavior_settings': dict(self.behavior_settings),
            'token_usage': dict(self.token_usage),
            'tool_call_records': list(self.tool_call_records),
            'latency_stats': dict(self.latency_stats),
            'error': self.error,
            'started_at': self.started_at,
            'completed_at': self.completed_at,
            'status': self.status.value,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'TurnContext':
        """Deserialize from dictionary."""
        data = data.copy()
        status_val = data.pop('status', 'running')
        status = TurnStatus(status_val)

        # Extract runtime fields before constructing
        token_usage = data.pop('token_usage', None)
        tool_call_records = data.pop('tool_call_records', None)
        latency_stats = data.pop('latency_stats', None)
        error = data.pop('error', None)
        completed_at = data.pop('completed_at', None)

        ctx = cls(
            session_id=data.get('session_id', ''),
            turn_number=data.get('turn_number', 0),
            working_dir=data.get('working_dir', ''),
            model=data.get('model', ''),
            provider=data.get('provider', ''),
            temperature=data.get('temperature', 0.0),
            max_tokens=data.get('max_tokens', 0),
            tool_configs=data.get('tool_configs'),
            approval_policy=data.get('approval_policy', ''),
            behavior_settings=data.get('behavior_settings'),
            started_at=data.get('started_at'),
            status=status,
        )

        # Restore runtime fields
        if token_usage is not None:
            ctx.token_usage = token_usage
        if tool_call_records is not None:
            ctx.tool_call_records = tool_call_records
        if latency_stats is not None:
            ctx.latency_stats = latency_stats
        ctx.error = error
        ctx.completed_at = completed_at

        return ctx


@dataclass
class SessionIndexEntry:
    """Lightweight session entry for the index file."""
    session_id: str = ""
    name: Optional[str] = None
    status: str = "created"
    created_at: float = 0.0
    updated_at: float = 0.0
    turn_count: int = 0
    preview: str = ""
    working_dir: str = ""
    session_dir: str = ""
    agent_id: Optional[str] = None
    session_type: str = "primary"

    def to_dict(self) -> Dict[str, Any]:
        return {
            'session_id': self.session_id,
            'name': self.name,
            'status': self.status,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'turn_count': self.turn_count,
            'preview': self.preview,
            'working_dir': self.working_dir,
            'session_dir': self.session_dir,
            'agent_id': self.agent_id,
            'session_type': self.session_type,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SessionIndexEntry':
        valid_fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid_fields)

    @classmethod
    def from_session(cls, session: Session, preview: str = "") -> 'SessionIndexEntry':
        """Create an index entry from a Session object."""
        return cls(
            session_id=session.id,
            name=session.name,
            status=session.status.value,
            created_at=session.created_at.timestamp(),
            updated_at=session.updated_at.timestamp(),
            turn_count=session.turn_count,
            preview=preview,
            working_dir=session.working_dir,
            session_dir=session.session_dir,
            agent_id=session.agent_id,
            session_type=session.session_type.value,
        )


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
