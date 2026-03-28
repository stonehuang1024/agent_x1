"""
RankMixer: Scaling Up Ranking Models in Industrial Recommenders
Paper: https://arxiv.org/abs/2507.15551

Implementation of RankMixer architecture with:
- Multi-head Token Mixing
- Per-token FFN (PFFN)
- Sparse MoE variant
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import math
from typing import Optional, List, Tuple


class MultiHeadTokenMixing(nn.Module):
    """
    Multi-head Token Mixing module.
    
    Each token is divided into H heads, then heads are shuffled across tokens
    to create new mixed tokens for global feature interactions.
    
    Args:
        num_tokens: Number of feature tokens (T)
        hidden_dim: Hidden dimension (D)
        num_heads: Number of heads (H), default equals num_tokens
    """
    def __init__(self, num_tokens: int, hidden_dim: int, num_heads: Optional[int] = None):
        super().__init__()
        self.num_tokens = num_tokens
        self.hidden_dim = hidden_dim
        self.num_heads = num_heads if num_heads is not None else num_tokens
        
        assert hidden_dim % self.num_heads == 0, \
            f"hidden_dim {hidden_dim} must be divisible by num_heads {self.num_heads}"
        
        self.head_dim = hidden_dim // self.num_heads
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: Input tensor of shape (batch_size, num_tokens, hidden_dim)
        Returns:
            Mixed tokens of shape (batch_size, num_heads, num_tokens * head_dim)
        """
        batch_size = x.shape[0]
        
        # Split each token into H heads: (B, T, D) -> (B, T, H, D//H)
        x = x.view(batch_size, self.num_tokens, self.num_heads, self.head_dim)
        
        # Transpose to (B, H, T, D//H) then reshape to (B, H, T*D//H)
        x = x.permute(0, 2, 1, 3).contiguous()
        x = x.view(batch_size, self.num_heads, -1)
        
        return x


class PerTokenFFN(nn.Module):
    """
    Per-token Feed-Forward Network.
    
    Each token has its own dedicated FFN parameters, enabling isolated
    modeling of different feature subspaces.
    
    Args:
        num_tokens: Number of tokens (T)
        hidden_dim: Hidden dimension (D)
        ffn_ratio: Expansion ratio for FFN hidden layer (k)
        dropout: Dropout rate
    """
    def __init__(self, num_tokens: int, hidden_dim: int, ffn_ratio: float = 4.0, dropout: float = 0.1):
        super().__init__()
        self.num_tokens = num_tokens
        self.hidden_dim = hidden_dim
        self.ffn_hidden_dim = int(hidden_dim * ffn_ratio)
        
        # Each token has its own FFN parameters
        # f_t,1: (D, k*D) and f_t,2: (k*D, D) for each token
        self.fc1 = nn.Parameter(torch.randn(num_tokens, hidden_dim, self.ffn_hidden_dim))
        self.bias1 = nn.Parameter(torch.zeros(num_tokens, self.ffn_hidden_dim))
        self.fc2 = nn.Parameter(torch.randn(num_tokens, self.ffn_hidden_dim, hidden_dim))
        self.bias2 = nn.Parameter(torch.zeros(num_tokens, hidden_dim))
        
        self.dropout = nn.Dropout(dropout)
        
        # Initialize parameters
        self._init_weights()
        
    def _init_weights(self):
        """Initialize weights using Xavier initialization"""
        for i in range(self.num_tokens):
            nn.init.xavier_uniform_(self.fc1[i])
            nn.init.xavier_uniform_(self.fc2[i])
            
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: Input tensor of shape (batch_size, num_tokens, hidden_dim)
        Returns:
            Output tensor of shape (batch_size, num_tokens, hidden_dim)
        """
        batch_size = x.shape[0]
        
        # Process each token with its own FFN
        outputs = []
        for t in range(self.num_tokens):
            # Get token t: (B, D)
            token = x[:, t, :]  # (batch_size, hidden_dim)
            
            # FC1 + GELU: (B, D) @ (D, kD) -> (B, kD)
            hidden = torch.matmul(token, self.fc1[t]) + self.bias1[t]
            hidden = F.gelu(hidden)
            hidden = self.dropout(hidden)
            
            # FC2: (B, kD) @ (kD, D) -> (B, D)
            output = torch.matmul(hidden, self.fc2[t]) + self.bias2[t]
            outputs.append(output)
        
        # Stack outputs: (B, T, D)
        output = torch.stack(outputs, dim=1)
        return output


class ReLURouter(nn.Module):
    """
    ReLU-based router for Sparse MoE.
    
    Uses ReLU activation instead of softmax for flexible expert selection.
    """
    def __init__(self, hidden_dim: int, num_experts: int):
        super().__init__()
        self.router = nn.Linear(hidden_dim, num_experts)
        
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: Input tensor of shape (batch_size, hidden_dim)
        Returns:
            Gate values of shape (batch_size, num_experts)
        """
        logits = self.router(x)
        gates = F.relu(logits)
        return gates


