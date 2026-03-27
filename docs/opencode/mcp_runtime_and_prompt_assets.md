# MCP Runtime / Prompt Assets 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 中 MCP 的 runtime 接入、prompt/resource/tool 资产模型，以及 server 路由管理。

核心问题是：

- MCP server 在 OpenCode 中如何连接和维护
- MCP tool、prompt、resource 三类资产是如何暴露出来的
- MCP 为什么既属于 runtime 工具来源，又属于外部受管资源
- OAuth 型 MCP server 是如何接入的
- MCP 资产如何被 command、tool、SDK route 和事件系统共同消费

核心源码包括：

- `packages/opencode/src/mcp/index.ts`
- `packages/opencode/src/server/routes/mcp.ts`
- `packages/opencode/src/mcp/auth.ts`
- `packages/opencode/src/mcp/oauth-provider.ts`
- `packages/opencode/src/mcp/oauth-callback.ts`

这一层本质上是 OpenCode 的**外部能力接入总线与协议资产桥接层**。

---

# 2. MCP 在 OpenCode 里的真正定位

很多系统把 MCP 只当作“外部工具来源”。

但 OpenCode 里的 MCP 明显更丰富，它至少有三类资产：

- tools
- prompts
- resources

并且它们分别接入了不同上游：

- tools -> tool runtime
- prompts -> command 系统
- resources -> 可读资源资产

所以 MCP 在 OpenCode 中不是单一工具插件，而是：

- **统一接入外部上下文能力的协议层**

---

# 3. `MCP.Status`：连接状态模型

`MCP.Status` 是一个 discriminated union，支持：

- `connected`
- `disabled`
- `failed`
- `needs_auth`
- `needs_client_registration`

这说明 OpenCode 把 MCP server 视为长期连接资源，而不是一次性调用对象。

## 3.1 为什么状态模型很重要

因为 MCP server 的可用性可能受多种因素影响：

- 配置禁用
- 连接失败
- 需要 OAuth
- 客户端注册未完成

因此必须有正式的状态机，而不是简单布尔值。

---

# 4. `MCP.state()`：按 instance 维护 MCP client 池

MCP 使用：

- `Instance.state(...)`

维护状态，其中核心包括：

- `clients: Record<string, Client>`
- `status: Record<string, Status>`

这说明 MCP 连接池也是 instance-scoped 的，而不是全局单例。

## 4.1 初始化流程

state 初始化时会：

1. 读取 `cfg.mcp`
2. 遍历每个配置条目
3. 跳过 disabled 项
4. 调 `create(key, mcp)` 建立连接
5. 保存 status
6. 若成功，保存 `mcpClient`

这说明 MCP server 是在 instance 初始化阶段整体装配的。

---

# 5. MCP 配置项与 `isMcpConfigured()`

`cfg.mcp` 中的条目不一定都是完整配置对象，因此有：

- `isMcpConfigured(entry)`

用来判断是否真的是合法 MCP 配置。

这说明配置层可能允许 disabled/占位项，而 runtime 会先做一层结构收缩，再进入连接逻辑。

---

# 6. 连接传输层：stdio / SSE / streamable HTTP

从 imports 可以直接看出 MCP 支持多种 transport：

- `StdioClientTransport`
- `SSEClientTransport`
- `StreamableHTTPClientTransport`

这说明 OpenCode 对 MCP server 的接入并不局限于本地命令进程，也支持远程 HTTP/SSE 型 server。

这正是 MCP 作为协议层的关键价值之一：

- 统一本地与远程能力源

---

# 7. `convertMcpTool()`：MCP tool 到 AI SDK tool 的桥接

这是 MCP 接入 runtime 的最关键桥梁之一。

## 7.1 输入

- `MCPToolDef`
- `client`
- `timeout?`

## 7.2 处理逻辑

1. 拿到 `mcpTool.inputSchema`
2. 强制构造成 JSONSchema object
3. `dynamicTool({...})`
4. 执行时调用：
   - `client.callTool({ name, arguments }, CallToolResultSchema, { timeout, resetTimeoutOnProgress: true })`

## 7.3 为什么重要

这说明 OpenCode 并没有给 MCP tool 单独做另一套工具 runtime。

而是把 MCP tool 转成与内置工具同构的 AI SDK tool。

也就是说，MCP tools 在模型视角里本质上和本地工具是同类对象。

---

# 8. `ToolsChanged`：MCP 动态工具列表事件

`MCP.ToolsChanged` 定义了：

- `mcp.tools.changed`

并且在 `registerNotificationHandlers()` 中，收到 MCP 协议的：

- `ToolListChangedNotificationSchema`

后会：

- `Bus.publish(ToolsChanged, { server })`

这说明 OpenCode 支持 MCP server 动态变更工具集，并通过内部事件系统把这个变化传播出去。

这很关键，因为某些 MCP server 的工具是动态生成或按授权状态变化的。

---

# 9. prompt 资产：MCP prompt 如何进入 command 系统

## 9.1 `fetchPromptsForClient()`

