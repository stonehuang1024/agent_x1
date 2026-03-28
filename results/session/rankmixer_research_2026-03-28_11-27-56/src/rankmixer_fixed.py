"""
RankMixer: Scaling Up Ranking Models in Industrial Recommenders
Paper: https://arxiv.org/abs/2507.15551

Fixed implementation with proper input handling.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Optional, List, Tuple


class MultiHeadTokenMixing(nn.Module):
    """Multi-head Token Mixing module."""
    def __init__(self, num_tokens: int, hidden_dim: int, num_heads: Optional[int] = None):
        super().__init__()
        self.num_tokens = num_tokens
        self.hidden_dim = hidden_dim
        self.num_heads = num_heads if num_heads is not None else num_tokens
        
        assert hidden_dim % self.num_heads == 0, \
            f"hidden_dim {hidden_dim} must be divisible by num_heads {self.num_heads}"
        
        self.head_dim = hidden_dim // self.num_heads
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch_size = x.shape[0]
        # Split each token into H heads: (B, T, D) -> (B, T, H, D//H)
        x = x.view(batch_size, self.num_tokens, self.num_heads, self.head_dim)
        # Transpose to (B, H, T, D//H) then reshape to (B, H, T*D//H)
        x = x.permute(0, 2, 1, 3).contiguous()
        x = x.view(batch_size, self.num_heads, -1)
        return x


class PerTokenFFN(nn.Module):
    """Per-token Feed-Forward Network."""
    def __init__(self, num_tokens: int, hidden_dim: int, ffn_ratio: float = 4.0, dropout: float = 0.1):
        super().__init__()
        self.num_tokens = num_tokens
        self.hidden_dim = hidden_dim
        self.ffn_hidden_dim = int(hidden_dim * ffn_ratio)
        
        # Each token has its own FFN parameters
        self.fc1 = nn.Parameter(torch.randn(num_tokens, hidden_dim, self.ffn_hidden_dim))
        self.bias1 = nn.Parameter(torch.zeros(num_tokens, self.ffn_hidden_dim))
        self.fc2 = nn.Parameter(torch.randn(num_tokens, self.ffn_hidden_dim, hidden_dim))
        self.bias2 = nn.Parameter(torch.zeros(num_tokens, hidden_dim))
        
        self.dropout = nn.Dropout(dropout)
        self._init_weights()
        
    def _init_weights(self):
        for i in range(self.num_tokens):
            nn.init.xavier_uniform_(self.fc1[i])
            nn.init.xavier_uniform_(self.fc2[i])
            
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch_size = x.shape[0]
        outputs = []
        for t in range(self.num_tokens):
            token = x[:, t, :]
            hidden = torch.matmul(token, self.fc1[t]) + self.bias1[t]
            hidden = F.gelu(hidden)
            hidden = self.dropout(hidden)
            output = torch.matmul(hidden, self.fc2[t]) + self.bias2[t]
            outputs.append(output)
        output = torch.stack(outputs, dim=1)
        return output


class SparseMoEPerTokenFFN(nn.Module):
    """Sparse Mixture-of-Experts variant of Per-token FFN."""
    def __init__(self, num_tokens: int, hidden_dim: int, num_experts: int = 4,
                 ffn_ratio: float = 4.0, dropout: float = 0.1, sparsity_weight: float = 0.01):
        super().__init__()
        self.num_tokens = num_tokens
        self.hidden_dim = hidden_dim
        self.num_experts = num_experts
        self.ffn_hidden_dim = int(hidden_dim * ffn_ratio)
        self.sparsity_weight = sparsity_weight
        
        # Experts
        self.expert_fc1 = nn.Parameter(torch.randn(num_tokens, num_experts, hidden_dim, self.ffn_hidden_dim))
        self.expert_bias1 = nn.Parameter(torch.zeros(num_tokens, num_experts, self.ffn_hidden_dim))
        self.expert_fc2 = nn.Parameter(torch.randn(num_tokens, num_experts, self.ffn_hidden_dim, hidden_dim))
        self.expert_bias2 = nn.Parameter(torch.zeros(num_tokens, num_experts, hidden_dim))
        
        # Routers
        self.router_train = nn.ModuleList([nn.Linear(hidden_dim, num_experts) for _ in range(num_tokens)])
        self.router_infer = nn.ModuleList([nn.Linear(hidden_dim, num_experts) for _ in range(num_tokens)])
        
        self.dropout = nn.Dropout(dropout)
        self.training_mode = True
        self._init_weights()
        
    def _init_weights(self):
        for t in range(self.num_tokens):
            for e in range(self.num_experts):
                nn.init.xavier_uniform_(self.expert_fc1[t, e])
                nn.init.xavier_uniform_(self.expert_fc2[t, e])
                
    def set_training_mode(self, mode: bool):
        self.training_mode = mode
        
    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        batch_size = x.shape[0]
        outputs = []
        total_gate_sum = 0.0
        
        router = self.router_train if self.training_mode else self.router_infer
        
        for t in range(self.num_tokens):
            token = x[:, t, :]
            gates = F.relu(router[t](token))
            total_gate_sum += gates.sum()
            
            expert_outputs = []
            for e in range(self.num_experts):
                hidden = torch.matmul(token, self.expert_fc1[t, e]) + self.expert_bias1[t, e]
                hidden = F.gelu(hidden)
                hidden = self.dropout(hidden)
                expert_out = torch.matmul(hidden, self.expert_fc2[t, e]) + self.expert_bias2[t, e]
                expert_outputs.append(expert_out)
            
            expert_outputs = torch.stack(expert_outputs, dim=1)
            gates = gates.unsqueeze(-1)
            output = (expert_outputs * gates).sum(dim=1)
            outputs.append(output)
        
        output = torch.stack(outputs, dim=1)
        aux_loss = self.sparsity_weight * total_gate_sum / (batch_size * self.num_tokens)
        
        return output, aux_loss


class RankMixerBlock(nn.Module):
    """Single RankMixer block."""
    def __init__(self, num_tokens: int, hidden_dim: int, num_heads: Optional[int] = None,
                 ffn_ratio: float = 4.0, dropout: float = 0.1, use_sparse_moe: bool = False,
                 num_experts: int = 4):
        super().__init__()
        self.num_tokens = num_tokens
        self.hidden_dim = hidden_dim
        self.use_sparse_moe = use_sparse_moe
        
        # Multi-head Token Mixing
        self.token_mixing = MultiHeadTokenMixing(num_tokens, hidden_dim, num_heads)
        
        # Projection layer
        num_heads_actual = num_heads if num_heads is not None else num_tokens
        mixed_dim = num_tokens * hidden_dim // num_heads_actual
        self.mixing_proj = nn.Linear(mixed_dim, hidden_dim)
        
        # Per-token FFN or Sparse MoE
        if use_sparse_moe:
            self.pffn = SparseMoEPerTokenFFN(num_tokens, hidden_dim, num_experts, ffn_ratio, dropout)
        else:
            self.pffn = PerTokenFFN(num_tokens, hidden_dim, ffn_ratio, dropout)
        
        self.norm1 = nn.LayerNorm(hidden_dim)
        self.norm2 = nn.LayerNorm(hidden_dim)
        self.dropout = nn.Dropout(dropout)
        
    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        batch_size = x.shape[0]
        
        # Token Mixing
        mixed = self.token_mixing(x)
        num_heads = mixed.shape[1]
        
        mixed_flat = mixed.view(batch_size * num_heads, -1)
        projected = self.mixing_proj(mixed_flat)
        projected = projected.view(batch_size, num_heads, self.hidden_dim)
        
        if num_heads == self.num_tokens:
            mixed_output = projected
        else:
            mixed_output = projected.mean(dim=1, keepdim=True).expand(-1, self.num_tokens, -1)
        
        x = self.norm1(x + self.dropout(mixed_output))
        
        # Per-token FFN
        if self.use_sparse_moe:
            ffn_out, aux_loss = self.pffn(x)
        else:
            ffn_out = self.pffn(x)
            aux_loss = None
            
        x = self.norm2(x + self.dropout(ffn_out))
        
        return x, aux_loss


class RankMixer(nn.Module):
    """Complete RankMixer model for recommendation ranking."""
    def __init__(self, input_dim: int, num_tokens: int = 16,
                 hidden_dim: int = 256, num_layers: int = 2, num_heads: Optional[int] = None,
                 ffn_ratio: float = 4.0, dropout: float = 0.1, use_sparse_moe: bool = False,
                 num_experts: int = 4, num_tasks: int = 1):
        super().__init__()
        self.num_tokens = num_tokens
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.use_sparse_moe = use_sparse_moe
        self.num_tasks = num_tasks
        
        # Input projection to tokens
        self.token_proj = nn.Linear(input_dim, num_tokens * hidden_dim)
        
        # RankMixer blocks
        self.blocks = nn.ModuleList([
            RankMixerBlock(num_tokens, hidden_dim, num_heads, ffn_ratio, dropout, use_sparse_moe, num_experts)
            for _ in range(num_layers)
        ])
        
        # Output heads
        self.task_heads = nn.ModuleList([
            nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim // 2),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(hidden_dim // 2, 1)
            ) for _ in range(num_tasks)
        ])
        
    def forward(self, features: torch.Tensor) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        batch_size = features.shape[0]
        
        # Tokenization: (B, input_dim) -> (B, T*D) -> (B, T, D)
        x = self.token_proj(features)
        x = x.view(batch_size, self.num_tokens, self.hidden_dim)
        
        # Pass through RankMixer blocks
        total_aux_loss = 0.0
        for block in self.blocks:
            x, aux_loss = block(x)
            if aux_loss is not None:
                total_aux_loss += aux_loss
        
        # Mean pooling
        pooled = x.mean(dim=1)
        
        # Task predictions
        predictions = torch.cat([head(pooled) for head in self.task_heads], dim=1)
        
        return predictions, total_aux_loss if self.use_sparse_moe else None
    
    def count_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


def create_rankmixer_small(input_dim: int = 624, num_tasks: int = 1) -> RankMixer:
    """Create a small RankMixer model: D=128, T=8, L=2"""
    return RankMixer(
        input_dim=input_dim,
        num_tokens=8,
        hidden_dim=128,
        num_layers=2,
        ffn_ratio=2.0,
        dropout=0.1,
        use_sparse_moe=False,
        num_tasks=num_tasks
    )


def create_rankmixer_base(input_dim: int = 624, num_tasks: int = 1) -> RankMixer:
    """Create a base RankMixer model: D=256, T=16, L=2"""
    return RankMixer(
        input_dim=input_dim,
        num_tokens=16,
        hidden_dim=256,
        num_layers=2,
        ffn_ratio=4.0,
        dropout=0.1,
        use_sparse_moe=False,
        num_tasks=num_tasks
    )


def create_rankmixer_moe(input_dim: int = 624, num_tasks: int = 1) -> RankMixer:
    """Create a RankMixer model with Sparse MoE: D=256, T=16, L=2, E=4"""
    return RankMixer(
        input_dim=input_dim,
        num_tokens=16,
        hidden_dim=256,
        num_layers=2,
        ffn_ratio=4.0,
        dropout=0.1,
        use_sparse_moe=True,
        num_experts=4,
        num_tasks=num_tasks
    )


if __name__ == "__main__":
    print("Testing RankMixer implementation...")
    
    # Test with 39 features * 16 dim = 624 input
    input_dim = 624
    
    # Small model
    model = create_rankmixer_small(input_dim, num_tasks=1)
    print(f"Small model parameters: {model.count_parameters():,}")
    
    x = torch.randn(4, input_dim)
    output, aux_loss = model(x)
    print(f"Output shape: {output.shape}, Aux loss: {aux_loss}")
    
    # Base model
    model_base = create_rankmixer_base(input_dim, num_tasks=1)
    print(f"\nBase model parameters: {model_base.count_parameters():,}")
    
    # MoE model
    model_moe = create_rankmixer_moe(input_dim, num_tasks=1)
    print(f"MoE model parameters: {model_moe.count_parameters():,}")
    
    print("\nAll tests passed!")
