"""
Web Tools Module - Web content retrieval and scraping.

Provides tools for:
- Fetching URL content (HTML, JSON, text)
- Extracting clean text from web pages
- Downloading files from URLs
- Checking URL reachability
"""

import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse

from ..core.tool import Tool
from ..util.http_client import http_get, http_download, _is_ssl_error, _curl_get

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
_DEFAULT_TIMEOUT = 15


def fetch_url(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, str]] = None,
    timeout: int = _DEFAULT_TIMEOUT,
    max_chars: int = 50000
) -> Dict[str, Any]:
    """
    Fetch content from a URL.

    Args:
        url: Target URL
        method: HTTP method (GET, POST) - default GET
        headers: Additional request headers
        params: Query parameters
        timeout: Request timeout seconds (default: 15)
        max_chars: Max response characters to return (default: 50000)

    Returns:
        Dictionary with response status, headers, content
    """
    try:
        import requests
    except ImportError:
        return {"error": "requests not installed"}

    req_headers = dict(_DEFAULT_HEADERS)
    if headers:
        req_headers.update(headers)

    logger.info(f"[FetchURL] {method} {url[:200]}")

    try:
        resp = requests.request(
            method.upper(),
            url,
            headers=req_headers,
            params=params,
            timeout=timeout,
            allow_redirects=True
        )

        content_type = resp.headers.get("Content-Type", "")
        is_json = "application/json" in content_type
        is_text = any(t in content_type for t in ["text/", "application/json", "application/xml"])

        body: Any
        if is_json:
            try:
                body = resp.json()
            except Exception:
                body = resp.text[:max_chars]
        elif is_text:
            body = resp.text[:max_chars]
        else:
            body = f"[Binary content, {len(resp.content)} bytes]"

        return {
            "url": resp.url,
            "status_code": resp.status_code,
            "ok": resp.ok,
            "content_type": content_type,
            "content_length": len(resp.content),
            "response_headers": dict(resp.headers),
            "body": body,
            "truncated": is_text and len(resp.text) > max_chars
        }
    except Exception as e:
        if _is_ssl_error(e):
            logger.warning(f"[FetchURL] SSL error, falling back to curl: {e}")
            result = _curl_get(url, headers=req_headers, timeout=timeout, max_chars=max_chars)
            if result.get("success") or result.get("status_code", 0) > 0:
                return {
                    "url": url,
                    "status_code": result.get("status_code", 0),
                    "ok": result.get("success", False),
                    "content_type": result.get("headers", {}).get("Content-Type", ""),
                    "content_length": len(result.get("body", "")),
                    "response_headers": result.get("headers", {}),
                    "body": result.get("body", ""),
                    "truncated": False,
                    "method": "curl_fallback"
                }
            return {"error": result.get("error", str(e)), "url": url}
        logger.exception("[FetchURL] Failed")
        return {"error": str(e), "url": url}


def extract_webpage_text(url: str, timeout: int = _DEFAULT_TIMEOUT, max_chars: int = 20000) -> Dict[str, Any]:
    """
    Fetch a web page and extract clean readable text (strips HTML tags).

    Args:
        url: Web page URL
        timeout: Request timeout seconds
        max_chars: Max text characters to return

    Returns:
        Dictionary with clean text content and page metadata
    """
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return {"error": "requests and beautifulsoup4 not installed. Run: pip install requests beautifulsoup4"}

    logger.info(f"[ExtractWebpage] {url[:200]}")

    try:
        resp = requests.get(url, headers=_DEFAULT_HEADERS, timeout=timeout)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
            tag.decompose()

        title = soup.title.get_text(strip=True) if soup.title else ""

        meta_desc = ""
        meta_tag = soup.find("meta", attrs={"name": "description"})
        if meta_tag:
            meta_desc = meta_tag.get("content", "")

        main = soup.find("main") or soup.find("article") or soup.find("body")
        if main:
            text = main.get_text(separator="\n", strip=True)
        else:
            text = soup.get_text(separator="\n", strip=True)

        lines = [line for line in text.splitlines() if line.strip()]
        clean_text = "\n".join(lines)

        return {
            "url": url,
            "title": title,
            "description": meta_desc,
            "text": clean_text[:max_chars],
            "text_length": len(clean_text),
            "truncated": len(clean_text) > max_chars,
            "status_code": resp.status_code
        }
    except Exception as e:
        logger.exception("[ExtractWebpage] Failed")
        return {"error": str(e), "url": url}


