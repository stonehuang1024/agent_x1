# Session `createUserMessage()` / Part Materialization

---

# 1. Module Purpose

This document explains how incoming prompt input is turned into a durable user message plus persisted parts, focusing on `createUserMessage()` inside `session/prompt.ts`.

The key questions are:

- How does `PromptInput` define the ingress contract for new user turns?
- How does `createUserMessage()` choose agent, model, and variant metadata for the new user message?
- How are different part types materialized into persisted `MessageV2.Part` records?
- Why are file and MCP resource inputs expanded into multiple synthetic parts instead of stored verbatim only?
- What does this ingress path reveal about how OpenCode converts high-level user intent into durable runtime state?

Primary source files:

- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/message-v2.ts`
- `packages/opencode/src/tool/read.ts`
- `packages/opencode/src/mcp/index.ts`

This layer is OpenCode’s **prompt-ingress and user-part materialization pipeline**.

---

# 2. Why this layer matters

Most later session logic begins by reading persisted messages and parts.

That means `createUserMessage()` is the point where abstract prompt input becomes the durable session state the rest of the runtime will reason over.

If you want to understand:

- how user files enter context
- how agent mentions become task instructions
- how MCP resources become readable context
- how format and system options are attached to a turn

this is the authoritative ingress function.

---

# 3. `PromptInput` is the public ingress contract

`SessionPrompt.PromptInput` includes fields such as:

- `sessionID`
- optional `messageID`
- optional `model`
- optional `agent`
- optional `noReply`
- optional `tools`
- optional `format`
- optional `system`
- optional `variant`
- `parts`

This is an important design point.

A user turn is not just a string prompt. It is a structured input object with message metadata, model-selection hints, formatting requirements, and typed parts.

---

# 4. Why `parts` are a discriminated union

The input `parts` array supports several input types directly:

- text parts
- file parts
- agent parts
- subtask parts

This means OpenCode’s ingress path already assumes the user turn may contain a structured mixture of:

- plain text
- attached files or directories
- explicit agent mentions
- explicit subtask requests

That is much richer than a traditional chat-input model.

---

# 5. `createUserMessage()` first resolves agent and model defaults

At the start of the function, it resolves:

- `agent` from explicit input, or default agent
- `model` from explicit input, agent default, or `lastModel(sessionID)`

This is important because a user turn always needs a concrete runtime execution context even if the caller did not specify one fully.

The ingress path is where missing selection details are filled in.

---

# 6. Variant selection is conditional and model-aware

The function computes a `variant` using:

- explicit input variant if present
- otherwise the agent’s default variant if the resolved model actually supports it

That second condition is very important.

OpenCode does not blindly carry an agent variant into the message. It first verifies that the resolved full model metadata exposes that variant.

This is a good compatibility check at ingress time.

---

# 7. The persisted message info already carries execution metadata

The new user message `info` includes:

- message ID
- role
- session ID
- creation time
- tool preferences
- agent name
- model selection
- optional system override
- optional format
- variant

This is a major design clue.

The user message itself is not just content. It is the durable state record of:

- what the user asked
- under which agent/model context the turn should run
- what output format constraints apply

That is why later loop logic can recover state without needing the original request object.

---

# 8. Instruction state is also cleaned around user-message creation

The function uses:

- `defer(() => InstructionPrompt.clear(info.id))`

This is subtle but important.

It shows that instruction-resolution state may be associated with a message ID and needs cleanup around message materialization.

So even ingress has side-channel state hygiene concerns beyond pure message persistence.

---

# 9. Part IDs are assigned late through a normalization helper

The function defines a local `assign(...)` helper that:

- preserves an existing part ID if provided
- otherwise generates a new ascending `PartID`

This is a nice normalization step.

The earlier materialization logic can work with draft parts, and final durable identity is assigned in one place before persistence.

That keeps the transformation pipeline cleaner.

---

# 10. Materialization is per-input-part, but each input part can expand into many persisted parts

This is one of the most important architectural facts in the whole function.

For each incoming input part, `createUserMessage()` returns:

- one or more draft persisted parts

Then it flattens them.

So the mapping is not:

- one input part -> one persisted part

Instead it is:

- one input part -> one or many persisted parts

That is exactly how OpenCode turns high-level user conveniences into richer internal context state.

---

# 11. MCP resource file inputs get special handling first

For file parts whose `source.type === "resource"`, the function takes a dedicated MCP resource path before looking at URL protocol.

This is important.

MCP resources are not treated as ordinary files. They are external capability-backed resources that need to be read through:

- `MCP.readResource(clientName, uri)`

So ingress understands MCP-backed files as a different class of input.

---

# 12. MCP resources are expanded into synthetic explanatory text plus the file part

For MCP resources, the function begins by adding a synthetic text part like:

- `Reading MCP resource: ...`

Then it attempts to read the actual resource content.

Depending on the returned content, it may append:

- synthetic text parts for textual contents
- synthetic placeholder text for binary contents
- the original file part itself

This is a very important design choice.

The user turn does not just reference the resource. It embeds a readable trace of resource ingestion into session state.

---

# 13. Why MCP resource expansion uses synthetic text parts

The runtime wants later model turns to understand what resource was read and what content came from it.

Embedding synthetic text parts means the resource read becomes part of the normal conversation state rather than a hidden side effect.

That improves:

- observability
- replayability
- context continuity

It is a strong state-first design.

---

# 14. MCP resource failures are also materialized into state

If MCP resource reading fails, the function logs the error and appends a synthetic text part describing the failure.

This is another important property.

Failure is not only logged externally. It is recorded into the conversation context so the agent can reason about the failure in subsequent turns.

---

# 15. `data:` URLs with `text/plain` are treated as pre-read file content

If a file part has a `data:` URL and MIME type `text/plain`, the function expands it into:

- a synthetic text part saying the Read tool was called
- a synthetic text part containing the decoded data URL text
- the original file part

This is very revealing.

Even when content is already inline as a data URL, the runtime still frames it as if a Read-tool-like action happened.

That keeps the resulting message state aligned with the user-visible mental model of file reading.

---

# 16. Why synthetic “Called the Read tool” text appears so often

Across multiple branches, the function adds synthetic text like:

- `Called the Read tool with the following input: ...`

This is not accidental verbosity.

It is a deliberate normalization strategy.

The runtime wants file ingestion to look like a readable action trace inside the message stream, even when the file was supplied directly rather than read interactively later.

That helps the model and future readers understand how file content entered context.

---

# 17. `file:` URLs are treated as local filesystem materialization requests

For `file:` URLs, the function:

- normalizes the path through `fileURLToPath(...)`
- stats it through `Filesystem.stat(...)`
- branches by whether it is a directory or file

This means a file part can really be a pointer into the local filesystem that ingress dereferences immediately into richer message state.

---

# 18. Directories are retyped as `application/x-directory`

If the stat says the path is a directory, the function changes:

- `part.mime = "application/x-directory"`

This is a small but important normalization.

It ensures later logic can reason about directory inputs explicitly rather than relying on ambiguous original MIME guesses.

---

# 19. Plain-text local files trigger real `ReadTool` execution during materialization

For `file:` inputs with `text/plain`, the function builds ReadTool arguments and actually executes:

- `ReadTool.init().then(...execute(args, readCtx))`

This is a major architectural fact.

Ingress does not just attach the file reference. It proactively reads the file and stores the output as synthetic text context.

That means the agent gets the readable file content immediately in session state.

---

# 20. Why real `ReadTool` execution at ingress is powerful

This keeps the context model consistent with later interactive file-reading behavior.

Instead of having one representation for “user attached file” and another for “agent used read tool,” OpenCode collapses both into a similar action-trace plus content model.

That is a strong unification strategy.

---

# 21. LSP ranges in file URLs are resolved during materialization

When the file URL has query params like:

- `start`
- `end`

`createUserMessage()` interprets them as a range and may call:

- `LSP.documentSymbol(...)`

to refine symbol ranges when an LSP server returned incomplete range information.

This is a sophisticated ingress feature.

It means a file attachment can point not just to a file but to a specific code region, and the system will try to normalize that region meaningfully before reading.

---

# 22. Why symbol-range normalization matters

If symbol search gives only a shallow or degenerate range, the raw attachment would be less useful.

By resolving fuller symbol ranges at ingress, the session state becomes better aligned with what the user likely meant when attaching a symbol result.

That is a high-quality context-materialization behavior.

---

# 23. Read-tool results may contribute attachments, not just text

After executing `ReadTool`, the function:

- appends a synthetic text part with `result.output`
- appends any returned attachments as synthetic parts if present
- otherwise appends the original file part

This is important.

The materialization path preserves richer file representations when the read tool produces them, such as image/PDF attachments.

So ingress can become multimodal even when starting from a local file reference.

---

# 24. File-read failures are surfaced both on the bus and in session state

If `ReadTool` fails, the function:

- logs the error
- publishes `Session.Event.Error`
- appends a synthetic text part describing the read failure

This is good dual-path error handling.

Operators and UI consumers can observe the error externally, while the conversation state also records it for the agent.

---

# 25. Directory inputs are materialized through the read tool too

For `application/x-directory`, the function builds args with:

- `{ filePath: filepath }`

and executes `ReadTool` again, then stores:

- synthetic read-tool call text
- synthetic text result output
- the original directory file part

This is interesting because directories are normalized through the same read-oriented tool path instead of a separate custom directory renderer inside ingress.

That keeps behavior unified.

---

# 26. Binary or non-text file inputs are converted into data URLs

For non-text, non-directory local file inputs, the function:

- records a synthetic read-tool call text
- reads bytes with `Filesystem.readBytes(filepath)`
- stores a `file` part whose URL becomes a base64 `data:` URL

This is a critical transformation.

It makes the session state self-contained with respect to file content rather than relying on the original filesystem pointer later.

---

# 27. Why converting files into data URLs is important

A durable conversation state should not depend too heavily on the original local file path staying readable forever.

By materializing binary content into a `data:` URL, OpenCode captures the content payload directly into message state.

That improves reproducibility and replay of the session context.

---

# 28. Agent parts are rewritten into explicit task guidance

If the input part is of type `agent`, the function returns:

- the original agent part
- plus a synthetic text part instructing the runtime to use the above context and call the task tool with the named subagent

This is one of the most important ingress behaviors.

An agent mention is not just metadata. It is immediately expanded into a task-orchestration instruction in conversation state.

---

# 29. Why agent-part expansion is useful

The later loop and processor do not need to infer the user’s intention from the bare fact that an agent part existed.

The ingress function translates that mention into explicit, model-readable execution intent.

That is a great example of front-loading semantic normalization.

---

# 30. Permission-aware hinting is embedded into agent-part expansion

When expanding an agent part, the function evaluates:

- `PermissionNext.evaluate("task", part.name, agent.permission)`

and may append a hint if the task permission would otherwise be denied.

This is subtle but important.

Ingress already knows enough about the permission model to shape the synthetic guidance text accordingly.

That helps keep the model’s planning aligned with what is actually allowed.

---

# 31. All other part types are persisted mostly as-is

For part types that do not need special expansion, the function simply attaches:

- `messageID`
- `sessionID`

and keeps the rest of the part structure.

This is the default path.

So the special handling is targeted, not universal.

---

# 32. Materialized parts pass through a plugin hook before persistence

Before writing anything, `createUserMessage()` triggers:

- `Plugin.trigger("chat.message", ..., { message: info, parts })`

This is a major extensibility point.

The finalized message info and materialized parts can still be observed or transformed by plugins before they are committed.

That means ingress is not a closed hardcoded pipeline.

---

# 33. Persistence happens message-first, then part-by-part

After plugin triggering, the function:

- `Session.updateMessage(info)`
- then `Session.updatePart(part)` for each part

This is the concrete durability path.

The message record is established first, then its parts are written.

That matches the data model cleanly.

---

# 34. The function returns the persisted shape, not just raw input echo

The returned value is:

- `{ info, parts }`

where `parts` are already the fully materialized, assigned-ID persisted part shapes.

So callers receive the normalized session-state view of the turn, not merely their original `PromptInput` echoed back.

That is exactly what downstream runtime code wants.

---

# 35. A representative ingress flow

A typical flow might look like this:

## 35.1 Caller submits `PromptInput`

- includes session ID, maybe model/agent hints, and typed parts

## 35.2 `createUserMessage()` resolves execution metadata

- concrete agent
- concrete model
- compatible variant

## 35.3 Each input part is expanded as needed

- MCP resource -> synthetic explanatory text + fetched content + file part
- local text file -> synthetic Read-tool trace + read output + attachment/file part
- directory -> synthetic read trace + listing output + directory part
- agent mention -> agent part + synthetic task instruction

## 35.4 Plugins observe the fully materialized turn

- `chat.message`

## 35.5 Message and parts are persisted into session state

- durable conversation state is now ready for the loop

This is the actual user-turn materialization pipeline.

---

# 36. Why this module matters architecturally

`createUserMessage()` reveals a major OpenCode design principle:

- user input should be normalized into durable, semantically rich state as early as possible

Rather than leaving file references, resource pointers, or agent mentions as opaque tokens for later stages to interpret, ingress expands them into:

- readable action traces
- explicit synthetic context
- normalized attachment representations
- concrete metadata-rich message state

That makes the rest of the runtime simpler and more replayable.

---

# 37. Key design principles behind this module

## 37.1 User turns should enter the runtime as structured state, not as raw request blobs

So `PromptInput` is a typed object and `createUserMessage()` persists rich `info` plus materialized parts.

## 37.2 High-level user conveniences should be expanded into explicit conversational context before the main loop runs

So files, directories, MCP resources, and agent mentions are converted into synthetic explanatory/readable parts.

## 37.3 Durable session state should capture both content and the action trace by which that content entered the conversation

So synthetic `Called the Read tool ...` parts are stored alongside the resulting content.

## 37.4 Ingress should remain extensible and observable

So the materialized message passes through the `chat.message` plugin hook before persistence.

---

# 38. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/session/prompt.ts`
2. `createUserMessage()`
3. `SessionPrompt.PromptInput`
4. `packages/opencode/src/tool/read.ts`
5. `packages/opencode/src/mcp/index.ts`
6. `packages/opencode/src/session/message-v2.ts`

