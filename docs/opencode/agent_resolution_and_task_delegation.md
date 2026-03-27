# Agent Resolution / Task Delegation 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 agent 解析与 task/subagent 委派链路。

核心问题是：

- agent 是如何定义、合并、默认选择与隐藏控制的
- `build / plan / general / explore / compaction / title / summary` 这些内建 agent 各自承担什么职责
- 用户显式 `@agent` 为什么会变成 `agent part + synthetic text`
- `subtask` part 与 `task` 工具如何协同形成子代理调用链
- 为什么 subagent 本质上是一个新的子 session，而不是当前 session 内简单切 persona

核心源码包括：

- `packages/opencode/src/agent/agent.ts`
- `packages/opencode/src/tool/task.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/message-v2.ts`
- `packages/opencode/src/session/system.ts`

这一层本质上是 OpenCode 的**代理角色解析、权限切片与子会话委派基础设施**。

---

# 2. 为什么 agent 系统不能只是一个字符串标签

在 OpenCode 里，agent 不只是 persona 名称。

它还决定：

- 默认 model
- reasoning / sampling options
- permission ruleset
- prompt 模板
- 可见性（hidden）
- mode（primary / subagent / all）
- 最大步骤数
- color / description

因此 agent 实际上是一个：

- **执行配置包**

而不仅是 UI 昵称。

---

# 3. `Agent.Info`：agent 的完整配置对象

`Agent.Info` 中最关键的字段包括：

- `name`
- `description`
- `mode`
- `native`
- `hidden`
- `topP`
- `temperature`
- `permission`
- `model`
- `variant`
- `prompt`
- `options`
- `steps`

## 3.1 含义

一次 agent 选择，等于同时选择了：

- prompt contract
- tool capability边界
- provider/model 参数偏好
- 运行方式

这就是为什么 agent resolution 是 session runtime 的核心环节。

---

# 4. `Agent.state()`：agent 集合如何生成

agent 列表不是硬编码常量，而是由：

- 内建 defaults
- `Config.get()` 读取的用户配置
- skill 相关 whitelist 路径
- permission merge

共同构造。

这意味着 agent 系统天然支持：

- 内建 agent
- 用户扩展 agent
- 覆盖默认 agent 行为
- 禁用现有 agent

---

# 5. 默认 permission 基线非常关键

`Agent.state()` 一开始先构建 `defaults = PermissionNext.fromConfig({...})`。

这个默认权限基线包括：

- `* = allow`
- `doom_loop = ask`
- `external_directory` 默认 ask，但对 truncate/skills 目录 allow
- `question = deny`
- `plan_enter = deny`
- `plan_exit = deny`
- `read` 默认 allow，但 `.env` 文件 ask

## 5.1 意义

所有 agent 并不是从零权限开始，而是从一个统一安全基线开始，再叠加差异化规则。

这让 agent 设计更一致，也避免遗漏关键安全规则。

---

# 6. 内建 primary agents

## 6.1 `build`

- 默认主 agent
- 描述为默认执行 agent
- 允许 `question` 与 `plan_enter`
- `mode = primary`

## 6.2 `plan`

- 计划模式 agent
- 核心特点是禁止 edit 工具，除了 plan 文件路径白名单
- 允许 `question` 与 `plan_exit`
- `mode = primary`

## 6.3 `compaction`

- 隐藏 agent
- prompt = compaction 专用模板
- `* = deny`
- `mode = primary`

## 6.4 `title` / `summary`

- 都是隐藏 primary agent
- 用于系统内部辅助生成 title / summary
- 基本禁用全部工具

这说明 primary agent 既包含面向用户的主要执行者，也包含系统内部专用 agent。

---

# 7. 内建 subagents

## 7.1 `general`

- 通用型 subagent
- 用于研究复杂问题、多步任务
- 禁止 todo 读写

## 7.2 `explore`

- 代码库探索型 subagent
- 大部分工具 deny，仅允许：
  - `grep`
  - `glob`
  - `list`
  - `bash`
  - `webfetch`
  - `websearch`
  - `codesearch`
  - `read`
- 强调代码探索职责

这说明 subagent 模式不是缩小版 primary agent，而是被设计成强职责分离的专业角色。

---

# 8. `mode` 的语义

agent 的 `mode` 有：

- `primary`
- `subagent`
- `all`

## 8.1 `primary`

适合直接作为当前 session 主执行者。

## 8.2 `subagent`

适合作为 `task` 工具委派目标。