def extract_links(url: str, same_domain_only: bool = False, timeout: int = _DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """
    Extract all hyperlinks from a web page.

    Args:
        url: Web page URL
        same_domain_only: Only return links from the same domain
        timeout: Request timeout seconds

    Returns:
        Dictionary with list of found links
    """
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return {"error": "requests and beautifulsoup4 not installed"}

    try:
        resp = requests.get(url, headers=_DEFAULT_HEADERS, timeout=timeout)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        base_domain = urlparse(url).netloc

        links = []
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                parsed = urlparse(url)
                href = f"{parsed.scheme}://{parsed.netloc}{href}"
            elif not href.startswith("http"):
                continue

            link_domain = urlparse(href).netloc
            if same_domain_only and link_domain != base_domain:
                continue

            links.append({
                "url": href,
                "text": a_tag.get_text(strip=True)[:200],
                "domain": link_domain
            })

        unique_links = list({l["url"]: l for l in links}.values())

        return {
            "source_url": url,
            "total_links": len(unique_links),
            "same_domain_only": same_domain_only,
            "links": unique_links[:200]
        }
    except Exception as e:
        logger.exception("[ExtractLinks] Failed")
        return {"error": str(e), "url": url}


def download_file(url: str, output_path: str, timeout: int = 60) -> Dict[str, Any]:
    """
    Download a file from a URL to local disk.

    Args:
        url: File URL to download
        output_path: Local file path to save to
        timeout: Download timeout seconds (default: 60)

    Returns:
        Dictionary with download result
    """
    try:
        import requests
    except ImportError:
        return {"error": "requests not installed"}

    try:
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"[DownloadFile] {url[:200]} -> {out}")

        with requests.get(url, headers=_DEFAULT_HEADERS, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            total_bytes = 0
            with open(out, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    f.write(chunk)
                    total_bytes += len(chunk)

        return {
            "url": url,
            "output_path": str(out),
            "file_size_bytes": total_bytes,
            "success": True
        }
    except Exception as e:
        if _is_ssl_error(e):
            logger.warning(f"[DownloadFile] SSL error, falling back to curl: {e}")
            out = Path(output_path).expanduser().resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            result = http_download(url, str(out), headers=dict(_DEFAULT_HEADERS), timeout=timeout)
            if result.get("success"):
                return {
                    "url": url,
                    "output_path": result.get("file_path", str(out)),
                    "file_size_bytes": result.get("size_bytes", 0),
                    "success": True,
                    "method": "curl_fallback"
                }
            return {"error": result.get("error", str(e)), "url": url}
        logger.exception("[DownloadFile] Failed")
        return {"error": str(e), "url": url}


def check_url(url: str, timeout: int = 10) -> Dict[str, Any]:
    """
    Check if a URL is reachable and get basic info.

    Args:
        url: URL to check
        timeout: Timeout seconds (default: 10)

    Returns:
        Dictionary with reachability and response info
    """
    try:
        import requests
    except ImportError:
        return {"error": "requests not installed"}

    try:
        resp = requests.head(url, headers=_DEFAULT_HEADERS, timeout=timeout, allow_redirects=True)
        return {
            "url": url,
            "final_url": resp.url,
            "reachable": True,
            "status_code": resp.status_code,
            "content_type": resp.headers.get("Content-Type", ""),
            "content_length": resp.headers.get("Content-Length"),
            "server": resp.headers.get("Server", ""),
            "redirect_count": len(resp.history)
        }
    except Exception as e:
        if _is_ssl_error(e):
            logger.warning(f"[CheckURL] SSL error, falling back to curl: {e}")
            result = _curl_get(url, headers=dict(_DEFAULT_HEADERS), timeout=timeout, max_chars=1)
            if result.get("status_code", 0) > 0:
                return {
                    "url": url,
                    "final_url": url,
                    "reachable": True,
                    "status_code": result["status_code"],
                    "content_type": result.get("headers", {}).get("Content-Type", ""),
                    "content_length": result.get("headers", {}).get("Content-Length"),
                    "server": result.get("headers", {}).get("Server", ""),
                    "redirect_count": 0,
                    "method": "curl_fallback"
                }
        return {
            "url": url,
            "reachable": False,
            "error": str(e)
        }


def fetch_rss_feed(url: str, max_items: int = 20) -> Dict[str, Any]:
    """
    Fetch and parse an RSS/Atom feed.

    Args:
        url: RSS or Atom feed URL
        max_items: Maximum items to return (default: 20)

    Returns:
        Dictionary with feed metadata and items
    """
    try:
        import feedparser
    except ImportError:
        return {"error": "feedparser not installed. Run: pip install feedparser"}

    try:
        logger.info(f"[RSSFeed] Fetching: {url[:200]}")
        feed = feedparser.parse(url)

        if feed.get("bozo") and not feed.entries:
            return {"error": f"Failed to parse feed: {feed.get('bozo_exception', 'unknown error')}"}

        items = []
        for entry in feed.entries[:max_items]:
            items.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
                "summary": entry.get("summary", "")[:500]
            })

        return {
            "feed_url": url,
            "feed_title": feed.feed.get("title", ""),
            "feed_description": feed.feed.get("description", "")[:300],
            "feed_link": feed.feed.get("link", ""),
            "item_count": len(items),
            "items": items
        }
    except Exception as e:
        logger.exception("[RSSFeed] Failed")
        return {"error": str(e), "url": url}


