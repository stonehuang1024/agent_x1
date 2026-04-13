"""
DCN-V2: Improved Deep & Cross Network
Implementation based on "DCN V2: Improved Deep & Cross Network and 
Practical Lessons for Web-scale Learning to Rank Systems" (WWW 2021)
"""
import torch
import torch.nn as nn
import torch.nn.functional as F


class CrossLayer(nn.Module):
    """
    Cross Layer for DCN-V2
    Formula: x_{l+1} = x0 ⊙ (W * x_l + b) + x_l
    
    Args:
        input_dim: input dimension
        low_rank: if > 0, use low-rank approximation with rank=low_rank
    """
    def __init__(self, input_dim, low_rank=0):
        super(CrossLayer, self).__init__()
        self.input_dim = input_dim
        self.low_rank = low_rank
        
        if low_rank > 0 and low_rank < input_dim:
            # Low-rank approximation: W ≈ U * V^T
            self.U = nn.Parameter(torch.randn(input_dim, low_rank) * 0.01)
            self.V = nn.Parameter(torch.randn(input_dim, low_rank) * 0.01)
        else:
            # Full-rank matrix
            self.W = nn.Parameter(torch.randn(input_dim, input_dim) * 0.01)
        
        self.b = nn.Parameter(torch.zeros(input_dim))
    
    def forward(self, x0, xl):
        """
        Args:
            x0: base features (batch_size, input_dim)
            xl: input to current layer (batch_size, input_dim)
        Returns:
            x_{l+1}: output of cross layer (batch_size, input_dim)
        """
        if self.low_rank > 0 and self.low_rank < self.input_dim:
            # Low-rank: W = U * V^T
            # W @ xl = U @ (V^T @ xl)
            temp = torch.matmul(xl, self.V)  # (batch, low_rank)
            temp = torch.matmul(temp, self.U.t())  # (batch, input_dim)
        else:
            temp = torch.matmul(xl, self.W)  # (batch, input_dim)
        
        # Add bias
        temp = temp + self.b
        
        # Hadamard product with x0 and add residual
        xl_plus_1 = x0 * temp + xl
        
        return xl_plus_1


class CrossNetwork(nn.Module):
    """
    Cross Network with multiple cross layers
    """
    def __init__(self, input_dim, num_layers, low_rank=0):
        super(CrossNetwork, self).__init__()
        self.num_layers = num_layers
        self.cross_layers = nn.ModuleList([
            CrossLayer(input_dim, low_rank) for _ in range(num_layers)
        ])
    
    def forward(self, x0):
        """
        Args:
            x0: input features (batch_size, input_dim)
        Returns:
            output: (batch_size, input_dim)
        """
        xl = x0
        for cross_layer in self.cross_layers:
            xl = cross_layer(x0, xl)
        return xl


class DeepNetwork(nn.Module):
    """
    Deep Network (MLP)
    """
    def __init__(self, input_dim, hidden_dims, dropout_rate=0.2, use_bn=True):
        super(DeepNetwork, self).__init__()
        self.layers = nn.ModuleList()
        self.batch_norms = nn.ModuleList() if use_bn else None
        self.dropouts = nn.ModuleList()
        
        prev_dim = input_dim
        for hidden_dim in hidden_dims:
            self.layers.append(nn.Linear(prev_dim, hidden_dim))
            if use_bn:
                self.batch_norms.append(nn.BatchNorm1d(hidden_dim))
            self.dropouts.append(nn.Dropout(dropout_rate))
            prev_dim = hidden_dim
    
    def forward(self, x):
        """
        Args:
            x: input (batch_size, input_dim)
        Returns:
            output: (batch_size, hidden_dims[-1])
        """
        for i, layer in enumerate(self.layers):
            x = layer(x)
            if self.batch_norms is not None:
                x = self.batch_norms[i](x)
            x = F.relu(x)
            x = self.dropouts[i](x)
        return x


