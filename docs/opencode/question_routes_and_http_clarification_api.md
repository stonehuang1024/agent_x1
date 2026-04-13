# Question Routes / HTTP Clarification API

---

# 1. Module Purpose

This document explains the dedicated HTTP route surface for OpenCode’s question and clarification workflow.

The key questions are:

- Why does OpenCode expose questions through a standalone `/question` API namespace?
- How do pending question requests become remotely observable resources?
- How do HTTP replies and rejections reconnect to suspended runtime execution?
- How is the question HTTP surface different from the permission HTTP surface?
- Why is this API important for non-embedded clients and multi-surface integrations?

Primary source files:

- `packages/opencode/src/server/routes/question.ts`
- `packages/opencode/src/question/index.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/tool/question.ts`
- `packages/opencode/src/tool/plan.ts`

This layer is OpenCode’s **HTTP-facing user-clarification control surface**.

---

# 2. Why questions deserve their own route namespace

Questions in OpenCode are not just assistant text asking for clarification.

They are runtime interrupt points that suspend execution until a user:

- answers one or more structured questions
- or explicitly dismisses them

That makes questions more like control-plane resources than plain chat messages.

A dedicated `/question` namespace is therefore the correct abstraction.

---

# 3. The question route surface is intentionally minimal

`server/routes/question.ts` exposes only three routes:

- `GET /question/`
- `POST /question/:requestID/reply`
- `POST /question/:requestID/reject`

That compact surface is telling.

The route layer is not trying to contain business logic.

It only exposes the minimal remote operations needed to work with suspended question requests:

- list them
- answer them
- reject them

That is good control-plane design.

---

# 4. `question.list`: pending questions are first-class runtime resources

`GET /question/` returns:

- `Question.Request[]`

This means pending clarification requests are durable enough in runtime state to be queried independently of the UI that triggered them.

That is important for:

- IDE integrations
- web or desktop frontends
- automation harnesses
- reconnecting clients that missed live events

The clarification workflow is therefore externally observable, not buried in a single client implementation.

---

# 5. What a `Question.Request` contains

From `question/index.ts`, a request contains:

- `id`
- `sessionID`
- `questions: Question.Info[]`
- optional tool linkage `{ messageID, callID }`

Each `Question.Info` can include:

- `question`
- `header`
- `options`
- `multiple?`
- `custom?`

This is a rich payload.

It gives remote clients enough structure to render a proper question UI instead of guessing from plain text.

---

# 6. Why tool linkage is included in question requests

If a question originated from a tool call, the request can carry:

- `messageID`
- `callID`

This is important because a client may want to render the question in the context of:

- a specific tool invocation
- a specific assistant turn
- a particular pending workflow step

So the question API is not only session-aware.

It can also be tool-call-aware.

---

# 7. `question.reply`: answering a pending question

`POST /question/:requestID/reply` validates:

- `requestID`
- `Question.Reply`

and forwards to:

- `Question.reply({ requestID, answers })`

This is the route that turns user clarification back into resumed execution.

Like the permission route, it is intentionally thin because the semantics live in the runtime module, not the transport edge.

---

# 8. `Question.Reply` shape and why it matters

A reply is:

- `answers: string[][]`

This means:

- each question maps to an array of selected labels
- the whole request returns one answer array per question

That unified shape supports:

- single-choice questions
- multiple-choice questions
- batched multi-question requests

The HTTP route preserves this structure directly instead of flattening it into ad hoc strings.

---

# 9. `question.reject`: dismissal is a formal API operation

`POST /question/:requestID/reject` maps to:

- `Question.reject(requestID)`

This is a very important distinction from plain unanswered questions.

A rejection is not “no answer yet”.

It is a deliberate runtime decision that the clarification prompt should be dismissed.

That is why it deserves its own route rather than overloading the reply shape.

---

# 10. Why reject is separate from reply-with-empty-answers

