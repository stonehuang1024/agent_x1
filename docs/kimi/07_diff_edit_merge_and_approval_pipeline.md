# Kimi Code CLI: Diff Display, Edit Execution, Merge Semantics, and Approval Pipeline

## 1. Executive Summary

Kimi Code CLI does not treat file editing as a blind side effect. Instead, its editing pipeline is built around a consistent sequence:

1. compute the prospective new file content
2. generate structured diff display blocks
3. request approval when required
4. only then persist the change
5. return structured edit information back to both the user-facing UI and the agent loop

This is one of the most important safety and UX strengths in the project.

## 2. The Main Files Involved

The main implementation points are:

- `src/kimi_cli/tools/file/write.py`
- `src/kimi_cli/tools/file/replace.py`
- `src/kimi_cli/utils/diff.py`
- `src/kimi_cli/tools/display.py`
- `src/kimi_cli/soul/approval.py`
- `src/kimi_cli/acp/convert.py`

Together, these files define how edits are represented, surfaced, approved, and executed.

## 3. Two Main File-Editing Tools

The repository exposes two main built-in editing primitives.

## 3.1 `WriteFile`

`WriteFile` supports:

- overwriting a file
- appending to a file

The tool computes:

- old file content if present
- new target content
- diff blocks between old and new

Then it may request approval, unless special plan-mode rules apply.

## 3.2 `StrReplaceFile`

`StrReplaceFile` supports:

- one or more string-based edits
- single replacement or replace-all behavior

It reads the current file, applies all requested edits in memory, checks whether any change actually occurred, computes diff blocks, requests approval, and only then writes the modified file.

This is the more surgical edit tool.

## 4. The Core Representation: `DiffDisplayBlock`

Structured diff display is defined in `src/kimi_cli/tools/display.py`.

A `DiffDisplayBlock` contains:

- `path`
- `old_text`
- `new_text`

This is important because edit presentation is not just a flat string diff log.

The runtime preserves edit semantics in a structured display object that different frontends can render appropriately.

## 5. Diff Generation Utilities

The diff helpers live in `src/kimi_cli/utils/diff.py`.

Two important functions are:

- `format_unified_diff(...)`
- `build_diff_blocks(...)`

## 5.1 `format_unified_diff(...)`

This produces a classical unified diff string.

It is useful when a textual diff representation is needed.

## 5.2 `build_diff_blocks(...)`

This function uses `difflib.SequenceMatcher` and groups changes with a small context window.

It produces multiple `DiffDisplayBlock` objects, each containing a local before/after region rather than the entire file.

This is the more important function for the runtime’s approval and UI flow.

## 6. Why Grouped Diff Blocks Are Better Than Full-File Replacement Views

Grouped diff blocks are a smart design choice because they:

- reduce noise for large files
- keep approval prompts readable
- allow frontends to focus on changed regions
- avoid flooding the UI with unrelated unchanged content

This makes approval practical in real coding workflows.

## 7. Editing Workflow in `WriteFile`

`WriteFile` follows a careful workflow.

## 7.1 Validate path

It first validates that the target path is safe:

- inside workspace or additional dirs
- otherwise requires absolute path

## 7.2 Special handling in plan mode

If plan mode is active and the write target is exactly the designated plan file, the write can be auto-approved.

This is a targeted exception to the general safety model.

It is a good example of how plan-mode policy is distributed into tool behavior.

## 7.3 Compute old and new text

If the file already exists, `WriteFile` reads old content.

It then computes the final target content depending on mode:

- overwrite → new content is the provided content
- append → new content is old content plus appended content

## 7.4 Generate diff blocks before any write

This is crucial: the runtime computes the change representation *before* mutating the file.

That allows approval and UI display to be based on the exact proposed change.

## 7.5 Request approval if needed

If the write is not the special auto-approved plan file case, `WriteFile` asks the approval subsystem for permission.

The approval request includes:

- sender/tool name
- action type
- textual description
- diff display blocks

## 7.6 Perform the write only after approval

Only after approval is granted does the tool write to disk.

That sequencing is exactly what you want in a human-in-the-loop coding agent.

## 8. Editing Workflow in `StrReplaceFile`

The replace tool has a similar shape but with a more surgical edit model.

## 8.1 Read file and apply edits in memory

It loads the file content and applies one or more replacements to an in-memory string.

## 8.2 Detect no-op edits

If the resulting content is identical to the original, the tool returns an error explaining that no replacement was made.

This is important because it prevents the agent from thinking it changed something when it actually did not.

## 8.3 Generate diff blocks

Again, the diff is computed before writing.

## 8.4 Request approval

Approval is requested with the diff display blocks.

## 8.5 Persist edit after approval

Only after approval does the tool write the new content back.

This is consistent with the `WriteFile` safety pipeline.

## 9. The Approval Subsystem

The approval engine is implemented in `src/kimi_cli/soul/approval.py`.

Its job is to mediate tool actions that may require user permission.

## 9.1 Approval state

The approval state tracks:

- `yolo`
- `auto_approve_actions`
- optional persistence callback

This allows session-level approval preferences to survive across runs.

## 9.2 Approval request lifecycle

When a tool requests approval:

1. the request is validated to ensure it originated from a tool call context
2. if yolo is active, approval is granted immediately
3. if the action has been auto-approved for the session, approval is granted immediately
4. otherwise a request object is created and queued
5. the caller awaits the future

This is a clean asynchronous approval model.

## 10. Why Approval Is Tied to Tool-Call Context

Approval can only be requested from inside a tool call because the approval request needs:

- tool call ID
- sender identity
- action classification
- display metadata tied to that action

This ensures approvals are attributable and structured.

That is much better than arbitrary runtime code asking for vague global permission.

## 11. Action Classification Matters

Tools pass action labels such as edit operations into approval.

This matters because `approve_for_session` stores action names in `auto_approve_actions`.

That means the system can remember “approve all future edits of this action type in this session” without turning on global yolo mode.

This is a well-designed middle ground between:

- approve everything forever
- prompt for every tiny edit

## 12. How Approval Reaches the UI

Inside the soul loop, approval requests are forwarded over `Wire`.

The runtime converts internal approval requests into `wire.types.ApprovalRequest` objects and emits them.

The frontend then resolves them and sends back:

- approve
- approve for session
- reject

The approval subsystem then resolves the original future accordingly.

This is a strong decoupling pattern.

## 13. Approval in ACP / IDE Clients

In ACP mode, approval requests are turned into ACP permission requests.

The client can show:

- approve once
- approve for session
- reject

And the diff blocks can be converted into protocol-native file-edit content.

So approval is not only a terminal feature; it is a protocol-level behavior.

## 14. How Diffs Reach ACP Clients

`display_block_to_acp_content(...)` converts `DiffDisplayBlock` into `FileEditToolCallContent`.

This means IDE clients can render edits using structured before/after content rather than plain logs.

That is important because editor users expect file-edit semantics, not just a terminal transcript.

## 15. Merge Semantics: What “Merge” Means Here

The repository does not appear to implement a full Git-style three-way merge engine as a first-class agent feature in the examined core editing path.

So the safest interpretation of “merge” in this codebase is:

- the runtime computes old/new text deltas
- presents localized diff blocks
- writes the approved result as the new file content

In other words, merge here is primarily **edit reconciliation at the file-content level**, not a dedicated SCM merge engine.

## 16. Append Mode as a Simple Merge-Like Operation

`WriteFile` in append mode is one simple merge-like behavior.

It takes:

- previous file content
- new append content
- concatenates them into a final target text

Then it diff-checks and requests approval.

This is not semantic merge, but it is still a controlled content combination path.

## 17. Replace Mode as Patch-Like Local Merge

`StrReplaceFile` is another merge-like mechanism.

It applies local textual patches to existing content, then surfaces the resulting delta.

Again, not a full SCM merge engine, but still a structured patch workflow.

## 18. Why This Editing Model Is Good for Agents

This editing model has several strong properties.

- It is deterministic.
- It is explainable.
- It exposes human-reviewable diffs before mutation.
- It works consistently across shell and ACP clients.
- It integrates naturally with the approval system.

For an autonomous coding agent, these are exactly the right primitives.

## 19. What the Agent Sees After an Edit

After a tool runs, the result is returned as a `ToolReturnValue`.

That result may contain:

- a short message
- display blocks, including diff blocks
- output content

For the LLM loop, tool results are later converted into `tool` messages and appended into context.

So the agent can reason about the result of the edit in subsequent steps.

That is important because edit execution is not invisible to the loop.

## 20. What the Human Sees After an Edit

The user-facing side may see:

- diff display blocks
- approval dialogs with before/after snippets
- tool success/failure messages
- ACP file-edit content in IDE clients

This means the edit pipeline has dual consumers:

- the LLM loop
- the human UI/protocol client

And the architecture serves both.

## 21. Safety and UX Strengths

The strongest strengths of this pipeline are:

- proposed changes are computed before writing
- edits are represented structurally
- approvals can include localized diffs
- session-level auto-approval is supported
- plan-mode exceptions are narrowly scoped
- the same diff artifacts can be reused across frontends

This is a very solid design for an engineering agent.

## 22. Limitations and Open Questions

There are also limitations.

- The edit model is mostly string/file based, not AST-aware.
- No first-class three-way merge engine is evident here.
- Replace operations are purely textual and may be brittle if the target text changes unexpectedly.
- There is no clearly visible conflict-resolution engine beyond approval and tool errors in the examined path.

These are reasonable trade-offs for a general-purpose CLI agent, but they are important to note.

## 23. Core Architectural Insight

The most important insight is:

Kimi Code CLI treats file edits as **first-class reviewable artifacts**, not just side effects.

That is implemented through three cooperating abstractions:

- diff builders
- display blocks
- asynchronous approval mediation

This makes the system much safer and more understandable than an agent that simply rewrites files and prints “done.”

## 24. Final Summary

Kimi Code CLI’s diff/edit/approval pipeline is one of the strongest parts of the architecture.

The core sequence is:

- compute candidate file content
- derive localized diff blocks
- request approval when necessary
- persist only after approval
- return structured change information to both runtime and UI layers

This means the project already has a solid foundation for trustworthy code-editing workflows, even without a full semantic merge engine.
