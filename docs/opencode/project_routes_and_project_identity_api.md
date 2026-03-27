# Project Routes / Project Identity API

---

# 1. Module Purpose

This document explains the `/project` route namespace and how it exposes project identity, project discovery, and project-level metadata mutation in OpenCode.

The key questions are:

- Why does OpenCode model `Project` as a first-class runtime object?
- What is the difference between project identity and instance identity?
- How do `server/routes/project.ts` and `project/project.ts` divide responsibilities?
- Why does git initialization belong under the project control plane?
- What does this route surface reveal about how OpenCode tracks repositories, sandboxes, and long-lived project metadata?

Primary source files:

- `packages/opencode/src/server/routes/project.ts`
- `packages/opencode/src/project/project.ts`
- `packages/opencode/src/project/instance.ts`
- `packages/opencode/src/project/bootstrap.ts`

This layer is OpenCode’s **project identity and project-metadata control-plane API**.

---

# 2. Why `/project` exists separately

A project is not the same thing as:

- a session
- a PTY
- a workspace
- a single bound instance

Project identity sits underneath many other subsystems.

It answers questions like:

- what repository or root worktree is this directory part of?
- what long-lived metadata has OpenCode stored for it?
- what sandboxes belong to it?
- what name, icon, or startup commands are attached to it?

That is why `/project` deserves its own route namespace.

---

# 3. Project identity versus instance identity

A useful distinction is:

## 3.1 Instance

- current runtime binding for a request
- tied to a concrete directory/worktree context
- hosts instance-scoped state like sessions, PTYs, pending permissions, and config

## 3.2 Project

- longer-lived identity for the underlying repository or global context
- can span multiple sandboxes/worktrees
- stores metadata like name, icon, commands, and sandbox inventory

This distinction is central to understanding why OpenCode has both `Instance` and `Project` abstractions.

---

# 4. The route surface is concise but foundational

`server/routes/project.ts` exposes:

- `GET /project/`
- `GET /project/current`
- `POST /project/git/init`
- `PATCH /project/:projectID`

This is a small API, but it sits on top of a very important concept:

- persistent project identity across instances and worktrees

---

# 5. `GET /project/`: list known projects

The list route returns:

- `Project.list()`

as `Project.Info.array()`.

This means OpenCode maintains a durable inventory of projects it has seen or opened, not merely the currently active one.

That is useful for:

- recent project UIs
- dashboards
- project switching
- higher-level operator tooling

---

# 6. Why project listing is different from instance discovery

Instance context is request-local and current.

Project listing is historical and persistent.

It gives a wider view of what the OpenCode runtime has tracked over time.

That is why it belongs in a distinct project namespace instead of being folded into `/path` or instance routes.

---

# 7. `GET /project/current`: current project as a first-class response

The current project route returns:

- `Instance.project`

This is a very practical control-plane route because it gives clients direct access to the currently bound project identity without forcing them to reconstruct it from lower-level path or VCS endpoints.

It is the project-level analog of “what am I currently attached to?”

---

# 8. Why current project is not just path information

A project contains more than a directory path.

`Project.Info` includes:

- `id`
- `worktree`
- optional `vcs`
- optional `name`
- optional `icon`
- optional `commands`
- `time`
- `sandboxes`

So `GET /project/current` gives a semantic project object, not just filesystem coordinates.

That is much more useful to clients.

---

# 9. `Project.Info`: what OpenCode thinks a project is

The core schema shows that a project is a durable metadata object with:

- stable ID
- current root worktree
- VCS classification
- user-facing metadata
- time fields
- sandbox/worktree inventory
- optional command metadata

This is a strong sign that OpenCode treats projects as first-class durable resources, not as transient request context.

---

# 10. Sandboxes are part of project identity

One especially revealing field is:

- `sandboxes: string[]`

This indicates a project can own multiple sandbox directories or worktree variants over time.

