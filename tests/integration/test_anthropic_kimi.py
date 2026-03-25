"""
Test Anthropic-style Kimi API with tool calls.
"""

import os
import sys
import logging

sys.path.insert(0, str(__file__).rsplit('/', 3)[0])

from src.engine.anthropic_engine import create_anthropic_engine
from src.tools import (
    WEATHER_TOOL,
    CALCULATOR_TOOL,
    TIME_TOOL,
    GET_STOCK_KLINE_TOOL,
    GET_STOCK_SNAPSHOT_TOOL,
    GET_STOCK_FINANCIALS_TOOL,
    GET_STOCK_INFO_TOOL,
    ANALYZE_STOCK_TOOL,
    EXA_SEARCH_TOOL,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


def main():
    """Test Anthropic engine with stock tools."""
    
    # Set API key from environment or use provided one
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        api_key = ""
    
    # Override base URL
    os.environ["ANTHROPIC_BASE_URL"] = "https://api.kimi.com/coding/"
    
    logger = logging.getLogger(__name__)
    logger.info("Creating Anthropic-style Kimi engine...")
    
    try:
        engine = create_anthropic_engine(
            api_key=api_key,
            model="kimi-k2.5",
            temperature=0.7,
            max_tokens=4096
        )
        
        # Register tools
        engine.register_tool(WEATHER_TOOL)
        engine.register_tool(CALCULATOR_TOOL)
        engine.register_tool(TIME_TOOL)
        engine.register_tool(GET_STOCK_KLINE_TOOL)
        engine.register_tool(GET_STOCK_SNAPSHOT_TOOL)
        engine.register_tool(GET_STOCK_FINANCIALS_TOOL)
        engine.register_tool(GET_STOCK_INFO_TOOL)
        engine.register_tool(ANALYZE_STOCK_TOOL)
        engine.register_tool(EXA_SEARCH_TOOL)
        
        logger.info(f"Registered {len(engine.tools)} tools")
        
        # Test simple query
        logger.info("\n" + "="*60)
        logger.info("Test 1: Simple calculation")
        logger.info("="*60)
        response = engine.chat("What is 150 * 2.5?")
        logger.info(f"Response: {response}")
        
        # Test stock query
        logger.info("\n" + "="*60)
        logger.info("Test 2: Stock snapshot")
        logger.info("="*60)
        response = engine.chat("Get the current stock price for Apple (AAPL)")
        logger.info(f"Response: {response[:500]}...")
        
        logger.info("\n✅ All tests passed!")
        return 0
        
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
