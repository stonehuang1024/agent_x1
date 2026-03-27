# Codex 项目详细解读（README_codex_01）

本文对 `/Users/simonwang/project/agent/codex` 做系统级源码解读，重点覆盖：

- 整体架构与设计目标
- 代码目录结构与各模块职责
- 核心调用链：CLI/App -> Core Session -> Turn Loop -> Prompt -> LLM -> Tool Call -> Context/Diff 持久化
- Prompt 设计，尤其是 `system/base instructions`、上下文注入、skills/rules 注入
- Context 管理与 context window 控制
- Skills / Rules / AGENTS.md 机制
- Tool call 的建模、构造、路由、执行与错误处理
- 代码索引与快速查找核心代码进入上下文的方式
- diff 展示、patch 应用、merge/变更聚合
- SDK 能力与接口分层

说明：本文基于仓库当前源码，而不是产品文档推断。所引用的关键文件以 `codex-rs` 为主，因为真正的智能体内核大多在 Rust 工作区中。

---

# 1. 项目整体定位

`codex` 是一个本地运行的 coding agent 系统，不只是一个 CLI 包装器，而是一个完整的“本地代理执行平台”。

从仓库顶层结构看，它由几层组成：

- 顶层包装与发布层
  - `codex-cli/`
  - `sdk/`
  - `docs/`
  - `shell-tool-mcp/`
- Rust 核心工作区
  - `codex-rs/`
- 外围构建/分发设施
  - `package.json`
  - `pnpm-workspace.yaml`
  - `BUILD.bazel`
  - `flake.nix`

最关键的事实是：

- **真正的 agent loop、prompt 组织、上下文管理、工具执行、diff 跟踪，都在 `codex-rs/core` 里。**
- CLI/TUI/App Server/SDK 主要是外层交互面。
- 这个项目的设计是“多前端 + 单核心引擎”。

---

# 2. 顶层目录结构解读

## 2.1 顶层主要目录

- `codex-rs/`
  - 核心 Rust workspace，几乎所有关键能力都在这。
- `codex-cli/`
  - 面向 npm 发布的 CLI 包装层，通常负责打包和分发 Rust 可执行产物、JS 包装逻辑。
- `sdk/`
  - Python / TypeScript SDK。
- `shell-tool-mcp/`
  - 与 MCP 工具生态相关的外围工具。
- `docs/`
  - 用户/贡献者文档。
- `.github/`
  - CI/CD、issue template、workflow。

## 2.2 `codex-rs` 工作区结构

`codex-rs/Cargo.toml` 暴露出这是一个非常大的 workspace，核心 crates 包括：

- 入口与交互
  - `cli`
  - `tui`
  - `app-server`
  - `app-server-client`
  - `app-server-protocol`
- 核心智能体
  - `core`
  - `protocol`
  - `state`
  - `config`
- 模型与平台集成
  - `backend-client`
  - `codex-api`
  - `codex-client`
  - `chatgpt`
  - `lmstudio`
  - `ollama`
  - `responses-api-proxy`
- 工具与执行
  - `exec`
  - `shell-command`
  - `shell-escalation`
  - `apply-patch`
  - `mcp-server`
  - `rmcp-client`
- 能力子系统
  - `skills`
  - `connectors`
  - `file-search`
  - `hooks`
  - `artifacts`
- 系统能力
  - `linux-sandbox`
  - `network-proxy`
  - `execpolicy`
  - `process-hardening`
  - `keyring-store`
  - `secrets`
- 观测性
  - `otel`
  - `feedback`

这说明它不是“单一 LLM 调用器”，而是一个完整的代理系统平台。

---

# 3. 整体架构：从前端到智能体核心

可以把架构理解成五层。

## 3.1 交互层

负责接收用户输入、展示结果、管理 session：

- `codex-rs/cli`
- `codex-rs/tui`
- `codex-rs/app-server`
- IDE 集成通常走 app-server / protocol

在 `codex-rs/cli/src/main.rs` 中，`main()` -> `cli_main()` 根据子命令分流：

- 无子命令：进入交互式 TUI
- `exec`：执行单次任务
- `review`
- `mcp-server`
- `app-server`
- `resume` / `fork`
- `login` / `logout`
- `cloud`

这层本质上只是入口路由，不承载主要智能逻辑。

## 3.2 Session / Turn 编排层

核心在 `codex-rs/core/src/codex.rs`。

这一层负责：

- 初始化 `Session`
- 解析 session 配置
- 构建 `TurnContext`
- 管理单轮 turn 的状态
- 组织 prompt、tools、context、skills
- 驱动模型采样循环
- 接收并分发 tool call
- 更新历史与 diff

## 3.3 Prompt / Context 层

负责把“系统规则 + 用户输入 + 历史 + 工具规格 + 技能说明 + 运行时配置”组织为模型可见输入。

主要组件：

- `build_prompt()`
- `ContextManager`
- settings update item 构建
- `UserInstructions` / `SkillInstructions`

## 3.4 Tool Runtime 层

负责：

- 工具规格构建
- 工具调用对象化
- 工具路由与 handler dispatch
- approval / sandbox / permission 管理
- diff 跟踪与工具执行事件回传

主要组件：

- `ToolRouter`
- `ToolRegistry`
- `ToolCallRuntime`
- 各种 `tools/handlers/*`
- `tools/runtimes/*`

## 3.5 基础设施层

包括：

- 模型 Provider
- MCP 连接
- 状态持久化
- 文件搜索
- 沙箱
- 日志 / telemetry
- 插件 / connectors / app tools

---

# 4. 核心入口与主调用链

这是理解项目最重要的一部分。

## 4.1 入口链

高层入口：

```text
CLI / TUI / App Server
  -> codex-rs/cli/src/main.rs
  -> 交互模式 / exec 模式
  -> codex_core::Session
  -> turn 执行
```

`cli_main()` 负责把参数与 config 整理后交给下游。

## 4.2 Session 初始化

在 `codex-rs/core/src/codex.rs` 的 `Session::new(...)` 中完成：

- 校验 `cwd`
- 确定是新会话、fork 还是 resume
- 组装 `RolloutRecorderParams`
- 建立 history / metadata builder
- 绑定 services：
  - model client
  - auth manager
  - skills manager
  - plugins manager
  - mcp manager
  - file watcher
  - agent control

这里可以看出一个关键设计：

- **Session 是长期存在的会话容器**
- **Turn 是一次具体的模型交互回合**

也就是：

- Session = 会话级资源与历史
- Turn = 单轮决策与执行

## 4.3 每轮 Turn 的上下文构建

`make_turn_context(...)` 是核心。

它为每轮组装：

- 当前模型与 provider
- cwd
- 日期 / 时区
- developer instructions
- compact prompt
- user instructions
- collaboration mode
- approval policy
- sandbox policy
- tools config
- feature flags
- dynamic tools
- skills 上下文
- timing state
- turn metadata state

这个设计很关键：

- **很多内容并不是直接写死在 prompt 里，而是先进入 `TurnContext`。**
- 之后 prompt 构造、tool router 构造、settings diff、event 回放都从 `TurnContext` 读取。

这使得系统具备：

- 更清晰的运行态抽象
- 更容易做 replay / resume / diff
- 更容易做“上下文与提示分离”

---

# 5. Prompt 设计：系统提示如何构造

你特别关心 loop 和 prompt，这里重点展开。

## 5.1 Prompt 的最终结构非常简洁

`build_prompt()` 在 `codex-rs/core/src/codex.rs` 中：

```text
Prompt {
  input,
  tools,
  parallel_tool_calls,
  base_instructions,
  personality,
  output_schema,
}
```

这说明最终发给模型的结构化 prompt 由六部分组成：

- `input`
  - 当前回合模型可见的消息项（包括历史、设置更新、用户输入、tool output 等）
- `tools`
  - 模型可见工具规格
- `parallel_tool_calls`
  - 模型是否可并行调工具
- `base_instructions`
  - 核心 system/base prompt
- `personality`
  - 输出风格/人格控制
- `output_schema`
  - 结构化输出约束

## 5.2 真正的 system prompt 对应什么？

从源码结构看，Codex 并不把“system prompt”做成一个孤立字符串常量直接塞给模型，而是拆为两层：

- **Base instructions**
  - session 级系统指令
- **Input items 中的上下文注入项**
  - 用户规则
  - AGENTS.md 片段
  - skill 片段
  - settings update items
  - 历史消息

也就是说它的“system prompt”是一个组合体，不是单段文本。

你可以把它理解为：

```text
System Prompt = BaseInstructions
             + Settings/Runtime Context Items
             + AGENTS.md/User Instructions
             + Skill Instructions
             + Tool Specs
```

这是一种更适合 agent 的设计，因为：

- 固定规则和动态上下文分离
- 变更可以做 diff，而不是每轮全量重注入
- skills 可以独立插拔
- tool spec 与对话上下文并列，而不是硬编码进系统提示

