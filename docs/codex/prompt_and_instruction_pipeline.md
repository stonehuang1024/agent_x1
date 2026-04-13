# Prompt and Instruction Pipeline in Codex

## Scope

This document explains how Codex constructs the model-facing prompt for a turn, and why the apparent simplicity of `build_prompt()` hides a much richer upstream pipeline.

The focus is not just on the final `Prompt` struct. The real subject is the full instruction pipeline that produces it:

- base instructions
- developer-side runtime updates
- contextual user-side updates
- AGENTS.md-derived instructions
- skill instructions
- model-visible tool specifications
- personality and output schema constraints

This module matters because Codex does not treat prompt engineering as a single string-rendering function. Instead, it treats prompt construction as a structured compilation process.

---

## 1. The key idea: the prompt is assembled, not authored in one place

A lot of agent systems implement prompt engineering as one giant function that concatenates text blocks. Codex does not do that.

Instead, the codebase spreads prompt construction across several layers:

- persistent base instructions
- turn-scoped updates
- history filtering
- user and skill instruction serialization
- tool visibility selection
- output-shape constraints

The final `build_prompt()` function is intentionally thin:

```text
Prompt {
  input,
  tools,
  parallel_tool_calls,
  base_instructions,
  personality,
  output_schema,
}
```

That thinness is not a sign of missing complexity. It is a sign that Codex has already normalized the complexity into upstream data structures.

---

## 2. Why Codex chooses a structured prompt pipeline

The architecture suggests several goals.

### Goal 1: separate stable instructions from volatile runtime state

A large static prompt is a poor representation for a system where:

- permissions can change
- sandbox settings can change
- collaboration mode can change
- realtime mode can start or stop
- model-specific instructions can change after a model switch

Codex solves this by separating:

- stable `BaseInstructions`
- dynamic contextual `ResponseItem`s

### Goal 2: make prompt construction replayable

If all prompt logic lived in ad hoc string builders, replaying a turn would require re-executing opaque rendering logic with hidden runtime dependencies.

Codex instead prefers explicit intermediate representations:

- `BaseInstructions`
- `DeveloperInstructions`
- `UserInstructions`
- `SkillInstructions`
- `ResponseItem`
- `Prompt`

That makes the prompt pipeline inspectable and much easier to reason about.

### Goal 3: keep tools as a first-class prompt component

Codex treats tool specifications as part of the model contract, not as prose buried in a long prompt paragraph.

This is a major architectural advantage. It means:

- tools are schema-bearing objects
- tool visibility can change independently from text instructions
- model capability flags can directly affect prompt shape

---

## 3. The final prompt object: what the model actually sees

The key assembly point in `codex-rs/core/src/codex.rs` is `build_prompt(...)`.

It produces:

- `input`
- `tools`
- `parallel_tool_calls`
- `base_instructions`
- `personality`
- `output_schema`

### `input`

This is the most information-dense field. It carries the model-visible conversation stream and contextual injections as `ResponseItem`s.

This includes things such as:

- user messages
- prior assistant outputs that should remain visible
- tool outputs from previous steps
- developer update messages
- contextual user-side updates
- AGENTS.md instructions
- skill messages

### `tools`

This is not a narrative description. It is the model-visible subset of `ToolSpec`s, already filtered by current runtime constraints.

### `parallel_tool_calls`

This tells the model whether the runtime and model capability layer allow parallel tool behavior.

### `base_instructions`

This is the closest thing to a classic system prompt, but even here Codex wraps it as structured data rather than treating it like an untyped string constant.

### `personality`

This gives the model an explicit style or communication-mode constraint without forcing that concern to be merged into the static base prompt.

### `output_schema`

This allows turn-scoped structured-output constraints to travel as first-class data.

The important observation is this:

- Codex sends a prompt object, not just a text transcript

That decision shapes the whole implementation.

---

## 4. `BaseInstructions`: the stable core instruction layer

`BaseInstructions` is defined in `codex-rs/protocol/src/models.rs` and maps directly to the provider-level `instructions` field.

Its default value comes from an included Markdown asset:

