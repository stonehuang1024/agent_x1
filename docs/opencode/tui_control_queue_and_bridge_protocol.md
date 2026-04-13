# TUI Control Queue / Bridge Protocol

---

# 1. Module Purpose

This document explains the request/response bridge embedded inside `server/routes/tui.ts`, focusing on `callTui`, `TuiControlRoutes`, and the `AsyncQueue` transport used to connect HTTP callers with a TUI-side control consumer.

The key questions are:

- Why does the TUI module need a queue-based bridge in addition to bus-driven event routes?
- How do `callTui`, `/tui/control/next`, and `/tui/control/response` work together?
- What protocol semantics emerge from the pair of `AsyncQueue` instances?
- What are the strengths and limitations of this bridge design?
- How does this fit into OpenCode’s broader frontend integration architecture?

Primary source files:

- `packages/opencode/src/server/routes/tui.ts`
- `packages/opencode/src/util/queue.ts`

This layer is OpenCode’s **TUI request/response bridge protocol over in-process async queues**.

---

# 2. Why this layer matters

Most of the `/tui` namespace is event-driven and fire-and-forget.

But some integrations need a stronger pattern:

- an HTTP caller submits a request
- a TUI-side consumer receives it
- the TUI-side consumer computes or gathers a result
- the original caller waits for that result

That is exactly what the queue bridge is for.

It adds a synchronous-response interaction model on top of the TUI integration surface.

---

# 3. The bridge has only three moving parts

The whole mechanism is built from:

- `request = new AsyncQueue<TuiRequest>()`
- `response = new AsyncQueue<any>()`
- a small set of route/helper functions

This simplicity is striking.

The protocol is tiny, but still expressive enough to act like a lightweight RPC channel.

---

# 4. `TuiRequest`: the request envelope

The queued request shape is:

- `path: string`
- `body: any`

This is a deliberately generic envelope.

It tells the TUI-side consumer:

- which logical HTTP path originated the request
- what payload was submitted

This means the bridge is route-aware without hardcoding one schema per bridged operation.

---

# 5. Why `path` is included in the queued request

If the bridge only carried an anonymous body, the TUI-side processor would need some other way to know how to interpret the payload.

Including `path` solves that elegantly.

It lets one queue carry requests for multiple logical operations while still preserving dispatch context.

That is a very practical design.

---

# 6. `callTui(ctx)`: enqueue request, await response

`callTui` does four things:

- parse the incoming JSON body
- push `{ path, body }` onto `request`
- wait on `response.next()`
- return that awaited result

This is the core bridge primitive.

It turns an HTTP request into a queued unit of work for the TUI-side consumer and blocks until a response arrives.

---

# 7. Why `callTui` is different from event publication

Event publication says:

- “something happened”

and does not necessarily require a reply.

`callTui` says:

- “please handle this specific request and give me a result”

That is a much stronger contract.

It is request/response, not broadcast signaling.

This distinction is essential for understanding why the TUI module contains both styles.

---

# 8. `AsyncQueue<T>`: the underlying transport primitive

The queue implementation is extremely small:

- `queue: T[]`
- `resolvers: ((value: T) => void)[]`
- `push(item)`
- `next()`
- async iterator support

This is a classic producer-consumer primitive.

If a consumer is already waiting, `push` resolves it immediately.

Otherwise the item is buffered.

If no item is buffered, `next()` waits until a producer arrives.

---

# 9. Why `AsyncQueue` is a good fit here

The TUI bridge needs exactly this behavior:

- incoming requests may arrive before a TUI control consumer polls
- or the consumer may already be waiting for the next item
- likewise for responses

`AsyncQueue` supports both buffered and waiting-consumer modes naturally.

That makes it a clean fit for a local bridge without extra dependencies.

---

# 10. `GET /tui/control/next`: the pull side of the protocol

The control route:

- waits on `request.next()`
- returns the next queued `TuiRequest`

This means the TUI-side control process operates as a polling or long-polling consumer.

