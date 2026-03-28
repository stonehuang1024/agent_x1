"""
Training script for RankMixer and baseline models.
Final fixed version.
"""

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader
import numpy as np
from sklearn.metrics import roc_auc_score, log_loss
import os
import json
import time
from typing import Dict, List, Tuple, Optional

from rankmixer_fixed import (
    RankMixer, create_rankmixer_small, create_rankmixer_base, create_rankmixer_moe
)
from baselines_fixed import create_baseline_models
from data_loader import create_data_loaders


class EarlyStopping:
    """Early stopping to prevent overfitting"""
    def __init__(self, patience: int = 5, min_delta: float = 0.0001):
        self.patience = patience
        self.min_delta = min_delta
        self.counter = 0
        self.best_score = None
        self.early_stop = False
        
    def __call__(self, val_score: float) -> bool:
        if self.best_score is None:
            self.best_score = val_score
        elif val_score < self.best_score + self.min_delta:
            self.counter += 1
            if self.counter >= self.patience:
                self.early_stop = True
        else:
            self.best_score = val_score
            self.counter = 0
        return self.early_stop


def compute_metrics(predictions: np.ndarray, labels: np.ndarray) -> Dict[str, float]:
    """Compute evaluation metrics"""
    predictions = np.clip(predictions, 1e-7, 1 - 1e-7)
    
    try:
        auc = roc_auc_score(labels, predictions)
    except:
        auc = 0.5
    
    try:
        logloss = log_loss(labels, predictions)
    except:
        logloss = 10.0
    
    return {'auc': auc, 'logloss': logloss}


def prepare_input(batch, device, input_dim=624):
    """Prepare input features from batch"""
    if len(batch) == 3:  # Synthetic dataset
        cat_feats, num_feats, labels = batch
        batch_size = cat_feats.shape[0]
        features = torch.cat([
            cat_feats.float().view(batch_size, -1) / 1000,
            num_feats.view(batch_size, -1)
        ], dim=1).to(device)
        
        if features.shape[1] < input_dim:
            padding = torch.zeros(batch_size, input_dim - features.shape[1], device=device)
            features = torch.cat([features, padding], dim=1)
        elif features.shape[1] > input_dim:
            features = features[:, :input_dim]
            
        labels = labels.to(device)
    else:
        features, labels = batch
        features = features.to(device)
        labels = labels.to(device)
        
        if features.dim() == 2 and features.shape[1] != input_dim:
            batch_size = features.shape[0]
            if features.shape[1] < input_dim:
                padding = torch.zeros(batch_size, input_dim - features.shape[1], device=device)
                features = torch.cat([features, padding], dim=1)
            else:
                features = features[:, :input_dim]
    
    return features, labels


def train_epoch(model, train_loader, optimizer, criterion, device, use_moe=False, input_dim=624):
    """Train for one epoch"""
    model.train()
    total_loss = 0.0
    all_predictions = []
    all_labels = []
    
    for batch_idx, batch in enumerate(train_loader):
        features, labels = prepare_input(batch, device, input_dim)
        
        optimizer.zero_grad()
        
        # Forward pass - handle both tuple and tensor outputs
        output = model(features)
        if isinstance(output, tuple):
            predictions, aux_loss = output
        else:
            predictions = output
            aux_loss = None
            
        predictions = torch.sigmoid(predictions)
        
        if use_moe and aux_loss is not None:
            loss = criterion(predictions, labels) + aux_loss
        else:
            loss = criterion(predictions, labels)
        
        # Backward pass
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        
        total_loss += loss.item()
        all_predictions.extend(predictions.detach().cpu().numpy())
        all_labels.extend(labels.detach().cpu().numpy())
        
        if batch_idx % 50 == 0:
            print(f"  Batch {batch_idx}/{len(train_loader)}, Loss: {loss.item():.4f}")
    
    all_predictions = np.array(all_predictions).flatten()
    all_labels = np.array(all_labels).flatten()
    metrics = compute_metrics(all_predictions, all_labels)
    metrics['loss'] = total_loss / len(train_loader)
    
    return metrics


def evaluate(model, data_loader, criterion, device, use_moe=False, input_dim=624):
    """Evaluate model"""
    model.eval()
    total_loss = 0.0
    all_predictions = []
    all_labels = []
    
    with torch.no_grad():
        for batch in data_loader:
            features, labels = prepare_input(batch, device, input_dim)
            
            output = model(features)
            if isinstance(output, tuple):
                predictions, aux_loss = output
            else:
                predictions = output
                aux_loss = None
                
            predictions = torch.sigmoid(predictions)
            
            if use_moe and aux_loss is not None:
                loss = criterion(predictions, labels) + aux_loss
            else:
                loss = criterion(predictions, labels)
            
            total_loss += loss.item()
            all_predictions.extend(predictions.cpu().numpy())
            all_labels.extend(labels.cpu().numpy())
    
    all_predictions = np.array(all_predictions).flatten()
    all_labels = np.array(all_labels).flatten()
    metrics = compute_metrics(all_predictions, all_labels)
    metrics['loss'] = total_loss / len(data_loader)
    
    return metrics


