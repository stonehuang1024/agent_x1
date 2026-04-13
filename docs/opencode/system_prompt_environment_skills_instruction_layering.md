# System Prompt / Environment Skills and Instruction Layering

---

# 1. Module Purpose

This document explains how OpenCode builds the final system prompt for a session turn, focusing on the layering of:

- environment context
- provider/model-specific prompt templates
- skill guidance
- instruction-file content
- structured-output enforcement

The key questions are:

- How is the final system prompt assembled in the session loop?
- What information comes from `SystemPrompt.environment(...)` versus `SystemPrompt.skills(...)`?
- How does `InstructionPrompt.system()` discover and load project, global, and URL-based instruction sources?
- Why are instruction and skill layers kept separate from the base environment layer?
- How does structured-output mode alter the final system prompt contract?

Primary source files:

- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/session/system.ts`
- `packages/opencode/src/session/instruction.ts`

This layer is OpenCode’s **final system prompt composition and instruction layering surface**.

---

# 2. Why this layer matters

The assistant does not run on raw user messages alone.

Every turn is shaped by a composed system prompt that blends:

- runtime environment facts
- provider/model guidance
- skill discoverability guidance
- project/global instruction documents
- special mode-specific constraints

This is where OpenCode turns static configuration and dynamic runtime state into the model’s top-level behavioral frame.

---

# 3. The final assembly happens in `session/prompt.ts`

The loop builds:

- `const skills = await SystemPrompt.skills(agent)`
- `const system = [ ...(await SystemPrompt.environment(model)), ...(skills ? [skills] : []), ...(await InstructionPrompt.system()) ]`

and then optionally appends:

- `STRUCTURED_OUTPUT_SYSTEM_PROMPT`

This is the authoritative composition site.

It shows that system prompt construction is a layered concatenation of multiple independently computed prompt sources.

---

# 4. The assembly order is meaningful

The order is:

- environment first
- skills second
- instruction files third
- structured-output enforcement last when applicable

This is not arbitrary.

It suggests a precedence model where:

- baseline runtime context comes first
- optional capability guidance comes next
- project/global instruction overlays come after that
- special mode constraints can be appended last for maximum salience

That is a sensible composition strategy.

---

# 5. `SystemPrompt.environment(...)` provides runtime facts, not user instructions

The environment layer includes text like:

- current model identity
- working directory
- workspace root
- whether the directory is a git repo
- platform
- today’s date
- an optional directories block

This is important.

The environment layer gives the model situational awareness about where it is running.

It is not a policy layer in the same sense as instruction documents.

---

# 6. Why model identity is included in the environment prompt

The environment prompt explicitly says:

- the model name
- the exact provider/model ID

This is a subtle but useful detail.

OpenCode wants the model to be aware of its exact runtime identity, presumably because different providers or model families may behave differently and that awareness can guide compliance with provider-specific expectations.

---

# 7. Environment prompt content is dynamic per execution context

The environment prompt uses current runtime values such as:

- `Instance.directory`
- `Instance.worktree`
- `project.vcs`
- `process.platform`
- current date

So this layer is not static repo documentation.

It is live execution context.

That makes it especially useful for agentic coding workflows where path and workspace awareness matter.

---

# 8. `SystemPrompt.provider(...)` exists, but the current loop path shown here uses `environment(...)`

The system module also exposes provider-specific template selection through:

- `SystemPrompt.provider(model)`

with templates for GPT, Gemini, Claude, Trinity, and a default path.

That is important architectural context even though the loop assembly site shown here directly uses `environment(...)`, `skills(...)`, and `InstructionPrompt.system()`.

It means the system-prompt module distinguishes:

- provider-template knowledge
- environment framing

as separate concerns.

---

# 9. `SystemPrompt.skills(...)` is conditional on permission policy

The skills layer first checks whether the `skill` permission is disabled for the current agent.

If so, it returns nothing.

This is a very important design choice.

Skill guidance is only surfaced when the agent is actually allowed to use the skill mechanism.

That avoids advertising a capability the current policy forbids.

---

# 10. Why permission-aware prompt composition is important

A model should not be told to use capabilities it cannot actually invoke.

By gating skill guidance on permission policy, OpenCode keeps the system prompt aligned with executable reality.

That reduces contradictory prompts and failure-inducing instructions.

---

# 11. The skills layer is descriptive capability guidance, not the skill implementation itself

When enabled, `SystemPrompt.skills(...)` returns text explaining:

- that skills provide specialized instructions and workflows
- that the skill tool should be used when a task matches a skill description
- a verbose formatted list of available skills

This is not the tool schema or tool runtime.

It is the model-facing explanation of the capability landscape.

That makes sense as a system-prompt responsibility.

---

# 12. Why the skills list is intentionally verbose

The source comment says agents ingest skill information better when the prompt presents a more verbose version here and a less verbose version in the tool description.

This is a valuable product insight encoded in code.

OpenCode is tuning not only what information is available, but where and in what form it is most effectively consumed by the model.

---

# 13. `InstructionPrompt.system()` loads instruction documents from several sources

The instruction layer gathers content from:

- project-level config files like `AGENTS.md`, `CLAUDE.md`, `CONTEXT.md`
- global config files
- optional `~/.claude/CLAUDE.md`
- extra configured instruction paths
- configured instruction URLs fetched over HTTP(S)

This is a substantial instruction-discovery system.

It means system prompt construction is partly filesystem- and config-driven, not only hardcoded in source.

---

# 14. Why instruction loading is split into `systemPaths()` and `system()`

`systemPaths()` discovers the file paths.

`system()` reads file contents and fetches URL contents.

This is a clean separation between:

- instruction source discovery
- instruction content materialization

That makes the module easier to reason about and extend.

---

# 15. Project instruction discovery prefers the nearest recognized file family

When project config is enabled, `systemPaths()` searches upward for recognized filenames:

- `AGENTS.md`
- `CLAUDE.md`
- `CONTEXT.md`

and stops after the first file family with matches.

This is important because it implies a priority scheme rather than indiscriminately loading every possible instruction file everywhere.

That helps limit prompt bloat and conflicting policy layers.

---

# 16. Global instruction discovery is also prioritized

After project-level discovery, the code checks global files and adds the first existing one.

This suggests OpenCode treats global instruction sources as another ordered layer rather than an unbounded instruction soup.

Again, this is good prompt hygiene.

---

# 17. Configured instruction paths support absolute, relative, and home-relative forms

Configured instructions can be:

- absolute paths
- relative globs resolved upward
- `~/`-prefixed home-relative paths
- URLs

This is a flexible user/operator-facing configuration surface.

It allows instruction layering to be customized without changing code.

---

# 18. URL-based instructions are fetched at system-prompt build time

For HTTP(S) instruction entries, `InstructionPrompt.system()`:

- fetches them with a 5-second timeout
- ignores failed fetches
- prefixes content with `Instructions from: <url>`

This is a noteworthy design choice.

Remote instruction documents can participate directly in the final system prompt.

That makes the instruction layer dynamic across environments.

---

# 19. Why each instruction chunk is prefixed with its source

Both file- and URL-based instruction content are prefixed like:

- `Instructions from: <path or url>`

This is excellent prompt hygiene.

It preserves provenance inside the assembled prompt so the model can see where a given instruction came from.

That can help disambiguate layered instructions and also improves debugging.

---

# 20. `InstructionPrompt.loaded(...)` tracks already-loaded instruction files via read-tool metadata

The instruction module can inspect message history and collect files that were already loaded as instruction-relevant content through completed read tool calls.

This is a subtle but important feature.

It helps prevent redundant instruction injection later when resolving additional file-local instruction context.

So the instruction layer is aware of conversation history, not just config files.

---

# 21. `InstructionPrompt.resolve(...)` adds local instruction files relative to a target file path

Besides global system assembly, the instruction module can also walk upward from a target file path and discover nearby instruction files, excluding:

- system-level ones already known
- files already loaded
- files already claimed for the current message

This is a very important extension mechanism.

It means instruction layering can become file-local and context-sensitive, not only globally session-scoped.

---

# 22. Claim tracking prevents duplicate local instruction injection per message

The module keeps a `claims` map keyed by message ID.

When a local instruction file is resolved for a message, it is claimed so the same file is not repeatedly injected for that same message.

This is careful prompt deduplication logic.

It prevents runaway instruction duplication during a single turn.

---

# 23. Why instruction deduplication matters

Instruction layering can easily create prompt bloat and inconsistent repetition.

The `claims` map plus loaded-file tracking show that OpenCode is explicitly managing that risk.

That is a sign of mature prompt orchestration design.

---

# 24. Structured-output mode appends a hard final constraint

If the latest user format is JSON-schema mode, the loop appends:

- `STRUCTURED_OUTPUT_SYSTEM_PROMPT`

This prompt says the assistant must use the `StructuredOutput` tool and must not answer with plain text.

This is a distinct final-layer constraint.

It does not replace the rest of the system prompt. It sharpens it for a specific output contract.

---

# 25. Why structured-output enforcement belongs at the end

Appending the structured-output instruction last increases its salience and reduces the chance that it gets diluted by more general environment or instruction text.

That is a sensible final override strategy for a strict output mode.

---

# 26. The system layer is therefore a composition of different kinds of guidance

It helps to distinguish the layers by purpose:

- environment = runtime facts
- skills = capability guidance
- instruction files = policy and workflow guidance from config/documents
- structured output = current-turn output contract override

This decomposition is very useful when reasoning about behavior changes.

---

# 27. A representative system-prompt lifecycle

A typical lifecycle looks like this:

## 27.1 Session loop determines current agent and model

- needed to compute environment and skills

## 27.2 Environment prompt is built

- model identity
- directory/workspace/platform/date facts

## 27.3 Skill guidance is added if allowed

- available skill descriptions for the current agent

## 27.4 Global/project/configured instructions are loaded

- local files and optional URLs become instruction chunks

## 27.5 Turn-specific mode constraints may be appended

- structured-output enforcement when JSON-schema mode is active

## 27.6 Final `system` array is passed to `processor.process(...)`

This is the actual system-prompt assembly pipeline.

---

# 28. Why this module matters architecturally

This layer shows that OpenCode does not treat the system prompt as a single static blob.

Instead, it is a composed control surface whose parts come from:

- live runtime state
- current agent permission/capability state
- repository or user instruction documents
- special per-turn output modes

That is a strong architecture for an agent runtime that has to adapt across projects and workflows.

---

# 29. Key design principles behind this module

## 29.1 Different kinds of top-level guidance should be layered by responsibility, not mixed blindly into one undifferentiated prompt blob

So environment, skills, instruction documents, and structured-output constraints are computed separately and then composed.

## 29.2 System prompt content should reflect executable reality

So skills are gated by permission policy and environment facts reflect the current runtime context.

## 29.3 Instruction systems need provenance and deduplication to remain trustworthy

So instruction chunks are source-labeled and tracked through loaded/claimed mechanisms.

## 29.4 Strict output contracts should be appended as high-salience final constraints when needed

So structured-output mode adds a dedicated final system prompt instruction.

---

# 30. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/session/prompt.ts`
2. `packages/opencode/src/session/system.ts`
3. `packages/opencode/src/session/instruction.ts`

