# Skill System 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 skill 体系。

它回答的问题是：

- skill 是什么
- 为什么 skill 不等于普通 prompt 模板
- skill 从哪里被发现
- skill 如何进入 system prompt
- skill 为什么要按需加载而不是一次全塞给模型
- skill 与工具、权限、上下文系统是如何协作的

核心源码包括：

- `packages/opencode/src/skill/skill.ts`
- `packages/opencode/src/tool/skill.ts`
- `packages/opencode/src/session/system.ts`
- `packages/opencode/src/permission/next.ts`

skill 系统本质上是 OpenCode 的**可发现、可权限控制、可懒加载的领域知识与工作流注入机制**。

---

# 2. Skill 的角色定位

## 2.1 skill 不是普通工具

普通工具做的事通常是：

- 读文件
- 搜索代码
- 执行 shell
- 修改代码

而 skill 做的事是：

- 给模型加载一份专门领域说明书
- 提供更详细的任务流程、约束、脚本、模板、参考材料入口

所以 skill 更像是：

- **按需加载的操作手册 / 领域工作流包**

## 2.2 skill 也不是全局 system prompt

如果把所有 skill 全部一开始塞进 system prompt，会出现几个问题：

- 上下文太大
- 多数 skill 对当前任务无关
- 会显著干扰模型注意力
- skill 越多，噪音越大

所以 OpenCode 的设计是：

- 先把 skill 的目录索引告诉模型
- 需要时再用 `skill` tool 加载正文

这是一种典型的 **index first, content later** 策略。

---

# 3. Skill 元数据与内容模型

## 3.1 `Skill.Info`

`skill.ts` 中定义了 skill 的基本结构：

- `name`
- `description`
- `location`
- `content`

这说明在 runtime 看来，一个 skill 最核心的是：

- 能被唯一命名
- 有可供匹配的描述
- 有来源位置
- 有实际正文

## 3.2 为什么 `name` 与 `description` 这么重要

因为模型在决定“该不该加载 skill”时，最先看到的就是 skill 索引。

也就是说：

- `name` 决定可调用标识
- `description` 决定语义匹配信号

skill 是否能被正确选中，很大程度取决于这两个字段的质量。

---

# 4. Skill 发现机制：`Skill.state()`

## 4.1 总体思路

`Skill.state()` 会扫描多个来源，把所有 skill 收集进内存状态：

- 外部 skill 目录
- `.opencode/skill` 或 `.opencode/skills`
- config 指定路径
- config 指定 URL 拉取到本地后的目录

最终构造成：

- `skills: Record<string, Info>`
- `dirs: string[]`

这是一种 **multi-source skill discovery pipeline**。

## 4.2 支持哪些目录来源

### 外部目录

外部目录常量包括：

- `.claude`
- `.agents`

扫描模式：

- `skills/**/SKILL.md`

说明 OpenCode 会兼容其他 agent/工具常见的 skill 目录布局。

### OpenCode 自己的目录

扫描模式：

- `{skill,skills}/**/SKILL.md`

通常来自：

- `Config.directories()` 返回的配置目录

### config 里显式声明的路径

- `config.skills.paths`

### config 里声明的远程 URL

- `config.skills.urls`

远程 URL 会先通过：

- `Discovery.pull(url)`

拉到本地，再扫描其中的 `SKILL.md`。

这说明 skill 生态并不局限于仓库内，还支持远程分发。

---

# 5. Skill 解析与注册算法

## 5.1 `addSkill(match)` 的流程

当扫描到一个 `SKILL.md` 文件时，系统会：

1. 用 `ConfigMarkdown.parse(match)` 解析 frontmatter + markdown 正文
2. 只提取 frontmatter 里的：
   - `name`
   - `description`
3. 若解析失败，发 bus error + log
4. 若成功，则加入 `skills[name]`
5. 同时记录 skill 所在目录到 `dirs`

这里说明 skill 的文件格式本质上是：

- 带 frontmatter 的 markdown 配置文档

## 5.2 为什么要通过 frontmatter 解析

