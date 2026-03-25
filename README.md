# Agent X1 🚀 - From Zero to Hero in 12 Hours

> **Author:** ericdhuang（黄树东）| **Created:** 2026-03-07 | **Mission:** Master Agent Fundamentals from Scratch

## 🎯 What is This?

A **minimal yet production-ready** LLM Agent Framework built from **0 to 1 in just 12 hours**. This isn't just another Agent wrapper - it's a **learning blueprint** showing you the **core mechanics** of how Agents actually work.

## ⚡ The "Aha!" Moment - Agent in 3 Lines

```python main.py
```
**That's it.** No black boxes. No magic. Just clean, readable code.

---

## 🧠 Understanding Agents: The Core Concepts

An Agent is essentially a **Loop** that connects **LLM + Tools + Memory**:

```
┌─────────────────────────────────────────────────────────────┐
│                    THE AGENT LOOP                            │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   User Input ──► LLM ──► Needs Tool?                         │
│                       │                                      │
│                       ├─ YES ──► Execute Tool ──► Back to LLM│
│                       │                                      │
│                       └─ NO ──► Final Response              │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 🔧 The Three Pillars

| Component | Purpose | Analogy |
|-----------|---------|---------|
| **Engine** | Talks to LLM APIs | The "Brain" |
| **Tools** | Extends capabilities | The "Hands" |
| **Memory** | Remembers context | The "Notebook" |

### 📦 Minimum Viable Agent

The smallest Agent needs just **3 files**:

```
mini_agent/
├── engine.py      # LLM communication layer
├── tool.py        # Tool wrapper & registry
└── agent.py       # The main loop
```

See the full implementation in [`src/core/`](src/core/) - it's cleaner than you think!

---

## ✨ Features That Make Sense

| Feature | Why It Matters |
|---------|----------------|
| **Multi-Provider** | Kimi, Anthropic-style APIs - one interface, any backend |
| **Tool Ecosystem** | 54+ tools across 10 categories (File, Bash, PDF, Web, Stock...) |
| **Session Management** | Auto-created session dirs, full LLM interaction logs |
| **Observability** | Token usage, timing, cost tracking - know what's happening |
| **Skill Framework** | Complex workflows via SKILL.md (e.g., paper reproduction) |

---

## 🏗️ Architecture: Built for Understanding

```
┌─────────────────────────────────────────────────────────────┐
│                         main.py                              │
│                    (Entry Point & CLI)                       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    src/__init__.py                           │
│                 (Agent Factory API)                          │
└─────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   src/core/     │  │   src/engine/   │  │   src/tools/    │
│   (Foundation)  │  │  (LLM Engines)   │  │  (Tool Library) │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure API key
export KIMI_API_KEY=
export ENABLE_TOOL_SEARCH=false
export ANTHROPIC_BASE_URL=https://api.kimi.com/coding/
export ANTHROPIC_API_KEY=
export SERPAPI_KEY=
export EXA_API_KEY=

# Run interactive mode
python main.py

# Run with specific provider
python main.py -p anthropic

# Single query mode
python main.py --query "What's the weather in Beijing?"
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                         main.py                              │
│                    (Entry Point & CLI)                       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    src/__init__.py                           │
│                 (Agent Factory API)                          │
└─────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│   src/core/     │  │   src/engine/   │  │   src/tools/    │
│   (Foundation)  │  │  (LLM Engines)   │  │  (Tool Library) │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

### Core Design Principles

1. **Modularity**: Clear separation between core models, engine implementations, and tools
2. **Extensibility**: Easy to add new LLM providers via the `BaseEngine` abstraction
3. **Configuration-Driven**: YAML/JSON config files with environment variable overrides
4. **Session Management**: Automatic session directories and LLM interaction logging
5. **Observability**: Full request/response logging with token tracking and timing
6. **Tool Ecosystem**: 54 pluggable tools across 10 categories

## Directory Structure

