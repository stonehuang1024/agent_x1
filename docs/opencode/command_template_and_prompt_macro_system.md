# Command Template / Prompt Macro System 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 command 模板与 prompt macro 系统。

核心问题是：

- `Command` 为什么不是普通 shell command 注册表
- command、MCP prompt、skill 为什么会被统一映射成同一种 `Command.Info`
- `$1 / $2 / $ARGUMENTS` 占位符如何展开
- 模板里的 ``!`cmd` `` 为什么会先执行再插值
- 为什么某些 command 最终变成普通 prompt，而另一些会直接变成 `subtask` part

核心源码包括：

- `packages/opencode/src/command/index.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/config/markdown.ts`
- `packages/opencode/src/skill/index.ts`
- `packages/opencode/src/mcp/index.ts`

这一层本质上是 OpenCode 的**高层命令入口、模板化 prompt 生成与多来源 prompt 宏统一编排基础设施**。

---

# 2. 为什么 `Command` 不是 shell command 系统

虽然名字叫 command，但从实现看它处理的不是：

- 可执行程序
- shell argv
- PATH 查找

而是：

- 一段模板化 prompt
- 可选 agent/model 绑定
- 可选 subtask 委派语义

也就是说 `Command` 更接近：

- slash command
- prompt macro
- workflow shortcut

而不是终端命令执行器。

---

# 3. `Command.Info`：统一抽象多来源命令

`Command.Info` 的关键字段包括：

- `name`
- `description`
- `agent?`
- `model?`
- `source`
- `template`
- `subtask?`
- `hints`

## 3.1 含义

无论一个命令来自：

- 内建 command
- 用户 config.command
- MCP prompt
- skill

最终都会落成同一个统一结构。

这就是后续 `SessionPrompt.command()` 能用一条主逻辑处理所有命令来源的原因。

---

# 4. 为什么 `template` 允许是 `Promise<string> | string`

这是因为：

- 普通 command/skill 模板本地就能同步拿到
- MCP prompt 需要异步请求 MCP server 才能取回内容

zod 对 async getter 支持不理想，所以这里直接把 `template` 设计成：

- 同步字符串或异步 Promise

这样 `command()` 里统一 `await command.template` 即可。

这是很实用的工程折中。

---

# 5. `Command.state()`：命令目录如何构建

命令集合由四层组成：

## 5.1 内建 commands

当前看到至少有：

- `init`
- `review`

其中：

- `init` 用 `PROMPT_INITIALIZE`
- `review` 用 `PROMPT_REVIEW`
- `review.subtask = true`

## 5.2 用户 `cfg.command`

用户可自定义：

- `agent`
- `model`
- `description`
- `template`
- `subtask`

## 5.3 MCP prompts

通过：

- `await MCP.prompts()`
- `MCP.getPrompt(...)`

把远程 prompt 暴露成命令。

## 5.4 skills

通过：

- `Skill.all()`

把 skill 内容映射成可调用命令，但若名字冲突则跳过。

---

# 6. 这套四层合并说明了什么

OpenCode 把所有“可被显式触发的一段 prompt/工作流入口”统一纳入 command 目录。

所以 command 系统实际上是：

- **prompt entry registry**

而不是单纯的 CLI alias 系统。

---

# 7. `source` 字段的价值

`source` 可能是：

- `command`
- `mcp`
- `skill`

这能帮助系统或 UI 区分：

- 这个入口来自本地配置
- 来自 MCP prompt catalog
- 还是来自 skill 库

虽然运行时处理逻辑统一，但来源语义仍被保留。

---

# 8. `hints()`：命令参数提示是如何推导的

`Command.hints(template)` 会找两类占位符：

- `$1`, `$2`, ...
- `$ARGUMENTS`

并返回去重后的 hints。

## 8.1 意义

command 调用入口无需额外写参数 schema，也能知道模板“期待什么样的参数展开方式”。

这是一种非常轻量的约定式参数系统。

---

# 9. `SessionPrompt.command()`：command 执行的真实主入口

完整流程大致是：

