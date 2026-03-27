# CLI, TUI, and Frontend Surfaces in Codex

## Scope

This document explains how Codex exposes its capabilities through multiple user-facing surfaces, especially the CLI and TUI, how those surfaces route into shared core runtime logic, and why the project keeps frontends comparatively thin instead of duplicating agent behavior in each entry point.

The main references are:

- `codex-rs/cli/src/main.rs`
- the presence of `codex-rs/tui/`
- `codex-rs/app-server/` as the rich-client transport surface

This document also discusses the idea of SDK/front-end surfaces conceptually, but it does **not** assume a standalone `codex-rs/sdk/README.md` exists, because that file is not present in the repository.

---

## 1. The key idea: Codex has multiple surfaces, but one core runtime

Codex is not a single-interface tool.

From the repository structure and CLI entrypoint, it clearly supports multiple front-facing surfaces such as:

- interactive terminal usage through the TUI
- non-interactive execution through `exec`-style subcommands
- app-server-backed rich clients such as VS Code
- specialized operational tools like review, resume, fork, cloud tasks, MCP, and sandbox utilities

### Why this matters

A weaker architecture would implement similar logic separately in each surface.

Codex instead appears to treat these as multiple entry surfaces into a shared runtime ecosystem.

That is the right design for consistency, maintainability, and feature reuse.

---

## 2. `cli/src/main.rs` is a routing layer, not the product logic center

The CLI entrypoint imports many crates and subcommands, but its role is fundamentally orchestration and dispatch.

It wires together surfaces such as:

- interactive TUI flow
- `exec`
- `review`
- login/logout
- MCP management
- app-server startup
- shell completion
- sandbox helpers
- apply / resume / fork
- cloud tasks
- feature inspection

### Why this matters

The CLI entrypoint is intentionally wide but not semantically deep.

It exposes the product surface area, but it does not own the core session, prompt, tool, diff, or stream-processing logic.

This is exactly the right separation for a multitool executable.

---

## 3. The CLI is effectively a multiplexer over specialized runtimes

The `MultitoolCli` structure demonstrates that the top-level `codex` command is a multiplexer.

If no subcommand is given, it routes into the interactive experience.

If a subcommand is given, it routes into specialized workflows.

### Why this is a strong design

Users get one stable command surface, but the codebase still keeps specialized flows decoupled underneath.

This lets Codex support:

- interactive conversational work
- batch or scripted execution
- operational utilities
- debugging tools

without collapsing everything into one mode.

---

## 4. The TUI is the default interactive surface

The CLI structure shows that when no subcommand is provided, options are forwarded into the interactive TUI flow through `codex_tui::Cli`.

### Why this matters

This means the TUI is not an afterthought. It is the default primary terminal experience.

That is important because it suggests:

- the interactive state model is a first-class product surface
- streaming item rendering matters deeply in terminal workflows
- terminal interactivity is not reduced to plain stdout text

This aligns with the rest of the architecture, where the runtime emits structured turn and item events rather than only final answers.

---

## 5. The TUI is a renderer over runtime events, not a separate agent implementation

Even without reopening the full TUI code here, the broader architecture strongly suggests that the TUI consumes the same kinds of runtime structures used elsewhere:

- turn state
- item lifecycle events
- streaming deltas
- approval flows
- diff summaries

### Why this is important

The TUI should not invent its own different agent semantics.

A healthy architecture lets the TUI focus on:

- rendering
- interaction affordances
- input composition
- incremental progress display

while the underlying runtime stays in core.

That is consistent with the rest of Codex’s design philosophy.

---

## 6. `exec` and `review` are alternate operational surfaces over shared internals

The CLI includes subcommands such as:

- `Exec`
- `Review`

These indicate that not all Codex usage is interactive chat.

### `Exec`

This is the non-interactive or batch-style surface.

### `Review`

This is a specialized workflow where the same runtime concepts are likely steered toward a review-oriented collaboration mode or task framing.

### Why this matters

This supports a broader point:

- the Codex runtime is not tied to one UX pattern

Instead, multiple surfaces shape the same core capabilities for different user needs.

---

## 7. `Resume` and `Fork` reveal that the CLI exposes session continuity directly

