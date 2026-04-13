# Gemini CLI 多 Agent 设计解读

本文基于 `gemini-cli` 源码，专门拆解其多 Agent 设计：

- Agent 如何被注册、暴露和调用
- 父 Agent 与子 Agent 的职责边界
- 子 Agent 之间如何通信、如何被管理
- Context、Session、Prompt ID 如何传递和隔离
- 本地子 Agent、远程 A2A Agent、Browser Agent 的差异
- 这个系统的设计理念：**工具化编排，而不是自由递归自治**

---

## 1. 先给结论：Gemini CLI 的“多 Agent”不是松散的 Agent 集群

Gemini CLI 的多 Agent 机制，本质上是一个**受控的、工具驱动的委托系统**：

- 主 Agent 负责对话主线和任务编排
- 子 Agent 被包装成标准 Tool，交给主 Agent 决策调用
- 子 Agent 自己也可以再调用工具，但**不能再调用其他 Agent**
- 所有子 Agent 的执行都由统一的运行时、消息总线、调度器和会话记录机制约束

所以它不是“Agent 自己找 Agent”的开放式协作网络，而是：

> **Parent Agent = 编排者 / 决策者**
>
> **Child Agent = 被委托执行的专用工作单元**

这点非常关键：它决定了系统是可控、可审计、可回放的，而不是黑盒式自组织。

---

## 2. 多 Agent 的入口：AgentRegistry 负责“发现与注册”

核心入口在：

- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/registry.ts`
- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/types.ts`

### 2.1 Agent 类型

`AgentDefinition` 分成两类：

- `local`
- `remote`

本质上，所有 Agent 都先抽象成统一定义，再由不同执行器处理。

### 2.2 内置 Agent

内置 Agent 在 `AgentRegistry.loadBuiltInAgents()` 中注册：

- `codebase_investigator`
- `cli_help`
- `generalist`
- `browser_agent`（按配置动态开启）

对应定义文件：

- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/codebase-investigator.ts`
- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/cli-help-agent.ts`
- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/generalist-agent.ts`
- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/browser/browserAgentDefinition.ts`

### 2.3 外部 Agent

`AgentRegistry` 还会加载：

- 用户目录下的 agents
- 项目目录下的 agents
- extension 注入的 agents
- remote A2A agents

这意味着“多 Agent”并不只限于内置角色，而是一个可扩展的 Agent 注册系统。

---

## 3. Agent 如何被“工具化”

Gemini CLI 最核心的设计是：**把 Agent 变成 Tool**。

关键文件：

- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/subagent-tool.ts`
- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/subagent-tool-wrapper.ts`
- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/local-invocation.ts`
- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/remote-invocation.ts`

### 3.1 `SubagentTool`

`SubagentTool` 是对外暴露给主 Agent 的 Tool 外壳：

- 校验输入 schema
- 根据 AgentDefinition 生成 Tool schema
- 让模型把“调用某个 Agent”看作一次普通 tool call

### 3.2 `SubagentToolWrapper`

`SubagentToolWrapper` 是执行层的动态分发器：

- `local` Agent -> `LocalSubagentInvocation`
- `remote` Agent -> `RemoteAgentInvocation`
- `browser_agent` -> `BrowserAgentInvocation`

也就是说，工具名一样，但真正执行路径取决于 Agent 类型。

### 3.3 这层包装的意义

它把多 Agent 问题变成了一个统一协议：

1. 主 Agent 生成 `functionCall`
2. 调度器把它识别成某个 Agent Tool
3. Wrapper 选择正确的 invocation
4. 子 Agent 执行并回传 `ToolResult`

这让“多 Agent”在运行时表现得像一套标准工具系统。

---

## 4. 父 Agent 与子 Agent 的分工

### 4.1 父 Agent：总控与编排

父 Agent 的职责：

- 理解用户目标
- 选择是否调用某个子 Agent
- 决定调用哪个子 Agent
- 汇总子 Agent 结果，再继续主流程

它并不直接关心子 Agent 内部怎么跑，只关心：

