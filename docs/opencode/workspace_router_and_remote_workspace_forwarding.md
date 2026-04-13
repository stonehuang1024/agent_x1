# Workspace Router / Remote Workspace Forwarding

---

# 1. Module Purpose

This document explains the experimental workspace-routing layer that allows the OpenCode server to forward requests to remote workspace adaptors instead of always handling them locally.

The key questions are:

- Why does OpenCode need a workspace router at all?
- How does `WorkspaceRouterMiddleware` decide whether to forward or handle locally?
- What role do `WorkspaceContext`, `Workspace`, and adaptors play in request forwarding?
- Why are all requests currently forwarded for remote workspaces, even `GET` requests?
- What does this reveal about OpenCode’s emerging multi-workspace architecture?

Primary source files:

- `packages/opencode/src/control-plane/workspace-router-middleware.ts`
- `packages/opencode/src/server/server.ts`
- `packages/opencode/src/control-plane/workspace-context.ts`
- `packages/opencode/src/control-plane/workspace.ts`
- `packages/opencode/src/control-plane/adaptors.ts`

This layer is OpenCode’s **experimental remote-workspace request forwarding plane**.

---

# 2. Why this module matters

The rest of the server often looks local-first.

Routes bind to:

- a directory
- an instance
- in-memory runtime state

But the existence of workspace routing shows OpenCode is aiming beyond purely local execution.

It wants to support workspaces whose authoritative runtime may live elsewhere.

That is a major architectural step.

---

# 3. The middleware is intentionally narrow and strategic

`WorkspaceRouterMiddleware` is small, but it sits in a powerful place in the request chain.

Its job is simple:

- if experimental workspaces are disabled, do nothing
- otherwise try to route the request to a remote workspace adaptor
- if a remote response is returned, short-circuit local handling
- otherwise continue locally

This makes it a gateway decision point for the entire HTTP control plane.

---

# 4. Why it appears after context binding in `server.ts`

In `server.ts`, the server first establishes:

- `WorkspaceContext.provide(...)`
- `Instance.provide(...)`

Then it applies:

- `WorkspaceRouterMiddleware`

This ordering is important.

It means the middleware can rely on already-parsed workspace identity and directory context when deciding what to do.

The router is not guessing from raw URLs alone.

It participates in the same context model as the rest of the server.

---

# 5. The feature is explicitly gated

`WorkspaceRouterMiddleware` only activates when:

- `Flag.OPENCODE_EXPERIMENTAL_WORKSPACES`

is enabled.

That is a strong signal that the architecture is real but still evolving.

The code is already integrated deeply enough to sit in the main request path, but still protected behind an experimental flag.

That is a sensible rollout posture.

---

# 6. `routeRequest(req)`: the core forwarding decision

The helper `routeRequest(req)` contains the real forwarding behavior.

Its first guard is:

- if no `WorkspaceContext.workspaceID`, return nothing

That means forwarding only applies when a request explicitly identifies a workspace.

No workspace ID means:

- local handling only

This is a clean contract.

---

# 7. Why workspace ID is the trigger, not directory

A directory alone does not imply remote authority.

A workspace ID does.

That distinction matters.

The router is based on the control-plane concept of a workspace object, not just a filesystem path.

That is a much more extensible design for remote or federated execution.

---

# 8. `Workspace.get(...)`: resolving the authoritative workspace object

Once a workspace ID is present, the middleware loads:

- `Workspace.get(WorkspaceContext.workspaceID)`

If it cannot find the workspace, it returns an explicit error response.

This is correct because request forwarding depends on authoritative workspace metadata, not just on a user-supplied workspace identifier.

The server validates that the target workspace actually exists in control-plane state.

---

# 9. Why missing workspace returns a concrete response instead of falling back locally

If a request names a workspace and that workspace does not exist, silently falling back to local handling would be dangerous.

The request’s intended execution context would be ambiguous or wrong.

Returning an explicit error is the safe and correct behavior.

---

# 10. Adaptors are the abstraction boundary for remote execution

After loading the workspace, the router does:

- `getAdaptor(workspace.type)`

Then it delegates to:

- `adaptor.fetch(workspace, pathAndQuery, requestLikeInput)`

This is a very clean abstraction.

The router itself does not need to know:

- how the remote workspace is reached
- what transport is used underneath
- how auth or remote protocol details work

It only needs an adaptor capable of fetch-like forwarding.

---

# 11. Why the adaptor abstraction is the right level

Remote workspaces might eventually be backed by different implementations:

- local proxy bridges
- remote servers
- custom workspace backends
- cloud-hosted environments

By using an adaptor interface, OpenCode keeps the routing decision separate from the transport mechanics.

That is exactly what a scalable multi-workspace architecture needs.

---

# 12. Forwarded request fidelity

When forwarding, the router preserves:

- method
- body for non-GET/non-HEAD requests
- abort signal
- headers
- path and query string

This means forwarded requests aim to behave like true remote executions of the same API operation, not rough approximations.

That fidelity matters because the entire higher-level route surface depends on consistent semantics.

---

# 13. Why the body handling is conditional

The router omits `body` for:

- `GET`
- `HEAD`

and otherwise forwards the request body as an `ArrayBuffer`.

This is the correct fetch-style behavior.

It avoids illegal or meaningless request bodies on methods where they are not expected, while still preserving full payloads for mutating operations.

---

# 14. Why headers are forwarded intact

Forwarding preserves:

- original request headers

This is important because many higher-level semantics may depend on them, including:

- auth
- workspace or directory hints
- content types
- client metadata

A remote workspace adaptor needs the same input contract that the local server would have received.

---

# 15. The path forwarding model

The forwarded path is built from:

- `new URL(req.url).pathname`
- plus `new URL(req.url).search`

This means remote workspaces receive the same logical API path and query parameters as the local server would.

That is important because it lets the adaptor treat the forwarded request as a true remote copy of the original HTTP operation.

---

# 16. Why all requests are forwarded today

The inline comment explains the current decision:

- all requests are forwarded because syncing is not implemented yet

This is one of the most informative comments in the whole module.

It reveals that OpenCode’s multi-workspace architecture currently assumes:

- the remote workspace is authoritative
- local replicas are not trusted to answer even read-only requests yet

That is a very important design constraint.

---

# 17. Why not optimize GETs yet

The comment suggests that in the future some non-mutating `GET` requests might be served locally.

But until synchronization exists, doing that could produce stale or inconsistent results.

Forwarding everything is the safer choice.

It favors semantic correctness over latency optimization.

That is the right tradeoff in an experimental distributed-control-plane feature.

---

# 18. What this says about OpenCode’s future direction

This middleware strongly implies that OpenCode is evolving toward:

- a federated workspace model
- where some runtime state may live remotely
- while the local server still offers a stable HTTP control-plane facade

That is a significant architectural ambition.

The middleware is small, but it represents a large future design space.

---

# 19. Why the middleware returns `Response | undefined`

The router returns either:

- a concrete forwarded `Response`
- or `undefined`

The middleware then decides:

- if response exists, return it immediately
- otherwise `next()` into the local route chain

This is a classic and elegant middleware control pattern.

It makes routing override explicit without complicating local routes themselves.

---

# 20. How this keeps local route handlers clean

Session, PTY, permission, question, and global route handlers do not need to know whether they are serving:

- a local workspace
- or a remote workspace proxy path

That decision happens before they run.

This is excellent separation of concerns.

It keeps the remote-workspace feature from infecting every route implementation.

---

# 21. Relationship to `WorkspaceContext`

The router depends on `WorkspaceContext.workspaceID` being available.

That makes `WorkspaceContext` the control-plane selector and the router the execution-path switch.

This is a good split:

- context layer says which workspace is targeted
- router decides where execution should occur

Those are distinct responsibilities.

---

# 22. Relationship to `Instance.provide(...)`

At first it may seem odd that instance binding happens before remote forwarding.

But this is reasonable because:

- local code still needs a consistent request context
- the middleware itself may need local control-plane state
- some requests will still be handled locally even when workspaces are enabled

So local instance provisioning remains part of the normal request pipeline.

The remote router is an overlay, not a replacement.

