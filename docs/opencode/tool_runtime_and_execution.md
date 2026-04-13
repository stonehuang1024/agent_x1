# Tool Runtime / Execution 模块详细解读

---

# 1. 模块定位

这个模块负责回答 OpenCode 里最关键的一个工程问题：

- 模型发出 tool call 之后，系统到底如何把这个调用变成真实可执行动作？

对应源码主要包括：

- `packages/opencode/src/tool/tool.ts`
- `packages/opencode/src/tool/registry.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/permission/next.ts`
- `packages/opencode/src/tool/batch.ts`
- `packages/opencode/src/tool/task.ts`
- `packages/opencode/src/tool/skill.ts`
- `packages/opencode/src/server/routes/mcp.ts`

如果说 `session_loop_prompt_context` 模块是总控中枢，那么这个模块就是 OpenCode 的**执行层与能力层总线**。

它解决的是：

- 工具是如何定义的
- 工具是如何注册和暴露给模型的
- 工具调用参数如何校验
- 工具执行时如何拿到 session/runtime 上下文
- 权限系统如何拦截和放行
- 工具输出如何截断、持久化、回流
- batch 并行执行如何实现
- subtask 为什么也被做成工具
- MCP 外部工具如何合流到统一运行时

---

# 2. 模块边界与职责分工

## 2.1 `tool/tool.ts`

这是工具抽象的最底层定义。

它规定了：

- 一个 tool 至少要有 `id`
- 要有 `init()`
- `init()` 返回：
  - `description`
  - `parameters`
  - `execute()`
  - 可选 `formatValidationError()`

同时，它把所有工具的执行都包进统一的包装器里，负责：

- 参数 schema 校验
- 统一错误文案
- 输出截断

可以把它看成 OpenCode 的**tool ABI**。

## 2.2 `tool/registry.ts`

这是工具目录与注册中心。

它负责：

- 收集内置工具
- 收集配置目录中的自定义工具
- 收集插件提供的工具
- 依据 provider/model/flag 过滤工具
- 初始化工具定义
- 向插件暴露 tool definition hook

它是 **tool discovery + tool assembly layer**。

## 2.3 `session/prompt.ts`

它不只是 loop 调度器，同时也是**工具解析与运行时装配器**。

最关键的函数是：

- `resolveTools()`

这个函数会把 registry 中的工具变成模型真正可调用的 AI SDK tools，并注入统一的 `Tool.Context`。

## 2.4 `permission/next.ts`

这是工具执行前的权限裁决器。

它负责：

- 规则集解析
- allow / deny / ask 判定
- pending approval 管理
- reply 后传播到对应 session
- disabled tool 过滤

它是 **tool execution firewall**。

## 2.5 `tool/batch.ts`

这是并行执行器。

它不是工具生态边缘补丁，而是一个正式工具，用来让模型显式发起多工具并行调用。

## 2.6 `tool/task.ts`

这是 subagent orchestration tool。

它把“创建/复用子任务 session，并让子 agent 在其中执行”抽象成一个 tool。

这说明 OpenCode 把“任务分治”也纳入工具系统，而不是单独搞一套 orchestrator 协议。

## 2.7 `tool/skill.ts`

这是按需加载 domain-specific workflow 的工具。

它本质上是“把 skill 内容注入到上下文”的桥接工具。

## 2.8 MCP 路由

`server/routes/mcp.ts` 不是工具实现本身，但它揭示了 OpenCode 对 MCP 的系统级支持：

- 动态添加 MCP server
- 认证
- 连接/断开
- 状态查询

这说明 MCP 不是临时外挂，而是 OpenCode 的正式外部工具接入面。

---

# 3. 工具抽象层：`Tool.define()` 的设计原理

## 3.1 为什么所有工具都要先经过 `Tool.define()`

`tool/tool.ts` 里的 `Tool.define()` 做了一件非常关键的事：

- 它把“工具作者写的 execute()”包裹成一个统一规范执行器

这个包装器会在调用真实 execute 前后插入统一行为。

所以 tool author 只需要关注：

- 这个工具要做什么
- 参数是什么
- 结果怎么表达

而系统性问题，比如：

- 参数校验
- 错误提示
- 长输出截断

由 `Tool.define()` 统一接管。

这是典型的 **framework inversion**：

- 工具开发者写业务逻辑
- runtime 负责约束执行协议

## 3.2 参数校验算法

每个工具都有一个 `zod` schema：

