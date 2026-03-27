# Session Retry / Delay and Retryable Error Policy

---

# 1. Module Purpose

This document explains how OpenCode decides whether a failed assistant step should be retried, how long it waits before retrying, and how retry state is surfaced during execution.

The key questions are:

- Which failures are considered retryable versus non-retryable?
- Why are context-overflow failures explicitly excluded from retry?
- How does `SessionRetry.delay(...)` combine provider headers with exponential backoff?
- How does `SessionProcessor` surface retry state to the rest of the runtime?
- What does this retry policy reveal about OpenCode’s distinction between transient transport/provider failures and failures that require a different execution strategy?

Primary source files:

- `packages/opencode/src/session/retry.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/session/message-v2.ts`

This layer is OpenCode’s **transient-failure retry classification and backoff policy**.

---

# 2. Why this layer matters

A long-running agent runtime cannot treat all failures the same.

Some failures mean:

- retry the same operation later

Others mean:

- stop immediately
- ask the user
- compact context
- fix credentials

The retry layer is where OpenCode draws that boundary for transient provider and transport failures.

That makes it a core resilience mechanism.

---

# 3. Retry policy is split across three modules

The full retry story spans:

- `MessageV2.fromError(...)` for error normalization
- `SessionRetry.retryable(...)` for retry classification and user-facing retry text
- `SessionRetry.delay(...)` / `sleep(...)` for backoff timing
- `SessionProcessor` for orchestration behavior

This is a clean separation of concerns.

Error shaping, retry classification, delay computation, and retry execution are related but not collapsed into one function.

---

# 4. `MessageV2.fromError(...)` shapes raw failures into typed runtime errors first

Before retry policy even runs, the processor converts thrown errors into normalized message errors.

Important cases include:

- abort -> `AbortedError`
- provider key loading -> `AuthError`
- `ECONNRESET` -> retryable `APIError`
- API call failures -> parsed `APIError` or `ContextOverflowError`
- parsed stream errors -> parsed `APIError` or `ContextOverflowError`

This is very important.

Retry policy operates on OpenCode’s own typed error model, not on arbitrary raw provider exceptions.

---

# 5. Why normalized error typing matters for retry logic

Without normalization, retry policy would need to understand many inconsistent upstream exception shapes.

By first mapping errors into `MessageV2` error types, OpenCode can classify retryability more deterministically.

That is a strong reliability pattern.

---

# 6. `ContextOverflowError` is explicitly never retried

`SessionRetry.retryable(...)` begins with:

- if error is `ContextOverflowError`, return `undefined`

This is one of the most important retry decisions in the entire runtime.

Context overflow is not a transient failure.

Retrying the exact same prompt with the exact same context is overwhelmingly likely to fail again.

So the runtime correctly refuses to waste retries there.

---

# 7. Why non-retry for overflow is a root-cause-oriented policy

This matches the user’s root-cause preference very well.

The real fix for overflow is:

- reduce or transform context

not:

- wait and try the same oversized request again

That is why overflow is routed toward compaction rather than retry.

---

# 8. Retryable `APIError` depends on the provider parser’s `isRetryable` flag

For `MessageV2.APIError`, `SessionRetry.retryable(...)` checks:

- `error.data.isRetryable`

If false, there is no retry.

So retryability is not guessed solely from message text. It is inherited from provider-aware parsing performed earlier.

That is an important architectural detail.

---

# 9. Why provider-aware retryability is the right design

Different providers expose rate limits, overloads, quota exhaustion, and malformed requests differently.

OpenCode lets provider parsing decide whether a given API failure is transient enough to retry.

That avoids overgeneralizing from raw status codes or brittle string matching alone.

---

# 10. Free-usage exhaustion gets a custom retry message

If a retryable API error’s response body includes:

- `FreeUsageLimitError`

then the retry message becomes:

- `Free usage exceeded, add credits https://opencode.ai/zen`

This is notable.

The runtime still surfaces it through the retry classification path, but with a very specific operator-facing explanation.

---

# 11. Overload messages are normalized into friendlier status text

If a retryable API error message includes `Overloaded`, the retry text becomes:

- `Provider is overloaded`

Otherwise it uses the provider message itself.

So `retryable(...)` is not only a yes/no function.

It also computes the human-readable retry status message shown during retry state.

---

# 12. `retryable(...)` also has a JSON-parsing fallback path

