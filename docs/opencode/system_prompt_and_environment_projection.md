# System Prompt / Environment Projection 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 `SystemPrompt` 体系与环境投影链路。

核心问题是：

- 为什么 system prompt 被拆成 `provider()`、`environment()`、`skills()`、`instructions()` 几层
- 不同 provider / model 为什么要用不同的基础 prompt 模板
- environment prompt 会暴露哪些运行时信息
- skills 信息为什么放进 system prompt，而不是只靠 skill tool 描述
- Codex 为什么把 instructions 放进 provider options，而不是普通 system message

核心源码包括：

- `packages/opencode/src/session/system.ts`
- `packages/opencode/src/session/llm.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/agent/agent.ts`

这一层本质上是 OpenCode 的**system prompt 分层构造与运行环境显式投影基础设施**。

---

# 2. 为什么 system prompt 不能只有一段固定模板

OpenCode 需要同时兼顾几类不同信息：

- provider/model 特有的行为约束
- 当前实例环境信息
- skills 能力目录
- 项目/全局 instruction 文件
- 用户本轮自定义 system
- agent 自定义 prompt

如果把这些全塞进一段写死文本：

- provider 差异难以维护
- 某些段落很难条件化开关
- 缓存粒度差
- 插件也很难只改其中一部分

所以系统选择把 system prompt 分层生成，再在 `LLM.stream()` 中合成。

---

# 3. `SystemPrompt` 的职责边界

`session/system.ts` 里 `SystemPrompt` 很小，但职责非常清晰：

- `instructions()`
- `provider(model)`
- `environment(model)`
- `skills(agent)`

也就是说它专门负责：

- 生成 system 侧静态/半静态 prompt 片段

而不是处理 message history 或 tool runtime。

---

# 4. `provider(model)`：provider/model 家族专属基础 prompt

这里根据 `model.api.id` 选择不同 prompt 模板：

- `gpt-5` -> `PROMPT_CODEX`
- 其他 `gpt-* / o1 / o3` -> `PROMPT_BEAST`
- `gemini-*` -> `PROMPT_GEMINI`
- `claude` -> `PROMPT_ANTHROPIC`
- `trinity` -> `PROMPT_TRINITY`
- 其他默认 -> `PROMPT_ANTHROPIC_WITHOUT_TODO`

## 4.1 为什么按 `model.api.id` 而不是 providerID

因为真正需要区分的往往不是 provider 公司名，而是具体模型家族行为差异。

例如同一 provider 下不同模型的 system prompt 习惯可能完全不同。

---

# 5. 不同 provider prompt 模板意味着什么

这说明 OpenCode 没有假设“一个万能系统提示能让所有模型都表现一样”。

相反，它承认：

- Claude
- Gemini
- GPT/Codex
- Trinity

在工具调用、风格、约束理解上都有差异，必须做 prompt-level 适配。

这是非常现实的工程策略。

---

# 6. `instructions()`：Codex 专用 instruction 入口

`instructions()` 直接返回：

- `PROMPT_CODEX.trim()`

它的主要调用点在 `LLM.stream()`：

- 若 `isCodex`，则 `options.instructions = SystemPrompt.instructions()`

以及 `agent/agent.ts` 某些路径里，也会通过 `ProviderTransform.providerOptions(...)` 注入 instructions。

## 6.1 为什么不是作为普通 system message

源码注释已经明确：

- For Codex sessions, skip `SystemPrompt.provider()` since it's sent via `options.instructions`

这说明某些 provider / API（特别是 Codex/OpenAI responses 风格）对 `instructions` 有专门字段，系统选择走 provider-native 通道，而不是一律塞进 message history。

---

# 7. `environment(model)`：显式环境投影

这部分会生成一段 `<env>` + `<directories>` 风格文本，包含：

- 当前模型名与精确 model ID
- `Working directory`
- `Workspace root folder`
- 当前目录是否 git repo
- `Platform`
- `Today's date`

## 7.1 为什么要显式告诉模型这些信息

因为这类信息会直接影响：

- 路径判断
- shell/tool 行为
- git 相关推理
- 当前任务是否可依赖 VCS

OpenCode 选择把这些环境事实前置成 system context，而不是让模型靠猜。

---

# 8. `environment()` 中的 `<directories>` 为什么基本为空

代码里可以看到：

- `project.vcs === "git" && false ? await Ripgrep.tree(...) : ""`

说明目录树注入逻辑当前被显式关闭了。

## 8.1 这说明什么

作者曾考虑过把目录树直接放进 environment prompt，但当前认为：

- 噪音可能太大
- 成本不划算
- 更适合按需检索，而不是系统提示全量注入

这其实与 OpenCode 整体“先检索再精读”的哲学一致。

---

# 9. `skills(agent)`：为什么 skills 不只靠 tool description 暴露

`skills()` 的逻辑是：

