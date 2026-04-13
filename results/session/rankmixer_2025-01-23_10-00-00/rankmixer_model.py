"""
RankMixer: Scaling Up Ranking Models in Industrial Recommenders
Paper: https://arxiv.org/abs/2507.15551
Implementation for research and educational purposes
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from typing import List, Optional, Tuple


class MultiHeadTokenMixing(nn.Module):
    """
    Multi-head Token Mixing module
    将每个token分成H个头，然后跨token重组，实现无参数的特征交互
    """
    def __init__(self, num_tokens: int, hidden_dim: int, num_heads: int):
        super().__init__()
        self.num_tokens = num_tokens
        self.hidden_dim = hidden_dim
        self.num_heads = num_heads
        
        assert hidden_dim % num_heads == 0, "hidden_dim must be divisible by num_heads"
        self.head_dim = hidden_dim // num_heads
        
        # 使用num_heads = num_tokens，保持token数量不变
        assert num_heads == num_tokens, "For simplicity, we set num_heads = num_tokens"
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: [batch_size, num_tokens, hidden_dim]
        Returns:
            output: [batch_size, num_tokens, hidden_dim]
        """
        batch_size = x.shape[0]
        
        # Split each token into H heads: [B, T, D] -> [B, T, H, D//H]
        x_split = x.reshape(batch_size, self.num_tokens, self.num_heads, self.head_dim)
        
        # Transpose to: [B, H, T, D//H]
        x_transposed = x_split.permute(0, 2, 1, 3)
        
        # Merge heads: [B, H, T, D//H] -> [B, H, T*D//H]
        # This creates H new tokens, each containing parts from all original tokens
        x_merged = x_transposed.reshape(batch_size, self.num_heads, -1)
        
        # Reshape back to [B, T, D] for residual connection
        # The mixing happens because each new token contains info from all original tokens
        output = x_merged.reshape(batch_size, self.num_tokens, self.hidden_dim)
        
        return output


