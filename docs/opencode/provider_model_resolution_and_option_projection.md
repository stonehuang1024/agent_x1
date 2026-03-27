# Provider / Model Resolution / Option Projection 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 provider / model 解析与参数投影链路。

核心问题是：

- `Provider.getModel()` / `getProvider()` / `getLanguage()` 分别解决什么问题
- 为什么 provider 层既管理模型清单，也管理 SDK 创建、认证、headers、runtime options
- `variant` 如何参与模型参数投影
- `ProviderTransform` 为什么既处理 message，又处理 schema、providerOptions、unsupported media、caching
- tool schema 为什么要按模型/provider 再做一次投影

核心源码包括：

- `packages/opencode/src/provider/provider.ts`
- `packages/opencode/src/provider/transform.ts`
- `packages/opencode/src/session/llm.ts`
- `packages/opencode/src/session/prompt.ts`

这一层本质上是 OpenCode 的**模型能力解析、provider 兼容适配与请求参数投影基础设施**。

---

# 2. 为什么 provider 层不能只是“按名字找模型”

在 OpenCode 里，provider/model 解析远不只是：

- 根据 `providerID + modelID` 找个字符串

真正还要处理：

- 哪个 SDK factory 创建 provider
- 认证从哪里来
- provider 特有默认 options
- 哪些模型支持 reasoning / media / interleaved / temperature
- variant 如何覆盖 options
- tool schema 需不需要因 provider 特性而变形
- messages 是否要清空空文本、改 toolCallId、插 cache hints

所以 provider 层本质是：

- **能力模型 + 兼容性投影器**

而不是简单 registry。

---

# 3. `Provider` 命名空间的职责边界

从 `provider.ts` 可以明确看出它负责至少四件事：

## 3.1 provider SDK 装载

内置 `BUNDLED_PROVIDERS` 直接绑定：

- OpenAI
- Anthropic
- Azure
- Google
- Bedrock
- OpenRouter
- Copilot 等

## 3.2 custom loader 逻辑

不同 provider 还可有自定义：

- `autoload`
- `getModel`
- `vars`
- `options`

## 3.3 模型能力与配置解析

包括 model options / capabilities / variants。

## 3.4 runtime provider 实例化

最终给 session/llm 层返回：

- provider
- language model
- model info

这说明 Provider 是一个很厚的 runtime abstraction。

---

# 4. `BUNDLED_PROVIDERS`：为什么直接静态导入这么多 SDK

这里不是运行时按 npm 动态 import，而是预先把常见 provider SDK 直接打进系统。

好处包括：

- 可控的打包行为
- 避免运行时缺包
- 更稳定的跨 provider 体验

同时又通过 `CUSTOM_LOADERS` 在 provider-specific 逻辑上保持灵活。

---

# 5. `CUSTOM_LOADERS`：provider-specific 行为不走通用路径

`CUSTOM_LOADERS` 清楚地表明，通用 provider 构造逻辑并不够。

例如：

- `anthropic`：默认加 beta headers
- `opencode`：根据是否有 key 决定是否仅暴露免费模型
- `openai`：固定走 `responses()`
- `github-copilot`：根据 modelID 决定 `responses()` 还是 `chat()`
- `azure`：根据 `useCompletionUrls` 决定 `chat()` / `responses()`
- `amazon-bedrock`：处理 credential chain、region prefix、endpoint

这说明不同 provider 的真实行为差异已经大到必须独立建 loader 分支。

---

# 6. `Provider.getModel()` 的角色

虽然这次没整段重读完整实现，但从 session 层调用点可以确认：

- 它是 `providerID + modelID -> Provider.Model` 的权威入口
- prompt、llm、compaction、subtask、attachment read 全都依赖它

因此 `Provider.Model` 不只是 ID，还封装了：

- limit
- capabilities
- api info
- options
- variants
- headers

这就是后续所有 transform 的输入基础。

---

# 7. `Provider.getProvider()` 与 `Provider.getLanguage()`

在 `LLM.stream()` 里会并行拿：

- `Provider.getLanguage(input.model)`
- `Provider.getProvider(input.model.providerID)`

这意味着：

## 7.1 `getProvider()`

偏 provider-level：

- provider metadata
- provider options
- auth/SDK 级能力

## 7.2 `getLanguage()`

偏 model invocation-level：

