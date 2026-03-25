"""
Baseline models for comparison:
- DeepFM
- DNN (Deep Neural Network)
"""
import torch
import torch.nn as nn
import torch.nn.functional as F


class DeepFM(nn.Module):
    """
    DeepFM: A Factorization-Machine based Neural Network for CTR Prediction
    Reference: https://arxiv.org/abs/1703.04247
    
    Combines FM component (2nd-order feature interactions) with Deep component (DNN)
    """
    def __init__(
        self,
        num_dense,
        sparse_vocab_sizes,
        embedding_dim=16,
        deep_hidden_dims=[256, 128],
        dropout_rate=0.2,
        use_bn=True
    ):
        super(DeepFM, self).__init__()
        
        self.num_dense = num_dense
        self.num_sparse = len(sparse_vocab_sizes)
        self.embedding_dim = embedding_dim
        
        # Embedding layers for sparse features
        self.embeddings = nn.ModuleDict({
            name: nn.Embedding(vocab_size, embedding_dim)
            for name, vocab_size in sparse_vocab_sizes.items()
        })
        
        # Linear part (1st order)
        self.linear_dense = nn.Linear(num_dense, 1)
        self.linear_sparse = nn.ModuleDict({
            name: nn.Embedding(vocab_size, 1)
            for name, vocab_size in sparse_vocab_sizes.items()
        })
        
        # FM part (2nd order) - uses same embeddings
        
        # Deep part
        self.input_dim = num_dense + self.num_sparse * embedding_dim
        self.deep_layers = nn.ModuleList()
        self.batch_norms = nn.ModuleList() if use_bn else None
        self.dropouts = nn.ModuleList()
        
        prev_dim = self.input_dim
        for hidden_dim in deep_hidden_dims:
            self.deep_layers.append(nn.Linear(prev_dim, hidden_dim))
            if use_bn:
                self.batch_norms.append(nn.BatchNorm1d(hidden_dim))
            self.dropouts.append(nn.Dropout(dropout_rate))
            prev_dim = hidden_dim
        
        # Output layer combines FM and Deep
        self.deep_output = nn.Linear(prev_dim, 1)
        
        self._init_weights()
    
    def _init_weights(self):
        for embedding in self.embeddings.values():
            nn.init.xavier_uniform_(embedding.weight)
        for embedding in self.linear_sparse.values():
            nn.init.xavier_uniform_(embedding.weight)
        nn.init.xavier_uniform_(self.linear_dense.weight)
        nn.init.xavier_uniform_(self.deep_output.weight)
    
    def forward(self, dense_features, sparse_features):
        """
        Args:
            dense_features: (batch_size, num_dense)
            sparse_features: (batch_size, num_sparse)
        Returns:
            logits: (batch_size,)
        """
        batch_size = dense_features.size(0)
        
        # Linear part (1st order)
        linear_output = self.linear_dense(dense_features)
        for i, (name, embedding) in enumerate(self.linear_sparse.items()):
            sparse_idx = sparse_features[:, i]
            linear_output = linear_output + embedding(sparse_idx)
        linear_output = linear_output.squeeze(-1)  # (batch,)
        
        # Embedding for FM and Deep
        sparse_embeds = []
        for i, (name, embedding) in enumerate(self.embeddings.items()):
            sparse_idx = sparse_features[:, i]
            sparse_embeds.append(embedding(sparse_idx))
        
        # FM part (2nd order)
        # sum_square part
        stack_embeds = torch.stack(sparse_embeds, dim=1)  # (batch, num_sparse, embedding_dim)
        square_of_sum = torch.sum(stack_embeds, dim=1) ** 2  # (batch, embedding_dim)
        sum_of_square = torch.sum(stack_embeds ** 2, dim=1)  # (batch, embedding_dim)
        fm_output = 0.5 * torch.sum(square_of_sum - sum_of_square, dim=1)  # (batch,)
        
        # Deep part
        sparse_embeds_cat = torch.cat(sparse_embeds, dim=1)
        deep_input = torch.cat([dense_features, sparse_embeds_cat], dim=1)
        
        deep_out = deep_input
        for i, layer in enumerate(self.deep_layers):
            deep_out = layer(deep_out)
            if self.batch_norms is not None:
                deep_out = self.batch_norms[i](deep_out)
            deep_out = F.relu(deep_out)
            deep_out = self.dropouts[i](deep_out)
        
        deep_output = self.deep_output(deep_out).squeeze(-1)
        
        # Combine all parts
        output = linear_output + fm_output + deep_output
        
        return output