这样 skill 的正文可以保持 markdown 自然表达能力，而元信息又能被 runtime 稳定提取。

相比在正文里靠正则找标题，这种方式更稳、更可验证。

## 5.3 重名 skill 的处理

如果两个 skill 使用同名：

- runtime 不会立刻 hard fail
- 会 `log.warn("duplicate skill name", ...)`
- 后者会覆盖前者

这说明当前策略是：

- **允许覆盖，但发警告**

它的实际效果是：

- 项目级 skill 可以覆盖全局 skill
- 自定义 skill 可以覆盖已有 skill

这与“项目优先于全局”的设计哲学一致。

---

# 6. 搜索顺序与覆盖语义

## 6.1 为什么先扫 global 再扫 project

源码里明确写了：

- 先扫 home 下的外部目录
- 再从当前项目路径向上扫 project 级目录

这意味着：

- project-level skill 会覆盖 global skill

这是很合理的优先级：

- 全局 skill 提供通用工作流
- 项目 skill 提供 repo 特定工作流
- 项目语义优先级更高

## 6.2 config path / URL 的位置

这些来源在 OpenCode skill 目录扫描之后追加处理。

这意味着 skill 来源优先级是一个“后扫后覆盖”的模型。

这是典型的 **ordered override resolution**。

---

# 7. Skill 的可见性控制：`available(agent)`

## 7.1 不是所有 agent 都能看到全部 skill

`Skill.available(agent)` 的逻辑是：

- 如果没有 agent，返回全部 skill
- 如果有 agent，则过滤掉：
  - `PermissionNext.evaluate("skill", skill.name, agent.permission).action === "deny"`

因此 skill 的可见性本身受权限系统控制。

这点非常重要，因为它说明：

- skill 不是纯内容目录
- 它也是 runtime 能力面的一部分

## 7.2 为什么这很重要

不同 agent mode 可能有不同定位：

- 有些 agent 只该做通用规划
- 有些 agent 才适合加载某些专业 workflow

通过 permission 先裁掉不可见 skill，可以显著降低模型误用 skill 的概率。

---

# 8. Skill 索引如何进入 system prompt

## 8.1 `SystemPrompt.skills(agent)`

这个函数会：

1. 检查 `skill` 工具是否整体被 permission 禁用
2. `Skill.available(agent)` 获取当前 agent 可见的 skill 列表
3. 返回一段 system prompt 文本，内容包括：
   - skill 是什么
   - 什么时候该用 skill tool
   - 可用 skill 的格式化列表

## 8.2 为什么 system 里用 verbose 版本

源码中有明确注释：

- 在 system prompt 里，agent 对 verbose 版 skill 列表吸收得更好
- tool description 中则用 less verbose 版本

这说明 OpenCode 对 prompt 工程做了经验型优化：

- 同样的信息，在 system 与 tool description 中用不同密度表达

## 8.3 `Skill.fmt()` 的两种模式

### verbose 模式

输出：

```xml
<available_skills>
  <skill>
    <name>...</name>
    <description>...</description>
    <location>file://...</location>
  </skill>
</available_skills>
```

### 非 verbose 模式

输出更接近列表摘要：

- `- **name**: description`

这是一种 **dual representation strategy**：

- system 里给结构化索引
- tool description 里给简洁摘要

---

# 9. `SkillTool`：按需加载 skill 正文

## 9.1 为什么要单独有一个 tool

skill 系统的关键设计不是“我有 skills”，而是“我可以按需加载 skill 内容”。

这就是 `SkillTool` 存在的意义。

它是从 skill 索引到 skill 正文的桥梁。

## 9.2 tool description 如何构造

`SkillTool` 初始化时会：

- 拿 `Skill.available(ctx?.agent)`
- 如果没有 skill，就写无可用 skill 的说明
- 如果有 skill，就在 description 中解释：
  - 这是专门领域 workflow
  - 识别到任务匹配时应调用此 tool
  - tool output 会包含 `<skill_content name="...">` block
  - 当前有哪些可用 skill

这让模型在看到工具时就知道：

- 它不是通用搜索工具
- 它是用于加载专门 instruction 的

