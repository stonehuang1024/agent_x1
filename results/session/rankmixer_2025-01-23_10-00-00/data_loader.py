"""
Data loading utilities for recommendation datasets
Supports Criteo, Avazu, and MovieLens datasets
"""

import os
import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset, DataLoader
from sklearn.preprocessing import LabelEncoder
from typing import Tuple, List, Optional
import requests
import zipfile
import gzip
import shutil


class CriteoDataset(Dataset):
    """
    Criteo Display Advertising Challenge Dataset
    45 million records, 13 integer features + 26 categorical features
    """
    def __init__(self, data_path: str, split: str = 'train', 
                 sample_ratio: float = 0.01, cache_dir: str = './cache'):
        self.data_path = data_path
        self.split = split
        self.sample_ratio = sample_ratio
        self.cache_dir = cache_dir
        
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f'criteo_{split}_{sample_ratio}.npz')
        
        if os.path.exists(cache_file):
            # Load from cache
            data = np.load(cache_file)
            self.features = data['features']
            self.labels = data['labels']
            self.feature_dims = data['feature_dims'].tolist()
        else:
            # Process raw data
            self.features, self.labels, self.feature_dims = self._load_data()
            # Save to cache
            np.savez(cache_file, features=self.features, labels=self.labels,
                    feature_dims=self.feature_dims)
        
        print(f"Criteo {split} dataset loaded: {len(self)} samples")
        print(f"Feature dimensions: {self.feature_dims}")
    
    def _load_data(self) -> Tuple[np.ndarray, np.ndarray, List[int]]:
        """Load and preprocess Criteo data"""
        # For demonstration, we'll use a smaller sample
        # In practice, download from: https://www.kaggle.com/c/criteo-display-ad-challenge
        
        file_path = os.path.join(self.data_path, f'{self.split}.txt')
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(
                f"Criteo data not found at {file_path}. "
                "Please download from https://www.kaggle.com/c/criteo-display-ad-challenge"
            )
        
        # Read data
        print(f"Loading Criteo data from {file_path}...")
        
        # Criteo format: label, int features (13), cat features (26)
        columns = ['label'] + [f'I{i}' for i in range(1, 14)] + [f'C{i}' for i in range(1, 27)]
        
        # Sample data for faster processing
        df = pd.read_csv(file_path, sep='\t', names=columns, nrows=int(100000 * self.sample_ratio))
        
        # Separate label
        labels = df['label'].values.astype(np.float32)
        
        # Process integer features (fill NA with 0, then discretize)
        int_features = []
        for col in [f'I{i}' for i in range(1, 14)]:
            df[col] = df[col].fillna(0)
            # Discretize into bins
            df[col] = pd.qcut(df[col], q=10, duplicates='drop', labels=False)
            int_features.append(df[col].values)
        
        # Process categorical features (fill NA with 'unknown')
        cat_features = []
        feature_dims = []
        
        for col in [f'C{i}' for i in range(1, 27)]:
            df[col] = df[col].fillna('unknown')
            le = LabelEncoder()
            df[col] = le.fit_transform(df[col].astype(str))
            cat_features.append(df[col].values)
            feature_dims.append(len(le.classes_))
        
        # Add integer feature dimensions
        for _ in range(13):
            feature_dims.insert(0, 10)  # 10 bins for each integer feature
        
        # Combine features
        features = np.column_stack(int_features + cat_features)
        
        return features.astype(np.int64), labels, feature_dims
    
    def __len__(self):
        return len(self.labels)
    
    def __getitem__(self, idx):
        return torch.LongTensor(self.features[idx]), torch.FloatTensor([self.labels[idx]])


