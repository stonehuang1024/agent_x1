# Kimi Code CLI: LLM Output Formats, Parsing, Tool Execution, Parallelism, and Mode Differences

## 1. Executive Summary

Kimi Code CLI does not treat model output as a single opaque text blob. Its architecture is built around **structured streaming events** that can represent:

- assistant text
- assistant thinking traces
- tool call starts
- tool call argument deltas
- tool results
- approvals
- status updates
- plan/todo updates
- subagent events

The most important idea is this:

- the model-facing side is abstracted by `kosong.step(...)`
- the runtime-facing side converts those results into `Wire` messages
- different frontends then render or translate those `Wire` messages differently

So the system is **event-first**, not plain-text-first.

## 2. The Main Output Model: `WireMessage`

The central runtime output contract is defined in `src/kimi_cli/wire/types.py`.

A `WireMessage` is either an `Event` or a `Request`.

## 2.1 Event types

Important event types include:

- `TurnBegin`
- `SteerInput`
- `TurnEnd`
- `StepBegin`
- `StepInterrupted`
- `CompactionBegin`
- `CompactionEnd`
- `MCPLoadingBegin`
- `MCPLoadingEnd`
- `StatusUpdate`
- `ContentPart`
- `ToolCall`
- `ToolCallPart`
- `ToolResult`
- `ApprovalResponse`
- `SubagentEvent`

## 2.2 Request types

Important request types include:

- `ApprovalRequest`
- `ToolCallRequest`
- `QuestionRequest`

This is important because the runtime distinguishes:

- one-way streamed state/content events
- interactive requests that require a response

That distinction is fundamental to the frontend protocol design.

## 3. What Counts as “LLM Output” in This System?

In Kimi CLI, “LLM output” is broader than final assistant text.

It includes:

- text content parts
- thinking content parts
- structured tool call objects
- tool call argument deltas
- final assistant message object
- usage metadata
- stop conditions such as no-tool-call completion

The runtime therefore understands the model output as a **multi-channel stream**, not just a paragraph.

## 4. The Step Boundary: Where Raw Model Output Becomes Structured Runtime Data

The core step call is made in `KimiSoul._step()` through:

- `kosong.step(chat_provider, system_prompt, toolset, effective_history, ...)`

That call receives callbacks:

- `on_message_part=wire_send`
- `on_tool_result=wire_send`

This tells us several things.

## 4.1 Text is streamed incrementally

Text content is not necessarily buffered until the end. It can be emitted as `TextPart` messages over the `Wire` while the step is still running.

## 4.2 Thinking is a first-class output type

The system supports `ThinkPart`, not just visible text.

This means the architecture explicitly distinguishes:

- visible assistant response
- model thinking trace

Whether every provider exposes this equally depends on provider/model capabilities, but the runtime is built to handle it.

## 4.3 Tool calls are not hidden inside text

Tool calls are represented as structured objects:

- `ToolCall`
- `ToolCallPart`

This is one of the most important architectural choices in the project.

The model is not “pretending” to call tools using text conventions. Tool invocation is part of the structured agent result.

## 5. Output Content Types

From `wire/types.py` and associated conversions, the system recognizes multiple content-part types.

Important ones include:

- `TextPart`
- `ThinkPart`
- `ImageURLPart`
- `AudioURLPart`
- `VideoURLPart`
- `ToolCallPart`

This means the output contract is multimodal-capable, although actual capability depends on the chosen model/provider.

## 6. Different Output Categories in Practice

## 6.1 Plain assistant text output

This is the normal explanatory or final-answer channel.

Represented as:

- streamed `TextPart`
- later part of the assistant `Message`

## 6.2 Thinking output

Represented as `ThinkPart`.

In ACP mode, this is converted into dedicated thought updates rather than plain visible user text.

## 6.3 Tool call output

Represented in two stages:

- `ToolCall` for a newly started structured tool call
- `ToolCallPart` for incremental argument streaming / refinement

This lets the UI show the tool call evolving in real time.

## 6.4 Tool result output

After execution, tool results are represented as `ToolResult`.

These results can contain:

- text output
- content parts
- display blocks such as diffs or todo plans
- success/error status

## 6.5 Control / runtime status output

The model step also leads to runtime-status outputs such as:

- `StatusUpdate`
- step boundaries
- compaction boundaries
- MCP loading boundaries

These are not model content, but they are part of the user-visible execution stream.

## 7. Output in Different Modes

The repository does not expose one single “plan / agent / code mode” enum exactly as some other agent systems do, but it does have several meaningful execution modes and behavior variants.

## 7.1 Default agent mode

This is the normal interactive execution path.

Characteristics:

- full loop execution
- tool use allowed
- approvals possible
- streaming text/thought/tool events
- persistent context

## 7.2 Plan mode