That matters because it means the project object is the umbrella entity over:

- main worktree
- sandbox worktrees
- related operator workflows

This is a higher-level concept than a single directory binding.

---

# 11. `POST /project/git/init`: git initialization as a project lifecycle step

The git-init route does more than run a git command.

It:

- reads the current `Instance.directory`
- reads the previous `Instance.project`
- calls `Project.initGit({ directory, project })`
- compares the returned project identity to the previous one
- may reload the current instance via `Instance.reload(...)`

This is a very important route because it shows git initialization can change project identity and therefore requires instance-level rebinding.

---

# 12. Why git init belongs under project control rather than a generic shell route

Git repository initialization is not just arbitrary command execution.

It changes how OpenCode understands the current directory structurally.

After git init, the directory may:

- gain VCS identity
- gain a stable project ID
- become associated with a different worktree/root model

That is exactly why this operation belongs under `/project` rather than being left to a shell command side effect.

---

# 13. Instance reload after git init is the key architectural detail

If `Project.initGit(...)` returns materially different project state, the route calls:

- `Instance.reload({ directory, worktree: directory, project: next, init: InstanceBootstrap })`

This is critical.

It shows that project identity is not passive metadata.

Changes to project identity can require full instance rebinding and bootstrap.

That is a major architectural fact.

---

# 14. Why the route compares old and new project info first

The route only reloads when key fields changed:

- `id`
- `vcs`
- `worktree`

This is good lifecycle discipline.

It avoids unnecessary reload churn while still ensuring that meaningful project-identity changes are reflected in instance state.

That is a principled optimization.

---

# 15. `PATCH /project/:projectID`: project metadata mutation

The update route validates:

- path param `projectID`
- request body using `Project.update.schema.omit({ projectID: true })`

Then it calls:

- `Project.update({ ...body, projectID })`

This exposes project metadata editing as a formal API capability.

---

# 16. Why project update is important

Projects carry user-meaningful metadata like:

- `name`
- `icon`
- `commands`

Those are not merely computed from git.

They are part of the persistent user-facing project model.

So the control plane needs a proper way to mutate them.

That is what this route provides.

---

# 17. `Project.fromDirectory(...)`: how project identity is discovered

The core project module reveals the deeper logic behind project identity.

It tries to discover:

- `.git` ancestry
- common git dir versus sandbox/worktree path
- cached OpenCode project ID
- fallback root-commit-based project ID
- top-level worktree root

This is sophisticated identity resolution.

Project identity is therefore not just “directory path hashed somehow.”

It is tied to VCS topology and worktree semantics.

---

# 18. Why root commit–derived identity is a strong design choice

When no cached ID exists, `Project.fromDirectory(...)` derives the project ID from:

- sorted root commits from `git rev-list --max-parents=0 HEAD`

This is a strong choice because it gives related worktrees of the same repository a shared stable identity.

That is much better than making each worktree directory a completely separate project.

---

# 19. Why cached IDs are written into `.git/opencode`

When project identity is derived, the code writes a cache file into:

- `.git/opencode`

and in the worktree case writes to the common git dir so the cache is shared.

This is a clever persistence strategy.

It stabilizes project identity across sessions and related worktrees without needing a more intrusive repository change.

---

# 20. Worktree handling is one of the most important project-level behaviors

The project resolver distinguishes between:

- the current sandbox or top-level path
- the common git worktree root

It then tracks sandboxes separately from the primary worktree.

This is essential for OpenCode because worktrees and sandboxes are not peripheral features.

They are part of the project model itself.

---

# 21. Non-git fallback behavior also matters

If git information is unavailable or unresolved, the project module can fall back to:

- `ProjectID.global`
- fake or optional VCS markers via flags
- root `/` worktree in some non-git cases

This shows the project model is designed to function even when a directory is not part of a normal git repository.

That makes the system more broadly usable.

---

# 22. Project persistence is database-backed

