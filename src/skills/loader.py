"""
Skill Loader - Parse SKILL.md files into SkillSpec objects.

Reads Anthropic-style SKILL.md markdown files and produces:
- SkillSummary (lightweight, for catalog injection)
- Full section map (for activated skill context injection)
- SkillToolPolicy (parsed from tool-related sections)
- SkillMetadata (name, tags, version)
"""

import re
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import SkillMetadata, SkillSummary, SkillToolPolicy

logger = logging.getLogger(__name__)


class SkillSpec:
    """
    Parsed representation of a single SKILL.md file.

    Attributes:
        metadata: Static identity (name, description, tags, version).
        summary: Lightweight catalog view for phase-1 discovery.
        tool_policy: Preferred / blocked tools for this skill.
        sections: Ordered mapping of heading -> content.
        raw_markdown: The full markdown text.
        skill_dir: Absolute path to the skill directory.
    """

    def __init__(
        self,
        metadata: SkillMetadata,
        summary: SkillSummary,
        tool_policy: SkillToolPolicy,
        sections: Dict[str, str],
        raw_markdown: str,
        skill_dir: str,
    ):
        self.metadata = metadata
        self.summary = summary
        self.tool_policy = tool_policy
        self.sections = sections
        self.raw_markdown = raw_markdown
        self.skill_dir = skill_dir

    @property
    def name(self) -> str:
        return self.metadata.name

    def get_section(self, heading: str) -> Optional[str]:
        """Return content for a section heading (case-insensitive match)."""
        heading_lower = heading.lower()
        for key, val in self.sections.items():
            if key.lower() == heading_lower:
                return val
        return None

    def get_full_context(self) -> str:
        """Return the full SKILL.md markdown for activated-skill injection."""
        return self.raw_markdown

    def get_sections_context(self, headings: List[str]) -> str:
        """Return only requested sections concatenated."""
        parts: List[str] = []
        for h in headings:
            content = self.get_section(h)
            if content:
                parts.append(f"## {h}\n\n{content}")
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def _parse_sections(markdown: str) -> Dict[str, str]:
    """Split markdown into {heading_text: body_content} preserving order."""
    sections: Dict[str, str] = {}
    matches = list(_HEADING_RE.finditer(markdown))

    for idx, match in enumerate(matches):
        heading_text = match.group(2).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown)
        body = markdown[start:end].strip()
        sections[heading_text] = body

    return sections


def _extract_first_paragraph(markdown: str) -> str:
    """Extract the first non-heading paragraph as a description."""
    for line in markdown.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def _extract_list_items(text: str) -> List[str]:
    """Extract markdown list items from a block of text."""
    items: List[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            items.append(stripped[2:].strip())
        elif re.match(r"^\d+\.\s+", stripped):
            items.append(re.sub(r"^\d+\.\s+", "", stripped).strip())
    return items


def _parse_metadata(sections: Dict[str, str], title: str, skill_dir_name: str) -> SkillMetadata:
    """Build SkillMetadata from parsed sections."""
    name = skill_dir_name

    description = ""
    for key in ("Purpose", "Description"):
        if key in sections:
            description = sections[key].split("\n")[0].strip()
            break
    if not description:
        description = title

    tags: List[str] = []
    for key in ("Tags", "Categories"):
        if key in sections:
            tags = _extract_list_items(sections[key])
            break

    version = "1.0.0"
    author = ""
    for key in ("Metadata", "About"):
        if key in sections:
            for line in sections[key].split("\n"):
                if "version" in line.lower():
                    parts = line.split(":")
                    if len(parts) >= 2:
                        version = parts[-1].strip()
                if "author" in line.lower():
                    parts = line.split(":")
                    if len(parts) >= 2:
                        author = parts[-1].strip()

    return SkillMetadata(
        name=name,
        description=description,
        tags=tags,
        version=version,
        author=author,
    )


def _parse_summary(metadata: SkillMetadata, sections: Dict[str, str]) -> SkillSummary:
    """Build SkillSummary from parsed sections."""
    def _first_section(*keys: str) -> str:
        for k in keys:
            for sk, sv in sections.items():
                if k.lower() in sk.lower():
                    return sv.strip()
        return ""

    return SkillSummary(
        name=metadata.name,
        description=metadata.description,
        tags=metadata.tags,
        when_to_use=_first_section("When to use"),
        when_not_to_use=_first_section("When NOT to use", "When not to use"),
        inputs_expected=_first_section("Inputs expected", "Inputs"),
        deliverables=_first_section("Output requirements", "Deliverables"),
    )


def _parse_tool_policy(sections: Dict[str, str]) -> SkillToolPolicy:
    """Build SkillToolPolicy from parsed sections."""
    preferred_categories: List[str] = []
    preferred_tools: List[str] = []
    blocked_tools: List[str] = []

    for key, body in sections.items():
        key_lower = key.lower()
        if "available tools" in key_lower or "tool" in key_lower and "how" in key_lower:
            items = _extract_list_items(body)
            for item in items:
                item_lower = item.lower()
                if "category:" in item_lower:
                    preferred_categories.append(item.split(":")[-1].strip())
                elif "block" in item_lower or "not use" in item_lower:
                    blocked_tools.append(item.split(":")[-1].strip() if ":" in item else item)
                else:
                    clean = item.split("—")[0].split("-")[0].split(":")[0].strip()
                    clean = clean.strip("`").strip()
                    if clean:
                        preferred_tools.append(clean)

    return SkillToolPolicy(
        preferred_categories=preferred_categories,
        preferred_tools=preferred_tools,
        blocked_tools=blocked_tools,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_skill_spec(skill_dir: str) -> SkillSpec:
    """
    Load and parse a SKILL.md from a skill directory.

    Args:
        skill_dir: Absolute path to the skill directory containing SKILL.md.

    Returns:
        Parsed SkillSpec object.

    Raises:
        FileNotFoundError: If SKILL.md does not exist in skill_dir.
        ValueError: If SKILL.md is empty or unparseable.
    """
    skill_path = Path(skill_dir)
    skill_md_path = skill_path / "SKILL.md"

    if not skill_md_path.exists():
        raise FileNotFoundError(f"SKILL.md not found in {skill_dir}")

    raw_markdown = skill_md_path.read_text(encoding="utf-8")
    if not raw_markdown.strip():
        raise ValueError(f"SKILL.md is empty in {skill_dir}")

    # Extract title from first H1
    title = skill_path.name
    first_h1 = re.search(r"^#\s+(.+)$", raw_markdown, re.MULTILINE)
    if first_h1:
        title = first_h1.group(1).strip()

    sections = _parse_sections(raw_markdown)
    skill_dir_name = skill_path.name

    metadata = _parse_metadata(sections, title, skill_dir_name)
    summary = _parse_summary(metadata, sections)
    tool_policy = _parse_tool_policy(sections)

    logger.info(f"[SkillLoader] Loaded skill '{metadata.name}' from {skill_dir}")

    return SkillSpec(
        metadata=metadata,
        summary=summary,
        tool_policy=tool_policy,
        sections=sections,
        raw_markdown=raw_markdown,
        skill_dir=str(skill_path.resolve()),
    )
