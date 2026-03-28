# 腾讯广告推荐模型调研Agent系统设计方案

## 1. 执行摘要与背景

### 1.1 项目愿景与目标

随着大语言模型（LLM）能力的爆发，推荐系统与广告排序模型的优化范式正经历从"人工调优"向"Agent自主进化"的根本性转变。本方案旨在为腾讯广告推荐团队构建一套基于LLM Agent的自动化调研与模型优化系统，实现7×24小时不间断的模型迭代实验，将算法工程师从繁琐的调参工作中解放出来，专注于战略性的算法创新。

**核心目标：**
- **效率提升**：参考Meta REA的5倍工程效率提升，目标在6个月内实现实验吞吐量提升3-5倍 [1]
- **智能决策**：融合历史实验经验、业界最新论文与LLM世界知识，自主生成高质量优化假设
- **持续进化**：建立"假设生成→实验执行→结果分析→经验沉淀"的闭环进化机制
- **人机协同**：明确Agent与算法工程师的分工边界，实现高效的协作模式

### 1.2 业务背景：腾讯广告推荐模型现状

腾讯广告推荐系统承载着公司核心收入业务，其技术复杂度在业界处于领先水平。当前系统呈现以下特征：

|维度|现状描述|挑战|
|:---|:---|:---|
|特征规模|2000+特征，涵盖用户画像、广告创意、上下文等|特征组合空间爆炸，人工探索效率低|
|模型架构|多任务、多场景联合建模|任务间权重调优复杂，需考虑多目标平衡|
|预估目标|点击率(CTR)、转化率(CVR)预估|多目标优化存在冲突，需精细权衡|
|训练平台|太极一站式平台|系统面向人工操作设计，缺乏API化接口|
|训练周期|单次训练1天至数周|长周期导致迭代效率瓶颈|

### 1.3 核心痛点分析

通过对团队调研，识别出以下三大核心痛点：

**痛点一：模型训练周期长，人工等待成本高**。广告模型训练通常需要1天甚至更长时间，工程师在等待过程中难以并行推进多个实验方向，导致整体迭代速度受限。Meta REA通过Hibernate-and-Wake机制解决了这一问题，Agent可在训练期间休眠，任务完成后自动唤醒继续推理 [1]。

**痛点二：调参经验难以沉淀，重复试错成本高**。团队积累了大量成功与失败的实验经验，但这些知识分散在个人笔记、Wiki和口头传承中，新人难以快速获取，老经验也容易被遗忘。Google的Self-Evolving系统通过结构化的经验数据库解决了知识沉淀问题 [3]。

**痛点三：前沿论文跟踪不及时，创新方向探索不足**。业界每月发布大量推荐系统相关论文，工程师日常忙于业务迭代，难以系统性跟踪并快速验证新方法。Meta REA的Dual-Source Hypothesis Engine集成了论文跟踪Agent，可实时整合前沿研究 [1]。

## 2. 业界方案深度解析

### 2.1 Meta Ranking Engineer Agent (REA)

Meta REA是目前业界最成熟的生产级广告排序优化Agent系统，已在Facebook、Instagram等核心广告业务中上线运行 [1]。

**核心技术机制：**

**Hibernate-and-Wake（休眠与唤醒）机制**：针对广告模型训练周期长（数天至数周）的特点，REA引入了该机制。当Agent启动一个长程训练任务后，它会将当前的推理状态、思考链和上下文保存至背景系统并进入休眠状态以节省计算资源；一旦任务完成或触发特定信号，系统会自动唤醒Agent并恢复其完整状态，确保长程异步工作流的连续性 [1]。

**Dual-Source Hypothesis Engine（双源假设引擎）**：该引擎结合了两类信息源以生成高质量实验方案。一是历史洞察数据库，综合了过去数千次实验的成败经验与元数据；二是深度ML研究Agent，实时跟踪并整合前沿的机器学习论文与行业趋势。这种结合使得REA能够提出单一人工工程师难以察觉的复杂模型配置 [1][4]。

**Three-Phase Planning Framework（三阶段规划框架）**：
1. **Validation（验证阶段）**：在受限的计算预算下快速测试初步想法
2. **Combination（组合阶段）**：将多个成功的实验元素（如不同的特征组合与优化器参数）进行融合
3. **Exploitation（利用阶段）**：在全量资源下扩展并部署表现最优的配置 [2]

**生产效果数据：**

|指标维度|提升效果|业务影响|
|:---|:---|:---|
|模型准确率|提升2.0x|广告点击率与转化率显著提升|
|工程产出|提升5.0x|实验吞吐量大幅增加|
|人力效能|3人完成16人工作量|团队可聚焦于战略创新|

### 2.2 Google Self-Evolving Recommendation System

Google提出的自进化推荐系统代表了LLM Agent驱动模型优化的前沿方向，已在YouTube推荐系统中实现生产部署 [3]。

**双Agent架构设计：**

该系统利用Gemini系列模型构建了两个核心Agent，模拟专业机器学习工程师的协作：

