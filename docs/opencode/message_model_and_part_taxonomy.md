# Message Model / Part Taxonomy 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 `MessageV2` 消息模型与 Part 类型系统。

核心问题是：

- 为什么 OpenCode 不把消息建模成单纯字符串，而是 `Message + Parts`
- user / assistant 两类 message 在 schema 上如何分工
- `Part` 联合类型为什么覆盖 text、tool、reasoning、patch、compaction、retry 等多种语义
- 这些 Part 如何持久化、hydrate、广播与投影到模型输入
- `fromError()` 如何把底层异常正规化成 assistant error

核心源码包括：

- `packages/opencode/src/session/message-v2.ts`
- `packages/opencode/src/session/index.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/session/compaction.ts`
- `packages/opencode/src/session/prompt.ts`

这一层本质上是 OpenCode 的**会话语义数据模型与流式执行产物容器**。

---

# 2. 为什么消息模型必须从“字符串”升级为“语义部件集合”

在 OpenCode 里，一条 assistant 回复可能同时包含：

- 文本回答
- reasoning 流
- tool 调用和结果
- 文件附件
- patch 摘要
- step 边界
- retry / compaction 标记
- 错误对象

如果只用一段字符串表示：

- 无法精细展示执行过程
- 无法可靠回放 tool 调用
- 无法做 diff / summary / revert
- 无法给不同 provider 重新构建模型消息

因此系统采用：

- `MessageV2.Info` 表示消息级骨架
- `MessageV2.Part[]` 表示语义内容明细

这是整个会话系统的核心数据建模选择。

---

# 3. 顶层消息分为 `User` 与 `Assistant`

`MessageV2.Info` 是一个按 `role` 区分的联合：

- `User`
- `Assistant`

## 3.1 `User`

包含：

- `time.created`
- `format`
- `summary`
- `agent`
- `model`
- `system`
- `tools`
- `variant`

## 3.2 `Assistant`

包含：

- `time.created/completed`
- `error`
- `parentID`
- `providerID/modelID`
- `mode`
- `agent`
- `path`
- `summary`
- `cost`
- `tokens`
- `structured`
- `variant`
- `finish`

这说明 user 与 assistant 虽然都叫 message，但语义完全不同。

---

# 4. `User` message 的职责

User message 不只是保存“用户说了什么”。

它还承载：

- 本轮使用哪个 agent
- 本轮指定哪个 model
- 是否要求 structured output
- 是否禁用某些 tool
- 自定义 system prompt
- variant 选择
- 本轮对应的文件 diff summary

也就是说，user message 本身就是一次 turn 配置对象，而不只是文本输入。

---

# 5. `Assistant` message 的职责

Assistant message 则更像一次执行结果总表。

它聚合：

- 这轮由哪个 provider/model 生成
- parent user 是谁
- 执行模式（普通 / compaction）
- token / cost 统计
- structured output 结果
- finish reason
- 失败时的 normalized error

而真正细节内容则在 Parts 里。

这说明 assistant info 负责：

- **turn-level execution metadata**

---

# 6. `Part` 联合类型：消息内容的真正核心

`MessageV2.Part` 是一个按 `type` 区分的联合，目前包含：

- `text`
- `subtask`
- `reasoning`
- `file`
- `tool`
- `step-start`
- `step-finish`
- `snapshot`
- `patch`
- `agent`
- `retry`
- `compaction`

这说明 OpenCode 不是把某些执行细节挂在 metadata 里，而是把它们都提升为正式 part 类型。

---

# 7. `TextPart`：普通文本并不普通

`TextPart` 除了 `text` 外还有：

- `synthetic`
- `ignored`
- `time.start/end`
- `metadata`

## 7.1 `synthetic`

表示这段文本不是直接来自用户原始输入，可能是系统注入，例如：

- 读取 MCP resource 的说明
- compaction 后的 synthetic continue prompt

## 7.2 `ignored`

说明它存在于消息历史中，但在某些投影场景中可能被跳过。

这让 text 也可以承载丰富控制语义。

---

# 8. `ReasoningPart`：推理过程是一等公民

Reasoning part 包含：

- `text`
- `metadata`
- `time.start/end`

这说明 OpenCode 将模型 reasoning 流正式写入消息系统，而不是只在流式 UI 中短暂显示。

因此系统可以：

