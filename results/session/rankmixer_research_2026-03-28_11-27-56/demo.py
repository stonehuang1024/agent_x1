#!/usr/bin/env python3
"""
Quick demo script for RankMixer.
Demonstrates all model architectures without full training.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import torch
import numpy as np
from src.rankmixer_fixed import create_rankmixer_small, create_rankmixer_base, create_rankmixer_moe
from src.baselines_fixed import create_baseline_models
from src.data_loader import SyntheticDataset

def demo_models():
    """Demonstrate all models"""
    print("="*80)
    print("RankMixer Demo - Model Architectures")
    print("="*80)
    
    input_dim = 624
    batch_size = 8
    
    # Create sample input
    x = torch.randn(batch_size, input_dim)
    
    # RankMixer models
    print("\n--- RankMixer Models ---")
    models = {
        'RankMixer-Small (D=128, T=8, L=2)': create_rankmixer_small(input_dim),
        'RankMixer-Base (D=256, T=16, L=2)': create_rankmixer_base(input_dim),
        'RankMixer-MoE (D=256, T=16, L=2, E=4)': create_rankmixer_moe(input_dim),
    }
    
    for name, model in models.items():
        params = sum(p.numel() for p in model.parameters())
        output, aux_loss = model(x)
        print(f"\n{name}:")
        print(f"  Parameters: {params:,}")
        print(f"  Output shape: {output.shape}")
        if aux_loss is not None:
            print(f"  Aux loss: {aux_loss.item():.4f}")
    
    # Baseline models
    print("\n--- Baseline Models ---")
    baseline_models = create_baseline_models(input_dim)
    
    for name, model in baseline_models.items():
        params = sum(p.numel() for p in model.parameters())
        output = model(x)
        print(f"\n{name}:")
        print(f"  Parameters: {params:,}")
        print(f"  Output shape: {output.shape}")

def demo_dataset():
    """Demonstrate dataset generation"""
    print("\n" + "="*80)
    print("Synthetic Dataset Demo")
    print("="*80)
    
    dataset = SyntheticDataset(num_samples=1000, num_features=39)
    
    print(f"\nDataset size: {len(dataset)}")
    
    # Get a sample
    cat_feats, num_feats, label = dataset[0]
    print(f"\nSample:")
    print(f"  Categorical features shape: {cat_feats.shape}")
    print(f"  Numerical features shape: {num_feats.shape}")
    print(f"  Label: {label.item()}")
    
    # Statistics
    labels = [dataset[i][2].item() for i in range(len(dataset))]
    print(f"\nLabel distribution:")
    print(f"  Positive rate: {np.mean(labels):.3f}")

def demo_forward_pass():
    """Demonstrate forward pass with gradients"""
    print("\n" + "="*80)
    print("Forward/Backward Pass Demo")
    print("="*80)
    
    input_dim = 624
    model = create_rankmixer_small(input_dim)
    
    # Create input
    x = torch.randn(4, input_dim, requires_grad=True)
    
    # Forward pass
    output, aux_loss = model(x)
    predictions = torch.sigmoid(output)
    
    print(f"\nInput shape: {x.shape}")
    print(f"Output shape: {output.shape}")
    print(f"Predictions: {predictions.squeeze().detach().numpy()}")
    
    # Backward pass
    loss = predictions.mean()
    loss.backward()
    
    print(f"\nGradients computed:")
    print(f"  Input grad shape: {x.grad.shape}")
    print(f"  Input grad norm: {x.grad.norm().item():.4f}")
    
    # Check some parameters have gradients
    has_grad = sum(1 for p in model.parameters() if p.grad is not None)
    total_params = sum(1 for p in model.parameters())
    print(f"  Parameters with grad: {has_grad}/{total_params}")

def demo_comparison():
    """Compare model sizes and theoretical FLOPs"""
    print("\n" + "="*80)
    print("Model Comparison")
    print("="*80)
    
    input_dim = 624
    
    models = {
        'MLP-Small': create_baseline_models(input_dim)['MLP-Small'],
        'DeepFM': create_baseline_models(input_dim)['DeepFM'],
        'DCNv2': create_baseline_models(input_dim)['DCNv2'],
        'AutoInt': create_baseline_models(input_dim)['AutoInt'],
        'RankMixer-Small': create_rankmixer_small(input_dim),
        'RankMixer-Base': create_rankmixer_base(input_dim),
    }
    
    print(f"\n{'Model':<25} {'Parameters':>12} {'Size (MB)':>10}")
    print("-"*50)
    
    for name, model in models.items():
        params = sum(p.numel() for p in model.parameters())
        size_mb = params * 4 / (1024 * 1024)  # Assuming float32
        print(f"{name:<25} {params:>12,} {size_mb:>10.2f}")

def main():
    print("\n" + "="*80)
    print("RankMixer Research Demo")
    print("Paper: RankMixer: Scaling Up Ranking Models in Industrial Recommenders")
    print("arXiv: 2507.15551")
    print("="*80)
    
    # Run all demos
    demo_models()
    demo_dataset()
    demo_forward_pass()
    demo_comparison()
    
    print("\n" + "="*80)
    print("Demo completed successfully!")
    print("="*80)
    print("\nFor full experiments, run: python run_final_experiment.py")
    print("For model tests, run: python test_models_fixed.py")

if __name__ == "__main__":
    main()
