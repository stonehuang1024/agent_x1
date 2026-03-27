# Kimi Code CLI: Deep Dive into Loop, Prompting, and Context Management

## 1. Why This Subsystem Matters

If there is one subsystem that determines whether Kimi Code CLI behaves like a serious engineering agent or just a shell-wrapped chatbot, it is the combination of:

- the **turn/step loop** in `KimiSoul`
- the **prompt layering model**
- the **context persistence and checkpoint model**
- the **dynamic injection system**
- the **tool-result-to-context feedback path**

This document focuses on that combined machinery.

The main files are:

- `src/kimi_cli/soul/kimisoul.py`
- `src/kimi_cli/soul/context.py`
- `src/kimi_cli/soul/dynamic_injection.py`
- `src/kimi_cli/soul/dynamic_injections/plan_mode.py`
- `src/kimi_cli/soul/compaction.py`
- `src/kimi_cli/soul/message.py`
- `src/kimi_cli/soul/agent.py`

## 2. The Mental Model: Turn, Step, Context, Tool Feedback

Kimi CLI is organized around **iterative agent execution**, not single-shot completion.

The runtime model is:

1. A user input starts a **turn**.
2. The turn is broken into one or more **steps**.
3. Each step performs an LLM inference with current context.
4. The LLM may emit tool calls.
5. Tools execute.
6. Tool results are appended into context.
7. The loop decides whether to continue with another step or stop the turn.

This means Kimi CLI is a **closed-loop controller**:

- LLM produces actions
- runtime executes them
- environment feedback is returned to the LLM
- the LLM revises its next move

That feedback loop is the center of the architecture.

## 3. The Run Boundary

## 3.1 `KimiSoul.run(...)`

The public turn entrypoint in `KimiSoul` is `run(user_input)`.

Its responsibilities are:

- refresh OAuth tokens before each turn
- emit `TurnBegin`
- convert raw input into a `Message(role="user", ...)`
- detect slash commands
- optionally run Ralph/flow mode
- otherwise run a normal `_turn(...)`
- emit `TurnEnd`

This means the soul has three high-level execution paths:

- **slash command path**
- **Ralph flow path**
- **normal agent turn path**

The loop deep dive below mainly concerns the normal `_turn(...)` path.

## 3.2 `_turn(...)`

`_turn(user_message)` does three key things:

1. validate message capability compatibility with the current model
2. create a checkpoint
3. append the user message to context and enter `_agent_loop()`

This means user input is persisted **before** the iterative loop begins.

The separation is useful because it clearly distinguishes:

- turn admission
- loop iteration
- special command dispatch

## 4. Two Nested Loops: Turn Loop and Step Loop

## 4.1 Turn loop

The turn loop is `_agent_loop()`.

Its job is to manage:

- step counting
- max-step enforcement
- auto-compaction
- checkpointing before each step
- approval forwarding
- D-Mail rollback behavior
- steer-message injection between steps
- final termination condition

A turn ends only when the loop obtains a `StepOutcome` that means the model has finished the turn, or when an exceptional condition interrupts execution.

## 4.2 Step loop

The inner unit is `_step()`.

A step does the actual agent computation:

- collect dynamic injections
- normalize history
- call the LLM agent-step abstraction
- stream content and tool events
- wait for tool results
- append assistant message and tool results back into context
- decide whether to continue or stop

So:

- `_agent_loop()` is the controller
- `_step()` is the execution unit

## 5. Detailed Step-by-Step Flow of `_agent_loop()`

The main loop in `KimiSoul._agent_loop()` works roughly as follows.

## 5.1 Clear stale steer inputs

Before beginning a new turn, the loop drains any old steer messages that might remain from a previous turn. This avoids turn contamination.

This is a subtle but important correctness detail.

## 5.2 Ensure MCP tools are ready

If the toolset is a `KimiToolset`, the loop checks whether MCP tool loading is still pending.

If yes, it emits:

- `MCPLoadingBegin`
- waits for MCP tool loading
- emits `MCPLoadingEnd`

