# Runtime 运行时管理模块详细解读

## 1. 模块概述

Runtime 运行时管理是 Gemini
CLI 的核心执行引擎，负责管理 AI 工具调度的完整生命周期。它采用**事件驱动架构**，通过状态机和异步调度实现高效的并发控制。

### 1.1 核心职责

- **事件循环管理**: 协调异步操作的执行顺序
- **任务调度**: 管理工具调度的队列和并发执行
- **状态管理**: 维护工具调用的状态机转换
- **并发控制**: 实现并行和串行执行策略
- **取消机制**: 支持操作的中断和清理

### 1.2 架构层次

```
┌─────────────────────────────────────────────────────────────┐
│                     Runtime Architecture                     │
├─────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │   Scheduler  │  │ StateManager │  │  ToolExecutor    │  │
│  │   (调度器)    │  │  (状态管理)   │  │   (工具执行)      │  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘  │
│         │                 │                   │            │
│  ┌──────┴─────────────────┴───────────────────┴─────────┐  │
│  │              CoreEventEmitter (事件总线)               │  │
│  └────────────────────────────────────────────────────────┘  │
│                          │                                   │
│  ┌───────────────────────┴──────────────────────────────┐   │
│  │              MessageBus (消息总线)                      │   │
│  └────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 1.3 核心组件

| 组件                  | 文件路径                          | 职责                 |
| --------------------- | --------------------------------- | -------------------- |
| Scheduler             | `scheduler/scheduler.ts`          | 工具调度的主要协调器 |
| SchedulerStateManager | `scheduler/state-manager.ts`      | 工具调用状态管理     |
| ToolExecutor          | `scheduler/tool-executor.ts`      | 实际执行工具调用     |
| CoreEventEmitter      | `utils/events.ts`                 | 全局事件发布/订阅    |
| MessageBus            | `confirmation-bus/message-bus.ts` | 确认消息传递         |

## 2. 事件驱动架构

### 2.1 CoreEventEmitter 设计

```typescript
export class CoreEventEmitter extends EventEmitter<CoreEvents> {
  private _eventBacklog: EventBacklogItem[] = [];
  private _backlogHead = 0;
  private static readonly MAX_BACKLOG_SIZE = 10000;

  // 核心事件枚举
  private _emitOrQueue<K extends keyof CoreEvents>(
    event: K,
    ...args: CoreEvents[K]
  ): void {
    if (this.listenerCount(event) === 0) {
      // 无监听器时缓存事件
      const backlogSize = this._eventBacklog.length - this._backlogHead;
      if (backlogSize >= CoreEventEmitter.MAX_BACKLOG_SIZE) {
        // 环形缓冲区：使用 head 指针避免 O(n) 的 shift 操作
        (this._eventBacklog as unknown[])[this._backlogHead] = undefined;
        this._backlogHead++;

        // 当死条目超过一半时压缩
        if (this._backlogHead >= CoreEventEmitter.MAX_BACKLOG_SIZE / 2) {
          this._eventBacklog = this._eventBacklog.slice(this._backlogHead);
          this._backlogHead = 0;
        }
      }
      this._eventBacklog.push({ event, args } as EventBacklogItem);
    } else {
      // 直接触发事件
      (this.emit as (event: K, ...args: CoreEvents[K]) => boolean)(
        event,
        ...args,
      );
    }
  }
}

// 全局单例
export const coreEvents = new CoreEventEmitter();
```

**设计亮点**:

- **环形缓冲区**: 使用 head 指针管理事件队列，避免频繁的数组重排
- **惰性发射**: 无监听器时缓存事件，UI 订阅后批量回放
- **类型安全**: TypeScript 泛型确保事件类型正确

### 2.2 事件类型

```typescript
export enum CoreEvent {
  UserFeedback = 'user-feedback', // 用户反馈
  ModelChanged = 'model-changed', // 模型切换
  McpProgress = 'mcp-progress', // MCP 进度更新
  HookStart = 'hook-start', // Hook 开始
  HookEnd = 'hook-end', // Hook 结束
  RetryAttempt = 'retry-attempt', // 重试尝试
  ToolCallsUpdate = 'tool_calls_update', // 工具调用更新
  // ... 更多事件
}
```

### 2.3 事件流示例

```
用户输入
   │
   ▼
