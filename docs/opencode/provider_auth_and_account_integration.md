# Provider Auth / Account Integration 模块详细解读

---

# 1. 模块定位

这一篇专门拆 OpenCode 的 provider、auth、account 集成链路。

核心问题是：

- provider 信息从哪里来
- auth 信息有哪些形态
- account/org 配置如何并入 provider 与 config 层
- provider options、环境变量、auth 存储、models.dev 数据是如何合成的
- 为什么同一个 provider 既可能走 API key，也可能走 OAuth，也可能走组织配置

核心源码包括：

- `packages/opencode/src/provider/provider.ts`
- `packages/opencode/src/auth/index.ts`
- `packages/opencode/src/account/index.ts`
- `packages/opencode/src/session/llm.ts`
- `packages/opencode/src/config/config.ts`

这一层本质上是 OpenCode 的**模型供应商接入与凭据来源合成系统**。

---

# 2. 为什么 provider/auth 不是一个简单 API key 读取器

OpenCode 支持的模型来源很多：

- OpenAI
- Anthropic
- Google / Vertex
- Azure
- Bedrock
- OpenRouter
- GitHub Copilot
- GitLab
- xAI
- Mistral
- Groq
- DeepInfra
- Cohere
- TogetherAI
- Perplexity
- Vercel
- OpenCode 自家 provider
- 以及自定义 provider

这些 provider 的认证方式也并不统一：

- API key
- OAuth access/refresh token
- well-known token
- cloud credential chain
- 环境变量
- 项目配置
- account/org remote config

所以 provider/auth 体系天然必须是：

- **多来源、多协议、多层覆盖的合成系统**

---

# 3. `Auth.Info`：认证信息的统一抽象

`Auth` 层定义了三类认证信息：

- `oauth`
- `api`
- `wellknown`

## 3.1 `oauth`

字段包括：

- `refresh`
- `access`
- `expires`
- `accountId?`
- `enterpriseUrl?`

适合：

- GitHub Copilot
- 需要 token 刷新的 provider
- 企业 OAuth endpoint 场景

## 3.2 `api`

字段只有：

- `key`

适合最常见的 API key provider。

## 3.3 `wellknown`

字段包括：

- `key`
- `token`

这和 `.well-known/opencode` 远程配置体系直接相关。

## 3.4 为什么统一成 discriminated union

这样上层模块就能：

- 用统一接口拿认证信息
- 再按 provider 具体需要解释

而不必直接知道底层存储细节。

---

# 4. `Auth` 的角色：凭据存储门面

`Auth` 导出的能力非常克制：

- `get(providerID)`
- `all()`
- `set(key, info)`
- `remove(key)`

背后实际由 `AuthService` 提供实现，但对上层来说它就是一个：

- **provider credential registry facade**

这层的价值在于把 provider 逻辑与凭据存储介质解耦。

---

# 5. `Account` 的角色：组织与控制台上下文入口

`Account` 暴露的主要接口是：

- `active()`
- `config(accountID, orgID)`
- `token(accountID)`

这说明 `Account` 不是 provider credential store，而是：

- 当前登录身份
- 当前激活组织
- 组织配置
- 控制台访问 token

的来源。

## 5.1 `active()`

给出当前活跃账号上下文。

## 5.2 `config(accountID, orgID)`

允许取组织级配置，并在 `Config.state()` 中合并进总配置。

## 5.3 `token(accountID)`

允许拿到 account token，再注入：

- `OPENCODE_CONSOLE_TOKEN`

这使控制台/组织级功能可在后续 provider/config 路径中使用。

---

# 6. Provider 层的真实职责

`provider.ts` 的职责远不只是“给你返回一个 SDK provider 对象”。

它同时处理：

- provider 清单
- model 元数据
- 自定义 loader
- provider options 合并
- auth / env / config 的优先级
- 部分 provider 的特殊认证逻辑
- model capability、cost、limits 元数据
- provider-specific SDK 实例选择

这说明 Provider 层是：

- **供应商模型抽象 + 认证适配 + SDK 构造器**

---

# 7. `BUNDLED_PROVIDERS`：SDK 工厂注册表

文件里先定义了一张映射：

- npm package id -> `create*` provider factory

例如：

- `@ai-sdk/openai`
- `@ai-sdk/anthropic`
- `@ai-sdk/google`
- `@ai-sdk/openai-compatible`
- `@openrouter/ai-sdk-provider`
- `@gitlab/gitlab-ai-provider`
- 等

