# Kimi Code CLI: ACP, IDE Integration, and VS Code Relationship

## 1. Executive Summary

Kimi Code CLI integrates with IDEs primarily through **ACP (Agent Client Protocol)** rather than by embedding editor-specific logic deep into the core runtime.

The repository clearly contains:

- a multi-session ACP server
- a deprecated single-session ACP compatibility path
- ACP session management
- ACP content conversion
- ACP-backed filesystem and terminal abstractions
- IDE integration documentation for tools such as Zed and JetBrains

What the repository does **not clearly contain as a main first-class codebase subtree** is the full implementation of the VS Code extension itself.

So the correct interpretation is:

- this repo contains the **agent backend/protocol side** of IDE integration
- the VS Code extension is likely a separate client product or distribution artifact

## 2. Why ACP Matters in This Architecture

ACP is not an optional add-on. It is the architectural layer that lets Kimi Code CLI operate as an editor/IDE agent backend.

Without ACP, Kimi Code CLI would still be a capable terminal agent.

With ACP, it becomes:

- an IDE-attachable agent runtime
- a multi-session backend
- a protocol service that can expose tool calls, planning, permissions, and streamed responses to external clients

This is one of the main reasons the project is more accurately described as an **agent runtime platform** than a simple CLI chatbot.

## 3. Two ACP Server Modes Exist

The repository contains two ACP-related entry paths.

## 3.1 Multi-session ACP server

Implemented through:

- `src/kimi_cli/acp/__init__.py`
- `src/kimi_cli/acp/server.py`

Entry:

- `acp_main()` starts `ACPServer()` over stdio using the ACP library

This is the main and current protocol server for IDE integration.

## 3.2 Deprecated single-session ACP server

Implemented through:

- `src/kimi_cli/ui/acp/__init__.py`

This path now intentionally rejects requests and tells clients to use:

- `kimi acp`

That is important because it shows the project moved from a simpler compatibility mode to a cleaner multi-session ACP design.

## 4. What `kimi acp` Actually Does

The command `kimi acp` starts a multi-session ACP server.

According to docs and implementation, it is used for:

- IDE plugin integration
- custom ACP client development
- multi-session concurrent handling

The server is run over stdio, which is a common design for local IDE protocol integrations.

## 5. ACP Session Lifecycle

The ACP server in `src/kimi_cli/acp/server.py` manages sessions explicitly.

Important lifecycle operations include:

- initialize
- new session
- load session
- resume session
- list sessions
- prompt
- cancel

This matters because editor integrations need more than a one-shot prompt API. They need durable sessions associated with workspaces.

## 6. Authentication Model

The ACP server checks authentication before creating or loading sessions.

If authentication is missing, the server returns `AUTH_REQUIRED` with a terminal-based login method description.

That means the protocol is designed not only for content exchange but also for onboarding/auth state handling.

This is especially important for external clients that need to guide the user through login without embedding Kimi-specific auth logic everywhere.

## 7. Capabilities Advertised to ACP Clients

During initialization, the server advertises capabilities such as:

- session loading/listing support
- prompt capabilities
- MCP capabilities
- session capabilities
- authentication methods

This is important because client behavior can be adapted to server features rather than hardcoded assumptions.

## 8. ACP Prompt Flow

The main ACP prompt path is implemented in `ACPSession.prompt()`.

The flow is:

1. convert ACP prompt blocks to runtime content parts
2. start a turn state
3. run the underlying `KimiCLI.run(...)`
4. iterate over streamed `Wire` messages
5. translate each wire message to ACP session updates
6. return a final ACP stop reason

This is the key architectural boundary:

- core runtime emits `Wire`
- ACP layer interprets `Wire`
- IDE sees ACP-native updates

That is a clean protocol translation design.

## 9. Prompt Block Conversion

`src/kimi_cli/acp/convert.py` converts ACP prompt blocks into internal content parts.

Supported inbound block types include:

- text blocks
- image blocks
- embedded text resources
- resource link references

Important implication:

- ACP clients can provide richer prompt material than plain text
- the runtime can still normalize that material into the same internal `ContentPart` model

This keeps the soul loop independent from ACP-specific payload shapes.

## 10. Outbound Streaming Model for IDEs

The ACP session layer maps runtime outputs to ACP updates.

Key mappings include:

- `TextPart` → `AgentMessageChunk`
- `ThinkPart` → `AgentThoughtChunk`
- `ToolCall` → `ToolCallStart`
- `ToolCallPart` → `ToolCallProgress`
- `ToolResult` → `ToolCallProgress` with completed/failed state
- todo/plan display blocks → `AgentPlanUpdate`

This is a very strong protocol story because it preserves semantic structure across the boundary.

## 11. Why This Is Better Than Plain Text Streaming

If the ACP layer only streamed raw text, IDE clients would lose important semantics.

By preserving structured updates, the client can:

- show live assistant text
- display reasoning/thinking separately
- visualize tool calls as real operations
- render file edits as diffs
- present plan updates as structured plan entries
- manage permissions cleanly

This is the difference between a protocol-level agent integration and a glorified terminal mirror.

## 12. Tool Call Translation in ACP

Tool calls are not just forwarded as opaque strings.

The ACP layer maintains per-tool-call state with:

- accumulated arguments
- streaming JSON lexer
- dynamically derived human-readable title

This lets IDEs show meaningful tool-call progress such as:

- `ReadFile: src/foo.py`
- `Grep: plan_mode`
- `Shell: pytest`

This is a very good UX/detail-oriented design.

## 13. Why Streaming JSON Parsing Exists Here

Tool arguments may be streamed incrementally by the model/runtime.

The ACP layer uses `streamingjson.Lexer` to track partial JSON arguments while they are still arriving.

This enables:

- incremental title generation
- live updating of tool-call displays
- partial introspection before the full call finishes

That is an advanced and thoughtful piece of the integration layer.

## 14. Diff Rendering for IDE Clients

Diff display blocks are converted to ACP file-edit content with:

- path
- old text
- new text

This means IDE clients can render edits as structured file changes rather than only as terminal-style text.

That is essential for code editor integrations.

## 15. Plan Updates for IDE Clients

Todo/plan display blocks are converted into ACP `AgentPlanUpdate` objects.

This is important because planning output becomes a first-class artifact in IDE UIs rather than just markdown text in the chat stream.

For a development agent, that is a strong capability.

## 16. Permission Flow in ACP

Approval requests are bridged into ACP permission requests.

The client receives options such as:

- approve once
- approve for session
- reject

This is a very strong fit for IDEs because it supports a native approval UX rather than forcing all approval through free-form text.

## 17. ACP-backed Filesystem Access

One of the most interesting parts of the IDE integration is `ACPKaos` in `src/kimi_cli/acp/kaos.py`.

This class is a KAOS backend that routes supported operations through ACP client capabilities.

Important supported operations include:

- reading text files via ACP client FS APIs
- writing text files via ACP client FS APIs
- terminal execution when supported

This is a powerful abstraction because it lets the same higher-level tools (`ReadFile`, `WriteFile`, etc.) operate through the IDE/client when available.

## 18. Why `ACPKaos` Is Architecturally Important

`ACPKaos` means the core runtime does **not** need editor-specific code in every tool.

Instead:

- tools call KAOS abstractions
- ACP mode swaps in an ACP-backed KAOS implementation
- filesystem operations are routed through the IDE client when possible

This is excellent architecture. It avoids polluting business logic with transport-specific branching.

## 19. ACP-backed Terminal Integration

Terminal support is also capability-sensitive.

If the ACP client advertises terminal capability, the normal `Shell` tool is replaced with an ACP `Terminal` tool.

That ACP terminal tool:

- requests approval
- creates a terminal via ACP
- streams terminal content through ACP protocol structures
- waits for completion or timeout
- releases the terminal handle

This means the shell tool becomes IDE-native when possible.

Again, that is a strong example of **backend/runtime reuse with frontend-specific adaptation**.

