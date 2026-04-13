# OpenCode 项目详细解读（扩展版）

本文基于 `opencode` 当前代码实现进行解读，重点回答以下问题：

- **整体设计是什么**
- **代码目录如何组织，核心模块分别负责什么**
- **LLM 是如何接入与调用的**
- **agent loop 是怎样运行的**
- **prompt 如何设计，尤其是 system prompt**
- **上下文怎么管理、压缩、恢复与选择**
- **代码如何检索、如何把检索结果与用户操作放进 context**
- **skills 和 rules/permissions 是怎么做的**
- **tool call 如何注册、暴露、执行、回写**
- **LLM 输出有哪些格式、如何解析**
- **是否包含 VS Code 插件，如果包含如何实现**
- **SDK 提供了哪些能力与接口**

---

# 源码文件跳转索引

这一节是阅读地图。你可以在支持 Markdown 链接跳转的 IDE 中直接点击进入源码。

## 一、建议阅读主线

- **1. CLI 与程序入口**
  - [`packages/opencode/src/index.ts`](./packages/opencode/src/index.ts)
  - 用途：看 OpenCode 如何启动、注册命令、初始化运行环境

- **2. Agent 定义与内置模式**
  - [`packages/opencode/src/agent/agent.ts`](./packages/opencode/src/agent/agent.ts)
  - 用途：看 `build`、`plan`、`general`、`explore`、`compaction`、`summary` 等 agent 如何定义

- **3. Session 主循环入口**
  - [`packages/opencode/src/session/prompt.ts`](./packages/opencode/src/session/prompt.ts)
  - 用途：看 user message 如何创建，loop 如何驱动，工具与 system prompt 如何组装

- **4. 模型输出消费器**
  - [`packages/opencode/src/session/processor.ts`](./packages/opencode/src/session/processor.ts)
  - 用途：看 text、reasoning、tool-call、finish-step、error 如何写回消息系统

- **5. 消息与 part 数据模型**
  - [`packages/opencode/src/session/message-v2.ts`](./packages/opencode/src/session/message-v2.ts)
  - 用途：看上下文、tool result、compaction、subtask 最终如何表示

- **6. LLM 请求封装**
  - [`packages/opencode/src/session/llm.ts`](./packages/opencode/src/session/llm.ts)
  - 用途：看 system prompt、tools、provider options 如何交给 AI SDK

- **7. system prompt 与 provider prompt**
  - [`packages/opencode/src/session/system.ts`](./packages/opencode/src/session/system.ts)
  - 用途：看不同模型族的基础提示词，以及环境 prompt 如何生成

- **8. instruction / 规则文件注入**
  - [`packages/opencode/src/session/instruction.ts`](./packages/opencode/src/session/instruction.ts)
  - 用途：看 `AGENTS.md`、`CLAUDE.md` 等如何进入系统上下文

- **9. 工具注册与暴露**
  - [`packages/opencode/src/tool/registry.ts`](./packages/opencode/src/tool/registry.ts)
  - 用途：看默认工具、插件工具、自定义工具如何统一注册

- **10. skills 发现与 skill tool**
  - [`packages/opencode/src/skill/skill.ts`](./packages/opencode/src/skill/skill.ts)
  - [`packages/opencode/src/tool/skill.ts`](./packages/opencode/src/tool/skill.ts)
  - 用途：看 skill 索引如何生成、skill 内容如何按需加载

- **11. 权限与规则控制**
  - [`packages/opencode/src/permission/next.ts`](./packages/opencode/src/permission/next.ts)
  - 用途：看 `allow / deny / ask` 如何拦截工具与能力调用

- **12. provider 抽象与适配**
  - [`packages/opencode/src/provider/provider.ts`](./packages/opencode/src/provider/provider.ts)
  - [`packages/opencode/src/provider/transform.ts`](./packages/opencode/src/provider/transform.ts)
  - 用途：看多模型接入、能力描述、参数适配、schema 变换

- **13. VS Code 插件**
  - [`sdks/vscode/package.json`](./sdks/vscode/package.json)
  - [`sdks/vscode/src/extension.ts`](./sdks/vscode/src/extension.ts)
  - 用途：看 VS Code 如何拉起 opencode 终端、传递当前文件与选区

