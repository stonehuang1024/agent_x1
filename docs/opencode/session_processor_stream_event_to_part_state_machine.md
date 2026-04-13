# Session Processor / Stream Event to Part State Machine

---

# 1. Module Purpose

This document explains how `SessionProcessor` converts streamed model/runtime events into durable assistant message state and persisted `MessageV2.Part` records.

The key questions are:

- Why does OpenCode need a dedicated processor between `LLM.stream(...)` and session persistence?
- How are streamed events mapped into text parts, reasoning parts, tool parts, step markers, patches, and errors?
- How does the processor manage retries, blocked tool executions, doom-loop detection, and compaction escalation?
- Why are snapshots and patch generation embedded into the same event-processing pipeline?
- What return values from `processor.process(...)` mean to the outer session loop?

Primary source files:

- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/compaction.ts`
- `packages/opencode/src/session/summary.ts`

This layer is OpenCode’s **stream-to-state assistant execution state machine**.

---

# 2. Why this layer matters

The session loop decides *when* an assistant turn should run.

But `SessionProcessor` decides how a streamed model execution becomes durable state.

That includes:

- partial text streaming
- reasoning streaming
- tool call lifecycle tracking
- usage and finish metadata
- snapshot and patch capture
- retry and error behavior
- compaction escalation

So this module is the main bridge between provider stream semantics and OpenCode’s persistent message-part model.

---

# 3. `SessionProcessor.create(...)` binds one processor to one assistant message

The processor is created with:

- `assistantMessage`
- `sessionID`
- `model`
- `abort`

This is important.

A processor instance is scoped to a single assistant message lineage.

It is not a generic shared stream parser.

That makes the resulting part updates easy to anchor to one durable assistant message ID.

---

# 4. The processor maintains local execution state beyond the persisted message

Inside `create(...)`, it keeps several in-memory structures:

- `toolcalls` map from tool call ID to tool part
- `snapshot`
- `blocked`
- `attempt`
- `needsCompaction`

This is the temporary control state used while translating the stream into persistent state.

It complements, but does not replace, the durable session model.

---

# 5. `process(...)` is a retrying outer loop around stream consumption

The processor does not consume the stream only once.

It wraps consumption in `while (true)` and can restart when retryable errors occur.

This is very important.

Retries are part of the processor contract, not something bolted on externally.

So stream-to-state persistence and retry policy are tightly integrated.

---

# 6. `SessionProcessor` is invoked from both normal loop execution and compaction

The call sites show it is used from:

- normal assistant execution in `session/prompt.ts`
- compaction summary generation in `session/compaction.ts`

That means the processor is the common execution engine for both ordinary assistant turns and compaction assistant turns.

This increases its architectural importance.

---

# 7. `start` events only update status, not message content

When the stream emits `start`, the processor sets:

- `SessionStatus` to `busy`

This is a small but important detail.

The processor distinguishes between:

- execution lifecycle/status events
- content or artifact events that deserve persisted parts

Not every stream event becomes a message part.

---

# 8. Reasoning events are materialized as first-class reasoning parts

The processor handles:

- `reasoning-start`
- `reasoning-delta`
- `reasoning-end`

For each reasoning stream ID, it creates a `reasoning` part, appends deltas to it, updates metadata, and finalizes timestamps on end.

This is significant because reasoning is not flattened into plain text.

It is preserved as its own typed part stream.

---

# 9. Why reasoning uses a map keyed by stream ID

Multiple reasoning spans can exist over the lifetime of a stream.

By indexing them in `reasoningMap[value.id]`, the processor can update the correct persisted part incrementally.

That is a clean event-correlation design.

---

# 10. Delta persistence is used during streaming, not full rewrites only

For reasoning and text, the processor uses:

- `Session.updatePartDelta(...)`

while deltas stream in.

This is an important operational detail.

The runtime persists incremental text growth as deltas rather than rewriting the whole part content on every token.

That is much better for streaming UIs and durable progressive updates.

---

# 11. Tool calls begin as `pending` tool parts

On `tool-input-start`, the processor creates or updates a tool part with:

- `status: "pending"`
- empty `input`
- empty `raw`

This is the first stage of the tool lifecycle.

The runtime records that tool planning has started even before a concrete executable call payload is finalized.

---

# 12. Why tool lifecycle has multiple persisted states

The processor later transitions tool parts through:

- `pending`
- `running`
- `completed`
- `error`

This gives OpenCode a real state machine for tool execution rather than a single monolithic event.

That is important for observability, interruption handling, and recovery.

---

# 13. `tool-call` turns a pending tool part into a running one

When a `tool-call` event arrives, the processor updates the matching tool part to:

- `status: "running"`
- concrete parsed `input`
- start time
- provider metadata

This is when the tool becomes an actual execution request in durable state.

So the `tool-call` event is the transition from intention to execution.

---

# 14. Doom-loop detection is embedded at tool-call time

After updating the running tool part, the processor loads recent parts and checks the last three.

If the last three are the same tool with the same input and non-pending status, it triggers:

- `PermissionNext.ask(...)` for `doom_loop`

This is a very important safety behavior.

The processor is not just a serializer. It also enforces anti-loop safeguards based on the exact persisted tool-call pattern.

---

# 15. Why doom-loop detection belongs here

The processor sees the real ordered tool part stream as it is being persisted.

That makes it the ideal place to detect pathological repeated tool execution patterns.

If this logic were higher up, it would be harder to correlate exact repeated tool-call state transitions.

---

# 16. `tool-result` completes the running tool part

On `tool-result`, the processor updates the matching tool part to:

- `status: "completed"`
- final `input`
- `output`
- `metadata`
- `title`
- `attachments`
- end time

Then it removes the call from the in-memory `toolcalls` map.

This is the successful terminal state for a tool-part lifecycle.

---

# 17. `tool-error` produces an error terminal state and may block continuation

On `tool-error`, the processor updates the running tool part to:

- `status: "error"`
- final `input`
- stringified `error`
- start/end time

If the error is a `PermissionNext.RejectedError` or `Question.RejectedError`, it may also set:

- `blocked = shouldBreak`

This is a subtle but crucial control-flow rule.

Some tool failures are ordinary tool failures.

Others indicate the loop should stop because the user denied or rejected something important.

---

# 18. Why blocked-state handling belongs in the processor

The blocked state arises from concrete streamed tool-execution outcomes.

Since the processor owns tool lifecycle translation, it is also the correct place to translate certain tool errors into higher-level stop behavior.

---

# 19. Step boundaries are captured with snapshots

On `start-step`, the processor:

- calls `Snapshot.track()`
- stores the snapshot ID
- writes a `step-start` part

On `finish-step`, it:

- computes usage/cost/tokens
- writes a `step-finish` part with finish reason and a new snapshot

This is very important.

Assistant execution is segmented into discrete steps with filesystem snapshot boundaries attached.

That is what later enables diff summaries and patch generation.

---

# 20. Why step markers are first-class parts

By persisting `step-start` and `step-finish` as parts, the runtime can later reconstruct:

- when each step happened
- what it cost
- what tokens it used
- what snapshot boundaries it spanned

This is far richer than storing only a final assistant message string.

---

# 21. Finish-step updates assistant-level usage and finish reason

On `finish-step`, the processor updates `input.assistantMessage` with:

- `finish`
- cumulative `cost`
- `tokens`

and persists the updated assistant message.

This means step completion updates both:

- a step-finish part
- the assistant message summary fields themselves

So assistant-level metadata and part-level execution trace evolve together.

---

# 22. Patch generation happens immediately after a completed step when snapshots differ

If a step began with a tracked snapshot, the processor computes:

- `Snapshot.patch(snapshot)`

If files changed, it writes a `patch` part with:

- patch hash
- changed files

This is a major architectural feature.

The assistant execution trace directly records code-change artifacts as typed parts tied to the assistant message.

---

# 23. Why patch generation is processor-local

The processor is where the runtime knows:

- a step started
- a step finished
- the relevant snapshot boundary

That makes it the right place to produce patch artifacts.

This keeps filesystem-change tracking tightly aligned with streamed execution boundaries.

---

# 24. Session summary enrichment is triggered at finish-step time

After writing step-finish and any patch part, the processor triggers:

- `SessionSummary.summarize(...)`

for the assistant message’s parent user message.

This is another sign that the processor owns the moment when an execution step becomes stable enough to summarize.

The summary layer depends on the processor’s step finalization events.

---

# 25. Compaction escalation is detected at finish-step time too

Still within `finish-step`, the processor checks:

- assistant is not already a summary turn
- `SessionCompaction.isOverflow(...)`

If overflow is true, it sets:

- `needsCompaction = true`

This is one of the most important control signals the processor emits.

It means a successful step may still be too large to continue, so the processor asks the outer loop to compact next.

---

# 26. Text streaming is materialized symmetrically to reasoning streaming

For normal assistant text, the processor handles:

- `text-start`
- `text-delta`
- `text-end`

It creates a `text` part, persists deltas incrementally, applies a plugin hook on completion, and finalizes timing/metadata.

This is the normal streamed assistant-output path.

---

# 27. `experimental.text.complete` is a final text normalization hook

At `text-end`, the processor triggers:

- `Plugin.trigger("experimental.text.complete", ...)`

with the completed text and allows plugins to alter it before the final part update.

This is a powerful postprocessing seam.

It means final text persistence is still extensible even after streaming has completed.

---

# 28. Non-content `finish` events do not directly persist parts

The plain `finish` event is effectively ignored in part persistence.

That is revealing.

The processor treats `finish-step` as the meaningful execution boundary, not the generic stream-finished event alone.

This reinforces the idea that the assistant turn is modeled as step-based state, not just one opaque provider response.

---

# 29. Errors split into compaction-worthy, retryable, and terminal categories

In the catch block, the processor converts the thrown error with:

- `MessageV2.fromError(...)`

Then it branches:

- context overflow -> `needsCompaction = true`
- retryable error -> update retry status, sleep, restart loop
- otherwise -> persist assistant error and publish session error event

This is a carefully layered failure model.

Not all failures mean the same thing operationally.

---

# 30. Retry behavior is part of the processor contract

For retryable errors, the processor:

- increments `attempt`
- computes delay through `SessionRetry.delay(...)`
- sets `SessionStatus` to `retry`
- sleeps through `SessionRetry.sleep(...)`
- restarts the outer processing loop

This means retry state is durable enough for observers and tightly integrated with the same processor lifecycle.

---

# 31. Why compaction-worthy errors are treated differently from retryable errors

Context overflow is not a transient provider glitch.

It usually requires changing the conversation state shape.

So the processor does not retry blindly.

Instead, it escalates outward via `needsCompaction` so the outer orchestration can change strategy.

That is the right root-cause treatment.

---

# 32. Cleanup after stream termination is explicit and conservative

After the main try/catch section, the processor performs cleanup:

- if a snapshot is still open, it computes and persists any patch
- any tool parts not completed or errored are marked `error` with `Tool execution aborted`
- assistant completion time is set
- assistant message is persisted

This is very important.

Even aborted or exceptional exits are normalized into coherent durable state.

The processor tries hard not to leave dangling half-open artifacts behind.

---

# 33. Final return values are high-level control signals for the outer loop

At the end, `process(...)` returns one of:

- `"compact"`
- `"stop"`
- `"continue"`

These are not provider statuses.

They are orchestration-level decisions derived from the entire event-processing lifecycle.

This is how the processor communicates back to `session/prompt.ts` or compaction orchestration.

---

# 34. Meaning of each processor return value

- `compact`: execution succeeded or failed in a way that requires context compaction before further progress
- `stop`: the run should stop because it was blocked or errored
- `continue`: the run finished normally and outer orchestration may decide the next loop step

This is the processor’s contract with the higher-level session loop.

---

# 35. A representative execution lifecycle

A typical assistant turn looks like this:

## 35.1 Processor is created for a fresh assistant message

- scoped to session, model, abort signal

## 35.2 Stream begins

- status becomes busy
- text/reasoning/tool/step events are persisted incrementally as parts

## 35.3 Tool calls may run, complete, fail, or trigger doom-loop permission checks

- tool part state machine advances in durable storage

## 35.4 Step finishes

- usage, finish reason, snapshots, patches, and summaries update
- overflow may request compaction

## 35.5 Cleanup runs

- dangling tools are marked aborted
- assistant completion time is set

## 35.6 High-level result is returned

- `continue`, `stop`, or `compact`

This is the actual stream-to-state lifecycle.

---

# 36. Why this module matters architecturally

`SessionProcessor` is one of the most important modules in the runtime because it converts a messy asynchronous event stream into a typed, durable execution trace.

It is where OpenCode unifies:

- streaming UX
- persistent assistant state
- tool orchestration state
- filesystem change tracking
- retries and permission stops
- compaction escalation

Without this layer, the session loop would have to manage far too many low-level protocol details itself.

---

# 37. Key design principles behind this module

## 37.1 Streamed execution should be persisted as typed incremental state, not just collapsed into one final response string

So the processor writes text, reasoning, tool, step, and patch parts as the stream unfolds.

## 37.2 Orchestration decisions should emerge from concrete persisted execution state and classified errors

So retries, blocked stops, and compaction escalation are derived inside the processor from real event outcomes.

## 37.3 Filesystem effects should be aligned with assistant step boundaries

So snapshots and patch parts are recorded at `start-step` / `finish-step` boundaries.

## 37.4 Cleanup must normalize partial execution into coherent terminal state

So dangling tools are marked aborted and the assistant message is always finalized before returning.

---

# 38. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/session/processor.ts`
2. `packages/opencode/src/session/prompt.ts`
3. `packages/opencode/src/session/compaction.ts`
4. `packages/opencode/src/session/summary.ts`

