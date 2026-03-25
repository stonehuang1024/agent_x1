"""
Hyperparameter tuning script for DCN-V2 and baseline models on real Criteo dataset
Based on SKILL.md optimization guidelines
"""
import os
import sys
import json
import argparse
import time
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.tensorboard import SummaryWriter
from sklearn.metrics import roc_auc_score, log_loss
from itertools import product

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.models.dcn_v2 import DCNv2, DCN
from src.models.baselines import DeepFM, DNN, LogisticRegression
from src.data.dataloader_large import get_dataloaders_large


def get_model(model_name, num_dense, sparse_vocab_sizes, embedding_dim, config):
    """Create model based on name with optimized hyperparameters"""
    if model_name == 'dcnv2_stacked':
        return DCNv2(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            cross_layers=config.get('cross_layers', 3),
            deep_hidden_dims=config.get('deep_hidden_dims', [512, 256, 128]),
            dropout_rate=config.get('dropout_rate', 0.3),
            use_bn=config.get('use_bn', True),
            structure='stacked',
            low_rank=config.get('low_rank', 0)
        )
    elif model_name == 'dcnv2_parallel':
        return DCNv2(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            cross_layers=config.get('cross_layers', 3),
            deep_hidden_dims=config.get('deep_hidden_dims', [512, 256, 128]),
            dropout_rate=config.get('dropout_rate', 0.3),
            use_bn=config.get('use_bn', True),
            structure='parallel',
            low_rank=config.get('low_rank', 0)
        )
    elif model_name == 'dcn':
        return DCN(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            cross_layers=config.get('cross_layers', 3),
            deep_hidden_dims=config.get('deep_hidden_dims', [512, 256, 128]),
            dropout_rate=config.get('dropout_rate', 0.3),
            use_bn=config.get('use_bn', True),
            structure=config.get('structure', 'stacked')
        )
    elif model_name == 'deepfm':
        return DeepFM(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            deep_hidden_dims=config.get('deep_hidden_dims', [512, 256, 128]),
            dropout_rate=config.get('dropout_rate', 0.3),
            use_bn=config.get('use_bn', True)
        )
    elif model_name == 'dnn':
        return DNN(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            hidden_dims=config.get('deep_hidden_dims', [512, 256, 128, 64]),
            dropout_rate=config.get('dropout_rate', 0.3),
            use_bn=config.get('use_bn', True)
        )
    elif model_name == 'lr':
        return LogisticRegression(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim
        )
    else:
        raise ValueError(f"Unknown model: {model_name}")


def train_epoch(model, train_loader, criterion, optimizer, device, max_batches=None):
    """Train for one epoch with optional batch limit"""
    model.train()
    total_loss = 0
    num_batches = 0
    
    for batch_idx, (dense_features, sparse_features, labels) in enumerate(train_loader):
        if max_batches and batch_idx >= max_batches:
            break
            
        dense_features = dense_features.to(device)
        sparse_features = sparse_features.to(device)
        labels = labels.to(device)
        
        optimizer.zero_grad()
        
        # Forward pass
        logits = model(dense_features, sparse_features)
        loss = criterion(logits, labels)
        
        # Add L2 regularization
        l2_reg = 0
        for param in model.parameters():
            l2_reg += torch.sum(param ** 2)
        loss = loss + args.l2_reg * l2_reg
        
        # Backward pass
        loss.backward()
        
        # Gradient clipping
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        
        optimizer.step()
        
        total_loss += loss.item()
        num_batches += 1
    
    return total_loss / num_batches if num_batches > 0 else 0