```
agent_x1/
├── main.py                      # Entry point with CLI argument parsing
├── config/
│   └── config.yaml             # Main configuration file
├── src/                        # Source code
│   ├── __init__.py             # Package exports & create_agent() factory
│   ├── core/                   # Core foundation modules
│   │   ├── __init__.py         # Core exports (Message, Role, Tool, Config, Logger)
│   │   ├── models.py           # Message dataclass, Role enum
│   │   ├── tool.py             # Tool wrapper & ToolRegistry
│   │   ├── config.py           # AppConfig, LLMConfig, PathConfig, ProviderType
│   │   ├── logger.py           # Unified logging with loguru
│   │   └── session_manager.py # Session management & LLM interaction logging
│   ├── engine/                 # LLM engine implementations
│   │   ├── __init__.py         # Engine factory & registry
│   │   ├── base.py             # BaseEngine abstract class
│   │   ├── kimi_engine.py      # Kimi OpenAI-compatible API
│   │   └── anthropic_engine.py # Anthropic-style API with session logging
│   ├── skills/                 # Skill Framework (NEW)
│   │   ├── __init__.py         # Skill exports
│   │   ├── models.py           # Skill data models
│   │   ├── loader.py           # SKILL.md parser
│   │   ├── registry.py         # Skill discovery & indexing
│   │   ├── context_manager.py  # Skill lifecycle & prompt assembly
│   │   └── workspace.py        # Session-scoped skill workspaces
│   ├── tools/                  # Tool implementations (54+ tools)
│   │   ├── __init__.py         # All tools export & CategorizedToolRegistry
│   │   ├── tool_registry.py    # Tool categorization & discovery
│   │   ├── example_tools.py    # Weather, calculator, time
│   │   ├── search_tools.py     # Google (SerpAPI), Exa AI search
│   │   ├── stock_tools.py      # Stock data & financials (yfinance)
│   │   ├── stock_analysis.py   # Technical analysis with indicators
│   │   ├── file_tools.py       # File operations (10 tools)
│   │   ├── bash_tools.py       # Bash/system operations (5 tools)
│   │   ├── pdf_tools.py        # PDF processing (7 tools) - incl. markdown_to_pdf
│   │   ├── ppt_tools.py        # PowerPoint operations (4 tools)
│   │   ├── data_tools.py       # Data processing (7 tools)
│   │   ├── web_tools.py        # Web scraping (6 tools)
│   │   └── economics_tools.py  # Economic data (5 tools)
│   └── util/                   # Utility modules
│       ├── logger.py           # Original logger (legacy)
│       ├── mail_utils.py       # Email utilities
│       └── parallel.py         # Parallel processing
├── skills/                     # Skill definitions (NEW)
│   └── recommendation_research/  # Research skill for paper reproduction
│       └── SKILL.md            # DCN V2, DeepFM paper implementation workflow
├── tests/                      # Test suite (96+ tests)
│   ├── unit/
│   │   ├── test_imports.py     # Import & functionality tests
│   │   ├── test_tools_unit.py  # Comprehensive tool tests
│   │   └── test_skills.py      # Skill framework tests (NEW)
│   └── integration/
│       ├── test_anthropic_kimi.py
│       ├── test_kimi_api.py
│       └── test_tools_integration.py
├── logs/                       # Log output directory
├── results/                    # Results output directory
│   └── session/                # Session directories (auto-created)
│       └── {session_name}_{timestamp}/
│           └── session_llm.md  # LLM interaction log
├── memory_data/                # Session history & memory
│   └── history_session.md      # Historical session summaries
└── docs/                       # Documentation
    └── skill_framework_plan.md # Skill framework design doc (NEW)
```

## Session Management

The Agent X1 system includes comprehensive session management for tracking conversations and operations.

### Session Directory Structure

Each session automatically creates:
- **Session Directory**: `results/session/{session_name}_{yyyy-mm-dd_H-M-S}/`
- **LLM Log**: `session_llm.md` - Complete LLM request/response log
- **History**: `memory_data/history_session.md` - Aggregated session summaries

### Session Log Format