## 5.3 Prompt 为什么设计成这种结构？

这是为了支持以下目标：

- 多前端共用同一代理核心
- 可恢复、可 replay
- 工具调用可结构化约束
- 上下文可增量更新
- 适配不同模型 provider
- 支持 skill 注入和 repo 规则注入

换句话说：

- **Codex 的 prompt 不是“写一段很长的 system prompt”**
- **而是“把系统约束拆成结构化组件，在每轮调度时重组”**

这是一个成熟 agent 框架的典型做法。

---

# 6. Loop：核心执行循环是怎样的

这是整个项目最关键的部分。

## 6.1 单轮主 loop 的骨架

核心函数是：

- `run_sampling_request(...)`
- 内部调用 `try_run_sampling_request(...)`

它的流程是：

```text
1. built_tools()
2. get_base_instructions()
3. build_prompt()
4. 创建 ToolCallRuntime
5. 启动 code_mode worker（如果启用）
6. 调用 try_run_sampling_request()
7. 若模型流失败且可重试，执行 retry / transport 切换
8. 成功后返回本轮输出
```

这只是“外层请求 loop”。

真正的 agent 语义 loop 在 `try_run_sampling_request()` 对模型流事件的处理里：

- 接收模型增量输出
- 识别普通文本输出
- 识别 function/tool calls
- 调用工具
- 把工具结果作为新的 `ResponseInputItem` 回灌模型
- 继续采样直到 turn 完成

虽然这次没有逐段展开 `try_run_sampling_request()` 全文，但从外围结构和 `ToolRouter` 的使用方式可以非常明确地确认：

- **Codex 的 loop 是典型的 ReAct / Tool-augmented loop。**
- **模型生成 -> 工具执行 -> 工具结果回注 -> 继续生成**。

## 6.2 Loop 的关键分层

### 第一层：外层健壮性 loop

在 `run_sampling_request()` 里：

- 处理 provider stream 中断
- 处理 context window exceeded
- 处理 usage limit
- 处理 websocket -> https transport 切换
- 做 backoff retry

作用：

- 保证通信层稳定
- 不把模型连接问题暴露成业务层崩溃

### 第二层：模型-工具交互 loop

这是智能体语义上的主循环：

- prompt 发给模型
- 模型返回 tool call 或文本
- 工具执行
- 结果回写模型输入
- 重复直到 completed

### 第三层：会话历史与上下文更新 loop

本轮结束后：

- 更新 `ContextManager`
- 更新 token 统计
- 持久化 rollout
- 更新 reference context item
- 准备下一轮 diff-based settings injection

## 6.3 为什么要区分 Session、Turn、Loop？

因为三者职责不同：

- `Session`
  - 长生命周期，维护 history、service handles、feature 状态
- `Turn`
  - 短生命周期，封装一次请求的运行态配置
- `Loop`
  - 运行控制逻辑，驱动模型与工具反复交互

这种设计避免“把所有状态塞进一个超级 agent 对象”而导致系统不可维护。

---

# 7. 上下文管理：Codex 如何管理 context

## 7.1 `ContextManager` 的角色

在 `codex-rs/core/src/context_manager/history.rs` 中，`ContextManager` 保存：

- `items: Vec<ResponseItem>`
  - 历史 transcript，按时间从旧到新
- `token_info`
  - token 使用信息
- `reference_context_item`
  - 作为 diff 基准的上下文快照

这个设计非常关键。

它并不是简单保存聊天记录，而是多了一层：

- **reference context baseline**

这意味着系统不是每轮都盲目全量重新注入环境信息，而是把当前环境抽象为 context item，并与上一轮比较，生成增量更新。

## 7.2 只记录“模型真正可见”的历史

`record_items()` 里有一个关键行为：

- 只保留 API message 和 ghost snapshot 等模型可见项
- 非模型可见项不会进入用于后续 prompt 的历史

这说明 Codex 区分：

- UI/内部事件
- 模型可见上下文

这是很重要的工程约束。

否则系统很容易把很多 UI 事件、内部控制事件错误地喂给模型，导致上下文污染。

## 7.3 settings update item：增量上下文注入

`build_settings_update_items(...)` 会基于：

- `reference_context_item`
- `previous_turn_settings`
- `current_context`
- `shell`
- `exec_policy`
- personality feature

构造“设置更新项”。

源码里的注释已经透露设计方向：

- 希望未来变成纯 diff
- 使 replay / backtracking 更确定
- 将 runtime inputs 对模型可见的影响显式化

这正是高级 agent 系统的上下文管理方法：

- 不只是保留自然语言 history
- 还保留“环境状态 history”

## 7.4 context window 超限怎么处理

在 `run_sampling_request()` 中如果收到 `CodexErr::ContextWindowExceeded`：

- 调用 `sess.set_total_tokens_full(&turn_context)`
- 将 token 使用标记为满窗
- 终止当前请求

说明 Codex 会显式追踪 context usage，而不是无感失败。

## 7.5 Context 管理的核心原则

Codex 的上下文管理可以总结为五条：

- **模型可见上下文与系统内部事件分离**
- **历史消息与环境状态都进入上下文模型**
- **环境状态尽量以 diff 增量注入，而非重复全文注入**
- **token 使用是显式状态，而非隐式副作用**
- **turn 结束后更新 reference baseline，为下一轮做准备**

---

# 8. Skills 与 Rules：如何做 repo 规则、技能注入

你特别关心 `skills` 和 `rule`，这部分是项目亮点之一。

## 8.1 skills 的目标

skills 不是简单模板集合，而是“可发现、可选择、可注入到模型上下文中的能力说明”。

用途包括：

- 任务型操作指南
- 项目规则
- 领域知识片段
- 某个工具或流程的专用说明

## 8.2 SkillsManager 的职责

在 `codex-rs/core/src/skills/manager.rs` 中，`SkillsManager` 负责：

- 安装/卸载 system skills
- 按 cwd 加载技能
- 按 config layer stack 解析技能根目录
- 按 cwd 缓存加载结果
- 支持强制 reload
- 支持额外用户 roots

关键点：

- **skills 是按 cwd 生效的**
- **skills 受 config layer、plugins、bundled 开关共同影响**
- **skills 加载结果会缓存，避免频繁扫描磁盘**

## 8.3 skill roots 的优先级

在 `skills/loader.rs` 中，skills 从多个 root 扫描并去重，scope 排序优先级为：

- `Repo`
- `User`
- `System`
- `Admin`

这意味着：

- 仓库级技能优先于用户级技能
- 用户级优先于系统级

这本质上就是一套 layered rules / layered skills 体系。

## 8.4 AGENTS.md / User Instructions 注入

`codex-rs/core/src/instructions/user_instructions.rs` 中：

- `UserInstructions` 表示目录级规则
- `SkillInstructions` 表示技能内容

它们都会被转成 `ResponseItem` 注入 prompt 输入。

特别是：

- `UserInstructions::serialize_to_text()` 会把内容包在特定 fragment marker 里
- `USER_INSTRUCTIONS_PREFIX = "# AGENTS.md instructions for "`

这说明 Codex 明确支持：

- 从 AGENTS.md 或类似项目规则源中抽取说明
- 以结构化片段形式注入模型上下文

也就是说，**rule 的实现不是“在代码里硬编码一堆系统提示”，而是“把 repo/user instructions 作为上下文消息片段注入”。**

## 8.5 skills 如何被显式选中

从 `skills/injection_tests.rs` 可以看出，系统支持：

- 文本中显式提到 `$skill-name`
- markdown 链接形式指向某个 skill path
- 去重
- 冲突消解
- 避免与 connector 名称冲突
- 若路径缺失/禁用，不做 plain fallback

这很符合你的“不要 fallback”审美：

- 如果用户明确指定 skill/path，就按确定性规则选
- 不会因为模糊匹配而随便替代

## 8.6 rules 与 skills 的关系

可以这样理解：

- `rules`
  - 更偏全局或目录级行为约束（例如 AGENTS.md）
- `skills`
  - 更偏任务/能力型知识片段

在模型上下文层面，两者最终都变成注入消息，但语义不同：

- rules 规定“怎么做事”
- skills 提供“做这类事时要知道什么”

---

# 9. Tool Call：工具调用是如何工作的

这是 Codex 的另一条主脉络。

## 9.1 Tool 的整体设计

工具系统分四步：

```text
1. 根据 TurnContext 构造 ToolsConfig
2. 根据 ToolsConfig 构造 ToolRouter / ToolSpec / ToolRegistry
3. 模型产出 tool call 后，用 ToolRouter 解析为 ToolCall
4. ToolRegistry 按 name/namespace 分发到 handler 执行
```

## 9.2 ToolsConfig：本轮允许哪些工具

`make_turn_context()` 中会构造：

