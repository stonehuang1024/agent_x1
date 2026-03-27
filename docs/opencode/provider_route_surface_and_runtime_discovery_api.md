# Provider Route Surface / Runtime Discovery API

---

# 1. Module Purpose

This document explains the HTTP route surface for provider discovery and provider-auth control in OpenCode.

The key questions are:

- Why does OpenCode expose a dedicated `/provider` route namespace?
- How does the provider route layer relate to lower-level provider runtime construction and provider error normalization?
- What is the difference between listing providers and listing provider auth methods?
- Why are OAuth authorize and callback flows exposed as explicit API operations?
- What does this route surface reveal about how OpenCode treats provider connectivity as runtime state?

Primary source files:

- `packages/opencode/src/server/routes/provider.ts`
- `packages/opencode/src/provider/provider.ts`
- `packages/opencode/src/provider/auth.ts`
- `packages/opencode/src/config/config.ts`
- `packages/opencode/src/provider/models.ts`

This layer is OpenCode’s **provider discovery and provider-auth control-plane API**.

---

# 2. Why `/provider` exists separately

Provider management is a distinct concern from:

- session execution
- message processing
- PTY management
- permission or question handling

A client often needs to answer higher-level setup questions before any model run can begin, such as:

- which providers are available?
- which providers are enabled or disabled by config?
- which providers are already connected?
- what authentication methods are available?
- how do I start or complete OAuth login?

That is why a dedicated `/provider` namespace makes sense.

---

# 3. The route surface is intentionally small

`server/routes/provider.ts` exposes:

- `GET /provider/`
- `GET /provider/auth`
- `POST /provider/:providerID/oauth/authorize`
- `POST /provider/:providerID/oauth/callback`

This is a compact but important setup/control surface.

It is not the place where model inference happens.

It is the place where clients discover and establish provider connectivity.

---

# 4. The route file is a thin transport layer

Just like other well-structured route modules in OpenCode, this file mostly delegates to deeper runtime modules:

- `Config.get()`
- `ModelsDev.get()`
- `Provider.list()`
- `Provider.fromModelsDevProvider(...)`
- `Provider.sort(...)`
- `ProviderAuth.methods()`
- `ProviderAuth.authorize(...)`
- `ProviderAuth.callback(...)`

That is the right design.

The route layer owns HTTP validation and response shape, while deeper modules own provider behavior.

---

# 5. `GET /provider/`: this is a discovery route, not just a list route

The main provider list route returns an object containing:

- `all`
- `default`
- `connected`

This is more than a naive list of provider records.

It is a structured provider discovery payload that helps clients answer three different questions:

- what providers exist in the current runtime?
- what default model should be assumed per provider?
- which providers already have live credentials or connectivity?

That is a strong control-plane response shape.

---

# 6. Why config filtering happens here

Before returning providers, the route loads config via:

- `Config.get()`

and applies:

- `disabled_providers`
- `enabled_providers`

This means provider discovery is not a raw dump of everything the codebase knows about.

It is filtered through current runtime configuration policy.

That is exactly what a client usually wants.

---

# 7. Why both enabled and disabled filters exist

The route handles:

- an allowlist-style `enabled_providers`
- a denylist-style `disabled_providers`

That is a flexible control model.

It allows OpenCode configuration to express either:

- only these providers should be surfaced
- or everything except these providers should be surfaced

The discovery API correctly reflects that configuration reality.

---

# 8. `ModelsDev.get()` as the provider catalog source

The route pulls provider catalog data from:

- `ModelsDev.get()`

This suggests the provider surface is built partly from a static or declarative provider/model catalog, not only from active runtime connections.

That matters because clients need to know what can be used, not only what is already authenticated.

So the provider API includes capability discovery as well as connectivity state.

---

# 9. The route merges catalog providers with connected providers

After filtering provider definitions from the model catalog, the route also loads:

- `Provider.list()`

Then it merges:

- transformed catalog providers
- connected providers

This is a key design point.

The returned provider view is a union of:

- what the system supports
- what the user/runtime has actually connected

That is much more useful than either source alone.

---

# 10. Why connected providers can override catalog data

The merge logic uses:

- `Object.assign(mapValues(filteredProviders, ...), connected)`

So connected provider entries can override or enrich the catalog-derived view.

That makes sense because a live configured provider may contain more authoritative runtime details than a generic catalog definition.

In other words, runtime reality wins over static catalog shape.

---

# 11. `all`, `default`, and `connected` each solve a different client need

## 11.1 `all`

- full provider info objects after config filtering and merge

## 11.2 `default`

- map of provider ID to chosen default model ID

## 11.3 `connected`

- list of provider IDs with live connected/authenticated state

This separation is very pragmatic.

It avoids forcing every client to recompute common derived views itself.

---

# 12. How default model selection works

The route computes defaults by calling:

- `Provider.sort(Object.values(item.models))[0].id`

This reveals that default model selection is not stored separately in the route layer.

It is derived from provider model ordering logic.

That keeps default choice tied to the provider runtime’s own sort semantics.

---

# 13. Why deriving defaults centrally is better than leaving it to clients

If clients had to choose defaults themselves, they could easily diverge from one another.

One client might pick the first unsorted model.

Another might pick alphabetically.

Another might hardcode a preference.

The route avoids that fragmentation by exposing a server-chosen default mapping.

That is a very good API decision.

---

# 14. `GET /provider/auth`: auth method discovery

The auth discovery route returns:

- `ProviderAuth.methods()`

as a map from provider ID to arrays of `ProviderAuth.Method`.

This is distinct from provider listing.

A provider may exist in the system catalog, but a client still needs to know:

- how can this provider be authenticated here?

That is why auth method discovery deserves its own endpoint.

---

# 15. Why auth method discovery is separate from provider list output

Keeping auth methods separate is good design because auth method metadata can be:

- more detailed
- more UI-oriented
- more variable across provider types

If it were crammed into the main provider list response, that payload would become less focused and harder to reason about.

The separation keeps each route semantically crisp.

---

# 16. OAuth is modeled as explicit control-plane operations

For providers that support OAuth, the route surface exposes:

- authorize
- callback

as explicit POST endpoints.

This shows OpenCode treats provider connection establishment as part of the API control plane, not just as a hidden local-side effect.

That is important for IDE clients, web clients, and automation flows.

---

# 17. `provider.oauth.authorize`: beginning the flow

`POST /provider/:providerID/oauth/authorize` validates:

- `providerID`
- `method`

then calls:

- `ProviderAuth.authorize({ providerID, method })`

and returns a `ProviderAuth.Authorization` object or `undefined`.

This route does not perform the whole login.

It asks the provider auth subsystem to start it and provide the necessary authorization information.

---

# 18. Why `method` is numeric

The route accepts:

- `method: number`

This indicates auth methods are indexed in the method list rather than addressed only by string identifiers.

That suggests the UI flow is expected to:

- query `/provider/auth`
- present the available methods
- choose one by index
- call authorize or callback with that index

It is a simple contract, though somewhat UI-driven.

---

# 19. Why authorize may return optional data

The route declares:

- `ProviderAuth.Authorization.optional()`

That suggests not every auth initiation flow necessarily returns a usable authorization object in the same way.

This is an important clue that provider auth methods may vary significantly.

The route surface leaves space for that variability instead of forcing a one-size-fits-all response.

---

# 20. `provider.oauth.callback`: completing the flow

`POST /provider/:providerID/oauth/callback` validates:

- `providerID`
- `method`
- optional `code`

then calls:

- `ProviderAuth.callback({ providerID, method, code })`

and returns `true` on success.

This separates the login completion step from the initiation step in a clean and explicit way.

---

# 21. Why callback is part of the HTTP API

OAuth flows often cross process and browser boundaries.

A dedicated callback endpoint in the control plane lets the client complete provider login in a structured, transport-neutral way.

This is especially important for:

- desktop shells
- web clients
- IDE integrations
- remote or hosted control surfaces

It avoids hardwiring provider auth completion into only one UI environment.

---

# 22. Route validation is especially important for auth flows

Both OAuth routes strictly validate:

- provider ID
- method index
- callback code shape

This matters because auth flows are high-value control-plane actions.

A lax contract here would make client bugs and provider-specific auth issues much harder to diagnose.

The route layer keeps the transport contract explicit.

---

# 23. Relationship to lower-level provider runtime modules

This route surface does not itself:

- build provider SDK clients
- send model requests
- normalize provider API errors
- classify retryability

Those responsibilities live deeper in modules like:

- `provider/provider.ts`
- `provider/error.ts`

That separation is healthy.

The provider route layer is about discovery and connectivity management, not inference execution.

---

# 24. Why this route surface still matters enormously

Even though it does not run model inference, this module is still critical because clients cannot use the provider runtime effectively unless they can first:

- discover providers
- understand defaults
- see connection state
- negotiate supported auth methods
- initiate or complete provider authentication

In other words, this is the setup plane for the model plane.

---

# 25. A representative provider-setup flow

A realistic client flow looks like this:

## 25.1 Discover provider catalog and connection state

- `GET /provider/`

## 25.2 Discover available auth methods

- `GET /provider/auth`

## 25.3 Start OAuth for a chosen provider and method

- `POST /provider/:providerID/oauth/authorize`

## 25.4 Complete OAuth callback

- `POST /provider/:providerID/oauth/callback`

## 25.5 Re-query provider list

- verify the provider appears in `connected`

This is a clean provider-onboarding lifecycle.

---

# 26. Key design principles behind this module

## 26.1 Provider discovery should reflect configuration policy, not just raw capability catalogs

So the provider list route filters through enabled/disabled config.

## 26.2 Capability discovery and connectivity state should be combined but not conflated

So the route returns all providers plus separate default and connected views.

## 26.3 Authentication setup deserves explicit control-plane endpoints

So OAuth initiation and completion are formal API operations.

## 26.4 Provider runtime behavior should stay separate from provider management transport

So route handlers delegate discovery and auth logic to provider modules instead of embedding it directly.

---

# 27. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/routes/provider.ts`
2. `packages/opencode/src/provider/auth.ts`
3. `packages/opencode/src/provider/provider.ts`
4. `packages/opencode/src/provider/models.ts`
5. `packages/opencode/src/config/config.ts`

Focus on these functions and concepts:

- provider list filtering
- merge of catalog and connected providers
- default model derivation
- `ProviderAuth.methods()`
- `ProviderAuth.authorize()`
- `ProviderAuth.callback()`

---

# 28. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: What exact information is carried by `ProviderAuth.Method` and `ProviderAuth.Authorization`, and how do different providers vary?
- **Question 2**: Should the provider list route expose more explicit state about why a provider is unavailable, disabled, or not connected?
- **Question 3**: How stable is the numeric `method` indexing contract across provider auth method list changes?
- **Question 4**: Are there non-OAuth provider auth flows that deserve additional dedicated control-plane endpoints?
- **Question 5**: How should clients react when catalog-derived provider info and connected runtime provider info differ materially?
- **Question 6**: Should connected-provider state changes emit more explicit events for UI clients listening on the event stream?
- **Question 7**: How are secrets, tokens, and credential storage managed behind `ProviderAuth.callback()`?
- **Question 8**: Should provider discovery eventually expose capability groupings like reasoning support, tool support, or streaming support more directly?

---

# 29. Summary

The `provider_route_surface_and_runtime_discovery_api` layer gives OpenCode clients a structured way to discover and prepare model providers before inference begins:

- it exposes config-filtered provider discovery rather than a raw provider catalog
- it reports both default model selection and current connection state
- it separates authentication method discovery from provider listing
- it models OAuth setup and completion as explicit control-plane operations

So this module is not the provider execution layer itself. It is the management and onboarding API that makes the deeper provider runtime usable by real clients.