The `session_llm.md` file contains:
```markdown
# Session LLM Log

**Session:** session_2024-03-15_10-30-45  
**Started:** 2024-03-15_10-30-45

---

## LLM Call 1 [10:30:45]

### Request

**Messages (3):**
```json
[{"role": "user", "content": "Hello"}]
```

**Tools (53):** search_google, get_stock_snapshot, ...

### Response

**Stop Reason:** end_turn

**Usage:**
- Input Tokens: 152
- Output Tokens: 45
- Total Tokens: 197
- Duration: 1250.50ms

---
```

### History Session Summary

The `history_session.md` contains structured summaries:
```markdown
## Session: session_2024-03-15_10-30-45

**Period:** 2024-03-15 10:30:45 → 2024-03-15 10:35:12  
**Duration:** 5.45 minutes  
**Summary:** Market analysis with stock data fetching

### Operation Steps

1. [10:30:45] User query: Fetch NVDA stock data...
2. [10:30:46] Executed tool: get_stock_snapshot
3. [10:30:47] Executed tool: get_stock_financials

### LLM Statistics

- **Total LLM Calls:** 3
- **Total Input Tokens:** 456
- **Total Output Tokens:** 234
- **Total Tokens:** 690

### LLM Call Details

| Iteration | Time | Input | Output | Total | Duration | Stop Reason | Tools |
|-----------|------|-------|--------|-------|----------|-------------|-------|
| 1 | 10:30:45 | 152 | 45 | 197 | 1250ms | end_turn | 2 |
...
```

## Module Details

### 1. Core Module (`src/core/`)

#### `session_manager.py`
- **SessionManager**: Manages session lifecycle and logging
  - `start_session()`: Creates session directory
  - `log_llm_interaction()`: Logs complete LLM request/response
  - `record_operation_step()`: Records major operations
  - `end_session()`: Writes session summary to history
- **LLMCallRecord**: Token usage and timing for each API call
- **SessionSummary**: Aggregated session statistics
- **Signal Handlers**: Graceful shutdown on Ctrl+C or kill

#### `models.py`
- **Message**: Dataclass for conversation messages with role, content, tool_calls
- **Role**: Enum for system/user/assistant/tool roles
- **Conversation history management**: Structured message storage

#### `tool.py`
- **Tool**: Wrapper for Python functions as LLM-callable tools
  - JSON Schema parameter definition
  - Automatic validation and execution
- **ToolRegistry**: Central registry for managing multiple tools

#### `config.py`
- **ProviderType**: Enum for supported LLM providers (kimi, anthropic, openai, gemini)
- **LLMConfig**: Provider-specific settings (api_key, base_url, model, temperature, etc.)
  - Default timeout: 600 seconds
  - Default max_iterations: 30
  - Default max_tokens: 4096
- **PathConfig**: Output directory configuration (logs, results, data, temp)
- **AppConfig**: Main configuration combining LLM and path settings
- **Configuration Loading Priority**:
  1. Environment variables (highest)
  2. Config file (YAML/JSON)
  3. Default values (lowest)

#### `logger.py`
- Unified logging with `loguru`
- Structured log format with timestamps, process/thread IDs
- Log rotation and file/console output
- Helper functions: `get_logger()`, `setup_logging()`, `set_log_level()`

### 2. Engine Module (`src/engine/`)

#### `base.py`
- **BaseEngine**: Abstract base class defining the engine interface
  - `register_tool()`: Tool registration
  - `chat()`: Main conversation loop with tool calling
  - `_call_llm()`: Abstract method for API calls
  - `_parse_response()`: Abstract method for response parsing
  - `clear_history()`, `get_conversation_history()`: State management
- **EngineConfig**: Configuration dataclass for engines

#### `kimi_engine.py`
- **KimiEngine**: OpenAI-compatible API implementation
  - Base URL: `https://api.moonshot.cn/v1`
  - Endpoint: `/chat/completions`
  - Supports function calling with `tools` parameter
  - Multi-turn conversation with tool results

#### `anthropic_engine.py`
- **AnthropicEngine**: Anthropic-style API implementation
  - Base URL: `https://api.kimi.com/coding/`
  - Endpoint: `/v1/messages`
  - Uses `x-api-key` header and `anthropic-version`
  - Tool use format with `tool_use` and `tool_result` content blocks

