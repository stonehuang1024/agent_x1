
# 需求文档：日志与展示系统优化

## 引言

Agent X1 当前的日志系统基于 `loguru` + 标准 `logging` 双轨并行，存在以下问题：

1. **用户界面信息混乱**：运行时日志（DEBUG/INFO 级别的内部日志）与用户需要看到的状态信息混在一起，缺乏清晰的视觉层次
2. **日志系统冗余**：`logger.py` 中同时存在 loguru 和标准 logging 两套系统，但未做清晰分工
3. **缺乏结构化的运行时展示**：任务执行时没有清晰的步骤状态、工具调用进度、Token 消耗等实时信息展示
4. **日志存储分散**：`session_llm.md`（旧架构）、`history_session.md`、loguru 控制台输出、标准 logging 文件输出各自为政
5. **缺乏语义化的视觉区分**：错误、警告、成功、进度等信息没有统一的视觉标识系统

参考 Claude Code 的设计理念，本次优化目标是建立一个 **分层、通用、简洁** 的日志与展示系统，将"给用户看的信息"和"给开发者调试的日志"彻底分离，同时提供统一的结构化日志存储。

---

## 需求

### 需求 1：统一日志基础设施（Logger 重构）

**用户故事：** 作为一名开发者，我希望有一个统一的日志基础设施，以便所有模块使用一致的日志接口，消除 loguru 与标准 logging 的冗余并存。

#### 验收标准

1. WHEN 系统启动 THEN 日志系统 SHALL 初始化为单一的统一日志后端（基于标准 logging），移除 loguru 依赖
2. WHEN 任何模块调用 `get_logger(name)` THEN 日志系统 SHALL 返回一个配置一致的 Logger 实例，支持 DEBUG/INFO/WARNING/ERROR/CRITICAL 五个级别
3. WHEN 日志消息产生 THEN 日志系统 SHALL 按照统一格式输出，包含：时间戳（ISO 毫秒精度）、日志级别、模块名、行号、消息内容
4. WHEN 配置文件或环境变量指定日志级别 THEN 日志系统 SHALL 动态调整全局日志级别
5. IF 日志输出目标为文件 THEN 日志系统 SHALL 支持按大小轮转（默认 10MB，保留 5 个备份）和独立的 error 日志文件

### 需求 2：分层日志架构（三级日志体系）

**用户故事：** 作为一名开发者，我希望日志按照操作层、会话层、业务层三级分层，以便不同场景下能快速定位所需信息。

#### 验收标准

1. WHEN 工具被调用 THEN 操作层日志 SHALL 记录：工具名、参数摘要、执行结果（成功/失败）、耗时（毫秒）、输出大小
2. WHEN LLM API 被调用 THEN 操作层日志 SHALL 记录：迭代次数、输入/输出 Token 数、耗时、停止原因、工具调用列表
3. WHEN 会话开始或结束 THEN 会话层日志 SHALL 记录：会话 ID、工作目录、启动时间、运行编号、结束原因、总 Token 消耗、总耗时
4. WHEN 每轮对话完成 THEN 会话层日志 SHALL 记录：轮次编号、用户输入摘要、助手响应摘要、工具调用次数
5. WHEN Shell 命令被执行 THEN 操作层日志 SHALL 记录：命令内容、退出码、标准输出/错误摘要
6. WHEN 文件被创建/修改/删除 THEN 操作层日志 SHALL 记录：文件路径、变更类型（CRUD）、内容摘要（前 100 字符）

### 需求 3：用户界面实时状态展示（Console Display）

**用户故事：** 作为一名用户，我希望在任务运行时看到清晰、简洁的实时状态信息，以便了解 Agent 当前在做什么、进度如何。

#### 验收标准

