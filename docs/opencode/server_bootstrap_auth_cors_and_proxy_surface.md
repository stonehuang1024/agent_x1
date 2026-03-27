# Server Bootstrap / Auth / CORS / Proxy Surface

---

# 1. Module Purpose

This document explains the top-level server bootstrap behavior in `server.ts`, focusing on authentication, request logging, CORS, OpenAPI exposure, Bun server startup, and the catch-all proxy surface.

The key questions are:

- How does `Server.createApp()` shape the HTTP surface before feature routes are even mounted?
- Why are auth, CORS, and logging handled centrally?
- How does OpenCode expose its OpenAPI document and route contract model?
- What does the catch-all proxy route imply about the relationship between API server and hosted UI?
- How does `Server.listen()` turn the Hono app into a long-lived Bun server process?

Primary source files:

- `packages/opencode/src/server/server.ts`
- `packages/opencode/src/server/error.ts`
- `packages/opencode/src/server/routes/global.ts`

This layer is OpenCode’s **top-level HTTP server bootstrap and edge-behavior surface**.

---

# 2. Why this layer deserves separate analysis

Most documentation naturally focuses on route namespaces like:

- session
- permission
- question
- PTY

But those routes all depend on server-wide edge behavior decided before route logic runs.

That includes:

- error normalization at the HTTP boundary
- auth enforcement
- request logging
- CORS policy
- instance/workspace context binding
- OpenAPI document exposure
- fallback proxy behavior
- Bun server startup and shutdown behavior

So `server.ts` is not just wiring. It defines the outer semantics of the entire API surface.

---

# 3. `Server.createApp()` is the true HTTP entrypoint orchestrator

`createApp(opts)` builds and returns the `Hono` application.

It is responsible for:

- global error handling
- auth middleware
- logging middleware
- CORS middleware
- workspace/instance scoping
- route registration
- event SSE exposure
- fallback proxying

This is the composition root of the server-side control plane.

---

# 4. Global error handling at the edge

The app installs `.onError(...)` very early.

It maps:

- `NotFoundError` -> `404`
- `Provider.ModelNotFoundError` -> `400`
- worktree-related named errors -> `400`
- other `NamedError`s -> `500`
- generic exceptions -> wrapped `NamedError.Unknown` with `500`

This is a strong design choice.

The HTTP edge preserves OpenCode’s typed internal error model instead of collapsing everything into vague status text.

---

# 5. Why edge error mapping matters

Without central error mapping, each route would have to:

- guess status codes
- serialize errors manually
- risk inconsistent error payload shapes

By centralizing this in `createApp()`, OpenCode ensures a consistent HTTP error contract across the whole control plane.

That is especially important for SDKs and generated clients.

---

# 6. Basic auth at the server boundary

Before most route logic, the app optionally installs HTTP basic auth based on:

- `OPENCODE_SERVER_PASSWORD`
- optional `OPENCODE_SERVER_USERNAME`

If password is absent, no auth challenge is installed.

If present, all non-OPTIONS requests must pass the basic auth check.

This is a deliberately simple server-edge security model.

---

# 7. Why auth is applied before route logic

Server-level auth is not tied to one route family.

If a deployment wants the API protected, it wants:

- the whole surface protected

So applying auth early is the right design.

It avoids duplicating route-level auth logic and keeps the security story simple for a local or semi-trusted deployment model.

---

# 8. Why OPTIONS requests bypass auth

The auth middleware explicitly lets:

- `OPTIONS`

pass through.

This is necessary for CORS preflight behavior.

Browsers sending authorization headers will often preflight first, and blocking preflight at the auth layer would break legitimate cross-origin clients.

So this exemption is practical and correct.

---

# 9. Request logging middleware

The server logs:

- request method
- request path
- request timing

It also avoids noisy self-recursion by special-casing:

- `/log`

This means the HTTP edge is instrumented centrally rather than expecting every route to log itself.

That produces more uniform observability.

---

# 10. Why `/log` is treated specially

The server exposes a log-ingest endpoint:

- `POST /log`

If the server logged that route like normal traffic and clients themselves used it heavily, logs could become noisy or recursive.

Skipping normal logging there is a pragmatic edge-case decision.

It keeps operational logging cleaner.

---

# 11. CORS policy is explicit and intentionally selective

The server allows origins such as:

- `http://localhost:*`
- `http://127.0.0.1:*`
- Tauri local origins
- `https://*.opencode.ai`
- explicit extra origins from `opts.cors`

