# DCN V2 论文分析笔记

## 基本信息
- **论文标题**: DCN V2: Improved Deep & Cross Network and Practical Lessons for Web-scale Learning to Rank Systems
- **作者**: Ruoxi Wang, Rakesh Shivanna, Derek Z. Cheng, Sagar Jain, Dong Lin, Lichan Hong, Ed H. Chi
- **机构**: Google Inc.
- **arXiv ID**: 2008.13535
- **发表**: WWW 2021

## 问题背景

### 核心问题
学习有效的特征交叉(feature crosses)是构建推荐系统的关键。然而：
1. 稀疏且庞大的特征空间需要穷举搜索来识别有效的交叉
2. 传统DNN学习效率低下，即使近似建模2阶或3阶特征交叉也很困难
3. 增加模型容量会导致服务延迟增加，难以满足实时推理需求

### DCN (V1) 的局限性
1. **表达能力有限**: 交叉网络只能 reproducing 由 O(input size) 参数表征的多项式类别
2. **容量分配不平衡**: 在大规模生产数据中，DNN部分占用绝大部分参数来学习隐式交叉

## DCN-V2 核心创新

### 1. 改进的交叉层 (Cross Layer)
**核心公式**:
```
x_{l+1} = x0 ⊙ (W_l * x_l + b_l) + x_l
```

其中：
- x0 ∈ R^d: 基础层（通常是embedding层）
- x_l, x_{l+1} ∈ R^d: 第l层的输入和输出
- W_l ∈ R^(d×d): 权重矩阵
- b_l ∈ R^d: 偏置向量
- ⊙: Hadamard积（逐元素乘积）

**与DCN (V1)的关系**:
当 W = 1 × w^T 时，DCN-V2退化为DCN。因此DCN-V2的函数类是DCN的严格超集。

### 2. 两种架构组合方式

#### Stacked Structure (堆叠结构)
```
x_final = h_Ld, where h_0 = x_Lc
```
输入先经过Cross Network，再经过Deep Network。

#### Parallel Structure (并行结构)
```
x_final = [x_Lc; h_Ld]
```
输入同时输入Cross Network和Deep Network，输出拼接。

### 3. 低秩Mixture-of-Experts (MoE) 优化

观察发现：学习到的权重矩阵W具有低秩特性。

**低秩交叉层**:
```
x_{l+1} = x0 ⊙ (U_l * V_l^T * x_l + b_l) + x_l
```
其中 U_l, V_l ∈ R^(d×r)，r << d

**Mixture of Low-Rank Experts**:
使用多个低秩专家，通过门控机制聚合：
```
Output = Σ G_i(x) * E_i(x)
```

## 实验结果

### 数据集
1. **Criteo Display Ads Dataset**: CTR预测任务
2. **MovieLens-1M**: 推荐任务

### 性能对比 (论文报告)

#### Criteo 数据集
| Model | LogLoss | AUC |
|-------|---------|-----|
| DCN-V2 (Parallel) | **0.4421** | **0.8105** |
| DCN-V2 (Stacked) | 0.4422 | 0.8104 |
| DCN | 0.4426 | 0.8101 |
| DeepFM | 0.4427 | 0.8100 |
| xDeepFM | 0.4429 | 0.8098 |
| AutoInt | 0.4427 | 0.8100 |
| DNN | 0.4428 | 0.8099 |

#### MovieLens-1M 数据集
| Model | LogLoss | AUC |
|-------|---------|-----|
| DCN-V2 (Parallel) | **0.4366** | **0.8452** |
| DCN | 0.4381 | 0.8435 |
| DeepFM | 0.4393 | 0.8423 |

### 关键发现
1. DCN-V2 在两个数据集上都优于所有baseline
2. Stacked结构在Criteo上表现更好，Parallel在MovieLens上更好
3. 低秩近似(r=4~64)可以达到接近全秩的性能

## 实现细节

### 超参数设置 (Criteo)
- Embedding dimension: 16
- Cross layers: 2-4
- Deep layers: [256, 128] 或 [512, 256, 128]
- Learning rate: 1e-4 to 1e-3
- Batch size: 512 or 1024
- Training steps: 150k
- L2 regularization: 1e-5 to 1e-6

### 特征处理
- Categorical features: 使用embedding layer
- Dense features: 归一化
- 支持不同大小的embedding（DCN-V2优势）

## 生产环境经验
1. DCN-V2在Google多个LTR系统中部署
2. 相比同等大小的ReLU网络，AUCLoss改善0.6%
3. 低秩MoE版本在保持性能的同时降低延迟

## 待复现内容
1. ✅ DCN-V2 Cross Network (Stacked & Parallel)
2. ✅ Baseline: DCN (V1)
3. ✅ Baseline: DeepFM
4. ✅ Baseline: DNN
5. ✅ 低秩DCN-V2 (可选)
6. ✅ Criteo数据集实验
7. ⬜ MovieLens-1M数据集实验 (可选)

## 关键公式总结

### 交叉层
```python
x_l_plus_1 = x0 * (W @ x_l + b) + x_l  # * 表示逐元素乘
```

### 预测
```python
y_pred = sigmoid(w_logit^T @ x_final)
loss = binary_cross_entropy(y_pred, y_true) + L2_reg
```