它会：

- `client.listPrompts()`
- 对每个 prompt 做 key 规范化：
  - `sanitizedClientName:sanitizedPromptName`
- 返回 `prompt + client` 信息

## 9.2 `getPrompt(clientName, name, args?)`

后续可通过：

- `client.getPrompt({ name, arguments })`

拿到 prompt 具体内容。

## 9.3 与 command 系统的关系

前面已经读到 `Command.state()` 会调用：

- `MCP.prompts()`
- `MCP.getPrompt(...)`

这意味着 MCP prompt 会被提升成 command 模板资产。

因此 MCP prompt 在 OpenCode 里并不是隐藏协议对象，而是用户可直接触发的高层任务入口。

---

# 10. resource 资产：MCP resource 如何进入可读上下文

## 10.1 `Resource` 类型

`MCP.Resource` 包括：

- `name`
- `uri`
- `description?`
- `mimeType?`
- `client`

说明 resource 被建模成一等只读资产。

## 10.2 `fetchResourcesForClient()`

会：

- `client.listResources()`
- 同样做 client/resource 名字规范化
- 返回 `resource + client`

## 10.3 `readResource(clientName, resourceUri)`

则通过：

- `client.readResource({ uri })`

取回实际资源内容。

这意味着 MCP resource 可以作为：

- 外部知识源
- 外部文档源
- 外部结构化数据源

进入 OpenCode。

---

# 11. 命名规范化：为什么要 sanitize client/prompt/resource 名称

无论 prompt 还是 resource，OpenCode 都会把：

- clientName
- prompt/resource name

清洗成只含：

- `[a-zA-Z0-9_-]`

然后再拼 key。

这样做的好处是：

- 可安全作为 command/tool/resource registry key
- 避免特殊字符破坏 UI、schema 或路由逻辑

这是很典型的协议资产内部规范化步骤。

---

# 12. OAuth 流程：远程 MCP server 的认证接入

MCP 的 OAuth 支持是这套系统最复杂也最成熟的部分之一。

## 12.1 `supportsOAuth(mcpName)`

只有当：

- mcp 配置存在
- 是合法配置
- `type === "remote"`
- `oauth !== false`

时，才认为该 server 支持 OAuth。

这说明 OAuth 是 remote MCP server 的正式能力，而不是所有 transport 都支持。

## 12.2 `startAuth(mcpName)`

流程大致是：

1. 读取 MCP config
2. 检查 remote / oauth enable
3. 启动回调 server：
   - `McpOAuthCallback.ensureRunning()`
4. 生成安全随机 `state`
5. 存入 `McpAuth.updateOAuthState(...)`
6. 创建 `McpOAuthProvider`
7. 创建带 authProvider 的 `StreamableHTTPClientTransport`
8. 尝试 connect
9. 若抛 `UnauthorizedError` 且捕获到 redirect URL
   - 把 transport 放进 `pendingOAuthTransports`
   - 返回 `authorizationUrl`

这是一套完整的 OAuth PKCE / callback 风格启动流程。

---

# 13. `authenticate()`：浏览器打开与 callback 等待

## 13.1 流程

`authenticate(mcpName)` 会：

1. 调 `startAuth()`
2. 若已认证，则直接返回现状
3. 读取已保存的 `oauthState`
4. 先注册 callback wait：
   - `McpOAuthCallback.waitForCallback(oauthState)`
5. 再尝试 `open(authorizationUrl)` 打开浏览器
6. 如果浏览器打开失败：
   - 发布 `mcp.browser.open.failed`
7. 等待 callback code
8. 校验 state 防 CSRF
9. `finishAuth(mcpName, code)`

## 13.2 为什么先注册 callback 再开浏览器

源码注释明确说明是为了避免 race condition：

- 如果 IdP 已有 SSO session，可能瞬间跳回
- 若此时还没开始等待 callback，就会丢事件

这是一个非常到位的工程细节。

---

# 14. `finishAuth()`：完成 OAuth 并重建连接

流程是：

1. 从 `pendingOAuthTransports` 取 transport
2. `transport.finishAuth(code)`
3. 清 code verifier
4. 重新读取 MCP config
5. `add(mcpName, mcpConfig)`
6. 返回新的 status

这说明 OAuth 成功后，OpenCode 并不是把 token 塞进当前 client 后就结束，而是：

- **重新走一遍 add/connect 流程，建立正式 client**

这样状态机会更清晰，也便于统一后续逻辑。

---

# 15. `removeAuth()` / `getAuthStatus()`

MCP OAuth 管理还提供：

- `removeAuth(mcpName)`
- `hasStoredTokens(mcpName)`
- `getAuthStatus(mcpName)`

其中 `getAuthStatus()` 可返回：

- `authenticated`
- `expired`
- `not_authenticated`

这说明 MCP auth 并不是一次性流程，而是正式受管凭据资产。

---

# 16. 路由层：`McpRoutes`

`server/routes/mcp.ts` 把 MCP 控制面正式暴露成 HTTP 接口：

