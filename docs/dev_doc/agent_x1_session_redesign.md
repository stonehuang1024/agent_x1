# Session 模块重构需求文档

## 引言

Agent X1 当前的 Session 模块存在以下核心问题：

1. **新旧双架构并行**：`src/core/session_manager.py`（旧版，负责文件日志）和 `src/session/`（新版，负责 SQLite 持久化）两套系统并存，职责重叠且耦合不清。`AgentLoop` 同时依赖两者，增加了维护复杂度。
2. **缺乏 Session/Turn 分层**：当前 `Turn` 仅是消息记录，没有 Codex 中 `TurnContext`（每轮执行快照）的概念，无法保证轮次内配置一致性。
3. **缺乏 JSONL Transcript 持久化**：Claude Code 使用 JSONL 格式记录完整对话历史，支持流式追加和高效恢复。当前系统仅依赖 SQLite，缺乏文件级 transcript。
4. **恢复机制不完善**：缺少 Claude Code 的 `--continue`（恢复最近 session）和 `--resume`（恢复指定 session）能力。
5. **Session 索引缺失**：没有 Claude Code 的 `sessions-index.json` 机制，无法快速查找和列出历史 session。
6. **Context Compaction 与 Session 脱节**：上下文压缩逻辑在 `ContextAssembler` 中，但压缩事件和状态未与 Session 生命周期关联。
7. **多 Agent 扩展性不足**：当前设计未预留子 Agent session 隔离和事件桥接能力。

本次重构以 **Claude Code 的 Session 设计为主要参考**，辅以 **Codex 的 Session/Turn/Execution Loop 设计**中的有效模式，对 Session 模块进行工业级重构升级。

### 设计原则

- **Session 是长生命周期容器**：拥有对话历史、服务句柄、配置快照、Token 预算等跨 Turn 资源（Codex 原则）
- **TurnContext 是每轮执行快照**：冻结模型、工作目录、策略、工具配置等，保证轮次内一致性（Codex 原则）
- **JSONL Transcript 为权威历史**：Prompt 应从已记录的历史构建，而非临时 UI 缓冲区（Codex 原则）
- **语义完成 ≠ 传输完成**：一个 Turn 可能包含多次 LLM 采样（tool call → result → follow-up），Turn 完成由语义循环决定（Codex 原则）
- **显式中间表示优于隐式共享状态**：使用 `Session`、`TurnContext`、`Prompt`、`ResponseItem` 等显式对象（Codex 原则）
- **预留多 Agent 扩展**：Session 支持 parent/child 关系和事件桥接，但本期不实现多 Agent 能力

---


## 需求

### 需求 1：统一 Session 管理器，消除双架构

**用户故事：** 作为一名开发者，我希望系统只有一个统一的 Session 管理入口，以便消除新旧两套 Session 系统的混乱和维护负担。

#### 验收标准

1. WHEN 系统启动 THEN SessionManager SHALL 成为唯一的 Session 管理入口，`src/core/session_manager.py` 的旧版 `SessionManager` 的功能（LLM 交互日志、操作步骤记录、Session 摘要生成）SHALL 被合并到新的统一 SessionManager 中。
2. WHEN AgentLoop 需要记录 LLM 交互 THEN AgentLoop SHALL 仅通过统一 SessionManager 的 API 进行，不再直接依赖旧版 `get_session_manager()`。
3. WHEN 旧版 SessionManager 的功能完全迁移后 THEN `src/core/session_manager.py` SHALL 被标记为 deprecated 并最终移除。
4. IF 外部模块仍引用旧版 SessionManager THEN 系统 SHALL 提供兼容适配层，在过渡期内保持向后兼容。

### 需求 2：Session 生命周期状态机增强

**用户故事：** 作为一名开发者，我希望 Session 拥有完整的生命周期状态机，包含 Compacting 状态和严格的状态转换规则，以便系统行为可预测且可调试。

#### 验收标准

1. WHEN Session 被创建 THEN Session SHALL 处于 `CREATED` 状态，且包含完整的配置快照（provider、model、temperature、max_tokens、working_dir）。
2. WHEN 第一条用户消息到达 THEN Session SHALL 自动转换为 `ACTIVE` 状态。
3. WHEN 用户退出（正常退出或 Ctrl+C）THEN Session SHALL 转换为 `PAUSED` 状态，保留完整的恢复信息。
4. WHEN 用户使用 `--continue` 恢复 THEN Session SHALL 从 `PAUSED` 转换回 `ACTIVE`，恢复完整的对话上下文。
5. WHEN 用户使用 `--resume <session_id>` 恢复 THEN 指定的 Session SHALL 从 `PAUSED`/`COMPLETED` 转换为 `ACTIVE`。
6. WHEN Context Window 接近上限触发压缩 THEN Session SHALL 转换为 `COMPACTING` 状态，压缩完成后回到 `ACTIVE`。
7. WHEN Session 被 Fork THEN 原 Session SHALL 保持当前状态不变（不再强制转为 FORKED），新 Session SHALL 以 `ACTIVE` 状态创建并记录 `parent_id`。
8. WHEN 尝试非法状态转换（如从 `ARCHIVED` 转为 `ACTIVE`）THEN SessionManager SHALL 抛出 `InvalidStateTransition` 异常。
9. WHEN 任何状态转换发生 THEN SessionManager SHALL 通过 EventBus 发射对应事件，并记录转换日志。

### 需求 3：TurnContext — 每轮执行快照

**用户故事：** 作为一名开发者，我希望每个 Turn 拥有独立的执行上下文快照（TurnContext），以便在 Turn 执行期间配置不会被外部修改所影响，保证轮次内一致性。

#### 验收标准

1. WHEN 一个新 Turn 开始 THEN 系统 SHALL 创建一个 `TurnContext` 对象，冻结以下信息：当前工作目录、模型/Provider 选择、工具配置、审批策略、行为设置。
2. WHEN Turn 执行期间 Session 级配置发生变更 THEN 当前 Turn 的 `TurnContext` SHALL 不受影响，变更仅在下一个 Turn 生效。
3. WHEN Turn 包含多次 LLM 采样（tool call → result → follow-up）THEN 所有采样 SHALL 共享同一个 `TurnContext`。
4. WHEN Turn 完成 THEN `TurnContext` SHALL 记录本轮的 Token 使用量、工具调用记录、延迟统计等元数据。
5. IF Turn 执行失败 THEN `TurnContext` SHALL 记录错误信息，且不影响 Session 级状态的完整性。

### 需求 4：JSONL Transcript 持久化

**用户故事：** 作为一名开发者，我希望 Session 的完整对话历史以 JSONL 格式持久化到文件，以便支持流式追加、高效恢复和离线分析。

#### 验收标准

1. WHEN Session 创建 THEN 系统 SHALL 在 Session 目录下创建 `{session_id}.jsonl` 文件。
2. WHEN 任何消息（user/assistant/tool/system）被记录 THEN 系统 SHALL 立即以 JSON 行格式追加到 JSONL 文件，每行包含：`type`（message_type）、`role`、`content`、`tool_calls`、`tool_call_id`、`timestamp`、`turn_number`、`token_count`、`metadata`。
3. WHEN Session 恢复 THEN 系统 SHALL 从 JSONL 文件重建完整的对话历史，而非仅依赖 SQLite。
4. WHEN JSONL 文件损坏（部分行无效）THEN 系统 SHALL 跳过无效行并记录警告，尽最大努力恢复有效历史。
5. WHEN Session 目录被清理 THEN JSONL 文件 SHALL 作为 Session 的权威历史记录被保留（直到显式删除）。
6. IF SQLite 和 JSONL 数据不一致 THEN JSONL SHALL 被视为权威数据源。

### 需求 5：Session 索引与快速查找

