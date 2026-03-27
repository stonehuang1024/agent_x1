# Session Loop / Prompt / Context

---

# 1. Module Purpose

This module family is the closest thing OpenCode has to a central runtime nervous system.

If you only choose one subsystem to understand how OpenCode actually works as an agent runtime, this is the one to read first:

- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/processor.ts`
- `packages/opencode/src/session/message-v2.ts`
- `packages/opencode/src/session/system.ts`
- `packages/opencode/src/session/instruction.ts`
- `packages/opencode/src/session/llm.ts`
- `packages/opencode/src/session/compaction.ts`

Together, these modules answer the core runtime questions:

- how user input enters the system
- how the multi-step agent loop advances
- how prompts are assembled dynamically
- which context is sent to the model
- how long histories are compacted
- how tool results flow back into the next turn
- how model output is parsed and written back into durable state

In other words, this subsystem is why OpenCode is not just a single-turn chatbot. It is a **long-lived, resumable, compactable, tool-executing multi-turn agent runtime**.

---

# 2. Module Boundaries and Responsibilities

## 2.1 Responsibility of `session/prompt.ts`

This is the main orchestrator.

It is responsible for:

- defining `PromptInput`
- creating user messages
- starting or resuming the loop
- selecting the effective history for the current turn
- deciding whether to handle `subtask` or `compaction` first
- creating the assistant message that will carry the current turn
- resolving tools for the current turn
- building the system prompt layers
- calling `SessionProcessor.process()`
- deciding whether to `continue`, `stop`, or `compact`

The best mental model is: **runtime orchestration layer**.

## 2.2 Responsibility of `session/processor.ts`

This is the consumer of model output events.

It is responsible for:

- text streaming
- reasoning streaming
- tool-call lifecycle handling
- patch and snapshot handling
- finish reason, token, and cost handling
- retry and context overflow handling
- persisting output back into `Session` and `Part`

It is a **stream-to-state transformer**.

## 2.3 Responsibility of `session/message-v2.ts`

This is the context and state-representation layer.

It is responsible for:

- defining message and part schemas
- reading and writing message streams
- converting internal message state into model input
- filtering already-compacted history
- normalizing tool results, files, and reasoning parts

It is the **state truth model**.

## 2.4 Responsibility of `session/system.ts`

This module handles the static or semi-static parts of system prompt assembly.

It is responsible for:

- selecting the base prompt by provider or model family
- generating environment prompts
- generating the skill index prompt

## 2.5 Responsibility of `session/instruction.ts`

This is the instruction discovery and injection layer.

It is responsible for:

- finding system-level `AGENTS.md` and `CLAUDE.md`
- finding project-level instruction files
- finding path-local instruction files
- avoiding duplicate injection of the same instruction source

## 2.6 Responsibility of `session/llm.ts`

This is the unified model invocation layer.

It is responsible for:

- assembling the final system prompt
- merging model, agent, and variant options
- filtering tools
- applying provider-specific option shaping
- calling the AI SDK `streamText()` path

## 2.7 Responsibility of `session/compaction.ts`

This is the context compaction and session-continuation layer.

It is responsible for:

- determining whether context overflow happened
- generating summary assistant messages
- replaying required user messages
- pruning old tool results
- removing large media or reducing context weight under overflow conditions

---

# 3. Overall Runtime Model

## 3.1 From user input to loop start

The main entrypoint is:

- `SessionPrompt.prompt()`

At a high level, it does three things:

1. clears revert state
2. calls `createUserMessage(input)` to persist a new user message
3. enters `loop({ sessionID })` unless `noReply === true`

This is a major design clue.

OpenCode does not execute directly from an ephemeral request object. It executes from a **new durable user message inside session state**.

That means:

- user input is persisted first
- the loop advances from persisted state second

This is a classic **state-driven runtime** pattern.

---

## 3.2 Why the loop is `while (true)`

`SessionPrompt.loop()` is effectively a persistent state iterator over the current session.

On every iteration, it recalculates:

- the latest user message
- the latest assistant message
- the latest finished assistant message
- pending `subtask` or `compaction` tasks

The key idea is:

- the loop is not repeatedly mutating one temporary prompt string
- each iteration recomputes the next action from current session state

That is why the runtime naturally supports:

- interruption and resume
- continuing after tool execution
- continuing after summary or compaction
- continuing after new user messages appear mid-flow
- shared session state instead of hidden function-stack state

---

# 4. Detailed Loop Execution Flow

The following sections break the loop down in the order the real control flow follows.

## 4.1 Step one: load effective history

At the beginning of the loop, the runtime calls:

- `MessageV2.stream(sessionID)`
- `MessageV2.filterCompacted(...)`

The purpose is:

- load the full persisted message stream for the session
- avoid feeding history back into the model if that history has already been replaced by compaction summaries

This already shows that OpenCode does **not** just preserve all history blindly. Context first passes through logical trimming.

## 4.2 Step two: identify the key messages for the current turn

The loop reverse-scans messages to identify:

- `lastUser`
- `lastAssistant`
- `lastFinished`
- `tasks` such as `compaction` or `subtask`

The algorithmic idea is straightforward:

- scan backward from the newest state
- identify the current user intent still needing work
- identify the last completed assistant boundary
- prioritize pending tasks before normal model execution

If the latest assistant already finished normally after the latest user message, the loop exits immediately.

If pending tasks exist, the loop handles them first.

This is a classic **reverse scan plus boundary detection** pattern.

Its complexity is roughly:

- `O(n)` scan per loop iteration

That is acceptable here because:

- compaction keeps effective history bounded
- the real working window is not expected to grow without limit

## 4.3 Step three: title generation and model selection

On the first loop step, the runtime asynchronously calls:

- `ensureTitle(...)`

Then it uses the latest user message’s:

- `providerID`
- `modelID`

to resolve the actual model through:

- `Provider.getModel(...)`

If the model does not exist, the system does not fail silently. It publishes a session error event and includes suggestions when available.

This shows that model choice is **not hardcoded globally**. Each user message can carry model identity, and the runtime resolves it dynamically.

## 4.4 Step four: handle pending subtask first

If the newest pending task is a `subtask`, the loop does not call the LLM yet. It first takes the subtask branch.

The flow is:

1. create an assistant message
2. create a `tool` part named `task`
3. construct a `Tool.Context`
4. call `TaskTool.execute()`
5. write the result back as a tool result part
6. if it is a command-style task, insert a synthetic user message:
   - `Summarize the task tool output above and continue with your task.`
7. `continue` into the next loop iteration

Several design principles are visible here:

- **subtask is not an add-on**; it is a first-class loop branch
- **subtask results are persisted as normal tool parts**, preserving one unified state model
- **some reasoning models require a synthetic user message** to keep the conversation signature valid after mid-loop task execution

This is a strong sign that OpenCode supports real **task and subagent orchestration**, not just one agent plus tools.

## 4.5 Step five: handle pending compaction first

If the pending task is `compaction`, the loop first calls:

- `SessionCompaction.process(...)`

Then it continues.

This means compaction is not a patch-on-error behavior. It is one of the formal branches of the loop.

## 4.6 Step six: detect overflow even without an explicit compaction task

Even when there is no pending compaction task, the loop still checks:

- `SessionCompaction.isOverflow({ tokens, model })`

If overflow is detected, it automatically creates a compaction user message and continues.

So compaction has two trigger paths:

- explicit task-triggered compaction
- automatic token-overflow compaction

---

# 5. Prompt Assembly Logic

## 5.1 The overall prompt strategy

OpenCode does not use a “single giant system prompt plus one giant history string” model.

Instead it uses:

- **layered system prompt assembly**
- **structured message-history conversion**
- **provider-aware adaptation**

The final model input is split into:

- `system: string[]`
- `messages: ModelMessage[]`

That is significantly more maintainable than raw string concatenation.

## 5.2 `session/system.ts`: provider prompt selection algorithm

`SystemPrompt.provider(model)` chooses a base prompt by `model.api.id`, for example:

- `gpt-5` -> `PROMPT_CODEX`
- `gpt-* / o1 / o3` -> `PROMPT_BEAST`
- `gemini-*` -> `PROMPT_GEMINI`
- `claude*` -> `PROMPT_ANTHROPIC`
- `trinity` -> `PROMPT_TRINITY`
- fallback -> `PROMPT_ANTHROPIC_WITHOUT_TODO`

This is not a complicated algorithm. It is a **model-family dispatch strategy**.

The principle is:

- different model families respond best to different base prompting styles
- therefore system prompt selection should vary by provider or model family

## 5.3 Environment prompt design

`SystemPrompt.environment(model)` assembles environment coordinates such as:

- current model name
- provider and model IDs
- working directory
- workspace root
- git-repository status
- platform
- current date
- optionally a directory tree summary

The principle here is:

- provide the model with a stable execution frame
- reduce uncertainty about where it is operating and what environment it is in

This is effectively the runtime’s **execution-frame prompt**.

## 5.4 Skills prompt design

`SystemPrompt.skills(agent)`:

1. checks whether `skill` usage is disabled by permission
2. calls `Skill.available(agent)`
3. generates a skill index prompt

It does **not** inject full skill bodies into the system prompt. It only injects summary/index information such as:

- what a skill is
- why it is useful
- which skills are currently available

This is an important context-optimization strategy:

- index first
- load details on demand later

This is effectively **lazy context expansion**.

## 5.5 Instruction prompt design

`InstructionPrompt.system()` includes logic to:

- search upward from the project path for `AGENTS.md`, `CLAUDE.md`, and `CONTEXT.md`
- look for instruction files from global config or the home directory
- load extra instruction content from configured local paths or URLs
- wrap file content as named instruction sources

The design principle is:

- treat instructions as system-rule sources, not ordinary user messages
- allow instructions to exist at multiple levels:
  - global
  - project-level
  - path-local

This is **hierarchical instruction layering**.

---

# 6. Context Selection and Construction Algorithms

## 6.1 Why `MessageV2.toModelMessages()` is one of the core algorithms

All internal message state must eventually pass through:

- `MessageV2.toModelMessages(input, model, options?)`

This function does much more than a simple map. It reconstructs the internal state system into a dialogue structure the model provider can actually accept.

It performs several classes of transformation.

### User-message conversion

- `text` -> user text
- `file` -> user file
- `compaction` -> text such as `What did we do so far?`
- `subtask` -> text describing that a tool was executed by the user

### Assistant-message conversion

- `text` -> assistant text
- `reasoning` -> assistant reasoning
- completed tool parts -> tool output
- errored tool parts -> tool error
- pending or running tool parts -> interrupted tool error

### Media compatibility handling

- providers that support media tool results keep them inline
- providers that do not support them cause media to be extracted and reintroduced as later user-file messages

This is a **provider-aware serialization algorithm**.

It is not only choosing which context to include. It is also deciding **how to encode that context into provider-compatible message formats**.

## 6.2 Why pending tools become errors in model history

This is a very important implementation detail.

Some providers require every `tool_use` to have a corresponding `tool_result`. If history contains a half-finished tool call and that history is replayed to the provider, the protocol may reject the message list.

OpenCode’s strategy is:

- convert `pending` or `running` tool parts into an `output-error`
- use error text like `[Tool execution was interrupted]`

That guarantees the historical message structure is still closed and protocol-valid.

This is a classic **protocol normalization** technique.

## 6.3 `filterCompacted()` as a history-trimming algorithm

`MessageV2.filterCompacted()` is meant to ensure that once a compaction summary already exists, the pre-summary full history is no longer sent back into the model.

The basic principle is:

- scan forward through the message stream
- identify completed summary assistants
- truncate history at the compaction boundary they establish

This can be understood as a **summary-boundary truncation** algorithm.

## 6.4 `insertReminders()` and mid-stream user message promotion

Even without reopening its whole implementation here, the loop shows that OpenCode enhances certain queued user messages with reminder wrappers.

Inside `loop()`, new user text that appears after the last finished assistant can be wrapped like this:

```xml
<system-reminder>
The user sent the following message:
...
Please address this message and continue with your tasks.
</system-reminder>
```

This is a **priority-lifting** technique:

- the message source is not changed
- but its salience is ephemerally increased for the next model turn

---

# 7. `SessionProcessor` as a Parsing State Machine

## 7.1 It is not a text parser; it is an event state machine

`SessionProcessor.process()` iterates over:

- `for await (const value of stream.fullStream)`

That means OpenCode is not interpreting model output with regexes or post-hoc text scraping. It is consuming already-structured stream events from the AI SDK.

So the processor is fundamentally:

- a **stream event consumer**
- a **message-state machine advancer**

## 7.2 Mapping events into persisted state

### Text stream

- `text-start` -> create an empty `TextPart`
- `text-delta` -> append text delta
- `text-end` -> trim, apply plugin transforms, finalize the part

### Reasoning stream

- `reasoning-start` -> create a `ReasoningPart`
- `reasoning-delta` -> append reasoning text
- `reasoning-end` -> close the reasoning part

### Tool stream

- `tool-input-start` -> create a pending tool part
- `tool-call` -> update to running
- `tool-result` -> update to completed
- `tool-error` -> update to error

### Step stream

- `start-step` -> record a snapshot boundary
- `finish-step` -> record finish reason, usage, cost, and patch data

This is a clear **event-to-persisted-part** mapping.

## 7.3 Doom loop detection algorithm

The processor defines:

- `DOOM_LOOP_THRESHOLD = 3`

When the newest three tool parts are all:

- the same tool name
- the same input
- not pending

the runtime triggers:

- `PermissionNext.ask({ permission: "doom_loop", ... })`

This is a simple but effective **repeated-call detection** algorithm.

It is basically a sliding-window check:

- window length = 3
- all three invocations must be structurally identical

Advantages:

- simple implementation
- low runtime cost
- quickly blocks obvious dead loops

Limitations:

- only catches exactly repeated inputs
- does not catch semantically similar loops with slightly different text

But as a runtime safeguard, it is highly practical.

## 7.4 Retry and overflow handling principles

When the processor catches an exception, the flow is roughly:

1. normalize it through `MessageV2.fromError()`
2. if it is a `ContextOverflowError`, mark compaction as needed
3. otherwise evaluate `SessionRetry.retryable(error)`
4. if retryable, retry with backoff
5. otherwise persist the error on the assistant message

This shows that error handling is not a blunt catch-all.

It follows a semantic pipeline:

- **normalize the error first**
- **then decide whether to compact, retry, or stop based on error meaning**

---

# 8. `LLM.stream()` Invocation Principles

## 8.1 It is not just a thin `streamText()` forwarder

Before the actual model call, `session/llm.ts` also handles:

- language-model acquisition
- provider, auth, and config resolution
- special cases such as Codex or OAuth-related behavior
- final system prompt assembly
- model, agent, and variant option merging
- plugin transforms
- tool filtering
- LiteLLM compatibility handling
- provider-aware message transforms through middleware

So `LLM.stream()` is best understood as a **provider-neutral orchestration wrapper**.

## 8.2 Final system prompt layering order

At the `LLM.stream()` level, system prompt assembly is layered roughly as:

1. `agent.prompt` or `SystemPrompt.provider(model)`
2. `input.system`
3. `input.user.system`

Then the loop passes in additional layers such as:

- `SystemPrompt.environment(model)`
- `SystemPrompt.skills(agent)`
- `InstructionPrompt.system()`
- structured-output constraints when relevant

So the final system prompt is not one template file. It is a layered composition.

## 8.3 Provider option merge algorithm

`LLM.stream()` merges several configuration layers:

- provider base options
- `model.options`
- `agent.options`
- variant options

This is a hierarchical deep-merge strategy.

The idea is:

- provider defines baseline capability constraints
- model layer refines per-model behavior
- agent layer refines task-mode behavior
- variant layer refines intensity or style

This is a classic **hierarchical option merge**.

## 8.4 LiteLLM compatibility shim

If:

- the history contains tool calls
- but the currently active tool set is empty
- and the provider is a LiteLLM proxy

OpenCode injects a `_noop` tool automatically.

This exists to satisfy proxy-layer validation rules where tools cannot be entirely absent if historical messages contain tool-call structure.

This is a highly pragmatic **compatibility shim**.

## 8.5 `experimental_repairToolCall`

When tool calling fails, `LLM.stream()` also contains a tool-name repair path:

- if the model used the wrong case but the lowercase form exists, it repairs to the lowercase tool name
- otherwise it rewrites the call as an `invalid` tool with an attached error

This is effectively a **tool-call repair algorithm**.

The purpose is not to tolerate every error. It is to:

- repair common superficial mistakes
- route unrecoverable mistakes into an explicit invalid-tool path

---

# 9. Compaction Logic and Algorithms

## 9.1 Overflow decision algorithm

`SessionCompaction.isOverflow()` roughly does the following:

1. check whether auto compaction is disabled
2. get the model context limit
3. calculate current token usage
4. reserve a safety buffer such as `min(20000, maxOutputTokens)`
5. calculate available input window
6. declare overflow if token usage exceeds the available input budget

This is a **capacity-threshold algorithm with safety margin**.

The core idea is not simply “compact when close to the limit.” It is:

- reserve room for model output
- reserve safety margin for provider-specific variation

## 9.2 Prune algorithm

`SessionCompaction.prune()` walks backward through old tool results and estimates cumulative token load.

The rules are roughly:

- leave the most recent turns untouched
- stop before the relevant summary boundary
- accumulate estimated token cost of completed tool results
- once a protection threshold is exceeded, mark older tool output as compacted
- keep protected tools such as `skill` from being pruned

This is an **old tool output pruning** algorithm.

The goal is:

- preserve recent context
- remove the payload of older, less critical tool output
- keep the state structure intact while trimming heavy historical content

## 9.3 Summary-generation algorithm

The core idea of `SessionCompaction.process()` is:

1. invoke the `compaction` agent to generate a summary assistant message
2. ask the model to summarize items such as:
   - Goal
   - Instructions
   - Discoveries
   - Accomplished work
   - Relevant files and directories
3. run that summary through `SessionProcessor.process()`
4. if auto compaction is active:
   - replay a previously interrupted user message when needed
   - or insert a synthetic continue message

This is not just summarization. It is a **structured continuation prompt generator**.

Its purpose is to prepare the next runtime phase with a compact but actionable handoff document.

## 9.4 Replay under overflow

If compaction was triggered by overflow, the system may:

- scan backward from the parent user message to find a replayable user request
- reinsert that user message after compaction

The reason is simple:

- summary compresses history
- but the currently actionable request must not be lost

This is a **summary plus replay continuity** strategy.

---

# 10. Core Design Principles Behind the Module

When you abstract this subsystem, several important principles become clear.

## 10.1 State comes before strings

The system is not organized around one giant prompt string. It is organized around:

- sessions
- messages
- parts

That is why the runtime is:

- interruptible
- resumable
- auditable
- replayable
- compactable

## 10.2 The loop is a scheduling center, not a text helper

The loop decides:

- what the next turn should do
- which context to use
- which branch to take
- when to stop

It is not merely a prompt-string builder.

## 10.3 Provider differences are systematically absorbed by the runtime

Through components such as:

- `SystemPrompt.provider()`
- `ProviderTransform`
- `MessageV2.toModelMessages()`
- `LLM.stream()`

the runtime absorbs provider protocol differences, tool-result differences, and media-format differences internally instead of scattering them throughout business logic.

## 10.4 Context management is a first-class runtime capability

OpenCode does not treat context as “just append the history.”

It has dedicated machinery for:

- filtering
- layered injection
- reminders
- summary
- pruning
- replay

That means context engineering has been elevated to a first-class runtime capability.

---

# 11. Interfaces with Other Modules

## 11.1 Interface with the agent layer

- the loop selects the agent from `lastUser.agent`
- the agent determines prompt behavior, permissions, steps, options, and variant behavior

## 11.2 Interface with the tool layer

- `resolveTools()` assembles tools from `ToolRegistry`
- the processor handles tool-call, tool-result, and tool-error lifecycle
- tool results become part of the next-turn context

## 11.3 Interface with the permission layer

- `ask()` inside tool context delegates to `PermissionNext`
- doom-loop detection also uses the permission system

## 11.4 Interface with the provider layer

- models are resolved through `Provider`
- message and option shapes are adapted via `ProviderTransform`
- actual `streamText()` execution is implemented by provider-side SDKs

## 11.5 Interface with skill and instruction layers

- the skill index enters the system prompt
- instruction files enter the system prompt or local context layers

---

# 12. Recommended Reading Order

If you want to go deeper into this subsystem, the recommended reading order is:

1. `packages/opencode/src/session/prompt.ts`
2. `packages/opencode/src/session/processor.ts`
3. `packages/opencode/src/session/message-v2.ts`
4. `packages/opencode/src/session/system.ts`
5. `packages/opencode/src/session/instruction.ts`
6. `packages/opencode/src/session/llm.ts`
7. `packages/opencode/src/session/compaction.ts`

Focus especially on these functions:

- `prompt()`
- `loop()`
- `resolveTools()`
- `createStructuredOutputTool()`
- `SessionProcessor.create().process()`
- `MessageV2.toModelMessages()`
- `MessageV2.filterCompacted()`
- `SystemPrompt.provider()`
- `SystemPrompt.environment()`
- `InstructionPrompt.system()`
- `InstructionPrompt.resolve()`
- `SessionCompaction.isOverflow()`
- `SessionCompaction.prune()`
- `SessionCompaction.process()`

---

# 13. Open Questions for Further Investigation

This article covers the main path, but several areas are still worth deeper investigation:

- **Question 1**: How exactly does `createUserMessage()` materialize user files, agent mentions, formatting constraints, and user-level system instructions into concrete parts?
- **Question 2**: What are the full implementation details of `insertReminders()`, and where exactly is its boundary relative to the later `<system-reminder>` wrapping inside `loop()`?
- **Question 3**: What protocol-level rewrites does `ProviderTransform.message()` apply for different providers?
- **Question 4**: Does `SessionRetry` optimize backoff differently by provider or error type?
- **Question 5**: Are the responsibilities of `SessionSummary.summarize()` versus compaction summary fully separated and clear?
- **Question 6**: Does structured-output failure always terminate immediately, or can recovery be made more granular?
- **Question 7**: How are snapshots and patches rendered and replayed in the UI layer?
- **Question 8**: When multiple frontends such as CLI, App, VS Code, and ACP act on the same session, how are busy, cancel, and resume concurrency semantics guaranteed?

---

# 14. Summary

The `session_loop_prompt_context` subsystem defines the core runtime control flow of OpenCode:

- the loop handles scheduling
- the processor parses output and persists state
- `message-v2` defines the structured state model
- system and instruction layers assemble rule-aware prompts
- `llm` performs provider-neutral invocation orchestration
- compaction keeps long-running sessions sustainable

So this module is not just “session logic.” It is the central control layer of the OpenCode agent runtime.
