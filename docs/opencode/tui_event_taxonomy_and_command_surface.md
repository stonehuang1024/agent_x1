# TUI Event Taxonomy / Command Surface

---

# 1. Module Purpose

This document explains the TUI event vocabulary itself, focusing on the event definitions in `cli/cmd/tui/event.ts` and how those events are consumed by the terminal UI.

The key questions are:

- What events make up the current TUI event contract?
- Why is `TuiEvent` defined as bus events rather than ad hoc UI callbacks?
- How do `PromptAppend`, `CommandExecute`, `ToastShow`, and `SessionSelect` differ semantically?
- Which command names appear to be part of the intended TUI command vocabulary?
- What does this event layer reveal about how OpenCode treats the TUI as an event-driven frontend?

Primary source files:

- `packages/opencode/src/cli/cmd/tui/event.ts`
- `packages/opencode/src/cli/cmd/tui/app.tsx`
- `packages/opencode/src/cli/cmd/tui/component/prompt/index.tsx`
- `packages/opencode/src/cli/cmd/tui/ui/toast.tsx`
- `packages/opencode/src/server/routes/tui.ts`

This layer is OpenCode’s **TUI event contract and command-dispatch vocabulary**.

---

# 2. Why this layer matters

The `/tui` routes and queue bridge explain how HTTP or other runtime layers talk to the terminal UI.

But the real stable-ish interface on the UI side is:

- `TuiEvent`

That is the vocabulary the UI listens to.

So if you want to understand how external actions become UI behavior, the event definitions are the next authoritative layer.

---

# 3. `TuiEvent` is defined as typed bus events

`cli/cmd/tui/event.ts` defines a compact event set using:

- `BusEvent.define(...)`

This means TUI integration is part of the same event-definition discipline used elsewhere in OpenCode.

The TUI is not wired through arbitrary callback names or untyped payloads.

It is integrated into the shared bus/event contract model.

---

# 4. Why that matters architecturally

Using `BusEvent.define(...)` gives each TUI event:

- a stable type name string
- a structured payload schema
- a shared representation compatible with bus publication/subscription

That is much better than scattering one-off UI callbacks throughout the application.

It makes the TUI part of the broader observable runtime architecture.

---

# 5. The current TUI event taxonomy is intentionally small

The defined events are:

- `TuiEvent.PromptAppend`
- `TuiEvent.CommandExecute`
- `TuiEvent.ToastShow`
- `TuiEvent.SessionSelect`

This is a deliberately small vocabulary.

That is a good sign.

It suggests the system is trying to expose a focused, reusable UI event layer rather than encoding every possible widget action as a separate top-level event.

---

# 6. `PromptAppend`: prompt-buffer mutation event

`PromptAppend` is defined as:

- type: `tui.prompt.append`
- properties: `{ text: string }`

This is the most direct editing-oriented TUI event.

Its semantics are simple:

- insert text into the prompt input area

This is a UI-local interaction, not a session execution event.

---

# 7. How `PromptAppend` is consumed

In `component/prompt/index.tsx`, the TUI subscribes to:

- `sdk.event.on(TuiEvent.PromptAppend.type, ...)`

and calls:

- `input.insertText(evt.properties.text)`

This is very clean.

It shows the event contract maps directly onto concrete UI behavior without route-level coupling.

---

# 8. Why prompt append is its own event instead of a command

Appending raw text is not really a high-level command like:

- open help
- submit prompt
- switch session

It is a direct manipulation of prompt state.

So it makes sense that `PromptAppend` is a dedicated event with text payload rather than being forced into the command channel.

That is good semantic separation.

---

# 9. `CommandExecute`: the largest and most flexible TUI event

`CommandExecute` is defined as:

- type: `tui.command.execute`
- properties: `{ command: ... }`

where `command` is a union of:

- a long enumerated set of known command names
- arbitrary `z.string()`

This event is the main generic command-dispatch surface for the TUI.

---

# 10. Why `CommandExecute` is so central

Many UI actions do not need their own specialized event schema.

They can be expressed as:

- execute this named command

That creates a powerful indirection layer:

- routes and other producers publish command intent
- the TUI command system decides how that intent is fulfilled

This is one of the cleanest design choices in the TUI layer.

---

# 11. The known command vocabulary reveals intended UI behaviors

The enumerated commands currently include:

- `session.list`
- `session.new`
- `session.share`
- `session.interrupt`
- `session.compact`
- `session.page.up`
- `session.page.down`
- `session.line.up`
- `session.line.down`
- `session.half.page.up`
- `session.half.page.down`
- `session.first`
- `session.last`
- `prompt.clear`
- `prompt.submit`
- `agent.cycle`

This list is very informative.

It shows the TUI command layer spans:

- navigation
- session lifecycle actions
- prompt actions
- agent selection/cycling

So `CommandExecute` is not just a small helper.

It is the main action-dispatch interface of the TUI.

---

# 12. Why the schema still allows arbitrary strings

`command` is defined as:

- `z.union([z.enum([...known commands...]), z.string()])`

That is an interesting compromise.

It means the event schema documents a known core command vocabulary, but does not hard-block new or external command names.

This gives the system:

- discoverable known commands
- future extensibility
- looser coupling between publishers and the full command registry

That is a practical design.

---

# 13. The tradeoff of allowing arbitrary command strings

This flexibility also means the event schema alone does not guarantee a command is actually supported by the TUI.

So the command event contract is partly:

- typed known core
- partly open string namespace

That is useful, but clients should remember it means command validity may depend on the runtime command registry, not just the event schema.

---

# 14. How `CommandExecute` is consumed in the TUI

In `app.tsx`, the TUI subscribes to:

- `sdk.event.on(TuiEvent.CommandExecute.type, ...)`

and calls:

- `command.trigger(evt.properties.command)`

This is the key bridge from event vocabulary to actual UI behavior.

The event layer does not execute commands itself.

It delegates to the TUI command system.

---

# 15. Why command dispatch through one trigger point is strong design

A single `command.trigger(...)` sink means command producers do not need to know how commands are implemented.

That improves:

- decoupling
- reuse
- testability
- transport independence

Routes, MCP internals, or other runtime layers can all request the same command execution by publishing one bus event.

That is exactly the kind of architecture you want for a UI command system.

---

# 16. `ToastShow`: presentation-layer feedback event

`ToastShow` is defined as:

- type: `tui.toast.show`
- properties:
  - optional `title`
  - required `message`
  - `variant` in `info | success | warning | error`
  - optional `duration`

This is the main user-feedback/notification event in the TUI layer.

---

# 17. Why toast is a dedicated event rather than a command

Toast display is not a durable application command in the same sense as session navigation or prompt submission.

It is transient UI feedback.

So it is better expressed as a data-rich event than as a command string.

That lets producers specify:

- message content
- severity
- display duration

without overloading the command system.

---

# 18. How `ToastShow` is consumed

In `app.tsx`, the TUI listens to:

- `TuiEvent.ToastShow.type`

and forwards the payload to:

- `toast.show(...)`

Then `ui/toast.tsx` parses the options with:

- `TuiEvent.ToastShow.properties.parse(options)`

before storing the current toast and scheduling timeout behavior.

This is a nice example of end-to-end schema reuse.

The same event schema validates both producers and UI consumption.

---

# 19. Why `duration` defaults in the event schema matter

`duration` has a default value of:

- `5000`

This is subtle but important.

It means the event schema itself carries a UX default rather than forcing every producer to specify it.

That keeps producers simpler and centralizes part of the notification contract.

---

# 20. `SessionSelect`: navigation event with stronger identity semantics

`SessionSelect` is defined as:

- type: `tui.session.select`
- properties: `{ sessionID }`

This is a more identity-specific navigation event.

Unlike `CommandExecute`, it does not encode an action name.

It directly names the target session resource to navigate to.

---

# 21. Why `SessionSelect` is not just another command string

Navigating to a particular session requires a parameterized payload:

- which session?

That is better modeled as a dedicated event with a typed `sessionID` field than as a generic command string with ad hoc arguments.

This is exactly the kind of case where a specialized event is better than a command.

---

# 22. How `SessionSelect` is consumed

In `app.tsx`, the TUI listens to:

- `TuiEvent.SessionSelect.type`

and calls route navigation with:

- `type: "session"`
- `sessionID: evt.properties.sessionID`

So the event directly drives structured UI navigation.

This is a clean event-to-router binding.

---

# 23. The TUI event system is already used outside the TUI route file

The grep results show non-route producers, for example in `mcp/index.ts`, publishing:

- `TuiEvent.ToastShow`

for authentication warnings.

This is important.

It proves the TUI event layer is not only an HTTP-facing UI bridge.

It is a general runtime-to-UI communication surface.

That makes it architecturally significant.

---

# 24. Why this broader producer set matters