Focus on these functions and concepts:

- `PromptInput`
- `createUserMessage()`
- `assign(...)`
- MCP resource handling
- `file:` vs `data:` URL handling
- `ReadTool.init().execute(...)` during materialization
- agent-part expansion
- `Plugin.trigger("chat.message", ...)`
- message-first then parts persistence

---

# 39. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Should more input part types be supported directly at ingress, such as explicit prompt-template references or richer structured attachments?
- **Question 2**: How stable is the synthetic “Called the Read tool ...” trace format, and should it be treated as part of a contract or as internal implementation detail?
- **Question 3**: Are there cases where eagerly executing `ReadTool` during ingress is too expensive and should become lazier?
- **Question 4**: How should large MCP resources or large local files be summarized or truncated at ingress without losing important semantics?
- **Question 5**: Should directory inputs use a dedicated list-oriented materialization path rather than the current read-tool-based normalization?
- **Question 6**: How do plugin hooks typically transform or enrich materialized user messages in practice?
- **Question 7**: Are there race or consistency concerns if the underlying local file changes between ingress-time read and later session replay?
- **Question 8**: How should permission semantics interact with ingress-time file or MCP-resource expansion in more restrictive environments?

---

# 40. Summary

The `session_create_user_message_and_part_materialization` layer is where OpenCode turns incoming prompt input into durable, semantically rich session state:

- it resolves agent, model, and variant metadata for the new user turn
- it expands files, directories, MCP resources, and agent mentions into one-or-many persisted parts
- it records synthetic action traces so later turns can understand how content entered context
- it passes the materialized turn through plugin hooks and then persists message and parts into the session model

So this module is the ingress normalization layer that prepares raw user intent for the rest of the OpenCode agent runtime.
