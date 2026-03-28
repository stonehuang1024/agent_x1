#!/usr/bin/env python3
"""
Quick test script to verify all models work correctly.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

import torch
import numpy as np
from src.rankmixer import create_rankmixer_small, create_rankmixer_base, create_rankmixer_moe
from src.baselines import create_baseline_models

def test_model(model, model_name, batch_size=4, num_features=39):
    """Test a single model"""
    print(f"\nTesting {model_name}...")
    
    # Create dummy input
    x = torch.randn(batch_size, num_features * 16)
    
    # Forward pass
    try:
        if hasattr(model, 'forward') and 'use_sparse_moe' in str(type(model)):
            output, aux_loss = model(x)
            print(f"  Output shape: {output.shape}, Aux loss: {aux_loss}")
        else:
            output = model(x)
            print(f"  Output shape: {output.shape}")
        
        # Count parameters
        num_params = sum(p.numel() for p in model.parameters())
        print(f"  Parameters: {num_params:,}")
        
        # Test backward pass
        loss = output.mean()
        loss.backward()
        print(f"  Backward pass: OK")
        
        print(f"  ✓ {model_name} passed all tests")
        return True
        
    except Exception as e:
        print(f"  ✗ {model_name} failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    print("="*60)
    print("Model Testing Suite")
    print("="*60)
    
    num_features = 39
    results = {}
    
    # Test RankMixer variants
    print("\n--- RankMixer Models ---")
    rankmixer_models = {
        'RankMixer-Small': create_rankmixer_small(num_features),
        'RankMixer-Base': create_rankmixer_base(num_features),
        'RankMixer-MoE': create_rankmixer_moe(num_features),
    }
    
    for name, model in rankmixer_models.items():
        results[name] = test_model(model, name, num_features=num_features)
    
    # Test baseline models
    print("\n--- Baseline Models ---")
    baseline_models = create_baseline_models(num_features)
    
    for name, model in baseline_models.items():
        results[name] = test_model(model, name, num_features=num_features)
    
    # Summary
    print("\n" + "="*60)
    print("Test Summary")
    print("="*60)
    
    passed = sum(results.values())
    total = len(results)
    
    for name, passed_test in results.items():
        status = "✓ PASS" if passed_test else "✗ FAIL"
        print(f"{name:<25} {status}")
    
    print("-"*60)
    print(f"Total: {passed}/{total} models passed")
    
    if passed == total:
        print("\n🎉 All models working correctly!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} model(s) failed")
        return 1

if __name__ == "__main__":
    exit(main())
