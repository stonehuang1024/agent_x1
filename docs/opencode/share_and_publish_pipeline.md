# Share / Publish Pipeline 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 session share / publish 链路。

核心问题是：

- session 的 `share_url` 是如何创建与维护的
- 自动分享与手动分享分别如何触发
- share 数据是如何持续同步到远端的
- 为什么 share 同步不是每次事件都立即发请求
- account / org 状态如何影响分享后端接口选择
- share 的外部可见性边界由哪些机制控制

核心源码包括：

- `packages/opencode/src/share/share-next.ts`
- `packages/opencode/src/session/index.ts`
- `packages/opencode/src/server/routes/session.ts`
- `packages/opencode/src/session/session.sql.ts`
- `packages/opencode/src/project/bootstrap.ts`

这一层本质上是 OpenCode 的**会话对外发布与增量同步基础设施**。

---

# 2. 分享功能在系统中的定位

OpenCode 的分享不是简单复制一个 URL。

它背后至少包含几层能力：

- 创建远端 share 资源
- 将本地 session 绑定到远端 share id/secret/url
- 在后续 session/message/part/diff 变化时持续同步
- 根据账号/组织状态决定走哪个 API 面
- 在 unshare/remove 时清理远端资源和本地关联

所以 share 更准确地说是一套：

- **发布 + 增量镜像同步**

机制。

---

# 3. `share_url`：session 持久化模型中的分享出口

在 `session.sql.ts` 中，session 表直接包含：

- `share_url`

这说明对上层 UI 和 API 来说，session 是否已分享的最直观信号就是：

- 是否存在 `share.url`

而在 `Session.fromRow()` 中，它会被映射成：

- `share = row.share_url ? { url } : undefined`

所以分享状态本身已经是 session info 的正式组成部分，而不是外挂元数据。

---

# 4. `ShareNext`：当前分享实现的职责边界

从实现看，`ShareNext` 已经承担正式 share 管道职责：

- request 组装
- create
- sync
- remove
- fullSync
- 事件订阅初始化

这说明它不是简单工具函数，而是：

- **share runtime manager**

---

# 5. 两套 API 端点：legacy vs console

`ShareNext` 内部定义了两套 endpoint：

- `legacyApi = /api/share`
- `consoleApi = /api/shares`

## 5.1 为什么会有两套

因为分享后端会根据当前是否处于：

- 有 active account + active org

而切换。

这说明 OpenCode 同时支持：

- 旧式/默认分享后端
- 组织控制台风格的分享后端

而运行时会根据账号上下文自动选择。

---

# 6. `request()`：分享请求上下文组装器

这是 share 系统的关键函数。

它返回：

- `headers`
- `api`
- `baseUrl`

## 6.1 无 active org 时

若 `Account.active()` 不存在 active org：

- `baseUrl = config.enterprise.url ?? "https://opncd.ai"`
- `api = legacyApi`
- `headers = {}`

## 6.2 有 active org 时

则：

- 读取 `Account.token(active.id)`
- `authorization: Bearer <token>`
- `x-org-id = active.active_org_id`
- `api = consoleApi`
- `baseUrl = active.url`

## 6.3 意义

这说明 share 系统的真正后端目标，并不是固定写死，而是：

- **账号上下文驱动的控制面选择**

这与前面 account/provider/config 体系是联动的。

---

# 7. `OPENCODE_DISABLE_SHARE`：总开关

如果：

- `OPENCODE_DISABLE_SHARE === true/1`

则整个 `ShareNext` 会被禁用。

这意味着分享能力被视为：

- 可整体关闭的外部可见性通路

这是合理的安全与部署边界设计。

---

# 8. `ShareNext.init()`：分享不是手动轮询，而是事件驱动同步

`init()` 会订阅这些事件：

- `Session.Event.Updated`
- `MessageV2.Event.Updated`
- `MessageV2.Event.PartUpdated`
- `Session.Event.Diff`

并分别调用：

- `sync(sessionID, [data...])`

## 8.1 这说明什么

Share 系统并不是在用户点击分享后一次性上传完毕就结束。

相反，它会在 session 生命周期中持续监听本地变化，并把这些变化同步到远端 share。

所以 share 更像：

- **live mirrored published session**

---

# 9. 为什么 `MessageV2.Event.Updated` 还会额外同步 model

当 message 更新且角色是 user 时，会额外同步：

- `type: "model"`
- 通过 `Provider.getModel(providerID, modelID)` 获取 model 信息

## 9.1 原因

用户消息本身会绑定当时使用的模型选择。

若只同步 message 文本，而不同步 model 元信息，远端 share 侧就无法完整还原这条消息的语义上下文。

因此 share 数据不仅包含会话文本，还包含：

