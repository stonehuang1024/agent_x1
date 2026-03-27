# Workspace Event Sync / SSE Bridge

---

# 1. Module Purpose

This document explains how OpenCode synchronizes workspace event streams and bridges them into the global event system, focusing on `workspaceEventLoop`, `parseSSE`, and the workspace-server `/event` relay surface.

The key questions are:

- How does OpenCode ingest event streams from workspace execution targets?
- Why is SSE parsing handled explicitly in `parseSSE(...)` rather than delegated to a library abstraction?
- How does `WorkspaceServerRoutes().get("/event")` participate in the event-bridging story?
- Why are workspace events re-emitted onto `GlobalBus` instead of kept isolated inside workspace-local channels?
- What reliability and contract implications follow from this bridge design?

Primary source files:

- `packages/opencode/src/control-plane/workspace.ts`
- `packages/opencode/src/control-plane/sse.ts`
- `packages/opencode/src/control-plane/workspace-server/server.ts`
- `packages/opencode/src/control-plane/workspace-server/routes.ts`

This layer is OpenCode’s **workspace event synchronization and SSE-bridge runtime**.

---

# 2. Why this layer matters

The workspace runtime does more than route requests.

It also tries to make workspace activity observable from the local server.

That requires a bridge from:

- workspace-local event streams

to:

- server-global event observation

This bridge is one of the most important parts of OpenCode’s multi-workspace architecture because it turns distributed execution contexts into a coherent event model.

---

# 3. The high-level event-sync pipeline

The event-sync path looks like this:

## 3.1 Workspace runtime selects workspaces to sync

- `Workspace.startSyncing(project)`

## 3.2 Each selected workspace runs `workspaceEventLoop(...)`

- fetches `"/event"` through its adaptor

## 3.3 The response body is parsed as SSE

- `parseSSE(res.body, signal, onEvent)`

## 3.4 Each parsed event is re-emitted on `GlobalBus`

- with a source label tied to the workspace ID

This is the core bridge.

---

# 4. `startSyncing(project)`: sync orchestration entrypoint

`Workspace.startSyncing(project)`:

- creates an `AbortController`
- lists workspaces for the project
- filters out `worktree` workspaces
- starts one `workspaceEventLoop(...)` per remaining workspace
- returns a `stop()` function that aborts all loops

This is a clean orchestration boundary.

It shows syncing is treated as a managed background activity, not just an ad hoc fetch.

---

# 5. Why `worktree` workspaces are excluded

The sync startup path excludes:

- `space.type === "worktree"`

This is a very important signal.

It suggests that worktree-based workspaces are treated as local execution contexts that do not need this cross-context SSE ingestion path.

The sync bridge is therefore aimed at non-worktree workspace types, which are presumably the more remote or externally mediated execution contexts.

---

# 6. `workspaceEventLoop(...)`: the core sync loop

The event loop repeatedly:

- resolves the adaptor for the workspace type
- fetches `"/event"` with `GET`
- checks that the response is usable
- parses the body with `parseSSE(...)`
- re-emits each event into `GlobalBus`
- retries after short delays on failure or stream termination

This is the authoritative event-ingestion loop for synchronized workspaces.

---

# 7. Why the loop fetches `"/event"`

This is not a random endpoint.

It is the same conceptual event surface used elsewhere in the server for instance-bound runtime streaming.

That reuse matters.

OpenCode is trying to standardize around a common idea:

- an execution context can expose a live `/event` SSE stream

Then higher-level control-plane layers can bridge or aggregate those streams.

That is strong architectural consistency.

---

# 8. Why the adaptor owns the fetch step

The loop does not fetch directly with raw URLs.

Instead it does:

- `adaptor.fetch(space, "/event", { method: "GET", signal })`

This is critical because different workspace backends may reach their execution targets differently.

The workspace sync loop should not need to know whether the backend is:

- local
- remote
- in-process
- proxied

That is exactly what the adaptor abstraction is for.

---

# 9. Failure handling before parsing begins

If the event fetch returns:

- no response
- a non-OK response
- no body

the loop sleeps for:

- `1000 ms`

and retries.

This is a simple but practical health-check pattern.

The loop treats the workspace event stream as something that may become available later rather than a one-shot fatal dependency.

---

# 10. `parseSSE(...)`: explicit SSE parsing

`parseSSE(...)` is a small custom parser that:

- reads a `ReadableStream<Uint8Array>`
- decodes UTF-8 text incrementally
- normalizes line endings
- splits on double newlines into SSE messages
- parses `data:`, `id:`, and `retry:` fields
- joins `data:` lines together
- attempts JSON parsing
- falls back to a synthetic `sse.message` event shape when JSON parsing fails

This function is central to the bridge.

---

# 11. Why a custom parser is useful here