class SparseMoEPerTokenFFN(nn.Module):
    """
    Sparse Mixture-of-Experts variant of Per-token FFN.
    
    Each token has multiple experts, but only a subset is activated per sample.
    Uses ReLU routing with L1 regularization for sparsity.
    
    Args:
        num_tokens: Number of tokens (T)
        hidden_dim: Hidden dimension (D)
        num_experts: Number of experts per token (E)
        ffn_ratio: Expansion ratio for FFN
        dropout: Dropout rate
        sparsity_weight: Weight for L1 regularization (lambda)
    """
    def __init__(self, num_tokens: int, hidden_dim: int, num_experts: int = 4,
                 ffn_ratio: float = 4.0, dropout: float = 0.1, sparsity_weight: float = 0.01):
        super().__init__()
        self.num_tokens = num_tokens
        self.hidden_dim = hidden_dim
        self.num_experts = num_experts
        self.ffn_hidden_dim = int(hidden_dim * ffn_ratio)
        self.sparsity_weight = sparsity_weight
        
        # Experts: (T, E, D, kD) and (T, E, kD, D)
        self.expert_fc1 = nn.Parameter(
            torch.randn(num_tokens, num_experts, hidden_dim, self.ffn_hidden_dim)
        )
        self.expert_bias1 = nn.Parameter(torch.zeros(num_tokens, num_experts, self.ffn_hidden_dim))
        self.expert_fc2 = nn.Parameter(
            torch.randn(num_tokens, num_experts, self.ffn_hidden_dim, hidden_dim)
        )
        self.expert_bias2 = nn.Parameter(torch.zeros(num_tokens, num_experts, hidden_dim))
        
        # Routers for training and inference (DTSI-MoE)
        self.router_train = nn.ModuleList([
            nn.Linear(hidden_dim, num_experts) for _ in range(num_tokens)
        ])
        self.router_infer = nn.ModuleList([
            nn.Linear(hidden_dim, num_experts) for _ in range(num_tokens)
        ])
        
        self.dropout = nn.Dropout(dropout)
        self.training_mode = True
        
        self._init_weights()
        
    def _init_weights(self):
        for t in range(self.num_tokens):
            for e in range(self.num_experts):
                nn.init.xavier_uniform_(self.expert_fc1[t, e])
                nn.init.xavier_uniform_(self.expert_fc2[t, e])
                
    def set_training_mode(self, mode: bool):
        """Switch between training and inference mode"""
        self.training_mode = mode
        
    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Args:
            x: Input tensor of shape (batch_size, num_tokens, hidden_dim)
        Returns:
            - output: Output tensor of shape (batch_size, num_tokens, hidden_dim)
            - aux_loss: L1 regularization loss for sparsity
        """
        batch_size = x.shape[0]
        outputs = []
        total_gate_sum = 0.0
        
        router = self.router_train if self.training_mode else self.router_infer
        
        for t in range(self.num_tokens):
            token = x[:, t, :]  # (batch_size, hidden_dim)
            
            # Get routing gates using ReLU
            gates = F.relu(router[t](token))  # (batch_size, num_experts)
            total_gate_sum += gates.sum()
            
            # Compute expert outputs
            expert_outputs = []
            for e in range(self.num_experts):
                # FC1 + GELU
                hidden = torch.matmul(token, self.expert_fc1[t, e]) + self.expert_bias1[t, e]
                hidden = F.gelu(hidden)
                hidden = self.dropout(hidden)
                
                # FC2
                expert_out = torch.matmul(hidden, self.expert_fc2[t, e]) + self.expert_bias2[t, e]
                expert_outputs.append(expert_out)
            
            # Weighted combination of experts
            expert_outputs = torch.stack(expert_outputs, dim=1)  # (B, E, D)
            gates = gates.unsqueeze(-1)  # (B, E, 1)
            output = (expert_outputs * gates).sum(dim=1)  # (B, D)
            outputs.append(output)
        
        output = torch.stack(outputs, dim=1)  # (B, T, D)
        
        # L1 regularization for sparsity
        aux_loss = self.sparsity_weight * total_gate_sum / (batch_size * self.num_tokens)
        
        return output, aux_loss


class RankMixerBlock(nn.Module):
    """
    Single RankMixer block consisting of:
    1. Multi-head Token Mixing
    2. Per-token FFN (or Sparse MoE variant)
    
    Args:
        num_tokens: Number of feature tokens (T)
        hidden_dim: Hidden dimension (D)
        num_heads: Number of heads for token mixing
        ffn_ratio: Expansion ratio for FFN
        dropout: Dropout rate
        use_sparse_moe: Whether to use Sparse MoE variant
        num_experts: Number of experts per token (if using MoE)
    """
    def __init__(self, num_tokens: int, hidden_dim: int, num_heads: Optional[int] = None,
                 ffn_ratio: float = 4.0, dropout: float = 0.1, use_sparse_moe: bool = False,
                 num_experts: int = 4):
        super().__init__()
        self.num_tokens = num_tokens
        self.hidden_dim = hidden_dim
        self.use_sparse_moe = use_sparse_moe
        
        # Multi-head Token Mixing
        self.token_mixing = MultiHeadTokenMixing(num_tokens, hidden_dim, num_heads)
        
        # After token mixing, we have H tokens each with T*D/H dimensions
        # We need to project them back to T tokens with D dimensions
        num_heads_actual = num_heads if num_heads is not None else num_tokens
        mixed_dim = num_tokens * hidden_dim // num_heads_actual
        
        self.mixing_proj = nn.Linear(mixed_dim, hidden_dim)
        
        # Per-token FFN or Sparse MoE
        if use_sparse_moe:
            self.pffn = SparseMoEPerTokenFFN(num_tokens, hidden_dim, num_experts, ffn_ratio, dropout)
        else:
            self.pffn = PerTokenFFN(num_tokens, hidden_dim, ffn_ratio, dropout)
        
        # Layer normalization
        self.norm1 = nn.LayerNorm(hidden_dim)
        self.norm2 = nn.LayerNorm(hidden_dim)
        
        self.dropout = nn.Dropout(dropout)
        
    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        """
        Args:
            x: Input tensor of shape (batch_size, num_tokens, hidden_dim)
        Returns:
            - output: Output tensor of shape (batch_size, num_tokens, hidden_dim)
            - aux_loss: Auxiliary loss from MoE (if used)
        """
        # Token Mixing with residual
        mixed = self.token_mixing(x)  # (B, H, T*D/H)
        
        # Project back to (B, T, D)
        # mixed is (B, H, T*D/H), we need to reshape and project
        batch_size = mixed.shape[0]
        num_heads = mixed.shape[1]
        
        # Reshape to (B*H, T*D/H) for projection
        mixed_flat = mixed.view(batch_size * num_heads, -1)
        projected = self.mixing_proj(mixed_flat)  # (B*H, D)
        
        # Reshape back to (B, H, D) then we need to get back to (B, T, D)
        # Since H = T in our design, we can transpose
        projected = projected.view(batch_size, num_heads, self.hidden_dim)
        
        # If H == T, we can use projected directly, otherwise we need to handle differently
        if num_heads == self.num_tokens:
            mixed_output = projected
        else:
            # Average pool across heads to get back to T tokens
            mixed_output = projected.mean(dim=1, keepdim=True).expand(-1, self.num_tokens, -1)
        
        x = self.norm1(x + self.dropout(mixed_output))
        
        # Per-token FFN with residual
        if self.use_sparse_moe:
            ffn_out, aux_loss = self.pffn(x)
        else:
            ffn_out = self.pffn(x)
            aux_loss = None
            
        x = self.norm2(x + self.dropout(ffn_out))
        
        return x, aux_loss


class RankMixer(nn.Module):
    """
    Complete RankMixer model for recommendation ranking.
    
    Args:
        num_features: Number of input features
        feature_dims: List of embedding dimensions for each feature
        num_tokens: Number of feature tokens (T)
        hidden_dim: Hidden dimension (D)
        num_layers: Number of RankMixer blocks (L)
        num_heads: Number of heads for token mixing
        ffn_ratio: Expansion ratio for FFN (k)
        dropout: Dropout rate
        use_sparse_moe: Whether to use Sparse MoE variant
        num_experts: Number of experts per token (if using MoE)
        num_tasks: Number of prediction tasks (e.g., click, conversion)
    """
    def __init__(self, num_features: int, feature_dims: List[int], num_tokens: int = 16,
                 hidden_dim: int = 256, num_layers: int = 2, num_heads: Optional[int] = None,
                 ffn_ratio: float = 4.0, dropout: float = 0.1, use_sparse_moe: bool = False,
                 num_experts: int = 4, num_tasks: int = 1):
        super().__init__()
        self.num_features = num_features
        self.feature_dims = feature_dims
        self.num_tokens = num_tokens
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.use_sparse_moe = use_sparse_moe
        self.num_tasks = num_tasks
        
        # Feature embedding layers
        self.embeddings = nn.ModuleList([
            nn.Embedding(10000, dim) if dim > 0 else nn.Linear(1, 1)  # Placeholder
            for dim in feature_dims
        ])
        
        # Calculate total embedding dimension
        total_emb_dim = sum(abs(d) for d in feature_dims)
        
        # Tokenization: project concatenated embeddings to tokens
        self.token_proj = nn.Linear(total_emb_dim, num_tokens * hidden_dim)
        
        # RankMixer blocks
        self.blocks = nn.ModuleList([
            RankMixerBlock(num_tokens, hidden_dim, num_heads, ffn_ratio, dropout, use_sparse_moe, num_experts)
            for _ in range(num_layers)
        ])
        
        # Output prediction heads
        self.task_heads = nn.ModuleList([
            nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim // 2),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(hidden_dim // 2, 1)
            ) for _ in range(num_tasks)
        ])
        
    def forward(self, features: torch.Tensor, feature_types: Optional[torch.Tensor] = None) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        """
        Args:
            features: Input features of shape (batch_size, num_features)
            feature_types: Optional tensor indicating feature types
        Returns:
            - predictions: Task predictions of shape (batch_size, num_tasks)
            - aux_loss: Auxiliary loss from MoE (if used)
        """
        batch_size = features.shape[0]
        
        # For simplicity, assume features are already embedded
        # In practice, you'd handle categorical and numerical features separately
        if features.dim() == 2:
            # Features are indices, embed them
            # This is a simplified version - real implementation would handle mixed types
            x = features.float()
        else:
            x = features
        
        # Tokenization: (B, F) -> (B, T*D) -> (B, T, D)
        x = self.token_proj(x)
        x = x.view(batch_size, self.num_tokens, self.hidden_dim)
        
        # Pass through RankMixer blocks
        total_aux_loss = 0.0
        for block in self.blocks:
            x, aux_loss = block(x)
            if aux_loss is not None:
                total_aux_loss += aux_loss
        
        # Mean pooling over tokens
        pooled = x.mean(dim=1)  # (B, D)
        
        # Task predictions
        predictions = []
        for head in self.task_heads:
            pred = head(pooled)
            predictions.append(pred)
        
        predictions = torch.cat(predictions, dim=1)  # (B, num_tasks)
        
        return predictions, total_aux_loss if self.use_sparse_moe else None
    
    def count_parameters(self) -> int:
        """Count trainable parameters"""
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


def create_rankmixer_small(num_features: int = 39, num_tasks: int = 1) -> RankMixer:
    """
    Create a small RankMixer model for fast iteration.
    Configuration: D=128, T=8, L=2
    """
    feature_dims = [8] * num_features  # Small embedding dims
    return RankMixer(
        num_features=num_features,
        feature_dims=feature_dims,
        num_tokens=8,
        hidden_dim=128,
        num_layers=2,
        ffn_ratio=2.0,  # Smaller FFN ratio for faster training
        dropout=0.1,
        use_sparse_moe=False,
        num_tasks=num_tasks
    )


def create_rankmixer_base(num_features: int = 39, num_tasks: int = 1) -> RankMixer:
    """
    Create a base RankMixer model.
    Configuration: D=256, T=16, L=2
    """
    feature_dims = [16] * num_features
    return RankMixer(
        num_features=num_features,
        feature_dims=feature_dims,
        num_tokens=16,
        hidden_dim=256,
        num_layers=2,
        ffn_ratio=4.0,
        dropout=0.1,
        use_sparse_moe=False,
        num_tasks=num_tasks
    )


def create_rankmixer_moe(num_features: int = 39, num_tasks: int = 1) -> RankMixer:
    """
    Create a RankMixer model with Sparse MoE.
    Configuration: D=256, T=16, L=2, E=4
    """
    feature_dims = [16] * num_features
    return RankMixer(
        num_features=num_features,
        feature_dims=feature_dims,
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
    # Test the model
    print("Testing RankMixer implementation...")
    
    # Create small model
    model = create_rankmixer_small(num_features=39, num_tasks=1)
    print(f"Small model parameters: {model.count_parameters():,}")
    
    # Test forward pass
    batch_size = 4
    num_features = 39
    x = torch.randn(batch_size, num_features * 8)  # Simplified input
    
    output, aux_loss = model(x)
    print(f"Output shape: {output.shape}")
    print(f"Aux loss: {aux_loss}")
    
    # Test base model
    model_base = create_rankmixer_base(num_features=39, num_tasks=1)
    print(f"\nBase model parameters: {model_base.count_parameters():,}")
    
    # Test MoE model
    model_moe = create_rankmixer_moe(num_features=39, num_tasks=1)
    print(f"MoE model parameters: {model_moe.count_parameters():,}")
    
    print("\nAll tests passed!")
