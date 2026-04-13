# MCP Runtime / Connection Auth and Capability Projection

---

# 1. Module Purpose

This document explains the deeper MCP runtime beneath the `/mcp` route surface, focusing on connection state, transport selection, OAuth-aware status handling, stored auth state, and how MCP capabilities become tools, prompts, and resources inside OpenCode.

The key questions are:

- How does the MCP runtime create and track client connections?
- How are remote and local MCP servers handled differently?
- How does OAuth affect connection status and recovery behavior?
- Why does the runtime keep per-instance MCP state while persisting auth material separately?
- How do MCP server capabilities become projected into the rest of OpenCode as tools, prompts, and resources?

Primary source files:

- `packages/opencode/src/mcp/index.ts`
- `packages/opencode/src/mcp/auth.ts`
- `packages/opencode/src/mcp/oauth-provider.ts`
- `packages/opencode/src/mcp/oauth-callback.ts`

This layer is OpenCode’s **MCP connection manager, auth-aware lifecycle runtime, and capability projection layer**.

---

# 2. Why this layer matters

The `/mcp` routes only expose lifecycle operations.

The deeper runtime is what actually decides:

- whether a server is connected
- whether it needs auth
- whether it is disabled or failed
- which transport succeeded
- how tools/prompts/resources are projected into the application

This is the real operational core of MCP integration.

---

# 3. MCP state is instance-scoped, but auth persistence is not

One of the most important architectural facts is that MCP uses two different storage models:

## 3.1 Instance-scoped runtime state

- active clients
- live connection status
- pending in-memory transport state

## 3.2 Persistent auth storage

- tokens
- dynamic client registration info
- OAuth code verifier/state
- stored on disk through `McpAuth`

This split is exactly what you want.

Live connections belong to runtime scope; credentials need durability across runs.

---

# 4. `MCP.Status`: explicit lifecycle states

The runtime defines a discriminated union for status:

- `connected`
- `disabled`
- `failed`
- `needs_auth`
- `needs_client_registration`

This is an excellent contract.

It distinguishes several materially different states that many systems would collapse into one vague “not connected” bucket.

That gives clients and operators much better observability.

---

# 5. Why `needs_auth` and `needs_client_registration` are distinct

These states represent different remediation paths.

## 5.1 `needs_auth`

- server can potentially proceed after the user authenticates

## 5.2 `needs_client_registration`

- server requires a client ID and cannot rely on dynamic client registration

That distinction is operationally important because the recovery step is different.

The runtime models that explicitly.

---

# 6. The MCP runtime defines its own event surface too

`mcp/index.ts` defines bus events including:

- `mcp.tools.changed`
- `mcp.browser.open.failed`

This is a strong sign that MCP is not just a config-and-connect subsystem.

It is integrated into the broader event-driven architecture and can notify the rest of the system when capabilities or UX conditions change.

---

# 7. `ToolsChanged`: capability inventory is dynamic

The runtime registers a notification handler for:

- `ToolListChangedNotificationSchema`

and publishes:

- `MCP.ToolsChanged`

This is important because MCP tool inventories can change at runtime.

OpenCode is designed to observe and react to that, not just snapshot tool lists once at startup.

---

# 8. `BrowserOpenFailed`: auth UX is part of runtime behavior

The `BrowserOpenFailed` event shows the runtime is aware that OAuth is not only a transport/auth matter.

It is also a user-experience event.

If a browser cannot be opened automatically, the rest of the application can react appropriately.

That is good platform thinking.

---

# 9. `state = Instance.state(...)`: connection state belongs to the bound instance

The MCP runtime keeps:

- `status`
- `clients`

inside `Instance.state(...)`.

That means MCP clients are scoped to the active instance, not globally shared across the entire process.

This is consistent with many other OpenCode subsystems and avoids uncontrolled global mutable connection state.

---

# 10. Why instance-scoped MCP clients make sense

Different instances may have:

- different current directories
- different config overlays
- different enabled/disabled MCP servers
- different lifecycle timing

So treating MCP connections as instance-local runtime state is sensible.

It keeps them aligned with the same scoping model used by sessions, config, and other runtime resources.

