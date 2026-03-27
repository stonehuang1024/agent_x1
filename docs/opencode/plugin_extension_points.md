# Plugin / Extension Points 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的插件系统与扩展点。

核心问题是：

- plugin 是如何被发现、安装、加载的
- 内置插件与外部插件有什么区别
- hook 是如何注入到 runtime 主链路中的
- 自定义 tool 是如何通过 plugin 暴露出来的
- `tool.definition` / `tool.execute.before` / `tool.execute.after` 这些 hook 的边界是什么
- plugin 为什么既能改 chat 参数，又能改 shell env，还能监听总线事件

核心源码包括：

- `packages/opencode/src/plugin/index.ts`
- `packages/opencode/src/tool/registry.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/llm.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/tool/bash.ts`
- `packages/opencode/src/session/compaction.ts`

这一层本质上是 OpenCode 的**宿主级扩展机制**。

---

# 2. 总体定位：plugin 不是单一 tool 扩展

从源码可以很清楚看出，plugin 能影响的远不止工具定义。

它至少覆盖：

- chat system prompt 变换
- chat params / headers 注入
- chat messages 变换
- command 执行前 hook
- tool definition 变换
- tool execute before / after hook
- shell env 注入
- compaction prompt/context 注入
- text complete 后处理
- 全局事件监听
- 额外 tool 暴露

这说明 OpenCode 的 plugin 设计目标不是：

- “给你加几个外部工具”

而是：

- **允许外部代码接入 runtime 主流程中的关键节点**

---

# 3. 插件输入环境：`PluginInput`

`Plugin.state()` 在初始化每个插件时，会构造统一输入：

- `client`
- `project`
- `worktree`
- `directory`
- `serverUrl`
- `Bun.$`

## 3.1 `client`

这里的 `client` 通过：

- `createOpencodeClient(...)`

创建，并且 `fetch` 直接走：

- `Server.Default().fetch(...)`

这意味着插件即使在同进程里，也通过正式 SDK client 调 OpenCode server 接口，而不是偷偷直连内部对象。

这是非常好的设计，因为：

- 保持 API 边界一致
- 插件更接近真实外部集成方
- 降低直接操作 runtime 内部状态的耦合

## 3.2 `serverUrl`

提供当前 server URL 抽象，让插件既可在本地嵌入式模式工作，也可适配真实 server 地址。

## 3.3 `Bun.$`

说明插件也可以安全地获得 Bun shell 能力，适合做集成型自动化。

---

# 4. 插件来源：内置、默认、用户配置

## 4.1 INTERNAL_PLUGINS

代码里直接内置了：

- `CodexAuthPlugin`
- `CopilotAuthPlugin`
- `GitlabAuthPlugin`

这类插件不是通过 npm 安装，而是直接 import 进宿主。

说明 OpenCode 把某些关键认证能力视为第一方扩展模块。

## 4.2 BUILTIN 默认插件

还有默认插件包：

- `opencode-anthropic-auth@0.0.13`

当未禁用默认插件时，它会自动加入插件列表。

## 4.3 用户配置插件

来自：

- `config.plugin`

这说明最终插件集合是：

- 内置直接导入插件
- 默认插件
- 用户显式配置插件

三层叠加。

---

# 5. 插件加载与安装机制

## 5.1 npm 安装型插件

当插件字符串不是 `file://` 时：

- 解析包名与版本
- 调 `BunProc.install(pkg, version)`
- 得到可 import 的本地路径

说明插件分发主要支持：

- npm package 形式

## 5.2 本地 file 插件

若是 `file://` 路径，则直接 import。

这对开发本地插件非常方便。

## 5.3 失败处理

如果安装或加载失败：

- 写日志
- 通过 `Bus.publish(Session.Event.Error, ...)` 发出错误事件

这说明插件失败不是默默吞掉，而会进入标准 session error 通道。

---

# 6. 为什么加载时要做去重

导入模块后，代码会：

- 遍历 `Object.entries(mod)`
- 用 `seen: Set<PluginInstance>` 去重同一个函数引用

这是为了解决一种实际问题：

- 同一个插件函数可能同时作为 named export 和 default export 暴露

若不去重，就会重复初始化。

这类细节非常说明插件系统是按真实生态痛点设计的。

---

# 7. `Plugin.trigger()`：hook 调度核心

## 7.1 设计

`Plugin.trigger(name, input, output)` 会：

1. 取出所有 hooks
2. 依次查找该 hook 名对应函数
3. 顺序执行 `await fn(input, output)`
4. 返回被逐步修改后的 `output`

## 7.2 为什么用 `input + output` 双对象模型

