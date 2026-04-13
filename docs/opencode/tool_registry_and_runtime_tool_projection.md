# Tool Registry / Runtime Tool Projection 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的工具注册与运行时投影链路。

核心问题是：

- `Tool.define()` 为所有工具统一了哪些行为
- builtin tools、plugin tools、目录扫描 custom tools 如何汇入同一 `ToolRegistry`
- 为什么 tool 不只是静态描述，而要在运行时 `init({ agent })`
- provider/model 不同时，为什么会切换 `apply_patch` 与 `edit/write`
- tool 如何在 registry、session prompt、LLM stream 三层逐步过滤与投影

核心源码包括：

- `packages/opencode/src/tool/tool.ts`
- `packages/opencode/src/tool/registry.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/llm.ts`
- `packages/opencode/src/permission/next.ts`

这一层本质上是 OpenCode 的**工具定义规范、工具目录聚合与面向模型的运行时 tool surface 投影基础设施**。

---

# 2. 为什么工具系统不能只是一个静态 JSON 列表

在 OpenCode 里，一个工具真正可用与否，取决于很多运行时因素：

- 当前 agent 权限
- 当前 session 局部禁用/允许
- 当前 provider/model 能力与提示格式
- 当前客户端类型
- feature flags
- 插件动态定义
- 工具自身初始化时需要读取环境/配置

所以工具系统必须是：

- 动态初始化
- 分层过滤
- 运行时投影

而不是一份固定 schema。

---

# 3. `Tool.Info`：工具的最小标准接口

每个工具都被抽象为：

- `id`
- `init(ctx?) => { description, parameters, execute, formatValidationError? }`

这说明工具定义被拆成两层：

## 3.1 静态层

- 工具 ID
- 初始化入口

## 3.2 运行时层

- 当前 agent 环境下的 description / schema / execute 逻辑

这就是为什么工具必须先 `init()` 才能真正投给模型。

---

# 4. `Tool.Context`：运行时工具能拿到什么

工具执行时的 context 包括：

- `sessionID`
- `messageID`
- `agent`
- `abort`
- `callID?`
- `extra?`
- `messages`
- `metadata(...)`
- `ask(...)`

## 4.1 含义

OpenCode 的工具不是纯函数，它们是：

- 会话感知的运行时执行单元

既能：

- 读取历史
- 更新 metadata
- 申请权限
- 响应 abort

因此工具系统天然适合 agentic runtime，而不只是 RPC 函数目录。

---

# 5. `Tool.define()`：所有工具的统一包装器

`Tool.define(id, init)` 最关键做了三件事：

## 5.1 参数校验

调用 `toolInfo.parameters.parse(args)`。

如果失败：

- 若工具提供 `formatValidationError`，用它生成更友好错误
- 否则生成统一的 invalid arguments 错误

## 5.2 执行实际逻辑

- `const result = await execute(args, ctx)`

## 5.3 统一输出截断

- 若 `result.metadata.truncated !== undefined`，认为工具自己已处理截断
- 否则 `Truncate.output(result.output, {}, initCtx?.agent)`
- 回写：
  - `output`
  - `metadata.truncated`
  - `metadata.outputPath?`

## 5.4 含义

这意味着所有工具自动共享：

- 参数校验规范
- 错误格式规范
- 大输出截断规范

这是非常重要的基础抽象。

---

# 6. 为什么截断逻辑放在 `Tool.define()` 而不是每个工具自己处理

如果每个工具各写一套：

- 行为不一致
- 元数据格式不统一
- 很容易漏掉超大输出保护

统一放在包装层后：

- 大多数工具只关心业务逻辑
- 极少数工具若要自管截断，再通过 `metadata.truncated` opt out

这很干净。

---

# 7. `ToolRegistry.state()`：工具目录并不只含 builtin

registry 初始化时会收集两类 custom 工具：

## 7.1 配置目录扫描的本地 tools

- 扫描 `Config.directories()` 下的 `{tool,tools}/*.{js,ts}`
- 通过文件名作为 namespace
- `default` 导出映射到 namespace
- 其他导出映射到 `namespace_id`

## 7.2 插件暴露的 tools

