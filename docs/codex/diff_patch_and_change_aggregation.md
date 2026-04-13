# Diff, Patch, and Change Aggregation in Codex

## Scope

This document explains how Codex tracks file mutations within a turn, generates user-visible diffs, validates and applies patches, and aggregates multiple file-editing actions into one coherent turn-level change model.

The main files are:

- `codex-rs/core/src/turn_diff_tracker.rs`
- `codex-rs/core/src/tools/handlers/apply_patch.rs`
- the surrounding tool runtime and event-emission paths that pass the shared diff tracker through execution

This subsystem matters because Codex does not rely on a simple “ask Git what changed” model. It maintains its own turn-level change-tracking view, which is essential for tool-driven editing workflows.

---

## 1. The key idea: changes are tracked at turn scope, not at individual tool-call scope

Codex treats a user-visible turn as the coherence unit for file changes.

Within one turn, the runtime may execute:

- multiple `apply_patch` calls
- shell edits
- renames
- add/delete sequences
- repeated writes to the same file

If each tool call reported changes independently without aggregation, the user would see fragmented and misleading results.

So Codex introduces a turn-scoped tracker that accumulates all touched files and compares the turn’s initial baseline against the final on-disk state.

That design is one of the strongest implementation choices in the repository.

---

## 2. `TurnDiffTracker`: the core abstraction

`TurnDiffTracker` stores several internal mappings:

- external path -> internal stable temp name
- internal stable temp name -> baseline file info
- internal stable temp name -> current external path
- a cache of git roots

### Why internal stable names exist

The stable internal name solves a hard problem:

- external paths can change during a turn because of renames or moves

If the system keyed files directly by current path only, then rename tracking would become messy and diffs could become inaccurate.

By assigning a stable internal identity per touched file, Codex can track:

- what this file was originally
- where it lives now
- what its original baseline content was

That is a much better representation for turn-level change aggregation.

---

## 3. Baseline snapshots are created when a file is first seen

The heart of the algorithm starts in `on_patch_begin(...)`.

When a file is touched for the first time in the turn, Codex captures a baseline snapshot containing:

- original path
- original bytes
- file mode
- blob oid

### Why “first touch” is the right baseline moment

The user-visible diff should answer:

- what changed during this turn?

That means the baseline should correspond to the state before any tool in this turn mutated the file.

If Codex captured a new baseline after each edit, then the final diff would lose the full turn history and show only the last local delta.

That would be a bad UX and a bad debugging experience.

---

## 4. Additions are modeled explicitly instead of faked as updates

The tracker uses special handling for files that do not yet exist on disk at first touch.

This lets Codex represent additions in a Git-like way using:

- zero object id
- `/dev/null` semantics

### Why this matters

A new file is not conceptually the same as an update to an empty file.

By modeling additions explicitly, Codex can produce proper diff headers and a much cleaner user-visible change summary.

This is a subtle but important correctness detail.

---

## 5. Rename tracking is not a post-processing guess

When `on_patch_begin(...)` sees an update with `move_path`, it updates the mapping from the stable internal identity to the new external path.

### Why this is important

Codex does not wait until the end of the turn and then guess that two paths are probably the same file.

Instead, it records rename intent at mutation time.

That yields a more reliable internal model because the runtime already knows the causal relationship:

- this change moved path A to path B

### Why this is better than inference

Inference after the fact can be brittle, especially if files are modified heavily or if multiple related files move in one turn.

Explicit rename tracking keeps the semantics clean.

---

## 6. Git root discovery is used for display and blob identity, not for computing the whole diff

`TurnDiffTracker` uses cached git-root detection and can ask Git for blob oids relative to the repository root.

### What this is used for

- producing repo-relative display paths
- obtaining blob hashes when available
- keeping output more aligned with Git-style expectations

### What it is not used for

Codex does not simply shell out to `git diff` and use that as the turn result.

That is a crucial distinction.

The diff itself is recomputed from the tracker’s own baseline snapshot and the current on-disk state.

This independence from Git’s staging or working-tree assumptions is a major advantage.

---

## 7. `get_unified_diff(...)`: recomputing the turn’s aggregated truth

At the end of the turn or when needed for display, Codex calls `get_unified_diff(...)`.

This function:

