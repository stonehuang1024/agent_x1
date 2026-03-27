# Global Event Stream / Instance-Scoping API

---

# 1. Module Purpose

This document explains the server-wide event stream, request-to-instance binding, and workspace-routing behavior that make the rest of OpenCode’s HTTP control plane coherent.

The key questions are:

- How does the server bind each request to a workspace and instance context?
- Why is instance scoping a foundational server concern rather than an implementation detail?
- What is the difference between the main `/event` stream and other route-local APIs?
- How does experimental workspace routing change the request path?
- Why does this infrastructure matter for every session, PTY, permission, and question route?

Primary source files:

- `packages/opencode/src/server/server.ts`
- `packages/opencode/src/control-plane/workspace-router-middleware.ts`
- `packages/opencode/src/server/routes/global.ts`
- `packages/opencode/src/project/instance.ts`

This layer is OpenCode’s **request-context binding and server-wide event transport infrastructure**.

---

# 2. Why this layer matters so much

Most of OpenCode’s runtime state is not global in the simple sense.

It is instance-scoped.

That includes state like:

- session status
- pending questions
- pending permissions
- PTY sessions
- plugin state
- tool runtime state
- project/worktree metadata

So before any route can behave correctly, the server has to answer:

- which workspace is this request for?
- which directory instance is active for it?

This is one of the core hidden responsibilities of the HTTP server.

---

# 3. Request scoping in `server.ts`

One middleware block in `Server.createApp()` does most of this work.

It reads request context from:

- query `workspace`
- header `x-opencode-workspace`
- query `directory`
- header `x-opencode-directory`
- fallback `process.cwd()`

Then it resolves the directory and wraps the request in:

- `WorkspaceContext.provide(...)`
- `Instance.provide(...)`

This is the real binding point between HTTP requests and runtime state.

---

# 4. Why both workspace ID and directory exist

These are related but different concepts.

## 4.1 `workspace`

- a logical control-plane identifier
- used especially for remote/adapted workspace routing

## 4.2 `directory`

- the concrete filesystem/project root used by the local instance

Having both allows OpenCode to support:

- direct local directory-bound requests
- remote workspace indirection
- future workspace-aware routing and synchronization logic

That is a flexible design.

---

# 5. Why the directory is decoded and normalized early

The middleware attempts `decodeURIComponent(raw)` and then applies:

- `Filesystem.resolve(...)`

This is important because route handlers should not each have to worry about:

- URL-encoded directory paths
- relative-path ambiguity
- path normalization

By normalizing up front, the server ensures every downstream subsystem sees a stable directory identity.

---

# 6. `Instance.provide(...)` is the real per-request runtime boundary

After workspace context is set, the server calls:

- `Instance.provide({ directory, init: InstanceBootstrap, fn })`

This means every downstream route is executed inside a specific project instance with bootstrap guarantees.

This is fundamental.

Without it, route handlers like:

- `SessionRoutes`
- `PermissionRoutes`
- `QuestionRoutes`
- `PtyRoutes`

would not know which underlying in-memory runtime state to use.

---

# 7. Why instance bootstrap belongs here

The server does not assume instances already exist.

By passing:

- `init: InstanceBootstrap`

into `Instance.provide(...)`, it allows instance setup to happen as part of request handling when necessary.

That keeps route handlers simpler and preserves a single place where instance lifecycle is entered.

---

# 8. This is why instance-scoped runtime state works at all

Modules like:

- `PermissionNext`
- `Question`
- `Pty`
- `SessionStatus`

all use `Instance.state(...)`.

That only works correctly because the server binds the current request into the right instance context before invoking those modules.

So this middleware is the enabling layer behind much of OpenCode’s local-runtime architecture.

---

# 9. `WorkspaceRouterMiddleware`: experimental remote forwarding

After local workspace/instance context is set, the server applies:

- `WorkspaceRouterMiddleware`

This middleware is gated by:

- `OPENCODE_EXPERIMENTAL_WORKSPACES`

When enabled, it may forward requests to a remote workspace adaptor instead of handling them locally.

This introduces a second level of control-plane behavior:

- local request execution
- or remote workspace delegation

---

# 10. How workspace routing actually works

`routeRequest(req)` does:

- return early if no `WorkspaceContext.workspaceID`
- load the workspace from control-plane state
- resolve an adaptor with `getAdaptor(workspace.type)`
- forward the request through `adaptor.fetch(...)`

It preserves:

