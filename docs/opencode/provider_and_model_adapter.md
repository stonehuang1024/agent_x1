# Provider / Model Adapter 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的模型接入层。

它回答的问题是：

- OpenCode 如何同时支持 OpenAI、Anthropic、Gemini、Bedrock、Copilot、OpenRouter 等不同 provider
- provider 与 model 元信息如何组织
- provider 配置、认证、环境变量、插件 loader 如何合并
- 模型对象如何被实例化为 AI SDK language model
- 为什么同样的消息、schema、options 在不同 provider 下要做不同变换

核心源码包括：

- `packages/opencode/src/provider/provider.ts`
- `packages/opencode/src/provider/transform.ts`
- `packages/opencode/src/session/llm.ts`

如果说前几篇讲的是 runtime 调度与工具执行，那么这一篇讲的是 **多模型兼容层**。

它的核心职责就是：

- 把一个统一的 agent runtime，稳定映射到大量 provider 差异之上

---

# 2. 模块边界与职责分工

## 2.1 `provider/provider.ts`

这是 provider registry + model loader + SDK loader。

它负责：

- 定义 `Provider.Info` 与 `Provider.Model`
- 维护 provider state
- 从 models.dev / config / env / auth / plugin loader 组装 provider 列表
- 选择默认模型 / small model
- 实例化 SDK client
- 实例化具体 language model
- 处理 provider init 错误与 model not found 错误

## 2.2 `provider/transform.ts`

这是 provider-aware transform 层。

它负责：

- messages 规范化
- providerOptions key remap
- 缓存策略注入
- 不支持模态的降级处理
- temperature / topP / topK 默认策略
- reasoning variants 生成
- provider options 组织
- tool schema 兼容变换

## 2.3 `session/llm.ts`

它是 provider 层的运行时调用入口。

它负责把：

- model
- messages
- system
- tools
- agent options
- provider options

整合后交给 AI SDK。

所以这三层的关系可以理解为：

- `provider.ts`：有什么模型、怎么拿到 SDK
- `transform.ts`：如何把统一 runtime 输入改写成 provider 能接受的形式
- `llm.ts`：把这一切组织成一次真实请求

---

# 3. `Provider.Model` 的数据结构

## 3.1 为什么模型对象这么重

OpenCode 的 `Provider.Model` 不只是一个 model id 字符串，而是完整元信息对象，包含：

- `id`
- `providerID`
- `api.id`
- `api.url`
- `api.npm`
- `name`
- `family`
- `capabilities`
- `cost`
- `limit`
- `status`
- `options`
- `headers`
- `release_date`
- `variants`

这意味着 OpenCode 从一开始就把模型当作**带能力描述与成本边界的资源对象**，而不是随便拼一个 provider/model 字符串。

## 3.2 `capabilities` 的意义

`capabilities` 里包含：

- 是否支持 `temperature`
- 是否支持 `reasoning`
- 是否支持 `attachment`
- 是否支持 `toolcall`
- 各种 input/output modality 支持情况
- `interleaved` 能力

这决定了 runtime 后续很多行为：

- 是否允许文件/图片进入上下文
- 是否启用 reasoning 相关参数
- 是否允许工具调用
- 是否要对 reasoning 做 interleaved 兼容变换

所以 `capabilities` 是 OpenCode provider abstraction 的关键。

## 3.3 `variants` 的意义

variants 用来表达同一个 model 下的不同推理/参数策略，比如：

- `low`
- `medium`
- `high`
- `max`
- `minimal`
- `none`
- `xhigh`

OpenCode 不是把 reasoning effort 硬编码在业务里，而是把它建模成 model variant，这样 loop/agent 可以按需选择。

---

# 4. provider state 是如何构建出来的

## 4.1 总体思路

`Provider.state()` 是整个 provider 层的核心。

它会把多种信息源合并成最终可用 provider map：

- `models.dev` 数据库
- 用户 config
- 环境变量
- `Auth` 存储
- 插件 auth loader
- 内置 `CUSTOM_LOADERS`
- provider allowlist / denylist

这是一个非常典型的 **multi-source provider synthesis pipeline**。

## 4.2 第一层：models.dev 作为基础数据库

系统会先加载：

- `ModelsDev.get()`

再通过：

- `fromModelsDevProvider()`
- `fromModelsDevModel()`

转成内部 `Provider.Info` / `Provider.Model`。

这说明 models.dev 充当的是：