- **14. SDK 与对外接口**
  - [`packages/sdk/js/src/index.ts`](./packages/sdk/js/src/index.ts)
  - [`packages/sdk/js/src/client.ts`](./packages/sdk/js/src/client.ts)
  - [`packages/sdk/js/src/v2/index.ts`](./packages/sdk/js/src/v2/index.ts)
  - [`packages/sdk/js/src/v2/client.ts`](./packages/sdk/js/src/v2/client.ts)
  - 用途：看 OpenCode 如何把 session/runtime 能力暴露成 SDK

---

# 1. 项目整体定位

`opencode` 是一个**开源 AI coding agent runtime**。它不是一个单纯“命令行提示词脚本”，而是一套围绕以下核心抽象组织起来的系统：

- **Session**：一次持续任务会话
- **Message**：用户与助手消息
- **Part**：消息内部的细粒度结构化片段
- **Loop**：驱动每一轮 agent 执行的状态机
- **Provider**：多模型、多厂商统一接入层
- **Tool**：可调用能力
- **Permission**：可执行能力的强约束控制面
- **Skill**：按需加载的任务说明书/工作流包

OpenCode 的工程重点不是“怎么让模型回答一句话”，而是：

- 如何组织**长生命周期多轮任务**
- 如何管理**上下文、压缩、回放**
- 如何把**工具调用**纳入统一状态机
- 如何把**权限、规则、skills** 接到主循环里
- 如何把能力暴露给 CLI、UI、ACP、SDK、VS Code 等不同入口

---

# 2. 顶层架构

如果从执行路径看，主链路可以概括为：

1. 用户从 CLI / App / ACP / VS Code 等入口发起任务
2. 进入 `SessionPrompt.prompt()` 创建 user message
3. 进入 `SessionPrompt.loop()` 驱动多轮 agent 执行
4. loop 根据 session 历史挑选上下文、构造 system prompt、装配 tools
5. `LLM.stream()` 把本轮输入发给 AI SDK / provider
6. 模型返回流式事件：text / reasoning / tool-call / finish / error
7. `SessionProcessor.process()` 按事件逐条落库为 `Part`
8. 如果发生 tool call，则执行工具、写回 tool result，并进入下一轮
9. 如果上下文过长，则进入 compaction 分支
10. 最终 assistant message 完成，供 UI/SDK/协议层消费

这套结构可以看成五层：

- **入口层**：CLI、App、Desktop、ACP、VS Code、SDK
- **Runtime 层**：SessionPrompt、SessionProcessor、MessageV2、SessionCompaction
- **模型层**：Provider、ProviderTransform、LLM
- **能力层**：ToolRegistry、MCP、Skill、PermissionNext、InstructionPrompt
- **存储层**：消息、part、summary、patch、snapshot 等持久化状态

---

# 3. 代码检索、代码索引、关键片段定位、上下文选择机制

这是你本次要求重点扩充的部分，也是 OpenCode 区别于简单聊天代理的关键能力之一。

## 3.1 OpenCode 的“检索”不是一个单一模块，而是多种机制的组合

代码检索与上下文进入模型，不是靠一个巨大的“向量索引模块”完成的，而是由几种不同技术共同组成：

- **显式搜索工具**
  - `read`
  - `glob`
  - `grep`
  - `codesearch`
  - 可选 `lsp`
- **消息级文件引用与用户显式附加上下文**
- **instruction 文件自动注入**
- **skills 按需加载**
- **历史消息与工具结果重放**
- **compaction 摘要压缩**
- **插件级消息变换 hook**

所以 OpenCode 并不是“先建立完整语义索引，再统一检索”，而是一个**混合式上下文检索系统**：

- 本地工程检索：`glob` / `grep` / `read` / `lsp`
- 外部代码文档检索：`codesearch`
- 用户显式选中文件/片段：文件 part / VS Code 选区 / `@path#Lx-Ly`
- 项目规则注入：`InstructionPrompt`
- 历史任务上下文：`MessageV2.toModelMessages()`

---

## 3.2 工具层如何提供“代码检索”能力

### 1. `read` / `glob` / `grep`

这类工具是本地代码库检索的基础能力：

- `glob`：找文件
- `grep`：按内容搜索
- `read`：读取文件内容或片段

模型一般不会一次把整个代码库塞入上下文，而是会：

