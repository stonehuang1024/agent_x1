"""Tools Module - LLM-callable tool implementations.

Categories:
- utility:    Weather (mock), calculator, time, knowledge search
- search:     Google (SerpAPI), Exa AI neural search
- stock:      Stock kline, snapshot, financials, company info
- economics:  FRED series, World Bank indicators, exchange rates, economic calendar
- file:       Read, write, append, list, search, move, copy, delete, metadata
- bash:       Shell commands, Python scripts, bash scripts, system info
- pdf:        Read, metadata, merge, split, create, extract images
- ppt:        Create, read, add slide, export to PDF
- web:        Fetch URL, scrape text, extract links, download, check, RSS
- data:       CSV/JSON/Excel read, stats analysis, filter, save, convert
- reader:     URL/PDF/HTML to markdown conversion
- arxiv:      Search and download academic papers from arXiv
- codebase:   Regex search (grep), file pattern search (glob), directory listing (ls)

Usage:
    from src.tools import ALL_TOOLS, TOOL_REGISTRY
    from src.tools.file_tools import READ_FILE_TOOL

    # Register all tools with engine
    for tool in ALL_TOOLS:
        engine.register_tool(tool)

    # Search tools by keyword
    matches = TOOL_REGISTRY.search("pdf")

    # Get full catalog (for LLM context)
    catalog = TOOL_REGISTRY.get_catalog()
"""

from ..core.tool import Tool
from .tool_registry import CategorizedToolRegistry

# --- Utility tools ---
from .example_tools import (
    CALCULATOR_TOOL,
    SEARCH_TOOL,
)

# --- Search tools ---
from .search_tools import (
    GOOGLE_SEARCH_TOOL,
    EXA_SEARCH_TOOL,
)

# --- Stock tools ---
from .stock_tools import (
    GET_STOCK_KLINE_TOOL,
    GET_STOCK_SNAPSHOT_TOOL,
    GET_STOCK_FINANCIALS_TOOL,
    GET_STOCK_INFO_TOOL,
)

# --- Stock analysis tools ---
from .stock_analysis import (
    ANALYZE_STOCK_TOOL,
)

# --- Economics tools ---
from .economics_tools import (
    GET_FRED_SERIES_TOOL,
    GET_WORLD_BANK_INDICATOR_TOOL,
    GET_EXCHANGE_RATES_TOOL,
    GET_ECONOMIC_CALENDAR_TOOL,
    GENERATE_ECONOMIC_REPORT_TOOL,
    ECONOMICS_TOOLS,
)

# --- File tools ---
from .file_tools import (
    READ_FILE_TOOL,
    WRITE_FILE_TOOL,
    APPEND_FILE_TOOL,
    EDIT_FILE_TOOL,
    LIST_DIRECTORY_TOOL,
    SEARCH_IN_FILES_TOOL,
    MOVE_FILE_TOOL,
    COPY_FILE_TOOL,
    DELETE_FILE_TOOL,
    GET_FILE_INFO_TOOL,
    CREATE_DIRECTORY_TOOL,
    FILE_TOOLS,
)

# --- Bash / Shell tools ---
from .bash_tools import (
    RUN_COMMAND_TOOL,
    RUN_PYTHON_SCRIPT_TOOL,
    RUN_BASH_SCRIPT_TOOL,
    GET_SYSTEM_INFO_TOOL,
    GET_ENV_VAR_TOOL,
    BASH_TOOLS,
)

# --- PDF tools ---
from .pdf_tools import (
    READ_PDF_TOOL,
    GET_PDF_METADATA_TOOL,
    MERGE_PDFS_TOOL,
    SPLIT_PDF_TOOL,
    CREATE_PDF_FROM_TEXT_TOOL,
    EXTRACT_PDF_IMAGES_TOOL,
    PDF_TOOLS,
)

# --- PowerPoint tools ---
from .ppt_tools import (
    CREATE_PRESENTATION_TOOL,
    READ_PRESENTATION_TOOL,
    ADD_SLIDE_TOOL,
    EXPORT_PPT_TO_PDF_TOOL,
    PPT_TOOLS,
)

