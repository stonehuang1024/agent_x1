# Instruction Prompt / Attachment Ingestion 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 instruction prompt 注入与附件摄取链路。

核心问题是：

- `AGENTS.md` / `CLAUDE.md` / 自定义 instruction 文件如何被发现并注入 system prompt
- 为什么 instruction 系统要有 `claims`、`loaded()`、`resolve()` 这类去重机制
- `createUserMessage()` 如何摄取 file、directory、data URL、MCP resource、agent part
- 为什么很多文件附件会先被转换成 synthetic text，再保留原附件
- `ReadTool` 为什么会在消息摄取阶段被内部调用

核心源码包括：

- `packages/opencode/src/session/instruction.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/tool/read.ts`
- `packages/opencode/src/session/message-v2.ts`

这一层本质上是 OpenCode 的**静态指令注入与输入附件语义化摄取基础设施**。

---

# 2. 为什么 instruction prompt 与附件摄取要放在 session 入口层处理

这两类能力有一个共同点：

- 它们都发生在“用户输入进入会话历史之前”

无论是：

- 自动注入项目 instruction
- 读取用户附带的文件/目录/MCP resource

本质上都是在把原始输入转成更适合后续模型消费的上下文。

所以它们放在：

- `createUserMessage()`
- `InstructionPrompt.*`

这条入口链路上是非常合理的。

---

# 3. Instruction 文件的默认来源

`instruction.ts` 定义了默认文件名：

- `AGENTS.md`
- `CLAUDE.md`
- `CONTEXT.md`（deprecated）

并区分两类来源：

## 3.1 project / relative 来源

从当前项目目录向上查找。

## 3.2 global 来源

- `OPENCODE_CONFIG_DIR/AGENTS.md`
- `Global.Path.config/AGENTS.md`
- `~/.claude/CLAUDE.md`（若未禁用）

这说明 instruction 系统天然是多层覆盖结构，而不是单文件配置。

---

# 4. `systemPaths()`：system instruction 文件收集器

这是 instruction 系统的关键入口之一。

它会收集：

- 项目根向上第一个命中的默认 instruction 文件族
- 全局 instruction 文件
- `config.instructions` 中声明的文件路径/glob

## 4.1 project config 可禁用

若：

- `OPENCODE_DISABLE_PROJECT_CONFIG`

则不再从项目树向上找，而改从：

- `OPENCODE_CONFIG_DIR`

解析相对 instruction。

这说明系统明确支持“完全脱离项目内 config 文件”的运行模式。

---

# 5. `config.instructions`：自定义指令来源不只支持本地文件

`systemPaths()` / `system()` 对 `config.instructions` 的处理非常灵活：

- 相对路径
- 绝对路径
- `~/` 路径
- `http://` / `https://` URL

## 5.1 这意味着什么

instruction prompt 并不局限于仓库内 markdown，还可以来自：

- 本机其他目录
- 远程文档 URL

因此 OpenCode 的 instruction 体系本质上是“指令资产聚合器”。

---

# 6. `system()`：真正注入模型 system prompt 的内容

`InstructionPrompt.system()` 会：

1. 调 `systemPaths()` 拿到全部文件路径
2. 读取它们文本内容
3. 给每段内容加前缀：
   - `Instructions from: <path-or-url>`
4. 对 URL 还会做 5 秒 timeout fetch
5. 返回非空文本数组

## 6.1 为什么保留来源前缀

这样模型不仅看到 instruction 内容，还知道：

- 它来自哪个文件/URL

这有助于：

- 可解释性
- 多份 instruction 同时存在时的上下文辨识

---

# 7. `loaded(messages)`：为什么要从 read tool 结果里反推“已加载过的 instruction 文件”

这个函数会扫描历史消息中的：

- `tool === "read"`
- `state.status === "completed"`
- 且未 compacted

再从其 `metadata.loaded` 中提取路径集合。

## 7.1 含义

instruction 系统并不是盲目每次都往用户消息里附加项目内 AGENTS.md。

它会先判断：

- 某个 instruction 文件是否已经通过 read tool 实际加载过

如果已经加载，就没必要再重复注入。

这是一种非常重要的去重机制。

---

# 8. `claims`：为什么还要按 messageID 做 claim 去重

`InstructionPrompt` 内部 state 维护：

