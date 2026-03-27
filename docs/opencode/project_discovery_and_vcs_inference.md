# Project Discovery / VCS Inference 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 如何从一个目录推导出 project、worktree、sandbox 与 VCS 信息。

核心问题是：

- `Project.fromDirectory()` 到底做了什么
- 为什么 project ID 不是简单用路径 hash
- git worktree / common dir 场景如何处理
- 非 git 目录为什么会落到 `ProjectID.global`
- sandbox 与 worktree 为什么可能不同
- project 初始化后如何影响 session 迁移、事件广播和 bootstrap 行为

核心源码包括：

- `packages/opencode/src/project/project.ts`
- `packages/opencode/src/project/vcs.ts`
- `packages/opencode/src/project/bootstrap.ts`
- `packages/opencode/src/project/project.sql.ts`

这一层本质上是 OpenCode 的**项目身份识别与版本控制环境推导系统**。

---

# 2. 为什么 project 发现不能只靠当前目录路径

OpenCode 需要的不是“当前 cwd 是什么”，而是：

- 这个目录属于哪个 project
- 这个 project 的稳定 identity 是什么
- 它的 worktree 根在哪
- 当前目录是不是某个 sandbox/worktree 变体
- 它是不是 git project

如果只用目录路径做 project ID，会有很多问题：

- 同一仓库不同子目录会被错误当成不同 project
- git worktree 会被错误拆成多个 project
- 仓库重命名或移动路径会改变 identity
- 没法把 pre-git session 迁移到正式 project

因此 project 发现必须依赖更稳定的来源，而不是目录字符串本身。

---

# 3. `Project.Info`：项目的持久化身份模型

`Project.Info` 包括：

- `id`
- `worktree`
- `vcs?`
- `name?`
- `icon?`
- `commands?`
- `time`
- `sandboxes`

## 3.1 `id`

project 的核心稳定标识。

## 3.2 `worktree`

项目的主工作树根。

## 3.3 `sandboxes`

说明同一个 project 可以记录多个 sandbox 目录，而不是只有一个入口目录。

## 3.4 `time_initialized`

虽然不在 `Info` 顶层直接命名，但通过 `time.initialized` 可见，项目还有“已初始化”阶段状态。

这意味着 project 不只是静态元数据，也有生命周期语义。

---

# 4. `ProjectTable`：为什么字段这么少却足够

`project.sql.ts` 的表结构很简洁：

- `id`
- `worktree`
- `vcs`
- `name`
- `icon_url`
- `icon_color`
- `time_initialized`
- `sandboxes`
- `commands`
- timestamps

这说明 Project 层更关心：

- 稳定身份
- 工作树根
- UI 辅助信息
- sandbox 列表
- 项目级命令

而不想把太多运行时状态堆进 project 表。

这是合理的分层选择。

---

# 5. `fromDirectory(directory)`：整个发现算法的入口

这是 project 体系的核心函数。

整体可以分成两条主分支：

- 发现 `.git` -> git project 路径
- 未发现 `.git` -> global/non-git 路径

---

# 6. 第一步：向上查找 `.git`

`fromDirectory()` 会：

- `Filesystem.up({ targets: [".git"], start: directory })`

找到最近的 `.git`。

## 6.1 含义

这说明 project 发现并不是只检查当前目录，而是沿祖先链向上查找 git 边界。

这正符合真实开发场景：

- 你常常在 repo 子目录里启动 agent

## 6.2 找到 `.git` 之后

先令：

- `sandbox = path.dirname(dotgit)`

也就是说：

- 找到 `.git` 所在目录的父目录，先作为当前 sandbox 候选

---

# 7. 没有 git binary 时的退化路径

如果系统里没有 `git` 可执行文件：

- 返回 `id = cached id 或 ProjectID.global`
- `worktree = sandbox`
- `sandbox = sandbox`
- `vcs = Flag.OPENCODE_FAKE_VCS`

