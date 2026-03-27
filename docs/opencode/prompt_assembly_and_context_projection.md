# Prompt Assembly / Context Projection 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 prompt 拼装与上下文投影链路。

核心问题是：

- 一次 `SessionPrompt.prompt()` 调用如何从用户输入走到真正的模型请求
- 为什么 prompt 构造不是只拼接文本，而是要先构造 user message、清理 revert、裁剪历史、插入 reminder、投影成 model messages
- `SessionPrompt.loop()` 如何在 subtask、compaction、normal turn 三种路径间切换
- structured output、instruction prompt、skills、max-steps 提醒如何注入 system/context
- `resolveTools()` 如何把本地工具与 MCP 工具投影成可供模型调用的 tool set

核心源码包括：

- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/message-v2.ts`
- `packages/opencode/src/session/compaction.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/session/instruction.ts`

这一层本质上是 OpenCode 的**会话输入编排、历史重写与模型上下文投影引擎**。

---

# 2. 为什么 prompt 组装必须是独立模块

OpenCode 的 prompt 并不是：

- system + user text

这么简单。

真正参与构造的内容包括：

- 当前 session 与 permission
- 可能存在的 revert 清理
- user part（text/file/agent/subtask）
- message history + compacted filtering
- subtask/compaction 待执行任务
- reminder 注入
- tools / MCP tools
- structured output tool
- instruction prompt system additions
- provider/model 兼容投影

所以 `SessionPrompt` 实际上承担的是：

- **把会话状态变成下一次 LLM 可执行上下文**

---

# 3. `PromptInput`：用户输入不是纯文本，而是一组 part

`PromptInput` 支持：

- `sessionID`
- `messageID?`
- `model?`
- `agent?`
- `noReply?`
- `tools?`
- `format?`
- `system?`
- `variant?`
- `parts[]`

而 `parts` 可以是：

- `TextPartInput`
- `FilePartInput`
- `AgentPartInput`
- `SubtaskPartInput`

这说明 prompt 输入从一开始就是结构化的，不是到末尾才解析附件或 agent mention。

---

# 4. `SessionPrompt.prompt()`：外部入口的主流程

流程非常清晰：

1. `Session.get(sessionID)`
2. `SessionRevert.cleanup(session)`
3. `createUserMessage(input)`
4. `Session.touch(sessionID)`
5. 兼容旧 `tools` 字段并转成 session permission
6. 若 `noReply === true`，直接返回 user message
7. 否则进入 `loop({ sessionID })`

## 4.1 为什么先 cleanup revert

因为 prompt 组装前，系统必须先把“待清理的回退历史”收口。

否则后续读取消息流时，看到的会是未完成恢复的脏状态。

---

# 5. `assertNotBusy()` 与 state map：为什么 prompt loop 需要自己的运行控制

`SessionPrompt` 内部也有一份 per-session state：

- `abort: AbortController`
- `callbacks[]`

并用它实现：

- `assertNotBusy()`
- `start()`
- `resume()`
- `cancel()`

这说明 prompt loop 不只是调用 processor 一次，而是一个可持续运行、可恢复、可取消的 session-level orchestrator。

---

# 6. `start/resume/cancel`：loop 级生命周期管理

## 6.1 `start(sessionID)`

如果当前 session 还没在跑，就创建新的 `AbortController`。

## 6.2 `resume(sessionID)`

若已有运行中的 loop，则直接返回其 signal。

## 6.3 `cancel(sessionID)`

- abort 控制器
- 删除 state entry
- 把 `SessionStatus` 设回 `idle`

这说明 SessionPrompt 负责的是：

- 一个 session 同时只能有一个主 loop

并支持外部等待已有 loop 结果，而不是并发起多个冲突推理。

---

# 7. `loop()`：真正的 prompt orchestration 引擎

`loop()` 不是单次模型调用，它是整个 session 推进器。

主结构是：

- 获取/启动 abort signal
- `while (true)`
- 每轮重读消息流并决定下一步执行什么

## 7.1 为什么每轮都重读 `msgs`

它使用：

- `MessageV2.filterCompacted(MessageV2.stream(sessionID))`

重新读取历史。

这是非常关键的设计，因为：

- tool 调用会更新 parts
- compaction 可能改写上下文
- subtask 会插入新的 user/assistant message
- structured output 可能提前结束

所以 loop 每轮都基于**最新 authoritative history** 决策，而不是靠本地快照硬撑。

---

# 8. `filterCompacted(...)`：历史读入前先做上下文降噪

虽然这次没有重读其具体实现，但从调用位置可以明确看出：

- prompt loop 在消费消息流前，先经过 compacted filtering

这意味着：

- 旧工具输出被 prune/compaction 标记后，不会原样继续污染后续 prompt

也就是说，上下文裁剪发生在 prompt 投影入口，而不是只在存储层打标签。

---

# 9. loop 中如何识别“当前应该围绕哪条消息继续”

每轮 loop 都会从最新消息流中推导出：

- `lastUser`
- `lastAssistant`
- `lastFinished`
- `tasks`

其中 `tasks` 会收集尚未被完成 assistant 消化掉的：

- `compaction` part
- `subtask` part

这说明 session loop 的调度核心是：

- **先找当前最新的用户意图和待执行控制任务，再决定走哪条路径**

---

# 10. 为什么 `lastAssistant.finish` 会决定是否退出 loop

如果最近 assistant 已经 finish，且 finish reason 不在：

- `tool-calls`
- `unknown`

并且它确实晚于最近 user，则 loop 直接退出。

这说明 prompt loop 并不会无限继续调用模型，而是以 assistant finish reason 作为是否完成这轮调度的关键判据。

---

# 11. `ensureTitle(...)`：为什么第一步就会触发标题生成

在 `step === 1` 时，loop 会调用：

- `ensureTitle({ session, modelID, providerID, history: msgs })`

这说明 session 标题生成并不是独立后台任务，而是 prompt orchestration 第一轮里顺手保证的元数据动作。

其输入直接依赖当前 history，因此属于 prompt 层的一部分。

---

# 12. 三条主执行分支：subtask / compaction / normal

`loop()` 每轮都会优先判断 `task = tasks.pop()`，然后进入三种路径之一：

## 12.1 subtask 分支

如果 task 是 `subtask`：

- 不走 LLM normal turn
- 而是直接调用 `TaskTool`
- 生成 assistant tool message 和 tool part
- 必要时再加 synthetic user 让主 loop继续总结/接管

## 12.2 compaction 分支

如果 task 是 `compaction`：

- 直接调用 `SessionCompaction.process(...)`
- `stop` 则 break，否则 continue

## 12.3 normal 分支

如果没有控制任务，则进入普通 assistant turn。

这说明 prompt loop 同时扮演：

- 对话推进器
- 控制任务调度器

---

# 13. subtask 分支：为什么它不走普通模型回合

subtask part 已经是“系统决定要调用 task tool”的结构化意图。

因此这里直接：

- 创建 assistant message
- 写 running tool part
- 构造 `Tool.Context`
- 执行 `TaskTool`
- 落 completed/error tool part

## 13.1 为什么 task.command 后要插 synthetic user message

代码里明确写了：

- 某些 reasoning 模型如果 mid-loop 出现 assistant messages 而没有 user follow-up，会出错

所以会补一个 synthetic user：

- `Summarize the task tool output above and continue with your task.`

这说明 prompt assembly 不仅构造语义上下文，还会修补 provider/模型的对话格式不变量。

---

# 14. compaction 分支：loop 如何把恢复任务接回主轨道

当检测到：

- 有 pending compaction task

就调用：

- `SessionCompaction.process({ messages: msgs, parentID, abort, sessionID, auto, overflow })`

然后根据返回值：

- `stop` -> 终止 loop
- 否则 `continue`，重新进入下一轮 history 读取

这说明 compaction 不是侧路流程，而是被 loop 正式纳入调度闭环。

---

# 15. 自动 compaction 的插入点

即使没有显式 compaction task，loop 也会检查：

- 最近完成的 assistant 不是 summary
- 且 `SessionCompaction.isOverflow(lastFinished.tokens, model)`

若满足，就：

- `SessionCompaction.create(...)`
- 然后 `continue`

这说明 compaction task 本身也是 loop 动态插入的控制消息，而不是只能外部显式触发。

---

# 16. normal 分支前的 reminder 注入

在普通流程里会先：

- `insertReminders({ messages: msgs, agent, session })`

虽然这次没展开其实现，但从命名可知它会把系统 reminder 注入消息历史。

此外代码还明确在 `step > 1 && lastFinished` 时，对 queued user messages 包一层：

```text
<system-reminder>
The user sent the following message:
...
Please address this message and continue with your tasks.
</system-reminder>
```

这说明 OpenCode 会在多步连续执行中主动重投用户最新意图，避免 agent 偏航。

---

# 17. normal 分支中的 assistant message 创建

进入普通执行前，loop 会先创建一条新的 assistant message，写入：

- `parentID = lastUser.id`
- `mode = agent.name`
- `agent = agent.name`
- `variant = lastUser.variant`
- `path.cwd/root`
- `cost/tokens = 0`
- `providerID/modelID`
- `time.created`

然后把它交给：

- `SessionProcessor.create(...)`

这说明 prompt 层负责先把 turn 骨架建好，再把执行权交给 processor。

---

# 18. `resolveTools(...)`：工具集投影器

这是 prompt 层里另一个核心函数。

输入包括：

- `agent`
- `model`
- `session`
- `tools?`
- `processor`
- `bypassAgentCheck`
- `messages`

输出是：

- `Record<string, AITool>`

## 18.1 它为什么属于 prompt assembly

因为模型真正能调用哪些工具，是 prompt 上下文的一部分，而不是 processor 事后决定的。

所以 tools 的可见性和包装逻辑必须在调用模型前完成。

---

# 19. 本地工具如何被包装

`resolveTools()` 会遍历 `ToolRegistry.tools(...)`，对每个 tool：

- 用 `ProviderTransform.schema(...)` 适配 schema
- 包装成 AI SDK `tool(...)`
- 在 `execute()` 前后触发 plugin hook
- 构造统一 `Tool.Context`
- 把附件补成 message-scoped file parts

## 19.1 `Tool.Context` 里有什么

- `sessionID`
- `abort`
- `messageID`
- `callID`
- `extra.model`
- `extra.bypassAgentCheck`
- `agent`
- `messages`
- `metadata()` 回调
- `ask()` 权限询问

这说明 tool 包装不是简单函数适配，而是把工具真正接线进 session runtime。

---

# 20. MCP tools 如何被并入同一 tool set

`resolveTools()` 后半段还会遍历：

- `await MCP.tools()`

并对 MCP tool 做同样包装：

- plugin hooks
- permission ask
- 执行结果转文本与附件
- `Truncate.output(...)`
- metadata `truncated/outputPath`

这说明在 prompt 层看来：

- 本地 tool
- MCP tool

最终都被投影为统一的 model-callable tool 集。

---

# 21. StructuredOutput tool：为什么结构化输出也被建模成工具

如果 `lastUser.format.type === "json_schema"`，loop 会注入：

- `tools["StructuredOutput"] = createStructuredOutputTool(...)`

同时 system prompt 追加：

- `STRUCTURED_OUTPUT_SYSTEM_PROMPT`

并把 `toolChoice` 设成：

- `required`

## 21.1 这说明什么

OpenCode 没有要求模型“直接输出 JSON”，而是要求模型：

- 最终必须调用一个 schema-validated tool

这比纯文本 JSON 可靠得多，也更符合 agent/tool runtime 架构。

---

# 22. structured output 成功与失败的收口

## 22.1 成功

如果 `structuredOutput !== undefined`：

- 写入 `processor.message.structured`
- 设 finish
- `Session.updateMessage(...)`
- 直接 break

## 22.2 失败

如果模型已经 finish，但没调用 StructuredOutput tool：

- 写 `StructuredOutputError`
- 更新 message
- break

这说明 structured output 在 prompt 层有明确成功/失败判定，不依赖后续模糊解析。

---

# 23. system prompt 的构成

normal 分支里 system prompt 由三部分组成：

- `SystemPrompt.environment(model)`
- `SystemPrompt.skills(agent)`
- `InstructionPrompt.system()`

若是 json schema 输出，再加：

- `STRUCTURED_OUTPUT_SYSTEM_PROMPT`

这说明 system 层同样是多来源合成，而不是单模板字符串。

---

# 24. 最终模型消息是如何拼起来的

传给 processor 的 `messages` 是：

- `...MessageV2.toModelMessages(msgs, model)`
- 如果已达到 `maxSteps`，再追加一个 assistant content = `MAX_STEPS`

## 24.1 为什么 `MAX_STEPS` 作为额外 assistant message 注入

这本质上是在 prompt 尾部增加一个强提醒：

- 已达到最大步骤数
- 应该收敛/结束

这也是 prompt assembly 的一部分，而不是外部控制变量。

---

# 25. `MessageV2.toModelMessages(...)`：上下文投影的真正边界

prompt 层最终并不手工拼 provider request content，而是把结构化历史交给：

- `MessageV2.toModelMessages(msgs, model)`

因此 prompt assembly 与 context projection 的边界是：

- `SessionPrompt` 负责决定“哪些历史、哪些提醒、哪些工具”
- `MessageV2` 负责把这些历史转成 provider 可消费消息

这是非常清晰的职责分工。

---

# 26. `createUserMessage(...)`：输入归一化器

这个函数是整个 prompt 链路的起点之一。

它会：

- 解析 agent/model/variant
- 创建 user `MessageV2.Info`
- 清理 instruction prompt 作用域
- 将输入 parts 统一赋 id
- 特殊处理 MCP resource
- 特殊处理 data URL / 本地文件 / 目录
- 在某些情况下主动调用 read tool 或提取文本内容

这说明用户输入在真正进入 history 前，已经过一轮重投影与规范化。

---

# 27. `resolvePromptParts(template)`：Markdown 模板到结构化 part 的转换

这个辅助函数会从模板中提取：

- 文件路径引用
- 目录引用
- agent 名称引用

然后把它们变成：

- `text`
- `file`
- `agent`

parts。

这说明 even 配置/模板输入也会被统一转换到 part 模型，而不是走完全不同的数据路径。

---

# 28. prompt assembly 的完整数据流

可以把整个过程概括成：

## 28.1 输入归一化

- `PromptInput`
- `createUserMessage()`
- user message + parts 落库

## 28.2 历史准备

- `SessionRevert.cleanup()`
- `MessageV2.stream()`
- `filterCompacted()`
- `insertReminders()`

## 28.3 控制任务分派

- subtask
- compaction
- normal turn

## 28.4 上下文投影

- `resolveTools()`
- system prompt 合成
- `MessageV2.toModelMessages()`
- structured output/max steps 提示追加

## 28.5 交给执行引擎

- `SessionProcessor.process()`

这就是 OpenCode prompt assembly 的主干。

---

# 29. 为什么 prompt 层必须知道 session permission 和 agent permission

在 `resolveTools()` 的 `ask()` 中会：

- `PermissionNext.merge(input.agent.permission, input.session.permission ?? [])`

这说明工具可否真正执行，不仅由 agent 默认能力决定，也受当前 session 覆盖权限影响。

因此 prompt 层不是纯字符串拼装器，它必须理解权限语义，才能正确投影 tool set。

---

# 30. 这个模块背后的关键设计原则

## 30.1 prompt 不是字符串，而是运行时上下文的投影结果

所以先有 message history、tasks、permissions、tools、system，再有 prompt。

## 30.2 会话推进必须是循环调度，而不是单次调用

所以有 `loop()` 管 subtask、compaction、normal 分支。

## 30.3 tools 必须在投影阶段统一包装并接线进 session runtime

否则模型工具调用无法与消息系统、权限系统和 share 系统对齐。

## 30.4 structured output 应作为工具协议处理，而不是依赖模型自觉输出 JSON

这显著提高可靠性。

---

# 31. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/session/prompt.ts`
2. `packages/opencode/src/session/message-v2.ts`
3. `packages/opencode/src/session/processor.ts`
4. `packages/opencode/src/session/compaction.ts`
5. `packages/opencode/src/session/instruction.ts`
6. `packages/opencode/src/session/system.ts`

