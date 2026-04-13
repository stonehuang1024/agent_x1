# Shell / Command Execution / Env Injection 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 shell、命令模板与终端环境注入链路。

核心问题是：

- `bash` 工具与 `SessionPrompt.shell()` 有什么区别
- 为什么 shell 执行既有一次性命令执行，也有 PTY 长连接终端
- `Command` 模板系统如何把 slash/command 调用转成 prompt 或 subtask
- `shell.env` 插件钩子为什么被多个执行入口共享
- bash 工具如何做权限解析、目录边界审批、超时和输出元数据流式更新

核心源码包括：

- `packages/opencode/src/tool/bash.ts`
- `packages/opencode/src/pty/index.ts`
- `packages/opencode/src/command/index.ts`
- `packages/opencode/src/session/prompt.ts`

这一层本质上是 OpenCode 的**命令执行、终端会话与 shell 环境投影基础设施**。

---

# 2. 为什么要区分三类执行路径

OpenCode 里与 shell 相关的能力至少分三类：

## 2.1 `bash` 工具

- 给 agent 在推理过程中调用
- 带权限审批
- 结果进入 tool part

## 2.2 `SessionPrompt.shell()`

- 表示“用户执行了一个 shell 工具”
- 直接写入会话历史
- 以 assistant/tool part 的形式保存输出

## 2.3 `Pty`

- 提供可交互长连接终端
- 维护 buffer 与订阅者
- 不等同于一次性 tool 调用

这三者解决的问题完全不同，所以必须分开建模。

---

# 3. `BashTool`：agent 可调用的一次性命令工具

`BashTool` 的参数包括：

- `command`
- `timeout?`
- `workdir?`
- `description`

它的职责是：

- 在安全边界内执行 shell 命令
- 流式回传 output metadata
- 以 tool result 形式进入消息历史

这就是普通 agentic shell 执行路径。

---

# 4. 为什么 `BashTool` 要先 parse 命令 AST

它使用 `web-tree-sitter + tree-sitter-bash` 解析命令，再遍历 `command` 节点。

目的不是花哨，而是为了两件事：

- 提取可能涉及的路径参数
- 形成权限 pattern / always pattern

相比简单字符串正则，AST 解析显然更稳。

---

# 5. `bash` 工具如何做路径边界分析

对于常见命令：

- `cd`
- `rm`
- `cp`
- `mv`
- `mkdir`
- `touch`
- `chmod`
- `chown`
- `cat`

它会遍历非 flag 参数，尝试：

- `fs.realpath(path.resolve(cwd, arg))`

若解析出的路径不在 `Instance.containsPath()` 范围内：

- 记录该外部目录
- 后续统一 ask `external_directory`

## 5.1 含义

系统不是到命令真正执行时才发现越界，而是在静态分析阶段就尽量提前收集外部目录访问需求。

---

# 6. `patterns` 与 `always`：bash 权限为什么分两层

对每条解析出的 command，系统会收集：

- `patterns.add(commandText)`
- `always.add(BashArity.prefix(command).join(" ") + " *")`

然后 ask：

- `permission = "bash"`
- `patterns = exact-ish 当前命令文本`
- `always = arity/prefix 级持久模式`

## 6.1 这意味着什么

权限系统既能表达：

- 此次命令是否允许
- 将来同类命令是否可持续自动允许

这对 shell 工具尤其重要。

---

# 7. `shell.env`：为什么 shell 环境注入是统一 hook

在 `BashTool.execute()` 中，真正 spawn 前会：

- `Plugin.trigger("shell.env", { cwd, sessionID, callID }, { env: {} })`

同样：

- `Pty.create()`
- `SessionPrompt.shell()`

也都用了这个 hook。

## 7.1 含义

OpenCode 把 shell 环境注入抽象成统一扩展点，而不是每条 shell 路径自己拼 env。

这能保证：

- 一次配置，三条路径共享
- 企业/插件可以统一注入代理、token、PATH、LANG 等环境变量

---

# 8. `BashTool` 执行时的 env 组成

最终 env 是：

- `process.env`
- `shellEnv.env`