- `prompts/base_instructions/default.md`

### Why this abstraction matters

Codex could have passed a raw string around everywhere. Instead, it wraps the concept in a dedicated type.

That gives the system several benefits:

- the instruction layer has an explicit identity in the protocol model
- defaulting is centralized
- future variants or alternate instruction sources can be reasoned about structurally
- prompt assembly code does not need to know where the instructions came from

### Architectural meaning

`BaseInstructions` should be interpreted as the stable, thread-level behavioral contract for the assistant.

It is not intended to carry every dynamic constraint. That role belongs to the contextual input pipeline.

---

## 5. Dynamic instructions are not appended to base instructions

One of the most important design choices is that Codex does not keep rewriting the base instruction blob every turn.

Instead, dynamic instruction changes become separate contextual items.

This is visible in `context_manager/updates.rs`, which builds response items for turn-to-turn updates.

That means the overall instruction model is effectively:

```text
stable core rules
  + dynamic developer updates
  + dynamic contextual user updates
  + repository rules
  + skills
  + tool contracts
```

This is a much better fit for a long-lived agent than a monolithic system prompt.

---

## 6. Developer-side dynamic updates: the hidden power of the prompt pipeline

The `build_settings_update_items(...)` function is one of the most important pieces of prompt engineering in the codebase.

It creates prompt-visible update items for state changes such as:

- model switch instructions
- permission and sandbox changes
- collaboration mode changes
- realtime activation changes
- personality changes
- environment context diffs

### Why this is powerful

This function encodes an important philosophy:

- runtime state should become explicit model-visible context only when it changes

That avoids prompt spam and preserves context window budget.

### Internal shape of the result

The function may produce up to two classes of items:

- developer-role message items
- contextual user-role message items

This split is significant:

- behavior constraints and runtime rules belong on the developer side
- environment-like contextual updates can appear as contextual user material

That is a subtle but thoughtful division of semantic responsibility.

---

## 7. Environment updates: contextual user messages instead of blind repetition

`build_environment_update_item(...)` compares the previous context item and the next turn context by constructing `EnvironmentContext` values.

A crucial behavior is:

- if the contexts are equal except for shell details, no new item is emitted

### Why this matters

This means Codex is not naively restating the same runtime environment every turn.

Instead, it uses a diff-oriented model:

- emit only meaningful changes
- avoid wasting context space
- preserve the signal-to-noise ratio of the prompt

### Algorithmic interpretation

This is not a text-diff algorithm. It is a semantic-diff algorithm over normalized environment state.

That is much more robust than comparing rendered strings.

---

## 8. Permissions and sandbox changes become developer instructions

`build_permissions_update_item(...)` checks whether sandbox policy or approval policy changed since the previous turn context.

If nothing changed, it emits nothing.

If something changed, it generates `DeveloperInstructions::from_policy(...)`.

### Why developer-side injection is appropriate here

Permission policy is not a user message. It is a runtime operating constraint imposed on the assistant.

So this belongs in developer instructions rather than in normal conversation text.

### Why this is better than inline prose in a static prompt

Because the permission model may change during a session, Codex needs a representation that is:

- explicit
- diffable
- localized to the turn where the change occurred

That is exactly what the update-item model provides.

---

## 9. Model-switch instructions and personality updates

Two particularly elegant pieces of the update pipeline are:

- `build_model_instructions_update_item(...)`
- `build_personality_update_item(...)`

### Model-switch instructions

When the model changes, Codex does not silently continue with the previous implicit assumptions. Instead, it injects model-specific instructions derived from the next model’s metadata.

This matters because different models may require:

- different formatting expectations
- different tool-usage behavior
- different communication assumptions

### Personality updates

If personality features are enabled and the personality changes without the model changing, Codex can emit a dedicated personality-spec developer message.

This keeps style control orthogonal to base instructions and orthogonal to normal user history.

### Why this split is good engineering

It prevents the system from conflating:

- “what model is active”
- “how the assistant should speak”
- “what the environment constraints are”

Those are separate concerns and the prompt pipeline preserves that separation.