class DNN(nn.Module):
    """
    Deep Neural Network (MLP) baseline
    Simple feed-forward network with embeddings
    """
    def __init__(
        self,
        num_dense,
        sparse_vocab_sizes,
        embedding_dim=16,
        hidden_dims=[256, 128, 64],
        dropout_rate=0.2,
        use_bn=True
    ):
        super(DNN, self).__init__()
        
        self.num_dense = num_dense
        self.num_sparse = len(sparse_vocab_sizes)
        self.embedding_dim = embedding_dim
        
        # Embedding layers
        self.embeddings = nn.ModuleDict({
            name: nn.Embedding(vocab_size, embedding_dim)
            for name, vocab_size in sparse_vocab_sizes.items()
        })
        
        # Deep layers
        input_dim = num_dense + self.num_sparse * embedding_dim
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
        
        self.output_layer = nn.Linear(prev_dim, 1)
        
        self._init_weights()
    
    def _init_weights(self):
        for embedding in self.embeddings.values():
            nn.init.xavier_uniform_(embedding.weight)
        nn.init.xavier_uniform_(self.output_layer.weight)
        nn.init.zeros_(self.output_layer.bias)
    
    def forward(self, dense_features, sparse_features):
        """
        Args:
            dense_features: (batch_size, num_dense)
            sparse_features: (batch_size, num_sparse)
        Returns:
            logits: (batch_size,)
        """
        # Embed sparse features
        sparse_embeds = []
        for i, (name, embedding) in enumerate(self.embeddings.items()):
            sparse_idx = sparse_features[:, i]
            sparse_embeds.append(embedding(sparse_idx))
        
        sparse_embeds = torch.cat(sparse_embeds, dim=1)
        x = torch.cat([dense_features, sparse_embeds], dim=1)
        
        # Deep network
        for i, layer in enumerate(self.layers):
            x = layer(x)
            if self.batch_norms is not None:
                x = self.batch_norms[i](x)
            x = F.relu(x)
            x = self.dropouts[i](x)
        
        output = self.output_layer(x).squeeze(-1)
        return output


class LogisticRegression(nn.Module):
    """
    Logistic Regression baseline
    Simple linear model with embeddings
    """
    def __init__(
        self,
        num_dense,
        sparse_vocab_sizes,
        embedding_dim=16
    ):
        super(LogisticRegression, self).__init__()
        
        self.num_dense = num_dense
        self.num_sparse = len(sparse_vocab_sizes)
        self.embedding_dim = embedding_dim
        
        # Linear part for dense features
        self.linear_dense = nn.Linear(num_dense, 1)
        
        # Embeddings for sparse features
        self.embeddings = nn.ModuleDict({
            name: nn.Embedding(vocab_size, embedding_dim)
            for name, vocab_size in sparse_vocab_sizes.items()
        })
        
        # Linear transformation for embedded sparse features
        self.linear_sparse = nn.Linear(self.num_sparse * embedding_dim, 1)
        
        self._init_weights()
    
    def _init_weights(self):
        nn.init.xavier_uniform_(self.linear_dense.weight)
        nn.init.xavier_uniform_(self.linear_sparse.weight)
        for embedding in self.embeddings.values():
            nn.init.xavier_uniform_(embedding.weight)
    
    def forward(self, dense_features, sparse_features):
        """
        Args:
            dense_features: (batch_size, num_dense)
            sparse_features: (batch_size, num_sparse)
        Returns:
            logits: (batch_size,)
        """
        # Linear part
        linear_out = self.linear_dense(dense_features).squeeze(-1)
        
        # Sparse embeddings
        sparse_embeds = []
        for i, (name, embedding) in enumerate(self.embeddings.items()):
            sparse_idx = sparse_features[:, i]
            sparse_embeds.append(embedding(sparse_idx))
        
        sparse_embeds = torch.cat(sparse_embeds, dim=1)
        sparse_out = self.linear_sparse(sparse_embeds).squeeze(-1)
        
        return linear_out + sparse_out