- orders tracked files in a stable way
- computes a per-file diff from baseline to current disk state
- concatenates the results into one aggregated unified diff

### Why stable ordering matters

Stable output ordering improves:

- readability
- deterministic testing
- reproducibility across runs

Codex sorts by repo-relative path to keep the output consistent and intuitive.

---

## 8. The diff is computed in memory, not delegated wholesale to Git

`get_file_diff(...)` compares:

- baseline bytes
- current bytes on disk

It also considers:

- file mode changes
- additions
- deletions
- binary versus text behavior

### Why in-memory diff computation is a strong design choice

Agent edits do not necessarily align neatly with:

- Git staging state
- user commits
- repository cleanliness assumptions

By computing diffs internally, Codex gets a turn-accurate view even when:

- the repo is dirty beforehand
- files were never staged
- new files were introduced mid-turn
- renames occurred inside the turn

That is exactly what a turn-oriented coding agent needs.

---

## 9. Binary files are handled explicitly

If a file cannot be treated as valid UTF-8 text on both sides, the tracker emits:

- `Binary files differ`

### Why this matters

It avoids pretending a text unified diff exists when it does not.

This preserves correctness and keeps the diff output faithful to the actual file category.

Not every change should be forced into a line diff representation.

---

## 10. Diff headers preserve add/delete/mode semantics

`get_file_diff(...)` also emits Git-like headers that capture:

- new file mode
- deleted file mode
- mode changes
- old and new headers

### Why this is more than cosmetic

These headers communicate information that matters operationally:

- was the file created?
- was it deleted?
- did it change from regular file to symlink or vice versa?

For a coding agent, those differences are often just as important as line-level content changes.

---

## 11. `apply_patch` is not a blind write path

The `ApplyPatchHandler` in `tools/handlers/apply_patch.rs` shows that Codex treats patch application as a disciplined workflow.

The handler does not simply receive a patch string and write files immediately.

It first:

- parses the tool payload
- re-parses and verifies the patch as an `apply_patch` action
- derives affected paths
- computes effective permissions
- decides whether patch application can be handled directly or should be delegated through the orchestrated runtime path

This is a much safer and more structured design than a naive patch-execution helper.

---

## 12. Patch verification happens before permission and execution

The handler uses `codex_apply_patch::maybe_parse_apply_patch_verified(...)`.

### Why this matters

A patch string is not trustworthy just because the model emitted it.

The runtime must know:

- whether it is really an apply-patch payload
- whether it is syntactically and structurally correct
- which files it affects

Without that validation step, later permission checks and diff tracking would operate on uncertain data.

Validation first is the correct order of operations.

---

## 13. Effective permissions are derived from the actual patch action

Once the patch is verified, the handler computes:

- affected file paths
- merged granted permissions
- additional write permissions required for the action
- effective file-system sandbox policy

### Why this is a strong design

Permission evaluation is based on the real write footprint of the patch, not on a generic “patches can modify anything” assumption.

That means approvals and sandbox checks can be more precise.

Precision here improves both safety and usability.

---

## 14. Mutating behavior is explicit in the handler model

The apply-patch handler declares itself as mutating via `is_mutating(...) -> true`.

### Why this matters

Mutability is a fundamental property for:

- approval logic
- telemetry
- orchestration policy
- user trust

Codex’s tool system makes mutating behavior explicit instead of inferring it later from side effects.

That is the right choice for an agent that edits code.

---

## 15. Direct application versus delegated execution

The apply-patch path can produce two broad outcomes:

- immediate output
- delegated execution through `InternalApplyPatchInvocation::DelegateToExec(...)`

### Why two paths exist

Not every patch application needs the same execution machinery.

Some can be handled directly once validated.

Others need orchestrated runtime handling with:

- approval semantics
- event emission
- sandbox execution integration
- richer tool lifecycle management

This dual path keeps the implementation flexible without sacrificing correctness.

---

## 16. Tool events are emitted around patch execution

When patch application is delegated, the handler creates a `ToolEmitter` and emits begin/finish events with a `ToolEventCtx`.

### Why this matters

Patch execution is not just a hidden file mutation. It is a user-visible and runtime-visible event.

Event emission supports:

- frontend progress rendering
- observability
- approval UX
- diff lifecycle reporting