- **Offline Agent (Inner Loop)**：负责高通量的假设生成与初步筛选。它在代理指标（Proxy Metrics）上进行快速迭代，探索新的模型架构、优化算法和奖励函数 [3]
- **Online Agent (Outer Loop)**：负责最终的生产验证。它将Offline Agent筛选出的候选方案部署到YouTube生产环境，针对"北极星"业务指标（如长期用户参与度、留存率）进行实测 [10]

**与传统AutoML的对比：**

|特性|传统AutoML|Google自进化系统|
|:---|:---|:---|
|搜索空间|预定义的超参数范围|开放式的代码、算法与奖励函数定义|
|推理能力|纯数学/统计优化|具备深度ML领域知识的逻辑推理|
|进化速度|依赖人工定义搜索模板|端到端自主迭代，发现新型优化算法|
|业务对齐|优化单一损失函数|能够理解并优化复杂的长期业务目标|

### 2.3 Karpathy autoresearch项目

Andrej Karpathy开源的autoresearch项目展示了如何用极简的代码实现自主AI研究闭环，为小规模团队提供了可行的参考路径 [7]。

**极简三文件架构：**

该项目仅用约630行代码构建了一个"AI研究员"原型，通过三个核心文件构建信任边界：

- **program.md**：人类唯一编辑的文件。定义研究大纲、基准指标（如val_bpb）、约束条件（如VRAM限制）和实验指令 [8]
- **train.py**：Agent的"沙盒"。Agent被允许修改此文件中的模型架构、优化器、超参数及注意力机制 [17]
- **prepare.py**：不可变的"信任边界"。包含数据处理和评估函数，确保所有实验的"度量衡"一致 [19]

**Ratchet Loop（棘轮循环）机制：**

系统采用"提议-训练-评估-决策"的循环。Agent每次运行固定5分钟的训练任务，提取验证指标。如果结果优于当前基准，则通过Git提交保留更改（Keep）；否则执行`git reset`回滚（Discard）。这种"只进不退"的机制确保了模型性能的稳步提升 [17]。

**实验数据：** 2天内运行约700次实验，发现20个有效改进，val_bpb从0.9979降至0.9697，训练效率提升11% [20]。

### 2.4 三大方案横向对比

|维度|Meta REA|Google Self-Evolving|Karpathy autoresearch|
|:---|:---|:---|:---|
|核心机制|Hibernate-and-Wake|双Agent (Offline/Online)|Ratchet Loop (Keep/Discard)|
|假设来源|历史数据库+论文Agent|Gemini模型推理|program.md引导推理|
|生产验证|三阶段规划(V-C-E)|北极星指标A/B Test|val_bpb验证集打分|
|复杂度|高（企业级）|高（需Gemini支持）|低（630行代码）|
|适用场景|大规模广告系统|推荐系统端到端优化|快速原型验证|

## 3. Agent技术架构设计

基于业界最新的Agent设计理念，本方案采用模块化架构，涵盖8大核心组件 [16][17]。

### 3.1 Session管理

Session管理是支撑长程实验任务的基础设施。参考Amazon Bedrock的Session Management APIs和Google ADK的三层架构，设计如下 [16][17]：

```
┌─────────────────────────────────────────────────────┐
│                   Session Layer                      │
├─────────────────────────────────────────────────────┤
│  Session: 单次对话/实验流程的完整生命周期            │
│    ├── State: 当前实验的临时状态（训练进度、指标）   │
│    └── Memory: 跨Session的长期知识库                 │
└─────────────────────────────────────────────────────┘
```

**核心能力：**
- **状态持久化**：借鉴LangGraph的Checkpointing机制，在每个执行步骤保存状态快照，支持从故障点恢复 [21]
- **Hibernate-and-Wake**：当训练任务启动后，Agent进入休眠状态，释放计算资源；任务完成后自动唤醒并恢复完整上下文
- **多Session并行**：支持多个Worker Agent同时运行独立的实验Session

### 3.2 Prompt Engineering

采用ReAct模式（Reason + Act）作为核心推理框架，结合Chain-of-Thought增强复杂逻辑处理能力 [13][15]。

**系统提示词结构：**

```python
SYSTEM_PROMPT = """
你是腾讯广告推荐模型优化Agent，具备以下能力：
1. 分析历史实验数据，识别成功模式与失败原因
2. 跟踪业界最新论文，提取可迁移的优化方法
3. 生成结构化的实验假设，包含预期收益与风险评估
4. 监控训练过程，异常时自动诊断与修复

当前实验环境：
- 特征数量：{feature_count}
- 模型架构：{model_architecture}
- 训练平台：太极一站式平台
- 核心指标：AUC, CTR, CVR

请按照 Thought -> Action -> Observation 的格式进行推理。
"""
```

**动态Prompt构建**：根据当前任务上下文实时注入相关的Few-Shot案例，提升模型在特定场景下的表现 [15]。

### 3.3 Runtime架构

参考Agent.xpu的异构调度设计和Restate的持久化执行能力，构建高可靠的运行时环境 [23][26]。

```
┌──────────────────────────────────────────────────────────┐
│                    Runtime Engine                         │
├──────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐       │
│  │ Task Queue  │  │  Executor   │  │  Monitor    │       │
│  │ (优先级调度) │  │ (异步执行)  │  │ (指标监控)  │       │
│  └─────────────┘  └─────────────┘  └─────────────┘       │
│                           │                               │
│  ┌─────────────────────────────────────────────────┐     │
│  │           Durable Execution Layer               │     │
│  │   (持久化执行，支持故障恢复与断点续跑)           │     │
│  └─────────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────┘
```

