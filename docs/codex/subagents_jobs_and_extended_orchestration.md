# Subagents, Jobs, and Extended Orchestration in Codex

## Scope

This document explains how Codex spawns delegated child threads, how subagent sessions are identified and supervised, how approvals can be routed back to the parent session, and how higher-level orchestration patterns such as guardian review and agent jobs fit into the architecture.

The main references are:

- `codex-rs/core/src/codex_delegate.rs`
- `codex-rs/core/src/tools/handlers/agent_jobs.rs`
- guardian-related subagent paths in `codex-rs/core/src/guardian.rs`

This subsystem matters because it shows that Codex is not limited to one foreground thread talking to one model. It supports delegated execution patterns while still reusing the same session and turn abstractions.

---

## 1. The key idea: subagents are real Codex threads, not fake helper functions

Many agent systems claim to support subagents, but what they actually implement is often just a helper function with a different prompt.

Codex appears to do something stronger.

It can spawn delegated child Codex threads with:

- their own session source
- their own event stream
- their own submission channel
- their own cancellation scope
- shared access to key parent-owned services

That means a subagent is much closer to a real child runtime than to a prompt wrapper.

---

## 2. `SessionSource::SubAgent(...)` is the identity anchor

Delegated runs are explicitly tagged with:

- `SessionSource::SubAgent(subagent_source)`

### Why this matters

A child session is not anonymous. The runtime can know why it exists.

Examples suggested by the code include subagent sources for things like:

- review
- compact
- memory consolidation
- thread spawning or collaboration
- guardian review
- other named delegated roles

This explicit source identity is essential for observability and policy.

---

## 3. `run_codex_thread_interactive(...)`: child-thread spawning for ongoing interaction

The first major entry point in `codex_delegate.rs` is `run_codex_thread_interactive(...)`.

It:

- spawns a fresh Codex instance
- tags it as a subagent session
- shares major service managers from the parent session
- creates separate event and op channels
- forwards events outward
- forwards ops inward

### Why this is important

This is not just “call the model with a subagent prompt.”

It is a full child runtime with its own live control channels.

That means the architecture supports:

- continued interaction with the child
- streaming event observation
- cancellation and shutdown
- multi-step delegated workflows

This is a major capability.

---

## 4. Service sharing is deliberate and selective

When a child Codex thread is spawned, it inherits major service references such as:

- skills manager
- plugins manager
- MCP manager
- file watcher
- agent control

### Why this matters

The child runtime should not need to rebuild the entire world from scratch.

At the same time, it still gets its own session identity and conversation history context.

This balance is important:

- shared services preserve ecosystem consistency and reduce duplication
- separate session identity preserves execution isolation

That is a strong orchestration design.

---

## 5. Initial history can be provided explicitly

Child threads may be started with:

- new history
- or an explicit `InitialHistory`

### Why this matters

Delegation is not always a blank-slate action.

Sometimes a child needs:

- a curated subset of the parent transcript
- a reconstructed compact history
- a task-specific starting context

The ability to inject `InitialHistory` makes the child runtime reusable for multiple orchestration patterns.

---

## 6. `run_codex_thread_one_shot(...)`: delegated execution for a bounded task

Codex also provides `run_codex_thread_one_shot(...)`.

This is the convenience wrapper for:

- spawn child thread
- submit initial input immediately
- bridge events until completion or abort
- auto-shutdown the child afterward

### Why this matters

Not every subagent needs to be interactively steered for a long time.

Some orchestration patterns are one-shot by nature:

- review this request
- compact this transcript
- produce a structured answer
- evaluate this approval decision

The one-shot helper supports those workflows without losing the benefits of a real child runtime.

---

## 7. One-shot still uses the same runtime model underneath

A particularly nice design property is that one-shot execution does not invent a separate lightweight executor.

Instead, it reuses the interactive child-thread path and then adds:

- immediate initial submission
- event bridging
- auto-shutdown after terminal turn events
- a closed submission channel afterward

### Why this is a good design

It avoids having two completely separate subagent implementations:

- one for interactive delegates
- one for one-shot delegates

Reusing the same underlying runtime reduces behavioral drift and makes the delegation model easier to reason about.

---

## 8. Parent cancellation propagates through child tokens

The delegate paths use child cancellation tokens derived from the parent token.