┌─────────────────┐
│ Scheduler.schedule() │ 发起工具调用
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ CoreEventEmitter │ 发布 TOOL_CALLS_UPDATE
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ MessageBus      │ 传递给 UI 层
└────────┬────────┘
         │
         ▼
    UI 更新显示
```

## 3. 调度器（Scheduler）架构

### 3.1 整体流程

```typescript
export class Scheduler {
  // 核心队列
  private readonly requestQueue: SchedulerQueueItem[] = [];
  private readonly state: SchedulerStateManager;
  private readonly executor: ToolExecutor;

  // 状态标志
  private isProcessing = false;
  private isCancelling = false;

  /**
   * 主调度入口
   */
  async schedule(
    request: ToolCallRequestInfo | ToolCallRequestInfo[],
    signal: AbortSignal,
  ): Promise<CompletedToolCall[]> {
    return runInDevTraceSpan(
      { operation: GeminiCliOperation.ScheduleToolCalls },
      async ({ metadata: spanMetadata }) => {
        const requests = Array.isArray(request) ? request : [request];

        let toolCallResponse: CompletedToolCall[] = [];

        if (this.isProcessing || this.state.isActive) {
          // 忙时入队等待
          toolCallResponse = await this._enqueueRequest(requests, signal);
        } else {
          // 空闲时立即执行
          toolCallResponse = await this._startBatch(requests, signal);
        }

        return toolCallResponse;
      },
    );
  }
}
```

### 3.2 三阶段执行模型

调度器采用**三阶段管道**处理工具调用：

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Phase 1    │     │  Phase 2    │     │  Phase 3    │
│ Ingestion   │ ──► │ Processing  │ ──► │ Execution   │
│ (接收与验证) │     │ (策略与确认) │     │ (执行与完成) │
└─────────────┘     └─────────────┘     └─────────────┘
```

#### Phase 1: 接收与验证

```typescript
private async _startBatch(
  requests: ToolCallRequestInfo[],
  signal: AbortSignal,
): Promise<CompletedToolCall[]> {
  this.isProcessing = true;
  this.state.clearBatch();
  const currentApprovalMode = this.config.getApprovalMode();

  try {
    const toolRegistry = this.context.toolRegistry;

    // 1. 构建工具调用对象
    const newCalls: ToolCall[] = requests.map((request) => {
      const enrichedRequest: ToolCallRequestInfo = {
        ...request,
        schedulerId: this.schedulerId,
        parentCallId: this.parentCallId,
      };

      const tool = toolRegistry.getTool(request.name);

      if (!tool) {
        // 工具未注册，直接标记为错误
        return this._createToolNotFoundErroredToolCall(
          enrichedRequest,
          toolRegistry.getAllToolNames(),
        );
      }

      // 2. 验证参数并创建调用对象
      return this._validateAndCreateToolCall(
        enrichedRequest,
        tool,
        currentApprovalMode,
      );
    });

    // 3. 入队等待处理
    this.state.enqueue(newCalls);
    await this._processQueue(signal);

    return this.state.completedBatch;
  } finally {
    this.isProcessing = false;
    this.state.clearBatch();
    this._processNextInRequestQueue();
  }
}
```

#### Phase 2: 处理循环

