# Session State Machine / Status Transitions 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的会话状态机与生命周期迁移。

核心问题是：

- session 运行态为什么单独由 `SessionStatus` 管，而不是直接塞进 session 表
- `idle / busy / retry` 三种状态如何切换
- 事件流里的 `session.created / updated / deleted / diff / error / status / compacted` 各自承担什么语义
- `time_created / time_updated / time_compacting / time_archived` 这些持久化时间字段与运行态状态之间如何分工
- 执行、回滚、归档、压缩、删除分别如何改变 session 生命周期

核心源码包括：

- `packages/opencode/src/session/status.ts`
- `packages/opencode/src/session/index.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/session/compaction.ts`
- `packages/opencode/src/session/retry.ts`
- `packages/opencode/src/session/revert.ts`
- `packages/opencode/src/session/session.sql.ts`

这一层本质上是 OpenCode 的**会话运行态与持久化生命周期双轨状态机**。

---

# 2. 为什么要区分“运行态状态”和“持久化生命周期”

Session 有两类完全不同的状态：

## 2.1 运行态

例如：

- 当前是否正在跑模型
- 是否处于 retry backoff 中
- 是否空闲

这类状态：

- 高频变化
- 强依赖当前进程内存
- 不一定需要长期持久化

## 2.2 生命周期态

例如：

- 创建时间
- 更新时间
- 是否 archived
- 是否正在 compacting
- 是否有 revert 信息

这类状态：

- 要写数据库
- 要在跨重启后保留
- 是 session 实体的一部分

所以 OpenCode 采用双轨设计：

- `SessionStatus` 管运行态
- `SessionTable` 管持久化生命周期

这是非常合理的分层。

---

# 3. `SessionStatus.Info`：运行态只有三种

`status.ts` 中定义的运行态非常克制：

- `idle`
- `busy`
- `retry`

其中 `retry` 还带：

- `attempt`
- `message`
- `next`

## 3.1 为什么没有更多状态

OpenCode 没把 every internal nuance 都暴露成 session status，比如：

- compacting
- blocked
- tool-running
- reverting

这些都在别的层表达，而不是污染顶层运行态接口。

这说明 `SessionStatus` 的目标是：

- **对 UI / API 暴露最小必要运行态**

---

# 4. `SessionStatus` 是 per-instance 内存态

`SessionStatus` 用：

- `Instance.state(() => Record<string, Info>)`

维护状态。

这意味着：

- 状态与当前实例目录绑定
- 默认不落库
- 会随进程/实例生命周期消失

## 4.1 为什么这合理

运行态本来就应该反映“当前进程是否正在执行某 session”。

如果写数据库，反而容易出现：

- 进程退出后残留 busy 状态
- 跨实例竞争导致陈旧状态

因此用 instance memory 管理是正确选择。

---

# 5. `SessionStatus.set()`：状态变更 = 内存更新 + Bus 事件

`set(sessionID, status)` 会：

1. `Bus.publish(SessionStatus.Event.Status, { sessionID, status })`
2. 若是 `idle`：
   - 同时发 deprecated `session.idle`
   - 删除内存缓存项
3. 否则把状态写入内存 map

## 5.1 这说明什么

`idle` 不会长驻在 map 里。

系统把：

- “没记录”
- “显式 idle”

视为等价。

这样内存 map 只保存非空闲会话，更简洁也更高效。

---

# 6. `SessionStatus.get()` 与 `list()` 的语义

- `get(sessionID)`：若无记录，默认返回 `idle`
- `list()`：返回当前实例中所有非 idle 的 session 状态

这使得 status API 很适合：

- 单 session 状态轮询
- 全局查看当前活跃/retrying session

---

# 7. 执行引擎如何驱动状态迁移

`processor.ts` 是运行态迁移的核心驱动器。

## 7.1 `start` -> `busy`

当 stream 收到 `start`：

- `SessionStatus.set(sessionID, { type: "busy" })`

## 7.2 retryable error -> `retry`

当捕获到 retryable error：

- `attempt++`
- `delay = SessionRetry.delay(...)`
- `SessionStatus.set({ type: "retry", attempt, message, next })`

## 7.3 fatal error -> `idle`

不可恢复错误时：

- `SessionStatus.set(sessionID, { type: "idle" })`

这意味着最典型路径是：

- `idle -> busy -> idle`
- `idle -> busy -> retry -> busy -> ... -> idle`

---

# 8. 为什么 `busy` 不单独记录“哪个 tool 正在跑”

虽然 processor 内部清楚当前有哪些 tool part 正在运行，但 `SessionStatus` 没把这些细节带出去。

原因很可能是：

- tool 细节已经在 message parts 中可见
- 顶层状态只需表示“会话正在工作中”
- 避免重复状态源导致不一致

这说明系统采用的是：

- 顶层 status 看粗粒度
- message parts 看细粒度

---

# 9. `Session.Event`：session 实体级事件

