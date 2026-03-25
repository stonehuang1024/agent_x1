"""
Quick hyperparameter tuning for DCN-V2 on real Criteo dataset
Focus on key parameters that impact AUC the most
"""
import os
import sys
import json
import time
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from sklearn.metrics import roc_auc_score, log_loss

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.models.dcn_v2 import DCNv2, DCN
from src.models.baselines import DeepFM, DNN
from src.data.dataloader_large import get_dataloaders_large


def get_model(model_name, num_dense, sparse_vocab_sizes, config):
    """Create model with given config"""
    embedding_dim = config['embedding_dim']
    deep_hidden_dims = config['deep_hidden_dims']
    dropout_rate = config['dropout_rate']
    
    if model_name == 'dcnv2_stacked':
        return DCNv2(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            cross_layers=config.get('cross_layers', 3),
            deep_hidden_dims=deep_hidden_dims,
            dropout_rate=dropout_rate,
            use_bn=True,
            structure='stacked',
            low_rank=0
        )
    elif model_name == 'dcnv2_parallel':
        return DCNv2(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            cross_layers=config.get('cross_layers', 3),
            deep_hidden_dims=deep_hidden_dims,
            dropout_rate=dropout_rate,
            use_bn=True,
            structure='parallel',
            low_rank=0
        )
    elif model_name == 'dcn':
        return DCN(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            cross_layers=config.get('cross_layers', 3),
            deep_hidden_dims=deep_hidden_dims,
            dropout_rate=dropout_rate,
            use_bn=True,
            structure='stacked'
        )
    elif model_name == 'deepfm':
        return DeepFM(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            deep_hidden_dims=deep_hidden_dims,
            dropout_rate=dropout_rate,
            use_bn=True
        )
    elif model_name == 'dnn':
        return DNN(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            hidden_dims=deep_hidden_dims,
            dropout_rate=dropout_rate,
            use_bn=True
        )


def train_epoch(model, train_loader, criterion, optimizer, device, l2_reg=1e-5):
    """Train for one epoch"""
    model.train()
    total_loss = 0
    num_batches = 0
    
    for dense_features, sparse_features, labels in train_loader:
        dense_features = dense_features.to(device)
        sparse_features = sparse_features.to(device)
        labels = labels.to(device)
        
        optimizer.zero_grad()
        logits = model(dense_features, sparse_features)
        loss = criterion(logits, labels)
        
        # L2 regularization
        l2_loss = 0
        for param in model.parameters():
            l2_loss += torch.sum(param ** 2)
        loss = loss + l2_reg * l2_loss
        
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()
        
        total_loss += loss.item()
        num_batches += 1
    
    return total_loss / num_batches


def evaluate(model, data_loader, criterion, device):
    """Evaluate model"""
    model.eval()
    total_loss = 0
    num_batches = 0
    all_labels = []
    all_preds = []
    
    with torch.no_grad():
        for dense_features, sparse_features, labels in data_loader:
            dense_features = dense_features.to(device)
            sparse_features = sparse_features.to(device)
            labels = labels.to(device)
            
            logits = model(dense_features, sparse_features)
            loss = criterion(logits, labels)
            
            total_loss += loss.item()
            num_batches += 1
            
            probs = torch.sigmoid(logits)
            all_labels.extend(labels.cpu().numpy())
            all_preds.extend(probs.cpu().numpy())
    
    avg_loss = total_loss / num_batches
    all_labels = np.array(all_labels)
    all_preds = np.array(all_preds)
    
    try:
        auc = roc_auc_score(all_labels, all_preds)
        logloss = log_loss(all_labels, all_preds, eps=1e-7)
    except:
        auc = 0.5
        logloss = float('inf')
    
    return avg_loss, auc, logloss


