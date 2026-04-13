# Command System / Slash Commands 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 command 系统与 slash command 风格入口。

核心问题是：

- command 在 OpenCode 里是什么
- 内置 command、配置 command、MCP prompt、skill 为什么会被统一成一类对象
- command template 如何定义参数占位
- command 的执行事件如何进入 runtime
- TUI / route / prompt 链路如何触发这些 command

核心源码包括：

- `packages/opencode/src/command/index.ts`
- `packages/opencode/src/server/routes/tui.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/config/config.ts`
- `packages/opencode/src/mcp/*`
- `packages/opencode/src/skill/skill.ts`

这一层本质上是 OpenCode 的**高层任务模板与快捷入口系统**。

---

# 2. command 不等于 shell command

这里的 `Command` 不是 bash 命令包装器。

它更接近：

- 预定义 prompt 模板
- 可带 agent/model/source 元数据的快捷任务入口
- MCP prompt / skill / config command 的统一抽象

所以 command 系统的本质是：

- **把高频任务模式封装成可复用 prompt/program template**

而不是直接执行系统命令。

---

# 3. `Command.Info`：统一命令描述结构

`Command.Info` 包括：

- `name`
- `description`
- `agent?`
- `model?`
- `source?`
- `template`
- `subtask?`
- `hints`

## 3.1 `source`

来源可为：

- `command`
- `mcp`
- `skill`

这非常关键，因为它说明 OpenCode 明确把三类不同上游来源统一收敛到 command 抽象中。

## 3.2 `template`

模板可以是：

- 直接字符串
- `Promise<string>`

后者是为 MCP prompt 这类异步来源准备的。

## 3.3 `subtask`

说明 command 不一定都走主会话，有些更适合以子任务方式执行。

---

# 4. `hints()`：模板参数占位分析器

`Command.hints(template)` 会从模板里提取：

- `$1`, `$2`, ...
- `$ARGUMENTS`

这说明 command 模板支持参数化替换，而 hints 是对调用者的静态提示。

## 4.1 为什么这很重要

这样 UI 或调用层就可以提前知道：

- 这个 command 需要哪些位置参数
- 是否支持整段 arguments 注入

因此 command 不只是文本片段，而是轻量可参数化模板。

---

# 5. 内置 command：`init` 与 `review`

`Command.state()` 先注册两条默认命令：

- `init`
- `review`

## 5.1 `init`

- 描述：创建/更新 `AGENTS.md`
- 模板来自 `template/initialize.txt`
- 会把 `${path}` 替换成 `Instance.worktree`

## 5.2 `review`

- 描述：review changes [commit|branch|pr]，默认看未提交改动
- 模板来自 `template/review.txt`
- `subtask: true`

这说明 OpenCode 本身已经把一部分高频工作流产品化成默认 command。

---

# 6. 配置 command：用户/项目可定义模板化任务

`Command.state()` 会读取：

- `cfg.command`

对每个配置条目生成 command：

- `name`
- `agent`
- `model`
- `description`
- `template`
- `subtask`
- `hints`

这说明 command 是 config system 的正式一部分。

换句话说，用户可以通过配置层为项目增加：

- 规范化 prompt 宏
- slash-style 工作流入口
- 面向团队复用的任务模板

---

# 7. MCP prompt -> command 的统一

这是 command 系统最有意思的地方之一。

## 7.1 `MCP.prompts()`

`Command.state()` 会把所有 MCP prompt 也加载进结果集。

## 7.2 异步 template 获取

对于 MCP prompt，`template` 不是同步字符串，而是一个 Promise：

- 调 `MCP.getPrompt(...)`
- 把 prompt 参数替换成 `$1`, `$2`, ...
- 把返回 messages 中的 text 内容拼成模板字符串

## 7.3 为什么这重要

这意味着 OpenCode 把外部 MCP prompt 生态中的模板能力，直接映射成了本地 command 入口。

也就是说，用户看到的 command 列表里，可以同时出现：

- 本地 config command
- 远端 MCP prompt
- skill

而不需要理解它们的底层差异。

这是一种非常漂亮的统一抽象。

---

# 8. skill -> command 的统一

`Command.state()` 最后还会遍历：

- `Skill.all()`

并把每个 skill 映射成 command：

- `source: "skill"`
- `template = skill.content`

而且如果已经存在同名 command，则跳过。

这说明 skill 不只是通过 `skill` tool 按需加载，也能直接成为可调用 command。

这是 command 系统的第二层统一：

- **高层任务模板不区分来源，只看最终是否能作为可执行模板**

---

# 9. `Command.state()` 的真正角色

综合上面几段，可以看出 `Command.state()` 在做的是：

- 收集内置 command
- 合并 config command
- 映射 MCP prompt
- 映射 skill
- 构建统一 command registry

因此它不是一个简单列表函数，而是：

- **command namespace synthesizer**

---

# 10. command 触发事件：`command.executed`

`Command.Event.Executed` 定义了：

- `name`
- `sessionID`
- `arguments`
- `messageID`

这说明 command 执行不是 UI 层私有动作，而是 runtime 的正式事件。

其意义在于：

- 可审计
- 可同步
- 可供插件/外部监听

---

# 11. TUI routes 与 command 触发

`server/routes/tui.ts` 清楚展示了 command 系统在 UI 侧的入口方式。

