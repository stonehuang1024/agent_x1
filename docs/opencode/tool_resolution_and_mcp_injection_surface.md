# Tool Resolution / MCP Injection Surface

---

# 1. Module Purpose

This document explains how a session turn gets its executable tool surface, focusing on `resolveTools(...)` in `session/prompt.ts` and how MCP-projected tools are injected alongside built-in tools.

The key questions are:

- How does OpenCode build the per-turn tool map actually given to the model?
- How are built-in tools filtered and adapted from `ToolRegistry`?
- How are permissions, plugin hooks, metadata updates, and tool context wired into each tool execution?
- How are MCP tools transformed from external capability definitions into session-executable tools?
- Why does schema transformation happen at tool-resolution time rather than only at registration time?

Primary source files:

- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/tool/registry.ts`
- `packages/opencode/src/mcp/index.ts`
- `packages/opencode/src/provider/transform.ts`

This layer is OpenCode’s **per-turn executable tool-surface assembly layer**.

---

# 2. Why this layer matters

The session loop does not hand the model a global static tool catalog.

Instead, every turn builds a tool map that reflects:

- current agent policy
- current model/provider compatibility
- session permission state
- current conversation context
- connected MCP capabilities

`resolveTools(...)` is where all of that comes together.

So this function defines what the model is actually allowed and able to call on a given step.

---

# 3. Tool resolution is per-turn, not global

The loop calls:

- `resolveTools({ agent, session, model, tools, processor, bypassAgentCheck, messages })`

inside normal processing.

This is very important.

Tool availability is derived at runtime from the current turn’s concrete execution context.

That means tools are not just a static app-level registry problem.

They are part of session orchestration.

---

# 4. The output of `resolveTools(...)` is an AI SDK tool map

The function returns:

- `Record<string, AITool>`

This is the exact surface that is later passed into `processor.process(...)` and ultimately into `LLM.stream(...)`.

So `resolveTools(...)` is the final assembly point before provider invocation.

---

# 5. A shared `context(...)` factory wires runtime context into every tool call

A major part of `resolveTools(...)` is the `context(args, options)` helper.

This function constructs the `Tool.Context` passed to each tool and includes:

- session ID
- abort signal
- assistant message ID
- tool call ID
- extra metadata including model and `bypassAgentCheck`
- current agent name
- current conversation messages
- `metadata(...)` updater
- `ask(...)` permission helper

This is a key architectural seam.

Every resolved tool, whether built-in or MCP-backed, is adapted to the same execution context contract.

---

# 6. Why tool context is created at resolution time

The context depends on turn-specific state like:

- the current processor message ID
- the current session permissions
- current messages
- current model

So it cannot be a one-time static binding at tool registration time.

This is why per-turn resolution is the correct architecture.

---

# 7. `metadata(...)` lets tools update their live running tool part

The `metadata(...)` function looks up the persisted running tool part through:

- `input.processor.partFromToolCall(options.toolCallId)`

If that part is still running, it updates:

- title
- metadata
- running state
- input
- start time

This is an important runtime feature.

Tools can enrich the live persisted tool part while executing, not just return a final result at the end.

That improves observability and partial-progress reporting.

---

# 8. `ask(...)` merges agent and session permission rules

The permission helper uses:

- `PermissionNext.merge(input.agent.permission, input.session.permission ?? [])`

and attaches tool identity metadata.

This is crucial.

Tool execution authorization is not based solely on agent defaults or solely on session overrides.

It is based on the merged effective ruleset for this turn.

That is the correct place to combine long-lived session policy and current agent policy.

---

# 9. Built-in tools come from `ToolRegistry.tools(...)`

The first resolution phase iterates over:

- `await ToolRegistry.tools({ modelID, providerID }, input.agent)`

This means the built-in tool set is already filtered by:

- current provider/model identity
- current agent

So `ToolRegistry` is not just a bag of all tools. It is a policy-aware upstream source for turn resolution.

---

# 10. Why model/provider is part of tool selection

Some tools or schemas may depend on provider capabilities or model compatibility.

By querying the registry with the current model/provider identity, OpenCode can avoid exposing tools that are incompatible with the current execution target.

That keeps compatibility decisions closer to the actual runtime context.

---

# 11. Built-in tool schemas are provider-transformed before exposure

For each registry tool, `resolveTools(...)` computes:

- `ProviderTransform.schema(input.model, z.toJSONSchema(item.parameters))`

and uses that as the AI SDK `inputSchema`.

This is a very important detail.

Tool schemas are adapted to the current provider at the moment they are exposed to the model.

So provider compatibility applies to tool inputs as well as message payloads.

---

# 12. Why schema transformation belongs here

A tool may be globally registered once but used across many providers and models.

The provider-compatible schema may differ per turn.

So schema transformation at resolution time is the right design.

It avoids freezing one provider’s schema quirks into the global tool definition.

---

# 13. Built-in tools are wrapped with execution hooks and attachment normalization

For registry tools, the resolved AI SDK tool wrapper does several things:

- triggers `tool.execute.before`
- executes the underlying tool with the shared context
- assigns IDs/session/message metadata to returned attachments
- triggers `tool.execute.after`
- returns the normalized output

This means `resolveTools(...)` does more than selection.

It also standardizes tool execution behavior.

---

# 14. Why plugin hooks are wrapped here rather than inside every tool

This is a clean cross-cutting abstraction.

Instead of requiring every tool implementation to manually trigger before/after hooks, the resolution layer wraps them uniformly.

That keeps tool implementations simpler and enforces consistent lifecycle observability.

---

# 15. MCP tools are injected after built-in tools

The second phase iterates over:

- `Object.entries(await MCP.tools())`

This is the MCP injection surface.

Connected MCP server capabilities are projected into the same resolved tool map used by the main agent loop.

That is what makes MCP tools first-class during a turn.

---

# 16. MCP tool injection reuses the same provider schema adaptation step

For each MCP tool, `resolveTools(...)` computes:

- `ProviderTransform.schema(input.model, asSchema(item.inputSchema).jsonSchema)`

and replaces the MCP tool’s `inputSchema` with the provider-compatible version.

This is a strong architectural choice.

External tools and internal tools both pass through the same provider-compatibility boundary before model exposure.

That keeps the model-facing tool surface uniform.

---

# 17. Why MCP tools need wrapping even though they are already tools

The MCP layer already projected server capabilities into AI SDK-ish tools.

But `resolveTools(...)` still wraps them again.

This is necessary because the session runtime still needs to add:

- session-specific permission checks
- plugin lifecycle hooks
- per-turn context wiring
- output truncation and attachment normalization

So MCP projection and session resolution are complementary layers, not duplicates.

---

# 18. MCP tool execution is explicitly permission-gated in-session

Before executing an MCP tool, the wrapper does:

- `ctx.ask({ permission: key, patterns: ["*"], always: ["*"] })`

This is very important.

MCP connectivity alone does not imply unconditional execution permission.

Once projected into the session, the tool still participates in the same permission system as the rest of the runtime.

---

# 19. Why MCP tool permissions are keyed by resolved tool name

The permission uses the final MCP key, which is the namespaced tool identity coming out of `MCP.tools()`.

That is correct because the permission system must reason about the same tool identity the model actually sees and invokes.

Otherwise policy would drift from runtime reality.

---

# 20. MCP output is normalized from rich content items into text plus attachments

After an MCP tool executes, the wrapper iterates over `result.content` and separates:

- text items -> appended to `textParts`
- image items -> converted into `file` attachments with base64 data URLs
- resource items -> text and/or binary attachment extraction

This is an important adaptation step.

MCP result content can be richer and more protocol-specific than the internal tool-result shape expected by the session runtime.

The wrapper normalizes that into OpenCode’s attachment-and-output contract.

---

# 21. Why preserving `content` while also returning normalized `output` matters

The MCP wrapper returns both:

- normalized `output` and `attachments`
- original `content`

with a comment explaining that direct `content` return preserves ordering when outputting to the model.

This is a subtle but important design detail.

The runtime wants a normalized persisted representation, but it also wants to preserve the richer original content order for model-facing tool output semantics.

So it keeps both layers.

---

# 22. MCP textual output is truncated through the normal truncation path

The wrapper calls:

- `Truncate.output(textParts.join("\n\n"), {}, input.agent)`

and stores truncation metadata like:

- `truncated`
- optional `outputPath`

This is another sign that MCP tools are fully integrated into standard runtime policies.

They do not bypass output-size management just because they are external.

---

# 23. Attachments returned from MCP are normalized into session file parts

MCP-derived attachments are converted into `file`-like attachment objects and assigned:

- `id`
- `sessionID`
- `messageID`

This makes them look like native tool attachments in the persisted session model.

That is exactly what a good integration layer should do.

---

# 24. Built-in and MCP tools converge into one final map

After both phases, `resolveTools(...)` returns a single `tools` object.

The session loop does not distinguish later whether a tool came from:

- `ToolRegistry`
- `MCP.tools()`

That means the resolution layer is where origin-specific differences are absorbed.

After that, the execution surface is unified.

---

# 25. `bypassAgentCheck` reveals a subtle interaction with agent mentions

The resolution input includes:

- `bypassAgentCheck`

which earlier is derived from whether the latest user message had an `agent` part.

This means tool context can carry a flag indicating that the user explicitly invoked an agent and some normal agent checks should be relaxed.

That is a subtle but important part of how tool resolution is influenced by prompt-ingress semantics.

---

# 26. Why current messages are passed into tool context

The `messages` array is included in the per-tool context.

This means tools can inspect conversation history when needed.

So resolved tools are not isolated function calls.

They execute with awareness of the current conversation state.

That is especially important for contextual tools, subagents, and actions that need to reason about prior turns.

---

# 27. The tool-resolution layer is both selection and policy application

A useful way to think about `resolveTools(...)` is that it performs four jobs at once:

- selects which tools exist for this turn
- adapts schemas for the current provider/model
- wraps execution with runtime policy hooks and permissions
- normalizes outputs into session-compatible result shapes

That is why this function is such a central orchestration seam.

---

# 28. A representative tool-resolution lifecycle

A typical lifecycle looks like this:

## 28.1 Loop has current agent, model, session, and processor

- resolution starts for this specific turn

## 28.2 Built-in tools are fetched from `ToolRegistry`

- already filtered by agent/model context
- schemas transformed for current provider
- wrapped with hooks and session context

## 28.3 MCP tools are fetched from live MCP inventory

- schemas transformed again for current provider
- wrapped with hooks, permission checks, truncation, and attachment normalization

## 28.4 Unified tool map is returned

- later passed directly to model execution

This is the actual assembly pipeline for the turn’s executable tool surface.

---

# 29. Why this module matters architecturally

This layer shows how OpenCode keeps tooling flexible without making execution chaotic.

It allows:

- internal tools
- external MCP tools
- provider-specific schema compatibility
- session-specific permissions
- plugin observability

all to converge into one coherent tool surface for the model.

That is a sophisticated orchestration boundary.

---

# 30. Key design principles behind this module

## 30.1 Tool exposure should be decided per turn from current runtime context, not from a global static catalog alone

So `resolveTools(...)` depends on agent, model, session, messages, and processor state.

## 30.2 Internal and external tools should pass through the same session execution policy boundary

So both built-in and MCP tools receive provider schema transforms, hooks, permissions, and normalized outputs.

## 30.3 Provider compatibility for tool schemas belongs at the final model-exposure boundary

So `ProviderTransform.schema(...)` is applied during resolution, not baked permanently into tool registration.

## 30.4 Tool execution should be observable and stateful during a turn

So the resolution layer wires `metadata(...)`, `ask(...)`, plugin hooks, and message/attachment IDs into every execution.

---

# 31. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/session/prompt.ts`
2. `resolveTools(...)`
3. `packages/opencode/src/tool/registry.ts`
4. `packages/opencode/src/mcp/index.ts`
5. `packages/opencode/src/provider/transform.ts`

