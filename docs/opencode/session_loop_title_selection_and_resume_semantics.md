# Session Loop / Title Selection and Resume Semantics

---

# 1. Module Purpose

This document focuses on two specific behaviors inside `session/prompt.ts` that are easy to miss but architecturally important:

- how session titles are generated
- how the loop decides when to continue, stop, resume, or return a final assistant message

The key questions are:

- Why is title generation asynchronous and limited to very specific conditions?
- How does `ensureTitle(...)` decide what conversation context counts for title generation?
- What exact conditions cause the loop to stop early versus continue into another step?
- How does the loop recover from interruptions, queued user messages, compaction, or subtask branches?
- What does the final return path reveal about how OpenCode treats session progress as durable state rather than call-stack state?

Primary source files:

- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/message-v2.ts`
- `packages/opencode/src/session/compaction.ts`
- `packages/opencode/src/session/summary.ts`

This layer is OpenCode’s **session-title generation and loop-resume decision model**.

---

# 2. Why these two topics belong together

At first, title generation and resume semantics may look unrelated.

But both are examples of the same deeper design principle:

- the loop repeatedly re-derives behavior from durable session state

Title generation is triggered only when the state says the conversation is truly at its first meaningful user turn.

Resume behavior is controlled by current message/task state rather than hidden procedural context.

So both behaviors expose how strongly state-driven this runtime really is.

---

# 3. `step` is a real control variable, not just a debug counter

Inside `loop()`, the runtime increments:

- `step++`

This value controls several first-step and later-step behaviors.

Notably:

- `ensureTitle(...)` is only triggered when `step === 1`
- `SessionSummary.summarize(...)` is only kicked off when `step === 1`
- reminder wrapping for queued user messages only happens when `step > 1`
- max-step enforcement is derived from `step >= maxSteps`

So `step` is a compact but important part of the loop’s scheduling semantics.

---

# 4. Title generation is deliberately asynchronous and non-blocking

On the first step, the loop does:

- `ensureTitle(...)`

without awaiting it.

This is very important.

Title generation is treated as a side task, not as a prerequisite for the main agent turn.

That means:

- the runtime does not stall the main reply just to get a title
- title generation is useful metadata enrichment, not critical-path execution

That is the right priority.

---

# 5. Why titles are generated only on the first step

The loop triggers `ensureTitle(...)` only when:

- `step === 1`

This reflects a strong product assumption:

- titles should be generated from the first real user intent, not from arbitrary later state after the session has evolved

That avoids titles being rewritten constantly as the task progresses.

It also keeps the session title tied to the original conversation purpose rather than later mid-session details.

---

# 6. `ensureTitle(...)` refuses to run for child sessions

The first guard is:

- `if (input.session.parentID) return`

This is an important design choice.

Child sessions are not treated as title-owning top-level conversations for this path.

That suggests titles are meant to describe primary conversation identity, not every nested branch or sub-session automatically.

---

# 7. Titles are only generated when the current title is still default

Another guard is:

- `if (!Session.isDefaultTitle(input.session.title)) return`

So title generation is one-shot or near one-shot by default.

If the session already has a non-default title, the runtime does not overwrite it.

This is good behavior because generated titles should not fight later user- or system-set titles.

---

# 8. The first meaningful user message is carefully defined

`ensureTitle(...)` does not just take the first user message blindly.

It searches for the first user message where:

- not every part is synthetic

That means synthetic-only user messages do not count as the true first conversational turn.

This is a subtle but very important distinction.

It prevents internal helper messages from hijacking the session title.

---

# 9. Why synthetic-only user messages must be excluded

The loop may create synthetic user messages for continuity reasons, such as:

- summarizing task output and continuing
- preserving provider conversation signatures
- replaying continuity instructions

Those messages are runtime scaffolding, not original human intent.

Using them for title generation would produce weak or misleading session names.

So excluding synthetic-only user messages is exactly right.

---

# 10. Title generation only happens if there is exactly one real user turn

`ensureTitle(...)` also computes whether the history contains only one non-synthetic user message.

If there is more than one real user message, it returns without generating a title.

This is a very important contract.

It means the title is intentionally derived from the earliest single-turn framing of the conversation, not from a later multi-turn merged context.

---

# 11. Why “exactly one real user message” is a strong heuristic

This heuristic says:

- title generation should happen while the conversation goal is still clean and unambiguous

Once multiple real user turns exist, the session may have drifted or broadened.

At that point, automatic title generation becomes less trustworthy.

So the runtime opts out instead of guessing.

---

# 12. Context for title generation is also carefully bounded

After finding the first real user message, `ensureTitle(...)` uses:

- all messages up to and including that message

This is described in code comments as including any shell or subtask execution that may have happened before the first prompt.

That is revealing.

The title generator is allowed to see a little bit of pre-prompt operational context when that context is part of how the session actually began.

---

# 13. Why pre-prompt task context can matter for titles

In command-driven or subtask-driven entry paths, the first meaningful session context may include tool-execution setup before the human-facing prompt text is obvious.

Including those earlier messages helps the title model better understand what kind of conversation this actually is.

This is a pragmatic, context-sensitive heuristic.

---

# 14. Subtask-only first user messages are handled specially

`ensureTitle(...)` detects when the first real user message consists only of:

- `subtask` parts

In that case, it does **not** rely on `MessageV2.toModelMessages(...)` alone.

Instead it extracts the subtask prompts directly.

This is an important source-grounded detail.

---

# 15. Why subtask-only handling needs a special path

The comments explain that normal model-message conversion would turn subtask parts into generic text like:

- “The following tool was executed by the user”

That would lose the actual subtask prompt semantics needed for a good title.

So the runtime bypasses that generic conversion and feeds the real subtask prompts into the title model instead.

That is a strong example of targeted semantic preservation.

---

# 16. Title generation uses the `title` agent, not the main session agent

`ensureTitle(...)` loads:

- `Agent.get("title")`

This is important.

Title generation is treated as its own agent role with its own model-selection behavior, not merely as an afterthought of the current main agent.

That makes the title path more configurable and more intentional.

---

# 17. Model selection for the title path is optimized for small/cheap execution

The code chooses:

- the title agent’s model if it has one
- otherwise `Provider.getSmallModel(input.providerID)`
- otherwise fallback to the original conversation model

This is a well-considered strategy.

A title is short metadata. It does not need the full-cost primary model path unless there is no smaller suitable option.

That is good runtime economics.

---

# 18. Title generation uses a narrow prompt framing

The title model is invoked with a leading user message:

- `Generate a title for this conversation:`

followed by either:

- direct subtask prompts
- or model-converted conversation context up to the first real user message

This is a very constrained generation setting.

The runtime is not asking the model to do anything broad or open-ended.

It is asking for a specific metadata artifact from a tightly bounded context slice.

---

# 19. Title output is cleaned before storage

After the result returns, `ensureTitle(...)`:

- strips `<think>...</think>` blocks
- takes the first non-empty trimmed line
- truncates to 100 characters

This is a practical output-normalization step.

It acknowledges that title-generation models may still emit extra reasoning or formatting noise.

The runtime cleans that into a product-ready title before persisting it.

---

# 20. Why title cleaning is important

A session title is UI-facing metadata.

It should be:

- short
- readable
- stable
- free of chain-of-thought or verbose explanation

The cleanup path enforces exactly that.

---

# 21. Resume semantics start with the reverse scan

The loop’s resume logic begins with finding:

- latest user
- latest assistant
- latest finished assistant
- pending tasks before the last finished boundary

This is the real reason the loop can resume safely.

It does not need to remember where it was procedurally.

It just recomputes the current frontier from session state.

That is the heart of its resumability.

---

# 22. One key early-stop rule defines “already finished”

If the latest assistant has a finish reason that is:

- present
- not `tool-calls`
- not `unknown`

and it comes after the latest user, the loop logs `exiting loop` and stops.

This is a crucial semantic rule.

It means:

- a completed assistant response after the latest user means there is no more work to do for that user turn

Unless a special branch reopens work later, the loop is done.

---

# 23. Why `tool-calls` and `unknown` do not count as final completion

Those finish reasons imply the assistant turn is not truly settled as a final answer state.

They indicate:

- tool-mediated continuation is still in flight
- or the state is not a clean terminal stop

So the loop correctly treats them as non-final and may continue processing.

---

# 24. Pending tasks are resume points embedded in state

A pending `subtask` or `compaction` part is effectively a serialized continuation marker.

When the loop sees one before the last finished assistant boundary, it resumes into that branch.

This is very important.

The runtime does not need hidden in-memory continuation tokens. It stores the continuation branch directly in message parts.

That is a strong state-machine design.

---

# 25. Overflow-triggered compaction is also a resume mechanism

When the loop detects overflow based on `lastFinished.tokens` and the current model, it creates a compaction task and continues.

That means compaction is not just cleanup.

It is an explicit “resume via compacted context” mechanism that keeps the session progressing even after context pressure invalidates the current continuation path.

---

# 26. Mid-loop user messages are promoted, not ignored

When `step > 1` and a previous finished assistant exists, the loop looks for user messages newer than that finished assistant and wraps their text in a reminder block.

This is a major resume semantic.

It means new user input that arrives while the loop is still active is not lost behind prior agent work.

Instead, it is promoted into the next model turn’s salience layer.

---

# 27. Why this matters for interactive concurrency

In many systems, once the model is mid-task, new user input becomes awkward or is deferred opaquely.

Here, the runtime has an explicit mechanism to reincorporate those queued messages into the next loop step.

That is one reason the session feels like a living shared state machine rather than a single locked request/response pipeline.

---

# 28. `SessionSummary.summarize(...)` is also first-step-only metadata work

On `step === 1`, the loop also triggers:

- `SessionSummary.summarize(...)`

This is another sign that the first step has special meaning.

The first step is where the runtime kicks off background metadata and summary enrichment that should track the conversation from its earliest meaningful state.

---

# 29. Structured-output mode changes stop semantics

If the loop is in JSON-schema mode and a `StructuredOutput` tool successfully captures output, the runtime:

- stores the structured result
- ensures finish is set
- updates the assistant message
- breaks immediately

This is an important alternate completion path.

The loop is not always waiting for a conventional assistant finish reason. Structured output can define its own success boundary.

---

# 30. JSON-schema mode also defines a specific failure stop

If the model finishes without calling the `StructuredOutput` tool in JSON-schema mode, the runtime writes a `StructuredOutputError` and breaks.

So structured-output sessions have a strong binary completion rule:

- produce valid structured output
- or fail explicitly

That is tighter than ordinary conversational completion semantics.

---

# 31. Final return semantics are state-based too

After the loop ends, the runtime:

- calls `SessionCompaction.prune({ sessionID })`
- iterates through `MessageV2.stream(sessionID)`
- returns the first non-user message it finds
- resolves any queued callbacks waiting on that message

This is a subtle but important return model.

The function does not simply return “the current in-memory assistant object.”

It re-reads the authoritative stream and returns the persisted result from state.

That is fully consistent with the state-first architecture.

---

# 32. Why callback resolution after re-reading state matters

Queued callbacks are resolved from the persisted stream result, not from a transient object reference.

This reduces the chance that the returned object diverges from what was actually written to durable session state.

Again, the runtime treats state as the source of truth.

---

# 33. A representative title-and-resume lifecycle

A typical lifecycle looks like this:

## 33.1 First real user turn arrives

- message is persisted
- loop starts

## 33.2 First step begins

- `ensureTitle(...)` runs asynchronously
- `SessionSummary.summarize(...)` starts

## 33.3 Loop checks for task branches or overflow

- subtask, compaction, or normal processing path

## 33.4 Additional user input may arrive mid-loop

- next steps wrap it in reminder form if needed

## 33.5 Loop stops only when state indicates the latest user turn is truly satisfied

- otherwise it continues from persisted task/message state

## 33.6 Final return reads persisted stream state back out

- callbacks resolve from durable session output

This is a clean state-driven metadata-and-resume lifecycle.

---

# 34. Why this module matters architecturally

This behavior reveals some of the strongest runtime design choices in OpenCode:

- metadata generation is state-aware and cheap enough to run off the critical path
- continuation is encoded in persisted messages and parts, not hidden coroutine state
- the loop can resume from subtasks, compaction, overflow, or queued user input using the same state frontier logic
- even the final return path trusts persisted state over transient in-memory execution artifacts

That is unusually disciplined runtime design for an agent loop.

---

# 35. Key design principles behind this layer

## 35.1 Title generation should reflect the first real conversation intent, not synthetic scaffolding or later drift

So `ensureTitle(...)` filters to the first non-synthetic user turn and only runs when exactly one real user turn exists.

## 35.2 Metadata enrichment should not block primary task execution

So title generation runs asynchronously and outside the main critical path.

## 35.3 Resumption should be derived from durable session state, not hidden procedural continuation state

So the loop re-scans messages, tasks, and finish markers every iteration.

## 35.4 Completion should be defined by semantic stop conditions, not merely by “one model call finished”

So the loop distinguishes true completion from `tool-calls`, `unknown`, compaction continuation, and structured-output-specific stop paths.

---

# 36. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/session/prompt.ts`
2. the `ensureTitle(...)` function in `prompt.ts`
3. `packages/opencode/src/session/message-v2.ts`
4. `packages/opencode/src/session/compaction.ts`
5. `packages/opencode/src/session/summary.ts`

