#!/usr/bin/env python3
"""
Final experiment script for RankMixer.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.train_final import run_experiment
import json

def main():
    # Experiment configuration
    config = {
        'dataset': 'synthetic',
        'num_samples': 30000,    # Medium dataset for validation
        'batch_size': 128,
        'num_epochs': 12,
        'learning_rate': 0.001,
        'save_dir': 'experiments/final_results',
        'models': ['rankmixer_small', 'baselines'],  # Quick test with small model
        'patience': 4
    }
    
    print("="*80)
    print("RankMixer Final Experiment")
    print("="*80)
    print(f"Configuration:")
    print(json.dumps(config, indent=2))
    print("="*80)
    
    histories = run_experiment(config)
    
    print("\n" + "="*80)
    print("Experiment completed!")
    print(f"Results saved to: {config['save_dir']}")
    print("="*80)

if __name__ == "__main__":
    main()
