# Experimental Routes / Operator Surfaces

---

# 1. Module Purpose

This document explains the `/experimental` route namespace in OpenCode, which groups together advanced, operator-facing, and still-evolving control-plane surfaces.

The key questions are:

- Why does OpenCode maintain an `/experimental` namespace at all?
- What kinds of capabilities are exposed there today?
- Why are tool discovery, workspace management, worktree control, global session listing, and MCP resource inspection grouped together?
- How does this route family differ from the stable, task-oriented control surfaces like `/session`, `/permission`, or `/pty`?
- What does this module reveal about OpenCode’s evolving operator and platform APIs?

Primary source files:

- `packages/opencode/src/server/routes/experimental.ts`
- `packages/opencode/src/server/routes/workspace.ts`
- `packages/opencode/src/tool/registry.ts`
- `packages/opencode/src/session/index.ts`
- `packages/opencode/src/mcp`
- `packages/opencode/src/worktree`

This layer is OpenCode’s **experimental operator-facing control-plane surface**.

---

# 2. Why `/experimental` exists

Some server capabilities do not fit neatly into the core stable route families.

They may be:

- still evolving
- more operator-oriented than end-user-oriented
- cross-cutting across multiple runtime subsystems
- highly useful for tooling, debugging, or orchestration

Placing them under `/experimental` makes that status explicit.

It communicates:

- these APIs are real and useful
- but they are not necessarily the most stable public surface yet

That is a healthy pattern.

---

# 3. This namespace is intentionally eclectic

The `/experimental` surface includes:

- tool ID listing
- tool schema listing for a provider/model pair
- nested workspace routes
- worktree creation/listing/removal/reset
- global session listing
- MCP resource listing

At first glance, this looks heterogeneous.

But there is a coherent pattern:

these are all **operator or platform introspection/control surfaces** that sit above a normal end-user conversation loop.

---

# 4. Why these routes are not under the main task-oriented namespaces

Core namespaces like:

- `/session`
- `/question`
- `/permission`
- `/pty`

map directly to stable runtime objects or user interaction flows.

The `/experimental` routes are different.

They often expose:

- global discovery
- orchestration helpers
- debugging or introspection surfaces
- still-maturing control-plane features

That makes `/experimental` a sensible home.

---

# 5. `GET /experimental/tool/ids`: fast tool inventory

This route returns:

- `ToolRegistry.ids()`

That means it exposes a lightweight list of all available tool IDs, including built-in and dynamically registered tools.

This is useful for:

- diagnostics
- tooling UIs
- capability inspection
- debugging tool registration problems

It is intentionally narrower than full tool definition listing.

---

# 6. Why tool IDs get their own route

A client often wants to ask a simple question first:

- what tools exist?

without paying the cost or complexity of fetching the full parameter schema for each tool.

A lightweight ID route is therefore valuable.

It supports cheap discovery and sanity checks.

---

# 7. `GET /experimental/tool`: provider/model-specific tool projection

The richer tool route accepts query params:

- `provider`
- `model`

Then it calls:

- `ToolRegistry.tools({ providerID, modelID })`

and returns, for each tool:

- `id`
- `description`
- `parameters`

This is a much more important route than it may first appear.

It exposes the model-conditioned tool surface that the runtime would actually make available.

---

# 8. Why tool listing depends on provider and model

In OpenCode, tool availability is not purely global.

It may depend on:

- provider capabilities
- model capabilities
- registry filtering logic
- dynamic plugin/tool registration

So exposing a generic “all tools” schema would be misleading.

The experimental route gets this right by making tool projection contextual.

---

# 9. JSON schema conversion is an important detail

For tool parameters, the route does:

- if the parameter shape looks like a Zod schema, convert it with `zodToJsonSchema(...)`
- otherwise return the parameters as-is

This is a very pragmatic interoperability choice.

It means callers receive a JSON-schema-like representation regardless of whether the tool definition originated as:

- a Zod schema
- plain JSON schema

That makes the route much easier for external tools and UIs to consume.

---

# 10. Why this route is especially valuable for operator tooling

A tool-inspection UI or integration layer can use `/experimental/tool` to answer:

- which tools are exposed to this provider/model pair?
- how should their parameters be rendered?
- what descriptions should be shown?

That is extremely useful for debugging, agent visualization, and capability introspection.

This is exactly the kind of surface that belongs in an experimental/operator namespace.

---

# 11. Nested `/experimental/workspace` routes

The experimental route tree mounts:

- `.route("/workspace", WorkspaceRoutes())`

The nested workspace routes expose:

- `POST /experimental/workspace/`
- `GET /experimental/workspace/`
- `DELETE /experimental/workspace/:id`

These routes are an important complement to the experimental workspace-router middleware documented elsewhere.

