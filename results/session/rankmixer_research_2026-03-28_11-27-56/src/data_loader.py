"""
Data loader for recommendation datasets.
Supports Criteo, Avazu, and synthetic datasets for testing.
"""

import torch
import numpy as np
import pandas as pd
from torch.utils.data import Dataset, DataLoader
from sklearn.preprocessing import LabelEncoder, StandardScaler
from typing import Tuple, List, Optional
import os
import gzip
import pickle


class SyntheticDataset(Dataset):
    """
    Synthetic dataset for fast testing and validation.
    Simulates CTR prediction task with categorical and numerical features.
    """
    def __init__(self, num_samples: int = 10000, num_features: int = 39,
                 num_categorical: int = 26, num_numerical: int = 13,
                 vocab_size: int = 1000, random_seed: int = 42):
        super().__init__()
        np.random.seed(random_seed)
        
        self.num_samples = num_samples
        self.num_features = num_features
        self.num_categorical = num_categorical
        self.num_numerical = num_numerical
        
        # Generate categorical features
        self.categorical_features = np.random.randint(0, vocab_size, 
                                                       size=(num_samples, num_categorical))
        
        # Generate numerical features
        self.numerical_features = np.random.randn(num_samples, num_numerical).astype(np.float32)
        
        # Generate labels with some pattern (not completely random)
        # Create some feature interactions for realistic simulation
        interaction_score = (
            (self.categorical_features[:, 0] % 10) * 0.1 +
            (self.categorical_features[:, 1] % 5) * 0.15 +
            self.numerical_features[:, 0] * 0.2 +
            np.sin(self.numerical_features[:, 1]) * 0.1
        )
        
        # Add noise and convert to binary labels
        probs = 1 / (1 + np.exp(-interaction_score))
        self.labels = (np.random.random(num_samples) < probs).astype(np.float32)
        
        print(f"Synthetic dataset created: {num_samples} samples")
        print(f"  - Categorical features: {num_categorical}")
        print(f"  - Numerical features: {num_numerical}")
        print(f"  - Positive rate: {self.labels.mean():.3f}")
        
    def __len__(self) -> int:
        return self.num_samples
    
    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor]:
        # Combine categorical and numerical features
        cat_feats = torch.LongTensor(self.categorical_features[idx])
        num_feats = torch.FloatTensor(self.numerical_features[idx])
        label = torch.FloatTensor([self.labels[idx]])
        
        return cat_feats, num_feats, label
    
    def get_feature_info(self) -> dict:
        return {
            'num_categorical': self.num_categorical,
            'num_numerical': self.num_numerical,
            'vocab_size': 1000,
            'embedding_dims': [8] * self.num_categorical + [1] * self.num_numerical
        }


class CriteoDataset(Dataset):
    """
    Criteo Display Advertising Challenge Dataset.
    13 numerical features + 26 categorical features.
    """
    def __init__(self, data_path: str, cache_path: Optional[str] = None,
                 num_samples: Optional[int] = None):
        super().__init__()
        self.data_path = data_path
        
        # Check if cached version exists
        if cache_path and os.path.exists(cache_path):
            print(f"Loading cached data from {cache_path}")
            with open(cache_path, 'rb') as f:
                cached = pickle.load(f)
                self.features = cached['features']
                self.labels = cached['labels']
                self.feature_info = cached['feature_info']
        else:
            self.features, self.labels = self._load_data(num_samples)
            self.feature_info = self._compute_feature_info()
            
            if cache_path:
                print(f"Saving cache to {cache_path}")
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                with open(cache_path, 'wb') as f:
                    pickle.dump({
                        'features': self.features,
                        'labels': self.labels,
                        'feature_info': self.feature_info
                    }, f)
        
        print(f"Criteo dataset loaded: {len(self)} samples")
        print(f"  - Positive rate: {self.labels.mean():.3f}")
        
    def _load_data(self, num_samples: Optional[int] = None) -> Tuple[np.ndarray, np.ndarray]:
        """Load and preprocess Criteo data"""
        print(f"Loading Criteo data from {self.data_path}")
        
        if not os.path.exists(self.data_path):
            raise FileNotFoundError(f"Data file not found: {self.data_path}")
        
        # Read data
        if self.data_path.endswith('.gz'):
            f = gzip.open(self.data_path, 'rt')
        else:
            f = open(self.data_path, 'r')
        
        features_list = []
        labels_list = []
        
        for i, line in enumerate(f):
            if num_samples and i >= num_samples:
                break
            
            if i % 100000 == 0:
                print(f"  Processed {i} lines...")
            
            parts = line.strip().split('\t')
            if len(parts) != 40:  # 1 label + 13 numerical + 26 categorical
                continue
            
            # Parse label
            label = int(parts[0])
            labels_list.append(label)
            
            # Parse numerical features
            numerical = []
            for j in range(1, 14):
                val = float(parts[j]) if parts[j] else 0.0
                numerical.append(val)
            
            # Parse categorical features (use hash for simplicity)
            categorical = []
            for j in range(14, 40):
                val = hash(parts[j]) % 100000 if parts[j] else 0
                categorical.append(val)
            
            features_list.append(numerical + categorical)
        
        f.close()
        
        features = np.array(features_list, dtype=np.float32)
        labels = np.array(labels_list, dtype=np.float32)
        
        return features, labels
    
    def _compute_feature_info(self) -> dict:
        return {
            'num_categorical': 26,
            'num_numerical': 13,
            'vocab_size': 100000,
            'embedding_dims': [8] * 26 + [1] * 13
        }
    
    def __len__(self) -> int:
        return len(self.labels)
    
    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor]:
        features = torch.FloatTensor(self.features[idx])
        label = torch.FloatTensor([self.labels[idx]])
        return features, label
    
    def get_feature_info(self) -> dict:
        return self.feature_info