**用户故事：** 作为一名开发者，我希望系统维护一个 Session 索引文件，以便快速列出、搜索和恢复历史 Session，而无需扫描数据库。

#### 验收标准

1. WHEN Session 创建/更新/关闭 THEN 系统 SHALL 更新 `sessions-index.json` 文件，包含：`session_id`、`name`、`status`、`created_at`、`updated_at`、`turn_count`、`preview`（最近用户消息摘要）、`working_dir`、`session_dir`。
2. WHEN 用户请求列出 Session THEN 系统 SHALL 优先从索引文件读取，而非查询数据库。
3. WHEN 索引文件与数据库不一致 THEN 系统 SHALL 提供 `rebuild_index()` 方法从数据库重建索引。
4. WHEN 系统启动 THEN 系统 SHALL 验证索引文件的完整性，如有损坏则自动重建。

### 需求 6：Session 恢复机制（Continue / Resume）

**用户故事：** 作为一名开发者，我希望能够通过 `--continue` 恢复最近的 Session 或通过 `--resume <id>` 恢复指定 Session，以便在中断后无缝继续工作。

#### 验收标准

1. WHEN 用户使用 `--continue` 启动 THEN 系统 SHALL 自动找到最近一个 `PAUSED` 或 `ACTIVE` 状态的 Session 并恢复。
2. WHEN 用户使用 `--resume <session_id>` 启动 THEN 系统 SHALL 恢复指定 Session，从 JSONL transcript 重建对话历史。
3. WHEN 恢复 Session 时 THEN 系统 SHALL 重建 Context（包括 system prompt、CLAUDE.md、conversation history），并通知用户恢复的 Turn 数量和 Token 使用情况。
4. IF 恢复的 Session 的配置快照与当前配置不同 THEN 系统 SHALL 发出警告，但仍允许恢复（使用当前配置）。
5. WHEN 恢复失败（Session 不存在或数据损坏）THEN 系统 SHALL 给出清晰的错误信息并建议创建新 Session。

### 需求 7：LLM 交互日志集成

**用户故事：** 作为一名开发者，我希望 LLM 交互日志（原 `session_llm.md` 功能）被集成到统一的 Session 管理中，以便所有 Session 相关的日志和记录都在一个地方管理。

#### 验收标准

1. WHEN LLM 调用完成 THEN SessionManager SHALL 记录完整的交互信息：iteration、messages、tools、response、usage（input/output tokens）、duration_ms、stop_reason。
2. WHEN Session 结束 THEN SessionManager SHALL 自动生成 Session 摘要，包含：总 LLM 调用次数、总 Token 使用量、总时长、操作步骤列表。
3. WHEN Session 摘要生成 THEN 摘要 SHALL 同时写入 Session 目录（`session_summary.md`）和全局历史文件（`history_session.md`）。
4. WHEN 记录 LLM 交互 THEN 系统 SHALL 同时写入 JSONL transcript（结构化）和 `session_llm.md`（人类可读 Markdown）。

### 需求 8：Token 预算管理增强

**用户故事：** 作为一名开发者，我希望 Token 预算管理更加精确和智能，以便系统能在接近上下文窗口限制时自动采取措施。

#### 验收标准

1. WHEN Turn 完成 THEN TokenBudget SHALL 精确更新 `used` 字段，基于 LLM 返回的实际 usage 数据。
2. WHEN Token 使用率超过 80% THEN 系统 SHALL 发出警告事件 `TOKEN_BUDGET_WARNING`。
3. WHEN Token 使用率超过 90% THEN 系统 SHALL 自动触发 Context Compaction，Session 进入 `COMPACTING` 状态。
4. WHEN Compaction 完成 THEN TokenBudget SHALL 更新为压缩后的实际使用量，Session 回到 `ACTIVE` 状态。
5. IF Token 预算完全耗尽且 Compaction 无法释放足够空间 THEN 系统 SHALL 通知用户并建议创建新 Session。

### 需求 9：Turn Diff Tracking（轮次级变更追踪）

**用户故事：** 作为一名开发者，我希望每个 Turn 的文件变更被独立追踪，以便用户能看到每轮对话产生的完整文件变更摘要。

#### 验收标准

1. WHEN Turn 开始 THEN 系统 SHALL 创建一个 Turn 级别的 `DiffTracker` 实例。
2. WHEN Turn 内的工具调用修改了文件 THEN DiffTracker SHALL 记录所有文件变更（创建、修改、删除、重命名）。
3. WHEN Turn 完成 THEN DiffTracker SHALL 生成本轮的变更摘要，包含：变更文件列表、每个文件的 diff 统计（+/- 行数）。
4. WHEN Turn 的变更摘要生成 THEN 摘要 SHALL 被记录到 JSONL transcript 和 Turn 元数据中。
5. IF 同一文件在一个 Turn 内被多次修改 THEN DiffTracker SHALL 聚合为一个统一的 diff，而非多个碎片化的 diff。

### 需求 10：多 Agent Session 扩展预留

**用户故事：** 作为一名开发者，我希望 Session 架构预留多 Agent 扩展能力，以便未来可以支持子 Agent 的 Session 隔离和事件桥接，而无需重构核心架构。

#### 验收标准

1. WHEN Session 数据模型设计 THEN Session SHALL 包含 `parent_id` 字段，支持 parent/child 层级关系。
2. WHEN Session 数据模型设计 THEN Session SHALL 包含 `agent_id` 字段（可选），标识拥有该 Session 的 Agent。
3. WHEN Session 数据模型设计 THEN Session SHALL 包含 `session_type` 字段，区分 `primary`（主 Session）和 `delegated`（子 Agent Session）。
4. WHEN EventBus 事件设计 THEN 系统 SHALL 预留 `SUBAGENT_SESSION_CREATED`、`SUBAGENT_SESSION_COMPLETED` 事件类型。
5. IF 当前版本不实现多 Agent 能力 THEN 以上字段和事件 SHALL 有合理的默认值，不影响单 Agent 使用。

### 需求 11：Session 目录结构规范化

**用户故事：** 作为一名开发者，我希望 Session 目录结构清晰规范，以便所有 Session 产出物都有明确的存放位置。

#### 验收标准

1. WHEN Session 创建 THEN 系统 SHALL 创建以下目录结构：
   ```
   results/session/{session_name}/
   ├── transcript.jsonl          # 完整对话 transcript（权威历史）
   ├── session_llm.md            # 人类可读 LLM 交互日志
   ├── session_activity.md       # Activity Stream 日志
   ├── session_log.jsonl         # 结构化事件日志
   ├── session_summary.md        # Session 摘要（结束时生成）
   ├── diffs/                    # Turn 级别的 diff 文件
   │   ├── turn_001.diff
   │   └── turn_002.diff
   └── artifacts/                # Session 产出物（工具生成的文件）
   ```
2. WHEN Session 目录创建 THEN 系统 SHALL 确保目录路径不包含特殊字符，使用 `{name}_{timestamp}` 格式。
3. WHEN Session 结束 THEN 所有日志文件 SHALL 被正确关闭和刷新。

### 需求 12：数据库 Schema 升级

**用户故事：** 作为一名开发者，我希望数据库 Schema 支持新的 Session 功能（TurnContext、agent_id、session_type 等），以便持久化层与业务模型保持一致。

#### 验收标准

1. WHEN 数据库初始化 THEN 系统 SHALL 执行 Schema 迁移，添加新字段：`sessions` 表增加 `agent_id`、`session_type`、`transcript_path` 字段。
2. WHEN 数据库初始化 THEN 系统 SHALL 创建 `turn_contexts` 表，存储每轮的 TurnContext 快照。
3. WHEN Schema 迁移执行 THEN 系统 SHALL 保持向后兼容，已有数据不受影响。
4. WHEN 迁移脚本执行 THEN 系统 SHALL 记录迁移版本号，防止重复执行。

