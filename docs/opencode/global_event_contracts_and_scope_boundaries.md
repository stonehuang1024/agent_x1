# Global Event Contracts / Scope Boundaries

---

# 1. Module Purpose

This document explains the event-scope model that emerges across OpenCode’s server runtime, focusing on the distinction between instance-bound events, global events, workspace-synchronized events, and the implications for client consumption.

The key questions are:

- What is the difference between `Bus` and `GlobalBus` in practical terms?
- How do `/event` and `/global/event` differ at the transport and semantic levels?
- How are workspace events synchronized into the global event stream?
- Why do some global events use filesystem-like directory labels while workspace-synchronized events use workspace IDs?
- What should clients understand about scope boundaries before subscribing to these streams?

Primary source files:

- `packages/opencode/src/server/server.ts`
- `packages/opencode/src/server/routes/global.ts`
- `packages/opencode/src/control-plane/workspace.ts`
- `packages/opencode/src/bus`
- `packages/opencode/src/bus/global`

This layer is OpenCode’s **event-scope boundary and stream-contract model**.

---

# 2. Why this topic matters

OpenCode is highly event-driven.

But not all events live at the same scope.

Clients can easily misunderstand the system if they assume:

- there is only one event stream
- all event payloads mean the same thing everywhere
- directory labels and workspace identifiers are interchangeable

They are not.

Understanding event scope is essential for building correct clients and dashboards.

---

# 3. The first major distinction: `Bus` versus `GlobalBus`

A useful mental model is:

## 3.1 `Bus`

- instance/runtime-local event backbone
- reflects activity within the currently bound instance context

## 3.2 `GlobalBus`

- server-wide aggregation and coordination backbone
- carries events that matter across instances or across workspace boundaries

This distinction is foundational.

It explains why OpenCode exposes more than one event stream.

---

# 4. `/event`: instance-bound live runtime stream

In `server.ts`, `GET /event` subscribes with:

- `Bus.subscribeAll(...)`

and emits SSE data containing the raw bus event payload.

This stream is the main event channel for the active instance context.

It is where clients see fine-grained runtime updates tied to the current bound directory/workspace instance.

---

# 5. `/global/event`: cross-instance/global stream

In `global.ts`, `GET /global/event` listens to:

- `GlobalBus.on("event", handler)`

and emits SSE data containing an object with:

- `directory`
- `payload`

This means `/global/event` is not just a duplicate of `/event`.

It is a higher-scope event stream with an explicit source label around each payload.

---

# 6. Why the payload shapes differ

This is one of the most important contract differences.

## 6.1 `/event`

- emits event payloads directly

## 6.2 `/global/event`

- emits `{ directory, payload }`

That extra wrapper exists because global observation needs to tell clients:

- where this event came from

Instance-bound streams do not need that same wrapper because the request context already defines the scope.

---

# 7. Why instance streams do not need source labeling

When a client subscribes to `/event`, the stream is already tied to:

- the currently bound instance/directory/workspace context of the request

So the stream’s scope is implicit in the subscription itself.

That makes a wrapped `{ directory, payload }` envelope less necessary there.

This is a good design economy.

---

# 8. Why global streams do need source labeling

A global subscriber may care about:

- multiple instances
- multiple projects
- multiple workspaces
- server-wide lifecycle signals

Without source labeling, the client would see global events but not know what they refer to.

So `/global/event` needs an explicit source field.

That is exactly why the wrapper exists.

---

# 9. Shared transport conventions still exist across both streams

Both `/event` and `/global/event`:

- emit `server.connected` on initial connect
- emit `server.heartbeat` every 10 seconds
- set SSE-friendly headers like `X-Accel-Buffering: no`

This is important because while the semantic scopes differ, the transport conventions are intentionally similar.

That makes client implementation more uniform.

---

# 10. Why `server.connected` and heartbeat are part of the contract

These are not just implementation noise.

They are part of the observable stream protocol.

Clients can use them to:

- detect stream establishment
- keep connections alive through proxies
- distinguish “connected but idle” from “dead stream”

