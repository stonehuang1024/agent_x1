# Control Plane / Workspace Models 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 control-plane 与 workspace 模型。

核心问题是：

- workspace 在 OpenCode 中是什么
- workspace 记录了哪些元数据
- 为什么 workspace 既可能是本地 worktree，也可能是 remote adaptor
- `WorkspaceContext` 如何影响 session 查询与路由行为
- control-plane 为什么需要自己的一套 workspace server / SSE / adaptor 层

核心源码包括：

- `packages/opencode/src/control-plane/workspace.ts`
- `packages/opencode/src/control-plane/workspace.sql.ts`
- `packages/opencode/src/control-plane/workspace-context.ts`
- `packages/opencode/src/control-plane/workspace-router-middleware.ts`
- `packages/opencode/src/control-plane/workspace-server/routes.ts`

这一层本质上是 OpenCode 的**多工作区控制面与远程工作区抽象层**。

---

# 2. 为什么在 project/instance 之外还需要 workspace

前面已经看到：

- `Project` 负责项目级归属
- `Instance` 负责当前目录运行上下文

但这还不够覆盖一种更高层场景：

- 同一个 project 下存在多个可切换工作空间
- 某些 workspace 实际是 remote target
- 控制面需要把 session、事件、目录视图绑定到 workspace 维度

因此 workspace 的角色不是重复 project，而是：

- **为多工作区/远程工作区控制面增加一个独立维度**

---

# 3. `WorkspaceTable`：workspace 的持久化模型

`workspace.sql.ts` 定义的表非常简洁：

- `id`
- `type`
- `branch`
- `name`
- `directory`
- `extra`
- `project_id`

## 3.1 这说明了什么

### `project_id`

workspace 明确归属于某个 project。

### `type`

workspace 不止一种形态，必须靠 type 区分其 adaptor 语义。

### `branch`

说明 workspace 与代码分支概念可能直接相关。

### `directory`

说明 workspace 可以有自己的目录定位。

### `extra`

说明不同 workspace adaptor 可能需要不同扩展配置，所以 schema 使用 JSON 承载非通用字段。

这是一种典型的：

- **核心公共列 + adaptor-specific extra JSON**

建模方式。

---

# 4. `Workspace.Info`：统一工作区描述对象

`Workspace.Info` 来自 `WorkspaceInfo` schema，并通过 `fromRow()` 映射数据库行：

- `id`
- `type`
- `branch`
- `name`
- `directory`
- `extra`
- `projectID`

这说明 workspace 也和 session/project 一样，采用：

- row model
- runtime info model

分层方式，而不是把数据库行直接暴露给上层。

---

# 5. `Workspace.create()`：通过 adaptor 创建 workspace

这是 workspace 模型最关键的入口。

## 5.1 输入

- `id?`
- `type`
- `branch`
- `projectID`
- `extra`

## 5.2 流程

1. 生成 `WorkspaceID`
2. `getAdaptor(input.type)`
3. 先调用：
   - `adaptor.configure({ ...input, id, name: null, directory: null })`
4. 用 configure 返回结果组装 `info`
5. 写入 `WorkspaceTable`
6. 再调用：
   - `adaptor.create(config)`

## 5.3 为什么先 `configure` 再 `create`

这说明 adaptor 的职责分成两段：

- `configure`：把输入规范化成最终 workspace 描述
- `create`：真正执行底层创建动作

这种分离非常合理，因为它允许：

- 先确定持久化元数据
- 再执行具体资源创建
- 也便于不同 adaptor 共享统一的 workspace 记录结构

---

# 6. `getAdaptor(type)`：workspace 的适配层核心

虽然这次没有继续读 adaptor 实现，但从 `workspace.ts` 与 middleware 已能明确看出：

- workspace 的行为不由 `Workspace` 自身硬编码
- 而是通过 adaptor 注入

adaptor 至少负责：

- `configure`
- `create`
- `remove`
- `fetch`

这说明 workspace 控制面的真正灵活性来自：

- **workspace adaptor abstraction**

它让 OpenCode 不必把 remote/local workspace 逻辑写死在主流程中。

---

# 7. `Workspace.list(project)` / `get(id)` / `remove(id)`

## 7.1 `list(project)`

按 `project.id` 读取该项目下所有 workspace，并按 `id` 排序。

这说明 workspace 是 project-scoped 资产。

## 7.2 `get(id)`

按 ID 读取单 workspace。

## 7.3 `remove(id)`

删除时会：

1. 先读取 row
2. `getAdaptor(row.type)`
3. 调 `adaptor.remove(info)`
4. 再删数据库记录

