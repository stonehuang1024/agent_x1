# VS Code Integration 模块详细解读

---

# 1. 模块定位

这一篇专门解读 OpenCode 的 VS Code 集成层。

核心问题是：

- 仓库里到底有没有 VS Code 插件
- 如果有，它做了什么，不做什么
- 它是如何和 opencode runtime 对接的
- 为什么它的实现很薄，但架构上反而更干净

核心源码包括：

- `sdks/vscode/package.json`
- `sdks/vscode/src/extension.ts`

这一层不是 runtime 主体，而是一个 **IDE bridge layer**。

它的作用不是在 VS Code 扩展宿主里重写整套 agent runtime，而是：

- 把 IDE 上下文采集出来
- 启动本地 opencode runtime
- 通过本地接口把上下文传递过去

---

# 2. 模块边界：插件做什么，不做什么

## 2.1 它做什么

从源码可确认，VS Code 插件负责：

- 注册命令
- 注册快捷键
- 在 VS Code 中创建/聚焦 opencode terminal
- 给 opencode 进程注入环境变量
- 探测 opencode 本地端口服务是否 ready
- 获取当前文件与选区
- 将当前文件表示为 `@path#Lx-Ly`
- 通过本地 HTTP API 把 prompt append 给运行中的 opencode

## 2.2 它不做什么

它不负责：

- 直接调用 LLM
- 自己维护 session loop
- 自己做工具调用编排
- 自己管理上下文压缩
- 自己处理 provider 兼容
- 自己实现权限系统

这意味着它不是“另一个独立 agent 产品”，而是 OpenCode runtime 的前端入口之一。

---

# 3. 扩展清单：`package.json`

## 3.1 扩展元信息

`sdks/vscode/package.json` 表明：

- 扩展名：`opencode`
- publisher：`sst-dev`
- main：`./dist/extension.js`
- VS Code engine：`^1.94.0`

这说明它是标准 VS Code 扩展结构。

## 3.2 注册命令

扩展声明了 3 个命令：

- `opencode.openTerminal`
- `opencode.openNewTerminal`
- `opencode.addFilepathToTerminal`

这已经足够说明其产品定位：

- 打开/复用 opencode
- 把当前文件上下文送进 opencode

## 3.3 菜单与快捷键

它还定义了：

- editor/title 菜单入口
- 快捷键：
  - `cmd+escape` / `ctrl+escape`
  - `cmd+shift+escape` / `ctrl+shift+escape`
  - `cmd+alt+k` / `ctrl+alt+k`

因此这个扩展的使用方式是非常轻量直接的：

- 快速打开
- 快速开新 tab
- 快速插入当前文件引用

---

# 4. 扩展入口：`activate(context)`

## 4.1 主入口结构

`extension.ts` 中的 `activate(context)` 非常短，但结构很清晰：

- 注册 `openNewTerminal`
- 注册 `openTerminal`
- 注册 `addFilepathToTerminal`
- push 到 `context.subscriptions`

这是一种最标准的 VS Code command-driven extension 结构。

## 4.2 为什么设计这么薄

这是一个很重要的架构选择。

如果插件本身承担了太多 runtime 逻辑，就会出现：

- CLI 与 VS Code 两套 runtime 行为分叉
- provider/tool/permission/context 逻辑重复实现
- 调试和维护复杂度陡增

OpenCode 的做法是：

- 把 runtime 统一留在 opencode 主程序里
- 扩展只负责桥接

这是很合理的分层。

---

# 5. 终端桥接：`openTerminal()` 的设计

## 5.1 整体流程

`openTerminal()` 的执行流程是：

1. 随机生成一个端口
2. 创建 VS Code terminal
3. 注入环境变量
4. 执行 `opencode --port <port>`
5. 轮询本地 HTTP 服务是否 ready
6. 如果当前有活动文件，则 append prompt

这不是简单“打开一个 shell”，而是：

- **在 IDE 中拉起一个附带本地通信端口的 opencode runtime**

## 5.2 随机端口算法

插件会生成一个随机端口，范围：

- `16384 ~ 65535`

这是一个简单的 **ephemeral high port selection** 策略。

目的是：

- 避免固定端口冲突
- 允许多次新开 terminal tab 时各自运行独立实例

## 5.3 terminal 环境变量

创建 terminal 时会注入：

- `_EXTENSION_OPENCODE_PORT`
- `OPENCODE_CALLER = "vscode"`

这两个变量各有作用：

### `_EXTENSION_OPENCODE_PORT`

用于：

- 扩展后续知道当前 terminal 对应哪个本地服务端口
- `addFilepathToTerminal` 可以精准把 prompt 发给该 terminal 对应的 runtime

### `OPENCODE_CALLER`

用于：

- runtime 感知调用来源是 VS Code
- 这类 caller 信息通常可用于 telemetry、UI 差异化、行为统计或入口判断

## 5.4 为什么要 `opencode --port <port>`

这说明 opencode 运行时不只是一个单纯 TUI，而是带本地服务能力的程序。

