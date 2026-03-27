# Model Streaming and Response Protocol in Codex

## Scope

This document explains how Codex represents model output, how it parses streaming provider events, how it turns those events into internal runtime state transitions, and how different output modes such as normal agent text, plan-mode text, reasoning deltas, and tool calls coexist in one structured response pipeline.

The main files are:

- `codex-rs/protocol/src/models.rs`
- `codex-rs/codex-api/src/sse/responses.rs`
- `codex-rs/core/src/codex.rs`
- `codex-rs/core/src/stream_events_utils.rs`

This subsystem matters because Codex does not treat model output as a single string. It treats output as a typed event stream that drives a runtime state machine.

---

## 1. The key idea: the model does not return text, it returns structured state transitions

At first glance, many users think the model interaction is:

- send prompt
- receive answer text

That is not how Codex is built.

The actual runtime assumption is much closer to:

- send structured prompt
- receive a stream of typed events
- progressively reconstruct response items
- classify each item as message, reasoning, tool call, or provider-side tool event
- update frontend-visible state and internal transcript state accordingly

This is a major architectural difference.

It means Codex is not a text chatbot with tools bolted on top. It is an event-driven orchestration engine for structured model output.

---

## 2. `ResponseItem`: the core output model

The foundational type is `ResponseItem` in `codex-rs/protocol/src/models.rs`.

Important variants include:

- `Message`
- `Reasoning`
- `LocalShellCall`
- `FunctionCall`
- `ToolSearchCall`
- `FunctionCallOutput`
- `CustomToolCall`
- `CustomToolCallOutput`
- `ToolSearchOutput`
- `WebSearchCall`
- `ImageGenerationCall`
- `GhostSnapshot`
- `Compaction`
- `Other`

### Why this type is so central

This is the shared protocol boundary across multiple runtime responsibilities:

- provider output parsing
- transcript storage
- tool-call identification
- compaction
- replay
- frontend event mapping

In other words, `ResponseItem` is the normalized unit of model-visible conversational state.

---

## 3. Why `ResponseItem` is better than plain text responses

A plain-text output stream would force the runtime to guess:

- whether the model is calling a tool
- whether a given chunk is reasoning or user-facing text
- whether a provider-side web search or image generation happened
- whether a text segment is final answer content or commentary

With `ResponseItem`, these distinctions are explicit.

This makes the runtime far more reliable and far less dependent on prompt conventions or text parsing heuristics.

---

## 4. `Message`: normal assistant or user-visible text output

The `Message` variant carries:

- `role`
- `content: Vec<ContentItem>`
- `phase: Option<MessagePhase>`
- optional end-turn metadata

### Why messages are content-block based

Codex does not assume a message is a single string. It is a list of content items, which allows the protocol to represent different content modalities and output forms consistently.

### `MessagePhase`

The protocol also defines:

- `Commentary`
- `FinalAnswer`

But the code explicitly notes that providers do not supply this consistently, so downstream logic must not depend on it as a hard guarantee.

That is a good example of Codex using provider hints without over-trusting them.

---

## 5. `Reasoning`: a first-class output type

Codex treats reasoning as its own response item, not as ordinary assistant text.

A reasoning item can include:

- summaries
- optional raw content
- encrypted content

### Why this matters

This allows Codex to:

- render reasoning differently from normal text
- preserve reasoning separately in transcript logic
- account for reasoning tokens differently in context management
- emit reasoning deltas as dedicated events instead of mixing them with normal assistant text deltas

This is a strong separation of concerns.

---

## 6. Tool invocation output types are structural, not textual

Tool-related variants include:

- `FunctionCall`
- `ToolSearchCall`
- `CustomToolCall`
- `LocalShellCall`

These represent actions the model wants the runtime to perform.

### Why this is important

The model does not need to write something like “please run tool X with Y.”

Instead, the provider stream can deliver a structured invocation item. Codex then parses and dispatches it through the tool system.

This is exactly how a serious tool-augmented runtime should work.

---

## 7. Output items and tool outputs both use the same broad transcript model

Notice that `ResponseItem` also includes output-side variants such as:

- `FunctionCallOutput`
- `CustomToolCallOutput`
- `ToolSearchOutput`

That means the transcript model is symmetrical enough to represent both:

