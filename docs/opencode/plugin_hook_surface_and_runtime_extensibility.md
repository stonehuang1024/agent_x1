# Plugin Hook Surface / Runtime Extensibility 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的插件钩子面与运行时扩展机制。

核心问题是：

- 插件是如何被发现、安装、加载和初始化的
- 为什么插件系统既有 internal plugin，又支持 npm/file:// plugin
- `Plugin.trigger()` 的 hook 模型是什么
- chat/tool/session/shell/permission 这些钩子分别插在运行时哪个位置
- 为什么 OpenCode 能在不改主链路代码的情况下修改 headers、params、system prompt、tool output 与 compaction prompt

核心源码包括：

- `packages/opencode/src/plugin/index.ts`
- `packages/opencode/src/session/llm.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/tool/registry.ts`
- `packages/opencode/src/tool/bash.ts`
- `packages/opencode/src/permission/index.ts`
- `packages/opencode/src/plugin/codex.ts`
- `packages/opencode/src/plugin/copilot.ts`

这一层本质上是 OpenCode 的**运行时 hook 总线与能力扩展框架**。

---

# 2. 为什么插件系统必须是一等公民

OpenCode 需要面对很多可变点：

- provider headers / auth 细节
- system prompt 改写
- chat params 调优
- tool 执行前后审计
- shell env 注入
- permission ask 决策
- compaction prompt 定制
- 新工具定义元数据扩展

如果所有这些都写死在主代码里：

- 主链路会迅速膨胀
- 企业/定制场景很难适配
- provider 生态差异会污染核心逻辑

因此插件系统被设计成：

- **贯穿 provider、prompt、tool、permission、shell 的统一扩展面**

---

# 3. `Plugin.state()`：插件系统是 per-instance 运行时对象

`plugin/index.ts` 使用：

- `Instance.state(async () => { ... })`

构造当前实例的插件状态，核心包括：

- `hooks`
- `input`

其中 `input` 提供给所有插件：

- `client`
- `project`
- `worktree`
- `directory`
- `serverUrl`
- `$`

这说明插件不是全局单例，而是与当前实例/项目目录绑定的 runtime 组件。

---

# 4. 插件输入里的 `client`：为什么插件可以反调 OpenCode server

state 初始化时会创建：

- `createOpencodeClient({ baseUrl, directory, headers, fetch })`

并把 `Server.Default().fetch(...)` 接进去。

这意味着插件不只是被动接收 hook 回调，它还能：

- 主动调用 OpenCode 自身 server API

这大大增强了插件的能力面。

---

# 5. internal plugins vs configurable plugins

插件有两类来源：

## 5.1 internal plugins

直接 import 的：

- `CodexAuthPlugin`
- `CopilotAuthPlugin`
- `GitlabAuthPlugin`

## 5.2 configurable plugins

来自：

- `config.plugin`
- 默认插件列表 `BUILTIN`

这说明 OpenCode 插件生态既包括：

- 官方内建扩展
- 用户/企业自定义扩展

---

# 6. 默认插件与禁用开关

`Plugin.state()` 中：

- 若未设置 `OPENCODE_DISABLE_DEFAULT_PLUGINS`
- 则会把 `BUILTIN` 合并进插件列表

这说明默认插件本身也是可关闭的，而不是硬编码不可禁用。

在可控性上这是很好的设计。

---

# 7. npm / file:// 插件加载路径

对于配置插件：

- 若不是 `file://`，先用 `BunProc.install(pkg, version)` 安装
- 否则直接 `import(plugin)`

## 7.1 为什么这重要

说明插件分发形态不止一种：

- 本地文件开发态插件
- npm 版本化发布插件

这对生态建设非常关键。

---

# 8. 插件安装/加载失败如何反馈

如果安装或 import 失败：

- 记录日志
- `Bus.publish(Session.Event.Error, { error: NamedError.Unknown(...) })`

这说明插件失败不是静默的，也不是只打日志。

系统会把它作为正式 session error 对外暴露。

---

