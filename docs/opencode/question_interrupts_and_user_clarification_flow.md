# Question Interrupts / User Clarification Flow 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 `Question` 体系：也就是 agent 如何在执行过程中暂停，向用户提出结构化问题，并在收到回答后继续执行。

核心问题是：

- 为什么 question 被建模成独立 runtime 服务，而不是普通文本输出
- `Question.ask()` 如何把执行流挂起到用户回复
- `question` tool 和 `plan_exit` 是如何复用同一套问题机制的
- 用户 dismiss 问题为什么会转成 `Question.RejectedError`
- question 权限与 processor 的 `blocked` 逻辑如何联动

核心源码包括：

- `packages/opencode/src/question/index.ts`
- `packages/opencode/src/tool/question.ts`
- `packages/opencode/src/tool/plan.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/agent/agent.ts`
- `packages/opencode/src/permission/next.ts`

这一层本质上是 OpenCode 的**结构化澄清提问、用户中断点与人机协商恢复基础设施**。

---

# 2. 为什么 question 不能只是模型输出一句“我有个问题”

如果只让模型用自然语言提问，会有几个问题：

- UI 很难知道这是需要用户作答的阻塞点
- 无法提供按钮式选项
- 无法把用户回答结构化回传给 agent
- 无法与 tool lifecycle / session status / permission 统一协调

因此 OpenCode 把“提问”建成正式 runtime primitive，而不是普通聊天文本。

---

# 3. `Question.Info`：单个问题的结构

每个问题包含：

- `question`
- `header`
- `options`
- `multiple?`
- `custom?`

## 3.1 含义

一个 question 不是简单字符串，而是带有：

- 标题
- 选项目录
- 是否允许多选
- 是否允许自定义输入

这让 UI 可以稳定渲染，并把用户回答转换成标准化结构。

---

# 4. `Question.Request`：一次 ask 可以包含多个问题

请求对象包含：

- `id`
- `sessionID`
- `questions: Info[]`
- `tool? { messageID, callID }`

这说明一次提问并不局限于一个问题，而是支持：

- 一次性收集多个澄清项

这对减少来回交互很有价值。

---

# 5. 为什么 request 里要带 `tool`

如果问题来自某个工具调用，那么 request 会附上：

- `messageID`
- `callID`

这使 question 不只是 session 级事件，还能精确关联到：

- 哪一次 tool call 在等待用户回答

这对 UI 高亮、恢复上下文与审计都很重要。

---

# 6. `Question.ask()`：核心就是“挂起 Promise”

`Question.ask()` 会：

1. 分配一个 `QuestionID`
2. 把 request 写入 `state.pending`
3. `Bus.publish(question.asked, info)`
4. 返回一个 Promise
5. 等待未来的 `reply()` 或 `reject()` 来 resolve/reject

## 6.1 含义

这和 `PermissionNext.ask()` 在结构上非常相似。

question 的本质也是：

- **运行时执行流挂起点**

只是它挂起的原因不是权限审批，而是信息不足需要用户澄清。

---

# 7. `Question.reply()`：如何恢复执行流

当 UI/调用方给出答案时：

- 找到 pending request
- 从 state 删除
- `Bus.publish(question.replied, ...)`
- `existing.resolve(input.answers)`

于是原先 `await Question.ask(...)` 的地方就会继续执行。

这说明 question 体系本质上是一个非常干净的 async rendezvous 机制。

---

# 8. `Question.reject()`：dismiss 为什么是异常而不是空答案

如果用户 dismiss：

- `Bus.publish(question.rejected, ...)`
- `existing.reject(new RejectedError())`

## 8.1 为什么不用空数组表示

因为“用户没有回答”和“用户明确拒绝继续这个问题”语义不同。

空答案可能表示：

- 允许继续，只是未选择

而 reject 则表示：

- 当前执行流在这个中断点被用户否决

因此必须走异常控制流。

---

# 9. `Question.RejectedError`

它的语义很直接：

- `The user dismissed this question`

这个错误随后会被上层 processor 特别识别，并可能导致整个执行 loop 停止。

这说明 dismiss 问题不只是一个 UI 动作，而是运行时控制信号。

---

# 10. `QuestionTool`：agent 暴露给模型的澄清入口

`tool/question.ts` 里定义了标准 `question` tool。

参数是：