```text
ToolsConfig::new(...)
  .with_web_search_config(...)
  .with_allow_login_shell(...)
  .with_agent_roles(...)
```

`ToolsConfig` 中包含大量 feature gate：

- shell / exec
- apply_patch
- web search
- image generation
- code mode
- js repl
- collab tools
- artifact tools
- request user input
- tool search / tool suggest
- agent jobs

这表明：

- **工具集合是动态的，不是静态写死的。**
- 它由模型能力、feature flags、session source、sandbox、平台能力共同决定。

## 9.3 ToolSpec：模型看到的工具描述

`tools/spec.rs` 中定义工具规格，包括：

- 名称
- 描述
- JSON schema 参数
- 输出 schema
- freeform tool 格式

例如：

- `shell`
- `exec_command`
- `write_stdin`
- `apply_patch`
- web search
- wait tools
- request permission tools

重点是：**Codex 强依赖 schema 化 tool spec，而不是让模型自由生成工具调用字符串。**

这对稳定性非常重要。

## 9.4 ToolRouter：解析与派发

在 `tools/router.rs` 中，`ToolRouter` 负责：

- 保存 `registry`
- 保存 `specs`
- 过滤得到 `model_visible_specs`
- `build_tool_call()`：把模型输出项解析成 `ToolCall`
- `dispatch_tool_call()`：真正执行工具

`build_tool_call()` 支持多种调用来源：

- `ResponseItem::FunctionCall`
- `ResponseItem::ToolSearchCall`
- `ResponseItem::CustomToolCall`
- `ResponseItem::LocalShellCall`

说明 Codex 的工具调用不仅支持普通 function calling，还支持：

- MCP 工具
- client-side tool search
- custom tool
- 本地 shell 调用

## 9.5 ToolRegistry：基于 name/namespace 的 handler 分发

`tools/registry.rs` 中：

- `ToolRegistry` 保存 `HashMap<String, Arc<dyn AnyToolHandler>>`
- key 由 `tool_handler_key(name, namespace)` 生成
- `dispatch_any()` 负责统一分发

这使系统同时支持：

- 内建工具
- MCP namespace 工具
- 未来动态工具扩展

## 9.6 错误处理

`dispatch_tool_call_with_code_mode_result()` 的错误策略：

- Fatal 错误直接返回失败
- 普通工具错误转成 `failure_result()` 回给模型

也就是说：

- **很多工具失败不是终止整个 agent turn**
- **而是回一个结构化失败结果给模型，让模型自行调整策略**

这是成熟 agent loop 必备机制。

---

# 10. LLM 大模型是如何调用的

## 10.1 调用入口

主入口是 `run_sampling_request()`。

它做的事情：

- 构造 prompt
- 构造 tool runtime
- 调 `try_run_sampling_request()` 与模型流交互

说明真正的 LLM 接口在更底层 `model_client` / `client_session` 里。

## 10.2 Provider 抽象

`TurnContext` 中携带：

- `provider: ModelProviderInfo`
- `model_info: ModelInfo`

并且 `run_sampling_request()` 会根据 provider：

- 使用 provider-specific `stream_max_retries()`
- 在 websocket 失败时切换 transport

因此它的 LLM 抽象不是“只支持 OpenAI 单接口”，而是：

- 有 provider 层
- 有 model info 层
- 有 transport 层

## 10.3 Prompt 到模型请求的过程

逻辑上是：

```text
TurnContext + ContextItems + ToolSpecs
  -> Prompt
  -> ModelClientSession
  -> streaming responses
  -> ResponseItem 序列
```

也就是说，模型返回的不只是字符串，而是结构化 response items。

这也是为什么后续可以直接从输出里识别 tool calls。

## 10.4 支持的能力

从代码可以推断模型能力被显式建模为：

- 是否支持 parallel tool calls
- shell tool 类型
- web search tool 类型
- apply_patch tool 类型
- 图像输入/原图细节能力
- 实验性支持工具集

所以 Codex 不是“把所有模型当成一样”，而是做 capability-aware orchestration。

---

# 11. 如何建立代码索引、快速查找核心代码并放入上下文

你特别问了“如何建立代码索引、如何快速查找核心代码放入上下文”，这一块在 Codex 的设计里很有代表性。

## 11.1 文件级索引：`file-search`

`codex-rs/file-search/src/lib.rs` 提供了高性能文件查找能力。

它的特点：

- 基于 `ignore::WalkBuilder` 扫描文件树
- 基于 `nucleo` 做 fuzzy matching
- 支持 streaming updates
- 支持多 root
- 支持 `.gitignore` 语义
- 支持 exclude 覆盖规则
- 支持 query 增量更新

这不是全文代码索引，而是**文件路径层级的模糊索引**。

核心设计：

- 一个线程做 walker
- 一个线程做 matcher
- walker 持续把文件路径注入 `nucleo`
- matcher 在 query 更新时增量匹配并产出 top-N 结果

这使得它很适合：

- IDE 中快速跳转文件
- 大仓库中快速锁定候选核心文件

## 11.2 为什么这种索引设计有效

很多时候 agent 并不需要一开始就有完整 AST/index DB，它先需要：

- 快速找出最可能相关的文件
- 再对这些文件做精读

Codex 的策略显然是：

- **第一层：快速路径级检索**
- **第二层：进入具体文件读取与 grep**

这是更经济的上下文利用方式。

## 11.3 代码放入上下文的策略

从整体架构可推断它的策略是：

- 先用文件搜索 / grep / tool_search 找候选
- 再由 read/open 类工具读取具体片段
- 把读取结果作为新的输入项送入模型
- 不做全仓库粗暴注入

也就是说，Codex 的上下文工程原则是：

- **上下文按需拉取，不全量预载入**
- **文件路径定位优先于大规模内容嵌入**
- **工具检索是上下文构建的一部分**

## 11.4 它是否做“语义代码索引”？

从本次看到的核心文件里，最明确的是：

- 文件路径 fuzzy search
- tool discovery search
- connectors / discoverable tools

没有看到一个统一的“向量化代码库索引引擎”作为主路径。

这意味着 Codex 的主流做法更偏：

- 文件系统检索
- 文本搜索
- 工具辅助检索
- 逐步缩小上下文

而不是先构建一个大型向量数据库再召回。

这是合理的，因为 coding agent 的很多任务更依赖：

- 路径约定
- 命名约定
- grep 精确匹配
- diff 与符号邻域

---

# 12. diff 展示、patch 应用、merge/变更聚合

这是 Codex 的一个工程亮点。

## 12.1 `TurnDiffTracker`：每轮变更跟踪器

`codex-rs/core/src/turn_diff_tracker.rs` 是核心。

它做的事情不是简单记录“哪个文件改了”，而是：

- 为每个首次触达文件建立 baseline snapshot
- 对新增文件使用 `/dev/null` 语义表示
- 为外部路径维护稳定内部 UUID 名称
- 支持 rename / move 跟踪
- 聚合生成标准 unified diff
- 尽量对齐 git 风格输出

## 12.2 baseline 机制

`on_patch_begin()` 在 patch 执行前记录：

- 文件初始内容
- mode
- oid
- 路径映射

这样后续无论同一 turn 里文件经历多少次修改，都能相对于“本轮开始前的状态”生成总 diff。

这个设计非常好，因为它解决了 agent 常见问题：

- 多次工具调用导致 diff 碎片化
- rename 之后 diff 失真
- 新增/删除文件难以统一展示

## 12.3 unified diff 生成

`get_unified_diff()` / `get_file_diff()` 的逻辑：

- 遍历 tracked file
- 对 baseline 与当前磁盘状态做比较
- 文本文件使用 `similar::TextDiff::from_lines`
- `context_radius(3)` 生成统一 diff
- 非文本文件输出 `Binary files differ`

这说明 diff 展示不是依赖 git `diff` 命令直接输出，而是**内存中自主计算**。

优点：

- 不依赖用户提交/暂存状态
- 不依赖文件已经落到 git index
- 更适合 turn 级别的代理编辑过程

## 12.4 apply_patch 的执行链

`tools/handlers/apply_patch.rs` 的流程非常清楚：

```text
模型调用 apply_patch
  -> handler 解析 patch_input
  -> verify patch correctness
  -> 推导受影响文件路径
  -> 计算本次 patch 需要的文件系统写权限
  -> apply_patch::apply_patch(...)
  -> 可能直接输出结果，或委托 runtime/orchestrator 执行
  -> 结合 emitter 和 diff tracker 汇报变更
```

关键点：

- 先验证 patch，而不是直接盲写
- 先计算权限，再执行
- 结合 `ToolEmitter` 发 begin/finish 事件
- 结合 `SharedTurnDiffTracker` 跟踪变更

## 12.5 merge 在这里怎么理解

这个仓库中“merge”不是一个单独的 Git merge 子系统，而主要体现在两个层次：