1. 先通过 `glob` / `grep` 缩小范围
2. 再用 `read` 读取真正相关的文件/片段
3. 再把读取结果通过工具结果回流进后续轮次上下文

这是一种**工具驱动的渐进式检索**。

### 2. `codesearch`

`packages/opencode/src/tool/codesearch.ts`

这个工具不是搜本地仓库，而是调用外部代码上下文服务。它会：

- 接受 `query`
- 接受 `tokensNum`
- 发 POST 请求到 `https://mcp.exa.ai/mcp`
- 调用远端工具 `get_code_context_exa`
- 返回检索到的代码上下文文本

它本质上是：

- **外部 API/SDK/库文档语义检索入口**
- 不是本地 repo 全量索引器

### 3. `lsp`（可选）

从 registry 可以看到 LSP 工具是实验特性，说明系统还支持通过语言服务器获取更结构化的代码导航能力。

这意味着 OpenCode 的“检索”不只是文本 grep，而是允许扩展到：

- 符号跳转
- 引用查找
- 结构化语言分析

---

## 3.3 ToolRegistry 如何决定暴露哪些检索工具

`packages/opencode/src/tool/registry.ts`

工具注册不是固定死的，而是按环境、flag、model、agent 决定：

- 默认会包含 `read`、`glob`、`grep`
- `codesearch` / `websearch` 只有在特定 provider 或 feature flag 打开时可用
- `lsp` 是实验开关
- `batch` 也是实验开关

这很重要，因为“可检索范围”和“可放入上下文的能力”本身是系统可控资源，不是无条件暴露给模型的。

---

## 3.4 系统如何“决定哪些代码、片段、上下文、用户操作放进 context prompt”

这是一个核心问题。OpenCode 的答案不是“把一切都塞给模型”，而是通过多层选择机制来决定。

### 第一层：用户输入直接决定

用户发送消息时，`SessionPrompt.prompt()` 会创建 user message，用户可以携带：

- 文本 part
- 文件 part
- agent part
- subtask part
- system 字段
- output format 字段

也就是说，**用户显式提供的文本、文件、操作意图**本来就是上下文的一部分。

### 第二层：Markdown/路径引用自动转 part

`SessionPrompt.resolvePromptParts()` 会解析 prompt 模板中的文件引用，把这些内容转成：

- `text` part
- `file` part
- `agent` part

这说明 OpenCode 支持“在 prompt 文本里引用文件/目录/agent”，并在进入 runtime 时结构化处理，而不是纯字符串拼接。

### 第三层：Session 历史筛选

loop 每轮开始时会读取：

- `MessageV2.stream(sessionID)`
- 然后通过 `MessageV2.filterCompacted()` 过滤历史

这意味着：

- 默认不是读全部历史
- 而是读“**去掉已被 compaction 替换的旧历史后**”的有效消息集

### 第四层：局部提醒注入

`SessionPrompt.loop()` 中有一个关键逻辑：

- 对于上一次已完成 assistant 之后新出现的 user 文本
- 会用 `<system-reminder>` 包裹
- 告诉模型“请处理这个新消息并继续任务”

这就是一种**上下文优先级提升机制**。

不是所有上下文权重一致，系统会对“中途插话的新用户消息”做结构化提升。

### 第五层：系统 instruction 注入

`InstructionPrompt.system()` 会把系统/项目级规则文件注入 system prompt。

而 `InstructionPrompt.resolve()` 还能根据当前读取文件路径，向上查找附近的：

- `AGENTS.md`
- `CLAUDE.md`
- `CONTEXT.md`

所以哪些上下文要进入 prompt，不只是由消息历史决定，也由：

- **当前代码路径**
- **项目规则文件**
- **局部目录规则**

共同决定。

### 第六层：skills 按需加载

skill 不会一上来全部塞进 system prompt。

系统只会先把 skill 列表索引注入，让模型知道：

- 有哪些 skill
- 什么时候该调用 skill tool

真正的 skill 内容只有在模型认为需要时，通过 `skill` tool 加载后，才进入后续上下文。

这是一种很关键的**懒加载上下文技术**。

### 第七层：Plugin 变换

在 `SessionPrompt.loop()` 中，正式发请求前会触发：

- `Plugin.trigger("experimental.chat.messages.transform", {}, { messages: msgs })`

