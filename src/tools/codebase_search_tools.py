"""
Codebase Search Tools — grep, glob, ls for code exploration.

Hybrid strategy: prefer fast CLI tools (ripgrep, fd) via subprocess,
fall back to pure-Python implementations when CLI is unavailable.

All functions return plain dicts; the Tool wrapper in tool.py handles
JSON serialization, timeout, and output truncation.
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.tool import Tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Directories excluded by default (matches common .gitignore patterns)
DEFAULT_EXCLUDES = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".eggs", "*.egg-info",
}

SUBPROCESS_TIMEOUT = 55  # default; overridden by configure_subprocess_timeout()


def configure_subprocess_timeout(timeout: int) -> None:
    """Set subprocess timeout from configuration.

    Called once at startup after config is loaded.
    """
    global SUBPROCESS_TIMEOUT
    SUBPROCESS_TIMEOUT = timeout


def _which(name: str) -> Optional[str]:
    """Return path to *name* if it exists on PATH, else None."""
    return shutil.which(name)


def _safe_decode(b: bytes, limit: int = 0) -> str:
    text = b.decode("utf-8", errors="replace")
    if limit and len(text) > limit:
        text = text[:limit]
    return text


# ---------------------------------------------------------------------------
# grep_search
# ---------------------------------------------------------------------------

def grep_search(
    query: str,
    search_path: str = ".",
    includes: Optional[List[str]] = None,
    case_sensitive: bool = False,
    max_results: int = 50,
    context_lines: int = 0,
    fixed_strings: bool = False,
) -> Dict[str, Any]:
    """
    Search for a regex/fixed-string pattern inside files.

    Prefers ripgrep (rg) for speed; falls back to Python re module.

    Args:
        query: Regex pattern (or literal if fixed_strings=True)
        search_path: Directory or file to search
        includes: Glob patterns to filter files (e.g. ["*.py", "*.js"])
        case_sensitive: Case-sensitive matching (default: False)
        max_results: Max matching lines to return
        context_lines: Lines of context around each match (0 = none)
        fixed_strings: Treat query as literal string, not regex

    Returns:
        Dict with matches list and metadata
    """
    search_path = os.path.abspath(search_path)
    if not os.path.exists(search_path):
        return {"error": f"Path not found: {search_path}"}

    rg = _which("rg")
    if rg:
        return _grep_ripgrep(
            rg, query, search_path, includes,
            case_sensitive, max_results, context_lines, fixed_strings,
        )
    return _grep_python(
        query, search_path, includes,
        case_sensitive, max_results, context_lines, fixed_strings,
    )


def _grep_ripgrep(
    rg: str, query: str, search_path: str,
    includes: Optional[List[str]], case_sensitive: bool,
    max_results: int, context_lines: int, fixed_strings: bool,
) -> Dict[str, Any]:
    cmd: List[str] = [rg, "--json"]
    if not case_sensitive:
        cmd.append("-i")
    if fixed_strings:
        cmd.append("-F")
    if context_lines > 0:
        cmd.extend(["-C", str(context_lines)])
    # max results: rg uses -m per file; we cap total results post-hoc
    cmd.extend(["-m", str(max_results)])
    if includes:
        for pattern in includes:
            cmd.extend(["-g", pattern])
    # default excludes
    for exc in DEFAULT_EXCLUDES:
        cmd.extend(["-g", f"!{exc}"])
    cmd.extend(["--", query, search_path])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"ripgrep timed out after {SUBPROCESS_TIMEOUT}s", "tool": "grep_search"}
    except FileNotFoundError:
        return _grep_python(
            query, search_path, includes,
            not ("-i" in cmd), max_results, context_lines, fixed_strings,
        )

    import json as _json
    matches: List[Dict[str, Any]] = []
    files_seen: set = set()
    for line in proc.stdout.splitlines():
        if len(matches) >= max_results:
            break
        try:
            obj = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if obj.get("type") == "match":
            data = obj["data"]
            path_text = data.get("path", {}).get("text", "")
            line_number = data.get("line_number", 0)
            line_text = data.get("lines", {}).get("text", "").rstrip("\n")
            matches.append({
                "file": path_text,
                "line": line_number,
                "text": line_text[:500],
            })
            files_seen.add(path_text)

    return {
        "query": query,
        "search_path": search_path,
        "backend": "ripgrep",
        "match_count": len(matches),
        "file_count": len(files_seen),
        "truncated": len(matches) >= max_results,
        "matches": matches,
    }


def _grep_python(
    query: str, search_path: str,
    includes: Optional[List[str]], case_sensitive: bool,
    max_results: int, context_lines: int, fixed_strings: bool,
) -> Dict[str, Any]:
    flags = 0 if case_sensitive else re.IGNORECASE
    if fixed_strings:
        pattern = re.compile(re.escape(query), flags)
    else:
        try:
            pattern = re.compile(query, flags)
        except re.error as e:
            return {"error": f"Invalid regex: {e}"}

    include_patterns = None
    if includes:
        from fnmatch import fnmatch
        include_patterns = includes

    matches: List[Dict[str, Any]] = []
    files_seen: set = set()

    def _should_exclude(name: str) -> bool:
        return name in DEFAULT_EXCLUDES

    def _file_matches_include(fname: str) -> bool:
        if not include_patterns:
            return True
        from fnmatch import fnmatch as _fn
        return any(_fn(fname, p) for p in include_patterns)

    target = Path(search_path)
    file_iter = [target] if target.is_file() else sorted(target.rglob("*"))

    for fpath in file_iter:
        if len(matches) >= max_results:
            break
        if fpath.is_dir():
            continue
        # check excludes on any parent
        if any(_should_exclude(part) for part in fpath.parts):
            continue
        if not _file_matches_include(fpath.name):
            continue
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    if len(matches) >= max_results:
                        break
                    if pattern.search(line):
                        matches.append({
                            "file": str(fpath),
                            "line": i,
                            "text": line.rstrip("\n")[:500],
                        })
                        files_seen.add(str(fpath))
        except (OSError, UnicodeDecodeError):
            continue

    return {
        "query": query,
        "search_path": search_path,
        "backend": "python-re",
        "match_count": len(matches),
        "file_count": len(files_seen),
        "truncated": len(matches) >= max_results,
        "matches": matches,
    }


# ---------------------------------------------------------------------------
# glob_search
# ---------------------------------------------------------------------------

def glob_search(
    pattern: str,
    search_directory: str = ".",
    type_filter: str = "any",
    max_depth: Optional[int] = None,
    excludes: Optional[List[str]] = None,
    max_results: int = 200,
) -> Dict[str, Any]:
    """
    Find files/directories by name pattern (glob).

    Prefers fd for speed; falls back to pathlib.

    Args:
        pattern: Glob pattern (e.g. "*.py", "test_*")
        search_directory: Root directory to search
        type_filter: "file", "directory", or "any"
        max_depth: Max directory depth (None = unlimited)
        excludes: Additional directory names to exclude
        max_results: Max entries to return

    Returns:
        Dict with matching paths and metadata
    """
    search_directory = os.path.abspath(search_directory)
    if not os.path.isdir(search_directory):
        return {"error": f"Directory not found: {search_directory}"}

    fd = _which("fd") or _which("fdfind")
    if fd:
        return _glob_fd(
            fd, pattern, search_directory, type_filter,
            max_depth, excludes, max_results,
        )
    return _glob_python(
        pattern, search_directory, type_filter,
        max_depth, excludes, max_results,
    )


def _glob_fd(
    fd: str, pattern: str, search_directory: str,
    type_filter: str, max_depth: Optional[int],
    excludes: Optional[List[str]], max_results: int,
) -> Dict[str, Any]:
    cmd: List[str] = [fd, "--glob", pattern]
    if type_filter == "file":
        cmd.extend(["-t", "f"])
    elif type_filter == "directory":
        cmd.extend(["-t", "d"])
    if max_depth is not None:
        cmd.extend(["--max-depth", str(max_depth)])
    all_excludes = DEFAULT_EXCLUDES | set(excludes or [])
    for exc in all_excludes:
        cmd.extend(["-E", exc])
    cmd.extend(["--max-results", str(max_results)])
    cmd.append(search_directory)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"fd timed out after {SUBPROCESS_TIMEOUT}s", "tool": "glob_search"}
    except FileNotFoundError:
        return _glob_python(
            pattern, search_directory, type_filter,
            max_depth, excludes, max_results,
        )

    lines = _safe_decode(proc.stdout).strip().splitlines()
    results = [l for l in lines if l][:max_results]

    return {
        "pattern": pattern,
        "search_directory": search_directory,
        "backend": "fd",
        "count": len(results),
        "truncated": len(results) >= max_results,
        "results": results,
    }


def _glob_python(
    pattern: str, search_directory: str,
    type_filter: str, max_depth: Optional[int],
    excludes: Optional[List[str]], max_results: int,
) -> Dict[str, Any]:
    from fnmatch import fnmatch

    all_excludes = DEFAULT_EXCLUDES | set(excludes or [])
    root = Path(search_directory)
    results: List[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        depth = len(rel.parts)
        if max_depth is not None and depth > max_depth:
            dirnames.clear()
            continue
        # prune excluded dirs
        dirnames[:] = [d for d in dirnames if d not in all_excludes]

        entries: List[str] = []
        if type_filter in ("any", "directory"):
            entries.extend(d for d in dirnames if fnmatch(d, pattern))
        if type_filter in ("any", "file"):
            entries.extend(f for f in filenames if fnmatch(f, pattern))

        for name in entries:
            if len(results) >= max_results:
                break
            results.append(str(Path(dirpath) / name))
        if len(results) >= max_results:
            break

    return {
        "pattern": pattern,
        "search_directory": search_directory,
        "backend": "python-pathlib",
        "count": len(results),
        "truncated": len(results) >= max_results,
        "results": results,
    }


# ---------------------------------------------------------------------------
# ls_directory
# ---------------------------------------------------------------------------

def ls_directory(
    path: str = ".",
    max_entries: int = 100,
    show_hidden: bool = False,
) -> Dict[str, Any]:
    """
    List immediate contents of a directory (non-recursive).

    Args:
        path: Directory path
        max_entries: Max entries to return (default: 100)
        show_hidden: Include hidden files/dirs (default: False)

    Returns:
        Dict with directory entries and metadata
    """
    target = os.path.abspath(path)
    if not os.path.isdir(target):
        return {"error": f"Not a directory: {target}"}

    entries: List[Dict[str, Any]] = []
    try:
        with os.scandir(target) as it:
            for entry in sorted(it, key=lambda e: e.name):
                if len(entries) >= max_entries:
                    break
                if not show_hidden and entry.name.startswith("."):
                    continue
                try:
                    stat = entry.stat(follow_symlinks=False)
                    entries.append({
                        "name": entry.name,
                        "type": "dir" if entry.is_dir(follow_symlinks=False) else "file",
                        "size": stat.st_size if not entry.is_dir(follow_symlinks=False) else None,
                    })
                except OSError:
                    entries.append({
                        "name": entry.name,
                        "type": "unknown",
                        "size": None,
                    })
    except PermissionError as e:
        return {"error": str(e), "path": target}

    return {
        "path": target,
        "count": len(entries),
        "truncated": len(entries) >= max_entries,
        "entries": entries,
    }


# ---------------------------------------------------------------------------
# Tool Definitions
# ---------------------------------------------------------------------------

GREP_SEARCH_TOOL = Tool(
    name="grep_search",
    description=(
        "Search for a regex or fixed-string pattern inside files. "
        "Uses ripgrep (rg) if available, otherwise falls back to Python re. "
        "Returns matching file paths, line numbers, and line text. "
        "Use 'includes' to filter by file extension (e.g. ['*.py']). "
        "Prefer this over run_command with grep."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Regex pattern or literal string to search for"},
            "search_path": {"type": "string", "description": "Directory or file to search (default: '.')"},
            "includes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Glob patterns to filter files (e.g. ['*.py', '*.js'])"
            },
            "case_sensitive": {"type": "boolean", "description": "Case-sensitive matching (default: false)"},
            "max_results": {"type": "integer", "description": "Max matching lines (default: 50)"},
            "context_lines": {"type": "integer", "description": "Lines of context around each match (default: 0)"},
            "fixed_strings": {"type": "boolean", "description": "Treat query as literal string, not regex (default: false)"},
        },
        "required": ["query"],
    },
    func=grep_search,
    timeout_seconds=60,
    max_output_chars=30000,
    is_readonly=True,
)

GLOB_SEARCH_TOOL = Tool(
    name="glob_search",
    description=(
        "Find files and directories by name pattern (glob). "
        "Uses fd if available, falls back to Python pathlib. "
        "Auto-excludes .git, node_modules, __pycache__, etc."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern (e.g. '*.py', 'test_*', '*.tsx')"},
            "search_directory": {"type": "string", "description": "Root directory (default: '.')"},
            "type_filter": {
                "type": "string",
                "enum": ["file", "directory", "any"],
                "description": "Filter by type (default: 'any')",
            },
            "max_depth": {"type": "integer", "description": "Max directory depth (optional)"},
            "excludes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Additional directory names to exclude",
            },
            "max_results": {"type": "integer", "description": "Max entries to return (default: 200)"},
        },
        "required": ["pattern"],
    },
    func=glob_search,
    timeout_seconds=30,
    max_output_chars=20000,
    is_readonly=True,
)

LS_DIRECTORY_TOOL = Tool(
    name="ls_directory",
    description=(
        "List immediate contents of a directory (non-recursive). "
        "Returns name, type (file/dir), and size for each entry. "
        "Quick overview before deeper exploration."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path (default: '.')"},
            "max_entries": {"type": "integer", "description": "Max entries (default: 100)"},
            "show_hidden": {"type": "boolean", "description": "Include hidden files (default: false)"},
        },
        "required": [],
    },
    func=ls_directory,
    timeout_seconds=10,
    max_output_chars=10000,
    is_readonly=True,
)

CODEBASE_TOOLS = [
    GREP_SEARCH_TOOL,
    GLOB_SEARCH_TOOL,
    LS_DIRECTORY_TOOL,
]