这说明 OpenCode 在“目录里有 `.git`，但环境没有 git 命令”时仍尽量维持 project 语义，而不是完全失败。

不过它不会强行声称是真正 git fully-capable 环境，而是退化到 fake VCS 标记。

---

# 8. `git rev-parse --git-common-dir`：worktree/common dir 推导

这是支持 git worktree 场景的关键。

## 8.1 目的

普通仓库和 git worktree 的 `.git` 结构不一样：

- 普通仓库：`.git` 是目录
- worktree：`.git` 可能是指向 common dir 的文件/引用

因此不能简单把 `.git` 的父目录当最终 worktree。

## 8.2 算法

- 执行 `git rev-parse --git-common-dir`
- 用 `gitpath()` 清理输出并转绝对路径
- 如果 common dir 不是 sandbox 本身，则取其父目录作为 `worktree`

这说明 OpenCode 明确想把：

- 多个 git worktree
- 同一个 common git 历史

映射到同一个 project identity 上。

这是非常重要的设计点。

---

# 9. project ID 的来源优先级

`fromDirectory()` 中 project ID 的获取顺序大致是：

1. 读取 `.git/opencode` 缓存 ID
2. 若是 worktree，再尝试从 common worktree 的 `.git/opencode` 读
3. 若还没有，则用 root commit 推导
4. 若仍失败，退回 `ProjectID.global`

这是一条很清晰的稳定身份推导链。

---

# 10. 为什么用 `.git/opencode` 缓存 ID

`readCachedId(dir)` 会读取：

- `<dir>/opencode`

也就是把 project ID 缓存在 git 元数据旁边。

## 10.1 这样做的价值

- 同一个仓库后续启动时无需每次重新算 ID
- 多个 git worktree 可以共享 common dir 上的缓存
- project ID 不依赖数据库本地状态，具有可重复恢复性

这比单纯把 project ID 只存在 SQLite 里更稳健。

---

# 11. 为什么用 root commit 生成 project ID

如果没有缓存 ID，则会执行：

- `git rev-list --max-parents=0 HEAD`

拿到根提交列表，再排序取首个，生成 `ProjectID`。

## 11.1 这个选择的优点

根 commit 通常能较稳定地代表一个仓库谱系：

- 仓库换路径，ID 不变
- 同仓库多个子目录，ID 不变
- worktree 共享历史，ID 不变

## 11.2 边界

当然，如果仓库被完全 rewrite history，根 commit 也会变。

但相对于路径 hash，这已经是更合理的稳定 identity 来源。

---

# 12. 为什么还要写回 `.git/opencode`

拿到 root commit 推导的 ID 后，源码会：

- 写到 common worktree 的 `.git/opencode`

并特别注释：

- 写到 common dir 是为了跨 worktree 共享 cache

这说明 OpenCode 非常明确地把“同一 git 仓库的多个 worktree”视为同一 project，而不是多个 project。

---

# 13. `git rev-parse --show-toplevel`：sandbox 的最终修正

在拿到 project ID 和 worktree 后，还会再调用：

- `git rev-parse --show-toplevel`

然后：

- `sandbox = top`

## 13.1 含义

这表示：

- `sandbox` 不是简单“找到 `.git` 的父目录”就算了
- 它最终会被修正成 git 认定的当前 worktree 顶层目录

这对于嵌套目录、符号链接、worktree 变体路径都更稳健。

---

# 14. 没有 `.git` 时为什么用 `ProjectID.global`

如果向上找不到 `.git`：

- `id = ProjectID.global`
- `worktree = "/"`
- `sandbox = "/"`
- `vcs = Flag.OPENCODE_FAKE_VCS`

## 14.1 这代表什么

OpenCode 明确允许非 git 场景存在，但会把它们放在：

- 全局 project 身份下

这与前面 `Instance.containsPath()` 对 `worktree === "/"` 的特殊处理是呼应的。

## 14.2 为什么不直接报错

因为用户仍然可能希望在非 git 目录里使用：

- read/glob/grep
- prompt/chat
- 写文件
- todo

