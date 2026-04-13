# Permission / Rules 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的权限与规则系统。

核心问题是：

- 规则如何表示
- 配置里的 permission 如何变成 runtime 规则集
- 多个规则集叠加时如何裁决
- `allow` / `deny` / `ask` 各自意味着什么
- pending approval 如何管理
- 为什么权限系统既能做工具过滤，也能做执行时审批

核心源码包括：

- `packages/opencode/src/permission/next.ts`
- `packages/opencode/src/agent/agent.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/tool/*`

权限系统不是 OpenCode 的附属安全补丁，而是其 runtime 核心之一。

---

# 2. 基本模型：规则是三元组

`PermissionNext.Rule` 的结构很简单：

- `permission`
- `pattern`
- `action`

其中 `action` 只能是：

- `allow`
- `deny`
- `ask`

这就是 OpenCode 权限系统的基本单元。

## 2.1 `permission`

表示能力类别，例如：

- `read`
- `edit`
- `glob`
- `grep`
- `skill`
- `task`
- `external_directory`
- `doom_loop`

## 2.2 `pattern`

表示更细的匹配范围，例如：

- 具体文件路径
- 文件 glob
- skill 名称
- agent 名称
- 查询文本
- `*`

## 2.3 `action`

表示命中后的处理方式：

- **allow**：直接放行
- **deny**：直接拒绝
- **ask**：需要用户审批

这个模型足够简单，但表达力已经很强。

---

# 3. `fromConfig()`：配置到规则集的编译

## 3.1 为什么需要编译层

用户配置里的 permission 写法更适合人读，例如：

- 某 permission 直接写字符串
- 某 permission 写成 pattern -> action 的对象

runtime 不想处理多种配置形态，因此先统一编译成 `Ruleset`。

## 3.2 编译规则

如果配置值是字符串：

- 生成 `{ permission: key, pattern: "*", action: value }`

如果配置值是对象：

- 把每个 pattern/action 展成一条规则

这是一种很直接的 **declarative config -> flat ruleset** 变换。

## 3.3 路径展开

编译时还会做：

- `~/` -> home 目录
- `$HOME/` -> home 目录

这说明 permission config 支持更友好的用户写法，而 runtime 统一转成绝对/展开后的匹配模式。

---

# 4. `merge()`：规则集合并

`merge(...rulesets)` 很简单：

- 直接 `flat()` 拼接

关键不在实现复杂，而在于配合 `evaluate()` 的语义：

- **后面的规则有机会覆盖前面的规则**

所以 merge 的顺序是有意义的。

这解释了为什么很多地方都在 carefully merge：

- defaults
- mode-specific rules
- user rules
- runtime overrides

---

# 5. `evaluate()`：裁决算法

这是权限系统的核心函数。

## 5.1 算法流程

输入：

- `permission`
- `pattern`
- 一个或多个 ruleset

流程：

1. `merge(...rulesets)`
2. 从后往前找最后一个匹配规则
3. 匹配条件：
   - `Wildcard.match(permission, rule.permission)`
   - `Wildcard.match(pattern, rule.pattern)`
4. 若找到则返回该 rule
5. 若找不到，返回默认：
   - `{ action: "ask", permission, pattern: "*" }`

## 5.2 最关键的语义

### `findLast`

这是整个系统最重要的行为语义之一。

它意味着：

- **last-match-wins**

也就是后定义的规则优先于前定义规则。

## 5.3 为什么这是个好选择

因为 OpenCode 经常需要这样的叠加顺序：

- 系统默认规则
- mode 专属规则
- 用户配置覆盖
- 临时运行时补丁

如果不是 last-match-wins，覆盖关系会更难推理。

## 5.4 默认是 `ask` 的意义

若没有任何规则命中，就默认 `ask` 而不是 `allow`。

这说明 OpenCode 的安全默认值是：

- **不确定时请求审批**

而不是：

- 不确定时直接放行

这是明显更稳的策略。

---

# 6. `ask()`：从规则裁决进入审批状态机

## 6.1 输入结构

`ask()` 接受：

- `sessionID`
- `permission`
- `patterns`
- `metadata`
- `always`
- 可选 tool 信息
- `ruleset`

这说明权限请求并不是“只有一个目标”，而可以一次检查多个 pattern。

## 6.2 执行流程

对每个 pattern：

1. 调用 `evaluate(permission, pattern, ruleset, approved)`
2. 如果 `deny`：
   - 抛 `DeniedError`
3. 如果 `allow`：
   - 继续检查下一个 pattern
