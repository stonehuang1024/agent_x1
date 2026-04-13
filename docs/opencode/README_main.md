# OpenCode 项目详细解读

本文基于 `opencode` 当前代码实现进行解读，目标是回答这些问题：

- **整体设计是什么**
- **代码目录如何组织，核心模块分别负责什么**
- **LLM 是如何接入与调用的**
- **agent loop 是怎样运行的**
- **prompt 如何设计，尤其是 system prompt**
- **上下文如何管理、压缩与恢复**
- **skills 和 rules/permissions 是怎么做的**
- **tool call 如何注册、暴露、执行、回写**
- **SDK 提供了哪些能力与接口**

---

# 源码文件跳转索引

这一节不是概念解释，而是“阅读地图”。如果你读完本文后想直接跳回源码，可以按下面索引进入。

## 一、建议阅读主线

- **1. CLI 与程序入口**
  - [`packages/opencode/src/index.ts`](./packages/opencode/src/index.ts)
  - 用途：看 OpenCode 如何启动、注册命令、初始化运行环境

- **2. Agent 定义与内置模式**
  - [`packages/opencode/src/agent/agent.ts`](./packages/opencode/src/agent/agent.ts)
  - 用途：看 `build`、`plan`、`general`、`explore`、`compaction`、`summary` 等 agent 如何定义

- **3. Session 主循环入口**
  - [`packages/opencode/src/session/prompt.ts`](./packages/opencode/src/session/prompt.ts)
  - 用途：看 user message 如何创建，loop 如何驱动，工具与 system prompt 如何组装

- **4. 模型输出消费器**
  - [`packages/opencode/src/session/processor.ts`](./packages/opencode/src/session/processor.ts)
  - 用途：看 text、reasoning、tool-call、finish-step、error 如何写回消息系统

- **5. 消息与 part 数据模型**
  - [`packages/opencode/src/session/message-v2.ts`](./packages/opencode/src/session/message-v2.ts)
  - 用途：看上下文、tool result、compaction、subtask 最终如何表示

- **6. LLM 请求封装**
  - [`packages/opencode/src/session/llm.ts`](./packages/opencode/src/session/llm.ts)
  - 用途：看 system prompt、tools、provider options 如何交给 AI SDK

- **7. system prompt 与 provider prompt**
  - [`packages/opencode/src/session/system.ts`](./packages/opencode/src/session/system.ts)
  - 用途：看不同模型族的基础提示词，以及环境 prompt 如何生成

- **8. instruction / 规则文件注入**
  - [`packages/opencode/src/session/instruction.ts`](./packages/opencode/src/session/instruction.ts)
  - 用途：看 `AGENTS.md`、`CLAUDE.md` 等如何进入系统上下文

- **9. 工具注册与暴露**
  - [`packages/opencode/src/tool/registry.ts`](./packages/opencode/src/tool/registry.ts)
  - 用途：看默认工具、插件工具、自定义工具如何统一注册

- **10. skills 发现与 skill tool**
  - [`packages/opencode/src/skill/skill.ts`](./packages/opencode/src/skill/skill.ts)
  - [`packages/opencode/src/tool/skill.ts`](./packages/opencode/src/tool/skill.ts)
  - 用途：看 skill 索引如何生成、skill 内容如何按需加载

- **11. 权限与规则控制**
  - [`packages/opencode/src/permission/next.ts`](./packages/opencode/src/permission/next.ts)
  - 用途：看 `allow / deny / ask` 如何拦截工具与能力调用

- **12. provider 抽象与适配**
  - [`packages/opencode/src/provider/provider.ts`](./packages/opencode/src/provider/provider.ts)
  - [`packages/opencode/src/provider/transform.ts`](./packages/opencode/src/provider/transform.ts)
  - 用途：看多模型接入、能力描述、参数适配、schema 变换

- **13. SDK 与对外接口**
  - [`packages/sdk/js/src/index.ts`](./packages/sdk/js/src/index.ts)
  - [`packages/sdk/js/src/client.ts`](./packages/sdk/js/src/client.ts)
  - [`packages/sdk/js/src/v2/index.ts`](./packages/sdk/js/src/v2/index.ts)
  - [`packages/sdk/js/src/v2/client.ts`](./packages/sdk/js/src/v2/client.ts)
  - 用途：看 OpenCode 如何把 session/runtime 能力暴露成 SDK

## 二、按主题跳转

### 1. 如果你最关心 loop

- **主文件**
  - [`packages/opencode/src/session/prompt.ts`](./packages/opencode/src/session/prompt.ts)

- **配套文件**
  - [`packages/opencode/src/session/processor.ts`](./packages/opencode/src/session/processor.ts)
  - [`packages/opencode/src/session/message-v2.ts`](./packages/opencode/src/session/message-v2.ts)
  - [`packages/opencode/src/session/llm.ts`](./packages/opencode/src/session/llm.ts)
  - [`packages/opencode/src/session/compaction.ts`](./packages/opencode/src/session/compaction.ts)

- **建议重点看这些函数/概念**
  - `prompt()`
  - `loop()`
  - `resolveTools()`
  - `insertReminders()`
  - `SessionProcessor.create().process()`
  - `MessageV2.toModelMessages()`

### 2. 如果你最关心 prompt

- **主文件**
  - [`packages/opencode/src/session/system.ts`](./packages/opencode/src/session/system.ts)
  - [`packages/opencode/src/session/prompt.ts`](./packages/opencode/src/session/prompt.ts)
  - [`packages/opencode/src/session/llm.ts`](./packages/opencode/src/session/llm.ts)

- **配套文件**
  - [`packages/opencode/src/session/instruction.ts`](./packages/opencode/src/session/instruction.ts)
  - [`packages/opencode/src/agent/agent.ts`](./packages/opencode/src/agent/agent.ts)

- **建议重点看这些概念**
  - provider prompt
  - agent prompt
  - environment prompt
  - skills prompt
  - instruction prompt
  - structured output prompt

### 3. 如果你最关心上下文管理

- **主文件**
  - [`packages/opencode/src/session/message-v2.ts`](./packages/opencode/src/session/message-v2.ts)
  - [`packages/opencode/src/session/prompt.ts`](./packages/opencode/src/session/prompt.ts)
  - [`packages/opencode/src/session/processor.ts`](./packages/opencode/src/session/processor.ts)
  - [`packages/opencode/src/session/compaction.ts`](./packages/opencode/src/session/compaction.ts)

- **建议重点看这些函数/概念**
  - `MessageV2.stream()`
  - `MessageV2.filterCompacted()`
  - `MessageV2.toModelMessages()`
  - compaction part
  - summary message
  - reminder 注入

