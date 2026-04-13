# 需求文档：Agent X1 全链路 DEBUG 日志增强

## 引言

Agent X1 当前的日志系统存在以下不足，导致开发者和学习者难以完整追踪 agent 的运行行为：

1. **日志级别过高**：默认 `INFO` 级别，大量关键动作（LLM 请求/响应细节、工具参数/结果、上下文组装过程、内存读写等）未被记录
2. **缺少 session_id**：日志中没有 session 标识，无法按会话回溯
3. **不按天分割**：所有日志写入同一个 `agent_x1.log`，文件持续增长，难以按日期定位问题
4. **核心模块日志覆盖不足**：`runtime/agent_loop.py`、`context/`、`memory/`、`session/`、`skills/` 等核心模块缺少 DEBUG 级别的详细动作日志
5. **参数记录不完整**：现有日志对 LLM 请求/响应、工具调用参数/结果的记录过于简略

本需求旨在建立一套**全链路 DEBUG 日志体系**，让开发者能够通过日志文件完整还原 agent 每一步的行为，用于学习、调试和验证 agent 功能是否正常。

### 当前日志系统架构

```
src/util/logger.py          — 统一日志配置（StandardFormatter, JSONFormatter, RotatingFileHandler）
src/util/log_integration.py — EventBus → Display/StructuredLog 桥接层
src/util/structured_log.py  — JSONL 结构化日志（写入 session 目录）
src/util/activity_stream.py — 实时活动流显示
src/logs/agent_x1.log       — 主日志文件（RotatingFileHandler, 10MB）
src/logs/agent_x1_error.log — 错误专用日志
```

### 需要增加日志覆盖的核心模块

| 模块 | 路径 | 关键动作 |
|------|------|----------|
| AgentLoop | `src/runtime/agent_loop.py` | 循环迭代、LLM 调用、工具调度、上下文组装 |
| ToolScheduler | `src/runtime/tool_scheduler.py` | 工具并行调度、执行、结果收集 |
| LoopDetector | `src/runtime/loop_detector.py` | 循环检测判定 |
| ContextAssembler | `src/context/context_assembler.py` | 分层组装、token 预算、层级淘汰 |
| ContextCompressor | `src/context/context_compressor.py` | 历史压缩、工具输出截断 |
| ContextWindow | `src/context/context_window.py` | token 预算计算 |
| MemoryController | `src/memory/memory_controller.py` | 记忆存储/检索/清理 |
| MemoryStore | `src/memory/memory_store.py` | SQLite 读写操作 |
| ProjectMemory | `src/memory/project_memory.py` | PROJECT.md 发现与加载 |
| SessionManager | `src/session/session_manager.py` | 会话创建/暂停/恢复/完成/归档 |
| SessionStore | `src/session/session_store.py` | SQLite 会话持久化 |
| SessionLogger | `src/session/session_logger.py` | 会话日志记录 |
| AnthropicEngine | `src/engine/anthropic_engine.py` | API 请求构建、响应解析、重试 |
| KimiEngine | `src/engine/kimi_engine.py` | API 请求构建、响应解析 |
| SkillLoader | `src/skills/loader.py` | Skill 发现与加载 |
| SkillContextManager | `src/skills/context_manager.py` | Skill 激活与上下文管理 |
| ToolRegistry | `src/tools/tool_registry.py` | 工具注册与查找 |
| EventBus | `src/core/events.py` | 事件发布与订阅 |
| PromptProvider | `src/prompt/prompt_provider.py` | 系统提示词组装 |

---

## 需求

### 需求 1：日志文件按天分割

**用户故事：** 作为一名开发者，我希望日志文件按天自动分割（如 `agent_x1_20260329.log`），以便我能快速定位某一天的运行记录，而不必在一个巨大的日志文件中搜索。

#### 验收标准

1. WHEN agent 启动 THEN 日志系统 SHALL 使用 `TimedRotatingFileHandler` 替代 `RotatingFileHandler`，按天（midnight）自动轮转日志文件
2. WHEN 日志文件轮转 THEN 系统 SHALL 生成文件名格式为 `agent_x1_YYYYMMDD.log`（如 `agent_x1_20260329.log`）
3. WHEN 日志文件轮转 THEN 系统 SHALL 保留最近 30 天的日志文件，自动删除更早的文件
4. WHEN 错误日志文件轮转 THEN 系统 SHALL 同样按天分割，格式为 `agent_x1_error_YYYYMMDD.log`
5. IF 日志目录不存在 THEN 系统 SHALL 自动创建目录
6. WHEN 日志系统初始化 THEN 系统 SHALL 在日志中记录当前日志文件路径，方便开发者定位

