"""
File Tools Module - File system operations.

Provides tools for:
- Reading files (text, binary, structured)
- Writing files
- Listing directories
- Searching within files
- Moving, copying, deleting files
- Getting file metadata
"""

import os
import shutil
import fnmatch
import hashlib
import logging
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Optional

from ..core.tool import Tool
from ..core.edit_manager import (
    DiffParser,
    DiffParseError,
    EditApplier,
    get_edit_guard,
)

logger = logging.getLogger(__name__)

_SAFE_BASE_DIRS: List[str] = []


def _resolve_safe_path(path: str) -> Path:
    """Resolve path and validate it is within safe base directories if configured."""
    resolved = Path(path).expanduser().resolve()
    if _SAFE_BASE_DIRS:
        for base in _SAFE_BASE_DIRS:
            try:
                resolved.relative_to(Path(base).resolve())
                return resolved
            except ValueError:
                continue
        raise PermissionError(f"Path '{path}' is outside allowed directories")
    return resolved


def read_file(path: str, encoding: str = "utf-8", max_chars: int = 50000) -> Dict[str, Any]:
    """
    Read a text file and return its contents.

    Args:
        path: File path to read
        encoding: Text encoding (default utf-8)
        max_chars: Maximum characters to return (default 50000)

    Returns:
        Dictionary with content and metadata
    """
    try:
        resolved = _resolve_safe_path(path)
        if not resolved.exists():
            return {"error": f"File not found: {path}"}
        if not resolved.is_file():
            return {"error": f"Path is not a file: {path}"}

        stat = resolved.stat()
        with open(resolved, "r", encoding=encoding, errors="replace") as f:
            content = f.read(max_chars)

        truncated = stat.st_size > max_chars

        # Register read with FileEditingGuard for read-before-edit enforcement
        get_edit_guard().record_read(str(resolved), content)

        return {
            "path": str(resolved),
            "size_bytes": stat.st_size,
            "content": content,
            "truncated": truncated,
            "encoding": encoding,
            "lines": content.count("\n") + 1
        }
    except PermissionError as e:
        return {"error": str(e), "path": path}
    except Exception as e:
        logger.exception("[ReadFile] Failed")
        return {"error": str(e), "path": path}


def write_file(path: str, content: str, encoding: str = "utf-8", overwrite: bool = True) -> Dict[str, Any]:
    """
    Write content to a text file.

    Args:
        path: Destination file path
        content: Text content to write
        encoding: Text encoding (default utf-8)
        overwrite: Whether to overwrite existing file (default True)

    Returns:
        Dictionary with write result
    """
    try:
        resolved = _resolve_safe_path(path)
        if resolved.exists() and not overwrite:
            return {"error": f"File already exists: {path}. Set overwrite=true to replace."}

        resolved.parent.mkdir(parents=True, exist_ok=True)
        with open(resolved, "w", encoding=encoding) as f:
            f.write(content)

        return {
            "path": str(resolved),
            "bytes_written": resolved.stat().st_size,
            "lines": content.count("\n") + 1,
            "success": True
        }
    except PermissionError as e:
        return {"error": str(e), "path": path}
    except Exception as e:
        logger.exception("[WriteFile] Failed")
        return {"error": str(e), "path": path}


def append_file(path: str, content: str, encoding: str = "utf-8") -> Dict[str, Any]:
    """
    Append content to an existing file (creates if missing).

    Args:
        path: File path
        content: Content to append
        encoding: Text encoding

    Returns:
        Dictionary with result
    """
    try:
        resolved = _resolve_safe_path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with open(resolved, "a", encoding=encoding) as f:
            f.write(content)
        return {
            "path": str(resolved),
            "size_bytes": resolved.stat().st_size,
            "success": True
        }
    except PermissionError as e:
        return {"error": str(e), "path": path}
    except Exception as e:
        logger.exception("[AppendFile] Failed")
        return {"error": str(e), "path": path}


_EXCLUDE_DIRS_DEFAULT = {'.git', '__pycache__', '.venv', 'venv', 'node_modules', '.pytest_cache', '.mypy_cache', 'dist', 'build'}


