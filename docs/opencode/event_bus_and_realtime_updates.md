# Event Bus / Realtime Updates 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的事件总线与实时更新链路。

核心问题是：

- `Bus` 和 `GlobalBus` 分别负责什么
- `BusEvent.define()` 为什么重要
- session/message/permission 等事件是如何发布出去的
- SSE 接口如何把内部事件暴露给外部客户端
- TUI、插件、HTTP 客户端如何共享同一套事件流基础设施

核心源码包括：

- `packages/opencode/src/bus/bus-event.ts`
- `packages/opencode/src/bus/index.ts`
- `packages/opencode/src/bus/global.ts`
- `packages/opencode/src/server/routes/global.ts`
- `packages/opencode/src/session/*`
- `packages/opencode/src/plugin/index.ts`

这一层本质上是 OpenCode 的**状态传播与实时同步基础设施**。

---

# 2. 为什么 OpenCode 需要事件总线

OpenCode 不是一次请求一次响应的纯同步程序。

它存在大量异步、增量、跨模块状态变化：

- session 创建/更新/删除
- message/part 增量写入
- tool call 状态变化
- permission ask/reply
- diff / revert / todo 更新
- server dispose
- plugin 错误
- LSP 状态更新

如果所有模块都直接互相回调，会导致：

- 强耦合
- UI 很难实时同步
- 插件难以观察系统行为
- 外部 SSE 很难统一接出

因此事件总线是必需的中介层。

---

# 3. `BusEvent.define()`：类型化事件定义注册器

## 3.1 结构

`BusEvent.define(type, properties)` 返回：

- `type`
- `properties`

并注册到内部 `registry`。

这里的 `properties` 是一个 zod schema。

## 3.2 为什么它重要

这意味着事件不是随手写字符串，而是：

- 有名字
- 有结构 schema
- 可被统一收集

这对 OpenCode 非常关键，因为这些事件不仅在进程内传播，还会被：

- SSE 暴露
- OpenAPI schema 引用
- 插件消费

## 3.3 `payloads()`

`BusEvent.payloads()` 会把 registry 里的所有事件组装成：

- `discriminatedUnion("type", ...)`

这一步非常重要，因为它把“事件定义表”变成了：

- 可序列化、可验证、可文档化的统一事件 payload schema

这正是 SSE route 能把全局事件以强类型 schema 暴露出去的基础。

---

# 4. `Bus`：实例级事件总线

## 4.1 作用域

`Bus` 使用 `Instance.state(...)` 保存 subscriptions。

这说明 `Bus` 是：

- **实例/工作目录作用域的事件总线**

不同 instance 的 bus 订阅彼此隔离。

## 4.2 内部状态

核心状态非常简单：

- `subscriptions: Map<any, Subscription[]>`

键可以是：

- 具体事件类型
- `*` 通配订阅

这是一个很轻量但足够实用的本地事件分发器。

---

# 5. `Bus.publish()`：事件传播算法

`publish(def, properties)` 的流程是：

1. 构造 payload：
   - `{ type, properties }`
2. 记录日志
3. 同时通知：
   - 该具体 type 的订阅者
   - `*` 通配订阅者
4. 再把事件转发给 `GlobalBus.emit("event", { directory, payload })`

## 5.1 最关键的语义

这说明 `Bus` 并不是纯本地终点。

它还承担：

- **实例级事件 -> 全局事件桥接**

也就是说，所有 instance 内事件最后都能向上汇聚到全局事件流。

这正是 OpenCode 同时支持：

- instance 内部响应
- 全局 SSE 广播

的关键设计。

---

# 6. `subscribe()` / `subscribeAll()` / `once()`

## 6.1 `subscribe(def, callback)`

只监听某个具体事件类型。

## 6.2 `subscribeAll(callback)`

监听所有事件。

这对：

- 插件
- 调试器
- 事件镜像器

特别有用。

## 6.3 `once()`

监听一次，直到 callback 返回 `done`。

说明 Bus 还兼顾了某些等待单次状态变化的控制流需求。

---

# 7. `InstanceDisposed`：实例生命周期结束事件

`Bus` 内定义了：

- `server.instance.disposed`

并且在 `Instance.state(..., cleanup)` 的清理回调中，会把该事件发送给 wildcard 订阅者。

这说明即使 instance 被销毁，Bus 也尽量给订阅方一个结束信号。

