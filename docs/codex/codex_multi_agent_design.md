# Codex Multi-Agent Design

## Scope

This document explains the multi-agent design of `codex`, focusing on how parent and child agents are spawned, how they communicate, how they are governed, and how session/context state is divided between long-lived and turn-scoped runtime objects.

The analysis is grounded in the core runtime and protocol code, especially:

- `@/Users/simonwang/project/agent/codex/codex-rs/core/src/codex_delegate.rs:57-214`
- `@/Users/simonwang/project/agent/codex/codex-rs/core/src/guardian.rs:1-812`
- `@/Users/simonwang/project/agent/codex/codex-rs/core/src/tools/handlers/multi_agents.rs:1-416`
- `@/Users/simonwang/project/agent/codex/codex-rs/core/src/tasks/review.rs:1-274`
- `@/Users/simonwang/project/agent/codex/codex-rs/core/src/agent/role.rs:1-314`
- `@/Users/simonwang/project/agent/codex/codex-rs/core/src/agent/guards.rs:1-227`
- `@/Users/simonwang/project/agent/codex/codex-rs/core/src/codex.rs:741-820`
- `@/Users/simonwang/project/agent/codex/codex-rs/core/src/codex.rs:1258-1348`
- `@/Users/simonwang/project/agent/codex/codex-rs/protocol/src/protocol.rs:2248-2278`
- `@/Users/simonwang/project/agent/codex/codex-rs/app-server/README.md:823-847`
- `@/Users/simonwang/project/agent/codex/dev_docs/subagents_jobs_and_extended_orchestration.md:1-538`
- `@/Users/simonwang/project/agent/codex/dev_docs/session_turn_execution_loop.md:1-710`

## 1. Executive summary

`codex` is not structured as one monolithic agent with a few helper functions. It is a runtime that can spawn nested Codex sessions, route some approvals back to the parent turn, run specialized review subagents, and fan out batch jobs across many delegated workers.

The important architectural pattern is:

- the **session** is long-lived and owns durable state and shared services
- the **turn context** is short-lived and freezes the execution snapshot for one unit of work
- the **subagent runtime** is a real child Codex instance with its own event stream and submission channel
- the **parent runtime** remains the authority for approvals, cancellation, and user-visible orchestration

The design is therefore hierarchical, but not fragmented. Child agents reuse the same core session/turn machinery instead of implementing a separate agent engine.

## 2. The core object model

### `Session`

`Session` is the long-lived container for a Codex conversation. It owns things that survive across turns, such as conversation history, service handles, active turn tracking, feature state, and shared agent-control infrastructure.

Relevant structure:

```@/Users/simonwang/project/agent/codex/codex-rs/core/src/codex.rs:741-759
pub(crate) struct Session {
    pub(crate) conversation_id: ThreadId,
    tx_event: Sender<Event>,
    agent_status: watch::Sender<AgentStatus>,
    out_of_band_elicitation_paused: watch::Sender<bool>,
    state: Mutex<SessionState>,
    /// The set of enabled features should be invariant for the lifetime of the
    /// session.
    features: ManagedFeatures,
    pending_mcp_server_refresh_config: Mutex<Option<McpServerRefreshConfig>>,
    pub(crate) conversation: Arc<RealtimeConversationManager>,
    pub(crate) active_turn: Mutex<Option<ActiveTurn>>,
    pub(crate) services: SessionServices,
    js_repl: Arc<JsReplHandle>,
    next_internal_sub_id: AtomicU64,
}
```

### `TurnContext`

`TurnContext` is the per-turn execution snapshot. It freezes the model, provider, cwd, sandbox policy, approval policy, tool config, dynamic tools, and many other turn-local decisions.

Relevant structure:

```@/Users/simonwang/project/agent/codex/codex-rs/core/src/codex.rs:775-820
pub(crate) struct TurnContext {
    pub(crate) sub_id: String,
    pub(crate) trace_id: Option<String>,
    pub(crate) realtime_active: bool,
    pub(crate) config: Arc<Config>,
    pub(crate) auth_manager: Option<Arc<AuthManager>>,
    pub(crate) model_info: ModelInfo,
    pub(crate) session_telemetry: SessionTelemetry,
    pub(crate) provider: ModelProviderInfo,
    pub(crate) reasoning_effort: Option<ReasoningEffortConfig>,
    pub(crate) reasoning_summary: ReasoningSummaryConfig,
    pub(crate) session_source: SessionSource,
    /// The session's current working directory. All relative paths provided by
    /// the model as well as sandbox policies are resolved against this path
    /// instead of `std::env::current_dir()`.
    pub(crate) cwd: PathBuf,
    ...
    pub(crate) final_output_json_schema: Option<Value>,
    pub(crate) codex_linux_sandbox_exe: Option<PathBuf>,
    pub(crate) tool_call_gate: Arc<ReadinessFlag>,
    pub(crate) truncation_policy: TruncationPolicy,
    pub(crate) js_repl: Arc<JsReplHandle>,
    pub(crate) dynamic_tools: Vec<DynamicToolSpec>,
    pub(crate) turn_metadata_state: Arc<TurnMetadataState>,
    pub(crate) turn_skills: TurnSkillsContext,
    pub(crate) turn_timing_state: Arc<TurnTimingState>,
}
```

The split matters because the runtime must not let turn-local behavior drift in the middle of execution. The turn context is the stable answer to “what rules are in force for this one agent step?”.

### `SessionSource` and `SubAgentSource`

The protocol marks each session with a source, and nested agents use `SessionSource::SubAgent(...)` with a specific `SubAgentSource` variant.

Relevant protocol types:

```@/Users/simonwang/project/agent/codex/codex-rs/protocol/src/protocol.rs:2251-2278
pub enum SessionSource {
    Cli,
    #[default]
    VSCode,
    Exec,
    Mcp,
    SubAgent(SubAgentSource),
    #[serde(other)]
    Unknown,
}

pub enum SubAgentSource {
    Review,
    Compact,
    ThreadSpawn {
        parent_thread_id: ThreadId,
        depth: i32,
        #[serde(default)]
        agent_nickname: Option<String>,
        #[serde(default, alias = "agent_type")]
        agent_role: Option<String>,
    },
    MemoryConsolidation,
    Other(String),
}
```

This is the identity anchor for child sessions. It is how the runtime knows *why* a subagent exists.

## 3. Multi-agent roles in the system

Codex supports multiple multi-agent roles, but they are not all the same kind of agent.

### 3.1 Collaboration / spawn agents

The collaboration tool surface is implemented in `multi_agents.rs`. It translates model tool calls into agent-control operations and creates child sessions from the live parent turn.

Key idea:

- child agents inherit the parent turn’s effective runtime state
- role-specific config may be layered on top
- session identity is tagged as a subagent thread spawn

Relevant logic:

```@/Users/simonwang/project/agent/codex/codex-rs/core/src/tools/handlers/multi_agents.rs:244-311
pub(crate) fn build_agent_spawn_config(
    base_instructions: &BaseInstructions,
    turn: &TurnContext,
) -> Result<Config, FunctionCallError> {
    let mut config = build_agent_shared_config(turn)?;
    config.base_instructions = Some(base_instructions.text.clone());
    Ok(config)
}

fn build_agent_shared_config(turn: &TurnContext) -> Result<Config, FunctionCallError> {
    let base_config = turn.config.clone();
    let mut config = (*base_config).clone();
    config.model = Some(turn.model_info.slug.clone());
    config.model_provider = turn.provider.clone();
    config.model_reasoning_effort = turn.reasoning_effort;
    config.model_reasoning_summary = Some(turn.reasoning_summary);
    config.developer_instructions = turn.developer_instructions.clone();
    config.compact_prompt = turn.compact_prompt.clone();
    apply_spawn_agent_runtime_overrides(&mut config, turn)?;

    Ok(config)
}
```

