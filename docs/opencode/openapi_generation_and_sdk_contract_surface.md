# OpenAPI Generation / SDK Contract Surface

---

# 1. Module Purpose

This document explains how OpenCode’s HTTP server turns route definitions into a machine-readable API contract that can be served live and generated programmatically.

The key questions are:

- How do `describeRoute`, `validator`, and `resolver` shape the contract surface?
- Why is OpenAPI generation tightly coupled to route implementation in this codebase?
- What is the difference between the live `/doc` endpoint and `Server.openapi()`?
- Why is this contract model important for SDKs and multi-client integrations?
- What does this reveal about OpenCode’s broader API design philosophy?

Primary source files:

- `packages/opencode/src/server/server.ts`
- `packages/opencode/src/server/routes/*.ts`
- `hono-openapi` usage throughout the route layer

This layer is OpenCode’s **code-first API contract and OpenAPI generation surface**.

---

# 2. Why this deserves its own article

OpenCode’s route files are not only route handlers.

They are also the source of truth for:

- input validation
- output schema declaration
- operation naming
- OpenAPI documentation
- downstream SDK generation

That means the contract system is not an optional documentation add-on.

It is built into how routes are authored.

---

# 3. The core contract pattern

Across the server route files, the recurring pattern is:

- `describeRoute(...)`
- `validator(...)`
- `resolver(...)`

Together, these define:

- human-readable route metadata
- typed request validation
- typed response schema exposure

This is a classic code-first API contract design.

---

# 4. `describeRoute(...)`: route meaning and identity

`describeRoute(...)` typically provides:

- `summary`
- `description`
- `operationId`
- `responses`
- sometimes `tags` or `deprecated`

This means each route carries its own semantic identity close to the handler implementation.

That is valuable because API documentation and route behavior evolve together rather than drifting apart.

---

# 5. Why `operationId` matters so much

OpenCode consistently assigns operation IDs like:

- `session.list`
- `permission.reply`
- `question.reject`
- `pty.create`
- `global.config.update`

These are more than labels.

They are the stable machine-facing names that clients, SDK generators, and documentation systems can use to refer to API operations.

A consistent operation ID scheme is a sign of a mature contract surface.

---

# 6. `validator(...)`: runtime validation as contract enforcement

The route layer uses `validator(...)` for:

- query params
- path params
- JSON bodies

backed by `zod` schemas.

This is important because the same schema shape supports both:

- runtime request validation
- OpenAPI schema generation

So the contract is not merely documented.

It is enforced on live traffic.

---

# 7. Why shared validation and documentation matter

If validation and documentation were defined separately, they would inevitably drift.

By using the same zod schema to power both:

- request enforcement
- route docs/spec generation

OpenCode avoids that divergence.

This is one of the strongest advantages of its route architecture.

---

# 8. `resolver(...)`: exposing typed response shapes

`resolver(...)` wraps zod schemas for use in response declarations.

This is how routes say things like:

- response is `Session.Info`
- response is `Pty.Info.array()`
- response is `z.boolean()`
- response is `BusEvent.payloads()`

This matters because the contract model is not input-only.

It also describes what callers should expect back.

---

# 9. Why response typing is especially important here

OpenCode returns many rich resource shapes:

- sessions
- messages and parts
- provider metadata
- config objects
- PTY info
- event payloads
- permission and question requests

If those outputs were undocumented or weakly defined, external clients would have a much harder time integrating safely.

The response schemas make the control plane significantly more consumable.

---

# 10. The server exposes the OpenAPI document live at `/doc`

In `server.ts`, `createApp()` mounts:

- `GET /doc`

through `openAPIRouteHandler(app, ...)`.

This means the running server can serve its own OpenAPI description directly.

That is a strong operational feature because clients and tooling can discover the contract from the live server itself.

---

# 11. Why `/doc` is useful in practice

A live OpenAPI route is useful for:

- SDK generation tooling
- interactive API explorers
- debugging deployed route contracts
- verifying that a running server matches expected capabilities

It reduces dependence on external documentation pipelines.

The server is self-describing at runtime.

---

# 12. `Server.openapi()`: programmatic spec generation

In addition to `/doc`, `server.ts` exports:

- `Server.openapi()`

which calls:

- `generateSpecs(Default(), { documentation: ... })`

This is a second contract surface:

- generate the spec from code without needing to hit a live HTTP endpoint

That is useful for build tooling and SDK pipelines.

---

# 13. Live doc versus programmatic generation

A useful distinction is:

## 13.1 `/doc`

- served from a running app
- runtime-accessible
- useful for live introspection

## 13.2 `Server.openapi()`

- code-driven generation path
- useful for tooling, CI, or SDK build steps
- does not require fetching from a running server

Both are valuable, and together they make the contract surface more flexible.

---

# 14. The route graph itself is the spec source of truth

`Server.openapi()` calls `generateSpecs(Default(), ...)`, and `Default()` is just:

- a lazily created `createApp({})`

This means the generated spec comes from the same composed route graph that handles real traffic.

That is exactly what you want in a code-first API system.

The spec is derived from the real server, not from a parallel hand-maintained document.

---

# 15. Why the code comments mention type recursion

`Server.openapi()` includes a comment about:

- breaking excessive type recursion from long route chains

That is a practical hint about the complexity of a large strongly typed route graph.

OpenCode’s API surface is big enough that the type machinery itself becomes nontrivial.

This is the cost of a deeply typed, code-first contract system.

---