The CLI includes explicit session operations:

- `Resume`
- `Fork`

### Why this matters

These are not low-level implementation details. They are product-level features exposed directly in the terminal experience.

That tells us the CLI/TUI surface is deeply aware of:

- stored sessions
- branching conversation state
- continuity of agent context

This is more sophisticated than a one-shot terminal chatbot command.

---

## 8. `Apply` exposes a post-processing bridge from agent edits to user Git workflows

The CLI includes an `Apply` command that applies the latest diff produced by the Codex agent as `git apply` to the local working tree.

### Why this is important

This shows that Codex’s surfaces are not only about live interaction. They also expose workflow bridges between:

- agent-generated change artifacts
- the user’s existing source-control workflow

That makes the CLI useful as both an interactive shell and an operational integration point.

---

## 9. The CLI also exposes system-level operational tooling

The top-level command includes tools for:

- sandbox execution
- login/logout
- MCP management
- app-server startup
- debug utilities
- feature inspection

### Why this matters

Codex is not just an assistant prompt shell. It is an ecosystem with:

- security controls
- auth state
- extension points
- server processes
- experimental features

The CLI functions as the administrative control plane for that ecosystem.

---

## 10. Why frontends stay thin in this architecture

The same pattern appears across TUI, CLI, and app-server clients:

- thin entrypoint
- specialized rendering or interaction layer
- shared core runtime below

### Why this is the right tradeoff

If each surface reimplemented:

- prompt assembly
- streaming normalization
- tool orchestration
- diff tracking
- compaction
- AGENTS.md and skill injection

the codebase would quickly drift into multiple inconsistent agent implementations.

Codex avoids that by centralizing runtime logic and keeping surfaces focused on UX and routing.

---

## 11. App-server belongs in the same family of frontend surfaces

Although the app-server is not a terminal surface, it fits the same architectural category:

- a frontend access surface over the core runtime

### Why this grouping is useful

It helps explain the project cleanly:

- CLI / TUI are local terminal surfaces
- app-server is the rich-client surface
- all of them route into the same deep runtime model

This is exactly why the system can support both terminal and IDE experiences without forking the agent brain.

---

## 12. There is a difference between “surface” and “runtime” in Codex

A useful distinction is:

### Surface

A user-facing or client-facing interface that:

- accepts input
- renders state
- initiates operations
- exposes certain workflows

Examples:

- CLI
- TUI
- app-server clients

### Runtime

The backend logic that:

- owns sessions and turns
- constructs prompts
- normalizes model streams
- dispatches tools
- tracks diffs
- manages context

### Why this distinction matters

Much of Codex’s architectural cleanliness comes from not confusing these two layers.

---

## 13. The CLI exposes both interactive and utility-like subprograms under one binary

The presence of subcommands like:

- `Completion`
- `Sandbox`
- `Execpolicy`
- `Debug`
- `ResponsesApiProxy`
- `StdioToUds`

shows that the `codex` binary is a host for many related utilities.

### Why this is valuable

A shared binary gives users one discoverable operational surface, while internal crates can still remain specialized.

This is a common and effective design pattern in advanced developer tooling.

---

## 14. Arg0 dispatch hints at packaging flexibility

The CLI and app-server entrypoints both use `arg0_dispatch_or_else(...)`.

### Why this matters

This suggests the project can adapt behavior depending on the executable invocation path or packaging form.

That is useful for:

- platform-specific packaging
- alternate launch names
- shipping related behaviors under one installed tool family

This is part of why the top-level entrypoints remain thin and composable.

---

## 15. Frontend surfaces differ in UX, not in core semantics

A terminal TUI and a VS Code extension feel very different to the user.

But under the architecture Codex is building, they should still share the same underlying semantics for:

- thread lifecycle
- turn lifecycle
- item progression
- tool execution
- approval handling
- diff visibility
- context continuity

### Why this matters

This consistency is exactly what lets users trust that Codex behaves the same way across environments.

The UI should change; the agent semantics should not drift casually.

---

## 16. A true standalone SDK surface is not clearly documented here

There may be SDK-related crates or embedding paths elsewhere in the repository, but based on the currently examined files there is no standalone `codex-rs/sdk/README.md` to ground a deep SDK-specific analysis.