**关键特性：**
- **持久化执行**：将非确定性的LLM调用封装为持久化步骤，确保长时程任务在系统崩溃后能自动恢复 [26]
- **异步并发**：支持多个实验任务并行执行，最大化GPU资源利用率
- **资源隔离**：不同Worker Agent的任务在独立的资源池中运行，避免相互影响

### 3.4 Memory系统

采用MemoryOS提出的三层存储架构，结合Mem0的混合存储技术 [19][30]。

|记忆层级|存储内容|技术实现|保留周期|
|:---|:---|:---|:---|
|短期记忆|当前实验的对话上下文|Redis缓存|Session结束清除|
|中期记忆|近期实验的关键结果|PostgreSQL|30天滚动|
|长期记忆|成功模式、失败教训、论文知识|向量数据库+图数据库|永久保留|

**向量数据库**（如Qdrant）负责语义相似度检索，支持"查找类似的成功实验"等查询；**图数据库**（如Neo4j）用于多跳推理与复杂实体关联，如"哪些特征组合在CTR任务上表现最佳" [20]。

### 3.5 Context管理

采用Model Context Protocol (MCP)作为工具与数据连接的标准协议 [27][28]。

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Agent     │────▶│ MCP Client  │────▶│ MCP Server  │
│  (Host)     │◀────│             │◀────│ (太极平台)  │
└─────────────┘     └─────────────┘     └─────────────┘
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    │                         │                         │
              ┌─────▼─────┐           ┌───────▼───────┐         ┌───────▼───────┐
              │ 训练任务   │           │ 特征工程      │         │ 指标查询      │
              │ 管理接口   │           │ 工具接口      │         │ 工具接口      │
              └───────────┘           └───────────────┘         └───────────────┘
```

**上下文压缩**：对于长对话历史，采用模型摘要技术将Token数压缩至原始的10%以内，降低推理成本 [19]。

### 3.6 Tools集成

将太极平台的核心能力封装为LLM可调用的工具函数 [22]。

```python
from langchain.tools import tool
from pydantic import BaseModel, Field

class TrainTaskConfig(BaseModel):
    """训练任务配置Schema"""
    model_name: str = Field(description="模型名称")
    feature_list: list[str] = Field(description="使用的特征列表")
    learning_rate: float = Field(description="学习率", ge=1e-6, le=1e-2)
    epochs: int = Field(description="训练轮数", ge=1, le=100)

@tool
def submit_training_task(config: TrainTaskConfig) -> dict:
    """
    向太极平台提交训练任务。
    
    Args:
        config: 训练任务的完整配置
    
    Returns:
        包含task_id和预估完成时间的字典
    """
    # 调用太极平台API
    response = taiji_client.submit_task(config.dict())
    return {"task_id": response.task_id, "eta": response.eta}

@tool
def query_experiment_history(query: str, top_k: int = 5) -> list[dict]:
    """
    查询历史实验记录。
    
    Args:
        query: 自然语言查询，如"最近成功的特征工程实验"
        top_k: 返回的最大记录数
    
    Returns:
        匹配的历史实验记录列表
    """
    # 向量检索 + 图数据库查询
    results = memory_system.search(query, top_k)
    return results
```

### 3.7 Skill设计

参考Spring AI的"渐进式披露"模式设计技能系统 [24]。

```
skills/
├── registry.yaml          # 技能注册表（轻量级）
├── feature_engineering/   # 特征工程技能
│   ├── skill.md          # 技能指令与元数据
│   └── examples/         # Few-Shot示例
├── hyperparameter_tuning/ # 超参调优技能
│   ├── skill.md
│   └── examples/
└── model_architecture/    # 模型结构优化技能
    ├── skill.md
    └── examples/
```

**核心理念**：启动时仅加载技能注册表，只有当模型语义匹配到特定技能时，才加载完整的技能内容，保持Context窗口精简 [24]。

### 3.8 Hook机制

参考Claude Code Hooks的设计，在实验生命周期的关键节点插入钩子函数 [21][20]。

|钩子名称|触发时机|典型用途|
|:---|:---|:---|
|pre_hypothesis|假设生成前|注入历史经验约束|
|post_hypothesis|假设生成后|合规性检查、资源预估|
|pre_train|训练启动前|配置校验、资源申请|
|on_metric_update|指标更新时|异常检测、早停判断|
|post_train|训练完成后|结果归档、经验提取|
|on_error|发生错误时|自动诊断、恢复策略|

```python
class ExperimentHooks:
    def pre_hypothesis(self, context: dict) -> dict:
        """假设生成前的钩子"""
        # 注入历史失败教训
        failed_patterns = memory.get_failed_patterns()
        context["avoid_patterns"] = failed_patterns
        return context
    
    def post_train(self, result: dict) -> None:
        """训练完成后的钩子"""
        # 提取并存储经验
        if result["auc_delta"] > 0.001:
            memory.store_success_pattern(result)
        else:
            memory.store_failure_pattern(result)
