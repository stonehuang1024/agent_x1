"""
HTTP Client Utility - Robust HTTP requests with curl fallback.

When Python's OpenSSL encounters SSL errors (e.g., 'record layer failure'
due to OpenSSL/LibreSSL incompatibility with certain servers), this module
automatically falls back to using curl subprocess which uses the system's
SecureTransport/LibreSSL.

Usage:
    from ..util.http_client import http_get, http_download

    # GET request (returns text)
    response = http_get("https://example.com/api", timeout=30)
    if response["success"]:
        print(response["body"])

    # Download file
    result = http_download("https://example.com/file.pdf", "/tmp/file.pdf")
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError

logger = logging.getLogger(__name__)

# Check if curl is available
CURL_PATH = shutil.which("curl")


def _is_ssl_error(error: Exception) -> bool:
    """Check if an exception is an SSL-related error."""
    error_str = str(error).lower()
    ssl_indicators = [
        "ssl",
        "record layer failure",
        "certificate verify",
        "handshake",
        "tlsv1",
        "sslv3",
    ]
    return any(indicator in error_str for indicator in ssl_indicators)


def _curl_get(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
    max_chars: int = 0,
) -> Dict[str, Any]:
    """
    Perform HTTP GET using curl subprocess.

    Returns:
        Dict with keys: success, status_code, body, headers, error
    """
    if not CURL_PATH:
        return {"success": False, "error": "curl not found on system"}

    # Use a temp file for response headers to avoid mixing with body
    import tempfile as _tempfile
    header_file = _tempfile.NamedTemporaryFile(mode='w', suffix='.headers', delete=False)
    header_file_path = header_file.name
    header_file.close()

    cmd = [
        CURL_PATH,
        "-sS",  # silent but show errors
        "-L",  # follow redirects
        "--max-time", str(timeout),
        "-w", "\n__HTTP_CODE__%{http_code}",  # append status code
        "-D", header_file_path,  # dump headers to temp file
    ]

    if headers:
        for key, value in headers.items():
            cmd.extend(["-H", f"{key}: {value}"])

    cmd.append(url)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 10,  # extra buffer for subprocess
        )

        output = result.stdout
        stderr = result.stderr

        # Extract HTTP status code from the appended marker
        status_code = 0
        if "__HTTP_CODE__" in output:
            parts = output.rsplit("__HTTP_CODE__", 1)
            output = parts[0]
            try:
                status_code = int(parts[1].strip())
            except ValueError:
                pass

        # Body is the entire output (headers are in the temp file)
        body = output
        
        # Parse response headers from temp file
        resp_headers = {}
        try:
            with open(header_file_path, 'r') as hf:
                for line in hf:
                    line = line.strip()
                    if ": " in line:
                        key, _, value = line.partition(": ")
                        resp_headers[key.strip()] = value.strip()
        except Exception:
            pass

        if max_chars > 0 and len(body) > max_chars:
            body = body[:max_chars]

        if result.returncode != 0 and status_code == 0:
            return {
                "success": False,
                "status_code": 0,
                "error": f"curl error (code {result.returncode}): {stderr.strip()}",
                "body": "",
                "headers": {},
            }

        return {
            "success": status_code < 400,
            "status_code": status_code,
            "body": body,
            "headers": resp_headers,
            "error": stderr.strip() if status_code >= 400 else "",
        }

    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"curl timeout after {timeout}s", "status_code": 0}
    except Exception as e:
        return {"success": False, "error": f"curl error: {str(e)}", "status_code": 0}
    finally:
        try:
            os.unlink(header_file_path)
        except OSError:
            pass


def _curl_download(
    url: str,
    output_path: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 120,
) -> Dict[str, Any]:
    """
    Download a file using curl subprocess.

    Returns:
        Dict with keys: success, file_path, size_bytes, status_code, error
    """
    if not CURL_PATH:
        return {"success": False, "error": "curl not found on system"}

    cmd = [
        CURL_PATH,
        "-sS",
        "-L",
        "--max-time", str(timeout),
        "-o", output_path,
        "-w", "%{http_code}",
    ]

    if headers:
        for key, value in headers.items():
            cmd.extend(["-H", f"{key}: {value}"])

    cmd.append(url)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 10,
        )

        status_code = 0
        try:
            status_code = int(result.stdout.strip())
        except ValueError:
            pass

        if result.returncode != 0:
            return {
                "success": False,
                "error": f"curl download error (code {result.returncode}): {result.stderr.strip()}",
                "status_code": status_code,
            }

        file_path = Path(output_path)
        if not file_path.exists():
            return {
                "success": False,
                "error": "curl completed but file not found",
                "status_code": status_code,
            }

        size = file_path.stat().st_size
        if size == 0:
            return {
                "success": False,
                "error": "Downloaded file is empty",
                "status_code": status_code,
            }

        return {
            "success": status_code < 400,
            "file_path": str(file_path.absolute()),
            "size_bytes": size,
            "status_code": status_code,
            "error": "" if status_code < 400 else f"HTTP {status_code}",
        }

    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"curl download timeout after {timeout}s"}
    except Exception as e:
        return {"success": False, "error": f"curl download error: {str(e)}"}


def http_get(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
    max_chars: int = 0,
) -> Dict[str, Any]:
    """
    Perform HTTP GET request with automatic curl fallback on SSL errors.

    Args:
        url: Target URL
        headers: Optional request headers
        timeout: Request timeout in seconds
        max_chars: Max response body characters (0 = unlimited)

    Returns:
        Dict with keys: success, status_code, body, headers, error, method
    """
    # Try Python requests first
    try:
        import requests as req_lib

        req_headers = headers or {}
        if "User-Agent" not in req_headers:
            req_headers["User-Agent"] = "Mozilla/5.0 (compatible; AgentX1/1.0)"

        resp = req_lib.get(url, headers=req_headers, timeout=timeout, allow_redirects=True)

        body = resp.text
        if max_chars > 0 and len(body) > max_chars:
            body = body[:max_chars]

        return {
            "success": resp.ok,
            "status_code": resp.status_code,
            "body": body,
            "headers": dict(resp.headers),
            "error": "" if resp.ok else f"HTTP {resp.status_code}",
            "method": "requests",
        }

    except Exception as e:
        is_ssl = _is_ssl_error(e)
        is_timeout = "timed out" in str(e).lower() or "timeout" in str(e).lower()
        
        if is_ssl or is_timeout:
            reason = "SSL error" if is_ssl else "timeout"
            logger.warning(
                f"[HTTPClient] Python {reason} for {url}: {e}. "
                f"Falling back to curl."
            )
            result = _curl_get(url, headers=headers, timeout=timeout, max_chars=max_chars)
            result["method"] = "curl_fallback"
            return result
        else:
            # Non-SSL error, don't fallback
            return {
                "success": False,
                "status_code": 0,
                "body": "",
                "headers": {},
                "error": f"Request error: {str(e)}",
                "method": "requests",
            }


def http_get_bytes(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Perform HTTP GET request returning raw bytes, with curl fallback.

    Args:
        url: Target URL
        headers: Optional request headers
        timeout: Request timeout in seconds

    Returns:
        Dict with keys: success, status_code, data (bytes), headers, error, method
    """
    try:
        import requests as req_lib

        req_headers = headers or {}
        if "User-Agent" not in req_headers:
            req_headers["User-Agent"] = "Mozilla/5.0 (compatible; AgentX1/1.0)"

        resp = req_lib.get(url, headers=req_headers, timeout=timeout, allow_redirects=True)

        return {
            "success": resp.ok,
            "status_code": resp.status_code,
            "data": resp.content,
            "headers": dict(resp.headers),
            "error": "" if resp.ok else f"HTTP {resp.status_code}",
            "method": "requests",
        }

    except Exception as e:
        is_ssl = _is_ssl_error(e)
        is_timeout = "timed out" in str(e).lower() or "timeout" in str(e).lower()
        
        if is_ssl or is_timeout:
            reason = "SSL error" if is_ssl else "timeout"
            logger.warning(
                f"[HTTPClient] Python {reason} for {url}: {e}. "
                f"Falling back to curl for binary download."
            )
            # Use curl to download to a temp file, then read bytes
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = tmp.name

            try:
                result = _curl_download(url, tmp_path, headers=headers, timeout=timeout)
                if result.get("success"):
                    with open(tmp_path, "rb") as f:
                        data = f.read()
                    return {
                        "success": True,
                        "status_code": result.get("status_code", 200),
                        "data": data,
                        "headers": {},
                        "error": "",
                        "method": "curl_fallback",
                    }
                else:
                    return {
                        "success": False,
                        "status_code": result.get("status_code", 0),
                        "data": b"",
                        "headers": {},
                        "error": result.get("error", "Unknown curl error"),
                        "method": "curl_fallback",
                    }
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        else:
            return {
                "success": False,
                "status_code": 0,
                "data": b"",
                "headers": {},
                "error": f"Request error: {str(e)}",
                "method": "requests",
            }


