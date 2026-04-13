# DeerFlow 2.0 — Memory, Skills, Sandbox & SDK Deep Dive

> **Document Scope**: This document provides detailed analysis of four key subsystems: (1) the long-term memory system, (2) the skills framework, (3) the sandbox execution environment, and (4) the embedded Python SDK.

---

## Table of Contents

1. [Memory System Architecture](#1-memory-system-architecture)
2. [Memory Update Pipeline](#2-memory-update-pipeline)
3. [Memory Storage & Persistence](#3-memory-storage--persistence)
4. [Memory Injection into Prompts](#4-memory-injection-into-prompts)
5. [Skills System Architecture](#5-skills-system-architecture)
6. [Skill Discovery & Loading](#6-skill-discovery--loading)
7. [Skill File Format (SKILL.md)](#7-skill-file-format-skillmd)
8. [Progressive Skill Loading Pattern](#8-progressive-skill-loading-pattern)
9. [Sandbox Architecture](#9-sandbox-architecture)
10. [Sandbox Tools Implementation](#10-sandbox-tools-implementation)
11. [Virtual Path System](#11-virtual-path-system)
12. [Security Model](#12-security-model)
13. [Embedded Python SDK (DeerFlowClient)](#13-embedded-python-sdk-deerflowclient)
14. [SDK Architecture & Internals](#14-sdk-architecture--internals)
15. [Gateway API Layer](#15-gateway-api-layer)
16. [Summary](#16-summary)

---

## 1. Memory System Architecture

### 1.1 Overview

DeerFlow's memory system provides **persistent long-term memory** across conversation sessions. Unlike simple chat history (which is per-thread), memory captures distilled knowledge about the user, their preferences, and important facts.

### 1.2 Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                    Memory System Architecture                     │
│                                                                   │
│  Conversation                                                     │
│      │                                                            │
│      ▼                                                            │
│  MemoryMiddleware (after_agent)                                   │
│      │ Filter: keep only user inputs + final AI responses         │
│      │ Strip: <uploaded_files> blocks                             │
│      │ Skip: tool messages, AI messages with tool_calls           │
│      │                                                            │
│      ▼                                                            │
│  MemoryQueue (debounced)                                          │
│      │ Deduplication: replace pending update for same thread      │
│      │ Debounce: wait N seconds before processing                 │
│      │ Batch: process all pending updates together                │
│      │                                                            │
│      ▼                                                            │
│  MemoryUpdater                                                    │
│      │ Load current memory from storage                           │
│      │ Format conversation + current memory into prompt           │
│      │ Call LLM with MEMORY_UPDATE_PROMPT                         │
│      │ Parse structured JSON response                             │
│      │ Deduplicate new facts against existing facts               │
│      │ Apply updates to memory structure                          │
│      │                                                            │
│      ▼                                                            │
│  MemoryStorage (FileMemoryStorage)                                │
│      │ Save to JSON file with atomic write (temp → rename)        │
│      │ Cache with mtime-based invalidation                        │
│      │                                                            │
│      ▼                                                            │
│  .deer-flow/memory.json (global)                                  │
│  .deer-flow/agents/{name}/memory.json (per-agent)                 │
└──────────────────────────────────────────────────────────────────┘
```

### 1.3 Memory Data Structure

```json
{
  "version": "1.0",
  "lastUpdated": "2026-03-31T07:00:00Z",
  "user": {
    "workContext": {
      "summary": "User is a senior frontend engineer working on a React-based dashboard project. Uses TypeScript exclusively.",
      "updatedAt": "2026-03-30T15:00:00Z"
    },
    "personalContext": {
      "summary": "User prefers concise explanations with code examples. Dislikes verbose documentation.",
      "updatedAt": "2026-03-28T10:00:00Z"
    },
    "topOfMind": {
      "summary": "Currently focused on migrating from Webpack to Vite. Deadline is next Friday.",
      "updatedAt": "2026-03-31T07:00:00Z"
    }
  },
  "history": {
    "recentMonths": {
      "summary": "March 2026: Worked on dashboard performance optimization, implemented virtual scrolling, set up CI/CD pipeline.",
      "updatedAt": "2026-03-31T07:00:00Z"
    },
    "earlierContext": {
      "summary": "January-February 2026: Built the initial dashboard from scratch, chose React + TypeScript stack.",
      "updatedAt": "2026-03-01T00:00:00Z"
    },
    "longTermBackground": {
      "summary": "User has 5+ years of frontend experience. Previously worked with Vue.js before switching to React.",
      "updatedAt": "2026-02-15T00:00:00Z"
    }
  },
  "facts": [
    {
      "id": "fact_a1b2c3d4",
      "content": "User prefers TypeScript over JavaScript for all projects",
      "category": "preference",
      "confidence": 0.95,
      "createdAt": "2026-03-15T10:00:00Z",
      "source": "thread-abc-123"
    },
    {
      "id": "fact_e5f6g7h8",
      "content": "Project uses React 19 with Server Components",
      "category": "technical",
      "confidence": 0.9,
      "createdAt": "2026-03-20T14:00:00Z",
      "source": "thread-def-456"
    }
  ]
}
```

---

## 2. Memory Update Pipeline

### 2.1 Step 1: Message Filtering (MemoryMiddleware)

The middleware filters conversation messages to extract only meaningful content:

```python
def _filter_messages_for_memory(messages):
    filtered = []
    skip_next_ai = False
    
    for msg in messages:
        if msg.type == "human":
            # Strip <uploaded_files> block (ephemeral, session-scoped)
            content = _UPLOAD_BLOCK_RE.sub("", content_str).strip()
            if not content:
                skip_next_ai = True  # Upload-only turn, skip paired AI response
                continue
            filtered.append(clean_msg)
            
        elif msg.type == "ai":
            if not msg.tool_calls:  # Only final responses (not intermediate)
                if not skip_next_ai:
                    filtered.append(msg)
                skip_next_ai = False
        
        # Skip: tool messages, AI messages with tool_calls
```

**Why filter?**
- Tool messages are implementation details, not meaningful conversation
- AI messages with tool_calls are intermediate reasoning steps
- Upload blocks contain ephemeral file paths that shouldn't persist
- Only user questions and final AI answers capture the conversation's essence

### 2.2 Step 2: Debounced Queuing (MemoryQueue)

```python
class MemoryUpdateQueue:
    def add(self, thread_id, messages, agent_name=None):
        with self._lock:
            # Replace existing pending update for same thread
            self._queue = [c for c in self._queue if c.thread_id != thread_id]
            self._queue.append(context)
            
            # Reset debounce timer
            self._reset_timer()  # Default: 30 seconds
    
    def _process_queue(self):
        # Process all pending updates
        for context in contexts_to_process:
            updater.update_memory(messages=context.messages, ...)
            time.sleep(0.5)  # Rate limiting between updates
```

**Key design decisions**:
- **Deduplication**: Only the latest update per thread is kept
- **Debouncing**: Waits for a configurable period (default 30s) before processing
- **Batching**: All pending updates are processed together
- **Rate limiting**: 0.5s delay between updates to avoid LLM rate limits
- **Thread safety**: Uses `threading.Lock` for concurrent access

### 2.3 Step 3: LLM-Based Memory Update (MemoryUpdater)

The updater sends the conversation + current memory to an LLM with a structured prompt:

```python
class MemoryUpdater:
    def update_memory(self, messages, thread_id, agent_name=None):
        # 1. Load current memory
        current_memory = storage.load(agent_name)
        
        # 2. Format conversation into text
        conversation_text = self._format_messages(messages)
        
        # 3. Build prompt with MEMORY_UPDATE_PROMPT template
        prompt = MEMORY_UPDATE_PROMPT.format(
            current_memory=json.dumps(current_memory),
            conversation=conversation_text,
        )
        
        # 4. Call LLM
        response = model.invoke(prompt)
        
        # 5. Parse structured JSON response
        updates = json.loads(response.content)
        
        # 6. Apply updates
        self._apply_updates(current_memory, updates)
        
        # 7. Save
        storage.save(current_memory, agent_name)
```

### 2.4 Step 4: Fact Deduplication

New facts are checked against existing facts to prevent duplicates:

```python
def _deduplicate_facts(existing_facts, new_facts):
    # Compare by content similarity
    # If a new fact's content matches an existing fact, skip it
    # Update confidence if the same fact is mentioned again
```

---

## 3. Memory Storage & Persistence

### 3.1 FileMemoryStorage

The default storage provider uses JSON files:

```python
class FileMemoryStorage(MemoryStorage):
    def load(self, agent_name=None):
        # Check cache with mtime-based invalidation
        file_path = self._get_memory_file_path(agent_name)
        current_mtime = file_path.stat().st_mtime
        
        if cached and cached_mtime == current_mtime:
            return cached_data  # Cache hit
        
        # Cache miss — reload from file
        data = json.load(file_path)
        self._cache[agent_name] = (data, current_mtime)
        return data
    
    def save(self, memory_data, agent_name=None):
        # Atomic write: temp file → rename
        temp_path = file_path.with_suffix(".tmp")
        json.dump(memory_data, temp_path)
        temp_path.replace(file_path)  # Atomic on most filesystems
        
        # Update cache
        self._cache[agent_name] = (memory_data, file_path.stat().st_mtime)
```

### 3.2 File Paths

| Scope | Path |
|-------|------|
| Global memory | `.deer-flow/memory.json` |
| Per-agent memory | `.deer-flow/agents/{agent_name}/memory.json` |

### 3.3 Cache Strategy

- **mtime-based invalidation**: Cache is valid as long as file modification time hasn't changed
- **Per-agent caching**: Each agent's memory is cached independently
- **Thread-safe**: Uses `threading.Lock` for concurrent access
- **Pluggable**: `MemoryStorage` is an abstract base class — custom implementations can use databases, cloud storage, etc.

---

## 4. Memory Injection into Prompts

### 4.1 Injection Format

Memory is injected into the system prompt as a `<memory>` section:

```xml
<memory>
## User Context
- **Work Context**: User is a senior frontend engineer working on a React-based dashboard project.
- **Personal Context**: User prefers concise explanations with code examples.
- **Current Focus**: Migrating from Webpack to Vite. Deadline is next Friday.

## History
- **Recent**: March 2026: Dashboard performance optimization, virtual scrolling, CI/CD pipeline.
- **Earlier**: January-February 2026: Built initial dashboard, chose React + TypeScript.
- **Background**: 5+ years frontend experience, previously Vue.js.

## Known Facts
- [preference | 0.95] User prefers TypeScript over JavaScript for all projects
- [technical | 0.90] Project uses React 19 with Server Components
</memory>
```

### 4.2 Token Budget

Facts are added incrementally until the `max_injection_tokens` limit is reached:

```python
def format_memory_for_injection(memory_data, max_tokens):
    sections = []
    
    # Always include user context and history (fixed cost)
    sections.append(format_user_context(memory_data["user"]))
    sections.append(format_history(memory_data["history"]))
    
    # Add facts ranked by confidence until budget exhausted
    facts = sorted(memory_data["facts"], key=lambda f: f["confidence"], reverse=True)
    remaining_tokens = max_tokens - count_tokens(sections)
    
    for fact in facts:
        fact_text = format_fact(fact)
        fact_tokens = count_tokens(fact_text)
        if fact_tokens > remaining_tokens:
            break
        sections.append(fact_text)
        remaining_tokens -= fact_tokens
    
    return "\n".join(sections)
```

---

## 5. Skills System Architecture

### 5.1 What is a Skill?

A skill is a **structured capability module** that teaches the agent how to perform specific tasks. Skills are the primary mechanism for extending DeerFlow's capabilities without modifying code.

### 5.2 Skill vs Tool

| Aspect | Skill | Tool |
|--------|-------|------|
| Nature | Knowledge/workflow | Executable function |
| Format | Markdown file (SKILL.md) | Python function with schema |
| Loading | Progressive (on-demand) | Eager (at agent creation) |
| Execution | Agent follows instructions | Direct function call |
| Extension | Drop a folder | Write code + register |

### 5.3 Skill Directory Structure

```
skills/
├── public/                          # Built-in skills (shipped with DeerFlow)
│   ├── bootstrap/                   # Agent personality creation
│   │   ├── SKILL.md                # Main skill file
│   │   └── templates/              # Supporting templates
│   ├── chart-visualization/         # Chart generation
│   │   ├── SKILL.md
│   │   └── references/
│   ├── data-analysis/               # Data analysis workflows
│   ├── deep-research/               # Deep research methodology
│   ├── frontend-design/             # Frontend design patterns
│   ├── image-generation/            # Image generation
│   ├── podcast-generation/          # Podcast creation
│   ├── ppt-generation/              # Slide deck creation
│   └── skill-creator/               # Meta-skill for creating skills
└── custom/                          # User-created skills
    └── my-custom-skill/
        ├── SKILL.md
        └── references/
```

---

## 6. Skill Discovery & Loading

### 6.1 Discovery Process

`load_skills()` in `skills/loader.py`:

```python
def load_skills():
    skills = []
    
    # 1. Scan public skills directory
    public_dir = skills_path / "public"
    for skill_dir in sorted(public_dir.iterdir()):
        skill_file = skill_dir / "SKILL.md"
        if skill_file.exists():
            skill = parse_skill_file(skill_file, category="public")
            if skill:
                skills.append(skill)
    
    # 2. Scan custom skills directory
    custom_dir = skills_path / "custom"
    for skill_dir in sorted(custom_dir.iterdir()):
        skill_file = skill_dir / "SKILL.md"
        if skill_file.exists():
            skill = parse_skill_file(skill_file, category="custom")
            if skill:
                skills.append(skill)
    
    # 3. Apply enabled/disabled state from extensions_config.json
    for skill in skills:
        skill.enabled = extensions_config.is_skill_enabled(skill.name)
    
    return sorted(skills, key=lambda s: s.name)
```

### 6.2 Skill Parsing

```python
def parse_skill_file(skill_file, category):
    content = skill_file.read_text()
    
    # Extract YAML front matter: ---\nkey: value\n---
    front_matter = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    
    # Parse key-value pairs
    metadata = {}
    for line in front_matter.split("\n"):
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()
    
    return Skill(
        name=metadata["name"],
        description=metadata["description"],
        license=metadata.get("license"),
        skill_dir=skill_file.parent,
        skill_file=skill_file,
        category=category,
        enabled=True,  # Default, overridden by config
    )
```

---

## 7. Skill File Format (SKILL.md)

### 7.1 Structure

```markdown
---
name: Deep Research
description: Conduct thorough research on any topic with citations
license: MIT
allowed-tools:
  - web_search
  - web_fetch
  - read_file
  - write_file
  - bash
---

# Deep Research Workflow

## Overview
This skill enables comprehensive research on any topic...

## Steps
1. **Understand the Question**: Analyze the research question...
2. **Search for Sources**: Use web_search to find primary sources...
3. **Analyze Sources**: Read and analyze each source...
4. **Synthesize Findings**: Combine insights from multiple sources...
5. **Generate Report**: Write a comprehensive report with citations...

## References
- See `references/methodology.md` for detailed research methodology
- See `templates/report.md` for report template

## Best Practices
- Always cite sources with URLs
- Cross-reference claims across multiple sources
- Distinguish between facts and opinions
```

### 7.2 Front Matter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Human-readable skill name |
| `description` | Yes | Brief description for skill listing |
| `license` | No | License type (MIT, Apache, etc.) |
| `allowed-tools` | No | Tools the skill may use |

---

## 8. Progressive Skill Loading Pattern

### 8.1 The Pattern

Skills are NOT loaded into the system prompt in full. Instead:

**Step 1 — Listing** (at agent creation):
```xml
<skill_system>
Available skills (use read_file to load when needed):

1. **Deep Research** — Conduct thorough research on any topic with citations
   File: /mnt/skills/public/deep-research/SKILL.md

2. **Chart Visualization** — Generate interactive charts and visualizations
   File: /mnt/skills/public/chart-visualization/SKILL.md

3. **Data Analysis** — Analyze datasets and generate insights
   File: /mnt/skills/public/data-analysis/SKILL.md
</skill_system>
```

**Step 2 — Loading** (at runtime, when needed):
```
Agent: "The user wants a research report. Let me load the Deep Research skill."
→ read_file("/mnt/skills/public/deep-research/SKILL.md")
→ Skill content loaded into context
→ Agent follows the skill's workflow
```

**Step 3 — Reference Loading** (as needed):
```
Agent: "The skill references a methodology document."
→ read_file("/mnt/skills/public/deep-research/references/methodology.md")
→ Reference content loaded into context
```

### 8.2 Why Progressive Loading?

| Approach | Token Cost | Flexibility |
|----------|-----------|-------------|
| Load all skills upfront | 5000-20000 tokens | Low (wastes context) |
| Progressive loading | 50-200 tokens (listing) | High (load on demand) |

With 10+ skills, progressive loading saves 90%+ of skill-related tokens.

---

## 9. Sandbox Architecture

### 9.1 Sandbox Providers

DeerFlow supports two sandbox providers:

#### LocalSandboxProvider
- Direct execution on the host machine
- File operations map to per-thread directories
- Host bash disabled by default (security)
- Virtual path resolution required

#### AioSandboxProvider (Docker)
- Isolated Docker containers per thread
- `/mnt/user-data` mounted as a volume
- Full bash access (isolated)
- No virtual path resolution needed (paths are real inside container)

### 9.2 Sandbox Lifecycle

```
Thread Created
    │
    ▼
First Tool Call → ensure_sandbox_initialized()
    │
    ├── Local: Create thread directories, return "local" sandbox
    │
    └── Docker: Acquire container from pool, mount volumes
    │
    ▼
Tool Execution (multiple calls within same turn)
    │
    ▼
after_agent → SandboxMiddleware.after_agent()
    │
    ├── Release sandbox back to pool (not destroyed)
    │
    ▼
Next Turn → Sandbox reacquired (same container/directories)
    │
    ▼
Application Shutdown → SandboxProvider.shutdown()
    │
    └── Destroy all containers / cleanup
```

### 9.3 Lazy Initialization

Sandboxes are acquired lazily by default:

```python
class SandboxMiddleware:
    def __init__(self, lazy_init=True):
        self._lazy_init = lazy_init
    
    def before_agent(self, state, runtime):
        if self._lazy_init:
            return None  # Skip — sandbox acquired on first tool call
        
        # Eager: acquire now
        sandbox_id = provider.acquire(thread_id)
        return {"sandbox": {"sandbox_id": sandbox_id}}
```

The actual acquisition happens in `ensure_sandbox_initialized()`:

```python
def ensure_sandbox_initialized(runtime):
    # Check if sandbox already exists
    sandbox_state = runtime.state.get("sandbox")
    if sandbox_state and sandbox_state.get("sandbox_id"):
        sandbox = provider.get(sandbox_state["sandbox_id"])
        if sandbox:
            return sandbox
    
    # Lazy acquisition
    sandbox_id = provider.acquire(thread_id)
    runtime.state["sandbox"] = {"sandbox_id": sandbox_id}
    return provider.get(sandbox_id)
```

---

## 10. Sandbox Tools Implementation

### 10.1 Tool Inventory

| Tool | Purpose | Parameters |
|------|---------|-----------|
| `bash` | Execute bash commands | `description`, `command` |
| `ls` | List directory contents (2 levels deep) | `description`, `path` |
| `read_file` | Read text file contents | `description`, `path`, `start_line?`, `end_line?` |
| `write_file` | Write content to file | `description`, `path`, `content`, `append?` |
| `str_replace` | Replace substring in file | `description`, `path`, `old_str`, `new_str`, `replace_all?` |

### 10.2 Common Execution Pattern

Every sandbox tool follows the same pattern:

```python
@tool("tool_name")
def tool_func(runtime, description, ...):
    try:
        # 1. Ensure sandbox is initialized (lazy)
        sandbox = ensure_sandbox_initialized(runtime)
        
        # 2. Ensure thread directories exist
        ensure_thread_directories_exist(runtime)
        
        # 3. If local sandbox:
        if is_local_sandbox(runtime):
            thread_data = get_thread_data(runtime)
            
            # a. Validate path security
            validate_local_tool_path(path, thread_data, read_only=...)
            
            # b. Resolve virtual → physical path
            if _is_skills_path(path):
                path = _resolve_skills_path(path)
            elif _is_acp_workspace_path(path):
                path = _resolve_acp_workspace_path(path, thread_id)
            else:
                path = _resolve_and_validate_user_data_path(path, thread_data)
        
        # 4. Execute operation
        result = sandbox.operation(path, ...)
        
        # 5. If local sandbox: mask host paths in output
        if is_local_sandbox(runtime):
            result = mask_local_paths_in_output(result, thread_data)
        
        return result
        
    except SandboxError as e:
        return f"Error: {e}"
    except PermissionError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error: Unexpected error: {_sanitize_error(e, runtime)}"
```

### 10.3 Bash Tool Special Handling

The bash tool has additional security measures:

```python
@tool("bash")
def bash_tool(runtime, description, command):
    sandbox = ensure_sandbox_initialized(runtime)
    
    if is_local_sandbox(runtime):
        # 1. Check if host bash is allowed
        if not is_host_bash_allowed():
            return "Error: Host bash is disabled..."
        
        # 2. Validate paths in command
        validate_local_bash_command_paths(command, thread_data)
        
        # 3. Replace virtual paths in command
        command = replace_virtual_paths_in_command(command, thread_data)
        
        # 4. Prepend cd to workspace
        command = f"cd {workspace} && {command}"
        
        # 5. Execute
        output = sandbox.execute_command(command)
        
        # 6. Mask paths in output
        return mask_local_paths_in_output(output, thread_data)
    
    # Docker: execute directly
    return sandbox.execute_command(command)
```

---

## 11. Virtual Path System

### 11.1 Path Mapping

| Virtual Path | Physical Path (Local) | Docker Path |
|-------------|----------------------|-------------|
| `/mnt/user-data/workspace` | `.deer-flow/threads/{id}/user-data/workspace` | `/mnt/user-data/workspace` (mounted) |
| `/mnt/user-data/uploads` | `.deer-flow/threads/{id}/user-data/uploads` | `/mnt/user-data/uploads` (mounted) |
| `/mnt/user-data/outputs` | `.deer-flow/threads/{id}/user-data/outputs` | `/mnt/user-data/outputs` (mounted) |
| `/mnt/skills` | `deer-flow/skills/` | `/mnt/skills` (mounted) |
| `/mnt/acp-workspace` | `.deer-flow/threads/{id}/acp-workspace/` | `/mnt/acp-workspace` (mounted) |

### 11.2 Path Resolution

```python
def replace_virtual_path(path, thread_data):
    mappings = {
        "/mnt/user-data/workspace": thread_data["workspace_path"],
        "/mnt/user-data/uploads": thread_data["uploads_path"],
        "/mnt/user-data/outputs": thread_data["outputs_path"],
    }
    
    # Longest-prefix-first matching
    for virtual_base, actual_base in sorted(mappings.items(), key=len, reverse=True):
        if path == virtual_base:
            return actual_base
        if path.startswith(f"{virtual_base}/"):
            rest = path[len(virtual_base):].lstrip("/")
            return f"{actual_base}/{rest}"
    
    return path
```

### 11.3 Output Masking

```python
def mask_local_paths_in_output(output, thread_data):
    # 1. Mask skills host paths → /mnt/skills
    # 2. Mask ACP workspace paths → /mnt/acp-workspace
    # 3. Mask user-data paths → /mnt/user-data/*
    
    # Uses regex for robust matching across path styles
    # Handles both raw and resolved paths
    # Handles both forward and backward slashes
```

---

## 12. Security Model

### 12.1 Path Validation

```python
def validate_local_tool_path(path, thread_data, *, read_only=False):
    # 1. Reject path traversal (..)
    _reject_path_traversal(path)
    
    # 2. Skills paths — read-only only
    if _is_skills_path(path):
        if not read_only:
            raise PermissionError("Write access to skills path is not allowed")
        return
    
    # 3. ACP workspace — read-only only
    if _is_acp_workspace_path(path):
        if not read_only:
            raise PermissionError("Write access to ACP workspace is not allowed")
        return
    
    # 4. User-data paths — always allowed
    if path.startswith("/mnt/user-data/"):
        return
    
    # 5. Everything else — denied
    raise PermissionError("Only paths under /mnt/user-data/, /mnt/skills/, or /mnt/acp-workspace/ are allowed")
```

### 12.2 Bash Command Path Validation

```python
def validate_local_bash_command_paths(command, thread_data):
    # Extract all absolute paths from command
    for path in _ABSOLUTE_PATH_PATTERN.findall(command):
        # Allow: /mnt/user-data/*, /mnt/skills/*, /mnt/acp-workspace/*
        # Allow: system paths (/bin/, /usr/bin/, /dev/, etc.)
        # Allow: MCP filesystem server allowed paths
        # Deny: everything else
```

### 12.3 Host Bash Gating

```python
def is_host_bash_allowed():
    # Controlled by config.yaml: sandbox.allow_host_bash
    # Default: False (disabled)
    # When disabled, bash_tool returns an error message
```

---

## 13. Embedded Python SDK (DeerFlowClient)

### 13.1 Overview

The `DeerFlowClient` provides direct in-process access to DeerFlow's agent capabilities without running HTTP services. It creates the same agent internally and provides a clean Python API.

### 13.2 API Surface

```python
class DeerFlowClient:
    # ── Constructor ──
    def __init__(self, model_name=None, thinking_enabled=None, subagent_enabled=None,
                 plan_mode=False, agent_name=None)
    
    # ── Chat ──
    def chat(self, message, thread_id=None, **kwargs) -> str
    def stream(self, message, thread_id=None, **kwargs) -> Iterator[StreamEvent]
    
    # ── Configuration ──
    def list_models(self) -> list[dict]
    def list_skills(self) -> list[dict]
    def get_config(self) -> dict
    
    # ── Memory ──
    def get_memory(self, agent_name=None) -> dict
    def create_memory_fact(self, content, category=None, confidence=None) -> dict
    def delete_memory_fact(self, fact_id) -> bool
    def clear_memory(self, agent_name=None) -> bool
    
    # ── Files ──
    def upload_files(self, thread_id, file_paths) -> list[dict]
    
    # ── MCP ──
    def get_mcp_config(self) -> dict
    def update_mcp_config(self, config) -> dict
    
    # ── Thread Management ──
    def list_threads(self, limit=20) -> list[dict]
    def delete_thread(self, thread_id) -> bool
```

### 13.3 Usage Examples

```python
from deerflow.client import DeerFlowClient

# Basic chat
client = DeerFlowClient(model_name="gpt-4")
response = client.chat("What is the capital of France?")
print(response)  # "The capital of France is Paris."

# Streaming
for event in client.stream("Write a Python function to sort a list"):
    if event.type == "messages-tuple":
        chunk = event.data
        if isinstance(chunk, list) and chunk[0].get("type") == "AIMessageChunk":
            print(chunk[0].get("content", ""), end="")

# With thread persistence
response1 = client.chat("My name is Alice", thread_id="thread-1")
response2 = client.chat("What's my name?", thread_id="thread-1")
# response2 will know the user's name is Alice

# Memory management
client.create_memory_fact("User prefers Python", category="preference", confidence=0.9)
memory = client.get_memory()
print(memory["facts"])

# File upload
client.upload_files("thread-1", ["./data.csv", "./report.pdf"])
response = client.chat("Analyze the uploaded data", thread_id="thread-1")
```

---

## 14. SDK Architecture & Internals

### 14.1 Lazy Agent Creation

```python
class DeerFlowClient:
    def __init__(self, ...):
        self._agent = None
        self._config_hash = None
    
    def _get_or_create_agent(self):
        current_hash = self._compute_config_hash()
        if self._agent is None or current_hash != self._config_hash:
            self._agent = make_lead_agent(config)
            self._config_hash = current_hash
        return self._agent
```

The agent is created lazily on first use and recreated if configuration changes.

### 14.2 Synchronous Streaming

The SDK provides synchronous streaming by running the async agent in a thread:

```python
def stream(self, message, thread_id=None):
    agent = self._get_or_create_agent()
    
    # Run async agent in a new event loop on a background thread
    for event in self._sync_stream(agent, message, thread_id):
        yield StreamEvent(type=event_type, data=event_data)
```

### 14.3 Thread Management

The SDK manages thread state through the checkpointer:

```python
def chat(self, message, thread_id=None):
    if thread_id is None:
        thread_id = str(uuid.uuid4())
    
    config = {"configurable": {"thread_id": thread_id}}
    result = agent.invoke({"messages": [HumanMessage(content=message)]}, config=config)
    
    # Extract final AI message
    return result["messages"][-1].content
```

---

## 15. Gateway API Layer

### 15.1 API Endpoints

The Gateway API provides REST endpoints for non-agent operations:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/models` | GET | List available models |
| `/api/skills` | GET | List available skills |
| `/api/skills/{name}/toggle` | POST | Enable/disable a skill |
| `/api/memory` | GET | Get memory data |
| `/api/memory/facts` | POST | Create a memory fact |
| `/api/memory/facts/{id}` | DELETE | Delete a memory fact |
| `/api/memory` | DELETE | Clear all memory |
| `/api/mcp/config` | GET/PUT | Get/update MCP configuration |
| `/api/uploads/{thread_id}` | POST | Upload files |
| `/api/artifacts/{path}` | GET | Serve generated artifacts |
| `/api/suggestions` | POST | Generate follow-up suggestions |

### 15.2 Run Lifecycle (services.py)

```python
async def start_run(body, thread_id, request):
    # 1. Get singletons
    bridge = get_stream_bridge(request)
    run_mgr = get_run_manager(request)
    checkpointer = get_checkpointer(request)
    store = get_store(request)
    
    # 2. Create run record
    record = await run_mgr.create_or_reject(thread_id, ...)
    
    # 3. Ensure thread exists in store
    await _upsert_thread_in_store(store, thread_id, metadata)
    
    # 4. Resolve agent factory
    agent_factory = resolve_agent_factory(body.assistant_id)
    
    # 5. Normalize input
    graph_input = normalize_input(body.input)
    
    # 6. Build config
    config = build_run_config(thread_id, body.config, body.metadata)
    
    # 7. Launch background task
    task = asyncio.create_task(run_agent(bridge, run_mgr, record, ...))
    
    # 8. Schedule title sync after run completes
    asyncio.create_task(_sync_thread_title_after_run(task, thread_id, ...))
    
    return record
```

### 15.3 SSE Consumer

```python
async def sse_consumer(bridge, record, request, run_mgr):
    try:
        async for entry in bridge.subscribe(record.run_id):
            if await request.is_disconnected():
                break
            
            if entry is HEARTBEAT_SENTINEL:
                yield ": heartbeat\n\n"
            elif entry is END_SENTINEL:
                yield format_sse("end", None)
                return
            else:
                yield format_sse(entry.event, entry.data)
    finally:
        # Handle disconnect
        if record.on_disconnect == DisconnectMode.cancel:
            await run_mgr.cancel(record.run_id)
```

---

## 16. Summary

### Memory System
- **LLM-driven updates**: Memory is updated by an LLM, not rule-based extraction
- **Structured storage**: User context, history, and ranked facts
- **Debounced processing**: Batched updates with configurable delay
- **Token-budgeted injection**: Facts ranked by confidence, added until budget exhausted
- **Pluggable storage**: Abstract base class supports custom backends

### Skills System
- **Markdown-based**: Skills are Markdown files with YAML front matter
- **Progressive loading**: Listed by name, loaded on demand
- **Category-based**: Public (built-in) and custom (user-created)
- **Config-driven**: Enable/disable via `extensions_config.json`

### Sandbox System
- **Dual providers**: Local (host) and Docker (isolated)
- **Lazy initialization**: Sandbox acquired on first tool use
- **Virtual path abstraction**: Consistent paths across environments
- **Security-first**: Path validation, traversal prevention, output masking

### SDK
- **In-process execution**: No HTTP services needed
- **Lazy agent creation**: Created on first use, recreated on config change
- **Full API surface**: Chat, stream, memory, files, MCP, threads
- **Synchronous interface**: Wraps async agent for easy integration