---

# 23. Failure mode behavior is intentionally simple

If the workspace exists and an adaptor is available, the middleware forwards.

If not, it either:

- returns an explicit workspace-not-found error
- or falls through to local handling when no forwarding applies

This simplicity is useful in an experimental layer.

The behavior is easy to reason about and does not attempt too much hidden fallback logic.

---

# 24. Why lack of fallback is actually a strength here

For distributed routing, “silent fallback” is usually dangerous.

If a request intended for a remote authoritative workspace accidentally executes locally, the control plane could diverge badly.

So the current behavior aligns well with a reliability-first architecture:

- explicit route or explicit error
- not implicit fallback

That is a strong design choice.

---

# 25. A representative forwarded request lifecycle

A typical remote-workspace request flow looks like this:

## 25.1 Client sends request with `workspace`

- query or header identifies a workspace

## 25.2 Server binds request context

- workspace ID and directory are resolved
- instance context is entered

## 25.3 Workspace router runs

- checks experimental flag
- loads workspace object
- resolves adaptor

## 25.4 Request is forwarded

- same path
- same query
- same headers
- same method
- same body when applicable

## 25.5 Remote workspace handles it authoritatively

- result is returned as the HTTP response

This is a true control-plane routing path, not just an internal helper.

---

# 26. Why this module is more than transport glue

It would be easy to dismiss this middleware as a small forwarding shim.

But it actually defines:

- the boundary between local and remote authority
- the request fidelity expectations for remote execution
- the current non-sync assumption of the workspace model
- the insertion point for distributed workspace support across the whole API

That makes it an architectural module, not just a convenience helper.

---

# 27. Key design principles behind this module

## 27.1 Workspace identity should be explicit

So forwarding is keyed off `workspaceID`, not guessed from directory alone.

## 27.2 Remote workspaces should be authoritative until synchronization exists

So all requests are forwarded today rather than partially answered locally.

## 27.3 Routing decisions should happen before feature-specific route logic

So local route handlers remain unaware of whether the request was forwarded.

## 27.4 Distributed control planes should avoid silent fallback

So missing workspace or adaptor resolution should not degrade into accidental local execution.

---

# 28. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/control-plane/workspace-router-middleware.ts`
2. `packages/opencode/src/server/server.ts`
3. `packages/opencode/src/control-plane/workspace-context.ts`
4. `packages/opencode/src/control-plane/workspace.ts`
5. `packages/opencode/src/control-plane/adaptors.ts`

Focus on these functions and concepts:

- `routeRequest()`
- `WorkspaceRouterMiddleware`
- `WorkspaceContext.workspaceID`
- `Workspace.get()`
- `getAdaptor()`
- forwarded `fetch(...)` contract
- experimental workspace flag

---

# 29. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: What adaptor types exist today, and how do their transport guarantees differ?
- **Question 2**: How will local read-only handling be introduced safely once workspace synchronization exists?
- **Question 3**: How should remote workspace failures be surfaced to clients so they remain distinguishable from local route failures?
- **Question 4**: What security model will govern remote workspace forwarding in more distributed deployments?
- **Question 5**: Should forwarded responses annotate that they were remotely served for observability and debugging?
- **Question 6**: How are event streams expected to work for remote workspaces, especially with local `/event` and `/global/event` consumers?
- **Question 7**: Will the instance-binding step remain local-first forever, or should some remote workspaces eventually avoid local instance initialization entirely?
- **Question 8**: How should caching, retries, and auth propagation behave across adaptor boundaries?

---

# 30. Summary

The `workspace_router_and_remote_workspace_forwarding` layer is the bridge between OpenCode’s current local-first runtime and its emerging remote-workspace architecture:

- it uses explicit workspace identity to decide whether a request should be forwarded
- it delegates remote execution through adaptor abstractions rather than hardcoding transport details
- it currently forwards all remote-workspace requests because synchronization is not yet in place
- it keeps higher-level route modules clean by making the routing decision before they run

So this module is not just middleware glue. It is the early distributed-routing control plane that will shape how OpenCode scales from local project instances to federated workspace execution.
