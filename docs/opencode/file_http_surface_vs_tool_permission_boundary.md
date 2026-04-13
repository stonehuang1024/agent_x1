# File HTTP Surface / Tool Permission Boundary

---

# 1. Module Purpose

This document explains the boundary between OpenCode’s read-oriented HTTP file surface and its permission-enforced file/tool access model.

The key questions are:

- Why does OpenCode expose file inspection through HTTP routes while also having permission-aware file tools?
- What responsibilities belong to `server/routes/file.ts` versus `tool/read.ts`, `tool/ls.ts`, and external-directory checks?
- How does instance scoping differ from explicit permission gating?
- Where are path boundaries enforced, and where are user approvals enforced?
- What does this split reveal about OpenCode’s distinction between client control-plane access and agent/tool execution authority?

Primary source files:

- `packages/opencode/src/server/routes/file.ts`
- `packages/opencode/src/file/index.ts`
- `packages/opencode/src/tool/read.ts`
- `packages/opencode/src/tool/ls.ts`
- `packages/opencode/src/tool/external-directory.ts`
- `packages/opencode/src/project/instance.ts`

This layer is OpenCode’s **read-oriented file control plane versus permissioned tool-access boundary**.

---

# 2. Why this distinction matters

At first glance, OpenCode seems to have overlapping capabilities:

- file routes can search, list, and read files
- tools can also list and read files

But these are not redundant by accident.

They serve different trust and execution models.

Understanding that split is essential to understanding how OpenCode thinks about:

- client inspection
- agent action
- permission prompts
- workspace/file boundaries

---

# 3. The file route surface is a control-plane API

`server/routes/file.ts` exposes read-oriented routes such as:

- `/find`
- `/find/file`
- `/file`
- `/file/content`
- `/file/status`

These routes delegate directly into the `File` and `Ripgrep` modules and return structured data.

They are designed for:

- clients
- IDEs
- dashboards
- structured codebase inspection

They are not framed as agent actions.

---

# 4. The tool layer is an agent-execution API

By contrast, tools like:

- `read`
- `list`
- `grep`
- `edit`
- `bash`

run inside an agent/tool execution model where the system can ask for user permission.

So even when a tool also reads or lists files, its semantics are different.

It is not just “another API endpoint.”

It is an action the agent is requesting authority to perform.

---

# 5. Why similar capabilities appear in both layers

The overlap exists because OpenCode has at least two distinct consumers:

## 5.1 External/control-plane clients

- want typed project inspection routes

## 5.2 Inference-time agents/tools

- need operational file access under a permission model

The same raw capability may exist in both places, but under different safety and authority assumptions.

That is good architecture, not duplication for its own sake.

---

# 6. `File` module: common lower-level filesystem logic

The `File` namespace in `file/index.ts` defines common schemas and behaviors such as:

- `File.Info`
- `File.Node`
- `File.Content`
- text/binary/image heuristics
- diff and patch-oriented content representation

This shows that the route layer and tool layer are not each reinventing filesystem logic independently.

There is shared lower-level file infrastructure.

---

# 7. Why shared lower-level file logic is important

A clean architecture wants:

- shared filesystem understanding
- separate transport/authority layers above it

That is what OpenCode is doing.

The `File` module centralizes file semantics, while the route layer and tool layer express different access patterns over that shared substrate.

---

# 8. The file route layer is mostly authority-light

The route handlers in `server/routes/file.ts` mostly do:

- validate input
- call `Ripgrep.search(...)` or `File.*`
- return JSON

They do not invoke explicit per-operation permission prompts.

That is a major contrast with the tool layer.

---

# 9. Why route access is not the same as unrestricted machine-wide access

Even though the file routes do not call permission prompts, they are still scoped by:

- instance binding
- current project/directory context

For example:

- `/find` uses `cwd: Instance.directory`

So the file route layer is not an unrestricted arbitrary-filesystem API.

It is a scoped control-plane view over the active codebase context.

That is an important distinction.

---

# 10. The tool layer explicitly asks for permission

`tool/read.ts` calls:

- `ctx.ask({ permission: "read", ... })`

`tool/ls.ts` calls:

- `ctx.ask({ permission: "list", ... })`

