# Revert / Snapshot / Patch 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 中与工作区变更快照、patch 生成、回退和恢复相关的链路。

核心问题是：

- OpenCode 如何知道“这一轮改了哪些文件”
- snapshot 为什么不是直接依赖用户仓库本身的 git 历史
- `patch` part 是怎么生成的
- `revert` 到底回退了什么
- `unrevert` 又是如何恢复现场的
- revert 状态为什么挂在 session 上而不是只挂在 message 上

核心源码包括：

- `packages/opencode/src/snapshot/index.ts`
- `packages/opencode/src/session/revert.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/session/index.ts`

这一层本质上是 OpenCode 的**工作区状态回溯与变更追踪系统**。

---

# 2. 总体设计：OpenCode 自己维护一套快照 git 仓库

## 2.1 为什么不用用户仓库的 git 历史直接做回退

原因很明显：

- 用户项目未必是 git repo
- 即使是 git repo，也不能随意改动用户真实 commit/history
- agent 每一步生成的临时状态不该污染用户正式版本历史
- revert 需求通常是“回到某次 agent 操作前”，而不是“git reset 到某个真实 commit”

因此 OpenCode 维护了一套独立 snapshot 仓库，用来记录：

- 当前工作树的 tree state
- 某轮开始前的快照
- 某轮结束后的 patch/diff

这是一种很成熟的 **shadow git repo** 设计。

## 2.2 snapshot 仓库存放位置

`gitdir()` 返回：

- `Global.Path.data/snapshot/<project.id>`

也就是说：

- 每个 project 都有一个独立 snapshot git directory
- 与用户真实项目仓库分离

这非常重要，因为它避免了对用户 git 元数据的污染。

---

# 3. `Snapshot.track()`：如何记录一个基准快照

## 3.1 启用条件

`track()` 只有在以下条件满足时才工作：

- `Instance.project.vcs === "git"`
- `cfg.snapshot !== false`

这里的 `vcs === git` 不是指“必须用用户 repo 的 git 历史”，而是 OpenCode 当前只在 git 型项目语义下启用 snapshot 体系。

## 3.2 初始化 shadow repo

如果 snapshot gitdir 不存在：

- `fs.mkdir(git, { recursive: true })`
- `git init` with:
  - `GIT_DIR = git`
  - `GIT_WORK_TREE = Instance.worktree`

然后会配置：

- `core.autocrlf = false`
- `core.longpaths = true`
- `core.symlinks = true`
- `core.fsmonitor = false`

这些设置都是为了让 snapshot repo 更稳定地跟踪真实工作树内容，而尽量避免平台差异影响。

## 3.3 快照生成算法

`track()` 的流程是：

1. `add(git)`
2. `git write-tree`
3. 返回 tree hash

关键点在于它不是 commit，而是：

- **直接记录 tree object hash**

这很巧妙，因为：

- 不需要维护 commit graph
- 只需要描述某一时刻工作树内容
- 更轻量

这意味着 OpenCode 的 snapshot 更接近：

- 文件树状态指针

而不是完整版本控制历史。

---

# 4. `add(git)` 与 exclude 同步

## 4.1 `add(git)`

内部会：

1. `syncExclude(git)`
2. `git add .`

说明 snapshot repo 也遵守一定的排除规则，而不是无脑把所有文件都纳入追踪。

## 4.2 `syncExclude(git)`

它会去读取用户真实仓库的：

- `.git/info/exclude`

然后同步写入 snapshot repo 的：

- `git/info/exclude`

这意味着 OpenCode snapshot 体系会尽量继承用户仓库已有的忽略策略。

这是很合理的，因为某些本地生成文件、缓存文件、敏感文件不应该被 agent snapshot 系统无差别捕获。

---

# 5. `Snapshot.patch(hash)`：如何生成本轮改动文件列表

## 5.1 作用

给定某个基准 tree hash，`patch(hash)` 会计算：

