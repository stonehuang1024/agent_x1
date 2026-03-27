# Project / Instance / Workspace Scoping 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 `Project`、`Instance`、workspace 作用域体系。

核心问题是：

- `Instance` 在 OpenCode 中到底是什么
- `directory` 和 `worktree` 为什么要分开
- project 边界如何被推导出来
- 为什么很多状态都挂在 `Instance.state(...)` 上
- session、tool、prompt、server route 为什么都依赖当前 instance 上下文
- workspace 作用域又是如何叠加到 project/session 上的

核心源码包括：

- `packages/opencode/src/project/instance.ts`
- `packages/opencode/src/project/project.ts`
- `packages/opencode/src/session/index.ts`
- `packages/opencode/src/control-plane/workspace-context.ts`
- `packages/opencode/src/control-plane/workspace.sql.ts`

这一层本质上是 OpenCode 的**目录边界与多实例作用域基础设施**。

---

# 2. 为什么 OpenCode 需要 `Instance`

OpenCode 不是只服务一个固定仓库。

它可能同时面对：

- 不同工作目录
- 不同 project
- 同一个 project 下不同子目录
- 多 workspace 控制面
- HTTP API 带目录 header 的远程调用

如果所有模块都直接读全局单例目录，就会出现：

- 状态串台
- session/project 混乱
- tool 权限边界错误
- 多工作区无法并存

因此 OpenCode 需要一个明确的运行时作用域对象：

- `Instance`

---

# 3. `Instance` 的上下文字段

`project/instance.ts` 里定义的上下文很简单：

- `directory`
- `worktree`
- `project`

## 3.1 `directory`

表示当前运行请求/会话所绑定的工作目录。

它不一定等于仓库根。

## 3.2 `worktree`

表示更大的工作树根目录，通常接近项目根或 sandbox 根。

## 3.3 `project`

表示持久化 project 实体：

- project id
- worktree
- vcs 等信息

这三个字段一起定义了 OpenCode 当前运行作用域。

---

# 4. `directory` 与 `worktree` 的区别

这是理解 OpenCode 作用域设计的关键。

## 4.1 `directory`

更像：

- 当前用户正在操作的 cwd
- 当前 session 绑定目录
- 当前 prompt/tool 的直接工作目录

## 4.2 `worktree`

更像：

- 项目或仓库级边界
- snapshot / project / config upward search 的根参考
- 更大的允许访问空间

## 4.3 为什么不能只保留一个目录

因为很多真实场景下：

- 用户从仓库子目录启动 agent
- 但 project/snapshot/config/LSP root 应该以更大工作树为准
- 同时 external directory 权限又不能把 worktree 内子路径误判为外部目录

所以必须把：

- 操作目录
- 项目边界目录

分开表示。

---

# 5. `Project.fromDirectory(...)`：实例引导入口

`Instance.boot(...)` 在未显式给出 `project/worktree` 时，会调用：

- `Project.fromDirectory(input.directory)`

并得到：

- `project`
- `sandbox`

然后映射成：

- `project`
- `worktree: sandbox`
- `directory: input.directory`

这说明 project 发现与 sandbox/worktree 推导是 instance 引导的一部分。

也就是说，`Instance` 不是生造上下文，而是通过 `Project` 层从真实目录结构推导出来的。

---

# 6. `Instance.provide()`：作用域注入器

## 6.1 调用语义

`Instance.provide({ directory, init?, fn })` 会：

1. 解析并规范化 `directory`
2. 查看 cache 中是否已有该 directory 对应实例
3. 没有则 `boot(...)`
4. 拿到 ctx 后，通过 `context.provide(ctx, fn)` 执行逻辑

这说明 `Instance.provide()` 本质上是：

- **按目录建立并注入运行时上下文**

## 6.2 为什么它很重要

很多模块都直接使用：

- `Instance.directory`
- `Instance.worktree`
- `Instance.project`

这些 getter 能成立的前提，就是外层调用先进入了 `Instance.provide()`。

因此 `Instance.provide()` 是几乎所有目录作用域代码的根入口。

---

# 7. instance cache：按目录缓存运行上下文

`instance.ts` 内部维护：

- `cache: Map<string, Promise<Context>>`

键是规范化后的 directory。

## 7.1 为什么 cache Promise 而不是直接 cache Context

因为 instance 初始化可能是异步的：

- 发现 project
- 初始化 state
- 跑 init hooks

缓存 Promise 可以自然支持：

- 并发请求同时等待同一个 instance 初始化结果

