# Gemini CLI 项目深度解读

本文基于仓库源码对 `gemini-cli` 项目做系统性拆解，重点覆盖：

- 整体设计与运行时分层
- 目录结构与各模块职责
- CLI 到 Core 的调用链
- LLM 大模型调用方式
- Agent loop 的完整执行过程
- Prompt 设计，尤其是 system prompt 的组装方式
- 上下文、历史、记忆（memory / GEMINI.md）管理
- Skills 与规则（rules / contextual instructions）机制
- Tool call / Tool scheduler / MCP 扩展机制
- A2A server 的 loop 设计

这不是对 README 或文档的二次转述，而是基于源码实现进行的工程级解读。

---

# 1. 项目整体定位

`Gemini CLI` 是一个终端优先的 AI agent 框架，核心目标不是“做一个简单聊天命令行”，而是：

- 把 Gemini 大模型包装成一个可以在本地工程目录内工作的软件工程代理
- 通过工具系统，把“只会输出文本”的模型升级为“能搜索、读文件、编辑、执行 shell、调用扩展/MCP”的 agent
- 通过 prompt、规则、memory、hooks、policy engine、tool scheduler，把 agent 的行为约束成一个较稳定、可扩展、可嵌入的工程系统

它本质上是一个分层的 agent runtime：

1. `packages/cli`
   - 面向用户的终端入口
   - 参数解析、认证、交互/非交互模式切换、UI

2. `packages/core`
   - 真正的 agent 内核
   - prompt 组装
   - chat/history 管理
   - LLM API 调用
   - tool registry / tool scheduler
   - memory / skills / hooks / policy / MCP / routing

3. `packages/a2a-server`
   - 把同一套 core runtime 包装成 Agent-to-Agent server
   - 展示另一种 loop 编排方式

4. `packages/vscode-ide-companion`
   - IDE 配套，向 agent 注入 editor context

从架构上讲，`cli` 不是“大脑”，`core` 才是。`cli` 更像壳层和启动器；`core` 才是 agent engine。

---

# 2. Monorepo 目录结构与模块职责

仓库根目录是 npm workspaces monorepo，`package.json` 中声明了：

- `workspaces: ["packages/*"]`

核心目录如下：

## 2.1 根目录

- `README.md`
  - 官方使用说明
- `GEMINI.md`
  - 项目级上下文指令文件，本项目自己也在用这套 memory/context 机制
- `docs/`
  - 用户文档
- `integration-tests/`
  - 集成测试
- `scripts/`
  - 构建、打包、生成、lint 等脚本
- `package.json`
  - monorepo 管理、脚本定义、workspaces 配置

## 2.2 packages/cli

面向用户的 CLI 包，负责：

- 进程启动
- 读取 settings
- 解析命令行参数
- 初始化 config
- 认证和 sandbox 切换
- 交互 UI / 非交互 headless 模式
- session resume/list/delete
- 连接 core runtime

关键文件：

- `packages/cli/index.ts`
  - 总入口，调用 `main()`
- `packages/cli/src/gemini.tsx`
  - CLI 主流程
- `packages/cli/src/nonInteractiveCli.ts`
  - 非交互 loop
- `packages/cli/src/interactiveCli.ts`
  - 交互 UI（未展开，但由 `gemini.tsx` 动态导入）
- `packages/cli/src/core/initializer.ts`
  - app 初始化逻辑

## 2.3 packages/core

这是最重要的包，承担 agent runtime 的绝大多数能力：

- Config 与运行时对象装配
- Gemini client / chat / turn
- PromptProvider 与 snippets system prompt 模板
- Tool registry、tool scheduler、tool executor
- Memory / GEMINI.md 发现和加载
- Skills 发现、激活
- MCP client / prompt / resources 注册
- Hook system、policy engine、loop detection、compression、model routing

重要子目录：

- `src/config/`
  - 配置总线、storage、workspace context、agent loop context
- `src/core/`
  - GeminiClient、GeminiChat、Turn、prompts
- `src/prompts/`
  - system prompt 拼装模板
- `src/tools/`
  - 内建工具、MCP 工具、tool registry
- `src/scheduler/`
  - tool scheduling / execution / policy interaction
- `src/services/`
  - context manager、compression、recording、routing 等服务
- `src/skills/`
  - skill loader / manager / builtin skills
- `src/utils/`
  - memory discovery、token 计算、环境上下文等
- `src/hooks/`
  - hook 类型与 hook 流程相关逻辑

## 2.4 packages/a2a-server

这是把 core runtime 包装成服务器的版本。它不是另一个独立 agent 内核，而是复用了 core 里的 GeminiClient / ToolCallRequestInfo / Config 等。

关键文件：

- `packages/a2a-server/src/http/app.ts`
- `packages/a2a-server/src/agent/executor.ts`
- `packages/a2a-server/src/agent/task.ts`

它非常适合用来理解“loop 在服务端如何被显式编排”。

---

# 3. 运行入口与启动链路

## 3.1 最外层入口

入口文件是：

- `packages/cli/index.ts`

它做的事情很薄：

- 注册 `uncaughtException` 处理
- 调用 `main()`
- 在异常时进行清理并退出

所以真正的启动主线在：

- `packages/cli/src/gemini.tsx`

## 3.2 `main()` 的总体流程

`main()` 是整个 CLI 启动编排中心，大致流程如下：

