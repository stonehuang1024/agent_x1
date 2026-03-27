# MCP OAuth Callback / Browser Flow

---

# 1. Module Purpose

This document explains the browser-based OAuth callback flow used by OpenCode’s MCP runtime, focusing on the local callback server, state validation, timeout handling, and the browser-open interaction model.

The key questions are:

- Why does OpenCode run a local callback server for MCP OAuth?
- How do `McpOAuthProvider` and `McpOAuthCallback` divide responsibilities?
- How are state, code verifier, and browser redirect handling coordinated?
- What security checks are enforced during callback handling?
- What does this flow reveal about OpenCode’s UX strategy for MCP authentication?

Primary source files:

- `packages/opencode/src/mcp/oauth-provider.ts`
- `packages/opencode/src/mcp/oauth-callback.ts`
- `packages/opencode/src/mcp/auth.ts`
- `packages/opencode/src/mcp/index.ts`

This layer is OpenCode’s **MCP browser-auth flow, local callback server, and OAuth completion bridge**.

---

# 2. Why this layer matters

The deeper MCP runtime article explains connection and status handling.

But the browser-based auth path has enough protocol and UX detail to deserve its own focused treatment.

This layer is where OpenCode handles:

- redirect URL ownership
- local callback receipt
- CSRF-style state validation
- timeout and cancellation
- browser-open failure handling

That makes it a security-sensitive and user-visible part of the MCP system.

---

# 3. The flow is split across two cooperating components

A useful mental model is:

## 3.1 `McpOAuthProvider`

- implements the MCP SDK’s OAuth client provider interface
- stores/retrieves OAuth-related state and credentials
- tells the runtime where to redirect and how to persist auth artifacts

## 3.2 `McpOAuthCallback`

- runs a local HTTP callback server
- waits for browser redirects to arrive
- validates state and resolves or rejects pending auth promises

This is a clean split between:

- protocol/provider integration
- callback-server orchestration

---

# 4. The redirect target is intentionally local

`McpOAuthProvider.redirectUrl` returns:

- `http://127.0.0.1:19876/mcp/oauth/callback`

This means OpenCode uses a local loopback callback server to complete OAuth.

That is a very common and sensible desktop/CLI auth pattern.

It allows the browser to return the authorization result directly to the local application environment.

---

# 5. Why loopback callback is a good fit here

OpenCode is not relying on:

- a hosted web callback owned by some remote service
- manual copy/paste of the auth code in the common path

Instead it uses a local callback endpoint that the CLI/runtime controls.

This usually provides a smoother UX while still keeping credentials local.

---

# 6. `McpOAuthProvider` supplies client metadata to the SDK

The provider’s `clientMetadata` includes:

- redirect URIs
- client name and URI
- grant types
- response types
- token endpoint auth method

This is the runtime’s advertised OAuth client identity.

It is especially important when dynamic client registration is supported.

---

# 7. Why token endpoint auth method depends on client secret presence

The provider chooses:

- `client_secret_post` if a client secret exists
- otherwise `none`

This is a direct reflection of whether the server is using:

- pre-registered confidential-ish client credentials
- or a public/dynamically registered flow without client secret use

That means client configuration materially changes protocol behavior.

---

# 8. `clientInformation()`: config and persisted registration are both supported

The provider resolves client identity in this order:

- preconfigured `clientId` / `clientSecret`
- persisted dynamically registered client info from `McpAuth.getForUrl(...)`
- otherwise `undefined` to trigger dynamic registration

This is a smart design.

It supports both:

- operator-provided client registration
- auto-registration when the server allows it

without forcing one model globally.

---

# 9. Why persisted client info is validated by server URL

The provider uses:

- `McpAuth.getForUrl(this.mcpName, this.serverUrl)`

That ensures stored registration info is only reused if it belongs to the same server URL.

This is an excellent safety invariant.

It prevents accidental reuse of client credentials after a server URL change.

---

# 10. Client secret expiry is handled explicitly

If persisted `clientSecretExpiresAt` is in the past, `clientInformation()` returns `undefined` so the runtime can re-register.

This is another good lifecycle detail.

The provider treats dynamic registration material as expirable state, not as permanent truth.

---

# 11. Tokens are also URL-bound and persisted

`tokens()` and `saveTokens()` read and write auth tokens through `McpAuth`, again tied to the MCP name and server URL.

This means both:

- client registration data
- OAuth tokens

share the same durability and URL-validation model.

That keeps credential state consistent.

---

# 12. Why `saveCodeVerifier(...)` and `codeVerifier()` matter

PKCE-style flows need a code verifier to be stable across the authorize/callback boundary.

The provider persists the code verifier through `McpAuth` and later retrieves it when the SDK needs it.

This is essential for correct OAuth completion.

It also shows that OpenCode treats the provider object as the durable protocol bridge, not merely a transient helper.

---

# 13. OAuth state handling is defensive and pragmatic

