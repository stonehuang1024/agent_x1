# Tool System and Dispatch in Codex

## Scope

This document explains how Codex models tools, selects which tools are available for a given turn, exposes them to the model, parses tool calls from model output, dispatches them to handlers, and writes tool results back into the conversation state.

The main source files are:

- `codex-rs/core/src/tools/spec.rs`
- `codex-rs/core/src/tools/router.rs`
- `codex-rs/core/src/tools/registry.rs`
- `codex-rs/core/src/tools/parallel.rs`
- `codex-rs/core/src/stream_events_utils.rs`

This subsystem is one of the architectural cores of Codex. The project is not just “LLM plus some tools.” It is built around a structured tool runtime with explicit routing, payload typing, concurrency controls, and transcript reinjection semantics.

---

## 1. The key idea: tools are a runtime subsystem, not a prompt trick

Some agent systems treat tools as a lightweight add-on:

- show the model a list of functions
- parse a function call
- run the function

Codex goes much further. Its tool system has explicit layers for:

- tool eligibility per turn
- tool specification generation
- model-visible filtering
- payload normalization
- namespace-aware dispatch
- mutability and policy checks
- concurrency control
- error-class-specific reinjection behavior

That makes the tool system a true execution engine rather than a thin wrapper around provider-side function calling.

---

## 2. The end-to-end tool pipeline

A concise mental model is:

```text
TurnContext
  -> ToolsConfig
  -> tool specs + handlers + registry
  -> ToolRouter
  -> model-visible tool list
  -> model emits structured tool call
  -> ToolRouter::build_tool_call()
  -> ToolCallRuntime
  -> ToolRegistry::dispatch_any()
  -> handler execution
  -> ResponseInputItem tool output
  -> write back into conversation history
  -> model follow-up step
```

This pipeline reveals a major design principle:

- tool invocation is part of the conversational state machine, not an out-of-band side effect

That is why results are written back into history rather than merely displayed in a UI.

---

## 3. `ToolsConfig`: the turn-scoped tool policy compiler

The first important layer is `ToolsConfig` in `tools/spec.rs`.

It is not a simple boolean bag. It is the turn-scoped declaration of what tool world exists for the current execution.

### Some of the dimensions it carries

- available models
- shell tool type
- unified exec backend
- login shell allowance
- apply-patch tool type
- web-search mode and config
- image generation enablement
- agent roles
- search and tool-suggest enablement
- request-permissions support
- code mode flags
- js repl flags
- image-detail capability flags
- collaborative tools
- artifact tools
- request-user-input support
- agent-jobs tools

### Why this is the right abstraction

A turn’s tool surface depends on more than the user prompt. It depends on:

- model capability
- platform capability
- feature flags
- session source
- sandbox policy
- collaboration mode
- environment constraints

`ToolsConfig` acts like a compiler stage that reduces all of those variables into a coherent tool-policy object.

---

## 4. Why tool availability is dynamic rather than static

Codex does not ship a fixed static set of tools to the model on every turn.

That would be simpler, but it would create several problems:

- tools that are unsupported in the current environment would still be advertised
- subagents could see tools they should not use
- code-only or js-repl-only modes would not be enforceable
- model capability mismatches would leak into runtime failures

By calculating tool availability per turn, Codex can make the model-facing tool contract much more truthful.

That truthfulness is extremely important for agent stability.

---

## 5. `ToolSpec`: the model contract layer

Tool specifications are created in `tools/spec.rs` and exposed to the model via the prompt.

A `ToolSpec` typically carries information such as:

- tool name
- description
- argument schema
- output schema
- freeform or structured calling mode

### Why schema-bearing tool specs matter

Codex relies on structured tool definitions instead of vague prose. This provides:

- validation-friendly calling contracts
- less ambiguity in model behavior
- better portability across providers
- an easier path to generating protocol-compatible request payloads

### Important architectural point

Natural-language prompt instructions and tool specs are not interchangeable.

Tool specs are the executable interface contract. Prompt text may shape behavior, but the spec defines the callable surface.

---

## 6. `ToolRouter::from_config(...)`: where specs and handlers become a runtime router

The router is created by feeding `ToolsConfig` and dynamic tool sources into `ToolRouter::from_config(...)`.

The inputs can include:

- MCP tools
- app tools
- discoverable tools
- dynamic tools

The output is:

- a `ToolRegistry`
- a list of configured tool specs
- a model-visible filtered list of tool specs

### Why router construction is a distinct step

Codex needs a single object that can answer several runtime questions consistently:

- what tools exist?
- which are visible to the model?
- how should a tool call be parsed?
- which handler should receive it?
- does this tool support parallel execution?

The router is that coordinating object.

---