- `claims: Map<messageID, Set<filepath>>`

并提供：

- `isClaimed()`
- `claim()`
- `clear(messageID)`

## 8.1 作用

在同一条消息构造期间，避免重复把同一个 instruction 文件多次注入。

这和 `loaded(messages)` 不同：

- `loaded` 是跨历史去重
- `claims` 是当前 message 构造期去重

这说明 instruction 去重考虑得非常细。

---

# 9. `resolve(messages, filepath, messageID)`：局部 instruction 发现器

这是 file read/attachment 场景里最关键的函数。

它会：

1. 取 system-level instruction paths
2. 取历史中已加载的 instruction files
3. 从目标文件所在目录向上走，直到 `Instance.directory`
4. 每层目录找默认 instruction 文件
5. 排除：
   - 当前目标文件本身
   - system paths
   - already loaded
   - 当前 message 已 claim
6. 读取命中的 instruction 文件内容并返回

## 9.1 含义

当用户读某个文件时，系统会尝试自动补充“这个文件附近目录层级的局部指令”。

这正是很多 agent 系统里最难做但最有价值的能力之一。

---

# 10. 为什么 `resolve()` 的根边界是 `Instance.directory` 而不是 `worktree`

它的 upward scan 条件是：

- `while (current.startsWith(root) && current !== root)`
- `root = Instance.directory`

这意味着局部 instruction 发现只在当前工作目录树内部进行，而不是扫整个 worktree。

这是一个很克制的选择：

- 降低噪音
- 保持就近原则
- 避免跨到工作树其他子系统目录带来无关指令

---

# 11. `createUserMessage()`：附件摄取总入口

在 `prompt.ts` 中，`createUserMessage()` 会把输入 part 逐个转换成真正的 `MessageV2.Part[]`。

处理逻辑大致分为：

- `file`
- `agent`
- 其他普通 part

其中 `file` 分支最复杂，因为它要处理多种协议和 MIME 语义。

---

# 12. MCP resource 摄取：先展开内容，再保留附件引用

如果 `file part` 的 `source.type === "resource"`，系统不会直接把它当普通 URL 附件。

而是：

1. 先插一段 synthetic text：
   - `Reading MCP resource: ...`
2. 调 `MCP.readResource(clientName, uri)`
3. 对返回内容逐项处理：
   - 有 `text` -> synthetic text
   - 有 `blob` -> synthetic text 标注二进制内容
4. 最后再把原始 `file part` 本身保留下来

## 12.1 为什么这样设计

因为 MCP resource 不只是一个文件引用，它可能已经是外部系统里的结构化资源。

系统想做的是：

- 把资源可读文本尽快前置进 prompt
- 同时保留资源附件身份

---

# 13. MCP resource 失败时也会显式写入 synthetic text

如果读取失败：

- 打日志
- 生成 synthetic text：
  - `Failed to read MCP resource ...`

这说明输入摄取阶段的失败也会被写入消息历史，而不是悄悄吞掉。

这样模型和用户都能看到上下文里到底发生了什么。

---

# 14. `data:` URL 的摄取逻辑

如果 file part 的 URL 协议是：

- `data:`

并且 MIME 是 `text/plain`，系统会生成三段内容：

1. synthetic text：
   - `Called the Read tool with the following input...`
2. synthetic text：
   - `decodeDataUrl(part.url)` 得到的真实文本
3. 原始 file part

## 14.1 为什么要模拟 “Called the Read tool”

因为从模型视角看，这和显式调用 read tool 读取文件文本是类似语义。

系统通过这种 synthetic narration，把“附件内文字已被读取”的事实显式写入上下文。

---

# 15. `file:` URL：本地文件摄取是最复杂的一支

当协议是 `file:` 时，会先：

- `fileURLToPath(part.url)`
- `Filesystem.stat(filepath)`
- 若是目录，则把 MIME 改成 `application/x-directory`

接着按 MIME 分三类：

- `text/plain`
- `application/x-directory`
- 其他二进制/媒体文件

---

# 16. 文本文件摄取：内部调用 `ReadTool`

对于 `text/plain` 文件，系统不会自己手写读取逻辑，而是：

- 计算可能的 `offset/limit`
- 构造 `ReadTool` 参数
- `ReadTool.init().then(t => t.execute(args, readCtx))`

## 16.1 为什么这是对的

