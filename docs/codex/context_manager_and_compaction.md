# Context Manager and Compaction in Codex

## Scope

This document explains how Codex manages model-visible history, tracks prompt pressure, preserves turn-to-turn context baselines, and decides when and how a conversation should be compacted.

The center of gravity is `codex-rs/core/src/context_manager/history.rs`, with related behavior in:

- `codex-rs/core/src/context_manager/updates.rs`
- `codex-rs/core/src/client.rs`
- the turn loop in `codex-rs/core/src/codex.rs`

This subsystem is crucial because Codex does not treat conversation history as a raw chat log. It treats history as a curated runtime artifact with invariants, accounting rules, and prompt-selection semantics.

---

## 1. The key idea: history is not just transcript, it is model state

A weak agent implementation stores whatever happened and replays it later. Codex does something more disciplined.

The `ContextManager` stores:

- the model-visible item stream
- token usage information
- a reference context snapshot used as the baseline for future context diffs

This already tells us that Codex views history as three things at once:

- a transcript
- a resource budget
- a baseline for future prompt updates

That is much more sophisticated than a plain list of messages.

---

## 2. The three fields that define the subsystem

The `ContextManager` owns:

- `items: Vec<ResponseItem>`
- `token_info: Option<TokenUsageInfo>`
- `reference_context_item: Option<TurnContextItem>`

### `items`

These are ordered from oldest to newest and represent the durable model-facing conversation substrate.

### `token_info`

This tracks the runtime’s best understanding of consumed prompt budget.

### `reference_context_item`

This is the most architecturally interesting field. It is the baseline used to compute model-visible settings updates for the next regular turn.

That means Codex does not just remember what was said. It also remembers the last known environment and runtime state that the model was effectively operating under.

---

## 3. Why a reference baseline exists at all

Many systems repeat environment and policy context every turn. Codex tries not to.

Instead, it stores a `reference_context_item` so the next turn can compute:

- what changed in the environment
- what changed in permissions
- what changed in collaboration mode
- what changed in realtime status
- what changed in personality or model instructions

### Why this is a major design win

Without a baseline, the system would face a bad tradeoff:

- either repeat too much state every turn
- or risk not telling the model about important runtime changes

The baseline allows a third option:

- inject only what changed

This is one of the strongest prompt-budget optimizations in the system.

---

## 4. `record_items(...)`: only model-relevant items are allowed in

One of the most important behaviors in the file is the filtering logic in `record_items(...)`.

It only persists:

- API-visible messages
- ghost snapshots in special handling paths

And it ignores other internal items.

### Why this matters

Codex draws a hard line between:

- runtime/UI/internal events
- model-visible context

That prevents a common failure mode in agent systems where telemetry or UI artifacts accidentally leak into the prompt.

### Invariant enforced here

The in-memory context history is not an event log. It is a curated prompt substrate.

That distinction is essential.

---

## 5. History is processed on the way in, not only on the way out

When items are recorded, Codex may preprocess them through `process_item(...)`.

The most visible case is tool output truncation.

### Why this preprocessing exists

Tool outputs can become very large. If Codex preserved raw outputs without bounds, then:

- history would grow uncontrollably
- replay would become expensive
- prompt budgeting would become unstable
- future turns could fail for preventable reasons

So tool outputs are normalized and truncated before being committed to durable history.

### Architectural interpretation

The history is not a perfect archival copy of everything the runtime ever saw.

It is a bounded, model-usable representation.

That is the right choice for an agent system.

---

## 6. `for_prompt(...)`: the final history preparation pass

When the session loop is ready to build model input, it clones history and calls `for_prompt(...)`.

This function does two main things:

- normalizes the history
- removes `GhostSnapshot` items from the prompt-facing result

### Why prompt preparation is a separate step

The runtime needs the flexibility to retain some internal history artifacts that should not always be shown to the model verbatim.

The prompt-facing view is therefore a projection of the stored state, not a blind dump.

### What `for_prompt(...)` guarantees

By the time its result is returned:

- call/output consistency has been repaired if needed
- unsupported image content has been stripped if necessary
- ghost snapshots have been excluded from the final prompt input

This is a key integrity boundary.

---

## 7. Normalization enforces structural invariants

The internal `normalize_history(...)` path enforces at least three important invariants:

1. every call has a corresponding output
2. every output has a corresponding call
3. unsupported image content is removed

### Why these invariants matter

If the call/output pairs are inconsistent, then prompt replay becomes semantically broken.

For example:

- a tool output without a tool call confuses the model about where the output came from
- a tool call without an output leaves unresolved execution state in the conversation