---

# 11. Initialization loads configured MCP servers eagerly

On state creation, the runtime:

- reads `cfg.mcp ?? {}`
- filters to valid configured entries
- marks disabled entries as `disabled`
- attempts to `create(...)` active ones
- stores resulting clients and status map

So MCP integration is not purely lazy on first explicit route call.

There is eager initialization behavior when the instance state is established.

---

# 12. Why eager initialization is useful

This gives the system early awareness of:

- which MCP servers are available
- which ones failed
- which ones require auth
- which are connected

That improves startup observability and lets route consumers ask for status immediately without forcing first-use connection attempts.

---

# 13. Cleanup on instance disposal is aggressive and practical

The instance-state cleanup path:

- looks for child-process PIDs on transports
- recursively finds descendants with `pgrep -P`
- sends `SIGTERM` to descendants
- closes all MCP clients
- clears pending OAuth transports

This is a very important implementation detail.

The runtime is explicitly trying to avoid orphaning local MCP subprocess trees.

---

# 14. Why descendant cleanup is necessary

The comments explain that some MCP servers spawn grandchildren the SDK does not close automatically.

So OpenCode compensates by killing the full descendant tree first.

This is exactly the kind of practical hardening a mature runtime needs.

It addresses real-world subprocess behavior rather than assuming the SDK handles everything cleanly.

---

# 15. `create(key, mcp)`: the heart of MCP connection logic

The `create(...)` function is the real connection state machine.

It decides how to:

- handle disabled entries
- connect remote servers
- connect local subprocess servers
- classify auth failures
- classify generic failures
- verify capability listing after connection

This is the central MCP runtime algorithm.

---

# 16. Remote MCP servers use transport fallback across protocol variants

For `mcp.type === "remote"`, the runtime tries two transports:

- `StreamableHTTPClientTransport`
- `SSEClientTransport`

This is important.

OpenCode does not assume one remote MCP transport style will always work.

It tries multiple protocol variants for compatibility.

That is a robust integration strategy.

---

# 17. Why transport fallback matters

Different MCP servers may support different transport styles or have quirks in one implementation path.

By iterating transports, OpenCode improves interoperability without forcing users to micromanage transport choice in the common path.

This is one of the strongest signs of production-minded runtime design in the MCP layer.

---

# 18. OAuth is enabled by default for remote servers unless explicitly disabled

For remote MCP servers, the runtime interprets:

- `mcp.oauth === false`

as explicit disablement.

Otherwise it constructs an `McpOAuthProvider` by default.

This is a major product choice.

It assumes remote servers commonly need OAuth-capable auth handling and builds that into the default runtime behavior.

---

# 19. The OAuth provider is attached at transport construction time

Both remote transports receive:

- `authProvider`

when OAuth is enabled.

That means authentication is not a separate outer wrapper step after transport creation.

It is part of the transport/client interaction model itself.

This is the right place for it.

---

# 20. `withTimeout(...)` protects MCP connection attempts

Both remote and local connection attempts are wrapped with:

- `withTimeout(..., connectTimeout)`

This is important operational hygiene.

External servers may hang during startup or negotiation.

The runtime ensures those hangs become bounded failures rather than indefinite stalls.

---

# 21. Auth-related connection failures are classified intentionally

When remote connection fails, the runtime checks whether the failure is:

- `UnauthorizedError`
- or an OAuth-related error when an auth provider is attached

Then it branches into:

- `needs_client_registration`
- or `needs_auth`

This is a key classification step.

It turns low-level transport/auth failures into meaningful runtime status states.

---

# 22. Why toast notifications are emitted on auth-required states

When auth is required, the runtime publishes:

- `TuiEvent.ToastShow`

with warning messages.

This is an important cross-layer integration detail.

The MCP runtime is not isolated from user experience.

It actively informs the TUI when operator intervention is needed.

That makes the runtime more usable and discoverable.

---

# 23. Pending OAuth transports are stored in memory

For remote auth-required cases, the runtime stores:

- `pendingOAuthTransports.set(key, transport)`

This is a subtle but important detail.

