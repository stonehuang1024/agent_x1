# Provider Transform / Message Protocol Rewrites

---

# 1. Module Purpose

This document explains the provider-specific transformation layer that rewrites model messages and schemas before they are sent to concrete model providers.

The key questions are:

- Why does OpenCode need a provider transform layer at all?
- What kinds of protocol mismatches does `ProviderTransform.message(...)` fix?
- How do message rewrites differ for Anthropic, Claude-family, Mistral, and OpenAI-compatible providers?
- How are unsupported input modalities handled before provider submission?
- Why is schema transformation tied to the same layer that rewrites outgoing messages?

Primary source files:

- `packages/opencode/src/provider/transform.ts`
- `packages/opencode/src/session/llm.ts`
- `packages/opencode/src/session/prompt.ts`

This layer is OpenCode’s **provider-protocol normalization and compatibility rewrite layer**.

---

# 2. Why this layer matters

OpenCode wants the rest of the runtime to think in a mostly provider-neutral way:

- session logic builds messages
- tools expose schemas
- model execution is invoked through a unified flow

But provider APIs are not actually identical.

They differ in areas like:

- empty message tolerance
- tool call ID constraints
- message sequence validity
- reasoning-channel conventions
- media support
- provider option key names

`ProviderTransform` is the layer that absorbs these differences so they do not leak everywhere else.

---

# 3. The main public seams are `message(...)` and `schema(...)`

The code search shows two main call sites:

- `ProviderTransform.message(...)` in `session/llm.ts`
- `ProviderTransform.schema(...)` in `session/prompt.ts`

This is an important architectural clue.

Provider transforms operate on both:

- outgoing conversation/message payloads
- tool input schemas

That means provider compatibility is handled at both the message protocol level and the tool-schema level.

---

# 4. `ProviderTransform.message(...)` is a pipeline, not one rewrite

The `message(...)` function applies several stages:

- `unsupportedParts(...)`
- `normalizeMessages(...)`
- optional caching injection
- provider-options key remapping

This is a good structure.

Rather than one giant tangled function, the transform is organized into conceptually separate normalization steps.

---

# 5. `unsupportedParts(...)`: modality gating before provider submission

The first stage checks user message parts for:

- files
- images

It maps MIME type into a modality such as:

- `image`
- `audio`
- `video`
- `pdf`

Then it compares that against `model.capabilities.input[modality]`.

If the model does not support that modality, the part is replaced with a text error message.

This is a very important pre-flight compatibility guard.

---

# 6. Why unsupported modalities become text errors instead of silent drops

If a model cannot consume a modality, silently removing the part would lose important user intent.

Instead, OpenCode rewrites unsupported media into explicit text telling the model:

- it cannot read the file or image
- it should inform the user

This preserves the conversational semantics of the problem while staying within provider constraints.

That is much better than silent degradation.

---

# 7. Empty or corrupted images are detected explicitly

`unsupportedParts(...)` also checks for empty base64 image data and rewrites it into a text error:

- `ERROR: Image file is empty or corrupted. Please provide a valid image.`

This is another good example of compatibility-plus-validation handling.

The runtime catches obviously broken media payloads before they become confusing provider-side failures.

---

# 8. Why this stage only rewrites user content

The function only rewrites:

- user messages with array content

That makes sense because user messages are where file and image attachments typically enter provider-facing message payloads.

The purpose is not general message sanitization. It is to ensure incoming multimodal user content does not violate provider capability constraints.

---

# 9. `normalizeMessages(...)`: provider-specific protocol repair layer

After unsupported parts are handled, `normalizeMessages(...)` performs deeper provider-specific rewrites.

This is where the transform layer adapts message lists to satisfy concrete provider protocol requirements.

Several distinct strategies are visible.

---

# 10. Anthropic and Bedrock reject empty content

For Anthropic and Bedrock npm providers, the transform filters out:

- messages with empty string content
- empty text parts
- empty reasoning parts
- messages that become empty after filtering

This is source-grounded in the comment:

- Anthropic rejects messages with empty content

So the transform protects the runtime from a provider that is stricter than the neutral in-memory message model.

---

# 11. Why empty-message filtering belongs here

The session/runtime layers may legitimately create transient empty pieces while assembling or transforming message streams.

They should not all need to know Anthropic’s exact tolerance rules.

By filtering empties here, OpenCode centralizes that provider-specific requirement in one place.

That is exactly what a transform layer is for.

---

# 12. Claude-family tool call IDs are normalized

If `model.api.id.includes("claude")`, the transform rewrites `toolCallId` values for assistant or tool messages by replacing invalid characters with `_`.

This is an important provider-specific repair.

The rest of the runtime can create IDs more freely, while the Claude-facing wire format is normalized to what the provider accepts.

---

