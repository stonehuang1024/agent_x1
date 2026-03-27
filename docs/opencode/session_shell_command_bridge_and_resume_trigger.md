# Session Shell Command Bridge / Resume Trigger

---

# 1. Module Purpose

This document explains the `SessionPrompt.shell(...)` path, which bridges a direct shell command execution into OpenCode’s session state and optionally resumes the main session loop afterward.

The key questions are:

- Why does shell execution create user and assistant messages instead of only running a subprocess?
- How is a shell command represented as a synthetic tool execution inside the session model?
- How does OpenCode choose shell invocation semantics across shells and platforms?
- How are live command outputs streamed back into the session tool part?
- Why does shell completion sometimes cancel the session and sometimes resume the loop with `resume_existing: true`?

Primary source files:

- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/shell/shell.ts`
- `packages/opencode/src/session/status.ts`

This layer is OpenCode’s **shell-to-session bridge and post-command continuation trigger**.

---

# 2. Why this layer matters

A shell command in OpenCode is not treated as an external side action with no relation to the conversation.

Instead, it is recorded into the same durable session model that ordinary assistant work uses.

That means shell execution can participate in:

- session replay
- UI inspection
- tool-like observability
- subsequent loop continuation

This is a very strong state-first design choice.

---

# 3. `shell(...)` is guarded by the same busy-state model as the main loop

The function begins with:

- `const abort = start(input.sessionID)`

If no abort signal is returned, it throws `Session.BusyError`.

This is important.

Shell execution is not a side channel that bypasses session concurrency control.

It shares the same in-memory busy-state guard used by the main loop.

---

# 4. Why shell execution should participate in session busy control

A shell command can mutate workspace state and produce artifacts that the session loop may later reason about.

Allowing arbitrary concurrent shell runs on the same session would make causality and state tracking much harder.

So using the same busy-state guard is the correct design.

---

# 5. Deferred cleanup decides whether shell completion ends the session run or resumes it

The function installs a deferred cleanup block that checks:

- `const callbacks = state()[input.sessionID]?.callbacks ?? []`

If there are no queued callbacks:

- it calls `cancel(input.sessionID)`

Otherwise:

- it triggers `loop({ sessionID, resume_existing: true })`

This is one of the most important parts of the whole function.

Shell execution is treated as a bridge stage inside a larger resumable session workflow.

---

# 6. Why callbacks determine whether to resume

Queued callbacks mean something is waiting for further session output.

So after the shell command finishes, OpenCode does not simply end.

It resumes the main loop so that the newly recorded shell results can be processed into the next assistant continuation.

If nothing is waiting, there is no reason to resume automatically.

That is a very pragmatic continuation rule.

---

# 7. `resume_existing: true` is the key handoff back into the loop

The resumed call uses:

- `loop({ sessionID: input.sessionID, resume_existing: true })`

This matters because the loop can reuse the existing session’s abort state rather than trying to start a brand-new run blindly.

So shell execution is integrated into the same resumable loop lifecycle, not bolted on as a completely separate mode.

---

# 8. Shell execution cleans up revert state before running

Before launching the shell, the function loads the session and runs:

- `SessionRevert.cleanup(session)`

when needed.

This is important context.

Shell execution is not exempt from the session’s broader workspace-state hygiene rules.

It participates in the same revert-cleanup discipline as normal prompt execution.

---

# 9. Shell input still resolves agent and model context

The function resolves:

- current agent via `Agent.get(input.agent)`
- current model from explicit input, agent default, or `lastModel(sessionID)`

This is notable because the shell bridge is still anchored to the session’s agent/model semantics.

Even though the shell command is not a model call itself, it is recorded as part of a session turn with agent/model identity.

---

# 10. Why a shell command first creates a synthetic user message

The function persists a user message whose content context is represented by a synthetic text part:

- `The following tool was executed by the user`

This is a very important design choice.

Rather than treating shell execution as an invisible system action, OpenCode records it as if the user initiated a tool-backed action in the conversation.

That keeps it aligned with the rest of the message model.

---

# 11. Why the shell bridge uses the same narrative convention as subtasks

That synthetic user text mirrors similar text used elsewhere for subtask/tool-like user actions.

This suggests an intentional narrative convention in the message layer:

- user-triggered non-chat actions are represented as synthetic user statements about tool execution

That helps the model later understand what happened without needing a separate hidden event channel.

---

# 12. The shell result is stored as an assistant message with a running tool part

After the user-side synthetic marker, the function creates an assistant message and a `tool` part for:

- `tool: "bash"`
- `status: "running"`
- input `{ command: input.command }`

This is the core shell bridge.

A shell command becomes a standard tool execution record inside the assistant message model.

That is excellent architectural reuse.

---

# 13. Why shell is modeled as a tool part

OpenCode already has strong infrastructure for:

- running/completed/error tool states
- metadata updates
- output capture
- UI rendering of tool calls

By expressing shell execution as a tool part, the shell bridge can reuse all of those semantics instead of inventing a separate command-result schema.

---

# 14. Shell selection is delegated to `Shell.preferred()`

The bridge uses:

- `const shell = Shell.preferred()`

then derives a normalized shell name from the executable path.

This is important because actual shell launch behavior is delegated to a lower-level shell-selection policy rather than hardcoded entirely in the bridge.

So the bridge is responsible for orchestration, while shell choice itself is abstracted.

---

# 15. Invocation arguments differ by shell family

The function maintains an `invocations` table for:

- `nu`
- `fish`
- `zsh`
- `bash`
- `cmd`
- `powershell`
- `pwsh`
- fallback

This is a rich cross-shell compatibility layer.

Different shells need different argument conventions and initialization behavior.

The bridge handles that explicitly.

---

# 16. Why `zsh` and `bash` source startup files before running the command

For `zsh` and `bash`, the command string sources:

- shell environment files like `.zshenv`, `.zshrc`, `.bashrc`

before `eval` of the user command.

This is an important usability feature.

It means shell commands run in a closer approximation of the user’s normal interactive shell environment rather than a barren subprocess environment.

---

# 17. Why there is still a generic fallback invocation

Not every shell will match the explicitly named set.

So the bridge includes a compatibility fallback:

- `-c <command>`

This keeps the shell path broadly usable without hardcoding every possible shell implementation.

---

# 18. Shell environment is extensible through a plugin hook

Before spawning the subprocess, the function triggers:

- `Plugin.trigger("shell.env", ...)`

and merges returned env vars into the process environment.

This is a powerful extension seam.

It lets plugins shape shell execution context without modifying the bridge logic itself.

---

# 19. Why `TERM: "dumb"` is forced

The spawned process environment includes:

- `TERM: "dumb"`

This is a practical control measure.

It discourages interactive or richly formatted terminal behavior that would not fit well into captured session output.

For a tool-style shell bridge, plain output is usually the right target.

---

# 20. Output is streamed into tool metadata while the command runs

The function appends stdout and stderr chunks into a single `output` string.

While the tool part is still running, it updates:

- `part.state.metadata.output`
- `part.state.metadata.description`

and persists the updated part.

This is very important.

Shell output is not only recorded at the end. It is visible incrementally in the live tool part state.

---

# 21. Why stdout and stderr are merged

Both stdout and stderr are appended into one textual output stream.

For a conversational session record, this makes sense.

The important thing is usually the chronological combined command output rather than a strict separation of streams.

That provides a simpler artifact for later model reasoning.

---

# 22. Abort handling kills the full process tree

The bridge defines:

- `const kill = () => Shell.killTree(proc, { exited: () => exited })`

If the session aborts, it:

- marks `aborted = true`
- kills the process tree

This is a crucial robustness feature.

A shell bridge that only killed the immediate child could leak subprocesses and desynchronize session state from actual workspace activity.

---

# 23. Why abort state is also reflected in recorded output

If the shell run was aborted, the function appends:

- `<metadata>`
- `User aborted the command`
- `</metadata>`

into the captured output string.

This is consistent with the state-first architecture.

Abort is not only a control event. It also becomes part of the persisted tool-output narrative for later inspection.

---

# 24. Completion finalizes both assistant message and tool part

After the subprocess closes, the function:

- sets `msg.time.completed`
- persists the assistant message
- turns the running tool part into `status: "completed"`
- stores final timing, metadata, and full output

This yields a normal completed assistant/tool artifact pair in session state.

Again, shell results become first-class session artifacts.

---

# 25. Why shell completion is stored as completed even after user abort

Even if the command was aborted, the part still transitions through the normal finalized tool-result path with abort metadata in the output.

This gives the runtime a coherent terminal state instead of leaving a perpetual `running` or generic error record.

That is a good normalization decision.

---

# 26. The return value exposes the assistant-side shell result artifact

`shell(...)` returns:

- `{ info: msg, parts: [part] }`

This means callers receive the persisted assistant/tool representation of the shell run, not the synthetic user message and user part.

That makes sense because the assistant tool result is the main artifact of interest after command execution.

---

# 27. The shell route is exposed directly through the session API

The grep results show `server/routes/session.ts` calling:

- `SessionPrompt.shell({ ...body, sessionID })`

So this bridge is part of the formal external session API, not merely an internal helper.

That makes clear why its persistence and resumability semantics are so important.

---

# 28. A representative shell-bridge lifecycle

A typical lifecycle looks like this:

## 28.1 Shell run claims the session busy slot

- `start(sessionID)` succeeds

## 28.2 Synthetic user message is persisted

- indicates a user-triggered tool execution

## 28.3 Assistant message and running `bash` tool part are persisted

- command input recorded

## 28.4 Subprocess runs and streams output into tool metadata

- stdout/stderr merged into live output

## 28.5 Process exits or is aborted

- assistant message completes
- tool part becomes completed with final output

## 28.6 Deferred cleanup either cancels or resumes main loop

- no callbacks -> idle cleanup
- callbacks waiting -> `loop(... resume_existing: true)`

This is the shell-to-session bridge lifecycle.

---

# 29. Why this module matters architecturally

This layer shows how OpenCode refuses to treat shell execution as an untracked side effect.

Instead, it translates shell activity into the same durable message/part model that governs the rest of the session runtime, and then decides whether to continue the main loop based on waiting session demand.

That is a very coherent design for a coding agent system where shell actions are part of the reasoning and execution story.

---

# 30. Key design principles behind this module

## 30.1 External execution actions should be recorded as first-class session state, not as opaque side effects

So shell commands become synthetic user context plus assistant tool-part state.

## 30.2 Shell execution should share the same session concurrency and cancellation model as the rest of the runtime

So it uses the same busy-state guard, abort controller, and process-tree kill behavior.

## 30.3 Live command output should be incrementally observable while still producing a coherent terminal artifact

So output streams into tool metadata during execution and then finalizes into completed tool output.

## 30.4 Post-command continuation should depend on actual waiting demand from the session loop ecosystem

So callbacks determine whether the bridge ends in idle cleanup or resumes the loop with `resume_existing`.

---

# 31. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/session/prompt.ts`
2. `SessionPrompt.shell()`
3. `packages/opencode/src/shell/shell.ts`
4. `packages/opencode/src/server/routes/session.ts`

