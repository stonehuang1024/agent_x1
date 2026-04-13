# Model Error Mapping / Retry Classification

---

# 1. Module Purpose

This document explains how OpenCode converts raw provider and runtime failures into structured message errors, and how those normalized errors later drive retry, compaction, or terminal-stop behavior.

The key questions are:

- Why does OpenCode normalize errors into `MessageV2` error types?
- How does `MessageV2.fromError()` classify aborts, auth failures, transport errors, API failures, and context overflows?
- Why is `ContextOverflowError` separated from generic `APIError`?
- How does retry classification consume the normalized error object?
- Where else in the system does this error mapping shape user-visible behavior?

Primary source files:

- `packages/opencode/src/session/message-v2.ts`
- `packages/opencode/src/session/retry.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/provider/error.ts`
- `packages/opencode/src/session/compaction.ts`
- `packages/opencode/src/acp/agent.ts`

This layer is OpenCode’s **provider-error normalization and execution-policy classification infrastructure**.

---

# 2. Why OpenCode normalizes model errors at all

Raw errors coming back from model providers are too inconsistent to use directly.

Different providers and SDK layers may surface failures as:

- `DOMException`
- SDK `APICallError`
- Node system errors like `ECONNRESET`
- stringified JSON blobs
- generic `Error`
- custom auth-loading failures

If the rest of the runtime had to understand all of those directly, error handling would become fragmented and brittle.

So OpenCode converts them into a smaller, stable internal error vocabulary.

---

# 3. Where normalization happens

The main entry point is:

- `MessageV2.fromError(e, { providerID })`

This function returns a structured assistant error object compatible with `Assistant["error"]`.

That makes it the canonical bridge between:

- arbitrary runtime/provider failure
- stable session/message error semantics

---

# 4. Why error normalization lives in `message-v2.ts`

At first glance, error mapping might seem like it belongs in provider code.

But OpenCode stores errors as part of assistant message state.

So the real consumer is not just provider code. It is the message model itself.

Placing normalization in `MessageV2` makes sense because the output must conform to the message schema used by:

- session persistence
- retry logic
- UI rendering
- ACP/server interfaces
- compaction decisions

---

# 5. The core structured error types

From `message-v2.ts`, the key normalized errors include:

- `APIError`
- `ContextOverflowError`
- `AuthError`
- `AbortedError`
- `OutputLengthError`
- `StructuredOutputError`
- generic `NamedError.Unknown`

Among these, the most important runtime branch is between:

- `ContextOverflowError`
- all other retryable or terminal API failures

That split drives major downstream behavior.

---

# 6. `APIError`: the general transport/provider failure bucket

`APIError` carries:

- `message`
- `statusCode?`
- `isRetryable`
- `responseHeaders?`
- `responseBody?`
- `metadata?`

This shape is extremely important because it captures both:

- user-readable explanation
- machine-readable retry and backoff data

That is why retry logic can later use it without re-reading raw provider exceptions.

---

# 7. `ContextOverflowError`: why it must be distinct

`ContextOverflowError` carries:

- `message`
- `responseBody?`

It is intentionally not just another `APIError` with a status code.

That is correct because context overflow is not merely a provider request failure.

It is a structural prompt-size condition that demands a different remediation path:

- compaction
- media stripping
- replay
- or user-visible “prompt too large” guidance

Retrying blindly would be the wrong response.

---

# 8. `fromError()` classification order matters

`MessageV2.fromError()` uses a `switch (true)` chain.

The order is meaningful:

1. Abort
2. Existing structured output length error
3. API key loading failure
4. low-level `ECONNRESET`
5. SDK `APICallError`
6. generic `Error`
7. stream-error parsing fallback
8. final unknown fallback

This shows the function prioritizes:

- known local runtime semantics first
- then provider-aware parsing
- then generic fallback handling

That ordering is sensible.

---

# 9. Abort mapping

If the error is:

- `DOMException` with `name === "AbortError"`

it becomes:

- `MessageV2.AbortedError`

This is important because user-initiated cancellation should not be conflated with provider failure.

Abort is a controlled shutdown, not an infrastructure problem.

---

# 10. Auth mapping

If the error matches:

- `LoadAPIKeyError`

it becomes:

- `MessageV2.AuthError`

with:

- `providerID`
- the original message

This gives higher layers a stable way to surface credential problems without needing to understand how auth was loaded.

---

# 11. Low-level transport mapping: `ECONNRESET`

If the raw error looks like a Node system error with:

- `code === "ECONNRESET"`

OpenCode maps it to:

- `APIError`
- `message = "Connection reset by server"`
- `isRetryable = true`
- extra transport metadata

This is a smart normalization step.

Even though it is not a provider SDK error, operationally it behaves like a retryable API transport failure.

---

# 12. `APICallError` mapping is provider-aware