# 13. Why tool-call ID normalization matters

Tool call IDs are part of the protocol linkage between:

- tool calls
- tool results
n
If provider-side validation rejects the ID format, the whole tool conversation can fail.

So even though the ID itself is mostly infrastructure metadata, it still needs provider-aware normalization.

---

# 14. Mistral has even stricter tool-call ID requirements

For Mistral-like models, the transform rewrites IDs to be:

- alphanumeric only
- exactly 9 characters

It does this by:

- removing non-alphanumerics
- truncating to 9 chars
- padding with zeros if needed

This is one of the strongest examples of hard provider protocol constraints leaking into message transport.

The transform layer absorbs that leak for the rest of the runtime.

---

# 15. Mistral also needs message-sequence repair

The transform contains a specific fix for this case:

- a `tool` message followed immediately by a `user` message

When that happens, it inserts an assistant message containing:

- `Done.`

This is a notable and very concrete protocol workaround.

It means Mistral does not accept that raw sequence shape, so OpenCode repairs the message list before submission.

---

# 16. Why sequence repair is more serious than simple field normalization

Field normalization is one thing.

Message-sequence repair means the provider expects a different conversational grammar than the neutral runtime model naturally produces.

That is exactly why a dedicated transform layer is necessary.

Without it, session logic would have to be polluted with provider-specific sequencing hacks.

---

# 17. Interleaved reasoning support is handled through provider options

If `model.capabilities.interleaved` declares a `field`, the transform:

- collects reasoning parts from assistant content
- concatenates their text
- removes reasoning parts from visible content
- injects the reasoning text into `providerOptions.openaiCompatible[field]`

This is a very important rewrite.

It shows that for some models, reasoning is not sent as normal visible message content but as provider-specific side-channel metadata.

---

# 18. Why reasoning-channel rewrites belong here

Reasoning representation is one of the most provider-specific aspects of modern model APIs.

Some providers expect reasoning inline.

Others expect it in a special provider-option field.

The runtime wants a neutral internal representation of reasoning parts, so `ProviderTransform` is the right place to translate that representation into provider-specific wire format.

---

# 19. `applyCaching(...)`: cache hints as provider-specific transport metadata

For Anthropic-like models, `message(...)` also applies caching hints to selected messages.

The logic:

- picks the first system messages and the last non-system messages
- injects provider-specific cache metadata
- sometimes at message level
- sometimes on the last content part

This is a sophisticated transport optimization step.

It is not part of conversation semantics. It is part of provider execution efficiency.

---

# 20. Why cache metadata differs by provider

The injected cache options include variants like:

- `anthropic.cacheControl`
- `openrouter.cacheControl`
- `bedrock.cachePoint`
- `openaiCompatible.cache_control`
- `copilot.copilot_cache_control`

This is a clear example of why provider option names cannot be treated as interchangeable.

Even when the conceptual intent is “mark this for caching,” the wire representation differs.

---

# 21. Why cache hints sometimes attach to message level and sometimes content level

The code chooses between message-level and content-level provider options depending on provider family.

That means not only option names but also option placement can vary by provider.

Again, this is exactly the sort of low-level incompatibility the transform layer exists to hide from higher-level logic.

---

# 22. `sdkKey(...)`: remapping provider option namespaces

The transform includes `sdkKey(npm)` to map npm provider package names into the provider-option namespace the AI SDK expects.

Examples include:

- OpenAI/Azure -> `openai`
- Bedrock -> `bedrock`
- Anthropic -> `anthropic`
- Google -> `google`
- OpenRouter -> `openrouter`
- Copilot -> `copilot`

This is a subtle but important compatibility layer.

The stored provider identity in OpenCode is not always the same key the SDK expects for `providerOptions`.

---

# 23. Why provider-options remapping is necessary

A runtime may persist or reason about provider identity one way, while the downstream SDK expects a different namespace key.

If those keys are not remapped, provider options could silently fail to apply.

That would be a very hard bug to notice.

So explicit remapping is a strong reliability measure.

---

# 24. Why Azure is excluded from some remapping behavior

The remap block excludes:

- `@ai-sdk/azure`

This means Azure compatibility is special enough that the general remapping rule should not be applied blindly.

Even without seeing every downstream detail, the explicit exception itself is important architectural evidence:

- provider-option namespace handling is not uniform enough to be fully generic

---

# 25. `ProviderTransform.message(...)` is called at the final transport boundary

The grep results show `session/llm.ts` calling:

- `ProviderTransform.message(args.params.prompt, input.model, options)`

inside the stream-parameter transform path.

This is exactly the right place.

By applying the transform at the final provider invocation boundary, the rest of the session runtime can keep working with a more neutral message representation for as long as possible.

---

# 26. `ProviderTransform.schema(...)` is the tool-schema counterpart