```typescript
private async _processQueue(signal: AbortSignal): Promise<void> {
  while (this.state.queueLength > 0 || this.state.isActive) {
    const shouldContinue = await this._processNextItem(signal);
    if (!shouldContinue) break;
  }
}

private async _processNextItem(signal: AbortSignal): Promise<boolean> {
  if (signal.aborted || this.isCancelling) {
    this.state.cancelAllQueued('Operation cancelled');
    return false;
  }

  const initialStatuses = new Map(
    this.state.allActiveCalls.map((c) => [c.request.callId, c.status]),
  );

  // 1. 从队列取出下一个
  if (!this.state.isActive) {
    const next = this.state.dequeue();
    if (!next) return false;

    // 2. 如果是并行化工具，批量取出
    if (this._isParallelizable(next.request)) {
      while (this.state.queueLength > 0) {
        const peeked = this.state.peekQueue();
        if (peeked && this._isParallelizable(peeked.request)) {
          this.state.dequeue();
        } else {
          break;
        }
      }
    }
  }

  // 3. 并行处理所有 Validating 状态的工具
  const validatingCalls = this.state.allActiveCalls.filter(
    (c): c is ValidatingToolCall =>
      c.status === CoreToolCallStatus.Validating,
  );
  if (validatingCalls.length > 0) {
    await Promise.all(
      validatingCalls.map((c) => this._processValidatingCall(c, signal)),
    );
  }

  // 4. 执行所有 Scheduled 状态的工具
  const scheduledCalls = this.state.allActiveCalls.filter(
    (c): c is ScheduledToolCall => c.status === CoreToolCallStatus.Scheduled,
  );

  const allReady = this.state.allActiveCalls.every(
    (c) => c.status === CoreToolCallStatus.Scheduled || this.isTerminal(c.status),
  );

  if (allReady && scheduledCalls.length > 0) {
    await Promise.all(scheduledCalls.map((c) => this._execute(c, signal)));
  }

  // 5. 终结已完成的调用
  for (const call of this.state.allActiveCalls) {
    if (this.isTerminal(call.status)) {
      this.state.finalizeCall(call.request.callId);
    }
  }

  // 6. 如果没有进展，让出事件循环
  const isWaitingForExternal = this.state.allActiveCalls.some(
    (c) => c.status === CoreToolCallStatus.AwaitingApproval ||
           c.status === CoreToolCallStatus.Executing,
  );

  if (isWaitingForExternal && this.state.isActive) {
    await new Promise((resolve) => queueMicrotask(() => resolve(true)));
    return true;
  }

  return false;
}
```

#### Phase 3: 单个工具编排

```typescript
private async _processToolCall(
  toolCall: ValidatingToolCall,
  signal: AbortSignal,
): Promise<void> {
  const callId = toolCall.request.callId;

  // 1. 策略检查
  const { decision, rule } = await checkPolicy(toolCall, this.config, this.subagent);

  if (decision === PolicyDecision.DENY) {
    // 策略拒绝
    this.state.updateStatus(callId, CoreToolCallStatus.Error, errorResponse);
    return;
  }

  // 2. 用户确认
  if (decision === PolicyDecision.ASK_USER) {
    const result = await resolveConfirmation(toolCall, signal, {
      config: this.config,
      messageBus: this.messageBus,
      state: this.state,
      // ... 其他依赖
    });

    if (result.outcome === ToolConfirmationOutcome.Cancel) {
      // 用户取消
      this.state.updateStatus(callId, CoreToolCallStatus.Cancelled, 'User denied');
      return;
    }
  }

  // 3. 标记为 Scheduled，等待执行
  this.state.updateStatus(callId, CoreToolCallStatus.Scheduled);
}
```

### 3.3 并发控制策略

#### 并行化检测

```typescript
private _isParallelizable(request: ToolCallRequestInfo): boolean {
  if (request.args) {
    const wait = request.args['wait_for_previous'];
    if (typeof wait === 'boolean') {
      return !wait;
    }
  }
  // 默认并行
  return true;
}
```

**设计说明**:

- AI 可以通过 `wait_for_previous: true` 强制串行执行
- 默认策略是最大化并行度
- 依赖分析由 AI 在提示工程层面控制

#### 并发执行示例

```
AI 请求: [ToolA, ToolB, ToolC, ToolD]

情况 1: 全部并行
ToolA ──► Executing
ToolB ──► Executing
ToolC ──► Executing
ToolD ──► Executing

情况 2: ToolC 依赖前面结果
ToolA ──► Executing ──► Success
ToolB ──► Executing ──► Success
ToolC ──► wait_for_previous: true ──► Scheduled ──► Executing
ToolD ──► Executing

情况 3: 批量并行化
Queue: [ToolA(parallel), ToolB(parallel), ToolC(serial), ToolD(parallel)]

Iteration 1:
  - Dequeue ToolA (parallelizable)
  - Peek ToolB (parallelizable) → Dequeue
  - Peek ToolC (serial) → Stop batching
  - Active: [ToolA, ToolB]
  - Execute both in parallel

Iteration 2:
  - ToolC becomes active
  - Execute sequentially
```