## 8.3 `all`

表示同时可被两类场景使用。

这说明 OpenCode 在架构上明确区分：

- 主会话 agent
- 被委托执行的子 agent

---

# 9. 用户配置如何覆盖内建 agent

在 `cfg.agent` 遍历中：

- 可通过 `disable` 删除内建 agent
- 不存在的 key 会创建新 agent
- `model / variant / prompt / description / temperature / topP / mode / color / hidden / steps / options / permission` 都可覆盖

这说明 agent 系统非常可定制，而且不是只允许改 prompt。

---

# 10. `defaultAgent()`：默认 agent 如何选

优先级是：

1. 若 `cfg.default_agent` 存在：
   - 必须存在
   - 不能是 `subagent`
   - 不能 hidden
2. 否则取第一个可见的 non-subagent agent

## 10.1 含义

系统不允许把 hidden 或 subagent 配成默认主 agent，这保护了主会话语义的清晰性。

---

# 11. `Agent.get()` / `Agent.list()`：agent 是 prompt/runtime 的公共依赖

这些 API 被多处使用：

- `createUserMessage()` 解析当前 agent
- `SessionPrompt.loop()` 决定 normal turn 用哪个 agent
- `task` 工具解析 `subagent_type`
- doom loop ask 取当前 assistant agent permission
- title/summary/compaction 等系统流程也要拿 agent

这说明 agent 不是 UI 配置层概念，而是整个 runtime 的核心索引。

---

# 12. `@agent` 输入是如何进入系统的

在 `resolvePromptParts(template)` / `createUserMessage()` 路径中：

- 若某个标记无法解析为本地文件，但能 `Agent.get(name)` 命中
- 就生成一个 `agent` part

随后在 `createUserMessage()` 的 `agent` 分支中，会再追加 synthetic text：

- `Use the above message and context to generate a prompt and call the task tool with subagent: <name>`

## 12.1 这说明什么

用户的 `@agent` 显式指定不会只体现在 metadata 上，而会被转换成：

- 结构化 `agent` part
- 明示性 task delegation 指令

这是一种非常直接有效的 delegation bridge。

---

# 13. 为什么 `@agent` 会设置 `bypassAgentCheck`

在 `SessionPrompt.loop()` 正常流程里，会检查最近 user message 是否包含 `agent` part：

- `bypassAgentCheck = lastUserMsg?.parts.some((p) => p.type === "agent") ?? false`

然后把这个标记一路传到：

- `resolveTools()`
- `Tool.Context.extra.bypassAgentCheck`
- 最终 `TaskTool.execute()`

## 13.1 含义

如果 subagent 是用户显式点名的，系统会跳过某些默认 task permission 审批。

这体现了一个重要原则：

- 用户显式 delegation 的优先级高于 agent 自发 delegation

---

# 14. `SubtaskPart`：子任务意图的正式表示

`MessageV2.SubtaskPart` 包含：

- `prompt`
- `description`
- `agent`
- `model?`
- `command?`

这说明 subtask 并不是“tool 参数暂存”，而是消息历史中的正式控制对象。

因此 loop 可以扫描历史 parts，识别还有哪些未处理的 subtask。

---

# 15. `SessionPrompt.loop()` 如何调度 subtask

在 loop 中，会从历史消息里收集：

- 所有 `compaction` 或 `subtask` parts

当没有 `lastFinished` 时，这些 parts 会进 `tasks` 队列。

随后：

- `const task = tasks.pop()`
- 如果 `task?.type === "subtask"`，进入 subtask 执行分支

这说明 subtask 并不是立刻内联执行，而是被 loop 作为待处理控制任务统一调度。

---

# 16. subtask 分支为什么先创建 assistant message + running tool part

在进入 subtask 分支后，系统会：

1. 创建一条 assistant message
2. 再创建一条 `tool` part，状态为 `running`
3. tool 名是 `TaskTool.id`
4. input 中写入：
   - `prompt`
   - `description`
   - `subagent_type`
   - `command`

## 16.1 含义

即使 subtask 逻辑是系统内部 orchestrated，它仍然被表现为一次正式的 tool execution。

这保持了消息历史的一致性：

- 普通工具调用
- 子代理委派

都统一以 tool part 生命周期呈现。

---

# 17. `TaskTool` 的定位

`TaskTool` 本身是一个标准 tool，参数包括：

- `description`
- `prompt`
- `subagent_type`
- `task_id?`
- `command?`

