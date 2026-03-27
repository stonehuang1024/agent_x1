# Provider Transport / Gateway Compatibility Edges

---

# 1. Module Purpose

This document explains the transport-edge and gateway-compatibility behavior around OpenCode’s provider integration layer.

The key questions are:

- How does OpenCode defend itself against gateway and proxy quirks around provider calls?
- Why do HTML error pages, no-body failures, and vendor-specific transport oddities matter so much?
- How do provider creation, auth style, base URLs, and SSE wrapping influence runtime reliability?
- Where does OpenCode compensate for incompatibilities introduced by hosted gateways, reverse proxies, and provider adapters?

Primary source files:

- `packages/opencode/src/provider/provider.ts`
- `packages/opencode/src/provider/error.ts`
- `packages/opencode/src/session/llm.ts`
- `packages/opencode/src/session/message-v2.ts`

This layer is OpenCode’s **provider-transport hardening and gateway-compatibility adaptation surface**.

---

# 2. Why transport edges deserve their own analysis

Model runtime correctness is not determined only by prompt logic.

A large amount of production pain comes from transport-edge behavior such as:

- proxy-injected HTML error pages
- gateways returning empty bodies
- provider-specific SSE quirks
- OAuth-backed providers behaving differently from API-key providers
- custom base URLs and compatible-provider shims
- streaming infrastructure that stalls without closing

OpenCode clearly recognizes this.

The provider layer is full of targeted adaptations for these realities.

---

# 3. The provider layer is not just SDK lookup

At a glance, `provider.ts` looks like a registry of bundled provider constructors.

But in practice it is doing much more:

- SDK selection
- model-loader selection
- provider-specific option shaping
- auth-aware fallback behavior
- environment-variable projection
- base URL construction
- streaming transport wrapping
- compatibility policy for provider families and gateways

So the provider layer is part transport adapter, part compatibility policy engine.

---

# 4. Bundled providers versus compatibility shims

`BUNDLED_PROVIDERS` contains a wide set of constructors, including:

- native provider SDKs
- `openai-compatible`
- gateway providers
- GitHub Copilot-specific compatibility layers

This is important because some integrations are not first-party direct providers in the strictest sense.

Some are:

- hosted gateways
- compatibility facades
- protocol-emulation layers

OpenCode deliberately treats those as first-class provider surfaces.

---

# 5. Why OpenAI-compatible providers matter so much

Many modern model deployments expose OpenAI-like APIs without actually being OpenAI.

That means the runtime must handle:

- nominally compatible request formats
- subtly different error semantics
- gateway-generated status codes and bodies
- provider-specific retry or auth quirks behind the same API shape

This is one reason provider normalization and transport hardening matter so much.

---

# 6. `wrapSSE(...)`: transport hardening for event streams

One of the most important transport helpers in `provider.ts` is:

- `wrapSSE(res, ms, ctl)`

It only activates when:

- timeout is positive
- response has a body
- content-type includes `text/event-stream`

Then it wraps the stream reader and enforces a timeout per read chunk.

This is a serious reliability feature.

---

# 7. Why SSE chunk timeout exists

A provider connection may not fully fail, yet still stall while streaming.

That is one of the hardest runtime failure modes to handle because:

- the HTTP connection remains open
- the stream is incomplete
- downstream logic may look permanently busy
- user cancellation may appear ineffective

`wrapSSE(...)` addresses that by timing out stalled reads and aborting the controller.

This is exactly the kind of infrastructure hardening long-lived streaming runtimes need.

---

# 8. Why timeout is enforced at read granularity, not request granularity

A request-level timeout would be too blunt.

Long streams are normal for agent execution.

The real risk is not total duration but:

- lack of progress

By timing each read chunk instead, OpenCode distinguishes between:

- a long but healthy stream
- a hanging stream that has stopped delivering data

That is the correct operational model.

---

# 9. SSE wrapping also propagates cancellation correctly

When a read times out, `wrapSSE(...)` does:

- `ctl.abort(err)`
- `reader.cancel(err)`

And its `cancel(reason)` path also aborts the controller and cancels the reader.

This matters because transport cleanup must be bidirectional:

- runtime abort should terminate the stream
- transport stall should surface as runtime abortable failure

That symmetry is a good design.

---

# 10. Gateway-generated HTML errors are treated as a real class of failure

In `provider/error.ts`, `message(...)` explicitly checks whether `responseBody` looks like HTML.

That means OpenCode expects model requests to pass through components that may inject:

- HTML login pages
- access-denied pages
- generic proxy error pages
- CDN/gateway error documents

This is not theoretical.

The code is clearly hardened for it.

---

# 11. Why HTML-body detection is essential

Without this, users would see useless output like raw markup blobs instead of actionable error text.

