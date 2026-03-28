# Agent X1 核心架构重设计方案

对 Agent X1 的 Session、Runtime、Context、Memory、Prompt、Loop 六大模块进行系统性重设计，参考 Claude Code / Codex / Gemini CLI 的工程实践，在保留现有工具生态和 Skill 框架的基础上，构建分层清晰、可扩展的 agent runtime。

---

## 一、现状问题总结

| 模块 | 现状 | 核心问题 |
|------|------|---------|
| **Session** | `SessionManager` 仅做 Markdown 日志记录 | 无持久化状态、无 resume/fork、无 checkpoint、无结构化存储 |
| **Runtime** | 散落在 `main.py` + `BaseEngine.chat()` | Loop/Engine/Tool执行 三者耦合，无状态机、无错误恢复 |
| **Context** | 系统 prompt 为 config.yaml 中的静态字符串 | 无分层注入、无 token 预算、无滑动窗口、无压缩 |
| **Memory** | 仅 `history_session.md` 文件追加 | 无跨会话检索、无结构化记忆、无遗忘策略 |
| **Prompt** | 单一字符串 + Skill 层叠 | 非组件化、无运行模式感知、不可测试 |
| **Loop** | 嵌入 `KimiEngine.chat()` / `AnthropicEngine.chat()` 的 while 循环 | 重复实现、无 loop detection、无 context overflow 处理 |

---

## 二、设计目标

1. **Loop 与 Engine 分离** — Loop 在上层编排，Engine 只负责单次 LLM 调用
2. **Context 分层可控** — 分层注入 + token 预算 + 自动压缩
3. **Session 可持久化** — SQLite 存储，支持 resume / checkpoint
4. **Memory 结构化** — SQLite + 本地文件，支持跨会话检索
5. **Prompt 组件化** — PromptProvider + Section Renderer，按运行状态动态组装
6. **最小破坏性** — 保留 `src/tools/`、`src/skills/`、`src/engine/` 的现有接口

---

## 三、目标目录结构

```
src/
├── core/                          # 核心基础设施（保留+扩展）
│   ├── __init__.py
│   ├── config.py                  # [保留] AppConfig / LLMConfig / PathConfig
│   ├── models.py                  # [修改] 扩展 Message，增加 token_count / importance
│   ├── tool.py                    # [保留] Tool / ToolRegistry
│   ├── edit_manager.py            # [保留]
│   └── events.py                  # [新建] 事件总线，AgentEvent 枚举 + EventBus
│
├── session/                       # Session 生命周期管理 ★新建
│   ├── __init__.py
│   ├── session.py                 # Session 数据模型 + 状态机
│   ├── session_manager.py         # 创建/恢复/列出/归档 Session
│   ├── session_store.py           # SQLite 持久化层
│   └── checkpoint.py              # Checkpoint 快照与恢复
│
├── runtime/                       # Agent Runtime 运行时 ★新建
│   ├── __init__.py
│   ├── agent_loop.py              # 统一 Agent Loop（状态机驱动）
│   ├── turn.py                    # 单次 Turn 封装（LLM call + tool exec）
│   ├── loop_detector.py           # Loop / 死循环检测
│   └── tool_scheduler.py          # 工具执行调度（状态机：validate→approve→exec）
│
├── context/                       # Context 上下文工程 ★新建
│   ├── __init__.py
│   ├── context_window.py          # Token 预算管理 + 滑动窗口
│   ├── context_assembler.py       # 分层 Context 组装器
│   ├── context_compressor.py      # 历史压缩（摘要 + 截断）
│   └── token_counter.py           # Token 计数工具
│
├── memory/                        # Memory 记忆系统 ★新建
│   ├── __init__.py
│   ├── memory_store.py            # SQLite 持久化（episodic + semantic）
│   ├── episodic_memory.py         # 情景记忆（会话事件记录 + 检索）
│   ├── semantic_memory.py         # 语义记忆（项目知识 / 用户偏好）
│   ├── memory_controller.py       # 记忆读写控制 + 遗忘曲线
│   └── project_memory.py          # PROJECT.md / AGENT.md 发现与加载
│
├── prompt/                        # Prompt 工程 ★新建
│   ├── __init__.py
│   ├── prompt_provider.py         # Prompt 组装总入口
│   ├── sections.py                # Section Renderers（preamble, mandates, tools, skills...）
│   └── templates/                 # 静态 prompt 模板片段
│       ├── base_system.md
│       └── compression.md
│
├── engine/                        # LLM Engine（保留+精简）
│   ├── __init__.py                # [保留] EngineRegistry / create_engine
│   ├── base.py                    # [修改] 精简为纯 LLM 调用接口
│   ├── kimi_engine.py             # [修改] 移除 loop，只保留 _call_llm / _parse_response
│   └── anthropic_engine.py        # [修改] 同上
│
├── skills/                        # Skill 框架（保留）
│   ├── __init__.py
│   ├── models.py
│   ├── loader.py
│   ├── registry.py
│   ├── context_manager.py
│   └── workspace.py
│
├── tools/                         # 工具集（保留）
│   └── ...                        # 全部保留不动
│
├── util/                          # 工具函数（保留+扩展）
│   ├── logger.py                  # [保留]
│   ├── mail_utils.py              # [保留]
│   ├── parallel.py                # [保留]
│   └── db.py                      # [新建] SQLite 连接管理 + migration
│
├── __init__.py                    # [修改] 更新 create_agent 工厂函数
│
data/                              # 数据目录（项目根）
├── agent_x1.db                    # SQLite 主数据库
└── migrations/                    # Schema 迁移脚本
    └── 001_init.sql
```