This ensures the first LLM step sees a stable tool surface rather than an in-flight partial tool registry.

## 5.3 Create approval forwarder

A background task pipes approval requests from the internal approval subsystem into the external `Wire` event channel.

The conversion is explicit:

- internal approval request
- converted into `wire.types.ApprovalRequest`
- UI resolves it
- response is sent back to approval subsystem
- `ApprovalResponse` is emitted to wire

This is a strong decoupling pattern: the loop does not care whether approval is being shown in a shell UI, ACP client, or web UI.

## 5.4 Step counter and max-step protection

Each iteration increments `step_no` and compares it with `max_steps_per_turn`.

If the limit is exceeded, it raises `MaxStepsReached`.

This is the hard safety boundary preventing infinite internal agent loops.

## 5.5 Emit `StepBegin`

Before any work, the loop emits `StepBegin(n=step_no)`.

This matters because frontends can synchronize UI state exactly with the engine’s step lifecycle.

## 5.6 Auto-compaction check

Before each step, the loop decides whether context compaction is needed using `should_auto_compact(...)`.

Compaction triggers if either condition is true:

- `token_count >= max_context_size * trigger_ratio`
- `token_count + reserved_context_size >= max_context_size`

This dual trigger is smarter than using only a percentage threshold because it leaves explicit room for response generation and tool-call payloads.

## 5.7 Create checkpoint before each step

The loop checkpoints before `_step()`.

This is one of the most important design choices in the entire project.

It means every LLM step is wrapped by a recoverable boundary.

That enables:

- rollback on D-Mail “time travel”
- safe resumption points
- non-linear turn control flow
- a persistent trail of reasoning boundaries in the context log

## 5.8 Run `_step()`

The loop then executes `_step()`.

The result may be one of three broad categories:

- a `StepOutcome` saying the turn should stop
- `None`, meaning continue looping
- a special `BackToTheFuture` exception requiring rollback to a checkpoint

## 5.9 Handle interruption and cleanup

If `_step()` raises anything unexpected, the loop emits `StepInterrupted()` and re-raises.

Regardless of success or failure, the approval piping task is cancelled and awaited safely.

This ensures the loop does not leak approval-forwarding tasks.

## 5.10 Decide whether turn ends

If `_step()` returns a real `StepOutcome`, the loop still checks pending steer inputs.

If new steer messages arrived, it injects them and forces another LLM step instead of ending the turn immediately.

That is an important usability design: user steering has priority over immediate stop.

If no steers remain, the loop returns a `TurnOutcome`.

## 5.11 Handle rollback / D-Mail

If `_step()` raised `BackToTheFuture`, the loop:

1. reverts the context to the target checkpoint
2. creates a fresh checkpoint
3. appends the injected future-originating messages

This is one of the most unusual parts of the system. It gives the runtime the ability to re-enter prior state with new hidden information.

## 6. Detailed Flow of `_step()`

Now we examine `_step()`, where the real agent computation occurs.

## 6.1 Collect dynamic injections

Before calling the LLM, the step queries all registered `DynamicInjectionProvider`s.

Currently the built-in provider list includes `PlanModeInjectionProvider`, but the design is extensible.

Each provider receives:

- full current history
- the soul instance, hence access to runtime state

Providers return `DynamicInjection(type, content)` items.

## 6.2 Convert injections into history messages

If injections exist, the step concatenates them using `system_reminder(...)` wrappers and appends them to context as a new `user` message.

That is an extremely important design point:

- injections are not hidden side metadata
- they become normal user-role context entries
- they persist into context history

The wrapper format is:

```text
<system-reminder>
...
</system-reminder>
```

This gives the model privileged guidance while still keeping the content inside the normal message stream.

## 6.3 Normalize history before model call

Because injections are appended as extra `user` messages, the system normalizes history with `normalize_history(...)`.

That function merges adjacent user messages into one combined user message.

Why this matters:

- injections remain persisted separately on disk
- but the API-facing message sequence stays cleaner
- the model sees one merged user block rather than fragmented user turns

Only adjacent `user` messages are merged.

