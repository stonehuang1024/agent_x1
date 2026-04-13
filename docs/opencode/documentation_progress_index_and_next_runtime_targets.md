# Documentation Progress Index / Next Runtime Targets

---

# 1. Purpose

This document summarizes the current documentation coverage across the OpenCode server/control-plane pass and identifies the most valuable next runtime modules to document after the route-surface layer.

It is not a replacement for the detailed articles.

It is a navigation and planning aid.

---

# 2. What has been covered directly in this pass

The current pass now includes dedicated route/control-plane articles for:

- `session_route_surface_and_control_plane_api.md`
- `permission_routes_and_http_approval_api.md`
- `question_routes_and_http_clarification_api.md`
- `pty_route_surface_and_remote_terminal_api.md`
- `global_event_stream_and_instance_scoping_api.md`
- `global_routes_and_server_level_control_plane.md`
- `workspace_router_and_remote_workspace_forwarding.md`
- `server_bootstrap_auth_cors_and_proxy_surface.md`
- `openapi_generation_and_sdk_contract_surface.md`
- `provider_route_surface_and_runtime_discovery_api.md`
- `config_routes_and_runtime_configuration_api.md`
- `experimental_routes_and_operator_surfaces.md`
- `project_routes_and_project_identity_api.md`
- `file_routes_and_filesystem_surface.md`
- `mcp_routes_and_external_server_surface.md`
- `tui_routes_and_terminal_ui_surface.md`
- `remaining_route_surface_inventory_and_gaps.md`
- `workspace_routes_and_project_bound_workspace_crud.md`
- `workspace_runtime_model_and_adaptor_resolution.md`
- `tui_control_queue_and_bridge_protocol.md`
- `global_event_contracts_and_scope_boundaries.md`
- `provider_auth_route_and_runtime_boundary.md`

This means the server-facing route/control-plane layer is now broadly covered as a coherent set.

---

# 3. What this coverage now makes easier

Because the route layer is documented in detail, it is now much easier to reason about:

- how clients enter the system
- how requests are scoped
- which resources are instance-bound versus global
- how workspaces and MCP servers extend the control plane
- how event streams are structured
- where auth/setup ends and deeper runtime logic begins

In other words, the external surface of the system is now far less opaque.

---

# 4. What remains most valuable after route coverage

With the route enumeration largely complete, the next highest-value documents are deeper runtime modules that explain the authoritative logic behind these APIs.

The strongest immediate candidates are:

- workspace event synchronization and SSE bridging
- event-bus contract production and bus-event taxonomy
- provider-auth plugin integration details
- MCP runtime internals beyond the route layer
- file boundary enforcement versus HTTP file routes
- TUI event taxonomy and command model

These are the modules that now most naturally extend the current route documentation pass.

---

# 5. Why route coverage alone is not enough

Routes explain:

- what operations exist
- what they accept
- what they return

But they do not fully explain:

- where authoritative state lives
- how retries, sync loops, or bridge protocols work internally
- which invariants keep different runtime layers aligned
- how extension mechanisms affect behavior under the surface

So the next phase should move from interface shape to runtime mechanics.

---

# 6. Suggested next runtime targets in priority order

## 6.1 Workspace event sync and SSE bridge

Why next:

- it directly extends the workspace and event-scope articles
- it explains how workspace-local streams become global observations

## 6.2 Event bus definitions and payload taxonomy

Why next:

- the whole server is event-driven
- route and client understanding now depends on event semantics and stability

## 6.3 MCP runtime internals

Why next:

- the route layer is documented
- deeper runtime behavior still determines tools, prompts, auth, and failure handling

## 6.4 File boundary enforcement and permission interplay

Why next:

- file routes are documented
- the relation between HTTP inspection surfaces and permissioned tool/file access is still only partially covered

## 6.5 TUI event taxonomy and command execution model

Why next:

- the TUI route and queue surfaces are covered
- deeper command/event meaning is still an open layer

---

# 7. What no longer needs urgent attention

At this point, the basic route-surface inventory itself no longer needs urgent expansion.

The only likely future additions in that area would be:

- updates if the codebase adds new route files
- revisions if experimental routes graduate or change materially
- corrections if route behavior diverges from what is currently documented

So the best return now comes from deeper runtime modules, not more route enumeration.

---

# 8. How to use this index

A useful reading sequence now is:

1. start with `acp_and_server_interfaces.md`
2. read the specific route/control-plane article for the subsystem of interest
3. follow into the corresponding deeper runtime article already available
4. use this index to identify which underlying runtime layer still lacks direct coverage

This makes the documentation set more navigable as it grows.

---

# 9. Open questions for further investigation

- **Question 1**: Which of the next runtime targets will give the highest leverage for understanding multiple already-documented route surfaces at once?
- **Question 2**: Should a future pass create a thematic index by subsystem instead of by documentation sequence?
- **Question 3**: Which of the current articles should eventually be linked together more explicitly with a shared README or navigation map?
- **Question 4**: Are there any route/control-plane articles from this pass that should be revisited after the deeper runtime modules are documented?

---

# 10. Summary

The current documentation pass has now covered the major OpenCode server route and control-plane surfaces in detail.

The next highest-value work is no longer route enumeration.

It is the deeper runtime logic behind those routes, especially workspace sync, event taxonomy, MCP internals, file boundary enforcement, and TUI command/event semantics.
