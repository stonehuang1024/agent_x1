# Snapshot / Patch / Revert Lifecycle 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 snapshot、patch 与 revert 生命周期。

核心问题是：

- 为什么 OpenCode 要维护一套独立于项目主仓库的 snapshot git 存储
- `step-start` / `step-finish` parts 如何定义一次执行边界
- patch part 为什么只记录文件列表而不是完整 diff 文本
- `SessionRevert.revert()` 如何同时回滚文件系统与消息历史
- `SessionRevert.cleanup()` 为什么不是 revert 时立即删消息，而是延后清理

核心源码包括：

- `packages/opencode/src/snapshot/index.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/session/revert.ts`
- `packages/opencode/src/session/summary.ts`
- `packages/opencode/src/session/index.ts`

这一层本质上是 OpenCode 的**执行边界快照、增量变更记录与可撤销会话历史基础设施**。

---

# 2. 为什么需要 snapshot，而不是直接依赖项目自己的 git

OpenCode 不能假设用户项目一定：

- 已初始化 git
- 工作树干净
- 愿意让 agent 直接操作主仓库历史
- 可以用主仓库 commit 作为每一步执行边界

因此系统自己维护一套独立 snapshot 存储，用于：

- 记录每步执行前后的文件树状态
- 生成 diff/patch
- 支撑 revert/unrevert
- 支撑 session summary

这和用户主仓库是解耦的。

---

# 3. `Snapshot` 本质上是什么

从 `snapshot/index.ts` 看，它在 `Global.Path.data` 下维护一个独立 git dir，通过：

- `--git-dir <snapshot_git>`
- `--work-tree <Instance.worktree>`

把当前工作树映射成一套内部 git 对象数据库。

## 3.1 含义

它不是复制整个仓库，也不是额外创建工作区，而是：

- 用用户当前工作树作为 work-tree
- 用 OpenCode 自己的内部 git dir 作为元数据存储

这是很节省空间且高效的设计。

---

# 4. `Snapshot.track()`：生成一个树对象哈希

`track()` 的核心流程是：

1. 检查当前项目必须是 `git`
2. 若 `cfg.snapshot === false` 则退出
3. 确保内部 git dir 存在，不存在则 `git init`
4. 配置：
   - `core.autocrlf=false`
   - `core.longpaths=true`
   - `core.symlinks=true`
   - `core.fsmonitor=false`
5. `add(git)` 把当前工作树索引进内部 git index
6. `git write-tree`
7. 返回 tree hash

## 4.1 为什么返回 tree hash 而不是 commit

系统只需要：

- 一个可恢复、可 diff 的文件树快照标识

并不需要提交历史、author、message 等 commit 语义。

因此 `write-tree` 足够，结构更轻。

---

# 5. 为什么 snapshot 只在 `Instance.project.vcs === "git"` 时启用

这表示当前实现假设：

- 有一个可枚举的 git-style project worktree 边界

虽然 snapshot 存储本身独立于用户主仓库历史，但它仍依赖项目被识别为 git 项目，来界定工作树根与行为边界。

这是一条实现边界，而不是概念必然。

---

# 6. `Snapshot.init()` 与 `cleanup()`

系统会注册定时任务：

- `snapshot.cleanup`
- 每小时执行一次
- 内部跑 `git gc --prune=7.days`

## 6.1 意义

snapshot git dir 不会无限增长，而是有自己的对象回收与保留窗口。

这说明 snapshot 不是临时 hack，而是完整考虑了长期运行成本。

---

# 7. `SessionProcessor.process()`：step 边界如何接入 snapshot

在 processor 中，流事件遇到：

## 7.1 `start-step`

- `snapshot = await Snapshot.track()`
- 写一条 `step-start` part，并保存 `snapshot`

## 7.2 `finish-step`

- 再次 `Snapshot.track()`
- 写 `step-finish` part，并保存结束时 snapshot
- 若起始 snapshot 存在，则 `Snapshot.patch(snapshot)`
- 若 patch 有文件，就写 `patch` part
- 然后触发 `SessionSummary.summarize(...)`

这说明 snapshot 是以“step”为最小执行边界，而不是整个 message 或整个 session 一次性记录。

---

# 8. 为什么 step-start / step-finish 都要记录 snapshot

因为只知道开始或只知道结束都不够：

- 有 start 才知道基线
- 有 finish 才知道最终状态
- 两者一起才能算完整 diff