The module reads and writes project rows through:

- `ProjectTable`
- database queries in `storage/db`

This confirms that projects are durable control-plane resources, not just recomputed transiently every request.

The route surface is therefore backed by real persisted state.

---

# 23. Project updates are also event-worthy

`Project.Event.Updated` is defined as:

- `project.updated`

This indicates project mutation is part of the observable runtime model, not just silent database state.

Even though the route file itself is thin, the underlying project model fits into OpenCode’s larger event-driven architecture.

---

# 24. Why project metadata matters to the user experience

Fields like:

- project name
- icon
- commands.start
- sandbox inventory

support richer product behaviors such as:

- project cards or switchers
- branded project identity in UI
- workspace/worktree startup flows
- environment-specific launch behavior

So `/project` is not just for infrastructure bookkeeping.

It supports product-level experience too.

---

# 25. A representative project lifecycle through the API

A typical flow could look like this:

## 25.1 List known projects

- `GET /project/`

## 25.2 Inspect the currently bound project

- `GET /project/current`

## 25.3 Initialize git if the current directory is not yet a repository

- `POST /project/git/init`

## 25.4 Update user-facing metadata

- `PATCH /project/:projectID`

This is a coherent project identity and lifecycle management surface.

---

# 26. Why this module matters architecturally

The `/project` API reveals that OpenCode’s model of “where work happens” is layered:

- request binds to an instance
- instance points at a project
- project can own multiple sandboxes/worktrees and persistent metadata

That is a richer architecture than simply “everything is just the current cwd.”

This route family is the public expression of that deeper model.

---

# 27. Key design principles behind this module

## 27.1 Project identity should be durable across related worktrees and sessions

So project IDs are derived from git structure and cached in shared git metadata.

## 27.2 Project-level metadata belongs above instance-local runtime state

So project routes expose current project, project listing, and project updates separately from instance routes.

## 27.3 Structural repository changes should trigger runtime rebinding when necessary

So git initialization can cause `Instance.reload(...)`.

## 27.4 Sandboxes and worktrees are part of project identity, not just incidental filesystem details

So project info explicitly tracks sandbox directories.

---

# 28. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/server/routes/project.ts`
2. `packages/opencode/src/project/project.ts`
3. `packages/opencode/src/project/instance.ts`
4. `packages/opencode/src/project/bootstrap.ts`

Focus on these functions and concepts:

- `Project.list()`
- `Instance.project`
- `Project.initGit()`
- `Instance.reload()`
- `Project.update()`
- `Project.fromDirectory()`
- root commit–derived project identity
- sandbox tracking

---

# 29. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: How exactly does `Project.initGit()` perform repository initialization and what edge cases does it handle?
- **Question 2**: When project metadata changes, which downstream clients or event subscribers are expected to react?
- **Question 3**: How should project identity behave for unusual repository layouts like submodules, nested repos, or detached worktrees?
- **Question 4**: Should the project route surface expose more direct sandbox/worktree management, or is it better kept separate under experimental/operator APIs?
- **Question 5**: How stable is root-commit-based identity across repository rewrites or imported histories?
- **Question 6**: Should `GET /project/current` expose more explicit information about how identity was resolved?
- **Question 7**: How does project persistence interact with deleted directories or repositories that move on disk?
- **Question 8**: Should project command metadata eventually be linked more tightly to workspace/worktree creation flows in the public API?

---

# 30. Summary

The `project_routes_and_project_identity_api` layer exposes OpenCode’s durable project model as a first-class control-plane surface:

- it lets clients list known projects and inspect the current bound project
- it treats git initialization as a structural project lifecycle action that may require instance reload
- it allows project-level metadata mutation for names, icons, and commands
- it reflects a deeper project identity model based on git topology, shared worktree identity, and sandbox tracking

So this module is not just project metadata CRUD. It is the API surface for how OpenCode understands and persists the identity of the codebase environments it operates on.
