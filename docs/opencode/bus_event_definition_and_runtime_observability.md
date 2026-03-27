# Bus Event Definition / Runtime Observability

---

# 1. Module Purpose

This document explains the core event-definition primitives behind OpenCode’s runtime observability model, focusing on `BusEvent`, `Bus`, and `GlobalBus`.

The key questions are:

- How are event types defined in OpenCode?
- Why does the system separate `BusEvent`, `Bus`, and `GlobalBus`?
- How do typed instance events become globally observable events?
- What role does the event registry play in shaping runtime contracts and schemas?
- What tradeoffs exist between type safety, instance scoping, and global aggregation?

Primary source files:

- `packages/opencode/src/bus/bus-event.ts`
- `packages/opencode/src/bus/index.ts`
- `packages/opencode/src/bus/global.ts`

This layer is OpenCode’s **typed event definition and observability backbone**.

---

# 2. Why this layer matters

Many OpenCode subsystems are event-driven:

- sessions
- permissions
- questions
- PTYs
- project updates
- TUI interactions
- workspace synchronization

But those event systems all depend on a small shared core.

That core is what determines:

- how event types are named
- how payloads are shaped
- how subscribers listen
- how events become globally observable

So this is one of the most foundational infrastructure layers in the codebase.

---

# 3. `BusEvent.define(...)`: where event contracts start

The basic primitive is:

- `BusEvent.define(type, properties)`

A definition contains:

- `type`
- `properties`

where `properties` is a zod schema.

This is the root of the event contract model.

It means events are not just strings floating around the system.

They are named contracts with associated payload schemas.

---

# 4. Why zod-backed event definitions are important

Using zod at the definition site gives OpenCode:

- structured payload schemas
- reusable typing
- reusable validation surfaces
- the ability to derive larger schema unions later

This is much stronger than plain string event names with untyped payload objects.

It lets events participate in the same typed-contract culture used elsewhere in the server and route surfaces.

---

# 5. `BusEvent` also maintains a registry

`bus-event.ts` keeps:

- `const registry = new Map<string, Definition>()`

and every call to `define(...)` registers the definition under its type name.

This is a major design choice.

The system does not merely create isolated constants.

It keeps a central registry of known event definitions.

---

# 6. Why the registry matters

A registry allows OpenCode to later ask:

- what event types are known?
- what is the full discriminated union schema of all events?

That turns the event layer from a passive naming convention into an introspectable runtime contract system.

This is especially useful for observability and schema generation.

---

# 7. `BusEvent.payloads()`: all events as one discriminated union

`payloads()` constructs:

- `z.discriminatedUnion("type", ...)`

over all registered event definitions, where each union member has:

- `type: z.literal(type)`
- `properties: def.properties`

This is a powerful capability.

It means the entire event vocabulary can be represented as one schema.

That is exactly the kind of primitive needed for:

- event-stream contracts
- API schemas
- tooling and introspection

---

# 8. Why the event union is an important architecture signal

When a system can derive “all events” as a typed union, it is treating events as first-class API/runtime contracts rather than incidental logging messages.

This aligns with everything else we have seen in OpenCode:

- route schemas are typed
- config shapes are typed
- tool interfaces are typed
- event contracts are typed too

That consistency is a mark of deliberate architecture.

---

# 9. `Bus`: instance-scoped event dispatch

`bus/index.ts` defines `Bus` as the main instance-scoped event hub.

It includes:

- typed event publishing
- typed subscription helpers
- wildcard subscription
- a special instance-disposal event
- automatic forwarding into `GlobalBus`

This makes `Bus` the main runtime event source inside a bound instance context.

---

# 10. Why `Bus` is instance-scoped

The underlying subscription state is created via:

- `Instance.state(...)`

This is one of the most important implementation details.

It means subscriptions are not stored globally across the whole server.

They belong to the current instance context.

That keeps runtime event dispatch aligned with request/directory binding.

---

# 11. `Bus.InstanceDisposed`: lifecycle boundary event

`Bus` defines a built-in event:

- `server.instance.disposed`

with payload:

- `{ directory: string }`