class AvazuDataset(Dataset):
    """
    Avazu Click-Through Rate Prediction Dataset.
    """
    def __init__(self, data_path: str, cache_path: Optional[str] = None,
                 num_samples: Optional[int] = None):
        super().__init__()
        self.data_path = data_path
        
        if cache_path and os.path.exists(cache_path):
            print(f"Loading cached data from {cache_path}")
            with open(cache_path, 'rb') as f:
                cached = pickle.load(f)
                self.features = cached['features']
                self.labels = cached['labels']
                self.feature_info = cached['feature_info']
        else:
            self.features, self.labels = self._load_data(num_samples)
            self.feature_info = self._compute_feature_info()
            
            if cache_path:
                print(f"Saving cache to {cache_path}")
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                with open(cache_path, 'wb') as f:
                    pickle.dump({
                        'features': self.features,
                        'labels': self.labels,
                        'feature_info': self.feature_info
                    }, f)
        
        print(f"Avazu dataset loaded: {len(self)} samples")
        print(f"  - Positive rate: {self.labels.mean():.3f}")
        
    def _load_data(self, num_samples: Optional[int] = None) -> Tuple[np.ndarray, np.ndarray]:
        """Load and preprocess Avazu data"""
        print(f"Loading Avazu data from {self.data_path}")
        
        if not os.path.exists(self.data_path):
            raise FileNotFoundError(f"Data file not found: {self.data_path}")
        
        # Read CSV
        df = pd.read_csv(self.data_path, nrows=num_samples)
        
        # Extract label
        labels = df['click'].values.astype(np.float32)
        
        # Process features (simplified - hash encoding)
        feature_cols = [c for c in df.columns if c not in ['click', 'id']]
        features_list = []
        
        for col in feature_cols:
            hashed = df[col].apply(lambda x: hash(str(x)) % 10000).values
            features_list.append(hashed)
        
        features = np.column_stack(features_list).astype(np.float32)
        
        return features, labels
    
    def _compute_feature_info(self) -> dict:
        return {
            'num_categorical': self.features.shape[1],
            'num_numerical': 0,
            'vocab_size': 10000,
            'embedding_dims': [8] * self.features.shape[1]
        }
    
    def __len__(self) -> int:
        return len(self.labels)
    
    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor]:
        features = torch.FloatTensor(self.features[idx])
        label = torch.FloatTensor([self.labels[idx]])
        return features, label
    
    def get_feature_info(self) -> dict:
        return self.feature_info


def create_data_loaders(dataset_name: str = 'synthetic', batch_size: int = 256,
                       train_split: float = 0.8, val_split: float = 0.1,
                       num_workers: int = 0, **kwargs) -> Tuple[DataLoader, DataLoader, DataLoader, dict]:
    """
    Create train/val/test data loaders.
    
    Args:
        dataset_name: 'synthetic', 'criteo', or 'avazu'
        batch_size: Batch size
        train_split: Proportion for training
        val_split: Proportion for validation
        num_workers: Number of data loading workers
        **kwargs: Additional arguments for dataset
    
    Returns:
        train_loader, val_loader, test_loader, feature_info
    """
    if dataset_name == 'synthetic':
        dataset = SyntheticDataset(**kwargs)
        feature_info = dataset.get_feature_info()
    elif dataset_name == 'criteo':
        dataset = CriteoDataset(**kwargs)
        feature_info = dataset.get_feature_info()
    elif dataset_name == 'avazu':
        dataset = AvazuDataset(**kwargs)
        feature_info = dataset.get_feature_info()
    else:
        raise ValueError(f"Unknown dataset: {dataset_name}")
    
    # Split dataset
    total_size = len(dataset)
    train_size = int(total_size * train_split)
    val_size = int(total_size * val_split)
    test_size = total_size - train_size - val_size
    
    train_dataset, val_dataset, test_dataset = torch.utils.data.random_split(
        dataset, [train_size, val_size, test_size],
        generator=torch.Generator().manual_seed(42)
    )
    
    # Create data loaders
    train_loader = DataLoader(train_dataset, batch_size=batch_size, 
                             shuffle=True, num_workers=num_workers)
    val_loader = DataLoader(val_dataset, batch_size=batch_size,
                           shuffle=False, num_workers=num_workers)
    test_loader = DataLoader(test_dataset, batch_size=batch_size,
                            shuffle=False, num_workers=num_workers)
    
    print(f"\nData splits:")
    print(f"  Train: {len(train_dataset)}")
    print(f"  Val: {len(val_dataset)}")
    print(f"  Test: {len(test_dataset)}")
    
    return train_loader, val_loader, test_loader, feature_info


if __name__ == "__main__":
    print("Testing data loaders...")
    
    # Test synthetic dataset
    print("\n=== Synthetic Dataset ===")
    train_loader, val_loader, test_loader, feature_info = create_data_loaders(
        dataset_name='synthetic',
        num_samples=10000,
        batch_size=32
    )
    
    for batch in train_loader:
        cat_feats, num_feats, labels = batch
        print(f"Categorical shape: {cat_feats.shape}")
        print(f"Numerical shape: {num_feats.shape}")
        print(f"Labels shape: {labels.shape}")
        break
    
    print("\nData loader tests passed!")
