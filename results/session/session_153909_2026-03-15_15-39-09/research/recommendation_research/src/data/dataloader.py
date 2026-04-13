"""
Data loader for Criteo-like dataset
"""
import torch
from torch.utils.data import Dataset, DataLoader
import pandas as pd
import numpy as np
import json
import os

class CriteoDataset(Dataset):
    """Criteo Display Ads Dataset"""
    
    def __init__(self, data_path, sparse_vocab_sizes, num_dense=13, num_sparse=26):
        """
        Args:
            data_path: path to CSV file
            sparse_vocab_sizes: dict mapping sparse feature name to vocab size
            num_dense: number of dense features
            num_sparse: number of sparse features
        """
        self.df = pd.read_csv(data_path)
        self.num_dense = num_dense
        self.num_sparse = num_sparse
        self.sparse_vocab_sizes = sparse_vocab_sizes
        
        # Dense feature names
        self.dense_cols = [f'I{i}' for i in range(1, num_dense + 1)]
        # Sparse feature names
        self.sparse_cols = [f'C{i}' for i in range(1, num_sparse + 1)]
        
        # Normalize dense features
        self.dense_means = self.df[self.dense_cols].mean()
        self.dense_stds = self.df[self.dense_cols].std() + 1e-8
        
        for col in self.dense_cols:
            self.df[col] = (self.df[col] - self.dense_means[col]) / self.dense_stds[col]
            # Fill NaN with 0
            self.df[col] = self.df[col].fillna(0)
    
    def __len__(self):
        return len(self.df)
    
    def __getitem__(self, idx):
        row = self.df.iloc[idx]
        
        # Dense features
        dense_features = torch.tensor(
            [row[col] for col in self.dense_cols], 
            dtype=torch.float32
        )
        
        # Sparse features (categorical indices)
        sparse_features = torch.tensor(
            [int(row[col]) for col in self.sparse_cols], 
            dtype=torch.long
        )
        
        # Label
        label = torch.tensor(float(row['label']), dtype=torch.float32)
        
        return dense_features, sparse_features, label


def get_dataloaders(data_dir, batch_size=512, num_workers=0):
    """
    Create train/val/test dataloaders
    
    Returns:
        train_loader, val_loader, test_loader, sparse_vocab_sizes
    """
    # Load dataset info
    with open(os.path.join(data_dir, 'dataset_info.json'), 'r') as f:
        dataset_info = json.load(f)
    
    sparse_vocab_sizes = dataset_info['sparse_vocab_sizes']
    num_dense = dataset_info['num_dense']
    num_sparse = dataset_info['num_sparse']
    
    # Create datasets
    train_dataset = CriteoDataset(
        os.path.join(data_dir, 'train.csv'),
        sparse_vocab_sizes,
        num_dense,
        num_sparse
    )
    
    val_dataset = CriteoDataset(
        os.path.join(data_dir, 'val.csv'),
        sparse_vocab_sizes,
        num_dense,
        num_sparse
    )
    
    test_dataset = CriteoDataset(
        os.path.join(data_dir, 'test.csv'),
        sparse_vocab_sizes,
        num_dense,
        num_sparse
    )
    
    # Create dataloaders
    train_loader = DataLoader(
        train_dataset,
        batch_size=batch_size,
        shuffle=True,
        num_workers=num_workers,
        pin_memory=True
    )
    
    val_loader = DataLoader(
        val_dataset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        pin_memory=True
    )
    
    test_loader = DataLoader(
        test_dataset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        pin_memory=True
    )
    
    return train_loader, val_loader, test_loader, sparse_vocab_sizes, num_dense, num_sparse