### 变更聚合 merge

多个 patch/tool action 在单个 turn 内最终 merge 成一个 unified diff 展示给用户。

### 权限与策略 merge

例如：

- session granted permissions
- turn granted permissions
- additional permissions
- sandbox policy

这些会在执行前做合并，形成有效权限视图。

### connectors / apps merge

`built_tools()` 中也会把：

- plugin apps
- accessible connectors

做 merge，形成本轮可见 app/tool 集合。

所以这里的 merge 更偏“系统状态合并”，不只是 Git 三方合并。

---

# 13. `built_tools()`：本轮工具集合如何生成

这是理解 prompt 与 tool call 关系的桥梁。

`built_tools()` 大致流程：

```text
1. 从 MCP connection manager 拉取全部 MCP tools
2. 从 plugins manager 拉取 plugin apps / skill roots
3. 结合 session 中显式启用的 connectors
4. 计算 accessible connectors
5. 若启用 app/tool search，则计算 discoverable tools
6. 用 ToolsConfig + 各类工具源构建 ToolRouter
```

这说明本轮 tools 不是固定静态数组，而是运行时求值结果，输入包含：

- 当前模型能力
- 当前 feature gate
- MCP server 当前连通情况
- 用户/会话显式启用的 connector
- plugin system 提供的 app/tool
- 当前 auth 状态

所以 tool list 本身就是动态上下文的一部分。

---

# 14. prompt、loop、tool call 三者如何协同

这三者的关系可以总结为：

```text
TurnContext
  -> built_tools()
  -> build_prompt()
  -> model stream
  -> ToolRouter::build_tool_call()
  -> ToolRegistry::dispatch_any()
  -> tool output
  -> 追加到 input
  -> model 继续推理
```

换句话说：

- prompt 决定模型看到什么
- loop 决定模型什么时候继续/停止
- tool call 决定模型如何作用于外部世界

而 Codex 的成熟之处在于：

- 三者都有清晰的数据结构边界
- 没有把所有逻辑硬塞到一个 `agent.run()` 黑盒里

---

# 15. Session source、SubAgent 与更复杂的 loop

你问 loop，很值得补充多 agent / 子 agent 设计。

## 15.1 `SessionSource`

`make_turn_context()` 里会携带 `session_source`。

用途是：

- 区分正常会话
- 区分 subagent
- 影响 tools 开关，例如某些用户交互工具只在非 subagent 中可用

## 15.2 agent job loop

在 `tools/handlers/agent_jobs.rs` 里有一个非常典型的后台 worker loop：

- 从数据库读取 pending items
- 构造 worker prompt
- `spawn_agent(...)`
- 标记 item running
- 轮询 finished threads
- 回收 stale item
- finalize finished item
- 导出结果

这个 loop 展示了 Codex 的另一个能力：

- **不仅支持单智能体 turn loop，还支持多 worker/subagent orchestration。**

也就是说 Codex 的 loop 体系至少有两种：

- 单轮模型工具交互 loop
- 多 agent job 调度 loop

---

# 16. Prompt 设计理念：为什么它看起来“薄”，其实很强

初看 `build_prompt()` 你会觉得它很薄，只有几个字段。但其实它背后依赖大量前处理。

## 16.1 Prompt 不是在一个函数里“写出来”的

很多系统会有一个超长 `render_system_prompt()`。Codex 不这样做。

它把 prompt 生产拆成：

- config 层
- turn context 层
- context history 层
- settings update 层
- skill/rule 注入层
- tool spec 层
- 最终 prompt 组装层

优点：

- 可测试
- 可 diff
- 可增量更新
- 更适合多产品形态复用

## 16.2 这意味着怎样的 system prompt 工程能力

Codex 的“system prompt 工程”本质是：

- 把固定指令与动态状态解耦
- 把 repo/user 规则与系统规则解耦
- 把技能说明从 base prompt 中抽离
- 把工具说明交给 tool spec，而不是自然语言段落

所以它的 prompt 设计比传统“长 system prompt”更先进，也更工程化。

---

# 17. SDK：有哪些功能与接口

仓库顶层 `sdk/` 包含：

- `sdk/typescript/`
- `sdk/python/`
- `sdk/python-runtime/`

说明 Codex 不只提供 CLI，也提供编程接口层。

## 17.1 TypeScript SDK

从目录结构看：

- `src/`
- `samples/`
- `tests/`
- `README.md`

一般意味着它提供：

- 客户端 API 封装
- 与 app-server 或协议层通信
- 样例代码
- 测试用例

## 17.2 Python SDK

同样提供：

- `src/`
- `tests/`
- `docs/`
- `README.md`

通常用于：

- 在 Python 应用中调用 Codex agent 或相关服务
- 自动化脚本集成
- 构建二次开发工具

## 17.3 SDK 与 core 的关系

这个仓库的 SDK 更像：

- 对外 API / 客户端层

而不是：

- 核心智能体引擎本身

真正的 loop、prompt、context、tool runtime 还是在 `codex-rs/core`。

如果你要二次开发智能体能力：

- 看 `core`
- 如果要嵌入现有应用，则看 `sdk` + `app-server-protocol`

---

# 18. 各模块设计与核心功能一览

下面给出按职责整理的模块地图。

## 18.1 `codex-rs/cli`

核心功能：

- 命令行参数解析
- 选择交互/exec/review/login/app-server 等模式
- 把 root config overrides 传播到子命令

不是智能体核心。

## 18.2 `codex-rs/tui`

核心功能：

- 终端 UI
- 聊天窗口、diff 展示、交互操作
- 事件消费与渲染

是展示层，不是代理核心。

## 18.3 `codex-rs/app-server`

核心功能：

- 面向 IDE/外部客户端的 server
- 暴露线程/配置/工具/事件等 RPC 接口

是远程控制与集成层。

## 18.4 `codex-rs/app-server-protocol`

核心功能：

- 线协议定义
- TS/JSON schema 生成
- v2 API 类型定义

是跨语言契约层。

## 18.5 `codex-rs/core`

核心功能：

- Session 生命周期
- Turn loop
- Prompt 构建
- Context 管理
- Tool routing/execution
- Skills/rules 注入
- Diff 跟踪
- 模型调用 orchestration

这是最重要的 crate。

## 18.6 `codex-rs/config`

核心功能：

- 配置结构定义
- 配置层叠加载
- feature / provider / permission 配置

决定系统运行形态。

## 18.7 `codex-rs/state`

核心功能：

- 状态存储
- 会话、作业、后台任务、持久化数据管理

是 agent runtime 的状态底座。

## 18.8 `codex-rs/skills`

核心功能：

- skill 元数据与装载机制
- 扫描技能根目录
- 优先级与去重

与 `core/src/skills/*` 协同工作。

## 18.9 `codex-rs/file-search`

核心功能：

- 路径级模糊检索
- 增量 query 更新
- 大仓库快速文件定位

服务于代码导航和上下文检索。

## 18.10 `codex-rs/apply-patch`

核心功能：

- patch 解析与验证
- patch action 表示
- 为核心工具 handler 提供底层 patch 语义支持

## 18.11 `codex-rs/connectors`

核心功能：

- app/connectors 发现与可访问性判断
- 与 MCP tools 集成

## 18.12 `codex-rs/rmcp-client` / `mcp-server`

核心功能：

- MCP 服务连接
- 远程工具接入
- OAuth / auth 等集成

## 18.13 `codex-rs/exec` / `shell-command` / `shell-escalation`

核心功能：

- shell 执行
- PTY / unified exec
- 升权与 approval 逻辑

## 18.14 `codex-rs/protocol`

核心功能：

- 核心数据模型定义
- 响应项 / 文件变更 / 事件协议等

---

# 19. 如果你要快速掌握这个项目，应优先看哪些文件

建议按下面顺序读。

## 第一层：主干

- `codex-rs/cli/src/main.rs`
- `codex-rs/core/src/codex.rs`
- `codex-rs/core/src/tools/router.rs`
- `codex-rs/core/src/tools/spec.rs`

## 第二层：上下文与提示

- `codex-rs/core/src/context_manager/history.rs`
- `codex-rs/core/src/context_manager/updates/*`
- `codex-rs/core/src/instructions/user_instructions.rs`
- `codex-rs/core/src/skills/manager.rs`
- `codex-rs/core/src/skills/loader.rs`

## 第三层：代码编辑与变更

- `codex-rs/core/src/turn_diff_tracker.rs`
- `codex-rs/core/src/tools/handlers/apply_patch.rs`
- `codex-rs/core/src/tools/runtimes/apply_patch.rs`
- `codex-rs/apply-patch/*`

## 第四层：检索与扩展

- `codex-rs/file-search/src/lib.rs`
- `codex-rs/connectors/*`
- `codex-rs/rmcp-client/*`
- `codex-rs/app-server-protocol/*`

---

# 20. 关键设计优点总结