### 4. 如果你最关心 skills

- **主文件**
  - [`packages/opencode/src/skill/skill.ts`](./packages/opencode/src/skill/skill.ts)
  - [`packages/opencode/src/tool/skill.ts`](./packages/opencode/src/tool/skill.ts)
  - [`packages/opencode/src/session/system.ts`](./packages/opencode/src/session/system.ts)

- **配套文件**
  - [`packages/opencode/src/permission/next.ts`](./packages/opencode/src/permission/next.ts)

- **建议重点看这些概念**
  - `Skill.state()`
  - `Skill.get()` / `Skill.list()`
  - `skill` tool 的 execute 路径
  - skill 索引注入 system prompt
  - skill 按需加载

### 5. 如果你最关心 tool call

- **主文件**
  - [`packages/opencode/src/tool/registry.ts`](./packages/opencode/src/tool/registry.ts)
  - [`packages/opencode/src/session/prompt.ts`](./packages/opencode/src/session/prompt.ts)
  - [`packages/opencode/src/session/processor.ts`](./packages/opencode/src/session/processor.ts)

- **配套文件**
  - [`packages/opencode/src/tool/`](./packages/opencode/src/tool/)
  - [`packages/opencode/src/tool/mcp.ts`](./packages/opencode/src/tool/mcp.ts)
  - [`packages/opencode/src/permission/next.ts`](./packages/opencode/src/permission/next.ts)

- **建议重点看这些概念**
  - Tool registry
  - tool schema 生成
  - `Tool.Context`
  - permission ask
  - tool part state machine
  - MCP 工具合流

### 6. 如果你最关心 provider / 模型接入

- **主文件**
  - [`packages/opencode/src/provider/provider.ts`](./packages/opencode/src/provider/provider.ts)
  - [`packages/opencode/src/provider/transform.ts`](./packages/opencode/src/provider/transform.ts)
  - [`packages/opencode/src/session/llm.ts`](./packages/opencode/src/session/llm.ts)

- **建议重点看这些概念**
  - provider config 合并
  - model capability 描述
  - variants / reasoning effort
  - provider option remap
  - schema transform
  - message transform

### 7. 如果你最关心 SDK / API

- **主文件**
  - [`packages/sdk/js/src/index.ts`](./packages/sdk/js/src/index.ts)
  - [`packages/sdk/js/src/client.ts`](./packages/sdk/js/src/client.ts)
  - [`packages/sdk/js/src/server.ts`](./packages/sdk/js/src/server.ts)
  - [`packages/sdk/js/src/v2/index.ts`](./packages/sdk/js/src/v2/index.ts)
  - [`packages/sdk/js/src/v2/client.ts`](./packages/sdk/js/src/v2/client.ts)
  - [`packages/sdk/js/src/v2/server.ts`](./packages/sdk/js/src/v2/server.ts)
  - [`packages/sdk/js/src/gen/client.gen.ts`](./packages/sdk/js/src/gen/client.gen.ts)
  - [`packages/sdk/js/src/gen/types.gen.ts`](./packages/sdk/js/src/gen/types.gen.ts)
  - [`packages/sdk/openapi.json`](./packages/sdk/openapi.json)

- **建议重点看这些概念**
  - OpenAPI 生成
  - typed client
  - session API
  - config API
  - command API
  - MCP API

## 三、按调用链跳转

- **从用户输入出发**
  - [`packages/opencode/src/index.ts`](./packages/opencode/src/index.ts)
  - [`packages/opencode/src/cli/`](./packages/opencode/src/cli/)
  - [`packages/opencode/src/session/prompt.ts`](./packages/opencode/src/session/prompt.ts)

- **从主循环出发**
  - [`packages/opencode/src/session/prompt.ts`](./packages/opencode/src/session/prompt.ts)
  - [`packages/opencode/src/session/processor.ts`](./packages/opencode/src/session/processor.ts)
  - [`packages/opencode/src/session/llm.ts`](./packages/opencode/src/session/llm.ts)

- **从状态模型出发**
  - [`packages/opencode/src/session/index.ts`](./packages/opencode/src/session/index.ts)
  - [`packages/opencode/src/session/message-v2.ts`](./packages/opencode/src/session/message-v2.ts)
  - [`packages/opencode/src/db/`](./packages/opencode/src/db/)

- **从能力系统出发**
  - [`packages/opencode/src/tool/registry.ts`](./packages/opencode/src/tool/registry.ts)
  - [`packages/opencode/src/tool/`](./packages/opencode/src/tool/)
  - [`packages/opencode/src/skill/skill.ts`](./packages/opencode/src/skill/skill.ts)
  - [`packages/opencode/src/permission/next.ts`](./packages/opencode/src/permission/next.ts)

- **从模型接入出发**
  - [`packages/opencode/src/provider/provider.ts`](./packages/opencode/src/provider/provider.ts)
  - [`packages/opencode/src/provider/transform.ts`](./packages/opencode/src/provider/transform.ts)
  - [`packages/opencode/src/session/llm.ts`](./packages/opencode/src/session/llm.ts)

- **从协议与对外接口出发**
  - [`packages/opencode/src/acp/`](./packages/opencode/src/acp/)
  - [`packages/sdk/js/`](./packages/sdk/js/)
  - [`packages/opencode/src/server/`](./packages/opencode/src/server/)

本文重点会放在：

- **loop**
- **prompt**
- **上下文管理**
- **skills**
- **tool call**

---

# 1. 项目整体定位

`opencode` 是一个**开源 AI coding agent**，但它并不是一个“单体 CLI 脚本”，而是一个**多包 monorepo**，核心思想是：

- **Agent Runtime 与 UI/客户端解耦**
- **Provider 无关**，底层可接 OpenAI / Anthropic / Gemini / Bedrock / OpenRouter / GitHub Copilot / 本地兼容 OpenAI 的模型等
- **消息、工具、权限、技能、上下文压缩** 都被做成独立模块
- **同一套后端能力**可以被 CLI/TUI、Web、Desktop、ACP client、SDK client 调用

从仓库根目录可以看到：

- `packages/opencode`：核心 agent/runtime/server/cli 实现
- `packages/sdk/js`：JS SDK
- `packages/app` / `packages/web` / `packages/desktop` / `packages/console`：不同客户端/界面
- `packages/plugin`：插件相关
- `packages/util`：通用工具

所以它不是“一个 CLI 工具 + 一些 prompts”，而是一套**可编排的 agent 平台**。

---

# 2. 顶层架构设计

如果从运行链路看，核心架构可以概括为：

