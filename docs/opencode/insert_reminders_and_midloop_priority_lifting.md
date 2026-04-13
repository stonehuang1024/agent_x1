# Insert Reminders / Mid-Loop Priority Lifting

---

# 1. Module Purpose

This document explains the reminder-injection behavior around the session loop, focusing on two related mechanisms in `session/prompt.ts`:

- `insertReminders(...)`
- mid-loop wrapping of newly arrived user text in `<system-reminder>` blocks

The key questions are:

- Why does OpenCode inject reminder text into user-visible conversation state at all?
- How does `insertReminders(...)` modify the latest user message depending on agent mode and plan-mode flags?
- How does the loop elevate newly arrived user messages during a multi-step run?
- Why are reminders inserted as synthetic text parts instead of out-of-band metadata?
- What does this layer reveal about OpenCode’s strategy for preserving behavioral intent across long-running turns?

Primary source files:

- `packages/opencode/src/session/prompt.ts`
- `packages/opencode/src/tool/read.ts`

This layer is OpenCode’s **behavioral reminder injection and mid-loop salience management system**.

---

# 2. Why this layer matters

Long-running agent loops can drift.

They can also lose track of:

- current execution mode
- transitions between planning and building
- new user messages that arrive while prior work is still in flight

The reminder layer exists to keep these constraints and priorities visible to the model at the point where they matter.

This is not just prompt decoration.

It is a control mechanism for multi-step stateful agent behavior.

---

# 3. There are two different reminder systems here

The source shows two distinct mechanisms:

- `insertReminders(...)` modifies the latest user message before normal processing
- later in the loop, queued newer user text is wrapped in `<system-reminder>` if it arrived after the last finished assistant step

These solve related but different problems.

`insertReminders(...)` handles **mode- and workflow-specific guardrails**.

Mid-loop wrapping handles **priority lifting for newly arrived user input**.

---

# 4. `insertReminders(...)` targets the latest user message

The function begins by finding:

- `input.messages.findLast((msg) => msg.info.role === "user")`

If there is no user message, it returns unchanged.

This is important because reminder injection is anchored to the most current user turn, not spread indiscriminately across history.

That keeps the reminder local to the turn being processed.

---

# 5. When experimental plan mode is disabled, the logic is simpler

If `Flag.OPENCODE_EXPERIMENTAL_PLAN_MODE` is false, `insertReminders(...)` applies two older behaviors:

- add `PROMPT_PLAN` when the current agent is `plan`
- add `BUILD_SWITCH` when the session previously had a `plan` assistant and the current agent is `build`

Then it returns immediately.

So the non-experimental path is a smaller compatibility-style reminder system focused on plan/build mode switching.

---

# 6. Why the reminder is added as a synthetic text part

In both old and new flows, reminders are added by pushing a `text` part with:

- `synthetic: true`

This is a strong architectural choice.

The runtime does not keep these instructions in some hidden side structure.

It places them directly into message parts, while marking them as synthetic so they can still be distinguished from literal user-authored text.

That makes them visible to the model and inspectable in session state.

---

# 7. `PROMPT_PLAN` is a mode-entry behavioral override

When the agent is `plan`, the older logic appends `PROMPT_PLAN` to the latest user message.

This indicates that plan mode is not just selected by agent identity alone.

The runtime reinforces that mode with an explicit textual instruction inside the message stream.

That is important because agent identity and textual behavioral reminders work together rather than relying on one mechanism only.

---

# 8. `BUILD_SWITCH` is a mode-transition reminder, not a static mode reminder

The old logic adds `BUILD_SWITCH` only when:

- there was previously a plan assistant
- the current agent is `build`

So it specifically models the transition:

- planning phase -> implementation phase

This is more precise than merely saying “we are in build mode.”

It reminds the model that the session is leaving a prior planning state.

---

# 9. Why transition-aware reminders matter

A multi-phase workflow is about more than the current mode.

It is also about how the current mode relates to what happened previously.

The build-switch reminder preserves that temporal context explicitly, which helps the model understand why it should act differently now.

---

# 10. Experimental plan mode changes the reminder model substantially

When the plan-mode flag is enabled, the logic becomes more structured.

It looks at the latest assistant message and distinguishes two important transitions:

- switching from `plan` to a non-plan agent
- entering `plan` mode from a non-plan state

So the new path treats reminders as part of an explicit workflow state machine.

---

# 11. Switching from `plan` to non-`plan` adds a plan-file-aware build reminder

If:

- current agent is not `plan`
- latest assistant agent was `plan`

then the function computes:

- `const plan = Session.plan(input.session)`

If the plan file exists, it creates and persists a synthetic text part containing:

- `BUILD_SWITCH`
- plus a message telling the model a plan file exists and it should execute on that plan

This is a major improvement over the older static reminder.

The reminder is grounded in concrete session artifacts.

---

# 12. Why plan-file awareness is important

A plan/build transition is much more reliable when the runtime can point to:

- the actual plan file path
- the fact that it already exists

That converts a generic mode switch into an actionable instruction rooted in durable project state.

---

# 13. In experimental mode, the reminder part is persisted immediately

In the plan-to-build branch, the code uses:

- `await Session.updatePart(...)`

and then pushes the returned part into `userMessage.parts`.

This is important.

The reminder is not just an in-memory mutation for the current turn. It becomes durable session state immediately.

That matches the overall state-first architecture.

---

# 14. Entering `plan` mode creates a large `<system-reminder>` contract

If:

- current agent is `plan`
- latest assistant was not `plan`

then the function creates a long synthetic text part wrapped in `<system-reminder>`.

This reminder contains a full planning workflow contract, including:

- do not execute or edit arbitrary files
- only plan-file edits are allowed
- phased workflow expectations
- guidance on agent usage, questions, and final plan exit behavior

This is much more than a small reminder.

It is effectively a mode-specific operating contract injected into the user turn.

---

# 15. Why a large contract is injected when entering plan mode

Plan mode imposes behavioral constraints that are easy for a general-purpose coding agent to violate unless they are kept highly salient.

Examples include:

- avoid non-readonly actions
- only edit the plan file
- ask clarifying questions at the right time
- produce a final plan before exiting planning

Embedding that contract directly in the active user turn is a strong way to keep those constraints in scope throughout model processing.

---

# 16. The plan reminder is tied to the actual plan file path

The injected reminder mentions:

- the session plan file path
- whether it already exists
- whether it should be created or incrementally edited

This is an important implementation detail.

The reminder is not generic prose. It is dynamically parameterized by actual session state.

That makes it operationally specific.

---

# 17. `insertReminders(...)` mutates the message set before processor invocation

In the main loop, the runtime does:

- `msgs = await insertReminders({ messages: msgs, agent, session })`

before it resolves tools and before it builds the final system/messages payload for model processing.

This ordering matters.

Reminder injection is part of the effective prompt construction path, not an afterthought appended later.

---

# 18. Mid-loop priority lifting is a separate mechanism later in the loop

After the first-step setup and tool resolution, the loop checks:

- `if (step > 1 && lastFinished)`

Then it scans user messages newer than `lastFinished` and rewrites eligible text parts into a `<system-reminder>` block.

This is not mode-reminder logic.

It is concurrency-aware salience management.

---

# 19. Which parts get wrapped mid-loop

The wrapping logic only rewrites parts when all of these are true:

- message role is `user`
- message ID is newer than the last finished assistant message
- part type is `text`
- part is not `ignored`
- part is not `synthetic`
- part text is non-empty

This is a careful filter.

The runtime only elevates fresh, real, human-authored textual input that arrived after the prior completed assistant boundary.

---

# 20. Why synthetic and ignored parts are excluded

Synthetic parts already come from the runtime and often already encode control semantics.

Ignored parts, by definition, should not influence model behavior.

So the priority-lifting mechanism correctly focuses on genuine newly arrived human input rather than re-wrapping system-generated scaffolding.

---

# 21. What the wrapper actually says

The wrapped text becomes:

- `<system-reminder>`
- `The user sent the following message:`
- original text
- `Please address this message and continue with your tasks.`
- `</system-reminder>`

This is very revealing.

The runtime does not tell the model to drop prior work entirely.

It tells the model to:

- address the new message
- then continue existing tasks

That is a nuanced multitask continuation policy.

---

# 22. Why this is called priority lifting

The new user message already exists in the conversation history.

But by wrapping it as a system reminder, the runtime raises its salience above ordinary historical user text.

So the message is not merely present.

It is highlighted as current coordination-critical input.

That is why this behavior is best understood as priority lifting.

---

# 23. Why this mechanism exists only after `step > 1`

On the first loop step, the current user message is already the primary active turn.

No additional lifting is needed.

The problem only appears once the loop continues across multiple iterations and new user input may arrive while prior assistant work is still unfolding.

So gating this logic on `step > 1` is exactly right.

---

# 24. Why `lastFinished` is the right boundary marker

The wrapping logic compares new user messages against:

- the latest finished assistant message

This is a smart choice because it defines the most recent fully completed assistant boundary.

Any newer user message is something the agent has not fully answered yet.

That is precisely the class of messages that should be elevated.

---

# 25. The reminder model is stateful, not purely prompt-local

