# Agent X1 Detailed Module Design

## Module 1: Session Management

### 1.1 Data Models

```python
# Session State Machine
class SessionStatus(Enum):
    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPACTING = "compacting"
    COMPLETED = "completed"
    FAILED = "failed"
    ARCHIVED = "archived"
    FORKED = "forked"

@dataclass
class Session:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_id: Optional[str] = None
    name: Optional[str] = None
    status: SessionStatus = SessionStatus.CREATED
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    config_snapshot: Dict[str, Any] = field(default_factory=dict)
    token_budget_total: int = 128000
    token_budget_used: int = 0
    turn_count: int = 0
    working_dir: str = ""

@dataclass
class Turn:
    id: int = 0
    session_id: str = ""
    turn_number: int = 0
    role: str = ""  # system/user/assistant/tool
    content: str = ""
    tool_calls: Optional[List[Dict]] = None
    tool_call_id: Optional[str] = None
    token_count: int = 0
    importance: float = 0.5
    created_at: datetime = field(default_factory=datetime.now)
```

### 1.2 SQLite Schema

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    parent_id TEXT REFERENCES sessions(id),
    name TEXT,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    config_snapshot TEXT DEFAULT '{}',
    token_budget_total INTEGER DEFAULT 128000,
    token_budget_used INTEGER DEFAULT 0,
    turn_count INTEGER DEFAULT 0,
    working_dir TEXT DEFAULT ''
);

CREATE TABLE turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_number INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT DEFAULT '',
    tool_calls TEXT,
    tool_call_id TEXT,
    token_count INTEGER DEFAULT 0,
    importance REAL DEFAULT 0.5,
    created_at REAL NOT NULL,
    UNIQUE(session_id, turn_number)
);

CREATE TABLE checkpoints (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    name TEXT,
    turn_number INTEGER NOT NULL,
    messages_snapshot TEXT NOT NULL,
    created_at REAL NOT NULL
);
```

### 1.3 Core Classes

**SessionStore**: SQLite persistence layer with CRUD operations
**SessionManager**: High-level API for session lifecycle
**SessionExporter**: Export to Markdown/JSON formats

---

## Module 2: Runtime

### 2.1 Agent State Machine

```
IDLE -> ASSEMBLING_CONTEXT -> WAITING_FOR_LLM -> EXECUTING_TOOLS -> COMPLETED
                          |                     |
                          v                     v
                      COMPACTING <------------- [if tool_calls]
```

### 2.2 Data Models

```python
class AgentState(Enum):
    IDLE = "idle"
    ASSEMBLING_CONTEXT = "assembling_context"
    WAITING_FOR_LLM = "waiting_for_llm"
    EXECUTING_TOOLS = "executing_tools"
    COMPACTING = "compacting"
    COMPLETED = "completed"
    ERROR = "error"

@dataclass
class AgentTurn:
    turn_number: int = 0
    user_input: str = ""
    assembled_messages: List[Message] = field(default_factory=list)
    llm_response: Optional[str] = None
    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    final_text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: float = 0.0
```

### 2.3 Core Classes

**AgentLoop**: Main orchestrator, extracts loop from engines
**ToolScheduler**: State-driven tool execution (PENDING->VALIDATING->EXECUTING->SUCCESS/ERROR)
**LoopDetector**: Detects repetitive tool call patterns

---

## Module 3: Context Management

### 3.1 Layered Context Model (L1-L7)

| Layer | Content | Mutable | Evictable |
|-------|---------|---------|-----------|
| L1 System | Assembled system prompt | No | No |
| L2 Project | PROJECT.md/AGENT.md | No | No |
| L3 Skill | Active skill context | Yes | Yes |
| L4 Memory | Retrieved memories | Yes | Yes |
| L5 History | Conversation turns | Yes | Compress |
| L6 Tool Output | Latest tool results | Yes | Truncate |
| L7 User | Current user input | No | No |

### 3.2 Core Classes

**ContextWindow**: Token budget management with warning/critical thresholds
**ContextAssembler**: Layer-by-layer assembly with budget-aware eviction
**ContextCompressor**: History summarization and tool output truncation
**TokenCounter**: Estimate token counts (tiktoken or approximate)

---

## Module 4: Memory System

### 4.1 Two-Tier Memory

**Episodic**: Session events (decisions, actions, outcomes)
**Semantic**: Long-term knowledge (preferences, conventions, facts)

### 4.2 SQLite Schema

```sql
CREATE TABLE episodic_memory (
    id INTEGER PRIMARY KEY,
    session_id TEXT,
    type TEXT,  -- decision/action/outcome/error/note
    content TEXT,
    importance REAL,
    access_count INTEGER DEFAULT 0,
    created_at REAL,
    last_accessed REAL
);

CREATE TABLE semantic_memory (
    id INTEGER PRIMARY KEY,
    category TEXT,  -- preference/convention/fact/pattern
    key TEXT,
    value TEXT,
    confidence REAL,
    source_session TEXT,
    created_at REAL,
    updated_at REAL
);

CREATE VIRTUAL TABLE memory_fts USING fts5(content, tokenize='porter');
```

### 4.3 Core Classes

**MemoryController**: Retrieve/store/cleanup operations
**EpisodicMemory**: Session event recording with importance scoring
**SemanticMemory**: Long-term fact storage with confidence tracking
**ProjectMemory**: PROJECT.md discovery and loading

### 4.4 Forgetting Curve

```python
def retention_score(memory, now):
    age_days = (now - memory.created_at) / 86400
    return memory.importance * exp(-0.3 * age_days / max(memory.importance, 0.1))
```

---

## Module 5: Prompt Engineering

### 5.1 Component Architecture

```
PromptProvider
  |- render_preamble()
  |- render_mandates()
  |- render_tools()
  |- render_skills_catalog() [if skills]
  |- render_active_skill() [if active]
  |- render_project_memory() [if PROJECT.md]
  |- render_guidelines()
```

### 5.2 PromptContext

```python
@dataclass
class PromptContext:
    mode: str  # interactive/single/headless
    tools: List[str]
    skills: List[SkillSummary]
    active_skill: Optional[SkillSpec]
    project_memory: str
    model_name: str
    max_tokens: int
```

### 5.3 Template Files

- `templates/base_system.md`: Base personality
- `templates/mandates.md`: Core rules
- `templates/compression.md`: Compression instructions

---

## Module 6: Loop

### 6.1 Execution Flow

```
1. User Input
2. ContextAssembler.build() -> messages
3. Engine.call_llm(messages) -> response
4. Parse response:
   - If tool_calls: ToolScheduler.execute() -> goto 3
   - If text: return result
5. LoopDetector.check() -> warn if repetitive
6. Session.record_turn()
```

### 6.2 Engine Refactor

Remove while loop from engines. New interface:

```python
class BaseEngine(ABC):
    def call_llm(self, messages: List[Message], tools: List[Tool]) -> LLMResponse
    # Remove: chat(), internal message storage, loop logic
```

### 6.3 ToolScheduler States

```
PENDING -> VALIDATING -> APPROVED -> EXECUTING -> SUCCESS
                               |                |
                               v                v
                           REJECTED          ERROR
```

---

## Implementation Phases

**Phase 1**: DB layer, events, models (2-3 days)
**Phase 2**: Session module (2 days)
**Phase 3**: Prompt + Context (2-3 days)
**Phase 4**: Memory module (2 days)
**Phase 5**: Runtime + Loop + Engine refactor (3 days)
**Phase 6**: Integration tests (1-2 days)



---

## Module 1 Complete: Session Detailed Design

The Session module design includes:

### Data Models
- `SessionStatus` enum with 8 states
- `Session` dataclass with token budget tracking
- `Turn` dataclass for message history
- `Checkpoint` for snapshot/restore
- `TokenBudget` for budget management

### SQLite Schema
- `sessions` table with full metadata
- `turns` table with tool call support
- `checkpoints` table for snapshots
- Indexes for efficient queries

### Core Classes
- `SessionStore`: Pure SQLite persistence layer
- `SessionManager`: High-level lifecycle API

### Key Features
- Fork/resume/checkpoint support
- Token budget tracking with utilization rates
- Soft delete (archive) with cleanup
- State change callbacks

---

## Module 2: Runtime (Detailed)

### 2.1 Goals
- Extract loop from engines into unified AgentLoop
- State machine driven execution
- Separate tool scheduling from LLM calling
- Support sync and async modes

### 2.2 Data Models

```python
class AgentState(Enum):
    IDLE = "idle"
    ASSEMBLING_CONTEXT = "assembling_context"
    WAITING_FOR_LLM = "waiting_for_llm"
    EXECUTING_TOOLS = "executing_tools"
    COMPACTING = "compacting"
    COMPLETED = "completed"
    ERROR = "error"

@dataclass
class AgentTurn:
    turn_number: int
    user_input: str
    assembled_messages: List[Message]
    llm_response: Optional[str]
    tool_calls: List[ToolCallRecord]
    final_text: str
    input_tokens: int
    output_tokens: int
    duration_ms: float