这种设计非常适合插件 hook：

- `input`：当前上下文，只读语义
- `output`：允许插件增量修改的目标对象

这比单纯返回新值更稳定，因为：

- 多个插件可以顺序叠加修改
- 宿主能控制可变面
- hook 协议更明确

## 7.3 顺序语义

插件是顺序执行的，因此后加载插件可以看到前面插件修改后的 output。

这意味着 plugin order 是有语义的。

---

# 8. `Plugin.list()` 与 `Plugin.init()`

## 8.1 `list()`

返回已初始化 hooks 集合，供如 `ToolRegistry` 这类模块进一步读取 plugin 暴露的 tool。

## 8.2 `init()`

会做两件事：

- 对每个 hook 调 `config?.(config)`
- 订阅 `Bus.subscribeAll(...)`，把所有 bus 事件转发给 hook 的 `event()`

这说明 plugin 系统不仅有点对点 hook，还有：

- **全局事件观察能力**

---

# 9. Tool 扩展点：`ToolRegistry` 如何接入 plugin tools

## 9.1 两类自定义 tool 来源

`ToolRegistry.state()` 会收集：

- `.opencode/{tool,tools}/*.{js,ts}` 本地工具模块
- `Plugin.list()` 中每个 plugin 的 `tool` 字段

这说明 OpenCode 的 tool 扩展不是只有插件一条路径。

还支持：

- 本地目录级自定义工具

## 9.2 `fromPlugin()` 适配器

无论来自本地工具文件还是插件 `tool` 字段，最终都通过 `fromPlugin()` 转成标准 `Tool.Info`。

适配过程包括：

- `parameters: z.object(def.args)`
- `description: def.description`
- 构造 `PluginToolContext`
- 调 `def.execute(args, pluginCtx)`
- 用 `Truncate.output(...)` 做输出截断处理

这说明插件 tool 并不是系统外的特例，而是被收敛进统一 tool runtime 接口。

---

# 10. `tool.definition`：工具定义暴露前拦截

在 `ToolRegistry.tools()` 中，每个工具 `init()` 之后还会执行：

- `Plugin.trigger("tool.definition", { toolID }, output)`

其中 `output` 包括：

- `description`
- `parameters`

这说明插件可以在工具真正暴露给模型之前修改：

- 工具描述
- 参数 schema

## 10.1 为什么这很强

这意味着 plugin 可以：

- 精简或增强工具描述
- 调整参数暴露形式
- 针对特定环境包装工具能力

但它仍然发生在“定义层”，还没到执行层。

---

# 11. `tool.execute.before/after`：运行时环绕 hook

在 `session/prompt.ts` 中，工具执行路径被统一包了一层 hook：

- `tool.execute.before`
- 真正执行工具
- `tool.execute.after`

而且这不仅对普通工具生效，对 `task` 特殊分支也显式触发了同名 hook。

## 11.1 before hook 输入

会包含：

- `tool`
- `sessionID`
- `messageID`
- `callID`
- `args`

## 11.2 after hook

通常还会包含：

- result 或 error 相关执行结果上下文

这使插件可以：

- 记录审计日志
- 注入遥测
- 做权限旁路审查
- 包装工具结果
- 做企业控制策略

这本质上是一个工具 runtime middleware 机制。

---

# 12. Chat 扩展点：LLM 调用前后可改造

## 12.1 `experimental.chat.system.transform`

在 `session/llm.ts` 中，system prompt 组装好后，会触发：

- `experimental.chat.system.transform`

允许插件修改：

- `system`

## 12.2 `chat.params`

随后会触发：

- `chat.params`

允许修改：

- model call params
- provider options
- instruction 等调用参数

## 12.3 `chat.headers`

还允许插件改：

- request headers

这对：

- API auth
- organization routing
- tracing headers

都很关键。

## 12.4 `experimental.chat.messages.transform`

在 prompt 构建期间，还会触发：

- `experimental.chat.messages.transform`

说明插件还能直接改造模型看到的 messages。

这是非常强的扩展点，基本相当于 message middleware。

---

# 13. 其他关键 hook 点

## 13.1 `chat.message`

在 chat message 进入主链路时触发，可用于消息级审计或增强。

## 13.2 `shell.env`

在 `bash.ts` 与 `prompt.ts` 的 shell/command 相关执行中都会触发：

- `shell.env`

允许插件注入环境变量。

这对：

- 凭证注入
- 代理设置
- 企业环境变量治理

非常有用。

## 13.3 `command.execute.before`

在命令执行前提供拦截点。

## 13.4 `experimental.session.compacting`

