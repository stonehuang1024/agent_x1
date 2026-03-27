# Permission Evaluation / User Approval Flow 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的权限求值与用户审批链路。

核心问题是：

- 为什么代码里同时存在 `PermissionNext` 和旧的 `Permission`
- `allow / deny / ask` 三态规则如何求值
- `ask()` 为什么既可能立刻放行，也可能挂起等待用户回复
- `always` 批准如何影响同 session 的其他 pending request
- `external_directory`、`doom_loop`、`task`、`bash`、`question` 等权限如何串进工具执行流

核心源码包括：

- `packages/opencode/src/permission/next.ts`
- `packages/opencode/src/permission/index.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/tool/bash.ts`
- `packages/opencode/src/tool/task.ts`
- `packages/opencode/src/tool/external-directory.ts`

这一层本质上是 OpenCode 的**运行时授权决策、用户审批挂起与规则持久化基础设施**。

---

# 2. 为什么权限系统不能只有布尔 allow/deny

agentic runtime 中很多动作都不是“绝对允许”或“绝对拒绝”。

例如：

- 读普通源码文件通常应允许
- 读 `.env` 可能要 ask
- 写外部目录通常要 ask
- 某些危险路径应 deny
- bash 的某一类命令可在本次允许，但不一定要永久允许

所以 OpenCode 采用三态：

- `allow`
- `deny`
- `ask`

这也是 agent 系统里最合理的权限基础模型。

---

# 3. 为什么有 `PermissionNext` 和旧 `Permission`

从代码状态看：

- `PermissionNext` 是当前主路径
- 旧 `Permission` 仍保留在部分地方，带 `permission.ask` plugin hook 等逻辑

## 3.1 `PermissionNext` 的特点

- 规则集显式化
- `permission/pattern/action` 模型清晰
- 支持 project-level approved rules 持久化表
- 与 session/tool/runtime 深度整合

## 3.2 旧 `Permission`

更偏早期 session-scoped pending/approved map 设计。

这说明代码库正处在权限系统迁移后的“新主路径 + 旧兼容残留”阶段。

---

# 4. `PermissionNext.Rule`：权限规则的最小单位

规则结构是：

- `permission`
- `pattern`
- `action`

例如：

- `permission = read`
- `pattern = *.env`
- `action = ask`

或者：

- `permission = task`
- `pattern = *`
- `action = deny`

这说明权限系统统一把各种动作抽象成：

- 对某种 permission 名称，在某个 pattern 范围内采取某个 action

---

# 5. `fromConfig()`：配置文件如何转成规则集

`PermissionNext.fromConfig(permission)` 会把：

- 字符串值 -> `pattern = "*"` 的规则
- 对象值 -> 各 pattern/action 展开成多条规则

并做 `~/`、`$HOME` 展开。

## 5.1 含义

权限配置语法对用户是简洁的，但运行时会被正规化成统一 ruleset。

这让后续 `evaluate()` 和 `ask()` 实现很简单。

---

# 6. `merge()`：权限组合非常朴素

`PermissionNext.merge(...rulesets)` 只是：

- `rulesets.flat()`

也就是说它不做去重、不做优先级计算，而是把顺序保留下来，后续由 `evaluate()` 的 `findLast()` 决定最终生效规则。

## 6.1 这很关键

权限优先级其实来自：

- 规则数组的合并顺序
- `findLast()` 的最后匹配胜出

所以“谁后 merge，谁优先级更高”是系统重要隐含语义。

---

# 7. `evaluate()`：最后匹配规则胜出

`PermissionNext.evaluate(permission, pattern, ...rulesets)` 会：

1. 合并所有 ruleset
2. 找 `findLast(...)`
3. 条件是：
   - `Wildcard.match(permission, rule.permission)`
   - `Wildcard.match(pattern, rule.pattern)`
4. 若没有命中，则默认：
   - `{ action: "ask", permission, pattern: "*" }`

## 7.1 含义