# --- Web tools ---
from .web_tools import (
    FETCH_URL_TOOL,
    EXTRACT_WEBPAGE_TEXT_TOOL,
    EXTRACT_LINKS_TOOL,
    DOWNLOAD_FILE_TOOL,
    CHECK_URL_TOOL,
    FETCH_RSS_FEED_TOOL,
    WEB_TOOLS,
)

# --- Data tools ---
from .data_tools import (
    READ_CSV_TOOL,
    READ_JSON_FILE_TOOL,
    READ_EXCEL_TOOL,
    ANALYZE_DATAFRAME_TOOL,
    FILTER_CSV_TOOL,
    SAVE_AS_CSV_TOOL,
    CONVERT_DATA_FORMAT_TOOL,
    DATA_TOOLS,
)

# --- Reader tools ---
from .reader_tools import (
    CONVERT_URL_TO_MARKDOWN_TOOL,
    CONVERT_PDF_TO_MARKDOWN_TOOL,
    CONVERT_HTML_TO_MARKDOWN_TOOL,
    CONVERT_FILE_TO_MARKDOWN_TOOL,
    READER_TOOLS,
)

# --- arXiv tools ---
from .arxiv_tools import (
    SEARCH_ARXIV_TOOL,
    GET_ARXIV_PAPER_DETAILS_TOOL,
    DOWNLOAD_ARXIV_PDF_TOOL,
    BATCH_DOWNLOAD_ARXIV_PDFS_TOOL,
    ARXIV_TOOLS,
)

# --- Codebase search tools ---
from .codebase_search_tools import (
    GREP_SEARCH_TOOL,
    GLOB_SEARCH_TOOL,
    LS_DIRECTORY_TOOL,
    CODEBASE_TOOLS,
)

# --- Context management tools ---
from .context_tools import (
    RECALL_COMPRESSED_MESSAGES_TOOL,
    CONTEXT_TOOLS,
    set_archive_instance,
)

# ----------------------------------------------------------------
# Build the categorized registry
# ----------------------------------------------------------------
TOOL_REGISTRY = CategorizedToolRegistry()

TOOL_REGISTRY.register_many([CALCULATOR_TOOL, SEARCH_TOOL], "utility")
TOOL_REGISTRY.register_many([GOOGLE_SEARCH_TOOL, EXA_SEARCH_TOOL], "search")
TOOL_REGISTRY.register_many(
    [GET_STOCK_KLINE_TOOL, GET_STOCK_SNAPSHOT_TOOL, GET_STOCK_FINANCIALS_TOOL, GET_STOCK_INFO_TOOL, ANALYZE_STOCK_TOOL],
    "stock"
)
TOOL_REGISTRY.register_many(ECONOMICS_TOOLS, "economics")
TOOL_REGISTRY.register_many(FILE_TOOLS, "file")
TOOL_REGISTRY.register_many(BASH_TOOLS, "bash")
TOOL_REGISTRY.register_many(PDF_TOOLS, "pdf")
TOOL_REGISTRY.register_many(PPT_TOOLS, "ppt")
TOOL_REGISTRY.register_many(WEB_TOOLS, "web")
TOOL_REGISTRY.register_many(DATA_TOOLS, "data")
TOOL_REGISTRY.register_many(READER_TOOLS, "reader")
TOOL_REGISTRY.register_many(ARXIV_TOOLS, "arxiv")
TOOL_REGISTRY.register_many(CODEBASE_TOOLS, "codebase")
TOOL_REGISTRY.register_many(CONTEXT_TOOLS, "context")

# Flat list of every registered tool (preserves category insertion order)
ALL_TOOLS = list(TOOL_REGISTRY._tools.values())

# Tool name -> category mapping (used by skill framework for tool filtering)
TOOL_CATEGORIES_MAP = dict(TOOL_REGISTRY._tool_categories)