- 与会话回放/展示有关的模型信息

---

# 10. `create(sessionID)`：分享创建流程

## 10.1 流程

1. 若 disabled，返回空结构
2. `request()` 决定 `baseUrl / headers / api`
3. `POST ${baseUrl}${api.create}`，body 为 `{ sessionID }`
4. 校验响应
5. 得到 `{ id, url, secret }`
6. 写入 `SessionShareTable`
7. 触发 `fullSync(sessionID)`
8. 返回结果

## 10.2 为什么要本地存 `id + secret + url`

- `id`：远端 share 资源标识
- `secret`：后续 sync/remove 的授权凭证
- `url`：供 UI/API 暴露

所以本地 share 表其实是：

- **远端分享资源的本地控制句柄**

---

# 11. `Session.share()`：session 层如何接入分享创建

在 `session/index.ts` 中：

- 会先检查 `cfg.share === "disabled"`
- 然后动态 import `ShareNext`
- 调 `ShareNext.create(id)`
- 再把 `share.url` 写回 session 表的 `share_url`
- 最后通过 `Bus.publish(Session.Event.Updated, { info })` 广播

## 11.1 关键点

真正的远端分享资源由 `ShareNext` 创建。

而 session 表只保存：

- 面向上层显示的 `share_url`

这种分层很合理：

- session 负责业务视图
- share 表负责远端控制信息
- ShareNext 负责网络同步

---

# 12. 自动分享：session 创建时的触发条件

在 `Session.create()` 中可以看到：

- 若不是 fork 出来的 child session
- 且 `Flag.OPENCODE_AUTO_SHARE` 为真，或 `cfg.share === "auto"`
- 就会 `share(result.id).catch(() => {})`

## 12.1 含义

OpenCode 支持：

- 手动分享
- 自动分享

并且自动分享是配置/flag 驱动的。

## 12.2 为什么 child session 不默认 share

fork / child session 往往更临时或更内部化，不一定都适合自动公开。

这说明系统对分享默认面是有边界控制的。

---

# 13. `sync(sessionID, data[])`：为什么不是立刻发请求

这是 share 管道里非常关键的工程细节。

## 13.1 队列结构

`ShareNext` 维护：

- `queue: Map<sessionID, { timeout, data: Map<string, Data> }>`

## 13.2 合并逻辑

如果当前 session 已有待同步队列：

- 新数据不会立即发请求
- 而是按 `key(item)` 合并进 `Map`

键规则例如：

- `session`
- `message/<id>`
- `part/<messageID>/<id>`
- `session_diff`
- `model`

## 13.3 延迟发送

第一次入队时会启动一个 1 秒定时器；到时：

1. 取出当前队列
2. 找到 share 句柄
3. `POST ${api.sync(share.id)}`
4. body 带：
   - `secret`
   - `data: Array.from(queued.data.values())`

## 13.4 为什么这是好设计

这实现的是：

- **per-session debounce + dedupe batching**

好处包括：

- 避免每个 part/message 更新都单独打网络请求
- 多次更新同一 message/part 只保留最新版本
- 流式 session 场景下分享同步更平滑

这是非常工程化且必要的设计。

---

# 14. `key(item)`：增量同步的去重基础

每类分享数据都有稳定 key：

- session -> `session`
- message -> `message/<id>`
- part -> `part/<messageID>/<id>`
- session_diff -> `session_diff`
- model -> `model`

这意味着 share 同步语义不是 append-only event log，而更接近：

- **latest-state patch set**

这也解释了为什么远端更容易保持最终一致，而不会被大量重复中间状态淹没。

---

# 15. `fullSync(sessionID)`：为什么创建分享后还要全量同步

创建分享后，`ShareNext.create()` 会立即调用：

- `fullSync(sessionID)`

## 15.1 它收集什么

- `Session.get(sessionID)`
- `Session.diff(sessionID)`
- `Array.fromAsync(MessageV2.stream(sessionID))`
- 从用户消息里提取唯一模型集合

最终构造：

- session
- messages
- parts
- session_diff
- models

然后统一走 `sync(...)`。

## 15.2 为什么必须 full sync

因为仅仅创建远端 share 资源，还不代表它拥有本地会话的完整内容。

全量同步能确保分享刚创建时，远端就已经拥有：

- 可回放的最小完整会话状态

之后再靠增量 `sync()` 追平变化。

因此整个分享模型是：

- **首次 full snapshot + 后续 incremental sync**

---

# 16. `remove(sessionID)`：取消分享流程

`ShareNext.remove()` 会：

1. 找本地 share 句柄
2. `DELETE ${api.remove(share.id)}`
3. body 带 `secret`
4. 删除本地 `SessionShareTable` 记录

