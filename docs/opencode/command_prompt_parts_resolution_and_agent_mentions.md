# Command Prompt Parts Resolution / File References and Agent Mentions

---

# 1. Module Purpose

This document explains how expanded command text is converted into structured prompt parts before entering the main prompt pipeline, focusing on `resolvePromptParts(...)` in `session/prompt.ts`.

The key questions are:

- How does OpenCode parse file references out of command-expanded text?
- Why does prompt-part resolution always keep the original template text as a text part?
- How are `@...` references interpreted as files, directories, or agent mentions?
- What happens when a referenced path does not exist?
- How does this layer connect the command template system to the richer prompt-ingress model used by `prompt(...)` and `createUserMessage()`?

Primary source files:

- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/config/markdown.ts`

This layer is OpenCode’s **expanded-command text to structured prompt-parts resolution layer**.

---

# 2. Why this layer matters

Command template expansion produces a final string.

But the session runtime does not want to treat important embedded references inside that string as mere plain text.

It wants to recognize things like:

- file references
- directory references
- agent mentions

and convert them into structured parts.

`resolvePromptParts(...)` is the bridge between:

- text-oriented command templates
- structured prompt-ingress state

That makes it a key integration point.

---

# 3. The command path explicitly flows through `resolvePromptParts(...)`

After a command template is expanded and validated, `command(...)` does:

- `const templateParts = await resolvePromptParts(template)`

This is important.

Prompt-part resolution is not an optional embellishment.

It is part of the normal command execution pipeline.

So command templates are expected to contain structural references that deserve richer treatment than raw text.

---

# 4. Resolution always starts with a text part containing the full template

`resolvePromptParts(template)` initializes:

- one `text` part with the entire template string

before doing anything else.

This is a very important design choice.

Even if the template also contains file references or agent mentions, the original expanded text remains part of the prompt.

That preserves the human-readable narrative of the command.

---

# 5. Why the full text is kept instead of replaced by structured parts only

A command template may say more than just “include this file” or “use this agent.”

It may contain explanatory text, workflow framing, or instructions surrounding those references.

Keeping the entire template as a text part ensures none of that context is lost when structured references are extracted.

That is exactly the right behavior.

---

# 6. Reference extraction is driven by `ConfigMarkdown.files(...)`

The resolver calls:

- `ConfigMarkdown.files(template)`

In `config/markdown.ts`, this uses a regex that matches `@...` references while avoiding some false positives.

This is the parser that turns inline text references into candidate structural items.

---

# 7. Why the `@...` syntax is useful here

Using an inline `@path`-style syntax lets commands stay author-friendly and text-first while still carrying structured semantics.

Template authors can write natural-looking instructions and include references that the runtime can later promote into richer parts.

That is a good ergonomic compromise.

---

# 8. Resolution deduplicates repeated references

The function keeps a `seen` set of names.

If the same `@...` reference appears multiple times in the template, it is only materialized once as a structured part.

This is important prompt hygiene.

Repeated textual mentions still remain in the full text part, but repeated structural attachments are not duplicated unnecessarily.

---

# 9. Why deduplication matters

Without deduplication, templates with repeated references could create redundant file or agent parts, inflating prompt state and creating confusing duplicated context.

The current behavior preserves the narrative text while keeping the structural layer clean.

---

# 10. Relative references resolve against the workspace root

For each `@...` match, the resolver computes a path using:

- home expansion for `~/...`
- otherwise `path.resolve(Instance.worktree, name)`

This is an important contract.

Command file references are interpreted relative to the workspace root, not necessarily the current working directory string in the text itself.

That gives commands a stable project-oriented reference frame.

---

# 11. Why workspace-root resolution is a sensible default

Commands often want to refer to project files in a way that is stable across subdirectories.

Resolving relative references from `Instance.worktree` makes command templates more portable and less sensitive to the immediate working directory.

That is a good default for a coding-agent workflow.

---

# 12. Existence check determines whether a reference is a path or a possible agent name

The resolver stats the computed path.

If the path does not exist, it then tries:

- `Agent.get(name)`

If an agent exists, it adds an `agent` part.

This is one of the most interesting behaviors in the function.

The same `@name` syntax can resolve either to:

- a filesystem reference
- an agent mention

depending on what exists.

---

# 13. Why agent fallback is useful

This lets command templates use a single lightweight reference style for both:

- project files
- named agents

That reduces syntax surface area.

More importantly, it means template authors do not need a separate bespoke agent-mention syntax when `@agentName` is expressive enough.

---

# 14. Why missing non-agent references are silently ignored structurally

If the path does not exist and no agent with that name exists, the resolver simply does not add a structured part.

This is acceptable because the original full template text still remains in the text part.

So unresolved references are not erased from the prompt. They just are not promoted into richer structured parts.

That is a graceful failure mode.

---

# 15. Directory references become `file` parts with directory MIME

If the resolved path exists and `stats.isDirectory()` is true, the resolver adds:

- `type: "file"`
- `url: file://...`
- `filename: name`
- `mime: "application/x-directory"`

