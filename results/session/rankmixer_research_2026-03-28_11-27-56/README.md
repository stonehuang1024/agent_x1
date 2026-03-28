# RankMixer: Scaling Up Ranking Models in Industrial Recommenders

[![Paper](https://img.shields.io/badge/arXiv-2507.15551-red)](https://arxiv.org/abs/2507.15551)
[![Python](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.0+-orange.svg)](https://pytorch.org/)

This repository contains a research reproduction of the RankMixer paper from ByteDance.

## 📄 Paper Information

**Title**: RankMixer: Scaling Up Ranking Models in Industrial Recommenders  
**Authors**: Jie Zhu, Zhifang Fan, Xiaoxie Zhu, et al. (ByteDance)  
**arXiv**: [2507.15551](https://arxiv.org/abs/2507.15551)  
**Published**: July 2025

## 🎯 Key Contributions

1. **Multi-head Token Mixing**: Parameter-free feature interaction module that replaces self-attention
2. **Per-token FFN (PFFN)**: Independent feed-forward networks for each token to model heterogeneous feature spaces
3. **Sparse MoE Extension**: ReLU routing with DTSI-MoE (Dense-training/Sparse-inference)
4. **Hardware-Aware Design**: Achieves 45% MFU (Model FLOPs Utilization) vs 4.5% in traditional models

## 📁 Repository Structure

```
rankmixer_research/
├── src/
│   ├── rankmixer_fixed.py      # RankMixer core implementation
│   ├── baselines_fixed.py       # Baseline models (MLP, DeepFM, DCNv2, AutoInt, MoE)
│   ├── data_loader.py           # Dataset loaders (Synthetic, Criteo, Avazu)
│   └── train_final.py           # Training script
├── experiments/                 # Experiment results
├── data/                        # Datasets
├── RESEARCH_REPORT.md           # Detailed research report
├── PAPER_NOTES.md              # Paper analysis notes
├── demo.py                     # Quick demo script
├── test_models_fixed.py        # Model testing suite
└── run_final_experiment.py     # Full experiment runner
```

## 🚀 Quick Start

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd rankmixer_research

# Install dependencies
pip install torch numpy pandas scikit-learn
```

### Quick Demo

```bash
# Run the demo to see all models in action
python demo.py
```

### Model Testing

```bash
# Test all models for correctness
python test_models_fixed.py
```

### Run Experiments

```bash
# Run full experiments (may take time)
python run_final_experiment.py
```

## 🏗️ Model Architectures

### RankMixer-Small
```python
from src.rankmixer_fixed import create_rankmixer_small

model = create_rankmixer_small(input_dim=624, num_tasks=1)
# Config: T=8, D=128, L=2, k=2.0
# Parameters: ~1.7M
```

### RankMixer-Base
```python
from src.rankmixer_fixed import create_rankmixer_base

model = create_rankmixer_base(input_dim=624, num_tasks=1)
# Config: T=16, D=256, L=2, k=4.0
# Parameters: ~19.5M
```

### RankMixer-MoE
```python
from src.rankmixer_fixed import create_rankmixer_moe

model = create_rankmixer_moe(input_dim=624, num_tasks=1)
# Config: T=16, D=256, L=2, k=4.0, E=4
# Parameters: ~70M
```

## 📊 Model Comparison

| Model | Parameters | Description |
|-------|-----------|-------------|
| MLP-Small | 88K | Simple baseline |
| DeepFM | 89K | FM + MLP |
| AutoInt | 223K | Self-attention based |
| DCNv2 | 912K | Deep & Cross Network |
| **RankMixer-Small** | **1.7M** | **Our implementation** |
| RankMixer-Base | 19.5M | Larger variant |
| RankMixer-MoE | 70M | Sparse MoE variant |

## 🔬 Implementation Details

### Multi-head Token Mixing
```python
# Split tokens into heads and shuffle
x = x.view(B, T, H, D//H)      # (B, T, H, D//H)
x = x.permute(0, 2, 1, 3)       # (B, H, T, D//H)
x = x.view(B, H, T*D//H)        # (B, H, T*D//H)
```

### Per-token FFN
```python
# Each token has independent FFN parameters
for t in range(num_tokens):
    hidden = torch.matmul(token[t], W1[t])  # W1[t]: (D, kD)
    hidden = GELU(hidden)
    output = torch.matmul(hidden, W2[t])    # W2[t]: (kD, D)
```

### Sparse MoE with ReLU Routing
```python
# ReLU routing instead of softmax Top-k
gates = ReLU(router(token))  # Flexible expert activation
aux_loss = λ * sum(gates)     # L1 sparsity regularization
```

## 📈 Expected Results

Based on the paper (production dataset):

| Model | Finish AUC Gain | Skip AUC Gain |
|-------|----------------|---------------|
| DCNv2 | +0.13% | +0.15% |
| DHEN | +0.18% | +0.36% |
| Wukong | +0.29% | +0.49% |
| **RankMixer-100M** | **+0.64%** | **+0.86%** |
| **RankMixer-1B** | **+0.95%** | **+1.25%** |

## 🧪 Experiments

### Quick Test
```bash
python demo.py
```

### Full Training
```python
from src.train_final import run_experiment

config = {
    'dataset': 'synthetic',      # or 'criteo', 'avazu'
    'num_samples': 50000,
    'batch_size': 256,
    'num_epochs': 15,
    'learning_rate': 0.001,
    'models': ['rankmixer_small', 'rankmixer_base', 'baselines']
}

histories = run_experiment(config)
```

## 📚 Documentation

- **[RESEARCH_REPORT.md](RESEARCH_REPORT.md)**: Detailed research report with implementation details
- **[PAPER_NOTES.md](PAPER_NOTES.md)**: In-depth paper analysis and notes

## 🔑 Key Insights

1. **Why Token Mixing over Self-Attention?**
   - Self-attention computes similarity between tokens
   - In recommendation, features are from heterogeneous spaces
   - Token mixing avoids meaningless cross-space similarity computation

2. **Why Per-token FFN?**
   - Shared FFN may cause high-frequency features to dominate
   - Per-token FFN isolates different feature subspaces
   - Better modeling of diverse feature patterns

3. **Hardware-Aware Design**
   - Traditional models: memory-bound, low MFU (~4.5%)
   - RankMixer: compute-bound, high MFU (~45%)
   - Enables 100x parameter scaling with same latency

## 🛠️ Development

### Running Tests
```bash
python test_models_fixed.py
```

### Adding New Models
```python
# In src/baselines_fixed.py
class MyModel(nn.Module):
    def __init__(self, input_dim, ...):
        super().__init__()
        # Your implementation
    
    def forward(self, x):
        # Forward pass
        return predictions
```

## 📖 Citation

```bibtex
@article{zhu2025rankmixer,
  title={RankMixer: Scaling Up Ranking Models in Industrial Recommenders},
  author={Zhu, Jie and Fan, Zhifang and Zhu, Xiaoxie and Jiang, Yuchen and others},
  journal={arXiv preprint arXiv:2507.15551},
  year={2025}
}
```

## 📝 License

This reproduction is for research and educational purposes.

## 🙏 Acknowledgments

- Original paper authors from ByteDance
- PyTorch team for the excellent framework
- Recommendation systems research community

## 📧 Contact

For questions or issues, please open an issue in the repository.

---

**Note**: This is a research reproduction. The original paper uses proprietary production data from ByteDance. We use synthetic and public datasets for validation.