### 需求 2：默认日志级别改为 DEBUG

**用户故事：** 作为一名 agent 学习者，我希望默认日志级别为 DEBUG，以便所有详细的运行信息都被记录下来，帮助我理解 agent 的每一步行为。

#### 验收标准

1. WHEN `setup_logging()` 被调用且未指定级别 THEN 系统 SHALL 使用 `DEBUG` 作为默认日志级别
2. WHEN 环境变量 `AGENT_X1_LOG_LEVEL` 被设置 THEN 系统 SHALL 优先使用环境变量指定的级别（保持现有行为）
3. WHEN 默认级别为 DEBUG THEN 控制台 handler 的级别 SHALL 保持为 `INFO`（避免控制台输出过多 DEBUG 信息干扰用户）
4. WHEN 默认级别为 DEBUG THEN 文件 handler 的级别 SHALL 设为 `DEBUG`（确保文件中记录所有详细信息）
5. WHEN 第三方库（urllib3, httpx, httpcore, asyncio）日志 THEN 系统 SHALL 保持其级别为 `WARNING`（避免第三方库的 DEBUG 信息淹没业务日志）

### 需求 3：日志格式中包含 session_id

**用户故事：** 作为一名开发者，我希望每条日志都包含当前的 session_id，以便我能按会话过滤和回溯日志，快速定位某次运行的完整行为链。

#### 验收标准

1. WHEN 日志记录被格式化 THEN 系统 SHALL 在日志格式中包含 `[sid:XXXXXXXX]` 字段（session_id 前8位），位于时间戳之后
2. IF session_id 尚未设置（如启动阶段） THEN 系统 SHALL 使用 `[sid:--------]` 占位符
3. WHEN session 被创建或恢复 THEN 系统 SHALL 通过 `threading.local()` 或 `logging.Filter` 机制将 session_id 注入到所有后续日志记录中
4. WHEN JSON 格式日志被使用 THEN 系统 SHALL 在 JSON 输出中包含 `"session_id"` 字段
5. WHEN 日志格式包含 session_id THEN 格式 SHALL 为：`[TIMESTAMP] [sid:XXXXXXXX] [PID] [TID] [LEVEL] [FILE:LINE] | MESSAGE`

### 需求 4：AgentLoop 全链路 DEBUG 日志

**用户故事：** 作为一名 agent 学习者，我希望 AgentLoop 的每一步迭代都有详细的 DEBUG 日志，以便我能完整追踪 agent 从接收用户输入到返回最终结果的全过程。

#### 验收标准

1. WHEN agent loop 启动 THEN 系统 SHALL 记录 DEBUG 日志：`[AgentLoop] Loop started | session_id=X | max_iterations=N | tool_count=N | system_prompt_length=N`
2. WHEN 每次迭代开始 THEN 系统 SHALL 记录 DEBUG 日志：`[AgentLoop] Iteration #N started | elapsed=Xs | message_count=N | estimated_tokens=N`
3. WHEN 上下文组装完成 THEN 系统 SHALL 记录 DEBUG 日志：`[AgentLoop] Context assembled | layers=N | total_tokens=N | budget_used=N% | layers_detail=[...]`
4. WHEN LLM 请求发送前 THEN 系统 SHALL 记录 DEBUG 日志：`[AgentLoop] LLM request | model=X | message_count=N | has_tools=bool | temperature=X | max_tokens=N`
5. WHEN LLM 响应收到 THEN 系统 SHALL 记录 DEBUG 日志：`[AgentLoop] LLM response | input_tokens=N | output_tokens=N | duration=Xs | finish_reason=X | has_content=bool | tool_call_count=N`
6. WHEN LLM 返回文本内容 THEN 系统 SHALL 记录 DEBUG 日志：`[AgentLoop] LLM content preview | length=N | first_500_chars="..."`
7. WHEN LLM 返回工具调用 THEN 系统 SHALL 对每个工具调用记录 DEBUG 日志：`[AgentLoop] Tool call #N | name=X | arguments_length=N | arguments_preview="..."`（参数预览最多500字符）
8. WHEN 工具执行完成 THEN 系统 SHALL 记录 DEBUG 日志：`[AgentLoop] Tool result #N | name=X | status=success/error | duration=Xms | output_length=N | output_preview="..."`（输出预览最多500字符）
9. WHEN 迭代结束 THEN 系统 SHALL 记录 INFO 日志（保持现有行为）：`[AgentLoop] Step N Complete | Duration=X | Tokens=X→X | Tool Calls: ...`
10. WHEN agent loop 结束 THEN 系统 SHALL 记录 DEBUG 日志：`[AgentLoop] Loop completed | total_iterations=N | total_duration=Xs | total_tokens=N | final_reason=X`
11. WHEN LLM 调用失败 THEN 系统 SHALL 记录 ERROR 日志：`[AgentLoop] LLM call failed | iteration=N | error=X | will_retry=bool`
12. IF 参数或输出超过500字符 THEN 系统 SHALL 截断并附加 `[N chars total]` 标注