The bridge only needs a specific subset of SSE behavior and wants direct access to the stream and abort signal.

A custom parser gives OpenCode:

- control over fallback behavior
- direct support for JSON-first event payloads
- simple integration with the workspace retry loop

That is a reasonable choice for infrastructure code like this.

---

# 12. Why line-ending normalization matters

The parser normalizes:

- `\r\n` to `\n`
- `\r` to `\n`

This is important because SSE data may come from different environments or transports.

Normalizing line endings avoids subtle framing bugs when splitting messages by blank lines.

It is a small but good robustness measure.

---

# 13. Why `data:` lines are joined

SSE permits multiple `data:` lines in one message.

The parser collects them and joins with newlines.

That means it respects a key part of SSE framing instead of assuming every event is single-line.

This is important for correctness, especially when event payloads are larger structured JSON blobs.

---

# 14. Why `id:` and `retry:` are retained

The parser tracks:

- last event `id`
- server-provided `retry`

Even though the sync loop itself does not implement SSE reconnection using those fields, the fallback path preserves them in synthetic events.

That is useful because it prevents those transport-level hints from being silently discarded when a message is not JSON.

---

# 15. JSON-first event handling is a key contract assumption

After collecting `data:` lines, the parser tries:

- `JSON.parse(raw)`

and passes the parsed object to `onEvent(...)`.

This shows that OpenCode expects workspace `/event` streams to primarily carry JSON-serialized event objects rather than arbitrary text.

That expectation aligns with the rest of the server event model.

---

# 16. Why there is a fallback synthetic `sse.message` event

If JSON parsing fails, the parser emits:

- `type: "sse.message"`
- `properties.data`
- `properties.id`
- `properties.retry`

This is a good compromise.

Instead of losing the message or crashing the sync loop, OpenCode wraps raw SSE data into a normal event-shaped object.

That preserves observability.

---

# 17. Why this fallback is meaningful, not just defensive

The fallback effectively says:

- even if the upstream event stream is not perfectly aligned with expected JSON event payloads, the bridge should continue and surface what it received

That can be very valuable during development, debugging, or partial interoperability with workspace backends.

---

# 18. Abort behavior is integrated into the parser

`parseSSE(...)`:

- registers an abort listener
- cancels the reader on abort
- stops the loop when the signal is aborted
- releases the reader lock in `finally`

This is good stream hygiene.

It means sync shutdown is cooperative and does not leave the stream reader hanging.

---

# 19. Why `workspaceEventLoop(...)` re-emits into `GlobalBus`

The loop passes parsed events into:

- `GlobalBus.emit("event", { directory: space.id, payload: event })`

This is the critical bridge action.

It turns workspace-local activity into globally observable server events.

Without this step, workspace events would remain siloed behind each workspace backend.

---

# 20. Why the re-emitted source label is `space.id`

The event envelope uses:

- `directory: space.id`

rather than a literal filesystem path.

That is semantically important.

The source of the event is the workspace resource, not just a directory string.

This reinforces the idea that workspaces are first-class execution identities.

---

# 21. Stream termination and retry after active connection loss

After `parseSSE(...)` returns, the loop waits:

- `250 ms`

and tries again.

This means the system distinguishes between:

- initial fetch failure -> `1000 ms` retry
- dropped/ended live SSE stream -> `250 ms` retry

That is a small but thoughtful distinction.

It helps reconnect faster after transient stream endings without hammering a clearly unavailable backend.

---

# 22. The loop is intentionally endless until aborted

`workspaceEventLoop(...)` runs:

- `while (!stop.aborted)`

This means synchronization is meant to be long-lived background infrastructure, not a one-time import of workspace events.

That is exactly what you want for keeping global observability current.

---

# 23. Error handling at the orchestration layer

Each loop is launched with:

- `.catch((error) => log.warn("workspace sync listener failed", ...))`

This is important because it prevents one workspace sync failure from crashing the whole orchestration path.

The system treats workspace sync as best-effort, resilient background infrastructure.

---

# 24. The workspace server relay route

`workspace-server/routes.ts` defines:

- `GET /event`

which:

- subscribes to `GlobalBus`
- emits `event.payload`
- sends `server.connected`
- sends heartbeats every 10 seconds
- unsubscribes on abort

This route is the event relay surface used inside the workspace server app.

---

# 25. Why the workspace server relay matters

At first glance, the workspace relay may look odd because it listens to `GlobalBus` and re-emits payloads only.

But architecturally it provides a standardized event endpoint inside the workspace-server app model.

That means adaptor-backed requests to workspace `/event` can speak the same SSE language as other parts of the system.

This is part of the event-surface unification story.

---

# 26. A subtle but important consequence of the workspace relay

The relay sends:

- `event.payload`

not the outer `{ directory, payload }` global envelope.

So consumers of workspace `/event` see the event payload stream itself, not the global source-label wrapper.