The provider can:

- save a state value
- return a saved state value
- or generate and persist a new random state if none exists

That last behavior is especially important.

The code comments explain that the SDK may call `state()` as a generator, not just a getter.

So OpenCode cannot rely on a state value always being pre-seeded.

This makes the flow more resilient to SDK calling patterns.

---

# 14. Why random state generation is critical

State is the main CSRF-style correlation token in the callback flow.

If the provider could not reliably produce one, the callback server would have no trustworthy pending-auth key to match on return.

So state generation is not optional infrastructure.

It is a core security requirement of the browser flow.

---

# 15. `redirectToAuthorization(...)`: provider delegates UX policy outward

When the SDK wants to redirect, `McpOAuthProvider` calls:

- `this.callbacks.onRedirect(authorizationUrl)`

This is a good separation.

The provider knows the protocol wants a redirect, but it does not hardcode exactly how the UI/runtime should open the browser.

That decision is delegated outward.

---

# 16. Why redirect delegation is architecturally clean

Browser-opening behavior is environment-specific.

A CLI, TUI, GUI wrapper, or headless environment may each want different behavior.

By using a callback, the provider preserves protocol correctness without overcommitting to one UI policy.

---

# 17. `McpOAuthCallback.ensureRunning()`: local callback server bootstrap

The callback module ensures a Bun server is running on:

- port `19876`

and only starts its own server if that port is not already in use.

This is important because multiple OpenCode instances may coexist.

The callback server is treated as a shared local facility rather than blindly launched multiple times.

---

# 18. Why port-in-use detection matters

If another OpenCode instance already owns the callback port, trying to start a second server would fail or behave unpredictably.

By checking first, the runtime accepts that the callback facility may already exist and can still be used.

That is a practical multi-instance safety measure.

---

# 19. The callback route is tightly scoped

The callback server only handles:

- `OAUTH_CALLBACK_PATH`

Everything else returns `404`.

This keeps the server extremely focused.

It is not a general-purpose local web service.

It is a single-purpose OAuth completion endpoint.

---

# 20. The callback handler validates required query parameters

The handler reads:

- `code`
- `state`
- `error`
- `error_description`

and logs receipt details.

Then it performs several important checks:

- `state` must exist
- callback errors are surfaced properly
- `code` must exist on success paths
- `state` must match a pending auth entry

This is the core correctness and security logic of the callback server.

---

# 21. Why missing state is treated as a potential CSRF attack

If `state` is absent, the handler returns an error page saying it may be a CSRF attack.

This is exactly the right default.

An OAuth callback without state correlation is unsafe to trust.

The code treats that as a serious validity failure rather than a minor UX issue.

---

# 22. Why invalid or expired state is also a hard failure

If the callback carries a state value not present in `pendingAuths`, the server again treats it as:

- invalid or expired state
- potential CSRF attack

This is another important security check.

The callback flow is only considered valid if it matches a known pending authorization session.

---

# 23. `pendingAuths`: the in-memory callback rendezvous map

`McpOAuthCallback` stores pending callbacks in:

- `Map<string, PendingAuth>`

keyed by OAuth state.

Each entry holds:

- `resolve`
- `reject`
- `timeout`

This is the callback-side handshake registry.

It bridges:

- the outgoing authorization request
- the later incoming browser callback

---

# 24. Why keying by OAuth state is the right choice

State is the correlation token that survives the browser round-trip.

So keying the pending map by state means the callback server can match the browser return to the correct pending authorization without inventing a second parallel identity scheme.

That is the correct protocol-aligned design.

---

# 25. `waitForCallback(oauthState)`: promise-based completion model

`waitForCallback(...)` returns a promise that:

- stores a pending resolver/rejector
- starts a 5-minute timeout
- resolves with the auth code when the callback arrives
- rejects on timeout

This is a clean asynchronous handshake abstraction.

The rest of the runtime can wait on the browser flow as a promise rather than dealing directly with callback server internals.

---

# 26. Why timeout matters here

OAuth browser flows can stall indefinitely if the user never completes authorization.

The 5-minute timeout ensures the runtime does not wait forever.

That is essential for a decent operator experience and to avoid accumulating stale pending auth entries.

---

# 27. Success and error pages are part of the UX contract

The callback server returns styled HTML pages for:

- success
- failure

The success page even tries to close the window automatically after 2 seconds.

This is a small but important usability detail.

It acknowledges that browser-based auth is part of the user journey and tries to provide a polished closure to it.

---

# 28. Error propagation is careful

When the callback includes an OAuth error, the server:

- clears timeout
- deletes the pending auth entry
- rejects the waiting promise with an error
- returns an HTML error page

This is correct dual-path behavior.

The user sees browser feedback, and the runtime waiting on the auth flow also receives the failure.

---

# 29. `cancelPending(...)` is notable but subtle

`cancelPending(mcpName)` attempts to remove a pending auth entry by key.

