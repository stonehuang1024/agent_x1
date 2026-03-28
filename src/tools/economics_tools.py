"""
Economics Tools Module - Macroeconomic data and analysis.

Provides tools for:
- FRED (Federal Reserve Economic Data) indicators
- World Bank development indicators
- Exchange rates
- Economic report generation
"""

import logging
import os
from typing import Dict, Any, List, Optional

from ..core.tool import Tool

logger = logging.getLogger(__name__)


def get_fred_series(
    series_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100
) -> Dict[str, Any]:
    """
    Fetch economic time series from FRED (Federal Reserve Economic Data).

    Common series IDs:
      GDP - US Gross Domestic Product
      UNRATE - US Unemployment Rate
      CPIAUCSL - Consumer Price Index (inflation)
      FEDFUNDS - Federal Funds Rate
      DGS10 - 10-Year Treasury Yield
      M2SL - M2 Money Supply
      INDPRO - Industrial Production Index
      PAYEMS - Nonfarm Payrolls

    Args:
        series_id: FRED series identifier
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        limit: Max data points to return (default: 100)

    Returns:
        Dictionary with series data and metadata
    """
    try:
        import requests
    except ImportError:
        return {"error": "requests not installed"}

    api_key = os.getenv("FRED_API_KEY", "")
    if not api_key:
        return {
            "error": "FRED_API_KEY environment variable not set. "
                     "Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html"
        }

    base_url = "https://api.stlouisfed.org/fred"
    logger.info(f"[FRED] Fetching series: {series_id}")

    try:
        # Get series info
        info_resp = requests.get(
            f"{base_url}/series",
            params={"series_id": series_id, "api_key": api_key, "file_type": "json"},
            timeout=15
        )
        info_resp.raise_for_status()
        series_info = info_resp.json().get("seriess", [{}])[0]

        # Get observations
        obs_params: Dict[str, Any] = {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "limit": limit,
            "sort_order": "desc"
        }
        if start_date:
            obs_params["observation_start"] = start_date
        if end_date:
            obs_params["observation_end"] = end_date

        obs_resp = requests.get(f"{base_url}/series/observations", params=obs_params, timeout=15)
        obs_resp.raise_for_status()
        observations = obs_resp.json().get("observations", [])

        data_points = [
            {"date": o["date"], "value": o["value"]}
            for o in observations
            if o["value"] != "."
        ]

        return {
            "series_id": series_id,
            "title": series_info.get("title", ""),
            "units": series_info.get("units_short", series_info.get("units", "")),
            "frequency": series_info.get("frequency_short", ""),
            "seasonal_adjustment": series_info.get("seasonal_adjustment_short", ""),
            "last_updated": series_info.get("last_updated", ""),
            "count": len(data_points),
            "data": data_points
        }
    except Exception as e:
        logger.exception("[FRED] Failed")
        return {"error": str(e), "series_id": series_id}


def get_world_bank_indicator(
    indicator: str,
    country: str = "US",
    start_year: Optional[int] = None,
    end_year: Optional[int] = None
) -> Dict[str, Any]:
    """
    Fetch World Bank development indicators.

    Common indicators:
      NY.GDP.MKTP.CD - GDP (current US$)
      NY.GDP.PCAP.CD - GDP per capita (current US$)
      SP.POP.TOTL - Total population
      FP.CPI.TOTL.ZG - Inflation (CPI %)
      SL.UEM.TOTL.ZS - Unemployment rate
      NE.EXP.GNFS.ZS - Exports (% of GDP)
      NE.IMP.GNFS.ZS - Imports (% of GDP)
      GC.DOD.TOTL.GD.ZS - Central government debt (% of GDP)

    Country codes: US, CN, DE, JP, GB, FR, IN, BR, CA, AU, etc.

    Args:
        indicator: World Bank indicator code
        country: ISO 2-letter country code (default: US)
        start_year: Start year
        end_year: End year

    Returns:
        Dictionary with indicator data
    """
    try:
        import requests
    except ImportError:
        return {"error": "requests not installed"}

    base_url = "https://api.worldbank.org/v2"
    logger.info(f"[WorldBank] Fetching {indicator} for {country}")

    try:
        params: Dict[str, Any] = {"format": "json", "per_page": 60}
        if start_year:
            params["date"] = f"{start_year}:{end_year or 2024}"

        url = f"{base_url}/country/{country}/indicator/{indicator}"
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()

        response_json = resp.json()
        if len(response_json) < 2:
            return {"error": "No data returned from World Bank API"}

        meta = response_json[0]
        raw_data = response_json[1] or []

        data_points = [
            {"year": item["date"], "value": item["value"]}
            for item in raw_data
            if item.get("value") is not None
        ]
        data_points.sort(key=lambda x: x["year"])

        indicator_name = raw_data[0].get("indicator", {}).get("value", indicator) if raw_data else indicator
        country_name = raw_data[0].get("country", {}).get("value", country) if raw_data else country

        return {
            "indicator": indicator,
            "indicator_name": indicator_name,
            "country": country,
            "country_name": country_name,
            "total_records": meta.get("total", len(data_points)),
            "count": len(data_points),
            "data": data_points
        }
    except Exception as e:
        logger.exception("[WorldBank] Failed")
        return {"error": str(e), "indicator": indicator, "country": country}


