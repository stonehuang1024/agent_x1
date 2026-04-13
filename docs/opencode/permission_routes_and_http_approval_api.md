# Permission Routes / HTTP Approval API

---

# 1. Module Purpose

This document explains the dedicated HTTP permission route surface and how it exposes OpenCode’s runtime approval system to external clients.

The key questions are:

- Why does OpenCode have a standalone `/permission` route namespace?
- What is the relationship between `server/routes/permission.ts` and `PermissionNext`?
- How do permission requests become pending HTTP-visible resources?
- How does HTTP approval feed back into blocked runtime execution?
- Why is there both a dedicated permission route and a deprecated session-scoped compatibility route?

Primary source files:

- `packages/opencode/src/server/routes/permission.ts`
- `packages/opencode/src/permission/next.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/server/routes/session.ts`

This layer is OpenCode’s **HTTP-facing human-approval control surface**.

---

# 2. Why permissions need their own route namespace

Permissions in OpenCode are not just internal tool errors.

They are runtime interrupt points that may require an external human or client to decide:

- allow once
- allow always
- reject

That makes them strong candidates for their own API namespace.

A dedicated route surface makes sense because permission approval is:

- session-related
- but not reducible to message CRUD
- and not tied to one single prompt response request

So `/permission` is a clean control-plane abstraction.

---

# 3. The permission route surface is intentionally small

`server/routes/permission.ts` exposes only two routes:

- `POST /permission/:requestID/reply`
- `GET /permission/`

That small surface is revealing.

The server is not trying to re-implement permission logic here.

It only exposes the two things external clients truly need:

- list pending permission requests
- answer one pending request

That is a strong sign of a well-factored design.

---

# 4. `permission.list`: pending permissions are first-class runtime resources

`GET /permission/` returns:

- `PermissionNext.Request[]`

This means pending permission requests are treated as queryable resources, not just transient UI popups.

That is important for:

- browser or desktop clients
- IDE extensions
- ACP bridges
- automation tools that want to inspect outstanding approvals

The runtime is making “waiting for user approval” visible and addressable through the control plane.

---

# 5. What a `PermissionNext.Request` contains

From `permission/next.ts`, a request contains:

- `id`
- `sessionID`
- `permission`
- `patterns`
- `metadata`
- `always`
- optional tool linkage `{ messageID, callID }`

This is a strong payload shape.

It contains enough context for a remote client to render:

- what permission is being requested
- which session it belongs to
- what exact resource or pattern is affected
- what tool call initiated it
- what scope could be persisted via `always`

So the HTTP API is not exposing a vague “approve?” object.

It exposes structured decision context.

---

# 6. Why `patterns` and `always` are both included

These two fields serve related but distinct purposes.

## 6.1 `patterns`

- the exact resource patterns currently under evaluation

## 6.2 `always`

- the subset of patterns that can be promoted into persistent allow rules if the user chooses `always`

This is important because the approval UI should not have to reconstruct persistence scope by itself.

The runtime provides the necessary semantics directly.

---

# 7. `permission.reply`: the mutation endpoint

`POST /permission/:requestID/reply` accepts:

- `reply: PermissionNext.Reply`
- optional `message`

and forwards them to:

- `PermissionNext.reply(...)`

This is the entire bridge from HTTP client decision back into runtime execution.

The route surface is minimal because the real logic lives where it should:

- inside `PermissionNext`

---

# 8. Why `message` exists on rejection

The reply payload includes an optional:

- `message`

That matters because OpenCode supports two different rejection semantics:

## 8.1 Reject without message

- halts execution via `RejectedError`

## 8.2 Reject with message

- produces `CorrectedError`
- allows the runtime to continue with human guidance

This is a subtle but powerful distinction.

The HTTP route preserves it directly.

---

# 9. Why the route does not expose session ID in the path

The route is keyed by:

- `requestID`

not:

- `sessionID + requestID`

That is a clean design.

Permission requests are globally unique runtime interrupts.

Once a client has the request ID, it does not need to repeat session scoping in the URL to answer it.

The underlying request already contains session context.

---

# 10. How pending permission state is stored

`PermissionNext` keeps in-memory state via `Instance.state(...)`, including:

- `pending: Map<PermissionID, PendingEntry>`
- `approved: Ruleset`

