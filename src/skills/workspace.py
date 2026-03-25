"""
Skill Workspace Manager - Session-scoped working directories for skills.

Creates and manages the directory structure a skill needs during execution,
all under the current session directory.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# Default sub-directories that a research-style skill typically needs.
DEFAULT_RESEARCH_SUBDIRS = [
    "papers",
    "notes",
    "datasets",
    "src",
    "configs",
    "scripts",
    "tests",
    "runs",
    "reports",
    "artifacts",
]


class SkillWorkspaceManager:
    """
    Manages the workspace directory for a skill within a session.

    The workspace lives at:
        {session_dir}/research/{skill_name}/

    Provides helpers to create standard sub-directories, track paths,
    and return workspace info for prompt injection.
    """

    def __init__(self, session_dir: str, skill_name: str):
        """
        Args:
            session_dir: Absolute path to the current session directory.
            skill_name: Name of the skill (used as subdirectory name).
        """
        self._session_dir = Path(session_dir)
        self._skill_name = skill_name
        self._workspace_dir = self._session_dir / "research" / skill_name
        self._initialized = False

    @property
    def workspace_dir(self) -> Path:
        return self._workspace_dir

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def ensure_workspace(
        self, subdirs: Optional[List[str]] = None
    ) -> str:
        """
        Create the workspace directory and standard sub-directories.

        Args:
            subdirs: Custom list of sub-directory names.
                     Defaults to DEFAULT_RESEARCH_SUBDIRS.

        Returns:
            Absolute path to the workspace root.
        """
        dirs_to_create = subdirs if subdirs is not None else DEFAULT_RESEARCH_SUBDIRS

        self._workspace_dir.mkdir(parents=True, exist_ok=True)
        for sub in dirs_to_create:
            (self._workspace_dir / sub).mkdir(parents=True, exist_ok=True)

        self._initialized = True
        logger.info(
            f"[SkillWorkspace] Initialized workspace at {self._workspace_dir} "
            f"with {len(dirs_to_create)} sub-dirs"
        )
        return str(self._workspace_dir.resolve())

    def get_subdir(self, name: str) -> str:
        """Return absolute path to a named sub-directory (creates if missing)."""
        subdir = self._workspace_dir / name
        subdir.mkdir(parents=True, exist_ok=True)
        return str(subdir.resolve())

    def list_artifacts(self, subdir: str = "") -> List[Dict[str, str]]:
        """
        List files in a workspace sub-directory.

        Args:
            subdir: Relative sub-directory name (e.g. "papers"). Empty = root.

        Returns:
            List of dicts with 'name' and 'path' keys.
        """
        target = self._workspace_dir / subdir if subdir else self._workspace_dir
        if not target.is_dir():
            return []
        return [
            {"name": f.name, "path": str(f.resolve())}
            for f in sorted(target.iterdir())
            if f.is_file()
        ]

    def get_workspace_summary(self) -> str:
        """
        Render a concise summary of the workspace for prompt injection.

        Returns:
            Markdown-formatted workspace overview.
        """
        if not self._initialized:
            return f"Workspace not yet initialized for skill '{self._skill_name}'."

        lines = [
            f"**Skill Workspace:** `{self._workspace_dir}`",
        ]

        for sub in sorted(self._workspace_dir.iterdir()):
            if sub.is_dir():
                file_count = sum(1 for f in sub.iterdir() if f.is_file())
                lines.append(f"- `{sub.name}/` — {file_count} file(s)")

        return "\n".join(lines)
