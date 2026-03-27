# Agent X1 Skill Framework — Design & Implementation

本文档记录 Agent X1 的 Skill 框架设计方案与实现状态。

## 1. 设计目标

在不破坏现有 `engine + tools + session` 架构的前提下，增加一套：
- 面向 Anthropic Skill 规范
- 可插拔、模块化
- 二阶段加载（先摘要后全文）
- 与现有 tools 协同工作
的专业化 skill 框架，并优先落地一个「广告/推荐模型 research」skill。

## 2. 架构概览

### 2.1 新增核心抽象

| 抽象 | 文件 | 职责 |
|------|------|------|
| `SkillSpec` | `src/skills/loader.py` | 解析后的 SKILL.md 完整对象 |
| `SkillSummary` | `src/skills/models.py` | 轻量摘要，用于 catalog 注入 |
| `SkillRegistry` | `src/skills/registry.py` | 扫描/索引/检索 skill |
| `SkillContextManager` | `src/skills/context_manager.py` | 生命周期、prompt 组装、tool 过滤 |
| `SkillWorkspaceManager` | `src/skills/workspace.py` | session 内 skill 工作目录管理 |
| `SkillRuntimeState` | `src/skills/models.py` | 运行时状态（阶段、artifact、notes） |

### 2.2 与现有模块的衔接

- `BaseEngine` 新增：`skill_context` / `get_effective_system_prompt()` / `get_effective_tools()`
- `AnthropicEngine.chat()` 每轮使用 `get_effective_system_prompt()` 和 `get_effective_tools()`
- `KimiEngine._call_llm()` 同上
- `main.py` 新增 `/skills` 和 `/skill <name>` 交互命令
- `src/__init__.py:create_agent()` 自动扫描 `skills/` 目录并注入 skill context

## 3. 目录结构

```
agent_x1/
├── src/skills/                  # 运行时框架
│   ├── __init__.py
│   ├── models.py                # SkillStatus, SkillMetadata, SkillSummary, etc.
│   ├── loader.py                # SKILL.md parser → SkillSpec
│   ├── registry.py              # SkillRegistry
│   ├── context_manager.py       # SkillContextManager
│   └── workspace.py             # SkillWorkspaceManager
├── skills/                      # Skill 内容（每个子目录一个 skill）
│   └── recommendation_research/
│       └── SKILL.md
├── tests/unit/
│   └── test_skills.py           # 框架单元测试
└── docs/
    └── skill_framework_plan.md  # 本文件
```

## 4. Skill 文件格式（Anthropic 风格）

每个 skill 以 `skills/<skill_name>/SKILL.md` 为权威定义，结构如下：

```markdown
# Skill Title

## Purpose
## Description
## Tags
## When to use
## When NOT to use
## Inputs expected
## Output requirements
## Workspace structure
## Workflow (phases)
## Available tools
## Domain heuristics
## Constraints
## Success criteria
```

`loader.py` 将其解析为：
- **summary_view** → `SkillSummary`（name, description, tags, when_to_use 等）
- **full_view** → `SkillSpec`（全部 sections, raw markdown, tool policy）

## 5. Prompt 与上下文管理

### 5.1 分层 Prompt 组装

`SkillContextManager.build_system_prompt(base_prompt)` 按 4 层拼装：

1. **Base Layer** — 原始 `config.system_prompt`
2. **Catalog Layer** — 所有 skill 的摘要 catalog（始终注入）
3. **Active Skill Layer** — 激活 skill 的完整 SKILL.md（仅激活后注入）
4. **Runtime Layer** — 当前阶段、artifact、workspace 状态（仅运行中注入）

### 5.2 二阶段加载

- **Phase 1 (Discovery)**: 只注入 skill catalog summary → 模型知道有哪些专业能力
- **Phase 2 (Activation)**: 用户/模型选择 skill 后，加载完整 SKILL.md + 创建 workspace

## 6. Skills 与 Tools 协同

### 6.1 角色分工
- **Tools**: 原子能力，负责"做事"
- **Skills**: 专业策略，负责"何时做、按什么顺序做、做到什么标准"

### 6.2 Tool 过滤机制