### 需求 5：LLM Engine 请求/响应 DEBUG 日志

**用户故事：** 作为一名开发者，我希望 LLM Engine 层记录完整的 API 请求和响应细节，以便我能诊断 API 调用问题和理解模型行为。

#### 验收标准

1. WHEN API 请求构建完成 THEN AnthropicEngine/KimiEngine SHALL 记录 DEBUG 日志：`[Engine] API request built | url=X | model=X | max_tokens=N | temperature=X | message_count=N | tool_count=N | system_prompt_length=N`
2. WHEN API 请求发送 THEN Engine SHALL 记录 DEBUG 日志：`[Engine] Sending request | payload_size=N bytes`
3. WHEN API 响应收到 THEN Engine SHALL 记录 DEBUG 日志：`[Engine] API response | status=N | response_size=N bytes | duration=Xs`
4. WHEN 响应解析完成 THEN Engine SHALL 记录 DEBUG 日志：`[Engine] Response parsed | content_length=N | tool_calls=N | usage={input:N, output:N, total:N} | finish_reason=X`
5. WHEN API 请求重试 THEN Engine SHALL 记录 WARNING 日志：`[Engine] Retrying request | attempt=N/M | wait=Xs | error=X`
6. WHEN 工具执行（engine 层） THEN Engine SHALL 记录 DEBUG 日志：`[Engine] Tool executing | name=X | arguments_preview="..." [N chars]`
7. WHEN 工具执行完成（engine 层） THEN Engine SHALL 记录 DEBUG 日志：`[Engine] Tool completed | name=X | duration=Xs | output_length=N | output_preview="..." [N chars]`
8. WHEN 工具输出被截断 THEN Engine SHALL 记录 WARNING 日志（保持现有行为）

### 需求 6：上下文组装 DEBUG 日志

**用户故事：** 作为一名 agent 学习者，我希望上下文组装过程有详细日志，以便我理解 agent 如何构建发送给 LLM 的消息，以及 token 预算如何分配。

#### 验收标准

1. WHEN ContextAssembler.build() 开始 THEN 系统 SHALL 记录 DEBUG 日志：`[ContextAssembler] Build started | session_id=X | user_input_length=N | budget_total=N | budget_available=N`
2. WHEN 每个上下文层被添加 THEN 系统 SHALL 记录 DEBUG 日志：`[ContextAssembler] Layer added | name=X | priority=N | token_count=N | required=bool | cumulative_tokens=N`
3. WHEN 层被淘汰（超出预算） THEN 系统 SHALL 记录 DEBUG 日志：`[ContextAssembler] Layer evicted | name=X | priority=N | token_count=N | reason=budget_exceeded`
4. WHEN 组装完成 THEN 系统 SHALL 记录 DEBUG 日志：`[ContextAssembler] Build complete | total_layers=N | total_tokens=N | budget_utilization=N% | message_count=N`
5. WHEN ContextCompressor 压缩历史 THEN 系统 SHALL 记录 DEBUG 日志：`[ContextCompressor] Compressing | input_messages=N | input_tokens=N | target_tokens=N`
6. WHEN 压缩完成 THEN 系统 SHALL 记录 DEBUG 日志：`[ContextCompressor] Compressed | output_messages=N | output_tokens=N | compression_ratio=X%`
7. WHEN ContextWindow 计算预算 THEN 系统 SHALL 记录 DEBUG 日志：`[ContextWindow] Budget check | max=N | used=N | available=N | should_compress=bool`

### 需求 7：Memory 系统 DEBUG 日志

**用户故事：** 作为一名开发者，我希望 Memory 系统的每次读写操作都有日志，以便我追踪 agent 的记忆存储和检索行为。

#### 验收标准

1. WHEN episodic memory 被存储 THEN 系统 SHALL 记录 DEBUG 日志：`[Memory] Episodic stored | session_id=X | type=X | importance=X | content_preview="..." [N chars]`
2. WHEN semantic memory 被存储 THEN 系统 SHALL 记录 DEBUG 日志：`[Memory] Semantic stored | category=X | key=X | confidence=X | value_preview="..." [N chars]`
3. WHEN memory 被检索 THEN 系统 SHALL 记录 DEBUG 日志：`[Memory] Retrieved | query="..." | results=N | types=[...] | top_importance=X`
4. WHEN memory 被清理（遗忘曲线） THEN 系统 SHALL 记录 INFO 日志：`[Memory] Cleanup | expired_count=N | remaining_count=N | threshold=X`
5. WHEN ProjectMemory 被加载 THEN 系统 SHALL 记录 DEBUG 日志：`[ProjectMemory] Loaded | path=X | size=N bytes | scope=X`
6. WHEN MemoryStore SQLite 操作执行 THEN 系统 SHALL 记录 DEBUG 日志：`[MemoryStore] SQL | operation=X | table=X | affected_rows=N | duration=Xms`