@dataclass
class ToolCallRecord:
    id: str
    tool_name: str
    arguments: Dict[str, Any]
    state: ToolExecutionState
    result: Optional[str]
    error_message: Optional[str]
    duration_ms: float
```

### 2.3 Core Classes

**AgentLoop**: Main orchestrator
- `run(user_input)` - async entry point
- `run_sync(user_input)` - sync wrapper
- State machine with callbacks
- Loop detection integration

**ToolScheduler**: Tool execution state machine
- States: PENDING → VALIDATING → APPROVED → EXECUTING → SUCCESS/ERROR
- Parallel execution support (max_parallel)
- Timeout handling
- Retry logic

**LoopDetector**: Repetitive pattern detection
- Window-based similarity checking
- Configurable threshold
- Warning injection to context

---

## Module 3: Context (Detailed)

### 3.1 Layered Context Model

| Layer | Source | Priority | Eviction Strategy |
|-------|--------|----------|-------------------|
| L1 System | PromptProvider | Highest | Never |
| L2 Project | PROJECT.md | High | Never |
| L3 Skill | Active skill | Medium | On overflow |
| L4 Memory | Retrieved | Medium | On overflow |
| L5 History | Past turns | Low | Compression |
| L6 Tool Output | Recent results | Lowest | Truncation |
| L7 User | Current input | Highest | Never |

### 3.2 Core Classes

**ContextWindow**: Token budget
- `max_tokens`, `reserve_tokens`
- `fits(messages)` - check if fits budget
- `should_compress()` - trigger check

**ContextAssembler**: Layer builder
- `build(session, user_input)` - assemble all layers
- `evict_layest_layers(budget_deficit)` - remove lowest priority
- Integration with MemoryController for L4

**ContextCompressor**: History management
- `compress(session)` - summarize old turns
- `truncate_outputs(turns)` - shorten tool outputs
- Two modes: simple truncation or LLM summary

**TokenCounter**: Estimation
- tiktoken integration (if available)
- Fallback to character-based approximation
- Cached counts for performance

---

## Module 4: Memory (Detailed)

### 4.1 Two-Tier Design

**Episodic Memory**: Session events
- Types: decision, action, outcome, error, note
- Importance scoring (0-1)
- Automatic forgetting via retention curve

**Semantic Memory**: Long-term facts
- Categories: preference, convention, fact, pattern
- Confidence scoring
- Manual management (no auto-delete)

### 4.2 SQLite Schema

```sql
CREATE TABLE episodic_memory (
    id INTEGER PRIMARY KEY,
    session_id TEXT,
    type TEXT CHECK(type IN ('decision','action','outcome','error','note')),
    content TEXT,
    importance REAL DEFAULT 0.5,
    access_count INTEGER DEFAULT 0,
    created_at REAL,
    last_accessed REAL
);

CREATE TABLE semantic_memory (
    id INTEGER PRIMARY KEY,
    category TEXT,
    key TEXT UNIQUE,
    value TEXT,
    confidence REAL DEFAULT 0.8,
    source_session TEXT,
    created_at REAL,
    updated_at REAL
);

CREATE VIRTUAL TABLE memory_fts USING fts5(content, tokenize='porter');
```

### 4.3 Core Classes

**MemoryController**: Main interface
- `retrieve_relevant(query, top_k=5)` - search both tiers
- `store_episodic(session_id, type, content, importance)`
- `store_semantic(category, key, value)`
- `cleanup_expired()` - apply forgetting curve

**EpisodicMemory**: Session events
- Event recording during turns
- Importance auto-scoring (tool errors = high)

**SemanticMemory**: Long-term storage
- Key-value with confidence
- User preferences, coding conventions

**ProjectMemory**: File-based
- Discover PROJECT.md/AGENT.md
- Load into L2 context

### 4.4 Forgetting Algorithm

```python
def retention_score(memory, now):
    age_days = (now - memory.created_at) / 86400
    importance = memory.importance
    # Ebbinghaus forgetting curve variant
    return importance * exp(-0.3 * age_days / max(importance, 0.1))
```

Delete when `retention_score < 0.05`

---

## Module 5: Prompt (Detailed)

### 5.1 Component Architecture

```
PromptProvider
  ├─ PreambleSection (identity, mode)
  ├─ MandatesSection (core rules)
  ├─ ToolsSection (available tools)
  ├─ SkillsCatalogSection (if skills discovered)
  ├─ ActiveSkillSection (if skill activated)
  ├─ ProjectMemorySection (if PROJECT.md exists)
  └─ GuidelinesSection (operational instructions)
```

### 5.2 PromptContext

```python
@dataclass
class PromptContext:
    mode: str  # interactive/single/headless
    model_name: str
    max_tokens: int
    tools: List[ToolSummary]
    skills: List[SkillSummary]
    active_skill: Optional[SkillSpec]
    project_memory: str
    user_preferences: Dict[str, str]
    runtime_state: str  # current agent state
```

### 5.3 Section Implementations

Each section is a function:

```python
def render_preamble(ctx: PromptContext) -> str:
    return f"You are Agent X1...\nMode: {ctx.mode}\nModel: {ctx.model_name}"

def render_mandates(ctx: PromptContext) -> str:
    return """## Core Mandates
1. Read before editing
2. Minimal changes
3. Explain reasoning
4. Follow PROJECT.md conventions"""
```

### 5.4 Templates Directory

```
src/prompt/templates/
  ├─ base_system.md      # Base personality
  ├─ mandates.md         # Core rules
  ├─ compression.md      # Compression instructions
  └─ tool_guidelines.md  # Tool usage patterns
```

---

## Module 6: Loop (Detailed)

### 6.1 Execution Flow

```
┌─────────────────────────────────────────┐
│  User Input                             │
└─────────────┬───────────────────────────┘
              ▼
┌─────────────────────────────────────────┐
│  ContextAssembler.build()               │
│  - Assemble L1-L7 layers                │
│  - Check token budget                   │
│  - Evict if overflow                    │
└─────────────┬───────────────────────────┘
              ▼
┌─────────────────────────────────────────┐
│  Engine.call_llm(messages, tools)       │
│  (No loop inside - single call)         │
└─────────────┬───────────────────────────┘
              ▼
        ┌─────┴─────┐
        ▼           ▼
   Tool Calls?   Text Response
        │           │
        ▼           ▼
┌──────────────┐  ┌──────────────┐
│ ToolScheduler│  │ Return result│
│ .execute()   │  │              │
└──────┬───────┘  └──────────────┘
       │
       ▼
┌─────────────────────────────────────────┐
│  LoopDetector.check()                   │
│  If repetitive → inject warning        │
└─────────────┬───────────────────────────┘
              │
              └──────► (Loop back to LLM call)
```

### 6.2 Engine Refactoring

Remove from `BaseEngine`:
- `chat()` method
- `messages` list storage
- While loop logic
- Tool execution loop

New minimal interface:

```python
class BaseEngine(ABC):
    @abstractmethod
    def call_llm(
        self,
        messages: List[Message],
        tools: Optional[List[Tool]] = None,
        system_prompt: Optional[str] = None
    ) -> LLMResponse:
        """Single LLM call - no loop, no state"""
```

### 6.3 ToolScheduler State Machine

```
┌─────────┐    validate     ┌───────────┐   approve   ┌───────────┐
│ PENDING │ ──────────────> │ VALIDATING│ ──────────> │  APPROVED │
└─────────┘                 └───────────┘             └─────┬─────┘
                                                          │
                              ┌──────────────┐            │ execute
                              │    ERROR     │<───────────┘
                              │   (retry?)   │<──────┐
                              └──────────────┘       │
                                    ▲                │
                                    └────────────────┘
                                     execute failed
