# MCP Capability Inventory / Tools Prompts and Resources

---

# 1. Module Purpose

This document explains how OpenCode turns connected MCP clients into usable capability inventories, focusing on tools, prompts, resources, and the direct fetch helpers built on top of them.

The key questions are:

- How do connected MCP clients become visible as OpenCode tools, prompts, and resources?
- Why are tools, prompts, and resources inventoried through separate helper functions?
- How are names sanitized and keyed to avoid collisions or invalid identifiers?
- What happens when capability listing fails after a server was previously connected?
- How do the rest of OpenCode’s runtime layers consume these inventories?

Primary source files:

- `packages/opencode/src/mcp/index.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/command/index.ts`
- `packages/opencode/src/server/routes/experimental.ts`

This layer is OpenCode’s **MCP capability inventory and projection surface**.

---

# 2. Why this layer matters

Connecting to an MCP server is only the first half of the problem.

The second half is:

- projecting the connected server’s capabilities into the rest of OpenCode in usable forms

That includes:

- AI SDK tools for agent execution
- prompt templates for command-like prompt reuse
- resources for external knowledge or content access

This projection layer is what turns MCP from a transport integration into a real extensibility mechanism.

---

# 3. Capability inventory depends on live connection state

The exported helpers:

- `MCP.tools()`
- `MCP.prompts()`
- `MCP.resources()`
- `MCP.getPrompt(...)`
- `MCP.readResource(...)`

all depend on the current set of live clients from:

- `MCP.clients()`
- `MCP.status()` / internal state

So inventories are not static config-derived catalogs.

They are runtime views over currently connected clients.

That is an important architectural point.

---

# 4. Why inventory is derived from connected clients rather than config alone

A configured MCP server may be:

- disabled
- failed
- needs auth
- disconnected
- connected

Only connected clients can reliably answer:

- what tools exist now
- what prompts exist now
- what resources exist now

So the inventory layer correctly derives from live clients instead of assuming config is enough.

---

# 5. `MCP.tools()`: tool inventory projection

`MCP.tools()` builds a record of AI SDK tools by:

- taking a snapshot of clients
- filtering to clients whose status is `connected`
- calling `client.listTools()` for each
- converting each MCP tool through `convertMcpTool(...)`
- keying the result by sanitized client name plus sanitized tool name

This is the main MCP-to-tool-runtime projection path.

---

# 6. Why `MCP.tools()` re-lists tools rather than trusting connect-time discovery

The runtime already validates `listTools()` during connection.

But `MCP.tools()` calls `listTools()` again at inventory time.

This is important because tool availability may change after connection, and the MCP protocol explicitly supports tool-list change notifications.

So the runtime does not treat tool inventory as permanently fixed after connect.

That is the right choice for dynamic capability systems.

---

# 7. Failure during `MCP.tools()` updates status aggressively

If `client.listTools()` fails during inventory building, the runtime:

- logs the error
- marks that client status as `failed`
- removes the client from live state
- skips projecting tools for it

This is a strong consistency policy.

A client that can no longer enumerate tools is no longer considered healthy enough to remain in the connected runtime set.

---

# 8. Why this failure policy is reasonable

For OpenCode, tools are a primary MCP capability.

If a server cannot list them anymore, it is not just mildly degraded. It is likely not operational for the main use case.

So downgrading it to failed state is a coherent operational decision.

---

# 9. Tool names are sanitized and prefixed by client

For each tool, the runtime computes:

- sanitized client name
- sanitized tool name
- final key: `client_tool`

where sanitization replaces non-alphanumeric, non-underscore, non-dash characters with `_`.

This is an important projection detail.

It produces keys that are safer for internal tool registries and less likely to violate naming assumptions elsewhere in the runtime.

---

# 10. Why client-prefixed tool names matter

Two different MCP servers can expose tools with the same name.

Without prefixing, collisions would be common.

Prefixing each tool with its client identity gives OpenCode a simple namespace strategy:

- tool identity = server namespace + tool name

That is exactly what a multi-server capability system needs.

---

# 11. `convertMcpTool(...)` is the actual projection seam

Each listed MCP tool is converted via:

- `convertMcpTool(mcpTool, client, timeout)`

This is the key seam between:

- MCP protocol tool definitions
- OpenCode’s AI SDK tool abstraction

The resulting tool is what the rest of the agent runtime can actually execute.

So inventory and execution projection are tightly linked.

---

# 12. Timeouts flow from MCP config into projected tools

When building tools, the runtime computes timeout from:

- per-server `entry?.timeout`
- or global experimental `mcp_timeout`