Assistant and tool messages are intentionally not merged because they carry semantic linkage such as:

- `tool_calls`
- `tool_call_id`

Breaking those pairings would corrupt the tool-call chain.

## 6.4 LLM step call

The actual step call is:

- `kosong.step(chat_provider, system_prompt, toolset, effective_history, ...)`

The runtime passes callbacks:

- `on_message_part=wire_send`
- `on_tool_result=wire_send`

This means streaming message chunks and tool progress are surfaced to the frontend immediately.

Architecturally, the LLM output path is already **structured and streaming-aware** at the step boundary.

## 6.5 Retry wrapper

The raw step call is wrapped with `tenacity.retry(...)` plus provider-specific recovery logic.

Retryable conditions include:

- connection errors
- timeout errors
- empty responses
- 429
- 500
- 502
- 503

There is also `_run_with_connection_recovery(...)`, which lets a `RetryableChatProvider` reinitialize or repair itself before retrying once.

This is important for production robustness. The agent loop is not built on the assumption that inference is always stable.

## 6.6 Status update after LLM response

After `kosong.step(...)` returns, `_step()` emits a `StatusUpdate` containing:

- token usage
- message ID
- plan mode state
- current context usage/token counts if available

The context token count is updated first from `result.usage.input`, then later from `result.usage.total` after context growth.

This creates a near-real-time token accounting model for the frontend.

## 6.7 Wait for tool results

The raw `StepResult` may contain tool calls whose actual completion is still pending. `_step()` waits on `result.tool_results()`.

This is a crucial part of the architecture: the step is not complete when the LLM emits tool calls; it is complete only after runtime tool execution resolves.

## 6.8 Plan-mode status correction

If tool execution changes plan mode state, the step emits a corrected `StatusUpdate(plan_mode=...)`.

This avoids stale UI state after tools like `EnterPlanMode` or `ExitPlanMode` run.

## 6.9 Grow context with result + tools

The step then calls `_grow_context(result, results)` under `asyncio.shield(...)`.

Using `shield(...)` means context mutation is protected from interruption. That is a deliberate consistency guarantee: once the system has a step result and tool results, it strongly prefers not to leave context half-written.

## 6.10 Detect tool rejection

If any tool result contains `ToolRejectedError`, the step stops with `stop_reason="tool_rejected"`.

This is part of the human-in-the-loop safety path.

## 6.11 Handle pending D-Mail

If `denwa_renji.fetch_pending_dmail()` returns a message, `_step()` raises `BackToTheFuture` with:

- target checkpoint ID
- a synthesized hidden user message describing the D-Mail content

This lets the outer loop revert and continue from earlier state with injected hidden information.

## 6.12 Final step decision

Finally:

- if `result.tool_calls` exists, return `None` so the loop continues
- otherwise return `StepOutcome(stop_reason="no_tool_calls", assistant_message=result.message)`

This is the simplest and most important stop criterion in the system:

- **no tool calls means the step can end the turn**

## 7. Prompt Architecture in Detail

## 7.1 Prompt sources

The effective prompt seen by the model is not a single string. It is composed from several layers.

The main sources are:

- agent system prompt loaded from file and rendered with Jinja
- context history restored from JSONL
- dynamic reminders injected before steps
- current user input
- tool-result messages accumulated from previous steps
- hidden system-tagged content such as compaction summaries or D-Mail instructions

## 7.2 System prompt lifecycle

The system prompt is loaded at agent creation time by `_load_system_prompt(...)` in `src/kimi_cli/soul/agent.py`.

That function:

- reads the prompt file
- renders it with Jinja using built-in runtime arguments and spec args
- returns the final concrete prompt string

The concrete prompt is then persisted into session context via `Context.write_system_prompt(...)` unless the session already has one.

This creates an important invariant:

- the active session prompt is effectively pinned once the session starts

This favors reproducibility within a session.

## 7.3 Built-in prompt variables

The system prompt can access runtime-generated variables such as:

- current time
- work directory path
- work directory listing
- `AGENTS.md` content
- discovered skills list
- additional directories info