```

---

## Implementation Roadmap

### Phase 1: Foundation (Days 1-2)
- `src/util/db.py` - SQLite utilities
- `src/core/events.py` - Event system
- `data/migrations/001_init.sql` - Schema

### Phase 2: Session (Days 3-4)
- `src/session/` module
- SessionStore + SessionManager
- Migration of old session logging

### Phase 3: Memory (Days 5-6)
- `src/memory/` module
- PROJECT.md discovery
- Forgetting curve implementation

### Phase 4: Context + Prompt (Days 7-9)
- `src/context/` module
- `src/prompt/` module
- Layer assembly + component rendering

### Phase 5: Runtime + Loop (Days 10-12)
- `src/runtime/` module
- AgentLoop implementation
- Engine refactoring (remove loops)
- `main.py` integration

### Phase 6: Testing (Days 13-15)
- Unit tests for each module
- Integration tests
- Migration of existing data

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| SQLite over file | Structured queries, ACID, no external dep |
| Engine loop extraction | Single implementation, testable, provider-agnostic |
| No vector DB | SQLite FTS5 sufficient for current scale |
| No embeddings | Avoid external API calls, use BM25/TF-IDF |
| 7-layer context | Matches Gemini CLI + Codex patterns |
| Component prompts | Testable, mode-aware, extensible |

## Reference Implementations

## Reference Implementations

| Feature | Claude Code | Codex | Gemini CLI | This Design |
|---------|-------------|-------|------------|-------------|
| Session format | JSONL | JSON | JSON | SQLite |
| Resume | ✓ | ✓ | ✓ | ✓ |
| Checkpoint | ✓ | ✓ | ✗ | ✓ |
| Fork | ✓ | ✓ | ✗ | ✓ |
| Token budget | ✓ | ✓ | ✗ | ✓ |
| Loop detection | ✓ | ✓ | ✗ | ✓ |
| Context layers | 5 | 5 | 4 | 7 |
| Prompt components | ✗ | ✗ | ✓ | ✓ |

---

# Appendix: Complete Code Implementations

## A.1 Runtime Module Full Implementation

### runtime/models.py

```python
"""Runtime data models for Agent execution."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Optional, List, Dict, Any, Callable
import uuid


class AgentState(Enum):
    """Agent runtime state machine states."""
    IDLE = "idle"                           # Waiting for input
    ASSEMBLING_CONTEXT = "assembling_context"  # Building prompt context
    WAITING_FOR_LLM = "waiting_for_llm"      # LLM call in progress
    EXECUTING_TOOLS = "executing_tools"       # Tool calls running
    COMPACTING = "compacting"                 # Compressing history
    PAUSED = "paused"                         # User paused
    COMPLETED = "completed"                   # Turn completed
    ERROR = "error"                           # Error occurred


class ToolExecutionState(Enum):
    """Individual tool call state machine."""
    PENDING = "pending"           # Queued for execution
    VALIDATING = "validating"     # Validating arguments
    APPROVING = "approving"       # Waiting for approval (reserved)
    EXECUTING = "executing"       # Currently running
    SUCCESS = "success"           # Completed successfully
    ERROR = "error"               # Failed with error
    CANCELLED = "cancelled"       # Cancelled by user/system


@dataclass
class ToolCallRecord:
    """Record of a single tool invocation."""
    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    
    # Execution state
    state: ToolExecutionState = ToolExecutionState.PENDING
    
    # Results
    result: Optional[str] = None
    error_message: Optional[str] = None
    output_truncated: bool = False
    
    # Metadata
    retry_count: int = 0
    max_retries: int = 3
    
    # Timing
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: float = 0.0
    timeout_seconds: int = 120
    
    def mark_started(self):
        """Mark execution started."""
        self.state = ToolExecutionState.EXECUTING
        self.started_at = datetime.now()
    
    def mark_success(self, result: str, truncated: bool = False):
        """Mark successful completion."""
        self.state = ToolExecutionState.SUCCESS
        self.result = result
        self.output_truncated = truncated
        self.completed_at = datetime.now()
        if self.started_at:
            self.duration_ms = (self.completed_at - self.started_at).total_seconds() * 1000
    
    def mark_error(self, error: str):
        """Mark execution error."""
        self.state = ToolExecutionState.ERROR
        self.error_message = error
        self.completed_at = datetime.now()
        if self.started_at:
            self.duration_ms = (self.completed_at - self.started_at).total_seconds() * 1000
    
    def can_retry(self) -> bool:
        """Check if eligible for retry."""
        return self.retry_count < self.max_retries and self.state == ToolExecutionState.ERROR


@dataclass
class AgentTurn:
    """Complete record of one agent turn."""
    turn_number: int = 0
    user_input: str = ""
    
    # Context assembly
    assembled_messages: List[Dict] = field(default_factory=list)
    context_token_count: int = 0
    
    # LLM interaction
    llm_response: Optional[str] = None
    llm_tool_calls: List[Dict] = field(default_factory=list)
    
    # Tool execution
    tool_records: List[ToolCallRecord] = field(default_factory=list)
    
    # Final result
    final_text: str = ""
    
    # Token usage
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    
    # Timing
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    llm_latency_ms: float = 0.0
    tool_latency_ms: float = 0.0
    total_duration_ms: float = 0.0
    
    # State transitions for debugging
    state_transitions: List[Dict[str, Any]] = field(default_factory=list)
    
    def record_state_change(self, from_state: AgentState, to_state: AgentState, reason: str = ""):
        """Record a state transition."""
        self.state_transitions.append({
            "from": from_state.value,
            "to": to_state.value,
            "timestamp": datetime.now().isoformat(),
            "reason": reason
        })
    
    def complete(self):
        """Mark turn as complete."""
        self.completed_at = datetime.now()
        self.total_duration_ms = (self.completed_at - self.started_at).total_seconds() * 1000


@dataclass
class AgentConfig:
    """Configuration for Agent runtime."""
    # Iteration limits
    max_iterations: int = 50
    max_empty_iterations: int = 3
    
    # Tool execution
    max_parallel_tools: int = 5
    default_tool_timeout: int = 120
    max_tool_timeout: int = 600
    max_tool_retries: int = 3
    
    # Loop detection
    loop_detection_window: int = 6
    loop_similarity_threshold: float = 0.85
    
    # Error handling
    stop_on_tool_error: bool = False
    max_consecutive_errors: int = 3
    
    # Callbacks
    on_state_change: Optional[Callable[[AgentState, AgentState], None]] = None
    on_tool_complete: Optional[Callable[[ToolCallRecord], None]] = None
    on_iteration_complete: Optional[Callable[[int, AgentTurn], None]] = None


@dataclass
class LLMResponse:
    """Structured LLM response."""
    content: Optional[str] = None
    tool_calls: List[Dict] = field(default_factory=list)
    
    # Usage
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    
    # Metadata
    model: str = ""
    finish_reason: str = ""
    latency_ms: float = 0.0
    
    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0
```

### runtime/tool_scheduler.py

```python
"""Tool execution scheduler with state machine and parallel support."""

import asyncio
import logging
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.core.tool import Tool, ToolRegistry
from .models import ToolCallRecord, ToolExecutionState

logger = logging.getLogger(__name__)


class ToolScheduler:
    """
    Schedules and executes tool calls with state tracking.
    
    Supports parallel execution for independent tools.
    """
    
    def __init__(
        self,
        tool_registry: ToolRegistry,
        max_parallel: int = 5,
        default_timeout: int = 120
    ):
        self.tool_registry = tool_registry
        self.max_parallel = max_parallel
        self.default_timeout = default_timeout
        self._executor = ThreadPoolExecutor(max_workers=max_parallel)
    
    async def schedule(self, records: List[ToolCallRecord]) -> List[ToolCallRecord]:
        """
        Execute multiple tool calls.
        
        Currently sequential - can be enhanced for parallel execution
        of independent tools.
        """
        for record in records:
            await self.execute(record)
        return records
    
    async def execute(self, record: ToolCallRecord) -> ToolCallRecord:
        """
        Execute a single tool call through the full state machine:
        PENDING -> VALIDATING -> EXECUTING -> SUCCESS/ERROR
        """
        try:
            # State: VALIDATING
            record.state = ToolExecutionState.VALIDATING
            
            # Lookup tool
            tool = self.tool_registry.get(record.tool_name)
            if not tool:
                record.mark_error(f"Tool '{record.tool_name}' not found")
                return record
            
            # Validate arguments (basic check)
            valid, error = self._validate_arguments(tool, record.arguments)
            if not valid:
                record.mark_error(f"Argument validation failed: {error}")
                return record
            
            # State: EXECUTING
            record.mark_started()
            logger.info(f"Executing tool {record.tool_name} (timeout={record.timeout_seconds}s)")
            
            # Execute with timeout
            try:
                result = await asyncio.wait_for(
                    self._execute_tool_async(tool, record.arguments),
                    timeout=record.timeout_seconds
                )
                
                # Check for truncation
                truncated = len(result) > 30000  # 30K char limit
                if truncated:
                    result = result[:30000] + "\n... [output truncated]"
                
                record.mark_success(result, truncated)
                logger.info(f"Tool {record.tool_name} completed in {record.duration_ms:.0f}ms")
                
            except asyncio.TimeoutError:
                record.mark_error(f"Tool execution timed out after {record.timeout_seconds}s")
                
        except Exception as e:
            logger.exception(f"Tool execution failed: {e}")
            record.mark_error(str(e))
        
        return record
    
    async def _execute_tool_async(self, tool: Tool, arguments: dict) -> str:
        """Execute tool in thread pool to not block event loop."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            tool.execute,
            arguments
        )
    
    def _validate_arguments(self, tool: Tool, arguments: dict) -> tuple[bool, Optional[str]]:
        """Validate tool arguments against schema."""
        required = set(tool.parameters.keys())
        provided = set(arguments.keys())
        
        missing = required - provided
        if missing:
            return False, f"Missing required arguments: {missing}"
        
        # Type checking could be added here
        return True, None
    
    def close(self):
        """Cleanup resources."""
        self._executor.shutdown(wait=False)


class ParallelToolScheduler(ToolScheduler):
    """Enhanced scheduler with true parallel execution support."""
    
    async def schedule(self, records: List[ToolCallRecord]) -> List[ToolCallRecord]:
        """Execute independent tools in parallel."""
        # Create tasks for all records
        tasks = [self.execute(record) for record in records]
        
        # Run with semaphore to limit concurrency
        semaphore = asyncio.Semaphore(self.max_parallel)
        
        async def bounded_execute(task):
            async with semaphore:
                return await task
        
        bounded_tasks = [bounded_execute(t) for t in tasks]
        results = await asyncio.gather(*bounded_tasks, return_exceptions=True)
        
        # Handle any exceptions
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                records[i].mark_error(f"Execution failed: {result}")
        
        return records
