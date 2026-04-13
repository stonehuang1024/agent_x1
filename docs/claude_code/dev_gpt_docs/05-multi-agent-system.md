## Document 5: Multi-Agent System

### Scope

This document analyzes Claude Code’s multi-agent architecture: how agents are defined, spawned, isolated, resumed, permissioned, and coordinated across both in-process subagents and teammate/swarm workflows.

Primary code references:

- `src/tools/AgentTool/AgentTool.tsx`
- `src/tools/AgentTool/runAgent.ts`
- `src/tools/AgentTool/forkSubagent.ts`
- `src/tools/AgentTool/loadAgentsDir.ts`
- `src/tools/AgentTool/builtInAgents.ts`
- `src/tools/shared/spawnMultiAgent.ts`
- `src/utils/agentContext.ts`
- `src/utils/forkedAgent.ts`
- `src/hooks/toolPermission/handlers/swarmWorkerHandler.ts`
- `src/hooks/useSwarmInitialization.ts`
- `src/utils/swarm/reconnection.ts`
- `src/Tool.ts`
- `src/query.ts`
- `src/QueryEngine.ts`

---

## 1. Executive Summary

### What

Claude Code’s multi-agent system is **not one mechanism but a family of delegation modes** built on top of the same shared query runtime.

From the inspected code, there are at least four distinct agent execution styles:

1. **Main-thread agent specialization**
   - a session can itself run under an agent-specific prompt or role
2. **In-process subagents** via the `Agent` tool
   - delegated workers that re-enter `query()` inside the same process
3. **Fork workers**
   - a special subagent path that inherits the parent’s full conversation bytes for prompt-cache sharing
4. **Teammates / swarm agents**
   - coordinated workers that may run in-process or out-of-process (tmux/iTerm2), with mailbox-based coordination and leader-mediated permissions

### Why

The project needs multiple delegation patterns because not all “multi-agent” work is the same:

- sometimes the parent needs a quick specialist search worker
- sometimes it needs a long-running background task
- sometimes it needs a byte-identical fork of the current conversation
- sometimes it needs persistent teammates with separate task identities and team coordination

A single generic “spawn agent” primitive would not fit all of those cases cleanly.

### How

The architecture works by reusing the same core runtime layers:

- **agent definitions** describe roles, prompts, tools, memory, hooks, model, permissions
- **AgentTool** maps model-emitted tool calls into agent selection and spawn mode
- **runAgent(...)** constructs a child `ToolUseContext` and re-enters `query()`
- **spawnMultiAgent.ts** handles teammate creation for in-process and pane-based swarm workers
- **AsyncLocalStorage agent context** preserves per-agent telemetry identity inside concurrent in-process execution
- **transcript metadata and sidechain storage** preserve lineage and resumability

### Architectural Classification

| Dimension | Classification | Why it fits |
|---|---|---|
| execution model | **Shared-core recursive agent runtime** | child agents mostly reuse `query()` rather than a second runtime |
| coordination style | **Hybrid delegation model** | local subagents, forked workers, and swarm teammates use different coordination paths |
| isolation model | **Configurable from shared-context to worktree/pane isolation** | fork shares context aggressively; swarm and worktree modes isolate more strongly |
| persistence model | **Sidechain transcripts + team metadata** | subagents and teammates preserve lineage outside the main transcript |
| permission model | **Context-sensitive and leader-mediated** | subagents may bubble or suppress prompts; swarm workers forward approval to leader |

---

## 2. The Core Architectural Idea

The most important design choice is this:

> **Multi-agent behavior is added as a thin orchestration layer over the same query/tool substrate, rather than as a separate agent framework.**

That means Claude Code avoids building two fundamentally different systems:

- one for “main chat”
- one for “agents”

Instead, it keeps one main execution substrate and varies:

- prompt identity
- tool pool
- permission mode
- transcript storage
- async vs sync execution
- inter-agent transport

