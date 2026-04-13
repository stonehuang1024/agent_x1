# Message State / Parts 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的消息与 part 数据模型。

核心问题是：

- OpenCode 为什么不把对话历史存成简单字符串数组
- `message` 和 `part` 分别承担什么职责
- 各种 part 类型分别表达什么语义
- 错误、工具结果、文件、摘要、补丁如何统一进入消息系统
- 历史如何被序列化、过滤、回放给模型

核心源码包括：

- `packages/opencode/src/session/message-v2.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/session/prompt.ts`

如果说 agent、tool、provider 负责“怎么运行”，那么 `MessageV2` 负责定义：

- **运行结果最终以什么形态成为系统状态真相**

---

# 2. 为什么 OpenCode 需要 `message + part` 两层结构

## 2.1 如果只有 message 会怎样

如果每一轮只存一条大文本消息，会遇到很多问题：

- 无法区分 text、reasoning、tool call、tool result
- 无法记录多段 streaming 文本的生命周期
- 无法表达一个 assistant message 中多个工具调用
- 无法记录 patch、snapshot、compaction、subtask 这样的非文本语义
- 无法精细回放历史给模型

## 2.2 两层结构的意义

OpenCode 将状态分成：

- **message**：一条高层会话消息
- **part**：消息内部的细粒度结构片段

这样做的好处是：

- 一条 assistant message 可以包含多个部分
- text、reasoning、tool、patch 可以并存
- 每个 part 有自己的状态、时间、metadata
- UI 可以按 part 类型精细渲染
- runtime 可以按 part 类型精细回放

这是一种典型的 **structured conversational state model**。

---

# 3. `MessageV2` 中的核心类型

## 3.1 message 级概念

虽然本篇重点在 part，但要先理解 message 层：

- user message
- assistant message

assistant message 上还会记录：

- `finish`
- `error`
- `cost`
- `tokens`
- `summary`
- `structured`
- `modelID`
- `providerID`
- `agent`
- `variant`

这说明 message 层主要承载：

- 一轮对话的高层属性
- 本轮模型调用的总体统计与终态

## 3.2 part 级概念

part 承载的是消息内部的具体内容与事件结果。

OpenCode 把很多本来会被混在文本里的东西显式类型化了。

---

# 4. `Part` 类型全景

从 `message-v2.ts` 可以看到，part 类型至少包括：

- `snapshot`
- `patch`
- `text`
- `reasoning`
- `file`
- `agent`
- `compaction`
- `subtask`
- `tool`
- `step-start`
- `step-finish`

这意味着一条消息不只是“几段文本”，而是一个小型事件流容器。

---

# 5. 各类 part 的职责与语义

## 5.1 `TextPart`

字段核心包括：

- `type: text`
- `text`
- `synthetic`
- `ignored`
- `time`
- `metadata`

### 语义

- 普通文本回答
- synthetic text 提示
- 某些系统自动插入的提示文本

### `synthetic`

表示这段文本不是用户直接输入，也不是模型自然生成，而是 runtime 合成消息的一部分。

例如：

- plan 切换提示
- compaction 之后的继续提示

### `ignored`

表示该文本 part 在某些上下文构造阶段可以被忽略。

## 5.2 `ReasoningPart`

字段包括：

- `type: reasoning`
- `text`
- `metadata`
- `time`

### 语义

专门表示模型 reasoning / thinking 内容。

它与普通 `text` 明确分离，说明 OpenCode 把 reasoning 视为 first-class output。

## 5.3 `FilePart`

字段包括：

- `mime`
- `filename`
- `url`
- `source`

### 语义

表示一份文件附件，可以来自：

- 用户上传
- tool 结果附件
- 图片/PDF 等富媒体

### `source`

`source` 又分为：

- `file`
- `symbol`
- `resource`

这说明文件 part 不一定只是“某个本地文件”，也可能代表：

- 某个符号提取结果
- 某个外部资源引用

这为后续更精细的上下文定位提供了基础。

## 5.4 `AgentPart`

字段包括：

- `type: agent`
- `name`
- `source`

### 语义

表示用户在 prompt 中显式提到了某个 agent。

这就是为什么 loop 中可以检测：

- 当前轮是否显式 `@agent`

并决定是否 `bypassAgentCheck`。

## 5.5 `CompactionPart`

字段包括：

- `auto`
- `overflow`

### 语义

表示一个 compaction 任务请求。

它不是摘要正文本身，而是触发“进行压缩总结”的任务标记。

## 5.6 `SubtaskPart`

字段包括：

- `prompt`
- `description`
- `agent`
- 可选 `model`
- 可选 `command`