- provider/model 元数据库
- 初始 capability / cost / limit / status 来源

## 4.3 第二层：config 扩展数据库

用户可以在 config 里：

- 覆盖 provider name/env/options
- 增加/覆盖 model 定义
- 覆盖 api id/url/npm
- 覆盖 capability/cost/headers/limit/family/release_date
- 覆盖 variants

这里的核心是 `mergeDeep(...)`。

也就是说 config 并不是替换整份模型定义，而是**增量 patch**。

## 4.4 第三层：env 自动激活 provider

对每个 provider，state 会检查它声明的 `env` 列表：

- 如果环境变量里存在对应 key
- 就认为该 provider 可激活
- 并把 source 记为 `env`

这是一种很自然的 autoload 机制。

## 4.5 第四层：Auth 存储激活 provider

如果 `Auth.all()` 中存在 provider 的 api key：

- source 记为 `api`
- `key` 进入 provider

说明 provider 激活来源不只 env，也可以来自系统存储的认证结果。

## 4.6 第五层：插件 auth loader

如果插件声明了 `auth.loader`，并且当前 provider 已有认证信息，就会调用 loader 返回额外 options。

这样插件可以在 provider 初始化前注入：

- baseURL
- headers
- provider-specific token material
- custom options

这是一种 **auth-driven provider patching**。

## 4.7 第六层：`CUSTOM_LOADERS`

这是 provider 层里最有工程含量的一部分。

系统为很多 provider 写了定制 loader，例如：

- `anthropic`
- `opencode`
- `openai`
- `github-copilot`
- `azure`
- `amazon-bedrock`
- `openrouter`
- `google-vertex`
- `gitlab`
- `cloudflare-workers-ai`
- `cloudflare-ai-gateway`
- 等等

这些 loader 负责：

- 是否 autoload
- provider 级默认 options
- 变量替换函数 `vars()`
- 自定义 `getModel()` 逻辑

这意味着 OpenCode 对 provider 的抽象不是“所有 provider 完全统一”，而是：

- 先统一到一个共性接口
- 对难统一的 provider 再用 loader 做定制

这是很成熟的 abstraction 策略。

---

# 5. `CUSTOM_LOADERS` 的核心设计原理

## 5.1 为什么需要 custom loader

不同 provider 的差异很多，例如：

- 认证方式不同
- model 实例化 API 不同
- reasoning 参数不同
- baseURL 可能是动态模板
- 某些 provider 需要额外 headers
- 某些 provider 根本不是标准 `sdk.languageModel(modelID)`

因此 OpenCode 使用 custom loader 把 provider 特殊性封装起来。

## 5.2 `openai` / `github-copilot` 的 `getModel()` 差异

例如：

- OpenAI 直接走 `sdk.responses(modelID)`
- GitHub Copilot 可能根据 modelID 选择：
  - `sdk.responses(modelID)`
  - `sdk.chat(modelID)`
  - 或 `sdk.languageModel(modelID)`

这说明“拿到一个 language model”本身在不同 provider 下就不是统一 API。

## 5.3 `amazon-bedrock` 的复杂性

Bedrock loader 里有大量 region / credential / endpoint / inference profile 逻辑，例如：

- region 优先级解析
- AWS credential chain
- bearer token 优先级
- endpoint 覆盖
- 根据 region 和 model 类型自动加 `us.` / `eu.` / `jp.` / `apac.` / `au.` 前缀

这本质上是一个 **provider-specific model ID normalization algorithm**。

它解决的问题是：

- 同一个逻辑模型在 Bedrock 上可能需要不同 cross-region prefix
- 这个映射不是业务层应该关心的事情

## 5.4 `google-vertex` 的 fetch 注入

Vertex loader 会注入自定义 `fetch`：

- 使用 `GoogleAuth()`
- 动态拿 access token
- 自动写入 `Authorization: Bearer ...`

这说明 provider 层不只是配置 model，也可以拦截底层 transport。

## 5.5 `cloudflare-ai-gateway` 的 unified 格式

Cloudflare AI Gateway loader 会：

- 使用 `createAiGateway`
- 使用 unified provider
- 要求 modelID 形如 `provider/model`

说明有些 provider 其实是“provider 的 provider”，即网关型 provider，需要再次把模型路由到上游。

OpenCode 通过 custom loader 吸收了这层复杂性。

---

# 6. `getSDK()`：provider SDK 实例化算法

## 6.1 目标

