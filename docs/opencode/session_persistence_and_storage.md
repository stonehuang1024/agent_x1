# Session Persistence / Storage 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的持久化与存储层。

核心问题是：

- session/message/part 分别如何落库
- 为什么消息与 part 要拆成不同表
- project、workspace、session、message、part 之间的关系是什么
- 哪些索引是为哪些访问模式服务的
- OpenCode 的 SQLite 层做了哪些运行时优化
- 为什么持久化模型与 runtime 结构强耦合但仍保持清晰边界

核心源码包括：

- `packages/opencode/src/session/session.sql.ts`
- `packages/opencode/src/session/index.ts`
- `packages/opencode/src/storage/db.ts`

这一层本质上是 OpenCode 的**状态持久化底座**。

---

# 2. 为什么 OpenCode 需要正式存储层

OpenCode 不是单次请求式 AI 调用器，而是：

- 长生命周期 session 系统
- 支持 message history
- 支持 tool state
- 支持 fork / child session
- 支持 todo
- 支持 compaction / summary
- 支持 revert / patch
- 支持分享与恢复

如果没有正式持久化层，这些能力都很难稳定实现。

因此 storage 在 OpenCode 中不是可选配件，而是 runtime 的核心基础设施。

---

# 3. 数据库技术选型与运行时配置

## 3.1 技术栈

从 `storage/db.ts` 可见：

- 底层数据库：`bun:sqlite`
- ORM：`drizzle-orm/bun-sqlite`
- migration：`drizzle-orm/bun-sqlite/migrator`

也就是说，OpenCode 采用的是：

- **本地 SQLite + Drizzle schema-first 管理**

这非常适合桌面/CLI/本地 agent runtime 场景。

## 3.2 数据库文件路径

`Database.Path` 会根据安装 channel 决定数据库文件：

- 默认：`Global.Path.data/opencode.db`
- 某些 channel 下：`opencode-<channel>.db`

这说明不同发布 channel 之间可以隔离数据库，避免互相污染状态。

## 3.3 SQLite PRAGMA 优化

数据库初始化时会设置：

- `journal_mode = WAL`
- `synchronous = NORMAL`
- `busy_timeout = 5000`
- `cache_size = -64000`
- `foreign_keys = ON`
- `wal_checkpoint(PASSIVE)`

这些设置的意图很明确：

- **WAL**：提高并发读写体验
- **NORMAL**：在性能与安全之间折中
- **busy_timeout**：降低锁冲突时的瞬时失败概率
- **cache_size**：提高读性能
- **foreign_keys**：确保级联删除与关系完整性

这说明 OpenCode 的数据库层不是默认配置直上，而是考虑了 agent runtime 的实际负载特征。

---

# 4. migration 机制

## 4.1 两种 migration 来源

`Database.Client()` 启动时会读取 migration entries：

- 若打包环境有 `OPENCODE_MIGRATIONS`
  - 使用 bundled migrations
- 否则
  - 从 `migration/` 目录扫描

这说明 OpenCode 同时支持：

- 开发环境按目录 migrations
- 发布环境内嵌 migrations

## 4.2 migration 条目结构

每条 migration 包括：

- `sql`
- `timestamp`
- `name`

并按 timestamp 排序执行。

这是很标准的有序 migration 日志模型。

## 4.3 `OPENCODE_SKIP_MIGRATIONS`

若开启该 flag，会把 migration SQL 替换成：

- `select 1;`

说明系统也预留了某些极端调试/兼容场景下跳过真正 schema 迁移的能力。

---

# 5. 顶层关系模型

从 `session.sql.ts` 可以梳理出主要关系：

- `project` -> `session`
- `session` -> `message`
- `message` -> `part`
- `session` -> `todo`
- `project` -> `permission`

其中：

- session 归属于 project
- message 归属于 session
- part 归属于 message，同时记录 session_id
- todo 归属于 session
- permission 归属于 project

这是一套非常清晰的层级结构。

---

# 6. `SessionTable`：会话级状态

## 6.1 字段结构

`SessionTable` 关键字段包括：

- `id`
- `project_id`
- `workspace_id`
- `parent_id`
- `slug`
- `directory`
- `title`
- `version`
- `share_url`
- `summary_additions`
- `summary_deletions`
- `summary_files`
- `summary_diffs`
- `revert`
- `permission`
- `time_compacting`
- `time_archived`
- timestamps

## 6.2 这些字段说明了什么

### `project_id`

说明 session 是项目作用域的，而不是全局散乱记录。

### `workspace_id`

说明在 control plane / multi-workspace 场景下，session 可以再挂接到 workspace 维度。

### `parent_id`

支持 session 分叉/层级结构。

### `share_url`

说明 session 可以被分享。

### `summary_*`

说明 session 层保存了一份整体 diff 摘要统计，而不仅仅依赖 message 级 patch。

### `revert`