```

### runtime/loop_detector.py

```python
"""Detects repetitive tool call patterns to prevent infinite loops."""

import logging
from typing import List, Dict, Any
from difflib import SequenceMatcher

from .models import ToolCallRecord

logger = logging.getLogger(__name__)


class LoopDetector:
    """
    Detects when the agent is repeating the same actions.
    
    Uses sliding window + similarity comparison.
    """
    
    def __init__(
        self,
        window_size: int = 6,
        similarity_threshold: float = 0.85,
        max_repetitions: int = 3
    ):
        self.window_size = window_size
        self.similarity_threshold = similarity_threshold
        self.max_repetitions = max_repetitions
        
        # History of tool call sets
        self._history: List[List[Dict]] = []
        self._warning_count = 0
    
    def record(self, records: List[ToolCallRecord]) -> None:
        """Record a set of tool calls for pattern detection."""
        snapshot = [
            {
                "tool": r.tool_name,
                "args": self._normalize_args(r.arguments)
            }
            for r in records
        ]
        
        self._history.append(snapshot)
        
        # Keep only recent history
        if len(self._history) > self.window_size * 2:
            self._history = self._history[-self.window_size * 2:]
    
    def detect(self) -> tuple[bool, Optional[str]]:
        """
        Detect if we're in a loop.
        
        Returns: (is_looping, warning_message)
        """
        if len(self._history) < self.window_size:
            return False, None
        
        # Get recent window
        recent = self._history[-self.window_size:]
        
        # Compare with previous windows
        repetitions = 0
        for i in range(len(self._history) - self.window_size):
            old_window = self._history[i:i + self.window_size]
            similarity = self._window_similarity(old_window, recent)
            
            if similarity >= self.similarity_threshold:
                repetitions += 1
        
        if repetitions >= self.max_repetitions:
            self._warning_count += 1
            warning = self._get_warning_message()
            logger.warning(f"Loop detected! Repetitions={repetitions}")
            return True, warning
        
        # Reset warning count if we're making progress
        if repetitions == 0:
            self._warning_count = 0
        
        return False, None
    
    def _window_similarity(
        self,
        window1: List[List[Dict]],
        window2: List[List[Dict]]
    ) -> float:
        """Calculate similarity between two windows of tool calls."""
        if len(window1) != len(window2):
            return 0.0
        
        similarities = []
        for calls1, calls2 in zip(window1, window2):
            sim = self._calls_similarity(calls1, calls2)
            similarities.append(sim)
        
        return sum(similarities) / len(similarities) if similarities else 0.0
    
    def _calls_similarity(self, calls1: List[Dict], calls2: List[Dict]) -> float:
        """Calculate similarity between two sets of tool calls."""
        if len(calls1) != len(calls2):
            return 0.0
        
        matches = 0
        for c1, c2 in zip(calls1, calls2):
            if c1["tool"] != c2["tool"]:
                continue
            
            # Compare arguments
            arg_sim = self._dict_similarity(c1["args"], c2["args"])
            if arg_sim > 0.8:
                matches += 1
        
        return matches / max(len(calls1), len(calls2))
    
    def _dict_similarity(self, d1: Dict, d2: Dict) -> float:
        """Calculate string similarity of two dicts."""
        s1 = str(sorted(d1.items()))
        s2 = str(sorted(d2.items()))
        return SequenceMatcher(None, s1, s2).ratio()
    
    def _normalize_args(self, args: Dict) -> Dict:
        """Normalize arguments for comparison (remove volatile fields)."""
        # Remove timestamps, random IDs, etc.
        volatile_suffixes = ('_at', '_time', '_id', 'timestamp')
        return {
            k: v for k, v in args.items()
            if not any(k.endswith(s) for s in volatile_suffixes)
        }
    
    def _get_warning_message(self) -> str:
        """Generate warning message based on loop severity."""
        base = """⚠️ **Loop Detection Warning**

The agent appears to be repeating similar actions without making progress.

Please try:
1. Summarize what you've learned so far
2. Take a different approach
3. Ask the user for guidance
4. Consider the task complete if no more progress can be made"""
        
        if self._warning_count > 1:
            base += f"\n\n(This is warning #{self._warning_count})"
        
        return base
    
    def reset(self):
        """Clear detection history."""
        self._history.clear()
        self._warning_count = 0
```

### runtime/agent_loop.py

```python
"""Main Agent execution loop - unified implementation."""

import logging
from typing import Optional, List

from src.core.models import Message, Role
from src.session.session_manager import SessionManager
from src.context.context_assembler import ContextAssembler
from src.engine.base import BaseEngine

from .models import AgentState, AgentTurn, AgentConfig, LLMResponse
from .tool_scheduler import ToolScheduler
from .loop_detector import LoopDetector

logger = logging.getLogger(__name__)


class AgentLoop:
    """
    Unified Agent execution loop.
    
    Orchestrates:
    - Context assembly
    - LLM calls
    - Tool execution
    - Loop detection
    - State management
    """
    
    def __init__(
        self,
        engine: BaseEngine,
        session_manager: SessionManager,
        context_assembler: ContextAssembler,
        tool_scheduler: ToolScheduler,
        loop_detector: LoopDetector,
        config: AgentConfig
    ):
        self.engine = engine
        self.session_manager = session_manager
        self.context_assembler = context_assembler
        self.tool_scheduler = tool_scheduler
        self.loop_detector = loop_detector
        self.config = config
        
        self._state = AgentState.IDLE
        self._consecutive_errors = 0
    
    @property
    def state(self) -> AgentState:
        return self._state
    
    def _transition(self, new_state: AgentState, reason: str = ""):
        """Transition to new state with callback."""
        old_state = self._state
        self._state = new_state
        
        if self.config.on_state_change:
            try:
                self.config.on_state_change(old_state, new_state)
            except Exception as e:
                logger.error(f"State change callback error: {e}")
        
        logger.debug(f"State: {old_state.value} -> {new_state.value} ({reason})")
    
    async def run(self, user_input: str) -> str:
        """
        Execute one complete turn.
        
        This is the main entry point - replaces engine.chat()
        """
        turn = AgentTurn(user_input=user_input)
        
        try:
            iteration = 0
            final_response = None
            
            while iteration < self.config.max_iterations:
                iteration += 1
                turn.turn_number = iteration
                
                # 1. Assemble context
                self._transition(AgentState.ASSEMBLING_CONTEXT, f"iteration {iteration}")
                messages = self.context_assembler.build(user_input)
                turn.assembled_messages = [m.to_dict() for m in messages]
                
                # 2. Call LLM
                self._transition(AgentState.WAITING_FOR_LLM)
                llm_response = await self._call_llm(messages)
                turn.llm_latency_ms += llm_response.latency_ms
                turn.input_tokens += llm_response.input_tokens
                turn.output_tokens += llm_response.output_tokens
                
                # 3. Handle tool calls or return result
                if llm_response.has_tool_calls:
                    self._transition(AgentState.EXECUTING_TOOLS)
                    
                    # Create tool records
                    tool_records = [
                        ToolCallRecord(
                            tool_name=tc.get("name", ""),
                            arguments=tc.get("arguments", {}),
                            timeout_seconds=self.config.default_tool_timeout
                        )
                        for tc in llm_response.tool_calls
                    ]
                    
                    # Execute tools
                    await self.tool_scheduler.schedule(tool_records)
                    turn.tool_records.extend(tool_records)
                    
                    # Check for errors
                    errors = [r for r in tool_records if r.state.value == "error"]
                    if errors:
                        self._consecutive_errors += 1
                        if self._consecutive_errors >= self.config.max_consecutive_errors:
                            raise RuntimeError(f"Too many consecutive errors: {errors[0].error_message}")
                        if self.config.stop_on_tool_error:
                            raise RuntimeError(f"Tool error (stop_on_tool_error): {errors[0].error_message}")
                    else:
                        self._consecutive_errors = 0
                    
                    # Convert tool results to messages for next iteration
                    for record in tool_records:
                        messages.append(Message(
                            role=Role.TOOL,
                            content=record.result or record.error_message or "",
                            tool_call_id=record.id
                        ))
                    
                    # Loop detection
                    self.loop_detector.record(tool_records)
                    is_looping, warning = self.loop_detector.detect()
                    if is_looping:
                        # Inject warning as system message
                        messages.append(Message.system(warning))
                    
                    # Continue to next iteration
                    continue
                
                else:
                    # Final response - done
                    final_response = llm_response.content or ""
                    break
            
            # Handle max iterations
            if iteration >= self.config.max_iterations:
                final_response = "Maximum iterations reached. Task may be incomplete."
                logger.warning(final_response)
            
            # Complete turn
            turn.final_text = final_response
            turn.complete()
            
            # Record in session
            self._record_turn(turn)
            
            self._transition(AgentState.COMPLETED)
            return final_response
            
        except Exception as e:
            logger.exception("Agent loop failed")
            self._transition(AgentState.ERROR, str(e))
            raise
    
    def run_sync(self, user_input: str) -> str:
        """Synchronous wrapper for run()."""
        import asyncio
        return asyncio.run(self.run(user_input))
    
    async def _call_llm(self, messages: List[Message]) -> LLMResponse:
        """Call LLM and wrap response."""
        import time
        
        start = time.time()
        
        # Call engine
        response = self.engine.call_llm(
            messages=messages,
            tools=self.tool_scheduler.tool_registry.get_all_tools(),
            system_prompt=None  # Already in messages
        )
        
        latency = (time.time() - start) * 1000
        
        return LLMResponse(
            content=response.get("content"),
            tool_calls=response.get("tool_calls", []),
            input_tokens=response.get("usage", {}).get("input_tokens", 0),
            output_tokens=response.get("usage", {}).get("output_tokens", 0),
            model=response.get("model", ""),
            finish_reason=response.get("finish_reason", ""),
            latency_ms=latency
        )
    
    def _record_turn(self, turn: AgentTurn):
        """Record turn in session history."""
        session = self.session_manager.active_session
        if not session:
            return
        
        # Record user message
        self.session_manager.record_turn(
            role="user",
            content=turn.user_input,
            token_count=0  # Will be estimated
        )
        
        # Record assistant response
        self.session_manager.record_turn(
            role="assistant",
            content=turn.final_text,
            token_count=turn.output_tokens,
            latency_ms=turn.llm_latency_ms
        )
