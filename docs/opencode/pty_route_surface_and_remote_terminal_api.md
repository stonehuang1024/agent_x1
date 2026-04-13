# PTY Route Surface / Remote Terminal API

---

# 1. Module Purpose

This document explains the HTTP and WebSocket route surface for OpenCode’s pseudo-terminal subsystem.

The key questions are:

- Why does OpenCode expose PTY sessions as a first-class remote API?
- How do `server/routes/pty.ts` and `pty/index.ts` divide responsibilities?
- Why is PTY state modeled as a managed runtime resource instead of an ad hoc shell process?
- How does WebSocket connection, replay buffering, and cursor synchronization work?
- Why is the PTY API distinct from `SessionPrompt.shell()` and the model-driven `bash` tool?

Primary source files:

- `packages/opencode/src/server/routes/pty.ts`
- `packages/opencode/src/pty/index.ts`
- `packages/opencode/src/server/server.ts`

This layer is OpenCode’s **remote interactive terminal control surface**.

---

# 2. Why PTY gets its own route namespace

OpenCode treats PTY sessions as real runtime objects.

That is why there is a dedicated `/pty` namespace rather than burying terminal behavior inside generic shell endpoints.

A PTY session has its own lifecycle:

- create
- inspect
- update
- connect
- remove

This is materially different from:

- a one-shot shell command
- a model-invoked bash tool call

So giving PTY its own control-plane surface is the right design.

---

# 3. What the PTY route surface exposes

`server/routes/pty.ts` exposes:

- `GET /pty/` -> list PTY sessions
- `POST /pty/` -> create PTY session
- `GET /pty/:ptyID` -> inspect one PTY session
- `PUT /pty/:ptyID` -> update title or size
- `DELETE /pty/:ptyID` -> remove and terminate PTY
- `GET /pty/:ptyID/connect` -> WebSocket attach for live terminal interaction

This is a full resource lifecycle plus a live-transport attachment point.

That is a strong control-plane design.

---

# 4. PTY routes are a thin transport layer over `Pty`

The route file mostly delegates directly to `Pty` runtime functions:

- `Pty.list()`
- `Pty.create(...)`
- `Pty.get(...)`
- `Pty.update(...)`
- `Pty.remove(...)`
- `Pty.connect(...)`

This is exactly what you want.

The transport edge stays thin, and the real behavior lives in the runtime module.

---

# 5. `Pty.Info`: what a PTY session is remotely

A PTY session is represented by:

- `id`
- `title`
- `command`
- `args`
- `cwd`
- `status`
- `pid`

This is a pragmatic remote shape.

It includes enough to:

- display the terminal session in a UI
- identify what process was launched
- track whether it is still running
- correlate it with OS-level execution if needed

So PTY is modeled as a managed terminal session, not as raw stream bytes only.

---

# 6. PTY sessions are instance-scoped runtime state

`pty/index.ts` stores active sessions in:

- `Instance.state(() => new Map<PtyID, ActiveSession>())`

This matters because PTY sessions are local runtime objects tied to the active project instance.

That means the PTY API is not global across unrelated workspaces.

It participates in the same instance-scoping discipline as other OpenCode runtime subsystems.

---

# 7. Why PTY state needs instance scoping

A PTY session depends on local context such as:

- working directory
- shell environment
- project instance lifecycle
- plugin-provided shell environment

Without instance scoping, PTY sessions would become unsafe or ambiguous in a multi-workspace server process.

So this design is foundational, not incidental.

---

# 8. `pty.list` and `pty.get`: discovery and inspection

The list and get routes expose active PTY sessions as queryable runtime objects.

This is important because a remote client may need to:

- reconnect to an existing terminal
- render terminal tabs or panes
- inspect current terminal metadata
- recover state after page reload or reconnect

The PTY API is therefore designed for long-lived client relationships, not just one-time terminal creation.

---

# 9. `pty.create`: creating a managed terminal session