它并不是轻量 helper，而是：

- 子代理会话管理器
- prompt 透传器
- session fork / reuse 控制器

---

# 18. `TaskTool` 如何限制可见 subagents

初始化时它会：

- `Agent.list().filter((a) => a.mode !== "primary")`

即只列出 subagent / all 类型 agent。

再根据 caller agent 的权限做过滤：

- `PermissionNext.evaluate("task", a.name, caller.permission).action !== "deny"`

这说明 task tool 对模型暴露的 subagent 列表本身就是 permission-aware 的。

模型看不到被当前 agent 禁止委派的目标。

---

# 19. `TaskTool.execute()` 的权限逻辑

默认情况下，它会先：

- `ctx.ask({ permission: "task", patterns: [params.subagent_type], ... })`

但若：

- `ctx.extra?.bypassAgentCheck`

就跳过这一步。

## 19.1 意义

这是 task delegation 的核心安全门：

- agent 自发委派 -> 需要 task permission
- 用户显式指定 subagent -> 可跳过默认阻拦

这一点和前面的 `@agent` 逻辑严丝合缝。

---

# 20. subagent 实际上是新的子 session

`TaskTool.execute()` 并不会在当前 session 内切换上下文，而是：

- 若 `task_id` 给定且旧 session 存在，则复用它
- 否则 `Session.create({ parentID: ctx.sessionID, title: ..., permission: [...] })`

## 20.1 为什么这是正确设计

这样每个 subtask 都拥有：

- 独立消息历史
- 独立状态机
- 独立权限集
- 可恢复的 `task_id`

这比在同一 session 内硬切角色清晰太多。

---

# 21. 子 session 权限是如何切片的

创建子 session 时，`TaskTool` 会加一组 permission：

- `todowrite = deny`
- `todoread = deny`
- 若 subagent 自身没有 `task` permission，则再 `task = deny`
- `config.experimental.primary_tools` 里的工具也会 allow 进来

## 21.1 含义

子代理不是简单继承父会话全部能力，而是会做专门权限裁剪，尤其限制：

- todo 工具
- 递归 task delegation

从而避免无限代理套娃和计划污染。

---

# 22. `hasTaskPermission`：为什么还要防递归 task

`TaskTool.execute()` 会看：

- `agent.permission.some(rule => rule.permission === "task")`

若没有，就在子 session 权限里显式 deny `task`。

这说明系统很清楚递归 delegation 会迅速失控，因此默认是：

- 只有明确允许的 subagent 才能再继续派 subtask

---

# 23. 子代理使用哪个模型

优先级是：

- 若目标 agent 自己配置了 `model`，用它
- 否则沿用当前 assistant message 的 `providerID/modelID`

这说明 subagent 既可以有自己专属模型，也可以继承当前主链路模型。

非常灵活。

---

# 24. `ctx.metadata(...)`：父会话中的 task tool part 如何关联到子 session

`TaskTool.execute()` 会给当前 tool 调用写 metadata：

- `title = params.description`
- `metadata.sessionId = session.id`
- `metadata.model = model`

这意味着主会话里的 task tool result 能直接指向对应子 session。

这对：

- UI 跳转
- 恢复 task
- 调试代理树

都非常关键。

---

# 25. 子代理 prompt 如何被构造

`TaskTool.execute()` 会：

1. `SessionPrompt.resolvePromptParts(params.prompt)`
2. 再调用：
   - `SessionPrompt.prompt({ sessionID: subSession.id, model, agent: agent.name, tools: {...}, parts: promptParts })`

这说明子代理并没有旁路 prompt 系统，而是完整复用主 prompt assembly 管线。

因此 subagent 本质上是：

- 在新 session 中重新跑一遍完整 prompt/processor loop

---

# 26. 为什么要给子代理禁用 `todowrite/todoread`

这体现出一个明确设计取舍：

- todo 是主代理/主会话的协调面
- 子代理应专注执行，不应写主流程计划状态

否则多个 subagent 并发时，todo 很容易失真或相互打架。

---

# 27. `task_id`：子代理会话是可恢复的

输出中会显式返回：

- `task_id: <session.id>`

而调用参数也允许传回旧的 `task_id` 继续同一子会话。

这说明 task delegation 不是一次性 fire-and-forget，而是支持：

- 持续恢复
- 增量推进
- 多轮子会话协作

---

# 28. 为什么 subtask command 场景后要补 synthetic user