## 4. 状态管理

### 4.1 状态机定义

```typescript
export enum CoreToolCallStatus {
  Validating = 'validating', // 验证中
  Scheduled = 'scheduled', // 已调度
  Executing = 'executing', // 执行中
  AwaitingApproval = 'awaiting_approval', // 等待确认
  Success = 'success', // 成功
  Error = 'error', // 错误
  Cancelled = 'cancelled', // 已取消
}
```

### 4.2 状态转换图

```
                    ┌─────────────┐
         ┌─────────│ Validating  │◄────────┐
         │         └──────┬──────┘         │
         │                │                │
         │                ▼                │
   Error │         ┌─────────────┐         │ Tail Call
         │    ┌───►│  Scheduled  │◄────────┘
         │    │    └──────┬──────┘
         │    │           │
         │    │           ▼
         │    │    ┌─────────────┐
         │    └───►│  Executing  │──────────► Success
         │         └──────┬──────┘
         │                │
         │                ▼
         │    ┌───────────────────────────┐
         └───►│      AwaitingApproval     │
              └─────────────┬─────────────┘
                            │
                            ▼
                      Cancelled
```

### 4.3 SchedulerStateManager 实现

```typescript
export class SchedulerStateManager {
  // 三个存储区域
  private readonly activeCalls = new Map<string, ToolCall>(); // 活跃调用
  private readonly queue: ToolCall[] = []; // 等待队列
  private _completedBatch: CompletedToolCall[] = []; // 已完成

  constructor(
    private readonly messageBus: MessageBus,
    private readonly schedulerId: string = ROOT_SCHEDULER_ID,
    private readonly onTerminalCall?: TerminalCallHandler,
  ) {}

  // 状态转换核心方法
  updateStatus(
    callId: string,
    status: CoreToolCallStatus,
    auxiliaryData?: unknown,
  ): void {
    const call = this.activeCalls.get(callId);
    if (!call) return;

    const updatedCall = this.transitionCall(call, status, auxiliaryData);
    this.activeCalls.set(callId, updatedCall);
    this.emitUpdate();
  }

  // 状态转换逻辑
  private transitionCall(
    call: ToolCall,
    newStatus: Status,
    auxiliaryData?: unknown,
  ): ToolCall {
    switch (newStatus) {
      case CoreToolCallStatus.Success:
        return this.toSuccess(call, auxiliaryData as ToolCallResponseInfo);
      case CoreToolCallStatus.Error:
        return this.toError(call, auxiliaryData as ToolCallResponseInfo);
      case CoreToolCallStatus.Scheduled:
        return this.toScheduled(call);
      case CoreToolCallStatus.Executing:
        return this.toExecuting(call, auxiliaryData);
      // ... 其他状态
    }
  }

  // 终结调用
  finalizeCall(callId: string): void {
    const call = this.activeCalls.get(callId);
    if (!call) return;

    if (this.isTerminalCall(call)) {
      this._completedBatch.push(call);
      this.activeCalls.delete(callId);
      this.onTerminalCall?.(call); // 回调通知
      this.emitUpdate();
    }
  }

  // 广播状态更新
  private emitUpdate() {
    const snapshot = this.getSnapshot();
    void this.messageBus.publish({
      type: MessageBusType.TOOL_CALLS_UPDATE,
      toolCalls: snapshot,
      schedulerId: this.schedulerId,
    });
  }
}
```

### 4.4 状态转换实现

