# Kimi Code CLI: Overall Architecture and System Design

## 1. Executive Summary

Kimi Code CLI is a Python-based terminal agent for software engineering workflows. At a high level, it is **not** just a single chat loop wrapped in a CLI. It is a layered system composed of:

- A **CLI and application bootstrap layer** that loads configuration, selects a model, restores sessions, and instantiates the agent runtime.
- A **runtime and agent specification layer** that defines which prompt, tools, subagents, and environment metadata an agent should use.
- A **core agent loop** (`KimiSoul`) that repeatedly calls the LLM, executes tools, appends results back into context, checkpoints state, manages approval, and compacts context when needed.
- A **context persistence layer** that stores system prompt, message history, token usage, and checkpoints in JSONL.
- A **tooling layer** that supports built-in tools, MCP tools, and subagent/task delegation.
- A **UI/protocol layer** that decouples the runtime from frontends through a `Wire` event stream; the same core loop can power shell UI, print mode, ACP server mode, and a web UI.
- A **skills layer** that injects reusable task instructions from `SKILL.md` files, optionally with flow-style execution.

Architecturally, the project is best understood as a **general agent engine with multiple frontends**, rather than a terminal chatbot with a few helper commands.

## 2. Repository Topology

At the repository root, the most important top-level areas are:

- `src/kimi_cli/`
  - Main Python application.
  - Contains CLI, runtime, loop, tools, UI bridges, ACP server, wire protocol, skill discovery, config, and sessions.
- `web/`
  - Web frontend application.
  - Used by the web UI mode of Kimi Code CLI.
- `vis/`
  - Visualization-related assets and code paths.
- `packages/`
  - Workspace packages, notably `kosong` and `kaos` used by the main runtime.
- `sdks/`
  - SDK packages, including `kimi-sdk`.
- `tests/`, `tests_ai/`, `tests_e2e/`
  - Unit/integration/end-to-end coverage.
- `docs/`
  - End-user and developer docs.
- `src/kimi_cli/agents/`
  - Built-in agent definitions and prompts.
- `src/kimi_cli/tools/`
  - Built-in tool implementations.
- `src/kimi_cli/soul/`
  - The most important internal subsystem: agent loop, context, approval, compaction, injections, agent runtime.

From a code-reading perspective, the shortest path to understanding the system is:

1. `src/kimi_cli/cli/__init__.py`
2. `src/kimi_cli/app.py`
3. `src/kimi_cli/soul/agent.py`
4. `src/kimi_cli/soul/kimisoul.py`
5. `src/kimi_cli/soul/context.py`
6. `src/kimi_cli/soul/toolset.py`
7. `src/kimi_cli/wire/types.py`
8. `src/kimi_cli/tools/`
9. `src/kimi_cli/skill/__init__.py`
10. `src/kimi_cli/acp/server.py`

## 3. Main Execution Flow

## 3.1 CLI entry

The CLI entrypoint is exposed through:

- `kimi = "kimi_cli.cli:cli"`
- `kimi-cli = "kimi_cli.cli:cli"`

in `pyproject.toml`.

The root command in `src/kimi_cli/cli/__init__.py` parses:

- Working directory and additional workspace directories
- Session selection / continuation
- Config source and model selection
- Thinking mode and yolo approval mode
- UI mode (`shell`, `print`, `acp`, `wire`)
- Agent selection and agent file override
- MCP config sources
- Skills directory override
- Loop control settings such as max steps/retries

This file is not the true “brain.” Its role is orchestration and mode selection.

## 3.2 App bootstrap

The real application bootstrap happens in `src/kimi_cli/app.py`, especially `KimiCLI.create(...)`.

That function performs the following sequence:

1. Load and validate configuration.
2. Resolve loop-control overrides.
3. Build OAuth manager.
4. Resolve model/provider from config and environment variables.
5. Construct the LLM abstraction via `create_llm(...)`.
6. Build the `Runtime` object.
7. Load the agent spec and system prompt.
8. Restore persisted context from the session context file.
9. Reuse persisted system prompt if the session already has one.
10. Instantiate `KimiSoul`.