Focus on these functions and concepts:

- reverse scan for `lastUser`, `lastAssistant`, `lastFinished`, and `tasks`
- the `exiting loop` early-stop condition
- `ensureTitle(...)`
- first real user message filtering
- synthetic message exclusion
- reminder wrapping for queued user messages
- structured-output stop semantics
- final re-read of `MessageV2.stream(sessionID)`

---

# 37. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Should child sessions eventually get their own title-generation path, or is top-level-only title ownership the right long-term model?
- **Question 2**: How should title generation behave if the first meaningful user turn is extremely long or dominated by file attachments?
- **Question 3**: Are there edge cases where multiple real user turns arrive so quickly that title generation should still proceed differently?
- **Question 4**: How do queued callbacks in `state()[sessionID]?.callbacks` get registered, and what exact consumers depend on this final return path?
- **Question 5**: Should reminder-wrapped mid-loop user messages also influence title or summary generation differently?
- **Question 6**: Are there any race conditions between asynchronous `ensureTitle(...)`, later manual title changes, and background summary generation?
- **Question 7**: Should structured-output mode have a recover-and-retry path rather than immediate failure on missing `StructuredOutput` invocation?
- **Question 8**: How do branch sessions, shares, and multi-client frontends affect the exact semantics of “latest user turn is satisfied”? 

---

# 38. Summary

The `session_loop_title_selection_and_resume_semantics` layer shows how OpenCode derives both metadata and continuation behavior from durable session state:

- titles are generated only once, only for top-level sessions, and only from the first real non-synthetic user turn
- title generation runs off the critical path and uses a dedicated lightweight agent/model strategy
- the loop resumes by re-scanning persisted message and task state rather than relying on call-stack continuation
- stop conditions distinguish real completion from tool-mediated, compaction-mediated, overflow-mediated, and structured-output-specific continuation paths

So this layer is a precise example of OpenCode’s core runtime philosophy: durable state defines both what the conversation is called and what the agent should do next.