```

## 4. 技术选型深度对比

### 4.1 方案A：从0自建（参考autoresearch）

**实现思路**：参考Karpathy的autoresearch项目，从零构建轻量级Agent框架，约1000-2000行核心代码。

**优点：**
- 完全可控，可深度定制以适配太极平台
- 代码量小，易于理解和维护
- 无第三方依赖风险，长期稳定性高
- 可逐步演进，不需要一次性投入大量资源

**缺点：**
- 需要自行实现Session管理、Memory系统等基础设施
- 缺乏成熟的Multi-Agent协作框架
- 工程化程度低，可能存在边界情况处理不完善

**开发周期**：3-4个月达到MVP

**适用场景**：团队具备较强的工程能力，追求极致可控性

### 4.2 方案B：Claude Code SDK二次开发

**实现思路**：基于Anthropic的Claude Code SDK进行二次开发，利用其成熟的Agent能力 [1]。

**优点：**
- SDK成熟度高，提供完整的Session、Memory、Tools框架
- MCP协议原生支持，工具集成便捷
- Claude模型在代码理解与生成方面表现优异
- 社区活跃，文档完善

**缺点：**
- 对Claude模型有强依赖，存在供应商锁定风险
- 定制化能力受限于SDK的扩展机制
- 部分高级特性可能需要Enterprise订阅
- 需要网络访问Claude API，存在延迟和稳定性考量

**开发周期**：2-3个月达到MVP

**适用场景**：追求快速上线，愿意接受一定的供应商依赖

### 4.3 方案C：OpenClaw等框架二次开发

**实现思路**：基于LangChain/LangGraph或MetaGPT等开源框架进行二次开发 [6][11]。

**优点：**
- 开源生态丰富，可复用大量现成组件
- 支持多种LLM后端（OpenAI、Claude、本地模型）
- LangGraph提供成熟的状态机与图编排能力
- 社区贡献的Tools和Agents可直接使用

**缺点：**
- 框架更新频繁，需要持续跟进版本变化
- 抽象层较重，性能可能不如自建方案
- 深度定制时可能需要修改框架源码
- 学习曲线较陡，团队需要时间熟悉

**开发周期**：2-3个月达到MVP

**适用场景**：追求技术中立，希望保持灵活的模型切换能力

### 4.4 技术选型对比总结

|评估维度|方案A：自建|方案B：Claude SDK|方案C：开源框架|
|:---|:---|:---|:---|
|开发周期|3-4个月|2-3个月|2-3个月|
|可控性|★★★★★|★★★|★★★★|
|生态丰富度|★★|★★★★|★★★★★|
|维护成本|中|低|中|
|供应商风险|无|高|低|
|定制灵活性|★★★★★|★★★|★★★★|
|团队学习成本|低|中|高|

### 4.5 推荐方案

**推荐采用"方案A+方案C"的混合策略**：

1. **核心框架自建**：参考autoresearch的极简设计，自主实现Ratchet Loop、Hibernate-and-Wake等核心机制，确保对太极平台的深度集成
2. **组件借鉴开源**：Memory系统采用Mem0的设计模式，Tools集成参考LangChain的@tool规范，降低重复造轮子的成本
3. **模型接口抽象**：设计统一的LLM接口层，支持快速切换不同的模型后端（Claude、GPT-4、混元等）

**推荐理由**：
- 结合了自建方案的可控性与开源方案的成熟组件
- 符合团队"快速迭代"的要求，MVP可在2-3个月内交付
- 长期演进路径清晰，不存在供应商锁定风险

## 5. Leader-Worker系统架构设计

### 5.1 整体架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          广告推荐模型调研Agent系统                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        Leader Agent (Planner)                        │   │
│  │  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐        │   │
│  │  │ 假设生成  │  │ 任务分配  │  │ 结果汇总  │  │ 策略进化  │        │   │
│  │  └───────────┘  └───────────┘  └───────────┘  └───────────┘        │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                    ┌───────────────┼───────────────┐                       │
│                    ▼               ▼               ▼                       │
│  ┌─────────────────────┐ ┌─────────────────────┐ ┌─────────────────────┐  │
│  │   Worker Agent #1   │ │   Worker Agent #2   │ │   Worker Agent #N   │  │
│  │  (特征工程探索)      │ │  (超参调优)         │ │  (模型结构优化)     │  │
│  └─────────────────────┘ └─────────────────────┘ └─────────────────────┘  │
│                    │               │               │                       │
│                    └───────────────┼───────────────┘                       │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         太极训练平台                                  │   │
│  │  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐        │   │
│  │  │ 训练任务  │  │ 特征服务  │  │ 模型仓库  │  │ 指标服务  │        │   │
│  │  │ 调度API   │  │ API       │  │ API       │  │ API       │        │   │
│  │  └───────────┘  └───────────┘  └───────────┘  └───────────┘        │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         知识与经验系统                                │   │
│  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐           │   │
│  │  │ 历史实验库    │  │ 论文知识库    │  │ 成功/失败模式库│           │   │
│  │  │ (PostgreSQL)  │  │ (向量数据库)  │  │ (图数据库)    │           │   │
│  │  └───────────────┘  └───────────────┘  └───────────────┘           │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Leader Agent设计

Leader Agent是整个系统的"大脑"，负责高层决策与任务协调，参考Meta REA的Planner设计 [1]。

**核心职责：**

|职责|描述|触发条件|
|:---|:---|:---|
|假设生成|融合历史经验与论文知识，生成优化假设|周期性（每日）或触发式（Worker完成实验）|
|任务分配|将假设分解为具体任务，分配给Worker|生成新假设后|
|结果汇总|收集Worker实验结果，进行综合分析|Worker完成实验后|
|策略进化|基于实验结果更新假设生成策略|累积足够实验数据后|

**假设生成流程：**

```python
class LeaderAgent:
    def generate_hypotheses(self) -> list[Hypothesis]:
        # 1. 获取当前模型状态
        current_state = self.get_model_state()
        
        # 2. 检索相关历史经验
        similar_experiments = self.memory.search_similar(current_state)
        success_patterns = self.memory.get_success_patterns()
        
        # 3. 检索最新论文知识
        relevant_papers = self.paper_agent.search_recent(current_state.task_type)
        
        # 4. 调用LLM生成假设
        hypotheses = self.llm.generate(
            prompt=self.hypothesis_prompt,
            context={
                "current_state": current_state,
                "similar_experiments": similar_experiments,
                "success_patterns": success_patterns,
                "relevant_papers": relevant_papers,
            }
        )
        
        # 5. 假设排序与筛选
        ranked_hypotheses = self.rank_hypotheses(hypotheses)
        return ranked_hypotheses[:self.max_parallel_workers]
