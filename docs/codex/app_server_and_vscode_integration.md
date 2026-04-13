# App-Server and VS Code Integration in Codex

## Scope

This document explains how Codex exposes its core runtime to rich clients such as the VS Code extension through the app-server, why the transport is JSON-RPC instead of a bespoke frontend binding, and how the thread/turn/item model lets IDEs remain comparatively thin while the core agent logic stays centralized.

The main references are:

- `codex-rs/app-server/README.md`
- `codex-rs/app-server/src/main.rs`
- `codex-rs/app-server-protocol/src/protocol/v2.rs`

This subsystem matters because it shows that the UI layer is not where Codex logic lives. The heavy lifting stays in core runtime services, while the app-server provides a stable, typed integration boundary.

---

## 1. The key idea: the IDE is a client, not the runtime

The app-server README states it directly:

- `codex app-server` is the interface used to power rich interfaces such as the VS Code extension

That sentence captures the core architectural point.

The VS Code extension is not the primary implementation of Codex behavior. It is a consumer of a backend agent runtime.

### Why this matters

This means:

- model execution logic is not duplicated inside the extension
- session, turn, tool, and diff semantics remain centralized
- multiple frontends can share the same core behavior
- protocol evolution can happen at one server boundary instead of in every UI implementation

This is a much more scalable architecture than embedding core agent logic directly in the IDE layer.

---

## 2. The app-server is a transport and protocol boundary over the core runtime

The server exposes Codex over JSON-RPC 2.0-like messages.

Supported transports include:

- stdio / JSONL
- websocket text frames, marked experimental

### Why JSON-RPC is a good fit

The app-server needs to support:

- request/response methods
- long-lived notifications
- connection-scoped initialization
- streaming turn progress
- structured errors

JSON-RPC is a natural fit for that blend.

It is simpler than forcing everything through a custom binary protocol and more structured than raw ad hoc events.

---

## 3. The app-server is intentionally not a monolithic web backend

The supported transports are notably lightweight:

- `stdio://` by default
- optional `ws://IP:PORT`

### Why this is revealing

This is not a design centered on a heavy multi-tenant HTTP application server.

Instead, it is optimized for local or tightly-coupled client integration, especially:

- an IDE launching or connecting to a colocated backend process
- a local client consuming structured notifications over a straightforward stream

That makes sense for a coding assistant primarily embedded into developer tools.

---

## 4. Initialization is mandatory and connection-scoped

The lifecycle begins with:

- `initialize`
- then `initialized`

and other requests are rejected until the handshake is complete.

### Why this handshake exists

The server needs to know:

- who the client is
- what capabilities or experimental options it requests
- which notifications it may opt out of

This creates a clean connection contract before any thread or turn work begins.

### Why this is a strong design choice

It prevents clients from being ambiguous or partially configured mid-session.

A connection either has a known identity and capability set, or it is not ready.

That improves correctness and observability.

---

## 5. Per-connection capability negotiation keeps the protocol evolvable

The `initialize` flow supports capabilities such as:

- experimental API opt-in
- exact-method notification opt-out

### Why this matters

Not every client wants every event stream.

For example:

- a full IDE might want rich item delta notifications
- a thinner client might want only higher-level completion events

By making this connection-scoped and explicit, Codex keeps the protocol flexible without fragmenting the server into multiple special-purpose variants.

---

## 6. The core interaction model is `Thread -> Turn -> Item`

The app-server documentation defines three top-level primitives:

- `Thread`
- `Turn`
- `Item`

### Thread

A conversation container.

### Turn

One user-visible step of the conversation, typically from user input through agent completion.

### Item

A granular unit of input or output within a turn, including messages, reasoning, tool work, file edits, and related artifacts.

### Why this abstraction is excellent

This hierarchy matches both:

- how agent runtimes actually work
- how rich clients want to render interactive progress

It is much more expressive than a simple “conversation with messages” model.

---

## 7. Why `Item` exists as a separate first-class primitive

A basic chat protocol could have stopped at:

- conversation
- message

Codex does not.

It introduces `Item` because a coding agent turn contains much more than final assistant prose:

- reasoning
- tool calls
- shell execution progress
- patch application
- plan updates
- approvals
- image generation
- review-mode transitions

### Why this matters for IDEs

An IDE needs to display these activities as first-class progress and state, not as hidden implementation details.

The `Item` model is what makes that possible.

---

## 8. Lifecycle: start or resume thread, then start turn, then stream item notifications

The app-server lifecycle described in the README is:

1. initialize connection
2. `thread/start`, `thread/resume`, or `thread/fork`
3. `turn/start`
4. consume notifications such as:
   - `turn/started`
   - `item/started`
   - `item/completed`
   - delta notifications
5. receive `turn/completed`

