"""
Training script for RankMixer and baseline models
"""

import os
import sys
import time
import json
import argparse
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.tensorboard import SummaryWriter
from sklearn.metrics import roc_auc_score, log_loss
from tqdm import tqdm

from rankmixer_model import RankMixer, DeepFM, DCNv2
from data_loader import get_data_loader


def calculate_metrics(predictions, labels):
    """Calculate AUC and LogLoss"""
    predictions = predictions.cpu().numpy().flatten()
    labels = labels.cpu().numpy().flatten()
    
    # Handle edge cases
    if len(np.unique(labels)) < 2:
        return 0.5, 1.0
    
    auc = roc_auc_score(labels, predictions)
    logloss = log_loss(labels, np.clip(predictions, 1e-7, 1-1e-7))
    
    return auc, logloss


def train_epoch(model, train_loader, optimizer, criterion, device, use_moe=False):
    """Train for one epoch"""
    model.train()
    total_loss = 0
    all_predictions = []
    all_labels = []
    total_aux_loss = 0
    
    pbar = tqdm(train_loader, desc='Training')
    for features, labels in pbar:
        features = features.to(device)
        labels = labels.to(device)
        
        optimizer.zero_grad()
        
        # Forward pass
        outputs = model(features, training=True)
        
        # Get predictions
        if hasattr(outputs, 'aux_loss'):
            predictions = outputs
            aux_loss = outputs.aux_loss
        else:
            predictions = outputs
            aux_loss = 0
        
        # Calculate loss
        loss = criterion(predictions, labels)
        
        # Add auxiliary loss for MoE
        if use_moe and aux_loss != 0:
            loss = loss + aux_loss
            total_aux_loss += aux_loss.item()
        
        # Backward pass
        loss.backward()
        optimizer.step()
        
        total_loss += loss.item()
        all_predictions.append(predictions.detach())
        all_labels.append(labels)
        
        pbar.set_postfix({'loss': loss.item()})
    
    # Calculate metrics
    all_predictions = torch.cat(all_predictions)
    all_labels = torch.cat(all_labels)
    auc, logloss = calculate_metrics(all_predictions, all_labels)
    
    avg_loss = total_loss / len(train_loader)
    avg_aux_loss = total_aux_loss / len(train_loader) if use_moe else 0
    
    return avg_loss, auc, logloss, avg_aux_loss


def evaluate(model, val_loader, criterion, device):
    """Evaluate model"""
    model.eval()
    total_loss = 0
    all_predictions = []
    all_labels = []
    
    with torch.no_grad():
        for features, labels in tqdm(val_loader, desc='Evaluating'):
            features = features.to(device)
            labels = labels.to(device)
            
            outputs = model(features, training=False)
            
            if hasattr(outputs, 'aux_loss'):
                predictions = outputs
            else:
                predictions = outputs
            
            loss = criterion(predictions, labels)
            total_loss += loss.item()
            
            all_predictions.append(predictions)
            all_labels.append(labels)
    
    all_predictions = torch.cat(all_predictions)
    all_labels = torch.cat(all_labels)
    auc, logloss = calculate_metrics(all_predictions, all_labels)
    
    avg_loss = total_loss / len(val_loader)
    
    return avg_loss, auc, logloss


def count_parameters(model):
    """Count trainable parameters"""
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