1. WHEN AgentLoop 开始新的迭代步骤 THEN 展示系统 SHALL 在控制台显示步骤编号和当前状态（如 `⚙️ Step 3/50 | Assembling context...`）
2. WHEN LLM 调用完成 THEN 展示系统 SHALL 显示简洁的统计信息：Token 消耗、耗时（如 `📊 Tokens: 1,234→567 | 2.3s`）
3. WHEN 工具被调用 THEN 展示系统 SHALL 显示工具名称和简要状态（如 `🔧 Running: read_file → /path/to/file`）
4. WHEN 工具执行完成 THEN 展示系统 SHALL 显示结果状态：成功（✅）或失败（❌），附带耗时
5. WHEN 多个工具并行执行 THEN 展示系统 SHALL 显示并行执行的工具列表和各自状态
6. WHEN 发生错误 THEN 展示系统 SHALL 以红色/醒目样式显示错误信息，区别于正常输出
7. WHEN 发生警告（如循环检测触发） THEN 展示系统 SHALL 以黄色/警告样式显示
8. WHEN 会话结束 THEN 展示系统 SHALL 显示会话摘要：总耗时、总 Token 消耗、LLM 调用次数、工具调用次数

### 需求 4：结构化日志存储

**用户故事：** 作为一名开发者，我希望所有日志以结构化格式持久化存储，以便后续分析、调试和审计。

#### 验收标准

1. WHEN 会话启动 THEN 日志存储系统 SHALL 在会话目录下创建结构化日志文件（JSON Lines 格式），文件名为 `session_log.jsonl`
2. WHEN 任何可记录事件发生 THEN 日志存储系统 SHALL 将事件以 JSON 对象追加到日志文件，包含：时间戳、事件类型、会话 ID、数据负载
3. WHEN 会话结束 THEN 日志存储系统 SHALL 生成一份人类可读的会话摘要文件 `session_summary.md`，包含：会话统计、操作步骤列表、Token 消耗明细
4. IF 日志文件超过配置的大小限制 THEN 日志存储系统 SHALL 自动轮转，保留最近 N 个日志文件
5. WHEN 需要查询历史日志 THEN 日志存储系统 SHALL 提供按时间范围、事件类型、会话 ID 过滤的查询接口

### 需求 5：Display 与 Log 分离架构

**用户故事：** 作为一名开发者，我希望"给用户看的展示信息"和"给开发者调试的日志"完全分离，以便用户看到干净的界面，开发者也能获取详细的调试信息。

#### 验收标准

1. WHEN 系统初始化 THEN 架构 SHALL 提供两个独立的输出通道：`ConsoleDisplay`（用户界面）和 `Logger`（开发者日志）
2. WHEN `ConsoleDisplay` 输出信息 THEN 信息 SHALL 仅包含用户关心的内容（状态、进度、结果），不包含内部调试信息（文件名、行号、线程 ID 等）
3. WHEN `Logger` 记录日志 THEN 日志 SHALL 包含完整的调试信息（时间戳、模块、行号、线程等），输出到文件而非控制台
4. IF 用户通过 `--verbose` 或 `--debug` 标志启动 THEN 系统 SHALL 将 Logger 的输出也显示到控制台，与 Display 信息并存
5. WHEN 模块需要同时输出用户信息和调试日志 THEN 模块 SHALL 通过 `display.status()` 输出用户信息，通过 `logger.debug()` 输出调试信息，两者互不干扰

### 需求 6：语义化视觉标识系统

**用户故事：** 作为一名用户，我希望不同类型的信息有明确的视觉区分（颜色、图标），以便快速识别信息的重要性和类型。

#### 验收标准

1. WHEN 展示系统输出信息 THEN 信息 SHALL 根据类型使用统一的前缀图标：
   - 状态/进度：⚙️
   - 成功：✅
   - 失败/错误：❌
   - 警告：⚠️
   - 信息：ℹ️
   - 工具调用：🔧
   - LLM 调用：🤖
   - 统计：📊
   - 会话：📋
   - 用户输入：👤
2. WHEN 终端支持 ANSI 颜色 THEN 展示系统 SHALL 使用语义化颜色：
   - 错误信息：红色
   - 警告信息：黄色
   - 成功信息：绿色
   - 进度/状态信息：青色
   - 统计数据：蓝色
   - 用户输入：白色/默认
3. IF 终端不支持颜色 THEN 展示系统 SHALL 优雅降级为纯文本输出，仅保留图标前缀

### 需求 7：Token 消耗与成本追踪