### Why this matters

Subagents should not outlive the orchestration context that created them unless explicitly intended.

Using child tokens gives Codex:

- cascading cancellation
- scoped shutdown control
- the ability to cancel only one delegated task while preserving the broader parent runtime

That is exactly the kind of control a serious orchestration system needs.

---

## 9. Event forwarding is selective, not blind pass-through

`forward_events(...)` in `codex_delegate.rs` does not simply pipe every child event upward unchanged.

It filters or special-cases certain events, including approval-related flows.

### Why this matters

The parent runtime needs to decide which events should:

- remain internal to the child
- be surfaced to the caller
- be routed into parent approval mechanisms
- be suppressed as noisy or redundant legacy deltas

This means delegated execution is supervised, not merely proxied.

That is an important distinction.

---

## 10. Approval requests can be routed back to the parent session

One of the most important behaviors in the delegate path is that approval events are handled via the parent session rather than just being surfaced raw from the child.

### Why this matters

A child runtime should not necessarily own all approval authority.

If subagents could independently authorize everything, the parent agent would lose governance over delegated actions.

By routing approval decisions back through the parent context, Codex preserves a clear authority boundary.

This is a crucial safety property.

---

## 11. Guardian review is implemented as a specialized subagent pattern

The guardian system uses a dedicated subagent to review approval requests under certain conditions.

The code indicates:

- approval routes can be redirected to a guardian reviewer
- the guardian subagent receives a compact, curated transcript and an action summary
- the guardian must return a structured decision
- failure or timeout results in fail-closed denial semantics

### Why this matters

This is a concrete example that proves the delegation model is not theoretical.

Codex is already using subagents for meaningful governance tasks.

---

## 12. Guardian review is a strong example of orchestration discipline

The guardian flow demonstrates several strong orchestration principles:

- use a dedicated subagent identity
- pass a bounded, relevant transcript rather than the entire raw history
- require structured output
- bound runtime with timeout
- fail closed on uncertainty

### Why this is impressive architecturally

It shows Codex is not using delegation casually.

It is using delegation for tasks that benefit from a separate reasoning context and a separate evaluation role.

That is much closer to principled multi-agent design than to prompt hacking.

---

## 13. Child sessions are observable as distinct sources

Because session source and subagent source are explicit, the system can distinguish:

- foreground interactive sessions
- delegated collaboration threads
- guardian subagents
- compact/memory/review helper threads

### Why this matters

Observability and telemetry become much better when delegated work is explicitly typed.

This helps with:

- debugging
- auditability
- analytics
- conditional runtime behavior

Without explicit source identity, all child work would blur together.

---

## 14. Agent jobs are a higher-level orchestration layer over many delegated tasks

`tools/handlers/agent_jobs.rs` shows another orchestration pattern: batch jobs.

This handler supports operations such as:

- `spawn_agents_on_csv`
- `report_agent_job_result`

### Why this matters

This is not just one child thread. It is a batch orchestration system that can create many delegated work items and track their progress.

That means Codex is capable of not only nested agents, but also broad concurrent task fan-out patterns.

---

## 15. Agent jobs introduce a scheduler-like dimension

The batch-job handler tracks concepts such as:

- total items
- pending items
- running items
- completed items
- failed items
- concurrency caps
- runtime limits
- progress emission
- ETA estimation

### Why this matters

Once these concepts appear, the system is no longer only a conversational agent runtime.

It is also a lightweight distributed-orchestration runtime for many agent tasks.

That is a substantial expansion of capability.

---

## 16. Concurrency in jobs is bounded intentionally

The agent-job layer defines:

- default concurrency
- maximum concurrency
- status poll intervals
- progress emission intervals
- per-item timeout behavior

### Why this matters

Large-scale delegated execution can easily become chaotic without resource controls.

These limits show that Codex treats batch orchestration as an operational system with:

- throughput concerns
- timeout concerns
- progress reporting concerns
- failure accounting

That is the correct posture.

---

## 17. Background progress events are part of the orchestration contract

The job progress emitter sends structured background event payloads back through the session.

### Why this matters

A user or client monitoring a long-running multi-agent job needs more than a final result.

They need:

- progress
- failures
- ETA-like hints
- confidence that the system is still alive

This is one more example of Codex treating observability as a first-class part of orchestration rather than as an optional extra.

