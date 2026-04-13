# Session Summary vs Compaction Summary Boundary

---

# 1. Module Purpose

This document explains the boundary between two different summarization mechanisms in OpenCode:

- background session/message summary in `session/summary.ts`
- context-compaction summary generation in `session/compaction.ts`

The key questions are:

- Why does OpenCode have two different summary systems at all?
- What does `SessionSummary.summarize(...)` actually compute?
- What makes a compaction summary fundamentally different from a session diff summary?
- How does the runtime distinguish ordinary assistant turns from compaction-summary assistant turns?
- Why is separating these responsibilities important for resumability and context management?

Primary source files:

- `packages/opencode/src/session/summary.ts`
- `packages/opencode/src/session/compaction.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/message-v2.ts`

This layer is OpenCode’s **dual summarization boundary between observability and continuation**.

---

# 2. Why two summary systems exist

At first glance, it might seem redundant to have both `SessionSummary` and `SessionCompaction`.

But they solve very different problems.

`SessionSummary` answers questions like:

- what changed in the session?
- what file diff metadata should be attached to the session or message?

`SessionCompaction` answers a very different question:

- how can the conversation be compressed into a continuation-ready form when context pressure is too high?

So one is about **observability and change tracking**.

The other is about **survival and continuation under context limits**.

---

# 3. `SessionSummary.summarize(...)` is lightweight metadata enrichment

`SessionSummary.summarize(...)` takes:

- `sessionID`
- `messageID`

Then it loads all session messages and runs two operations in parallel:

- `summarizeSession(...)`
- `summarizeMessage(...)`

This is an important signal.

The summary module is not generating a natural-language continuation prompt.

It is computing structured metadata derived from snapshots and message scope.

---

# 4. `summarizeSession(...)` computes session-level diff totals

At session level, the code:

- computes diffs via `computeDiff(...)`
- stores summary counts on the session
- writes full diff data to storage under `session_diff`
- publishes `Session.Event.Diff`

The resulting session summary contains:

- additions
- deletions
- file count

This is clearly operational/project summary data, not conversational recap text.

---

# 5. `summarizeMessage(...)` computes per-user-turn diff metadata

At message level, the summary module narrows scope to:

- the selected user message
- assistant messages whose `parentID` matches that user message

Then it computes diffs over just that subset and stores them into:

- `userMsg.summary.diffs`

This is again structured metadata.

It tells the runtime what changed during a particular user-turn lineage.

---

# 6. `computeDiff(...)` is snapshot-based, not semantic summarization

The diff computation scans message parts looking for:

- earliest `step-start` snapshot
- latest `step-finish` snapshot

Then it runs:

- `Snapshot.diffFull(from, to)`

This is a crucial distinction.

The summary module is driven by filesystem snapshot boundaries, not by LLM-generated textual abstraction.

So it is really a diff summarizer, not a conversational summarizer in the ordinary natural-language sense.

---

# 7. Why `SessionSummary` belongs to observability rather than continuation

Nothing in `summary.ts` constructs a prompt for the next model turn.

Instead it:

- records change metrics
- persists diff artifacts
- updates session and user-message metadata
- emits diff events

That is a classic observability/reporting role.

It helps users and systems understand what happened, but it is not itself the mechanism that enables context recovery.

---

# 8. `SessionSummary.summarize(...)` is triggered opportunistically in normal execution

The source shows it being triggered:

- on `step === 1` in `session/prompt.ts`
- again in `session/processor.ts` around assistant step completion

This suggests the summary layer is cheap enough and useful enough to run as background metadata enrichment during ordinary processing.

That is very different from compaction, which is a heavyweight exceptional workflow.

---

# 9. Compaction begins with context-pressure or explicit compaction tasks

`SessionCompaction` is reached when:

- a pending compaction task exists
- or `SessionCompaction.isOverflow(...)` says the context is too large
- or processor execution returns `compact`

This is a very different trigger model.

Compaction is not routine metadata enrichment.

It is a response to context budget constraints or explicit continuation management.

---

# 10. `SessionCompaction.isOverflow(...)` is budget math, not summary logic

The overflow check compares token usage against model limits while reserving output budget.

It uses:

- model context/input limits
- configured reserved token budget
- `ProviderTransform.maxOutputTokens(model)`

This confirms compaction is fundamentally a context-management system.

The summary it produces is instrumental to fitting and continuing, not to describing diffs for UI or analytics.

---

# 11. Compaction creates a dedicated assistant message marked `summary: true`

When compaction runs, it persists an assistant message with:

- `mode: "compaction"`
- `agent: "compaction"`
- `summary: true`

This is an extremely important contract.

Compaction summary output is not stored as ordinary assistant text.

It is explicitly typed in message state as a summary assistant turn.

That is how the runtime can distinguish compaction summaries from normal conversation turns.

---

# 12. Why `summary: true` is a key boundary marker

The summary flag allows downstream logic to treat compaction-generated messages specially.

The grep results show `message-v2.ts` and compaction pruning logic checking this boundary.

That means compaction summaries are first-class state markers, not just conventional text labels.

This is the real architectural boundary between the two summary systems.

---

# 13. The compaction summary is generated by an LLM through `SessionProcessor`

Unlike `SessionSummary`, compaction creates a processor and runs an actual model call with a dedicated prompt.

The prompt asks for:

- the conversation goal
- instructions
- discoveries
- accomplished work
- relevant files/directories

This is natural-language continuation-oriented abstraction.

So compaction summary is a semantic restatement of work for future continuation, not a diff report.

---

# 14. Why compaction uses a dedicated `compaction` agent

The code resolves:

- `Agent.get("compaction")`

and optionally uses that agent’s model.

This is important because context compression is treated as its own specialized operation with its own prompt and possible model strategy.

It is not just “run the normal agent but ask for a summary.”

---

# 15. Compaction strips media to survive context limits

When constructing compaction messages, it uses:

- `MessageV2.toModelMessages(messages, model, { stripMedia: true })`

This is a strong signal of its purpose.

Compaction is explicitly trying to preserve semantic continuity while shedding expensive or oversized context payloads.

That is continuation-oriented summarization under budget constraints.

---

# 16. Plugins can alter the compaction summary prompt

Compaction triggers:

- `experimental.session.compacting`

which can inject extra context or replace the prompt.

This is another sign that compaction summary is a deliberate orchestration stage.

It is designed as a customizable continuation artifact, not a fixed diff calculation.

---

# 17. Compaction can create replay or continue messages after summarizing

If compaction succeeds and `auto` is true, it may:

- replay an earlier user message lineage
- or synthesize a continue message telling the agent to proceed

This is crucial.

Compaction summary is not the end product.

It is part of a recovery-and-resume workflow that prepares the next turn after context reduction.

That is fundamentally different from `SessionSummary`, which does not drive continuation.

---

# 18. Overflow-specific replay logic shows compaction is about preserving task continuity

When overflow is caused by too much context, compaction may search backward for a prior user message to replay.

If one is found, it re-creates that user message and copies forward its parts, with media reduced to textual placeholders when necessary.

This is a very strong example of continuation semantics.

The goal is not merely “summarize the past.”

The goal is “restore enough actionable context to continue work safely.”

---

# 19. Compaction failure is treated as execution failure

If compaction itself overflows or errors, the processor message gets a `ContextOverflowError` and returns `stop`.

That means compaction sits on the critical execution path once invoked.

By contrast, `SessionSummary` failure would be metadata degradation, not necessarily an inability to proceed with the conversation.

This difference in failure criticality is one of the clearest boundary markers between the two systems.

---

# 20. `message-v2.ts` encodes compaction as a structural part type too

The grep results show:

- `CompactionPart`
- special compaction handling in message conversion/filtering

So compaction is represented in state both as:

- a user-side compaction task part
- an assistant-side summary message marked `summary: true`

This makes compaction a formal state-machine branch.

Again, that is much richer than the lightweight summary metadata system.

---

# 21. Why compaction and summary should not be conflated

If these systems were merged conceptually, several architectural problems would appear:

- diff metadata would be mistaken for continuation-ready abstraction
- compaction failures would be treated like harmless reporting failures
- special summary assistant turns would blur with ordinary session metrics
- downstream filters and pruning rules would lose a clean boundary

The current separation avoids all of these problems.

---

# 22. `SessionSummary` is about “what changed?”

At a high level, `SessionSummary` answers:

- what files changed?
- how many additions and deletions occurred?
- what diff belongs to this turn or session?

This is useful for UI, analytics, and task reporting.

It is not sufficient for reconstructing next-step working context under a shrinking model budget.

---

# 23. `SessionCompaction` is about “how do we keep going?”

At a high level, compaction answers:

- how do we compress prior work into a continuation prompt?
- what context can be stripped or replayed?
- how can the loop re-enter execution after exceeding context budget?

That is a different problem category entirely.

---

# 24. A representative lifecycle showing both systems together

A normal execution may look like this:

## 24.1 First step begins

- `SessionSummary.summarize(...)` starts in the background

