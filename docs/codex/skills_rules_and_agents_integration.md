# Skills, Rules, and AGENTS.md Integration in Codex

## Scope

This document explains how Codex loads skills, layers them across scopes, caches them by working directory, converts repository rules into prompt-visible instruction fragments, and integrates both rules and skills into the broader execution pipeline.

The main files are:

- `codex-rs/core/src/skills/manager.rs`
- `codex-rs/core/src/skills/loader.rs`
- `codex-rs/core/src/instructions/user_instructions.rs`
- related instruction and project-doc discovery paths referenced by the codebase

This subsystem matters because Codex does not treat repository rules and reusable task guidance as hidden prompt constants. Instead, it models them as discoverable, layered, and prompt-visible inputs.

---

## 1. The key idea: skills and rules are injected context, not hardcoded behavior

Codex separates two related but distinct concepts:

- repository or directory rules
- reusable skill descriptions

Both eventually influence model behavior, but the architecture does not implement them by burying everything in the base prompt.

Instead, Codex treats them as structured context inputs that can be:

- discovered
- layered
- cached
- explicitly selected
- injected into the prompt as separate fragments

This is a very strong design choice because it keeps the agent extensible without making the base instruction layer bloated or repository-specific.

---

## 2. What “rules” mean in practice

In the Codex architecture, rules are primarily associated with:

- `AGENTS.md`
- project-level or directory-level documentation
- user-supplied instruction fragments associated with a location in the repository

These rules answer questions like:

- how should the assistant behave in this repository?
- what conventions or constraints apply in this directory?
- what local workflow expectations should override generic coding behavior?

These are not task-specific capability modules. They are behavior constraints and repository guidance.

---

## 3. What “skills” mean in practice

Skills are more like reusable capability packages.

They answer questions such as:

- how should the assistant perform a particular class of task?
- what extra instructions belong to that task family?
- what tools, dependencies, or policy information are attached to that reusable capability?

Skills are therefore closer to:

- task guidance
- domain knowledge capsules
- reusable procedure descriptions

This is different from repository rules, even though both are eventually injected into the model context.

---

## 4. `SkillsManager`: the high-level loader and cache boundary

The central management object is `SkillsManager` in `skills/manager.rs`.

It owns:

- the Codex home path
- the plugins manager
- a cache keyed by working directory

### Why the manager is keyed by `cwd`

This is a strong signal about intended semantics:

- skills are not global in an undifferentiated way
- they are resolved relative to the current repository or working context

That is exactly the right behavior for a coding agent that may operate in many repositories with different conventions and skill sets.

---

## 5. Caching by working directory is a major practical optimization

`SkillsManager` caches `SkillLoadOutcome` values per working directory.

### Why this matters

Loading skills may involve:

- reading configuration layers
- scanning directories
- parsing `SKILL.md`
- reading metadata files
- integrating plugin-provided roots
- building implicit invocation indexes

Doing that on every turn would be unnecessarily expensive.

By caching per `cwd`, Codex preserves responsiveness while still letting the skill set vary naturally by project location.

### Why per-cwd caching is the right granularity

If the cache were truly global, skill sets from one repository could bleed into another. If it were too fine-grained, the system would lose performance unnecessarily.

Per-working-directory caching is a practical middle ground.

---

## 6. Bundled system skills are treated as installable state

The manager’s constructor can install or uninstall bundled system skills depending on configuration.

### Why this is interesting

This shows that system skills are not just compiled constants.

They are managed as a materialized skill source under Codex home, which means:

- they can be enabled or disabled cleanly
- the same loading pipeline can likely treat them similarly to other roots
- system-level skill behavior remains explicit in the file-based skill ecosystem

This is more flexible than hardcoding system skills into Rust source logic.

---

## 7. Skill roots are layered and merged from multiple sources

The loader builds skill roots from multiple places, including:

- config-layer-derived roots
- plugin-provided skill roots
- repository-local skill roots
- extra user roots passed explicitly

### Why this matters

Skills are an extensibility surface, so one root is not enough.

Codex has to support:

- repository-local skills
- user-owned skills
- system or admin skills
- plugin-contributed skills

That makes root layering a central feature, not a corner case.

---

## 8. Root precedence is explicit and important

The loader assigns a priority ordering by `SkillScope`:

- `Repo`
- `User`
- `System`
- `Admin`

with repo roots ranked highest during deduplication ordering.

### Why this ordering makes sense