The grep results also show `ProviderTransform.schema(...)` being used in `session/prompt.ts` when:

- projecting normal tool schemas
- projecting MCP tool schemas

This is important because providers differ not only in message protocol but also in what JSON-schema shapes they accept reliably.

So schema rewriting belongs beside message rewriting.

---

# 27. Why schema and message transforms belong in one module

Both are solving the same class of problem:

- adapting a provider-neutral runtime representation into provider-compatible transport payloads

If message transforms and schema transforms were split arbitrarily, compatibility reasoning would become harder.

Keeping them together makes the provider-normalization layer easier to understand and maintain.

---

# 28. This module is not just about “cleanup”

It would be easy to describe this file as a collection of hacks.

That would be misleading.

What it really does is enforce a deliberate architectural boundary:

- runtime internals stay mostly provider-neutral
- provider protocol quirks are absorbed at the edge

That is not accidental cleanup. It is a compatibility architecture.

---

# 29. A representative transform pipeline

A typical message flow looks like this:

## 29.1 Session/runtime builds provider-neutral messages

- reasoning parts
- tool calls/results
- files or images
- provider-agnostic providerOptions assumptions

## 29.2 `ProviderTransform.message(...)` runs at invocation time

- rejects unsupported media by rewriting to explicit text
- filters invalid empty content
- normalizes tool call IDs
- repairs provider-specific message sequence constraints
- injects reasoning into provider-specific fields when needed
- injects cache metadata
- remaps providerOptions namespace keys

## 29.3 Provider receives a wire-format-compatible payload

This is the real protocol-adaptation pipeline.

---

# 30. Why this module matters architecturally

This layer explains how OpenCode can support many providers without infecting every higher-level subsystem with provider-specific conditionals.

It centralizes protocol adaptation for:

- content support
- message validity
- tool metadata validity
- reasoning-channel formatting
- caching hints
- SDK namespace compatibility

That is one of the strongest signs of deliberate multi-provider runtime design in the codebase.

---

# 31. Key design principles behind this module

## 31.1 Internal runtime representations should stay more neutral than provider wire formats

So message parts and tool schemas are transformed only near the provider boundary.

## 31.2 Provider incompatibilities should be normalized explicitly rather than left to fail unpredictably at runtime

So empty content, bad tool IDs, unsupported modalities, and bad message sequences are rewritten deterministically.

## 31.3 When compatibility requires semantic degradation, the system should preserve user intent as text rather than silently dropping content

So unsupported files or images become explicit textual error content instead of disappearing.

## 31.4 Transport optimizations such as caching and reasoning side channels belong in the compatibility layer, not in session orchestration logic

So cache hints and reasoning-field rewrites live in `ProviderTransform`.

---

# 32. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/provider/transform.ts`
2. `packages/opencode/src/session/llm.ts`
3. `packages/opencode/src/session/prompt.ts`

Focus on these functions and concepts:

- `ProviderTransform.message()`
- `unsupportedParts()`
- `normalizeMessages()`
- `applyCaching()`
- `sdkKey()`
- `ProviderTransform.schema()`
- the `transformParams` call site in `llm.ts`
- the tool-schema call sites in `prompt.ts`

---

# 33. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: What exact transformations does `ProviderTransform.schema()` apply for different providers, and how often do provider schema quirks differ from message quirks?
- **Question 2**: Which providers besides Anthropic, Mistral, and OpenAI-compatible ones still need additional explicit message rewrites over time?
- **Question 3**: Should unsupported-modality rewrites be surfaced more explicitly to users, not only to the model?
- **Question 4**: How stable are the current reasoning-field conventions for openai-compatible providers, and how will this evolve as provider APIs change?
- **Question 5**: Are there edge cases where tool-call ID normalization could accidentally cause collisions after truncation or character stripping?
- **Question 6**: Should sequence repairs like the Mistral `tool -> user` fix be modeled more declaratively as provider grammar rules?
- **Question 7**: How should provider transform testing be structured so protocol rewrites remain trustworthy as providers evolve?
- **Question 8**: Are there future opportunities to split transport optimization concerns like caching from pure protocol-validity transforms without losing architectural clarity?

---

# 34. Summary

The `provider_transform_message_protocol_rewrites` layer is the boundary where OpenCode’s provider-neutral runtime model is rewritten into provider-compatible wire format:

- unsupported media is rewritten into explicit textual error content
- empty-content, tool-ID, sequence, and reasoning-channel incompatibilities are normalized per provider
- cache hints and provider-option namespace remapping are applied at the transport edge
- the same module also participates in tool-schema compatibility through `ProviderTransform.schema()`

So this module is the compatibility shield that lets the rest of OpenCode behave more uniformly across a messy multi-provider model ecosystem.