## 24.2 Assistant step finishes

- snapshots produce diff metadata
- session and message summaries update

## 24.3 Token usage grows too large

- overflow is detected
- compaction task is created or processor returns `compact`

## 24.4 Compaction runs as a dedicated branch

- `summary: true` assistant message is created
- LLM produces continuation-oriented summary text
- replay or continue message may be synthesized

## 24.5 Main loop resumes

- now using the compacted state boundary

This makes the difference between the two systems very clear.

---

# 25. Why pruning also depends on the compaction boundary

`SessionCompaction.prune(...)` stops when it encounters an assistant message with:

- `summary`

That means compaction summaries act as structural anchors in the retained history.

This would make no sense if session diff summaries and compaction summaries were the same kind of thing.

The pruning logic itself depends on the distinction.

---

# 26. Why `SessionSummary.diff(...)` has a separate retrieval path

The diff API reads stored `session_diff` records and normalizes file names.

This is another sign that session summary data is meant for retrieval/reporting workflows.

Compaction summaries, by contrast, live directly in the conversation stream as assistant messages consumed by continuation logic.

That is a major storage and consumption difference.

---

# 27. The deepest conceptual boundary: structured metrics vs semantic continuation artifact

The cleanest way to think about the difference is:

- `SessionSummary` produces structured metrics and diff artifacts about work already done
- `SessionCompaction` produces a semantic continuation artifact that helps a future agent keep working

Once framed that way, the code’s design choices make perfect sense.

---

# 28. Why this module matters architecturally

This boundary is one of the clearest examples of OpenCode avoiding abstraction collapse.

Many systems use the word “summary” ambiguously and mix together:

- UI summary
- diff summary
- context compression summary
- handoff summary

OpenCode keeps at least two of these roles distinct:

- diff/metadata summary
- compaction/handoff summary

That makes the runtime easier to reason about.

---

# 29. Key design principles behind this boundary

## 29.1 Observability metadata and continuation artifacts are different classes of state

So `SessionSummary` stores diffs and counts, while compaction stores a summary assistant turn designed for resumption.

## 29.2 Context compaction is an execution-path concern, not just reporting

So compaction is driven by overflow, creates dedicated task/message state, and may synthesize replay or continue messages.

## 29.3 Structural state markers should distinguish compaction summaries from ordinary assistant output

So compaction messages are marked `summary: true` and tied to `compaction` mode/agent.

## 29.4 Background metadata enrichment should stay lightweight and parallelizable

So `SessionSummary.summarize(...)` computes diffs opportunistically without becoming the core continuation mechanism.

---

# 30. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/session/summary.ts`
2. `packages/opencode/src/session/compaction.ts`
3. `packages/opencode/src/session/processor.ts`
4. `packages/opencode/src/session/prompt.ts`
5. `packages/opencode/src/session/message-v2.ts`

Focus on these functions and concepts:

- `SessionSummary.summarize()`
- `summarizeSession()`
- `summarizeMessage()`
- `computeDiff()`
- `SessionCompaction.isOverflow()`
- `SessionCompaction.process()`
- `summary: true` on compaction assistant messages
- replay vs continue message synthesis after compaction
- `SessionCompaction.prune()` boundary behavior

---

# 31. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Should compaction summaries also expose a typed structured payload alongside free-form text for stronger machine-readable continuation?
- **Question 2**: How often do session diff summaries and compaction summaries diverge in practice, and what does that reveal about task complexity?
- **Question 3**: Should there be a dedicated user-facing handoff summary separate from both current systems?
- **Question 4**: How should compaction prompts evolve to preserve more high-value context under extreme multimodal pressure?
- **Question 5**: Could `SessionSummary` diff metadata be used to improve compaction quality, for example by biasing file relevance sections?
- **Question 6**: Are there situations where compaction should happen proactively before overflow rather than reactively after detection?
- **Question 7**: How should pruning and compaction interact over very long sessions with many repeated handoff summaries?
- **Question 8**: What tests best guarantee that the summary boundary stays conceptually clean as new summary-like features are added?

---

# 32. Summary

The `session_summary_vs_compaction_summary_boundary` layer shows that OpenCode uses the word “summary” for two intentionally different systems:

- `SessionSummary` computes snapshot-based diff metadata for sessions and user-turn lineages
- `SessionCompaction` generates a dedicated `summary: true` assistant message that acts as a continuation artifact under context pressure
- the former is lightweight observability/reporting state
- the latter is an execution-path recovery and resumability mechanism

So the real boundary is simple but important: one system explains what changed, while the other preserves how to keep going.
