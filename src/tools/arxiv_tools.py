"""
arXiv Tools - Search and download academic papers from arXiv.

API Reference: https://info.arxiv.org/help/api/basics.html
"""

import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable
from functools import wraps
from urllib.error import HTTPError, URLError

from ..core.tool import Tool
from ..util.logger import get_logger

logger = get_logger(__name__)

# arXiv API endpoint
ARXIV_API_URL = "http://export.arxiv.org/api/query"
ARXIV_PDF_URL = "https://arxiv.org/pdf"

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY_BASE = 2  # seconds
API_TIMEOUT = 30
PDF_TIMEOUT = 120  # Increased for large PDFs

# XML namespaces for Atom feed
NAMESPACES = {
    'atom': 'http://www.w3.org/2005/Atom',
    'arxiv': 'http://arxiv.org/schemas/atom'
}


def retry_with_backoff(max_retries: int = MAX_RETRIES, base_delay: float = RETRY_DELAY_BASE,
                       exceptions: tuple = (URLError, HTTPError, TimeoutError)):
    """
    Decorator to retry a function with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Base delay in seconds (will be multiplied by 2^attempt)
        exceptions: Tuple of exceptions to catch and retry
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    # Don't retry on 404 errors
                    if isinstance(e, HTTPError) and e.code == 404:
                        logger.warning(f"[ArXivTool] {func.__name__}: 404 error, not retrying")
                        raise
                    
                    if attempt < max_retries:
                        delay = base_delay * (2 ** attempt)  # Exponential backoff
                        logger.warning(
                            f"[ArXivTool] {func.__name__}: Attempt {attempt + 1}/{max_retries + 1} failed: {e}. "
                            f"Retrying in {delay}s..."
                        )
                        time.sleep(delay)
                    else:
                        logger.error(f"[ArXivTool] {func.__name__}: All {max_retries + 1} attempts failed")
                        
            raise last_exception
        return wrapper
    return decorator


def _make_api_request(
    search_query: str,
    start: int = 0,
    max_results: int = 10,
    sort_by: str = "relevance",
    sort_order: str = "descending",
    id_list: Optional[List[str]] = None,
    retry_attempt: int = 0
) -> Optional[str]:
    """
    Make a request to the arXiv API with retry support.
    
    Args:
        search_query: Search query string (e.g., "all:electron")
        start: Start index for results
        max_results: Maximum number of results (max 30000)
        sort_by: Sort field (relevance, lastUpdatedDate, submittedDate)
        sort_order: Sort order (ascending, descending)
        id_list: Optional list of arXiv IDs to fetch directly
        retry_attempt: Current retry attempt (internal use)
    
    Returns:
        XML response string or None on error
    """
    params = {
        'start': start,
        'max_results': max(min(max_results, 30000), 1),
        'sortBy': sort_by,
        'sortOrder': sort_order
    }
    
    if search_query:
        params['search_query'] = search_query
    
    if id_list:
        params['id_list'] = ','.join(id_list)
    
    query_string = urllib.parse.urlencode(params)
    url = f"{ARXIV_API_URL}?{query_string}"
    
    try:
        logger.info(f"[ArXivAPI] Requesting: {url}")
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'arXivTool/1.0 (research assistant)',
                'Accept': 'application/atom+xml'
            }
        )
        
        with urllib.request.urlopen(req, timeout=API_TIMEOUT) as response:
            data = response.read().decode('utf-8')
            logger.info(f"[ArXivAPI] Response: {len(data)} bytes")
            return data
            
    except HTTPError as e:
        logger.error(f"[ArXivAPI] HTTP Error {e.code}: {e.reason}")
        # Don't retry on 404
        if e.code == 404:
            return None
        # Retry on 5xx errors or rate limiting (429)
        if e.code >= 500 or e.code == 429:
            if retry_attempt < MAX_RETRIES:
                delay = RETRY_DELAY_BASE * (2 ** retry_attempt)
                logger.warning(f"[ArXivAPI] Retrying in {delay}s (attempt {retry_attempt + 1}/{MAX_RETRIES})")
                time.sleep(delay)
                return _make_api_request(
                    search_query, start, max_results, sort_by, sort_order, id_list,
                    retry_attempt=retry_attempt + 1
                )
        return None
        
    except URLError as e:
        logger.error(f"[ArXivAPI] URL Error: {e.reason}")
        if retry_attempt < MAX_RETRIES:
            delay = RETRY_DELAY_BASE * (2 ** retry_attempt)
            logger.warning(f"[ArXivAPI] Retrying in {delay}s (attempt {retry_attempt + 1}/{MAX_RETRIES})")
            time.sleep(delay)
            return _make_api_request(
                search_query, start, max_results, sort_by, sort_order, id_list,
                retry_attempt=retry_attempt + 1
            )
        return None
        
    except TimeoutError as e:
        logger.error(f"[ArXivAPI] Timeout Error: {e}")
        if retry_attempt < MAX_RETRIES:
            delay = RETRY_DELAY_BASE * (2 ** retry_attempt)
            logger.warning(f"[ArXivAPI] Retrying in {delay}s (attempt {retry_attempt + 1}/{MAX_RETRIES})")
            time.sleep(delay)
            return _make_api_request(
                search_query, start, max_results, sort_by, sort_order, id_list,
                retry_attempt=retry_attempt + 1
            )
        return None
        
    except Exception as e:
        logger.error(f"[ArXivAPI] Error: {e}")
        return None


def _parse_atom_entry(entry_elem: ET.Element) -> Dict[str, Any]:
    """Parse a single Atom entry into a paper dict."""
    paper = {}
    
    # Get arXiv ID from link or id element
    id_elem = entry_elem.find('atom:id', NAMESPACES)
    if id_elem is not None and id_elem.text:
        # Extract ID from URL like http://arxiv.org/abs/2401.12345
        match = re.search(r'arxiv\.org/abs/(\d+\.\d+)', id_elem.text)
        if match:
            paper['id'] = match.group(1)
        else:
            paper['id'] = id_elem.text.split('/')[-1]
    
    # Title
    title_elem = entry_elem.find('atom:title', NAMESPACES)
    paper['title'] = title_elem.text.strip() if title_elem is not None and title_elem.text else "Unknown"
    
    # Authors
    authors = []
    for author in entry_elem.findall('atom:author', NAMESPACES):
        name_elem = author.find('atom:name', NAMESPACES)
        if name_elem is not None and name_elem.text:
            authors.append(name_elem.text)
    paper['authors'] = authors
    
    # Summary/Abstract
    summary_elem = entry_elem.find('atom:summary', NAMESPACES)
    paper['summary'] = summary_elem.text.strip() if summary_elem is not None and summary_elem.text else ""
    
    # Categories
    categories = []
    for cat in entry_elem.findall('arxiv:primary_category', NAMESPACES):
        if cat.get('term'):
            categories.append(cat.get('term'))
    for cat in entry_elem.findall('atom:category', NAMESPACES):
        term = cat.get('term')
        if term and term not in categories:
            categories.append(term)
    paper['categories'] = categories
    
    # Published date
    published_elem = entry_elem.find('atom:published', NAMESPACES)
    paper['published'] = published_elem.text if published_elem is not None else ""
    
    # Updated date
    updated_elem = entry_elem.find('atom:updated', NAMESPACES)
    paper['updated'] = updated_elem.text if updated_elem is not None else ""
    
    # Links (PDF, etc.)
    pdf_url = None
    abs_url = None
    for link in entry_elem.findall('atom:link', NAMESPACES):
        rel = link.get('rel', '')
        href = link.get('href', '')
        title = link.get('title', '')
        
        if rel == 'alternate':
            abs_url = href
        elif 'pdf' in title.lower() or href.endswith('.pdf'):
            pdf_url = href
    
    paper['abs_url'] = abs_url or f"https://arxiv.org/abs/{paper.get('id', '')}"
    paper['pdf_url'] = pdf_url or f"{ARXIV_PDF_URL}/{paper.get('id', '')}.pdf"
    
    # arxiv specific elements
    comment_elem = entry_elem.find('arxiv:comment', NAMESPACES)
    paper['comment'] = comment_elem.text if comment_elem is not None else ""
    
    journal_elem = entry_elem.find('arxiv:journal_ref', NAMESPACES)
    paper['journal'] = journal_elem.text if journal_elem is not None else ""
    
    doi_elem = entry_elem.find('arxiv:doi', NAMESPACES)
    paper['doi'] = doi_elem.text if doi_elem is not None else ""
    
    return paper


def _parse_atom_response(xml_data: str) -> Dict[str, Any]:
    """Parse Atom XML response into structured data."""
    try:
        root = ET.fromstring(xml_data)
        
        # Get total results from opensearch elements
        total_results = root.find('opensearch:totalResults', {
            **NAMESPACES, 
            'opensearch': 'http://a9.com/-/spec/opensearch/1.1/'
        })
        total = int(total_results.text) if total_results is not None else 0
        
        # Get start index
        start_elem = root.find('opensearch:startIndex', {
            **NAMESPACES,
            'opensearch': 'http://a9.com/-/spec/opensearch/1.1/'
        })
        start = int(start_elem.text) if start_elem is not None else 0
        
        # Get items per page
        items_elem = root.find('opensearch:itemsPerPage', {
            **NAMESPACES,
            'opensearch': 'http://a9.com/-/spec/opensearch/1.1/'
        })
        items_per_page = int(items_elem.text) if items_elem is not None else 0
        
        # Parse entries
        papers = []
        for entry in root.findall('atom:entry', NAMESPACES):
            paper = _parse_atom_entry(entry)
            if paper.get('id'):
                papers.append(paper)
        
        return {
            'total_results': total,
            'start_index': start,
            'items_per_page': items_per_page,
            'papers': papers
        }
    except ET.ParseError as e:
        logger.error(f"[ArXivAPI] XML Parse Error: {e}")
        return {'total_results': 0, 'papers': [], 'error': f'Parse error: {e}'}
    except Exception as e:
        logger.error(f"[ArXivAPI] Parse Error: {e}")
        return {'total_results': 0, 'papers': [], 'error': str(e)}


# ============================================================================
# Tool Functions
# ============================================================================

def search_arxiv_papers(
    query: str,
    search_field: str = "all",
    max_results: int = 10,
    start: int = 0,
    sort_by: str = "relevance",
    sort_order: str = "descending",
    category: str = ""
) -> str:
    """
    Search for papers on arXiv.
    
    Args:
        query: Search query string
        search_field: Field to search (all, ti=title, au=author, abs=abstract, co=comment, jr=journal, cat=category, rn=report_number, id=id)
        max_results: Maximum number of results (1-50)
        start: Start index for pagination
        sort_by: Sort by (relevance, lastUpdatedDate, submittedDate)
        sort_order: Sort order (ascending, descending)
        category: Filter by arXiv category (e.g., cs.AI, physics, math)
    
    Returns:
        JSON string with search results
    """
    try:
        # Build search query
        if search_field and search_field != "all":
            search_query = f"{search_field}:{query}"
        else:
            search_query = f"all:{query}"
        
        # Add category filter if provided
        if category:
            search_query = f"{search_query} AND cat:{category}"
        
        # Make API request
        xml_data = _make_api_request(
            search_query=search_query,
            start=start,
            max_results=min(max_results, 50),
            sort_by=sort_by,
            sort_order=sort_order
        )
        
        if xml_data is None:
            return json.dumps({
                "success": False,
                "error": "Failed to fetch results from arXiv API"
            }, ensure_ascii=False)
        
        # Parse response
        result = _parse_atom_response(xml_data)
        
        if 'error' in result:
            return json.dumps({
                "success": False,
                "error": result['error']
            }, ensure_ascii=False)
        
        # Format papers for output
        papers_summary = []
        for paper in result['papers']:
            papers_summary.append({
                'id': paper['id'],
                'title': paper['title'],
                'authors': paper['authors'][:5],  # First 5 authors
                'author_count': len(paper['authors']),
                'published': paper['published'][:10] if paper['published'] else '',  # YYYY-MM-DD
                'primary_category': paper['categories'][0] if paper['categories'] else '',
                'categories': paper['categories'],
                'abs_url': paper['abs_url'],
                'pdf_url': paper['pdf_url']
            })
        
        return json.dumps({
            "success": True,
            "total_results": result['total_results'],
            "returned": len(papers_summary),
            "start": start,
            "query": query,
            "search_field": search_field,
            "category_filter": category,
            "papers": papers_summary
        }, ensure_ascii=False, indent=2)
        
    except Exception as e:
        logger.error(f"[ArXivTool] Search error: {e}")
        return json.dumps({
            "success": False,
            "error": f"Search error: {str(e)}"
        }, ensure_ascii=False)


def get_arxiv_paper_details(arxiv_id: str) -> str:
    """
    Get detailed information about a specific arXiv paper.
    
    Args:
        arxiv_id: arXiv paper ID (e.g., "2401.12345" or "arxiv:2401.12345")
    
    Returns:
        JSON string with paper details including full abstract
    """
    try:
        # Clean up ID
        clean_id = arxiv_id.replace('arxiv:', '').strip()
        
        xml_data = _make_api_request(
            search_query="",
            id_list=[clean_id],
            max_results=1
        )
        
        if xml_data is None:
            return json.dumps({
                "success": False,
                "error": f"Failed to fetch paper {arxiv_id} from arXiv API"
            }, ensure_ascii=False)
        
        result = _parse_atom_response(xml_data)
        
        if not result['papers']:
            return json.dumps({
                "success": False,
                "error": f"Paper {arxiv_id} not found"
            }, ensure_ascii=False)
        
        paper = result['papers'][0]
        
        return json.dumps({
            "success": True,
            "paper": {
                'id': paper['id'],
                'title': paper['title'],
                'authors': paper['authors'],
                'summary': paper['summary'],
                'categories': paper['categories'],
                'primary_category': paper['categories'][0] if paper['categories'] else '',
                'published': paper['published'],
                'updated': paper['updated'],
                'comment': paper['comment'],
                'journal': paper['journal'],
                'doi': paper['doi'],
                'abs_url': paper['abs_url'],
                'pdf_url': paper['pdf_url']
            }
        }, ensure_ascii=False, indent=2)
        
    except Exception as e:
        logger.error(f"[ArXivTool] Details error: {e}")
        return json.dumps({
            "success": False,
            "error": f"Error getting paper details: {str(e)}"
        }, ensure_ascii=False)


def download_arxiv_pdf(
    arxiv_id: str,
    output_dir: str = "./downloads",
    filename: str = "",
    max_retries: int = MAX_RETRIES,
    chunk_size: int = 8192  # 8KB chunks for large files
) -> str:
    """
    Download a PDF from arXiv with retry mechanism and chunked download.
    
    Args:
        arxiv_id: arXiv paper ID (e.g., "2401.12345")
        output_dir: Directory to save the PDF
        filename: Optional custom filename (defaults to {arxiv_id}.pdf)
        max_retries: Maximum retry attempts for network errors
        chunk_size: Size of download chunks in bytes
    
    Returns:
        JSON string with download result
    """
    # Clean up ID
    clean_id = arxiv_id.replace('arxiv:', '').strip()
    
    # Ensure output directory exists
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Determine filename
    if not filename:
        filename = f"{clean_id}.pdf"
    if not filename.endswith('.pdf'):
        filename += '.pdf'
    
    file_path = output_path / filename
    
    # Check if file already exists
    if file_path.exists():
        return json.dumps({
            "success": True,
            "file_path": str(file_path.absolute()),
            "arxiv_id": clean_id,
            "filename": filename,
            "size_bytes": file_path.stat().st_size,
            "already_exists": True,
            "message": "File already exists"
        }, ensure_ascii=False)
    
    # Download with retry
    pdf_url = f"{ARXIV_PDF_URL}/{clean_id}.pdf"
    logger.info(f"[ArXivTool] Downloading PDF from {pdf_url}")
    
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(
                pdf_url,
                headers={
                    'User-Agent': 'arXivTool/1.0 (research assistant)',
                    'Accept': 'application/pdf'
                }
            )
            
            # Use chunked download for large files
            with urllib.request.urlopen(req, timeout=PDF_TIMEOUT) as response:
                # Check content length if available
                content_length = response.headers.get('Content-Length')
                if content_length:
                    logger.info(f"[ArXivTool] Expected file size: {int(content_length):,} bytes")
                
                # Download in chunks
                downloaded = 0
                with open(file_path, 'wb') as f:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        # Log progress for large files every 1MB
                        if downloaded % (1024 * 1024) < chunk_size:
                            logger.info(f"[ArXivTool] Downloaded {downloaded:,} bytes...")
            
            file_size = file_path.stat().st_size
            logger.info(f"[ArXivTool] Successfully downloaded {file_size:,} bytes to {file_path}")
            
            # Verify download (basic check: file should not be empty)
            if file_size == 0:
                raise ValueError("Downloaded file is empty")
            
            # Verify it's a PDF (check magic bytes)
            with open(file_path, 'rb') as f:
                header = f.read(4)
                if header != b'%PDF':
                    logger.warning(f"[ArXivTool] Downloaded file may not be a valid PDF (header: {header})")
            
            return json.dumps({
                "success": True,
                "file_path": str(file_path.absolute()),
                "arxiv_id": clean_id,
                "filename": filename,
                "size_bytes": file_size,
                "pdf_url": pdf_url,
                "already_exists": False,
                "attempts": attempt + 1
            }, ensure_ascii=False)
            
        except HTTPError as e:
            last_error = e
            error_msg = f"HTTP Error {e.code}: {e.reason}"
            
            if e.code == 404:
                error_msg = f"Paper {arxiv_id} not found on arXiv"
                logger.error(f"[ArXivTool] {error_msg}")
                return json.dumps({
                    "success": False,
                    "arxiv_id": arxiv_id,
                    "error": error_msg
                }, ensure_ascii=False)
            
            # Retry on 5xx errors or rate limiting (429)
            if e.code >= 500 or e.code == 429:
                if attempt < max_retries:
                    delay = RETRY_DELAY_BASE * (2 ** attempt)
                    logger.warning(f"[ArXivTool] Server error, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                    continue
            
            logger.error(f"[ArXivTool] Download error: {error_msg}")
            return json.dumps({
                "success": False,
                "arxiv_id": arxiv_id,
                "error": error_msg,
                "attempts": attempt + 1
            }, ensure_ascii=False)
            
        except URLError as e:
            last_error = e
            if attempt < max_retries:
                delay = RETRY_DELAY_BASE * (2 ** attempt)
                logger.warning(f"[ArXivTool] Network error ({e.reason}), retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                continue
            
            logger.error(f"[ArXivTool] Network error after {max_retries + 1} attempts: {e.reason}")
            return json.dumps({
                "success": False,
                "arxiv_id": arxiv_id,
                "error": f"Network error: {str(e.reason)}. Please check your internet connection.",
                "attempts": attempt + 1
            }, ensure_ascii=False)
            
        except TimeoutError as e:
            last_error = e
            if attempt < max_retries:
                delay = RETRY_DELAY_BASE * (2 ** attempt)
                logger.warning(f"[ArXivTool] Timeout, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                continue
            
            logger.error(f"[ArXivTool] Timeout after {max_retries + 1} attempts")
            return json.dumps({
                "success": False,
                "arxiv_id": arxiv_id,
                "error": f"Download timeout. The paper may be too large or the network is slow. Try again later.",
                "attempts": attempt + 1
            }, ensure_ascii=False)
            
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                delay = RETRY_DELAY_BASE * (2 ** attempt)
                logger.warning(f"[ArXivTool] Error ({e}), retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                continue
            
            logger.error(f"[ArXivTool] Download error after {max_retries + 1} attempts: {e}")
            return json.dumps({
                "success": False,
                "arxiv_id": arxiv_id,
                "error": f"Download failed: {str(e)}",
                "attempts": attempt + 1
            }, ensure_ascii=False)
    
    # Should not reach here, but just in case
    return json.dumps({
        "success": False,
        "arxiv_id": arxiv_id,
        "error": f"Download failed after {max_retries + 1} attempts: {str(last_error)}"
    }, ensure_ascii=False)


# ============================================================================
# Tool Definitions
# ============================================================================

SEARCH_ARXIV_TOOL = Tool(
    name="search_arxiv_papers",
    description="Search for papers on arXiv. Supports searching by title, author, abstract, and category. Returns paper metadata including arXiv ID, title, authors, and URLs.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (e.g., 'machine learning', 'quantum computing')"
            },
            "search_field": {
                "type": "string",
                "description": "Field to search: 'all' (default), 'ti' (title), 'au' (author), 'abs' (abstract), 'cat' (category)",
                "enum": ["all", "ti", "au", "abs", "co", "jr", "cat", "rn", "id"],
                "default": "all"
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results to return (1-50, default 10)",
                "minimum": 1,
                "maximum": 50,
                "default": 10
            },
            "start": {
                "type": "integer",
                "description": "Start index for pagination (default 0)",
                "default": 0
            },
            "sort_by": {
                "type": "string",
                "description": "Sort field: 'relevance' (default), 'lastUpdatedDate', 'submittedDate'",
                "enum": ["relevance", "lastUpdatedDate", "submittedDate"],
                "default": "relevance"
            },
            "sort_order": {
                "type": "string",
                "description": "Sort order: 'descending' (default) or 'ascending'",
                "enum": ["ascending", "descending"],
                "default": "descending"
            },
            "category": {
                "type": "string",
                "description": "Filter by arXiv category (e.g., 'cs.AI', 'physics', 'math.ST')",
                "default": ""
            }
        },
        "required": ["query"]
    },
    func=search_arxiv_papers
)

GET_ARXIV_PAPER_DETAILS_TOOL = Tool(
    name="get_arxiv_paper_details",
    description="Get detailed information about a specific arXiv paper including full abstract, all authors, categories, and metadata.",
    parameters={
        "type": "object",
        "properties": {
            "arxiv_id": {
                "type": "string",
                "description": "arXiv paper ID (e.g., '2401.12345' or 'arxiv:2401.12345')"
            }
        },
        "required": ["arxiv_id"]
    },
    func=get_arxiv_paper_details
)

DOWNLOAD_ARXIV_PDF_TOOL = Tool(
    name="download_arxiv_pdf",
    description="Download a PDF from arXiv to local directory with automatic retry on network errors. Supports papers up to large sizes with chunked download and 3 retry attempts.",
    parameters={
        "type": "object",
        "properties": {
            "arxiv_id": {
                "type": "string",
                "description": "arXiv paper ID (e.g., '2401.12345')"
            },
            "output_dir": {
                "type": "string",
                "description": "Directory to save the PDF (default: ./downloads)",
                "default": "./downloads"
            },
            "filename": {
                "type": "string",
                "description": "Custom filename (optional, defaults to {arxiv_id}.pdf)",
                "default": ""
            }
        },
        "required": ["arxiv_id"]
    },
    func=download_arxiv_pdf
)


def batch_download_arxiv_pdfs(
    arxiv_ids: List[str],
    output_dir: str = "./downloads",
    delay_between: float = 1.0
) -> str:
    """
    Download multiple PDFs from arXiv in batch.
    
    Args:
        arxiv_ids: List of arXiv paper IDs (e.g., ["2401.12345", "2501.67890"])
        output_dir: Directory to save the PDFs
        delay_between: Delay in seconds between downloads (default: 1.0)
    
    Returns:
        JSON string with batch download results
    """
    try:
        logger.info(f"[ArXivTool] Starting batch download of {len(arxiv_ids)} papers")
        
        # Clean up IDs
        clean_ids = [aid.replace('arxiv:', '').strip() for aid in arxiv_ids]
        
        # Ensure output directory exists
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        results = {
            "success": True,
            "total": len(clean_ids),
            "successful": 0,
            "failed": 0,
            "already_existed": 0,
            "downloads": [],
            "errors": []
        }
        
        for i, arxiv_id in enumerate(clean_ids, 1):
            logger.info(f"[ArXivTool] Downloading {i}/{len(clean_ids)}: {arxiv_id}")
            
            result_json = download_arxiv_pdf(arxiv_id, output_dir=output_dir)
            result = json.loads(result_json)
            
            if result["success"]:
                if result.get("already_exists"):
                    results["already_existed"] += 1
                else:
                    results["successful"] += 1
                results["downloads"].append({
                    "arxiv_id": arxiv_id,
                    "file_path": result["file_path"],
                    "filename": result["filename"],
                    "size_bytes": result["size_bytes"],
                    "already_exists": result.get("already_exists", False)
                })
            else:
                results["failed"] += 1
                results["errors"].append({
                    "arxiv_id": arxiv_id,
                    "error": result.get("error", "Unknown error")
                })
            
            # Delay between downloads to avoid rate limiting (except for last one)
            if i < len(clean_ids) and delay_between > 0:
                time.sleep(delay_between)
        
        # Update overall success based on results
        if results["failed"] == results["total"]:
            results["success"] = False
        elif results["failed"] > 0:
            results["success"] = True  # Partial success
            results["message"] = f"Downloaded {results['successful']} papers, {results['failed']} failed"
        else:
            results["message"] = f"All {results['successful']} papers downloaded successfully"
        
        logger.info(f"[ArXivTool] Batch download complete: {results['successful']} success, {results['failed']} failed")
        
        return json.dumps(results, ensure_ascii=False, indent=2)
        
    except Exception as e:
        logger.error(f"[ArXivTool] Batch download error: {e}")
        return json.dumps({
            "success": False,
            "error": f"Batch download error: {str(e)}"
        }, ensure_ascii=False)


BATCH_DOWNLOAD_ARXIV_PDFS_TOOL = Tool(
    name="batch_download_arxiv_pdfs",
    description="Download multiple PDFs from arXiv in batch. Downloads papers sequentially with rate limiting to avoid server overload. Returns detailed results for each paper.",
    parameters={
        "type": "object",
        "properties": {
            "arxiv_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of arXiv paper IDs (e.g., ['2401.12345', '2501.67890'])"
            },
            "output_dir": {
                "type": "string",
                "description": "Directory to save the PDFs (default: ./downloads)",
                "default": "./downloads"
            },
            "delay_between": {
                "type": "number",
                "description": "Delay in seconds between downloads to avoid rate limiting (default: 1.0)",
                "default": 1.0
            }
        },
        "required": ["arxiv_ids"]
    },
    func=batch_download_arxiv_pdfs
)

# Tool list for registration
ARXIV_TOOLS = [
    SEARCH_ARXIV_TOOL,
    GET_ARXIV_PAPER_DETAILS_TOOL,
    DOWNLOAD_ARXIV_PDF_TOOL,
    BATCH_DOWNLOAD_ARXIV_PDFS_TOOL,
]
