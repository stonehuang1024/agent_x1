# Format / Output Normalization 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的格式化与输出规范化链路。

核心问题是：

- 文件编辑后自动格式化是如何触发的
- formatter 配置如何覆盖内置 formatter
- 为什么工具输出需要统一截断和外置保存
- `Truncate` 如何在保护上下文窗口的同时保留完整结果
- tool output、message output、diagnostics 输出是如何被规范化的

核心源码包括：

- `packages/opencode/src/format/index.ts`
- `packages/opencode/src/tool/truncation.ts`
- `packages/opencode/src/tool/registry.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/tool/write.ts`
- `packages/opencode/src/tool/edit.ts`
- `packages/opencode/src/tool/apply_patch.ts`

这一层本质上是 OpenCode 的**结果收敛、格式统一与上下文预算保护基础设施**。

---

# 2. 为什么需要“格式化”和“输出规范化”两层能力

OpenCode 在修改代码与读取工具结果时会同时面对两类问题：

## 2.1 文件内容质量问题

- 编辑后代码风格可能不一致
- 某些语言强依赖 formatter 维持稳定格式

这对应：

- `Format`

## 2.2 输出体积与可读性问题

- shell / grep / read / plugin tool 输出可能非常大
- 模型上下文窗口有限
- 需要保留完整结果供后续精细读取

这对应：

- `Truncate`
- 各工具的输出 metadata / diagnostics 附加规范

所以这篇的两部分虽然不同，但本质都在解决：

- **把原始结果收敛成更适合 agent runtime 消费的形式**

---

# 3. `Format`：文件编辑后的自动格式化层

## 3.1 状态模型

`Format.state()` 是 `Instance.state(...)`，内部维护：

- `enabled: Record<string, boolean>`
- `formatters: Record<string, Formatter.Info>`

这说明 formatter 可用性也是：

- 按 instance 目录缓存
- 按 formatter name 缓存 enabled 结果

## 3.2 为什么缓存 enabled

某些 formatter 的 `enabled()` 可能需要探测命令是否存在、环境是否可用。

把结果缓存下来可以避免：

- 每次文件编辑都重复做昂贵探测

---

# 4. formatter 来源：内置 + config 覆盖

`Format.state()` 会先收集：

- `./formatter` 模块里导出的内置 formatter

再合并：

- `cfg.formatter`

## 4.1 全局禁用

若：

- `cfg.formatter === false`

则直接返回空 formatter 集。

## 4.2 单 formatter 覆盖

如果某个 config formatter：

- `disabled: true`

则直接把同名 formatter 从可用集合中删掉。

否则会 `mergeDeep` 覆盖：

- `command`
- `extensions`
- 其他字段

并把 `enabled()` 固定为 always true。

这说明 config formatter 不只是开关，还能完整替换 formatter 定义。

---

# 5. `getFormatter(ext)`：按扩展名选择 formatter

流程是：

1. 读取当前格式化器集合
2. 遍历所有 formatter
3. 看是否支持该扩展名
4. 看 `isEnabled(item)` 是否为真
5. 收集匹配项

这意味着：

- 同一个扩展名可以命中多个 formatter
- OpenCode 会依次运行它们

这说明 formatter 并非强制 one-of 选择，而更接近一个：

- **可串联 formatter pipeline**

---

# 6. `Format.init()`：文件编辑事件驱动格式化

`Format.init()` 会订阅：

- `File.Event.Edited`

一旦收到事件：

1. 取目标文件路径
2. 根据 `path.extname(file)` 找 formatter
3. 逐个 `Process.spawn(...)` 执行 formatter command

## 6.1 工作目录

formatter command 在：

- `cwd: Instance.directory`

下执行。

## 6.2 环境变量

会带上：

- `process.env`
- `item.environment`

这说明 formatter 能很好地复用项目环境与自定义执行环境。

---

# 7. 为什么格式化基于 `File.Event.Edited` 而不是直接嵌入每个写工具

这是个很好的架构选择。

如果把格式化写死在每个 edit/write/apply_patch 工具里，会导致：

- 工具实现重复
- 外部编辑器修改不会触发
- 格式化策略难统一

现在通过：

- 写入动作 -> `File.Event.Edited`
- `Format` 订阅该事件

实现了解耦。

这说明格式化被视为：

- **编辑后副作用处理层**

而不是某个具体工具的私有逻辑。

---

# 8. `Format.status()`：可观测性出口

`status()` 会返回每个 formatter 的：

- `name`
- `extensions`
- `enabled`

这说明 formatter 不是黑盒，系统有正式方式向 UI / API 暴露当前格式化能力状态。

---

# 9. `Truncate`：为什么工具输出必须统一截断

工具输出很容易爆炸：