If the error is not a directly recognized `APIError`, the function tries to parse `error.data.message` as JSON.

Then it checks patterns like:

- `json.error.type === "too_many_requests"`
- code containing `exhausted` or `unavailable`
- error code containing `rate_limit`

This is a pragmatic fallback for providers that embed structured failure data inside message strings.

---

# 13. Why the JSON fallback exists

Not every provider or upstream layer emits perfectly normalized retry metadata.

The fallback path gives OpenCode one more chance to classify rate-limit or overload failures as transient.

That is a sensible robustness measure at the edge of inconsistent provider ecosystems.

---

# 14. `retryable(...)` returns a string, not a boolean

This is a subtle but important API design choice.

The function returns either:

- `undefined` for non-retryable
- or a human-readable retry message

This means retry classification and retry-status presentation are fused into one return value.

That keeps the processor logic simpler because one function answers both:

- should we retry?
- what status message should we show while retrying?

---

# 15. `delay(...)` prefers provider headers over local backoff heuristics

`SessionRetry.delay(attempt, error?)` first looks for response headers on an API error.

It checks, in order:

- `retry-after-ms`
- `retry-after` as seconds
- `retry-after` as HTTP date

Only if none of those yield a delay does it fall back to exponential backoff.

This is the correct precedence.

Provider-directed retry timing is usually more accurate than a generic client heuristic.

---

# 16. Why supporting both `retry-after-ms` and `retry-after` matters

Different providers expose retry hints in different formats.

By supporting:

- explicit millisecond header
- numeric seconds header
- HTTP date header

OpenCode can adapt to multiple provider conventions without special-casing every backend in the processor itself.

---

# 17. Exponential backoff is the default fallback policy

When headers do not supply a retry delay, the function uses:

- initial delay `2000ms`
- backoff factor `2`

So attempts scale like:

- 2s
- 4s
- 8s
- 16s
- ...

This is standard and appropriate for transient provider pressure.

---

# 18. Delay capping differs depending on whether provider headers were present

Two caps exist:

- `RETRY_MAX_DELAY_NO_HEADERS = 30_000`
- `RETRY_MAX_DELAY = 2_147_483_647`

If there are no provider headers, the computed backoff is capped at 30 seconds.

`sleep(...)` also caps any requested wait to the max 32-bit timeout supported safely by `setTimeout`.

This is a nice distinction.

Local heuristics are constrained more tightly, while explicit provider-directed waits are allowed to be much longer if necessary.

---

# 19. `sleep(...)` is abort-aware

`SessionRetry.sleep(ms, signal)`:

- registers an abort listener
- clears the timeout on abort
- rejects with `AbortError`

This is important for long-running session behavior.

Retries must not trap the session in a non-cancelable sleep.

Abort-awareness keeps retry waiting aligned with the runtime’s broader cancellation model.

---

# 20. `SessionProcessor` is where retry policy becomes behavior

Inside the processor catch block, once an error has been normalized:

- `const retry = SessionRetry.retryable(error)`

If `retry` is defined, the processor:

- increments `attempt`
- computes `delay`
- sets session status to `retry`
- waits via `SessionRetry.sleep(...)`
- `continue`s the outer processing loop

This is the actual retry execution path.

---

# 21. Retry state is surfaced through `SessionStatus`

During retry, the processor publishes status:

- `type: "retry"`
- retry `attempt`
- human-readable `message`
- timestamp `next`

This is a very good runtime contract.

Retries are not hidden internal behavior.

They are surfaced as explicit session state for UIs or other observers.

---

# 22. Why surfacing `next` retry time is useful

By exposing `next: Date.now() + delay`, the runtime gives consumers enough information to show:

- countdowns
- retry scheduling state
- why the session appears temporarily paused

That improves observability and user trust during transient outages.

---

# 23. Retry preserves the same processor lifecycle context

When the processor retries, it does not create a new assistant message or return to the outer session loop first.

It stays inside the same processor `while (true)` lifecycle.

That means retries are considered part of the same logical assistant step attempt sequence rather than separate outer-loop turns.

This is an important semantic choice.

---

# 24. Why this is better than outer-loop retries

If retries happened outside the processor, the session loop would need to reason about partial stream state, incomplete parts, and retry counters.

Keeping retry local to the processor lets the stream-to-state engine own transient failure recovery.

