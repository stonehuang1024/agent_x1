# Session Route Surface / Control-Plane API

---

# 1. Module Purpose

This document explains `server/routes/session.ts` as the core HTTP control-plane surface for OpenCode session lifecycle, execution, history, diffing, sharing, and rollback.

The key questions are:

- Why are session routes much broader than a simple prompt endpoint?
- How does the route surface mirror the internal session runtime model?
- Which routes are read-only discovery endpoints versus mutation or execution endpoints?
- How do prompt, shell, command, summary, fork, revert, and message-level operations fit together?
- Why does this file reveal OpenCode’s control-plane philosophy so clearly?

Primary source files:

- `packages/opencode/src/server/routes/session.ts`
- `packages/opencode/src/session/index.ts`
- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/compaction.ts`
- `packages/opencode/src/session/revert.ts`
- `packages/opencode/src/session/summary.ts`
- `packages/opencode/src/session/message-v2.ts`

This layer is OpenCode’s **primary HTTP session control plane**.

---

# 2. Why this file matters so much

If you want to understand what OpenCode considers a first-class remote session capability, this file is one of the best places to look.

It exposes not just prompting, but also:

- listing and querying sessions
- status inspection
- forking
- sharing
- diff retrieval
- message pagination
- message/part mutation
- shell execution
- command execution
- revert and unrevert
- explicit summarization
- abort and cleanup boundaries

This is a very strong signal that a session is treated as a long-lived runtime object, not a transient chat request.

---

# 3. The route file mirrors the internal session model

The imports alone are revealing.

This route layer depends on:

- `Session`
- `SessionPrompt`
- `SessionCompaction`
- `SessionRevert`
- `SessionStatus`
- `SessionSummary`
- `MessageV2`
- `Todo`
- `Agent`
- `Snapshot`
- `PermissionNext`

That means the session route surface spans nearly every important runtime subsystem.

So the route file is not a thin CRUD wrapper.

It is where many major runtime capabilities become remotely controllable.

---

# 4. Route design style

The file uses a consistent Hono + OpenAPI + zod pattern:

- `describeRoute(...)`
- `validator(...)`
- `resolver(...)`

This gives each route:

- explicit operation IDs
- typed validation
- stable response schema contracts
- automatic documentation support

That matters because session APIs are consumed by multiple clients and need to stay mechanically understandable.

---

# 5. Read surface versus mutation surface

A useful way to understand the file is to split it into two broad classes:

## 5.1 Read/discovery routes

- `GET /session/`
- `GET /session/status`
- `GET /session/:sessionID`
- `GET /session/:sessionID/children`
- `GET /session/:sessionID/todo`
- `GET /session/:sessionID/diff`
- `GET /session/:sessionID/message`
- `GET /session/:sessionID/message/:messageID`

## 5.2 Mutation/execution routes

- `POST /session/`
- `PATCH /session/:sessionID`
- `DELETE /session/:sessionID`
- `POST /session/:sessionID/init`
- `POST /session/:sessionID/fork`
- `POST /session/:sessionID/abort`
- `POST /session/:sessionID/share`
- `DELETE /session/:sessionID/share`
- `POST /session/:sessionID/summarize`
- `POST /session/:sessionID/message`
- `POST /session/:sessionID/prompt_async`
- `POST /session/:sessionID/command`
- `POST /session/:sessionID/shell`
- `POST /session/:sessionID/revert`
- `POST /session/:sessionID/unrevert`
- message/part delete and patch routes

This split is useful because it shows OpenCode exposing both:

- state introspection
- runtime mutation and execution control

---

# 6. `session.list`: sessions are a queryable persistent resource

`GET /session/` supports filters like:

- `directory`
- `roots`
- `start`
- `search`
- `limit`

This means sessions are not just opaque IDs.

They are a searchable, browsable persistent collection.

That is a classic control-plane characteristic.

Clients are expected to navigate a session inventory, not merely hold one session in memory.

---

# 7. `session.status`: runtime liveness is first-class API data

`GET /session/status` returns:

- `Record<string, SessionStatus.Info>`

This exposes the in-memory runtime status layer directly over HTTP.

That is important because it lets clients observe:

- which sessions are busy
- which are retrying
- which have no active runtime entry

This route makes the execution engine observable in real time, not just after messages are written.

---

# 8. `session.get` and `session.children`: sessions form a graph, not a flat list

`GET /session/:sessionID`

returns the full session info.

`GET /session/:sessionID/children`

returns forked descendants.

This confirms that OpenCode models session history as more than a linear transcript.

Branching and fork lineage are first-class data.

That aligns directly with the fork/revert/task-oriented runtime model seen elsewhere.

---

# 9. `session.todo`: planning state is part of session state

`GET /session/:sessionID/todo` exposes session todo state as a dedicated route.

This matters because it shows todo/plan state is not treated as incidental tool output only.

It is promoted into a durable session-facing API resource.

That is a strong product signal: planning artifacts matter enough to be queried directly.

---

# 10. `session.create`, `session.update`, `session.delete`

These routes expose standard session lifecycle mutation:

- create
- patch title / archive time
- delete

While these seem routine, they reveal that session metadata is expected to evolve independently from message history.

For example:

- a session can be archived
- a session title can be updated after creation

This is again consistent with sessions being durable managed entities.

---

# 11. `session.init`: initialization is a real workflow, not just a side-effect

`POST /:sessionID/init` calls:

- `Session.initialize(...)`

The route description explicitly frames this as analyzing the current application and generating an `AGENTS.md`-style project-specific setup.

That means initialization is considered a formal session-level action.

It is not hidden behind onboarding UI logic.

---

# 12. Why `session.init` belongs in the session route surface

Initialization affects the working session context:

- project instructions
- agent behavior
- repo-specific conventions

So it makes sense to expose it as part of session lifecycle rather than treating it as a global command unrelated to sessions.

---

# 13. `session.fork`: branching is explicit and addressable

`POST /:sessionID/fork` routes into:

- `Session.fork(...)`

This allows a new session to be created from a specific point in an existing session.

This is crucial because it means session history can branch intentionally through the API, not only through internal UI affordances.

Forking is therefore a real part of the remote runtime model.

---

# 14. `session.abort`: execution control is direct, not indirect

`POST /:sessionID/abort` simply calls:

- `SessionPrompt.cancel(sessionID)`

This is a clean example of control-plane design.

The route does not try to infer whether the session is prompt-driven, tool-driven, or retry-sleeping.

It just invokes the session-level cancellation primitive.

That is exactly the right abstraction boundary.

---

# 15. `session.share` and `session.unshare`

These routes expose sharing as a first-class session capability:

- create shareable session state
- remove it later

This is important because sharing is not modeled as an external export utility.

It is part of session state itself, surfaced through the same control plane.

That keeps the object model coherent.

---

# 16. `session.diff`: diff inspection is session-native

`GET /:sessionID/diff?messageID=...` routes into:

- `SessionSummary.diff(...)`

and returns:

- `Snapshot.FileDiff[]`

This is important because code-change inspection is not hidden behind a UI-only feature.

It is exposed as structured API data.

That makes OpenCode much more useful for IDEs, dashboards, and orchestration clients that want to reason about code changes programmatically.

---

# 17. `session.summarize`: compaction is also an explicit API action

`POST /:sessionID/summarize` does more than just toggle a flag.

It:

- retrieves the session
- runs `SessionRevert.cleanup(session)` first
- inspects session history to identify the active agent context
- creates compaction work through `SessionCompaction.create(...)`
- then re-enters `SessionPrompt.loop({ sessionID })`

This is very revealing.

Summary generation is not just “call a summarizer”.

It is a controlled session-runtime transformation with cleanup, compaction state creation, and continued execution.

---

# 18. Why summarize performs revert cleanup first

This route explicitly calls:

- `SessionRevert.cleanup(session)`

before launching compaction.

That means the API wants the session history to be in a logically committed state before summary generation proceeds.

In other words:

- compaction should operate on the settled session timeline
- not on a pending revert shadow state

That is a strong consistency decision.

---

# 19. Why summarize discovers the current agent from history

Before compaction creation, the route scans messages backward to find the most recent user message and infer the active agent.

This matters because compaction is not entirely agent-agnostic.

The route tries to preserve the conversational execution context rather than summarizing in a vacuum.

That is a subtle but valuable runtime choice.

---

# 20. Message list route: history is paginable, not just bulk-loaded

`GET /:sessionID/message` supports either:

- full history retrieval
- or cursor-based paging using `limit` and `before`

It also exposes the next page through:

- `Link`
- `X-Next-Cursor`

This is good API hygiene for long sessions.

It acknowledges that message history can become large enough to require pagination semantics.

---

# 21. Why `before` requires `limit`

The route validates that:

- `before` cannot be used without `limit`

This is a small but important contract clarity rule.

Cursor semantics only make sense in a paged context.

The route avoids ambiguous queries rather than trying to infer intent.

---

# 22. `session.message` and message-level resource access

`GET /:sessionID/message/:messageID` retrieves one message with parts.

This matters because message identity is stable and externally meaningful.

Clients can directly address and inspect a single message without re-fetching the whole session.

That is useful for:

- incremental UIs
- drill-down inspection
- message-specific tooling
- revert/diff workflows

---

# 23. Message deletion does not imply code revert

The delete-message route description explicitly says:

- deleting a message does **not** revert file changes made while processing it

That is an excellent API contract detail.

It distinguishes clearly between:

- transcript mutation
- file-system rollback

Those are different operations, and the route documentation states that explicitly.

---

# 24. Message deletion is busy-guarded

Before deleting a message, the route calls:

- `SessionPrompt.assertNotBusy(sessionID)`

That is important because transcript mutation during active execution would risk race conditions and inconsistent state.

So the control plane protects message mutation with the same session busy semantics used elsewhere.

---

# 25. Part deletion and part patching expose a very low-level edit surface

The route file allows:

- deleting a specific part
- patching a specific part

This is a fairly powerful capability.

It means the message-part model is not just internal storage detail.

It is an externally addressable API surface.

That can support advanced editors, repair tools, or protocol adapters.

---

# 26. Why part patching validates path identity aggressively

When patching a part, the route checks that:

- `body.id === partID`
- `body.messageID === messageID`
- `body.sessionID === sessionID`

If not, it throws a mismatch error.

That is exactly the right safeguard.

This API accepts full part payloads, so identity consistency must be enforced strictly.

---

# 27. `session.prompt`: synchronous prompt execution over HTTP

`POST /:sessionID/message` is the core prompt route.

It validates:

- `SessionPrompt.PromptInput` minus `sessionID`

Then it streams the JSON response body through Hono’s `stream(...)`, eventually writing the result of:

- `SessionPrompt.prompt({ ...body, sessionID })`

This is notable because the route name says “message”, but semantically it is:

- create a user turn and run the session prompt loop

That is an important part of the API vocabulary.

---

# 28. Why prompt uses streaming even when returning JSON

The route writes JSON through a stream rather than returning a plain `c.json(...)` response.

That suggests the implementation wants flexibility around long-running execution or progressive response handling even for a final JSON payload.

It is a pragmatic interface choice for a route that may take meaningful time to complete.

---

# 29. `session.prompt_async`: fire-and-return execution

`POST /:sessionID/prompt_async` starts prompt execution but returns immediately with:

- `204`

Internally it still calls:

- `SessionPrompt.prompt({ ...body, sessionID })`

but does not await and serialize the result into the response.

This is a very useful control-plane distinction.

It allows clients to:

- enqueue work now
- observe progress later through session/event APIs

rather than tying up the HTTP request.

---

# 30. Why async prompting belongs alongside synchronous prompting

Some clients want:

- request/response behavior

Others want:

- command-and-observe behavior

By exposing both in the same route family, OpenCode supports both interaction modes cleanly without inventing separate runtimes.

---

# 31. `session.command`: command-template execution is a first-class session action

`POST /:sessionID/command` calls:

- `SessionPrompt.command(...)`

This means slash-command or command-template style actions are not merely UI conveniences.

They are part of the session control plane.

That is consistent with OpenCode’s general philosophy that command templates are structured prompt-entry mechanisms.

---

# 32. `session.shell`: user-driven shell execution is also explicit

`POST /:sessionID/shell` calls:

- `SessionPrompt.shell(...)`

This is distinct from the model-driven `bash` tool.

The route exposes the user-initiated shell pathway as a formal session action.

That means remote clients can drive shell execution inside the session model without pretending to be the LLM.

---

# 33. `session.revert` and `session.unrevert`

These routes expose file+history rollback semantics directly:

- revert to a message or part boundary
- restore from revert state

They delegate to:

- `SessionRevert.revert(...)`
- `SessionRevert.unrevert(...)`

This is strong evidence that rollback is not a hidden UI action.

It is part of the public runtime contract.

---

# 34. Deprecated permission response endpoint

The route:

- `POST /:sessionID/permissions/:permissionID`

is marked deprecated.

It forwards to:

- `PermissionNext.reply(...)`

This is useful context because it shows the session route surface has evolved.

Permission response is still supported here for compatibility, but it likely belongs more naturally in the dedicated permission route namespace now.

---

# 35. The route surface reveals a layered object model

From this single file, you can infer OpenCode’s remote object hierarchy:

- session
- message
- part
- diff
- todo
- share state
- revert state
- runtime execution status

That is not the shape of a simple chat app.

It is the shape of a session-oriented agent runtime with inspectable internal state.

---

# 36. Why this file deserves the name “control plane”

A control plane is where clients:

- discover runtime objects
- inspect state
- trigger actions
- observe status
- mutate configuration or lifecycle

`server/routes/session.ts` does all of that for sessions.

So calling it a control-plane API is precise, not rhetorical.

---

# 37. A representative lifecycle through this route surface

A common remote flow can look like this:

## 37.1 Create a session

- `POST /session/`

## 37.2 Send a prompt

- `POST /session/:sessionID/message`
- or `prompt_async`

## 37.3 Observe progress

- `GET /session/status`
- `GET /session/:sessionID/message`
- or the global event stream elsewhere

## 37.4 Inspect outputs and changes

- `GET /session/:sessionID/diff`
- `GET /session/:sessionID/todo`

## 37.5 Branch or summarize

- `POST /session/:sessionID/fork`
- `POST /session/:sessionID/summarize`

## 37.6 Correct or roll back

- delete message/part
- revert/unrevert
- abort active execution

This is a full runtime lifecycle, not a single interaction API.

---

# 38. Key design principles behind this module

## 38.1 Sessions are durable managed resources

So the API supports list, inspect, update, branch, share, summarize, and delete.

## 38.2 Prompting is only one operation inside a larger session runtime

So shell, command, revert, summarize, and diff sit beside prompt routes.

## 38.3 Message and part granularity matter externally

So there are direct APIs for paging, fetching, deleting, and patching them.

## 38.4 Runtime safety must be preserved through the control plane

So destructive operations like message deletion are busy-guarded and revert has its own dedicated semantics.

---

# 39. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/routes/session.ts`
2. `packages/opencode/src/session/index.ts`
3. `packages/opencode/src/session/prompt.ts`
4. `packages/opencode/src/session/revert.ts`
5. `packages/opencode/src/session/compaction.ts`
6. `packages/opencode/src/session/summary.ts`
7. `packages/opencode/src/session/message-v2.ts`