### 需求 13：完整的单元测试和集成测试

**用户故事：** 作为一名开发者，我希望重构后的 Session 模块有完整的测试覆盖，以便确保功能正确性和回归安全。

#### 验收标准

1. WHEN 编写测试 THEN 测试 SHALL 遵循项目的 TESTING_GUIDELINES.md 规范，测试应暴露 bug 而非确认实现正确。
2. WHEN 编写单元测试 THEN 测试 SHALL 覆盖以下模块：
   - Session 数据模型（序列化/反序列化、状态转换验证）
   - TurnContext 创建和冻结语义
   - SessionManager 生命周期操作（create、resume、pause、complete、fail、archive）
   - JSONL Transcript 读写和恢复
   - Session 索引的 CRUD 和一致性
   - TokenBudget 计算和阈值触发
   - DiffTracker 变更聚合
   - SessionStore 数据库操作
3. WHEN 编写集成测试 THEN 测试 SHALL 覆盖：
   - 完整的 Session 生命周期（创建 → 多轮对话 → 暂停 → 恢复 → 完成）
   - Session Fork 和 Checkpoint 恢复
   - Context Compaction 触发和恢复
   - AgentLoop 与 SessionManager 的集成
   - 并发安全性（多线程访问同一 Session）
4. WHEN 测试执行 THEN 所有测试 SHALL 可独立运行，不依赖外部服务（LLM API、网络等）。
5. WHEN 测试执行 THEN 测试 SHALL 使用临时数据库和临时目录，不污染生产数据。


# 实施计划

## Phase 1：数据模型层（无外部依赖，纯数据结构）

- [ ] 1. 重构 Session 数据模型与状态机（`src/session/models.py`）

  - [ ] 1.1 新增 `SessionType` 枚举和 `TurnStatus` 枚举
    - `SessionType`：`PRIMARY = "primary"`, `DELEGATED = "delegated"`
    - `TurnStatus`：`RUNNING = "running"`, `COMPLETED = "completed"`, `FAILED = "failed"`
    - _需求：10.3_

  - [ ] 1.2 移除 `SessionStatus.FORKED`，保留其余 7 个状态
    - 当前 `SessionStatus` 包含 `FORKED = "forked"`，需移除
    - 保留：`CREATED`, `ACTIVE`, `PAUSED`, `COMPACTING`, `COMPLETED`, `FAILED`, `ARCHIVED`
    - _需求：2.7_

  - [ ] 1.3 新增 `InvalidStateTransition` 异常类
    - 继承 `Exception`，包含 `from_status`、`to_status`、`session_id` 属性
    - `__str__` 返回 `"Invalid transition: {from_status} → {to_status} for session {session_id}"`
    - _需求：2.8_

  - [ ] 1.4 实现严格的状态转换矩阵，在 `Session` 模型中添加 `validate_transition(new_status)` 方法
    - 定义 `VALID_TRANSITIONS: Dict[SessionStatus, Set[SessionStatus]]` 类级常量：
      ```
      CREATED   → {ACTIVE, FAILED, ARCHIVED}
      ACTIVE    → {PAUSED, COMPACTING, COMPLETED, FAILED, ARCHIVED}
      PAUSED    → {ACTIVE, COMPLETED, FAILED, ARCHIVED}
      COMPACTING→ {ACTIVE, FAILED}
      COMPLETED → {ACTIVE, ARCHIVED}       # ACTIVE 用于 --resume 恢复
      FAILED    → {ACTIVE, ARCHIVED}       # ACTIVE 用于重试
      ARCHIVED  → {}                        # 终态，不可转换
      ```
    - `validate_transition(new_status)` 方法：若 `new_status` 不在 `VALID_TRANSITIONS[self.status]` 中，抛出 `InvalidStateTransition(self.status, new_status, self.id)`
    - 同状态转换（`status == new_status`）视为无操作，不抛异常但返回 `False`
    - _需求：2.1-2.9_

  - [ ] 1.5 为 `Session` 模型增加多 Agent 扩展字段
    - `agent_id: Optional[str] = None` — 拥有该 Session 的 Agent 标识
    - `session_type: SessionType = SessionType.PRIMARY` — 区分主/委托 Session
    - `transcript_path: str = ""` — JSONL transcript 文件路径
    - 更新 `to_dict()` 序列化：`session_type` 输出为 `self.session_type.value`
    - 更新 `from_dict()` 反序列化：`session_type` 从字符串恢复为枚举，缺失时默认 `PRIMARY`
    - 确保 `from_dict()` 对旧数据（无 `agent_id`/`session_type`/`transcript_path` 字段）向后兼容
    - _需求：10.1-10.5_

  - [ ] 1.6 新增 `TurnContext` 数据类
    - 配置字段（冻结后不可变）：`turn_number: int`, `working_dir: str`, `model: str`, `provider: str`, `temperature: float`, `max_tokens: int`, `tool_configs: List[str]`, `approval_policy: str`, `behavior_settings: Dict[str, Any]`
    - 运行时字段（冻结后仍可更新）：`token_usage: Dict[str, int]`（含 `input_tokens`, `output_tokens`, `total_tokens`）, `tool_call_records: List[Dict[str, Any]]`, `latency_stats: Dict[str, float]`（含 `total_ms`, `llm_ms`, `tool_ms`）, `error: Optional[str]`, `started_at: float`, `completed_at: Optional[float]`, `status: TurnStatus`
    - `session_id: str` — 关联的 Session ID
    - `_frozen: bool = False` — 内部冻结标志
    - 实现 `freeze()` 方法：设置 `_frozen = True`
    - 实现 `__setattr__` 覆盖：当 `_frozen=True` 时，仅允许修改运行时字段（`token_usage`, `tool_call_records`, `latency_stats`, `error`, `completed_at`, `status`），修改配置字段抛出 `AttributeError("Cannot modify frozen config field: {name}")`
    - 实现 `complete(token_usage, tool_call_records, latency_stats)` 方法：设置 `status=COMPLETED`, `completed_at=time.time()`, 更新运行时字段
    - 实现 `fail(error: str)` 方法：设置 `status=FAILED`, `error=error`, `completed_at=time.time()`
    - 实现 `to_dict()` / `from_dict()` 序列化方法
    - _需求：3.1-3.5_

  - [ ] 1.7 新增 `SessionIndexEntry` 数据类
    - 字段：`session_id: str`, `name: Optional[str]`, `status: str`, `created_at: float`, `updated_at: float`, `turn_count: int`, `preview: str`, `working_dir: str`, `session_dir: str`, `agent_id: Optional[str]`, `session_type: str`
    - 实现 `to_dict()` / `from_dict()` 序列化方法
    - 实现 `from_session(session: Session, preview: str = "") -> SessionIndexEntry` 工厂方法
    - _需求：5.1_

  - [ ] 1.8 更新 `Turn` 模型
    - 增加 `metadata: Dict[str, Any] = field(default_factory=dict)` 字段
    - 更新 `to_dict()` 和 `from_dict()` 以包含 `metadata`
    - _需求：9.4_

  - [ ] 1.9 更新 `TokenBudget` 模型
    - 增加 `warning_threshold: float = 0.8` 和 `compaction_threshold: float = 0.9` 属性
    - 增加 `needs_warning() -> bool` 方法：`utilization_rate >= warning_threshold`
    - 增加 `needs_compaction() -> bool` 方法：`utilization_rate >= compaction_threshold`
    - 增加 `is_exhausted() -> bool` 方法：`available <= 0`
    - 增加 `reset_used(new_used: int)` 方法：用于 Compaction 后重置
    - _需求：8.1-8.5_

## Phase 2：新增独立模块（无 SessionManager 依赖）

