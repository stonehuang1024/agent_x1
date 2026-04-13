"""
Quick comparison for demonstration - smaller dataset and fewer epochs
"""

import os
import json
import time
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.metrics import roc_auc_score, log_loss
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from rankmixer_model import RankMixer, DeepFM, DCNv2
from data_loader import SyntheticDataset
from torch.utils.data import DataLoader


def calculate_metrics(predictions, labels):
    predictions = predictions.flatten()
    labels = labels.flatten()
    if len(np.unique(labels)) < 2:
        return 0.5, 1.0
    try:
        auc = roc_auc_score(labels, predictions)
        logloss = log_loss(labels, np.clip(predictions, 1e-7, 1-1e-7))
    except:
        auc = 0.5
        logloss = 1.0
    return auc, logloss


def train_and_evaluate(model_name, model, train_loader, val_loader, epochs=5, lr=0.001):
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = model.to(device)
    criterion = nn.BCELoss()
    optimizer = optim.Adam(model.parameters(), lr=lr, weight_decay=1e-5)
    
    best_auc = 0
    best_epoch = 0
    history = []
    
    print(f"\n{'='*60}")
    print(f"Training {model_name}")
    print(f"Parameters: {sum(p.numel() for p in model.parameters()):,}")
    print(f"{'='*60}")
    
    start_time = time.time()
    
    for epoch in range(epochs):
        model.train()
        train_loss = 0
        train_preds, train_labels = [], []
        
        for features, labels in train_loader:
            features, labels = features.to(device), labels.to(device)
            optimizer.zero_grad()
            outputs = model(features)
            loss = criterion(outputs, labels)
            if hasattr(outputs, 'aux_loss'):
                loss = loss + outputs.aux_loss
            loss.backward()
            optimizer.step()
            
            train_loss += loss.item()
            train_preds.append(outputs.detach().cpu().numpy())
            train_labels.append(labels.cpu().numpy())
        
        train_preds = np.concatenate(train_preds)
        train_labels = np.concatenate(train_labels)
        train_auc, _ = calculate_metrics(train_preds, train_labels)
        
        model.eval()
        val_loss = 0
        val_preds, val_labels = [], []
        
        with torch.no_grad():
            for features, labels in val_loader:
                features, labels = features.to(device), labels.to(device)
                outputs = model(features)
                loss = criterion(outputs, labels)
                val_loss += loss.item()
                val_preds.append(outputs.cpu().numpy())
                val_labels.append(labels.cpu().numpy())
        
        val_preds = np.concatenate(val_preds)
        val_labels = np.concatenate(val_labels)
        val_auc, _ = calculate_metrics(val_preds, val_labels)
        
        print(f"Epoch {epoch+1}/{epochs} - Train AUC: {train_auc:.4f}, Val AUC: {val_auc:.4f}")
        
        history.append({'epoch': epoch+1, 'train_auc': train_auc, 'val_auc': val_auc})
        
        if val_auc > best_auc:
            best_auc = val_auc
            best_epoch = epoch + 1
    
    training_time = time.time() - start_time
    
    return {
        'model': model_name,
        'num_parameters': sum(p.numel() for p in model.parameters()),
        'best_auc': best_auc,
        'best_epoch': best_epoch,
        'training_time': training_time,
        'history': history
    }


