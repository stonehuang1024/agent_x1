# Provider Auth Route / Runtime Boundary

---

# 1. Module Purpose

This document explains the boundary between the `/provider` authentication routes and the deeper provider-auth runtime implementation.

The key questions are:

- What responsibilities belong to `server/routes/provider.ts` versus `provider/auth.ts` and `provider/auth-service.ts`?
- Why is provider auth exposed as a small HTTP surface over a separate Effect-based service layer?
- How do auth methods, pending OAuth state, and credential persistence fit together?
- Why does the provider-auth layer use its own runtime instead of the shared runtime?
- What does this module reveal about how OpenCode integrates plugin-defined auth flows with provider credential storage?

Primary source files:

- `packages/opencode/src/server/routes/provider.ts`
- `packages/opencode/src/provider/auth.ts`
- `packages/opencode/src/provider/auth-service.ts`
- `packages/opencode/src/auth/service`
- `packages/opencode/src/plugin`

This layer is OpenCode’s **provider-auth route wrapper and runtime service boundary**.

---

# 2. Why this boundary matters

The `/provider` auth endpoints look simple:

- list methods
- start OAuth
- finish OAuth callback

But the underlying implementation is doing much more:

- discovering auth-capable plugins
- holding pending OAuth state
- validating callback requirements
- converting successful results into stored auth credentials
- bridging plugin auth flows into the general provider auth store

So the boundary between route and runtime is important to understand.

---

# 3. Three layers are involved

The provider-auth path is split across three layers:

## 3.1 HTTP route layer

- `server/routes/provider.ts`
- validates transport input and returns JSON

## 3.2 runtime wrapper layer

- `provider/auth.ts`
- exposes a simple namespace API and runs the Effect service

## 3.3 Effect service layer

- `provider/auth-service.ts`
- owns real auth behavior, state, and persistence logic

This is a strong separation of concerns.

---

# 4. The route layer is intentionally minimal

The auth-related provider routes do very little beyond:

- validating `providerID`
- validating `method`
- validating optional `code`
- calling `ProviderAuth.methods()` / `authorize()` / `callback()`

That is exactly what a good transport layer should do.

It exposes the control-plane API without embedding provider-auth business logic directly in HTTP handlers.

---

# 5. `provider/auth.ts` is a wrapper, not the real implementation

`provider/auth.ts` defines the `ProviderAuth` namespace, but it is mostly a façade over:

- `auth-service.ts`

It exports:

- `Method`
- `Authorization`
- `methods()`
- `authorize(...)`
- `callback(...)`
- `api(...)`

and forwards them through a small local runtime helper.

So this file is best understood as a runtime adapter boundary, not the true source of provider-auth behavior.

---

# 6. Why `provider/auth.ts` creates its own runtime

This is one of the most important details in the whole module.

The file explicitly says it uses a separate runtime because:

- `runtime.ts -> auth-service.ts -> provider/auth.ts` would create a circular import

So it constructs:

- `ManagedRuntime.make(S.ProviderAuthService.defaultLayer)`

and runs service calls through that local runtime.

This is a pragmatic architecture boundary to break an import cycle.

---

# 7. Why this separate runtime is considered safe

The comment also says:

- `AuthService` is stateless file I/O
- so the duplicate runtime instance is harmless

That is an important assumption.

It means the service being wrapped does not rely on delicate shared in-memory mutable state that would be corrupted by a second runtime instance.

This is the architectural reason the workaround is acceptable.

---

# 8. The real auth model begins in `auth-service.ts`

`provider/auth-service.ts` defines:

- `Method`
- `Authorization`
- typed provider-auth errors
- `ProviderAuthService.Service`
- a `ProviderAuthService` Effect service implementation

This is the true behavioral core of provider auth.

---

# 9. `Method`: provider auth capability surface

A provider auth method contains:

- `type: "oauth" | "api"`
- `label`

This is intentionally compact.

It gives clients enough to:

- enumerate available auth choices
- display them meaningfully
- select one by index

The route layer returns these method arrays directly.

---

# 10. Why method arrays are discovered dynamically

The service builds its method map by looking through:

- `Plugin.list()`
- filtering plugins with `auth?.provider`

This is a major architectural point.

Provider auth capabilities are plugin-driven, not purely hardcoded into the route layer or a static provider table.

That makes the auth system extensible.

---

# 11. Plugin-defined auth is the real source of truth

