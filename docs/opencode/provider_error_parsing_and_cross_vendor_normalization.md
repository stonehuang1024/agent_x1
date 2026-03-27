# Provider Error Parsing / Cross-Vendor Normalization

---

# 1. Module Purpose

This document explains how OpenCode translates provider-specific API and stream failures into a small cross-vendor error vocabulary before those errors are mapped into `MessageV2` runtime errors.

The key questions are:

- Why does OpenCode need a provider-specific error parser at all?
- How does `provider/error.ts` detect context overflow across many vendors?
- How are messy provider messages normalized into cleaner user-facing text?
- Why is OpenAI retryability treated specially?
- What is the difference between `parseAPICallError()` and `parseStreamError()`?

Primary source files:

- `packages/opencode/src/provider/error.ts`
- `packages/opencode/src/session/message-v2.ts`
- `packages/opencode/src/session/retry.ts`
- `packages/opencode/src/provider/schema.ts`

This layer is OpenCode’s **cross-vendor provider-error interpretation and normalization bridge**.

---

# 2. Why provider-specific error parsing exists

Even after using a shared SDK layer, provider failures are still inconsistent.

Different vendors and gateways vary in:

- message wording
- response body format
- HTTP status behavior
- retryability hints
- stream error structure
- overflow signaling

If OpenCode tried to handle all of that directly inside session runtime code, provider quirks would leak everywhere.

So `provider/error.ts` acts as a provider-facing normalization step before runtime-facing error mapping happens.

---

# 3. Where this module sits in the stack

The rough flow is:

- raw provider or stream failure occurs
- `ProviderError.parseAPICallError(...)` or `ProviderError.parseStreamError(...)`
- `MessageV2.fromError(...)` converts parsed output into runtime error types
- `SessionProcessor` / `SessionRetry` consume those runtime errors

So this module is not the final error taxonomy.

It is the intermediate semantic cleanup layer between provider chaos and runtime policy.

---

# 4. The central design goal

This file is trying to answer a simple but hard question:

- given a provider-specific failure, what does OpenCode actually need to know?

Usually the important answers are only:

- is this context overflow?
- is this retryable?
- what should the user-facing message be?
- what body/headers/metadata should be preserved?

That reduction is the core design goal.

---

# 5. Overflow detection is the most important normalization job

The file starts with:

- `OVERFLOW_PATTERNS`

This is a curated regex list covering many vendors, including patterns associated with:

- Anthropic
- Amazon Bedrock
- OpenAI
- Gemini
- xAI
- Groq
- OpenRouter / DeepSeek
- GitHub Copilot
- llama.cpp server
- LM Studio
- MiniMax
- Moonshot / Kimi
- generic context-length failures
- HTTP `413`

This immediately shows the module’s main purpose:

- **cross-vendor prompt-too-large detection**

---

# 6. Why overflow detection needs regexes at all

There is no portable universal overflow signal across providers.

Some providers return:

- a structured code
- a generic 400
- a 413
- plain text in the message
- gateway-transformed text
- no body at all

So regex-based message recognition is unavoidable here.

This is not pretty, but it is practical and grounded in real provider behavior.

---

# 7. `isOverflow(message)` combines regex and status-like heuristics

`isOverflow(...)` first checks the regex list.

Then it also handles a special no-body pattern:

- `400 (no body)`
- `413 (no body)`

for providers or gateways such as:

- Cerebras
- Mistral

This is important because some providers signal prompt overflow only through a status-shell message with no useful structured content.

---

# 8. Why no-body detection matters

If OpenCode only looked for friendly vendor messages, many overflow conditions would fall through into generic API errors.

That would break the compaction path and produce worse UX.

The no-body heuristic prevents that.

---

# 9. `message(providerID, e)`: human-readable message reconstruction

A major responsibility of this file is not just classification, but message cleanup.

`message(...)` tries to produce the best user-facing text from an `APICallError`.

Its logic roughly is:

- if SDK message is empty, fall back to response body or HTTP status text
- if response body is missing, or message already looks meaningful, return the message
- otherwise try to parse JSON response bodies for useful nested error text
- if response body is HTML, replace it with a more readable gateway/proxy explanation
- otherwise concatenate message and body

This is a thoughtful normalization strategy.

---

# 10. Why HTML response-body handling is important

Gateways and proxies sometimes return:

- HTML error pages

instead of JSON.

Dumping raw HTML into the user-visible error would be terrible.

So `message(...)` detects HTML-looking bodies and replaces them with more helpful explanations, especially for:

- `401`
- `403`

This is a very practical UX improvement.

---

# 11. The special `401` HTML message

