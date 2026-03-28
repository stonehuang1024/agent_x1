"""
Edit Manager Module - Core editing infrastructure for file modifications.

Provides:
- DiffParser: Parse SEARCH/REPLACE formatted diff blocks
- SearchEngine: Exact string matching with near-match suggestions
- EditApplier: Order-invariant multi-block edit application
- FileEditingGuard: Session-level read-before-edit enforcement

Architecture:
    LLM edit_file call
    └── file_tools.edit_file()
        ├── FileEditingGuard.validate_edit()   (read-before-edit check)
        ├── DiffParser.parse()                 (parse SEARCH/REPLACE blocks)
        ├── EditApplier.apply()                (apply edits to content)
        │   └── SearchEngine.find_exact()      (locate each block)
        └── Atomic write to disk
"""

import logging
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclass
class ReplaceBlock:
    """A single SEARCH/REPLACE edit block."""
    search: str
    replace: str


@dataclass
class MatchResult:
    """Result of searching for a SEARCH block in file content."""
    found: bool
    position: int = -1
    end_position: int = -1
    match_count: int = 0
    suggestions: Optional[List[str]] = None


@dataclass
class AppliedEdit:
    """Record of a successfully applied edit."""
    original_index: int
    position: int
    length: int


@dataclass
class FailedEdit:
    """Record of a failed edit with diagnostics."""
    original_index: int
    reason: str
    suggestions: Optional[List[str]] = None


@dataclass
class EditResult:
    """Result of applying edits to file content."""
    success: bool
    new_content: str
    applied_count: int = 0
    failed_edits: Optional[List[FailedEdit]] = None
    snippet_after: Optional[str] = None


class DiffParseError(ValueError):
    """Raised when diff content has invalid SEARCH/REPLACE format."""

    def __init__(self, message: str, line_number: Optional[int] = None):
        self.line_number = line_number
        if line_number is not None:
            message = f"Line {line_number}: {message}"
        super().__init__(message)


# ---------------------------------------------------------------------------
# DiffParser
# ---------------------------------------------------------------------------

class DiffParser:
    """
    Parse SEARCH/REPLACE formatted diff text into ReplaceBlock list.

    Supported format:
        ------- SEARCH
        [original code]
        =======
        [replacement code]
        +++++++ REPLACE

    Features:
    - State machine parser (idle → search → replace → idle)
    - Supports marker variants: '------- SEARCH', '<<<<<<< SEARCH'
    - Auto-strips Markdown code fences (```diff ... ```)
    - Empty SEARCH block = create-new-file semantics (single block only)
    - Raises DiffParseError on malformed input
    """

    _SEARCH_MARKERS = ("------- SEARCH", "<<<<<<< SEARCH")
    _SEPARATOR_MARKERS = ("=======",)
    _REPLACE_MARKERS = ("+++++++ REPLACE", ">>>>>>> REPLACE")

    @classmethod
    def parse(cls, diff_content: str) -> List[ReplaceBlock]:
        """
        Parse diff content into a list of ReplaceBlock.

        Args:
            diff_content: SEARCH/REPLACE formatted text

        Returns:
            List of ReplaceBlock instances

        Raises:
            DiffParseError: If the format is invalid
        """
        if not diff_content or not diff_content.strip():
            raise DiffParseError("Empty diff content")

        cleaned = cls._strip_code_fences(diff_content)
        lines = cleaned.split("\n")
        blocks: List[ReplaceBlock] = []

        state = "idle"  # idle | search | replace
        search_buffer: List[str] = []
        replace_buffer: List[str] = []

        for i, line in enumerate(lines, start=1):
            stripped = line.strip()

            if cls._is_search_marker(stripped):
                if state != "idle":
                    raise DiffParseError(
                        f"Unexpected SEARCH marker (current state: {state})",
                        line_number=i,
                    )
                state = "search"
                search_buffer = []

            elif cls._is_separator(stripped):
                if state != "search":
                    raise DiffParseError(
                        "Unexpected separator '=======' (missing SEARCH block)",
                        line_number=i,
                    )
                state = "replace"
                replace_buffer = []

            elif cls._is_replace_marker(stripped):
                if state != "replace":
                    raise DiffParseError(
                        "Unexpected REPLACE marker (missing REPLACE content)",
                        line_number=i,
                    )
                blocks.append(ReplaceBlock(
                    search="\n".join(search_buffer),
                    replace="\n".join(replace_buffer),
                ))
                state = "idle"

            else:
                if state == "search":
                    search_buffer.append(line)
                elif state == "replace":
                    replace_buffer.append(line)
                # idle state: ignore lines between blocks

        if state != "idle":
            raise DiffParseError(
                f"Unclosed diff block (state: {state}). "
                "Missing final '+++++++ REPLACE' marker."
            )

        if not blocks:
            raise DiffParseError("No valid SEARCH/REPLACE blocks found")

        return blocks

    @classmethod
    def _strip_code_fences(cls, content: str) -> str:
        """Remove Markdown code fence wrappers if present."""
        stripped = content.strip()
        # Remove opening fence: ```diff, ```python, ``` etc.
        if stripped.startswith("```"):
            first_newline = stripped.find("\n")
            if first_newline != -1:
                stripped = stripped[first_newline + 1:]
        # Remove closing fence
        if stripped.endswith("```"):
            last_newline = stripped.rfind("\n", 0, len(stripped) - 3)
            if last_newline != -1:
                stripped = stripped[:last_newline]
            else:
                stripped = stripped[:-3]
        return stripped.strip()

    @classmethod
    def _is_search_marker(cls, line: str) -> bool:
        return any(line == m for m in cls._SEARCH_MARKERS)

    @classmethod
    def _is_separator(cls, line: str) -> bool:
        return any(line == m for m in cls._SEPARATOR_MARKERS)

    @classmethod
    def _is_replace_marker(cls, line: str) -> bool:
        return any(line == m for m in cls._REPLACE_MARKERS)


