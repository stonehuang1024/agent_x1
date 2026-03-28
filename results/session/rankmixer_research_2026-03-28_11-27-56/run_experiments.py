#!/usr/bin/env python3
"""
Main script to run RankMixer experiments.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.train import run_experiment
import json

def main():
    # Experiment configuration
    config = {
        'dataset': 'synthetic',  # Use synthetic for fast iteration
        'num_samples': 50000,    # Small dataset for quick testing
        'batch_size': 256,
        'num_epochs': 15,
        'learning_rate': 0.001,
        'save_dir': 'experiments/results',
        'models': ['rankmixer_small', 'rankmixer_base', 'rankmixer_moe', 'baselines'],
        'patience': 5
    }
    
    print("="*80)
    print("RankMixer Research Experiment")
    print("="*80)
    print(f"Configuration:")
    print(json.dumps(config, indent=2))
    print("="*80)
    
    # Run experiment
    histories = run_experiment(config)
    
    print("\n" + "="*80)
    print("All experiments completed!")
    print(f"Results saved to: {config['save_dir']}")
    print("="*80)

if __name__ == "__main__":
    main()