They give clients a way to manage workspace objects themselves.

---

# 12. Why workspace management is still experimental

Workspace concepts are clearly central to OpenCode’s emerging remote/federated control plane.

But they are still behind an experimental umbrella in the route surface.

That suggests the workspace model is real but not yet treated as a fully settled stable API contract.

This is consistent with the forwarding middleware and experimental workspace flag elsewhere in the codebase.

---

# 13. Workspace creation is project-bound

`POST /experimental/workspace/` validates a workspace-create schema with `projectID` omitted from the request body, then injects:

- `projectID: Instance.project.id`

before calling `Workspace.create(...)`.

This is a very important design detail.

It means clients do not arbitrarily assign workspace ownership.

The server binds the new workspace to the current project context authoritatively.

---

# 14. Why project binding matters for workspace creation

A workspace is not a floating global object.

It belongs to a project context.

Letting the client freely set `projectID` would weaken that invariant.

By injecting the current project ID server-side, the route keeps workspace ownership aligned with instance context.

That is good control-plane hygiene.

---

# 15. Workspace listing is also project-aware

`GET /experimental/workspace/` returns:

- `Workspace.list(Instance.project)`

So workspace enumeration is scoped to the current project, not all possible workspaces everywhere.

This again shows that workspaces are attached to project identity, not treated as a totally separate top-level world.

---

# 16. Workspace deletion is a direct management action

`DELETE /experimental/workspace/:id` validates the workspace ID and delegates to:

- `Workspace.remove(id)`

This gives operator clients a full minimal lifecycle for workspace objects:

- create
- list
- remove

That is enough to make workspaces manageable without yet overcomplicating the route surface.

---

# 17. Worktree management routes

The experimental namespace also exposes:

- `POST /experimental/worktree`
- `GET /experimental/worktree`
- `DELETE /experimental/worktree`
- `POST /experimental/worktree/reset`

These routes make git worktree sandbox management a formal API concern.

That is significant because worktrees are not just a local CLI trick in OpenCode.

They are part of the operational model.

---

# 18. Why worktrees belong in an experimental/operator surface

Worktree creation and reset are powerful operations that are especially useful for:

- sandboxing
- branch isolation
- agent experimentation
- operator workflows
- advanced tooling

They are not necessarily part of the most common end-user conversation loop.

So their placement under `/experimental` is sensible.

---

# 19. Worktree creation is a high-level workflow operation

`POST /experimental/worktree` validates against:

- `Worktree.create.schema`

and then calls:

- `Worktree.create(body)`

The route description explicitly says it can also run configured startup scripts.

That means worktree creation is not just file-system branching.

It is an environment setup operation.

---

# 20. Worktree listing reflects project sandbox state

`GET /experimental/worktree` does not call a generic worktree scan directly in the route.

Instead it returns:

- `Project.sandboxes(Instance.project.id)`

This is notable because the route is exposing the project’s known sandbox inventory, not merely whatever git worktrees happen to exist on disk.

That is a more meaningful operator view.

---

# 21. Worktree removal updates both worktree state and project sandbox state

`DELETE /experimental/worktree` does two things:

- `Worktree.remove(body)`
- `Project.removeSandbox(Instance.project.id, body.directory)`

This is a critical detail.

It shows worktree removal is not just a git operation.

It also updates project-level bookkeeping about sandbox membership.

That is exactly the kind of higher-level lifecycle coupling that an operator-facing API should expose cleanly.

---

# 22. Worktree reset is a control-plane primitive

`POST /experimental/worktree/reset` validates:

- `Worktree.reset.schema`

and delegates to:

- `Worktree.reset(body)`

The route description says it resets a worktree branch to the primary default branch.

This gives clients a formal recovery/reset mechanism for sandbox branches.

That is powerful and operationally useful.

---

# 23. `GET /experimental/session`: global session discovery

This route is especially important.

Unlike instance-bound session routes, it exposes:

- `Session.GlobalInfo.array()`

and delegates to:

- `Session.listGlobal(...)`

with filters like:

- `directory`
- `roots`
- `start`
- `cursor`
- `search`
- `limit`
- `archived`

This is a global discovery surface for sessions across projects.

---

# 24. Why global session listing is experimental

This route does not fit the normal per-session control flow.

It is closer to an operator or dashboard query surface:

- search across sessions
- paginate recent activity
- filter archived state
- find root sessions only

That makes it a strong fit for `/experimental`.

It is discovery and administration oriented rather than direct session execution control.

---

# 25. Cursor pagination and the `x-next-cursor` header

The route fetches up to:

- `limit + 1`

then trims the response and, if more results remain, sets:

- `x-next-cursor`

based on the last returned session’s `time.updated`.

This is a well-designed pagination pattern.