`getSDK(model)` 的目标是：

- 根据 model 的 providerID、npm 包、options
- 返回一个可复用的 provider SDK 实例

## 6.2 baseURL 解析与变量替换

`getSDK()` 会处理：

- `options.baseURL`
- `model.api.url`
- `${VAR_NAME}` 模板替换
- custom loader 的 `vars()` 替换
- 环境变量替换

这意味着 provider URL 可以是模板化的，而不是静态字符串。

这是 **late binding URL resolution**。

## 6.3 headers / apiKey 合并

`getSDK()` 会：

- 如果 `options.apiKey` 没有，且 provider.key 存在，则自动填入
- 将 model.headers 合并进 options.headers

所以 provider-level 与 model-level 信息会在 SDK 实例化前统一归并。

## 6.4 SDK 缓存

系统对：

- `{ providerID, npm, options }`

做 JSON 序列化后哈希，作为缓存 key。

若已有 SDK，则直接复用。

这是一种 **memoized provider client cache**。

优点：

- 避免重复创建 SDK client
- 保持相同配置下的客户端复用

## 6.5 fetch 包装与 chunk timeout

`getSDK()` 会给 provider fetch 注入一层包装：

- 合并 abort signals
- 处理全局 timeout
- 处理 SSE chunk timeout
- 对 OpenAI body 做特殊字段清理

其中 `wrapSSE()` 会对 event-stream 的每次 `reader.read()` 加超时保护。

这是一个很重要的 **stream transport watchdog** 设计。

它避免：

- SSE 长时间卡住却没有显式失败
- runtime 无限制等待 provider 流

## 6.6 bundled provider vs 动态安装 provider

如果 `model.api.npm` 在 `BUNDLED_PROVIDERS` 中：

- 直接用内置 import 的 createXxx 函数

否则：

- 若不是 `file://`，则 `BunProc.install(model.api.npm, "latest")`
- 动态 import 已安装模块
- 找到 `create*` 函数
- 实例化 SDK

这说明 OpenCode 的 provider 体系不仅支持内置 provider，也支持动态 provider 包。

---

# 7. `getLanguage()`：模型实例化流程

## 7.1 为什么要区分 SDK 和 language model

SDK 是 provider 级客户端。

language model 是：

- 在 SDK 上绑定 конкрет modelID 后得到的具体可调用模型对象

所以 `getLanguage()` 会：

1. 先拿 state
2. 再拿 provider
3. 再 `getSDK(model)`
4. 根据 provider 是否有 custom `modelLoader`：
   - 用 custom loader
   - 否则默认 `sdk.languageModel(model.api.id)`
5. 缓存到 `models` map

## 7.2 模型缓存

key 形式为：

- `${providerID}/${model.id}`

这是一种 **language model object cache**。

优点是：

- 同一模型在一个 runtime 中只实例化一次
- 后续流请求可以直接复用

## 7.3 NoSuchModelError -> ModelNotFoundError

如果 SDK 抛出 `NoSuchModelError`，OpenCode 会统一转成：

- `Provider.ModelNotFoundError`

这让上层 runtime 可以只理解内部错误模型，而不用直接依赖底层 SDK 异常类型。

---

# 8. `ProviderTransform.message()`：消息适配算法

这是 provider 兼容层里最关键的函数之一。

## 8.1 总流程

`ProviderTransform.message(msgs, model, options)` 会按顺序做：

1. `unsupportedParts(...)`
2. `normalizeMessages(...)`
3. 对 Anthropic 类模型应用 `applyCaching(...)`
4. remap `providerOptions` key

这不是小修小补，而是完整的 **message compatibility pipeline**。

## 8.2 `unsupportedParts()`：不支持模态的降级

对于 user message 中的：

- `file`
- `image`

函数会根据 mime -> modality 映射判断：

- 当前 model 是否支持该 modality

如果不支持，就替换成错误文本，例如：

- `ERROR: Cannot read ... (this model does not support image input). Inform the user.`

这说明 OpenCode 并不会默默丢掉不支持的附件，而是显式转成可解释文本。

这是一个很重要的 **capability-aware fallback-to-text** 策略。

## 8.3 `normalizeMessages()`：provider 级消息修正

### Anthropic / Bedrock

- 过滤空字符串消息
- 过滤空 text/reasoning part

原因是这类 provider 会拒绝 empty content。

### Claude toolCallId 规范化

