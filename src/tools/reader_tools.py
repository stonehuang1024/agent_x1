"""
Reader Tool Module - Document to Markdown Conversion.

Provides tools for converting HTML, PDF, DOC, and other document formats
to clean markdown using the @vakra-dev/reader package (Node.js) with
Python fallback implementations.

Tools:
- CONVERT_URL_TO_MARKDOWN: Convert webpage URL to markdown
- CONVERT_PDF_TO_MARKDOWN: Convert PDF file to markdown
- CONVERT_HTML_TO_MARKDOWN: Convert HTML content/string to markdown
- CONVERT_FILE_TO_MARKDOWN: Auto-detect file type and convert to markdown
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse

from ..core.tool import Tool


# Try to import fallback libraries
try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False


def _get_reader_path() -> Optional[str]:
    """Get the path to the reader CLI executable."""
    # Check local node_modules first
    local_reader = Path(__file__).parent.parent.parent / "node_modules" / ".bin" / "reader"
    if local_reader.exists():
        return str(local_reader)
    
    # Check global npm
    try:
        result = subprocess.run(
            ["which", "reader"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    
    # Check npx
    try:
        result = subprocess.run(
            ["npx", "-p", "@vakra-dev/reader", "which", "reader"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return "npx"
    except Exception:
        pass
    
    return None


def _run_reader_scrape(url: str, formats: List[str] = None) -> Dict[str, Any]:
    """
    Run reader scrape command via CLI.
    
    Args:
        url: URL to scrape
        formats: Output formats (default: ["markdown"])
        
    Returns:
        Dictionary with markdown content and metadata
    """
    reader_path = _get_reader_path()
    if not reader_path:
        raise RuntimeError("Reader CLI not found. Please install: npm install @vakra-dev/reader")
    
    if formats is None:
        formats = ["markdown"]
    
    # Create temp file for output
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        output_file = f.name
    
    try:
        # Build command
        if reader_path == "npx":
            cmd = ["npx", "-p", "@vakra-dev/reader", "reader", "scrape", url]
        else:
            cmd = [reader_path, "scrape", url]
        
        # Add format option
        for fmt in formats:
            cmd.extend(["--format", fmt])
        
        # Run reader
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode != 0:
            return {
                "success": False,
                "error": f"Reader CLI failed: {result.stderr}",
                "url": url
            }
        
        # Parse output - reader outputs markdown directly or JSON
        output = result.stdout.strip()
        
        return {
            "success": True,
            "markdown": output,
            "url": url,
            "format": "markdown"
        }
        
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Reader CLI timed out after 60 seconds",
            "url": url
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Error running reader: {str(e)}",
            "url": url
        }
    finally:
        # Cleanup temp file
        try:
            os.unlink(output_file)
        except:
            pass


def _fallback_url_to_markdown(url: str) -> Dict[str, Any]:
    """Fallback: Use requests + BeautifulSoup to convert URL to markdown."""
    try:
        import requests
        
        # Fetch URL
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        html_content = response.text
        
        # Convert to markdown
        return _fallback_html_to_markdown(html_content, url)
        
    except Exception as e:
        return {
            "success": False,
            "error": f"Fallback failed: {str(e)}",
            "url": url
        }


def _fallback_html_to_markdown(html: str, source: str = "") -> Dict[str, Any]:
    """Fallback: Convert HTML to markdown using BeautifulSoup."""
    if not BS4_AVAILABLE:
        return {
            "success": False,
            "error": "BeautifulSoup not available and Reader CLI not found",
            "source": source
        }
    
    try:
        soup = BeautifulSoup(html, 'lxml')
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()
        
        # Extract title
        title = ""
        title_tag = soup.find('title')
        if title_tag:
            title = title_tag.get_text().strip()
        
        # Convert headings
        markdown_lines = []
        if title:
            markdown_lines.append(f"# {title}\n")
        
        # Process body content
        body = soup.find('body') or soup
        
        for element in body.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'ul', 'ol', 'li', 'a', 'strong', 'em', 'code', 'pre']):
            if element.name == 'h1':
                markdown_lines.append(f"# {element.get_text().strip()}\n")
            elif element.name == 'h2':
                markdown_lines.append(f"## {element.get_text().strip()}\n")
            elif element.name == 'h3':
                markdown_lines.append(f"### {element.get_text().strip()}\n")
            elif element.name == 'h4':
                markdown_lines.append(f"#### {element.get_text().strip()}\n")
            elif element.name == 'p':
                text = element.get_text().strip()
                if text:
                    markdown_lines.append(f"{text}\n")
            elif element.name == 'ul':
                for li in element.find_all('li'):
                    markdown_lines.append(f"- {li.get_text().strip()}\n")
            elif element.name == 'ol':
                for i, li in enumerate(element.find_all('li'), 1):
                    markdown_lines.append(f"{i}. {li.get_text().strip()}\n")
            elif element.name == 'a':
                href = element.get('href', '')
                text = element.get_text().strip()
                if href and text:
                    markdown_lines.append(f"[{text}]({href})\n")
            elif element.name == 'strong' or element.name == 'b':
                markdown_lines.append(f"**{element.get_text().strip()}** ")
            elif element.name == 'em' or element.name == 'i':
                markdown_lines.append(f"*{element.get_text().strip()}* ")
            elif element.name == 'code':
                markdown_lines.append(f"`{element.get_text().strip()}` ")
            elif element.name == 'pre':
                code = element.get_text().strip()
                markdown_lines.append(f"```\n{code}\n```\n")
        
        markdown = '\n'.join(markdown_lines)
        
        # Clean up
        markdown = '\n\n'.join(line for line in markdown.split('\n') if line.strip())
        
        return {
            "success": True,
            "markdown": markdown,
            "source": source,
            "format": "markdown",
            "title": title,
            "fallback": True
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": f"HTML to markdown conversion failed: {str(e)}",
            "source": source
        }


def _fallback_pdf_to_markdown(file_path: str) -> Dict[str, Any]:
    """Fallback: Use PyMuPDF to convert PDF to markdown."""
    if not PYMUPDF_AVAILABLE:
        return {
            "success": False,
            "error": "PyMuPDF not available and Reader CLI not found. Install: pip install pymupdf",
            "file_path": file_path
        }
    
    try:
        doc = fitz.open(file_path)
        
        markdown_lines = []
        markdown_lines.append(f"# PDF Document: {Path(file_path).name}\n")
        markdown_lines.append(f"**Pages:** {len(doc)}\n\n---\n")
        
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            text = page.get_text()
            
            if text.strip():
                markdown_lines.append(f"## Page {page_num + 1}\n")
                markdown_lines.append(text)
                markdown_lines.append("\n---\n")
        
        doc.close()
        
        markdown = '\n'.join(markdown_lines)
        
        return {
            "success": True,
            "markdown": markdown,
            "file_path": file_path,
            "format": "markdown",
            "pages": len(doc),
            "fallback": True
        }
        
    except Exception as e:
        return {
            "success": False,
            "error": f"PDF to markdown conversion failed: {str(e)}",
            "file_path": file_path
        }


def convert_url_to_markdown(url: str, use_reader: bool = True) -> str:
    """
    Convert a webpage URL to markdown.
    
    Args:
        url: The URL to convert
        use_reader: Whether to try using Reader CLI first
        
    Returns:
        JSON string with markdown content and metadata
    """
    if use_reader:
        reader_path = _get_reader_path()
        if reader_path:
            result = _run_reader_scrape(url, formats=["markdown"])
            if result["success"]:
                return json.dumps(result, ensure_ascii=False, indent=2)
    
    # Fallback to Python implementation
    result = _fallback_url_to_markdown(url)
    return json.dumps(result, ensure_ascii=False, indent=2)


def convert_pdf_to_markdown(file_path: str, use_reader: bool = True) -> str:
    """
    Convert a PDF file to markdown.
    
    Args:
        file_path: Path to the PDF file
        use_reader: Whether to try using Reader CLI first
        
    Returns:
        JSON string with markdown content and metadata
    """
    # Validate file exists
    path = Path(file_path)
    if not path.exists():
        return json.dumps({
            "success": False,
            "error": f"File not found: {file_path}"
        }, ensure_ascii=False)
    
    if not path.suffix.lower() == '.pdf':
        return json.dumps({
            "success": False,
            "error": f"File is not a PDF: {file_path}"
        }, ensure_ascii=False)
    
    # Try Reader CLI first if available
    if use_reader:
        reader_path = _get_reader_path()
        if reader_path:
            # Reader can handle PDFs via URL or local path
            result = _run_reader_scrape(f"file://{path.absolute()}", formats=["markdown"])
            if result["success"]:
                return json.dumps(result, ensure_ascii=False, indent=2)
    
    # Fallback to PyMuPDF
    result = _fallback_pdf_to_markdown(str(path.absolute()))
    return json.dumps(result, ensure_ascii=False, indent=2)


def convert_html_to_markdown(html: str, source: str = "") -> str:
    """
    Convert HTML content to markdown.
    
    Args:
        html: HTML content string
        source: Source identifier (optional)
        
    Returns:
        JSON string with markdown content and metadata
    """
    result = _fallback_html_to_markdown(html, source)
    return json.dumps(result, ensure_ascii=False, indent=2)


def convert_file_to_markdown(file_path: str) -> str:
    """
    Auto-detect file type and convert to markdown.
    
    Args:
        file_path: Path to the file
        
    Returns:
        JSON string with markdown content and metadata
    """
    path = Path(file_path)
    
    if not path.exists():
        return json.dumps({
            "success": False,
            "error": f"File not found: {file_path}"
        }, ensure_ascii=False)
    
    suffix = path.suffix.lower()
    
    if suffix == '.pdf':
        return convert_pdf_to_markdown(file_path)
    elif suffix in ['.html', '.htm']:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                html = f.read()
            return convert_html_to_markdown(html, file_path)
        except Exception as e:
            return json.dumps({
                "success": False,
                "error": f"Error reading HTML file: {str(e)}"
            }, ensure_ascii=False)
    elif suffix in ['.txt', '.md', '.markdown']:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return json.dumps({
                "success": True,
                "markdown": content,
                "file_path": file_path,
                "format": "markdown" if suffix in ['.md', '.markdown'] else "text"
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({
                "success": False,
                "error": f"Error reading file: {str(e)}"
            }, ensure_ascii=False)
    else:
        return json.dumps({
            "success": False,
            "error": f"Unsupported file type: {suffix}. Supported: .pdf, .html, .htm, .txt, .md"
        }, ensure_ascii=False)


# ============================================================================
# Tool Definitions
# ============================================================================

CONVERT_URL_TO_MARKDOWN_TOOL = Tool(
    name="convert_url_to_markdown",
    description="Convert a webpage URL to clean markdown format. Uses the Reader package (if available) or falls back to Python implementation.",
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL of the webpage to convert (e.g., https://example.com/article)"
            },
            "use_reader": {
                "type": "boolean",
                "description": "Whether to try using the Reader CLI first (default: true)",
                "default": True
            }
        },
        "required": ["url"]
    },
    func=convert_url_to_markdown,
    timeout_seconds=60,
    max_output_chars=60000,
    is_readonly=True,
)

CONVERT_PDF_TO_MARKDOWN_TOOL = Tool(
    name="convert_pdf_to_markdown",
    description="Convert a PDF file to markdown format. Uses the Reader package (if available) or falls back to PyMuPDF.",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Full path to the PDF file (e.g., /path/to/document.pdf)"
            },
            "use_reader": {
                "type": "boolean",
                "description": "Whether to try using the Reader CLI first (default: true)",
                "default": True
            }
        },
        "required": ["file_path"]
    },
    func=convert_pdf_to_markdown,
    timeout_seconds=120,
    max_output_chars=60000,
    is_readonly=True,
)

CONVERT_HTML_TO_MARKDOWN_TOOL = Tool(
    name="convert_html_to_markdown",
    description="Convert HTML content to markdown format.",
    parameters={
        "type": "object",
        "properties": {
            "html": {
                "type": "string",
                "description": "The HTML content string to convert"
            },
            "source": {
                "type": "string",
                "description": "Optional source identifier for context",
                "default": ""
            }
        },
        "required": ["html"]
    },
    func=convert_html_to_markdown,
    timeout_seconds=30,
    max_output_chars=60000,
    is_readonly=True,
)

CONVERT_FILE_TO_MARKDOWN_TOOL = Tool(
    name="convert_file_to_markdown",
    description="Auto-detect file type and convert to markdown. Supports PDF, HTML, TXT, MD files.",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Full path to the file to convert (e.g., /path/to/file.pdf or /path/to/page.html)"
            }
        },
        "required": ["file_path"]
    },
    func=convert_file_to_markdown,
    timeout_seconds=60,
    max_output_chars=60000,
    is_readonly=True,
)

# List of all reader tools
READER_TOOLS = [
    CONVERT_URL_TO_MARKDOWN_TOOL,
    CONVERT_PDF_TO_MARKDOWN_TOOL,
    CONVERT_HTML_TO_MARKDOWN_TOOL,
    CONVERT_FILE_TO_MARKDOWN_TOOL,
]