Worse, some downstream classification logic would treat those responses as opaque noise.

OpenCode instead turns them into cleaner explanations such as:

- unauthorized due to gateway/proxy blocking
- forbidden by gateway or account settings

This is exactly what a transport-edge compatibility layer should do.

---

# 12. No-body provider failures are another transport edge

`provider/error.ts` also special-cases messages like:

- `400 (no body)`
- `413 (no body)`

This reflects a different failure class:

- the provider or gateway preserves status code
- but loses the semantic body completely

That often happens with intermediate infrastructure, custom gateways, or imperfect SDK wrappers.

OpenCode compensates by interpreting these status-shell failures semantically anyway.

---

# 13. Why no-body errors are dangerous

If a provider returns a bare status without payload:

- retry logic may lose context
- overflow may not be recognized
- users get poor explanations
- the runtime may choose the wrong remediation path

The no-body handling in `isOverflow(...)` is therefore not cosmetic.

It protects the compaction and control-flow behavior of the whole system.

---

# 14. Proxy and gateway auth failures distort normal provider semantics

The HTML handling for `401` and `403` makes this especially clear.

OpenCode recognizes that the failure may not really come from the model provider itself.

It may come from:

- a reverse proxy
- a hosted gateway
- an auth layer
- an expired session or token bridge

That is why the resulting messages are framed in terms of:

- blocked by a gateway or proxy
- re-authentication or permission checks

This is transport-aware diagnosis, not just status-code reporting.

---

# 15. Provider creation is also transport policy

`provider.ts` does not just instantiate SDKs generically.

It has provider-specific `CUSTOM_LOADERS` for cases like:

- `anthropic`
- `opencode`
- `openai`
- GitHub Copilot variants
- Azure variants
- Amazon Bedrock

These loaders affect:

- how models are selected
- which API style is used
- which headers are sent
- which environment variables are projected
- what base URLs are constructed

So transport compatibility starts at provider construction time, not only in error handling.

---

# 16. Anthropic custom header injection is a compatibility edge

For Anthropic, the custom loader injects `anthropic-beta` headers with several feature flags.

This tells us OpenCode is deliberately opting into transport-level capabilities such as:

- fine-grained tool streaming
- interleaved thinking variants
- Claude-code-specific behavior

That is an example of transport adaptation via headers rather than prompt content.

---

# 17. OpenAI and Copilot loader differences matter

For `openai`, the custom loader prefers:

- `sdk.responses(modelID)`

For GitHub Copilot variants, model selection may switch between:

- `responses(...)`
- `chat(...)`
- `languageModel(...)`

based on model ID and SDK capability.

This is another compatibility edge:

- not every “OpenAI-like” deployment should be driven through the same API style

OpenCode explicitly encodes those choices.

---

# 18. Why API-style switching matters for transport reliability

Different endpoints may behave differently for:

- tool calling
- streaming
- reasoning metadata
- output limits
- error envelopes

Choosing the wrong API surface can create subtle breakage even if the provider nominally supports the model.

The loader logic is therefore a transport-compatibility mechanism, not just a convenience abstraction.

---

# 19. Azure and base URL construction

The Azure-related loaders show another major compatibility concern:

- endpoint shape is deployment-specific
- some modes want completion URLs
- some want responses URLs
- cognitive-services variants build explicit `baseURL`

This is a good reminder that “provider transport” often includes URL topology, not just headers and bodies.

---

# 20. Environment projection is part of transport correctness

Some custom loaders expose `vars(...)` to project provider-specific environment variables.

For example, Azure loaders project resource names.

This means OpenCode treats runtime environment shaping as part of provider transport setup.

That is correct because many SDKs still depend on environment-driven defaults or credentials.

---

# 21. Amazon Bedrock highlights auth and region transport edges

The Bedrock loader handles several transport-relevant details:

- region precedence
- profile precedence
- bearer token handling
- environment-backed credentials

Bedrock is a strong example of a provider where transport correctness depends heavily on cloud credential plumbing, not just API request structure.

OpenCode encodes that explicitly.

---

# 22. `opencode` provider behavior shows hosted-gateway awareness

The `opencode` custom loader checks whether a real key is available.

If not, it prunes paid models and may inject:

- `apiKey: "public"`

This is a different kind of transport compatibility edge.

It is about ensuring the provider surface remains coherent even when the auth layer is partially constrained.

The transport contract is being shaped by business/auth reality.

---

# 23. `message(...)` prefers meaningful top-level SDK messages unless the body is more informative

The logic:

- do not always dump response bodies
- only combine when useful
- parse JSON if it adds clarity

is important for gateway compatibility too.

Gateways often preserve one meaningful summary line in the top-level error while stuffing noisy detail in the body.

The function tries to avoid making those errors worse.

---

# 24. Why transport-edge handling must preserve `responseHeaders`