---

## 10. Realtime state as explicit prompt-visible control flow

The realtime update builders show another important pattern.

Codex can emit:

- realtime start instructions
- realtime end instructions

based on the transition between previous state and next turn state.

### Why this matters

Realtime conversation mode changes the behavioral contract of the assistant. It is not just a UI-side flag.

The model needs to be told when realtime mode begins or ends, and Codex makes that explicit.

### Architectural takeaway

Any runtime mode that changes how the assistant should behave is treated as prompt-relevant state, not as a hidden host-only setting.

That principle is central to the reliability of the system.

---

## 11. AGENTS.md instructions: repository rules become typed prompt fragments

In `core/src/instructions/user_instructions.rs`, `UserInstructions` are serialized into a structured text fragment wrapped by AGENTS.md fragment markers.

The type carries:

- `directory`
- `text`

and serializes them into a message-like textual fragment.

### Why this matters

Codex does not hardcode repository-specific behavior into the base prompt. Instead, it treats repository rules as contextual material.

This has several advantages:

- rules can vary by working directory
- rules are naturally layered with user or repo scope
- the base prompt stays reusable across repositories
- prompts remain explainable: repo rules are visible as separate injected fragments

### Why this is better than hidden policy logic

If repo rules were baked into runtime behavior without prompt visibility, the assistant could appear inconsistent or inexplicable to users. By injecting them explicitly, Codex aligns model behavior with visible context.

---

## 12. Skill instructions: capability fragments, not generic prose

`SkillInstructions` are also converted into `ResponseItem`s, but with a different semantic role.

They include:

- skill name
- skill path
- skill contents

The serialized form is wrapped using a skill fragment marker and embeds structured tags such as:

- `<name>`
- `<path>`

### Why this structure matters

Skills are not just extra chatter. They are task- or capability-oriented knowledge injections.

By labeling them structurally, Codex gives the model more context than plain prose would provide:

- where the instruction came from
- what it is called
- what capability domain it belongs to

### Architectural meaning

Repository rules tell the assistant how to behave in this codebase.

Skills tell the assistant how to perform a certain kind of task.

Both are injected as prompt-visible items, but they are intentionally not collapsed into one undifferentiated instruction blob.

---

## 13. Input history is already filtered before it reaches the prompt

One reason `build_prompt()` looks small is that `input` has already been curated upstream.

By the time prompt assembly occurs, the system has already:

- recorded only model-relevant items into history
- normalized items for model input
- removed or transformed content incompatible with the current model’s input modalities

This means prompt assembly is not where prompt selection happens. It is where prompt selection is finalized.

That distinction is important.

- selection mostly happens upstream in context management and runtime update generation
- final packaging happens in `build_prompt()`

---

## 14. Tool specs are prompt components, not comments in text

The `tools` field in the prompt is one of the most important deviations from traditional chatbot prompting.

Instead of describing tools only in natural language, Codex passes schema-driven tool specs that the model can call structurally.

### Why this matters for prompt engineering

This allows Codex to offload a lot of brittle natural-language instruction burden into a typed contract:

- tool names
- argument schemas
- output expectations
- tool visibility filtering

### Practical effect

The model does not need to infer from prose how to call tools. It receives explicit machine-readable affordances.

This makes the prompt both shorter and more reliable.

---

## 15. Personality and output schema: turn-scoped constraints, not static defaults

The prompt builder directly includes:

- `personality`
- `output_schema`

These are not always-global assumptions. They are turn-level controls.

### `personality`

This lets the runtime tune the assistant’s style or communication mode independently of the base instruction bundle.

### `output_schema`

This lets the caller require a structured final output shape for a particular turn.

### Why these belong in the prompt object

These constraints are not historical artifacts and not repo rules. They are active execution requirements.

Embedding them as typed prompt fields rather than text paragraphs makes the runtime more deterministic.

---

## 16. Parallel tool-call capability is part of prompt semantics

Another small but important field in the prompt is `parallel_tool_calls`.

This shows that Codex treats tool concurrency not just as a runtime implementation detail, but also as something the model should know.

