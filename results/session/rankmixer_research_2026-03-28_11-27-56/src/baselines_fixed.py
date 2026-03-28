"""
Baseline models for comparison with RankMixer.
Fixed implementations with proper input handling.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import List


class MLPRankingModel(nn.Module):
    """Simple MLP baseline for recommendation ranking."""
    def __init__(self, input_dim: int, hidden_dims: List[int] = [256, 128, 64], 
                 dropout: float = 0.1, num_tasks: int = 1):
        super().__init__()
        
        layers = []
        current_dim = input_dim
        for hidden_dim in hidden_dims:
            layers.extend([
                nn.Linear(current_dim, hidden_dim),
                nn.ReLU(),
                nn.Dropout(dropout)
            ])
            current_dim = hidden_dim
        
        self.mlp = nn.Sequential(*layers)
        self.task_heads = nn.ModuleList([nn.Linear(current_dim, 1) for _ in range(num_tasks)])
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.mlp(x)
        predictions = torch.cat([head(x) for head in self.task_heads], dim=1)
        return predictions
    
    def count_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


class DeepFM(nn.Module):
    """DeepFM: Deep Factorization Machine."""
    def __init__(self, input_dim: int, num_features: int = 39, embed_dim: int = 16,
                 hidden_dims: List[int] = [128, 64], dropout: float = 0.1, num_tasks: int = 1):
        super().__init__()
        self.num_features = num_features
        self.embed_dim = embed_dim
        
        # FM first order
        self.fm_first_order = nn.Linear(input_dim, 1)
        
        # Deep component
        layers = []
        current_dim = input_dim
        for hidden_dim in hidden_dims:
            layers.extend([
                nn.Linear(current_dim, hidden_dim),
                nn.ReLU(),
                nn.Dropout(dropout)
            ])
            current_dim = hidden_dim
        
        self.deep = nn.Sequential(*layers)
        self.deep_output = nn.Linear(current_dim, 1)
        
        # Task heads
        self.task_heads = nn.ModuleList([nn.Linear(2, 1) for _ in range(num_tasks)])
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # FM first order
        fm_first = self.fm_first_order(x)
        
        # Deep component
        deep_out = self.deep_output(self.deep(x))
        
        # Combine
        combined = torch.cat([fm_first, deep_out], dim=1)
        predictions = torch.cat([head(combined) for head in self.task_heads], dim=1)
        
        return predictions
    
    def count_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


class DCNv2(nn.Module):
    """DCNv2: Deep & Cross Network V2."""
    def __init__(self, input_dim: int, num_cross_layers: int = 3,
                 hidden_dims: List[int] = [128, 64], dropout: float = 0.1, num_tasks: int = 1):
        super().__init__()
        self.num_cross_layers = num_cross_layers
        
        # Cross network
        self.cross_w = nn.ParameterList([nn.Parameter(torch.randn(input_dim, input_dim)) 
                                         for _ in range(num_cross_layers)])
        self.cross_b = nn.ParameterList([nn.Parameter(torch.zeros(input_dim)) 
                                         for _ in range(num_cross_layers)])
        
        # Deep network
        layers = []
        current_dim = input_dim
        for hidden_dim in hidden_dims:
            layers.extend([
                nn.Linear(current_dim, hidden_dim),
                nn.ReLU(),
                nn.Dropout(dropout)
            ])
            current_dim = hidden_dim
        
        self.deep = nn.Sequential(*layers)
        self.combination = nn.Linear(input_dim + current_dim, 64)
        
        # Task heads
        self.task_heads = nn.ModuleList([
            nn.Sequential(nn.ReLU(), nn.Linear(64, 1)) for _ in range(num_tasks)
        ])
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Cross network
        x0 = x
        x_cross = x0
        for i in range(self.num_cross_layers):
            x_cross = x0 * (torch.matmul(x_cross, self.cross_w[i]) + self.cross_b[i]) + x_cross
        
        # Deep network
        x_deep = self.deep(x0)
        
        # Combine
        combined = torch.cat([x_cross, x_deep], dim=1)
        combined = self.combination(combined)
        
        predictions = torch.cat([head(combined) for head in self.task_heads], dim=1)
        return predictions
    
    def count_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


class AutoInt(nn.Module):
    """AutoInt: Automatic Feature Interaction Learning."""
    def __init__(self, input_dim: int, num_heads: int = 2, num_layers: int = 2,
                 hidden_dim: int = 128, dropout: float = 0.1, num_tasks: int = 1):
        super().__init__()
        
        # Input projection
        self.input_proj = nn.Linear(input_dim, hidden_dim)
        
        # Multi-head self-attention layers
        self.attention_layers = nn.ModuleList([
            nn.MultiheadAttention(hidden_dim, num_heads, dropout=dropout, batch_first=True)
            for _ in range(num_layers)
        ])
        
        self.norms = nn.ModuleList([nn.LayerNorm(hidden_dim) for _ in range(num_layers)])
        
        # Output MLP
        self.mlp = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim // 2, hidden_dim // 4),
            nn.ReLU(),
            nn.Dropout(dropout)
        )
        
        # Task heads
        self.task_heads = nn.ModuleList([nn.Linear(hidden_dim // 4, 1) for _ in range(num_tasks)])
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch_size = x.shape[0]
        
        # Project input
        x = self.input_proj(x).unsqueeze(1)  # (B, 1, hidden_dim)
        
        # Apply attention layers
        for attn, norm in zip(self.attention_layers, self.norms):
            attn_out, _ = attn(x, x, x)
            x = norm(x + attn_out)
        
        # Flatten and pass through MLP
        x = x.squeeze(1)
        x = self.mlp(x)
        
        predictions = torch.cat([head(x) for head in self.task_heads], dim=1)
        return predictions
    
    def count_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


class MoERankingModel(nn.Module):
    """MoE (Mixture of Experts) baseline model."""
    def __init__(self, input_dim: int, hidden_dim: int = 256, num_experts: int = 4,
                 top_k: int = 2, dropout: float = 0.1, num_tasks: int = 1):
        super().__init__()
        self.num_experts = num_experts
        self.top_k = top_k
        
        # Input projection
        self.input_proj = nn.Linear(input_dim, hidden_dim)
        
        # Expert networks
        self.experts = nn.ModuleList([
            nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(hidden_dim, hidden_dim)
            ) for _ in range(num_experts)
        ])
        
        # Gating network
        self.gate = nn.Linear(hidden_dim, num_experts)
        
        # Output layers
        self.output_layers = nn.ModuleList([
            nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim // 2),
                nn.ReLU(),
                nn.Linear(hidden_dim // 2, 1)
            ) for _ in range(num_tasks)
        ])
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.input_proj(x)
        
        # Compute gating weights
        gate_logits = self.gate(x)
        gates = F.softmax(gate_logits, dim=-1)
        
        # Top-k gating
        top_gates, top_indices = torch.topk(gates, self.top_k, dim=-1)
        top_gates = top_gates / top_gates.sum(dim=-1, keepdim=True)
        
        # Compute expert outputs
        expert_outputs = torch.stack([expert(x) for expert in self.experts], dim=1)
        
        # Weighted combination
        output = torch.zeros_like(x)
        for i in range(self.top_k):
            expert_idx = top_indices[:, i:i+1].unsqueeze(-1).expand(-1, -1, x.shape[-1])
            selected_experts = torch.gather(expert_outputs, 1, expert_idx).squeeze(1)
            output += top_gates[:, i:i+1] * selected_experts
        
        predictions = torch.cat([head(output) for head in self.output_layers], dim=1)
        return predictions
    
    def count_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


def create_baseline_models(input_dim: int = 624, num_tasks: int = 1):
    """Create all baseline models with similar parameter counts."""
    models = {
        'MLP-Small': MLPRankingModel(input_dim, hidden_dims=[128, 64], dropout=0.1, num_tasks=num_tasks),
        'MLP-Base': MLPRankingModel(input_dim, hidden_dims=[256, 128, 64], dropout=0.1, num_tasks=num_tasks),
        'DeepFM': DeepFM(input_dim, num_features=39, embed_dim=16, hidden_dims=[128, 64], dropout=0.1, num_tasks=num_tasks),
        'DCNv2': DCNv2(input_dim, num_cross_layers=2, hidden_dims=[128, 64], dropout=0.1, num_tasks=num_tasks),
        'AutoInt': AutoInt(input_dim, num_heads=2, num_layers=2, hidden_dim=128, dropout=0.1, num_tasks=num_tasks),
        'MoE': MoERankingModel(input_dim, hidden_dim=128, num_experts=4, top_k=2, dropout=0.1, num_tasks=num_tasks)
    }
    return models


if __name__ == "__main__":
    print("Testing baseline models...")
    
    input_dim = 624
    batch_size = 4
    
    x = torch.randn(batch_size, input_dim)
    models = create_baseline_models(input_dim, num_tasks=1)
    
    for name, model in models.items():
        params = model.count_parameters()
        output = model(x)
        print(f"{name}: {params:,} parameters, output shape: {output.shape}")
    
    print("\nAll baseline tests passed!")