This is a strong and coherent architecture decision.

---

## 3. Agent Definitions: The Type System for Delegation

## 3.1 What

`src/tools/AgentTool/loadAgentsDir.ts` defines the runtime contract for an `AgentDefinition`.

An agent can carry all of the following:

- `agentType`
- `whenToUse`
- `tools`
- `disallowedTools`
- `skills`
- `mcpServers`
- `hooks`
- `model`
- `effort`
- `permissionMode`
- `maxTurns`
- `background`
- `initialPrompt`
- `memory`
- `isolation`
- `omitClaudeMd`
- prompt material via `getSystemPrompt(...)`

### Why this matters

This means an agent is **not just a prompt preset**.

It is a structured runtime role describing:

- how it should think
- what it can use
- how much authority it has
- whether it persists memory
- whether it runs in background
- whether it requires worktree/remote isolation
- whether it augments itself with MCP servers or hooks

So agents are first-class operating modes, not mere personas.

---

## 3.2 Built-in, custom, and plugin agents

The code defines three broad categories:

- **built-in agents**
- **custom agents** from user/project/policy settings and markdown
- **plugin agents**

### Built-in agents

`src/tools/AgentTool/builtInAgents.ts` shows built-ins such as:

- `GENERAL_PURPOSE_AGENT`
- `STATUSLINE_SETUP_AGENT`
- `EXPLORE_AGENT`
- `PLAN_AGENT`
- `CLAUDE_CODE_GUIDE_AGENT`
- `VERIFICATION_AGENT`
- coordinator-mode worker agents when that mode is enabled

### Custom agents

Custom agents come from markdown and JSON sources via `loadAgentsDir.ts`.

These support frontmatter-defined behavior such as:

- tools and disallowed tools
- memory
- background
- isolation
- hooks
- MCP servers
- effort and permission mode

### Plugin agents

Plugin agents reuse the same runtime type but are sourced from plugin loading.

### Why this is architecturally elegant

All agent categories converge on one runtime `AgentDefinition` contract.

That gives the system one spawn/orchestration path even though the authoring surfaces differ.

---

## 3.3 Agent precedence and activation

`getActiveAgentsFromList(...)` groups agents by source and then resolves conflicts by `agentType`.

The source layering observed in code is roughly:

- built-in
- plugin
- user settings
- project settings
- flag settings
- policy settings

Later writes into the map win for the same `agentType`.

### Why this matters

The agent system supports override behavior similar to prompt/config layering.

That makes the multi-agent architecture consistent with the rest of the product’s layered customization model.

---

## 4. The `Agent` Tool as the Main Delegation Gateway

## 4.1 What

The large [AgentTool.tsx](/Users/simonwang/agent/claude-code/src/tools/AgentTool/AgentTool.tsx) file is the main gateway where model-emitted `tool_use` turns into agent delegation.

The grep evidence shows its schema includes fields such as:

- `subagent_type`
- `run_in_background`
- `isolation`
- `cwd`

and that its `call(...)` method routes into:

- normal subagent execution
- fork-subagent behavior
- background teammate-style spawn behavior
- resume logic

### Why this is important

The multi-agent system is not triggered by special hidden runtime rules. It is surfaced to the model through a normal tool contract.

That means agent delegation is treated as just another action in the ReAct loop — though a very special one.

---

## 4.2 Why delegation is modeled as a tool

This is a strong design choice.

Benefits:

- the model decides when delegation is useful
- delegation inherits all normal tool machinery: schemas, permissions, transcript integration, progress rendering
- agent spawning becomes observable and permissionable
- delegation composes naturally with the rest of the tool system

Tradeoff:

- the `Agent` tool becomes very powerful and correspondingly complex

Still, for Claude Code’s architecture, this choice is correct.

---

## 5. The Main Subagent Execution Path: `runAgent(...)`

## 5.1 What