Runtime overrides copied into the child include:

- approval policy
- shell environment policy
- sandbox policy
- cwd
- Linux sandbox executable

That is the core parent-to-child state transfer mechanism.

### 3.2 Review subagent

Review is a dedicated one-shot child thread. It is used for review workflows and has its own rubric, feature restrictions, and model selection logic.

Relevant design:

- disables non-review tools such as spawn-csv and collab
- sets explicit review instructions from `REVIEW_PROMPT`
- uses `AskForApproval::Never`
- runs as `SubAgentSource::Review`

```@/Users/simonwang/project/agent/codex/codex-rs/core/src/tasks/review.rs:87-129
async fn start_review_conversation(
    session: Arc<SessionTaskContext>,
    ctx: Arc<TurnContext>,
    input: Vec<UserInput>,
    cancellation_token: CancellationToken,
) -> Option<async_channel::Receiver<Event>> {
    let config = ctx.config.clone();
    let mut sub_agent_config = config.as_ref().clone();
    ...
    sub_agent_config.base_instructions = Some(crate::REVIEW_PROMPT.to_string());
    sub_agent_config.permissions.approval_policy = Constrained::allow_only(AskForApproval::Never);
    ...
    (run_codex_thread_one_shot(
        sub_agent_config,
        session.auth_manager(),
        session.models_manager(),
        input,
        session.clone_session(),
        ctx.clone(),
        cancellation_token,
        SubAgentSource::Review,
        None,
        None,
    )
```

This is a clean example of a specialized child agent with a narrow role and a strict output contract.

### 3.3 Guardian reviewer

Guardian is the approval-review subagent. It is a governance layer, not a generic worker.

It exists to decide whether certain `on-request` approvals can be auto-granted instead of shown to the user.

Relevant flow:

- parent turn decides whether approval routing should go through guardian
- guardian receives a compact transcript and a precise action summary
- guardian returns strict JSON with risk assessment
- fail closed on timeout, malformed output, or execution failure

The guardian review entrypoint and behavior are in `guardian.rs`.

## 4. Parent/child division of responsibilities

### Parent agent responsibilities

The parent session/turn remains the authority for:

- maintaining the durable transcript
- owning the active turn state
- deciding whether a child should be spawned
- brokering approvals
- receiving child events and mapping them into parent-visible state
- deciding whether a child result is surfaced, suppressed, or converted into a higher-level event
- enforcing cancellation and shutdown

### Child agent responsibilities

A child agent is responsible for:

- performing its own model sampling loop
- emitting its own events
- requesting approvals or user input when necessary
- following the config and role given at spawn time
- terminating when its task is complete or cancelled

The child is not a separate architecture. It is the same Codex runtime, just spawned with a different `SessionSource` and different config.

## 5. Communication model

Codex uses explicit channels and event forwarding, not shared mutable state between parent and child.

### 5.1 Spawn-time wiring

`run_codex_thread_interactive(...)` creates two channels:

- `tx_sub` / `rx_sub` for child events to the caller
- `tx_ops` / `rx_ops` for caller submissions to the child

It then spawns:

- an event-forwarding task
- an op-forwarding task

```@/Users/simonwang/project/agent/codex/codex-rs/core/src/codex_delegate.rs:73-133
let (tx_sub, rx_sub) = async_channel::bounded(SUBMISSION_CHANNEL_CAPACITY);
let (tx_ops, rx_ops) = async_channel::bounded(SUBMISSION_CHANNEL_CAPACITY);
...
tokio::spawn(async move {
    forward_events(...).await;
});
...
tokio::spawn(async move {
    forward_ops(codex_for_ops, rx_ops, cancel_token_ops).await;
});
```

### 5.2 Event forwarding semantics

Child events are not blindly passed through. The parent-side forwarder filters and reroutes approval-related events.

Ignored or suppressed in the delegate stream:

- legacy delta events
- token count events
- session configured events
- thread name updates