The runtime is preserving enough transport context to finish auth later without reconstructing everything blindly.

That is part of the connection/auth lifecycle handshake.

---

# 24. Local MCP servers use subprocess stdio transport

For `mcp.type === "local"`, the runtime builds:

- `StdioClientTransport`

with:

- command and args
- `cwd: Instance.directory`
- merged environment

So local MCP integration is really subprocess-based capability hosting.

That is a different operational model from remote HTTP/SSE MCP servers.

---

# 25. Why local MCP uses the current instance directory

`cwd` is set to:

- `Instance.directory`

This ties local MCP server execution to the active project context.

That makes sense for tools or resources that need to operate relative to the codebase currently being worked on.

It is consistent with OpenCode’s broader context-binding model.

---

# 26. `BUN_BE_BUN` is injected for local `opencode` command servers

When the command is `opencode`, the runtime injects:

- `BUN_BE_BUN: "1"`

This is a notable runtime compatibility detail.

It suggests the MCP layer contains explicit accommodations for running OpenCode-provided subprocess servers correctly under Bun.

---

# 27. Capability verification happens after connection

Even after a client connects, `create(...)` immediately calls:

- `mcpClient.listTools()`

If that fails, the client is closed and status becomes `failed`.

This is extremely important.

The runtime does not equate “transport connected” with “usable MCP server.”

It verifies that core capability discovery actually works.

---

# 28. Why post-connect verification is strong design

A server that technically accepts a connection but cannot enumerate tools is not useful to OpenCode’s runtime.

By verifying tool listing early, the runtime catches partial or broken MCP integrations sooner and reports them as failed rather than connected.

That is the right operational choice.

---

# 29. Capability projection: MCP tools become AI SDK tools

The runtime includes:

- `convertMcpTool(...)`

which takes an MCP tool definition and returns an AI SDK `dynamicTool(...)`.

This is one of the most important projection layers in the whole MCP subsystem.

It is how external MCP tool definitions become usable by OpenCode’s tool-execution runtime.

---

# 30. Why `convertMcpTool(...)` is architecturally significant

This function is the seam between:

- MCP protocol tool definitions
- OpenCode’s model/tool runtime built around AI SDK tool abstractions

That means MCP is not just an isolated integration.

Its capabilities are projected into the same general tool-execution universe that the rest of the system can consume.

This is the essence of MCP as an extensibility mechanism.

---

# 31. Prompt and resource projection follow similar patterns

The runtime includes helper functions like:

- `fetchPromptsForClient(...)`
- `fetchResourcesForClient(...)`

These functions:

- list prompts/resources from the MCP client
- sanitize names into stable keys
- annotate each with the client name

So MCP projection is not limited to tools.

It also brings external prompts and resources into OpenCode’s command/resource model.

---

# 32. Why sanitization and client-prefixed keys matter

The runtime builds keys from:

- sanitized client name
- sanitized prompt/resource name

This helps avoid collisions and produce safer internal identifiers.

That is important because MCP servers are external systems and their names may not map cleanly to OpenCode’s own naming assumptions.

---

# 33. `McpAuth`: persistent MCP auth store

`mcp/auth.ts` stores:

- tokens
- client registration info
- code verifier
- OAuth state
- server URL

in a JSON file under:

- `Global.Path.data/mcp-auth.json`

This is the durable credential/state layer for MCP OAuth.

---

# 34. Why server URL is stored with credentials

`McpAuth.getForUrl(...)` validates that stored credentials belong to the same server URL.

If the URL changed, credentials are treated as invalid.

This is a very good safety measure.

It prevents silent reuse of tokens or dynamic registration info against a different server endpoint just because the logical MCP name stayed the same.

---

# 35. Why OAuth provider logic owns both dynamic registration and tokens

`McpOAuthProvider` can:

- provide client metadata
- read/save registered client info
- read/save tokens
- save code verifier
- save/generate OAuth state
- invalidate credentials

This is a comprehensive integration layer between the MCP SDK’s OAuth client expectations and OpenCode’s persistent auth storage.

That is why the provider class is central to the runtime.

---

# 36. State generation behavior is intentionally resilient

