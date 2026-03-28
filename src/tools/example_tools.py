"""
Example Tools Module - Basic tool implementations.

Provides simple demonstration tools:
- Calculator (safe eval)
- Knowledge base search
"""

import json
import logging
from typing import Dict, Any, List, Optional

from ..core.tool import Tool

logger = logging.getLogger(__name__)


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
    func=calculate,
    timeout_seconds=10,
    max_output_chars=5000,
    is_readonly=True,
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
    func=search_mock_database,
    timeout_seconds=10,
    max_output_chars=10000,
    is_readonly=True,
)
