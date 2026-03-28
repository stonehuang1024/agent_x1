"""
Tool Registry - Centralized catalog of all available tools with category metadata.

Enables LLMs to discover, search, and understand the full tool ecosystem.
"""

from typing import Dict, List, Any, Optional
from ..core.tool import Tool


TOOL_CATEGORIES: Dict[str, Dict[str, Any]] = {
    "search": {
        "label": "Web Search",
        "description": "Search the web using Google (SerpAPI) or Exa AI neural search",
        "tools": []
    },
    "stock": {
        "label": "Stock & Finance",
        "description": "Retrieve stock prices, OHLCV kline data, financial statements, company info",
        "tools": []
    },
    "economics": {
        "label": "Economics & Macro",
        "description": "Fetch macroeconomic indicators from FRED, World Bank; exchange rates; economic calendar",
        "tools": []
    },
    "file": {
        "label": "File Operations",
        "description": "Read, write, append, list, search, move, copy, delete files and directories",
        "tools": []
    },
    "bash": {
        "label": "Shell & System",
        "description": "Execute shell commands, Python scripts, bash scripts; get system info",
        "tools": []
    },
    "pdf": {
        "label": "PDF Processing",
        "description": "Read, create, merge, split PDF files; extract text and images",
        "tools": []
    },
    "ppt": {
        "label": "PowerPoint / Presentations",
        "description": "Create and read PowerPoint (.pptx) presentations; add slides; export to PDF",
        "tools": []
    },
    "web": {
        "label": "Web & HTTP",
        "description": "Fetch URLs, scrape web pages, extract links, download files, parse RSS feeds",
        "tools": []
    },
    "data": {
        "label": "Data Processing",
        "description": "Read CSV/JSON/Excel files, statistical analysis, filtering, format conversion",
        "tools": []
    },
    "reader": {
        "label": "Document Reader",
        "description": "Convert URLs, PDFs, HTML, and documents to clean markdown format",
        "tools": []
    },
    "arxiv": {
        "label": "arXiv Research",
        "description": "Search and download academic papers from arXiv repository",
        "tools": []
    },
    "utility": {
        "label": "Utility",
        "description": "Calculator, time, weather (mock), knowledge base search",
        "tools": []
    },
    "codebase": {
        "label": "Codebase Search",
        "description": "Search code with regex (grep), find files by pattern (glob), list directories (ls)",
        "tools": []
    },
}


class CategorizedToolRegistry:
    """
    Extended tool registry with category metadata for LLM tool discovery.

    Allows searching tools by name, category, or keyword, making it easy
    for LLMs to select the most appropriate tool.
    """

    def __init__(self):
        self._tools: Dict[str, Tool] = {}
        self._tool_categories: Dict[str, str] = {}

    def register(self, tool: Tool, category: str) -> None:
        """
        Register a tool under a category.

        Args:
            tool: Tool instance
            category: Category key from TOOL_CATEGORIES

        Raises:
            ValueError: If tool already registered or category unknown
        """
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already registered")
        if category not in TOOL_CATEGORIES:
            raise ValueError(f"Unknown category '{category}'. Valid: {list(TOOL_CATEGORIES.keys())}")
        self._tools[tool.name] = tool
        self._tool_categories[tool.name] = category

    def register_many(self, tools: List[Tool], category: str) -> None:
        """Register a list of tools under the same category."""
        for tool in tools:
            self.register(tool, category)

    def get(self, name: str) -> Optional[Tool]:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_category(self, name: str) -> Optional[str]:
        """Get the category of a tool by name."""
        return self._tool_categories.get(name)

    def list_tools(self) -> List[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())

    def list_by_category(self, category: str) -> List[Tool]:
        """Return all tools in a category."""
        return [
            t for name, t in self._tools.items()
            if self._tool_categories.get(name) == category
        ]

    def search(self, keyword: str) -> List[Dict[str, Any]]:
        """
        Search tools by keyword (matches name or description).

        Args:
            keyword: Search keyword (case-insensitive)

        Returns:
            List of matching tool summaries
        """
        kw = keyword.lower()
        results = []
        for name, tool in self._tools.items():
            if kw in name.lower() or kw in tool.description.lower():
                results.append({
                    "name": tool.name,
                    "category": self._tool_categories.get(name, ""),
                    "description": tool.description
                })
        return results

    def get_all_schemas(self) -> List[Dict[str, Any]]:
        """Return all tool schemas for LLM registration."""
        return [t.get_schema() for t in self._tools.values()]

    def get_catalog(self) -> Dict[str, Any]:
        """
        Return the full tool catalog organized by category.

        Useful for giving LLMs an overview of all available capabilities.
        """
        catalog: Dict[str, Any] = {}
        for cat_key, cat_meta in TOOL_CATEGORIES.items():
            tools_in_cat = self.list_by_category(cat_key)
            if not tools_in_cat:
                continue
            catalog[cat_key] = {
                "label": cat_meta["label"],
                "description": cat_meta["description"],
                "tool_count": len(tools_in_cat),
                "tools": [
                    {"name": t.name, "description": t.description}
                    for t in tools_in_cat
                ]
            }
        return catalog

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