- `GET /mcp/` -> status
- `POST /mcp/` -> add server
- `POST /mcp/:name/auth` -> start OAuth
- `POST /mcp/:name/auth/callback` -> finish auth
- `POST /mcp/:name/auth/authenticate` -> start + open browser + wait callback
- `DELETE /mcp/:name/auth` -> remove auth
- `POST /mcp/:name/connect` -> connect
- `POST /mcp/:name/disconnect` -> disconnect

这说明 MCP 在 OpenCode 中不仅存在于内部 runtime，也是一类对外可编排的资源。

---

# 17. MCP 与事件系统的关系

除了 `mcp.tools.changed` 外，还有：

- `mcp.browser.open.failed`

表明 MCP 模块与 Bus 系统深度整合。

这意味着 MCP 的动态变化、错误与交互需求都能进入：

- TUI
- SSE
- 插件
- 外部客户端

共享的事件主干。

---

# 18. MCP 与 command / tool / resource 三条链路的关系

可以把 MCP 在 OpenCode 中的接入分成三条平行链路：

## 18.1 MCP Tool

- `listTools`
- `convertMcpTool()`
- 进入 tool runtime

## 18.2 MCP Prompt

- `listPrompts`
- `getPrompt()`
- 进入 command/template 系统

## 18.3 MCP Resource

- `listResources`
- `readResource()`
- 进入可读资源/上下文资产系统

这种三分结构非常重要，因为它说明 OpenCode 充分利用了 MCP 协议的不同资产类型，而不是只拿它来跑工具。

---

# 19. instance 生命周期与 MCP client 清理

`MCP.state(..., dispose)` 的清理逻辑还专门处理：

- 递归杀子进程/孙进程
- 再关闭 client
- 清空 pending OAuth transports

源码注释甚至点名了像 `chrome-devtools-mcp` 这类会再派生子进程的 server。

这说明 OpenCode 在 MCP runtime 管理上考虑到了：

- 长生命周期子进程
- 孤儿进程
- transport close 不彻底

这些真实世界问题。

---

# 20. 这个模块背后的关键设计原则

## 20.1 MCP 不应只被视作工具来源

prompt、resource、tool 三类资产都应被完整接入。

## 20.2 协议资产应映射到宿主已有抽象

- MCP tool -> Tool
- MCP prompt -> Command
- MCP resource -> Resource reader

这样宿主架构能保持统一。

## 20.3 OAuth 远程 server 应作为正式生命周期资源管理

而不是让外部浏览器流程散落在 UI 代码里。

## 20.4 事件化处理动态变化

工具列表变化、浏览器打开失败等都通过 Bus 暴露，这让 MCP 真正成为平台级能力。

---

# 21. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/mcp/index.ts`
2. `packages/opencode/src/server/routes/mcp.ts`
3. `packages/opencode/src/mcp/auth.ts`
4. `packages/opencode/src/mcp/oauth-provider.ts`
5. `packages/opencode/src/mcp/oauth-callback.ts`

重点盯住这些函数/概念：

- `convertMcpTool()`
- `fetchPromptsForClient()`
- `fetchResourcesForClient()`
- `getPrompt()`
- `readResource()`
- `startAuth()`
- `authenticate()`
- `finishAuth()`
- `ToolsChanged`
- `BrowserOpenFailed`

---

# 22. 下一步还需要深挖的问题

这一篇已经把 MCP runtime 与资产接入主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`create()`、`clients()`、`prompts()`、`resources()` 等中段实现的完整缓存/刷新策略还可继续精读
- **问题 2**：MCP tool 返回结果与 OpenCode tool output 格式之间是否还存在额外包装层，还值得继续追踪
- **问题 3**：`McpAuth` 的 token 存储、过期刷新和 code verifier 管理还适合单独拆专题
- **问题 4**：prompt/resource 名字规范化后的冲突处理策略是否足够稳健，还可继续分析
- **问题 5**：多个 MCP server 同时暴露大量工具/资源时，对 prompt 上下文和 tool schema 的体积影响还值得评估
- **问题 6**：MCP remote transport 的错误恢复、断线重连和 timeout 策略还可继续精读
- **问题 7**：MCP 与 workspace/control-plane 的远程模型是否会进一步融合，还值得观察设计演进
- **问题 8**：命令系统中如何呈现 MCP prompts 的参数提示、错误提示和来源信息，还可以继续查 UI 代码

---

# 23. 小结

`mcp_runtime_and_prompt_assets` 模块定义了 OpenCode 如何把 MCP server 作为一类正式外部能力源接入系统：

- `MCP` 负责连接管理、状态跟踪、OAuth 流程和 client 生命周期
- MCP tools 被转换为标准 runtime tools
- MCP prompts 被提升为 command/template 资产
- MCP resources 被纳入可读外部资源体系
- `McpRoutes` 则把这些 server 作为受管资源暴露给外部控制面

因此，这一层不是简单的 MCP 工具桥接，而是 OpenCode 对外部上下文协议生态的完整资产接入基础设施。