- model-emitted tool requests
- runtime-emitted tool results

### Why that symmetry matters

A tool-augmented agent loop only closes properly when action requests and action results live in the same representational world.

Codex achieves that through the `ResponseItem` / `ResponseInputItem` family rather than through separate disconnected mechanisms.

---

## 8. The wire is streaming SSE, not a monolithic response object

The provider-facing streaming parser lives in `codex-api/src/sse/responses.rs`.

The `process_sse(...)` function consumes a byte stream, parses Server-Sent Events, and converts them into `ResponseEvent`s.

### Important event kinds handled

Among others, the runtime recognizes:

- `response.output_item.added`
- `response.output_item.done`
- `response.output_text.delta`
- `response.reasoning_summary_text.delta`
- `response.reasoning_text.delta`
- `response.completed`
- `response.failed`
- `response.incomplete`
- `response.created`

### Why Codex uses streaming event parsing

Because it wants to support:

- low-latency UI feedback
- early tool-call detection
- plan extraction before completion
- reasoning streaming
- cancellation responsiveness
- immediate provider error classification

A fully buffered model response would throw away many of these capabilities.

---

## 9. `process_responses_event(...)`: provider event normalization

`process_responses_event(...)` turns raw provider event payloads into internal `ResponseEvent`s.

This function is very important because it translates provider-specific wire events into a stable internal streaming language.

### Examples of normalization

- `response.output_item.done` becomes `ResponseEvent::OutputItemDone(item)`
- `response.output_text.delta` becomes `ResponseEvent::OutputTextDelta(delta)`
- reasoning summary and raw reasoning deltas become dedicated reasoning events
- provider completion events yield structured completion records with response id and token usage
- failed or incomplete responses are mapped into structured `ApiError` cases

### Why this is an important boundary

Everything downstream of this layer can reason in Codex-native events rather than in raw provider event strings.

That is exactly the right abstraction boundary.

---

## 10. Provider errors are classified early, not left as generic stream failures

When `response.failed` is received, the SSE layer can recognize and classify cases such as:

- context window exceeded
- quota exceeded
- usage-not-included anomalies
- invalid prompt
- server overloaded
- retryable rate-limit or stream errors

### Why this matters

A strong runtime must distinguish:

- semantic turn failures
- provider transport or service failures

By classifying failures early, Codex gives the turn loop enough information to decide whether to:

- abort
- retry
- compact
- notify the user
- switch transport

This is much better than propagating one generic stream error string upward.

---

## 11. The event stream feeds the semantic loop in `try_run_sampling_request(...)`

Once SSE events have been normalized, the core turn loop in `codex.rs` consumes them.

This loop maintains state such as:

- active output item
- in-flight tool futures
- assistant text parsers
- plan-mode stream state
- last agent message
- follow-up requirements

### Why this matters

The core turn loop is not simply “print tokens as they arrive.”

It is a streaming state machine that must decide, event by event:

- what this chunk belongs to
- whether it starts or finishes a logical item
- whether it triggers a tool
- whether it affects frontend rendering
- whether it changes the semantic completion status of the turn

That is a much richer job than streaming text to stdout.

---

## 12. `OutputItemAdded` versus `OutputItemDone`

Codex distinguishes between:

- an output item being introduced
- an output item being completed

### Why this distinction exists

A response item can begin existing before all its content has arrived.

For example, a `Message` item may be added first, then receive text deltas, and only later become complete.

### Why this is useful

This lets the runtime:

- start frontend item lifecycles early
- attach per-item parsers
- emit “started” events for UI
- progressively build state rather than waiting for the whole item to be complete

That improves responsiveness significantly.

---

## 13. `OutputTextDelta`: streaming text is attached to active items

`ResponseEvent::OutputTextDelta(delta)` is not treated as free-floating text.

Instead, the turn loop associates it with the active assistant item and routes it through per-item text parsers.

### Why a parser layer exists

Codex wants to do more than append text bytes:

- strip or separate hidden markup
- extract plan-mode segments
- keep citations local or filter them from visible display text
- preserve item-level streaming consistency

That is why `AssistantTextStreamParser` exists.

---

## 14. `AssistantTextStreamParser`: why streamed text needs its own parser

The turn loop keeps `AssistantMessageStreamParsers`, which holds one parser per active item.