- `Plugin.list()`
- 遍历 `plugin.tool`
- 转成统一 `Tool.Info`

这说明工具系统支持：

- builtin
- repo-local custom tools
- plugin-provided tools

三条来源并存。

---

# 8. `fromPlugin()`：插件工具如何适配到本地工具协议

它会把插件的 `ToolDefinition` 转成 `Tool.Info`：

- `parameters = z.object(def.args)`
- `description = def.description`
- `execute(args, ctx)` 中注入：
  - `directory`
  - `worktree`

再对插件输出做 `Truncate.output(...)`。

## 8.1 含义

插件工具不需要理解 OpenCode 全部内部实现，只要遵守较轻量的 plugin tool 协议，就能被适配进统一 registry。

这大大降低了扩展面成本。

---

# 9. `register()`：运行时动态注册

`ToolRegistry.register(tool)` 会把工具插入或替换 `custom` 列表中的同 ID 项。

说明 registry 不只是启动期构建，也支持：

- 运行时动态扩展或覆写工具

这对测试、插件热加载或运行时注入都很有价值。

---

# 10. builtin tool 集合是有条件构成的

`all()` 返回的 builtin + custom 工具列表里包含大量条件逻辑。

核心 builtin 包括：

- `invalid`
- `question`（条件开启）
- `bash`
- `read`
- `glob`
- `grep`
- `edit`
- `write`
- `task`
- `webfetch`
- `todowrite`
- `websearch`
- `codesearch`
- `skill`
- `apply_patch`
- `lsp`（实验 flag）
- `batch`（实验配置）
- `plan_exit`（plan mode + cli）
- `custom`

## 10.1 含义

工具目录本身就已经是：

- client-aware
- flag-aware
- config-aware

并不是所有环境都暴露同一套工具。

---

# 11. 为什么 `question` tool 要按客户端条件开启

只有当：

- `OPENCODE_CLIENT in [app, cli, desktop]`
- 或显式 enable flag

才会把 `QuestionTool` 放进 registry。

## 11.1 含义

某些客户端可能没有合适的 question UI，因此工具层直接不暴露它，避免模型调用一个无交互承载能力的功能。

这体现了 runtime surface 必须与宿主能力一致。

---

# 12. `tools(model, agent?)`：registry 到运行时工具定义的投影

`ToolRegistry.tools(...)` 是关键桥梁。

它会：

1. 取 `all()`
2. 按 model/provider 过滤一轮
3. 对每个工具 `init({ agent })`
4. 触发 `Plugin.trigger("tool.definition", { toolID }, output)`
5. 返回最终 `{ id, description, parameters, execute }`

也就是说：

- registry 产出的是“已初始化可执行工具集”

而不只是 ID 列表。

---

# 13. provider/model 特化：`apply_patch` vs `edit/write`

在 `ToolRegistry.tools(...)` 中：

- 若 modelID 包含 `gpt-` 且不是 `oss`、不是 `gpt-4`
- 则 `usePatch = true`
- `apply_patch` 开启
- `edit/write` 关闭

否则：

- `apply_patch` 关闭
- `edit/write` 开启

## 13.1 含义

工具 surface 会随模型家族变化。

这说明系统承认：

- 某些模型更适合 Codex 风格 patch editing
- 另一些模型更适合 file write/edit primitives

这是非常现实的 provider-aware tool projection。

---

# 14. `codesearch` / `websearch` 的 provider/flag 过滤

这两类工具只有在：

- `model.providerID === opencode`
- 或 `OPENCODE_ENABLE_EXA`

时才启用。

## 14.1 含义

外部搜索工具依赖特定 backend 能力或 feature flag，因此 registry 层直接裁掉，避免模型看到不可运行的搜索能力。

---

# 15. `tool.definition` 插件钩子

在每个工具完成 `init()` 后，registry 会触发：

- `Plugin.trigger("tool.definition", { toolID }, output)`

其中 `output` 可变，包含：

- `description`
- `parameters`

## 15.1 含义

插件不仅能新增工具，也能修改现有工具的定义面，包括：

- 描述文案
- 参数 schema

这是运行时 tool prompt surface 的重要扩展点。

---