1. 初始化 startup profiler
2. patch stdio / unhandled rejection handler / signal handler
3. 加载 settings
4. 解析命令行参数
5. 构建 partial config
6. 做 auth 刷新与 admin settings 拉取
7. 如有需要，重启到 sandbox 或 child process
8. 真正构建完整 config
9. 初始化 storage、policy、cleanup、hooks、session 管理
10. 根据模式进入：
    - 交互模式 -> `startInteractiveUI(...)`
    - 非交互模式 -> `runNonInteractive(...)`

这个设计说明：

- `config` 是 runtime 的中心依赖注入容器
- CLI 先做外围准备，再把控制权交给 interactive/non-interactive loop
- 同一套 core runtime 可以服务于 TUI、headless、A2A server 三类宿主

---

# 4. Config：整个系统的装配中心

最关键的装配文件是：

- `packages/core/src/config/config.ts`

这个类本质上不是“普通配置对象”，而是一个大型运行时容器，负责持有和初始化：

- model 配置
- tool registry
- mcp client manager
- skill manager
- prompt registry
- resource registry
- policy engine
- message bus
- storage
- Gemini client
- context manager
- sandbox manager
- hook system
- model router service
- file discovery service
- workspace context

## 4.1 Config 构造阶段装入什么

构造函数中可见大量运行时字段：

- tools
  - `coreTools`
  - `allowedTools`
  - `excludeTools`
  - `toolDiscoveryCommand`
  - `toolCallCommand`
- MCP
  - `mcpServerCommand`
  - `mcpServers`
  - `mcpEnabled`
  - `allowedMcpServers`
  - `blockedMcpServers`
- memory / context
  - `userMemory`
  - `geminiMdFileCount`
  - `geminiMdFilePaths`
  - `workspaceContext`
- execution controls
  - `disableLoopDetection`
  - `maxSessionTurns`
  - `continueOnFailedApiCall`
  - `retryFetchErrors`
  - `maxAttempts`
- skills
  - `skillsSupport`
  - `disabledSkills`
  - `adminSkillsEnabled`
- hooks / policy
  - `policyEngine`
  - `messageBus`
  - `enableHooks`
- compression / masking
  - `toolOutputMasking`
  - `compressionThreshold`
- IDE / JIT context
  - `ideMode`
  - `experimentalJitContext`
- routing
  - `modelRouterService`

这意味着 `Config` 已经承担了 DI container / service locator 的角色。

## 4.2 `initialize()` 阶段做什么

`config.initialize()` 会做真正的 runtime 装配：

1. 初始化 storage
2. 把 include directories 加入 workspace context
3. 初始化 file service / git service
4. 创建 `PromptRegistry` 与 `ResourceRegistry`
5. 初始化 `AgentRegistry`
6. 创建 `ToolRegistry`
7. 创建并启动 `McpClientManager`
8. skills 发现与注册
9. hook system 初始化
10. 若开启 `experimentalJitContext`，创建并刷新 `ContextManager`
11. 初始化 `_geminiClient`

非常关键的一点：

- `Config` 不是 new 完就可用
- 许多关键模块只有在 `initialize()` 之后才处于 ready 状态

这也是为什么在 `cli/src/gemini.tsx` 里，非交互模式会显式 `await config.initialize()` 后再进入实际 loop。

---

# 5. LLM 调用链：从用户输入到 Gemini API

这一块是整个项目最核心的链路。

主线类如下：

1. `GeminiClient`
   - Agent loop 总控
2. `Turn`
   - 单次模型 turn 的事件化包装
3. `GeminiChat`
   - 与 `@google/genai` 的实际 chat/history 适配层
4. `ContentGenerator`
   - 底层 API 调用封装（通过 config 提供）

## 5.1 `GeminiClient` 的角色

文件：

- `packages/core/src/core/client.ts`

它的职责不是“直接发一个请求”，而是：

- 持有整个 chat session
- 维护 session turn count
- 处理 hooks（BeforeAgent / AfterAgent）
- 处理 loop detection
- 触发 history compression
- 处理 IDE context 注入
- 做 model routing 与 model stickiness
- 更新 tools schema（可能依赖 model）
- 调用 `Turn.run()` 获取流式事件
- 在工具调用后把结果再喂回模型，继续 agentic loop

它是 agent loop 的总导演。

## 5.2 `GeminiChat` 的角色

文件：

- `packages/core/src/core/geminiChat.ts`

这是 Gemini 对话层的关键适配器，负责：

- 维护 history
- 设置 system instruction
- 设置 tools declarations
- 调用 `generateContentStream`
- 做 stream 中途失败重试
- 记录 user/model 消息到 chat recording
- 维护 curated history vs comprehensive history
- 处理函数调用/函数响应相关的 Gemini API 约束

源码里甚至写明：

- 这是对上游 `js-genai` chats 的复制版本
- 目的是绕过上游对 function responses 处理的 bug

这说明该项目对 LLM/tool call 的 runtime 行为做了较深定制，而不是简单调用 SDK。

## 5.3 `Turn` 的角色

文件：

- `packages/core/src/core/turn.ts`

`Turn` 把一个模型 turn 的原始流式结果，转成上层 agent loop 可消费的事件：

- `Content`
- `Thought`
- `ToolCallRequest`
- `Finished`
- `Error`
- `Retry`
- `InvalidStream`
- `LoopDetected`
- `AgentExecutionStopped/Blocked`

也就是说：

- `GeminiChat` 更贴近 API
- `Turn` 更贴近 agent runtime
- `GeminiClient` 更贴近 orchestration

这是个很清晰的三层分工。

---