`POST /pty/` validates `Pty.CreateInput` and delegates to `Pty.create(...)`.

Create input can include:

- `command?`
- `args?`
- `cwd?`
- `title?`
- `env?`

This is flexible enough to support:

- default shell sessions
- custom command launches
- explicit working directories
- custom terminal titles
- extra environment injection

So PTY creation is configurable but still structured.

---

# 10. `Pty.create(...)` chooses a shell-aware default

If `command` is omitted, it uses:

- `Shell.preferred()`

If the command ends with `sh`, it appends:

- `-l`

This is an important UX and compatibility choice.

It means default interactive shell sessions are launched as login shells when appropriate, improving environment consistency with what users expect in a normal terminal.

---

# 11. Shell environment injection also applies to PTY sessions

`Pty.create(...)` calls:

- `Plugin.trigger("shell.env", { cwd }, { env: {} })`

and merges that with:

- `process.env`
- user-supplied `input.env`

This shows PTY sessions participate in the same environment-injection extension surface as shell tool execution.

That is excellent consistency.

PTY is not a separate environment world.

---

# 12. Why PTY environment consistency matters

If PTY sessions used a totally different environment model than:

- user shell execution
- model-driven bash tool execution

then debugging and user expectations would diverge badly.

Reusing `shell.env` keeps terminal behavior aligned with the rest of the shell/runtime surface.

---

# 13. PTY creation also injects terminal-specific environment markers

The created environment includes:

- `TERM = xterm-256color`
- `OPENCODE_TERMINAL = 1`

And on Windows it forces UTF-8-oriented locale variables.

These are transport-level compatibility details that improve terminal behavior across platforms and downstream tools.

---

# 14. PTY is backed by `bun-pty`

The runtime lazily imports:

- `spawn` from `bun-pty`

and uses it to create the actual pseudo-terminal process.

This means OpenCode is not emulating a terminal over plain subprocess pipes.

It is using a real PTY abstraction, which matters for:

- interactive shell behavior
- line editing
- TTY-aware tools
- full-screen terminal apps

That is the correct implementation approach.

---

# 15. Active PTY session state includes more than process info

Internally, an `ActiveSession` stores:

- `info`
- `process`
- `buffer`
- `bufferCursor`
- `cursor`
- `subscribers`

This reveals the two core runtime responsibilities beyond process management:

- replay buffer management
- live subscriber fan-out

That is the heart of the remote terminal API.

---

# 16. Why a replay buffer exists

A remote client may connect:

- after the PTY has already produced output
- after a reconnect
- after temporary network interruption

Without buffering, the client would only see future output and lose context.

The PTY subsystem solves this by maintaining a bounded output buffer and cursor offsets.

That turns a raw terminal stream into a reconnect-friendly remote terminal resource.

---

# 17. Buffer limits and why they matter

The PTY runtime defines:

- `BUFFER_LIMIT = 2 MiB`
- `BUFFER_CHUNK = 64 KiB`

As output arrives, the buffer is truncated from the front once it exceeds the limit, and `bufferCursor` is advanced accordingly.

This is important because terminal output can grow without bound.

The subsystem needs replayability without unbounded memory growth.

This design achieves that balance.

---

# 18. Subscriber fan-out model

Each active session maintains:

- `subscribers: Map<unknown, Socket>`

When PTY output arrives, the runtime iterates over subscribers and sends the chunk to each still-valid socket.

This means one PTY session can have multiple live observers.

That is useful for:

- multiple UI panes
- mirrored client views
- debugging or supervisory tools

The PTY API is therefore multi-subscriber aware.

---

# 19. Output replay and cursor semantics

`Pty.connect(id, ws, cursor?)` computes replay starting position based on:

- explicit numeric cursor
- special `cursor === -1` meaning start at live tail
- otherwise default from beginning of buffered content

Then it sends:

- buffered text chunks
- followed by a special metadata frame containing the current cursor

This is a strong protocol design.

It lets reconnecting clients synchronize precisely instead of guessing where they are in the stream.