```

---

## A.2 Context Module Full Implementation

### context/context_window.py

```python
"""Token budget management for context window."""

from dataclasses import dataclass
from typing import List, Optional

from src.core.models import Message


@dataclass
class ContextBudget:
    """Token budget configuration."""
    max_tokens: int = 128000        # Model context window
    reserve_tokens: int = 8192       # Reserved for response
    warning_threshold: float = 0.8   # 80% - trigger warning
    critical_threshold: float = 0.95  # 95% - force compression
    
    @property
    def available_for_context(self) -> int:
        """Tokens available for input context."""
        return self.max_tokens - self.reserve_tokens


class ContextWindow:
    """
    Manages token budget for context assembly.
    
    Tracks usage and determines when compression is needed.
    """
    
    def __init__(self, budget: ContextBudget):
        self.budget = budget
        self._current_usage = 0
        self._message_counts: List[int] = []  # Per-message token counts
    
    def estimate_tokens(self, messages: List[Message]) -> int:
        """Estimate token count for messages."""
        # Simple estimation: ~4 chars per token
        total = 0
        for msg in messages:
            content_len = len(msg.content or "")
            # Add overhead for role, formatting
            total += content_len // 4 + 10
        return total
    
    def fits(self, messages: List[Message]) -> bool:
        """Check if messages fit in remaining budget."""
        needed = self.estimate_tokens(messages)
        return (self._current_usage + needed) <= self.budget.available_for_context
    
    def remaining(self) -> int:
        """Get remaining token budget."""
        return self.budget.available_for_context - self._current_usage
    
    def utilization(self) -> float:
        """Get current utilization ratio (0-1)."""
        return self._current_usage / self.budget.available_for_context
    
    def should_warn(self) -> bool:
        """Check if nearing limit (warning threshold)."""
        return self.utilization() >= self.budget.warning_threshold
    
    def should_compress(self) -> bool:
        """Check if compression required (critical threshold)."""
        return self.utilization() >= self.budget.critical_threshold
    
    def add(self, messages: List[Message]) -> bool:
        """
        Add messages to window.
        
        Returns: True if added, False if doesn't fit
        """
        needed = self.estimate_tokens(messages)
        if self._current_usage + needed > self.budget.available_for_context:
            return False
        
        self._current_usage += needed
        self._message_counts.append(needed)
        return True
    
    def remove(self, count: int = 1) -> int:
        """
        Remove last N messages from budget.
        
        Returns: tokens freed
        """
        freed = 0
        for _ in range(min(count, len(self._message_counts))):
            freed += self._message_counts.pop()
        self._current_usage -= freed
        return freed
```

### context/context_assembler.py

```python
"""Assembles layered context for LLM calls."""

import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field

from src.core.models import Message, Role
from src.session.session_manager import SessionManager
from src.memory.memory_controller import MemoryController
from src.prompt.prompt_provider import PromptProvider

from .context_window import ContextWindow, ContextBudget
from .context_compressor import ContextCompressor

logger = logging.getLogger(__name__)


@dataclass
class ContextLayer:
    """Single layer of context."""
    name: str
    priority: int  # Higher = more important, kept longer
    messages: List[Message] = field(default_factory=list)
    required: bool = False  # If True, never evicted


class ContextAssembler:
    """
    Builds complete context from multiple layers.
    
    Layer priority (high to low):
    1. System prompt (required)
    2. User input (required)
    3. Project memory (required)
    4. Retrieved memories
    5. Active skill context
    6. Conversation history
    7. Tool outputs (most evictable)
    """
    
    def __init__(
        self,
        session_manager: SessionManager,
        memory_controller: Optional[MemoryController] = None,
        prompt_provider: Optional[PromptProvider] = None,
        compressor: Optional[ContextCompressor] = None,
        max_tokens: int = 128000
    ):
        self.session_manager = session_manager
        self.memory_controller = memory_controller
        self.prompt_provider = prompt_provider
        self.compressor = compressor or ContextCompressor()
        self.window = ContextWindow(ContextBudget(max_tokens=max_tokens))
    
    def build(
        self,
        user_input: str,
        skill_context: Optional[str] = None
    ) -> List[Message]:
        """
        Build complete context for LLM call.
        
        Returns list of messages ready for LLM.
        """
        layers = self._build_layers(user_input, skill_context)
        
        # Add layers in priority order
        result = []
        for layer in sorted(layers, key=lambda l: l.priority, reverse=True):
            if not layer.messages:
                continue
            
            # Check if fits
            if not self.window.fits(layer.messages):
                if layer.required:
                    # Must fit - try compression
                    logger.warning(f"Required layer '{layer.name}' doesn't fit, compressing")
                    layer.messages = self._compress_layer(layer.messages)
                else:
                    # Skip this layer
                    logger.debug(f"Skipping layer '{layer.name}' - doesn't fit")
                    continue
            
            self.window.add(layer.messages)
            result.extend(layer.messages)
        
        # Check if we need compression
        if self.window.should_compress():
            logger.info("Context utilization critical, triggering compression")
            result = self._compress_messages(result)
        
        return result
    
    def _build_layers(
        self,
        user_input: str,
        skill_context: Optional[str]
    ) -> List[ContextLayer]:
        """Build all context layers."""
        layers = []
        
        # L1: System prompt (highest priority)
        if self.prompt_provider:
            system_prompt = self.prompt_provider.build_system_prompt()
            layers.append(ContextLayer(
                name="system",
                priority=100,
                messages=[Message.system(system_prompt)],
                required=True
            ))
        
        # L2: Project memory
        project_memory = self._load_project_memory()
        if project_memory:
            layers.append(ContextLayer(
                name="project",
                priority=90,
                messages=[Message.system(f"## Project Context\n\n{project_memory}")],
                required=True
            ))
        
        # L3: Retrieved memories
        if self.memory_controller:
            memories = self.memory_controller.retrieve_relevant(user_input, top_k=3)
            if memories:
                memory_text = "\n\n".join([m.content for m in memories])
                layers.append(ContextLayer(
                    name="memory",
                    priority=70,
                    messages=[Message.system(f"## Relevant Memories\n\n{memory_text}")]
                ))
        
        # L4: Active skill
        if skill_context:
            layers.append(ContextLayer(
                name="skill",
                priority=60,
                messages=[Message.system(f"## Active Skill\n\n{skill_context}")]
            ))
        
        # L5: Conversation history
        history = self._load_history()
        if history:
            layers.append(ContextLayer(
                name="history",
                priority=40,
                messages=history
            ))
        
        # L6: User input (required, but after system context)
        layers.append(ContextLayer(
            name="user",
            priority=95,
            messages=[Message.user(user_input)],
            required=True
        ))
        
        return layers
    
    def _load_project_memory(self) -> Optional[str]:
        """Load PROJECT.md if exists."""
        # Implementation would search for and load PROJECT.md
        return None
    
    def _load_history(self) -> List[Message]:
        """Load recent conversation history."""
        session = self.session_manager.active_session
        if not session:
            return []
        
        turns = self.session_manager.get_history(recent_n=10)
        messages = []
        for turn in turns:
            if turn.role == "assistant" and turn.tool_calls:
                # Include tool calls
                messages.append(Message(
                    role=Role.ASSISTANT,
                    content=turn.content,
                    tool_calls=turn.tool_calls
                ))
            else:
                role = Role(turn.role) if turn.role in [r.value for r in Role] else Role.USER
                messages.append(Message(role=role, content=turn.content))
        
        return messages
    
    def _compress_layer(self, messages: List[Message]) -> List[Message]:
        """Compress a single layer."""
        return self.compressor.compress_messages(messages)
    
    def _compress_messages(self, messages: List[Message]) -> List[Message]:
        """Compress entire message list."""
        return self.compressor.compress_history(messages)