It gives clients incremental traversal over a large global session list without overloading the response body contract.

---

# 26. Why this route is useful for dashboards and operators

A client can use `/experimental/session` to build:

- recent sessions lists
- cross-project search
- archived session browsers
- root-thread overviews
- activity feeds

That is broader than a normal conversation UI and strongly suggests operator or admin tooling use cases.

---

# 27. `GET /experimental/resource`: MCP resource inspection

The final route returns:

- `await MCP.resources()`

as a map of MCP resource objects.

This is another operator-facing introspection endpoint.

It exposes what MCP-connected resources are currently visible to the runtime.

---

# 28. Why MCP resource listing fits here

MCP resource inspection is not a common end-user action during a single conversation turn.

It is more useful for:

- debugging MCP integrations
- building operator panels
- checking connected server state
- inspecting system capabilities

That fits the `/experimental` namespace very well.

---

# 29. What unifies this whole namespace

Although the endpoints look diverse, they share a clear theme:

they expose **higher-level platform introspection and management surfaces** that are valuable for advanced clients, operators, dashboards, and still-evolving product features.

That includes:

- tool capability inspection
- workspace management
- worktree sandbox control
- global session discovery
- MCP resource inspection

This is a coherent category.

---

# 30. Why this namespace matters architecturally

The `/experimental` routes show that OpenCode is not only a chat/session runtime.

It is also growing into a broader programmable platform with operator surfaces for:

- introspection
- environment control
- multi-workspace management
- global discovery
- advanced tooling integration

This namespace is where some of that future platform surface is incubated.

---

# 31. A representative operator workflow through `/experimental`

A realistic advanced workflow could look like this:

## 31.1 Inspect available tools for a provider/model pair

- `GET /experimental/tool`

## 31.2 Create or list workspaces

- `POST /experimental/workspace/`
- `GET /experimental/workspace/`

## 31.3 Create a sandbox worktree

- `POST /experimental/worktree`

## 31.4 Query recent sessions across projects

- `GET /experimental/session`

## 31.5 Inspect available MCP resources

- `GET /experimental/resource`

This is clearly an operator/platform workflow, not just a single chat turn.

---

# 32. Key design principles behind this module

## 32.1 Evolving or operator-oriented control surfaces should be clearly separated from the core stable runtime APIs

So these features live under `/experimental`.

## 32.2 Introspection surfaces should expose runtime-effective views, not only raw internal state

So tools are projected by provider/model, sessions are listed globally with filters, and worktrees reflect project sandbox bookkeeping.

## 32.3 Higher-level lifecycle operations should remain authoritative at the server

So workspace creation injects project identity and worktree removal updates project sandbox state.

## 32.4 Experimental does not mean ad hoc

These routes are still typed, validated, documented, and integrated into the broader control plane.

---

# 33. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/routes/experimental.ts`
2. `packages/opencode/src/server/routes/workspace.ts`
3. `packages/opencode/src/tool/registry.ts`
4. `packages/opencode/src/session/index.ts`
5. `packages/opencode/src/control-plane/workspace.ts`
6. `packages/opencode/src/worktree`
7. `packages/opencode/src/mcp`

Focus on these functions and concepts:

- `ToolRegistry.ids()`
- `ToolRegistry.tools(...)`
- `zodToJsonSchema(...)`
- `Workspace.create/list/remove`
- `Worktree.create/remove/reset`
- `Project.sandboxes()`
- `Session.listGlobal(...)`
- `MCP.resources()`

---

# 34. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Which of these experimental routes are expected to graduate into stable namespaces first?
- **Question 2**: How stable is the provider/model-conditioned tool projection contract for external consumers?
- **Question 3**: What additional metadata might advanced clients need for global session pagination and discovery?
- **Question 4**: How do workspace CRUD routes interact with the remote-workspace forwarding model and adaptor implementations?
- **Question 5**: What exact startup-script and environment behaviors occur during `Worktree.create(...)`?
- **Question 6**: Should MCP resource inspection eventually support filtering, provenance, or live event subscriptions?
- **Question 7**: Are there authorization concerns around exposing worktree and global session discovery surfaces in less-trusted deployments?
- **Question 8**: Does `/experimental` risk becoming too broad, and should some of these surfaces eventually split into their own stable namespaces?

---

# 35. Summary

The `experimental_routes_and_operator_surfaces` layer groups together OpenCode’s advanced and still-evolving control-plane capabilities:

- tool inventory and schema inspection
- project-bound workspace lifecycle management
- worktree sandbox creation and recovery operations
- global session discovery with pagination and filtering
- MCP resource introspection

So this namespace is not random overflow. It is the operator/platform incubation area where OpenCode exposes powerful discovery and orchestration APIs that sit above the core task-oriented runtime surfaces.