在 `session/index.ts` 中，Session 定义了这些核心事件：

- `session.created`
- `session.updated`
- `session.deleted`
- `session.diff`
- `session.error`

## 9.1 它们与 `SessionStatus.Event.Status` 的区别

`Session.Event.*` 表示：

- session 这个业务实体发生了什么变化

`SessionStatus.Event.Status` 表示：

- 当前运行态如何变化

这两套事件分别描述：

- 持久化业务对象变化
- 临时运行状态变化

---

# 10. `session.created` / `updated` / `deleted`

## 10.1 created

`createNext()` 插入数据库后发布：

- `Session.Event.Created`

## 10.2 updated

大量更新行为都会发布：

- `touch()`
- `share/unshare`
- `setTitle()`
- `setArchived()`
- 其他 row 更新逻辑

## 10.3 deleted

删除 session 时会发布删除事件。

这说明 UI/同步层真正应该订阅的是 session event，而不是自己读数据库轮询。

---

# 11. `session.diff`：会话级文件差异事件

`summary.ts` 和 `revert.ts` 中都可见：

- 计算 diffs 后写 `Storage.write(["session_diff", sessionID], diffs)`
- 再 `Bus.publish(Session.Event.Diff, { sessionID, diff })`

这说明 diff 被建模成：

- 会话派生状态
- 有持久化副本
- 也有实时事件

这对 share 同步、UI diff 面板和回滚链路都很重要。

---

# 12. `session.error`：运行失败的业务事件出口

processor 在两类场景发 `Session.Event.Error`：

- context overflow
- fatal assistantMessage error

这说明 error 不只是一条日志，而是正式的 session-level event，供上层统一处理。

---

# 13. `SessionCompaction.Event.Compacted`

`compaction.ts` 还定义了：

- `session.compacted`

在 compaction 完成且没有 fatal error 时发布。

## 13.1 为什么它不并入 `session.updated`

因为 compaction 是高阶生命周期动作：

- 它会生成 summary assistant message
- 可能 replay user message
- 改写后续上下文结构

这比一般 metadata update 更值得单独建事件。

---

# 14. 数据库里的持久化时间字段

`session.sql.ts` 中除了标准时间戳，还明确有：

- `time_created`
- `time_updated`
- `time_compacting`
- `time_archived`

## 14.1 `time_created`

session 首次创建时间。

## 14.2 `time_updated`

session 业务更新时间，由 `touch()` 和其他更新路径维护。

## 14.3 `time_archived`

归档时间，由 `setArchived()` 设置/清除。

## 14.4 `time_compacting`

表示会话处于 compacting 生命周期阶段的持久化时间位。

虽然这次没展开到它的所有写入路径，但 schema 已经说明 compaction 被视为正式生命周期状态，而非纯运行态标记。

---

# 15. 为什么 archived 是持久化字段，而不是 `SessionStatus`

归档不是“当前正在做什么”，而是“这个 session 现在处于什么长期业务状态”。

因此它适合：

- 存数据库
- 出现在 `Session.Info.time.archived`

而不适合进 `SessionStatus.Info`。

这正体现了运行态/生命周期态的边界。

---

# 16. revert 如何改变生命周期

`revert.ts` 中：

- `SessionPrompt.assertNotBusy(sessionID)` 先禁止 busy 时回滚
- 回滚过程中会改 `session.revert`
- 重新计算 diff 并发布 `Session.Event.Diff`
- 删除/裁剪 message 与 part

这说明 revert 不是 runtime status，而是 session 历史结构和 diff 状态的正式改变。

因此它主要反映在：

- session row 的 `revert`
- message/part 历史变化
- diff event

---

# 17. 为什么回滚前要 `assertNotBusy`

这在状态机上很重要。

如果一个 session 正在：

- LLM streaming
- tool execution
- retry/backoff

时去执行 revert，会造成历史基线和执行流并发冲突。

因此系统明确要求：

- 只有非 busy 的 session 才能做 revert/unrevert

这是一条很关键的不变量。

---

# 18. compaction 与状态机的关系

compaction 涉及两个层次：

## 18.1 运行时触发原因

- processor 检测 overflow
- 触发 `compact` 分支

## 18.2 生命周期结果

- 创建 compaction user/assistant messages
- 发布 `session.compacted`
- 可能 replay 用户消息或生成 synthetic continue
- 改变后续上下文结构

所以 compaction 不是简单 status，而是：

- **状态机中的恢复路径 + 生命周期重写动作**

---

# 19. retry 状态为什么单独带 `message` 与 `next`

`retry` 状态结构里不仅有 attempt，还有：

- `message`
- `next`

这让 UI/调用方可以直接展示：

- 当前在重试什么原因
- 下次重试时间

因此 `retry` 不是抽象布尔值，而是具备可操作信息的运行态。

---

# 20. `SessionRetry.retryable()` 如何决定状态机会不会进 retry

只有满足这些条件才会进入 retry 路线：

