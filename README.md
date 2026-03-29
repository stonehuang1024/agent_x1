<p align="center">
  <h1 align="center">🤖 Agent X1</h1>
  <p align="center">
    <strong>Modular, Multi-Provider LLM Agent System with Tool Calling & Skill Framework</strong>
  </p>
  <p align="center">
    <code>v1.0.0</code> · Python 3.10+ · 70 source files · 22K+ lines of code
  </p>
</p>

---

## Table of Contents

- [Quick Start](#quick-start)
- [Usage](#usage)
- [Architecture Overview](#architecture-overview)
- [Project Structure](#project-structure)
- [Core Modules](#core-modules)
- [Tool System](#tool-system)
- [Skill Framework](#skill-framework)
- [Configuration](#configuration)
- [Observability & Logging](#observability--logging)
- [Testing](#testing)
- [Tech Stack](#tech-stack)

---

## Quick Start

### 1. Install Dependencies

```bash
# Clone the repository
git clone <repo-url> && cd agent_x1

# Create virtual environment
python -m venv venv
source venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure API Key

```bash
# Option A: Environment variable
export ANTHROPIC_API_KEY="your-api-key"
export ANTHROPIC_BASE_URL="https://api.kimi.com/coding/"
export ENABLE_TOOL_SEARCH=false

# optional
export KIMI_API_KEY="" 
# optional
export EXA_API_KEY=""
# optional
export SERPAPI_KEY=""

# Option B: Edit config file
python main.py --init-config
# Then edit config/config.yaml with your API key
```

### 3. Run

```bash
# Interactive mode (default)
python main.py

# Single query
python main.py --query "Analyze the latest trends in recommendation systems"

# With specific provider
python main.py --provider anthropic --model kimi-k2.5

# Verbose output
python main.py --verbose

# Debug mode
python main.py --debug
```

---

## Usage

### Interactive Mode

```bash
python main.py
```

Available commands in interactive mode:

| Command | Description |
|---------|-------------|
| `/help` | Show help information |
| `/tools` | List all registered tools |
| `/skills` | List available skills |
| `/skill <name>` | Activate a specific skill |
| `/clear` | Clear conversation history |
| `/history` | Show recent conversation history |
| `/quit` | Exit the agent |

### Single Query Mode

```bash
python main.py --query "Search arXiv for papers on CTR prediction published in 2025"
```

### Session Recovery

```bash
# Resume the most recent session
python main.py --continue

# Resume a specific session by ID
python main.py --resume <session-id>
```

### CLI Options

```
python main.py [OPTIONS]

Options:
  -c, --config PATH        Path to config file (YAML)
  -p, --provider TYPE      LLM provider: kimi | anthropic | openai
  -m, --model NAME         Model identifier
  -q, --query TEXT         Single query mode
  -C, --continue           Resume most recent session
  --resume SESSION_ID      Resume specific session
  --api-key KEY            API key (overrides config/env)
  --log-level LEVEL        DEBUG | INFO | WARNING | ERROR
  --verbose, -v            Verbose output
  --debug                  Debug output (implies --verbose)
  --init-config            Generate default config file
  --new-arch               Use AgentLoop architecture (default: true)
```

### Programmatic API

```python
from src import create_agent

# Create a fully configured agent
agent = create_agent("config/config.yaml")

# Chat
response = agent.chat("What is the AUC of DeepFM on Criteo dataset?")
print(response)
```

---

## Architecture Overview

Agent X1 follows a **layered architecture** with clear separation of concerns. The system is built around an event-driven `AgentLoop` that orchestrates LLM calls, tool execution, context management, and session persistence.

### System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                          main.py (Entry Point)                      │
│                   CLI parsing · Config loading · Bootstrap           │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│   Engine Layer   │ │  Runtime Layer   │ │  Session Layer   │
│                  │ │                  │ │                  │
│ BaseEngine       │ │ AgentLoop        │ │ SessionManager   │
│ ├─ KimiEngine    │ │ ToolScheduler    │ │ SessionStore     │
│ └─ AnthropicEng  │ │ LoopDetector     │ │ SessionLogger    │
│                  │ │                  │ │ TranscriptWriter │
│ EngineRegistry   │ │ AgentConfig      │ │ DiffTracker      │
│ EngineConfig     │ │ AgentState       │ │ SessionIndex     │
└────────┬─────────┘ └────────┬─────────┘ └────────┬─────────┘
         │                    │                     │
         │         ┌──────────┴──────────┐          │
         │         ▼                     ▼          │
         │  ┌──────────────┐  ┌──────────────────┐  │
         │  │Context Layer │  │  Memory Layer    │  │
         │  │              │  │                  │  │
         │  │ Assembler    │  │ MemoryController │  │
         │  │ Compressor   │  │ MemoryStore      │  │
         │  │ ContextWindow│  │ ProjectMemory    │  │
         │  │ ImportScore  │  │ EpisodicMemory   │  │
         │  │ SysReminder  │  │ SemanticMemory   │  │
         │  └──────────────┘  └──────────────────┘  │
         │                                          │
    ┌────┴──────────────────────────────────────────┴────┐
    │                    Tool System                      │
    │                                                     │
    │  ┌─────────┐ ┌──────┐ ┌─────┐ ┌──────┐ ┌───────┐  │
    │  │ search  │ │ file │ │ web │ │ data │ │ arxiv │  │
    │  │ stock   │ │ bash │ │ pdf │ │ ppt  │ │reader │  │
    │  │economics│ │ code │ │     │ │      │ │       │  │
    │  └─────────┘ └──────┘ └─────┘ └──────┘ └───────┘  │
    └────────────────────────────────────────────────────┘
    ┌────────────────────────────────────────────────────┐
    │                  Cross-Cutting                      │
    │                                                     │
    │  EventBus · Prompt · Skills · Observability(Util)   │
    └────────────────────────────────────────────────────┘
```

### Data Flow

```
User Input
    │
    ▼
┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  AgentLoop  │────▶│ ContextAssembler │────▶│  Engine.call_llm│
│  (Runtime)  │     │ (Build Messages) │     │  (API Request)  │
└──────┬──────┘     └──────────────────┘     └────────┬────────┘
       │                                              │
       │            ┌──────────────────┐              │
       │            │   LLM Response   │◀─────────────┘
       │            │ (text/tool_calls)│
       │            └────────┬─────────┘
       │                     │
       │         ┌───────────┴───────────┐
       │         ▼                       ▼
       │  ┌─────────────┐       ┌──────────────┐
       │  │ Text Output │       │ToolScheduler │
       │  │ (Final Resp)│       │(Execute Tools)│
       │  └─────────────┘       └──────┬───────┘
       │                               │
       │         ┌─────────────────────┘
       │         ▼
       │  ┌──────────────┐
       └──│ Next Iteration│──▶ (loop until text response or max_iterations)
          └──────────────┘
```

### Event System

All modules communicate through a central `EventBus` for loose coupling:

```
EventBus
  ├── SESSION_CREATED / COMPLETED / FAILED / RESUMED
  ├── TURN_STARTED / COMPLETED / FAILED
  ├── LLM_CALL_STARTED / COMPLETED / FAILED
  ├── TOOL_CALLED / SUCCEEDED / FAILED
  ├── CONTEXT_ASSEMBLED / COMPRESSED
  ├── MEMORY_STORED / RETRIEVED / EXPIRED
  └── LOOP_STARTED / ITERATION / DETECTED / COMPLETED
```

---

## Project Structure

```
agent_x1/
├── main.py                          # Entry point: CLI, bootstrap, run modes
├── requirements.txt                 # Python dependencies
├── config/
│   └── config.yaml                  # Main configuration file
│
├── src/                             # Source code (70 files, 22K+ lines)
│   ├── __init__.py                  # Package init, create_agent() factory
│   │
│   ├── core/                        # Foundation layer
│   │   ├── __init__.py              # Exports: Message, Role, Tool, Config, EditManager
│   │   ├── models.py               # Message, Role data models
│   │   ├── tool.py                  # Tool wrapper, ToolRegistry, timeout/safety
│   │   ├── config.py               # AppConfig, LLMConfig, PathConfig, ToolSafetyConfig
│   │   ├── events.py               # EventBus, AgentEvent, EventPayload
│   │   ├── edit_manager.py         # DiffParser, SearchEngine, EditApplier, FileEditingGuard
│   │   └── session_manager.py      # Legacy session manager (backward compat)
│   │
│   ├── engine/                      # LLM provider abstraction
│   │   ├── __init__.py              # EngineRegistry, create_engine() factory
│   │   ├── base.py                  # BaseEngine abstract class, EngineConfig, ProviderType
│   │   ├── kimi_engine.py          # Kimi (OpenAI-compatible) implementation
│   │   └── anthropic_engine.py     # Anthropic-style API implementation
│   │
│   ├── runtime/                     # Agent execution runtime
│   │   ├── __init__.py              # Exports: AgentLoop, ToolScheduler, LoopDetector
│   │   ├── agent_loop.py           # Core agent loop: LLM ↔ Tool iteration cycle
│   │   ├── tool_scheduler.py       # Parallel tool execution with timeout
│   │   ├── loop_detector.py        # Infinite loop detection (similarity-based)
│   │   └── models.py               # AgentState, AgentConfig, ToolCallRecord
│   │
│   ├── session/                     # Session persistence & lifecycle
│   │   ├── __init__.py              # Exports: Session, SessionManager, Transcript, etc.
│   │   ├── models.py               # Session, Turn, Checkpoint, TokenBudget, SessionSummary
│   │   ├── session_manager.py      # Session CRUD, state transitions, recovery
│   │   ├── session_store.py        # SQLite-backed session persistence
│   │   ├── session_logger.py       # Markdown session log writer (session_llm.md)
│   │   ├── session_index.py        # JSON index for quick session lookup
│   │   ├── transcript.py           # JSONL transcript writer/reader
│   │   └── diff_tracker.py         # File change tracking per session
│   │
│   ├── memory/                      # Long-term memory system
│   │   ├── __init__.py              # Exports: MemoryController, MemoryStore, models
│   │   ├── models.py               # EpisodicMemory, SemanticMemory, ProjectMemoryFile
│   │   ├── memory_store.py         # SQLite-backed memory persistence
│   │   ├── memory_controller.py    # Memory CRUD, search, relevance scoring
│   │   └── project_memory.py       # PROJECT.md loader for project conventions
│   │
│   ├── context/                     # Context window management
│   │   ├── __init__.py              # Exports: ContextAssembler, Compressor, Window
│   │   ├── context_assembler.py    # Multi-layer context assembly pipeline
│   │   ├── context_compressor.py   # Token-aware message compression
│   │   ├── context_window.py       # Sliding window with budget tracking
│   │   ├── importance_scorer.py    # Message importance scoring for compression
│   │   └── system_reminder.py      # Dynamic system reminder injection
│   │
│   ├── prompt/                      # Prompt engineering
│   │   ├── __init__.py              # Exports: PromptProvider, PromptContext
│   │   ├── prompt_provider.py      # Dynamic prompt assembly with mode/skill awareness
│   │   ├── sections.py             # Prompt section builders (tools, guidelines, etc.)
│   │   └── templates/
│   │       ├── base_system.md      # Base system prompt template
│   │       └── compression.md     # Compression instruction template
│   │
│   ├── tools/                       # 60+ LLM-callable tools (13 categories)
│   │   ├── __init__.py              # Tool registration, ALL_TOOLS, TOOL_CATEGORIES_MAP
│   │   ├── tool_registry.py        # CategorizedToolRegistry with search
│   │   ├── example_tools.py        # Calculator, knowledge search
│   │   ├── search_tools.py         # Google (SerpAPI), Exa AI neural search
│   │   ├── stock_tools.py          # Stock kline, snapshot, financials, info
│   │   ├── stock_analysis.py       # Comprehensive stock analysis
│   │   ├── economics_tools.py      # FRED, World Bank, exchange rates, calendar
│   │   ├── file_tools.py           # File CRUD, search, move, copy, edit
│   │   ├── bash_tools.py           # Shell commands, Python/Bash scripts, system info
│   │   ├── pdf_tools.py            # PDF read, merge, split, create, extract images
│   │   ├── ppt_tools.py            # PowerPoint create, read, add slide, export
│   │   ├── web_tools.py            # URL fetch, scrape, links, download, RSS
│   │   ├── data_tools.py           # CSV/JSON/Excel read, analyze, filter, convert
│   │   ├── reader_tools.py         # URL/PDF/HTML → Markdown conversion
│   │   ├── arxiv_tools.py          # arXiv search, paper details, PDF download
│   │   └── codebase_search_tools.py # grep, glob, ls for code exploration
│   │
│   ├── skills/                      # Pluggable skill framework
│   │   ├── __init__.py              # Exports: SkillRegistry, SkillContextManager
│   │   ├── models.py               # SkillSpec, SkillSummary, SkillPhase, SkillArtifact
│   │   ├── loader.py               # SKILL.md parser → SkillSpec
│   │   ├── registry.py             # Skill discovery and indexing
│   │   ├── context_manager.py      # Prompt assembly, tool filtering, lifecycle
│   │   └── workspace.py            # Session-scoped skill working directories
│   │
│   └── util/                        # Observability & utilities
│       ├── logger.py               # Structured logging with session binding
│       ├── structured_log.py       # JSONL structured event logger
│       ├── display.py              # ConsoleDisplay with color/formatting
│       ├── activity_stream.py      # Real-time activity stream output
│       ├── log_integration.py      # EventBus → Display/Logger wiring
│       ├── token_tracker.py        # Token usage tracking and reporting
│       ├── db.py                   # SQLite database utilities
│       ├── parallel.py             # Parallel execution helpers
│       └── mail_utils.py           # Email notification utilities
│
├── skills/                          # Skill definitions (SKILL.md files)
│   └── recommendation_research/
│       └── SKILL.md                 # Recommendation model research skill
│
├── tests/                           # Test suite (45 files, 16K+ lines)
│   ├── unit/                        # Unit tests
│   │   ├── test_tools_unit.py
│   │   ├── test_context_assembler.py
│   │   ├── test_context_compressor.py
│   │   ├── test_context_window.py
│   │   ├── test_importance_scorer.py
│   │   ├── test_edit_manager.py
│   │   ├── test_skills.py
│   │   ├── test_logger.py
│   │   ├── test_display.py
│   │   ├── test_activity_stream.py
│   │   ├── test_structured_log.py
│   │   ├── test_token_tracker.py
│   │   ├── test_arxiv_tools.py
│   │   ├── test_reader_tools.py
│   │   ├── test_codebase_search_tools.py
│   │   ├── test_system_reminder.py
│   │   ├── test_project_memory_loader.py
│   │   ├── test_tool_safety.py
│   │   ├── test_log_integration.py
│   │   ├── test_imports.py
│   │   └── test_session/            # Session subsystem tests
│   │       ├── test_models.py
│   │       ├── test_session_manager.py
│   │       ├── test_session_store.py
│   │       ├── test_session_logger.py
│   │       ├── test_session_index.py
│   │       ├── test_transcript.py
│   │       ├── test_diff_tracker.py
│   │       └── test_configurable_paths.py
│   └── integration/                 # Integration tests
│       ├── test_tools_integration.py
│       ├── test_context_pipeline.py
│       ├── test_session_integration.py
│       ├── test_debug_logging_e2e.py
│       ├── test_logging_display_e2e.py
│       ├── test_arxiv_integration.py
│       ├── test_reader_integration.py
│       ├── test_anthropic_kimi.py
│       └── test_kimi_api.py
│
├── data/                            # Runtime data
│   ├── agent_x1.db                  # SQLite database (sessions + memory)
│   ├── sessions-index.json          # Quick session lookup index
│   └── migrations/                  # Database migration scripts
│
├── results/                         # Output directory
│   ├── session/                     # Per-session output files
│   └── memory_data/                 # Memory export data
│
├── docs/                            # Reference documentation
│   ├── dev_doc/                     # Development design documents
│   ├── codex/                       # Codex architecture reference
│   ├── opencode/                    # OpenCode architecture reference
│   ├── gemini/                      # Gemini CLI architecture reference
│   ├── kimi/                        # Kimi architecture reference
│   └── prompt/                      # Prompt templates and test prompts
│
└── memory_data/                     # Historical session summaries
    └── history_session.md
```

---

## Core Modules

### 1. Core (`src/core/`)

The foundation layer providing shared data models, configuration, and cross-cutting concerns.

| File | Purpose |
|------|---------|
| `models.py` | `Message` and `Role` data classes for LLM conversation |
| `tool.py` | `Tool` wrapper with JSON Schema, timeout protection, output truncation; `ToolRegistry` |
| `config.py` | `AppConfig` → `LLMConfig` + `PathConfig` + `ToolSafetyConfig`; YAML/JSON/env loading |
| `events.py` | `EventBus` pub/sub system with 25+ event types for loose coupling |
| `edit_manager.py` | `DiffParser`, `SearchEngine`, `EditApplier` for safe file editing |
| `session_manager.py` | Legacy session manager (backward compatibility) |

### 2. Engine (`src/engine/`)

Provider-agnostic LLM abstraction layer. Swap providers by changing one config value.

```
BaseEngine (abstract)
├── KimiEngine      — OpenAI-compatible API (Kimi/Moonshot)
├── AnthropicEngine — Anthropic-style API
└── [Future: OpenAIEngine, GeminiEngine]
```

**Key interfaces:**
- `call_llm(messages, tools, system_prompt)` → Single LLM invocation (used by AgentLoop)
- `chat(user_input)` → Full conversation turn with internal tool loop (legacy)
- `register_tool(tool)` / `unregister_tool(name)` → Dynamic tool management
- `get_effective_tools()` → Returns tools filtered by active skill policy

### 3. Runtime (`src/runtime/`)

The execution engine that orchestrates the LLM ↔ Tool iteration cycle.

| Component | Responsibility |
|-----------|---------------|
| **AgentLoop** | Core loop: assemble context → call LLM → execute tools → repeat until done |
| **ToolScheduler** | Parallel tool execution with `ThreadPoolExecutor`, timeout enforcement |
| **LoopDetector** | Detects infinite loops via response similarity (cosine-based, configurable threshold) |
| **AgentConfig** | Runtime parameters: `max_iterations`, `max_parallel_tools`, `default_tool_timeout` |

### 4. Session (`src/session/`)

Complete session lifecycle management with persistence, recovery, and audit trail.

| Component | Responsibility |
|-----------|---------------|
| **SessionManager** | Create, activate, pause, resume, complete sessions; state machine transitions |
| **SessionStore** | SQLite-backed persistence for sessions, turns, checkpoints |
| **SessionLogger** | Writes `session_llm.md` — human-readable Markdown log of all LLM interactions |
| **TranscriptWriter** | Writes `transcript.jsonl` — machine-readable JSONL for replay/analysis |
| **SessionIndex** | JSON index (`sessions-index.json`) for fast session lookup |
| **DiffTracker** | Tracks file changes (create/modify/delete) within a session |

**Session State Machine:**
```
CREATED → ACTIVE → PAUSED → ACTIVE → COMPLETED
                 → FAILED
                 → ARCHIVED
```

### 5. Memory (`src/memory/`)

Dual-layer memory system for long-term knowledge retention.

| Component | Responsibility |
|-----------|---------------|
| **MemoryController** | CRUD operations, relevance search, memory lifecycle |
| **MemoryStore** | SQLite persistence with full-text search |
| **EpisodicMemory** | Session-specific memories (tool results, decisions, errors) |
| **SemanticMemory** | Cross-session knowledge (facts, preferences, patterns) |
| **ProjectMemoryLoader** | Loads `PROJECT.md` for project-specific conventions |

### 6. Context (`src/context/`)

Intelligent context window management to maximize LLM effectiveness within token limits.

| Component | Responsibility |
|-----------|---------------|
| **ContextAssembler** | Multi-layer pipeline: system prompt + memory + history + skill context |
| **ContextCompressor** | Token-aware compression: summarize old messages, preserve recent ones |
| **ContextWindow** | Sliding window with budget tracking and overflow handling |
| **ImportanceScorer** | Scores messages by role, recency, tool results, error content |
| **SystemReminderBuilder** | Injects dynamic reminders (date, active skill, warnings) |

### 7. Prompt (`src/prompt/`)

Dynamic prompt engineering with mode and skill awareness.

| Component | Responsibility |
|-----------|---------------|
| **PromptProvider** | Assembles system prompt from template + sections + skill context |
| **sections.py** | Modular prompt section builders (tools, guidelines, operational rules) |
| **templates/** | Markdown templates for base system prompt and compression instructions |

### 8. Skills (`src/skills/`)

Pluggable professional skill system. Skills are defined via `SKILL.md` files and provide domain-specific prompt context, tool filtering, and phased workflows.

| Component | Responsibility |
|-----------|---------------|
| **SkillRegistry** | Auto-discovers skills from `skills/` directory |
| **SkillContextManager** | Activates skills, builds skill-aware prompts, filters tools |
| **SkillWorkspaceManager** | Creates session-scoped working directories for skill outputs |
| **loader.py** | Parses `SKILL.md` → `SkillSpec` (metadata, phases, artifacts, tool policy) |

**Skill definition example** (`skills/recommendation_research/SKILL.md`):
```markdown
# Skill: Recommendation Research
## Metadata
- name: recommendation_research
- description: Research and reproduce recommendation models
## Phases
1. Literature Review
2. Data Preparation
3. Model Implementation
4. Experiment & Evaluation
## Tool Policy
- required: [search_arxiv, run_python_script, write_file]
- preferred: [fetch_url, read_csv, analyze_dataframe]
```

### 9. Tools (`src/tools/`)

60+ LLM-callable tools organized into 13 categories. See [Tool System](#tool-system) for the full catalog.

### 10. Utilities (`src/util/`)

Observability, logging, and infrastructure utilities.

| Component | Responsibility |
|-----------|---------------|
| **logger.py** | Structured logging with per-session log files, rotation, session binding |
| **structured_log.py** | JSONL event logger for machine-readable audit trail |
| **display.py** | `ConsoleDisplay` with color formatting, progress indicators, log file mirroring |
| **activity_stream.py** | Real-time activity stream: LLM calls, tool executions, status updates |
| **log_integration.py** | Wires `EventBus` events → Display + ActivityStream + StructuredLogger |
| **token_tracker.py** | Tracks token usage per turn, per session, with budget warnings |
| **db.py** | SQLite connection management and migration utilities |
| **parallel.py** | Thread-based parallel execution helpers |
| **mail_utils.py** | Email notification utilities |

---

## Tool System

All tools are wrapped in the `Tool` class with:
- **JSON Schema** parameter definitions for LLM function calling
- **Timeout protection** (configurable per-tool, default 120s)
- **Output truncation** (configurable per-tool, default 50K chars)
- **Categorized registry** with keyword search

### Tool Catalog (60+ tools, 13 categories)

| Category | Count | Tools |
|----------|-------|-------|
| **utility** | 2 | `calculate`, `search_knowledge` |
| **search** | 2 | `search_google`, `web_search_exa` |
| **stock** | 5 | `get_stock_kline`, `get_stock_snapshot`, `get_stock_financials`, `get_stock_info`, `analyze_stock` |
| **economics** | 5 | `get_fred_series`, `get_world_bank_indicator`, `get_exchange_rates`, `get_economic_calendar`, `generate_economic_report` |
| **file** | 11 | `read_file`, `write_file`, `append_file`, `edit_file`, `list_directory`, `search_in_files`, `move_file`, `copy_file`, `delete_file`, `get_file_info`, `create_directory` |
| **bash** | 5 | `run_command`, `run_python_script` (1800s timeout), `run_bash_script`, `get_system_info`, `get_environment_variable` |
| **pdf** | 6 | `read_pdf`, `get_pdf_metadata`, `merge_pdfs`, `split_pdf`, `create_pdf_from_text`, `extract_pdf_images` |
| **ppt** | 4 | `create_presentation`, `read_presentation`, `add_slide`, `export_ppt_to_pdf` |
| **web** | 6 | `fetch_url`, `extract_webpage_text`, `extract_links`, `download_file`, `check_url`, `fetch_rss_feed` |
| **data** | 7 | `read_csv`, `read_json_file`, `read_excel`, `analyze_dataframe`, `filter_csv`, `save_as_csv`, `convert_data_format` |
| **reader** | 4 | `convert_url_to_markdown`, `convert_pdf_to_markdown`, `convert_html_to_markdown`, `convert_file_to_markdown` |
| **arxiv** | 4 | `search_arxiv`, `get_arxiv_paper_details`, `download_arxiv_pdf`, `batch_download_arxiv_pdfs` |
| **codebase** | 3 | `grep_search`, `glob_search`, `ls_directory` |

### Tool Safety

Every tool execution is protected by:

1. **Timeout** — `ThreadPoolExecutor` with per-tool configurable timeout
2. **Output truncation** — Prevents token overflow from large tool outputs
3. **Command safety** — Dangerous shell patterns (`rm -rf /`, `mkfs`, etc.) are blocked
4. **Subprocess isolation** — CLI tools run in isolated subprocesses with separate timeout

---

## Skill Framework

Skills are **domain-specific capability packages** that customize the agent's behavior for specialized tasks.

### How Skills Work

1. **Discovery** — `SkillRegistry` scans `skills/` directory for `SKILL.md` files
2. **Activation** — User activates via `/skill <name>` or auto-detection from query keywords
3. **Prompt Injection** — Skill context (phases, guidelines, artifacts) is injected into system prompt
4. **Tool Filtering** — Only skill-relevant tools are exposed to the LLM
5. **Workspace** — A session-scoped directory is created for skill outputs

### Creating a New Skill

1. Create directory: `skills/<skill_name>/`
2. Write `SKILL.md` with metadata, phases, tool policy, and guidelines
3. Restart the agent — skill is auto-discovered

---

## Configuration

### Config File (`config/config.yaml`)

```yaml
# LLM Provider
provider: "anthropic"                    # kimi | anthropic | openai

# Provider-specific settings
providers:
  anthropic:
    api_key: ""                          # Or set ANTHROPIC_API_KEY env var
    base_url: "https://api.kimi.com/coding/"
    model: "kimi-k2.5"
    temperature: 0.7
    max_tokens: 16384

# Global engine settings
timeout: 3600                            # LLM request timeout (seconds)
max_iterations: 200                      # Max tool-call rounds per query

# Tool safety limits
tool_safety:
  default_timeout: 120                   # Default tool timeout (seconds)
  default_max_output: 50000              # Max output chars per tool
  subprocess_timeout: 55                 # CLI subprocess timeout

# Path configuration
paths:
  log_dir: "logs"
  result_dir: "results"
  data_dir: "data"
  session_dir: ""                        # Default: {result_dir}/session
  memory_data_dir: ""                    # Default: {result_dir}/memory_data
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `LLM_PROVIDER` | Provider type: `kimi`, `anthropic`, `openai` |
| `KIMI_API_KEY` | Kimi API key |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `KIMI_MODEL` / `ANTHROPIC_MODEL` / `OPENAI_MODEL` | Model identifier |
| `KIMI_BASE_URL` / `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` | API endpoint |
| `LOG_LEVEL` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `SERPAPI_API_KEY` | Google search via SerpAPI |
| `EXA_API_KEY` | Exa AI neural search |
| `FRED_API_KEY` | FRED economic data |

### Configuration Priority

```
CLI arguments  >  Environment variables  >  config.yaml  >  Hardcoded defaults
```

---

## Observability & Logging

Agent X1 provides a comprehensive observability stack:

### Output Files (per session)

```
results/session/<session_name>_<timestamp>/
├── session_llm.md          # Human-readable LLM interaction log
├── session_activity.md     # Real-time activity stream log
├── session_summary.md      # Auto-generated session summary
├── session_log.jsonl       # Machine-readable structured events
└── transcript.jsonl        # Full conversation transcript for replay
```

### Logging Chain

```
EventBus events
    │
    ├──▶ ConsoleDisplay      (real-time terminal output)
    ├──▶ ActivityStream      (activity_stream.md)
    ├──▶ StructuredLogger    (session_log.jsonl)
    └──▶ TokenTracker        (usage statistics)
```

### Log Levels

- `--debug` — Full debug output including raw API payloads
- `--verbose` — Detailed tool execution and context assembly info
- Default — Clean output with key events only

---

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Unit tests only
python -m pytest tests/unit/ -v

# Integration tests only
python -m pytest tests/integration/ -v

# Specific test module
python -m pytest tests/unit/test_context_assembler.py -v

# With coverage
python -m pytest tests/ --cov=src --cov-report=term-missing
```

**Test coverage:** 45 test files, 16K+ lines covering all core modules.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **Language** | Python 3.10+ |
| **LLM Providers** | Kimi (OpenAI-compatible), Anthropic-style API |
| **Database** | SQLite (sessions, memory, indexes) |
| **HTTP Client** | `requests` |
| **Config** | YAML (`pyyaml`) |
| **Data Processing** | `pandas`, `numpy`, `openpyxl` |
| **Visualization** | `matplotlib`, `seaborn` |
| **PDF** | `pymupdf` (PyMuPDF) |
| **PowerPoint** | `python-pptx` |
| **Web Scraping** | `beautifulsoup4`, `lxml` |
| **Search** | SerpAPI, Exa AI |
| **Academic** | arXiv API |
| **Financial** | `yfinance`, FRED API, World Bank API |
| **Terminal** | `prompt_toolkit` |
| **Testing** | `pytest` |

### Code Statistics

| Metric | Value |
|--------|-------|
| Source files | 70 |
| Source lines | 22,000+ |
| Test files | 45 |
| Test lines | 16,000+ |
| Tool count | 60+ |
| Tool categories | 13 |
| Core modules | 10 |

---

<p align="center">
  <sub>Built with ❤️ by Agent X1 Team</sub>
</p>
