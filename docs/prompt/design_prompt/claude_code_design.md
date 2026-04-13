【角色定位】
你是一位拥有10年后端架构经验、深入研究过Anthropic Claude Code源码实现的AI系统架构师。你需要输出一份《Claude Code 内部架构技术白皮书》，目标读者是构建AI Coding Agent平台的工程团队。

【文档目标】
生成一份涵盖Claude Code核心架构的详细技术文档，特别深入解析其Multi-Agent系统的设计理念、状态隔离机制和通信协议。文档需要包含架构图描述、状态转换图、数据流和具体实现机制。

【强制要求 - 必须覆盖的模块】

### 1. Session 生命周期管理架构
详细说明Claude Code的Session体系：
- Session的创建、恢复（--resume/--continue机制）和销毁完整生命周期
- Session状态持久化层：本地文件系统存储结构、序列化格式（JSON/Markdown Artifact）、加密策略
- 长会话管理机制：30+小时运行任务的Checkpoint策略、Context Window自动清理算法、Token预算管理
- Session隔离边界：父子Session的内存隔离、文件系统视图隔离、环境变量隔离机制

### 2. Context 上下文工程系统
深度解析Context管理：
- CLAUDE.md项目记忆系统：自动注入机制、层级覆盖策略（全局 vs 项目级 vs 会话级）、动态摘要生成算法
- Context Window优化：滑动窗口策略、重要性评分算法（如何决定保留/丢弃历史消息）、Token压缩技术
- 多模态Context处理：文本、图像、结构化数据（JSON/Mermaid图表）的统一表示和序列化
- Context版本化：历史版本的Branch/Tag机制、Diff算法、回滚策略

### 3. Runtime 运行时环境
分析Node.js/TypeScript服务端架构：
- Client-Server架构：Capability Server与Coding Agent的进程分离模型、MCP（Model Context Protocol）通信协议细节
- 沙箱安全模型：文件系统沙箱（chroot/glob-pattern权限）、网络沙箱、命令执行沙箱（Bash工具的限制策略）
- 异步事件循环：消息队列设计、并发控制（Semaphore/Backpressure）、死锁预防机制
- 资源监控：内存泄漏检测、CPU配额管理、磁盘I/O节流策略

### 4. Memory 记忆系统架构
深入Memory子系统的两层设计：
- Episodic Memory（情景记忆）：短期任务上下文的存储结构、检索算法（基于向量的相似性搜索）、遗忘曲线实现
- Semantic Memory（语义记忆）：长期知识图谱、项目领域模型的构建、增量更新机制
- 记忆写入控制：写权限ACL（哪些Agent可以写入持久记忆）、Schema验证、审核循环（Review Loops）
- 记忆冲突解决：并发写入的乐观锁/悲观锁策略、最终一致性保证

### 5. Tools 工具生态系统
解析Tools的注册、发现和执行：
- 内置Tools集合：Read/Write/Edit/LS/Grep/Bash/Web Search/Task的实现细节、幂等性保证、错误重试策略
- MCP（Model Context Protocol）标准：Tools注册协议、Schema定义、Capability Discovery机制
- Hooks系统：Pre-tool Hooks（敏感文件拦截、权限验证）和Post-tool Hooks（类型检查、重复代码检测）的链式调用架构
- 自定义Tool开发：In-process MCP Server模式、Python/TypeScript SDK的Tool封装、依赖注入容器

### 6. Code Retrieval & Indexing 代码检索系统
分析代码库理解机制：
- 代码索引管道：语法解析（Tree-sitter）、符号提取、Embedding生成、增量索引更新策略
- 检索算法：混合检索（关键词+语义）、相关性排序（BM25 + Vector Similarity）、上下文切片（Context Slicing）技术
- 代码图谱构建：模块依赖图、调用链分析、数据流追踪、架构违规检测
- 实时检索优化：懒加载策略、缓存失效机制、预计算索引

### 7. Multi-Agent Architecture 多智能体架构（核心重点）
这是文档的最重要部分，必须深入以下子系统：