### Why this matters

This lifecycle mirrors the core runtime architecture almost exactly.

That is a sign of a healthy integration design.

The app-server is not inventing a parallel conversational model for the UI. It is exposing the same fundamental model the runtime already uses.

---

## 9. The IDE remains thin because the app-server streams semantic events, not raw tokens only

After `turn/start`, the client receives structured notifications such as:

- item lifecycle events
- agent-message deltas
- tool progress
- reasoning-related events
- turn completion

### Why this is powerful

A thin client can build a rich interface as long as the backend streams semantically meaningful events.

The client does not need to:

- infer whether a command is running
- reconstruct whether a patch was applied
- parse tool calls out of raw text
- guess when a turn is actually complete

The app-server already translates internal runtime state into a client-consumable event model.

---

## 10. `thread/start`, `thread/resume`, and `thread/fork` expose runtime continuity explicitly

The API supports:

- starting fresh threads
- resuming stored threads
- forking threads into a new conversation lineage
- optional ephemeral in-memory threads

### Why this matters

This is much richer than a plain stateless chat endpoint.

It exposes a real conversation runtime with:

- persistence
- branching
- in-memory temporary sessions
- loaded versus not-loaded state

That is exactly what rich IDE integrations need for serious coding workflows.

---

## 11. Loaded-thread management is part of the contract

The API includes operations such as:

- `thread/loaded/list`
- `thread/unsubscribe`
- status change notifications
- thread closure behavior when the last subscriber disconnects

### Why this matters

The app-server is not simply a persistence API over stored rollouts. It also manages live in-memory sessions.

This distinction is important because:

- a thread can exist on disk but not be loaded
- a thread can be active, idle, or closed
- subscribers affect server-side lifecycle

This is a live runtime API, not just a data retrieval API.

---

## 12. The app-server protocol is intentionally typed and versioned

`app-server-protocol/src/protocol/v2.rs` contains a large translation layer from core types to wire types.

### Why this is important

The app-server does not dump internal Rust structs directly onto the wire.

Instead, it defines an explicit wire protocol with:

- versioned API surface
- naming conventions
- TypeScript export support
- JSON schema support
- translation from core enums and structs into wire-stable representations

This is how you keep a frontend/backend boundary maintainable over time.

---

## 13. The protocol layer is a compatibility and naming adapter

The `v2` protocol module translates core runtime types into wire-friendly forms such as:

- camelCase naming
- exported TypeScript definitions
- protocol-specific enum representations
- request/response/notification naming conventions

### Why this matters

Core runtime types are designed for internal Rust execution.

Wire types need different priorities:

- stable serialization
- client language friendliness
- explicit versioning
- looser coupling from internal implementation changes

The protocol layer absorbs that mismatch cleanly.

---

## 14. The app-server exposes much more than chat

The API overview includes not only thread and turn operations, but also things such as:

- command execution
- file system operations
- model listing
- collaboration mode listing
- skills listing and config writing
- plugin install/read/list operations
- MCP server auth and reload operations
- config read/write operations
- rollback and compaction triggers

### Why this matters

The app-server is the integration surface for the whole Codex environment, not just the chat bubble.

This allows the IDE to act as a control plane for:

- runtime configuration
- auxiliary tools
- live thread lifecycle
- ecosystem extensions

That is much more capable than a frontend that only displays completions.

---

## 15. Why VS Code integration can stay thin

Given the app-server API, a VS Code extension mostly needs to handle:

- transport setup
- initialization handshake
- thread and turn method calls
- rendering notifications into UI state
- approval and user-input UX
- optional config and skills panels

### What it does not need to own

It does not need to reimplement:

- session loop logic
- tool dispatch
- prompt assembly
- diff tracking
- compaction
- provider streaming normalization
- skill loading
- AGENTS.md layering

That division of labor is the key advantage of the architecture.

---

## 16. Notifications are the UI state machine backbone

The README makes it clear that progress is streamed through notifications like:

- `thread/started`
- `turn/started`
- `item/started`
- `item/completed`
- item delta notifications
- `turn/completed`

### Why this is important

A rich client needs a state machine, but it does not need to invent one from scratch if the backend already provides the right event vocabulary.

These notifications are effectively the frontend state machine protocol.

They tell the client:

- what just began
- what is streaming
- what completed
- what the final turn status is

That is exactly what an interactive IDE panel needs.

---

## 17. Backpressure and overload handling are treated as protocol concerns

The README documents bounded queues and an overload JSON-RPC error code `-32001` with retry guidance.

### Why this matters

The app-server is not pretending to be infinitely elastic.

Instead, it acknowledges that:

- transport ingress can saturate
- clients must retry responsibly
- overload handling must be part of the protocol contract