Because arbitrary runtime subsystems can publish TUI events, the event definitions effectively become a frontend contract shared across the whole application.

That raises the importance of:

- event naming
- payload stability
- schema clarity

So `TuiEvent` is much more than a local file of constants.

---

# 25. Relationship to `/tui` routes

The `/tui` route file mostly does one of two things:

- validates input against `TuiEvent.*.properties`
- publishes the corresponding `TuiEvent`

This means the route layer is truly downstream of the event contract.

The event definitions are the more authoritative abstraction.

That is exactly how it should be.

---

# 26. Relationship to the queue bridge

The queue bridge is for request/response interactions.

`TuiEvent` is for broadcast or action signaling.

These two mechanisms solve different problems:

- `TuiEvent` -> state change or UI action notification
- control queue -> interactions requiring a response

Seeing both together makes the TUI architecture much clearer.

---

# 27. A representative event-driven TUI flow

A typical flow looks like this:

## 27.1 Some subsystem or route publishes `TuiEvent.CommandExecute`

- for example `prompt.submit`

## 27.2 The TUI app receives the event

- via `sdk.event.on(...)`

## 27.3 The app forwards the command to the command system

- `command.trigger(...)`

## 27.4 UI behavior changes accordingly

- submit prompt, open dialog, navigate, etc.

A similar pattern holds for toast and prompt-append events, just with different sinks.

---

# 28. Why this layer matters architecturally

The TUI event layer reveals that OpenCode’s terminal frontend is structured around:

- typed event inputs
- a reusable command-dispatch core
- dedicated data-rich events for operations that do not fit the command model

That is a mature frontend integration design.

It avoids both extremes:

- everything as one giant untyped event bus
- everything as rigid specialized routes/commands

Instead it uses a balanced event taxonomy.

---

# 29. Key design principles behind this module

## 29.1 Frontend interactions should be expressed as typed bus events when they need to cross subsystem boundaries

So `TuiEvent` is defined with `BusEvent.define(...)` and zod schemas.

## 29.2 Generic command execution should be centralized rather than duplicated across producers

So `CommandExecute` funnels action requests into `command.trigger(...)`.

## 29.3 Some UI actions deserve dedicated typed events instead of being forced into a command string channel

So prompt append, toast display, and session selection each have their own event shapes.

## 29.4 Event schemas should be reusable at both production and consumption sites

So the same event property schemas validate route inputs and UI-side handling.

---

# 30. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/cli/cmd/tui/event.ts`
2. `packages/opencode/src/cli/cmd/tui/app.tsx`
3. `packages/opencode/src/cli/cmd/tui/component/prompt/index.tsx`
4. `packages/opencode/src/cli/cmd/tui/ui/toast.tsx`
5. `packages/opencode/src/server/routes/tui.ts`

Focus on these functions and concepts:

- `TuiEvent.PromptAppend`
- `TuiEvent.CommandExecute`
- `TuiEvent.ToastShow`
- `TuiEvent.SessionSelect`
- `command.trigger(...)`
- `toast.show(...)`
- prompt input insertion
- route validation against event schemas

---

# 31. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Which command names are actually registered in the TUI command system beyond the small core list enumerated in `TuiEvent.CommandExecute`?
- **Question 2**: Should `CommandExecute` remain open to arbitrary strings, or should it eventually align with a stronger shared command registry schema?
- **Question 3**: Which `TuiEvent` definitions are intended to be stable public integration contracts versus internal implementation details?
- **Question 4**: Should more navigation actions use dedicated typed events like `SessionSelect` instead of generic commands?
- **Question 5**: How should runtime subsystems decide whether to emit a toast versus a command or a richer request/response interaction?
- **Question 6**: Are there missing TUI events today for common UI integration tasks that are currently forced awkwardly through commands?
- **Question 7**: How should event-versioning or deprecation be handled if the TUI vocabulary expands significantly?
- **Question 8**: What telemetry or debugging tools exist to inspect live TUI event traffic across producers and consumers?

---

# 32. Summary

The `tui_event_taxonomy_and_command_surface` layer defines the actual event vocabulary that powers OpenCode’s terminal UI integration:

- `PromptAppend` handles prompt-text insertion
- `CommandExecute` is the main generic action-dispatch channel
- `ToastShow` carries transient user-feedback notifications
- `SessionSelect` supports parameterized session navigation

These events are produced by routes and runtime subsystems alike, then consumed by the TUI app through bus subscriptions and command/UI handlers.

So this module is the real frontend event contract behind the TUI route surface.