---

# 20. The PTY WebSocket protocol has a control frame concept

`pty/index.ts` defines a control frame as:

- `0x00 + UTF-8 JSON`

using `meta(cursor)`.

That means the PTY WebSocket stream is not just raw text.

It is a lightweight multiplexed protocol with:

- data frames for terminal output
- control/meta frames for cursor synchronization

This is a key detail for client implementers.

---

# 21. Why the metadata frame matters

If the server only sent raw bytes, clients would struggle to know:

- how much replay they received
- where the current tail is
- what cursor to use on reconnect

The metadata frame solves that by sending the authoritative cursor boundary after replay.

That makes reconnection semantics robust.

---

# 22. `pty.connect` route: why WebSocket upgrade is the right transport

The connect route uses:

- `upgradeWebSocket(...)`

This is the correct transport choice for PTY interaction because terminal sessions need:

- low-latency bidirectional communication
- streaming output
- streaming input
- persistent attachment

HTTP request/response would be a poor fit here.

---

# 23. Route-level connect validation details

The route validates:

- `ptyID`
- optional query `cursor`

It also rejects invalid cursor values such as:

- non-integer values
- values below `-1`

This is a good API contract detail.

Cursor semantics are precise, and the route enforces that precision.

---

# 24. Socket validation in the route layer

Before calling `Pty.connect(...)`, the route validates that `ws.raw` behaves like a socket with:

- `readyState`
- `send(...)`
- `close(...)`

This is an example of the route layer doing transport-shape validation before handing off to runtime logic.

It keeps the PTY runtime from depending too heavily on an unchecked WebSocket object shape.

---

# 25. `Pty.connect(...)` also handles stale or invalid connections carefully

When a new client connects, the runtime:

- derives a stable connection key
- removes any previous mapping for that key
- stores the new subscriber
- cleans up dead sockets during output fan-out
- removes the subscriber on close

This is simple but important lifecycle hygiene.

A remote terminal API can leak resources easily if stale sockets are not cleaned up aggressively.

---

# 26. PTY input path is intentionally simple

Incoming WebSocket messages are forwarded to:

- `session.process.write(String(message))`

This means the PTY route does not try to invent a complex input protocol.

Terminal input is largely treated as raw user keystream data, which is exactly what a PTY expects.

That keeps the interface unsurprising.

---

# 27. `pty.update`: terminal metadata and resize control

`PUT /pty/:ptyID` accepts:

- `title?`
- `size? { rows, cols }`

and maps to `Pty.update(...)`.

This is important because a real remote terminal client needs more than input/output.

It also needs:

- title updates for UI representation
- window resizing so TTY-aware programs render correctly

The PTY API includes both.

---

# 28. Why resize belongs in the PTY API

Applications like:

- shells
- TUIs
- editors
- pagers

behave differently depending on terminal size.

Without resize support, many interactive terminal programs would behave poorly over the remote API.

So this is not optional polish.

It is core PTY correctness.

---

# 29. `pty.remove`: terminal termination as a control-plane action

`DELETE /pty/:ptyID` calls:

- `Pty.remove(id)`

The runtime then:

- deletes session state
- kills the process
- closes subscribers
- publishes `pty.deleted`

This makes PTY termination a clean lifecycle operation.

A remote client does not need to simulate shell `exit` semantics or guess how to tear down a terminal.

It can call the control-plane endpoint directly.

---

# 30. PTY exit behavior is also evented

When the process exits, the runtime:

- updates status to `exited`
- publishes `pty.exited`
- then removes the session

This is important because PTY state transitions are visible through the event system, not just through polling route responses.

So PTY integrates naturally with OpenCode’s broader event-driven runtime model.

---

# 31. PTY versus `SessionPrompt.shell()`

These are different abstractions.

## 31.1 `SessionPrompt.shell()`

- one user-driven shell action in the context of a session
- result folded back into message history

## 31.2 PTY API

