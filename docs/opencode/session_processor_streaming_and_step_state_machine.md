# Session Processor / Streaming / Step State Machine 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 `SessionProcessor`：也就是单轮 assistant 执行时，如何消费 LLM 流式事件、落库 message parts、驱动 tool 生命周期，并在 step 边界上决定继续、停止还是压缩。

核心问题是：

- `SessionProcessor.process()` 为什么要自己维护一个 while-loop 状态机
- LLM `fullStream` 的事件是如何被翻译成 `MessageV2` parts 的
- text、reasoning、tool、step 各类 part 的生命周期如何组织
- `doom_loop` 检测、retry、compaction、blocked stop 是如何接入主循环的
- 为什么 processor 最终只返回 `continue | stop | compact`

核心源码包括：

- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/session/llm.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/message-v2.ts`
- `packages/opencode/src/session/summary.ts`
- `packages/opencode/src/snapshot/index.ts`

这一层本质上是 OpenCode 的**单轮执行状态机、流式事件归档器与工具调用协调器**。

---

# 2. 为什么需要 `SessionProcessor`

`LLM.stream()` 只负责：

- 向 provider 发起流请求
- 拼 system/message/tool 参数
- 返回 provider 级流式事件

但真正产品运行时还需要：

- 持久化 reasoning/text/tool/step parts
- 更新 assistant message 的 token/cost/finish/error
- 在 tool 调用与拒绝时改变控制流
- 记录 snapshot/patch
- 触发 summary
- 处理 retry / compaction

所以必须有一层专门把“provider stream”翻译成“session runtime state”。

这就是 `SessionProcessor` 的职责。

---

# 3. `create()`：processor 绑定单条 assistant message

`SessionProcessor.create(input)` 的输入固定绑定：

- `assistantMessage`
- `sessionID`
- `model`
- `abort`

这说明 processor 处理的是：

- **某一条 assistant message 的一次执行生命周期**

而不是整个 session 的总调度器。

session 级调度仍在 `SessionPrompt.loop()`。

---

# 4. processor 内部维护的四个核心状态

创建时会维护：

- `toolcalls: Record<string, ToolPart>`
- `snapshot?: string`
- `blocked = false`
- `attempt = 0`
- `needsCompaction = false`

## 4.1 含义

这些状态恰好对应单轮执行中最重要的四个维度：

- 正在运行中的 tool call 映射
- 当前 step 的起始 snapshot
- 是否因权限/问题拒绝而阻断
- 是否需要 retry
- 是否需要 compaction

---

# 5. 为什么 `process()` 里还有一个 `while (true)`

`process(streamInput)` 内层并不是只消费一次 stream 就结束，而是包了一层：

- `while (true)`

原因是它要支持：

- retryable error 后重试

也就是说：

- provider stream 失败不一定等于本轮结束
- 可能会 sleep 后重新发同一轮请求

所以需要一个上层状态机循环，而不是一次性 `for await`。

---

# 6. processor 的最终返回值为什么只有三种

末尾只有：

- `return "compact"`
- `return "stop"`
- `return "continue"`

这说明 processor 的职责不是决定下一个 prompt 内容，而只是向上游 `SessionPrompt.loop()` 报告：

- 需要压缩上下文
- 当前链路应停止
- 可以继续下一轮 loop

非常清晰。

---

# 7. `start` 事件：会话状态进入 busy

`fullStream` 遇到：

- `type === "start"`

就会：

- `SessionStatus.set(sessionID, { type: "busy" })`

这说明 processor 是 session status 的第一责任人。

一旦真正开始流式执行，就把 session 标记为忙碌。

---

# 8. reasoning part 生命周期

reasoning 相关事件包括：

- `reasoning-start`
- `reasoning-delta`
- `reasoning-end`

实现方式：

## 8.1 start

- 创建 `reasoning` part
- 初始 `text = ""`
- 记录 `time.start`
- 保存 provider metadata
- 放进 `reasoningMap[id]`

## 8.2 delta

- 追加 `text`
- `Session.updatePartDelta(field: "text", delta)`

## 8.3 end

- `trimEnd()`
- 补 `time.end`
- 最终 `Session.updatePart`
- 从 `reasoningMap` 删除

## 8.4 含义

processor 允许多个 reasoning stream 片段并行存在，因此用 `id -> part` map 管理，而不是单一 currentReasoning。

---

# 9. text part 生命周期

文本输出则是：

- `text-start`
- `text-delta`
- `text-end`

与 reasoning 不同，这里只维护一个：

- `currentText`

## 9.1 start

- 创建 `text` part
- 记录 start time

## 9.2 delta

- 追加文本
- `Session.updatePartDelta`

## 9.3 end

- `trimEnd()`
- 触发 `experimental.text.complete`
- 用插件返回结果覆盖 `currentText.text`
- 再写最终 part

## 9.4 含义

text output 被视为主 assistant 可见输出流，因此保留一个串行 currentText 即可。

而 reasoning 允许 provider 以多段/多 lane 输出，故结构不同。

---

# 10. `experimental.text.complete`：text 完结后仍允许插件修正

在 `text-end` 时会触发：

- `Plugin.trigger("experimental.text.complete", { sessionID, messageID, partID }, { text })`

这说明文本输出落库前还有一次最终变换机会，比如：

- 清洗
- 格式修正
- 注入后处理

processor 本身不关心具体做什么，只负责把这个 hook 插在正确生命周期点上。

---

# 11. tool part 生命周期总览

tool 相关事件包括：

- `tool-input-start`
- `tool-input-delta`
- `tool-input-end`
- `tool-call`
- `tool-result`
- `tool-error`

processor 对它们的处理构成一条完整 tool state machine。

---

# 12. 为什么 `tool-input-start` 先创建 `pending` tool part

在 `tool-input-start` 时，会立刻创建：

- `type: "tool"`
- `state.status = "pending"`
- `input = {}`
- `raw = ""`

并按 `callID` 放进 `toolcalls` 映射。

## 12.1 含义

模型一旦开始生成 tool input，系统就先为这个调用占位。

这样即使后面 tool 调用失败或中断，消息历史里也不会缺失这次调用的轨迹。

---

# 13. `tool-input-delta` / `tool-input-end` 当前为何几乎空实现

这两个事件目前只 `break`。

这说明当前持久化模型更关心：

- tool call 最终结构化输入

而不是中间参数 token-by-token 的生成过程。

换句话说，processor 保留了事件接口，但暂时不把 tool input streaming 细节写进 part。

---

# 14. `tool-call`：pending -> running 的状态跃迁

一旦收到 `tool-call`：

- 找到 `toolcalls[value.toolCallId]`
- 更新 part：
  - `status = running`
  - `input = value.input`
  - `time.start = now`
  - `metadata = providerMetadata`

这时 tool part 才真正进入可执行状态。

---

# 15. doom loop 检测为什么放在 `tool-call` 时机

在 tool part 切换为 running 后，processor 会取该 assistant message 的所有 parts，检查最后三个 part 是否：

- 都是 tool
- toolName 相同
- 状态不是 pending
- `state.input` JSON 完全一致

若命中，就触发：

- `PermissionNext.ask({ permission: "doom_loop", ... })`

## 15.1 这时机很合理

因为只有在 tool 真正被执行前，系统才能判断“又是同一个工具 + 同一个输入”。

如果更早判断，输入还不完整。

如果更晚判断，循环已经发生更多次。

---

# 16. `tool-result`：running -> completed

收到 `tool-result` 后，如果匹配到 running tool part，则写入：

- `status = completed`
- `input`
- `output = value.output.output`
- `metadata = value.output.metadata`
- `title = value.output.title`
- `attachments = value.output.attachments`
- `time.end`

然后从 `toolcalls` map 删除。

## 16.1 含义

processor 统一规定了“一个工具调用结束后，在消息历史中保留什么信息”。

tool 扩展只需要返回标准 output 对象，不必自己管持久化结构。

---

# 17. `tool-error`：running -> error

收到 `tool-error` 时，如果对应 part 还在 running：

- 写入 `status = error`
- `input`
- `error = value.error.toString()`
- `time.end`

然后如果错误属于：

- `PermissionNext.RejectedError`
- `Question.RejectedError`

则：

- `blocked = shouldBreak`

最后从 `toolcalls` map 删除。

## 17.1 这说明什么

tool 错误不是一刀切：

- 普通工具错误 -> 记录错误，模型可能继续
- 权限/提问被拒 -> 可能阻断整个 loop

这正是执行控制流和 tool part 生命周期耦合的地方。

---

# 18. `error` 事件：直接进入 catch

当 stream 抛出：

- `type === "error"`

processor 直接 `throw value.error`。

这意味着 provider/transport/严重逻辑异常不在事件 switch 内局部消化，而是统一交给外层 catch 处理 retry / stop 逻辑。

这种分层很清楚。

---

# 19. step 边界生命周期

step 相关事件包括：

- `start-step`
- `finish-step`

它们把一轮 LLM+tool 交互划成一个明确边界。

## 19.1 `start-step`

- `snapshot = await Snapshot.track()`
- 写 `step-start(snapshot)` part

## 19.2 `finish-step`

- 计算 usage / cost / tokens
- 更新 assistantMessage.finish、cost、tokens
- 写 `step-finish(snapshot, tokens, cost, reason)`
- 若起始 snapshot 存在，则写 patch part
- 触发 `SessionSummary.summarize()`
- 判断是否需要 compaction

step 事件是 processor 最重要的“轮次完成”信号。

---

# 20. finish-step 为什么负责 assistant message 的 token/cost/finish 更新

因为只有到 step 完成时，usage / finishReason 才是可信最终值。

所以 processor 在这里统一更新：

- `assistantMessage.finish`
- `assistantMessage.cost`
- `assistantMessage.tokens`

这保证 message 顶层汇总字段与 parts 细节保持同步。

---

# 21. 为什么 compaction 判定放在 finish-step 后

processor 会在 `finish-step` 后检查：

- 当前 message 还不是 summary message
- `SessionCompaction.isOverflow({ tokens, model })`

若为真，则：

- `needsCompaction = true`

## 21.1 含义

是否要压缩上下文，取决于这一轮完成后产生的真实 token 使用情况。

因此 finish-step 是最自然的判断点。

---

# 22. outer catch：retry / overflow / fatal error 的总入口

任何 stream 内抛出的异常都会进外层 `catch (e)`。

处理逻辑分三类：

## 22.1 Context overflow

- `MessageV2.ContextOverflowError.isInstance(error)`
- `needsCompaction = true`
- 发布 `Session.Event.Error`

## 22.2 Retryable error

- `SessionRetry.retryable(error)` 返回消息
- `attempt++`
- 计算 delay
- `SessionStatus.set(type: "retry", attempt, message, next)`
- `SessionRetry.sleep(delay, abort)`
- `continue` 回 while-loop 重试

## 22.3 Fatal error

- `assistantMessage.error = error`
- 发布 `Session.Event.Error`
- `SessionStatus.set(idle)`

这就是单轮执行的错误状态机。

---

# 23. 为什么 retry 是 processor 而不是 LLM 层处理

因为 retry 不只是网络重发，还涉及：

- session status 显示为 retry
- attempt 计数
- 与 abort 信号协作
- 与 message/part 生命周期协调

这些都属于 session runtime，而不是 provider adapter 该关心的事情。

所以放在 processor 层是对的。

---

# 24. finally-ish 收尾逻辑非常关键

无论成功、失败还是 break，processor 在跳出 try/catch 后都会做几件事：

## 24.1 若 `snapshot` 仍未清空

- 仍计算 `Snapshot.patch(snapshot)`
- 有文件则写 patch part

## 24.2 清理未完成 tool part

对所有状态不是 `completed/error` 的 tool part：

- 统一改成 `error`
- `error = "Tool execution aborted"`

## 24.3 补 assistant 完成时间

- `assistantMessage.time.completed = Date.now()`
- `Session.updateMessage(assistantMessage)`

## 24.4 决定最终返回值

- `compact`
- `stop`
- `stop` if assistant has error
- else `continue`

这段逻辑保证无论哪条路径退出，消息状态都不会悬空。

---

# 25. 为什么要把 dangling tool calls 统一改成 error

如果 stream 中断后保留 `pending/running` tool part：

- UI 会显示永远执行中
- message history 会不一致
- 某些 provider 在后续历史里要求 tool_use/tool_result 成对，会出问题

因此统一收敛为 error 是必要的尾处理。

---

# 26. `blocked` 的语义

`blocked` 只在：

- `PermissionNext.RejectedError`
- `Question.RejectedError`

等“用户拒绝继续”的场景中被置位。

最终如果 `blocked` 为真：

- processor 返回 `stop`

这说明 blocked 不是普通错误，而是：

- **出于用户交互决策导致的主动停止**

和 provider/API error 的语义不同。

---

# 27. `SessionPrompt.loop()` 如何消费 processor 返回值

虽然主逻辑在 `prompt.ts`，但从职责分工看：

- `continue` -> loop 可能进入下一轮工具-模型迭代
- `compact` -> 进入 compaction 路径
- `stop` -> 当前轮停止

这说明 processor 是 loop 的下层执行引擎，而不是最终 orchestration owner。

---

# 28. `LLM.stream()` 与 processor 的边界

可以明确拆分为：

## 28.1 `LLM.stream()`

负责：

- provider/model/options/system/tools/messages 投影
- provider stream 建立
- tool repair / headers / params 等 provider-aware 逻辑

## 28.2 `SessionProcessor.process()`

负责：

- 消费流事件
- 持久化 parts/messages
- 管理工具状态机
- 处理 retry/compaction/blocked
- 记录 snapshot/summary

这是非常健康的分层。

---

# 29. 一个完整的单轮执行数据流

可以概括为：

## 29.1 创建 assistant message

由 `SessionPrompt.loop()` 完成。

## 29.2 创建 processor

- 绑定该 assistant message

## 29.3 启动 `LLM.stream()`

- 获取 `fullStream`

## 29.4 processor 消费事件

- start -> busy
- reasoning/text -> 持久化输出
- tool-* -> tool state machine
- start-step/finish-step -> snapshot、patch、summary、usage
- error -> retry or fatal

## 29.5 尾处理

- 修补未完成工具
- 写 assistant 完成时间
- 返回 `continue/stop/compact`

这就是 OpenCode 单轮 assistant runtime 的主状态机。

---

# 30. 这个模块背后的关键设计原则

## 30.1 provider stream 事件必须被翻译为稳定的本地状态机

所以有 processor 统一消费并落库。

## 30.2 工具调用必须是正式生命周期对象，而不是临时 callback

所以有 pending/running/completed/error 四态 tool part。

## 30.3 任意退出路径都必须收敛到一致状态

所以有 finally-ish patch flush、tool abort 标记与 message completed time。

## 30.4 compaction、retry、blocked stop 都属于单轮执行控制，而非上层 prompt 拼装职责

因此集中在 processor 内最合适。

---

# 31. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/session/processor.ts`
2. `packages/opencode/src/session/llm.ts`
3. `packages/opencode/src/session/prompt.ts`
4. `packages/opencode/src/session/message-v2.ts`
5. `packages/opencode/src/session/retry.ts`
6. `packages/opencode/src/session/compaction.ts`