**用户故事：** 作为一名用户，我希望实时了解当前会话的 Token 消耗情况，以便控制使用成本。

#### 验收标准

1. WHEN 每次 LLM 调用完成 THEN 追踪系统 SHALL 累计记录：本次输入 Token、本次输出 Token、会话累计输入 Token、会话累计输出 Token
2. WHEN 用户请求查看统计（如 `/stats` 命令） THEN 追踪系统 SHALL 显示：会话总 Token、LLM 调用次数、工具调用次数、会话总耗时
3. WHEN 会话结束 THEN 追踪系统 SHALL 在会话摘要中包含完整的 Token 消耗明细

### 需求 8：事件驱动的日志集成

**用户故事：** 作为一名开发者，我希望日志系统与现有的 EventBus 事件总线集成，以便通过事件订阅机制自动触发日志记录和展示更新。

#### 验收标准

1. WHEN EventBus 发射 `TOOL_SUCCEEDED` 或 `TOOL_FAILED` 事件 THEN 日志集成层 SHALL 自动记录工具操作日志并更新控制台展示
2. WHEN EventBus 发射 `LLM_CALL_COMPLETED` 事件 THEN 日志集成层 SHALL 自动记录 LLM 调用日志并更新 Token 统计展示
3. WHEN EventBus 发射 `SESSION_COMPLETED` 或 `SESSION_FAILED` 事件 THEN 日志集成层 SHALL 自动生成会话摘要
4. WHEN EventBus 发射 `LOOP_DETECTED` 事件 THEN 日志集成层 SHALL 自动在控制台显示循环检测警告
5. WHEN EventBus 发射 `STATE_CHANGED` 事件 THEN 日志集成层 SHALL 自动更新控制台的状态显示

### 需求 9：实时动态活动流（Live Activity Stream）

**用户故事：** 作为一名用户，我希望在任务运行时能够实时看到 Agent 的每一个动作（工具调用、调用结果、LLM 请求、LLM 返回等），以便随时掌握任务的执行进展和当前正在做的事情。

#### 验收标准

1. WHEN Agent 发起 LLM 请求 THEN 活动流 SHALL 实时显示一条 LLM 请求条目，包含：迭代编号、输入消息数量（如 `🤖 LLM Request #3 | 12 messages | sending...`）
2. WHEN LLM 返回响应 THEN 活动流 SHALL 实时显示一条 LLM 响应条目，包含：Token 消耗、耗时、响应类型（文本/工具调用）（如 `🤖 LLM Response #3 | 1,234→567 tokens | 2.3s | 3 tool calls`）
3. WHEN LLM 返回文本内容 THEN 活动流 SHALL 显示响应文本的前 N 个字符（默认 200），超出部分以 `... [truncated, 1,234 chars total]` 标记截断
4. WHEN 工具调用开始 THEN 活动流 SHALL 实时显示工具名称和关键参数摘要（如 `🔧 Tool: read_file → path="/src/main.py"`），参数摘要超过 100 字符时截断
5. WHEN 工具调用返回结果 THEN 活动流 SHALL 显示结果状态和输出摘要：成功时显示输出前 N 个字符（默认 150），失败时显示错误信息前 200 字符（如 `✅ read_file (120ms) → "import os\nimport sys..." [truncated, 2,345 chars]`）
6. WHEN 多个工具并行执行 THEN 活动流 SHALL 按完成顺序逐条显示每个工具的结果，并标注并行批次（如 `⚙️ Parallel batch [3 tools]`）
7. WHEN 循环检测触发 THEN 活动流 SHALL 以醒目的警告样式显示检测信息（如 `⚠️ Loop detected: repeated pattern in last 5 iterations`）
8. WHEN 活动流条目中包含多行文本（如工具输出、LLM 响应） THEN 系统 SHALL 将换行符替换为可视化标记（如 `↵`）并压缩为单行显示，保持活动流的紧凑性
9. WHEN 活动流条目的总长度超过终端宽度 THEN 系统 SHALL 在终端宽度处截断并附加省略标记（`...`），避免换行导致的视觉混乱
10. IF 用户通过 `--verbose` 标志启动 THEN 活动流 SHALL 增加显示更多细节：完整的工具参数、LLM 响应的前 500 字符、工具输出的前 300 字符
11. WHEN 每条活动流条目输出 THEN 条目 SHALL 包含相对时间戳前缀（如 `[+0:32]`），表示自会话开始以来的经过时间，方便用户感知任务节奏