# 6. 大模型是如何被调用的

## 6.1 实际 API 调用点

最关键调用在：

- `GeminiChat.makeApiCallAndProcessStream()`

内部最终会调用：

- `this.context.config.getContentGenerator().generateContentStream(...)`

传入内容包括：

- `model`
- `contents`
- `config`
  - 其中包含 `systemInstruction`
  - 也包含 `tools`
  - 也包含 `abortSignal`

也就是说，对 Gemini API 来说，真正的一次请求结构是：

1. `systemInstruction`
2. `contents`（历史 + 新输入）
3. `tools`（function declarations）
4. model 配置

## 6.2 `tools` 如何进入模型调用

在 `GeminiClient.startChat()` 和 `GeminiClient.setTools()` 中：

- 从 `toolRegistry.getFunctionDeclarations(modelId)` 拿到 tool declarations
- 包装成：
  - `[{ functionDeclarations: toolDeclarations }]`
- 设置到 `GeminiChat`

因此，tool calling 不是通过 prompt 模拟，而是通过 Gemini function calling 原生能力接入。

## 6.3 system prompt 如何进入模型调用

`GeminiClient.startChat()` 中：

- `const systemInstruction = getCoreSystemPrompt(this.config, systemMemory)`
- 然后传给 `new GeminiChat(...)`

后续 API 调用时，在 `GeminiChat.makeApiCallAndProcessStream()` 中：

- 构造 `GenerateContentConfig`
- 把 `systemInstruction: this.systemInstruction` 放进去

所以 system prompt 是 runtime 级别的正式参数，不是拼到 user message 里伪装的。

## 6.4 模型路由与重试

`GeminiClient.processTurn()` 中：

- 如果当前 sequence 已选定模型，则 stick to 当前模型
- 否则通过 `modelRouterService.route(routingContext)` 选择模型
- 再经过 `applyModelSelection(...)` 走 availability / fallback / policy

`GeminiChat.sendMessageStream()` 与 `makeApiCallAndProcessStream()` 中则负责：

- 连接阶段失败重试
- mid-stream 错误重试
- invalid stream 检测与恢复
- 429 / validation required 处理

说明这个系统并不是“每次 turn 固定一个模型 + 一次请求结束”，而是有：

- 路由
- 粘性模型
- 可恢复重试
- 错误后的继续执行策略

---

# 7. Agent Loop：整个循环是怎样跑起来的

这是本文最重要的一节。

Gemini CLI 本质上跑的是一个经典 agent loop：

1. 把用户输入 + system prompt + history 发给模型
2. 模型输出文本或 tool call
3. 如果有 tool call，就执行工具
4. 把工具结果作为 function response 继续发回模型
5. 重复上述过程，直到没有新的 tool call
6. turn 结束，等待下一次用户输入

但源码实现比这个抽象流程复杂得多。

## 7.1 非交互模式 loop

文件：

- `packages/cli/src/nonInteractiveCli.ts`

这里的 loop 很直观：

1. `while (true)` turnCount++
2. 调用：
   - `geminiClient.sendMessageStream(...)`
3. 遍历 stream events：
   - 内容 -> 输出给终端或 JSON stream
   - tool call request -> 收集到 `toolCallRequests`
   - error / loop detected / max turns 等 -> 特殊处理
4. 如果收集到 tool calls：
   - `scheduler.schedule(toolCallRequests, abortSignal)`
   - 执行工具
   - 把 `completedToolCalls` 记录到 chat recording
   - 如果有 stop-execution tool，则结束
   - 否则把 tool responses 继续喂回模型
5. 没有更多工具时，退出 loop

这个 loop 是“CLI 宿主层”的显式调度。

## 7.2 `GeminiClient.sendMessageStream()` 是上层 loop 的核心入口

其关键逻辑：

1. 如果是新 prompt_id，重置 loop detector / hook state / currentSequenceModel
2. 触发 BeforeAgent hook
   - stop -> 直接停止
   - block -> 直接阻塞
   - additional context -> 注入 `<hook_context>`
3. 调 `processTurn(...)`
4. turn 返回后触发 AfterAgent hook
   - 可 stop / block / clear context / continuation
5. 返回 turn

这里要注意：

- 它本身不直接执行工具
- 它只负责把当前一轮模型推理跑完，产出 tool call request events
- 真正的“执行工具然后再喂回模型”是在宿主 loop 或 A2A task loop 里做

所以架构上是：

- `GeminiClient` 负责 LLM turn orchestration
- `Scheduler` 负责工具执行
- `CLI/A2A Task` 负责把二者串成完整 agent loop

## 7.3 `processTurn()` 的内部阶段

`GeminiClient.processTurn()` 大致按以下顺序执行：

### 阶段 1：turn 初始化

- 新建 `Turn`
- 检查 session turn 数是否超限

### 阶段 2：上下文压缩

- `tryCompressChat(prompt_id, false)`
- 若压缩成功，发出 `ChatCompressed` 事件

### 阶段 3：token window 检查

- 计算 remaining token count
- 估算本次 request token count
- 如果会溢出，发 `ContextWindowWillOverflow`

### 阶段 4：tool output masking

- 对历史中的工具输出做 masking / pruning 处理

### 阶段 5：IDE context 注入

如果 `ideMode` 开启，且当前 history 末尾不是 pending functionCall：

- 取 editor context
- 全量或增量变更 JSON
- 追加到 chat history 里作为一条 user message

### 阶段 6：loop detection