- 不是 `ContextOverflowError`
- 对 `APIError` 来说必须 `isRetryable === true`
- 或 JSON 错误体命中 rate limit / unavailable / exhausted 等模式

否则就直接进入 fatal error / idle。

这说明是否发生 `busy -> retry`，不是通用异常都能触发，而是被精确限制的。

---

# 21. `SessionRetry.delay()` 如何影响状态时间轴

delay 计算优先级为：

1. `retry-after-ms`
2. `retry-after` 秒数
3. `retry-after` HTTP date
4. 指数退避

若无 headers，则：

- `RETRY_INITIAL_DELAY * 2^(attempt-1)`
- capped at 30s

这意味着 retry 状态机既支持 provider 指令驱动，也支持本地 backoff 策略。

---

# 22. 一个典型 session 状态时间线

可以把最常见执行过程画成：

## 22.1 正常完成

- create session
- `session.created`
- `session.updated`
- user 发起执行
- `session.status = busy`
- assistant parts 持续写入
- summary/diff 更新
- `session.status = idle`
- `session.updated`

## 22.2 遇到可重试错误

- `idle -> busy`
- provider rate limit
- `busy -> retry`
- 到点后重新发起 stream
- `retry -> busy`
- 最后成功或失败回 `idle`

## 22.3 上下文溢出

- `idle -> busy`
- 捕获 `ContextOverflowError`
- 发布 `session.error`
- processor 返回 `compact`
- 进入 compaction 生命周期动作
- 发布 `session.compacted`

---

# 23. 为什么 status 事件和 entity 事件都需要存在

如果只有 entity 事件：

- UI 很难实时知道某 session 正在重试还是忙碌

如果只有 status 事件：

- 就无法表达 share/title/archive/diff/revert 这些实体变化

所以两套事件并存是必要的：

- `SessionStatus.Event.Status` 管运行态
- `Session.Event.*` 管持久化实体变化

这是一种很清晰的事件分层。

---

# 24. 这个模块背后的关键设计原则

## 24.1 运行态必须轻量、易失、进程内维护

因此 `SessionStatus` 不落库。

## 24.2 生命周期态必须持久化并纳入正式 schema

因此有 `time_archived`、`time_compacting`、`revert`、`share_url` 等字段。

## 24.3 事件流应同时覆盖运行态和实体态

这样 UI、share、同步、调试都能订阅各自需要的信息。

## 24.4 高阶恢复动作不应被压扁成普通 status

compaction、revert 就是典型例子。

---

# 25. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/session/status.ts`
2. `packages/opencode/src/session/index.ts`
3. `packages/opencode/src/session/processor.ts`
4. `packages/opencode/src/session/retry.ts`
5. `packages/opencode/src/session/compaction.ts`
6. `packages/opencode/src/session/revert.ts`
7. `packages/opencode/src/session/session.sql.ts`

重点盯住这些函数/概念：

- `SessionStatus.set()`
- `SessionStatus.Event.Status`
- `Session.Event.Created/Updated/Diff/Error`
- `SessionCompaction.Event.Compacted`
- `SessionRetry.retryable()`
- `SessionRetry.delay()`
- `setArchived()`
- `assertNotBusy()`

---

# 26. 下一步还需要深挖的问题

这一篇已经把 session 状态机主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`time_compacting` 的完整写入/清理路径还值得继续追踪，确认它与 `session.compacted` 事件的精确关系
- **问题 2**：idle 状态是否在所有正常完成路径都显式写回，还值得继续核对更完整调用链
- **问题 3**：多实例同时观察同一 session 时，内存态 `SessionStatus` 的可见性边界还值得专门分析
- **问题 4**：archived session 是否仍允许执行、share、revert 等操作，还值得继续查上层约束
- **问题 5**：compaction 过程中 UI 应该显示 busy 还是专门 compacting，目前从状态层看并未区分，这个产品语义值得思考
- **问题 6**：retry part 与 retry status 之间是否还有更紧密映射，还可继续看 message 写入路径
- **问题 7**：deprecated `session.idle` 还有哪些订阅方，是否仍在系统中被使用，还值得继续 grep
- **问题 8**：如果进程在 busy/retry 中崩溃，恢复后的状态可见性与补偿策略如何处理，还需要继续观察 bootstrap/recovery 逻辑

---

# 27. 小结

`session_state_machine_and_status_transitions` 模块定义了 OpenCode 如何把会话运行态与持久化生命周期拆开管理，并通过事件流把这两层重新连接起来：

- `SessionStatus` 负责 `idle / busy / retry` 的轻量运行态
- `Session` 事件负责 created/updated/diff/error 等实体变化
- `SessionTable` 字段承载 archived、compacting、revert、share 等长期生命周期信息
- processor、retry、compaction、revert 则共同驱动状态迁移

因此，这一层不是单纯的状态枚举，而是 OpenCode 会话执行、恢复、归档与事件传播机制的核心组织方式。