- 把 `toolCallId` 中非法字符替换成 `_`

### Mistral toolCallId 规范化

- 只能保留字母数字
- 截断到 9 字符
- 不足补零

并且如果：

- `tool` message 后面跟 `user` message

还会自动插一个 assistant `Done.`，修正消息顺序。

### interleaved reasoning 转移

如果模型的 `capabilities.interleaved` 指定了某个字段，例如：

- `reasoning_content`
- `reasoning_details`

则会把 reasoning part 提取出来，塞进 message 级 providerOptions，而不是保留在 content 数组里。

这说明 reasoning 在不同 provider 下的表达方式并不统一，OpenCode 通过 transform 吸收了这一差异。

## 8.4 `applyCaching()`：消息缓存点策略

对 Anthropic / OpenRouter / Bedrock 等模型，OpenCode 会给：

- 前两个 system
- 最后两个非 system 消息

打上 provider-specific cache 控制字段。

例如：

- `anthropic.cacheControl`
- `bedrock.cachePoint`
- `openaiCompatible.cache_control`

这是一种 **selective prompt caching policy**。

说明 OpenCode 已经把 prompt caching 作为 provider 优化的一部分纳入 runtime。

## 8.5 providerOptions key remap

AI SDK 不同 provider 对 `providerOptions` 的命名空间 key 有不同要求。

例如：

- openai 要 `openai`
- anthropic 要 `anthropic`
- google 要 `google`
- bedrock 要 `bedrock`

而 OpenCode 内部可能用 `providerID` 作为 key。

所以 transform 会把 stored key remap 成 SDK 预期 key。

这是 **namespace remapping**，否则同样的 option 根本传不到 provider。

---

# 9. sampling 参数默认策略

## 9.1 `temperature()`

OpenCode 并没有给所有模型统一 temperature。

而是按 model id 做 heuristics，例如：

- `qwen` -> `0.55`
- `claude` -> `undefined`
- `gemini` -> `1.0`
- `kimi-k2` 某些变体 -> `1.0` 或 `0.6`

这说明 OpenCode 假设不同模型族对 temperature 敏感性不同。

## 9.2 `topP()` / `topK()`

同样也按 model id 做 provider/model 特定 heuristics。

这是一种经验型 **family-specific sampling preset**。

目标不是数学最优，而是工程上让不同模型开箱表现更稳定。

---

# 10. reasoning `variants()` 生成算法

这是 provider transform 里最复杂、最有价值的一部分之一。

## 10.1 目标

给一个 model，自动推导出它支持哪些 reasoning effort / thinking 模式。

## 10.2 为什么不能统一成一个字段

因为不同 provider 的 reasoning API 风格差异极大，例如：

- OpenAI：`reasoningEffort`
- Anthropic：`thinking.type/budgetTokens`
- Bedrock：`reasoningConfig`
- Google：`thinkingConfig`
- OpenRouter：`reasoning.effort`
- Copilot：`reasoningEffort + include`

所以 OpenCode 不能只说“这个模型支持高/中/低推理”，还必须知道对应 provider 该怎么表示。

## 10.3 生成方式

`variants(model)` 会基于：

- `model.capabilities.reasoning`
- `model.api.npm`
- `model.id`
- `model.api.id`
- `release_date`

决定返回哪组 variant map。

例如：

- OpenAI gpt-5 可能支持 `minimal/low/medium/high/xhigh`
- Anthropic 可能支持 `high/max` 或 adaptive 模式
- Google Gemini 2.5 用 `thinkingBudget`
- Google Gemini 3.x 用 `thinkingLevel`
- Groq 可能支持 `none/low/medium/high`

这实际上是一个 **provider-specific reasoning capability compiler**。

## 10.4 配置层如何再覆盖 variants

在 `Provider.state()` 后期，系统还会把：

- `ProviderTransform.variants(model)`
- config 中的 `model.variants`

合并，再过滤掉 `disabled` variants。

所以 variants 是：

- 系统自动推导
- 用户配置可再 patch

---

# 11. `ProviderTransform.options()`：基础 provider options 生成

## 11.1 作用

这个函数根据 model/provider 特征生成基础 options，比如：

- `store: false`
- `promptCacheKey`
- `reasoningEffort`
- `reasoningSummary`
- `thinkingConfig`
- `enable_thinking`
- `chat_template_args`
- `gateway.caching`

## 11.2 为什么要在这里做

因为这些参数不是 agent 业务逻辑关心的，而是 provider 协议层要求。

