"""
Search Tools Module - Web search capabilities.

Provides search tools using:
- SerpAPI (Google Search)
- Exa AI (Neural search)
"""

import os
import logging
from typing import Dict, Any, List, Optional

from ..core.tool import Tool

logger = logging.getLogger(__name__)


def search_google(query: str, location: str = "United States", num_results: int = 5) -> Dict[str, Any]:
    """
    Search Google using SerpAPI.
    
    Args:
        query: Search query
        location: Search location
        num_results: Number of results (1-10)
        
    Returns:
        Search results or error
    """
    try:
        from serpapi import GoogleSearch
    except ImportError:
        return {"error": "serpapi package not installed. Run: pip install serpapi"}
    
    api_key = os.getenv("SERPAPI_KEY", "")
    if not api_key:
        return {"error": "SERPAPI_KEY environment variable not set"}
    
    logger.info(f"[GoogleSearch] Query: '{query}', Location: '{location}'")
    
    params = {
        "engine": "google",
        "q": query,
        "location": location,
        "google_domain": "google.com",
        "hl": "en",
        "gl": "us",
        "num": min(max(num_results, 1), 10),
        "api_key": api_key
    }
    
    try:
        search = GoogleSearch(params)
        results = search.get_dict()
        
        if "error" in results:
            logger.error(f"[GoogleSearch] API error: {results['error']}")
            return {"error": results["error"], "query": query}
        
        organic_results = results.get("organic_results", [])
        
        formatted_results = []
        for i, result in enumerate(organic_results[:num_results], 1):
            formatted_results.append({
                "position": i,
                "title": result.get("title", ""),
                "link": result.get("link", ""),
                "snippet": result.get("snippet", ""),
                "source": result.get("source", "")
            })
        
        response = {
            "query": query,
            "total_results": len(organic_results),
            "results": formatted_results
        }
        
        knowledge_graph = results.get("knowledge_graph", {})
        if knowledge_graph:
            response["knowledge_graph"] = {
                "title": knowledge_graph.get("title"),
                "description": knowledge_graph.get("description"),
                "type": knowledge_graph.get("type")
            }
        
        logger.info(f"[GoogleSearch] Found {len(formatted_results)} results")
        return response
        
    except Exception as e:
        logger.exception("[GoogleSearch] Failed")
        return {"error": str(e), "query": query}


def web_search_exa(
    query: str,
    num_results: int = 5,
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
    include_text: bool = False
) -> Dict[str, Any]:
    """
    Search web using Exa AI.
    
    Args:
        query: Search query
        num_results: Number of results
        include_domains: Domains to include
        exclude_domains: Domains to exclude
        include_text: Include full text
        
    Returns:
        Search results
    """
    try:
        from exa_py import Exa
    except ImportError:
        return {"error": "exa-py not installed. Run: pip install exa-py"}
    
    api_key = os.getenv("EXA_API_KEY", "")
    if not api_key:
        return {"error": "EXA_API_KEY environment variable not set"}
    
    logger.info(f"[ExaSearch] Query: '{query}'")
    
    try:
        exa = Exa(api_key)
        
        options = {"num_results": min(max(num_results, 1), 10)}
        
        if include_domains:
            options["include_domains"] = include_domains
        if exclude_domains:
            options["exclude_domains"] = exclude_domains
        if include_text:
            options["text"] = {"max_characters": 1000}
        
        response = exa.search_and_contents(query, **options)
        
        results = []
        for result in response.results:
            result_dict = {
                "title": getattr(result, 'title', ''),
                "url": getattr(result, 'url', ''),
                "published_date": getattr(result, 'published_date', None),
                "author": getattr(result, 'author', None),
                "score": getattr(result, 'score', None),
            }
            
            if include_text and hasattr(result, 'text'):
                result_dict["text"] = result.text[:1000] if result.text else None
            
            results.append(result_dict)
        
        return {
            "query": query,
            "total_results": len(results),
            "results": results
        }
        
    except Exception as e:
        logger.exception("[ExaSearch] Failed")
        return {"error": str(e), "query": query}


# Tool Definitions
GOOGLE_SEARCH_TOOL = Tool(
    name="search_google",
    description="Search Google using SerpAPI. Returns results with titles, URLs, snippets.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "location": {"type": "string", "description": "Location for results", "default": "United States"},
            "num_results": {"type": "integer", "description": "Results to return (1-10)", "default": 5}
        },
        "required": ["query"]
    },
    func=search_google
)

EXA_SEARCH_TOOL = Tool(
    name="web_search_exa",
    description="Search web using Exa AI neural search. Returns high-quality results.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "num_results": {"type": "integer", "description": "Results (1-10)", "default": 5},
            "include_domains": {"type": "array", "items": {"type": "string"}, "description": "Domains to include"},
            "exclude_domains": {"type": "array", "items": {"type": "string"}, "description": "Domains to exclude"},
            "include_text": {"type": "boolean", "description": "Include full text", "default": False}
        },
        "required": ["query"]
    },
    func=web_search_exa
)