### 语义

表示一个待执行的子任务请求。

在 loop 里，subtask 会被优先处理，并转换为 task tool 执行路径。

也就是说，subtask 是消息系统中用来承载“待派发子任务”的结构化命令。

## 5.7 `PatchPart`

字段包括：

- `hash`
- `files`

### 语义

表示某一步执行后，工作区出现的代码变更摘要。

它把“本轮到底改了哪些文件”显式记入消息系统。

## 5.8 `SnapshotPart`

字段包括：

- `snapshot`

### 语义

表示某个时刻的工作区快照标记，用于后续 patch 计算和回滚/比较。

## 5.9 `ToolPart`

虽然这里未重新完整贴出 schema，但从 processor 与 toModelMessages 能看出其核心字段：

- `tool`
- `callID`
- `state`
- `metadata`

其中 `state` 至少有：

- `pending`
- `running`
- `completed`
- `error`

### 语义

表示一次工具调用的全生命周期状态。

这就是 tool runtime 与 message state 结合的关键点。

---

# 6. 错误类型模型

`MessageV2` 里定义了多个内部错误类型：

- `OutputLengthError`
- `AbortedError`
- `StructuredOutputError`
- `AuthError`
- `APIError`
- `ContextOverflowError`

这说明 OpenCode 并不满足于把 error 存成普通字符串，而是维护了一套统一错误 taxonomy。

## 6.1 `StructuredOutputError`

表示：

- 当前消息要求 `json_schema`
- 但模型未按契约返回结构化结果

## 6.2 `ContextOverflowError`

表示：

- 当前输入超过上下文预算
- 通常需要走 compaction 分支

## 6.3 `APIError`

用于统一封装 provider/API 层错误，例如：

- message
- statusCode
- isRetryable
- responseHeaders
- responseBody
- metadata

这些错误字段会直接影响 retry / stop / compact 分支判断。

---

# 7. 为什么 `MessageV2.toModelMessages()` 是核心函数

这是整个消息系统最关键的桥梁函数之一。

它负责把：

- 内部 `MessageV2.WithParts[]`

变成：

- AI SDK 所需的 `ModelMessage[]`

也就是说，它定义了：

- 哪些历史会进入模型
- 以什么格式进入模型
- 不同 part 如何映射到 provider 消息协议

---

# 8. 用户消息的序列化规则

## 8.1 `text` part

直接转成 user text。

## 8.2 `file` part

- 若是普通支持的文件，则转成 user file
- 若 `stripMedia` 开启且是媒体，则降级成提示文本
- `text/plain` 与目录文件通常不会作为 file part 传给模型，而会走文本表达

## 8.3 `compaction` part

会转成：

- `What did we do so far?`

这说明 compaction part 本身不会原样暴露给模型，而会转成摘要请求语义。

## 8.4 `subtask` part

会转成：

- `The following tool was executed by the user`

这是一种对模型更容易理解的自然语言回放方式。

---

# 9. assistant 消息的序列化规则

## 9.1 `text`

转成 assistant text。

若当前模型与原消息模型不同，则会丢弃部分 provider metadata，以避免跨 provider 元数据污染。

## 9.2 `step-start`

转成 assistant 的 step-start 语义部分。

## 9.3 `reasoning`

转成 assistant reasoning。

这也是 reasoning 能持续回流到后续上下文的原因。

## 9.4 `tool` completed

转成：

- `tool-<name>`
- `state: output-available`
- `toolCallId`
- `input`
- `output`

## 9.5 `tool` error

转成：

- `tool-<name>`
- `state: output-error`
- `errorText`

## 9.6 `tool` pending/running

OpenCode 会把它也转成 `output-error`，并填：

- `[Tool execution was interrupted]`

这是为了满足某些 provider 对 tool call / tool result 成对闭合的协议要求。

这是一种非常重要的 **history normalization** 技术。

---

# 10. 媒体附件与 tool result 回放

## 10.1 问题背景

某些 provider 对 tool result 中的媒体支持不一致。

所以同一份 tool result，不能总是原样回放。

## 10.2 `toModelOutput()`

如果 tool output 是对象形式：

- `{ text, attachments }`

则会把 data URL 附件抽出，转成：

- text
- media

## 10.3 provider 能力差异

`supportsMediaInToolResults` 会根据 provider 判断：

- 是否允许在 tool result 中直接携带 media

如果不支持：

- 会把图片/PDF 抽出来
- 额外注入一条 user message：
  - `Attached image(s) from tool result:`
  - 后接 file parts

这说明消息系统不是被动存储层，而是主动参与 provider 协议兼容。

---