That timeout is passed into `convertMcpTool(...)` and then into actual tool execution calls.

This is an important detail.

Capability projection is not just about naming and schemas. It also carries operational constraints like timeout policy into the projected runtime object.

---

# 13. `MCP.prompts()`: prompt inventory projection

`MCP.prompts()`:

- takes a client snapshot
- filters to connected clients
- calls `fetchPromptsForClient(...)`
- flattens entries from all clients into one object

Each prompt record retains:

- its MCP prompt metadata
- the owning client name

This is the main prompt-catalog projection path.

---

# 14. Why prompts are projected separately from tools

MCP prompts are not executable tools.

They are reusable prompt templates or prompt-producing assets.

So they need a different consumption path than tool execution.

Keeping them in a separate inventory:

- avoids conflating capabilities with different semantics
- lets prompt consumers reason about them as prompt sources, not callable tools

That is correct architecture.

---

# 15. Prompt keys use a different namespace convention

`fetchPromptsForClient(...)` generates keys like:

- `client:prompt`

rather than `client_prompt`.

This is interesting.

The prompt layer uses a colon separator while tools use underscore concatenation.

That means different capability kinds are projected with slightly different naming idioms.

This is source-grounded and worth noticing.

---

# 16. Why prompt projection retains the original prompt name too

The inventory key is sanitized and namespaced, but the stored prompt info still contains the underlying prompt metadata and original prompt name.

That is important because consumers may need:

- a safe registry key for indexing
- the original MCP prompt identity for fetch calls back to the server

So the projection preserves both levels of identity.

---

# 17. `MCP.resources()`: resource inventory projection

`MCP.resources()` mirrors the prompt path:

- take client snapshot
- keep only connected clients
- call `fetchResourcesForClient(...)`
- flatten inventories from all clients into a single object

Each resource entry also retains its owning client.

This is the resource-catalog projection surface.

---

# 18. Why resources are not fetched eagerly in full

The inventory only lists resources.

Actual content retrieval happens separately through:

- `MCP.readResource(clientName, resourceUri)`

That is a very good design.

Resources may be heavy or dynamic, so inventory should expose metadata first and content only on demand.

This is the same general pattern used in many well-designed capability systems.

---

# 19. `fetchPromptsForClient(...)` and `fetchResourcesForClient(...)` are normalization helpers

These helpers do three important things:

- ask the client for the underlying MCP list
- sanitize names into safe keys
- attach `client` ownership metadata to each returned entry

That means they are not just thin wrappers.

They are normalization points that shape external MCP metadata into OpenCode-ready inventory entries.

---

# 20. Why attaching `client` ownership metadata is essential

Later consumers need to know:

- which MCP client to call back into for `getPrompt(...)`
- which MCP client to call back into for `readResource(...)`

So the flattened inventory cannot discard source ownership.

The client field is the bridge back to the live MCP connection.

---

# 21. `MCP.getPrompt(...)`: on-demand prompt materialization

Prompt inventory alone does not give the full rendered prompt content.

`MCP.getPrompt(clientName, name, args?)`:

- looks up the live client by name
- calls `client.getPrompt(...)`
- returns the server response or `undefined` on failure

This means prompt inventory and prompt materialization are intentionally separate stages.

---

# 22. Why prompt retrieval is on-demand

Prompt templates may require arguments and may be dynamic.

So `MCP.prompts()` gives the prompt catalog, while `MCP.getPrompt(...)` performs actual retrieval when a concrete prompt instance is needed.

That is a clean split between:

- capability discovery
- capability invocation

---

# 23. `MCP.readResource(...)`: on-demand resource materialization

`MCP.readResource(clientName, resourceUri)` follows the same pattern:

- find the live client
- call `client.readResource({ uri })`
- return result or `undefined` on failure

Again, this separates:

- metadata inventory
- content retrieval

That is strong design.

---

# 24. Main runtime consumers reveal how these inventories are actually used

The grep results show several direct consumers:

## 24.1 Session tool resolution

`session/prompt.ts` iterates over:

- `Object.entries(await MCP.tools())`

So connected MCP tools are injected directly into the session tool universe.

## 24.2 Command prompt registry

`command/index.ts` iterates over:

- `Object.entries(await MCP.prompts())`

and later uses:

- `MCP.getPrompt(...)`

So MCP prompts become part of the broader command/template system.

## 24.3 Experimental route resource surface

`server/routes/experimental.ts` returns:

- `await MCP.resources()`

So MCP resources are also surfaced through HTTP inventory routes.