- 输入是什么
- 输出是什么
- 是否需要继续追问或补充工具调用

### 4.2 子 Agent：专用执行器

子 Agent 的职责：

- 在限定工具集和限定目标下完成一件事
- 自己完成多轮推理与工具调用
- 输出结构化结果或摘要

典型子 Agent 分工：

- `codebase_investigator`：做代码库分析、结构梳理、根因定位
- `cli_help`：回答 Gemini CLI 使用与文档问题
- `generalist`：处理高轮次、高输出量、通用复杂任务
- `browser_agent`：执行浏览器自动化任务

### 4.3 设计原则

这套分工体现的是：

- 父 Agent 负责“判断与组合”
- 子 Agent 负责“深入执行”
- 子 Agent 的输出应该尽量结构化、可读、可回传

---

## 5. 子 Agent 内部怎么运行：LocalAgentExecutor

关键文件：

- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/local-executor.ts`

### 5.1 子 Agent 不是“直接调用模型一次”

Local Agent 走的是完整 agent loop：

- 模型生成 function call
- 调度工具
- 把 tool response 喂回模型
- 继续循环，直到 `complete_task`

这意味着子 Agent 自身也是一个完整的 agent runtime，而不是一个单次 prompt。

### 5.2 父子隔离的 ToolRegistry

`LocalAgentExecutor.create()` 会创建一个**独立的工具注册表**：

- 从父 registry 拷贝允许的工具
- 过滤掉 agent 名称本身，防止递归调用
- 按 `toolConfig` 控制可见工具
- 若没有 `toolConfig`，默认使用父 registry 的工具集

### 5.3 防止 Agent 递归调用 Agent

这是系统最重要的安全边界之一：

- `getAllAgentNames()` 会收集所有 Agent 名称
- 若某工具名是 Agent 名称，则跳过注册
- 结论：**Agent 不能调用另一个 Agent，也不能自我递归**

这不是限制表达能力，而是为了避免：

- 无限嵌套
- 责任边界失控
- 会话和调度状态爆炸

### 5.4 promptId 和 parentCallId

`LocalAgentExecutor.create()` 会从上下文中提取：

- `parentPromptId = context.promptId`
- `parentCallId = getToolCallContext()?.callId`

然后生成本子 Agent 自己的 `agentId`：

- 如果有父 promptId，就加前缀
- 再拼上子 Agent 名称和随机后缀

这让每个子 Agent turn 的 promptId 都可追踪、可归属到父调用链。

---

## 6. 通信机制：消息总线 + 调度器 + 活动事件

关键文件：

- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/confirmation-bus/message-bus.ts`
- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/agent-scheduler.ts`
- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/local-invocation.ts`
- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/browser/browserAgentInvocation.ts`

### 6.1 MessageBus 的作用

MessageBus 负责：

- tool confirmation request / response
- policy decision 处理
- UI 层消息广播
- 子 Agent 调用链上的确认与回传

它不是单纯“事件广播”，而是把**权限决策**也绑在消息流中。

### 6.2 子 Agent 如何把消息标识为“来自哪个子 Agent”

`LocalAgentExecutor.create()` 会包装一层 `subagentMessageBus`：

- 若消息类型是 tool-confirmation-request
- 就自动附加 `subagent: definition.name`
- 这样 policy engine 和 UI 就知道这次确认请求来自哪个子 Agent

这是一种轻量但很关键的“身份注入”。

### 6.3 调度器的职责

`scheduleAgentTools()` 会：

- 创建代理 config
- 把 agent 专属 toolRegistry 注入给 Scheduler
- 将 `schedulerId`、`subagent`、`parentCallId` 传给调度器
- 执行实际 tool call

因此，子 Agent 的工具执行不是直接触发的，而是经过统一调度层。

### 6.4 事件流与可观测性

子 Agent 会通过 `SubagentActivityEvent` 把执行过程流式发给上层：

- `THOUGHT_CHUNK`
- `TOOL_CALL_START`
- `TOOL_CALL_END`
- `ERROR`

上层会把这些事件转换成可显示的 progress，形成子 Agent 的实时执行面板。

---

## 7. Remote Agent：跨进程 / 跨服务的多 Agent 通信

关键文件：

- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/remote-invocation.ts`
- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/a2a-client-manager.ts`
- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/a2aUtils.ts`