---

## 技术约束与边界

1. **兼容性**：新系统必须兼容现有的 `get_logger(__name__)` 调用方式，现有模块无需大规模修改
2. **性能**：日志写入不应阻塞主执行流程，文件 I/O 应使用缓冲写入
3. **依赖最小化**：移除 loguru 依赖，仅使用 Python 标准库 `logging` 模块
4. **向后兼容**：旧版 `SessionManager` 的 `session_llm.md` 日志格式在过渡期内保留，新系统并行运行
5. **终端兼容**：支持 macOS/Linux 终端，Windows 终端的 ANSI 颜色支持为可选项


# 实施计划：日志与展示系统优化

- [ ] 1. 重构统一日志基础设施 (`src/util/logger.py`)
   - 移除 `loguru` 依赖：删除 `from loguru import logger as __inside_rename_logger`、`__inside_rename_logger.remove()`、`__inside_rename_logger.add(...)` 等所有 loguru 相关代码
   - 移除 `pyproject.toml` / `requirements.txt` 中的 loguru 依赖项
   - 保留并完善 `setup_logging()`、`get_logger()`、`set_log_level()` 等标准 logging 接口，确保所有现有模块的 `get_logger(__name__)` 调用无需修改
   - 确保 `xlog = get_logger(__name__)` 保持向后兼容
   - 支持通过环境变量 `AGENT_X1_LOG_LEVEL` 动态调整日志级别
   - _需求：1.1, 1.2, 1.3, 1.4, 1.5_

- [ ] 2. 实现 ConsoleDisplay 用户界面展示模块 (`src/util/display.py`)
   - 创建 `ConsoleDisplay` 类，作为所有用户可见输出的统一出口，与 Logger（开发者日志）彻底分离
   - 实现语义化图标前缀系统：⚙️(状态) / ✅(成功) / ❌(错误) / ⚠️(警告) / ℹ️(信息) / 🔧(工具) / 🤖(LLM) / 📊(统计) / 📋(会话) / 👤(用户)
   - 实现 ANSI 颜色输出（错误红/警告黄/成功绿/进度青/统计蓝），终端不支持颜色时优雅降级为纯文本+图标
   - 提供核心方法：`status()`, `success()`, `error()`, `warning()`, `info()`, `tool_start()`, `tool_end()`, `llm_stats()`, `session_summary()`
   - 实现 `--verbose` / `--debug` 模式开关：verbose 模式下将 Logger 调试信息也输出到控制台
   - _需求：3.1-3.8, 5.1-5.5, 6.1-6.3_

- [ ] 3. 实现实时动态活动流 (`src/util/activity_stream.py`)
   - 创建 `ActivityStream` 类，依赖 `ConsoleDisplay` 进行实际输出，专注于活动流条目的格式化和截断逻辑
   - 实现 LLM 请求/响应条目格式化：
     - 请求：`[+M:SS] 🤖 LLM Request #N | X messages | sending...`
     - 响应：`[+M:SS] 🤖 LLM Response #N | X→Y tokens | Z.Zs | N tool calls`
   - 实现工具调用条目格式化：
     - 开始：`[+M:SS] 🔧 Tool: tool_name → key_param="value"` （参数摘要超 100 字符截断）
     - 成功：`[+M:SS] ✅ tool_name (120ms) → "output preview..."` （输出前 150 字符，截断标记）
     - 失败：`[+M:SS] ❌ tool_name (120ms) → "error message..."` （错误前 200 字符）
   - 实现并行批次标注：`[+M:SS] ⚙️ Parallel batch [N tools]`，按完成顺序逐条显示
   - 实现循环检测警告：`[+M:SS] ⚠️ Loop detected: ...` 醒目样式
   - 实现文本压缩：多行文本中换行符替换为 `↵`，压缩为单行
   - 实现终端宽度截断：超出终端宽度时截断并附加 `...`
   - 实现相对时间戳前缀 `[+M:SS]`，基于会话开始时间计算
   - 实现 LLM 文本响应截断：默认 200 字符，`--verbose` 模式下 500 字符
   - 实现 `--verbose` 模式增强：完整工具参数、更长的输出预览（300 字符）
   - _需求：9.1-9.11_