This design means a large amount of “global workspace context” is injected at the **system prompt layer**, not repeatedly rediscovered in every user message.

## 7.4 Internal prompt wrappers

The helper functions in `soul/message.py` show two special wrappers:

- `system(...)` → `<system>...</system>`
- `system_reminder(...)` → `<system-reminder>...</system-reminder>`

These are not provider-native roles. They are text wrappers placed inside content parts.

This implies the project chooses to encode some high-priority instruction semantics *inside message text*, rather than relying only on provider-native role channels.

That is pragmatic and portable across providers.

## 8. Dynamic Injection System

## 8.1 Why injections exist

Not every important instruction belongs permanently in the top-level system prompt.

Some instructions are situational and temporary, for example:

- “plan mode is active, remain read-only”
- “you just re-entered plan mode with an existing plan file”
- future dynamic guidance based on runtime status

The dynamic injection system solves that problem.

## 8.2 Provider interface

`DynamicInjectionProvider` is an abstract interface with one method:

- `get_injections(history, soul) -> list[DynamicInjection]`

This is intentionally minimal.

Each provider decides:

- when to inject
- how often to inject
- what content to inject

## 8.3 Current built-in provider: plan mode

`PlanModeInjectionProvider` is the most concrete example.

It injects reminders only while `soul.plan_mode` is active.

Its throttling strategy is inferred from history:

- scan backward to find the last plan reminder
- count assistant turns since then
- inject only after a configurable interval

This is an elegant design because it does not require separate counters stored in session state; it can infer cadence from context history itself.

## 8.4 Full vs sparse reminders

Plan mode reminders have three forms:

- full reminder
- sparse reminder
- reentry reminder

The full reminder includes:

- read-only rule
- plan file path and allowed edit exception
- workflow steps
- explicit turn-ending constraints

The sparse reminder is a compressed version used periodically.

The reentry reminder is used when plan mode is re-entered and a previous plan file already exists.

## 8.5 Why this is a strong design

This is one of the better ideas in the codebase.

Instead of:

- bloating the permanent system prompt
- duplicating plan constraints in every tool
- relying on UI-only mode indicators

the system injects temporary, enforceable, model-visible reminders exactly when needed.

That is prompt-state management done at the right abstraction boundary.

## 9. Context Persistence Design

## 9.1 JSONL as storage

The `Context` class persists the conversation as JSONL.

It stores:

- ordinary `Message` records
- `_system_prompt`
- `_usage`
- `_checkpoint`

This provides a durable, append-only record of the full agent state evolution.

## 9.2 Restore model

`Context.restore()` replays the file and reconstructs:

- `_history`
- `_token_count`
- `_next_checkpoint_id`
- `_system_prompt`

This means the context file is the source of truth for conversational runtime history.

## 9.3 Appending messages

`append_message(...)` does two things:

- extend in-memory `_history`
- append serialized JSONL lines to disk

That keeps memory and persistence aligned closely.

## 9.4 System prompt persistence

`write_system_prompt(...)` ensures the prompt is the first record in the file. If the file already has content, it prepends via a temp file and atomic replace.

This is a careful durability implementation, not a naive append.

## 9.5 Token usage persistence

`update_token_count(...)` stores `_usage` records to disk every time the token count is updated.

This gives the loop a persistent approximation of current context size across restarts.

## 9.6 Checkpoint persistence

`checkpoint(...)` writes `_checkpoint` records and optionally injects a synthetic user message like `CHECKPOINT N` wrapped as a system-tagged content part.

This is a very unusual design but it is intentional: some subsystems need the checkpoint concept to be visible in the conversational state as well.

## 10. Checkpointing and Time Travel

## 10.1 Why checkpoints exist

Most agent systems only append messages. Kimi CLI goes further by making checkpoints a first-class context primitive.

A checkpoint marks a point the runtime can return to if it decides a later path should be abandoned.

## 10.2 Reversion logic

`Context.revert_to(checkpoint_id)`:

1. rotates the current file to a backup path
2. clears current in-memory state
3. replays lines until the target checkpoint is encountered
4. rewrites a truncated context file