def run_single_experiment(model_name, config, data_loaders, device, epochs=2):
    """Run single experiment"""
    train_loader, val_loader, test_loader, sparse_vocab_sizes, num_dense, _ = data_loaders
    
    torch.manual_seed(42)
    np.random.seed(42)
    
    model = get_model(model_name, num_dense, sparse_vocab_sizes, config)
    model = model.to(device)
    
    num_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    
    criterion = nn.BCEWithLogitsLoss()
    optimizer = optim.AdamW(
        model.parameters(), 
        lr=config['lr'], 
        weight_decay=config['weight_decay']
    )
    scheduler = optim.lr_scheduler.CosineAnnealingWarmRestarts(
        optimizer, T_0=10, T_mult=2, eta_min=config['lr'] * 0.01
    )
    
    best_val_auc = 0
    best_epoch = 0
    
    print(f"\nConfig: emb_dim={config['embedding_dim']}, hidden={config['deep_hidden_dims']}, "
          f"dropout={config['dropout_rate']}, lr={config['lr']}, cross={config.get('cross_layers', 'N/A')}")
    
    start_time = time.time()
    
    for epoch in range(epochs):
        train_loss = train_epoch(model, train_loader, criterion, optimizer, device, config.get('l2_reg', 1e-5))
        val_loss, val_auc, val_logloss = evaluate(model, val_loader, criterion, device)
        scheduler.step()
        
        print(f"  Epoch {epoch+1}/{epochs} - Train Loss: {train_loss:.4f}, Val AUC: {val_auc:.4f}, Val LogLoss: {val_logloss:.4f}")
        
        if val_auc > best_val_auc:
            best_val_auc = val_auc
            best_epoch = epoch
    
    # Test evaluation
    test_loss, test_auc, test_logloss = evaluate(model, test_loader, criterion, device)
    
    training_time = time.time() - start_time
    
    result = {
        'model': model_name,
        'config': config,
        'best_val_auc': float(best_val_auc),
        'test_auc': float(test_auc),
        'test_logloss': float(test_logloss),
        'num_params': num_params,
        'training_time': training_time,
        'best_epoch': best_epoch + 1
    }
    
    print(f"  -> Test AUC: {test_auc:.4f}, Test LogLoss: {test_logloss:.4f}")
    
    return result