这意味着消息最终进入模型前，还有一层插件可编程变换。

也就是说，OpenCode 允许外部插件进一步决定：

- 哪些消息被保留
- 哪些片段被重写
- 哪些内容要增强/裁剪/替换

---

## 3.5 真正送给模型的 context 是如何构成的

最终进入模型的不是“一大段 prompt 文本”，而是两类东西：

### 1. system prompt

由多层组成：

- provider prompt 或 agent prompt
- environment prompt
- skills index prompt
- instruction files
- structured output prompt（如果启用）
- 本次调用临时 system

### 2. message history

由 `MessageV2.toModelMessages(msgs, model)` 生成。

这个函数决定了哪些 part 会进入模型上下文。

---

## 3.6 `MessageV2.toModelMessages()` 是上下文选择的核心实现

`packages/opencode/src/session/message-v2.ts`

它负责把内部消息系统转成 AI SDK 所需的 `ModelMessage[]`。这是“哪些东西真正进模型”的核心关口。

### 用户消息如何进入

- `text` part -> user text
- `file` part -> user file
- `compaction` part -> 变成 `What did we do so far?`
- `subtask` part -> 变成工具已执行的提示文本

### assistant 消息如何进入

- `text` -> assistant text
- `reasoning` -> assistant reasoning
- tool completed -> tool output
- tool error -> tool error
- pending/running tool -> interrupted tool error

### 为什么要把 pending tool 变成 error 注入

因为某些 provider（尤其 Claude 风格）要求每个 tool_use 都有对应结果。

如果会话被中断、恢复，系统不能把半截未完成 tool_use 原样喂回模型，否则 provider 协议会报错。

所以 OpenCode 会主动把未完成 tool 调用规范化成：

- `output-error`
- `errorText: [Tool execution was interrupted]`

这是一种非常重要的**协议兼容修复技术**。

---

## 3.7 工具结果中的附件、媒体、上下文如何处理

`MessageV2.toModelMessages()` 还做了一件很关键的事：

- 对支持媒体 tool result 的 provider，直接保留
- 对不支持媒体 tool result 的 provider，把图片/PDF 抽出来，变成后续 user file message 再喂回模型

这说明 OpenCode 的上下文选择不仅是“选什么”，还是“**选什么表达形式**”。

同一份上下文在不同 provider 下，进入 prompt 的方式不一样。

这是 `ProviderTransform + MessageV2.toModelMessages()` 协同工作的结果。

---

## 3.8 OpenCode 在上下文选择上用了哪些关键技术

可以总结为以下几类关键技术：

- **结构化消息模型**
  - Session / Message / Part，而不是纯字符串历史

- **渐进式检索**
  - 先 `glob/grep` 缩小范围，再 `read` 精读文件片段

- **混合检索架构**
  - 本地文本搜索 + 外部代码搜索 + 可选 LSP

- **上下文懒加载**
  - skill 只注入索引，不注入全部正文

- **上下文压缩**
  - compaction summary 替换旧历史

- **局部规则注入**
  - 根据目录路径动态读取 `AGENTS.md/CLAUDE.md`

- **消息优先级增强**
  - `<system-reminder>` 包裹中途用户消息

- **插件级变换**
  - `experimental.chat.messages.transform`

- **provider 感知型上下文变换**
  - 媒体附件、tool result、schema、message 表达方式按 provider 适配

结论是：OpenCode 没有采用“单一统一向量数据库检索”那一条路，而是采用**围绕 runtime 状态机的多源混合上下文系统**。

---

# 4. 是否包含 VS Code 插件？如果包含，如何实现？

答案是：**包含**。

仓库中明确有一个 VS Code 扩展：

- [`sdks/vscode/package.json`](./sdks/vscode/package.json)
- [`sdks/vscode/src/extension.ts`](./sdks/vscode/src/extension.ts)

不过这个插件的定位不是“把整个 agent runtime 重写进 VS Code extension host”，而是：

- **在 VS Code 中作为前端入口 / 启动器 / 上下文注入器**
- 真正的 runtime 仍然是 `opencode` CLI/TUI/server

这是一种很干净的架构：

- VS Code 负责 IDE 集成
- opencode 自己负责 agent runtime

---

## 4.1 VS Code 插件提供了什么能力

