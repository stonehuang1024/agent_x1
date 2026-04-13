# TUI Routes / Terminal UI Surface

---

# 1. Module Purpose

This document explains the `/tui` route namespace, which exposes an HTTP control surface for driving and integrating with OpenCode’s terminal user interface.

The key questions are:

- Why does OpenCode expose a dedicated TUI-facing HTTP route surface?
- How do these routes relate to `TuiEvent`, the global bus, and terminal-side command execution?
- Why does the module contain both direct UI action routes and a request/response queue bridge?
- What is the difference between publishing TUI events and invoking TUI control requests?
- What does this route surface reveal about how OpenCode treats its terminal UI as a remotely steerable frontend?

Primary source files:

- `packages/opencode/src/server/routes/tui.ts`
- `packages/opencode/src/cli/cmd/tui/event`
- `packages/opencode/src/bus`
- `packages/opencode/src/session`

This layer is OpenCode’s **terminal-UI command bridge and TUI event control-plane API**.

---

# 2. Why `/tui` exists separately

The TUI is a real frontend surface with its own interaction model.

It needs to respond to actions like:

- append text to the prompt
- open dialogs
- submit or clear prompts
- execute named commands
- show toast notifications
- publish UI-specific events

Those operations are not the same as:

- session control
- PTY interaction
- filesystem browsing

So a dedicated `/tui` namespace is the right design.

---

# 3. Two different integration models live in this file

`server/routes/tui.ts` contains two distinct patterns:

## 3.1 Event-publishing routes under `TuiRoutes`

- push commands or events onto the bus for the TUI to react to

## 3.2 Queue-based request/response bridge via `callTui` and `TuiControlRoutes`

- pass arbitrary request payloads to a waiting TUI control consumer and wait for a response

This distinction is very important.

The module is not just “emit UI events.”

It also contains an RPC-like bridge for TUI interaction.

---

# 4. The request/response bridge is built on `AsyncQueue`

The file defines:

- `request = new AsyncQueue<TuiRequest>()`
- `response = new AsyncQueue<any>()`

and a helper:

- `callTui(ctx)`

which reads the incoming JSON body, queues a request, and waits for the next response.

This is effectively a simple in-process rendezvous channel between HTTP and a TUI-side consumer.

---

# 5. Why this queue bridge exists

Some interactions are not just fire-and-forget UI commands.

They may need:

- a terminal-side processor to inspect a request
- a corresponding response to be returned to the HTTP caller

That is different from publishing a bus event.

The queue bridge supports this request/response style cleanly.

---

# 6. `TuiRequest`: the bridge payload shape

Queued TUI requests contain:

- `path`
- `body`

This is a deliberately generic shape.

It lets the TUI-side consumer know:

- which logical route was invoked
- what payload was provided

without forcing a more rigid schema for every bridged action.

That makes the bridge flexible, though also less strongly typed than the event routes.

---

# 7. `GET /tui/control/next`: pull the next pending TUI request

`TuiControlRoutes` exposes:

- `GET /next`

which waits on:

- `request.next()`

and returns the next queued `TuiRequest`.

This is a pull-based control-plane endpoint for a TUI-side worker or frontend process.

---

# 8. Why a pull model is used here

A pull model makes sense when the terminal-side consumer is effectively an active process asking:

- do you have work for me?

rather than expecting the server to push into the TUI over a separate transport.

This is a simple and robust integration style for a local terminal frontend.

---

# 9. `POST /tui/control/response`: complete the pending bridge request

The control routes also expose:

- `POST /response`

which pushes the provided JSON body into:

- `response`

and returns `true`.

This completes the other half of the bridge.

It lets the TUI-side processor send a result back to whatever originally called `callTui(...)`.

---

# 10. Why this is essentially a lightweight TUI RPC channel

Taken together:

- queue request
- wait for `/next`
- send `/response`

forms a very lightweight RPC-like mechanism between HTTP clients and a TUI process.

That is a powerful pattern hiding inside a fairly small file.

---

# 11. Most of the public `/tui` routes are bus-driven actions

The main `TuiRoutes` namespace is mostly built around:

- `Bus.publish(...)`
- `TuiEvent.*`

This means many TUI-facing actions are modeled as event publications rather than direct imperative UI mutation in the route handler.

That is consistent with OpenCode’s broader event-driven architecture.

