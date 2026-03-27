# File Access / Boundary Enforcement 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的文件访问边界控制。

核心问题是：

- 为什么几乎所有文件类工具都会先做 `assertExternalDirectory()`
- `Instance.containsPath()` 的边界语义是什么
- `external_directory` 权限和普通 `read/edit/list/lsp` 权限之间是什么关系
- 为什么同一 project 的 worktree 内路径不应被视为外部目录
- 路径逃逸、防误操作和权限询问是如何叠加形成边界控制的

核心源码包括：

- `packages/opencode/src/tool/external-directory.ts`
- `packages/opencode/src/project/instance.ts`
- `packages/opencode/src/tool/read.ts`
- `packages/opencode/src/tool/write.ts`
- `packages/opencode/src/tool/edit.ts`
- `packages/opencode/src/tool/apply_patch.ts`
- `packages/opencode/src/tool/bash.ts`

这一层本质上是 OpenCode 的**文件系统作用域控制与越界权限防线**。

---

# 2. 为什么需要专门的边界控制层

OpenCode 的很多工具都会接触文件系统：

- read
- write
- edit
- apply_patch
- glob
- grep
- ls
- lsp
- bash

如果这些工具只校验“有没有 read/edit 权限”，还不够。

因为还有另一个更关键的问题：

- **你操作的路径是不是当前项目边界之外的路径**

这和：

- 能不能读写某个文件

是两个不同维度。

因此 OpenCode 将其拆成两层：

- 功能权限，如 `read` / `edit` / `list` / `lsp`
- 越界权限，如 `external_directory`

这是非常正确的安全模型。

---

# 3. `assertExternalDirectory()`：统一越界检查入口

这个函数非常小，但几乎是所有文件类工具的共同前置步骤。

核心逻辑是：

1. 若没有 target，直接返回
2. 若显式 `bypass`，直接返回
3. 若 `Instance.containsPath(target)` 为真，直接返回
4. 否则按 file/directory 推 parentDir
5. 组出 glob：`<parentDir>/*`
6. `ctx.ask({ permission: "external_directory", patterns: [glob], always: [glob], metadata })`

## 3.1 这说明什么

它并不直接禁止越界访问。

它做的是：

- **把越界访问提升为一个单独的显式审批动作**

这比简单报错或完全放开都更合理。

---

# 4. `Instance.containsPath()`：边界判断的真正核心

`assertExternalDirectory()` 自己不决定什么算内部路径，真正逻辑在：

- `Instance.containsPath(filepath)`

其语义是：

- 若路径在 `Instance.directory` 内，返回 true
- 否则若 `Instance.worktree === "/"`，返回 false
- 否则若路径在 `Instance.worktree` 内，返回 true

## 4.1 这意味着什么

OpenCode 的内部路径边界并不是单一目录，而是：

- 当前工作目录 `directory`
- 当前项目工作树 `worktree`

共同决定。

这非常重要，因为用户可能在 repo 子目录中工作，但仍应允许访问同一 worktree 内其他路径，而不触发外部目录审批。

---

# 5. 为什么 `worktree === "/"` 要特殊处理

这是边界控制里最关键的安全细节之一。

对于非 git 场景，worktree 可能被设为：

- `/`

如果此时仍把“在 worktree 内”当成内部路径，那么几乎任何绝对路径都会被视为内部路径。

这会直接击穿 `external_directory` 权限边界。

所以源码明确特判：

- 若 `Instance.worktree === "/"`，跳过 worktree contains 判断

这是一条非常重要的 root-cause 级安全防线。

---

# 6. `external_directory` 权限到底控制什么

它控制的不是读写动作本身，而是：

- **访问当前项目目录/工作树之外的目录范围**

换句话说：

- 你能否“越界”到另一个目录树中操作文件

一旦越界，再叠加具体动作权限：

- read
- edit
- list
- lsp
- bash 等

所以完整权限模型是：

- 越界审批
- 动作审批

两层叠加。

---

# 7. 为什么 `external_directory` 用 glob 而不是单文件路径

`assertExternalDirectory()` 生成的 pattern 是：

- `path.join(parentDir, "*")`

而不是具体文件路径。

## 7.1 这意味着批准的是目录级范围

如果用户同意访问某个外部目录，那么该目录下后续操作可以通过规则复用，不必对每个具体文件都重新提问。

## 7.2 为什么这更合理

因为“越界”本身通常是目录级意图：

- 我要进入另一个项目目录看看
- 我要读某个外部生成目录里的若干文件

而不是只针对一个单独文件。

这是很符合人类审批心智模型的设计。

---

# 8. `read` 工具的边界控制顺序

`ReadTool.execute()` 里的顺序非常有代表性：