### Why the model must know

If the model is allowed to plan multiple tool invocations in parallel, that changes its reasoning strategy.

So parallelism is not merely a backend optimization. It is part of the model-facing contract.

This is another example of Codex making operational semantics explicit in the prompt rather than leaving them implicit.

---

## 17. The hidden algorithm: prompt compilation by layered normalization

There is no single “algorithm” here in the numerical sense, but there is a very clear orchestration algorithm:

```text
1. load stable base instructions
2. compute turn-scoped runtime state
3. compare current state against prior reference state
4. emit only meaningful update items
5. collect repo rules and skill fragments
6. filter history to model-visible items only
7. select model-visible tool specs
8. package everything into a structured prompt object
```

This is best described as a prompt-compilation pipeline.

### Why that phrase is appropriate

Like a compiler, this pipeline:

- ingests distributed inputs from different layers
- normalizes them into intermediate representations
- removes irrelevant or redundant material
- emits a compact target representation for downstream execution

That is exactly what a mature agent runtime should do.

---

## 18. Why `build_prompt()` stays deliberately small

The small size of `build_prompt()` is an architectural win.

If that function started absorbing:

- environment diff logic
- AGENTS.md loading
- skill discovery
- permission prompt rendering
- history filtering
- tool selection

it would become a giant orchestration hotspot and a source of subtle bugs.

By keeping it minimal, Codex enforces a clean contract:

- upstream subsystems decide what belongs in the prompt
- `build_prompt()` only assembles already-normalized pieces

This is much easier to maintain.

---

## 19. Risks when modifying this subsystem

### Risk 1: collapsing structured instruction layers into one text blob

This would make diffs, replay, and targeted updates harder.

### Risk 2: moving dynamic state into base instructions

That would cause instruction duplication, larger prompts, and weaker change tracking.

### Risk 3: making repo rules implicit

If AGENTS.md behavior stops being represented as explicit prompt-visible fragments, the assistant may appear less predictable.

### Risk 4: letting tool instructions drift from tool specs

If natural-language tool guidance says one thing while the actual model-visible tool spec says another, tool reliability will degrade.

### Risk 5: skipping capability-aware filtering

Prompt components must remain aware of model modality and capability differences. Otherwise the runtime will build prompts the active model cannot reliably consume.

---

## 20. How to extend the prompt pipeline safely

If you need to add a new prompt-affecting behavior, the right questions are usually:

- Is this a stable thread-level instruction or a dynamic turn-level update?
- Should this be a developer message, a contextual user message, or a typed field on the prompt object?
- Should this appear every turn, or only when it changes?
- Does this belong in history so replay can reconstruct the behavior?
- Does this affect only text instructions, or also tool visibility and output constraints?

### Safe extension pattern

A robust extension often looks like this:

1. normalize the new concept into its own type or update builder
2. inject it as a `ResponseItem` or typed prompt field
3. keep `build_prompt()` simple
4. make sure history and replay semantics remain explicit

That pattern matches the rest of the codebase.

---

## 21. Condensed mental model

Use this model when reading the code:

```text
BaseInstructions
  = stable core instruction layer

ResponseItem input
  = dynamic model-visible context
    including history, tool outputs, repo rules, skills, and state diffs

Tool specs
  = machine-readable action contract

Prompt
  = final structured packaging of all normalized prompt components
```

The most important thing to remember is this:

- Codex does not rely on one giant system prompt
- Codex composes a model contract from multiple structured layers

That is the foundation of its prompt engineering approach.

---

## Next questions to investigate

- Where exactly are AGENTS.md files discovered, layered, and transformed into `UserInstructions` before they reach prompt assembly?
- How are skill-selection heuristics implemented before `SkillInstructions` are turned into `ResponseItem`s?
- Which prompt components are persisted into history versus injected transiently for the current turn only?
- How does `ContextManager::for_prompt(...)` interact with multimodal models when the history contains images or other modality-specific content?
- Which fields in the provider request correspond one-to-one with `Prompt`, and which require provider-specific translation?