If `state()` is requested and no OAuth state has been pre-saved, `McpOAuthProvider` generates a random new state value and persists it.

This is a pragmatic design choice.

It acknowledges that the SDK may ask for state generation implicitly, so the provider cannot assume a pre-seeded value always exists.

That makes the auth flow more robust.

---

# 37. Runtime UX and protocol logic are deliberately coupled in places

The MCP runtime:

- emits TUI toasts for auth conditions
- holds pending OAuth transports in memory
- persists tokens and registration info
- reacts to MCP tool-list-changed notifications

This means the subsystem spans:

- transport/protocol logic
- credential logic
- runtime capability projection
- user-facing feedback

That breadth is why it deserves its own documentation layer.

---

# 38. A representative MCP runtime lifecycle

A typical lifecycle looks like this:

## 38.1 Instance loads MCP config

- state initialization reads configured servers

## 38.2 Runtime attempts to connect each active server

- remote via HTTP/SSE transports
- local via stdio transport

## 38.3 Auth-related failures are classified

- `needs_auth`
- `needs_client_registration`

## 38.4 Successful connections are capability-verified

- `listTools()` must work

## 38.5 Runtime stores active clients and statuses

- instance-scoped live state

## 38.6 Tools/prompts/resources are later projected into OpenCode runtime surfaces

- via MCP capability listing and conversion helpers

This is a full connection-and-capability lifecycle, not just a route implementation detail.

---

# 39. Key design principles behind this module

## 39.1 Connection state and credential state should live in different storage layers

So live clients are instance-scoped while auth material is persisted through `McpAuth`.

## 39.2 External capability servers should be classified by actionable lifecycle states, not vague success/failure buckets

So MCP status distinguishes `connected`, `needs_auth`, `needs_client_registration`, `disabled`, and `failed`.

## 39.3 Connection success should be validated by usable capability discovery, not transport handshake alone

So `listTools()` is required after client connect.

## 39.4 MCP capabilities should project into the same broader tool/prompt/resource model used by the rest of OpenCode

So tool, prompt, and resource conversion helpers normalize external MCP capabilities for internal consumption.

---

# 40. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/mcp/index.ts`
2. `packages/opencode/src/mcp/auth.ts`
3. `packages/opencode/src/mcp/oauth-provider.ts`
4. `packages/opencode/src/mcp/oauth-callback.ts`

Focus on these functions and concepts:

- `MCP.Status`
- `create(...)`
- `convertMcpTool(...)`
- `fetchPromptsForClient(...)`
- `fetchResourcesForClient(...)`
- `pendingOAuthTransports`
- `McpAuth.getForUrl()`
- `McpOAuthProvider`
- cleanup of descendant subprocesses

---

# 41. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: How are the later public helpers like `MCP.tools()`, `MCP.prompts()`, and `MCP.resources()` structured, and how do they cache or recompute capability inventories?
- **Question 2**: Should capability verification after connection extend beyond `listTools()` to prompts or resources as well?
- **Question 3**: How should clients reason about instance-scoped MCP state if multiple instances exist concurrently with different MCP configs?
- **Question 4**: Are there cases where automatic toast emission from the runtime should be replaced or supplemented with more structured eventing?
- **Question 5**: How should the runtime handle partially usable MCP servers that expose resources or prompts but have broken tool listing?
- **Question 6**: What invariants keep `pendingOAuthTransports` aligned with persisted auth state across reconnect attempts?
- **Question 7**: Should transport fallback order be configurable for advanced deployments?
- **Question 8**: How should name sanitization and capability key generation evolve if two servers expose colliding prompt/resource names after sanitization?

---

# 42. Summary

The `mcp_runtime_connection_auth_and_capability_projection` layer is the operational core of OpenCode’s MCP integration:

- it manages live client connections and rich lifecycle status classification
- it separates live instance-scoped connection state from persisted auth state
- it supports both remote and local MCP servers with different transport models
- it verifies usable capabilities after connection and projects MCP tools, prompts, and resources into the rest of the runtime

So this module is not just transport glue. It is the connection manager and capability projection layer that makes MCP a real extensibility substrate inside OpenCode.