```typescript
// 转换为执行中状态
private toExecuting(call: ToolCall, data?: unknown): ExecutingToolCall {
  this.validateHasToolAndInvocation(call, CoreToolCallStatus.Executing);

  // 合并增量更新
  const execData = data as Partial<ExecutingToolCall> | undefined;

  return {
    request: call.request,
    tool: call.tool,
    status: CoreToolCallStatus.Executing,
    startTime: 'startTime' in call ? call.startTime : undefined,
    outcome: call.outcome,
    invocation: call.invocation,
    // 增量数据
    liveOutput: execData?.liveOutput ?? ('liveOutput' in call ? call.liveOutput : undefined),
    pid: execData?.pid ?? ('pid' in call ? call.pid : undefined),
    progressMessage: execData?.progressMessage,
    progressPercent: execData?.progressPercent,
    schedulerId: call.schedulerId,
    approvalMode: call.approvalMode,
  };
}
```

## 5. 工具执行器

### 5.1 ToolExecutor 架构

```typescript
export class ToolExecutor {
  constructor(private readonly context: AgentLoopContext) {}

  async execute(context: ToolExecutionContext): Promise<CompletedToolCall> {
    const { call, signal, outputUpdateHandler, onUpdateToolCall } = context;
    const { request } = call;
    const toolName = request.name;
    const callId = request.callId;

    return runInDevTraceSpan(
      {
        operation: GeminiCliOperation.ToolCall,
        attributes: {
          [GEN_AI_TOOL_NAME]: toolName,
          [GEN_AI_TOOL_CALL_ID]: callId,
        },
      },
      async ({ metadata: spanMetadata }) => {
        spanMetadata.input = request;

        let completedToolCall: CompletedToolCall;

        try {
          // 1. 设置执行 ID 回调
          const setExecutionIdCallback = (executionId: number) => {
            const executingCall: ExecutingToolCall = {
              ...call,
              status: CoreToolCallStatus.Executing,
              pid: executionId,
            };
            onUpdateToolCall(executingCall);
          };

          // 2. 执行工具（带 Hooks）
          const promise = executeToolWithHooks(
            invocation,
            toolName,
            signal,
            tool,
            liveOutputCallback,
            shellExecutionConfig,
            setExecutionIdCallback,
            this.config,
            request.originalRequestName,
          );

          // 3. 等待结果
          const toolResult: ToolResult = await promise;

          // 4. 构建完成结果
          if (signal.aborted) {
            completedToolCall = await this.createCancelledResult(
              call,
              'Cancelled',
              toolResult,
            );
          } else if (toolResult.error === undefined) {
            completedToolCall = await this.createSuccessResult(
              call,
              toolResult,
            );
          } else {
            completedToolCall = this.createErrorResult(call, toolResult.error);
          }
        } catch (executionError) {
          // 异常处理
          completedToolCall = this.handleExecutionError(
            call,
            executionError,
            signal,
          );
        }

        spanMetadata.output = completedToolCall;
        return completedToolCall;
      },
    );
  }
}
```

### 5.2 实时输出处理

```typescript
// 设置实时输出回调
const liveOutputCallback =
  tool.canUpdateOutput && outputUpdateHandler
    ? (outputChunk: ToolLiveOutput) => {
        outputUpdateHandler(callId, outputChunk);
      }
    : undefined;

// 在 StateManager 中更新
this.state.updateStatus(callId, CoreToolCallStatus.Executing, {
  liveOutput: out, // 增量更新
});
```

### 5.3 输出截断处理

```typescript
private async truncateOutputIfNeeded(
  call: ToolCall,
  content: PartListUnion,
): Promise<{ truncatedContent: PartListUnion; outputFile?: string }> {
  const toolName = call.request.name;
  const callId = call.request.callId;

  // 仅对 shell 工具和 MCP 工具进行截断
  if (typeof content === 'string' && toolName === SHELL_TOOL_NAME) {
    const threshold = this.config.getTruncateToolOutputThreshold();

    if (threshold > 0 && content.length > threshold) {
      // 保存完整输出到文件
      const { outputFile: savedPath } = await saveTruncatedToolOutput(
        content,
        toolName,
        callId,
        this.config.storage.getProjectTempDir(),
        this.context.promptId,
      );

      // 截断内容返回给 LLM
      const truncatedContent = formatTruncatedToolOutput(
        content,
        outputFile,
        threshold,
      );

      return { truncatedContent, outputFile: savedPath };
    }
  }

  return { truncatedContent: content };
}
```

## 6. 取消机制

### 6.1 AbortSignal 模式