## 7. Model-visible tools are a filtered projection, not the full runtime universe

`ToolRouter` stores both:

- all configured specs
- `model_visible_specs`

This distinction is critical.

### Why the distinction exists

Some tools may exist at runtime for nested orchestration or internal code-mode workflows, but should not be directly exposed to the model in the current turn.

For example, code-mode nested tools can be hidden when `code_mode_only_enabled` is active.

### Architectural meaning

The runtime tool universe and the model-visible tool universe are not always identical.

That is a healthy abstraction boundary. It prevents internal orchestration tools from leaking into the public model contract unnecessarily.

---

## 8. `ToolCall`: the normalized invocation representation

After the model emits output, Codex does not dispatch raw `ResponseItem`s directly to handlers.

Instead, it normalizes them into `ToolCall` values with:

- `tool_name`
- `tool_namespace`
- `call_id`
- `payload`

The payload itself is a typed enum:

- `Function { arguments }`
- `ToolSearch { arguments }`
- `Custom { input }`
- `LocalShell { params }`
- `Mcp { server, tool, raw_arguments }`

### Why this normalization layer is necessary

Different providers and output item types express tool intent differently.

Codex needs one internal representation so that downstream execution paths do not care whether a call originated as:

- a standard function call
- a tool-search item
- a custom tool call
- a local shell call
- an MCP call disguised as a normal function-call-shaped item

This is a classic adapter-layer design and it is exactly the right choice here.

---

## 9. `build_tool_call(...)`: structured parsing of model output

`ToolRouter::build_tool_call(...)` converts a `ResponseItem` into an optional `ToolCall`.

### What it supports

It recognizes at least:

- `ResponseItem::FunctionCall`
- `ResponseItem::ToolSearchCall`
- `ResponseItem::CustomToolCall`
- `ResponseItem::LocalShellCall`

It also has logic to detect whether a nominal function call is actually an MCP invocation by asking the session to parse tool naming and namespace conventions.

### Why this is more robust than regex-based parsing

The model output is already structured. Codex preserves that structure all the way into execution.

That means it does not need brittle prompt parsing hacks to infer what the model intended.

This is one of the reasons Codex’s tool system is architecturally stronger than tool systems that rely on natural-language conventions.

---

## 10. MCP support is absorbed into the same call model

A particularly strong design choice is that MCP tool calls do not require a completely separate orchestration path.

Instead, `build_tool_call(...)` can reinterpret a function call as:

- `ToolPayload::Mcp { server, tool, raw_arguments }`

### Why this matters

It means Codex can unify:

- built-in tools
- custom tools
- MCP tools

under one dispatch pipeline.

This reduces conceptual fragmentation and makes the runtime far more extensible.

### Architectural lesson

Extensibility is better achieved by normalizing different tool sources into one invocation model than by adding special-case dispatch ladders for every tool family.

---

## 11. `ToolRegistry`: handler lookup by `name + namespace`

Once a `ToolCall` is ready, Codex uses `ToolRegistry` to dispatch the invocation.

The registry maps handlers by a key derived from:

- tool name
- optional namespace

### Why namespace matters

Without namespace support, external tool ecosystems would collide easily with built-in tools or with one another.

Namespace-aware lookup allows Codex to support:

- built-in tools
- MCP server tools
- future dynamic or plugin tools

without flattening everything into one global name table.

### Runtime meaning

The registry is the execution-side mirror of the tool spec layer.

It answers: if the model asks for this capability, who actually owns execution?

---

## 12. `dispatch_any(...)`: the real execution gateway

`ToolRegistry::dispatch_any(...)` is where tool execution becomes concrete.

This method does more than call a handler.

It also:

- updates active turn state tool-call counters
- computes telemetry metadata
- checks MCP server origin details when relevant
- verifies a handler exists
- verifies handler/payload kind compatibility
- records mutability-related behavior
- logs result telemetry

### Why this is important

The registry is not just a lookup table. It is also a runtime governance point.

That is where Codex ensures:

- the call is understood
- the payload matches the expected kind
- the invocation is observable
- the runtime can explain success or failure later

This is a major difference between a toy tool loop and a production-grade one.

---

## 13. Unsupported tool calls are surfaced back to the model, not silently ignored

If no handler exists for a requested tool, `dispatch_any(...)` produces a `FunctionCallError::RespondToModel(...)` rather than silently failing.

### Why this matters

Silence is poison in an agent loop. If the model thinks it called a tool and nothing happens, the conversation state becomes incoherent.

By responding explicitly, Codex lets the model see:

- the call was not supported
- the failure itself is part of the conversation state

This preserves agent coherence and creates the chance for self-correction.

---

## 14. Payload-kind compatibility is enforced explicitly