没有额外过多魔法变量。

这说明 bash tool 尽量接近真实 shell 环境，同时允许插件做必要增量注入。

---

# 9. `BashTool` 的流式 metadata 更新

在命令执行期间：

- stdout/stderr 每来一段，就 append 到 `output`
- 然后 `ctx.metadata({ metadata: { output, description } })`

并且 metadata 中的 output 会被截断到 `MAX_METADATA_LENGTH`。

## 9.1 为什么只截 metadata 不截最终 output

metadata 是为了：

- UI/运行时中途展示
- 避免超大 blob 实时写入 tool state metadata

最终返回值 `output` 仍然保留完整结果，再由上层 truncation/part 持久化体系处理。

这是很合理的分层。

---

# 10. timeout / abort / kill tree

`BashTool` 在执行中会维护：

- `timedOut`
- `aborted`
- `exited`

并通过：

- `Shell.killTree(proc, { exited: () => exited })`

来终止整棵进程树，而不是只杀父进程。

## 10.1 为什么 kill tree 很关键

很多 shell 命令会再 fork 子进程。

如果只 kill 顶层 shell：

- 子进程可能残留
- 会造成资源泄漏与后台幽灵进程

因此 kill tree 是正确的根因处理。

---

# 11. 超时和用户中断会如何体现在输出里

如果 timeout 或 abort 发生，会把 metadata 附加到输出末尾：

```text
<bash_metadata>
...
</bash_metadata>
```

其中可能包含：

- `bash tool terminated command after exceeding timeout ...`
- `User aborted the command`

这说明异常终止不会只反映在状态码里，还会显式写入输出文本，方便模型继续理解发生了什么。

---

# 12. `SessionPrompt.shell()`：为什么还需要一条“用户执行命令”的会话路径

`shell(input)` 并不是普通 tool 调用，而是：

- 先占用 session loop 的运行权
- 清理 revert
- 创建一条 user message
- 再创建一条 assistant message + running `tool:bash` part
- 真正执行 shell
- 最后把结果写回 part

## 12.1 这说明什么

这是“把用户发起的 shell 操作也纳入消息历史”的路径。

它更像：

- 系统替用户补记一条工具执行历史

而不是让 agent 自己决定是否执行 shell。

---

# 13. `SessionPrompt.shell()` 与 `BashTool` 的主要差异

## 13.1 `BashTool`

- 走权限 ask
- 由 agent 在推理中调用
- 通过 tool context 执行

## 13.2 `SessionPrompt.shell()`

- 直接由用户/系统入口调用
- 先写 user message: `The following tool was executed by the user`
- 再写 assistant/tool part
- 完成后若有 queued callbacks，还会恢复 session loop

这两条路径语义完全不同，但都映射成会话历史中的 bash tool 记录。

---

# 14. `SessionPrompt.shell()` 的 shell invocation 兼容层

它会根据 `Shell.preferred()` 推导 shellName，然后选择不同 args：

- `nu` / `fish` -> `-c`
- `zsh` -> 先 source `~/.zshenv` 和 `.zshrc`，再 `eval`
- `bash` -> 先 source `~/.bashrc`，再 `eval`
- `cmd` -> `/c`
- `powershell` / `pwsh` -> `-NoProfile -Command`
- fallback -> `-c`

## 14.1 含义

这条路径比 `BashTool` 更强调“贴近用户实际交互 shell 环境”。

尤其 zsh/bash 会主动加载 rc 文件，以还原用户 alias 与 shell 初始化行为。

---

# 15. 为什么 `SessionPrompt.shell()` 里 TERM 用 `dumb`

spawn 时 env 会加：

- `TERM: "dumb"`

这说明该路径主要服务非交互式输出捕获，而不是复杂终端能力。

与 PTY 的 `xterm-256color` 明显不同，体现了两条路径的使用场景差异。

---

# 16. `Pty.create()`：长连接交互终端入口

PTY 路径的目标是：

- 创建持续存在的终端 session
- 保存 buffer
- 支持 websocket-like 订阅与增量读取
- 支持 resize / write / connect / remove