默认值不是 allow，而是 ask。

这使得系统在规则未覆盖时倾向安全审批，而不是默认放权。

---

# 8. 为什么默认是 `ask`

在 agentic coding 系统里，最危险的状态就是：

- 新能力未配置时被默认放行

OpenCode 这里选择：

- 没匹配到规则 -> ask

这是一条非常重要的安全默认值。

---

# 9. `ask()`：权限请求的三种可能结果

`PermissionNext.ask(...)` 对每个 `pattern` 做求值，可能出现：

## 9.1 `deny`

直接抛：

- `DeniedError`

## 9.2 `allow`

继续检查下一个 pattern，最后直接 return

## 9.3 `ask`

创建 pending request：

- 放进 `state.pending`
- `Bus.publish(permission.asked, info)`
- 返回一个挂起 Promise，等待 reply

这说明 ask() 本质上是：

- **同步求值 + 异步人工审批挂起点**

---

# 10. `PermissionNext.Request`

一个权限请求包含：

- `id`
- `sessionID`
- `permission`
- `patterns[]`
- `metadata`
- `always[]`
- `tool? { messageID, callID }`

## 10.1 为什么要有 `always[]`

因为用户不仅可以批准这一次，还可能说：

- 对这类模式以后都自动允许

所以 request 必须携带“若选择 always，应该写入哪些持久规则”的候选模式。

---

# 11. `reply()`：once / always / reject 的区别

`PermissionNext.reply()` 支持：

- `once`
- `always`
- `reject`

## 11.1 `once`

- 只 resolve 当前 pending request

## 11.2 `always`

- 将 `existing.info.always` 中每个 pattern 写入 `s.approved`
- resolve 当前 request
- 然后扫描同一 session 的其他 pending request
- 若它们现在也都变成 allow，则自动 resolve

## 11.3 `reject`

- reject 当前 request
- 并 reject 同一 session 中所有其他 pending request

这套语义非常完整。

---

# 12. 为什么 `reject` 要清空同 session 其他 pending request

如果用户已经对当前会话中的一个权限请求明确拒绝，继续保留同 session 其他 pending ask 往往没有意义，反而会造成：

- UI 残留
- agent 逻辑卡死
- 拒绝后仍不断追问

所以系统直接把同 session 其他 pending 也 reject 掉，是一个很实用的收敛策略。

---

# 13. 为什么 `always` 会自动解开其他 pending request

当用户对一类模式选择 always，本质上是在写入新 allow rule。

因此同一 session 中其它仍在等待的请求，如果现在已被这条新规则覆盖，就应该自动通过。

这能显著减少重复审批噪音。

---

# 14. `approved` 的持久化现状

`PermissionNext.state()` 会从 `PermissionTable` 读已存规则：

- `approved: stored`

但 `reply(always)` 里有注释：

- TODO: we don't save the permission ruleset to disk yet until there's UI to manage it

## 14.1 含义

当前设计已经有持久化表与读取逻辑，但写回持久化仍未完全启用。

说明系统在“持久审批规则”的产品面上尚未 fully closed-loop。

---

# 15. `disabled()`：为什么需要单独的工具禁用推导

`PermissionNext.disabled(tools, ruleset)` 会：

- 对 edit 家族工具统一映射到 `edit` permission
- 若某 permission 在 ruleset 里最后命中的是 `pattern="*" && action="deny"`
- 则把该工具加入 disabled set

## 15.1 意义

这不是审批时再判断，而是在把工具暴露给模型前，就先把整类明确禁用的工具剔掉。

这能减少模型产生无效 tool call。

---

# 16. `resolveTools()` 如何调用权限系统

在 `SessionPrompt.resolveTools()` 里，每个工具的 `Tool.Context.ask()` 都会走：

- `PermissionNext.ask({... ruleset: PermissionNext.merge(agent.permission, session.permission ?? []) })`

并附带：

- `tool: { messageID, callID }`