这也解释了为什么 `SessionSummary.computeDiff()` 会找：

- earliest `step-start.snapshot`
- latest `step-finish.snapshot`

它们共同定义了一段执行范围。

---

# 9. `patch(hash)`：为什么 patch part 只存文件列表

`Snapshot.patch(hash)` 做的是：

- 对比 `hash` 与当前工作树
- `git diff --name-only`
- 返回：
  - `hash`
  - `files[]`

## 9.1 这说明 patch part 的角色

patch part 不是完整文本 diff 缓存，而是：

- “从某个 snapshot hash 起，哪些文件被这一步影响了”的稀疏索引

这非常适合 revert，因为 revert 最核心要知道的是：

- 哪些文件需要回退到哪个 snapshot

---

# 10. 为什么不把完整 diff 文本塞进 patch part

完整 diff 文本可能：

- 很大
- 重复
- 不适合频繁写入消息 part
- 对 revert 来说并非必须

真正需要完整差异时，系统可以用：

- `Snapshot.diff(hash)`
- `Snapshot.diffFull(from, to)`

按需再算。

这是一种很合理的延迟计算策略。

---

# 11. `Snapshot.diff(hash)` 与 `diffFull(from, to)` 的区别

## 11.1 `diff(hash)`

- 产出原始 unified diff 文本
- 用于显示某个 revert/snapshot 相关的 diff 文本

## 11.2 `diffFull(from, to)`

- 产出结构化 `FileDiff[]`
- 每个文件包含：
  - `file`
  - `before`
  - `after`
  - `additions`
  - `deletions`
  - `status`

这说明 snapshot 层同时支持：

- 给人看的文本 diff
- 给系统/UI 统计和摘要用的结构化 diff

---

# 12. `diffFull()` 为什么要单独跑 `--name-status` 和 `--numstat`

它先取：

- `--name-status` 得到 added/deleted/modified

再取：

- `--numstat` 得到 additions/deletions

再对每个文件：

- `git show from:file`
- `git show to:file`

## 12.1 含义

Git 单条命令并不能直接提供这份完整结构化对象，所以实现选择多步拼装，得到既适合 UI 又适合 summary 的高保真 diff 数据。

---

# 13. `Snapshot.restore(snapshot)`：整树恢复

`restore()` 的流程是：

1. `git read-tree <snapshot>`
2. `git checkout-index -a -f`

也就是：

- 先把内部 index 指向目标树
- 再强制把该树 checkout 到 worktree

## 13.1 它用于哪里

主要用于：

- `SessionRevert.unrevert()`

当用户取消 revert 状态时，直接恢复到之前保留的整树 snapshot。

---

# 14. `Snapshot.revert(patches)`：按 patch 文件粒度回滚

这和 `restore()` 不同。

`revert(patches)` 会遍历每个 patch 中的文件，然后对尚未处理过的文件：

- `git checkout <hash> -- <file>`

如果 checkout 失败，再判断该文件在 snapshot 里是否存在：

- 存在但 checkout 失败 -> 记录日志，保留
- 不存在 -> `fs.unlink(file)` 删除

## 14.1 含义

revert 并不总是把整棵树强行回滚，而是：

- 只回退被这些 patch 影响过的文件

这能把回滚范围精确限制在会话修改集合内。

---

# 15. 为什么 `revert(patches)` 要去重文件

它维护一个 `Set<string>`，确保同一个文件只回退一次。

因为同一 session 多个步骤可能多次修改同一文件。

revert 的目标是：

- 恢复到“最早相关 snapshot 对应的状态”

而不是重复 checkout 同一文件多次，造成无意义操作与潜在不一致。

---

# 16. `SessionRevert.revert()`：为什么回滚先从消息历史扫描 patch

`revert()` 会遍历整个 session history：

- 找到从目标 message/part 开始之后的所有内容
- 收集其中的 `patch` parts
- 同时确定 `revert.messageID / partID`

## 16.1 含义

回滚不是简单“回到某个时间点”，而是：

- 从消息历史中推导出“从哪里开始撤销”
- 再把之后产生的代码改动集合取出来回退

这是把代码状态与消息历史绑定起来的关键步骤。

---

# 17. 为什么 `revert()` 要区分 message revert 和 part revert

输入允许：

- `messageID`
- `partID?`

并且在命中时会判断：

- 如果当前 message 在被选中的 part 之前已经没有有用 `text/tool` part
- 那就升级成整条 message revert

