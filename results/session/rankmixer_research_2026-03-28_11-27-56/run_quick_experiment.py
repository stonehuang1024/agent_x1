#!/usr/bin/env python3
"""
Quick experiment script for RankMixer.
Runs a small-scale experiment for fast validation.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.train_fixed import run_experiment
import json

def main():
    # Quick experiment configuration
    config = {
        'dataset': 'synthetic',
        'num_samples': 20000,    # Small dataset for quick testing
        'batch_size': 128,
        'num_epochs': 10,
        'learning_rate': 0.001,
        'save_dir': 'experiments/quick_results',
        'models': ['rankmixer_small', 'baselines'],  # Start with small model + baselines
        'patience': 3
    }
    
    print("="*80)
    print("RankMixer Quick Experiment")
    print("="*80)
    print(f"Configuration:")
    print(json.dumps(config, indent=2))
    print("="*80)
    
    # Run experiment
    histories = run_experiment(config)
    
    print("\n" + "="*80)
    print("Quick experiment completed!")
    print(f"Results saved to: {config['save_dir']}")
    print("="*80)

if __name__ == "__main__":
    main()
