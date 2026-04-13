"""
Run complete experiments for RankMixer paper reproduction
"""

import os
import sys
import subprocess
import json
import time


def run_command(cmd, description):
    """Run command and print output"""
    print(f"\n{'='*80}")
    print(f"{description}")
    print(f"{'='*80}")
    print(f"Command: {cmd}")
    print()
    
    result = subprocess.run(cmd, shell=True, capture_output=False, text=True)
    
    if result.returncode != 0:
        print(f"Warning: Command failed with return code {result.returncode}")
    
    return result.returncode == 0


def main():
    """Run all experiments"""
    
    print("="*80)
    print("RANKMIXER PAPER REPRODUCTION - EXPERIMENT PIPELINE")
    print("="*80)
    
    # Create output directory
    os.makedirs('./output', exist_ok=True)
    
    # Test model implementation
    print("\n" + "="*80)
    print("STEP 1: Testing Model Implementation")
    print("="*80)
    
    success = run_command(
        "python rankmixer_model.py",
        "Testing RankMixer model implementation"
    )
    
    if not success:
        print("Model test failed! Please check the implementation.")
        return
    
    # Test data loading
    print("\n" + "="*80)
    print("STEP 2: Testing Data Loading")
    print("="*80)
    
    success = run_command(
        "python data_loader.py",
        "Testing data loader"
    )
    
    if not success:
        print("Data loader test failed! Please check the implementation.")
        return
    
    # Run quick training test
    print("\n" + "="*80)
    print("STEP 3: Quick Training Test (Small Model)")
    print("="*80)
    
    success = run_command(
        "python train.py --model rankmixer --dataset synthetic "
        "--n_samples 5000 --n_features 20 --epochs 3 --batch_size 64 "
        "--hidden_dim 32 --num_tokens 8 --num_heads 8 "
        "--output_dir ./output/quick_test",
        "Quick training test with small model"
    )
    
    if not success:
        print("Quick training test failed! Please check the training script.")
        return
    
    # Run full comparison experiments
    print("\n" + "="*80)
    print("STEP 4: Running Model Comparison")
    print("="*80)
    
    success = run_command(
        "python evaluate.py --mode compare --dataset synthetic "
        "--n_samples 30000 --n_features 39 --epochs 15 --batch_size 256",
        "Running model comparison experiments"
    )
    
    if not success:
        print("Model comparison failed! Check the evaluate script.")
        return
    
    # Run scaling law analysis
    print("\n" + "="*80)
    print("STEP 5: Running Scaling Law Analysis")
    print("="*80)
    
    success = run_command(
        "python evaluate.py --mode scaling --dataset synthetic "
        "--n_samples 30000 --n_features 39 --epochs 12 --batch_size 256",
        "Running scaling law analysis"
    )
    
    if not success:
        print("Scaling law analysis failed! Check the evaluate script.")
        return
    
    # Generate final report
    print("\n" + "="*80)
    print("STEP 6: Generating Final Report")
    print("="*80)
    
    generate_report()
    
    print("\n" + "="*80)
    print("ALL EXPERIMENTS COMPLETED!")
    print("="*80)
    print("\nResults are saved in the ./output directory:")
    print("  - ./output/compare/: Model comparison results")
    print("  - ./output/scaling/: Scaling law analysis results")
    print("  - ./output/quick_test/: Quick test results")
    print("\nCheck the following files:")
    print("  - comparison_plots.png: Visual comparison of models")
    print("  - comparison_results.json: Detailed comparison metrics")
    print("  - scaling_law.png: Scaling law visualization")


def generate_report():
    """Generate final experiment report"""
    
    report = []
    report.append("="*80)
    report.append("RANKMIXER REPRODUCTION - EXPERIMENT REPORT")
    report.append("="*80)
    report.append("")
    report.append(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    report.append("")
    
    # Load comparison results
    compare_file = './output/compare/comparison_results.json'
    if os.path.exists(compare_file):
        with open(compare_file, 'r') as f:
            compare_results = json.load(f)
        
        report.append("-" * 80)
        report.append("MODEL COMPARISON RESULTS")
        report.append("-" * 80)
        report.append("")
        
        for name, result in compare_results.items():
            report.append(f"Model: {name}")
            report.append(f"  Parameters: {result['num_parameters']:,}")
            report.append(f"  Best Val AUC: {result['best_val_auc']:.4f}")
            report.append(f"  Best Epoch: {result['best_epoch']}")
            report.append(f"  Training Time: {result['total_training_time']/60:.1f} minutes")
            report.append("")
    
    # Load scaling results
    scaling_file = './output/scaling/scaling_results.json'
    if os.path.exists(scaling_file):
        with open(scaling_file, 'r') as f:
            scaling_results = json.load(f)
        
        report.append("-" * 80)
        report.append("SCALING LAW RESULTS")
        report.append("-" * 80)
        report.append("")
        
        for result in scaling_results:
            report.append(f"Hidden Dim: {result['hidden_dim']}")
            report.append(f"  Parameters: {result['params']:,}")
            report.append(f"  Best AUC: {result['best_auc']:.4f}")
            report.append("")
    
    report.append("="*80)
    
    # Save report
    report_text = "\n".join(report)
    with open('./output/EXPERIMENT_REPORT.txt', 'w') as f:
        f.write(report_text)
    
    print(report_text)
    print(f"\nReport saved to: ./output/EXPERIMENT_REPORT.txt")


if __name__ == "__main__":
    main()