1. **用户输入** 进入 CLI / ACP / Web 等入口
2. 进入 `SessionPrompt.prompt()` 创建 user message
3. 进入 `SessionPrompt.loop()` 驱动 agent 主循环
4. 从 Session 历史中构建上下文、system prompt、tool 集
5. 通过 `LLM.stream()` 调用模型
6. 模型输出 text / reasoning / tool-call
7. `SessionProcessor` 逐事件消费流并写回消息与 part
8. 如果有 tool call，则执行 tool，再继续 loop
9. 如果上下文过长，则触发 compaction
10. 最终 assistant message 完成，供 UI/SDK/ACP 消费

可以把它理解为几层：

- **入口层**
  - CLI
  - ACP
  - SDK
  - Server routes
- **Agent Runtime 层**
  - SessionPrompt
  - SessionProcessor
  - MessageV2
  - Session / SessionSummary / SessionCompaction
- **模型抽象层**
  - Provider
  - ProviderTransform
  - LLM
- **能力层**
  - ToolRegistry
  - MCP
  - Skill
  - PermissionNext
  - InstructionPrompt
- **存储与状态层**
  - SQLite/Drizzle
  - Session / Message / Part tables
  - Snapshot/Patch
- **扩展层**
  - Plugin hooks
  - Custom tools
  - Custom agents
  - Custom instructions
  - External skills

这套设计的一个重要特点是：

- **loop 是中心**
- **消息与 part 是状态真相来源**
- **prompt 不是静态模板，而是动态组装结果**
- **tool、skill、permission 都通过 loop 注入模型行为**

---

# 3. 代码目录结构与模块职责

重点看 `packages/opencode/src`。

## 3.1 入口与命令层

### `src/index.ts`

这是 CLI 主入口，负责：

- 初始化日志
- 设置运行环境变量
- 执行数据库迁移
- 注册所有命令
- 统一错误处理

可见它本身不是 agent loop，而是**命令调度入口**。

### `src/cli/`

CLI/TUI 相关逻辑都在这里，包括：

- `run`
- `serve`
- `acp`
- `models`
- `agent`
- `session`
- `db`
- TUI attach/thread

也就是说：CLI 只是调用核心 runtime，并不持有核心推理逻辑。

---

## 3.2 Agent 定义层

### `src/agent/agent.ts`

这里定义了 agent 的 schema 和内置 agent：

- `build`
- `plan`
- `general`
- `explore`
- `compaction`
- `title`
- `summary`

每个 agent 包含这些核心字段：

- `name`
- `description`
- `mode`：`subagent | primary | all`
- `permission`
- `model`
- `variant`
- `prompt`
- `options`
- `steps`
- `temperature`
- `topP`

### 内置 agent 的含义

- **build**
  - 默认主 agent
  - 可执行开发任务
  - 允许 question / 计划进入等
- **plan**
  - 只读分析 agent
  - 默认禁止 edit tools
- **general**
  - 通用 subagent
  - 用于复杂调研与并行多步任务
- **explore**
  - 专门做搜索/探索的 subagent
  - 有自己的 prompt：`PROMPT_EXPLORE`
- **compaction / title / summary**
  - 隐藏内部 agent
  - 用于压缩上下文、生成标题、生成摘要

### 自定义 agent

用户配置里的 `cfg.agent` 可以：

- 覆盖内置 agent
- 新增自定义 agent
- 替换 prompt / permission / model / variant / options

所以 agent 在 OpenCode 中不是“写死的人设”，而是**一种可配置的执行模式**。

---

## 3.3 Session 与消息层

### `src/session/index.ts`

`Session` 负责：

- session 基本信息存取
- title / parent-child session / fork 等
- session summary / share / revert / permission 等字段管理

Session 的核心信息包括：

- `id`
- `directory`
- `projectID`
- `workspaceID`
- `title`
- `summary`
- `permission`
- `time`

### `src/session/message-v2.ts`

这是整个 runtime 的**关键真相结构**。

它定义了：

- `UserMessage`
- `AssistantMessage`
- `Part`

其中 `Part` 又细分为：

- `text`
- `reasoning`
- `file`
- `tool`
- `step-start`
- `step-finish`
- `snapshot`
- `patch`
- `agent`
- `retry`
- `subtask`
- `compaction`

这说明 OpenCode 不是把一条消息看成一大段文本，而是把它建模为**可追踪的多部分事件流**。

### 为什么这个设计很重要

因为它让系统具备这些能力：

- UI 能实时展示 reasoning/text/tool-call
- tool 结果可以独立持久化
- 历史可以重新转换成模型输入
- compaction 可以只压缩部分内容
- patch/snapshot 可以和回答过程绑定
- 错误、重试、权限拒绝可以落在具体 part 上

这是整个 loop 可持续运行的基础。

---

## 3.4 Prompt 与 loop 层

### `src/session/prompt.ts`

这是本项目最核心的文件之一。

它负责：

- 接受用户输入并创建 user message
- 启动/恢复 loop
- 从消息历史构造 prompt
- 解析工具
- 插入技能、环境、规则 prompt
- 调用 `SessionProcessor`
- 处理 compaction / subtask / structured output

可以说它是**agent orchestration 总控中心**。

### `src/session/processor.ts`

这个模块负责把 `LLM.stream()` 的流式事件翻译成 session 内部状态。

职责包括：

- 处理 text delta
- 处理 reasoning delta
- 处理 tool-call / tool-result / tool-error
- 处理 step-start / step-finish
- 记录 tokens / cost / patch
- 做 retry 与 overflow 检测

所以：

- `prompt.ts` 决定“下一轮怎么跑”
- `processor.ts` 决定“模型这一轮输出如何落库与推进状态”

---

## 3.5 Provider / 模型接入层

### `src/provider/provider.ts`

这是 provider 抽象层核心。

职责：

- 维护 provider 和 model 元数据
- 从 config/env/auth 构建 provider
- 接入不同 AI SDK provider
- 生成 language model 实例
- 决定某些 provider 的特殊调用方式

内置 provider 非常多，包括：

- OpenAI
- Anthropic
- Google / Vertex
- OpenRouter
- GitHub Copilot
- Azure
- Bedrock
- Mistral
- Groq
- DeepInfra
- Cerebras
- Cohere
- TogetherAI
- Perplexity
- Vercel
- GitLab
- Cloudflare
- xAI
- 以及 OpenAI-compatible 类 provider

### `src/provider/transform.ts`

这是 provider 适配层核心，负责“把统一的 OpenCode 消息/参数”转换为“不同 provider 能接受的输入”。

它解决的问题包括：

- providerOptions key 的映射
- message 结构归一化
- tool-call id 格式修正
- 不同 provider 的推理参数差异
- caching 参数差异
- JSON schema 差异
- 不支持的媒体/模态转换

