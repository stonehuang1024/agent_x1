# Config Routes / Runtime Configuration API

---

# 1. Module Purpose

This document explains the instance-scoped configuration route surface in OpenCode and how it relates to the broader runtime configuration system.

The key questions are:

- Why does OpenCode expose a dedicated `/config` route namespace?
- What is the difference between instance-scoped config and global config?
- How do `server/routes/config.ts` and `config/config.ts` divide responsibilities?
- Why does the config route expose both general config access and provider-focused config discovery?
- What does the route surface reveal about how configuration shapes runtime behavior?

Primary source files:

- `packages/opencode/src/server/routes/config.ts`
- `packages/opencode/src/config/config.ts`
- `packages/opencode/src/provider/provider.ts`

This layer is OpenCode’s **instance-scoped runtime configuration control-plane API**.

---

# 2. Why `/config` exists separately

Configuration is neither:

- just a startup file concern
- nor merely an internal implementation detail

In OpenCode, config actively controls runtime behavior such as:

- enabled providers
- permissions
- compaction options
- plugins
- commands
- agents and modes
- project-local overrides

So clients need a direct API surface for reading and mutating the active effective config of the current instance.

That is why `/config` deserves its own namespace.

---

# 3. Instance-scoped config versus global config

A useful distinction is:

## 3.1 `/config`

- current effective config for the bound instance/directory context
- shaped by project files, global files, flags, inline config, remote config, and more

## 3.2 `/global/config`

- server-level global config surface
- not tied to one bound project instance

This distinction mirrors the broader OpenCode architecture:

- instance scope
- global scope

---

# 4. The route surface is intentionally compact

`server/routes/config.ts` exposes:

- `GET /config/`
- `PATCH /config/`
- `GET /config/providers`

This is a small route set, but it sits on top of a very rich configuration system.

That is an important pattern in OpenCode:

- simple transport surface
- complex authoritative runtime behind it

---

# 5. `GET /config/`: retrieve the effective config

The main route simply returns:

- `await Config.get()`

validated and documented as:

- `Config.Info`

This is significant because the route is not exposing raw file contents.

It is exposing the resolved effective configuration after all precedence and merge logic have been applied.

That is exactly what clients generally need.

---

# 6. Why effective config matters more than raw file config

The config subsystem loads from many sources with precedence, including:

- remote `.well-known/opencode`
- global user config
- custom config path
- project config
- `.opencode` directory config
- inline config content
- account/org config
- managed enterprise config
- environment flags

A client usually does not want to manually reproduce all of that.

It wants to know:

- what configuration is actually active right now?

`GET /config/` answers that question directly.

---

# 7. `Config.state`: configuration is instance-scoped computed state

The config module defines:

- `Config.state = Instance.state(async () => { ... })`

This is a foundational fact.

It means the active config is computed and cached in instance scope, not as a single global singleton.

That is the correct design because config depends on:

- current directory
- current worktree
- project-local files
- instance-local environment

So the config route naturally belongs in the instance-bound control plane.

---

# 8. Why config loading is so layered

The code in `config/config.ts` makes it clear that OpenCode treats configuration as a layered precedence system, not a single file.

This supports real-world use cases like:

- org defaults
- user defaults
- project overrides
- local `.opencode` customization
- temporary environment-driven overrides
- enterprise managed policy

That is a sophisticated configuration model.

The route layer wisely does not duplicate that complexity.

---

# 9. Merge semantics are also part of the design

The config module includes a custom merge function that concatenates certain array fields like:

- `plugin`
- `instructions`

instead of overwriting them.

This matters because the effective config returned by `/config` is not just the last file loaded.

It is the result of a type-aware merge policy.

That makes the route output much more meaningful than simple raw JSON retrieval.

---

# 10. `PATCH /config/`: config mutation as a formal API action

The update route validates the body against:

- `Config.Info`

then calls:

- `Config.update(config)`

and returns the provided config object.

This means config updates are first-class control-plane operations, not just manual file edits outside the API.

That is important for clients and automation.

---

# 11. Why full-schema validation is important here

Config is a high-leverage input surface.

Bad configuration can affect:

- provider behavior
- permissions
- plugin loading
- agent behavior
- command availability
- formatting and LSP state

So validating config updates against the authoritative `Config.Info` schema is essential.

It keeps configuration mutation from becoming loosely typed and error-prone.

---

# 12. Why the route returns the updated config object

After `Config.update(config)`, the route returns:

- `config`

This is a simple response pattern.

It gives the caller immediate confirmation of the accepted payload shape.

For deeper questions about normalization or writeback side effects, the caller can always re-fetch `GET /config/`.

That keeps the write route straightforward.

---

# 13. The route layer does not expose config-source provenance

One thing `GET /config/` does not do is explain where each value came from.

It returns the resolved effective config, not a provenance map.

That is a reasonable choice for the main control-plane surface because most clients care first about:

- the active result

not necessarily:

- every contributing source layer

Still, this is an important limitation to understand when debugging complex config behavior.

---

# 14. `GET /config/providers`: provider-focused config discovery

The third route returns:

- `providers`
- `default`

built from:

- `Provider.list()`
- `Provider.sort(...)`

This is interesting because the config namespace includes a provider-specific view, even though there is also a `/provider` namespace.

That is not redundant by accident.

It reflects two different API perspectives.

---

# 15. `/config/providers` versus `/provider/`

A useful distinction is:

## 15.1 `/provider/`