扩展之所以能 append prompt，而不是只能模拟键盘输入，就是因为 runtime 暴露了本地 HTTP 接口。

这点很关键：

- VS Code 插件并不直接把 prompt 丢到 shell stdin
- 它优先通过 runtime API 与 opencode 通信

---

# 6. ready 检测与连接建立

## 6.1 轮询逻辑

启动 terminal 后，插件会：

- 最多尝试 10 次
- 每次等待 200ms
- 请求 `http://localhost:${port}/app`

只要请求成功，就认为 runtime ready。

这是一个很轻量的 **poll until ready** 算法。

总等待时间大约为：

- 10 * 200ms = 2s 左右

## 6.2 为什么要这么做

因为 terminal 中启动 `opencode --port` 是异步的。

如果 runtime 还没 ready，插件就直接发 append 请求，会失败。

所以插件必须等到本地服务真正起来后，再注入 prompt。

## 6.3 为什么不是更复杂的握手协议

因为这里的需求很简单：

- 检查服务是否活着
- 能接收 append prompt 就够了

所以用一个轻量 GET 探测就足够。这种设计成本低，也便于调试。

---

# 7. 复用终端 vs 新建终端

## 7.1 `openTerminal`

这个命令会先查：

- `vscode.window.terminals.find((t) => t.name === TERMINAL_NAME)`

如果已存在名为 `opencode` 的 terminal：

- 直接 `show()`
- 不重复启动新实例

这是一种 **singleton terminal reuse** 策略。

## 7.2 `openNewTerminal`

这个命令不会走“查找已有 terminal 并复用”的逻辑，而是直接：

- `await openTerminal()`

注意，这里的 `openTerminal()` 是内部 helper，不是命令函数本身。因此它总会新建 terminal。

所以扩展同时支持两种用户习惯：

- 想复用当前 opencode tab
- 想专门开一个新 tab 做另一项工作

---

# 8. 当前文件与选区如何编码

## 8.1 `getActiveFile()` 的流程

插件会：

1. 获取当前 active editor
2. 获取当前 document
3. 获取 workspace folder
4. 使用 `vscode.workspace.asRelativePath(document.uri)` 生成工作区相对路径
5. 构造 `@relativePath`
6. 如果有选区，则附加行号范围

最终输出形式如：

- `@src/session/prompt.ts`
- `@src/session/prompt.ts#L120`
- `@src/session/prompt.ts#L120-L180`

## 8.2 这是引用，而不是内容本身

这点很关键。

插件并没有：

- 读取全文
- 直接把选中代码作为纯文本塞到 prompt

而是先传“引用表达式”。

这样做的优点有：

- prompt 更短
- 用户意图更明确
- runtime 可晚绑定真实内容
- 更容易让 `resolvePromptParts()`、`read`、instruction 注入协同工作

这是一种非常干净的 **locator over payload** 设计。

## 8.3 行号为什么转换为 1-based

VS Code API 中 line 是 0-based。

插件会显式：

- `selection.start.line + 1`
- `selection.end.line + 1`

转换成更符合人类与 markdown 引用习惯的 1-based 行号。

这样与：

- `#L10`
- `#L10-L20`

等引用格式保持一致。

---

# 9. prompt 注入：`appendPrompt()`

## 9.1 通信接口

插件使用：

- `POST http://localhost:${port}/tui/append-prompt`

请求体为：

- `{ text }`

这说明 runtime 已经提供了一个专门的 prompt append 接口。

## 9.2 为什么 append prompt 很重要

这使得扩展可以：

- 把当前文件引用插入已有对话上下文
- 而不是必须重启整个 agent
- 也不是只能模拟用户在 terminal 里手动输入

这对于 IDE 体验非常重要，因为用户常见行为是：

- 正在和 agent 对话
- 切到另一个文件
- 想把这个文件追加进当前问题

append-prompt API 正是为这种交互设计的。

## 9.3 fallback 行为

在 `addFilepathToTerminal` 中，如果 terminal 没有 `_EXTENSION_OPENCODE_PORT`：

- 就会 `terminal.sendText(fileRef, false)`

也就是说：

- **优先走本地 API**
- **没有端口信息时，退回 shell 发送文本**

这个 fallback 不代表架构主路径，而是兼容非标准状态的兜底手段。

---

# 10. `addFilepathToTerminal` 的交互意义

## 10.1 它做的不是“打开文件”

这个命令做的是：

- 把当前活动文件/选区编码成引用
- 追加到当前活动 opencode terminal

也就是说，它不是 editor 命令，而是**上下文注入命令**。

## 10.2 为什么要判断 active terminal

插件会检查：

- 是否有 `vscode.window.activeTerminal`
- terminal 名字是否为 `opencode`

只有满足这些条件才会执行 append。

这是因为它的目标不是随便往任何 shell 里打字，而是仅与 opencode runtime 协作。

## 10.3 这与 runtime 的结合方式

最终被送进去的是：

- 一个路径引用字符串

然后真正的上下文解析、文件读取、指令注入，都由 runtime 侧继续完成。

这说明 IDE 与 runtime 的契约非常明确：

