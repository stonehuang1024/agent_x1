# Search / Retrieval Stack 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的检索栈。

核心问题是：

- 为什么系统同时有 `glob`、`grep`、`read`、`lsp`、`codesearch`
- 它们各自解决什么层次的问题
- 本地 ripgrep 与外部 code search 的边界在哪里
- 为什么不同检索工具会返回不同形态的结果与 metadata
- 这些工具如何共同组成 agent 的“先定位、再精读、再语义追踪”工作流

核心源码包括：

- `packages/opencode/src/tool/glob.ts`
- `packages/opencode/src/tool/grep.ts`
- `packages/opencode/src/tool/read.ts`
- `packages/opencode/src/tool/lsp.ts`
- `packages/opencode/src/tool/codesearch.ts`
- `packages/opencode/src/file/ripgrep.ts`
- `packages/opencode/src/file/index.ts`

这一层本质上是 OpenCode 的**分层检索与证据收集基础设施**。

---

# 2. 为什么需要分层检索，而不是一个万能 search 工具

代码探索并不是单一步骤。

通常会经历：

- 先找文件名
- 再找文本命中
- 再读局部内容
- 再做符号级跳转
- 必要时再查询外部代码知识

如果把这些都塞进一个工具：

- 参数会极度复杂
- 返回结果不稳定
- 工具难以针对不同任务优化

所以 OpenCode 明确拆层：

- `glob`：文件名/路径匹配
- `grep`：文本匹配
- `read`：精读内容
- `lsp`：语义级导航
- `codesearch`：外部/互联网代码检索

这是一套非常清晰的 retrieval ladder。

---

# 3. 底层公共能力：`Ripgrep`

本地检索栈的底层公共件是：

- `file/ripgrep.ts`

它不是一个简单 wrapper，而是完整管理了：

- `rg` 可执行文件发现
- 必要时自动下载 ripgrep binary
- 文件枚举
- 目录树生成
- JSON 搜索结果解析

所以 `Ripgrep` 是 OpenCode 本地检索能力的核心基础设施。

---

# 4. `Ripgrep.filepath()`：为什么系统自己管理 rg binary

`Ripgrep.state()` 会先尝试：

- `which("rg")`

如果系统没有可用 `rg`，则会：

- 根据平台选择预编译二进制
- 下载到 `Global.Path.bin`
- 解压/赋权

## 4.1 意义

这保证了本地检索工具不强依赖用户系统预装 ripgrep。

因此 OpenCode 的 `glob/grep/tree/search` 都有较强的自包含性。

## 4.2 这不是 fallback，而是运行时依赖管理

它并不是实现多套搜索逻辑，而是保证同一套 `ripgrep` 能力稳定存在。

---

# 5. `Ripgrep.files()`：文件路径枚举层

`files()` 本质是：

- 调 `rg --files`
- 默认 `--glob=!.git/*`
- 可选 `hidden/follow/maxDepth/glob/signal`
- 流式 yield 路径

这说明它不是全文搜索，而是：

- **项目文件清单迭代器**

它被多个上层模块复用：

- `glob`
- `ls`
- `File.state()` 文件索引构建
- `Ripgrep.tree()`

---

# 6. `Ripgrep.search()`：结构化文本匹配层

`search()` 走的是：

- `rg --json`

并把结果解析成：

- `begin`
- `match`
- `end`
- `summary`

最后只保留 `match` 项。

## 6.1 为什么 JSON 很重要

相比 grep tool 里自己拆文本输出，JSON 模式更适合：

- 服务端 API
- 精细结构化消费
- 保留 submatch、line_number、offset 等信息

因此 `server/routes/file.ts` 会直接暴露 `Ripgrep.Match.shape.data.array()`。

这说明：

- CLI/tool 面可以偏人类友好
- 内部 API 面可以偏结构化精确

---

# 7. `Ripgrep.tree()`：目录结构摘要层

`tree()` 会基于 `Ripgrep.files()` 构建目录树，再按 BFS 方式输出路径前缀列表。

特点包括：

- 跳过 `.opencode`
- 只保留目录层级，不直接列出所有文件
- 可加 `limit`
- 超限时输出 `[N truncated]`

这说明 `tree()` 的目标不是精确检索，而是：

- **快速建立目录心智模型**

---

# 8. `glob`：文件名/路径匹配工具

`GlobTool` 的职责非常纯粹：

- 在某个目录下按 glob pattern 找文件

## 8.1 流程

1. ask `permission: glob`
2. 解析 search dir
3. `assertExternalDirectory()`
4. 调 `Ripgrep.files({ cwd, glob: [pattern] })`
5. 最多保留 100 个结果
6. 按 mtime 倒序排序
7. 输出绝对路径列表

## 8.2 为什么按 mtime 排序

这说明 OpenCode 认为“最近改过的文件”通常更值得优先关注。

在 agent 搜索场景里，这确实很实用。

---

# 9. `glob` 的定位：先缩小候选文件集合