# 16. Route contracts extend beyond the route namespaces

The OpenAPI surface includes not only namespaced routes like:

- `/session`
- `/provider`
- `/permission`
- `/question`
- `/pty`

but also top-level routes like:

- `/path`
- `/vcs`
- `/command`
- `/agent`
- `/skill`
- `/event`
- `/instance/dispose`

So the contract model covers the entire server surface, not just resource subtrees.

---

# 17. Why the route authoring style scales well

The route style used across the server is repetitive in a good way.

Each route tends to declare, in one place:

- what it is called
- what it expects
- what it returns
- how it behaves

That scales much better than architectures where docs, schemas, handlers, and client contracts are scattered across separate layers.

---

# 18. This contract model supports SDK generation naturally

Although the SDK build pipeline is not fully explored in this article, the route architecture makes SDK generation a natural outcome because:

- route schemas are explicit
- response types are declared
- operation IDs are stable
- the whole app can generate OpenAPI specs programmatically

That is exactly the kind of structure SDK pipelines want.

---

# 19. Why this matters especially for OpenCode

OpenCode is not serving only one built-in frontend.

Its API is consumed by:

- local clients
- IDE integrations
- ACP adapters indirectly through SDK surfaces
- automation and tooling
- potentially generated SDK consumers

That means the route contract must be durable and machine-readable.

A loose, undocumented API surface would be a poor fit for this ecosystem.

---

# 20. Schema reuse is a big strength of the design

Routes reuse schemas and types from core runtime modules such as:

- `Session.Info`
- `MessageV2.Part`
- `PermissionNext.Request`
- `Question.Request`
- `Pty.Info`
- `Config.Info`

This is important because the API contract stays close to the core runtime data model.

That reduces duplication and helps keep the transport surface aligned with actual runtime state shapes.

---

# 21. Why this is better than hand-written DTO layers in this codebase

In some systems, hand-written DTOs can help isolate internals from API contracts.

But OpenCode’s architecture benefits strongly from direct schema reuse because:

- many internal data types are already designed to be externally meaningful
- the system is highly event- and resource-oriented
- duplication would increase drift risk

The current approach is a good fit for the project’s style and structure.

---

# 22. Contract quality also depends on route descriptions

The summaries and descriptions throughout the route files are not fluff.

They are part of the machine-visible documentation surface.

In a code-first OpenAPI system, good route descriptions materially improve:

- discoverability
- SDK docs
- human operator understanding
- API explorer usability

So the descriptive metadata is part of the real product surface.

---

# 23. A representative contract generation path

A typical path looks like this:

## 23.1 Route author defines a Hono route

- with `describeRoute`, `validator`, `resolver`

## 23.2 The route becomes part of `createApp()`

- composed into the full server graph

## 23.3 OpenAPI generation consumes the composed app

- via `/doc`
- or via `Server.openapi()`

## 23.4 SDK/tooling consumes the generated spec

- for typed clients or documentation

This is a clean, closed contract-production loop.

---

# 24. Why the contract layer is part of the platform story

A platform is easier to adopt when its API surface is:

- discoverable
- typed
- documented
- stable enough to automate against

OpenCode’s route contract model contributes directly to that.

It helps turn the server from “internal endpoints” into a reusable developer-facing surface.

---

# 25. Key design principles behind this module

## 25.1 Route implementation and route contract should come from the same source of truth

So OpenCode uses code-first route metadata and schema binding.

## 25.2 Validation is part of the contract, not separate from it

So zod validators also shape the documented API surface.

## 25.3 Rich resource-oriented APIs need typed response schemas

So routes expose explicit response shapes through `resolver(...)`.

## 25.4 A mature API surface should be both live-discoverable and programmatically generatable

So OpenCode supports both `/doc` and `Server.openapi()`.

---

# 26. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/server.ts`
2. several route files under `packages/opencode/src/server/routes/`
3. any core schemas reused by those routes

Focus on these functions and concepts:

- `describeRoute()`
- `validator()`
- `resolver()`
- `openAPIRouteHandler()`
- `generateSpecs()`
- `operationId`
- schema reuse from runtime modules

---

# 27. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: How exactly are the generated SDKs built from this OpenAPI surface, and which parts of the contract are considered public/stable?
- **Question 2**: Are there any route schemas today that expose internal details too directly and might deserve a more explicit API-facing abstraction?
- **Question 3**: How should deprecated routes and operations be surfaced more clearly in generated SDKs and docs?
- **Question 4**: Are there any parts of the event-stream contracts that are difficult to model accurately in OpenAPI today?
- **Question 5**: Should OpenCode expose versioned OpenAPI documents if the route surface evolves significantly over time?
- **Question 6**: Which generated clients exist today, and how much custom post-processing do they require beyond raw spec generation?
- **Question 7**: Do all route descriptions maintain the same quality and precision, or are some namespaces under-documented relative to others?
- **Question 8**: How should future experimental routes be represented in the contract so client generators can handle them safely?

---

# 28. Summary

The `openapi_generation_and_sdk_contract_surface` layer shows that OpenCode’s HTTP API is designed as a real machine-readable platform contract:

- route definitions carry their own documentation, validation, and response schemas
- the full server graph can expose a live OpenAPI document at `/doc`
- the same graph can also generate specs programmatically through `Server.openapi()`
- this architecture makes SDK generation and multi-client integration much more reliable

So this module is not just documentation plumbing. It is the contract architecture that turns OpenCode’s route graph into a typed, discoverable, reusable API surface.
