# Kimi Code CLI: Skills, Rules, Subagents, and the Plan System

## 1. Executive Summary

Kimi Code CLI does not implement agent behavior through one monolithic policy engine. Instead, behavior emerges from several cooperating layers:

- static agent specifications
- system prompt templates
- discovered skills
- slash commands
- dynamic injections such as plan-mode reminders
- tool-level checks and approvals
- subagent delegation through the `Task` tool
- session-persisted dynamic subagents

This means “rules” in Kimi CLI are **distributed across prompt, runtime state, tool constraints, and protocol interactions**.

That is one of the most important architectural characteristics of the project.

## 2. Skills: What They Are

Skills are implemented in `src/kimi_cli/skill/__init__.py`.

A skill is essentially a directory containing `SKILL.md`, plus optional frontmatter.

Important extracted fields include:

- `name`
- `description`
- `type`

Supported `type` values are:

- `standard`
- `flow`

So a skill is not just a prompt snippet. It is a typed reusable behavior artifact.

## 3. Skill Discovery Model

Skill discovery is layered and supports multiple ecosystems.

The resolution order includes:

- built-in bundled skills
- user-level skill directories
- project-level skill directories
- optional CLI override directory

User-level candidates include paths such as:

- `~/.config/agents/skills`
- `~/.agents/skills`
- `~/.kimi/skills`
- `~/.claude/skills`
- `~/.codex/skills`

Project-level candidates include:

- `.agents/skills`
- `.kimi/skills`
- `.claude/skills`
- `.codex/skills`

This is a notable design decision: Kimi CLI explicitly interoperates with surrounding agent conventions instead of forcing an isolated proprietary layout.

## 4. Skill Loading and Indexing

After discovery, skills are normalized and indexed by name.

The skill index is a simple lookup map, not a semantic retrieval engine. It exists to:

- support slash command registration
- make skill lookup deterministic
- allow later tool/prompt integration

This is lightweight and appropriate for the feature.

## 5. Standard Skills vs Flow Skills

## 5.1 Standard skills

A standard skill behaves like a reusable prompt macro.

When the user invokes `/skill:<name>`, the soul:

1. loads `SKILL.md`
2. optionally appends extra user-supplied arguments under `User request:`
3. runs `_turn(...)` with that content as a user message

This is elegant because the skill becomes part of the normal conversation flow instead of requiring a new execution engine.

## 5.2 Flow skills

Flow skills contain Mermaid or D2 diagrams that are parsed into structured flows.

When invoked as `/flow:<name>`, they are executed via `FlowRunner`.

This means Kimi CLI supports both:

- prompt-template reuse
- graph/flow-based guided interaction

That is more sophisticated than a simple slash-command library.

## 6. How Skills Influence Prompting

Skills do not modify the global system prompt permanently.

Instead, they influence the model by injecting a new user turn whose content is the skill text.

This is an important design choice:

- skills are **episodic behaviors**, not global identity changes
- they compose naturally with existing conversation history
- they reuse the same loop, tool, and context system

Architecturally, this keeps skills lightweight and easy to reason about.

## 7. What Counts as “Rules” in Kimi CLI?

There is no single `rules.py` or centralized policy DSL that governs everything.

Instead, rules come from multiple places.

## 7.1 Agent spec rules

Agent specs control:

- which system prompt is used
- which tools are available
- which tools are excluded
- which fixed subagents exist

That is already a form of policy definition.

## 7.2 System prompt rules

The system prompt carries broad behavioral rules, priorities, and workspace-aware guidance.

This is the highest-level normative layer.

## 7.3 Dynamic runtime rules

Dynamic injections, especially in plan mode, add temporary rules such as:

- read-only constraints
- allowed plan file exception
- required turn endings
- prohibited approval phrasing

These are runtime-state-sensitive rules.

## 7.4 Tool-level rules

Tools can reject execution based on:

- plan mode
- approval rejection
- unsupported context
- client capability limitations

This means some rules are enforced after the model chooses an action, not only before.

## 7.5 UI/protocol rules

