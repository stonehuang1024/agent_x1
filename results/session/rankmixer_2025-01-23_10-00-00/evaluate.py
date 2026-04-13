"""
Evaluation and comparison script for RankMixer and baseline models
"""

import os
import json
import argparse
import numpy as np
import pandas as pd
import torch
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
import seaborn as sns
from typing import Dict, List

from train import train_model


def run_comparison(args):
    """Run comparison between multiple models"""
    
    results = {}
    
    # Define model configurations - all configs have all required parameters
    configs = [
        # Small models for fast iteration
        {
            'name': 'RankMixer-Small',
            'model': 'rankmixer',
            'hidden_dim': 64,
            'num_layers': 2,
            'num_tokens': 8,
            'num_heads': 8,
            'ffn_ratio': 4,
            'use_moe': False,
            'num_experts': 4,
            'moe_top_k': 2,
            'dropout': 0.1,
        },
        {
            'name': 'RankMixer-Medium',
            'model': 'rankmixer',
            'hidden_dim': 128,
            'num_layers': 2,
            'num_tokens': 16,
            'num_heads': 16,
            'ffn_ratio': 4,
            'use_moe': False,
            'num_experts': 4,
            'moe_top_k': 2,
            'dropout': 0.1,
        },
        {
            'name': 'RankMixer-MoE',
            'model': 'rankmixer',
            'hidden_dim': 128,
            'num_layers': 2,
            'num_tokens': 16,
            'num_heads': 16,
            'ffn_ratio': 4,
            'use_moe': True,
            'num_experts': 4,
            'moe_top_k': 2,
            'dropout': 0.1,
        },
        {
            'name': 'DeepFM',
            'model': 'deepfm',
            'hidden_dim': 128,
            'num_layers': 2,
            'num_tokens': 16,
            'num_heads': 16,
            'ffn_ratio': 4,
            'use_moe': False,
            'num_experts': 4,
            'moe_top_k': 2,
            'dropout': 0.1,
        },
        {
            'name': 'DCNv2',
            'model': 'dcnv2',
            'hidden_dim': 128,
            'num_layers': 3,
            'num_tokens': 16,
            'num_heads': 16,
            'ffn_ratio': 4,
            'use_moe': False,
            'num_experts': 4,
            'moe_top_k': 2,
            'dropout': 0.1,
        },
    ]
    
    # Run experiments
    for config in configs:
        print(f"\n{'='*80}")
        print(f"Running {config['name']}...")
        print(f"{'='*80}")
        
        # Update args with config
        for key, value in config.items():
            if key != 'name':
                setattr(args, key, value)
        
        args.output_dir = os.path.join('./output/compare', config['name'])
        
        try:
            result = train_model(args)
            results[config['name']] = result
        except Exception as e:
            print(f"Error running {config['name']}: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    # Save comparison results
    os.makedirs('./output/compare', exist_ok=True)
    
    with open('./output/compare/comparison_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    
    # Generate comparison plots
    plot_comparison(results)
    
    # Print summary table
    print_summary_table(results)
    
    return results


def plot_comparison(results: Dict):
    """Generate comparison plots"""
    
    if len(results) == 0:
        print("No results to plot")
        return
    
    # Set style
    sns.set_style("whitegrid")
    plt.rcParams['figure.figsize'] = (15, 10)
    
    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    
    # Plot 1: Validation AUC over epochs
    ax = axes[0, 0]
    for name, result in results.items():
        history = result['history']
        epochs = [h['epoch'] for h in history]
        val_aucs = [h['val_auc'] for h in history]
        ax.plot(epochs, val_aucs, marker='o', label=name, linewidth=2)
    ax.set_xlabel('Epoch')
    ax.set_ylabel('Validation AUC')
    ax.set_title('Validation AUC over Epochs')
    ax.legend()
    ax.grid(True, alpha=0.3)
    
    # Plot 2: Parameters vs Best AUC
    ax = axes[0, 1]
    names = list(results.keys())
    params = [results[n]['num_parameters'] / 1e6 for n in names]  # in millions
    best_aucs = [results[n]['best_val_auc'] for n in names]
    
    colors = plt.cm.tab10(np.linspace(0, 1, len(names)))
    for i, (name, param, auc) in enumerate(zip(names, params, best_aucs)):
        ax.scatter(param, auc, s=200, c=[colors[i]], label=name, alpha=0.7, edgecolors='black')
    
    ax.set_xlabel('Number of Parameters (Millions)')
    ax.set_ylabel('Best Validation AUC')
    ax.set_title('Model Size vs Performance')
    ax.legend()
    ax.grid(True, alpha=0.3)
    
    # Plot 3: Training time comparison
    ax = axes[1, 0]
    train_times = [results[n]['total_training_time'] / 60 for n in names]  # in minutes
    bars = ax.bar(names, train_times, color=colors, alpha=0.7, edgecolor='black')
    ax.set_ylabel('Training Time (minutes)')
    ax.set_title('Training Time Comparison')
    ax.tick_params(axis='x', rotation=45)
    
    # Add value labels on bars
    for bar in bars:
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height,
                f'{height:.1f}m',
                ha='center', va='bottom', fontsize=9)
    
    # Plot 4: Convergence speed (epochs to best AUC)
    ax = axes[1, 1]
    best_epochs = [results[n]['best_epoch'] for n in names]
    bars = ax.bar(names, best_epochs, color=colors, alpha=0.7, edgecolor='black')
    ax.set_ylabel('Epochs to Best AUC')
    ax.set_title('Convergence Speed')
    ax.tick_params(axis='x', rotation=45)
    
    # Add value labels on bars
    for bar in bars:
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height,
                f'{int(height)}',
                ha='center', va='bottom', fontsize=9)
    
    plt.tight_layout()
    plt.savefig('./output/compare/comparison_plots.png', dpi=300, bbox_inches='tight')
    print("\nComparison plots saved to: ./output/compare/comparison_plots.png")
    plt.close()


