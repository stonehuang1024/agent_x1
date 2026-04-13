# Code Retrieval / Context Injection 模块详细解读

---

# 1. 模块定位

这一篇专门解释 OpenCode 如何回答这样的问题：

- 代码是怎么被找到的
- 关键片段是怎么被定位的
- 为什么不是一上来把整个仓库塞进 prompt
- 文件引用、工具读取、instruction、skill、历史消息是如何共同组成最终上下文的

核心源码包括：

- `packages/opencode/src/tool/read.ts`
- `packages/opencode/src/tool/glob.ts`
- `packages/opencode/src/tool/grep.ts`
- `packages/opencode/src/tool/codesearch.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/instruction.ts`
- `packages/opencode/src/session/message-v2.ts`
- `sdks/vscode/src/extension.ts`

这个模块是 OpenCode context engineering 的核心落点。

OpenCode 并不依赖单一“向量数据库索引器”来解决所有检索问题，而是采用了一套**混合式代码检索与上下文注入体系**。

---

# 2. 总体设计：不是单一索引，而是多源协同

## 2.1 OpenCode 的检索来源

从源码看，OpenCode 至少同时使用了以下几类上下文来源：

- **用户显式输入**
  - 文本
  - 文件 part
  - `@path` / `@path#Lx-Ly`
  - agent 引用
- **本地仓库检索工具**
  - `glob`
  - `grep`
  - `read`
  - 可选 `lsp`
- **外部代码文档检索**
  - `codesearch`
- **项目/目录规则注入**
  - `InstructionPrompt.system()`
  - `InstructionPrompt.resolve()`
- **技能说明按需加载**
  - `SystemPrompt.skills()`
  - `SkillTool`
- **会话历史回放**
  - `MessageV2.toModelMessages()`
- **上下文压缩摘要**
  - `filterCompacted()`
  - `SessionCompaction`

因此 OpenCode 的上下文策略不是“统一向量检索”，而是：

- **静态规则注入**
- **动态工具检索**
- **用户显式定位**
- **历史上下文回放**
- **按需懒加载**

共同组成的多源上下文系统。

## 2.2 为什么不直接塞整个仓库

原因很清楚：

- token window 有限
- 噪音会显著干扰模型决策
- 大仓库里大多数文件与当前任务无关
- 工具调用允许渐进式聚焦

所以 OpenCode 的策略是：

- 先缩小范围
- 再精准读取
- 再把真正相关片段回流到上下文

这是一种典型的 **progressive narrowing** 检索策略。

---

# 3. 用户输入如何成为上下文起点

## 3.1 `PromptInput` 不是单纯字符串

从 `session/prompt.ts` 可以看到，用户输入最终会被结构化成多种 part，而不是只保留为纯文本。

这意味着上下文系统一开始就是结构化的。

用户可以通过：

- 文本
- 文件
- agent
- subtask
- format
- system

来影响最终 prompt。

## 3.2 `resolvePromptParts()` 的作用

`SessionPrompt.resolvePromptParts(template)` 的职责是：

- 扫描 prompt 文本
- 识别文件引用、agent 引用等占位结构
- 转成 `PromptInput["parts"]`

也就是说，OpenCode 在 prompt 解析阶段就已经开始“结构化上下文抽取”，而不是直到模型调用前才做字符串处理。

## 3.3 为什么 `@path#Lx-Ly` 很关键

VS Code 插件中，当前文件或选区会被转换成：

- `@relative/path`
- `@relative/path#L12-L40`

这说明用户并不总是直接上传代码正文，而是先提供“定位引用”。

这样做的好处是：

- prompt 更短
- 定位更精确
- runtime 可以延后读取真实内容
- 更容易插入局部 instruction

这是一种 **reference-first context injection** 设计。

---

# 4. 本地检索工具的职责分层

OpenCode 的本地检索不是一个工具做完所有事，而是多个工具分工。

## 4.1 `glob`：按路径模式找候选文件

`GlobTool` 的职责是：

- 根据 glob pattern 找文件
- 可指定搜索目录
- 返回路径列表
- 按修改时间排序
- 结果最多 100 条

### 算法特点

- 使用 `Ripgrep.files(...)`
- 限制结果集大小
- 按 `mtime` 降序排序

这意味着 `glob` 不是简单文件系统递归，而是更偏向“快速列候选文件”的第一阶段过滤器。

## 4.2 `grep`：按内容找命中位置

`GrepTool` 的职责是：

- 用正则在文件内容中找匹配
- 支持 path 限定
- 支持 include pattern
- 输出文件 + 行号 + 内容摘要

### 算法特点