- [ ] 2. 新增 JSONL Transcript 引擎（`src/session/transcript.py`）

  - [ ] 2.1 实现 `TranscriptWriter` 类
    - 构造函数接受 `file_path: Union[str, Path]`，以 `'a'` 模式打开文件（追加写入），编码 `utf-8`
    - `append(entry: Dict[str, Any])` 方法：
      - 自动注入 `timestamp` 字段（如果 entry 中没有）为 `time.time()`
      - 使用 `json.dumps(entry, ensure_ascii=False)` 序列化
      - 写入一行（末尾 `\n`）
      - 立即调用 `self._file.flush()` 和 `os.fsync(self._file.fileno())` 确保持久化
    - `close()` 方法：关闭文件句柄，设置 `_closed = True`
    - 实现 `__enter__` / `__exit__` 上下文管理器协议
    - 对已关闭的 writer 调用 `append()` 应抛出 `RuntimeError("TranscriptWriter is closed")`
    - _需求：4.1, 4.2_

  - [ ] 2.2 实现 `TranscriptReader` 类
    - 构造函数接受 `file_path: Union[str, Path]`
    - `read_all() -> List[Dict[str, Any]]` 方法：
      - 逐行读取文件
      - 对每行尝试 `json.loads()`
      - 成功则加入结果列表
      - 失败则记录 `logger.warning(f"Skipping invalid JSONL line {line_number}: {e}")` 并跳过
      - 空行跳过
      - 文件不存在返回空列表（不抛异常）
    - `read_range(start_line: int, end_line: int) -> List[Dict]` 方法：读取指定行范围
    - `count_entries() -> int` 方法：返回有效条目数量
    - _需求：4.3, 4.4_

  - [ ] 2.3 实现 `rebuild_history_from_transcript(path: Union[str, Path]) -> List[Turn]` 函数
    - 使用 `TranscriptReader` 读取所有条目
    - 将每个条目映射为 `Turn` 对象：`role` → `Turn.role`, `content` → `Turn.content`, `tool_calls` → `Turn.tool_calls`, `tool_call_id` → `Turn.tool_call_id`, `turn_number` → `Turn.turn_number`, `token_count` → `Turn.token_count`, `metadata` → `Turn.metadata`
    - 缺失字段使用默认值
    - 返回按 `turn_number` 排序的 Turn 列表
    - _需求：4.3, 6.2_

- [ ] 3. 新增 Session 索引管理器（`src/session/session_index.py`）

  - [ ] 3.1 实现 `SessionIndex` 类核心结构
    - 构造函数接受 `index_path: Union[str, Path]`（默认 `data/sessions-index.json`）
    - 内部维护 `_entries: Dict[str, SessionIndexEntry]`（session_id → entry 映射）
    - 构造时调用 `_load()` 从文件加载索引
    - _需求：5.1_

  - [ ] 3.2 实现索引文件 I/O（带文件锁）
    - `_load()` 方法：读取 JSON 文件，解析为 `SessionIndexEntry` 列表，填充 `_entries`；文件不存在或解析失败时初始化为空
    - `_save()` 方法：将 `_entries` 序列化为 JSON 写入文件
    - 读写均使用 `fcntl.flock(fd, fcntl.LOCK_EX)` 排他锁（macOS/Linux），写入完成后释放
    - 写入使用原子操作：先写入临时文件 `{path}.tmp`，再 `os.replace()` 覆盖原文件
    - _需求：5.1_

  - [ ] 3.3 实现 CRUD 方法
    - `update(entry: SessionIndexEntry)` → 更新或插入，调用 `_save()`
    - `remove(session_id: str)` → 从 `_entries` 中删除，调用 `_save()`
    - `get(session_id: str) -> Optional[SessionIndexEntry]` → 查找单个条目
    - `list_all(status: Optional[str] = None) -> List[SessionIndexEntry]` → 返回所有条目（可按 status 过滤），按 `updated_at` 降序排列
    - `get_latest(status: Optional[str] = None) -> Optional[SessionIndexEntry]` → 返回最近更新的指定状态 Session
    - _需求：5.1, 5.2, 6.1_

  - [ ] 3.4 实现重建和验证
    - `rebuild_from_store(store: SessionStore)` 方法：清空 `_entries`，从 `store.list_sessions()` 读取所有 Session，为每个创建 `SessionIndexEntry` 并填充，调用 `_save()`
    - `validate() -> bool` 方法：检查索引文件是否存在、是否可解析、条目数是否合理；失败时记录 WARNING 并返回 `False`
    - _需求：5.3, 5.4_

- [ ] 4. 新增 DiffTracker（`src/session/diff_tracker.py`）

  - [ ] 4.1 实现 `ChangeType` 枚举和 `FileChange` 数据类
    - `ChangeType`：`CREATED = "created"`, `MODIFIED = "modified"`, `DELETED = "deleted"`, `RENAMED = "renamed"`
    - `FileChange`：`path: str`, `change_type: ChangeType`, `lines_added: int = 0`, `lines_removed: int = 0`, `old_path: Optional[str] = None`
    - _需求：9.1_

  - [ ] 4.2 实现 `DiffTracker` 类
    - 内部维护 `_changes: Dict[str, FileChange]`（path → FileChange 映射）
    - `record_change(path, change_type, lines_added=0, lines_removed=0, old_path=None)` 方法：
      - 若 `path` 已存在于 `_changes`：累加 `lines_added` 和 `lines_removed`，`change_type` 取最新值（但 `CREATED` + `MODIFIED` = `CREATED`，`CREATED` + `DELETED` = 移除记录）
      - 若 `path` 不存在：创建新 `FileChange` 并加入
      - 对于 `RENAMED`：移除 `old_path` 的记录（如果存在），创建新路径的记录
    - `get_changes() -> List[FileChange]` 方法：返回所有变更列表
    - `get_summary() -> Dict[str, Any]` 方法：返回 `{"files_changed": int, "total_additions": int, "total_deletions": int, "changes": [FileChange.to_dict()]}`
    - `save_diff(output_dir: Path, turn_number: int)` 方法：将摘要写入 `output_dir/turn_{turn_number:03d}.diff` 文件
    - `reset()` 方法：清空所有变更记录
    - _需求：9.1-9.5_

## Phase 3：数据库层升级

- [ ] 5. 数据库 Schema 升级与 SessionStore 重构

  - [ ] 5.1 创建迁移脚本 `data/migrations/002_session_refactor.sql`
    - `ALTER TABLE sessions ADD COLUMN agent_id TEXT DEFAULT NULL;`
    - `ALTER TABLE sessions ADD COLUMN session_type TEXT DEFAULT 'primary';`
    - `ALTER TABLE sessions ADD COLUMN transcript_path TEXT DEFAULT '';`
    - 创建 `turn_contexts` 表（完整 DDL 含所有字段、CHECK 约束、外键、索引）
    - 创建 `schema_migrations` 表：`version INTEGER PRIMARY KEY`, `applied_at REAL NOT NULL`, `description TEXT`
    - 注意：SQLite 不支持 `ALTER TABLE ... DROP CONSTRAINT`，`FORKED` 状态在 CHECK 约束中保留但在应用层不再使用
    - _需求：12.1-12.4_

  - [ ] 5.2 重构 `SessionStore._ensure_tables()` 迁移版本管理
    - 创建 `schema_migrations` 表（如不存在）
    - 查询已执行的最大版本号
    - 扫描 `data/migrations/` 目录下所有 `NNN_*.sql` 文件
    - 按版本号顺序执行未执行的迁移脚本
    - 每个迁移执行后插入 `schema_migrations` 记录
    - 使用事务包裹每个迁移脚本的执行
    - _需求：12.4_

  - [ ] 5.3 更新 `SessionStore` 的 Session CRUD 方法
    - `_row_to_session()` 增加读取 `agent_id`、`session_type`、`transcript_path` 字段（使用 `dict(row)` 安全读取，缺失字段用默认值）
    - `create_session()` 的 INSERT 语句增加 `agent_id`、`session_type`、`transcript_path` 字段
    - `update_session()` 的 UPDATE 语句增加 `agent_id`、`session_type`、`transcript_path` 字段
    - _需求：12.1_

  - [ ] 5.4 新增 `SessionStore` 的 TurnContext CRUD 方法
    - `save_turn_context(ctx: TurnContext)` → INSERT INTO `turn_contexts`，JSON 字段使用 `json.dumps()`
    - `update_turn_context(ctx: TurnContext)` → UPDATE `turn_contexts` WHERE `session_id` AND `turn_number`
    - `get_turn_context(session_id: str, turn_number: int) -> Optional[TurnContext]` → SELECT + 反序列化
    - `get_all_turn_contexts(session_id: str) -> List[TurnContext]` → SELECT 所有 + 排序
    - `_row_to_turn_context(row) -> TurnContext` 辅助方法
    - _需求：12.2_

  - [ ] 5.5 更新 `SessionStore` 的 Turn CRUD 方法
    - `add_turn()` 的 INSERT 语句增加 `metadata` 字段（JSON 序列化）
    - `_row_to_turn()` 增加读取 `metadata` 字段（JSON 反序列化，缺失时默认 `{}`）
    - _需求：9.4_