This is important because directories are treated as first-class prompt parts, not as awkward textual approximations.

That allows later ingestion to branch on directory semantics explicitly.

---

# 16. Regular files become text/plain file parts

If the resolved path exists and is not a directory, the resolver adds:

- `type: "file"`
- `url: file://...`
- `filename: name`
- `mime: "text/plain"`

This indicates that command prompt-part resolution is intentionally optimistic about normal file references being readable text inputs.

That aligns with the later `createUserMessage()` behavior, which eagerly reads text files into synthetic context.

---

# 17. Why prompt-part resolution stops at lightweight descriptors

`resolvePromptParts(...)` does **not** read file contents itself.

It only creates lightweight `file` or `agent` descriptors.

That is the right separation of concerns.

This function is about structural detection.

Later ingress/materialization code is responsible for actually dereferencing files or agent mentions into richer conversation state.

---

# 18. This function is the handoff point into the richer ingress pipeline

Once `resolvePromptParts(...)` returns, `command(...)` either:

- wraps the result into a `subtask` part
- or combines `templateParts` with `input.parts`

Then it calls `prompt(...)`, which eventually calls `createUserMessage()`.

This means the resolver is not the end of interpretation.

It is the bridge that turns a command-expanded string into the structured inputs that the main prompt-ingress pipeline already knows how to materialize.

---

# 19. Why this separation is elegant

The command system does not need its own separate file-reading or agent-expansion logic.

It only needs to detect structure and pass structured parts onward.

Then the general-purpose prompt-ingress machinery handles the heavy lifting.

That avoids duplicated logic and keeps the architecture clean.

---

# 20. Agent parts resolved here later become task-oriented synthetic guidance

A resolved `agent` part is only the first stage.

Later, `createUserMessage()` expands `agent` parts into:

- the agent part itself
- plus synthetic text instructing the runtime/model to call the task tool with that subagent

This is important because command prompt-part resolution is upstream of deeper semantic expansion.

It introduces the structure; later layers operationalize it.

---

# 21. File parts resolved here later become read/materialization actions

Likewise, file or directory parts created here later flow through `createUserMessage()`, which may:

- run `ReadTool`
- create synthetic read-trace text
- inline file content or attachments
- normalize directories specially

So `resolvePromptParts(...)` is a detector, not the final file-ingestion layer.

That distinction is important when reading the codebase.

---

# 22. `ConfigMarkdown.files(...)` intentionally excludes some false positives

The regex in `config/markdown.ts` is designed to avoid matching:

- word-internal `@` uses
- backtick-adjacent cases
- some punctuation-ending cases

This is a reminder that prompt-part resolution is trying to be author-friendly without treating every `@` character in prose as a file or agent reference.