- `parameters.parse(args)`

如果失败：

- 优先调用 `formatValidationError(error)`
- 否则生成统一错误：
  - `The <tool> tool was called with invalid arguments...`

这是一种简单而有效的 **schema-first validation** 机制。

优点：

- 校验逻辑在工具定义处声明
- 错误消息可以按工具定制
- runtime 无需为每个工具写手工参数检查

## 3.3 输出截断机制

`Tool.define()` 在 execute 完成后，会自动调用：

- `Truncate.output(result.output, {}, initCtx?.agent)`

除非工具已经自己在 metadata 中声明：

- `truncated !== undefined`

这说明输出截断是默认行为，不是可选优化。

设计原理是：

- 工具输出是模型下一轮上下文的重要组成部分
- 如果不统一做截断，长输出会迅速吞掉 context window

这是一个 **post-execution output budget control** 机制。

---

# 4. 工具注册中心：`ToolRegistry`

## 4.1 `ToolRegistry.state()` 的注册来源

registry 的 state 会汇总两类自定义来源：

- **配置目录中的自定义工具文件**
  - `Config.directories()`
  - 扫描 `{tool,tools}/*.{js,ts}`
- **插件提供的工具**
  - `Plugin.list()`
  - 读取 `plugin.tool`

然后通过 `fromPlugin()` 变成统一 `Tool.Info`。

这意味着 OpenCode 的工具系统不是硬编码一组内置工具，而是天然支持：

- repo 级定制工具
- 插件级扩展工具

这是一个典型的 **multi-source tool registry**。

## 4.2 `fromPlugin()` 的适配逻辑

插件工具定义格式不等于 runtime 内部工具格式，因此 registry 会做一次转换：

- `def.args` -> `z.object(def.args)`
- 注入 `directory`、`worktree`
- 执行 `def.execute()`
- 再统一做 `Truncate.output()`
- 返回标准 `{ title, output, metadata }`

这里的本质是 **plugin tool normalization**。

OpenCode 不要求插件直接理解全部 runtime 内部协议，但 registry 会把插件结果适配到同一工具返回契约上。

## 4.3 `all()` 的内置工具清单

registry `all()` 会返回：

- `invalid`
- 可选 `question`
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
- 可选 `lsp`
- 可选 `batch`
- 可选 `plan exit`
- `custom`

这里体现两个原则：

- 基础工具常驻
- 高风险/实验/模式专属工具由 flag 与 client 条件控制

## 4.4 provider/model 感知的工具过滤

`ToolRegistry.tools()` 里有几个重要分支：

### `codesearch` / `websearch`

只有在以下条件启用：

- `model.providerID === opencode`
- 或 `Flag.OPENCODE_ENABLE_EXA`

### `apply_patch` vs `edit/write`

如果模型是某类 GPT 系列：

- 启用 `apply_patch`
- 禁用 `edit` / `write`

否则：

- 启用 `edit` / `write`
- 禁用 `apply_patch`

这代表 OpenCode 不是对所有模型暴露同样的修改能力协议，而是根据模型擅长的调用格式选择工具面。

这是一个 **model-specific tool surface selection** 策略。

## 4.5 初始化与插件 hook

`ToolRegistry.tools()` 对每个工具执行：

1. `t.init({ agent })`
2. 产出 `description` 与 `parameters`
3. 触发：
   - `Plugin.trigger("tool.definition", { toolID }, output)`

这说明工具定义不是最终常量，还允许插件在暴露给模型前改写：

- 描述文本
- 参数 schema

所以 registry 是一个真正的**工具装配流水线**，不是静态数组。

---

# 5. `SessionPrompt.resolveTools()`：工具运行时装配

这是工具系统真正接上 agent runtime 的地方。

## 5.1 为什么 registry 返回的还不是最终工具

registry 返回的是内部 `Tool.Info`。

而模型调用时需要的是 AI SDK 的 `tool({...})` 对象。

因此 `resolveTools()` 的作用是：

- 从 registry 获取工具
- 做 provider schema 变换
- 包装 execute
- 注入统一上下文
- 合并 MCP 工具
- 注入结构化输出工具

这一步是 **tool binding**。

## 5.2 `Tool.Context` 的设计

`Tool.Context` 包含：

- `sessionID`
- `messageID`
- `agent`
- `abort`
- `callID`
- `extra`
- `messages`
- `metadata()`
- `ask()`

这里最关键的不是字段本身，而是它的设计哲学：

