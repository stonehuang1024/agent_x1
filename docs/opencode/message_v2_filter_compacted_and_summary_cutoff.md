# MessageV2 / Filter Compacted and Summary Cutoff

---

# 1. Module Purpose

This document explains how `MessageV2` defines the effective conversation history after compaction, focusing on:

- `filterCompacted(...)`
- `toModelMessages(...)`
- the interaction between compaction user parts and summary assistant messages

The key questions are:

- How does OpenCode decide which prefix of session history is still relevant after compaction has succeeded?
- Why does the message layer stop at a user compaction marker once a completed summary exists?
- How are compacted histories converted into provider-facing model messages?
- Why are compaction and subtask parts converted into textual stand-ins instead of preserved verbatim?
- What does this layer reveal about how OpenCode turns a long event log into an effective current working context?

Primary source files:

- `packages/opencode/src/session/message-v2.ts`
- `packages/opencode/src/session/compaction.ts`
- `packages/opencode/src/session/prompt.ts`

This layer is OpenCode’s **effective-history cutoff and model-message projection layer**.

---

# 2. Why this layer matters

The session database stores a long-lived event log of messages and parts.

But the model should not always see the entire raw history.

After compaction, the effective working history becomes:

- the compacted continuation boundary
- not the full original conversation prefix

`MessageV2.filterCompacted(...)` is the function that enforces that boundary before the loop builds model input.

So this layer determines what “the conversation so far” actually means at runtime.

---

# 3. `filterCompacted(...)` operates over streamed durable history

The session loop calls:

- `MessageV2.filterCompacted(MessageV2.stream(sessionID))`

This is important.

The filtering logic does not depend on in-memory loop state.

It derives the effective history from the durable message stream itself.

That is consistent with the rest of OpenCode’s state-first architecture.

---

# 4. The algorithm is small but very important

`filterCompacted(...)` keeps:

- `result` array
- `completed` set of user message IDs

Then for each streamed message it:

- pushes the message
- checks whether this message is a compaction user message whose ID is in `completed`
- breaks if so
- marks completed parent IDs when it sees successful assistant summary messages

Then it reverses the accumulated result.

This compact algorithm is the real compaction cutoff rule.

---

# 5. What counts as a completed compaction summary

A user ID is added to `completed` only when the stream contains an assistant message that is:

- role `assistant`
- `summary === true`
- has `finish`
- has no `error`

This is a precise contract.

A compaction summary becomes authoritative only after it finished successfully.

That avoids truncating history based on an incomplete or failed compaction attempt.

---

# 6. Why success-only cutoff is essential

If the runtime cut off history as soon as it saw a compaction request, a failed summary could discard the only usable context.

By waiting for a completed summary assistant message, OpenCode ensures the replacement context actually exists before treating older history as obsolete.

That is the right reliability boundary.

---

# 7. Why the break condition is on the user compaction marker, not the summary message itself

The loop breaks when it sees a user message that:

- has a `compaction` part
- and whose ID is already in `completed`

This is very clever.

The compaction user message is the explicit request boundary, while the later summary assistant message is the result.

Once the result is known to exist, encountering the original compaction request tells the filter:

- everything older than this request has now been superseded by the summary

That is the real cutoff point.

---

# 8. Why the cutoff preserves post-compaction continuation state

Because the break happens at the compaction request boundary, the retained history includes:

- the completed compaction summary
n- any replayed or continuation user messages after it
- subsequent assistant turns

So the retained history is not just the summary itself.

It is the whole continuation segment after the compaction boundary.

That is exactly what the runtime needs.

---

# 9. Reversing at the end restores chronological order

The stream iteration and break behavior are shaped by the underlying paging/stream order, so `filterCompacted(...)` reverses the collected result before returning it.

The important point is that the final output is a forward-chronological effective history.

That makes it directly usable by later logic like:

- reverse scans for `lastUser` / `lastAssistant`
- `toModelMessages(...)`
- title-generation or compaction prompt construction

---

# 10. `filterCompacted(...)` is not deleting history

This function does **not** mutate storage.

It only chooses the effective slice of history used for current execution.

That is an important distinction.

Compaction is implemented as:

- append new summary and continuation state
- then filter old superseded context out of the active working set

Not as destructive history rewrite.

That preserves auditability and replay potential.

---