class PerTokenFFN(nn.Module):
    """
    Per-token Feed-Forward Network
    每个token有独立的FFN参数
    """
    def __init__(self, num_tokens: int, hidden_dim: int, ffn_ratio: int = 4):
        super().__init__()
        self.num_tokens = num_tokens
        self.hidden_dim = hidden_dim
        self.ffn_hidden_dim = hidden_dim * ffn_ratio
        
        # 每个token有独立的FFN参数
        self.w1 = nn.Parameter(torch.randn(num_tokens, hidden_dim, self.ffn_hidden_dim) * 0.02)
        self.b1 = nn.Parameter(torch.zeros(num_tokens, self.ffn_hidden_dim))
        self.w2 = nn.Parameter(torch.randn(num_tokens, self.ffn_hidden_dim, hidden_dim) * 0.02)
        self.b2 = nn.Parameter(torch.zeros(num_tokens, hidden_dim))
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: [batch_size, num_tokens, hidden_dim]
        Returns:
            output: [batch_size, num_tokens, hidden_dim]
        """
        batch_size = x.shape[0]
        
        # Process each token independently
        # x: [B, T, D]
        # w1: [T, D, kD] -> [T, D, kD]
        
        # Expand x for batch matrix multiplication
        x_expanded = x.unsqueeze(-1)  # [B, T, D, 1]
        
        # First linear layer with GELU activation
        # w1: [T, D, kD], we need to apply it to each token
        hidden = torch.einsum('btd,tdk->btk', x, self.w1) + self.b1  # [B, T, kD]
        hidden = F.gelu(hidden)
        
        # Second linear layer
        output = torch.einsum('btk,tkd->btd', hidden, self.w2) + self.b2  # [B, T, D]
        
        return output


class SparseMoEPerTokenFFN(nn.Module):
    """
    Sparse Mixture-of-Experts variant of Per-token FFN
    使用ReLU路由和DTSI策略
    """
    def __init__(self, num_tokens: int, hidden_dim: int, num_experts: int = 4, 
                 ffn_ratio: int = 4, top_k: int = 2, use_relu_routing: bool = True):
        super().__init__()
        self.num_tokens = num_tokens
        self.hidden_dim = hidden_dim
        self.num_experts = num_experts
        self.ffn_hidden_dim = hidden_dim * ffn_ratio
        self.top_k = top_k
        self.use_relu_routing = use_relu_routing
        
        # Experts: [num_tokens, num_experts, D, kD] and [num_tokens, num_experts, kD, D]
        self.expert_w1 = nn.Parameter(
            torch.randn(num_tokens, num_experts, hidden_dim, self.ffn_hidden_dim) * 0.02
        )
        self.expert_b1 = nn.Parameter(torch.zeros(num_tokens, num_experts, self.ffn_hidden_dim))
        self.expert_w2 = nn.Parameter(
            torch.randn(num_tokens, num_experts, self.ffn_hidden_dim, hidden_dim) * 0.02
        )
        self.expert_b2 = nn.Parameter(torch.zeros(num_tokens, num_experts, hidden_dim))
        
        # Router (two routers for DTSI)
        self.router_train = nn.Linear(hidden_dim, num_experts)
        self.router_infer = nn.Linear(hidden_dim, num_experts)
        
        # For load balancing
        self.aux_loss_coef = 0.01
    
    def forward(self, x: torch.Tensor, training: bool = True) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Args:
            x: [batch_size, num_tokens, hidden_dim]
        Returns:
            output: [batch_size, num_tokens, hidden_dim]
            aux_loss: scalar
        """
        batch_size = x.shape[0]
        
        # Compute routing weights
        if training:
            router_logits = self.router_train(x)  # [B, T, E]
        else:
            router_logits = self.router_infer(x)  # [B, T, E]
        
        if self.use_relu_routing:
            # ReLU routing
            gates = F.relu(router_logits)  # [B, T, E]
            # Normalize
            gates = gates / (gates.sum(dim=-1, keepdim=True) + 1e-9)
        else:
            # Top-k softmax routing
            top_k_logits, top_k_indices = torch.topk(router_logits, self.top_k, dim=-1)
            gates = torch.zeros_like(router_logits)
            gates.scatter_(-1, top_k_indices, F.softmax(top_k_logits, dim=-1))
        
        # Compute expert outputs
        # For efficiency, we'll compute all experts and then weight them
        # x: [B, T, D]
        # expert_w1: [T, E, D, kD]
        
        # Expand x for all experts
        x_expanded = x.unsqueeze(2).unsqueeze(-1)  # [B, T, 1, D, 1]
        
        # First layer: [B, T, E, kD]
        hidden = torch.einsum('btd,tedk->btek', x, self.expert_w1) + self.expert_b1
        hidden = F.gelu(hidden)
        
        # Second layer: [B, T, E, D]
        expert_outputs = torch.einsum('btek,tekd->bted', hidden, self.expert_w2) + self.expert_b2
        
        # Weighted combination: [B, T, E, D] * [B, T, E, 1] -> [B, T, D]
        gates_expanded = gates.unsqueeze(-1)  # [B, T, E, 1]
        output = (expert_outputs * gates_expanded).sum(dim=2)  # [B, T, D]
        
        # Auxiliary loss for load balancing (only for training)
        aux_loss = 0.0
        if training and self.use_relu_routing:
            # L1 regularization on gates to encourage sparsity
            aux_loss = self.aux_loss_coef * gates.sum() / (batch_size * self.num_tokens)
        
        return output, aux_loss