- method
- body
- signal
- headers
- path and query string

This is a real protocol-forwarding layer.

---

# 11. Why remote workspace forwarding matters

The code comment makes the current intent explicit:

- all requests are forwarded for remote workspaces because syncing is not implemented yet

This is a very important architectural clue.

It means the HTTP server is already being shaped as a federated control plane, not just a purely local API surface.

The local process can become a router for remote workspaces.

---

# 12. Why `GET` is not yet special-cased

The comment says that in the future, some non-mutating `GET` routes might be handled locally.

But for now, forwarding is simpler and safer because state synchronization is incomplete.

That is a reasonable tradeoff.

It prefers correctness over premature optimization.

---

# 13. The server-wide `/event` endpoint

One of the most important global transport surfaces in `server.ts` is:

- `GET /event`

This endpoint subscribes to:

- `Bus.subscribeAll(...)`

and exposes the resulting runtime events as SSE.

This is the main live event stream for the instance-bound server runtime.

---

# 14. Why `/event` is foundational

Most route surfaces expose snapshots or one-off mutations.

`/event` exposes:

- ongoing runtime change

That includes events from many subsystems, such as:

- sessions
- messages and parts
- questions
- permissions
- PTY lifecycle
- instance disposal

So `/event` is the streaming spine of the control plane.

---

# 15. The `/event` stream protocol

On connection, the server immediately emits:

- `server.connected`

Then it subscribes to all bus events and forwards each as SSE data.

It also sends a heartbeat every 10 seconds:

- `server.heartbeat`

Finally, it closes the stream if it sees:

- `Bus.InstanceDisposed`

This is a careful long-lived event-stream protocol.

---

# 16. Why `server.connected` is useful

An initial connected event gives clients:

- immediate proof that the stream is alive
- a clean point to mark subscription readiness

This is better than requiring the client to infer stream success from TCP/socket behavior alone.

It is a small but useful handshake convention.

---

# 17. Why heartbeat is essential

The heartbeat exists specifically to prevent:

- stalled proxy streams

This is important because SSE connections may otherwise appear idle long enough for:

- proxies
- gateways
- browsers
- load balancers

to assume the connection is dead.

A 10-second heartbeat is practical transport hardening for a long-lived event channel.

---

# 18. Why the stream closes on `InstanceDisposed`

If the active instance is disposed, the stream is no longer valid for that runtime context.

Closing the stream immediately is correct because it forces the client to:

- reconnect
- rebind to a fresh runtime instance if needed

That prevents clients from silently listening to a dead context.

---

# 19. SSE transport headers matter here

The server sets:

- `X-Accel-Buffering: no`
- `X-Content-Type-Options: nosniff`

These are transport-level details, but very important ones.

They help ensure the event stream behaves correctly through proxies and does not get buffered into useless chunks.

This is similar in spirit to other transport-hardening code elsewhere in the codebase.

---

# 20. Why `/event` lives in `server.ts` instead of `GlobalRoutes`

There are actually two different event stream concepts in the codebase:

## 20.1 `server.ts` -> `/event`

- instance-bound server runtime events
- uses `Bus.subscribeAll(...)`

## 20.2 `global.ts` -> `/global/event`

- global cross-instance events
- uses `GlobalBus`

This distinction is easy to miss, but it is architecturally important.

---

# 21. Local bus versus global bus

A good mental model is:

## 21.1 `Bus`

- events for the active instance/runtime context
- session/message/permission/question/PTy etc.

## 21.2 `GlobalBus`

- server-wide or cross-instance coordination
- higher-level events like global disposal

This explains why both `/event` and `/global/event` exist.

They expose different scopes of observability.

---

# 22. Why clients may need both event streams

A sophisticated client might care about:

- active session/message updates within one project instance
- plus global lifecycle events across the whole OpenCode server

Those are different needs.

So separate event channels are justified.

---

# 23. Route composition happens after scoping middleware for a reason

The server mounts routes like:

- `/session`
- `/permission`
- `/question`
- `/pty`
- `/provider`
- `/mcp`

only after the workspace/instance scoping middleware is installed.

That ordering is essential.

It guarantees route handlers run inside the correct runtime context.

If the order were reversed, large parts of the server would break semantically.

---

# 24. Why `/path` belongs near this layer

The route:

- `GET /path`

returns:

- home
- state
- config
- worktree
- directory

This route is especially useful because it exposes the result of instance binding and global path resolution.