### 需求 8：Session 生命周期 DEBUG 日志

**用户故事：** 作为一名开发者，我希望 Session 的每个生命周期事件都有详细日志，以便我追踪会话从创建到结束的完整过程。

#### 验收标准

1. WHEN session 被创建 THEN 系统 SHALL 记录 INFO 日志：`[Session] Created | id=X | name=X | working_dir=X | session_dir=X | budget_total=N`
2. WHEN session 状态变更 THEN 系统 SHALL 记录 INFO 日志：`[Session] Status changed | id=X | from=X | to=X`
3. WHEN turn 开始 THEN 系统 SHALL 记录 DEBUG 日志：`[Session] Turn started | session_id=X | turn_number=N`
4. WHEN turn 结束 THEN 系统 SHALL 记录 DEBUG 日志：`[Session] Turn ended | session_id=X | turn_number=N | token_usage=N | duration=Xms`
5. WHEN session 被 fork THEN 系统 SHALL 记录 INFO 日志：`[Session] Forked | parent_id=X | child_id=X | inherited_turns=N`
6. WHEN checkpoint 被创建 THEN 系统 SHALL 记录 DEBUG 日志：`[Session] Checkpoint | session_id=X | checkpoint_id=X | turn_number=N`
7. WHEN transcript 被写入 THEN 系统 SHALL 记录 DEBUG 日志：`[Session] Transcript write | session_id=X | entry_type=X | content_length=N`
8. WHEN session index 被更新 THEN 系统 SHALL 记录 DEBUG 日志：`[Session] Index updated | session_id=X | status=X | turn_count=N`
9. WHEN session 被归档 THEN 系统 SHALL 记录 INFO 日志：`[Session] Archived | id=X | age_days=N | turn_count=N`
10. WHEN SessionStore SQLite 操作执行 THEN 系统 SHALL 记录 DEBUG 日志：`[SessionStore] SQL | operation=X | session_id=X | duration=Xms`

### 需求 9：Tool 调度与执行 DEBUG 日志

**用户故事：** 作为一名 agent 学习者，我希望工具调度和执行的每一步都有日志，以便我理解 agent 如何选择和执行工具。

#### 验收标准

1. WHEN ToolScheduler 接收工具调用列表 THEN 系统 SHALL 记录 DEBUG 日志：`[ToolScheduler] Scheduling | tool_count=N | tools=[name1, name2, ...] | parallel=bool`
2. WHEN 单个工具开始执行 THEN 系统 SHALL 记录 DEBUG 日志：`[ToolScheduler] Executing | name=X | call_id=X | arguments_preview="..." [N chars]`
3. WHEN 单个工具执行完成 THEN 系统 SHALL 记录 DEBUG 日志：`[ToolScheduler] Completed | name=X | call_id=X | status=X | duration=Xms | output_length=N | output_preview="..." [N chars]`
4. WHEN 工具执行失败 THEN 系统 SHALL 记录 ERROR 日志：`[ToolScheduler] Failed | name=X | call_id=X | error=X | duration=Xms`
5. WHEN LoopDetector 检测到循环 THEN 系统 SHALL 记录 WARNING 日志：`[LoopDetector] Loop detected | pattern=X | count=N | threshold=N`
6. WHEN ToolRegistry 注册工具 THEN 系统 SHALL 记录 DEBUG 日志：`[ToolRegistry] Registered | name=X | description_preview="..." | param_count=N`
7. WHEN ToolRegistry 查找工具 THEN 系统 SHALL 记录 DEBUG 日志：`[ToolRegistry] Lookup | name=X | found=bool`

### 需求 10：Skill 系统 DEBUG 日志

**用户故事：** 作为一名开发者，我希望 Skill 的加载和激活过程有日志，以便我理解 agent 的技能系统如何工作。

#### 验收标准