This is one of the reasons Codex feels like a full agent runtime rather than a hidden automation script.

---

## 17. The diff tracker is shared through the tool execution path

Patch execution is given access to the shared turn diff tracker.

### Why this is critical

The patch handler and the diff subsystem are not independent. They cooperate so that:

- mutations are tracked from the start of the turn
- later edits to the same file accumulate correctly
- turn-level diff generation remains accurate even after multiple tool actions

Without threading the tracker through tool execution, Codex would lose one of its main coherence guarantees.

---

## 18. Turn-level aggregation is the real “merge” concept here

In this subsystem, “merge” does not primarily mean Git’s three-way merge algorithm.

It more often means:

- aggregating many tool-driven edits into one final turn diff
- merging permissions and sandbox views
- merging change intent into a coherent user-visible result

### Why this is an important conceptual clarification

Users often think in Git terms, but Codex’s runtime has a separate concern:

- what changed during this one conversational action?

That is a turn-aggregation problem more than a repository-merge problem.

---

## 19. The hidden algorithm of turn-level change aggregation

A good summary of the subsystem is:

```text
1. on first file touch, capture baseline snapshot
2. assign a stable internal identity per tracked file
3. update current path mapping when moves/renames occur
4. apply edits through validated and permission-aware runtimes
5. compare final on-disk state against the original per-turn baseline
6. emit one unified diff representing the whole turn
```

This is the core algorithmic idea of the change-tracking system.

---

## 20. Why this design is stronger than shelling out to `git diff`

If Codex had relied on `git diff` directly for turn results, it would inherit several problems:

- unrelated repository dirtiness could pollute the result
- newly created files might depend on current staging state
- turn-specific rename semantics could become fuzzy
- repeated edits in one turn would be harder to attribute cleanly

By keeping its own baseline and recomputing diffs in memory, Codex gets a cleaner answer to the question it actually cares about:

- what did this turn do?

That is a much better fit for an agent runtime.

---

## 21. What can go wrong if this subsystem is changed carelessly

### Risk 1: losing first-touch baseline semantics

If the baseline is updated after every edit, the final diff will no longer represent the whole turn.

### Risk 2: removing stable internal identities

Rename tracking will become much harder and more error-prone.

### Risk 3: delegating diff truth to external Git state

Turn-level correctness will degrade when repositories are already dirty or partially staged.

### Risk 4: bypassing patch verification

Permission checks and downstream execution logic will operate on untrusted or malformed patch intent.

### Risk 5: decoupling patch execution from diff tracking

The system will lose its ability to aggregate multiple edits into one coherent turn result.

---

## 22. How to extend this subsystem safely

If you add a new mutating tool or change-tracking behavior, the safest sequence is:

1. decide when the file should become tracked for baseline capture
2. preserve stable internal identity across renames or multiple writes
3. make mutating tools thread the shared turn diff tracker through execution
4. keep validation before permissions and execution
5. preserve the invariant that user-visible diff reflects the whole turn, not the last substep only

### Questions to ask first

- Does this tool mutate files directly or indirectly?
- Should the mutation be compared against the first state seen in the turn or some later local checkpoint?
- Can the tool rename or move files?
- Does the permission system have enough information to infer the write footprint precisely?
- Will the user-visible final diff still be coherent if this tool runs multiple times in one turn?

Those questions fit the design of the existing subsystem.

---

## 23. Condensed mental model

Use this model when reading the code:

```text
TurnDiffTracker
  = first-touch baseline + stable file identity + final turn diff

ApplyPatchHandler
  = patch verification + permission derivation + orchestrated execution

unified diff
  = baseline-to-final comparison over the whole turn
```

The most important takeaway is this:

- Codex treats code changes as a turn-scoped aggregated artifact, not as a loose stream of unrelated file edits

That is the defining property of this subsystem.

---

## Next questions to investigate

- How do shell-based editing tools and unified exec paths cooperate with `TurnDiffTracker` compared with `apply_patch`?
- Where exactly are `TurnDiff` events emitted for frontends, and what triggers them in normal versus aborted turns?
- How does the tracker behave when a file is created and then deleted within the same turn?
- Are there edge cases around symlink handling, binary detection, or permission inheritance that deserve deeper analysis?
- How do undo or rollback flows interact with the turn-level diff model?