The service constructs a map of provider IDs to plugin auth definitions, then exposes just the projected method metadata publicly.

So the provider auth route surface is ultimately fronting:

- plugin-defined provider auth behavior

This is important because it means adding or changing provider auth can happen through the plugin system rather than through route code changes alone.

---

# 12. `Authorization`: what authorize returns

The authorization shape contains:

- `url`
- `method: "auto" | "code"`
- `instructions`

This is richer than just “here is a URL.”

It also tells the caller:

- how the callback is expected to work
- what instructions should be shown to the user

That makes the route surface more UI-capable.

---

# 13. Why `Authorization.method` matters

The `method` field tells the system whether callback completion is:

- automatic (`auto`)
- or requires an explicit code (`code`)

This is essential because not all OAuth-like flows complete the same way.

The auth service preserves that distinction all the way from plugin result to route response.

That is good design.

---

# 14. Pending OAuth state is stored per instance

The service creates instance-scoped state using:

- `InstanceState.make(...)`

and stores:

- `methods`
- `pending: new Map<ProviderID, AuthOuathResult>()`

This is one of the most important facts in the module.

A started OAuth flow is not purely stateless.

Pending callback state is kept in instance-scoped memory.

---

# 15. Why pending OAuth state must exist

After `authorize(...)`, the system needs to remember enough context to later complete the flow during `callback(...)`.

That includes:

- which provider was being authorized
- what callback mode it expects
- how to invoke the completion callback

That is why the service stores pending results keyed by `ProviderID`.

---

# 16. `methods()`: projected method discovery

The `methods()` service method reads the plugin-derived method map and projects each auth definition down to:

- `type`
- `label`

This projection is important.

It keeps the public route contract small and avoids exposing more plugin-internal auth implementation detail than necessary.

That is a good boundary.

---

# 17. `authorize(...)`: start flow and store pending state

The service’s `authorize(...)` method:

- reads the instance-scoped state
- selects the chosen method by provider ID and numeric index
- returns early if the method is not OAuth
- calls `method.authorize()`
- stores the result in `pending`
- returns `{ url, method, instructions }`

This is the key handshake-start path.

---

# 18. Why non-OAuth methods return nothing from `authorize(...)`

If the chosen method is not OAuth, `authorize(...)` returns nothing.

That makes sense because API-key-style methods do not require browser or callback flow initiation.

The route surface therefore naturally treats OAuth initiation as optional depending on the selected method.

---

# 19. `callback(...)`: complete flow using pending state

The service’s callback path:

- looks up the pending auth result by provider ID
- fails with `OauthMissing` if no pending state exists
- fails with `OauthCodeMissing` if the flow expects a code and none was provided
- invokes the stored callback function appropriately
- fails with `OauthCallbackFailed` if the result is absent or unsuccessful
- persists credentials via the general auth service

This is the heart of the provider-auth lifecycle.

---

# 20. Why `OauthMissing` is a real semantic error

If callback is called without a previously started pending flow, the service raises:

- `ProviderAuthOauthMissing`

That is the correct behavior.

It prevents the system from pretending callback completion can happen out of thin air.

The stateful relationship between authorize and callback is explicit and enforced.

---

# 21. Why `OauthCodeMissing` exists separately

Some flows require an explicit code and some do not.

So missing code is not a generic callback failure.

It is a distinct error condition when the pending auth result says:

- callback method is `code`

This distinction helps the route/runtime boundary preserve a more precise failure model.

---

# 22. What successful callback actually persists

When callback succeeds, the service checks the returned result.

If the result contains:

- `key`

it stores provider auth as:

- `type: "api"`

If the result contains:

- `refresh`

it stores provider auth as:

- `type: "oauth"`
- plus access/refresh/expires fields

This is a very important convergence point.

Different plugin-auth flows are normalized into the shared provider auth store.

---

# 23. Why this normalization matters

The rest of the provider runtime does not want to care about every plugin’s private auth result shape.

It wants stable stored auth records that can be queried later by provider loading and request execution logic.

So callback acts as the normalization boundary between:

- plugin auth result
- shared stored provider credential format

That is strong architecture.

---

# 24. `api(...)`: the non-OAuth path exists too

The wrapper exposes:

- `ProviderAuth.api(...)`

which directly stores an API key through the auth service.

Even though the current route file does not expose a dedicated HTTP endpoint for this path, the runtime boundary clearly supports it.

