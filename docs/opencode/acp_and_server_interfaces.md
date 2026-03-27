# ACP / Server Interfaces 模块详细解读

---

# 1. 模块定位

This document explains OpenCode's server-facing control plane and its ACP integration layer.

The key questions are:

- What capabilities do the HTTP server interfaces expose?
- Why is the server more than a chat API and closer to a runtime control plane?
- How do event streaming, instance scoping, and route composition work?
- How does ACP adapt OpenCode into the Agent Client Protocol ecosystem?
- What is the boundary between ACP, HTTP routes, CLI, and local UI clients?

Primary source files:

- `packages/opencode/src/server/server.ts`
- `packages/opencode/src/server/routes/session.ts`
- `packages/opencode/src/server/routes/question.ts`
- `packages/opencode/src/server/routes/permission.ts`
- `packages/opencode/src/server/routes/pty.ts`
- `packages/opencode/src/acp/agent.ts`
- `packages/opencode/src/acp/session.ts`

This layer is OpenCode’s **multi-protocol interface and control-plane adaptation layer**.

---

# 2. One runtime, many interface surfaces

The codebase shows that OpenCode is intentionally exposed through several different access surfaces:

- **CLI / local terminal workflows**
- **HTTP server routes**
- **OpenAPI-described APIs**
- **SDK consumers**
- **ACP agent-side integration**
- **PTY and event-stream endpoints for live clients**

These are not separate runtimes.

They all sit on top of the same core internals:

- `Session`
- `SessionPrompt`
- `MessageV2`
- `PermissionNext`
- `Question`
- `ToolRegistry`
- `Bus`
- `Provider`

That is the central architectural idea here:

- **diverse protocols, shared runtime semantics**

---

# 3. The server is a control plane, not a single inference endpoint

`server.ts` makes this obvious.

The app exposes routes for:

- auth management
- session control
- question and permission workflows
- PTY management
- provider information
- project and config state
- file and MCP routes
- event streaming
- instance disposal
- command, agent, skill, formatter, and LSP discovery

This is far broader than a typical `/chat/completions` surface.

OpenCode is exposing a persistent agent runtime, not a stateless answer API.

---

# 4. HTTP stack style: code-first contracts

The server uses:

- `Hono`
- `hono-openapi`
- `zod`

Each route typically combines:

- request handling
- input validation
- response schema definition
- OpenAPI metadata

This is a clean code-first API contract approach.

It matters because these routes are consumed by more than one client style:

- internal UI layers
- SDK users
- IDE extensions
- ACP bridges
- automation clients

Keeping validation and API description near the implementation is the right choice for a multi-client runtime.

---

# 5. Top-level server composition in `createApp()`

`Server.createApp()` wires the entire HTTP surface.

Important responsibilities include:

- global error handling
- optional basic auth
- request logging
- CORS policy
- workspace and instance scoping
- route composition
- event streaming

This function is the real HTTP entry-point orchestration layer.

---

# 6. Error handling at the server boundary

The global `.onError(...)` handler translates runtime exceptions into HTTP responses.

It treats several error classes specially:

- `NotFoundError` -> `404`
- `Provider.ModelNotFoundError` -> `400`
- worktree-related errors -> `400`
- other `NamedError`s -> `500`
- generic exceptions -> wrapped as `NamedError.Unknown`

This means the server surface preserves OpenCode’s internal typed error model instead of flattening everything into ad hoc JSON.

---

# 7. Authentication at the HTTP edge

Before instance routing, the server optionally applies HTTP basic auth using:

- `OPENCODE_SERVER_PASSWORD`
- optional `OPENCODE_SERVER_USERNAME`

This is intentionally simple.

It protects the server surface as a whole, rather than introducing per-route auth complexity inside the runtime layer.

---

# 8. Request logging and observability

The server logs:

- method
- path
- request timing

with a special-case skip for `/log` to avoid noisy recursive logging.

This is a small but important detail: the logging endpoint itself should not explode server logs.

---
 
# 9. TUI routes: remote control for terminal-facing UX

Although `server.ts` only mounts `TuiRoutes()` here, the surrounding architecture makes their purpose clear.

TUI routes are not model-facing APIs.

They are remote-control surfaces for terminal-style UI behavior.

In practice, this means external shells, local wrappers, IDE integrations, or desktop clients can drive the terminal UI through explicit route calls instead of brittle keyboard simulation.

That is a strong design choice.

It turns the TUI into an event-driven surface that can be controlled programmatically.

---
 
# 10. Why TUI control belongs in the server at all

If terminal control were implemented purely inside the TUI process, external tools would have no reliable way to:

- append prompt text
- trigger prompt submission
- switch sessions
- fire UI commands
- display feedback like toasts

By exposing TUI control as routes backed by the same runtime event model, OpenCode makes terminal UX remotely orchestratable.

That is especially useful for IDE bridges and automation harnesses.

