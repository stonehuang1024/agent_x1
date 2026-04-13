# Compaction / Retry / Recovery 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 在执行失败、上下文膨胀和会话修复时走的恢复链路。

核心问题是：

- 什么情况下系统会选择 retry，而不是立即失败
- 什么情况下会转入 compaction，而不是 retry
- compaction 到底是在压缩什么
- `prune`、`overflow`、`replay`、`synthetic continue` 各自扮演什么角色
- revert 与 cleanup 如何构成另一条恢复路径

核心源码包括：

- `packages/opencode/src/session/retry.ts`
- `packages/opencode/src/session/compaction.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/session/summary.ts`
- `packages/opencode/src/session/revert.ts`
- `packages/opencode/src/session/message-v2.ts`

这一层本质上是 OpenCode 的**异常恢复、上下文自救与会话修复基础设施**。

---

# 2. 为什么恢复链路必须独立建模

agent 执行中断的原因并不只有一种：

- provider rate limit
- provider overloaded / unavailable
- 上下文窗口溢出
- 媒体附件太大
- 用户拒绝权限
- tool 执行失败
- 逻辑走偏，需要人工 revert

这些失败并不能都用同一种策略处理。

如果全部“出错即停止”：

- 自动化体验很差
- 大量临时性 provider 错误会白白中断
- 长会话无法继续推进

所以 OpenCode 把恢复分成多条路线：

- retry
- compaction
- revert/unrevert/cleanup

这是必要的 principal-level 设计。

---

# 3. 恢复路径的大分流：retry vs compaction vs stop

在 `SessionProcessor.process()` 中，捕获异常后大致有三条路：

## 3.1 retry

适用于：

- provider 暂时不可用
- rate limit
- overloaded
- 明确标记为 retryable 的 API 错误

## 3.2 compaction

适用于：

- `ContextOverflowError`
- token usage 达到可用上下文阈值

## 3.3 stop

适用于：

- 不可恢复错误
- 权限/问题被拒绝且应中断
- compaction 也失败

这就是 OpenCode 恢复体系的最高层分流器。

---

# 4. `SessionRetry.retryable()`：什么错误值得重试

`retry.ts` 的 `retryable()` 首先明确：

- `ContextOverflowError` 永不重试

这非常关键，因为上下文超限不是“再试一次就好”的问题。

## 4.1 对 `APIError` 的判断

若错误是 `MessageV2.APIError`：

- 必须 `error.data.isRetryable === true`
- 否则直接不重试

并且它还会把部分错误映射成人类可读原因：

- `FreeUsageLimitError` -> 引导充值
- `Overloaded` -> `Provider is overloaded`
- 否则用 provider message

## 4.2 对 JSON 字符串错误体的猜测

如果 error.data.message 里其实是 JSON，还会继续解析：

- `too_many_requests`
- `rate_limit`
- `exhausted`
- `unavailable`

然后映射成统一提示。

这说明 retry 判断并不只依赖 typed provider error，还兼容一些不够规整的错误返回。

---

# 5. `SessionRetry.delay()`：延迟时间的权威来源优先级

delay 计算不是纯本地 backoff，而是多级优先：

## 5.1 provider 指令优先

若 response headers 有：

- `retry-after-ms`
- `retry-after`

则优先使用。

其中 `retry-after` 既支持：

- 秒数
- HTTP date

## 5.2 本地退避兜底

否则按：

- `2000ms * 2^(attempt-1)`

若没有 headers，则 capped at 30s。

若有 headers 分支但未命中具体值，则使用指数退避原值。

这说明 OpenCode 既尊重 provider backpressure，也保留统一 backoff 策略。

---

# 6. `SessionRetry.sleep()`：可中断退避等待

`sleep(ms, signal)` 不是裸 `setTimeout`，而是：

- 挂 abort listener
- abort 时 reject `AbortError`
- timeout 到时 resolve

这意味着 retry backoff 不是不可打断的死等。

如果用户取消当前执行，sleep 会立即结束，恢复控制权。

这是恢复链路里很重要的交互细节。

---

# 7. processor 中 retry 的完整路径

在 `processor.ts` 里，若错误可重试：

1. `attempt++`
2. `delay = SessionRetry.delay(...)`
3. `SessionStatus.set(type: "retry", attempt, message, next)`
4. `await SessionRetry.sleep(delay, abort)`
5. `continue` 回到 `while(true)` 再次发起 LLM stream