This is the clearest expression of the difference.

Tool-based file access is mediated as an approval-sensitive operation.

That is true even when the target path is inside the current instance context.

---

# 11. Why tools need explicit permission prompts

In the tool model, the issue is not only path scope.

It is also:

- agent authority
- user approval
- auditability of what the agent is asking to do

So even a read inside the current project can require a permission model, because the actor is an autonomous tool invocation rather than a trusted control-plane client request.

This is the key conceptual split.

---

# 12. External-directory enforcement is an additional safety layer

`assertExternalDirectory(...)` in `tool/external-directory.ts` checks:

- whether the target path is contained by `Instance.containsPath(target)`

If not, it asks for:

- `permission: "external_directory"`

This is a second safety layer beyond ordinary read/list permissions.

It specifically handles leaving the current project/worktree boundary.

---

# 13. Why `external_directory` is different from `read` or `list`

This is very important.

A tool might be generally allowed to read or list files, but accessing a path outside the current instance boundary is a different category of risk.

So OpenCode models it separately.

That is a principled permission design.

---

# 14. `Instance.containsPath(...)` is the critical path-boundary primitive

The external-directory check depends on `Instance.containsPath(...)`.

Earlier source review showed that this function checks whether a path is within:

- `Instance.directory`
- or `Instance.worktree`

with special handling for root worktrees.

This means boundary enforcement is anchored in the current instance/project binding model.

That ties file safety back to the same context model used across the rest of the system.

---

# 15. `read` tool behavior is richer than the HTTP read route

The `read` tool does much more than a basic file-content fetch.

It handles:

- missing-file suggestions
- directory reading with pagination-like offset/limit behavior
- image/PDF attachment projection
- binary-file rejection
- line truncation
- byte caps
- instruction prompt injection
- LSP warming
- file-time tracking

This is a major clue.

The tool is optimized for agentic consumption and conversational use, not just raw content delivery.

---

# 16. Why tool-read and HTTP file-read are different products

The HTTP route returns a structured `File.Content` object suitable for general clients.

The `read` tool returns formatted output tailored to agent workflow, including:

- annotated line numbers
- instructional reminders
- preview metadata
- attachments

These are different user experiences serving different consumers.

So keeping them separate is correct.

---

# 17. `list` tool behavior is also optimized for agent workflows

`tool/ls.ts`:

- resolves the search path relative to `Instance.directory`
- performs `external_directory` checks
- asks for `list` permission
- uses `Ripgrep.files(...)`
- renders a tree-like textual output with limits and metadata

That is not the same thing as returning `File.Node[]` from the HTTP route.

Again, one is agent-facing formatted output; the other is client-facing structured data.

---

# 18. Why the HTTP route surface can stay structured while tools stay conversational

This is one of the strongest design separations in the whole subsystem.

## 18.1 HTTP routes

- structured JSON
- stable-ish client contracts
- project-scoped inspection

## 18.2 Tools

- permission prompts
- richer conversational formatting
- agent-centric safety controls
- output shaped for model consumption

This is a coherent split of responsibilities.

---

# 19. Boundary enforcement is layered, not singular

It would be wrong to think OpenCode has only one file-safety mechanism.

Instead, several layers coexist:

- instance scoping limits route context
- `Instance.containsPath(...)` defines project/worktree boundaries
- `external_directory` permission protects boundary escapes
- `read` / `list` permission protects tool-level authority

This layered model is much more robust than relying on one check alone.

---

# 20. Why the route surface does not appear to use the same prompt-based permission flow

The file routes are part of the server control plane. They are not framed as autonomous agent behavior.

So the assumption appears to be:

- if a client is authorized to use the server control plane, instance scoping is the primary boundary

Whereas the tool layer assumes:

- the agent itself needs per-action permission to access the filesystem on the user’s behalf

This is the trust-boundary split in one sentence.

---

# 21. The `File` module itself also carries content classification policy

The `File` namespace contains significant logic for deciding:

- binary versus text
- image MIME handling
- patch/diff structures
- ignore awareness

This indicates the file layer is not just thin wrappers around `fs`.

It is the semantic policy layer for what “reading a file” means in OpenCode.

Both route and tool layers rely on that semantics, but they surface it differently.