class DCNv2(nn.Module):
    """
    DCN-V2 Model
    
    Args:
        num_dense: number of dense features
        sparse_vocab_sizes: dict of sparse feature vocab sizes
        embedding_dim: embedding dimension for sparse features
        cross_layers: number of cross layers
        deep_hidden_dims: list of hidden dimensions for deep network
        dropout_rate: dropout rate
        use_bn: whether to use batch normalization
        structure: 'stacked' or 'parallel'
        low_rank: low-rank approximation rank (0 for full-rank)
    """
    def __init__(
        self,
        num_dense,
        sparse_vocab_sizes,
        embedding_dim=16,
        cross_layers=2,
        deep_hidden_dims=[256, 128],
        dropout_rate=0.2,
        use_bn=True,
        structure='stacked',
        low_rank=0
    ):
        super(DCNv2, self).__init__()
        
        self.num_dense = num_dense
        self.num_sparse = len(sparse_vocab_sizes)
        self.embedding_dim = embedding_dim
        self.structure = structure
        
        # Embedding layers for sparse features
        self.embeddings = nn.ModuleDict({
            name: nn.Embedding(vocab_size, embedding_dim)
            for name, vocab_size in sparse_vocab_sizes.items()
        })
        
        # Calculate input dimension
        # Dense features + sum of all sparse embeddings
        self.input_dim = num_dense + self.num_sparse * embedding_dim
        
        # Cross Network
        self.cross_net = CrossNetwork(self.input_dim, cross_layers, low_rank)
        
        # Deep Network
        if structure == 'stacked':
            # Stacked: Cross output -> Deep Network
            self.deep_net = DeepNetwork(
                self.input_dim, deep_hidden_dims, dropout_rate, use_bn
            )
            self.output_dim = deep_hidden_dims[-1]
        else:
            # Parallel: [Cross output; Deep output]
            self.deep_net = DeepNetwork(
                self.input_dim, deep_hidden_dims, dropout_rate, use_bn
            )
            self.output_dim = self.input_dim + deep_hidden_dims[-1]
        
        # Final prediction layer
        self.logit_layer = nn.Linear(self.output_dim, 1)
        
        # Initialize weights
        self._init_weights()
    
    def _init_weights(self):
        """Initialize weights"""
        for name, embedding in self.embeddings.items():
            nn.init.xavier_uniform_(embedding.weight)
        
        nn.init.xavier_uniform_(self.logit_layer.weight)
        nn.init.zeros_(self.logit_layer.bias)
    
    def forward(self, dense_features, sparse_features):
        """
        Args:
            dense_features: (batch_size, num_dense)
            sparse_features: (batch_size, num_sparse)
        Returns:
            logits: (batch_size, 1)
        """
        batch_size = dense_features.size(0)
        
        # Embed sparse features
        sparse_embeds = []
        for i, (name, embedding) in enumerate(self.embeddings.items()):
            sparse_idx = sparse_features[:, i]
            sparse_embeds.append(embedding(sparse_idx))
        
        # Concatenate all embeddings
        sparse_embeds = torch.cat(sparse_embeds, dim=1)  # (batch, num_sparse * embedding_dim)
        
        # Concatenate dense and sparse features
        x0 = torch.cat([dense_features, sparse_embeds], dim=1)  # (batch, input_dim)
        
        # Cross Network
        cross_output = self.cross_net(x0)
        
        # Deep Network
        if self.structure == 'stacked':
            # Stacked: Cross output -> Deep
            deep_output = self.deep_net(cross_output)
            final_output = deep_output
        else:
            # Parallel: Concatenate Cross and Deep outputs
            deep_output = self.deep_net(x0)
            final_output = torch.cat([cross_output, deep_output], dim=1)
        
        # Final prediction
        logits = self.logit_layer(final_output)
        
        return logits.squeeze(-1)


class DCN(nn.Module):
    """
    Original DCN (V1) for comparison
    Uses rank-1 approximation: W = 1 * w^T
    """
    def __init__(
        self,
        num_dense,
        sparse_vocab_sizes,
        embedding_dim=16,
        cross_layers=2,
        deep_hidden_dims=[256, 128],
        dropout_rate=0.2,
        use_bn=True,
        structure='stacked'
    ):
        super(DCN, self).__init__()
        
        self.num_dense = num_dense
        self.num_sparse = len(sparse_vocab_sizes)
        self.embedding_dim = embedding_dim
        self.structure = structure
        
        # Embedding layers
        self.embeddings = nn.ModuleDict({
            name: nn.Embedding(vocab_size, embedding_dim)
            for name, vocab_size in sparse_vocab_sizes.items()
        })
        
        self.input_dim = num_dense + self.num_sparse * embedding_dim
        
        # Original DCN uses rank-1 cross layers
        self.cross_weights = nn.ParameterList([
            nn.Parameter(torch.randn(self.input_dim) * 0.01)
            for _ in range(cross_layers)
        ])
        self.cross_biases = nn.ParameterList([
            nn.Parameter(torch.zeros(self.input_dim))
            for _ in range(cross_layers)
        ])
        
        # Deep Network
        if structure == 'stacked':
            self.deep_net = DeepNetwork(
                self.input_dim, deep_hidden_dims, dropout_rate, use_bn
            )
            self.output_dim = deep_hidden_dims[-1]
        else:
            self.deep_net = DeepNetwork(
                self.input_dim, deep_hidden_dims, dropout_rate, use_bn
            )
            self.output_dim = self.input_dim + deep_hidden_dims[-1]
        
        self.logit_layer = nn.Linear(self.output_dim, 1)
        self._init_weights()
    
    def _init_weights(self):
        for embedding in self.embeddings.values():
            nn.init.xavier_uniform_(embedding.weight)
        nn.init.xavier_uniform_(self.logit_layer.weight)
        nn.init.zeros_(self.logit_layer.bias)
    
    def forward(self, dense_features, sparse_features):
        batch_size = dense_features.size(0)
        
        # Embed sparse features
        sparse_embeds = []
        for i, (name, embedding) in enumerate(self.embeddings.items()):
            sparse_idx = sparse_features[:, i]
            sparse_embeds.append(embedding(sparse_idx))
        
        sparse_embeds = torch.cat(sparse_embeds, dim=1)
        x0 = torch.cat([dense_features, sparse_embeds], dim=1)
        
        # Cross Network (rank-1)
        xl = x0
        for w, b in zip(self.cross_weights, self.cross_biases):
            # x_{l+1} = x0 * (w^T * xl + b) + xl
            temp = torch.sum(w * xl, dim=1, keepdim=True) + b  # (batch, input_dim)
            xl = x0 * temp + xl
        cross_output = xl
        
        # Deep Network
        if self.structure == 'stacked':
            deep_output = self.deep_net(cross_output)
            final_output = deep_output
        else:
            deep_output = self.deep_net(x0)
            final_output = torch.cat([cross_output, deep_output], dim=1)
        
        logits = self.logit_layer(final_output)
        return logits.squeeze(-1)