class AvazuDataset(Dataset):
    """
    Avazu Click-Through Rate Prediction Dataset
    ~40 million records, 23 categorical features
    """
    def __init__(self, data_path: str, split: str = 'train',
                 sample_ratio: float = 0.01, cache_dir: str = './cache'):
        self.data_path = data_path
        self.split = split
        self.sample_ratio = sample_ratio
        self.cache_dir = cache_dir
        
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f'avazu_{split}_{sample_ratio}.npz')
        
        if os.path.exists(cache_file):
            data = np.load(cache_file)
            self.features = data['features']
            self.labels = data['labels']
            self.feature_dims = data['feature_dims'].tolist()
        else:
            self.features, self.labels, self.feature_dims = self._load_data()
            np.savez(cache_file, features=self.features, labels=self.labels,
                    feature_dims=self.feature_dims)
        
        print(f"Avazu {split} dataset loaded: {len(self)} samples")
        print(f"Feature dimensions: {self.feature_dims}")
    
    def _load_data(self) -> Tuple[np.ndarray, np.ndarray, List[int]]:
        """Load and preprocess Avazu data"""
        file_path = os.path.join(self.data_path, f'{self.split}.csv')
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(
                f"Avazu data not found at {file_path}. "
                "Please download from https://www.kaggle.com/c/avazu-ctr-prediction"
            )
        
        print(f"Loading Avazu data from {file_path}...")
        
        # Sample data
        df = pd.read_csv(file_path, nrows=int(100000 * self.sample_ratio))
        
        # Separate label
        labels = df['click'].values.astype(np.float32)
        
        # Drop id and click columns
        df = df.drop(['id', 'click'], axis=1)
        
        # Encode all categorical features
        features = []
        feature_dims = []
        
        for col in df.columns:
            le = LabelEncoder()
            df[col] = le.fit_transform(df[col].astype(str))
            features.append(df[col].values)
            feature_dims.append(len(le.classes_))
        
        features = np.column_stack(features)
        
        return features.astype(np.int64), labels, feature_dims
    
    def __len__(self):
        return len(self.labels)
    
    def __getitem__(self, idx):
        return torch.LongTensor(self.features[idx]), torch.FloatTensor([self.labels[idx]])


class MovieLensDataset(Dataset):
    """
    MovieLens 1M Dataset for rating prediction
    Can be adapted for CTR by treating ratings as binary (like/dislike)
    """
    def __init__(self, data_path: str, split: str = 'train', 
                 test_ratio: float = 0.2, cache_dir: str = './cache'):
        self.data_path = data_path
        self.split = split
        self.test_ratio = test_ratio
        self.cache_dir = cache_dir
        
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, f'movielens_{split}.npz')
        
        if os.path.exists(cache_file):
            data = np.load(cache_file)
            self.features = data['features']
            self.labels = data['labels']
            self.feature_dims = data['feature_dims'].tolist()
        else:
            self.features, self.labels, self.feature_dims = self._load_data()
            np.savez(cache_file, features=self.features, labels=self.labels,
                    feature_dims=self.feature_dims)
        
        print(f"MovieLens {split} dataset loaded: {len(self)} samples")
        print(f"Feature dimensions: {self.feature_dims}")
    
    def _load_data(self) -> Tuple[np.ndarray, np.ndarray, List[int]]:
        """Load and preprocess MovieLens data"""
        ratings_file = os.path.join(self.data_path, 'ratings.dat')
        users_file = os.path.join(self.data_path, 'users.dat')
        movies_file = os.path.join(self.data_path, 'movies.dat')
        
        if not all(os.path.exists(f) for f in [ratings_file, users_file, movies_file]):
            raise FileNotFoundError(
                f"MovieLens data not found. Please download from https://grouplens.org/datasets/movielens/1m/"
            )
        
        print("Loading MovieLens data...")
        
        # Load ratings
        ratings = pd.read_csv(ratings_file, sep='::', engine='python',
                             names=['user_id', 'movie_id', 'rating', 'timestamp'])
        
        # Load users
        users = pd.read_csv(users_file, sep='::', engine='python',
                           names=['user_id', 'gender', 'age', 'occupation', 'zipcode'])
        
        # Load movies
        movies = pd.read_csv(movies_file, sep='::', engine='python',
                            names=['movie_id', 'title', 'genres'], encoding='latin-1')
        
        # Merge data
        data = ratings.merge(users, on='user_id').merge(movies, on='movie_id')
        
        # Convert rating to binary label (like if rating >= 4)
        labels = (data['rating'] >= 4).astype(np.float32).values
        
        # Encode categorical features
        feature_cols = ['user_id', 'movie_id', 'gender', 'age', 'occupation']
        
        features = []
        feature_dims = []
        
        for col in feature_cols:
            le = LabelEncoder()
            data[col] = le.fit_transform(data[col].astype(str))
            features.append(data[col].values)
            feature_dims.append(len(le.classes_))
        
        # Add timestamp as feature (discretized)
        data['hour'] = pd.to_datetime(data['timestamp'], unit='s').dt.hour
        features.append(data['hour'].values)
        feature_dims.append(24)
        
        features = np.column_stack(features)
        
        # Split train/test
        n_samples = len(labels)
        n_test = int(n_samples * self.test_ratio)
        
        if self.split == 'train':
            features = features[n_test:]
            labels = labels[n_test:]
        else:
            features = features[:n_test]
            labels = labels[:n_test]
        
        return features.astype(np.int64), labels, feature_dims
    
    def __len__(self):
        return len(self.labels)
    
    def __getitem__(self, idx):
        return torch.LongTensor(self.features[idx]), torch.FloatTensor([self.labels[idx]])