This separation is important:

- `KimiCLI` is the application facade.
- `Runtime` is the dependency bag + execution environment.
- `Agent` is the prompt/tool/subagent specification.
- `KimiSoul` is the stateful execution engine.

## 3.3 Running the soul

`KimiCLI.run(...)` creates a `Wire`, starts a UI loop, and launches `run_soul(...)`.

`run_soul(...)` in `src/kimi_cli/soul/__init__.py` is the bridge that connects:

- the runtime side (`soul.run(...)`)
- the UI side (`Wire` consumer)
- cancellation handling

This is a clean separation. The core loop does not directly depend on shell rendering or ACP streaming. Instead, it emits `WireMessage` events, and each frontend chooses how to visualize them.

## 4. Core Architectural Components

## 4.1 `Runtime`: the dependency and environment container

Defined in `src/kimi_cli/soul/agent.py`, `Runtime` contains:

- `config`
- `oauth`
- `llm`
- `session`
- built-in system prompt args
- `denwa_renji` for D-Mail / checkpoint-related time-travel behavior
- `approval`
- `labor_market` for subagent management
- environment detection
- discovered skills
- additional workspace directories

Conceptually, `Runtime` is the mutable operating envelope of the agent.

It is also where a large amount of **prompt-time world knowledge** is prepared. During `Runtime.create(...)`, the system gathers:

- directory listing of the work directory
- `AGENTS.md` content
- environment metadata
- discovered skills
- additional directory listings

These are converted into built-in prompt arguments:

- `KIMI_NOW`
- `KIMI_WORK_DIR`
- `KIMI_WORK_DIR_LS`
- `KIMI_AGENTS_MD`
- `KIMI_SKILLS`
- `KIMI_ADDITIONAL_DIRS_INFO`

This is a major design choice: the system prompt is **parameterized with live workspace information**, not kept as a static string.

## 4.2 `Agent`: prompt + tools + runtime

An `Agent` is essentially:

- `name`
- `system_prompt`
- `toolset`
- `runtime`

It is loaded from an **agent spec** YAML.

The spec supports:

- inheritance via `extend`
- custom system prompt path
- prompt arguments
- tool inclusion/exclusion
- fixed subagents

This means Kimi CLI treats “agent behavior” as a configurable package rather than hardcoding a single assistant identity.

## 4.3 `KimiSoul`: the real execution engine

`src/kimi_cli/soul/kimisoul.py` is the most important file in the repository.

This class owns:

- context history
- loop control
- slash commands
- plan mode state
- dynamic injections
- checkpoint behavior
- step execution and retry logic
- compaction
- tool result integration

If you want to understand how the agent actually behaves, this is the file to study first.

## 4.4 `Context`: persistent conversation state

`src/kimi_cli/soul/context.py` persists the live conversation into a JSONL file.

It stores not only normal messages, but also internal records:

- `_system_prompt`
- `_usage`
- `_checkpoint`

This design has several implications:

- Context is durable across sessions.
- The exact system prompt used for a session is pinned and persisted.
- Token count is tracked incrementally rather than recomputed every time.
- Time-travel / checkpoint semantics are implemented by file rotation + replay.

This is more advanced than a simple in-memory chat history.

## 4.5 `KimiToolset`: dynamic tool loading and execution

`src/kimi_cli/soul/toolset.py` loads tools from import paths such as:

- `kimi_cli.tools.shell:Shell`
- `kimi_cli.tools.file.read:ReadFile`
- MCP-generated tools from remote servers

It provides:

- built-in tool registry
- hidden-tool support
- tool lookup by name or type
- dependency injection into tool constructors
- MCP tool loading in the background

This is a plugin architecture with typed dependency injection.

## 4.6 `Wire`: runtime/frontend decoupling