class RankMixerBlock(nn.Module):
    """
    Single RankMixer Block with Token Mixing and Per-token FFN
    """
    def __init__(self, num_tokens: int, hidden_dim: int, num_heads: int, 
                 ffn_ratio: int = 4, use_moe: bool = False, num_experts: int = 4,
                 moe_top_k: int = 2):
        super().__init__()
        self.use_moe = use_moe
        
        # Multi-head Token Mixing
        self.token_mixing = MultiHeadTokenMixing(num_tokens, hidden_dim, num_heads)
        self.ln1 = nn.LayerNorm(hidden_dim)
        
        # Per-token FFN (dense or sparse)
        if use_moe:
            self.pffn = SparseMoEPerTokenFFN(num_tokens, hidden_dim, num_experts, 
                                             ffn_ratio, moe_top_k)
        else:
            self.pffn = PerTokenFFN(num_tokens, hidden_dim, ffn_ratio)
        self.ln2 = nn.LayerNorm(hidden_dim)
    
    def forward(self, x: torch.Tensor, training: bool = True) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        Args:
            x: [batch_size, num_tokens, hidden_dim]
        Returns:
            output: [batch_size, num_tokens, hidden_dim]
            aux_loss: scalar (0 if not using MoE)
        """
        # Token Mixing with residual
        x = self.ln1(self.token_mixing(x) + x)
        
        # Per-token FFN with residual
        if self.use_moe:
            ffn_out, aux_loss = self.pffn(x, training)
        else:
            ffn_out = self.pffn(x)
            aux_loss = 0.0
        
        x = self.ln2(ffn_out + x)
        
        return x, aux_loss


class RankMixer(nn.Module):
    """
    Complete RankMixer Model for CTR Prediction
    """
    def __init__(self, 
                 feature_dims: List[int],  # List of embedding dimensions for each feature
                 num_tokens: int = 16,
                 hidden_dim: int = 128,  # Reduced from 768 for faster experimentation
                 num_layers: int = 2,
                 num_heads: int = 16,
                 ffn_ratio: int = 4,
                 use_moe: bool = False,
                 num_experts: int = 4,
                 moe_top_k: int = 2,
                 dropout: float = 0.1,
                 num_tasks: int = 1):
        super().__init__()
        
        self.num_tokens = num_tokens
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.use_moe = use_moe
        
        # Feature embedding layers
        self.feature_embeddings = nn.ModuleList([
            nn.Embedding(dim, hidden_dim) if dim > 0 else nn.Linear(1, hidden_dim, bias=False)
            for dim in feature_dims
        ])
        
        # Calculate total feature dimension
        self.total_feature_dim = len(feature_dims) * hidden_dim
        
        # Tokenization: project features to tokens
        # We concatenate all features and split into num_tokens
        tokens_per_feature = max(1, len(feature_dims) // num_tokens)
        self.token_proj = nn.Linear(hidden_dim, hidden_dim)
        
        # RankMixer blocks
        self.blocks = nn.ModuleList([
            RankMixerBlock(num_tokens, hidden_dim, num_heads, ffn_ratio,
                          use_moe, num_experts, moe_top_k)
            for _ in range(num_layers)
        ])
        
        # Output layers for each task
        self.output_layers = nn.ModuleList([
            nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim // 2),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(hidden_dim // 2, 1),
                nn.Sigmoid()
            )
            for _ in range(num_tasks)
        ])
        
        self._init_weights()
    
    def _init_weights(self):
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, nn.Embedding):
                nn.init.normal_(m.weight, std=0.01)
    
    def forward(self, features: torch.Tensor, training: bool = True) -> torch.Tensor:
        """
        Args:
            features: [batch_size, num_features] - categorical feature indices
                     or [batch_size, num_features, hidden_dim] - pre-computed embeddings
        Returns:
            predictions: [batch_size, num_tasks]
        """
        batch_size = features.shape[0]
        
        # Handle different input types
        if features.dim() == 2:
            # Categorical features: [B, num_features]
            # Embed each feature
            embedded = []
            for i, emb_layer in enumerate(self.feature_embeddings):
                feat = features[:, i].long()
                emb = emb_layer(feat)  # [B, hidden_dim]
                embedded.append(emb)
            x = torch.stack(embedded, dim=1)  # [B, num_features, hidden_dim]
        else:
            # Pre-computed embeddings: [B, num_features, hidden_dim]
            x = features
        
        # Tokenization: group features into tokens
        num_features = x.shape[1]
        features_per_token = max(1, num_features // self.num_tokens)
        
        # Pad if necessary
        if num_features < self.num_tokens:
            padding = self.num_tokens - num_features
            x = F.pad(x, (0, 0, 0, padding))
            num_features = x.shape[1]
        
        # Split into tokens and project
        tokens = []
        for i in range(self.num_tokens):
            start_idx = i * features_per_token
            end_idx = min((i + 1) * features_per_token, num_features)
            if start_idx < num_features:
                token_features = x[:, start_idx:end_idx, :]  # [B, features_per_token, D]
                token = token_features.mean(dim=1)  # [B, D]
                token = self.token_proj(token)
                tokens.append(token)
            else:
                # Use zero padding for missing tokens
                tokens.append(torch.zeros(batch_size, self.hidden_dim, device=x.device))
        
        x = torch.stack(tokens, dim=1)  # [B, num_tokens, hidden_dim]
        
        # Pass through RankMixer blocks
        total_aux_loss = 0.0
        for block in self.blocks:
            x, aux_loss = block(x, training)
            total_aux_loss += aux_loss
        
        # Mean pooling over tokens
        pooled = x.mean(dim=1)  # [B, hidden_dim]
        
        # Output predictions for each task
        predictions = []
        for output_layer in self.output_layers:
            pred = output_layer(pooled)  # [B, 1]
            predictions.append(pred)
        
        output = torch.cat(predictions, dim=1)  # [B, num_tasks]
        
        # Add auxiliary loss as attribute for access during training
        output.aux_loss = total_aux_loss
        
        return output
    
    def count_parameters(self):
        """Count trainable parameters"""
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


# Baseline models for comparison
class DeepFM(nn.Module):
    """DeepFM baseline for comparison"""
    def __init__(self, feature_dims: List[int], embed_dim: int = 64, 
                 mlp_dims: List[int] = [256, 128, 64], dropout: float = 0.1):
        super().__init__()
        
        # Embedding layers
        self.embeddings = nn.ModuleList([
            nn.Embedding(dim, embed_dim) for dim in feature_dims
        ])
        
        num_features = len(feature_dims)
        
        # FM component
        self.fm_first_order = nn.ModuleList([
            nn.Embedding(dim, 1) for dim in feature_dims
        ])
        
        # Deep component
        input_dim = num_features * embed_dim
        layers = []
        for i, dim in enumerate(mlp_dims):
            if i == 0:
                layers.append(nn.Linear(input_dim, dim))
            else:
                layers.append(nn.Linear(mlp_dims[i-1], dim))
            layers.append(nn.ReLU())
            layers.append(nn.Dropout(dropout))
        layers.append(nn.Linear(mlp_dims[-1], 1))
        layers.append(nn.Sigmoid())
        self.deep = nn.Sequential(*layers)
    
    def forward(self, features: torch.Tensor) -> torch.Tensor:
        batch_size = features.shape[0]
        
        # Embeddings
        embeds = []
        fm_first = []
        for i, (emb_layer, fm_layer) in enumerate(zip(self.embeddings, self.fm_first_order)):
            feat = features[:, i].long()
            embeds.append(emb_layer(feat))
            fm_first.append(fm_layer(feat))
        
        embeds = torch.stack(embeds, dim=1)  # [B, num_features, embed_dim]
        fm_first = torch.cat(fm_first, dim=1)  # [B, num_features]
        
        # FM second order
        square_of_sum = torch.sum(embeds, dim=1) ** 2
        sum_of_square = torch.sum(embeds ** 2, dim=1)
        fm_second = 0.5 * (square_of_sum - sum_of_square)
        
        # FM output
        fm_output = torch.sum(fm_first, dim=1, keepdim=True) + torch.sum(fm_second, dim=1, keepdim=True)
        
        # Deep output
        deep_input = embeds.view(batch_size, -1)
        deep_output = self.deep(deep_input)
        
        # Combined output
        output = torch.sigmoid(fm_output + deep_output)
        
        return output


class DCNv2(nn.Module):
    """DCNv2 baseline for comparison"""
    def __init__(self, feature_dims: List[int], embed_dim: int = 64,
                 num_cross_layers: int = 3, mlp_dims: List[int] = [256, 128],
                 dropout: float = 0.1):
        super().__init__()
        
        self.embeddings = nn.ModuleList([
            nn.Embedding(dim, embed_dim) for dim in feature_dims
        ])
        
        num_features = len(feature_dims)
        input_dim = num_features * embed_dim
        
        # Cross network
        self.cross_layers = nn.ModuleList([
            nn.Linear(input_dim, input_dim) for _ in range(num_cross_layers)
        ])
        self.cross_b = nn.ParameterList([
            nn.Parameter(torch.zeros(input_dim)) for _ in range(num_cross_layers)
        ])
        
        # Deep network
        layers = []
        for i, dim in enumerate(mlp_dims):
            if i == 0:
                layers.append(nn.Linear(input_dim, dim))
            else:
                layers.append(nn.Linear(mlp_dims[i-1], dim))
            layers.append(nn.ReLU())
            layers.append(nn.Dropout(dropout))
        self.deep = nn.Sequential(*layers)
        
        # Output layer
        self.output = nn.Sequential(
            nn.Linear(input_dim + mlp_dims[-1], 1),
            nn.Sigmoid()
        )
    
    def forward(self, features: torch.Tensor) -> torch.Tensor:
        batch_size = features.shape[0]
        
        # Embeddings
        embeds = []
        for i, emb_layer in enumerate(self.embeddings):
            feat = features[:, i].long()
            embeds.append(emb_layer(feat))
        
        x0 = torch.cat(embeds, dim=1)  # [B, num_features * embed_dim]
        x = x0
        
        # Cross network
        for cross_layer, cross_b in zip(self.cross_layers, self.cross_b):
            x = x0 * cross_layer(x) + cross_b + x
        
        cross_output = x
        
        # Deep network
        deep_output = self.deep(x0)
        
        # Concatenate and output
        combined = torch.cat([cross_output, deep_output], dim=1)
        output = self.output(combined)
        
        return output


if __name__ == "__main__":
    # Test the model
    batch_size = 32
    num_features = 39  # Typical for Criteo dataset
    feature_dims = [100] * num_features  # Simplified
    
    # Create model
    model = RankMixer(
        feature_dims=feature_dims,
        num_tokens=16,
        hidden_dim=128,
        num_layers=2,
        num_heads=16,
        ffn_ratio=4,
        use_moe=False,
        num_tasks=1
    )
    
    # Test forward pass
    features = torch.randint(0, 100, (batch_size, num_features))
    output = model(features)
    
    print(f"Model parameters: {model.count_parameters():,}")
    print(f"Output shape: {output.shape}")
    print("RankMixer model test passed!")