# Tool Definitions
FETCH_URL_TOOL = Tool(
    name="fetch_url",
    description=(
        "Fetch content from any URL (HTTP GET/POST). "
        "Returns status code, response headers, and body content. "
        "Automatically parses JSON responses."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Target URL"},
            "method": {"type": "string", "enum": ["GET", "POST"], "description": "HTTP method (default: GET)"},
            "headers": {"type": "object", "additionalProperties": {"type": "string"}, "description": "Extra request headers"},
            "params": {"type": "object", "additionalProperties": {"type": "string"}, "description": "Query parameters"},
            "timeout": {"type": "integer", "description": "Request timeout seconds (default: 15)"},
            "max_chars": {"type": "integer", "description": "Max response characters (default: 50000)"}
        },
        "required": ["url"]
    },
    func=fetch_url,
    timeout_seconds=30,
    max_output_chars=60000,
    is_readonly=True,
)

EXTRACT_WEBPAGE_TEXT_TOOL = Tool(
    name="extract_webpage_text",
    description=(
        "Fetch a web page and extract clean readable text by stripping HTML tags. "
        "Returns title, description meta tag, and clean body text."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Web page URL"},
            "timeout": {"type": "integer", "description": "Request timeout seconds (default: 15)"},
            "max_chars": {"type": "integer", "description": "Max text characters (default: 20000)"}
        },
        "required": ["url"]
    },
    func=extract_webpage_text,
    timeout_seconds=30,
    max_output_chars=60000,
    is_readonly=True,
)

EXTRACT_LINKS_TOOL = Tool(
    name="extract_links",
    description="Extract all hyperlinks from a web page. Optionally filter to same-domain links only.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Web page URL"},
            "same_domain_only": {"type": "boolean", "description": "Only return same-domain links (default: false)"},
            "timeout": {"type": "integer", "description": "Request timeout seconds (default: 15)"}
        },
        "required": ["url"]
    },
    func=extract_links,
    timeout_seconds=30,
    max_output_chars=30000,
    is_readonly=True,
)

DOWNLOAD_FILE_TOOL = Tool(
    name="download_file",
    description="Download a file from a URL to local disk. Returns file size and path.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "File URL to download"},
            "output_path": {"type": "string", "description": "Local file path to save to"},
            "timeout": {"type": "integer", "description": "Download timeout seconds (default: 60)"}
        },
        "required": ["url", "output_path"]
    },
    func=download_file,
    timeout_seconds=120,
    max_output_chars=5000,
)

CHECK_URL_TOOL = Tool(
    name="check_url",
    description="Check if a URL is reachable. Returns status code, content type, redirect count.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to check"},
            "timeout": {"type": "integer", "description": "Timeout seconds (default: 10)"}
        },
        "required": ["url"]
    },
    func=check_url,
    timeout_seconds=15,
    max_output_chars=5000,
    is_readonly=True,
)

FETCH_RSS_FEED_TOOL = Tool(
    name="fetch_rss_feed",
    description=(
        "Fetch and parse an RSS or Atom feed. Returns feed title, description, and latest items "
        "with titles, links, publication dates, and summaries."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "RSS or Atom feed URL"},
            "max_items": {"type": "integer", "description": "Maximum items to return (default: 20)"}
        },
        "required": ["url"]
    },
    func=fetch_rss_feed,
    timeout_seconds=30,
    max_output_chars=30000,
    is_readonly=True,
)

WEB_TOOLS = [
    FETCH_URL_TOOL,
    EXTRACT_WEBPAGE_TEXT_TOOL,
    EXTRACT_LINKS_TOOL,
    DOWNLOAD_FILE_TOOL,
    CHECK_URL_TOOL,
    FETCH_RSS_FEED_TOOL,
]
