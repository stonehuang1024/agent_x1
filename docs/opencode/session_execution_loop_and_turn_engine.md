# Session Execution Loop / Turn Engine 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的单轮执行引擎，也就是一条用户消息如何沿着：

- prompt 组装
- model stream
- tool call
- part 持久化
- retry / compaction / stop

一路跑完。

核心问题是：

- 一条 user message 如何变成完整 assistant turn
- 为什么执行引擎是流式 part 驱动，而不是一次性生成整条回答
- tool call、reasoning、text、step snapshot 如何落库
- 什么情况下单轮会 `continue`、`stop` 或 `compact`
- retry、doom loop、permission deny 在执行引擎里如何介入

核心源码包括：

- `packages/opencode/src/session/llm.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/message-v2.ts`
- `packages/opencode/src/session/index.ts`

这一层本质上是 OpenCode 的**单轮会话执行状态机与流式产物落盘引擎**。

---

# 2. 为什么要把“单轮执行”视为独立引擎

一次 assistant 回复在 OpenCode 里不是简单字符串返回。

它可能包含：

- reasoning 流
- 多个 tool call
- 多个 tool result
- 文本增量输出
- step 开始/结束标记
- snapshot patch
- retry part
- compaction 触发

如果把这些都塞成一次性 response：

- 无法实时展示
- 无法精细恢复执行状态
- 无法把工具调用变成正式会话记录

因此 OpenCode 明确用“processor + message parts”的方式建模单轮执行。

---

# 3. 基础数据模型：一条消息不是一段文本，而是一组 `Part`

`message-v2.ts` 里可以看到 assistant/user message 都围绕 Part 组织。

与执行引擎强相关的 part 类型包括：

- `text`
- `reasoning`
- `tool`
- `step-start`
- `step-finish`
- `patch`
- `retry`
- `file`
- `compaction`

## 3.1 为什么这很关键

这意味着单轮执行不是 append 一条最终 answer，而是：

- **把 LLM 流中的每种语义片段映射成正式持久化对象**

这是 OpenCode 能做实时 UI、调试、回放和分享同步的基础。

---

# 4. `SessionProcessor.create(...)`：单轮执行器实例

processor 是围绕一条 assistant message 创建的：

- `assistantMessage`
- `sessionID`
- `model`
- `abort`

内部维护状态：

- `toolcalls`
- `snapshot`
- `blocked`
- `attempt`
- `needsCompaction`

并暴露核心方法：

- `process(streamInput)`

## 4.1 这说明什么

processor 是一次 turn 的**临时执行上下文**。

它把这轮生成过程中需要跨 stream event 共享的信息都封在实例内，而不是散落全局状态。

---

# 5. `LLM.StreamInput`：单轮执行真正吃进去的原料

`llm.ts` 里 `StreamInput` 包含：

- 当前 user message
- `sessionID`
- `model`
- `agent`
- `system`
- `abort`
- `messages`
- `tools`
- `retries`
- `toolChoice`

这说明 LLM stream 调用不是只给“当前一句用户输入”，而是完整会话运行上下文。

---

# 6. `LLM.stream()`：prompt -> model stream 的装配层

`LLM.stream()` 的职责不是做 loop，而是把这轮调用送进模型。

主要步骤包括：

1. 读取 language / config / provider / auth
2. 生成 system prompt 列表
3. 触发 plugin system transform
4. 计算 model/provider/agent/variant options
5. 触发 `chat.params` / `chat.headers`
6. `resolveTools(input)` 过滤掉被禁用工具
7. 兼容 LiteLLM proxy 的 `_noop` tool
8. 调 `streamText(...)`

## 6.1 它本质上是 model invocation assembler

也就是说：

- prompt 结构
- provider options
- tool set
- telemetry
- headers

都在这里拼好，然后交给 AI SDK 流式执行。

---

# 7. system prompt 组装不是单一来源

`LLM.stream()` 里最终 system prompt 来自多层合并：

- agent prompt 或 provider prompt
- 本次调用显式传入的 `system`
- 当前 user message 自带的 `system`
- plugin `experimental.chat.system.transform`

这说明 prompt 不是静态模板，而是一个多来源、可插件改写的合成结果。

---

# 8. tools 不是固定列表，而是动态过滤后的能力集

`resolveTools()` 会根据：

- agent permission disable
- `input.user.tools?.[tool] === false`

删除不可用工具。

因此每轮 LLM 实际看到的工具集，是：

