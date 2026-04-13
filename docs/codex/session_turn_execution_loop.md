# Session, Turn, and Execution Loop in Codex

## Scope

This document explains the subsystem centered on `codex-rs/core/src/codex.rs`, with emphasis on how Codex turns frontend requests into a durable session, a turn-scoped runtime configuration, a model sampling loop, and a follow-up execution cycle that can repeatedly call tools until the turn reaches a terminal state.

This is the most foundational module in the codebase. Almost every other subsystem depends on its abstractions:

- prompt construction depends on `TurnContext`
- tool visibility depends on turn-scoped settings
- context persistence depends on the session transcript
- diff tracking is owned at turn execution time
- app-server and TUI both eventually drive the same session and turn machinery

---

## 1. Why this module exists

Codex is not built around a single monolithic `agent.run()` entry point. Instead, it separates runtime concerns into three layers:

- `Session`
  - a long-lived container for conversation history, service handles, feature state, permissions, and session-wide resources
- `TurnContext`
  - a per-turn execution snapshot that freezes the model, working directory, policies, dynamic tool configuration, and behavioral settings for one unit of work
- execution loops
  - the control flow that consumes submissions, starts turns, streams model output, dispatches tools, records outputs, and decides whether another follow-up model step is needed

This separation is one of the strongest architectural decisions in Codex. It avoids mixing:

- UI state
- session state
- turn-local decisions
- provider transport behavior
- tool orchestration

If these were all stored in one object, the system would quickly become unreplayable and extremely hard to reason about.

---

## 2. Main source files and their roles

### `codex-rs/core/src/codex.rs`

This file is the orchestration center. It contains:

- Codex thread creation
- submission handling
- session loop spawning
- turn construction
- model sampling loop setup
- parts of the streaming event handling path
- retry, compaction, and follow-up logic

### `codex-rs/core/src/codex_delegate.rs`

This file extends the same abstractions to delegated or nested Codex threads. It is important because it proves that the session/turn model is reusable, not hardcoded only for the top-level interactive agent.

### Nearby supporting modules

The loop in `codex.rs` depends heavily on nearby modules, even when they are not the focus of this document:

- `context_manager/*`
- `tools/*`
- `stream_events_utils.rs`
- `turn_diff_tracker.rs`
- `instructions/*`

The important point is that `codex.rs` is not where every algorithm lives. It is where those algorithms are assembled into a coherent runtime.

---

## 3. The high-level control flow

A useful mental model is:

```text
frontend request
  -> Codex thread handle
  -> submission channel
  -> submission_loop()
  -> build or update turn state
  -> assemble model-visible input
  -> run_sampling_request()
  -> stream model output
  -> detect tool calls / messages / reasoning
  -> execute tools if needed
  -> write outputs back into session history
  -> continue until turn completion
```

This already shows a core property of Codex:

- the model loop is not the outermost loop
- the outermost loop is the submission loop
- model sampling is only one kind of work that a session can perform

That distinction matters because the agent also needs to respond to:

- interrupts
- realtime conversation events
- turn-context overrides
- cleanup actions
- shutdown
- delegated execution

---

## 4. Session creation: why Codex starts with a long-lived thread

When a Codex instance is spawned, it does not immediately run a model request. Instead, it creates:

- a `Session`
- submission and event channels
- an async task that runs `submission_loop(...)`

This design means the system is actor-like. The Codex handle is effectively a client API for an internal background runtime.

### What this buys the system

- operations can be serialized through a submission channel
- the UI or app-server can submit new operations without directly touching the internal state lock graph
- session shutdown can be modeled as just another operation
- long-lived background state survives across multiple turns

### Why this is better than a direct synchronous API

A synchronous “call model now” API would make it hard to support:

- streaming outputs
n- interruptions
- concurrent frontend actions
- delegated workers
- durable session semantics

The internal background thread provides a stable execution boundary.

---

## 5. Submission loop: the real outer event loop

The core outer loop is `submission_loop(sess, config, rx_sub)`.

At a systems level, this is the true event dispatcher of the agent runtime.

### What `submission_loop` processes

The loop consumes `Submission` values, each of which wraps:

- a unique ID
- an `Op`
- optional trace metadata

The `Op` enum represents runtime intents such as:

- interruption
- realtime conversation start/audio/text/close
- turn-context override
- user input submission
- shutdown
- cleanup tasks

This means Codex models “things the runtime should do” as messages, not direct method calls.