def train_model(model, train_loader, val_loader, test_loader, config, device):
    """Complete training loop"""
    model_name = config['model_name']
    num_epochs = config['num_epochs']
    learning_rate = config['learning_rate']
    use_moe = config.get('use_moe', False)
    input_dim = config.get('input_dim', 624)
    
    print(f"\n{'='*60}")
    print(f"Training {model_name}")
    print(f"Parameters: {sum(p.numel() for p in model.parameters()):,}")
    print(f"Device: {device}")
    print(f"{'='*60}")
    
    criterion = nn.BCELoss()
    optimizer = optim.Adam(model.parameters(), lr=learning_rate, weight_decay=1e-5)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='max', 
                                                      factor=0.5, patience=3, verbose=True)
    early_stopping = EarlyStopping(patience=config.get('patience', 5))
    
    model = model.to(device)
    
    history = {
        'model_name': model_name,
        'num_parameters': sum(p.numel() for p in model.parameters()),
        'train_metrics': [],
        'val_metrics': [],
        'test_metrics': None,
        'training_time': 0.0
    }
    
    best_val_auc = 0.0
    start_time = time.time()
    
    for epoch in range(num_epochs):
        print(f"\nEpoch {epoch+1}/{num_epochs}")
        print("-" * 40)
        
        train_metrics = train_epoch(model, train_loader, optimizer, criterion, device, use_moe, input_dim)
        print(f"Train - Loss: {train_metrics['loss']:.4f}, AUC: {train_metrics['auc']:.4f}")
        
        val_metrics = evaluate(model, val_loader, criterion, device, use_moe, input_dim)
        print(f"Val   - Loss: {val_metrics['loss']:.4f}, AUC: {val_metrics['auc']:.4f}")
        
        scheduler.step(val_metrics['auc'])
        
        history['train_metrics'].append(train_metrics)
        history['val_metrics'].append(val_metrics)
        
        if val_metrics['auc'] > best_val_auc:
            best_val_auc = val_metrics['auc']
            model_path = os.path.join(config['save_dir'], f"{model_name}_best.pt")
            torch.save(model.state_dict(), model_path)
            print(f"  -> Saved best model (Val AUC: {best_val_auc:.4f})")
        
        if early_stopping(val_metrics['auc']):
            print(f"\nEarly stopping triggered at epoch {epoch+1}")
            break
    
    training_time = time.time() - start_time
    history['training_time'] = training_time
    
    model_path = os.path.join(config['save_dir'], f"{model_name}_best.pt")
    if os.path.exists(model_path):
        model.load_state_dict(torch.load(model_path))
    
    test_metrics = evaluate(model, test_loader, criterion, device, use_moe, input_dim)
    history['test_metrics'] = test_metrics
    
    print(f"\n{'='*60}")
    print(f"Training completed in {training_time:.1f}s")
    print(f"Test  - AUC: {test_metrics['auc']:.4f}, LogLoss: {test_metrics['logloss']:.4f}")
    print(f"{'='*60}")
    
    return history


def run_experiment(config):
    """Run complete experiment with all models"""
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}")
    
    os.makedirs(config['save_dir'], exist_ok=True)
    
    print("\n" + "="*60)
    print("Loading Data")
    print("="*60)
    train_loader, val_loader, test_loader, feature_info = create_data_loaders(
        dataset_name=config['dataset'],
        batch_size=config['batch_size'],
        num_samples=config.get('num_samples', 100000)
    )
    
    input_dim = 624
    
    models = {}
    
    if 'rankmixer_small' in config['models']:
        models['RankMixer-Small'] = create_rankmixer_small(input_dim, num_tasks=1)
    if 'rankmixer_base' in config['models']:
        models['RankMixer-Base'] = create_rankmixer_base(input_dim, num_tasks=1)
    if 'rankmixer_moe' in config['models']:
        models['RankMixer-MoE'] = create_rankmixer_moe(input_dim, num_tasks=1)
    
    if 'baselines' in config['models']:
        baseline_models = create_baseline_models(input_dim, num_tasks=1)
        models.update(baseline_models)
    
    all_histories = []
    
    for model_name, model in models.items():
        model_config = config.copy()
        model_config['model_name'] = model_name
        model_config['use_moe'] = 'MoE' in model_name and 'RankMixer' in model_name
        model_config['input_dim'] = input_dim
        
        try:
            history = train_model(model, train_loader, val_loader, test_loader, 
                                 model_config, device)
            all_histories.append(history)
        except Exception as e:
            print(f"Error training {model_name}: {e}")
            import traceback
            traceback.print_exc()
    
    results_path = os.path.join(config['save_dir'], 'results.json')
    with open(results_path, 'w') as f:
        json.dump(all_histories, f, indent=2)
    
    print("\n" + "="*80)
    print("EXPERIMENT SUMMARY")
    print("="*80)
    print(f"{'Model':<25} {'Params':<12} {'Test AUC':<12} {'Test LogLoss':<12} {'Time (s)':<10}")
    print("-"*80)
    
    for history in all_histories:
        print(f"{history['model_name']:<25} "
              f"{history['num_parameters']:<12,} "
              f"{history['test_metrics']['auc']:<12.4f} "
              f"{history['test_metrics']['logloss']:<12.4f} "
              f"{history['training_time']:<10.1f}")
    
    return all_histories


if __name__ == "__main__":
    config = {
        'dataset': 'synthetic',
        'num_samples': 50000,
        'batch_size': 256,
        'num_epochs': 15,
        'learning_rate': 0.001,
        'save_dir': 'experiments/results',
        'models': ['rankmixer_small', 'rankmixer_base', 'baselines'],
        'patience': 5
    }
    
    run_experiment(config)