Focus on these functions and concepts:

- `session.prompt`
- `session.prompt_async`
- `session.command`
- `session.shell`
- `session.summarize`
- `session.revert`
- message paging
- part patch/delete
- busy guards

---

# 40. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Why do the prompt routes use streamed JSON responses instead of plain JSON, and how do clients consume that in practice?
- **Question 2**: Should message deletion and part patching require additional audit or permission safeguards in some deployment modes?
- **Question 3**: How are event-stream subscribers expected to combine `/session/status` polling with live bus events for the best UX?
- **Question 4**: Are there any route-level race conditions between `prompt_async`, `abort`, and message mutation APIs that clients need to handle carefully?
- **Question 5**: Should there be a dedicated session route for diffing the whole session rather than per-message diff lookup?
- **Question 6**: How should share/unshare interact with archived or reverted sessions over time?
- **Question 7**: Why is the deprecated permission response route still mounted here instead of redirecting clients to the dedicated permission namespace?
- **Question 8**: Which of these session routes are considered stable external contract versus still evolving internal-facing API surface?

---

# 41. Summary

The `session_route_surface_and_control_plane_api` module shows that OpenCode’s HTTP session layer is a full control plane for a persistent agent runtime:

- it manages session lifecycle, history, status, branching, sharing, rollback, and summarization
- it exposes both synchronous and asynchronous execution entry points
- it treats messages, parts, todos, and diffs as structured API resources
- it preserves runtime safety and consistency through busy guards and dedicated revert/summary flows

So this file is not just a chat route bundle. It is the clearest HTTP expression of OpenCode’s session-centric runtime architecture.
