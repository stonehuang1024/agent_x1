# Agent X1 Redesign Review — 设计与实现差异分析及修改需求

## 引言

本文档对 Agent X1 重设计方案（`agent_x1_redesign_session.md` 和 `agent_x1_redesign_detail_session.md`）与实际实现代码进行逐模块对比，识别差异点，并提出修改需求。

**设计文档**：
- 主设计：`docs/dev_doc/agent_x1_redesign_session.md`
- 详细设计：`docs/dev_doc/agent_x1_redesign_detail_session.md`

**对比范围**：6 大模块 — Session、Runtime、Context、Memory、Prompt、Engine Refactoring + 基础设施（DB、Events、Models）

---

## 差异总览

| 模块 | 设计要求 | 实现状态 | 差异等级 |
|------|---------|---------|---------|
| **基础设施 - DB** | `src/util/db.py` + migration | ✅ 已实现 | 🟢 一致 |
| **基础设施 - Events** | `src/core/events.py` EventBus | ✅ 已实现 | 🟡 已实现但未集成 |
| **基础设施 - Models** | Message 增加 `token_count`, `importance` | ❌ 未修改 | 🔴 缺失 |
| **Session** | 完整生命周期管理 | ✅ 基本实现 | 🟡 小差异 |
| **Runtime - AgentLoop** | 统一 Agent Loop 状态机 | ✅ 基本实现 | 🔴 多处差异 |
| **Runtime - ToolScheduler** | 独立文件 + 状态机 | ⚠️ 重复定义 | 🔴 结构问题 |
| **Runtime - LoopDetector** | 独立文件 + 高级检测 | ⚠️ 重复定义 | 🔴 结构问题 |
| **Context** | 7 层组装 + Token 预算 | ✅ 基本实现 | 🟡 小差异 |
| **Memory** | 双层记忆 + FTS5 | ✅ 基本实现 | 🟢 基本一致 |
| **Prompt** | 组件化 + sections.py | ⚠️ 部分实现 | 🔴 缺失 sections.py |
| **Engine** | 保留 chat() + 新增 call_llm() | ✅ 已实现 | 🟡 小差异 |
| **main.py 集成** | --new-arch flag | ✅ 已实现 | 🟡 小差异 |

---

## 需求

### 需求 1：修复 `src/core/models.py` — Message 缺少设计要求的字段

**用户故事：** 作为一名开发者，我希望 Message 数据类包含 `token_count` 和 `importance` 字段，以便 Context 层和 Session 层能够进行 token 预算管理和消息重要性评估。

#### 验收标准

1. WHEN Message 类被实例化 THEN 系统 SHALL 支持 `token_count: int = 0` 和 `importance: float = 0.5` 两个可选字段
2. WHEN Message.to_dict() 被调用 THEN 系统 SHALL 在输出字典中包含 `token_count` 和 `importance` 字段（如果非默认值）
3. WHEN Message.from_dict() 被调用 THEN 系统 SHALL 能够正确解析 `token_count` 和 `importance` 字段
4. IF Message 类增加了新字段 THEN 系统 SHALL 保持与现有代码的向后兼容性（默认值不影响现有逻辑）
5. WHEN Message 类增加新字段后 THEN 系统 SHALL 增加 `Message.system()` 和 `Message.user()` 两个便捷类方法（设计文档中 ContextAssembler 使用了 `Message.system()` 和 `Message.user()`）

---

### 需求 2：修复 `src/runtime/models.py` — 消除与独立文件的重复定义

**用户故事：** 作为一名开发者，我希望 Runtime 模块的类定义不存在重复，以便代码结构清晰、维护方便。

#### 验收标准

1. WHEN 查看 `src/runtime/models.py` THEN 系统 SHALL 仅包含数据模型定义（`AgentState`, `ToolExecutionState`, `ToolCallRecord`, `AgentConfig`），不包含 `ToolScheduler` 和 `LoopDetector` 的完整实现
2. WHEN `src/runtime/tool_scheduler.py` 存在 THEN `src/runtime/models.py` 中 SHALL 不再包含 `ToolScheduler` 类的重复定义
3. WHEN `src/runtime/loop_detector.py` 存在 THEN `src/runtime/models.py` 中 SHALL 不再包含 `LoopDetector` 类的重复定义
4. WHEN `src/runtime/__init__.py` 导入这些类 THEN 系统 SHALL 从正确的独立文件导入，而非从 models.py 导入重复定义