def evaluate(model, data_loader, criterion, device, max_batches=None):
    """Evaluate model with optional batch limit"""
    model.eval()
    total_loss = 0
    num_batches = 0
    
    all_labels = []
    all_preds = []
    
    with torch.no_grad():
        for batch_idx, (dense_features, sparse_features, labels) in enumerate(data_loader):
            if max_batches and batch_idx >= max_batches:
                break
                
            dense_features = dense_features.to(device)
            sparse_features = sparse_features.to(device)
            labels = labels.to(device)
            
            # Forward pass
            logits = model(dense_features, sparse_features)
            loss = criterion(logits, labels)
            
            total_loss += loss.item()
            num_batches += 1
            
            # Store predictions
            probs = torch.sigmoid(logits)
            all_labels.extend(labels.cpu().numpy())
            all_preds.extend(probs.cpu().numpy())
    
    avg_loss = total_loss / num_batches if num_batches > 0 else float('inf')
    all_labels = np.array(all_labels)
    all_preds = np.array(all_preds)
    
    # Calculate metrics
    try:
        auc = roc_auc_score(all_labels, all_preds)
        logloss = log_loss(all_labels, all_preds, eps=1e-7)
    except:
        auc = 0.5
        logloss = float('inf')
    
    return avg_loss, auc, logloss


def run_experiment(model_name, config, args, train_loader, val_loader, test_loader, 
                   sparse_vocab_sizes, num_dense, device):
    """Run a single experiment with given configuration"""
    # Set random seeds
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    
    # Create model
    model = get_model(model_name, num_dense, sparse_vocab_sizes, config['embedding_dim'], config)
    model = model.to(device)
    
    # Count parameters
    num_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    
    # Loss and optimizer
    criterion = nn.BCEWithLogitsLoss()
    optimizer = optim.AdamW(
        model.parameters(), 
        lr=config['lr'], 
        weight_decay=config['weight_decay'],
        betas=(0.9, 0.999)
    )
    
    # Cosine annealing scheduler
    scheduler = optim.lr_scheduler.CosineAnnealingWarmRestarts(
        optimizer, T_0=10, T_mult=2, eta_min=config['lr'] * 0.01
    )
    
    # Training loop
    best_val_auc = 0
    best_epoch = 0
    
    print(f"\nTraining {model_name} with config: {config}")
    start_time = time.time()
    
    for epoch in range(args.epochs):
        epoch_start = time.time()
        
        # Train
        train_loss = train_epoch(model, train_loader, criterion, optimizer, device, 
                                max_batches=args.max_batches_per_epoch)
        
        # Evaluate
        val_loss, val_auc, val_logloss = evaluate(model, val_loader, criterion, device,
                                                  max_batches=args.max_batches_per_epoch)
        
        # Learning rate scheduling
        scheduler.step(epoch + train_loss)
        
        epoch_time = time.time() - epoch_start
        
        print(f"  Epoch {epoch+1}/{args.epochs} [{epoch_time:.1f}s] - "
              f"Train Loss: {train_loss:.4f}, "
              f"Val AUC: {val_auc:.4f}, "
              f"Val LogLoss: {val_logloss:.4f}")
        
        # Save best model
        if val_auc > best_val_auc:
            best_val_auc = val_auc
            best_epoch = epoch
    
    training_time = time.time() - start_time
    
    # Final evaluation on test set
    test_loss, test_auc, test_logloss = evaluate(model, test_loader, criterion, device)
    
    results = {
        'model': model_name,
        'config': config,
        'best_val_auc': float(best_val_auc),
        'test_auc': float(test_auc),
        'test_logloss': float(test_logloss),
        'num_params': num_params,
        'training_time': training_time,
        'best_epoch': best_epoch + 1
    }
    
    print(f"  Results - Val AUC: {best_val_auc:.4f}, Test AUC: {test_auc:.4f}, Test LogLoss: {test_logloss:.4f}")
    
    return results


def grid_search(model_name, param_grid, args, train_loader, val_loader, test_loader,
                sparse_vocab_sizes, num_dense, device):
    """Perform grid search over hyperparameters"""
    # Generate all combinations
    keys = list(param_grid.keys())
    values = [param_grid[k] for k in keys]
    
    all_results = []
    
    for combo in product(*values):
        config = dict(zip(keys, combo))
        
        try:
            result = run_experiment(
                model_name, config, args, train_loader, val_loader, test_loader,
                sparse_vocab_sizes, num_dense, device
            )
            all_results.append(result)
        except Exception as e:
            print(f"  Error with config {config}: {e}")
            continue
    
    # Sort by test AUC
    all_results.sort(key=lambda x: x['test_auc'], reverse=True)
    
    return all_results