这说明 workspace 的删除也不是纯数据库操作，而是：

- **持久化删除 + adaptor 资源删除**

---

# 8. `WorkspaceContext`：控制面过滤作用域

`workspace-context.ts` 很小，但非常关键。

它只维护一个字段：

- `workspaceID?`

并提供：

- `provide({ workspaceID, fn })`
- getter `workspaceID`

## 8.1 为什么这足够重要

因为很多上层逻辑不需要知道完整 workspace info，只需要知道：

- 当前请求是否绑定某个 workspace
- 若绑定，应在查询和路由中追加这一层过滤

这正是 `WorkspaceContext` 的职责。

## 8.2 为什么获取不到时返回 `undefined`

getter 中如果没有 context，会直接返回 undefined，而不是抛错。

这让大量业务代码可以自然写成：

- 有 workspace 就加过滤
- 没有就按普通 project 逻辑走

这是很实用的渐进式作用域叠加方式。

---

# 9. `WorkspaceContext` 如何影响 session 数据面

从之前读过的 `session/index.ts` 可见：

- `Session.list()` 在当前 `WorkspaceContext.workspaceID` 存在时
- 会追加 `eq(SessionTable.workspace_id, WorkspaceContext.workspaceID)`

这意味着 workspace 不是独立于 session 的平行系统，而是：

- **对 session 视图的附加筛选维度**

这和 control-plane 的定位非常一致：

- 工作区切换主要影响你看到和操作哪些 session

---

# 10. `WorkspaceRouterMiddleware`：远程工作区请求转发器

这是 control-plane 中最有架构意义的部分之一。

## 10.1 当前行为

中间件注释已经说明：

- 现在实际上需要转发所有请求
- 因为还没有完整同步机制
- 理想未来是非变更型 GET 可以本地处理

这说明当前 control-plane 还处于：

- **远程优先转发、同步能力逐步补齐**

的阶段。

## 10.2 路由逻辑

`routeRequest(req)` 流程：

1. 若没有 `WorkspaceContext.workspaceID`，不处理
2. 读取 workspace
3. 若不存在，返回 500
4. `getAdaptor(workspace.type)`
5. 调 `adaptor.fetch(workspace, pathname + search, { method, body, signal, headers })`

这说明 remote workspace 场景下，本地 server 实际充当：

- **workspace-aware proxy**

## 10.3 为什么重要

这使 OpenCode 可以在本地统一 API 面下，把实际请求路由到不同工作区后端，而不要求上层调用方理解所有远程差异。

---

# 11. 为什么 middleware 要受实验 flag 控制

`WorkspaceRouterMiddleware` 只有在：

- `OPENCODE_EXPERIMENTAL_WORKSPACES`

开启时才生效。

这说明多工作区控制面仍是实验特性，宿主明确保留了：

- 开关隔离
- 逐步演进

这在涉及请求转发和远程工作区时是很合理的。

---

# 12. `WorkspaceServerRoutes`：workspace 专用事件出口

`workspace-server/routes.ts` 定义了一个很小的 SSE server：

- `GET /event`

## 12.1 行为

它会：

- 监听 `GlobalBus.on("event", handler)`
- 只把 `event.payload` 发出去
- 发送 `server.connected`
- 每 10 秒发送 `server.heartbeat`

## 12.2 与全局 `/global/event` 的区别

全局 route 发送的是：

- `{ directory, payload }`

而 workspace server 发送的是：

- `payload` 本身

这说明 workspace server 更像是：

- 给远程 workspace adaptor 消费的轻量事件流

而不是给多实例控制面 UI 使用的全局聚合流。

---

# 13. `Workspace.startSyncing(project)`：远程 workspace 事件镜像器

这是 control-plane 中最关键的后台同步逻辑。

## 13.1 选择哪些 workspace 同步

会先取：

- `list(project)`
- 再 `filter((space) => space.type !== "worktree")`

这说明：

- 本地 worktree 类型不需要远程 SSE 同步
- 只有 remote/non-worktree workspace 需要建立监听循环

## 13.2 `workspaceEventLoop(space, stop)`

对每个 workspace：

1. `getAdaptor(space.type)`
2. `adaptor.fetch(space, "/event", { method: "GET", signal })`
3. 若响应失败，等待 1 秒重试
4. 若成功，`parseSSE(res.body, stop, callback)`
5. callback 中把远程事件重新发到：
   - `GlobalBus.emit("event", { directory: space.id, payload: event })`
6. 若 SSE 断开，250ms 后重连

## 13.3 这意味着什么

这实际上是在做：

- **远程 workspace event stream -> 本地 GlobalBus 事件镜像**

这是一个非常清晰的控制面同步设计。