- 直接调用 ripgrep
- 解析 `-nH --field-match-separator=|` 输出
- 获取每个文件的 mtime
- 结果按文件修改时间排序
- 最多显示 100 条命中

这里的重点不是全文检索的复杂语义，而是：

- **快速定位“哪里可能相关”**

因此 `grep` 是 OpenCode 代码定位链路里的第二阶段过滤器。

## 4.3 `read`：精确提取内容

`ReadTool` 是真正把代码片段送入上下文的关键工具。

它支持：

- 读文件
- 读目录
- 指定 `offset`
- 指定 `limit`
- 自动处理文本/图片/PDF
- 二进制检测
- 附加路径局部 instruction

在实际工作流里，模型往往是：

1. `glob`
2. `grep`
3. `read`

也就是说：

- `glob` 找范围
- `grep` 找行位点
- `read` 才拿真正正文

这是一个很标准的 **candidate -> match -> extract** 三段式算法。

---

# 5. `ReadTool` 的实现逻辑与关键算法

## 5.1 绝对路径与工作区边界

`read` 工具会先：

- 若路径不是绝对路径，则相对 `Instance.directory` 解析
- 调用 `assertExternalDirectory(...)`

这表示读文件不是无限制的，runtime 会检查是否越出允许边界。

## 5.2 权限审批

读取前会调用：

- `ctx.ask({ permission: "read", patterns: [filepath], ... })`

因此即使是“读文件”这种基础操作，也在统一权限系统下。

## 5.3 文件不存在时的 suggestions

如果文件不存在：

- 会在同目录下找近似名字建议
- 最多给 3 个 suggestions

这是一种 **local typo recovery** 设计。

## 5.4 目录读取

若路径是目录：

- 列出 dirents
- 目录末尾加 `/`
- 支持 offset/limit 分页
- 最终以 `<entries>` 块输出

这意味着 `read` 不仅是文件读取器，也是目录浏览器。

## 5.5 图片 / PDF 特殊处理

对于：

- 图片（排除 SVG 等）
- PDF

`read` 不会尝试按文本行输出，而是：

- 返回一条成功提示
- 以 `attachments` 形式附带 base64 data URL

这样后续 `MessageV2.toModelMessages()` 再决定如何把附件送给不同 provider。

说明检索系统不仅检文本，也能把富媒体变成上下文资产。

## 5.6 二进制检测算法

`isBinaryFile()` 使用两类策略：

### 扩展名黑名单

对常见二进制扩展名直接判定为 binary：

- `.zip`
- `.exe`
- `.class`
- `.jar`
- `.wasm`
- `.pyc`
- 等

### 内容抽样判定

若扩展名不确定：

- 读取前 4096 字节
- 若出现 `0x00`，直接判 binary
- 统计不可打印字符比例
- 若超过 30%，判 binary

这是一种轻量但实用的 **binary sniffing heuristic**。

## 5.7 行级文本提取算法

对于普通文本文件：

- 默认最多读 2000 行
- 每行最多 2000 字符
- 总输出最多 50KB
- 支持 offset
- 逐行读取，避免一次性加载整个文件

这是一个典型的 **streaming bounded file read** 设计。

它同时控制了：

- 行数预算
- 单行预算
- 总字节预算

非常适合作为 agent runtime 的文件读取器。

## 5.8 为什么 `read` 会注入 instruction

这是 `ReadTool` 最重要的隐藏能力之一。

它会调用：

- `InstructionPrompt.resolve(ctx.messages, filepath, ctx.messageID)`

如果命中路径相关 instruction，就在输出末尾附加：

```xml
<system-reminder>
...
</system-reminder>
```

这说明 `read` 的输出不仅是文件正文，还可能包含“与这个文件相关的局部规则”。

这是 OpenCode 上下文系统中一个非常强的设计：

- **规则随文件读取自动进入局部上下文**

---

# 6. 外部代码检索：`CodeSearchTool`

## 6.1 它解决什么问题

本地仓库搜索只能覆盖当前代码库。

但很多问题还需要：

- SDK 文档
- 框架 API
- 外部库样例
- 官方实现说明

`CodeSearchTool` 就是 OpenCode 的外部代码上下文入口。

## 6.2 实现方式

它会构造一个 JSON-RPC 请求：

- `method: tools/call`
- `name: get_code_context_exa`
- 参数：
  - `query`
  - `tokensNum`

发到：

- `https://mcp.exa.ai/mcp`

然后解析 SSE 返回中的：

- `data: ...`
- `result.content[0].text`

## 6.3 算法特点

- 外部超时：30 秒
- SSE 文本解析
- 返回首个内容块
- 若无结果，给出建议性兜底文本

这里并没有复杂 rerank，而是把外部服务视作“上游检索器”，OpenCode 负责把结果纳入 tool runtime。

