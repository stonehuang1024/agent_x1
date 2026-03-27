# MCP Routes / External Server Surface

---

# 1. Module Purpose

This document explains the `/mcp` route namespace, which exposes OpenCode’s control-plane surface for managing Model Context Protocol servers, their connection state, and their authentication flows.

The key questions are:

- Why does OpenCode expose MCP servers as a first-class API surface?
- How do `server/routes/mcp.ts` and the deeper `MCP` runtime divide responsibilities?
- Why are status, add, connect, disconnect, and OAuth flows all part of one namespace?
- How does this route surface relate to MCP-backed tools, prompts, and resources used elsewhere in the runtime?
- What does this API reveal about OpenCode’s position as an orchestrator of external capability servers?

Primary source files:

- `packages/opencode/src/server/routes/mcp.ts`
- `packages/opencode/src/mcp/index.ts`
- `packages/opencode/src/mcp/auth.ts`
- `packages/opencode/src/mcp/oauth-provider.ts`
- `packages/opencode/src/mcp/oauth-callback.ts`

This layer is OpenCode’s **external MCP server management and authentication control-plane API**.

---

# 2. Why `/mcp` exists separately

MCP servers are not just another provider.

They expose external capabilities such as:

- tools
- prompts
- resources

that OpenCode can incorporate into its runtime.

Because these are long-lived external server relationships rather than one-shot request dependencies, they need a dedicated management surface.

That is why `/mcp` exists.

---

# 3. The route surface models MCP servers as managed runtime resources

`server/routes/mcp.ts` exposes:

- `GET /mcp/`
- `POST /mcp/`
- `POST /mcp/:name/auth`
- `POST /mcp/:name/auth/callback`
- `POST /mcp/:name/auth/authenticate`
- `DELETE /mcp/:name/auth`
- `POST /mcp/:name/connect`
- `POST /mcp/:name/disconnect`

This is not a passive info namespace.

It is a full management API for external capability servers.

---

# 4. Why MCP management belongs in the control plane

OpenCode’s runtime can depend on MCP servers for:

- additional tools
- additional prompts
- additional resources

That means clients need structured ways to:

- inspect MCP availability
- add servers dynamically
- authenticate them
- connect or disconnect them
- observe their health

Those are classic control-plane concerns.

---

# 5. The route layer is intentionally thin

The file mostly delegates to the deeper `MCP` namespace:

- `MCP.status()`
- `MCP.add(...)`
- `MCP.supportsOAuth(...)`
- `MCP.startAuth(...)`
- `MCP.finishAuth(...)`
- `MCP.authenticate(...)`
- `MCP.removeAuth(...)`
- `MCP.connect(...)`
- `MCP.disconnect(...)`

This is good structure.

The route module handles HTTP contract and validation; the MCP runtime handles the actual external-server lifecycle.

---

# 6. `GET /mcp/`: status overview for all MCP servers

The main route returns:

- `z.record(z.string(), MCP.Status)`

by calling:

- `MCP.status()`

This means the route exposes a named map of MCP server status objects rather than a flat list.

That is a practical choice because MCP servers are usually addressed by stable names in config and operator workflows.

---

# 7. Why status is the primary MCP read API

Before a client can do anything meaningful with MCP integration, it needs to know:

- what servers are configured?
- are they connected?
- are they disabled?
- are they authenticated?
- are they failing?

A status map answers those questions efficiently.

So `GET /mcp/` is the natural entrypoint to the namespace.

---

# 8. Relationship to the deeper MCP runtime

The grep results show `MCP` is used elsewhere to surface:

- `MCP.tools()`
- `MCP.prompts()`
- `MCP.readResource(...)`
- `MCP.resources()`

That means the `/mcp` route surface is not exposing the final user-facing capability consumption paths directly.

Instead, it manages the external server relationships that make those capabilities available to the rest of the runtime.

That is an important distinction.

---

# 9. `POST /mcp/`: dynamic MCP server registration

The add route validates a body containing:

- `name`
- `config: Config.Mcp`

then calls:

- `MCP.add(name, config)`

and returns:

- `result.status`

This is a strong design signal.

It means MCP servers are not only statically configured in files.

They can also be added dynamically through the control plane.

---

# 10. Why dynamic add matters

Dynamic MCP add is useful for:

- onboarding external servers from a UI
- testing integrations
- temporary operator workflows
- runtime extension without manual config editing

This makes OpenCode a more flexible orchestrator of external capability servers.

---

# 11. Why `Config.Mcp` is used directly in route validation

The route reuses:

- `Config.Mcp`

for request validation.

This is important because it keeps the transport contract aligned with the actual authoritative MCP configuration model.

The route is not inventing a separate shadow DTO for MCP configuration.

That reduces drift.

---

# 12. OAuth support is modeled explicitly

Several routes exist just for authentication:

- start OAuth
- finish OAuth callback
- authenticate end-to-end
- remove stored auth

This shows that MCP servers may require independent authentication lifecycles, and OpenCode treats those lifecycles as first-class API operations.

That is the right approach for an integration platform.

---

# 13. `POST /mcp/:name/auth`: start OAuth flow

This route first checks:

- `MCP.supportsOAuth(name)`

If OAuth is not supported, it returns a JSON error with status `400`.

Otherwise it calls:

- `MCP.startAuth(name)`

and returns an object containing:

- `authorizationUrl`

This is a classic “begin browser-based auth” control-plane endpoint.

---

# 14. Why OAuth capability is checked explicitly first

Not every MCP server supports OAuth.

So treating auth-start as universally valid would create poor and ambiguous behavior.

By checking capability up front, the route gives clients a clear contract:

- only call this flow for OAuth-capable MCP servers

That is good API hygiene.

---

# 15. Why a JSON authorization URL response is useful

Returning:

- `authorizationUrl`

makes the route UI-agnostic.

A web client, desktop shell, or IDE integration can each decide how to present or open that URL.

The server does not force one presentation model.

That is a strong control-plane design.

---

# 16. `POST /mcp/:name/auth/callback`: explicit completion step

This route validates:

- `code`

then calls:

- `MCP.finishAuth(name, code)`

and returns the resulting `MCP.Status`.

This is the explicit callback completion surface.

It is useful for environments where the client receives the callback code and wants to pass it back to the server deliberately.

---

# 17. Why explicit callback completion matters

OAuth often crosses browser, desktop, and server boundaries.

A dedicated callback route lets OpenCode support multi-step authentication flows in a controlled, transport-neutral way.

It is especially useful when the UI or client wants to mediate the callback step itself.

---

# 18. `POST /mcp/:name/auth/authenticate`: one-shot authenticate flow

This route also checks OAuth capability, then calls:

- `MCP.authenticate(name)`

and returns `MCP.Status`.

The route description says it starts OAuth and waits for callback, opening a browser.

So this is the “fully managed” authentication path.

---

# 19. Why both explicit and one-shot auth routes exist

These two auth paths serve different client needs.

## 19.1 Split flow

- `auth`
- `auth/callback`

Useful when the client wants full control over browser or callback handling.

## 19.2 One-shot flow

- `auth/authenticate`

Useful when the server/runtime can manage the whole browser-based authentication lifecycle itself.

This is a good example of exposing both low-level and convenience control-plane operations without conflating them.

---

# 20. `DELETE /mcp/:name/auth`: credential removal

This route calls:

- `MCP.removeAuth(name)`

and returns:

- `{ success: true }`

This is an important lifecycle operation because authentication is not only about acquisition.

Clients also need a clean way to:

- revoke local stored credentials
- force reauthentication
- clear broken auth state

That makes auth removal a natural part of the namespace.

---

# 21. `POST /mcp/:name/connect` and `/disconnect`: connection lifecycle control

These routes call:

- `MCP.connect(name)`
- `MCP.disconnect(name)`

and return `true` on success.

This means OpenCode separates:

- being configured
- being authenticated
- being actively connected

Those are different lifecycle states, and the route surface reflects that properly.

---

# 22. Why connect/disconnect is separate from add/remove

Adding an MCP server config is not the same as opening a live connection.

Likewise, disconnecting does not necessarily mean forgetting the server configuration.

That separation is important for real operator workflows where clients may want to:

- keep a server configured but disconnected
- reconnect on demand
- test connection lifecycle independently from configuration lifecycle

This is good resource modeling.

---

# 23. What deeper `mcp/index.ts` hints tell us

The grep output shows the MCP runtime includes concepts like:

- `MCP.ToolsChanged`
- `MCP.BrowserOpenFailed`
- OAuth transport/provider logic
- remote-server enable/disable handling