## 20.1 Prompt 工程成熟

不是粗暴拼字符串，而是：

- base instructions
- settings diff
- history
- skills
- AGENTS.md
- tools

分层组装。

## 20.2 上下文工程成熟

- 历史与 runtime settings 分开管理
- reference context baseline 支持 diff
- token 使用显式追踪

## 20.3 Tool 系统成熟

- schema 化 tool spec
- registry/router 分发
- MCP / local / custom 统一抽象
- approval / sandbox 集成

## 20.4 变更管理成熟

- turn 级 diff 跟踪
- rename / add / delete 统一处理
- 自主 unified diff 生成

## 20.5 可扩展性强

- 多前端
- 多 provider
- 多工具源
- skills / plugins / connectors
- app-server + SDK

---

# 21. 你最关心的问题，给出一句话答案

## 21.1 loop 是怎样的？

- **本质是 `模型生成 -> 识别 tool call -> 工具执行 -> 工具结果回灌 -> 模型继续生成` 的 agent loop，外面再包一层流式重试/transport 切换的健壮性 loop。**

## 21.2 prompt 怎么设计？

- **不是单一长字符串，而是 `base instructions + 历史输入项 + settings diff + AGENTS.md/user instructions + skill instructions + tool specs` 的结构化组合。**

## 21.3 system prompt 在哪里？

- **核心是 `BaseInstructions`，但完整 system 行为约束实际上分散在 base instructions、settings update items、AGENTS.md 注入、skills 注入、tool specs 之中。**

## 21.4 上下文怎么管理？

- **通过 `ContextManager` 维护模型可见历史、token 使用与 reference context baseline，并基于 baseline 为下一轮生成增量 settings update。**

## 21.5 skills/rules 如何做？

- **skills 来自 repo/user/system/admin 多层 root 扫描和优先级去重；rules 主要通过 AGENTS.md / user instructions 作为结构化片段注入到模型上下文。**

## 21.6 如何 tool call？

- **模型输出结构化 response item，`ToolRouter` 将其解析为 `ToolCall`，`ToolRegistry` 再按 name/namespace 分发给 handler 执行。**

## 21.7 如何建立代码索引与快速找核心代码？

- **先用 `file-search` 做路径级 fuzzy 索引，再对候选文件做精读与 grep，把必要片段按需拉入上下文。**

## 21.8 如何展示 diff 与 merge？

- **`TurnDiffTracker` 在 turn 开始时建立 baseline，之后把本轮所有文件修改聚合成统一 unified diff；merge 更多体现为变更、权限、工具来源的状态合并。**

---

# 22. 对二次开发者的建议

如果你打算基于这个项目做定制智能体，建议：

- 优先扩展 `core`，不要先改 CLI/TUI
- 先理解 `TurnContext`，再理解 `build_prompt()`
- 新增工具优先接入 `ToolSpec + ToolHandler + ToolRegistry`
- 不要把 repo 规则写死进 base prompt，优先走 AGENTS.md / instruction injection
- 检索能力优先做“按需拉取上下文”，不要全仓库灌入模型
- 代码修改路径优先接 `apply_patch + TurnDiffTracker`，不要绕开 diff 体系

---

# 23. 最后给出一个脑图式总结

```text
CLI / TUI / App Server / SDK
  -> Session
    -> TurnContext
      -> ToolsConfig
      -> SkillsContext
      -> User/Developer/Runtime Settings
    -> ContextManager
      -> model-visible history
      -> token usage
      -> reference context baseline
    -> built_tools()
      -> MCP tools
      -> plugin apps
      -> discoverable tools
      -> ToolRouter / ToolRegistry
    -> build_prompt()
      -> BaseInstructions
      -> input items
      -> tool specs
    -> run_sampling_request()
      -> model stream
      -> parse tool calls
      -> dispatch tools
      -> append tool outputs
      -> continue until completion
    -> TurnDiffTracker
      -> baseline snapshots
      -> unified diff
      -> add/delete/rename tracking
```

以上就是对 `codex` 项目的源码级详细解读。

---

# 24. 代码如何检索、如何索引、如何定位关键代码段，以及如何决定把哪些内容放入 context prompt

这是你新增问题里最值得深挖的一部分。要点先说在前面：

- **Codex 的主路径不是“预先构建一个巨大的语义向量索引库”。**
- **它更像是一个分层检索系统：路径级索引 -> 文本级定位 -> 精确读取 -> 结构化注入 prompt。**
- **决定哪些内容进入 prompt，不是 UI 层拍脑袋，而是由 `ContextManager`、`TurnContext`、settings diff、skills/rules 注入和工具回写共同决定。**

## 24.1 检索不是一个动作，而是一条流水线

从仓库设计看，Codex 的检索/上下文构造至少分五层：

```text
1. 文件级候选召回
2. 文本/符号级二次定位
3. 精确片段读取
4. 判定哪些结果需要进入模型上下文
5. 将结果转成 ResponseItem / ResponseInputItem 注入 prompt
```

也就是说，它不是“先把整个仓库 embed，再从向量库拿结果”，而是更工程化地做逐层收敛。

## 24.2 第一层：文件路径级索引

`codex-rs/file-search/src/lib.rs` 是最明确的索引实现。

它做的是：

- 遍历文件树
- 遵守 `.gitignore`
- 支持多根目录
- 将路径条目持续注入 `nucleo` 匹配器
- 对查询做增量 fuzzy 匹配

这个索引的本质不是 AST，也不是 embedding，而是：

- **路径索引**
- **候选文件召回索引**

为什么这样设计合理：

- coding agent 第一需求通常不是“立刻理解整个仓库”
- 而是“先找到最相关的几个文件”
- 一旦路径锁定，后续 grep/read 的性价比远高于大范围语义召回

因此它的关键技术不是向量搜索，而是：

- `ignore::WalkBuilder` 文件遍历
- `nucleo` 模糊匹配
- streaming / incremental query 更新
- 多线程 walker + matcher 协作

## 24.3 第二层：文本级定位与关键代码段定位

路径索引只解决“找文件”，并不解决“找文件里哪段代码最关键”。

Codex 的整体设计说明下一步会使用：

- 文本搜索
- 函数名/工具名/协议名级别精确匹配
- tool search / discoverable tools
- 按调用链继续收敛

从你前面关心的核心路径看，典型做法就是：

- 先锁定 `codex-rs/core/src/codex.rs`
- 再锁定 `build_prompt`、`run_sampling_request`
- 再锁定 `ToolRouter::build_tool_call`
- 再锁定 `ContextManager::for_prompt`

这说明“关键代码段定位”在 Codex 中更接近：

- **基于架构主干的定向文本定位**
- **而不是一个黑盒语义检索器自动决定一切**

## 24.4 第三层：读段而不是读全仓库

无论是 CLI、TUI、还是 app-server 驱动的客户端，合理的 agent 行为都不是把整个文件树都放进 prompt，而是：

- 只把当前任务直接相关的代码片段读出来
- 尽量保留局部上下文
- 避免把大量无关实现塞进上下文窗

这和 `ContextManager` 的设计是一致的：

- history 只保留模型真正需要看到的项
- runtime context 用 diff 形式增量注入
- tool output 用结构化 item 回灌

## 24.5 第四层：什么会被放入 context prompt

从 `build_prompt()` 看，最终 prompt 中的 `input` 是 `Vec<ResponseItem>`。真正关键的是：**哪些东西会被转成 `ResponseItem`。**

会进入 prompt 的主要有：

- 历史消息
- 上一轮工具输出
- 当前用户输入
- AGENTS.md / user instructions
- skill instructions
- settings update items
- 环境上下文 diff
- 某些结构化 runtime 消息

不会直接进入 prompt 的主要有：

- 仅 UI 可见的事件
- 纯内部调度事件
- 非模型可见 telemetry
- 某些 ghost snapshot 只用于内部状态，最终 `for_prompt()` 会剔除

这点在 `ContextManager::record_items()` 和 `ContextManager::for_prompt()` 上非常清楚：

- 只有 API message / ghost snapshot 这类模型上下文项会进 history
- `for_prompt()` 最终还会做 normalize，并移除不应直接给模型看的项

## 24.6 `ContextManager` 如何决定保留哪些上下文

`ContextManager` 有三个关键动作：

- `record_items()`
- `for_prompt()`
- `estimate_token_count()`

### `record_items()` 的选择逻辑

它只记录：

- 模型 API message
- ghost snapshot

这意味着系统从一开始就执行“上下文白名单”原则。

### `for_prompt()` 的选择逻辑

它会：

- 标准化历史
- 移除 `GhostSnapshot`
- 按输入模态剥离不适合的内容，例如模型不支持图像时去掉图像内容

这说明 Codex 的上下文选择是**能力感知的**：

- 不是所有历史都原样复用
- 而是根据模型输入能力做裁剪