1. `Command.get(input.command)`
2. 决定 `agentName`
3. 解析传入 arguments
4. `await command.template`
5. 展开占位符
6. 执行模板中的内嵌 shell 宏
7. 选择模型
8. 校验模型存在
9. 校验 agent 存在
10. `resolvePromptParts(template)`
11. 视情况构造成普通 parts 或 `subtask` part
12. 触发 `command.execute.before`
13. 最终调用 `prompt(...)`

也就是说 command 只是高层入口，最终还是落到 session prompt 主管线。

---

# 10. 参数解析不是简单 `split(" ")`

`argsRegex` 会匹配：

- `[Image N]`
- 双引号字符串
- 单引号字符串
- 普通非空白序列

然后再通过 `quoteTrimRegex` 去掉首尾引号。

## 10.1 含义

command 参数解析已经考虑到：

- 带空格的参数
- 图片占位 token

这使得模板命令对多模态/复杂参数更稳。

---

# 11. `$1..$N` 的展开规则

模板里所有 `$N` 会先被扫描，得到最大的占位编号 `last`。

替换时：

- 若位置超出输入参数数目 -> 替换为空串
- 若是最后一个占位符 -> 吞掉后续所有剩余参数并 join 成一串
- 否则一一对应替换

## 11.1 为什么最后一个占位符要吞掉剩余参数

源码注释写得很明确：

- `Let the final placeholder swallow any extra arguments so prompts read naturally`

这能让模板在用户给了比预期更多参数时，仍自然拼成 prompt，而不是无声丢参。

---

# 12. `$ARGUMENTS` 的语义

`$ARGUMENTS` 直接替换成：

- 原始 `input.arguments`

它和 `$1..$N` 的区别是：

- `$1..$N` 是结构化位置参数
- `$ARGUMENTS` 是整段自由文本透传

两者并存让模板既能严格位置化，也能保留自然语言自由度。

---

# 13. 如果模板没有任何占位符怎么办

若：

- 没有 `$1..$N`
- 没有 `$ARGUMENTS`
- 但用户又传了 arguments

系统会：

- 在模板后追加 `\n\n${input.arguments}`

## 13.1 这说明什么

command 系统默认尽量不丢掉用户输入。

即使模板作者忘了写占位符，调用参数仍会被拼进 prompt，而不是直接消失。

---

# 14. ``!`cmd` ``：为什么模板支持内嵌 shell 宏

在 `command()` 中：