Everything else is rejected by omission.

This is not a public-any-origin API stance.

It is a controlled integration policy aimed at:

- local development
- desktop shells
- first-party hosted surfaces
- explicitly allowed custom clients

---

# 12. Why central CORS matters for this server

OpenCode is consumed by different client types:

- local browser-based tools
- desktop shells
- IDE extensions
- potentially hosted frontends

CORS behavior must be coherent across the whole control plane.

Centralizing it avoids route-by-route inconsistency and makes the server easier to deploy correctly.

---

# 13. OpenAPI document exposure in the live server

`createApp()` mounts:

- `GET /doc`

via `openAPIRouteHandler(app, ...)`

This exposes the route contract surface as a live OpenAPI document.

That is an important platform feature because it makes the server self-describing to:

- SDK generation tools
- interactive documentation clients
- integrators
- automation systems

---

# 14. Why OpenAPI exposure belongs at bootstrap level

The OpenAPI document depends on the whole composed route graph.

So it naturally belongs at the top-level server composition layer rather than inside any one route namespace.

This reflects the fact that OpenAPI is describing:

- the entire server surface

not one subsystem.

---

# 15. Route registration order matters

After validation middleware for common query params, the server mounts:

- `/project`
- `/pty`
- `/config`
- `/experimental`
- `/session`
- `/permission`
- `/question`
- `/provider`
- `/`
- `/mcp`
- `/tui`

and also exposes several top-level routes like:

- `/path`
- `/vcs`
- `/command`
- `/agent`
- `/skill`
- `/lsp`
- `/formatter`
- `/event`
- `/instance/dispose`

This route composition shows the server is a combined API host for both:

- core runtime control
- discovery and introspection

---

# 16. The catch-all proxy route is especially revealing

At the end of the route chain, the server installs:

- `.all("/*", ...)`

which proxies unmatched requests to:

- `https://app.opencode.ai`

and then sets a strict CSP on the response.

This is a very important architectural clue.

It means the local server is not just an API endpoint host.

It can also act as a UI-serving or UI-bridging entry surface.

---

# 17. Why the proxy route sits last

Because it catches all unmatched paths, it must come after all explicit control-plane routes.

That ordering guarantees:

- real API paths are handled locally
- everything else can be treated as hosted-app surface or asset traffic

This is a standard but critical composition rule.

---

# 18. Why the proxy rewrites the `host` header

The proxy forwards requests to `app.opencode.ai` and sets:

- `host: app.opencode.ai`

This is important because many upstream systems depend on host for:

- routing
- CSP
- CDN behavior
- correct asset or application responses

So this is not incidental detail.

It is part of making the fallback proxy behave like a real upstream request.

---

# 19. CSP on proxied responses

After proxying, the server sets a restrictive:

- `Content-Security-Policy`

This indicates the proxy surface is not blindly passing upstream responses through.

It is imposing local security constraints on the proxied UI/application surface.

That is a sign of careful edge behavior.

---

# 20. Why a local API server would proxy a hosted UI at all

A likely interpretation is that OpenCode wants:

- one local entrypoint for both API and app experience

That allows clients to interact with:

- local API routes for runtime control
- hosted UI assets or app shell for frontend experience

through a unified local server boundary.

That is a practical product integration pattern.

---

# 21. `Server.openapi()`: offline spec generation surface

Beyond `/doc`, `server.ts` also exposes:

- `Server.openapi()`

which calls:

- `generateSpecs(Default(), ... )`

This is important because the codebase can generate OpenAPI specs programmatically, not just serve them at runtime.

That is useful for:

- SDK generation
- CI tooling
- offline contract inspection

---

# 22. `Server.Default` and laziness

The namespace defines:

- `Default = lazy(() => createApp({}))`

This suggests the default server app is constructed lazily and reused where needed, such as during spec generation.

That is a small but useful composition optimization.

It avoids eager global app construction while still making a canonical app definition available.

---

# 23. `Server.listen(...)`: Bun server bootstrap

`listen(opts)` turns the Hono app into a Bun server by calling:

- `Bun.serve(...)`

with:

- `fetch: app.fetch`
- `websocket: websocket`
- `idleTimeout: 0`
- selected `hostname`
- chosen `port`

This is the final bootstrap step that converts the route graph into a long-lived network service.

---

# 24. Why `idleTimeout: 0` matters

This likely reflects the server’s need to support:

- long-lived SSE connections
- long-lived WebSocket PTY sessions
- potentially long-running requests

An aggressive idle timeout would be hostile to the rest of OpenCode’s runtime model.

So disabling idle timeout at the Bun server layer is a coherent choice.

---

# 25. Port selection behavior

If `opts.port === 0`, the code tries:

- `4096`
- then `0`

Otherwise it tries the explicit port.

If serving fails, it throws.

This is a pragmatic startup policy:

- prefer a predictable port when auto-selecting
- but still allow ephemeral fallback

That may be especially useful for local tooling and desktop integration.

---

# 26. mDNS publication is conditional and network-aware

If enabled, `listen(...)` may publish via:

- `MDNS.publish(...)`

but only when the hostname is not loopback.

If mDNS was requested on loopback, the server logs a warning and skips publication.

This is a thoughtful network-edge detail.

It avoids advertising an unusable loopback-only address through mDNS.

---

# 27. Why `server.stop` is wrapped

After startup, `listen(...)` wraps `server.stop` so that it can:

- unpublish mDNS first when needed
- then call the original stop logic

This is good lifecycle hygiene.

It ensures network advertisement state is cleaned up when the server shuts down.

---

# 28. Why this layer is more than bootstrap glue

At first glance, auth, CORS, proxying, and startup may look like generic server code.

But in OpenCode they materially shape the platform surface:

- who can reach the control plane
- which origins can call it
- how clients discover contracts
- how local API and hosted UI coexist
- how long-lived connections remain viable
- how the server announces itself on the network

That makes this a core architecture layer, not incidental infrastructure.

---

# 29. A representative top-level request path

A typical request may flow like this:

## 29.1 Edge checks happen first

- auth
- logging
- CORS

## 29.2 Context binding happens next

- workspace and instance are resolved

## 29.3 Explicit API route may handle the request

- session / permission / question / PTY / etc.

## 29.4 Otherwise the proxy surface may handle it

- forwarded to hosted app endpoint

This shows `server.ts` truly defines the outer shape of all HTTP behavior.

---

# 30. Key design principles behind this module

## 30.1 Cross-cutting edge behavior should be centralized

So auth, logging, CORS, and error handling live in `createApp()`.

## 30.2 The server should be self-describing

So OpenAPI is exposed both live (`/doc`) and programmatically (`Server.openapi()`).

## 30.3 Long-lived runtime transports must shape server bootstrap choices

So idle timeout is disabled and websocket/SSE support is first-class.

## 30.4 The local server can be both API host and app-facing entrypoint

So unmatched requests are proxied to the hosted app surface with explicit CSP handling.

---

# 31. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/server.ts`
2. `packages/opencode/src/server/routes/global.ts`
3. `packages/opencode/src/server/error.ts`
4. `packages/opencode/src/config/config.ts`

Focus on these functions and concepts:

- `Server.createApp()`
- `.onError(...)`
- auth middleware
- CORS middleware
- `/doc`
- `.all("/*")` proxy route
- `Server.openapi()`
- `Server.listen()`
- mDNS behavior

---

# 32. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: How do the generated SDKs and client tooling actually consume `/doc` versus `Server.openapi()` today?
- **Question 2**: Should the proxy route expose more diagnostics when upstream `app.opencode.ai` is unavailable?
- **Question 3**: How should auth and CORS evolve if OpenCode is used in less-trusted or more remote deployment environments?
- **Question 4**: Are there any subtle interactions between CSP rewriting and the hosted app’s asset or websocket behavior?
- **Question 5**: Should startup port selection be configurable in a more policy-driven way for desktop and cloud environments?
- **Question 6**: How are long-lived SSE and websocket connections affected by Bun server behavior under load?
- **Question 7**: Should the server expose a more explicit readiness endpoint beyond simple health/version?
- **Question 8**: How tightly coupled should the local API server remain to the hosted app proxy surface over time?

---

# 33. Summary

The `server_bootstrap_auth_cors_and_proxy_surface` layer defines the outer edge behavior of the entire OpenCode HTTP platform:

- it centralizes error mapping, auth, logging, and CORS
- it exposes the API contract through OpenAPI surfaces
- it bootstraps a Bun server configured for long-lived SSE and websocket traffic
- it also acts as a fallback proxy surface for the hosted OpenCode app with explicit CSP control

So this module is not just server startup code. It is the edge architecture that makes the rest of OpenCode’s HTTP control plane deployable, observable, secure enough for its model, and integrated with the broader product surface.
