# Agent Definition / Modes 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 agent 定义体系。

它回答的问题是：

- agent 到底是什么
- `build`、`plan`、`general`、`explore`、`compaction`、`title`、`summary` 分别代表什么
- `mode` 字段到底控制了什么
- agent 如何与 permission、model、variant、steps 协同
- 用户配置如何覆盖内置 agent
- 为什么 agent 是整个 runtime 的行为调度器，而不只是 prompt 名称

核心源码包括：

- `packages/opencode/src/agent/agent.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/tool/task.ts`
- `packages/opencode/src/tool/plan.ts`

agent 系统本质上是 OpenCode 的**行为模式与权限边界编排层**。

---

# 2. `Agent.Info` 的数据结构

## 2.1 agent 不是只有 prompt

`Agent.Info` 包含：

- `name`
- `description`
- `mode`
- `native`
- `hidden`
- `topP`
- `temperature`
- `color`
- `permission`
- `model`
- `variant`
- `prompt`
- `options`
- `steps`

这说明一个 agent 并不只是“一段 system prompt”，而是一整套行为约束对象。

## 2.2 每个字段的语义

### `mode`

取值：

- `subagent`
- `primary`
- `all`

### `permission`

决定：

- 工具可见性
- 工具能否执行
- 外部目录访问
- doom loop 等特殊行为审批

### `model`

允许 agent 绑定默认模型。

### `variant`

允许 agent 指定推理强度或 provider-specific 思考配置。

### `prompt`

允许 agent 覆盖默认 provider prompt。

### `steps`

决定 loop 最多允许走多少步。

因此 agent 实际上是一个 **runtime policy bundle**。

---

# 3. `Agent.state()`：内置 agent 是如何定义的

## 3.1 state 的总体职责

`Agent.state()` 会：

1. 读取 config
2. 读取 skill 目录
3. 构造默认 permissions
4. 构造内置 agents
5. 用 config 覆盖/新增 agents
6. 处理外部目录白名单补充

最终返回：

- `Record<string, Agent.Info>`

这说明 agent 体系既有：

- 内置标准 agent
- 用户自定义 agent

## 3.2 默认 permission 基线

在内置 agent 之前，系统先构造一份 `defaults` 权限集，包括：

- `* = allow`
- `doom_loop = ask`
- `external_directory` 默认 ask，但对白名单目录 allow
- `question = deny`
- `plan_enter = deny`
- `plan_exit = deny`
- `read` 默认 allow，但 `.env` 相关默认 ask

这说明 agent 的 permission 不是从空白开始，而是建立在一份系统默认基线之上。

## 3.3 为什么 skill 目录会进入白名单

`skillDirs = await Skill.dirs()`

然后与 `Truncate.GLOB` 一起构造 `whitelistedDirs`。

这意味着：

- skill 所在目录被默认视作 agent 可安全访问的外部目录

原因很合理：

- skill 本身是系统认可的工作流资源
- skill 返回的文件列表若还要每次因为 external_directory 再 ask，会非常影响体验

---

# 4. 内置 agent 逐个解读

## 4.1 `build`

### 定位

- 默认主 agent
- 负责真实执行任务

### 配置特点

- `mode: primary`
- `native: true`
- 在默认 permission 基础上：
  - `question: allow`
  - `plan_enter: allow`

### 含义

`build` 是“默认执行者”。它能：

- 使用常规工具
- 提问用户
- 允许进入 plan 模式

这是最接近“标准 coding agent”的主模式。

## 4.2 `plan`

### 定位

- 规划模式
- 强调研究、规划、不实际改代码

### 配置特点

- `mode: primary`
- `native: true`
- `question: allow`
- `plan_exit: allow`
- 对 `edit` 默认 deny
- 仅允许编辑 plan markdown 文件

### 含义

plan 模式不是简单“少写代码”，而是通过 permission 显式禁止修改普通工程文件。

这意味着 plan 模式的安全边界是运行时 enforce 的，不是仅靠 prompt 约束。