`src/tools/AgentTool/runAgent.ts` is the heart of in-process subagent execution.

Its flow is approximately:

1. resolve the child model
2. assign a new `agentId`
3. optionally route transcript into a subdirectory
4. register Perfetto lineage
5. fork or build initial message context
6. derive user/system context
7. customize permission mode and app-state view for the child
8. resolve child tools
9. build child system prompt
10. create a child abort controller
11. run SubagentStart hooks and preload skills
12. initialize agent-specific MCP servers
13. create a child `ToolUseContext`
14. optionally expose cache-safe params
15. persist sidechain transcript metadata
16. re-enter `query(...)`
17. record yielded messages and cleanup on exit

### Why this is the architectural center

This function proves that Claude Code’s multi-agent model is primarily **recursive reuse of the main agent loop**.

That is the most important structural fact in the entire subsystem.

---

## 5.2 Query recursion, not alternate runtime

`runAgent(...)` literally calls `query({ ... })` with a new context.

That means a child agent gets:

- the same model loop
- the same tool runtime
- the same stop hooks
- the same compaction behavior
- the same tool-result semantics
- the same stream event model

but with modified parameters.

### Why this is excellent design

This avoids “agent drift” where subagents behave fundamentally differently from the main runtime.

### Cost

Some subagent-specific logic must still be layered around `query()`, which is why `runAgent.ts` is substantial.

---

## 6. Child Context Construction and Isolation Strategy

## 6.1 `createSubagentContext(...)`

`runAgent.ts` uses `createSubagentContext(...)` from `src/utils/forkedAgent.ts` to create the child `ToolUseContext`.

From inspected comments and call sites, this helper handles:

- message ownership
- cloned or shared read-file caches
- child `agentId` / `agentType`
- app-state access behavior
- content replacement state
- abort controller choice
- callback sharing for sync vs async subagents

### Why this matters

This is the boundary where “same runtime” becomes “different agent instance.”

The design carefully decides what to share and what to clone.

---

## 6.2 Sync vs async subagents

`runAgent(...)` distinguishes sync and async behavior in important ways.

### Shared for sync agents

- parent abort controller
- some app-state/update callbacks
- terminal-interactive behavior more naturally

### Isolated for async agents

- new unlinked `AbortController`
- no direct permission prompting unless explicitly allowed
- more background-task semantics

### Why this split exists

A synchronous delegated worker behaves like a nested assistant action.

A background agent behaves more like an independent task process.

The code correctly models those as different operational modes.

---

## 6.3 Read-file cache and message context

When forking from parent messages, `runAgent.ts`:

- filters incomplete tool calls from the inherited history
- may clone the parent read-file state cache
- otherwise starts with a fresh bounded cache

### Why this is smart

It preserves context reuse where beneficial, but avoids invalid transcript states and unbounded leakage.

The helper `filterIncompleteToolCalls(...)` is especially telling: the child must never inherit an assistant tool-use message whose `tool_result` never arrived.

That is API correctness thinking, not just convenience.

---

## 7. Agent Prompt and Tool Pool Customization

## 7.1 Agent-specific system prompt

`runAgent.ts` builds an `agentSystemPrompt` by calling the agent definition’s `getSystemPrompt(...)`, then wrapping it through:

- `enhanceSystemPromptWithEnvDetails(...)`
- `asSystemPrompt(...)`

If a prompt override is provided, it can bypass normal agent prompt reconstruction.

### Why

Every child agent gets a distinct role and context, but still sees the same environment details machinery as the main runtime.

---

## 7.2 Tool pool resolution

The child tool pool is built from:

- `availableTools` provided by the caller
- `resolveAgentTools(...)` filtering
- optional agent-specific MCP tools
- deduplication by tool name

### Why this matters

Agents are not only prompt-isolated; they are **capability-isolated**.

That is crucial to turning them into meaningful specialists instead of clones.

---