- 注册工具全集
- 经过 agent 和当前消息局部配置过滤后的结果

这意味着 tool availability 是 turn-scoped 的，而非全局常量。

---

# 9. `experimental_repairToolCall`：工具名纠错层

如果模型返回了错误大小写工具名，例如：

- `Read` 而不是 `read`

系统会：

- 自动修复为 lower-case 已知工具名

否则把它改写成：

- `invalid`

并把错误信息塞入 input。

这说明 OpenCode 不会轻易因为 tool call 细微格式错误就整轮崩掉，而是把错误显式转译进工具协议。

这是很实用的鲁棒性设计。

---

# 10. `SessionProcessor.process()`：真正的 turn loop

processor 里的核心循环是：

- `while (true)`

每一轮：

1. 调 `LLM.stream(streamInput)`
2. 遍历 `stream.fullStream`
3. 将 stream event 映射成 message parts / status / retries
4. 根据结果决定返回：
   - `continue`
   - `stop`
   - `compact`

## 10.1 为什么这里还有 while(true)

因为单次模型 stream 可能不会让整个 turn 真正结束。

例如：

- 触发 retry
- 需要 compaction 后重试
- deny 逻辑可能中断继续循环

这说明 processor 不只是 event consumer，还是单轮的高层控制器。

---

# 11. `start` 事件：进入 busy 状态

一旦收到 stream 的：

- `start`

processor 会：

- `SessionStatus.set(sessionID, { type: "busy" })`

这说明会话状态更新与模型流启动是直接绑定的。

---

# 12. reasoning 三段式事件如何落盘

processor 处理：

- `reasoning-start`
- `reasoning-delta`
- `reasoning-end`

流程是：

## 12.1 start

创建一个空 `reasoning` part，保存：

- `id`
- `messageID`
- `sessionID`
- `text: ""`
- `time.start`
- `metadata`

并 `Session.updatePart(...)`。

## 12.2 delta

把增量文本追加到本地 part，并通过：

- `Session.updatePartDelta(...)`

流式写出。

## 12.3 end

trim 文本、补 `time.end`、再 `updatePart(...)` 收口。

这说明 reasoning 不是隐藏日志，而是第一等持久化对象。

---

# 13. text 三段式事件如何落盘

text 处理与 reasoning 类似：

- `text-start`
- `text-delta`
- `text-end`

但在 `text-end` 时还会额外执行：

- `Plugin.trigger("experimental.text.complete", ...)`

然后再落最终 text。

## 13.1 这说明什么

最终 assistant 文本在结束前仍有插件后处理机会。

因此文本输出不是模型字面结果的完全直通，而是允许受 runtime transform 影响。

---

# 14. tool call 事件如何落盘

processor 处理了完整的工具状态机：

- `tool-input-start`
- `tool-call`
- `tool-result`
- `tool-error`

## 14.1 `tool-input-start`

先创建一个 `tool` part，状态为：

- `pending`
- `input: {}`
- `raw: ""`

并记录 `callID` 与 `toolName`。

## 14.2 `tool-call`

将其升级为：

- `running`
- 写入结构化 input
- 记录 `time.start`
- 可带 provider metadata

## 14.3 `tool-result`

将其改成：

- `completed`
- 写入 `output`
- `metadata`
- `title`
- `attachments`
- `time.start/end`

## 14.4 `tool-error`

将其改成：

- `error`
- 记录 error string
- `time.start/end`

这说明 tool execution 在会话里有清晰的生命周期，而不是只保留最终文本摘要。

---

# 15. doom loop 检测：为什么看最后三个 tool part

在 `tool-call` 阶段，processor 会取：

- 当前 assistant message 的最近三个 parts

若这三项都满足：

- 都是同一个 tool
- input 完全相同
- 状态都不是 pending

则触发：

- `PermissionNext.ask({ permission: "doom_loop", ... })`

## 15.1 含义

这不是简单计数器，而是：

- **相同工具 + 相同输入 + 短窗口重复**

的循环检测。

相比粗暴“工具调用次数超过 N 次”，这更接近真正的死循环信号。

---

# 16. step 事件：单轮内部步骤边界

processor 还会处理：

- `start-step`
- `finish-step`

## 16.1 `start-step`

- `snapshot = await Snapshot.track()`
- 写入 `step-start` part

## 16.2 `finish-step`