### 7.1 Remote Agent 的本质

Remote Agent 不是本地 loop，而是通过 A2A 协议访问远端 Agent Card 和远端任务流。

### 7.2 状态持久化

`RemoteAgentInvocation` 用一个静态 `sessionState` 保存：

- `contextId`
- `taskId`

这是为了让同一个远端 Agent 的多次调用能延续会话状态。

### 7.3 远端通信流程

1. 解析 Agent Card
2. 建立 A2A Client
3. 按 `contextId/taskId` 发消息
4. 流式接收 status / artifact / message 更新
5. 从响应中提取新的 context/task id
6. 下次调用继续复用

### 7.4 远端 Agent 的角色

远端 Agent 更像“外部协作者服务”而不是本地子任务：

- 本地系统发起委托
- 远端系统维护自己的上下文
- 本地只保留映射和结果重组

---

## 8. Browser Agent：一种特殊子 Agent

关键文件：

- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/browser/browserAgentDefinition.ts`
- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/browser/browserAgentInvocation.ts`

### 8.1 它不是普通工具

Browser Agent 是一个专用子 Agent：

- 通过 `LocalAgentExecutor` 运行自己的 loop
- 但工具集在运行时动态装配
- 任务结束后必须清理浏览器资源

### 8.2 它的特殊性

- 依赖 MCP / browser 工具初始化
- 允许视觉分析、页面操作、截图理解
- 适合端到端网页任务

### 8.3 设计取向

Browser Agent 体现了同一原则：

> 复杂能力不要直接塞进主 Agent，而是封装成可委托的专用子 Agent。

---

## 9. Context 如何管理：所有 Agent 共享同一个运行时骨架，但各自有局部视图

关键文件：

- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/config/agent-loop-context.ts`
- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/config/config.ts`

### 9.1 AgentLoopContext 包含什么

`AgentLoopContext` 不是简单配置，而是一个执行期视图：

- `config`
- `promptId`
- `toolRegistry`
- `messageBus`
- `geminiClient`
- `sandboxManager`

### 9.2 父子 Agent 的 context 关系

子 Agent 不是重新构造一套全新世界，而是：

- 共享父级 `config`
- 继承父级运行时能力
- 但使用自己的工具视图、promptId、执行轨迹

这是一种**共享容器 + 局部隔离**模型。

### 9.3 为什么这样设计

因为需要同时满足：

- 共享 policy / storage / model / message bus
- 子任务必须有独立执行身份
- 工具集合必须受控
- 会话记录必须可分辨主/子

---

## 10. Session 如何管理：主会话与子会话都走同一套记录机制

关键文件：

- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/core/geminiChat.ts`
- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/services/chatRecordingService.ts`
- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/utils/session.ts`
- `@/Users/simonwang/project/agent/gemini-cli/packages/sdk/src/agent.ts`
- `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/core/client.ts`

### 10.1 sessionId 的来源

`sessionId` 来自 `promptId` 或显式创建的 UUID。

### 10.2 ChatRecordingService

每个 `GeminiChat` 都会持有一个 `ChatRecordingService`：

- 初始化时写入 session file
- 记录 user / gemini 消息
- 记录 thoughts
- 记录 tool calls
- 记录 token usage

文件存储在项目临时目录下的 chats 子目录。

### 10.3 主会话与子会话

`ConversationRecord.kind` 可以是：

- `main`
- `subagent`

这表示主会话和子 Agent 会话会以不同语义被记录，但底层格式一致。

### 10.4 恢复会话

恢复链路是：

1. SDK 找到历史 session 文件
2. 读取 `ConversationRecord`
3. 生成 `ResumedSessionData`
4. `GeminiChat` 初始化时接入该数据
5. `ChatRecordingService.initialize()` 接管已有文件

因此恢复不是“只恢复文本历史”，而是恢复完整对话记录和文件引用。

### 10.5 历史是怎么进入模型的

`GeminiChat` 维护两种 history：

- `comprehensive history`
- `curated history`

它会过滤无效内容、整理 function response / function call，再送入模型。

这保证了子 Agent 和主 Agent 都是基于同样的历史管理规则运行。

---

## 11. 不同角色的职责表

| 角色 | 职责 | 关键文件 |
| --- | --- | --- |
| 主 Agent | 统筹任务、决定是否委托、汇总结果 | `packages/core/src/core/geminiChat.ts` |
| 子 Agent | 在限定工具和限定目标下执行专用任务 | `packages/core/src/agents/local-executor.ts` |
| SubagentTool | 把 Agent 暴露成标准 Tool | `packages/core/src/agents/subagent-tool.ts` |
| SubagentToolWrapper | 按类型分发到 local/remote/browser invocation | `packages/core/src/agents/subagent-tool-wrapper.ts` |
| MessageBus | 确认、策略、UI 通知、子 agent 身份透传 | `packages/core/src/confirmation-bus/message-bus.ts` |
| Scheduler | 工具调用的统一执行和确认编排 | `packages/core/src/scheduler/scheduler.ts` |
| AgentRegistry | Agent 发现、加载、注册、刷新 | `packages/core/src/agents/registry.ts` |
| ChatRecordingService | 会话记录、恢复、审计 | `packages/core/src/services/chatRecordingService.ts` |
| A2AClientManager | 远端 Agent 通信与会话状态缓存 | `packages/core/src/agents/a2a-client-manager.ts` |

---

## 12. 协同交互的完整链路

### 12.1 本地子 Agent 调用链

1. 主 Agent 在一次 model turn 中生成 `functionCall`
2. `Scheduler` 接收到 call request
3. `SubagentTool` / `SubagentToolWrapper` 解析出目标 Agent
4. `LocalSubagentInvocation` 创建 `LocalAgentExecutor`
5. 子 Agent 自己进入 loop
6. 子 Agent 的工具调用经由自己的 isolated registry 和 scheduler
7. 子 Agent 输出 `ToolResult`
8. 主 Agent 把结果写回自己的历史

### 12.2 远端子 Agent 调用链

1. 主 Agent 调用 remote agent tool
2. `RemoteAgentInvocation` 通过 A2A client 发消息
3. 远端流式返回消息 / task / artifact
4. 本地重组结果并更新 contextId/taskId
5. 主 Agent 使用返回内容继续推理

### 12.3 Browser Agent 调用链

1. 主 Agent 触发 browser agent
2. Browser invocation 动态连接 MCP 工具
3. 子 Agent 自己运行 browser loop
4. 页面交互、截图、工具输出实时流回主界面
5. 完成后清理浏览器资源

---

## 13. 设计理念总结

Gemini CLI 的多 Agent 设计有几个很清晰的原则：

- **统一抽象**：所有 Agent 都先变成 Tool
- **局部自治**：子 Agent 内部可以多轮推理，但只在自己的边界内自治
- **强隔离**：工具、会话、promptId、context 都有局部视图
- **强可观测**：activity、message bus、chat recording 都支持追踪
- **强约束**：禁止 Agent 互相递归调用，避免系统失控
- **可扩展**：本地、远端、浏览器三类 Agent 都走同一套委托模式

一句话概括：

> Gemini CLI 不是“多个 agent 自由聊天”，而是“一个主控 agent 通过统一工具协议，编排多个专用 agent 完成复杂任务”。

---

## 14. 推荐阅读源码

如果你想继续沿着实现看，建议按这个顺序读：

1. `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/registry.ts`
2. `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/subagent-tool.ts`
3. `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/local-executor.ts`
4. `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/core/geminiChat.ts`
5. `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/services/chatRecordingService.ts`
6. `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/remote-invocation.ts`
7. `@/Users/simonwang/project/agent/gemini-cli/packages/core/src/agents/browser/browserAgentInvocation.ts`
