# Claude Code — Code Retrieval, Indexing, and Context Building

> **Document 01**: Deep dive into how Claude Code searches, indexes, and retrieves code, and how it decides what to place into the LLM context prompt. Covers the full pipeline from user query to context assembly.

---

## Table of Contents

1. [Overview: No Traditional Index](#1-overview-no-traditional-index)
2. [Code Search Tools](#2-code-search-tools)
3. [File Reading and Content Extraction](#3-file-reading-and-content-extraction)
4. [Context Placement Strategy](#4-context-placement-strategy)
5. [Attachment System — Dynamic Context Injection](#5-attachment-system--dynamic-context-injection)
6. [Tool Result Storage and Budget Management](#6-tool-result-storage-and-budget-management)
7. [Micro-Compaction — Tool Result Compression](#7-micro-compaction--tool-result-compression)
8. [ToolSearch — Deferred Tool Discovery](#8-toolsearch--deferred-tool-discovery)
9. [Skill Discovery and Prefetch](#9-skill-discovery-and-prefetch)
10. [Memory Prefetch](#10-memory-prefetch)
11. [File State Cache](#11-file-state-cache)
12. [Diff Display and Merge](#12-diff-display-and-merge)
13. [Key Technical Decisions](#13-key-technical-decisions)
14. [Summary](#14-summary)

---

## 1. Overview: No Traditional Index

**Claude Code does NOT build a traditional code index** (no AST parsing, no embedding-based vector store, no symbol table). Instead, it relies on:

1. **Runtime search tools** (ripgrep, glob) that the LLM invokes on demand
2. **File reading** with token-aware truncation
3. **Attachment-based context injection** for file changes, memory, and skills
4. **LLM-driven exploration** — the model decides what to search for and read

This is a fundamentally different approach from tools like Cursor or Copilot that pre-index the codebase. Claude Code treats code retrieval as a tool-use problem: the LLM is the "indexer" that decides what's relevant.

### Why This Design?

- **No startup cost**: No index building, no embedding computation
- **Always fresh**: Every search hits the actual filesystem
- **LLM-guided**: The model's reasoning determines what's relevant
- **Scales to any repo**: No memory overhead for large codebases
- **Works with any language**: No language-specific parser needed

### The Trade-off

- **Higher token cost**: Each search/read consumes API tokens
- **Latency**: Each file read is a tool call round-trip
- **No semantic search**: Can't find "similar" code without exact patterns
- **Context window pressure**: Large files must be truncated or paginated

---

## 2. Code Search Tools

### GrepTool — Content Search via ripgrep

**File**: `src/tools/GrepTool/GrepTool.ts` (578 lines)

The primary code search tool, wrapping ripgrep (`rg`) for fast regex-based content search.

#### Input Schema

```typescript
z.strictObject({
  pattern: z.string(),           // Regex pattern to search
  path: z.string().optional(),   // Directory to search (default: CWD)
  glob: z.string().optional(),   // File type filter (e.g., "*.ts")
  output_mode: z.enum(['content', 'files_with_matches', 'count']).optional(),
  '-B': z.number().optional(),   // Lines before match
  '-A': z.number().optional(),   // Lines after match
  '-C': z.number().optional(),   // Context lines
  '-n': z.boolean().optional(),  // Show line numbers (default: true)
  '-i': z.boolean().optional(),  // Case insensitive
  type: z.string().optional(),   // File type (js, py, rust, etc.)
  head_limit: z.number().optional(),  // Limit results (default: 250)
  offset: z.number().optional(),      // Skip first N results
  multiline: z.boolean().optional(),  // Multiline regex mode
})
```

#### Key Implementation Details

1. **Default result cap**: `head_limit` defaults to 250 to prevent context bloat
2. **VCS exclusion**: Automatically excludes `.git`, `.svn`, `.hg`, `.bzr`, `.jj`, `.sl`
3. **Permission-aware**: Respects file read ignore patterns from permission settings
4. **Concurrency-safe**: `isConcurrencySafe() = true` — can run in parallel
5. **Max result size**: 20,000 characters before persistence to disk

#### Output Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `files_with_matches` | File paths only (default) | Finding which files contain a pattern |
| `content` | Matching lines with context | Reading specific code sections |
| `count` | Match counts per file | Understanding pattern distribution |

#### Execution Flow

```
GrepTool.call(input)
  ├── Validate path exists
  ├── Build ripgrep arguments
  │   ├── --glob for file filtering
  │   ├── --type for language filtering
  │   ├── -B/-A/-C for context lines
  │   ├── -n for line numbers
  │   ├── -i for case insensitive
  │   └── Exclude VCS directories
  ├── Execute ripgrep via ripGrep() utility
  ├── Apply head_limit and offset (pagination)
  └── Return { filenames, content, numFiles, appliedLimit }
```

### GlobTool — File Pattern Matching

**File**: `src/tools/GlobTool/GlobTool.ts` (199 lines)

Finds files by name pattern using glob matching.

```typescript
z.strictObject({
  pattern: z.string(),           // Glob pattern (e.g., "**/*.test.ts")
  path: z.string().optional(),   // Directory to search (default: CWD)
})
```

- **Result limit**: 100 files maximum
- **Concurrency-safe**: Can run in parallel
- **Relativized paths**: Results are relative to CWD to save tokens

### Embedded Search Tools (Ant-only)

When `hasEmbeddedSearchTools()` is true (ant-native builds), `bfs` and `ugrep` are embedded in the Bun binary. In this case:
- GlobTool and GrepTool are removed from the tool list
- `find` and `grep` in Bash are aliased to the embedded fast tools
- The model uses Bash directly for search operations

### BashTool — General-Purpose Search

**File**: `src/tools/BashTool/`

The Bash tool can also be used for code search via commands like:
- `find . -name "*.ts" -type f`
- `grep -rn "pattern" src/`
- `wc -l src/**/*.ts`
- `head -n 50 src/main.ts`

The system prompt guides the model to prefer dedicated search tools, but Bash provides a fallback for complex queries.

---

## 3. File Reading and Content Extraction

### FileReadTool — The Primary File Reader

**File**: `src/tools/FileReadTool/FileReadTool.ts` (1184 lines)

The most complex read tool, handling multiple file types with token-aware limits.

#### Input Schema

```typescript
z.strictObject({
  file_path: z.string(),
  offset: z.number().optional(),    // Start line (1-based)
  limit: z.number().optional(),     // Number of lines to read
})
```

#### Supported File Types

| Type | Handling |
|------|----------|
| **Text files** | Read with line numbers, token-limited |
| **Images** (png, jpg, gif, webp) | Converted to base64, resized if needed |
| **PDFs** | Extracted via `readPDF()`, page range support |
| **Notebooks** (.ipynb) | Cell-by-cell extraction via `readNotebook()` |
| **Binary files** | Detected by extension, rejected with helpful message |
| **Device files** | Blocked (`/dev/zero`, `/dev/random`, etc.) |

#### Token-Aware Reading

```typescript
// Default limits from getDefaultFileReadingLimits()
const limits = {
  maxTokens: context.fileReadingLimits?.maxTokens ?? DEFAULT_MAX_TOKENS,
  maxSizeBytes: context.fileReadingLimits?.maxSizeBytes ?? DEFAULT_MAX_SIZE,
}
```

When a file exceeds the token limit:
1. Throws `MaxFileReadTokenExceededError`
2. Error message suggests using `offset` and `limit` parameters
3. Or suggests searching for specific content instead

#### File State Cache Integration

Every file read updates the `readFileState` cache:

```typescript
// After reading a file
context.readFileState.set(cacheKeys.fileContent(filePath), {
  content: fileContent,
  modTime: modificationTime,
  tokenCount: estimatedTokens,
})
```

This cache is used by:
- **Attachment system**: Detects file changes between turns
- **Memory deduplication**: Avoids re-injecting already-read CLAUDE.md files
- **FileEditTool**: Validates file hasn't changed since last read

#### Image Handling

```typescript
// Images are resized to fit within token budget
const resized = await compressImageBufferWithTokenLimit(buffer, tokenBudget)
// Converted to base64 for API
return { type: 'image', source: { type: 'base64', data: base64, media_type } }
```

#### Skill and Memory Discovery on Read

When a file is read, the tool triggers:
1. **Skill directory discovery**: `discoverSkillDirsForPaths()` — finds `.claude/skills/` directories
2. **Conditional skill activation**: `activateConditionalSkillsForPaths()` — activates path-triggered skills
3. **Memory file detection**: `isAutoMemFile()` — detects memory-related files

---

## 4. Context Placement Strategy

### What Goes Into the Context Prompt?

The context prompt is assembled from multiple sources, each placed at a specific position:

```
┌─────────────────────────────────────────────────────────┐
│ System Prompt                                            │
│  ├── Static sections (identity, capabilities, style)    │
│  ├── Dynamic sections (env info, language, MCP)         │
│  └── Tool descriptions (with deferred tool stubs)       │
├─────────────────────────────────────────────────────────┤
│ User Context (prepended to first user message)          │
│  ├── CLAUDE.md content (project + user + enterprise)    │
│  └── Current date                                        │
├─────────────────────────────────────────────────────────┤
│ System Context (appended to system prompt)              │
│  └── Git status snapshot                                 │
├─────────────────────────────────────────────────────────┤
│ Conversation Messages                                    │
│  ├── User messages (with tool results)                  │
│  ├── Assistant messages (with tool calls)               │
│  └── Attachment messages (injected between turns)       │
│       ├── File change diffs                              │
│       ├── Memory attachments                             │
│       ├── Skill discovery results                        │
│       ├── Agent listing deltas                           │
│       ├── Deferred tools deltas                          │
│       ├── MCP instructions deltas                        │
│       ├── IDE selection context                          │
│       ├── Queued command notifications                   │
│       ├── Todo/Task list updates                         │
│       ├── Plan file content                              │
│       ├── LSP diagnostics                                │
│       └── Hook additional context                        │
└─────────────────────────────────────────────────────────┘
```

### Decision Logic: What Gets Included?

The system uses several strategies to decide what enters the context:

#### 1. LLM-Driven Inclusion (Tool Results)

The model decides what to search for and read. Tool results are automatically included in the conversation:

```typescript
// In query.ts — tool results become part of the message history
state = {
  messages: [...messagesForQuery, ...assistantMessages, ...toolResults],
  // ...
}
```

#### 2. Automatic Attachment Injection

**File**: `src/utils/attachments.ts` (3998 lines — the largest utility file!)

Between each tool execution turn, the system injects "attachment messages" containing contextual information the model didn't explicitly request:

```typescript
// In query.ts, after tool execution
for await (const attachment of getAttachmentMessages(
  null,
  updatedToolUseContext,
  null,
  queuedCommandsSnapshot,
  [...messagesForQuery, ...assistantMessages, ...toolResults],
  querySource,
)) {
  yield attachment
  toolResults.push(attachment)
}
```

#### 3. File Change Detection

When a file is edited (via FileEditTool, FileWriteTool, or BashTool), the system generates a diff attachment:

```typescript
// In attachments.ts — generateFileAttachment()
async function generateFileAttachment(
  filePath: string,
  toolUseContext: ToolUseContext,
): Promise<AttachmentMessage | null> {
  // Compare current file content with cached version
  const cached = toolUseContext.readFileState.get(cacheKeys.fileContent(filePath))
  if (!cached) return null
  
  // Generate two-file diff snippet
  const diff = getSnippetForTwoFileDiff(cached.content, currentContent)
  return createAttachmentMessage({ type: 'edited_text_file', diff, filePath })
}
```

#### 4. Memory Prefetch (Async)

**File**: `src/utils/attachments.ts` — `startRelevantMemoryPrefetch()`

At the start of each query, a side-question is fired to find relevant memory files:

```typescript
// In query.ts — fired once per user turn
using pendingMemoryPrefetch = startRelevantMemoryPrefetch(
  state.messages,
  state.toolUseContext,
)

// Later, after tool execution, consume if settled
if (pendingMemoryPrefetch.settledAt !== null) {
  const memoryAttachments = filterDuplicateMemoryAttachments(
    await pendingMemoryPrefetch.promise,
    toolUseContext.readFileState,
  )
  for (const memAttachment of memoryAttachments) {
    yield createAttachmentMessage(memAttachment)
  }
}
```

The prefetch runs concurrently with the model's streaming response, so it's essentially free latency.

#### 5. IDE Selection Context

When connected to an IDE (VS Code, JetBrains), the currently selected code is injected:

```typescript
// IDE selection becomes an attachment
if (ideSelection) {
  attachments.push({
    type: 'ide_selection',
    filePath: ideSelection.filePath,
    content: ideSelection.content,
    startLine: ideSelection.startLine,
    endLine: ideSelection.endLine,
  })
}
```

---

## 5. Attachment System — Dynamic Context Injection

**File**: `src/utils/attachments.ts` (3998 lines)

The attachment system is the primary mechanism for injecting contextual information between turns. It's the largest utility file in the codebase.

### Attachment Types

| Type | Source | Purpose |
|------|--------|---------|
| `edited_text_file` | File change detection | Show diffs of modified files |
| `relevant_memory` | Memory prefetch | Inject relevant CLAUDE.md content |
| `nested_memory` | Directory-level CLAUDE.md | Inject directory-specific rules |
| `skill_discovery` | Skill search prefetch | Suggest relevant skills |
| `skill_listing` | Skill tool commands | List available skills |
| `agent_listing_delta` | Agent definitions | List available agent types |
| `deferred_tools_delta` | ToolSearch system | Announce deferred tools |
| `mcp_instructions_delta` | MCP servers | Inject MCP server instructions |
| `ide_selection` | IDE bridge | Current editor selection |
| `queued_command` | Message queue | Pending user commands |
| `todo_list` | Todo system | Current todo state |
| `task_list` | Task system | Current task state |
| `plan_file` | Plan mode | Current plan content |
| `lsp_diagnostics` | LSP servers | Code diagnostics/errors |
| `hook_additional_context` | Custom hooks | Hook-injected context |
| `max_turns_reached` | Query loop | Turn limit signal |
| `hook_stopped_continuation` | Stop hooks | Hook stop signal |

### Attachment Message Format

Attachments are injected as user-role messages with a special `attachment` field:

```typescript
function createAttachmentMessage(attachment: Attachment): AttachmentMessage {
  return {
    type: 'attachment',
    uuid: randomUUID(),
    timestamp: new Date().toISOString(),
    attachment,
    message: {
      role: 'user',
      content: renderAttachmentContent(attachment),
    },
  }
}
```

### File Change Diff Generation

When a file is modified, the system generates a compact diff:

```typescript
// From FileEditTool/utils.ts
function getSnippetForTwoFileDiff(
  oldContent: string,
  newContent: string,
): string {
  // Uses unified diff format
  // Truncated to fit within token budget
  // Shows context lines around changes
}
```

The diff is wrapped in `<system-reminder>` tags:

```xml
<system-reminder>
File was edited: src/main.ts
--- a/src/main.ts
+++ b/src/main.ts
@@ -10,3 +10,4 @@
 function main() {
+  console.log('hello')
   return 0
 }
</system-reminder>
```

---

## 6. Tool Result Storage and Budget Management

**File**: `src/utils/toolResultStorage.ts` (1041 lines)

### Per-Tool Result Persistence

When a tool result exceeds its `maxResultSizeChars` threshold, it's persisted to disk:

```typescript
// Persistence flow
async function maybePersistLargeToolResult(
  toolResultBlock: ToolResultBlockParam,
  toolName: string,
  persistenceThreshold: number,
): Promise<ToolResultBlockParam> {
  const size = contentSize(content)
  if (size <= threshold) return toolResultBlock  // Small enough, keep inline
  
  // Persist to disk: ~/.claude/projects/<project>/<session>/tool-results/<id>.txt
  const result = await persistToolResult(content, toolUseId)
  
  // Replace with preview + file path reference
  return {
    ...toolResultBlock,
    content: `<persisted-output>
Output too large (${size}). Full output saved to: ${filepath}
Preview (first 2KB):
${preview}
...
</persisted-output>`
  }
}
```

### Per-Tool Thresholds

| Tool | maxResultSizeChars | Notes |
|------|-------------------|-------|
| GrepTool | 20,000 | Capped grep output |
| GlobTool | 100,000 | File lists can be large |
| FileEditTool | 100,000 | Edit results with diffs |
| FileReadTool | Infinity | Never persisted (self-bounds via maxTokens) |
| BashTool | 50,000 (default) | Shell output |

### Per-Message Aggregate Budget

Beyond per-tool limits, there's a per-message aggregate budget that limits the total size of all tool results in a single API-level user message:

```typescript
// enforceToolResultBudget() in toolResultStorage.ts
// Walks messages grouped by API-level user message
// For each group exceeding the budget:
//   1. Re-apply previously cached replacements (byte-identical)
//   2. Freeze previously-seen unreplaced results
//   3. Persist largest fresh results to disk
```

Key design principles:
- **Stable decisions**: Once a result is seen, its fate is frozen (preserves prompt cache)
- **Largest-first**: The biggest fresh results are persisted first
- **Re-apply cached**: Previously persisted results use the exact same preview string
- **Skip Read tool**: FileReadTool results are never budget-persisted (it self-bounds)

---

## 7. Micro-Compaction — Tool Result Compression

**File**: `src/services/compact/microCompact.ts` (531 lines)

Micro-compaction is a lightweight context compression that targets old tool results without requiring a full conversation summary.

### Three Micro-Compaction Strategies

#### 1. Time-Based Micro-Compaction

When the gap since the last assistant message exceeds a threshold (cache has expired):

```typescript
function maybeTimeBasedMicrocompact(messages, querySource): MicrocompactResult | null {
  const gapMinutes = (Date.now() - lastAssistantTimestamp) / 60_000
  if (gapMinutes < config.gapThresholdMinutes) return null
  
  // Clear all but the most recent N compactable tool results
  const keepSet = new Set(compactableIds.slice(-keepRecent))
  // Replace old results with: "[Old tool result content cleared]"
}
```

#### 2. Cached Micro-Compaction (Ant-only)

Uses the API's `cache_edits` feature to delete tool results from the server-side cache without modifying local messages:

```typescript
async function cachedMicrocompactPath(messages, querySource): Promise<MicrocompactResult> {
  // Register tool results in state
  // Determine which to delete based on count threshold
  // Create cache_edits block for API layer
  // Local messages remain unchanged — cache_reference handles it
}
```

This is the most efficient approach because:
- No local message mutation
- Server-side cache editing preserves the cached prefix
- Only the deleted tool results are re-tokenized

#### 3. Legacy Micro-Compaction (Removed)

Previously replaced old tool results with `[Old tool result content cleared]` based on token count thresholds. Now removed in favor of cached MC.

### Compactable Tools

Only specific tools' results are eligible for micro-compaction:

```typescript
const COMPACTABLE_TOOLS = new Set([
  FILE_READ_TOOL_NAME,    // File reads become stale
  ...SHELL_TOOL_NAMES,    // Bash output is ephemeral
  GREP_TOOL_NAME,         // Search results are reference-only
  GLOB_TOOL_NAME,         // File lists are reference-only
  WEB_SEARCH_TOOL_NAME,   // Web results are reference-only
  WEB_FETCH_TOOL_NAME,    // Fetched content is reference-only
  FILE_EDIT_TOOL_NAME,    // Edit confirmations are low-value
  FILE_WRITE_TOOL_NAME,   // Write confirmations are low-value
])
```

---

## 8. ToolSearch — Deferred Tool Discovery

**File**: `src/tools/ToolSearchTool/prompt.ts` (122 lines)

When the tool count exceeds a threshold, less-used tools are "deferred" — their full schemas are not sent in the initial prompt. Instead, only their names are listed, and the model must use ToolSearch to fetch their schemas before calling them.

### Deferral Rules

```typescript
function isDeferredTool(tool: Tool): boolean {
  if (tool.alwaysLoad === true) return false    // Explicit opt-out
  if (tool.isMcp === true) return true           // MCP tools always deferred
  if (tool.name === TOOL_SEARCH_TOOL_NAME) return false  // Never defer itself
  if (tool.name === AGENT_TOOL_NAME && isForkSubagentEnabled()) return false
  return tool.shouldDefer === true
}
```

### ToolSearch Query Forms

The model can search for tools using three query forms:

1. **Exact select**: `"select:Read,Edit,Grep"` — fetch specific tools by name
2. **Keyword search**: `"notebook jupyter"` — fuzzy keyword matching
3. **Name-required search**: `"+slack send"` — require "slack" in name, rank by remaining terms

### Deferred Tools Delta

When `isDeferredToolsDeltaEnabled()` is true, deferred tool names are announced via `<system-reminder>` attachment messages instead of being embedded in the system prompt. This keeps the system prompt stable for cache efficiency.

---

## 9. Skill Discovery and Prefetch

**File**: `src/services/skillSearch/prefetch.ts`

Skills are discovered and prefetched asynchronously:

```typescript
// In query.ts — per-iteration prefetch
const pendingSkillPrefetch = skillPrefetch?.startSkillDiscoveryPrefetch(
  null,
  messages,
  toolUseContext,
)

// After tool execution, consume results
if (pendingSkillPrefetch) {
  const skillAttachments = await skillPrefetch.collectSkillDiscoveryPrefetch(
    pendingSkillPrefetch,
  )
  for (const att of skillAttachments) {
    yield createAttachmentMessage(att)
  }
}
```

Skills are discovered from:
1. **Bundled skills**: Built into the binary
2. **Project skills**: `.claude/skills/` directories
3. **User skills**: `~/.claude/skills/`
4. **MCP skills**: Skills from connected MCP servers
5. **Plugin skills**: Skills from loaded plugins

---

## 10. Memory Prefetch

The memory prefetch system runs a "side question" to find relevant memory files:

```typescript
function startRelevantMemoryPrefetch(
  messages: Message[],
  toolUseContext: ToolUseContext,
): MemoryPrefetch {
  // Fire a lightweight LLM query to find relevant memories
  // Runs concurrently with the main model's streaming response
  // Returns memory file contents as attachments
}
```

Memory sources:
- `~/.claude/CLAUDE.md` — User-level memory
- `<project>/CLAUDE.md` — Project-level memory
- `<project>/<dir>/CLAUDE.md` — Directory-level memory
- `~/.claude/memory/` — Memory directory (memdir)

### Deduplication

Memory attachments are deduplicated against the `readFileState` cache:

```typescript
function filterDuplicateMemoryAttachments(
  attachments: Attachment[],
  readFileState: FileStateCache,
): Attachment[] {
  // Skip memories for files already read this session
  return attachments.filter(att => !readFileState.has(att.filePath))
}
```

---

## 11. File State Cache

**File**: `src/utils/fileStateCache.ts`

The file state cache is an LRU cache that tracks file contents and metadata across turns:

```typescript
type FileStateCache = {
  get(key: string): CachedFileState | undefined
  set(key: string, value: CachedFileState): void
  has(key: string): boolean
  clear(): void
}

// Cache keys
const cacheKeys = {
  fileContent: (path: string) => `file:${path}`,
  fileModTime: (path: string) => `modtime:${path}`,
}
```

### Uses

1. **File change detection**: Compare cached vs. current content for diff generation
2. **Edit validation**: FileEditTool checks if file was modified externally
3. **Memory deduplication**: Avoid re-injecting already-read CLAUDE.md files
4. **Token estimation**: Cached token counts for context budget calculations

### Lifecycle

- Created per session (main thread) or per agent (sub-agents)
- Sub-agents can clone the parent's cache (`cloneFileStateCache`)
- Cleared on agent cleanup to free memory

---

## 12. Diff Display and Merge

### FileEditTool — String Replacement Based Editing

**File**: `src/tools/FileEditTool/FileEditTool.ts` (626 lines)

Claude Code uses a **string replacement** approach for file editing, not line-based diffs:

```typescript
// Input schema
z.strictObject({
  file_path: z.string(),
  old_string: z.string(),    // Exact text to find
  new_string: z.string(),    // Replacement text
  replace_all: z.boolean().optional(),  // Replace all occurrences
})
```

#### Edit Validation Pipeline

```
1. Check old_string !== new_string
2. Check file isn't denied by permissions
3. Check file size < 1 GiB
4. Read current file content
5. Verify file hasn't been modified since last read (readFileState check)
6. Find old_string in file content
7. If not found: try fuzzy matching (findActualString)
8. Apply replacement
9. Generate diff for display
10. Track in file history
11. Notify IDE of change
```

#### Fuzzy String Matching

When `old_string` isn't found exactly, `findActualString()` tries:
- Whitespace normalization
- Quote style preservation (`preserveQuoteStyle()`)
- Partial matching with context

#### Diff Generation

```typescript
// Generate unified diff for display
const diff = fetchSingleFileGitDiff(filePath)
// Or compute in-memory diff
const patch = getPatchForEdit(oldContent, newContent)
```

### FileWriteTool — Full File Creation/Overwrite

For creating new files or complete overwrites:

```typescript
z.strictObject({
  file_path: z.string(),
  content: z.string(),
})
```

### File History Tracking

**File**: `src/utils/fileHistory.ts`

When enabled, file edits are tracked for undo/redo:

```typescript
function fileHistoryTrackEdit(
  filePath: string,
  oldContent: string,
  newContent: string,
  toolUseId: string,
): void {
  // Snapshot before edit for potential rollback
  fileHistoryMakeSnapshot(filePath, oldContent)
}
```

---

## 13. Key Technical Decisions

### 1. No Pre-Built Index

**Decision**: Rely on runtime search tools instead of pre-built code indexes.

**Rationale**: 
- Zero startup cost
- Always fresh results
- Works with any language/framework
- LLM reasoning guides search strategy

**Trade-off**: Higher per-query token cost, no semantic search capability.

### 2. Attachment-Based Context Injection

**Decision**: Use attachment messages between turns to inject contextual information.

**Rationale**:
- Decouples context sources from the query loop
- Each attachment type can be independently enabled/disabled
- Attachments are visible in the conversation history for debugging
- Can be filtered by sub-agents (e.g., skip file change diffs for read-only agents)

### 3. Multi-Layer Result Compression

**Decision**: Apply compression at multiple levels (per-tool, per-message, micro-compact, auto-compact).

**Rationale**:
- Each layer catches different cases
- Lighter layers run first (cheaper)
- Heavier layers (auto-compact) are last resort
- Prompt cache stability is preserved at each layer

### 4. Stable Replacement Decisions

**Decision**: Once a tool result's fate is decided (replaced or not), it's frozen forever.

**Rationale**:
- Changing a decision would alter the API request prefix
- This would bust the prompt cache
- Frozen decisions are re-applied identically each turn
- New decisions only apply to fresh (never-before-seen) results

### 5. Async Prefetch Pattern

**Decision**: Fire memory and skill prefetch queries concurrently with the main model stream.

**Rationale**:
- Main model streaming takes 5-30 seconds
- Prefetch queries take ~1 second
- By the time tools finish, prefetch results are ready
- Zero additional latency in the common case

---

## 14. Summary

Claude Code's approach to code retrieval and context building is distinctive in several ways:

1. **No traditional index** — The LLM itself drives code exploration through tool calls, making the system language-agnostic and always up-to-date.

2. **Rich attachment system** — A sophisticated 16+ type attachment system injects contextual information (file diffs, memory, skills, diagnostics) between turns without the model explicitly requesting it.

3. **Multi-layer compression** — Six layers of context compression (snip, micro, cached-MC, time-based MC, auto-compact, reactive compact) work together to keep conversations within token limits while preserving the most important information.

4. **Prompt cache stability** — Every design decision considers prompt cache impact. Replacement decisions are frozen, tool schemas are sorted, dynamic content is moved to attachments, and deferred tools reduce schema size.

5. **Async prefetch** — Memory and skill discovery run concurrently with model streaming, hiding their latency entirely.

The system trades higher per-query token cost for simplicity, freshness, and language-agnosticism — a pragmatic choice for a general-purpose coding assistant.

---

*Next document: [02-llm-output-parsing-tool-calling-context.md](./02-llm-output-parsing-tool-calling-context.md) — Deep dive into LLM output format, parsing, and tool call execution.*