这层非常关键，因为 OpenCode 的上层 loop 是统一的，而下层模型 API 差异很大。

---

## 3.6 Tool 层

### `src/tool/registry.ts`

工具注册中心负责：

- 收集内置工具
- 加载用户自定义 tools
- 加载 plugin tools
- 根据当前 model/provider 过滤可用工具
- 构造工具 schema 和执行器

默认工具包括：

- `bash`
- `read`
- `glob`
- `grep`
- `edit`
- `write`
- `task`
- `webfetch`
- `todoread/todowrite`
- `websearch`
- `codesearch`
- `skill`
- `apply_patch`
- 可选的 `lsp`
- 可选 `batch`

### 特殊点

- 对某些 `gpt-*` 模型，优先暴露 `apply_patch`
- 对其他模型，暴露 `edit` / `write`
- `codesearch` / `websearch` 对 provider 或 flag 有条件开放

这说明 tool 层不是“全部工具固定开放”，而是**按模型能力和配置动态裁剪**。

---

## 3.7 Skill 层

### `src/skill/skill.ts`

Skill 系统是 OpenCode 很重要的一层，它的定位不是 tool，而是**任务专用说明书与工作流包**。

Skill 的定义字段包括：

- `name`
- `description`
- `location`
- `content`

Skill 来源可以有多种：

- 项目内 `.opencode/skill` 或 `.opencode/skills`
- 外部目录 `.claude/skills/`、`.agents/skills/`
- config 指定本地目录
- config 指定 URL 下载的 skill 集

Skill 会扫描 `**/SKILL.md`。

### `src/tool/skill.ts`

`skill` 不是被动加载，而是作为一个**显式 tool**提供给模型。

模型先看到可用 skill 列表，然后在任务匹配时调用 `skill` tool，加载 skill 内容到上下文。

Skill tool 执行后返回：

- `<skill_content name="..."> ... </skill_content>`
- skill base directory
- skill 文件采样列表

也就是说，Skill 的设计不是静态 system prompt，而是**按需懒加载**。

这对上下文窗口非常重要。

---

## 3.8 Permission / Rule 层

### `src/permission/next.ts`

这是权限系统核心。

它定义了：

- `Rule = { permission, pattern, action }`
- `action = allow | deny | ask`
- `ruleset`
- `ask()`
- `reply()`
- `evaluate()`
- `disabled()`

权限既可以作用在：

- tool 级别，如 `bash` / `read` / `edit`
- 也可以作用在某些 pattern，如路径、skill 名称等

### 关键机制

当工具执行前，系统会调用 `PermissionNext.ask()`：

- 如果规则匹配 `deny`，直接抛错
- 如果匹配 `ask`，生成 pending permission request，等待 UI/客户端响应
- 如果匹配 `allow`，直接放行

### 规则来源

规则会合并：

- agent 默认 permission
- 用户 config permission
- session 级 permission
- 用户“always allow”产生的批准规则

因此 rules 不是装饰，而是贯穿整个 tool/skill/subtask 执行链路的**强制控制面**。

---

## 3.9 Instruction / Rule Prompt 层

### `src/session/instruction.ts`

它负责搜集各种 instruction 文件：

- `AGENTS.md`
- `CLAUDE.md`
- `CONTEXT.md`（deprecated）
- global config 的 `AGENTS.md`
- 用户 config 里指定的 instruction files / URLs

### 两类用途

#### 1. `system()`

把系统级 instruction 文件内容直接拼进 system prompt。

#### 2. `resolve()`

当模型读取某个路径附近文件时，可以向上查找邻近目录中的 `AGENTS.md/CLAUDE.md`，并动态补充局部 instruction。

这非常关键：

- 全局 instruction 进入 system prompt
- 局部 instruction 则随着代码阅读路径动态注入

也就是说 OpenCode 的“rule / instruction”不是单层，而是：

- **全局系统规则**
- **项目根规则**
- **路径局部规则**

---

# 4. LLM 调用链详细解读

下面看模型调用的真实路径。

## 4.1 从 loop 到模型

主链路是：

- `SessionPrompt.prompt()`
- `SessionPrompt.loop()`
- `SessionProcessor.process()`
- `LLM.stream()`
- `Provider.getLanguage()`
- `streamText()`

其中：

- `SessionPrompt.loop()` 负责本轮组装输入
- `SessionProcessor.process()` 负责消费输出
- `LLM.stream()` 负责真正发请求给模型

---

## 4.2 `LLM.stream()` 做了什么

`src/session/llm.ts`

核心流程：

1. 根据 `input.model` 找到 language model
2. 获取当前 provider、config、auth
3. 判断是否是特殊模式，比如 OpenAI OAuth/Codex
4. 构建最终 system prompt
5. 计算 provider-specific options
6. 过滤工具
7. 注入 headers
8. 用 `streamText()` 发起流式调用
9. 用 middleware 在最后一步改写消息格式

### system prompt 组装顺序

`LLM.stream()` 中最终 system 内容是：

1. **agent.prompt 或 provider prompt**
2. **input.system**
3. **last user message 上附带的 system**

代码上就是：

- 若 agent 自带 prompt，则优先用 agent prompt
- 否则根据 model/provider 选 provider-specific system prompt
- 再加 loop 组装出的系统内容
- 再加用户此次调用临时 system

这意味着：

- **agent prompt 负责 agent 行为模式**
- **provider prompt 负责模型特定指令适配**
- **system[] 负责环境、skills、instructions 等动态上下文**
- **user.system** 负责单次调用级附加约束

---

# 5. Prompt 设计详细解读

这是本项目最值得重点看的部分。

## 5.1 prompt 不是单模板，而是多层组合

OpenCode 的 prompt 由几层组成：

### 第一层：provider/system prompt

来自 `src/session/system.ts`。

`SystemPrompt.provider(model)` 根据模型类型返回不同 prompt：

- `PROMPT_CODEX`
- `PROMPT_BEAST`
- `PROMPT_GEMINI`
- `PROMPT_ANTHROPIC`
- `PROMPT_TRINITY`
- `PROMPT_ANTHROPIC_WITHOUT_TODO`

也就是说，OpenCode 不认为所有模型遵循同一套最佳提示词，而是按 provider/model family 做分流。

这是很成熟的做法，因为：

- Claude 对 tool-use / todo / planning 的最佳提示习惯不同
- Gemini 对指令风格和多模态理解不同
- GPT-5/Codex 对 instructions 入口也不同

### 第二层：环境 prompt

`SystemPrompt.environment(model)` 会注入：

- 当前模型信息
- working directory
- workspace root
- 是否 git repo
- platform
- 当前日期
- 可选目录树