## 7.3 Permission-mode override per agent

`runAgent.ts` derives an `agentGetAppState()` view that can override:

- `toolPermissionContext.mode`
- `shouldAvoidPermissionPrompts`
- `awaitAutomatedChecksBeforeDialog`
- `alwaysAllowRules.session`
- `effortValue`

### Why this is powerful

The child agent can operate under a different authority regime than the parent.

That is essential for patterns like:

- planner/searcher agents with low privileges
- bubble-mode agents whose prompts surface back to parent terminal
- background workers that cannot open UI dialogs

---

## 8. Hooks, Skills, and MCP as Agent-Level Extensions

## 8.1 Hooks

Agent definitions can include frontmatter hooks.

`runAgent.ts` registers them for the agent lifecycle and converts Stop hooks to SubagentStop behavior for agent scope.

### Why

An agent is not just a prompt. It can carry its own lifecycle policy.

---

## 8.2 Skills

Agent definitions can preload prompt-based skills.

The code:

- resolves the skill name
- supports plugin-prefixed resolution
- loads skill prompt content
- injects it as meta user messages into initial agent messages

### Why this matters

This means an agent can boot with role-specific working knowledge without inflating the parent prompt.

---

## 8.3 Agent-specific MCP servers

`initializeAgentMcpServers(...)` shows that agents can define additive MCP servers in frontmatter.

The logic supports:

- named existing servers
- inline server definitions
- admin-trust rules for plugin-only restrictions
- per-agent cleanup of newly created clients

### Why this is significant

An agent can expand its tool universe dynamically at spawn time.

This is a very strong specialization mechanism.

---

## 9. Fork Subagents: Cache-Identical Child Workers

## 9.1 What

`src/tools/AgentTool/forkSubagent.ts` defines a special fork path.

Key properties from code/comments:

- `subagent_type` may be omitted when the feature is on
- omission routes to an implicit fork worker
- child inherits the parent’s full conversation context and system prompt bytes
- all spawns run in background for a uniform task-notification model
- fork is mutually exclusive with coordinator mode

### Why this exists

This is not just another child-agent type.

It is a **prompt-cache-optimized worker cloning strategy**.

---

## 9.2 Byte-identical prefix design

The code comments are unusually explicit:

- fork children must produce byte-identical API request prefixes for prompt-cache sharing
- the parent assistant message is cloned with all `tool_use` blocks intact
- the child receives placeholder `tool_result` blocks with identical text
- only the final directive block differs per child

### Why this is architecturally impressive

This is a very advanced optimization.

The multi-agent system is not just about delegation correctness — it is also designed around prompt-cache economics.

---

## 9.3 Recursive fork guard

`isInForkChild(...)` and guard logic in `AgentTool.tsx` prevent recursive forking.

### Why

Fork children keep the `Agent` tool in their pool for cache-identical tool definitions, so the guard must happen at call time rather than by simply removing the tool.

This is subtle and well thought out.

---

## 10. Transcript, Metadata, and Session Lineage

## 10.1 Sidechain transcripts

`runAgent.ts` records subagent messages through sidechain transcript helpers such as:

- `recordSidechainTranscript(...)`
- `writeAgentMetadata(...)`
- `setAgentTranscriptSubdir(...)`

### Why

Subagents should be resumable and inspectable without polluting the main transcript as if they were ordinary assistant turns.

This creates a clean architectural concept:

- **main transcript** for main session
- **sidechain transcripts** for delegated agent conversations

---

## 10.2 Lineage metadata

Agent metadata can include:

- `agentType`
- `worktreePath`
- original task `description`
- transcript grouping subdir

`agentContext.ts` additionally tracks:

- `agentId`
- `parentSessionId`
- `invokingRequestId`
- `invocationKind`
- one-shot emission semantics for telemetry linkage

### Why this matters

This system is designed to reconstruct who spawned whom and under which request edge.

