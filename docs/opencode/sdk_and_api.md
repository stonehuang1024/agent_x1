# SDK / API 模块详细解读

---

# 1. 模块定位

这一篇专门解释 OpenCode 对外暴露能力的 SDK / API 设计。

核心问题是：

- OpenCode 如何把自己的 runtime 能力暴露给外部程序
- JS SDK 是怎么组织的
- 为什么会同时有 `client`、`server`、`createOpencode()` 这几层
- OpenAPI 生成的 typed SDK 在整个系统中承担什么角色
- SDK 与 CLI / server / TUI 是什么关系

核心源码包括：

- `packages/sdk/js/src/index.ts`
- `packages/sdk/js/src/client.ts`
- `packages/sdk/js/src/server.ts`
- `packages/sdk/js/src/v2/index.ts`
- `packages/sdk/js/src/v2/client.ts`
- `packages/sdk/js/src/v2/server.ts`
- `packages/sdk/js/src/gen/sdk.gen.ts`
- `packages/sdk/js/src/gen/types.gen.ts`

这一层本质上是 OpenCode 的**程序化接入面**。

---

# 2. 总体结构：SDK 不是手写业务 client，而是“手写包装 + 自动生成类型客户端”

## 2.1 最外层 API

SDK 对外主要暴露三类入口：

- `createOpencodeClient(...)`
- `createOpencodeServer(...)`
- `createOpencode(...)`

## 2.2 中间层

- `OpencodeClient`
- `createClient(...)`

这里的 `OpencodeClient` 是从生成代码中来的 typed client wrapper。

## 2.3 最底层

- `gen/sdk.gen.ts`
- `gen/client.gen.ts`
- `gen/types.gen.ts`

这些文件是从 OpenAPI 自动生成的。

所以 SDK 的架构不是“手写所有请求方法”，而是：

- 用 OpenAPI 生成底层 typed client
- 再用少量手写代码包装出更实用的入口

这是一种非常标准、也非常稳健的 **generated core + ergonomic wrapper** 设计。

---

# 3. `index.ts`：统一入口工厂

## 3.1 作用

`packages/sdk/js/src/index.ts` 做的事情非常简单：

- 导出 `client`
- 导出 `server`
- 提供 `createOpencode(options?)`

## 3.2 `createOpencode()` 的设计

它会：

1. 调用 `createOpencodeServer(...)`
2. 拿到启动后的本地 server url
3. 再调用 `createOpencodeClient({ baseUrl: server.url })`
4. 返回：
   - `client`
   - `server`

这意味着 SDK 支持一种非常方便的模式：

- 在同一个进程里直接拉起一个本地 opencode server
- 然后立刻拿到对应的 typed client

这是一个典型的 **embedded server + local client bootstrap** 模式。

对集成方来说很方便，因为它不需要自己先手动启动 CLI server。

---

# 4. `createOpencodeClient()`：客户端包装层

## 4.1 核心职责

`client.ts` 的职责很聚焦：

- 给生成 client 注入默认 fetch
- 注入额外 headers
- 返回 `OpencodeClient`

## 4.2 默认 fetch 的意义

如果调用方没有传 `fetch`，SDK 会注入一个自定义 fetch：

- 关闭请求 timeout
- 直接调用全局 `fetch(req)`

这说明 SDK 默认假设：

- 某些 API（尤其 SSE / 长轮询 / 流式接口）不适合被短 timeout 打断

因此 SDK 会主动把 fetch 行为调整到更适合 agent runtime 的模式。

## 4.3 `directory` header

v1 client 支持：

- `directory?: string`

如果传入，会写 header：

- `x-opencode-directory`

这说明 SDK 客户端允许调用方告诉 server：

- 当前想让 opencode 以哪个工作目录视角运行

这是一个很重要的多工作区/外部集成能力。

## 4.4 v2 client 的改进

`v2/client.ts` 在 v1 基础上多了：

- `experimental_workspaceID?: string`

并会设置 header：

- `x-opencode-workspace`

同时，对 `directory` 做了更细的 non-ASCII 处理：

- 如果路径包含非 ASCII，先 encodeURIComponent

这说明 v2 相比 v1，更明显考虑了：

- workspace 粒度路由
- 跨平台/非 ASCII 路径兼容

---

# 5. `createOpencodeServer()`：本地 server 启动器

## 5.1 核心功能

`server.ts` 提供的是一个“spawn opencode serve”的包装器，而不是纯 JS 内嵌 HTTP server。

流程是：

1. 组装命令行参数：
   - `serve`
   - `--hostname=...`
   - `--port=...`
   - 可选 `--log-level=...`
2. `spawn('opencode', args)`
3. 将 config JSON 写入环境变量：
   - `OPENCODE_CONFIG_CONTENT`
4. 监听 stdout，直到发现：
   - `opencode server listening on ...`