#### `__init__.py`
- **EngineRegistry**: Factory pattern for engine creation
- **create_engine()**: Universal factory function
- **create_kimi_engine()**, **create_anthropic_engine()**: Provider-specific factories

### 3. Tools Module (`src/tools/`)

#### Tool Categories

| Category | Count | Tools | File |
|----------|-------|-------|------|
| **Utility** | 4 | Weather, Calculator, Time, Search | example_tools.py |
| **Search** | 2 | Google (SerpAPI), Exa AI | search_tools.py |
| **Stock** | 5 | Kline, Snapshot, Financials, Info, **Analysis** | stock_tools.py, stock_analysis.py |
| **File** | 10 | Read, Write, Append, List, Search, Move, Copy, Delete, Info, Mkdir | file_tools.py |
| **Bash** | 5 | Run Command, Python Script, Bash Script, System Info, Env Var | bash_tools.py |
| **PDF** | 7 | Read, Metadata, Merge, Split, Create, Extract Images, **Markdown to PDF** | pdf_tools.py |
| **PowerPoint** | 4 | Create, Read, Add Slide, Export to PDF | ppt_tools.py |
| **Web** | 6 | Fetch URL, Extract Text, Extract Links, Download, Check, RSS | web_tools.py |
| **Data** | 7 | Read CSV, Read JSON, Read Excel, Analyze, Filter, Save, Convert | data_tools.py |
| **Economics** | 5 | FRED Series, World Bank, Exchange Rates, Calendar, Report | economics_tools.py |

**Total: 54+ tools across 10 categories**

#### arXiv Tools

Specialized tools for academic paper research:
- `search_arxiv` - Search papers on arXiv with keyword/topic
- `get_arxiv_paper_details` - Get detailed paper metadata
- `download_arxiv_pdf` - Download PDF to session directory
- `batch_download_arxiv_pdfs` - Batch download multiple papers
- `convert_pdf_to_markdown` - Convert PDF to markdown for analysis

#### PDF Tools

The PDF tools include `markdown_to_pdf()` which converts Markdown to PDF while preserving:
- Headers (H1-H6) with proper font sizing
- Tables with cell borders
- Lists (bulleted and numbered)
- Code blocks with monospace formatting
- Horizontal rules

```python
from src.tools.pdf_tools import MARKDOWN_TO_PDF_TOOL

result = MARKDOWN_TO_PDF_TOOL.execute('{
    "markdown_path": "input.md",
    "output_path": "output.pdf",
    "title": "My Document"
}')
```

#### Tool Registry

The `CategorizedToolRegistry` organizes tools by category:
```python
from src.tools import TOOL_REGISTRY

# Search tools by keyword
tools = TOOL_REGISTRY.search("stock")

# Get catalog by category
catalog = TOOL_REGISTRY.get_catalog()

# Get tools by category
file_tools = TOOL_REGISTRY.get_by_category("file")
```

### 4. Skills Framework (`src/skills/`)

The Skill Framework enables complex, multi-step workflows with structured context injection:

#### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  SkillRegistry        │  Discovers & indexes SKILL.md files  │
├─────────────────────────────────────────────────────────────┤
│  SkillContextManager  │  Manages skill lifecycle & prompts   │
├─────────────────────────────────────────────────────────────┤
│  SkillWorkspaceManager│  Session-scoped working directories  │
└─────────────────────────────────────────────────────────────┘
```

#### Built-in Skill: Recommendation Research

Located at `skills/recommendation_research/SKILL.md`:

**Purpose**: Implement and validate recommendation algorithms from academic papers (DCN V2, DeepFM, etc.)

**9-Phase Workflow**:
1. **Environment Setup** - Python, PyTorch, dependencies
2. **Paper Acquisition** - Download from arXiv via `search_arxiv`, `download_arxiv_pdf`
3. **Paper Ingestion** - Parse PDF with `convert_pdf_to_markdown`
4. **Paper Analysis** - Extract model architecture, training details
5. **Implementation** - Create model, training, evaluation code
6. **Data Preparation** - Download and preprocess datasets (Criteo, MovieLens)
7. **Training & Validation** - Train model with logging, checkpointing
8. **Optimization** - Hyperparameter tuning, feature engineering
9. **Report Generation** - Final report with AUC comparisons

#### Using Skills

```python
from src import create_agent

