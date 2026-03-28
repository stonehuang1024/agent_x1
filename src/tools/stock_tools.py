"""
Stock Tools Module - Stock data retrieval and analysis.

Provides tools for:
- Kline/OHLCV data retrieval
- Stock snapshots
- Financial reports
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Union
from pathlib import Path

from ..core.tool import Tool
from ..core.config import PathConfig

logger = logging.getLogger(__name__)


def get_result_directory() -> Path:
    """Create and return timestamped result directory."""
    # Use config paths or defaults
    base_dir = Path(os.getenv("RESULT_DIR", "results")) / "stock_ana"
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    result_dir = base_dir / f"data_{timestamp}"
    result_dir.mkdir(parents=True, exist_ok=True)
    return result_dir


def save_dataframe_to_file(df, filename: str, result_dir: Path) -> str:
    """Save DataFrame to CSV and JSON files."""
    csv_path = result_dir / f"{filename}.csv"
    json_path = result_dir / f"{filename}.json"
    
    df.to_csv(csv_path, index=True)
    df.to_json(json_path, orient='records', indent=2)
    
    logger.info(f"[StockData] Saved to {csv_path}")
    return str(csv_path)


def get_stock_kline(
    symbols: Union[str, List[str]],
    period: str = "1mo",
    interval: str = "1d",
    start: Optional[str] = None,
    end: Optional[str] = None,
    save_to_file: bool = True,
    max_display_rows: int = 10
) -> Dict[str, Any]:
    """
    Get stock Kline/OHLCV data.
    
    Args:
        symbols: Stock symbol(s)
        period: Data period (1d, 5d, 1mo, 3mo, 6mo, 1y, etc.)
        interval: Data interval (1m, 2m, 5m, 15m, 30m, 60m, 1d, etc.)
        start: Start date (YYYY-MM-DD)
        end: End date (YYYY-MM-DD)
        save_to_file: Save to file
        max_display_rows: Max rows to include in response
        
    Returns:
        Kline data dictionary
    """
    try:
        import yfinance as yf
        import pandas as pd
    except ImportError:
        return {"error": "yfinance and pandas required. Run: pip install yfinance pandas"}
    
    if isinstance(symbols, str):
        symbols = [symbols]
    
    symbols = [s.upper().strip() for s in symbols]
    logger.info(f"[StockData] Fetching kline for {symbols}, period={period}, interval={interval}")
    
    result_dir = get_result_directory() if save_to_file else None
    all_data = {}
    files_saved = []
    errors = []
    
    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            
            if start and end:
                hist = ticker.history(start=start, end=end, interval=interval)
            else:
                hist = ticker.history(period=period, interval=interval)
            
            if hist.empty:
                errors.append(f"{symbol}: No data available")
                continue
            
            hist.reset_index(inplace=True)
            if 'Date' in hist.columns:
                hist['Date'] = hist['Date'].dt.strftime('%Y-%m-%d')
            
            records = hist.head(max_display_rows).to_dict('records')
            all_data[symbol] = {
                "total_rows": len(hist),
                "displayed_rows": len(records),
                "columns": list(hist.columns),
                "data": records
            }
            
            if save_to_file and result_dir:
                filepath = save_dataframe_to_file(hist, f"{symbol}_kline", result_dir)
                files_saved.append(filepath)
                all_data[symbol]["saved_file"] = filepath
                
        except Exception as e:
            logger.error(f"[StockData] Error fetching {symbol}: {e}")
            errors.append(f"{symbol}: {str(e)}")
    
    result = {
        "symbols": symbols,
        "period": period,
        "interval": interval,
        "data": all_data,
        "errors": errors if errors else None
    }
    
    if files_saved:
        result["files_saved"] = files_saved
        result["result_directory"] = str(result_dir)
    
    return result


def get_stock_snapshot(symbol: str) -> Dict[str, Any]:
    """
    Get real-time stock snapshot.
    
    Args:
        symbol: Stock symbol
        
    Returns:
        Snapshot data
    """
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance required"}
    
    try:
        ticker = yf.Ticker(symbol.upper().strip())
        info = ticker.info
        
        return {
            "symbol": symbol.upper(),
            "name": info.get("longName", info.get("shortName", "N/A")),
            "current_price": info.get("currentPrice", info.get("regularMarketPrice")),
            "previous_close": info.get("previousClose"),
            "open": info.get("open"),
            "day_high": info.get("dayHigh"),
            "day_low": info.get("dayLow"),
            "volume": info.get("volume"),
            "market_cap": info.get("marketCap"),
            "pe_ratio": info.get("trailingPE"),
            "52_week_high": info.get("fiftyTwoWeekHigh"),
            "52_week_low": info.get("fiftyTwoWeekLow")
        }
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


def get_stock_financials(symbol: str, statement_type: str = "income") -> Dict[str, Any]:
    """
    Get financial statements.
    
    Args:
        symbol: Stock symbol
        statement_type: Type (income, balance, cash)
        
    Returns:
        Financial data
    """
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance required"}
    
    try:
        ticker = yf.Ticker(symbol.upper().strip())
        
        if statement_type == "income":
            data = ticker.financials
        elif statement_type == "balance":
            data = ticker.balance_sheet
        elif statement_type == "cash":
            data = ticker.cashflow
        else:
            return {"error": f"Invalid statement type: {statement_type}"}
        
        if data is None or data.empty:
            return {"error": f"No {statement_type} data available for {symbol}"}
        
        # Convert to dict with string keys for JSON serialization
        result = {}
        for col in data.columns:
            col_str = col.strftime('%Y-%m-%d') if hasattr(col, 'strftime') else str(col)
            result[col_str] = data[col].dropna().to_dict()
        
        return {
            "symbol": symbol.upper(),
            "statement_type": statement_type,
            "data": result
        }
        
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


def get_stock_info(symbol: str) -> Dict[str, Any]:
    """
    Get comprehensive stock information.
    
    Args:
        symbol: Stock symbol
        
    Returns:
        Company info dictionary
    """
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance required"}
    
    try:
        ticker = yf.Ticker(symbol.upper().strip())
        info = ticker.info
        
        return {
            "symbol": symbol.upper(),
            "name": info.get("longName", info.get("shortName")),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "country": info.get("country"),
            "website": info.get("website"),
            "employees": info.get("fullTimeEmployees"),
            "summary": info.get("longBusinessSummary"),
            "market_cap": info.get("marketCap"),
            "enterprise_value": info.get("enterpriseValue"),
            "beta": info.get("beta"),
            "dividend_yield": info.get("dividendYield"),
            "ex_dividend_date": info.get("exDividendDate"),
            "fiscal_year_end": info.get("fiscalYearEnd")
        }
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


# Tool Definitions
GET_STOCK_KLINE_TOOL = Tool(
    name="get_stock_kline",
    description="Get stock OHLCV/kline data. Symbols like AAPL, MSFT, GOOGL. Periods: 1d, 5d, 1mo, 3mo, 6mo, 1y, etc. Intervals: 1m, 2m, 5m, 15m, 30m, 60m, 1d, etc.",
    parameters={
        "type": "object",
        "properties": {
            "symbols": {"type": "string", "description": "Stock symbol or comma-separated list"},
            "period": {"type": "string", "description": "Data period", "default": "1mo"},
            "interval": {"type": "string", "description": "Data interval", "default": "1d"},
            "start": {"type": "string", "description": "Start date (YYYY-MM-DD)"},
            "end": {"type": "string", "description": "End date (YYYY-MM-DD)"},
            "save_to_file": {"type": "boolean", "description": "Save to file", "default": True},
            "max_display_rows": {"type": "integer", "description": "Max rows in response", "default": 10}
        },
        "required": ["symbols"]
    },
    func=get_stock_kline,
    timeout_seconds=60,
    max_output_chars=50000,
    is_readonly=True,
)

GET_STOCK_SNAPSHOT_TOOL = Tool(
    name="get_stock_snapshot",
    description="Get real-time stock snapshot with current price, volume, day high/low, etc.",
    parameters={
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Stock symbol"}
        },
        "required": ["symbol"]
    },
    func=get_stock_snapshot,
    timeout_seconds=30,
    max_output_chars=10000,
    is_readonly=True,
)

GET_STOCK_FINANCIALS_TOOL = Tool(
    name="get_stock_financials",
    description="Get financial statements. Types: income, balance, cash.",
    parameters={
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Stock symbol"},
            "statement_type": {"type": "string", "enum": ["income", "balance", "cash"], "default": "income"}
        },
        "required": ["symbol"]
    },
    func=get_stock_financials,
    timeout_seconds=60,
    max_output_chars=50000,
    is_readonly=True,
)

GET_STOCK_INFO_TOOL = Tool(
    name="get_stock_info",
    description="Get company information including sector, industry, employees, summary.",
    parameters={
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "Stock symbol"}
        },
        "required": ["symbol"]
    },
    func=get_stock_info,
    timeout_seconds=30,
    max_output_chars=10000,
    is_readonly=True,
)