That is consistent with `/event` semantics elsewhere, where scope is assumed by the subscription context.

This is a good and coherent choice.

---

# 27. Relationship to `WorkspaceServer.App()`

The worktree adaptor’s `fetch(...)` path constructs a request against:

- `WorkspaceServer.App().fetch(request)`

and injects the right directory/workspace headers.

That means even local worktree-backed workspaces can be modeled as if they expose a workspace-local HTTP API, including `/event`.

This is a very elegant abstraction layer.

---

# 28. Why the bridge design is architecturally strong

This design reuses the same key concepts at multiple layers:

- `/event` as the runtime event surface
- adaptors as the transport/backend boundary
- `GlobalBus` as the server-wide aggregation layer
- SSE as the streaming format

Because the system reuses these concepts rather than inventing special cases everywhere, the architecture stays understandable even as workspaces become more complex.

---

# 29. Main limitations and caveats

The current bridge also has important caveats:

- it assumes workspace `/event` streams are primarily JSON event payloads
- it uses a field named `directory` for source labels even when the label is a workspace ID
- it treats sync as best-effort without strong durability guarantees
- it does not appear to deduplicate or transform event taxonomies between workspace-local and global views

These are not necessarily flaws, but they are important contract realities.

---

# 30. A representative end-to-end event-sync flow

A typical synchronized event flow looks like this:

## 30.1 A workspace backend exposes `/event`

- directly or through `WorkspaceServer`

## 30.2 `workspaceEventLoop(...)` fetches that stream via the workspace adaptor

- `adaptor.fetch(space, "/event", ...)`

## 30.3 `parseSSE(...)` incrementally parses the stream

- JSON event payloads are reconstructed from SSE frames

## 30.4 Each event is re-emitted into `GlobalBus`

- tagged with `directory: space.id`

## 30.5 Global observers receive the event through `/global/event`

- as a globally sourced workspace event

This is the actual distributed observability bridge in action.

---

# 31. Key design principles behind this module

## 31.1 Workspace event observability should use the same event-stream vocabulary as the rest of the server

So workspaces expose or proxy `/event` and are parsed as SSE JSON event streams.

## 31.2 Transport and backend specifics should stay behind adaptors

So the sync loop only knows it can `fetch(...)` an event stream from a workspace.

## 31.3 Global observability requires re-emitting workspace-local events into a shared aggregation layer

So parsed events are bridged into `GlobalBus`.

## 31.4 Stream infrastructure should stay resilient to transient failure and imperfect payloads

So the loop retries and `parseSSE(...)` falls back to synthetic `sse.message` events when JSON parsing fails.

---

# 32. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/control-plane/workspace.ts`
2. `packages/opencode/src/control-plane/sse.ts`
3. `packages/opencode/src/control-plane/workspace-server/routes.ts`
4. `packages/opencode/src/control-plane/workspace-server/server.ts`
5. `packages/opencode/src/control-plane/adaptors/worktree.ts`

Focus on these functions and concepts:

- `Workspace.startSyncing()`
- `workspaceEventLoop()`
- `parseSSE()`
- `GlobalBus.emit("event", ...)`
- workspace `/event` relay behavior
- `WorkspaceServer.App()`
- retry timing and abort handling

---

# 33. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Which non-worktree workspace types are expected to participate in the sync bridge, and what guarantees do they provide about `/event` payload shape?
- **Question 2**: Should workspace-synchronized events be transformed or tagged more explicitly before entering `GlobalBus`?
- **Question 3**: Could the `directory` field in the global envelope be renamed to better reflect workspace-ID source labels?
- **Question 4**: Why does the workspace-server `/event` route relay all `GlobalBus` events rather than filtering by workspace context, and is that intentional long-term?
- **Question 5**: Should `parseSSE(...)` eventually support richer SSE features like named event types, or is JSON-in-data sufficient for OpenCode’s model?
- **Question 6**: How should clients reason about duplicates or loops if workspace event streams are reintroduced into broader global observation paths?
- **Question 7**: Are the current retry timings sufficient for unstable remote workspace backends?
- **Question 8**: What additional diagnostics would help operators understand when workspace sync is stale, disconnected, or replaying malformed SSE payloads?

---

# 34. Summary

The `workspace_event_sync_and_sse_bridge` layer is the runtime machinery that makes workspace activity visible beyond the workspace itself:

- `workspaceEventLoop(...)` fetches workspace `/event` streams through adaptors
- `parseSSE(...)` reconstructs JSON-first event payloads from SSE frames
- parsed events are re-emitted into `GlobalBus` with workspace-scoped source labels
- the workspace-server app provides a standardized `/event` relay surface that fits into the same event vocabulary as the rest of the system

So this module is the distributed event bridge that turns multi-workspace execution into a coherent global observability model.
