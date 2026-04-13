# Config System / Instruction Loading 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的配置系统与 instruction 加载链路。

核心问题是：

- OpenCode 配置从哪里来
- 配置优先级如何定义
- 为什么 `.opencode` 目录不只是一个配置文件目录，而是一个能力扩展目录
- Markdown frontmatter 如何被解析
- instruction 如何从系统级、项目级、路径级进入上下文
- 为什么 instruction 需要去重、claim、延迟注入

核心源码包括：

- `packages/opencode/src/config/config.ts`
- `packages/opencode/src/config/markdown.ts`
- `packages/opencode/src/session/instruction.ts`

这一层本质上是 OpenCode 的**配置解析与规则注入基础设施**。

---

# 2. 配置系统的总体目标

OpenCode 的配置系统显然不是只为“几个简单用户偏好”服务。

从源码看，它同时承载：

- provider 配置
- permission 配置
- agent/mode 配置
- plugin 配置
- command 配置
- instructions 配置
- remote / managed config
- `.opencode` 目录扩展能力
- 依赖安装与本地插件运行准备

这说明 config 在 OpenCode 中是一个**平台级能力装配系统**，而不只是 JSON 读取器。

---

# 3. 配置优先级模型

`Config.state()` 的注释已经把配置加载顺序写得很清楚，低优先级到高优先级大致是：

1. remote `.well-known/opencode`
2. global user config
3. custom config path `OPENCODE_CONFIG`
4. project config `opencode.json/jsonc`
5. `.opencode` 目录中的配置与扩展
6. inline config `OPENCODE_CONFIG_CONTENT`
7. account/org remote config
8. managed config dir（最高优先级）

这是一条非常完整的 **layered config precedence chain**。

## 3.1 为什么需要这么多层

因为 OpenCode 要同时满足：

- 个人用户本地使用
- 仓库级项目约束
- 团队共享默认配置
- SaaS/组织级控制
- 企业托管配置
- SDK/进程级临时注入

所以单一配置文件根本不够。

---

# 4. 配置合并策略

## 4.1 `mergeConfigConcatArrays()`

配置合并并不完全是普通 `mergeDeep`。

对两个字段做了专门拼接去重：

- `plugin`
- `instructions`

这说明 OpenCode 认为这些字段的语义是：

- 多来源累积

而不是“后者完全覆盖前者”。

## 4.2 为什么这是必要的

如果按普通 deep merge：

- plugin 列表容易被后层覆盖掉前层
- instruction 列表也会丢失前层来源

而这两个字段显然更适合“追加汇总”。

所以这里体现出 config 不是盲目通用 merge，而是**字段语义感知 merge**。

---

# 5. 远程 config 与组织级 config

## 5.1 `.well-known/opencode`

如果某个 auth 条目类型为：

- `wellknown`

系统会去请求：

- `<url>/.well-known/opencode`

并把返回 JSON 中的：

- `config`

并入当前配置。

这说明 OpenCode 支持类似组织默认配置中心的能力。

## 5.2 为什么先加载它

远程 `.well-known` 被放在最底层之一，说明它更像：

- organization defaults

而不是强制覆盖用户/项目设置。

## 5.3 account/org config

在后续还会尝试：

- `Account.config(active.id, active.active_org_id)`

并合入结果。

同时还可能注入：

- `OPENCODE_CONSOLE_TOKEN`

这说明 OpenCode 配置体系已经和账号/组织体系发生联动。

---

# 6. 全局、本地、项目、托管配置

## 6.1 global config

通过 `global()` 读取，通常对应：

- `~/.config/opencode/...`

## 6.2 custom config path

若设置：

- `OPENCODE_CONFIG`

则加载指定路径配置。

这为调试、临时脚本、自动化环境提供了非常方便的入口。

## 6.3 project config

当未禁用 project config 时，会通过：

- `ConfigPaths.projectFiles("opencode", Instance.directory, Instance.worktree)`

加载仓库中的 project 级配置。

## 6.4 managed config

managed config dir 根据平台决定：

- macOS: `/Library/Application Support/opencode`
- Windows: `ProgramData\opencode`
- Linux: `/etc/opencode`

并且始终**最后加载**。

这说明它是：

- enterprise admin-controlled override layer

具有最高优先级。

---

# 7. `.opencode` 目录不只是配置文件夹

## 7.1 `ConfigPaths.directories(...)`

state 会枚举配置目录，然后对每个目录做多件事：

- 加载 `opencode.json(c)`
- 处理依赖安装
- 加载 command
- 加载 agent
- 加载 mode
- 加载 plugin

这说明 `.opencode` 目录本质上是一个：

- **局部扩展包目录**

而不是单纯配置文件存放处。

## 7.2 依赖安装

对每个配置目录，还会先检查：

- `needsInstall(dir)`

如果需要，则：

- 改写/生成 `package.json`
- 注入 `@opencode-ai/plugin`
- 创建 `.gitignore`
- `bun install`