如果散落在 loop/agent 层，代码会非常混乱。

所以 OpenCode 选择把它们集中在 transform 层。

## 11.3 例子

### OpenAI / Copilot

默认：

- `store = false`

### OpenRouter

- `usage.include = true`
- 某些 Gemini 3 还默认高 reasoning

### Google / Vertex

- 默认 `thinkingConfig.includeThoughts = true`

### Alibaba-CN reasoning models

- `enable_thinking = true`

### GPT-5 非 chat 系列

- 默认 `reasoningEffort = medium`
- `reasoningSummary = auto`
- 某些情况下还会加 `textVerbosity = low`

说明 OpenCode 不仅统一 provider，还主动替你补 provider 的最佳实践默认值。

---

# 12. `smallOptions()`：小模型/轻量调用策略

`smallOptions(model)` 主要在需要更轻量推理时使用。

例如：

- OpenAI gpt-5 -> `reasoningEffort: low/minimal`
- Google Gemini -> `thinkingBudget: 0` 或 `thinkingLevel: minimal`
- OpenRouter -> 禁用或最小 reasoning
- Venice -> `disableThinking: true`

这说明 OpenCode 有意识地区分：

- 标准推理调用
- 小成本、低思考量调用

这对标题生成、轻量总结、小任务很有价值。

---

# 13. `providerOptions()`：如何把 options 路由到正确命名空间

## 13.1 普通 provider

如果不是 gateway：

- key = `sdkKey(model.api.npm)` 或 `model.providerID`
- 返回 `{ [key]: options }`

## 13.2 gateway 的特殊处理

对于 `@ai-sdk/gateway`，逻辑不同：

- `gateway` namespace 保留 gateway 原生路由控制
- 其他 options 按 modelID 前缀路由到上游 provider slug
- 例如 `amazon/*` 会映射到 `bedrock`

这意味着 gateway provider 其实是“二级 provider 命名空间映射”问题。

OpenCode 为此写了专门的 routing 逻辑。

---

# 14. `schema()`：工具 schema 的 provider 兼容变换

## 14.1 为什么 schema 也要 transform

工具参数 schema 虽然是 JSON Schema，但不同 provider 对 JSON Schema 支持程度并不一样。

特别是 Gemini / Google 系列，对一些 schema 形式更挑剔。

## 14.2 Gemini 兼容修正

`schema()` 中对 Gemini 类模型会做这些处理：

- integer enum -> string enum
- 如果 enum 在 number/integer 类型上，则强制改成 `string`
- object 的 `required` 只保留实际存在于 `properties` 中的字段
- array 若 `items` 缺失，则补空 items
- 对 schema-empty items 补 `type: string`
- 非 object 类型删除 `properties` / `required`

这是一个完整的 **schema sanitization algorithm for Gemini**。

如果不做这层变换，很多工具 schema 会在 provider 侧直接被拒绝。

---

# 15. `LLM.stream()` 如何消费 provider 层

## 15.1 模型和 provider 的获取顺序

`LLM.stream()` 中会并行获取：

- `Provider.getLanguage(input.model)`
- `Config.get()`
- `Provider.getProvider(input.model.providerID)`
- `Auth.get(input.model.providerID)`

也就是说，真正发请求前，runtime 会拿到：

- language model object
- provider info
- 当前配置
- 当前 auth

## 15.2 options merge 层次

`LLM.stream()` 会把几层 options 合并：

1. `ProviderTransform.options(...)` 或 `smallOptions(...)`
2. `input.model.options`
3. `input.agent.options`
4. `variant`

这是一个 **hierarchical option merge**。

优先级上，越靠后的越像任务/agent 层定制。

## 15.3 最终 providerOptions 生成

合并后的 `options` 再交给：

- `ProviderTransform.providerOptions(input.model, params.options)`

变成 AI SDK 所需的 providerOptions 命名空间结构。

## 15.4 message transform 在真正请求前执行

`wrapLanguageModel(...middleware)` 中会调用：

- `ProviderTransform.message(args.params.prompt, input.model, options)`

这意味着：

- prompt/system/messages 先在 runtime 内统一构造
- 最后一步再按 provider 做协议重写

这是一种非常干净的架构边界。

---

# 16. 模型选择辅助逻辑

## 16.1 `getSmallModel()`

这个函数会：

- 先看 config 是否指定 `small_model`
- 否则按 provider 的优先级列表找轻量模型