- `loopDetector.turnStarted(signal)`
- 如果怀疑自循环，尝试 `_recoverFromLoop(...)`

### 阶段 7：模型选择

- 根据 routingContext 进行 model routing
- 经过 availability policy 得到最终模型
- 发出 `ModelInfo`
- 更新 tools declaration（model-sensitive）

### 阶段 8：真正跑模型 turn

- `turn.run(modelConfigKey, request, linkedSignal, displayContent)`
- 流式处理事件
- 同时把 event 喂给 loop detector

### 阶段 9：invalid stream / next speaker continuation

如果：

- invalid stream
- 或 `nextSpeakerCheck` 判定下一说话者仍然是 model

则自动递归发送：

- `System: Please continue.`
- 或 `Please continue.`

这其实是 loop 的一个隐藏强化层：

- 不是每次 Gemini 结束就真的结束
- 它会做额外判断决定是否继续让模型再说一轮

## 7.4 A2A server loop 更能看清完整 agent loop

文件：

- `packages/a2a-server/src/agent/executor.ts`
- `packages/a2a-server/src/agent/task.ts`

`CoderAgentExecutor.execute()` 中的 loop 逻辑非常清晰：

1. `agentEvents = currentTask.acceptUserMessage(...)`
2. `for await (const event of agentEvents)`
   - 文本类事件 -> `acceptAgentMessage(event)`
   - `ToolCallRequest` -> 收集到数组
3. 如果有工具调用：
   - `scheduleToolCalls(toolCallRequests, abortSignal)`
4. `waitForPendingTools()`
5. `completedTools = getAndClearCompletedTools()`
6. 如果 completedTools 全部 cancel：
   - 加入 history
   - 结束 turn
7. 否则：
   - `agentEvents = sendCompletedToolsToLlm(completedTools, abortSignal)`
   - 回到 while 循环继续
8. 若没有 completedTools，turn 结束
9. task 状态改成 `input-required`

这个 loop 就是标准 agentic loop 的教科书式实现：

- `LLM -> Tool Requests -> Scheduler -> Tool Responses -> LLM -> ...`

它说明整个系统不是“模型只调用一次工具”，而是支持多轮连续工具链。

---

# 8. Prompt 设计：system prompt 是怎么构建的

这一块是源码里非常精致的一部分。

核心文件：

- `packages/core/src/core/prompts.ts`
- `packages/core/src/prompts/promptProvider.ts`
- `packages/core/src/prompts/snippets.ts`
- `packages/core/src/prompts/snippets.legacy.ts`

## 8.1 Prompt 构建总入口

`getCoreSystemPrompt(config, userMemory)` 最终调用：

- `new PromptProvider().getCoreSystemPrompt(...)`

所以 `PromptProvider` 才是 prompt assembly 的中心。

## 8.2 `PromptProvider.getCoreSystemPrompt()` 的总体思路

它不是写死一大段 prompt，而是“按运行时状态组装 prompt section”。

大致步骤：

1. 解析 `GEMINI_SYSTEM_MD` 环境变量
   - 若指定外部 system.md，则直接读模板文件作为 base prompt
2. 否则走标准组合逻辑
3. 收集运行时状态：
   - interactive / approval mode
   - plan mode / yolo mode
   - skills
   - enabled tools
   - approved plan path
   - active model 是否支持 modern features
   - 可用 context filenames
4. 根据这些状态构造 `SystemPromptOptions`
5. 调用 `snippets.getCoreSystemPrompt(options)` 生成 base prompt
6. 最后调用 `renderFinalShell(basePrompt, userMemory, contextFilenames)`
   - 把 memory/contextual instructions 包到 prompt 底部

这个设计非常关键：

- prompt 是参数化模板，而不是单文本常量
- 运行模式不同，system prompt 的结构也不同

## 8.3 `snippets.ts` 是 prompt 的模板 DSL

`snippets.ts` 不只是字符串常量，而是一个 prompt rendering library。

它定义了很多 section renderer：

- `renderPreamble`
- `renderCoreMandates`
- `renderSubAgents`
- `renderAgentSkills`
- `renderHookContext`
- `renderPrimaryWorkflows`
- `renderPlanningWorkflow`
- `renderOperationalGuidelines`
- `renderSandbox`
- `renderInteractiveYoloMode`
- `renderGitRepo`
- `renderUserMemory`
- `renderTaskTracker`

最外层的 `getCoreSystemPrompt(options)` 就是按 section 拼装。

这说明 prompt 设计已经组件化了。

## 8.4 system prompt 的核心内容有哪些

从 `snippets.ts` 可以看出 system prompt 主要包含以下层次：

### 1. Preamble

定义 agent 身份：

- interactive CLI agent
- autonomous CLI agent
- specializing in software engineering tasks

### 2. Core Mandates

这是最核心的行为约束层，包含：

- 安全与系统完整性
- source control 约束
- 上下文效率原则
- 工程标准
- contextual precedence
- 测试要求
- 指令与 inquiry 的区分
- explain before acting
- 不要 silent tool call
- 不要擅自 revert
- skill guidance 等

可以把它理解为“高优先级 agent constitution”。

### 3. Sub-agents

如果注册了 sub-agents，就把可用 agent 列出来，并要求：

- 作为 orchestrator 调度更适合的 agent
- 对只读独立任务可以并行派发
- 对会修改同一资源的任务禁止并行

### 4. Skills

把可用 skill 列出来，但不直接展开内容，而是告诉模型：

- 需要时调用 `activate_skill` 工具激活 skill