---

### 需求 3：修复 `src/runtime/agent_loop.py` — AgentLoop 与设计的多处差异

**用户故事：** 作为一名开发者，我希望 AgentLoop 严格按照设计文档实现，以便状态机驱动的执行流程正确、可靠。

#### 验收标准

1. WHEN AgentLoop 处理 tool_calls 响应 THEN 系统 SHALL 正确解析 tool_calls 的格式（当前代码从 `tc.get("name")` 取值，但 AnthropicEngine.call_llm 返回的 tool_calls 格式为 `{"id": ..., "type": "function", "function": {"name": ..., "arguments": ...}}`，需要从 `tc["function"]["name"]` 取值）
2. WHEN AgentLoop 将 assistant 的 tool_use 响应添加到 messages 时 THEN 系统 SHALL 先将 assistant 消息（包含 tool_calls）添加到 messages 列表中，再添加 tool results（当前实现缺少将 assistant 消息加入 messages 的步骤）
3. WHEN AgentLoop 调用 engine.call_llm() 时 THEN 系统 SHALL 传递正确类型的 tools 参数（设计要求传 `Dict[str, Tool]`，当前代码调用 `self.tool_scheduler.tool_registry.get_all_tools()` 需确认返回类型匹配）
4. WHEN ContextWindow 在多次 build 调用之间 THEN 系统 SHALL 重置 token 使用量（当前 ContextAssembler.build() 每次调用不会重置 ContextWindow 的 `_current_usage`，导致累积计算错误）
5. WHEN AgentLoop 完成一个 turn THEN 系统 SHALL 通过 EventBus 发出相应事件（设计文档定义了 EventBus，但 AgentLoop 未集成使用）

---

### 需求 4：修复 `src/prompt/` — 缺少 `sections.py` 独立文件

**用户故事：** 作为一名开发者，我希望 Prompt 模块按照设计文档的组件化架构实现，以便各 section 可独立测试和扩展。

#### 验收标准

1. WHEN 查看 `src/prompt/` 目录 THEN 系统 SHALL 包含独立的 `sections.py` 文件，其中定义各个 section renderer 函数
2. WHEN `PromptProvider.build_system_prompt()` 被调用 THEN 系统 SHALL 接受 `PromptContext` 参数（当前实现的 `build_system_prompt()` 方法签名接受 `context` 参数，但 `ContextAssembler._build_layers()` 中调用时未传递 `PromptContext`，使用了无参调用 `self.prompt_provider.build_system_prompt()`）
3. WHEN sections.py 被创建 THEN 系统 SHALL 包含设计文档中定义的所有 section 函数：`render_preamble`, `render_mandates`, `render_tools`, `render_skills_catalog`, `render_active_skill`, `render_project_context`, `render_guidelines`, `render_compression_instructions`, `render_loop_warning`, `render_error_recovery`
4. WHEN `src/prompt/templates/` 目录 THEN 系统 SHALL 包含 `base_system.md` 和 `compression.md` 模板文件（设计要求但未实现）

---

### 需求 5：修复 `src/core/events.py` — EventBus 已实现但未被任何模块集成

**用户故事：** 作为一名开发者，我希望 EventBus 被各模块正确集成使用，以便实现模块间的松耦合通信。

#### 验收标准

1. WHEN AgentLoop 状态发生变化 THEN 系统 SHALL 通过 EventBus 发出 `STATE_CHANGED` 事件
2. WHEN 工具执行完成 THEN 系统 SHALL 通过 EventBus 发出 `TOOL_SUCCEEDED` 或 `TOOL_FAILED` 事件
3. WHEN Session 状态变化 THEN 系统 SHALL 通过 EventBus 发出对应的 `SESSION_*` 事件
4. WHEN LLM 调用完成 THEN 系统 SHALL 通过 EventBus 发出 `LLM_CALL_COMPLETED` 或 `LLM_CALL_FAILED` 事件
5. IF EventBus 集成到各模块 THEN 系统 SHALL 保持现有功能不受影响（事件发送为非阻塞、异常安全）

---

### 需求 6：修复 `src/runtime/tool_scheduler.py` — ToolScheduler 缺少设计要求的功能

**用户故事：** 作为一名开发者，我希望 ToolScheduler 包含设计文档中要求的参数验证、重试逻辑和计时功能，以便工具执行更加健壮。

