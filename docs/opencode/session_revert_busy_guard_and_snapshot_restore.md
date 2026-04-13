# Session Revert / Busy Guard and Snapshot Restore

---

# 1. Module Purpose

This document explains how OpenCode supports reverting and unreverting session-visible work, focusing on:

- `SessionRevert.revert(...)`
- `SessionRevert.unrevert(...)`
- `SessionRevert.cleanup(...)`
- the use of `SessionPrompt.assertNotBusy(...)`
- snapshot revert/restore behavior

The key questions are:

- Why do revert and unrevert refuse to run while a session is active?
- How does OpenCode choose the message or part boundary to revert to?
- What role do patch parts and snapshots play in reverting workspace changes?
- How does `cleanup(...)` reconcile the durable message log with an active revert state?
- What does this layer reveal about how OpenCode separates reversible workspace state from persistent conversation history?

Primary source files:

- `packages/opencode/src/session/revert.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/summary.ts`

This layer is OpenCode’s **session revert orchestration and snapshot-restore boundary**.

---

# 2. Why this layer matters

OpenCode sessions are not only conversational.

They can also mutate the workspace.

That means users need a way to:

- roll back changes associated with later session work
- keep track of what point the session has effectively been rewound to
- potentially undo that rewind later

The revert module is how OpenCode expresses this capability in a structured, session-aware way.

---

# 3. Revert and unrevert are explicitly blocked while a session is busy

Both `revert(...)` and `unrevert(...)` begin with:

- `SessionPrompt.assertNotBusy(input.sessionID)`

This is one of the most important invariants in the module.

Revert-style operations are not allowed to race with the active session loop.

That is exactly the right policy.

---

# 4. Why the busy guard is essential here

Revert manipulates both:

- workspace snapshot state
- session-visible revert metadata

If a live loop or shell bridge were simultaneously making changes, the revert boundary could become incoherent immediately.

So fail-fast busy exclusion is the correct root-cause-oriented design.

---

# 5. Revert begins by loading the full session message history

`revert(...)` calls:

- `Session.messages({ sessionID })`

and scans the entire session history.

This is important because revert is defined relative to durable session history, not just the latest assistant turn.

It needs a full view to decide where the rewind boundary is and which patches lie after it.

---

# 6. Revert tracks the latest seen user message while scanning

During the scan, it maintains:

- `lastUser`

This is a subtle but critical piece of logic.

If the requested revert point ends up removing the effective useful parts of a message, the revert boundary may need to move back to the preceding user-turn boundary.

That reflects how conversation state is organized semantically.

---

# 7. Revert can target either a whole message or a specific part

The input allows:

- `messageID`
- optional `partID`

This means the revert system supports two granularities:

- rewinding from a message boundary
- rewinding from a part boundary inside a message

That is a powerful capability.

It recognizes that not every rewind decision maps neatly to whole-message deletion.

---

# 8. The scan decides the actual revert boundary dynamically

When the target message or part is encountered, the code checks whether earlier remaining parts in that message include useful types like:

- `text`
- `tool`

If not, it clears `partID` and moves the effective boundary to:

- the last user message if one exists
- otherwise the current message

This is a very important heuristic.

The runtime prefers a coherent revert boundary over a technically exact but semantically empty partial-message rewind.

---

# 9. Why useful-part detection matters

If reverting at a part boundary would leave a message with no meaningful content, the resulting session history would be awkward and misleading.

By collapsing to a higher message boundary when necessary, OpenCode preserves a cleaner conversational state after revert.

That is a principled design choice.

---

# 10. Patch parts after the revert point are collected for workspace rollback

Once the revert boundary is found, later `patch` parts are collected into:

- `patches`

This is a key bridge between conversation history and filesystem state.

The message log records patches as part artifacts, and the revert system uses those artifacts to determine what workspace changes must be undone.

That is exactly what an event-sourced coding-agent runtime should do.

---

# 11. Revert uses snapshots to establish a restorable pre-revert state

If a revert is actually going to happen, the module sets:

- `revert.snapshot = session.revert?.snapshot ?? (await Snapshot.track())`

This is very important.

Before applying the workspace rewind, OpenCode records or reuses a snapshot that can later be restored by `unrevert(...)`.

So revert is itself reversible.

---

# 12. Why the revert snapshot may be reused

