# LLM Output / Stream Parsing 模块详细解读

---

# 1. 模块定位

这一篇专门回答两个问题：

- OpenCode 中，LLM 输出到底是什么格式
- 输出之后，系统如何解析、落库、驱动下一步执行

核心源码包括：

- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/session/message-v2.ts`
- `packages/opencode/src/session/llm.ts`
- `packages/opencode/src/session/prompt.ts`

这一层的本质不是“聊天文本处理”，而是 **模型流事件 -> 持久化状态 -> loop 控制流** 的转换系统。

也就是说，OpenCode 里真正被执行的不是“模型说了什么字符串”，而是：

- 模型发出了哪些结构化事件
- 这些事件被 runtime 解释成了什么状态转移
- 状态转移如何决定下一轮 loop 的行为

---

# 2. 模块边界与职责

## 2.1 `session/llm.ts`

负责：

- 调用 AI SDK `streamText()`
- 产生结构化流事件
- 组织系统提示词、消息、工具、provider options
- 修复部分 tool call 错误

## 2.2 `session/processor.ts`

负责：

- 消费流事件
- 将事件写成 `Part`
- 更新 assistant message 的 finish、cost、tokens、error
- 决定返回 `continue / stop / compact`

## 2.3 `session/message-v2.ts`

负责：

- 定义错误类型
- 定义 `Part` 结构
- 把内部消息表示转换成模型消息
- 在下一轮中回放之前的 text / reasoning / tool result

## 2.4 `session/prompt.ts`

负责：

- 调用 `processor.process()`
- 根据 processor 返回值推进 loop
- 在 structured output 模式下决定是否报错或终止

---

# 3. OpenCode 中的 LLM 输出是什么

## 3.1 第一层：typed event stream

在 OpenCode 中，模型输出不是单个字符串，而是一个异步流：

- `stream.fullStream`

`SessionProcessor.process()` 会对这个流做：

```ts
for await (const value of stream.fullStream) {
  switch (value.type) {
    ...
  }
}
```

这说明系统收到的是**带类型标签的流事件**。

## 3.2 事件类型清单

从 `processor.ts` 中可以确认，至少处理了这些类型：

- `start`
- `reasoning-start`
- `reasoning-delta`
- `reasoning-end`
- `tool-input-start`
- `tool-input-delta`
- `tool-input-end`
- `tool-call`
- `tool-result`
- `tool-error`
- `error`
- `start-step`
- `finish-step`
- `text-start`
- `text-delta`
- `text-end`
- `finish`

这意味着 OpenCode 的“模型输出协议”天然支持：

- 流式文本
- 流式 reasoning
- 工具调用
- 工具结果
- 步骤边界
- 终止原因
- 错误

它不是把所有内容都扁平化成普通文本。

---

# 4. 第二层：事件如何被落成 `Part`

## 4.1 为什么要落成 `Part`

OpenCode 的状态真相不是“当前内存里正在 stream 的 token”，而是持久化消息系统中的：

- message
- part

也就是说，流事件只是瞬时信号，`Part` 才是持久化事实。

这样做有几个直接好处：

- 可以恢复会话
- 可以回放历史
- 可以让 UI 按类型渲染
- 可以在下一轮重新送回模型
- 可以做审计、统计、摘要、压缩

## 4.2 文本事件 -> `TextPart`

### `text-start`

processor 创建一个新的 `TextPart`：

- `type: text`
- `text: ""`
- `time.start`
- `metadata`

### `text-delta`

processor 将增量内容：

- 拼接到 `currentText.text`
- 调用 `Session.updatePartDelta(...)`

这说明文本不是一口气写入，而是**按 delta 实时持久化**。

### `text-end`

processor 会：

1. `trimEnd()`
2. 触发插件 hook：
   - `experimental.text.complete`
3. 更新结束时间
4. `Session.updatePart(currentText)`

这表示文本输出的最终完成时刻也是显式状态事件，而不是最后一个 delta 到来时隐式结束。

## 4.3 reasoning 事件 -> `ReasoningPart`

### `reasoning-start`

创建：

- `type: reasoning`
- 空文本
- `metadata`
- `time.start`

### `reasoning-delta`

像 text 一样增量拼接，并持久化 delta。

### `reasoning-end`

- `trimEnd()`
- 写入 `time.end`
- 更新 metadata

这说明 OpenCode 把 reasoning 当成与 text 平级的结构化输出，而不是混在普通文字里。

## 4.4 tool 事件 -> `ToolPart`

### `tool-input-start`

创建一个 `ToolPart`：

- `type: tool`
- `tool: value.toolName`
- `callID: value.id`
- `state.status = pending`
- `state.input = {}`
- `state.raw = ""`

### `tool-call`

更新为：

- `status = running`
- `input = value.input`
- `time.start`
- `metadata = value.providerMetadata`

### `tool-result`

更新为：

- `status = completed`
- `input`
- `output`
- `metadata`
- `title`
- `attachments`
- `time.end`

### `tool-error`

更新为：

- `status = error`
- `error`
- `input`
- `time.end`

这是一套完整的工具执行状态机。

## 4.5 step 事件 -> `step-start` / `step-finish`

### `start-step`

processor 会：

- 调用 `Snapshot.track()`
- 写入 `step-start` part

### `finish-step`

processor 会：

- 计算 usage / cost
- 更新 assistant message 的 `finish`、`cost`、`tokens`
- 写入 `step-finish` part
- 如果 snapshot 有 patch，则额外写入 `patch` part
- 触发 summary / overflow 检测

这意味着“step”是 OpenCode 中的正式运行时边界，不只是调试信息。

---

# 5. assistant message 的最终状态如何更新

## 5.1 finishReason 写到哪里

在 `finish-step` 时：

- `input.assistantMessage.finish = value.finishReason`

这说明 finish reason 是 message 级字段，不只是 step 级辅助信息。

## 5.2 token / cost 的计算

processor 通过：

- `Session.getUsage({ model, usage, metadata })`

把 provider 返回的 usage + provider metadata 转换成统一 tokens/cost 表示。

这说明 OpenCode 不直接信任不同 provider 的 usage 输出格式，而是做统一归一化。

## 5.3 completed time 的写入

在循环结束前，processor 总会：

- `input.assistantMessage.time.completed = Date.now()`
- `Session.updateMessage(input.assistantMessage)`

因此 assistant message 的生命周期边界也是持久化事实。

---

# 6. 为什么说 processor 是一个状态机

## 6.1 它维护了哪些状态

processor 内部维护了几个运行时状态：

- `toolcalls`
- `snapshot`
- `blocked`
- `attempt`
- `needsCompaction`
- `currentText`
- `reasoningMap`

这些状态不是业务数据模型的一部分，而是当前这次 process 过程中使用的执行状态。

## 6.2 状态机的核心转移

它的核心转移可以概括为：

- 流事件驱动 part 状态变化
- 异常驱动 retry / stop / compact
- 权限拒绝驱动 blocked
- overflow 驱动 needsCompaction
- 完成后统一收口到：
  - `continue`
  - `stop`
  - `compact`

所以 `processor.process()` 本质上是一个：

- **有限状态、事件驱动、带错误恢复的运行时状态机**

---

# 7. doom loop 检测算法

## 7.1 阈值定义

processor 定义：

- `DOOM_LOOP_THRESHOLD = 3`

## 7.2 检测条件

在每次 `tool-call` 时，会读取当前 assistant message 的 parts，拿最后三个 part：

- 长度必须为 3
- 每个 part 都是 `tool`
- 工具名相同
- 状态不能是 `pending`
- `JSON.stringify(input)` 完全相同

若成立，则触发：

- `PermissionNext.ask({ permission: "doom_loop", ... })`

## 7.3 这是什么算法

这是一个很典型的 **固定窗口重复调用检测**：

- 窗口大小 = 3
- 比较调用名字与输入是否完全同构

优点：

- 成本低
- 不依赖复杂语义判断
- 对明显死循环非常有效

局限：

- 只能检测严格相同输入
- 检测不到轻微变体的循环

但它作为 runtime 防护已经足够实用。

---

# 8. 错误处理与重试机制

## 8.1 `error` 事件 vs `catch`

processor 既处理：

- 流中的 `value.type === "error"`

也处理外层 `try/catch` 捕获到的异常。

最终都会统一进入：

- `MessageV2.fromError(...)`

转成内部标准错误对象。

## 8.2 错误分类

`message-v2.ts` 中定义了多种错误：

- `OutputLengthError`
- `AbortedError`
- `StructuredOutputError`
- `AuthError`
- `APIError`
- `ContextOverflowError`

说明 OpenCode 不是只把异常当字符串，而是维护了内部错误分类体系。

## 8.3 overflow 的特殊分支

如果 `MessageV2.fromError(e)` 结果是：

- `ContextOverflowError`

则 processor 不立即结束，而是：

- `needsCompaction = true`
- 发布 session error 事件
- 循环末尾返回 `compact`

这表示 overflow 在 runtime 眼里是“需要压缩再继续”的条件，而不是普通致命错误。

## 8.4 retry 逻辑

对非 overflow 错误，processor 会：

1. 调用 `SessionRetry.retryable(error)` 判断是否可重试
2. 若可重试：
   - `attempt++`
   - 计算 `SessionRetry.delay(...)`
   - 设置 `SessionStatus = retry`
   - `SessionRetry.sleep(...)`
   - 然后 `continue`
3. 否则把错误写进 assistant message

这是一个 **分类错误处理 + backoff retry** 机制。

## 8.5 为什么这很重要

agent runtime 里很多失败是短暂网络故障、provider 速率限制、瞬时服务错误。

如果没有 retry，系统会显著脆弱。

而 OpenCode 做到了：

- retry 是内建能力
- retry 状态可观测
- retry 延迟进入 session status

---

# 9. tool error 对 loop 的影响

## 9.1 用户拒绝和普通错误不同

在 `tool-error` 分支中，如果错误是：

- `PermissionNext.RejectedError`
- `Question.RejectedError`

则会：

- `blocked = shouldBreak`

也就是说，不是所有 tool error 都会导致 loop 终止。

只有某些“用户拒绝型错误”会触发阻断控制流。

## 9.2 为什么这是必要的

普通工具错误往往可以让模型继续解释、修复、改用别的方法。

但用户拒绝权限，通常代表：

- 这个方向当前不应继续自动执行

因此系统要允许根据配置：

- 要么停下来
- 要么继续 loop 看模型是否能换方案

这是一个很细致的 runtime 行为控制点。

---

# 10. `finish-step` 的特殊意义

## 10.1 它不只是“结束一轮”

在 `finish-step`，OpenCode 会同时完成多件事：

- usage/cost 归一化
- assistant message 更新
- `step-finish` part 落库
- snapshot patch 计算
- summary 触发
- overflow 检测

这说明 `finish-step` 是整个 process 生命周期中的**收口节点**。

## 10.2 patch 为什么在这里生成

processor 在 `start-step` 时做：

- `Snapshot.track()`

在 `finish-step` 或 process 结束收尾时做：

- `Snapshot.patch(snapshot)`

然后若有变更文件则写成 `patch` part。

这代表 OpenCode 可以把“本轮对工作区造成的变更”显式记入消息系统。

这对：

- UI 展示
- diff 回放
- 历史总结
- 审计

都非常重要。

---

# 11. 未完成工具调用如何善后

## 11.1 process 结束时的补偿逻辑

在 process 结束后，processor 会遍历当前 assistant message 的 parts：

- 如果某个 tool part 状态既不是 `completed` 也不是 `error`
- 则强制写成：
  - `status = error`
  - `error = "Tool execution aborted"`

## 11.2 为什么必须这样做

否则历史中会留下半完成 tool call，后续：

- UI 状态会不一致
- 历史重放会不完整
- 某些 provider 重新消费历史会协议报错

因此这一步是一个非常重要的 **end-of-stream reconciliation**。

---

# 12. `MessageV2` 如何把历史再喂回模型

## 12.1 为什么 `toModelMessages()` 影响输出解析语义

虽然 `toModelMessages()` 看起来属于“输入构造”，但它实际上定义了历史输出在下一轮中的语义回放方式。

它决定了：

- 上一轮 text 如何被模型再次看到
- reasoning 是否保留
- tool result 如何编码
- error 如何表达
- 被中断 tool call 如何被修补

因此它和 output parsing 是前后对应的一体两面。

## 12.2 工具结果回放

已完成的 tool part 会被回放成：

- `state: output-available`

失败的 tool part 会被回放成：

- `state: output-error`

pending/running 也会被强制回放为：

- `output-error`
- `[Tool execution was interrupted]`

所以输出解析不是一次性消费完就丢了，而是被转译成下一轮可持续消费的上下文。

## 12.3 reasoning 回放

reasoning part 也会作为 assistant reasoning 内容回放给模型。

这意味着 OpenCode 默认允许模型的显式 reasoning 历史成为后续上下文的一部分。

这在一些 provider 里很重要，因为 reasoning 可能承载中间推理状态。

---

# 13. 结构化输出模式如何工作

## 13.1 普通 text 模式

默认输出格式：

- `type: text`

模型可以正常输出 text、reasoning、tool calls。

## 13.2 `json_schema` 模式

`message-v2.ts` 定义了：

- `OutputFormatJsonSchema`
- `retryCount`

而 loop 中会：

- 注入 `StructuredOutput` tool
- 设置 `toolChoice = required`

## 13.3 为什么结构化输出被做成 tool

这不是让模型“尽量输出合法 JSON”，而是要求模型：

- 调用一个带 schema 的工具
- 由 runtime/AI SDK 做参数校验
- 成功后把结果存到 `processor.message.structured`

这是比纯文本 JSON 更稳定的方案。

## 13.4 如果模型没有调用 StructuredOutput

在 loop 里，如果：

- 本轮 finish 了
- 没有 error
- 但 `structuredOutput === undefined`
- 当前 format 是 `json_schema`

则会创建：

- `StructuredOutputError`

也就是说，结构化输出模式有严格契约，不允许模型“只输出一段像 JSON 的文字然后结束”。

---

# 14. 不同 mode 下输出有什么不同

## 14.1 协议层其实差别不大

无论 `plan`、`build`、`explore`、`compaction`，底层输出协议仍然是同一套：

- text
- reasoning
- tool-call
- tool-result
- finish-step
- error

也就是说 **mode 不改变底层输出数据结构**。

## 14.2 差异在行为层

不同 mode 真正改变的是：

- 使用哪种 prompt
- 暴露哪些 tools
- 权限边界是什么
- step 上限是多少
- 模型/variant 是否不同

所以不同 mode 的输出差异更多体现为：

- 是否更容易发起工具调用
- 是否更偏文本计划
- 是否更偏探索性搜索
- 是否更偏摘要/压缩

而不是换了一套事件协议。

---

# 15. `LLM.stream()` 对输出解析的前置影响

## 15.1 tool call repair

`LLM.stream()` 中实现了：

- `experimental_repairToolCall`

逻辑是：

- 若工具名大小写不对，但转小写后存在，则自动修复
- 否则将其改写成 `invalid` 工具，并带上错误信息

这是一种 **pre-parse repair**。

也就是说 processor 接收的事件在到达前，已经被 LLM 层尝试修复过一部分。

## 15.2 LiteLLM 兼容 noop tool

如果历史中已有 tool calls，但当前 tools 为空，某些代理层会报错。

`LLM.stream()` 会自动注入 `_noop` 工具，确保协议通过。

这意味着输出解析的稳定性不只依赖 processor，也依赖 LLM 层对 provider 兼容性的前置修正。

---

# 16. 这个模块背后的关键设计原则

## 16.1 输出不是字符串，而是可编排事件

OpenCode 从根上避免了“后处理字符串解析 agent 行为”的脆弱方案。

它依赖 typed event stream，把文本、thinking、tool、finish 全部分开。

## 16.2 解析的目标不是展示，而是状态真相

processor 的主要职责不是把流展示给用户，而是把它写成 durable state。

这就是为什么 message/part 模型如此重要。

## 16.3 错误处理是 runtime 控制流的一部分

overflow、retry、deny、abort 都不是边缘情况，而是内建分支。

## 16.4 输出回放是系统级能力

一次解析完成的输出不会消失，而会在下一轮通过 `toModelMessages()` 重新变成上下文。

这使得系统具备真正的多轮连续性。

---

# 17. 推荐阅读顺序

建议按下面顺序继续深挖：

1. `packages/opencode/src/session/processor.ts`
2. `packages/opencode/src/session/message-v2.ts`
3. `packages/opencode/src/session/llm.ts`
4. `packages/opencode/src/session/prompt.ts`

重点盯住这些函数/概念：

- `SessionProcessor.create()`
- `process()`
- `MessageV2.fromError()`
- `MessageV2.toModelMessages()`
- `LLM.stream()`
- `experimental_repairToolCall`
- `StructuredOutputError`
- `ContextOverflowError`

---

# 18. 下一步还需要深挖的问题

这个模块已经把主流程讲清楚了，但仍有一些地方值得继续单独深挖：

- **问题 1**：`MessageV2.fromError()` 如何把不同 SDK/provider 异常映射成统一错误分类
- **问题 2**：`SessionRetry` 的 delay/backoff 具体公式，以及是否区分速率限制与网络错误
- **问题 3**：`Snapshot.track()` / `Snapshot.patch()` 的内部实现细节与成本模型
- **问题 4**：`experimental.text.complete` 和其他 plugin hooks 如何影响最终文本内容与展示
- **问题 5**：reasoning metadata 在不同 provider 下的字段差异与兼容策略是什么
- **问题 6**：structured output 的 retryCount 在 loop 中是否已经完全生效，还是还有补完空间
- **问题 7**：`finish-step` 与最终 `finish` 事件之间的语义边界是否完全一致
- **问题 8**：当一次响应里同时出现大量 reasoning 和 tool call 时，UI 层如何排序渲染这些 part

---

# 19. 小结

`llm_output_and_stream_parsing` 模块定义了 OpenCode 如何把模型输出变成真正可执行、可回放、可恢复的系统状态：

- `LLM.stream()` 负责产出结构化流
- `SessionProcessor` 负责事件解析与状态推进
- `MessageV2` 负责错误模型与历史回放
- loop 再根据 processor 的结果决定继续、停止或压缩

因此，这一层是 OpenCode 从“模型 token 流”跨越到“agent runtime 状态机”的关键桥梁。