def http_download(
    url: str,
    output_path: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 120,
) -> Dict[str, Any]:
    """
    Download a file from URL with automatic curl fallback on SSL errors.

    Args:
        url: File URL to download
        output_path: Local file path to save to
        headers: Optional request headers
        timeout: Download timeout in seconds

    Returns:
        Dict with keys: success, file_path, size_bytes, status_code, error, method
    """
    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Try Python requests first
    try:
        import requests as req_lib

        req_headers = headers or {}
        if "User-Agent" not in req_headers:
            req_headers["User-Agent"] = "Mozilla/5.0 (compatible; AgentX1/1.0)"

        resp = req_lib.get(
            url, headers=req_headers, timeout=timeout,
            allow_redirects=True, stream=True,
        )
        resp.raise_for_status()

        downloaded = 0
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)

        file_path = Path(output_path)
        size = file_path.stat().st_size

        if size == 0:
            raise ValueError("Downloaded file is empty")

        return {
            "success": True,
            "file_path": str(file_path.absolute()),
            "size_bytes": size,
            "status_code": resp.status_code,
            "error": "",
            "method": "requests",
        }

    except Exception as e:
        is_ssl = _is_ssl_error(e)
        is_timeout = "timed out" in str(e).lower() or "timeout" in str(e).lower()
        
        if is_ssl or is_timeout:
            reason = "SSL error" if is_ssl else "timeout"
            logger.warning(
                f"[HTTPClient] Python {reason} downloading {url}: {e}. "
                f"Falling back to curl."
            )
            result = _curl_download(url, output_path, headers=headers, timeout=timeout)
            result["method"] = "curl_fallback"
            return result
        else:
            return {
                "success": False,
                "file_path": "",
                "size_bytes": 0,
                "status_code": 0,
                "error": f"Download error: {str(e)}",
                "method": "requests",
            }