1. WHEN SkillLoader 扫描目录 THEN 系统 SHALL 记录 DEBUG 日志：`[SkillLoader] Scanning | directory=X | found=N skills`
2. WHEN Skill 被加载 THEN 系统 SHALL 记录 DEBUG 日志（保持现有 INFO 行为）：`[SkillLoader] Loaded | name=X | path=X | tools=N | prompt_length=N`
3. WHEN Skill 被激活 THEN 系统 SHALL 记录 DEBUG 日志：`[SkillContext] Activated | name=X | goal=X | workspace=X`
4. WHEN Skill workspace 被创建 THEN 系统 SHALL 记录 DEBUG 日志：`[SkillWorkspace] Created | path=X | sub_dirs=N`

### 需求 11：EventBus 事件 DEBUG 日志

**用户故事：** 作为一名开发者，我希望 EventBus 的事件发布和订阅都有日志，以便我追踪事件在系统中的流转。

#### 验收标准

1. WHEN 事件被发布 THEN 系统 SHALL 记录 DEBUG 日志：`[EventBus] Emit | event=X | subscriber_count=N | payload_keys=[...]`
2. WHEN 事件订阅者被注册 THEN 系统 SHALL 记录 DEBUG 日志：`[EventBus] Subscribe | event=X | handler=X`
3. WHEN 事件处理器执行失败 THEN 系统 SHALL 记录 ERROR 日志：`[EventBus] Handler error | event=X | handler=X | error=X`

### 需求 12：Prompt 组装 DEBUG 日志

**用户故事：** 作为一名 agent 学习者，我希望系统提示词的组装过程有日志，以便我理解 agent 的 prompt 是如何构建的。

#### 验收标准

1. WHEN PromptProvider 组装系统提示词 THEN 系统 SHALL 记录 DEBUG 日志：`[PromptProvider] Building system prompt | sections=N | total_length=N`
2. WHEN 每个 prompt section 被添加 THEN 系统 SHALL 记录 DEBUG 日志：`[PromptProvider] Section added | name=X | length=N | priority=N`

### 需求 13：回归测试验证

**用户故事：** 作为一名开发者，我希望所有日志增强修改都有完备的单元测试和集成测试覆盖，以便确保修改不会破坏现有功能。

#### 验收标准

1. WHEN logger.py 被修改 THEN 系统 SHALL 通过所有现有 `test_logger.py` 单元测试
2. WHEN 日志格式被修改 THEN 系统 SHALL 新增单元测试验证：按天分割文件名格式、session_id 注入、DEBUG 级别默认值
3. WHEN 各模块添加 DEBUG 日志 THEN 系统 SHALL 新增集成测试验证：关键动作的日志输出存在且格式正确
4. WHEN 所有修改完成 THEN 系统 SHALL 通过完整的 `pytest tests/` 测试套件，无回归失败
5. WHEN 测试运行 THEN 系统 SHALL 验证日志文件按天命名、session_id 正确注入、DEBUG 信息被记录到文件
6. IF 现有测试依赖日志格式 THEN 系统 SHALL 更新这些测试以适配新格式，而非删除测试

### 需求 14：不改变原有功能

**用户故事：** 作为一名用户，我希望日志增强不会改变 agent 的任何业务行为，以便我可以安全地升级。

#### 验收标准

1. WHEN 日志代码被添加 THEN 系统 SHALL 仅添加日志语句，不修改任何业务逻辑代码路径
2. WHEN 默认日志级别改为 DEBUG THEN 控制台输出 SHALL 保持 INFO 级别，用户体验不变
3. WHEN 日志格式改变 THEN 系统 SHALL 保持 `get_logger()`、`setup_logging()`、`set_log_level()` 等公共 API 的签名和行为不变
4. WHEN 日志文件名改变 THEN 系统 SHALL 保持日志目录位置不变（`src/logs/`）
5. IF 参数记录涉及敏感信息（如 API key） THEN 系统 SHALL 对敏感字段进行脱敏处理（如 `api_key=sk-...XXXX`）


# 实施计划：Agent X1 全链路 DEBUG 日志增强

---

- [ ] 1. 改造日志基础设施 — `src/util/logger.py`
  - 修改 `setup_logging()` 函数，将 `RotatingFileHandler` 替换为 `TimedRotatingFileHandler`（按天 midnight 轮转）
  - 主日志文件名改为 `agent_x1_YYYYMMDD.log`，错误日志改为 `agent_x1_error_YYYYMMDD.log`，设置 `backupCount=30`（保留30天）
  - 将 `setup_logging()` 的默认参数 `level` 从 `logging.INFO` 改为 `logging.DEBUG`
  - 文件 handler 级别设为 `DEBUG`，控制台 handler 级别固定为 `INFO`（不受默认级别影响）
  - 保持环境变量 `AGENT_X1_LOG_LEVEL` 覆盖机制不变
  - 保持第三方库（urllib3, httpx, httpcore, asyncio, uvicorn.access）日志级别为 `WARNING`
  - 在 `setup_logging()` 末尾用 `logger.info` 记录当前日志文件路径
  - 保持 `get_logger()`、`set_log_level()`、`reset_logging()` 等公共 API 签名不变
  - _需求：1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3, 2.4, 2.5, 14.2, 14.3, 14.4_