- 工具不是纯函数
- 工具是运行在会话 runtime 内部的受控执行单元

### `metadata()`

工具执行中途可以调用 `metadata()` 更新：

- `title`
- `metadata`

而这些信息会实时写回对应 tool part。

这让 UI 能在工具尚未完成时展示更丰富进度。

### `ask()`

工具不直接越权执行，而是必须通过权限系统申请。

### `messages`

工具可以访问当前消息上下文，因此工具是可以做上下文感知操作的，而不是完全盲执行。

## 5.3 本地工具如何包装

对于 registry 返回的本地工具，`resolveTools()` 会：

1. 将 zod schema 通过 `ProviderTransform.schema(...)` 适配 provider
2. 生成 AI SDK tool
3. 包装 execute：
   - 触发 `tool.execute.before`
   - 执行真实工具
   - 规范化 attachments，补 `id/sessionID/messageID`
   - 触发 `tool.execute.after`

这里体现的是 **cross-cutting concern injection**：

- 插件 hook
- 附件标准化
- provider schema 适配

都由 runtime 统一注入。

## 5.4 MCP 工具如何合流

`resolveTools()` 还会遍历：

- `MCP.tools()`

对于 MCP 工具，它会同样进行：

- schema transform
- before/after plugin hook
- `ctx.ask()` 权限申请
- 结果文本提取
- 图片/资源转附件
- `Truncate.output()`

这说明在 session loop 眼里：

- 本地工具
- MCP 工具

最终都被收敛成同一类 tool runtime object。

这是很重要的架构统一。

---

# 6. 权限系统：`PermissionNext`

## 6.1 权限为什么是工具运行时的一等能力

OpenCode 不是让模型决定“我可以执行什么”，而是让 runtime 决定。

因此权限系统是工具运行时的强制前置层。

## 6.2 规则表示

权限规则由三元组组成：

- `permission`
- `pattern`
- `action`

其中 `action` 只能是：

- `allow`
- `deny`
- `ask`

这相当于一个轻量级 ACL 系统。

## 6.3 `fromConfig()`：配置到规则集的转换

配置里可以写：

- `permission: "allow"`
- 或按 pattern 写对象

`fromConfig()` 会统一转成规则数组。

同时还会做 home 目录展开：

- `~/`
- `$HOME`

因此配置层是更友好的声明式写法，runtime 层则统一转成标准 ruleset。

## 6.4 `evaluate()` 的裁决算法

`evaluate(permission, pattern, ...rulesets)` 会：

1. 合并多个 ruleset
2. 从后向前找最后一个匹配规则
3. 条件为：
   - permission 通配匹配
   - pattern 通配匹配
4. 若没命中，默认 `ask`

这是一个典型的 **last-match-wins** 策略。

优点：

- 后面的规则可覆盖前面规则
- 规则合并简单直接
- 默认 `ask` 比默认 `allow` 更安全

## 6.5 `ask()` 的执行流程

当工具调用：

- `ctx.ask({...})`

最终会进入 `PermissionNext.ask()`。

算法流程：

1. 对 request 中每个 pattern 做 `evaluate()`
2. 若 `deny` -> 直接抛 `DeniedError`
3. 若 `allow` -> 继续
4. 若 `ask` ->
   - 生成 request id
   - 放入 `pending` map
   - 发布 `permission.asked` 事件
   - 返回一个等待用户回复的 promise

这使得权限系统天然支持异步审批。

## 6.6 `reply()` 的传播逻辑

当用户回复审批：

- `reject`
  - 拒绝当前请求
  - 同 session 下其他 pending permission 一并 reject
- `once`
  - 只放行这一次
- `always`
  - 将请求中的 `always` pattern 永久加入已批准规则
  - 并尝试自动放行同 session 中其他已满足规则的 pending 请求

这说明权限系统不是单点确认框，而是一个**会话级、项目级审批状态机**。

## 6.7 `disabled()` 的作用

`LLM.stream().resolveTools()` 会先调用：

- `PermissionNext.disabled(Object.keys(input.tools), input.agent.permission)`

这一步会把被 agent 规则整体禁用的工具直接从工具集合中移除。

也就是说权限有两层：

- **暴露前过滤**：工具不让模型看到
- **执行前审批**：模型看得到，但执行时仍可能被 ask/deny

这是一种很合理的双层防线。

---

# 7. 工具执行状态机

## 7.1 processor 里的工具状态