def list_directory(path: str, pattern: str = "*", recursive: bool = False, include_hidden: bool = False, exclude_dirs: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    List files and directories at a path.

    Args:
        path: Directory path to list
        pattern: Glob pattern filter (default '*')
        recursive: Whether to list recursively
        include_hidden: Include hidden files (starting with '.')
        exclude_dirs: List of directory names to exclude when recursive (default: .git, __pycache__, venv, node_modules, etc.)

    Returns:
        Dictionary with entries list (max 500 entries returned)
    """
    try:
        resolved = _resolve_safe_path(path)
        if not resolved.exists():
            return {"error": f"Path not found: {path}"}
        if not resolved.is_dir():
            return {"error": f"Path is not a directory: {path}"}

        exclusions = _EXCLUDE_DIRS_DEFAULT.copy()
        if exclude_dirs:
            exclusions.update(exclude_dirs)

        entries = []
        glob_fn = resolved.rglob if recursive else resolved.glob
        for item in sorted(glob_fn(pattern)):
            name = item.name
            if not include_hidden and name.startswith("."):
                continue
            # Skip if any part of the path is in exclusions (for recursive)
            if recursive and any(part in exclusions for part in item.relative_to(resolved).parts[:-1] if part != name):
                continue
            stat = item.stat()
            entry = {
                "name": name,
                "path": str(item),
                "type": "directory" if item.is_dir() else "file",
            }
            if item.is_file():
                entry["size_bytes"] = stat.st_size
            entries.append(entry)
            if len(entries) >= 500:
                break

        return {
            "path": str(resolved),
            "pattern": pattern,
            "recursive": recursive,
            "count": len(entries),
            "truncated": len(entries) >= 500,
            "entries": entries
        }
    except PermissionError as e:
        return {"error": str(e), "path": path}
    except Exception as e:
        logger.exception("[ListDirectory] Failed")
        return {"error": str(e), "path": path}


def search_in_files(directory: str, query: str, pattern: str = "*.txt", recursive: bool = True, max_results: int = 50) -> Dict[str, Any]:
    """
    Search for text inside files in a directory.

    Args:
        directory: Directory to search
        query: Text string to search for
        pattern: File name pattern to filter (e.g. '*.py')
        recursive: Search subdirectories
        max_results: Max number of matching lines to return

    Returns:
        Dictionary with search results
    """
    try:
        resolved = _resolve_safe_path(directory)
        if not resolved.is_dir():
            return {"error": f"Directory not found: {directory}"}

        matches = []
        glob_fn = resolved.rglob if recursive else resolved.glob
        for filepath in glob_fn(pattern):
            if not filepath.is_file():
                continue
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    for lineno, line in enumerate(f, 1):
                        if query.lower() in line.lower():
                            matches.append({
                                "file": str(filepath),
                                "line": lineno,
                                "content": line.rstrip()
                            })
                            if len(matches) >= max_results:
                                break
            except Exception:
                continue
            if len(matches) >= max_results:
                break

        return {
            "directory": str(resolved),
            "query": query,
            "pattern": pattern,
            "total_matches": len(matches),
            "results": matches
        }
    except PermissionError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.exception("[SearchFiles] Failed")
        return {"error": str(e)}


def move_file(source: str, destination: str, overwrite: bool = False) -> Dict[str, Any]:
    """
    Move or rename a file or directory.

    Args:
        source: Source path
        destination: Destination path
        overwrite: Overwrite destination if exists

    Returns:
        Dictionary with result
    """
    try:
        src = _resolve_safe_path(source)
        dst = _resolve_safe_path(destination)

        if not src.exists():
            return {"error": f"Source not found: {source}"}
        if dst.exists() and not overwrite:
            return {"error": f"Destination exists: {destination}. Set overwrite=true to replace."}

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return {"source": str(src), "destination": str(dst), "success": True}
    except PermissionError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.exception("[MoveFile] Failed")
        return {"error": str(e)}


def copy_file(source: str, destination: str, overwrite: bool = False) -> Dict[str, Any]:
    """
    Copy a file or directory.

    Args:
        source: Source path
        destination: Destination path
        overwrite: Overwrite destination if exists

    Returns:
        Dictionary with result
    """
    try:
        src = _resolve_safe_path(source)
        dst = _resolve_safe_path(destination)

        if not src.exists():
            return {"error": f"Source not found: {source}"}
        if dst.exists() and not overwrite:
            return {"error": f"Destination exists: {destination}. Set overwrite=true."}

        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(str(dst))
            shutil.copytree(str(src), str(dst))
        else:
            shutil.copy2(str(src), str(dst))

        return {"source": str(src), "destination": str(dst), "success": True}
    except PermissionError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.exception("[CopyFile] Failed")
        return {"error": str(e)}


def delete_file(path: str, recursive: bool = False) -> Dict[str, Any]:
    """
    Delete a file or directory.

    Args:
        path: Path to delete
        recursive: Delete directory recursively (required for non-empty dirs)

    Returns:
        Dictionary with result
    """
    try:
        resolved = _resolve_safe_path(path)
        if not resolved.exists():
            return {"error": f"Path not found: {path}"}

        if resolved.is_dir():
            if recursive:
                shutil.rmtree(str(resolved))
            else:
                resolved.rmdir()
        else:
            resolved.unlink()

        return {"path": str(resolved), "success": True}
    except OSError as e:
        return {"error": str(e), "path": path}
    except Exception as e:
        logger.exception("[DeleteFile] Failed")
        return {"error": str(e), "path": path}


def get_file_info(path: str) -> Dict[str, Any]:
    """
    Get metadata about a file or directory.

    Args:
        path: File or directory path

    Returns:
        Dictionary with metadata
    """
    try:
        resolved = _resolve_safe_path(path)
        if not resolved.exists():
            return {"error": f"Path not found: {path}"}

        stat = resolved.stat()
        info: Dict[str, Any] = {
            "path": str(resolved),
            "name": resolved.name,
            "type": "directory" if resolved.is_dir() else "file",
            "size_bytes": stat.st_size,
            "extension": resolved.suffix,
            "parent": str(resolved.parent),
        }

        if resolved.is_file():
            with open(resolved, "rb") as f:
                data = f.read(65536)
            info["md5"] = hashlib.md5(data).hexdigest()
            info["is_binary"] = b"\x00" in data

        return info
    except PermissionError as e:
        return {"error": str(e), "path": path}
    except Exception as e:
        logger.exception("[GetFileInfo] Failed")
        return {"error": str(e), "path": path}


def create_directory(path: str) -> Dict[str, Any]:
    """
    Create a directory (and parents if needed).

    Args:
        path: Directory path to create

    Returns:
        Dictionary with result
    """
    try:
        resolved = _resolve_safe_path(path)
        resolved.mkdir(parents=True, exist_ok=True)
        return {"path": str(resolved), "success": True}
    except PermissionError as e:
        return {"error": str(e), "path": path}
    except Exception as e:
        logger.exception("[CreateDirectory] Failed")
        return {"error": str(e), "path": path}


def edit_file(
    file_path: str,
    diff: str,
    replace_all: bool = False,
    encoding: str = "utf-8",
) -> Dict[str, Any]:
    """
    Edit a file using SEARCH/REPLACE diff blocks.

    Requires the file to have been read via read_file first
    (enforced by FileEditingGuard).

    Args:
        file_path: Path to the file to edit
        diff: SEARCH/REPLACE formatted diff text
        replace_all: Replace all matches (default: first match only)
        encoding: File encoding (default: utf-8)

    Returns:
        Dictionary with edit result and diagnostics
    """
    try:
        resolved = _resolve_safe_path(file_path)
        resolved_str = str(resolved)

        # 1. Read-before-edit guard check
        guard = get_edit_guard()
        allowed, reason = guard.validate_edit(resolved_str)
        if not allowed:
            return {"error": reason, "error_type": "edit_denied", "path": file_path}

        # 2. Parse SEARCH/REPLACE blocks
        try:
            blocks = DiffParser.parse(diff)
        except DiffParseError as e:
            return {
                "error": f"Diff parse error: {e}",
                "error_type": "parse_error",
                "path": file_path,
            }

        # 3. Read current file content (or handle create-new-file)
        if resolved.exists():
            with open(resolved, "r", encoding=encoding, errors="replace") as f:
                current_content = f.read()
        else:
            # File does not exist — only valid for empty-SEARCH create semantics
            if len(blocks) == 1 and not blocks[0].search:
                current_content = ""
            else:
                return {
                    "error": f"File not found: {file_path}",
                    "error_type": "file_not_found",
                    "path": file_path,
                }

        # 4. Verify content freshness (detect external modifications)
        if resolved.exists() and not guard.verify_freshness(resolved_str, current_content):
            return {
                "error": (
                    f"File '{file_path}' has been modified externally since "
                    f"last read. Please re-read the file before editing."
                ),
                "error_type": "content_stale",
                "path": file_path,
                "suggestion": "Call read_file to refresh the cached content.",
            }

        # 5. Apply edits
        result = EditApplier.apply(current_content, blocks, replace_all=replace_all)

        if not result.success:
            error_details = []
            if result.failed_edits:
                for fe in result.failed_edits:
                    detail = {
                        "block_index": fe.original_index + 1,
                        "reason": fe.reason,
                    }
                    if fe.suggestions:
                        detail["suggestions"] = fe.suggestions
                    error_details.append(detail)
            return {
                "error": "Some edit blocks failed to apply",
                "error_type": "apply_failed",
                "path": file_path,
                "failed_edits": error_details,
                "hint": (
                    "Ensure SEARCH blocks exactly match file content "
                    "(including whitespace and indentation). "
                    "Use more surrounding context to make matches unique."
                ),
            }

        # 6. Atomic write: temp file -> os.replace()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(resolved.parent),
            prefix=f".{resolved.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding=encoding) as tmp_f:
                tmp_f.write(result.new_content)
            os.replace(tmp_path, str(resolved))
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        # 7. Update guard cache with new content
        guard.record_read(resolved_str, result.new_content)

        response: Dict[str, Any] = {
            "path": resolved_str,
            "success": True,
            "applied_edits": result.applied_count,
            "file_size_bytes": resolved.stat().st_size,
            "lines": result.new_content.count("\n") + 1,
        }
        if result.snippet_after:
            response["snippet_after"] = result.snippet_after
        return response

    except PermissionError as e:
        return {"error": str(e), "error_type": "permission_error", "path": file_path}
    except Exception as e:
        logger.exception("[EditFile] Failed")
        return {"error": str(e), "error_type": "unexpected_error", "path": file_path}


# Tool Definitions
READ_FILE_TOOL = Tool(
    name="read_file",
    description=(
        "Read contents of a text file. Returns file content with metadata. "
        "Supports any text encoding. Truncates at max_chars."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or relative file path"},
            "encoding": {"type": "string", "description": "Text encoding (default: utf-8)"},
            "max_chars": {"type": "integer", "description": "Max characters to read (default: 50000)"}
        },
        "required": ["path"]
    },
    func=read_file,
    timeout_seconds=30,
    max_output_chars=60000,
    is_readonly=True,
)

WRITE_FILE_TOOL = Tool(
    name="write_file",
    description=(
        "Write text content to a file. Creates parent directories if needed. "
        "Set overwrite=false to protect existing files."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Destination file path"},
            "content": {"type": "string", "description": "Text content to write"},
            "encoding": {"type": "string", "description": "Text encoding (default: utf-8)"},
            "overwrite": {"type": "boolean", "description": "Overwrite existing file (default: true)"}
        },
        "required": ["path", "content"]
    },
    func=write_file,
    timeout_seconds=30,
    max_output_chars=5000,
)

APPEND_FILE_TOOL = Tool(
    name="append_file",
    description="Append text content to an existing file. Creates the file if it does not exist.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path"},
            "content": {"type": "string", "description": "Content to append"},
            "encoding": {"type": "string", "description": "Text encoding (default: utf-8)"}
        },
        "required": ["path", "content"]
    },
    func=append_file,
    timeout_seconds=30,
    max_output_chars=5000,
)

LIST_DIRECTORY_TOOL = Tool(
    name="list_directory",
    description=(
        "List files and directories. Supports glob patterns (e.g. '*.py') "
        "and recursive listing. Auto-excludes .git, __pycache__, venv, node_modules to keep results small. "
        "Max 500 entries returned."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path to list"},
            "pattern": {"type": "string", "description": "Glob pattern filter (default: '*')"},
            "recursive": {"type": "boolean", "description": "List recursively (default: false). When true, excludes .git, node_modules, etc."},
            "include_hidden": {"type": "boolean", "description": "Include hidden files (default: false)"},
            "exclude_dirs": {"type": "array", "items": {"type": "string"}, "description": "Additional directory names to exclude when recursive (optional)"}
        },
        "required": ["path"]
    },
    func=list_directory,
    timeout_seconds=30,
    max_output_chars=30000,
    is_readonly=True,
)

SEARCH_IN_FILES_TOOL = Tool(
    name="search_in_files",
    description=(
        "Search for text inside files in a directory. Returns matching lines with file paths and line numbers. "
        "Supports file name pattern filtering."
    ),
    parameters={
        "type": "object",
        "properties": {
            "directory": {"type": "string", "description": "Directory to search in"},
            "query": {"type": "string", "description": "Text string to search for"},
            "pattern": {"type": "string", "description": "File name pattern (e.g. '*.py', default: '*.txt')"},
            "recursive": {"type": "boolean", "description": "Search subdirectories (default: true)"},
            "max_results": {"type": "integer", "description": "Max matching lines to return (default: 50)"}
        },
        "required": ["directory", "query"]
    },
    func=search_in_files,
    timeout_seconds=60,
    max_output_chars=30000,
    is_readonly=True,
)

MOVE_FILE_TOOL = Tool(
    name="move_file",
    description="Move or rename a file or directory to a new location.",
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Source path"},
            "destination": {"type": "string", "description": "Destination path"},
            "overwrite": {"type": "boolean", "description": "Overwrite destination if exists (default: false)"}
        },
        "required": ["source", "destination"]
    },
    func=move_file,
    timeout_seconds=30,
    max_output_chars=5000,
)

COPY_FILE_TOOL = Tool(
    name="copy_file",
    description="Copy a file or directory to a new location.",
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Source path"},
            "destination": {"type": "string", "description": "Destination path"},
            "overwrite": {"type": "boolean", "description": "Overwrite destination if exists (default: false)"}
        },
        "required": ["source", "destination"]
    },
    func=copy_file,
    timeout_seconds=60,
    max_output_chars=5000,
)

DELETE_FILE_TOOL = Tool(
    name="delete_file",
    description="Delete a file or directory. Set recursive=true to delete non-empty directories.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to delete"},
            "recursive": {"type": "boolean", "description": "Delete directory recursively (default: false)"}
        },
        "required": ["path"]
    },
    func=delete_file,
    timeout_seconds=30,
    max_output_chars=5000,
)

GET_FILE_INFO_TOOL = Tool(
    name="get_file_info",
    description="Get metadata about a file or directory: size, type, extension, MD5 hash.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File or directory path"}
        },
        "required": ["path"]
    },
    func=get_file_info,
    timeout_seconds=15,
    max_output_chars=5000,
    is_readonly=True,
)

CREATE_DIRECTORY_TOOL = Tool(
    name="create_directory",
    description="Create a directory and all necessary parent directories.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path to create"}
        },
        "required": ["path"]
    },
    func=create_directory,
    timeout_seconds=15,
    max_output_chars=5000,
)

EDIT_FILE_TOOL = Tool(
    name="edit_file",
    description=(
        "Perform precise code edits using SEARCH/REPLACE blocks. "
        "Rules: (1) You MUST call read_file first before editing any file. "
        "(2) SEARCH blocks must exactly match file content including indentation. "
        "(3) Each SEARCH must uniquely match one location unless replace_all=true. "
        "(4) For small files (<100 lines) or large rewrites (>50%%), prefer write_file. "
        "Diff format:\n"
        "------- SEARCH\n"
        "[exact original code]\n"
        "=======\n"
        "[replacement code]\n"
        "+++++++ REPLACE\n"
        "Multiple SEARCH/REPLACE blocks supported in a single call."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "File path to edit (must have been read via read_file first)",
            },
            "diff": {
                "type": "string",
                "description": (
                    "SEARCH/REPLACE formatted diff text. Format:\n"
                    "------- SEARCH\n"
                    "[exact original code]\n"
                    "=======\n"
                    "[replacement code]\n"
                    "+++++++ REPLACE"
                ),
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all matches of each SEARCH block (default: false)",
            },
            "encoding": {
                "type": "string",
                "description": "File encoding (default: utf-8)",
            },
        },
        "required": ["file_path", "diff"],
    },
    func=edit_file,
    timeout_seconds=30,
    max_output_chars=10000,
)

FILE_TOOLS = [
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
]
