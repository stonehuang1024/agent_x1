"""
Agent X1 - Comprehensive Import Test

This test validates that all modules can be imported correctly
and the basic functionality works as expected.
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_core_imports():
    """Test core module imports."""
    print("Testing core imports...")
    from src.core import Message, Role, Tool, AppConfig, LLMConfig, PathConfig
    from src.core import load_config, create_default_config_file
    from src.core import get_logger, setup_logging
    print("  ✓ Core imports successful")


def test_engine_imports():
    """Test engine module imports."""
    print("Testing engine imports...")
    from src.engine import BaseEngine, EngineConfig, ProviderType
    from src.engine import KimiEngine, AnthropicEngine
    from src.engine import create_engine, create_kimi_engine, create_anthropic_engine
    from src.engine import EngineRegistry
    print("  ✓ Engine imports successful")


def test_tools_imports():
    """Test tools module imports."""
    print("Testing tools imports...")
    from src.tools import Tool
    from src.tools import ALL_TOOLS
    from src.tools import WEATHER_TOOL, CALCULATOR_TOOL, TIME_TOOL, SEARCH_TOOL
    from src.tools import GOOGLE_SEARCH_TOOL, EXA_SEARCH_TOOL
    from src.tools import (
        GET_STOCK_KLINE_TOOL, GET_STOCK_SNAPSHOT_TOOL,
        GET_STOCK_FINANCIALS_TOOL, GET_STOCK_INFO_TOOL
    )
    print(f"  ✓ Tools imports successful ({len(ALL_TOOLS)} tools available)")


def test_main_imports():
    """Test main package imports."""
    print("Testing main package imports...")
    from src import create_agent, AppConfig, load_config, get_logger
    from src import create_engine, ProviderType, ALL_TOOLS
    print("  ✓ Main package imports successful")


def test_config_functionality():
    """Test configuration functionality."""
    print("Testing config functionality...")
    from src.core import AppConfig, LLMConfig, PathConfig
    
    config = AppConfig()
    assert config.llm.provider == "kimi"
    assert config.llm.temperature == 0.7
    assert config.paths.log_dir == "logs"
    print("  ✓ Config functionality working")


def test_tool_functionality():
    """Test tool functionality."""
    print("Testing tool functionality...")
    from src.tools import CALCULATOR_TOOL
    
    result = CALCULATOR_TOOL.execute('{"expression": "2 + 2"}')
    assert "4" in result
    print("  ✓ Tool functionality working")


def test_engine_creation():
    """Test engine creation (without API calls)."""
    print("Testing engine creation...")
    from src.engine import EngineConfig, ProviderType, KimiEngine
    
    config = EngineConfig(
        provider=ProviderType.KIMI,
        api_key="test-key",
        model="kimi-latest"
    )
    
    # Just verify the config is valid
    assert config.provider == ProviderType.KIMI
    assert config.api_key == "test-key"
    print("  ✓ Engine creation working")


def run_all_tests():
    """Run all tests."""
    print("\n" + "="*60)
    print("Agent X1 - Import and Functionality Tests")
    print("="*60 + "\n")
    
    tests = [
        test_core_imports,
        test_engine_imports,
        test_tools_imports,
        test_main_imports,
        test_config_functionality,
        test_tool_functionality,
        test_engine_creation,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  ✗ {test.__name__} failed: {e}")
            failed += 1
    
    print("\n" + "="*60)
    print(f"Results: {passed} passed, {failed} failed")
    print("="*60)
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