That is important because it shows the provider-auth runtime is broader than the current route surface.

---

# 25. Relationship to the general auth store

The provider-auth service depends on:

- `Auth.AuthService`

and persists results with:

- `auth.set(providerID, ...)`

This is the key backend integration point.

Provider auth does not own long-term credential storage by itself.

It delegates to the shared auth service.

That keeps storage concerns centralized.

---

# 26. Relationship to provider runtime loading

The grep results in `provider/provider.ts` show the provider runtime later checks stored auth through:

- `Auth.get(input.id)`

and other auth-related lookups.

So the route/service path here is directly upstream of whether provider clients can actually be instantiated with valid credentials.

That means the provider-auth boundary is operationally crucial even though it does not itself execute model requests.

---

# 27. Why this module matters architecturally

This boundary shows OpenCode using a clean layered design:

- HTTP routes expose a small auth control plane
- a wrapper module isolates runtime/circular-import issues
- an Effect service owns plugin-driven auth flows and pending state
- a shared auth service persists the normalized credentials

This is a much better design than putting OAuth state machines directly into route handlers.

---

# 28. A representative provider-auth lifecycle

A typical lifecycle looks like this:

## 28.1 Client asks for available auth methods

- route calls `ProviderAuth.methods()`

## 28.2 Client starts OAuth for a chosen provider and method

- route calls `ProviderAuth.authorize(...)`
- service stores pending auth result in instance state

## 28.3 Client completes callback

- route calls `ProviderAuth.callback(...)`
- service validates pending state and callback requirements
- service normalizes success into stored auth credentials

## 28.4 Provider runtime later consumes stored auth

- via general auth lookups during provider loading

This is a clean layered authentication pipeline.

---

# 29. Key design principles behind this module

## 29.1 HTTP routes should expose auth flow steps without owning auth state machines

So route handlers stay thin and delegate immediately.

## 29.2 Plugin-defined provider auth should be normalized into a shared provider credential store

So callback transforms plugin results into standard auth records.

## 29.3 Pending OAuth state belongs to runtime state, not to the route layer

So `auth-service.ts` holds pending provider auth state in instance-scoped memory.

## 29.4 Runtime boundaries should be made explicit when circular dependencies would otherwise force architectural leakage

So `provider/auth.ts` builds a separate managed runtime wrapper.

---

# 30. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/routes/provider.ts`
2. `packages/opencode/src/provider/auth.ts`
3. `packages/opencode/src/provider/auth-service.ts`
4. `packages/opencode/src/auth/service`
5. `packages/opencode/src/provider/provider.ts`

Focus on these functions and concepts:

- `ProviderAuth.methods()`
- `ProviderAuth.authorize()`
- `ProviderAuth.callback()`
- `ProviderAuthService.methods/authorize/callback/api`
- pending provider auth state
- `auth.set(...)`
- circular-import runtime boundary

---

# 31. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Should the HTTP route surface eventually expose the `api(...)` path for direct API-key submission as a first-class endpoint?
- **Question 2**: How are pending OAuth states cleaned up if a flow is abandoned or a callback never arrives?
- **Question 3**: What exact plugin auth result variants are supported beyond `key` and `refresh`-based success cases?
- **Question 4**: Does instance-scoped pending auth state create any UX issues across reloads, restarts, or multi-client flows?
- **Question 5**: Could the circular-import boundary be eliminated with a different runtime/module structure, or is the separate runtime the cleanest option?
- **Question 6**: How are provider-auth errors mapped at the HTTP boundary today, and are they sufficiently precise for clients?
- **Question 7**: Should successful provider-auth changes emit explicit events for connected clients?
- **Question 8**: How do plugin-defined auth methods evolve safely without destabilizing the provider route contract?

---

# 32. Summary

The `provider_auth_route_and_runtime_boundary` layer shows that OpenCode’s provider-auth flow is carefully split across transport, wrapper, and service layers:

- the HTTP routes expose a minimal auth control plane
- `provider/auth.ts` wraps the service in a separate managed runtime to avoid circular-import problems
- `auth-service.ts` discovers plugin-defined auth methods, tracks pending OAuth state, validates callbacks, and normalizes successful results
- the normalized credentials are persisted through the shared auth service for later provider runtime use

So this module is not just route glue. It is the layered boundary where plugin-driven provider auth becomes durable runtime credential state.
