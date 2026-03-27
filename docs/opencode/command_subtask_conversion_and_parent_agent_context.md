# Command Subtask Conversion / Parent Agent and Model Context

---

# 1. Module Purpose

This document explains the command-execution branch where an expanded command becomes a formal `subtask` part instead of ordinary prompt parts, focusing on how OpenCode preserves parent-session execution context while requesting a spawned subagent run.

The key questions are:

- When does a command become a `subtask` instead of a normal prompt?
- Why does the command path package the command as a single `subtask` part rather than expanded text/file/agent parts?
- How does OpenCode preserve the parent session’s agent/model context while still specifying a target subagent and subtask model?
- What information is carried inside the `subtask` part?
- How does this branch connect slash commands to the later session-loop subtask execution path?

Primary source files:

- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/command/index.ts`

This layer is OpenCode’s **command-to-subtask conversion and parent-context preservation layer**.

---

# 2. Why this layer matters

Not every command should directly become a normal user prompt to the current agent.

Some commands are really requests to:

- delegate work to a subagent
- use a specialized model or agent mode
- preserve the parent conversation while spawning a targeted subtask branch

The command-to-subtask branch is how OpenCode expresses that distinction structurally.

---

# 3. The branch decision happens after template expansion and part resolution

In `command(...)`, the code first:

- resolves the command
- expands arguments and shell substitutions
- validates model and agent
- computes `templateParts`

Only then does it compute:

- `const isSubtask = (agent.mode === "subagent" && command.subtask !== false) || command.subtask === true`

This is important.

Subtask conversion is a semantic execution decision layered on top of already-resolved command content and agent metadata.

---

# 4. Why `isSubtask` depends on both agent mode and command policy

The condition means a command becomes a subtask when either:

- the target agent is a subagent and the command does not explicitly opt out
- or the command explicitly opts in with `subtask === true`

This is a balanced rule.

Agent mode provides the default semantic.

Command metadata can still override it.

That makes the behavior both principled and configurable.

---

# 5. Why explicit opt-out matters for subagents

Just because an agent is marked as a subagent does not mean every command targeting it must always become a delegated task.

The `command.subtask !== false` clause preserves room for commands that want the subagent’s content or template semantics without forcing the later subtask execution branch.

That is a useful escape hatch.

---

# 6. Why explicit opt-in matters for non-subagents

Conversely, a command may want true subtask semantics even if the target agent is not globally marked as a subagent.

`command.subtask === true` allows command authors to request that behavior directly.

So subtask conversion is not coupled too rigidly to agent mode alone.

---

# 7. In the subtask branch, OpenCode discards the ordinary expanded parts and creates one `subtask` part

When `isSubtask` is true, `parts` becomes a single-element array containing:

- `type: "subtask"`
- target `agent`
- `description`
- `command`
- target `model`
- prompt text

This is a major design choice.

The command no longer enters the session as ordinary rich prompt parts.

It enters as an explicit task-delegation artifact.

---

# 8. Why one `subtask` part is better than many ordinary parts here

A subtask request is not just another user prompt.

It is a request for the runtime to switch branches and execute task-tool logic later.

Encoding it as one structured `subtask` part preserves that semantic intention much more clearly than flattening it into normal text or agent parts.

That is the right abstraction.

---

# 9. The `subtask` part carries the target agent identity directly

The part stores:

- `agent: agent.name`

This is crucial.

The delegated target is part of the durable message state itself.

Later loop logic does not need to infer which subagent to use from surrounding prose.

The request is explicit and typed.

---

# 10. The command name is also embedded in the subtask request

The part includes:

- `command: input.command`

This is a useful provenance detail.

It means later runtime logic and future readers can tell which slash command produced this subtask request, not only which target agent it asked for.

That improves observability and debugging.

---

# 11. Why `description` is included too

The part stores:

- `description: command.description ?? ""`

This provides additional human-readable context about what the subtask is meant to do.

So the subtask request contains both:

- structured target execution metadata
- lightweight descriptive intent

That is a good balance.

---

# 12. The prompt payload for the subtask is simplified to text only

The code comment explicitly says:

- `TODO: how can we make task tool accept a more complex input?`

and currently stores:

- the first text part found in `templateParts`

as the subtask prompt.

This is a very important limitation.

The subtask branch currently collapses the command’s richer resolved structure into a plain text prompt payload for the task tool.

---

# 13. Why this limitation matters architecturally

Ordinary command execution can preserve richer prompt parts like files and agent mentions.

The current subtask branch cannot fully carry that richer structure into the delegated task input.

So there is an intentional simplification boundary here.

The runtime still supports formal delegation, but with a narrower payload shape than the full prompt-ingress model.

---

# 14. The target subtask model is preserved separately inside the `subtask` part

The part stores:

- `model.providerID`
- `model.modelID`

based on the earlier `taskModel` resolution.

This is very important.

The delegated task can target a specific model independently of the parent user message’s eventual model.

That is exactly the kind of execution split a subtask system should support.

---

# 15. Why parent user context and subtask target context must be kept separate

The parent session still needs its own coherent user-turn identity:

- who is speaking now
- what model the parent session is using
- what agent currently owns the main conversation

The delegated subtask needs its own target context:

- which subagent to run
- which model it should use

If these were collapsed, the parent session and spawned task semantics would blur.

The code avoids that.

---

# 16. `userAgent` is deliberately different in the subtask branch

When `isSubtask` is true, the code sets:

- `userAgent = input.agent ?? default agent`

instead of the command’s target agent.

This is subtle but crucial.

The enclosing user message stays attributed to the parent session’s conversational agent, not to the spawned subagent.

That preserves parent-session continuity.

---

# 17. `userModel` is also deliberately different in the subtask branch

When `isSubtask` is true, `userModel` becomes:

- parsed `input.model` if provided
- otherwise `lastModel(sessionID)`

not the subtask target model.

Again, this is exactly right.

The parent session user turn remains anchored to the current conversation’s model context, while the delegated task model rides inside the `subtask` part.

---

# 18. Why this split is the heart of the design

The parent user message says:

- “within this ongoing session, I am requesting a delegated task”

The `subtask` part says:

- “here is the exact target agent/model/prompt for that delegated task”

This separation keeps the main session coherent while still enabling structured delegation.

That is a very good design.

---

# 19. The non-subtask branch highlights the difference clearly

If `isSubtask` is false, `parts` becomes:

- `templateParts`
- plus any input parts

and `userAgent` / `userModel` align with the command target context.

So the subtask branch is not just a small variation.

It changes both:

- the shape of the persisted parts
- the meaning of the user message’s own agent/model identity

That is why it deserves separate documentation.

---

# 20. The command path still triggers `command.execute.before` before either branch is submitted

Before calling `prompt(...)`, the runtime triggers:

- `command.execute.before`

with the computed `parts`.

This means plugins can observe whether a command was converted into a single `subtask` part or a richer ordinary prompt-part set.

So subtask conversion is visible at the extensibility boundary too.

---

# 21. The branch ultimately feeds into ordinary `prompt(...)`

Even in the subtask case, the code still calls:

- `prompt({ sessionID, messageID, model: userModel, agent: userAgent, parts, variant })`

This is important.

There is no separate alternate entry point for subtask commands.

They enter the session through the same user-message creation path, just with a different structured part payload.

That keeps the architecture unified.

---

# 22. Later loop logic is what actually executes the subtask

Once the user message containing a `subtask` part is persisted, the main session loop later detects pending `subtask` parts and enters the dedicated subtask-execution branch.

This means command-to-subtask conversion is only the first half of the delegation story.

It serializes the delegation request into durable state.

The loop later consumes that state and performs the execution.

---

# 23. Why serializing the subtask request first is so important

By storing the subtask request in the message model before executing it, OpenCode gains:

- resumability
- replayability
- inspectability
- a clean state boundary between request and execution

That is much stronger than executing the subtask immediately as an ephemeral branch inside `command(...)`.

---

# 24. A representative command-to-subtask lifecycle

A typical lifecycle looks like this:

## 24.1 Slash command resolves to target agent/model/template

- template expansion and validation already complete

## 24.2 Runtime decides `isSubtask`

- based on target agent mode and command subtask policy

## 24.3 Command is converted into one `subtask` part

- target agent/model/description/command/prompt stored explicitly

## 24.4 Parent user message is still created in parent session context

- `userAgent` and `userModel` remain tied to current conversation context

## 24.5 Main session loop later consumes the `subtask` part

- delegated task branch executes from durable state

This is the actual delegation pipeline.

---

# 25. Why this module matters architecturally

This layer shows that OpenCode takes delegation seriously as a first-class runtime concept.

Commands do not merely *suggest* that a subagent should be used.

They can compile into a formal subtask request object that preserves a clean distinction between:

- parent session context
- delegated task target context

That is a much stronger design than implicit delegation through prompt wording alone.

---

# 26. Key design principles behind this module

## 26.1 Delegation requests should be represented as typed runtime state, not just prose

So qualifying commands become a structured `subtask` part.

## 26.2 Parent session identity and delegated task identity should remain distinct

So `userAgent` / `userModel` for the parent message differ from the target agent/model stored in the subtask part.

## 26.3 Command metadata should be able to refine or override generic agent-mode defaults

So `command.subtask` can explicitly opt in or out.

## 26.4 Delegation should be serialized before execution to preserve resumability and observability

So the subtask request is persisted through `prompt(...)` and only executed later by the session loop.

---

# 27. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/session/prompt.ts`
2. `command()`
3. the `isSubtask` branch
4. `createUserMessage()`
5. the subtask-execution branch in `loop()`