It asks the server:

- give me the next pending TUI request to handle

That is the consumer-facing half of the bridge.

---

# 11. Why pull-based consumption is practical for a TUI

A terminal UI process is often easiest to model as an active worker that repeatedly asks for the next task.

That avoids having to stand up a separate push transport from server to terminal.

The queue plus `GET /next` route gives a simple and reliable “pull the next job” protocol.

---

# 12. `POST /tui/control/response`: complete the waiting request

The response route:

- accepts any JSON body
- pushes it onto `response`
- returns `true`

This is the producer side for the response queue.

Whichever HTTP call is waiting inside `callTui(...)` receives the next value pushed here.

That completes the request/response handshake.

---

# 13. The effective protocol flow

The bridge protocol is:

## 13.1 HTTP caller invokes a TUI-backed action

- route uses `callTui(...)`

## 13.2 Server enqueues `{ path, body }`

- into `request`

## 13.3 TUI-side consumer calls `GET /tui/control/next`

- receives the next request

## 13.4 TUI-side consumer handles it

- locally in terminal/UI code

## 13.5 TUI-side consumer calls `POST /tui/control/response`

- sends response payload back

## 13.6 Original `callTui(...)` resumes

- returns the response to the original caller

This is a complete bridge protocol using only queues and two small routes.

---

# 14. Why this is effectively a local RPC channel

Even though it is not named RPC, that is what the pattern is doing.

It provides:

- request envelope
- reply channel
- blocking await semantics
- generic payload transport

The implementation is intentionally lightweight, but the semantics are RPC-like.

---

# 15. Strength: very small and easy to reason about

One of the best qualities of this design is simplicity.

There is no:

- broker
- stream framing protocol
- correlation table
- heavy session management

Instead, there are just two FIFO queues and two endpoints.

For a local TUI integration, that is refreshingly simple.

---

# 16. Limitation: no explicit correlation IDs

The bridge does **not** include:

- request IDs
- response IDs
- per-call correlation metadata

This means correctness depends on strict FIFO pairing between:

- queued requests
- returned responses

That is workable if requests are processed serially and responses are posted in exact order.

But it is an important constraint.

---

# 17. Why FIFO pairing is the hidden protocol invariant

Because `callTui(...)` just waits on:

- `response.next()`

and the response route just pushes the next body into a shared queue, the protocol assumes:

- the next response belongs to the oldest waiting caller

That is the central invariant of the bridge.

If concurrent or reordered handling occurs, results could be mismatched.

This is the most important protocol limitation to understand.

---

# 18. Limitation: no timeout or cancellation logic in the bridge itself

`AsyncQueue.next()` waits indefinitely until something arrives.

So `callTui(...)` can wait forever unless the surrounding HTTP/request lifecycle aborts elsewhere.

The bridge itself does not implement:

- timeout
- cancellation cleanup
- dead consumer detection

That simplicity is useful, but it leaves resilience questions to higher layers.

---

# 19. Limitation: `body` and response payloads are untyped at the bridge level

`TuiRequest.body` is `z.any()` and the response queue stores `any`.

This gives the protocol maximum flexibility.

But it also means bridge-level validation is weak compared with the more strongly typed event routes.

So this mechanism is better viewed as a generic transport shim than as a highly constrained public contract.

---

# 20. Why the protocol may still be the right tradeoff

For a local terminal integration, the design probably optimizes for:

- low complexity
- minimal ceremony
- easy debugging
- easy embedding

Those are reasonable priorities.

A heavier correlated-RPC protocol might be more robust, but also more complex than the current use cases require.

---

# 21. Relationship to the rest of `/tui`

The queue bridge is not the dominant TUI interaction mode.

Most public `/tui` routes are event-based.

So the queue bridge likely exists for a narrower set of interactions where the server needs:

- a result back from terminal-side handling

That makes it a complementary mechanism, not the main TUI transport.

---

# 22. Async iterator support in `AsyncQueue`