So these events matter for operational correctness, not just convenience.

---

# 11. Instance disposal is an explicit stream boundary in `/event`

The instance-bound stream closes itself when it observes:

- `Bus.InstanceDisposed.type`

This is a strong semantic boundary.

It tells clients that the runtime context behind the stream is no longer valid.

That means the client should not treat the connection as a generic endless subscription.

It is tied to instance lifecycle.

---

# 12. Why this matters for client design

A client listening to `/event` must be prepared to:

- reconnect after instance disposal
- potentially re-resolve the correct directory/workspace binding
- avoid assuming subscriptions are stable across instance reloads or resets

This is a core implication of instance-bound event scope.

---

# 13. Global disposal is different

`/global/dispose` emits a global event:

- `global.disposed`

through `GlobalBus`, with:

- `directory: "global"`

This shows that global lifecycle events are not scoped to one project directory or one workspace.

They are server-level control-plane signals.

---

# 14. Why `directory: "global"` is semantically useful

The global stream uses a `directory` field for source identity, but not all global events originate from a real filesystem directory.

Using:

- `directory: "global"`

is a simple way to say:

- this event belongs to global server scope rather than a specific project or workspace

This is a meaningful convention for consumers.

---

# 15. Workspace synchronization adds a third event-origin pattern

The workspace runtime introduces another important behavior:

- it fetches `/event` from workspace targets through adaptors
- parses the SSE stream
- re-emits events on `GlobalBus`

but it labels them with:

- `directory: space.id`

This creates a third kind of source identity in the global stream.

---

# 16. Why workspace-synchronized events use workspace IDs instead of filesystem directories

This is a subtle but critical design choice.

For synchronized workspace events, the meaningful source is not merely a path on disk.

It is:

- the workspace resource itself

That is why `directory` in the global event envelope can actually contain a workspace ID rather than a literal filesystem directory string.

This is semantically correct, but clients need to know it.

---

# 17. Why the field name `directory` can be misleading

Once workspace sync enters the picture, `directory` really means something closer to:

- source scope label

rather than always a literal local project directory.

That is an important contract caveat.

Consumers should not blindly assume the field is always a real filesystem path.

Sometimes it is:

- a project directory-like source
- `global`
- a workspace ID

This is one of the biggest subtle points in the event model.

---

# 18. Global stream source labels therefore have mixed semantics

A client consuming `/global/event` should be prepared for source labels that represent:

- a project/directory scope
- a global server scope
- a workspace resource scope

That means the global event envelope is best understood as:

- `source_label`

in semantics, even though the field is named `directory`.

This is worth documenting explicitly.

---

# 19. Workspace sync also shows how events cross scope boundaries

A nonlocal workspace’s `/event` stream begins as an instance-bound event stream in that workspace context.

Then the local server re-emits those events into `GlobalBus`.

So event flow can cross layers like this:

- remote/local workspace instance event
- adaptor fetch over `/event`
- parsed SSE events
- re-emitted into server-global stream

This is one of the clearest examples of OpenCode’s control-plane layering.

---

# 20. Why not every workspace type is synced the same way

`Workspace.startSyncing(project)` excludes:

- `space.type === "worktree"`

This matters because it shows the event aggregation model is not universal across all workspace types.

Some workspace backends, especially local worktree ones, may not need remote-style SSE bridging into `GlobalBus`.

That is an important client-facing implication when reasoning about where events should appear.

---

# 21. A practical client mental model

A good client mental model is:

## 21.1 Subscribe to `/event` when you care about one currently bound runtime context

- detailed, local, instance-bound activity

## 21.2 Subscribe to `/global/event` when you care about multiple contexts or server-wide coordination

- broader but more source-annotated event observation

This is the simplest practical rule for consumers.

---

# 22. Why some clients may need both streams

A sophisticated client may need:

- `/event` for the currently focused project/session view
- `/global/event` for cross-project dashboards, workspace sync, or global lifecycle awareness

Using both is reasonable because they answer different questions.

That is exactly why both exist.