## Phase 4：核心 SessionManager 重构

- [ ] 6. 重构 SessionManager 状态机与核心逻辑（`src/session/session_manager.py`）

  - [ ] 6.1 重构 `_transition()` 方法，集成严格状态验证
    - 调用 `session.validate_transition(new_status)` 进行验证
    - 验证失败时让 `InvalidStateTransition` 异常向上传播（不吞掉）
    - 同状态转换时直接返回（无操作）
    - 成功转换后更新 `session.status`、`session.updated_at`
    - 终态（`COMPLETED`/`FAILED`/`ARCHIVED`）设置 `session.ended_at`
    - 调用 `self.store.update_session(session)` 持久化
    - 调用 `self._notify_state_change(session, old_status, new_status)` 发送事件
    - 调用 `self._update_index(session)` 更新索引
    - _需求：2.8, 2.9_

  - [ ] 6.2 移除 Fork 时标记原 Session 为 FORKED 的逻辑
    - 当前 `create_session()` 中 Fork 分支有 `parent.status = SessionStatus.FORKED`，需移除
    - Fork 后原 Session 保持当前状态不变
    - 新 Session 以 `ACTIVE` 状态创建，记录 `parent_id`
    - _需求：2.7_

  - [ ] 6.3 更新 `_emit_session_event()` 方法
    - 移除 `SessionStatus.FORKED` 的事件映射
    - 增加 `SessionStatus.COMPACTING` → `AgentEvent.SESSION_COMPACTING` 映射
    - _需求：2.9_

  - [ ] 6.4 新增 `begin_turn(session_id: Optional[str] = None) -> TurnContext` 方法
    - 获取 Session（使用 `_get_session(session_id)`）
    - 若 Session 状态为 `CREATED`，自动转换为 `ACTIVE`（第一条消息触发）
    - 创建 `TurnContext`：从 `session.config_snapshot` 冻结配置字段，`turn_number = session.turn_count + 1`，`started_at = time.time()`，`status = TurnStatus.RUNNING`
    - 调用 `turn_context.freeze()` 冻结配置
    - 调用 `self.store.save_turn_context(turn_context)` 持久化
    - 发射 `AgentEvent.TURN_STARTED` 事件
    - 返回 `TurnContext`
    - _需求：2.2, 3.1-3.3_

  - [ ] 6.5 新增 `end_turn(turn_context: TurnContext, diff_summary: Optional[Dict] = None)` 方法
    - 若 `turn_context.error` 不为 None，调用 `turn_context.fail(turn_context.error)`；否则调用 `turn_context.complete(...)`
    - 调用 `self.store.update_turn_context(turn_context)` 持久化
    - 更新 Session 统计：`session.turn_count += 1`，`session.budget.used += turn_context.token_usage.get('total_tokens', 0)`
    - 若 `diff_summary` 不为 None，将其写入 transcript（`type: "diff_summary"`）
    - 发射 `AgentEvent.TURN_COMPLETED` 或 `AgentEvent.TURN_FAILED` 事件
    - **Token 预算检查**：
      - 若 `session.budget.needs_warning()`：发射 `AgentEvent.TOKEN_BUDGET_WARNING` 事件
      - 若 `session.budget.needs_compaction()`：调用 `self._transition(session, SessionStatus.COMPACTING)`，发射 `AgentEvent.SESSION_COMPACTING` 事件
    - 调用 `self.store.update_session(session)` 持久化
    - _需求：3.4, 3.5, 8.1-8.5_

  - [ ] 6.6 新增 `complete_compaction(session_id: Optional[str] = None, new_used: int = 0)` 方法
    - 获取 Session，验证当前状态为 `COMPACTING`
    - 调用 `session.budget.reset_used(new_used)` 重置 Token 使用量
    - 调用 `self._transition(session, SessionStatus.ACTIVE)` 回到 ACTIVE
    - _需求：8.4_

  - [ ] 6.7 在 `create_session()` 中集成 Transcript 和 Index
    - 创建 Session 目录后，初始化 `TranscriptWriter`，设置 `session.transcript_path`
    - 将 `TranscriptWriter` 实例存储在 `self._transcript_writers: Dict[str, TranscriptWriter]` 中
    - 创建 `SessionIndexEntry` 并调用 `self._index.update(entry)` 更新索引
    - _需求：4.1, 5.1_

  - [ ] 6.8 在 `record_turn()` 中同时写入 SQLite 和 JSONL Transcript
    - 现有 SQLite 写入逻辑保持不变
    - 额外调用 `self._get_transcript_writer(session.id).append(...)` 写入 JSONL
    - Transcript entry 包含：`type`, `role`, `content`, `tool_calls`, `tool_call_id`, `timestamp`, `turn_number`, `token_count`, `metadata`
    - _需求：4.2_

  - [ ] 6.9 新增 `continue_session() -> Session` 方法（对应 `--continue`）
    - 调用 `self._index.get_latest(status="paused")` 查找最近的 PAUSED Session
    - 若未找到，调用 `self._index.get_latest(status="active")` 查找 ACTIVE Session
    - 若仍未找到，抛出 `ValueError("No resumable session found. Use --resume <id> or start a new session.")`
    - 找到后调用 `self._resume_internal(session)` 恢复
    - _需求：6.1_

  - [ ] 6.10 新增 `resume_session_by_id(session_id: str) -> Session` 方法（对应 `--resume <id>`）
    - 从 `self.store.get_session(session_id)` 获取 Session
    - 若不存在，抛出 `ValueError(f"Session {session_id} not found")`
    - 调用 `self._resume_internal(session)` 恢复
    - _需求：6.2_

  - [ ] 6.11 实现 `_resume_internal(session: Session) -> Session` 内部恢复方法
    - **Step 1 - 配置检查**：比较 `session.config_snapshot` 与当前 `self.config` 的 provider/model/temperature/max_tokens，不一致时记录 `logger.warning(f"Config mismatch: session used {old}, current is {new}")` 并发射警告事件
    - **Step 2 - 历史重建**：若 `session.transcript_path` 存在且文件可读，调用 `rebuild_history_from_transcript(session.transcript_path)` 重建 Turn 列表；否则从 SQLite `self.store.get_turns(session.id)` 获取
    - **Step 3 - 状态转换**：调用 `self._transition(session, SessionStatus.ACTIVE)` 恢复为 ACTIVE
    - **Step 4 - 重新初始化 Transcript Writer**：为该 Session 创建新的 `TranscriptWriter`（追加模式）
    - **Step 5 - 设置为活跃 Session**：`self._active_session = session`
    - **Step 6 - 日志和通知**：记录 `logger.info(f"Resumed session {session.id[:8]}: {len(turns)} turns, {session.budget.used} tokens used")`
    - 返回 Session
    - _需求：6.2-6.5_

  - [ ] 6.12 新增 `_update_index(session: Session, preview: str = "")` 内部方法
    - 创建 `SessionIndexEntry.from_session(session, preview)` 并调用 `self._index.update(entry)`
    - 在所有状态转换和 `record_turn()` 中调用
    - _需求：5.1_

  - [ ] 6.13 更新 `pause_session()` 方法
    - 当前仅在 `ACTIVE` 状态时暂停，需扩展为 `ACTIVE` 或 `COMPACTING` 状态均可暂停
    - 暂停时关闭 `TranscriptWriter`（flush 并 close）
    - _需求：2.3_

  - [ ] 6.14 更新 `complete_session()` 和 `fail_session()` 方法
    - 关闭 `TranscriptWriter`
    - 关闭 `SessionLogger`（如果已集成）
    - 更新 SessionIndex
    - _需求：2.3, 11.3_

