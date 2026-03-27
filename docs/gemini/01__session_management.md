# Session 管理模块详细解读

## 1. 模块概述

Session 管理是 Gemini
CLI 的核心组件，负责管理用户与 AI 的对话会话。它涵盖了会话的创建、维护、恢复、持久化以及上下文管理等功能。整个 Session 系统采用分层架构设计，分布在 SDK、Core 和 CLI 三个层级。

### 1.1 核心职责

- **会话生命周期管理**: 创建、恢复、删除会话
- **对话持久化**: 将会话内容保存到磁盘
- **上下文管理**: 维护会话的上下文状态
- **会话搜索**: 支持对历史会话的搜索和检索

### 1.2 架构层次

```
SDK Layer (packages/sdk/src/)
├── session.ts          # GeminiCliSession 类
├── agent.ts            # GeminiCliAgent 类
└── types.ts            # SessionContext 接口

Core Layer (packages/core/src/)
├── services/
│   └── chatRecordingService.ts    # ChatRecordingService
├── utils/
│   ├── session.ts                 # Session ID 生成
│   └── sessionUtils.ts            # 会话工具函数
└── config/
    └── agent-loop-context.ts      # AgentLoopContext 接口

CLI Layer (packages/cli/src/)
└── utils/
    ├── sessionUtils.ts            # SessionSelector 类
    ├── sessions.ts                # 会话管理工具
    └── sessionCleanup.ts          # 会话清理
```

## 2. 核心数据结构

### 2.1 ConversationRecord - 会话记录

```typescript
interface ConversationRecord {
  sessionId: string; // 唯一会话标识符
  projectHash: string; // 项目哈希（用于隔离不同项目）
  startTime: string; // ISO 格式开始时间
  lastUpdated: string; // ISO 格式最后更新时间
  messages: MessageRecord[]; // 消息数组
  summary?: string; // AI 生成的会话摘要
  directories?: string[]; // 工作区目录
  kind?: 'main' | 'subagent'; // 会话类型
}
```

**关键设计点**:

- **sessionId**: 使用 UUID v4 生成，确保全局唯一性
- **projectHash**: 基于项目路径计算，实现项目级隔离
- **kind**: 区分主会话和子代理会话，子代理会话对用户不可见

### 2.2 MessageRecord - 消息记录

```typescript
type MessageRecord = BaseMessageRecord & ConversationRecordExtra;

interface BaseMessageRecord {
  id: string; // 消息唯一 ID
  timestamp: string; // ISO 格式时间戳
  content: PartListUnion; // 消息内容（支持多模态）
  displayContent?: PartListUnion; // 显示内容（可选）
}

// 扩展类型支持不同类型的消息
type ConversationRecordExtra =
  | { type: 'user' | 'info' | 'error' | 'warning' }
  | {
      type: 'gemini';
      toolCalls?: ToolCallRecord[]; // 工具调用记录
      thoughts?: ThoughtSummary[]; // AI 思考过程
      tokens?: TokensSummary | null; // Token 使用统计
      model?: string; // 使用的模型
    };
```

**消息类型说明**:

- `user`: 用户输入的消息
- `gemini`: AI 的回复消息
- `info`/`error`/`warning`: 系统消息

### 2.3 ToolCallRecord - 工具调用记录

```typescript
interface ToolCallRecord {
  id: string; // 调用唯一 ID
  name: string; // 工具名称
  args: Record<string, unknown>; // 调用参数
  result?: PartListUnion | null; // 执行结果
  status: Status; // 执行状态
  timestamp: string; // 时间戳
  // UI 展示相关字段
  displayName?: string; // 显示名称
  description?: string; // 描述
  resultDisplay?: ToolResultDisplay; // 结果展示方式
  renderOutputAsMarkdown?: boolean; // 是否以 Markdown 渲染
}
```

### 2.4 SessionContext - 会话上下文

```typescript
interface SessionContext {
  sessionId: string; // 会话 ID
  transcript: readonly Content[]; // 对话历史（只读）
  cwd: string; // 当前工作目录
  timestamp: string; // ISO 时间戳
  fs: AgentFilesystem; // 文件系统访问接口
  shell: AgentShell; // Shell 执行接口
  agent: GeminiCliAgent; // 代理实例
  session: GeminiCliSession; // 会话实例
}
```