Codex repairs these invariants before building prompt input.

### Why this is a strong design choice

The system does not assume all upstream producers are perfectly tidy. Instead, it normalizes before model exposure.

That makes the runtime more resilient.

---

## 8. Multimodal filtering is capability-aware

A subtle but important feature of `for_prompt(...)` is modality-sensitive filtering.

When a model does not support image input, image content is stripped from messages and tool outputs.

### Why this matters

Codex supports multiple providers and models with different capabilities. A history that is valid for one model may not be valid for another.

### What this prevents

Without modality-aware filtering, the runtime could:

- send invalid payloads to text-only models
- overcount prompt cost for unsupported content
- create replay inconsistencies after a model switch

This is a good example of context management being capability-aware rather than purely data-retentive.

---

## 9. Token estimation is approximate by design

`ContextManager::estimate_token_count(...)` and related helpers use approximate token-count heuristics rather than exact tokenizer simulation.

### Why Codex uses approximation

A fully accurate token count for every item, every time, across multiple providers and content types would be expensive and awkward.

Instead Codex uses:

- byte-based heuristics
- estimated model-visible payload sizes
- image-specific approximations

### Why this is acceptable

The runtime does not need perfect token arithmetic for every decision. It mostly needs:

- a good lower-bound or rough estimate
- early warning for prompt pressure
- enough accuracy to decide when compaction or token-full behavior is necessary

This is a practical engineering tradeoff.

---

## 10. The image-cost estimator is more sophisticated than a naive byte count

One of the more interesting implementation details is the image handling path.

Rather than blindly charging raw base64 payload size, the estimator:

- detects image data URLs
- discounts raw payload bytes
- substitutes a per-image estimate
- handles `detail: original` differently through patch-based estimation
- caches some estimates for repeated payloads

### Why this matters

Raw serialized size is a bad proxy for how image inputs are charged by the model API.

So Codex adds a model-aware approximation layer instead of pretending base64 length equals prompt cost.

### Architectural lesson

The context subsystem is not only about preserving history. It is also about approximating provider-side economics well enough to drive runtime decisions.

---

## 11. Total token usage is not just the last API response

`get_total_token_usage(...)` does more than return the most recent provider-reported total.

It also accounts for:

- reasoning items the server may not have already included
- local items added after the last successful model-generated item

### Why this is necessary

The runtime can accumulate local context after a provider response:

- tool outputs
- developer messages
- contextual updates

If Codex only trusted the last provider total, it would undercount the actual next prompt cost.

### Important idea

Prompt pressure is partly server-reported and partly locally estimated.

That hybrid accounting model is much more realistic for an agent loop that continuously appends context between model requests.

---

## 12. The breakdown view is for operational introspection

`get_total_token_usage_breakdown(...)` exposes a more detailed accounting breakdown such as:

- last API response total tokens
- estimated model-visible bytes of all history
- estimated tokens added since last successful API response
- estimated bytes added since last successful API response

### Why this is useful

This is not just nice-to-have telemetry. It helps answer operational questions like:

- is prompt growth coming from the original transcript or from recent tool outputs?
- are we close to the limit because of model-side cost or local append-heavy behavior?
- is compaction likely to help enough?

This makes the context system diagnosable rather than opaque.

---

## 13. `set_token_usage_full(...)` and explicit full-window marking

When Codex encounters a context-window-exceeded condition, the turn loop can call `set_total_tokens_full(...)`, which in turn marks usage as full at the context-window boundary.

### Why explicit full marking is important

Without this, the runtime might continue operating under stale or misleading estimates after a context-limit failure.

By marking the context as full, the system records a strong signal:

- the current history is effectively at capacity for this model configuration

### Why this is better than silent failure

Silent failure would leave the runtime guessing. Explicit marking allows future logic to react more conservatively and transparently.

---

## 14. History trimming operations are carefully defined around user turns

The context manager also supports operations like:

- removing the first item
- removing the last item
- dropping the last `n` user turns
- replacing tool-originated images with placeholders

### Why user-turn trimming is the chosen semantic boundary

A turn is not just any item. It is usually anchored by a user message boundary.

So when the runtime needs rollback-like behavior, trimming by user-turn boundaries is more semantically meaningful than removing arbitrary trailing items.

### Why normalization is tied to removal

When removing an item, the system also removes corresponding call/output counterparts if needed. This preserves structural invariants without requiring a full normalization pass every time.

That is a subtle but useful correctness detail.

---

## 15. Compaction is not generic summarization

`compact_conversation_history(...)` in `core/src/client.rs` makes a unary compact request that returns a new list of `ResponseItem`s.