```typescript
// 全局取消
private _enqueueRequest(
  requests: ToolCallRequestInfo[],
  signal: AbortSignal,
): Promise<CompletedToolCall[]> {
  return new Promise<CompletedToolCall[]>((resolve, reject) => {
    // 监听取消信号
    const abortHandler = () => {
      const index = this.requestQueue.findIndex(
        (item) => item.requests === requests,
      );
      if (index > -1) {
        this.requestQueue.splice(index, 1);
        reject(new Error('Tool call cancelled while in queue.'));
      }
    };

    if (signal.aborted) {
      reject(new Error('Operation cancelled'));
      return;
    }

    signal.addEventListener('abort', abortHandler, { once: true });

    this.requestQueue.push({
      requests,
      signal,
      resolve: (results) => {
        signal.removeEventListener('abort', abortHandler);
        resolve(results);
      },
      reject: (err) => {
        signal.removeEventListener('abort', abortHandler);
        reject(err);
      },
    });
  });
}
```

### 6.2 批量取消

```typescript
cancelAll(): void {
  if (this.isCancelling) return;
  this.isCancelling = true;

  // 1. 清空请求队列
  while (this.requestQueue.length > 0) {
    const next = this.requestQueue.shift();
    next?.reject(new Error('Operation cancelled by user'));
  }

  // 2. 取消活跃调用
  const activeCalls = this.state.allActiveCalls;
  for (const activeCall of activeCalls) {
    if (!this.isTerminal(activeCall.status)) {
      this.state.updateStatus(
        activeCall.request.callId,
        CoreToolCallStatus.Cancelled,
        'Operation cancelled by user',
      );
    }
  }

  // 3. 清空等待队列
  this.state.cancelAllQueued('Operation cancelled by user');
}
```

### 6.3 级联取消

```typescript
// Tail Call 替换
if (result.tailToolCallRequest) {
  // 如果原始调用被取消，新调用也应该被取消
  if (signal.aborted) {
    this.state.updateStatus(callId, CoreToolCallStatus.Cancelled, 'Cancelled');
    return;
  }

  // 替换为新的工具调用
  const newRequest: ToolCallRequestInfo = {
    callId: originalCallId,
    name: tailRequest.name,
    args: tailRequest.args,
    schedulerId: this.schedulerId,
  };

  const validatingCall = this._validateAndCreateToolCall(
    newRequest,
    newTool,
    activeCall.approvalMode ?? this.config.getApprovalMode(),
  );

  this.state.replaceActiveCallWithTailCall(callId, validatingCall);
}
```

## 7. 性能优化

### 7.1 队列微任务调度

```typescript
// 等待外部事件时让出事件循环
if (isWaitingForExternal && this.state.isActive) {
  await new Promise((resolve) => queueMicrotask(() => resolve(true)));
  return true;
}
```

**设计意图**:

- `queueMicrotask` 比 `setTimeout(0)` 更快
- 允许 I/O 事件和 UI 更新优先处理
- 避免阻塞事件循环

### 7.2 状态变更批处理

```typescript
// 一次性处理所有验证中的调用
await Promise.all(
  validatingCalls.map((c) => this._processValidatingCall(c, signal)),
);

// 一次性执行所有已调度的调用
await Promise.all(scheduledCalls.map((c) => this._execute(c, signal)));
```

### 7.3 事件背压处理

```typescript
// CoreEventEmitter 中的环形缓冲区
private _eventBacklog: EventBacklogItem[] = [];
private _backlogHead = 0;

private _emitOrQueue<K extends keyof CoreEvents>(event: K, ...args: CoreEvents[K]): void {
  if (this.listenerCount(event) === 0) {
    // 使用 head 指针管理队列，避免 O(n) 的 shift 操作
    const backlogSize = this._eventBacklog.length - this._backlogHead;
    if (backlogSize >= CoreEventEmitter.MAX_BACKLOG_SIZE) {
      (this._eventBacklog as unknown[])[this._backlogHead] = undefined;
      this._backlogHead++;

      // 死条目超过一半时压缩
      if (this._backlogHead >= CoreEventEmitter.MAX_BACKLOG_SIZE / 2) {
        this._eventBacklog = this._eventBacklog.slice(this._backlogHead);
        this._backlogHead = 0;
      }
    }
    this._eventBacklog.push({ event, args } as EventBacklogItem);
  } else {
    this.emit(event, ...args);
  }
}
```