它与 bash tool 最大的不同在于：

- 不以一次性工具结果为中心
- 而是以交互过程与终端状态为中心

---

# 17. `Pty.create()` 的 env 组成

PTy create 时 env 是：

- `process.env`
- `input.env`
- `shellEnv.env`
- `TERM=xterm-256color`
- `OPENCODE_TERMINAL=1`
- Windows 额外 `LC_*` / `LANG`

## 17.1 为什么和 `SessionPrompt.shell()` / `BashTool` 不同

因为 PTY 本质上是交互终端，会依赖：

- TERM
- 终端能力
- locale

它需要更完整的终端环境语义。

---

# 18. PTY 的 buffer 设计

`Pty` 维护：

- `buffer`
- `bufferCursor`
- `cursor`
- `BUFFER_LIMIT = 2MB`
- `BUFFER_CHUNK = 64KB`

当 buffer 超出上限时，会裁掉前面的 excess，并推进 `bufferCursor`。

## 18.1 含义

这说明 PTY 不是无限日志存储，而是有界滑动窗口。

这对长期终端会话是必要的内存保护。

---

# 19. PTY 事件模型

`Pty.Event` 包括：

- `pty.created`
- `pty.updated`
- `pty.exited`
- `pty.deleted`

说明 PTY 自己也是一个正式的 runtime 实体，有完整事件生命周期，而不是临时裸进程。

---

# 20. `Command` 模板系统：为什么它不是 shell 工具

`Command` 命名空间管理的是：

- command/mcp/skill 来源的模板命令

`Command.Info` 包含：

- `name`
- `description`
- `agent?`
- `model?`
- `source`
- `template`
- `subtask?`
- `hints`

这说明 Command 系统更接近：

- slash command / prompt macro / workflow trigger

而不是 shell command executor。

---

# 21. `Command.state()`：命令来源的三层合并

命令集合来自：

- 内建 command（如 `init` / `review`）
- 用户 config.command
- MCP prompts
- skills（若未与现有命令重名）

这说明 command 系统本质上是“可调用 prompt/模板目录”，不是单一配置表。

---

# 22. `Command.hints()`：模板占位参数推断

它会从模板中找：

- `$1`, `$2`, ...
- `$ARGUMENTS`

并生成 hints。

这让命令调用侧知道模板需要哪些参数形式，是很轻量但实用的 UX 设计。

---

# 23. `SessionPrompt.command()`：模板命令如何转成实际 prompt

主要流程：

1. `Command.get(input.command)`
2. 决定 `agentName`
3. 解析用户传入 arguments
4. 读取模板内容
5. 用 `$1...$N` / `$ARGUMENTS` 替换参数
6. 若模板内存在 ``!`cmd` ``，执行内嵌 shell 片段并替换结果
7. 决定 taskModel
8. 校验 model 存在
9. 找到目标 agent
10. `resolvePromptParts(template)`
11. 决定是否走 subtask 路线
12. `Plugin.trigger("command.execute.before", ..., { parts })`
13. 最终调用 `prompt(...)`

这说明 command 系统最终还是回到 prompt/session 主链路，而不是自己单独执行。

---

# 24. 命令模板中的内嵌 shell 执行

命令模板支持：

- ``!`...` ``

匹配后会通过 Bun `$` 执行，并把结果嵌回模板文本。

## 24.1 这意味着什么

command 模板不是纯静态文本，它可以在展开阶段动态取环境信息。

这是很强的能力，但也意味着 command 模板本身具有执行性。

---

# 25. `subtask` 命令模式

在 `command()` 中：

- 若 `agent.mode === "subagent" && command.subtask !== false`
- 或 `command.subtask === true`

就不会把模板内容当普通 user parts 注入。

而是构造一个单独 `subtask` part，其中包含：

- `agent`
- `description`
- `command`
- `model`
- `prompt`

这说明 command 系统可以直接触发子代理委派，而不必先让主 agent 再决定一次。

---

# 26. 命令路径里的 agent/model 解析优先级

`taskModel` 的优先级是：