def print_summary_table(results: Dict):
    """Print summary comparison table"""
    
    print("\n" + "="*100)
    print("MODEL COMPARISON SUMMARY")
    print("="*100)
    
    if len(results) == 0:
        print("No results to display")
        return
    
    # Create DataFrame
    data = []
    for name, result in results.items():
        data.append({
            'Model': name,
            'Parameters (M)': f"{result['num_parameters'] / 1e6:.2f}",
            'Best Val AUC': f"{result['best_val_auc']:.4f}",
            'Best Epoch': result['best_epoch'],
            'Training Time (min)': f"{result['total_training_time'] / 60:.1f}"
        })
    
    df = pd.DataFrame(data)
    print(df.to_string(index=False))
    
    # Calculate improvements
    print("\n" + "="*100)
    print("PERFORMANCE IMPROVEMENTS")
    print("="*100)
    
    baseline_auc = results.get('DeepFM', {}).get('best_val_auc', 0.5)
    
    for name, result in results.items():
        if name != 'DeepFM':
            improvement = (result['best_val_auc'] - baseline_auc) * 100
            print(f"{name}: {improvement:+.4f}% AUC improvement over DeepFM")
    
    print("="*100)


def analyze_scaling_law(args):
    """Analyze scaling law by training models of different sizes"""
    
    print("\n" + "="*80)
    print("SCALING LAW ANALYSIS")
    print("="*80)
    
    # Different model sizes
    hidden_dims = [32, 64, 96, 128]
    results = []
    
    for hidden_dim in hidden_dims:
        print(f"\nTraining RankMixer with hidden_dim={hidden_dim}...")
        
        args.model = 'rankmixer'
        args.hidden_dim = hidden_dim
        args.num_layers = 2
        args.num_tokens = max(8, hidden_dim // 8)
        args.num_heads = max(8, hidden_dim // 8)
        args.ffn_ratio = 4
        args.use_moe = False
        args.num_experts = 4
        args.moe_top_k = 2
        args.dropout = 0.1
        args.output_dir = f'./output/scaling/hidden_{hidden_dim}'
        
        try:
            result = train_model(args)
            results.append({
                'hidden_dim': hidden_dim,
                'num_tokens': args.num_tokens,
                'params': result['num_parameters'],
                'best_auc': result['best_val_auc']
            })
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    # Plot scaling law
    if len(results) > 1:
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        
        # Scaling with parameters
        ax = axes[0]
        params = [r['params'] / 1e6 for r in results]
        aucs = [r['best_auc'] for r in results]
        ax.plot(params, aucs, marker='o', linewidth=2, markersize=8)
        ax.set_xlabel('Number of Parameters (Millions)')
        ax.set_ylabel('Best Validation AUC')
        ax.set_title('Scaling Law: Parameters vs Performance')
        ax.grid(True, alpha=0.3)
        
        # Scaling with hidden dimension
        ax = axes[1]
        hidden_dims_list = [r['hidden_dim'] for r in results]
        ax.plot(hidden_dims_list, aucs, marker='s', linewidth=2, markersize=8, color='orange')
        ax.set_xlabel('Hidden Dimension')
        ax.set_ylabel('Best Validation AUC')
        ax.set_title('Scaling Law: Hidden Dim vs Performance')
        ax.grid(True, alpha=0.3)
        
        plt.tight_layout()
        plt.savefig('./output/scaling/scaling_law.png', dpi=300, bbox_inches='tight')
        print("\nScaling law plot saved to: ./output/scaling/scaling_law.png")
        plt.close()
        
        # Save results
        with open('./output/scaling/scaling_results.json', 'w') as f:
            json.dump(results, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description='Evaluate and compare models')
    
    parser.add_argument('--mode', type=str, default='compare',
                       choices=['compare', 'scaling', 'single'],
                       help='Evaluation mode')
    
    # Data arguments
    parser.add_argument('--dataset', type=str, default='synthetic',
                       choices=['criteo', 'avazu', 'movielens', 'synthetic'])
    parser.add_argument('--data_path', type=str, default='./data')
    parser.add_argument('--n_samples', type=int, default=50000)
    parser.add_argument('--n_features', type=int, default=39)
    
    # Training arguments
    parser.add_argument('--epochs', type=int, default=15)
    parser.add_argument('--batch_size', type=int, default=256)
    parser.add_argument('--lr', type=float, default=0.001)
    parser.add_argument('--seed', type=int, default=42)
    
    args = parser.parse_args()
    
    if args.mode == 'compare':
        run_comparison(args)
    elif args.mode == 'scaling':
        analyze_scaling_law(args)
    elif args.mode == 'single':
        # Run single model evaluation
        args.model = 'rankmixer'
        args.hidden_dim = 128
        args.num_layers = 2
        args.num_tokens = 16
        args.num_heads = 16
        args.ffn_ratio = 4
        args.use_moe = False
        args.num_experts = 4
        args.moe_top_k = 2
        args.dropout = 0.1
        args.output_dir = './output/single_eval'
        train_model(args)


if __name__ == "__main__":
    main()