This parser layer is especially important because it lets Codex treat streamed assistant text as a structured stream rather than raw token chunks.

### Responsibilities of the parser path

It can:

- accumulate incremental text
- split visible text from plan-mode content
- strip citations from display content
- flush buffered state when an item completes

### Why this is necessary

Streaming text often arrives in inconvenient boundaries that do not correspond to logical content boundaries.

A parser layer smooths that mismatch and gives the rest of the runtime cleaner semantic chunks.

---

## 15. Plan mode is not a separate provider protocol; it is a special interpretation layer

Plan mode does not replace the normal response protocol with a completely different one.

Instead, it adds an interpretation layer on top of normal streamed text handling.

This is visible in the presence of:

- `PlanModeStreamState`
- `ProposedPlanItemState`
- `ProposedPlanSegment`
- `PlanDeltaEvent`

### What this means architecturally

The provider still emits messages and deltas.

Codex then interprets certain assistant text segments as proposed plan content and routes them differently.

That is a very clean design because it avoids forking the entire response-processing pipeline just for plan mode.

---

## 16. `PlanModeStreamState`: deferred rendering for plan-mode assistant output

Plan mode introduces a special state object that tracks:

- pending assistant message items
- which assistant items have emitted start notifications
- buffered leading whitespace
- plan item lifecycle state

### Why this extra state exists

In plan mode, Codex wants to avoid showing empty or misleading assistant text items when the stream is really producing plan content.

So it defers some agent-message starts until it knows whether text is normal visible commentary or plan-specific content.

### Why this is subtle but important

This preserves a better UI and better item semantics without requiring a separate plan-only provider stream format.

It is a runtime interpretation improvement layered on top of the same underlying protocol.

---

## 17. Plan segments become `PlanDelta` events and `TurnItem::Plan`

When the assistant text parser emits plan segments, Codex maps them to dedicated plan behavior.

For example:

- normal visible text becomes agent-message content delta
- proposed-plan start begins a plan item
- proposed-plan delta emits `EventMsg::PlanDelta`
- plan completion eventually yields a completed `TurnItem::Plan`

### Why this is a good design

The runtime separates:

- normal conversational narration
- explicit plan content

without requiring the model to switch to an entirely different protocol.

That is both elegant and practical.

---

## 18. Reasoning deltas are streamed separately from message text

The SSE layer recognizes reasoning summary and reasoning raw-content deltas separately from output-text deltas.

That means reasoning is not merely appended into normal assistant text.

### Why this matters

Reasoning often has different product and runtime requirements:

- separate rendering
- separate accounting
- optional visibility rules
- different storage semantics

By keeping reasoning on its own event channel, Codex preserves that flexibility.

---

## 19. Output completion is where tool calls become executable actions

When `OutputItemDone(item)` arrives, the runtime uses `ToolRouter::build_tool_call(...)` to determine whether the completed item is a tool invocation.

If so, the runtime does not treat it as ordinary text completion. Instead, it:

- records the response item
- queues tool execution
- marks the turn as needing follow-up

### Why completion time is the right boundary

Tool-call items are usually not driven by long text deltas like assistant messages are. They are best understood once the full structured item is available.

That is why the runtime makes tool-dispatch decisions at item completion rather than on arbitrary intermediate deltas.

---

## 20. Non-tool items still become typed turn items

If `OutputItemDone(...)` is not a tool call, the runtime still processes it structurally.

For example, it can map items into frontend-facing `TurnItem`s and emit lifecycle events.

### Why this matters

Codex is not just storing response items. It is also translating them into product-facing runtime items for UIs and app-server consumers.

So the response protocol layer doubles as a translation layer between:

- model-native structures
- frontend-visible state items

This is one reason the streaming pipeline is so central.

---

## 21. Code, plan, and agent modes differ mainly in interpretation and orchestration

A common misconception is that each mode has an entirely different output format.

That is not quite true.

### Agent mode

In normal agent mode:

- assistant text is rendered as agent-message deltas
- reasoning is separate
- tool calls become executable actions

### Plan mode

In plan mode:

- the same streamed text path is reinterpreted through the plan parser
- some text becomes plan segments rather than ordinary assistant narration
- plan lifecycle events and plan items are emitted

### Code-oriented behavior