从 `sdks/vscode/package.json` 可以看到，扩展注册了 3 个命令：

- `opencode.openTerminal`
- `opencode.openNewTerminal`
- `opencode.addFilepathToTerminal`

还定义了：

- 编辑器标题栏菜单入口
- 快捷键绑定
  - `cmd+escape` / `ctrl+escape`
  - `cmd+shift+escape` / `ctrl+shift+escape`
  - `cmd+alt+k` / `ctrl+alt+k`

所以它的用户体验目标是：

- 快速打开 opencode
- 快速在新终端标签中打开 opencode
- 快速把当前文件或选中行作为 `@path#Lx-Ly` 插到 opencode prompt 中

---

## 4.2 插件如何激活

扩展主入口是：

- `sdks/vscode/src/extension.ts`

`activate(context)` 中注册了命令：

- `openNewTerminal`
- `openTerminal`
- `addFilepathToTerminal`

这说明扩展的生命周期很简单：

- 激活后注册命令
- 用户执行命令时再和 opencode 交互

---

## 4.3 插件如何启动 opencode

`openTerminal()` 的实现很值得注意。

它会：

1. 生成一个随机本地端口
2. 创建一个 VS Code terminal
3. 注入环境变量：
   - `_EXTENSION_OPENCODE_PORT`
   - `OPENCODE_CALLER=vscode`
4. 在终端里执行：
   - `opencode --port <port>`
5. 轮询 `http://localhost:<port>/app`，确认 opencode 内部服务已就绪
6. 如果有当前文件，就通过 HTTP 接口把 prompt 附加进去

说明这个 VS Code 插件的关键实现方式是：

- **不是直接把消息传给模型**
- 而是**启动本地 opencode runtime**
- 然后通过本地 HTTP 接口与其通信

这是一种“IDE 外壳 + 本地 agent server”架构。

---

## 4.4 插件如何把当前文件/选区送进上下文

`getActiveFile()` 逻辑会：

1. 获取当前 active editor
2. 取工作区相对路径
3. 生成 `@relative/path` 形式引用
4. 如果用户有选区，则追加：
   - `#Lstart`
   - 或 `#Lstart-Lend`

所以最终结果像：

- `@src/session/prompt.ts`
- `@src/session/prompt.ts#L120-L180`

这很关键：它不是直接把代码全文复制进 prompt，而是把**文件定位引用**插到 prompt 中。

然后 runtime 端再基于这个引用去解析、读取和构造上下文。

这是一种非常好的设计，因为它：

- 降低 prompt 体积
- 保留代码位置语义
- 允许更晚、更精准地加载实际片段

---

## 4.5 插件如何把 prompt 发给 opencode

当 terminal 是 opencode terminal 时，`addFilepathToTerminal` 会：

- 先从 terminal env 中读出 `_EXTENSION_OPENCODE_PORT`
- 然后调用：
  - `POST http://localhost:<port>/tui/append-prompt`
- body 为：
  - `{ text }`

如果端口不存在，就退化成直接往 terminal 输入文本。

所以 VS Code 插件的 prompt 注入机制是：

- **优先走 runtime 提供的 append-prompt HTTP API**
- 否则才退回 shell 文本输入

---

## 4.6 VS Code 插件的实现特点总结

它不是一个“重量级 IDE agent”，而是一个**非常薄但非常实用的桥接层**：

- **命令层**：注册快捷入口
- **终端层**：启动/聚焦 opencode
- **上下文层**：把当前文件与选区变成 `@路径#行号` 引用
- **通信层**：通过本地 HTTP API 把 prompt 附加给运行中的 opencode

所以 VS Code 集成的核心不是在插件内部做复杂推理，而是：

- 把 IDE 上下文准确传给 runtime
- 利用 runtime 原有 loop / tool / context 系统继续完成任务

这是很合理的架构边界。

---

# 5. LLM 输出是什么格式？有哪些类型？不同 mode 下有什么不同？

OpenCode 的 LLM 输出不是“单个字符串”，而是**流式结构化事件流**。

这是理解整个系统的关键。

## 5.1 LLM 输出的第一层格式：流事件

`SessionProcessor.process()` 遍历的是：

- `stream.fullStream`

它处理的事件类型包括：

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
- `start-step`
- `finish-step`
- `text-start`
- `text-delta`
- `text-end`
- `finish`
- `error`