#### 验收标准

1. WHEN ToolScheduler 执行工具 THEN 系统 SHALL 记录执行耗时到 `ToolCallRecord.duration_ms`（当前 `tool_scheduler.py` 中的 `execute()` 方法未记录 duration_ms，但 `models.py` 中的重复 ToolScheduler 有记录）
2. WHEN 工具执行失败 THEN 系统 SHALL 支持重试逻辑（设计文档中 `ToolCallRecord` 有 `retry_count` 和 `max_retries` 字段，但实现中缺失）
3. WHEN ToolScheduler 验证参数 THEN 系统 SHALL 检查必需参数是否提供（设计文档中有 `_validate_arguments` 方法，但实现中缺失）
4. WHEN 工具执行超时 THEN 系统 SHALL 使用 `asyncio.wait_for` 设置超时（设计文档要求但实现中缺失 timeout 处理）

---

### 需求 7：修复 `src/context/context_assembler.py` — ContextWindow 状态重置问题

**用户故事：** 作为一名开发者，我希望 ContextAssembler 在每次 build 调用时正确管理 token 预算，以便不会因为累积计算导致上下文组装失败。

#### 验收标准

1. WHEN `ContextAssembler.build()` 被调用 THEN 系统 SHALL 重置 ContextWindow 的 `_current_usage` 为 0（当前实现中 ContextWindow 是实例变量，多次调用 build() 会累积 token 使用量）
2. WHEN ContextAssembler 组装 L5 History 层 THEN 系统 SHALL 正确处理 Role 枚举值（当前代码 `Role(turn.role)` 可能因为 turn.role 是字符串 "user" 而非 Role.USER 导致问题）
3. WHEN ContextAssembler 组装上下文 THEN 系统 SHALL 按照设计的 7 层模型正确排序（当前缺少 L6 Tool Output 层的独立处理）

---

### 需求 8：修复 `src/session/session_store.py` — `get_default_store` 使用已弃用的 `get_paths()`

**用户故事：** 作为一名开发者，我希望 SessionStore 的便捷函数使用正确的配置获取方式，以便不会因为 API 变更导致运行时错误。

#### 验收标准

1. WHEN `get_default_store()` 被调用 THEN 系统 SHALL 使用 `config.paths` 而非已弃用的 `get_paths()` 函数获取路径配置
2. IF `get_paths()` 函数不存在于 `src/core/config.py` THEN 系统 SHALL 使用 `load_config().paths` 替代

---

### 需求 9：修复 `src/runtime/models.py` — ToolCallRecord 缺少设计要求的字段

**用户故事：** 作为一名开发者，我希望 ToolCallRecord 包含设计文档中定义的完整字段，以便支持重试、超时和详细的执行追踪。

#### 验收标准

1. WHEN ToolCallRecord 被创建 THEN 系统 SHALL 包含以下设计要求的字段：`output_truncated: bool`, `retry_count: int`, `max_retries: int`, `started_at: Optional[datetime]`, `completed_at: Optional[datetime]`, `timeout_seconds: int`
2. WHEN `mark_success()` 被调用 THEN 系统 SHALL 记录 `completed_at` 时间戳和计算 `duration_ms`
3. WHEN `mark_error()` 被调用 THEN 系统 SHALL 记录 `completed_at` 时间戳
4. WHEN 检查是否可重试 THEN 系统 SHALL 提供 `can_retry()` 方法

---

### 需求 10：修复 `src/prompt/prompt_provider.py` — PromptProvider.build_system_prompt 调用不一致

**用户故事：** 作为一名开发者，我希望 PromptProvider 的接口调用方式在所有调用点保持一致，以便不会因为参数不匹配导致运行时错误。

#### 验收标准

1. WHEN `ContextAssembler._build_layers()` 调用 `prompt_provider.build_system_prompt()` THEN 系统 SHALL 传递正确的 `PromptContext` 参数（当前无参调用会导致 TypeError，因为 `build_system_prompt` 需要 `context: PromptContext` 参数）
2. WHEN `PromptProvider.build_system_prompt()` 被调用 THEN 系统 SHALL 支持无参调用（使用默认 PromptContext）或接受 PromptContext 参数
3. IF PromptProvider 支持无参调用 THEN 系统 SHALL 使用合理的默认 PromptContext 值

---

### 需求 11：修复 `src/engine/kimi_engine.py` — `_call_llm()` 委托方式导致重复 system prompt