```

### 5.3 Worker Agent设计

Worker Agent是执行层，负责具体实验的执行与监控。支持多种专业化类型：

|Worker类型|专注领域|典型任务|
|:---|:---|:---|
|FeatureWorker|特征工程|特征筛选、特征交叉、特征萃取|
|HyperparamWorker|超参调优|学习率、优化器、Epoch、Batch Size|
|ArchitectureWorker|模型结构|网络层数、注意力机制、多任务权重|
|SampleWorker|样本优化|采样策略、负样本构造、类别平衡|

**Worker执行流程：**

```python
class WorkerAgent:
    def execute_experiment(self, task: ExperimentTask) -> ExperimentResult:
        # 1. 解析任务，生成配置
        config = self.parse_task(task)
        
        # 2. 配置校验
        validation_result = self.validate_config(config)
        if not validation_result.is_valid:
            return ExperimentResult(status="failed", reason=validation_result.reason)
        
        # 3. 提交训练任务
        task_id = self.taiji_client.submit_task(config)
        
        # 4. 进入休眠状态（Hibernate）
        self.hibernate(task_id)
        
        # 5. 任务完成后被唤醒（Wake）
        # ... 由Runtime自动调用
    
    def on_task_complete(self, task_id: str):
        # 6. 收集实验结果
        metrics = self.taiji_client.get_metrics(task_id)
        
        # 7. 分析与总结
        analysis = self.analyze_result(metrics)
        
        # 8. 上报Leader
        self.report_to_leader(analysis)