所以从 runtime 的角度，LLM 输出是一个**typed event stream**。

---

## 5.2 LLM 输出的第二层格式：消息 part

这些事件不会直接原样留在内存里，而是被 `SessionProcessor` 转成持久化的 `Part`：

- `text`
- `reasoning`
- `tool`
- `step-start`
- `step-finish`
- `patch`
- `snapshot`

也就是说，OpenCode 把“模型输出”提升成了**可回放、可审计、可重建的状态对象**。

---

## 5.3 文本输出是怎样的

当模型返回普通文字时，processor 会经历：

- `text-start`
- `text-delta`
- `text-end`

然后写成 `TextPart`：

- `type: text`
- `text`
- `time`
- `metadata`

文本输出不是一次性字符串，而是**增量流拼接后落库**。

---

## 5.4 reasoning 输出是怎样的

如果模型/provider 支持 reasoning，processor 会处理：

- `reasoning-start`
- `reasoning-delta`
- `reasoning-end`

然后形成 `ReasoningPart`。

这说明 OpenCode 把“思维链式输出”视为单独 part，而不是混在普通文本里。

因此 UI 或协议层可以选择：

- 展示 reasoning
- 隐藏 reasoning
- 单独统计 reasoning token
- 单独转发 reasoning metadata

---

## 5.5 tool call 输出是怎样的

当模型发起工具调用时，LLM 输出中会出现：

- `tool-input-start`
- `tool-call`
- 工具执行结束后 `tool-result` 或 `tool-error`

processor 会把它存成 `ToolPart`，包含：

- `tool`
- `callID`
- `state.status`
  - `pending`
  - `running`
  - `completed`
  - `error`
- `input`
- `output`
- `metadata`
- `attachments`
- `title`
- `time`

这说明“输出工具调用”和“输出代码文本”在系统内部是两条完全不同的类型通道。

---

## 5.6 finish / finishReason 是怎样的

在 `finish-step` 事件时，OpenCode 会记录：

- `finishReason`
- token usage
- cost
- snapshot / patch

常见 finish 状态在 loop 中被区分为：

- `tool-calls`
- `unknown`
- 其他真正结束状态，如 `stop`

loop 的判断逻辑是：

- 如果 assistant finish 不是 `tool-calls` / `unknown`
- 且最后一个 user 在 assistant 之前
- 说明这一轮已经完整结束

所以 finishReason 是 loop 控制流的重要输入，而不只是统计字段。

---

## 5.7 不同 mode 下输出有什么不同

这里要注意：OpenCode 的 `mode` 本质上是 **agent mode**，不是完全不同的协议。

也就是说，`plan`、`build`、`general`、`explore`、`compaction` 等模式下，**底层输出事件格式是相同的**，差异主要体现在：

- 使用哪个 agent prompt
- 开放哪些 tools
- 权限规则是什么
- steps 限制是什么
- 模型/variant 是什么
- 是否鼓励编辑、只读、探索、压缩、标题生成等行为

### `plan` 模式

特点：

- 更偏分析/规划
- 默认编辑类工具受限或禁止
- 输出更可能是结构化分析文本、计划文本
- 工具调用倾向于 `read` / `grep` / `glob` / `question`

### `build` / 主 agent 模式

特点：

- 可以执行开发任务
- 更可能发起 `edit` / `write` / `bash` / `apply_patch`
- 输出既可能是文本说明，也可能先工具调用再给结论

### `explore` 模式

特点：

- 主要面向搜索和调研
- 输出通常偏“发现结果总结”
- 工具调用更偏搜索、读取、检索，而不是修改

### `compaction` / `summary` / `title` 模式

特点：

- 往往是内部 agent
- 输出目标单一且契约明确
- 例如生成摘要、标题、压缩上下文
- 更少依赖复杂工具链，更多是受约束文本输出

所以“不同 mode 输出有什么不同”的准确答案是：

- **协议层输出类型相同**
- **行为层输出风格、工具使用倾向、结束条件、权限边界不同**

---

## 5.8 输出代码和输出工具调用有什么不同

### 输出代码

如果模型直接输出代码，本质上仍然是：

- `text-start`
- `text-delta`
- `text-end`

系统并不会把“这段文本刚好是代码”单独建模成另一种内部 part 类型。