---

## 18. Depth limits matter in delegated agent spawning

The agent-jobs code imports logic such as:

- `exceeds_thread_spawn_depth_limit`
- `next_thread_spawn_depth`

### Why this matters

Recursive or unconstrained subagent spawning is a common failure mode in multi-agent systems.

Depth limits are a strong sign that Codex is aware of this risk and is enforcing structural bounds.

That is a very important control mechanism.

---

## 19. Delegation reuses the same core session/turn abstractions

The most important architectural property across all of this is reuse.

Subagents, guardian review, and jobs all appear to reuse the same foundational concepts:

- `Session`
- `TurnContext`
- `Codex::spawn`
- event channels
- submission channels
- cancellation tokens

### Why this is so important

This is what separates a composable runtime from a pile of special cases.

If every orchestration feature had its own ad hoc executor, the codebase would become extremely hard to reason about.

Codex instead extends the same core abstractions outward.

---

## 20. The hidden algorithm of extended orchestration

A good summary of the orchestration model is:

```text
1. decide whether a task needs delegated execution
2. spawn a child Codex session with explicit subagent source
3. provide bounded initial history and task input
4. supervise child events and route approvals through parent authority when needed
5. observe completion, failure, or timeout
6. convert delegated outcome into parent-visible state or progress events
7. optionally fan out across many tasks with bounded concurrency and lifecycle tracking
```

This is a coherent orchestration framework, not a collection of isolated hacks.

---

## 21. Why this architecture is strong

This design gives Codex several important strengths:

- delegated execution without rewriting the runtime model
- supervised child sessions with explicit identity
- strong cancellation and timeout semantics
- approval governance preserved at the parent level
- multi-item batch orchestration layered on the same core
- cleaner observability for both single-child and large-job workflows

This is a surprisingly mature orchestration architecture for a coding agent.

---

## 22. What can go wrong if this subsystem is changed carelessly

### Risk 1: treating subagents as prompt variants instead of real runtime children

That would weaken observability, lifecycle control, and event consistency.

### Risk 2: letting child sessions own approvals independently without parent governance

That could undermine safety and policy coherence.

### Risk 3: introducing new orchestration flows that bypass `SessionSource` and `SubAgentSource`

The system would become harder to observe and reason about.

### Risk 4: removing depth or timeout limits

Recursive or batch execution could spiral into resource or behavior instability.

### Risk 5: creating one-off executors instead of reusing core session and turn abstractions

That would fragment the architecture and increase maintenance cost sharply.

---

## 23. How to extend this subsystem safely

If you add a new subagent role or orchestration feature, the safe pattern is usually:

1. define an explicit `SubAgentSource`
2. decide whether the child should be interactive or one-shot
3. determine the minimum bounded initial history the child needs
4. preserve parent authority for approvals when appropriate
5. define timeout, cancellation, and depth-limit behavior clearly
6. decide how progress and final outcomes should flow back to the parent

### Questions to ask first

- Is this really a separate agent role, or just a new prompt for the existing foreground turn?
- Does the child need its own session identity and event stream?
- Which approvals should remain controlled by the parent?
- How deep can this delegation recurse safely?
- What progress information must be visible during long-running delegated work?

Those questions align directly with the existing architecture.

---

## 24. Condensed mental model

Use this model when reading the code:

```text
subagent
  = child Codex runtime with explicit source identity

one-shot delegate
  = subagent with immediate input and automatic shutdown

guardian
  = specialized review subagent with fail-closed semantics

agent jobs
  = bounded fan-out orchestration over many delegated work items
```

The most important takeaway is this:

- Codex extends its core session-and-turn runtime into a real delegated orchestration system rather than faking multi-agent behavior with prompt tricks

That is the defining property of this subsystem.

---

## Next questions to investigate

- How exactly are `SubAgentSource` variants defined across the protocol layer, and which orchestrators currently use each one?
- What history-compaction or transcript-curation logic is used before spawning specialized subagents like guardian or compact workers?
- How do agent jobs persist intermediate and final state in `codex_state`, and what recovery semantics exist if the parent process restarts?
- Which delegated workflows are interactive versus one-shot today, and what drove those choices?
- How are collab-agent spawn/resume/close notifications mapped back into app-server or TUI item lifecycles?