`glob` 不看内容，只看路径。

因此适合：

- 找 `*config*`
- 找 `**/*.sql.ts`
- 找某类模块文件

它常常是最便宜的第一步。

换句话说：

- `glob` 解决“去哪里找”
- 还不解决“里面有没有我要的内容”

---

# 10. `grep`：内容匹配工具

`GrepTool` 负责在文件内容中匹配 regex。

## 10.1 流程

1. ask `permission: grep`
2. 解析 `searchPath`
3. `assertExternalDirectory()`
4. `rg -nH --hidden --no-messages --field-match-separator=| --regexp <pattern>`
5. 可附带 `--glob <include>`
6. 解析每条命中
7. 用 `Filesystem.stat` 读 mtime
8. 按文件修改时间倒序排序
9. 最多返回 100 条命中

## 10.2 输出形态

输出是人类友好的文本：

- `Found N matches`
- 每个文件分组
- 每条 `Line X: ...`
- 超长行裁到 2000 chars
- 若超限则补 truncation hint

这说明 grep tool 主要面向 agent/人类阅读，而不是 API 结构消费。

---

# 11. `grep` 为什么不用 `Ripgrep.search()`

虽然底层已有 `Ripgrep.search()`，但 `grep` 仍自己调用 `rg` 文本模式。

原因很可能是：

- 这里要直接生成更简洁的人类可读输出
- 不需要完整 JSON 结构
- 只需要命中行号与文本

这说明 OpenCode 在 retrieval 层做了明确区分：

- **内部结构化接口**
- **面向 agent 提示词的人类可读接口**

---

# 12. `grep` 的错误处理语义

它特别处理了 `rg` exit code：

- `0`：有匹配
- `1`：无匹配
- `2`：有错误，但可能仍有输出

如果是 `2` 且仍有 output，不直接失败，而是在结果尾部加：

- `(Some paths were inaccessible and skipped)`

这是很成熟的搜索工具包装策略：

- 尽量返回部分有效结果
- 同时保留“不完整搜索”的信号

---

# 13. `read`：精读与局部展开工具

`ReadTool` 不是 search，而是：

- **authoritative content reader**

它负责：

- 读取文件
- 读取目录 entries
- 分页读大文件
- 图片/PDF 附件化
- binary 检测
- 注入 instruction reminder

## 13.1 为什么 read 是检索栈的一部分

因为搜索只负责定位证据，真正确认逻辑仍要靠精读源码。

因此在 retrieval pipeline 中，`read` 是最后确认层。

---

# 14. `read` 的分页语义很关键

`read` 支持：

- `offset`
- `limit`

并在结果里明确告诉你：

- 当前显示哪几行
- 下一次该用哪个 offset 继续

同时还有限制：

- 单行最多 2000 chars
- 总输出最多 50 KB

这说明 `read` 不是一次性全量 dump，而是：

- **有边界的顺序展开器**

非常适合 agent 分段精读大型文件。

---

# 15. `read` 的多模态语义

如果目标是：

- image
- PDF

则直接返回附件，文本 output 只给成功提示。

如果是 binary，则直接拒绝。

这说明 `read` 并不是单纯文本 cat，而是统一文件读取入口。

---

# 16. `lsp`：语义级检索工具

`LspTool` 提供的不是字符串匹配，而是语言服务器语义操作：

- `goToDefinition`
- `findReferences`
- `hover`
- `documentSymbol`
- `workspaceSymbol`
- `goToImplementation`
- call hierarchy 系列

## 16.1 工作流

1. 边界检查
2. ask `permission: lsp`
3. 检查文件存在
4. `LSP.hasClients(file)`
5. `LSP.touchFile(file, true)`
6. 调具体 LSP API
7. 返回 JSON stringify 结果

这说明 `lsp` 负责的是：

- **跨文本表面的语义跳转**

---

# 17. 为什么 `lsp` 不替代 `grep`

LSP 很强，但不能替代 grep：

- LSP 依赖语言服务器存在且可用
- 不是每种文件类型都有 LSP
- 模糊文本线索时 grep 更快
- 非代码文本、配置、模板文件 often 无法依赖 LSP

因此合理工作流通常是：

- 先 `glob/grep/read`
- 再用 `lsp` 做语义确认与扩展

---

# 18. `codesearch`：外部代码搜索能力

grep 结果表明系统注册了：

- `CodeSearchTool`

并且它受：

- `permission: codesearch`

控制。

同时在 `tool/registry.ts` 中可见：

- `codesearch` / `websearch` 只对特定 provider 或 flag 打开

这说明 `codesearch` 并不是默认本地工具，而是：

- **受模型/能力门控的外部检索面**

---

# 19. `codesearch` 与本地搜索的分工

本地搜索回答的是：

- 当前 workspace 里有什么

而 `codesearch` 回答的是：

- 外部世界里有没有相关实现、模式或先例

所以它不是 `grep` 的加强版，而是另一个维度：

- **workspace-local retrieval** vs **world knowledge retrieval**