# 9. 为什么要避免 duplicate initialization

加载模块后会遍历 `Object.entries(mod)`，但用：

- `seen = new Set<PluginInstance>()`

去重。

原因是同一个插件函数可能同时以：

- named export
- default export

形式暴露。

如果不去重，就会重复初始化同一插件。

这说明插件加载器对 JS/TS module export 细节是有经验处理的。

---

# 10. `Plugin.trigger()`：插件系统的核心抽象

`trigger(name, input, output)` 的语义很简单：

1. 遍历当前实例的所有 hooks
2. 取 `hook[name]`
3. 若存在，则 `await fn(input, output)`
4. 最后返回 `output`

## 10.1 这说明 hook 协议是“就地修改 output”风格

插件不是返回新对象链式组合，而是直接 mutate 第二个参数。

这种模式的优点是：

- 调用方简洁
- 多插件累积修改更自然
- 能表达 append/merge/override

代价是：

- 插件顺序变得重要
- side effect 更强

---

# 11. `Plugin.init()`：config hook 与全局事件转发

`init()` 主要做两件事：

## 11.1 config hook

遍历 hooks 调：

- `hook.config?.(config)`

## 11.2 事件 hook

- `Bus.subscribeAll(...)`
- 将每个 bus event 传给 `hook.event?.({ event })`

这说明插件不仅能拦截特定运行时节点，还能观察整个系统事件总线。

---

# 12. chat 相关 hook 面

从 grep 结果看，session/llm/prompt 主链路里至少有这些：

- `experimental.chat.system.transform`
- `chat.params`
- `chat.headers`
- `experimental.chat.messages.transform`
- `chat.message`
- `experimental.text.complete`

这些 hook 基本覆盖了从：

- 用户消息落库
- message history 改写
- system prompt 构造
- provider 参数生成
- headers 注入
- 文本完成后处理

整条 chat pipeline。

---

# 13. `experimental.chat.system.transform`

在 `LLM.stream()` 中，system prompt 刚拼出来后会调用：

- `Plugin.trigger("experimental.chat.system.transform", { sessionID, model }, { system })`

这意味着插件可以：

- 增删 system 段落
- 改写 provider prompt
- 注入企业策略或审计提示

并且是在真正发请求前的最后阶段之一。

---

# 14. `chat.params`

`LLM.stream()` 还会对：

- temperature
- topP
- topK
- options

调用：

- `Plugin.trigger("chat.params", ..., { ... })`

这让插件可以在 provider request 发起前，动态调整：

- 采样参数
- reasoning budget
- providerOptions
- 其他 runtime options

---

# 15. `chat.headers`

headers 也会经过：

- `Plugin.trigger("chat.headers", ..., { headers: {} })`

这对 provider auth / telemetry / enterprise integration 尤其关键。

实际内建插件里就有代表：

## 15.1 `plugin/codex.ts`

为 openai/codex 场景注入：

- `originator`
- `User-Agent`

## 15.2 `plugin/copilot.ts`

也会在 `chat.headers` 上对 copilot 请求做调整。

这说明 header hook 不是摆设，而是 provider 兼容层真实依赖的一部分。

---

# 16. `experimental.chat.messages.transform`

在 normal turn 进入 processor 前，prompt 层会调用：

- `Plugin.trigger("experimental.chat.messages.transform", {}, { messages: msgs })`

这意味着插件可以在 `MessageV2.toModelMessages()` 之前，先改写内部消息历史。

比如：

- 删除某些 part
- 注入额外 synthetic text
- 做企业策略过滤

它的权力非常大，因此属于高风险高价值 hook。

---

# 17. `chat.message`

`createUserMessage()` 完成 parts 构造后，会调用：

- `Plugin.trigger("chat.message", { sessionID, agent, model, messageID, variant }, { message, parts })`

这说明插件甚至可以在用户消息正式落库前介入输入摄取结果。

这对：

- 自动补充 metadata
- 输入安全审计
- 自定义附件展开

都很有用。

---

