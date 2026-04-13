# Kimi Code CLI: SDK, Workspace Dependencies, and External Interfaces

## 1. Executive Summary

Kimi Code CLI is not a standalone monolith. It sits on top of a small internal ecosystem of workspace packages and protocol integrations.

The most important supporting components are:

- `kosong`
  - LLM abstraction layer
- `pykaos` / `kaos`
  - filesystem / process / environment abstraction layer
- `fastmcp`
  - MCP client integration
- `kimi-sdk`
  - a lightweight API SDK that exists in the workspace, but is not obviously the primary runtime backbone of the CLI

Understanding these boundaries is essential because many of Kimi CLI’s capabilities are intentionally delegated to these lower-level packages.

## 2. Workspace Structure Relevant to External Interfaces

The root `pyproject.toml` declares a workspace with members including:

- `packages/kosong`
- `packages/kaos`
- `packages/kimi-code`
- `sdks/kimi-sdk`

This is important because the CLI is architected as part of a workspace ecosystem rather than a single-package project.

## 3. `kosong`: The Real LLM Runtime Abstraction

The most important dependency in the entire project is `kosong`.

Its own metadata describes it as:

- “The LLM abstraction layer for modern AI agent applications.”

This is not just a utility dependency. It is the main abstraction that Kimi CLI relies on for:

- message structures
- chat-provider interfaces
- step execution
- tooling abstractions
- content parts
- tool result types
- MCP content conversion

## 4. What Kimi CLI Delegates to `kosong`

From the code examined, Kimi CLI depends on `kosong` for several core concepts.

## 4.1 Chat providers

`llm.py` creates provider-specific `ChatProvider` instances through `kosong` provider implementations.

Examples include support for:

- Kimi
- OpenAI legacy
- OpenAI responses
- Anthropic
- Gemini / Google GenAI
- Vertex AI
- echo/testing providers

## 4.2 Agent step execution

The core loop calls:

- `kosong.step(...)`

This is the heart of inference + tool orchestration.

So Kimi CLI does **not** implement raw chat-completion orchestration entirely by itself. It delegates the step abstraction to `kosong`.

## 4.3 Message and content-part types

Important types such as:

- `Message`
- `TextPart`
- `ThinkPart`
- image/video URL parts
- `ToolCall`
- `ToolResult`

are either directly from `kosong` or wrapped closely around its model.

## 4.4 Tooling model

Kimi CLI’s tool classes are built on `kosong.tooling` abstractions such as:

- `CallableTool`
- `CallableTool2`
- `ToolReturnValue`
- `ToolError`
- `ToolOk`
- `DisplayBlock`

This means Kimi CLI’s built-in tools are really specializations inside a larger tool runtime framework.

## 5. Why `kosong` Matters Architecturally

`kosong` is what allows Kimi CLI to stay:

- provider-agnostic
- structured-output aware
- tool-call aware
- multimodal-capable
- stream-friendly

Without `kosong`, Kimi CLI would likely have much more provider-specific and protocol-specific code tangled inside the core loop.

So if one asks “what is the actual LLM SDK/runtime interface for Kimi CLI?”, the best answer is:

- **primarily `kosong`**

## 6. `kaos` / `pykaos`: The Environment Abstraction Layer

The second most important foundational dependency is `pykaos` / `kaos`.

This package provides a filesystem / process / OS abstraction layer.

Its package metadata shows it is a separate workspace dependency with async filesystem and SSH-oriented capabilities.

## 6.1 What Kimi CLI Delegates to `kaos`

Kimi CLI uses `KaosPath` and KAOS operations for:

- path normalization/canonicalization
- file reads/writes/appends
- directory iteration
- globbing
- process/environment operations
- working-directory changes

This is important because the CLI does not bind itself purely to direct local `pathlib` and `subprocess` usage.

## 6.2 Why that matters

Because of KAOS, the same higher-level tools can potentially operate across different backends, including:

- local execution
- ACP-backed filesystem execution
- potentially remote/SSH-oriented environments depending on KAOS usage

That is a major source of portability and integration flexibility.

## 7. `ACPKaos` as Proof of the KAOS Abstraction Strategy

`src/kimi_cli/acp/kaos.py` is one of the clearest demonstrations of how important KAOS is to the architecture.

`ACPKaos` swaps filesystem behavior so that supported operations route through an ACP client instead of directly to local disk.

This means tools like:

- `ReadFile`
- `WriteFile`
- related file operations

can keep using KAOS abstractions while the backend changes underneath them.

This is excellent layering.

## 8. `fastmcp`: External Tool and Protocol Extension Layer

The third major external interface dependency is `fastmcp`.

Kimi CLI uses `fastmcp` for:

- MCP config parsing/validation
- MCP client lifecycle
- remote MCP server connection
- MCP tool discovery
- MCP tool invocation

This is how Kimi CLI extends its native tool inventory with tools from external MCP servers.

## 9. What Kimi CLI Delegates to `fastmcp`

The project relies on `fastmcp` for:

- representing MCP server configs
- connecting to MCP servers
- listing tools exposed by those servers
- calling MCP tools
- handling remote auth modes such as OAuth-backed MCP servers

At runtime, the CLI wraps discovered MCP tools into local callable tool objects (`MCPTool`) so the rest of the agent loop can treat them like ordinary tools.