也就是说，“代码回答”在协议层仍是 **text part**。

### 输出工具调用

如果模型决定调用工具，则会变成：

- tool event
- tool state machine
- tool result part

这是完全不同的分支。

所以 OpenCode 在协议层明确区分两种行为：

- **说（text）**
- **做（tool call）**

这是 agent runtime 设计的核心。

---

## 5.9 结构化输出模式

如果用户要求 `json_schema` 输出，loop 会：

- 动态注入 `StructuredOutput` tool
- 在 system prompt 中强制要求必须用该 tool
- 把 `toolChoice` 设为 `required`

这时模型最终不能靠普通 text 结束，而必须：

- 调用 `StructuredOutput`
- 输入 JSON 数据
- 被 tool schema 校验
- 校验通过后保存到 `processor.message.structured`

所以结构化输出模式下，最终输出不再是普通文本，而是：

- **工具调用形式的最终结果**

这是一个很重要的设计，因为它避免了“让模型自觉输出合法 JSON”这种不稳定方案。

---

# 6. LLM 输出后如何解析？如何触发工具调用？是否支持并行工具调用？

这是本次你要求详细补充的第四个大问题。

## 6.1 解析输出的第一站：`SessionProcessor.process()`

所有模型输出最终都先进入：

- `packages/opencode/src/session/processor.ts`

这里做的不是“正则解析文本”，而是：

- 消费 AI SDK 返回的 typed stream events
- 按事件类型构造/更新 message parts
- 更新 assistant message 的 finish、cost、tokens、error
- 决定 loop 返回 `continue` / `stop` / `compact`

所以它是一个**事件驱动解析器**，不是文本后处理器。

---

## 6.2 tool call 是如何被接住的

当流里出现 `tool-input-start` 时：

- processor 创建一个 `ToolPart`
- 状态设为 `pending`
- 记录 `toolName`、`callID`

当出现 `tool-call` 时：

- 状态更新为 `running`
- 写入 `input`
- 记录 `providerMetadata`
- 检查是否形成 doom loop

当出现 `tool-result` 时：

- 状态更新为 `completed`
- 写入 `output`、`attachments`、`metadata`、`title`

当出现 `tool-error` 时：

- 状态更新为 `error`
- 写入错误文本
- 如果是权限拒绝，还可能阻断 loop

这套机制很完整，说明工具调用是**第一等输出类型**。

---

## 6.3 工具调用真正在哪里执行

工具本身的执行不是在 processor 里硬编码，而是在：

- `SessionPrompt.resolveTools()`

这里会：

1. 从 `ToolRegistry.tools()` 获取本地工具定义
2. 对参数 schema 做 provider-aware 转换
3. 用 AI SDK 的 `tool({...})` 包装成模型可调用工具
4. 为每个工具注入统一的 `Tool.Context`
5. 包装 `execute()`，在前后触发 plugin hooks

因此工具执行的真正统一入口是：

- `item.execute(args, ctx)`

而这个 `ctx` 是 runtime 级上下文，不是裸函数参数。

---

## 6.4 `Tool.Context` 提供了什么能力

`resolveTools()` 里注入的 `Tool.Context` 包含：

- `sessionID`
- `messageID`
- `callID`
- `abort`
- `agent`
- `messages`
- `extra.model`
- `extra.bypassAgentCheck`
- `metadata()`
- `ask()`

这意味着工具执行时不只是“拿参数做事情”，而是能：

- 感知当前 session
- 感知当前 agent
- 访问最近消息
- 申请权限
- 动态修改 tool part 的 metadata/title
- 响应 abort

因此 tool call 体系是一个**运行时框架**，不是函数表。

---

## 6.5 权限如何介入工具调用

工具执行前，`ctx.ask()` 会调用：

- `PermissionNext.ask()`

它会综合：

- agent permission
- session permission
- 用户批准规则

然后决定：

- `allow`
- `deny`
- `ask`

因此工具调用能否真正执行，不是由模型决定，而是由 runtime 权限系统决定。

这点非常重要。

---

## 6.6 MCP 工具如何调用

除了本地工具，`resolveTools()` 还会合并：

- `MCP.tools()`

对 MCP 工具，系统也会统一做：

- schema transform
- plugin before/after
- permission ask
- 结果文本与附件整理
- 超长输出裁剪