The `Wire` layer is one of the better architectural decisions in the project.

Instead of binding the loop directly to one UI, the soul emits structured events like:

- `TurnBegin`
- `StepBegin`
- `StatusUpdate`
- `ApprovalRequest`
- `ToolCall...`
- `CompactionBegin/End`
- `TurnEnd`

This lets the same runtime power:

- shell UI
- print mode
- ACP mode
- wire stdio mode
- web integrations

This makes the agent engine reusable and testable.

## 5. How LLM Invocation Works

## 5.1 Provider abstraction

The LLM abstraction is in `src/kimi_cli/llm.py`.

The project supports multiple provider types:

- `kimi`
- `openai_legacy`
- `openai_responses`
- `anthropic`
- `gemini` / `google_genai`
- `vertexai`
- `_echo`
- `_scripted_echo`
- `_chaos`

The provider-specific SDK objects are wrapped behind a common `LLM` dataclass containing:

- `chat_provider`
- `max_context_size`
- `capabilities`
- raw model/provider config

Kimi CLI itself does not directly code against each provider API inside the loop. It delegates that to the `kosong` abstraction layer.

## 5.2 LLM creation

`create_llm(...)` resolves:

- base URL
- API key / OAuth-backed auth
- model name
- generation kwargs such as temperature, top_p, max_tokens
- session-aware prompt cache key for Kimi provider
- thinking mode

This is notable because the runtime can alter provider behavior through:

- config file
- environment variables
- OAuth refresh
- per-session metadata

## 5.3 Actual step call

The actual agent-step call happens in `KimiSoul._step()`:

- collect dynamic injections
- append injection reminders into context as user content
- normalize history
- call `kosong.step(...)`

The call signature effectively consists of:

- `chat_provider`
- `system_prompt`
- `toolset`
- message history
- streaming callbacks for message parts and tool results

So the heart of inference is not a raw “chat completion” call; it is a structured agent step through `kosong`, with tool-call support and streaming hooks.

## 6. Prompt Architecture

## 6.1 System prompt source

System prompts are not embedded directly in Python. They are loaded from agent spec files via:

- `src/kimi_cli/agentspec.py`
- `_load_system_prompt(...)` in `src/kimi_cli/soul/agent.py`

The prompt file is rendered through Jinja with `${...}` placeholders. That allows live substitution of runtime state.

## 6.2 System prompt construction model

The system prompt is constructed from two categories of arguments:

- Built-in runtime arguments gathered automatically from the workspace
- Agent-spec-defined custom arguments

This means prompt engineering in Kimi CLI is partly static and partly runtime-generated.

A key architectural point is that **system prompt generation happens once per agent load**, and the resulting prompt is then persisted into the session context file. On later resumes, the persisted prompt can override a newly loaded one. This favors session continuity over “latest prompt always wins.”

## 6.3 Prompt layering

The effective prompt seen by the model is layered as follows:

1. Agent system prompt
2. Context history
3. Dynamic injected reminders
4. Current user turn / steer inputs
5. Tool results and any generated follow-up messages

This is important: plan mode, skills, D-Mail, and runtime reminders are not separate out-of-band channels. They are converted into messages and merged into the same LLM-visible conversation history.

## 7. Loop Design

## 7.1 Turn loop vs step loop

Kimi CLI distinguishes:

- a **turn**: one user request lifecycle
- a **step**: one LLM call plus any resulting tool execution

`_agent_loop()` repeatedly executes steps until one of the following happens:

- the assistant stops without tool calls
- max step count is reached
- an exception interrupts the run
- control flow rewinds to a checkpoint via D-Mail behavior

This distinction is fundamental. The loop is not “one prompt in, one response out.” It is a bounded iterative agent loop.

## 7.2 Step lifecycle

Inside `_agent_loop()` a typical step is:

1. Emit `StepBegin`
2. Start approval-piping task
3. Auto-compact if token budget is too high
4. Create checkpoint
5. Execute `_step()`
6. Consume tool results and steers
7. Either continue or terminate turn