For clients, it is a concrete way to verify which local context the server has actually attached to the request.

---

# 25. Why `/instance/dispose` also belongs near this layer

`POST /instance/dispose` calls:

- `Instance.dispose()`

That makes instance lifecycle an explicit API concern.

This is closely related to request scoping because if the instance is the core runtime boundary, then disposal of that boundary must also be a formal control-plane operation.

---

# 26. The catch-all proxy at the bottom is also part of interface strategy

After all explicit control-plane routes, `server.ts` ends with:

- `.all("/*", ...)`

which proxies unmatched paths to:

- `https://app.opencode.ai`

with a specific CSP applied.

This suggests the local server is not only an API host.

It can also serve as a proxy surface for app assets or UI integration.

That is part of the broader interface design.

---

# 27. Why this infrastructure is more than plumbing

It would be easy to dismiss:

- workspace scoping
- instance provision
- SSE event transport
- remote workspace forwarding

as generic server plumbing.

That would be wrong.

These pieces actively determine:

- which runtime state is read or mutated
- whether clients observe the correct event stream
- whether remote workspaces are addressed locally or forwarded
- whether long-lived clients stay synchronized correctly

This is core architecture, not background noise.

---

# 28. A representative request flow through this layer

A typical request flow looks like this:

## 28.1 Request arrives

- may carry `workspace` and/or `directory`

## 28.2 Context is normalized

- directory is decoded and resolved
- workspace ID is parsed if present

## 28.3 Request enters workspace and instance scopes

- `WorkspaceContext.provide(...)`
- `Instance.provide(...)`

## 28.4 Optional remote forwarding happens

- `WorkspaceRouterMiddleware` may delegate to a remote adaptor

## 28.5 Local route executes or remote response returns

- session/question/permission/pty/etc.

## 28.6 Clients can observe follow-up effects through `/event`

That is the real control-plane execution path under almost every HTTP feature in OpenCode.

---

# 29. Key design principles behind this module

## 29.1 Runtime state should always be accessed through explicit request context binding

So the server resolves workspace and directory before route handling.

## 29.2 Event streams are first-class control-plane infrastructure

So `/event` exposes live bus updates with connection and heartbeat semantics.

## 29.3 Local and remote workspaces should share one routing model

So experimental workspace middleware can forward requests through adaptors instead of duplicating route logic.

## 29.4 Instance lifecycle must be observable and controllable through the API

So instance-bound path discovery, disposal, and event closure are formal parts of the server surface.

---

# 30. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/server.ts`
2. `packages/opencode/src/control-plane/workspace-router-middleware.ts`
3. `packages/opencode/src/server/routes/global.ts`
4. `packages/opencode/src/project/instance.ts`

Focus on these functions and concepts:

- request scoping middleware
- `WorkspaceContext.provide()`
- `Instance.provide()`
- `WorkspaceRouterMiddleware`
- `routeRequest()`
- `/event`
- heartbeat behavior
- `Instance.dispose()`

---

# 31. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: How exactly does `Instance.provide()` isolate and retrieve per-directory runtime state under the hood?
- **Question 2**: Which event types flowing through `/event` are intended to be stable external contracts, and which are implementation details?
- **Question 3**: How should clients combine `/event` and `/global/event` subscriptions for the best multi-workspace UX?
- **Question 4**: What additional logic will be needed before some GET requests can be served locally for remote workspaces without full sync?
- **Question 5**: Are there any subtle race conditions around instance disposal and immediately reconnecting event-stream clients?
- **Question 6**: How should authentication, CORS, and workspace routing evolve if OpenCode is deployed in more remote or multi-user environments?
- **Question 7**: Does the catch-all proxy surface have any interesting interactions with the API routes or CSP behavior in practice?
- **Question 8**: Should the server expose stronger introspection endpoints for current workspace-routing and instance-binding decisions?

---

# 32. Summary

The `global_event_stream_and_instance_scoping_api` layer is the infrastructure that makes the rest of OpenCode’s HTTP control plane coherent:

- request middleware binds each call to a workspace and concrete directory instance
- optional workspace-routing middleware can forward those requests to remote workspace adaptors
- the server-wide `/event` SSE stream exposes live instance-bound bus events with connection and heartbeat semantics
- instance lifecycle operations like path discovery and disposal sit naturally alongside this scoping model

So this module is not just server plumbing. It is the context-binding and event-transport foundation that lets every higher-level OpenCode API operate against the correct runtime state.