- 从该 hash 到当前工作树之间，哪些文件发生了变化

返回结构：

- `{ hash, files }`

## 5.2 算法

1. 先 `add(git)` 同步当前工作树状态
2. 调用：
   - `git diff --name-only hash -- .`
3. 解析结果
4. 映射成绝对工作区路径列表

这说明 `patch()` 关心的是：

- **哪些文件变了**

而不是完整 diff 正文。

## 5.3 为什么 processor 里用它

processor 在：

- `start-step` 时 `Snapshot.track()`
- `finish-step` 或 process 收尾时 `Snapshot.patch(snapshot)`

然后若有变化文件，就写成：

- `PatchPart`

这正是 OpenCode 能在每一步后展示“本轮修改了哪些文件”的来源。

---

# 6. `Snapshot.diff(hash)` 与 `diffFull(from, to)`

## 6.1 `diff(hash)`

返回：

- 从 `hash` 到当前工作树的完整 diff 文本

适合用于：

- revert 状态展示
- 文本级差异回显

## 6.2 `diffFull(from, to)`

返回更结构化的文件差异数组：

- `file`
- `before`
- `after`
- `additions`
- `deletions`
- `status`

并通过：

- `git diff --name-status`
- `git diff --numstat`
- `git show from:file`
- `git show to:file`

拼出完整前后文本快照。

这说明 snapshot 层并不只是文件名跟踪，还具备：

- 结构化 diff 生产能力

---

# 7. `Snapshot.revert(patches)`：如何把工作树回退

## 7.1 输入是什么

这里输入不是单个 hash，而是一组：

- `Patch[]`

也就是多个 patch part 中记录的：

- 基准 hash
- 受影响文件列表

## 7.2 算法

对每个 patch 中的每个 file：

1. 去重，避免同一文件重复 revert
2. 执行：
   - `git checkout <hash> -- <file>`
3. 若失败：
   - `git ls-tree <hash> -- <relativePath>`
   - 如果文件在 snapshot 中本来存在，则保留
   - 否则删除本地文件

这是一种非常务实的 **file-wise revert algorithm**。

## 7.3 为什么不是整树 reset

因为 revert 目标常常不是“还原整个项目”，而是：

- 撤销从某个消息/part 之后 agent 造成的改动

因此按 patch files 级别回退更精准，也更贴近消息历史语义。

---

# 8. `Snapshot.restore(snapshot)`：如何做 unrevert

## 8.1 与 `revert()` 的区别

- `revert(patches)`：把当前工作树回退到较早状态
- `restore(snapshot)`：把工作树恢复到某个先前保存的 tree snapshot

## 8.2 算法

1. `git read-tree snapshot`
2. `git checkout-index -a -f`

也就是说它是：

- 先把目标 tree 读入 index
- 再强制 checkout 到工作树

这很适合 unrevert，因为 unrevert 的语义是：

- 回到“执行 revert 前”的现场

---

# 9. processor 如何生成 patch parts

## 9.1 `start-step`

processor 收到 `start-step` 时：

- `snapshot = await Snapshot.track()`
- 记录 `step-start` part

## 9.2 `finish-step`

收到 `finish-step` 后：

- 若之前有 snapshot
- 调用 `Snapshot.patch(snapshot)`
- 若 `patch.files.length > 0`
- 写入 `type: patch` 的 part

## 9.3 process 结束兜底

即使异常离开正常循环，只要还持有 snapshot，也会在收尾逻辑再试一次：

- `Snapshot.patch(snapshot)`
- 写 patch part

这说明 OpenCode 对 patch 记录非常重视，不希望因为异常路径而丢失本轮改动踪迹。

---

# 10. `SessionRevert.revert()`：从消息历史构造回退点

这是整个 revert 链路最关键的地方。

## 10.1 输入

- `sessionID`
- `messageID`
- 可选 `partID`

意味着用户既可以：

- 回退整条消息
- 也可以从某个 part 开始回退

