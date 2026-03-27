# Workspace Runtime Model / Adaptor Resolution

---

# 1. Module Purpose

This document explains the deeper runtime model behind OpenCode workspaces, focusing on workspace persistence, adaptor resolution, workspace creation/removal semantics, and workspace event synchronization.

The key questions are:

- What exactly is a workspace at runtime beyond the CRUD route surface?
- How do `WorkspaceInfo`, `WorkspaceTable`, and adaptor resolution work together?
- Why does workspace creation involve both database persistence and adaptor-backed provisioning?
- How does OpenCode synchronize remote workspace events into the global event stream?
- What does the adaptor model reveal about OpenCode’s long-term multi-workspace architecture?

Primary source files:

- `packages/opencode/src/control-plane/workspace.ts`
- `packages/opencode/src/control-plane/types.ts`
- `packages/opencode/src/control-plane/adaptors/index.ts`
- `packages/opencode/src/control-plane/adaptors/worktree.ts`
- `packages/opencode/src/control-plane/workspace-router-middleware.ts`

This layer is OpenCode’s **workspace runtime model and adaptor-backed execution abstraction**.

---

# 2. Why this layer matters

The route surface shows that workspaces exist.

But `workspace.ts` shows what they really are:

- persisted control-plane records
- tied to projects
- backed by adaptors that know how to configure, create, remove, and fetch through them
- optionally synchronized into the global event bus

That is a much richer model than simple CRUD.

---

# 3. `WorkspaceInfo`: the core runtime shape

The workspace model is defined in `types.ts` as:

- `id`
- `type`
- `branch`
- `name`
- `directory`
- `extra`
- `projectID`

This shape is revealing.

A workspace is not just an opaque ID.

It carries enough data to:

- identify its backend type
- associate it with a branch and directory
- attach backend-specific extra metadata
- bind it to a project

So it is a real transportable resource model.

---

# 4. Why `type` is so central

The `type` field is the pivot that determines which adaptor will manage the workspace.

That means a workspace is not defined only by its data fields.

It is also defined by:

- the implementation class of backend behavior chosen through `type`

This is a classic adaptor-based resource model.

---

# 5. The adaptor interface defines the workspace lifecycle contract

`types.ts` defines `Adaptor` with four operations:

- `configure(input)`
- `create(input, from?)`
- `remove(config)`
- `fetch(config, input, init?)`

This is the key abstraction in the whole workspace system.

It says every workspace backend must support:

- normalization/configuration
- provisioning
- teardown
- request forwarding

That is a very clean control-plane contract.

---

# 6. Why `fetch(...)` is part of the adaptor contract

This is especially important.

Workspaces are not just provisioned resources.

They are also request targets.

By making `fetch(...)` part of the adaptor interface, OpenCode directly ties workspace identity to request execution.

That is exactly why workspace CRUD and workspace routing form one coherent architecture.

---

# 7. `getAdaptor(type)`: runtime backend selection

`adaptors/index.ts` keeps a registry:

- `ADAPTORS: Record<string, () => Promise<Adaptor>>`

and resolves backends through:

- `getAdaptor(type)`

Today the built-in registry includes:

- `worktree`

This means the system is intentionally open-ended.

The current implementation is small, but the abstraction is designed for more backend types.

---

# 8. Why adaptors are lazily loaded

The built-in adaptor registry uses:

- `lazy(async () => (await import("./worktree")).WorktreeAdaptor)`

This is a good fit for experimental, pluggable control-plane backends.

It avoids eagerly loading every workspace backend at startup and keeps the architecture extensible.

---

# 9. `installAdaptor(...)`: a deliberate extension seam

`adaptors/index.ts` also exposes:

- `installAdaptor(type, adaptor)`

with comments indicating this is currently mostly for testing but may become a future extension path.

This is a very important clue.

OpenCode is already preparing for custom adaptor installation beyond the built-in set.

That means the workspace system is intended to be extensible, not hardcoded forever.

---

# 10. Workspace creation is a two-phase process

In `Workspace.create(...)`, the runtime does:

- generate or normalize workspace ID
- resolve adaptor by `type`
- call `adaptor.configure(...)`
- build the final `Info`
- persist it in the database
- call `adaptor.create(config)`

This is a subtle and important lifecycle.

Workspace creation is not just “insert row then done.”

It involves both:

- authoritative metadata shaping
- actual backend provisioning

---

# 11. Why `configure(...)` happens before persistence

The adaptor gets a chance to normalize or enrich the workspace info before the row is stored.

That is crucial because fields like:

- `name`
- `branch`
- `directory`
- `extra`

may need backend-specific resolution.

Persisting only after `configure(...)` means the stored record reflects the adaptor-resolved reality, not just raw caller input.

That is good design.

---

# 12. Why persistence happens before `adaptor.create(...)`

The current implementation writes the row to the database before calling:

- `adaptor.create(config)`

That implies the system wants the workspace to exist as a control-plane record as provisioning begins.

This can make sense for observability and lifecycle consistency, though it also raises interesting failure-mode questions if create later fails.

That ordering is a very important detail to understand.

---