Repository-local behavior usually has the strongest claim on relevance inside that repository.

A user-level skill may be broadly useful, but if the repository defines a more specific version, the repository should usually win.

System and admin layers provide defaults or centrally managed guidance, but they should not casually override repository-local intent.

This precedence model is one of the key design choices in the whole subsystem.

---

## 9. Deduplication happens after discovery, not before

The loader first discovers skills across roots and then deduplicates by path.

### Why that matters

Layered discovery and later deduplication preserve two important behaviors:

- all candidate roots can participate
- precedence can be encoded in ordering and subsequent sorting

This is better than ad hoc early termination because it makes the final selection behavior more deliberate and inspectable.

---

## 10. Skills are not just Markdown blobs; they have metadata and policy surfaces

The loader parses not only `SKILL.md` but also supporting metadata such as:

- interface information
- dependencies
- policy
- permissions
- managed network overrides

### Why this matters

Skills are not merely human-readable text snippets. They are also capability descriptors.

That means a skill may carry:

- display-facing metadata
- implicit invocation eligibility
- dependency declarations
- permission-related semantics

This is much richer than a generic prompt template system.

---

## 11. Skills are intentionally constrained by loader validation

The loader defines limits and validation rules such as:

- required frontmatter structure
- field length limits
- scan-depth limits
- limits on number of scanned skill directories

### Why this matters

Without validation and bounded scanning, the skill system could become:

- fragile
- slow
- easy to abuse with malformed content
- unpredictable across repositories

These limits indicate that Codex treats the skill layer as an engineered subsystem, not a loose content folder.

---

## 12. Skills can be disabled via layered configuration

`finalize_skill_outcome(...)` computes disabled paths from configuration layers.

That means the effective skill set is not only discovered from roots. It is also filtered by later policy decisions.

### Why this matters

A skill existing on disk does not automatically mean it is active.

This is another important separation:

- discovery determines what exists
- configuration determines what is enabled

That separation is useful for controlled rollout, local overrides, and avoiding multiple competing implementations from all being active at once.

---

## 13. Implicit invocation indexes are built from allowed skills

After loading, Codex builds implicit skill path indexes from the allowed set.

This suggests that skills can be invoked not only by explicit exact references, but also through a more structured implicit matching path.

### Why this matters

The system appears to support both:

- explicit skill selection
- implicit resolution in some contexts

But the implicit path is still based on a precomputed allowed subset rather than on open-ended guesswork over all discovered files.

That keeps the mechanism more controlled.

---

## 14. AGENTS.md instructions are serialized into explicit prompt fragments

`UserInstructions` in `instructions/user_instructions.rs` contain:

- a `directory`
- a `text` payload

and serialize into a text fragment wrapped with AGENTS.md markers.

### Why this matters

Repository rules are not injected as invisible host-side logic. They become explicit model-visible content.

That makes agent behavior more explainable and more faithful to repository-local governance.

### Why directory labeling is important

The serialized form includes the directory context. That helps preserve provenance:

- which part of the repository supplied these instructions?

Provenance matters in layered rule systems.

---

## 15. Skill instructions are serialized differently from AGENTS.md rules

`SkillInstructions` are converted into prompt-visible content with structured fields such as:

- `<name>`
- `<path>`
- contents

wrapped in a skill fragment marker.

### Why separate serialization matters

Repository rules and skills are not semantically identical, so they should not be presented to the model in exactly the same undifferentiated form.

By giving skills their own fragment wrapper and metadata tags, Codex preserves:

- source identity
- capability identity
- cleaner downstream reasoning about what kind of instruction this is

This is a subtle but valuable design detail.

---

## 16. The model sees both rules and skills as prompt-visible context, but their jobs differ

Even though both become `ResponseItem`s, their intended semantics differ.

### Rules

Typically answer:

- how should work be done here?
- what repository conventions apply?
- what local constraints should override generic behavior?

### Skills

Typically answer:

- how should this class of task be performed?
- what specific capability guidance or task recipe should be followed?

### Why that distinction matters

If rules and skills were treated as the same thing, the prompt would become less interpretable and future extension logic would be harder to manage.

Codex keeps the distinction visible.

---

## 17. Skills integrate with plugins as first-class sources

The manager combines plugin-provided skill roots into the loading pipeline.

### Why this matters

This shows that the skill system is part of Codex’s extensibility model, not a closed built-in-only feature.

Plugins can extend not just tool availability but also the guidance layer of the agent.