这说明 OpenCode 对绝大多数主流 provider 采用：

- **统一 AI SDK provider factory 封装**

而不是为每个 provider 自己发 HTTP。

---

# 8. `CUSTOM_LOADERS`：provider 特化逻辑入口

真正体现工程复杂度的是 `CUSTOM_LOADERS`。

它允许某些 provider 自定义：

- `autoload`
- `getModel()`
- `vars()`
- `options`

这说明虽然底层有统一 SDK 工厂，但不同 provider 的细节仍必须通过专门 loader 处理。

---

# 9. OpenCode provider 的特殊逻辑

`CUSTOM_LOADERS.opencode()` 很值得注意。

## 9.1 判断是否有 key

它会依次检查：

- 环境变量里是否有相关 key
- `Auth.get("opencode")`
- `config.provider.opencode.options.apiKey`

## 9.2 没 key 时做什么

如果没有 key：

- 删除所有非免费模型
- 只保留 cost.input 为 0 的模型
- 同时传 `apiKey: "public"`

这说明 OpenCode provider 支持一种：

- 未登录/无私钥时的 public/free model 视图

这是一个非常产品化的 provider 策略，而不是简单报错。

---

# 10. OpenAI / Copilot 的模型接口选择

`openai` 与 `github-copilot*` loader 里都能看到：

- `responses()`
- `chat()`
- `languageModel()`

之间的选择逻辑。

尤其 Copilot 还会根据模型名判断：

- 是否应走 Responses API

这说明 Provider 层还负责：

- **同一 provider 内不同 model API surface 的选择**

这不是 auth 问题，但与 provider 适配强相关。

---

# 11. Azure 的配置来源合成

`azure` loader 明确体现了来源合成逻辑。

## 11.1 resource name 来源

优先级大致是：

- `provider.options.resourceName`
- `AZURE_RESOURCE_NAME`

## 11.2 getModel 行为

还会根据：

- `useCompletionUrls`

选择走 `chat()` 还是 `responses()`。

## 11.3 `vars()`

loader 还可以导出补充环境变量：

- `AZURE_RESOURCE_NAME`

说明 Provider 层不只是读取 env，也能反向生成 provider 执行环境所需变量。

---

# 12. Bedrock：最典型的多认证来源 provider

`amazon-bedrock` loader 是一个很好的例子。

它会综合：

- `config.provider["amazon-bedrock"].options`
- `Auth.get("amazon-bedrock")`
- `AWS_REGION`
- `AWS_PROFILE`
- `AWS_ACCESS_KEY_ID`
- `AWS_BEARER_TOKEN_BEDROCK`
- `AWS_WEB_IDENTITY_TOKEN_FILE`
- container credentials

## 12.1 region 优先级

源码明确写了：

- config region
- env region
- default `us-east-1`

## 12.2 profile 优先级

- config profile
- env profile

## 12.3 bearer token vs credential chain

如果存在 bearer token：

- bearer token 优先
- 不再走 `fromNodeProviderChain(...)`

否则：

- 使用 AWS credential provider chain

这说明 Provider 层不是只“塞 key 进 SDK”，而是理解各云厂商认证体系，并作出可靠选择。

---

# 13. `Config.state()` 与 account/auth 的结合

在 config 加载流程里，和 account/auth 强相关的两条链路是：

## 13.1 `Auth.all()` 驱动 `.well-known/opencode`

如果 auth 条目类型为 `wellknown`：

- 使用其 token
- 请求 `<url>/.well-known/opencode`
- 合并远程 config

这说明 auth 不是只为模型调用服务，也为配置发现服务。

## 13.2 `Account.active()` 驱动 org config

若存在 active org：

- `Account.config(...)`
- `Account.token(...)`
- 把 token 注入 `OPENCODE_CONSOLE_TOKEN`
- 合并组织配置

这说明 account 系统是高优先级配置来源之一，并能影响 provider 行为。

---

# 14. `session/llm.ts` 如何消费 auth/provider

在真正发起模型调用前，`LLM.stream()` 会并行读取：

- `Provider.getLanguage(input.model)`
- `Config.get()`
- `Provider.getProvider(input.model.providerID)`
- `Auth.get(input.model.providerID)`

## 14.1 auth 影响运行时分支

例如：

- 若 provider 是 `openai`
- 且 auth 类型是 `oauth`
- 则视为 Codex 风格场景