这是一个很重要的设计：

- skill 不直接常驻 system prompt
- 先列目录，按需加载详细指令
- 节省 token

### 5. Primary Workflows / Planning Workflow

根据当前 approval mode：

- 普通模式 -> 加 development lifecycle、new application workflow、tool usage、validation 规范
- plan mode -> 切换到计划生成 workflow

### 6. Operational Guidelines

约束输出风格、工具使用、命令解释、确认协议等。

### 7. Sandbox / Git / YOLO 等补充段

根据运行环境动态插入。

### 8. User Memory / Contextual Instructions

最后附加 `renderUserMemory(...)` 产物。

这层让 prompt 不只是 system constitution，还把项目/用户上下文一起接上。

## 8.5 Prompt 设计的核心哲学

这个项目的 prompt 有几个非常明显的设计哲学：

### 哲学 1：把“行为系统”写进 prompt

不是只告诉模型“你是个 helpful assistant”，而是把：

- 工程流程
- 安全规范
- 测试要求
- tool usage discipline
- memory precedence
- plan mode discipline

都明确定义成 system prompt 中的结构化规则。

### 哲学 2：prompt 是可组合的 runtime artifact

system prompt 不固定，而是由：

- 交互模式
- plan/yolo 模式
- 可用工具
- skills
- repo 状态
- sandbox
- memory

共同决定。

### 哲学 3：把本地环境事实引入 prompt，但保持边界清晰

比如：

- skills 只是“可激活目录”
- memory 带有明确优先级
- hook_context 明确要求只作信息上下文，不覆盖 core mandates

这是一种防 prompt 污染设计。

---

# 9. 上下文管理：system/context/history 是如何组织的

上下文管理分成至少四层：

1. system prompt
2. persistent memory（GEMINI.md 等）
3. chat history
4. runtime transient context（IDE context / hook context / tool responses）

## 9.1 system prompt

由 `PromptProvider` 生成，作为 `systemInstruction` 正式传给 Gemini API。

## 9.2 persistent memory：`GEMINI.md` 体系

这是本项目一个非常重要的机制。

关键文件：

- `packages/core/src/utils/memoryDiscovery.ts`
- `packages/core/src/services/contextManager.ts`
- `packages/core/src/tools/memoryTool.ts`（通过引用可知负责 context filename）

### 9.2.1 会找哪些文件

通过 `getAllGeminiMdFilenames()`，系统会搜索 context 文件名变体，默认是 `GEMINI.md`。

来源包括：

- 全局：`~/.gemini/` 下
- extension 提供的 context files
- workspace/project 路径向上搜索
- trusted roots 内的环境/目录级文件
- JIT 子目录 memory

### 9.2.2 memory 分层

源码中最终拼成 `HierarchicalMemory`：

- `global`
- `extension`
- `project`

并在 `renderUserMemory()` 中以结构化标签包进 prompt：

- `<global_context>`
- `<extension_context>`
- `<project_context>`

### 9.2.3 冲突优先级

在 `renderUserMemory()` 中明确写入 prompt：

- Sub-directories > Workspace Root > Extensions > Global
- 但不能覆盖 safety / security / agent integrity 级别的 core mandates

这很关键：

- 项目规则是强约束
- 但不能推翻系统安全边界

### 9.2.4 JIT 子目录上下文

`ContextManager.discoverContext(accessedPath, trustedRoots)` 会：

- 当 agent 访问某个路径时
- 从该路径向上找到对应 trusted root
- 只加载尚未加载过的子目录级 `GEMINI.md`

这是很高级的设计：

- 不把整个代码树所有子目录上下文一次性塞进 prompt
- 而是在访问具体路径时“按需加载”
- 节省 token，且提高局部上下文准确度

## 9.3 chat history

由 `GeminiChat` 维护。

### 9.3.1 两类 history

`GeminiChat.getHistory(curated?: boolean)` 支持两种历史：

- comprehensive history
  - 完整记录，包括可能无效的模型响应
- curated history
  - 只保留有效 user/model turns，用于后续请求

这样做是因为：

- 完整记录利于调试与回放
- 但发给模型的历史必须合法、干净

### 9.3.2 function call / function response 的历史约束

源码专门处理了一个 Gemini API 约束：

- `functionResponse` 必须紧跟 `functionCall`

因此在注入 IDE context 或其他额外上下文时，会避免打断 pending tool call 链。

这体现了该项目对 Gemini function calling 协议有精细适配。

## 9.4 IDE context

`GeminiClient.getIdeContextParts()` 中会：

- 读取 IDE store 中的 open files / active file / cursor / selectedText
- 首次发送全量 JSON
- 后续只发送 delta changes JSON

注入形式是 user history 中的一条文本：

- “Here is the user's editor context as a JSON object...”
- 或 “Here is a summary of changes...”

这是一个典型的 runtime transient context。

它不属于 system prompt，也不持久驻留在 memory 文件，而是随着会话变化逐步注入。

## 9.5 hook context

BeforeAgent hook 可以返回 additional context，随后被包成：

- `<hook_context>...</hook_context>`

并追加到 request 末尾。

同时 system prompt 中又专门加了 Hook Context section，要求模型：

- 将其视为只读信息
- 不要把 hook context 当成高优先级指令覆盖 core mandates

这是一个典型的防上下文注入污染设计。

---

# 10. 上下文压缩：历史过长时怎么处理

关键文件：

- `packages/core/src/services/chatCompressionService.ts`