- [ ] 4. 实现结构化日志存储模块 (`src/util/structured_log.py`)
   - 创建 `StructuredLogger` 类，负责将事件以 JSONL 格式持久化到 `session_log.jsonl`
   - 定义日志事件类型枚举：`TOOL_CALL`, `LLM_CALL`, `FILE_CHANGE`, `SHELL_EXEC`, `SESSION_START`, `SESSION_END`, `TURN_COMPLETE`
   - 每条 JSON 记录包含：`timestamp`（ISO 毫秒精度）、`event_type`、`session_id`、`data`（事件负载）
   - 实现会话结束时生成 `session_summary.md`：会话统计、操作步骤列表、Token 消耗明细
   - 实现日志文件大小轮转机制（默认 10MB，保留 5 个备份）
   - 使用缓冲写入，确保日志 I/O 不阻塞主执行流程
   - _需求：4.1, 4.2, 4.3, 4.4, 4.5_

- [ ] 5. 实现 Token 消耗追踪器 (`src/util/token_tracker.py`)
   - 创建 `TokenTracker` 类，维护会话级别的 Token 累计统计
   - 记录每次 LLM 调用的输入/输出 Token 数，维护会话累计输入/输出 Token
   - 记录 LLM 调用次数、工具调用次数、会话总耗时
   - 提供 `get_stats()` 方法返回当前统计数据字典，供 `ConsoleDisplay`、`ActivityStream` 和 `StructuredLogger` 使用
   - 提供 `record_llm_call()` 和 `record_tool_call()` 方法
   - _需求：7.1, 7.2, 7.3_

- [ ] 6. 实现 EventBus 日志集成层 (`src/util/log_integration.py`)
   - 创建 `LogIntegration` 类，作为 EventBus 与日志/展示系统之间的桥梁
   - 持有 `ConsoleDisplay`、`ActivityStream`、`StructuredLogger`、`TokenTracker` 的引用
   - 订阅 `TOOL_SUCCEEDED` / `TOOL_FAILED` → 调用 `ActivityStream` 输出工具结果条目 + 调用 `StructuredLogger` 记录操作层日志 + 调用 `TokenTracker.record_tool_call()`
   - 订阅 `LLM_CALL_STARTED` → 调用 `ActivityStream` 输出 LLM 请求条目
   - 订阅 `LLM_CALL_COMPLETED` → 调用 `ActivityStream` 输出 LLM 响应条目 + 调用 `TokenTracker.record_llm_call()` + 调用 `StructuredLogger` 记录 LLM 调用日志
   - 订阅 `SESSION_COMPLETED` / `SESSION_FAILED` → 调用 `StructuredLogger` 生成会话摘要 + 调用 `ConsoleDisplay.session_summary()`
   - 订阅 `LOOP_DETECTED` → 调用 `ActivityStream` 输出循环检测警告
   - 订阅 `STATE_CHANGED` → 调用 `ConsoleDisplay.status()` 更新状态
   - 提供 `setup(event_bus)` 方法一键注册所有事件处理器
   - 扩展 EventBus 事件的 `data` 负载，确保携带足够信息（如 Token 数、耗时、工具参数等）
   - _需求：8.1-8.5, 2.1, 2.2_