- 根据 `value.usage` + model 计算 token/cost
- 更新 assistant message 的 `finish/cost/tokens`
- 写入 `step-finish` part
- 如果有 snapshot，则计算 `Snapshot.patch(snapshot)`
- 若 patch 有文件变化，写入 `patch` part
- 调 `SessionSummary.summarize(...)`
- 检查是否需要 compaction

## 16.3 为什么 step 很重要

它让一轮 assistant 响应内部的多个阶段有明确边界，而不是一整轮只在最后收尾。

---

# 17. snapshot / patch：为什么执行引擎要跟踪文件改动

在 step 边界上，processor 会：

- `Snapshot.track()` 记录起点
- `Snapshot.patch(snapshot)` 得到文件变更
- 若有变化，写入 `patch` part

这说明单轮执行不仅关心语言输出，也关心这轮工具调用实际对工作树造成了什么影响。

这对于：

- summary
- revert
- share
- UI diff 展示

都非常关键。

---

# 18. `SessionSummary.summarize(...)`：单轮完成后的摘要更新

在 `finish-step` 后 processor 会调用：

- `SessionSummary.summarize({ sessionID, messageID: assistantMessage.parentID })`

这说明 summary 更新是单轮执行后的标准后处理，而不是用户手动操作。

---

# 19. compaction 判定：为什么在 step finish 后做

processor 会在每步结束后根据：

- `SessionCompaction.isOverflow({ tokens, model })`

来判断是否溢出。

若 assistant message 还没 summary 且溢出，就设置：

- `needsCompaction = true`

## 19.1 为什么不是等完全失败才 compact

因为这里拿到的是这一步真实 token usage，更接近 authoritative overflow 信号。

这样系统能在真正上下文爆炸前主动转入 compaction 分支。

---

# 20. 错误处理：`ContextOverflowError`、retryable error、fatal error

processor 的 catch 分三类：

## 20.1 Context overflow

- `MessageV2.fromError(e)` 若识别为 `ContextOverflowError`
- 设置 `needsCompaction = true`
- 发布 `Session.Event.Error`

最终返回：

- `compact`

## 20.2 Retryable error

如果 `SessionRetry.retryable(error)` 不为 undefined：

- `attempt++`
- 计算 backoff delay
- `SessionStatus.set(type: "retry")`
- `SessionRetry.sleep(delay, abort)`
- `continue` 进入下一轮 while

## 20.3 Fatal error

否则：

- 把 error 写进 `assistantMessage.error`
- `Bus.publish(Session.Event.Error, ...)`
- `SessionStatus.set(idle)`
- 最终 `stop`

这说明 processor 的错误处理不是单层 try/catch，而是有明确恢复等级。

---

# 21. tool deny / question reject 如何影响循环

若 `tool-error` 的根因是：

- `PermissionNext.RejectedError`
- `Question.RejectedError`

则：

- `blocked = shouldBreak`

而 `shouldBreak` 取决于：

- `experimental.continue_loop_on_deny !== true`

这意味着 deny 后到底停不停止，不是写死，而受实验配置控制。

---

# 22. 流结束后的兜底清理

无论成功失败，processor 在循环结束路径上还会做几件事：

## 22.1 补 patch

如果还有未收口 `snapshot`，再算一次 patch 并写 `patch` part。

## 22.2 将未完成 tool part 标成 error

所有状态不是 `completed/error` 的 tool part，都被改成：

- `status: error`
- `error: "Tool execution aborted"`

## 22.3 完成 assistant message

- `assistantMessage.time.completed = Date.now()`
- `Session.updateMessage(assistantMessage)`

这保证 message 不会留下半完成但无终态的脏状态。

---

# 23. `process()` 的三种返回值语义

processor 最终只返回三种高层决策：

- `continue`
- `stop`
- `compact`

## 23.1 `compact`

说明上下文需要压缩，然后上层应走 compaction 分支。

## 23.2 `stop`

说明本轮应终止，原因可能是：

- blocked
- fatal error
- 正常无需继续

## 23.3 `continue`

说明当前 assistant message 没有 fatal 问题，也不需要 compact，可以继续后续单轮外逻辑。

这说明 processor 负责的是：

- **把细粒度 stream event 收敛成上层可判断的 turn result**

---

# 24. `prompt.ts`：tools 如何被包装进执行引擎

`prompt.ts` 里会把本地 tool 和 MCP tool 统一包装进 LLM 可调用形式。

包装时会做：