- 真正交给 AI SDK 的 language model 对象

也就是说，OpenCode 明确把“provider 配置对象”和“某个模型的可调用语言模型实例”区分开来。

---

# 8. variant：为什么不是单纯 prompt 标签

在 `LLM.stream()` 中：

- 若不是 small model
- 且 `input.model.variants` 存在
- 且 `input.user.variant` 存在

就取：

- `variant = input.model.variants[input.user.variant]`

然后与：

- base options
- model.options
- agent.options

一起 `mergeDeep(...)`。

## 8.1 这说明什么

variant 不是 UI 标签，而是真实 runtime options overlay。

它可以直接改变：

- reasoning effort
- providerOptions
- thinking budget
- 其他 provider 特有参数

---

# 9. option 投影顺序：谁覆盖谁

在 `LLM.stream()` 里最终 options 来源顺序大致是：

1. `ProviderTransform.smallOptions(model)` 或 `ProviderTransform.options(...)`
2. `input.model.options`
3. `input.agent.options`
4. `variant`

这说明优先级是：

- provider/model 默认
- 模型级覆盖
- agent 级覆盖
- variant 最后覆盖

这是很合理的层级：

- variant 往往代表当前回合特化模式，应当最强。

---

# 10. `ProviderTransform.options(...)`：为什么 transform 层要参与默认 options 生成

这说明 provider 参数默认值并不完全存放在 provider.ts 里。

`ProviderTransform` 还负责：

- 根据 model 特性生成 providerOptions
- 统一推导 temperature/topP/topK/maxOutputTokens
- 后续再投影进 AI SDK 所需格式

这是一种很清晰的分工：

- Provider: 解析 provider/model 身份和 SDK
- Transform: 生成和改写请求参数

---

# 11. `chat.params` / `chat.headers` plugin hooks

`LLM.stream()` 会先触发：

- `Plugin.trigger("chat.params", ...)`
- `Plugin.trigger("chat.headers", ...)`

这说明 provider/model 参数并不是最终写死，而是允许插件动态改写：

- temperature
- topP
- topK
- options
- headers

因此 provider 参数投影是：

- provider defaults
n- agent/model/variant overlay
- plugin final adjustment

组成的多阶段流水线。

---

# 12. `maxOutputTokens`：为什么有 provider 特例

`LLM.stream()` 中：

- Codex oauth 场景不设 `maxOutputTokens`
- GitHub Copilot 也不设
- 其他场景用 `ProviderTransform.maxOutputTokens(model)`

这说明有些 provider API 对 max output token 语义不兼容或不适配，必须特判。

因此 output token 控制也不是统一常量，而是 provider-aware 的。

---

# 13. `ProviderTransform.message(...)`：消息投影的总入口

AI SDK stream 调用时，通过 middleware 把：

- `args.params.prompt = ProviderTransform.message(args.params.prompt, input.model, options)`

也就是说，真正送进 provider 前，prompt 还会再做一次 provider-specific 投影。

这条链非常关键，因为最终兼容性很多都在这里实现。

---

# 14. `ProviderTransform.normalizeMessages()`：为什么不同 provider 需要不同消息修正

这里做了很多非常具体的兼容修正：

## 14.1 Anthropic / Bedrock

- 移除空字符串消息
- 移除空 text/reasoning part

因为这类 provider 会拒绝空内容。

## 14.2 Claude 系列

- toolCallId 只保留 `[a-zA-Z0-9_-]`

## 14.3 Mistral

- toolCallId 需为 exactly 9 个 alphanumeric
- 还会修消息顺序，避免 tool message 后直接跟 user message

## 14.4 interleaved reasoning provider

- 把 reasoning parts 汇总到特定 providerOptions 字段
- 并从 content 中移除 reasoning parts

这说明 message compatibility 不是抽象理论，而是大量 provider bug/约束经验的累积结果。

---

# 15. `unsupportedParts()`：不支持的多模态输入不会直接硬失败

对于 user message 里的 file/image parts：

- 先看 MIME -> modality
- 再看 model.capabilities.input[modality]
- 若不支持，就改写成 text：
  - `ERROR: Cannot read ... this model does not support ...`

## 15.1 意义

系统不会把不支持的媒体静默吞掉，也不一定马上抛异常。