Even when the message is cleaned up, `parseAPICallError()` preserves:

- `responseHeaders`

This matters because gateways and providers may communicate retry guidance through headers like:

- `retry-after`
- `retry-after-ms`

So transport compatibility is not just about rendering a message.

It is also about retaining machine-readable control data.

---

# 25. The provider layer has to assume hostile intermediaries

Taken together, these behaviors imply a practical design assumption:

- requests may pass through imperfect or intrusive intermediaries

Those intermediaries may:

- rewrite errors
- remove bodies
- inject HTML
- delay SSE reads
- distort status semantics
- require alternate auth flows

OpenCode is clearly designed with that reality in mind.

---

# 26. Transport-edge behavior influences runtime policy far downstream

These provider-layer choices affect:

- whether a failure becomes `ContextOverflowError`
- whether retry is attempted
- how long retry waits
- whether the user sees gateway-auth guidance
- whether compaction triggers instead of retry
- whether a stream stalls forever or fails cleanly

So these are not low-level details with low impact.

They materially shape session runtime behavior.

---

# 27. A representative failure path

A useful example is:

## 27.1 Request goes through a gateway

- gateway returns HTML `401`

## 27.2 Provider error parsing runs

- HTML body is detected
- message becomes a readable gateway-auth explanation
- error remains an API-style failure, not a markup blob

## 27.3 Runtime error mapping runs

- becomes structured `APIError`

## 27.4 Retry logic evaluates it

- likely non-retryable or user-actionable
- user sees meaningful diagnosis

This is a textbook example of transport-edge normalization doing real work.

---

# 28. Another representative path: hanging SSE

## 28.1 Provider opens an event stream

- content-type is SSE

## 28.2 Stream stops producing chunks

- connection remains open

## 28.3 `wrapSSE(...)` times out the stalled read

- aborts controller
- cancels reader
- surfaces failure upstream

## 28.4 Runtime can now retry, stop, or report error

Without SSE wrapping, this session might just appear permanently busy.

This is exactly why transport-level safeguards matter.

---

# 29. Key design principles behind this module

## 29.1 Provider integration must assume unreliable intermediaries

So HTML pages, empty bodies, and odd status wrappers are normalized intentionally.

## 29.2 Streaming correctness requires progress detection, not just open-socket detection

So SSE reads are wrapped with chunk-level timeout logic.

## 29.3 Provider construction is part of transport compatibility

So custom loaders shape headers, URLs, auth, environment, and API style per provider family.

## 29.4 Clean user-facing errors and preserved machine metadata are both necessary

So messages are cleaned up while headers, bodies, and metadata remain available for downstream policy.

---

# 30. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/provider/provider.ts`
2. `packages/opencode/src/provider/error.ts`
3. `packages/opencode/src/session/message-v2.ts`
4. `packages/opencode/src/session/retry.ts`
5. `packages/opencode/src/session/llm.ts`

Focus on these functions and concepts:

- `wrapSSE()`
- `CUSTOM_LOADERS`
- `isOpenAiErrorRetryable()`
- HTML/no-body handling in `message()`
- `parseAPICallError()`
- `parseStreamError()`
- `responseHeaders`
- base URL and API-style selection

---

# 31. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Where exactly is `wrapSSE()` injected into provider fetch flows later in `provider.ts`, and are all streaming providers covered consistently?
- **Question 2**: Which gateway/proxy environments have motivated the current HTML-body and no-body heuristics in practice?
- **Question 3**: Should more providers receive explicit retryability overrides similar to the OpenAI-family 404 case?
- **Question 4**: Are there additional hosted gateways whose auth failures should get more specific remediation messages?
- **Question 5**: How do custom `openai-compatible` providers differ in stream error envelopes compared with first-party OpenAI?
- **Question 6**: Should SSE chunk timeout be configurable per provider or model family rather than relying on a single default?
- **Question 7**: Are there provider transports where `responses(...)` versus `chat(...)` selection still causes subtle tool-calling or streaming incompatibilities?
- **Question 8**: Could some silent overflow providers be better handled proactively through context budgeting instead of reactive transport parsing?

---

# 32. Summary

The `provider_transport_and_gateway_compatibility_edges` layer is where OpenCode hardens itself against the ugly real-world behavior of provider transports and intermediaries:

- it wraps SSE streams to detect stalled progress
- it recognizes HTML gateway pages and no-body failures as meaningful transport conditions
- it uses provider-specific loaders to shape headers, URLs, auth, and endpoint style correctly
- it preserves enough transport metadata for downstream retry, compaction, and user-facing diagnosis

So this is not a minor plumbing detail. It is the reliability layer that keeps OpenCode’s multi-provider runtime functioning sensibly when vendors, proxies, and gateways behave inconsistently.
