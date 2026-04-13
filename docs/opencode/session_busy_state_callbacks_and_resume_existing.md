# Session Busy State / Callbacks and `resume_existing`

---

# 1. Module Purpose

This document explains the in-memory coordination layer inside `SessionPrompt`, focusing on:

- the per-session busy-state map
- `assertNotBusy(...)`
- `start(...)`
- `resume(...)`
- queued callback waiters in `loop(...)`
- `resume_existing` semantics

The key questions are:

- How does OpenCode prevent multiple active runners from executing the same session concurrently?
- Why does the busy-state entry contain both an abort controller and a callback queue?
- What happens when `loop(...)` is called while a session is already running?
- How does `resume_existing: true` differ from a normal loop start?
- What does this coordination layer reveal about how OpenCode supports one active runner but many waiting consumers?

Primary source files:

- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/revert.ts`
- `packages/opencode/src/server/routes/session.ts`

This layer is OpenCode’s **single-runner session coordination and waiter-queue layer**.

---

# 2. Why this layer matters

The session runtime is resumable and multi-step, but it is not meant to have many independent concurrent loop runners for the same session.

Instead, OpenCode wants:

- at most one active runner per session
- shared waiting semantics for additional callers
- a clean way to resume an existing run when appropriate

The in-memory `state()` map in `SessionPrompt` is where this is enforced.

---

# 3. The coordination state is per session ID

`SessionPrompt` stores instance-local state shaped like:

- `abort: AbortController`
- `callbacks: { resolve, reject }[]`

for each active session ID.

This is a compact but powerful design.

A session’s live coordinator entry contains:

- how to cancel the active run
- who is waiting for its next non-user output

That is enough to serialize execution and multiplex waiting callers.

---

# 4. Why this state is in-memory rather than persisted

This layer tracks:

- live execution ownership
- active abort signal
- in-flight waiters

These are runtime coordination concerns, not durable conversation facts.

Persisting them would complicate correctness and recovery, especially across process restarts.

Keeping them instance-local is the right choice.

---

# 5. Instance teardown aborts all active sessions

The `Instance.state(...)` cleanup handler aborts every stored controller when the instance state is torn down.

This is an important safety behavior.

It ensures no active session run is left detached from the instance lifecycle.

So the busy-state layer participates in process-level cleanup, not only per-session orchestration.

---

# 6. `assertNotBusy(...)` is the hard exclusion guard

`assertNotBusy(sessionID)` simply checks whether there is an active state entry and throws `Session.BusyError` if so.

This is a deliberately strict helper.

It is used by operations that must not run while the session loop is active.

That creates a clear boundary between:

- operations that can wait or resume
- operations that must fail fast when the session is active

---

# 7. Why some operations use `assertNotBusy(...)` instead of queueing

The grep results show `SessionRevert.revert(...)` and `SessionRevert.unrevert(...)` calling `assertNotBusy(...)`.

This makes sense.

Revert-style operations mutate session/workspace state in ways that should not race with an active session runner.

Queueing them behind the runner would not necessarily preserve the right semantics.

So fail-fast is the safer policy.

---

# 8. `start(...)` claims the active-runner slot

`start(sessionID)`:

- returns nothing if the session already has a state entry
- otherwise creates a new `AbortController`
- stores `{ abort, callbacks: [] }`
- returns the controller’s signal

This is the core single-runner acquisition primitive.

Either you become the active runner, or you discover someone else already is.

---

# 9. Why `start(...)` returns an abort signal instead of the full state entry

The caller mainly needs:

- proof it acquired the run slot
- the abort signal for this execution

Returning only the signal keeps the acquisition API narrow and discourages callers from manipulating the shared state entry directly.

That is good encapsulation.

---

# 10. `resume(...)` does not create a new runner

`resume(sessionID)`:

- returns nothing if no active state entry exists
- otherwise returns the existing abort signal

This is crucial.

`resume_existing` is not a new independent start.

It is explicit reuse of the already-active coordinator entry.

That is how resumed paths avoid violating the single-runner rule.

---

# 11. Why resumed runs share the same abort signal

If resumption created a new controller, cancellation semantics would fragment.

By reusing the existing abort signal, all execution that belongs to the same active session run remains governed by one cancellation source.

That is the correct semantics.

---

# 12. `loop(...)` chooses between `start(...)` and `resume(...)`

At loop entry:

- normal calls use `start(sessionID)`
- `resume_existing` calls use `resume(sessionID)`

This is the main public decision point for session coordination.

The loop therefore supports two intentionally different invocation modes:

- claim a fresh active run
- join and continue an already-active run lifecycle

---

# 13. What happens when a normal `loop(...)` call arrives while the session is already running

If `start(...)` returns nothing, `loop(...)` does **not** throw.

Instead it returns a promise and pushes `{ resolve, reject }` onto:

- `state()[sessionID].callbacks`

This is very important.

Additional callers do not become parallel runners.

They become waiters for the existing runner’s next resolved output.

---

# 14. Why queued waiters are better than throwing for ordinary loop calls

In many user-facing contexts, a second caller likely wants:

- the result of the ongoing session work

not a concurrency error.

By queueing callbacks, OpenCode lets multiple consumers wait on the same active session execution without duplicating the work.

That is a strong coordination design.

---

# 15. The callback queue is specifically a non-user-output waiter list

At the end of the loop, the runtime iterates through `MessageV2.stream(sessionID)` and returns the first non-user item.

Before returning it, it resolves every queued callback with that item.

So the callback queue is really waiting for:

- the next assistant-side result artifact produced by the current active run

That is the actual contract.

---

# 16. Why callbacks resolve from streamed durable state rather than transient in-memory objects

The loop re-reads `MessageV2.stream(sessionID)` and resolves callbacks from that persisted stream result.

This is consistent with the wider state-first architecture.

Waiters do not receive an arbitrary mutable in-memory object.

They receive the durable artifact the session actually persisted.

That improves correctness and consistency.

---

# 17. `resume_existing` is used by shell-triggered continuation

The grep results show `shell(...)` using:

- `loop({ sessionID: input.sessionID, resume_existing: true })`

when callbacks are waiting after shell execution.

This is the clearest real-world use of resumed loop semantics.

The shell bridge does not start a second independent loop. It hands continuation back to the same active session coordination slot.

---

# 18. Why shell resumption depends on `resume_existing`

The shell bridge already claimed the session’s active slot with `start(...)`.

When it wants the main loop to continue afterward, it cannot call normal `loop(...)` start semantics again.

That would just see the session as busy.

`resume_existing` is exactly the mechanism that says:

- continue work inside the currently owned run slot

---

# 19. Deferred cleanup ensures the active-runner slot is released consistently

The loop uses:

- `defer(() => cancel(sessionID))`

When the active run ends, `cancel(...)`:

- aborts the controller
- deletes the state entry
- sets session status to idle

So releasing the busy-state slot is centralized and consistent.

That is important because stale in-memory ownership would break future session execution.

---

# 20. Why cancellation deletes the whole coordinator entry

The entry contains both:

- active abort controller
- queued callbacks

When the run is done, that entire live coordination context is obsolete.

Deleting the whole entry is simpler and safer than trying to partially reset pieces of it.

---

# 21. There is an intentional asymmetry between waiters and exclusive operations

Some APIs:

- wait behind the existing runner via callback queueing

Other APIs:

- throw `BusyError` through `assertNotBusy(...)`

This is not inconsistency.

It reflects two different semantic needs:

- consumers that want the result of ongoing work
- state-mutating operations that must not overlap active execution at all

That is a good distinction.

---

# 22. The busy-state layer is the true in-memory counterpart to durable session state

Durable messages and parts explain:

- what happened in the conversation

The busy-state map explains:

- who currently owns execution rights for this session
- who is waiting for its result
- how to cancel it

This division of responsibility is very clean.

---

# 23. A representative coordination lifecycle

A typical lifecycle looks like this:

## 23.1 First caller starts a loop

- `start(sessionID)` creates the coordinator entry
- caller becomes active runner

## 23.2 Second caller asks for loop result while the first is still running

- `start(sessionID)` returns nothing
- callback is queued
- second caller waits on promise resolution

## 23.3 Active runner finishes and re-reads persisted session stream

- first non-user item is found
- all queued callbacks are resolved with that durable item

## 23.4 Deferred cleanup runs

- `cancel(sessionID)` removes the coordinator entry
- session becomes idle again

## 23.5 A resumed path like shell continuation can reuse the active slot

- `resume_existing` returns the existing abort signal instead of trying to re-claim ownership

This is the core single-runner/many-waiters lifecycle.

---

# 24. Why this module matters architecturally

This layer is one of the clearest examples of OpenCode balancing:

- serialized execution correctness
- resumability
- multi-consumer access

It avoids parallel loop runners for the same session, but it still lets additional callers observe or await the same active run through queued callbacks.

That is a pragmatic and elegant coordination model.

---

# 25. Key design principles behind this module

## 25.1 A session should have at most one active runner at a time

So `start(...)` only succeeds once per active session entry and `assertNotBusy(...)` protects incompatible operations.

## 25.2 Additional consumers should usually wait on existing work rather than duplicate it

So normal `loop(...)` callers queue callbacks when a runner already exists.

## 25.3 Resumed execution should reuse the same live coordination context

So `resume_existing` returns the already-owned abort signal instead of creating a fresh run slot.

## 25.4 Live coordination state should remain ephemeral and be cleaned up centrally

So the state map is instance-local and deleted through `cancel(...)` at run end.

---

# 26. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/session/prompt.ts`
2. `assertNotBusy()`
3. `start()`
4. `resume()`
5. `loop()` entry and callback queue branch
6. loop final callback resolution block
7. `cancel()`
8. `packages/opencode/src/session/revert.ts`