Focus on these functions and concepts:

- `SystemPrompt.environment()`
- `SystemPrompt.skills()`
- `InstructionPrompt.systemPaths()`
- `InstructionPrompt.system()`
- `InstructionPrompt.loaded()`
- `InstructionPrompt.resolve()`
- `InstructionPrompt.claims`
- `STRUCTURED_OUTPUT_SYSTEM_PROMPT`
- final `system` assembly in `prompt.ts`

---

# 31. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: How and where is `SystemPrompt.provider(...)` used elsewhere, and should that provider-template layer be integrated more explicitly into the current loop assembly path?
- **Question 2**: How should conflicting instructions from project files, global files, and remote URLs be prioritized or resolved more formally?
- **Question 3**: Should instruction URLs be cached to reduce per-turn fetch cost and nondeterminism?
- **Question 4**: How should file-local instruction resolution via `InstructionPrompt.resolve(...)` interact with compaction and session replay?
- **Question 5**: Are there situations where the environment block should include a directory tree again rather than the currently disabled behavior?
- **Question 6**: How large can the combined system prompt grow in complex projects with many instruction layers, and what controls should exist?
- **Question 7**: Should skill guidance become more selective so only the most relevant subset of skills is surfaced per turn?
- **Question 8**: What tests best ensure instruction deduplication remains correct when multiple read-tool and local-instruction paths interact?

---

# 32. Summary

The `system_prompt_environment_skills_instruction_layering` layer is where OpenCode assembles the final top-level prompt frame for a turn:

- `SystemPrompt.environment()` contributes live runtime and model context
- `SystemPrompt.skills()` contributes capability guidance when skill use is allowed
- `InstructionPrompt.system()` loads source-labeled project, global, configured, and remote instruction documents
- structured-output mode appends a final hard constraint requiring tool-based JSON-schema output

So this module is the prompt-composition surface that turns runtime context, capability guidance, and instruction documents into the system-level behavioral frame for the model.