ACP and other frontends may support or not support certain interaction primitives, such as structured questions. That also shapes the effective behavior of the agent.

## 8. The Plan System: Why It Exists

The plan system is not just a UX extra. It is a serious control mechanism for shifting the agent into a planning-first workflow.

The core idea is:

- planning should be read-only
- planning should produce a visible plan artifact
- the user should explicitly approve the plan before execution proceeds

This is an excellent separation between:

- research/design phase
- execution/change phase

## 9. Entering Plan Mode

The `EnterPlanMode` tool is implemented in `src/kimi_cli/tools/plan/enter.py`.

Its behavior:

- asks the user whether to enter plan mode
- if approved, toggles plan mode on
- returns guidance describing the plan-mode workflow

That workflow explicitly says:

- do not edit code files
- explore with `Glob`, `Grep`, `ReadFile`
- write the plan with `WriteFile`
- call `ExitPlanMode` when ready

So plan mode is deeply integrated with the retrieval model described in earlier docs.

## 10. Exiting Plan Mode

The `ExitPlanMode` tool is implemented in `src/kimi_cli/tools/plan/__init__.py`.

Its behavior:

1. verify that plan mode is active
2. read the current plan file
3. present the plan to the user via a structured `QuestionRequest`
4. branch on the user’s answer

Possible outcomes:

- approve → turn plan mode off and proceed
- reject → remain in plan mode, wait for feedback
- revise → remain in plan mode with user feedback attached

This is one of the most interesting human-in-the-loop workflows in the project.

## 11. Why the Plan System Is Strongly Designed

The plan system has several good properties.

- It creates a concrete plan artifact on disk.
- It constrains agent behavior while planning.
- It requires explicit user review before implementation.
- It uses the same question/request protocol model as the rest of the runtime.
- It persists state across sessions.

This is more robust than simply telling the model “think before acting.”

## 12. Plan Mode Enforcement Is Distributed

Plan mode is enforced through several cooperating layers.

## 12.1 Soul state

`KimiSoul` stores `plan_mode` and persists it in session state.

## 12.2 Dynamic reminders

`PlanModeInjectionProvider` periodically injects strong plan-mode reminders into context.

## 12.3 Tool binding

Specific tools are bound to plan-mode callbacks, including:

- `WriteFile`
- `EnterPlanMode`
- `ExitPlanMode`
- `AskUserQuestion`

## 12.4 Tool-time rejection

Rather than removing tools from the registry entirely, the system lets tools inspect plan state at execution time and reject disallowed behavior.

This is a better design than dynamically hiding tools because:

- tool schemas remain stable
- exceptions such as the plan file can be handled locally
- UI state stays simpler

## 13. `AskUserQuestion` as a Policy-Aware Interaction Tool

`AskUserQuestion` is implemented in `src/kimi_cli/tools/ask_user/__init__.py`.

It is more than a generic survey tool.

Its description dynamically changes in plan mode to remind the model:

- use this tool only for clarification or choosing between approaches
- do not ask about plan approval through this tool
- do not reference “the plan” before `ExitPlanMode`

This is a great example of how Kimi CLI embeds policy directly into tool affordances.

## 14. Subagents: Why They Exist

Subagents allow the runtime to delegate specialized work while preserving the parent agent as coordinator.

The main abstractions live in:

- `Runtime.labor_market`
- `CreateSubagent`
- `Task`

There are two broad classes of subagents:

- fixed subagents from agent specs
- dynamic subagents created during a session

## 15. Fixed Subagents

Fixed subagents are declared in agent specs and loaded during `load_agent(...)`.

These are preconfigured specialist agents with:

- their own system prompt
- their own `Runtime` clone
- shared or isolated subagent runtime properties depending on copy strategy

They are registered into `LaborMarket` before tool loading completes.

This is important because tools like `Task` rely on subagent availability during initialization.

## 16. Dynamic Subagents

Dynamic subagents are created by the `CreateSubagent` tool in `tools/multiagent/create.py`.

The tool:

- takes a new name
- takes a new system prompt
- creates an `Agent` sharing the existing toolset
- uses `runtime.copy_for_dynamic_subagent()`
- registers the subagent in the labor market
- persists the definition into session state

This is powerful because the agent can define new specialist personas at runtime.

## 17. The `Task` Tool: How Delegation Works

`Task` is implemented in `tools/multiagent/task.py`.

Its parameters require:

- a short task description
- the subagent name
- a full prompt with all background context

That last requirement is crucial and explicitly stated: the subagent does **not** automatically inherit the parent’s full context.

This is a deliberate design choice.

## 18. Why the Subagent Prompt Must Be Detailed

The `Task` tool description explicitly tells the caller to provide all necessary background because the subagent cannot see the caller’s context automatically.

This avoids a common agent anti-pattern where subagents implicitly inherit ambiguous or oversized context.

Instead, the parent agent must perform **explicit context handoff**.

This is good architecture because it forces clarity.

## 19. How a Subagent Actually Runs

When `Task` executes:

1. it locates the target subagent from `LaborMarket`
2. creates a dedicated context file for the subagent
3. creates a new `Context`
4. creates a new `KimiSoul` for that subagent
5. runs the subagent via `run_soul(...)`
6. forwards subagent events to the parent wire wrapped as `SubagentEvent`
7. returns the final assistant response as tool output

This means subagents are not fake internal functions. They are real nested souls with their own loop and context.

## 20. How Subagent Events Are Surfaced

Subagent events are wrapped as `SubagentEvent(task_tool_call_id=..., event=...)` and pushed to the parent wire.

Important exception:

- approval requests, approval responses, external tool-call requests, and question requests remain at the root wire level

This is a subtle but excellent design choice. It preserves a coherent user interaction surface even when nested agents are active.

## 21. Runtime Copy Semantics for Subagents

`Runtime` provides different cloning strategies.

## 21.1 Fixed subagents

Fixed subagents get:

- their own `DenwaRenji`
- shared approval
- their own `LaborMarket`
- shared skills and additional dirs

## 21.2 Dynamic subagents

Dynamic subagents get:

- their own `DenwaRenji`
- shared approval
- shared `LaborMarket`
- shared skills and additional dirs

This distinction matters. Dynamic subagents are meant to participate in the same evolving subagent ecosystem, while fixed subagents are more isolated specialists.

## 22. Strength of the Subagent Design

The subagent design has several strengths:

- explicit context handoff
- isolated context files
- real nested agent loops
- event forwarding to parent UI
- session-persisted dynamic specialists
- reuse of the same runtime abstractions as the main agent

This is significantly better than pretending subagents are just text roleplay.

## 23. Where the “Rules” Really Live in Practice

After studying the code, the most accurate answer is:

Rules live in five places.

- agent spec composition
- prompt text
- dynamic prompt injections
- tool execution guards
- interactive protocol workflows

That means the system’s behavior is not reducible to one policy file. It is a **distributed policy architecture**.

## 24. Core Trade-off of This Design

The benefit of distributed rules is flexibility.

The cost is that behavior is harder to audit globally.

To understand why the agent behaved a certain way, one must often inspect:

- agent spec
- system prompt
- current mode
- dynamic injections
- tool availability
- frontend capability
- session state

This is powerful but nontrivial to debug.

## 25. Most Important Insight

Skills, plan mode, and subagents are not isolated features. They are all examples of the same deeper architectural idea:

- the runtime can **reshape agent behavior without rewriting the main loop**

It does this by changing one or more of:

- prompt content
- tool affordances
- session state
- question/approval workflows
- delegated agent topology

This is the real extensibility model of Kimi CLI.

## 26. Final Summary

Kimi Code CLI’s behavior-shaping system is built from composable layers:

- **skills** provide reusable prompt or flow behaviors
- **rules** are distributed across prompts, runtime state, and tools
- **plan mode** creates a structured read-only planning workflow with explicit user approval
- **subagents** enable explicit delegated execution with isolated context and forwarded events

Together, these mechanisms make the project more than a single-agent shell assistant. They turn it into a configurable, multi-behavior agent runtime.