If the failure is SDK-level `APICallError`, OpenCode delegates to:

- `ProviderError.parseAPICallError({ providerID, error })`

Then:

- if `parsed.type === "context_overflow"`, return `ContextOverflowError`
- otherwise return `APIError`

This is one of the most important boundaries in the stack.

It means provider-specific messiness is absorbed inside `ProviderError`, while the rest of the runtime sees only normalized `MessageV2` errors.

---

# 13. Why provider-aware parsing is necessary

Different providers may express prompt-too-large, throttling, overload, auth, or malformed-request failures differently.

OpenCode cannot rely on:

- a single status code
- a single error field name
- a single SDK convention

So `ProviderError.parseAPICallError()` acts as the translation layer from provider-specific behavior to OpenCode runtime semantics.

---

# 14. What `APICallError` mapping preserves

When mapping a non-overflow provider error, OpenCode keeps:

- `message`
- `statusCode`
- `isRetryable`
- `responseHeaders`
- `responseBody`
- `metadata`

That preservation is critical because later systems need:

- headers for retry timing
- body text for usage-limit or rate-limit detection
- status and metadata for diagnostics

The normalized layer does not throw away useful retry context.

---

# 15. Generic `Error` fallback

If the error is simply an `Error`, but did not match earlier structured cases, it becomes:

- `NamedError.Unknown({ message: e.toString() })`

This means OpenCode refuses to let a plain exception leak through untyped.

Every terminal error still becomes part of the stable message-error taxonomy.

---

# 16. Late fallback: `parseStreamError(e)`

If the error was not a standard `Error`, OpenCode still attempts:

- `ProviderError.parseStreamError(e)`

Then again:

- overflow -> `ContextOverflowError`
- otherwise -> `APIError`

This is an important second chance.

Some streaming APIs produce odd non-`Error` payloads, especially through proxies or lower-level transport layers.

OpenCode still tries to recover structured semantics from them.

---

# 17. Final fallback behavior

If everything else fails:

- `NamedError.Unknown({ message: JSON.stringify(e) })`

This is crude but correct.

The system still prefers a typed persisted unknown error over losing the failure entirely.

---

# 18. How `SessionProcessor` consumes normalized errors

In `processor.ts`, the catch block does:

- `const error = MessageV2.fromError(e, { providerID })`

Then branches on that normalized object.

The key consequence is that all later policy is based on internal error semantics, not raw exceptions.

That is exactly what a normalization layer should enable.

---

# 19. `ContextOverflowError` triggers compaction instead of retry

Once normalized, processor checks:

- `MessageV2.ContextOverflowError.isInstance(error)`

If true:

- `needsCompaction = true`
- publish session error
- do not retry

This is the clearest proof that `ContextOverflowError` is a control-flow signal, not just an error label.

---

# 20. `APIError` drives retry logic

If the error is not overflow, processor asks:

- `SessionRetry.retryable(error)`

This relies on normalized error fields, especially for:

- `APIError.isRetryable`
- `APIError.responseHeaders`
- `APIError.responseBody`

So error normalization is what makes retry policy possible in a provider-agnostic way.

---

# 21. Retry classification for `APIError`

`SessionRetry.retryable(error)` uses this logic:

- if not `APIError`, try heuristic JSON parsing
- if `APIError` and `isRetryable === false`, stop
- if `responseBody` contains `FreeUsageLimitError`, return a credit/usage message
- if `message` contains `Overloaded`, return `Provider is overloaded`
- otherwise return the API error’s message

This shows retry classification is intentionally user-facing.

It chooses a concise explanation string that can be surfaced in session status.

---

# 22. Why `responseBody` matters after normalization

For some providers, the cleanest retry classification signal is not the status code but the body contents.

For example:

- usage limit markers
- proxy-specific error envelopes
- rate-limit identifiers embedded in response payloads

Because `fromError()` preserves `responseBody`, the retry layer can still inspect it later.

This is a good example of lossless-enough normalization.

---

# 23. Retry delay depends on normalized headers

`SessionRetry.delay(attempt, error?)` reads:

- `error.data.responseHeaders["retry-after-ms"]`
- `error.data.responseHeaders["retry-after"]`

This is only possible because `APIError` stores normalized headers in a predictable shape.

Again, normalization is what turns diverse provider failures into reusable policy inputs.

---

# 24. Where normalized errors influence compaction itself

In `session/compaction.ts`, if compaction recursively returns `compact`, the compaction assistant message is explicitly assigned:

- `new MessageV2.ContextOverflowError(...)`

That means `ContextOverflowError` is not only produced from provider failures.

It is also used internally to represent a semantic “this prompt cannot fit” condition in OpenCode’s own control flow.

That is a very strong signal that overflow is a first-class runtime concept.

---

# 25. Where normalized errors influence ACP/server behavior