- `bashRegex = /!`([^`]+)`/g`
- `ConfigMarkdown.shell(template)` 找出这些片段
- 再用 Bun `$` 执行并替换回模板

## 14.1 这意味着什么

command 模板不是静态字符串，而是：

- **可求值的 prompt 宏**

它可以在 prompt 展开阶段读取当前环境信息、git 状态、文件列表等，再把结果嵌回 prompt。

这让 command 非常强大。

---

# 15. 为什么内嵌 shell 放在 prompt 展开阶段，而不是后续 tool 阶段

因为这里的目标不是让模型自己决定是否执行某个命令，而是让模板作者在构建 prompt 时先注入环境上下文。

也就是说 ``!`cmd` `` 的作用更像：

- compile-time macro

而不是 run-time tool call。

---

# 16. `ConfigMarkdown.shell()` 与 `ConfigMarkdown.files()` 的角色

在 `config/markdown.ts` 中：

- `SHELL_REGEX = /!`([^`]+)`/g`
- `FILE_REGEX = /(?<![\w`])@(\.?[^\s`,.]*(?:\.[^\s`,.]+)*)/g`

前者让 command 模板支持 shell 宏。

后者则让 prompt 模板支持 `@file` 语法，被 `resolvePromptParts()` 进一步解析成 file/resource/agent 等 parts。

这说明 markdown-like template 语法本身就是 command 系统的上游 DSL。

---

# 17. agent/model 解析优先级

在 `command()` 中：

## 17.1 `agentName`

优先级为：

1. `command.agent`
2. `input.agent`
3. `Agent.defaultAgent()`

## 17.2 `taskModel`

优先级为：

1. `command.model`
2. `command.agent` 自带 model
3. `input.model`
4. `lastModel(sessionID)`

## 17.3 含义

command 作者可以：

- 强绑定 agent/model
- 部分绑定
- 或完全继承当前会话

这使模板既能作为严格工作流，也能作为轻量快捷入口。

---

# 18. 为什么要显式校验 model 与 agent 是否存在

在 prompt 真正执行前，系统会：

- `Provider.getModel(...)` 校验模型
- `Agent.get(agentName)` 校验 agent

如果失败：

- 发布 `Session.Event.Error`
- 给出 suggestion / available agents hint
- 然后抛错

这意味着 command 系统不会容忍“坏配置默默降级”。

它选择尽早暴露错误，这是正确的工程策略。

---

# 19. `resolvePromptParts(template)`：模板文本如何转成结构化 parts

command 展开完模板后，不会直接把整段字符串塞给模型，而是先走：

- `resolvePromptParts(template)`

这一步会进一步识别：

- `@file`
- `@directory`
- `@agent`
- 图片/资源等其他 prompt 片段

因此 command 模板不是“字符串 prompt”，而是“结构化 prompt 生成器”。

---

# 20. 为什么有些 command 会变成 `subtask` part

`isSubtask` 的判断是：

- `(agent.mode === "subagent" && command.subtask !== false) || command.subtask === true`

若成立，就不把 `templateParts` 直接作为本轮 user parts，而是构造：

- 一个单独 `subtask` part

其中写入：

- `agent`
- `description`
- `command`
- `model`
- `prompt`

## 20.1 含义

command 不只是 prompt 宏，还可以直接成为“子代理委派入口”。

这让 `/review` 之类命令能直接把任务发给 subagent，而不用先让主 agent 再转手。

---

# 21. `review` 为什么默认 `subtask: true`

内建 `review` command 明确配置：

- `subtask: true`

这说明设计者认为 review 适合作为：

- 独立委派工作单元

而不是当前主 agent 的普通 prompt。

这与 review 任务通常需要独立上下文、独立分析链路的特点非常一致。

---

# 22. subtask command 为什么只抽取第一个 text part 作为 `prompt`

在 `subtask` 构造中：

- `prompt: templateParts.find((y) => y.type === "text")?.text ?? ""`

并带有注释：

- `TODO: how can we make task tool accept a more complex input?`

## 22.1 这说明什么

当前 `TaskTool` 的输入模型仍偏文本中心。

command 模板虽然能产生更复杂的结构化 parts，但在 subtask 路径里暂时只把 text 部分降维传递过去。

这是一个明确的当前架构边界。

---

# 23. 非 subtask command 的执行方式

如果 `isSubtask` 不成立：

- `parts = [...templateParts, ...(input.parts ?? [])]`

随后：

- `prompt({ sessionID, messageID, model: userModel, agent: userAgent, parts, variant })`

也就是说，它本质上是在当前会话中注入一条模板化用户请求，再走正常 prompt loop。

---

# 24. `command.execute.before`：命令展开后的最后改写口

在真正调用 `prompt(...)` 前，会触发：

- `Plugin.trigger("command.execute.before", { command, sessionID, arguments }, { parts })`

这意味着插件可以在最终执行前修改：

- 模板展开结果
- subtask 构造结果
- 注入更多 parts
- 调整 command 语义

这是 command 系统的关键扩展钩子。

---

# 25. skills 为什么也映射成 commands

`Command.state()` 会把 `Skill.all()` 返回的每个 skill 变成：

- `name = skill.name`
- `description = skill.description`
- `template = skill.content`
- `source = skill`

## 25.1 意义

skill 不仅能通过 skill tool 被模型主动发现和调用，也能被用户显式当作命令入口触发。

这实现了：

- agentic discovery path
- explicit invocation path

两种入口并存。

---

# 26. MCP prompts 为什么也映射成 commands

通过：

- `MCP.prompts()` 枚举 prompt catalog
- `MCP.getPrompt(...)` 实际取内容

再把参数名映射成 `$1/$2/...`

这让 MCP server 暴露的 prompt 模板可以无缝接入本地 command 调用体系。

## 26.1 含义

OpenCode 实际上把 MCP prompt 当成“远程命令模板提供者”。

这是一种很漂亮的抽象统一。

---

# 27. 名称冲突策略

对于 skills：

- 若已有同名 command，则跳过 skill

说明优先级至少在这层是：

- 内建 / config / MCP command 优先于 skill 同名入口

这避免命令名歧义，但也意味着 skill 可能被覆盖隐藏。

---

# 28. 一个完整的 command 宏执行数据流

可以概括为：

## 28.1 建立命令目录

- defaults
- config.command
- MCP prompts
- skills

## 28.2 调用命令

- `Command.get(name)`
- 解析 arguments
- 取 template

## 28.3 展开模板

- `$1..$N`
- `$ARGUMENTS`
- ``!`cmd` ``
- `@file` / `@agent` 等 prompt parts

## 28.4 决定执行模式

- 当前会话普通 prompt
- 或构造成 `subtask` 委派给 subagent

## 28.5 插件改写

- `command.execute.before`

## 28.6 最终进入 `prompt()` 主链路

这就是 OpenCode command 系统的完整闭环。

---

# 29. 这个模块背后的关键设计原则

## 29.1 command 应统一所有“显式触发的 prompt 入口”

所以把本地 command、MCP prompt、skill 都映射到同一抽象。

## 29.2 模板必须同时支持结构化参数和自由文本参数

所以有 `$1..$N` 与 `$ARGUMENTS`。

## 29.3 模板展开应先于 prompt 执行

所以 ``!`cmd` `` 与 `@file` 解析都在进入主 loop 之前完成。

## 29.4 某些命令本质上就是子任务入口

所以 command 可直接产出 `subtask` part，而不是强行走当前 agent。

---

# 30. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/command/index.ts`
2. `packages/opencode/src/session/prompt.ts` 中 `command()`
3. `packages/opencode/src/config/markdown.ts`
4. `packages/opencode/src/skill/index.ts`
5. `packages/opencode/src/mcp/index.ts`

