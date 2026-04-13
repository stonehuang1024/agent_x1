# DeerFlow 2.0 — Overall Architecture & Design Deep Dive

> **Document Scope**: This document provides a comprehensive top-down analysis of the DeerFlow 2.0 project — from high-level architecture to module-level design, covering the agent loop, prompt engineering, multi-agent orchestration, runtime, memory, context management, skills system, tool calling, and the embedded Python SDK.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Code Directory Structure](#2-code-directory-structure)
3. [System Architecture](#3-system-architecture)
4. [Core Module Breakdown](#4-core-module-breakdown)
5. [The Agent Loop — How It Works](#5-the-agent-loop--how-it-works)
6. [Prompt Design — System Prompt Engineering](#6-prompt-design--system-prompt-engineering)
7. [Multi-Agent Design & Communication](#7-multi-agent-design--communication)
8. [Runtime Architecture](#8-runtime-architecture)
9. [Memory System — Long-Term Persistence](#9-memory-system--long-term-persistence)
10. [Context Compression & Summarization](#10-context-compression--summarization)
11. [Session & Thread Management](#11-session--thread-management)
12. [Skills System](#12-skills-system)
13. [Tool System & Tool Calling](#13-tool-system--tool-calling)
14. [LLM Invocation — Model Factory](#14-llm-invocation--model-factory)
15. [Sandbox & File System](#15-sandbox--file-system)
16. [Embedded Python SDK (DeerFlowClient)](#16-embedded-python-sdk-deerflowclient)
17. [User Interaction & Frontend](#17-user-interaction--frontend)
18. [Key Technical Challenges](#18-key-technical-challenges)
19. [Areas Worth Further Investigation](#19-areas-worth-further-investigation)
20. [Summary](#20-summary)

---

## 1. Project Overview

**DeerFlow** (**D**eep **E**xploration and **E**fficient **R**esearch **Flow**) is an open-source **super agent harness** developed by ByteDance. Version 2.0 is a ground-up rewrite that transforms DeerFlow from a Deep Research framework into a general-purpose agent runtime.

### Core Identity

DeerFlow 2.0 is not a framework you wire together — it is a **batteries-included, fully extensible agent harness** built on **LangGraph** and **LangChain**. It ships with:

- A **lead agent** that orchestrates everything
- **Sub-agents** for parallel task decomposition
- **Persistent long-term memory** across sessions
- **Sandboxed execution environments** (local or Docker-based)
- **Extensible skills** (Markdown-based capability modules)
- **MCP server integration** for external tool access
- **Multi-channel support** (Web, Telegram, Slack, Feishu)

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend Agent Runtime | Python 3.12+, LangGraph, LangChain |
| API Gateway | FastAPI |
| Frontend | Next.js, React, TypeScript |
| Reverse Proxy | Nginx |
| Package Management | uv (Python), pnpm (Node.js) |
| Containerization | Docker, Docker Compose |
| State Persistence | SQLite (checkpointer), JSON files (memory) |

---

## 2. Code Directory Structure

```
deer-flow/
├── backend/                          # All backend code
│   ├── app/                          # Application layer (Gateway + Channels)
│   │   ├── channels/                 # IM channel integrations (Telegram, Slack, Feishu)
│   │   │   ├── base.py              # Base channel class
│   │   │   ├── manager.py           # Channel lifecycle manager
│   │   │   ├── feishu.py            # Feishu/Lark integration
│   │   │   ├── slack.py             # Slack integration
│   │   │   ├── telegram.py          # Telegram integration
│   │   │   └── store.py             # Channel state store
│   │   └── gateway/                  # FastAPI Gateway API
│   │       ├── app.py               # FastAPI application setup
│   │       ├── config.py            # Gateway config
│   │       ├── deps.py              # Dependency injection
│   │       ├── services.py          # Run lifecycle service layer
│   │       └── routers/             # API route handlers
│   │           ├── agents.py        # Agent CRUD
│   │           ├── artifacts.py     # File artifact serving
│   │           ├── mcp.py           # MCP config management
│   │           ├── memory.py        # Memory CRUD API
│   │           ├── models.py        # Model listing
│   │           ├── runs.py          # Stateless run endpoints
│   │           ├── skills.py        # Skills management
│   │           ├── suggestions.py   # Follow-up suggestion generation
│   │           ├── thread_runs.py   # Thread-scoped run endpoints
│   │           ├── threads.py       # Thread CRUD + search
│   │           └── uploads.py       # File upload handling
│   ├── packages/
│   │   └── harness/                  # Core harness package (pip-installable)
│   │       └── deerflow/
│   │           ├── agents/           # Agent system
│   │           │   ├── lead_agent/   # Lead agent (entry point)
│   │           │   │   ├── agent.py  # make_lead_agent() factory
│   │           │   │   └── prompt.py # System prompt template
│   │           │   ├── memory/       # Memory subsystem
│   │           │   │   ├── prompt.py # Memory update/injection prompts
│   │           │   │   ├── queue.py  # Debounced memory update queue
│   │           │   │   ├── storage.py# Memory storage providers
│   │           │   │   └── updater.py# LLM-based memory updater
│   │           │   ├── middlewares/  # Middleware chain
│   │           │   │   ├── clarification_middleware.py
│   │           │   │   ├── dangling_tool_call_middleware.py
│   │           │   │   ├── deferred_tool_filter_middleware.py
│   │           │   │   ├── loop_detection_middleware.py
│   │           │   │   ├── memory_middleware.py
│   │           │   │   ├── sandbox_audit_middleware.py
│   │           │   │   ├── subagent_limit_middleware.py
│   │           │   │   ├── thread_data_middleware.py
│   │           │   │   ├── title_middleware.py
│   │           │   │   ├── todo_middleware.py
│   │           │   │   ├── token_usage_middleware.py
│   │           │   │   ├── tool_error_handling_middleware.py
│   │           │   │   ├── uploads_middleware.py
│   │           │   │   └── view_image_middleware.py
│   │           │   ├── checkpointer/ # State persistence
│   │           │   ├── factory.py    # Agent factory
│   │           │   ├── features.py   # Feature flags
│   │           │   └── thread_state.py # ThreadState schema
│   │           ├── client.py         # Embedded Python SDK
│   │           ├── community/        # Community tool integrations
│   │           │   ├── aio_sandbox/  # Docker sandbox provider
│   │           │   ├── ddg_search/   # DuckDuckGo search
│   │           │   ├── firecrawl/    # Firecrawl web scraping
│   │           │   ├── image_search/ # Image search
│   │           │   ├── infoquest/    # BytePlus InfoQuest
│   │           │   ├── jina_ai/      # Jina AI reader
│   │           │   └── tavily/       # Tavily search
│   │           ├── config/           # Configuration system
│   │           │   ├── app_config.py # Main config loader (config.yaml)
│   │           │   ├── extensions_config.py # MCP + skills state
│   │           │   ├── memory_config.py
│   │           │   ├── model_config.py
│   │           │   ├── paths.py      # Virtual/physical path mapping
│   │           │   ├── sandbox_config.py
│   │           │   ├── skills_config.py
│   │           │   └── summarization_config.py
│   │           ├── guardrails/       # Safety guardrails
│   │           ├── mcp/              # MCP client integration
│   │           │   ├── cache.py      # MCP tool caching
│   │           │   ├── client.py     # MCP client wrapper
│   │           │   ├── oauth.py      # OAuth token flows
│   │           │   └── tools.py      # MCP tool conversion
│   │           ├── models/           # LLM model providers
│   │           │   ├── factory.py    # create_chat_model()
│   │           │   ├── claude_provider.py
│   │           │   ├── openai_codex_provider.py
│   │           │   ├── patched_deepseek.py
│   │           │   └── patched_openai.py
│   │           ├── reflection/       # Dynamic class resolution
│   │           ├── runtime/          # Execution runtime
│   │           │   ├── runs/         # Run lifecycle management
│   │           │   │   ├── manager.py# RunManager (create, cancel, status)
│   │           │   │   ├── worker.py # Background agent execution
│   │           │   │   └── schemas.py# RunStatus enum
│   │           │   ├── store/        # Thread data store (SQLite)
│   │           │   ├── stream_bridge/# SSE event bridge
│   │           │   └── serialization.py
│   │           ├── sandbox/          # Sandbox execution
│   │           │   ├── local/        # Local sandbox provider
│   │           │   ├── tools.py      # Sandbox tool implementations (bash, read_file, etc.)
│   │           │   ├── security.py   # Path validation & security
│   │           │   └── sandbox_provider.py
│   │           ├── skills/           # Skills system
│   │           │   ├── loader.py     # Skill discovery & loading
│   │           │   ├── parser.py     # SKILL.md frontmatter parser
│   │           │   ├── installer.py  # .skill archive installer
│   │           │   ├── types.py      # Skill data types
│   │           │   └── validation.py # Skill validation
│   │           ├── subagents/        # Sub-agent system
│   │           │   ├── executor.py   # SubagentExecutor (async execution)
│   │           │   ├── registry.py   # Subagent type registry
│   │           │   ├── config.py     # SubagentConfig
│   │           │   └── builtins/     # Built-in subagent types
│   │           │       ├── bash_agent.py
│   │           │       └── general_purpose.py
│   │           ├── tools/            # Tool system
│   │           │   ├── tools.py      # get_available_tools()
│   │           │   └── builtins/     # Built-in tools
│   │           │       ├── task_tool.py        # Subagent delegation
│   │           │       ├── tool_search.py      # Deferred tool discovery
│   │           │       ├── clarification_tool.py
│   │           │       ├── present_file_tool.py
│   │           │       ├── view_image_tool.py
│   │           │       └── invoke_acp_agent_tool.py
│   │           ├── uploads/          # File upload management
│   │           └── utils/            # Utility functions
│   ├── tests/                        # Comprehensive test suite (70+ test files)
│   └── docs/                         # Backend documentation
├── frontend/                         # Next.js frontend application
│   └── src/
│       ├── app/                      # Next.js app router pages
│       ├── components/               # React components
│       │   ├── ai-elements/          # AI-specific UI components
│       │   ├── workspace/            # Workspace UI (chat, settings, etc.)
│       │   └── ui/                   # Shared UI primitives
│       └── core/                     # Frontend core logic
│           ├── agents/               # Agent API client
│           ├── api/                  # API client & stream handling
│           ├── memory/               # Memory management
│           ├── messages/             # Message parsing & rendering
│           ├── skills/               # Skills API
│           ├── threads/              # Thread management
│           └── tools/                # Tool state management
├── skills/                           # Skills directory
│   └── public/                       # Built-in public skills
│       ├── bootstrap/                # Agent personality creation
│       ├── chart-visualization/      # Chart generation
│       ├── data-analysis/            # Data analysis
│       ├── deep-research/            # Deep research workflow
│       ├── frontend-design/          # Frontend design
│       ├── image-generation/         # Image generation
│       ├── podcast-generation/       # Podcast generation
│       ├── ppt-generation/           # Slide deck creation
│       ├── skill-creator/            # Meta-skill for creating skills
│       └── ...                       # More skills
├── docker/                           # Docker configuration
├── scripts/                          # Utility scripts
├── config.example.yaml               # Configuration template
└── extensions_config.example.json    # MCP/skills state template
```

---

## 3. System Architecture

### High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Client Layer                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │  Browser  │  │ Telegram │  │  Slack   │  │  Feishu / Lark   │   │
│  │  (Web UI) │  │   Bot    │  │   Bot    │  │      Bot         │   │
│  └─────┬─────┘  └────┬─────┘  └────┬─────┘  └───────┬──────────┘   │
└────────┼──────────────┼─────────────┼────────────────┼──────────────┘
         │              │             │                │
         ▼              └─────────────┴────────────────┘
┌─────────────────┐              │
│  Nginx (:2026)  │◄─────────────┘ (IM channels connect to Gateway directly)
│  Reverse Proxy  │
└────────┬────────┘
         │
    ┌────┴────────────────────────────────┐
    │                │                    │
    ▼                ▼                    ▼
┌──────────┐  ┌───────────┐  ┌────────────────┐
│ LangGraph│  │  Gateway   │  │   Frontend     │
│  Server  │  │    API     │  │   (Next.js)    │
│ (:2024)  │  │  (:8001)   │  │   (:3000)      │
│          │  │            │  │                │
│ Agent    │  │ REST API   │  │ Chat UI        │
│ Runtime  │  │ for config,│  │ Settings       │
│ SSE      │  │ uploads,   │  │ Agent Builder  │
│ Streaming│  │ memory,    │  │ Artifacts      │
│          │  │ skills     │  │                │
└──────────┘  └───────────┘  └────────────────┘
    │              │
    ▼              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Shared Infrastructure                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │ config.yaml  │  │ extensions   │  │ .deer-flow/ (runtime     │  │
│  │ (models,     │  │ _config.json │  │  data: threads, memory,  │  │
│  │  tools,      │  │ (MCP, skills │  │  checkpoints, uploads)   │  │
│  │  sandbox)    │  │  state)      │  │                          │  │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### Three-Service Architecture

DeerFlow runs as three cooperating services behind Nginx:

1. **LangGraph Server (Port 2024)**: The core agent runtime. Handles agent creation, thread state management, middleware execution, tool orchestration, and SSE streaming. This is where the LLM loop runs.

2. **Gateway API (Port 8001)**: A FastAPI application providing REST endpoints for non-agent operations — model listing, MCP configuration, skills management, file uploads, memory CRUD, thread cleanup, artifact serving, and follow-up suggestion generation.

3. **Frontend (Port 3000)**: A Next.js application providing the web UI — chat interface, settings panel, agent builder, artifact viewer, and more.

**Nginx (Port 2026)** acts as the unified entry point, routing:
- `/api/langgraph/*` → LangGraph Server
- `/api/*` → Gateway API
- `/*` → Frontend

---

## 4. Core Module Breakdown

### 4.1 Lead Agent (`agents/lead_agent/`)

The **lead agent** is the central orchestrator. It is created by `make_lead_agent(config)` which:

1. **Resolves the model** — determines which LLM to use based on request, agent config, or global default
2. **Loads tools** — assembles built-in tools, configured tools, MCP tools, and optionally subagent tools
3. **Builds the middleware chain** — a sequence of pre/post-processing steps
4. **Generates the system prompt** — dynamically assembled from templates, memory, skills, and agent personality
5. **Creates the agent** via `create_agent()` from LangChain

```python
# Simplified flow of make_lead_agent()
def make_lead_agent(config: RunnableConfig):
    model = create_chat_model(name=model_name, thinking_enabled=thinking_enabled)
    tools = get_available_tools(model_name=model_name, subagent_enabled=subagent_enabled)
    middlewares = _build_middlewares(config, model_name=model_name, agent_name=agent_name)
    system_prompt = apply_prompt_template(subagent_enabled=subagent_enabled, agent_name=agent_name)
    
    return create_agent(
        model=model,
        tools=tools,
        middleware=middlewares,
        system_prompt=system_prompt,
        state_schema=ThreadState,
    )
```

### 4.2 Middleware Chain (`agents/middlewares/`)

The middleware chain is the backbone of DeerFlow's extensibility. Each middleware implements `before_agent()` and/or `after_agent()` hooks that run before and after each LLM call.

**Middleware execution order** (carefully designed for dependency correctness):

| Order | Middleware | Purpose |
|-------|-----------|---------|
| 1 | `ThreadDataMiddleware` | Initialize workspace/uploads/outputs paths |
| 2 | `UploadsMiddleware` | Inject uploaded file list into context |
| 3 | `SandboxMiddleware` | Acquire sandbox environment |
| 4 | `SummarizationMiddleware` | Compress context when approaching token limits |
| 5 | `TodoMiddleware` | Task tracking in plan mode |
| 6 | `TokenUsageMiddleware` | Track token consumption |
| 7 | `TitleMiddleware` | Auto-generate conversation title |
| 8 | `MemoryMiddleware` | Queue conversation for memory update |
| 9 | `ViewImageMiddleware` | Inject image data for vision models |
| 10 | `DeferredToolFilterMiddleware` | Hide deferred tool schemas until searched |
| 11 | `SubagentLimitMiddleware` | Truncate excess parallel task calls |
| 12 | `LoopDetectionMiddleware` | Detect and break repetitive tool call loops |
| 13 | `ToolErrorHandlingMiddleware` | Convert tool exceptions to ToolMessages |
| 14 | `DanglingToolCallMiddleware` | Patch missing ToolMessages |
| 15 | `ClarificationMiddleware` | Intercept clarification requests (always last) |

### 4.3 Thread State (`agents/thread_state.py`)

The `ThreadState` extends LangGraph's `AgentState` with DeerFlow-specific fields:

```python
class ThreadState(AgentState):
    # Inherited from AgentState:
    messages: list[BaseMessage]          # Conversation history
    
    # DeerFlow extensions:
    sandbox: SandboxState | None         # {sandbox_id: str}
    thread_data: ThreadDataState | None  # {workspace_path, uploads_path, outputs_path}
    title: str | None                    # Auto-generated conversation title
    artifacts: list[str]                 # Generated file paths (deduplicated)
    todos: list | None                   # Task tracking (plan mode)
    uploaded_files: list[dict] | None    # Uploaded file metadata
    viewed_images: dict[str, ViewedImageData]  # image_path -> {base64, mime_type}
```

### 4.4 Configuration System (`config/`)

DeerFlow uses a layered configuration system:

- **`config.yaml`** — Primary configuration: models, tools, sandbox, summarization, memory, subagents, skills paths
- **`extensions_config.json`** — MCP server configs and skills enabled/disabled state
- **`.env`** — API keys and secrets
- **Environment variables** — Override any config value

The config system supports **hot-reload**: `config.yaml` changes are picked up on the next config access without restart.

---

## 5. The Agent Loop — How It Works

### 5.1 Request Flow (End-to-End)

```
User Message → Nginx → LangGraph Server → make_lead_agent()
                                              │
                                              ▼
                                    ┌─────────────────┐
                                    │ Middleware Chain  │
                                    │ (before_agent)   │
                                    │ - Set up paths   │
                                    │ - Inject uploads  │
                                    │ - Acquire sandbox │
                                    │ - Summarize if    │
                                    │   needed          │
                                    └────────┬──────────┘
                                             │
                                             ▼
                                    ┌─────────────────┐
                                    │   LLM Call       │
                                    │ (with system     │
                                    │  prompt + tools) │
                                    └────────┬──────────┘
                                             │
                              ┌──────────────┼──────────────┐
                              │              │              │
                         Text Response  Tool Calls    Thinking
                              │              │              │
                              │              ▼              │
                              │     ┌────────────────┐     │
                              │     │ Execute Tools   │     │
                              │     │ (bash, search,  │     │
                              │     │  read_file,     │     │
                              │     │  task, etc.)    │     │
                              │     └────────┬────────┘     │
                              │              │              │
                              │              ▼              │
                              │     Tool Results added      │
                              │     to messages             │
                              │              │              │
                              │              ▼              │
                              │     ┌────────────────┐     │
                              │     │ LLM Call Again  │     │
                              │     │ (with tool      │     │
                              │     │  results)       │     │
                              │     └────────┬────────┘     │
                              │              │              │
                              └──────────────┼──────────────┘
                                             │
                                    (Loop until no more tool calls)
                                             │
                                             ▼
                                    ┌─────────────────┐
                                    │ Middleware Chain  │
                                    │ (after_agent)    │
                                    │ - Queue memory   │
                                    │   update         │
                                    │ - Generate title │
                                    │ - Release sandbox│
                                    └────────┬──────────┘
                                             │
                                             ▼
                                    SSE Stream → Client
```

### 5.2 The Core Loop (LangGraph ReAct Pattern)

DeerFlow uses LangGraph's **ReAct (Reasoning + Acting)** pattern:

1. **Reason**: The LLM receives the conversation history (including system prompt, user message, and any tool results) and decides what to do next
2. **Act**: If the LLM outputs tool calls, those tools are executed
3. **Observe**: Tool results are appended to the message history
4. **Repeat**: The LLM is called again with the updated history, until it produces a final text response with no tool calls

This loop is managed by LangGraph's `create_agent()` which creates a graph with:
- A **model node** that calls the LLM
- A **tools node** that executes tool calls
- **Conditional edges** that route back to the model if there are tool calls, or end if there are none

### 5.3 Streaming

DeerFlow supports real-time SSE streaming with multiple stream modes:

- **`values`**: Full state snapshots (title, messages, artifacts)
- **`messages-tuple`**: Per-message updates (AI text chunks, tool calls, tool results)
- **`updates`**: Node-level state writes
- **`end`**: Stream completion with usage statistics

The streaming pipeline:
```
Agent.astream() → StreamBridge → SSE Consumer → Nginx → Client
```

The `StreamBridge` is an async pub/sub system that decouples the agent execution from HTTP response delivery, supporting both `cancel` and `continue` disconnect modes.

---

## 6. Prompt Design — System Prompt Engineering

### 6.1 System Prompt Structure

The system prompt is dynamically assembled by `apply_prompt_template()` in `agents/lead_agent/prompt.py`. It uses XML-tagged sections for clear semantic boundaries:

```xml
<role>
You are {agent_name}, an open-source super agent.
</role>

<soul>
{Custom agent personality from SOUL.md}
</soul>

<memory>
{Injected long-term memory context}
</memory>

<thinking_style>
{Thinking guidelines — concise, strategic, outline-only}
</thinking_style>

<clarification_system>
{Clarification workflow: CLARIFY → PLAN → ACT}
{5 mandatory clarification scenarios with examples}
</clarification_system>

<skill_system>
{Available skills with progressive loading pattern}
{Skill names, descriptions, and file locations}
</skill_system>

<available-deferred-tools>
{Names of deferred tools discoverable via tool_search}
</available-deferred-tools>

<subagent_system>
{Subagent orchestration instructions — only if enabled}
{Decompose → Delegate → Synthesize pattern}
{Hard concurrency limit enforcement}
</subagent_system>

<working_directory>
{File system layout: uploads, workspace, outputs}
{File management instructions}
</working_directory>

<response_style>
{Clear, concise, natural tone, action-oriented}
</response_style>

<citations>
{Citation format and workflow for research tasks}
</citations>

<critical_reminders>
{Key rules: clarification first, skill first, progressive loading, etc.}
</critical_reminders>

<current_date>2026-03-31, Monday</current_date>
```

### 6.2 Key Prompt Design Principles

1. **XML-Tagged Sections**: Each concern is wrapped in XML tags (`<role>`, `<memory>`, `<skill_system>`, etc.) for clear semantic boundaries that LLMs can parse reliably.

2. **Dynamic Assembly**: The prompt is not static — it is assembled at agent creation time based on:
   - Whether subagent mode is enabled
   - Which skills are available
   - What memory context exists
   - Whether the model supports vision
   - Whether tool_search is enabled
   - Custom agent personality (SOUL.md)

3. **Progressive Loading**: Skills are listed by name/description only. The agent must call `read_file` to load the full skill content when needed, keeping the initial context window lean.

4. **Clarification-First Workflow**: The prompt enforces a strict `CLARIFY → PLAN → ACT` workflow, requiring the agent to ask for clarification before starting work when requirements are unclear.

5. **Subagent Orchestration**: When enabled, the prompt includes detailed instructions for task decomposition with hard concurrency limits, batch execution patterns, and counter-examples.

### 6.3 Memory Injection

Memory is injected into the system prompt as a `<memory>` section containing:
- **User Context**: Work context, personal context, current focus
- **History**: Recent months, earlier context, long-term background
- **Facts**: Ranked by confidence, formatted as `[category | confidence] content`

Token budget is enforced — facts are added incrementally until the `max_injection_tokens` limit is reached.

### 6.4 Agent Personality (SOUL.md)

Custom agents can have a `SOUL.md` file that defines their personality, expertise, and behavioral guidelines. This is injected into the `<soul>` section of the system prompt.

---

## 7. Multi-Agent Design & Communication

### 7.1 Architecture: Lead Agent + Sub-Agents

DeerFlow uses a **hierarchical multi-agent architecture**:

```
┌─────────────────────────────────────────────┐
│              Lead Agent                      │
│  (Orchestrator — full context, all tools)    │
│                                              │
│  Decides: direct execution vs. delegation    │
└──────────┬──────────┬──────────┬─────────────┘
           │          │          │
           ▼          ▼          ▼
    ┌──────────┐ ┌──────────┐ ┌──────────┐
    │ Sub-Agent│ │ Sub-Agent│ │ Sub-Agent│
    │ (general │ │ (general │ │  (bash)  │
    │ purpose) │ │ purpose) │ │          │
    │          │ │          │ │          │
    │ Isolated │ │ Isolated │ │ Isolated │
    │ context  │ │ context  │ │ context  │
    └──────────┘ └──────────┘ └──────────┘
```

### 7.2 Sub-Agent Types

- **`general-purpose`**: A capable agent for complex, multi-step tasks requiring exploration and action. Has access to web search, file operations, and other tools.
- **`bash`**: Command execution specialist for running bash commands. Only available when host bash is allowed or using AioSandboxProvider.

### 7.3 Communication Pattern

Sub-agents communicate with the lead agent through the **`task` tool**:

1. **Lead Agent** calls `task(description, prompt, subagent_type)` — this is a standard LLM tool call
2. **`task_tool`** creates a `SubagentExecutor` and starts background execution via `execute_async()`
3. The executor runs in a **ThreadPoolExecutor** with timeout support
4. **Polling loop**: `task_tool` polls every 5 seconds for completion, streaming progress events via `get_stream_writer()`
5. On completion, the result is returned as a tool result to the lead agent

### 7.4 Context Isolation

Each sub-agent runs in **complete context isolation**:
- Its own message history (starts fresh with just the task prompt)
- Its own system prompt (subagent-specific, not the lead agent's)
- Shared sandbox and thread data (for file access)
- No access to the lead agent's conversation history
- No access to other sub-agents' contexts

### 7.5 Parallel Execution

Sub-agents can run in parallel — the lead agent can issue multiple `task` tool calls in a single response. The system enforces a **hard concurrency limit** (configurable, default 3) via `SubagentLimitMiddleware`, which silently truncates excess calls.

For tasks requiring more than `max_concurrent_subagents` sub-tasks, the prompt instructs the agent to use **multi-batch execution** across multiple turns.

### 7.6 Subagent Lifecycle

```
task_tool called → SubagentExecutor created → execute_async()
                                                    │
                                    ┌───────────────┘
                                    ▼
                          _scheduler_pool.submit(run_task)
                                    │
                                    ▼
                          _execution_pool.submit(self.execute)
                                    │
                                    ▼
                          asyncio.run(self._aexecute(task))
                                    │
                                    ▼
                          create_agent() → agent.astream()
                                    │
                                    ▼
                          (ReAct loop with tools)
                                    │
                                    ▼
                          SubagentResult returned
                                    │
                                    ▼
                          task_tool returns result to lead agent
```

---

## 8. Runtime Architecture

### 8.1 Run Lifecycle

The runtime manages the lifecycle of agent runs:

```
RunStatus: pending → running → success | error | interrupted
```

Key components:
- **`RunManager`**: Creates, tracks, and cancels runs. Enforces single-run-per-thread semantics.
- **`run_agent()`**: The background worker that executes the agent graph, publishing events to the StreamBridge.
- **`StreamBridge`**: Async pub/sub for SSE events. Supports multiple subscribers per run.

### 8.2 Checkpointing

DeerFlow uses LangGraph's checkpointing system for state persistence:
- **SQLite-based** checkpointer for local development
- Stores full thread state (messages, title, artifacts, todos, etc.) after each agent step
- Enables **multi-turn conversations** — state is restored from checkpoint on each new message
- Supports **rollback** (Phase 2, not yet fully implemented)

### 8.3 Store

The Store provides a key-value persistence layer for thread metadata:
- Thread creation timestamps, titles, and metadata
- Used by `/threads/search` for listing conversations
- SQLite-backed with async support

---

## 9. Memory System — Long-Term Persistence

### 9.1 Architecture

```
Conversation → MemoryMiddleware → MemoryQueue → MemoryUpdater → LLM → Storage
                (after_agent)     (debounced)    (summarize)           (JSON file)
```

### 9.2 Memory Structure

```json
{
  "user": {
    "workContext": {"summary": "...", "updatedAt": "..."},
    "personalContext": {"summary": "...", "updatedAt": "..."},
    "topOfMind": {"summary": "...", "updatedAt": "..."}
  },
  "history": {
    "recentMonths": {"summary": "...", "updatedAt": "..."},
    "earlierContext": {"summary": "...", "updatedAt": "..."},
    "longTermBackground": {"summary": "...", "updatedAt": "..."}
  },
  "facts": [
    {
      "id": "fact_abc12345",
      "content": "User prefers TypeScript over JavaScript",
      "category": "preference",
      "confidence": 0.9,
      "createdAt": "2026-03-31T07:00:00Z",
      "source": "thread-123"
    }
  ]
}
```

### 9.3 Memory Update Flow

1. **MemoryMiddleware** (after_agent): Filters messages to keep only user inputs and final assistant responses, then queues them
2. **MemoryQueue**: Debounces updates (configurable delay) to batch multiple conversation turns
3. **MemoryUpdater**: Sends the conversation + current memory to an LLM with `MEMORY_UPDATE_PROMPT`
4. The LLM returns structured JSON with updates to user context, history, new facts, and facts to remove
5. **Deduplication**: New facts are checked against existing facts by content to prevent duplicates
6. **Upload filtering**: File upload mentions are stripped from memory to prevent stale path references
7. **Persistence**: Updated memory is saved to a JSON file

### 9.4 Memory Injection

At agent creation time, `_get_memory_context()` loads the memory data and formats it for injection into the system prompt using `format_memory_for_injection()`. Facts are ranked by confidence and included up to the token budget.

---

## 10. Context Compression & Summarization

### 10.1 SummarizationMiddleware

DeerFlow uses LangChain's `SummarizationMiddleware` to manage context window pressure:

- **Trigger conditions** (OR logic): token count, message count, or fraction of model's max input
- **Retention policy**: Keep N recent messages (or tokens/fraction) intact
- **Summary generation**: Older messages are summarized by an LLM into a single HumanMessage
- **AI/Tool pair protection**: Never splits an AI message from its corresponding tool messages

### 10.2 Configuration Example

```yaml
summarization:
  enabled: true
  model_name: gpt-4o-mini  # Lightweight model for cost efficiency
  trigger:
    - type: tokens
      value: 6000
    - type: messages
      value: 75
  keep:
    type: messages
    value: 25
  trim_tokens_to_summarize: 5000
```

### 10.3 Other Context Management Strategies

- **Sub-agent context isolation**: Each sub-agent starts with a clean context, preventing context bloat
- **Progressive skill loading**: Skills are loaded on-demand, not all at once
- **Deferred tool schemas**: MCP tool schemas are hidden until explicitly searched, reducing prompt size
- **File offloading**: Intermediate results are written to the filesystem rather than kept in context

---

## 11. Session & Thread Management

### 11.1 Thread Model

Each conversation is a **thread** identified by a UUID:
- **Thread state** is persisted via the checkpointer (messages, title, artifacts, etc.)
- **Thread data** is stored on the filesystem (`.deer-flow/threads/{thread_id}/`)
- **Thread metadata** is stored in the Store (creation time, title, etc.)

### 11.2 Thread Data Layout

```
.deer-flow/threads/{thread_id}/
├── user-data/
│   ├── workspace/    # Agent's working directory
│   ├── uploads/      # User-uploaded files
│   └── outputs/      # Final deliverables
└── acp-workspace/    # ACP agent workspace (if used)
```

### 11.3 Virtual Path Mapping

DeerFlow uses virtual paths that are consistent across local and Docker sandbox modes:

| Virtual Path | Physical Path (Local) |
|-------------|----------------------|
| `/mnt/user-data/workspace` | `.deer-flow/threads/{id}/user-data/workspace` |
| `/mnt/user-data/uploads` | `.deer-flow/threads/{id}/user-data/uploads` |
| `/mnt/user-data/outputs` | `.deer-flow/threads/{id}/user-data/outputs` |
| `/mnt/skills` | `deer-flow/skills/` |
| `/mnt/acp-workspace` | `.deer-flow/threads/{id}/acp-workspace/` |

---

## 12. Skills System

### 12.1 What is a Skill?

A skill is a **structured capability module** — a Markdown file (`SKILL.md`) that defines a workflow, best practices, and references to supporting resources. Skills are the primary mechanism for extending DeerFlow's capabilities.

### 12.2 Skill Structure

```
skills/public/deep-research/
├── SKILL.md              # Main skill file with frontmatter + instructions
├── references/           # Supporting reference documents
│   └── methodology.md
├── scripts/              # Helper scripts
│   └── analyze.py
└── templates/            # Output templates
    └── report.md
```

### 12.3 SKILL.md Format

```markdown
---
name: Deep Research
description: Conduct thorough research on any topic
license: MIT
allowed-tools:
  - web_search
  - web_fetch
  - read_file
  - write_file
  - bash
---

# Deep Research Workflow

1. Understand the research question
2. Search for primary sources
3. Analyze and synthesize findings
4. Generate a comprehensive report

## References
- See `references/methodology.md` for research methodology
```

### 12.4 Progressive Loading Pattern

Skills are NOT loaded into the system prompt in full. Instead:

1. The system prompt lists available skills with name, description, and file path
2. When a task matches a skill, the agent calls `read_file` on the skill's SKILL.md
3. The skill file references additional resources under the same folder
4. Resources are loaded incrementally as needed during execution

This keeps the initial context window lean and works well with token-sensitive models.

### 12.5 Skill Discovery & Loading

`load_skills()` in `skills/loader.py`:
1. Scans `skills/public/` and `skills/custom/` directories
2. Parses `SKILL.md` frontmatter for metadata
3. Checks `extensions_config.json` for enabled/disabled state
4. Returns sorted list of `Skill` objects

---

## 13. Tool System & Tool Calling

### 13.1 Tool Sources

Tools come from four sources:

1. **Configured tools** (from `config.yaml`): `web_search`, `web_fetch`, `bash`, `read_file`, `write_file`, `str_replace`, `ls`
2. **Built-in tools**: `present_file`, `ask_clarification`, `view_image`, `task` (subagent), `tool_search`
3. **MCP tools** (from `extensions_config.json`): Any tool exposed by configured MCP servers
4. **ACP tools**: `invoke_acp_agent` for external agent protocols (Codex, Claude Code)

### 13.2 Tool Resolution

`get_available_tools()` assembles the complete tool list:

```python
def get_available_tools(...) -> list[BaseTool]:
    # 1. Load configured tools from config.yaml
    loaded_tools = [resolve_variable(tool.use, BaseTool) for tool in config.tools]
    
    # 2. Add built-in tools
    builtin_tools = [present_file, ask_clarification]
    if subagent_enabled: builtin_tools.append(task_tool)
    if model_supports_vision: builtin_tools.append(view_image_tool)
    
    # 3. Load MCP tools (with optional deferred loading)
    mcp_tools = get_cached_mcp_tools()
    if tool_search_enabled:
        # Register MCP tools in deferred registry
        # Add tool_search tool instead of individual MCP tools
        registry = DeferredToolRegistry()
        for t in mcp_tools: registry.register(t)
        builtin_tools.append(tool_search_tool)
    
    # 4. Add ACP tools if configured
    acp_tools = [build_invoke_acp_agent_tool(acp_agents)]
    
    return loaded_tools + builtin_tools + mcp_tools + acp_tools
```

### 13.3 Deferred Tool Loading (tool_search)

When `tool_search` is enabled, MCP tools are NOT included in the agent's tool binding. Instead:

1. Tool names are listed in `<available-deferred-tools>` in the system prompt
2. The agent calls `tool_search(query)` to discover tools by name/description
3. `tool_search` returns full OpenAI function schemas for matched tools
4. The `DeferredToolFilterMiddleware` promotes matched tools so they can be called
5. On subsequent LLM calls, the promoted tools are included in `bind_tools()`

This dramatically reduces prompt size when many MCP tools are configured.

### 13.4 Sandbox Tools

Sandbox tools (`bash`, `ls`, `read_file`, `write_file`, `str_replace`) are the agent's interface to the execution environment. They handle:

- **Virtual path resolution**: Translating `/mnt/user-data/*` to physical paths
- **Security validation**: Path traversal prevention, permission checks
- **Local vs. Docker routing**: Different code paths for local and container execution
- **Output masking**: Replacing physical paths with virtual paths in tool output

---

## 14. LLM Invocation — Model Factory

### 14.1 Model Creation

`create_chat_model()` in `models/factory.py`:

1. Loads model config from `config.yaml` by name
2. Resolves the LangChain class via reflection (e.g., `langchain_openai:ChatOpenAI`)
3. Applies thinking mode settings (extended thinking, reasoning effort)
4. Attaches LangSmith tracing if enabled
5. Returns a `BaseChatModel` instance

### 14.2 Supported Providers

| Provider | Class Path | Notes |
|----------|-----------|-------|
| OpenAI | `langchain_openai:ChatOpenAI` | Supports Responses API |
| Anthropic | `langchain_anthropic:ChatAnthropic` | Extended thinking support |
| DeepSeek | `langchain_deepseek:ChatDeepSeek` | Patched for compatibility |
| Codex CLI | `deerflow.models.openai_codex_provider:CodexChatModel` | CLI-backed |
| Claude Code | `deerflow.models.claude_provider:ClaudeChatModel` | OAuth-backed |
| OpenRouter | `langchain_openai:ChatOpenAI` + `base_url` | Gateway compatibility |

### 14.3 Thinking Mode

DeerFlow supports extended thinking for models that support it:
- `thinking_enabled: true` activates the model's reasoning capabilities
- `reasoning_effort` controls the depth (low/medium/high/xhigh)
- Fallback to non-thinking mode if the model doesn't support it

---

## 15. Sandbox & File System

### 15.1 Sandbox Providers

- **`LocalSandboxProvider`**: Direct execution on the host. File tools map to per-thread directories. Host bash is disabled by default.
- **`AioSandboxProvider`**: Docker-based isolated containers. Full filesystem isolation with mounted volumes.

### 15.2 Lazy Initialization

Sandboxes are acquired lazily — `ensure_sandbox_initialized()` is called on the first tool use, not at agent creation time. This avoids unnecessary container creation for simple conversations.

### 15.3 Security

- **Path traversal prevention**: All paths are validated against allowed roots
- **Virtual path enforcement**: Agents can only access `/mnt/user-data/*`, `/mnt/skills/*`, and `/mnt/acp-workspace/*`
- **Host bash gating**: Host bash is disabled by default in local mode; must be explicitly enabled
- **Output masking**: Physical paths are replaced with virtual paths in all tool output

---

## 16. Embedded Python SDK (DeerFlowClient)

The `DeerFlowClient` provides direct in-process access to all agent capabilities without running HTTP services:

```python
from deerflow.client import DeerFlowClient

client = DeerFlowClient(
    model_name="gpt-4",
    thinking_enabled=True,
    subagent_enabled=False,
)

# One-shot chat
response = client.chat("Analyze this paper for me", thread_id="my-thread")

# Streaming
for event in client.stream("hello"):
    if event.type == "messages-tuple" and event.data.get("type") == "ai":
        print(event.data["content"])

# Configuration
models = client.list_models()
skills = client.list_skills()

# Memory management
client.create_memory_fact("User prefers Python", category="preference")
memory = client.get_memory()

# File uploads
client.upload_files("thread-1", ["./report.pdf"])

# MCP configuration
client.update_mcp_config({"github": {...}})
```

The SDK creates the same agent internally, with lazy initialization and config-change detection for automatic recreation.

---

## 17. User Interaction & Frontend

### 17.1 Chat Interface

The frontend provides a rich chat interface with:
- **Streaming message display** with markdown rendering
- **Chain-of-thought visualization** (thinking/reasoning display)
- **Task progress tracking** (sub-agent status, todo lists)
- **Artifact viewer** (code, files, charts)
- **Model selector** with thinking mode toggle
- **File upload** with drag-and-drop
- **Follow-up suggestions**

### 17.2 Execution Modes

The frontend supports multiple execution modes:
- **Flash**: Fast, minimal processing
- **Standard**: Default balanced mode
- **Pro (Plan Mode)**: TodoList-based task tracking
- **Ultra (Sub-Agent Mode)**: Full sub-agent decomposition

### 17.3 Settings

- Model selection and configuration
- Memory management (view, edit, clear)
- MCP server configuration
- Skills management (enable/disable)

---

## 18. Key Technical Challenges

1. **Context Window Management**: Balancing information richness with token limits through summarization, progressive loading, and deferred tools
2. **Multi-Agent Coordination**: Ensuring sub-agents have enough context without leaking the lead agent's full state
3. **Sandbox Security**: Preventing path traversal and unauthorized access while maintaining usability
4. **Memory Quality**: Extracting meaningful long-term facts from conversations without accumulating noise
5. **Streaming Reliability**: Maintaining SSE connections across long-running tasks with proper disconnect handling
6. **Tool Error Recovery**: Gracefully handling tool failures without breaking the agent loop
7. **Configuration Hot-Reload**: Supporting config changes without service restart across multiple processes

---

## 19. Areas Worth Further Investigation

1. **Checkpoint Rollback**: Phase 2 rollback is stubbed but not implemented — how will it handle mid-conversation state reversion?
2. **Memory Conflict Resolution**: How does the system handle contradictory facts from different sessions?
3. **Sub-Agent Nesting Prevention**: Currently prevented by excluding task_tool from sub-agents — what about indirect nesting via MCP?
4. **Token Counting Accuracy**: The system uses approximate counting (char/4 fallback) — how does this affect summarization trigger accuracy?
5. **MCP Tool Caching**: Cache invalidation is based on file mtime — what about remote MCP servers that change without file updates?
6. **Guardrails System**: The guardrails module exists but its integration depth and customization options need exploration
7. **ACP Agent Integration**: The Codex/Claude Code integration via ACP is relatively new — edge cases and failure modes
8. **Frontend State Management**: How does the frontend handle reconnection after SSE disconnects?
9. **Concurrent Thread Safety**: How does the system handle multiple simultaneous conversations?
10. **Skill Composition**: Can skills reference or compose with other skills?

---

## 20. Summary

DeerFlow 2.0 is a sophisticated, production-ready agent harness that demonstrates several key architectural decisions:

- **Middleware-based extensibility**: The middleware chain pattern allows clean separation of concerns and easy addition of new capabilities
- **Hierarchical multi-agent design**: The lead agent + sub-agent pattern provides both simplicity (single entry point) and power (parallel decomposition)
- **Progressive context engineering**: Skills, tools, and memory are loaded incrementally to manage context window pressure
- **Dual execution model**: Local development and Docker production share the same virtual path abstraction
- **LLM-driven memory**: Memory updates are generated by LLMs, not rule-based extraction, enabling nuanced understanding
- **Configuration-driven architecture**: Nearly everything is configurable via YAML/JSON without code changes

The project is well-structured with clear module boundaries, comprehensive test coverage (70+ test files), and thorough documentation. It represents a mature approach to building agent systems that can handle real-world complexity.