def get_exchange_rates(base_currency: str = "USD", target_currencies: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Get current currency exchange rates using Open Exchange Rates API.

    Uses the free exchangerate-api.com endpoint (no key required for base rates).

    Args:
        base_currency: Base currency code (default: USD)
        target_currencies: List of target currency codes. None = all available.

    Returns:
        Dictionary with exchange rates
    """
    try:
        import requests
    except ImportError:
        return {"error": "requests not installed"}

    logger.info(f"[ExchangeRates] Base: {base_currency}")

    try:
        url = f"https://open.er-api.com/v6/latest/{base_currency.upper()}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("result") != "success":
            return {"error": data.get("error-type", "API error"), "base": base_currency}

        rates = data.get("rates", {})

        if target_currencies:
            rates = {k: v for k, v in rates.items() if k.upper() in [c.upper() for c in target_currencies]}

        return {
            "base_currency": data.get("base_code", base_currency.upper()),
            "last_updated": data.get("time_last_update_utc", ""),
            "next_update": data.get("time_next_update_utc", ""),
            "rate_count": len(rates),
            "rates": rates
        }
    except Exception as e:
        logger.exception("[ExchangeRates] Failed")
        return {"error": str(e), "base_currency": base_currency}


def get_economic_calendar(country: str = "US", days_ahead: int = 7) -> Dict[str, Any]:
    """
    Get upcoming economic events/releases using TradingEconomics (requires API key)
    or return a structured placeholder with common release types.

    Falls back to a structured description of common economic calendar events
    if TRADING_ECONOMICS_API_KEY is not set.

    Args:
        country: Country name or code
        days_ahead: Number of days ahead to look

    Returns:
        Dictionary with upcoming economic events
    """
    try:
        import requests
        from datetime import datetime, timedelta
    except ImportError:
        return {"error": "requests not installed"}

    api_key = os.getenv("TRADING_ECONOMICS_API_KEY", "")

    if api_key:
        try:
            from datetime import datetime, timedelta
            start = datetime.now().strftime("%Y-%m-%d")
            end = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
            url = f"https://api.tradingeconomics.com/calendar/country/{country}/{start}/{end}"
            resp = requests.get(url, params={"c": api_key, "f": "json"}, timeout=10)
            resp.raise_for_status()
            events = resp.json()
            return {
                "country": country,
                "days_ahead": days_ahead,
                "event_count": len(events),
                "events": events[:50]
            }
        except Exception as e:
            logger.exception("[EconomicCalendar] TradingEconomics call failed")
            return {"error": str(e)}

    from datetime import datetime, timedelta
    return {
        "note": "Set TRADING_ECONOMICS_API_KEY for live data",
        "country": country,
        "common_us_releases": [
            {"event": "Non-Farm Payrolls", "frequency": "Monthly (first Friday)"},
            {"event": "CPI (Inflation)", "frequency": "Monthly (mid-month)"},
            {"event": "FOMC Rate Decision", "frequency": "8 times/year"},
            {"event": "GDP Advance Estimate", "frequency": "Quarterly"},
            {"event": "Retail Sales", "frequency": "Monthly"},
            {"event": "ISM Manufacturing PMI", "frequency": "Monthly (first business day)"},
            {"event": "Initial Jobless Claims", "frequency": "Weekly (Thursdays)"},
            {"event": "PPI (Producer Prices)", "frequency": "Monthly"},
            {"event": "Core PCE Price Index", "frequency": "Monthly"},
            {"event": "Consumer Confidence", "frequency": "Monthly (last Tuesday)"},
        ]
    }


def generate_economic_report(
    topics: List[str],
    country: str = "US",
    include_fred: bool = True
) -> Dict[str, Any]:
    """
    Generate a structured economic analysis report combining multiple indicators.

    Fetches data for requested topics and returns a comprehensive summary.

    Topics supported: gdp, unemployment, inflation, interest_rates, trade, population

    Args:
        topics: List of economic topics to include
        country: Country code (default: US)
        include_fred: Include FRED data where applicable (requires FRED_API_KEY)

    Returns:
        Dictionary with structured economic report
    """
    TOPIC_CONFIG = {
        "gdp": {
            "worldbank": "NY.GDP.MKTP.CD",
            "fred": "GDP",
            "label": "GDP"
        },
        "unemployment": {
            "worldbank": "SL.UEM.TOTL.ZS",
            "fred": "UNRATE",
            "label": "Unemployment Rate"
        },
        "inflation": {
            "worldbank": "FP.CPI.TOTL.ZG",
            "fred": "CPIAUCSL",
            "label": "Inflation (CPI)"
        },
        "interest_rates": {
            "fred": "FEDFUNDS",
            "label": "Federal Funds Rate"
        },
        "trade": {
            "worldbank": "NE.EXP.GNFS.ZS",
            "label": "Trade (Exports % GDP)"
        },
        "population": {
            "worldbank": "SP.POP.TOTL",
            "label": "Total Population"
        }
    }

    report: Dict[str, Any] = {
        "country": country,
        "topics_requested": topics,
        "sections": {}
    }

    for topic in topics:
        config = TOPIC_CONFIG.get(topic.lower())
        if not config:
            report["sections"][topic] = {"error": f"Unknown topic: {topic}"}
            continue

        section: Dict[str, Any] = {"label": config["label"], "data_sources": {}}

        if "worldbank" in config:
            wb_data = get_world_bank_indicator(config["worldbank"], country)
            section["data_sources"]["world_bank"] = wb_data

        if include_fred and "fred" in config and country.upper() == "US":
            fred_data = get_fred_series(config["fred"], limit=20)
            section["data_sources"]["fred"] = fred_data

        report["sections"][topic] = section

    return report


# Tool Definitions
GET_FRED_SERIES_TOOL = Tool(
    name="get_fred_series",
    description=(
        "Fetch economic time series data from FRED (Federal Reserve Economic Data). "
        "Common IDs: GDP, UNRATE (unemployment), CPIAUCSL (inflation), FEDFUNDS (interest rate), "
        "DGS10 (10Y Treasury), M2SL (money supply), PAYEMS (nonfarm payrolls). "
        "Requires FRED_API_KEY environment variable."
    ),
    parameters={
        "type": "object",
        "properties": {
            "series_id": {"type": "string", "description": "FRED series identifier (e.g. 'GDP', 'UNRATE')"},
            "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            "limit": {"type": "integer", "description": "Max data points (default: 100)"}
        },
        "required": ["series_id"]
    },
    func=get_fred_series,
    timeout_seconds=60,
    max_output_chars=30000,
    is_readonly=True,
)

GET_WORLD_BANK_INDICATOR_TOOL = Tool(
    name="get_world_bank_indicator",
    description=(
        "Fetch World Bank development indicators for any country. No API key required. "
        "Common indicators: NY.GDP.MKTP.CD (GDP), SP.POP.TOTL (population), "
        "FP.CPI.TOTL.ZG (inflation %), SL.UEM.TOTL.ZS (unemployment %), "
        "NE.EXP.GNFS.ZS (exports % GDP). Country codes: US, CN, DE, JP, GB, IN, BR."
    ),
    parameters={
        "type": "object",
        "properties": {
            "indicator": {"type": "string", "description": "World Bank indicator code"},
            "country": {"type": "string", "description": "ISO 2-letter country code (default: US)"},
            "start_year": {"type": "integer", "description": "Start year"},
            "end_year": {"type": "integer", "description": "End year"}
        },
        "required": ["indicator"]
    },
    func=get_world_bank_indicator,
    timeout_seconds=60,
    max_output_chars=30000,
    is_readonly=True,
)

GET_EXCHANGE_RATES_TOOL = Tool(
    name="get_exchange_rates",
    description=(
        "Get current currency exchange rates. No API key required. "
        "Supports all major currencies. Specify base currency and optionally filter targets."
    ),
    parameters={
        "type": "object",
        "properties": {
            "base_currency": {"type": "string", "description": "Base currency code (default: USD)"},
            "target_currencies": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Target currency codes to filter (e.g. ['EUR','CNY','JPY']). Default: all."
            }
        },
        "required": []
    },
    func=get_exchange_rates,
    timeout_seconds=30,
    max_output_chars=10000,
    is_readonly=True,
)

GET_ECONOMIC_CALENDAR_TOOL = Tool(
    name="get_economic_calendar",
    description=(
        "Get upcoming economic event calendar. With TRADING_ECONOMICS_API_KEY returns live events. "
        "Without key returns a structured list of common US economic releases."
    ),
    parameters={
        "type": "object",
        "properties": {
            "country": {"type": "string", "description": "Country name or code (default: US)"},
            "days_ahead": {"type": "integer", "description": "Days ahead to look (default: 7)"}
        },
        "required": []
    },
    func=get_economic_calendar,
    timeout_seconds=30,
    max_output_chars=30000,
    is_readonly=True,
)

GENERATE_ECONOMIC_REPORT_TOOL = Tool(
    name="generate_economic_report",
    description=(
        "Generate a structured economic analysis report combining multiple indicators. "
        "Topics: gdp, unemployment, inflation, interest_rates, trade, population. "
        "Combines World Bank and FRED data sources."
    ),
    parameters={
        "type": "object",
        "properties": {
            "topics": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Topics: gdp, unemployment, inflation, interest_rates, trade, population"
            },
            "country": {"type": "string", "description": "Country code (default: US)"},
            "include_fred": {"type": "boolean", "description": "Include FRED data (US only, needs FRED_API_KEY)"}
        },
        "required": ["topics"]
    },
    func=generate_economic_report,
    timeout_seconds=120,
    max_output_chars=50000,
    is_readonly=True,
)

ECONOMICS_TOOLS = [
    GET_FRED_SERIES_TOOL,
    GET_WORLD_BANK_INDICATOR_TOOL,
    GET_EXCHANGE_RATES_TOOL,
    GET_ECONOMIC_CALENDAR_TOOL,
    GENERATE_ECONOMIC_REPORT_TOOL,
]