---

# 12. `POST /tui/append-prompt`: prompt injection into the UI

This route validates against:

- `TuiEvent.PromptAppend.properties`

then publishes:

- `TuiEvent.PromptAppend`

This is a clean example of UI intent being represented as a typed event rather than ad hoc mutation logic.

---

# 13. Why prompt append is evented

Appending text to the UI prompt is an interface event, not a session mutation by itself.

By modeling it as a `TuiEvent`, the route keeps the HTTP layer decoupled from specific terminal-rendering internals.

The TUI decides how to render and apply that event.

That is good separation of concerns.

---

# 14. Several routes map directly to named TUI commands

Routes like:

- `/open-help`
- `/open-sessions`
- `/open-themes`
- `/open-models`
- `/submit-prompt`
- `/clear-prompt`

all publish:

- `TuiEvent.CommandExecute`

with a specific command string.

This is an important architectural choice.

The HTTP route surface is not directly manipulating UI widgets.

It is translating HTTP actions into the same command vocabulary the TUI already understands.

---

# 15. Why command-driven UI integration is strong design

A command vocabulary provides:

- reuse across keyboard shortcuts, menus, and HTTP bridges
- decoupling between transport and UI implementation
- a stable internal action model

That means the `/tui` API can stay relatively thin while still steering complex UI behavior.

This is a good frontend control-plane pattern.

---

# 16. `open-help`, `open-sessions`, and `open-models`

These routes are straightforward command shims:

- `help.show`
- `session.list`
- `model.list`

They expose common UI navigational actions through HTTP.

This suggests OpenCode expects non-TUI actors to be able to steer the TUI intentionally.

That is very useful for integrations.

---

# 17. `open-themes` is a notable detail

The `open-themes` route currently publishes:

- `command: "session.list"`

rather than a theme-specific command name.

This is a source-grounded detail worth documenting.

It may be intentional reuse, a placeholder, or a bug, but it is definitely not a direct “theme dialog” command in the current implementation.

---

# 18. `submit-prompt` and `clear-prompt` are UI-command operations, not session execution routes

These routes publish commands:

- `prompt.submit`
- `prompt.clear`

That means they are operating at the TUI interaction layer.

They are not directly invoking session prompt-processing logic in the route itself.

The terminal UI remains the actor that interprets and performs those actions.

This distinction matters.

---

# 19. `POST /tui/execute-command`: alias translation layer

This route accepts:

- `{ command: string }`

then maps user-facing command aliases like:

- `session_new`
- `session_share`
- `session_interrupt`
- `messages_page_up`
- `agent_cycle`

into internal command names like:

- `session.new`
- `session.share`
- `session.interrupt`
- `session.page.up`
- `agent.cycle`

and publishes `TuiEvent.CommandExecute`.

This is an important translation surface.

---

# 20. Why alias translation is useful

External clients may use command identifiers that differ from the TUI’s internal naming scheme.

This route acts as a compatibility layer between:

- a transport/API-facing command vocabulary
- the TUI’s internal command system

That reduces coupling and lets the TUI keep a cleaner internal naming model.

---

# 21. Why the route uses a handwritten command map

The route contains an inline mapping object and an `@ts-expect-error` comment.

That is a notable implementation detail.

It suggests the alias surface is maintained manually here rather than derived from a shared command registry.

That may be expedient, but it also means the route can drift if command inventories evolve.

---

# 22. `POST /tui/show-toast`: UI notification surface

This route validates against:

- `TuiEvent.ToastShow.properties`

and publishes:

- `TuiEvent.ToastShow`

This is a nice example of the TUI route surface exposing not just navigation and prompt operations, but also presentation-layer notifications.

It makes the TUI externally steerable at the UX level.

---

# 23. `POST /tui/publish`: generic TUI event publication

The file also exposes a generic publish route that validates against a union of all known `TuiEvent` definitions and publishes the chosen event.

This is one of the most flexible routes in the namespace.

It effectively turns the TUI event system itself into an externally reachable control-plane surface.

---

# 24. Why generic publish is powerful and risky

A generic event-publish endpoint is powerful because it lets advanced clients or bridges drive many UI behaviors without needing one route per action.

But it also increases coupling to the TUI event model and may expose more of the UI’s internal event vocabulary than a narrower API would.

