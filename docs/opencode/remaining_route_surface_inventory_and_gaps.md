# Remaining Route Surface Inventory / Gaps

---

# 1. Purpose

This document closes the current route-surface documentation pass by inventorying the server route modules and identifying what has already been documented, what is only covered indirectly, and what still deserves standalone treatment.

The goal is not to re-document every route again.

The goal is to make the coverage state explicit.

---

# 2. Route inventory reviewed in this pass

The `packages/opencode/src/server/routes` directory currently contains:

- `config.ts`
- `experimental.ts`
- `file.ts`
- `global.ts`
- `mcp.ts`
- `permission.ts`
- `project.ts`
- `provider.ts`
- `pty.ts`
- `question.ts`
- `session.ts`
- `tui.ts`
- `workspace.ts`

This matches the route tree mounted from `server.ts` plus the nested workspace routes used under `/experimental/workspace`.

---

# 3. Route surfaces already documented directly

The following route modules now have dedicated standalone route-surface articles:

- `session.ts` -> `session_route_surface_and_control_plane_api.md`
- `permission.ts` -> `permission_routes_and_http_approval_api.md`
- `question.ts` -> `question_routes_and_http_clarification_api.md`
- `pty.ts` -> `pty_route_surface_and_remote_terminal_api.md`
- `global.ts` -> `global_routes_and_server_level_control_plane.md`
- `provider.ts` -> `provider_route_surface_and_runtime_discovery_api.md`
- `config.ts` -> `config_routes_and_runtime_configuration_api.md`
- `experimental.ts` -> `experimental_routes_and_operator_surfaces.md`
- `project.ts` -> `project_routes_and_project_identity_api.md`
- `file.ts` -> `file_routes_and_filesystem_surface.md`
- `mcp.ts` -> `mcp_routes_and_external_server_surface.md`
- `tui.ts` -> `tui_routes_and_terminal_ui_surface.md`

That covers the main route modules mounted by the server.

---

# 4. Route-adjacent server surfaces already documented separately

Some important server-facing surfaces are not individual route files but still needed dedicated documentation. Those are also covered now:

- `server.ts` request scoping and `/event` -> `global_event_stream_and_instance_scoping_api.md`
- `server.ts` bootstrap/auth/CORS/OpenAPI/proxy -> `server_bootstrap_auth_cors_and_proxy_surface.md`
- OpenAPI contract generation -> `openapi_generation_and_sdk_contract_surface.md`
- workspace forwarding middleware -> `workspace_router_and_remote_workspace_forwarding.md`

These are important because the route files only make full sense when read alongside these server-wide behaviors.

---

# 5. Coverage that exists only indirectly right now

One route file is covered indirectly but not yet in its own dedicated article:

- `workspace.ts`

It was discussed inside:

- `experimental_routes_and_operator_surfaces.md`
- `workspace_router_and_remote_workspace_forwarding.md`

But it has not yet been given a standalone document focused specifically on project-bound workspace CRUD and its relationship to project identity.

So this is the clearest remaining direct route-surface gap.

---

# 6. Important implementation gaps discovered during route review

This pass also exposed some notable source-level gaps or caveats:

- `GET /find/symbol` is documented as LSP-backed symbol search but currently returns `[]`
- `POST /tui/open-themes` currently publishes `session.list` rather than a theme-specific command
- some route contracts expose future-facing or broader capabilities whose runtime behavior may still be evolving, especially under `/experimental`

These are not documentation problems.

They are source-level realities worth tracking.

---

# 7. High-level coverage conclusion

At this point, the route-surface series covers:

- the core session control plane
- interrupt/approval APIs
- PTY control
- provider and config management
- global and instance-level server surfaces
- filesystem discovery
- MCP integration management
- TUI integration
- experimental/operator surfaces
- project identity

That means the major server-side HTTP control plane is now largely documented as a coherent set of modules rather than a pile of disconnected endpoints.

---

# 8. What still merits direct follow-up

The strongest next standalone route-focused candidate is:

- `workspace.ts` -> project-bound workspace CRUD and its role in the experimental/federated workspace model

After that, route coverage is essentially complete enough that the next worthwhile documents should probably shift from route files to deeper runtime modules or to cross-cutting synthesis articles.

---

# 9. Suggested transition after workspace

Once the standalone workspace route article is complete, the documentation stream can naturally move to one of these directions:

- deeper workspace runtime and adaptor internals
- event contract stability and client-consumption patterns
- MCP runtime internals beyond the route layer
- filesystem boundary enforcement versus HTTP file routes
- TUI bridge internals and command model

In other words, after workspace CRUD, the remaining work is more about depth and synthesis than route enumeration.

---

# 10. Open questions for further investigation

- **Question 1**: Should `workspace.ts` remain nested under `/experimental`, or is it becoming stable enough to justify a top-level namespace eventually?
- **Question 2**: Which route-level inconsistencies discovered in this pass are deliberate versus accidental, especially `find/symbol` and `open-themes`?
- **Question 3**: Which route contracts are considered externally stable enough to support generated SDK commitments?
- **Question 4**: After route coverage is complete, which runtime modules are the most important next layer to document in the same level of detail?

---

# 11. Summary

The current route-surface pass now covers nearly the entire server HTTP API directly.

The only clear remaining standalone route article is the nested workspace CRUD surface in `server/routes/workspace.ts`.

After that, the documentation effort can move from route enumeration to deeper runtime and cross-cutting architecture analysis.