**变更统计**: 新建 ~18 个文件，修改 ~6 个文件，保留 tools/skills 全部不动

---

## 四、各模块详细设计

### 4.1 Session — 可持久化的会话生命周期

**参考**: Claude Code JSONL + Checkpoint, Codex FSM + Resume, Gemini CLI session management

#### 状态机

```
[*] → CREATED → ACTIVE → PAUSED → ACTIVE (resume)
                  ↓                    ↓
              COMPACTING → ACTIVE    FORKED → ACTIVE
                  ↓
              COMPLETED / FAILED → ARCHIVED
```

#### SQLite Schema（`sessions` 表）

| 列 | 类型 | 说明 |
|----|------|------|
| id | TEXT PK | UUID |
| parent_id | TEXT NULL | fork 来源 |
| name | TEXT | 可选命名 |
| status | TEXT | CREATED/ACTIVE/PAUSED/COMPLETED/FAILED/ARCHIVED |
| created_at | REAL | timestamp |
| updated_at | REAL | timestamp |
| config_snapshot | TEXT | JSON 序列化的关键配置 |
| token_budget_total | INT | 总预算 |
| token_budget_used | INT | 已用 |

#### SQLite Schema（`turns` 表）

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER PK | 自增 |
| session_id | TEXT FK | 关联 session |
| turn_number | INT | 轮次序号 |
| role | TEXT | user/assistant/tool/system |
| content | TEXT | 消息内容 |
| tool_calls | TEXT NULL | JSON 序列化的 tool calls |
| tool_call_id | TEXT NULL | tool result 关联 |
| token_count | INT | 该条消息的 token 数 |
| importance | REAL | 重要性评分 (0-1) |
| created_at | REAL | timestamp |

#### 关键接口

```python
class SessionManager:
    def create_session(name=None, parent_id=None) -> Session
    def resume_session(session_id) -> Session
    def list_sessions(status=None, limit=20) -> List[SessionSummary]
    def checkpoint(session_id) -> str  # 返回 checkpoint_id
    def archive_session(session_id) -> None
```

**优点**: 结构化存储，支持 resume，可查询历史
**代价**: 引入 SQLite 依赖（Python 内置，无额外安装）

---

### 4.2 Runtime — Agent Loop 状态机

**参考**: Codex AgentState 枚举 + 递归 loop, Gemini CLI GeminiClient → Turn → GeminiChat 三层分工

#### 核心思路

将现有 `BaseEngine.chat()` 中的 while 循环提取到独立的 `AgentLoop`，Engine 降级为纯 LLM 调用层。

#### AgentLoop 状态机

```python
class AgentState(Enum):
    IDLE = "idle"
    ASSEMBLING_CONTEXT = "assembling_context"
    INFERENCING = "inferencing"
    EXECUTING_TOOLS = "executing_tools"
    COMPACTING = "compacting"
    COMPLETED = "completed"
    ERROR = "error"
```