4. 如果 `ask`：
   - 生成或复用 request id
   - 放入 `pending` map
   - 发布 `permission.asked`
   - 返回一个等待 resolve/reject 的 promise

这说明 `ask()` 本质上不是简单布尔校验，而是一个：

- **同步规则判断 + 异步人工审批桥接器**

## 6.3 `pending` map 的作用

每条 ask 请求都会进：

- `Map<PermissionID, PendingEntry>`

其中保存：

- request info
- `resolve`
- `reject`

这使得权限系统可以：

- 暂停当前工具执行
- 等待 UI/用户反馈
- 再恢复或拒绝

---

# 7. `reply()`：审批响应传播算法

## 7.1 支持的回复类型

- `once`
- `always`
- `reject`

## 7.2 `reject`

当用户拒绝时：

- 当前 request reject
- 同 session 下其他 pending permission 也会一起 reject
- 发布对应 `permission.replied`

### 为什么是 session 级联 reject

这意味着 OpenCode 把“用户拒绝当前审批”视为：

- 这个 session 当前自动执行方向应整体停下来

这比只拒绝单一 request 更符合真实交互语义。

## 7.3 `once`

只对当前 request 放行：

- `existing.resolve()`

不会写入持久批准规则。

## 7.4 `always`

会把 `existing.info.always` 中的 patterns 写进 `approved`：

- `{ permission, pattern, action: "allow" }`

然后：

- resolve 当前 request
- 再扫描同 session 其他 pending requests
- 若这些 pending 现在全部可由 approved 规则放行，则自动 resolve

这是一种非常漂亮的 **approval propagation** 机制。

它意味着一次“总是允许”不仅放行当前操作，还能解锁当前 session 中其他已满足条件的后续请求。

---

# 8. `approved` 状态与持久化边界

## 8.1 当前状态来源

`state()` 启动时会从 `PermissionTable` 读取已保存规则：

- `approved: stored`

说明权限系统设计上是支持持久批准规则的。

## 8.2 但当前实现还未完全落盘

源码中对 `always` 的后续存盘还留着 TODO 注释。

这说明当前设计意图是：

- 项目级权限批准可以持久化

但 UI/管理能力可能还未完全成熟，因此暂未完整落地。

---

# 9. `disabled()`：暴露前过滤

## 9.1 它解决什么问题

有些工具如果对某 agent 来说整体不可用，就不应该出现在模型可见工具列表里。

这正是 `disabled()` 的职责。

## 9.2 算法

对工具名列表逐个处理：

- 如果工具属于编辑类：
  - `edit`, `write`, `patch`, `multiedit`
  - 统一映射 permission = `edit`
- 否则 permission = tool name
- 找 ruleset 中最后一个匹配该 permission 的规则
- 若该规则是：
  - `pattern = "*"`
  - `action = "deny"`
- 则把该工具记入 disabled set

## 9.3 为什么编辑工具要归并到 `edit`

因为从安全边界角度看：

- edit/write/patch/multiedit 都属于“修改文件”这一类能力

如果分开配规则，既冗余又容易漏掉变体。

因此 OpenCode 在权限层做了一个很合理的抽象归并。

## 9.4 这与 `ask()` 的区别

- `disabled()`：工具暴露前过滤
- `ask()`：工具执行时审批

这正是 OpenCode 权限系统的双层防线：

- **看不见**
- **看得见但执行仍需批准**

---

# 10. 权限系统与 agent 的关系

## 10.1 agent 是权限模板载体

在 `agent.ts` 中，每个 agent 都带有完整 `permission` ruleset。

这说明 agent 的本质之一就是：

- 一套权限边界

## 10.2 默认 agent 规则的设计

例如：

- `build` 基本允许大多数操作
- `plan` 明确 deny 编辑工具
- `explore` 从 `* = deny` 开始，只开放只读探索相关工具
- `compaction/title/summary` 则几乎 `* = deny`

这说明 OpenCode 的 mode 差异不是纯 prompt 差异，而是 runtime permission 差异。

## 10.3 用户规则如何叠加

每个 agent 最终都会 merge：

- defaults
- mode-specific config
- user global config
- agent-specific config

这就是为什么 last-match-wins 如此重要。

---

# 11. 权限系统与工具系统的关系

## 11.1 工具内部调用 `ctx.ask()`

例如：

- `read`
- `glob`
- `grep`
- `skill`
- `task`
- MCP 工具

都会在执行内部显式请求权限。

说明工具不是默认自由执行，而是必须自己声明其受保护操作。