在 compaction 过程中允许插件补充：

- context
- prompt

说明 compaction 也被纳入扩展面。

## 13.5 `experimental.text.complete`

在 processor 完成文本段后允许插件后处理文本结果。

这说明插件甚至能参与 assistant 文本输出的最终成型阶段。

---

# 14. Bus 事件桥接：插件的观察者模式接口

`Plugin.init()` 中通过：

- `Bus.subscribeAll(...)`

把所有 bus 事件转发给每个 hook 的：

- `event({ event })`

这意味着插件不仅能拦截点状 hook，还能被动观察整个 runtime 的事件流。

这是另一种扩展模式：

- **observer-style extension**

与前面的 trigger-style hook 互补。

---

# 15. 插件系统的边界与约束

## 15.1 不是任意内部对象注入

插件虽然很强，但宿主仍保持了一层边界：

- 通过 SDK client 暴露 server 能力
- 通过定义好的 hooks 暴露时机
- 通过 ToolRegistry 适配器收敛 tool 形态

这比直接把内部 Session/Provider/DB 单例暴露给插件要干净得多。

## 15.2 插件顺序敏感

因为 `trigger()` 顺序执行，多个插件可能会互相覆盖 output。

这意味着：

- 插件顺序就是一部分行为定义

## 15.3 插件能做很多，但仍受宿主 runtime 驱动

插件并不直接“拥有主循环”，而是在 OpenCode 设定的生命周期节点上被调用。

这保证宿主仍是主导者。

---

# 16. 这个模块背后的关键设计原则

## 16.1 扩展点要覆盖主流程关键节点

OpenCode 没把 plugin 限制在 UI 或工具层，而是覆盖：

- prompt
- model call
- tool execution
- shell env
- compaction
- events

## 16.2 工具扩展与 hook 扩展并存

插件既可以：

- 新增工具
- 也可以修改已有流程

这是两种不同粒度的扩展能力。

## 16.3 插件应通过正式 API 边界接入宿主

SDK client + hook protocol 比直接暴露内部对象更稳。

## 16.4 错误应该进入标准事件链路

插件安装/加载失败通过 `Session.Event.Error` 上报，说明插件不是系统外异常。

---

# 17. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/plugin/index.ts`
2. `packages/opencode/src/tool/registry.ts`
3. `packages/opencode/src/session/prompt.ts`
4. `packages/opencode/src/session/llm.ts`
5. `packages/opencode/src/session/processor.ts`
6. `packages/opencode/src/tool/bash.ts`
7. `packages/opencode/src/session/compaction.ts`

重点盯住这些函数/概念：

- `Plugin.state()`
- `Plugin.trigger()`
- `Plugin.init()`
- `Plugin.list()`
- `ToolRegistry.fromPlugin()`
- `tool.definition`
- `tool.execute.before`
- `tool.execute.after`
- `chat.params`
- `chat.headers`
- `shell.env`
- `event()`

---

# 18. 下一步还需要深挖的问题

这一篇已经把 plugin 扩展主框架讲清楚了，但还有一些值得继续拆的点：

- **问题 1**：`@opencode-ai/plugin` 包里的 Hooks / ToolDefinition 类型细节还可继续精读
- **问题 2**：内置认证插件具体如何通过 hook 修改 provider/auth 行为，还值得单独分析
- **问题 3**：多个插件同时改写同一个 output 时，是否存在冲突管理或顺序声明机制，还需要进一步确认
- **问题 4**：插件 tool 的权限、审计与 UI 展示方式是否与内置工具完全一致，还值得继续追踪
- **问题 5**：`experimental.*` hook 与稳定 hook 的兼容承诺边界是什么，还需进一步确认
- **问题 6**：通过 SDK client 调回宿主 server 时，是否存在递归调用或死循环保护机制
- **问题 7**：`.opencode/tools` 本地工具与 npm 插件工具在发布、隔离、版本控制上的差异，还可以进一步展开
- **问题 8**：插件依赖安装缓存、更新与安全信任模型也值得继续研究

---

# 19. 小结

`plugin_extension_points` 模块定义了 OpenCode 如何把自身变成一个可扩展宿主：

- `Plugin` 负责插件安装、初始化、hook 调度和事件桥接
- `ToolRegistry` 负责把插件工具与本地工具目录统一收敛成标准工具
- `session/llm/prompt/processor/bash/compaction` 等主链路则提供关键 hook 点
- SDK client 边界保证插件通过正式控制面与宿主交互

因此，这一层不是简单插件市场接口，而是 OpenCode runtime 的系统级扩展架构。