# 13. `WorkspaceID.ascending(...)`: identity generation

Workspace creation uses:

- `WorkspaceID.ascending(input.id)`

This suggests workspace IDs are generated in a monotonic or ordered fashion rather than as arbitrary opaque randomness alone.

That may help with sorting, inspection, or operator ergonomics.

It is a small but notable implementation detail.

---

# 14. Workspaces are persisted in `WorkspaceTable`

The runtime stores workspace rows with:

- `id`
- `type`
- `branch`
- `name`
- `directory`
- `extra`
- `project_id`

So a workspace is a durable control-plane resource.

It is not recomputed from context alone.

This persistence is what makes CRUD and later routing/sync behavior possible.

---

# 15. `Workspace.list(project)`: simple and project-bound by design

Listing just selects rows where:

- `WorkspaceTable.project_id == project.id`

maps them through `fromRow(...)`, and sorts by ID.

This reinforces the project-ownership invariant seen in the route layer.

Project binding is not just an HTTP route convention.

It is built into the runtime persistence model.

---

# 16. `Workspace.get(id)` and `Workspace.remove(id)`

The runtime supports:

- fetch one workspace by ID
- remove by ID after loading the row

In `remove(...)`, it:

- resolves the adaptor for the row’s type
- calls `adaptor.remove(info)`
- deletes the database row
- returns the removed info

This means removal is also a two-layer lifecycle action:

- backend teardown
- control-plane record deletion

---

# 17. Why adaptor-backed remove is essential

If OpenCode deleted the row without calling the adaptor, it could orphan real backend resources.

So `adaptor.remove(...)` is not optional cleanup.

It is part of the semantic contract of deleting a workspace.

That is exactly what a serious control plane should do.

---

# 18. The built-in `worktree` adaptor

The built-in adaptor in `adaptors/worktree.ts` is especially illuminating.

It implements:

- `configure`
- `create`
- `remove`
- `fetch`

using the existing `Worktree` runtime and a local workspace server app.

This shows that today, one concrete workspace backend is:

- a managed git worktree sandbox

---

# 19. `WorktreeAdaptor.configure(...)`: derive real workspace coordinates

The adaptor calls:

- `Worktree.makeWorktreeInfo(info.name ?? undefined)`

and returns a new info object with:

- resolved `name`
- resolved `branch`
- resolved `directory`

This is a perfect example of why adaptor-level configuration exists.

The backend knows how to turn partial workspace intent into concrete worktree coordinates.

---

# 20. `WorktreeAdaptor.create(...)`: provision and bootstrap

The adaptor parses the finalized config, then calls:

- `Worktree.createFromInfo({ name, directory, branch })`

which returns a bootstrap function, and then executes that bootstrap.

This is very important.

A worktree workspace is not just declared.

It is actually created and bootstrapped as an environment.

That makes workspaces real execution contexts, not just metadata labels.

---

# 21. `WorktreeAdaptor.fetch(...)`: local fetch through a workspace server app

The adaptor’s `fetch(...)` implementation is one of the most revealing parts of the whole system.

It:

- parses the config
- imports `WorkspaceServer`
- constructs a request URL if needed
- injects `x-opencode-directory` with the workspace directory
- builds a new `Request`
- calls `WorkspaceServer.App().fetch(request)`

This means a worktree workspace is served by a local server app that behaves like a workspace-bound execution target.

That is a very elegant design.

---

# 22. Why `fetch(...)` injects `x-opencode-directory`

This header is the bridge between:

- workspace selection
- instance binding inside the downstream app

The adaptor does not need a totally separate code path for worktree execution.

It can reuse the same server machinery by rebinding the request to the target workspace directory.

That is strong architectural reuse.

---

# 23. Workspaces are not necessarily remote in the network sense

The worktree adaptor proves an important point:

“workspace” does not necessarily mean an externally hosted remote service.

A workspace can also be:

- a local alternate execution context served through an adaptor-backed local app boundary

That is a nuanced and powerful model.

It means the workspace abstraction is about execution context indirection, not only about network remotes.

---

# 24. Workspace event synchronization is built into the runtime

`Workspace.startSyncing(project)` starts event loops for listed workspaces excluding:

- `space.type === "worktree"`

For each other workspace, it calls:

- `workspaceEventLoop(space, stop.signal)`

This means the workspace runtime can subscribe to workspace-specific event streams and re-emit them into the global bus.

That is a major piece of the distributed control-plane story.

---

# 25. `workspaceEventLoop(...)`: SSE bridging into `GlobalBus`

The loop repeatedly:

- resolves the adaptor
- calls `adaptor.fetch(space, "/event", { method: "GET", signal })`
- retries when the response is absent or invalid
- parses SSE from the response body
- re-emits each event on `GlobalBus` with `directory: space.id`

This is one of the clearest architectural bridges in the whole control-plane layer.

It turns workspace-local event streams into a server-wide observable stream.

---

# 26. Why events are re-emitted with `directory: space.id`

This is a critical design choice.

The global bus does not tag workspace events with filesystem directories here.

It tags them with:

- the workspace ID

That means the workspace itself becomes the event-source identity in global observation.

