# Global Routes / Server-Level Control Plane

---

# 1. Module Purpose

This document explains `server/routes/global.ts` as the server-wide control-plane surface for health, global events, global configuration, and multi-instance disposal.

The key questions are:

- Why does OpenCode need a `/global` route namespace in addition to instance-scoped routes?
- What is the difference between global control and instance-bound control?
- How does `/global/event` differ from the main `/event` stream in `server.ts`?
- Why are global config and global disposal exposed as explicit API operations?
- What does this file reveal about OpenCode’s server-wide architecture?

Primary source files:

- `packages/opencode/src/server/routes/global.ts`
- `packages/opencode/src/server/server.ts`
- `packages/opencode/src/bus/global.ts`
- `packages/opencode/src/project/instance.ts`
- `packages/opencode/src/config/config.ts`

This layer is OpenCode’s **server-level global control plane**.

---

# 2. Why `/global` exists at all

Most OpenCode routes are scoped to a concrete runtime instance or project directory.

But some concerns live above any single instance, such as:

- server health
- global config
- global event observation
- disposal of all instances

Those capabilities do not belong under `/session`, `/pty`, `/permission`, or other instance-bound namespaces.

So `/global` is the right place for them.

---

# 3. Global control versus instance control

A useful distinction is:

## 3.1 Instance control

- session execution
- PTY state
- permission/question pending state
- provider interactions for a concrete directory context

## 3.2 Global control

- server health
- cross-instance eventing
- configuration not tied to one active project instance
- disposal across all instances

`global.ts` is the API expression of that higher-level scope.

---

# 4. The route surface is compact but powerful

`GlobalRoutes` exposes:

- `GET /global/health`
- `GET /global/event`
- `GET /global/config`
- `PATCH /global/config`
- `POST /global/dispose`

This is a small surface area, but each route operates at server-wide scope.

That makes this file more important than its size suggests.

---

# 5. `global.health`: the simplest route, but still important

`GET /global/health` returns:

- `healthy: true`
- `version: Installation.VERSION`

This is a classic health-check route.

But it also doubles as a server identity route because it exposes the running OpenCode version.

That matters for:

- monitoring
- desktop or web client compatibility checks
- integration diagnostics
- automated probes

---

# 6. Why health belongs under `/global`

Health is not specific to any one instance.

A session may be absent, an instance may not yet be bootstrapped, or a directory may not yet be selected.

The server still needs a way to say:

- the OpenCode process is alive

So a global route is the correct place for this.

---

# 7. `global.config.get`: configuration as a server-level resource

`GET /global/config` returns:

- `Config.getGlobal()`

This shows that OpenCode distinguishes between:

- global configuration
- instance/session-specific runtime state

The API does not force every client to derive or reconstruct global config from scattered routes.

It makes configuration its own first-class control-plane resource.

---

# 8. `global.config.update`: config mutation is formal API behavior

`PATCH /global/config` validates against:

- `Config.Info`

then calls:

- `Config.updateGlobal(config)`

This is notable because it means the server supports programmatic configuration management, not just local file editing or internal UI mutation.

That is a strong platform feature.

---

# 9. Why global config is not instance-scoped

Some settings should affect the whole OpenCode environment regardless of which project instance is active.

If such config were only reachable through per-instance routes, clients would have to pick an arbitrary instance just to manage server-wide preferences.

That would be a bad abstraction.

The dedicated global config route solves this cleanly.

---

# 10. `/global/event`: the cross-instance event channel

`GET /global/event` uses:

- `GlobalBus`
- `streamSSE(...)`

This is the global analog of the instance-bound `/event` route from `server.ts`.

The key distinction is scope:

- `/event` streams instance/runtime bus events
- `/global/event` streams global bus events

That difference is architecturally important.

---

# 11. The `GlobalBus` event payload shape

The route describes the SSE payload as an object containing:

- `directory`
- `payload`

where `payload` itself is a `BusEvent` payload.

This is interesting because global events preserve:

- both the event itself
- and the originating directory context

That makes global observation useful in a multi-instance environment.

---

# 12. Why directory tagging matters on global events

A global event stream without per-directory tagging would be much less useful.

Clients would know something happened somewhere, but not where.

By including `directory`, the global event channel can support:

- multi-project dashboards
- global observers
- cross-instance orchestration tools
- smarter client-side routing of events to the right view

This is a strong design choice.

---

# 13. `/global/event` stream lifecycle

On connection, the route sends:

- `server.connected`

wrapped inside the global event payload shape.

Then it attaches a handler with:

- `GlobalBus.on("event", handler)`

It also emits heartbeats every 10 seconds:

- `server.heartbeat`

And on stream abort it:

- clears the heartbeat
- detaches the handler
- logs the disconnect

This is a careful long-lived SSE lifecycle.

---

# 14. Why heartbeats exist here too

Just like the instance-bound `/event` stream, the global event stream explicitly defends against stalled proxy connections.

That is important because global observers may stay connected for a long time and still go quiet between meaningful events.

Heartbeat traffic keeps the stream operational across infrastructure that dislikes idle SSE connections.

---

# 15. Why `/global/event` uses `GlobalBus.on/off` instead of `Bus.subscribeAll`

This is the clearest code-level proof that OpenCode maintains:

- one eventing layer for instance/runtime-local activity
- another for global/server-wide activity

The global route is not just a duplicate API path.

It is backed by a different event source with different scope and semantics.

---

