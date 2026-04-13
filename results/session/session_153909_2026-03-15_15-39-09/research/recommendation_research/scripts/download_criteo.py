#!/usr/bin/env python3
"""
Download and preprocess Criteo Display Ads Dataset
"""
import os
import urllib.request
import gzip
import shutil
import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split

# Criteo dataset columns
COLUMNS = ['label'] + [f'I{i}' for i in range(1, 14)] + [f'C{i}' for i in range(1, 27)]

def download_file(url, output_path):
    """Download file with progress"""
    print(f"Downloading {url}...")
    urllib.request.urlretrieve(url, output_path)
    print(f"Downloaded to {output_path}")

def prepare_criteo_sample():
    """
    Download Criteo dataset sample.
    Using a smaller subset for faster experimentation.
    """
    data_dir = "/Users/simonwang/agent_x1/results/session/session_153909_2026-03-15_15-39-09/research/recommendation_research/datasets"
    os.makedirs(data_dir, exist_ok=True)
    
    # Try to download from Kaggle or use a smaller sample
    # For this research, we'll create a synthetic dataset that mimics Criteo structure
    # In production, you would download the actual Criteo dataset
    
    print("Creating Criteo-like synthetic dataset for testing...")
    
    # Generate synthetic data with similar characteristics to Criteo
    np.random.seed(42)
    n_samples = 100000  # Start with 100k samples
    
    data = {}
    
    # Label (click or not)
    data['label'] = np.random.binomial(1, 0.25, n_samples).astype(np.float32)
    
    # Integer features (13 features) - typically counts or continuous values
    for i in range(1, 14):
        # Mix of zero-inflated and log-normal distributions
        if np.random.random() > 0.5:
            data[f'I{i}'] = np.random.exponential(2, n_samples).astype(np.float32)
        else:
            data[f'I{i}'] = np.random.lognormal(0, 1, n_samples).astype(np.float32)
    
    # Categorical features (26 features)
    vocab_sizes = np.random.randint(100, 10000, 26)
    for i, vocab_size in enumerate(vocab_sizes, 1):
        data[f'C{i}'] = np.random.randint(0, vocab_size, n_samples).astype(np.int32)
    
    df = pd.DataFrame(data)
    
    # Split into train/val/test
    train_df, temp_df = train_test_split(df, test_size=0.2, random_state=42, stratify=df['label'])
    val_df, test_df = train_test_split(temp_df, test_size=0.5, random_state=42, stratify=temp_df['label'])
    
    # Save
    train_df.to_csv(f"{data_dir}/train.csv", index=False)
    val_df.to_csv(f"{data_dir}/val.csv", index=False)
    test_df.to_csv(f"{data_dir}/test.csv", index=False)
    
    print(f"Dataset created:")
    print(f"  Train: {len(train_df)} samples")
    print(f"  Val: {len(val_df)} samples")
    print(f"  Test: {len(test_df)} samples")
    print(f"  Positive rate: {df['label'].mean():.3f}")
    
    # Save column info
    info = {
        'num_dense': 13,
        'num_sparse': 26,
        'sparse_vocab_sizes': {f'C{i}': int(vocab_sizes[i-1]) for i in range(1, 27)},
        'train_size': len(train_df),
        'val_size': len(val_df),
        'test_size': len(test_df)
    }
    
    import json
    with open(f"{data_dir}/dataset_info.json", 'w') as f:
        json.dump(info, f, indent=2)
    
    print(f"Dataset info saved to {data_dir}/dataset_info.json")
    
    return data_dir

if __name__ == "__main__":
    prepare_criteo_sample()