If the session already had an active revert state, the code reuses the existing snapshot.

That means repeated revert operations while already in revert mode continue to refer back to the same saved pre-revert workspace state.

This is a thoughtful design.

It avoids creating a confusing stack of nested unrevert targets.

---

# 13. Workspace rollback is performed through `Snapshot.revert(patches)`

Once patches are collected, the module calls:

- `Snapshot.revert(patches)`

This shows that revert is fundamentally patch-driven with snapshot support for later restoration.

The workspace is rolled back according to the patch artifacts recorded after the revert boundary.

That ties message-state history and filesystem-state history together tightly.

---

# 14. Revert also computes a diff from the saved snapshot

After reverting, if a snapshot exists, the code computes:

- `revert.diff = await Snapshot.diff(revert.snapshot)`

This is important because the revert state records not just where the boundary is, but also what changed relative to the saved snapshot.

That likely helps UI or later logic explain the current revert state meaningfully.

---

# 15. Session-level diff metadata is recomputed after revert

The code then filters session messages from the new revert boundary onward and runs:

- `SessionSummary.computeDiff({ messages: rangeMessages })`

Then it writes the diff to storage and publishes `Session.Event.Diff`.

This is a very important integration point.

Revert does not leave session diff metadata stale.

It recalculates summary/diff state to reflect the post-revert reality.

---

# 16. Why recomputing diffs after revert is necessary

Without recomputation, the session’s reported additions/deletions/files counts could describe the pre-revert state rather than the current effective state.

That would make the session summary misleading.

So recomputing diffs is the correct consistency step.

---

# 17. `Session.setRevert(...)` persists both boundary and summary metadata

When revert succeeds, the module stores:

- the `revert` descriptor
- a summarized diff count object with additions, deletions, and files

This indicates revert state is a first-class session attribute, not merely a temporary hidden flag.

The rest of the runtime can observe that the session is currently in a reverted state.

---

# 18. `unrevert(...)` restores the saved snapshot and clears revert state

`unrevert(...)`:

- checks busy guard
- loads the session
- if no revert exists, returns unchanged
- restores `session.revert.snapshot` if present
- clears revert state

This is a clean inverse operation.

The core semantic is simple:

- restore the saved pre-revert workspace state
- remove the active revert marker

---

# 19. Why `unrevert(...)` does not need to reconstruct message history itself

`unrevert(...)` focuses on workspace restoration and clearing revert state.

The message-log reconciliation work is handled elsewhere, especially via `cleanup(...)` when new prompt or shell activity begins.

This separation keeps `unrevert(...)` small and focused.

---

# 20. `cleanup(...)` is how active revert state is materialized into history truncation

When prompting or running shell commands, the code first calls:

- `SessionRevert.cleanup(session)`

if `session.revert` exists.

This is a very important design choice.

A revert does not immediately rewrite or delete later session messages.

Instead, the revert state is stored, and cleanup is applied lazily when the session is about to continue.

---

# 21. Why lazy cleanup is useful

This lets the system separate two concerns:

- immediate workspace rollback and revert-state marking
- later history truncation when the session is ready to continue from the reverted point

That can make revert actions safer and easier to reason about.

The runtime first marks the session as reverted, then normalizes the history when resuming work.

---

# 22. `cleanup(...)` splits history into preserved and removed regions

It scans all messages and classifies them into:

- `preserve`
- `remove`
- possibly a `target` message when part-level revert is active

Messages before the revert message ID are preserved.

Messages after it are removed.

The revert target message may be preserved or removed depending on whether the revert is at message level or part level.

This is the true durable-history truncation phase.

---

# 23. Whole-message revert removes the target message too

If `session.revert.partID` is absent, the target message itself goes into `remove`.

That means message-level revert rewinds the conversation to before that message entirely.

This is semantically straightforward.

---

# 24. Part-level revert preserves the message but trims later parts

If `session.revert.partID` exists, the target message is preserved and later cleanup removes:

- the target part itself
- everything after it in that message

This is a sophisticated behavior.

It allows fine-grained rewind inside a single message while preserving earlier parts in that same message.

---

# 25. Message and part removals publish explicit bus events

During cleanup, removals publish:

- `MessageV2.Event.Removed`
- `MessageV2.Event.PartRemoved`

This is very important for observers.

History truncation is not a silent database mutation.

It is surfaced as explicit event activity.