避免重复 boot。

## 7.2 `track()` 的作用

`track(directory, next)` 会：

- 在 Promise 失败时自动把 cache 项删除

这防止失败初始化把坏状态永久留在 cache 里。

这是很好的失败恢复设计。

---

# 8. `Instance.state(...)`：实例级状态分区机制

`Instance.state(init, dispose?)` 最终调用：

- `State.create(() => Instance.directory, init, dispose)`

这说明很多模块里的 `Instance.state(...)` 都是在做同一件事：

- 按 `Instance.directory` 分片保存状态

## 8.1 这意味着什么

例如这些模块都可能各自有实例级状态：

- ToolRegistry
- Config.state
- Plugin.state
- Session 状态
- LSP.state
- InstructionPrompt.state
- Bus.state

它们并不是全局共享单例，而是：

- **每个 directory 一份状态副本**

这正是 OpenCode 能支持多实例并存的关键。

---

# 9. `containsPath(filepath)`：权限边界判断辅助函数

`Instance.containsPath()` 的语义很关键：

- 如果路径在 `Instance.directory` 内，返回 true
- 否则如果 `worktree === "/"`，直接 false
- 否则如果路径在 `Instance.worktree` 内，返回 true

## 9.1 为什么要同时检查 directory 和 worktree

因为：

- 有些路径虽然不在当前 cwd 子树内
- 但仍在当前项目工作树内
- 这种路径不应被当作 external directory

## 9.2 为什么 `worktree === "/"` 要特殊处理

源码注释已经点明：

- 非 git 项目可能把 worktree 设为 `/`
- 如果直接拿 `/` 做 contains，会导致任何绝对路径都被视为内部路径

这会破坏 external_directory 权限边界。

所以这里有一个非常重要的安全特判。

---

# 10. `reload()` / `dispose()` / `disposeAll()`：实例生命周期管理

## 10.1 `reload()`

对指定 directory：

- `State.dispose(directory)`
- `InstanceState.dispose(directory)`
- 删除 cache
- 重新 boot
- 发送 disposed 事件

这说明 reload 不是局部热修，而是完整地重建该目录实例状态。

## 10.2 `dispose()`

释放当前 instance：

- dispose 状态
- 删 cache
- 发 dispose 事件

## 10.3 `disposeAll()`

遍历所有 cache entries，逐一在其上下文中执行 `Instance.dispose()`。

这说明 OpenCode 是明确支持多实例资源管理的，而不是只假定单实例运行。

---

# 11. `emit(directory)`：为什么实例销毁事件走 `GlobalBus`

`Instance.emit()` 直接往 `GlobalBus` 发：

- `server.instance.disposed`

这说明 instance 生命周期事件不仅是本地模块关注的事，也要让：

- 全局 SSE
- 多工作区 UI
- 外部控制面

知道某个目录实例已失效。

因此 instance 生命周期天然属于全局观察面。

---

# 12. session 与 instance 的绑定关系

从 `session/index.ts` 可以清楚看到：

- `Session.create()` 默认 `directory: Instance.directory`
- `projectID: Instance.project.id`
- `plan()` 依赖 `Instance.project.vcs` 与 `Instance.worktree`
- `list()` 默认按 `Instance.project.id` 过滤

这说明 session 从创建到查询都深度绑定当前 instance。

## 12.1 为什么重要

这意味着 session 不是全局匿名聊天记录，而是：

- 归属于某 project
- 在某 directory 下创建
- 在某 workspace 视图中查询

因此 session 的目录作用域不是附加属性，而是其 identity 的一部分。

---

# 13. workspace 作用域如何叠加到 session 查询

在 `session/index.ts` 的 `list()` 中还能看到：

- 若 `WorkspaceContext.workspaceID` 存在
- 则附加 `eq(SessionTable.workspace_id, WorkspaceContext.workspaceID)`

这说明 workspace 不是替代 project/instance，而是额外一层过滤作用域。

换句话说：

- project 决定更大资源归属
- instance 决定当前目录上下文
- workspace 决定控制面视图/选择范围

这三层是叠加而非互斥关系。

---

# 14. `ProjectInfo` 与 global session 视图

`Session.listGlobal()` 不再只看当前 project，而是全局列 session，并关联：

- `ProjectTable.id`
- `ProjectTable.name`
- `ProjectTable.worktree`

最终返回：

- `GlobalSession`
- 其中带 `project` 摘要

这说明 OpenCode 在更高层控制面上也承认：

- session 是 project-scoped 资产