- [ ] 2. 实现 session_id 注入机制 — `src/util/logger.py`
  - 新增 `SessionIdFilter(logging.Filter)` 类，使用 `threading.local()` 存储当前 session_id
  - 提供 `set_session_id(sid: str)` 和 `clear_session_id()` 模块级函数，供 SessionManager 调用
  - `SessionIdFilter.filter()` 方法在每条 `LogRecord` 上注入 `record.session_id` 属性（前8位，无 session 时为 `--------`）
  - 修改 `StandardFormatter.FORMAT` 为：`[%(asctime)s] [sid:%(session_id)s] [%(process)d] [%(thread)d] [%(levelname)-5s] [%(filename)s:%(lineno)d] | %(message)s`
  - 修改 `JSONFormatter.format()` 在 JSON 输出中增加 `"session_id"` 字段
  - 在 `setup_logging()` 中将 `SessionIdFilter` 添加到 root logger
  - _需求：3.1, 3.2, 3.3, 3.4, 3.5_

- [ ] 3. 在 SessionManager 中绑定 session_id — `src/session/session_manager.py` 和 `src/core/session_manager.py`
  - 在 session 创建（`create_session`）时调用 `set_session_id(session.id)`
  - 在 session 恢复（`resume_session`）时调用 `set_session_id(session.id)`
  - 在 session 结束（`complete_session` / `archive_session`）时调用 `clear_session_id()`
  - 确保 `import` 来自 `src.util.logger`
  - _需求：3.3, 8.1, 8.2_

- [ ] 4. 添加 AgentLoop 全链路 DEBUG 日志 — `src/runtime/agent_loop.py`
  - 在 `run()` / 主循环入口处添加 DEBUG 日志：loop started（session_id, max_iterations, tool_count, system_prompt_length）
  - 在每次迭代开始处添加 DEBUG 日志：iteration #N started（elapsed, message_count, estimated_tokens）
  - 在上下文组装完成后添加 DEBUG 日志：context assembled（layers, total_tokens, budget_used%, layers_detail）
  - 在 LLM 请求发送前添加 DEBUG 日志：LLM request（model, message_count, has_tools, temperature, max_tokens）
  - 在 LLM 响应收到后添加 DEBUG 日志：LLM response（input_tokens, output_tokens, duration, finish_reason, has_content, tool_call_count）
  - 在 LLM 返回文本时添加 DEBUG 日志：content preview（length, first_500_chars）
  - 在 LLM 返回工具调用时，对每个工具调用添加 DEBUG 日志：tool call #N（name, arguments_length, arguments_preview≤500字符）
  - 在工具执行完成后添加 DEBUG 日志：tool result #N（name, status, duration, output_length, output_preview≤500字符）
  - 保持现有 INFO 日志不变（Step N Complete 等）
  - 在 loop 结束时添加 DEBUG 日志：loop completed（total_iterations, total_duration, total_tokens, final_reason）
  - 在 LLM 调用失败时添加 ERROR 日志：LLM call failed（iteration, error, will_retry）
  - 新增 `_truncate(text: str, max_len: int = 500) -> str` 辅助函数，截断并附加 `[N chars total]`
  - _需求：4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10, 4.11, 4.12_

- [ ] 5. 添加 LLM Engine 请求/响应 DEBUG 日志 — `src/engine/anthropic_engine.py` 和 `src/engine/kimi_engine.py`
  - 在 API 请求构建完成后添加 DEBUG 日志：API request built（url, model, max_tokens, temperature, message_count, tool_count, system_prompt_length）
  - 在 API 请求发送时添加 DEBUG 日志：Sending request（payload_size bytes）
  - 在 API 响应收到后添加 DEBUG 日志：API response（status, response_size bytes, duration）
  - 在响应解析完成后添加 DEBUG 日志：Response parsed（content_length, tool_calls, usage, finish_reason）
  - 在 API 请求重试时添加 WARNING 日志：Retrying request（attempt, wait, error）
  - 在工具执行时添加 DEBUG 日志：Tool executing / Tool completed（name, duration, output_length, output_preview）
  - 保持现有的工具输出截断 WARNING 日志不变
  - 对 API key 等敏感字段进行脱敏处理（如 `api_key=sk-...XXXX`）
  - _需求：5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 14.5_