This is strong evidence that the inventory layer is the real projection hub.

---

# 25. Why this makes MCP a first-class extensibility substrate

Because tools, prompts, and resources are projected into multiple subsystems:

- session agent execution
- command prompt lookup
- HTTP resource inventory

MCP is not isolated to one corner of the app.

It becomes a cross-cutting capability provider.

That is exactly what a good MCP integration should achieve.

---

# 26. Tool-list-changed notifications fit naturally into this model

The earlier `ToolsChanged` event matters even more in this context.

If MCP tool inventories can change at runtime, then a dynamic `MCP.tools()` inventory function plus a `ToolsChanged` notification is the correct pair:

- notification says inventory changed
- inventory helper rebuilds the current projected catalog

That is a coherent dynamic capability model.

---

# 27. A representative end-to-end projection flow

A typical flow looks like this:

## 27.1 MCP server connects successfully

- client becomes part of live instance state

## 27.2 Runtime asks for projected capabilities

- `MCP.tools()` / `MCP.prompts()` / `MCP.resources()`

## 27.3 Inventory helpers normalize names and attach client ownership

- safe keys for OpenCode registries
- original server linkage retained

## 27.4 Consumers use projected inventories

- session loop injects tools
- command registry exposes prompts
- routes expose resources

## 27.5 On-demand helpers materialize actual prompt/resource content

- `MCP.getPrompt(...)`
- `MCP.readResource(...)`

This is the true capability projection pipeline.

---

# 28. Important caveats in the current design

Several source-grounded caveats are worth noting:

- tool, prompt, and resource naming conventions are not perfectly uniform across capability types
- sanitization can still create collisions in edge cases
- prompt/resource inventory failures do not appear to downgrade client status as aggressively as tool-list failures do
- the inventory helpers rely on live connection state, so availability is inherently dynamic and instance-scoped

These are not necessarily flaws, but they are important contract realities.

---

# 29. Key design principles behind this module

## 29.1 External MCP capabilities should be projected into OpenCode-native abstractions, not left as raw protocol objects

So tools become AI SDK tools, while prompts and resources become normalized inventory entries.

## 29.2 Capability discovery and capability materialization should be separate stages

So inventories list prompts/resources, and dedicated helpers fetch actual prompt or resource content on demand.

## 29.3 Multi-server capability systems need namespacing and normalization

So the runtime sanitizes names and prefixes them with client identity.

## 29.4 Runtime inventories should derive from live client health, not merely from configuration

So only connected clients contribute to projected tool, prompt, and resource catalogs.

---

# 30. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/mcp/index.ts`
2. `convertMcpTool(...)`
3. `fetchPromptsForClient(...)`
4. `fetchResourcesForClient(...)`
5. `packages/opencode/src/session/prompt.ts`
6. `packages/opencode/src/command/index.ts`
7. `packages/opencode/src/server/routes/experimental.ts`

Focus on these functions and concepts:

- `MCP.tools()`
- `MCP.prompts()`
- `MCP.resources()`
- `MCP.getPrompt()`
- `MCP.readResource()`
- tool key sanitization
- prompt/resource key sanitization
- connected-client filtering
- status downgrade on tool inventory failure

---

# 31. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Should tool, prompt, and resource key naming conventions be unified more explicitly across capability types?
- **Question 2**: How should sanitization collisions be detected and handled when two capabilities normalize to the same key?
- **Question 3**: Should prompt and resource listing failures also downgrade MCP client status as aggressively as tool-list failures?
- **Question 4**: What caching, if any, should exist for capability inventories between dynamic change notifications?
- **Question 5**: How should consumers react to `ToolsChanged` events in practice, especially if a tool disappears mid-session?
- **Question 6**: Are there capability types in MCP beyond tools/prompts/resources that OpenCode should project in a similar way later?
- **Question 7**: How should prompt arguments be surfaced more richly in the command system for MCP-backed prompts?
- **Question 8**: What is the right long-term contract for instance-scoped MCP inventories in multi-client or multi-instance environments?

---

# 32. Summary

The `mcp_capability_inventory_tools_prompts_resources` layer is the projection hub that makes live MCP connections useful throughout OpenCode:

- `MCP.tools()` turns connected server tools into AI SDK tools for agent execution
- `MCP.prompts()` and `MCP.resources()` build normalized capability catalogs from live clients
- `MCP.getPrompt()` and `MCP.readResource()` materialize those capabilities on demand
- session, command, and route layers consume these inventories directly

So this module is the layer where connected MCP servers stop being transport endpoints and become real, named runtime capabilities inside OpenCode.