## 17.1 含义

系统允许：

- 精细到某个 part 的回滚
- 也允许当 part 级回滚没有意义时自动退化成 message 级回滚

这使回滚语义更符合实际 UX。

---

# 18. `revert.snapshot`：为什么回滚时还要先再做一次 `Snapshot.track()`

一旦确定要进入 revert 状态：

- `revert.snapshot = session.revert?.snapshot ?? (await Snapshot.track())`

这表示系统在真正执行文件回退前，会先保存“当前现场”的整树 snapshot。

## 18.1 用途

这正是后续 `unrevert()` 的恢复点。

也就是说 revert 不是单向 destructive 操作，而是：

- 先保存现场
- 再执行回退
- 允许再反悔恢复

这非常重要。

---

# 19. `revert.diff`：为什么回滚后还要计算 diff 文本

完成 `Snapshot.revert(patches)` 后，如果 `revert.snapshot` 存在，会：

- `revert.diff = await Snapshot.diff(revert.snapshot)`

这说明 session 的 revert 状态不仅保存“如何恢复”，还保存：

- 当前 revert 相对之前现场的 diff 文本

方便 UI/用户查看这次回滚究竟改回了什么。

---

# 20. revert 后为什么还要重新计算 session diff summary

`SessionRevert.revert()` 会：

- 取 `rangeMessages = all.filter(msg => msg.info.id >= revert.messageID)`
- `SessionSummary.computeDiff({ messages: rangeMessages })`
- 写回 `session_diff`
- 发 `Session.Event.Diff`
- 更新 session summary additions/deletions/files

## 20.1 含义

回滚不仅影响文件系统，也改变“这段会话现在被认为造成了哪些变更”。

所以 summary 必须同步重算，否则 UI 会显示错误的变更统计。

---

# 21. 为什么 `revert()` 不立即删除消息/parts

`revert()` 最后只是：

- `Session.setRevert({ sessionID, revert, summary })`

并没有立刻删消息。

真正清理是在：

- `SessionRevert.cleanup(session)`

而 cleanup 又只会在后续某些会话入口里执行，例如 `prompt()` / `shell()` 发现 `session.revert` 时。

## 21.1 这说明什么

revert 先进入一种“挂起的回滚状态”，让用户还能：

- 查看结果
- 选择 unrevert
- 再决定是否继续会话

这比立即 destructive cleanup 更安全。

---

# 22. `SessionRevert.unrevert()`：撤销回滚

`unrevert()` 会：

- `SessionPrompt.assertNotBusy()`
- 若有 `session.revert.snapshot`，则 `Snapshot.restore(snapshot)`
- `Session.clearRevert(sessionID)`

这条路径说明 revert 不是最终提交动作，而是可逆的中间状态。

---

# 23. `cleanup()`：真正清理消息历史的逻辑

cleanup 会：

1. 读取 `session.revert`
2. 遍历消息
3. `messageID` 之前的全保留
4. `messageID` 之后的全部删除
5. 若是 `partID` 级回滚，则只删目标 message 中从该 part 开始的后续 parts
6. 清除 session.revert

并发布：

- `MessageV2.Event.Removed`
- `MessageV2.Event.PartRemoved`

## 23.1 含义

这一步才真正让消息历史与已回退的文件状态重新对齐。

---

# 24. 为什么 cleanup 要延迟到下一次交互

因为在 revert 刚发生时，用户可能还需要：

- 看看回滚差异
- 决定是否 unrevert
- 从 UI 理解将被删除哪些历史

如果立刻删历史，会让用户缺乏确认窗口。

因此“先标记 revert 状态，后 cleanup”是一种很成熟的撤销 UX 设计。

---

# 25. patch part 在异常/中断路径也会生成

在 processor 的 finally-ish 收尾路径中，如果还有未清空的 `snapshot`：

- 仍会 `Snapshot.patch(snapshot)`
- 若有文件则写 `patch` part
- 然后清空 snapshot

## 25.1 这很重要

说明即使 step 没有完美 finish，系统也尽量保留本次执行已造成的文件变更索引，避免 revert/summary 丢失信息。

---

# 26. snapshot、summary、revert 三者的关系

可以这样理解：

## 26.1 snapshot

提供可恢复的文件树边界

## 26.2 patch

提供从某个起点 snapshot 出发，哪些文件变了的索引

## 26.3 summary

基于 step 边界 snapshot 生成结构化 diff 统计

