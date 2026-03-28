"""
Training script for RankMixer and baseline models.
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
import argparse

from rankmixer import (
    RankMixer, create_rankmixer_small, create_rankmixer_base, create_rankmixer_moe
)
from baselines import create_baseline_models
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
    # Clip predictions to avoid log(0)
    predictions = np.clip(predictions, 1e-7, 1 - 1e-7)
    
    auc = roc_auc_score(labels, predictions)
    logloss = log_loss(labels, predictions)
    
    return {
        'auc': auc,
        'logloss': logloss
    }


def train_epoch(model: nn.Module, train_loader: DataLoader, optimizer: optim.Optimizer,
                criterion: nn.Module, device: torch.device, use_moe: bool = False) -> Dict[str, float]:
    """Train for one epoch"""
    model.train()
    total_loss = 0.0
    all_predictions = []
    all_labels = []
    
    for batch_idx, batch in enumerate(train_loader):
        if len(batch) == 3:  # Synthetic dataset with separate cat/num features
            cat_feats, num_feats, labels = batch
            # Combine features for simplicity
            features = torch.cat([
                cat_feats.float() / 1000,  # Normalize
                num_feats
            ], dim=1).to(device)
            labels = labels.to(device)
        else:  # Other datasets
            features, labels = batch
            features = features.to(device)
            labels = labels.to(device)
        
        optimizer.zero_grad()
        
        # Forward pass
        if use_moe:
            predictions, aux_loss = model(features)
            predictions = torch.sigmoid(predictions)
            loss = criterion(predictions, labels) + aux_loss
        else:
            predictions = model(features)
            predictions = torch.sigmoid(predictions)
            loss = criterion(predictions, labels)
        
        # Backward pass
        loss.backward()
        optimizer.step()
        
        total_loss += loss.item()
        all_predictions.extend(predictions.detach().cpu().numpy())
        all_labels.extend(labels.detach().cpu().numpy())
        
        if batch_idx % 100 == 0:
            print(f"  Batch {batch_idx}/{len(train_loader)}, Loss: {loss.item():.4f}")
    
    # Compute metrics
    all_predictions = np.array(all_predictions).flatten()
    all_labels = np.array(all_labels).flatten()
    metrics = compute_metrics(all_predictions, all_labels)
    metrics['loss'] = total_loss / len(train_loader)
    
    return metrics


def evaluate(model: nn.Module, data_loader: DataLoader, criterion: nn.Module,
             device: torch.device, use_moe: bool = False) -> Dict[str, float]:
    """Evaluate model"""
    model.eval()
    total_loss = 0.0
    all_predictions = []
    all_labels = []
    
    with torch.no_grad():
        for batch in data_loader:
            if len(batch) == 3:
                cat_feats, num_feats, labels = batch
                features = torch.cat([
                    cat_feats.float() / 1000,
                    num_feats
                ], dim=1).to(device)
                labels = labels.to(device)
            else:
                features, labels = batch
                features = features.to(device)
                labels = labels.to(device)
            
            if use_moe:
                predictions, aux_loss = model(features)
                predictions = torch.sigmoid(predictions)
                loss = criterion(predictions, labels) + aux_loss
            else:
                predictions = model(features)
                predictions = torch.sigmoid(predictions)
                loss = criterion(predictions, labels)
            
            total_loss += loss.item()
            all_predictions.extend(predictions.cpu().numpy())
            all_labels.extend(labels.cpu().numpy())
    
    all_predictions = np.array(all_predictions).flatten()
    all_labels = np.array(all_labels).flatten()
    metrics = compute_metrics(all_predictions, all_labels)
    metrics['loss'] = total_loss / len(data_loader)
    
    return metrics


def train_model(model: nn.Module, train_loader: DataLoader, val_loader: DataLoader,
                test_loader: DataLoader, config: Dict, device: torch.device) -> Dict:
    """
    Complete training loop.
    
    Args:
        model: Model to train
        train_loader: Training data loader
        val_loader: Validation data loader
        test_loader: Test data loader
        config: Training configuration
        device: Device to train on
    
    Returns:
        Training history
    """
    model_name = config['model_name']
    num_epochs = config['num_epochs']
    learning_rate = config['learning_rate']
    use_moe = config.get('use_moe', False)
    
    print(f"\n{'='*60}")
    print(f"Training {model_name}")
    print(f"Parameters: {sum(p.numel() for p in model.parameters()):,}")
    print(f"Device: {device}")
    print(f"{'='*60}")
    
    # Setup
    criterion = nn.BCELoss()
    optimizer = optim.Adam(model.parameters(), lr=learning_rate, weight_decay=1e-5)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='max', 
                                                      factor=0.5, patience=3, verbose=True)
    early_stopping = EarlyStopping(patience=config.get('patience', 5))
    
    model = model.to(device)
    
    # Training history
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
        
        # Train
        train_metrics = train_epoch(model, train_loader, optimizer, criterion, device, use_moe)
        print(f"Train - Loss: {train_metrics['loss']:.4f}, AUC: {train_metrics['auc']:.4f}, "
              f"LogLoss: {train_metrics['logloss']:.4f}")
        
        # Validate
        val_metrics = evaluate(model, val_loader, criterion, device, use_moe)
        print(f"Val   - Loss: {val_metrics['loss']:.4f}, AUC: {val_metrics['auc']:.4f}, "
              f"LogLoss: {val_metrics['logloss']:.4f}")
        
        # Update scheduler
        scheduler.step(val_metrics['auc'])
        
        # Save history
        history['train_metrics'].append(train_metrics)
        history['val_metrics'].append(val_metrics)
        
        # Save best model
        if val_metrics['auc'] > best_val_auc:
            best_val_auc = val_metrics['auc']
            model_path = os.path.join(config['save_dir'], f"{model_name}_best.pt")
            torch.save(model.state_dict(), model_path)
            print(f"  -> Saved best model (Val AUC: {best_val_auc:.4f})")
        
        # Early stopping
        if early_stopping(val_metrics['auc']):
            print(f"\nEarly stopping triggered at epoch {epoch+1}")
            break
    
    training_time = time.time() - start_time
    history['training_time'] = training_time
    
    # Load best model and evaluate on test set
    model_path = os.path.join(config['save_dir'], f"{model_name}_best.pt")
    if os.path.exists(model_path):
        model.load_state_dict(torch.load(model_path))
    
    test_metrics = evaluate(model, test_loader, criterion, device, use_moe)
    history['test_metrics'] = test_metrics
    
    print(f"\n{'='*60}")
    print(f"Training completed in {training_time:.1f}s")
    print(f"Test  - AUC: {test_metrics['auc']:.4f}, LogLoss: {test_metrics['logloss']:.4f}")
    print(f"{'='*60}")
    
    return history


def run_experiment(config: Dict) -> List[Dict]:
    """
    Run complete experiment with all models.
    
    Args:
        config: Experiment configuration
    
    Returns:
        List of training histories for all models
    """
    # Set device
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}")
    
    # Create save directory
    os.makedirs(config['save_dir'], exist_ok=True)
    
    # Load data
    print("\n" + "="*60)
    print("Loading Data")
    print("="*60)
    train_loader, val_loader, test_loader, feature_info = create_data_loaders(
        dataset_name=config['dataset'],
        batch_size=config['batch_size'],
        num_samples=config.get('num_samples', 100000)
    )
    
    # Create models
    num_features = feature_info['num_categorical'] + feature_info['num_numerical']
    
    models = {}
    
    # RankMixer variants
    if 'rankmixer_small' in config['models']:
        models['RankMixer-Small'] = create_rankmixer_small(num_features, num_tasks=1)
    if 'rankmixer_base' in config['models']:
        models['RankMixer-Base'] = create_rankmixer_base(num_features, num_tasks=1)
    if 'rankmixer_moe' in config['models']:
        models['RankMixer-MoE'] = create_rankmixer_moe(num_features, num_tasks=1)
    
    # Baselines
    if 'baselines' in config['models']:
        baseline_models = create_baseline_models(num_features, num_tasks=1)
        models.update(baseline_models)
    
    # Train all models
    all_histories = []
    
    for model_name, model in models.items():
        model_config = config.copy()
        model_config['model_name'] = model_name
        model_config['use_moe'] = 'MoE' in model_name and 'RankMixer' in model_name
        
        try:
            history = train_model(model, train_loader, val_loader, test_loader, 
                                 model_config, device)
            all_histories.append(history)
        except Exception as e:
            print(f"Error training {model_name}: {e}")
            import traceback
            traceback.print_exc()
    
    # Save results
    results_path = os.path.join(config['save_dir'], 'results.json')
    with open(results_path, 'w') as f:
        json.dump(all_histories, f, indent=2)
    
    # Print summary
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


def main():
    parser = argparse.ArgumentParser(description='Train RankMixer and baselines')
    parser.add_argument('--dataset', type=str, default='synthetic', 
                       choices=['synthetic', 'criteo', 'avazu'])
    parser.add_argument('--num_samples', type=int, default=100000)
    parser.add_argument('--batch_size', type=int, default=256)
    parser.add_argument('--num_epochs', type=int, default=20)
    parser.add_argument('--learning_rate', type=float, default=0.001)
    parser.add_argument('--save_dir', type=str, default='../experiments/results')
    parser.add_argument('--models', nargs='+', 
                       default=['rankmixer_small', 'rankmixer_base', 'baselines'])
    
    args = parser.parse_args()
    
    config = {
        'dataset': args.dataset,
        'num_samples': args.num_samples,
        'batch_size': args.batch_size,
        'num_epochs': args.num_epochs,
        'learning_rate': args.learning_rate,
        'save_dir': args.save_dir,
        'models': args.models,
        'patience': 5
    }
    
    run_experiment(config)


if __name__ == "__main__":
    main()