因此 `codesearch` 的作用更像：

- **远程代码文档语义检索代理**

而不是本地索引器。

---

# 7. instruction 系统如何参与上下文注入

## 7.1 `InstructionPrompt.system()`：系统级注入

系统级 instruction 来源包括：

- 项目路径向上找到的 `AGENTS.md` / `CLAUDE.md` / `CONTEXT.md`
- 全局 config 目录中的 instruction 文件
- `~/.claude/CLAUDE.md`
- config 中声明的本地 instruction 路径
- config 中声明的 instruction URL

这些内容会作为 system prompt 的一部分进入模型。

## 7.2 `InstructionPrompt.resolve()`：文件局部注入

当某个文件被读取时，系统会从该文件所在目录一路向上查找：

- `AGENTS.md`
- `CLAUDE.md`
- `CONTEXT.md`

并过滤掉：

- 已经在 system 中注入过的 instruction
- 已被历史 read 加载过的 instruction
- 当前 message 已 claim 过的 instruction

这是一种很精细的 **hierarchical local instruction injection**。

它避免了两个问题：

- 重复注入相同规则
- 读取文件时遗漏与该路径强相关的局部约束

## 7.3 `claim` 机制的意义

`InstructionPrompt` 内部维护了：

- `claims: Map<messageID, Set<filepath>>`

这表示：

- 同一轮 message 中，同一条 instruction 只注入一次

这是一种局部去重策略，避免模型在单轮上下文中反复看到同一规则。

---

# 8. 历史消息如何进入上下文

## 8.1 `MessageV2.toModelMessages()` 是最终入口

所有历史都要通过：

- `MessageV2.toModelMessages(msgs, model)`

进入模型。

因此“检索到什么”和“真正进入 context 什么”不是完全同一件事。

## 8.2 用户消息

用户消息中的这些 part 会进入上下文：

- `text`
- `file`
- `compaction`
- `subtask`

其中：

- `compaction` 会变成 `What did we do so far?`
- `subtask` 会变成“用户执行了某个工具”的提示

## 8.3 assistant 消息

assistant 历史中的：

- `text`
- `reasoning`
- `tool result`
- `tool error`

也会被回放进下一轮。

这意味着上下文并不只由“读出来的源码”构成，还包括：

- 之前的发现
- 之前的推理
- 之前的工具结果
- 之前的失败

因此 OpenCode 的上下文系统是“**检索结果 + 会话执行历史**”共同组成的。

---

# 9. `filterCompacted()` 与上下文窗口控制

虽然这篇不主讲 compaction，但上下文注入离不开它。

`MessageV2.filterCompacted()` 的作用是：

- 当历史已经有 compaction summary 后
- 不再继续把 summary 之前的全部细节回放给模型

这保证了上下文不会无限膨胀。

所以 OpenCode 的上下文选择可以理解成：

- 选取“还活着的历史”
- 而不是“全部历史”

---

# 10. `insertReminders()` 与中途用户消息增强

从 `prompt.ts` 的控制流可见，OpenCode 在正式发模型请求前会执行：

- `insertReminders(...)`

此外，对上次已完成 assistant 之后的新用户消息，还会临时包裹成：

```xml
<system-reminder>
The user sent the following message:
...
Please address this message and continue with your tasks.
</system-reminder>
```

这说明上下文系统不仅决定“放什么进去”，还决定“**以什么权重和语气放进去**”。

这是一种 **context prioritization by wrapper** 技术。

---

# 11. skill 是如何作为懒加载上下文进入的

## 11.1 先注入 skill 索引

`SystemPrompt.skills()` 会在 system 中注入：

- skill 是什么
- 当前有哪些可用 skill
- 什么时候应该调用 `skill` tool

## 11.2 再按需加载 skill 内容

真正的 skill 正文只有在模型调用 `SkillTool` 后才进入上下文。

`SkillTool.execute()` 返回：

- `<skill_content name="...">`
- skill 内容
- base dir
- sampled file list

这是一种标准的 **lazy context expansion**。

也就是说 skill 不是一开始全塞进 prompt，而是先给目录，再按需打开具体说明书。

---

# 12. VS Code 文件引用如何接入这一体系

## 12.1 插件只做定位，不做重解释

VS Code 插件里：

- `getActiveFile()` 会把当前文件或选区变成 `@path#Lx-Ly`
- 再通过 `/tui/append-prompt` 发给正在运行的 opencode

插件并不直接读取源码正文塞进 prompt。

## 12.2 这意味着什么

这意味着 OpenCode 把 IDE 入口和 context runtime 清晰分开：

- IDE 负责采集“用户当前关注点”
- runtime 负责解析引用、读取真实内容、注入局部 instruction、回流上下文