Focus on these functions and concepts:

- `shell()`
- deferred cleanup with callbacks
- synthetic user message creation
- assistant `bash` tool part creation
- per-shell invocation table
- `Plugin.trigger("shell.env", ...)`
- stdout/stderr metadata streaming
- abort handler and `Shell.killTree(...)`
- `loop({ resume_existing: true })`

---

# 32. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Should shell execution ever persist a distinct typed part separate from generic `tool: "bash"`, or is the current reuse of tool parts the right abstraction long-term?
- **Question 2**: How should exit codes be represented more explicitly in shell tool metadata or final output?
- **Question 3**: Are there cases where stdout and stderr should remain separated for better debugging fidelity?
- **Question 4**: How should shell output truncation or artifact offloading behave for extremely large command outputs?
- **Question 5**: What exact semantics do queued callbacks represent, and how often does the shell bridge actually trigger a resumed loop in practice?
- **Question 6**: Should shell startup file sourcing be configurable per environment for users who want more deterministic subprocess behavior?
- **Question 7**: How should shell bridge behavior differ in remote or containerized environments with different shell expectations?
- **Question 8**: What tests best guarantee that aborted shell subprocess trees are fully cleaned up while leaving coherent session artifacts behind?

---

# 33. Summary

The `session_shell_command_bridge_and_resume_trigger` layer turns direct shell execution into durable session state and then decides whether the broader conversation should continue:

- it records a synthetic user-triggered tool execution followed by an assistant-side `bash` tool part
- it streams subprocess output into live tool metadata and finalizes a completed tool artifact on exit
- it uses the same session busy/cancel model as the main loop
- it either returns the session to idle or resumes the loop with `resume_existing` depending on whether callbacks are waiting

So this module is the bridge that makes shell execution part of OpenCode’s conversational runtime rather than an untracked external side channel.