# 11. `toModelMessages(...)` is the second half of the boundary

After `filterCompacted(...)` decides what messages remain in scope, `toModelMessages(...)` converts those retained `MessageV2.WithParts[]` entries into provider-facing model messages.

So the effective history boundary is a two-step process:

- choose which durable messages still count
- convert those messages into model-ready wire content

This distinction matters.

---

# 12. User text parts pass through unless ignored

For user messages, `toModelMessages(...)` includes text parts when:

- part type is `text`
- part is not `ignored`

This is the baseline content-preservation rule.

The model sees real textual user context directly unless explicitly excluded.

---

# 13. Non-text file handling depends on media stripping options

For user file parts that are not plain text or directories, `toModelMessages(...)` either:

- preserves them as file parts
- or rewrites them into textual placeholders like `[Attached mime: filename]`

when `stripMedia` is enabled.

This is important because compacted histories often need to reduce payload size while preserving semantic awareness that attachments existed.

---

# 14. Why compaction uses `stripMedia: true`

`SessionCompaction.process(...)` calls:

- `MessageV2.toModelMessages(messages, model, { stripMedia: true })`

This is exactly how compaction survives oversized multimodal contexts.

The model gets a summary-friendly textual mention of attachments rather than the full heavy attachment payload.

That preserves continuity while shrinking cost.

---

# 15. Compaction parts are turned into a textual prompt seed

When `toModelMessages(...)` sees a user `compaction` part, it inserts user text:

- `What did we do so far?`

This is a very important semantic rewrite.

The raw compaction marker is an internal control artifact.

The model-facing representation becomes a natural-language request for summary/handoff context.

That makes compaction understandable to the model without exposing internal control-structure details.

---

# 16. Why internal control parts become text

Providers and models operate on conversational payloads, not OpenCode-specific part types.

So internal control parts like `compaction` or `subtask` must be translated into model-comprehensible text.

This is a key role of the message projection layer.

---

# 17. Subtask parts are also projected into textual stand-ins

For user `subtask` parts, `toModelMessages(...)` inserts text:

- `The following tool was executed by the user`

This mirrors the compaction behavior.

Again, OpenCode translates internal structured workflow markers into the model’s conversational grammar.

---

# 18. Assistant messages are also filtered and normalized during projection

For assistant messages, `toModelMessages(...)` does more than pass through text.

It:

- skips certain errored assistant turns
- preserves text and reasoning parts
- maps tool parts into AI SDK tool output/error blocks
- injects media attachments separately for providers that cannot handle media in tool results
- drops pure `step-start`-only messages from final output

So the projection layer is a substantial normalization pass.

---

# 19. Why errored assistant messages are sometimes skipped

The function skips assistant messages with errors unless they are aborted in a way that still produced meaningful non-trivial parts.

This prevents provider-facing history from being polluted by unusable failed turns while still preserving relevant partial output when it exists.

That is a nuanced projection policy.

---

# 20. Tool results respect compaction pruning too

If a completed tool part has `part.state.time.compacted`, `toModelMessages(...)` replaces its output with:

- `[Old tool result content cleared]`

This is another compaction-adjacent boundary.

Even within retained history, some old tool outputs may already have been pruned to save space.

So the effective context is shaped by both:

- history cutoff after successful compaction
- content clearing for older retained tool outputs

---

# 21. Why filtering and pruning are complementary

`filterCompacted(...)` removes entire superseded history prefixes from the active set.

Pruning preserves the active set but clears bulky old tool-result bodies.

Together they create a layered context-budget strategy:

- remove obsolete history regions
- compress retained history where possible

That is a strong design.

---

# 22. Providers that cannot handle tool-result media get synthetic user follow-ups

When assistant tool results include media attachments and the provider does not support media in tool results, `toModelMessages(...)` emits:

- an assistant tool-output block without those media files
- then a synthetic user message attaching the media separately

This is another example of message-layer protocol adaptation.

The projection layer is where model-facing conversation grammar is corrected for provider constraints.

---

# 23. Step-start-only messages are removed from final model output

Before converting to AI SDK model messages, the code filters out any UI message whose parts are only `step-start`.

This is sensible.

Step markers are useful durable execution artifacts, but they are not helpful conversational content for the model.

So the message layer keeps them in storage while excluding them from provider-facing context.

---