这层的作用是给模型一个稳定的环境坐标系。

### 第三层：skills prompt

`SystemPrompt.skills(agent)` 会把可用 skills 列表注入 system prompt，告诉模型：

- skills 是什么
- 什么时候该用 skill tool
- 当前有哪些 skill 可用

注意：这里只给**索引**，不是全部 skill 内容。

### 第四层：instruction prompt

`InstructionPrompt.system()` 把系统/项目 instruction files 内容拼进来。

### 第五层：structured output prompt

如果用户要求 `json_schema` 输出，会额外加入：

- `STRUCTURED_OUTPUT_SYSTEM_PROMPT`

要求模型必须调用 `StructuredOutput` tool，而不能直接输出纯文本。

### 第六层：agent prompt

如果 agent 自己配置了 prompt，如 `explore` / `compaction` / `title` / `summary`，则它优先覆盖 provider prompt。

---

## 5.2 system prompt 的最终来源

在 `SessionPrompt.loop()` 中传给 `processor.process()` 的 `system` 是：

- `SystemPrompt.environment(model)`
- `SystemPrompt.skills(agent)`
- `InstructionPrompt.system()`
- 条件性追加 structured-output 提示

在 `LLM.stream()` 中，又会在前面拼：

- `agent.prompt` 或 `SystemPrompt.provider(model)`
- `input.system`
- `input.user.system`

所以最终 system prompt 实际是：

1. **agent / provider 基础行为 prompt**
2. **环境描述**
3. **skills 索引**
4. **全局/项目 instruction 文件**
5. **结构化输出约束（如果启用）**
6. **本次调用附加 system 指令**

这是一个很完整的分层结构。

---

## 5.3 为什么这种 prompt 设计有效

因为它解决了传统 agent prompt 常见的几个问题：

### 问题一：单个巨型 system prompt 难维护

OpenCode 把 prompt 拆成：

- provider 层
- agent 层
- environment 层
- instructions 层
- skill 层
- output-contract 层

便于替换和调试。

### 问题二：skill 内容过长会污染上下文

它只把 skill 列表放进 system prompt，真正内容按需 tool load。

### 问题三：不同模型差异巨大

provider-specific prompt + provider transform 双层适配。

### 问题四：项目规则与路径规则无法表达

`InstructionPrompt` 将 instruction 文件系统化。

### 问题五：结构化输出不可靠

它通过 tool contract 强行把输出变成 tool call，不依赖模型“自觉输出 JSON”。

---

# 6. Loop 机制详细解读

这是整个系统最关键的地方。

## 6.1 loop 的入口

`SessionPrompt.prompt()`：

- 先创建 user message
- 若 `noReply === true` 则只存消息不回复
- 否则进入 `loop({ sessionID })`

## 6.2 loop 的核心控制逻辑

`SessionPrompt.loop()` 是一个 `while (true)` 循环。

每次迭代会做：

1. 取当前 session 的消息流
2. 找到：
   - 最后一个 user
   - 最后一个 assistant
   - 最后一个已完成 assistant
   - 尚未处理的 compaction / subtask
3. 判断是否已经结束
4. 选择模型与 agent
5. 先处理 pending subtask / compaction
6. 否则进入正常 LLM 推理轮
7. 根据结果决定：
   - `stop`
   - `compact`
   - `continue`

这个设计的关键思想是：

- **loop 的输入不是“当前 prompt 字符串”**
- 而是**session 中截至当前时刻的全部消息状态**

换句话说，loop 是一个**状态机驱动器**，而不是“函数递归地拼 prompt”。

---

## 6.3 step 的含义

`loop()` 中维护 `step++`。

作用：

- 第 1 步时异步触发 `ensureTitle`
- 根据 agent 的 `steps` 限制判断是否到达 `isLastStep`
- 若达到最大步数，在 messages 末尾插入 `MAX_STEPS` 提醒 assistant 结束

这说明 OpenCode 的 loop 是**有限步迭代**而不是完全无限自治。

---

## 6.4 正常轮次的执行过程

普通推理轮次如下：

1. 获取当前 `agent`
2. 通过 `insertReminders()` 对历史消息做提醒增强
3. 创建一个新的 assistant message 作为本轮承载
4. 创建 `SessionProcessor`
5. 解析 tools
6. 如果要求结构化输出，注入 `StructuredOutput` tool
7. 构造 system prompt
8. 通过 `processor.process()` 运行一轮模型
9. 根据返回值决定下一步

这里最重要的是：

- 每轮 assistant 都有独立 message 记录
- 工具执行结果也记录在该 assistant message 的 parts 中
- 下一轮时再把历史重新编码成 model messages

所以 loop 不是“在单条 assistant message 里无限追加”，而是**离散轮次的 session state machine**。

---

## 6.5 subtask 的处理

如果历史中存在 `subtask` part，loop 不会直接进入普通推理，而是先执行 subtask。

处理流程：

1. 创建 assistant message
2. 创建一个 `task` tool part
3. 构造 `Tool.Context`
4. 调用 `TaskTool.execute()`
5. 将结果写回 tool part
6. 如果是 command 型任务，还会插入 synthetic user message，提示“总结 task tool 输出并继续”
7. 然后继续 loop

这代表 OpenCode 支持**subagent/subtask orchestration**，不是只有“主 agent + tools”。

---

## 6.6 compaction 的处理

如果出现 `compaction` part，或者检测到上下文溢出：

- loop 会先进入 `SessionCompaction.process()`
- compaction 完成后再继续 normal loop

换句话说：

- compaction 不是异常补丁
- 而是 loop 的正式分支

这是成熟 agent runtime 的重要特征。

---

## 6.7 loop 与 processor 的分工

### loop 负责

- 调度轮次
- 构造输入
- 选择工具
- 管理 compaction/subtask
- 控制结束条件

### processor 负责

- 消费模型流
- 写 text/reasoning/tool/patch parts
- 统计 usage/cost
- 判断 retry / compact / stop

这个分层很干净。

---

# 7. SessionProcessor 如何消费模型输出

`src/session/processor.ts`

它处理 `stream.fullStream` 的每种事件。

## 7.1 支持的主要事件

- `start`
- `reasoning-start`
- `reasoning-delta`
- `reasoning-end`
- `tool-input-start`
- `tool-call`
- `tool-result`
- `tool-error`
- `text-start`
- `text-delta`
- `text-end`
- `start-step`
- `finish-step`
- `finish`
- `error`

## 7.2 text / reasoning 的落地方式

- text 与 reasoning 都会被独立保存为 part
- delta 流实时写回数据库 / 总线
- end 时补齐 metadata 与 time