- shell 命令输出大量日志
- grep 返回数百上千条匹配
- plugin tool 返回大块文本
- prompt 拼装后的工具结果可能极长

如果把这些结果原样全部喂回模型：

- 浪费上下文
- 降低后续推理效率
- 更容易造成 token overflow

因此需要统一截断层。

---

# 10. `Truncate` 的核心常量

默认上限是：

- `MAX_LINES = 2000`
- `MAX_BYTES = 50 * 1024`

并把完整原文外置到：

- `Global.Path.data/tool-output`

## 10.1 含义

OpenCode 并不是简单“扔掉超出的内容”，而是：

- 保留预览
- 将完整输出落地成文件
- 给模型一个二次读取路径

这是比单纯截断高级很多的设计。

---

# 11. `Truncate.output(text, options, agent?)`：输出收敛算法

## 11.1 不超限时

如果：

- 行数 <= `maxLines`
- 字节数 <= `maxBytes`

则直接返回：

- `{ content: text, truncated: false }`

## 11.2 超限时

则：

1. 按 `direction` 选择 head 或 tail 模式
2. 在行数和字节数双约束下构建 preview
3. 计算被移除量：
   - 按 bytes 或 lines
4. 生成一个 tool output file id
5. 把完整 text 写到 `tool-output/<id>`
6. 返回：
   - `content: preview + truncation hint`
   - `truncated: true`
   - `outputPath`

这说明 `Truncate` 本质上是在做：

- **preview synthesis + overflow offloading**

---

# 12. `direction = head | tail`

`Truncate` 允许：

- `head`
- `tail`

两种预览策略。

## 12.1 head

保留前面的内容，适合：

- shell 输出开头更关键
- 文档/日志前部包含主要结构

## 12.2 tail

保留末尾内容，适合：

- 最近日志更关键
- 错误栈通常在结尾

这说明截断并不是一刀切，而是允许根据工具语义选择更适合的保留方向。

---

# 13. 为什么 hint 文案会因 agent 能力不同而变化

`Truncate.hasTaskTool(agent)` 会看当前 agent 是否允许使用：

- `task`

如果允许，hint 会鼓励：

- 用 Task 工具让 explore agent 继续处理输出文件

否则提示：

- 用 Grep / Read + offset/limit 自己精查

这说明输出规范化并不只是静态字符串模板，而会根据 agent 能力面动态给出更优后续操作建议。

这是很有 agentic 产品感的设计。

---

# 14. `ToolRegistry.fromPlugin()`：plugin tool 输出如何被统一收敛

plugin tool 执行完 `def.execute()` 后，并不会原样直接返回。

还会经过：

- `Truncate.output(result, {}, initCtx?.agent)`

然后返回：

- `output`
- `metadata.truncated`
- `metadata.outputPath`

这说明插件工具被正式纳入统一输出预算控制中，不会绕过系统的上下文保护机制。

---

# 15. `session/prompt.ts`：普通工具结果如何统一进入消息系统

在工具执行结果写回消息前，也会做：

- `const truncated = await Truncate.output(textParts.join("\n\n"), {}, input.agent)`

然后把：

- `metadata.truncated`
- `metadata.outputPath`
- `output: truncated.content`

一起写入最终结果。

这说明不仅 plugin tool，连整个运行时工具输出聚合到 prompt/message 前，也会再过一遍统一截断层。

因此 `Truncate` 是 tool runtime 的正式基础设施，而不是某几个工具的局部逻辑。

---

# 16. `tool/tool.ts` 的语义：有些工具自己处理截断

grep 结果里也能看到：

- tool runtime 会“跳过那些自己处理 truncation 的工具”

这说明 OpenCode 同时支持两种模式：

## 16.1 工具自行控制输出规模

例如：

- `grep`
- `glob`
- `ls`

这类工具天然知道自己的结果结构，能更好地在语义层做截断。

## 16.2 最终统一截断兜底

对普通工具结果，再由 `Truncate` 做统一收口。

这是一种很合理的双层策略：

- 结构化工具先按语义截断
- 通用层再按上下文预算兜底

---

# 17. 结构化工具自己的截断策略

从 grep 结果可以看出很多工具都有自己的 `metadata.truncated` 语义：

## 17.1 `grep`

- 只显示前 100 条匹配
- 输出总匹配数和隐藏数量

## 17.2 `glob`

- 只显示前 100 条路径
- 给出更具体路径/模式建议

## 17.3 `ls`

- metadata 中记录 `count` 与 `truncated`

这说明 OpenCode 鼓励工具在它们最理解自己输出结构的地方先做信息压缩。

---

# 18. diagnostics 输出：另一类结果规范化

在：

- `write.ts`
- `edit.ts`
- `apply_patch.ts`

里，还会在结果后附加：