# 11. `filterCompacted()` 的意义

虽然这一篇重点不是 compaction，但消息系统的一个核心职责就是：

- 不让已被 summary 替换的旧历史继续污染上下文

因此 `filterCompacted()` 会在构造历史时：

- 过滤掉逻辑上已被 compaction 截断的消息段

这说明消息系统不仅存状态，还承担**历史窗口管理**角色。

---

# 12. `parts()`、`stream()`、`page()` 的作用

## 12.1 `parts(messageID)`

用于读取某条 message 的全部 parts。

processor、doom loop 检测、UI 展示都会依赖它。

## 12.2 `stream(sessionID)`

用于按会话顺序拉取消息流。

loop 几乎每轮都会基于它来计算上下文。

## 12.3 `page()`

用于分页获取消息历史。

这表明 message-v2 还承担对外历史访问 API 的一部分职责。

---

# 13. `fromError()` 的位置意义

虽然本次没有重读其完整实现，但从 processor 的用法可以确认：

- 所有异常最终都会尽量归一到 `MessageV2` 的内部错误类型上

这意味着 message-v2 并不只是“数据 schema 文件”，它还承担：

- runtime error normalization

的责任。

因此 `MessageV2` 其实是：

- **状态模型 + 历史序列化器 + 错误归一化器**

---

# 14. 为什么 `Part` 模型对 UI 和 runtime 都重要

## 14.1 对 UI

UI 可以根据 part 类型做不同展示：

- text 气泡
- reasoning 折叠块
- tool 执行卡片
- patch 文件列表
- 文件附件预览
- compaction / summary 标识

## 14.2 对 runtime

runtime 依赖它来：

- 判断上轮做过什么
- 判断 tool 是否未完成
- 判断是否 overflow
- 选择哪些历史回放
- 做 patch / summary / compaction

也就是说，part 不是为 UI 设计的临时视图层，而是 runtime 的基础状态单元。

---

# 15. 消息系统背后的几个关键设计思想

## 15.1 结构化优先

OpenCode 一开始就避免把“工具调用、文件、思考、补丁”混成一段文本。

## 15.2 可回放优先

所有输出最终都要能再次进入模型上下文。

因此 part 设计天然考虑了“如何回放”。

## 15.3 Provider 兼容在消息边界吸收

媒体、tool result、metadata 兼容问题，很多都在 `toModelMessages()` 层解决。

## 15.4 历史不是 append-only 文本，而是可裁剪状态流

通过 `filterCompacted()` 和各类 part，历史可以被逻辑裁剪、总结、回放。

---

# 16. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/session/message-v2.ts`
2. `packages/opencode/src/session/processor.ts`
3. `packages/opencode/src/session/prompt.ts`

重点盯住这些函数/概念：

- `TextPart`
- `ReasoningPart`
- `FilePart`
- `CompactionPart`
- `SubtaskPart`
- `ToolPart`
- `MessageV2.toModelMessages()`
- `MessageV2.filterCompacted()`
- `MessageV2.fromError()`
- `MessageV2.stream()`
- `MessageV2.parts()`

---

# 17. 下一步还需要深挖的问题

这一篇已经把消息系统主结构说明白了，但还有一些值得继续拆解的问题：

- **问题 1**：`ToolPart`、`step-start`、`step-finish` 等完整 schema 及字段边界还可以单独继续细拆
- **问题 2**：`MessageV2.fromError()` 对不同 provider/SDK 错误的映射细节还值得单独深入
- **问题 3**：`stream()` 的排序/游标/存储读取实现细节还可以继续展开
- **问题 4**：消息与 part 在数据库中的表结构、索引与性能策略还可继续分析
- **问题 5**：`providerMetadata` 在跨模型回放时为什么有的保留、有的剥离，具体边界可继续精读
- **问题 6**：附件在 storage / db / UI 中的持久化链路还可以继续单独拆解
- **问题 7**：`ignored`、`synthetic` 等标志位在各个 runtime 分支里的使用边界还可以继续追踪
- **问题 8**：summary / compaction / revert 与消息系统的交互还可以再展开成独立专题

---

# 18. 小结

`message_state_and_parts` 模块定义了 OpenCode 的状态真相形式：

- message 承载一轮交互的高层属性
- part 承载细粒度内容、工具状态、文件、推理、补丁与任务语义
- `toModelMessages()` 负责把历史状态重新序列化为模型上下文
- 错误模型与 compaction 过滤又保证了这份历史既可持续、又可压缩、又可兼容多 provider

因此，这个模块是 OpenCode 从“事件流”走向“持久化 agent 状态系统”的基础设施。