- `questions: z.array(Question.Info.omit({ custom: true }))`

执行时：

- `await Question.ask({ sessionID, questions, tool })`
- 然后把用户答案格式化为 tool output 文本

## 10.1 为什么 `custom` 被 omit

这意味着模型调用 `question` tool 时，不能直接控制 `custom` 字段，避免它随意改变 UI 交互模式。

也就是说：

- question tool 暴露的是受控子集

这是很合理的安全/产品约束。

---

# 11. `QuestionTool` 的返回值为什么还要转成自然语言 output

它会输出类似：

- `User has answered your questions: ... You can now continue with the user's answers in mind.`

同时 metadata 中保留：

- `answers`

## 11.1 含义

这样设计兼顾两层消费者：

- 模型可以直接读自然语言结果继续推理
- 系统/UI 仍有结构化 `answers`

这是一种典型的“面向模型文本 + 面向系统结构化元数据”双通道输出设计。

---

# 12. `plan_exit` 为什么复用 Question

`tool/plan.ts` 中 `PlanExitTool.execute()` 会调用：

- `Question.ask({ questions: [...] })`

然后读取：

- `answers[0]?.[0]`

若回答是 `No`：

- `throw new Question.RejectedError()`

## 12.1 意义

这说明 Question 不只是一个通用澄清工具，也被用作：

- 工作流确认对话框基础设施

比如是否从 plan 模式切换到 build 模式。

---

# 13. question 与 permission 的边界

question 本身不是 permission ask。

但 agent 是否有资格提出问题，是由 permission 控制的。

从 `agent.ts` 可见默认规则中：

- `question = deny`

而：

- `build` / `plan` 等某些 agent 会显式 `question = allow`

## 13.1 含义

“向用户提问”被视为一种正式能力，不是所有 agent 天然都能做。

这非常有意思，也很合理：

- 有些 agent应专注执行，不要频繁打扰用户
- 只有主协调型 agent 才应主动澄清

---

# 14. CLI / automation 场景为什么常禁用 `question`

grep 可见某些 CLI 路径会显式加入：

- `permission: "question", action: "deny", pattern: "*"`

例如：

- `cli/cmd/run.ts`
- `cli/cmd/github.ts`

## 14.1 含义

在非交互式或半自动批处理场景中，question 会破坏流程连续性，因此系统选择在入口层硬禁用。

这体现了交互式 runtime 和自动化 runtime 的明确区分。

---

# 15. question 是如何阻断 processor 的

在 `processor.ts` 中，若 tool-error 里的错误是：

- `Question.RejectedError`

就会：

- `blocked = shouldBreak`

最终 processor 可能返回：

- `stop`

## 15.1 含义

这说明 question 并不只是拿答案的辅助工具。

它还能成为：

- 用户明确停止当前执行链路的中断点

---

# 16. 为什么用户 dismiss question 要和权限拒绝并列处理

processor 中将：

- `PermissionNext.RejectedError`
- `Question.RejectedError`

并列处理。

这是因为两者都代表：

- 执行继续所需的人类决策没有拿到批准

区别只是：

- 前者是能力授权没拿到
- 后者是信息澄清/工作流确认没拿到

从 runtime 控制角度，它们都应导致阻断。

---

# 17. question 事件总线

`Question.Event` 包括：

- `question.asked`
- `question.replied`
- `question.rejected`

这说明 question 是一等 runtime entity，有完整事件生命周期，而不是隐藏在 tool 调用里的局部逻辑。

这对：

- UI 实时展示
- 远程客户端同步
- 审计与记录

都很关键。

---

# 18. `Question.list()`：为什么要能列出 pending questions

它会返回所有 pending request。

这说明系统预期：

- 某时刻可能有未回答的问题挂起
- UI/服务端需要查询这些问题并展示给用户

这再次证明 question 是持久 runtime 状态，而不是瞬时回调。

---

# 19. question tool 在工作流 prompt 中的重要性

在 plan workflow 的系统提示里，明确要求：

- 先 explore
- 再用 question tool 澄清歧义
- 最后 plan_exit

## 19.1 含义

OpenCode 并不把“向用户澄清”当作失败，而是把它制度化进某些 agent workflow。

换句话说：

- 适时提问是主规划 agent 的一部分能力

而不是例外情况。

---

# 20. 为什么 answers 是 `string[][]`