## 26.4 revert

基于 patch 集合回退文件系统，并基于 revert 标记清理消息历史

这四者共同构成了 OpenCode 的可撤销执行模型。

---

# 27. 一个完整的 lifecycle

可以概括为：

## 27.1 执行开始

- `start-step`
- `Snapshot.track()`
- 写 `step-start(snapshot)`

## 27.2 执行结束

- `finish-step`
- 再次 `Snapshot.track()`
- 写 `step-finish(snapshot)`
- `Snapshot.patch(startSnapshot)`
- 写 `patch` part
- `SessionSummary.summarize()`

## 27.3 用户触发回滚

- 扫描目标之后的 `patch` parts
- 先 `Snapshot.track()` 保留当前现场
- `Snapshot.revert(patches)`
- 写 `session.revert`
- 重算 summary/diff

## 27.4 用户确认继续

- 下一次 prompt/shell 前 `SessionRevert.cleanup()`
- 删除被回滚覆盖的消息/parts
- 清除 revert 标记

## 27.5 用户反悔

- `unrevert()`
- `Snapshot.restore(revert.snapshot)`
- 清除 revert 标记

这就是 OpenCode 的完整快照回滚闭环。

---

# 28. 这个模块背后的关键设计原则

## 28.1 代码状态回滚必须和消息历史回滚绑定

否则会出现“代码回去了，但历史还声称改过”的不一致。

## 28.2 回滚应尽量精确到会话修改集合，而非整树粗暴覆盖

所以常规 revert 用 patch 文件列表，而不是直接 restore 整树。

## 28.3 回滚必须可反悔

所以先保存 `revert.snapshot`，再允许 `unrevert()`。

## 28.4 摘要系统必须建立在同一套 snapshot 边界上

所以 summary 复用 step snapshots，而不是另起一套变更追踪。

---

# 29. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/snapshot/index.ts`
2. `packages/opencode/src/session/processor.ts`
3. `packages/opencode/src/session/revert.ts`
4. `packages/opencode/src/session/summary.ts`
5. `packages/opencode/src/session/index.ts`

重点盯住这些函数/概念：

- `Snapshot.track()`
- `Snapshot.patch()`
- `Snapshot.diff()`
- `Snapshot.diffFull()`
- `Snapshot.restore()`
- `Snapshot.revert()`
- `SessionRevert.revert()`
- `SessionRevert.unrevert()`
- `SessionRevert.cleanup()`
- `step-start` / `step-finish` / `patch`

---

# 30. 下一步还需要深挖的问题

这一篇已经把 snapshot/patch/revert 主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`snapshot/index.ts` 后半段 `diffFull()` 的完整实现细节，尤其 binary file 与 large file 处理，还值得继续读完
- **问题 2**：内部 snapshot git dir 的精确路径计算与 `gitdir()` 实现还值得继续查看
- **问题 3**：`add(git)` 的索引更新策略和 ignore 边界还值得继续精读
- **问题 4**：非 git 项目当前完全不启用 snapshot/revert，这是否是产品有意限制，还值得继续确认
- **问题 5**：patch 只存文件列表，若同一文件跨多 step 多次修改，revert 的最终语义是否总与用户直觉一致，还值得继续验证
- **问题 6**：cleanup 延迟执行期间，UI 如何准确表现“当前处于 revert pending 状态”，还值得继续查看前端/服务端接口
- **问题 7**：`Snapshot.restore()` 使用 read-tree + checkout-index，对未跟踪文件/权限位/symlink 的恢复边界还值得继续验证
- **问题 8**：summary 与 revert 都依赖 snapshots，若 track 失败时当前降级行为是否足够可见，还值得继续追踪日志与错误处理

---

# 31. 小结

`snapshot_patch_and_revert_lifecycle` 模块定义了 OpenCode 如何用独立 snapshot 存储把代码执行边界、文件变更索引、diff 摘要与可逆回滚统一起来：

- `Snapshot.track()` 为每个 step 记录文件树边界
- `patch` part 记录该 step 影响到的文件集合
- `SessionSummary` 基于这些 snapshot 生成结构化 diff 摘要
- `SessionRevert` 则把文件系统回退、revert 状态保存、unrevert 恢复与消息历史 cleanup 串成完整闭环

因此，这一层不是简单的 diff 辅助工具，而是 OpenCode 让 agent 改动可追踪、可解释、可撤销的核心安全与状态管理基础设施。
