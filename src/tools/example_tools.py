"""
Example Tools Module - Basic tool implementations.

Provides simple demonstration tools:
- Weather query (mock data)
- Calculator (safe eval)
- Current time
- Knowledge base search
"""

import json
import random
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

from ..core.tool import Tool

logger = logging.getLogger(__name__)


def get_weather(location: str, unit: str = "celsius") -> Dict[str, Any]:
    """
    Get current weather for a location (mock data).
    
    Args:
        location: City name
        unit: Temperature unit (celsius/fahrenheit)
        
    Returns:
        Weather data dictionary
    """
    mock_data: Dict[str, Dict[str, Any]] = {
        "beijing": {"temp_c": 22, "condition": "晴", "humidity": 45, "wind": 12},
        "shanghai": {"temp_c": 25, "condition": "多云", "humidity": 60, "wind": 8},
        "shenzhen": {"temp_c": 28, "condition": "阴", "humidity": 70, "wind": 15},
        "guangzhou": {"temp_c": 30, "condition": "晴", "humidity": 65, "wind": 10},
        "new york": {"temp_c": 18, "condition": "Sunny", "humidity": 50, "wind": 20},
        "london": {"temp_c": 15, "condition": "Rainy", "humidity": 85, "wind": 18},
        "tokyo": {"temp_c": 23, "condition": "Cloudy", "humidity": 55, "wind": 14},
    }
    
    location_key = location.lower().strip()
    
    if location_key in mock_data:
        data = mock_data[location_key]
    else:
        data = {
            "temp_c": random.randint(15, 35),
            "condition": random.choice(["晴", "多云", "阴", "小雨"]),
            "humidity": random.randint(40, 90),
            "wind": random.randint(5, 25)
        }
    
    temp = data["temp_c"]
    if unit.lower() == "fahrenheit":
        temp = round(temp * 9 / 5 + 32)
        unit_label = "fahrenheit"
    else:
        unit_label = "celsius"
    
    return {
        "location": location,
        "temperature": temp,
        "unit": unit_label,
        "condition": data["condition"],
        "humidity": f"{data['humidity']}%",
        "wind_speed": data["wind"]
    }


def calculate(expression: str) -> Dict[str, Any]:
    """
    Safely evaluate mathematical expression.
    
    Args:
        expression: Math expression as string
        
    Returns:
        Result dictionary
    """
    try:
        allowed_chars = set("0123456789+-*/()., %")
        
        for char in expression:
            if char not in allowed_chars and not char.isalnum() and not char.isspace():
                return {
                    "expression": expression,
                    "error": f"Invalid character: '{char}'"
                }
        
        safe_dict = {
            "abs": abs, "round": round, "max": max, "min": min,
            "sum": sum, "pow": pow,
            "pi": 3.141592653589793, "e": 2.718281828459045
        }
        
        result = eval(expression, {"__builtins__": {}}, safe_dict)
        
        return {"expression": expression, "result": result}
        
    except ZeroDivisionError:
        return {"expression": expression, "error": "Division by zero"}
    except SyntaxError as e:
        return {"expression": expression, "error": f"Invalid syntax: {e}"}
    except Exception as e:
        return {"expression": expression, "error": f"Calculation error: {e}"}


def get_current_time(timezone: str = "UTC") -> Dict[str, Any]:
    """
    Get current date and time.
    
    Args:
        timezone: Timezone identifier
        
    Returns:
        Time information dictionary
    """
    now = datetime.now()
    
    timezone_offsets = {
        "utc": 0, "asia/shanghai": 8, "asia/tokyo": 9,
        "europe/london": 0, "america/new_york": -5,
        "america/los_angeles": -8
    }
    
    tz_key = timezone.lower()
    offset_hours = timezone_offsets.get(tz_key, 0)
    offset_str = f"UTC{offset_hours:+d}" if offset_hours != 0 else "UTC"
    
    return {
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "timezone": timezone,
        "timezone_offset": offset_str,
        "weekday": now.strftime("%A"),
        "timestamp": int(now.timestamp())
    }


def search_mock_database(query: str, category: Optional[str] = None) -> Dict[str, Any]:
    """
    Search mock knowledge base.
    
    Args:
        query: Search query
        category: Category filter
        
    Returns:
        Search results
    """
    mock_db: Dict[str, List[Dict[str, str]]] = {
        "tech": [
            {"title": "Python", "content": "Python is a programming language..."},
            {"title": "AI", "content": "AI development uses Python..."},
        ],
        "science": [
            {"title": "Physics", "content": "Physics studies matter and energy..."},
        ],
        "general": [
            {"title": "Health", "content": "Health is important..."},
        ]
    }
    
    categories_to_search = [category.lower()] if category and category.lower() in mock_db else list(mock_db.keys())
    
    query_lower = query.lower()
    results = []
    
    for cat in categories_to_search:
        for doc in mock_db.get(cat, []):
            if query_lower in doc["title"].lower() or query_lower in doc["content"].lower():
                results.append({"category": cat, **doc})
    
    return {
        "query": query,
        "category": category,
        "count": len(results),
        "results": results[:5]
    }


# Tool Definitions
WEATHER_TOOL = Tool(
    name="get_weather",
    description="Get current weather for a city. Returns temperature, condition, humidity, wind.",
    parameters={
        "type": "object",
        "properties": {
            "location": {"type": "string", "description": "City name"},
            "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}
        },
        "required": ["location"]
    },
    func=get_weather
)

CALCULATOR_TOOL = Tool(
    name="calculate",
    description="Evaluate mathematical expressions. Supports +, -, *, /, **, abs, round, max, min, pi, e",
    parameters={
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "Math expression to evaluate"}
        },
        "required": ["expression"]
    },
    func=calculate
)

TIME_TOOL = Tool(
    name="get_current_time",
    description="Get current date and time. Supports timezones like UTC, Asia/Shanghai, America/New_York",
    parameters={
        "type": "object",
        "properties": {
            "timezone": {"type": "string", "description": "Timezone identifier", "default": "UTC"}
        },
        "required": []
    },
    func=get_current_time
)

SEARCH_TOOL = Tool(
    name="search_knowledge",
    description="Search knowledge base. Categories: tech, science, general",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "category": {"type": "string", "enum": ["tech", "science", "general"]}
        },
        "required": ["query"]
    },
    func=search_mock_database
)