Plan mode is a runtime behavioral mode rather than a separate transport or provider format.

Characteristics:

- active read-only reminders are dynamically injected
- write/side-effect tools are restricted except the plan file path
- turns are expected to end via clarification or plan-exit approval path
- output still uses the same structured event model

So plan mode changes:

- tool permissions
- prompt reminders
- status updates
- plan file workflow

but **not** the core underlying event format.

## 7.3 Print mode

Print mode is a frontend mode.

Characteristics:

- non-interactive or script-friendly rendering
- can emit plain text or stream-json
- can be configured to only output final assistant text

This is mostly a UI/rendering distinction layered over the same runtime.

## 7.4 ACP mode

ACP mode is the most structurally interesting alternate mode.

In ACP mode, the runtime `Wire` events are translated into ACP protocol updates such as:

- text chunks
- thought chunks
- tool call updates
- plan updates
- permission requests

So ACP mode is not just another CLI output style. It is a protocol translation layer.

## 7.5 Web / Wire mode

Wire mode exposes the runtime’s structured stream more directly. The web stack then consumes related structured outputs for browser-based UI.

## 8. How Tool Calls Are Parsed and Displayed While Streaming

A key detail appears in `src/kimi_cli/acp/session.py`.

Each tool call in ACP mode is tracked by `_ToolCallState`, which stores:

- the base `ToolCall`
- accumulated raw argument string
- a `streamingjson.Lexer`

This is extremely important.

## 8.1 Why `streamingjson` is used

Tool call arguments may stream in incrementally rather than arriving as one complete JSON object.

Using `streamingjson.Lexer` allows the client/session layer to:

- append partial argument strings
- incrementally inspect partial JSON
- derive a useful subtitle while the tool call is still forming

## 8.2 `extract_key_argument(...)`

The helper in `src/kimi_cli/tools/__init__.py` extracts a concise human-meaningful title from tool arguments.

Examples:

- `Task` → description
- `Shell` → command
- `ReadFile` → path
- `Grep` → pattern
- `WriteFile` → path

This is not just cosmetic. It greatly improves the usability of streaming tool-call UIs because users can immediately see what the model is trying to do.

## 8.3 Incremental tool call progress

As argument parts arrive, ACP mode can update the tool-call title/progress rather than waiting for the full call to finish.

That is a strong UX and debugging capability.

## 9. How Tool Results Are Parsed and Mapped

Tool results are not simply dumped verbatim to every frontend.

## 9.1 Runtime-level representation

At runtime, a tool returns a `ToolReturnValue`.

That can contain:

- `message`
- `output`
- `display` blocks
- error state

## 9.2 Context-level conversion

Inside the soul, tool results are converted into `tool` messages using `tool_result_to_message(...)`.

Conversion rules include:

- errors become `<system>ERROR: ...</system>` plus output
- success messages become `<system>message</system>` plus output
- empty output gets a synthetic placeholder message

This is the representation used when the result is fed back to the LLM in later steps.

## 9.3 ACP-level conversion

In ACP mode, tool results are converted by `tool_result_to_acp_content(...)` in `acp/convert.py`.

That mapping can produce:

- plain content blocks for text output
- `FileEditToolCallContent` for diffs
- terminal-specific content in special cases

So there are actually multiple result mappings depending on destination:

- one mapping for LLM context
- another mapping for human-facing ACP clients

This is a good separation of concerns.

## 10. Diff Output Handling

Diffs are treated as first-class structured display content.

`display_block_to_acp_content(...)` converts `DiffDisplayBlock` into ACP file-edit content with:

- path
- old text
- new text

This means edits are not merely represented as plain textual logs. They are promoted into structured diff objects for protocol clients.

Architecturally, this is important because it preserves edit semantics across frontends.

## 11. Plan / Todo Output Handling

Plan updates are also structured.

In ACP mode, `TodoDisplayBlock` is converted into `AgentPlanUpdate` entries.

This means plan output is not just a markdown checklist. The runtime can expose it as a structured planning artifact to clients.

This is a key difference between a “chat app with markdown” and an actual agent protocol runtime.

## 12. Approval Requests and Questions as Interactive Output Types

The runtime also emits interactive requests:

- `ApprovalRequest`
- `QuestionRequest`

These are distinct from content output.

## 12.1 Approval flow

Approval requests are emitted over `Wire`, then resolved by the frontend, then the response is fed back into the runtime.

In ACP mode this is bridged to `session/request_permission` with options like:

- approve once
- approve for session
- reject

## 12.2 Question flow

`QuestionRequest` exists in the wire model, but ACP mode currently logs it as unsupported and resolves with empty answers.

That is an important current limitation.

## 13. How Tool Execution Actually Happens

Tool execution is handled through the loaded toolset, not by ad hoc UI code.

## 13.1 Built-in tool execution

