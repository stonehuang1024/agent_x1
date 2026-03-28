# RankMixer 论文解析笔记

## 基本信息

- **论文标题**: RankMixer: Scaling Up Ranking Models in Industrial Recommenders
- **作者**: Jie Zhu, Zhifang Fan, Xiaoxie Zhu, et al. (ByteDance)
- **arXiv ID**: 2507.15551
- **发表时间**: 2025年7月
- **机构**: ByteDance (抖音推荐团队)

## 研究背景

### 问题定义

推荐系统(RS)是信息分发的核心基础设施。现代推荐方法基于深度学习推荐模型(DLRMs)，通过神经网络灵活捕捉特征交互。

### 现有挑战

1. **计算效率问题**: 
   - CPU时代设计的特征交叉模块在现代GPU上效率低下
   - 模型FLOPs利用率(MFU)通常只有个位数百分比
   - 内存带宽受限而非计算受限

2. **扩展性瓶颈**:
   - 传统模型参数增长与计算成本成正比
   - 难以实现LLM那样的scaling law
   - 严格的延迟约束和高QPS需求

3. **特征异构性**:
   - 推荐数据包含数百个字段的异构特征空间
   - 用户/物品ID空间可能有数亿元素
   - 不同语义空间的特征直接计算内积不合理

## 核心贡献

### 1. Multi-head Token Mixing

**核心思想**: 用无参数的token shuffling替代self-attention

**实现方式**:
```
输入: T个token, 每个维度D
1. 将每个token分成H个头: (T, D) -> (T, H, D/H)
2. 重排: 将相同head位置的向量组合成新token
3. 输出: H个token, 每个包含T*D/H维度
```

**优势**:
- 无参数，仅内存操作
- 避免self-attention的O(T²)复杂度
- 支持跨token的特征交互

**与self-attention对比**:
- Self-attention: 计算token间相似度，在异构空间上效果差
- Token Mixing: 直接shuffle，不依赖相似度计算

### 2. Per-token FFN (PFFN)

**核心思想**: 每个token有独立的FFN参数

**公式**:
```
v_t = f_t,2(GELU(f_t,1(s_t)))

其中:
- f_t,1: R^D -> R^(kD)
- f_t,2: R^(kD) -> R^D
- 每个token t有独立的参数
```

**优势**:
- 隔离不同特征子空间的参数
- 避免高频特征主导低频特征
- 保持计算复杂度不变的同时增加参数量

**与MMoE的区别**:
- MMoE: 所有专家处理相同输入
- PFFN: 每个FFN处理不同token输入

### 3. Sparse MoE 扩展

#### ReLU Routing

**问题**: 传统Top-k路由对所有token一视同仁

**解决方案**:
```
G_i,j = ReLU(h(s_i))  # 替代softmax

Loss = L_task + λ * Σ G_i,j  # L1正则化控制稀疏性
```

**优势**:
- 高信息token激活更多专家
- 低信息token激活更少专家
- 可微分，端到端训练

#### DTSI-MoE (Dense-training/Sparse-inference)

**核心思想**:
- 训练时使用两个router: h_train 和 h_infer
- 两个router都更新
- 推理时只使用h_infer

**优势**:
- 避免专家欠训练
- 推理时保持稀疏性

### 4. 硬件感知设计

**设计原则**:
1. 最大化GPU并行性
2. 减少内存带宽瓶颈
3. 计算密集型而非内存密集型

**效果**:
- MFU从4.5%提升到45%
- 参数扩展100倍，推理延迟基本不变

## 实验结果

### 离线实验 (生产数据集)

| Model | Finish AUC Δ | Skip AUC Δ | Params | FLOPs/Batch |
|-------|-------------|-----------|--------|-------------|
| DLRM-MLP | baseline | baseline | 8.7M | 52G |
| DLRM-MLP-100M | +0.15% | +0.15% | 95M | 185G |
| DCNv2 | +0.13% | +0.15% | 22M | 170G |
| DHEN | +0.18% | +0.36% | 22M | 158G |
| Wukong | +0.29% | +0.49% | 122M | 442G |
| **RankMixer-100M** | **+0.64%** | **+0.86%** | **107M** | **233G** |
| **RankMixer-1B** | **+0.95%** | **+1.25%** | **1.1B** | **2.1T** |

**关键发现**:
1. RankMixer-100M显著优于所有baseline
2. 相同参数量下，RankMixer计算效率更高(FLOPs更低)
3. 扩展到1B参数仍有显著提升

### 扩展性分析

**Scaling Law**:
- 模型质量与总参数量呈幂律关系
- 不同扩展维度(T, D, L)效果相近
- 从计算效率角度，增大D比增加L更好(更大的矩阵乘法)

**推荐配置**:
- 100M: (D=768, T=16, L=2)
- 1B: (D=1536, T=32, L=2)

### Ablation Study

| Setting | ΔAUC |
|---------|------|
| w/o skip connections | -0.07% |
| w/o multi-head token mixing | -0.50% |
| w/o layer normalization | -0.05% |
| Per-token FFN → shared FFN | -0.31% |