这说明工具审批的权威规则集来自：

- agent permission
- session permission

而不是某个工具自己决定。

---

# 17. agent permission 与 session permission 的叠加

在大量路径中都能看到：

- `PermissionNext.merge(agent.permission, session.permission ?? [])`

因为 `evaluate()` 是 `findLast()`，所以后 merge 的 session.permission 实际上可以覆盖 agent 默认权限。

这就是 session 级临时授权/禁止生效的关键机制。

---

# 18. `task` 权限如何介入子代理委派

在 `TaskTool` 中：

- caller 侧会基于 `PermissionNext.evaluate("task", a.name, caller.permission)` 过滤可见 subagents
- 真正执行前还会 `ctx.ask({ permission: "task", patterns: [subagent_type], ... })`

这说明 task delegation 有两道门：

- 工具定义暴露层过滤
- 运行时执行审批层 ask

这是非常稳的双重控制。

---

# 19. `external_directory`：越界访问审批的统一权限名

多个地方都使用：

- `permission: "external_directory"`

例如：

- `tool/external-directory.ts`
- `tool/bash.ts`
- 文件读取/编辑相关链路

## 19.1 含义

系统把“访问当前实例边界外目录”抽象成独立权限，而不是散落在每个工具里各搞一套。

这是边界控制设计的一致性体现。

---

# 20. `doom_loop`：为什么也走权限系统

在 `SessionProcessor.process()` 中，如果检测到连续三次：

- 同一 tool
- 同一输入
- 非 pending

就会：

- `PermissionNext.ask({ permission: "doom_loop", patterns: [toolName], ... })`

## 20.1 这很有意思

“是否允许继续潜在死循环”被建模成权限问题，而不是纯内部 heuristic stop。

这说明 OpenCode 把用户干预权放在非常高的位置：

- 即使系统怀疑死循环，也交给权限机制决定是否继续。

---

# 21. `plan_enter` / `plan_exit` / `question`

从 agent 默认权限和 `tool/plan.ts` 可见：

- `plan_enter` / `plan_exit` / `question` 都被视作正式 permission 名称

这说明权限系统不仅保护文件/命令/目录，也保护：

- 模式切换
- 用户提问
- 工作流控制动作

它的覆盖面比“工具权限”更广。

---

# 22. 旧 `Permission` 系统的特点

旧系统 `permission/index.ts`：

- 按 sessionID 维护 pending/approved map
- `permission.ask` plugin hook 可直接 allow/deny/ask
- `respond(always)` 会覆盖当前 session 下同类权限

相比 `PermissionNext`：

- 更偏 session-scoped memory
- 规则模型不如 Next 明确

这也解释了为什么新系统更适合作为统一主路径。

---

# 23. `PermissionNext` 的错误类型与控制流语义

它定义了三种关键错误：

## 23.1 `RejectedError`

- 用户拒绝，无附加消息
- 会 halts execution

## 23.2 `CorrectedError`

- 用户拒绝，但带反馈消息
- 意味着 agent 仍可基于指导继续

## 23.3 `DeniedError`

- 被配置规则自动拒绝
- 表示硬性策略阻止

## 23.4 为什么这很重要

这三者虽然都叫“没拿到权限”，但语义不同：

- 人工拒绝
- 人工纠正
- 配置硬拒绝

上层执行器可以据此做不同处理。

---

# 24. processor 如何响应权限拒绝

在 `processor.ts` 的 `tool-error` 分支中，如果错误是：

- `PermissionNext.RejectedError`
- `Question.RejectedError`

则：

- `blocked = shouldBreak`

随后最终可能返回 `stop`。

这说明权限拒绝不是普通工具错误，它会影响整轮执行控制流。

---

# 25. 一个完整的审批数据流

可以概括为：

## 25.1 上层发起 ask

- tool/bash/task/external-directory/doom_loop 等调用 `PermissionNext.ask()`

## 25.2 本地规则求值