而是把限制显式投影进 prompt，让模型向用户说明。

这是很不错的产品化处理。

---

# 16. `applyCaching()`：provider cache hint 注入

对于 Anthropic / OpenRouter / Bedrock / OpenAI-compatible / Copilot，transform 层会在：

- 前两条 system
- 最后两条非 system

消息上注入 ephemeral/default cache hints。

## 16.1 为什么在 transform 层做

因为缓存是 provider 传输层特性，不该污染上层消息模型。

同时它又和具体 provider SDK key 格式强相关，因此最适合放在 transform 层。

---

# 17. `sdkKey()` 与 providerOptions key remap

不同 npm SDK 希望的 providerOptions key 不同：

- openai -> `openai`
- bedrock -> `bedrock`
- anthropic -> `anthropic`
- openrouter -> `openrouter`
- copilot -> `copilot`

而 OpenCode 内部 `providerID` 未必和 AI SDK key 一致。

因此 `ProviderTransform.message()` 会：

- 若 `model.providerID in opts`
- 则 remap 到 SDK 期待的 key

这是一条非常关键的桥接层，不然 providerOptions 很容易 silently 不生效。

---

# 18. `ProviderTransform.temperature/topP/topK()`：模型家族经验参数

这些函数会按 model id 猜测默认采样策略，例如：

- qwen
- claude
- gemini
- glm
- minimax
- kimi

这说明 OpenCode 对不同模型家族积累了经验默认值，而不是一刀切统一采样参数。

这对跨 provider 的默认体验很重要。

---

# 19. `ProviderTransform.variants()`：reasoning effort 的 provider-specific 映射

这个函数非常重要，因为它把“高层 reasoning effort 语义”映射到各 provider 真实参数格式。

例如：

- OpenRouter -> `reasoning.effort`
- Gateway + Anthropic -> `thinking` / `budgetTokens`
- Gateway + Google -> `thinkingConfig`
- Grok mini -> `reasoningEffort` 或 OpenRouter reasoning

## 19.1 含义

variant 之所以有价值，是因为 transform 层已经把它们翻译成 provider 真正懂的参数。

这不是简单 UI preset，而是跨 provider reasoning abstraction。

---

# 20. 工具 schema 为什么也要经过 `ProviderTransform.schema(...)`

在 `SessionPrompt.resolveTools()` 中：

- 本地工具：`ProviderTransform.schema(input.model, z.toJSONSchema(item.parameters))`
- MCP 工具：`ProviderTransform.schema(input.model, asSchema(item.inputSchema).jsonSchema)`

这说明模型看到的 tool schema 也必须因 provider/model 特性而变形。

## 20.1 为什么这是必要的

不同 provider 对 JSON schema 支持程度不同：

- 某些关键词不支持
- 某些结构需要降级
- 某些字段命名/嵌套有限制

所以 schema 投影是 provider compatibility 的一部分，而不是 tool 自己静态决定的。

---

# 21. prompt 层如何依赖 provider/model 解析

`prompt.ts` 中很多地方都依赖 `Provider.getModel()`：

- normal turn 解析 lastUser model
- subtask 指定 task.model 时解析子模型
- compaction 回放时继承 variant/model
- 附件内部 read tool 也会解析 model

这说明 provider/model 解析不是 LLM 层独占能力，而是整个 session runtime 的公共基础设施。

---

# 22. `ProviderTransform.OUTPUT_TOKEN_MAX`

transform 层还定义：

- `OUTPUT_TOKEN_MAX = Flag.OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX || 32_000`

并被 `LLM.OUTPUT_TOKEN_MAX` 直接引用。

这说明“输出 token 上限”被视为 provider transform 的一部分，因为它最终会参与 maxOutputTokens、compaction reserved 等多个投影决策。

---

# 23. provider 层与 auth/env/config 的关系

`provider.ts` 中可见 provider 解析会大量依赖：

- `Auth`
- `Env`
- `Config`

例如：

- `opencode` 判断是否有 key 决定是否隐藏付费模型
- `azure` 取 resourceName
- `bedrock` 组 credential chain / region / endpoint

这说明 provider/model 解析并不是纯静态配置读取，而是：

- **配置 + 环境 + 认证 + provider 规则**

共同决定的运行时结果。

---

# 24. provider 特例为什么大量出现在 `provider.ts` 而不是 `transform.ts`