If reject were encoded as an empty answer array, the runtime could not distinguish between:

- a user intentionally dismissing the question
- a user submitting no choices
- a malformed client response

The explicit reject route avoids that ambiguity entirely.

That is the right API design.

---

# 11. How question state is stored at runtime

`Question` uses `Instance.state(...)` with:

- `pending: Map<QuestionID, PendingEntry>`

So the question HTTP routes expose live instance-scoped question state.

That is why instance/workspace middleware in `server.ts` matters here too.

Without correct scoping, a client would be looking at the wrong pending question set.

---

# 12. `Question.ask(...)`: where HTTP-visible requests come from

Questions are created by runtime code calling:

- `Question.ask(...)`

That can happen through:

- the `question` tool
- planning workflows like `plan_exit`
- potentially other future structured clarification flows

The route layer does not create those questions.

It only exposes and resolves them.

That separation of concerns is clean.

---

# 13. `Question.ask(...)` as a suspended promise boundary

When the runtime calls `Question.ask(...)`, it:

- allocates a `QuestionID`
- stores the pending request
- publishes `question.asked`
- returns a Promise that resolves later through `reply()` or rejects through `reject()`

This means a question request is fundamentally:

- a suspended execution continuation waiting for user input

The HTTP routes are simply one remote mechanism for satisfying that continuation.

---

# 14. Why event streams and HTTP routes complement each other here too

The expected client pattern is similar to permissions:

- observe `question.asked`
- optionally refresh with `GET /question/`
- answer through `POST /question/:requestID/reply`
- or dismiss through `POST /question/:requestID/reject`

This makes question events and question routes complementary:

- events provide liveness
- routes provide authoritative mutation and recovery

---

# 15. `Question.reply(...)`: the real logic center

The route handler is small because `Question.reply(...)` does the real work:

- finds the pending request
- removes it from pending state
- publishes `question.replied`
- resolves the suspended promise with structured answers

That resumed Promise then allows the calling tool or workflow to continue.

This is where HTTP and runtime execution reconnect.

---

# 16. `Question.reject(...)`: rejection semantics

`Question.reject(...)`:

- finds the pending request
- removes it from pending state
- publishes `question.rejected`
- rejects the suspended promise with `Question.RejectedError`

That rejection then propagates up to execution control layers.

So the reject route is not just dismissing UI.

It is feeding a real runtime control signal back into the suspended workflow.

---

# 17. How question rejection affects session execution

In `SessionProcessor`, tool errors caused by:

- `Question.RejectedError`

are treated similarly to rejected permission decisions and may set:

- `blocked = shouldBreak`

That means question rejection can halt execution, not just close a popup.

This is why the HTTP clarification API matters so much.

It sits directly on a runtime stop/continue boundary.

---

# 18. Question routes versus permission routes

The two route namespaces are structurally similar, but semantically different.

## 18.1 Permission routes

- answer whether a capability may be used
- can create policy via `always`
- may carry corrective rejection guidance

## 18.2 Question routes

- provide missing task information or clarification
- return structured answers
- or dismiss the clarification request

So both are interrupt APIs, but they represent different kinds of human input.

That separation is good and necessary.

---

# 19. Why question routes do not expose an `always`-style option

Questions are about gathering information for a specific runtime moment.

Unlike permissions, they do not generally establish durable future policy.

So the API surface does not need concepts like:

- allow once
- allow always

It only needs:

- reply
- reject

That is a clean semantic difference.

---

# 20. Why question replies are batch-oriented

A single request can include multiple questions.

That is why the HTTP reply API is designed for batched answers rather than one-question-per-request semantics.

This is efficient for both runtime and UI because it reduces back-and-forth when the agent already knows what clarification set it needs.

---

# 21. Unknown request behavior is intentionally forgiving

Both `Question.reply(...)` and `Question.reject(...)` log a warning and return if the request is unknown.