This is a sign that the server is designed as a real runtime service rather than a demo bridge.

---

## 18. Transport remains local-friendly, but the protocol is still disciplined

Even though stdio is the default transport, the app-server still provides:

- formal initialization
- typed request/response/notification schemas
- versioned API layering
- error contracts

### Why this is a good combination

Local transports often tempt teams to skip protocol discipline and rely on informal conventions.

Codex does not do that.

It keeps the transport simple while keeping the protocol rigorous.

That is the right combination for IDE integration.

---

## 19. The server entry point is intentionally small

`app-server/src/main.rs` is very thin. It mainly:

- parses the listen transport
- constructs loader overrides
- forwards control into `run_main_with_transport(...)`

### Why this matters

The entry point is not where app-server behavior is implemented.

This mirrors the architecture elsewhere in Codex:

- small entry points
- heavy logic in deeper services

This keeps process startup code simple and reduces coupling.

---

## 20. App-server v2 is where active API evolution is meant to happen

The protocol guidance embedded in the repository emphasizes that active API development belongs in v2.

### Why this matters

This shows that the team is treating the app-server boundary as a long-lived public integration surface.

Versioning matters because:

- external clients may lag server updates
- TypeScript code generation depends on stable shapes
- frontend integrations need migration paths

This is a key sign of architectural maturity.

---

## 21. Why thread/turn/item mirrors the internal runtime so well

The app-server’s public primitives match the internal core concepts closely:

- long-lived session-like thread
- per-user-action turn
- fine-grained item lifecycle

### Why this is beneficial

When an external protocol mirrors the internal runtime model closely:

- translation is simpler
- bugs caused by conceptual mismatch are reduced
- documentation becomes clearer
- frontends get better fidelity into real runtime state

Codex benefits from exactly this alignment.

---

## 22. The hidden algorithm of the integration layer

A good summary of the app-server design is:

```text
1. accept an initialized client connection
2. create or load a thread-bound runtime session
3. accept turn requests and forward them into core session/turn machinery
4. observe internal runtime progress as item and turn lifecycle events
5. translate those events into protocol notifications
6. allow the client to steer, interrupt, inspect, configure, and persist the runtime
```

This is not “the UI talks directly to the LLM.”

It is “the UI talks to a runtime service that owns the agent.”

---

## 23. What can go wrong if this subsystem is changed carelessly

### Risk 1: moving core logic into the frontend layer

That would fragment behavior across clients and make consistency much harder.

### Risk 2: letting wire types track internal structs too closely

That would make the protocol brittle whenever internal implementation details change.

### Risk 3: weakening initialization and capability negotiation

Clients would become harder to reason about and protocol evolution would get riskier.

### Risk 4: collapsing rich item lifecycle events into generic message streaming

The IDE would lose much of the semantic structure that makes Codex usable for coding tasks.

### Risk 5: ignoring thread loaded/unloaded lifecycle distinctions

Clients could become confused about whether they are interacting with persisted history or a live runtime.

---

## 24. How to extend this subsystem safely

If you add new IDE-facing features through the app-server, the safest approach is usually:

1. keep the new behavior grounded in existing internal runtime concepts
2. add explicit v2 wire types instead of leaking internal Rust shapes
3. decide whether it is a request/response method, a notification, or both
4. preserve connection initialization and capability gating semantics
5. make sure the client can render the new behavior through item or thread lifecycle concepts where appropriate

### Questions to ask first

- Is this feature a thread-level operation, a turn-level operation, or an item-level event?
- Should it be an explicit RPC method, a notification, or a follow-up to an existing call?
- Does the client need full-fidelity streaming updates or only terminal state?
- Should this feature exist for all clients, or only behind experimental capability opt-in?
- Can the frontend remain thin, or are we accidentally pushing core agent logic outward?

Those questions align with the existing architecture.

---

## 25. Condensed mental model

Use this model when reading the system:

```text
core runtime
  = owns session, turn, tool, prompt, diff, and model logic

app-server
  = typed JSON-RPC integration boundary exposing that runtime

VS Code / rich client
  = transport client + renderer + user interaction layer
```

The most important takeaway is this:

- Codex keeps the agent brain in the backend and uses the app-server as the stable contract that rich clients consume

That is the defining property of the integration architecture.

---

## Next questions to investigate

- Which internal app-server components translate core `EventMsg` values into v2 `item/*` notifications, and how lossy or lossless is that mapping?
- How does subscriber management interact with long-lived background tasks, review threads, and subagent threads?
- Which parts of the API are intentionally v2-only because v1 could not represent the needed lifecycle richness?
- How does the VS Code client handle opt-out notifications versus full-fidelity item streaming in practice?
- What are the most important differences between app-server thread persistence and the CLI/TUI’s direct in-process session experience?
