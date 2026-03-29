"""Stock analysis tools for Agent X1.

Provides technical analysis capabilities including:
- Technical indicators: SMA, EMA, RSI, MACD, Bollinger Bands, ATR, Stochastic, OBV
- Candlestick charts with indicators
- Signal generation (buy/sell signals)
- Automatic plot saving to timestamped directories
"""
from __future__ import annotations  # Defer type annotation evaluation
import os
import sys
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, TYPE_CHECKING
from pathlib import Path

from ..core.tool import Tool
from .stock_tools import get_result_directory

logger = logging.getLogger(__name__)

# Lazy-loaded heavy dependencies — these libraries consume hundreds of MB
# of memory and should only be loaded when actually needed, not at import
# time (which happens whenever any test imports from src.tools).
_yf = None
_pd = None
_np = None
_plt = None
_mdates = None
_Rectangle = None


def _ensure_deps():
    """Lazily import heavy dependencies on first use."""
    global _yf, _pd, _np, _plt, _mdates, _Rectangle
    if _pd is not None:
        return
    import yfinance as yf
    import pandas as pd
    import numpy as np
    import matplotlib
    matplotlib.use('Agg')  # Use non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.patches import Rectangle
    _yf = yf
    _pd = pd
    _np = np
    _plt = plt
    _mdates = mdates
    _Rectangle = Rectangle


def calculate_sma(data: pd.Series, period: int) -> pd.Series:
    """Calculate Simple Moving Average."""
    return data.rolling(window=period).mean()


def calculate_ema(data: pd.Series, period: int) -> pd.Series:
    """Calculate Exponential Moving Average."""
    return data.ewm(span=period, adjust=False).mean()