这类细节对 UI、长连接消费者、插件都很重要。

---

# 8. `GlobalBus`：跨实例全局汇聚器

`GlobalBus` 非常简单，就是一个 Node `EventEmitter`：

- event: [{ directory?, payload }]

## 8.1 为什么还需要它

如果只有实例级 `Bus`：

- 无法很方便地做全局 SSE
- 多 workspace / 多 instance 事件没法统一观察
- `global.dispose` 之类的事件也没有统一出口

因此 `GlobalBus` 的职责是：

- **聚合所有 instance 事件，形成全局广播总线**

## 8.2 `directory`

全局事件包裹了一层：

- `directory`
- `payload`

这让消费者知道：

- 这个事件来自哪个实例目录

对多工作区场景尤其重要。

---

# 9. `GlobalRoutes /event`：SSE 暴露层

这是外部实时消费事件的主入口。

## 9.1 SSE 建立时

`/global/event` 会：

- 设置 `X-Accel-Buffering: no`
- 设置 `X-Content-Type-Options: nosniff`
- `streamSSE(...)`

这说明它明确针对代理缓冲与流式行为做了处理。

## 9.2 初始连接事件

连接建立后立刻发送：

- `server.connected`

作用是：

- 让客户端确认流已建立
- 不必等真实业务事件出现

## 9.3 心跳

每 10 秒发送：

- `server.heartbeat`

目的是：

- 防止代理/中间层把空闲流断开
- 让前端知道连接还活着

## 9.4 真实事件转发

通过：

- `GlobalBus.on("event", handler)`

把全局事件逐条写入 SSE。

## 9.5 中断处理

`stream.onAbort()` 时会：

- 清 heartbeat
- `GlobalBus.off(...)`
- 结束等待

这说明 SSE 生命周期收尾是完整的，没有遗留订阅泄漏。

---

# 10. 为什么 SSE 绑定到 `GlobalBus` 而不是直接绑定各模块

如果 SSE route 直接订阅所有 session/message/permission 模块，会导致：

- route 知道太多内部细节
- 新增事件类型需要改 route
- 多实例事件难统一

现在通过：

- 模块 -> Bus.publish()
- Bus -> GlobalBus.emit()
- GlobalRoutes -> SSE

形成分层链路，职责就很清楚：

- 业务模块负责发事件
- Bus 负责实例级分发与上送
- GlobalBus 负责聚合
- SSE route 负责外部传输

这是很好的分层设计。

---

# 11. 事件是如何从业务模块发出的

从 grep 结果可见，事件广泛散落在各核心模块。

## 11.1 session 相关

`Session.Event` 至少定义了：

- `session.created`
- `session.updated`
- `session.deleted`
- `session.diff`
- `session.error`

这些事件覆盖会话生命周期、diff 摘要和错误状态。

## 11.2 message / part 相关

在 revert/cleanup 等路径中会发：

- `MessageV2.Event.Removed`
- `MessageV2.Event.PartRemoved`

而 processor 中 message/part 的增量写入也会经过相应更新事件链。

这说明消息系统天然是事件驱动的。

## 11.3 permission 相关

之前已确认 permission 系统会发布：

- `permission.asked`
- `permission.replied`

这使 UI/客户端能实时显示审批请求与审批结果。

## 11.4 todo / diff / error 相关

例如：

- `Todo.Event.Updated`
- `Session.Event.Diff`
- `Session.Event.Error`

也都通过 Bus 传播。

---

# 12. processor 为什么高度依赖事件链路

`session/processor.ts` 是最典型的事件密集型模块。

它会在这些时刻触发状态变化：

- reasoning part 生成
- tool part start/update/end
- step-start / step-finish
- patch 生成
- session error
- summary/diff 更新

这些变化不是最终一次性写好，而是流式、增量、跨阶段发生。

因此如果没有事件总线：

- UI 就只能轮询数据库
- 插件无法实时观测
- SSE 无法实时推送

事件总线在这里承担的是：

- **流式状态传播层**

---

# 13. 插件为什么依赖 `subscribeAll()`

`Plugin.init()` 中：

- `Bus.subscribeAll(async (input) => hook.event?.({ event: input }))`

这说明插件系统把总线事件视为宿主对外公开的一类 runtime signal。

插件因此可以：

- 做日志/审计
- 做指标采集
- 做状态同步
- 做第三方桥接

