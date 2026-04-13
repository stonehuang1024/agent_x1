# Session Compaction Trigger / Summary Message Pipeline 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 compaction 管线：也就是当上下文过长时，系统如何判定溢出、裁剪旧上下文、调用 `compaction` agent 生成摘要消息，并在必要时 replay 用户请求继续执行。

核心问题是：

- `SessionCompaction.isOverflow()` 如何判断当前轮已经逼近模型输入上限
- `prune()` 与 `process()` 分别解决什么问题
- 为什么 compaction 不是简单“删旧消息”，而是要生成一条 `summary: true` 的 assistant message
- overflow 场景为什么要找一个 `replay` user message
- compaction 后为什么还会插入一条 synthetic continue user 提示

核心源码包括：

- `packages/opencode/src/session/compaction.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/message-v2.ts`
- `packages/opencode/src/provider/transform.ts`

这一层本质上是 OpenCode 的**上下文预算控制、摘要重写与对话续跑基础设施**。

---

# 2. 为什么 compaction 必须存在

长对话的根本问题是：

- 历史消息越来越多
- 工具输出可能极长
- 文件/媒体上下文会持续堆积
- 模型输入窗口终究有限

如果没有 compaction，系统只能：

- 直接报 context overflow
- 或盲删旧内容，破坏连续性

OpenCode 的选择是：

- 先尽量保留重要上下文
- 必要时生成一个可续跑的摘要消息
- 然后让会话继续

这是典型的 agentic 长会话管理策略。

---

# 3. `isOverflow()`：溢出判定的核心逻辑

`SessionCompaction.isOverflow({ tokens, model })` 会：

1. 若 `config.compaction.auto === false` -> false
2. 若 `model.limit.context === 0` -> false
3. 计算当前 token count：
   - `tokens.total`
   - 或 `input + output + cache.read + cache.write`
4. 计算 reserved：
   - `config.compaction.reserved`
   - 否则 `min(20_000, maxOutputTokens(model))`
5. 计算 usable：
   - 若 model 有 `limit.input`，则 `limit.input - reserved`
   - 否则 `context - maxOutputTokens(model)`
6. 若 `count >= usable` -> overflow

## 3.1 含义

这不是等真正撞到 provider hard limit 才触发，而是：

- 给输出与系统余量预留 buffer
- 提前在接近上限时启动 compaction

这样更稳定。

---

# 4. 为什么要留 `reserved` buffer

如果把输入上下文塞满到模型极限：

- tool call / reasoning / output token 空间会不足
- provider 可能直接拒绝
- 模型行为会变得不稳定

因此 compaction 判定不是看“是否超过最大 context”，而是看：

- 是否超过“可安全使用的输入预算”

这个 reserved buffer 很关键。

---

# 5. `SessionProcessor` 如何触发 compaction

在 `finish-step` 后，processor 会检查：

- 当前 message 不是 summary message
- `SessionCompaction.isOverflow({ tokens, model })`

若是，则：

- `needsCompaction = true`

最终 `process()` 返回：

- `compact`

此外，如果错误被 `MessageV2.ContextOverflowError` 识别，也会：

- `needsCompaction = true`

这说明 compaction 既可能是：

- 预防性触发
- 也可能是 overflow 后补救性触发

---

# 6. `SessionPrompt.loop()` 与 compaction 的衔接

虽然主逻辑在 `prompt.ts`，但可以看出 loop 会把：

- `processor.process(...) === "compact"`

转交给 `SessionCompaction.process(...)`。

这说明：

- processor 负责发现需要压缩
- compaction 模块负责真正执行压缩
- loop 负责调度它们

分层很清楚。

---

# 7. `prune()`：为什么 compaction 前还有一层工具输出裁剪

`prune({ sessionID })` 的目标不是生成摘要，而是：

- 回头扫描旧消息
- 找出过旧、过大的 completed tool outputs
- 把它们标记为 `time.compacted`

## 7.1 关键常量

- `PRUNE_MINIMUM = 20_000`
- `PRUNE_PROTECT = 40_000`
- `PRUNE_PROTECTED_TOOLS = ["skill"]`

## 7.2 含义

compaction 不一定一上来就用 LLM 重写整个历史。