- 回放 reasoning
- share reasoning
- 在调试中保留 reasoning metadata

---

# 9. `FilePart` 与 `FilePartSource`

`FilePart` 包含：

- `mime`
- `filename`
- `url`
- `source`

而 `source` 还能细分为：

- `file`
- `symbol`
- `resource`

## 9.1 这说明什么

同一个 file attachment，不只是“一个 blob”。

系统还能知道它来自：

- 本地文件
- 某个 symbol 范围
- MCP resource

这对 prompt 投影、UI 展示与审计都很重要。

---

# 10. `ToolPart`：工具调用的正式状态机载体

`ToolPart` 包含：

- `callID`
- `tool`
- `state`
- `metadata`

其中 `state` 又是一个联合：

- `pending`
- `running`
- `completed`
- `error`

## 10.1 为什么 tool state 要做成子联合

因为工具调用天生就是有生命周期的对象。

如果只保留最终 output，就会丢失：

- 开始时间
- 输入参数
- 中途失败
- abort 情况
- compacted 标记
- attachments

所以 tool call 被精确建模成嵌套状态机。

---

# 11. 各种 `ToolState`

## 11.1 pending

- `input`
- `raw`

表示模型刚提出工具调用意图，但还没真正执行。

## 11.2 running

- `input`
- `title?`
- `metadata?`
- `time.start`

表示工具正在运行。

## 11.3 completed

- `input`
- `output`
- `title`
- `metadata`
- `time.start/end/compacted?`
- `attachments?`

表示工具输出已完成，且之后还能被 prune/compact 标记。

## 11.4 error

- `input`
- `error`
- `metadata?`
- `time.start/end`

表示执行失败或被中止。

---

# 12. `StepStartPart` / `StepFinishPart`

这两个 part 用来表达单轮执行中的步骤边界。

## 12.1 `StepStartPart`

- `snapshot?`

## 12.2 `StepFinishPart`

- `reason`
- `snapshot?`
- `cost`
- `tokens`

它们把“每一步开始/结束”变成消息历史里可追踪的结构。

这对于：

- summary
- diff 计算
- UI 逐步回放

都很关键。

---

# 13. `PatchPart` / `SnapshotPart`

## 13.1 `SnapshotPart`

保存一个快照标识。

## 13.2 `PatchPart`

保存：

- `hash`
- `files`

表示从某次快照比较得到的文件变更摘要。

它们让会话执行与工作树变化发生正式关联，而不是只存在于外部 git 状态里。

---

# 14. `CompactionPart` / `RetryPart` / `SubtaskPart`

这些 part 说明系统不仅记录内容，也记录控制流动作。

## 14.1 `CompactionPart`

- `auto`
- `overflow?`

表示这条用户消息本身是一次 compaction 请求。

## 14.2 `RetryPart`

- `attempt`
- `error`
- `time.created`

表示一次明确重试事件的持久化表示。

## 14.3 `SubtaskPart`

- `prompt`
- `description`
- `agent`
- `model?`
- `command?`

说明系统也把子任务派发建模为消息组成部分。

---

# 15. `AgentPart`

`AgentPart` 保存：

- `name`
- `source?`

这说明 agent 选择/切换也被视作会话内容的一部分，而不只是 message info 上的字段覆盖。

---

# 16. 错误模型：assistant error 不是裸 `Error`

`Assistant.error` 是一个按 `name` 区分的联合，包含：

- `AuthError`
- `Unknown`
- `OutputLengthError`
- `AbortedError`
- `StructuredOutputError`
- `ContextOverflowError`
- `APIError`

这说明 OpenCode 把错误正规化成 schema 化对象，而不是把异常堆栈随便塞进字符串。

这对：

- retry 判断
- UI 展示
- share 同步
- provider 独立错误处理

都非常关键。

---

# 17. `fromError()`：底层异常到统一错误对象的转换器

`MessageV2.fromError(e, { providerID })` 就是错误正规化的入口。

从 grep 可见它会处理：

- `AbortError`
- provider 相关错误
- API 调用错误
- 上下文溢出
- 未知错误

## 17.1 意义

processor 和 retry 系统都不想直接面对各种原始异常类型。

所以 `fromError()` 把它们统一转换为 `Assistant.error` 可承载的 schema 对象。

这是一条非常关键的标准化边界。

---

# 18. `WithParts`：真正对外消费的消息形态

`WithParts` 很简单：