Focus on these functions and concepts:

- `resolveTools()`
- shared `context(...)`
- `ToolRegistry.tools(...)`
- `ProviderTransform.schema(...)`
- `tool.execute.before` / `tool.execute.after`
- MCP tool wrapping
- truncation of MCP text output
- attachment normalization
- merged permission rulesets

---

# 32. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: How exactly does `ToolRegistry.tools(...)` filter tools by agent and model, and what invariants does it enforce upstream of `resolveTools(...)`?
- **Question 2**: Should built-in tools also go through the same output truncation path that MCP tools currently use explicitly?
- **Question 3**: How should collisions between built-in tool IDs and MCP tool IDs be prevented or resolved over time?
- **Question 4**: Are there provider-specific schema transformations that still need tool-level special-casing beyond the current shared `ProviderTransform.schema(...)` path?
- **Question 5**: How should permission UX differ for MCP tools versus built-in tools if their risk profiles differ substantially?
- **Question 6**: Should `bypassAgentCheck` influence more of tool resolution than just the execution context passed to tools?
- **Question 7**: How should streaming or incremental MCP tool outputs integrate with the current normalized output-and-attachments contract?
- **Question 8**: What tests best guarantee that tool resolution remains deterministic across changing MCP inventories and provider schema quirks?

---

# 33. Summary

The `tool_resolution_and_mcp_injection_surface` layer is where OpenCode assembles the exact tool map a model can use on a given turn:

- built-in tools come from `ToolRegistry`, filtered by current agent/model context and provider-transformed for the current schema boundary
- MCP tools are injected from live capability inventory and wrapped so they obey the same session permissions, hooks, truncation, and attachment conventions
- every resolved tool receives per-turn execution context tied to the current processor message and session state

So this module is the convergence point where registry tools, MCP capabilities, provider compatibility, and session policy become one executable tool surface for the agent loop.