## 10.2 遍历历史算法

它会读取整个 session 消息历史，然后：

- 追踪最近的 user message
- 一旦命中目标 message/part，就确定 `revert` 起点
- 从命中点之后遇到的 `patch` parts 都收集进 `patches`

这里的关键思想是：

- revert 不是直接“删消息”
- 而是先根据消息边界，算出该撤销哪些文件改动

## 10.3 为什么 `messageID` 可能被重写成最近 user message

当 target part 前已经没有“有意义剩余内容”时：

- 系统会把 revert 起点提升到最近 user message

这说明 OpenCode 在语义上更倾向于：

- 回退到一个自然对话边界

而不是留下一个半残缺消息状态。

---

# 11. revert 状态为什么要记录在 session 上

`Session.Info.revert` 包括：

- `messageID`
- `partID?`
- `snapshot?`
- `diff?`

## 11.1 为什么不是只写一条临时变量

因为 revert 是一个可持续状态：

- 当前 session 已经处于“回退后的临时视图”
- 还可能被 `unrevert`
- 还可能需要 `cleanup`
- UI 也可能需要展示当前 revert 状态

因此它必须落在 session 持久化层，而不是只存在内存里。

## 11.2 `snapshot` 的作用

在真正执行 `Snapshot.revert(patches)` 前，会先保存：

- `revert.snapshot = session.revert?.snapshot ?? (await Snapshot.track())`

也就是说：

- 第一次 revert 时，先保存当前现场
- 后续重复 revert 时可复用已有 snapshot

这样 `unrevert` 才知道如何回到 revert 前的工作树状态。

## 11.3 `diff` 的作用

revert 后会：

- `revert.diff = await Snapshot.diff(revert.snapshot)`

说明 session 的 revert 状态不仅知道“怎么恢复”，也保留了“当前与原现场差了什么”的文本 diff 视图。

---

# 12. revert 后如何生成 summary diff

在 `SessionRevert.revert()` 中，系统还会：

1. 取从 revert 起点开始的消息范围
2. `SessionSummary.computeDiff({ messages: rangeMessages })`
3. `Storage.write(["session_diff", sessionID], diffs)`
4. 发布 `Session.Event.Diff`
5. 更新 session summary：
   - additions
   - deletions
   - files

这说明 revert 不只是工作树变化，也会同步更新：

- session 层的汇总 diff 统计
- 对外 diff 事件
- storage 中的结构化 diff

因此 revert 是完整的状态迁移，不只是文件系统操作。

---

# 13. `unrevert()`：恢复到 revert 前现场

## 13.1 前置条件

- session 必须有 `revert`
- 若没有，直接返回 session

## 13.2 核心流程

- 如果 `session.revert.snapshot` 存在
  - `Snapshot.restore(snapshot)`
- 然后 `Session.clearRevert(sessionID)`

这说明 unrevert 的语义非常纯粹：

- 恢复工作树
- 清空 session revert 标记

---

# 14. `cleanup()`：如何把历史真正裁剪掉

revert 后并不一定立即删除消息历史。

`cleanup(session)` 才是真正做历史裁剪的逻辑。

## 14.1 整体目标

基于 `session.revert`：

- 删除 revert 起点之后的消息
- 或删除目标 message 中某个 part 之后的 parts
- 最后清掉 session revert 状态

## 14.2 两种情况

### 回退整条消息/从某条消息开始

- 删除目标 message 及其后的所有 message

### 回退某个 part 开始

- 保留该 message 之前的 parts
- 删除该 part 及之后的 parts

## 14.3 为什么要单独有 cleanup

因为 revert 有两个层面：

- **工作树层**：先把文件回退，方便用户预览/确认
- **历史层**：再决定是否真正清理消息/part 记录

这是一种很好的分阶段设计，避免一上来就不可逆删历史。

---

# 15. Session 层与 revert 的持久化接口

从 `session/index.ts` 可见：

