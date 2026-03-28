# Agent X1

> 一个模块化、多 Provider 的 LLM Agent 系统，支持丰富的工具调用、技能框架、会话管理和记忆系统。

**版本**: 1.0.0 · **语言**: Python 3.10+ · **框架**: 纯 Python（无 Web 框架依赖）

---

## 目录

- [项目概述](#项目概述)
- [核心特性](#核心特性)
- [系统架构](#系统架构)
- [项目目录结构](#项目目录结构)
- [模块详解](#模块详解)
  - [core — 核心基础层](#core--核心基础层)
  - [engine — LLM 引擎层](#engine--llm-引擎层)
  - [runtime — Agent 运行时](#runtime--agent-运行时)
  - [session — 会话管理](#session--会话管理)
  - [memory — 记忆系统](#memory--记忆系统)
  - [context — 上下文组装](#context--上下文组装)
  - [prompt — 提示词管理](#prompt--提示词管理)
  - [tools — 工具库](#tools--工具库)
  - [skills — 技能框架](#skills--技能框架)
  - [util — 工具类](#util--工具类)
- [工具清单](#工具清单)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [测试](#测试)
- [技术栈与依赖](#技术栈与依赖)

---

## 项目概述

Agent X1 是一个自主 AI Agent 系统，围绕 **"LLM + Tool Calling + Skill"** 范式构建。系统通过统一的引擎抽象层对接多种 LLM Provider（Anthropic、Kimi/Moonshot、OpenAI），并提供 **50+ 内置工具**覆盖文件操作、代码搜索、Shell 执行、PDF/PPT 处理、数据分析、学术论文检索、股票分析、Web 爬取等场景。

系统采用 **新旧双架构并行** 设计：
- **经典架构**：Engine 内置 chat loop，直接驱动工具调用循环
- **新架构（AgentLoop）**：将循环控制、上下文组装、工具调度、循环检测等职责解耦为独立组件，支持更精细的状态管理和事件驱动

---

## 核心特性

| 特性 | 说明 |
|------|------|
| 🔌 **多 Provider 支持** | Anthropic / Kimi (Moonshot) / OpenAI，通过 `EngineRegistry` 动态注册 |
| 🛠️ **50+ 内置工具** | 覆盖 13 个类别：文件、Shell、PDF、PPT、Web、数据、搜索、股票、经济、arXiv、代码搜索等 |
| 🎯 **技能框架 (Skill)** | 基于 `SKILL.md` 声明式定义专业技能，支持自动发现、工具过滤、提示词注入 |
| 💾 **双层记忆系统** | 情景记忆（Episodic）+ 语义记忆（Semantic），SQLite 持久化 |
| 📋 **会话管理** | 完整生命周期：创建 → 激活 → 暂停 → 恢复 → 完成/失败 → 归档，支持 Fork/Checkpoint |
| 🔄 **AgentLoop 新架构** | 统一执行循环，集成上下文组装、工具调度、循环检测、事件总线 |
| 📐 **上下文分层组装** | 7 层优先级上下文：系统提示 > 用户输入 > 项目记忆 > 检索记忆 > 技能 > 历史 > 工具输出 |
| 🔍 **循环检测** | 滑动窗口相似度检测，防止 Agent 陷入重复工具调用 |
| 📝 **文件编辑守卫** | Read-before-edit 策略，SEARCH/REPLACE diff 格式，防止盲编辑 |
| 📡 **事件总线** | 松耦合的 EventBus，支持会话、LLM、工具、上下文等 20+ 事件类型 |

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         main.py (Entry Point)                   │
│              Interactive Mode / Single Query Mode                │
└──────────────────────────┬──────────────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
┌──────────────────────┐  ┌──────────────────────────────────────┐
│   Engine (经典架构)    │  │       AgentLoop (新架构)              │
│  engine.chat()       │  │  ┌─────────────────────────────────┐ │
│  内置 tool loop      │  │  │ ContextAssembler (上下文组装)     │ │
└──────────────────────┘  │  │ ToolScheduler   (工具调度)       │ │
                          │  │ LoopDetector    (循环检测)       │ │
                          │  │ EventBus        (事件总线)       │ │
                          │  └─────────────────────────────────┘ │
                          └──────────────────────────────────────┘
                                        │
              ┌─────────────────────────┼─────────────────────────┐
              │                         │                         │
              ▼                         ▼                         ▼
┌──────────────────┐  ┌──────────────────────┐  ┌────────────────────┐
│  SessionManager  │  │   MemoryController   │  │   SkillRegistry    │
│  会话生命周期管理  │  │  情景 + 语义记忆      │  │  技能发现/激活/过滤  │
│  Turn / Checkpoint│  │  检索 / 清理 / 摘要   │  │  SKILL.md 解析     │
└────────┬─────────┘  └──────────┬───────────┘  └────────────────────┘
         │                       │
         ▼                       ▼
┌──────────────────────────────────────┐
│         SQLite (agent_x1.db)         │
│  sessions / turns / checkpoints      │
│  episodic_memory / semantic_memory   │
│  memory_fts (全文检索)               │
└──────────────────────────────────────┘
```

### 引擎继承体系

```
BaseEngine (abstract)
├── KimiEngine      — OpenAI-compatible API (Moonshot)
├── AnthropicEngine — Anthropic-style API
└── [Future: OpenAIEngine, GeminiEngine, ...]
```

---

## 项目目录结构

```
agent_x1/
├── main.py                          # 主入口：CLI 参数解析、引擎创建、交互/单查询模式
├── requirements.txt                 # Python 依赖清单
├── config/
│   └── config.yaml                  # 全局配置：Provider、模型、路径、系统提示词
├── src/
│   ├── __init__.py                  # 包入口，提供 create_agent() 工厂函数
│   ├── core/                        # 🏗️ 核心基础层
│   │   ├── __init__.py              # 统一导出 Message, Role, Tool, Config, EditManager
│   │   ├── models.py                # Message / Role 数据模型
│   │   ├── config.py                # AppConfig / LLMConfig / PathConfig 配置管理
│   │   ├── tool.py                  # Tool 包装器 + ToolRegistry
│   │   ├── edit_manager.py          # DiffParser / SearchEngine / EditApplier / FileEditingGuard
│   │   ├── events.py                # EventBus 事件总线 + AgentEvent 枚举
│   │   └── session_manager.py       # 旧版会话管理器（兼容层）
│   ├── engine/                      # 🔌 LLM 引擎层
│   │   ├── __init__.py              # EngineRegistry 工厂 + create_engine()
│   │   ├── base.py                  # BaseEngine 抽象基类 + EngineConfig + ProviderType
│   │   ├── anthropic_engine.py      # Anthropic API 引擎实现
│   │   └── kimi_engine.py           # Kimi/Moonshot OpenAI-compatible 引擎实现
│   ├── runtime/                     # 🔄 Agent 运行时（新架构）
│   │   ├── __init__.py              # 导出 AgentLoop, ToolScheduler, LoopDetector
│   │   ├── agent_loop.py            # AgentLoop 统一执行循环
│   │   ├── tool_scheduler.py        # ToolScheduler / ParallelToolScheduler 工具调度
│   │   ├── loop_detector.py         # LoopDetector 循环检测
│   │   └── models.py                # AgentState / AgentConfig / ToolCallRecord
│   ├── session/                     # 📋 会话管理（新架构）
│   │   ├── __init__.py              # 导出 SessionManager
│   │   ├── models.py                # Session / Turn / Checkpoint / TokenBudget / SessionStatus
│   │   ├── session_manager.py       # SessionManager 完整生命周期管理
│   │   └── session_store.py         # SessionStore SQLite 持久化层
│   ├── memory/                      # 💾 记忆系统
│   │   ├── __init__.py              # 导出 MemoryController, MemoryStore
│   │   ├── models.py                # EpisodicMemory / SemanticMemory 数据模型
│   │   ├── memory_store.py          # MemoryStore SQLite 存储层
│   │   ├── memory_controller.py     # MemoryController 高级记忆管理接口
│   │   └── project_memory.py        # ProjectMemoryLoader 项目级记忆加载
│   ├── context/                     # 📐 上下文管理
│   │   ├── __init__.py              # 导出 ContextAssembler, ContextWindow
│   │   ├── context_assembler.py     # ContextAssembler 分层上下文组装
│   │   ├── context_compressor.py    # ContextCompressor 上下文压缩
│   │   └── context_window.py        # ContextWindow / ContextBudget Token 预算管理
│   ├── prompt/                      # 📝 提示词管理
│   │   ├── __init__.py              # 导出 PromptProvider
│   │   ├── prompt_provider.py       # PromptProvider 模块化提示词组装
│   │   ├── sections.py              # 独立 Section 渲染器（preamble, mandates, tools, skills...）
│   │   └── templates/               # 提示词模板
│   │       ├── base_system.md       # 基础系统提示词模板
│   │       └── compression.md       # 压缩指令模板
│   ├── skills/                      # 🎯 技能框架
│   │   ├── __init__.py              # 导出 SkillRegistry, SkillContextManager
│   │   ├── models.py                # SkillMetadata / SkillSummary / SkillToolPolicy / SkillPhase
│   │   ├── loader.py                # SkillSpec + load_skill_spec() SKILL.md 解析器
│   │   ├── registry.py              # SkillRegistry 技能发现与索引
│   │   ├── context_manager.py       # SkillContextManager 提示词注入 + 工具过滤
│   │   └── workspace.py             # SkillWorkspaceManager 会话级工作目录管理
│   ├── tools/                       # 🛠️ 工具库（50+ 工具）
│   │   ├── __init__.py              # ALL_TOOLS 注册 + TOOL_REGISTRY 分类注册表
│   │   ├── tool_registry.py         # CategorizedToolRegistry 分类工具注册表
│   │   ├── example_tools.py         # 基础工具：天气、计算器、时间、知识搜索
│   │   ├── search_tools.py          # 搜索工具：Google (SerpAPI)、Exa AI 神经搜索
│   │   ├── file_tools.py            # 文件工具：读写、编辑、搜索、移动、复制、删除
│   │   ├── bash_tools.py            # Shell 工具：命令执行、Python/Bash 脚本、系统信息
│   │   ├── codebase_search_tools.py # 代码搜索：grep 正则搜索、glob 文件匹配、ls 目录列表
│   │   ├── pdf_tools.py             # PDF 工具：读取、合并、拆分、创建、提取图片
│   │   ├── ppt_tools.py             # PPT 工具：创建、读取、添加幻灯片、导出 PDF
│   │   ├── web_tools.py             # Web 工具：URL 抓取、文本提取、链接提取、RSS
│   │   ├── data_tools.py            # 数据工具：CSV/JSON/Excel 读取、统计分析、过滤、转换
│   │   ├── reader_tools.py          # Reader 工具：URL/PDF/HTML 转 Markdown
│   │   ├── arxiv_tools.py           # arXiv 工具：论文搜索、详情、下载、批量下载
│   │   ├── stock_tools.py           # 股票工具：K线、快照、财务、公司信息
│   │   ├── stock_analysis.py        # 股票分析：综合分析报告
│   │   └── economics_tools.py       # 经济工具：FRED、世界银行、汇率、经济日历
│   └── util/                        # 🔧 工具类
│       ├── db.py                    # DatabaseManager SQLite 管理 + 自动迁移
│       ├── logger.py                # 日志系统（基于 loguru）
│       ├── mail_utils.py            # 邮件工具
│       └── parallel.py              # 并行执行工具
├── data/
│   ├── agent_x1.db                  # SQLite 数据库（会话 + 记忆）
│   └── migrations/
│       └── 001_init.sql             # 数据库 Schema 初始化脚本
├── skills/
│   └── recommendation_research/
│       └── SKILL.md                 # 推荐系统研究技能定义
├── memory_data/
│   └── history_session.md           # 历史会话记录
├── results/                         # 会话输出目录
│   └── session/                     # 各会话的产出文件
├── downloads/                       # 下载文件存储
├── tests/
│   ├── unit/                        # 单元测试
│   │   ├── test_tools_unit.py       # 工具单元测试
│   │   ├── test_edit_manager.py     # 编辑管理器测试
│   │   ├── test_skills.py           # 技能框架测试
│   │   ├── test_arxiv_tools.py      # arXiv 工具测试
│   │   ├── test_reader_tools.py     # Reader 工具测试
│   │   ├── test_codebase_search_tools.py  # 代码搜索工具测试
│   │   ├── test_tool_safety.py      # 工具安全性测试
│   │   └── test_imports.py          # 导入完整性测试
│   └── integration/                 # 集成测试
│       ├── test_tools_integration.py      # 工具集成测试
│       ├── test_arxiv_integration.py      # arXiv 集成测试
│       ├── test_reader_integration.py     # Reader 集成测试
│       ├── test_anthropic_kimi.py         # Anthropic/Kimi 引擎测试
│       └── test_kimi_api.py               # Kimi API 测试
└── docs/                            # 参考文档
    ├── dev_doc/                     # 开发文档、架构设计、白皮书
    ├── codex/                       # OpenAI Codex 架构分析
    ├── gemini/                      # Google Gemini CLI 架构分析
    ├── opencode/                    # OpenCode 架构分析
    ├── kimi/                        # Kimi 架构分析
    └── prompt/                      # 提示词参考
```

---

## 模块详解

### core — 核心基础层

**路径**: `src/core/`

核心模块提供整个系统的基础设施：

| 文件 | 职责 |
|------|------|
| `models.py` | `Message` 和 `Role` 数据模型，统一消息格式 |
| `config.py` | `AppConfig` / `LLMConfig` / `PathConfig`，支持 YAML 配置 + 环境变量 + CLI 覆盖 |
| `tool.py` | `Tool` 包装器（name, description, parameters, execute）+ `ToolRegistry` |
| `edit_manager.py` | 文件编辑核心：`DiffParser`（SEARCH/REPLACE 格式解析）、`SearchEngine`（精确匹配 + 近似建议）、`EditApplier`（位置无关多块编辑）、`FileEditingGuard`（read-before-edit 守卫） |
| `events.py` | `EventBus` 事件总线 + `AgentEvent` 枚举（20+ 事件类型覆盖会话/LLM/工具/上下文/记忆/运行时） |
| `session_manager.py` | 旧版会话管理器（兼容层） |

### engine — LLM 引擎层

**路径**: `src/engine/`

引擎层通过抽象基类 `BaseEngine` 统一不同 LLM Provider 的接口：

- **`BaseEngine`**：定义 `register_tool()`, `chat()`, `call_llm()`, `get_effective_tools()`, `get_effective_system_prompt()` 等核心接口
- **`AnthropicEngine`**：实现 Anthropic 风格 API 调用（支持 streaming、tool_use content block 解析）
- **`KimiEngine`**：实现 OpenAI-compatible API 调用（Moonshot/Kimi）
- **`EngineRegistry`**：Provider → Engine 类映射注册表，支持动态扩展
- **`create_engine()`**：工厂函数，根据配置自动创建对应引擎

引擎内置 **技能感知**：通过 `set_skill_context()` 和 `set_tool_categories()` 实现激活技能时的提示词注入和工具过滤。

### runtime — Agent 运行时

**路径**: `src/runtime/`

新架构的核心，将 Agent 执行循环从 Engine 中解耦：

| 组件 | 职责 |
|------|------|
| `AgentLoop` | 统一执行循环：上下文组装 → LLM 调用 → 工具执行 → 循环检测 → 状态管理 |
| `ToolScheduler` | 工具调度器：参数校验、超时控制、输出截断、自动重试、事件发射 |
| `ParallelToolScheduler` | 并行工具调度器：基于 `asyncio.Semaphore` 的并发执行 |
| `LoopDetector` | 循环检测器：滑动窗口 + 相似度阈值，防止重复工具调用 |
| `AgentState` | 状态机：IDLE → ASSEMBLING_CONTEXT → WAITING_FOR_LLM → EXECUTING_TOOLS → COMPLETED/ERROR |

### session — 会话管理

**路径**: `src/session/`

完整的会话生命周期管理：

- **状态机**：`CREATED → ACTIVE → PAUSED → COMPLETED/FAILED → ARCHIVED`，支持 `FORKED` 分支
- **Turn 记录**：每轮对话（user/assistant/tool）持久化到 SQLite
- **Checkpoint**：任意时刻创建快照，支持 Fork/Restore
- **Token 预算**：`TokenBudget` 跟踪总量/已用/保留/可用
- **自动维护**：`archive_old_sessions()` 归档过期会话，`cleanup_archived()` 清理

### memory — 记忆系统

**路径**: `src/memory/`

双层记忆架构：

| 层级 | 类型 | 说明 |
|------|------|------|
| **情景记忆** | `EpisodicMemory` | 会话级事件：决策(decision)、行动(action)、结果(outcome)、错误(error)、洞察(insight)、笔记(note) |
| **语义记忆** | `SemanticMemory` | 跨会话长期知识：偏好(preference)、惯例(convention)、事实(fact)、模式(pattern)、流程(procedure) |

- **`MemoryController`**：高级接口，提供 `record_decision()`, `store_preference()`, `retrieve_relevant()`, `summarize_session()` 等方法
- **`MemoryStore`**：SQLite 持久化层，支持全文检索（FTS5）
- **`ProjectMemoryLoader`**：加载项目级 `PROJECT.md` 记忆文件
- **自动清理**：基于重要性衰减的过期记忆清理

### context — 上下文组装

**路径**: `src/context/`

分层优先级上下文组装系统：

```
优先级（高 → 低）：
1. System Prompt     (priority=100, required)
2. User Input        (priority=95,  required)
3. Project Memory    (priority=90,  required)
4. Retrieved Memory  (priority=70)
5. Active Skill      (priority=60)
6. Conversation History (priority=40)
```

- **`ContextAssembler`**：按优先级组装各层，自动跳过超出 Token 预算的非必需层
- **`ContextWindow`**：Token 预算管理，判断消息是否适配窗口
- **`ContextCompressor`**：当上下文超限时压缩历史消息

### prompt — 提示词管理

**路径**: `src/prompt/`

模块化提示词组装系统：

- **`PromptProvider`**：从独立 Section 渲染器组装完整系统提示词
- **`sections.py`**：独立渲染函数：
  - `render_preamble()` — Agent 身份声明
  - `render_mandates()` — 核心行为准则
  - `render_tools()` — 可用工具列表
  - `render_skills_catalog()` — 技能目录
  - `render_active_skill()` — 激活技能详情
  - `render_project_context()` — 项目上下文
  - `render_guidelines()` — 操作指南
  - `render_error_recovery()` — 错误恢复指令
  - `render_loop_warning()` — 循环检测警告
  - `render_compression_instructions()` — 压缩指令

### tools — 工具库

**路径**: `src/tools/`

50+ 内置工具，按 13 个类别组织：

| 类别 | 工具数 | 代表工具 |
|------|--------|----------|
| **utility** | 4 | `weather`, `calculator`, `time`, `search` |
| **search** | 2 | `google_search` (SerpAPI), `exa_search` (神经搜索) |
| **file** | 11 | `read_file`, `write_file`, `edit_file`, `search_in_files`, `create_directory` |
| **bash** | 5 | `run_command`, `run_python_script`, `run_bash_script`, `get_system_info` |
| **codebase** | 3 | `grep_search` (正则搜索), `glob_search` (文件匹配), `ls_directory` |
| **pdf** | 6 | `read_pdf`, `merge_pdfs`, `split_pdf`, `create_pdf`, `extract_pdf_images` |
| **ppt** | 4 | `create_presentation`, `read_presentation`, `add_slide`, `export_ppt_to_pdf` |
| **web** | 6 | `fetch_url`, `extract_webpage_text`, `extract_links`, `download_file`, `fetch_rss` |
| **data** | 7 | `read_csv`, `read_json`, `read_excel`, `analyze_dataframe`, `filter_csv` |
| **reader** | 4 | `convert_url_to_markdown`, `convert_pdf_to_markdown`, `convert_html_to_markdown` |
| **arxiv** | 4 | `search_arxiv`, `get_arxiv_paper_details`, `download_arxiv_pdf`, `batch_download` |
| **stock** | 5 | `get_stock_kline`, `get_stock_snapshot`, `get_stock_financials`, `analyze_stock` |
| **economics** | 5 | `get_fred_series`, `get_world_bank_indicator`, `get_exchange_rates`, `get_economic_calendar` |

工具通过 `CategorizedToolRegistry` 统一管理，支持按类别搜索和技能级工具过滤。

### skills — 技能框架

**路径**: `src/skills/`

基于 `SKILL.md` 的声明式技能系统：

**工作流程**：
1. **发现阶段**：`SkillRegistry` 扫描 `skills/` 目录，解析 `SKILL.md` 生成轻量 `SkillSummary`
2. **目录注入**：将技能目录注入系统提示词，让 LLM 知道可用技能
3. **激活阶段**：用户或 LLM 激活技能后，加载完整 `SkillSpec`，注入详细上下文
4. **工具过滤**：根据技能的 `SkillToolPolicy` 过滤可用工具集
5. **工作空间**：`SkillWorkspaceManager` 为每个技能创建会话级目录结构

**SKILL.md 结构**：
```markdown
# Skill Name
## Purpose / Description
## Tags
## When to use / When NOT to use
## Inputs expected / Output requirements
## Workspace structure
## Workflow (分阶段)
## Available tools
## Constraints / Success criteria
```

**内置技能**：
- `recommendation_research` — 推荐系统/广告模型论文研究与复现

### util — 工具类

**路径**: `src/util/`

| 文件 | 职责 |
|------|------|
| `db.py` | `DatabaseManager`：SQLite 连接管理、WAL 模式、自动迁移、CRUD 便捷方法 |
| `logger.py` | 基于 loguru 的日志系统，支持文件 + 控制台输出 |
| `mail_utils.py` | 邮件发送工具 |
| `parallel.py` | 并行执行工具 |

---

## 工具清单

<details>
<summary>点击展开完整工具清单（50+ 工具）</summary>

### Utility
- `weather` — 天气查询（Mock）
- `calculator` — 数学计算
- `time` — 当前时间
- `search` — 知识搜索

### Search
- `google_search` — Google 搜索（SerpAPI）
- `exa_search` — Exa AI 神经语义搜索

### File
- `read_file` — 读取文件内容
- `write_file` — 写入文件
- `append_file` — 追加内容到文件
- `edit_file` — SEARCH/REPLACE 格式编辑文件
- `list_directory` — 列出目录内容
- `search_in_files` — 在文件中搜索文本
- `move_file` — 移动/重命名文件
- `copy_file` — 复制文件
- `delete_file` — 删除文件
- `get_file_info` — 获取文件元信息
- `create_directory` — 创建目录

### Bash / Shell
- `run_command` — 执行 Shell 命令
- `run_python_script` — 执行 Python 脚本
- `run_bash_script` — 执行 Bash 脚本
- `get_system_info` — 获取系统信息
- `get_env_var` — 获取环境变量

### Codebase Search
- `grep_search` — 正则表达式搜索文件内容
- `glob_search` — 按文件名模式搜索
- `ls_directory` — 快速列出目录内容

### PDF
- `read_pdf` — 读取 PDF 内容
- `get_pdf_metadata` — 获取 PDF 元数据
- `merge_pdfs` — 合并多个 PDF
- `split_pdf` — 拆分 PDF
- `create_pdf_from_text` — 从文本创建 PDF
- `extract_pdf_images` — 提取 PDF 中的图片

### PowerPoint
- `create_presentation` — 创建 PPT
- `read_presentation` — 读取 PPT 内容
- `add_slide` — 添加幻灯片
- `export_ppt_to_pdf` — PPT 导出为 PDF

### Web
- `fetch_url` — 获取 URL 内容
- `extract_webpage_text` — 提取网页文本
- `extract_links` — 提取网页链接
- `download_file` — 下载文件
- `check_url` — 检查 URL 可用性
- `fetch_rss_feed` — 获取 RSS 订阅

### Data
- `read_csv` — 读取 CSV 文件
- `read_json_file` — 读取 JSON 文件
- `read_excel` — 读取 Excel 文件
- `analyze_dataframe` — 数据统计分析
- `filter_csv` — 过滤 CSV 数据
- `save_as_csv` — 保存为 CSV
- `convert_data_format` — 数据格式转换

### Reader
- `convert_url_to_markdown` — URL 转 Markdown
- `convert_pdf_to_markdown` — PDF 转 Markdown
- `convert_html_to_markdown` — HTML 转 Markdown
- `convert_file_to_markdown` — 通用文件转 Markdown

### arXiv
- `search_arxiv` — 搜索 arXiv 论文
- `get_arxiv_paper_details` — 获取论文详情
- `download_arxiv_pdf` — 下载论文 PDF
- `batch_download_arxiv_pdfs` — 批量下载论文

### Stock
- `get_stock_kline` — 获取股票 K 线数据
- `get_stock_snapshot` — 获取股票快照
- `get_stock_financials` — 获取财务数据
- `get_stock_info` — 获取公司信息
- `analyze_stock` — 综合股票分析

### Economics
- `get_fred_series` — FRED 经济数据序列
- `get_world_bank_indicator` — 世界银行指标
- `get_exchange_rates` — 汇率查询
- `get_economic_calendar` — 经济日历
- `generate_economic_report` — 生成经济报告

</details>

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

```bash
# 生成默认配置文件
python main.py --init-config

# 编辑配置，填入 API Key
vim config/config.yaml
```

或通过环境变量配置：

```bash
export ANTHROPIC_API_KEY="your-api-key"
export LLM_PROVIDER="anthropic"
export EXA_API_KEY=""
export SERPAPI_KEY=""
export ANTHROPIC_API_KEY=""
export ANTHROPIC_BASE_URL="https://api.kimi.com/coding/"
export ENABLE_TOOL_SEARCH=false
```

### 3. 运行

```bash
# 交互模式（默认）
python main.py

# 单次查询
python main.py --query "分析一下苹果公司最近的股价走势"

# 指定 Provider
python main.py --provider anthropic --model kimi-k2.5

# 使用新架构（默认已启用）
python main.py --new-arch
```

### 4. 交互命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/tools` | 列出已注册工具 |
| `/skills` | 列出可用技能 |
| `/skill <name>` | 激活指定技能 |
| `/clear` | 清除对话历史 |
| `/history` | 显示对话历史 |
| `/quit` | 退出 |

---

## 配置说明

配置文件位于 `config/config.yaml`，支持以下配置项：

```yaml
# LLM Provider 选择
provider: "anthropic"          # anthropic / kimi / openai

# Provider 配置
providers:
  anthropic:
    api_key: ""                # 或通过 ANTHROPIC_API_KEY 环境变量
    base_url: "https://api.kimi.com/coding/"
    model: "kimi-k2.5"
    temperature: 0.7
    max_tokens: 16384
  kimi:
    api_key: ""                # 或通过 KIMI_API_KEY 环境变量
    base_url: "https://api.moonshot.cn/v1"
    model: "moonshot-v1-32k"

# 全局引擎设置
timeout: 3600                  # 请求超时（秒）
max_iterations: 200            # 最大工具调用轮次

# 路径配置
paths:
  log_dir: "logs"
  result_dir: "results"
  data_dir: "data"
  temp_dir: "tmp"

# 日志级别
log_level: "INFO"
```

**优先级**：CLI 参数 > 环境变量 > 配置文件 > 默认值

---

## 测试

```bash
# 运行所有单元测试
python -m pytest tests/unit/ -v

# 运行集成测试
python -m pytest tests/integration/ -v

# 运行特定测试
python -m pytest tests/unit/test_edit_manager.py -v
python -m pytest tests/unit/test_skills.py -v
```

---

## 技术栈与依赖

| 类别 | 依赖 |
|------|------|
| **HTTP 客户端** | `requests` |
| **配置解析** | `pyyaml` |
| **数据验证** | `jsonschema` |
| **日志** | `loguru` |
| **数据分析** | `pandas`, `numpy` |
| **可视化** | `matplotlib`, `seaborn` |
| **股票数据** | `yfinance` |
| **Excel** | `openpyxl` |
| **Web 搜索** | `exa-py` |
| **Web 爬取** | `beautifulsoup4`, `lxml` |
| **RSS 解析** | `feedparser` |
| **PDF 处理** | `pymupdf` |
| **PPT 处理** | `python-pptx` |
| **系统监控** | `psutil` |
| **终端交互** | `prompt_toolkit` |
| **文档转换** | `reader` (vakra-dev) |
| **数据库** | `sqlite3` (内置) |

---

## 代码统计

| 指标 | 数值 |
|------|------|
| 源码文件数 | ~59 个 `.py` 文件 |
| 源码总行数 | ~16,300 行 |
| 模块数 | 10 个子模块 |
| 内置工具数 | 50+ |
| 工具类别数 | 13 |
| 数据库表 | 5 张 + 2 个视图 + 1 个 FTS 虚拟表 |
| 事件类型 | 20+ |

---

## License

Private Project — All Rights Reserved.