# 24. Effective history after compaction is therefore highly curated

By the time the loop sends context to the model, the history has been shaped by several layers:

- `filterCompacted(...)` removes superseded pre-compaction history
- projection rewrites internal parts into text/tool grammar
- optional media stripping reduces payload size
- pruned tool outputs are replaced with placeholders
- non-conversational step-only artifacts are dropped

This means “history” in OpenCode is not raw transcript replay.

It is a curated, execution-aware working context.

---

# 25. Why this design preserves both durability and efficient continuation

OpenCode keeps the full event log durable, but presents only a curated active window to the model.

That lets it have both:

- durable audit/replay-friendly state
- efficient context windows for ongoing execution

The history filter and projection layer together are what make that balance possible.

---

# 26. A representative compaction cutoff lifecycle

A typical lifecycle looks like this:

## 26.1 Session grows large

- compaction task is created as a user part

## 26.2 Compaction assistant summary finishes successfully

- assistant message is marked `summary: true`
- parent user ID is now considered completed for compaction purposes

## 26.3 Future loop iterations call `filterCompacted(...)`

- stream is scanned
- once the corresponding compaction user message is reached, older history is cut off

## 26.4 Retained continuation history is projected through `toModelMessages(...)`

- compaction marker becomes text
- media may be stripped
- step-only artifacts are dropped

This is the true effective-history reset mechanism.

---

# 27. Why this module matters architecturally

This layer shows how OpenCode avoids a common agent-runtime trap: treating stored chat history and model input history as the same thing.

They are not the same.

Stored history is an event log.

Model input history is a curated operational context derived from that log.

`MessageV2.filterCompacted(...)` and `toModelMessages(...)` are the key functions that enforce that distinction.

---

# 28. Key design principles behind this layer

## 28.1 Durable history and effective model context should be separated

So compaction appends new state and the message layer filters superseded prefixes without deleting the underlying log.

## 28.2 History cutoffs should happen only after successful replacement context exists

So only completed, non-errored `summary: true` assistant messages authorize a compaction cutoff.

## 28.3 Internal workflow parts must be translated into model-comprehensible conversation text

So compaction and subtask parts become natural-language stand-ins during projection.

## 28.4 Context reduction should happen in layers, not by one blunt mechanism alone

So cutoff, media stripping, and tool-result pruning all cooperate to keep model context usable.

---

# 29. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/session/message-v2.ts`
2. `filterCompacted(...)`
3. `toModelMessages(...)`
4. `packages/opencode/src/session/compaction.ts`
5. `packages/opencode/src/session/prompt.ts`

Focus on these functions and concepts:

- `filterCompacted()`
- completed summary parent tracking
- compaction user-part break condition
- `toModelMessages()`
- compaction part -> `What did we do so far?`
- subtask part -> `The following tool was executed by the user`
- `stripMedia`
- `[Old tool result content cleared]`
- filtering out step-start-only messages

---

# 30. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Should the compaction cutoff algorithm become more explicit about multiple successive compaction generations in extremely long sessions?
- **Question 2**: How should collisions between multiple completed summary branches be handled if more complex branching semantics appear later?
- **Question 3**: Should compaction and subtask textual stand-ins be made more descriptive or more structured?
- **Question 4**: How much semantic degradation happens when media is stripped during compaction, and can it be improved?
- **Question 5**: Are there edge cases where skipping errored assistant messages removes context that would still be useful for continuation?
- **Question 6**: Should step-start and related execution metadata sometimes be preserved in model context for debugging-oriented agents?
- **Question 7**: How should `filterCompacted(...)` interact with future branching or merge semantics if sessions become less linear?
- **Question 8**: What tests best ensure that effective-history cutoff never activates on failed or partial compaction runs?

---

# 31. Summary

The `message_v2_filter_compacted_and_summary_cutoff` layer is where OpenCode decides which durable history still counts as active context after compaction:

- `filterCompacted(...)` cuts off superseded history only after a successful `summary: true` compaction assistant exists
- the cutoff occurs at the matching user compaction marker, preserving the post-compaction continuation segment
- `toModelMessages(...)` then projects that retained history into model-facing conversation form, translating internal parts, stripping media when needed, and excluding non-conversational artifacts

So this module is the boundary where OpenCode turns a full session event log into the smaller, curated working memory that the model should actually see.