后续会影响：

- `maxOutputTokens` 处理

这说明 auth 类型不只决定能否调用，还会影响 runtime 参数策略。

---

# 15. provider 信息的多来源本质

虽然这次没有把 `provider.ts` 全文件重新读完，但结合此前已知内容，可以确认 provider 信息至少来自：

- `ModelsDev` 静态/远程模型清单
- 用户 config provider 定义
- 环境变量
- `Auth` 存储
- `Account` / org config
- plugin 可能的参数/header 注入
- custom loader provider 特化逻辑

因此 Provider 最核心的价值不是某个单点函数，而是：

- **把多种来源融合成统一的 provider/model 视图**

---

# 16. 为什么 auth/account/config/provider 要分层

可以把四者职责简单区分为：

## 16.1 `Auth`

管：

- provider 凭据条目

## 16.2 `Account`

管：

- 用户身份
- active org
- org config
- console token

## 16.3 `Config`

管：

- 多来源配置合并
- provider config 字段组织

## 16.4 `Provider`

管：

- 把上面这些信息编译成真正可用的 provider SDK 与 model 能力

这种分层非常合理，否则 provider.ts 会变成一个巨大无比的杂糅模块。

---

# 17. 这个模块背后的关键设计原则

## 17.1 认证信息应统一抽象，但允许多形态

API key、OAuth、well-known token 都收敛到 `Auth.Info`。

## 17.2 provider 适配必须理解各供应商生态差异

Azure、Bedrock、Copilot、OpenAI 都有不同的模型与认证语义。

## 17.3 account/org 配置应成为 provider 行为的一等来源

OpenCode 明显支持团队/组织级运营与控制台能力，因此 account 不只是 UI 登录状态。

## 17.4 不同来源要有清晰优先级

config、env、auth、remote config、account config 都不能混乱覆盖。

---

# 18. 推荐阅读顺序

建议按这个顺序继续深挖：

1. `packages/opencode/src/auth/index.ts`
2. `packages/opencode/src/account/index.ts`
3. `packages/opencode/src/config/config.ts`
4. `packages/opencode/src/provider/provider.ts`
5. `packages/opencode/src/session/llm.ts`

重点盯住这些函数/概念：

- `Auth.get()`
- `Auth.all()`
- `Account.active()`
- `Account.config()`
- `Account.token()`
- `CUSTOM_LOADERS`
- `BUNDLED_PROVIDERS`
- `shouldUseCopilotResponsesApi()`
- `fromNodeProviderChain()`
- `Provider.getProvider()`
- `Provider.getLanguage()`

---

# 19. 下一步还需要深挖的问题

这一篇已经把 auth/account/provider 主链路讲清楚了，但还有一些值得继续拆的点：

- **问题 1**：`provider.ts` 后半段关于 provider/model 列表生成、搜索、排序、能力合并的完整算法还可继续系统精读
- **问题 2**：`AuthService` 与 `AccountService` 的底层存储方式、刷新机制和安全边界还值得单独分析
- **问题 3**：各 provider 的环境变量命名、`vars()` 生成与实际 SDK 调用之间的映射还可继续梳理
- **问题 4**：plugin 对 `chat.headers` 和 `chat.params` 的改写，与 provider/auth 原生配置的冲突优先级还值得继续确认
- **问题 5**：OpenCode provider 的 free/public 模型策略是否还涉及服务端限流或账号状态判断，还需要继续查看更多代码
- **问题 6**：GitHub Copilot、GitLab 等 OAuth 型 provider 的 token 刷新与过期处理链路还需进一步追踪
- **问题 7**：organization config 与 managed config 同时存在时，对 provider 配置的最终覆盖规则还可继续细化
- **问题 8**：`ModelsDev` 数据更新策略、缓存与离线行为也值得单独拆专题

---

# 20. 小结

`provider_auth_and_account_integration` 模块定义了 OpenCode 如何把供应商模型、凭据、环境变量、组织配置和运行时策略整合成可调用的模型资源：

- `Auth` 统一管理 provider 凭据
- `Account` 提供活动账号、组织配置和控制台 token
- `Config` 负责把远程/本地/组织配置并入总配置
- `Provider` 则把这些来源编译成真正的 provider SDK、model 入口与调用选项

因此，这一层不是简单的 API key 注入逻辑，而是 OpenCode 多供应商模型接入能力的控制与适配中枢。
