# DeerFlow 2.0 — Prompt Engineering & Multi-Agent Orchestration Deep Dive

> **Document Scope**: This document provides an exhaustive analysis of DeerFlow's prompt engineering system and multi-agent orchestration, including the complete system prompt template, dynamic assembly logic, sub-agent communication patterns, and the task decomposition workflow.

---

## Table of Contents

1. [System Prompt Architecture](#1-system-prompt-architecture)
2. [Complete Prompt Template Analysis](#2-complete-prompt-template-analysis)
3. [Dynamic Prompt Assembly](#3-dynamic-prompt-assembly)
4. [Clarification System Design](#4-clarification-system-design)
5. [Skills Prompt Integration](#5-skills-prompt-integration)
6. [Deferred Tools Prompt Section](#6-deferred-tools-prompt-section)
7. [Sub-Agent System Prompt](#7-sub-agent-system-prompt)
8. [Agent Personality (SOUL.md)](#8-agent-personality-soulmd)
9. [Multi-Agent Orchestration](#9-multi-agent-orchestration)
10. [Sub-Agent Executor Implementation](#10-sub-agent-executor-implementation)
11. [Task Tool — The Communication Bridge](#11-task-tool--the-communication-bridge)
12. [Sub-Agent Types & Configuration](#12-sub-agent-types--configuration)
13. [Concurrency Control](#13-concurrency-control)
14. [Context Isolation Strategy](#14-context-isolation-strategy)
15. [Prompt Design Patterns & Principles](#15-prompt-design-patterns--principles)
16. [Summary](#16-summary)

---

## 1. System Prompt Architecture

### 1.1 Design Philosophy

DeerFlow's system prompt follows several key principles:

1. **XML-Tagged Sections**: Each concern is wrapped in XML tags for clear semantic boundaries
2. **Dynamic Assembly**: The prompt is built at agent creation time, not hardcoded
3. **Conditional Inclusion**: Sections are included/excluded based on configuration
4. **Progressive Disclosure**: Skills and tools are listed by name, loaded on demand
5. **Behavioral Enforcement**: Critical rules are repeated and emphasized with examples

### 1.2 Prompt Section Map

```
SYSTEM_PROMPT_TEMPLATE
│
├── <role> ─────────────── Agent identity (always present)
├── <soul> ─────────────── Custom personality (optional, from SOUL.md)
├── <memory> ───────────── Long-term memory (optional, if enabled)
├── <thinking_style> ───── Reasoning guidelines (always present)
├── <clarification_system> Clarification workflow (always present)
├── <skill_system> ─────── Available skills (optional, if skills exist)
├── <available-deferred-tools> Deferred tool names (optional, if tool_search enabled)
├── <subagent_system> ──── Sub-agent orchestration (optional, if subagent enabled)
├── <working_directory> ── File system layout (always present)
├── <response_style> ───── Output formatting (always present)
├── <citations> ────────── Citation format (always present)
├── <critical_reminders> ─ Key behavioral rules (always present)
└── <current_date> ─────── Current date (always present)
```

---

## 2. Complete Prompt Template Analysis

### 2.1 Role Section

```xml
<role>
You are {agent_name}, an open-source super agent.
</role>
```

- `{agent_name}` defaults to "DeerFlow 2.0" but can be customized per-agent
- Establishes the agent's identity in a single line

### 2.2 Thinking Style Section

```xml
<thinking_style>
- Think concisely and strategically about the user's request BEFORE taking action
- Break down the task: What is clear? What is ambiguous? What is missing?
- **PRIORITY CHECK: If anything is unclear, missing, or has multiple interpretations,
  you MUST ask for clarification FIRST - do NOT proceed with work**
{subagent_thinking}
- Never write down your full final answer or report in thinking process, but only outline
- CRITICAL: After thinking, you MUST provide your actual response to the user.
  Thinking is for planning, the response is for delivery.
- Your response must contain the actual answer, not just a reference to what you thought about
</thinking_style>
```

**Key design decisions**:
- Thinking is for **planning only**, not for writing the final answer
- Clarification check is embedded in thinking guidelines
- When subagent mode is active, adds decomposition check:
  ```
  - **DECOMPOSITION CHECK: Can this task be broken into 2+ parallel sub-tasks?
    If YES, COUNT them. If count > {n}, you MUST plan batches of ≤{n}...**
  ```

### 2.3 Clarification System Section

This is one of the most detailed sections, implementing a strict `CLARIFY → PLAN → ACT` workflow:

**5 Mandatory Clarification Scenarios**:

| Type | Icon | When to Use | Example |
|------|------|-------------|---------|
| `missing_info` | ❓ | Required details not provided | "Create a web scraper" without target website |
| `ambiguous_requirement` | 🤔 | Multiple valid interpretations | "Optimize the code" — performance? readability? memory? |
| `approach_choice` | 🔀 | Several valid approaches exist | "Add authentication" — JWT? OAuth? session? |
| `risk_confirmation` | ⚠️ | Destructive actions need confirmation | Deleting files, modifying production configs |
| `suggestion` | 💡 | Recommendation needs approval | "I recommend refactoring. Should I proceed?" |

**Enforcement rules** (explicitly stated):
- ❌ DO NOT start working and then ask for clarification mid-execution
- ❌ DO NOT skip clarification for "efficiency"
- ❌ DO NOT make assumptions when information is missing
- ✅ Analyze → Identify unclear aspects → Ask BEFORE any action

### 2.4 Working Directory Section

```xml
<working_directory existed="true">
- User uploads: `/mnt/user-data/uploads`
- User workspace: `/mnt/user-data/workspace`
- Output files: `/mnt/user-data/outputs`

**File Management:**
- Uploaded files are automatically listed in the <uploaded_files> section
- Use `read_file` tool to read uploaded files
- For PDF, PPT, Excel, Word: converted Markdown versions available
- All temporary work in `/mnt/user-data/workspace`
- Final deliverables must be in `/mnt/user-data/outputs`
{acp_section}
</working_directory>
```

### 2.5 Response Style Section

```xml
<response_style>
- Clear and Concise: Avoid over-formatting unless requested
- Natural Tone: Use paragraphs and prose, not bullet points by default
- Action-Oriented: Focus on delivering results, not explaining processes
</response_style>
```

### 2.6 Citations Section

A comprehensive citation system for research tasks:
- **Inline format**: `[citation:TITLE](URL)` after claims
- **Sources section**: Standard markdown links at the end
- **Workflow**: Search → Extract URLs → Write with citations → Collect in Sources
- **Strict rules**: Never write research content without citations

### 2.7 Critical Reminders Section

```xml
<critical_reminders>
- **Clarification First**: ALWAYS clarify unclear requirements BEFORE starting work
{subagent_reminder}
- Skill First: Always load the relevant skill before starting complex tasks
- Progressive Loading: Load resources incrementally as referenced in skills
- Output Files: Final deliverables must be in `/mnt/user-data/outputs`
- Clarity: Be direct and helpful
- Including Images and Mermaid: Use images and diagrams when helpful
- Multi-task: Better utilize parallel tool calling
- Language Consistency: Keep using the same language as user's
- Always Respond: You MUST always provide a visible response after thinking
</critical_reminders>
```

---

## 3. Dynamic Prompt Assembly

### 3.1 Assembly Function

```python
def apply_prompt_template(
    subagent_enabled=False,
    max_concurrent_subagents=3,
    *,
    agent_name=None,
    available_skills=None,
) -> str:
    # 1. Get memory context
    memory_context = _get_memory_context(agent_name)
    
    # 2. Build subagent section (if enabled)
    subagent_section = _build_subagent_section(n) if subagent_enabled else ""
    subagent_reminder = "..." if subagent_enabled else ""
    subagent_thinking = "..." if subagent_enabled else ""
    
    # 3. Get skills section
    skills_section = get_skills_prompt_section(available_skills)
    
    # 4. Get deferred tools section
    deferred_tools_section = get_deferred_tools_prompt_section()
    
    # 5. Build ACP section
    acp_section = _build_acp_section()
    
    # 6. Get agent personality
    soul = get_agent_soul(agent_name)
    
    # 7. Format template
    prompt = SYSTEM_PROMPT_TEMPLATE.format(
        agent_name=agent_name or "DeerFlow 2.0",
        soul=soul,
        skills_section=skills_section,
        deferred_tools_section=deferred_tools_section,
        memory_context=memory_context,
        subagent_section=subagent_section,
        subagent_reminder=subagent_reminder,
        subagent_thinking=subagent_thinking,
        acp_section=acp_section,
    )
    
    # 8. Append current date
    return prompt + f"\n<current_date>{datetime.now().strftime('%Y-%m-%d, %A')}</current_date>"
```

### 3.2 Conditional Sections

| Section | Condition | Token Impact |
|---------|-----------|-------------|
| `<soul>` | Agent has SOUL.md file | 100-500 tokens |
| `<memory>` | Memory enabled + has data | 200-1000 tokens |
| `<skill_system>` | Skills exist and are enabled | 50-200 tokens (listing only) |
| `<available-deferred-tools>` | tool_search enabled + deferred tools exist | 10-100 tokens |
| `<subagent_system>` | Subagent mode enabled | 1500-2000 tokens |
| `{subagent_thinking}` | Subagent mode enabled | 50 tokens |
| `{subagent_reminder}` | Subagent mode enabled | 50 tokens |
| `{acp_section}` | ACP agents configured | 100 tokens |

### 3.3 Token Budget Estimation

| Mode | Estimated System Prompt Size |
|------|------------------------------|
| Minimal (no skills, no memory, no subagent) | ~2000 tokens |
| Standard (skills + memory) | ~3000-4000 tokens |
| Full (skills + memory + subagent + deferred tools) | ~5000-6000 tokens |

---

## 4. Clarification System Design

### 4.1 The ask_clarification Tool

```python
@tool
def ask_clarification(
    question: str,
    clarification_type: str = "missing_info",
    context: str | None = None,
    options: list[str] | None = None,
) -> str:
    """Ask the user for clarification before proceeding."""
```

### 4.2 Clarification Flow

```
User: "Deploy the application"
    │
    ▼
Agent (thinking): "Missing environment info — I MUST ask for clarification"
    │
    ▼
Agent calls: ask_clarification(
    question="Which environment should I deploy to?",
    clarification_type="approach_choice",
    context="I need to know the target environment",
    options=["development", "staging", "production"]
)
    │
    ▼
ClarificationMiddleware intercepts:
    1. Formats message with icon (🔀) and options
    2. Creates ToolMessage with formatted content
    3. Returns Command(goto=END) — interrupts execution
    │
    ▼
SSE stream sends clarification to frontend
    │
    ▼
Frontend renders clarification UI with options
    │
    ▼
User responds: "staging"
    │
    ▼
New HumanMessage("staging") → Agent resumes
    │
    ▼
Agent: "Deploying to staging..." (proceeds with work)
```

### 4.3 Why Command(goto=END)?

The clarification middleware uses `Command(goto=END)` instead of a regular tool response because:
1. It **stops the agent loop** — the agent cannot continue without user input
2. It **preserves state** — the conversation is checkpointed at this point
3. It **resumes cleanly** — the next user message continues from the checkpoint
4. It **prevents assumptions** — the agent cannot guess and proceed

---

## 5. Skills Prompt Integration

### 5.1 Skills Section Format

```xml
<skill_system>
You have access to skills that provide optimized workflows for specific tasks.

**Progressive Loading Pattern:**
1. When a user query matches a skill's use case, call `read_file` on the skill's main file
2. Read and understand the skill's workflow and instructions
3. The skill file contains references to external resources under the same folder
4. Load referenced resources only when needed during execution
5. Follow the skill's instructions precisely

**Skills are located at:** /mnt/skills

<available_skills>
    <skill>
        <name>Deep Research</name>
        <description>Conduct thorough research on any topic with citations</description>
        <location>/mnt/skills/public/deep-research/SKILL.md</location>
    </skill>
    <skill>
        <name>Chart Visualization</name>
        <description>Generate interactive charts and visualizations</description>
        <location>/mnt/skills/public/chart-visualization/SKILL.md</location>
    </skill>
    ...
</available_skills>
</skill_system>
```

### 5.2 XML Structure for Skills

Skills use nested XML tags (`<skill>`, `<name>`, `<description>`, `<location>`) for structured parsing. This format:
- Is easily parsed by LLMs
- Provides clear semantic boundaries
- Supports filtering by name or description
- Includes the exact file path for `read_file`

---

## 6. Deferred Tools Prompt Section

### 6.1 Format

```xml
<available-deferred-tools>
github__list_repos
github__create_issue
slack__send_message
slack__list_channels
jira__create_ticket
jira__search_issues
</available-deferred-tools>
```

### 6.2 How the Agent Uses It

1. Agent sees tool names in `<available-deferred-tools>`
2. When a task requires one of these tools, agent calls `tool_search("select:github__list_repos")`
3. `tool_search` returns the full OpenAI function schema
4. The tool is promoted (removed from deferred registry)
5. On the next LLM call, the tool's schema is included in `bind_tools`
6. Agent can now call the tool normally

---

## 7. Sub-Agent System Prompt

### 7.1 The Sub-Agent Section

When subagent mode is enabled, a massive `<subagent_system>` section is injected (~1500-2000 tokens). It contains:

1. **Core principle**: "DECOMPOSE, DELEGATE, SYNTHESIZE"
2. **Hard concurrency limit**: Maximum N task calls per response (configurable, default 3)
3. **Available subagent types**: general-purpose, bash
4. **Orchestration strategy**: When to use subagents vs. direct execution
5. **Multi-batch execution**: How to handle >N sub-tasks across multiple turns
6. **Usage examples**: Single batch and multi-batch patterns
7. **Counter-examples**: When NOT to use subagents

### 7.2 Concurrency Limit Enforcement

The prompt is extremely explicit about the concurrency limit:

```
⛔ HARD CONCURRENCY LIMIT: MAXIMUM {n} `task` CALLS PER RESPONSE. THIS IS NOT OPTIONAL.
- Each response, you may include at most {n} `task` tool calls.
- Any excess calls are silently discarded by the system.
- Before launching subagents, you MUST count your sub-tasks in your thinking.
```

This is reinforced in:
- `<thinking_style>` section (decomposition check)
- `<subagent_system>` section (detailed rules)
- `<critical_reminders>` section (summary reminder)
- Multiple examples and counter-examples

### 7.3 When to Use vs. Not Use Subagents

**USE subagents when:**
- Complex research questions requiring multiple sources
- Multi-aspect analysis with independent dimensions
- Large codebases needing simultaneous analysis
- Comprehensive investigations requiring thorough coverage

**DO NOT use subagents when:**
- Task cannot be decomposed into 2+ parallel sub-tasks
- Ultra-simple actions (read one file, quick edits)
- Need immediate clarification from user
- Meta conversation about history
- Sequential dependencies (each step depends on previous)

---

## 8. Agent Personality (SOUL.md)

### 8.1 How It Works

Custom agents can have a `SOUL.md` file that defines their personality:

```python
def get_agent_soul(agent_name):
    soul = load_agent_soul(agent_name)
    if soul:
        return f"<soul>\n{soul}\n</soul>\n"
    return ""
```

### 8.2 SOUL.md Example

```markdown
You are a senior Python developer with expertise in:
- FastAPI and async programming
- Database design and optimization
- Testing best practices (pytest, TDD)

Your communication style:
- Always explain the "why" behind technical decisions
- Provide code examples with comments
- Suggest improvements proactively
- Use Python type hints consistently
```

### 8.3 Where SOUL.md Lives

```
.deer-flow/agents/{agent_name}/SOUL.md
```

---

## 9. Multi-Agent Orchestration

### 9.1 Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    Lead Agent                                 │
│                                                               │
│  System Prompt: Full DeerFlow prompt with <subagent_system>   │
│  Tools: All tools + task tool                                 │
│  Context: Full conversation history                           │
│  Memory: Long-term memory access                              │
│                                                               │
│  Decision: "This needs 3 parallel research tasks"             │
│                                                               │
│  tool_calls: [                                                │
│    task(desc="Research A", prompt="...", type="general"),      │
│    task(desc="Research B", prompt="...", type="general"),      │
│    task(desc="Research C", prompt="...", type="general"),      │
│  ]                                                            │
└──────┬──────────────┬──────────────┬─────────────────────────┘
       │              │              │
       ▼              ▼              ▼
┌──────────┐  ┌──────────┐  ┌──────────┐
│ Sub-Agent│  │ Sub-Agent│  │ Sub-Agent│
│    A     │  │    B     │  │    C     │
│          │  │          │  │          │
│ Prompt:  │  │ Prompt:  │  │ Prompt:  │
│ Subagent │  │ Subagent │  │ Subagent │
│ system   │  │ system   │  │ system   │
│ prompt + │  │ prompt + │  │ prompt + │
│ task     │  │ task     │  │ task     │
│ prompt   │  │ prompt   │  │ prompt   │
│          │  │          │  │          │
│ Tools:   │  │ Tools:   │  │ Tools:   │
│ All      │  │ All      │  │ All      │
│ except   │  │ except   │  │ except   │
│ task,    │  │ task,    │  │ task,    │
│ clarify, │  │ clarify, │  │ clarify, │
│ present  │  │ present  │  │ present  │
│          │  │          │  │          │
│ Context: │  │ Context: │  │ Context: │
│ Fresh    │  │ Fresh    │  │ Fresh    │
│ (task    │  │ (task    │  │ (task    │
│  only)   │  │  only)   │  │  only)   │
└──────────┘  └──────────┘  └──────────┘
       │              │              │
       ▼              ▼              ▼
   Result A       Result B       Result C
       │              │              │
       └──────────────┼──────────────┘
                      │
                      ▼
              Lead Agent receives
              all 3 results as
              ToolMessages
                      │
                      ▼
              Synthesizes into
              final response
```

### 9.2 Communication Protocol

Sub-agents communicate with the lead agent through a simple protocol:

1. **Lead → Sub**: Task description + prompt (via `task` tool call args)
2. **Sub → Lead**: Result text (via `task` tool return value)
3. **No direct sub-to-sub communication**
4. **No access to lead agent's conversation history**
5. **Shared filesystem** (same sandbox, same thread directories)

---

## 10. Sub-Agent Executor Implementation

### 10.1 SubagentExecutor

```python
class SubagentExecutor:
    _scheduler_pool = ThreadPoolExecutor(max_workers=8)
    _execution_pool = ThreadPoolExecutor(max_workers=8)
    
    def __init__(self, config: SubagentConfig, tools, thread_data, sandbox_state):
        self._config = config
        self._tools = tools
        self._thread_data = thread_data
        self._sandbox_state = sandbox_state
        self._timeout = config.timeout_seconds
    
    def execute_async(self):
        """Start execution in background thread pool."""
        self._future = self._scheduler_pool.submit(self._run_task)
    
    def _run_task(self):
        """Run the actual agent execution."""
        future = self._execution_pool.submit(self.execute)
        return future.result(timeout=self._timeout)
    
    def execute(self):
        """Create and run the sub-agent."""
        # 1. Build middleware chain (minimal)
        middlewares = build_subagent_runtime_middlewares()
        
        # 2. Create agent
        agent = create_agent(
            model=create_chat_model(name=self._config.model),
            tools=self._filtered_tools(),
            middleware=middlewares,
            system_prompt=self._config.system_prompt,
        )
        
        # 3. Run with task prompt
        result = asyncio.run(self._aexecute(agent, task_prompt))
        return SubagentResult(content=result)
    
    def _filtered_tools(self):
        """Remove disallowed tools (task, ask_clarification, present_files)."""
        disallowed = set(self._config.disallowed_tools or [])
        return [t for t in self._tools if t.name not in disallowed]
```

### 10.2 Two-Pool Architecture

The executor uses two thread pools:

```
_scheduler_pool (8 workers)
    │ Manages scheduling and timeout
    │
    └── _execution_pool (8 workers)
            │ Runs actual agent execution
            │ asyncio.run() for async agent
```

**Why two pools?**
- The scheduler pool handles timeout enforcement
- The execution pool runs the actual agent
- Separation prevents timeout handling from blocking execution slots

### 10.3 Timeout Handling

```python
def _run_task(self):
    future = self._execution_pool.submit(self.execute)
    try:
        return future.result(timeout=self._timeout)
    except TimeoutError:
        future.cancel()
        return SubagentResult(content="Task timed out", error=True)
```

---

## 11. Task Tool — The Communication Bridge

### 11.1 Task Tool Definition

```python
@tool("task")
def task_tool(
    runtime: ToolRuntime,
    description: str,
    prompt: str,
    subagent_type: str = "general-purpose",
) -> str:
    """Delegate a task to a subagent for autonomous execution."""
```

### 11.2 Execution Flow

```python
def task_tool(runtime, description, prompt, subagent_type):
    # 1. Get subagent config
    config = get_subagent_config(subagent_type)
    
    # 2. Create executor
    executor = SubagentExecutor(
        config=config,
        tools=runtime.tools,
        thread_data=runtime.state.get("thread_data"),
        sandbox_state=runtime.state.get("sandbox"),
    )
    
    # 3. Start async execution
    executor.execute_async()
    
    # 4. Poll for completion (every 5 seconds)
    writer = get_stream_writer()
    while not executor.is_done():
        time.sleep(5)
        writer.write({"status": "running", "description": description})
    
    # 5. Return result
    result = executor.get_result()
    return result.content
```

### 11.3 Progress Streaming

During execution, the task tool streams progress events:

```python
writer = get_stream_writer()
while not executor.is_done():
    time.sleep(5)
    writer.write({
        "status": "running",
        "description": description,
        "elapsed": elapsed_seconds,
    })
```

These events are visible in the frontend as sub-agent progress indicators.

---

## 12. Sub-Agent Types & Configuration

### 12.1 General-Purpose Sub-Agent

```python
GENERAL_PURPOSE_CONFIG = SubagentConfig(
    name="general-purpose",
    description="A capable agent for complex, multi-step tasks...",
    system_prompt="""You are a general-purpose subagent working on a delegated task.
    
    <guidelines>
    - Focus on completing the delegated task efficiently
    - Use available tools as needed
    - Think step by step but act decisively
    - If you encounter issues, explain them clearly
    - Return a concise summary of what you accomplished
    - Do NOT ask for clarification
    </guidelines>
    
    <output_format>
    1. Brief summary of what was accomplished
    2. Key findings or results
    3. Relevant file paths, data, or artifacts
    4. Issues encountered (if any)
    5. Citations: Use [citation:Title](URL) format
    </output_format>
    """,
    tools=None,  # Inherit all tools from parent
    disallowed_tools=["task", "ask_clarification", "present_files"],
    model="inherit",
    max_turns=50,
)
```

### 12.2 Bash Sub-Agent

```python
BASH_CONFIG = SubagentConfig(
    name="bash",
    description="Command execution specialist...",
    system_prompt="...",
    tools=["bash"],  # Only bash tool
    disallowed_tools=["task", "ask_clarification", "present_files"],
    model="inherit",
    max_turns=20,
)
```

### 12.3 Disallowed Tools

Sub-agents are explicitly prevented from using:
- **`task`**: Prevents sub-agent nesting (no recursive delegation)
- **`ask_clarification`**: Sub-agents cannot ask users for input
- **`present_files`**: Only the lead agent presents files to users

### 12.4 Model Inheritance

When `model="inherit"`, the sub-agent uses the same model as the lead agent. This can be overridden per-subagent type in `config.yaml`.

---

## 13. Concurrency Control

### 13.1 Three Layers of Control

```
Layer 1: Prompt Instructions
    "MAXIMUM {n} task calls per response"
    "Count sub-tasks in thinking"
    "Plan batches if > {n}"
    │
Layer 2: SubagentLimitMiddleware (after_model)
    Silently truncates excess task calls
    │
Layer 3: ThreadPoolExecutor (8 workers)
    Physical limit on concurrent executions
```

### 13.2 SubagentLimitMiddleware

```python
class SubagentLimitMiddleware:
    def __init__(self, max_concurrent=3):
        self.max_concurrent = max_concurrent
    
    def after_model(self, state, runtime):
        last_msg = state["messages"][-1]
        task_calls = [tc for tc in last_msg.tool_calls if tc["name"] == "task"]
        
        if len(task_calls) > self.max_concurrent:
            # Keep first max_concurrent calls, discard rest
            kept = last_msg.tool_calls[:self.max_concurrent]
            return {"messages": [last_msg.model_copy(update={"tool_calls": kept})]}
```

### 13.3 Multi-Batch Execution Pattern

For tasks requiring more than N sub-tasks:

```
Turn 1: Agent identifies 6 sub-tasks, launches first 3
    → task("Research A"), task("Research B"), task("Research C")
    → Wait for all 3 to complete
    → Results returned as ToolMessages

Turn 2: Agent launches remaining 3
    → task("Research D"), task("Research E"), task("Research F")
    → Wait for all 3 to complete
    → Results returned as ToolMessages

Turn 3: Agent synthesizes ALL 6 results into final response
```

---

## 14. Context Isolation Strategy

### 14.1 What Sub-Agents See

| Context Element | Lead Agent | Sub-Agent |
|----------------|-----------|-----------|
| System prompt | Full DeerFlow prompt | Minimal subagent prompt |
| Conversation history | Full thread history | Only task prompt |
| Memory | Long-term memory | None |
| Skills | All enabled skills | None |
| Tools | All tools + task | All tools - task/clarify/present |
| Sandbox | Shared | Shared (same thread) |
| Thread data | Full | Shared (same paths) |
| Deferred tools | Via tool_search | Direct access |

### 14.2 Why Isolation?

1. **Token efficiency**: Sub-agents don't waste tokens on irrelevant conversation history
2. **Focus**: Sub-agents only see their specific task, reducing distraction
3. **Security**: Sub-agents can't access or leak the lead agent's full context
4. **Simplicity**: Each sub-agent is a clean, focused execution unit

### 14.3 Shared Resources

Despite context isolation, sub-agents share:
- **Filesystem**: Same sandbox, same thread directories
- **Sandbox state**: Same sandbox_id (reused, not recreated)
- **Thread data**: Same workspace/uploads/outputs paths

This allows sub-agents to:
- Read files created by other sub-agents
- Write to the shared workspace
- Access uploaded files

---

## 15. Prompt Design Patterns & Principles

### 15.1 Pattern: XML-Tagged Sections

Every major section uses XML tags:
```xml
<role>...</role>
<memory>...</memory>
<skill_system>...</skill_system>
```

**Why**: LLMs parse XML tags reliably, providing clear semantic boundaries that prevent section bleed.

### 15.2 Pattern: Positive + Negative Examples

The prompt uses both ✅ and ❌ examples:
```
✅ USE subagents when: Complex research, multi-aspect analysis
❌ DO NOT use subagents when: Single file read, sequential dependencies
```

**Why**: Negative examples are as important as positive ones for preventing misuse.

### 15.3 Pattern: Repeated Emphasis

Critical rules appear in multiple sections:
- Clarification-first: In `<thinking_style>`, `<clarification_system>`, and `<critical_reminders>`
- Concurrency limit: In `<thinking_style>`, `<subagent_system>`, and `<critical_reminders>`

**Why**: LLMs may not attend to every section equally. Repetition increases compliance.

### 15.4 Pattern: Concrete Examples with Code

The prompt includes actual code examples:
```python
ask_clarification(
    question="Which environment should I deploy to?",
    clarification_type="approach_choice",
    options=["development", "staging", "production"]
)
```

**Why**: Concrete examples are more effective than abstract instructions.

### 15.5 Pattern: Progressive Disclosure

Skills and tools are listed by name only, with full content loaded on demand:
```xml
<skill>
    <name>Deep Research</name>
    <description>Conduct thorough research</description>
    <location>/mnt/skills/public/deep-research/SKILL.md</location>
</skill>
```

**Why**: Keeps the initial prompt lean while maintaining full capability access.

### 15.6 Pattern: Behavioral Guardrails

The prompt includes explicit behavioral constraints:
```
- CRITICAL: After thinking, you MUST provide your actual response
- NEVER write your full final answer in thinking process
- Always Respond: Your thinking is internal. You MUST always provide a visible response
```

**Why**: Prevents common LLM failure modes (thinking without responding, putting answers in thinking blocks).

---

## 16. Summary

### Prompt Engineering
- **Dynamic assembly**: 12+ conditional sections assembled at agent creation time
- **XML-tagged structure**: Clear semantic boundaries for reliable LLM parsing
- **Behavioral enforcement**: Critical rules repeated across multiple sections
- **Progressive disclosure**: Skills and tools listed by name, loaded on demand
- **Concrete examples**: Code examples for every tool and workflow

### Multi-Agent Orchestration
- **Hierarchical design**: Lead agent orchestrates, sub-agents execute
- **Context isolation**: Sub-agents start fresh with only their task
- **Shared filesystem**: Sub-agents can collaborate through files
- **Concurrency control**: Three layers (prompt, middleware, thread pool)
- **Nesting prevention**: Sub-agents cannot spawn sub-agents
- **Timeout enforcement**: Two-pool architecture with configurable timeouts

### Key Design Decisions
1. **Clarification before action**: Strict CLARIFY → PLAN → ACT workflow prevents wasted work
2. **Subagent decomposition**: Complex tasks are parallelized, not serialized
3. **Hard concurrency limits**: Prevents resource exhaustion and ensures predictable behavior
4. **Minimal sub-agent prompts**: Sub-agents get focused instructions, not the full system prompt
5. **Shared sandbox**: Sub-agents can read/write the same filesystem for collaboration
