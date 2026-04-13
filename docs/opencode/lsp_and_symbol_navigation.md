# LSP / Symbol Navigation 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 LSP 与符号导航体系。

核心问题是：

- OpenCode 为什么还需要 LSP，而不只靠 `grep/read/glob`
- `lsp` 工具到底能做什么
- LSP server 是如何发现、启动、缓存和复用的
- 为什么 `touchFile()` 很关键
- symbol / definition / reference / call hierarchy 这些结果如何成为 agent 的导航能力基础

核心源码包括：

- `packages/opencode/src/tool/lsp.ts`
- `packages/opencode/src/lsp/index.ts`
- `packages/opencode/src/lsp/server.ts`
- `packages/opencode/src/lsp/client.ts`

这一层本质上是 OpenCode 的**语义级代码导航系统**。

---

# 2. 为什么还需要 LSP

`glob`、`grep`、`read` 已经能做很多事情，但它们主要解决的是：

- 路径模式查找
- 文本匹配查找
- 精确文件内容提取

它们擅长的是：

- lexical retrieval
- textual narrowing

而 LSP 擅长的是：

- definition / implementation
- references
- hover
- symbol index
- call hierarchy

也就是说，LSP 解决的是：

- **语义导航**

这在大型代码库中非常重要，因为：

- 某个名字可能被重载
- 文本匹配不等于真实引用关系
- symbol 作用域和跳转点常常不是简单 grep 能精确解决的

---

# 3. `LspTool`：对模型暴露的能力面

## 3.1 支持的操作

`LspTool` 目前支持：

- `goToDefinition`
- `findReferences`
- `hover`
- `documentSymbol`
- `workspaceSymbol`
- `goToImplementation`
- `prepareCallHierarchy`
- `incomingCalls`
- `outgoingCalls`

这基本覆盖了代码理解中最重要的语义导航动作。

## 3.2 参数模型

参数包括：

- `operation`
- `filePath`
- `line`
- `character`

其中行列号使用用户熟悉的 1-based，再在内部转成 0-based。

这和 read/tool/VS Code 路径引用体系保持一致。

## 3.3 权限与边界

在真正执行前，LSP tool 会：

- 解析相对路径为 `Instance.directory` 下路径
- `assertExternalDirectory(ctx, file)`
- `ctx.ask({ permission: "lsp", patterns: ["*"], always: ["*"] })`

说明 LSP 导航也在统一权限系统下，不是绕过安全边界的特殊通道。

---

# 4. LSP tool 的执行流程

`LspTool.execute()` 的控制流很清晰：

1. 解析文件路径
2. 检查 external directory
3. 请求 `lsp` 权限
4. 检查文件存在
5. `LSP.hasClients(file)` 确认有可用 server
6. `LSP.touchFile(file, true)`
7. 根据 operation 调用对应 LSP 方法
8. 输出 JSON 字符串或“无结果”提示

这说明 LSP tool 不是自己维护协议细节，而是薄封装 `LSP` 命名空间能力。

---

# 5. 为什么 `touchFile(file, true)` 很关键

这一步很容易被忽略，但其实非常重要。

## 5.1 它做什么

`touchFile()` 会：

- 获取该文件对应的 LSP clients
- 对每个 client 发 `notify.open({ path })`
- 若 `waitForDiagnostics === true`
  - 再等待 diagnostics 到达

## 5.2 为什么需要先 open

很多 LSP server 在未收到 `textDocument/didOpen` 前：

- 不会完整建立文件上下文
- definition/reference/diagnostic 结果可能不完整

因此 OpenCode 在执行语义导航前先“触碰”文件，是一种很合理的预热策略。

## 5.3 等待 diagnostics 的意义

如果马上发 definition/reference 请求，有些 server 还没完成初始分析。

等待 diagnostics 可以提高：

- server 已准备好的概率
- 语义结果质量

这是一个很实用的 **warm-before-query** 设计。

---

# 6. `LSP.state()`：客户端池与 server 配置中心

## 6.1 维护哪些状态

`LSP.state()` 返回的核心状态包括：

- `broken: Set<string>`
- `servers: Record<string, LSPServer.Info>`
- `clients: LSPClient.Info[]`
- `spawning: Map<string, Promise<LSPClient.Info | undefined>>`

这说明 LSP 层并不是每次请求临时拉起进程，而是一个：

- **带缓存、带去重、带故障记忆的 client pool**

## 6.2 `broken`

用来记录某个 root + server 组合已经坏掉，不要重复尝试。

这避免了不停重试失败 server 导致的噪音与性能浪费。

## 6.3 `spawning`

用来记录某个 root + server 当前正在启动的 Promise。

这避免并发请求时对同一个 LSP server 重复 spawn。

这是典型的 **single-flight spawn dedupe**。

---

# 7. server 注册与配置覆盖

## 7.1 内置 server 列表