这是本项目在上下文管理上最有工程深度的地方之一。

## 10.1 为什么要压缩

当历史太长，接近模型 token limit 时：

- 不能无脑丢掉旧历史
- 也不能一直带着巨量 tool output
- 必须把“长期重要状态”提炼成更短的快照

## 10.2 压缩触发

在 `GeminiClient.processTurn()` 里，先调用：

- `tryCompressChat(prompt_id, false)`

`ChatCompressionService.compress(...)` 会：

- 检查当前历史 token 数
- 若未超过阈值，则 `NOOP`
- 超过阈值则压缩

默认阈值是模型上下文窗口的 `0.5`。

## 10.3 压缩前的工具输出截断

压缩前先跑 `truncateHistoryToBudget(...)`：

- 反向遍历 history
- 优先保留最新 tool outputs
- 超出预算的旧 function responses 会截断
- 截断内容还会保存到临时文件，并生成一个占位说明

这说明系统非常清楚一个现实问题：

- 真正炸 context 的往往不是对话文本，而是 grep / cat / logs / shell 输出

## 10.4 压缩策略

压缩不是“总结全部历史”，而是：

1. 找 split point
2. 老历史部分送去 summarizer
3. 新历史部分保留原样
4. 生成 `<state_snapshot>` 风格摘要
5. 再做一次 verification pass 自我校验
6. 用：
   - `user: <summary>`
   - `model: Got it...`
   - `...recent history`
   重构新 history

非常重要的一点：

- 压缩本身也是一次 LLM 调用
- 但它用的是 utility/compression model config alias
- 并且会做一次“自检式二次总结”

这说明它不是粗暴压缩，而是试图保证状态不丢失。

## 10.5 压缩 prompt

压缩 prompt 由：

- `PromptProvider.getCompressionPrompt()`

生成。

所以这个项目不仅普通 agent prompt 是系统化的，连 compression prompt 也走统一 prompt provider。

---

# 11. Skills：怎么发现、激活、注入到模型行为中

关键文件：

- `packages/core/src/skills/skillLoader.ts`
- `packages/core/src/skills/skillManager.ts`
- `packages/core/src/tools/activate-skill.ts`

## 11.1 Skill 文件格式

skill 是基于 `SKILL.md` 文件定义的。

`skillLoader.ts` 中：

- 通过 glob 搜索 `SKILL.md` 或 `*/SKILL.md`
- 解析 frontmatter
- 必须有：
  - `name`
  - `description`
- skill body 为 frontmatter 后面的正文

也就是说 skill 本质上是“结构化指令包”。

## 11.2 Skills 从哪里来

`SkillManager.discoverSkills(...)` 中的优先级：

1. built-in skills
2. extension skills
3. user skills
4. user agent skills alias
5. workspace skills
6. workspace agent skills alias

并且只有 trusted folder 才加载 workspace skills。

这很好理解：

- 用户全局 skill 可以长期复用
- 工程内 skill 可能带执行偏好，因此需要 trust 边界

## 11.3 Skill 冲突解决

`addSkillsWithPrecedence()` 用 name 去重：

- 后加载的覆盖前加载的
- 如果覆盖 builtin，会给 warning
- 非 builtin 冲突也发 warning

所以 skill 是“同名覆盖式”的，而不是多版本共存。

## 11.4 Skill 如何出现在 prompt 中

system prompt 里并不会直接塞入 skill body，而是：

- 仅列出 skill 名称、描述、位置
- 明示通过 `activate_skill` 工具来激活

这是一个重要的 token 优化设计。

## 11.5 Skill 激活后发生什么

根据 `tools/activate-skill.ts` 的 grep 结果：

- `skillManager.activateSkill(skillName)`
- skill 所在目录会加入 workspace context，便于读取配套资源
- ActivateSkillTool 的 schema 会随着已发现 skills 动态重注册

这意味着：

1. skills 先发现并注册元数据
2. system prompt 告诉模型有哪些 skill 可用
3. 模型如需详细 instructions，调用 `activate_skill`
4. runtime 再把 skill 激活并允许读取 skill 资源

所以 skill 机制本质是“按需展开的 prompt/tool 混合机制”。

---

# 12. Rules：系统规则、项目规则、用户规则如何叠加

在这个项目里，“rules” 不是一个独立模块，而是多个层次叠加形成的行为约束系统。

## 12.1 系统级 rules

来自 system prompt 的：

- Core Mandates
- Operational Guidelines
- Planning Workflow
- Sandbox / Git / Task Tracker 等 sections

这是最高层 agent runtime 规则。

## 12.2 Project/User contextual rules

来自：

- `GEMINI.md`
- `~/.gemini/GEMINI.md`
- extension context files
- 子目录级 GEMINI.md

这些在 `renderUserMemory()` 中被附加为 `<loaded_context>`。

它们主要定义：

- 技术栈偏好
- 开发习惯
- 项目约束
- 文档规则
- PR/测试/格式化约束等

例如本项目自己的 `GEMINI.md` 中写了：

- docs 修改要用 `docs-writer` skill
- `snippets.legacy.ts` 不要随意改 prompt 语义
- 测试环境变量用 `vi.stubEnv`

这就是项目规则直接进入 agent 上下文的体现。

## 12.3 Hook / Policy rules

除了 prompt 层规则，系统还有 runtime 规则层：

- Policy Engine
- Hook System

它们可以在模型前、工具前、工具确认时、session start/end 等阶段介入。

所以该系统是“双层约束”：