# 16. `global.dispose`: disposal across all instances

`POST /global/dispose` does two things:

- `await Instance.disposeAll()`
- emits a `global.disposed` event through `GlobalBus`

This is one of the strongest server-level control-plane operations in the codebase.

It is essentially a mass cleanup/reset command for the server runtime.

---

# 17. Why global dispose emits a global event explicitly

After disposing all instances, the route emits:

- `global.disposed`

with `directory: "global"`

That matters because external clients need to know that the entire runtime landscape has changed, not just one session or one instance.

A global event is the right way to signal that.

---

# 18. Why `directory: "global"` is a useful convention

The disposal event uses:

- `directory: "global"`

This is a simple but effective namespace convention.

It tells consumers:

- this event is not about one project directory
- it concerns the global server state

That is much clearer than using an empty directory or forcing clients to infer global scope another way.

---

# 19. Why global disposal belongs in the public API

Clients may need to:

- force cleanup of leaked runtime state
- reset the server between tests
- react to environment changes
- intentionally reload the control plane

Without a global disposal API, those workflows would require out-of-band process control.

Exposing it through the control plane makes the runtime more manageable.

---

# 20. Relationship to instance disposal in `server.ts`

There are two distinct disposal routes in the broader server surface:

## 20.1 `POST /instance/dispose`

- dispose only the current bound instance

## 20.2 `POST /global/dispose`

- dispose all instances
- emit a global disposal event

This two-level disposal model is consistent with the broader architecture:

- instance scope
- global scope

---

# 21. Why global routes are intentionally not crowded

This file could have become a dumping ground for miscellaneous server functions.

Instead, it stays focused on truly global concerns:

- health
- event observation
- config
- mass disposal

That restraint is good API design.

It keeps the namespace semantically crisp.

---

# 22. Why this module is part of the control plane, not just admin plumbing

A control plane is where clients can:

- inspect server-wide state
- watch server-wide changes
- mutate server-wide policy
- perform server-wide lifecycle operations

`global.ts` does all of that.

So these routes are not incidental admin helpers.

They are a real part of the OpenCode platform surface.

---

# 23. How `/global/event` complements `/event`

A good mental model is:

## 23.1 `/event`

- instance-bound
- powered by `Bus`
- used for detailed runtime activity inside the current instance

## 23.2 `/global/event`

- server-wide / cross-instance
- powered by `GlobalBus`
- used for global lifecycle and cross-directory observability

Clients may use one or both depending on the level of control they need.

---

# 24. A representative global control-plane flow

A realistic flow looks like this:

## 24.1 Client checks server health

- `GET /global/health`

## 24.2 Client loads global config

- `GET /global/config`

## 24.3 Client subscribes to global lifecycle events

- `GET /global/event`

## 24.4 Client updates global preferences if needed

- `PATCH /global/config`

## 24.5 Client triggers server-wide cleanup

- `POST /global/dispose`

## 24.6 Subscribers observe `global.disposed`

This is a proper server-level control-plane lifecycle.

---

# 25. Key design principles behind this module

## 25.1 Global concerns should be separated cleanly from instance concerns

So health, global config, and global disposal live under `/global`.

## 25.2 Multi-instance systems need both local and global event channels

So OpenCode exposes both `/event` and `/global/event` with different buses behind them.

## 25.3 Long-lived SSE control-plane streams need heartbeat and cleanup discipline

So the global event route explicitly handles connected, heartbeat, and abort cleanup semantics.

## 25.4 Server lifecycle operations should be externally manageable

So disposal and config management are formal API actions, not hidden internals.

---

# 26. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/routes/global.ts`
2. `packages/opencode/src/server/server.ts`
3. `packages/opencode/src/bus/global.ts`
4. `packages/opencode/src/project/instance.ts`
5. `packages/opencode/src/config/config.ts`

Focus on these functions and concepts:

- `GlobalRoutes`
- `GlobalDisposedEvent`
- `/global/event`
- `GlobalBus.on/off`
- `Config.getGlobal()`
- `Config.updateGlobal()`
- `Instance.disposeAll()`

---

# 27. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: What exact event sources feed `GlobalBus`, and how broad is its intended long-term contract?
- **Question 2**: Which clients today actually consume `/global/event` in addition to instance-bound `/event`?
- **Question 3**: Should global config updates themselves emit explicit global config-changed events?
- **Question 4**: Are there additional truly global resources that should eventually live under `/global`, such as installation, auth overview, or workspace inventory?
- **Question 5**: How should `/global/dispose` behave in multi-user or remote deployment scenarios where mass disposal could be disruptive?
- **Question 6**: Should the global health route surface more operational diagnostics beyond version and `healthy: true`?
- **Question 7**: How should clients combine global and instance event subscriptions without duplicating work or producing confusing UX?
- **Question 8**: Is `directory: "global"` sufficient as a global-event marker, or should the event schema eventually make scope explicit?

---

# 28. Summary

The `global_routes_and_server_level_control_plane` layer exposes the OpenCode server’s truly global concerns as a clean API surface:

- health and version are available without instance context
- global configuration is retrievable and mutable through typed routes
- `/global/event` provides a cross-instance SSE stream backed by `GlobalBus`
- `/global/dispose` gives clients a formal way to reset all active instances and observe that transition

So this module is not just admin scaffolding. It is the top-level server control plane that sits above the instance-bound runtime APIs and makes OpenCode manageable as a full platform process.