### `estimate_token_count()` 的作用

它基于：

- base instructions token 估计
- item token 估计

来评估当前 prompt 大小。

虽然这是近似估算，不是 tokenizer 级精算，但它足够驱动：

- 满窗检测
- compaction 触发
- 上下文风险感知

## 24.7 settings diff：为什么它是上下文选择的核心技术

`codex-rs/core/src/context_manager/updates.rs` 其实暴露了一个非常关键的思想：

- **不是每轮都把完整环境、权限、模式、人格、realtime 状态全文重发给模型**
- **而是只在前后两轮有变化时生成 update item**

`build_settings_update_items(...)` 会尝试构造：

- 环境更新项
- 权限更新项
- collaboration mode 更新项
- realtime 更新项
- personality 更新项
- model instructions 更新项

这就是决定哪些 runtime 上下文该进 prompt 的核心机制。

### 环境上下文如何选择

`build_environment_update_item()` 会比较：

- previous turn 的环境上下文
- next turn 的环境上下文

如果 `equals_except_shell()` 成立，就**不生成新的 context item**。

这意味着：

- 工作目录没变
- 关键环境没变
- 就不浪费 context window 重复描述

### 权限上下文如何选择

`build_permissions_update_item()` 只在以下变化时生成 developer instruction：

- `sandbox_policy` 变化
- `approval_policy` 变化

换言之：

- 权限是 prompt 的一部分
- 但只在变化时进入 prompt

### collaboration / realtime / personality / model instructions

这些都属于“模型行为边界条件”。Codex 的策略是：

- 如果没变，不重复注入
- 如果变了，用 developer instruction 形式精确告知模型

这是一个非常重要的技术点：

- **Codex 不是仅在“内容层”做上下文管理，也在“行为约束层”做 diff 管理。**

## 24.8 用户操作如何决定是否进入 context

你还特别问了“用户操作”如何决定放不放进 context prompt。

从 app-server 和 core 的设计看，用户操作分三类：

### 第一类：直接语义输入

例如：

- 文本输入
- 图像输入
- local image
- skill invocation
- mention app/plugin

这些会直接进入本轮 `turn/start` 的 `input`，因此显然进入 prompt。

### 第二类：改变 agent 运行条件的操作

例如：

- 切换 collaboration mode
- 改模型
- 改 sandbox / approval policy
- 开启 realtime

这些不会以“用户自然语言消息”进入，而是被转换为 settings update / developer instruction item。

### 第三类：纯控制类操作

例如：

- thread/list
- thread/archive
- UI 折叠/展开
- 本地渲染事件

这些通常**不会进入模型上下文**。

这三分法非常关键，因为它解释了：

- 为什么 Codex 的 prompt 不会被 UI 噪音污染
- 为什么它能保持模型上下文相对干净

## 24.9 历史压缩与 compact：当上下文太大时怎么办

`core/src/client.rs` 中 `compact_conversation_history(...)` 说明了另一个关键技术：

- Codex 支持调用 compact endpoint，对现有 `ResponseItem` 历史做压缩
- 请求仍然带着：
  - `instructions`
  - `input`
  - `tools`
  - `parallel_tool_calls`
  - `reasoning`
  - `text` / output schema

也就是说，历史压缩不是脱离 agent 语境的独立摘要，而是：

- 在当前模型能力与工具上下文下重新生成可继续工作的压缩 history

这点很重要，因为很多系统的 summarize/compact 会把 tool context 丢掉，Codex 明显是在避免这个问题。

## 24.10 如何定位“关键代码段”进入上下文：实际决策原则

虽然我们没看到一个名为 `select_relevant_snippets()` 的统一函数，但从整体架构能总结出实际判定标准：

- **离当前任务最近的调用链优先**
- **离当前错误/工具/协议最近的代码优先**
- **结构边界处的代码优先**
  - 入口函数
  - router
  - protocol model
  - context manager
  - handler
- **配置/协议定义优先于猜测实现**
- **只要能用结构化上下文表达，就不要灌入大段自然语言说明**

换句话说，Codex 更像一个“结构优先的检索式 agent”，而不是“全文背包式 agent”。

## 24.11 这里面有哪些关键技术

归纳起来，关键技术包括：

- 路径级模糊索引：`nucleo` + 增量搜索
- 文件系统过滤：`.gitignore` / roots / excludes
- 结构化上下文表示：`ResponseItem` / `ResponseInputItem`
- 历史归一化与白名单保留：`ContextManager`
- 环境/权限/模式 diff 注入：`build_settings_update_items()`
- 能力感知裁剪：按输入模态移除不适合内容
- 历史压缩：compact conversation history
- 工具回灌：将 tool outputs 重新纳入上下文，而不是仅显示在 UI
- token 估计与 context window 监控

其中最有工程含量的其实不是搜索本身，而是：

- **如何把检索结果变成干净、可持续、可 replay 的 prompt 上下文。**

---

# 25. 是否包含 VS Code 插件？如果包含，如何实现？

## 25.1 结论

**包含 VS Code 集成支持。**

但是要精确表述：

- 这个仓库里我没有看到 VS Code 扩展前端源码本体作为主目录存在
- 但 `codex-rs/app-server/README.md` 明确写明：
  - `codex app-server` 是用于驱动 **Codex VS Code extension** 这类富交互界面的接口
- 文档中还给出了官方 VS Code 扩展初始化例子，`clientInfo.name = "codex_vscode"`

因此可以确定：

- **仓库本身包含 VS Code 扩展所依赖的后端接口与协议层**
- **VS Code 扩展前端实现很可能在另一个仓库或发布产物中**
- **这里是扩展后端能力的 authoritative implementation**

## 25.2 VS Code 集成的总体实现形态

实现方式不是“VS Code 直接嵌入 core”，而是：

```text
VS Code extension
  -> 启动/连接 codex app-server
  -> 通过 JSON-RPC 2.0 发送 initialize / thread / turn / fs / config 等请求
  -> 订阅 item/turn/thread notifications
  -> 在 VS Code UI 中渲染消息、计划、工具调用、diff、审批等状态
```

这是一种典型的：

- **前端扩展 / 后端 agent server 解耦架构**

优点：

- VS Code、CLI、TUI 可以共用一套 agent 核心
- 协议稳定后，前端可以独立演化
- IDE 集成不会把复杂 agent runtime 直接塞进 Node extension host

## 25.3 `codex app-server` 的协议基础

`codex-rs/app-server/README.md` 明确说明：

- 使用 **JSON-RPC 2.0** 风格双向通信
- 支持：
  - `stdio://`（默认）
  - `ws://IP:PORT`（实验性）

因此 VS Code 扩展最典型的实现方式应该是：

- 启动本地 `codex app-server --listen stdio://`
- 通过 extension host 和 server 子进程用 JSONL over stdio 交互

这和很多 language server / MCP client / AI IDE extension 的架构非常一致。

## 25.4 初始化握手如何做

app-server 生命周期文档明确给出：

- 客户端连接后，必须先发 `initialize`
- 然后发 `initialized` notification
- 否则其他请求会被拒绝

VS Code 扩展例子：

```json
{
  "method": "initialize",
  "id": 0,
  "params": {
    "clientInfo": {
      "name": "codex_vscode",
      "title": "Codex VS Code Extension",
      "version": "0.1.0"
    }
  }
}
```

这说明扩展至少会维护：

- client identity
- capabilities
- 可选 notification opt-out 列表

## 25.5 VS Code 扩展需要实现哪些能力

根据 app-server 暴露的 API，一个完整的 VS Code 扩展前端至少需要实现：

### 会话与线程管理

- `thread/start`
- `thread/resume`
- `thread/fork`
- `thread/list`
- `thread/read`
- `thread/archive`
- `thread/unarchive`
- `thread/name/set`

对应 VS Code UI 能力：

- 新建聊天
- 恢复历史会话
- 分叉会话
- 左侧会话列表
- 查看 thread 历史

### Turn 驱动

- `turn/start`
- `turn/steer`
- `turn/interrupt`

对应 VS Code UI 能力：

- 发送消息
- 在进行中的 turn 中追加 steering
- 中断生成

### 文件与工作区操作

- `fs/readFile`
- `fs/writeFile`
- `fs/createDirectory`
- `fs/readDirectory`
- `fs/getMetadata`
- `fs/remove`
- `fs/copy`

这些能力对于扩展很关键，因为它们可以：

- 让扩展在需要时直接经 app-server 访问文件系统
- 不必把所有文件操作逻辑都实现两套

### 模型与配置操作

- `model/list`
- `config/read`
- `config/value/write`
- `config/batchWrite`
- `collaborationMode/list`

对应 UI：

- 模型下拉框
- 模式选择器
- 设置面板

### Skills / Plugins / Apps / MCP