在 `SessionProcessor.process()` 中，工具 part 的状态主要包括：

- `pending`
- `running`
- `completed`
- `error`

这些状态不是 UI 用字段，而是运行时真状态。

## 7.2 状态推进顺序

典型流程是：

1. 模型输出 `tool-input-start`
2. 创建 `ToolPart`，状态 `pending`
3. 模型输出 `tool-call`
4. 更新为 `running`
5. 工具真正执行
6. 成功 -> `completed`
7. 失败 -> `error`

这意味着工具调用不是“调用函数拿结果”，而是一个**可观测状态机**。

## 7.3 为什么状态机重要

有了状态机，系统可以支持：

- streaming UI
- 中断恢复
- 权限等待
- tool progress metadata
- 错误持久化
- 历史回放

如果没有 `pending/running/completed/error` 这套状态，很多 agent runtime 能力都无法稳定实现。

---

# 8. `BatchTool`：并行工具调用实现

## 8.1 OpenCode 为什么需要专门的 batch 工具

默认的 tool loop 更适合：

- 一步思考
- 一步执行
- 看结果
- 再决定下一步

但某些场景下，多组工具调用彼此独立，例如：

- 并发 grep 多个目录
- 并发 read 多个文件
- 并发无依赖搜索

这时单步调用会增加 latency 和轮次。

所以 OpenCode 提供了显式 `batch` 工具。

## 8.2 参数结构

`batch` 接受：

- `tool_calls: [{ tool, parameters }]`

并明确要求：

- 至少 1 个
- 最大 25 个

## 8.3 并行执行算法

核心实现是：

- `Promise.all(toolCalls.map((call) => executeCall(call)))`

也就是说是真并发，而不是伪并发。

每个 `executeCall()` 都会：

1. 生成独立 `partID`
2. 检查是否 disallowed
3. 从 registry 获取 tool
4. 校验参数
5. 写入 running part
6. 执行 tool
7. 成功则写 completed
8. 失败则写 error

这是一个标准的 **fan-out / fan-in parallel execution** 模式。

## 8.4 为什么要限制 25 个

因为 batch 的成本不是线性的字符串成本，而是：

- 多个 tool result 都要落库
- 多个结果都可能回流上下文
- 某些工具可能本身是高成本操作

所以限制 25 个是一种 runtime 保护措施，避免模型一次发起不受控的爆炸式并行任务。

## 8.5 为什么禁止 batch 自递归

`DISALLOWED = ["batch"]`

如果允许 batch 里再 batch，会导致：

- 嵌套 fan-out
- 难以控制的 part 爆炸
- 复杂错误聚合
- 权限与追踪难度急剧上升

所以禁止递归 batch 是很理性的设计。

## 8.6 为什么 external tools / MCP tools 不能随便 batch

代码里明确提示：

- 不在 registry 中的外部工具不能被 batched
- 包括 MCP / environment 等外部工具

原因很可能是：

- registry 内工具更容易做统一参数校验与状态追踪
- 外部工具的运行时依赖、会话语义、连接状态更复杂

因此 batch 的边界是：

- **只对本地 registry 工具提供受控并行**

## 8.7 batch 结果聚合

最终 batch 返回：

- 总调用数
- 成功数
- 失败数
- 工具列表
- 细节摘要
- 成功调用附件聚合

这是一个典型的 **aggregated execution report**。

---

# 9. `TaskTool`：子任务与子 agent 编排

## 9.1 为什么 task 被设计成工具

这是 OpenCode 很有代表性的设计选择。

很多系统把 subagent orchestration 单独做成 planner/executor 协议；OpenCode 直接把它做成工具。

好处是：

- 复用现有 tool 调用协议
- 复用权限系统
- 复用 tool result 持久化
- 复用 loop 的下一轮上下文回流

这说明 OpenCode 的工具系统足够强，可以承载“启动另一个 agent”这种高级行为。

## 9.2 参数设计

`task` 接受：

- `description`
- `prompt`
- `subagent_type`
- 可选 `task_id`
- 可选 `command`

这里的关键点是 `task_id`：

- 它允许恢复已有子任务 session
- 而不是每次都开新 subagent

这意味着 `task` 工具是支持续跑的。

## 9.3 子 agent 可见性过滤

初始化 task tool 时，会先：

- `Agent.list()`
- 过滤掉 `mode === primary`
- 如果调用者 agent 存在，还会根据 `PermissionNext.evaluate("task", agentName, caller.permission)` 再过滤一次