---

# 23. Event scope also shapes reliability expectations

A stream’s scope affects when it should end and how reconnection should work.

## 23.1 `/event`

- tied to instance lifecycle
- can terminate on instance disposal

## 23.2 `/global/event`

- tied more to server-wide lifecycle
- may continue across many instance-level changes

So reconnection strategy should differ based on which stream the client is using.

---

# 24. Event type stability is not the only contract question

When thinking about event contracts, it is not enough to ask:

- what event types exist?

Clients also need to ask:

- at what scope do these events appear?
- what source label semantics apply?
- when can the stream end naturally?
- which events are local-only versus globally re-emitted?

Those are equally important parts of the contract.

---

# 25. Why this topic sits above individual modules

Questions, permissions, PTY sessions, session updates, project changes, and workspace sync all feed into the event story.

So event-scope documentation cannot be fully understood from any one module in isolation.

It is a cross-cutting architecture topic.

That is why it deserves its own dedicated article.

---

# 26. A representative event flow comparison

A useful comparison:

## 26.1 Local current-instance update

- event produced on `Bus`
- visible on `/event`
- source scope implicit in subscription context

## 26.2 Global server lifecycle event

- event produced on `GlobalBus`
- visible on `/global/event`
- source label may be `global`

## 26.3 Workspace-synchronized event

- workspace adaptor fetches remote/local workspace `/event`
- local server re-emits on `GlobalBus`
- visible on `/global/event`
- source label is workspace ID

This comparison captures the three most important patterns.

---

# 27. Key design principles behind this layer

## 27.1 Event streams should match the scope of the state they describe

So OpenCode exposes both instance-bound and global event channels.

## 27.2 Global aggregation requires explicit source labeling

So `/global/event` wraps payloads in `{ directory, payload }`.

## 27.3 Source labels may represent logical execution contexts, not only filesystem paths

So workspace-synchronized events use workspace IDs and global events may use `global`.

## 27.4 Stream lifecycle should reflect runtime lifecycle

So `/event` closes on instance disposal while `/global/event` serves a broader server-wide role.

---

# 28. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/server.ts`
2. `packages/opencode/src/server/routes/global.ts`
3. `packages/opencode/src/control-plane/workspace.ts`
4. `packages/opencode/src/bus`
5. `packages/opencode/src/bus/global`

Focus on these functions and concepts:

- `Bus.subscribeAll()`
- `/event`
- `GlobalBus.on/off`
- `/global/event`
- `workspaceEventLoop()`
- `startSyncing()`
- `global.disposed`
- source-label semantics in global events

---

# 29. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Should the `directory` field in `/global/event` eventually be renamed or generalized to reflect its mixed source-label semantics more accurately?
- **Question 2**: Which event types are intentionally propagated from workspaces into `GlobalBus`, and should any be filtered or transformed?
- **Question 3**: How should clients distinguish workspace IDs from filesystem directories reliably in the global event stream?
- **Question 4**: Are there event types currently visible on `/event` that should also be re-exposed globally for better observability?
- **Question 5**: Should the event system expose stronger versioning or stability guarantees for external consumers?
- **Question 6**: How should clients handle deduplication or overlap if they consume both `/event` and `/global/event` simultaneously?
- **Question 7**: Should worktree workspaces participate in a more explicit event aggregation model over time?
- **Question 8**: What tooling exists or should exist to inspect live event flows and validate scope assumptions during development?

---

# 30. Summary

The `global_event_contracts_and_scope_boundaries` layer explains how OpenCode’s event system is partitioned by scope:

- `/event` exposes instance-bound runtime activity directly from `Bus`
- `/global/event` exposes cross-instance and server-wide activity from `GlobalBus` using source labels
- workspace synchronization bridges workspace-local `/event` streams into the global stream, often tagging them with workspace IDs rather than literal directories
- stream lifecycle and client reconnection expectations differ depending on which scope is being consumed

So this topic is not just about event names. It is about understanding the scope model that makes OpenCode’s live runtime observability coherent.