That is excellent observability design for nested agents.

---

## 11. AsyncLocalStorage and Concurrent In-Process Agents

## 11.1 What

`src/utils/agentContext.ts` uses `AsyncLocalStorage` to store per-agent execution context.

The file comment explains the reason clearly:

- background agents can run concurrently in one process
- `AppState` is shared and would be overwritten
- AsyncLocalStorage isolates async execution chains

### Why this is one of the most important design choices

Without this, analytics, lineage, and agent identity would corrupt each other when concurrent in-process agents overlap.

This is the right tool for the problem.

---

## 11.2 Supported agent context kinds

The module distinguishes:

- `SubagentContext`
- `TeammateAgentContext`

with shared helpers for:

- retrieving current context
- typing/narrowing
- analytics-safe agent naming
- sparse one-shot invocation-edge emission

### Why this matters

The code acknowledges that “subagent” and “teammate” are not identical roles, even though both are agents.

That conceptual honesty improves the architecture.

---

## 12. Teammates and Swarm Mode

## 12.1 What

`src/tools/shared/spawnMultiAgent.ts` shows a second major multi-agent branch: **teammates**.

Teammates differ from ordinary subagents in that they can be:

- **in-process teammates**
- **tmux / split-pane teammates**
- **separate-window teammates**
- **iTerm2 pane teammates**

depending on backend availability and mode.

### Why this exists

Some delegation patterns are better expressed as persistent workers with independent UI/task identity rather than hidden nested subagent loops.

---

## 12.2 Backend abstraction for teammate spawning

`spawnMultiAgent.ts` detects or selects a backend via swarm backend helpers.

Possible paths include:

- in-process runner
- tmux split-pane
- tmux separate window
- iTerm2 native panes
- fallback to in-process when pane backend is unavailable in auto mode

### Why this is a good design

It keeps the teammate abstraction stable while making the visual/process transport adaptive to the user environment.

---

## 12.3 Mailbox-based initial prompt delivery

For pane-based teammates, the process is:

- spawn teammate process with CLI identity args
- register it in AppState and team file
- send initial instructions via mailbox

### Why mailbox exists

Unlike in-process subagents, out-of-process teammates cannot directly receive the parent’s in-memory prompt context.

The mailbox becomes the coordination transport.

---

## 12.4 In-process teammate startup

When in-process mode is enabled, `spawnMultiAgent.ts`:

- spawns a teammate context
- starts the in-process execution loop
- strips parent message history from `toolUseContext.messages` to avoid pinning main conversation history unnecessarily

### Why this last detail matters

It shows the team is actively preventing silent memory retention bugs in long-running multi-agent sessions.

---

## 13. Swarm Initialization and Reconnection

## 13.1 Session startup initialization

`src/hooks/useSwarmInitialization.ts` initializes swarm behavior when swarm features are enabled.

It handles:

- resumed teammate sessions where `teamName` and `agentName` come from transcript messages
- fresh spawns where team context comes from CLI-provided dynamic teammate context

### Why

Swarm agents are not only runtime tasks; they are resumable session actors.

---

## 13.2 Reconnection model

`src/utils/swarm/reconnection.ts` provides:

- `computeInitialTeamContext()` for startup before first render
- `initializeTeammateContextFromSession(...)` for resumed sessions

The logic reads the team file to restore:

- `teamName`
- `leadAgentId`
- `selfAgentId`
- `selfAgentName`
- leader/non-leader role

### Why this is good architecture

It reconstructs swarm identity from persisted team metadata instead of relying on fragile transient process state.

---

## 14. Permission Delegation in Swarm Workers

## 14.1 What

`src/hooks/toolPermission/handlers/swarmWorkerHandler.ts` defines leader-mediated permission handling for swarm workers.

The flow is:

1. if swarms are disabled or this is not a worker, do nothing special
2. attempt classifier auto-approval for Bash when applicable
3. create a permission request
4. register callbacks before sending to avoid races
5. send request to leader via mailbox
6. show pending visual state while waiting
7. resolve allow/reject/cancel asynchronously

### Why this is architecturally important

This is a real distributed permission protocol inside the product.

It shows that teammates are not just cloned REPL sessions. They participate in a leader-worker authority model.

---

## 14.2 Why leader-mediated permissioning is correct

A worker pane or background teammate should not unilaterally surface or decide high-impact permissions in isolation.

Leader-mediated approval preserves:

- user control
- central auditability
- coherent task supervision

This is a strong safety model for multi-agent operation.

---

## 15. Cleanup and Resource Lifetime

## 15.1 `runAgent(...)` cleanup

The `finally` block in `runAgent.ts` performs substantial lifecycle cleanup:

- agent-specific MCP cleanup
- session hook cleanup
- prompt-cache tracking cleanup
- read-file cache release
- initial message array release
- Perfetto unregister
- transcript-subdir cleanup
- todo entry cleanup
- background shell task kill
- monitor-task cleanup

### Why this matters

This is strong evidence that the multi-agent system is built for long-running sessions where leaked per-agent resources would accumulate quickly.

---

## 15.2 Teammate task cleanup

`spawnMultiAgent.ts` registers out-of-process teammate tasks and ties abort to pane termination for pane backends.

### Why

Even visually separate teammates still participate in the session’s unified task lifecycle.

That keeps the UI and execution model coherent.

---

## 16. Isolation Modes and Their Tradeoffs

## 16.1 Shared-context subagent

### Characteristics

- fastest startup
- shares much of parent runtime context
- good for search/planning or quick delegated work

### Tradeoff

- less isolation
- tighter coupling to parent state and prompt world

---

## 16.2 Fork child

### Characteristics

- inherits full parent conversation prefix
- optimized for prompt-cache sharing
- background-style worker model

### Tradeoff

- semantics are specialized and more constrained
- recursion must be guarded carefully

---

## 16.3 Worktree / remote isolation

Agent definitions can request `isolation` such as `worktree` (and `remote` in certain builds).

The fork helper also builds a worktree notice explaining:

- inherited context may refer to parent paths
- files should be re-read in the child environment
- changes are isolated from parent working copy

### Why this matters

The system supports a spectrum of isolation rather than a one-size-fits-all model.

That is the right design for coding tasks.

---

## 16.4 Swarm teammates

### Characteristics

- separate task identity
- optional pane/process separation
- persistent team coordination

### Tradeoff

- more infrastructure and state management
- mailbox and team-file coordination complexity

---

## 17. Direct Answers to the Required Questions

## 17.1 Why use a multi-agent design here?

Because different software-engineering subtasks benefit from different execution styles:

- narrow research or planning workers
- background verification workers
- isolated worktree modifiers
- persistent teammates in coordinated workflows

A single-threaded assistant can simulate delegation in text, but real delegation improves latency hiding, specialization, and workflow structure.

---

## 17.2 How is context isolation and sharing handled between agents?

It is handled by execution-mode-specific context construction:

- ordinary subagents build a child `ToolUseContext` and may inherit filtered parent messages plus cloned caches
- fork children inherit byte-identical parent prefixes for cache sharing
- worktree-isolated agents receive notices instructing path translation and re-reading
- in-process teammates avoid pinning the parent message history in memory
- pane-based teammates communicate through mailbox/team-file coordination instead of direct in-memory state

So the architecture uses **selective sharing**, not universal sharing or universal isolation.

---

## 17.3 What are the coordination mechanisms among multiple agents?

The inspected code shows several coordination mechanisms:

- parent → child `query()` recursion for ordinary subagents
- sidechain transcripts for persistence and inspection
- AsyncLocalStorage for in-process concurrent identity separation
- mailbox delivery for teammate prompts and permission requests
- team files for persistent swarm membership and metadata
- leader-mediated permission routing for swarm workers
- AppState task registry for unified UI/task lifecycle

