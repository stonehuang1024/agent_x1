# Workspace Routes / Project-Bound Workspace CRUD

---

# 1. Module Purpose

This document explains `server/routes/workspace.ts` as the focused CRUD surface for OpenCode workspace objects.

The key questions are:

- Why does OpenCode expose workspace CRUD separately from general project or instance routes?
- How are workspaces bound to projects rather than treated as free-floating global objects?
- Why is the workspace route surface currently nested under `/experimental/workspace`?
- How does this route layer relate to workspace routing and remote-workspace forwarding?
- What does this API reveal about OpenCode’s evolving multi-workspace control plane?

Primary source files:

- `packages/opencode/src/server/routes/workspace.ts`
- `packages/opencode/src/control-plane/workspace.ts`
- `packages/opencode/src/server/routes/experimental.ts`
- `packages/opencode/src/control-plane/workspace-router-middleware.ts`

This layer is OpenCode’s **project-bound workspace CRUD control-plane API**.

---

# 2. Why a dedicated workspace CRUD surface exists

A workspace is not the same as:

- the current instance
- the current directory
- the current project metadata row

Instead, a workspace is a control-plane object representing a managed execution target associated with a project.

Because it has its own lifecycle:

- create
- list
- remove

it deserves its own route surface.

---

# 3. Why workspace CRUD is nested under `/experimental`

The routes are mounted from `experimental.ts` as:

- `/experimental/workspace`

This is an important product signal.

It means the workspace model is real and already integrated into the server architecture, but it is still treated as evolving rather than fully stable.

That matches what we also see in:

- experimental workspace forwarding
- `OPENCODE_EXPERIMENTAL_WORKSPACES`

So the route placement is coherent with the broader architecture.

---

# 4. The route surface is intentionally minimal

`server/routes/workspace.ts` exposes:

- `POST /experimental/workspace/`
- `GET /experimental/workspace/`
- `DELETE /experimental/workspace/:id`

This is a very small CRUD surface.

That makes sense because the workspace model is still relatively early.

The route file provides just enough lifecycle management to make workspace objects controllable without overcommitting to a larger API prematurely.

---

# 5. The route layer is thin over `Workspace`

As with other OpenCode route modules, this file mostly delegates to the deeper runtime/control-plane module:

- `Workspace.create(...)`
- `Workspace.list(...)`
- `Workspace.remove(...)`

That is good structure.

The route file owns request validation and project binding, while the underlying workspace module owns actual workspace state.

---

# 6. `POST /experimental/workspace/`: create workspace

The create route validates the JSON body against:

- `Workspace.create.schema.omit({ projectID: true })`

Then it injects:

- `projectID: Instance.project.id`

before calling:

- `Workspace.create(...)`

This is the single most important behavior in the route file.

---

# 7. Why `projectID` is omitted from the request body

The client is not allowed to choose an arbitrary project owner for the workspace.

That is correct.

Workspace ownership is derived from the currently bound project context, not from untrusted client input.

This preserves a key invariant:

- workspaces are project-bound resources

not global objects the caller can attach anywhere.

---

# 8. Why project-bound creation matters architecturally

The server already has a strong model of:

- current request context
- current instance
- current project

Letting the client bypass that by supplying a foreign `projectID` would break the alignment between:

- request scope
- project identity
- workspace ownership

So the server-side injection of `Instance.project.id` is exactly the right design.

---

# 9. `GET /experimental/workspace/`: list workspaces for current project

The list route returns:

- `Workspace.list(Instance.project)`

This is an equally important constraint.

It means workspace enumeration is scoped to the current project.

The route does not expose a global list of all workspaces across all projects.

That is consistent with the project-bound creation model.

---

# 10. Why project-scoped listing is the correct default

A workspace is meaningful in relation to a project.

If the route returned all workspaces globally, the client would need additional logic to sort out:

- which project they belong to
- which ones are relevant to the current instance

By scoping listing to `Instance.project`, the API stays aligned with the request context and current operator intent.

---

# 11. `DELETE /experimental/workspace/:id`: remove workspace

The delete route validates:

- path param `id` using `Workspace.Info.shape.id`

then calls:

- `Workspace.remove(id)`

and returns the removed workspace or `undefined`.

This is a straightforward deletion surface.

---

# 12. Why remove returns the removed workspace optionally

Returning `Workspace.Info.optional()` is a practical contract choice.

It gives clients a way to observe what was removed when successful, while still allowing absence semantics if the implementation chooses to surface them that way.

It is slightly richer than a bare boolean and more useful for UI state reconciliation.

---

# 13. Why this route file is still important despite its size

It would be easy to dismiss `workspace.ts` as too small to matter.

That would be a mistake.

This file encodes the most important ownership rule in the whole workspace API:

- workspace lifecycle is project-bound

That is foundational for the wider workspace-routing and remote-execution architecture.

---

# 14. Relationship to workspace forwarding middleware

The CRUD routes here are not the same thing as the forwarding behavior in:

- `workspace-router-middleware.ts`

A useful distinction is:

## 14.1 `server/routes/workspace.ts`

- create/list/remove workspace objects