- IDE 提供定位
- runtime 提供语义处理

---

# 11. 这一实现为什么说“薄但很实用”

## 11.1 命令层

扩展提供了最常用的 3 个动作：

- 打开 opencode
- 新建 opencode tab
- 注入当前文件/选区

从产品角度看，这已经覆盖了最重要的 IDE 使用路径。

## 11.2 终端层

通过 VS Code terminal，扩展获得了：

- 与 CLI/TUI 统一的运行载体
- 与用户原有终端工作流一致的交互感

这比自建一个复杂 webview agent shell 轻很多。

## 11.3 上下文层

利用 `@path#Lx-Ly`，扩展不承担源码解析复杂性，只负责精准表达用户注意点。

## 11.4 通信层

利用本地 HTTP API，扩展能把“追加上下文”与“终端显示/交互”分离开，避免脆弱的 stdin 注入依赖。

所以说它“薄”，是因为：

- 没有重复实现 runtime
- 没有重复实现 provider/tool/context 系统

说它“实用”，是因为：

- 刚好补上了 IDE 场景最关键的能力缺口

---

# 12. 这个模块背后的架构原则

## 12.1 入口与 runtime 分离

这是整个设计最重要的原则。

- VS Code 是入口
- opencode 主程序是 runtime

这样 CLI、TUI、VS Code 不会各自维护一套 agent 核心逻辑。

## 12.2 IDE 负责定位，runtime 负责语义

插件只负责：

- 当前文件
- 当前选区
- 当前 terminal 绑定
- prompt append

而：

- 文件读取
- instruction 注入
- 工具调用
- prompt 组装
- provider 适配

全部仍由 runtime 完成。

## 12.3 优先使用稳定接口

通过 `append-prompt` 这样的本地 API，扩展避免了大量脆弱行为，例如：

- 依赖 shell prompt 状态
- 模拟复杂 terminal 输入行为
- 与终端渲染强耦合

## 12.4 交互方式贴近开发者习惯

命令、快捷键、terminal、相对路径引用，这些都高度贴合真实开发者工作流。

---

# 13. 与其他模块的关系

## 13.1 与 `session_loop_prompt_context`

VS Code 插件把当前文件引用交给 runtime，最终由 loop、prompt、message-v2 决定如何进入上下文。

## 13.2 与 `code_retrieval_and_context_injection`

插件只提供 `@path#Lx-Ly`，后续如何解析、读取、附加 instruction，由检索与上下文模块完成。

## 13.3 与 `tool_runtime_and_execution`

插件本身不执行工具，但它帮助模型更容易锁定正确文件，从而触发更高质量的 read/grep/edit/tool 流程。

## 13.4 与 provider 层

插件完全不关心 provider 差异，这些复杂性都被 runtime 吸收掉了。

---

# 14. 推荐阅读顺序

建议按这个顺序读：

1. `sdks/vscode/package.json`
2. `sdks/vscode/src/extension.ts`
3. `packages/opencode/src/session/prompt.ts`
4. `packages/opencode/src/tool/read.ts`
5. `packages/opencode/src/session/instruction.ts`

重点盯住这些函数/概念：

- `activate()`
- `openTerminal()`
- `appendPrompt()`
- `getActiveFile()`
- `/tui/append-prompt`
- `SessionPrompt.resolvePromptParts()`

---

# 15. 下一步还需要深挖的问题

这个模块已经把主结构解释清楚了，但还有一些值得继续深挖的问题：

- **问题 1**：`/tui/append-prompt` 在 runtime 侧的具体实现细节是什么，是否直接追加 user message part
- **问题 2**：多个 opencode terminals 同时存在时，扩展与各端口的绑定关系是否完全可靠
- **问题 3**：当前 workspace 如果是 multi-root，`asRelativePath()` 与 runtime path 解析是否有边界问题
- **问题 4**：扩展是否支持把 selection 的实际内容而不是路径引用直接传给 runtime，如果不支持，是否有设计上的刻意限制
- **问题 5**：如果 opencode 进程启动失败或端口被占用，当前错误反馈对用户是否足够清晰
- **问题 6**：扩展与未来 Webview/UI 形态是否会共用这套本地 API，还是会分叉成另一套协议
- **问题 7**：`OPENCODE_CALLER=vscode` 在 runtime 中具体影响哪些行为或遥测字段
- **问题 8**：终端复用策略是否需要更细粒度，比如按 workspace/session 维度复用而不是按 terminal name

---

# 16. 小结

`vscode_integration` 模块展示了 OpenCode 一个非常成熟的架构选择：

- 不把 IDE 插件做成另一套 agent runtime
- 而是把它做成一个轻量、明确、可靠的桥接层

它负责：

- 启动 runtime
- 绑定 terminal 与本地端口
- 获取当前文件与选区
- 把定位引用追加进当前对话

而真正的：

- 上下文解析
- 工具调用
- 模型交互
- 权限与压缩

全部仍由 opencode runtime 统一负责。

因此，这一层虽然代码不多，但它非常清晰地体现了 OpenCode 的系统边界设计能力。