**用户故事：** 作为一名开发者，我希望 KimiEngine 的内部 `_call_llm()` 方法正确委托到 `call_llm()`，以便不会导致 system prompt 被重复注入。

#### 验收标准

1. WHEN KimiEngine._call_llm() 委托到 call_llm() THEN 系统 SHALL 不会导致 system prompt 被重复添加（当前 `_call_llm()` 传递 `self.messages` 给 `call_llm()`，而 `call_llm()` 会再次添加 system message，如果 `self.messages` 中已有 system message 则会重复）
2. WHEN KimiEngine.call_llm() 构建消息列表 THEN 系统 SHALL 检查传入的 messages 中是否已包含 system message，避免重复添加

---

### 需求 12：确保 `src/prompt/templates/` 目录和模板文件存在

**用户故事：** 作为一名开发者，我希望 Prompt 模板目录和文件按设计文档创建，以便支持静态 prompt 片段的管理。

#### 验收标准

1. WHEN 查看 `src/prompt/templates/` 目录 THEN 系统 SHALL 包含 `base_system.md` 文件（Agent X1 的基础人设描述）
2. WHEN 查看 `src/prompt/templates/` 目录 THEN 系统 SHALL 包含 `compression.md` 文件（用于 ContextCompressor 调用 LLM 做摘要时的指令模板）

---

## 差异详细分析

### A. 基础设施层

#### A.1 `src/util/db.py` — ✅ 一致
- DatabaseManager 实现了 WAL 模式、foreign keys、migration 运行
- 全局实例管理 `get_db_manager()` / `reset_db_manager()`
- 与设计一致

#### A.2 `data/migrations/001_init.sql` — ✅ 一致
- 包含 sessions、turns、checkpoints、episodic_memory、semantic_memory、memory_fts 共 6 个表 + 2 个视图
- Schema 与设计文档一致，且增加了额外的 CHECK 约束和视图（超出设计，是好事）

#### A.3 `src/core/events.py` — 🟡 已实现但未集成
- EventBus 实现完整（subscribe/emit/unsubscribe）
- AgentEvent 枚举包含所有设计要求的事件类型
- **问题**：没有任何模块实际使用 EventBus（AgentLoop、SessionManager、ToolScheduler 都未集成）

#### A.4 `src/core/models.py` — 🔴 缺失字段
- **设计要求**：Message 增加 `token_count: int` 和 `importance: float` 字段
- **实际**：Message 类未做任何修改，仍然只有 `role`, `content`, `tool_calls`, `tool_call_id`, `name`
- **设计要求**：Message 需要 `Message.system()` 和 `Message.user()` 类方法
- **实际**：缺少这两个便捷方法，但 ContextAssembler 中使用了 `Message.system()` 和 `Message.user()`

### B. Session 模块

#### B.1 `src/session/models.py` — 🟢 基本一致
- SessionStatus 枚举包含所有 8 个状态 ✅
- Session 数据类包含 token budget、统计字段 ✅
- Turn 数据类包含 latency_ms（超出设计，是好事）✅
- Checkpoint 数据类完整 ✅
- TokenBudget 数据类完整 ✅

#### B.2 `src/session/session_store.py` — 🟡 小问题
- CRUD 操作完整 ✅
- Checkpoint 管理完整 ✅
- **问题**：`get_default_store()` 使用 `from src.core.config import get_paths`，但 config.py 中可能没有 `get_paths` 函数

#### B.3 `src/session/session_manager.py` — 🟢 基本一致
- 生命周期管理完整（create/resume/pause/complete/fail/archive）✅
- Fork 支持 ✅
- Checkpoint 管理 ✅
- Token budget 管理 ✅
- 状态变更回调 ✅

### C. Runtime 模块

#### C.1 `src/runtime/models.py` — 🔴 严重问题：重复定义
- 文件中包含了 `ToolScheduler` 和 `LoopDetector` 的完整实现
- 同时 `src/runtime/tool_scheduler.py` 和 `src/runtime/loop_detector.py` 也各自有独立实现
- 这导致了三份代码，且 `__init__.py` 从 `models.py` 和独立文件都导入了类
- `ToolCallRecord` 缺少设计要求的 `retry_count`, `max_retries`, `started_at`, `completed_at`, `timeout_seconds`, `output_truncated` 字段