This reveals that the MCP subsystem is a real runtime integration manager, not just a thin config adapter.

The `/mcp` routes are therefore sitting atop a substantial external-system orchestration layer.

---

# 24. Why MCP matters to the rest of the runtime

Elsewhere in the codebase, MCP servers can contribute:

- tools to the session prompt loop
- prompts to command/template systems
- resources readable by the runtime

So the `/mcp` route surface is effectively the management API for a major extension mechanism inside OpenCode.

That gives this module high architectural importance.

---

# 25. A representative MCP lifecycle through the API

A typical flow could look like this:

## 25.1 Inspect current MCP server state

- `GET /mcp/`

## 25.2 Add a new MCP server config

- `POST /mcp/`

## 25.3 Authenticate if needed

- `POST /mcp/:name/auth`
- or `POST /mcp/:name/auth/authenticate`
- optionally `POST /mcp/:name/auth/callback`

## 25.4 Connect the server

- `POST /mcp/:name/connect`

## 25.5 Use MCP-backed tools/resources elsewhere in the runtime

- via sessions, commands, or experimental resource routes

## 25.6 Disconnect or remove auth when needed

- `POST /mcp/:name/disconnect`
- `DELETE /mcp/:name/auth`

This is a full external-server management lifecycle.

---

# 26. Why this module matters architecturally

The `/mcp` routes show that OpenCode is more than a local LLM client.

It is also a broker/orchestrator for external capability servers that can extend what the runtime can do.

That has major implications for:

- extensibility
- operator control
- authentication complexity
- external dependency management
- live capability discovery

This module is one of the clearest windows into OpenCode’s platform ambitions.

---

# 27. Key design principles behind this module

## 27.1 External capability servers should be managed as first-class runtime resources

So MCP servers have status, add, auth, connect, and disconnect operations.

## 27.2 Authentication lifecycle should be explicit and transport-neutral

So the API exposes both split OAuth steps and a convenience authenticate flow.

## 27.3 Configuration, authentication, and connection are distinct lifecycle phases

So the route surface does not collapse them into one opaque action.

## 27.4 Extension infrastructure should remain integrated with the broader runtime, not isolated from it

So MCP-managed capabilities surface elsewhere as tools, prompts, and resources.

---

# 28. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/routes/mcp.ts`
2. `packages/opencode/src/mcp/index.ts`
3. `packages/opencode/src/mcp/auth.ts`
4. `packages/opencode/src/mcp/oauth-provider.ts`
5. `packages/opencode/src/mcp/oauth-callback.ts`

Focus on these functions and concepts:

- `MCP.status()`
- `MCP.add()`
- `MCP.supportsOAuth()`
- `MCP.startAuth()`
- `MCP.finishAuth()`
- `MCP.authenticate()`
- `MCP.connect()`
- `MCP.disconnect()`
- `MCP.tools()/prompts()/resources()`

---

# 29. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: What exact states can `MCP.Status` take, and how should clients interpret transitions between them?
- **Question 2**: How are MCP config additions persisted, and are they intended to survive process restarts automatically?
- **Question 3**: What event stream signals should clients watch to observe MCP connection, auth, or tool-set changes in real time?
- **Question 4**: How does `MCP.authenticate()` behave in environments where automatic browser opening fails or is unavailable?
- **Question 5**: Should the route surface eventually expose MCP tool and prompt inventories directly under `/mcp`, or is keeping that under runtime/experimental surfaces the better separation?
- **Question 6**: How do remote-workspace scenarios interact with local MCP server management and connection ownership?
- **Question 7**: What security constraints should surround dynamic MCP add/connect operations in less-trusted deployments?
- **Question 8**: How should MCP failures be surfaced to clients when a server is configured but partially authenticated or intermittently reachable?

---

# 30. Summary

The `mcp_routes_and_external_server_surface` layer exposes OpenCode’s external capability-server management model as a first-class API:

- it reports MCP server status and supports dynamic server registration
- it models authentication explicitly, including split OAuth and convenience authenticate flows
- it separates auth state from live connection state with explicit connect/disconnect operations
- it sits above a deeper MCP runtime that contributes tools, prompts, and resources to the rest of OpenCode

So this module is not just integration plumbing. It is the control-plane surface for one of OpenCode’s most important extensibility mechanisms.