重点盯住这些函数/概念：

- `SessionProcessor.create()`
- `SessionProcessor.process()`
- `toolcalls`
- `reasoningMap`
- `blocked`
- `needsCompaction`
- `start-step` / `finish-step`
- `tool-result` / `tool-error`
- `SessionRetry.retryable()`

---

# 32. 下一步还需要深挖的问题

这一篇已经把 processor 状态机主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`tool-input-delta` / `tool-input-end` 当前为空实现，未来是否会记录原始 JSON 生成过程，还值得关注
- **问题 2**：`Session.updatePartDelta()` 的存储/广播效率与大文本增量更新策略还值得继续深读
- **问题 3**：`MessageV2.fromError()` 如何精细映射 provider 错误类型，还可以单独拆一篇
- **问题 4**：`SessionRetry.retryable()` 与 `delay()` 的错误分类与退避策略还值得进一步阅读
- **问题 5**：reasoning 多 lane map 在不同 provider 上是否都能稳定复用，还值得继续对照 provider streams
- **问题 6**：`blocked` 与 `continue_loop_on_deny` 配置在 UX 上的差异还值得进一步验证
- **问题 7**：processor 对 `start` 只设 busy，但对正常 finish 没显式设 idle，最终由哪里统一归位还可继续追踪上层 loop/status 逻辑
- **问题 8**：当 provider 发送非常规事件顺序时，当前状态机是否足够鲁棒，还值得结合 `ai` SDK 输出协议继续分析

---

# 33. 小结

`session_processor_streaming_and_step_state_machine` 模块定义了 OpenCode 如何把 provider 流式输出转化为稳定、可持久化、可恢复的单轮执行状态机：

- `SessionProcessor` 以 assistant message 为单位消费 `LLM.stream()` 的事件流
- text、reasoning、tool、step 都被映射成正式 `MessageV2` parts 生命周期
- retry、compaction、权限阻断、snapshot/patch、summary 都在同一执行控制面内被统一协调

因此，这一层不是简单的 stream adapter，而是 OpenCode 把模型输出、工具调用与会话状态真正编排成可运行代理循环的核心执行引擎。