- broader provider discovery and auth-control surface
- includes catalog-style provider discovery and connected state

## 15.2 `/config/providers`

- provider view from the config/runtime-availability perspective
- focused on configured providers and default model selection

So `/config/providers` is not the onboarding surface.

It is the configuration-shaped provider surface.

---

# 16. Why `/config/providers` belongs here

Clients often need a simple answer to:

- given the current config, what providers are actually in play, and what are their default models?

That is a configuration question as much as a provider question.

Putting this route under `/config` is therefore reasonable.

It serves clients that are thinking in terms of active runtime setup rather than provider-auth workflow.

---

# 17. Logging around provider config discovery

`GET /config/providers` wraps its work in:

- `log.time("providers")`

This is a small detail, but it suggests provider listing may be meaningful enough operationally to time explicitly.

That makes sense because provider resolution can involve runtime loading and config interplay.

---

# 18. Why the route uses `Provider.list()` rather than raw config fields

The providers route does not simply inspect raw config text.

It asks the provider runtime for:

- `Provider.list()`

This is important because runtime-visible providers may be influenced by more than static config fields alone.

The route therefore surfaces the runtime-effective provider view, not merely the stored declarative config fragment.

---

# 19. Default model derivation appears here too

Like the `/provider/` route, `/config/providers` computes defaults through:

- `Provider.sort(Object.values(item.models))[0].id`

This reinforces a broader design principle:

- model default selection is centralized in provider sorting logic
- routes expose the derived result instead of inventing route-local heuristics

That consistency is good for clients.

---

# 20. The underlying config system reveals just how much config controls

Even from the first part of `config/config.ts`, we can already see config influencing:

- provider auth-derived remote config
- user/global defaults
- project files
- `.opencode` plugin, command, and agent loading
- remote account/org config
- enterprise managed config
- permission overrides
- compaction flags
- deprecated field migrations

This is not minor preference state.

Config is a major part of OpenCode’s runtime composition system.

---

# 21. Why config is tied to instance lifecycle

Because config loading depends on:

- `Instance.directory`
- `Instance.worktree`
- project-local paths and `.opencode` directories

the configuration system belongs naturally to instance scope.

That means the `/config` route is effectively a window into instance composition state.

It is not just a settings editor.

---

# 22. Why the route surface stays small despite deep underlying complexity

The route file does not try to expose:

- every config source separately
- every merge step
- every plugin-install side effect
- every migration path

That is a good design choice.

The transport API stays focused on the few high-value operations clients actually need:

- get active config
- update config
- inspect configured providers

The deep complexity remains encapsulated in `Config`.

---

# 23. A representative config lifecycle

A typical client flow looks like this:

## 23.1 Retrieve active config

- `GET /config/`

## 23.2 Inspect active configured providers

- `GET /config/providers`

## 23.3 Modify settings

- `PATCH /config/`

## 23.4 Re-fetch active config

- confirm effective runtime state

This is a clean configuration control-plane loop.

---

# 24. Why this module matters beyond settings UX

Because config affects so many subsystems, the `/config` API is really part of the server’s runtime composition control plane.

It helps determine:

- what providers exist
- what plugins load
- what commands and agents appear
- what permissions and compaction policies apply

So documenting it as a first-class architecture module is justified.

---

# 25. Key design principles behind this module

## 25.1 Configuration should be surfaced as effective runtime state, not just raw file content

So `GET /config/` returns resolved `Config.Info`.

## 25.2 Config mutation should be validated and formalized through the API

So `PATCH /config/` uses the full schema and delegates to `Config.update()`.

## 25.3 Instance-bound runtime composition belongs to instance scope

So config is computed through `Instance.state(...)` and exposed through an instance route namespace.

## 25.4 Common derived views should be exposed for clients instead of recomputed everywhere

So `/config/providers` publishes active providers and default models directly.

---

# 26. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/routes/config.ts`
2. `packages/opencode/src/config/config.ts`
3. `packages/opencode/src/provider/provider.ts`
4. `packages/opencode/src/config/paths.ts`

Focus on these functions and concepts:

- `Config.get()`
- `Config.update()`
- `Config.state`
- config precedence order
- merge behavior for arrays
- `/config/providers`
- default model derivation

---

# 27. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: How exactly does `Config.update()` decide which file or source layer to write back to?
- **Question 2**: Should the config API eventually expose provenance information showing which source contributed each active value?
- **Question 3**: What config changes trigger downstream reloads or event emissions for clients already connected?
- **Question 4**: How do managed enterprise config layers interact with API-based config mutation when they override lower-priority values?
- **Question 5**: Should `/config/providers` surface why a provider is absent, disabled, or unresolved?
- **Question 6**: How does plugin installation/loading behavior interact with repeated config reloads and instance disposal?
- **Question 7**: Are there configuration fields today that are too coupled to internal implementation details to be ideal API-facing schema?
- **Question 8**: Should some config subdomains eventually get their own narrower route surfaces instead of living under one broad `Config.Info` schema?

---

# 28. Summary

The `config_routes_and_runtime_configuration_api` layer exposes the current instance’s effective runtime configuration as a formal control-plane surface:

- `GET /config/` returns resolved config after the full precedence and merge pipeline
- `PATCH /config/` makes configuration mutation a validated API operation
- `GET /config/providers` gives a provider-specific runtime view derived from active configuration and provider state

So this module is not just a settings endpoint. It is the API surface for OpenCode’s instance-scoped runtime composition system.
