# Command Template Expansion / Argument Binding

---

# 1. Module Purpose

This document explains how OpenCode expands slash-command templates into concrete prompt text, focusing on:

- command lookup in `command/index.ts`
- argument tokenization and placeholder binding in `session/prompt.ts`
- `$1`, `$2`, and `$ARGUMENTS` handling
- embedded shell substitution via ``!`...` `` blocks
- model/agent selection for command execution

The key questions are:

- How does OpenCode turn a slash command plus raw argument string into a final prompt template?
- What are the exact semantics of numbered placeholders and `$ARGUMENTS`?
- Why does the final numbered placeholder absorb extra trailing arguments?
- How are embedded shell substitutions executed and injected into the template?
- How do command sources from config, MCP prompts, and skills converge into one common template-expansion path?

Primary source files:

- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/command/index.ts`

This layer is OpenCode’s **slash-command template expansion and binding pipeline**.

---

# 2. Why this layer matters

OpenCode commands are not just static named actions.

They are prompt templates that can come from multiple sources and be parameterized by runtime arguments.

That means the command system is really a prompt-programming layer on top of the session runtime.

Understanding template expansion is essential for understanding:

- slash commands
- MCP-backed prompts exposed as commands
- skills invoked as commands
- subtask-oriented command execution

---

# 3. Command definitions are unified under `Command.Info`

`command/index.ts` defines a common shape with fields like:

- `name`
- `description`
- `agent`
- `model`
- `source`
- `template`
- `subtask`
- `hints`

This is important.

Commands from different origins are normalized into one shared metadata shape before execution.

That is what makes the later expansion pipeline source-agnostic.

---

# 4. Command sources are intentionally heterogeneous

The command registry assembles commands from:

- built-in default commands like `init` and `review`
- config-defined commands
- MCP prompts projected as commands
- skills exposed as commands

This is a strong design choice.

OpenCode treats many prompt-producing capability sources as one common command abstraction.

---

# 5. Why commands from different origins can share one expansion path

All command sources eventually provide:

- a template string or async template getter

Once that template exists, the rest of the pipeline can process it the same way.

That means the source diversity is normalized early, which greatly simplifies execution semantics.

---

# 6. `Command.hints(...)` reveals the template language contract

The command registry extracts hints from templates by looking for:

- numbered placeholders like `$1`
- `$ARGUMENTS`

This is a useful signal.

The command template language is intentionally small and explicit.

It is based primarily on:

- positional substitution
- whole-argument substitution

That keeps it understandable.

---

# 7. MCP prompts are adapted into the same placeholder language

When MCP prompts are exposed as commands, the registry maps their named arguments into:

- `$1`, `$2`, and so on

Then `MCP.getPrompt(...)` is called with those positional placeholders substituted back into named prompt arguments.

This is a very important compatibility layer.

It means the slash-command system presents a single positional argument model even when the original MCP prompt definition used named arguments.

---

# 8. Why MCP prompt adaptation matters

Without this adapter, MCP-backed prompts would have a different invocation model from native commands.

By translating MCP arguments into numbered placeholders, OpenCode gives users and later execution code one consistent command argument language.

That is good product design.

---

# 9. The `command(...)` function begins by resolving command metadata

The execution path starts with:

- `const command = await Command.get(input.command)`
- agent selection from command, input override, or default agent

So command expansion always begins from a resolved command definition, not directly from arbitrary template text.

That preserves source metadata like model overrides and `subtask` policy.

---

# 10. Raw arguments are tokenized with a command-aware regex

The function tokenizes `input.arguments` using a regex that preserves:

- `[Image N]` as a single token
- double-quoted strings
- single-quoted strings
- other non-space sequences

Then it strips surrounding quotes from each token.

This is more careful than a simple whitespace split.

It is designed for real prompt-command usage where quoted phrases matter.

---

# 11. Why `[Image N]` is treated specially

The tokenizer explicitly preserves tokens like:

- `[Image 1]`

as single units.

This suggests command argument binding is expected to interact with image-reference tokens in the user-facing workflow.

That is a subtle but important domain-specific adaptation.

---

# 12. The template itself may be async

`const templateCommand = await command.template`

This is important because some command sources, especially MCP-backed prompts, are resolved lazily.

The expansion path therefore handles both static and dynamically fetched templates uniformly.

---

# 13. Numbered placeholders are positional, but the last one is greedy

The code finds all numbered placeholders and tracks the highest one as `last`.

Then during replacement:

- each placeholder maps to the corresponding argument
- but the final numbered placeholder consumes the rest of the arguments joined with spaces

This is one of the most important semantics in the command template language.

---

# 14. Why the last placeholder swallows extra arguments

The comment says this helps prompts read naturally.

That is exactly right.

If a command wants a long trailing free-form argument, the final placeholder can act like a rest-parameter without introducing a second special syntax.

This is a very elegant small-language feature.

---

# 15. `$ARGUMENTS` preserves the original raw argument string

After numbered substitution, the code also replaces:

- `$ARGUMENTS`

with the original unparsed `input.arguments` string.

This is important because positional tokens and the raw argument string serve different use cases.

Sometimes a prompt wants structured positional binding.

Sometimes it wants the exact original tail string.

OpenCode supports both.

---

# 16. Why both positional and raw-string substitution are useful

Positional placeholders are great for commands with clear argument structure.

`$ARGUMENTS` is better when the command wants to preserve the full raw phrasing exactly.

Supporting both keeps the template language small but expressive.

---

# 17. Commands with no placeholders still receive arguments by default

If the template contains neither numbered placeholders nor `$ARGUMENTS`, but arguments were provided, the code appends:

- two newlines
- the raw argument string

This is a very user-friendly fallback.

It means command authors do not have to explicitly wire arguments for simple “append the user’s text to this prompt” behavior.

---

# 18. Why this fallback is good product behavior

Without it, users could pass arguments to a command and silently have them ignored.

Appending arguments by default when the template does not explicitly consume them is a good least-surprise rule.

---

# 19. Embedded shell substitutions are extracted through `ConfigMarkdown.shell(...)`

After argument expansion, the code scans the template for shell blocks and executes them.

The regex anchor visible here is:

- ``!`...` ``

Each matched command is run with Bun shell execution and its text output is spliced back into the template.

This is a powerful template feature.

---

# 20. Why shell substitution happens after argument binding

This ordering matters.

By the time shell substitutions run, the template already contains any resolved placeholders.

So shell commands can depend on user-provided command arguments indirectly through the expanded template text.

That is the sensible ordering for a templating pipeline.

---

# 21. Shell substitutions are executed in parallel

The code uses `Promise.all(...)` over all shell matches.

That is a nice optimization.

Independent embedded shell snippets do not need to be serialized.

So command-template expansion is intentionally efficient where it safely can be.

---

# 22. Shell substitution failures are converted into inline error text

If executing an embedded shell command fails, the result inserted into the template becomes:

- `Error executing command: ...`

This is important.

Template expansion does not hard-fail immediately on shell substitution errors.

Instead it surfaces the failure inline in the resulting prompt text.

That keeps the command path robust while still exposing the problem to the model or user-facing output.

---

# 23. Final template text is trimmed before downstream use

After all substitutions, the code does:

- `template = template.trim()`

This small normalization step prevents leading/trailing whitespace artifacts from placeholder replacement or shell substitution.

It is minor but sensible cleanup.

---

# 24. Command execution also resolves a task model independently of the eventual user model

The command path computes `taskModel` from:

- command model override
- command agent’s model
- input model
- or last session model

Then it verifies the model exists through `Provider.getModel(...)`.

This is important because command expansion is not only about text substitution.

It also resolves the execution target associated with the command semantics.

---

# 25. Why model validation happens before prompt submission

If a command specifies a bad model, the runtime publishes a session error and throws before trying to continue.

That is the correct fail-fast behavior.

It prevents opaque downstream failures from a template that looked valid textually but pointed to an invalid execution configuration.

---

# 26. Agent lookup is also validated explicitly

After computing `agentName`, the code loads the agent and, if missing:

- publishes a session error
- includes available agent names as a hint
- throws

This is another example of configuration validation embedded in the command path.

A command is not considered ready just because its template expanded successfully.

Its execution context must also exist.

---

# 27. Template text is converted into prompt parts before prompting

Once the final template is ready, the code calls:

- `resolvePromptParts(template)`

This is a major transition point.

The expanded template stops being plain text and becomes structured prompt parts, which may include:

- text
- files
- agent mentions

So command template expansion feeds directly into the richer prompt-ingress pipeline.

---

# 28. Commands can either become subtasks or ordinary prompt parts

If the selected agent is a subagent and command policy allows it, the command becomes a single `subtask` part with fields like:

- target agent
- description
- command name
- resolved model
- prompt text

Otherwise it becomes:

- resolved template parts
- plus any explicitly supplied input parts

This is a very important branching behavior.

A slash command is not always just “generate prompt text and send it.”

It can instead become a formal subtask invocation request.

---

# 29. Why subtask conversion changes the eventual user agent/model

When `isSubtask` is true, the code uses:

- the caller/default agent as the user-side agent
- the input/last model as the user-side model

rather than the command’s target subagent model as the actual user message model.

This is subtle but important.

The subtask payload carries the target subagent/model, while the enclosing user turn remains anchored to the parent session’s current execution context.

That preserves the distinction between:

- current conversation agent
- requested spawned subtask agent

---

# 30. Command execution is observable through plugin hooks and bus events

Before prompting, the runtime triggers:

- `command.execute.before`

After prompting, it publishes:

- `Command.Event.Executed`

So command invocation is not a hidden prompt transformation.

It is a first-class observable runtime event.

---

# 31. A representative command expansion lifecycle

A typical lifecycle looks like this:

## 31.1 Command metadata is resolved

- source may be config, MCP, skill, or built-in command

## 31.2 Raw arguments are tokenized

- quotes preserved meaningfully
- `[Image N]` preserved as single token

## 31.3 Template placeholders are expanded

- `$1`, `$2`, ...
- final placeholder swallows remaining args
- `$ARGUMENTS` gets raw string
- fallback appends raw arguments when no placeholders exist

## 31.4 Embedded shell substitutions run

- ``!`...` `` blocks evaluated and replaced

## 31.5 Agent/model context is validated

- model and agent must exist

## 31.6 Final template becomes prompt parts or a subtask part

- then flows into `prompt(...)`

This is the actual slash-command expansion pipeline.

---

# 32. Why this module matters architecturally

This layer shows how OpenCode turns commands into a lightweight prompt DSL rather than a hardcoded command handler table.

Commands from many sources converge into:

- one template language
- one argument-binding model
- one shell-substitution mechanism
- one downstream prompt-ingress path

That is a very flexible architecture for user-extensible command behavior.

---

# 33. Key design principles behind this module

## 33.1 Different command sources should converge into one small, understandable template language

So built-in commands, config commands, MCP prompts, and skills all become template-bearing `Command.Info` entries.

## 33.2 Argument binding should be simple but expressive

So positional placeholders, greedy final placeholder behavior, `$ARGUMENTS`, and fallback append behavior cover the main use cases without a large DSL.

## 33.3 Template expansion should happen before prompt-part materialization

So shell substitutions and placeholder replacement produce the final text that `resolvePromptParts(...)` will interpret structurally.

## 33.4 Command execution context must be validated, not assumed

So model and agent existence checks happen before the command enters the prompt pipeline.

---

# 34. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/command/index.ts`
2. `packages/opencode/src/session/prompt.ts`
3. `Command.hints()`
4. `SessionPrompt.command()`
5. `resolvePromptParts()`