This event is special because it marks the end of an instance lifecycle and is also used by SSE stream handling to close instance-bound event subscriptions.

So it is a key lifecycle contract, not just another event.

---

# 12. Why `InstanceDisposed` is defined in the bus layer

Instance disposal affects the validity of every other instance-scoped subscription.

So it makes sense that the bus infrastructure itself knows about this event.

It is a fundamental control-plane boundary for the event system.

---

# 13. `Bus.publish(...)`: more than local dispatch

Publishing an event does three things:

- constructs `{ type, properties }`
- dispatches it to local instance-scoped subscribers
- emits it into `GlobalBus` with the current `Instance.directory`

That third step is extremely important.

It means local instance events automatically become part of the global observability plane.

---

# 14. Why automatic forwarding to `GlobalBus` matters

Without this forwarding step, there would be a large gap between:

- local instance activity
- server-wide visibility

The current design ensures that any event published on `Bus` can also be observed globally with source labeling.

That is what makes `/global/event` useful as a cross-instance observation surface.

---

# 15. What `GlobalBus` actually is

`global.ts` defines:

- `new EventEmitter<{ event: [{ directory?: string; payload: any }] }>()`

So `GlobalBus` is intentionally much simpler than `Bus`.

It does not manage typed publish helpers or per-instance state.

It is a raw server-wide aggregation channel.

---

# 16. Why `GlobalBus` is simpler than `Bus`

`GlobalBus` has a narrower role:

- aggregate globally interesting event payloads with source labels

It is not trying to be the primary typed application event dispatcher.

That is `Bus`’s job.

This separation keeps the architecture clear:

- `Bus` for local typed production/consumption
- `GlobalBus` for broader cross-context observation

---

# 17. The relationship between `Bus` and `GlobalBus`

A useful mental model is:

## 17.1 `Bus`

- where events are authored and consumed within an instance

## 17.2 `GlobalBus`

- where events are aggregated for server-wide visibility and relaying

This is why the two systems coexist rather than one replacing the other.

---

# 18. `subscribe`, `once`, and `subscribeAll`

`Bus` exposes:

- `subscribe(def, callback)`
- `once(def, callback)`
- `subscribeAll(callback)`

These are the main consumption primitives.

They preserve typed usage at the definition level while still supporting wildcard observation for generic consumers like SSE streams.

This is a good balance between precision and flexibility.

---

# 19. Why wildcard subscription is essential

Some consumers do not care about one event type only.

They need:

- every event

Examples include:

- `/event` SSE streams
- diagnostic tooling
- generalized runtime observers

So `subscribeAll(...)` is a necessary capability for making the event bus observable in a generic way.

---

# 20. How instance shutdown triggers wildcard subscribers

When the instance state is disposed, the cleanup function emits a synthetic event to wildcard subscribers:

- type: `server.instance.disposed`
- properties: `{ directory: Instance.directory }`

This is a clever design.

It ensures listeners watching the whole event stream are informed that the stream’s underlying scope has ended.

That is exactly the signal the SSE layer needs.

---

# 21. Logging is built into event publication/subscription

`Bus` logs:

- publishing
- subscribing
- unsubscribing

And `BusEvent` also has its own logger.

This is worth noting because event systems are often hard to debug.

Basic logging at the bus layer helps operational visibility and debugging of event flow.

---

# 22. The type-safety boundary is not perfect

While `BusEvent` definitions are typed, some parts of the bus layer still use:

- `any`
- untyped wildcard callbacks
- `payload: any` in `GlobalBus`

This is a pragmatic compromise.

The system preserves strong typing at event definition and typed subscription sites, while allowing the aggregation layer and generic observers to remain flexible.

That tradeoff is understandable, though it means some guarantees weaken as you move outward from the definition site.

---

# 23. Why this tradeoff is probably intentional

A globally aggregated event stream must often carry many different event shapes.

Trying to keep every generic aggregation point perfectly typed can add significant complexity.

OpenCode instead appears to optimize for:

- typed event definitions
- typed direct subscriptions
- flexible aggregated observation

That is a reasonable division of responsibility.

---

# 24. Why `payloads()` is still valuable despite looser `GlobalBus` typing