Built-in tools are loaded dynamically via `KimiToolset.load_tools(...)`.

The tool class is instantiated through dependency injection, then invoked when the step result requests it.

## 13.2 MCP tool execution

For MCP tools, `MCPTool.__call__(...)`:

- requests approval
- opens a fastmcp client context
- calls `client.call_tool(...)`
- converts result content into local content parts
- applies timeout handling

So MCP tools are normalized into the same runtime tool model.

## 13.3 Wire external tools

`WireExternalTool` allows tool calls to be routed outward to a `Wire` client for execution.

This is important because it means the core runtime is capable of delegating tool execution beyond its own local process when needed.

## 14. Is Parallel Tool Calling Supported?

This needs careful wording.

## 14.1 What is clearly supported

The runtime clearly supports asynchronous behavior and multiple concurrent background tasks, for example:

- MCP server connections via `asyncio.gather(...)`
- approval piping in a separate task
- UI loop and soul loop running concurrently
- asynchronous tool execution APIs

## 14.2 What is not clearly demonstrated as a first-class model feature

From the core loop examined, we do **not** have strong direct evidence that the LLM is encouraged to emit multiple tool calls that are executed in parallel as a guaranteed first-class policy of the normal step loop.

The system definitely supports structured tool calls and async execution, but whether a single model step executes multiple tool calls concurrently depends heavily on `kosong.step(...)`, which is abstracted away from the current repo inspection.

So the correct conclusion is:

- **the architecture is async-capable and can handle multiple in-flight concerns**
- **parallel MCP connection loading exists**
- **full first-class parallel tool-call execution in the normal agent step is not conclusively proven from the inspected files alone**

That is the evidence-based answer.

## 15. How ACP Mode Renders Runtime Output

`ACPSession.prompt()` is a good lens for understanding output parsing.

It iterates over `self._cli.run(...)` and pattern-matches on each wire message.

Examples:

- `ThinkPart` → `_send_thinking(...)`
- `TextPart` → `_send_text(...)`
- `ToolCall` → `_send_tool_call(...)`
- `ToolCallPart` → `_send_tool_call_part(...)`
- `ToolResult` → `_send_tool_result(...)`
- `ApprovalRequest` → `_handle_approval_request(...)`

This means ACP mode is essentially a **wire-event interpreter**.

That is exactly the right abstraction: frontend protocol layers should interpret structured runtime events, not re-implement agent logic.

## 16. Why Tool Call IDs Are Prefixed in ACP

ACP session code prefixes tool call IDs with a per-turn ID:

- `turn_id/tool_call_id`

This solves a subtle but real problem.

If a tool call is rejected or cancelled and not appended into context, a later step may emit the same underlying tool call ID from the LLM side. Prefixing with turn ID preserves frontend uniqueness and avoids collision/confusion.

This is a strong example of protocol-aware runtime hygiene.

## 17. ACP-Specific Tool Replacement

One of the most interesting design details is in `acp/tools.py`.

If the client advertises terminal capability, the normal `Shell` tool is replaced with an ACP-backed `Terminal` tool.

This means:

- the tool name/schema stay consistent
- execution backend changes depending on frontend capabilities
- output is streamed using terminal-specific protocol content

That is an elegant form of capability-sensitive tool adaptation.

## 18. What “Mode Differences” Really Mean in This Repository

The key insight is that most modes do **not** change the fundamental LLM output contract.

Instead, modes mostly change one or more of:

- prompt constraints
- approval behavior
- tool availability/adaptation
- rendering/transport layer
- final output filtering

So the runtime has one underlying structured agent output model, and different modes are largely alternate projections of that model.

## 19. Strengths of the Output/Parsing Design

The strongest aspects are:

- structured event model rather than text-only output
- support for streamed text and streamed tool-call arguments
- separate mappings for LLM context vs frontend protocol rendering
- diff and plan outputs as first-class structured artifacts
- capability-sensitive tool replacement in ACP
- approval and request types integrated into the same wire model

This is a fairly sophisticated output architecture.

## 20. Open Questions / What Still Needs Deeper Verification

The main areas still worth deeper inspection are:

- the exact internal `kosong.step(...)` result model
- whether normal multi-tool parallelism is supported and under what constraints
- how shell and web frontends differ in rendering rich tool progress
- whether different providers yield materially different streaming granularity

## 21. Final Summary

Kimi Code CLI uses a **structured, streaming, event-based output architecture**.

The core principles are:

- model output is not treated as a plain final string
- tool calls are structured and streamable
- tool arguments can be parsed incrementally
- tool results are mapped both for LLM reuse and frontend display
- plan, diff, approval, and question flows are all first-class message types
- different modes mostly change transport/rendering and behavioral constraints, not the core output model

This makes the project much more like an agent runtime platform than a terminal wrapper around a chat completion API.