## 4.3 `general`

### 定位

- 通用 subagent
- 用于研究复杂问题、并行做多单元工作

### 配置特点

- `mode: subagent`
- `native: true`
- 禁止 `todoread` / `todowrite`

### 含义

它是一个适合被 `task` tool 调用的通用子代理。

禁止 todo 读写说明：

- 子 agent 不应该污染主任务计划层

## 4.4 `explore`

### 定位

- 快速探索代码库
- 更像“检索型 subagent”

### 配置特点

- `mode: subagent`
- `prompt: PROMPT_EXPLORE`
- 权限从 `* = deny` 开始
- 只 allow：
  - `grep`
  - `glob`
  - `list`
  - `bash`
  - `webfetch`
  - `websearch`
  - `codesearch`
  - `read`
  - 部分 external_directory

### 含义

`explore` 是典型的“只读/探索 agent”。

它不是靠模型自觉只读，而是 permission 层直接裁掉绝大多数执行能力。

## 4.5 `compaction`

### 定位

- 内部压缩 agent
- 专门为长上下文压缩服务

### 配置特点

- `mode: primary`
- `hidden: true`
- `prompt: PROMPT_COMPACTION`
- `* = deny`

### 含义

它是一个内部使用 agent，不面向普通用户直接选择。

之所以完全 deny 工具，是因为 compaction 任务本质上只需要：

- 阅读历史
- 生成摘要

不应再进行实际工具执行。

## 4.6 `title`

### 定位

- 会话标题生成 agent

### 配置特点

- `hidden: true`
- `temperature: 0.5`
- `prompt: PROMPT_TITLE`
- `* = deny`

### 含义

它是一个小任务 agent，用于生成 session 标题。

## 4.7 `summary`

### 定位

- 会话摘要 agent

### 配置特点

- `hidden: true`
- `prompt: PROMPT_SUMMARY`
- `* = deny`

### 含义

与 title 类似，属于内部辅助 agent。

---

# 5. `mode` 字段的真正含义

## 5.1 `primary`

`primary` 代表：

- 可以作为主对话 agent
- 可成为默认 agent
- 通常面向用户显式会话主线

例如：

- `build`
- `plan`
- `compaction`
- `title`
- `summary`

但其中一些又是 hidden，因此“primary”不等于“面向用户可见”。

## 5.2 `subagent`

`subagent` 代表：

- 主要作为 `task` tool 启动的子代理
- 通常用于分治、研究、探索

例如：

- `general`
- `explore`

## 5.3 `all`

`all` 主要用于用户自定义 agent 的默认 mode。

意味着：

- 既不严格限制为 primary，也不严格限制为 subagent
- 更像一个兼容型模式

## 5.4 为什么 mode 不等于权限

mode 决定的是 agent 的角色定位。

permission 决定的是 agent 的具体能力边界。

两者是协同关系，不是替代关系。

---

# 6. agent 与 loop 的关系

## 6.1 loop 中 agent 如何生效

在 `SessionPrompt.loop()` 里：

- `const agent = await Agent.get(lastUser.agent)`
- `const maxSteps = agent.steps ?? Infinity`
- `resolveTools({ agent, ... })`
- `SystemPrompt.skills(agent)`
- `processor.process({ agent, ... })`

说明 agent 会影响：

- 最大步数
- 工具集合
- 技能索引
- prompt
- permissions
- options

也就是说，agent 是 loop 每一轮行为的控制模板。

## 6.2 `steps` 的作用

`const isLastStep = step >= maxSteps`

当达到最大步数时，loop 会额外给模型加上：

- `MAX_STEPS`

提示模型该收束。

所以 `steps` 不是 UI 提示，而是 loop 控制参数。

---

# 7. agent 与工具系统的关系

## 7.1 `resolveTools()` 会按 agent 过滤工具

工具最终是否可用，不仅看 registry 和 user overrides，也看：

- `PermissionNext.disabled(Object.keys(input.tools), input.agent.permission)`