Focus on these functions and concepts:

- `Command.Info`
- `Command.hints()`
- MCP prompt adaptation into numbered placeholders
- `argsRegex`
- `placeholderRegex`
- `$ARGUMENTS`
- shell substitution via ``!`...` ``
- subtask conversion branch
- `command.execute.before`
- `Command.Event.Executed`

---

# 35. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Should the command template language eventually support named placeholders directly instead of normalizing everything to positional ones?
- **Question 2**: How should shell substitution security and determinism be managed as command templates become more powerful?
- **Question 3**: Should command expansion failures from embedded shell snippets sometimes be terminal rather than merely inserted inline as text?
- **Question 4**: How should argument tokenization evolve for more complex multimodal or structured argument formats?
- **Question 5**: Are there edge cases where greedy last-placeholder behavior produces surprising expansions for some commands?
- **Question 6**: Should command templates support richer typed parts directly rather than always flowing through plain text before `resolvePromptParts(...)`?
- **Question 7**: How should MCP prompt arguments with optional/default values be represented in the current positional placeholder model?
- **Question 8**: What tests best guarantee that command templates from all supported sources expand identically through the shared pipeline?

---

# 36. Summary

The `command_template_expansion_and_argument_binding` layer is how OpenCode turns a slash command and raw argument string into a concrete prompt-ready execution request:

- commands from built-in definitions, config, MCP prompts, and skills all converge into a shared template-bearing command model
- positional placeholders, `$ARGUMENTS`, fallback append behavior, and embedded shell substitution shape the final template text
- model and agent context are validated before execution
- the final expanded template becomes structured prompt parts or a formal subtask request that flows into the normal session prompt pipeline

So this module is the prompt-DSL layer that makes OpenCode’s command system flexible, source-agnostic, and tightly integrated with the broader session runtime.
