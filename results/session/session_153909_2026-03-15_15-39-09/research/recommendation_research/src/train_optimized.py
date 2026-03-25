"""
Optimized training script with hyperparameter tuning for DCN-V2 and baselines
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


def train(args):
    """Main training function"""
    # Set random seeds
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    
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
    print(f"Num dense features: {num_dense}")
    print(f"Num sparse features: {num_sparse}")
    
    # Model config
    config = {
        'cross_layers': args.cross_layers,
        'deep_hidden_dims': args.deep_hidden_dims,
        'dropout_rate': args.dropout_rate,
        'use_bn': args.use_bn,
        'low_rank': args.low_rank,
        'structure': args.structure
    }
    
    # Create model
    print(f"Creating model: {args.model}")
    model = get_model(args.model, num_dense, sparse_vocab_sizes, args.embedding_dim, config)
    model = model.to(device)
    
    # Count parameters
    num_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"Number of parameters: {num_params:,}")
    
    # Loss and optimizer
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([args.pos_weight]).to(device) if args.pos_weight != 1.0 else None)
    
    # Use AdamW for better regularization
    optimizer = optim.AdamW(
        model.parameters(), 
        lr=args.lr, 
        weight_decay=args.weight_decay,
        betas=(0.9, 0.999)
    )
    
    # Cosine annealing scheduler
    scheduler = optim.lr_scheduler.CosineAnnealingWarmRestarts(
        optimizer, T_0=10, T_mult=2, eta_min=args.lr * 0.01
    )
    
    # TensorBoard
    writer = SummaryWriter(os.path.join(args.log_dir, args.model + f"_{args.exp_name}"))
    
    # Training loop
    best_val_auc = 0
    best_epoch = 0
    patience_counter = 0
    
    print("\nStarting training...")
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
        
        # Log
        writer.add_scalar('Loss/train', train_loss, epoch)
        writer.add_scalar('Loss/val', val_loss, epoch)
        writer.add_scalar('AUC/val', val_auc, epoch)
        writer.add_scalar('LogLoss/val', val_logloss, epoch)
        writer.add_scalar('LR', optimizer.param_groups[0]['lr'], epoch)
        
        epoch_time = time.time() - epoch_start
        
        print(f"Epoch {epoch+1}/{args.epochs} [{epoch_time:.1f}s] - "
              f"Train Loss: {train_loss:.4f}, "
              f"Val Loss: {val_loss:.4f}, "
              f"Val AUC: {val_auc:.4f}, "
              f"Val LogLoss: {val_logloss:.4f}, "
              f"LR: {optimizer.param_groups[0]['lr']:.2e}")
        
        # Save best model
        if val_auc > best_val_auc:
            best_val_auc = val_auc
            best_epoch = epoch
            patience_counter = 0
            
            # Save checkpoint
            os.makedirs(args.save_dir, exist_ok=True)
            torch.save({
                'epoch': epoch,
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'val_auc': val_auc,
                'config': config,
                'args': vars(args)
            }, os.path.join(args.save_dir, f'{args.model}_{args.exp_name}_best.pt'))
        else:
            patience_counter += 1
        
        # Early stopping
        if patience_counter >= args.patience:
            print(f"Early stopping at epoch {epoch+1}")
            break
    
    training_time = time.time() - start_time
    print(f"\nTraining completed in {training_time:.2f}s")
    print(f"Best Val AUC: {best_val_auc:.4f} at epoch {best_epoch+1}")
    
    # Load best model and evaluate on test set
    print("\nEvaluating on test set...")
    checkpoint = torch.load(os.path.join(args.save_dir, f'{args.model}_{args.exp_name}_best.pt'))
    model.load_state_dict(checkpoint['model_state_dict'])
    
    test_loss, test_auc, test_logloss = evaluate(model, test_loader, criterion, device)
    print(f"Test Loss: {test_loss:.4f}")
    print(f"Test AUC: {test_auc:.4f}")
    print(f"Test LogLoss: {test_logloss:.4f}")
    
    # Save results
    results = {
        'model': args.model,
        'exp_name': args.exp_name,
        'best_val_auc': float(best_val_auc),
        'test_auc': float(test_auc),
        'test_logloss': float(test_logloss),
        'num_params': num_params,
        'training_time': training_time,
        'best_epoch': best_epoch + 1,
        'config': config,
        'args': vars(args)
    }
    
    os.makedirs(args.save_dir, exist_ok=True)
    with open(os.path.join(args.save_dir, f'{args.model}_{args.exp_name}_results.json'), 'w') as f:
        json.dump(results, f, indent=2)
    
    writer.close()
    
    return results


def main():
    parser = argparse.ArgumentParser(description='Train DCN-V2 and baselines with optimized hyperparameters')
    
    # Data
    parser.add_argument('--data_dir', type=str, 
                        default='/Users/simonwang/agent_x1/results/session/session_153909_2026-03-15_15-39-09/research/recommendation_research/datasets_real',
                        help='Data directory')
    parser.add_argument('--batch_size', type=int, default=2048, help='Batch size')
    parser.add_argument('--num_workers', type=int, default=2, help='Number of data loading workers')
    parser.add_argument('--max_batches_per_epoch', type=int, default=None, help='Max batches per epoch (for testing)')
    
    # Model
    parser.add_argument('--model', type=str, default='dcnv2_stacked',
                        choices=['dcnv2_stacked', 'dcnv2_parallel', 'dcn', 'deepfm', 'dnn', 'lr'],
                        help='Model name')
    parser.add_argument('--embedding_dim', type=int, default=16, help='Embedding dimension')
    parser.add_argument('--cross_layers', type=int, default=3, help='Number of cross layers')
    parser.add_argument('--deep_hidden_dims', type=int, nargs='+', default=[512, 256, 128],
                        help='Deep network hidden dimensions')
    parser.add_argument('--dropout_rate', type=float, default=0.3, help='Dropout rate')
    parser.add_argument('--use_bn', action='store_true', default=True, help='Use batch normalization')
    parser.add_argument('--low_rank', type=int, default=0, help='Low-rank approximation (0 for full-rank)')
    parser.add_argument('--structure', type=str, default='stacked', choices=['stacked', 'parallel'])
    
    # Training
    parser.add_argument('--epochs', type=int, default=2, help='Number of epochs')
    parser.add_argument('--lr', type=float, default=1e-3, help='Learning rate')
    parser.add_argument('--weight_decay', type=float, default=1e-5, help='Weight decay')
    parser.add_argument('--l2_reg', type=float, default=1e-5, help='L2 regularization')
    parser.add_argument('--pos_weight', type=float, default=1.0, help='Positive class weight for BCE loss')
    parser.add_argument('--patience', type=int, default=5, help='Early stopping patience')
    parser.add_argument('--seed', type=int, default=42, help='Random seed')
    
    # Experiment
    parser.add_argument('--exp_name', type=str, default='default', help='Experiment name')
    
    # Logging
    parser.add_argument('--save_dir', type=str, 
                        default='/Users/simonwang/agent_x1/results/session/session_153909_2026-03-15_15-39-09/research/recommendation_research/artifacts',
                        help='Directory to save models')
    parser.add_argument('--log_dir', type=str, 
                        default='/Users/simonwang/agent_x1/results/session/session_153909_2026-03-15_15-39-09/research/recommendation_research/runs',
                        help='Directory for tensorboard logs')
    
    global args
    args = parser.parse_args()
    
    train(args)


if __name__ == '__main__':
    main()
