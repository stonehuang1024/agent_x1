# File Watcher / Live Refresh 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的文件监听与实时刷新链路。

核心问题是：

- FileWatcher 监听什么、怎么监听
- 为什么既监听工作目录又监听 git 目录
- 文件变化事件如何传播到 VCS、UI 和其他模块
- `File` 模块里的文件索引缓存如何与实时刷新协同
- 这套机制如何为 branch 变化感知和实时更新提供基础

核心源码包括：

- `packages/opencode/src/file/watcher.ts`
- `packages/opencode/src/file/index.ts`
- `packages/opencode/src/project/vcs.ts`
- `packages/opencode/src/project/bootstrap.ts`

这一层本质上是 OpenCode 的**本地文件系统变化感知与增量刷新基础设施**。

---

# 2. 为什么 OpenCode 需要文件监听

OpenCode 不是静态分析器，它运行时会持续依赖文件系统最新状态：

- 代码文件被外部编辑器改动
- `.git/HEAD` 变化导致 branch 切换
- 新增/删除文件影响 search/list/read 结果
- LSP、snapshot、VCS、UI 视图都可能需要跟进变化

如果每次都全量轮询文件系统：

- 成本高
- 延迟大
- 实时感差

因此需要正式文件监听层。

---

# 3. `FileWatcher.Event.Updated`

FileWatcher 只暴露了一个核心事件：

- `file.watcher.updated`

事件字段包括：

- `file`
- `event`

其中 `event` 为：

- `add`
- `change`
- `unlink`

这说明 FileWatcher 的职责很聚焦：

- **把底层文件事件规范化成统一总线事件**

而不直接承担更高层业务逻辑。

---

# 4. 底层监听实现：`@parcel/watcher`

`watcher.ts` 使用：

- `@parcel/watcher/wrapper`

并按平台动态 require 对应 binding：

- macOS -> `fs-events`
- Linux -> `inotify`
- Windows -> `windows`

## 4.1 为什么动态加载

这说明 OpenCode 不想在所有平台一股脑打包所有 watcher binary，而是按：

- `platform`
- `arch`
- Linux 下还带 `glibc/musl`

动态选 binding。

这是典型的跨平台原生 watcher 适配做法。

## 4.2 失败处理

如果 binding 加载失败：

- 打 error
- 返回 undefined

这样上层初始化可以优雅跳过，而不是直接崩进程。

---

# 5. `lazy(watcher)`：为什么 watcher binding 用惰性加载

文件监听未必在所有场景都真的需要，因此 binding 通过：

- `lazy(() => require(...))`

延迟初始化。

这样可以：

- 减少启动时不必要开销
- 让无 watcher 能力的环境更容易退化运行
- 避免一次 require 失败后永久污染状态

---

# 6. `FileWatcher.state()`：实例级订阅集

FileWatcher 使用：

- `Instance.state(...)`

维护当前实例的 watcher 订阅。

返回值核心是：

- `subs: ParcelWatcher.AsyncSubscription[]`

这说明：

- watcher 订阅是按 instance 目录隔离的
- instance dispose 时也能统一 unsubscribe

这和 OpenCode 其他 runtime 组件的 per-instance state 策略保持一致。

---

# 7. 初始化流程：如何选择 backend

在 `state()` 中会根据平台选择 backend：

- `win32 -> windows`
- `darwin -> fs-events`
- `linux -> inotify`

若平台不支持，则直接记录错误并返回空状态。

这说明 OpenCode 明确区分：

- watcher 不可用
- watcher 初始化失败

而不是把其当成总是存在的能力。

---

# 8. `subscribe` callback：事件规范化层

底层 watcher 回调里，Parcel 的事件会被映射为：

- `create -> add`
- `update -> change`
- `delete -> unlink`

然后统一：

- `Bus.publish(FileWatcher.Event.Updated, ...)`

这一步很关键，因为它把第三方 watcher 的事件语义转成 OpenCode 自己的总线契约。

这样后续模块就不需要知道底层库细节。

---

# 9. 为什么监听 `Instance.directory`

当开启：

- `OPENCODE_EXPERIMENTAL_FILEWATCHER`

时，会订阅：

- `Instance.directory`

## 9.1 ignore 列表

监听时会忽略：

- `FileIgnore.PATTERNS`
- `cfg.watcher?.ignore`
- `Protected.paths()`

这说明 watcher 的目标不是粗暴监听所有路径，而是受：

- 系统默认忽略
- 用户自定义忽略
- 受保护路径

共同约束。

## 9.2 意义

监听当前工作目录能帮助系统及时感知：

- 新增文件
- 文本变化
- 删除文件