## 20. Tool Replacement Strategy

`replace_tools(...)` in `src/kimi_cli/acp/tools.py` adapts the runtime toolset based on client capabilities.

This is a subtle but powerful extensibility pattern:

- keep tool names and schemas stable
- change execution backend depending on environment

This preserves model-facing consistency while improving frontend integration quality.

## 21. What About VS Code Specifically?

The README explicitly references a **Kimi Code VS Code Extension**.

However, from the repository structure and files inspected, the main facts are:

- there is strong ACP/IDE backend support here
- there are docs for IDE usage generally
- there is no clearly obvious full VS Code extension source tree as a primary repository module

So the safest and most accurate statement is:

- the repository supports VS Code integration conceptually and operationally through ACP/backend facilities
- but the extension host/client implementation likely lives separately or is not the core artifact in this repo

## 22. Why This Distinction Matters

If one says “the VS Code plugin is in this repo,” that overstates the evidence.

If one says “this repo has nothing to do with VS Code integration,” that understates the evidence.

The precise truth is:

- **this repo provides the integration backend and protocol machinery**
- **the editor-specific client/extension layer is likely external or separately packaged**

That is the correct architectural reading.

## 23. What a VS Code Extension Would Likely Need to Do

A VS Code extension built on this runtime would likely:

1. launch `kimi acp`
2. perform ACP initialization
3. create/load sessions per workspace
4. send prompt content blocks
5. receive streamed message/thought/tool updates
6. display diffs, plans, approvals, and terminal actions in the editor UI
7. expose filesystem and terminal capabilities to the backend when supported

Notably, the extension would not need to re-implement the agent loop. It would mainly be:

- UI layer
- session manager
- ACP transport client
- capability provider

That is a very maintainable split of responsibilities.

## 24. IDEs Explicitly Documented in This Repository

The docs explicitly describe usage in:

- Zed
- JetBrains IDEs

Both use ACP by launching:

- `kimi acp`

This is strong evidence that ACP is the intended general IDE integration path.

## 25. Multi-session Design Is a Big Deal

The multi-session ACP design is especially important for IDE use because it supports:

- multiple workspaces or threads
- persistent session loading/resumption
- better editor lifecycle alignment

This is much better than forcing one CLI process to act like a single ephemeral chat.

## 26. Current Limitations / Gaps

From the code and notes available, current limitations include:

- the old single-session ACP server path is deprecated
- some ACP methods such as mode/model switching are not fully implemented as rich runtime features
- question support may not be available in all clients
- some ACP client ecosystems may not exercise full session lifecycle support yet

These are normal maturity-stage constraints, not architectural flaws.

## 27. Architectural Strengths of the IDE Integration Design

The strongest design choices are:

- protocol-first integration via ACP
- clean separation between runtime and frontend
- structured streaming instead of plain text mirroring
- ACP-backed KAOS abstraction for filesystem access
- capability-driven terminal tool replacement
- session-aware multi-turn, multi-session design
- authentication surfaced through protocol-compatible flows

These choices make the project extensible across multiple IDEs.

## 28. Core Architectural Insight

The most important insight is this:

Kimi Code CLI does not integrate with IDEs by bolting an editor plugin directly onto a terminal app.

Instead, it creates a **protocol boundary** where:

- the soul loop remains editor-agnostic
- runtime tools remain largely editor-agnostic
- ACP bridges structured agent behavior into editor-native interactions

That is the right long-term architecture.

## 29. Final Summary

Kimi Code CLI’s IDE story is primarily an **ACP backend architecture**.

This repository clearly contains:

- ACP servers
- ACP session management
- prompt/result conversion
- ACP filesystem/terminal support
- IDE integration docs

What it does not clearly contain as the main code artifact is the full VS Code extension implementation itself.

So the right conclusion is:

- **IDE integration is a first-class capability of this repository**
- **VS Code integration is supported via this backend/protocol architecture**
- **the actual VS Code extension client likely lives elsewhere or is packaged separately**

This is a strong and well-factored design.