This means `/permission` routes expose the current instance’s live permission state.

That is why instance/workspace scoping middleware in `server.ts` is so important.

Without correct instance binding, the route would be looking at the wrong pending approval set.

---

# 11. `PermissionNext.ask(...)`: where permission requests originate

Permissions are generally initiated by runtime code calling:

- `PermissionNext.ask(...)`

This happens in places like:

- `SessionPrompt.resolveTools()` via `ctx.ask(...)`
- task delegation flows
- doom-loop detection in `SessionProcessor`

The permission route does not create requests.

It only exposes and resolves them.

That separation is correct.

---

# 12. `ask(...)` as a suspended Promise boundary

When `PermissionNext.ask(...)` decides action should be `ask`, it:

- allocates or uses a request ID
- stores a pending entry
- publishes `permission.asked`
- returns a Promise that resolves or rejects later

This is the key runtime abstraction.

A permission request is fundamentally:

- a suspended execution promise waiting for a human decision

The HTTP route is simply one way to satisfy that suspended promise.

---

# 13. Why the route surface pairs so naturally with the event system

The usual client flow is likely:

- subscribe to events
- observe `permission.asked`
- call `GET /permission/` or use event payload directly
- submit `POST /permission/:requestID/reply`

This makes the HTTP permission API a natural companion to the event bus.

The event stream tells you something needs attention.

The route lets you inspect or answer it.

---

# 14. `PermissionNext.reply(...)`: the real logic center

The route itself is tiny because all important semantics are in `PermissionNext.reply(...)`.

That function:

- finds the pending request
- removes it from pending state
- publishes `permission.replied`
- resolves or rejects the suspended promise
- optionally expands `always` approvals into stored allow rules
- may also auto-resolve other pending requests in the same session if newly-approved rules now cover them

So the route is just the transport edge of a much richer runtime behavior.

---

# 15. Why `always` is especially important

If the reply is:

- `always`

then `PermissionNext.reply(...)` appends allow rules to `approved` for every pattern in `existing.info.always`.

This is significant because the HTTP route is not just approving a one-off action.

It may be mutating the future permission model for that instance/project.

That turns approval into policy formation.

---

# 16. Session-wide cascading effects on reject

If a request is rejected, `PermissionNext.reply(...)` also rejects all other pending permissions for the same session.

This is a strong runtime policy.

It means a human rejection is treated as a broader interruption to that session’s permission flow, not just a single isolated approval denial.

That behavior is worth documenting clearly because it affects client UX.

---

# 17. Session-wide cascading effects on `always`

If the reply is `always`, the runtime also scans other pending requests in the same session and auto-resolves any whose patterns are now fully allowed by the new approval set.

This is another important behavior for remote clients.

A single approval may cause multiple pending prompts to disappear because the runtime can now proceed safely without further human intervention.

---

# 18. Why `permission.list` is still useful even with events

A client could rely only on `permission.asked` events.

But `permission.list` still matters because:

- the client may reconnect after missing events
- the UI may need an authoritative refresh
- there may be multiple outstanding requests to render at once

So list endpoints and event streams complement each other.

Neither is sufficient alone for robust clients.

---

# 19. Rule evaluation and why the HTTP route does not need to know it

`PermissionNext.evaluate(...)` handles:

- wildcard permission matching
- wildcard pattern matching
- last-rule-wins behavior
- default `ask`

That logic is intentionally hidden from the route layer.

This is good design.

The HTTP permission surface should expose requests and answers, not duplicate ruleset reasoning.

---

# 20. `PermissionNext.disabled(...)` and the approval API

`disabled(...)` is used elsewhere to hide tools from the model when rules say they are globally denied.

This is related to the HTTP approval API because it shows two different control modes:

- some tools are hidden up front and never create permission requests
- other tool actions remain visible but trigger runtime approval requests

The permission route only deals with the second category.

That is an important distinction.

---

# 21. The deprecated session-scoped permission reply route

`server/routes/session.ts` still contains:

- `POST /session/:sessionID/permissions/:permissionID`

marked deprecated.

It simply forwards to:

- `PermissionNext.reply(...)`

This exists for compatibility, but the dedicated `/permission` namespace is clearly the newer, cleaner abstraction.

---