Even if a handler exists, `dispatch_any(...)` verifies that the handler matches the payload kind.

If it does not, the runtime returns a fatal error.

### Why this guard is necessary

Tool names alone are not enough to guarantee correctness. A tool might exist, but the invocation payload might be semantically incompatible.

This is especially important in a system that supports multiple payload families.

### Architectural consequence

Codex refuses to blur the line between:

- handler existence
- invocation validity

That is a good correctness boundary.

---

## 15. `ToolCallRuntime`: execution orchestration around the router and registry

`ToolCallRuntime` in `tools/parallel.rs` wraps the lower-level router and registry with turn-scoped execution behavior.

It holds:

- the router
- the session
- the turn context
- the shared turn diff tracker
- a concurrency lock used to control serial versus parallel execution

### Why this layer exists

The router knows how to interpret and dispatch a tool call.

The runtime layer knows how to execute it safely within the current turn, including:

- cancellation behavior
- concurrency constraints
- abort result shaping
- code-mode source distinctions

This is another example of Codex separating interpretation from orchestration.

---

## 16. Parallel execution is explicit and tool-aware

`ToolCallRuntime` checks whether a tool supports parallel execution by consulting:

- `self.router.tool_supports_parallel(&call.tool_name)`

Then it uses an `RwLock` strategy:

- parallel-capable tools take a read lock
- non-parallel tools take a write lock

### Why this is clever

This creates a simple concurrency gate:

- many parallel-safe tools can proceed together
- any non-parallel-safe tool gains exclusive execution

### Why this is better than a global boolean switch

Different tools have different interference characteristics.

A blanket “all tools parallel” or “all tools serial” policy would be too coarse.

Codex instead makes parallelism a per-tool property mediated by one shared turn-scoped concurrency primitive.

That is a strong engineering choice.

---

## 17. Cancellation produces structured aborted outputs

If cancellation occurs while a tool is running, `ToolCallRuntime` does not merely drop the task.

It constructs an aborted response using:

- the original call id
- the original payload
- a formatted abort message

### Why this matters

From the model’s perspective, an aborted tool is still part of the conversation history. The agent needs to know that the tool was interrupted.

That is why Codex emits a structured aborted output instead of silently vanishing the invocation.

### Why tool-specific abort messages exist

Shell-like tools get more specific abort messaging including wall time, because that information is useful for both users and the model.

This is a good example of the runtime preserving semantic value even in cancellation paths.

---

## 18. `handle_output_item_done(...)`: where model output becomes queued tool execution

In `stream_events_utils.rs`, `handle_output_item_done(...)` is the bridge from model output to runtime action.

When `ToolRouter::build_tool_call(...)` returns `Some(call)`, the function:

- logs the payload preview
- records the completed response item immediately
- creates a tool future using `ToolCallRuntime`
- marks `needs_follow_up = true`
- stores the future in the in-flight output structure

### Why immediate recording matters

The model-emitted tool call itself becomes part of durable conversation state before the tool finishes.

That is important for:

- replay
- debugging
- frontend visibility
- preserving causal ordering

### Why the tool is queued rather than blocking everything inline

The runtime wants to maintain a stream-processing loop and support multiple in-flight tool paths when allowed. Queueing the future keeps that orchestration flexible.

---

## 19. Tool errors are classified by recovery semantics

Codex distinguishes at least three important error classes during tool parsing and dispatch:

- `MissingLocalShellCallId`
- `RespondToModel(message)`
- `Fatal(message)`

### Why this classification is so important

Not every tool failure should kill the turn.

Some failures should simply become model-visible outputs so the model can adapt.

Others indicate a broken invariant or unrecoverable runtime problem and should terminate the turn.

### This is a maturity signal

A strong agent runtime should model tool errors by recovery semantics, not just by string messages. Codex does that.

---

## 20. Failure results are converted into transcript-compatible outputs

`dispatch_tool_call_with_code_mode_result(...)` and `failure_result(...)` convert non-fatal failures into structured outputs matching the payload family.

For example:

- tool-search failures yield empty search results
- custom-tool failures yield custom output text
- ordinary function-style failures yield `FunctionToolOutput`

### Why this matters

The model should receive failure information in the same structural channel it expects for that tool family.

That consistency helps the model recover more reliably than if every failure were turned into arbitrary prose.

---

## 21. Direct calls can be restricted by execution mode

The router dispatch path also enforces mode-specific policies.

For example, when `js_repl_tools_only` is active, direct tool calls other than the approved JS REPL tools are rejected with a model-visible response.

### Why this is important

Execution mode should not just influence which tools are shown. It must also constrain what actually executes, in case the model or an internal path attempts to bypass the intended mode boundary.

This is a defense-in-depth measure.

---

