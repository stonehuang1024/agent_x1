# Session Status / Busy Retry Idle Semantics

---

# 1. Module Purpose

This document explains how OpenCode represents and publishes session execution status, focusing on the `SessionStatus` module and the main call sites that set:

- `busy`
- `retry`
- `idle`

The key questions are:

- What status states exist, and what data does each state carry?
- Where in the runtime are these statuses published?
- How do status transitions relate to the session loop, processor retries, cancellation, and terminal failures?
- Why is status state kept separate from message/part persistence?
- What does this layer reveal about how OpenCode exposes live execution progress to external observers?

Primary source files:

- `packages/opencode/src/session/status.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/processor.ts`

This layer is OpenCode’s **live session execution status publication layer**.

---

# 2. Why this layer matters

A session has two different kinds of state:

- durable conversational state in messages and parts
- live execution status for observers

`SessionStatus` is the second kind.

It tells the outside world whether a session is:

- actively working
- waiting to retry
- idle

This is important for UI responsiveness, orchestration visibility, and multi-client coordination.

---

# 3. `SessionStatus.Info` is a small tagged union

The status module defines three states:

- `idle`
- `retry`
- `busy`

`retry` also carries:

- `attempt`
- `message`
- `next`

This is a very compact model.

The runtime intentionally does not expose dozens of micro-states here.

It exposes a small set of high-value live states that are easy for observers to reason about.

---

# 4. Why `retry` is richer than `busy`

`busy` only needs to say:

- the session is actively working now

But `retry` means:

- work is paused temporarily for a specific reason
- the runtime knows when it will try again

So `retry` needs more metadata.

That is exactly why the union gives it structured fields instead of a plain label.

---

# 5. `SessionStatus` is instance-local state plus bus publication

The module keeps a local instance-backed state map from session ID to status.

But every `set(...)` also publishes:

- `SessionStatus.Event.Status`

This is an important design choice.

Status is both:

- queryable local state
- an event stream for observers

That supports both pull-style and push-style consumers.

---

# 6. `idle` is treated specially in storage

When `SessionStatus.set(sessionID, { type: "idle" })` runs, the module:

- publishes the status event
- publishes deprecated `session.idle`
- removes the session entry from local status state

So `idle` is represented by absence in the local state map rather than a permanently stored idle record.

That is a neat implementation detail.

---

# 7. Why idle-as-absence makes sense

For long-lived systems, most sessions are idle most of the time.

Not storing idle entries keeps the live state map smaller and makes “active status map” semantics more natural.

The `get(...)` method can still synthesize `idle` as the default.

That is a clean design.

---

# 8. `get(...)` returns `idle` by default

If a session has no stored active status, `get(sessionID)` returns:

- `{ type: "idle" }`

This means callers do not need to special-case missing entries.

The status API exposes a total view even though idle is internally represented by deletion.

---

# 9. `busy` is published at the outer session-loop level

In `session/prompt.ts`, the main loop begins each iteration with:

- `SessionStatus.set(sessionID, { type: "busy" })`

This is significant.

The session is marked busy not only when provider streaming starts, but already when the loop begins another cycle of active orchestration work.

That reflects a broader notion of `busy` than just “currently receiving tokens.”

---

# 10. `busy` is also published by the processor on stream start

Inside `SessionProcessor`, when the stream emits `start`, it also does:

- `SessionStatus.set(input.sessionID, { type: "busy" })`

This means busy may be reaffirmed at multiple layers.

That is acceptable because `busy` is idempotent in meaning.

It simply confirms the session remains actively executing.

---

# 11. Why both loop-level and processor-level busy signals exist

The outer loop may be doing meaningful work before the provider stream actually starts:

- scanning messages
- handling pending tasks
- building tools
- assembling prompts

The processor-level `busy` then marks actual stream execution.

So the repeated signal reflects two related active phases of the same overall session run.

---