agent = create_agent()

# Activate skill for paper reproduction research
ctx = agent.skill_context
ctx.activate_skill(
    "recommendation_research",
    goal="Implement DCN V2 paper and validate on Criteo dataset"
)

# The skill's full SKILL.md is now injected into system prompt
# Tools are filtered based on skill's preferred/blocked lists
response = agent.chat("Download DCN V2 paper and implement the model")
```

#### Skill System Commands (Interactive Mode)

```
/skills              - List available skills
/skill <name>        - Activate a skill
```

#### Workspace Structure

Each activated skill creates a workspace in the session directory:

```
results/session/{session_name}_{timestamp}/
└── research/
    └── recommendation_research/
        ├── papers/        # Downloaded papers
        ├── notes/         # Analysis notes
        ├── datasets/      # Training data
        ├── src/           # Implementation code
        ├── configs/       # Hyperparameter configs
        ├── scripts/       # Utility scripts
        ├── tests/         # Unit tests
        ├── runs/          # Experiment logs
        ├── reports/       # Final reports
        ├── logs/          # Training logs
        └── README.md      # Project documentation
```

## Configuration

### Config File (`config/config.yaml`)

```yaml
# LLM Provider Configuration
# Set which provider to use: kimi, anthropic, openai
provider: "anthropic"

# Provider-specific configurations
providers:
  kimi:
    api_key: ""  # Set via KIMI_API_KEY env var or provide here
    base_url: "https://api.moonshot.cn/v1"
    model: "moonshot-v1-32k"
    temperature: 0.7
    max_tokens: 228960
    
  anthropic:
    api_key: ""  # Set via ANTHROPIC_API_KEY env var or provide here
    base_url: "https://api.kimi.com/coding/"
    model: "kimi-k2.5"
    temperature: 0.7
    max_tokens: 228960
    
  openai:
    api_key: ""  # Set via OPENAI_API_KEY env var or provide here
    base_url: "https://api.openai.com/v1"
    model: "gpt-4"
    temperature: 0.7
    max_tokens: 228960

# Global engine settings (can be overridden by provider-specific settings above)
timeout: 600
max_iterations: 30

# System prompt
system_prompt: |
  You are a helpful AI assistant with access to tools.
  Use the available tools when needed to provide accurate information.
  Always respond in the same language as the user's query.
  
  Session file organization:
  - All files produced in each session should be saved to: results/session/{session_name}_{yyyy-mm-dd_H-M-S}/
  - This includes PDFs, data files, reports, and any other generated content

# Path Configuration
paths:
  log_dir: "logs"
  result_dir: "results"
  data_dir: "data"
  temp_dir: "tmp"

# Logging
log_level: "INFO"
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `KIMI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | API authentication |
| `LLM_PROVIDER` | Override provider type |
| `LLM_TEMPERATURE` | Override temperature |
| `LLM_TIMEOUT` | Override timeout (default: 600s) |
| `LLM_MAX_ITERATIONS` | Override max iterations (default: 30) |
| `LOG_LEVEL` | Set logging level |
| `LOG_DIR` / `RESULT_DIR` | Override paths |
| `FRED_API_KEY` | For economic data tools |
| `SERPAPI_KEY` | For Google search |
| `EXA_API_KEY` | For Exa AI search |

## Usage Examples

### Basic Usage

```python
from src import create_agent

# Create agent with default config
agent = create_agent()

# Chat
response = agent.chat("What's the weather in Beijing?")
print(response)
```

### With Session Management

```python
from src import create_agent
from src.core.session_manager import get_session_manager

agent = create_agent()

# Session directory is auto-created
session_dir = get_session_manager().get_session_directory()
print(f"Files will be saved to: {session_dir}")