---
 
# 11. MCP routes sit at the control-plane boundary too

The server mounts `/mcp`, which signals that MCP servers are not just internal tool providers.

They are also externally manageable resources.

From the server perspective, MCP is something clients may need to:

- inspect
- configure
- authenticate
- connect
- disconnect

So MCP exists in OpenCode at two levels simultaneously:

- as an internal tool/prompt/resource source
- as an externally managed integration surface

That is a platform-oriented design.

---
 
# 12. ACP: what it is conceptually

ACP stands for:

- Agent Client Protocol

In OpenCode, ACP is the layer that makes the runtime appear as an ACP-compatible agent to external ACP clients.

It is not a replacement runtime.

It is a protocol adapter.

The same core session and tool machinery stays underneath.

---
 
# 13. ACP Agent is a live adapter, not a static translator

`ACP.Agent` holds substantial runtime coordination state:

- the ACP-side connection
- SDK access to OpenCode
- an ACP session manager
- event subscription lifecycle control
- permission serialization queues
- tool-progress tracking structures
- bash-output deduplication state

That tells us ACP is an active runtime participant.

It continuously watches OpenCode and re-expresses its state for ACP consumers.

---
 
# 14. ACP bootstrap model

`ACP.init(...)` returns a factory that can create an `ACP.Agent` for a given connection and config.

This means ACP is instantiated per connection context, not as a single global singleton.

That is the correct choice because each ACP client may need:

- its own connection lifecycle
- its own session mapping
- its own event subscription handling

---
 
# 15. ACP event subscription model

The ACP agent starts `runEventSubscription()` once and then continuously consumes:

- `sdk.global.event({ signal })`

For each streamed event payload, it calls:

- `handleEvent(payload)`

This is one of the strongest architectural signals in the codebase:

- ACP is downstream of the same evented runtime exposed elsewhere

It does not invent a separate state-update mechanism.

---
 
# 16. Why ACP consumes events instead of polling state

ACP needs timely updates for:

- permission prompts
- tool progress
- completed tool outputs
- usage changes
- message parts

Polling would be too slow and too lossy, especially for streaming and long-running tools.

The event stream gives ACP an almost live mirror of session execution.

---
 
# 17. ACP permission flow

The `permission.asked` branch inside `handleEvent()` is one of the clearest examples of protocol adaptation.

When OpenCode emits a permission request, ACP:

- resolves the corresponding ACP session
- serializes per-session permission handling through `permissionQueues`
- calls `connection.requestPermission(...)`
- converts the ACP response back into `sdk.permission.reply(...)`

Permission options are mapped explicitly to:

- `allow_once`
- `allow_always`
- `reject_once`

This is a direct semantic bridge between OpenCode approval flows and ACP approval UX.

---
 
# 18. Why ACP serializes permissions per session

Permission events can arrive quickly during tool-heavy execution.

If ACP forwarded them concurrently for the same session, the client could be overwhelmed or approvals could become semantically ambiguous.

`permissionQueues` solves that by guaranteeing ordered handling per session.

This is a subtle but very important runtime adaptation.

---
 
# 19. ACP also patches edit previews into the client flow

Inside the permission path, ACP contains special logic for edit permissions.

If the user is not rejecting an edit permission request, ACP may reconstruct new file content from diff metadata and call:

- `connection.writeTextFile(...)`

This shows ACP is not only relaying permission decisions.

It is also enriching the client’s file-view and editing experience where the protocol supports it.

---
 
# 20. ACP tool lifecycle translation

The `message.part.updated` branch handles tool parts as they evolve through states like:

- `pending`
- `running`
- `completed`
- `error`

ACP then emits `sessionUpdate` calls with tool-call updates that include:

- status
- tool kind
- title
- raw input
- optional content
- optional diff payloads

This is how OpenCode tool parts become ACP-native tool call progress.

---
 
# 21. Bash tool output deduplication in ACP

ACP tracks `bashSnapshots` by tool call ID and hashes tool output while a bash call is running.

If the output has not changed, ACP suppresses redundant repeated in-progress updates.

This is a highly practical optimization.

Bash output is often noisy and incremental. Without deduplication, ACP clients would receive a large amount of repetitive traffic.

---
 
# 22. ACP emits richer edit semantics than plain text

For completed edit-like tool calls, ACP may emit a diff content block containing:

- file path
- old text
- new text

This is important because protocol consumers often want to render real code diffs instead of just showing opaque tool output strings.

OpenCode’s internal tool metadata is being upgraded into a more client-meaningful representation.

---
 
# 23. Todo output becomes ACP plan updates

ACP treats `todowrite` specially.

It parses todo JSON output into structured plan entries and sends a `sessionUpdate` of type:

- `plan`

This is another strong example of semantic adaptation:

- OpenCode todo runtime state
- becomes ACP planning state

ACP is clearly doing more than transport-level forwarding.

---
 