- [ ] 6. 添加上下文组装 DEBUG 日志 — `src/context/context_assembler.py`、`src/context/context_compressor.py`、`src/context/context_window.py`
  - 在 `ContextAssembler.build()` 开始时添加 DEBUG 日志：Build started（session_id, user_input_length, budget_total, budget_available）
  - 在每个上下文层被添加时添加 DEBUG 日志：Layer added（name, priority, token_count, required, cumulative_tokens）
  - 在层被淘汰时添加 DEBUG 日志：Layer evicted（name, priority, token_count, reason）
  - 在组装完成时添加 DEBUG 日志：Build complete（total_layers, total_tokens, budget_utilization%, message_count）
  - 在 `ContextCompressor` 压缩开始时添加 DEBUG 日志：Compressing（input_messages, input_tokens, target_tokens）
  - 在压缩完成时添加 DEBUG 日志：Compressed（output_messages, output_tokens, compression_ratio%）
  - 在 `ContextWindow` 计算预算时添加 DEBUG 日志：Budget check（max, used, available, should_compress）
  - _需求：6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_

- [ ] 7. 添加 Memory 系统 DEBUG 日志 — `src/memory/memory_controller.py`、`src/memory/memory_store.py`、`src/memory/project_memory.py`
  - 在 episodic memory 存储时添加 DEBUG 日志：Episodic stored（session_id, type, importance, content_preview≤500字符）
  - 在 semantic memory 存储时添加 DEBUG 日志：Semantic stored（category, key, confidence, value_preview≤500字符）
  - 在 memory 检索时添加 DEBUG 日志：Retrieved（query, results, types, top_importance）
  - 在 memory 清理时添加 INFO 日志：Cleanup（expired_count, remaining_count, threshold）
  - 在 ProjectMemory 加载时添加 DEBUG 日志：Loaded（path, size bytes, scope）
  - 在 MemoryStore SQLite 操作时添加 DEBUG 日志：SQL（operation, table, affected_rows, duration ms）
  - _需求：7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

- [ ] 8. 添加 Session 生命周期 DEBUG 日志 — `src/session/session_manager.py`、`src/session/session_store.py`、`src/session/session_logger.py`、`src/session/session_index.py`、`src/session/transcript.py`
  - 在 session 创建时添加 INFO 日志：Created（id, name, working_dir, session_dir, budget_total）
  - 在 session 状态变更时添加 INFO 日志：Status changed（id, from, to）
  - 在 turn 开始时添加 DEBUG 日志：Turn started（session_id, turn_number）
  - 在 turn 结束时添加 DEBUG 日志：Turn ended（session_id, turn_number, token_usage, duration ms）
  - 在 session fork 时添加 INFO 日志：Forked（parent_id, child_id, inherited_turns）
  - 在 checkpoint 创建时添加 DEBUG 日志：Checkpoint（session_id, checkpoint_id, turn_number）
  - 在 transcript 写入时添加 DEBUG 日志：Transcript write（session_id, entry_type, content_length）
  - 在 session index 更新时添加 DEBUG 日志：Index updated（session_id, status, turn_count）
  - 在 session 归档时添加 INFO 日志：Archived（id, age_days, turn_count）
  - 在 SessionStore SQLite 操作时添加 DEBUG 日志：SQL（operation, session_id, duration ms）
  - _需求：8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10_

- [ ] 9. 添加 Tool 调度与执行 DEBUG 日志 — `src/runtime/tool_scheduler.py`、`src/runtime/loop_detector.py`、`src/tools/tool_registry.py`
  - 在 ToolScheduler 接收工具调用列表时添加 DEBUG 日志：Scheduling（tool_count, tools, parallel）
  - 在单个工具开始执行时添加 DEBUG 日志：Executing（name, call_id, arguments_preview≤500字符）
  - 在单个工具执行完成时添加 DEBUG 日志：Completed（name, call_id, status, duration ms, output_length, output_preview≤500字符）
  - 在工具执行失败时添加 ERROR 日志：Failed（name, call_id, error, duration ms）
  - 在 LoopDetector 检测到循环时添加 WARNING 日志：Loop detected（pattern, count, threshold）
  - 在 ToolRegistry 注册工具时添加 DEBUG 日志：Registered（name, description_preview, param_count）
  - 在 ToolRegistry 查找工具时添加 DEBUG 日志：Lookup（name, found）
  - _需求：9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7_