`LSP.state()` 会先把 `LSPServer` 命名空间中的内置 server 注册进 `servers`，例如：

- `deno`
- `typescript`
- `vue`
- `eslint`
- `oxlint`
- `biome`
- `gopls`
- `ruby-lsp`
- `ty`
- `pyright`
- `elixir-ls`
- `zls`
- `csharp`
- `fsharp`
- `sourcekit-lsp`
- `rust`
- `clangd`
- 等

这说明 OpenCode 的 LSP 支持面非常广。

## 7.2 config 覆盖

随后还会读取：

- `cfg.lsp`

允许：

- 禁用某 server
- 自定义 command
- 自定义 env
- 自定义 extensions
- 自定义 initialization

说明 LSP 系统不是硬编码死的，也支持项目/用户自定义 server。

---

# 8. `filterExperimentalServers()` 与实验开关

可以看到对 Python 相关 server 有实验分支：

- 若 `OPENCODE_EXPERIMENTAL_LSP_TY` 开启
  - 禁用 `pyright`
- 否则
  - 禁用 `ty`

这说明 OpenCode 把不同实验实现视为可切换的同类能力提供者，而不是让多个冲突 server 并存。

这是很干净的实验功能切换方式。

---

# 9. `getClients(file)`：按文件获取可用语义引擎

这是 LSP 层最核心的调度函数。

## 9.1 选择逻辑

对于每个 server：

1. 检查文件扩展名是否匹配
2. 通过 `server.root(file)` 决定该文件属于哪个项目根
3. 若该 root+server 已 broken，则跳过
4. 若已有已连接 client，则直接复用
5. 若已有 inflight spawn，则 await 它
6. 否则创建新的 spawn 任务

这是一套非常标准的 **root-aware LSP client routing**。

## 9.2 为什么 root 很重要

LSP server 通常以 workspace root 为作用域。

同一种语言在不同子项目下可能需要：

- 不同 tsconfig / pyproject / Cargo workspace
- 不同依赖图
- 不同 root diagnostics

所以不能简单按“语言种类”全局只启一个 server。

OpenCode 正确地采用了：

- **server type + project root**

作为 client 复用键。

---

# 10. `hasClients(file)`：轻量可用性探测

`hasClients(file)` 与 `getClients(file)` 不同，它只检查：

- 是否存在理论上可用的 server 配置
- 是否能为该文件求得 root
- 是否未标记为 broken

它不会强制 spawn client。

这使得 tool 在真正执行前可以快速判断：

- 当前文件类型是否具备 LSP 能力

---

# 11. 语义查询能力

## 11.1 `hover()`

发送：

- `textDocument/hover`

适合获取：

- 符号说明
- 类型信息
- 文档片段

## 11.2 `definition()`

发送：

- `textDocument/definition`

用于：

- 跳转定义

## 11.3 `references()`

发送：

- `textDocument/references`
- `includeDeclaration: true`

适合做：

- 使用点/调用点查找

## 11.4 `implementation()`

发送：

- `textDocument/implementation`

适合处理接口/抽象类/trait 等场景。

## 11.5 `documentSymbol()`

发送：

- `textDocument/documentSymbol`

用于获取文件内符号树。

## 11.6 `workspaceSymbol(query)`

发送：

- `workspace/symbol`

并做两层筛选：

- 只保留特定 `SymbolKind`
- 最多 10 条

说明 OpenCode 不直接把全量 symbol flood 给模型，而会做语义裁剪。

## 11.7 call hierarchy

支持：

- `prepareCallHierarchy`
- `incomingCalls`
- `outgoingCalls`

这对于理解调用关系非常有价值，尤其是大型面向对象或服务型项目。

---

# 12. `DocumentSymbol` / `Symbol` 类型建模

`LSP` 命名空间里显式建模了：

- `Range`
- `Symbol`
- `DocumentSymbol`

说明 LSP 结果在 OpenCode 中不是只做原始 JSON passthrough，而是被收敛成内部统一类型。

这为：

- OpenAPI 暴露
- SDK 类型生成
- FilePart.symbol source
- 统一 UI/rendering

提供了结构基础。

---

# 13. 与消息系统的关系：`symbol source`

此前在 `MessageV2.FilePart.source` 中已经看到：

- `type: symbol`
- `path`
- `range`
- `name`

这说明 LSP 导航并不是孤立工具，它和消息系统已有接口：

- 某些文件/引用最终可以带上 symbol-level source 信息

因此 LSP 的长期价值不仅是一次性 tool output，还包括：

- 为上下文中的符号引用建立更精准的来源模型

---

# 14. LSP server 的 root 解析策略

`LSPServer` 中大量 server 使用：

- `NearestRoot(includePatterns, excludePatterns?)`

## 14.1 基本思想

从目标文件目录向上找：

- 某些标志文件

例如：

- `package-lock.json`
- `go.mod`
- `pyproject.toml`
- `Cargo.toml`
- `Gemfile`
- `build.zig`
- `.sln`
- `compile_commands.json`