说明 session 层直接记录当前 revert 状态引用。

### `permission`

说明 session 可以有自己的 permission overrides。

### `time_compacting` / `time_archived`

说明 compaction 与归档都是正式 session 生命周期状态，而不是临时内存标志。

## 6.3 索引

`SessionTable` 上的索引：

- `session_project_idx`
- `session_workspace_idx`
- `session_parent_idx`

这正好服务了几个常见查询：

- 按项目列 session
- 按 workspace 列 session
- 查 child sessions

这说明索引设计是明显围绕产品访问路径来的。

---

# 7. `MessageTable`：消息级状态

## 7.1 存储策略

`MessageTable` 并没有把所有 message 字段拆成很多列，而是：

- `id`
- `session_id`
- timestamps
- `data: json`

其中 `data` 类型是：

- `Omit<MessageV2.Info, "id" | "sessionID">`

这说明 OpenCode 对 message 采用的是：

- **关键关系字段列化 + 具体内容 JSON 化**

## 7.2 为什么这样做合理

message 的结构虽然明确，但字段种类较多，而且未来可能演化。

把核心关系键单独做列：

- 有利于索引和 join

把其余结构放 JSON：

- 降低 schema 频繁变更成本
- 保持与 `MessageV2` runtime schema 对齐

这是一种非常适合复杂对话状态的折中设计。

## 7.3 索引

`message_session_time_created_id_idx`

按：

- `session_id`
- `time_created`
- `id`

建索引。

这非常贴合消息历史读取模式：

- 按 session 拉时间序历史

---

# 8. `PartTable`：细粒度执行状态

## 8.1 存储策略

`PartTable` 字段包括：

- `id`
- `message_id`
- `session_id`
- timestamps
- `data: json`

其中 `data` 类型是：

- `Omit<MessageV2.Part, "id" | "sessionID" | "messageID">`

## 8.2 为什么 part 表单独存在

如果把 parts 全塞进 message JSON 里，会遇到：

- streaming delta 更新困难
- 单个 tool part 状态更新代价高
- 无法高效按 message/part 顺序读取
- UI/processor 很难增量更新

单独建 `PartTable` 后，就能：

- 独立 update 某个 part
- 流式写 delta
- 精细管理 tool 状态
- 按 message 查询 part 列表

这就是为什么 OpenCode 的 structured state 能稳定工作。

## 8.3 为什么 part 还要再存 `session_id`

即便 part 已通过 `message_id` 能间接关联 session，表里仍直接存 `session_id`。

原因很可能有两个：

- 按 session 查询 part 更方便
- 某些统计/清理/事件逻辑可少做 join

这是典型的受控冗余换查询便利。

## 8.4 索引

- `part_message_id_id_idx`
- `part_session_idx`

分别服务：

- 查某条 message 的 part 列表
- 按 session 做 part 范围查询/清理/统计

---

# 9. `TodoTable`：session 内计划状态

## 9.1 字段设计

- `session_id`
- `content`
- `status`
- `priority`
- `position`
- timestamps

主键：

- `(session_id, position)`

## 9.2 为什么 position 是主键一部分

这说明 todo 列表不是无序集合，而是有明确排序的任务列表。

OpenCode 把 todo 视为：

- 某个 session 下的有序工作计划

而不是简单 checkbox 集合。

---

# 10. `PermissionTable`：项目级批准状态

## 10.1 字段设计

- `project_id` 主键
- timestamps
- `data: json` -> `PermissionNext.Ruleset`

## 10.2 说明了什么

权限批准状态是 project 级别，而不是 session 级别。

这很合理，因为很多 `always allow` 的语义更接近：

- 对当前项目长期授权

而不是只对某一次会话有效。

虽然当前部分落盘逻辑仍有 TODO，但 schema 已经表明了设计意图。

---

# 11. `Session.Info` 与 `SessionTable` 的映射

## 11.1 `fromRow()`

`Session.fromRow()` 把数据库行转换成 runtime `Session.Info`：

- 把 `summary_*` 聚合成 `summary`
- 把 `share_url` 包成 `share`
- 把时间列包成 `time`
- 把 `permission` / `revert` 直接带上

## 11.2 `toRow()`

`Session.toRow()` 则做反向映射。

这说明 session 层不是把 ORM row 暴露给上层，而是显式维护：

- row model
- runtime model

之间的转换边界。

这是非常好的分层做法。

---

# 12. `Session.create()` / `fork()` 暗示的持久化语义

## 12.1 `create()`

创建 session 时，会绑定：

- `directory = Instance.directory`
- 可选 `parentID`
- 可选 `permission`
- 可选 `workspaceID`

说明 session 一创建就已经带上了：

- 工作目录上下文
- 父子关系
- 可选权限覆盖
- workspace 作用域

## 12.2 `fork()`

`fork()` 会：

- 基于原 session 新建 child/fork session
- 复制原有消息与 parts（截到某 messageID 之前）
- 建立新的 messageID 映射