5. 解析出 URL
6. 返回 `{ url, close() }`

这说明 JS SDK 的 server 模块其实是 **CLI process orchestration wrapper**。

## 5.2 为什么不是直接 import server app 启动

这样设计的好处是：

- SDK 复用的是与用户命令行启动同一套 opencode 程序
- 避免 JS SDK 与 CLI server 逻辑分叉
- 启动路径与真实用户环境更一致

这是和 VS Code 插件“薄桥接”很一致的设计风格。

## 5.3 启动就绪检测算法

server 启动器会：

- 监听 stdout
- 当某行以 `opencode server listening` 开头
- 再用正则抓 URL

如果超时：

- 抛 `Timeout waiting for server to start`

如果进程提前退出：

- 把 stderr/stdout 拼进错误消息

这是一个很直接的 **stdout readiness probe** 实现。

优点：

- 不依赖额外握手 API
- 兼容真实 CLI 行为

## 5.4 `close()` 的语义

返回对象里的：

- `close()` -> `proc.kill()`

说明 SDK 把“server 进程生命周期”也纳入了其 API 能力边界。

---

# 6. `createOpencodeTui()`：TUI 启动器

## 6.1 作用

`createOpencodeTui()` 提供的是：

- 程序化拉起交互式 TUI

支持参数：

- `project`
- `model`
- `session`
- `agent`
- `config`

## 6.2 为什么 SDK 要提供 TUI 启动器

因为 OpenCode 不只是 HTTP API 服务，也有强交互终端产品。

有些集成方想做的不是：

- 远程调用 REST/SSE

而是：

- 从另一个程序里启动 opencode TUI 会话

因此 SDK 同时暴露 server 和 TUI 两种运行形态。

---

# 7. 生成客户端：`gen/sdk.gen.ts`

## 7.1 它是什么

这个文件是：

- `@hey-api/openapi-ts` 自动生成的 typed SDK

里面定义了大量按资源分组的方法，例如：

- `Global`
- `Project`
- `Pty`
- `Config`
- `Tool`
- `Session`
- `Provider`
- `Find`
- `File`
- `App`
- `Mcp`
- `Lsp`
- `Formatter`
- `Tui`
- `Auth`
- `Event`

这已经非常明确地说明：

- OpenCode 的对外 API 不是只有 chat 一条接口
- 它暴露的是整套 runtime 控制面

## 7.2 这意味着什么

SDK 调用方理论上可以通过 API 完成很多操作：

- 创建/查询/更新 session
- 拉消息历史
- 发 prompt
- 触发命令
- 读取 config/provider/tool 列表
- 操作 MCP
- 订阅全局事件
- 控制 TUI
- 管理 auth

也就是说，OpenCode 的 HTTP API 更像一个 **agent runtime control plane API**。

## 7.3 SSE 能力

从生成代码可以看到，例如：

- `Global.event()` -> `get.sse(...)`

这说明 SDK 直接支持 server-sent events，而不是让调用方自己拼 SSE 客户端。

对于 agent runtime 这种强事件驱动系统，这是非常关键的。

---

# 8. 类型生成：`gen/types.gen.ts`

## 8.1 作用

`types.gen.ts` 把 OpenAPI schema 映射成 TypeScript 类型。

例如可以看到：

- `EventInstallationUpdated`
- `UserMessage`
- `AssistantMessage`
- `TextPart`
- `ReasoningPart`
- `FileSource`
- `Range`
- 等等

这意味着 SDK 使用者可以在类型系统中直接拿到：

- message shape
- event shape
- part shape
- route request/response shape

## 8.2 为什么这很重要

没有类型生成的话，外部集成方会面临：

- 自己猜响应结构
- 协议升级难以发现 breaking changes
- SSE/event payload 很难安全消费

有了自动生成类型后，SDK 就成为一个可靠的契约层。

---

# 9. v1 与 v2 SDK 的差异

## 9.1 共同点

两者都提供：

- `index.ts`
- `client.ts`
- `server.ts`
- `createOpencode()`
- `createOpencodeClient()`
- `createOpencodeServer()`

## 9.2 v2 的明显增强

从已读代码看，v2 比 v1 明确多了：

- `experimental_workspaceID`
- `x-opencode-workspace` header
- non-ASCII directory 更细致的编码处理

这说明 v2 更偏向支持：

- 多 workspace
- 更复杂 IDE / server 集成场景

因此 v2 不是简单改名，而是在接入粒度上更成熟。

---

# 10. SDK 与 server 的架构关系

## 10.1 SDK 不重写 server 逻辑

JS SDK 的 server 启动器并没有把 OpenCode server 逻辑嵌进 JS 进程，而是直接：

- spawn `opencode serve`

这和 VS Code 插件的架构思想一致：