# 18. `experimental.text.complete`

在 processor 处理 `text-end` 时，会调用：

- `Plugin.trigger("experimental.text.complete", { sessionID, messageID, partID }, { text })`

这意味着 assistant 文本在最终写回前，还可以被插件做最后加工。

例如：

- 清理尾部噪音
- 规范化输出格式
- 注入轻量标记

---

# 19. tool 相关 hook 面

最频繁的一组扩展点是：

- `tool.execute.before`
- `tool.execute.after`
- `tool.definition`

## 19.1 `tool.execute.before/after`

本地 tool、MCP tool、task tool 三条路径都在用。

这说明 OpenCode 把“工具执行审计/包装”统一做成正式 hook，而不是某个具体工具私货。

## 19.2 `tool.definition`

在 `tool/registry.ts` 中，工具定义暴露给模型前还会调用：

- `Plugin.trigger("tool.definition", { toolID }, output)`

这意味着插件可以改工具描述、schema、可见定义元数据。

---

# 20. `tool.execute.before/after` 在运行时中的意义

这些 hook 能看到：

- tool 名
- sessionID
- callID
- args
- output

因此它们可用于：

- logging / tracing
- metrics
- policy enforcement
- output post-processing
- 企业审计

并且它们覆盖：

- 普通本地工具
- MCP 工具
- task 子代理工具

说明 hook 面相当完整。

---

# 21. shell 相关 hook：`shell.env`

grep 可见：

- `tool/bash.ts`
- `pty/index.ts`
- `session/prompt.ts` shell command 路径

都会调用：

- `Plugin.trigger("shell.env", { cwd, sessionID?, callID? }, { env: {} })`

这说明 shell 环境变量注入是统一扩展点。

非常适合：

- 企业代理配置
- 凭证注入
- 调试环境变量
- command sandbox 标签注入

---

# 22. permission 相关 hook：`permission.ask`

在 `permission/index.ts` 中，权限判定流程会调用：

- `Plugin.trigger("permission.ask", info, { status: "ask" })`

这意味着插件甚至可以参与权限决策。

## 22.1 含义

插件可以把默认 ask/allow/deny 再次改写。

这对：

- 企业策略网关
- 自动审批系统
- 目录白名单策略

非常关键。

同时这也意味着这是一个高风险 hook。

---

# 23. compaction 相关 hook：`experimental.session.compacting`

`SessionCompaction.process()` 中会调用：

- `Plugin.trigger("experimental.session.compacting", { sessionID }, { context: [], prompt: undefined })`

这意味着插件可以：

- 为 compaction 补上下文
- 彻底替换默认 compaction prompt

从而显著影响长会话恢复质量。

---

# 24. command 相关 hook：`command.execute.before`

grep 显示在 `session/prompt.ts` 的 shell/command 执行路径上还有：

- `Plugin.trigger("command.execute.before", ...)`

说明插件不仅能介入工具调用，也能介入 session 内的 command 执行链路。

---

# 25. 事件 hook：为什么 `Bus.subscribeAll` 很重要

插件系统初始化后会订阅所有 Bus 事件，并把它们转发给：

- `hook.event({ event })`

这意味着插件可以被动观察整个系统：

- session
- message
- mcp
- file watcher
- workspace
- permission
- share

等所有事件。

这使插件系统拥有“旁路观察器”能力，而不仅是局部插针。

---

# 26. 插件顺序的语义

因为 `Plugin.trigger()` 是按 hooks 顺序逐个执行，且共享同一个 output 对象，所以顺序非常重要：

- internal plugins 先加载
- default + config plugins 后加载
- 同名 hook 按加载顺序叠加修改

这意味着插件生态虽然灵活，但也天然存在：

- 顺序依赖
- 覆盖冲突
- 最后写入者获胜

这些都是扩展架构必须接受的 trade-off。

---

# 27. 为什么插件系统采用“可变 output 对象”而非纯函数合并

这是一个很值得注意的设计点。

采用 mutable output 的好处：

