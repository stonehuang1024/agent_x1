"""
Baseline models for comparison with RankMixer.
Includes: DCNv2, DeepFM, AutoInt, and simple MLP baseline.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import List, Optional


class MLPRankingModel(nn.Module):
    """
    Simple MLP baseline for recommendation ranking.
    Similar to DLRM-MLP.
    """
    def __init__(self, num_features: int, feature_dims: List[int],
                 hidden_dims: List[int] = [256, 128, 64], dropout: float = 0.1,
                 num_tasks: int = 1):
        super().__init__()
        self.num_features = num_features
        
        # Calculate input dimension
        total_emb_dim = sum(abs(d) for d in feature_dims)
        
        # MLP layers
        layers = []
        input_dim = total_emb_dim
        for hidden_dim in hidden_dims:
            layers.extend([
                nn.Linear(input_dim, hidden_dim),
                nn.ReLU(),
                nn.Dropout(dropout)
            ])
            input_dim = hidden_dim
        
        self.mlp = nn.Sequential(*layers)
        
        # Output heads
        self.task_heads = nn.ModuleList([
            nn.Linear(input_dim, 1) for _ in range(num_tasks)
        ])
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.mlp(x)
        predictions = torch.cat([head(x) for head in self.task_heads], dim=1)
        return predictions
    
    def count_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


class DeepFM(nn.Module):
    """
    DeepFM: Deep Factorization Machine.
    Combines FM component and Deep component.
    Reference: https://arxiv.org/abs/1703.04247
    """
    def __init__(self, num_features: int, feature_dims: List[int],
                 embed_dim: int = 16, hidden_dims: List[int] = [256, 128],
                 dropout: float = 0.1, num_tasks: int = 1):
        super().__init__()
        self.num_features = num_features
        self.embed_dim = embed_dim
        
        # FM component - linear part
        self.fm_first_order = nn.Linear(num_features, 1)
        
        # FM component - second order embeddings
        self.fm_embeddings = nn.ModuleList([
            nn.Embedding(10000, embed_dim) for _ in range(num_features)
        ])
        
        # Deep component
        input_dim = num_features * embed_dim
        layers = []
        for hidden_dim in hidden_dims:
            layers.extend([
                nn.Linear(input_dim, hidden_dim),
                nn.ReLU(),
                nn.Dropout(dropout)
            ])
            input_dim = hidden_dim
        
        self.deep = nn.Sequential(*layers)
        self.deep_output = nn.Linear(input_dim, 1)
        
        # Task heads
        self.task_heads = nn.ModuleList([
            nn.Linear(2, 1) for _ in range(num_tasks)  # FM + Deep
        ])
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch_size = x.shape[0]
        
        # FM first order
        fm_first = self.fm_first_order(x)
        
        # FM second order
        # For simplicity, assume x contains indices for embedding lookup
        # In practice, this would be more complex
        fm_second = 0
        if x.dim() == 2:
            # Convert to indices (simplified)
            x_indices = (x * 100).long().clamp(0, 9999)
            embeddings = []
            for i in range(min(self.num_features, x.shape[1])):
                emb = self.fm_embeddings[i](x_indices[:, i])
                embeddings.append(emb)
            
            if embeddings:
                embeddings = torch.stack(embeddings, dim=1)  # (B, F, E)
                # Sum of squares - square of sums
                sum_square = embeddings.sum(dim=1).pow(2).sum(dim=1, keepdim=True)
                square_sum = embeddings.pow(2).sum(dim=1).sum(dim=1, keepdim=True)
                fm_second = 0.5 * (sum_square - square_sum)
        
        # Deep component
        deep_input = x.view(batch_size, -1)
        deep_out = self.deep_output(self.deep(deep_input))
        
        # Combine
        combined = torch.cat([fm_first, deep_out], dim=1)
        predictions = torch.cat([head(combined) for head in self.task_heads], dim=1)
        
        return predictions
    
    def count_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


class DCNv2(nn.Module):
    """
    DCNv2: Deep & Cross Network V2.
    Reference: https://arxiv.org/abs/2008.13535
    """
    def __init__(self, num_features: int, feature_dims: List[int],
                 embed_dim: int = 16, num_cross_layers: int = 3,
                 hidden_dims: List[int] = [256, 128], dropout: float = 0.1,
                 num_tasks: int = 1):
        super().__init__()
        self.num_features = num_features
        self.num_cross_layers = num_cross_layers
        
        # Input dimension
        input_dim = num_features * embed_dim
        
        # Cross network layers
        self.cross_w = nn.ParameterList([
            nn.Parameter(torch.randn(input_dim, input_dim)) 
            for _ in range(num_cross_layers)
        ])
        self.cross_b = nn.ParameterList([
            nn.Parameter(torch.zeros(input_dim))
            for _ in range(num_cross_layers)
        ])
        
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
        
        # Combination layer
        self.combination = nn.Linear(input_dim + current_dim, 64)
        
        # Task heads
        self.task_heads = nn.ModuleList([
            nn.Sequential(
                nn.ReLU(),
                nn.Linear(64, 1)
            ) for _ in range(num_tasks)
        ])
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch_size = x.shape[0]
        
        # Flatten input
        x0 = x.view(batch_size, -1)  # (B, input_dim)
        x_cross = x0
        
        # Cross layers
        for i in range(self.num_cross_layers):
            # x_l+1 = x0 * (W * x_l + b) + x_l
            x_cross = x0 * (torch.matmul(x_cross, self.cross_w[i]) + self.cross_b[i]) + x_cross
        
        # Deep network
        x_deep = self.deep(x0)
        
        # Concatenate and combine
        combined = torch.cat([x_cross, x_deep], dim=1)
        combined = self.combination(combined)
        
        # Task predictions
        predictions = torch.cat([head(combined) for head in self.task_heads], dim=1)
        
        return predictions
    
    def count_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


class AutoInt(nn.Module):
    """
    AutoInt: Automatic Feature Interaction Learning.
    Uses multi-head self-attention for feature interaction.
    Reference: https://arxiv.org/abs/1810.11921
    """
    def __init__(self, num_features: int, feature_dims: List[int],
                 embed_dim: int = 16, num_heads: int = 2, num_layers: int = 2,
                 hidden_dim: int = 128, dropout: float = 0.1, num_tasks: int = 1):
        super().__init__()
        self.num_features = num_features
        self.embed_dim = embed_dim
        
        # Feature embeddings
        self.embeddings = nn.ModuleList([
            nn.Linear(1, embed_dim) for _ in range(num_features)
        ])
        
        # Multi-head self-attention layers
        self.attention_layers = nn.ModuleList([
            nn.MultiheadAttention(embed_dim, num_heads, dropout=dropout, batch_first=True)
            for _ in range(num_layers)
        ])
        
        self.norms = nn.ModuleList([
            nn.LayerNorm(embed_dim) for _ in range(num_layers)
        ])
        
        # Output MLP
        input_dim = num_features * embed_dim
        self.mlp = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Dropout(dropout)
        )
        
        # Task heads
        self.task_heads = nn.ModuleList([
            nn.Linear(hidden_dim // 2, 1) for _ in range(num_tasks)
        ])
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch_size = x.shape[0]
        
        # Embed features
        embeddings = []
        for i in range(min(self.num_features, x.shape[1])):
            feat = x[:, i:i+1]
            emb = self.embeddings[i](feat)
            embeddings.append(emb)
        
        # Stack embeddings: (B, F, E)
        x = torch.stack(embeddings, dim=1)
        
        # Apply attention layers
        for attn, norm in zip(self.attention_layers, self.norms):
            attn_out, _ = attn(x, x, x)
            x = norm(x + attn_out)
        
        # Flatten and pass through MLP
        x = x.view(batch_size, -1)
        x = self.mlp(x)
        
        # Task predictions
        predictions = torch.cat([head(x) for head in self.task_heads], dim=1)
        
        return predictions
    
    def count_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


class MoERankingModel(nn.Module):
    """
    MoE (Mixture of Experts) baseline model.
    """
    def __init__(self, num_features: int, feature_dims: List[int],
                 hidden_dim: int = 256, num_experts: int = 4, 
                 top_k: int = 2, num_layers: int = 2, dropout: float = 0.1,
                 num_tasks: int = 1):
        super().__init__()
        self.num_experts = num_experts
        self.top_k = top_k
        
        # Input projection
        total_emb_dim = sum(abs(d) for d in feature_dims)
        self.input_proj = nn.Linear(total_emb_dim, hidden_dim)
        
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
        
        # Task predictions
        predictions = torch.cat([head(output) for head in self.output_layers], dim=1)
        
        return predictions
    
    def count_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


def create_baseline_models(num_features: int = 39, num_tasks: int = 1):
    """
    Create all baseline models with similar parameter counts for fair comparison.
    """
    feature_dims = [16] * num_features
    
    models = {
        'MLP-Small': MLPRankingModel(
            num_features, feature_dims, 
            hidden_dims=[128, 64], dropout=0.1, num_tasks=num_tasks
        ),
        'MLP-Base': MLPRankingModel(
            num_features, feature_dims,
            hidden_dims=[256, 128, 64], dropout=0.1, num_tasks=num_tasks
        ),
        'DeepFM': DeepFM(
            num_features, feature_dims,
            embed_dim=16, hidden_dims=[128, 64], dropout=0.1, num_tasks=num_tasks
        ),
        'DCNv2': DCNv2(
            num_features, feature_dims,
            embed_dim=16, num_cross_layers=2, hidden_dims=[128, 64],
            dropout=0.1, num_tasks=num_tasks
        ),
        'AutoInt': AutoInt(
            num_features, feature_dims,
            embed_dim=16, num_heads=2, num_layers=2,
            hidden_dim=128, dropout=0.1, num_tasks=num_tasks
        ),
        'MoE': MoERankingModel(
            num_features, feature_dims,
            hidden_dim=128, num_experts=4, top_k=2,
            num_layers=2, dropout=0.1, num_tasks=num_tasks
        )
    }
    
    return models


if __name__ == "__main__":
    print("Testing baseline models...")
    
    num_features = 39
    batch_size = 4
    
    # Create test input
    x = torch.randn(batch_size, num_features * 16)
    
    models = create_baseline_models(num_features, num_tasks=1)
    
    for name, model in models.items():
        params = model.count_parameters()
        output = model(x)
        print(f"{name}: {params:,} parameters, output shape: {output.shape}")
    
    print("\nAll baseline tests passed!")