That is a necessary balance.

---

# 23. Prompt-part resolution therefore operates as a best-effort structural promotion layer

It does not try to fully parse arbitrary markdown or command syntax.

Instead it promotes the most important inline references into structured parts when it can do so confidently.

That is a pragmatic design.

---

# 24. A representative resolution lifecycle

A typical lifecycle looks like this:

## 24.1 Command template finishes text expansion

- arguments and shell substitutions already resolved

## 24.2 `resolvePromptParts(...)` seeds a full text part

- entire template preserved

## 24.3 `@...` references are scanned

- deduplicated via `seen`

## 24.4 Each reference is classified

- existing directory -> directory file part
- existing file -> text/plain file part
- missing path but known agent -> agent part
- otherwise no structural part added

## 24.5 Structured parts flow into `prompt(...)`

- later materialized by general ingress logic

This is the actual command-text-to-parts pipeline.

---

# 25. Why this module matters architecturally

This layer shows how OpenCode keeps commands text-centric for author ergonomics while still benefiting from the richer structured prompt model used by the main session runtime.

It is a conversion boundary that prevents the command system from becoming a separate silo with duplicated ingestion logic.

That is very good architecture.

---

# 26. Key design principles behind this module

## 26.1 Command templates should remain human-authorable text, but important inline references should be promoted into structured runtime parts

So `resolvePromptParts(...)` keeps the full text while adding file or agent parts for detected references.

## 26.2 Structural detection and deep materialization should be separate stages

So this function only detects files/directories/agents, while later ingress code performs reading and semantic expansion.

## 26.3 The same inline syntax should support multiple useful reference classes when possible

So a missing file reference can still resolve as an agent mention.

## 26.4 Prompt-part resolution should preserve narrative context even when structural promotion fails

So unresolved references remain visible in the original text part.

---

# 27. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/session/prompt.ts`
2. `resolvePromptParts()`
3. `packages/opencode/src/config/markdown.ts`
4. `command()` in `session/prompt.ts`
5. `createUserMessage()` in `session/prompt.ts`

Focus on these functions and concepts:

- `resolvePromptParts()`
- `ConfigMarkdown.files()`
- `seen` deduplication
- `@...` path resolution against `Instance.worktree`
- agent fallback via `Agent.get(name)`
- directory vs file part creation
- handoff into `prompt(...)`
- later agent/file materialization in `createUserMessage()`

---

# 28. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Should command prompt-part resolution support a more explicit syntax for agent mentions to avoid ambiguity with missing files?
- **Question 2**: How should binary files be referenced intentionally from command templates, given the current optimistic `text/plain` file-part creation path here?
- **Question 3**: Should unresolved `@...` references ever surface a warning rather than silently remaining only in text?
- **Question 4**: How should prompt-part resolution evolve if commands need richer inline structured references beyond files and agents?
- **Question 5**: Are there edge cases in `ConfigMarkdown.files()` where legitimate file paths are still missed because of punctuation or formatting context?
- **Question 6**: Should relative reference resolution sometimes use the current working directory rather than workspace root for certain command sources?
- **Question 7**: How should prompt-part resolution interact with MCP resources or remote references in future command syntaxes?
- **Question 8**: What tests best guarantee that command-expanded text and structured parts stay semantically aligned as the template language evolves?

---

# 29. Summary

The `command_prompt_parts_resolution_and_agent_mentions` layer is how OpenCode turns expanded command text into structured prompt inputs without losing the original narrative prompt:

- it preserves the full expanded template as a text part
- it scans `@...` references and promotes them into file, directory, or agent parts when possible
- it deduplicates structural references and leaves unresolved ones visible in plain text
- it hands the resulting structured parts into the main prompt-ingress pipeline, where deeper file and agent materialization occurs

So this module is the bridge that lets text-authored commands participate fully in OpenCode’s richer structured prompt model.