# ---------------------------------------------------------------------------
# SearchEngine
# ---------------------------------------------------------------------------

class SearchEngine:
    """
    Search for SEARCH block text within file content.

    Phase 1: Exact string matching only.
    Provides near-match suggestions when exact match fails.
    """

    @staticmethod
    def find_exact(
        content: str, search: str, start_from: int = 0
    ) -> MatchResult:
        """
        Find exact occurrence of search text in content.

        Args:
            content: File content to search in
            search: Text to find
            start_from: Character position to start searching from

        Returns:
            MatchResult with position info
        """
        if not search:
            return MatchResult(found=False, match_count=0)

        pos = content.find(search, start_from)
        total_count = content.count(search)

        if pos >= 0:
            return MatchResult(
                found=True,
                position=pos,
                end_position=pos + len(search),
                match_count=total_count,
            )

        # Not found — provide suggestions
        suggestions = SearchEngine.find_near_matches(content, search)
        return MatchResult(
            found=False,
            position=-1,
            end_position=-1,
            match_count=0,
            suggestions=suggestions,
        )

    @staticmethod
    def count_matches(content: str, search: str) -> int:
        """Count occurrences of search text in content."""
        if not search:
            return 0
        return content.count(search)

    @staticmethod
    def find_near_matches(
        content: str, search: str, top_n: int = 3
    ) -> List[str]:
        """
        Find near-matches when exact match fails.

        Uses first-line similarity to locate candidate regions,
        then scores the full candidate block.

        Args:
            content: File content
            search: The SEARCH text that failed to match
            top_n: Number of suggestions to return

        Returns:
            List of suggestion strings with line info and similarity score
        """
        if not search or not content:
            return []

        content_lines = content.split("\n")
        search_lines = search.split("\n")
        first_line = search_lines[0]
        search_len = len(search_lines)

        if not first_line.strip():
            # If first line is blank, try second line
            if len(search_lines) > 1:
                first_line = search_lines[1]
            else:
                return []

        candidates: List[Tuple[float, int, List[str]]] = []

        for i, line in enumerate(content_lines):
            line_sim = SequenceMatcher(None, line, first_line).ratio()
            if line_sim > 0.5:
                # Extract candidate block of same length
                candidate_lines = content_lines[i: i + search_len]
                candidate_text = "\n".join(candidate_lines)
                full_sim = SequenceMatcher(
                    None, candidate_text, search
                ).ratio()
                if full_sim > 0.4:
                    candidates.append((full_sim, i + 1, candidate_lines))

        # Sort by similarity descending, take top_n
        candidates.sort(key=lambda c: c[0], reverse=True)
        suggestions = []
        for sim, start_line, lines in candidates[:top_n]:
            preview = "\n".join(lines[:5])
            if len(lines) > 5:
                preview += f"\n... ({len(lines) - 5} more lines)"
            suggestions.append(
                f"Line {start_line} ({int(sim * 100)}% similar):\n{preview}"
            )

        return suggestions


# ---------------------------------------------------------------------------
# EditApplier
# ---------------------------------------------------------------------------