系统先尝试做更便宜的裁剪：

- 把足够老的、很长的 tool outputs 标记为已压缩

这样 `MessageV2.filterCompacted(...)` 后，历史就能先瘦身一轮。

---

# 8. `prune()` 的扫描策略

它从最新消息向前扫：

- 至少保留最近 2 个 user turns
- 一旦遇到 summary assistant (`msg.info.summary`) 就停
- 只看 completed tool parts
- `skill` 工具不裁
- 已经 compacted 过的输出直接成为停止边界

## 8.1 含义

这体现了几个设计原则：

- 最近上下文优先保留
- summary 之前的历史是上一轮压缩边界，不要反复跨边界破坏
- skill 输出更可能是高价值结构化指引，所以保护

---

# 9. 为什么 `prune()` 只标记 `time.compacted` 而不直接删 output

从当前代码看，它主要是：

- `part.state.time.compacted = Date.now()`
- `Session.updatePart(part)`

真正是否在模型历史中隐藏这些输出，交给：

- `MessageV2.filterCompacted(...)`
- `toModelMessages(...)`

## 9.1 含义

系统把“数据还在数据库中”和“是否继续投给模型”分开处理。

这是很好的设计：

- 审计/history 仍完整
- 模型上下文可变瘦

---

# 10. `process()`：compaction 的正式执行入口

`SessionCompaction.process(input)` 会接收：

- `parentID`
- `messages`
- `sessionID`
- `abort`
- `auto`
- `overflow?`

它不是纯函数，而是会真正：

- 创建 summary assistant message
- 调 compaction agent
- 必要时 replay 用户消息
- 发布 `session.compacted`

---

# 11. overflow 场景下为什么要找 `replay`

当 `input.overflow` 为真时，代码会从触发点往前找：

- 最近一条 user message
- 且该 user message 不是 compaction message

找到后：

- `replay = msg`
- `messages = input.messages.slice(0, i)`

## 11.1 含义

在真实 overflow 场景里，当前这条用户请求可能连“被完整处理一次”都没来得及完成。

所以 compaction 后需要：

- 保留更早历史用于生成摘要
- 再把这条用户请求重新放回队列，让系统重试

这就是 replay 机制的意义。

---

# 12. 为什么 `hasContent` 不成立时要放弃 replay

它还会检查：

- `messages` 中是否还有至少一条非 compaction 的 user message

如果没有，则：

- `replay = undefined`
- `messages = input.messages`

## 12.1 含义

如果把历史切掉后只剩空壳，compaction agent就没有足够上下文可总结。

这时与其硬 replay，不如用完整历史直接尝试压缩。

---

# 13. compaction 为什么用专门的 `compaction` agent

`process()` 会：

- `Agent.get("compaction")`
- 若 agent 自带 model 就用之
- 否则复用当前 user message 的 provider/model

同时创建一条 assistant message：

- `mode = "compaction"`
- `agent = "compaction"`
- `summary = true`

## 13.1 含义

compaction 被视为系统内部专门任务，而不是普通 assistant 延续。

其输出语义是：

- 面向未来 agent 的工作摘要

因此独立 agent 非常合理。

---

# 14. `summary: true` 的意义

这条 compaction assistant message 会被标记：

- `summary = true`

这让后续系统能识别：

- 这是摘要消息，不是普通回答
- 某些流程（如 prune / loop / compaction boundaries）应特殊对待它

例如 `prune()` 遇到 `msg.info.summary` 就会停止回扫。

---

# 15. compaction prompt 的默认模板

默认 prompt 要求 compaction agent 生成：

- `## Goal`
- `## Instructions`
- `## Discoveries`
- `## Accomplished`
- `## Relevant files / directories`

## 15.1 这说明什么

这不是“随便总结一下聊天历史”，而是明确要求生成：

- 可供下一个 agent 接手工作的 continuation brief

它本质上是一个 handoff summary，而不是对用户的最终答复。

---

# 16. `experimental.session.compacting`：插件可改写 compaction prompt

在真正调用 processor 前，会触发：

- `Plugin.trigger("experimental.session.compacting", { sessionID }, { context: [], prompt: undefined })`

最终：