# 16. `SessionPrompt.resolveTools()`：第二层过滤

registry 给出已初始化工具后，`SessionPrompt.resolveTools(...)` 还会再做一层 session/agent 级投影。

关键输入包括：

- `agent`
- `model`
- `session`
- `tools` override
- `processor`
- `bypassAgentCheck`
- `messages`

这里会构造真正传给 `ai` SDK 的 tool set 与 `Tool.Context`。

---

# 17. 为什么还需要第二层过滤

因为 registry 只解决：

- 当前环境中“有哪些工具理论上可初始化”

但还没解决：

- 当前 user message 是否关闭某个工具
- 当前 agent/session 权限是否禁用
- 工具执行时如何把 metadata/ask/abort/memory 接进当前 assistant message

这些都属于当前轮会话语境，因此必须在 `SessionPrompt.resolveTools()` 再处理一次。

---

# 18. `Tool.Context.ask()`：工具权限并不是工具自己实现

在 `SessionPrompt.resolveTools()` 里，每个工具的 `ctx.ask()` 最终都会走：

- `PermissionNext.ask({ ..., ruleset: PermissionNext.merge(agent.permission, session.permission ?? []) })`

## 18.1 含义

工具不负责知道自己能不能执行，它只负责：

- 在需要时声明要申请哪类 permission

真正的授权决策由统一权限系统处理。

这让工具实现更纯粹，也更一致。

---

# 19. `ctx.metadata()`：工具执行中的增量状态更新桥

同样在 `SessionPrompt.resolveTools()` 中，会把 `metadata(...)` 接到当前 tool part 更新逻辑上。

因此工具在执行过程中可以不断上报：

- 标题
- 局部 metadata
- 中间输出摘要

这正是像 `bash` 工具那样流式更新 output preview 的基础。

---

# 20. `LLM.resolveTools()`：第三层过滤，真正投给模型前再裁一轮

在 `session/llm.ts` 里还有一个 `resolveTools(input)`，它会：

- `PermissionNext.disabled(Object.keys(input.tools), input.agent.permission)`
- 若 `input.user.tools?.[tool] === false` 或 disabled.has(tool)
  - 从 `input.tools` 删除

## 20.1 含义

即使工具已经进入当前轮运行时字典，真正给模型前仍会再按：

- 用户级 tool toggle
- agent 默认禁用

做最后裁剪。

这形成了非常稳的三层过滤：

- registry 层
- session runtime 层
- llm projection 层

---

# 21. `PermissionNext.disabled()`：为什么是“暴露面裁剪”而非执行期拒绝

它会把 `pattern=* && action=deny` 的整类 permission 映射成禁用工具集。

这意味着：

- 如果某工具对当前 agent 明确不可用，模型压根看不到它

而不是让模型反复调用后再被拒绝。

这对模型行为质量非常重要。

---

# 22. `invalid` tool：为什么要始终存在

registry 中 `InvalidTool` 被放在首位，LLM 层在 repair 失败时也会把坏 tool call 改成：

- `toolName: "invalid"`

## 22.1 含义

这提供了一个安全兜底：

- provider 若产生不存在/拼错的 tool call
- runtime 仍有一个合法目标来承接错误信息

从而避免整个对话协议因坏 tool call 崩掉。

---

# 23. `activeTools` 为什么排除 `invalid`

在 `LLM.stream()` 中：

- `activeTools = Object.keys(tools).filter((x) => x !== "invalid")`

说明：

- `invalid` 是协议修复用保底工具
- 不是希望模型正常主动选择的工具

这也是一个细节很强的实现。

---

# 24. `toolChoice` 与 tools 的关系

`LLM.stream()` 会把：

- `tools`
- `activeTools`
- `toolChoice`

一起传给 `streamText()`。

这说明工具 surface 不仅仅是“暴露哪些工具”，还包括：

- 是否要求必须用工具
- 是否允许自动选择
- 是否完全不用工具

因此 tool projection 与 prompting strategy 强耦合。

---

# 25. 大输出截断与 agent 能力的联动

`Truncate.output(...)` 会依据 agent 是否拥有 task 能力，决定给模型什么后续建议：