#### C.2 `src/runtime/agent_loop.py` — 🔴 多处差异
- **tool_calls 解析格式错误**：代码从 `tc.get("name")` 取值，但 engine.call_llm() 返回的格式是 `{"function": {"name": ...}}`
- **缺少 assistant 消息追加**：当 LLM 返回 tool_calls 时，应先将 assistant 消息（含 tool_calls）加入 messages，再加 tool results
- **ContextWindow 未重置**：多次迭代中 ContextAssembler.build() 不会重置 token 计数
- **未集成 EventBus**

#### C.3 `src/runtime/tool_scheduler.py` — 🟡 功能不完整
- 基本执行逻辑正确 ✅
- ThreadPoolExecutor 并行支持 ✅
- **缺失**：参数验证 `_validate_arguments()`
- **缺失**：执行计时 `duration_ms`
- **缺失**：超时处理 `asyncio.wait_for`
- **缺失**：重试逻辑

#### C.4 `src/runtime/loop_detector.py` — 🟢 基本一致
- 滑动窗口检测 ✅
- 参数归一化 ✅
- 警告消息生成 ✅
- reset() 方法 ✅

### D. Context 模块

#### D.1 `src/context/context_window.py` — 🟢 一致
- Token 预算管理 ✅
- 阈值检查 ✅
- 估算方法 ✅

#### D.2 `src/context/context_assembler.py` — 🟡 小差异
- 7 层模型基本实现（缺少 L6 Tool Output 独立层）
- **问题**：`prompt_provider.build_system_prompt()` 无参调用，但方法签名需要 `PromptContext`
- **问题**：ContextWindow 在多次 build() 调用间不重置

#### D.3 `src/context/context_compressor.py` — 🟢 一致
- 消息压缩 ✅
- 历史压缩 ✅
- 紧急截断 ✅

### E. Memory 模块

#### E.1 — 🟢 基本一致
- 双层记忆（Episodic + Semantic）✅
- FTS5 全文搜索 ✅
- 遗忘曲线 ✅
- ProjectMemory 发现与加载 ✅
- MemoryController 完整接口 ✅

### F. Prompt 模块

#### F.1 `src/prompt/prompt_provider.py` — 🟡 部分实现
- PromptContext 数据类 ✅
- PromptProvider 组件化组装 ✅
- **缺失**：`sections.py` 独立文件（设计要求 section renderers 为独立函数文件）
- **缺失**：`templates/` 目录和模板文件
- **问题**：所有 section renderers 作为 PromptProvider 的私有方法实现，而非独立函数

### G. Engine 重构

#### G.1 — 🟢 基本一致
- `BaseEngine.call_llm()` 抽象方法已定义 ✅
- `AnthropicEngine.call_llm()` 已实现 ✅
- `KimiEngine.call_llm()` 已实现 ✅
- `chat()` 方法保留为向后兼容 ✅
- **小问题**：KimiEngine._call_llm() 委托到 call_llm() 时可能导致 system prompt 重复

### H. main.py 集成

#### H.1 — 🟡 基本一致
- `--new-arch` flag ✅
- `create_agent_loop()` 函数 ✅
- 交互模式和单查询模式都支持 agent_loop ✅
- **问题**：`create_agent_loop()` 中 `ToolRegistry` 的 `get_all_tools()` 方法需确认存在




# 实施计划 — Agent X1 Redesign 设计与实现差异修复

> 基于 `.codebuddy/plan/redesign_review/requirements.md` 需求文档

---

- [ ] 1. 修复 `src/core/models.py` — 为 Message 添加缺失字段和便捷方法
   - 在 `Message` 数据类中添加 `token_count: int = 0` 和 `importance: float = 0.5` 字段
   - 添加 `Message.system(content: str)` 类方法，返回 `Message(role=Role.SYSTEM.value, content=content)`
   - 添加 `Message.user(content: str)` 类方法，返回 `Message(role=Role.USER.value, content=content)`
   - 修改 `to_dict()` 方法：当 `token_count != 0` 或 `importance != 0.5` 时包含这两个字段
   - 修改 `from_dict()` 方法：解析 `token_count` 和 `importance` 字段（带默认值）
   - 确保所有新字段有默认值，保持向后兼容
   - _需求：1.1, 1.2, 1.3, 1.4, 1.5_

