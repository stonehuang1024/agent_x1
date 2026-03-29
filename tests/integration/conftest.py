"""Shared fixtures and configuration for integration tests."""

import pytest


def pytest_addoption(parser):
    """Add --run-network option to pytest CLI."""
    parser.addoption(
        "--run-network",
        action="store_true",
        default=False,
        help="Run tests that require external network access (arXiv, Kimi, etc.)",
    )


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "network: mark test as requiring external network access"
    )


def pytest_collection_modifyitems(config, items):
    """Skip network tests unless --run-network is given."""
    if config.getoption("--run-network"):
        return  # User explicitly asked for network tests

    skip_network = pytest.mark.skip(
        reason="Needs --run-network option to run (requires external network access)"
    )
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip_network)