- `evaluate(permission, pattern, rulesets...)`

## 25.3 三种路径

- allow -> 立即继续
- deny -> 抛 DeniedError
- ask -> pending + `permission.asked`

## 25.4 用户回复

- `reply(once|always|reject)`

## 25.5 控制流恢复或终止

- resolve promise -> 工具继续
- reject -> 工具报错 -> processor 可能 stop

这就是 OpenCode 的用户审批闭环。

---

# 26. 为什么权限系统要基于 wildcard pattern

很多动作不能只按工具名授权，例如：

- `read` 某些文件允许，但 `.env` 要 ask
- `task` 某些 subagent 允许，另一些 deny
- `external_directory` 某些目录 allow，另一些 ask
- `bash` 某类命令前缀可 always allow

所以 pattern-based rule 是必要的，不然审批会过于粗糙。

---

# 27. 这个模块背后的关键设计原则

## 27.1 权限默认值必须保守

规则未命中时默认 `ask`。

## 27.2 权限不只是工具开关，而是运行时行为约束系统

所以覆盖 `task`、`doom_loop`、`plan_exit`、`question` 等控制动作。

## 27.3 用户批准需要有 `once` 与 `always` 区分

否则体验要么过度骚扰，要么过度放权。

## 27.4 模型不应看到明确禁用的能力

所以有 `disabled()` 在工具暴露前先过滤。

---

# 28. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/permission/next.ts`
2. `packages/opencode/src/session/prompt.ts`
3. `packages/opencode/src/tool/task.ts`
4. `packages/opencode/src/tool/bash.ts`
5. `packages/opencode/src/tool/external-directory.ts`
6. `packages/opencode/src/session/processor.ts`
7. `packages/opencode/src/permission/index.ts`

重点盯住这些函数/概念：

- `PermissionNext.fromConfig()`
- `PermissionNext.merge()`
- `PermissionNext.evaluate()`
- `PermissionNext.ask()`
- `PermissionNext.reply()`
- `disabled()`
- `RejectedError / CorrectedError / DeniedError`
- `external_directory`
- `doom_loop`

---

# 29. 下一步还需要深挖的问题

这一篇已经把权限与审批主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`PermissionTable` 的 schema 与 project 级审批规则持久化读取/写回完整链路还值得继续查看
- **问题 2**：旧 `Permission` 系统还有哪些实际调用点，哪些地方仍未迁移到 `PermissionNext`，值得继续 grep
- **问题 3**：`CorrectedError` 在上层是否被充分利用来继续推理而非 stop，这一点值得继续追踪
- **问题 4**：`always` 规则目前尚未完整落盘，跨重启审批体验如何变化，还值得继续验证
- **问题 5**：bash AST 路径推断并不穷尽所有 shell 语法，权限 ask 的覆盖边界还值得继续评估
- **问题 6**：`Wildcard.findLast` 顺序语义对复杂 ruleset 是否足够直观，还值得从配置 UX 角度思考
- **问题 7**：plugin `permission.ask` 与 `PermissionNext` 新路径之间的边界还可继续整理
- **问题 8**：session 级 permission 覆盖 agent 默认权限时，UI 是否能清楚展示最终生效规则，还值得关注

---

# 30. 小结

`permission_evaluation_and_user_approval_flow` 模块定义了 OpenCode 如何把工具调用、目录越界、子代理委派、死循环防护与工作流切换统一纳入一套模式化审批体系：

- `PermissionNext` 提供规则化的 `allow / deny / ask` 求值模型
- `ask()` 与 `reply()` 形成挂起式用户审批闭环
- `disabled()` 让明确禁用的工具在暴露给模型前就被过滤掉
- `task`、`external_directory`、`bash`、`doom_loop`、`question` 等不同权限名共同覆盖了运行时的核心风险点

因此，这一层不是简单的弹窗确认逻辑，而是 OpenCode 保持可控自动化、用户主权与安全边界的核心执行约束系统。