### Why I am being explicit about this

It would be easy to invent a clean SDK story that the repository may not actually document in the expected place.

The evidence we do have strongly supports a broader point instead:

- Codex’s surfaces are designed around shared protocol and runtime boundaries, especially via core crates and the app-server protocol

That is the safe, evidence-based conclusion.

---

## 17. What the frontend surfaces likely share operationally

Even without reading every frontend implementation file, the architecture strongly suggests that all surfaces must share infrastructure around:

- config overrides
- feature toggles
- session lookup and persistence
- model and sandbox settings
- auth state
- rollout/session storage

The CLI entrypoint clearly wires config and feature controls near the top.

### Why this matters

This indicates the surfaces are not isolated apps. They are clients of a shared Codex environment.

That environment-level consistency is one of the reasons the project can support many workflows coherently.

---

## 18. The hidden algorithm of the frontend-surface layer

A good summary of the architectural pattern is:

```text
1. parse surface-specific inputs and flags
2. determine which operational mode or subprogram the user wants
3. construct the appropriate frontend/runtime bridge
4. reuse shared core services for agent behavior
5. render or expose results according to the chosen surface
```

This is not “many agents with different UIs.”

It is “one agent runtime with many entry surfaces.”

---

## 19. Why this design is strong

This multi-surface design gives Codex several important advantages:

- consistent behavior across terminal and IDE experiences
- less duplication of core logic
- easier protocol and runtime evolution
- specialized workflows without fragmenting the codebase
- clearer boundaries between UX and execution semantics

That is exactly what a mature coding-agent platform should aim for.

---

## 20. What can go wrong if this layer is changed carelessly

### Risk 1: pushing runtime logic into the CLI or TUI layer

This would create surface-specific behavior drift.

### Risk 2: treating the CLI entrypoint as the right place for deep orchestration

That would bloat the entry surface and make changes harder to reason about.

### Risk 3: allowing different surfaces to interpret turn/item semantics differently

Users would experience inconsistent behavior across interfaces.

### Risk 4: inventing untyped ad hoc client behavior instead of reusing protocolized runtime concepts

That would reduce reliability and make the system harder to evolve.

### Risk 5: assuming undocumented SDK contracts exist without verifying them in the repository

That would produce misleading documentation and architectural confusion.

---

## 21. How to extend this subsystem safely

If you add a new surface or significantly change an existing one, the safe pattern is usually:

1. keep the new surface thin
2. map its UX concepts onto existing thread/turn/item/runtime concepts
3. avoid reimplementing prompt, tool, or context logic in the surface layer
4. reuse existing protocol or core abstractions where possible
5. document clearly what is surface-specific and what is core-runtime behavior

### Questions to ask first

- Is this truly a new user-facing surface, or just a new mode of an existing surface?
- Can this surface use app-server or core runtime abstractions rather than inventing new ones?
- What parts of the behavior are UX-only, and what parts would wrongly duplicate agent semantics?
- How will session continuity, approvals, and item streaming be exposed here?
- Is there enough repository evidence to document a new SDK or protocol boundary accurately?

Those questions fit the current architecture well.

---

## 22. Condensed mental model

Use this model when reading the repository:

```text
CLI
  = top-level operational multiplexer

TUI
  = default interactive terminal renderer

app-server
  = rich-client integration surface

core runtime
  = shared agent engine underneath all of them
```

The most important takeaway is this:

- Codex supports multiple user-facing surfaces, but it is architected to keep the agent runtime centralized and the surfaces comparatively thin

That is the defining property of this layer.

---

## Next questions to investigate

- Which TUI modules are the main consumers of item lifecycle events and diff summaries during an interactive turn?
- How do `exec` and `review` differ internally from the default TUI path in terms of runtime setup and output rendering?
- Is there a more formal SDK or embedding surface elsewhere in the repository that deserves its own dedicated documentation file once located precisely?
- How does the CLI select between direct in-process runtime execution and app-server-mediated execution for certain workflows?
- Which surface-specific behaviors are intentionally allowed to differ, and which are required to remain identical across CLI, TUI, and app-server clients?