## Phase 5：LLM 交互日志集成

- [ ] 7. 新增 SessionLogger（`src/session/session_logger.py`）

  - [ ] 7.1 实现 `SessionLogger` 类核心结构
    - 构造函数接受 `session_dir: Union[str, Path]`, `session_id: str`, `transcript_writer: Optional[TranscriptWriter] = None`
    - 内部维护：`_llm_file` (session_llm.md 文件句柄), `_activity_file` (session_activity.md 文件句柄), `_llm_calls: List[Dict]` (LLM 调用记录), `_operation_steps: List[str]` (操作步骤), `_start_time: float`
    - 构造时创建文件并写入 header
    - _需求：7.1_

  - [ ] 7.2 实现 `log_llm_interaction()` 方法
    - 参数：`iteration: int`, `messages: List[Dict]`, `tools: List[Dict]`, `response: Dict`, `usage: Dict`, `duration_ms: float`, `stop_reason: str`
    - 写入 `session_llm.md`：格式化为 Markdown（与当前 `src/core/session_manager.py` 的 `log_llm_interaction` 格式一致）
    - 写入 JSONL transcript（通过 `transcript_writer.append()`）：`type: "llm_interaction"`, 包含所有参数
    - 记录到 `_llm_calls` 列表
    - _需求：7.1, 7.4_

  - [ ] 7.3 实现 `record_activity(step: str)` 方法
    - 格式化为 `[HH:MM:SS] step` 写入 `session_activity.md`
    - 记录到 `_operation_steps` 列表
    - _需求：7.1_

  - [ ] 7.4 实现 `generate_summary(session: Session) -> str` 方法
    - 计算总 LLM 调用次数、总 Token 使用量、总时长、操作步骤列表
    - 生成 Markdown 格式摘要
    - 写入 `session_dir/session_summary.md`
    - 追加到全局 `memory_data/history_session.md`
    - 返回摘要文本
    - _需求：7.2, 7.3_

  - [ ] 7.5 实现 `close()` 方法
    - 关闭 `_llm_file` 和 `_activity_file` 文件句柄
    - 设置 `_closed = True`
    - _需求：11.3_

## Phase 6：事件系统扩展与旧版迁移

- [ ] 8. 事件系统扩展与旧版迁移

  - [ ] 8.1 在 `src/core/events.py` 的 `AgentEvent` 枚举中新增事件类型
    - `TOKEN_BUDGET_WARNING = auto()` — Token 预算警告
    - `SESSION_COMPACTING = auto()` — Session 进入压缩状态
    - `SUBAGENT_SESSION_CREATED = auto()` — 子 Agent Session 创建（预留）
    - `SUBAGENT_SESSION_COMPLETED = auto()` — 子 Agent Session 完成（预留）
    - _需求：8.2, 10.4_

  - [ ] 8.2 在 `src/core/session_manager.py` 中添加 deprecation 警告
    - 在 `SessionManager.__init__()` 中添加 `warnings.warn("src.core.session_manager.SessionManager is deprecated, use src.session.SessionManager instead", DeprecationWarning, stacklevel=2)`
    - 在 `init_session_manager()` 和 `get_session_manager()` 中同样添加
    - _需求：1.3, 1.4_

  - [ ] 8.3 创建 Session 目录结构规范化
    - 在 `SessionManager.create_session()` 中创建完整目录结构：
      - `transcript.jsonl` — 由 TranscriptWriter 管理
      - `session_llm.md` — 由 SessionLogger 管理
      - `session_activity.md` — 由 SessionLogger 管理
      - `session_log.jsonl` — 由 StructuredLogger 管理（已存在）
      - `diffs/` — 由 DiffTracker 管理
      - `artifacts/` — 预创建空目录
    - _需求：11.1, 11.2_

  - [ ] 8.4 更新 `src/session/__init__.py` 导出列表
    - 新增导出：`TurnContext`, `TurnStatus`, `SessionType`, `SessionIndexEntry`, `InvalidStateTransition`, `TranscriptWriter`, `TranscriptReader`, `rebuild_history_from_transcript`, `SessionIndex`, `SessionLogger`, `DiffTracker`, `FileChange`, `ChangeType`
    - _需求：1.1_

## Phase 7：AgentLoop 集成

- [ ] 9. 更新 AgentLoop 集成（`src/runtime/agent_loop.py` + `main.py`）

  - [ ] 9.1 移除 AgentLoop 对旧版 `get_session_manager()` 的依赖
    - 移除 `from src.core.session_manager import get_session_manager as get_legacy_session_manager` 导入
    - 移除 `_log_to_session_llm()` 方法（其功能由 SessionLogger 替代）
    - 移除 `_call_llm()` 中对 `_log_to_session_llm()` 的调用
    - _需求：1.2_

  - [ ] 9.2 在 AgentLoop 中集成 TurnContext 生命周期
    - 在 `__init__` 中增加 `self.session_logger: Optional[SessionLogger] = None` 参数
    - 在 `run()` 方法开头调用 `turn_context = self.session_manager.begin_turn()` 获取 TurnContext
    - 在 `run()` 方法的 `finally` 块中调用 `self.session_manager.end_turn(turn_context, diff_summary)` 结束 Turn
    - 在 `_call_llm()` 完成后通过 `self.session_logger.log_llm_interaction(...)` 记录交互
    - 在工具执行结果中检测文件变更，通过 `DiffTracker.record_change()` 记录
    - _需求：3.1-3.5, 7.1, 9.1-9.5_

  - [ ] 9.3 更新 `main.py` 入口支持 Session 恢复
    - 新增 CLI 参数：`--continue`（恢复最近 Session）、`--resume <session_id>`（恢复指定 Session）
    - `--continue` 时调用 `session_manager.continue_session()` 替代 `create_session()`
    - `--resume <id>` 时调用 `session_manager.resume_session_by_id(id)` 替代 `create_session()`
    - 恢复成功后打印恢复信息：Turn 数量、Token 使用情况、配置差异警告
    - 在 `KeyboardInterrupt` 和正常退出时调用 `session_manager.pause_session()` 而非 `end_current_session()`
    - _需求：6.1-6.5_

## Phase 8：单元测试