这说明本地插件、自定义工具、扩展命令等能力在 OpenCode 看来是“可编程配置资产”，不是纯静态 JSON。

## 7.3 为什么依赖安装放进 config state

因为 `.opencode` 目录里的插件/工具可能依赖 npm 包。

如果只读取配置而不准备运行依赖，后续扩展能力会失效。

所以 config 装载流程天然包含扩展运行环境准备。

---

# 8. 兼容与迁移逻辑

`Config.state()` 里还能看到一些兼容逻辑：

- `mode` 迁移到 `agent`
- legacy `tools` 顶层配置迁移成 `permission`
- `autoshare` 迁移成 `share`
- flag 覆盖 `compaction` 配置

这说明 config 系统承担了：

- **向后兼容迁移层**

它不只是读取新格式配置，也负责吸收历史格式。

---

# 9. `ConfigMarkdown`：Markdown frontmatter 解析基础设施

## 9.1 为什么配置系统需要 Markdown 解析器

OpenCode 中很多“配置型文档”并不是 JSON，而是 Markdown + frontmatter，例如：

- skill
- 可能的 command/template 文档
- instruction 类文档生态

因此需要统一的 markdown config parser。

## 9.2 `parse(filePath)`

流程：

1. 读取文本
2. 尝试 `gray-matter` 正常解析
3. 若失败，尝试 `fallbackSanitization(...)`
4. 仍失败则抛 `FrontmatterError`

这说明 OpenCode 已经考虑到现实世界里，很多外部 agent 文档 frontmatter 可能不严格合法。

## 9.3 `fallbackSanitization()` 的意义

这个函数会对 frontmatter 做一些宽松修正，例如：

- 保留注释与空行
- 对 `key: value` 行做重新处理
- 若 value 含 `:`，转成 block scalar

其目标不是完整 YAML 修复器，而是：

- 尽量兼容常见不严格 frontmatter 文档

这对于兼容 `.claude`、外部 skills、共享模板非常重要。

## 9.4 FILE / SHELL regex

`ConfigMarkdown` 还提供：

- `FILE_REGEX`
- `SHELL_REGEX`

说明 markdown 配置文档除了 frontmatter，还可能支持：

- `@file` 引用
- `!` 命令嵌入

虽然这次没有继续沿调用链深挖，但这表明 markdown 文档在 OpenCode 中并不只是静态文本，而是潜在可解析模板。

---

# 10. `InstructionPrompt`：系统级与局部 instruction 加载器

instruction 系统是 config 与上下文系统之间的桥梁。

## 10.1 支持哪些 instruction 文件名

固定文件名集合：

- `AGENTS.md`
- `CLAUDE.md`
- `CONTEXT.md`（deprecated）

这说明 OpenCode 有意兼容 Claude Code 等生态中常见的 instruction 文件约定。

## 10.2 `systemPaths()`：系统级 instruction 发现

它会从多个来源收集 paths：

- project 路径向上查找 `AGENTS.md/CLAUDE.md/CONTEXT.md`
- 全局 config 目录中的 `AGENTS.md`
- `~/.claude/CLAUDE.md`
- `config.instructions` 中指定的本地文件
- 相对 instruction 路径的 upward glob 解析

最终返回一个 `Set<string>`。

这就是 system prompt 中 instruction 的主要来源集合。

## 10.3 `system()`：把 instruction 正文读出来

在拿到 `systemPaths()` 之后：

- 逐个读本地文件正文
- 对 `config.instructions` 中的 URL 也做 HTTP fetch
- 返回一组字符串：
  - `Instructions from: <path or url>\n<content>`

这些内容随后会进入 system prompt。

这说明 OpenCode 支持：

- 本地 instruction
- 远程 instruction

两种 system 级约束来源。

---

# 11. 路径级 instruction：`resolve(messages, filepath, messageID)`

这是 instruction 体系里最有工程含量的部分之一。

## 11.1 作用

当某个文件被 `read` 时，系统希望顺手把与该路径相关的局部约束也读出来。

但又不能：

- 重复注入
- 注入已经全局注入过的 instruction
- 注入已经在历史 read 中注入过的 instruction

所以需要专门的 resolve 逻辑。

## 11.2 算法

输入：

- 当前 messages
- 被读取文件路径 `filepath`
- 当前 messageID

流程：

1. 获取 system-level instruction paths
2. 获取历史中已通过 read 加载过的 instruction paths
3. 从目标文件所在目录开始一路向上走到 `Instance.directory`
4. 在每一级目录找 `AGENTS.md/CLAUDE.md/CONTEXT.md`
5. 若找到并满足：
   - 不是目标文件自己
   - 不在 system paths 里
   - 不在 already loaded 里
   - 当前 message 尚未 claim
6. 则：
   - `claim(messageID, found)`
   - 读取正文
   - 返回 `Instructions from: ...` 内容

这是一个非常漂亮的 **hierarchical local instruction resolution with dedupe** 算法。