- `promptText = compacting.prompt ?? [defaultPrompt, ...compacting.context].join("\n\n")`

## 16.1 含义

插件可以：

- 完全替换 compaction prompt
- 或追加额外上下文

这让 compaction 策略可以按产品/组织场景定制。

---

# 17. compaction 调用时为什么 `tools: {}`

它调用 `processor.process()` 时传入：

- `tools: {}`

说明 compaction agent 的目标纯粹是：

- 读取已有历史
- 输出摘要

而不是在压缩过程中继续执行工具。

这避免 compaction 递归膨胀成新的工具工作流。

---

# 18. 为什么 `toModelMessages(messages, model, { stripMedia: true })`

compaction 时会：

- `stripMedia: true`

这表示在做摘要时，即使原历史里有大图片/PDF/媒体，也尽量不再把这些媒体实体重新塞进 compaction 模型上下文。

## 18.1 含义

压缩的目的是减负，不是把所有重型上下文再重复一次。

这也是 overflow 场景下成功 compaction 的关键。

---

# 19. compaction 自己也可能再次 overflow

如果 compaction 的 `processor.process()` 返回：

- `compact`

系统会把 compaction message 标成：

- `ContextOverflowError`
- `finish = "error"`

错误消息分两种：

- 有 replay：`Conversation history too large to compact...`
- 无 replay：`Session too large to compact...`

然后返回：

- `stop`

## 19.1 含义

系统没有做 fallback 套娃压缩，而是明确承认：

- 连压缩都压不下时，当前模型就无法承担该会话

这和“不要靠多重 fallback 掩盖失败”的设计原则一致。

---

# 20. auto compaction 成功后为什么要 replay 用户请求

当：

- `result === "continue"`
- `input.auto === true`
- 且存在 `replay`

系统会：

1. 复制原 user message 的头信息
2. 重建一条新的 user message
3. 复制 replay parts（跳过 compaction part）
4. 若 part 是 media file，则替换成文本占位：
   - `[Attached mime: filename]`
5. 再额外插一条 synthetic text part，提示：
   - 历史已 compacted
   - 如因大附件导致 overflow，要向用户解释附件太大
   - `Continue if you have next steps...`

## 20.1 含义

compaction 成功后不是直接结束，而是自动把“刚才没处理完的请求”重新送回 loop。

这才实现了真正无缝续跑。

---

# 21. 为什么 replay 时要把 media file 降级成文本占位

在 overflow 场景里，原问题很可能就是因为大媒体导致超限。

所以 replay 时：

- 不再把真实媒体内容重新喂给模型
- 而是只保留一个文本占位说明它存在过

这样模型至少知道：

- 用户上传过某个附件

但不会再次因为附件本体导致爆 context。

---

# 22. synthetic continue 文本的作用

这段 synthetic 文本会提醒模型：

- 历史已经 compacted
- 若附件太大导致无法处理，应向用户解释
- 如果还能继续，就继续；否则请求澄清

## 22.1 含义

这不是简单的系统提示补丁，而是 compaction 之后的桥接指令。

它帮助新一轮 agent 在“摘要历史 + replay 请求”的混合上下文里作出正确行动。

---

# 23. `MessageV2.filterCompacted()`：compaction 对后续 history 的影响

在 `SessionPrompt.loop()` 每轮开头，会先：

- `MessageV2.filterCompacted(MessageV2.stream(sessionID))`

这说明一旦某些 tool outputs 被 prune 标记或某些 compaction summary 已建立，后续送进模型的 history 会被过滤/瘦身。

也就是说 compaction 并不是一次性替换，而是持续影响后续上下文投影。

---

# 24. compaction part 本身的角色

grep 可见系统还会写入：

- `type: "compaction"`
- `auto`
- `overflow`

这种 part 的作用是：

- 作为历史中的控制标记
- 告诉后续流程哪些消息是 compaction 相关节点
- 帮助 replay / prune / loop 判断边界

它不是给模型看的业务内容，而是运行时控制元数据。

---

# 25. `session.compacted` 事件

当 compaction 成功且 message 无 error：

- `Bus.publish(SessionCompaction.Event.Compacted, { sessionID })`

这说明 compaction 是正式的 session 生命周期事件，可被 UI 或插件监听。

---