1. 若 `skill` 权限被 agent 禁用，则返回空
2. 否则 `Skill.available(agent)`
3. 返回一段说明：
   - Skills 是 specialized instructions/workflows
   - 任务匹配时应使用 skill tool
   - 再附 `Skill.fmt(list, { verbose: true })`

## 9.1 意义

系统并不满足于“模型能看到 skill tool 名称”。

它还要主动在 system prompt 中告诉模型：

- skill 是什么
- 何时应该用
- 当前有哪些可用 skill

这能显著提升 skill 命中率。

---

# 10. 为什么 `skills()` 要看 permission

它先做：

- `PermissionNext.disabled(["skill"], agent.permission).has("skill")`

说明如果 agent 根本没有 skill 能力，就不应把 skill 信息塞进 system prompt。

这很好地避免了：

- 向模型宣传一个不可用能力
- 增加无意义上下文噪音

这也是 prompt 与 permission 保持一致性的体现。

---

# 11. `LLM.stream()` 中 system prompt 的真正合成顺序

在 `llm.ts` 中，第一层 system 数组是这样构造的：

- 优先 `input.agent.prompt`
- 否则若是 Codex 则跳过 provider prompt
- 否则 `SystemPrompt.provider(input.model)`
- 再拼 `input.system`
- 再拼 `input.user.system`

然后再经过：

- `experimental.chat.system.transform`

## 11.1 含义

这里有三个关键优先级：

- agent prompt 可覆盖 provider prompt
- 调用时显式 system 是额外叠加
- 用户消息携带的 system 是最局部的附加层

这是一套很合理的 prompt 叠加顺序。

---

# 12. `SessionPrompt.loop()` 中的 system 组装

在 normal turn 路径里，`prompt.ts` 还会构造另一层 `system`：

- `SystemPrompt.environment(model)`
- `SystemPrompt.skills(agent)`
- `InstructionPrompt.system()`
- 若 json schema，再加 `STRUCTURED_OUTPUT_SYSTEM_PROMPT`

然后把这组数组传进：

- `processor.process({ ..., system, ... })`

也就是说，最终 LLM 真正看到的 system 由两大块组成：

## 12.1 model-specific/agent-specific header

在 `LLM.stream()` 内部构造。

## 12.2 runtime environment/instructions/skills additions

在 `SessionPrompt.loop()` 先准备，再传给 `LLM.stream()`。

---

# 13. 为什么分成“外层准备 + 内层再合成”两步

这看起来有点绕，但其实有清晰分工：

## 13.1 `SessionPrompt.loop()`

知道：

- 当前 session 历史
- 当前 agent
- 是否 structured output
- 当前 instruction 文件
- 当前 skill availability

## 13.2 `LLM.stream()`

知道：

- provider/model 具体适配
- Codex 特例
- plugin system transform
- option/header 投影

所以两步构造避免了把所有上下文逻辑都塞进某一层。

---

# 14. `experimental.chat.system.transform`：system prompt 仍可被插件重写

在 `LLM.stream()` 里，system 拼完后会进入：

- `Plugin.trigger("experimental.chat.system.transform", { sessionID, model }, { system })`

这说明即便 `SystemPrompt` 已经生成了标准 system 片段，插件仍可：

- 追加
- 替换
- 归并
- 清洗

这让 system prompt 体系既有标准默认，又保留高扩展性。

---

# 15. 为什么 `LLM.stream()` 还要重新“rejoin” system 数组

在 plugin transform 后，有一段逻辑：

- 如果 `system.length > 2` 且 `system[0] === header`
- 就把后面的段合并成第二项

注释写得很清楚：

- `to maintain 2-part structure for caching if header unchanged`

## 15.1 含义

system prompt 的数组分段不是无所谓的。

它会影响：

- provider caching 行为
- prompt fragment 稳定性

所以系统会尽量保持一个稳定 header + dynamic rest 的二段结构。

这是非常细节但很高级的优化。

---

# 16. environment prompt 中的“模型名提示”为什么重要

`environment()` 第一行就告诉模型：

- `You are powered by the model named ...`
- `The exact model ID is providerID/api.id`

这意味着系统希望模型“知道自己是谁”。

这在跨模型 prompt 适配中很有价值，因为不同模型对 tool calling、reasoning、verbosity 的自我约束会不同。

---

# 17. `agent.prompt` 与 `SystemPrompt.provider()` 的关系

在 `LLM.stream()` 里：

- 若 `input.agent.prompt` 存在
- 就不用 `SystemPrompt.provider(input.model)`

这说明 agent prompt 被视为 provider prompt 的强覆盖层，而不是简单附加。

换句话说：

- provider prompt 是通用默认
- agent prompt 是具体 agent persona/contract

agent prompt 优先级更高是合理的。

---

# 18. structured output system prompt 为什么放在 `SessionPrompt.loop()`

因为它依赖的是：

- `lastUser.format`

这是当前回合级别的输出格式要求，而不是 provider/model 的通用属性。

所以这条系统提示属于：

- turn-specific runtime instruction