## 22. Tool results are not UI events only; they are model inputs for the next step

After tool execution completes, the result becomes a `ResponseInputItem`-compatible structure that is written back into the conversation history.

This is one of the most important properties of the entire tool system.

### Why this is crucial

A tool call is not the end of reasoning. It is usually an intermediate step.

The agent loop only works if the model can inspect the tool result as part of the next prompt.

That is why the tool pipeline is really a feedback loop, not a one-shot function-calling side branch.

### This is the core closure property

The tool system closes the loop:

- model decides to act
- runtime acts
- runtime converts action result back into model-readable state
- model continues

That is what makes Codex an actual tool-augmented agent.

---

## 23. `ToolRouter` versus `ToolRegistry` versus `ToolCallRuntime`

These three types can be confusing at first, so it is worth drawing the distinction clearly.

### `ToolRouter`

Responsible for:

- owning configured tool specs
- filtering model-visible tools
- parsing `ResponseItem` into `ToolCall`
- routing calls into the execution layer

### `ToolRegistry`

Responsible for:

- mapping `(name, namespace)` to a handler
- validating handler compatibility
- performing the concrete handler dispatch
- wrapping execution with telemetry and invariants

### `ToolCallRuntime`

Responsible for:

- running tool dispatch inside the turn runtime
- applying cancellation behavior
- enforcing per-tool parallelism rules
- returning transcript-compatible outputs

### Why this split is good

The split avoids mixing three different concerns:

- interpretation
- lookup and governance
- execution orchestration

That makes the subsystem easier to extend safely.

---

## 24. The hidden algorithm of the tool subsystem

The tool subsystem can be summarized as a staged execution algorithm:

```text
1. compile turn-specific tool policy
2. construct tool specs and handler registry
3. expose only the correct visible subset to the model
4. normalize model output into internal ToolCall values
5. validate call support and payload compatibility
6. execute with mode, policy, concurrency, and cancellation controls
7. convert result or failure into transcript-compatible output
8. append output to conversation state for follow-up reasoning
```

This is much closer to an execution runtime than to a simple function-call adapter.

---

## 25. What can go wrong if this subsystem is changed carelessly

### Risk 1: exposing tools the runtime cannot safely execute

If model-visible specs drift from actual runtime capability, the model will plan against a false contract.

### Risk 2: bypassing `ToolCall` normalization

If code paths start dispatching raw provider output items directly, support for multiple payload families and MCP adaptation will degrade.

### Risk 3: weakening handler/payload compatibility checks

That would create runtime ambiguity and harder-to-diagnose failures.

### Risk 4: treating failures as generic text instead of structured outputs

That would make recovery less reliable because the model would lose the expected tool-output shape.

### Risk 5: breaking turn-scoped concurrency control

If parallelism rules are loosened carelessly, tools with side effects may begin to interfere with one another in difficult-to-debug ways.

---

## 26. How to extend the tool system safely

If you add a new tool or tool family, the usual safe sequence is:

1. decide how the tool should appear in `ToolsConfig`
2. define a `ToolSpec` with the correct schema and visibility
3. register a handler under the correct name and namespace
4. define how the model output should normalize into `ToolPayload`
5. decide whether the tool supports parallel execution
6. define how failures should be represented back to the model
7. ensure the result shape becomes a valid `ResponseInputItem`

### Extension questions to ask

- Is this tool model-visible, runtime-only, or both?
- Is the payload best represented as function JSON, freeform custom input, or a special typed structure?
- Is the tool mutating?
- Is the tool safe to run in parallel?
- What should the model see if the tool is denied, missing, aborted, or partially successful?

Those questions map directly onto the architecture of the subsystem.

---

## 27. Condensed mental model

Use this model when reading the code:

```text
ToolsConfig
  = turn-specific policy for what tools should exist

ToolSpec
  = model-facing tool contract

ToolRouter
  = interpreter and coordinator for tool calls

ToolRegistry
  = handler lookup and guarded dispatch layer

ToolCallRuntime
  = execution orchestration with cancellation and concurrency controls

ResponseInputItem tool output
  = feedback channel back into the agent loop
```

The single most important takeaway is this:

- Codex treats tools as structured conversational actions whose results must re-enter the model’s world in a typed way

That is the defining property of the subsystem.

---

## Next questions to investigate

- How are individual tool handlers structured internally, especially mutating ones such as `apply_patch` and shell/unified exec tools?
- Where and how are approval prompts injected into the tool execution path for dangerous operations?
- Which tools are marked as parallel-safe today, and what policy rationale explains those choices?
- How do code-mode nested tools interact with the public tool surface across different collaboration modes?
- How does the runtime distinguish between tool outputs that should be compacted aggressively and those that should remain more verbatim for future reasoning?