- 入口轻包装
- runtime 本体复用主程序

## 10.2 为什么这是好事

这样可以避免：

- JS SDK server 行为与 CLI server 分叉
- 两套配置解析逻辑
- 两套 provider/tool/runtime 生命周期实现

所以 SDK 只是程序化入口，而不是另一份 runtime 实现。

---

# 11. SDK 暴露的能力边界

## 11.1 控制面能力

通过 SDK/OpenAPI 可以推断，外部程序至少能控制：

- session 生命周期
- provider/config/tool 元信息
- MCP 管理
- TUI 控制
- 全局事件流
- 文件/查找接口
- PTY/命令接口

## 11.2 这意味着 OpenCode 可以被嵌入什么场景

- IDE 插件
- 桌面应用壳
- 自动化脚本
- 多租户工作台
- 内部 agent 平台
- 定制 UI 前端

也就是说，OpenCode 的 API 设计并不只是给一个 CLI 附属用，而是支持更大范围的集成。

---

# 12. `OPENCODE_CONFIG_CONTENT` 的设计意义

无论 server 启动器还是 TUI 启动器，都会通过环境变量注入：

- `OPENCODE_CONFIG_CONTENT = JSON.stringify(config)`

这说明 SDK 设计倾向于：

- 不强依赖写配置文件到磁盘
- 而是支持“进程级临时配置注入”

这对程序化集成非常友好，因为调用方可以：

- 动态拼接配置
- 启动一次临时 server/TUI
- 不污染用户本地长期配置

这是一个非常实用的 **ephemeral process config injection** 设计。

---

# 13. 这个模块背后的关键设计原则

## 13.1 契约优先

通过 OpenAPI 生成 typed client/type definitions，OpenCode 把 API 契约做成了第一等资产。

## 13.2 包装层要薄

手写 SDK 代码只做：

- 进程启动包装
- fetch/header 便利增强
- 组合工厂

复杂协议能力都交给生成代码与 server 本体。

## 13.3 统一 runtime，多个入口

JS SDK、VS Code、CLI，走的是同一个 opencode runtime，而不是三套逻辑。

## 13.4 控制面 API 不只服务 chat

OpenCode 的 API 设计明确覆盖：

- chat/runtime
- config/provider
- mcp
- tui
- events
- files/find
- pty

这说明它把自己定位成一个完整 agent platform runtime，而非单功能聊天接口。

---

# 14. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/sdk/js/src/index.ts`
2. `packages/sdk/js/src/client.ts`
3. `packages/sdk/js/src/server.ts`
4. `packages/sdk/js/src/v2/client.ts`
5. `packages/sdk/js/src/gen/sdk.gen.ts`
6. `packages/sdk/js/src/gen/types.gen.ts`
7. `packages/sdk/openapi.json`

重点盯住这些函数/概念：

- `createOpencode()`
- `createOpencodeClient()`
- `createOpencodeServer()`
- `createOpencodeTui()`
- `OpencodeClient`
- `Global.event()`
- `Session.*`
- `Tui.*`
- `Mcp.*`
- `EventSubscribe*`

---

# 15. 下一步还需要深挖的问题

这一篇已经把 SDK 主框架讲清楚了，但还有一些地方值得继续拆：

- **问题 1**：`packages/sdk/openapi.json` 与 server route 的生成链路是什么，OpenAPI 是如何维护的
- **问题 2**：`gen/client.gen.ts` 的请求层封装、错误处理与 SSE 行为细节还可继续展开
- **问题 3**：v1 与 v2 API 在 schema 和路由层的真实差异还有哪些，不只是 header 层增强
- **问题 4**：`OpencodeClient` 在生成代码中对资源分组、命名空间和泛型错误处理的设计可以继续细读
- **问题 5**：SDK 如果嵌入长生命周期应用，server 进程生命周期、日志采集、异常恢复如何更稳妥地管理
- **问题 6**：`x-opencode-directory` / `x-opencode-workspace` 在 server 端的解析逻辑和优先级边界是什么
- **问题 7**：TUI 控制接口（append prompt、submit prompt、toast、control response 等）背后的协议如何与前端/终端状态同步
- **问题 8**：事件流 API 是否足够支撑构建完整外部 UI，是否还需要额外 query/replay 端点协助状态重建

---

# 16. 小结

`sdk_and_api` 模块展示了 OpenCode 如何把内部 agent runtime 对外产品化：

- 用 OpenAPI 生成 typed client 与类型系统
- 用少量手写包装提供更好用的 `client/server/tui` 入口
- 用进程启动器复用同一套 opencode 主程序
- 用 headers 与配置注入支持目录/workspace/临时配置等程序化集成场景

因此，OpenCode 的 SDK 不是“给 HTTP 接口套一层壳”，而是它成为可嵌入 agent runtime 平台的重要一环。