Inside `_step()`:

1. Gather injections
2. Normalize history
3. Call `kosong.step(...)`
4. Emit usage/status updates
5. Await tool results
6. Append assistant message and tool messages into context
7. Detect rejection / D-Mail / further tool calls / final answer

This is a robust and explicit orchestration loop.

## 7.3 Retry and resilience

Each step is wrapped with retry logic using `tenacity`, covering retryable provider failures such as:

- connection failures
- timeouts
- empty responses
- 429 / 500 / 502 / 503 status codes

There is also provider-level recovery for chat providers implementing retryable behavior.

This makes the loop operationally resilient, not just logically correct.

## 7.4 Compaction

The loop triggers compaction when token usage approaches limits using:

- current tracked token count
- reserved response budget
- compaction ratio threshold

Compaction:

1. calls a compaction strategy (`SimpleCompaction`)
2. clears existing context
3. rewrites system prompt
4. creates a fresh checkpoint
5. appends compacted messages
6. updates estimated token count

This means context management is a first-class loop concern rather than an afterthought.

## 8. Context Management Model

## 8.1 Persistence format

The context file is JSONL. This is a strong choice because it supports:

- append-only writes
- crash resilience
- easy inspection and replay
- special internal record types alongside normal messages

## 8.2 Checkpointing

Before each step, the system creates a checkpoint record. Optionally, it can also append a synthetic user message such as `CHECKPOINT n`.

Checkpoints enable:

- rollback to prior state
- D-Mail-based non-linear control flow
- context surgery without recomputing everything

## 8.3 Revert and rotation

When reverting, the system rotates the old file, reconstructs the context up to the target checkpoint, and resumes from there.

This is more reliable than trying to mutate in-memory state only.

## 8.4 Token accounting

Token count is explicitly persisted using `_usage` records. The loop updates:

- input token count after step result usage is known
- total token count after growing context

That tracked count is then used for compaction decisions.

## 9. Skills and Rules

## 9.1 Skills discovery

Skill discovery is implemented in `src/kimi_cli/skill/__init__.py`.

The search roots are layered:

- built-in bundled skills
- user-level skills directories
- project-level skills directories
- optional override directory

Supported candidate directories include `.agents/skills`, `.kimi/skills`, `.claude/skills`, `.codex/skills`, etc.

That tells us Kimi CLI intentionally interoperates with conventions from adjacent agent ecosystems.

## 9.2 Skill structure

A skill is generally a directory with `SKILL.md`.

The parser extracts:

- `name`
- `description`
- `type` (`standard` or `flow`)

If the skill is a flow skill, it can parse Mermaid or D2 diagrams into executable flow structures.

## 9.3 Skill execution model

In `KimiSoul`, skills are exposed as slash-command-like affordances. For standard skills, the runtime reads `SKILL.md` and turns it into a user message. If extra command arguments exist, they are appended under `User request:`.

This is an elegant design: a skill is not necessarily a hardcoded tool. It can be a reusable prompt macro.

## 9.4 Rules

There is no single “rules engine” module in the same sense as a policy DSL. Instead, rules are distributed across:

- system prompt instructions
- agent spec tool allow/exclude choices
- approval policy and yolo state
- plan mode restrictions
- slash-command handling
- tool-level permission behavior

So “rules” in Kimi CLI are architectural rather than centralized.

## 10. Tool Calling Architecture

## 10.1 Tool sources

Kimi CLI tools come from three places:

- built-in Python tools under `src/kimi_cli/tools/`
- MCP tools loaded from configured MCP servers
- subagent/task orchestration tools

## 10.2 Tool loading

`KimiToolset.load_tools(...)` dynamically imports each tool class and injects constructor dependencies based on parameter type annotations.

This avoids giant switch statements and makes tools pluggable.

## 10.3 Tool execution flow