- `tool.execute.before` plugin hook
- 权限 ask
- 执行真实 tool
- `tool.execute.after` hook
- 文本结果 `Truncate.output(...)`
- attachment 转成 message file part

## 24.1 为什么这很关键

因为 processor 看到的 `tool-result`，其实已经是这层包装后的统一输出，而不是工具原生返回值。

因此 turn engine 的工具执行语义，本质上建立在 prompt/tool wrapping 层之上。

---

# 25. `createUserMessage(...)`：单轮的起点是正式消息对象，而不是裸 prompt

`prompt.ts` 里 `createUserMessage()` 会：

- 解析 agent/model/variant
- 生成 user `MessageV2.Info`
- 处理 file/resource parts
- 处理 text synthetic parts

这说明单轮执行最开始就已经把用户输入结构化为 message + parts，而不是直到发 LLM 前才临时拼装。

---

# 26. 单轮执行的数据流可以怎么理解

可以把完整路径概括为：

## 26.1 输入阶段

- 用户输入 -> `createUserMessage()` -> user message + parts

## 26.2 装配阶段

- `prompt.ts` 整理消息历史、tools、system prompt
- `LLM.stream()` 组装 provider/model 请求

## 26.3 执行阶段

- `SessionProcessor.process()` 消费 `stream.fullStream`
- reasoning/text/tool/step 逐类落 part

## 26.4 收尾阶段

- 更新 assistant message
- 写 patch/summary
- 判断 retry/compact/stop

这就是 OpenCode 单轮 turn engine 的主干。

---

# 27. 这个模块背后的关键设计原则

## 27.1 一条 assistant 回复必须被拆成可持久化的语义 part

这样才能支持实时 UI、分享、回放与恢复。

## 27.2 model stream 事件应直接驱动状态更新

而不是等结束后再批量回填。

## 27.3 tool execution 必须成为消息历史的正式组成部分

否则 agent 无法真正基于工具结果连续推理。

## 27.4 retry、deny、compaction 必须在 turn engine 内统一裁决

否则上层很难理解单轮是否真的完成。

---

# 28. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/session/message-v2.ts`
2. `packages/opencode/src/session/prompt.ts`
3. `packages/opencode/src/session/llm.ts`
4. `packages/opencode/src/session/processor.ts`
5. `packages/opencode/src/session/summary.ts`
6. `packages/opencode/src/session/compaction.ts`
7. `packages/opencode/src/session/retry.ts`

重点盯住这些函数/概念：

- `LLM.stream()`
- `resolveTools()`
- `experimental_repairToolCall`
- `SessionProcessor.create()`
- `process()`
- `start-step` / `finish-step`
- `tool-call` / `tool-result`
- `ContextOverflowError`
- `SessionSummary.summarize()`

---

# 29. 下一步还需要深挖的问题

这一篇已经把单轮执行引擎主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`prompt.ts` 中完整的历史消息构造、structured output、subtask 和 instruction 注入链路还值得单独拆文档
- **问题 2**：assistant message 的创建时机与 parent/child message linking 还可以继续追踪更完整路径
- **问题 3**：`SessionCompaction` 的真实压缩算法、何时生成 summary message、如何重写上下文还值得单独展开
- **问题 4**：`SessionRetry.retryable()` 与不同 provider error 的映射策略还值得继续精读
- **问题 5**：reasoning metadata、provider metadata 与 UI 展示之间的对应关系还可继续梳理
- **问题 6**：step 粒度在不同 provider/模型上的事件一致性是否完全稳定，还值得继续验证
- **问题 7**：tool abort 后统一标记 error 的策略是否会掩盖更细粒度终止原因，这点值得产品层面思考
- **问题 8**：多 agent / subtask 嵌套时，processor 之间的父子关系如何串接，还值得继续追踪

---

# 30. 小结

`session_execution_loop_and_turn_engine` 模块定义了 OpenCode 如何把一次用户输入转化成一条完整、可回放、可持续推进的 assistant 执行轮次：

- `prompt.ts` 负责把消息、工具和系统提示整理成可调用上下文
- `LLM.stream()` 负责组装 provider 请求并发起流式模型调用
- `SessionProcessor.process()` 负责消费流事件、落盘 part、跟踪工具状态与文件 patch，并统一裁决 retry/stop/compact
- `MessageV2` 则提供了承载整个执行过程的细粒度消息模型

因此，这一层不是简单的聊天请求处理器，而是 OpenCode 代理式会话执行、工具编排与状态持久化的核心引擎。