**设计意图**: SessionContext 为工具函数和系统指令提供统一的运行时环境访问能力。

## 3. 核心类详解

### 3.1 GeminiCliSession (SDK 层)

`packages/sdk/src/session.ts` - 面向开发者的 Session API

#### 3.1.1 构造函数与初始化

```typescript
export class GeminiCliSession {
  private readonly config: Config;
  private readonly tools: Array<Tool<any>>;
  private readonly skillRefs: SkillReference[];
  private readonly instructions: SystemInstructions | undefined;
  private client: GeminiClient | undefined;
  private initialized = false;

  constructor(
    options: GeminiCliAgentOptions,
    private readonly sessionId: string,
    private readonly agent: GeminiCliAgent,
    private readonly resumedData?: ResumedSessionData,
  ) {
    // 初始化配置参数
    const configParams: ConfigParameters = {
      sessionId: this.sessionId,
      targetDir: cwd,
      cwd,
      debugMode: options.debug ?? false,
      model: options.model || PREVIEW_GEMINI_MODEL_AUTO,
      userMemory: initialMemory,
      enableHooks: false,
      mcpEnabled: false,
      extensionsEnabled: false,
      // ... 其他配置
    };
    this.config = new Config(configParams);
  }
}
```

**关键设计决策**:

- **懒加载**: Client 和初始化采用延迟加载策略
- **配置隔离**: 每个 Session 拥有独立的 Config 实例
- **恢复支持**: 通过 resumedData 支持会话恢复

#### 3.1.2 初始化流程

```typescript
async initialize(): Promise<void> {
  if (this.initialized) return;

  // 1. 身份验证
  const authType = getAuthTypeFromEnv() || AuthType.COMPUTE_ADC;
  await this.config.refreshAuth(authType);
  await this.config.initialize();

  // 2. 加载技能
  if (this.skillRefs.length > 0) {
    const loadedSkills = await Promise.all(
      this.skillRefs.map(ref => loadSkillsFromDir(ref.path))
    );
    skillManager.addSkills(loadedSkills.flat());
  }

  // 3. 注册工具
  const registry = loopContext.toolRegistry;
  for (const toolDef of this.tools) {
    const sdkTool = new SdkTool(toolDef, messageBus, this.agent, undefined);
    registry.registerTool(sdkTool);
  }

  // 4. 恢复历史记录（如果是恢复会话）
  if (this.resumedData) {
    const history: Content[] = this.resumedData.conversation.messages.map(m => ({
      role: m.type === 'gemini' ? 'model' : 'user',
      parts: Array.isArray(m.content) ? m.content : [{ text: String(m.content) }]
    }));
    await this.client.resumeChat(history, this.resumedData);
  }

  this.initialized = true;
}
```

#### 3.1.3 消息流处理

```typescript
async *sendStream(
  prompt: string,
  signal?: AbortSignal,
): AsyncGenerator<ServerGeminiStreamEvent> {
  if (!this.initialized || !this.client) {
    await this.initialize();
  }

  let request: Parameters<GeminiClient['sendMessageStream']>[0] = [{ text: prompt }];

  while (true) {
    // 1. 动态系统指令更新
    if (typeof this.instructions === 'function') {
      const context: SessionContext = { /* ... */ };
      const newInstructions = await this.instructions(context);
      this.config.setUserMemory(newInstructions);
      client.updateSystemInstruction();
    }

    // 2. 发送消息并接收流式响应
    const stream = client.sendMessageStream(request, abortSignal, sessionId);
    const toolCallsToSchedule: ToolCallRequestInfo[] = [];

    for await (const event of stream) {
      yield event;
      if (event.type === GeminiEventType.ToolCallRequest) {
        toolCallsToSchedule.push({
          ...event.value,
          isClientInitiated: false,
          prompt_id: sessionId,
        });
      }
    }

    // 3. 如果没有工具调用，结束对话
    if (toolCallsToSchedule.length === 0) break;

    // 4. 执行工具调用
    const context: SessionContext = { /* ... */ };
    const completedCalls = await scheduleAgentTools(
      this.config,
      toolCallsToSchedule,
      { schedulerId: sessionId, toolRegistry: scopedRegistry, signal: abortSignal }
    );

    // 5. 将工具结果作为下一轮对话的输入
    const functionResponses = completedCalls.flatMap(call => call.response.responseParts);
    request = functionResponses as Parameters<GeminiClient['sendMessageStream']>[0];
  }
}
```

