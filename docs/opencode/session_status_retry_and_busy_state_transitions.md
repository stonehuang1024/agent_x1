# Session Status / Retry / Busy-State Transitions

---

# 1. Module Purpose

This document explains how OpenCode tracks session execution state, exposes retry progress, prevents concurrent session mutation, and transitions between `busy`, `retry`, and `idle` states.

The key questions are:

- What exactly does `SessionStatus` represent?
- Why does OpenCode keep a separate status state machine instead of inferring status from messages?
- How are retryable model failures classified and delayed?
- How do `SessionPrompt`, `SessionProcessor`, and `SessionRetry` cooperate?
- When does a session become `busy`, `retry`, or `idle`?

Primary source files:

- `packages/opencode/src/session/status.ts`
- `packages/opencode/src/session/retry.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/index.ts`

This layer is OpenCode’s **runtime execution-status and retry coordination infrastructure**.

---

# 2. Why session status needs its own state machine

OpenCode cannot reliably derive runtime state only from persisted messages.

For example:

- a stream may be in progress before the assistant message is finalized
- a provider retry may be sleeping with no new parts written yet
- a session may be blocked from concurrent work before any new message appears
- abort and cleanup paths may need immediate UI updates

So the system maintains a separate in-memory status channel with explicit states.

---

# 3. `SessionStatus.Info`: the three status modes

`SessionStatus.Info` is a union of:

- `idle`
- `busy`
- `retry`

`retry` additionally carries:

- `attempt`
- `message`
- `next`

This shows that status is intentionally minimal.

It answers only:

- is work running right now?
- is the system waiting before a retry?
- or is the session idle?

---

# 4. Why `retry` is a first-class state instead of a `busy` subtype

A retry sleep is operationally different from active streaming.

During retry wait:

- no provider stream is currently producing output
- the user should still understand work is ongoing
- the UI can show countdown / next retry time
- cancellation still matters

So `retry` is not merely “busy but slower”.

It is a distinct execution mode.

---

# 5. `SessionStatus` storage model

`status.ts` uses `Instance.state()` to hold:

- an in-memory record keyed by `sessionID`

`get(sessionID)` defaults to:

- `{ type: "idle" }`

`set(sessionID, status)`:

- publishes `session.status`
- removes the entry entirely when status becomes `idle`

This is important: `idle` is represented by absence in state, not by permanently storing idle rows.

---

# 6. Why `idle` deletes state instead of storing it

This keeps the runtime state table small and naturally scoped to active sessions.

Only sessions that are:

- currently streaming
- retrying
- otherwise active

occupy memory.

That also makes `list()` naturally return the active status set.

---

# 7. Status events

`SessionStatus.Event` includes:

- `session.status`
- deprecated `session.idle`

`session.status` is the real event stream now.

This means consumers should treat session status as an explicit evented runtime signal, not something they must infer by polling messages.

---

# 8. Where `busy` is set in normal execution

There are two main places where a session becomes `busy`:

## 8.1 In `SessionPrompt.loop()`

At the top of each loop iteration:

- `SessionStatus.set(sessionID, { type: "busy" })`

This marks the session as active before orchestration proceeds.

## 8.2 In `SessionProcessor.process()` on stream start

When the provider emits `start`:

- `SessionStatus.set(input.sessionID, { type: "busy" })`

This reinforces that actual model streaming has started.

---

# 9. Why both loop and processor set `busy`

They cover slightly different layers:

- `SessionPrompt.loop()` marks the orchestration cycle as active
- `SessionProcessor` marks the underlying provider stream as active

This duplication is acceptable because `busy` is idempotent and cheap.

It ensures status stays correct even if work shifts between orchestration and stream handling phases.

---

# 10. Busy-state gating: `assertNotBusy()` and `start()`

In `prompt.ts`, OpenCode maintains a separate execution-control state for running loops.

Two key functions are:

- `assertNotBusy(sessionID)`
- `start(sessionID)`

If a session already has active loop state, calls such as:

- `prompt(...)`
- `shell(...)`
- `revert(...)`

can fail with:

- `Session.BusyError`

This means “busy” is enforced in two related but different ways:

- runtime status events for UI/observers
- loop-state locking for mutation safety

---

# 11. `Session.BusyError`

`session/index.ts` defines:

- `BusyError(sessionID)` -> `Session <id> is busy`

This is the hard guard against concurrent session execution.

It is not just informational.

It actively prevents overlapping prompt loops or shell operations from mutating the same session simultaneously.

---

# 12. Why OpenCode needs both status and locking

Status alone is not enough.

If the system only emitted `busy` but did not enforce exclusivity:

- two prompt loops could run concurrently
- tool parts could interleave incorrectly
- revert/cleanup could race with active execution

So the design separates:

- **status visibility**
- **execution exclusivity**

That is the correct architecture.

---

# 13. When `idle` is set from prompt control flow

In `prompt.ts`, `cancel(sessionID)` will:

- abort the active loop state
- delete the loop state entry
- `SessionStatus.set(sessionID, { type: "idle" })`