What matters is not only that compaction exists, but how it is framed.

The compaction request still carries:

- instructions
- formatted input
- tools
- parallel tool-call capability
- reasoning configuration
- text/output-schema configuration

### Why this is significant

This means Codex does not summarize history in a vacuum.

It compacts history in the active runtime context of the agent.

That is much better than a naive summary because the compacted transcript can remain compatible with:

- the current model
- the current tool contract
- the current output requirements

In other words, compaction is continuity-preserving, not merely compression-oriented.

---

## 16. Why compaction belongs in the model client path

The compact operation is executed as a model-backed unary API call rather than as a local heuristic rewrite.

### Why this is the right tradeoff

The runtime wants compaction that preserves semantic utility, not just token count.

A model can produce a better compressed continuation context than a rule-based truncator in many cases, especially when tool outputs and reasoning need to remain meaningful.

### But Codex still keeps local structure around it

Even though compaction is model-assisted, the surrounding runtime still controls:

- when compaction is triggered
- what prompt contract is supplied to the compact endpoint
- how the resulting `ResponseItem`s replace or rebuild history

So Codex delegates compression intelligence, but not orchestration authority.

---

## 17. How compaction integrates with the turn loop

The turn loop monitors usage after sampling passes and can trigger compaction when:

- token pressure is too high
- and a follow-up step is still needed

This is a subtle but important policy.

### Why compaction is tied to follow-up need

If a turn is already semantically complete, compaction pressure may be less urgent.

But if the runtime must continue with another model step, then preserving enough prompt headroom becomes immediately necessary.

### Operational significance

Compaction is not just a background maintenance action. It is part of turn-continuation control.

That is why the loop and context subsystem are so tightly coupled.

---

## 18. The hidden algorithm of the context subsystem

A useful way to describe this subsystem is as a bounded-state maintenance algorithm:

```text
1. accept new model-relevant items only
2. preprocess oversized or expensive payloads
3. preserve structural call/output invariants
4. keep a reference baseline for future state diffs
5. estimate prompt cost using hybrid accounting
6. expose a prompt-safe normalized projection
7. trigger compaction when semantic continuation and token pressure require it
```

This is not a pure storage system. It is a runtime control system for prompt continuity.

---

## 19. What can go wrong if this module is changed carelessly

### Risk 1: leaking internal events into prompt history

If `record_items(...)` becomes too permissive, the model may start seeing internal runtime noise.

### Risk 2: breaking call/output invariants

If normalization behavior is weakened, replay and tool continuity can become inconsistent.

### Risk 3: miscounting prompt pressure badly enough to destabilize continuation

If token estimation becomes too naive, the system may compact too late or too aggressively.

### Risk 4: losing the reference baseline

If the baseline model is removed or bypassed, the runtime may revert to expensive full-state reinjection every turn.

### Risk 5: making compaction oblivious to tool and output context

If compaction is treated as generic summarization, the resulting compressed history may no longer support the next agent step properly.

---

## 20. How to extend this subsystem safely

If you need to add a new kind of model-visible context, ask the following questions first:

- Should it be recorded permanently or only projected for one turn?
- Does it require normalization behavior?
- Does it participate in structural pairing invariants?
- Does it significantly affect token estimation?
- Should it influence the next-turn baseline diff?

### Safe extension pattern

A good extension usually follows this sequence:

1. define how the new item is represented as a `ResponseItem`
2. decide whether `record_items(...)` should persist it
3. teach normalization whether it has structural pairings or modality constraints
4. account for its prompt cost in estimation logic if needed
5. decide whether it should be preserved through compaction

That sequence keeps the subsystem coherent.

---

## 21. Condensed mental model

Use this model when navigating the code:

```text
ContextManager
  = curated model-visible history
  + token accounting
  + next-turn context baseline

for_prompt()
  = normalized, capability-safe projection for the model

reference_context_item
  = baseline for diff-style runtime reinjection

compaction
  = model-assisted transcript compression performed in the current agent contract
```

The single most important conclusion is this:

- Codex manages context as an active runtime resource, not as a passive chat log

That design choice is one of the main reasons the system can support long-running tool-augmented turns.

---

## Next questions to investigate

- Where exactly is `reference_context_item` updated across normal turns, compaction turns, and replay/reconstruction paths?
- How does the compacted history get merged back into live session state after a successful compact request?
- Which `ResponseItem` categories are intentionally preserved through compaction, and which are most likely to be summarized away?
- How do contextual user messages interact with user-turn boundary detection during rollback or turn dropping?
- Are there any provider-specific token-accounting adjustments beyond the generic heuristic and image-cost estimator?