**设计亮点**:

- **Generator 函数**: 使用 AsyncGenerator 实现真正的流式响应
- **循环对话**: 支持多轮工具调用直到完成
- **上下文注入**: 每次工具调用都注入最新的 SessionContext

### 3.2 GeminiCliAgent (SDK 层)

`packages/sdk/src/agent.ts` - Agent 工厂类

```typescript
export class GeminiCliAgent {
  private options: GeminiCliAgentOptions;

  constructor(options: GeminiCliAgentOptions) {
    this.options = options;
  }

  // 创建新会话
  session(options?: { sessionId?: string }): GeminiCliSession {
    const sessionId = options?.sessionId || createSessionId();
    return new GeminiCliSession(this.options, sessionId, this);
  }

  // 恢复已有会话
  async resumeSession(sessionId: string): Promise<GeminiCliSession> {
    const storage = new Storage(cwd);
    await storage.initialize();

    // 1. 列出所有会话文件
    const sessions = await storage.listProjectChatFiles();

    // 2. 使用前缀匹配优化查找
    const truncatedId = sessionId.slice(0, 8);
    const candidates = sessions.filter((s) => s.filePath.includes(truncatedId));
    const filesToCheck = candidates.length > 0 ? candidates : sessions;

    // 3. 验证会话 ID 并加载数据
    for (const sessionFile of filesToCheck) {
      const loaded = await storage.loadProjectTempFile<ConversationRecord>(
        sessionFile.filePath,
      );
      if (loaded && loaded.sessionId === sessionId) {
        const resumedData: ResumedSessionData = {
          conversation: loaded,
          filePath: path.join(
            storage.getProjectTempDir(),
            sessionFile.filePath,
          ),
        };
        return new GeminiCliSession(
          this.options,
          loaded.sessionId,
          this,
          resumedData,
        );
      }
    }

    throw new Error(`Session with ID ${sessionId} not found`);
  }
}
```

**设计模式**: 工厂模式 + 构建者模式，提供清晰的 API 接口。

### 3.3 ChatRecordingService (Core 层)

`packages/core/src/services/chatRecordingService.ts` - 会话持久化服务

#### 3.3.1 文件存储结构

```
~/.gemini/tmp/
└── <project_hash>/
    └── chats/
        ├── session-2025-01-15T10-30-00-a1b2c3d4.json
        ├── session-2025-01-15T11-45-00-e5f6g7h8.json
        └── ...
```

**文件名格式**: `session-{timestamp}-{session_id_prefix}.json`

- **timestamp**: ISO 8601 格式，去掉冒号（文件系统安全）
- **session_id_prefix**: 会话 ID 的前 8 个字符，用于快速查找

#### 3.3.2 初始化与恢复

```typescript
initialize(resumedSessionData?: ResumedSessionData, kind?: 'main' | 'subagent'): void {
  if (resumedSessionData) {
    // 恢复模式
    this.conversationFile = resumedSessionData.filePath;
    this.sessionId = resumedSessionData.conversation.sessionId;
    this.kind = resumedSessionData.conversation.kind;

    // 清除缓存强制重新读取
    this.cachedLastConvData = null;
    this.cachedConversation = null;
  } else {
    // 新建模式
    const chatsDir = path.join(
      this.context.config.storage.getProjectTempDir(),
      'chats'
    );
    fs.mkdirSync(chatsDir, { recursive: true });

    const timestamp = new Date()
      .toISOString()
      .slice(0, 16)
      .replace(/:/g, '-');
    const filename = `${SESSION_FILE_PREFIX}${timestamp}-${this.sessionId.slice(0, 8)}.json`;
    this.conversationFile = path.join(chatsDir, filename);

    this.writeConversation({
      sessionId: this.sessionId,
      projectHash: this.projectHash,
      startTime: new Date().toISOString(),
      lastUpdated: new Date().toISOString(),
      messages: [],
      kind: this.kind,
    });
  }
}
```