1. prompt 中让模型“知道该怎么做”
2. runtime 中在真正执行时“限制能做什么”

这比只有 prompt 约束的 agent 可靠得多。

---

# 13. Tool Call 机制：模型如何调用工具

这是项目的另一核心部分。

## 13.1 Tool registry

关键文件：

- `packages/core/src/tools/tool-registry.ts`

`ToolRegistry` 负责：

- 注册内建工具
- 注册 discovered tools
- 注册 MCP tools
- 向模型暴露 function declarations
- 按名称取 tool instance

`Config.initialize()` 中会创建 tool registry。

## 13.2 Tools 如何暴露给模型

在 `GeminiClient.setTools()` / `startChat()` 里：

- 从 `toolRegistry.getFunctionDeclarations(modelId)` 获取 declarations
- 传给 Gemini API 的 `tools`

也就是说，模型并不知道工具实现，只看到函数 schema。

## 13.3 模型发出工具调用

当 Gemini API 返回 function call 时：

- `Turn.run()` 从 `resp.functionCalls` 中取出
- `handlePendingFunctionCall(...)` 构造 `ToolCallRequestInfo`
- 产出 `GeminiEventType.ToolCallRequest`

这一步只是在事件层“提出请求”，尚未执行。

## 13.4 Tool scheduler 执行工具

关键文件：

- `packages/core/src/core/coreToolScheduler.ts`

它的职责包括：

1. 根据 tool name 找到 tool instance
2. build invocation，校验参数
3. 走 Policy Engine
4. 若需要用户确认，则进入 AwaitingApproval
5. 用户确认后进入 Scheduled
6. 再进入 Executing
7. 由 `ToolExecutor` 实际执行
8. 记录成功 / 错误 / 取消状态
9. 产生 `CompletedToolCall`

这是一个完整的工具状态机，而不是“拿到函数名就 execute”。

## 13.5 Tool call 状态机

从 scheduler 代码看，典型状态有：

- `Validating`
- `AwaitingApproval`
- `Scheduled`
- `Executing`
- `Success`
- `Error`
- `Cancelled`

因此工具调用是高度受控的工作流。

## 13.6 工具结果如何回喂给模型

在 A2A `Task.sendCompletedToolsToLlm()` 中看得很清楚：

- 遍历 completed tool calls
- 抽取 `response.responseParts`
- 合并成 `llmParts`
- `geminiClient.sendMessageStream(llmParts, ...)`

这意味着 tool result 并不是普通自然语言再拼一段，而是标准 function response parts 回送给模型。

所以它是正统的 function calling agent loop。

---

# 14. Tool 的扩展方式：Discovered Tools 与 MCP

## 14.1 Discovered Tools

`tool-registry.ts` 中的 `DiscoveredToolInvocation` 表明：

- 有些工具不是内建 TS 类，而是外部命令发现来的
- 执行方式是：
  - `spawn(toolCallCommand, [originalToolName])`
  - 把参数 JSON 写到 stdin
  - stdout 作为结果

因此项目支持一种“外部进程工具协议”。

## 14.2 MCP Tools

Config 初始化时会创建：

- `McpClientManager`

并异步启动：

- `startConfiguredMcpServers()`

MCP server 可向系统提供：

- tools
- prompts
- resources

相应 registry 有：

- `ToolRegistry`
- `PromptRegistry`
- `ResourceRegistry`

这说明 MCP 在这里不是只做 tool call，而是扩展三类能力：

1. Tool
2. Prompt
3. Resource

## 14.3 MCP Prompt Registry

`PromptRegistry` 代码非常简单，但很说明问题：

- 提示词也可由外部 MCP server 注册
- 同名 prompt 会自动重命名为 `serverName_promptName`

意味着该系统把 prompt 也看作一种可发现资源。

## 14.4 MCP Instructions 注入 memory

`memoryDiscovery.ts` 与 `ContextManager` 中都可见：

- `config.getMcpClientManager()?.getMcpInstructions()`
- 最终拼到 project memory 中

说明：

- MCP 不只是“提供工具”
- 还会把 server usage instructions 注入 agent 上下文
- 让模型知道有哪些外部能力以及如何使用它们

---

# 15. Prompt、Skills、Tools 三者关系

这个项目有一个很值得借鉴的设计：

## 15.1 Prompt 决定行为规范

system prompt 负责：

- 定义 agent 的角色和方法论
- 约束工具如何使用
- 规定工程流程与安全边界

## 15.2 Tools 决定执行能力

tools 决定 agent 能做什么：

- 读文件
- grep
- edit
- shell
- memory
- ask_user
- activate_skill
- MCP tools

## 15.3 Skills 决定领域化操作手册

skills 不是新执行能力，而是：

- 一组特定问题域下的高密度指令包
- 通过 `activate_skill` 按需注入

因此：

- Prompt = 宪法
- Tools = 手脚
- Skills = 专项 SOP

这三者组合起来，agent 才既通用又可定制。

---

# 16. Loop、Prompt、Context 三者的协同关系

这是理解这个项目的关键。

## 16.1 Loop 决定执行节奏

loop 负责：

- 什么时候问模型
- 什么时候执行工具
- 什么时候回喂工具结果
- 什么时候结束一轮

## 16.2 Prompt 决定模型“倾向怎样思考与行动”

prompt 负责：

- 让模型遵循 research -> strategy -> execution
- 让模型知道要优先搜索再读文件
- 让模型知道何时应使用 skills / sub-agents / tools
- 让模型知道上下文优先级与安全边界