```

### 5.4 与太极平台集成

将太极平台的核心能力封装为MCP Server，实现与Agent系统的标准化集成。

**需要封装的接口：**

|接口类别|接口名称|功能描述|
|:---|:---|:---|
|训练管理|submit_task|提交训练任务|
|训练管理|cancel_task|取消训练任务|
|训练管理|get_task_status|查询任务状态|
|特征服务|list_features|获取可用特征列表|
|特征服务|get_feature_stats|获取特征统计信息|
|指标服务|get_training_metrics|获取训练过程指标|
|指标服务|get_eval_metrics|获取评估指标（AUC、CTR等）|
|模型服务|deploy_model|部署模型到测试环境|

**改造要点：**
- 现有太极平台主要面向人工操作，需要开发RESTful API层
- 新增异步任务状态查询接口，支持Webhook回调
- 开发指标订阅机制，支持训练过程中的实时指标推送

### 5.5 知识库与经验沉淀设计

**历史实验数据库（PostgreSQL）：**

```sql
CREATE TABLE experiments (
    id SERIAL PRIMARY KEY,
    experiment_name VARCHAR(255),
    hypothesis_id VARCHAR(64),
    config JSONB,
    metrics JSONB,
    status VARCHAR(32),
    created_at TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE TABLE experiment_analysis (
    id SERIAL PRIMARY KEY,
    experiment_id INT REFERENCES experiments(id),
    analysis_text TEXT,
    success_factors JSONB,
    failure_factors JSONB,
    lessons_learned TEXT
);
```

**论文知识库（向量数据库）：**
- 定期抓取arXiv、ACL、NeurIPS等顶会的推荐系统相关论文
- 使用Embedding模型将论文摘要、方法章节向量化
- 支持语义检索：如"注意力机制在CTR预估中的应用"

**成功/失败模式库（图数据库）：**
- 节点：特征、模型组件、超参数、实验结果
- 边：因果关系、相关性
- 支持图查询：如"哪些特征组合在相似场景下取得过成功"

## 6. 算法工程师与Agent协作模式

### 6.1 角色定义与边界

|角色|定位|核心职责|
|:---|:---|:---|
|算法工程师|战略制定者、质量把关者|定义优化目标、审核关键决策、处理复杂异常|
|Leader Agent|任务规划者、经验整合者|生成假设、分配任务、汇总结果、更新策略|
|Worker Agent|执行者、监控者|执行实验、监控指标、上报结果|

### 6.2 人机交互流程

```
┌─────────────────────────────────────────────────────────────────────┐
│                         日常迭代流程                                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  算法工程师                    Agent系统                            │
│      │                            │                                 │
│      │  1. 定义优化目标           │                                 │
│      │  (如：CTR提升0.5%)         │                                 │
│      │ ─────────────────────────▶ │                                 │
│      │                            │                                 │
│      │                            │  2. 生成假设列表                │
│      │  3. 审核关键假设           │                                 │
│      │ ◀───────────────────────── │                                 │
│      │                            │                                 │
│      │  4. 批准/修改/拒绝         │                                 │
│      │ ─────────────────────────▶ │                                 │
│      │                            │                                 │
│      │                            │  5. 并行执行实验                │
│      │                            │     (7×24自动运行)              │
│      │                            │                                 │
│      │  6. 定期查看进展报告       │                                 │
│      │ ◀───────────────────────── │                                 │
│      │                            │                                 │
│      │  7. 审核最终结果           │                                 │
│      │  8. 决策是否上线           │                                 │
│      │                            │                                 │
└─────────────────────────────────────────────────────────────────────┘
```

### 6.3 任务分工清单

|任务类别|Agent能做|工程师需做|
|:---|:---|:---|
|**假设生成**|基于历史和论文生成候选假设|审核假设质量，过滤高风险方案|
|**实验设计**|自动生成实验配置和对比组|定义实验的业务约束和红线|
|**代码编写**|生成特征工程、超参配置代码|审核代码质量，处理复杂逻辑|
|**实验执行**|7×24自动提交、监控、记录|处理平台异常和资源申请|
|**结果分析**|自动统计指标、生成分析报告|深度解读结果，判断业务价值|
|**经验沉淀**|自动提取成功/失败模式|验证经验的可迁移性|
|**论文跟踪**|自动检索、摘要相关论文|判断论文方法的适用性|
|**模型上线**|提供上线建议和风险评估|最终决策和线上验证|

### 6.4 Human-in-the-Loop机制

参考业界企业级Agent部署的最佳实践，对以下关键节点设置人工审核 [25]：

- **高资源消耗实验**：预估GPU时长超过48小时的实验需要人工审批
- **涉及核心特征变更**：修改Top 50重要特征的实验需要人工审核
- **异常结果处理**：指标波动超过阈值时触发告警，等待人工确认
- **模型上线决策**：所有上线决策必须由算法工程师最终确认

## 7. 详细实施路线图

### 7.1 Phase 1：MVP验证阶段（第1-2个月）

**目标**：验证核心技术可行性，完成最小可用系统

**里程碑：**

|周次|里程碑|交付物|
|:---|:---|:---|
|Week 1-2|技术选型与环境搭建|技术方案文档、开发环境|
|Week 3-4|核心Agent框架开发|Leader/Worker Agent原型|
|Week 5-6|太极平台对接|MCP Server接口封装|
|Week 7-8|端到端流程验证|完整的单次实验闭环|

**详细任务：**

**Week 1-2：技术选型与环境搭建**
- 确定LLM模型（建议先使用Claude API，后续迁移至内部模型）
- 搭建开发环境，配置代码仓库、CI/CD流程
- 设计数据库Schema，部署PostgreSQL、向量数据库

**Week 3-4：核心Agent框架开发**
- 实现Leader Agent的假设生成模块
- 实现Worker Agent的任务执行模块
- 开发Hibernate-and-Wake机制的原型

**Week 5-6：太极平台对接**
- 与太极平台团队对接，明确API需求
- 开发MCP Server，封装训练任务管理接口
- 实现指标查询与异步回调机制

**Week 7-8：端到端流程验证**
- 选择一个简单的优化场景（如学习率调优）进行验证
- 跑通"假设生成→任务提交→结果收集→经验存储"完整流程
- 修复关键Bug，输出MVP演示

**验收标准：**
- 能够自动生成至少3个合理的优化假设
- 能够自动提交训练任务并收集结果
- 完整流程运行一次的成功率达到80%

**资源需求：**
- 2名全职工程师
- 1名兼职算法专家（提供业务指导）
- Claude API费用预算：约$1000/月

### 7.2 Phase 2：能力扩展阶段（第3-4个月）

**目标**：扩展Agent能力，支持多Worker并行，引入经验学习机制

**里程碑：**

|周次|里程碑|交付物|
|:---|:---|:---|
|Week 9-10|多Worker并行支持|并行实验调度器|
|Week 11-12|Memory系统完善|三层记忆架构|
|Week 13-14|论文知识库集成|论文检索与摘要系统|
|Week 15-16|Dashboard与告警|运营监控平台|

**详细任务：**

**Week 9-10：多Worker并行支持**
- 实现Worker Agent的多实例管理
- 开发任务队列与优先级调度机制
- 实现资源隔离与负载均衡

**Week 11-12：Memory系统完善**
- 部署Neo4j图数据库，建立知识图谱Schema
- 实现成功/失败模式的自动提取与存储
- 开发基于图的相似实验检索功能

**Week 13-14：论文知识库集成**
- 开发arXiv论文抓取与解析模块
- 实现论文Embedding与向量检索
- 集成论文知识到假设生成流程

**Week 15-16：Dashboard与告警**
- 开发Web Dashboard，展示实验进展与指标
- 实现异常告警（企业微信/邮件）
- 开发实验报告自动生成功能

**验收标准：**
- 支持同时运行3个以上Worker Agent
- 假设生成质量提升，有效假设比例达到30%
- Dashboard能够实时展示所有实验状态

**资源需求：**
- 3名全职工程师
- 1名前端工程师（Dashboard开发）
- GPU资源：约100卡/天

### 7.3 Phase 3：生产级部署阶段（第5-6个月）

**目标**：完善系统稳定性，扩大应用范围，形成可复制的最佳实践

**里程碑：**

|周次|里程碑|交付物|
|:---|:---|:---|
|Week 17-18|稳定性增强|高可用部署方案|
|Week 19-20|场景扩展|覆盖3个以上业务场景|
|Week 21-22|效果评估与调优|效果评估报告|
|Week 23-24|文档与培训|用户手册、培训材料|

**详细任务：**

**Week 17-18：稳定性增强**
- 实现Agent的故障自动恢复机制
- 部署高可用架构（多副本、自动扩缩容）
- 完善日志与监控体系

**Week 19-20：场景扩展**
- 将系统应用到CTR、CVR、多任务建模等多个场景
- 针对不同场景定制Skill和Prompt
- 收集各场景的反馈并优化

**Week 21-22：效果评估与调优**
- 统计系统上线以来的实验数据
- 计算效率提升指标（对比人工迭代）
- 根据数据反馈优化假设生成策略

**Week 23-24：文档与培训**
- 编写用户操作手册
- 编写系统维护手册
- 组织团队培训，推广最佳实践

**验收标准：**
- 系统稳定运行率达到99%
- 至少在3个业务场景中取得正向效果
- 实验效率提升达到3倍以上
- 团队成员能够独立使用系统

**资源需求：**
- 2名全职工程师（维护与优化）
- 1名SRE工程师（稳定性保障）
- GPU资源：约200卡/天

## 8. 风险分析与应对策略

### 8.1 技术风险

|风险项|风险等级|影响描述|应对策略|
|:---|:---|:---|:---|
|LLM幻觉|高|生成不合理的假设或错误的代码|引入多重校验机制，关键假设需人工审核|
|长程任务失败|中|训练任务因资源或配置问题中断|实现Checkpoint机制，支持断点续跑|
|API稳定性|中|外部LLM API不稳定导致系统异常|设计降级策略，关键路径支持多模型切换|
|向量检索质量|中|检索到的历史经验相关性不高|持续优化Embedding模型，引入反馈机制|

### 8.2 业务风险

|风险项|风险等级|影响描述|应对策略|
|:---|:---|:---|:---|
|实验成本超支|高|大量实验消耗过多GPU资源|设置资源配额，高消耗实验需审批|
|线上影响|高|错误配置导致线上模型效果下降|严格区分测试环境与生产环境，上线前必须人工审核|
|数据安全|中|实验数据泄露|数据脱敏处理，API调用审计|

### 8.3 组织风险

|风险项|风险等级|影响描述|应对策略|
|:---|:---|:---|:---|
|人员接受度低|中|工程师担心被取代，抵触使用|明确定位为"工具"而非"替代"，强调解放人力做更有价值的事|
|技能转型|低|团队需要学习新的Agent开发技能|组织培训，引入外部专家支持|
|跨团队协作|中|与太极平台团队协作不顺畅|前期充分沟通，建立定期同步机制|

## 9. 预期收益与ROI分析

### 9.1 效率提升预期

参考Meta REA的生产数据（3人完成16人工作量），结合腾讯广告的实际情况，预期收益如下 [1][4]：

|指标|当前状态|6个月目标|12个月目标|
|:---|:---|:---|:---|
|每周实验数量|5-10次/人|15-30次/人|50次/人|
|有效实验比例|20%|30%|40%|
|从假设到验证周期|1-2周|3-5天|1-2天|
|工程师可支持的并行方向|1-2个|3-5个|5-10个|

### 9.2 人力成本节省

假设当前团队有10名算法工程师，每人年均成本100万元：

|场景|当前状态|Agent辅助后|年节省|
|:---|:---|:---|:---|
|常规调参工作|占用50%时间|占用20%时间|300万元（可投入创新工作）|
|实验监控|占用20%时间|占用5%时间|150万元|
|论文调研|占用15%时间|占用10%时间|50万元|

### 9.3 模型指标提升预期

基于更高的实验吞吐量和更智能的假设生成，预期模型指标有望实现：

|指标|保守预期|乐观预期|业务影响|
|:---|:---|:---|:---|
|AUC提升|+0.2%|+0.5%|广告收入提升约1-2%|
|CTR提升|+0.3%|+0.8%|用户体验与收入双提升|
|CVR提升|+0.2%|+0.4%|广告主ROI提升|

### 9.4 ROI计算

|投入项|金额（6个月）|
|:---|:---|
|研发人力（5人×6个月）|300万元|
|LLM API费用|6万元|
|GPU资源|约30万元|
|**总投入**|**336万元**|

|收益项|金额（年化）|
|:---|:---|
|人力成本节省|500万元|
|广告收入提升（假设+1%）|数亿元级|
|**ROI**|**>10x**|

## 10. 参考文献

[1] Meta Engineering, 2026-03-17. Ranking Engineer Agent (REA): The Autonomous AI Agent Accelerating Meta's Ads Ranking Innovation. https://engineering.fb.com/2026/03/17/developer-tools/ranking-engineer-agent-rea-autonomous-ai-system-accelerating-meta-ads-ranking-innovation

[2] Daily.dev, 2026-03-17. Ranking Engineer Agent (REA): The Autonomous AI Agent... https://app.daily.dev/posts/ranking-engineer-agent-rea-the-autonomous-ai-agent-accelerating-meta-s-ads-ranking-innovation-cbu80xva0

[3] Haochen Wang, et al. (Google), 2026-02-12. Self-Evolving Recommendation System: End-To-End Autonomous Model Optimization With LLM Agents. https://arxiv.org/abs/2602.10226

[4] LinkedIn, 2026-03-20. Meta's AI Agent Boosts Ads Ranking Accuracy 2x, Efficiency 5x. https://linkedin.com/posts/madananuj_ranking-engineer-agent-rea-the-autonomous-activity-7440875693854527488-iEOE

[6] OneUptime, 2026-02-02. How to Build Agents with LangChain. https://oneuptime.com/blog/post/2026-02-02-langchain-agents/view

[7] Andrej Karpathy, 2026-03-15. autoresearch: AI agents running research on LLM training. https://github.com/karpathy/autoresearch

[8] DataCamp, 2026-03-23. A Guide to Andrej Karpathy's AutoResearch: Automating ML with AI. https://datacamp.com/tutorial/guide-to-autoresearch

[10] arXiv, 2026-02-10. End-To-End Autonomous Model Optimization With LLM Agents. https://arxiv.org/pdf/2602.10226

[11] Skywork.ai, 2025-10-05. MetaGPT X (MGX) Deep Dive. https://skywork.ai/skypage/en/MetaGPT-X-(MGX)-Deep-Dive-Why-It's-the-Most-Lovable-AI-Co-developer-You'll-Meet-in-2025/1972580997544996864

[13] Salesforce, 2026-01-09. What Are ReAct Agents?. https://salesforce.com/agentforce/ai-agents/react-agents

[15] Promptingguide.ai, 2026-02-01. ReAct - Prompt Engineering Guide. https://promptingguide.ai/techniques/react

[16] AWS, 2025-03-25. Amazon Bedrock launches Session Management APIs. https://aws.amazon.com/blogs/machine-learning/amazon-bedrock-launches-session-management-apis-for-generative-ai-applications-preview

[17] Google, 2025-04-09. Introduction to Conversational Context: Session, State, and Memory. https://google.github.io/adk-docs/sessions

[19] Mem0, 2025-12-31. AI Memory Layer Guide December 2025. https://mem0.ai/blog/ai-memory-layer-guide

[20] Deepsense.ai, 2026-03-16. Building Production AI Agents: Hooks and Multi-agent Orchestration. https://deepsense.ai/blog/building-production-ai-agents-hooks-tool-confirmation-and-multi-agent-orchestration-in-ragbits-1-5-release

[21] Yuv.ai, 2026-02-12. Claude Code Hooks Mastery. https://yuv.ai/blog/claude-code-hooks-mastery

[22] LangChain Docs, 2026-02-04. Tools - Best Practices. https://docs.langchain.com/oss/python/langchain/tools

[23] arXiv, 2025-06-30. Efficient Scheduling of Agentic LLM Workloads (Agent.xpu). https://arxiv.org/html/2506.24045v1

[24] Spring.io, 2026-01-13. Spring AI Agentic Patterns: Agent Skills. https://spring.io/blog/2026/01/13/spring-ai-agentic-patterns-part-1-agent-skills

[25] WorkOS, 2025-07-23. Enterprise AI Agent Playbook. https://workos.com/blog/enterprise-ai-agent-playbook-what-anthropic-and-openai-reveal-about-building-production-ready-systems

[26] Restate.dev, 2025-06-19. Durable AI Loops: Fault Tolerance across Frameworks. https://restate.dev/blog/durable-ai-loops-fault-tolerance-across-frameworks-and-without-handcuffs

[27] Model Context Protocol, 2025-11-25. MCP Specification Version 2025-11-25. https://modelcontextprotocol.io/specification/2025-11-25

[28] Black Hills Information Security, 2025-10-22. Model Context Protocol (MCP) Overview. https://blackhillsinfosec.com/model-context-protocol

[30] ACL Anthology, 2025-11. MemoryOS: A Memory Operating System for AI Agents. https://aclanthology.org/2025.emnlp-main.1318/