def download_movielens_1m(data_dir: str = './data/movielens'):
    """Download and extract MovieLens 1M dataset"""
    os.makedirs(data_dir, exist_ok=True)
    
    url = "https://files.grouplens.org/datasets/movielens/ml-1m.zip"
    zip_path = os.path.join(data_dir, "ml-1m.zip")
    
    if not os.path.exists(os.path.join(data_dir, "ratings.dat")):
        print(f"Downloading MovieLens 1M dataset...")
        response = requests.get(url, stream=True)
        with open(zip_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        print("Extracting...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(data_dir)
        
        # Move files to data_dir
        extracted_dir = os.path.join(data_dir, "ml-1m")
        for file in os.listdir(extracted_dir):
            shutil.move(os.path.join(extracted_dir, file), data_dir)
        os.rmdir(extracted_dir)
        os.remove(zip_path)
        
        print("MovieLens 1M dataset ready!")
    else:
        print("MovieLens 1M dataset already exists.")
    
    return data_dir


def create_synthetic_dataset(n_samples: int = 10000, n_features: int = 39,
                            feature_dim: int = 100, seed: int = 42):
    """
    Create synthetic dataset for testing
    Similar structure to Criteo dataset
    """
    np.random.seed(seed)
    
    # Generate random features
    features = np.random.randint(0, feature_dim, size=(n_samples, n_features))
    
    # Generate labels with some pattern
    # Make some features more important
    weights = np.random.randn(n_features)
    logits = np.sum(features * weights, axis=1) / n_features
    probs = 1 / (1 + np.exp(-logits))
    labels = (np.random.random(n_samples) < probs).astype(np.float32)
    
    feature_dims = [feature_dim] * n_features
    
    return features.astype(np.int64), labels, feature_dims


class SyntheticDataset(Dataset):
    """Synthetic dataset for quick testing"""
    def __init__(self, n_samples: int = 10000, n_features: int = 39,
                 feature_dim: int = 100, split: str = 'train', seed: int = 42):
        
        # Different seed for train/test
        seed = seed if split == 'train' else seed + 1
        
        self.features, self.labels, self.feature_dims = create_synthetic_dataset(
            n_samples, n_features, feature_dim, seed
        )
        
        print(f"Synthetic {split} dataset: {len(self)} samples, {n_features} features")
    
    def __len__(self):
        return len(self.labels)
    
    def __getitem__(self, idx):
        return torch.LongTensor(self.features[idx]), torch.FloatTensor([self.labels[idx]])


def get_data_loader(dataset_name: str = 'synthetic', data_path: str = './data',
                   batch_size: int = 256, split: str = 'train', **kwargs):
    """
    Get data loader for specified dataset
    
    Args:
        dataset_name: 'criteo', 'avazu', 'movielens', or 'synthetic'
        data_path: path to dataset directory
        batch_size: batch size
        split: 'train' or 'test'
        **kwargs: additional dataset-specific arguments
    """
    if dataset_name == 'criteo':
        dataset = CriteoDataset(data_path, split=split, **kwargs)
    elif dataset_name == 'avazu':
        dataset = AvazuDataset(data_path, split=split, **kwargs)
    elif dataset_name == 'movielens':
        dataset = MovieLensDataset(data_path, split=split, **kwargs)
    elif dataset_name == 'synthetic':
        dataset = SyntheticDataset(split=split, **kwargs)
    else:
        raise ValueError(f"Unknown dataset: {dataset_name}")
    
    shuffle = (split == 'train')
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=shuffle,
                       num_workers=0, pin_memory=True)
    
    return loader, dataset.feature_dims


if __name__ == "__main__":
    # Test data loading
    print("Testing synthetic dataset...")
    train_loader, feature_dims = get_data_loader('synthetic', batch_size=32, 
                                                  n_samples=1000, n_features=39)
    
    for batch_features, batch_labels in train_loader:
        print(f"Batch features shape: {batch_features.shape}")
        print(f"Batch labels shape: {batch_labels.shape}")
        print(f"Feature dimensions: {feature_dims}")
        break
    
    print("\nData loader test passed!")