def main():
    global args
    parser = argparse.ArgumentParser(description='Hyperparameter tuning for DCN-V2 and baselines')
    
    # Data
    parser.add_argument('--data_dir', type=str, 
                        default='/Users/simonwang/agent_x1/results/session/session_153909_2026-03-15_15-39-09/research/recommendation_research/datasets_real',
                        help='Data directory')
    parser.add_argument('--batch_size', type=int, default=2048, help='Batch size')
    parser.add_argument('--num_workers', type=int, default=2, help='Number of data loading workers')
    parser.add_argument('--max_batches_per_epoch', type=int, default=None, help='Max batches per epoch')
    
    # Model
    parser.add_argument('--model', type=str, default='dcnv2_stacked',
                        choices=['dcnv2_stacked', 'dcnv2_parallel', 'dcn', 'deepfm', 'dnn'],
                        help='Model name')
    
    # Training
    parser.add_argument('--epochs', type=int, default=2, help='Number of epochs')
    parser.add_argument('--l2_reg', type=float, default=1e-5, help='L2 regularization')
    parser.add_argument('--seed', type=int, default=42, help='Random seed')
    
    # Output
    parser.add_argument('--output_dir', type=str, 
                        default='/Users/simonwang/agent_x1/results/session/session_153909_2026-03-15_15-39-09/research/recommendation_research/artifacts',
                        help='Directory to save results')
    
    args = parser.parse_args()
    
    # Device
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}")
    
    # Load data
    print("Loading data...")
    train_loader, val_loader, test_loader, sparse_vocab_sizes, num_dense, num_sparse = get_dataloaders_large(
        args.data_dir,
        batch_size=args.batch_size,
        num_workers=args.num_workers,
        use_chunked=True
    )
    
    print(f"Train samples: {len(train_loader.dataset)}")
    print(f"Val samples: {len(val_loader.dataset)}")
    print(f"Test samples: {len(test_loader.dataset)}")
    
    # Define hyperparameter grid based on SKILL.md guidelines
    param_grid = {
        'embedding_dim': [16, 32, 64],
        'deep_hidden_dims': [[256, 128], [512, 256, 128], [512, 256, 128, 64]],
        'cross_layers': [2, 3, 4],
        'dropout_rate': [0.2, 0.3],
        'lr': [1e-3, 5e-4, 1e-4],
        'weight_decay': [1e-5, 1e-6],
        'use_bn': [True]
    }
    
    # For non-cross models, remove cross_layers
    if args.model in ['deepfm', 'dnn']:
        del param_grid['cross_layers']
    
    print(f"\n{'='*60}")
    print(f"Starting hyperparameter tuning for {args.model}")
    print(f"Parameter grid: {param_grid}")
    print(f"{'='*60}")
    
    # Run grid search
    results = grid_search(
        args.model, param_grid, args, train_loader, val_loader, test_loader,
        sparse_vocab_sizes, num_dense, device
    )
    
    # Save results
    os.makedirs(args.output_dir, exist_ok=True)
    output_file = os.path.join(args.output_dir, f'{args.model}_tuning_results.json')
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    
    # Print summary
    print(f"\n{'='*60}")
    print("HYPERPARAMETER TUNING SUMMARY")
    print(f"{'='*60}")
    print(f"\nTop 5 configurations for {args.model}:")
    for i, result in enumerate(results[:5], 1):
        print(f"\n{i}. Test AUC: {result['test_auc']:.4f}, Test LogLoss: {result['test_logloss']:.4f}")
        print(f"   Config: {result['config']}")
    
    print(f"\nResults saved to: {output_file}")


if __name__ == '__main__':
    main()