# 22. Why the dedicated route is better than the deprecated one

The old session-scoped path implies permission replies are primarily subordinate to session routes.

The dedicated path says something more accurate:

- pending permissions are a standalone cross-session control-plane concern

That is the better model for clients that may render a global approval inbox or observe many sessions at once.

---

# 23. How permission replies affect execution control

Once the HTTP route calls `PermissionNext.reply(...)`, the suspended execution path resumes or aborts.

Possible outcomes include:

- allowed once -> tool execution continues
- always -> tool execution continues and future matching requests may auto-resolve
- reject -> runtime throws rejection/correction error

Then higher layers like `SessionProcessor` may:

- continue
- stop
- or surface correction guidance

So the HTTP route is tiny, but it sits directly on the boundary of runtime control flow.

---

# 24. Why this API is essential for non-embedded clients

If OpenCode only supported permission approval inside a single built-in UI, then:

- ACP bridges
- browser clients
- remote desktop shells
- IDE integrations

would have no standard way to complete approval flows.

The dedicated permission route solves that.

It turns human approval into a proper API operation.

---

# 25. A representative permission HTTP lifecycle

A typical flow looks like this:

## 25.1 Runtime hits an approval boundary

- tool code calls `PermissionNext.ask(...)`

## 25.2 Request becomes pending

- promise is suspended
- `permission.asked` event is published
- request appears in `PermissionNext.list()`

## 25.3 Client observes pending request

- through event stream
- or through `GET /permission/`

## 25.4 Client answers it

- `POST /permission/:requestID/reply`

## 25.5 Runtime resumes

- resolve, reject, or correct
- session execution continues or halts accordingly

This is the complete HTTP approval loop.

---

# 26. Key design principles behind this module

## 26.1 Human approval should be modeled as a real control-plane resource

So pending permission requests are listable and addressable by ID.

## 26.2 Transport layers should be thin when runtime semantics already exist elsewhere

So `server/routes/permission.ts` is deliberately minimal and delegates to `PermissionNext`.

## 26.3 Approval can be more than a binary decision

So rejection may include corrective feedback, and `always` can mutate future policy.

## 26.4 Event streams and list/mutate routes should complement each other

So clients can both react live and recover authoritative state after reconnects.

---

# 27. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/routes/permission.ts`
2. `packages/opencode/src/permission/next.ts`
3. `packages/opencode/src/session/prompt.ts`
4. `packages/opencode/src/session/processor.ts`
5. `packages/opencode/src/server/routes/session.ts`

Focus on these functions and concepts:

- `PermissionRoutes`
- `PermissionNext.ask()`
- `PermissionNext.reply()`
- `PermissionNext.list()`
- `permission.asked`
- `permission.replied`
- `RejectedError`
- `CorrectedError`
- session-wide cascading approval effects

---

# 28. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: How do front-end clients present multiple concurrent pending approvals across sessions in practice?
- **Question 2**: Should `permission.list` support filtering by session ID or permission type for large multi-session clients?
- **Question 3**: How should the route surface evolve once persistent management of `always` rules gains a full UI and storage story?
- **Question 4**: Is session-wide cascading rejection always the right UX, or are there cases where clients might want more granular behavior?
- **Question 5**: Should the HTTP reply route expose richer result information than `true`, such as how many pending requests were auto-resolved?
- **Question 6**: How should remote clients display the difference between `RejectedError` and `CorrectedError` outcomes after replying?
- **Question 7**: When multiple clients are connected, what is the desired concurrency model if two clients try to answer the same request?
- **Question 8**: How quickly should deprecated session-scoped permission reply routes be removed once all clients migrate?

---

# 29. Summary

The `permission_routes_and_http_approval_api` layer exposes OpenCode’s permission system as a clean HTTP control-plane surface:

- `GET /permission/` makes pending approvals queryable
- `POST /permission/:requestID/reply` feeds human decisions back into suspended runtime execution
- the real semantics live in `PermissionNext`, including one-time approval, persistent approval, corrective rejection, and cascading same-session effects
- the dedicated route namespace is cleaner and more accurate than the older deprecated session-scoped compatibility path

So this module is not just a small helper route. It is the API boundary where human approval becomes a formal, remotely manageable part of OpenCode’s agent runtime.