#### 3.3.3 消息记录机制

```typescript
recordMessage(message: {
  model: string | undefined;
  type: ConversationRecordExtra['type'];
  content: PartListUnion;
  displayContent?: PartListUnion;
}): void {
  if (!this.conversationFile) return;

  this.updateConversation((conversation) => {
    const msg = this.newMessage(message.type, message.content, message.displayContent);

    if (msg.type === 'gemini') {
      // AI 消息：整合思考过程和 Token 统计
      conversation.messages.push({
        ...msg,
        thoughts: this.queuedThoughts,    // 排队的思考过程
        tokens: this.queuedTokens,        // Token 使用统计
        model: message.model,
      });
      this.queuedThoughts = [];
      this.queuedTokens = null;
    } else {
      // 其他消息：直接添加
      conversation.messages.push(msg);
    }
  });
}
```

**关键设计**:

- **异步队列**: thoughts 和 tokens 使用内存队列，延迟写入
- **批量更新**: updateConversation 封装读写逻辑
- **缓存优化**: 使用 cachedConversation 避免重复读取

#### 3.3.4 缓存与写入优化

```typescript
private readConversation(): ConversationRecord {
  // 缓存命中直接返回
  if (this.cachedConversation) {
    return this.cachedConversation;
  }

  // 从磁盘读取
  this.cachedLastConvData = fs.readFileSync(this.conversationFile!, 'utf8');
  this.cachedConversation = JSON.parse(this.cachedLastConvData);
  return this.cachedConversation;
}

private writeConversation(
  conversation: ConversationRecord,
  { allowEmpty = false }: { allowEmpty?: boolean } = {}
): void {
  const newContent = JSON.stringify(conversation, null, 2);

  // 内容未变化则跳过写入（基于字符串比较）
  if (this.cachedLastConvData === newContent) return;

  this.cachedConversation = conversation;
  conversation.lastUpdated = new Date().toISOString();
  const contentToWrite = JSON.stringify(conversation, null, 2);
  this.cachedLastConvData = contentToWrite;

  fs.mkdirSync(path.dirname(this.conversationFile), { recursive: true });
  fs.writeFileSync(this.conversationFile, contentToWrite);
}
```

**性能优化**:

1. **内存缓存**: 避免重复 JSON 解析
2. **内容哈希**: 通过字符串比较检测变化
3. **延迟更新**: lastUpdated 只在写入时更新

#### 3.3.5 磁盘满错误处理

```typescript
// 处理 ENOSPC（磁盘空间不足）错误
if (
  error instanceof Error &&
  'code' in error &&
  (error as NodeJS.ErrnoException).code === 'ENOSPC'
) {
  this.conversationFile = null;
  debugLogger.warn(ENOSPC_WARNING_MESSAGE);
  return; // 不抛出错误，允许 CLI 继续运行
}
```

**优雅降级**: 当磁盘满时，自动禁用记录功能，但不中断用户会话。

### 3.4 SessionSelector (CLI 层)

`packages/cli/src/utils/sessionUtils.ts` - 会话选择器

#### 3.4.1 会话文件发现

```typescript
export const getAllSessionFiles = async (
  chatsDir: string,
  currentSessionId?: string,
  options: GetSessionOptions = {},
): Promise<SessionFileEntry[]> => {
  const files = await fs.readdir(chatsDir);
  const sessionFiles = files
    .filter((f) => f.startsWith(SESSION_FILE_PREFIX) && f.endsWith('.json'))
    .sort();

  const sessionPromises = sessionFiles.map(
    async (file): Promise<SessionFileEntry> => {
      try {
        const content: ConversationRecord = JSON.parse(
          await fs.readFile(filePath, 'utf8'),
        );

        // 验证必需字段
        if (
          !content.sessionId ||
          !content.messages ||
          !Array.isArray(content.messages)
        ) {
          return { fileName: file, sessionInfo: null }; // 损坏文件
        }

        // 跳过纯系统消息会话
        if (!hasUserOrAssistantMessage(content.messages)) {
          return { fileName: file, sessionInfo: null };
        }

        // 跳过子代理会话
        if (content.kind === 'subagent') {
          return { fileName: file, sessionInfo: null };
        }

        return { fileName: file, sessionInfo };
      } catch {
        return { fileName: file, sessionInfo: null }; // 解析失败
      }
    },
  );

  return await Promise.all(sessionPromises);
};
```