# Chat - LLM interactions are automatically logged
response = agent.chat("Create a PDF report of NVDA stock data")
```

```python
from src import create_agent

agent = create_agent(
    provider="anthropic",
    api_key="your-key",
    model="kimi-k2.5",
    temperature=0.5
)
```

### Direct Engine Usage

```python
from src.engine import create_engine, ProviderType
from src.tools import WEATHER_TOOL, CALCULATOR_TOOL

engine = create_engine(
    provider=ProviderType.KIMI,
    api_key="your-key",
    model="kimi-latest"
)

engine.register_tool(WEATHER_TOOL)
engine.register_tool(CALCULATOR_TOOL)

response = engine.chat("What's 123 * 456?")
```

### Markdown to PDF Conversion

```python
from src.tools.pdf_tools import markdown_to_pdf

result = markdown_to_pdf(
    markdown_path="report.md",
    output_path="report.pdf",
    title="Q1 Financial Report"
)

print(f"PDF created: {result['output_path']}")
print(f"Pages: {result['pages']}")
print(f"Tables found: {result['elements_found']['tables']}")
```

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run unit tests
python tests/unit/test_imports.py

# Run tool tests
python tests/unit/test_tools_unit.py

# Run integration tests
python tests/integration/test_tools_integration.py

# Run with verbose output
python tests/unit/test_imports.py -v
```

**Test Coverage**: 96+ tests (71 unit + 18 integration + 7 legacy + skill framework tests)

## Potential Optimizations

### 1. Engine Enhancements
- **Streaming Support**: Add streaming response support for real-time output
- **Retry Logic**: Implement exponential backoff for API failures
- **Caching**: Add response caching for identical queries
- **Rate Limiting**: Built-in rate limit handling per provider

### 2. Tool Improvements
- **Async Tools**: Support async tool execution for I/O-bound operations
- **Tool Chaining**: Allow tools to call other tools
- **Tool Versioning**: Version control for tool schemas
- **Tool Documentation**: Auto-generate tool documentation from docstrings

### 3. Memory & Context
- **Long-term Memory**: Persistent conversation storage
- **Context Summarization**: Automatic context compression for long conversations
- **Multi-session Management**: Support multiple concurrent conversation sessions

### 4. Configuration
- **Hot Reload**: Config file changes without restart
- **Multiple Profiles**: Support for different configuration profiles
- **Secrets Management**: Integration with vaults (HashiCorp Vault, AWS Secrets)

### 5. Observability
- **Metrics**: Prometheus/OpenTelemetry integration
- **Tracing**: Distributed tracing for multi-step operations
- **Cost Tracking**: Per-request cost estimation and tracking

### 6. Security
- **Input Sanitization**: PII detection and redaction
- **Tool Sandboxing**: Isolated tool execution environment
- **API Key Rotation**: Automatic key rotation support

## Provider Support Status

| Provider | Status | Base URL | Notes |
|----------|--------|----------|-------|
| Kimi | Ready | `https://api.moonshot.cn/v1` | OpenAI-compatible |
| Anthropic-style | Ready | `https://api.kimi.com/coding/` | Kimi's Anthropic endpoint with session logging |
| OpenAI | Planned | `https://api.openai.com/v1` | Native OpenAI support |
| Gemini | Planned | TBD | Google Gemini API |

## Troubleshooting

### 401 Unauthorized
- Check API key is set correctly
- Verify key has necessary permissions
- Check environment variable name matches provider

### 404 Not Found
- Verify base_url is correct for provider
- Check provider endpoint URLs in documentation

### Tool Not Found
- Ensure tool is registered before `chat()` call
- Check tool name matches exactly (case-sensitive)

### Import Errors
- Ensure running from project root
- Check `PYTHONPATH` includes project directory
- Verify all `__init__.py` files exist

### Session Directory Not Created
- Check write permissions in project directory
- Verify `paths.result_dir` in config.yaml
- Check logs for initialization errors

## License

MIT License

## Contributing

1. Fork the repository
2. Create a feature branch
3. Add tests for new functionality
4. Submit a pull request