可以从职责上理解：

## 24.1 `provider.ts`

负责：

- 这个 provider 怎么连上
- 用哪个 SDK factory
- 用 chat 还是 responses 还是 languageModel
- endpoint/credential/region 怎么给

## 24.2 `transform.ts`

负责：

- 已经得到一个模型后，消息/参数/schema 怎么适配到 provider 规范

这条边界很清晰，也说明代码虽然复杂，但职责并没完全混乱。

---

# 25. 一个完整的 model 请求投影链路

可以把完整过程概括为：

## 25.1 选择模型

- `Provider.getModel(providerID, modelID)`

## 25.2 获取 provider/runtime

- `Provider.getProvider(providerID)`
- `Provider.getLanguage(model)`

## 25.3 组装默认参数

- `ProviderTransform.options(...)`
- `model.options`
- `agent.options`
- `variant`

## 25.4 插件再改写

- `chat.params`
- `chat.headers`

## 25.5 投影消息与 schema

- `ProviderTransform.message(...)`
- `ProviderTransform.schema(...)`
- `ProviderTransform.providerOptions(...)`

## 25.6 发起流式请求

- `streamText(...)`

这就是 OpenCode 的 provider/model 投影总链路。

---

# 26. 这个模块背后的关键设计原则

## 26.1 provider 差异必须被显式编码，而不是指望 AI SDK 全兜底

Claude/Mistral/Bedrock/OpenAI 的差异已经证明这一点。

## 26.2 高层模型语义应与低层 provider 参数解耦

所以有 variant / reasoning effort -> provider-specific options 的映射。

## 26.3 schema、message、providerOptions 都属于投影层的一部分

不能只处理 message，不处理 tool schema。

## 26.4 默认体验需要模型家族经验参数支撑

所以有 temperature/topP/topK 的启发式默认值。

---

# 27. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/provider/provider.ts`
2. `packages/opencode/src/provider/transform.ts`
3. `packages/opencode/src/session/llm.ts`
4. `packages/opencode/src/session/prompt.ts`

重点盯住这些函数/概念：

- `Provider.getModel()`
- `Provider.getProvider()`
- `Provider.getLanguage()`
- `CUSTOM_LOADERS`
- `ProviderTransform.message()`
- `ProviderTransform.schema()`
- `ProviderTransform.variants()`
- `chat.params`
- `chat.headers`

---

# 28. 下一步还需要深挖的问题

这一篇已经把 provider/model 投影主框架讲清楚了，但还有一些值得继续展开的点：

- **问题 1**：`Provider.getModel()` / `getLanguage()` / `getProvider()` 的完整实现细节与缓存策略还值得单独精读
- **问题 2**：`ProviderTransform.schema()`、`providerOptions()`、`smallOptions()`、`maxOutputTokens()` 的完整实现还可继续展开
- **问题 3**：OpenAI/Copilot `responses()` vs `chat()` 路径选择对 tool/reasoning 行为差异还值得继续验证
- **问题 4**：Bedrock 跨区域 inference profile 前缀逻辑的边界还可以继续系统化总结
- **问题 5**：providerOptions key remap 是否覆盖了全部第三方 SDK case，还值得继续检查
- **问题 6**：media unsupported 时转成 text error 提示，是否总是最优 UX，还值得思考
- **问题 7**：variant 命名与 UI 呈现如何与实际 provider 参数映射保持一致，还值得继续追踪
- **问题 8**：未来若新增更多 provider，自定义 loader 与 transform 的职责边界是否仍足够清晰，也值得观察

---

# 29. 小结

`provider_model_resolution_and_option_projection` 模块定义了 OpenCode 如何把高层的“选择某个模型并调用它”翻译成 provider 可执行、可兼容、可扩展的真实请求：

- `Provider` 负责 provider/model 身份解析、SDK 创建、认证与 provider-specific loader 逻辑
- `ProviderTransform` 负责 messages、schema、providerOptions、sampling 参数与缓存 hint 的兼容投影
- `LLM.stream()` 把 model、agent、variant、plugins 合并成最终请求参数
- `prompt.ts` 则在工具 schema 与 session runtime 中广泛复用这套投影能力

因此，这一层不是普通模型配置读取，而是 OpenCode 跨 provider 统一执行体验的关键适配基础设施。