## 11.3 为什么只走到 `Instance.directory`

说明局部 instruction 的作用域是：

- 当前工作目录向下文件的祖先路径链

而不是无限向工作树根或系统根一路爬升。

这使局部规则更贴近当前任务上下文。

---

# 12. `loaded(messages)` 与 claim 机制

## 12.1 `loaded(messages)`

它会扫描历史消息里的 read tool result：

- 只看 `tool === read`
- 状态必须 `completed`
- 从 metadata.loaded 中提取已加载 instruction 路径

这表示 instruction 系统会记住：

- 哪些 instruction 已经随着文件读取进入过上下文

## 12.2 claim 机制

`InstructionPrompt.state()` 维护：

- `claims: Map<messageID, Set<filepath>>`

用于保证：

- 同一轮 message 中，同一个 instruction 文件只注入一次

## 12.3 为什么需要双重去重

- `loaded(messages)`：跨历史去重
- `claim(messageID, filepath)`：同轮去重

两层结合后，instruction 才不会在长对话中疯狂重复出现。

---

# 13. 配置系统与 instruction 系统的关系

两者关系可以概括为：

- `Config` 决定有哪些 instruction 来源
- `InstructionPrompt` 决定这些 instruction 在什么时候、以什么粒度进入上下文

这点很重要，因为它说明：

- instruction 不是单纯配置字段
- 它是一套动态上下文注入机制

---

# 14. 远程 instruction 与超时控制

对于 URL instruction：

- `fetch(url, { signal: AbortSignal.timeout(5000) })`

这说明系统显式给远程 instruction 设置了 5s 超时。

原因很合理：

- system prompt 组装不应被远程资源无限阻塞

这是一个很务实的 runtime 边界控制。

---

# 15. 这个模块背后的关键设计原则

## 15.1 配置是分层叠加的，而不是单文件真理源

OpenCode 支持本地、项目、远程、组织、托管、进程级配置叠加。

## 15.2 扩展能力属于配置系统的一部分

`.opencode` 目录中的 agent/plugin/command/依赖安装都说明：

- config system 也是 extension assembly system

## 15.3 instruction 应分层注入

- system 级 instruction：在对话开始时给整体规则
- 路径级 instruction：在读具体文件时再补局部规则

## 15.4 去重是必要条件

没有 system/already/claim 三层去重，instruction 注入很快会变成噪音源。

## 15.5 现实文档生态并不完美，因此解析器要足够宽容

`fallbackSanitization()` 就体现了这种工程现实主义。

---

# 16. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/config/config.ts`
2. `packages/opencode/src/config/markdown.ts`
3. `packages/opencode/src/session/instruction.ts`
4. `packages/opencode/src/config/paths.ts`

重点盯住这些函数/概念：

- `Config.state()`
- `mergeConfigConcatArrays()`
- `managedConfigDir()`
- `ConfigPaths.projectFiles()`
- `ConfigPaths.directories()`
- `ConfigMarkdown.parse()`
- `fallbackSanitization()`
- `InstructionPrompt.systemPaths()`
- `InstructionPrompt.system()`
- `InstructionPrompt.loaded()`
- `InstructionPrompt.resolve()`

---

# 17. 下一步还需要深挖的问题

这一篇已经把配置与 instruction 主链路讲清楚了，但还有一些点值得继续拆：

- **问题 1**：`ConfigPaths` 的目录搜索算法和 `.opencode` 发现规则还可以继续精读
- **问题 2**：`loadCommand`、`loadAgent`、`loadMode`、`loadPlugin` 的具体实现细节还可以各拆成独立子专题
- **问题 3**：远程 `.well-known/opencode` 与 account/org config 的安全边界、缓存与失败恢复机制还可继续分析
- **问题 4**：`ConfigMarkdown` 中 file/shell regex 的完整消费路径和模板展开语义还值得继续追踪
- **问题 5**：managed config 在企业部署下如何与本地插件/技能能力互相制衡，还可继续研究
- **问题 6**：instruction URL 内容进入 system prompt 后，是否存在签名、完整性、来源信任机制，还需进一步确认
- **问题 7**：config 依赖自动安装的失败恢复、锁策略和并发行为，还可继续展开
- **问题 8**：deprecated `mode` 与 legacy `tools` 迁移逻辑最终会如何淘汰，还值得继续关注

---

# 18. 小结

`config_system_and_instruction_loading` 模块定义了 OpenCode 如何把分散在多个位置的配置、规则和说明文档整合进 runtime：

- `Config.state()` 负责多层配置合并与扩展目录装配
- `ConfigMarkdown` 负责解析带 frontmatter 的 Markdown 配置文档
- `InstructionPrompt.system()` 负责 system 级规则注入
- `InstructionPrompt.resolve()` 负责文件路径相关的局部规则注入
- 去重与 claim 机制则保证注入既完整又不过量

因此，这一层不是普通配置读取逻辑，而是 OpenCode 平台装配与上下文规则注入系统的重要基础设施。
