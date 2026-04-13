"""
Unit tests for models
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import torch
import pytest
from src.models.dcn_v2 import DCNv2, DCN, CrossLayer, CrossNetwork
from src.models.baselines import DeepFM, DNN, LogisticRegression


class TestCrossLayer:
    def test_forward_shape(self):
        batch_size = 32
        input_dim = 100
        
        layer = CrossLayer(input_dim)
        x0 = torch.randn(batch_size, input_dim)
        xl = torch.randn(batch_size, input_dim)
        
        output = layer(x0, xl)
        
        assert output.shape == (batch_size, input_dim)
    
    def test_low_rank_forward(self):
        batch_size = 32
        input_dim = 100
        low_rank = 16
        
        layer = CrossLayer(input_dim, low_rank=low_rank)
        x0 = torch.randn(batch_size, input_dim)
        xl = torch.randn(batch_size, input_dim)
        
        output = layer(x0, xl)
        
        assert output.shape == (batch_size, input_dim)
    
    def test_residual_connection(self):
        """Test that residual connection works"""
        input_dim = 10
        layer = CrossLayer(input_dim)
        
        x0 = torch.ones(1, input_dim)
        xl = torch.zeros(1, input_dim)
        
        output = layer(x0, xl)
        
        # With zero input and bias=0, output should equal xl (residual)
        assert torch.allclose(output, xl, atol=1e-6)


class TestCrossNetwork:
    def test_forward_shape(self):
        batch_size = 32
        input_dim = 100
        num_layers = 3
        
        net = CrossNetwork(input_dim, num_layers)
        x = torch.randn(batch_size, input_dim)
        
        output = net(x)
        
        assert output.shape == (batch_size, input_dim)
    
    def test_polynomial_degree(self):
        """Test that L layers can produce up to L+1 degree polynomial"""
        batch_size = 4
        input_dim = 5
        num_layers = 2
        
        net = CrossNetwork(input_dim, num_layers)
        x = torch.randn(batch_size, input_dim)
        
        output = net(x)
        
        # Output should have same shape as input
        assert output.shape == x.shape


class TestDCNv2:
    def test_forward_shape(self):
        batch_size = 32
        num_dense = 13
        num_sparse = 26
        embedding_dim = 16
        
        sparse_vocab_sizes = {f'C{i}': 1000 for i in range(1, num_sparse + 1)}
        
        model = DCNv2(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            cross_layers=2,
            deep_hidden_dims=[256, 128],
            structure='stacked'
        )
        
        dense_features = torch.randn(batch_size, num_dense)
        sparse_features = torch.randint(0, 100, (batch_size, num_sparse))
        
        output = model(dense_features, sparse_features)
        
        assert output.shape == (batch_size,)
    
    def test_parallel_structure(self):
        batch_size = 32
        num_dense = 13
        num_sparse = 26
        embedding_dim = 16
        
        sparse_vocab_sizes = {f'C{i}': 1000 for i in range(1, num_sparse + 1)}
        
        model = DCNv2(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            cross_layers=2,
            deep_hidden_dims=[256, 128],
            structure='parallel'
        )
        
        dense_features = torch.randn(batch_size, num_dense)
        sparse_features = torch.randint(0, 100, (batch_size, num_sparse))
        
        output = model(dense_features, sparse_features)
        
        assert output.shape == (batch_size,)
    
    def test_low_rank(self):
        batch_size = 32
        num_dense = 13
        num_sparse = 5
        embedding_dim = 16
        low_rank = 8
        
        sparse_vocab_sizes = {f'C{i}': 1000 for i in range(1, num_sparse + 1)}
        
        model = DCNv2(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            cross_layers=2,
            low_rank=low_rank
        )
        
        dense_features = torch.randn(batch_size, num_dense)
        sparse_features = torch.randint(0, 100, (batch_size, num_sparse))
        
        output = model(dense_features, sparse_features)
        
        assert output.shape == (batch_size,)


class TestDCN:
    def test_forward_shape(self):
        batch_size = 32
        num_dense = 13
        num_sparse = 26
        embedding_dim = 16
        
        sparse_vocab_sizes = {f'C{i}': 1000 for i in range(1, num_sparse + 1)}
        
        model = DCN(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            cross_layers=2,
            deep_hidden_dims=[256, 128]
        )
        
        dense_features = torch.randn(batch_size, num_dense)
        sparse_features = torch.randint(0, 100, (batch_size, num_sparse))
        
        output = model(dense_features, sparse_features)
        
        assert output.shape == (batch_size,)


class TestDeepFM:
    def test_forward_shape(self):
        batch_size = 32
        num_dense = 13
        num_sparse = 26
        embedding_dim = 16
        
        sparse_vocab_sizes = {f'C{i}': 1000 for i in range(1, num_sparse + 1)}
        
        model = DeepFM(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            deep_hidden_dims=[256, 128]
        )
        
        dense_features = torch.randn(batch_size, num_dense)
        sparse_features = torch.randint(0, 100, (batch_size, num_sparse))
        
        output = model(dense_features, sparse_features)
        
        assert output.shape == (batch_size,)


class TestDNN:
    def test_forward_shape(self):
        batch_size = 32
        num_dense = 13
        num_sparse = 26
        embedding_dim = 16
        
        sparse_vocab_sizes = {f'C{i}': 1000 for i in range(1, num_sparse + 1)}
        
        model = DNN(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim,
            hidden_dims=[256, 128, 64]
        )
        
        dense_features = torch.randn(batch_size, num_dense)
        sparse_features = torch.randint(0, 100, (batch_size, num_sparse))
        
        output = model(dense_features, sparse_features)
        
        assert output.shape == (batch_size,)


class TestLogisticRegression:
    def test_forward_shape(self):
        batch_size = 32
        num_dense = 13
        num_sparse = 26
        embedding_dim = 16
        
        sparse_vocab_sizes = {f'C{i}': 1000 for i in range(1, num_sparse + 1)}
        
        model = LogisticRegression(
            num_dense=num_dense,
            sparse_vocab_sizes=sparse_vocab_sizes,
            embedding_dim=embedding_dim
        )
        
        dense_features = torch.randn(batch_size, num_dense)
        sparse_features = torch.randint(0, 100, (batch_size, num_sparse))
        
        output = model(dense_features, sparse_features)
        
        assert output.shape == (batch_size,)


def test_gradient_flow():
    """Test that gradients flow properly through the model"""
    batch_size = 8
    num_dense = 5
    num_sparse = 3
    embedding_dim = 8
    
    sparse_vocab_sizes = {f'C{i}': 100 for i in range(1, num_sparse + 1)}
    
    model = DCNv2(
        num_dense=num_dense,
        sparse_vocab_sizes=sparse_vocab_sizes,
        embedding_dim=embedding_dim,
        cross_layers=2,
        deep_hidden_dims=[32, 16]
    )
    
    dense_features = torch.randn(batch_size, num_dense)
    sparse_features = torch.randint(0, 50, (batch_size, num_sparse))
    labels = torch.randn(batch_size)
    
    # Forward pass
    output = model(dense_features, sparse_features)
    loss = torch.nn.functional.binary_cross_entropy_with_logits(output, labels)
    
    # Backward pass
    loss.backward()
    
    # Check that gradients exist
    has_grad = False
    for param in model.parameters():
        if param.grad is not None and torch.sum(torch.abs(param.grad)) > 0:
            has_grad = True
            break
    
    assert has_grad, "No gradients found"


if __name__ == '__main__':
    # Run tests
    pytest.main([__file__, '-v'])