**设计考虑**:

- **并行处理**: 使用 Promise.all 并行读取所有文件
- **容错设计**: 损坏文件返回 null，不影响其他会话
- **隐私保护**: 过滤掉子代理会话

#### 3.4.2 会话去重与排序

```typescript
export const getSessionFiles = async (...): Promise<SessionInfo[]> => {
  const allFiles = await getAllSessionFiles(chatsDir, currentSessionId, options);

  // 过滤损坏文件
  const validSessions = allFiles
    .filter((entry): entry is { fileName: string; sessionInfo: SessionInfo } =>
      entry.sessionInfo !== null
    )
    .map((entry) => entry.sessionInfo);

  // 按 ID 去重（保留最新的）
  const uniqueSessionsMap = new Map<string, SessionInfo>();
  for (const session of validSessions) {
    if (!uniqueSessionsMap.has(session.id) ||
        new Date(session.lastUpdated).getTime() >
        new Date(uniqueSessionsMap.get(session.id)!.lastUpdated).getTime()) {
      uniqueSessionsMap.set(session.id, session);
    }
  }
  const uniqueSessions = Array.from(uniqueSessionsMap.values());

  // 按开始时间排序（最旧在前）
  uniqueSessions.sort(
    (a, b) => new Date(a.startTime).getTime() - new Date(b.startTime).getTime()
  );

  // 设置 1-based 索引
  uniqueSessions.forEach((session, index) => {
    session.index = index + 1;
  });

  return uniqueSessions;
};
```

#### 3.4.3 会话查找算法

```typescript
async findSession(identifier: string): Promise<SessionInfo> {
  const sessions = await this.listSessions();

  if (sessions.length === 0) {
    throw SessionError.noSessionsFound();
  }

  // 1. 先尝试 UUID 匹配
  const sessionByUuid = sortedSessions.find(
    (session) => session.id === identifier
  );
  if (sessionByUuid) {
    return sessionByUuid;
  }

  // 2. 尝试索引号匹配（1-based）
  const index = parseInt(identifier, 10);
  if (!isNaN(index) && index.toString() === identifier &&
      index > 0 && index <= sortedSessions.length) {
    return sortedSessions[index - 1];
  }

  throw SessionError.invalidSessionIdentifier(identifier);
}
```

**用户友好的标识符**:

- 支持完整 UUID
- 支持数字索引（如 `1`, `2`, `3`）
- 支持 `latest` 关键字

## 4. 持久化机制详解

### 4.1 存储位置

```typescript
// 基础路径计算
const projectHash = getProjectHash(context.config.getProjectRoot());
const baseDir = path.join(os.homedir(), '.gemini', 'tmp', projectHash);

// 会话文件
const chatsDir = path.join(baseDir, 'chats');

// 其他相关目录
const logsDir = path.join(baseDir, 'logs'); // 活动日志
const toolOutputDir = path.join(
  baseDir,
  'tool-outputs',
  `session-${sessionId}`,
); // 工具输出
const sessionDir = path.join(baseDir, sessionId); // 会话专属目录（计划、任务等）
```

### 4.2 写入策略

**增量写入**:

- 每次只追加新消息，不重写整个文件
- 使用 updateConversation 封装更新逻辑

**原子性保证**:

```typescript
private updateConversation(
  updateFn: (conversation: ConversationRecord) => void
) {
  const conversation = this.readConversation();  // 读取
  updateFn(conversation);                        // 修改
  this.writeConversation(conversation);          // 写入
}
```

**错误恢复**:

- 文件损坏时创建空会话作为占位符
- 磁盘满时优雅降级，继续运行

