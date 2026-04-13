# Codex Developer Documentation Module Index

This index decomposes `README_codex_main.md` into standalone deep-dive documents. Each document focuses on one subsystem, its implementation logic, runtime responsibilities, data flow, invariants, and the main algorithms or coordination patterns that matter for maintainers.

## Planned module breakdown

1. `session_turn_execution_loop.md`
   - Session lifecycle
   - Submission loop
   - Turn context construction
   - Sampling loop
   - Follow-up turns, retries, and turn completion semantics

2. `prompt_and_instruction_pipeline.md`
   - `BaseInstructions`
   - Prompt assembly
   - User instructions, developer instructions, skills injection
   - Output schema and personality propagation

3. `context_manager_and_compaction.md`
   - Context history model
   - Model-visible transcript filtering
   - Settings update items
   - Token estimation and compaction strategy

4. `tool_system_and_dispatch.md`
   - `ToolsConfig`
   - `ToolSpec`
   - `ToolRouter`
   - `ToolRegistry`
   - `ToolCallRuntime`
   - Error propagation and tool result reinjection

5. `model_streaming_and_response_protocol.md`
   - `ResponseItem`
   - SSE event processing
   - Stream item state machine
   - Differences across modes such as plan, agent, and code-oriented execution

6. `retrieval_indexing_and_context_selection.md`
   - `file-search`
   - Text-level narrowing
   - Why Codex uses layered retrieval instead of a monolithic semantic index
   - How retrieved material becomes prompt-visible context

7. `diff_patch_and_change_aggregation.md`
   - `TurnDiffTracker`
   - Patch verification and application path
   - Unified diff generation
   - Rename/add/delete handling and turn-level aggregation

8. `skills_rules_and_agents_integration.md`
   - `SkillsManager`
   - skill root precedence and deduplication
   - AGENTS.md instructions
   - Explicit skill selection and deterministic injection

9. `app_server_and_vscode_integration.md`
   - App-server runtime role
   - JSON-RPC lifecycle
   - Thread/turn/item abstractions
   - Why the IDE layer stays thin

10. `cli_tui_sdk_and_frontend_surfaces.md`
    - CLI entry routing
    - TUI as a rendering surface
    - SDK roles and external embedding boundaries

11. `subagents_jobs_and_extended_orchestration.md`
    - `SessionSource`
    - delegated codex threads
    - agent jobs and background workers
    - multi-agent orchestration boundaries

## Suggested reading order

- Start with `session_turn_execution_loop.md`
- Then read `prompt_and_instruction_pipeline.md`
- Then `context_manager_and_compaction.md`
- Then `tool_system_and_dispatch.md`
- Then `model_streaming_and_response_protocol.md`
- After that, branch into retrieval, diff, skills, app-server, and SDK/frontends

## Documentation conventions for all module files

- Each file is written in English.
- Each file focuses on one module only.
- Each file ends with a `Next questions to investigate` section.
- Each file distinguishes:
  - public role of the module
  - internal data flow
  - runtime invariants
  - algorithmic or orchestration patterns
  - extension risks and modification guidance

## Current progress

- Completed: module decomposition plan
- In progress: `session_turn_execution_loop.md`