重点盯住这些函数/概念：

- `SessionPrompt.prompt()`
- `SessionPrompt.loop()`
- `createUserMessage()`
- `resolveTools()`
- `StructuredOutput` tool
- `insertReminders()`
- `MessageV2.toModelMessages()`
- `MAX_STEPS`

---

# 32. 下一步还需要深挖的问题

这一篇已经把 prompt 拼装与上下文投影主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`insertReminders()` 的完整策略、注入条件和文案来源还值得单独展开
- **问题 2**：`createUserMessage()` 对本地文件/data URL/MCP resource 的处理分支非常长，还可以单独拆成附件摄取文档
- **问题 3**：`ensureTitle()` 的具体标题生成 prompt 与写回策略还值得继续追踪
- **问题 4**：`InstructionPrompt.system()` 与 `InstructionPrompt.resolve()` 的协作关系还可进一步精读
- **问题 5**：subtask command 场景下 synthetic user 插入是否适用于所有 provider，还值得继续验证
- **问题 6**：session permission 与 agent permission merge 的优先级细节还可以继续系统化总结
- **问题 7**：`experimental.chat.messages.transform` 和其他 plugin hooks 的组合顺序是否可能产生冲突，还值得进一步观察
- **问题 8**：多 workspace / remote workspace 模式下，当前 prompt assembly 是否需要 workspace-aware 上下文投影，还值得继续追踪

---

# 33. 小结

`prompt_assembly_and_context_projection` 模块定义了 OpenCode 如何把结构化用户输入、当前会话历史、控制任务、权限、工具集与系统提示共同投影成一次可执行的模型上下文：

- `SessionPrompt.prompt()` 负责外部入口与用户消息落库
- `SessionPrompt.loop()` 负责持续调度 subtask、compaction 与普通 turn
- `resolveTools()` 把本地与 MCP 工具统一包装成模型可调用能力
- `MessageV2.toModelMessages()` 则把内部消息历史转成 provider 可消费上下文

因此，这一层不是简单 prompt builder，而是 OpenCode 会话编排、上下文重写与模型输入投影的总控制器。