- 有 `task` -> 建议把大文件委派给 explore agent 处理
- 没有 `task` -> 建议自己用 grep/read offset 读取

## 25.1 含义

工具输出截断并非单纯文本截断，而是会根据当前 agent 可用能力，生成不同续跑策略提示。

这说明 tool runtime surface 与 orchestration strategy 是联动的。

---

# 26. 一个完整的工具投影数据流

可以概括为：

## 26.1 定义工具

- `Tool.define()` 统一包装

## 26.2 聚合工具目录

- builtin
- local custom files
- plugin tools

## 26.3 运行时初始化

- `ToolRegistry.tools(model, agent)`
- tool-specific `init({ agent })`
- plugin `tool.definition` 改写

## 26.4 session 级绑定

- `SessionPrompt.resolveTools()`
- 注入 `metadata` / `ask` / `abort` / `messages`

## 26.5 LLM 前最终裁剪

- `LLM.resolveTools()`
- 去掉 disabled/user-disabled tools
- 生成 `tools + activeTools`

## 26.6 provider stream 执行

- tool call 进入 processor state machine

这就是 OpenCode 的完整 tool surface pipeline。

---

# 27. 这个模块背后的关键设计原则

## 27.1 工具定义应统一拥有参数校验与输出截断包装

所以有 `Tool.define()`。

## 27.2 工具来源必须可扩展，但执行协议必须统一

所以 builtin/custom/plugin 最终都被收束成 `Tool.Info`。

## 27.3 工具暴露面必须由环境、agent、session、provider 多层共同决定

所以有 registry -> session -> llm 三层投影。

## 27.4 模型不应看到它明确不能用的工具

所以在给 provider 之前会做 disabled/user toggle 过滤。

---

# 28. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/tool/tool.ts`
2. `packages/opencode/src/tool/registry.ts`
3. `packages/opencode/src/session/prompt.ts` 中 `resolveTools()`
4. `packages/opencode/src/session/llm.ts` 中 `resolveTools()`
5. `packages/opencode/src/permission/next.ts`
6. `packages/opencode/src/tool/truncation.ts`

重点盯住这些函数/概念：

- `Tool.define()`
- `ToolRegistry.tools()`
- `fromPlugin()`
- `tool.definition`
- `PermissionNext.disabled()`
- `SessionPrompt.resolveTools()`
- `LLM.resolveTools()`
- `invalid`
- `activeTools`

---

# 29. 下一步还需要深挖的问题

这一篇已经把 tool registry 与 runtime projection 主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`SessionPrompt.resolveTools()` 的完整实现细节还值得单独精读，尤其 `ai.tool(...)` 适配层
- **问题 2**：plugin/local custom tools 的错误边界与沙箱能力还值得继续评估
- **问题 3**：`invalid` tool 的具体实现及它如何向模型反馈错误，还值得继续拆读
- **问题 4**：`tool.definition` 插件改写 schema 后，与 provider schema compatibility transform 如何联动，还值得继续追踪
- **问题 5**：不同 provider 对 tool schema 和 tool choice 的兼容性差异，还可以继续结合 `provider/transform.ts` 深挖
- **问题 6**：tool output truncation 与 snapshot/revert/summary 的交互边界还值得继续观察
- **问题 7**：`TodoReadTool` 当前被注释掉，背后设计取舍还值得继续梳理
- **问题 8**：目录扫描 custom tools 与 plugin tools 的命名冲突/优先级策略还值得继续确认

---

# 30. 小结

`tool_registry_and_runtime_tool_projection` 模块定义了 OpenCode 如何把多来源工具收束成统一协议，并按当前模型、agent、session 与权限上下文动态投影给 LLM：

- `Tool.define()` 统一了参数校验与输出截断
- `ToolRegistry` 聚合 builtin、本地自定义与插件工具
- `SessionPrompt.resolveTools()` 把工具绑定到当前会话执行上下文
- `LLM.resolveTools()` 则在最终 provider 调用前再做禁用与用户 toggle 过滤

因此，这一层不是简单的工具清单，而是 OpenCode 让模型真正看到“此刻可用且可执行的工具能力面”的核心运行时基础设施。