Rerouted to parent approval logic:

- `ExecApprovalRequest`
- `ApplyPatchApprovalRequest`
- `RequestPermissions`
- `RequestUserInput`

Forwarded outward as normal events:

- MCP tool begin/end
- terminal turn completion/abort events
- other non-approval events

This is the key communication rule: **children may speak, but the parent mediates authority-sensitive messages**.

### 5.3 Approval routing back to the parent

When a child requests shell, patch, permission, or user-input approval, the delegate layer does not let the child resolve it in isolation.

Instead it calls parent-session APIs such as:

- `request_command_approval(...)`
- `request_patch_approval(...)`
- `request_permissions(...)`
- `request_user_input(...)`

If guardian routing is enabled, the parent first asks the guardian reviewer.

### 5.4 Cancellation model

Each child task receives child cancellation tokens derived from the parent token.

This gives:

- cascading cancellation
- bounded lifetime
- clean shutdown if the parent goes away
- ability to stop one delegated task without destabilizing the parent runtime

The one-shot helper adds an additional shutdown bridge so that completion triggers child shutdown automatically.

## 6. How the guardian communicates

Guardian is its own child runtime, but it is narrower than a typical subagent.

### 6.1 Input construction

Guardian does not consume the whole raw history blindly. It gets:

- a curated transcript of relevant user, assistant, and tool entries
- a precise JSON serialization of the proposed action
- optional retry context
- an instruction boundary that separates evidence from the request

The transcript builder intentionally keeps the prompt reviewable and bounded.

### 6.2 Output contract

Guardian must output strict JSON with:

- `risk_level`
- `risk_score`
- `rationale`
- `evidence`

### 6.3 Decision policy

The parent fails closed unless guardian returns a low-enough risk score.

- `risk_score < 80` => approve
- otherwise => deny
- timeout / parse failure / subagent failure => deny

This is a governance subagent, not a best-effort hint generator.

## 7. How agent jobs coordinate many child agents

`agent_jobs.rs` is the batch orchestration layer.

It fans out a job over many items and emits progress back into the session as background events.

Key properties:

- each CSV row becomes a job item
- concurrency is bounded
- progress is emitted periodically
- runtime limits exist per item
- the parent session observes job state via background events

Relevant progress emission:

```@/Users/simonwang/project/agent/codex/codex-rs/core/src/tools/handlers/agent_jobs.rs:108-172
struct JobProgressEmitter {
    started_at: Instant,
    last_emit_at: Instant,
    last_processed: usize,
    last_failed: usize,
}
...
session
    .notify_background_event(turn, format!("agent_job_progress:{payload}"))
    .await;
```

This is a higher-level orchestration mechanism than a single subagent spawn. It coordinates a swarm of worker tasks, but still uses the same session and turn foundations.

## 8. Role management

Agent roles are configuration layers applied at spawn time.

The important separation is:

- **role definition**: what the role is allowed or intended to do
- **spawn orchestration**: when to create a child and which role to apply
- **runtime config application**: how the role config merges into the child session

`role.rs` explicitly states that it does **not** decide when to spawn a sub-agent or which role to use. That orchestration belongs elsewhere.

Relevant behavior:

- built-in roles and user-defined roles are resolved from config
- role config is layered at high precedence
- the caller’s current profile/provider is preserved unless the role explicitly takes ownership
- the spawn tool surface exposes available roles to the model

Built-in roles include the default role and example specialized roles such as `explorer` and `worker`.

The main design point is that role selection is a configuration concern, not a separate runtime.

## 9. Session and context management

### 9.1 Session lifetime

A `Session` owns durable state and shared services across turns.

It is the container that lets Codex preserve:

- history
- approval state
- active turn tracking
- managed features
- service handles
- rollout persistence
- subagent identity and spawn accounting

### 9.2 Turn lifetime

A `TurnContext` is created from session state and runtime inputs.

It captures the exact execution environment for one turn, including:

- cwd
- model/provider
- reasoning settings
- approvals and sandbox policy
- dynamic tools
- session source
- telemetry state
- final output schema
- JS REPL handle

That object is then passed into prompt assembly, tool routing, approval logic, and event generation.

### 9.3 Child session context

Child agents do not invent their own arbitrary context. They inherit from the parent turn and then may override via a role or specialized config.

For interactive subagents, the runtime explicitly passes:

- cloned auth manager
- cloned models manager
- shared skills/plugins/MCP/file-watcher/agent-control services
- optional initial history
- child session source

This keeps the child consistent with the parent while still giving it its own session identity.

### 9.4 Session source as observability metadata

Because child sessions are tagged as `SessionSource::SubAgent(...)`, downstream components can distinguish:

- normal interactive sessions
- review subagents
- compacting subagents
- thread-spawned workers
- guardian subagents
- memory consolidation agents

This matters for logging, filtering, analytics, and control policy.

## 10. Session startup and spawn guardrails

Codex enforces structural limits so multi-agent use does not spiral out of control.

Important guardrails include:

- maximum subagent depth
- global spawned-thread counting
- per-session nickname reservation for spawned agents
- disabling some features when depth is too high

Relevant protocol and guard logic:

- `SubAgentSource::ThreadSpawn { depth, ... }`
- `next_thread_spawn_depth(...)`
- `exceeds_thread_spawn_depth_limit(...)`
- `Guards::reserve_spawn_slot(...)`

The practical effect is that recursive spawning is bounded and observable, not unconstrained.

## 11. Communication and coordination patterns by role

### Interactive worker / spawned collaborator

- parent spawns a child
- child receives a task prompt and inherited runtime state
- child streams events back to the parent
- parent forwards approvals to itself or guardian
- parent may continue submitting further ops to the child

### Review subagent

- one-shot child
- narrow rubric
- no approvals requested from the child
- result is parsed into a review output object
- parent emits review-completion events

### Guardian reviewer

- one-shot child
- receives compact evidence and action summary
- returns strict JSON risk assessment
- parent decides approve/deny
- fail closed on any uncertainty

### Batch job workers

- many worker tasks under one job
- bounded concurrency
- periodic progress reporting
- final aggregation into job result / CSV output

## 12. Mental model for the whole architecture

A concise way to think about the multi-agent system is:

```text
Session
  = durable parent runtime and shared services

TurnContext
  = frozen per-turn execution snapshot

Subagent
  = child Codex runtime with its own session identity

Parent runtime
  = authority for approvals, cancellation, and orchestration

Guardian
  = specialized approval-review subagent

Review
  = specialized one-shot analysis subagent

Agent jobs
  = bounded fan-out orchestration over many child tasks
```

## 13. Why this design is strong

The architecture has several good properties:

- child agents are real runtimes, not prompt tricks
- authority stays with the parent
- approvals are centrally governed
- roles are config layers, not hardcoded forks
- session identity is explicit and observable
- turn-local behavior is frozen, so execution stays consistent
- batch fan-out and nested delegation reuse the same core runtime abstractions

## 14. Risks to avoid when extending it

If you extend this subsystem, avoid these mistakes:

- treating subagents like ad hoc helper functions instead of real sessions
- letting child runtimes own approvals independently
- bypassing `SessionSource` / `SubAgentSource`
- mutating turn behavior mid-execution instead of freezing it in `TurnContext`
- adding a one-off executor instead of reusing the core session/turn model
- removing depth or timeout limits on child spawning

## 15. Bottom line

Codex’s multi-agent design is hierarchical, explicit, and conservative.

The parent session remains the source of authority. Child agents are separate Codex sessions with their own event streams, but they inherit just enough state to remain coherent. Specialized roles like review and guardian narrow the child’s purpose further, while agent jobs lift the same architecture into bounded fan-out workflows.

That is the main design lesson: **Codex scales multi-agent behavior by reusing one core runtime and layering explicit roles, sources, channels, and guardrails on top of it**.