Also, if `cancel()` is called on a session with no active state, it still sets:

- `idle`

This ensures UI consumers are always driven back to a clean terminal state.

---

# 14. Why `cancel()` forces `idle`

Aborting a session is a top-level runtime fact.

Even if deeper layers have not yet finished all cleanup, the user-facing session should stop appearing active.

So `cancel()` immediately drives the external status to `idle`.

---

# 15. Retry classification lives in `SessionRetry.retryable()`

`retry.ts` decides whether an error should trigger retry and what user-facing message should be shown.

It returns either:

- a retry message string
- or `undefined` for non-retryable errors

This is an important choice.

The function does not just answer “retry or not”.

It also provides the status message that `SessionStatus` will expose.

---

# 16. `ContextOverflowError` is explicitly not retryable

The first rule in `retryable()` is:

- if `MessageV2.ContextOverflowError.isInstance(error)` -> `undefined`

This is correct.

Context overflow is not a transient transport failure.

Retrying the exact same request would almost certainly fail again.

The correct response is compaction, not retry.

---

# 17. Retryable API errors

If the error is `MessageV2.APIError`:

- `isRetryable` must be true
- otherwise no retry

Then it produces a message such as:

- `Free usage exceeded, add credits https://opencode.ai/zen`
- `Provider is overloaded`
- or the provider’s own message

This means retry classification already includes user-facing explanation, not just machine logic.

---

# 18. Non-API retryable error parsing

For some errors, `retryable()` tries to parse `error.data.message` as JSON and inspect fields like:

- `json.type`
- `json.error.type`
- `json.error.code`
- `json.code`

It recognizes patterns such as:

- `too_many_requests`
- `rate_limit`
- `exhausted`
- `unavailable`

This shows the system has accumulated provider-specific retry heuristics beyond the normalized `APIError` shape.

---

# 19. Why fallback JSON parsing exists

Not every provider or proxy returns perfectly normalized structured errors.

Some failures arrive as stringified JSON embedded inside a generic error object.

Instead of giving up, OpenCode tries to salvage retry semantics from the payload.

That is a pragmatic compatibility strategy.

---

# 20. Delay calculation: `SessionRetry.delay()`

`delay(attempt, error?)` uses the following priority:

## 20.1 Honor response headers when available

From `responseHeaders`:

- `retry-after-ms`
- `retry-after`
  - numeric seconds
  - or HTTP date format

## 20.2 Otherwise exponential backoff

- `RETRY_INITIAL_DELAY = 2000`
- `RETRY_BACKOFF_FACTOR = 2`
- capped by `RETRY_MAX_DELAY_NO_HEADERS = 30000`

## 20.3 Sleep hard cap

`setTimeout` is capped by:

- `RETRY_MAX_DELAY = 2_147_483_647`

This is careful and robust.

---

# 21. Why provider headers take precedence

If a provider explicitly communicates retry timing, that is better than a client-side guess.

Using `retry-after-ms` or `retry-after`:

- reduces unnecessary pressure on the provider
- improves success probability
- keeps behavior aligned with backend throttling policy

This is the right default.

---

# 22. `SessionRetry.sleep()` is abort-aware

`sleep(ms, signal)`:

- starts a timeout
- registers an abort handler
- clears the timer and rejects with `AbortError` if aborted

This matters because retry waiting is still part of active execution.

The user must be able to cancel during backoff, not only during a live stream.

---

# 23. How processor enters retry state

In `SessionProcessor.process()` catch block:

- `const retry = SessionRetry.retryable(error)`
- if defined:
  - `attempt++`
  - `const delay = SessionRetry.delay(attempt, apiError?)`
  - `SessionStatus.set(sessionID, { type: "retry", attempt, message: retry, next: Date.now() + delay })`
  - `await SessionRetry.sleep(delay, abort)`
  - `continue`

So retry is a full loop transition, not an ad hoc re-call.

---

# 24. Why `retry` stores `next` as absolute time

`next` is stored as:

- `Date.now() + delay`

rather than only storing `delay`.

That is better for consumers because they can compute:

- countdowns
- ETA displays
- stale status correction

without needing to know when the state was emitted.

---

# 25. Retry preserves the same assistant message lifecycle

When processor retries, it does not create a new assistant message.

Instead, it re-enters the `while (true)` loop for the same bound `assistantMessage`.

This is important because all retries belong to the same logical assistant turn.

Otherwise message history would fragment into multiple partial assistant attempts.

---

# 26. What happens when retry is not possible

If the error is not retryable:

- `input.assistantMessage.error = error`
- `Bus.publish(Session.Event.Error, ...)`
- `SessionStatus.set(sessionID, { type: "idle" })`

So terminal failures drive the session back to idle immediately.

This is the clean failure path.

---

# 27. Why processor explicitly sets `idle` only on fatal error

In the processor itself, explicit `idle` is set when a fatal non-retryable error occurs.