- [ ] 2. 清理 `src/runtime/models.py` — 移除重复的 ToolScheduler 和 LoopDetector 定义
   - 从 `src/runtime/models.py` 中删除 `ToolScheduler` 类（第 63~103 行），保留独立文件 `tool_scheduler.py` 中的版本
   - 从 `src/runtime/models.py` 中删除 `LoopDetector` 类（第 106~150 行），保留独立文件 `loop_detector.py` 中的版本
   - 确认 `src/runtime/__init__.py` 已从正确的独立文件导入（当前已正确：`from .tool_scheduler import ToolScheduler` 和 `from .loop_detector import LoopDetector`）
   - 移除 `models.py` 中因删除类而不再需要的 import（`asyncio`, `Callable`, `ToolRegistry`, `datetime` 等）
   - _需求：2.1, 2.2, 2.3, 2.4_

- [ ] 3. 增强 `src/runtime/models.py` 中的 ToolCallRecord — 添加设计要求的字段和方法
   - 添加字段：`output_truncated: bool = False`, `retry_count: int = 0`, `max_retries: int = 2`, `started_at: Optional[datetime] = None`, `completed_at: Optional[datetime] = None`, `timeout_seconds: int = 120`
   - 修改 `mark_success()` 方法：记录 `completed_at = datetime.now()`，计算 `duration_ms`
   - 修改 `mark_error()` 方法：记录 `completed_at = datetime.now()`
   - 添加 `can_retry() -> bool` 方法：返回 `self.retry_count < self.max_retries and self.state == ToolExecutionState.ERROR`
   - 添加 `mark_started()` 方法：记录 `started_at = datetime.now()`，设置 `state = EXECUTING`
   - _需求：9.1, 9.2, 9.3, 9.4_

- [ ] 4. 修复 `src/runtime/agent_loop.py` — tool_calls 解析格式和 assistant 消息追加
   - **修复 tool_calls 解析**：将 `tc.get("name", "")` 改为 `tc.get("function", {}).get("name", "")`，将 `tc.get("arguments", {})` 改为 `json.loads(tc.get("function", {}).get("arguments", "{}"))` — 因为 `AnthropicEngine.call_llm()` 返回的 tool_calls 格式为 `{"id": ..., "type": "function", "function": {"name": ..., "arguments": ...}}`
   - **修复 ToolCallRecord.id**：使用 `tc.get("id", ...)` 而非自动生成的 uuid，以便 tool result 的 `tool_call_id` 与 LLM 返回的 id 匹配
   - **添加 assistant 消息追加**：在执行 tool calls 之前，先将 assistant 消息（包含 tool_calls 信息）添加到 messages 列表中
   - **修复 `Message.system()` 调用**：在任务 1 完成后，此处的 `Message.system(warning)` 调用将正常工作
   - 添加 `import json` 到文件头部
   - _需求：3.1, 3.2_

- [ ] 5. 修复 `src/runtime/agent_loop.py` — ToolRegistry.get_all_tools() 不存在
   - `ToolRegistry` 没有 `get_all_tools()` 方法，需要修复 `_call_llm()` 中的调用
   - 方案：在 `ToolRegistry` 中添加 `get_all_tools() -> Dict[str, Tool]` 方法（返回 `dict(self._tools)`），或者修改 `agent_loop.py` 中的调用为使用已有的 `self.tool_scheduler.tool_registry._tools`（不推荐访问私有属性）
   - 推荐方案：在 `src/core/tool.py` 的 `ToolRegistry` 类中添加 `get_all_tools()` 方法
   - _需求：3.3_

- [ ] 6. 修复 `src/context/context_assembler.py` — ContextWindow 状态重置和 PromptProvider 调用
   - **ContextWindow 重置**：在 `build()` 方法开头添加 `self.window.reset()` 调用；同时在 `ContextWindow` 类中添加 `reset()` 方法（将 `_current_usage` 置 0，清空 `_message_counts`）
   - **PromptProvider 调用修复**：将 `self.prompt_provider.build_system_prompt()` 改为 `self.prompt_provider.build_system_prompt(PromptContext())` 或使 `build_system_prompt` 支持无参调用（添加 `context: Optional[PromptContext] = None` 默认参数）
   - 推荐方案：修改 `PromptProvider.build_system_prompt()` 的签名为 `context: Optional[PromptContext] = None`，内部 `context = context or PromptContext()`
   - 添加 `from src.prompt.prompt_provider import PromptContext` 导入（如果选择在调用侧传参）
   - _需求：7.1, 10.1, 10.2, 10.3_