def calculate_rsi(data: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index."""
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_macd(data: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate MACD, Signal line, and Histogram."""
    ema_fast = calculate_ema(data, fast)
    ema_slow = calculate_ema(data, slow)
    macd = ema_fast - ema_slow
    signal_line = calculate_ema(macd, signal)
    histogram = macd - signal_line
    return macd, signal_line, histogram


def calculate_bollinger_bands(data: pd.Series, period: int = 20, std_dev: int = 2) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate Bollinger Bands (middle, upper, lower)."""
    middle = calculate_sma(data, period)
    std = data.rolling(window=period).std()
    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)
    return middle, upper, lower


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Average True Range."""
    high_low = high - low
    high_close = _np.abs(high - close.shift())
    low_close = _np.abs(low - close.shift())
    ranges = _pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    return true_range.rolling(window=period).mean()


def calculate_stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k_period: int = 14, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
    """Calculate Stochastic Oscillator (%K and %D)."""
    lowest_low = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()
    k = 100 * ((close - lowest_low) / (highest_high - lowest_low))
    d = k.rolling(window=d_period).mean()
    return k, d


def calculate_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """Calculate On-Balance Volume."""
    obv = _pd.Series(index=close.index, dtype=float)
    obv.iloc[0] = volume.iloc[0]
    for i in range(1, len(close)):
        if close.iloc[i] > close.iloc[i-1]:
            obv.iloc[i] = obv.iloc[i-1] + volume.iloc[i]
        elif close.iloc[i] < close.iloc[i-1]:
            obv.iloc[i] = obv.iloc[i-1] - volume.iloc[i]
        else:
            obv.iloc[i] = obv.iloc[i-1]
    return obv


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add all technical indicators to the DataFrame."""
    df = df.copy()
    
    if 'Close' not in df.columns:
        logger.error("[StockAnalysis] DataFrame missing 'Close' column")
        return df
    
    close = df['Close']
    high = df['High'] if 'High' in df.columns else close
    low = df['Low'] if 'Low' in df.columns else close
    volume = df['Volume'] if 'Volume' in df.columns else _pd.Series(1, index=df.index)
    
    # Moving Averages
    df['SMA_5'] = calculate_sma(close, 5)
    df['SMA_10'] = calculate_sma(close, 10)
    df['SMA_20'] = calculate_sma(close, 20)
    df['SMA_50'] = calculate_sma(close, 50)
    df['SMA_200'] = calculate_sma(close, 200)
    
    df['EMA_12'] = calculate_ema(close, 12)
    df['EMA_26'] = calculate_ema(close, 26)
    
    # MACD
    df['MACD'], df['MACD_Signal'], df['MACD_Hist'] = calculate_macd(close)
    
    # RSI
    df['RSI'] = calculate_rsi(close)
    
    # Bollinger Bands
    df['BB_Middle'], df['BB_Upper'], df['BB_Lower'] = calculate_bollinger_bands(close)
    df['BB_Width'] = (df['BB_Upper'] - df['BB_Lower']) / df['BB_Middle']
    df['BB_Percent'] = (close - df['BB_Lower']) / (df['BB_Upper'] - df['BB_Lower'])
    
    # ATR
    df['ATR'] = calculate_atr(high, low, close)
    
    # Stochastic
    df['Stoch_K'], df['Stoch_D'] = calculate_stochastic(high, low, close)
    
    # OBV
    df['OBV'] = calculate_obv(close, volume)
    
    # Price changes
    df['Price_Change'] = close.pct_change()
    df['Price_Change_Abs'] = close.diff()
    
    # Volatility (20-day rolling std)
    df['Volatility'] = df['Price_Change'].rolling(window=20).std() * _np.sqrt(252)
    
    logger.info(f"[StockAnalysis] Added {len([c for c in df.columns if c not in ['Open', 'High', 'Low', 'Close', 'Volume']])} indicators")
    
    return df


def generate_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Generate buy/sell signals based on indicator combinations."""
    df = df.copy()
    
    close = df['Close']
    
    # Signal initialization
    df['Signal_Buy'] = 0
    df['Signal_Sell'] = 0
    
    # RSI signals
    if 'RSI' in df.columns:
        df.loc[df['RSI'] < 30, 'Signal_Buy'] += 1
        df.loc[df['RSI'] > 70, 'Signal_Sell'] += 1
    
    # MACD signals
    if 'MACD' in df.columns and 'MACD_Signal' in df.columns:
        df.loc[(df['MACD'] > df['MACD_Signal']) & (df['MACD'].shift(1) <= df['MACD_Signal'].shift(1)), 'Signal_Buy'] += 1
        df.loc[(df['MACD'] < df['MACD_Signal']) & (df['MACD'].shift(1) >= df['MACD_Signal'].shift(1)), 'Signal_Sell'] += 1
    
    # Bollinger Band signals
    if 'BB_Lower' in df.columns and 'BB_Upper' in df.columns:
        df.loc[close < df['BB_Lower'], 'Signal_Buy'] += 1
        df.loc[close > df['BB_Upper'], 'Signal_Sell'] += 1
    
    # Golden/Death Cross (SMA 50/200)
    if 'SMA_50' in df.columns and 'SMA_200' in df.columns:
        df.loc[(df['SMA_50'] > df['SMA_200']) & (df['SMA_50'].shift(1) <= df['SMA_200'].shift(1)), 'Signal_Buy'] += 2
        df.loc[(df['SMA_50'] < df['SMA_200']) & (df['SMA_50'].shift(1) >= df['SMA_200'].shift(1)), 'Signal_Sell'] += 2
    
    # Final signal classification
    df['Signal'] = 'Hold'
    df.loc[df['Signal_Buy'] >= 2, 'Signal'] = 'Strong Buy'
    df.loc[(df['Signal_Buy'] == 1), 'Signal'] = 'Buy'
    df.loc[df['Signal_Sell'] >= 2, 'Signal'] = 'Strong Sell'
    df.loc[(df['Signal_Sell'] == 1), 'Signal'] = 'Sell'
    
    return df


def plot_stock_analysis(
    df: pd.DataFrame,
    symbol: str,
    result_dir: Path,
    show_indicators: List[str] = ['SMA', 'MACD', 'RSI', 'BB']
) -> str:
    """
    Create comprehensive stock analysis chart with indicators.
    
    Returns path to saved plot file.
    """
    # Prepare data
    if 'Date' in df.columns:
        df['Date'] = _pd.to_datetime(df['Date'])
        dates = df['Date']
    elif 'Datetime' in df.columns:
        df['Datetime'] = _pd.to_datetime(df['Datetime'])
        dates = df['Datetime']
    else:
        dates = df.index
    
    # Calculate subplot layout
    num_plots = 2  # Price + Volume
    if 'RSI' in df.columns and 'RSI' in show_indicators:
        num_plots += 1
    if 'MACD' in df.columns and 'MACD' in show_indicators:
        num_plots += 1
    if 'Stoch_K' in df.columns:
        num_plots += 1
    
    fig, axes = _plt.subplots(num_plots, 1, figsize=(14, 4 * num_plots), sharex=True)
    if num_plots == 1:
        axes = [axes]
    
    plot_idx = 0
    
    # Main price chart
    ax_price = axes[plot_idx]
    ax_price.plot(dates, df['Close'], label='Close', color='black', linewidth=1.5)
    
    # Add moving averages
    if 'SMA' in show_indicators:
        if 'SMA_20' in df.columns:
            ax_price.plot(dates, df['SMA_20'], label='SMA 20', color='blue', alpha=0.7)
        if 'SMA_50' in df.columns:
            ax_price.plot(dates, df['SMA_50'], label='SMA 50', color='orange', alpha=0.7)
        if 'SMA_200' in df.columns:
            ax_price.plot(dates, df['SMA_200'], label='SMA 200', color='red', alpha=0.7)
    
    # Add Bollinger Bands
    if 'BB' in show_indicators:
        if 'BB_Upper' in df.columns:
            ax_price.fill_between(dates, df['BB_Upper'], df['BB_Lower'], alpha=0.1, color='gray', label='Bollinger Bands')
            ax_price.plot(dates, df['BB_Upper'], color='gray', linestyle='--', alpha=0.5)
            ax_price.plot(dates, df['BB_Lower'], color='gray', linestyle='--', alpha=0.5)
    
    # Add signals
    if 'Signal' in df.columns:
        buy_signals = df[df['Signal'].isin(['Buy', 'Strong Buy'])]
        sell_signals = df[df['Signal'].isin(['Sell', 'Strong Sell'])]
        if len(buy_signals) > 0:
            buy_dates = dates[df['Signal'].isin(['Buy', 'Strong Buy'])]
            ax_price.scatter(buy_dates, buy_signals['Close'], color='green', marker='^', s=100, label='Buy', zorder=5)
        if len(sell_signals) > 0:
            sell_dates = dates[df['Signal'].isin(['Sell', 'Strong Sell'])]
            ax_price.scatter(sell_dates, sell_signals['Close'], color='red', marker='v', s=100, label='Sell', zorder=5)
    
    ax_price.set_title(f'{symbol} Stock Analysis', fontsize=14, fontweight='bold')
    ax_price.set_ylabel('Price')
    ax_price.legend(loc='upper left', fontsize=8)
    ax_price.grid(True, alpha=0.3)
    
    plot_idx += 1
    
    # Volume chart
    if plot_idx < len(axes) and 'Volume' in df.columns:
        ax_volume = axes[plot_idx]
        colors = ['green' if df['Close'].iloc[i] >= df['Close'].iloc[i-1] else 'red' 
                  for i in range(1, len(df))]
        colors.insert(0, 'gray')
        ax_volume.bar(dates, df['Volume'], color=colors, alpha=0.6, width=0.8)
        ax_volume.set_ylabel('Volume')
        ax_volume.grid(True, alpha=0.3)
        plot_idx += 1
    
    # RSI
    if 'RSI' in df.columns and 'RSI' in show_indicators and plot_idx < len(axes):
        ax_rsi = axes[plot_idx]
        ax_rsi.plot(dates, df['RSI'], color='purple', linewidth=1.5)
        ax_rsi.axhline(y=70, color='red', linestyle='--', alpha=0.5, label='Overbought (70)')
        ax_rsi.axhline(y=30, color='green', linestyle='--', alpha=0.5, label='Oversold (30)')
        ax_rsi.fill_between(dates, 30, 70, alpha=0.1, color='gray')
        ax_rsi.set_ylabel('RSI')
        ax_rsi.set_ylim(0, 100)
        ax_rsi.legend(loc='upper left', fontsize=8)
        ax_rsi.grid(True, alpha=0.3)
        plot_idx += 1
    
    # MACD
    if 'MACD' in df.columns and 'MACD' in show_indicators and plot_idx < len(axes):
        ax_macd = axes[plot_idx]
        ax_macd.plot(dates, df['MACD'], label='MACD', color='blue', linewidth=1.2)
        ax_macd.plot(dates, df['MACD_Signal'], label='Signal', color='red', linewidth=1.2)
        colors_macd = ['green' if df['MACD_Hist'].iloc[i] >= 0 else 'red' for i in range(len(df))]
        ax_macd.bar(dates, df['MACD_Hist'], color=colors_macd, alpha=0.6, width=0.8, label='Histogram')
        ax_macd.axhline(y=0, color='black', linestyle='-', alpha=0.3)
        ax_macd.set_ylabel('MACD')
        ax_macd.legend(loc='upper left', fontsize=8)
        ax_macd.grid(True, alpha=0.3)
        plot_idx += 1
    
    # Stochastic
    if 'Stoch_K' in df.columns and plot_idx < len(axes):
        ax_stoch = axes[plot_idx]
        ax_stoch.plot(dates, df['Stoch_K'], label='%K', color='blue', linewidth=1.2)
        ax_stoch.plot(dates, df['Stoch_D'], label='%D', color='red', linewidth=1.2)
        ax_stoch.axhline(y=80, color='red', linestyle='--', alpha=0.5)
        ax_stoch.axhline(y=20, color='green', linestyle='--', alpha=0.5)
        ax_stoch.fill_between(dates, 20, 80, alpha=0.1, color='gray')
        ax_stoch.set_ylabel('Stochastic')
        ax_stoch.set_ylim(0, 100)
        ax_stoch.legend(loc='upper left', fontsize=8)
        ax_stoch.grid(True, alpha=0.3)
        plot_idx += 1
    
    # Format x-axis
    if plot_idx > 0:
        axes[-1].xaxis.set_major_formatter(_mdates.DateFormatter('%Y-%m-%d'))
        axes[-1].xaxis.set_major_locator(_mdates.AutoDateLocator())
        _plt.xticks(rotation=45)
    
    _plt.tight_layout()
    
    # Save plot
    plot_filename = f"{symbol}_analysis.png"
    plot_path = result_dir / plot_filename
    _plt.savefig(plot_path, dpi=150, bbox_inches='tight', facecolor='white')
    _plt.close()
    
    logger.info(f"[StockAnalysis] Saved plot to {plot_path}")
    return str(plot_path)


def analyze_stock(
    symbol: str,
    period: str = "3mo",
    interval: str = "1d",
    generate_plot: bool = True,
    indicators: List[str] = ["SMA", "EMA", "MACD", "RSI", "BB", "ATR", "Stochastic", "OBV"],
    save_data: bool = True
) -> Dict[str, Any]:
    """
    Comprehensive stock analysis with technical indicators and optional plotting.
    
    Args:
        symbol: Stock symbol (e.g., "AAPL")
        period: Data period (1d to max)
        interval: Data interval (1m to 3mo)
        generate_plot: Whether to generate and save analysis chart
        indicators: List of indicators to calculate
        save_data: Whether to save processed data to file
    
    Returns:
        Dictionary with analysis results, indicator values, signals, and file paths
    """
    symbol = symbol.upper().strip()
    logger.info(f"[StockAnalysis] Analyzing {symbol}, period={period}, interval={interval}")
    
    # Load heavy dependencies on first actual use
    _ensure_deps()
    
    try:
        # Fetch data
        ticker = _yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        
        if df.empty:
            return {
                "symbol": symbol,
                "error": "No data available for the specified period"
            }
        
        logger.info(f"[StockAnalysis] Retrieved {len(df)} rows of data")
        
        # Reset index to get Date as column
        df.reset_index(inplace=True)
        if 'Date' in df.columns:
            df['Date'] = _pd.to_datetime(df['Date'])
        elif 'Datetime' in df.columns:
            df['Date'] = _pd.to_datetime(df['Datetime'])
        
        # Add indicators
        df = add_all_indicators(df)
        
        # Generate signals
        df = generate_signals(df)
        
        # Create result directory
        result_dir = get_result_directory()
        
        # Calculate summary statistics
        close = df['Close']
        latest = df.iloc[-1]
        
        summary = {
            "symbol": symbol,
            "period": period,
            "interval": interval,
            "total_rows": len(df),
            "date_range": {
                "start": df['Date'].iloc[0].strftime('%Y-%m-%d') if hasattr(df['Date'].iloc[0], 'strftime') else str(df['Date'].iloc[0]),
                "end": df['Date'].iloc[-1].strftime('%Y-%m-%d') if hasattr(df['Date'].iloc[-1], 'strftime') else str(df['Date'].iloc[-1])
            },
            "price_stats": {
                "current": round(latest['Close'], 2),
                "open": round(latest['Open'], 2) if 'Open' in df.columns else None,
                "high": round(latest['High'], 2) if 'High' in df.columns else None,
                "low": round(latest['Low'], 2) if 'Low' in df.columns else None,
                "change_pct": round((latest['Close'] - df['Close'].iloc[0]) / df['Close'].iloc[0] * 100, 2),
                "volatility": round(latest['Volatility'] * 100, 2) if 'Volatility' in df.columns else None
            },
            "volume_stats": {
                "current": int(latest['Volume']) if 'Volume' in df.columns else None,
                "avg_20d": int(df['Volume'].tail(20).mean()) if 'Volume' in df.columns else None
            } if 'Volume' in df.columns else None
        }
        
        # Add indicator values
        indicator_summary = {}
        
        if 'RSI' in df.columns and 'RSI' in indicators:
            indicator_summary['RSI'] = {
                "current": round(latest['RSI'], 2),
                "signal": "Oversold" if latest['RSI'] < 30 else "Overbought" if latest['RSI'] > 70 else "Neutral"
            }
        
        if 'MACD' in df.columns and 'MACD' in indicators:
            indicator_summary['MACD'] = {
                "macd": round(latest['MACD'], 4),
                "signal": round(latest['MACD_Signal'], 4),
                "histogram": round(latest['MACD_Hist'], 4),
                "trend": "Bullish" if latest['MACD'] > latest['MACD_Signal'] else "Bearish"
            }
        
        if 'SMA' in indicators:
            indicator_summary['Moving_Averages'] = {
                "SMA_5": round(latest['SMA_5'], 2) if 'SMA_5' in df.columns else None,
                "SMA_20": round(latest['SMA_20'], 2) if 'SMA_20' in df.columns else None,
                "SMA_50": round(latest['SMA_50'], 2) if 'SMA_50' in df.columns else None,
                "SMA_200": round(latest['SMA_200'], 2) if 'SMA_200' in df.columns else None,
                "trend_50_200": "Golden Cross (Bullish)" if ('SMA_50' in df.columns and 'SMA_200' in df.columns and latest['SMA_50'] > latest['SMA_200']) else "Death Cross (Bearish)" if 'SMA_50' in df.columns and 'SMA_200' in df.columns else None
            }
        
        if 'BB' in df.columns and 'BB' in indicators:
            indicator_summary['Bollinger_Bands'] = {
                "upper": round(latest['BB_Upper'], 2),
                "middle": round(latest['BB_Middle'], 2),
                "lower": round(latest['BB_Lower'], 2),
                "width_pct": round(latest['BB_Width'] * 100, 2),
                "position": round(latest['BB_Percent'] * 100, 2)
            }
        
        if 'ATR' in df.columns and 'ATR' in indicators:
            indicator_summary['ATR'] = {
                "current": round(latest['ATR'], 2),
                "atr_pct": round(latest['ATR'] / latest['Close'] * 100, 2)
            }
        
        if 'Stochastic' in indicators and 'Stoch_K' in df.columns:
            indicator_summary['Stochastic'] = {
                "k": round(latest['Stoch_K'], 2),
                "d": round(latest['Stoch_D'], 2),
                "signal": "Oversold" if latest['Stoch_K'] < 20 else "Overbought" if latest['Stoch_K'] > 80 else "Neutral"
            }
        
        summary["indicators"] = indicator_summary
        
        # Trading signals summary
        latest_signal = latest['Signal'] if 'Signal' in df.columns else 'Hold'
        signal_counts = df['Signal'].value_counts().to_dict() if 'Signal' in df.columns else {}
        
        summary["signals"] = {
            "current": latest_signal,
            "history": {k: int(v) for k, v in signal_counts.items()},
            "buy_signals_30d": int(df.tail(30)['Signal'].isin(['Buy', 'Strong Buy']).sum()) if len(df) >= 30 else int(df['Signal'].isin(['Buy', 'Strong Buy']).sum()),
            "sell_signals_30d": int(df.tail(30)['Signal'].isin(['Sell', 'Strong Sell']).sum()) if len(df) >= 30 else int(df['Signal'].isin(['Sell', 'Strong Sell']).sum())
        }
        
        # Save data
        files_saved = []
        
        if save_data:
            # Save processed data with indicators
            data_filename = f"{symbol}_processed_data"
            data_path = result_dir / f"{data_filename}.csv"
            df.to_csv(data_path, index=False)
            files_saved.append(str(data_path))
            
            # Save summary as JSON
            summary_path = result_dir / f"{symbol}_analysis_summary.json"
            with open(summary_path, 'w') as f:
                json.dump(summary, f, indent=2, default=str)
            files_saved.append(str(summary_path))
            
            logger.info(f"[StockAnalysis] Saved data to {result_dir}")
        
        # Generate plot
        plot_path = None
        if generate_plot:
            plot_path = plot_stock_analysis(df, symbol, result_dir, show_indicators=indicators)
            files_saved.append(plot_path)
        
        return {
            "symbol": symbol,
            "analysis_summary": summary,
            "save_directory": str(result_dir),
            "files_saved": files_saved,
            "total_data_points": len(df),
            "indicators_calculated": len([c for c in df.columns if c not in ['Date', 'Datetime', 'Open', 'High', 'Low', 'Close', 'Volume']]),
            "success": True
        }
        
    except Exception as e:
        logger.exception(f"[StockAnalysis] Failed to analyze {symbol}")
        return {
            "symbol": symbol,
            "error": str(e),
            "success": False
        }


# Tool definition
ANALYZE_STOCK_TOOL = Tool(
    name="analyze_stock",
    description=(
        "Comprehensive technical analysis of a stock with indicators and chart generation. "
        "Calculates SMA, EMA, MACD, RSI, Bollinger Bands, ATR, Stochastic, OBV. "
        "Generates buy/sell signals and saves analysis charts to files. "
        "Example: analyze_stock('AAPL', period='3mo', interval='1d', generate_plot=True)"
    ),
    parameters={
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Stock symbol (e.g., 'AAPL', 'MSFT', 'TSLA')"
            },
            "period": {
                "type": "string",
                "enum": ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"],
                "default": "3mo",
                "description": "Analysis period"
            },
            "interval": {
                "type": "string",
                "enum": ["1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"],
                "default": "1d",
                "description": "Data interval"
            },
            "generate_plot": {
                "type": "boolean",
                "default": True,
                "description": "Generate and save analysis chart"
            },
            "indicators": {
                "type": "array",
                "items": {"type": "string"},
                "default": ["SMA", "EMA", "MACD", "RSI", "BB", "ATR", "Stochastic", "OBV"],
                "description": "Indicators to calculate"
            },
            "save_data": {
                "type": "boolean",
                "default": True,
                "description": "Save processed data to file"
            }
        },
        "required": ["symbol"]
    },
    func=analyze_stock,
    timeout_seconds=180,
    max_output_chars=30000,
    is_readonly=True,
)
