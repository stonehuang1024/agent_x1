# File Routes / Filesystem Surface

---

# 1. Module Purpose

This document explains the filesystem-oriented HTTP route surface in OpenCode, covering text search, file discovery, directory listing, file reading, and file status inspection.

The key questions are:

- Why does OpenCode expose a dedicated filesystem route surface at all?
- How do the file routes relate to higher-level tools like grep, edit, bash, and LSP?
- What is the difference between text search, file-name search, symbol search, file listing, file reading, and file status?
- Why are these routes rooted in the active instance directory?
- What does this route surface reveal about OpenCode’s control-plane relationship to the local codebase?

Primary source files:

- `packages/opencode/src/server/routes/file.ts`
- `packages/opencode/src/file`
- `packages/opencode/src/file/ripgrep.ts`
- `packages/opencode/src/project/instance.ts`

This layer is OpenCode’s **filesystem discovery and content-inspection control-plane API**.

---

# 2. Why this route family matters

OpenCode is fundamentally a codebase-aware system.

So beyond sessions and model execution, clients also need structured access to the project filesystem for tasks like:

- searching code
- locating files
- listing directories
- reading file contents
- checking version-control status

That is why a dedicated filesystem route surface exists.

It gives clients a read-oriented control-plane view of the working tree.

---

# 3. Why these routes live under the root route namespace

In `server.ts`, `FileRoutes()` is mounted at:

- `/`

So the effective endpoints are things like:

- `/find`
- `/find/file`
- `/find/symbol`
- `/file`
- `/file/content`
- `/file/status`

This placement is revealing.

These routes are not just one subsystem among many.

They are foundational enough to sit near the root of the server surface.

---

# 4. The route surface is read-oriented and discovery-oriented

`server/routes/file.ts` exposes:

- `GET /find`
- `GET /find/file`
- `GET /find/symbol`
- `GET /file`
- `GET /file/content`
- `GET /file/status`

There are no write routes here.

That is important.

This namespace is about discovering and inspecting the codebase, not mutating it.

Mutation is handled elsewhere through tools and permission-aware execution flows.

---

# 5. Why the file route surface is separate from edit or bash tools

A client may want passive codebase inspection without invoking:

- model-driven tools
- edit permissions
- shell execution

The file route surface provides that cleanly.

It is a direct control-plane API for filesystem visibility rather than an agent-action surface.

That separation is good architecture.

---

# 6. `GET /find`: text search with ripgrep

The text-search route validates:

- `pattern`

then calls:

- `Ripgrep.search({ cwd: Instance.directory, pattern, limit: 10 })`

This is the main content-search endpoint.

It provides project-local grep-like search as an HTTP operation.

---

# 7. Why text search is rooted in `Instance.directory`

The search explicitly uses:

- `cwd: Instance.directory`

This is important because it means search scope is request-context-bound, not global across every known project.

The route behaves relative to the currently bound instance and directory.

That keeps filesystem search aligned with the same scoping model used throughout the rest of the server.

---

# 8. Why the text-search result shape matters

The route exposes:

- `Ripgrep.Match.shape.data.array()`

rather than a raw ripgrep CLI output stream.

That means OpenCode is not exposing shell text for clients to parse.

It is exposing a structured search result contract.

That is much better for:

- editors
- IDE integrations
- programmatic tooling
- UIs showing search hits

---

# 9. Why the route limit is fixed at 10

The route hardcodes:

- `limit: 10`

This is a notable design choice.

It suggests the endpoint is optimized for quick interactive discovery rather than exhaustive grep export.

That fits many UI workflows, though it also constrains heavy operator use.

It is an important contract detail for clients.

---

# 10. `GET /find/file`: file and directory name search

This route validates:

- `query`
- optional `dirs`
- optional `type`
- optional `limit`

then delegates to:

- `File.search(...)`

This is different from text search.

It is a path/name discovery route rather than a content search route.

---

# 11. Why file-name search is a separate API

Searching for a symbol or string inside files is a different user intent from asking:

- where is a file named X?
- what directories match Y?

Separating `find` and `find/file` keeps those intents explicit and keeps the underlying implementation free to optimize differently.

That is a good API choice.

---

# 12. The `dirs` and `type` controls are practical discovery features

`GET /find/file` lets clients influence whether search should include directories and whether results should be limited to:

- files
- directories

This is useful for common UI patterns like:

- open-file pickers
- directory choosers
- project explorers
- quick navigation palettes

The route is therefore shaped for practical editor-style workflows.

---

# 13. `GET /find/symbol`: intended LSP-backed symbol search

The symbol route is documented as:

- searching workspace symbols using LSP

and its response schema is:

- `LSP.Symbol.array()`

But the handler currently returns:

- `[]`

with the intended LSP call commented out.

This is a very important implementation detail.

---

# 14. Why the symbol route matters even in its current state

The existence of the route shows the intended architecture:

- OpenCode wants a language-aware symbol search API, not only text and filename search

But the current implementation is effectively disabled or unfinished.

That is important to document honestly because clients should not assume symbol search is actually active despite the route contract suggesting it.

---

# 15. This route is a good example of contract versus implementation maturity

The route surface already declares:

- a symbol-search capability
- response typing
- operation identity

But the runtime path returns an empty array.

That suggests the contract has been sketched ahead of full implementation.

This is a useful example of why source-grounded documentation matters.

---

# 16. `GET /file`: directory listing

The list route validates:

- `path`

then calls:

- `File.list(path)`

and returns:

- `File.Node.array()`

This gives clients a structured filesystem tree view for a requested path.

It is a basic but foundational file-browsing API.