- [ ] 10. 单元测试 — 数据模型（`tests/unit/test_session/test_models.py`）
  - 遵循 TESTING_GUIDELINES.md 规范，测试应暴露 bug 而非确认实现正确

  - [ ] 10.1 状态转换矩阵测试
    - 测试所有 7 个状态的合法转换（参照 1.4 中的转换表），每个合法转换调用 `validate_transition()` 不抛异常
    - 测试所有非法转换（如 `ARCHIVED → ACTIVE`, `CREATED → COMPLETED`, `COMPACTING → PAUSED` 等），验证抛出 `InvalidStateTransition` 且异常包含正确的 `from_status` 和 `to_status`
    - 测试同状态转换（如 `ACTIVE → ACTIVE`）返回 `False` 且不抛异常
    - 测试 `COMPLETED → ACTIVE` 合法（用于 `--resume` 恢复）
    - 测试 `FAILED → ACTIVE` 合法（用于重试）
    - 边界：对无效的 `new_status`（非 `SessionStatus` 类型）验证行为
    - _需求：2.1-2.9_

  - [ ] 10.2 TurnContext 冻结语义测试
    - 创建 `TurnContext`，冻结前修改配置字段（如 `model`）成功
    - 调用 `freeze()` 后修改配置字段（如 `model`, `working_dir`, `temperature`）抛出 `AttributeError`
    - 冻结后修改运行时字段（如 `token_usage`, `error`, `status`）成功
    - 测试 `complete()` 方法：设置 `status=COMPLETED`, `completed_at` 非 None
    - 测试 `fail()` 方法：设置 `status=FAILED`, `error` 非 None, `completed_at` 非 None
    - 测试 `to_dict()` / `from_dict()` 往返一致性
    - 边界：对已 `COMPLETED` 的 TurnContext 再次调用 `complete()` 的行为
    - _需求：3.1-3.5_

  - [ ] 10.3 Session 序列化测试
    - 测试 `Session.to_dict()` / `Session.from_dict()` 往返一致性（含新字段 `agent_id`, `session_type`, `transcript_path`）
    - 测试旧数据（无新字段）的 `from_dict()` 向后兼容性
    - 测试 `SessionType` 枚举的序列化/反序列化
    - 测试 `SessionIndexEntry.from_session()` 工厂方法
    - 测试 `SessionIndexEntry.to_dict()` / `from_dict()` 往返一致性
    - _需求：10.1-10.5_

  - [ ] 10.4 TokenBudget 边界值测试
    - `total=0` 时 `utilization_rate` 返回 `1.0`，`available` 返回 `0`
    - `used > total` 时 `available` 返回 `0`（不返回负数）
    - `reserved > total` 时 `utilization_rate` 返回 `1.0`
    - `needs_warning()` 在 `utilization_rate == 0.8` 时返回 `True`，`0.79` 时返回 `False`
    - `needs_compaction()` 在 `utilization_rate == 0.9` 时返回 `True`，`0.89` 时返回 `False`
    - `is_exhausted()` 在 `available == 0` 时返回 `True`
    - `reset_used(new_used)` 正确重置 `used` 字段
    - _需求：8.1-8.5_

- [ ] 11. 单元测试 — Transcript（`tests/unit/test_session/test_transcript.py`）

  - [ ] 11.1 TranscriptWriter 测试
    - 写入单条 entry 后文件包含一行有效 JSON
    - 写入多条 entry 后文件包含对应行数的有效 JSON
    - 每次 `append()` 后数据已 flush 到磁盘（通过独立读取文件验证）
    - 自动注入 `timestamp` 字段（entry 中未提供时）
    - 对已关闭的 writer 调用 `append()` 抛出 `RuntimeError`
    - 上下文管理器协议：`with` 块退出后 writer 已关闭
    - 写入包含 Unicode 字符的 entry 正确处理
    - 写入包含换行符的 content 字段不破坏 JSONL 格式（`json.dumps` 会转义）
    - _需求：4.1, 4.2_

  - [ ] 11.2 TranscriptReader 测试
    - 读取有效 JSONL 文件返回正确的条目列表
    - 读取包含无效行的文件：跳过无效行，返回有效条目，记录 WARNING 日志
    - 读取空文件返回空列表
    - 读取不存在的文件返回空列表（不抛异常）
    - 读取包含空行的文件：跳过空行
    - `count_entries()` 返回正确数量
    - `read_range()` 返回指定范围的条目
    - _需求：4.3, 4.4_

  - [ ] 11.3 `rebuild_history_from_transcript()` 测试
    - 从有效 JSONL 文件重建 Turn 列表，字段映射正确
    - 缺失字段使用默认值
    - 返回列表按 `turn_number` 排序
    - 空文件返回空列表
    - 包含无效行的文件：跳过无效行，重建有效 Turn
    - _需求：4.3, 6.2_

- [ ] 12. 单元测试 — Session 索引（`tests/unit/test_session/test_session_index.py`）

  - [ ] 12.1 索引 CRUD 测试
    - `update()` 插入新条目后 `list_all()` 包含该条目
    - `update()` 更新已有条目后字段正确更新
    - `remove()` 删除条目后 `list_all()` 不包含该条目
    - `remove()` 删除不存在的条目不抛异常
    - `get()` 返回正确的条目
    - `get()` 查找不存在的 session_id 返回 None
    - _需求：5.1_

  - [ ] 12.2 索引查询测试
    - `list_all()` 按 `updated_at` 降序排列
    - `list_all(status="paused")` 仅返回 PAUSED 状态的条目
    - `get_latest()` 返回最近更新的条目
    - `get_latest(status="paused")` 返回最近的 PAUSED 条目
    - `get_latest()` 索引为空时返回 None
    - _需求：5.2, 6.1_

  - [ ] 12.3 索引重建和验证测试
    - `rebuild_from_store()` 从 mock SessionStore 重建索引，条目数量和内容正确
    - `validate()` 对有效索引文件返回 True
    - `validate()` 对损坏的 JSON 文件返回 False
    - `validate()` 对不存在的文件返回 False
    - 索引文件损坏后重新 `_load()` 初始化为空（不崩溃）
    - _需求：5.3, 5.4_

- [ ] 13. 单元测试 — DiffTracker（`tests/unit/test_session/test_diff_tracker.py`）

  - [ ] 13.1 变更记录测试
    - 记录单个文件创建，`get_changes()` 返回 1 个 `FileChange`
    - 记录同一文件多次修改，`get_changes()` 返回 1 个聚合的 `FileChange`（lines_added/lines_removed 累加）
    - 记录文件创建后删除，`get_changes()` 返回空列表（创建+删除=无变更）
    - 记录文件重命名，旧路径记录被移除，新路径记录被创建
    - `get_summary()` 返回正确的统计信息
    - `save_diff()` 写入正确格式的 diff 文件
    - `reset()` 后 `get_changes()` 返回空列表
    - _需求：9.1-9.5_

- [ ] 14. 单元测试 — SessionStore（`tests/unit/test_session/test_session_store.py`）

  - [ ] 14.1 新字段读写测试
    - 创建包含 `agent_id`, `session_type`, `transcript_path` 的 Session，读取后字段值正确
    - 创建不包含新字段的 Session（使用默认值），读取后默认值正确
    - 更新 Session 的新字段，读取后更新值正确
    - _需求：12.1_

  - [ ] 14.2 TurnContext CRUD 测试
    - `save_turn_context()` 后 `get_turn_context()` 返回正确的 TurnContext
    - `update_turn_context()` 更新后读取值正确
    - `get_all_turn_contexts()` 返回按 turn_number 排序的列表
    - JSON 字段（`tool_configs`, `behavior_settings`, `token_usage` 等）正确序列化/反序列化
    - _需求：12.2_

  - [ ] 14.3 Schema 迁移测试
    - 全新数据库执行迁移后所有表和字段存在
    - 已有 001 迁移的数据库执行 002 迁移后新字段存在，旧数据不受影响
    - 重复执行迁移不报错（幂等性）
    - `schema_migrations` 表正确记录迁移版本
    - _需求：12.3, 12.4_

  - [ ] 14.4 Turn metadata 字段测试
    - 创建包含 `metadata` 的 Turn，读取后 `metadata` 正确
    - 创建不包含 `metadata` 的 Turn（旧数据），读取后 `metadata` 为空 dict
    - _需求：9.4_