### Why that matters

This message-driven design provides:

- a natural sequencing mechanism
- a single place to attach tracing spans
- a single place to convert external intent into internal state transitions
- a clear boundary for cancellation and structured event emission

### Important invariant

The session loop should be thought of as the only authority that turns external requests into stateful runtime progress. Frontend layers should not bypass it.

That invariant is central to replayability and debugging.

---

## 6. Session versus turn: the most important boundary in the runtime

One of the most important ideas in Codex is that a session is not the same thing as a turn.

### Session responsibilities

A `Session` owns resources that survive across many turns:

- conversation history
- service handles
- auth and provider access
- plugin and MCP managers
- rollout recording
- feature and configuration state
- accumulated token usage and metadata

### Turn responsibilities

A `TurnContext` captures decisions that must be stable while one turn runs:

- current working directory
- approval policy
- sandbox policy
- model and provider selection
- collaboration mode
- personality and reasoning behavior
- output schema
- tool configuration
- per-turn metadata headers

### Why the split is necessary

If turn-scoped values lived directly on the session and changed in place during execution, the runtime would face difficult consistency problems:

- a streamed model response could observe changed tool visibility halfway through a turn
- approval policy could drift mid-request
- diff tracking might be computed against the wrong working directory
- retries could become nondeterministic

By freezing these values into a `TurnContext`, the system guarantees turn-local consistency.

---

## 7. Turn construction: `make_turn_context(...)`

The turn-construction path is where Codex converts mutable session configuration into a stable execution snapshot.

### Inputs to turn construction

Although the exact fields are numerous, the turn builder conceptually combines:

- session configuration
- provider/model choice
- current working directory
- user and developer instructions
- collaboration mode
- policy settings
- feature toggles
- dynamic tool settings
- skills outcome
- timing and metadata state

### Why `TurnContext` is a central abstraction

`TurnContext` is the object that lets multiple independent subsystems agree on the same runtime truth.

The following subsystems all read from it:

- prompt builder
- tool router builder
- event generation
- policy checks
- model client requests
- diff tracking metadata
- app-server and UI rendering support

Without a turn object, every subsystem would need to recompute or lock shared state independently, which would create race conditions and inconsistent snapshots.

### Hidden algorithmic idea

The turn builder is not an algorithm in the classic numerical sense, but it does perform an important compilation step:

- it compiles distributed configuration and runtime inputs into a normalized execution plan for one turn

That is a recurring pattern throughout Codex: the system prefers explicit intermediate representations instead of implicit shared state.

---

## 8. Input assembly before a model request

Before the runtime sends anything to the model, it assembles the model-visible history for the current turn.

A critical snippet in `codex.rs` shows this shape:

- collect pending input
- record it into history when needed
- call `clone_history().for_prompt(...)`
- filter or normalize according to model input modalities

### Why pending input is processed first

Pending input may include user messages that arrived while the model was still active. If the frontend allows such behavior, the runtime must convert those messages into durable transcript items before constructing the next model request.

This guarantees two things:

- history remains authoritative
- model input is derived from recorded state, not from ad hoc side channels

### The invariant here

The prompt should be built from session history, not from transient UI buffers.

That invariant makes resume, replay, and postmortem inspection possible.

---

## 9. `run_sampling_request(...)`: the outer model-request wrapper

Once the turn has an input transcript and a stable context, Codex enters `run_sampling_request(...)`.

This function is best understood as the resilience wrapper around the semantic agent loop.

### What it does

At a high level it:

1. builds the tool router for the turn
2. loads base instructions
3. builds the final prompt
4. prepares model client session state
5. initializes code-mode workers if needed
6. delegates the actual streaming logic to `try_run_sampling_request(...)`
7. handles retry or fallback transport behavior when provider streaming fails

### Why this function exists as a separate layer

The runtime needs to distinguish two different problem classes:

- semantic turn progression
  - messages, reasoning, tool calls, outputs, follow-up continuation
- transport and provider reliability
  - transient stream errors, retryable failures, context-limit conditions, switching connection modes

Merging both concerns into one loop would make the logic much harder to understand and much easier to break.

### Architectural reading

`run_sampling_request(...)` is effectively a supervisory loop around the lower-level agent stream processor.

---

## 10. `try_run_sampling_request(...)`: the semantic execution loop

This is the part of the runtime where the agent actually behaves like an agent.

The most important idea is simple:

- the model does not produce a single final string
- the model produces a stream of structured output events
- Codex reacts to those events in real time