### 4.3 会话恢复流程

```
用户请求恢复会话
        │
        ▼
┌──────────────────┐
│ 解析标识符       │───► UUID / 索引 / "latest"
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ 查找会话文件     │───► 扫描 chats 目录
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ 验证会话 ID      │───► 比对 sessionId 字段
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ 重建对话历史     │───► 转换 MessageRecord → Content
└──────────────────┘
        │
        ▼
┌──────────────────┐
│ 恢复 GeminiClient │───► resumeChat(history, resumedData)
└──────────────────┘
        │
        ▼
   恢复完成，继续对话
```

### 4.4 会话清理策略

```typescript
// 删除会话时清理所有相关数据
async deleteSession(sessionId: string): Promise<void> {
  const tempDir = this.context.config.storage.getProjectTempDir();

  // 1. 删除会话文件
  const sessionPath = path.join(chatsDir, `${sessionId}.json`);
  if (fs.existsSync(sessionPath)) {
    fs.unlinkSync(sessionPath);
  }

  // 2. 清理活动日志
  const logPath = path.join(logsDir, `session-${sessionId}.jsonl`);
  if (fs.existsSync(logPath)) {
    fs.unlinkSync(logPath);
  }

  // 3. 清理工具输出
  const safeSessionId = sanitizeFilenamePart(sessionId);
  const toolOutputDir = path.join(tempDir, 'tool-outputs', `session-${safeSessionId}`);
  if (fs.existsSync(toolOutputDir) && toolOutputDir.startsWith(toolOutputsBase)) {
    fs.rmSync(toolOutputDir, { recursive: true, force: true });
  }

  // 4. 清理会话专属目录
  const sessionDir = path.join(tempDir, safeSessionId);
  if (fs.existsSync(sessionDir) && sessionDir.startsWith(tempDir)) {
    fs.rmSync(sessionDir, { recursive: true, force: true });
  }
}
```

**安全措施**:

- 路径验证: 确保只删除 tmp 目录下的文件
- 文件名清理: sanitizeFilenamePart 防止路径遍历攻击

## 5. 上下文管理机制

### 5.1 AgentLoopContext

```typescript
interface AgentLoopContext {
  readonly config: Config; // 全局配置
  readonly promptId: string; // 当前回合唯一 ID
  readonly toolRegistry: ToolRegistry; // 工具注册表
  readonly messageBus: MessageBus; // 消息总线
  readonly geminiClient: GeminiClient; // LLM 客户端
  readonly sandboxManager: SandboxManager; // 沙盒管理器
}
```

**作用域**: 每个 Agent 回合或子代理循环拥有独立的上下文视图。

### 5.2 Config 类

`packages/core/src/config/config.ts`

```typescript
export class Config implements McpContext, AgentLoopContext {
  // 核心属性
  private sessionId: string;
  private targetDir: string;
  private cwd: string;
  private userMemory: string;

  // 管理器实例
  readonly storage: Storage;
  readonly toolRegistry: ToolRegistry;
  readonly messageBus: MessageBus;
  readonly geminiClient: GeminiClient;
  readonly sandboxManager: SandboxManager;
  readonly skillManager: SkillManager;

  // 方法
  getSessionId(): string {
    return this.sessionId;
  }
  getWorkingDir(): string {
    return this.cwd;
  }
  getProjectRoot(): string {
    return this.targetDir;
  }
  setUserMemory(memory: string): void {
    this.userMemory = memory;
  }
  getUserMemory(): string {
    return this.userMemory;
  }
}
```

**单例模式**: Config 作为依赖注入容器，统一管理所有服务实例。

### 5.3 上下文传递链

```
GeminiCliSession
       │
       ├──► Config (AgentLoopContext)
       │       ├──► ToolRegistry
       │       ├──► MessageBus
       │       ├──► GeminiClient
       │       └──► SandboxManager
       │
       └──► SessionContext (工具函数使用)
               ├──► fs: AgentFilesystem
               ├──► shell: AgentShell
               ├──► agent: GeminiCliAgent
               └──► session: GeminiCliSession
```

### 5.4 动态上下文更新