在 `SessionPrompt.loop()` 的 subtask 分支中，如果 `task.command` 存在，会插一条 synthetic user：

- `Summarize the task tool output above and continue with your task.`

源码注释指出这是为某些 reasoning models 修复 mid-loop assistant-only 历史问题。

这再次说明 task delegation 链路不只是功能逻辑，还包含很多模型兼容性细节。

---

# 29. `Agent.generate(...)`：agent 本身还能被模型生成

`agent.ts` 里还有 `generate(...)`：

- 用 `PROMPT_GENERATE`
- 结合现有 agent 名单
- 让模型生成新的 agent 配置对象

并在 OpenAI oauth/Codex 场景下仍走 `SystemPrompt.instructions()` providerOptions。

这说明 agent 系统甚至支持“由模型辅助生成新的 agent 配置”。

这是非常强的元编程能力。

---

# 30. 一个完整的 agent delegation 数据流

可以概括为：

## 30.1 解析 agent

- `createUserMessage()` 识别 `agent part`
- 或默认 `Agent.defaultAgent()`

## 30.2 主 loop 判定 delegation

- normal turn 中允许模型用 `task` tool
- 或用户显式 `@agent` 触发 bypass route

## 30.3 `TaskTool.execute()`

- 检查 permission
- 解析目标 agent
- 创建/复用子 session
- 写 metadata
- 在子 session 中调用 `SessionPrompt.prompt()`

## 30.4 把子 session 输出包装回主会话

- `task_id`
- `<task_result> ... </task_result>`

这就是 OpenCode 的 subagent orchestration 主链路。

---

# 31. 这个模块背后的关键设计原则

## 31.1 agent 是执行配置包，而不是名字标签

因此 model/permission/prompt/options 都归 agent 管。

## 31.2 子代理应拥有独立 session，而不是污染主会话轨迹

所以 task delegation 通过新 session 承载。

## 31.3 用户显式指定的 delegation 应优先于 agent 自发 delegation 限制

所以有 `bypassAgentCheck`。

## 31.4 递归代理必须被权限边界控制

所以默认 deny nested task，除非明确允许。

---

# 32. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/agent/agent.ts`
2. `packages/opencode/src/tool/task.ts`
3. `packages/opencode/src/session/prompt.ts`
4. `packages/opencode/src/session/message-v2.ts`
5. `packages/opencode/src/session/system.ts`

重点盯住这些函数/概念：

- `Agent.get()`
- `Agent.list()`
- `Agent.defaultAgent()`
- `TaskTool.execute()`
- `SubtaskPart`
- `bypassAgentCheck`
- `task_id`
- `Session.create(parentID: ...)`

---

# 33. 下一步还需要深挖的问题

这一篇已经把 agent 解析与 task 委派主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`TaskTool` 的描述模板 `task.txt` 与模型实际选择 subagent 的 prompt 质量还值得继续观察
- **问题 2**：子 session 与父 session 在 UI 中的关系呈现方式还值得继续追踪 server/routes 与前端实现
- **问题 3**：`config.experimental.primary_tools` 在子代理权限中的确切语义还可继续精读更上层配置文档
- **问题 4**：`Agent.generate()` 生成的新 agent 配置最终如何写回 config，还值得继续查相关写入链路
- **问题 5**：subagent 恢复时复用旧 `task_id` 的上下文是否会导致历史过长，还值得继续结合 compaction 思考
- **问题 6**：用户显式 `@agent` 跳过 task permission 是否还需要更细粒度审计，这点值得继续从安全角度考虑
- **问题 7**：多 subagent 并行执行时，父会话如何聚合多个 task result，还值得继续追踪上层 orchestrator/UI
- **问题 8**：隐藏 primary agent（title/summary/compaction）与显式用户 agent 列表之间的边界还值得继续整理

---

# 34. 小结

`agent_resolution_and_task_delegation` 模块定义了 OpenCode 如何把 agent 从配置概念变成真正的运行时执行单元，并将子任务委派建模为可恢复的子会话：

- `Agent` 命名空间负责 agent 定义、默认选择、权限合并与用户覆盖
- `@agent` 会被转换成 `agent part + synthetic delegation hint`
- `TaskTool` 负责创建/复用子 session，并在其中复用完整 prompt 执行链路
- `SessionPrompt.loop()` 则把 `subtask` 当成正式控制任务统一调度

因此，这一层不是简单的多 persona 机制，而是 OpenCode 多代理协作、权限隔离与可恢复子任务编排的核心基础设施。