---

# 20. `File.state()`：本地检索缓存层

在 `file/index.ts` 中，`File.state()` 会用：

- `Ripgrep.files({ cwd: Instance.directory })`

构建文件/目录快照缓存。

这说明除了显式工具外，系统本身也维护了一份本地文件索引，用于：

- list
- 状态展示
- 更快的项目结构获取

因此检索栈不仅服务工具调用，也服务 runtime 内部状态。

---

# 21. 一个典型的 OpenCode 检索工作流

从这些工具职责可以抽象出一个非常清晰的推荐流程：

## 21.1 定位目录或文件候选

- `glob`
- `ls`
- `Ripgrep.tree()`

## 21.2 定位文本命中

- `grep`
- `Ripgrep.search()`（服务端/结构化场景）

## 21.3 精读上下文

- `read`

## 21.4 做语义扩展

- `lsp`

## 21.5 必要时引入外部知识

- `codesearch`
- `websearch`

这就是 OpenCode 的 retrieval ladder。

---

# 22. 权限与边界如何影响检索栈

所有本地检索工具都不是无条件访问文件系统。

它们通常叠加：

- 工具权限：`glob` / `grep` / `read` / `lsp`
- 边界权限：`external_directory`

这说明 OpenCode 把检索也视为敏感能力，而不只是“安全无害的读操作”。

这是正确的，因为检索本身就可能泄露大量信息。

---

# 23. 输出形态为什么不统一成 JSON

因为这些工具服务对象不同：

## 23.1 面向 agent 推理的工具

像 `glob` / `grep` / `read`，更偏向：

- 低摩擦文本结果
- 易读
- 容易直接进 prompt

## 23.2 面向系统内部与 API 的能力

像 `Ripgrep.search()` / `lsp.metadata.result`，更偏向：

- 结构化数据
- 程序级消费

因此“不统一”其实是有意识的：

- 在最适合消费的一层返回最合适的形态

---

# 24. 这个模块背后的关键设计原则

## 24.1 检索要分层，而不是试图用一个工具包打天下

路径、文本、语义、外部世界是不同问题。

## 24.2 本地检索应建立在稳定的 `ripgrep` 基础设施上

所以有自动发现/下载/封装的 `Ripgrep`。

## 24.3 面向 agent 的输出应优先可读性

所以 `grep` 和 `read` 输出不是纯 JSON。

## 24.4 语义检索应作为高阶能力补充，而不是基础依赖

所以 `lsp` 是增强层，不是前提条件。

---

# 25. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/file/ripgrep.ts`
2. `packages/opencode/src/tool/glob.ts`
3. `packages/opencode/src/tool/grep.ts`
4. `packages/opencode/src/tool/read.ts`
5. `packages/opencode/src/tool/lsp.ts`
6. `packages/opencode/src/tool/codesearch.ts`
7. `packages/opencode/src/file/index.ts`
8. `packages/opencode/src/server/routes/file.ts`

重点盯住这些函数/概念：

- `Ripgrep.filepath()`
- `Ripgrep.files()`
- `Ripgrep.search()`
- `Ripgrep.tree()`
- `GlobTool.execute()`
- `GrepTool.execute()`
- `ReadTool.execute()`
- `LSP.hasClients()`
- `CodeSearchTool`

---

# 26. 下一步还需要深挖的问题

这一篇已经把检索栈主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`tool/codesearch.ts` 的完整请求协议、限流与返回格式还值得单独拆文档
- **问题 2**：`server/routes/file.ts` 暴露的结构化搜索接口与工具层 grep 的使用边界还可以继续对照
- **问题 3**：`ls` 工具与 `glob` 的角色边界还可继续更细致比较
- **问题 4**：大仓库下 `Ripgrep.files()` 构建文件索引的性能特征还值得继续评估
- **问题 5**：`read` 注入的 instruction reminder 与 retrieval pipeline 的耦合程度还可以继续梳理
- **问题 6**：LSP 结果的 JSON 输出是否还可以进一步归一化成人类更好读的格式，这点值得从产品角度继续思考
- **问题 7**：外部 `codesearch` 与本地 `grep/read` 的自动编排策略是否已经存在，还值得继续追踪 agent planner
- **问题 8**：在 remote workspace 或多 workspace 模式下，当前 retrieval 栈是否需要额外的 workspace routing 适配，还值得继续观察

---

# 27. 小结

`search_and_retrieval_stack` 模块定义了 OpenCode 如何分层获取代码证据：

- `Ripgrep` 提供稳定的本地文件枚举与文本搜索基础设施
- `glob` 负责路径级定位
- `grep` 负责文本命中定位
- `read` 负责权威内容精读
- `lsp` 提供语义级导航与引用/定义追踪
- `codesearch` 则把检索范围扩展到工作区之外

因此，这一层不是几个孤立工具的堆叠，而是一套围绕“低成本定位 -> 精确展开 -> 语义追踪 -> 外部补充”构建的检索体系。