- `skills/list`
- `skills/changed`
- `app/list`
- `plugin/list`
- `plugin/read`
- `plugin/install`
- `plugin/uninstall`
- `mcpServerStatus/list`
- `mcpServer/oauth/login`

对应 UI：

- skills 面板
- app/plugin 市场列表
- MCP 授权弹窗

### 审批与权限交互

- 批准 shell / patch / permission request
- 响应 `RequestUserInput`
- 处理 guardian_subagent 模式

这部分对于 IDE 集成尤其重要，因为它决定：

- 用户如何在 GUI 中审批危险操作
- 用户如何输入补充参数

## 25.6 VS Code 端如何渲染流式结果

`codex app-server` 不是只返回最终答案，而是流式发送通知：

- `turn/started`
- `item/started`
- `item/completed`
- `item/agentMessage/delta`
- 各种工具 begin/end
- plan delta
- reasoning delta
- turn completed

因此扩展一般要实现一个事件驱动 UI 状态机：

```text
JSON-RPC notification
  -> client-side reducer / store
  -> thread state / turn state / item state 更新
  -> chat panel / diff panel / approval panel / status bar 刷新
```

这和单纯“等最终回答渲染文本”完全不同。

## 25.7 VS Code 如何显示 diff

结合 `TurnDiffTracker` 和 app-server 事件体系，可以推断 VS Code 扩展的 diff 展示方式是：

- 等待 turn 结束或 patch 完成后收到统一 diff 事件
- 或结合文件变更事件、patch apply begin/end 逐步刷新
- 在 VS Code 内部调用 diff viewer / virtual document / SCM-like UI 展示

这里后端的关键支撑就是：

- `TurnDiffTracker::get_unified_diff()`
- `EventMsg::TurnDiff`

也就是说，VS Code 不需要自己从 Git 猜 turn 改了什么；后端直接提供 turn 级统一 diff。

## 25.8 VS Code 如何显示计划模式、工具进度、审批

从 app-server 协议可知，扩展可单独渲染：

- 计划项
- agent message delta
- reasoning delta
- shell begin/output/end
- patch begin/end
- web search begin/end
- image generation begin/end
- approval request
- request user input

因此 VS Code 集成不是一个“chat window”，而是一个“多事件面板”。

## 25.9 如果你自己实现一个 VS Code 扩展，核心流程是什么

可以概括为：

```text
1. 扩展激活
2. 启动 codex app-server 子进程（stdio）
3. 发送 initialize / initialized
4. 根据工作区调用 thread/start 或 thread/resume
5. 用户发送消息 -> turn/start
6. 监听 item/started, item/completed, delta, turn/completed
7. 对审批/输入请求回调 UI
8. 对 fs/config/model/skills/plugin 等 RPC 做面板集成
9. 在 turn 完成后渲染 diff / 最终结果
```

## 25.10 这一架构的关键优点

- **IDE 扩展非常薄，核心逻辑都在 app-server/core**
- **同一 agent 核心可被多个前端复用**
- **协议清晰，易于测试与版本化**
- **前后端边界明确，便于演进**

---

# 26. LLM 输出是怎样的格式？有哪些类型输出？不同 mode 下输出有什么不同？

这是理解 Codex 的关键，因为它不是“模型只输出一段字符串”。

## 26.1 核心结论

**LLM 在 Codex 中的输出主结构是 `ResponseItem`。**

定义在：

- `codex-rs/protocol/src/models.rs`

也就是说，Codex 接收的是一个**结构化输出流**，而不是简单文本流。

## 26.2 `ResponseItem` 的主要类型

根据协议定义，主要包括：

- `Message`
- `Reasoning`
- `LocalShellCall`
- `FunctionCall`
- `ToolSearchCall`
- `FunctionCallOutput`
- `CustomToolCall`
- `CustomToolCallOutput`
- `ToolSearchOutput`
- `WebSearchCall`
- `ImageGenerationCall`
- `GhostSnapshot`
- `Compaction`
- `Other`

其中最重要的几类分别对应：

### `Message`

普通消息输出：

- `role`
- `content: Vec<ContentItem>`
- `phase: Option<MessagePhase>`

这就是模型的常规自然语言输出载体。

### `Reasoning`

表示 reasoning 内容，包括：

- summary
- raw content（可选）
- encrypted content

说明 Codex 支持把 reasoning 作为独立 item 处理，而不是混在普通文本消息中。

### `FunctionCall`

结构化工具调用：

- `name`
- `namespace`
- `arguments: String`
- `call_id`

注意：`arguments` 是**字符串形式的 JSON**，而不是已经解析好的对象。

### `LocalShellCall`

模型直接触发本地 shell 调用的结构化 item。

### `CustomToolCall`

自由格式工具调用，`input` 是字符串。

### `ToolSearchCall`

表示模型请求客户端执行工具搜索。

### `WebSearchCall` / `ImageGenerationCall`

是 Responses API 特定的输出 item，表示模型已触发这些 provider 级工具能力。

## 26.3 `ContentItem` 的格式

`Message.content` 里的内容项是：

- `InputText`
- `InputImage`
- `OutputText`

这意味着消息本身也不是只存一段文本，而是分块内容结构。

## 26.4 输出流不是一次性对象，而是 SSE 事件流

在 `codex-api/src/sse/responses.rs` 里，SSE 事件被解析成 `ResponseEvent`。关键事件包括：

- `response.output_item.added`
- `response.output_item.done`
- `response.output_text.delta`
- `response.reasoning_summary_text.delta`
- `response.reasoning_text.delta`
- `response.completed`
- `response.failed`
- `response.incomplete`

这说明模型输出过程是：

```text
SSE event stream
  -> ResponseEvent
  -> 累积 / 更新 active item
  -> item 完成后变成完整 ResponseItem
```

## 26.5 文本输出与工具调用输出的区别

### 文本输出

文本输出主要表现为：

- `OutputItemAdded(Message)`
- 后续不断收到 `OutputTextDelta`
- 最终 `OutputItemDone(Message)`

也就是说：

- 先有 item 壳
- 再有 delta
- 最后完成 item

### 工具调用输出

工具调用则通常表现为：

- `OutputItemDone(FunctionCall)`
- 或 `OutputItemDone(LocalShellCall)`
- 或 `OutputItemDone(CustomToolCall)`

因为工具调用本身通常不依赖长文本 delta，而是完整结构化 item 一次给出。

## 26.6 `MessagePhase`：commentary vs final answer

协议里定义了：

- `Commentary`
- `FinalAnswer`

它用于区分：

- 中途 commentary / 进度说明
- 最终回答

但源码也明确提醒：

- provider 不一定稳定提供这个字段
- 因此下游不能完全依赖它

这说明 Codex 设计是“尽量利用 provider phase 信息，但保留兼容逻辑”。

## 26.7 不同 mode 下输出有什么不同

### Plan 模式

这是源码中最明确被特殊处理的模式。

在 `try_run_sampling_request()` 和相关 plan-mode 处理代码里：

- `plan_mode = turn_context.collaboration_mode.mode == ModeKind::Plan`
- 使用 `AssistantTextStreamParser` 把同一份流式文本拆成：
  - 普通 assistant text
  - proposed plan text

plan mode 会额外引入：

- `PlanModeStreamState`
- `ProposedPlanItemState`
- `ProposedPlanSegment`
- `EventMsg::PlanDelta`
- `TurnItem::Plan`

也就是说，plan mode 下模型文本输出不是被当作普通消息直出，而是会被再解析一层：

- 普通文本 -> agent message delta
- 计划片段 -> plan item / plan delta

### Agent 模式

普通 agent 模式下：

- 模型文本主要作为 `AgentMessage` 流式展示
- reasoning 单独以 reasoning events 展示
- tool call 按结构化 item 被捕获并执行

### Code mode

从 `build_prompt()`、`built_tools()`、`ToolRouter` 可以看出 code mode 更显著的差异主要不在“输出协议换了一套”，而在：

- 可见工具集不同
- 有 code mode worker
- 某些 nested tools 会从 model-visible tools 中隐藏

也就是说：

- **code mode 主要改变的是工具可见性、执行策略与编排方式**
- **不是 ResponseItem 协议本身完全换一种输出格式**

### Review mode

源码中对 review child thread 有特殊处理：

- 某些 assistant text delta 可能被抑制
- 最终由 review output / 特定 item 供 UI 展示

说明某些 mode 会改“前端展示行为”，即使底层仍基于统一事件流。

## 26.8 输出代码和输出工具调用分别怎样体现

### 输出代码

模型“输出代码”在 Codex 中通常不是一个单独的协议类型。它一般体现在：

- `Message.content` 中的 `OutputText`
- 也就是普通 assistant 文本里包含代码块

如果模型只是给建议代码、不直接修改文件，那就是普通消息输出。

### 输出工具调用

模型一旦要“真正作用于外部世界”，就不再通过普通文本表达，而是输出：