## 10. Why the MCP Layer Is Important

MCP is the main external extension mechanism for tool capabilities.

That means the project’s tool surface is not limited to built-in Python code. It can be expanded dynamically by connecting to external tool providers.

This is a major architectural choice because it shifts extensibility from:

- only local plugin classes

to:

- protocol-mediated tool ecosystems

## 11. `kimi-sdk`: Present but Not the Main Runtime Backbone

The workspace also contains:

- `sdks/kimi-sdk`

Its package metadata describes it as:

- “A lightweight Python SDK for the Kimi API.”

It depends on `kosong>=0.37.0`.

That relationship is interesting because it suggests `kimi-sdk` itself is built on top of `kosong`, not the other way around.

## 11.1 What we can safely infer

From the examined files, `kimi-sdk` exists in the workspace, but it is **not obviously the central runtime dependency used by the CLI’s core loop**.

The CLI’s core loop and provider creation path are much more directly tied to:

- `kosong`
- `kaos`
- `fastmcp`

So the clean architectural reading is:

- `kimi-sdk` is part of the broader ecosystem
- but the CLI runtime itself is primarily built on the lower-level abstractions above

## 12. Programmatic API Surface of the CLI Itself

The main programmatic entrypoint for the CLI runtime is not the SDK package. It is `KimiCLI` in `src/kimi_cli/app.py`.

Important interfaces include:

- `KimiCLI.create(...)`
- `KimiCLI.run(...)`
- `KimiCLI.run_shell(...)`
- `KimiCLI.run_print(...)`
- `KimiCLI.run_acp(...)`
- `KimiCLI.run_wire_stdio(...)`

So if an internal consumer wants to drive the agent runtime directly, `KimiCLI` is the main facade.

## 13. Internal Interface Layers

The project can be understood as a stack of interfaces.

## 13.1 Configuration interface

- `Config`
- `LLMProvider`
- `LLMModel`
- `LoopControl`

## 13.2 Runtime interface

- `Runtime`
- `Agent`
- `Session`
- `Context`

## 13.3 LLM/tool interface

- `LLM`
- `ChatProvider`
- `kosong.step(...)`
- `KimiToolset`
- built-in tool classes
- MCPTool wrappers

## 13.4 Event/protocol interface

- `WireMessage`
- ACP server/session interfaces
- web/wire/print/shell frontends

These layers are relatively well separated.

## 14. Why the Dependency Split Is Good Design

The split between workspace packages gives the project several architectural benefits.

- `kosong` keeps provider and tool orchestration generic
- `kaos` keeps environment and filesystem interaction abstract
- `fastmcp` keeps MCP protocol concerns separate
- `kimi-cli` focuses on agent behavior, prompting, sessions, and UI/protocol orchestration

This is much cleaner than putting everything into one giant package.

## 15. What Interfaces Are Stable vs Internal?

The repository suggests different levels of interface stability.

## 15.1 More external-facing / likely stable enough to rely on

- CLI commands
- `kimi acp`
- session-oriented runtime behavior
- wire/ACP protocol behavior
- config file shape

## 15.2 More internal-facing

- soul internals
- dynamic injection implementation details
- exact tool binding patterns
- labor market internals
- compaction internals

This distinction matters for anyone wanting to build on top of the system.

## 16. The Most Important External Interface Overall

If forced to choose one most important external interface, it is not the standalone SDK package. It is the combination of:

- `KimiCLI` as programmatic runtime facade
- `Wire` as structured event stream
- ACP as IDE-facing protocol boundary

Those are the interfaces that really let the runtime interact with the outside world.

## 17. Key Architectural Insight

The workspace layout reveals an important design philosophy:

Kimi Code CLI is intentionally built as a **composition layer** over lower-level reusable agent infrastructure.

It specializes in:

- prompts
- sessions
- loop control
- approvals
- context persistence
- tool inventory
- UI/protocol bridging

while delegating general infrastructure downward.

That is a good long-term architecture for maintainability.

## 18. Risks / Trade-offs of This Dependency Model

This approach also has trade-offs.

- Understanding the project requires crossing package boundaries.
- Important behavior may live outside `src/kimi_cli/`.
- Changes in lower-level workspace packages can materially affect CLI behavior.
- Full system comprehension requires reading across the workspace, not only the CLI package.

These are natural costs of modularity.

## 19. What to Deep-Dive Next

After understanding the dependency boundaries, the most valuable final document is a synthesis covering:

- the project’s strongest architectural decisions
- its main technical bottlenecks
- its likely core problems
- what deserves deeper investigation next

That synthesis should combine the findings from all previous documents.

## 20. Final Summary

Kimi Code CLI is best understood as the top layer of a workspace ecosystem.

Its runtime depends most critically on:

- `kosong` for LLM, tool, and content abstractions
- `kaos` for filesystem/process/environment abstraction
- `fastmcp` for MCP-based external tool integration

The `kimi-sdk` package exists in the workspace, but based on the inspected runtime paths, it is not the central backbone of the CLI’s core agent loop.

So the architecture is:

- **workspace-composed**
- **protocol-aware**
- **runtime-layered**

and that is one of the main reasons the project remains extensible across CLI, IDE, MCP, and web contexts.