这意味着 agent 的 permission 直接决定工具可见面。

## 7.2 `task` tool 如何利用 agent mode

`TaskTool` 初始化时会：

- `Agent.list().then((x) => x.filter((a) => a.mode !== "primary"))`

也就是说：

- 只有非 primary agent 才会进入 subagent 候选列表

这说明 `mode` 还影响 agent 是否可作为 task 调用目标。

## 7.3 为什么 subagent 默认禁止 todo

在 `general` 和 `TaskTool` 中都能看到对子 session todo 的控制。

这说明 agent 设计有明确层级：

- 主 agent 负责全局计划
- subagent 负责局部任务

这种层级分工避免多 agent 同时修改全局任务状态。

---

# 8. plan 模式为什么是一个很典型的工程化设计

## 8.1 不是 prompt 层“建议不要编辑”

plan agent 直接在 permission 中写：

- `edit: * = deny`

只对白名单计划文件放开。

因此 plan 模式是一个**真正受限执行环境**。

## 8.2 `plan_exit` 工具

`PlanExitTool` 的逻辑是：

1. 问用户：是否切换回 build agent
2. 如果同意：
   - 创建一条新的 synthetic user message
   - `agent = build`
   - 文本提示计划已批准，可以开始执行

这说明 mode 切换不是神秘状态切换，而是通过：

- tool
- 用户确认
- 新 user message

显式驱动的。

## 8.3 这背后的原则

OpenCode 不喜欢隐藏式模式切换。

它倾向于把模式切换变成：

- 对话中的显式事件
- 可持久化的消息状态
- 用户可观察的行为转换

---

# 9. 用户配置如何扩展/覆盖 agent

## 9.1 config 可以修改现有 agent

`cfg.agent` 中的条目会覆盖：

- `model`
- `variant`
- `prompt`
- `description`
- `temperature`
- `topP`
- `mode`
- `color`
- `hidden`
- `name`
- `steps`
- `options`
- `permission`

这意味着内置 agent 不是死的，用户可以按项目需要调优。

## 9.2 config 也可以创建新 agent

如果 `cfg.agent[key]` 对应的 agent 不存在：

- 系统会新建一个：
  - `mode: all`
  - `permission: defaults + user`
  - `native: false`

这意味着 OpenCode 支持“在统一 runtime 里注入项目自己的 agent 模式”。

## 9.3 `disable` 字段

如果某个 agent 配置中：

- `disable: true`

则会直接从结果里删掉。

这给用户充分控制哪些模式在项目中有效。

---

# 10. default agent 的选择逻辑

## 10.1 `defaultAgent()`

默认 agent 选择顺序：

1. 若 config 指定 `default_agent`
   - 必须存在
   - 不能是 subagent
   - 不能 hidden
2. 否则选第一个：
   - `mode !== subagent`
   - `hidden !== true`

这说明“默认 agent”必须是一个面向主会话、用户可见的 agent。

## 10.2 为什么不能把 subagent 设为默认

因为 subagent 的设计前提是：

- 作为某个主任务下的局部执行者
- 常常有较强限制
- 不一定适合直接接管完整用户对话

所以 runtime 明确禁止这种配置错误。

---

# 11. `Agent.list()` 与 agent 展示顺序

`Agent.list()` 会：

- 取所有 agents
- 用 `sortBy` 排序
- 若 config 指定 `default_agent`，则把它排前面
- 否则优先 `build`

这说明 agent 列表不仅是技术配置，也兼顾产品层默认展示优先级。

---

# 12. agent 与 provider/model 的协同

## 12.1 agent 可以指定默认 model

在 `createUserMessage()` 和 loop 里都能看到：

- 用户若没显式指定 model
- 会优先使用 `agent.model`

这意味着 agent 不是 provider-agnostic 的纯行为模板，它也可以绑定适合自己的默认模型。

## 12.2 agent 可以指定 variant

agent 上的 `variant` 能影响：