### Responsibilities of the semantic loop

This loop maintains and coordinates:

- currently active output item state
- stream parsers for assistant text
- plan-mode stream state when applicable
- in-flight tool futures
- whether a follow-up model step is needed
- the latest agent-visible message fragment

### Practical consequence

The runtime is not “generate once, then inspect output.”

It is:

- stream
- classify
- dispatch
- record
- continue

This makes Codex much closer to an event-driven state machine than a traditional request/response chatbot.

---

## 11. Why Codex uses streaming as a state machine

A non-streaming design would simplify implementation, but it would lose major capabilities:

- early UI feedback
- incremental reasoning display
- live tool progress
- immediate detection of tool calls
- plan extraction while text is still arriving
- cancellation and interruption responsiveness

So the implementation cost of a streaming state machine is justified by the runtime behavior it enables.

### State-machine view

Conceptually, the loop moves through transitions like this:

```text
idle
  -> receiving output item shell
  -> receiving deltas
  -> item completion
  -> maybe tool dispatch
  -> maybe follow-up input writeback
  -> maybe next model step
  -> terminal turn completion
```

This is one of the best ways to understand the module when reading the code.

---

## 12. Follow-up turns and the `needs_follow_up` flag

A subtle but critical part of the loop is that one visible user turn may require multiple internal model sampling rounds.

That happens when:

- the model calls a tool
- the tool returns structured output
- the model must inspect that output and continue reasoning

### Why `needs_follow_up` exists

The runtime needs an explicit signal that the current sampling pass is not semantically complete, even if the provider stream itself has ended cleanly.

This is different from transport completion.

- provider stream completed = one request completed
- turn completed = the agent has no more follow-up work to do

Those are not the same thing.

### This is a core invariant

A turn should complete only when the semantic loop is done, not merely when one model request finishes.

That invariant is what allows tool-augmented reasoning to work correctly.

---

## 13. Turn-local diff tracking

Another important design decision appears near the execution loop: each turn gets its own `TurnDiffTracker` instance.

### Why the diff tracker is turn-scoped

From the user perspective, a turn is the unit that should yield a coherent diff.

Inside the runtime, that turn may contain:

- multiple tool calls
- multiple patch applications
- file renames
- repeated writes to the same file

If diff tracking were tied to each tool call independently, the final user-visible output would be fragmented and misleading.

### What the loop does with it

The execution path creates one shared diff tracker for the turn and passes it into tool execution.

This produces a clean guarantee:

- all file mutations inside the turn aggregate into one turn-level change model

That is both a UX decision and an internal consistency decision.

---

## 14. Auto-compaction and token pressure

The loop also monitors token usage after sampling passes complete.

### What the code is doing conceptually

After a sampling pass, it compares current usage against an auto-compaction limit and decides whether history needs to be compacted before the next follow-up step.

### Why this logic belongs near the loop

Token pressure is not just a context-manager concern. It directly affects whether the agent can safely continue the current reasoning path.

If the runtime only discovered token overflow deep inside a provider failure path, the resulting behavior would be brittle and hard to recover from.

Placing this logic at the orchestration layer lets Codex:

- observe cumulative turn cost
- decide whether another loop iteration is safe
- compact before the next semantic step when needed

This is one more example of Codex preferring explicit runtime control over blind provider delegation.

---

## 15. Retries and transport robustness

Codex distinguishes semantic continuation from transport robustness.

### What robustness means here

The runtime may need to recover from:

- stream interruptions
- retryable provider failures
- transport mismatches
- context-window errors
- temporary connectivity problems

### Why retries are outside the semantic logic

If transport retries were mixed directly into message and tool state handling, the runtime would risk replaying partial semantic state incorrectly.

Instead, the system wraps the semantic request processor with a supervisory layer that decides when a request should be retried.

This separation reduces corruption risks such as:

- double tool execution
- duplicated partial outputs
- inconsistent in-flight state reuse

### Important implementation principle

A failed stream does not automatically mean a failed turn. But a retried request must still preserve turn-level consistency.

This is why the same turn-scoped client session may be reused across retries.

---

## 16. Event emission as a first-class concern

Codex is not only executing logic. It is also continuously emitting events for frontends and observers.

This means the execution loop is also a translation boundary between:

- internal state transitions
- externally consumable event streams

### Why this matters architecturally

The loop is responsible for making the runtime observable.

That includes events for:

- start and completion
- hook execution
- agent messages
- plan deltas
- tool lifecycles
- errors
- turn completion

This is one reason the loop code looks orchestration-heavy rather than algorithm-heavy. A large part of its job is making internal progress externally visible without leaking raw implementation details.

---

## 17. Delegated Codex threads and subagents

`codex_delegate.rs` proves that the session/turn machinery is not a one-off design.

### What delegated threads show

The system can:

- spawn a child Codex thread
- feed it initial user input
- bridge its events back upward
- automatically shut it down on completion

This means the architecture supports nested or worker-like execution without rewriting the core control flow.

### Why this matters

A lot of agent systems claim to support subagents but actually implement them as special-case hacks. Codex does not appear to do that.

Instead, it reuses the same abstractions:

- session
- turn context
- submission channel
- event bridge
- shutdown signaling

That is a strong sign of a composable runtime design.

---

## 18. Hidden design principles visible in this module

The session/turn/loop subsystem reveals several strong design principles.

### Principle 1: explicit intermediate representations

Instead of passing around raw settings and prompt fragments everywhere, Codex uses explicit runtime objects:

- `Session`
- `TurnContext`
- `Prompt`
- `ResponseItem`
- `ResponseInputItem`
- `ToolCall`

This makes the system inspectable and testable.

### Principle 2: event-driven orchestration

The runtime is fundamentally built around:

- message intake
- stream events
- state transitions
- structured outputs

This is much more scalable than burying all logic in nested synchronous calls.

### Principle 3: turn-level consistency

A turn is treated as a coherence boundary for:

- settings
- tool set
- diff tracking
- prompt context
- output semantics

This is essential for deterministic behavior.

### Principle 4: semantic progress is distinct from transport success

A clean provider response does not imply the agent is done.

That distinction is critical for tool-augmented execution.

---

## 19. What can go wrong if you modify this module carelessly

This module is central, so small changes can have wide ripple effects.

### Risk 1: breaking turn determinism

If you start reading mutable session configuration during the middle of a turn instead of using `TurnContext`, you may create hard-to-debug inconsistencies.

### Risk 2: duplicating tool execution

If retry logic is moved into the wrong layer, tool calls may be replayed unexpectedly.

### Risk 3: corrupting history authority

If prompt input starts coming from transient UI state instead of recorded conversation items, replay and resume semantics degrade quickly.

### Risk 4: mixing provider concerns with semantic concerns

If transport failure handling and semantic continuation are entangled, the loop becomes fragile and difficult to reason about.

### Risk 5: losing observability

If event emission is treated as optional side work instead of part of the orchestration contract, frontends such as TUI and app-server clients will drift from runtime truth.

---

## 20. How to extend this subsystem safely

If you need to add new behavior, the safest pattern is usually:

1. decide whether the behavior is session-scoped or turn-scoped
2. if turn-scoped, add it to `TurnContext` construction rather than reading ad hoc globals later
3. ensure it becomes visible to the right downstream consumer
   - prompt builder
   - tool builder
   - policy logic
   - event layer
4. make sure follow-up behavior is explicit
5. preserve the invariant that recorded history remains the source of truth

### Example extension questions to ask first

- Is this setting stable for an entire turn?
- Should this affect prompt input, tool visibility, or both?
- Must this be recorded into history for replay?
- Does this need an event surface for frontends?
- Could this change cause different behavior when a request retries?

Those questions will save a lot of debugging time.

---

## 21. Condensed mental model

If you only keep one model in your head, use this one:

```text
Session
  = long-lived conversation runtime and durable state

TurnContext
  = compiled per-turn execution snapshot

submission_loop
  = outer actor loop for runtime operations

run_sampling_request
  = resilient wrapper around one model sampling phase

try_run_sampling_request
  = semantic stream-processing loop

needs_follow_up
  = signal that one provider response is not the end of the user-visible turn
```

That model makes the rest of the codebase much easier to decode.

---

## Next questions to investigate

- How exactly does `make_turn_context(...)` merge session settings, CLI overrides, and runtime updates into one stable turn snapshot?
- Which fields in `TurnContext` are purely descriptive, and which ones are behavior-changing downstream?
- How does the stream event handling path cooperate with `stream_events_utils.rs` to transform output items into frontend-visible turn items?
- What is the exact boundary between `run_sampling_request(...)` and model-client provider implementations?
- How does one-shot delegated execution differ from interactive delegated execution in cancellation and event forwarding semantics?