class EditApplier:
    """
    Apply multiple ReplaceBlock edits to file content.

    Uses order-invariant application: blocks are sorted by their
    position in the file (descending) and applied back-to-front
    to prevent position drift.
    """

    @staticmethod
    def apply(
        original_content: str,
        blocks: List[ReplaceBlock],
        replace_all: bool = False,
    ) -> EditResult:
        """
        Apply edit blocks to content.

        Args:
            original_content: Current file content
            blocks: List of ReplaceBlock to apply
            replace_all: If True, replace all occurrences of each block

        Returns:
            EditResult with new content and diagnostics
        """
        if not blocks:
            return EditResult(
                success=True,
                new_content=original_content,
                applied_count=0,
            )

        # Phase 1: Locate all blocks in original content
        located: List[Tuple[int, int, int, ReplaceBlock]] = []
        # (position, end_position, original_index, block)
        failed: List[FailedEdit] = []

        for idx, block in enumerate(blocks):
            # Empty search = create-new-file / prepend semantics
            if not block.search:
                if len(blocks) == 1 and not original_content:
                    # Create new file with replace content
                    return EditResult(
                        success=True,
                        new_content=block.replace,
                        applied_count=1,
                    )
                else:
                    failed.append(FailedEdit(
                        original_index=idx,
                        reason="Empty SEARCH block is only valid for creating "
                               "new files (single block, empty file).",
                    ))
                    continue

            if replace_all:
                # For replace_all, do a simple full-content replacement
                count = SearchEngine.count_matches(
                    original_content, block.search
                )
                if count == 0:
                    result = SearchEngine.find_exact(
                        original_content, block.search
                    )
                    failed.append(FailedEdit(
                        original_index=idx,
                        reason="SEARCH_NOT_FOUND: No matches found",
                        suggestions=result.suggestions,
                    ))
                else:
                    # Apply replace_all immediately via str.replace
                    original_content = original_content.replace(
                        block.search, block.replace
                    )
                    located.append((-1, -1, idx, block))  # sentinel
                continue

            # Normal mode: exact single match
            result = SearchEngine.find_exact(original_content, block.search)

            if not result.found:
                failed.append(FailedEdit(
                    original_index=idx,
                    reason="SEARCH_NOT_FOUND: Text not found in file",
                    suggestions=result.suggestions,
                ))
                continue

            if result.match_count > 1:
                failed.append(FailedEdit(
                    original_index=idx,
                    reason=(
                        f"MULTIPLE_MATCHES: Found {result.match_count} "
                        f"matches. Use replace_all=true or provide more "
                        f"context to make SEARCH unique."
                    ),
                ))
                continue

            located.append((
                result.position,
                result.end_position,
                idx,
                block,
            ))

        # If any blocks failed, return failure with diagnostics
        if failed:
            return EditResult(
                success=False,
                new_content=original_content,
                applied_count=len(located),
                failed_edits=failed,
            )

        # Phase 2: Check for overlapping edits
        # Filter out replace_all sentinels (position == -1)
        real_located = [loc for loc in located if loc[0] >= 0]
        real_located.sort(key=lambda x: x[0])

        for i in range(len(real_located) - 1):
            _, end_a, idx_a, _ = real_located[i]
            start_b, _, idx_b, _ = real_located[i + 1]
            if end_a > start_b:
                failed.append(FailedEdit(
                    original_index=idx_b,
                    reason=(
                        f"OVERLAPPING_EDITS: Block #{idx_b + 1} overlaps "
                        f"with block #{idx_a + 1}."
                    ),
                ))
                return EditResult(
                    success=False,
                    new_content=original_content,
                    applied_count=0,
                    failed_edits=failed,
                )

        # Phase 3: Apply edits back-to-front (descending position)
        real_located.sort(key=lambda x: x[0], reverse=True)
        content = original_content

        for pos, end_pos, idx, block in real_located:
            content = content[:pos] + block.replace + content[end_pos:]

        # Generate snippet around last edit location
        snippet = EditApplier._generate_snippet(content, real_located)

        return EditResult(
            success=True,
            new_content=content,
            applied_count=len(located),
            snippet_after=snippet,
        )

    @staticmethod
    def _generate_snippet(
        content: str,
        located: List[Tuple[int, int, int, ReplaceBlock]],
        context_lines: int = 3,
    ) -> Optional[str]:
        """Generate a snippet of the content around the edit location."""
        if not located:
            return None

        # Use the first (lowest position) edit for snippet
        sorted_locs = sorted(located, key=lambda x: x[0])
        pos = sorted_locs[0][0]
        if pos < 0:
            return None

        lines = content.split("\n")
        # Find which line the position falls on
        char_count = 0
        target_line = 0
        for i, line in enumerate(lines):
            char_count += len(line) + 1  # +1 for \n
            if char_count > pos:
                target_line = i
                break

        start = max(0, target_line - context_lines)
        end = min(len(lines), target_line + context_lines + 1)

        snippet_lines = []
        for i in range(start, end):
            prefix = ">>>" if i == target_line else "   "
            snippet_lines.append(f"{prefix} {i + 1:4d} | {lines[i]}")

        return "\n".join(snippet_lines)