例如优先找：

- `claude-haiku`
- `gemini-flash`
- `gpt-5-nano`

对 Bedrock 还会处理 cross-region prefix 优先级：

- `global.`
- 用户 region 前缀
- 无前缀

这说明 OpenCode 不只会“找默认模型”，还会主动找轻量便宜的小模型。

## 16.2 `defaultModel()`

默认模型选择顺序：

1. config 中显式指定的 model
2. 最近使用模型记录
3. 第一个可用 provider 中按 `sort()` 排序后的最佳模型

这是一种结合：

- 用户偏好
- 历史使用
- 系统优先级

的默认策略。

## 16.3 `sort()`

排序偏好包括：

- 是否匹配优先模型关键词
- 是否是 `latest`
- model id 本身

说明默认模型选择也不是随机，而是带明确产品偏好的。

---

# 17. 错误模型

provider 层定义了两个核心错误：

- `ModelNotFoundError`
- `InitError`

并且：

- `getModel()` 会在找不到 provider 或 model 时用 fuzzysort 给 suggestions
- `getSDK()` 初始化失败会统一抛 `InitError`

这意味着 provider 层已经主动承担了“可诊断性”的责任，而不是把晦涩底层异常直接抛给上层。

---

# 18. 这个模块背后的核心设计原则

## 18.1 统一抽象，但不掩盖差异

OpenCode 不是假装所有 provider 完全一样。

相反，它承认：

- model loader 不一样
- schema 要求不一样
- reasoning 参数不一样
- message 协议不一样
- 缓存策略不一样
- transport 特性也不一样

然后通过：

- `Provider`
- `ProviderTransform`
- `LLM.stream()`

把这些差异系统性吸收掉。

## 18.2 元信息驱动

大量行为依赖 model 元信息：

- capabilities
- cost
- limit
- family
- status
- release_date
- variants

这使得 runtime 行为不是大量硬编码分支，而是部分由模型元数据驱动。

## 18.3 兼容层前置

OpenCode 尽量在 provider transform 层就把问题处理掉，而不是把兼容分支散落到 tool/runtime/UI 层。

## 18.4 provider 选择与 transport 也是 runtime 责任

认证、fetch 包装、超时、SSE 监控、动态 provider 安装，这些都不是外围脚手架，而是 provider runtime 的一部分。

---

# 19. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/provider/provider.ts`
2. `packages/opencode/src/provider/transform.ts`
3. `packages/opencode/src/session/llm.ts`

重点盯住这些函数/概念：

- `Provider.state()`
- `CUSTOM_LOADERS`
- `getSDK()`
- `getLanguage()`
- `getModel()`
- `getSmallModel()`
- `defaultModel()`
- `ProviderTransform.message()`
- `normalizeMessages()`
- `unsupportedParts()`
- `applyCaching()`
- `variants()`
- `options()`
- `providerOptions()`
- `schema()`

---

# 20. 下一步还需要深挖的问题

这个模块已经把 provider 主框架说明白了，但还有一些地方值得继续独立深挖：

- **问题 1**：`ModelsDev.get()` 的数据来源、更新机制、缓存策略是什么
- **问题 2**：provider/plugin auth loader 的完整接口契约与生命周期是什么
- **问题 3**：`Session.getUsage()` 如何把 provider usage metadata 精确映射成统一成本模型
- **问题 4**：不同 provider 的 reasoning metadata 字段差异，是否都能被统一保真
- **问题 5**：`wrapSSE()` 在不同网络错误和半关闭连接情况下的行为是否完全可靠
- **问题 6**：动态安装第三方 provider npm 包的安全边界、缓存与版本控制策略是什么
- **问题 7**：gateway provider 的路由/caching/byok 控制在更复杂场景下如何组合
- **问题 8**：不同 provider 对 tool streaming 的支持差异，在 `streamText()` 返回事件层面还有哪些隐性兼容分支

---

# 21. 小结

`provider_and_model_adapter` 模块定义了 OpenCode 如何把单一 agent runtime 扩展到多 provider 世界：

- `Provider` 负责 provider/model 目录、认证与实例化
- `ProviderTransform` 负责消息、schema、options、reasoning、缓存等协议适配
- `LLM.stream()` 负责把统一 runtime 输入组装成一次真实 provider 调用

因此，这个模块不是“模型配置文件”，而是 OpenCode 多模型兼容能力的核心基础设施。