而不是 `SystemPrompt.provider()` 的固定内容。

---

# 19. skills 与 tool description 的双重暴露策略

源码注释中明确提到：

- agents seem to ingest skills better if we present verbose version here and less verbose version in tool description

这说明系统是有意识地采用“双通道”策略：

- system prompt 给 verbose skill catalog
- tool description 给 concise operational description

这是一种非常经验化但有效的 prompt engineering 设计。

---

# 20. `SystemPrompt.environment()` 与检索哲学的关系

它只投影：

- 目录根信息
- git 状态
- 平台
- 日期

而不强行展开目录树或文件索引。

这说明 OpenCode 的设计原则是：

- system prompt 只放稳定、高价值、低体积环境事实
- 细节结构交给检索工具按需获取

这能很好控制上下文预算。

---

# 21. Codex 路径是 system prompt 体系里的一个特殊分支

对于 Codex：

- 不走 `SystemPrompt.provider(model)` 常规 message path
- 而是 `options.instructions = SystemPrompt.instructions()`

这说明 system prompt 的真正投影目标不总是 `messages[].role = system`。

有时 provider API 提供了更原生的 instruction 通道，OpenCode 会优先使用它。

这也是 provider-aware prompt projection 的体现。

---

# 22. 一个完整的 system prompt 形成过程

可以概括为：

## 22.1 在 `SessionPrompt.loop()` 先准备 runtime additions

- environment
- skills
- instruction files
- structured output rule

## 22.2 在 `LLM.stream()` 再准备 model/agent header

- agent.prompt 或 provider prompt
- call-level custom system
- user-level system

## 22.3 插件最终变换

- `experimental.chat.system.transform`

## 22.4 对特定 provider 走原生 instruction 通道

- Codex `options.instructions`

这就是 OpenCode 的 system prompt 总管道。

---

# 23. 这个模块背后的关键设计原则

## 23.1 system prompt 应按来源和职责分层，而不是一锅煮

provider、environment、skills、instructions、turn-specific rule 是不同层。

## 23.2 稳定信息与高变化信息应分段组织，以利缓存

所以有 header/rest 的二段结构维护。

## 23.3 只把高价值环境事实放进 system prompt

更细节的信息交给检索工具。

## 23.4 provider 若有原生 instruction 通道，应优先利用

Codex 就是典型案例。

---

# 24. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/session/system.ts`
2. `packages/opencode/src/session/prompt.ts`
3. `packages/opencode/src/session/llm.ts`
4. `packages/opencode/src/session/instruction.ts`
5. `packages/opencode/src/skill/index.ts`
6. `packages/opencode/src/agent/agent.ts`

重点盯住这些函数/概念：

- `SystemPrompt.provider()`
- `SystemPrompt.environment()`
- `SystemPrompt.skills()`
- `SystemPrompt.instructions()`
- `experimental.chat.system.transform`
- `STRUCTURED_OUTPUT_SYSTEM_PROMPT`
- `agent.prompt`

---

# 25. 下一步还需要深挖的问题

这一篇已经把 system prompt 与环境投影主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：各 prompt 模板文件（anthropic/gemini/beast/codex/trinity/qwen）的具体内容差异还值得单独逐份解读
- **问题 2**：`Skill.available()` 与 `Skill.fmt()` 的实现细节还值得继续阅读，理解 skill catalog 如何生成
- **问题 3**：header/rest 两段 system caching 到底如何与 provider cache 机制联动，还可以继续追踪 transform 层
- **问题 4**：environment prompt 当前关闭目录树注入，背后的历史原因和性能权衡还值得进一步确认
- **问题 5**：agent.prompt 与 provider prompt 同时存在时为何选择覆盖而不是叠加，这个设计边界还可继续思考
- **问题 6**：Codex `instructions` 通道与普通 system messages 的实际效果差异还值得继续比较
- **问题 7**：structured output 规则若与 instruction 文件冲突，当前优先级如何体现，还可进一步梳理
- **问题 8**：未来多 workspace/remote workspace 场景下，environment 投影是否需要更丰富的 workspace 元信息，也值得关注

---

# 26. 小结

`system_prompt_and_environment_projection` 模块定义了 OpenCode 如何把 provider 适配、运行环境事实、skills 目录与 instruction 文件共同组织成模型可消费的 system context：

- `SystemPrompt.provider()` 负责模型家族特定基础 prompt
- `SystemPrompt.environment()` 负责投影当前工作环境事实
- `SystemPrompt.skills()` 负责把可用 skill 能力目录显式告诉模型
- `SystemPrompt.instructions()` 则为特定 provider（如 Codex）提供原生 instruction 通道内容
- `SessionPrompt.loop()` 与 `LLM.stream()` 共同完成这些 system 片段的分层合成与最终投影

因此，这一层不是简单的系统提示模板，而是 OpenCode 保持跨模型一致行为、环境感知与工具意识的核心 prompt 基础设施。
