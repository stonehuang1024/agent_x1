# DCN-V2 论文复现

本仓库包含 **DCN V2: Improved Deep & Cross Network and Practical Lessons for Web-scale Learning to Rank Systems** (WWW 2021) 的完整复现。

## 📄 论文信息

- **标题**: DCN V2: Improved Deep & Cross Network and Practical Lessons for Web-scale Learning to Rank Systems
- **作者**: Ruoxi Wang, et al. (Google)
- **arXiv**: [2008.13535](https://arxiv.org/abs/2008.13535)
- **会议**: WWW 2021

## 🚀 快速开始

### 环境安装

```bash
pip install -r requirements.txt
```

### 数据准备

```bash
python scripts/download_criteo.py
```

### 训练模型

```bash
# 训练 DCN-V2 (Stacked)
PYTHONPATH=$PWD python src/train.py --model dcnv2_stacked --epochs 20

# 训练 DCN-V2 (Parallel)
PYTHONPATH=$PWD python src/train.py --model dcnv2_parallel --epochs 20

# 训练其他基线模型
PYTHONPATH=$PWD python src/train.py --model dcn --epochs 20
PYTHONPATH=$PWD python src/train.py --model deepfm --epochs 20
PYTHONPATH=$PWD python src/train.py --model dnn --epochs 20
PYTHONPATH=$PWD python src/train.py --model lr --epochs 20
```

### 评估对比

```bash
PYTHONPATH=$PWD python src/evaluate.py --embedding_dim 8
```

### 运行测试

```bash
PYTHONPATH=$PWD python -m pytest tests/test_models.py -v
```

## 📁 项目结构

```
.
├── papers/              # 论文PDF和解析
├── notes/               # 研究笔记
├── datasets/            # 数据集
├── src/                 # 源代码
│   ├── models/          # 模型实现
│   ├── data/            # 数据加载
│   ├── train.py         # 训练脚本
│   └── evaluate.py      # 评估脚本
├── scripts/             # 辅助脚本
├── tests/               # 单元测试
├── artifacts/           # 模型检查点和结果
└── reports/             # 报告
```

## 🏗️ 实现模型

- **DCN-V2**: 改进的深度交叉网络（支持Stacked和Parallel两种结构）
- **DCN**: 原始深度交叉网络（V1）
- **DeepFM**: FM + DNN
- **DNN**: 深度神经网络基线
- **LR**: 逻辑回归基线

## 📊 核心公式

### DCN-V2 交叉层

```
x_{l+1} = x0 ⊙ (W_l * x_l + b_l) + x_l
```

其中 W_l ∈ R^(d×d) 为全秩矩阵，显著提升了表达能力。

### 低秩近似

```
x_{l+1} = x0 ⊙ (U_l * V_l^T * x_l + b_l) + x_l
```

其中 U_l, V_l ∈ R^(d×r)，r << d，降低计算成本。

## 📈 实验结果

在合成Criteo-like数据集上的对比：

| Model | AUC | LogLoss | #Params |
|-------|-----|---------|---------|
| DNN | 0.5024 | 0.7897 | 1.02M |
| DCN | 0.5022 | 1.5304 | 1.02M |
| DeepFM | 0.5001 | 0.8026 | 1.14M |
| DCN-V2 (Parallel) | 0.4992 | 1.9759 | 1.12M |
| DCN-V2 (Stacked) | 0.4980 | 0.5686 | 1.12M |

*注：合成数据上AUC接近随机水平，建议在真实Criteo数据上验证*

## ✅ 验证状态

- [x] 论文解析
- [x] DCN-V2实现
- [x] 基线模型实现
- [x] 数据加载器
- [x] 训练pipeline
- [x] 评估pipeline
- [x] 单元测试（13个测试通过）
- [x] 多模型对比
- [x] 最终报告

## 📚 参考文献

1. Wang, R., et al. (2021). DCN V2: Improved Deep & Cross Network. WWW.
2. Wang, R., et al. (2017). Deep & Cross Network. ADKDD.
3. Guo, H., et al. (2017). DeepFM. IJCAI.

## 📄 许可证

本复现仅供学术研究使用。