At runtime, the LLM issues tool calls via the `kosong` step abstraction. The loop streams tool-call events to the UI through `Wire`, waits for tool results, then converts those tool results back into messages appended to context.

That means tool execution is not a side-channel. Tool outputs are part of the ongoing reasoning state.

## 10.4 MCP tools

MCP support is substantial rather than superficial:

- MCP configs can come from CLI flags or management commands
- servers can be loaded in the background
- OAuth-authenticated MCP servers are supported
- tool metadata from remote servers is converted into local callable tools

The loading logic also tracks MCP server connection status and surfaces loading events to the UI.

## 10.5 Approval mediation

The soul runs an approval-piping task that relays internal approval requests into `Wire` messages. Frontends resolve them and send responses back, which are then fed into the core approval system.

This decoupling is key: tools can request approval without knowing which UI is handling the request.

## 11. Session and State Management

Sessions are persisted per work directory. The `Session` model stores:

- session ID
- work directory
- context file path
- wire log path
- session state
- title / updated timestamp

The session state persists more than chat text. It also includes things like:

- approval preferences
- dynamic subagents
- plan-mode-related state
- additional directories

This is why the system can resume not only a conversation but also parts of the runtime environment.

## 12. UI and Protocol Modes

## 12.1 Shell mode

The default interactive experience. It presents the richest terminal UI and supports shell-like interaction.

## 12.2 Print mode

A non-interactive mode suitable for scripts and pipeline usage.

## 12.3 ACP mode

ACP support is implemented in `src/kimi_cli/acp/server.py` and related ACP modules.

This is the main IDE integration surface. Instead of embedding VS Code specifics directly in the core, Kimi CLI exposes a protocol server that ACP-capable IDEs can connect to.

## 12.4 Wire stdio mode

A lower-level structured event transport for integrations.

## 12.5 Web mode

The repository also contains a `web/` application and server-side support under `src/kimi_cli/web/`, indicating a separate browser-based experience layered on top of the same core runtime.

## 13. VS Code Integration: What Exists at a High Level

The repository README explicitly states that Kimi Code CLI can integrate with VS Code via a separate **Kimi Code VS Code Extension**. Inside this repository, what clearly exists is:

- ACP support for IDE integration
- web/open-in integration hooks
- protocol/server infrastructure

What is **not clearly present in this repository** is the full source tree of the VS Code extension itself. The extension appears to be an external integration product, while this repo provides the backend/runtime side needed by editor clients.

Therefore, the correct architectural interpretation is:

- this repository **supports** VS Code integration
- but it does so primarily through ACP/server capabilities, not by containing the full VS Code extension implementation in the main Python source tree

A later deep-dive document should distinguish carefully between:

- protocol support present here
- extension host code that may live elsewhere

## 14. Diff, Merge, and Edit Presentation

The project has explicit diff utilities in `src/kimi_cli/utils/diff.py`.

Two important helper functions exist:

- `format_unified_diff(...)`
- `build_diff_blocks(...)`

These are used to:

- generate unified diffs for edits
- generate structured diff display blocks for UI rendering and approval prompts

This is important because Kimi CLI does not treat file editing as a blind write. It has a displayable edit representation that can be surfaced to shell, ACP clients, or web clients.

In architectural terms, diff generation is part of the **human-in-the-loop safety UX**.

## 15. Code Search, Indexing, and Retrieval: High-Level Assessment

At the overall architecture level, the project appears to favor **tool-based retrieval over heavyweight prebuilt semantic indexing**.

What is clearly present:

- file search and grep via local tools
- ripgrep-backed code retrieval (`Grep` tool)
- file reading and directory listing tools
- workspace snapshot information injected into prompt
- additional directory support
- skill discovery indexes by skill name

What is not clearly visible from the core architecture scan:

- a full embedding-based code index
- a language-server-backed symbol graph as a central subsystem
- a dedicated chunk-ranking engine in the core loop

So the current architecture likely relies on:

- **fast deterministic search tools**
- agent-driven iterative retrieval
- context compaction and prompt discipline

rather than on an always-on semantic vector index.

This is a crucial architectural conclusion and should be examined in a later dedicated document.

## 16. SDK and Workspace Dependencies

The project uses several important workspace dependencies:

- `kosong`
  - LLM abstraction, message types, tooling protocol, provider integrations, step execution
- `pykaos` / `kaos`
  - environment abstraction over local and potentially remote filesystem/OS actions
- `fastmcp`
  - MCP client and server integration

There is also an `sdks/` directory, but from the overall architecture standpoint, the main runtime is more tightly coupled to `kosong`, `kaos`, and `fastmcp` than to an end-user application SDK.

A dedicated SDK deep-dive should map:

- which APIs are internal framework APIs
- which APIs are intended for external consumers
- how much of `sdks/kimi-sdk` is actually used by the CLI runtime

## 17. Architectural Strengths

The strongest parts of this design are:

- **Clear runtime/frontend separation** via `Wire`
- **Configurable agent spec model** with prompt/tool/subagent composition
- **Persistent JSONL context with checkpoints**
- **Robust tool architecture** with MCP extension support
- **Operational resilience** through retries and provider recovery
- **Skill system** that is simple but composable
- **Protocol-oriented integration** via ACP rather than UI-specific hacks

## 18. Architectural Tensions and Core Problems

Several architectural tensions stand out.

### 18.1 Retrieval remains mostly agent-driven

The architecture appears strong in tool-based search, but less obviously strong in **systematic code indexing and ranking**. This may be acceptable for many workflows, but it can become a bottleneck for large repositories.

### 18.2 Prompt/context quality depends heavily on tool strategy

Because much of retrieval is iterative, the quality of the final prompt context depends on:

- which tools the model chooses
- how much context it decides to read
- whether it selects the right snippets early

This is a common challenge in agent systems.

### 18.3 Rules are distributed, not centralized

This gives flexibility, but makes policy reasoning harder. There is no single “rule engine” module to inspect.

### 18.4 Session-pinned system prompt can diverge from code evolution

Persisting the system prompt per session improves continuity, but it also means a long-lived session may continue using an old prompt after the codebase’s prompt templates change.

### 18.5 Multi-mode complexity is real

Supporting shell, print, ACP, wire, and web from one core is powerful, but increases the surface area for event compatibility and UI-state synchronization bugs.

## 19. What to Study Next

The next documents should go deeper in this order:

1. **Loop, prompt, and context deep dive**
   - The single most important internal subsystem.
2. **Tool calling and output parsing deep dive**
   - How LLM outputs are structured, streamed, parsed, and executed.
3. **Code retrieval and indexing deep dive**
   - Search strategy, grep tooling, snippet selection, context budgeting.
4. **Skills, rules, and subagents deep dive**
   - How reusable behaviors are composed.
5. **ACP / IDE / VS Code integration deep dive**
   - What is implemented here versus what belongs to external clients/extensions.
6. **Diff/edit/approval pipeline deep dive**
   - How edits are shown, approved, and persisted.
7. **SDK and workspace dependency map**
   - Especially `kosong`, `kaos`, and `fastmcp` interfaces.

## 20. Final Summary

Kimi Code CLI is a thoughtfully layered agent system centered on a reusable execution engine (`KimiSoul`) rather than a one-off terminal app. Its most important design ideas are:

- agent behavior is defined by **agent specs**
- runtime state is carried by a **Runtime** object
- execution is performed by an iterative **step-based loop**
- history is persisted through **JSONL context with checkpoints**
- capabilities are extended through **tools, MCP, skills, and subagents**
- frontends consume a common **Wire** event stream

The most important subsystem to understand next is the combination of:

- loop mechanics
- prompt layering
- context growth/compaction
- tool-result integration

That subsystem determines whether the project is merely functional or genuinely strong as an engineering agent.