这样 UI 可以实时渲染增量内容。

## 7.3 tool call 的落地方式

tool 的生命周期是：

- `pending`
- `running`
- `completed`
- `error`

并对应一个 `ToolPart.state`。

tool-call 时先把 tool part 设为 `running`，tool-result 时写入：

- `input`
- `output`
- `metadata`
- `attachments`
- `title`
- `time`

这使 tool 成为一等公民，而不是 assistant 文本里的嵌入片段。

## 7.4 doom loop 检测

如果最近连续 3 次 tool 调用：

- tool 名相同
- 输入相同

则会触发 `doom_loop` permission ask。

说明项目已经显式处理 agent 无限重复调用工具的问题。

## 7.5 finish-step 时的工作

在 `finish-step` 时，processor 会：

- 计算 token usage 和 cost
- 更新 assistant message.finish
- 记录 patch/snapshot
- 异步触发 summary
- 检测是否需要 compaction

这是每一轮的重要收口点。

## 7.6 error / retry / overflow

如果出现错误：

- 先转成 `MessageV2.fromError()` 的统一错误结构
- 如果是 context overflow，则标记需要 compaction
- 如果是 retryable error，则进行 backoff retry
- 否则写入 assistant message.error 并停止

所以 OpenCode 的错误处理不是简单 catch，而是将错误纳入消息状态机。

---

# 8. 上下文管理机制详细解读

这是项目的另一条主线。

## 8.1 上下文的真实来源

OpenCode 发送给模型的上下文并不是一段“历史拼接字符串”，而是由这些组成：

- system prompt 多层组合
- `MessageV2.toModelMessages(msgs, model)` 转出来的历史消息
- 工具调用与结果
- reasoning parts
- file parts
- synthetic reminders
- compaction summary
- skill 内容（按需加载后变成上下文的一部分）

## 8.2 `MessageV2.toModelMessages()` 的作用

这个函数很关键。

它把内部消息系统转换成 AI SDK 需要的 `ModelMessage[]`。

### 用户消息转换

- text part -> user text
- file part -> user file
- compaction part -> “What did we do so far?”
- subtask part -> 工具执行提示文本

### assistant 消息转换

- text part -> assistant text
- reasoning part -> assistant reasoning
- tool completed -> tool output part
- tool error -> tool error part
- pending/running tool -> 构造成 interrupted error，防止 dangling tool-use

### 特殊媒体处理

对于不支持“tool result 携带媒体”的 provider：

- 会把工具结果中的 image/pdf 提取出来
- 注入为一个后续 user message

这是很细的 provider 兼容设计。

---

## 8.3 历史裁剪：`filterCompacted()`

`MessageV2.filterCompacted()` 会在读取历史时：

- 检测已经完成的 summary assistant
- 当遇到对应 compaction user message 时停止回溯

本质上，它确保：

- 旧上下文被 summary 替代后，不再继续把完整旧历史塞回模型

这就是 compaction 生效的关键。

---

## 8.4 compaction 的地位

虽然本次没有展开 `session/compaction.ts` 全文，但从 loop 和 processor 的集成方式已经能看出：

- compaction 是 session 生命周期内的正式对象
- 有独立 `compaction` part
- 可以自动触发
- 可以由 overflow 触发
- 可以 prune
- compaction 完成后，历史会被逻辑性截断

因此 OpenCode 的上下文管理不是“到上限就截断前文”，而是**摘要式压缩 + 历史断点替换**。

---

## 8.5 reminder 注入

当 loop 进入后续轮次时：

- 对上一个已完成 assistant 之后的新 user 消息
- 会用 `<system-reminder>` 包裹
- 告诉模型“请优先处理这个新消息并继续任务”

这解决了多轮自治 agent 很常见的问题：

- 模型容易忽视用户中途插话

OpenCode 用系统提醒把这类消息临时提升优先级。

---

## 8.6 instruction 的上下文管理

`InstructionPrompt` 实现了两种上下文注入：

- **system 级 instruction**：全局进入 system prompt
- **路径相关 instruction**：根据读到的文件路径向上查找局部 `AGENTS.md/CLAUDE.md`

这是非常强的“上下文局部规则”机制。

---

## 8.7 skills 的上下文管理

skills 采用两段式上下文管理：

### 第一段：索引进 system prompt

只告诉模型有哪些 skill 可用。

### 第二段：内容按需加载

模型识别任务匹配 skill 后，再调 `skill` tool 把 skill 内容引入上下文。

这意味着：

- skills 不会一开始就污染窗口
- skill 内容只在真正需要时加载
- skill 还能附带目录和文件资源位置

这是比“把所有 SOP 塞进 system prompt”更高级的设计。

---

# 9. Skills 机制详细解读

## 9.1 skill 的本质

在 OpenCode 中，skill 不是模型本体能力，而是：

- 某类任务的专用说明书
- 可能包含 workflow
- 可能包含参考文件/脚本/模板
- 通过 tool 调用按需注入

它有点像“可加载的领域知识包 + 操作规程包”。

## 9.2 skill 的发现机制

`Skill.state()` 会扫描多个来源：

### 项目/用户兼容目录

- `~/.claude/skills/**/SKILL.md`
- `~/.agents/skills/**/SKILL.md`
- 从当前目录向上扫描 `.claude` / `.agents`

### OpenCode 自己的目录

- `.opencode/skill/**/SKILL.md`
- `.opencode/skills/**/SKILL.md`

### 配置扩展来源

- `config.skills.paths`
- `config.skills.urls`

因此它兼容其他 agent 生态的 skill 布局，是一个明显的生态互操作设计。

## 9.3 skill 的内容结构

每个 `SKILL.md` 需要 frontmatter 至少可解析出：

- `name`
- `description`

正文内容就是 skill 的 instruction body。

## 9.4 skill 的使用流程

1. loop 构建 system prompt 时，把 skill 列表告诉模型
2. 模型判断任务匹配某个 skill
3. 调用 `skill` tool，传 skill name
4. 系统做 permission 检查
5. 返回完整 skill 内容
6. skill 内容进入后续上下文，影响模型行为

所以 skill 是“由模型主动选择、由系统受控注入”的。

## 9.5 为什么 skill 做成 tool 而不是固定 prompt

优势很明显：

- 降低基础 prompt 长度
- 避免所有用户都承担所有 skill token 成本
- 让模型只在任务匹配时启用专业 SOP
- 权限系统可以拦截某些 skill
- skill 内容可以附带文件资源与目录语义

这是一种很好的 agent memory / workflow packaging 方式。

---

# 10. Tool Call 机制详细解读

## 10.1 工具从哪里来

工具来源有三类：