- long-lived interactive terminal resource
- separate lifecycle
- live WebSocket interaction
- not inherently translated into assistant message history

This distinction is critical.

PTY is for interactive terminal sessions, not message-native shell turns.

---

# 32. PTY versus the model-driven `bash` tool

Likewise, PTY is distinct from the `bash` tool.

## 32.1 `bash` tool

- model-initiated
- permission-controlled
- output folded into tool parts
- meant for agent execution

## 32.2 PTY

- user/client-initiated interactive terminal
- remote UI surface
- not a normal tool-call lifecycle object

This is why the PTY API deserves separate documentation.

It occupies a different part of the product and runtime model.

---

# 33. A representative PTY API lifecycle

A typical remote terminal workflow looks like this:

## 33.1 Create terminal

- `POST /pty/`

## 33.2 Attach client

- `GET /pty/:ptyID/connect` via WebSocket

## 33.3 Replay buffered output

- server replays available buffered terminal content
- sends cursor metadata frame

## 33.4 Live interaction

- client sends input via WebSocket
- PTY output is broadcast live to subscribers

## 33.5 Resize or retitle as needed

- `PUT /pty/:ptyID`

## 33.6 Terminal exits or is removed

- `pty.exited` / `pty.deleted`
- client can remove it explicitly or observe automatic cleanup

This is a complete remote terminal resource lifecycle.

---

# 34. Key design principles behind this module

## 34.1 Interactive terminals should be modeled as managed resources, not just spawned processes

So PTY sessions have IDs, metadata, lifecycle routes, and event integration.

## 34.2 Remote terminal APIs need replay, not just live streaming

So the subsystem maintains a bounded buffer and cursor-based synchronization.

## 34.3 Terminal transport should remain close to PTY semantics

So WebSocket input is mostly raw keystream data, while resize and title are explicit control operations.

## 34.4 PTY should integrate with the same instance and plugin environment model as the rest of OpenCode

So it uses instance state and the `shell.env` hook.

---

# 35. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/routes/pty.ts`
2. `packages/opencode/src/pty/index.ts`
3. `packages/opencode/src/server/server.ts`
4. `packages/opencode/src/session/prompt.ts`
5. `packages/opencode/src/tool/bash.ts`

Focus on these functions and concepts:

- `Pty.create()`
- `Pty.connect()`
- replay buffer logic
- `meta(cursor)`
- `Pty.update()`
- `Pty.remove()`
- WebSocket upgrade flow
- PTY lifecycle events

---

# 36. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Should the PTY route surface expose a direct HTTP write endpoint in addition to WebSocket input for some clients?
- **Question 2**: How do current UI clients interpret the `0x00 + JSON` control frame and manage reconnect cursors in practice?
- **Question 3**: Are there cases where keeping exited PTY sessions around briefly would improve debuggability or UX?
- **Question 4**: Should PTY sessions carry more metadata, such as creation time, last activity time, or owning session linkage?
- **Question 5**: How well does the PTY subsystem behave under many concurrent subscribers or very high terminal output rates?
- **Question 6**: Should buffer size and replay chunking be configurable per deployment or per client capability?
- **Question 7**: How should PTY authorization be handled if the server is deployed beyond a local trusted environment?
- **Question 8**: Would some PTY workflows benefit from a tighter linkage to session/message history, or is the current separation the right long-term design?

---

# 37. Summary

The `pty_route_surface_and_remote_terminal_api` layer exposes OpenCode’s terminal subsystem as a real remote resource model:

- HTTP routes manage PTY session lifecycle and metadata
- a WebSocket route provides low-latency bidirectional terminal interaction
- the runtime maintains replay buffers and cursor metadata so clients can reconnect cleanly
- PTY sessions share instance scoping and environment injection patterns with the rest of OpenCode while remaining distinct from session-shell actions and model tool calls

So this module is not just terminal plumbing. It is the dedicated control-plane and transport surface that makes OpenCode’s interactive terminal capability remotely usable and reconnect-safe.