- `info`
- `parts`

但它是最常见的“完整消息对象”。

因为无论：

- prompt 构造
- summary
- compaction
- revert
- UI

真正需要的都不是 message row 本身，而是：

- 骨架 + 全部语义 parts

---

# 19. 持久化方式：message 与 part 分表存储

在 `session/index.ts` 中可以看到：

## 19.1 `updateMessage(msg)`

- 往 `MessageTable` upsert
- `id` / `session_id` / `time_created`
- 其余字段进入 `data`
- 然后发 `message.updated`

## 19.2 `updatePart(part)`

- 往 `PartTable` upsert
- `id` / `message_id` / `session_id` / `time_created`
- 其余字段进入 `data`
- 然后发 `message.part.updated`

这说明消息系统在数据库层面就是：

- message 表存骨架
- part 表存明细

这是很标准也很可扩展的拆分。

---

# 20. `updatePartDelta()`：为什么 delta 不直接写库

`updatePartDelta()` 当前只做：

- `Bus.publish(MessageV2.Event.PartDelta, input)`

并不直接改数据库。

## 20.1 这意味着什么

delta 主要服务：

- 流式 UI 实时渲染

而正式落盘仍由后续完整 `updatePart(...)` 负责。

这避免了为每个 token/delta 频繁写库。

这是非常合理的性能设计。

---

# 21. hydrate：数据库行如何还原成完整消息对象

`message-v2.ts` 里的 `hydrate(rows)` 会：

1. 拿到一组 message rows
2. 批量查它们对应的 part rows
3. 依 message_id 分组
4. 把 row 转成：
   - `info(row)`
   - `part(row)`
5. 输出 `WithParts[]`

这说明系统读取消息历史时天然就是 message+parts 一起 hydrate，而不是让上层自己手拼。

---

# 22. `toModelMessages()`：为什么 MessageV2 能投影到 provider 无关的模型输入

这是 `MessageV2` 的另一项核心职责。

`toModelMessages(input, model, options)` 会把 `WithParts[]` 转成 AI SDK 的：

- `ModelMessage[]`

并做很多 provider-aware 处理。

这意味着 `MessageV2` 不仅是存储模型，也是：

- **运行时上下文投影模型**

---

# 23. user parts 如何投影到模型输入

对于 user message：

- `text` -> text part
- `file` -> file part 或 media placeholder
- `compaction` -> 文本 `What did we do so far?`
- `subtask` -> 文本 `The following tool was executed by the user`

## 23.1 `stripMedia`

若开启 `stripMedia` 且文件是图片/PDF：

- 不直接传 media
- 改成 `[Attached <mime>: <filename>]`

这说明相同的消息历史，可根据上下文窗口/模型能力投影成不同表示。

---

# 24. assistant parts 如何投影到模型输入

对于 assistant message：

- `text` -> assistant text
- `step-start` -> step marker
- `tool(completed/error/running...)` -> tool call/result/error 结构

## 24.1 不同模型的 provider metadata 处理

若目标模型与原生成模型不同：

- 会去掉 providerMetadata/callProviderMetadata

这说明 MessageV2 在做跨模型 replay 时，会主动剥离可能不兼容的 provider-specific 信息。

---

# 25. tool output 的特殊投影逻辑

在 `toModelMessages()` 中：

- 如果 tool output 已被 compacted，则 outputText 变成 `[Old tool result content cleared]`
- 若 `stripMedia` 开启，则 attachments 清空

## 25.1 意义

这说明 message model 本身就知道：

- 哪些 tool output 已不应完整进入后续上下文
- 哪些媒体需要被省略

因此 prune/compaction 的结果会真正影响后续 prompt 投影，而不是只停留在存储层。

---

# 26. provider 对 tool-result media 的兼容差异

`toModelMessages()` 里专门判断：

- 哪些 provider 支持 tool result 中带 media
- 哪些不支持

如果 provider 不支持且 tool result 有 media：

- media 会被抽出来，作为额外 user message 注入

这说明 MessageV2 还是 provider 兼容层的一部分，而不是纯 schema 文件。

---

# 27. `convertToModelMessages(...)` 前的中间 UIMessage 层

`toModelMessages()` 不是直接构造最终 provider message，而是：

1. 先构建 `UIMessage[]`
2. 再调用 AI SDK 的 `convertToModelMessages(...)`