这说明取消分享不是只删本地 URL，而是会正式通知远端资源删除。

---

# 17. `Session.unshare()` 与 session 删除的关系

在 `session/index.ts` 中：

- `Session.unshare()` 会调用 `ShareNext.remove(id)`
- 然后把 `share_url` 置空
- 再广播 `Session.Event.Updated`

而 `Session.remove(sessionID)` 删除 session 时，还会：

- `await unshare(sessionID).catch(() => {})`

这说明系统努力保证：

- session 删除时不会留下远端 orphaned share

这是很重要的数据清理边界。

---

# 18. `InstanceBootstrap()`：分享同步为何是实例启动时能力

在 `project/bootstrap.ts` 中：

- `ShareNext.init()` 会在 instance bootstrap 时调用

这意味着一旦实例启动并存在已分享 session，该实例就会自动订阅后续变更并持续同步。

分享不是某个独立任务线程，而是 runtime 的正式组成部分。

---

# 19. 与 HTTP routes 的关系

虽然本次读到的 `server/routes/session.ts` 片段还没展开到 share 路由段，但从 session 层实现可以明确知道：

- 对外 share/unshare 最终会落到 `Session.share()` / `Session.unshare()`
- HTTP route 只是它们的调用入口

所以 authoritative 分享逻辑不在 route，而在 session + ShareNext 组合层。

---

# 20. 外部可见性边界由什么控制

这一层的对外暴露边界主要由几件事共同决定：

## 20.1 配置与环境开关

- `cfg.share === "disabled"`
- `OPENCODE_DISABLE_SHARE`
- `OPENCODE_AUTO_SHARE`

## 20.2 账号上下文

- 是否有 active account
- 是否有 active org
- 是否拿到 org token

## 20.3 远端控制句柄

- `share.id`
- `share.secret`

## 20.4 session 生命周期清理

- unshare
- remove session 时自动 unshare

这说明 OpenCode 并不是无条件公开会话，而是通过多层开关、认证和 secret 机制控制分享边界。

---

# 21. 这个模块背后的关键设计原则

## 21.1 分享应被建模成正式远端资源，而不是一次性导出

所以本地会保存 share id / secret / url，并做后续同步。

## 21.2 初次发布与后续同步应分阶段处理

`create + fullSync + incremental sync` 是合理分层。

## 21.3 高频更新必须做批处理去重

否则流式会话会把分享后端打爆。

## 21.4 分享后端应服从账号/组织控制面

这样团队环境下分享语义才能与控制台体系一致。

---

# 22. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/share/share-next.ts`
2. `packages/opencode/src/session/index.ts`
3. `packages/opencode/src/session/session.sql.ts`
4. `packages/opencode/src/server/routes/session.ts`
5. `packages/opencode/src/account/index.ts`

重点盯住这些函数/概念：

- `ShareNext.request()`
- `ShareNext.init()`
- `ShareNext.create()`
- `ShareNext.sync()`
- `ShareNext.fullSync()`
- `ShareNext.remove()`
- `Session.share()`
- `Session.unshare()`
- `share_url`
- `SessionShareTable`

---

# 23. 下一步还需要深挖的问题

这一篇已经把 share/publish 主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`SessionShareTable` 的具体 schema、唯一约束和迁移历史还值得单独查看
- **问题 2**：远端 `/api/share` 与 `/api/shares` 两套接口在 payload 和权限上的差异还可继续确认
- **问题 3**：share sync 失败后的重试策略目前较轻，是否会导致长时间漂移，还值得评估
- **问题 4**：远端 share 数据如何被消费、展示和鉴权，还需要继续看服务端/SDK 代码
- **问题 5**：auto share 在企业/组织环境下的默认策略是否还受其他配置影响，还可继续梳理
- **问题 6**：share secret 的本地存储安全性与清理策略还值得继续分析
- **问题 7**：fork session、archive session 与 share 同步之间是否存在额外边界，还可继续验证
- **问题 8**：message/part 压缩或 revert 后，对已分享远端的最终一致性是否完全可靠，还值得继续检查

---

# 24. 小结

`share_and_publish_pipeline` 模块定义了 OpenCode 如何把本地 session 发布成可外部访问的 share 资源，并在后续变化中持续保持同步：

- `ShareNext` 负责远端资源创建、请求上下文选择、增量队列同步和删除
- `Session` 负责把 share URL 纳入正式会话模型并提供 share/unshare 入口
- account/org 上下文决定分享走 legacy 还是 console 控制面
- 事件驱动与批处理合并保证流式会话下的同步效率与一致性

因此，这一层不是简单复制链接功能，而是 OpenCode 会话发布、外部可见性和持续镜像同步能力的基础设施。