这说明 fork 不是逻辑引用，而是**物理复制一段历史到新 session**。

这样做的好处是：

- fork 后的新分支可以独立继续演化
- 不会与原 session 历史耦合更新

---

# 13. 事务与 effect 机制

## 13.1 `Database.use()`

提供一个统一入口：

- 若当前上下文已有 tx，则复用
- 否则直接用全局 client

## 13.2 `Database.transaction()`

若当前没有 transaction context：

- 新开一个 transaction
- 用 `Context` 注入 `{ tx, effects }`

这说明 OpenCode 的数据库层支持：

- **隐式事务上下文传播**

## 13.3 `Database.effect()`

可将某些副作用回调登记到当前 transaction context 中，待事务完成后再执行。

这是一种很实用的模式，能避免：

- DB 尚未 commit，外部副作用已提前发生

虽然当前未在这里展开具体调用点，但这个设计本身说明 OpenCode 很在意一致性边界。

---

# 14. JSON 存储策略的意义

OpenCode 很多复杂状态都放在 JSON 字段中，例如：

- message data
- part data
- summary_diffs
- revert
- permission ruleset

这说明它的 schema 策略是：

- 关系边界清晰的用显式列
- 变化快、层级深、与 runtime schema 对齐的内容用 JSON

这是非常适合 agent runtime 的存储模型。

因为 agent state 往往：

- 结构复杂
- 类型丰富
- 演化速度快

纯第三范式未必是最优选择。

---

# 15. 存储层背后的关键访问模式

从索引和表设计可以反推 OpenCode 的主要访问模式：

- 按 project/workspace 列出 sessions
- 按 session 读取消息历史
- 按 message 读取 parts
- 按 session 查询 todo
- 按 parent 查 child sessions
- 按 project 获取 permission rules

这些访问模式都和产品功能直接对齐，而不是抽象数据库理论先行。

---

# 16. 这个模块背后的核心设计原则

## 16.1 状态必须可恢复

session/message/part 的拆分让 OpenCode 可以从数据库重建完整会话状态。

## 16.2 结构化状态优先于纯文本日志

存储层直接对齐 runtime 的结构化 state，而不是只记聊天文本。

## 16.3 关系与 JSON 混合建模

核心关联键、时间戳、索引列化；复杂 payload JSON 化。

这是对 agent runtime 非常实用的折中。

## 16.4 存储层为产品访问模式服务

索引设计不是抽象好看，而是明确服务于：

- 会话列表
- 历史回放
- part 增量更新
- fork
- todo
- permission

---

# 17. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/storage/db.ts`
2. `packages/opencode/src/session/session.sql.ts`
3. `packages/opencode/src/session/index.ts`
4. `packages/opencode/src/session/message-v2.ts`

重点盯住这些函数/概念：

- `Database.Client()`
- `migrate()`
- `Database.use()`
- `Database.transaction()`
- `SessionTable`
- `MessageTable`
- `PartTable`
- `TodoTable`
- `PermissionTable`
- `Session.fromRow()`
- `Session.toRow()`
- `Session.create()`
- `Session.fork()`

---

# 18. 下一步还需要深挖的问题

这一篇已经把存储主模型讲清楚了，但还有一些地方值得继续拆：

- **问题 1**：`Session.messages()`、`MessageV2.stream()`、`MessageV2.page()` 的具体 SQL 查询与分页/排序策略还可继续精读
- **问题 2**：part delta 更新是如何落到数据库中的，还可以继续沿 `Session.updatePartDelta()` 追踪
- **问题 3**：Storage 层与文件附件/二进制资源的关系是什么，是否有额外 blob/文件存储体系
- **问题 4**：session share/revert/summary 等字段的完整生命周期与更新点还可以单独拆开
- **问题 5**：workspace_id 在 control-plane 场景中的真实查询路径与作用边界还值得继续确认
- **问题 6**：fork 时 message/part 复制的完整算法、性能代价与一致性边界还可继续分析
- **问题 7**：数据库 effect 延迟执行机制在哪些关键路径被使用，还可以继续追踪
- **问题 8**：随着 session 数量与 part 数量增长，当前 SQLite 索引策略的性能上界在哪里，还可进一步评估

---

# 19. 小结

`session_persistence_and_storage` 模块定义了 OpenCode 如何把 agent runtime 的复杂状态稳定落盘：

- `SessionTable` 承载会话级元信息与生命周期状态
- `MessageTable` 承载消息级高层状态
- `PartTable` 承载细粒度执行与内容片段
- `TodoTable` 与 `PermissionTable` 分别承载会话计划与项目级授权状态
- `Database` 层则负责 SQLite 初始化、迁移、事务和上下文传播

因此，这一层不是普通聊天记录存储，而是 OpenCode 可恢复、可分叉、可审计 agent runtime 的状态基础设施。