def main():
    print("="*80)
    print("RANKMIXER QUICK COMPARISON (Reduced Scale)")
    print("="*80)
    
    # Smaller dataset for quick demo
    n_features = 20
    feature_dim = 50
    n_train = 5000
    n_val = 1250
    batch_size = 128
    epochs = 5
    
    print(f"\nDataset: {n_train} training, {n_val} validation")
    print(f"Features: {n_features}, Epochs: {epochs}")
    
    train_dataset = SyntheticDataset(n_samples=n_train, n_features=n_features, 
                                     feature_dim=feature_dim, split='train')
    val_dataset = SyntheticDataset(n_samples=n_val, n_features=n_features,
                                   feature_dim=feature_dim, split='test')
    
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)
    
    feature_dims = train_dataset.feature_dims
    
    # Reduced models for faster training
    models_config = [
        {
            'name': 'RankMixer-Small',
            'model': RankMixer(feature_dims=feature_dims, num_tokens=8, hidden_dim=32,
                             num_layers=2, num_heads=8, ffn_ratio=4, use_moe=False)
        },
        {
            'name': 'RankMixer-Medium',
            'model': RankMixer(feature_dims=feature_dims, num_tokens=8, hidden_dim=64,
                             num_layers=2, num_heads=8, ffn_ratio=4, use_moe=False)
        },
        {
            'name': 'DeepFM',
            'model': DeepFM(feature_dims=feature_dims, embed_dim=32,
                          mlp_dims=[128, 64], dropout=0.1)
        },
        {
            'name': 'DCNv2',
            'model': DCNv2(feature_dims=feature_dims, embed_dim=32,
                         num_cross_layers=2, mlp_dims=[128, 64], dropout=0.1)
        },
    ]
    
    results = {}
    for config in models_config:
        result = train_and_evaluate(config['name'], config['model'], 
                                   train_loader, val_loader, epochs=epochs)
        results[config['name']] = result
    
    # Save and display results
    os.makedirs('./output/compare', exist_ok=True)
    with open('./output/compare/comparison_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    
    print("\n" + "="*80)
    print("COMPARISON SUMMARY")
    print("="*80)
    print(f"{'Model':<20} {'Parameters':<15} {'Best AUC':<12} {'Best Epoch':<12} {'Time (s)':<10}")
    print("-"*80)
    
    for name, result in results.items():
        print(f"{name:<20} {result['num_parameters']:<15,} {result['best_auc']:<12.4f} "
              f"{result['best_epoch']:<12} {result['training_time']:<10.1f}")
    
    # Improvements
    print("\n" + "="*80)
    print("PERFORMANCE IMPROVEMENTS (relative to DeepFM)")
    print("="*80)
    
    baseline_auc = results['DeepFM']['best_auc']
    for name, result in results.items():
        if name != 'DeepFM':
            improvement = (result['best_auc'] - baseline_auc) * 100
            print(f"{name}: {improvement:+.4f}% AUC")
    
    plot_results(results)
    print("\nResults saved to ./output/compare/")


def plot_results(results):
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    
    # Plot 1: Validation AUC
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
    
    # Plot 2: Parameters vs AUC
    ax = axes[0, 1]
    names = list(results.keys())
    params = [results[n]['num_parameters'] / 1e6 for n in names]
    best_aucs = [results[n]['best_auc'] for n in names]
    
    colors = plt.cm.tab10(np.linspace(0, 1, len(names)))
    for i, (name, param, auc) in enumerate(zip(names, params, best_aucs)):
        ax.scatter(param, auc, s=200, c=[colors[i]], label=name, alpha=0.7, edgecolors='black')
    ax.set_xlabel('Parameters (Millions)')
    ax.set_ylabel('Best Validation AUC')
    ax.set_title('Model Size vs Performance')
    ax.legend()
    ax.grid(True, alpha=0.3)
    
    # Plot 3: Training time
    ax = axes[1, 0]
    train_times = [results[n]['training_time'] for n in names]
    bars = ax.bar(names, train_times, color=colors, alpha=0.7, edgecolor='black')
    ax.set_ylabel('Training Time (seconds)')
    ax.set_title('Training Time Comparison')
    ax.tick_params(axis='x', rotation=45)
    
    # Plot 4: Convergence
    ax = axes[1, 1]
    best_epochs = [results[n]['best_epoch'] for n in names]
    bars = ax.bar(names, best_epochs, color=colors, alpha=0.7, edgecolor='black')
    ax.set_ylabel('Epochs to Best AUC')
    ax.set_title('Convergence Speed')
    ax.tick_params(axis='x', rotation=45)
    
    plt.tight_layout()
    plt.savefig('./output/compare/comparison_plots.png', dpi=300, bbox_inches='tight')
    print("\nPlots saved to ./output/compare/comparison_plots.png")
    plt.close()


if __name__ == "__main__":
    main()