```

### context/context_compressor.py

```python
"""Compresses context to fit within token budget."""

import logging
from typing import List, Optional

from src.core.models import Message, Role

logger = logging.getLogger(__name__)


class ContextCompressor:
    """
    Compresses conversation history and context.
    
    Strategies:
    1. Truncate long tool outputs
    2. Remove old messages beyond threshold
    3. Summarize old conversation (if LLM available)
    """
    
    def __init__(
        self,
        max_tool_output_length: int = 2000,
        summary_threshold: int = 20,  # Summarize if more than N messages
        keep_recent: int = 6        # Always keep N most recent
    ):
        self.max_tool_output_length = max_tool_output_length
        self.summary_threshold = summary_threshold
        self.keep_recent = keep_recent
    
    def compress_messages(self, messages: List[Message]) -> List[Message]:
        """Simple compression - truncate tool outputs."""
        compressed = []
        for msg in messages:
            if len(msg.content or "") > self.max_tool_output_length * 2:
                # Truncate long content
                truncated = msg.content[:self.max_tool_output_length]
                truncated += f"\n... [{len(msg.content) - self.max_tool_output_length} chars truncated]"
                compressed.append(Message(
                    role=msg.role,
                    content=truncated,
                    tool_calls=msg.tool_calls,
                    tool_call_id=msg.tool_call_id
                ))
            else:
                compressed.append(msg)
        return compressed
    
    def compress_history(
        self,
        messages: List[Message],
        target_count: Optional[int] = None
    ) -> List[Message]:
        """
        Compress conversation history.
        
        Keeps recent messages, summarizes older ones.
        """
        if len(messages) <= self.summary_threshold:
            return messages
        
        target = target_count or self.keep_recent + 2
        
        # Always keep system messages and recent user/assistant
        system_msgs = [m for m in messages if m.role == Role.SYSTEM]
        recent_msgs = messages[-self.keep_recent:]
        
        # Middle section to summarize
        middle_start = len(system_msgs)
        middle_end = len(messages) - self.keep_recent
        
        if middle_end > middle_start:
            middle_msgs = messages[middle_start:middle_end]
            
            # Simple summarization: just note that messages were removed
            summary = Message.system(
                f"[... {len(middle_msgs)} earlier messages omitted for brevity ...]"
            )
            
            return system_msgs + [summary] + recent_msgs
        
        return messages
    
    def truncate_for_emergency(self, messages: List[Message], max_chars: int) -> List[Message]:
        """
        Emergency truncation - just cut to fit.
        
        Used when all else fails.
        """
        total = 0
        result = []
        
        for msg in reversed(messages):  # Start from most recent
            msg_len = len(msg.content or "")
            if total + msg_len > max_chars and msg.role != Role.SYSTEM:
                # Skip this message
                continue
            total += msg_len
            result.insert(0, msg)
        
        return result
```

---

## A.3 Memory Module Full Implementation

### memory/models.py

```python
"""Memory system data models."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List
import uuid


class EpisodicType(Enum):
    """Types of episodic memories."""
    DECISION = "decision"      # Key decision made
    ACTION = "action"          # Action taken
    OUTCOME = "outcome"        # Result/outcome
    ERROR = "error"            # Error encountered
    NOTE = "note"              # General note
    INSIGHT = "insight"        # Key insight learned


class SemanticCategory(Enum):
    """Categories of semantic memory."""
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
        """Update last accessed."""
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
        # importance acts as a multiplier
        # Higher importance = slower decay
        base_retention = self.importance
        decay_factor = 0.3 / max(self.importance, 0.1)
        
        import math
        return base_retention * math.exp(-decay_factor * age_days)


@dataclass
class SemanticMemory:
    """Long-term fact/convention memory."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    category: SemanticCategory = SemanticCategory.FACT
    key: str = ""           # Lookup key
    value: str = ""         # Memory content
    
    # Confidence
    confidence: float = 0.8   # 0-1, certainty level
    verification_count: int = 0  # Times verified
    
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
    """Represents a discovered PROJECT.md file."""
    path: str = ""
    content: str = ""
    last_modified: datetime = field(default_factory=datetime.now)
    scope: str = "project"  # 'global' or 'project'
    
    @property
    def summary(self) -> str:
        """Generate a short summary."""
        lines = self.content.split('\n')[:5]
        return '\n'.join(lines)
```

### memory/memory_store.py

```python
"""SQLite storage for memory system."""

import sqlite3
import json
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from .models import EpisodicMemory, SemanticMemory, EpisodicType, SemanticCategory


class MemoryStore:
    """SQLite persistence for memories."""
    
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self._init_tables()
    
    def _init_tables(self):
        """Initialize memory tables."""
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
                
                -- Full-text search for content
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    content, 
                    tokenize='porter',
                    content_rowid='rowid'
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
        """Retrieve an episodic memory by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM episodic_memory WHERE id = ?",
                (memory_id,)
            ).fetchone()
            
            if row:
                return self._row_to_episodic(row)
            return None
    
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
            
            # Use FTS if available, fallback to LIKE
            sql = """SELECT * FROM episodic_memory 
                     WHERE content LIKE ?"""
            params = [f"%{query}%"]
            
            if session_id:
                sql += " AND session_id = ?"
                params.append(session_id)
            
            if types:
                sql += " AND type IN ({})".format(','.join(['?'] * len(types)))
                params.extend([t.value for t in types])
            
            sql += " ORDER BY importance DESC, created_at DESC LIMIT ?"
            params.append(limit)
            
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_episodic(row) for row in rows]
    
    def update_access(self, memory_id: str) -> None:
        """Update access count and last_accessed."""
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
        """Retrieve semantic memory by key."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM semantic_memory WHERE key = ?",
                (key,)
            ).fetchone()
            
            if row:
                return self._row_to_semantic(row)
            return None
    
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
    
    def _row_to_episodic(self, row: sqlite3.Row) -> EpisodicMemory:
        """Convert row to EpisodicMemory."""
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
        """Convert row to SemanticMemory."""
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
```

### memory/memory_controller.py

```python
"""Main memory system controller."""

import logging
from typing import List, Optional
from datetime import datetime
from pathlib import Path

from .models import EpisodicMemory, SemanticMemory, EpisodicType, SemanticCategory
from .memory_store import MemoryStore

logger = logging.getLogger(__name__)


