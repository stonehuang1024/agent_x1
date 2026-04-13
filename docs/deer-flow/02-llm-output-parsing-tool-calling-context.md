# DeerFlow 2.0 — LLM Output, Parsing, Tool Calling & Context Engineering

> **Document Scope**: This document provides a deep dive into four critical areas: (1) how DeerFlow builds context for LLM prompts, (2) LLM output formats across different modes, (3) output parsing and tool call execution, and (4) parallel tool calling support.

---

## Table of Contents

1. [Context Engineering — What Goes Into the Prompt](#1-context-engineering--what-goes-into-the-prompt)
2. [Code Retrieval & Indexing](#2-code-retrieval--indexing)
3. [Deferred Tool Discovery — tool_search](#3-deferred-tool-discovery--tool_search)
4. [LLM Output Formats](#4-llm-output-formats)
5. [Output Parsing & Message Processing](#5-output-parsing--message-processing)
6. [Tool Call Execution Pipeline](#6-tool-call-execution-pipeline)
7. [Parallel Tool Calling](#7-parallel-tool-calling)
8. [Error Handling & Recovery](#8-error-handling--recovery)
9. [Loop Detection & Safety](#9-loop-detection--safety)
10. [Streaming Output Pipeline](#10-streaming-output-pipeline)
11. [Frontend Message Rendering](#11-frontend-message-rendering)
12. [Key Technical Innovations](#12-key-technical-innovations)
13. [Summary](#13-summary)

---

## 1. Context Engineering — What Goes Into the Prompt

### 1.1 The Context Assembly Pipeline

DeerFlow's context is not a static prompt — it is dynamically assembled through a multi-stage pipeline involving the system prompt template, middleware chain, and runtime state. Here is the complete picture of what ends up in the LLM's context window:

```
┌─────────────────────────────────────────────────────────────────┐
│                    SYSTEM PROMPT (assembled once)                │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ <role> Agent identity and capabilities </role>            │   │
│  ├──────────────────────────────────────────────────────────┤   │
│  │ <soul> Custom personality from SOUL.md (if configured)    │   │
│  ├──────────────────────────────────────────────────────────┤   │
│  │ <memory> Long-term memory injection (facts, context)      │   │
│  ├──────────────────────────────────────────────────────────┤   │
│  │ <thinking_style> Reasoning guidelines                     │   │
│  ├──────────────────────────────────────────────────────────┤   │
│  │ <clarification_system> When/how to ask for clarification  │   │
│  ├──────────────────────────────────────────────────────────┤   │
│  │ <skill_system> Available skills (name + description only) │   │
│  ├──────────────────────────────────────────────────────────┤   │
│  │ <available-deferred-tools> Deferred tool names            │   │
│  ├──────────────────────────────────────────────────────────┤   │
│  │ <subagent_system> Task decomposition instructions         │   │
│  ├──────────────────────────────────────────────────────────┤   │
│  │ <working_directory> File system layout                    │   │
│  ├──────────────────────────────────────────────────────────┤   │
│  │ <response_style> Output formatting guidelines             │   │
│  ├──────────────────────────────────────────────────────────┤   │
│  │ <citations> Citation format for research tasks            │   │
│  ├──────────────────────────────────────────────────────────┤   │
│  │ <critical_reminders> Key behavioral rules                 │   │
│  ├──────────────────────────────────────────────────────────┤   │
│  │ <current_date> Current date and day of week               │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                    TOOL SCHEMAS (bind_tools)                     │
│                                                                  │
│  Active tools: bash, read_file, write_file, str_replace, ls,    │
│  web_search, web_fetch, present_files, ask_clarification,       │
│  view_image, task, tool_search                                   │
│  (Deferred tools are EXCLUDED until searched)                    │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                    MESSAGE HISTORY                                │
│                                                                  │
│  [Summarization message] (if context was compressed)             │
│  [HumanMessage] User's earlier messages                          │
│  [AIMessage] Agent's earlier responses                           │
│  [ToolMessage] Tool results from earlier turns                   │
│  ...                                                             │
│  [HumanMessage] Current user message                             │
│    ├── <uploaded_files> block (injected by UploadsMiddleware)     │
│    └── User's actual question                                    │
│                                                                  │
│  [AIMessage with tool_calls] (if in tool loop)                   │
│  [ToolMessage] Tool results                                      │
│  ...                                                             │
└──────────────────────────────────────────────────────────────────┘
```

### 1.2 Middleware-Driven Context Injection

Each middleware can modify the context before it reaches the LLM:

| Middleware | What It Injects | When |
|-----------|----------------|------|
| `UploadsMiddleware` | `<uploaded_files>` block prepended to last HumanMessage | `before_agent` |
| `SummarizationMiddleware` | Replaces old messages with a summary HumanMessage | `before_agent` |
| `ViewImageMiddleware` | Base64 image data as multimodal content blocks | `before_agent` |
| `DanglingToolCallMiddleware` | Synthetic error ToolMessages for interrupted tool calls | `wrap_model_call` |
| `DeferredToolFilterMiddleware` | Removes deferred tool schemas from `request.tools` | `wrap_model_call` |
| `LoopDetectionMiddleware` | Warning HumanMessage when loops detected | `after_model` |
| `TodoMiddleware` | Todo system prompt + `write_todos` tool | `before_agent` |

### 1.3 Context Decision Logic

**What gets included in context and why:**

1. **System Prompt** — Always included. Assembled once at agent creation time. Contains:
   - Agent identity and personality
   - Memory context (long-term facts and summaries)
   - Available skills (names only, not full content)
   - Deferred tool names (not schemas)
   - Working directory layout
   - Behavioral guidelines

2. **Tool Schemas** — Included via `bind_tools()`. Only **active** tools are bound:
   - Configured tools (from `config.yaml`)
   - Built-in tools (present_files, ask_clarification, etc.)
   - **Promoted** MCP tools (after `tool_search` discovers them)
   - Deferred tools are **excluded** until searched

3. **Message History** — Full conversation history, subject to:
   - **Summarization**: Old messages compressed when token/message limits hit
   - **Dangling tool call patching**: Missing ToolMessages filled with error placeholders
   - **Upload injection**: File metadata prepended to user messages

4. **User Operations** — Injected as structured blocks:
   - File uploads → `<uploaded_files>` block in HumanMessage
   - Image viewing → Base64 content blocks in message
   - Todo updates → Todo state in system prompt extension

### 1.4 Token Budget Management

DeerFlow uses multiple strategies to manage the context window:

```
┌─────────────────────────────────────────────────────────────┐
│                    Token Budget Strategies                    │
│                                                              │
│  1. PROGRESSIVE SKILL LOADING                                │
│     Skills listed by name only (~50 tokens each)             │
│     Full content loaded on-demand via read_file              │
│     Saves: 1000-5000 tokens per skill                        │
│                                                              │
│  2. DEFERRED TOOL SCHEMAS                                    │
│     MCP tools listed by name only (~10 tokens each)          │
│     Full schema loaded via tool_search                       │
│     Saves: 200-500 tokens per tool                           │
│                                                              │
│  3. CONTEXT SUMMARIZATION                                    │
│     Triggered by: token count, message count, or fraction    │
│     Older messages → LLM summary → single HumanMessage       │
│     Keeps N recent messages intact                           │
│     Saves: 50-90% of old message tokens                      │
│                                                              │
│  4. MEMORY TOKEN BUDGET                                      │
│     Facts ranked by confidence                               │
│     Added incrementally until max_injection_tokens reached    │
│     Saves: Prevents unbounded memory growth                  │
│                                                              │
│  5. SUB-AGENT CONTEXT ISOLATION                              │
│     Each sub-agent starts fresh (no parent history)          │
│     Only receives task description + tools                   │
│     Saves: Entire parent conversation context                │
│                                                              │
│  6. FILE OFFLOADING                                          │
│     Intermediate results written to filesystem               │
│     Referenced by path, not included in context              │
│     Saves: Arbitrary amounts of generated content            │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Code Retrieval & Indexing

### 2.1 DeerFlow's Approach: No Code Index

DeerFlow does **not** build a code index or use embedding-based retrieval for code. Instead, it relies on:

1. **Sandbox tools** (`read_file`, `ls`, `bash`) for direct filesystem access
2. **Skills** for domain-specific workflows and references
3. **Progressive exploration** — the agent navigates the filesystem like a human developer

### 2.2 How the Agent Locates Code

The agent uses a **manual exploration pattern**:

```
1. ls /mnt/user-data/workspace/          → See project structure
2. read_file /mnt/user-data/workspace/package.json  → Understand dependencies
3. bash "grep -r 'function_name' /mnt/user-data/workspace/src/"  → Find references
4. read_file /mnt/user-data/workspace/src/target.ts  → Read specific file
5. str_replace ... → Make targeted edits
```

### 2.3 Skills as Domain Knowledge

Skills serve as pre-indexed domain knowledge:
- A skill's `SKILL.md` contains workflow instructions and references
- Reference files under the skill directory provide templates, examples, and methodology
- The agent loads these on-demand when a task matches a skill's description

### 2.4 MCP-Based Code Tools

For more sophisticated code operations, DeerFlow supports MCP servers that can provide:
- Code search tools (via filesystem MCP server)
- Git operations
- IDE-like features (go-to-definition, find-references)

These are configured in `extensions_config.json` and loaded as MCP tools.

---

## 3. Deferred Tool Discovery — tool_search

### 3.1 The Problem

When many MCP servers are configured, each exposing multiple tools, the total tool schema size can consume thousands of tokens. This is wasteful because the agent rarely needs all tools in a single conversation.

### 3.2 The Solution: Deferred Tool Loading

DeerFlow implements a **two-phase tool loading** pattern:

**Phase 1 — Registration (at agent creation)**:
```python
# In get_available_tools():
registry = DeferredToolRegistry()
for tool in mcp_tools:
    registry.register(tool)  # Store full tool object
set_deferred_registry(registry)

# System prompt includes:
# <available-deferred-tools>
# tool_name_1, tool_name_2, tool_name_3, ...
# </available-deferred-tools>
```

**Phase 2 — Discovery (at runtime)**:
```python
# Agent calls tool_search("select:tool_name_1,tool_name_2")
# or tool_search("keyword query")

@tool
def tool_search(query: str) -> str:
    registry = get_deferred_registry()
    matched_tools = registry.search(query)
    
    # Convert to OpenAI function format (model-agnostic)
    tool_defs = [convert_to_openai_function(t) for t in matched_tools]
    
    # Promote: remove from deferred registry so DeferredToolFilterMiddleware
    # stops filtering them from bind_tools
    registry.promote({t.name for t in matched_tools})
    
    return json.dumps(tool_defs)
```

**Phase 3 — Invocation (subsequent LLM calls)**:
```
DeferredToolFilterMiddleware.wrap_model_call():
    # Before: request.tools = [all tools including deferred]
    # After:  request.tools = [active tools only] (deferred filtered out)
    # But promoted tools are no longer in the deferred registry,
    # so they pass through the filter and are included in bind_tools
```

### 3.3 Search Query Forms

The `tool_search` tool supports three query forms (aligned with Claude Code patterns):

| Query Form | Example | Behavior |
|-----------|---------|----------|
| `select:name1,name2` | `select:Read,Edit,Grep` | Exact name match |
| `+keyword rest` | `+slack send message` | Name must contain "slack", rank by "send message" |
| `keyword query` | `notebook jupyter` | Regex match against name + description |

### 3.4 Per-Request Isolation

The deferred registry uses `contextvars.ContextVar` for per-request isolation:
- Each async request gets its own registry instance
- Concurrent requests don't interfere with each other
- `asyncio.run_in_executor` correctly inherits the context

---

## 4. LLM Output Formats

### 4.1 Output Types

The LLM produces several types of output, depending on the situation:

#### Type 1: Text Response (Final Answer)

```json
{
  "type": "ai",
  "content": "Here is the analysis of your code...\n\n## Key Findings\n...",
  "tool_calls": [],
  "additional_kwargs": {}
}
```

#### Type 2: Tool Calls (Action Request)

```json
{
  "type": "ai",
  "content": "",
  "tool_calls": [
    {
      "id": "call_abc123",
      "name": "read_file",
      "args": {
        "description": "Reading the main configuration file",
        "path": "/mnt/user-data/workspace/config.yaml"
      }
    }
  ]
}
```

#### Type 3: Multiple Tool Calls (Parallel Actions)

```json
{
  "type": "ai",
  "content": "",
  "tool_calls": [
    {
      "id": "call_abc123",
      "name": "read_file",
      "args": {"description": "Read package.json", "path": "/mnt/user-data/workspace/package.json"}
    },
    {
      "id": "call_def456",
      "name": "read_file",
      "args": {"description": "Read tsconfig", "path": "/mnt/user-data/workspace/tsconfig.json"}
    }
  ]
}
```

#### Type 4: Thinking + Text (Extended Reasoning)

For models with thinking/reasoning support (Claude, DeepSeek):

**Anthropic format:**
```json
{
  "type": "ai",
  "content": [
    {
      "type": "thinking",
      "thinking": "Let me analyze this step by step...\n1. The user wants...\n2. I should..."
    },
    {
      "type": "text",
      "text": "Based on my analysis, here are the key findings..."
    }
  ]
}
```

**OpenAI/DeepSeek format:**
```json
{
  "type": "ai",
  "content": "Based on my analysis...",
  "additional_kwargs": {
    "reasoning_content": "Let me think about this...\n1. First...\n2. Then..."
  }
}
```

**Inline thinking (some models):**
```json
{
  "type": "ai",
  "content": "<think>Let me analyze this...</think>\n\nBased on my analysis..."
}
```

#### Type 5: Thinking + Tool Calls

```json
{
  "type": "ai",
  "content": [
    {
      "type": "thinking",
      "thinking": "I need to read the file first to understand the structure..."
    }
  ],
  "tool_calls": [
    {
      "id": "call_abc123",
      "name": "read_file",
      "args": {"description": "Read source file", "path": "/mnt/user-data/workspace/src/main.py"}
    }
  ]
}
```

#### Type 6: Sub-Agent Delegation

```json
{
  "type": "ai",
  "content": "",
  "tool_calls": [
    {
      "id": "call_task_1",
      "name": "task",
      "args": {
        "description": "Research the latest React 19 features",
        "prompt": "Search for and summarize the key new features in React 19...",
        "subagent_type": "general-purpose"
      }
    },
    {
      "id": "call_task_2",
      "name": "task",
      "args": {
        "description": "Analyze the current codebase structure",
        "prompt": "List and analyze the directory structure of the project...",
        "subagent_type": "general-purpose"
      }
    }
  ]
}
```

#### Type 7: Clarification Request

```json
{
  "type": "ai",
  "content": "",
  "tool_calls": [
    {
      "id": "call_clarify_1",
      "name": "ask_clarification",
      "args": {
        "question": "Which database are you using — PostgreSQL or MySQL?",
        "options": ["PostgreSQL", "MySQL", "Other"]
      }
    }
  ]
}
```

#### Type 8: File Presentation

```json
{
  "type": "ai",
  "content": "I've created the report. Here are the generated files:",
  "tool_calls": [
    {
      "id": "call_present_1",
      "name": "present_files",
      "args": {
        "filepaths": [
          "/mnt/user-data/outputs/report.pdf",
          "/mnt/user-data/outputs/data.csv"
        ]
      }
    }
  ]
}
```

### 4.2 Mode-Specific Output Differences

#### Standard Mode (Flash/Standard)
- Direct tool calls and text responses
- No todo tracking
- No sub-agent delegation (unless explicitly enabled)

#### Plan Mode (Pro)
- Includes `write_todos` tool calls for task tracking
- Agent creates a structured todo list before starting work
- Updates todo status (`in_progress`, `completed`) as it works
- Example todo tool call:
```json
{
  "name": "write_todos",
  "args": {
    "todos": [
      {"id": "1", "title": "Analyze requirements", "status": "completed"},
      {"id": "2", "title": "Design database schema", "status": "in_progress"},
      {"id": "3", "title": "Implement API endpoints", "status": "pending"}
    ]
  }
}
```

#### Sub-Agent Mode (Ultra)
- Includes `task` tool calls for delegation
- Lead agent decomposes work into parallel sub-tasks
- Each sub-task runs as an independent agent with its own tool calls
- Results are synthesized by the lead agent

### 4.3 Tool Result Format

Tool results are returned as `ToolMessage` objects:

```json
{
  "type": "tool",
  "tool_call_id": "call_abc123",
  "name": "read_file",
  "content": "# config.yaml\nmodel:\n  name: gpt-4\n  ...",
  "status": "success"
}
```

Error results:
```json
{
  "type": "tool",
  "tool_call_id": "call_abc123",
  "name": "bash",
  "content": "Error: Tool 'bash' failed with PermissionError: Unsafe absolute paths in command: /etc/passwd. Use paths under /mnt/user-data",
  "status": "error"
}
```

---

## 5. Output Parsing & Message Processing

### 5.1 LangChain's Built-In Parsing

DeerFlow relies on **LangChain's built-in output parsing** — it does NOT implement custom output parsers. The flow is:

```
LLM API Response → LangChain Provider (ChatOpenAI/ChatAnthropic/etc.)
                   → Parses into AIMessage object
                   → Extracts: content, tool_calls, additional_kwargs
                   → Returns structured AIMessage
```

LangChain handles:
- OpenAI function calling format → `tool_calls` list
- Anthropic tool use format → `tool_calls` list
- Thinking/reasoning content → `additional_kwargs.reasoning_content` or content blocks
- Streaming chunks → `AIMessageChunk` with partial content/tool_calls

### 5.2 Middleware Post-Processing

After the LLM produces output, middlewares process it:

```
AIMessage from LLM
    │
    ▼
LoopDetectionMiddleware.after_model()
    │ Hash tool_calls, check for repetition
    │ If warn_threshold: inject warning HumanMessage
    │ If hard_limit: strip tool_calls, force text output
    │
    ▼
SubagentLimitMiddleware.after_model()
    │ Count task tool calls
    │ If > max_concurrent_subagents: truncate excess
    │
    ▼
ClarificationMiddleware.after_model()
    │ Check for ask_clarification tool call
    │ If found: set interrupt flag for user input
    │
    ▼
(Tool execution or final response)
```

### 5.3 Serialization for Streaming

Before sending to the client, LangChain objects are serialized:

```python
# In runtime/serialization.py

def serialize(obj, *, mode=""):
    if mode == "messages":
        # (message_chunk, metadata_dict) → [serialized_chunk, metadata]
        return serialize_messages_tuple(obj)
    if mode == "values":
        # Full state dict → strip __pregel_* keys, serialize all values
        return serialize_channel_values(obj)
    return serialize_lc_object(obj)

def serialize_lc_object(obj):
    # Pydantic v2: obj.model_dump()
    # Pydantic v1: obj.dict()
    # Fallback: str(obj)
```

---

## 6. Tool Call Execution Pipeline

### 6.1 End-to-End Tool Execution Flow

```
AIMessage with tool_calls
    │
    ▼
LangGraph ToolNode
    │ For each tool_call in tool_calls:
    │
    ├─── ToolErrorHandlingMiddleware.wrap_tool_call()
    │    │ try:
    │    │     handler(request)  ← actual tool execution
    │    │ except GraphBubbleUp:
    │    │     raise  ← preserve LangGraph control flow
    │    │ except Exception as exc:
    │    │     return ToolMessage(content="Error: ...", status="error")
    │    │
    │    ▼
    │    SandboxAuditMiddleware.wrap_tool_call()
    │    │ Log tool invocation for audit trail
    │    │
    │    ▼
    │    GuardrailMiddleware.wrap_tool_call() (if configured)
    │    │ Check tool call against safety rules
    │    │
    │    ▼
    │    Actual Tool Function
    │    │
    │    ├── bash_tool(runtime, description, command)
    │    │   1. ensure_sandbox_initialized(runtime)
    │    │   2. If local: validate paths, replace virtual paths, apply cwd
    │    │   3. sandbox.execute_command(command)
    │    │   4. If local: mask host paths in output
    │    │   5. Return output string
    │    │
    │    ├── read_file_tool(runtime, description, path, start_line, end_line)
    │    │   1. ensure_sandbox_initialized(runtime)
    │    │   2. If local: validate path, resolve virtual→physical
    │    │   3. sandbox.read_file(path)
    │    │   4. Apply line range if specified
    │    │   5. Return file content
    │    │
    │    ├── task_tool(runtime, description, prompt, subagent_type)
    │    │   1. Create SubagentExecutor
    │    │   2. execute_async() → ThreadPoolExecutor
    │    │   3. Poll every 5s for completion
    │    │   4. Stream progress events
    │    │   5. Return SubagentResult
    │    │
    │    ├── tool_search(query)
    │    │   1. Get deferred registry
    │    │   2. Search by query (select/keyword/regex)
    │    │   3. Convert matched tools to OpenAI function format
    │    │   4. Promote matched tools (remove from deferred)
    │    │   5. Return JSON array of tool definitions
    │    │
    │    └── ... (other tools)
    │
    ▼
ToolMessage(s) appended to message history
    │
    ▼
LangGraph routes back to model node (loop continues)
```

### 6.2 Sandbox Tool Execution Details

For sandbox tools (`bash`, `read_file`, `write_file`, `str_replace`, `ls`), the execution path depends on the sandbox type:

**Local Sandbox:**
```
Tool called → ensure_sandbox_initialized()
           → validate_local_tool_path() (security check)
           → replace_virtual_path() (/mnt/user-data → physical path)
           → sandbox.execute_command() / sandbox.read_file() / etc.
           → mask_local_paths_in_output() (physical → virtual in output)
           → Return result
```

**Docker (AioSandbox):**
```
Tool called → ensure_sandbox_initialized()
           → sandbox.execute_command() (runs inside container)
           → Return result (paths already virtual inside container)
```

### 6.3 Virtual Path Resolution

The path resolution system maps virtual paths to physical paths:

```python
def replace_virtual_path(path, thread_data):
    mappings = {
        "/mnt/user-data/workspace": thread_data["workspace_path"],
        "/mnt/user-data/uploads":   thread_data["uploads_path"],
        "/mnt/user-data/outputs":   thread_data["outputs_path"],
    }
    # Longest-prefix-first matching
    for virtual_base, actual_base in sorted(mappings.items(), key=len, reverse=True):
        if path.startswith(f"{virtual_base}/"):
            return actual_base + path[len(virtual_base):]
    return path
```

And the reverse for output masking:
```python
def mask_local_paths_in_output(output, thread_data):
    # Replace physical paths with virtual paths in tool output
    # Handles: user-data paths, skills paths, ACP workspace paths
    # Uses regex for robust matching across path styles
```

---

## 7. Parallel Tool Calling

### 7.1 LangGraph's Native Parallel Execution

DeerFlow supports parallel tool calling through LangGraph's `ToolNode`, which executes all tool calls from a single AIMessage concurrently:

```
AIMessage with tool_calls: [call_1, call_2, call_3]
    │
    ▼
ToolNode processes ALL calls:
    ├── call_1 → tool_function_1() ──┐
    ├── call_2 → tool_function_2() ──┤ (concurrent execution)
    └── call_3 → tool_function_3() ──┘
                                      │
                                      ▼
                              [ToolMessage_1, ToolMessage_2, ToolMessage_3]
                                      │
                                      ▼
                              All appended to message history
                                      │
                                      ▼
                              LLM called with all results
```

### 7.2 When Parallel Calls Happen

The LLM decides when to make parallel tool calls. Common patterns:

1. **Multiple file reads**: Reading several files simultaneously
2. **Multiple sub-agent tasks**: Delegating parallel research tasks
3. **Search + read**: Searching and reading in parallel
4. **Multiple bash commands**: Running independent commands

### 7.3 Sub-Agent Parallel Execution

Sub-agents are the primary parallel execution mechanism:

```python
# In task_tool:
class SubagentExecutor:
    _scheduler_pool = ThreadPoolExecutor(max_workers=8)
    _execution_pool = ThreadPoolExecutor(max_workers=8)
    
    def execute_async(self):
        # Submit to scheduler pool
        self._scheduler_pool.submit(self._run_task)
    
    def _run_task(self):
        # Submit actual execution to execution pool
        future = self._execution_pool.submit(self.execute)
        result = future.result(timeout=self._timeout)
```

The `SubagentLimitMiddleware` enforces concurrency limits:

```python
class SubagentLimitMiddleware:
    def after_model(self, state, runtime):
        messages = state.get("messages", [])
        last_msg = messages[-1]
        task_calls = [tc for tc in last_msg.tool_calls if tc["name"] == "task"]
        
        if len(task_calls) > max_concurrent:
            # Truncate excess task calls
            truncated = last_msg.model_copy(
                update={"tool_calls": last_msg.tool_calls[:max_concurrent]}
            )
            return {"messages": [truncated]}
```

### 7.4 Middleware Wrapping for Parallel Calls

Each tool call in a parallel batch goes through the middleware chain independently:

```
Parallel tool calls: [call_1, call_2, call_3]
    │
    ├── call_1 → ToolErrorHandling → SandboxAudit → Guardrail → tool_func_1()
    ├── call_2 → ToolErrorHandling → SandboxAudit → Guardrail → tool_func_2()
    └── call_3 → ToolErrorHandling → SandboxAudit → Guardrail → tool_func_3()
```

If one tool fails, the error is captured as an error ToolMessage, and the other tools continue normally.

---

## 8. Error Handling & Recovery

### 8.1 ToolErrorHandlingMiddleware

This is the primary error recovery mechanism:

```python
class ToolErrorHandlingMiddleware:
    def wrap_tool_call(self, request, handler):
        try:
            return handler(request)
        except GraphBubbleUp:
            raise  # Preserve LangGraph control flow (interrupt/pause)
        except Exception as exc:
            # Convert to error ToolMessage
            detail = str(exc)[:497] + "..." if len(str(exc)) > 500 else str(exc)
            return ToolMessage(
                content=f"Error: Tool '{tool_name}' failed with {exc.__class__.__name__}: {detail}. "
                        f"Continue with available context, or choose an alternative tool.",
                status="error",
            )
```

Key design decisions:
- **Never crashes the agent loop** — all exceptions become error messages
- **Preserves LangGraph control flow** — `GraphBubbleUp` exceptions pass through
- **Truncates long errors** — Max 500 chars to avoid context pollution
- **Suggests alternatives** — Error message encourages the agent to try different approaches

### 8.2 DanglingToolCallMiddleware

Handles interrupted tool calls (e.g., user cancellation):

```python
class DanglingToolCallMiddleware:
    def wrap_model_call(self, request, handler):
        # Scan message history for AIMessages with tool_calls
        # that have no corresponding ToolMessages
        patched = self._build_patched_messages(request.messages)
        if patched:
            request = request.override(messages=patched)
        return handler(request)
    
    def _build_patched_messages(self, messages):
        # For each dangling tool_call, insert:
        # ToolMessage(content="[Tool call was interrupted...]", status="error")
        # Inserted IMMEDIATELY AFTER the AIMessage (correct ordering)
```

### 8.3 Sandbox Error Handling

Sandbox tools have layered error handling:

```python
@tool("bash")
def bash_tool(runtime, description, command):
    try:
        sandbox = ensure_sandbox_initialized(runtime)
        # ... execution ...
    except SandboxError as e:
        return f"Error: {e}"
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Unexpected error: {_sanitize_error(e, runtime)}"
```

The `_sanitize_error()` function masks host filesystem paths in error messages.

---

## 9. Loop Detection & Safety

### 9.1 Detection Algorithm

```python
class LoopDetectionMiddleware:
    def after_model(self, state, runtime):
        # 1. Hash the tool calls (name + args, order-independent)
        call_hash = _hash_tool_calls(tool_calls)
        
        # 2. Track in sliding window (per-thread)
        history.append(call_hash)
        if len(history) > window_size:
            history = history[-window_size:]
        
        # 3. Count occurrences
        count = history.count(call_hash)
        
        # 4. Warn at threshold (3 by default)
        if count >= warn_threshold:
            return {"messages": [HumanMessage(content=WARNING_MSG)]}
        
        # 5. Hard stop at limit (5 by default)
        if count >= hard_limit:
            # Strip tool_calls, force text output
            stripped = last_msg.model_copy(update={"tool_calls": []})
            return {"messages": [stripped]}
```

### 9.2 Hash Function

Tool calls are hashed deterministically:
```python
def _hash_tool_calls(tool_calls):
    normalized = [{"name": tc["name"], "args": tc["args"]} for tc in tool_calls]
    normalized.sort(key=lambda tc: (tc["name"], json.dumps(tc["args"], sort_keys=True)))
    return hashlib.md5(json.dumps(normalized).encode()).hexdigest()[:12]
```

This is **order-independent** — the same set of tool calls always produces the same hash.

### 9.3 Per-Thread Tracking with LRU Eviction

```python
self._history: OrderedDict[str, list[str]]  # thread_id → [hash, hash, ...]
self._warned: dict[str, set[str]]           # thread_id → {warned_hashes}

# LRU eviction when max_tracked_threads exceeded
while len(self._history) > self.max_tracked_threads:
    self._history.popitem(last=False)  # Remove oldest
```

---

## 10. Streaming Output Pipeline

### 10.1 End-to-End Streaming Architecture

```
Agent.astream(stream_mode=["values", "messages"])
    │
    ▼
StreamBridge.publish(run_id, event_type, data)
    │ (async pub/sub with per-run queues)
    │
    ▼
SSE Consumer (async generator)
    │ Formats as SSE frames:
    │   event: values
    │   data: {"messages": [...], "title": "...", "artifacts": [...]}
    │
    │   event: messages
    │   data: [{"type": "AIMessageChunk", "content": "Hello"}, {}]
    │
    ▼
Nginx (reverse proxy)
    │
    ▼
Frontend useStream hook
    │ Parses SSE events
    │ Updates React state
    │ Renders incrementally
```

### 10.2 Stream Modes

| Mode | Content | Use Case |
|------|---------|----------|
| `values` | Full state snapshot (messages, title, artifacts, todos) | State synchronization |
| `messages` | Per-message chunks (AI text, tool calls, tool results) | Real-time display |
| `updates` | Node-level state writes | Debugging |
| `checkpoints` | Checkpoint metadata | State inspection |
| `debug` | Detailed execution trace | Development |
| `tasks` | Task execution status | Progress tracking |
| `custom` | Custom events from tools | Extension point |

### 10.3 SSE Frame Format

```
event: values
data: {"messages": [...], "title": "Analysis Report", "artifacts": ["/mnt/user-data/outputs/report.pdf"]}

event: messages
data: [{"type": "AIMessageChunk", "content": "Based on", "id": "run-abc-0"}, {"tags": ["seq:step:1"]}]

event: messages
data: [{"type": "AIMessageChunk", "content": " my analysis", "id": "run-abc-0"}, {"tags": ["seq:step:1"]}]

event: end
data: null
```

---

## 11. Frontend Message Rendering

### 11.1 Message Grouping

The frontend groups messages into semantic groups for rendering:

```typescript
type MessageGroup =
  | HumanMessageGroup           // User messages
  | AssistantProcessingGroup    // AI thinking + tool calls (collapsible)
  | AssistantMessageGroup       // AI final text response
  | AssistantPresentFilesGroup  // File presentation
  | AssistantClarificationGroup // Clarification questions
  | AssistantSubagentGroup      // Sub-agent task delegation
```

### 11.2 Grouping Logic

```typescript
function groupMessages(messages) {
  for (const message of messages) {
    if (message.type === "human") {
      // → HumanMessageGroup
    }
    else if (message.type === "tool") {
      if (isClarificationToolMessage(message)) {
        // → Add to processing group AND create ClarificationGroup
      } else {
        // → Add to last open processing group
      }
    }
    else if (message.type === "ai") {
      if (hasPresentFiles(message)) {
        // → AssistantPresentFilesGroup
      } else if (hasSubagent(message)) {
        // → AssistantSubagentGroup
      } else if (hasReasoning(message) || hasToolCalls(message)) {
        // → AssistantProcessingGroup (accumulate consecutive)
      }
      if (hasContent(message) && !hasToolCalls(message)) {
        // → AssistantMessageGroup (final response)
      }
    }
  }
}
```

### 11.3 Reasoning Content Extraction

The frontend handles three different reasoning formats:

```typescript
function extractReasoningContentFromMessage(message) {
  // Format 1: additional_kwargs.reasoning_content (OpenAI/DeepSeek)
  if (message.additional_kwargs?.reasoning_content) {
    return message.additional_kwargs.reasoning_content;
  }
  
  // Format 2: content[0].thinking (Anthropic)
  if (Array.isArray(message.content) && message.content[0]?.thinking) {
    return message.content[0].thinking;
  }
  
  // Format 3: <think>...</think> inline tags (some models)
  if (typeof message.content === "string") {
    return splitInlineReasoning(message.content).reasoning;
  }
}
```

### 11.4 Upload File Parsing

```typescript
function parseUploadedFiles(content: string): FileInMessage[] {
  // Parse <uploaded_files>...</uploaded_files> block
  // Extract: filename, size, path
  // Format: "- filename (size)\n  Path: /path/to/file"
}

function stripUploadedFilesTag(content: string): string {
  // Remove <uploaded_files> block from display
  return content.replace(/<uploaded_files>[\s\S]*?<\/uploaded_files>/g, "").trim();
}
```

---

## 12. Key Technical Innovations

### 12.1 Deferred Tool Loading

The two-phase tool loading pattern (register → search → promote) is a significant innovation for managing large tool sets. It reduces prompt size by 80-90% for MCP-heavy configurations while maintaining full tool access.

### 12.2 Middleware-Based Architecture

The middleware chain pattern provides clean separation of concerns:
- Each middleware handles one aspect (memory, summarization, error handling, etc.)
- Middlewares can intercept at multiple points (before_agent, after_agent, wrap_model_call, wrap_tool_call)
- The `@Next`/`@Prev` positioning system allows custom middlewares to be inserted at precise locations

### 12.3 Virtual Path Abstraction

The virtual path system (`/mnt/user-data/*`) provides:
- Consistent paths across local and Docker environments
- Security boundary enforcement
- Output masking to prevent host path leakage
- Clean separation between user data, skills, and system files

### 12.4 Context-Aware Error Recovery

The error handling strategy is designed to keep the agent loop running:
- Tool errors → error ToolMessages (agent can retry or try alternatives)
- Dangling tool calls → synthetic error messages (prevents LLM format errors)
- Loop detection → warning injection → forced stop (prevents infinite loops)
- All errors include actionable guidance for the agent

### 12.5 Per-Request Tool Registry Isolation

Using `contextvars.ContextVar` for the deferred tool registry ensures:
- No cross-request contamination in async environments
- Correct context inheritance in thread pool executors
- Clean lifecycle management (set → use → reset)

---

## 13. Summary

DeerFlow's approach to LLM interaction is characterized by:

1. **Dynamic context assembly** — The prompt is not static; it's built from templates, middleware injections, memory, skills, and runtime state
2. **Progressive information loading** — Skills, tools, and file content are loaded on-demand to manage context window pressure
3. **Standard output parsing** — Relies on LangChain's built-in parsing rather than custom parsers, ensuring compatibility across LLM providers
4. **Robust error recovery** — Multiple layers of error handling ensure the agent loop never crashes
5. **Native parallel execution** — LangGraph's ToolNode executes all tool calls from a single response concurrently
6. **Safety mechanisms** — Loop detection, subagent limits, and path validation prevent runaway execution
7. **Transparent streaming** — Multi-mode SSE streaming provides real-time visibility into agent execution
8. **Frontend-aware message processing** — Messages are grouped and rendered based on semantic type, with support for thinking, tool calls, clarifications, and file presentations