## 11.2 `resolveTools()` 与权限系统

在工具装配阶段：

- 先调用 `PermissionNext.disabled(...)` 隐藏整体禁用工具
- 运行时工具上下文里再注入 `ask()`

所以 permission 已经深入嵌入 tool runtime 生命周期。

---

# 12. 权限系统与特殊运行时分支的关系

## 12.1 `doom_loop`

processor 检测到连续重复工具调用时，会发起：

- `permission: doom_loop`

说明权限系统不仅用于文件/工具安全，也用于“行为异常确认”。

## 12.2 `external_directory`

访问工作区外目录时，会触发：

- `permission: external_directory`

这说明 OpenCode 把“越出工作目录”视作一类独立风险边界。

## 12.3 `question`

某些模式允许 agent 向用户提问，某些模式则不允许。

这也通过 permission 统一建模。

所以 permission 系统并不局限于传统文件系统安全，而是扩展到：

- 工具能力
- 行为能力
- 模式切换能力
- 风险确认能力

---

# 13. 为什么说这是一个 session-aware 审批系统

## 13.1 它不是全局单点确认框

权限请求里总是带：

- `sessionID`
- 可选 `tool: { messageID, callID }`

说明每次权限审批都与具体 session / tool call 绑定。

## 13.2 session 级联行为

- reject 时同 session 其他 pending 一并拒绝
- always 时自动放行同 session 中其他已满足条件的 pending

这说明权限系统把 session 当成审批作用域。

这是很合理的，因为用户通常是在“当前这轮任务”上做判断。

---

# 14. 这个模块背后的关键设计原则

## 14.1 规则必须简单但覆盖面广

三元组模型非常简单，却能覆盖：

- 文件路径
- 工具能力
- 外部目录
- skill
- task
- 特殊行为

## 14.2 安全默认值应为 `ask`

默认 ask 比默认 allow 更稳健，尤其适合 agent runtime。

## 14.3 可见性与执行审批分离

如果只做执行审批，模型会看到太多实际不可用能力；如果只做可见性过滤，用户又缺少过程控制。

双层机制更平衡。

## 14.4 审批应该与 session 语义绑定

一次拒绝/总是允许，往往影响的是当前整轮任务，而不是孤立单一调用。

OpenCode 的 session-aware 传播逻辑非常符合这一点。

---

# 15. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/permission/next.ts`
2. `packages/opencode/src/agent/agent.ts`
3. `packages/opencode/src/session/prompt.ts`
4. `packages/opencode/src/tool/read.ts`
5. `packages/opencode/src/tool/task.ts`
6. `packages/opencode/src/tool/skill.ts`

重点盯住这些函数/概念：

- `fromConfig()`
- `merge()`
- `evaluate()`
- `ask()`
- `reply()`
- `disabled()`
- `DeniedError`
- `RejectedError`
- `CorrectedError`
- `doom_loop`
- `external_directory`

---

# 16. 下一步还需要深挖的问题

这一篇已经把权限主机制讲清楚了，但还有一些地方值得继续展开：

- **问题 1**：`Wildcard.match()` 的具体语义与路径/glob 匹配边界值得继续精读
- **问题 2**：`PermissionTable` 的读写、项目级作用域与未来 UI 管理方式还可继续拆解
- **问题 3**：`CorrectedError` 与 `RejectedError` 在上层 loop/processor 中的行为差异还可继续追踪
- **问题 4**：question、plan_enter、plan_exit 这类非文件权限在 UI 中的审批体验如何呈现，还可继续考察
- **问题 5**：MCP 工具的 permission pattern 与本地工具的 pattern 语义是否完全一致
- **问题 6**：多工作区/多实例场景下 approved 规则的作用域边界如何定义，还值得继续确认
- **问题 7**：如果规则集非常大，当前 last-match-wins 线性扫描是否会产生性能压力
- **问题 8**：未来持久化 always 规则后，规则冲突可视化与管理体验应如何设计

---

# 17. 小结

`permission_and_rules` 模块定义了 OpenCode 的运行时安全边界：

- `Rule(permission, pattern, action)` 是最小权限单元
- `fromConfig()` 把声明式配置编译成规则集
- `evaluate()` 用 last-match-wins 裁决最终动作
- `disabled()` 负责暴露前过滤
- `ask()/reply()` 负责执行时异步审批
- session-aware pending/always/reject 传播则让权限系统真正适合 agent 连续执行场景

因此，这一层不是一个附加 confirmation dialog 系统，而是 OpenCode runtime 的正式安全控制平面。