- [ ] 7. 集成到 AgentLoop 运行时 (`src/runtime/agent_loop.py`)
   - 在 `AgentLoop.__init__()` 中接收 `ConsoleDisplay` 和 `ActivityStream` 实例（可选参数，保持向后兼容）
   - 在迭代循环开头调用 `ConsoleDisplay.status()` 显示步骤编号（`⚙️ Step N/max`）
   - 在 `_call_llm()` 调用前通过 `_emit()` 发射 `LLM_CALL_STARTED` 事件，携带 `iteration`、`message_count` 数据
   - 在 `_call_llm()` 返回后通过 `_emit()` 发射 `LLM_CALL_COMPLETED` 事件，携带 `iteration`、`input_tokens`、`output_tokens`、`duration_ms`、`tool_call_count`、`content_preview` 数据
   - 在工具调用前通过 `_emit()` 发射 `TOOL_CALLED` 事件，携带 `tool_name`、`arguments_summary` 数据
   - 在并行工具执行时，先通过 `ActivityStream` 输出并行批次标注
   - 在会话结束时调用 `ConsoleDisplay.session_summary()` 显示会话摘要
   - 保留现有的 `_log_to_session_llm()` 调用（过渡期向后兼容）
   - _需求：3.1-3.5, 3.8, 9.1-9.6, 2.1-2.4_

- [ ] 8. 集成到 ToolScheduler 和 SessionManager
   - 在 `ToolScheduler.execute()` 中，工具执行前发射 `TOOL_CALLED` 事件（携带工具名、参数摘要）
   - 确保 `TOOL_SUCCEEDED` / `TOOL_FAILED` 事件携带完整数据负载：`tool_name`、`arguments`、`duration_ms`、`result_preview`（前 150 字符）、`result_length`、`error_message`
   - 在 `SessionManager` 中，会话创建时初始化 `StructuredLogger`（创建 `session_log.jsonl`）
   - 在 `SessionManager` 中，会话结束时触发 `StructuredLogger` 生成 `session_summary.md`
   - 在每轮对话完成时记录会话层日志（轮次编号、用户输入摘要、工具调用次数）
   - 对 Shell 命令工具记录命令内容、退出码、标准输出/错误摘要
   - 对文件工具记录文件路径、变更类型（CRUD）、内容摘要
   - _需求：2.1, 2.3-2.6, 4.1, 4.3, 8.1-8.2_

- [ ] 9. 添加 CLI 参数和启动初始化 (`main.py`)
   - 在 `parse_arguments()` 中添加 `--verbose` 和 `--debug` 命令行参数
   - 在 `AppConfig` 中添加日志相关配置项：`log_level`、`log_dir`、`enable_structured_log`、`enable_color_output`、`verbose_mode`
   - 在 `main()` 函数中，系统启动时初始化完整的日志展示链：
     1. 创建 `ConsoleDisplay` 实例（根据 `--verbose`/`--debug` 配置）
     2. 创建 `ActivityStream` 实例（传入 `ConsoleDisplay` 和 verbose 标志）
     3. 创建 `StructuredLogger` 实例（传入会话目录）
     4. 创建 `TokenTracker` 实例
     5. 创建 `LogIntegration` 实例并调用 `setup(event_bus)` 一键注册
   - 将 `ConsoleDisplay` 和 `ActivityStream` 传入 `AgentLoop` 构造函数
   - _需求：1.4, 5.4, 9.10_

- [ ] 10. 编写测试
   - 为 `ConsoleDisplay` 编写单元测试：验证图标前缀正确性、颜色输出、无颜色降级、verbose 模式切换
   - 为 `ActivityStream` 编写单元测试：
     - 验证 LLM 请求/响应条目格式（含截断逻辑）
     - 验证工具调用条目格式（参数摘要截断、输出预览截断）
     - 验证多行文本压缩（换行符→`↵`）
     - 验证终端宽度截断
     - 验证相对时间戳格式
     - 验证 `--verbose` 模式下截断阈值变化
   - 为 `StructuredLogger` 编写单元测试：验证 JSONL 写入格式、会话摘要生成、文件轮转
   - 为 `TokenTracker` 编写单元测试：验证累计统计准确性、边界值（零 Token、超大值）
   - 为 `LogIntegration` 编写集成测试：模拟 EventBus 事件发射，验证各订阅处理器正确触发日志记录和展示更新
   - 为重构后的 `logger.py` 编写测试：验证 loguru 移除后 `get_logger()` 正常工作、日志级别动态调整
   - _需求：1.1-1.5, 3.1-3.8, 4.1-4.4, 7.1-7.3, 8.1-8.5, 9.1-9.11_