- `<diagnostics file="..."> ... </diagnostics>`

其中 diagnostics 文本来自：

- `LSP.Diagnostic.pretty`

并且限制：

- 每文件最多若干条
- 超出则加 suffix `... and N more`

这说明 OpenCode 对“修改完成后的问题反馈”也做了统一结构化规范，而不是把任意日志直接拼进去。

---

# 19. `LSP.Diagnostic.pretty` 的意义

虽然这次没重读 LSP 文件，但 grep 结果可见它被大量用于：

- 把诊断转成稳定文本格式

这样一来：

- edit/write/apply_patch 的后处理输出具有统一风格
- 模型更容易消费 diagnostics
- UI/日志也更一致

这说明输出规范化并不只针对长度，也包括：

- **错误信息格式统一**

---

# 20. `Truncate.cleanup()`：为什么外置输出还要定期清理

完整输出文件会放到：

- `tool-output/`

并通过 `Scheduler.register(...)` 每小时清理一次，保留 7 天。

## 20.1 为什么必须清理

如果不清理：

- 高强度工具调用会迅速堆满磁盘
- 大量过期输出文件没有长期价值

## 20.2 清理策略

根据 identifier timestamp 与 cutoff 比较，删除过期项。

这说明 tool output offload 不是临时 hack，而是配套了完整 retention 策略。

---

# 21. `Format` 与 `Truncate` 的关系

两者虽然处理对象不同，但都在做“原始结果 -> 更适合系统继续处理的结果”。

## 21.1 `Format`

面向：

- 文件内容本身

目标是：

- 让写回磁盘的结果更规范

## 21.2 `Truncate`

面向：

- 工具输出文本

目标是：

- 让送回模型的结果更可控

因此它们共同构成了 OpenCode 的：

- **input/output normalization layer**

---

# 22. 这个模块背后的关键设计原则

## 22.1 编辑后副作用应事件化解耦

格式化通过 `File.Event.Edited` 触发，而不是嵌死在写工具中。

## 22.2 输出预算保护必须是系统级能力

不能依赖每个工具自己自觉控制长度。

## 22.3 完整结果不应丢失，而应外置存储

这样模型既能拿到摘要，也能在需要时精读原文。

## 22.4 结构化工具应先做语义压缩，再交给统一截断层兜底

这样既保留语义质量，又保护上下文窗口。

---

# 23. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/format/index.ts`
2. `packages/opencode/src/tool/truncation.ts`
3. `packages/opencode/src/tool/tool.ts`
4. `packages/opencode/src/tool/registry.ts`
5. `packages/opencode/src/session/prompt.ts`
6. `packages/opencode/src/tool/write.ts`
7. `packages/opencode/src/tool/edit.ts`
8. `packages/opencode/src/tool/apply_patch.ts`

重点盯住这些函数/概念：

- `Format.init()`
- `getFormatter()`
- `Format.status()`
- `Truncate.output()`
- `Truncate.cleanup()`
- `metadata.truncated`
- `outputPath`
- `LSP.Diagnostic.pretty`

---

# 24. 下一步还需要深挖的问题

这一篇已经把格式化与输出规范化主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`./formatter` 下各内置 formatter 的探测逻辑、命令模板和语言覆盖面还值得单独拆文档
- **问题 2**：`File.Event.Edited` 的触发点完整列表还需要继续 grep，才能更完整描出 format 触发链路
- **问题 3**：tool runtime 中“哪些工具自己处理 truncation，哪些交给统一层”的边界还值得继续梳理
- **问题 4**：超大二进制输出、图片输出与 text/base64 输出之间的规范是否完全一致，还可继续检查
- **问题 5**：`outputPath` 的后续读取 UX 是否足够顺畅，还值得从 CLI/TUI 角度继续评估
- **问题 6**：格式化失败目前主要记日志，是否还应更显式反馈给用户，这个产品边界值得讨论
- **问题 7**：截断阈值对不同模型上下文窗口是否应动态调整，还值得进一步分析
- **问题 8**：diagnostics 注入与 tool 输出正文之间的排序、权重和去重策略还可继续精细化研究

---

# 25. 小结

`format_and_output_normalization` 模块定义了 OpenCode 如何在“写文件”和“消费工具结果”两端保持系统输出质量：

- `Format` 通过编辑事件驱动自动格式化，保证落盘代码更规范
- `Truncate` 通过 preview + 外置完整文件的方式控制工具输出体积
- plugin tool、普通 tool 和带 diagnostics 的写工具最终都被收敛到统一输出规范中
- 定期 cleanup 则保证这些辅助输出不会无限堆积

因此，这一层不是零散后处理逻辑，而是 OpenCode 保持代码质量、上下文预算和输出可消费性的基础设施。