## 16.3 Context 决定模型“基于哪些事实”做决策

context 包括：

- system prompt
- GEMINI.md / loaded_context
- history
- IDE context
- hook_context
- tool responses

最终一个高质量 agent，并不是靠单点优化，而是：

- loop 保证能行动
- prompt 保证行动方式合理
- context 保证行动依据正确

Gemini CLI 的设计本质上就是把这三者工程化组合。

---

# 17. A2A Server：同一套内核的另一种宿主形态

`packages/a2a-server` 非常值得注意，因为它说明 core runtime 是可嵌入的。

## 17.1 `CoderAgentExecutor`

它不是自己重新实现一套 agent，而是：

- 读取 workspace settings / extensions
- 调用 `loadConfig(...)`
- 创建 `Task`
- 使用 task 中的 `geminiClient`
- 跑自己的 execution loop

## 17.2 `Task`

`Task` 负责：

- 接受用户消息
- 接受 agent message
- schedule tools
- wait tools
- 把 tool results 回喂给 llm
- 管理 task state

这说明 core runtime 被设计成：

- CLI 可以用
- Server 也可以用
- 本质是 headless runtime + 多宿主适配

这是很典型的“核心引擎 + 多前端壳层”架构。

---

# 18. 这个项目设计上的几个亮点

## 18.1 Prompt 组件化而不是大字符串

`PromptProvider + snippets.ts` 的组合非常成熟：

- 可测试
- 可按运行模式裁剪
- 易于插入新 section
- 便于对 legacy/modern model 做差异化 prompt

## 18.2 历史压缩做得很工程化

不是简单 summarize，而是：

- 先截断大 tool outputs
- 再做 snapshot 总结
- 再做 verification pass
- 再重构 history

## 18.3 Tool scheduler 是强状态机，而不是简单调用器

它具备：

- 参数校验
- policy check
- approval flow
- modify-with-editor
- cancel cascade
- completed batch reporting

非常像一个小型 workflow engine。

## 18.4 Skills 的按需激活很节省上下文

不是把所有 skill body 常驻在 system prompt，而是：

- 仅列索引
- 按需通过 tool 激活

这对长会话 token 成本控制非常有效。

## 18.5 Context 分层明确

它清楚区分了：

- system constitution
- loaded_context
- hook_context
- IDE context
- function response history

这使得不同来源的上下文不容易互相污染。

---

# 19. 这个项目的核心主线，用一句话概括

如果只用一句话概括 `gemini-cli` 的架构：

> 它是一个以 `Config` 为装配中心、以 `GeminiClient + GeminiChat + Turn` 为 LLM runtime、以 `ToolRegistry + CoreToolScheduler` 为执行系统、以 `PromptProvider + GEMINI.md memory + ContextManager` 为行为/上下文控制层的终端型软件工程 agent 平台。

---

# 20. 建议你阅读源码的顺序

如果你接下来还想继续深入，建议按这个顺序读：

## 第一层：入口与装配

- `packages/cli/index.ts`
- `packages/cli/src/gemini.tsx`
- `packages/core/src/config/config.ts`

## 第二层：LLM loop

- `packages/core/src/core/client.ts`
- `packages/core/src/core/turn.ts`
- `packages/core/src/core/geminiChat.ts`
- `packages/cli/src/nonInteractiveCli.ts`

## 第三层：prompt / memory / context

- `packages/core/src/prompts/promptProvider.ts`
- `packages/core/src/prompts/snippets.ts`
- `packages/core/src/utils/memoryDiscovery.ts`
- `packages/core/src/services/contextManager.ts`
- `GEMINI.md`

## 第四层：tools / scheduler / skills

- `packages/core/src/tools/tool-registry.ts`
- `packages/core/src/core/coreToolScheduler.ts`
- `packages/core/src/skills/skillLoader.ts`
- `packages/core/src/skills/skillManager.ts`
- `packages/core/src/tools/activate-skill.ts`

## 第五层：高级能力

- `packages/core/src/services/chatCompressionService.ts`
- `packages/core/src/hooks/*`
- `packages/core/src/policy/*`
- `packages/core/src/mcp* / tools/mcp-*`
- `packages/a2a-server/src/agent/executor.ts`
- `packages/a2a-server/src/agent/task.ts`

---

# 21. 最后总结

`Gemini CLI` 的核心不是一个“命令行聊天器”，而是一套相当完整的 agent runtime：

- `CLI` 负责启动、交互、认证、会话和宿主环境
- `Core` 负责 prompt、LLM、history、tool call、skills、memory、policy、hooks、MCP
- `Loop` 通过“模型输出 -> 工具执行 -> 工具结果回喂”的方式实现 agentic execution
- `Prompt` 通过组件化 sections 定义方法论、规则和执行纪律
- `Context` 通过 system prompt + GEMINI.md + history + IDE context + hook context 分层管理
- `Skills` 通过按需激活避免 prompt 膨胀
- `Tool scheduler` 通过状态机、审批与策略检查把工具执行做成可靠运行时

如果你要学习“一个工程级 CLI agent 是如何落地”的源码，这个仓库最值得看的，不是 UI，而是这四条主线：

1. `Config` 装配主线
2. `GeminiClient / GeminiChat / Turn` 的 LLM loop 主线
3. `PromptProvider / snippets / memoryDiscovery / ContextManager` 的 prompt-context 主线
4. `ToolRegistry / CoreToolScheduler / skills / MCP` 的能力扩展主线