## 9.3 参数结构

参数只有一个：

- `name`

这说明 skill 选择逻辑主要在模型侧完成，runtime 侧只负责校验和加载。

---

# 10. `SkillTool.execute()` 的执行流程

## 10.1 查 skill

首先：

- `Skill.get(params.name)`

若找不到，则返回：

- 可用 skill 名列表

这是一种直接明了的错误恢复方式。

## 10.2 权限审批

然后调用：

- `ctx.ask({ permission: "skill", patterns: [params.name], always: [params.name] })`

这意味着：

- 加载某个 skill 是要审批的
- 不是只要 skill 可见就一定可直接加载

这里体现了两层控制：

- **可见性控制**：`available(agent)`
- **执行审批控制**：`ctx.ask()`

## 10.3 skill 目录与资源采样

skill 加载后，会取：

- `dir = path.dirname(skill.location)`
- `base = pathToFileURL(dir).href`

并用 `Ripgrep.files()` 在 skill 目录下采样最多 10 个文件，排除 `SKILL.md` 本身。

这是一个很实用的设计：

- skill 不只是正文
- 还经常配套脚本、参考文档、模板文件

通过把这些资源路径一起回传，模型就知道后续可去哪读更多上下文。

## 10.4 输出格式

最终输出形如：

```xml
<skill_content name="...">
# Skill: ...
...
Base directory for this skill: file://...
<skill_files>
<file>...</file>
</skill_files>
</skill_content>
```

这说明 skill tool 输出不是普通一句摘要，而是**结构化上下文块**。

它会直接作为下一轮上下文的一部分，被模型再次看到。

---

# 11. 为什么 skill 是懒加载上下文系统

## 11.1 基本思想

skill 的内容可能非常长，甚至包含一整套工作流说明。

如果所有 skill 一次性进入上下文：

- token 成本很高
- 注意力被稀释
- 大量内容与当前任务无关

因此 OpenCode 把 skill 体系设计成两阶段：

### 第一阶段：索引进入 system

系统告诉模型：

- 有哪些 skill
- 每个 skill 大致做什么

### 第二阶段：正文进入 tool result

只有在模型主动调用 `skill(name)` 时，正文才进入上下文。

这就是非常典型的 **lazy context loading**。

## 11.2 为什么这比静态注入更好

因为它使得上下文成本与任务复杂度更匹配：

- 任务简单时，不需要 skill 正文
- 任务专业时，才按需扩展上下文

---

# 12. Skill 与权限系统的关系

## 12.1 可见性层

`Skill.available(agent)` 使用：

- `PermissionNext.evaluate("skill", skill.name, agent.permission)`

因此 skill 列表本身就会被裁剪。

## 12.2 执行层

`SkillTool.execute()` 再次使用：

- `ctx.ask(permission: "skill", patterns: [skill name])`

因此 skill 使用是两阶段控制：

- **能不能看见**
- **能不能真正加载**

## 12.3 为什么不只做一层控制

如果只有执行审批：

- 模型会看到大量其实不可用 skill，容易误选

如果只有可见性过滤：

- 用户就失去对具体 skill 加载行为的二次控制

两层结合更合理。

---

# 13. Skill 与上下文系统的关系

## 13.1 skill 正文如何进入后续上下文

skill tool 调用完成后，会生成一个 `ToolPart`。

下一轮 loop 开始时：

- `MessageV2.toModelMessages()` 会把这个 tool result 回放给模型

因此 skill 内容并不是“瞬时读一下就没了”，而是会成为后续会话上下文的一部分。

## 13.2 skill 与检索工具的配合

skill tool 返回的内容里还包含：

- `Base directory for this skill: ...`
- sampled file list

这意味着模型可以在后续继续使用：

- `read`
- `glob`
- `grep`

去读取 skill 目录中的脚本、模板和参考文件。

所以 skill 不是孤立文本，而是一个**上下文入口点**。

---

# 14. Skill 与外部生态的兼容性

## 14.1 支持 `.claude` / `.agents`