That makes the architecture richer and more modular.

---

## 18. Rule and skill discovery is repository-aware

The codebase around project documentation shows that AGENTS.md discovery is tied to project-root search behavior and directory traversal rules.

This means Codex does not just read one file in the current directory. It reasons hierarchically about project documentation.

### Why this matters

Repository rules are often layered:

- root-level global rules
- nested directory-specific rules

A hierarchical scan lets the runtime carry both broad and local guidance instead of forcing one to overwrite the other prematurely.

This is a strong match for real codebase governance patterns.

---

## 19. The skill subsystem balances determinism and flexibility

This subsystem could easily become too fuzzy if it tried to infer everything from vague references.

Instead, the architecture suggests a more disciplined balance:

- explicit roots
- explicit scope ordering
- explicit enable/disable filtering
- explicit path- and name-based skill identities
- explicit serialization into prompt fragments

That structure is exactly what keeps a skill system usable in a large coding agent.

---

## 20. The hidden algorithm of the subsystem

A good summary is:

```text
1. collect skill roots from config, plugins, repo, and user overrides
2. load skills from all roots
3. dedupe and sort them by scope precedence and identity
4. filter them through configuration-driven enable/disable logic
5. build indexes for allowed implicit invocation
6. serialize active rules and skills into distinct prompt-visible fragments
7. inject those fragments into the turn’s prompt pipeline
```

This is not a simplistic “read Markdown and stuff it into a prompt” mechanism.

It is a layered discovery-and-injection system.

---

## 21. Why this design is stronger than hardcoding rules into the base prompt

If repository rules and reusable skills were hardcoded into the base instruction layer, several problems would appear:

- the base prompt would become repository-specific
- local overrides would be much harder
- skill discovery and plugin extension would be clumsy
- replay and prompt provenance would become less clear

By externalizing rules and skills into discoverable sources and injecting them explicitly, Codex keeps the core runtime reusable while still letting each repository heavily shape behavior.

That is exactly the right tradeoff.

---

## 22. What can go wrong if this subsystem is changed carelessly

### Risk 1: collapsing rules and skills into one undifferentiated instruction blob

That would reduce interpretability and future extensibility.

### Risk 2: weakening root precedence semantics

Repository-local intent could get overridden by user or system defaults in surprising ways.

### Risk 3: bypassing enable/disable filtering

The active skill set would become inconsistent with configuration.

### Risk 4: making implicit invocation too fuzzy

This would introduce nondeterminism and possibly choose the wrong skill when the user expected precision.

### Risk 5: hiding repository rules from prompt-visible context

That would make agent behavior less explainable and less faithful to local project guidance.

---

## 23. How to extend this subsystem safely

If you add a new skill source, rule source, or selection mechanism, the safe sequence is usually:

1. define where the new source fits in root precedence
2. keep discovery separate from enable/disable policy
3. preserve explicit identity and provenance for each injected fragment
4. decide whether it belongs in rule-style serialization or skill-style serialization
5. make sure the resulting prompt fragment remains structured and inspectable

### Questions to ask first

- Is this source repository-specific, user-specific, system-wide, or plugin-provided?
- Should it override existing roots or just add another candidate layer?
- Is it a behavioral rule or a task capability?
- Should it be eligible for implicit invocation, explicit invocation, or both?
- How will users understand why a given skill or rule was injected into the model context?

Those questions align well with the current architecture.

---

## 24. Condensed mental model

Use this model when reading the code:

```text
SkillsManager
  = cwd-scoped loader + cache + finalizer

loader
  = root discovery + parsing + precedence ordering + dedupe

AGENTS.md / rules
  = repository-behavior constraints serialized as user-instruction fragments

skills
  = reusable capability descriptions serialized as skill fragments
```

The most important takeaway is this:

- Codex treats both repository rules and reusable capabilities as explicit, layered, prompt-visible context rather than hidden hardcoded prompt text

That is the defining property of this subsystem.

---

## Next questions to investigate

- How exactly are explicit skill mentions collected and resolved when the user references a skill by name or path?
- What heuristics govern implicit invocation, and where are the boundaries that prevent over-triggering?
- How are plugin namespaces reflected in skill identity and conflict avoidance?
- How does project-level AGENTS.md discovery behave across deeply nested repositories or nonstandard project-root markers?
- Which parts of skill metadata currently affect runtime policy versus only UI or descriptive behavior?