# 24. Usage telemetry in ACP

`sendUsageUpdate(...)` fetches session messages, identifies assistant messages, resolves model context limits, and computes:

- used input context
- total cost
- total context window size

It then sends an ACP `usage_update` payload.

This means ACP clients can observe:

- cost
- context pressure
- usage progression

not just message text.

---
 
# 25. Why ACP uses the SDK instead of deep internal imports

ACP talks to OpenCode primarily through an `OpencodeClient` SDK handle.

That is a strong modularity choice.

It means ACP is built on a supported interface boundary rather than bypassing the public stack with too much internal coupling.

As a result, ACP behaves more like a real external integration surface.

---
 
# 26. HTTP server versus ACP: the clean mental model

A useful distinction is:

## 26.1 HTTP server

- route-based control plane
- discovery and mutation APIs
- SSE events
- well suited for apps, local services, IDE extensions, and SDK consumers

## 26.2 ACP

- protocol adapter for external agent-client ecosystems
- event-to-protocol translator
- well suited for clients that already speak ACP

They serve different client ecosystems while preserving the same runtime behavior.

---
 
# 27. Why these interfaces matter strategically

Together, the server and ACP layers make OpenCode usable as:

- a local agent platform
- an IDE runtime backend
- a terminal automation surface
- a protocol-compliant external agent service

Without these layers, OpenCode would be trapped inside a single built-in shell experience.

With them, it becomes a reusable platform.

---
 
# 28. A representative end-to-end interface flow

A typical cross-interface flow looks like this:

## 28.1 Client starts or resumes a session

- via HTTP route or SDK-backed ACP call

## 28.2 Runtime performs work

- prompts
- tool calls
- permission requests
- question flows

## 28.3 Internal bus emits events

- part updates
- status updates
- permission and question requests
- diff and summary updates

## 28.4 Server and ACP consume the same semantics differently

- server exposes them as SSE and route responses
- ACP turns them into protocol-native session and tool updates

## 28.5 Human decisions return through the matching interface

- HTTP permission/question routes
- or ACP permission callbacks

The underlying session runtime remains the same throughout.

---
 
# 29. Key design principles behind this module

## 29.1 Multiple protocols should share one runtime truth

So HTTP, SDK, TUI, and ACP all sit on top of the same session and event machinery.

## 29.2 A stateful agent runtime must expose events, not only request/response APIs

So SSE and ACP event subscription are both central, not optional.

## 29.3 Human-in-the-loop interactions belong in the public control plane

So permissions and questions are exposed as first-class interfaces.

## 29.4 Interface adapters should preserve semantics, not flatten everything into text

So ACP translates todo state, diffs, permissions, and tool progress into richer client-native structures.

---
 
# 30. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/server.ts`
2. `packages/opencode/src/server/routes/session.ts`
3. `packages/opencode/src/server/routes/question.ts`
4. `packages/opencode/src/server/routes/permission.ts`
5. `packages/opencode/src/server/routes/pty.ts`
6. `packages/opencode/src/acp/session.ts`
7. `packages/opencode/src/acp/agent.ts`

Focus on these functions and concepts:

- `Server.createApp()`
- instance/workspace middleware
- `/event`
- `ACP.Agent.runEventSubscription()`
- `ACP.Agent.handleEvent()`
- ACP permission queues
- ACP tool-call updates
- usage update computation

---
 
# 31. Open questions for further investigation

There are several strong follow-up questions worth exploring:

- **Question 1**: What are the full request/response surfaces in `server/routes/session.ts`, especially around prompt, resume, fork, revert, diff, and share?
- **Question 2**: How exactly do `QuestionRoutes` and `PermissionRoutes` map onto front-end UX and acknowledgement flows?
- **Question 3**: What transport contract do `PtyRoutes` guarantee for streaming terminal consumers?
- **Question 4**: Which emitted bus events are intended to be stable external contracts versus purely internal implementation details?
- **Question 5**: Which OpenCode runtime concepts still have no good ACP equivalent, if any?
- **Question 6**: How does `ACPSessionManager` represent forks, task sub-sessions, and mode changes over long session lifetimes?
- **Question 7**: Should the server surface more direct control-plane APIs for plan/todo/diff workflows beyond today’s route breakdown?
- **Question 8**: How should auth and remote deployment security evolve if this server is used in less-trusted environments?

---
 
# 32. Summary

The `acp_and_server_interfaces` layer is where OpenCode becomes an externally usable agent platform:

- the HTTP server exposes a typed, instance-scoped control plane
- SSE event streaming makes the runtime observable in real time
- session, permission, question, PTY, discovery, and integration routes expose the real runtime surface area
- ACP subscribes to the same semantics and translates them into a protocol-native agent interface

So this module is not mere transport glue. It is the boundary where OpenCode’s internal agent runtime becomes remotely controllable, observable, and interoperable across multiple client ecosystems.