# 12. `retry` is published only from the processor retry path

The grep results show `retry` status is set when:

- a retryable error occurs
- attempt count is incremented
- delay is computed

Then the processor publishes:

- `type: "retry"`
- `attempt`
- `message`
- `next`

This makes retry a specialized execution substate owned by the processor’s transient-failure handling.

---

# 13. Why retry belongs to processor-level state rather than loop-level state

Retry is a property of a failed model/tool execution attempt that will be retried within the same processor lifecycle.

The outer loop does not need to own that nuance.

Keeping retry publication inside the processor preserves a clean responsibility boundary.

---

# 14. `next` encodes when retry should happen

The processor computes:

- `next: Date.now() + delay`

This is useful because consumers can interpret retry state as:

- not currently busy
- not terminally idle
- expected to resume automatically at a known time

That makes retry a first-class waiting state instead of an opaque pause.

---

# 15. Terminal non-retry processor failures transition to idle

When the processor encounters a non-retryable, non-compaction error, it:

- records assistant error
- publishes session error event
- sets `SessionStatus` to `idle`

This is a key semantic rule.

A terminal execution failure still ends the live active run, so the session status becomes idle even though the conversation state now contains an error outcome.

---

# 16. Why `idle` does not mean success

This is an important distinction.

`idle` means:

- no active execution is currently running

It does **not** mean:

- the last execution succeeded

Success or failure is recorded in messages and parts.

Status only describes live execution liveness, not outcome quality.

---

# 17. Cancellation also transitions to idle

In `SessionPrompt.cancel(sessionID)`, the runtime:

- aborts the controller if present
- deletes the session from the internal busy-state map
- sets `SessionStatus` to `idle`

Even if no running state entry exists, it still sets idle.

This is a good normalization behavior.

Cancel always leaves the externally visible status in a known terminal live state.

---

# 18. Why cancel sets idle even when nothing was running

This makes the API easier to consume.

A cancel request produces a consistent result:

- after cancel, the session is idle

It avoids forcing callers to distinguish “already idle” from “just canceled to idle.”

---

# 19. The main loop uses deferred cancellation cleanup

The loop sets up:

- `using _ = defer(() => cancel(sessionID))`

This is very important.

It means the loop’s eventual exit path normally funnels through `cancel(...)`, which in turn publishes idle.

So idle is the default terminal live-status cleanup for a completed or aborted loop lifecycle.

---

# 20. Why idle publication is centralized through cancellation cleanup

Instead of scattering idle updates through every possible loop exit branch, OpenCode uses deferred cleanup to converge many exits into one status-normalization path.

That is good control-flow hygiene.

---

# 21. Status is intentionally separate from durable session progress

A session may be idle while still containing:

- incomplete pending subtasks
- a recent compaction summary
- tool errors
- assistant errors
- queued user messages waiting for future resumption

This is because status answers a different question:

- is the runtime actively executing right now?

not:

- what is the semantic state of the conversation?

This separation is very important.

---

# 22. Shell-triggered continuation fits this model naturally

The surrounding code shows shell-related resumption uses:

- `loop({ sessionID, resume_existing: true })`

Status semantics work naturally here because:

- the resumed loop will publish `busy`
- retries within it can publish `retry`
- final deferred cleanup still lands on `idle`

So status behavior remains consistent even when execution is resumed indirectly after shell activity.

---

# 23. `busy` is a coarse liveness indicator, not a detailed phase machine

The status model does not distinguish between:

- prompt assembly
- streaming tokens
- tool execution
- compaction
- summary generation

All of these are represented as `busy` unless the processor is specifically sleeping for retry.

This is a deliberate simplification.

The detailed execution trace lives elsewhere, especially in messages and parts.

---

# 24. Why a coarse status model is probably the right choice

Most observers want to know:

- Is the session doing work?
- Is it waiting to retry?
- Is it done for now?

They do not necessarily need every micro-phase reflected in the status channel.