这说明 OpenCode skill 系统有意兼容其他 agent 工具生态的目录布局。

这很重要，因为很多团队已经有既有 skill/agent 资产。

OpenCode 并没有强迫这些资产必须重写成完全新的目录标准。

## 14.2 支持远程 URL 分发

通过：

- `config.skills.urls`
- `Discovery.pull(url)`

系统允许 skill 从远程分发源同步下来。

这说明 skill 系统不仅是本地 repo 功能，也具备潜在的共享与分发能力。

---

# 15. 技术设计上的几个关键算法与策略

## 15.1 多源扫描

skill 来源来自多处，系统通过统一扫描流程收敛为单一索引。

这是 **multi-source discovery**。

## 15.2 有序覆盖

global -> project -> config path -> pulled URL 等来源按顺序覆盖。

这是 **ordered override resolution**。

## 15.3 权限裁剪

可见性与执行权限分离。

这是 **visibility + execution dual gate**。

## 15.4 索引与正文分离

skill 索引先进入 system，正文后进入 tool result。

这是 **index/body separation**。

## 15.5 资源目录显式暴露

skill 不只返回正文，还显式暴露基础目录与文件采样。

这是 **resource-aware prompt injection**。

---

# 16. 这个模块背后的核心设计原则

## 16.1 把工作流当成一等上下文资源

skill 不是普通文档附件，而是专门为 agent 协作准备的工作流包。

## 16.2 内容按需进入上下文

OpenCode 不追求“把所有潜在有用知识都塞进去”，而是追求：

- 让模型知道有什么
- 再按需加载具体内容

## 16.3 skill 也是受控能力面

skill 不仅是知识资源，也是 agent 能力面的一部分，因此必须受权限系统控制。

## 16.4 skill 要可发现、可复用、可扩展

通过：

- 兼容外部目录布局
- 支持 config path
- 支持远程 URL
- 支持覆盖

OpenCode 把 skill 体系做成了可扩展生态，而不是硬编码内置模板。

---

# 17. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/skill/skill.ts`
2. `packages/opencode/src/tool/skill.ts`
3. `packages/opencode/src/session/system.ts`
4. `packages/opencode/src/permission/next.ts`

建议重点看这些函数/概念：

- `Skill.state()`
- `addSkill()`
- `Skill.available()`
- `Skill.fmt()`
- `SystemPrompt.skills()`
- `SkillTool.execute()`
- `PermissionNext.evaluate()`
- `ctx.ask()`

---

# 18. 下一步还需要深挖的问题

这一篇已经把 skill 主体讲清楚了，但还有一些值得继续深挖的问题：

- **问题 1**：`Discovery.pull(url)` 的拉取、缓存、更新策略是什么
- **问题 2**：`ConfigMarkdown.parse()` 对 skill frontmatter 的校验边界与错误恢复策略是什么
- **问题 3**：skill 目录中的脚本、模板、引用资源，在后续工具调用中是否有专门约定或最佳实践
- **问题 4**：当 skill 正文非常长时，tool output truncation 会如何影响 skill 使用效果
- **问题 5**：重复 skill 名覆盖时，是否需要更显式的优先级或冲突提示机制
- **问题 6**：skill 与 agent mode 之间是否存在更细粒度耦合，例如某些 skill 只适用于特定子 agent
- **问题 7**：远程 skill URL 的安全模型和信任边界是什么
- **问题 8**：skill 内容中如果引用相对路径、脚本命令、模板变量，runtime 是否存在统一解析约定

---

# 19. 小结

`skill_system` 模块定义了 OpenCode 如何把“专业工作流知识”做成可管理、可发现、可权限控制、可按需加载的上下文资源：

- `Skill.state()` 负责多源发现与索引建立
- `SystemPrompt.skills()` 负责把索引放进 system prompt
- `SkillTool` 负责按需加载正文与资源目录
- `PermissionNext` 负责可见性与执行控制
- tool result 回流机制则负责把 skill 内容持续注入后续上下文

因此，skill 系统不是附加说明文档功能，而是 OpenCode context engineering 与能力扩展体系的重要一环。