def train_model(args):
    """Main training function"""
    
    # Set random seed
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    
    # Device
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}")
    
    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)
    
    # Load data
    print(f"\nLoading {args.dataset} dataset...")
    train_loader, feature_dims = get_data_loader(
        args.dataset, args.data_path, args.batch_size, 'train',
        n_samples=args.n_samples, n_features=args.n_features
    )
    val_loader, _ = get_data_loader(
        args.dataset, args.data_path, args.batch_size, 'test',
        n_samples=args.n_samples // 4, n_features=args.n_features
    )
    
    print(f"Feature dimensions: {feature_dims}")
    print(f"Number of features: {len(feature_dims)}")
    
    # Create model
    print(f"\nCreating {args.model} model...")
    
    if args.model == 'rankmixer':
        model = RankMixer(
            feature_dims=feature_dims,
            num_tokens=args.num_tokens,
            hidden_dim=args.hidden_dim,
            num_layers=args.num_layers,
            num_heads=args.num_heads,
            ffn_ratio=args.ffn_ratio,
            use_moe=args.use_moe,
            num_experts=args.num_experts,
            moe_top_k=args.moe_top_k,
            dropout=args.dropout,
            num_tasks=1
        )
    elif args.model == 'deepfm':
        model = DeepFM(
            feature_dims=feature_dims,
            embed_dim=args.hidden_dim // 2,
            mlp_dims=[args.hidden_dim, args.hidden_dim // 2],
            dropout=args.dropout
        )
    elif args.model == 'dcnv2':
        model = DCNv2(
            feature_dims=feature_dims,
            embed_dim=args.hidden_dim // 2,
            num_cross_layers=args.num_layers,
            mlp_dims=[args.hidden_dim, args.hidden_dim // 2],
            dropout=args.dropout
        )
    else:
        raise ValueError(f"Unknown model: {args.model}")
    
    model = model.to(device)
    
    num_params = count_parameters(model)
    print(f"Model parameters: {num_params:,}")
    
    # Loss and optimizer
    criterion = nn.BCELoss()
    
    if args.optimizer == 'adam':
        optimizer = optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    elif args.optimizer == 'adamw':
        optimizer = optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    elif args.optimizer == 'rmsprop':
        optimizer = optim.RMSprop(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    else:
        raise ValueError(f"Unknown optimizer: {args.optimizer}")
    
    # Learning rate scheduler
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode='max', factor=0.5, patience=3, verbose=True
    )
    
    # Tensorboard
    writer = SummaryWriter(os.path.join(args.output_dir, 'logs'))
    
    # Training loop
    best_auc = 0
    best_epoch = 0
    history = []
    
    print(f"\nStarting training for {args.epochs} epochs...")
    start_time = time.time()
    
    for epoch in range(args.epochs):
        epoch_start = time.time()
        
        # Train
        train_loss, train_auc, train_logloss, train_aux_loss = train_epoch(
            model, train_loader, optimizer, criterion, device, args.use_moe
        )
        
        # Evaluate
        val_loss, val_auc, val_logloss = evaluate(model, val_loader, criterion, device)
        
        # Update learning rate
        scheduler.step(val_auc)
        
        epoch_time = time.time() - epoch_start
        
        # Log
        print(f"\nEpoch {epoch+1}/{args.epochs} ({epoch_time:.1f}s)")
        print(f"  Train - Loss: {train_loss:.4f}, AUC: {train_auc:.4f}, LogLoss: {train_logloss:.4f}")
        if args.use_moe:
            print(f"  Train - Aux Loss: {train_aux_loss:.4f}")
        print(f"  Val   - Loss: {val_loss:.4f}, AUC: {val_auc:.4f}, LogLoss: {val_logloss:.4f}")
        
        # Tensorboard
        writer.add_scalar('Loss/train', train_loss, epoch)
        writer.add_scalar('Loss/val', val_loss, epoch)
        writer.add_scalar('AUC/train', train_auc, epoch)
        writer.add_scalar('AUC/val', val_auc, epoch)
        writer.add_scalar('LogLoss/train', train_logloss, epoch)
        writer.add_scalar('LogLoss/val', val_logloss, epoch)
        
        # Save history
        history.append({
            'epoch': epoch + 1,
            'train_loss': train_loss,
            'train_auc': train_auc,
            'train_logloss': train_logloss,
            'val_loss': val_loss,
            'val_auc': val_auc,
            'val_logloss': val_logloss,
            'epoch_time': epoch_time
        })
        
        # Save best model
        if val_auc > best_auc:
            best_auc = val_auc
            best_epoch = epoch + 1
            torch.save({
                'epoch': epoch,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'val_auc': val_auc,
                'args': vars(args)
            }, os.path.join(args.output_dir, 'best_model.pt'))
            print(f"  -> Saved best model (AUC: {val_auc:.4f})")
    
    total_time = time.time() - start_time
    
    # Save final results
    results = {
        'model': args.model,
        'num_parameters': num_params,
        'best_epoch': best_epoch,
        'best_val_auc': best_auc,
        'total_training_time': total_time,
        'history': history,
        'args': vars(args)
    }
    
    with open(os.path.join(args.output_dir, 'results.json'), 'w') as f:
        json.dump(results, f, indent=2)
    
    print(f"\n{'='*60}")
    print(f"Training completed!")
    print(f"Best validation AUC: {best_auc:.4f} (epoch {best_epoch})")
    print(f"Total training time: {total_time/60:.1f} minutes")
    print(f"Results saved to: {args.output_dir}")
    print(f"{'='*60}")
    
    writer.close()
    
    return results


def main():
    parser = argparse.ArgumentParser(description='Train RankMixer and baseline models')
    
    # Data arguments
    parser.add_argument('--dataset', type=str, default='synthetic',
                       choices=['criteo', 'avazu', 'movielens', 'synthetic'],
                       help='Dataset to use')
    parser.add_argument('--data_path', type=str, default='./data',
                       help='Path to dataset')
    parser.add_argument('--n_samples', type=int, default=50000,
                       help='Number of samples for synthetic dataset')
    parser.add_argument('--n_features', type=int, default=39,
                       help='Number of features for synthetic dataset')
    
    # Model arguments
    parser.add_argument('--model', type=str, default='rankmixer',
                       choices=['rankmixer', 'deepfm', 'dcnv2'],
                       help='Model architecture')
    parser.add_argument('--hidden_dim', type=int, default=128,
                       help='Hidden dimension')
    parser.add_argument('--num_layers', type=int, default=2,
                       help='Number of layers')
    parser.add_argument('--num_tokens', type=int, default=16,
                       help='Number of tokens for RankMixer')
    parser.add_argument('--num_heads', type=int, default=16,
                       help='Number of heads for token mixing')
    parser.add_argument('--ffn_ratio', type=int, default=4,
                       help='FFN expansion ratio')
    parser.add_argument('--dropout', type=float, default=0.1,
                       help='Dropout rate')
    
    # MoE arguments
    parser.add_argument('--use_moe', action='store_true',
                       help='Use Sparse MoE')
    parser.add_argument('--num_experts', type=int, default=4,
                       help='Number of experts per token')
    parser.add_argument('--moe_top_k', type=int, default=2,
                       help='Top-k experts to activate')
    
    # Training arguments
    parser.add_argument('--epochs', type=int, default=20,
                       help='Number of epochs')
    parser.add_argument('--batch_size', type=int, default=256,
                       help='Batch size')
    parser.add_argument('--lr', type=float, default=0.001,
                       help='Learning rate')
    parser.add_argument('--weight_decay', type=float, default=1e-5,
                       help='Weight decay')
    parser.add_argument('--optimizer', type=str, default='adam',
                       choices=['adam', 'adamw', 'rmsprop'],
                       help='Optimizer')
    
    # Other arguments
    parser.add_argument('--seed', type=int, default=42,
                       help='Random seed')
    parser.add_argument('--output_dir', type=str, default='./output',
                       help='Output directory')
    
    args = parser.parse_args()
    
    # Create output directory with model name
    args.output_dir = os.path.join(args.output_dir, f"{args.model}_{args.dataset}")
    
    train_model(args)


if __name__ == "__main__":
    main()