---

# 14. 为什么 workspace 同步事件要写进 `GlobalBus`

因为一旦远程 workspace 事件进入 `GlobalBus`，它就自动获得：

- 全局 SSE 暴露能力
- 插件事件观察能力
- 多实例 UI 消费能力
- 与本地实例事件同构的处理链路

这意味着 remote workspace 不需要另起一套事件消费机制，只要：

- 先适配成 `GlobalBus` 事件

整个系统其余部分就都能复用。

这是 control-plane 设计里最漂亮的一点之一。

---

# 15. `workspace.ready` / `workspace.failed`

`Workspace.Event` 目前定义了：

- `workspace.ready`
- `workspace.failed`

虽然这次没有继续沿触发点追踪，但从命名就能看出，它们承担的是：

- 工作区准备完成
- 工作区准备失败

这类控制面生命周期信号。

也就是说，workspace 不只是静态配置记录，也有动态生命周期状态。

---

# 16. control-plane 的角色总结

把这几个模块放在一起看，可以更清楚地理解 control-plane：

## 16.1 `WorkspaceTable`

负责持久化记录 workspace 元数据。

## 16.2 `Workspace`

负责 workspace CRUD 与远程同步启动。

## 16.3 `WorkspaceContext`

负责请求级作用域注入。

## 16.4 `WorkspaceRouterMiddleware`

负责把当前请求在需要时转发到目标 workspace。

## 16.5 `WorkspaceServerRoutes`

负责提供 workspace 级 SSE 事件出口。

这说明 control-plane 不是某一个模块，而是围绕 workspace 维度组织起来的一整层控制系统。

---

# 17. 这个模块背后的关键设计原则

## 17.1 workspace 是 project 之上的操作视角，不是 project 的替代品

workspace 仍然归属于 project，但增加了 branch/type/remote 等控制面语义。

## 17.2 远程工作区应通过 adaptor 统一接入

而不是把每类远程工作区的逻辑散落在 server routes 里。

## 17.3 作用域注入要轻量

只传 `workspaceID` 就足够驱动上层过滤与转发逻辑。

## 17.4 远程事件应汇入同一全局事件主干

这样 UI、插件和外部客户端就不需要区分本地/远程来源。

---

# 18. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/control-plane/workspace.sql.ts`
2. `packages/opencode/src/control-plane/workspace.ts`
3. `packages/opencode/src/control-plane/workspace-context.ts`
4. `packages/opencode/src/control-plane/workspace-router-middleware.ts`
5. `packages/opencode/src/control-plane/workspace-server/routes.ts`
6. `packages/opencode/src/control-plane/adaptors/*`

重点盯住这些函数/概念：

- `Workspace.create()`
- `Workspace.list()`
- `Workspace.remove()`
- `Workspace.startSyncing()`
- `workspaceEventLoop()`
- `WorkspaceContext.provide()`
- `WorkspaceRouterMiddleware`
- `adaptor.fetch()`
- `parseSSE()`

---

# 19. 下一步还需要深挖的问题

这一篇已经把 control-plane 与 workspace 主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：各类 adaptor 的具体实现、能力差异与配置结构还值得单独拆文档
- **问题 2**：workspace `type = "worktree"` 的特殊含义和本地工作区行为边界还可继续精读
- **问题 3**：`parseSSE()` 的容错、重连和事件解析语义还值得继续查看源码
- **问题 4**：workspace router 目前为什么连 GET 也转发，未来本地只读同步会如何落地，还可继续分析
- **问题 5**：workspace 事件如何与 session/workspace_id 过滤配合，形成完整多工作区 UI 体验，还可继续追踪前端/TUI
- **问题 6**：workspace create/remove 的事务边界与 adaptor 失败回滚策略还值得继续确认
- **问题 7**：remote workspace 的认证、网络错误与延迟对整体 runtime 的影响还需进一步研究
- **问题 8**：control-plane 相关 schema、routes 与 SDK 暴露面是否已经完整对齐，也可继续核对

---

# 20. 小结

`control_plane_and_workspace_models` 模块定义了 OpenCode 如何把单项目 runtime 扩展成多工作区控制面：

- `WorkspaceTable` 持久化工作区元数据
- `Workspace` 负责 CRUD、adaptor 调度与远程事件同步
- `WorkspaceContext` 负责请求级作用域注入
- `WorkspaceRouterMiddleware` 负责 workspace-aware 请求转发
- `WorkspaceServerRoutes` 提供轻量事件出口

因此，这一层不是简单的 workspace 列表功能，而是 OpenCode 迈向多工作区与远程工作区控制面的关键基础设施。