`acp/agent.ts` repeatedly uses:

- `MessageV2.fromError(e, { providerID })`

for API-facing operations.

This means ACP/server layers also rely on the same normalized error taxonomy.

That is good architecture.

It avoids one error vocabulary for internal session runtime and another for remote/control-plane interfaces.

---

# 26. Where normalized errors influence CLI UX

In `cli/cmd/github.ts`, the code checks:

- `err.name === "ContextOverflowError"`

and converts it into a friendlier prompt-too-large error.

This is another important effect of normalization:

- CLIs can branch on stable error names
- without needing provider-specific logic

---

# 27. Error mapping and message persistence are tightly linked

Because assistant messages persist structured `error` objects, normalized error names become part of the durable session record.

That means the taxonomy must stay meaningful over time.

It is not just for transient control flow.

It also shapes:

- history display
- API responses
- debugging
- downstream automation

---

# 28. Why `OutputLengthError` is preserved if already structured

`fromError()` explicitly returns the error unchanged when:

- `MessageV2.OutputLengthError.isInstance(e)`

This shows the system avoids double-wrapping already normalized internal errors.

That is the correct behavior.

If an earlier layer already emitted a recognized OpenCode error type, later layers should preserve it.

---

# 29. A complete error-policy pipeline

The full flow looks like this:

## 29.1 Raw failure occurs

- transport error
- provider SDK error
- auth load failure
- overflow
- generic runtime exception

## 29.2 Normalize

- `MessageV2.fromError(e, { providerID })`

## 29.3 Classify by semantic type

- `ContextOverflowError` -> compaction path
- `APIError` with retryable metadata -> retry path
- `AuthError` -> terminal credential failure
- `AbortedError` -> controlled cancellation
- `Unknown` -> terminal generic failure

## 29.4 Persist and surface

- assistant message error
- session events
- status transitions
- CLI/API UX branches

This is a clean and well-factored pipeline.

---

# 30. Key design principles behind this module

## 30.1 Provider errors must be translated into runtime semantics, not merely re-labeled

That is why overflow becomes a dedicated error type.

## 30.2 Retry policy should consume normalized machine-readable fields, not parse raw exceptions everywhere

That is why `APIError` preserves retryability, headers, and body.

## 30.3 Internal control-flow failures and provider failures can share the same semantic taxonomy

That is why `ContextOverflowError` is used both for provider responses and internal compaction failure.

## 30.4 Persisted error records should remain meaningful to every layer of the system

That is why ACP, CLI, processor, and UI can all branch on the same normalized error names.

---

# 31. Recommended reading order

To continue digging deeper, read in this order:

1. `packages/opencode/src/session/message-v2.ts`
2. `packages/opencode/src/provider/error.ts`
3. `packages/opencode/src/session/retry.ts`
4. `packages/opencode/src/session/processor.ts`
5. `packages/opencode/src/session/compaction.ts`
6. `packages/opencode/src/acp/agent.ts`

Focus on these functions and concepts:

- `MessageV2.fromError()`
- `APIError`
- `ContextOverflowError`
- `ProviderError.parseAPICallError()`
- `ProviderError.parseStreamError()`
- `SessionRetry.retryable()`
- `SessionRetry.delay()`

---

# 32. Open questions for further investigation

There are several strong follow-up questions worth investigating:

- **Question 1**: What exact heuristics inside `provider/error.ts` map different vendor responses into `context_overflow` versus generic retryable API failure?
- **Question 2**: Should more low-level transport failures besides `ECONNRESET` be explicitly normalized as retryable `APIError`s?
- **Question 3**: Is `NamedError.Unknown` too broad for some classes of runtime failure that deserve stronger structure?
- **Question 4**: Should `ContextOverflowError` include more machine-readable metadata, such as prompt token estimates or attachment contribution?
- **Question 5**: Are response headers consistently normalized across all providers and proxies, especially for custom gateways?
- **Question 6**: How stable is the retry JSON heuristic for non-API errors across different providers?
- **Question 7**: Should CLI and ACP layers expose more of the preserved `metadata` for diagnostics?
- **Question 8**: Are there any persisted historical sessions whose old error shapes differ from the current normalized taxonomy?

---

# 33. Summary

The `model_error_mapping_and_retry_classification` layer is where OpenCode turns provider chaos into stable runtime meaning:

- `MessageV2.fromError()` normalizes raw failures into durable internal error types
- `APIError` preserves retry and diagnostics metadata for later policy decisions
- `ContextOverflowError` is kept separate because it requires compaction, not blind retry
- `SessionRetry`, `SessionProcessor`, ACP, and CLI layers all depend on this shared error vocabulary

So this is not just an error-formatting utility. It is the semantic bridge that lets the rest of OpenCode make consistent decisions about retrying, compacting, stopping, and explaining failures to the user.