For HTML + `401`, OpenCode returns a guidance-oriented message that suggests re-authentication, including the `opencode auth login <your provider URL>` flow.

This is notable because provider-error normalization is not purely descriptive.

It also nudges the user toward an operational fix.

---

# 12. JSON body parsing inside `message(...)`

If the response body parses as JSON, the function tries common nested fields such as:

- `body.message`
- `body.error`
- `body.error.message`

Then it combines that with the top-level SDK message.

This is useful because many SDKs provide a generic outer message while the real cause is nested deeper in the response body.

---

# 13. Why `providerID` is passed into `message(...)`

The current implementation does not yet use `providerID` heavily inside `message(...)`, but the signature shows intent.

Provider-aware message shaping may expand over time.

That is a sensible API design choice because this layer is explicitly about vendor-aware normalization.

---

# 14. `parseStreamError(input)`: stream-payload normalization

This function handles non-`APICallError` structured payloads that may come from stream failures.

It:

- parses JSON-like input with `json(...)`
- requires `body.type === "error"`
- switches on `body.error.code`

Recognized stream error codes include:

- `context_length_exceeded`
- `insufficient_quota`
- `usage_not_included`
- `invalid_prompt`

This function returns either:

- `context_overflow`
- `api_error`

with already-cleaned messages.

---

# 15. Why `parseStreamError()` is separate from `parseAPICallError()`

These two functions serve different raw shapes:

## 15.1 `parseAPICallError()`

- consumes SDK `APICallError`
- sees status codes, headers, body, retryability flags

## 15.2 `parseStreamError()`

- consumes generic stream-side payloads
- usually only has JSON error content
- lacks full header/status richness

Keeping them separate avoids awkward overloading and makes each parser clearer.

---

# 16. `parseStreamError()` currently treats stream API errors as non-retryable

Its `api_error` branch always returns:

- `isRetryable: false`

This is a conservative choice.

Without robust retry metadata from stream payloads, OpenCode prefers not to speculate.

That is safer than blindly retrying ambiguous stream failures.

---

# 17. `usage_not_included` is a good example of semantic normalization

For stream error code:

- `usage_not_included`

OpenCode returns a very specific message about upgrading to Plus for Codex use.

That is much more actionable than simply preserving a raw provider code.

It shows the module is trying to normalize toward operator usefulness, not just structural consistency.

---

# 18. `parseAPICallError(...)`: the main provider error parser

This is the core function for standard SDK request failures.

Its logic is:

1. build a normalized message via `message(...)`
2. if overflow is detected or `statusCode === 413`, return `context_overflow`
3. otherwise return `api_error`
4. preserve status, headers, body, and metadata
5. compute retryability with one provider-specific exception for OpenAI-family IDs

This is compact but high-value logic.

---

# 19. Why `413` is treated as overflow unconditionally

`413` means request entity too large.

In the context of model prompting, that is effectively equivalent to prompt/context overflow for OpenCode’s purposes.

So mapping it directly to `context_overflow` is reasonable.

This helps route those failures into compaction instead of generic error handling.

---

# 20. OpenAI retryability special case

The parser uses:

- `input.providerID.startsWith("openai")`

to decide whether to override retryability through `isOpenAiErrorRetryable(...)`.

That helper returns true for:

- `status === 404`
- or whatever the SDK already marked retryable

The embedded comment explains why:

- OpenAI sometimes returns `404` for models that are actually available

This is a very concrete vendor quirk being normalized away.

---

# 21. Why provider-specific retryability belongs here

Retryability is partly a provider concern and partly a runtime concern.

The provider-facing half belongs in this file.

For example:

- an OpenAI `404` may be transient or gateway-related in practice
- another provider’s `404` might clearly be terminal

This file captures that provider-side interpretation before the generic runtime retry layer sees the error.

---

# 22. Metadata preservation in `parseAPICallError()`

The parser preserves:

- `statusCode`
- `responseHeaders`
- `responseBody`
- `metadata`

Currently `metadata` mainly captures:

- `url`

when present.

This is valuable because later layers may need diagnostic context even after normalization has happened.

---

# 23. Why the parsed result type is intentionally small

`ParsedAPICallError` only has two variants:

- `context_overflow`
- `api_error`

That is a strong reduction.

The parser does not try to model every provider-specific error kind.

It only preserves distinctions that matter downstream for runtime control:

- overflow versus non-overflow
- retryable versus non-retryable
- good message text
- headers/body/metadata

This is the right abstraction level.

---

# 24. `json(input)` is a tiny but important helper

The helper accepts:

- JSON strings
- plain objects

and returns an object if possible.