Even though `GlobalBus` stores `payload: any`, the existence of `BusEvent.payloads()` means the system still has a canonical typed union of event payloads available.

So the looser emitter typing does not mean event contracts are fundamentally untyped.

It just means the global transport channel is more permissive than the underlying definition system.

---

# 25. How this layer supports SSE streams

The SSE route layers rely heavily on these primitives:

- `/event` uses `Bus.subscribeAll(...)`
- `/global/event` uses `GlobalBus.on("event", ...)`
- workspace sync re-emits into `GlobalBus`

So the whole streaming observability story depends directly on the separation between:

- typed local bus
- aggregated global bus

This layer is therefore central to live runtime observability.

---

# 26. A representative event lifecycle

A typical lifecycle looks like this:

## 26.1 A subsystem defines an event

- `BusEvent.define("some.type", schema)`

## 26.2 A subsystem publishes the event locally

- `Bus.publish(def, properties)`

## 26.3 Local instance subscribers receive it

- via `subscribe(...)` or `subscribeAll(...)`

## 26.4 The event is also forwarded globally

- `GlobalBus.emit("event", { directory: Instance.directory, payload })`

## 26.5 Global observers or SSE routes can consume it

- through `/global/event`

This captures the main flow from definition to observability.

---

# 27. Why the event-definition layer matters beyond observability

Because definitions are centrally registered and schema-backed, they also shape:

- external contract thinking
- documentation quality
- possible future schema export or tooling
- consistency of event naming across the codebase

This makes `BusEvent` an architectural policy layer, not just a helper.

---

# 28. Key design principles behind this module

## 28.1 Events should be defined as named typed contracts, not just emitted as arbitrary strings

So `BusEvent.define(...)` couples type names with zod schemas.

## 28.2 Instance-local dispatch and global observability are related but distinct concerns

So OpenCode uses both `Bus` and `GlobalBus`.

## 28.3 Runtime observability should not require every subsystem to implement its own global forwarding logic

So `Bus.publish(...)` automatically emits to `GlobalBus`.

## 28.4 Generic observation layers may need more flexibility than direct typed producers/consumers

So the aggregation layer remains looser even while event definitions stay strongly structured.

---

# 29. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/bus/bus-event.ts`
2. `packages/opencode/src/bus/index.ts`
3. `packages/opencode/src/bus/global.ts`
4. any subsystem defining events through `BusEvent.define(...)`
5. the SSE stream routes consuming `Bus` and `GlobalBus`

Focus on these functions and concepts:

- `BusEvent.define()`
- `BusEvent.payloads()`
- `Bus.publish()`
- `Bus.subscribe()`
- `Bus.subscribeAll()`
- `Bus.InstanceDisposed`
- `GlobalBus.emit/on/off`

---

# 30. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Where is `BusEvent.payloads()` currently consumed, and should it play a larger role in stream/schema contracts?
- **Question 2**: Should `GlobalBus` eventually carry a stronger typed event envelope instead of `payload: any`?
- **Question 3**: Are there important event producers that bypass `Bus.publish(...)` and therefore weaken the uniform observability model?
- **Question 4**: How should event naming and schema evolution be managed as more subsystems rely on these contracts externally?
- **Question 5**: Should event definitions include richer metadata such as category, stability, or scope tags in addition to type and payload schema?
- **Question 6**: How can operators best inspect the event registry and active subscriptions during debugging?
- **Question 7**: Are there performance or memory concerns with instance-scoped subscription state under heavy event traffic?
- **Question 8**: How should wildcard observation and typed direct subscription coexist if the event vocabulary grows substantially?

---

# 31. Summary

The `bus_event_definition_and_runtime_observability` layer is the foundation of OpenCode’s event-driven architecture:

- `BusEvent.define(...)` creates schema-backed named event contracts
- `Bus` handles instance-scoped dispatch and typed/local subscription behavior
- `GlobalBus` aggregates events into a simpler server-wide observation channel
- `Bus.publish(...)` bridges local typed events into global observability automatically

So this module is the core infrastructure that turns internal runtime activity into structured, observable event contracts across the system.