#### AgentLoop 核心流程

```
用户输入 → ASSEMBLING_CONTEXT
  → context_assembler.build(session, user_msg)
  → INFERENCING
    → engine.call_llm(messages, tools)
    → parse response
    → if tool_calls: → EXECUTING_TOOLS
      → tool_scheduler.execute(tool_calls)
      → add results to session
      → loop_detector.check()
      → 回到 INFERENCING
    → if text response: → COMPLETED
  → if context overflow: → COMPACTING
    → context_compressor.compress(session)
    → 回到 INFERENCING
```

#### Engine 接口精简

```python
class BaseEngine(ABC):
    def call_llm(self, messages, tools, system_prompt) -> LLMResponse
    # 移除 chat()、messages 列表、循环逻辑
```

#### Turn 封装

```python
@dataclass
class Turn:
    turn_number: int
    user_input: str
    llm_responses: List[LLMResponse]
    tool_calls: List[ToolCallRecord]
    final_text: str
    token_usage: TokenUsage
    duration_ms: float
```

#### ToolScheduler 状态机

```
VALIDATING → APPROVED → EXECUTING → SUCCESS/ERROR/CANCELLED
```

简化版，不做用户审批（当前项目非 IDE 模式），但预留接口。

**优点**: Loop 逻辑统一、可测试、引擎可替换、新增 provider 无需重写 loop
**代价**: 需要重构 engine 接口，现有 `chat()` 方法废弃

---

### 4.3 Context — 分层注入 + Token 预算

**参考**: Claude Code 分层 Context Stack, Codex 5-layer, Gemini CLI 4-layer context

#### 分层模型（优先级从高到低）

| 层 | 内容 | 生命周期 | 可驱逐 |
|----|------|---------|--------|
| L1 System | 组装后的 system prompt | 固定 | 否 |
| L2 Project | PROJECT.md / AGENT.md 内容 | 项目级 | 否 |
| L3 Skill | 激活 skill 的 full context | Skill 激活期 | 是 |
| L4 Memory | 检索到的相关记忆 | 按需 | 是 |
| L5 History | 对话历史 | 会话级 | 是（压缩） |
| L6 Tool Output | 工具执行结果 | 单次 | 是（截断） |
| L7 User | 当前用户消息 | 单次 | 否 |

#### ContextWindow

```python
class ContextWindow:
    max_tokens: int          # 模型上下文窗口大小
    reserve_tokens: int      # 留给响应的 token
    warning_threshold: float # 0.8 — 触发警告
    critical_threshold: float # 0.95 — 触发压缩

    def fits(self, messages) -> bool
    def remaining_budget(self) -> int
    def should_compress(self) -> bool
```

#### ContextAssembler

```python
class ContextAssembler:
    def build(self, session, user_message, skill_ctx=None) -> List[Message]:
        """按层组装，自动裁剪以适配 token 预算"""
```

#### ContextCompressor

```python
class ContextCompressor:
    def compress(self, session) -> CompactionResult:
        """
        1. 截断大型 tool outputs（>2000 chars → 保留摘要）
        2. 对旧对话做 LLM 摘要
        3. 用 [summary] + [recent N turns] 替换完整历史
        """
```

**优点**: Token 使用可控，防止 context overflow 导致 API 失败
**代价**: 压缩需要额外 LLM 调用（可选，fallback 为简单截断）

---

### 4.4 Memory — SQLite 持久化记忆

**参考**: Claude Code 4-tier memory, Codex Episodic + Semantic, Gemini CLI GEMINI.md hierarchy

#### 设计选择

- **不引入向量数据库** — 项目规模不需要，用 SQLite FTS5 全文搜索 + 关键词匹配即可
- **不引入 embedding API** — 避免外部依赖，用 TF-IDF / BM25 近似检索
- **保留文件形式的 project memory** — 类似 CLAUDE.md / GEMINI.md，用 `PROJECT.md` 存放项目级上下文