```typescript
// 系统指令函数式更新
if (typeof this.instructions === 'function') {
  const context: SessionContext = {
    sessionId,
    transcript: client.getHistory(), // 实时获取最新历史
    cwd: this.config.getWorkingDir(),
    timestamp: new Date().toISOString(),
    fs,
    shell,
    agent: this.agent,
    session: this,
  };

  // 异步执行用户提供的指令函数
  const newInstructions = await this.instructions(context);
  this.config.setUserMemory(newInstructions);
  client.updateSystemInstruction(); // 更新系统提示
}
```

**使用场景**:

- 根据对话历史动态调整 AI 行为
- 根据当前工作目录调整文件操作策略
- 基于时间戳添加时效性信息

## 6. Session 搜索功能

### 6.1 搜索实现

```typescript
// 启用全文内容加载
const sessions = await getSessionFiles(chatsDir, currentSessionId, {
  includeFullContent: true, // 加载完整消息内容
});

// 执行搜索
const searchResults = sessions
  .map((session) => {
    const matches: TextMatch[] = [];

    // 在每条消息中搜索
    session.messages?.forEach((msg) => {
      const content = msg.content;
      let index = content.toLowerCase().indexOf(query);

      while (index !== -1) {
        // 提取上下文片段
        const before = content.slice(Math.max(0, index - 50), index);
        const match = content.slice(index, index + query.length);
        const after = content.slice(
          index + query.length,
          index + query.length + 50,
        );

        matches.push({
          before: before.length === 50 ? '...' + before : before,
          match,
          after: after.length === 50 ? after + '...' : after,
          role: msg.role,
        });

        // 继续搜索下一个匹配
        index = content.toLowerCase().indexOf(query, index + 1);
      }
    });

    return {
      ...session,
      matchSnippets: matches,
      matchCount: matches.length,
    };
  })
  .filter((session) => session.matchCount! > 0) // 过滤无匹配的会话
  .sort((a, b) => b.matchCount! - a.matchCount!); // 按匹配数排序
```

### 6.2 搜索结果展示

```typescript
export interface TextMatch {
  before: string; // 匹配前的文本（50 字符，带省略号）
  match: string; // 匹配的文本
  after: string; // 匹配后的文本（50 字符，带省略号）
  role: 'user' | 'assistant'; // 消息角色
}

export interface SessionInfo {
  // ... 其他字段
  fullContent?: string; // 完整内容（搜索时加载）
  messages?: Array<{
    // 结构化消息（搜索时加载）
    role: 'user' | 'assistant';
    content: string;
  }>;
  matchSnippets?: TextMatch[]; // 搜索匹配片段
  matchCount?: number; // 匹配总数
}
```

### 6.3 性能考虑

**按需加载**:

- 默认不加载完整内容，减少内存占用
- 仅在搜索时通过 `includeFullContent: true` 加载

**无索引设计**:

- 采用简单的字符串匹配，无需维护索引
- 适合会话数量不多的场景（< 1000）
- 对于大量会话，可考虑添加简单的倒排索引

## 7. 关键设计决策与最佳实践

### 7.1 ID 生成策略

```typescript
// 使用 Node.js crypto 模块生成 UUID v4
import { randomUUID } from 'node:crypto';

export function createSessionId(): string {
  return randomUUID(); // 例如: "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

**优点**:

- 全局唯一，无需中心化 ID 生成器
- 包含时间戳信息（UUID v4 版本字段）
- 前 8 字符可作为文件名前缀，便于查找

### 7.2 文件命名约定

```typescript
const filename = `${SESSION_FILE_PREFIX}${timestamp}-${this.sessionId.slice(0, 8)}.json`;
// 结果: "session-2025-01-15T10-30-00-a1b2c3d4.json"
```

**设计考量**:

- **可读性**: 时间戳直观显示会话创建时间
- **可排序**: 按文件名排序即为时间顺序
- **可查找**: sessionId 前缀支持快速定位

### 7.3 错误处理策略

**分层处理**:

1. **SDK 层**: 抛出清晰的错误，包含上下文
2. **Core 层**: 记录日志，优雅降级
3. **CLI 层**: 友好的错误提示

**示例**:

```typescript
export class SessionError extends Error {
  constructor(
    readonly code: SessionErrorCode,
    message: string,
  ) {
    super(message);
    this.name = 'SessionError';
  }