Keeping the status model coarse avoids overfitting UI and orchestration logic to internal implementation details.

---

# 25. Status publication is event-first and state-second

`SessionStatus.set(...)` publishes the bus event before mutating local state.

That is worth noting.

The bus event is part of the primary contract, not just an afterthought.

This design reinforces that status is meant to be observed live, not only polled later.

---

# 26. Deprecated `session.idle` still exists for compatibility

When idle is set, the module also publishes:

- `SessionStatus.Event.Idle`

marked deprecated.

This shows the status layer evolved from a narrower idle-event model toward a more general status-event model.

That is useful architectural context when reading older integrations.

---

# 27. A representative status lifecycle

A typical lifecycle looks like this:

## 27.1 Session loop starts or resumes

- `busy` published

## 27.2 Processor stream starts

- `busy` may be reaffirmed

## 27.3 If transient provider failure occurs

- `retry` published with attempt, message, next retry timestamp

## 27.4 Retry sleep ends and execution resumes

- `busy` published again when work restarts

## 27.5 Execution completes, aborts, or fails terminally

- deferred cleanup or terminal branch publishes `idle`

This is the live execution-status lifecycle.

---

# 28. Why this module matters architecturally

The status layer gives OpenCode a lightweight but explicit control plane for live session execution.

It keeps liveness and waiting semantics visible without polluting durable conversation state with ephemeral runtime details.

That is especially important in a system with:

- resumable loops
- retries
- multi-step tool execution
- possible external observers via bus events

---

# 29. Key design principles behind this module

## 29.1 Live execution state should be observable separately from durable conversation state

So `SessionStatus` publishes `busy`, `retry`, and `idle` independently of messages and parts.

## 29.2 The status model should stay coarse unless richer distinctions are truly necessary

So the union is small and focused on high-value liveness semantics.

## 29.3 Terminal cleanup should normalize status consistently regardless of exit path

So loop cleanup and cancel behavior converge on `idle`.

## 29.4 Waiting states should be explicit and actionable for observers

So `retry` includes attempt count, message, and next retry time.

---

# 30. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/session/status.ts`
2. `packages/opencode/src/session/prompt.ts`
3. `packages/opencode/src/session/processor.ts`

Focus on these functions and concepts:

- `SessionStatus.Info`
- `SessionStatus.set()`
- `SessionStatus.get()`
- loop-level `busy`
- processor-level `busy`
- processor retry branch
- terminal error -> `idle`
- `cancel()` -> `idle`
- deprecated `session.idle` event

---

# 31. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Should compaction or shell-bridge execution ever get their own explicit status types, or is `busy` sufficient long-term?
- **Question 2**: Should there be a more explicit distinction between “waiting on user permission/question” and generic `busy` or `idle` states?
- **Question 3**: How should status state be persisted, if at all, for reconnecting clients that miss bus events?
- **Question 4**: Are there UI consumers still relying on deprecated `session.idle`, and when can that event be removed?
- **Question 5**: Should retry status include the underlying provider or error category in a more typed form?
- **Question 6**: How should multiple simultaneous observers reconcile status changes with message-stream updates to present a coherent UX?
- **Question 7**: Are there cases where nested or resumed loops could transiently publish misleading busy/idle transitions?
- **Question 8**: What tests best guarantee that every terminal path reliably lands on `idle` without leaving stale active status behind?

---

# 32. Summary

The `session_status_busy_retry_idle_semantics` layer is how OpenCode exposes the live liveness state of a session without confusing it with durable conversation outcomes:

- `busy` marks active orchestration or streaming work
- `retry` marks transient failure recovery with structured timing metadata
- `idle` marks the absence of active execution, whether after success, failure, cancellation, or cleanup
- bus publication and instance-local state make status observable both reactively and synchronously

So this module is the lightweight control-plane view of session execution state that sits alongside, but distinct from, the persistent message-and-part model.