### 1. 内置工具

由 `ToolRegistry.all()` 返回。

### 2. 用户自定义工具

扫描：

- `{tool,tools}/*.{js,ts}`

### 3. 插件工具

从 `Plugin.list()` 收集 `plugin.tool`

---

## 10.2 tool 的暴露过程

在 `SessionPrompt.resolveTools()` 中：

1. 从 `ToolRegistry.tools()` 获取所有工具定义
2. 将每个工具参数 schema 经过 `ProviderTransform.schema()` 适配
3. 转成 AI SDK 的 `tool({...})`
4. 包装执行逻辑，注入：
   - `Tool.Context`
   - `PermissionNext.ask`
   - `Plugin.trigger(before/after)`
   - part metadata 更新

此外还会合并 `MCP.tools()`。

所以模型看到的是 AI SDK tool schema，但内部执行上下文非常丰富。

---

## 10.3 Tool.Context 包含什么

工具执行时上下文包括：

- `sessionID`
- `abort`
- `messageID`
- `callID`
- `agent`
- `messages`
- `extra.model`
- `extra.bypassAgentCheck`
- `metadata()`：可回写运行时 title/metadata
- `ask()`：触发权限申请

这意味着工具不是裸函数，而是运行在完整 agent runtime 上下文中。

---

## 10.4 工具执行的生命周期

模型调用 tool 后：

1. processor 收到 `tool-input-start`
2. 创建 pending tool part
3. 收到 `tool-call`
4. part 状态变为 running
5. 真正执行 tool
6. 成功则 `tool-result`
7. 失败则 `tool-error`
8. processor 更新消息 part
9. 下一轮 loop 继续

这是一个标准的 tool-augmented LLM action loop。

---

## 10.5 MCP tool 集成

`SessionPrompt.resolveTools()` 里除了本地工具，还会合并 `MCP.tools()`。

这表示 OpenCode 工具系统支持两大类：

- 本地 runtime 工具
- MCP 提供的远程/外部工具

并且对 MCP 工具同样做：

- permission ask
- plugin before/after hook
- output truncation
- attachments 映射

因此 MCP 是被并入统一 tool call 框架的，不是另一个平行系统。

---

## 10.6 Structured Output 也是 tool

这是个很巧妙的设计。

当用户要求 JSON schema 输出时：

- loop 动态插入 `StructuredOutput` tool
- system prompt 强制要求必须调用它
- `toolChoice = required`
- execute 时校验 schema 并保存结构化结果

优点：

- 不依赖模型纯文本 JSON 稳定性
- 输出契约进入 tool call 机制
- 和普通回答共享同一个运行通道

---

# 11. Rule / Permission 机制详细解读

## 11.1 为什么说 PermissionNext 是控制平面

OpenCode 的权限不是 UI 层补充功能，而是整个 runtime 的控制平面。

几乎所有高风险动作都通过 `ask()` 路径：

- bash
- edit/write/patch
- skill
- doom_loop
- MCP tools
- subtask tool 等

## 11.2 permission 的匹配逻辑

`evaluate(permission, pattern, ...rulesets)` 会：

- 合并多个 ruleset
- 取**最后一个匹配规则**
- 默认没有规则时为 `ask`

这表示配置是**后写覆盖前写**。

## 11.3 agent 自带权限模型

不同 agent 的默认权限差异很大：

### build

- 默认开发模式
- 可 question
- 可 plan_enter

### plan

- 编辑默认 deny
- 只允许某些计划文件路径
- 更偏向只读分析

### explore

- 几乎只开放搜索/读取相关能力
- 其他默认 deny

这就让 agent 不仅是 prompt/persona，更是**权限模板**。

---

# 12. Provider / SDK 接入详细解读

## 12.1 模型元数据如何组织

`Provider.Model` 中定义了非常完整的 model 能力数据：

- `api.id`
- `api.url`
- `api.npm`
- `capabilities.temperature`
- `capabilities.reasoning`
- `capabilities.attachment`
- `capabilities.toolcall`
- 各模态输入输出支持
- `limit.context/input/output`
- `cost`
- `variants`

这让上层 runtime 可以按能力而不是 provider 名字做决策。

## 12.2 为什么需要 `ProviderTransform`

因为不同 provider 对同一概念的表达不同，例如：

- reasoning effort 名字不同
- cache control 参数不同
- tool call id 格式限制不同
- message 顺序限制不同
- JSON schema 兼容性不同
- media/tool-result 表达方式不同

所以 OpenCode 不是在业务层写很多 `if provider === x`，而是集中到 transform 层。

## 12.3 `variants` 机制

`ProviderTransform.variants(model)` 会根据模型/provider 生成 reasoning variants，例如：

- `low`
- `medium`
- `high`
- `max`
- `minimal`
- `none`
- `xhigh`

不同 provider 映射到不同底层参数：

- OpenAI: `reasoningEffort`
- Anthropic: `thinking.budgetTokens`
- Gemini: `thinkingConfig`
- Bedrock: `reasoningConfig`
- OpenRouter/Gateway: 各自命名空间

这让上层只关心“变体名”，下层做 provider-specific 转换。

## 12.4 OpenCode 如何真正调模型

`LLM.stream()` 最终调用的是 AI SDK 的：

- `streamText()`
- `wrapLanguageModel()`

OpenCode 自身并不手写各 provider HTTP 请求，它主要建立在 AI SDK provider abstraction 之上，再叠加：

- provider registry
- options transform
- message transform
- headers/hooks/permissions/tool runtime

这也是为什么它能支持很多 provider。

---

# 13. JS SDK 结构与能力

## 13.1 目录

`packages/sdk/js` 下核心文件有：

- `src/client.ts`
- `src/server.ts`
- `src/index.ts`
- `src/v2/client.ts`
- `src/v2/server.ts`
- `src/v2/index.ts`
- `src/gen/client.gen.ts`
- `src/gen/sdk.gen.ts`
- `src/gen/types.gen.ts`
- `example/example.ts`

此外 `packages/sdk/openapi.json` 是生成基础。

## 13.2 SDK 的设计方式

从目录看，SDK 是：

- 基于 OpenAPI 生成基础 typed client/types
- 再在 `v2/` 层做更贴近产品语义的包装

也就是说，SDK 不是随手写几个 fetch 函数，而是**以 OpenAPI 为契约源生成**。

## 13.3 SDK 能力范围

从 ACP 和 session 代码能看到 SDK 至少提供这些命名空间：

- `sdk.session`
  - `create`
  - `get`
  - `list`
  - `messages`
- `sdk.config`
  - `providers`
- `sdk.command`
  - `list`
- `sdk.mcp`
  - `add`