1. `command.model`
2. `command.agent` 自带 model
3. `input.model`
4. `lastModel(sessionID)`

agentName 则是：

1. `command.agent`
2. `input.agent`
3. `Agent.defaultAgent()`

这说明 command 自身可以强绑定 agent/model，也可以把选择权留给调用者或当前 session。

---

# 27. `command.execute.before`：命令模板展开后的最后扩展点

在真正调用 `prompt(...)` 前，系统会触发：

- `Plugin.trigger("command.execute.before", { command, sessionID, arguments }, { parts })`

这意味着插件可以修改：

- 最终生成的 prompt parts
- subtask 构造
- 附件注入

这是 command 系统的重要扩展面。

---

# 28. shell / command / pty 三者如何协同

可以这样理解：

## 28.1 `bash` 工具

- agent 在会话内执行一次性命令

## 28.2 `SessionPrompt.shell()`

- 用户显式 shell 行为写入会话历史

## 28.3 `Pty`

- 提供持续交互终端

## 28.4 `Command`

- 把高层模板命令翻译成 prompt 或 subtask，不直接等同 shell

这四条路径共同组成了 OpenCode 的命令与终端能力面。

---

# 29. 这个模块背后的关键设计原则

## 29.1 一次性 shell 执行与交互式终端必须分离

所以有 `BashTool` 和 `Pty` 两套模型。

## 29.2 shell 环境注入应统一抽象，避免入口分裂

所以有共享的 `shell.env` hook。

## 29.3 命令模板系统应复用 prompt/session 主链路，而不是旁路实现

所以 `command()` 最终还是调用 `prompt(...)`。

## 29.4 shell 安全边界应尽量前置到静态分析阶段

所以 bash 工具先 parse AST，再 ask permission/external_directory。

---

# 30. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/tool/bash.ts`
2. `packages/opencode/src/pty/index.ts`
3. `packages/opencode/src/command/index.ts`
4. `packages/opencode/src/session/prompt.ts`
5. `packages/opencode/src/shell/shell.ts`

重点盯住这些函数/概念：

- `BashTool.execute()`
- `Shell.killTree()`
- `shell.env`
- `Pty.create()`
- `Pty.connect()`
- `Command.get()`
- `SessionPrompt.command()`
- `command.execute.before`

---

# 31. 下一步还需要深挖的问题

这一篇已经把 shell/command/pty 主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`Pty.connect()` 后半段的 cursor/meta 帧协议与客户端订阅语义还值得继续精读
- **问题 2**：`Shell.killTree()` 的跨平台实现细节还值得继续查看，特别是 Windows 行为
- **问题 3**：模板中的内嵌 shell 替换 ``!`cmd` `` 是否需要更强的安全约束，还值得继续评估
- **问题 4**：`SessionPrompt.shell()` 与 `BashTool` 在权限/审计上的差异是否完全符合产品预期，还可继续思考
- **问题 5**：command 模板与 skill/MCP prompt 同名冲突时当前优先级是否总合理，还值得继续评估
- **问题 6**：bash 工具静态解析的命令白名单仍非穷尽，某些复杂 shell 语法的路径推断边界还值得继续验证
- **问题 7**：PTY buffer 上限与高吞吐终端场景下的 UX 取舍还值得继续观察
- **问题 8**：将 shell output 写入 part metadata 时的大小与性能成本还可以继续测量

---

# 32. 小结

`shell_command_execution_and_env_injection` 模块定义了 OpenCode 如何把 shell 与命令能力分层组织成安全、可审计、可扩展的运行时设施：

- `BashTool` 负责 agentic 一次性 shell 执行与权限控制
- `SessionPrompt.shell()` 负责把用户显式命令执行纳入会话历史
- `Pty` 负责长连接交互终端生命周期
- `Command` 系统负责把模板命令、MCP prompt 与 skill 内容翻译成 prompt 或 subtask
- `shell.env` 则提供贯穿这些路径的统一环境注入扩展点

因此，这一层不是单纯的命令执行封装，而是 OpenCode 连接会话系统、终端系统、权限系统与插件系统的关键基础设施。