- hook 签名统一
- 复杂嵌套结构好改
- append/merge 直观

缺点：

- 类型更难约束
- 插件间副作用耦合更强
- 调试更难

从源码里的 `@ts-expect-error` 注释也能看出来，这套系统在类型层面仍有一定粗糙度，但在工程实用性上非常直接。

---

# 28. 一个典型的插件扩展链路示例

以一次普通 chat turn 为例，插件可以依次介入：

1. `chat.message`
   - 修改用户输入 parts
2. `experimental.chat.messages.transform`
   - 改写历史消息
3. `experimental.chat.system.transform`
   - 改 system prompt
4. `chat.params`
   - 调模型参数
5. `chat.headers`
   - 注入请求头
6. `tool.execute.before`
   - 审计某个 tool 调用
7. `tool.execute.after`
   - 改工具结果
8. `experimental.text.complete`
   - 收敛最终 assistant 文本

这说明插件系统几乎覆盖整个 turn lifecycle。

---

# 29. 这个模块背后的关键设计原则

## 29.1 扩展点必须沿主链路均匀分布

所以 provider、prompt、tool、shell、permission、compaction 都有 hook。

## 29.2 插件既要能主动改写，也要能被动观察

所以同时存在 `trigger(...)` 和 `event` 总线订阅。

## 29.3 官方兼容逻辑本身也应尽量插件化

Codex/Copilot/GitLab auth plugin 就是例子。

## 29.4 插件系统要服务 runtime，而不是只服务 UI 配置

因此它拿到的是 client/project/worktree/$/serverUrl 等强能力输入。

---

# 30. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/plugin/index.ts`
2. `packages/opencode/src/session/llm.ts`
3. `packages/opencode/src/session/prompt.ts`
4. `packages/opencode/src/session/processor.ts`
5. `packages/opencode/src/tool/registry.ts`
6. `packages/opencode/src/tool/bash.ts`
7. `packages/opencode/src/permission/index.ts`
8. `packages/opencode/src/plugin/codex.ts`
9. `packages/opencode/src/plugin/copilot.ts`

重点盯住这些函数/概念：

- `Plugin.trigger()`
- `Plugin.init()`
- `chat.params`
- `chat.headers`
- `chat.message`
- `tool.execute.before/after`
- `shell.env`
- `permission.ask`
- `experimental.session.compacting`

---

# 31. 下一步还需要深挖的问题

这一篇已经把插件扩展面主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`@opencode-ai/plugin` 的 `Hooks` 类型定义还值得单独查看，确认全部 hook 面列表
- **问题 2**：Codex/Copilot/GitLab 内建插件的完整行为还可以继续分别拆文档
- **问题 3**：多个插件同时改写同一 output 对象时的冲突策略目前主要依赖顺序，还值得进一步评估
- **问题 4**：插件加载失败通过 `Session.Event.Error` 暴露，但具体落到哪个 session 的边界还可继续检查
- **问题 5**：插件是否拥有过大的 shell/client 能力面，这个安全边界值得继续分析
- **问题 6**：`tool.definition` 对模型可见工具描述的影响范围还可继续追踪到 prompt 构造侧
- **问题 7**：event hook 在高事件量场景下的性能影响还值得继续评估
- **问题 8**：未来如果插件需要隔离沙箱，当前直接 import/执行模式可能需要重构，这一点值得提前关注

---

# 32. 小结

`plugin_hook_surface_and_runtime_extensibility` 模块定义了 OpenCode 如何把核心执行链路开放给插件系统，同时保持主逻辑相对稳定：

- `Plugin.state()` 负责插件发现、安装、加载与实例级初始化
- `Plugin.trigger()` 提供统一的可变 output hook 机制
- chat/tool/shell/permission/compaction 等关键节点都暴露了扩展面
- `Plugin.init()` 则把系统事件总线整体转发给插件观察

因此，这一层不是简单插件加载器，而是 OpenCode 运行时可扩展性、provider 兼容扩展与企业定制能力的核心基础设施。