## 7.1 为什么这是同一个 processor 内重试

它不是重建整个 session 或 message，而是让同一轮执行器继续推进。

因此：

- 上下文连续
- assistant message 连续
- UI 仍能理解这是同一轮中的 retry，而不是新回合

---

# 8. 为什么上下文溢出不走 retry

`ContextOverflowError` 被明确排除在 retry 之外。

原因很简单：

- rate limit 是瞬时外部条件
- context overflow 是输入规模结构性问题

再发同样请求只会继续失败。

所以系统选择：

- `needsCompaction = true`
- processor 返回 `compact`

这是典型的 root-cause 处理，而不是 band-aid retry。

---

# 9. `SessionCompaction.isOverflow()`：压缩触发阈值

这个函数不是简单比较 total tokens vs context。

## 9.1 基本逻辑

- 若 `config.compaction.auto === false`，直接禁用
- 若 model context limit 为 0，认为不可判断
- token count 优先用 `tokens.total`
- 否则用 `input + output + cache.read + cache.write`

## 9.2 预留空间

会计算：

- `reserved = config.compaction.reserved ?? min(20_000, maxOutputTokens(model))`

然后得到可用输入上限：

- 若模型显式给 `limit.input`，用 `input limit - reserved`
- 否则用 `context - maxOutputTokens(model)`

最终：

- `count >= usable` 就算 overflow

## 9.3 为什么要 reserved

因为会话还没结束时，不能把整个窗口都吃满。

必须为：

- 模型继续输出
- 系统 prompt / tool call 扩展

预留空间。

这就是 compaction 触发不是“到极限才压”，而是提前压的原因。

---

# 10. `prune()`：不是总结，而是剪掉旧工具输出

很多人会把 compaction 和 summary 混在一起，但 `prune()` 是另一条独立策略。

## 10.1 它做什么

从最近消息往前扫描，直到累计约 40k token 的 tool 输出保护区。

超过保护区且满足条件的旧 tool result，会被标记：

- `part.state.time.compacted = Date.now()`

## 10.2 它不会直接删除 part

它不是删记录，而是标记这些旧工具输出已经被 compacted/pruned。

## 10.3 保护规则

- 至少保留最近两轮 user turn
- assistant summary 之前停止
- `skill` 工具受保护，不参与 prune
- 已 compacted 的工具输出不重复处理

这说明 prune 的目标是：

- **优先清理长工具输出噪音，而不是改写整个对话语义**

---

# 11. 为什么 `skill` 被列为 protected tool

`PRUNE_PROTECTED_TOOLS = ["skill"]`

这说明 skill tool 的输出在系统中被视为高价值上下文，不适合自动裁剪。

这体现了 OpenCode 对不同工具输出价值的差异化判断，而不是无脑按长度砍。

---

# 12. `SessionCompaction.process()`：真正的压缩执行器

这个函数才是“把会话重新压成可继续上下文”的核心。

输入包括：

- `parentID`
- `messages`
- `sessionID`
- `abort`
- `auto`
- `overflow?`

它做的事情不是简单删旧消息，而是启动一个专门的 compaction assistant turn。

---

# 13. overflow 模式下的 `replay` 选择逻辑

若 `input.overflow` 为真，会尝试：

1. 找到当前 parent user message 之前最近一个 user message
2. 且这个 user message 不能已带 `compaction` part
3. 以它作为 `replay`
4. `messages = input.messages.slice(0, i)`，即截断上下文到 replay 之前

## 13.1 为什么要 replay

如果当前上下文太大，特别是因为大附件/媒体导致，单纯总结最近状态还不够。

系统需要：

- 把更早、可承载的上下文保留下来
- 然后重新把用户意图 replay 回去

这样压缩后的下一轮更容易继续有效推理。

---

# 14. `hasContent` 检查：避免压得只剩空壳

overflow 分支里还会验证：

- replay 之外是否还存在至少一条非 compaction user message

若没有，就放弃 replay，退回原 messages。

这说明 compaction 不是盲目裁剪，而要保证裁完后仍有足够语义基线。

---

# 15. compaction 不是普通 assistant，而是专门 agent + mode

`SessionCompaction.process()` 会创建一条 assistant message：

- `mode: "compaction"`
- `agent: "compaction"`
- `summary: true`

并选模型：

- 优先 `compaction` agent 自己配置的 model
- 否则沿用用户消息所在模型