从而支持文件索引、UI 状态和某些增量逻辑刷新。

---

# 10. 为什么还要监听 git 目录

如果当前 project 是 git，还会额外监听：

- `git rev-parse --git-dir` 得到的 git 目录

并且：

- 忽略 git 目录下除 `HEAD` 外的大部分内容

## 10.1 目的

这里的目标不是监听所有 git 对象变化，而是：

- 主要感知 `HEAD` 等关键引用变化

## 10.2 为什么这很重要

branch 切换不一定会在工作目录普通文件变化中立刻表现为足够可靠的信号。

监听 git dir，特别是 `HEAD`，可以更稳地感知：

- branch 切换
- checkout 等版本控制状态变化

这与 `Vcs` 模块的 branch 监听直接呼应。

---

# 11. `SUBSCRIBE_TIMEOUT_MS`：防止 watcher 初始化卡死

无论监听 `Instance.directory` 还是 git dir，订阅都包在：

- `withTimeout(pending, 10_000)`

里。

## 11.1 为什么需要超时

原生文件监听有时会因为：

- 平台问题
- 权限问题
- 网络文件系统
- watcher binding 异常

导致订阅迟迟不返回。

设置超时能避免 instance 初始化被无限卡住。

## 11.2 超时后的清理

若超时失败，还会：

- `pending.then((s) => s.unsubscribe()).catch(() => {})`

这说明系统在失败路径上也尽量清理潜在半初始化订阅，避免泄漏。

---

# 12. `FileWatcher.init()`：实验开关与总入口

`init()` 的逻辑很简单：

- 若 `OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER` 开启，则直接跳过
- 否则调用 `state()`

这说明 FileWatcher 当前仍带有实验/可禁用属性。

这很合理，因为文件监听往往是跨平台问题最多的基础设施之一。

---

# 13. `File` 模块的索引缓存：非事件驱动但与实时变化协同

`file/index.ts` 里的 `File.state()` 维护了一份：

- `files`
- `dirs`

缓存。

## 13.1 如何构建

- 若当前是在全局 home/global project 视图
  - 用更保守的目录扫描逻辑
- 否则
  - 用 `Ripgrep.files({ cwd: Instance.directory })` 枚举文件
  - 同时推导目录列表

## 13.2 `fetching` 标记

状态里还维护：

- `fetching`

用于避免在已有刷新进行时无限重复触发新刷新。

## 13.3 与实时变化的关系

虽然这里没有直接看到 FileWatcher 订阅后主动 invalidation 的逻辑，但 watcher 事件已经为后续模块或 UI 提供了刷新触发基础，而 `File.state()` 则是当前目录快照缓存。

换句话说：

- FileWatcher 提供变化信号
- File 模块提供可重建的目录/文件索引快照

两者是互补关系。

---

# 14. `File.status()`：工作树变化快照

`File.status()` 并不依赖 watcher，而是实时查询 git：

- `diff --numstat HEAD`
- `ls-files --others --exclude-standard`
- `diff --name-only --diff-filter=D HEAD`

得到：

- modified
- added
- deleted

文件列表。

这说明 OpenCode 的“文件变化展示”并不是纯 watcher 事件堆出来的，而是：

- watcher 负责及时感知变化
- status 负责需要时重新计算 authoritative git diff

这是非常合理的组合。

---

# 15. `File.read()` / `File.list()` / `File.search()` 与实时刷新

这些 API 都依赖：

- `Instance.directory`
- `Instance.containsPath()`
- 当前文件系统真实状态

因此在外部编辑器修改文件后，只要 watcher 能推动 UI 或调用侧刷新，这些函数就会读取到最新内容。

也就是说，watcher 本身不必缓存所有文件内容，它主要负责：

- 提供刷新时机

而真正的数据读取仍由 `File` 模块在请求时完成。

---

# 16. `Vcs` 如何利用 FileWatcher 实现 branch 变化感知

`project/vcs.ts` 中：

- `Vcs.state()` 订阅 `FileWatcher.Event.Updated`
- 每次文件更新后检查当前 branch
- 若 branch 变化，发布：
  - `vcs.branch.updated`

## 16.1 为什么这很巧妙

VCS 模块并不自己实现另一套 git 监听，而是复用 FileWatcher 事件作为触发器。

这说明 OpenCode 采用的是：

- **底层统一文件变化信号 -> 上层按需派生业务语义**

而不是每个模块各自监听文件系统。

## 16.2 `HEAD` 的特殊处理

Vcs 订阅时还会忽略：

- `evt.properties.file.endsWith("HEAD")`

然后再主动 `currentBranch()` 比较。