#### 7.1 Agent角色定义与权限模型
- Agent身份体系：通过YAML Front Matter + System Prompt定义的专业化Agent（Architect/Frontend/Backend/DB/QA/Security）
- 角色继承与组合：模型继承（model: inherit）vs 专用模型、权限矩阵（读/写/执行/网络访问的细粒度控制）
- Agent生命周期：编译期（Prompt模板渲染）→ 运行期（上下文激活）→ 终止期（产物归档）
- 可视化标识：Color Coding机制在UI层的认知辅助作用

#### 7.2 Agent间通信协议
- 父子通信机制：Task工具的实现细节、同步调用（await）vs 异步调用（fire-and-forget）、超时和取消策略
- 消息传递格式：结构化JSON协议、错误传播链（Error Chain）、Trace ID贯穿机制
- 共享状态通道：通过Capability Server的中转通信、发布-订阅模式、事件总线架构
- 跨Agent上下文引用：Resume机制的实现（如何定位并恢复先前Agent的上下文状态）

#### 7.3 Session与Context的隔离策略
- 上下文隔离边界：父Agent的Context Window与子Agent的严格分离、Token预算的独立计算
- 工作目录隔离：子Agent的CWD（Current Working Directory）沙箱、文件系统视图的限制（能否看到兄弟Agent的文件）
- 环境隔离：环境变量的继承与覆盖规则、Secret/凭证的传递策略（防止泄露给不可信子Agent）
- 并行执行模型：多子Agent并行时的资源竞争解决、死锁预防、并行度限制（并发Semaphore）

#### 7.4 协同工作流模式
- Orchestrator编排模式：主控Agent的任务分解算法（Task Decomposition）、动态路由决策（基于任务类型的Agent选择）、结果聚合策略（Result Aggregation）
- 流水线模式：规划Agent（Planner）→ 执行Agent（Worker）→ 审查Agent（Reviewer）的链式执行、产物传递格式（Artifact Passing）
- 专家会诊模式：多专业Agent并行分析同一问题（如安全+性能+架构）、冲突解决机制（Conflict Resolution）、投票共识算法
- 自我修正循环：错误检测→子Agent诊断→修复→验证的闭环控制、最大迭代次数限制、循环退出条件

#### 7.5 状态一致性与容错
- 分布式事务：跨Agent操作的原子性保证（两阶段提交/ Saga模式）、补偿事务（Compensating Transaction）
- 故障恢复：子Agent崩溃检测、自动重启策略、状态重建（State Reconstruction）机制
- 最终一致性：异步通信的乱序处理、消息去重（Idempotency Key）、重放攻击防护

【输出格式要求】
1. 使用Mermaid语法绘制：系统架构图、Agent状态机图、数据流图、时序图（描述多Agent协作流程）
2. 包含伪代码片段：展示关键算法（如Task分发、Context压缩、Memory检索）
3. 对比分析表格：Single-Agent vs Multi-Agent在Context管理、Token消耗、并行度等方面的差异
4. 边界情况分析：极端场景（Token溢出、无限递归Agent生成、循环依赖）的处理机制

【技术深度要求】
- 必须讨论实现细节：如LRU缓存的TTL策略、Checkpoint的序列化格式、MCP的JSON-RPC封装
- 必须包含性能指标：Token消耗预估、延迟瓶颈分析、内存占用估算
- 必须指出设计权衡：强一致性 vs 可用性、细粒度隔离 vs 通信开销、实时性 vs 准确性

【参考材料】
- 基于Claude Code SDK（TypeScript/Python）的最新实现（2025年6月发布）
- Agentic OS架构四层模型（Context Management / Shared Knowledge / Task Distribution / Self-Learning）
- VERO Framework的Scaffold设计模式
- Model Context Protocol (MCP) 标准规范

【输出长度】
不少于8000字的技术文档，分为7个主要章节，每个章节包含原理说明、架构设计、实现机制和最佳实践四个小节。