`AsyncQueue` implements `AsyncIterable<T>` by repeatedly yielding:

- `await this.next()`

This is a nice touch because it allows queue consumers to be written as async loops if desired.

Even though the TUI control route does not use that form directly, it makes the primitive more generally useful.

---

# 23. Why `TuiControlRoutes` being nested under `/control` matters

The bridge endpoints live under:

- `/tui/control/next`
- `/tui/control/response`

That naming is good because it distinguishes:

- control-channel transport

from:

- higher-level TUI action routes like `/append-prompt` or `/show-toast`

This helps keep the route namespace semantically organized.

---

# 24. What this bridge reveals about OpenCode’s frontend philosophy

OpenCode is willing to expose frontend integration in more than one style:

- typed event publication for many UI actions
- queue-based request/response bridging for cases needing a reply

That flexibility is notable.

It suggests the terminal UI is treated as a programmable peer in the system rather than just a passive renderer.

---

# 25. A representative bridge interaction

A realistic bridge interaction looks like this:

## 25.1 Some HTTP route needs terminal-side handling

- it calls `callTui(ctx)`

## 25.2 `callTui` enqueues `{ path, body }`

- original caller is now blocked on `response.next()`

## 25.3 TUI-side process long-polls `/tui/control/next`

- receives the queued request

## 25.4 TUI-side logic handles the request locally

- maybe showing UI, gathering input, or computing a response

## 25.5 TUI-side process posts the result to `/tui/control/response`

- response queue resolves the waiting caller

This is the entire bridge protocol in action.

---

# 26. Key design principles behind this module

## 26.1 Not all frontend integration should be reduced to fire-and-forget events

So the TUI layer includes a request/response bridge in addition to event routes.

## 26.2 A small FIFO queue protocol can be sufficient for local frontend coordination

So `AsyncQueue` is used instead of a more complex RPC system.

## 26.3 Bridge payloads should carry dispatch context as well as raw data

So `TuiRequest` includes both `path` and `body`.

## 26.4 Simplicity can be worth protocol tradeoffs when integration is local and controlled

So the bridge omits correlation IDs, retries, and timeouts in favor of minimalism.

---

# 27. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/routes/tui.ts`
2. `packages/opencode/src/util/queue.ts`
3. any TUI consumer code that calls `/tui/control/next` and `/response`

Focus on these functions and concepts:

- `callTui()`
- `TuiRequest`
- `TuiControlRoutes`
- `AsyncQueue.push()`
- `AsyncQueue.next()`
- FIFO response pairing
- lack of correlation IDs

---

# 28. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Which routes or consumers actually use `callTui(...)` today, and for what interaction patterns?
- **Question 2**: Is the bridge intentionally single-flight/FIFO, or will it need request IDs once concurrency increases?
- **Question 3**: Should `callTui(...)` gain timeout or abort semantics to avoid indefinite waits?
- **Question 4**: How should the bridge behave if the TUI consumer crashes or disconnects while requests are pending?
- **Question 5**: Are `z.any()` request and response payloads acceptable long-term, or should some bridged operations become strongly typed?
- **Question 6**: Is the queue bridge intended only for local trusted environments, or should it be hardened for broader deployments?
- **Question 7**: Could the async-iterator form of `AsyncQueue` support cleaner TUI control workers in the future?
- **Question 8**: Should the control bridge eventually emit diagnostics or events so operators can observe pending or stuck TUI requests?

---

# 29. Summary

The `tui_control_queue_and_bridge_protocol` layer provides a lightweight request/response channel between HTTP callers and a TUI-side control consumer:

- `callTui(...)` enqueues a `{ path, body }` request and waits for the next response
- `/tui/control/next` lets the TUI pull pending work
- `/tui/control/response` completes the waiting call by pushing a response back
- the whole design is built on a tiny `AsyncQueue` FIFO primitive

So this module is a minimalist local RPC bridge for the terminal UI, complementing the broader event-driven TUI route surface with a path for interactions that need an explicit reply.