__all__ = [
    # Base
    "Tool",
    "CategorizedToolRegistry",
    "TOOL_REGISTRY",
    # Combined
    "ALL_TOOLS",
    # Category lists
    "ECONOMICS_TOOLS",
    "FILE_TOOLS",
    "BASH_TOOLS",
    "PDF_TOOLS",
    "PPT_TOOLS",
    "WEB_TOOLS",
    "DATA_TOOLS",
    "READER_TOOLS",
    "ARXIV_TOOLS",
    # Utility
    "CALCULATOR_TOOL",
    "SEARCH_TOOL",
    # Search
    "GOOGLE_SEARCH_TOOL",
    "EXA_SEARCH_TOOL",
    # Stock
    "GET_STOCK_KLINE_TOOL",
    "GET_STOCK_SNAPSHOT_TOOL",
    "GET_STOCK_FINANCIALS_TOOL",
    "GET_STOCK_INFO_TOOL",
    "ANALYZE_STOCK_TOOL",
    # Economics
    "GET_FRED_SERIES_TOOL",
    "GET_WORLD_BANK_INDICATOR_TOOL",
    "GET_EXCHANGE_RATES_TOOL",
    "GET_ECONOMIC_CALENDAR_TOOL",
    "GENERATE_ECONOMIC_REPORT_TOOL",
    # File
    "READ_FILE_TOOL",
    "WRITE_FILE_TOOL",
    "APPEND_FILE_TOOL",
    "LIST_DIRECTORY_TOOL",
    "SEARCH_IN_FILES_TOOL",
    "MOVE_FILE_TOOL",
    "COPY_FILE_TOOL",
    "DELETE_FILE_TOOL",
    "GET_FILE_INFO_TOOL",
    "EDIT_FILE_TOOL",
    "CREATE_DIRECTORY_TOOL",
    # Bash
    "RUN_COMMAND_TOOL",
    "RUN_PYTHON_SCRIPT_TOOL",
    "RUN_BASH_SCRIPT_TOOL",
    "GET_SYSTEM_INFO_TOOL",
    "GET_ENV_VAR_TOOL",
    # PDF
    "READ_PDF_TOOL",
    "GET_PDF_METADATA_TOOL",
    "MERGE_PDFS_TOOL",
    "SPLIT_PDF_TOOL",
    "CREATE_PDF_FROM_TEXT_TOOL",
    "EXTRACT_PDF_IMAGES_TOOL",
    # PPT
    "CREATE_PRESENTATION_TOOL",
    "READ_PRESENTATION_TOOL",
    "ADD_SLIDE_TOOL",
    "EXPORT_PPT_TO_PDF_TOOL",
    # Web
    "FETCH_URL_TOOL",
    "EXTRACT_WEBPAGE_TEXT_TOOL",
    "EXTRACT_LINKS_TOOL",
    "DOWNLOAD_FILE_TOOL",
    "CHECK_URL_TOOL",
    "FETCH_RSS_FEED_TOOL",
    # Data
    "READ_CSV_TOOL",
    "READ_JSON_FILE_TOOL",
    "READ_EXCEL_TOOL",
    "ANALYZE_DATAFRAME_TOOL",
    "FILTER_CSV_TOOL",
    "SAVE_AS_CSV_TOOL",
    "CONVERT_DATA_FORMAT_TOOL",
    # Reader
    "CONVERT_URL_TO_MARKDOWN_TOOL",
    "CONVERT_PDF_TO_MARKDOWN_TOOL",
    "CONVERT_HTML_TO_MARKDOWN_TOOL",
    "CONVERT_FILE_TO_MARKDOWN_TOOL",
    # arXiv
    "SEARCH_ARXIV_TOOL",
    "GET_ARXIV_PAPER_DETAILS_TOOL",
    "DOWNLOAD_ARXIV_PDF_TOOL",
    "BATCH_DOWNLOAD_ARXIV_PDFS_TOOL",
    # Codebase
    "GREP_SEARCH_TOOL",
    "GLOB_SEARCH_TOOL",
    "LS_DIRECTORY_TOOL",
    "CODEBASE_TOOLS",
    # Context
    "RECALL_COMPRESSED_MESSAGES_TOOL",
    "CONTEXT_TOOLS",
    "set_archive_instance",
]