找到第一个匹配后，把其所在目录当作 root。

## 14.2 为什么这合理

不同语言生态的 workspace 根定义本来就不同。

OpenCode 没有搞统一强行约定，而是按语言生态各自的 root 习惯来做 root detection。

这非常工程化。

---

# 15. 自动下载与安装策略

`LSPServer` 的很多实现都支持：

- 如果本地缺少 server
- 且未禁用自动下载
- 就自动安装或下载所需语言服务器

例如：

- Vue server
- ESLint server
- gopls
- pyright
- elixir-ls
- zls
- clangd
- csharp-ls
- fsautocomplete

这说明 OpenCode 对 LSP 的目标不是“你自己先把所有语言服务器都装好”，而是：

- 尽可能降低语义导航能力的启动门槛

这非常符合 agent 产品体验。

## 15.1 风险与边界

当然，这也带来一些边界：

- 依赖网络
- 下载体积
- 平台兼容性
- 安装失败恢复

所以同时提供了：

- `OPENCODE_DISABLE_LSP_DOWNLOAD`

让用户能显式禁用自动下载。

---

# 16. `status()` 与 `diagnostics()`

## 16.1 `status()`

会返回当前已连接 clients 的状态：

- `id`
- `name`
- `root`
- `status`

适合用来做：

- UI 状态展示
- server health 检查

## 16.2 `diagnostics()`

会聚合所有 client 的 diagnostics。

说明 LSP 不只是导航能力，也承担静态分析/诊断能力入口。

## 16.3 `Diagnostic.pretty()`

把诊断统一格式化为：

- `SEVERITY [line:col] message`

这使诊断可直接进入文本上下文或 CLI/TUI 输出。

---

# 17. 这个模块背后的关键设计原则

## 17.1 文本检索与语义检索并存

OpenCode 没有迷信单一方案，而是让：

- `glob/grep/read` 负责文本侧 narrowing
- `lsp` 负责语义侧 navigation

## 17.2 LSP client 需要 root-aware 复用

按语言 + root 维度缓存 client，是大型多项目工作区下的正确做法。

## 17.3 预热比直接查询更可靠

`touchFile()` 先 open 再查，是提高结果质量的重要工程细节。

## 17.4 自动安装降低门槛，但要可控

OpenCode 通过 flag 让用户能在“自动准备能力”和“完全显式控制”之间选择。

## 17.5 语义结果也要进入统一类型系统

Range / Symbol / DocumentSymbol 的 schema 化，让 LSP 能与 SDK、消息系统、UI 更自然地融合。

---

# 18. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/tool/lsp.ts`
2. `packages/opencode/src/lsp/index.ts`
3. `packages/opencode/src/lsp/client.ts`
4. `packages/opencode/src/lsp/server.ts`

重点盯住这些函数/概念：

- `LspTool.execute()`
- `LSP.getClients()`
- `LSP.hasClients()`
- `LSP.touchFile()`
- `LSP.definition()`
- `LSP.references()`
- `LSP.documentSymbol()`
- `LSP.workspaceSymbol()`
- `NearestRoot()`
- `LSPServer.*.spawn()`

---

# 19. 下一步还需要深挖的问题

这一篇已经把 LSP 主框架讲清楚了，但还有一些值得继续拆的点：

- **问题 1**：`lsp/client.ts` 中 JSON-RPC 初始化、didOpen/diagnostics 协议细节还可继续精读
- **问题 2**：symbol source 是如何在 prompt part 或 FilePart 中被实际构造和消费的，还值得沿调用链继续追踪
- **问题 3**：多 server 同时命中同一文件时，结果聚合、去重与排序策略是否足够稳定
- **问题 4**：自动下载 server 的缓存、升级、失败恢复与安全边界还可继续展开
- **问题 5**：`workspaceSymbol("")` 这种空查询策略在不同 LSP server 上的行为一致性如何，还值得验证
- **问题 6**：diagnostics 是否会进入 agent 上下文或影响工具决策，还需要继续查更多调用点
- **问题 7**：LSP 与 read/grep 联动时，是否存在“先用 symbol 定位再 read 局部范围”的专门优化路径
- **问题 8**：实验中的 `ty` 与 `pyright` 切换，在 Python 大仓库下的效果差异还值得继续评估

---

# 20. 小结

`lsp_and_symbol_navigation` 模块定义了 OpenCode 的语义级代码导航能力：

- `LspTool` 向模型暴露 definition/reference/hover/symbol/call hierarchy 等操作
- `LSP` 命名空间负责按 root 发现、缓存、复用和调度语言服务器
- `touchFile()` 负责在查询前完成文件预热与诊断等待
- `LSPServer` 则负责跨语言生态的 root 检测、进程启动与必要时的自动安装

因此，这一层并不是 grep 的替代品，而是 OpenCode 从文本检索升级到语义导航的重要基础设施。