所以本地工具和 MCP 工具在上层 loop 看起来是同一种 tool call。

这说明 OpenCode 已经把 MCP 纳入了统一能力模型，而不是外挂模块。

---

## 6.7 是否支持并行工具调用？

答案是：**支持，但不是所有工具都天然并行，而是通过专门的 `batch` 工具提供受控并行执行。**

### 证据：`packages/opencode/src/tool/batch.ts`

这个工具定义了：

- 参数 `tool_calls: [{ tool, parameters }]`
- 描述为：`Array of tool calls to execute in parallel`

内部实现明确使用：

- `Promise.all(toolCalls.map((call) => executeCall(call)))`

也就是说，`batch` 工具会把多组工具调用真正并发执行。

### 它的并行特性包括：

- 最多执行 25 个工具
- 每个调用独立创建 tool part
- 每个调用独立记录 success/error
- 最终聚合返回执行摘要与 metadata

### 它的限制包括：

- `batch` 不能递归 batch 自己
- 某些工具被明确禁止进入 batch
- 外部工具（例如环境依赖 / MCP / 某些 registry 外工具）不能随意 batched
- 需要先在 registry 里存在且参数能被 schema 校验

所以 OpenCode 的并行工具调用不是“模型随便一次返回多个独立 tool calls 就自动并发”，而是：

- **通过专门的 batch tool 显式请求并行**
- runtime 再受控执行

这是一种更稳妥的设计。

---

## 6.8 普通 tool call 和 batch 并行有什么差异

### 普通 tool call

- 更符合单步思考 -> 单步执行 -> 观察结果 -> 再决定下一步
- 适合有依赖顺序的任务
- loop 控制最清晰

### batch tool

- 适合彼此独立的多工具操作
- 能减少轮次和 latency
- 适合并发搜索、并发读取、并发无依赖工具调用
- 但需要额外的安全限制和结果聚合逻辑

所以 OpenCode 不是一味追求并行，而是：

- 在需要时提供并行工具
- 默认仍以可解释、可控的单步 tool loop 为主

---

## 6.9 输出解析后的状态如何继续推进 loop

`SessionProcessor.process()` 返回值可能是：

- `continue`
- `stop`
- `compact`

然后 `SessionPrompt.loop()` 决定下一步：

- `stop`：结束
- `compact`：创建 compaction 任务
- `continue`：进入下一轮

因此“解析输出 -> 工具执行 -> 写回结果 -> 再推进 loop”是闭环的，不需要额外拼接一层状态机。

---

# 7. Loop、Prompt、上下文、Skill、Tool Call 总结

把这几个最关键问题收束一下：

## 7.1 loop 是什么

OpenCode 的 loop 是一个**基于 session/message/part 的迭代状态机**。

它不是递归拼 prompt，而是：

- 每轮读取最新状态
- 先处理 compaction / subtask
- 正常轮再构造 tools + system + message history
- 调用模型
- 消费事件流
- 再决定下一轮

## 7.2 prompt 是什么

prompt 不是一个模板字符串，而是：

- provider/agent base prompt
- environment prompt
- instruction prompt
- skills index
- structured output contract
- message history

共同组成的多层输入。

## 7.3 上下文怎么管理

上下文管理依赖：

- `MessageV2.toModelMessages()`
- `filterCompacted()`
- `SessionCompaction`
- `InstructionPrompt`
- `SystemPrompt.skills()`
- reminder 注入
- 插件消息变换

## 7.4 skill 是什么

skill 是按需加载的任务说明书/工作流包。

它先以索引形式进入 system prompt，再通过 `skill` tool 惰性加载完整内容。

## 7.5 tool call 是什么

tool call 是 runtime 的第一等执行通道：

- 有 schema
- 有 permission
- 有 part state machine
- 有 plugin hook
- 有 MCP 统一接入
- 有并行 batch 扩展

---

# 8. 一句话总结

**OpenCode 的本质不是“一个会调模型的 CLI”，而是一套以 session/message/part 为状态真相、以 loop 为执行调度中心、以 provider abstraction 为模型适配层、以 tool/permission/skill/instruction 为能力扩展面的完整 agent runtime；它的代码检索、上下文选择、输出解析、工具调用、并行执行、IDE 集成都已经被工程化成统一体系。**