- [ ] 7. 增强 `src/runtime/tool_scheduler.py` — 添加计时、参数验证和超时处理
   - **添加执行计时**：在 `execute()` 方法中记录 `start_time`，执行完成后计算 `record.duration_ms`，调用 `record.mark_started()` 和 `record.mark_success()`/`record.mark_error()` 时自动记录时间
   - **添加参数验证**：添加 `_validate_arguments(tool, arguments)` 方法，检查 tool schema 中的 required 参数是否提供
   - **添加超时处理**：使用 `asyncio.wait_for(coroutine, timeout=record.timeout_seconds)` 包裹工具执行，捕获 `asyncio.TimeoutError`
   - **添加重试逻辑**：当 `record.can_retry()` 为 True 时，自动重试执行
   - **添加截断标记**：当输出被截断时，设置 `record.output_truncated = True`
   - _需求：6.1, 6.2, 6.3, 6.4_

- [ ] 8. 修复 `src/session/session_store.py` — get_default_store 使用不存在的 get_paths
   - `config.py` 中没有 `get_paths()` 函数，需要修改 `get_default_store()` 函数
   - 将 `from src.core.config import get_paths` 改为 `from src.core.config import load_config`
   - 将 `paths = get_paths()` 改为 `config = load_config()` 然后 `paths = config.paths`
   - 将 `db_path = paths.data_dir / "agent_x1.db"` 保持不变（`config.paths` 应有 `data_dir` 属性）
   - _需求：8.1, 8.2_

- [ ] 9. 创建 `src/prompt/sections.py` 和模板文件
   - **创建 `src/prompt/sections.py`**：将 `PromptProvider` 中的私有方法提取为独立的模块级函数：`render_preamble(ctx)`, `render_mandates(ctx)`, `render_tools(ctx)`, `render_skills_catalog(ctx)`, `render_active_skill(ctx)`, `render_project_context(ctx)`, `render_guidelines(ctx)`
   - 额外添加设计文档要求的函数：`render_compression_instructions(ctx)`, `render_loop_warning(ctx, warning_count)`, `render_error_recovery(ctx)`
   - **重构 `PromptProvider`**：修改 `build_system_prompt()` 调用 `sections.py` 中的独立函数，而非私有方法
   - **创建 `src/prompt/templates/` 目录**
   - **创建 `src/prompt/templates/base_system.md`**：Agent X1 基础人设描述模板
   - **创建 `src/prompt/templates/compression.md`**：ContextCompressor 摘要指令模板
   - _需求：4.1, 4.2, 4.3, 4.4, 12.1, 12.2_

- [ ] 10. 集成 EventBus 到各模块
   - **AgentLoop**：在 `__init__` 中接受可选的 `event_bus` 参数；在 `_transition()` 中发出 `STATE_CHANGED` 事件；在 `_call_llm()` 完成后发出 `LLM_CALL_COMPLETED` 或 `LLM_CALL_FAILED`；在工具执行后发出 `TOOL_SUCCEEDED` / `TOOL_FAILED`
   - **ToolScheduler**：在 `execute()` 完成后通过 EventBus 发出工具执行事件
   - **SessionManager**：在状态变更时发出 `SESSION_CREATED`, `SESSION_RESUMED`, `SESSION_COMPLETED` 等事件
   - **main.py**：在 `create_agent_loop()` 中创建 EventBus 实例并传递给各组件
   - 所有事件发送需用 try/except 包裹，确保异常安全
   - _需求：5.1, 5.2, 5.3, 5.4, 5.5_

- [ ] 11. 修复 `src/engine/kimi_engine.py` — _call_llm 委托导致 system prompt 重复
   - 修改 `KimiEngine._call_llm()` 方法：不再直接传 `self.messages` 给 `call_llm()`（因为 `call_llm()` 会再次添加 system message）
   - 方案 A：`_call_llm()` 过滤掉 `self.messages` 中的 system messages 后再传给 `call_llm()`
   - 方案 B：`_call_llm()` 不委托到 `call_llm()`，而是直接调用 API（保持原有逻辑独立）
   - 推荐方案 A：`filtered = [m for m in self.messages if m.role != Role.SYSTEM.value]`，然后 `return self.call_llm(filtered, self.tools)`
   - _需求：11.1, 11.2_

