# Session Title Generation / History Summarization 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的标题生成与历史摘要链路。

核心问题是：

- session 标题为什么不是创建时立即固定，而是由后续 LLM 生成
- `ensureTitle()` 为什么只在特定时机触发
- subtask-only 的首条消息为什么要特殊处理标题上下文
- `SessionSummary.summarize()` 同时更新了哪些摘要层次
- diff 摘要为什么依赖 snapshot/step part 而不是直接扫描文件系统现状

核心源码包括：

- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/summary.ts`
- `packages/opencode/src/agent/agent.ts`
- `packages/opencode/src/session/message-v2.ts`

这一层本质上是 OpenCode 的**会话可读性增强与历史变化摘要基础设施**。

---

# 2. 为什么标题生成与 summary 要单独建链路

会话系统中有两类“帮助人理解历史”的元信息：

## 2.1 标题

回答：

- 这个会话大致在做什么

## 2.2 summary / diff

回答：

- 这轮或这个会话到底改了哪些文件、增删了多少内容

两者关注点不同：

- 标题偏语义摘要
- diff summary 偏工作结果摘要

因此 OpenCode 没有试图用一个统一字段糊弄过去，而是单独维护。

---

# 3. `ensureTitle()`：标题生成发生在 prompt loop 的第一步

在 `SessionPrompt.loop()` 中：

- `step++`
- `if (step === 1) ensureTitle(...)`

这说明标题生成不是后台批处理，也不是 session create 时同步执行。

而是：

- 当第一轮真正有上下文可供理解时，再异步触发

这是很合理的时机选择。

---

# 4. 为什么标题不是在 `Session.create()` 时生成

创建 session 时，系统往往只知道：

- 一个默认标题
- 或仅知道会话刚开始

但真正能代表会话主题的信息，通常来自：

- 第一条真实用户消息
- 甚至前置的 shell/subtask 历史

所以等到 `loop()` 第一轮有历史可看时再生成标题，语义会更准确。

---

# 5. `ensureTitle()` 的前置条件非常克制

它会依次检查：

- 若当前 session 有 `parentID`，直接 return
- 若当前 title 已不是默认标题，直接 return
- 若找不到第一条非 synthetic user message，return
- 若真实 user message 不止一条，return

## 5.1 含义

标题生成只会发生在：

- 根 session
- 仍使用默认标题
- 只有第一条真实用户输入时

这避免：

- 子 session/子任务频繁重命名
- 会话已经有人工/稳定标题后被覆盖
- 多轮后标题漂移

这是非常稳健的策略。

---

# 6. “第一条真实用户消息”为什么要排除 synthetic

`ensureTitle()` 会把：

- 所有 parts 都是 `synthetic` 的 user message

视为不算“真实用户消息”。

这很关键，因为在 OpenCode 里很多系统动作会插入 synthetic user，例如：

- shell 后续提示
- subtask 总结提示
- compaction continue 提示

如果这些也算首条用户消息，就会生成非常糟糕的标题。

---

# 7. 标题上下文并不是只看第一条用户消息

它会先找到：

- 第一条真实 user message 的索引

然后取：

- `contextMessages = history.slice(0, firstRealUserIdx + 1)`

也就是说，标题生成会看到：

- 该真实用户消息之前的上下文

包括注释里明确提到的：

- shell/subtask executions that preceded the user's first prompt

这说明标题生成不是机械总结一句话，而是会参考前置上下文事件。

---

# 8. subtask-only 首条消息为什么要特殊处理

如果首条真实 user message：

- 含有 `subtask` parts
- 且所有 parts 都是 `subtask`

则 `hasOnlySubtaskParts = true`

后续标题生成时不会直接 `toModelMessages(contextMessages, model)`，而是：

- 把所有 `subtask.prompt` 直接拼接成 user content

## 8.1 为什么这么做

源码注释写得很清楚：

- `toModelMessages` 会把 subtask part 转成泛化文本：
  - `The following tool was executed by the user`

这对标题生成信息量太低。

因此标题路径直接拿原始 `subtask.prompt`，能更准确反映真实主题。

这是很好的 root-cause 修正，而不是容忍错误标题。

---

# 9. 标题生成使用专用 `title` agent

`ensureTitle()` 会：

- `Agent.get("title")`

然后选模型：

- 若 `title` agent 配置了 model，优先用它
- 否则优先 `Provider.getSmallModel(providerID)`
- 再退回 `Provider.getModel(providerID, modelID)`

## 9.1 含义

标题生成被视为：

- 一个独立、轻量、低成本的系统任务

因此优先 small model 很合理，能降低成本与延迟。

---

# 10. 标题生成如何调用 LLM

它直接走：

- `LLM.stream({... small: true, tools: {}, retries: 2, ... })`

messages 前缀是：

- `Generate a title for this conversation:`

后接：

- subtask prompt 文本
- 或完整 contextMessages 的 model 投影

这说明标题生成不是走完整 processor/part 持久化流程，而是一次轻量 LLM 调用，最终只取文本结果。

---

# 11. 标题生成结果如何清洗

得到 `text` 后会：

1. 去掉 `<think>...</think>`
2. split lines
3. trim 每行
4. 取第一条非空行
5. 超过 100 字符则截断到 97 + `...`
6. `Session.setTitle({ sessionID, title })`

## 11.1 这说明什么

系统明确知道某些模型可能输出思考标记或多行内容，因此做了专门清洗。

标题最终追求的是：

- 单行
- 可读
- 短小

---

# 12. 为什么 `ensureTitle()` 用 `LLM.stream()` 而不是 `generateObject`

标题本质上只是短文本结果，不需要结构化对象。

直接用：

- `result.text`

最简单直接。

同时继续复用 provider/model/system prompt 投影体系，避免另一套标题专用模型调用逻辑分叉。

---

# 13. `SessionSummary.summarize()`：摘要更新的主入口

`summary.ts` 里它会同时执行：

- `summarizeSession(...)`
- `summarizeMessage(...)`

说明 OpenCode 把摘要分成两个层级：

## 13.1 session 级摘要

聚合整个 session 的 diff 统计。

## 13.2 message 级摘要

挂到某条 user message 的 `summary.diffs` 上。

这让全局概览与局部回合摘要可以并存。

---

# 14. `summarizeSession()`：会话级 diff 汇总

它会：

1. `computeDiff({ messages })`
2. `Session.setSummary({ additions, deletions, files })`
3. `Storage.write(["session_diff", sessionID], diffs)`
4. `Bus.publish(Session.Event.Diff, { sessionID, diff: diffs })`

## 14.1 意义

session summary 不是只写数据库字段，它同时：

- 更新持久化 diff 文件缓存
- 发实时 diff 事件

所以 UI、share、API 都能复用同一份结果。

---

# 15. `summarizeMessage()`：为什么把 diff 摘要挂回 user message

它会筛出：

- 当前 user message 本身
- 以及 `assistant.parentID === messageID` 的 assistant message

然后对这组消息 `computeDiff()`，并把结果写进：

- `userMsg.summary.diffs`

## 15.1 含义

OpenCode 认为一个 user turn 的摘要，应该落在：

- 提出该请求的 user message 上

而不是只在 assistant 上记录。

这很利于按用户回合查看“这一问带来了什么变更”。

---

# 16. `computeDiff()`：为什么依赖 step snapshot 而不是直接 diff 当前文件系统

它会扫描所有消息的 parts，找到：

- 最早 `step-start.snapshot` -> `from`
- 最晚 `step-finish.snapshot` -> `to`

如果两者都存在，则：

- `Snapshot.diffFull(from, to)`

否则返回 `[]`

## 16.1 这说明什么

摘要要表达的是：

- 某段会话执行所造成的变化

而不是“当前工作树现状”。

所以必须基于执行边界快照，而不是直接读现在的文件系统，否则会混入：

- 后续手工修改
- 其他 session 造成的改动

这是非常关键的语义边界。

---

# 17. 为什么只看最早 start 和最晚 finish

这意味着 OpenCode 想得到的是：

- 从本段执行开始到本段执行结束的总变化

而不是每一步独立 diff 的集合。

这适合 session/message summary 的使用场景，因为它更偏：

- 总体成果摘要

如果以后需要逐步细分，已有 patch part 可作为更细粒度来源。

---

# 18. `diff()`：为什么还要做 git path unquote

`SessionSummary.diff()` 会从 `Storage.read(["session_diff", sessionID])` 读出 diff，再对某些路径做：

- `unquoteGitPath()`

目的是把被 git-style quoting 的文件名恢复成正常文本。

## 18.1 含义

摘要系统不仅计算 diff，还负责把 diff 结果正规化成适合 UI/API 消费的格式。

这说明它不是简单缓存层，而是有展示语义责任。

---

# 19. summary 更新在什么时候触发

从 `processor.ts` 可见，在 `finish-step` 时会调用：

- `SessionSummary.summarize({ sessionID, messageID: assistantMessage.parentID })`

在 `prompt.ts` 的第一步 normal turn 中还会：

- `SessionSummary.summarize({ sessionID, messageID: lastUser.id })`

这说明 summary 不是会话结束后统一离线跑，而是在关键执行边界持续刷新。

---

# 20. 标题与 summary 的关系：都服务可理解性，但路径不同

可以这么区分：

## 20.1 标题

- 依赖 LLM
- 语义化
- 只在早期生成一次

## 20.2 summary / diff

- 依赖 snapshot/patch
- 结构化
- 会反复更新

因此它们互补，而不是替代关系。

---

# 21. 为什么 summary agent 在当前链路里没有显式单独调用

虽然 `agent.ts` 定义了隐藏 `summary` agent，但本次读到的主摘要链路 `SessionSummary.summarize()` 并不直接调用它。

这说明当前系统至少存在两类 summary 概念：

- 基于 snapshot diff 的结构化摘要
- 可能由 summary agent 支撑的其他语义摘要用途

这是后续值得继续深挖的分叉点。

---

# 22. 标题生成如何避免对子任务 session 乱命名

`ensureTitle()` 首先就排除了：

- `input.session.parentID`

也就是所有子 session。

这说明 task/subagent 会话不会自动走这套标题生成逻辑。

原因很合理：

- 子任务标题通常已经由 `TaskTool` 创建时根据 description 指定
- 没必要再被模型重写

---

# 23. 一个完整的“可读性增强”数据流

可以概括为：

## 23.1 标题

- 第一步 loop
- 收集首条真实用户上下文
- 调 `title` agent / small model
- 清洗文本
- `Session.setTitle()`

## 23.2 会话摘要

- step finish
- `computeDiff(fromSnapshot, toSnapshot)`
- `Session.setSummary()`
- 写 `session_diff`
- 发 `session.diff`

## 23.3 回合摘要

- 以 user message + child assistant messages 为边界
- 计算 diff
- 写入 `userMsg.summary.diffs`

这就是 OpenCode 的历史可解释性基础设施。

---

# 24. 这个模块背后的关键设计原则

## 24.1 标题应尽量只生成一次，并锁定在会话最早真实语义上

所以只在首条真实用户消息时触发。

## 24.2 摘要应基于执行边界，而不是当前工作树现状

所以依赖 step snapshot diff。

## 24.3 语义摘要与结构化变更摘要必须分离

标题用 LLM，diff summary 用 snapshots。

## 24.4 子任务/系统 synthetic 消息不应污染用户可读性元信息

所以需要排除 synthetic 与 parent session。

---

# 25. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/session/prompt.ts` 中 `ensureTitle()`
2. `packages/opencode/src/session/summary.ts`
3. `packages/opencode/src/agent/agent.ts`
4. `packages/opencode/src/session/message-v2.ts`
5. `packages/opencode/src/snapshot/index.ts`