That tradeoff is important to understand.

---

# 25. Relationship to `Bus`

Most TUI routes simply do:

- `Bus.publish(...)`

That means the terminal UI is integrated into the same event backbone used by many other OpenCode subsystems.

This is good architectural consistency.

The TUI is not isolated behind a completely separate side channel.

It participates in the same event-driven runtime model.

---

# 26. Why `/tui` matters even if you are not using the terminal UI directly

This route surface is valuable to:

- automation bridges
- desktop wrappers
- remote controllers
- integration tests
- alternate frontends that want to steer or interoperate with the TUI

So `/tui` is not just an implementation detail of the terminal app.

It is a real integration surface.

---

# 27. A representative TUI integration flow

A typical flow could look like this:

## 27.1 Append text into the prompt buffer

- `POST /tui/append-prompt`

## 27.2 Open a TUI dialog or view

- `POST /tui/open-help`
- `POST /tui/open-sessions`
- `POST /tui/open-models`

## 27.3 Submit or clear the prompt

- `POST /tui/submit-prompt`
- `POST /tui/clear-prompt`

## 27.4 Show feedback in the UI

- `POST /tui/show-toast`

## 27.5 Use the control queue bridge when a request/response exchange is needed

- `/tui/control/next`
- `/tui/control/response`

This is a coherent remote-TUI control workflow.

---

# 28. Why this module matters architecturally

The `/tui` routes show that OpenCode’s terminal UI is not treated as a sealed local-only frontend.

Instead, it is exposed as:

- an event-driven controllable interface
- with both fire-and-forget actions and request/response bridging

That makes the TUI a programmable surface in the overall platform architecture.

This is a much richer design than “the CLI just renders stuff locally.”

---

# 29. Key design principles behind this module

## 29.1 UI actions should be expressed in the same event and command vocabulary the TUI already understands

So most routes publish `TuiEvent` or `CommandExecute` instead of directly mutating UI state.

## 29.2 Some TUI integrations need request/response semantics, not just event publication

So the module includes an `AsyncQueue`-based control bridge.

## 29.3 The TUI should be remotely steerable without tightly coupling the HTTP layer to rendering internals

So routes speak in commands, events, and generic queued payloads.

## 29.4 Integration convenience sometimes justifies alias translation at the edge

So `/execute-command` maps transport-facing aliases into internal command names.

---

# 30. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/routes/tui.ts`
2. `packages/opencode/src/cli/cmd/tui/event`
3. `packages/opencode/src/bus`
4. `packages/opencode/src/cli/cmd/tui`

Focus on these functions and concepts:

- `callTui()`
- `TuiControlRoutes`
- `AsyncQueue`
- `TuiEvent.PromptAppend`
- `TuiEvent.CommandExecute`
- `TuiEvent.ToastShow`
- generic `/tui/publish`
- alias mapping in `/tui/execute-command`

---

# 31. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Which code path actually mounts or exposes `TuiControlRoutes`, and how is the queue bridge consumed in practice?
- **Question 2**: Is `open-themes` intentionally mapped to `session.list`, or is that a bug or unfinished implementation?
- **Question 3**: Should the command alias mapping in `/execute-command` be derived from a shared command registry instead of a handwritten map?
- **Question 4**: Which `TuiEvent` variants are intended to be stable external contracts versus internal UI implementation details?
- **Question 5**: How are queue ordering, cancellation, or timeout semantics handled for `callTui()` request/response exchanges?
- **Question 6**: Should the generic publish route be narrowed for security or stability in less-trusted deployments?
- **Question 7**: How do non-terminal clients use this API today, and which endpoints are most important in practice?
- **Question 8**: How should the TUI surface interact with remote workspace routing and multi-instance server usage over time?

---

# 32. Summary

The `tui_routes_and_terminal_ui_surface` layer turns OpenCode’s terminal UI into a programmable control-plane surface:

- most routes publish typed TUI events or command executions onto the shared bus
- the module also includes an `AsyncQueue`-based request/response bridge for richer TUI integrations
- HTTP-facing command aliases can be translated into internal TUI commands
- toast, prompt, dialog, and generic event publication are all exposed as remote actions

So this module is not just UI glue. It is the API surface that makes OpenCode’s terminal frontend steerable, automatable, and integrated into the broader event-driven system.