1. 规范化相对路径到绝对路径
2. `Filesystem.stat(filepath)`
3. `assertExternalDirectory(...)`
4. `ctx.ask({ permission: "read", patterns: [filepath] })`
5. 再做文件不存在、目录读取、图片/PDF、binary/text 等处理

## 8.1 先 external，再 read

这个顺序很合理：

- 先解决“能不能跨目录边界访问”
- 再解决“能不能执行 read 动作”

这是两个不同语义层面的许可。

## 8.2 目录读取也受边界控制

而且 read tool 传 `kind` 时会根据 stat 判断：

- directory 或 file

因此目录读取和文件读取都能正确落到对应的 external dir 审批语义上。

---

# 9. `read` 工具里的 `bypassCwdCheck`

`ReadTool` 在调用 `assertExternalDirectory()` 时支持：

- `bypass: Boolean(ctx.extra?.["bypassCwdCheck"])`

这说明在某些受控内部调用链中，系统允许绕过 cwd 边界检查。

但注意：

- 这是显式传入的内部 override
- 并不是普通用户工具调用默认拥有的能力

所以它仍然是受控例外，而不是后门。

---

# 10. `write` / `edit` / `apply_patch` 的边界控制

这些写类工具也都先做：

- `assertExternalDirectory(ctx, filepath)`

然后才进入：

- `edit` 权限 ask
n- 写入动作
- `File.Event.Edited`
- `FileWatcher.Event.Updated`
- LSP diagnostics

## 10.1 含义

OpenCode 并没有因为某工具本身已经有 `edit` 权限，就放弃外部目录控制。

相反，越界写入被视为需要更高谨慎级别的行为。

这很合理，因为：

- 改项目内文件
- 改另一个目录树中的文件

风险完全不同。

---

# 11. `glob` / `grep` / `ls` / `lsp` 也做目录边界控制

从 grep 结果可以看到这些工具都调用：

- `assertExternalDirectory(...)`

这说明 even read-only 或近似 read-only 的搜索/导航工具，也不能随意跨到外部目录工作。

## 11.1 为什么连搜索也要控制

因为搜索本身就会暴露大量目录内容与文件名信息。

即便不写文件，跨目录检索仍然是敏感能力。

这体现了 OpenCode 的边界模型并不只关注“破坏性操作”，也关注：

- **信息暴露边界**

---

# 12. `bash` 的越界控制比普通文件工具更复杂

`tool/bash.ts` 没直接复用 `assertExternalDirectory()`，而是自己做了一套更复杂的目录提取逻辑。

从 grep 结果可见：

- 它会收集命令涉及的目录
- 若 `cwd` 不在 `Instance.containsPath(cwd)` 内，加入外部目录集合
- 若命令解析出的路径不在内部边界内，也加入集合
- 最终统一 ask：
  - `permission: "external_directory"`
  - `patterns: globs`

## 12.1 为什么 bash 需要单独处理

因为 shell 命令可能同时接触：

- cwd
- 多个显式路径参数
- 目录与文件混合

简单的单 target 检查不够表达其越界面。

这说明 OpenCode 对 shell 的边界控制并没有偷懒，而是按其复杂性单独处理。

---

# 13. 文件模块自身的路径防线：`Instance.containsPath()`

除了工具层 ask 之外，`File.read()` / `File.list()` 自身也会检查：

- `if (!Instance.containsPath(full)) throw new Error("Access denied: path escapes project directory")`

## 13.1 为什么还要在文件模块里再做一次

因为工具层 ask 属于权限审批逻辑。

而 `File` 模块是底层文件访问 API，本身也必须具备不变量：

- 不允许路径逃逸出项目边界

这是典型的 defense in depth：

- 上层审批
- 下层硬边界

双保险。

---

# 14. 这些 TODO 注释说明了什么边界风险

在 `File.read()` / `File.list()` 中可以看到两条 TODO：

- `Filesystem.contains` 只是 lexical 检查，symlink 可能逃逸
- Windows 跨盘路径也可能绕过

这非常重要，因为它说明当前实现虽然已有边界控制，但作者也清楚：

- 这还不是最终最强形式的 canonical path 安全

这类注释对文档很有价值，因为它明确指出了：

- 已知边界风险
- 后续可能的 root-cause 改进方向

---

# 15. Agent 默认权限中如何处理 `external_directory`

从 `agent/agent.ts` grep 结果可见，agent 默认 permission rules 里明确包含：

- `external_directory: { "*": "ask", ...whitelistedDirs }`

有些 agent 还会对特定路径（如 plans 目录）设置 allow。

## 15.1 这说明什么

`external_directory` 不是临时特例权限，而是 agent permission system 的一等公民。

也就是说，是否允许越界访问，本来就是 agent 能力面的一部分。

## 15.2 Truncate 输出目录的 deny 规则