因此模型在 tool description 中看到的 subagent 列表，本身已经是按权限裁剪后的。

这是一个很好的“**先裁剪可见面，再交给模型选择**”设计。

## 9.4 执行流程

`task.execute()` 的流程大致是：

1. 根据 `bypassAgentCheck` 决定是否先 ask permission
2. 校验目标 agent 是否存在
3. 判断目标 agent 是否被允许继续调用 task
4. 若 `task_id` 存在，尝试复用 session
5. 否则创建新的子 session
6. 为子 session 注入权限限制：
   - 禁止 `todowrite`
   - 禁止 `todoread`
   - 若目标 agent 没 task 权限，则禁止其继续调用 task
7. 读取当前 assistant message，继承模型配置
8. 通过 `ctx.metadata()` 回写子 session 信息
9. `SessionPrompt.resolvePromptParts(params.prompt)`
10. `SessionPrompt.prompt(...)` 在子 session 中真正发起运行
11. 从子 session 最终结果中提取最后文本
12. 把 `task_id` 与 `<task_result>` 打包返回

这个实现很完整，说明 subtask 并不是表面上的“再问一次模型”，而是完整启动了一个子 runtime。

## 9.5 为什么要禁止子任务读写 todo

这是一种 orchestration discipline。

todo 往往代表主任务的整体计划。如果子 agent 也能随意改主层级 todo，会导致：

- 计划污染
- 状态竞争
- 主/子任务边界混乱

因此默认禁掉子任务的 todo 读写，是为了保持层次清晰。

---

# 10. `SkillTool`：技能按需加载

## 10.1 skill tool 的角色

skill tool 不是执行外部动作，而是把一份专门领域说明书加载进上下文。

它的本质是一个**上下文扩展工具**。

## 10.2 为什么 skill 不是一开始全注入

`SystemPrompt.skills()` 只注入 skill 索引，不注入完整 skill 内容。

等模型识别到当前任务和某个 skill 匹配时，再调用：

- `skill({ name })`

这样做的原因是：

- skill 全文可能很长
- 大量 skill 同时注入会浪费上下文
- 许多任务只需要其中一个 skill

这是典型的 **lazy loading for prompt context**。

## 10.3 执行流程

`SkillTool.execute()` 会：

1. `Skill.get(name)` 查 skill
2. 若不存在，返回 available skills 提示
3. 先申请：
   - `permission: "skill"`
   - `patterns: [skill name]`
4. 获取 skill 所在目录
5. 采样列出最多 10 个辅助文件
6. 返回：
   - `<skill_content name="...">`
   - skill 正文
   - base directory
   - sampled file list

这说明 skill tool 返回的不是一句摘要，而是**可直接被后续轮次消费的结构化上下文块**。

---

# 11. MCP：外部工具生态的统一接入

## 11.1 从 server 路由能看到什么

`server/routes/mcp.ts` 暴露了：

- 查询状态
- 添加 MCP server
- OAuth 启动/回调/认证/移除
- connect / disconnect

这说明 OpenCode 对 MCP 的支持已经是正式平台能力，而不是临时本地 hack。

## 11.2 运行时如何看待 MCP 工具

在 `SessionPrompt.resolveTools()` 中，`MCP.tools()` 返回的工具会被合流成与本地工具同类型对象。

运行时会为它们统一注入：

- plugin before/after
- permission ask
- schema transform
- 结果截断
- 文本与附件规范化

因此对 loop 来说，本地工具与 MCP 工具没有根本差别，差别只在执行来源。

## 11.3 为什么这是重要设计

这避免了“本地工具走一套、MCP 工具走另一套”导致的系统分裂。

也就是说，OpenCode 把外部协议生态吸收进了内部 tool runtime，而不是让 MCP 成为旁路能力。

---

# 12. 工具结果如何回流到上下文

## 12.1 工具结果先写成 part

无论普通 tool、batch 子调用、task、skill 还是 MCP 工具，结果最终都要写成 `ToolPart`：

- `input`
- `output`
- `metadata`
- `attachments`
- `title`
- `time`

这保证所有结果都有统一状态真相。

## 12.2 再由 `MessageV2.toModelMessages()` 转成模型上下文

下一轮 loop 开始时，这些 tool parts 会被转换成模型可读的 tool result 内容：

- `completed` -> output-available
- `error` -> output-error
- `pending/running` -> interrupted tool error