Code-oriented execution tends to change more in:

- tool surface
- nested orchestration
- execution strategy

than in the base `ResponseItem` protocol itself.

### Why this matters

Codex reuses one structured output model across multiple modes instead of inventing entirely separate response languages per mode.

That is a good composability decision.

---

## 22. Response events and transcript items are related but not identical

It is important not to confuse:

- streaming `ResponseEvent`
- durable `ResponseItem`
- frontend-facing `TurnItem`

### `ResponseEvent`

A transient runtime event produced by the streaming parser.

### `ResponseItem`

A normalized conversational artifact that can be stored in history.

### `TurnItem`

A frontend- or app-facing representation of an item within the current turn lifecycle.

### Why this distinction matters

These types live at different layers and should not be collapsed conceptually.

Codex uses each for a different purpose:

- runtime streaming control
- transcript durability
- UI rendering and eventing

This layered design is one reason the system remains understandable despite its complexity.

---

## 23. The hidden algorithm of the streaming subsystem

The response protocol subsystem can be summarized as a layered streaming algorithm:

```text
1. parse raw SSE into provider events
2. normalize provider events into `ResponseEvent`
3. maintain active per-item stream state
4. route text deltas through assistant-text parsers
5. route reasoning deltas through reasoning event paths
6. on item completion, classify as tool or non-tool output
7. convert completed output into transcript items and frontend items
8. if a tool was called, queue runtime action and prepare follow-up reasoning
```

This is an event-driven stream-processing architecture, not a classic request/response parser.

---

## 24. Why this architecture is strong

This design gives Codex several major advantages:

- low-latency incremental UI updates
- structurally reliable tool dispatch
- plan-mode extraction without separate provider protocols
- clean reasoning separation
- better replayability and transcript integrity
- explicit lifecycle events for frontends

It also gives the runtime a clear place to handle provider-specific oddities without contaminating the rest of the codebase.

---

## 25. What can go wrong if this subsystem is changed carelessly

### Risk 1: collapsing everything into text streaming

That would destroy clear distinctions between messages, reasoning, and tools.

### Risk 2: mixing provider wire details into the semantic loop

That would make the core runtime harder to maintain and more provider-coupled.

### Risk 3: breaking item lifecycle consistency

If item-added, delta, and item-done paths drift apart, frontend rendering and transcript persistence can become inconsistent.

### Risk 4: weakening plan-mode parsing boundaries

That could cause plan text to leak into normal assistant display or vice versa.

### Risk 5: treating tool-call parsing as text heuristics

That would regress one of Codex’s biggest reliability advantages.

---

## 26. How to extend this subsystem safely

If you add a new provider-side output kind or a new runtime interpretation layer, the usual safe process is:

1. define or identify the wire event
2. normalize it into a stable `ResponseEvent`
3. decide whether it should persist as a `ResponseItem`
4. decide whether it should produce a `TurnItem` or event-stream side effect
5. decide whether it should influence tool execution or follow-up continuation
6. preserve existing item lifecycle semantics

### Extension questions to ask

- Is this a transient stream event or a durable transcript item?
- Does it belong with assistant text, reasoning, tool invocation, or some new category?
- Should it affect frontend rendering immediately, only at completion, or both?
- Does it need a parser layer because streamed chunk boundaries are semantically messy?
- Does it require follow-up model reasoning after completion?

Those questions map directly onto the architecture.

---

## 27. Condensed mental model

Use this model when reading the code:

```text
SSE wire events
  -> `ResponseEvent`
  -> per-item stream state
  -> `ResponseItem` completion
  -> tool dispatch or non-tool turn item emission
  -> transcript persistence and follow-up reasoning
```

The most important takeaway is this:

- Codex treats model output as a typed streaming protocol whose events actively drive the agent runtime

That is the central property of the subsystem.

---

## Next questions to investigate

- Where exactly in `try_run_sampling_request(...)` are reasoning-delta events converted into specific frontend event types and persisted transcript state?
- How do review-mode or child-thread execution paths modify normal assistant-text emission semantics?
- Which provider-specific event kinds are intentionally ignored today, and why?
- How are citations represented internally after parser stripping, and what would be required to surface them in protocol events?
- How do `ResponseInputItem` and `ResponseItem` conversions preserve causality across multi-step tool loops?