#### SQLite Schema（`episodic_memory` 表）

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER PK | 自增 |
| session_id | TEXT | 来源会话 |
| type | TEXT | decision / action / outcome / error / note |
| content | TEXT | 记忆内容 |
| importance | REAL | 0-1 重要性 |
| access_count | INT | 被检索次数 |
| created_at | REAL | 创建时间 |
| last_accessed | REAL | 最后访问时间 |

#### SQLite Schema（`semantic_memory` 表）

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER PK | 自增 |
| category | TEXT | preference / convention / fact / pattern |
| key | TEXT | 记忆键（如 "user_prefers_chinese"） |
| value | TEXT | 记忆值 |
| confidence | REAL | 置信度 |
| source_session | TEXT | 来源 |
| created_at | REAL | |
| updated_at | REAL | |

#### 遗忘曲线

```python
def retention_score(memory, now) -> float:
    age_days = (now - memory.created_at) / 86400
    importance = memory.importance
    return importance * exp(-0.3 * age_days / max(importance, 0.1))
```

- 每次 session 启动时运行清理，删除 `retention_score < 0.05` 的 episodic memory
- semantic memory 不自动删除，需手动管理

#### 检索接口

```python
class MemoryController:
    def retrieve_relevant(self, query, top_k=5) -> List[MemoryItem]
    def store_episodic(self, session_id, type, content, importance) -> None
    def store_semantic(self, category, key, value) -> None
    def cleanup_expired() -> int  # 返回清理数量
```

#### PROJECT.md 发现

- 搜索路径: `~/.agent_x1/PROJECT.md` → `{cwd}/PROJECT.md` → `{cwd}/.agent_x1/PROJECT.md`
- 优先级: 项目级 > 全局级
- 自动注入到 Context L2 层

**优点**: 轻量、无外部依赖、支持跨会话检索
**代价**: 检索精度不如向量搜索（对当前项目规模够用）

---

### 4.5 Prompt — 组件化组装

**参考**: Gemini CLI PromptProvider + snippets 组件化, Claude Code 分层 CLAUDE.md

#### 核心思路

将静态 system prompt 字符串替换为 **PromptProvider + Section Renderers** 模式。

#### PromptProvider

```python
class PromptProvider:
    def build_system_prompt(self, context: PromptContext) -> str:
        """根据运行时状态组装 system prompt"""
        sections = []
        sections.append(render_preamble(context))
        sections.append(render_core_mandates(context))
        sections.append(render_available_tools(context))
        if context.skills:
            sections.append(render_skills_catalog(context))
        if context.active_skill:
            sections.append(render_active_skill(context))
        if context.project_memory:
            sections.append(render_project_context(context))
        sections.append(render_operational_guidelines(context))
        return "\n\n---\n\n".join(s for s in sections if s)
```

#### PromptContext

```python
@dataclass
class PromptContext:
    mode: str                        # "interactive" / "single" / "headless"
    tools: List[str]                 # 可用工具名列表
    skills: List[SkillSummary]       # 已发现 skills
    active_skill: Optional[SkillSpec]
    project_memory: str              # PROJECT.md 内容
    user_preferences: Dict[str, str] # 用户偏好
    model_name: str                  # 当前模型
    max_tokens: int                  # 模型上下文窗口
```

#### Section 示例

```python
def render_preamble(ctx: PromptContext) -> str:
    return (
        f"You are Agent X1, an autonomous AI assistant specializing in "
        f"research, analysis, and software engineering tasks.\n"
        f"Current mode: {ctx.mode}\n"
        f"Model: {ctx.model_name}"
    )

def render_core_mandates(ctx: PromptContext) -> str:
    return """## Core Mandates
1. Always verify before acting — read files before editing
2. Prefer minimal changes over large rewrites
3. Explain your reasoning before executing tools
4. Follow project conventions from PROJECT.md"""
```

#### 压缩 Prompt

独立的 `templates/compression.md` 模板，用于 ContextCompressor 调用 LLM 做摘要时使用。

**优点**: 可测试、可按模式裁剪、易于迭代、新 section 即插即用
**代价**: 初次实现需要将现有 prompt 拆分为多个 section

---

### 4.6 Loop — 统一 Agent Loop

**参考**: Gemini CLI 的 CLI→GeminiClient→Turn 三层, Codex AgentLoop 状态机

#### 从 Engine 中提取 Loop