That keeps responsibilities cleaner.

---

# 25. Retry does not apply to blocked or user-driven rejections

Permission rejections and question rejections affect `blocked` handling in the processor, not retry classification.

That is exactly right.

Those are not transient provider failures.

They are explicit control-flow outcomes driven by user or policy decisions.

So the runtime stops rather than retries.

---

# 26. Retry and compaction are intentionally separate escalation paths

The processor has two distinct special paths for failures:

- retry transient failures
- compact on context overflow

This is a very good example of root-cause-oriented branching.

Different classes of failure get different remedies.

OpenCode does not try to paper over all failure modes with one generic retry hammer.

---

# 27. A representative retry lifecycle

A typical retry lifecycle looks like this:

## 27.1 Provider call or stream fails

- raw error is thrown

## 27.2 Error is normalized

- `MessageV2.fromError(...)`

## 27.3 Retry classification runs

- `SessionRetry.retryable(error)` returns either `undefined` or a status string

## 27.4 If retryable, delay is computed

- provider headers first
- exponential backoff otherwise

## 27.5 Session status becomes `retry`

- attempt count and next retry timestamp are published

## 27.6 Sleep completes unless aborted

- processor restarts the stream-processing loop

This is the runtime’s transient-failure recovery loop.

---

# 28. Why this module matters architecturally

The retry layer shows that OpenCode distinguishes between:

- failures that need patience
- failures that need context transformation
- failures that need user intervention
- failures that are terminal

That distinction is essential in any robust agent runtime.

The code does this with a small, composable set of functions rather than burying policy inside one giant catch block.

That is good architecture.

---

# 29. Key design principles behind this module

## 29.1 Retry policy should act on typed, normalized runtime errors rather than arbitrary provider exceptions

So `MessageV2.fromError(...)` shapes failures before retry classification runs.

## 29.2 Retry should be reserved for transient failures, not structural failures

So overflow is excluded and routed toward compaction instead.

## 29.3 Provider-directed retry timing should take precedence over generic client heuristics

So `delay(...)` honors `retry-after-ms` and `retry-after` before local backoff.

## 29.4 Retries should be observable, cancelable, and scoped to the same logical assistant execution

So retry state is published to `SessionStatus`, waits are abort-aware, and the processor retries within the same execution loop.

---

# 30. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/session/retry.ts`
2. `packages/opencode/src/session/processor.ts`
3. `packages/opencode/src/session/message-v2.ts`

Focus on these functions and concepts:

- `SessionRetry.retryable()`
- `SessionRetry.delay()`
- `SessionRetry.sleep()`
- `MessageV2.fromError()`
- `MessageV2.APIError.isRetryable`
- processor retry branch
- `SessionStatus` with `type: "retry"`
- overflow exclusion from retry

---

# 31. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Should retry classification become more provider-specific over time rather than relying partly on generic JSON parsing fallbacks?
- **Question 2**: Should the runtime impose a maximum retry-attempt count for certain classes of retryable errors?
- **Question 3**: How should retry state interact with multi-client frontends that may reconnect while a session is sleeping between attempts?
- **Question 4**: Should quota or free-usage exhaustion really remain in the retry path, or should it transition into a more explicitly terminal billing state?
- **Question 5**: Are there cases where repeated retryable transport failures should eventually trigger a different remediation path, such as provider failover or explicit user guidance?
- **Question 6**: How should retry status be surfaced in UI to distinguish “waiting because overloaded” from “waiting because provider explicitly told us when to retry”?
- **Question 7**: What tests best validate the precedence order among `retry-after-ms`, numeric `retry-after`, date `retry-after`, and exponential backoff?
- **Question 8**: Should retry messaging be localized or standardized more strongly for different provider failure categories?

---

# 32. Summary

The `session_retry_delay_and_retryable_error_policy` layer is how OpenCode handles transient execution failures without confusing them with structural or user-driven failures:

- raw failures are normalized into typed `MessageV2` errors
- `SessionRetry.retryable()` decides whether the failure deserves retry and produces user-facing retry status text
- `SessionRetry.delay()` prefers provider retry headers and falls back to exponential backoff
- `SessionProcessor` publishes retry status, waits abortably, and retries within the same assistant execution lifecycle

So this module is the resilience policy layer that tells OpenCode when patience is the right response—and when it definitely is not.