这说明 compaction 是系统里的正式子 agent / 子模式，而不是隐藏函数。

---

# 16. compaction prompt 的作用：生成“继续工作所需的提示摘要”

默认 prompt 明确要求模型输出结构化总结，关注：

- Goal
- Instructions
- Discoveries
- Accomplished
- Relevant files / directories

这说明 compaction 不是抽象压缩算法，而是：

- **用 LLM 生成供下一位 agent/下一轮上下文继续工作的摘要 prompt**

这和单纯 token-level compression 完全不同。

---

# 17. 插件还能改写 compaction prompt

在 compaction 前会触发：

- `experimental.session.compacting`

plugin 可注入：

- `context`
- `prompt`

这说明 compaction 也是可扩展点，不是封闭实现。

---

# 18. compaction 使用 `SessionProcessor` 自己再跑一轮

这点非常关键。

compaction 没有自己另写一套推理器，而是：

- 创建 compaction assistant message
- 复用 `SessionProcessor.create(...)`
- 再 `processor.process(...)`

只是输入消息改成：

- 现有历史（可 strip media）
- 再加一个用户文本 prompt，要求总结
- tools 置空

这说明 compaction 是复用主执行引擎的专用模式，而不是旁路实现。

---

# 19. 为什么 compaction 要 `stripMedia: true`

传给 `MessageV2.toModelMessages(messages, model, { stripMedia: true })`

意味着压缩时会去掉媒体内容。

原因很明确：

- 触发 overflow 的常见原因之一就是大媒体输入
- 生成总结时不需要真的再把大媒体塞回模型上下文

这是一种直接针对根因的压缩策略。

---

# 20. compaction 失败时的恢复策略

如果 compaction 自己又返回：

- `compact`

系统会把 compaction assistant message 记成：

- `ContextOverflowError`
- `finish = "error"`

然后直接 `stop`。

## 20.1 为什么不无限递归压缩

因为“压缩都压不下”说明当前信息密度已经超出模型能力边界。

继续递归 compaction 只会制造噪音。

所以这里直接停止，是正确的边界控制。

---

# 21. compaction 成功后的两条恢复路线

若 compaction 返回 `continue` 且 `input.auto === true`，有两种后续：

## 21.1 有 replay

创建一条新的 user message，把原 replay user 的：

- agent
- model
- format
- tools
- system
- variant

都复制过来。

然后把 replay 的 parts 重放进去。

但若是媒体 file part，会改写成：

- `[Attached <mime>: <filename>]`

文本描述，而不重新塞入媒体。

## 21.2 无 replay

创建一条 synthetic user message，内容是：

- 如果 overflow 源于大媒体，解释媒体已从上下文移除
- 然后提示 assistant：继续下一步，或不确定时请求澄清

这说明 compaction 不只是生成 summary，还负责把会话重新推进回主执行轨道。

---

# 22. `SessionCompaction.create()`：compaction 请求本身也是会话事件

这个函数会先创建一条 user message，并写入一个：

- `compaction` part

字段包括：

- `auto`
- `overflow`

这说明“开始进行 compaction”本身也被显式写入消息历史，可供 UI 和后续逻辑识别。

---

# 23. `summary.ts` 在恢复链路中的作用

compaction / revert 都依赖 summary/diff 基础设施。

`SessionSummary.summarize()` 会：

- 重新计算 session diff
- 更新 session summary numbers
- 写 `Storage["session_diff"]`
- 发布 `Session.Event.Diff`

而 `computeDiff()` 则通过：

- 最早 `step-start.snapshot`
- 最晚 `step-finish.snapshot`

算全量 diff。

这意味着恢复操作不是只修消息文本，也要把文件状态摘要重新对齐。

---

# 24. revert：另一条恢复路径

与 retry/compaction 不同，revert 处理的是：

- 已经执行过的会话历史需要回退

`SessionRevert.revert()` 会：

1. `assertNotBusy`
2. 遍历 messages/parts 找回退点
3. 收集其后的 `patch` parts
4. `Snapshot.revert(patches)`
5. 记录 `revert.snapshot` 和 `revert.diff`
6. 重新计算 diff
7. `Session.setRevert(...)`

这说明 revert 是：

- **对已落地文件改动和消息历史的显式恢复**

---

# 25. `unrevert()` 与 `cleanup()`：恢复状态收口

## 25.1 `unrevert()`