只是某些依赖 git 的高级能力会降级。

---

# 15. `fromDirectory()` 之后的数据库 upsert

发现逻辑算出 `data` 后，会：

1. 读取现有 `ProjectTable` 行
2. 若无则创建默认 project info
3. 更新：
   - worktree
   - vcs
   - time.updated
   - sandboxes
4. `insert ... onConflictDoUpdate`

这说明 project 发现不是纯计算函数，它还是：

- **project registry maintenance path**

也就是每次启动/进入目录，都顺手确保数据库里的 project 记录是最新的。

---

# 16. `sandboxes`：为什么 project 需要记住多个 sandbox

如果：

- `data.sandbox !== result.worktree`
- 且未记录过

就会把 `sandbox` 追加到 `result.sandboxes`。

随后还会：

- 过滤掉已不存在的目录

## 16.1 含义

同一个 project 下，OpenCode 想记住它曾经关联过哪些 sandbox/worktree 入口。

这对以下场景都很有价值：

- 多 worktree
- 从不同子目录进入同一 project
- 控制面展示多个可选目录视角

---

# 17. session 从 global project 迁移到正式 project

这是 `fromDirectory()` 中最容易被忽略、但非常关键的一段。

如果新发现的 `data.id !== ProjectID.global`：

- 会把 `SessionTable` 中：
  - `project_id == ProjectID.global`
  - `directory == data.worktree`
- 的 session 迁移到：
  - `project_id = data.id`

## 17.1 为什么这么做

这解决了一种真实场景：

- 某目录最初还不是 git 仓库
- 用户已在里面产生 session
- 后来目录初始化为 git 仓库
- project 应该从 global 升级成稳定 git project

如果不做迁移，这些 session 会永久挂错 project。

这说明 OpenCode 在 project identity 演进上考虑得非常周到。

---

# 18. `Project.Event.Updated` 与全局广播

`fromDirectory()` 完成后会：

- `GlobalBus.emit("event", { payload: { type: project.updated, properties: result } })`

`update()`、`addSandbox()`、`removeSandbox()` 也都会发同样事件。

这说明 project 元信息是全局观察面的一等资产，而不是纯数据库后台数据。

UI、控制面和插件都可以实时感知 project 更新。

---

# 19. `discover(input)`：实验性的 icon 发现

如果开启：

- `OPENCODE_EXPERIMENTAL_ICON_DISCOVERY`

且：

- project 是 git
- 没有手动 icon override
- 还没有 icon url

则会扫描：

- `**/favicon.{ico,png,svg,jpg,jpeg,webp}`

取最短路径那个文件，转成 data URL，写回 project icon。

## 19.1 这说明什么

Project 不是只服务后端逻辑，也服务 UI 展示层。

icon discovery 是一个很产品化的小功能，但也体现出 project 元数据的扩展潜力。

---

# 20. `initGit()`：把 global/non-git project 升级成 git project

`Project.initGit({ directory, project })` 的逻辑是：

- 如果已经是 git，直接返回
- 否则要求系统有 git
- 在目录下 `git init --quiet`
- 然后重新 `fromDirectory(directory)`

这说明 project VCS 不是不可变属性，而可以在运行时从非 git 状态升级到 git 状态。

并且升级后会复用前面那套：

- ID 发现
- upsert
- session migration

链路。

---

# 21. `Vcs` 模块：对 project VCS 状态的运行时观察

`project/vcs.ts` 建立在 `Instance.project.vcs` 基础上。

## 21.1 初始化条件

如果当前 project 不是 git：

- 不建立分支监听

## 21.2 `currentBranch()`

通过：

- `git rev-parse --abbrev-ref HEAD`

读取当前 branch。

## 21.3 订阅文件变化

`Vcs.state()` 会订阅：

- `FileWatcher.Event.Updated`

一旦 branch 变化，就：

- `Bus.publish(Vcs.Event.BranchUpdated, { branch })`