全局视图只是把多个 project-scoped session 聚合显示。

---

# 15. `SystemPrompt.environment()` 如何利用 instance 信息

在 `session/system.ts` 中，system prompt 会注入：

- `Working directory: ${Instance.directory}`
- `Workspace root folder: ${Instance.worktree}`
- `Is directory a git repo: ${project.vcs === "git" ? "yes" : "no"}`

这说明 instance/project 作用域不仅影响后端逻辑，也直接进入模型上下文。

换句话说，模型看到的“环境信息”本质上就是 instance 作用域的文本化投影。

---

# 16. tool / plugin 为什么依赖 instance 上下文

很多工具和插件都直接把 instance 信息塞进执行上下文：

- `ToolRegistry.fromPlugin()` 给 plugin tool ctx 注入 `directory/worktree`
- `write/read/edit` 等工具以 `Instance.directory` 解析相对路径
- prompt / snapshot / config / instruction / lsp 都依赖 `Instance.directory/worktree`

这意味着 instance 是整个 runtime 的公共地基，不只是 project 模块内部概念。

---

# 17. 为什么说 `Instance` 是“作用域容器”而不是“项目对象”

很容易把 `Instance` 理解成 `Project` 的别名，但其实不是。

## 17.1 `Project`

更偏持久化实体：

- project id
- worktree
- vcs
- 数据库存储归属

## 17.2 `Instance`

更偏运行时容器：

- 当前 directory
- 当前 worktree
- 当前 project
- 当前实例级 state 分片
- 生命周期管理

所以 `Instance` 是围绕“当前执行作用域”组织的，而不是围绕“项目元数据”组织的。

---

# 18. 这个模块背后的关键设计原则

## 18.1 运行时作用域必须显式建模

否则多目录、多工作区、多 session 都会混乱。

## 18.2 工作目录与项目根应分离

`directory` 与 `worktree` 分离是很多权限与导航逻辑成立的前提。

## 18.3 实例级状态必须按目录隔离

`Instance.state(...)` 让大量模块可以天然获得 per-directory state partition。

## 18.4 workspace 是附加过滤层，不替代 project/instance

这样多工作区控制面才不会破坏底层 project 归属模型。

---

# 19. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/project/instance.ts`
2. `packages/opencode/src/project/project.ts`
3. `packages/opencode/src/session/index.ts`
4. `packages/opencode/src/control-plane/workspace-context.ts`
5. `packages/opencode/src/control-plane/workspace.sql.ts`

重点盯住这些函数/概念：

- `Instance.provide()`
- `Instance.state()`
- `Instance.containsPath()`
- `Instance.reload()`
- `Instance.dispose()`
- `Project.fromDirectory()`
- `WorkspaceContext.workspaceID`
- `Session.create()`
- `Session.list()`
- `Session.listGlobal()`

---

# 20. 下一步还需要深挖的问题

这一篇已经把 instance/project/workspace 作用域主框架讲清楚了，但还有一些地方值得继续展开：

- **问题 1**：`project/project.ts` 中 project 发现、sandbox 计算、VCS 判定的完整算法还值得继续精读
- **问题 2**：`WorkspaceContext` 是如何从 HTTP header / control-plane middleware 注入的，还可继续追踪
- **问题 3**：`InstanceState` 与 `State` 两层销毁的职责差异还可继续梳理
- **问题 4**：同一 project 下多个 directory instance 并存时，哪些状态共享、哪些状态隔离，还值得更系统地确认
- **问题 5**：non-git 项目下 `worktree = "/"` 的更多上下游影响还可继续检查
- **问题 6**：`ProjectTable` 与 `WorkspaceTable` 的持久化关系还适合在下一篇 control-plane 文档中继续展开
- **问题 7**：路径解析、external_directory 权限与 instance contains 之间是否存在边缘 case，还值得继续验证
- **问题 8**：server 路由如何在多工作区/多目录调用下选中正确 instance，还可继续追路由 middleware

---

# 21. 小结

`project_instance_and_workspace_scoping` 模块定义了 OpenCode 如何在多目录、多项目、多工作区环境下稳定组织 runtime：

- `Project` 负责项目级归属与工作树发现
- `Instance` 负责当前运行上下文与实例级状态隔离
- `directory` 与 `worktree` 共同定义操作边界与项目边界
- `workspace` 则作为控制面上的额外过滤层叠加在 session 等数据之上

因此，这一层不是简单 cwd 管理，而是 OpenCode 整个 runtime 目录作用域模型的基础设施。