## 11.1 `/open-help` / `/open-sessions` / `/open-models`

这些 route 本质上都在发：

- `TuiEvent.CommandExecute`

例如：

- `help.show`
- `session.list`
- `model.list`

这说明 TUI 自己内部也有一套命令名空间，route 只是远程触发器。

## 11.2 `/execute-command`

这是更通用的命令执行入口。

它把外部短名字映射到内部命令，再通过 `Bus.publish(TuiEvent.CommandExecute, ...)` 驱动 TUI。

这让 IDE/HTTP 调用方不必直接模拟键盘，而能正式调用 command 动作。

---

# 12. command 与 slash command 的关系

虽然这里没有一个单独的 `slash-command.ts` 文件，但从命名和用途看，OpenCode 的 command 很明显就是 slash command 风格能力的底层数据模型：

- 有名字
- 有描述
- 有参数 hints
- 有模板
- 可由 UI 选择和触发

换句话说，slash command 只是用户交互层表现；真正的底层抽象是：

- `Command.Info`

---

# 13. command 如何进入 session prompt 主链路

从之前读过的 `session/prompt.ts` 和本次 grep 可以确认，command 执行时会：

- 经过 `command.execute.before` plugin hook
- 结合当前 session / agent / model / arguments
- 最终生成新的 prompt 或子任务
- 再进入正式 SessionPrompt 流程

这说明 command 并不直接替代 prompt，而是：

- **生成/包装 prompt 的更高层入口**

---

# 14. `agent` / `model` / `subtask` 元数据的意义

command 本身除了模板内容，还可以带：

- `agent`
- `model`
- `subtask`

这说明 command 不只是文本快捷键，它还能携带执行策略。

## 14.1 `agent`

意味着某条 command 可以指定更合适的 agent 模式。

## 14.2 `model`

意味着 command 可以绑定特定模型。

## 14.3 `subtask`

意味着 command 可以指定它更适合在子任务上下文中运行，而不是污染主会话。

这让 command 成为一种轻量 workflow descriptor，而不是纯 prompt snippet。

---

# 15. command 与 config / MCP / skill 的关系

这个模块最核心的结构化认知可以总结为：

## 15.1 config command

适合：

- 团队私有 prompt 模板
- 项目工作流快捷入口

## 15.2 MCP prompt

适合：

- 外部系统通过协议提供 prompt 模板

## 15.3 skill

适合：

- 更大段、带领域知识的说明文档/操作手册

而 command 层把三者统一后，对上层 UI/调用者暴露一个一致界面。

这是非常好的产品架构选择。

---

# 16. 这个模块背后的关键设计原则

## 16.1 用户看到的入口应统一，而不是暴露底层来源差异

config command、MCP prompt、skill 最终都能表现成 command。

## 16.2 command 应该是参数化模板，而不是死文本

通过 `$1`、`$ARGUMENTS`，command 具备可重用性。

## 16.3 command 不该绕过 runtime，而应接入标准会话流程

command 最终仍通过 session/prompt/agent/runtime 主链路执行。

## 16.4 UI command 与任务 command 可以共享命名/事件机制

TUI 内部命令和用户任务模板命令虽不完全相同，但都可通过事件驱动桥接。

---

# 17. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/command/index.ts`
2. `packages/opencode/src/config/config.ts`
3. `packages/opencode/src/server/routes/tui.ts`
4. `packages/opencode/src/session/prompt.ts`
5. `packages/opencode/src/mcp/*`
6. `packages/opencode/src/skill/skill.ts`

重点盯住这些函数/概念：

- `Command.Info`
- `Command.hints()`
- `Command.state()`
- `Command.get()`
- `Command.list()`
- `Command.Event.Executed`
- `TuiEvent.CommandExecute`
- `callTui()`
- `command.execute.before`

---

# 18. 下一步还需要深挖的问题

这一篇已经把 command 主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`session/prompt.ts` 中 command 解析、参数替换和真正 prompt 生成的完整实现还值得单独精读
- **问题 2**：TUI 内部命令名空间与 `Command.Info.name` 的关系是否完全统一，还需要继续确认
- **问题 3**：MCP prompt 参数类型、默认值和错误处理如何映射到 command UI，还值得继续分析
- **问题 4**：skill 既可作为 command，又可通过 `skill` tool 按需加载，这两种入口的推荐使用边界还可继续梳理
- **问题 5**：command 执行事件在 UI 或插件中的具体消费路径还值得继续追踪
- **问题 6**：未来如果 command 模板支持更复杂 frontmatter 或结构化参数，当前 hints 机制是否足够
- **问题 7**：slash command 的最终 UX 呈现、补全与提示逻辑位于哪里，还值得继续查 TUI/CLI 代码
- **问题 8**：command 与 plan/subtask/agent mode 之间的联动规则还能继续细化

---

# 19. 小结

`command_system_and_slash_commands` 模块定义了 OpenCode 如何把高频任务入口统一成一套可配置的模板系统：

- `Command` 提供统一的命令描述模型
- 内置 command、配置 command、MCP prompt、skill 都会被合并进同一 registry
- `hints()` 提供参数占位提示
- TUI routes 和事件系统则提供远程/界面触发通路
- 最终 command 仍会接入标准 session/agent/runtime 主链路

因此，这一层不是简单菜单项系统，而是 OpenCode 对高频工作流与 slash-style 交互的统一抽象。