## 14.2 `WorkspaceRouterMiddleware`

- use the current workspace context to decide whether a request should be forwarded to a remote adaptor

So the route file manages workspace resources, while the middleware uses those resources to steer execution.

---

# 15. Why both layers are necessary

Without workspace CRUD, there would be no formal API for creating and managing workspace objects.

Without routing middleware, workspace objects would not affect actual request execution.

So these two layers form a natural pair:

- resource management
- execution-path selection

That is a coherent control-plane design.

---

# 16. Relationship to project identity

Because the create and list routes are explicitly bound to `Instance.project`, workspace CRUD belongs much closer to project identity than to session or PTY lifecycles.

This suggests a useful architectural reading:

- workspaces are project-level resources that can influence request routing

That is a strong and meaningful model.

---

# 17. Why workspace CRUD is not under `/project`

Even though workspaces are project-bound, it still makes sense that they are not nested under `/project` today.

Why?

Because workspace objects are not only metadata about a project.

They are also part of an evolving distributed execution model.

That gives them enough distinct semantics to justify their own route subtree, even while remaining project-owned.

---

# 18. Minimal CRUD suggests careful API staging

The route surface intentionally does not yet expose:

- update workspace
- inspect one workspace by ID
- workspace health
- workspace sync state
- workspace auth or transport details

That restraint is good.

It suggests the API is being staged conservatively while the model and forwarding behavior mature.

---

# 19. What this route surface implies about the underlying workspace model

Even without opening every workspace runtime file, the route layer already tells us several important things:

- workspaces have stable IDs
- workspaces have an `Info` schema
- workspaces are project-bound
- workspaces are listable per project
- workspaces are removable resources

That is enough to conclude the workspace model is already more than an internal experiment.

It is a real control-plane entity.

---

# 20. A representative workspace lifecycle through the API

A typical flow looks like this:

## 20.1 Client binds to a project instance

- current request already has `Instance.project`

## 20.2 Client creates a workspace for that project

- `POST /experimental/workspace/`

## 20.3 Client lists current project workspaces

- `GET /experimental/workspace/`

## 20.4 Client begins using workspace-aware routing elsewhere

- via workspace headers/query and middleware forwarding

## 20.5 Client removes the workspace when no longer needed

- `DELETE /experimental/workspace/:id`

This is a clean control-plane lifecycle for an evolving distributed-execution feature.

---

# 21. Why this module matters beyond CRUD

The route file is small, but it anchors an important architectural transition in OpenCode:

from:

- purely local per-directory execution

toward:

- project-bound workspace objects that can eventually represent remote or alternative execution contexts

That makes this file more significant than its code size suggests.

---

# 22. Key design principles behind this module

## 22.1 Workspaces should be owned by the current project context, not arbitrarily assigned by clients

So `projectID` is omitted from input and injected from `Instance.project.id`.

## 22.2 Workspace enumeration should default to the current project scope

So listing uses `Workspace.list(Instance.project)`.

## 22.3 Workspace lifecycle management and workspace-based request forwarding are distinct layers

So CRUD lives in route handlers while routing decisions live in middleware.

## 22.4 Experimental distributed-control-plane features should start with a small, clear API

So the route surface remains minimal and focused.

---

# 23. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/routes/workspace.ts`
2. `packages/opencode/src/control-plane/workspace.ts`
3. `packages/opencode/src/control-plane/workspace-router-middleware.ts`
4. `packages/opencode/src/server/routes/experimental.ts`
5. `packages/opencode/src/project/project.ts`

Focus on these functions and concepts:

- `Workspace.create()`
- `Workspace.list()`
- `Workspace.remove()`
- `Instance.project.id`
- workspace IDs and `Workspace.Info`
- request forwarding based on workspace context

---

# 24. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: What exact fields are contained in `Workspace.Info`, and how do they map to remote adaptor behavior?
- **Question 2**: Should the route surface eventually support `GET /:id` and `PATCH /:id` once workspace semantics stabilize?
- **Question 3**: How are workspace objects persisted, and what lifecycle rules govern cleanup when projects disappear?
- **Question 4**: How do workspace CRUD operations affect existing forwarded requests or active workspace-bound clients?
- **Question 5**: Should workspaces remain nested under `/experimental`, or are they on track to become a top-level stable namespace?
- **Question 6**: How should auth, permissions, and ownership be handled for workspace management in less-trusted deployments?
- **Question 7**: What invariants connect workspace objects to project sandboxes, worktrees, or remote execution backends?
- **Question 8**: How should clients best combine workspace CRUD, workspace routing, and global event streams to build multi-workspace UX?

---

# 25. Summary

The `workspace_routes_and_project_bound_workspace_crud` layer exposes the basic lifecycle of workspace objects as a project-bound control-plane API:

- creation injects the current project ID server-side
- listing is scoped to the current project
- removal operates on stable workspace IDs
- the route surface complements, but is distinct from, the middleware that uses workspaces for remote request forwarding

So this module is not just tiny CRUD glue. It is the ownership-defining API surface for OpenCode’s evolving workspace and distributed-execution model.