这是很合理的职责边界。

---

# 13. 上下文选择背后的几个关键算法思想

## 13.1 渐进式收缩

不是先全量拿仓库，而是：

- `glob` 缩范围
- `grep` 找命中
- `read` 精提取

这是 **progressive narrowing**。

## 13.2 预算受控

无论是 read 还是 grep，都有：

- 行数上限
- 字节上限
- 结果条数上限

这是 **bounded retrieval**。

## 13.3 路径感知规则注入

文件一旦被读取，其所在路径相关 instruction 也会一起注入。

这是 **path-sensitive context enrichment**。

## 13.4 懒加载

skill 与外部搜索都不是预先全注入，而是在需要时才加载。

这是 **lazy expansion**。

## 13.5 历史回放与压缩共存

上下文不是只由当前检索结果构成，还要叠加：

- 历史消息
- 工具结果
- 历史推理
- 已压缩 summary

这是 **retrieval + memory hybrid context**。

## 13.6 provider-aware 表达

同样的文件、图片、tool result，在不同 provider 下可能被编码成不同消息形式。

这是 **representation-aware context injection**。

---

# 14. 这个模块背后的核心设计原则

## 14.1 不追求“一次全知道”

OpenCode 假设模型不该一次拥有整个代码库，而应通过工具交互逐步定位答案。

## 14.2 检索不是独立子系统，而是 runtime 行为的一部分

检索工具、instruction、history、skill、summary 都与 loop 紧密耦合，不是孤立搜索引擎。

## 14.3 上下文质量比上下文数量更重要

通过路径引用、分页、截断、去重、局部规则注入，系统强调的是：

- 相关性
- 紧凑性
- 可解释性

## 14.4 规则要贴着文件走

只在 system prompt 注入全局规则还不够。

OpenCode 的设计是：

- 当你真正读一个文件时，再把该路径附近的局部规则补进来

这非常适合大型仓库。

---

# 15. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/tool/glob.ts`
2. `packages/opencode/src/tool/grep.ts`
3. `packages/opencode/src/tool/read.ts`
4. `packages/opencode/src/tool/codesearch.ts`
5. `packages/opencode/src/session/instruction.ts`
6. `packages/opencode/src/session/prompt.ts`
7. `packages/opencode/src/session/message-v2.ts`
8. `sdks/vscode/src/extension.ts`

建议重点盯住这些函数/概念：

- `GlobTool.execute()`
- `GrepTool.execute()`
- `ReadTool.execute()`
- `isBinaryFile()`
- `InstructionPrompt.systemPaths()`
- `InstructionPrompt.resolve()`
- `SessionPrompt.resolvePromptParts()`
- `insertReminders()`
- `MessageV2.toModelMessages()`

---

# 16. 下一步还需要深挖的问题

这个模块已经把主框架讲清楚了，但还有几个值得继续单独深挖的问题：

- **问题 1**：`resolvePromptParts()` 对 `@path#Lx-Ly`、agent mention、占位符替换的完整解析算法细节是什么
- **问题 2**：如果路径引用命中 symbol 或 resource，`FilePart.source` 的构造过程和回显逻辑是什么
- **问题 3**：LSP 工具在代码定位中的真实能力边界是什么，是否可用于 definition/reference/symbol 级导航
- **问题 4**：`read` 注入的 `<system-reminder>` 与 loop 层追加的 `<system-reminder>` 在模型行为上会如何叠加
- **问题 5**：`codesearch` 的上游结果质量如何影响 agent 决策，是否存在 rerank 或后处理空间
- **问题 6**：`FileTime.read()` 与 `LSP.touchFile()` 在上下文系统中的隐含作用是什么
- **问题 7**：多文件、大文件、媒体文件同时存在时，`MessageV2.toModelMessages()` 的上下文预算策略还有哪些边缘行为
- **问题 8**：VS Code 传入的路径引用在 CLI/TUI 侧具体如何被消费，是否有专门的 mention parser

---

# 17. 小结

`code_retrieval_and_context_injection` 模块定义了 OpenCode 如何从“用户当前关心哪里”走到“模型真正看到什么”：

- `glob/grep/read` 提供本地渐进式检索
- `codesearch` 提供外部代码文档检索
- `InstructionPrompt` 提供系统级与路径级规则注入
- `resolvePromptParts()` 负责把引用转换成结构化 part
- `MessageV2.toModelMessages()` 负责把这些内容变成 provider 可消费的上下文
- VS Code 则提供轻量入口，把 IDE 关注点转换成路径引用

因此，这个模块不是单纯“搜索功能”，而是 OpenCode 整体 context engineering 的关键基础设施。