This allows `parseStreamError()` to tolerate multiple calling shapes without re-implementing parsing logic repeatedly.

Small utility, useful role.

---

# 25. How this parser connects to `MessageV2.fromError()`

In `message-v2.ts`, `fromError()` delegates to:

- `ProviderError.parseAPICallError(...)`
- `ProviderError.parseStreamError(...)`

Then it converts parsed results into runtime-native types:

- `ContextOverflowError`
- `APIError`

This shows the layering clearly:

- provider parser understands vendor quirks
- message layer understands runtime semantics and persistence schema

That separation is excellent.

---

# 26. How this parser connects to retry behavior

Once `parseAPICallError()` has set:

- `isRetryable`
- `responseHeaders`
- `responseBody`

`SessionRetry` can later use those normalized fields to decide:

- whether to retry
- what message to show
- how long to wait

So even though retry happens elsewhere, this module materially shapes retry behavior.

---

# 27. How this parser connects to compaction behavior

If `parseAPICallError()` or `parseStreamError()` returns:

- `type: "context_overflow"`

then `MessageV2.fromError()` turns it into:

- `ContextOverflowError`

and `SessionProcessor` routes that into:

- compaction instead of retry

So overflow detection here directly controls whether the runtime tries to compress and continue the conversation.

---

# 28. What this file does not try to do

It does not:

- persist errors
- decide retry timing
- update session status
- trigger compaction directly
- classify human approval failures

That is good design.

This module is focused on provider-facing interpretation only.

---

# 29. A complete normalization flow example

A representative path looks like this:

## 29.1 Provider returns strange overflow text

- maybe `400 (no body)`
- maybe `maximum context length is ...`
- maybe `request entity too large`

## 29.2 `parseAPICallError()` runs

- reconstructs message
- detects overflow
- returns `context_overflow`

## 29.3 `MessageV2.fromError()` runs

- returns `ContextOverflowError`

## 29.4 `SessionProcessor` reacts

- no retry
- trigger compaction path

This is exactly why cross-vendor overflow normalization is so valuable.

---

# 30. Key design principles behind this module

## 30.1 Provider diversity should be absorbed before runtime policy is applied

So vendor quirks are handled in `provider/error.ts`, not scattered across session code.

## 30.2 Overflow detection is the most important semantic distinction

So the parser works hard to recognize prompt-too-large conditions across vendors and gateways.

## 30.3 User-facing error messages should be cleaned up early

So HTML bodies, nested JSON errors, and empty messages are normalized before they reach the runtime.

## 30.4 Retryability must account for provider quirks

So OpenAI-family `404` behavior is corrected here instead of contaminating generic retry logic.

---

# 31. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/provider/error.ts`
2. `packages/opencode/src/session/message-v2.ts`
3. `packages/opencode/src/session/retry.ts`
4. `packages/opencode/src/provider/schema.ts`

Focus on these functions and concepts:

- `OVERFLOW_PATTERNS`
- `isOverflow()`
- `message()`
- `parseStreamError()`
- `parseAPICallError()`
- `isOpenAiErrorRetryable()`
- `MessageV2.fromError()`

---

# 32. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Which providers still slip through overflow detection even with the current regex list, especially newer gateways and self-hosted backends?
- **Question 2**: Should more provider-specific retryability overrides exist beyond the current OpenAI-family 404 rule?
- **Question 3**: Is there enough structured metadata preserved today for postmortem debugging of provider failures?
- **Question 4**: Should HTML error-body normalization cover more gateway cases beyond the current 401/403 specialization?
- **Question 5**: Are there stream error codes from newer providers that should be recognized in `parseStreamError()`?
- **Question 6**: Should `providerID` be used more aggressively inside `message()` for provider-specific message shaping?
- **Question 7**: Can prompt-too-large conditions from silent-failure providers like the comment-mentioned `z.ai` be detected proactively through token budgeting instead?
- **Question 8**: How often do reverse proxies or API gateways distort provider errors enough that the current parser loses important semantics?

---

# 33. Summary

The `provider_error_parsing_and_cross_vendor_normalization` layer is where OpenCode turns provider-specific failure noise into a small, runtime-meaningful semantic surface:

- it detects context overflow across many vendors and gateways
- it reconstructs more useful human-readable messages from empty, nested, or HTML error responses
- it normalizes retryability and preserves headers/body/metadata for downstream policy
- it feeds the later `MessageV2` error taxonomy used by retry, compaction, status handling, and client UX

So this module is not a minor helper. It is the provider-facing semantic adapter that makes the rest of OpenCode’s error handling behave consistently across a messy multi-vendor model ecosystem.