这样做保证“附件读取”和“用户显式 read tool”共享同一套权威行为：

- 外部目录边界
- 图片/PDF/binary 处理
- 大文件分页
- instruction resolve
- metadata.loaded

这避免了两套读取实现分叉。

---

# 17. 基于 symbol range 的局部读取

对于带 `?start=&end=` query 的 file URL，系统会尝试：

- 解析 start/end
- 若 `start === end`，再用 `LSP.documentSymbol()` 反查更完整范围
- 最终推导 `offset/limit`

## 17.1 这说明什么

文件附件并不总是整文件语义。

某些 attachment 实际表达的是：

- 某个 symbol
- 某个代码片段范围

OpenCode 会尽量把它收窄成局部 read，而不是粗暴整文件灌入上下文。

---

# 18. 内部 `ReadTool` 调用为什么设置 `bypassCwdCheck`

内部构造的 `Tool.Context.extra` 带：

- `bypassCwdCheck: true`

这说明附件摄取是受系统信任的内部调用路径，不应再因为 cwd 检查而重复阻塞。

同时它不会弹 ask，因为 `readCtx.ask = async () => {}`。

## 18.1 这并不是漏洞

因为此时附件已经是用户明确提供的输入部分，系统是在把用户显式给定的输入转成上下文。

这属于受控内部摄取，而不是 agent 自发越界读取。

---

# 19. 文本文件读取后会生成哪些 part

读取成功时，通常会生成：

1. synthetic text：
   - `Called the Read tool with the following input...`
2. synthetic text：
   - `result.output`
3. 若 read 返回 attachments，则把 attachment 转成 synthetic file parts
4. 否则保留原始 file part

## 19.1 含义

对于文本附件，系统想优先把“内容”推进 prompt，而不是只保留“附件存在”。

因此文本附件实际上会被语义化成：

- narration + extracted text + optional attachment record

---

# 20. 目录摄取：也复用 `ReadTool`

若 MIME 是：

- `application/x-directory`

系统会：

- 调 `ReadTool.execute({ filePath })`
- 生成 synthetic text narration
- 再生成 synthetic text 的目录 listing output
- 最后保留原始 directory file part

这说明目录附件不是静态引用，而是会立即展开为目录条目摘要。

---

# 21. 非文本二进制/媒体文件：直接读 bytes 并转 data URL

对于既不是文本也不是目录的本地文件，系统会：

- `FileTime.read(sessionID, filepath)`
- 生成 synthetic text：`Called the Read tool...`
- 再把文件 bytes 读出并编码成：
  - `data:<mime>;base64,...`

最终写成新的 `file part`。

这说明即便用户最初给的是 `file:` URL，系统在持久化消息里更倾向于把它标准化成 self-contained data URL 附件。

---

# 22. 为什么附件会被转成 data URL

因为这样消息历史就不再依赖外部文件路径实时存在。

好处包括：

- share 更容易同步
- 回放更稳定
- provider 投影更直接

代价是：

- 持久化体积增大

这正是该设计的 trade-off。

---

# 23. `agent` part 摄取：把用户 @agent 变成明确工具调用指令

若 part.type 是 `agent`，系统会：

1. 保留原始 agent part
2. 再追加一段 synthetic text：
   - `Use the above message and context to generate a prompt and call the task tool with subagent: <name>`

并在 task permission deny 时附加提示：

- `Invoked by user; guaranteed to exist.`

## 23.1 含义

用户对 agent 的显式选择不会只停留为 metadata，而会被翻译成模型可执行的自然语言指令。

这是一种很实用的“结构化意图 -> prompt hint”桥接方式。

---

# 24. `InstructionPrompt.clear(info.id)`：为什么 message 构造结束后要清 claim

`createUserMessage()` 用 `defer` 在退出时清理当前 message 的 instruction claims。

这说明 claims 只应在单次 message 构造期间有效。

否则会错误影响后续消息的 instruction 注入判断。

---

# 25. `Plugin.trigger("chat.message", ...)`：摄取完成后还有可扩展钩子

在所有 parts 构造完后，系统会触发：

- `chat.message`

并把：

- `message: info`
- `parts`

交给 plugin 修改。

这说明附件摄取与用户消息构造并不是封闭管道，而是保留了扩展点供插件二次处理。

---