This is useful for real clients because retries, race conditions, reconnects, or duplicate submissions can happen.

The API behaves safely rather than crashing on stale request IDs.

---

# 22. Why this API is essential for external clients

Without dedicated question routes, only a bundled UI could conveniently satisfy clarification interrupts.

That would make OpenCode far less reusable.

With the route surface, any client can participate in question workflows, including:

- web UI
- desktop shells
- ACP bridges via separate adapters
- IDE panels
- test harnesses

This makes structured clarification a platform feature rather than a UI-local feature.

---

# 23. A representative question HTTP lifecycle

A typical flow looks like this:

## 23.1 Runtime reaches ambiguity

- tool or workflow calls `Question.ask(...)`

## 23.2 Question becomes pending

- request is stored in instance state
- `question.asked` is published
- request becomes visible via `GET /question/`

## 23.3 Client renders clarification UI

- based on structured question payload

## 23.4 User responds

- `POST /question/:requestID/reply`
- or `POST /question/:requestID/reject`

## 23.5 Suspended execution resumes or aborts

- answer data flows back into the tool/workflow
- or `Question.RejectedError` propagates upward

This is the complete HTTP clarification loop.

---

# 24. Why this is a control-plane API, not a chat API

A normal chat API would treat user clarification as another chat message.

OpenCode does something more structured:

- it surfaces clarification as a pending runtime object
- it provides typed answer and reject operations
- it reconnects those answers to a suspended promise

That is distinctly control-plane behavior.

It prioritizes runtime correctness over conversational looseness.

---

# 25. Key design principles behind this module

## 25.1 Clarification should be modeled as a first-class runtime interrupt

So pending questions are listable and replyable through a dedicated API namespace.

## 25.2 Transport layers should stay thin when runtime semantics are already well-defined

So `server/routes/question.ts` delegates almost entirely to `Question` runtime methods.

## 25.3 Rejection is semantically different from empty input

So there is a dedicated reject route instead of overloading the reply payload.

## 25.4 Structured answers are better than free-form message guessing for suspended workflows

So the API uses `string[][]` answers aligned with the original question schema.

---

# 26. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/routes/question.ts`
2. `packages/opencode/src/question/index.ts`
3. `packages/opencode/src/tool/question.ts`
4. `packages/opencode/src/tool/plan.ts`
5. `packages/opencode/src/session/processor.ts`

Focus on these functions and concepts:

- `QuestionRoutes`
- `Question.ask()`
- `Question.reply()`
- `Question.reject()`
- `question.asked`
- `question.replied`
- `question.rejected`
- `Question.RejectedError`

---

# 27. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: How do current front-end clients render multi-question requests and map `string[][]` answers back into human-readable UI state?
- **Question 2**: Should `question.list` support filtering by session ID for large multi-session clients?
- **Question 3**: How should clients surface the difference between a question reject and a permission reject when both can stop execution?
- **Question 4**: Are there cases where a rejected question should continue execution with default behavior rather than halting it?
- **Question 5**: Should question replies support richer typed answer payloads in the future beyond label arrays?
- **Question 6**: How should stale or duplicate question replies be surfaced to clients beyond current warning-only behavior?
- **Question 7**: Should the server expose question history or only pending questions?
- **Question 8**: How often do workflows use direct `Question.ask(...)` versus the public `question` tool, and does that matter for route consumers?

---

# 28. Summary

The `question_routes_and_http_clarification_api` layer exposes OpenCode’s clarification workflow as a clean HTTP control-plane surface:

- `GET /question/` makes pending clarification requests observable
- `POST /question/:requestID/reply` feeds structured answers back into suspended runtime execution
- `POST /question/:requestID/reject` turns dismissal into an explicit runtime rejection signal
- the route layer stays deliberately thin while the `Question` runtime module owns the real semantics

So this module is not just a convenience API. It is the boundary where user clarification becomes a remotely manageable, structured part of OpenCode’s agent execution model.
