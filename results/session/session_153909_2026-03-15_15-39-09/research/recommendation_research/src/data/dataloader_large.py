"""
Optimized Data loader for large Criteo dataset with memory efficiency
"""
import torch
from torch.utils.data import Dataset, DataLoader
import pandas as pd
import numpy as np
import json
import os

class CriteoDatasetLarge(Dataset):
    """Memory-efficient Criteo Display Ads Dataset for large files"""
    
    def __init__(self, data_path, sparse_vocab_sizes, num_dense=13, num_sparse=26, 
                 chunksize=100000, precompute_stats=True):
        """
        Args:
            data_path: path to CSV file
            sparse_vocab_sizes: dict mapping sparse feature name to vocab size
            num_dense: number of dense features
            num_sparse: number of sparse features
            chunksize: number of rows to process at once for memory efficiency
            precompute_stats: whether to precompute normalization stats
        """
        self.data_path = data_path
        self.num_dense = num_dense
        self.num_sparse = num_sparse
        self.sparse_vocab_sizes = sparse_vocab_sizes
        self.chunksize = chunksize
        
        # Dense feature names
        self.dense_cols = [f'I{i}' for i in range(1, num_dense + 1)]
        # Sparse feature names
        self.sparse_cols = [f'C{i}' for i in range(1, num_sparse + 1)]
        
        # Get total number of rows
        print(f"Counting rows in {data_path}...")
        self.total_rows = sum(1 for _ in open(data_path)) - 1  # minus header
        print(f"Total rows: {self.total_rows:,}")
        
        # Precompute normalization statistics if needed
        if precompute_stats:
            self._compute_stats()
        else:
            self.dense_means = None
            self.dense_stds = None
    
    def _compute_stats(self):
        """Compute mean and std for dense features using streaming"""
        print(f"Computing normalization statistics...")
        
        # Initialize accumulators
        sums = np.zeros(self.num_dense)
        sum_squares = np.zeros(self.num_dense)
        counts = 0
        
        for chunk in pd.read_csv(self.data_path, chunksize=self.chunksize):
            dense_values = chunk[self.dense_cols].values
            # Handle NaN
            dense_values = np.nan_to_num(dense_values, nan=0.0)
            
            sums += np.sum(dense_values, axis=0)
            sum_squares += np.sum(dense_values ** 2, axis=0)
            counts += len(chunk)
        
        self.dense_means = sums / counts
        self.dense_stds = np.sqrt(sum_squares / counts - self.dense_means ** 2) + 1e-8
        
        print(f"Stats computed: means={self.dense_means[:3]}..., stds={self.dense_stds[:3]}...")
    
    def __len__(self):
        return self.total_rows
    
    def __getitem__(self, idx):
        # Read specific row - for large datasets, we read in chunks
        chunk_idx = idx // self.chunksize
        row_idx = idx % self.chunksize
        
        # Read the chunk containing this row
        skiprows = chunk_idx * self.chunksize + 1  # +1 for header
        nrows = min(self.chunksize, self.total_rows - skiprows + 1)
        
        chunk = pd.read_csv(self.data_path, skiprows=range(1, skiprows), nrows=nrows)
        row = chunk.iloc[row_idx]
        
        # Dense features
        dense_features = np.array([row[col] for col in self.dense_cols], dtype=np.float32)
        dense_features = np.nan_to_num(dense_features, nan=0.0)
        
        if self.dense_means is not None:
            dense_features = (dense_features - self.dense_means) / self.dense_stds
        
        dense_features = torch.tensor(dense_features, dtype=torch.float32)
        
        # Sparse features (categorical indices)
        sparse_features = torch.tensor(
            [int(row[col]) for col in self.sparse_cols], 
            dtype=torch.long
        )
        
        # Label
        label = torch.tensor(float(row['label']), dtype=torch.float32)
        
        return dense_features, sparse_features, label


class CriteoDatasetChunked(Dataset):
    """Chunked dataset that loads data in memory-efficient way"""
    
    def __init__(self, data_path, sparse_vocab_sizes, num_dense=13, num_sparse=26):
        self.data_path = data_path
        self.num_dense = num_dense
        self.num_sparse = num_sparse
        self.sparse_vocab_sizes = sparse_vocab_sizes
        
        self.dense_cols = [f'I{i}' for i in range(1, num_dense + 1)]
        self.sparse_cols = [f'C{i}' for i in range(1, num_sparse + 1)]
        
        # Load entire file into memory (for smaller files)
        print(f"Loading {data_path}...")
        self.df = pd.read_csv(data_path)
        print(f"Loaded {len(self.df)} rows")
        
        # Preprocess dense features
        self._preprocess()
    
    def _preprocess(self):
        """Preprocess dense features"""
        # Fill NaN with 0
        for col in self.dense_cols:
            self.df[col] = self.df[col].fillna(0)
        
        # Normalize
        self.dense_means = self.df[self.dense_cols].mean().values
        self.dense_stds = self.df[self.dense_cols].std().values + 1e-8
        
        for i, col in enumerate(self.dense_cols):
            self.df[col] = (self.df[col] - self.dense_means[i]) / self.dense_stds[i]
    
    def __len__(self):
        return len(self.df)
    
    def __getitem__(self, idx):
        row = self.df.iloc[idx]
        
        dense_features = torch.tensor(
            [row[col] for col in self.dense_cols], 
            dtype=torch.float32
        )
        
        sparse_features = torch.tensor(
            [int(row[col]) for col in self.sparse_cols], 
            dtype=torch.long
        )
        
        label = torch.tensor(float(row['label']), dtype=torch.float32)
        
        return dense_features, sparse_features, label


def get_dataloaders_large(data_dir, batch_size=2048, num_workers=4, use_chunked=True):
    """
    Create train/val/test dataloaders for large dataset
    
    Returns:
        train_loader, val_loader, test_loader, sparse_vocab_sizes, num_dense, num_sparse
    """
    # Load dataset info
    with open(os.path.join(data_dir, 'dataset_info.json'), 'r') as f:
        dataset_info = json.load(f)
    
    sparse_vocab_sizes = dataset_info['sparse_vocab_sizes']
    num_dense = dataset_info['num_dense']
    num_sparse = dataset_info['num_sparse']
    
    # Choose dataset class based on size
    DatasetClass = CriteoDatasetChunked if use_chunked else CriteoDatasetLarge
    
    # Create datasets
    print("Creating datasets...")
    train_dataset = DatasetClass(
        os.path.join(data_dir, 'train.csv'),
        sparse_vocab_sizes,
        num_dense,
        num_sparse
    )
    
    val_dataset = DatasetClass(
        os.path.join(data_dir, 'val.csv'),
        sparse_vocab_sizes,
        num_dense,
        num_sparse
    )
    
    test_dataset = DatasetClass(
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
        pin_memory=True,
        drop_last=True
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