Some reminder parts are only appended in memory.

Others, especially in experimental plan-mode transitions, are persisted immediately through `Session.updatePart(...)`.

This shows the reminder system spans both:

- ephemeral prompt shaping for the current turn
- durable state mutation for workflow transitions

That is an important distinction.

---

# 26. Reminder injection is not unique to `insertReminders(...)`

The grep results also show `tool/read.ts` appending `<system-reminder>` when there are extra reading instructions.

That suggests `<system-reminder>` is a broader runtime convention, not a one-off string in `session/prompt.ts`.

So reminder blocks are part of a larger cross-module control language used to influence model behavior explicitly.

---

# 27. A representative reminder lifecycle

A typical lifecycle looks like this:

## 27.1 User turn is loaded for processing

- latest user message identified

## 27.2 Mode-specific reminders are injected

- plan-mode entry reminder
- build-switch reminder
- legacy plan/build reminders when experimental mode is off

## 27.3 Model processing begins

- reminders now exist inside the active message set

## 27.4 Loop continues to later steps

- new user text may arrive during ongoing work

## 27.5 Newly arrived human text is lifted into `<system-reminder>` form

- model is instructed to address it and then continue prior tasks

This is the real behavioral-control lifecycle for reminders.

---

# 28. Why this module matters architecturally

This layer shows that OpenCode does not rely only on static system prompts or agent selection to control behavior.

It also injects dynamic, state-dependent reminder text directly into active conversation state.

That allows the runtime to preserve:

- workflow mode constraints
- transition semantics
- responsiveness to concurrent user input

without rebuilding the entire orchestration model around special cases.

---

# 29. Key design principles behind this module

## 29.1 Critical workflow constraints should be made highly salient at the moment of execution

So reminders are injected directly into the active user message or rewritten as `<system-reminder>` blocks.

## 29.2 Multi-step loops need an explicit mechanism for re-prioritizing newly arrived user input

So fresh user text after the last finished assistant boundary is wrapped and elevated during later steps.

## 29.3 Behavioral control text should be distinguishable from literal user-authored text

So injected reminder parts are marked `synthetic: true` where they are created as parts.

## 29.4 Workflow transitions should be grounded in durable state artifacts, not just abstract mode names

So experimental plan/build reminders reference the actual session plan file path and existence state.

---

# 30. Recommended reading order

To dig deeper, read in this order:

1. `packages/opencode/src/session/prompt.ts`
2. `insertReminders(...)`
3. the mid-loop `<system-reminder>` wrapping block in `loop()`
4. `packages/opencode/src/tool/read.ts`

Focus on these functions and concepts:

- `insertReminders()`
- `PROMPT_PLAN`
- `BUILD_SWITCH`
- experimental plan-mode branches
- `Session.plan(input.session)`
- `Filesystem.exists(plan)`
- `Session.updatePart(...)` in reminder branches
- the `step > 1 && lastFinished` mid-loop wrapper
- filtering of non-synthetic user text

---

# 31. Open questions for further investigation

There are several useful follow-up questions worth exploring:

- **Question 1**: Should all reminder injection become durable state, or should some remain purely ephemeral prompt shaping?
- **Question 2**: How often do `<system-reminder>` wrappers materially improve responsiveness to mid-loop user interruptions in practice?
- **Question 3**: Could repeated reminder injection create prompt bloat in very long sessions, and if so how should it be controlled?
- **Question 4**: Should there be a more typed internal representation of reminders rather than relying on textual `<system-reminder>` conventions?
- **Question 5**: How should reminder behavior interact with compaction so critical workflow constraints are not lost or duplicated awkwardly?
- **Question 6**: Should non-text user inputs arriving mid-loop also receive an analogous priority-lifting treatment?
- **Question 7**: What other subsystems besides `tool/read.ts` rely on the same reminder convention, and is that convention stable enough to be treated as an architectural primitive?
- **Question 8**: How should plan/build reminder policies evolve if the plan workflow becomes more strongly typed in future versions?

---

# 32. Summary

The `insert_reminders_and_midloop_priority_lifting` layer is the mechanism that keeps workflow constraints and new user input salient during long-running session execution:

- `insertReminders(...)` injects synthetic plan/build and plan-workflow reminders into the latest user turn
- experimental plan mode persists some reminders as durable session parts tied to the actual plan file state
- later loop steps lift newly arrived human text into `<system-reminder>` form so it gets addressed promptly without abandoning ongoing tasks
- the broader runtime uses `<system-reminder>` as a cross-module control convention

So this module is the dynamic salience-control layer that helps OpenCode remain both mode-aware and interruption-aware during multi-step agent runs.
