"""PROJECT.md / CLAUDE.md discovery and loading.

Supports three-layer loading:
  - Global: ~/.agent_x1/CLAUDE.md
  - Project: {project-root}/CLAUDE.md, {project-root}/.agent_x1/CLAUDE.md
  - Sub-dir: {subdir}/CLAUDE.md (searched upward to project root)

Backward compatible with legacy PROJECT.md / AGENT.md formats.
"""

import logging
import re
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

from .models import ProjectMemoryFile

logger = logging.getLogger(__name__)


@dataclass
class ProjectMemoryConfig:
    """Configuration for project memory discovery."""
    # New CLAUDE.md format + legacy formats for backward compatibility
    filenames: List[str] = field(default_factory=lambda: [
        "CLAUDE.md", ".agent_x1/CLAUDE.md",
        "PROJECT.md", "AGENT.md",
        ".agent_x1/PROJECT.md", ".agent_x1/AGENT.md",
    ])
    # Global-layer filenames searched under ~/.agent_x1/
    global_filenames: List[str] = field(default_factory=lambda: [
        "CLAUDE.md", "PROJECT.md", "AGENT.md",
    ])
    # Sub-dir layer only searches CLAUDE.md
    subdir_filenames: List[str] = field(default_factory=lambda: [
        "CLAUDE.md",
    ])
    max_size: int = 100_000  # Max file size in bytes

    # Front-matter regex: matches optional YAML block delimited by ---
    _FRONT_MATTER_RE: str = field(
        default=r"^---\s*\n(.*?)\n---\s*\n",
        repr=False,
    )


class ProjectMemoryLoader:
    """Discovers and loads PROJECT.md / CLAUDE.md style files.

    Three-layer hierarchy (loaded in this order):
      1. Global   – ~/.agent_x1/CLAUDE.md
      2. Project  – {project-root}/CLAUDE.md  (and legacy names)
      3. Sub-dir  – {subdir}/CLAUDE.md  (upward to project root)
    """

    def __init__(self, config: Optional[ProjectMemoryConfig] = None):
        self.config = config or ProjectMemoryConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discover(self, start_path: Optional[Path] = None) -> List[ProjectMemoryFile]:
        """Discover project-level and global memory files.

        Searches upward from *start_path* (default: cwd) for project-level
        files, then checks the home directory for global files.

        Returns files ordered: project → parent → global.
        """
        files: List[ProjectMemoryFile] = []
        current = start_path or Path.cwd()

        # Search up the directory tree for project-level files
        while current != current.parent:
            for filename in self.config.filenames:
                path = current / filename
                mem = self._try_load_file(
                    path,
                    scope="project" if current == (start_path or Path.cwd()) else "parent",
                )
                if mem is not None:
                    files.append(mem)
            current = current.parent

        # Check home directory for global config
        home = Path.home()
        for filename in self.config.global_filenames:
            path = home / ".agent_x1" / filename
            mem = self._try_load_file(path, scope="global")
            if mem is not None:
                files.append(mem)

        return files

    def discover_subdir(
        self,
        active_file_path: Path,
        project_root: Optional[Path] = None,
    ) -> List[ProjectMemoryFile]:
        """Discover sub-directory CLAUDE.md files.

        Searches from the directory containing *active_file_path* upward
        until *project_root* (exclusive).  Only looks for CLAUDE.md.

        Args:
            active_file_path: Path to the file currently being accessed.
            project_root: Project root directory.  If None, uses cwd.

        Returns:
            List of discovered files, ordered from deepest sub-dir first.
        """
        files: List[ProjectMemoryFile] = []
        root = (project_root or Path.cwd()).resolve()

        current = active_file_path.resolve()
        if current.is_file():
            current = current.parent

        while current != root and current != current.parent:
            for filename in self.config.subdir_filenames:
                path = current / filename
                mem = self._try_load_file(path, scope="subdir")
                if mem is not None:
                    files.append(mem)
            current = current.parent

        return files

    def discover_global(self) -> List[ProjectMemoryFile]:
        """Discover only global-layer memory files (~/.agent_x1/)."""
        files: List[ProjectMemoryFile] = []
        home = Path.home()
        for filename in self.config.global_filenames:
            path = home / ".agent_x1" / filename
            mem = self._try_load_file(path, scope="global")
            if mem is not None:
                files.append(mem)
        return files

    def discover_project(self, project_root: Optional[Path] = None) -> List[ProjectMemoryFile]:
        """Discover only project-layer memory files at *project_root*."""
        files: List[ProjectMemoryFile] = []
        root = project_root or Path.cwd()
        for filename in self.config.filenames:
            path = root / filename
            mem = self._try_load_file(path, scope="project")
            if mem is not None:
                files.append(mem)
        return files

    def load(self, start_path: Optional[Path] = None) -> Optional[str]:
        """Load and combine project memory files into a single string.

        Returns None if no files are found.
        """
        files = self.discover(start_path)
        if not files:
            return None

        sections = []
        for f in files:
            header = f"<!-- From: {f.path} (scope: {f.scope}) -->"
            sections.append(f"{header}\n{f.content}")

        return "\n\n---\n\n".join(sections)

    def load_single(self, path: Path) -> Optional[ProjectMemoryFile]:
        """Load a single project memory file."""
        return self._try_load_file(path, scope="project")

    # ------------------------------------------------------------------
    # YAML Front Matter parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_front_matter(content: str) -> Dict[str, Any]:
        """Parse YAML Front Matter from content.

        Extracts ``version`` and ``last_updated`` (and any other keys)
        from a YAML block delimited by ``---`` at the start of the file.

        Returns an empty dict if no front matter is found.
        """
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not match:
            return {}

        metadata: Dict[str, Any] = {}
        yaml_block = match.group(1)

        for line in yaml_block.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip().strip('"').strip("'")
                value = value.strip().strip('"').strip("'")
                if key:
                    metadata[key] = value

        return metadata

    @staticmethod
    def _strip_front_matter(content: str) -> str:
        """Remove YAML Front Matter from content, returning body only."""
        return re.sub(r"^---\s*\n.*?\n---\s*\n", "", content, count=1, flags=re.DOTALL)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _try_load_file(
        self, path: Path, scope: str = "project"
    ) -> Optional[ProjectMemoryFile]:
        """Attempt to load a single file, returning None on failure."""
        if not path.exists():
            return None

        try:
            stat = path.stat()
            if stat.st_size > self.config.max_size:
                logger.debug(f"Skipping oversized file: {path} ({stat.st_size} bytes)")
                return None

            raw_content = path.read_text(encoding="utf-8")
            metadata = self._parse_front_matter(raw_content)
            # Keep full content (including front matter) for downstream use
            content = raw_content

            mem = ProjectMemoryFile(
                path=str(path),
                content=content,
                scope=scope,
                metadata=metadata,
            )
            logger.debug(
                "[ProjectMemory] Loaded | path=%s | size=%d bytes | scope=%s",
                str(path), stat.st_size, scope
            )
            return mem

        except Exception as e:
            logger.warning(f"Failed to read {path}: {e}")
            return None