`SkillToolPolicy` 定义：
- `preferred_categories` — 优先暴露的工具类别
- `preferred_tools` — 优先暴露的具体工具
- `blocked_tools` — 明确屏蔽的工具

激活 skill 后，`get_effective_tools()` 仅返回符合策略的工具子集。

### 6.3 当前 research skill 优先工具集
- category: `arxiv`, `reader`, `file`, `bash`, `data`, `web`

## 7. 广告/推荐 Research Skill 设计

**Skill 名称**: `recommendation_research`

**覆盖范围**:
- arXiv 论文搜索与下载
- PDF 转 markdown
- 模型实现（target + baseline）
- 小数据集下载（< 200MB）
- 语法检查、单测、smoke test
- AUC/LogLoss 指标验证
- 持续优化与报告

**9 阶段工作流**:
1. Environment Setup
2. Paper Discovery & Retrieval
3. Paper Analysis & Notes
4. Dataset Acquisition
5. Model Implementation
6. Training Pipeline
7. Validation & Testing
8. Optimization
9. Reporting

**领域启发式**:
- 优先从 LR → FM → DeepFM → DCN 等经典模型起步
- 小数据优先验证管线正确性
- Criteo/Avazu（广告CTR）、MovieLens（推荐）为默认数据集选择
- AUC > 0.5 为最低门槛，Criteo 上 DeepFM 约 0.801

**详细内容见**: `skills/recommendation_research/SKILL.md`

## 8. Engine 改造要点

### BaseEngine 新增接口

```python
# Properties
engine.skill_context -> Optional[SkillContextManager]

# Methods
engine.set_skill_context(ctx)
engine.set_tool_categories(categories)
engine.get_effective_system_prompt() -> str
engine.get_effective_tools() -> Dict[str, Tool]
```

### AnthropicEngine 改造
- `chat()` 中使用 `get_effective_tools()` 取代 `self.tools`
- `_call_anthropic_api()` 中使用 `get_effective_system_prompt()` 取代 `self.system_prompt`
- `_convert_tools_to_anthropic()` 接受可选 tools 参数

### KimiEngine 改造
- `_call_llm()` 中使用 `get_effective_system_prompt()` 和 `get_effective_tools()`

## 9. 交互命令

`main.py` 新增：
- `/skills` — 列出所有已发现的 skill
- `/skill <name>` — 激活指定 skill

## 10. 测试

`tests/unit/test_skills.py` 覆盖：
- Model 枚举与 dataclass
- SKILL.md 解析（正常、缺失、空文件）
- Registry 发现、搜索、catalog 生成
- Workspace 目录创建与 artifact 列表
- ContextManager 激活/停用、prompt 组装、tool 过滤
- Engine 集成（skill context 注入）
- 真实 `recommendation_research` SKILL.md 加载验证

## 11. 实现状态

| 模块 | 状态 |
|------|------|
| `src/skills/models.py` | ✅ 完成 |
| `src/skills/loader.py` | ✅ 完成 |
| `src/skills/registry.py` | ✅ 完成 |
| `src/skills/context_manager.py` | ✅ 完成 |
| `src/skills/workspace.py` | ✅ 完成 |
| `src/skills/__init__.py` | ✅ 完成 |
| `BaseEngine` skill 接口 | ✅ 完成 |
| `AnthropicEngine` skill-aware | ✅ 完成 |
| `KimiEngine` skill-aware | ✅ 完成 |
| `main.py` 命令集成 | ✅ 完成 |
| `src/__init__.py` create_agent | ✅ 完成 |
| `skills/recommendation_research/SKILL.md` | ✅ 完成 |
| `tests/unit/test_skills.py` | ✅ 完成 |
| `docs/skill_framework_plan.md` | ✅ 完成 |

## 12. 后续扩展方向

- SessionManager 增加 skill 维度事件记录（phase 推进、artifact 产出）
- 长对话上下文压缩策略（immutable / semi-static / volatile 分层）
- skill 模板与脚手架（`templates/`, `prompts/` 子目录）
- 更多 skill：NLP Research, Data Analysis, Report Generation 等
- skill 编写指南文档（`docs/skills/skill_authoring_guide.md`）