---

## 18. Why This Multi-Agent System Looks the Way It Does

### Why not a separate dedicated agent orchestration service?

Because the strongest feature in Claude Code is its shared query/tool/runtime substrate. Reusing it for child agents is more coherent than building a second orchestration plane.

### Why not only use subagents and skip teammates/swarm?

Because UI-visible persistent workers and leader/worker flows solve a different product problem than invisible nested delegation.

### Why not isolate every child in a separate process?

Because many delegated tasks benefit from low-latency in-process reuse, shared caches, and simpler lifecycle management.

### Why not share all context with every agent automatically?

Because prompt cost, correctness, memory retention, and safety all suffer if sharing is uncontrolled. The code instead chooses targeted sharing depending on agent type.

---

## 19. Pros & Cons of the Overall Multi-Agent Architecture

### Strengths

- **Strong reuse of the main query runtime**
- **Rich agent definition contract beyond prompt presets**
- **Multiple delegation modes fit different workflows well**
- **Thoughtful lineage, transcript, and telemetry design**
- **Good safety model for permissions, especially in swarm mode**
- **Attention to prompt-cache and memory-retention economics**
- **Strong cleanup discipline**

### Weaknesses

- **The architecture is conceptually broad**: subagents, forks, teammates, swarm, coordinator all coexist
- **`AgentTool.tsx` is very large and central**
- **Behavior depends heavily on mode, feature flags, and environment**
- **Teammate coordination introduces additional persistence and transport complexity**
- **Some semantics, especially around fork/cache identity, are subtle and expensive to understand**

### Plausible Improvement Directions

1. expose a formal multi-agent mode matrix in code or docs showing the exact differences between subagent, fork, teammate, and coordinator workers
2. extract agent-spawn policy selection out of `AgentTool.tsx` into a smaller planner/router module
3. add a persisted lineage graph/debug view for agent trees and mailbox coordination
4. further separate cache-optimization logic from semantic delegation logic in the fork path

---

## 20. Deep Questions

1. **Will the current family of agent types stabilize, or continue expanding?**
   - Subagent, fork, teammate, and coordinator are already distinct patterns.

2. **Should fork children remain a specialized optimization path, or eventually become a more formal “conversation clone” primitive?**

3. **How much more complexity can `AgentTool.tsx` absorb before the spawn-routing logic needs a first-class state machine or planner?**

4. **Is the current mailbox/team-file coordination sufficient for long-lived swarm workflows, or will the system eventually need a more explicit message bus?**

5. **Can the product surface agent lineage and authority boundaries more transparently to users?**
   - The runtime knows a lot; the UX may not reveal all of it.

---

## 21. Next Deep-Dive Directions

The next strongest follow-ups from the multi-agent system are:

1. **Context Management & Compression**
   - because delegation and sidechain transcripts directly affect context pressure and compaction strategy
2. **Memory & Persistence**
   - because agents already integrate sidechain transcripts, memory prompts, and resumability
3. **Runtime & Execution Environment**
   - because worktree isolation, pane spawning, subprocess lifetime, and remote execution sit beneath the delegation layer

---

## 22. Bottom Line

Claude Code’s multi-agent system is best understood as a **shared-runtime delegation architecture with several execution modes rather than one generic agent framework**.

Its most important architectural ideas are:

- reuse `query()` recursively instead of inventing a second orchestration runtime
- represent agents as rich runtime definitions, not just prompts
- support multiple isolation/coordination modes for different task shapes
- preserve lineage through sidechain transcripts, AsyncLocalStorage, and telemetry metadata
- keep safety centralized through permission modes and leader-mediated worker approvals

That makes the subsystem more complex than a simple “spawn child agent” feature, but it is also what allows Claude Code to support real delegated software-engineering workflows instead of merely simulating them in text.