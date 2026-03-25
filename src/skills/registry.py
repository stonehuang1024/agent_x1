"""
Skill Registry - Discover, index, and retrieve skills.

Scans a skills root directory for subdirectories containing SKILL.md,
parses their summaries (lightweight), and provides on-demand full loading.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from .loader import SkillSpec, load_skill_spec
from .models import SkillSummary

logger = logging.getLogger(__name__)


class SkillRegistry:
    """
    Central registry for all available skills.

    Responsibilities:
    - Scan skills directory for SKILL.md files
    - Parse and cache lightweight SkillSummary objects
    - On-demand full SkillSpec loading (phase-2 activation)
    - Search / filter by name, tag, keyword
    """

    def __init__(self, skills_root: str):
        """
        Args:
            skills_root: Absolute path to the top-level skills directory.
        """
        self._skills_root = Path(skills_root)
        self._summaries: Dict[str, SkillSummary] = {}
        self._specs: Dict[str, SkillSpec] = {}
        self._skill_dirs: Dict[str, str] = {}

    @property
    def skills_root(self) -> Path:
        return self._skills_root

    def discover(self) -> int:
        """
        Scan skills_root for subdirectories containing SKILL.md.
        Parses only summaries (phase-1 lightweight loading).

        Returns:
            Number of skills discovered.
        """
        self._summaries.clear()
        self._specs.clear()
        self._skill_dirs.clear()

        if not self._skills_root.is_dir():
            logger.warning(f"[SkillRegistry] Skills root not found: {self._skills_root}")
            return 0

        count = 0
        for child in sorted(self._skills_root.iterdir()):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                spec = load_skill_spec(str(child))
                self._summaries[spec.name] = spec.summary
                self._specs[spec.name] = spec
                self._skill_dirs[spec.name] = str(child.resolve())
                count += 1
                logger.info(f"[SkillRegistry] Discovered skill: {spec.name}")
            except Exception as e:
                logger.warning(f"[SkillRegistry] Failed to load skill from {child}: {e}")

        logger.info(f"[SkillRegistry] Discovered {count} skill(s) total")
        return count

    def list_names(self) -> List[str]:
        """Return names of all discovered skills."""
        return list(self._summaries.keys())

    def get_summary(self, name: str) -> Optional[SkillSummary]:
        """Get lightweight summary for a skill by name."""
        return self._summaries.get(name)

    def get_all_summaries(self) -> Dict[str, SkillSummary]:
        """Return all discovered skill summaries."""
        return dict(self._summaries)

    def get_spec(self, name: str) -> Optional[SkillSpec]:
        """
        Get the full SkillSpec for a skill (phase-2 activation load).

        Returns:
            SkillSpec if found, None otherwise.
        """
        return self._specs.get(name)

    def get_skill_dir(self, name: str) -> Optional[str]:
        """Return absolute path to a skill's directory."""
        return self._skill_dirs.get(name)

    def search(self, keyword: str) -> List[SkillSummary]:
        """
        Search skills by keyword (matches name, description, or tags).

        Args:
            keyword: Case-insensitive search term.

        Returns:
            List of matching SkillSummary objects.
        """
        kw = keyword.lower()
        results: List[SkillSummary] = []
        for summary in self._summaries.values():
            if (
                kw in summary.name.lower()
                or kw in summary.description.lower()
                or any(kw in tag.lower() for tag in summary.tags)
            ):
                results.append(summary)
        return results

    def get_catalog_text(self) -> str:
        """
        Build a compact catalog string for system-prompt injection.

        This is the phase-1 content: skill names + short descriptions + tags,
        so the LLM can decide which skill to activate without loading full context.
        """
        if not self._summaries:
            return ""

        lines = ["# Available Skills\n"]
        for summary in self._summaries.values():
            lines.append(summary.to_catalog_text())
            lines.append("")
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._summaries)

    def __contains__(self, name: str) -> bool:
        return name in self._summaries