# 26. prune 与 summary compaction 的关系

可以这样理解：

## 26.1 prune

- 更轻量
- 不调用模型
- 只裁旧 tool outputs 的可见性

## 26.2 process/summary compaction

- 更重
- 调用专门 compaction agent
- 生成新的 summary assistant message
- 必要时 replay 用户请求

两者是递进关系，不是替代关系。

---

# 27. 一个完整的 compaction lifecycle

可以概括为：

## 27.1 正常执行逐步增长上下文

- tool outputs
- assistant texts
- attachments
- history all accumulate

## 27.2 进入接近上限或真实 overflow

- `isOverflow()` 为真
- 或 catch 到 `ContextOverflowError`

## 27.3 loop 调度 compaction

- 可先 prune old tool outputs
- 再调用 `SessionCompaction.process()`

## 27.4 生成 summary assistant message

- `agent=compaction`
- `summary=true`
- 输出 continuation brief

## 27.5 若是 overflow 且有 replay

- 重建原用户请求
- 降级媒体为文本占位
- 插 synthetic continue 指令
- 让 loop 继续

## 27.6 后续历史投影瘦身

- `filterCompacted()`
- summary 成为新的历史锚点

这就是 OpenCode 的上下文续跑闭环。

---

# 28. 这个模块背后的关键设计原则

## 28.1 长会话必须优先尝试语义压缩，而不是直接失败

所以有 compaction agent summary pipeline。

## 28.2 裁剪与总结应分层处理

所以先 prune，再必要时做 summary compaction。

## 28.3 overflow 后真正重要的是“让任务继续”

所以有 replay 用户请求与 synthetic continue bridge。

## 28.4 附件/媒体是上下文爆炸高风险源，必须在 compaction 时显式降级

所以有 `stripMedia` 与 replay 时的文本占位替换。

---

# 29. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/session/compaction.ts`
2. `packages/opencode/src/session/processor.ts`
3. `packages/opencode/src/session/prompt.ts`
4. `packages/opencode/src/session/message-v2.ts`
5. `packages/opencode/src/provider/transform.ts`

重点盯住这些函数/概念：

- `SessionCompaction.isOverflow()`
- `SessionCompaction.prune()`
- `SessionCompaction.process()`
- `experimental.session.compacting`
- `summary: true`
- `replay`
- `filterCompacted()`
- `ContextOverflowError`

---

# 30. 下一步还需要深挖的问题

这一篇已经把 compaction 主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`compaction.ts` 后半段 replay part 写入完成后的完整细节还值得继续读完
- **问题 2**：`MessageV2.filterCompacted()` 与 `toModelMessages()` 如何精确隐藏 compacted tool outputs，还值得单独拆读
- **问题 3**：`summary=true` assistant message 在 UI 中如何显示、是否对用户显式可见，还值得继续查看前端层
- **问题 4**：compaction prompt 模板本身是否最适合所有任务类型，还值得继续从不同场景验证
- **问题 5**：当 compaction 也 overflow 时，目前直接 stop，是否需要提示更换更大上下文模型，还值得继续追踪上层 UX
- **问题 6**：`prune()` 对 `skill` 工具做保护，其余工具为何不细分优先级，还值得继续思考
- **问题 7**：auto compaction replay 时保留了 user/system/tools/variant 字段，这些字段在极端多轮场景下是否足够，还值得继续验证
- **问题 8**：overflow 由大媒体触发时，目前只给文字解释建议，未来是否需要更结构化附件降采样策略，还值得关注

---

# 31. 小结

`session_compaction_trigger_and_summary_message_pipeline` 模块定义了 OpenCode 如何在长会话逼近或超出上下文上限时，把历史裁剪、摘要重写与任务续跑串成一个稳定流程：

- `isOverflow()` 提前基于输入预算与 reserved buffer 判定风险
- `prune()` 先轻量隐藏旧 tool outputs
- `process()` 再通过专门的 `compaction` agent 生成 continuation summary message
- overflow 场景下还会 replay 用户请求，并对媒体上下文做降级处理

因此，这一层不是简单的“压缩历史”，而是 OpenCode 保持长会话可持续执行、可恢复上下文与任务连续性的核心记忆管理基础设施。