`Question.Answer = string[]`

而 `reply.answers` 是：

- `Answer[]`

即：

- 每个问题对应一个答案数组
- 多个问题形成二维数组

## 20.1 含义

这种设计统一支持：

- 单选 -> 长度 1 的数组
- 多选 -> 多 label 数组
- 多问题批量提问 -> 二维结构

非常整洁，没有为单选/多选单独建不同返回类型。

---

# 21. 一个完整的 question interrupt 数据流

可以概括为：

## 21.1 agent 调用 `question` tool

- 或 workflow/tool 内部直接 `Question.ask()`

## 21.2 runtime 挂起

- pending request 存入 state
- 发布 `question.asked`
- Promise 挂起

## 21.3 用户响应

- `reply()` -> resolve answers
- 或 `reject()` -> 抛 `Question.RejectedError`

## 21.4 tool 恢复

- `QuestionTool` 把答案转成 tool result
- 模型继续执行

## 21.5 若被拒绝

- processor 识别为 blocked
- 当前 loop 返回 `stop`

这就是 OpenCode 的用户澄清闭环。

---

# 22. 为什么 question 与普通聊天输入不冲突

普通用户输入会进入：

- user message
- prompt loop

而 question answer 是：

- 直接 resolve 某个 pending runtime request

两者走的是两条完全不同的通道：

- 一条是会话消息
- 一条是运行时中断恢复信号

这避免了“模型自己读不懂哪句是对哪个问题的回答”的歧义。

---

# 23. 这个模块背后的关键设计原则

## 23.1 澄清提问必须被建模成正式 runtime interrupt

所以有 `Question.ask()/reply()/reject()`，而不是自由文本约定。

## 23.2 结构化问题与结构化回答比自然语言来回更可控

所以 question 有 options/multiple/custom 等显式 schema。

## 23.3 是否允许提问本身也是一种权限能力

所以 `question` 受 agent permission 控制。

## 23.4 dismiss 问题必须能中断执行流

所以 `Question.RejectedError` 被 processor 视为 blocked stop 条件。

---

# 24. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/question/index.ts`
2. `packages/opencode/src/tool/question.ts`
3. `packages/opencode/src/tool/plan.ts`
4. `packages/opencode/src/session/processor.ts`
5. `packages/opencode/src/agent/agent.ts`

重点盯住这些函数/概念：

- `Question.ask()`
- `Question.reply()`
- `Question.reject()`
- `Question.RejectedError`
- `QuestionTool.execute()`
- `PlanExitTool.execute()`
- `permission = "question"`
- `blocked`

---

# 25. 下一步还需要深挖的问题

这一篇已经把 question 与用户澄清主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：question 请求在服务端/API/UI 中如何被展示和回复，还值得继续查看 server routes 与前端实现
- **问题 2**：`custom` 字段在直接 `Question.ask()` 场景中如何被使用，还值得继续找调用点
- **问题 3**：批量多问题回答的 UX 是否足够清晰，还值得结合前端交互继续观察
- **问题 4**：在 CLI/自动化入口禁用 `question` 后，相关 workflow 如何自动降级或失败，还值得继续查看调用方行为
- **问题 5**：Question 与 PermissionNext 在抽象层面非常相似，未来是否会统一成更通用的 interrupt framework，值得继续思考
- **问题 6**：question answer 当前主要靠 labels 回传，自定义文本答案的编码方式与约束还值得继续确认
- **问题 7**：Question 被 reject 后是否应留下更显式的 assistant/tool summary，当前可能还可改进
- **问题 8**：workflow 中 question 的使用策略是否会导致过度打扰用户，还值得继续从产品角度审视

---

# 26. 小结

`question_interrupts_and_user_clarification_flow` 模块定义了 OpenCode 如何在 agent 执行过程中把“向用户澄清”变成正式、可恢复、可中断的运行时能力：

- `Question` 命名空间提供 ask/reply/reject 的挂起式交互协议
- `QuestionTool` 让模型能以结构化方式主动提问
- `plan_exit` 等工作流工具也复用同一套用户确认机制
- `Question.RejectedError` 则把用户 dismiss 直接反馈进 processor 的停止控制流

因此，这一层不是简单的问答 UI，而是 OpenCode 把用户澄清、人类决策与代理执行流安全衔接起来的关键中断基础设施。