这给系统留下了足够空间在 provider 适配前做：

- media stripping
- tool output normalization
- metadata 兼容化
- compaction 投影

这是很聪明的中间层设计。

---

# 28. `filterCompacted(...)`

grep 结果显示还有一个：

- `filterCompacted(stream)`

从命名和上下文看，它用于处理已经 compacted 的消息流，避免旧工具输出继续污染视图或上下文。

这说明“被 compacted”不仅是 part 内一个时间戳字段，还会在上层消费逻辑里被真正利用。

---

# 29. cursor：消息分页游标

`message-v2.ts` 里还提供：

- `cursor.encode()`
- `cursor.decode()`

基于：

- `id`
- `time`

做 base64url 编解码。

这说明消息历史天然支持稳定分页，而不是只能全量读出。

---

# 30. 事件系统：消息与 part 的实时同步出口

`MessageV2.Event` 定义了：

- `message.updated`
- `message.removed`
- `message.part.updated`
- `message.part.delta`
- `message.part.removed`

这让系统可以分别订阅：

- 消息骨架更新
- part 完整更新
- 流式 delta
- 删除事件

因此 UI、share、同步层都不必轮询数据库。

---

# 31. 为什么 message/part 分离是对的

如果把所有内容都塞进一整块 JSON：

- 文本 delta 难以高效更新
- tool state 变化难以局部修改
- reasoning / patch / retry 等难做类型化校验
- provider 投影逻辑会非常混乱

现在的拆分方式使得：

- schema 更稳
- 存储更清晰
- 流式更新更自然
- 投影更可组合

这就是 `MessageV2` 设计成功的根基。

---

# 32. 这个模块背后的关键设计原则

## 32.1 会话历史必须保存“过程”，不只保存“结果”

所以有 reasoning/tool/step/patch/retry 等多种 part。

## 32.2 错误必须 schema 化，不能停留在原始异常层

所以有 `fromError()` 和 assistant error union。

## 32.3 模型输入投影应建立在统一消息模型之上

所以有 `toModelMessages()`。

## 32.4 流式展示与持久化写入应解耦

所以有 `PartDelta` 事件与最终 `updatePart()` 双轨。

---

# 33. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/session/message-v2.ts`
2. `packages/opencode/src/session/index.ts`
3. `packages/opencode/src/session/processor.ts`
4. `packages/opencode/src/session/compaction.ts`
5. `packages/opencode/src/session/prompt.ts`

重点盯住这些函数/概念：

- `MessageV2.Part`
- `ToolState`
- `Assistant.error`
- `WithParts`
- `updateMessage()`
- `updatePart()`
- `updatePartDelta()`
- `toModelMessages()`
- `fromError()`

---

# 34. 下一步还需要深挖的问题

这一篇已经把消息模型主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`fromError()` 对 `APICallError`、`LoadAPIKeyError`、provider 自定义错误的完整映射细节还值得逐分支精读
- **问题 2**：`toModelMessages()` 后半段对 pending/running tool call 的补偿逻辑还可以继续完整追踪
- **问题 3**：`filterCompacted()` 的具体策略及其调用点还值得继续 grep
- **问题 4**：structured output 与 `Assistant.structured` 的写入时机、校验失败重试策略还值得单独展开
- **问题 5**：message/part ID 使用升序或降序生成的语义差异还可进一步总结
- **问题 6**：跨 provider replay 时 metadata 剥离是否足够完全，还值得继续验证
- **问题 7**：file part 的 data URL 持久化体积与数据库/存储边界是否存在压力，还可继续评估
- **问题 8**：未来若支持更多非文本 part 类型，这套 discriminated union 的扩展边界也值得关注

---

# 35. 小结

`message_model_and_part_taxonomy` 模块定义了 OpenCode 如何把一段会话历史表达成可持久化、可流式更新、可回放、可重新投影到模型输入的结构化对象：

- `User` 与 `Assistant` message 承担不同层级的 turn metadata
- `Part` 联合类型承载执行过程中的所有关键语义片段
- `updateMessage` / `updatePart` / `hydrate` 提供稳定的存取与广播路径
- `toModelMessages()` 则把这套内部模型重新投影成 provider 可消费的上下文

因此，这一层不是普通聊天消息 schema，而是 OpenCode 整个代理式执行、恢复、分享与 prompt 重建能力的基础数据模型。