def urllib_get(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """
    Perform HTTP GET using urllib with curl fallback on SSL errors.
    This is for code that specifically needs urllib-style behavior.

    Returns:
        Dict with keys: success, status_code, data (str), error, method
    """
    import urllib.request

    req_headers = headers or {
        "User-Agent": "Mozilla/5.0 (compatible; AgentX1/1.0)"
    }

    req = urllib.request.Request(url, headers=req_headers)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read().decode("utf-8")
            return {
                "success": True,
                "status_code": response.status,
                "data": data,
                "error": "",
                "method": "urllib",
            }

    except HTTPError as e:
        # HTTP errors are not SSL errors - return them directly
        return {
            "success": False,
            "status_code": e.code,
            "data": "",
            "error": f"HTTP {e.code}: {e.reason}",
            "method": "urllib",
        }

    except (URLError, TimeoutError, OSError) as e:
        is_ssl = _is_ssl_error(e)
        is_timeout = isinstance(e, TimeoutError) or "timed out" in str(e).lower()
        
        if is_ssl or is_timeout:
            reason = "SSL error" if is_ssl else "timeout"
            logger.warning(
                f"[HTTPClient] Python {reason} (urllib) for {url}: {e}. "
                f"Falling back to curl."
            )
            result = _curl_get(url, headers=headers, timeout=timeout)
            result["method"] = "curl_fallback"
            # Adapt to urllib-style return
            result["data"] = result.pop("body", "")
            return result
        else:
            return {
                "success": False,
                "status_code": 0,
                "data": "",
                "error": f"Request error: {str(e)}",
                "method": "urllib",
            }