- `FunctionCall`
- `CustomToolCall`
- `LocalShellCall`
- `ToolSearchCall`

这就是 Codex 的关键边界：

- **代码建议是文本**
- **代码执行/文件变更/命令执行是工具调用**

这个边界非常重要，也非常工程化。

---

# 27. LLM 输出后，如何解析？如何调用工具？是否支持并行工具调用？

这部分是“协议 -> 运行时动作”的桥梁。

## 27.1 总体流程

完整流程大致是：

```text
SSE stream
  -> process_sse()
  -> process_responses_event()
  -> ResponseEvent
  -> try_run_sampling_request() 逐事件处理
  -> 对 OutputItemDone 调用 ToolRouter::build_tool_call()
  -> 若是工具调用则交给 ToolCallRuntime
  -> 得到 ResponseInputItem tool output
  -> 写回 conversation history
  -> needs_follow_up = true
  -> 继续下一轮模型推理
```

## 27.2 第一步：SSE 解析

`codex-api/src/sse/responses.rs` 中：

- `process_sse()` 读取 SSE 流
- 逐条反序列化为 `ResponsesStreamEvent`
- 再由 `process_responses_event()` 映射为 `ResponseEvent`

这个阶段处理的关键事情：

- 事件类型分流
- 错误归一化
- `response.failed` -> `ApiError`
- `response.completed` -> token usage / response id
- `output_item.added/done` -> `ResponseItem`

## 27.3 第二步：流式事件进入主 loop

`try_run_sampling_request()` 是 authoritative handler。

它维护：

- `active_item`
- `in_flight` 工具 future 队列
- `needs_follow_up`
- `last_agent_message`
- `assistant_message_stream_parsers`
- `plan_mode_state`

也就是说主 loop 不是拿到“最终回答”才处理，而是边流边处理。

## 27.4 第三步：普通输出的解析

### `OutputItemAdded`

如果是普通非工具 item：

- `handle_non_tool_response_item(...)`
- 生成 `TurnItem`
- 对 agent message 建立 active item
- 若有初始文本，seed parser

### `OutputTextDelta`

如果当前 active item 是 agent message：

- 进入 `AssistantMessageStreamParsers`
- 再转成 `AgentMessageContentDelta` 或 plan delta

### `OutputItemDone`

当 item 完成时：

- flush parser
- 持久化 completed response item
- 生成 completed turn item

## 27.5 第四步：工具调用解析

这里关键函数是：

- `ToolRouter::build_tool_call(session, item)`

它会根据 `ResponseItem` 类型判断是否是工具调用：

- `FunctionCall`
- `ToolSearchCall`
- `CustomToolCall`
- `LocalShellCall`

并转换成内部统一结构：

```text
ToolCall {
  tool_name,
  tool_namespace,
  call_id,
  payload,
}
```

其中 `payload` 可能是：

- `Function { arguments }`
- `Mcp { server, tool, raw_arguments }`
- `ToolSearch { arguments }`
- `Custom { input }`
- `LocalShell { params }`

### 为什么这一步重要

因为它把 provider/model 的原始输出协议，转换成了 Codex 自己的统一工具调用模型。

这一步之后，下游 handler 不需要关心是 OpenAI Responses API 还是别的 provider 细节。

## 27.6 第五步：工具执行

在 `handle_output_item_done()` 里：

- 若 `build_tool_call()` 返回 `Some(call)`
- 立即记录 completed response item
- 构造 `tool_future = tool_runtime.handle_tool_call(call, cancellation_token)`
- `needs_follow_up = true`
- 把 future 放进 `in_flight`

这说明工具调用并不是同步阻塞到整个 loop 结束，而是：

- 先排队
- 后续统一 drain

## 27.7 第六步：工具输出如何回灌模型

工具执行完成后会返回 `ResponseInputItem`，常见类型有：

- `FunctionCallOutput`
- `McpToolCallOutput`
- `CustomToolCallOutput`
- `ToolSearchOutput`

随后在 `drain_in_flight()` 中：

- `sess.record_conversation_items(&turn_context, &[response_input.into()]).await`

也就是说，工具结果不会只是 UI 日志，而是**正式写回会话 history**，成为下一次 prompt 的输入。

这就是 ReAct loop 能闭环的关键。

## 27.8 工具错误如何反馈

`handle_output_item_done()` 里对工具解析/执行错误有三类处理：

### `MissingLocalShellCallId`

- 生成一个 `FunctionCallOutput` 错误结果回给模型
- `needs_follow_up = true`

### `RespondToModel(message)`

- 把错误信息包装成 `FunctionCallOutput`
- 写回 conversation history
- 让模型继续决定下一步

### `Fatal(message)`

- 直接终止 turn

这表明 Codex 偏向：

- 能恢复的错误尽量返回给模型
- 只有真正 fatal 的错误才杀掉 turn

## 27.9 是否支持并行工具调用？

**支持，但受模型能力和工具能力双重约束。**

### 模型层支持

`build_prompt()` 会设置：

- `parallel_tool_calls: turn_context.model_info.supports_parallel_tool_calls`

并且 compact 请求也会把这个字段传给 API。

这说明并行工具调用首先是一个**模型能力开关**。

### 工具层支持

`ToolRouter::tool_supports_parallel(tool_name)` 会检查：

- `ConfiguredToolSpec.supports_parallel_tool_calls`

这说明不是所有工具都允许并行执行。

### 运行时层实现

`try_run_sampling_request()` 中维护：

- `FuturesOrdered<BoxFuture<'static, CodexResult<ResponseInputItem>>> in_flight`

当多个 tool call 出现时，可以把多个 future 放入 `in_flight`。

这意味着运行时具备：

- 挂起多个 in-flight 工具任务
- 之后统一 drain

所以并行能力至少在架构上是存在的。

## 27.10 并行工具调用在工程上意味着什么

这并不是简单“多线程调用几个函数”，而是要解决：

- 哪些工具允许并行
- 哪些工具会争抢资源
- 如何维持 tool output 与 `call_id` 的关联
- 多个工具输出完成后如何稳定回灌 history
- turn diff 在多工具下如何聚合

Codex 的解法是：

- `call_id` 贯穿 tool call 与 output
- `ToolCallRuntime` 统一封装运行时
- `SharedTurnDiffTracker` 聚合变更
- `in_flight` 队列统一回收结果

## 27.11 并行不代表完全无约束

要注意：

- prompt 告诉模型“可以并行调用工具”，不代表模型一定会这么做
- 某些工具虽然模型支持并行，但 spec 未必支持
- 某些 mode 下 model-visible tools 还会被过滤

因此并行工具调用的真实约束是：

```text
Can parallelize = model supports parallel
               AND tool spec allows parallel
               AND current mode exposes the tool
               AND runtime/policy does not block it
```

## 27.12 整个输出解析与工具执行链的核心价值

这一整条链路最关键的价值是：

- **模型输出是结构化的，不靠 fragile 文本正则判断**
- **工具调用先统一解析为内部 `ToolCall`，再统一 dispatch**
- **工具结果回写为 `ResponseInputItem`，形成闭环**
- **文本输出、reasoning 输出、plan 输出、tool 输出都能走统一事件系统**
- **并行能力是显式建模的，不是偶然副作用**

这也是 Codex 相比很多“聊天机器人外接工具脚本”的本质差异。

---

# 28. 对你新增四个问题的最终浓缩回答

## 28.1 代码如何检索、索引、决定哪些上下文进入 prompt？

- **先做路径级索引与召回，再做文本级定位，再精读关键片段，最后把结果转成结构化上下文 item 注入 prompt。**
- **真正决定进入 prompt 的是 `ResponseItem` / `ResponseInputItem` 体系、`ContextManager` 的白名单保留、以及 `build_settings_update_items()` 的增量上下文注入。**

## 28.2 是否包含 VS Code 插件？

- **包含 VS Code 集成支持，但这里更准确地说是包含 VS Code 扩展所依赖的 app-server 与 protocol。**
- **扩展通过 JSON-RPC 2.0 与 `codex app-server` 通信，后者驱动线程、turn、item、diff、审批、skills、fs、config 等能力。**

## 28.3 LLM 输出格式是什么？

- **核心输出格式是 `ResponseItem`，包括 message、reasoning、function_call、local_shell_call、custom_tool_call、tool_search_call、web_search_call、image_generation_call 等。**
- **plan mode 会把输出文本进一步拆为普通消息与 plan 片段；code mode 更主要改变工具编排与可见性，而不是完全更换输出协议。**

## 28.4 输出后如何解析、调用工具、支持并行吗？

- **SSE -> `ResponseEvent` -> `try_run_sampling_request()` -> `ToolRouter::build_tool_call()` -> `ToolCallRuntime` -> `ResponseInputItem` 回灌 history。**
- **支持并行工具调用，但前提是模型支持 parallel tool calls，且工具 spec 也声明支持。**