这看起来有点反直觉，但实际上说明：

- 模块不直接把单个 HEAD 文件事件当作 branch 变化真相
- 而是以它为触发器，再重新读取 authoritative branch 名

这种做法更稳健。

---

# 17. `InstanceBootstrap()` 如何接入 watcher

在 `project/bootstrap.ts` 中，instance 启动时会依次初始化：

- `Plugin.init()`
- `LSP.init()`
- `FileWatcher.init()`
- `File.init()`
- `Vcs.init()`
- 等

这说明文件监听被视为 instance runtime 的正式基础设施，而不是按需小插件。

初始化顺序也很有意义：

- 先有 watcher
- 再有 file/vcs 等依赖变化信号的模块

---

# 18. ignore 与 protected 的安全边界意义

FileWatcher 监听时排除：

- `FileIgnore.PATTERNS`
- `Protected.paths()`

这说明 watcher 不只是性能考虑，还包含：

- 对不应暴露或不应干扰的路径做保护

这样可以减少：

- 噪音事件
- 敏感路径监控
- 无意义目录遍历

---

# 19. 这套实时刷新模型的本质

把这些模块合起来看，OpenCode 的实时刷新策略并不是“watch everything then mutate everything”。

而是三层：

## 19.1 感知层

- `FileWatcher`

负责捕获文件系统变化信号。

## 19.2 语义派生层

- `Vcs`
- 可能的其他订阅者

负责把通用文件变化转换成更高级事件，例如 branch change。

## 19.3 权威数据层

- `File.status()`
- `File.read()`
- `File.list()`
- `File.search()`

在需要时重新读取真实状态。

这是一种很稳的架构：

- watcher 不承担过重状态责任
- 读取逻辑也不必轮询一切

---

# 20. 这个模块背后的关键设计原则

## 20.1 文件变化应先被统一规范化，再供上层消费

所以有 `FileWatcher.Event.Updated`。

## 20.2 文件监听与业务语义解析应分离

branch 变化感知由 `Vcs` 在 watcher 之上派生，而不是 watcher 自己理解 git 语义。

## 20.3 watcher 必须可降级、可超时、可忽略噪声路径

否则跨平台稳定性会很差。

## 20.4 watcher 负责触发刷新，不必成为权威数据源

真正的内容读取仍由 `File` 模块按需完成。

---

# 21. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/file/watcher.ts`
2. `packages/opencode/src/file/index.ts`
3. `packages/opencode/src/project/vcs.ts`
4. `packages/opencode/src/project/bootstrap.ts`

重点盯住这些函数/概念：

- `FileWatcher.init()`
- `FileWatcher.Event.Updated`
- `watcher()`
- `withTimeout(...)`
- `File.status()`
- `File.read()`
- `File.search()`
- `Vcs.currentBranch()`
- `Vcs.Event.BranchUpdated`

---

# 22. 下一步还需要深挖的问题

这一篇已经把文件监听与实时刷新主链路讲清楚了，但还有一些地方值得继续展开：

- **问题 1**：`FileIgnore` 与 `Protected` 的完整规则来源与优先级还值得继续精读
- **问题 2**：File 模块的缓存刷新触发是否还有其他隐式调用点，还可以继续 grep
- **问题 3**：watcher 在大型 monorepo、网络盘和容器挂载目录下的稳定性边界还值得评估
- **问题 4**：branch 切换时，除 `vcs.branch.updated` 外，还有哪些模块会进一步响应，还值得继续追踪
- **问题 5**：Parcel watcher 事件顺序、去抖和批处理语义是否会影响上层逻辑，需要继续核对
- **问题 6**：`File.status()` 中 deleted file 的 removed 行数未精确计算，这是否会影响 UI 统计，还值得继续思考
- **问题 7**：`File.search()` 与 `Ripgrep.files()` 的缓存时效在高频文件变更下是否足够及时，还可继续验证
- **问题 8**：未来 watcher 是否会从 experimental 变成默认强依赖，其兼容策略也值得关注

---

# 23. 小结

`file_watcher_and_live_refresh` 模块定义了 OpenCode 如何感知本地文件与 git 状态变化，并把这些变化转化为系统可消费的实时信号：

- `FileWatcher` 负责跨平台文件系统订阅与统一事件发布
- `Vcs` 在 watcher 之上派生 branch 变化事件
- `File` 模块则在需要时读取和计算权威文件状态与目录索引
- `InstanceBootstrap()` 把这些能力装配进每个实例的运行时环境

因此，这一层不是单纯的文件监听实现，而是 OpenCode 实时刷新、VCS 感知和本地状态同步能力的基础设施。