# ---------------------------------------------------------------------------
# FileEditingGuard
# ---------------------------------------------------------------------------

class FileEditingGuard:
    """
    Session-level guard enforcing read-before-edit policy.

    Tracks which files have been read via read_file, caches their
    content, and validates that edit_file operations target only
    recently-read files.

    Singleton pattern: use get_edit_guard() to access the global instance.
    """

    CACHE_TTL = 120  # seconds

    def __init__(self):
        self._session_read_files: set = set()
        self._file_content_cache: Dict[str, str] = {}
        self._last_read_time: Dict[str, float] = {}

    def record_read(self, file_path: str, content: str) -> None:
        """
        Record that a file has been read.
        Called by read_file() after successful read.

        Args:
            file_path: Absolute resolved file path
            content: File content that was read
        """
        normalized = str(file_path)
        self._session_read_files.add(normalized)
        self._file_content_cache[normalized] = content
        self._last_read_time[normalized] = time.time()
        logger.debug(f"[EditGuard] Recorded read: {normalized}")

    def validate_edit(self, file_path: str) -> Tuple[bool, Optional[str]]:
        """
        Check if a file is allowed to be edited.

        Args:
            file_path: Absolute resolved file path

        Returns:
            (allowed, reason) tuple. reason is None if allowed.
        """
        normalized = str(file_path)

        # Check if file has been read
        if normalized not in self._session_read_files:
            return (
                False,
                f"EDIT_DENIED: You must call read_file on "
                f"'{normalized}' before editing. This is a safety "
                f"requirement to prevent edits based on stale or "
                f"assumed content.",
            )

        # Check cache freshness
        last_read = self._last_read_time.get(normalized, 0)
        elapsed = time.time() - last_read
        if elapsed > self.CACHE_TTL:
            return (
                False,
                f"CACHE_EXPIRED: File '{normalized}' was read "
                f"{int(elapsed)}s ago (TTL={self.CACHE_TTL}s). "
                f"Please re-read the file to ensure content is current.",
            )

        return (True, None)

    def get_cached_content(self, file_path: str) -> Optional[str]:
        """Get cached content for a file, or None if not cached."""
        return self._file_content_cache.get(str(file_path))

    def verify_freshness(
        self, file_path: str, current_content: str
    ) -> bool:
        """
        Verify that cached content matches current disk content.
        Detects external modifications since last read.

        Args:
            file_path: Absolute resolved file path
            current_content: Content currently on disk

        Returns:
            True if content matches (file is fresh)
        """
        cached = self._file_content_cache.get(str(file_path))
        if cached is None:
            return False

        if cached == current_content:
            return True

        # Tolerate line-ending differences
        normalized_cache = cached.replace("\r\n", "\n")
        normalized_current = current_content.replace("\r\n", "\n")
        return normalized_cache == normalized_current

    def invalidate(self, file_path: str) -> None:
        """Remove a file from the read cache."""
        normalized = str(file_path)
        self._session_read_files.discard(normalized)
        self._file_content_cache.pop(normalized, None)
        self._last_read_time.pop(normalized, None)

    def reset(self) -> None:
        """Clear all cached state."""
        self._session_read_files.clear()
        self._file_content_cache.clear()
        self._last_read_time.clear()
        logger.debug("[EditGuard] Reset all state")


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_guard_instance: Optional[FileEditingGuard] = None


def get_edit_guard() -> FileEditingGuard:
    """Get or create the global FileEditingGuard singleton."""
    global _guard_instance
    if _guard_instance is None:
        _guard_instance = FileEditingGuard()
    return _guard_instance


def reset_edit_guard() -> None:
    """Reset the global FileEditingGuard (useful for testing)."""
    global _guard_instance
    if _guard_instance is not None:
        _guard_instance.reset()
    _guard_instance = None