- reasoning effort
- provider-specific thought mode
- 其他 model option 细化

因此 agent 还能控制推理强度和风格。

## 12.3 agent options 的优先级

在 `LLM.stream()` 中，options merge 顺序里包含：

- `input.agent.options`

这说明 agent 是模型参数调优的一个正式层级。

---

# 13. `Agent.generate()`：agent 生成器

## 13.1 功能

`Agent.generate()` 可以根据自然语言需求生成 agent 配置对象。

输出 schema 包括：

- `identifier`
- `whenToUse`
- `systemPrompt`

## 13.2 实现方式

它会：

- 获取默认模型
- 获取 language model
- 给出 `PROMPT_GENERATE`
- 把当前已有 agent 名列表发给模型，要求不要重名
- 用 `generateObject()` 或 `streamObject()` 生成结构化对象

这说明 OpenCode 不仅支持手写 agent 配置，还支持“由模型帮你起草 agent”。

## 13.3 为什么这也属于 agent 系统的一部分

因为它说明 agent 在 OpenCode 里已经被做成显式可配置对象，而不是藏在代码里的常量 prompt。

---

# 14. 这个模块背后的核心设计原则

## 14.1 agent = 行为模板 + 权限模板 + 模型模板

OpenCode 的 agent 不是 prompt alias，而是行为约束包。

## 14.2 模式切换要显式化

进入 plan、退出 plan、启动 subagent，都尽量通过：

- tool
- message
- session state

显式完成，而不是偷偷改内存状态。

## 14.3 子 agent 应该是受控执行单元

subagent 默认更受限，不应无边界继承主 agent 的全部能力。

## 14.4 prompt 约束不够，必须 runtime enforce

例如 plan 模式禁止编辑，不靠提示词自觉，而靠 permission 强制执行。

这是 OpenCode agent 体系最工程化的一点。

---

# 15. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/agent/agent.ts`
2. `packages/opencode/src/session/prompt.ts`
3. `packages/opencode/src/tool/task.ts`
4. `packages/opencode/src/tool/plan.ts`

重点盯住这些函数/概念：

- `Agent.state()`
- `Agent.get()`
- `Agent.list()`
- `Agent.defaultAgent()`
- `Agent.generate()`
- `mode`
- `steps`
- `permission`
- `plan_exit`

---

# 16. 下一步还需要深挖的问题

这一篇已经把 agent 主框架讲清楚了，但还有一些点值得继续拆开：

- **问题 1**：`PROMPT_EXPLORE`、`PROMPT_COMPACTION`、`PROMPT_SUMMARY`、`PROMPT_TITLE` 的具体内容与行为差异还可以继续细拆
- **问题 2**：agent 的 `options` 如何在不同 provider 下映射成最终 provider options
- **问题 3**：agent `variant` 与 user message `variant` 同时出现时，优先级边界是否完全清晰
- **问题 4**：custom agent 如果设置 `mode: all`，在 task/subagent 场景中的真实行为边界是什么
- **问题 5**：agent `color`、`hidden` 等字段在 UI 层的具体作用还有待继续考察
- **问题 6**：plan mode 的计划文件路径、plan 生命周期与 session 的绑定关系还可继续单独拆解
- **问题 7**：`Agent.generate()` 产出的对象如何落地到真实配置文件，还有哪些验证步骤
- **问题 8**：当多个自定义 agent 权限非常接近时，runtime 是否需要更强的 agent 选择辅助机制

---

# 17. 小结

`agent_definition_and_modes` 模块定义了 OpenCode 的行为模式系统：

- `Agent.Info` 定义 agent 的完整行为包
- `Agent.state()` 构造内置与自定义 agent
- `mode` 决定角色定位
- `permission` 决定能力边界
- `model/variant/options` 决定模型行为
- `steps` 决定 loop 上限
- `task` 和 `plan_exit` 等工具则把 agent 切换与编排变成显式运行时事件

因此，agent 系统是 OpenCode 将“不同工作模式”工程化的关键基础层。