所以工具结果回流不是靠 tool 自己写 prompt，而是靠消息系统统一回放。

这是 OpenCode 很重要的设计：

- **执行与上下文回流解耦**

## 12.3 attachment 的标准化

工具执行后，runtime 会补齐每个附件的：

- `id`
- `sessionID`
- `messageID`

这样附件就从临时执行结果升级成 session 内部可持久化资产。

---

# 13. 这个模块背后的核心设计原则

## 13.1 工具系统是 runtime 核心，不是附加能力

OpenCode 不是“有时调一下工具”，而是把工具系统做成 agent runtime 的主执行通道之一。

## 13.2 统一抽象优先于特例实现

无论：

- 普通本地工具
- 插件工具
- 自定义脚本工具
- subtask
- skill
- batch
- MCP

最终都尽量收敛到统一的：

- schema
- execute
- permission
- tool part
- result replay

## 13.3 权限与执行绑定，而不是附加在 UI 层

权限系统不依赖前端按钮，而是运行时强制执行。

这保证了：

- CLI 场景
- App 场景
- SDK 场景
- 自动化场景

都能共享同一安全边界。

## 13.4 工具输出预算管理是内建能力

通过 `Truncate.output()`，系统假定：

- 工具输出默认可能过长
- 所以必须由 runtime 控制上下文预算

这是很成熟的 agent runtime 设计。

## 13.5 并行必须是受控并行

OpenCode 支持并行，但通过显式 `batch` 工具、限制数量、禁止递归、限制工具范围来控制复杂度。

这说明它追求的不是“最大并发”，而是“可解释、可追踪、可恢复的并发”。

---

# 14. 推荐阅读顺序

如果你要继续沿着这个模块深挖，建议按这个顺序读：

1. `packages/opencode/src/tool/tool.ts`
2. `packages/opencode/src/tool/registry.ts`
3. `packages/opencode/src/session/prompt.ts` 中 `resolveTools()`
4. `packages/opencode/src/permission/next.ts`
5. `packages/opencode/src/tool/batch.ts`
6. `packages/opencode/src/tool/task.ts`
7. `packages/opencode/src/tool/skill.ts`
8. `packages/opencode/src/session/processor.ts`
9. `packages/opencode/src/server/routes/mcp.ts`

建议重点盯住这些函数/概念：

- `Tool.define()`
- `ToolRegistry.state()`
- `ToolRegistry.all()`
- `ToolRegistry.tools()`
- `SessionPrompt.resolveTools()`
- `PermissionNext.evaluate()`
- `PermissionNext.ask()`
- `PermissionNext.reply()`
- `PermissionNext.disabled()`
- `BatchTool.execute()`
- `TaskTool.execute()`
- `SkillTool.execute()`

---

# 15. 下一步还需要深挖的问题

这个模块已经把主链路理顺了，但还有一些点值得继续深挖：

- **问题 1**：`edit`、`write`、`apply_patch` 三种修改工具的协议差异，以及不同模型为什么切换不同修改工具
- **问题 2**：`bash` 工具如何处理 shell、安全边界、超时、中断与输出截断
- **问题 3**：MCP 核心实现 `MCP.tools()` 的内部缓存、连接管理、失败恢复策略是什么
- **问题 4**：插件工具与本地工具在 telemetry、error classification、attachment 处理上是否完全一致
- **问题 5**：tool metadata 在 UI 中如何被展示，哪些字段会影响用户可见的进度反馈
- **问题 6**：batch 内部如果多个工具互相竞争文件写入或锁资源，runtime 是否有冲突检测
- **问题 7**：task tool 生成子 session 后，父子 session 的取消、重试、汇总关系是否还有更深层的协议
- **问题 8**：Skill 的资源文件、脚本、模板如何在后续工具调用中被真正消费，是否存在专门路径解析策略

---

# 16. 小结

`tool_runtime_and_execution` 模块定义了 OpenCode 的执行体系：

- `Tool.define()` 提供统一 ABI
- `ToolRegistry` 负责发现与装配
- `resolveTools()` 负责运行时绑定
- `PermissionNext` 负责权限裁决
- `SessionProcessor` 负责状态持久化与回流
- `BatchTool` 提供受控并行
- `TaskTool` 负责子 agent 编排
- `SkillTool` 负责按需上下文扩展
- MCP 则把外部工具生态纳入统一执行面

因此，这个模块不是一个“工具目录”，而是 OpenCode agent runtime 的执行引擎。