从 ACP 代码还能看出它支持：

- 获取 providers/models
- 管理 session
- 读取消息历史
- 设置 MCP servers
- 拉取可用 commands

换句话说，SDK 暴露的是 **OpenCode runtime API**，而不是简单的 “ask model once”。

---

# 14. ACP 集成说明

`src/acp/agent.ts` 和 `src/acp/session.ts` 展示了 OpenCode 如何作为 ACP agent 工作。

其职责包括：

- 创建 / 加载 session
- 维护 ACP session state
- 选择 model / mode
- 重放 session history
- 把 OpenCode 消息映射成 ACP 侧更新事件
- 发送 usage update
- 注入 MCP server 配置

这进一步说明 OpenCode 的内部 runtime 已经被抽象得足够稳定，可以挂在一个协议层上对外提供 agent 能力。

---

# 15. 整体运行时序图（文字版）

下面给一个简化版执行时序：

## 15.1 普通一次提问

1. 用户输入文本/文件
2. `SessionPrompt.prompt()` 创建 user message
3. `loop()` 拉历史消息
4. 选 agent/model
5. 构造 system prompt + tools + model messages
6. `SessionProcessor.process()` 调用 `LLM.stream()`
7. 模型输出 text/reasoning
8. processor 将 text/reasoning 持久化
9. `finish-step` 更新 tokens/cost/patch
10. assistant 完成

## 15.2 带工具调用

1. 前 1-6 同上
2. 模型输出 tool-call
3. processor 创建 tool part = running
4. tool execute
5. tool result 回写 part
6. assistant finish reason 通常为 `tool-calls`
7. loop 再次迭代
8. 历史中已包含 tool result
9. 模型基于工具结果继续推理
10. 最终停止

## 15.3 上下文过长

1. processor 或 loop 检测 overflow
2. 返回 `compact`
3. loop 创建 compaction task
4. compaction 生成 summary
5. `filterCompacted()` 截断旧历史
6. loop 继续，用 summary 替换原长历史

---

# 16. 这个项目设计上的几个亮点

## 16.1 以消息状态机为中心，而不是 prompt 字符串为中心

这是整个项目最成熟的部分。

很多 agent 项目核心状态只有：

- conversation array
- tool history text

而 OpenCode 的核心状态是：

- `Session`
- `Message`
- `Part`

这让它天然适合：

- 实时 UI
- 重放
- 中断恢复
- compaction
- 结构化输出
- 权限审计

## 16.2 Prompt 分层做得很好

不是一个万能 system prompt，而是：

- provider prompt
- agent prompt
- environment
- instructions
- skills index
- output contract
- per-call system

这比传统 giant prompt 可维护性强很多。

## 16.3 Skill 采用按需加载，而不是全量注入

这很节省上下文，也更像真正的“工作流库”。

## 16.4 Provider 抽象足够深

不是只支持几个 provider，而是系统化兼容多 provider 差异，包括：

- reasoning
- caching
- media
- tools
- schema
- ids
- headers

## 16.5 权限模型是运行时一等公民

agent 的“能力边界”不是 prompt 约束，而是 permission ruleset。

这是根本性的工程化差异。

---

# 17. 重点结论

最后把最关键的问题收束一下。

## 17.1 loop 是怎样的

OpenCode 的 loop 是一个**基于 session/message/part 状态的迭代状态机**：

- 每轮读取最新状态
- 优先处理 pending subtask / compaction
- 正常轮次则构造上下文并调用模型
- tool call、retry、overflow、structured output 都是 loop 分支的一部分
- 直到满足 stop 条件为止

它不是递归 prompt，而是**runtime orchestrator**。

## 17.2 prompt 怎么设计

prompt 是分层动态组合的：

- provider-specific prompt
- agent prompt
- environment prompt
- system/project instruction files
- skills index
- 结构化输出契约
- 单次调用 system

其中 **skills 内容不是直接进入 system prompt，而是先只暴露索引，再按需 tool load**。

## 17.3 上下文怎么管理

上下文管理依赖：

- `MessageV2.toModelMessages()` 的结构化转换
- `filterCompacted()` 的历史裁剪
- `SessionCompaction` 的摘要替换
- 中途 user message 的 reminder 包裹
- instruction 文件的全局/局部注入
- skill 的按需加载

所以它不是“截断历史”，而是**分层压缩 + 动态注入 + 结构化回放**。

## 17.4 skills 怎么做

skills 是：

- 从多个目录/URL发现的 `SKILL.md`
- 先以索引形式进入 system prompt
- 再通过 `skill` tool 按需加载完整内容
- 受权限系统控制
- 可附带资源目录语义

本质是**任务专用的可加载 workflow/instruction 包**。

## 17.5 tool call 怎么做

tool call 路径是：

- `ToolRegistry` / `MCP.tools()` 提供工具定义
- `SessionPrompt.resolveTools()` 转为 AI SDK tool
- 工具执行时注入上下文、权限、plugin hooks
- `SessionProcessor` 追踪 tool 生命周期
- 结果写为 tool part 并回流到下一轮上下文

它是一个非常完整的 tool runtime，而不是简单函数调用。

## 17.6 SDK 有什么

SDK 主要提供对 OpenCode runtime API 的 typed 访问，包括：

- session 创建/获取/列出/读消息
- config providers
- command list
- mcp add
- 以及 v1/v2 客户端封装

它背后基于 `openapi.json` 生成，是 OpenCode 作为平台对外暴露能力的重要接口层。

---

# 18. 推荐阅读顺序

如果你想继续深入源码，建议按下面顺序读：

1. `packages/opencode/src/index.ts`
2. `packages/opencode/src/agent/agent.ts`
3. `packages/opencode/src/session/prompt.ts`
4. `packages/opencode/src/session/processor.ts`
5. `packages/opencode/src/session/message-v2.ts`
6. `packages/opencode/src/session/llm.ts`
7. `packages/opencode/src/session/system.ts`
8. `packages/opencode/src/session/instruction.ts`
9. `packages/opencode/src/tool/registry.ts`
10. `packages/opencode/src/tool/skill.ts`
11. `packages/opencode/src/skill/skill.ts`
12. `packages/opencode/src/permission/next.ts`
13. `packages/opencode/src/provider/provider.ts`
14. `packages/opencode/src/provider/transform.ts`
15. `packages/sdk/js/src/v2/*`

---

# 19. 一句话总结

**OpenCode 的本质不是“带 prompt 的 CLI”，而是一套以 session/message/part 为核心状态模型、以 loop 为调度中心、以 provider abstraction 为模型适配层、以 skill/tool/permission 为能力扩展面的通用 AI coding agent runtime。**