**结论**: Multi-head Token Mixing最重要(-0.50%)

### 路由策略对比

| Routing strategy | ΔAUC | ΔParams | ΔFLOPs |
|-----------------|------|---------|--------|
| All-Concat-MLP | -0.18% | 0% | 0% |
| All-Share | -0.25% | 0% | 0% |
| Self-Attention | -0.03% | +16% | +71.8% |
| **Multi-Head Token-Mixing** | **baseline** | **0%** | **0%** |

### 在线A/B测试

**部署**: 抖音Feed推荐排序全流量

**结果**:
- 用户活跃天数: +0.3%
- App使用时长: +1.08%
- 推理成本: 无增加

## 技术细节

### 输入层与Tokenization

**特征类型**:
1. 用户特征: 用户ID、用户信息
2. 候选特征: 视频ID、作者ID
3. 序列特征: 通过序列模块处理
4. 交叉特征: 用户-候选交互

**Tokenization策略**:
```
1. 按语义聚类特征
2. 连接同一类别的embedding
3. 投影到固定维度的token

x_i = Proj(e_input[d*(i-1):d*i]), i=1,...,T
```

### 模型维度公式

**Dense版本**:
```
#Params ≈ 2 * k * L * T * D²
FLOPs ≈ 4 * k * L * T * D²
```

**Sparse MoE版本**:
```
有效参数 = 总参数 * sparsity_ratio
```

### 训练细节

**优化器**:
- Dense部分: RMSProp, lr=0.01
- Sparse部分: Adagrad

**分布式训练**:
- Sparse部分: 异步更新
- Dense部分: 同步更新

**超参数**:
- Batch size: 512
- 训练数据: 每天万亿级记录
- 实验周期: 两周数据

## 与其他工作的关系

### 特征交互模型演进

1. **Wide&Deep** (2016): 结合LR和DNN
2. **DeepFM** (2017): FM + DNN
3. **DCN/DCNv2** (2017/2020): 显式高阶交叉
4. **AutoInt** (2019): Self-attention用于特征交互
5. **DHEN** (2023): 组合多种交互算子
6. **Wukong** (2024): 扩展DHEN的scaling研究
7. **RankMixer** (2025): 本文工作

### Scaling Law研究

**NLP/CV领域**:
- GPT-3, PaLM, Chinchilla等
- 模型性能与参数量、数据量、计算量呈幂律关系

**推荐系统领域**:
- HSTU (2024): 序列模型的scaling
- Wukong (2024): 特征交互的scaling
- **RankMixer (2025)**: 统一架构的scaling

## 关键洞察

### 1. 为什么self-attention在推荐中效果不佳？

**NLP场景**:
- 所有token共享统一embedding空间
- 内积相似度有意义

**推荐场景**:
- 特征空间异构(用户ID vs 物品属性)
- ID空间可能有数亿元素
- 不同语义空间的内积难以解释

### 2. 为什么Per-token FFN有效？

**传统共享FFN**:
- 所有token共享相同变换
- 高频特征可能主导
- 长尾信号被淹没

**Per-token FFN**:
- 每个token有独立参数
- 隔离不同特征子空间
- 更好建模多样性

### 3. 硬件感知设计的重要性

**CPU时代设计**:
- 内存带宽充足
- 计算是瓶颈
- 复杂算子可行

**GPU时代设计**:
- 计算能力远超内存带宽
- 需要高并行度
- 简单、规则的计算模式更高效

## 复现要点

### 核心组件

1. **MultiHeadTokenMixing**
   - 输入: (B, T, D)
   - 输出: (B, H, T*D/H)
   - 关键: H=T用于residual connection

2. **PerTokenFFN**
   - 每个token独立参数
   - GELU激活
   - Residual + LayerNorm

3. **SparseMoEPerTokenFFN**
   - ReLU routing
   - L1正则化
   - 双router (train/infer)

### 实现注意事项

1. **维度对齐**:
   - Token mixing后需要投影回(B, T, D)
   - 确保residual connection维度匹配

2. **数值稳定性**:
   - LayerNorm位置
   - Dropout应用
   - 梯度裁剪

3. **效率优化**:
   - 避免Python循环
   - 使用向量化操作
   - 考虑CUDA kernel优化

## 开放问题

1. **Tokenization策略**: 如何最优地将特征分组到token？
2. **Expert数量**: 如何确定每个token的最佳专家数？
3. **跨模态扩展**: 能否扩展到多模态推荐？
4. **与其他技术的结合**: 如何与序列模型、图神经网络结合？

## 总结

RankMixer通过以下创新实现了推荐模型的有效扩展:

1. **架构创新**: Multi-head Token Mixing + Per-token FFN
2. **效率优化**: 硬件感知设计，高MFU
3. **扩展机制**: Sparse MoE，参数与计算解耦
4. **工业验证**: 大规模生产环境部署，显著业务提升

这项工作为推荐系统的模型scaling提供了重要的实践经验和理论指导。