---

# 22. Why this module matters architecturally

This subsystem reveals one of OpenCode’s most important architectural distinctions:

- codebase inspection through the control plane
- versus agent authority to operate on files through tools

Those two things overlap in capability but differ in:

- trust model
- output shape
- permission path
- interaction style

Recognizing that difference makes many other design choices in the codebase easier to understand.

---

# 23. A representative comparison

A useful side-by-side comparison:

## 23.1 HTTP client wants file content

- calls `/file/content`
- gets structured JSON from `File.read(...)`
- scoped by current instance context

## 23.2 Agent wants file content

- invokes `read` tool
- may trigger `external_directory` and `read` permission prompts
- gets agent-friendly formatted output plus reminders/attachments

Same raw domain, different authority and consumption model.

---

# 24. Another representative comparison

## 24.1 HTTP client wants a directory tree

- calls `/file`
- gets `File.Node[]`

## 24.2 Agent wants to inspect a directory

- invokes `list` tool
- may trigger path-boundary and list permissions
- gets tree-rendered textual output with truncation metadata

Again, this is an intentional product split.

---

# 25. Why this is not “multiple implementations” in the bad sense

The user rule against duplicate implementations is important.

This subsystem does **not** primarily look like accidental duplicate logic.

The underlying file semantics live in shared modules.

What differs is:

- transport
- permission path
- output shape
- actor trust model

So this is one of the cases where two surfaces are justified because they serve genuinely different responsibilities.

---

# 26. Key design principles behind this module

## 26.1 File inspection for clients and file access for agents are different authority models

So OpenCode keeps HTTP routes and tools separate even when capabilities overlap.

## 26.2 Instance context defines the default filesystem boundary for server-facing inspection

So routes operate relative to `Instance.directory` and project/worktree scope.

## 26.3 Agent actions need explicit permission and boundary checks beyond mere instance scoping

So tools use `ctx.ask(...)` and `assertExternalDirectory(...)`.

## 26.4 Shared lower-level file semantics should be centralized while presentation and safety differ by surface

So the `File` module provides shared semantics while route and tool layers present different contracts.

---

# 27. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/routes/file.ts`
2. `packages/opencode/src/file/index.ts`
3. `packages/opencode/src/tool/read.ts`
4. `packages/opencode/src/tool/ls.ts`
5. `packages/opencode/src/tool/external-directory.ts`
6. `packages/opencode/src/project/instance.ts`

Focus on these functions and concepts:

- `File.read()`
- `File.list()`
- `File.search()`
- `ReadTool.execute()`
- `ListTool.execute()`
- `assertExternalDirectory()`
- `Instance.containsPath()`
- tool `ctx.ask(...)` permissions

---

# 28. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Should any parts of the HTTP file surface also adopt stronger permission or auth boundaries in less-trusted deployments?
- **Question 2**: How closely do `File.read()` and `ReadTool.execute()` share implementation today, and is there any worthwhile refactoring without collapsing their distinct responsibilities?
- **Question 3**: Which path-safety checks are enforced inside the `File` module itself versus assumed by callers?
- **Question 4**: How should remote workspace forwarding affect the distinction between direct file routes and permissioned file tools?
- **Question 5**: Should agent-facing file tools expose more structured machine-readable output in addition to their current conversational formatting?
- **Question 6**: Are there cases where the same user action could be satisfied by either route or tool surface, and how should clients decide between them?
- **Question 7**: How do protected files or ignore rules interact differently across the route and tool layers?
- **Question 8**: Should the system surface clearer documentation or telemetry about when `external_directory` checks are triggered versus normal read/list permissions?

---

# 29. Summary

The `file_http_surface_vs_tool_permission_boundary` layer explains a crucial split in OpenCode’s filesystem architecture:

- the HTTP file routes provide structured, instance-scoped codebase inspection for clients
- the file tools provide agent-oriented access under explicit permission and boundary checks
- both layers rely on shared lower-level file semantics, but they differ in trust model, output format, and safety path
- `external_directory` checks and per-tool permissions make agent access stricter than plain control-plane inspection

So this subsystem is not accidental duplication. It is the explicit boundary between client inspection authority and agent-execution authority over the filesystem.