---

# 17. Why directory listing belongs in the control plane

Many OpenCode clients need to browse the project tree to:

- navigate files
- choose targets for operations
- render project explorers
- understand code layout

Providing a typed route for this is much better than making every client shell out or reconstruct filesystem access itself.

---

# 18. `GET /file/content`: file reading as an explicit route

The read route validates:

- `path`

then calls:

- `File.read(path)`

and returns:

- `File.Content`

This is the core content-inspection endpoint.

It allows clients to fetch actual file contents through the server’s scoped filesystem API.

---

# 19. Why file reading is separate from listing

Listing answers:

- what nodes are here?

Reading answers:

- what is in this file?

These are distinct resource operations, and separating them keeps each route precise.

It also allows `File.Content` to evolve independently from tree/listing shapes.

---

# 20. `GET /file/status`: version-control-oriented file inspection

The status route delegates to:

- `File.status()`

and returns:

- `File.Info.array()`

The route description says this returns git status for all files in the project.

So this is not just generic filesystem metadata.

It is a VCS-aware inspection surface.

---

# 21. Why git status belongs near the filesystem API

File status sits naturally between:

- raw filesystem browsing
- higher-level project/VCS state

A client often needs to know not only that a file exists, but whether it is:

- modified
- added
- deleted
- otherwise changed relative to git state

This route provides that file-centric change view.

---

# 22. The file routes are intentionally thin over deeper subsystems

The route file mostly delegates to:

- `Ripgrep.search(...)`
- `File.search(...)`
- `File.list(...)`
- `File.read(...)`
- `File.status()`

That is good structure.

The transport layer stays small, while the deeper filesystem logic remains centralized.

---

# 23. Relationship to permission-aware tool execution

Elsewhere in the codebase, tools like:

- grep
- ls
- bash
- edit

interact with permission systems and external-directory checks.

The route surface here is different.

It exposes read-oriented filesystem control-plane operations directly for the bound instance context.

That difference is important:

- tool APIs are agent execution primitives
- file routes are client inspection primitives

---

# 24. Why instance scoping still matters for filesystem routes

Even though these are simple-looking file operations, instance scoping matters because it determines:

- the root directory for search
- the project whose git status is relevant
- the working tree the client is allowed to inspect through this server context

So these routes are not generic machine-wide filesystem APIs.

They are scoped codebase-inspection APIs.

---

# 25. What this route family reveals about OpenCode’s architecture

OpenCode does not treat codebase access as something only the model or internal tools should have.

It also exposes a direct typed client-facing surface for:

- code search
- file discovery
- file tree browsing
- file content reading
- file status inspection

That is a sign of a serious developer platform API, not just a chat backend.

---

# 26. A representative filesystem API workflow

A typical client workflow could look like this:

## 26.1 Search project text

- `GET /find?pattern=...`

## 26.2 Search for a file or directory by name

- `GET /find/file?query=...`

## 26.3 Browse a directory tree

- `GET /file?path=...`

## 26.4 Read a specific file

- `GET /file/content?path=...`

## 26.5 Inspect changed files

- `GET /file/status`

This is a coherent codebase-inspection control-plane workflow.

---

# 27. Key design principles behind this module

## 27.1 Codebase inspection should be directly available through typed HTTP routes

So OpenCode exposes search, list, read, and status operations as first-class API calls.

## 27.2 Filesystem APIs should be scoped to the active instance context

So search roots and file operations are tied to `Instance.directory` and project-local state.

## 27.3 Content search, path search, symbol search, listing, reading, and status are distinct operations

So the route surface models them separately instead of collapsing them into one vague file endpoint.

## 27.4 Transport routes should expose structured results, not raw shell output

So search and file operations return typed schemas rather than CLI text.

---

# 28. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/routes/file.ts`
2. `packages/opencode/src/file`
3. `packages/opencode/src/file/ripgrep.ts`
4. `packages/opencode/src/project/instance.ts`
5. `packages/opencode/src/lsp`

Focus on these functions and concepts:

- `Ripgrep.search()`
- `File.search()`
- `File.list()`
- `File.read()`
- `File.status()`
- `LSP.Symbol`
- instance-scoped search root behavior

---

# 29. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Why is `GET /find/symbol` currently stubbed to return an empty array, and what is blocking real LSP-backed symbol search?
- **Question 2**: Should `GET /find` expose configurable limits or paging for heavier search clients?
- **Question 3**: How do `File.list()` and `File.read()` enforce path-safety and boundaries relative to the active instance worktree?
- **Question 4**: Should the filesystem route surface expose richer metadata like file size, timestamps, or ignore status?
- **Question 5**: How does `File.status()` behave in non-git projects or in unusual worktree/sandbox configurations?
- **Question 6**: Should there be explicit write/update routes here, or is keeping mutation entirely in tool flows the right long-term separation?
- **Question 7**: How do these filesystem routes interact with remote workspace forwarding when the authoritative workspace is remote?
- **Question 8**: Are there opportunities to unify file search semantics between the HTTP route surface and tool permission flows without mixing their responsibilities?

---

# 30. Summary

The `file_routes_and_filesystem_surface` layer gives OpenCode clients a typed, instance-scoped way to inspect the active codebase:

- it supports text search, path search, directory listing, file reading, and VCS-style file status inspection
- it separates these concerns into distinct route operations with structured response schemas
- it keeps filesystem inspection distinct from permissioned tool execution flows
- it also reveals an intended but not yet active symbol-search surface via LSP

So this module is not just convenience file browsing. It is the direct control-plane API that lets OpenCode clients inspect the codebase they are operating on.