This is sensible because the workspace is the actual execution target abstraction.

---

# 27. Retry behavior in workspace sync

If the event fetch fails or lacks a usable body, the loop sleeps for:

- 1000 ms

If an SSE connection fails after being established, it waits:

- 250 ms

before retrying.

This is simple but practical streaming resilience.

It shows the workspace system expects transient failures and tries to remain attached.

---

# 28. Why `worktree` is excluded from sync startup

`startSyncing(project)` filters out:

- `space.type !== "worktree"`

This is important.

It suggests worktree workspaces are treated differently from other workspace types for event syncing, likely because they are local execution contexts that do not need the same remote SSE bridge.

That is a strong hint about the intended division between local and nonlocal workspace backends.

---

# 29. Relationship to `WorkspaceRouterMiddleware`

The middleware uses:

- `Workspace.get(...)`
- `getAdaptor(workspace.type)`
- `adaptor.fetch(...)`

So the same adaptor interface powers both:

- request forwarding during normal API handling
- event-stream synchronization during background workspace sync

This is excellent architectural reuse.

One abstraction handles both execution and observability paths.

---

# 30. Why this module matters architecturally

This workspace runtime layer shows OpenCode building a serious control-plane architecture for alternate execution contexts:

- workspaces are durable resources
- adaptors normalize and provision them
- adaptors also execute requests through them
- nonlocal workspace events can be merged into the global bus
- the same abstraction supports both routing and synchronization

That is much more sophisticated than simple project sandbox management.

---

# 31. A representative workspace runtime lifecycle

A typical lifecycle looks like this:

## 31.1 Workspace creation request arrives

- route injects current `projectID`

## 31.2 Runtime resolves adaptor and configures concrete workspace details

- `adaptor.configure(...)`

## 31.3 Workspace record is persisted

- `WorkspaceTable`

## 31.4 Backend is provisioned

- `adaptor.create(...)`

## 31.5 Requests may later be routed through that workspace

- `adaptor.fetch(...)`

## 31.6 Event streams may be synchronized into `GlobalBus`

- `workspaceEventLoop(...)`

## 31.7 Workspace removal tears down backend and deletes the record

- `adaptor.remove(...)`
- database delete

This is a full control-plane lifecycle, not just CRUD.

---

# 32. Key design principles behind this module

## 32.1 Workspace resources should be backend-agnostic at the control-plane layer

So `WorkspaceInfo` is generic and behavior is delegated to adaptors.

## 32.2 The same abstraction should handle provisioning, teardown, request execution, and event access

So `Adaptor` includes `configure`, `create`, `remove`, and `fetch`.

## 32.3 Project-bound workspace identity should still allow multiple backend implementations

So `type` selects a lazily resolved adaptor rather than hardcoding one workspace model.

## 32.4 Multi-workspace systems need both routed execution and unified observability

So adaptors power both forwarded requests and workspace event synchronization into `GlobalBus`.

---

# 33. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/control-plane/types.ts`
2. `packages/opencode/src/control-plane/workspace.ts`
3. `packages/opencode/src/control-plane/adaptors/index.ts`
4. `packages/opencode/src/control-plane/adaptors/worktree.ts`
5. `packages/opencode/src/control-plane/workspace-router-middleware.ts`

Focus on these functions and concepts:

- `WorkspaceInfo`
- `Adaptor`
- `Workspace.create()`
- `Workspace.remove()`
- `getAdaptor()`
- `installAdaptor()`
- `WorktreeAdaptor.fetch()`
- `workspaceEventLoop()`
- `startSyncing()`

---

# 34. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: What additional adaptor types are planned beyond `worktree`, and how different will their `fetch(...)` semantics be?
- **Question 2**: Should workspace creation roll back the database record if `adaptor.create(...)` fails after persistence?
- **Question 3**: What exact event types are expected to flow through remote workspace `/event` streams, and how stable are they?
- **Question 4**: How should `installAdaptor(...)` evolve if custom adaptor installation becomes a supported extension mechanism?
- **Question 5**: Why are `Workspace.Event.Ready` and `Workspace.Event.Failed` defined here, and where are they intended to be emitted?
- **Question 6**: How should clients distinguish workspace-ID-tagged global events from directory-tagged global events elsewhere in the system?
- **Question 7**: Are there lifecycle races between request forwarding, sync startup, and workspace removal?
- **Question 8**: Should worktree workspaces eventually participate in the same sync/event model, or is their exclusion a permanent design choice?

---

# 35. Summary

The `workspace_runtime_model_and_adaptor_resolution` layer reveals that OpenCode workspaces are durable, project-bound execution targets backed by adaptor implementations:

- `WorkspaceInfo` captures the generic control-plane shape
- `Adaptor` defines the lifecycle contract for provisioning, removal, and request execution
- the built-in `worktree` adaptor shows how a local alternate execution context can still fit the same model
- workspace event streams can be bridged into `GlobalBus`, making multi-workspace observability part of the runtime design

So this module is not just a helper behind experimental CRUD routes. It is the core abstraction that makes OpenCode’s evolving multi-workspace control plane possible.