若 session 有 `revert.snapshot`，则：

- `Snapshot.restore(snapshot)`
- `Session.clearRevert(sessionID)`

## 25.2 `cleanup()`

在最终确认回退后：

- 删除回退点之后的 messages
- 或删除回退点 message 中 partID 之后的 parts
- 发 `MessageV2.Event.Removed / PartRemoved`
- 最后 `Session.clearRevert()`

这说明 revert 不是一次动作，而是：

- 标记回退态
- 可恢复/可清理

组成的一条完整恢复子状态机。

---

# 26. 为什么 revert 前也要重新算 diff

因为会话恢复不仅是“消息删掉了”，更关键的是：

- 当前工作树与会话历史之间的 summary/diff 必须重新对齐

所以 revert 里会：

- `SessionSummary.computeDiff(rangeMessages)`
- 写 storage
- 发 `session.diff`

这保证恢复后 UI 和 share 等消费方看到的是一致状态。

---

# 27. 从恢复体系角度看，OpenCode 实际有三种不同层次的修复

## 27.1 传输层修复

- retry backoff

解决 provider 暂时不可用。

## 27.2 上下文层修复

- prune
- compaction
- replay / synthetic continue

解决 prompt 过大、上下文失衡。

## 27.3 工作树/历史层修复

- revert
- unrevert
- cleanup

解决已经执行出的错误结果或需要撤销的变更。

这是非常完整的 recovery architecture。

---

# 28. 这个模块背后的关键设计原则

## 28.1 可重试错误与结构性错误必须分开处理

rate limit 可以 retry；context overflow 必须 compact。

## 28.2 压缩不该只是丢上下文，而应生成可继续工作的高价值摘要

所以 compaction 是一个专门 LLM summary turn。

## 28.3 长工具输出应优先被剪枝

所以有 `prune()` 先降噪。

## 28.4 历史回退必须同时恢复文件系统与消息轨迹

所以 revert 绑定 snapshot/patch/diff/cleanup。

---

# 29. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/session/retry.ts`
2. `packages/opencode/src/session/processor.ts`
3. `packages/opencode/src/session/compaction.ts`
4. `packages/opencode/src/session/summary.ts`
5. `packages/opencode/src/session/revert.ts`
6. `packages/opencode/src/session/message-v2.ts`

重点盯住这些函数/概念：

- `SessionRetry.retryable()`
- `SessionRetry.delay()`
- `SessionCompaction.isOverflow()`
- `SessionCompaction.prune()`
- `SessionCompaction.process()`
- `replay`
- `stripMedia`
- `SessionRevert.revert()`
- `SessionRevert.cleanup()`

---

# 30. 下一步还需要深挖的问题

这一篇已经把恢复体系主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`MessageV2.toModelMessages()` 中 `stripMedia`、compacted tool output 和 summary assistant 的具体转换规则还值得单独精读
- **问题 2**：`Session.setRevert()` / `clearRevert()` / `setSummary()` 的完整实现与 DB 更新语义还可以继续展开
- **问题 3**：`prune()` 之后 tool output 在 prompt 构造时到底如何被忽略，还值得继续追踪 prompt builder
- **问题 4**：compaction agent 的默认 prompt 是否对不同任务类型都足够稳定，还值得从产出质量角度继续评估
- **问题 5**：auto replay 在有复杂文件/资源 part 时是否会损失太多上下文，还可继续验证
- **问题 6**：retry 状态对应的 `RetryPart` 何处真正写入消息历史，还值得继续追踪完整路径
- **问题 7**：revert 与 share 同步、summary 刷新之间是否存在时间竞态，还值得继续思考
- **问题 8**：未来若引入更长上下文模型，当前 compaction reserved/prune 阈值是否需要动态模型化，还值得关注

---

# 31. 小结

`compaction_retry_and_recovery` 模块定义了 OpenCode 如何在执行受阻时尽量不中断工作，而是选择最合适的修复路径：

- `SessionRetry` 负责 provider 瞬时失败时的可中断指数退避重试
- `SessionCompaction` 负责上下文溢出时的摘要压缩、媒体剥离与对话重放
- `prune` 负责优先削减旧工具输出噪音
- `SessionRevert` 负责对错误历史和文件改动进行显式撤销与清理

因此，这一层不是若干异常分支的拼凑，而是 OpenCode 保持长会话可持续推进、可恢复、可回滚的核心自救体系。