grep 还显示 agent 代码里会特别检查 `external_directory` 的 deny 是否针对：

- `Truncate.GLOB`

说明系统对 tool-output 这类目录也在权限模型里单独考虑过。

---

# 16. 为什么工作树内路径不应触发 `external_directory`

这是整个设计的核心原则之一。

用户虽然可能从：

- `repo/packages/foo`

进入系统，但同一个 repo 下的：

- `repo/packages/bar`
- `repo/.opencode/...`
- `repo/docs/...`

本质上仍属于当前项目边界。

如果这些都要触发 external_directory 询问：

- UX 会极差
- 项目级操作几乎无法顺畅进行
- snapshot/LSP/config/instruction 等大量逻辑都会变得别扭

所以 OpenCode 用 `directory + worktree` 的双边界模型，确保：

- worktree 内访问被视为项目内访问

这是非常正确的产品和架构选择。

---

# 17. 为什么越界审批 metadata 要带 `filepath` / `parentDir`

`assertExternalDirectory()` 会把这些信息塞进：

- `metadata.filepath`
- `metadata.parentDir`

这说明审批系统不是只看抽象 glob，而是保留足够上下文，方便：

- UI 展示真实目标路径
- 规则记录更可解释
- 日志/审计更准确

这对安全可观测性很重要。

---

# 18. 完整边界模型可以怎么理解

可以把 OpenCode 的文件访问边界拆成三层：

## 18.1 作用域层

- `Instance.directory`
- `Instance.worktree`
- `Instance.containsPath()`

定义什么算内部路径。

## 18.2 审批层

- `assertExternalDirectory()`
- `ctx.ask({ permission: "external_directory" ... })`

定义越界是否允许。

## 18.3 动作层

- `read` / `edit` / `list` / `lsp` / `bash`

定义在允许范围内具体能做什么。

这种三层设计非常清晰。

---

# 19. 这个模块背后的关键设计原则

## 19.1 越界与动作是两个不同权限维度

不能只靠 read/edit 权限覆盖 external directory 风险。

## 19.2 项目边界应以 `directory + worktree` 联合定义

这样既保留当前工作目录语义，也保留项目级访问便利性。

## 19.3 底层文件模块也应保有硬边界

不应完全依赖上层工具审批。

## 19.4 已知边界弱点要显式暴露，而不是假装完美

symlink / cross-drive TODO 就是很好的例子。

---

# 20. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/tool/external-directory.ts`
2. `packages/opencode/src/project/instance.ts`
3. `packages/opencode/src/tool/read.ts`
4. `packages/opencode/src/tool/write.ts`
5. `packages/opencode/src/tool/edit.ts`
6. `packages/opencode/src/tool/apply_patch.ts`
7. `packages/opencode/src/tool/bash.ts`
8. `packages/opencode/src/file/index.ts`
9. `packages/opencode/src/agent/agent.ts`

重点盯住这些函数/概念：

- `assertExternalDirectory()`
- `Instance.containsPath()`
- `external_directory`
- `ctx.ask(...)`
- `Access denied: path escapes project directory`
- `bypassCwdCheck`

---

# 21. 下一步还需要深挖的问题

这一篇已经把文件边界与越界审批主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`Filesystem.contains()` 的具体实现是否能被 realpath/canonical path 替换，以彻底解决 symlink 逃逸问题
- **问题 2**：Windows 跨盘路径与 UNC 路径在当前边界模型下的真实行为还值得专门验证
- **问题 3**：bash 路径提取逻辑对复杂 shell 语法的覆盖率还需要进一步评估
- **问题 4**：`bypassCwdCheck` 的所有调用点还值得继续梳理，确认是否都足够安全
- **问题 5**：worktree 内但 directory 外的访问在 UI 审批展示上是否足够可解释，还值得从 UX 角度继续思考
- **问题 6**：`external_directory` 规则与 agent mode/subtask 间是否有特殊组合逻辑，还可继续查 permission 调用链
- **问题 7**：tool-output、plans 等系统目录的白名单/黑名单策略还可以继续系统化总结
- **问题 8**：未来若支持 remote workspace/file bridge，当前本地路径边界模型是否需要扩展，也值得预先观察

---

# 22. 小结

`file_access_and_boundary_enforcement` 模块定义了 OpenCode 如何在保留项目级操作流畅性的同时，防止文件系统越界访问变成隐式默认行为：

- `Instance.containsPath()` 定义内部路径边界
- `assertExternalDirectory()` 把越界访问提升为单独审批维度
- 各类文件与搜索工具在执行具体动作前都会先过越界检查
- `File` 模块自身仍保留底层硬边界防线

因此，这一层不是简单路径拼接检查，而是 OpenCode 文件系统安全模型、权限语义和项目作用域边界共同组成的核心基础设施。