重点盯住这些函数/概念：

- `ensureTitle()`
- `Session.setTitle()`
- `SessionSummary.summarize()`
- `summarizeSession()`
- `summarizeMessage()`
- `computeDiff()`
- `Snapshot.diffFull()`

---

# 26. 下一步还需要深挖的问题

这一篇已经把标题生成与历史摘要主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`Session.setTitle()` / `Session.setSummary()` 的完整 DB 更新与事件广播实现还值得继续读 `session/index.ts`
- **问题 2**：隐藏 `summary` agent 的具体使用场景还值得继续 grep，以区分它与 `SessionSummary` 的关系
- **问题 3**：title agent prompt 文件 `prompt/title.txt` 的内容与标题质量策略还值得单独分析
- **问题 4**：子任务标题目前依赖 `TaskTool` description，是否总能足够准确，还值得继续评估
- **问题 5**：若首条真实用户消息很短但前面有很多 shell/subtask 历史，标题质量是否会被上下文噪音影响，还值得继续验证
- **问题 6**：`computeDiff()` 只取 earliest start/latest finish，在多分支回滚/复杂子任务场景下是否会过于粗糙，还值得继续思考
- **问题 7**：message-level summary 目前只保留 diffs，不保留自然语言摘要，这是否足够，还值得从产品角度评估
- **问题 8**：diff path unquote 是否已覆盖所有特殊路径编码情况，还值得继续检查更多 git diff 输入样本

---

# 27. 小结

`session_title_generation_and_history_summarization` 模块定义了 OpenCode 如何让一段复杂会话历史对人类更可读：

- `ensureTitle()` 使用专门的 title agent/小模型，为根会话生成一次稳定标题
- `SessionSummary.summarize()` 持续基于 snapshot 边界计算 session 级与 message 级 diff 摘要
- `computeDiff()` 用 step snapshot 语义而非当前文件系统现状，保证摘要与执行边界一致

因此，这一层不是简单的装饰性元数据，而是 OpenCode 让长会话、复杂工具执行与代码变更结果可被快速理解的关键基础设施。