重点盯住这些函数/概念：

- `Command.hints()`
- `Command.get()`
- `Command.list()`
- `SessionPrompt.command()`
- `ConfigMarkdown.shell()`
- `resolvePromptParts()`
- `command.execute.before`
- `subtask`

---

# 31. 下一步还需要深挖的问题

这一篇已经把 command 宏系统主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`ConfigMarkdown.files()` 对 `@file` 语法的边界处理与歧义规避还值得继续细读
- **问题 2**：MCP prompt 的参数 schema 与本地 `$1/$2` 位置参数映射之间是否会丢失语义，还值得继续评估
- **问题 3**：subtask command 当前只传 text prompt，复杂附件/结构化 parts 如何传入 TaskTool 仍是明显待解问题
- **问题 4**：模板中的 ``!`cmd` `` 执行缺少专门审批链路，这一设计边界还值得继续从安全角度审视
- **问题 5**：同名冲突时 skill 被 command/MCP 覆盖，UI 是否能清楚展示这一点，还值得继续关注
- **问题 6**：命令模板是否支持 frontmatter 或更多结构化元数据，还值得继续阅读 config markdown 相关代码
- **问题 7**：`review` 等内建 command 的 prompt 内容本身还值得单独逐篇拆读
- **问题 8**：command 宏与 workflow/plan/tool 体系之间的边界还可以继续整理

---

# 32. 小结

`command_template_and_prompt_macro_system` 模块定义了 OpenCode 如何把各种显式命令入口统一收束为一套模板化 prompt 宏机制：

- `Command` 命名空间统一管理本地 command、MCP prompt 与 skill 映射入口
- `SessionPrompt.command()` 负责参数展开、shell 宏求值、agent/model 解析与执行模式选择
- `resolvePromptParts()` 进一步把模板文本转成结构化 prompt parts
- 某些命令还可以直接构造成 `subtask` part，进入子代理委派链路

因此，这一层不是 shell alias 或简单 slash command，而是 OpenCode 把用户显式入口、外部 prompt catalog 与内部代理编排统一起来的核心 prompt 宏基础设施。
