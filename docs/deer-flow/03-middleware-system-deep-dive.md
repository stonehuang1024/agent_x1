# DeerFlow 2.0 — Middleware System Deep Dive

> **Document Scope**: This document provides a comprehensive analysis of DeerFlow's middleware chain — the backbone of its extensibility. It covers every built-in middleware, the execution model, hook points, ordering constraints, and the `@Next`/`@Prev` positioning system for custom middleware insertion.

---

## Table of Contents

1. [Middleware Architecture Overview](#1-middleware-architecture-overview)
2. [Middleware Lifecycle & Hook Points](#2-middleware-lifecycle--hook-points)
3. [Middleware Execution Order](#3-middleware-execution-order)
4. [Infrastructure Middlewares](#4-infrastructure-middlewares)
5. [Context Management Middlewares](#5-context-management-middlewares)
6. [Safety & Error Handling Middlewares](#6-safety--error-handling-middlewares)
7. [Feature Middlewares](#7-feature-middlewares)
8. [Custom Middleware Insertion (@Next/@Prev)](#8-custom-middleware-insertion-nextprev)
9. [Feature Flags (RuntimeFeatures)](#9-feature-flags-runtimefeatures)
10. [Middleware State Schemas](#10-middleware-state-schemas)
11. [Lead Agent vs Sub-Agent Middleware Chains](#11-lead-agent-vs-sub-agent-middleware-chains)
12. [Summary](#12-summary)

---

## 1. Middleware Architecture Overview

### 1.1 What is a Middleware?

In DeerFlow, a middleware is a class that extends `AgentMiddleware[StateType]` and implements one or more hook methods. Middlewares form a **chain** that wraps the agent's execution, providing pre-processing, post-processing, and interception capabilities.

### 1.2 The Middleware Chain Pattern

```
User Message → [Middleware 1] → [Middleware 2] → ... → [Middleware N] → LLM
                                                                         │
                                                                         ▼
User ← [Middleware N] ← ... ← [Middleware 2] ← [Middleware 1] ← LLM Response
```

Each middleware can:
- **Modify state** before the agent runs (`before_agent`)
- **Modify state** after the agent runs (`after_agent`)
- **Intercept model calls** to modify inputs/outputs (`wrap_model_call`)
- **Intercept tool calls** to modify execution (`wrap_tool_call`)
- **Modify model output** after each LLM call (`after_model`)

### 1.3 Key Design Principles

1. **Single Responsibility**: Each middleware handles exactly one concern
2. **Order Matters**: Middlewares execute in a specific order with dependency constraints
3. **State Isolation**: Each middleware declares its own state schema (compatible with `ThreadState`)
4. **Sync + Async**: Every hook has both sync and async variants
5. **Composable**: Middlewares can be mixed and matched via feature flags

---

## 2. Middleware Lifecycle & Hook Points

### 2.1 Available Hook Points

```python
class AgentMiddleware[StateType]:
    # ── Agent-level hooks ──
    def before_agent(self, state, runtime) -> dict | None
    def after_agent(self, state, runtime) -> dict | None
    
    # ── Model-level hooks ──
    def before_model(self, state, runtime) -> dict | None
    def after_model(self, state, runtime) -> dict | None
    def wrap_model_call(self, request, handler) -> ModelCallResult
    
    # ── Tool-level hooks ──
    def wrap_tool_call(self, request, handler) -> ToolMessage | Command
    
    # ── Async variants ──
    async def abefore_agent(self, state, runtime) -> dict | None
    async def aafter_agent(self, state, runtime) -> dict | None
    async def abefore_model(self, state, runtime) -> dict | None
    async def aafter_model(self, state, runtime) -> dict | None
    async def awrap_model_call(self, request, handler) -> ModelCallResult
    async def awrap_tool_call(self, request, handler) -> ToolMessage | Command
```

### 2.2 Hook Execution Timeline

```
┌─────────────────────────────────────────────────────────────────┐
│                    Agent Invocation                               │
│                                                                   │
│  ┌─── before_agent() ───┐                                       │
│  │ ThreadDataMiddleware  │ Set up workspace/uploads/outputs paths │
│  │ UploadsMiddleware     │ Inject <uploaded_files> block          │
│  │ SandboxMiddleware     │ Acquire sandbox (if not lazy)          │
│  │ SummarizationMW      │ Compress old messages if needed        │
│  │ TodoMiddleware        │ Inject todo reminder if needed         │
│  │ ViewImageMiddleware   │ Inject image data                     │
│  └───────────────────────┘                                       │
│                                                                   │
│  ┌─── ReAct Loop (may repeat) ──────────────────────────────┐   │
│  │                                                            │   │
│  │  ┌─── before_model() ───┐                                 │   │
│  │  │ TodoMiddleware        │ Inject todo reminder            │   │
│  │  └──────────────────────┘                                 │   │
│  │                                                            │   │
│  │  ┌─── wrap_model_call() ───┐                              │   │
│  │  │ DanglingToolCallMW      │ Patch missing ToolMessages    │   │
│  │  │ DeferredToolFilterMW    │ Remove deferred tool schemas  │   │
│  │  └─────────────────────────┘                              │   │
│  │                                                            │   │
│  │  ┌─── LLM CALL ───┐                                      │   │
│  │  │ model.invoke()  │                                      │   │
│  │  └────────────────┘                                       │   │
│  │                                                            │   │
│  │  ┌─── after_model() ───┐                                  │   │
│  │  │ LoopDetectionMW     │ Check for repetitive tool calls   │   │
│  │  │ SubagentLimitMW     │ Truncate excess task calls        │   │
│  │  │ TitleMiddleware     │ Generate title (first turn only)  │   │
│  │  │ TokenUsageMW        │ Track token consumption           │   │
│  │  └─────────────────────┘                                  │   │
│  │                                                            │   │
│  │  ┌─── If tool_calls present ───┐                          │   │
│  │  │                              │                          │   │
│  │  │  ┌─── wrap_tool_call() ──┐  │                          │   │
│  │  │  │ ToolErrorHandlingMW   │  │ Catch exceptions          │   │
│  │  │  │ SandboxAuditMW       │  │ Log tool invocations       │   │
│  │  │  │ GuardrailMW          │  │ Safety checks              │   │
│  │  │  │ ClarificationMW      │  │ Intercept clarifications   │   │
│  │  │  └──────────────────────┘  │                          │   │
│  │  │                              │                          │   │
│  │  │  Tool execution              │                          │   │
│  │  │  ToolMessage(s) returned     │                          │   │
│  │  └──────────────────────────────┘                          │   │
│  │                                                            │   │
│  │  (Loop back to before_model if tool_calls were present)    │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌─── after_agent() ───┐                                        │
│  │ MemoryMiddleware     │ Queue conversation for memory update    │
│  │ SandboxMiddleware    │ Release sandbox                         │
│  └──────────────────────┘                                        │
└──────────────────────────────────────────────────────────────────┘
```

### 2.3 Return Value Semantics

All `before_*` and `after_*` hooks return `dict | None`:
- **`None`**: No state changes
- **`dict`**: State updates to merge (e.g., `{"messages": [new_msg]}`, `{"title": "New Title"}`)

`wrap_model_call` returns `ModelCallResult`:
- Must call `handler(request)` to continue the chain
- Can modify `request` before passing to handler
- Can modify the result after handler returns

`wrap_tool_call` returns `ToolMessage | Command`:
- `ToolMessage`: Normal tool result
- `Command`: LangGraph control flow (e.g., interrupt, goto END)

---

## 3. Middleware Execution Order

### 3.1 Lead Agent Middleware Chain

The lead agent's middleware chain is carefully ordered for dependency correctness:

```
Position  Middleware                      Hook Points Used
────────  ─────────────────────────────  ──────────────────────────────
  0       ThreadDataMiddleware           before_agent (set up paths)
  1       UploadsMiddleware              before_agent (inject files)
  2       SandboxMiddleware              before_agent + after_agent
  3       DanglingToolCallMiddleware     wrap_model_call
  4       GuardrailMiddleware*           wrap_tool_call
  5       SandboxAuditMiddleware         wrap_tool_call
  6       ToolErrorHandlingMiddleware    wrap_tool_call
  7       SummarizationMiddleware        before_agent
  8       TodoMiddleware*                before_model + tool injection
  9       TokenUsageMiddleware           after_model
 10       TitleMiddleware                after_model
 11       MemoryMiddleware               after_agent
 12       ViewImageMiddleware*           before_agent
 13       DeferredToolFilterMiddleware*  wrap_model_call
 14       SubagentLimitMiddleware*       after_model
 15       LoopDetectionMiddleware        after_model
 16       ClarificationMiddleware        wrap_tool_call (ALWAYS LAST)

* = conditionally included based on configuration
```

### 3.2 Why Order Matters

| Dependency | Reason |
|-----------|--------|
| ThreadData before Uploads | Uploads needs workspace paths to exist |
| ThreadData before Sandbox | Sandbox needs thread_data for path mapping |
| Summarization before TodoReminder | Todo reminder checks if write_todos is still in context |
| DanglingToolCall before LLM call | Must patch messages before LLM sees them |
| DeferredToolFilter before LLM call | Must remove deferred schemas before bind_tools |
| ToolErrorHandling wraps all tools | Must catch exceptions from any tool |
| Clarification ALWAYS last | Must intercept after all other tool processing |
| LoopDetection after model | Must check tool calls after LLM produces them |
| SubagentLimit after model | Must truncate after LLM produces tool calls |
| Memory after agent | Must capture final conversation state |

---

## 4. Infrastructure Middlewares

### 4.1 ThreadDataMiddleware

**Purpose**: Initialize per-thread filesystem paths.

**Hook**: `before_agent`

**Behavior**:
```python
def before_agent(self, state, runtime):
    thread_id = runtime.context.get("thread_id")
    paths = get_paths()
    return {
        "thread_data": {
            "workspace_path": str(paths.sandbox_workspace_dir(thread_id)),
            "uploads_path": str(paths.sandbox_uploads_dir(thread_id)),
            "outputs_path": str(paths.sandbox_outputs_dir(thread_id)),
        }
    }
```

**Key Detail**: With `lazy_init=True`, only sets paths without creating directories. Directories are created on first tool use by `ensure_thread_directories_exist()`.

### 4.2 UploadsMiddleware

**Purpose**: Inject uploaded file information into the agent's context.

**Hook**: `before_agent`

**Behavior**:
1. Reads file metadata from `last_message.additional_kwargs.files`
2. Scans the uploads directory for historical files
3. Creates an `<uploaded_files>` block listing all files with paths and sizes
4. Prepends the block to the last HumanMessage content

**Output format**:
```xml
<uploaded_files>
The following files were uploaded in this message:

- report.pdf (2.5 MB)
  Path: /mnt/user-data/uploads/report.pdf

The following files were uploaded in previous messages and are still available:

- data.csv (150.3 KB)
  Path: /mnt/user-data/uploads/data.csv

You can read these files using the `read_file` tool with the paths shown above.
</uploaded_files>
```

### 4.3 SandboxMiddleware

**Purpose**: Manage sandbox lifecycle (acquire/release).

**Hooks**: `before_agent` (acquire), `after_agent` (release)

**Behavior**:
- **Lazy mode** (`lazy_init=True`, default): Skips `before_agent`. Sandbox acquired on first tool call via `ensure_sandbox_initialized()`.
- **Eager mode** (`lazy_init=False`): Acquires sandbox in `before_agent`.
- **Release**: Always releases sandbox in `after_agent` (returns to pool, not destroyed).

**Key Detail**: Sandboxes are reused across turns within the same thread. The provider maintains a pool keyed by thread_id.

---

## 5. Context Management Middlewares

### 5.1 SummarizationMiddleware

**Purpose**: Compress old messages when context window pressure is high.

**Hook**: `before_agent`

**Trigger conditions** (configurable, OR logic):
- Token count exceeds threshold (e.g., 6000 tokens)
- Message count exceeds threshold (e.g., 75 messages)
- Token fraction of model's max input exceeds threshold

**Behavior**:
1. Count tokens in message history
2. If any trigger condition met:
   a. Identify messages to summarize (all except N most recent)
   b. Protect AI/Tool message pairs (never split them)
   c. Send old messages to a lightweight LLM (e.g., gpt-4o-mini)
   d. Replace old messages with a single summary HumanMessage
3. Keep N recent messages intact for immediate context

**Configuration**:
```yaml
summarization:
  enabled: true
  model_name: gpt-4o-mini
  trigger:
    - type: tokens
      value: 6000
    - type: messages
      value: 75
  keep:
    type: messages
    value: 25
```

### 5.2 DanglingToolCallMiddleware

**Purpose**: Fix broken message history from interrupted tool calls.

**Hook**: `wrap_model_call`

**Problem**: When a user cancels a request mid-execution, AIMessages with `tool_calls` may exist without corresponding ToolMessages. This causes LLM API errors.

**Solution**:
```python
def _build_patched_messages(self, messages):
    # 1. Collect all existing ToolMessage IDs
    existing_ids = {msg.tool_call_id for msg in messages if isinstance(msg, ToolMessage)}
    
    # 2. For each AIMessage with tool_calls:
    for msg in messages:
        for tc in msg.tool_calls:
            if tc["id"] not in existing_ids:
                # Insert synthetic error ToolMessage IMMEDIATELY AFTER the AIMessage
                patched.append(ToolMessage(
                    content="[Tool call was interrupted and did not return a result.]",
                    tool_call_id=tc["id"],
                    status="error",
                ))
```

**Key Detail**: Patches are inserted at the correct position (after the AIMessage), not appended to the end. This is why it uses `wrap_model_call` instead of `before_model`.

### 5.3 DeferredToolFilterMiddleware

**Purpose**: Remove deferred tool schemas from model binding to save tokens.

**Hook**: `wrap_model_call`

**Behavior**:
```python
def _filter_tools(self, request):
    registry = get_deferred_registry()
    deferred_names = {e.name for e in registry.entries}
    active_tools = [t for t in request.tools if t.name not in deferred_names]
    return request.override(tools=active_tools)
```

**Key Detail**: After `tool_search` promotes a tool, it's removed from the deferred registry, so this filter no longer strips it. The tool becomes "active" and its schema is included in subsequent `bind_tools` calls.

### 5.4 ViewImageMiddleware

**Purpose**: Inject base64 image data for vision-capable models.

**Hook**: `before_agent`

**Behavior**:
1. Checks `state.viewed_images` for pending image data
2. Converts image data to multimodal content blocks
3. Injects into the message for the LLM to process
4. Clears `viewed_images` after processing

---

## 6. Safety & Error Handling Middlewares

### 6.1 ToolErrorHandlingMiddleware

**Purpose**: Convert tool exceptions into error ToolMessages.

**Hook**: `wrap_tool_call`

**Behavior**:
```python
def wrap_tool_call(self, request, handler):
    try:
        return handler(request)
    except GraphBubbleUp:
        raise  # Preserve LangGraph control flow
    except Exception as exc:
        return ToolMessage(
            content=f"Error: Tool '{tool_name}' failed with {exc.__class__.__name__}: {detail}. "
                    f"Continue with available context, or choose an alternative tool.",
            status="error",
        )
```

**Design decisions**:
- Error messages are truncated to 500 chars
- `GraphBubbleUp` exceptions pass through (interrupt/pause/resume)
- Error message includes actionable guidance for the agent

### 6.2 LoopDetectionMiddleware

**Purpose**: Detect and break repetitive tool call loops.

**Hook**: `after_model`

**Algorithm**:
1. Hash tool calls (name + args, order-independent) using MD5
2. Track hashes in a per-thread sliding window (default: 20 entries)
3. Count occurrences of the current hash
4. At `warn_threshold` (default: 3): Inject warning HumanMessage
5. At `hard_limit` (default: 5): Strip all tool_calls, force text output

**Warning message**:
```
[LOOP DETECTED] You are repeating the same tool calls. Stop calling tools 
and produce your final answer now. If you cannot complete the task, summarize 
what you accomplished so far.
```

**Hard stop**: Modifies the AIMessage to remove `tool_calls` and append the stop message to content, forcing the agent to produce a final text response.

**Thread safety**: Uses `threading.Lock` for the shared history dict. Uses `OrderedDict` with LRU eviction (default: 100 threads max).

### 6.3 SubagentLimitMiddleware

**Purpose**: Enforce maximum concurrent sub-agent tasks.

**Hook**: `after_model`

**Behavior**:
```python
def after_model(self, state, runtime):
    last_msg = state["messages"][-1]
    task_calls = [tc for tc in last_msg.tool_calls if tc["name"] == "task"]
    
    if len(task_calls) > self.max_concurrent:
        # Keep only max_concurrent task calls
        kept_calls = last_msg.tool_calls[:self.max_concurrent]
        return {"messages": [last_msg.model_copy(update={"tool_calls": kept_calls})]}
```

**Key Detail**: Silently truncates excess calls. The prompt instructs the agent to use multi-batch execution for tasks exceeding the limit.

### 6.4 SandboxAuditMiddleware

**Purpose**: Log tool invocations for audit trail.

**Hook**: `wrap_tool_call`

**Behavior**: Logs tool name, arguments, and execution time for security auditing.

### 6.5 GuardrailMiddleware (Optional)

**Purpose**: Check tool calls against safety rules.

**Hook**: `wrap_tool_call`

**Behavior**: Delegates to a configurable guardrail provider. Can block dangerous tool calls.

---

## 7. Feature Middlewares

### 7.1 TitleMiddleware

**Purpose**: Auto-generate conversation title after first exchange.

**Hook**: `after_model`

**Trigger conditions**:
- Title generation is enabled in config
- Thread doesn't already have a title
- First complete exchange (1 user message + 1 assistant response)

**Behavior**:
1. Extract first user message and first assistant response (truncated to 500 chars each)
2. Format into a title generation prompt
3. Call a lightweight LLM (configurable model)
4. Parse and clean the title (strip quotes, enforce max chars)
5. Fallback: truncate user message if LLM fails

**Key Detail**: Uses a separate, lightweight model for title generation to avoid consuming the main model's context.

### 7.2 MemoryMiddleware

**Purpose**: Queue conversation for long-term memory update.

**Hook**: `after_agent`

**Behavior**:
1. Filter messages to keep only user inputs and final assistant responses
2. Strip `<uploaded_files>` blocks (ephemeral, session-scoped)
3. Skip AI messages with tool_calls (intermediate steps)
4. Queue filtered messages to `MemoryQueue` with debouncing
5. Memory update happens asynchronously via LLM summarization

**Message filtering logic**:
```python
def _filter_messages_for_memory(messages):
    for msg in messages:
        if msg.type == "human":
            # Strip <uploaded_files> block, keep user's question
            # Skip if nothing remains after stripping
            filtered.append(clean_msg)
        elif msg.type == "ai":
            if not msg.tool_calls:  # Only final responses
                filtered.append(msg)
        # Skip: tool messages, AI messages with tool_calls
```

### 7.3 TodoMiddleware

**Purpose**: Task tracking for plan mode.

**Hooks**: `before_model` (inject reminder), tool injection (`write_todos`)

**Context-loss detection**:
When `SummarizationMiddleware` compresses old messages, the original `write_todos` tool call may be lost. TodoMiddleware detects this and injects a reminder:

```python
def before_model(self, state, runtime):
    todos = state.get("todos") or []
    if not todos:
        return None
    
    messages = state.get("messages") or []
    if _todos_in_messages(messages):
        return None  # write_todos still visible
    if _reminder_in_messages(messages):
        return None  # Reminder already injected
    
    # Inject reminder
    return {"messages": [HumanMessage(
        name="todo_reminder",
        content="<system_reminder>\nYour todo list from earlier...\n</system_reminder>"
    )]}
```

### 7.4 TokenUsageMiddleware

**Purpose**: Track token consumption per model call.

**Hook**: `after_model`

**Behavior**: Extracts token usage from the LLM response metadata and logs it.

### 7.5 ClarificationMiddleware

**Purpose**: Intercept clarification requests and interrupt execution.

**Hook**: `wrap_tool_call` (ALWAYS LAST in the chain)

**Behavior**:
```python
def wrap_tool_call(self, request, handler):
    if request.tool_call.get("name") != "ask_clarification":
        return handler(request)  # Not a clarification, pass through
    
    # Format the clarification message with icons and options
    formatted = self._format_clarification_message(args)
    
    # Return a Command that interrupts execution
    return Command(
        update={"messages": [ToolMessage(content=formatted, name="ask_clarification")]},
        goto=END,  # Stop the agent loop
    )
```

**Clarification types and icons**:
| Type | Icon | Example |
|------|------|---------|
| `missing_info` | ❓ | "Which database are you using?" |
| `ambiguous_requirement` | 🤔 | "Do you mean X or Y?" |
| `approach_choice` | 🔀 | "Should I use approach A or B?" |
| `risk_confirmation` | ⚠️ | "This will delete all data. Proceed?" |
| `suggestion` | 💡 | "I suggest using TypeScript instead." |

**Key Detail**: Uses `Command(goto=END)` to interrupt the LangGraph execution, returning control to the user. The conversation resumes when the user responds.

---

## 8. Custom Middleware Insertion (@Next/@Prev)

### 8.1 The Positioning System

DeerFlow provides `@Next` and `@Prev` decorators for precise middleware positioning:

```python
from langchain.agents.middleware import Next, Prev

@Next(ToolErrorHandlingMiddleware)
class MyMiddleware(AgentMiddleware):
    """Inserted immediately AFTER ToolErrorHandlingMiddleware."""
    pass

@Prev(MemoryMiddleware)
class AnotherMiddleware(AgentMiddleware):
    """Inserted immediately BEFORE MemoryMiddleware."""
    pass
```

### 8.2 Insertion Algorithm

```python
def _insert_extra(chain, extras):
    # 1. Validate: no middleware has both @Next and @Prev
    # 2. Conflict detection: two extras targeting same anchor → error
    # 3. Unanchored extras → insert before ClarificationMiddleware
    # 4. Anchored extras → iterative insertion (supports cross-external anchoring)
    # 5. Invariant: ClarificationMiddleware always stays last
```

### 8.3 Cross-External Anchoring

Custom middlewares can anchor to each other:

```python
@Next(ToolErrorHandlingMiddleware)
class MiddlewareA(AgentMiddleware):
    pass

@Next(MiddlewareA)  # Anchors to another custom middleware
class MiddlewareB(AgentMiddleware):
    pass
```

The algorithm resolves these iteratively, detecting circular dependencies.

### 8.4 Constraints

- A middleware cannot have both `@Next` and `@Prev`
- Two middlewares cannot target the same anchor in the same direction
- Circular dependencies are detected and raise `ValueError`
- `ClarificationMiddleware` is always repositioned to the end after insertion

---

## 9. Feature Flags (RuntimeFeatures)

### 9.1 The RuntimeFeatures Dataclass

```python
@dataclass
class RuntimeFeatures:
    sandbox: bool | AgentMiddleware = True
    guardrail: bool | AgentMiddleware = False
    summarization: bool | AgentMiddleware = False
    auto_title: bool | AgentMiddleware = True
    memory: bool | AgentMiddleware = True
    vision: bool | AgentMiddleware = False
    subagent: bool | AgentMiddleware = False
```

### 9.2 Feature Value Semantics

| Value | Meaning |
|-------|---------|
| `False` | Feature disabled, middleware skipped |
| `True` | Feature enabled with built-in default middleware |
| `AgentMiddleware` instance | Feature enabled with custom middleware replacement |

### 9.3 Usage in SDK

```python
from deerflow.agents.factory import create_deerflow_agent
from deerflow.agents.features import RuntimeFeatures

agent = create_deerflow_agent(
    model=my_model,
    tools=my_tools,
    features=RuntimeFeatures(
        sandbox=True,
        memory=True,
        vision=True,
        subagent=True,
        summarization=my_custom_summarization_middleware,
    ),
)
```

---

## 10. Middleware State Schemas

### 10.1 State Schema Compatibility

Each middleware declares a `state_schema` that must be compatible with `ThreadState`:

```python
class MemoryMiddlewareState(AgentState):
    pass  # Compatible with ThreadState

class TitleMiddlewareState(AgentState):
    title: NotRequired[str | None]

class SandboxMiddlewareState(AgentState):
    sandbox: NotRequired[SandboxState | None]
    thread_data: NotRequired[ThreadDataState | None]

class UploadsMiddlewareState(AgentState):
    uploaded_files: NotRequired[list[dict] | None]
```

### 10.2 State Reducers

Some state fields use custom reducers for merge semantics:

```python
class ThreadState(AgentState):
    artifacts: Annotated[list[str], merge_artifacts]      # Deduplicated merge
    viewed_images: Annotated[dict, merge_viewed_images]   # Dict merge, empty={} clears
```

---

## 11. Lead Agent vs Sub-Agent Middleware Chains

### 11.1 Lead Agent Chain

Built by `_build_middlewares()` in `lead_agent/agent.py`:

```
ThreadDataMiddleware → UploadsMiddleware → SandboxMiddleware →
DanglingToolCallMiddleware → GuardrailMiddleware* → SandboxAuditMiddleware →
ToolErrorHandlingMiddleware → SummarizationMiddleware → TodoMiddleware* →
TokenUsageMiddleware → TitleMiddleware → MemoryMiddleware →
ViewImageMiddleware* → DeferredToolFilterMiddleware* →
SubagentLimitMiddleware* → LoopDetectionMiddleware → ClarificationMiddleware
```

### 11.2 Sub-Agent Chain

Built by `build_subagent_runtime_middlewares()`:

```
ThreadDataMiddleware → SandboxMiddleware →
GuardrailMiddleware* → SandboxAuditMiddleware →
ToolErrorHandlingMiddleware
```

**Key differences from lead agent**:
- No `UploadsMiddleware` (sub-agents don't handle uploads)
- No `DanglingToolCallMiddleware` (sub-agents start fresh)
- No `SummarizationMiddleware` (sub-agents have short contexts)
- No `TodoMiddleware` (sub-agents don't track todos)
- No `TitleMiddleware` (sub-agents don't generate titles)
- No `MemoryMiddleware` (sub-agents don't update memory)
- No `DeferredToolFilterMiddleware` (sub-agents get direct tool access)
- No `SubagentLimitMiddleware` (sub-agents can't spawn sub-agents)
- No `LoopDetectionMiddleware` (sub-agents have timeout limits)
- No `ClarificationMiddleware` (sub-agents can't ask users)

---

## 12. Summary

DeerFlow's middleware system is the architectural backbone that enables:

1. **Clean separation of concerns** — Each middleware handles exactly one aspect of agent behavior
2. **Flexible composition** — Feature flags and `@Next`/`@Prev` positioning allow precise customization
3. **Robust error handling** — Multiple layers of error recovery prevent agent loop crashes
4. **Context management** — Summarization, deferred tools, and progressive loading manage token budgets
5. **Safety guarantees** — Loop detection, subagent limits, and guardrails prevent runaway execution
6. **Lifecycle management** — Sandbox, memory, and title generation are handled transparently

The middleware chain is the primary extension point for DeerFlow — new capabilities are added by creating new middlewares, not by modifying the core agent loop.