这说明 project/VCS 发现不是一次性静态过程，后续 branch 变化也会被运行时跟踪。

---

# 22. `InstanceBootstrap()`：project 发现之后系统如何启动

`project/bootstrap.ts` 的 `InstanceBootstrap()` 会初始化：

- Plugin
- ShareNext
- Format
- LSP
- FileWatcher
- File
- Vcs
- Snapshot
- Truncate

并监听：

- `Command.Event.Executed`

当执行 `Command.Default.INIT` 时：

- `Project.setInitialized(Instance.project.id)`

这说明 project 的“initialized”状态并不是启动就自动置位，而是与某个正式初始化命令绑定。

这是很合理的产品语义：

- 进入目录不等于完成项目初始化
- 执行 init workflow 才算初始化完成

---

# 23. 为什么 `gitpath()` 很重要

`git` 命令输出的路径经常有这些问题：

- 带结尾换行
- Windows 路径格式差异
- 相对路径

`gitpath(cwd, name)` 专门处理：

- 去掉尾部换行
- 统一 Windows path
- 若是相对路径则解析成绝对路径

这类小函数看似不起眼，但对跨平台可靠性非常关键。

---

# 24. 这个模块背后的关键设计原则

## 24.1 project identity 应尽量稳定且与仓库谱系相关

因此优先用缓存 ID 和 root commit，而不是路径 hash。

## 24.2 git worktree 应归属于同一 project

common dir + cached id 共享正是为此服务。

## 24.3 非 git 场景也应可运行，但要显式降级

所以有 `ProjectID.global` 与 fake VCS 路径。

## 24.4 project 发现应与数据库登记、session 迁移、事件广播一体化

它不是孤立纯函数，而是项目身份维护主链路。

---

# 25. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/project/project.ts`
2. `packages/opencode/src/project/project.sql.ts`
3. `packages/opencode/src/project/vcs.ts`
4. `packages/opencode/src/project/bootstrap.ts`

重点盯住这些函数/概念：

- `Project.fromDirectory()`
- `readCachedId()`
- `gitpath()`
- `Project.initGit()`
- `Project.setInitialized()`
- `Project.addSandbox()`
- `Vcs.currentBranch()`
- `Vcs.Event.BranchUpdated`
- `InstanceBootstrap()`

---

# 26. 下一步还需要深挖的问题

这一篇已经把 project 发现与 VCS 推导主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`ProjectID.make()` 的具体规范与 global ID 的定义细节还可继续查看 schema 文件
- **问题 2**：`Filesystem.up()` 在符号链接、挂载点和大目录树下的行为边界还值得继续确认
- **问题 3**：多 root commit 仓库被排序后取首个的语义是否足够稳健，还可继续评估
- **问题 4**：`Flag.OPENCODE_FAKE_VCS` 在测试与真实运行中的使用场景还值得继续梳理
- **问题 5**：session 迁移只按 `directory == data.worktree` 过滤，是否覆盖了所有 pre-git 历史场景，还可继续思考
- **问题 6**：icon discovery 的性能边界和更新策略还值得继续评估
- **问题 7**：`Project.commands` 字段的完整消费链路还可继续追 command/control-plane/UI 代码
- **问题 8**：project bootstrap 各初始化子系统的依赖顺序是否有强约束，还可以继续精读

---

# 27. 小结

`project_discovery_and_vcs_inference` 模块定义了 OpenCode 如何从一个目录推导出稳定的项目身份与版本控制上下文：

- `Project.fromDirectory()` 负责 git 边界发现、common dir/worktree 解析、project ID 推导和数据库登记
- cached ID 与 root commit 共同保证 project identity 稳定
- `ProjectID.global` 为非 git 场景提供降级路径
- `Vcs` 模块则在运行时继续追踪 branch 变化
- `InstanceBootstrap()` 把这些 project/VCS 信息接入后续子系统初始化

因此，这一层不是简单“当前目录是不是 git repo”的判断，而是 OpenCode 项目身份、历史迁移和运行边界推导的基础设施。