- [ ] 10. 添加 Skill 系统和 EventBus 和 Prompt DEBUG 日志 — `src/skills/loader.py`、`src/skills/context_manager.py`、`src/core/events.py`、`src/prompt/prompt_provider.py`
  - 在 SkillLoader 扫描目录时添加 DEBUG 日志：Scanning（directory, found N skills）
  - 在 Skill 被加载时添加 DEBUG 日志：Loaded（name, path, tools, prompt_length）
  - 在 Skill 被激活时添加 DEBUG 日志：Activated（name, goal, workspace）
  - 在 EventBus 事件被发布时添加 DEBUG 日志：Emit（event, subscriber_count, payload_keys）
  - 在 EventBus 订阅者被注册时添加 DEBUG 日志：Subscribe（event, handler）
  - 在 EventBus 处理器执行失败时添加 ERROR 日志：Handler error（event, handler, error）
  - 在 PromptProvider 组装系统提示词时添加 DEBUG 日志：Building system prompt（sections, total_length）
  - 在每个 prompt section 被添加时添加 DEBUG 日志：Section added（name, length, priority）
  - _需求：10.1, 10.2, 10.3, 10.4, 11.1, 11.2, 11.3, 12.1, 12.2_

- [ ] 11. 更新现有单元测试以适配新日志格式 — `tests/unit/test_logger.py`、`tests/unit/test_log_integration.py`、`tests/unit/test_activity_stream.py`
  - 更新 `test_logger.py` 中依赖日志格式的断言，适配新增的 `[sid:XXXXXXXX]` 字段
  - 更新 `test_logger.py` 中依赖 `RotatingFileHandler` 的测试，改为验证 `TimedRotatingFileHandler`
  - 更新 `test_logger.py` 中默认级别为 `INFO` 的断言，改为 `DEBUG`
  - 更新 `test_log_integration.py` 中可能受日志格式影响的测试
  - 更新 `test_activity_stream.py` 中可能受影响的测试
  - 确保 `reset_logging()` 在测试 teardown 中正确清理 `SessionIdFilter` 和 `threading.local` 状态
  - _需求：13.1, 13.6_

- [ ] 12. 新增日志基础设施单元测试 — `tests/unit/test_logger.py`（扩展）
  - 新增测试：验证 `TimedRotatingFileHandler` 被正确使用，文件名包含日期后缀
  - 新增测试：验证 `backupCount=30`（保留30天）
  - 新增测试：验证错误日志同样按天分割
  - 新增测试：验证默认日志级别为 `DEBUG`
  - 新增测试：验证控制台 handler 级别为 `INFO`，文件 handler 级别为 `DEBUG`
  - 新增测试：验证环境变量 `AGENT_X1_LOG_LEVEL` 覆盖默认级别
  - 新增测试：验证 `set_session_id()` 后日志输出包含 `[sid:XXXXXXXX]`
  - 新增测试：验证未设置 session_id 时日志输出包含 `[sid:--------]`
  - 新增测试：验证 `clear_session_id()` 后恢复为 `[sid:--------]`
  - 新增测试：验证 JSON 格式日志包含 `"session_id"` 字段
  - 新增测试：验证第三方库日志级别保持 `WARNING`
  - _需求：13.2, 13.5_

- [ ] 13. 新增全链路日志集成测试 — `tests/integration/test_debug_logging_e2e.py`（新建）
  - 新增测试：模拟完整 AgentLoop 迭代，验证 DEBUG 日志按正确顺序输出（loop started → iteration → context assembled → LLM request → LLM response → tool call → tool result → loop completed）
  - 新增测试：验证 LLM Engine 层的 API request/response DEBUG 日志存在且参数完整
  - 新增测试：验证上下文组装过程的 layer added/evicted/build complete 日志
  - 新增测试：验证 Memory 存储/检索/清理的 DEBUG 日志
  - 新增测试：验证 Session 生命周期日志（created → turn started → turn ended → status changed）
  - 新增测试：验证 ToolScheduler 调度/执行/完成的 DEBUG 日志
  - 新增测试：验证 EventBus emit/subscribe 的 DEBUG 日志
  - 新增测试：验证长参数被正确截断并附加 `[N chars total]` 标注
  - 新增测试：验证 session_id 在整个日志链路中保持一致
  - 新增测试：验证日志文件按天命名格式正确
  - 新增测试：验证敏感信息（API key）被脱敏
  - _需求：13.2, 13.3, 13.4, 13.5, 14.5_

- [ ] 14. 运行完整回归测试套件并修复失败
  - 运行 `pytest tests/unit/ -v` 确保所有单元测试通过
  - 运行 `pytest tests/integration/ -v` 确保所有集成测试通过
  - 运行 `pytest tests/ -v` 确保完整测试套件无回归失败
  - 如有因日志格式变更导致的测试失败，更新测试断言以适配新格式（不删除测试）
  - 验证所有业务逻辑代码路径未被修改（仅新增日志语句）
  - _需求：13.1, 13.4, 13.6, 14.1_