- `setRevert(...)`
- `clearRevert(sessionID)`

## 15.1 `setRevert()`

会更新：

- `revert`
- `summary_additions`
- `summary_deletions`
- `summary_files`
- `summary_diffs`
- `time_updated`

这说明 revert 与 summary 是绑定更新的。

## 15.2 `clearRevert()`

会把：

- `revert = null`
- 更新时间戳

因此 revert 是正式 session 状态，而不是 patch tool 的临时副产物。

---

# 16. snapshot cleanup 机制

`Snapshot.init()` 会注册 scheduler：

- `snapshot.cleanup`
- 每小时运行一次
- `git gc --prune=7.days`

这意味着 shadow git repo 不是无限膨胀的，OpenCode 还专门做了周期性清理。

这是非常重要的运维细节，因为 agent 长期使用后 snapshot object 很容易变多。

---

# 17. 这个模块背后的关键设计原则

## 17.1 回退应该基于 agent 自己的状态历史，而不是篡改用户真实 VCS

所以 OpenCode 维护了独立 shadow repo。

## 17.2 patch 与 snapshot 分工明确

- snapshot = 某时刻工作树基准
- patch = 从该基准到当前工作树的变更文件集合

## 17.3 revert 是两阶段过程

- 文件系统先回退
- 历史状态后清理

这样更安全、更可观察。

## 17.4 session 必须显式记录 revert 状态

否则 unrevert、cleanup、UI 展示都无法稳定进行。

---

# 18. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/snapshot/index.ts`
2. `packages/opencode/src/session/revert.ts`
3. `packages/opencode/src/session/processor.ts`
4. `packages/opencode/src/session/index.ts`

重点盯住这些函数/概念：

- `Snapshot.track()`
- `Snapshot.patch()`
- `Snapshot.diff()`
- `Snapshot.diffFull()`
- `Snapshot.revert()`
- `Snapshot.restore()`
- `SessionRevert.revert()`
- `SessionRevert.unrevert()`
- `SessionRevert.cleanup()`
- `Session.setRevert()`
- `Session.clearRevert()`

---

# 19. 下一步还需要深挖的问题

这一篇已经把 snapshot/revert 主框架讲清楚了，但还有一些地方值得继续展开：

- **问题 1**：`SessionSummary.computeDiff()` 的具体算法与 message/patch 关系还可以继续精读
- **问题 2**：UI 如何展示 revert 状态、patch 列表和 diff 文本，还值得继续追踪前端/TUI 代码
- **问题 3**：非 git 项目场景下 snapshot/revert 为什么直接不启用，是否存在未来替代实现空间
- **问题 4**：`Snapshot.track()` 使用 `git write-tree` 而不写 commit 的长期维护边界是否完全足够
- **问题 5**：大仓库、大量二进制文件场景下 snapshot repo 的体积与性能成本需要进一步评估
- **问题 6**：revert 某个 part 时，为什么用“是否还剩 text/tool”来决定是否提升到 message 边界，这个语义还可继续细化
- **问题 7**：`Storage.write(["session_diff", sessionID], diffs)` 的消费方是谁，还值得继续追踪
- **问题 8**：cleanup 删除消息/part 后，相关 bus 事件如何驱动 UI 与缓存层同步刷新，还可继续展开

---

# 20. 小结

`revert_snapshot_and_patch` 模块定义了 OpenCode 如何追踪和回退 agent 对工作区造成的影响：

- `Snapshot` 用独立 shadow git repo 跟踪工作树状态
- `processor` 在每个 step 前后生成 patch parts
- `SessionRevert` 根据 message/part 边界计算应撤销的补丁集合
- `revert` 先回退文件系统，再记录 session revert 状态与 diff 摘要
- `unrevert` 通过 snapshot 恢复现场
- `cleanup` 最终裁剪消息历史

因此，这一层不是简单的 undo 功能，而是 OpenCode agent runtime 的工作区状态回溯基础设施。