class MemoryController:
    """
    High-level memory management interface.
    
    Coordinates episodic and semantic memory storage,
    retrieval, and maintenance.
    """
    
    def __init__(self, store: MemoryStore):
        self.store = store
    
    # ========== Storage ==========
    
    def record_decision(
        self,
        session_id: str,
        decision: str,
        context: str = "",
        importance: float = 0.8,
        turn_number: Optional[int] = None
    ) -> EpisodicMemory:
        """Record a key decision."""
        memory = EpisodicMemory(
            session_id=session_id,
            type=EpisodicType.DECISION,
            content=decision,
            context=context,
            importance=importance,
            turn_number=turn_number
        )
        return self.store.store_episodic(memory)
    
    def record_action(
        self,
        session_id: str,
        action: str,
        importance: float = 0.5,
        turn_number: Optional[int] = None
    ) -> EpisodicMemory:
        """Record an action taken."""
        memory = EpisodicMemory(
            session_id=session_id,
            type=EpisodicType.ACTION,
            content=action,
            importance=importance,
            turn_number=turn_number
        )
        return self.store.store_episodic(memory)
    
    def record_outcome(
        self,
        session_id: str,
        outcome: str,
        success: bool = True,
        turn_number: Optional[int] = None
    ) -> EpisodicMemory:
        """Record an outcome."""
        memory = EpisodicMemory(
            session_id=session_id,
            type=EpisodicType.OUTCOME,
            content=outcome,
            importance=0.9 if success else 0.7,
            turn_number=turn_number
        )
        return self.store.store_episodic(memory)
    
    def record_error(
        self,
        session_id: str,
        error: str,
        turn_number: Optional[int] = None
    ) -> EpisodicMemory:
        """Record an error for future reference."""
        memory = EpisodicMemory(
            session_id=session_id,
            type=EpisodicType.ERROR,
            content=error,
            importance=0.8,  # Errors are important
            turn_number=turn_number
        )
        return self.store.store_episodic(memory)
    
    def record_insight(
        self,
        session_id: str,
        insight: str,
        turn_number: Optional[int] = None
    ) -> EpisodicMemory:
        """Record a key insight."""
        memory = EpisodicMemory(
            session_id=session_id,
            type=EpisodicType.INSIGHT,
            content=insight,
            importance=0.9,
            turn_number=turn_number
        )
        return self.store.store_episodic(memory)
    
    def store_preference(
        self,
        key: str,
        value: str,
        confidence: float = 0.8,
        source_session: Optional[str] = None
    ) -> SemanticMemory:
        """Store a user preference."""
        memory = SemanticMemory(
            category=SemanticCategory.PREFERENCE,
            key=key,
            value=value,
            confidence=confidence,
            source_session=source_session
        )
        return self.store.store_semantic(memory)
    
    def store_convention(
        self,
        key: str,
        value: str,
        confidence: float = 0.9,
        source_session: Optional[str] = None
    ) -> SemanticMemory:
        """Store a coding convention."""
        memory = SemanticMemory(
            category=SemanticCategory.CONVENTION,
            key=key,
            value=value,
            confidence=confidence,
            source_session=source_session
        )
        return self.store.store_semantic(memory)
    
    # ========== Retrieval ==========
    
    def retrieve_relevant(
        self,
        query: str,
        session_id: Optional[str] = None,
        top_k: int = 5,
        include_semantic: bool = True
    ) -> List[EpisodicMemory]:
        """
        Retrieve memories relevant to the query.
        
        Searches both episodic and semantic memory.
        """
        results = []
        
        # Search episodic memories
        episodic = self.store.search_episodic(
            query=query,
            session_id=session_id,
            limit=top_k
        )
        
        for mem in episodic:
            mem.touch()
            self.store.update_access(mem.id)
            results.append(mem)
        
        # Include semantic if requested
        if include_semantic:
            semantic = self.store.search_semantic(query=query, limit=top_k)
            for sem in semantic:
                # Convert to episodic format for unified handling
                fake_ep = EpisodicMemory(
                    id=f"sem:{sem.key}",
                    session_id="semantic",
                    type=EpisodicType.NOTE,
                    content=f"{sem.key}: {sem.value}",
                    importance=sem.confidence
                )
                results.append(fake_ep)
        
        # Sort by importance and take top_k
        results.sort(key=lambda m: m.importance, reverse=True)
        return results[:top_k]
    
    def get_preferences(self) -> List[SemanticMemory]:
        """Get all stored preferences."""
        return self.store.search_semantic(category=SemanticCategory.PREFERENCE)
    
    def get_conventions(self) -> List[SemanticMemory]:
        """Get all stored conventions."""
        return self.store.search_semantic(category=SemanticCategory.CONVENTION)
    
    # ========== Maintenance ==========
    
    def cleanup_expired(self, threshold: float = 0.05) -> int:
        """
        Remove episodic memories with low retention scores.
        
        Returns: number of memories deleted
        """
        now = datetime.now()
        old_memories = self.store.get_old_memories(days=7)
        
        deleted = 0
        for mem in old_memories:
            score = mem.retention_score(now)
            if score < threshold:
                self.store.delete_episodic(mem.id)
                deleted += 1
                logger.debug(f"Deleted expired memory {mem.id[:8]} (score={score:.3f})")
        
        if deleted > 0:
            logger.info(f"Cleaned up {deleted} expired memories")
        
        return deleted
    
    def summarize_session(self, session_id: str) -> str:
        """
        Generate a summary of key memories from a session.
        """
        # Get high-importance episodic memories
        memories = self.store.search_episodic(
            session_id=session_id,
            query="",  # All
            limit=100
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
```

### memory/project_memory.py

```python
"""PROJECT.md / AGENT.md discovery and loading."""

import logging
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass

from .models import ProjectMemoryFile

logger = logging.getLogger(__name__)


@dataclass
class ProjectMemoryConfig:
    """Configuration for project memory discovery."""
    filenames: List[str] = None
    max_size: int = 100000  # Max file size in bytes
    
    def __post_init__(self):
        if self.filenames is None:
            self.filenames = [
                "PROJECT.md",
                "AGENT.md", 
                ".agent_x1/PROJECT.md",
                ".agent_x1/AGENT.md"
            ]


class ProjectMemoryLoader:
    """
    Discovers and loads PROJECT.md style files.
    
    Search order:
    1. Current working directory
    2. .agent_x1/ subdirectory
    3. User home directory (global config)
    """
    
    def __init__(self, config: Optional[ProjectMemoryConfig] = None):
        self.config = config or ProjectMemoryConfig()
    
    def discover(self, start_path: Optional[Path] = None) -> List[ProjectMemoryFile]:
        """
        Discover all PROJECT.md files in hierarchy.
        
        Returns list from most-specific to least-specific.
        """
        files = []
        
        # Search from start_path up to root
        current = start_path or Path.cwd()
        
        while current != current.parent:
            for filename in self.config.filenames:
                path = current / filename
                if path.exists() and path.stat().st_size < self.config.max_size:
                    try:
                        content = path.read_text(encoding='utf-8')
                        files.append(ProjectMemoryFile(
                            path=str(path),
                            content=content,
                            scope="project" if current == (start_path or Path.cwd()) else "parent"
                        ))
                        logger.debug(f"Found project memory: {path}")
                    except Exception as e:
                        logger.warning(f"Failed to read {path}: {e}")
            
            current = current.parent
        
        # Check home directory for global config
        home = Path.home()
        for filename in ["PROJECT.md", "AGENT.md"]:
            path = home / ".agent_x1" / filename
            if path.exists():
                try:
                    content = path.read_text(encoding='utf-8')
                    files.append(ProjectMemoryFile(
                        path=str(path),
                        content=content,
                        scope="global"
                    ))
                except Exception as e:
                    logger.warning(f"Failed to read {path}: {e}")
        
        return files
    
    def load(self, start_path: Optional[Path] = None) -> Optional[str]:
        """
        Load and combine project memory files.
        
        Most-specific (current dir) takes precedence.
        """
        files = self.discover(start_path)
        
        if not files:
            return None
        
        # Combine content
        sections = []
        for f in files:
            header = f"<!-- From: {f.path} (scope: {f.scope}) -->"
            sections.append(f"{header}\n{f.content}")
        
        return "\n\n---\n\n".join(sections)
    
    def load_single(self, path: Path) -> Optional[ProjectMemoryFile]:
        """Load a single project memory file."""
        if not path.exists():
            return None
        
        try:
            content = path.read_text(encoding='utf-8')
            return ProjectMemoryFile(
                path=str(path),
                content=content
            )
        except Exception as e:
            logger.error(f"Failed to load {path}: {e}")
            return None
```

---

## A.4 Prompt Module Full Implementation

### prompt/prompt_provider.py

```python
"""Main prompt assembly system."""

import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field

from src.core.tool import Tool
from src.skills.models import SkillSummary, SkillSpec

from .sections import (
    render_preamble,
    render_mandates,
    render_tools,
    render_skills_catalog,
    render_active_skill,
    render_project_context,
    render_guidelines,
    render_compression_instructions
)

logger = logging.getLogger(__name__)


@dataclass
class PromptContext:
    """Context for prompt rendering."""
    mode: str = "interactive"  # interactive/single/headless
    model_name: str = ""
    max_tokens: int = 128000
    
    tools: List[Tool] = field(default_factory=list)
    skills: List[SkillSummary] = field(default_factory=list)
    active_skill: Optional[SkillSpec] = None
    
    project_memory: str = ""
    user_preferences: Dict[str, str] = field(default_factory=dict)
    
    runtime_state: str = "idle"
    iteration_count: int = 0
    is_recovery: bool = False  # True if recovering from error


class PromptProvider:
    """
    Assembles system prompts from modular sections.
    
    Replaces the monolithic system_prompt string with
    component-based assembly.
    """
    
    def __init__(self, template_dir: Optional[str] = None):
        self.template_dir = template_dir
        self._section_renderers = {
            "preamble": render_preamble,
            "mandates": render_mandates,
            "tools": render_tools,
            "skills_catalog": render_skills_catalog,
            "active_skill": render_active_skill,
            "project_context": render_project_context,
            "guidelines": render_guidelines,
            "compression": render_compression_instructions
        }
    
    def build_system_prompt(
        self,
        context: PromptContext,
        include_sections: Optional[List[str]] = None
    ) -> str:
        """
        Build complete system prompt.
        
        Args:
            context: Prompt context with all necessary data
            include_sections: Specific sections to include (default: all)
        
        Returns:
            Assembled system prompt string
        """
        sections = []
        
        # Always include these
        sections.append(render_preamble(context))
        sections.append(render_mandates(context))
        
        # Tools (always needed)
        sections.append(render_tools(context))
        
        # Optional sections based on context
        if context.skills and not context.active_skill:
            sections.append(render_skills_catalog(context))
        
        if context.active_skill:
            sections.append(render_active_skill(context))
        
        if context.project_memory:
            sections.append(render_project_context(context))
        
        # Operational guidelines
        sections.append(render_guidelines(context))
        
        # Filter empty sections and join
        non_empty = [s for s in sections if s.strip()]
        return "\n\n---\n\n".join(non_empty)
    
    def build_compression_prompt(
        self,
        messages_to_compress: List[Dict[str, Any]]
    ) -> str:
        """Build prompt for history compression."""
        context = PromptContext(
            mode="headless",
            model_name="compression"
        )
        return render_compression_instructions(context, messages_to_compress)
    
    def get_section(self, name: str, context: PromptContext) -> Optional[str]:
        """Get a single section by name."""
        renderer = self._section_renderers.get(name)
        if renderer:
            return renderer(context)
        return None
```

### prompt/sections.py

```python
"""Individual prompt section renderers.

Each function takes PromptContext and returns a string section.
"""

from typing import List, Optional, Dict, Any

from .prompt_provider import PromptContext


def render_preamble(ctx: PromptContext) -> str:
    """Render identity and mode preamble."""
    lines = [
        "# Agent X1",
        "",
        f"You are Agent X1, an autonomous AI assistant specializing in research,",
        f"analysis, and software engineering tasks.",
        "",
        f"**Current Mode**: {ctx.mode}",
        f"**Model**: {ctx.model_name}",
    ]
    
    if ctx.iteration_count > 0:
        lines.append(f"**Iteration**: {ctx.iteration_count}")
    
    if ctx.is_recovery:
        lines.append("**Status**: Recovering from previous error")
    
    return "\n".join(lines)


def render_mandates(ctx: PromptContext) -> str:
    """Render core mandates/rules."""
    return """## Core Mandates

1. **Verify before acting** - Always read files before editing them
2. **Minimal changes** - Prefer small, targeted edits over large rewrites
3. **Explain reasoning** - Briefly explain your approach before executing tools
4. **Follow conventions** - Respect project conventions from PROJECT.md
5. **Be concise** - Avoid verbose output unless specifically requested
6. **Handle errors gracefully** - If a tool fails, analyze why and try alternatives
"""


def render_tools(ctx: PromptContext) -> str:
    """Render available tools section."""
    lines = ["## Available Tools", ""]
    
    if not ctx.tools:
        lines.append("No tools available.")
        return "\n".join(lines)
    
    # Group by category if available
    by_category = {}
    for tool in ctx.tools:
        cat = getattr(tool, 'category', 'general')
        by_category.setdefault(cat, []).append(tool)
    
    for category, tools in sorted(by_category.items()):
        lines.append(f"### {category.title()}")
        for tool in tools:
            desc = tool.description[:100] + "..." if len(tool.description) > 100 else tool.description
            lines.append(f"- **{tool.name}**: {desc}")
        lines.append("")
    
    return "\n".join(lines)


def render_skills_catalog(ctx: PromptContext) -> str:
    """Render available skills catalog."""
    if not ctx.skills:
        return ""
    
    lines = ["## Available Skills", ""]
    lines.append("Use `activate_skill(skill_name)` to activate a skill.")
    lines.append("")
    
    for skill in ctx.skills:
        lines.append(f"- **{skill.name}**: {skill.description}")
        if skill.tags:
            lines.append(f"  Tags: {', '.join(skill.tags)}")
    
    return "\n".join(lines)


def render_active_skill(ctx: PromptContext) -> str:
    """Render active skill context."""
    if not ctx.active_skill:
        return ""
    
    skill = ctx.active_skill
    lines = [
        "## Active Skill",
        "",
        f"**Name**: {skill.metadata.name}",
        f"**Description**: {skill.metadata.description}",
    ]
    
    # Include skill content if available
    if hasattr(skill, 'get_full_context'):
        context = skill.get_full_context()
        if context:
            lines.extend(["", "### Skill Context", "", context])
    
    return "\n".join(lines)


def render_project_context(ctx: PromptContext) -> str:
    """Render PROJECT.md content."""
    if not ctx.project_memory:
        return ""
    
    return f"""## Project Context

{ctx.project_memory}
"""


def render_guidelines(ctx: PromptContext) -> str:
    """Render operational guidelines."""
    guidelines = ["## Operational Guidelines", ""]
    
    # Mode-specific guidelines
    if ctx.mode == "interactive":
        guidelines.extend([
            "- This is an interactive session. You can ask clarifying questions.",
            "- Use `ask_user` tool if you need more information.",
        ])
    elif ctx.mode == "single":
        guidelines.extend([
            "- This is a single-query mode. Provide complete response without asking questions.",
            "- Make reasonable assumptions if information is missing.",
        ])
    
    guidelines.extend([
        "",
        "### Tool Usage",
        "- Check tool parameters carefully before calling",
        "- Handle timeouts gracefully - some tools may take minutes",
        "- If output is truncated, consider using more specific queries",
        "",
        "### File Operations",
        "- Use Glob to find files efficiently",
        "- Use Grep to search content",
        "- Read files before editing (enforced)",
        "- Use Edit for precise changes, Write only for new files",
    ])
    
    return "\n".join(guidelines)


def render_compression_instructions(
    ctx: PromptContext,
    messages: Optional[List[Dict]] = None
) -> str:
    """Render compression instructions for history summarization."""
    return """## Task: Summarize Conversation History

You are being asked to compress a conversation history while preserving key information.

### Instructions

1. Identify the main topic and goal of the conversation
2. Note key decisions made and their rationale
3. Preserve important facts discovered
4. Note any errors encountered and how they were resolved
5. Summarize tool calls by their purpose, not individual invocations

### Output Format

Provide a concise summary in this format:

**Topic**: [main subject]
**Goal**: [what we were trying to achieve]
**Key Decisions**:
- [decision 1]: [rationale]
**Important Facts**:
- [fact 1]
**Status**: [completed/in progress/blocked]
**Next Steps**: [what would logically come next]

Be concise but preserve information needed to continue the task.
"""


def render_loop_warning(ctx: PromptContext, warning_count: int) -> str:
    """Render loop detection warning."""
    base = """⚠️ **Loop Detection Warning**

The agent appears to be repeating similar actions without making progress.

Please try:
1. Summarize what you've learned so far
2. Take a different approach
3. Consider if the task is already complete
4. Ask the user for guidance if stuck
"""
    
    if warning_count > 1:
        base += f"\n(This is warning #{warning_count})"
    
    return base


def render_error_recovery(ctx: PromptContext, error: str) -> str:
    """Render error recovery instructions."""
    return f"""## Error Recovery

The previous operation encountered an error:

```
{error}
```

### Recovery Steps

1. **Analyze** the error message - what went wrong?
2. **Check** if resources (files, connections) are still valid
3. **Adjust** your approach based on the error type
4. **Retry** with corrected parameters if applicable

If the error persists, try a completely different approach or ask the user for guidance.
"""
```

---

## A.5 Loop Module Integration

The Loop module is primarily the `AgentLoop` class shown in A.1, which integrates all other modules.

Key integration points:

1. **Session**: `AgentLoop` records turns via `SessionManager`
2. **Context**: `ContextAssembler` builds messages before each LLM call
3. **Memory**: Memories can be retrieved during context assembly
4. **Prompt**: System prompt is built via `PromptProvider`
5. **Tools**: `ToolScheduler` executes tool calls

### Engine Refactoring

Old interface (to be removed):
```python
class BaseEngine:
    def chat(self, user_input: str) -> str: ...  # Has internal loop
```

New interface:
```python
class BaseEngine:
    def call_llm(
        self, 
        messages: List[Message],
        tools: Optional[List[Tool]] = None,
        system_prompt: Optional[str] = None
    ) -> Dict: ...  # Single call, no loop
```

### Main.py Integration

Old flow:
```python
engine = create_engine(config)
response = engine.chat(user_input)  # Loop inside engine
```

New flow:
```python
# Initialize all components
session_manager = SessionManager(store, config)
memory_controller = MemoryController(memory_store)
prompt_provider = PromptProvider()
context_assembler = ContextAssembler(session_manager, memory_controller, prompt_provider)
tool_scheduler = ToolScheduler(tool_registry)
loop_detector = LoopDetector()

# Create loop (replaces engine.chat)
agent_loop = AgentLoop(
    engine=engine,
    session_manager=session_manager,
    context_assembler=context_assembler,
    tool_scheduler=tool_scheduler,
    loop_detector=loop_detector,
    config=AgentConfig()
)

# Use loop
response = agent_loop.run_sync(user_input)
```

---

## Summary: File Structure

### New Files (18 total)

**Session (4)**
- `src/session/__init__.py`
- `src/session/models.py`
- `src/session/session_store.py`
- `src/session/session_manager.py`

**Runtime (4)**
- `src/runtime/__init__.py`
- `src/runtime/models.py`
- `src/runtime/tool_scheduler.py`
- `src/runtime/loop_detector.py`
- `src/runtime/agent_loop.py`

**Context (3)**
- `src/context/__init__.py`
- `src/context/context_window.py`
- `src/context/context_assembler.py`
- `src/context/context_compressor.py`

**Memory (4)**
- `src/memory/__init__.py`
- `src/memory/models.py`
- `src/memory/memory_store.py`
- `src/memory/memory_controller.py`
- `src/memory/project_memory.py`

**Prompt (3)**
- `src/prompt/__init__.py`
- `src/prompt/prompt_provider.py`
- `src/prompt/sections.py`

### Modified Files (5)

- `src/core/models.py` - Add token_count, importance to Message
- `src/engine/base.py` - Remove loop, simplify to call_llm
- `src/engine/kimi_engine.py` - Remove chat(), implement call_llm
- `src/engine/anthropic_engine.py` - Same
- `main.py` - Use AgentLoop instead of engine.chat()

### Database Schema

```sql
-- data/migrations/001_init.sql
-- Includes: sessions, turns, checkpoints, episodic_memory, semantic_memory tables
```