This is robust because the revert operation is not only in memory; it also reconstructs the persistent backing store.

## 10.3 D-Mail control flow

The D-Mail mechanism uses checkpoints to implement “future self” intervention.

From the loop’s perspective, D-Mail creates a hidden user message instructing the model that:

- it has received information from a future self
- it should react to that information
- it must not mention this to the user

This is one of the most distinctive control-flow features in the project.

## 11. Context Growth: How Results Re-enter the Prompt

## 11.1 Assistant message first

`_grow_context(...)` first appends the assistant message from the current LLM step.

If usage info exists, it then updates token count with total usage.

## 11.2 Tool results become tool messages

Each tool result is converted into a `Message(role="tool", tool_call_id=...)` using `tool_result_to_message(...)`.

The conversion rules are:

- error results become `<system>ERROR: ...</system>` plus any output
- success results become optional `<system>message</system>` plus output
- empty tool output becomes `<system>Tool output is empty.</system>`

This is important because tools do not just return opaque runtime values. Their outputs are normalized into model-readable follow-up context.

## 11.3 Capability validation

Before appending tool messages, `_grow_context(...)` checks whether they require capabilities unsupported by the current model, such as image/video/thinking.

This prevents the runtime from poisoning context with content the current model cannot interpret properly.

## 11.4 Why this feedback path matters

The step loop only works because tool outputs are turned into future prompt context. That is the mechanism that enables iterative reasoning.

Without it, tool execution would be disconnected from subsequent inference.

## 12. Steering: Mid-Turn User Guidance

## 12.1 What steering is

`steer(...)` pushes a new input into an in-memory queue while a turn is already running.

This allows user follow-up guidance without discarding the current turn.

## 12.2 How steering is consumed

`_consume_pending_steers()` drains the queue and injects each steer as a normal follow-up `user` message using `_inject_steer(...)`.

It then emits a `SteerInput` event.

## 12.3 Why it matters

This gives the system a way to accept dynamic user correction during long-running agent execution.

Architecturally, steering is not special after injection; it becomes ordinary user context. That keeps the downstream logic simple.

## 13. Plan Mode as Prompt-State, Not Just UI-State

## 13.1 Plan mode state lives in the soul

`KimiSoul` persists `plan_mode` in session state and exposes helpers to:

- toggle plan mode
- schedule next-step reminders
- bind plan-mode awareness into specific tools
- manage a session-specific plan file path

## 13.2 Plan mode affects multiple layers

Plan mode is enforced through multiple cooperating mechanisms:

- dynamic prompt reminders
- tool-level plan mode checks
- plan file path binding
- UI status updates
- session-state persistence

This is a good example of a “distributed rule” implemented consistently across runtime, prompt, and tools.

## 13.3 Key design idea

The project does **not** simply hide tools in plan mode. Instead, tools remain present but can reject disallowed actions at call time.

This is a better design because:

- tool schema remains stable
- UI/tool registry stays simpler
- plan-mode semantics remain explicit
- exceptions such as writing the plan file can be handled locally

## 14. Compaction Strategy in Detail

## 14.1 Triggering

Compaction is proactive, not reactive at failure time.

The loop checks before each step whether context is nearing size limits.

## 14.2 SimpleCompaction strategy

`SimpleCompaction` preserves the most recent user/assistant messages and compacts older history into a generated summary.

The process is:

1. split history into `to_compact` and `to_preserve`
2. build a synthetic user message enumerating older messages
3. append a compaction prompt (`prompts.COMPACT`)
4. call `kosong.step(...)` with an empty toolset
5. wrap the summary as a new compacted user message
6. append preserved recent messages after it

This is effectively a **summary-based rolling memory** strategy.

## 14.3 Important implementation details

- Only text parts are fed into the compaction prompt.
- `ThinkPart` outputs are dropped from compaction results.
- The compacted summary is wrapped in a user message prefixed with a system marker.
- Estimated token count is computed conservatively when exact counts are not available.

## 14.4 Architectural implication

Compaction is not just truncation. It is a semantic rewrite of old context into a compressed narrative that remains visible to the model.