# 26. `Session.updateMessage()` + `Session.updatePart()`：摄取结果立即落库

`createUserMessage()` 的结尾会：

- 先 `Session.updateMessage(info)`
- 再逐个 `Session.updatePart(part)`

这意味着附件摄取与 instruction 相关 synthetic part，不是临时 prompt 变量，而是正式写入会话历史。

因此后续：

- summary
- compaction
- share
- replay

都能看到这些结果。

---

# 27. `InstructionPrompt.system()` 与 `InstructionPrompt.resolve()` 的分工

可以把它们理解成两层：

## 27.1 `system()`

负责全局/项目级 instruction，进入 system prompt。

## 27.2 `resolve()`

负责某个具体文件附近的局部 instruction，通常在 read/attachment 场景中作为额外提醒注入。

这是一种非常好的“全局规则 + 局部规则”分层。

---

# 28. instruction 与 attachment ingestion 如何协同

两者在本质上其实是同一件事的两个方向：

- instruction 是“系统主动补上下文”
- attachment ingestion 是“把用户给的上下文显式展开”

最终它们都会变成：

- synthetic text
- file part
- system prompt strings

并进入同一套 MessageV2 / prompt projection 体系。

---

# 29. 这个模块背后的关键设计原则

## 29.1 输入应尽可能在入口处被语义化

所以文本文件、目录、MCP resource 都会在进入历史前被展开成可读内容。

## 29.2 局部 instruction 必须去重，避免反复污染上下文

所以有 `loaded()` 与 `claims` 双层去重。

## 29.3 系统内部附件读取应复用权威 read 逻辑

所以 `createUserMessage()` 内部直接调 `ReadTool`。

## 29.4 用户显式提供的结构化意图应被翻译成模型可执行提示

agent part -> synthetic task guidance 就是典型例子。

---

# 30. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/session/instruction.ts`
2. `packages/opencode/src/session/prompt.ts` 中 `createUserMessage()`
3. `packages/opencode/src/tool/read.ts`
4. `packages/opencode/src/session/message-v2.ts`
5. `packages/opencode/src/mcp/index.ts`

重点盯住这些函数/概念：

- `InstructionPrompt.systemPaths()`
- `InstructionPrompt.system()`
- `InstructionPrompt.loaded()`
- `InstructionPrompt.resolve()`
- `createUserMessage()`
- `decodeDataUrl()`
- `ReadTool.execute()`
- `bypassCwdCheck`

---

# 31. 下一步还需要深挖的问题

这一篇已经把 instruction 注入与附件摄取主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`ReadTool` 返回的 `metadata.loaded` 与 `InstructionPrompt.resolve()` 的完整配合路径还值得继续串起来验证
- **问题 2**：大型二进制文件直接 data URL 持久化的体积边界和存储成本还值得继续评估
- **问题 3**：MCP resource 的 blob 内容目前主要转成文本提示，是否需要更丰富的附件投影策略，还值得观察
- **问题 4**：LSP symbol range 补全在不同语言服务器下是否稳定，还需要继续验证
- **问题 5**：`chat.message` plugin hook 目前有哪些实际插件使用，还可继续 grep
- **问题 6**：instruction URL fetch 的缓存策略目前较轻，是否会重复拉取远程内容，还值得继续确认
- **问题 7**：目录附件展开时只保留 listing，而不继续深入读文件，这个深度策略是否总是合适，还值得从 UX 角度继续评估
- **问题 8**：`agent` part 到 task tool 的提示翻译是否应更结构化，而不是纯文本指令，还值得继续思考

---

# 32. 小结

`instruction_prompt_and_attachment_ingestion` 模块定义了 OpenCode 如何在用户输入刚进入系统时，就把外部文件、目录、资源与项目指令转成高价值上下文：

- `InstructionPrompt` 负责发现、去重和注入全局/局部 instruction 文件
- `createUserMessage()` 负责把 data URL、本地文件、目录、MCP resource、agent part 等输入统一摄取成结构化 message parts
- `ReadTool` 被复用为权威读取路径，避免实现分叉
- 最终所有这些内容都会正式落入消息历史，供后续 prompt、summary、share、compaction 使用

因此，这一层不是简单的附件上传处理，而是 OpenCode 将“外部输入”与“项目规则”语义化并纳入会话系统的关键入口基础设施。