也就是说，Bus 事件流本身就是一种扩展 API。

---

# 14. `GlobalDisposedEvent`：全局控制事件

在 `/global/dispose` 中，除了 `Instance.disposeAll()` 之外，还会显式发：

- `global.disposed`

并且其 `directory` 标为：

- `global`

这说明全局控制事件与普通实例事件被统一放进同一 SSE 通道，只是作用域不同。

这样客户端只要订阅一个 SSE 流，就能收到：

- 实例业务事件
- 全局控制事件

---

# 15. 事件 schema 与 OpenAPI 的关系

`/global/event` 的 response schema使用：

- `BusEvent.payloads()`

这非常关键，因为它表明事件系统不是“运行时才知道有哪些事件”。

相反，它们已经被统一注册，并能生成 OpenAPI schema。

这带来的好处是：

- SDK 可以有类型
- 第三方客户端知道合法事件结构
- 文档与实现一致

这是 OpenCode 事件系统很成熟的一点。

---

# 16. `Bus` vs `GlobalBus` 的职责边界

可以这样理解：

## 16.1 `Bus`

负责：

- 实例内订阅/发布
- 事件本地消费
- 向全局上送事件

## 16.2 `GlobalBus`

负责：

- 接收各实例上送事件
- 为全局 SSE/聚合观察者提供统一事件入口

因此两者不是重复实现，而是上下两层：

- **local bus + global aggregation bus**

---

# 17. 这个模块背后的关键设计原则

## 17.1 事件先定义 schema，再进入系统

`BusEvent.define()` 保证事件有正式契约，而不是散乱字符串。

## 17.2 实例内变化与全局广播应解耦

`Bus` 与 `GlobalBus` 分层正是为了解耦本地响应和对外广播。

## 17.3 流式产品必须有统一事件主干

session、permission、message、todo、plugin error 等都走一条事件主干，系统才能稳定扩展。

## 17.4 SSE 是传输层，不是业务层

真正的业务语义都在事件定义与发布点，SSE 只是把它们送出进程。

---

# 18. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/bus/bus-event.ts`
2. `packages/opencode/src/bus/index.ts`
3. `packages/opencode/src/bus/global.ts`
4. `packages/opencode/src/server/routes/global.ts`
5. `packages/opencode/src/session/processor.ts`
6. `packages/opencode/src/session/index.ts`
7. `packages/opencode/src/permission/next.ts`

重点盯住这些函数/概念：

- `BusEvent.define()`
- `BusEvent.payloads()`
- `Bus.publish()`
- `Bus.subscribe()`
- `Bus.subscribeAll()`
- `GlobalBus.emit()`
- `streamSSE()`
- `server.connected`
- `server.heartbeat`
- `global.disposed`

---

# 19. 下一步还需要深挖的问题

这一篇已经把事件主干讲清楚了，但还有一些地方值得继续展开：

- **问题 1**：`MessageV2.Event.*` 的完整事件集合与各自触发点还可以继续系统梳理
- **问题 2**：session/message 更新事件在前端/TUI 中的具体消费路径还值得继续追踪
- **问题 3**：`GlobalBus` 是否需要背压、订阅上限或监听器泄漏保护，还可继续评估
- **问题 4**：SSE 重连后客户端如何补齐断线期间事件，目前是否完全依赖重新拉取状态
- **问题 5**：Bus 事件是否可能因为高频 part 更新而形成热点，是否存在节流/批量化策略
- **问题 6**：多 instance 并发下，`directory` 是否足够作为全局事件源标识，还可进一步确认
- **问题 7**：插件 `event()` hook 的失败隔离与异常传播边界还值得继续分析
- **问题 8**：除 SSE 外，是否还有 WebSocket 或 TUI 专用事件桥接层值得单独拆解

---

# 20. 小结

`event_bus_and_realtime_updates` 模块定义了 OpenCode 如何把内部状态变化稳定传播给本地模块、插件和外部客户端：

- `BusEvent` 负责事件 schema 定义与统一注册
- `Bus` 负责实例内分发，并把事件桥接到全局
- `GlobalBus` 负责跨实例聚合
- `/global/event` 则通过 SSE 把这些事件实时推送出去

因此，这一层不是简单日志流，而是 OpenCode 实时 UI、插件生态和外部控制面共享的事件主干。