## 15. Prompt Semantics: Why Role Choice Matters

A subtle but important pattern in Kimi CLI is that many internal instructions are represented as:

- `user` messages
- with text wrapped in `<system>` or `<system-reminder>` tags

rather than using dedicated system-role messages for every event.

Possible reasons this design makes sense:

- portability across providers and abstractions
- easier persistence in a single JSONL conversation stream
- no need to rely on provider-specific multi-system-message semantics
- easier merging with adjacent user messages before the API call

In other words, Kimi CLI uses a **textual control-channel convention inside the message stream**.

That is one of the most important prompt-engineering patterns in this codebase.

## 16. What the Model Actually Sees

Ignoring provider-specific formatting, a typical model-visible input before one step may conceptually look like:

1. system prompt with runtime-rendered workspace metadata
2. prior user/assistant/tool history
3. latest dynamic plan-mode reminder merged into user content
4. current user request or steer message
5. all previous tool results already normalized into tool-role messages

This means the model sees:

- long-term static identity and policy via system prompt
- medium-term operational state via persisted history
- short-term temporary constraints via injected reminders
- environment feedback via tool messages

That layering is the real prompt architecture.

## 17. Strengths of This Loop and Prompt Design

The strongest qualities are:

- **Explicit turn/step separation**
- **Durable JSONL context**
- **Checkpoint-based recovery**
- **Dynamic prompt-state injection**
- **Structured tool feedback into context**
- **Compaction as a first-class loop concern**
- **UI decoupling through wire events**
- **Mid-turn steering support**

Taken together, this is a fairly mature agent-loop design.

## 18. Architectural Risks and Limitations

## 18.1 Prompt state is spread across several channels

The full effective prompt depends on:

- system prompt rendering
n- persisted history
- dynamic reminders
- hidden textual wrappers
- tool messages
- compaction summaries

This is powerful, but also harder to reason about and debug than a simpler prompt stack.

## 18.2 Internal control relies on textual conventions

Using `<system>` and `<system-reminder>` inside text is practical, but it is still a convention. Its effectiveness depends on the model respecting those wrappers consistently.

## 18.3 Compaction can distort history

All summary-based compaction strategies have the same risk: if the summary omits or distorts a detail, later reasoning may be biased.

## 18.4 Long-running sessions may accumulate instruction drift

Because reminders are injected into history and the system prompt is session-pinned, the effective context of an old session may differ significantly from fresh-start assumptions.

## 18.5 D-Mail increases conceptual complexity

Checkpoint-based time travel is powerful, but makes reasoning about exact causal history harder for humans reading logs and debugging behavior.

## 19. Core Design Insight

The core insight of Kimi CLI is that agent control is not encoded in one place.

Instead, it is produced by the composition of:

- persistent context
- pre-step injections
- step retries
- tool execution
- checkpoint boundaries
- compaction
- event-stream visibility

That composition is what makes the system feel agentic.

## 20. What to Deep-Dive Next

The next most valuable areas to study are:

1. **LLM output format and parsing**
   - how `kosong.step(...)` represents assistant text, tool calls, streaming args, and final results
2. **Tool-call execution and concurrency model**
   - whether parallel tool calling is supported and how it is surfaced
3. **Code retrieval and context selection strategy**
   - how the agent decides which files/snippets should enter context
4. **Skills/subagents/rules**
   - how reusable prompting and multi-agent delegation are composed

## 21. Final Summary

Kimi Code CLI’s loop and prompt system is built around a robust iterative control cycle:

- persist user input
- create checkpoint
- inject temporary guidance
- normalize history
- call the LLM with tools
- execute tools
- write assistant and tool outputs back to context
- compact when necessary
- continue until no more tool calls are needed

The most distinctive characteristics of this design are:

- **dynamic prompt-state injection** instead of giant static prompts
- **checkpointed persistent context** instead of ephemeral chat memory
- **tool-output-as-context** instead of tool output as a side-effect only
- **stepwise control and retry** instead of single-shot completion

This subsystem is the real heart of the project.