  static noSessionsFound(): SessionError {
    return new SessionError(
      'NO_SESSIONS_FOUND',
      'No previous sessions found for this project.',
    );
  }
}
```

### 7.4 隐私与安全

**数据隔离**:

- 不同项目使用不同 projectHash，会话文件隔离存储
- 子代理会话标记为 `kind: 'subagent'`，对用户不可见

**路径安全**:

```typescript
// 清理会话 ID 防止路径遍历
const safeSessionId = sanitizeFilenamePart(sessionId);

// 验证路径在允许范围内
if (fs.existsSync(sessionDir) && sessionDir.startsWith(tempDir)) {
  fs.rmSync(sessionDir, { recursive: true, force: true });
}
```

### 7.5 扩展性设计

**插件化工具**:

```typescript
// 动态注册工具
for (const toolDef of this.tools) {
  const sdkTool = new SdkTool(toolDef, messageBus, this.agent, undefined);
  registry.registerTool(sdkTool);
}

// 工具绑定上下文
scopedRegistry.getTool = (name: string) => {
  const tool = originalRegistry.getTool(name);
  if (tool instanceof SdkTool) {
    return tool.bindContext(context); // 注入 SessionContext
  }
  return tool;
};
```

**技能系统**:

```typescript
// 动态加载技能
const loadPromises = this.skillRefs.map(async (ref) => {
  if (ref.type === 'dir') {
    return await loadSkillsFromDir(ref.path);
  }
});
const loadedSkills = (await Promise.all(loadPromises)).flat();
skillManager.addSkills(loadedSkills);
```

## 8. 使用示例

### 8.1 创建新会话

```typescript
import { GeminiCliAgent } from '@google/gemini-cli-sdk';

const agent = new GeminiCliAgent({
  instructions: 'You are a helpful coding assistant.',
  tools: [myCustomTool],
  cwd: '/path/to/project',
});

const session = agent.session();

for await (const event of session.sendStream('Hello!')) {
  if (event.type === GeminiEventType.Text) {
    process.stdout.write(event.value);
  }
}
```

### 8.2 恢复会话

```typescript
// 通过 ID 恢复
const resumedSession = await agent.resumeSession('a1b2c3d4-...');

// 或使用 SessionSelector
const selector = new SessionSelector(config);
const { sessionData, sessionPath } = await selector.resolveSession('latest');
```

### 8.3 动态系统指令

```typescript
const agent = new GeminiCliAgent({
  instructions: async (context) => {
    const fileCount = await context.fs.readFile('.filecount');
    return `You are in a project with ${fileCount} files. Current time: ${context.timestamp}`;
  },
});
```

### 8.4 搜索历史会话

```typescript
const sessions = await getSessionFiles(chatsDir, currentSessionId, {
  includeFullContent: true,
});

const results = sessions
  .filter((s) => s.fullContent?.includes('search term'))
  .map((s) => ({
    id: s.id,
    name: s.displayName,
    matches: s.messageCount,
  }));
```

## 9. 总结

Gemini CLI 的 Session 管理系统采用了分层、模块化的架构设计：

1. **SDK 层**提供开发者友好的 API，封装了复杂的内部逻辑
2. **Core 层**实现核心的持久化、上下文管理和工具调度
3. **CLI 层**提供用户交互功能，如会话选择、搜索和清理

**核心特点**:

- 完整的会话生命周期管理
- 可靠的持久化机制，支持磁盘满等边界情况
- 灵活的上下文注入，支持动态系统指令
- 用户友好的会话恢复，支持多种标识符
- 安全的文件操作，防止路径遍历攻击

**适用场景**:

- 长期运行的 AI 对话应用
- 需要会话历史的交互式工具
- 多项目隔离的对话管理
- 工具调用和代码执行的上下文保持

该设计在功能完整性、性能和安全性之间取得了良好的平衡，可以作为构建类似 AI 对话系统的参考实现。
