# RankMixer: Scaling Up Ranking Models in Industrial Recommenders

This repository contains a PyTorch implementation of **RankMixer** from the paper "RankMixer: Scaling Up Ranking Models in Industrial Recommenders" by Zhu et al. (ByteDance, 2025).

## Paper Overview

**RankMixer** is a hardware-aware recommendation model designed to scale efficiently on modern GPUs while maintaining high prediction accuracy. The key innovations include:

1. **Multi-head Token Mixing**: Replaces expensive self-attention with a parameter-free token mixing mechanism for efficient cross-feature interactions
2. **Per-token FFN**: Uses independent feed-forward networks for each feature token to prevent feature domination
3. **Sparse MoE Extension**: Supports scaling to billions of parameters using ReLU routing and DTSI (Dense-Training-Sparse-Inference)

### Key Results from Paper
- Scaled from 16M to 1B parameters (70× increase) without increasing inference latency
- Improved MFU from 4.5% to 45% (10× improvement)
- Online A/B tests: +0.3% active days, +1.08% app duration on Douyin

## Repository Structure

```
.
├── rankmixer_model.py      # Core RankMixer implementation + baselines
├── data_loader.py          # Dataset loaders (Criteo, Avazu, MovieLens, Synthetic)
├── train.py               # Training script
├── evaluate.py            # Evaluation and comparison script
├── quick_comparison.py    # Quick demo comparison
├── run_experiments.py     # Full experiment pipeline
└── output/                # Experiment results
```

## Installation

```bash
pip install torch numpy pandas scikit-learn matplotlib seaborn tqdm
```

## Quick Start

### 1. Test Model Implementation
```bash
python rankmixer_model.py
```

### 2. Run Quick Comparison
```bash
python quick_comparison.py
```

This runs a fast comparison between RankMixer variants and baselines (DeepFM, DCNv2) on a synthetic dataset.

### 3. Train Individual Model
```bash
python train.py --model rankmixer --dataset synthetic \
    --n_samples 10000 --n_features 39 --epochs 15 \
    --hidden_dim 128 --num_tokens 16 --num_layers 2
```

### 4. Full Comparison
```bash
python evaluate.py --mode compare --dataset synthetic \
    --n_samples 20000 --epochs 15 --batch_size 256
```

### 5. Scaling Law Analysis
```bash
python evaluate.py --mode scaling --dataset synthetic \
    --n_samples 20000 --epochs 12
```

## Model Architectures

### RankMixer
```python
model = RankMixer(
    feature_dims=[100] * 39,  # Embedding dimension for each feature
    num_tokens=16,            # Number of feature tokens
    hidden_dim=128,           # Hidden dimension
    num_layers=2,             # Number of RankMixer blocks
    num_heads=16,             # Number of heads for token mixing
    ffn_ratio=4,              # FFN expansion ratio
    use_moe=False,            # Whether to use Sparse MoE
    num_experts=4,            # Number of experts (if use_moe=True)
    dropout=0.1
)
```

### Baselines
- **DeepFM**: Factorization Machine + Deep Neural Network
- **DCNv2**: Deep & Cross Network v2

## Datasets

### Supported Datasets
1. **Synthetic** (default): Generated for quick testing
2. **Criteo**: Display Advertising Challenge dataset
3. **Avazu**: CTR Prediction dataset
4. **MovieLens 1M**: Movie rating dataset

### Download Real Datasets

**MovieLens 1M:**
```python
from data_loader import download_movielens_1m
download_movielens_1m('./data/movielens')
```

**Criteo & Avazu:**
Download from Kaggle and place in `./data/criteo` or `./data/avazu`.

## Implementation Details

### Multi-head Token Mixing
```python
# Split tokens into heads
x_split = x.reshape(B, T, H, D//H)
# Transpose to mix across tokens
x_transposed = x_split.permute(0, 2, 1, 3)
# Merge to create mixed tokens
output = x_transposed.reshape(B, T, D)
```

### Per-token FFN
Each token has its own FFN parameters:
```python
# Token i: s_i -> W_i1 -> GELU -> W_i2 -> output
v_i = W_i2 @ GELU(W_i1 @ s_i + b_i1) + b_i2
```

### Sparse MoE (Optional)
- **ReLU Routing**: Flexible expert activation with L1 regularization
- **DTSI**: Dense training + sparse inference for better expert utilization

## Experimental Results

### Quick Comparison Results (Synthetic Dataset)

| Model | Parameters | Best AUC | Improvement over DeepFM |
|-------|-----------|----------|------------------------|
| DeepFM | 123K | 0.4851 | - |
| DCNv2 | 945K | 0.5058 | +2.08% |
| RankMixer-Small | 167K | 0.5173 | +3.22% |
| RankMixer-Medium | 600K | 0.5325 | +4.75% |

### Key Observations
1. **RankMixer-Medium** achieves the best AUC with moderate parameter count
2. **Parameter Efficiency**: RankMixer shows better scaling than DCNv2
3. **Fast Convergence**: Reaches good performance in just 5 epochs

## Scaling Configurations

Based on the paper, here are recommended configurations:

| Size | Hidden Dim | Tokens | Layers | Parameters | Use Case |
|------|-----------|--------|--------|------------|----------|
| Small | 64 | 8 | 2 | ~170K | Quick experiments |
| Medium | 128 | 16 | 2 | ~600K | Standard training |
| Large | 256 | 16 | 2 | ~2.4M | High accuracy |
| Paper 100M | 768 | 16 | 2 | ~100M | Paper reproduction |
| Paper 1B | 1536 | 32 | 2 | ~1.1B | Production scale |

## Citation

```bibtex
@article{zhu2025rankmixer,
  title={RankMixer: Scaling Up Ranking Models in Industrial Recommenders},
  author={Zhu, Jie and Fan, Zhifang and Zhu, Xiaoxie and Jiang, Yuchen and Wang, Hangyu and others},
  journal={arXiv preprint arXiv:2507.15551},
  year={2025}
}
```

## License

This implementation is for research and educational purposes only.

## Acknowledgments

- Original paper authors from ByteDance
- PyTorch team for the deep learning framework
- Various open-source recommendation datasets