**Before** (当前):
```
main.py → engine.chat(user_input)
  └─ while loop 在 engine 内部
    └─ _call_llm() → _parse_response() → _execute_tools() → 循环
```

**After** (目标):
```
main.py → agent_loop.run(user_input)
  └─ AgentLoop 状态机驱动
    ├─ context_assembler.build()       # 组装上下文
    ├─ engine.call_llm()               # 纯 LLM 调用
    ├─ tool_scheduler.execute()        # 工具执行
    ├─ context_compressor.compress()   # 按需压缩
    ├─ loop_detector.check()           # 死循环检测
    └─ session.record_turn()           # 持久化
```

#### Loop Detection

```python
class LoopDetector:
    """检测重复的 tool call 模式"""
    window_size: int = 6
    similarity_threshold: float = 0.9

    def check(self, recent_tool_calls) -> bool:
        """如果最近 N 次 tool calls 形成重复模式，返回 True"""
```

检测到 loop 时，向 LLM 注入提示: `"Warning: You appear to be repeating the same actions. Please try a different approach or summarize your findings."`

#### 统一入口

```python
class AgentLoop:
    def __init__(self, engine, session, context_assembler, ...):
        ...

    async def run(self, user_input: str) -> str:
        """统一的 agent loop 入口，取代 engine.chat()"""

    def run_sync(self, user_input: str) -> str:
        """同步包装"""
```

**优点**: 一处实现、所有 provider 共享、可加入 hook 和事件
**代价**: 需要重构 main.py 的调用链

---

## 五、实施路线图

### Phase 1: 基础设施（预计 2-3 天）

1. 新建 `src/util/db.py` — SQLite 连接管理 + migration
2. 新建 `data/migrations/001_init.sql` — 建表
3. 新建 `src/core/events.py` — 事件枚举
4. 修改 `src/core/models.py` — Message 增加 `token_count`, `importance` 字段

### Phase 2: Session 模块（预计 2 天）

5. 新建 `src/session/` 全部文件
6. 迁移 `src/core/session_manager.py` 的日志功能到新 Session 模块
7. 保留旧 `session_manager.py` 做兼容层（deprecated wrapper）

### Phase 3: Prompt + Context（预计 2-3 天）

8. 新建 `src/prompt/` 全部文件
9. 新建 `src/context/` 全部文件
10. 将 config.yaml 的 system_prompt 迁移为 prompt templates

### Phase 4: Memory 模块（预计 2 天）

11. 新建 `src/memory/` 全部文件
12. 实现 PROJECT.md 发现与加载
13. 实现 episodic/semantic 存储 + 检索 + 遗忘

### Phase 5: Runtime + Loop（预计 3 天）

14. 新建 `src/runtime/` 全部文件
15. 修改 `src/engine/base.py` — 精简为纯调用接口
16. 修改 `src/engine/kimi_engine.py` + `anthropic_engine.py` — 移除 loop
17. 修改 `main.py` — 使用 AgentLoop 替代 engine.chat()
18. 修改 `src/__init__.py` — 更新 create_agent

### Phase 6: 集成测试（预计 1-2 天）

19. 更新 `tests/` — Session/Memory/Context/Loop 单元测试
20. 端到端集成测试

**总计预估: 12-15 天**

---

## 六、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Engine 接口变更导致现有功能中断 | 高 | 旧 `chat()` 保留为 deprecated 兼容方法 |
| SQLite 并发写入冲突 | 低 | 单进程场景，用 WAL 模式 |
| Context 压缩丢失关键信息 | 中 | 先用简单截断，LLM 摘要作为可选增强 |
| 改动范围过大一次性无法完成 | 中 | 按 Phase 逐步实施，每个 Phase 独立可用 |

---

## 七、不做的事情（Scope Out）

- **不引入向量数据库** — 用 SQLite FTS5 满足当前需求
- **不引入 embedding API** — 避免额外外部调用成本
- **不实现 Multi-Agent / Sub-Agent** — 当前单 Agent 够用，预留接口即可
- **不实现 MCP 协议** — 当前工具系统已满足需求
- **不改动 tools/ 和 skills/** — 这两个模块设计良好，保持不动
- **不做 sandbox/沙箱** — 当前场景不需要进程隔离