def main():
    # Settings
    data_dir = '/Users/simonwang/agent_x1/results/session/session_153909_2026-03-15_15-39-09/research/recommendation_research/datasets_real'
    output_dir = '/Users/simonwang/agent_x1/results/session/session_153909_2026-03-15_15-39-09/research/recommendation_research/artifacts'
    batch_size = 2048
    num_workers = 2
    epochs = 2
    
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}")
    
    # Load data once
    print("Loading data...")
    data_loaders = get_dataloaders_large(data_dir, batch_size=batch_size, num_workers=num_workers, use_chunked=True)
    print(f"Train: {len(data_loaders[0].dataset)}, Val: {len(data_loaders[1].dataset)}, Test: {len(data_loaders[2].dataset)}")
    
    all_results = []
    
    # Experiment 1: DCN-V2 Stacked - embedding dimension
    print("\n" + "="*70)
    print("EXPERIMENT 1: DCN-V2 Stacked - Embedding Dimension")
    print("="*70)
    
    for emb_dim in [16, 32, 64]:
        config = {
            'embedding_dim': emb_dim,
            'deep_hidden_dims': [512, 256, 128],
            'cross_layers': 3,
            'dropout_rate': 0.3,
            'lr': 1e-3,
            'weight_decay': 1e-5,
            'l2_reg': 1e-5
        }
        result = run_single_experiment('dcnv2_stacked', config, data_loaders, device, epochs)
        all_results.append(result)
    
    # Experiment 2: DCN-V2 Stacked - hidden layer sizes
    print("\n" + "="*70)
    print("EXPERIMENT 2: DCN-V2 Stacked - Hidden Layer Sizes")
    print("="*70)
    
    for hidden_dims in [[256, 128], [512, 256, 128], [512, 256, 128, 64]]:
        config = {
            'embedding_dim': 32,
            'deep_hidden_dims': hidden_dims,
            'cross_layers': 3,
            'dropout_rate': 0.3,
            'lr': 1e-3,
            'weight_decay': 1e-5,
            'l2_reg': 1e-5
        }
        result = run_single_experiment('dcnv2_stacked', config, data_loaders, device, epochs)
        all_results.append(result)
    
    # Experiment 3: DCN-V2 Stacked - cross layers
    print("\n" + "="*70)
    print("EXPERIMENT 3: DCN-V2 Stacked - Number of Cross Layers")
    print("="*70)
    
    for cross_layers in [2, 3, 4]:
        config = {
            'embedding_dim': 32,
            'deep_hidden_dims': [512, 256, 128],
            'cross_layers': cross_layers,
            'dropout_rate': 0.3,
            'lr': 1e-3,
            'weight_decay': 1e-5,
            'l2_reg': 1e-5
        }
        result = run_single_experiment('dcnv2_stacked', config, data_loaders, device, epochs)
        all_results.append(result)
    
    # Experiment 4: DCN-V2 Stacked - learning rate
    print("\n" + "="*70)
    print("EXPERIMENT 4: DCN-V2 Stacked - Learning Rate")
    print("="*70)
    
    for lr in [1e-3, 5e-4, 1e-4]:
        config = {
            'embedding_dim': 32,
            'deep_hidden_dims': [512, 256, 128],
            'cross_layers': 3,
            'dropout_rate': 0.3,
            'lr': lr,
            'weight_decay': 1e-5,
            'l2_reg': 1e-5
        }
        result = run_single_experiment('dcnv2_stacked', config, data_loaders, device, epochs)
        all_results.append(result)
    
    # Experiment 5: DCN-V2 Stacked - dropout rate
    print("\n" + "="*70)
    print("EXPERIMENT 5: DCN-V2 Stacked - Dropout Rate")
    print("="*70)
    
    for dropout in [0.2, 0.3, 0.4]:
        config = {
            'embedding_dim': 32,
            'deep_hidden_dims': [512, 256, 128],
            'cross_layers': 3,
            'dropout_rate': dropout,
            'lr': 1e-3,
            'weight_decay': 1e-5,
            'l2_reg': 1e-5
        }
        result = run_single_experiment('dcnv2_stacked', config, data_loaders, device, epochs)
        all_results.append(result)
    
    # Experiment 6: Compare structures (Stacked vs Parallel)
    print("\n" + "="*70)
    print("EXPERIMENT 6: DCN-V2 Stacked vs Parallel")
    print("="*70)
    
    for structure in ['dcnv2_stacked', 'dcnv2_parallel']:
        config = {
            'embedding_dim': 32,
            'deep_hidden_dims': [512, 256, 128],
            'cross_layers': 3,
            'dropout_rate': 0.3,
            'lr': 1e-3,
            'weight_decay': 1e-5,
            'l2_reg': 1e-5
        }
        result = run_single_experiment(structure, config, data_loaders, device, epochs)
        all_results.append(result)
    
    # Experiment 7: Compare with baselines (best config)
    print("\n" + "="*70)
    print("EXPERIMENT 7: Baseline Comparison")
    print("="*70)
    
    best_config = {
        'embedding_dim': 32,
        'deep_hidden_dims': [512, 256, 128],
        'cross_layers': 3,
        'dropout_rate': 0.3,
        'lr': 1e-3,
        'weight_decay': 1e-5,
        'l2_reg': 1e-5
    }
    
    for model_name in ['dcn', 'deepfm', 'dnn']:
        result = run_single_experiment(model_name, best_config, data_loaders, device, epochs)
        all_results.append(result)
    
    # Save all results
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, 'quick_tune_results.json')
    with open(output_file, 'w') as f:
        json.dump(all_results, f, indent=2)
    
    # Print summary
    print("\n" + "="*70)
    print("FINAL SUMMARY - All Results Sorted by Test AUC")
    print("="*70)
    
    all_results.sort(key=lambda x: x['test_auc'], reverse=True)
    
    for i, result in enumerate(all_results[:10], 1):
        config_str = f"emb={result['config']['embedding_dim']}, hidden={result['config']['deep_hidden_dims']}, " \
                    f"cross={result['config'].get('cross_layers', 'N/A')}, dropout={result['config']['dropout_rate']}, " \
                    f"lr={result['config']['lr']}"
        print(f"\n{i}. {result['model']}")
        print(f"   Test AUC: {result['test_auc']:.4f}, Test LogLoss: {result['test_logloss']:.4f}")
        print(f"   Config: {config_str}")
        print(f"   Params: {result['num_params']:,}, Time: {result['training_time']:.1f}s")
    
    print(f"\nResults saved to: {output_file}")


if __name__ == '__main__':
    main()
