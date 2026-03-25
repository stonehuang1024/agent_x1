"""
Evaluation script for comparing all models
"""
import os
import sys
import json
import argparse
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import roc_auc_score, log_loss, accuracy_score, precision_score, recall_score, f1_score
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.models.dcn_v2 import DCNv2, DCN
from src.models.baselines import DeepFM, DNN, LogisticRegression
from src.data.dataloader import get_dataloaders


def get_model(model_name, num_dense, sparse_vocab_sizes, embedding_dim, config):
    """Create model based on name"""
    if model_name == 'dcnv2_stacked':
        return DCNv2(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            cross_layers=config.get('cross_layers', 2),
            deep_hidden_dims=config.get('deep_hidden_dims', [256, 128]),
            dropout_rate=config.get('dropout_rate', 0.2),
            use_bn=config.get('use_bn', True),
            structure='stacked',
            low_rank=config.get('low_rank', 0)
        )
    elif model_name == 'dcnv2_parallel':
        return DCNv2(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            cross_layers=config.get('cross_layers', 2),
            deep_hidden_dims=config.get('deep_hidden_dims', [256, 128]),
            dropout_rate=config.get('dropout_rate', 0.2),
            use_bn=config.get('use_bn', True),
            structure='parallel',
            low_rank=config.get('low_rank', 0)
        )
    elif model_name == 'dcn':
        return DCN(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            cross_layers=config.get('cross_layers', 2),
            deep_hidden_dims=config.get('deep_hidden_dims', [256, 128]),
            dropout_rate=config.get('dropout_rate', 0.2),
            use_bn=config.get('use_bn', True),
            structure=config.get('structure', 'stacked')
        )
    elif model_name == 'deepfm':
        return DeepFM(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            deep_hidden_dims=config.get('deep_hidden_dims', [256, 128]),
            dropout_rate=config.get('dropout_rate', 0.2),
            use_bn=config.get('use_bn', True)
        )
    elif model_name == 'dnn':
        return DNN(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            hidden_dims=config.get('deep_hidden_dims', [256, 128, 64]),
            dropout_rate=config.get('dropout_rate', 0.2),
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


def evaluate_model(model, data_loader, device):
    """Evaluate model and return predictions"""
    model.eval()
    
    all_labels = []
    all_preds = []
    all_probs = []
    
    with torch.no_grad():
        for dense_features, sparse_features, labels in data_loader:
            dense_features = dense_features.to(device)
            sparse_features = sparse_features.to(device)
            
            logits = model(dense_features, sparse_features)
            probs = torch.sigmoid(logits)
            preds = (probs > 0.5).float()
            
            all_labels.extend(labels.numpy())
            all_preds.extend(preds.cpu().numpy())
            all_probs.extend(probs.cpu().numpy())
    
    all_labels = np.array(all_labels)
    all_preds = np.array(all_preds)
    all_probs = np.array(all_probs)
    
    # Calculate metrics
    metrics = {
        'auc': roc_auc_score(all_labels, all_probs),
        'logloss': log_loss(all_labels, all_probs),
        'accuracy': accuracy_score(all_labels, all_preds),
        'precision': precision_score(all_labels, all_preds, zero_division=0),
        'recall': recall_score(all_labels, all_preds, zero_division=0),
        'f1': f1_score(all_labels, all_preds, zero_division=0)
    }
    
    return metrics


def compare_models(args):
    """Compare all trained models"""
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}")
    
    # Load data
    print("Loading data...")
    train_loader, val_loader, test_loader, sparse_vocab_sizes, num_dense, num_sparse = get_dataloaders(
        args.data_dir,
        batch_size=args.batch_size,
        num_workers=0
    )
    
    # Find all model results
    models_to_eval = []
    for filename in os.listdir(args.save_dir):
        if filename.endswith('_results.json'):
            model_name = filename.replace('_results.json', '')
            result_path = os.path.join(args.save_dir, filename)
            checkpoint_path = os.path.join(args.save_dir, f'{model_name}_best.pt')
            
            if os.path.exists(checkpoint_path):
                with open(result_path, 'r') as f:
                    results = json.load(f)
                models_to_eval.append((model_name, results, checkpoint_path))
    
    print(f"\nFound {len(models_to_eval)} models to evaluate")
    
    # Evaluate each model
    all_results = []
    
    for model_name, train_results, checkpoint_path in models_to_eval:
        print(f"\n{'='*50}")
        print(f"Evaluating: {model_name}")
        print(f"{'='*50}")
        
        # Load config
        config = train_results.get('config', {})
        
        # Create model
        model = get_model(model_name, num_dense, sparse_vocab_sizes, args.embedding_dim, config)
        model = model.to(device)
        
        # Load checkpoint
        checkpoint = torch.load(checkpoint_path, map_location=device)
        model.load_state_dict(checkpoint['model_state_dict'])
        
        # Evaluate on test set
        test_metrics = evaluate_model(model, test_loader, device)
        
        print(f"Test AUC: {test_metrics['auc']:.4f}")
        print(f"Test LogLoss: {test_metrics['logloss']:.4f}")
        print(f"Test Accuracy: {test_metrics['accuracy']:.4f}")
        print(f"Test F1: {test_metrics['f1']:.4f}")
        
        # Combine results
        combined_results = {
            'model': model_name,
            'num_params': train_results.get('num_params', 0),
            'training_time': train_results.get('training_time', 0),
            'best_epoch': train_results.get('best_epoch', 0),
            'val_auc': train_results.get('best_val_auc', 0),
            **test_metrics
        }
        all_results.append(combined_results)
    
    # Create comparison table
    df = pd.DataFrame(all_results)
    
    # Sort by test AUC
    df = df.sort_values('auc', ascending=False)
    
    print(f"\n{'='*80}")
    print("FINAL COMPARISON TABLE")
    print(f"{'='*80}")
    print(df.to_string(index=False))
    
    # Save to CSV
    output_path = os.path.join(args.save_dir, 'comparison_results.csv')
    df.to_csv(output_path, index=False)
    print(f"\nResults saved to: {output_path}")
    
    # Create visualization
    try:
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        
        # AUC comparison
        axes[0].barh(df['model'], df['auc'])
        axes[0].set_xlabel('AUC')
        axes[0].set_title('Test AUC Comparison')
        axes[0].set_xlim([min(df['auc']) - 0.01, max(df['auc']) + 0.01])
        
        # LogLoss comparison
        axes[1].barh(df['model'], df['logloss'])
        axes[1].set_xlabel('LogLoss')
        axes[1].set_title('Test LogLoss Comparison')
        
        plt.tight_layout()
        plot_path = os.path.join(args.save_dir, 'comparison_plot.png')
        plt.savefig(plot_path, dpi=150)
        print(f"Plot saved to: {plot_path}")
    except Exception as e:
        print(f"Could not create plot: {e}")
    
    return df


def main():
    parser = argparse.ArgumentParser(description='Evaluate and compare models')
    
    parser.add_argument('--data_dir', type=str, 
                        default='/Users/simonwang/agent_x1/results/session/session_153909_2026-03-15_15-39-09/research/recommendation_research/datasets',
                        help='Data directory')
    parser.add_argument('--save_dir', type=str, 
                        default='/Users/simonwang/agent_x1/results/session/session_153909_2026-03-15_15-39-09/research/recommendation_research/artifacts',
                        help='Directory with saved models')
    parser.add_argument('--batch_size', type=int, default=512, help='Batch size')
    parser.add_argument('--embedding_dim', type=int, default=16, help='Embedding dimension')
    
    args = parser.parse_args()
    
    compare_models(args)


if __name__ == '__main__':
    main()