For successful completion or some other exit paths, the broader loop/cancel machinery is responsible for returning the session to idle.

This suggests a layered responsibility split:

- processor handles fatal-error idle transition directly
- loop lifecycle and cancellation handle the rest

---

# 28. Interaction between `blocked` and session status

When a tool error is caused by:

- `PermissionNext.RejectedError`
- `Question.RejectedError`

processor may set:

- `blocked = shouldBreak`

That affects the final processor result (`stop`), but it is not represented as a dedicated `SessionStatus` variant.

So OpenCode currently treats blocked execution as:

- a terminal control-flow outcome
- not a separately observable status mode

That is a meaningful design choice.

---

# 29. Why there is no explicit `stopped` or `blocked` status

The current status model is intentionally small.

It tracks only active runtime phases:

- busy
- retry
- idle

Terminal outcomes such as:

- blocked by user rejection
- completed normally
- failed terminally

are represented elsewhere through:

- message state
- error parts
- session events
- loop termination

This keeps status simple, though it may hide some nuance from the UI.

---

# 30. A complete state transition sequence

A typical successful run looks like:

## 30.1 Start orchestration

- `SessionPrompt.loop()` -> `busy`

## 30.2 Provider stream starts

- `SessionProcessor` receives `start` -> `busy`

## 30.3 Stream completes normally

- assistant message finalizes
- loop exits or continues
- outer control path eventually calls `idle`

A retrying run looks like:

## 30.4 Stream fails transiently

- processor catch -> retryable
- `SessionStatus = retry`
- sleep
- loop restarts stream
- back to `busy`

A terminal error run looks like:

## 30.5 Stream fails terminally

- processor catch -> non-retryable
- assistant error set
- `SessionStatus = idle`

A canceled run looks like:

## 30.6 Cancel

- `cancel(sessionID)` aborts state
- status forced to `idle`

---

# 31. Relationship to `shell()` and other entry points

`shell(input)` also uses `start(sessionID)` and throws `Session.BusyError` if the session is already active.

This shows the busy-state lock is not specific to model prompting.

It protects any execution path that mutates session runtime state.

---

# 32. Why status is per-session, not global

Each session can independently be:

- active
- retrying
- idle

That allows OpenCode to support multiple concurrent sessions without conflating their runtime state.

This is the obvious but important design choice for a multi-session IDE workflow.

---

# 33. Key design principles behind this module

## 33.1 Execution visibility and execution locking are separate concerns

So OpenCode keeps both `SessionStatus` and prompt-loop busy guards.

## 33.2 Retry is a real runtime phase, not an implementation detail

So it gets its own status variant with countdown metadata.

## 33.3 Retry logic should honor provider guidance before using client heuristics

So `retry-after-ms` and `retry-after` are preferred.

## 33.4 Context overflow should be handled structurally, not retried blindly

So it routes to compaction instead of retry.

---

# 34. Recommended reading order

To continue digging deeper, read in this order:

1. `packages/opencode/src/session/status.ts`
2. `packages/opencode/src/session/retry.ts`
3. `packages/opencode/src/session/processor.ts`
4. `packages/opencode/src/session/prompt.ts`
5. `packages/opencode/src/session/index.ts`

Focus on these functions and concepts:

- `SessionStatus.get()`
- `SessionStatus.set()`
- `SessionRetry.retryable()`
- `SessionRetry.delay()`
- `SessionRetry.sleep()`
- `Session.BusyError`
- `assertNotBusy()`
- `cancel()`

---

# 35. Open questions for further investigation

There are still several useful follow-up questions:

- **Question 1**: Which exact upper-layer path is responsible for setting `idle` after a fully successful normal prompt loop, and is that flow completely consistent across all exits?
- **Question 2**: Would a dedicated `blocked` status improve UX when permission or question rejection halts a session?
- **Question 3**: Should retry state expose more metadata, such as provider ID or error category, for better diagnostics?
- **Question 4**: How do front-end consumers render `session.status` versus message-level errors today?
- **Question 5**: Are there any race conditions between `cancel()` forcing `idle` and deeper cleanup still emitting later events?
- **Question 6**: Should long-running tool execution without active model streaming also have a distinct visible status beyond `busy`?
- **Question 7**: How should automation/CLI entry points surface retry countdowns when there is no persistent UI?
- **Question 8**: Is the current retry classification broad enough for all supported providers and proxies, especially custom gateways?

---

# 36. Summary

The `session_status_retry_and_busy_state_transitions` layer defines how OpenCode makes runtime execution visible, safe, and resilient:

- `SessionStatus` exposes a compact per-session state machine with `busy`, `retry`, and `idle`
- `SessionRetry` classifies transient failures, computes provider-aware backoff, and sleeps in an abort-aware way
- `SessionProcessor` drives retry transitions during model execution
- `SessionPrompt` and related entry points enforce mutual exclusion with `BusyError`

So this is not just a cosmetic status indicator. It is the coordination layer that keeps session execution observable, cancelable, and concurrency-safe while retrying transient failures intelligently.