Focus on these functions and concepts:

- `SessionProcessor.create()`
- `process()`
- `reasoning-start/delta/end`
- `tool-input-start`, `tool-call`, `tool-result`, `tool-error`
- doom-loop detection
- `start-step` / `finish-step`
- patch creation
- `SessionSummary.summarize()` trigger point
- `SessionCompaction.isOverflow()` trigger point
- final `compact` / `stop` / `continue` returns

---

# 39. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Should more streamed provider event types be materialized into typed parts as providers add richer capabilities?
- **Question 2**: How should the processor evolve if providers begin streaming structured output or tool-plan metadata more explicitly?
- **Question 3**: Are there edge cases where repeated retries could interact awkwardly with partially persisted reasoning or text parts?
- **Question 4**: Should doom-loop detection become more semantic than exact tool-name plus exact-input repetition?
- **Question 5**: How should snapshot and patch capture behave for tools that cause large but intentionally transient workspace changes?
- **Question 6**: Are there cases where `text-end` should preserve original timing start rather than resetting `start` before `end` in the final part update?
- **Question 7**: What tests best validate that cleanup always leaves tool parts in coherent terminal states after aborts or failures?
- **Question 8**: Should blocked tool errors and question rejections produce more explicit typed assistant-state markers beyond the current stop behavior?

---

# 40. Summary

The `session_processor_stream_event_to_part_state_machine` layer is the execution engine that turns model and tool stream events into durable assistant state:

- reasoning, text, tool, step, and patch artifacts are persisted incrementally as typed parts
- retries, blocked states, doom-loop checks, and overflow escalation are handled inside the processor
- step completion updates assistant usage metadata and triggers diff summaries and possible compaction
- cleanup normalizes partial execution before returning orchestration-level results

So this module is the core stream-to-state state machine that makes OpenCode’s assistant execution observable, resumable, and controllable.