However, the main pending map is keyed by OAuth state rather than MCP name.

That means this function is worth treating cautiously when reasoning about correctness.

It may reflect a mismatch in assumptions or a path used differently elsewhere.

This is an important source-grounded observation.

---

# 30. Browser-open flow is intentionally separated from redirect generation

The provider only reports the authorization URL through `onRedirect(...)`.

The wider MCP runtime is then free to:

- open the browser automatically
- emit `BrowserOpenFailed` if that fails
- fall back to user-visible instructions or manual opening flows

This separation is good architecture.

It decouples OAuth protocol state from environment-specific browser-launch behavior.

---

# 31. Why browser-open failure is a first-class runtime concern

Opening a browser is not guaranteed to work in:

- headless environments
- remote sessions
- unusual desktop setups

By treating browser-open failure as a real runtime event rather than an ignored detail, OpenCode can surface actionable guidance instead of leaving the user stuck.

That is good UX engineering.

---

# 32. The callback server lifecycle is explicitly manageable

`McpOAuthCallback` supports:

- `ensureRunning()`
- `stop()`
- `isRunning()`
- `isPortInUse()`

This is useful because the callback server is a real local runtime service with lifecycle and concurrency implications.

It is not just a hidden helper function.

---

# 33. Why `stop()` rejects pending auths

When the server stops, it:

- clears timeouts
- rejects all pending auth promises
- clears the map

That is exactly the right lifecycle behavior.

Otherwise callers would be left waiting on promises that can never resolve.

---

# 34. A representative MCP OAuth browser flow

A typical flow looks like this:

## 34.1 Runtime prepares provider and callback server

- callback server ensures local port is available
- provider exposes redirect URL and state machinery

## 34.2 Authorization URL is generated and surfaced

- via `redirectToAuthorization(...)`
- runtime may open browser

## 34.3 Runtime waits on `waitForCallback(state)`

- pending auth promise is stored with timeout

## 34.4 Browser redirects back to local loopback URL

- callback server receives `code` and `state`

## 34.5 Callback validates state and resolves the pending promise

- runtime receives the code and can complete auth

This is the complete handshake from browser redirect to local completion.

---

# 35. Key design principles behind this module

## 35.1 Desktop/CLI OAuth flows should complete locally without shipping credentials through a hosted web callback

So OpenCode uses a loopback callback server on `127.0.0.1`.

## 35.2 Protocol state should be persisted where necessary and correlated with strict state validation

So state, code verifier, client info, and tokens are persisted through `McpAuth`, while callback completion is keyed by OAuth state.

## 35.3 Browser UX should be coordinated with protocol logic, but not hardwired into the protocol provider itself

So redirect generation and browser opening are split across provider callbacks and higher runtime logic.

## 35.4 Pending browser auth flows need explicit timeout, cancellation, and shutdown behavior

So the callback server manages pending promises and rejects them on timeout or shutdown.

---

# 36. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/mcp/oauth-provider.ts`
2. `packages/opencode/src/mcp/oauth-callback.ts`
3. `packages/opencode/src/mcp/auth.ts`
4. the auth-related paths in `packages/opencode/src/mcp/index.ts`

Focus on these functions and concepts:

- `redirectUrl`
- `clientInformation()`
- `tokens()`
- `saveCodeVerifier()` / `state()`
- `ensureRunning()`
- `waitForCallback()`
- callback state validation
- `BrowserOpenFailed`

---

# 37. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Is `cancelPending(mcpName)` intentionally keyed differently from `pendingAuths` state entries, or does it represent a mismatch worth fixing?
- **Question 2**: Where exactly in `mcp/index.ts` is browser opening attempted, and how is `BrowserOpenFailed` emitted in that path?
- **Question 3**: Should the callback server support a more explicit instance-awareness model when multiple OpenCode processes are active?
- **Question 4**: How should the UX differ between automatic browser auth and manual copy/paste or external browser flows?
- **Question 5**: Are the current HTML success/error pages sufficient for all environments, including remote or SSH-based usage?
- **Question 6**: Should the callback timeout be configurable per MCP server or environment?
- **Question 7**: How are stale state, code verifier, and token artifacts cleaned up across interrupted auth attempts?
- **Question 8**: What additional telemetry would help diagnose OAuth failures in real-world MCP integrations?

---

# 38. Summary

The `mcp_oauth_callback_and_browser_flow` layer is the browser-facing half of OpenCode’s MCP authentication system:

- `McpOAuthProvider` handles OAuth client metadata, persisted state, token storage, and redirect coordination
- `McpOAuthCallback` runs a local loopback callback server and resolves pending auth promises keyed by OAuth state
- strict state validation, timeout handling, and shutdown cleanup make the flow operationally and security-wise coherent
- browser opening is intentionally separated from protocol generation so the runtime can adapt to different environments

So this module is the local browser-auth bridge that turns MCP OAuth from a protocol capability into a usable desktop/CLI workflow.