---

# 26. Why cleanup directly deletes database rows

The cleanup path uses direct database deletion for messages and parts after it has decided what should be removed.

That means revert cleanup is an actual durable history rewrite at this stage, not merely a filter.

This is different from compaction, which keeps history and filters it from active context.

That boundary is worth noticing.

---

# 27. Why revert and compaction differ so much here

Compaction preserves old history and appends a summary boundary.

Revert cleanup actually removes later messages and parts from storage after the revert state is applied.

That makes sense because revert means:

- “we are undoing and discarding this later branch of work”

not merely:

- “we are compressing older context for continuation.”

They are fundamentally different operations.

---

# 28. A representative revert lifecycle

A typical lifecycle looks like this:

## 28.1 User requests revert to a message or part

- busy guard ensures no active run exists

## 28.2 Revert boundary and later patches are discovered

- scan full session history

## 28.3 Workspace is rolled back

- revert snapshot tracked or reused
- `Snapshot.revert(patches)` applied

## 28.4 Session revert state and diff summary are stored

- session now knows it is reverted

## 28.5 Next prompt or shell action triggers `cleanup(...)`

- later messages and/or parts are deleted
- revert state cleared

## 28.6 Optional unrevert can restore the saved snapshot before cleanup or further work

This is the full revert orchestration model.

---

# 29. Why this module matters architecturally

This layer shows that OpenCode treats revert as more than a git-like file rollback.

It is a session-aware operation that coordinates:

- workspace snapshots and patches
- message/part boundaries
- diff summary recomputation
- later durable history cleanup
- strict exclusion from concurrent active execution

That is a sophisticated design for an agent runtime that edits code and maintains a persistent conversational log.

---

# 30. Key design principles behind this module

## 30.1 Revert operations must never race with active session execution

So `assertNotBusy(...)` guards both revert and unrevert.

## 30.2 Workspace rollback and conversational rewind should be coordinated but can occur in phases

So revert sets snapshot-backed revert state first, and cleanup later truncates durable history.

## 30.3 Revert boundaries should preserve semantic conversation coherence, not just raw positional precision

So part-level revert can collapse to a higher message boundary if no useful parts would remain.

## 30.4 Session summaries and diff metadata must remain consistent with reverted state

So diffs are recomputed and republished after revert.

---

# 31. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/session/revert.ts`
2. `revert()`
3. `unrevert()`
4. `cleanup()`
5. `packages/opencode/src/session/prompt.ts`
6. `packages/opencode/src/session/summary.ts`

Focus on these functions and concepts:

- `SessionPrompt.assertNotBusy()`
- revert boundary detection
- useful-part heuristic for part-level revert
- `Snapshot.track()` / `Snapshot.revert()` / `Snapshot.restore()`
- `SessionSummary.computeDiff()` after revert
- `Session.setRevert()` / `Session.clearRevert()`
- cleanup message deletion vs part deletion

---

# 32. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Should revert state expose richer user-facing metadata explaining exactly which messages/parts were rewound and why the boundary was chosen?
- **Question 2**: Are there edge cases where the useful-part heuristic for part-level revert still leaves a confusing preserved message shape?
- **Question 3**: Should unrevert also trigger diff recomputation explicitly, or is that always handled elsewhere sufficiently?
- **Question 4**: How should revert semantics interact with compaction summaries if the reverted range crosses a compaction boundary?
- **Question 5**: What should happen if workspace snapshot restore succeeds but later session cleanup fails partially?
- **Question 6**: Should there be support for stacking multiple revert checkpoints instead of reusing one snapshot while in revert mode?
- **Question 7**: How should revert work in multi-client or distributed runtimes where busy-state guards are only instance-local?
- **Question 8**: What tests best guarantee that revert cleanup never leaves dangling message/part references after partial-message rewinds?

---

# 33. Summary

The `session_revert_busy_guard_and_snapshot_restore` layer is how OpenCode safely rewinds session-associated work without racing active execution:

- revert and unrevert are blocked while the session is busy
- revert identifies a coherent message/part boundary, rolls back later patches, records a reusable snapshot, and recomputes diff metadata
- unrevert restores the saved snapshot and clears revert state
- cleanup later materializes the revert into durable message/part deletion before new session activity proceeds

So this module is the boundary where workspace rollback, conversational rewind, and session-consistency rules come together.