Focus on these functions and concepts:

- `isSubtask`
- `parts` construction for subtask vs non-subtask
- `userAgent`
- `userModel`
- stored `subtask` fields
- later loop consumption of pending subtask parts
- the `TODO` about richer task-tool input

---

# 28. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: How should the task tool evolve so subtask requests can preserve richer structured inputs beyond the current first-text-part prompt simplification?
- **Question 2**: Should command-to-subtask conversion preserve more provenance, such as original template parts or source command type?
- **Question 3**: Are there edge cases where the current `isSubtask` policy is surprising for users, especially with commands targeting subagents but opting out?
- **Question 4**: Should parent-session `userModel` selection in the subtask branch ever explicitly inherit more from the command target model?
- **Question 5**: How should file attachments or agent mentions embedded in command templates be represented when a command becomes a subtask?
- **Question 6**: Should subtask parts include a richer structured description of expected outputs or verification criteria?
- **Question 7**: How should nested subtask-producing commands behave if a subagent command itself wants to spawn another subtask?
- **Question 8**: What tests best guarantee that parent-session context and delegated task context never get conflated in future changes?

---

# 29. Summary

The `command_subtask_conversion_and_parent_agent_context` layer is where qualifying slash commands stop being ordinary prompt expansions and become formal delegation requests:

- `isSubtask` uses both agent mode and command policy to decide whether a command should compile into a `subtask` part
- the resulting `subtask` part stores the target agent, target model, command provenance, description, and prompt payload explicitly
- the enclosing user message still preserves the parent session’s own agent/model context
- the later session loop consumes the stored subtask request and performs the actual delegated execution

So this module is the boundary where command execution becomes structured delegation without sacrificing parent-session continuity.