Focus on these functions and concepts:

- instance `state()` map
- `assertNotBusy()`
- `start()`
- `resume()`
- `resume_existing`
- queued callback promises
- final `q.resolve(item)` behavior
- shell-triggered resumed loop continuation

---

# 27. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Should queued callbacks ever be rejected explicitly on certain failure paths rather than only resolved from final stream state?
- **Question 2**: How should this coordination model evolve if sessions ever support explicit branching or multiple concurrent active tracks?
- **Question 3**: Are there edge cases where `resume_existing` could be invoked after the coordinator entry was already cleaned up, and how should those be surfaced?
- **Question 4**: Should the callback queue expose richer semantics, such as multiple streamed intermediate updates rather than one final resolved item?
- **Question 5**: How should this in-memory coordination interact with multi-process or distributed deployments where instance-local state is insufficient?
- **Question 6**: Are there operations beyond revert/unrevert that should also use `assertNotBusy(...)` defensively?
- **Question 7**: Should the coordinator entry track more metadata, such as start time or owning execution mode, for better observability?
- **Question 8**: What tests best guarantee that coordinator entries are always cleaned up and do not leak across aborted or resumed runs?

---

# 28. Summary

The `session_busy_state_callbacks_and_resume_existing` layer is the in-memory coordination system that lets OpenCode run one active session executor while still serving multiple waiters:

- `start(...)` claims exclusive active-runner ownership for a session
- `assertNotBusy(...)` protects operations that must not overlap active execution
- ordinary extra loop callers queue callbacks instead of creating parallel runners
- `resume_existing` reuses the current active run slot and abort signal for continuation paths like shell resumption
- final callback resolution is based on persisted session output, not transient in-memory state

So this module is the concurrency-control layer that makes OpenCode’s resumable session runtime both serialized and shareable.