- [ ] 15. 单元测试 — SessionManager（`tests/unit/test_session/test_session_manager.py`）

  - [ ] 15.1 状态转换集成测试
    - 创建 Session → `CREATED`，调用 `begin_turn()` → `ACTIVE`
    - `ACTIVE` → `pause_session()` → `PAUSED`
    - `PAUSED` → `resume_paused()` → `ACTIVE`
    - `ACTIVE` → `complete_session()` → `COMPLETED`
    - `ACTIVE` → `fail_session()` → `FAILED`
    - `COMPLETED` → `archive_session()` → `ARCHIVED`
    - `ARCHIVED` → `resume_session()` 抛出 `InvalidStateTransition`
    - `ACTIVE` → `_transition(COMPACTING)` → `COMPACTING` → `complete_compaction()` → `ACTIVE`
    - 每次转换验证 EventBus 收到正确事件
    - _需求：2.1-2.9_

  - [ ] 15.2 begin_turn / end_turn 生命周期测试
    - `begin_turn()` 返回冻结的 TurnContext，配置字段与 Session 快照一致
    - `begin_turn()` 对 `CREATED` 状态的 Session 自动转换为 `ACTIVE`
    - `end_turn()` 更新 Session 统计（turn_count, budget.used）
    - `end_turn()` 持久化 TurnContext 到数据库
    - `end_turn()` 在 Token 使用率 > 80% 时发射 `TOKEN_BUDGET_WARNING` 事件
    - `end_turn()` 在 Token 使用率 > 90% 时触发 `COMPACTING` 状态转换
    - _需求：3.1-3.5, 8.1-8.5_

  - [ ] 15.3 Session 恢复测试（continue / resume）
    - `continue_session()` 找到最近的 PAUSED Session 并恢复为 ACTIVE
    - `continue_session()` 无 PAUSED Session 时查找 ACTIVE Session
    - `continue_session()` 无可恢复 Session 时抛出 `ValueError`
    - `resume_session_by_id()` 恢复指定 Session，状态变为 ACTIVE
    - `resume_session_by_id()` 从 JSONL transcript 重建对话历史（mock TranscriptReader）
    - `resume_session_by_id()` Session 不存在时抛出 `ValueError`
    - 恢复时配置不匹配发出警告日志
    - 恢复时 JSONL 文件损坏（部分无效行）仍能恢复有效历史
    - _需求：6.1-6.5_

  - [ ] 15.4 Fork 行为测试
    - Fork 后原 Session 状态不变（不再变为 FORKED）
    - Fork 后新 Session 状态为 ACTIVE，`parent_id` 指向原 Session
    - Fork 后新 Session 包含原 Session 的所有 Turn
    - _需求：2.7_

  - [ ] 15.5 record_turn 双写测试
    - `record_turn()` 同时写入 SQLite（通过 store.add_turn 验证）和 JSONL（通过 mock TranscriptWriter 验证）
    - `record_turn()` 更新 SessionIndex
    - _需求：4.2, 5.1_

- [ ] 16. 单元测试 — SessionLogger（`tests/unit/test_session/test_session_logger.py`）

  - [ ] 16.1 LLM 交互日志测试
    - `log_llm_interaction()` 写入 `session_llm.md`（验证文件内容包含 iteration、usage、stop_reason）
    - `log_llm_interaction()` 写入 JSONL transcript（验证 mock TranscriptWriter.append 被调用）
    - 多次调用后 `_llm_calls` 列表长度正确
    - _需求：7.1, 7.4_

  - [ ] 16.2 Session 摘要测试
    - `generate_summary()` 生成包含正确统计信息的 Markdown
    - 摘要写入 `session_summary.md` 文件
    - 摘要追加到全局 `history_session.md` 文件
    - _需求：7.2, 7.3_

  - [ ] 16.3 资源管理测试
    - `close()` 后文件句柄已关闭
    - `close()` 后再次调用 `log_llm_interaction()` 不崩溃（静默忽略或抛出明确异常）
    - _需求：11.3_

## Phase 9：集成测试

- [ ] 17. 集成测试（`tests/integration/test_session_integration.py`）
  - 遵循 TESTING_GUIDELINES.md 规范，所有测试使用 `tmp_path` fixture

  - [ ] 17.1 完整 Session 生命周期测试
    - 创建 Session（`CREATED`）→ `begin_turn()` 触发 `ACTIVE` → 记录多轮对话（3+ 轮，含 user/assistant/tool 消息）→ `pause_session()` → `continue_session()` 恢复 → 继续对话 → `complete_session()`
    - 验证 SQLite 数据：Session 状态、turn_count、budget.used 正确
    - 验证 JSONL transcript：条目数量与 SQLite turns 一致，内容匹配
    - 验证 SessionIndex：条目状态与 Session 一致
    - 验证 TurnContext：每轮的 TurnContext 已持久化，配置字段正确
    - _需求：2.1-2.6, 3.1-3.5, 4.1-4.3, 5.1_

  - [ ] 17.2 Session Fork 和 Checkpoint 恢复测试
    - 创建 Session → 记录 5 轮对话 → 创建 Checkpoint → 继续 2 轮 → Fork
    - 验证原 Session 状态不变（非 FORKED）
    - 验证新 Session 包含 Fork 前的所有 Turn
    - 从 Checkpoint 恢复 → 验证恢复的 Session 包含 Checkpoint 时刻的 Turn
    - _需求：2.7_

  - [ ] 17.3 Context Compaction 触发测试
    - 创建 Session（budget.total=1000, reserved=100）
    - 记录多轮对话，每轮 token_count=100，直到 `utilization_rate > 0.9`
    - 验证 Session 自动进入 `COMPACTING` 状态
    - 验证 `TOKEN_BUDGET_WARNING` 事件在 80% 时发射
    - 调用 `complete_compaction(new_used=200)` → 验证回到 `ACTIVE`，`budget.used=200`
    - _需求：8.1-8.5_

  - [ ] 17.4 Session 恢复（continue/resume）端到端测试
    - 创建 Session → 记录 3 轮对话 → `pause_session()`
    - 创建新的 SessionManager 实例（模拟重启）
    - 调用 `continue_session()` → 验证恢复的 Session ID 与原 Session 一致
    - 验证对话历史完整（从 JSONL 重建）
    - 调用 `resume_session_by_id(session_id)` → 验证同样能恢复
    - _需求：6.1-6.5_

  - [ ] 17.5 JSONL 损坏恢复测试
    - 创建 Session → 记录 5 轮对话 → `pause_session()`
    - 手动在 JSONL 文件中插入 3 行无效 JSON（在有效行之间）
    - 调用 `resume_session_by_id()` → 验证恢复成功
    - 验证恢复的 Turn 数量为 5（无效行被跳过）
    - 验证日志中记录了 3 条 WARNING
    - _需求：4.4, 6.5_

  - [ ] 17.6 数据一致性测试（SQLite vs JSONL vs Index 三方一致）
    - 创建 Session → 记录 10 轮对话 → 暂停 → 恢复 → 记录 5 轮 → 完成
    - 验证 SQLite turns 数量 == JSONL 条目数量 == 15
    - 验证 SessionIndex 中的 turn_count == 15
    - 验证 SessionIndex 中的 status == "completed"
    - _需求：4.6, 5.1_

  - [ ] 17.7 SessionLogger 集成测试
    - 创建 Session → 通过 SessionLogger 记录 3 次 LLM 交互 → 完成 Session
    - 验证 `session_llm.md` 包含 3 个 LLM Call 段落
    - 验证 `session_summary.md` 包含正确的统计信息
    - 验证 `history_session.md` 被追加了摘要
    - _需求：7.1-7.4_