### 7.4 内存优化

```typescript
// 及时清理已完成批次
get completedCalls(): CompletedToolCall[] {
  return this.state.completedBatch;  // 返回副本，保护内部状态
}

// 批次完成后清空
finally {
  this.state.clearBatch();  // 释放内存
  this._processNextInRequestQueue();  // 处理下一个批次
}
```

## 8. 最佳实践与设计模式

### 8.1 异步资源管理

```typescript
// 使用 try-finally 确保资源释放
private async _startBatch(requests: ToolCallRequestInfo[], signal: AbortSignal): Promise<CompletedToolCall[]> {
  this.isProcessing = true;

  try {
    // 执行业务逻辑
    await this._processQueue(signal);
    return this.state.completedBatch;
  } finally {
    this.isProcessing = false;
    this.state.clearBatch();
    this._processNextInRequestQueue();
  }
}
```

### 8.2 不可变更新

```typescript
// 使用展开运算符创建新对象
private patchCall<T extends ToolCall>(call: T, patch: Partial<T>): T {
  return { ...call, ...patch };  // 不可变更新
}

// 状态转换示例
private toSuccess(call: ToolCall, response: ToolCallResponseInfo): SuccessfulToolCall {
  return {
    request: call.request,
    tool: call.tool,
    status: CoreToolCallStatus.Success,
    response,
    // ... 其他字段
  };
}
```

### 8.3 类型安全的状态机

```typescript
// 使用 TypeScript 的 tagged union
type ToolCall =
  | ValidatingToolCall
  | ScheduledToolCall
  | ExecutingToolCall
  | SuccessfulToolCall
  | ErroredToolCall
  | CancelledToolCall
  | WaitingToolCall;

// 类型守卫
private isTerminalCall(call: ToolCall): call is CompletedToolCall {
  return (
    call.status === CoreToolCallStatus.Success ||
    call.status === CoreToolCallStatus.Error ||
    call.status === CoreToolCallStatus.Cancelled
  );
}
```

### 8.4 依赖注入

```typescript
export interface SchedulerOptions {
  context: AgentLoopContext; // 运行时上下文
  messageBus?: MessageBus; // 消息总线（可选，默认使用 context 的）
  getPreferredEditor: () => EditorType | undefined;
  schedulerId: string; // 调度器唯一 ID
  subagent?: string; // 子代理标识
  parentCallId?: string; // 父调用 ID（用于调用链追踪）
  onWaitingForConfirmation?: (waiting: boolean) => void;
}

export class Scheduler {
  constructor(options: SchedulerOptions) {
    this.context = options.context;
    this.config = this.context.config;
    this.messageBus = options.messageBus ?? this.context.messageBus;
    // ... 其他初始化
  }
}
```

## 9. 总结

Gemini CLI 的 Runtime 运行时管理采用了成熟的事件驱动架构：

### 核心特点

1. **三阶段管道**: Ingestion → Processing → Execution 的清晰职责划分
2. **状态机驱动**: 严格的状态转换确保调用生命周期可控
3. **智能并发**: 支持并行/串行混合执行，由 AI 控制依赖关系
4. **优雅取消**: 基于 AbortSignal 的全链路取消支持
5. **高性能**: 环形缓冲区、微任务调度、批量处理等优化手段

### 适用场景

- 需要协调多个异步工具调用的 AI 应用
- 对执行顺序有复杂依赖的自动化工作流
- 需要实时反馈和取消能力的交互式系统
- 高并发的工具执行环境

### 可扩展性

- 通过 `AgentLoopContext` 支持依赖注入
- 通过 `MessageBus` 解耦 UI 与核心逻辑
- 通过 `ToolExecutor` 抽象支持自定义执行策略
- 通过状态机模式支持新的工具调用状态

该设计为构建复杂、可靠、高性能的 AI 工具调度系统提供了优秀的参考实现。
