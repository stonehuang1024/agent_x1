"""
Skill Framework Data Models.

Defines all dataclasses and enumerations used by the skill system:
- SkillMetadata: static identity of a skill
- SkillSummary: lightweight view for catalog injection
- SkillToolPolicy: which tools a skill prefers / blocks
- SkillArtifact: a tracked output produced during skill execution
- SkillPhase: a named stage in a skill workflow
- SkillRuntimeState: mutable state of an active skill session
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Any, Optional
from pathlib import Path


class SkillStatus(Enum):
    """Lifecycle status of a skill within a session."""
    INACTIVE = "inactive"
    DISCOVERED = "discovered"
    ACTIVATED = "activated"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ArtifactType(Enum):
    """Types of artifacts produced by a skill."""
    PAPER_PDF = "paper_pdf"
    PAPER_MARKDOWN = "paper_markdown"
    NOTE = "note"
    DATASET = "dataset"
    CODE = "code"
    CONFIG = "config"
    TEST = "test"
    EXPERIMENT_LOG = "experiment_log"
    REPORT = "report"
    CHECKPOINT = "checkpoint"
    PLOT = "plot"


@dataclass
class SkillMetadata:
    """Static identity of a skill parsed from SKILL.md front-matter or headers."""
    name: str
    description: str
    tags: List[str] = field(default_factory=list)
    version: str = "1.0.0"
    author: str = ""


@dataclass
class SkillToolPolicy:
    """Defines which tools a skill prefers, and which it blocks."""
    preferred_categories: List[str] = field(default_factory=list)
    preferred_tools: List[str] = field(default_factory=list)
    blocked_tools: List[str] = field(default_factory=list)


@dataclass
class SkillSummary:
    """Lightweight view injected into the LLM catalog prompt (phase-1 discovery)."""
    name: str
    description: str
    tags: List[str]
    when_to_use: str = ""
    when_not_to_use: str = ""
    inputs_expected: str = ""
    deliverables: str = ""

    def to_catalog_text(self) -> str:
        """Render a concise catalog entry for system-prompt injection."""
        lines = [
            f"### {self.name}",
            f"**Description:** {self.description}",
        ]
        if self.tags:
            lines.append(f"**Tags:** {', '.join(self.tags)}")
        if self.when_to_use:
            lines.append(f"**When to use:** {self.when_to_use}")
        if self.when_not_to_use:
            lines.append(f"**When NOT to use:** {self.when_not_to_use}")
        if self.inputs_expected:
            lines.append(f"**Inputs:** {self.inputs_expected}")
        if self.deliverables:
            lines.append(f"**Deliverables:** {self.deliverables}")
        return "\n".join(lines)


@dataclass
class SkillPhase:
    """A named stage in a skill workflow."""
    name: str
    description: str
    order: int = 0
    completed: bool = False
    notes: str = ""


@dataclass
class SkillArtifact:
    """A tracked output produced during skill execution."""
    artifact_type: ArtifactType
    path: str
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillRuntimeState:
    """Mutable state of an active skill within the current session."""
    skill_name: str
    status: SkillStatus = SkillStatus.INACTIVE
    workspace_dir: Optional[str] = None
    current_phase: Optional[str] = None
    phases: List[SkillPhase] = field(default_factory=list)
    artifacts: List[SkillArtifact] = field(default_factory=list)
    goal: str = ""
    constraints: str = ""
    success_criteria: str = ""
    notes: List[str] = field(default_factory=list)

    def get_runtime_summary(self) -> str:
        """Render current runtime state for prompt injection."""
        lines = [
            f"## Active Skill Runtime: {self.skill_name}",
            f"**Status:** {self.status.value}",
            f"**Goal:** {self.goal}" if self.goal else "",
            f"**Workspace:** {self.workspace_dir}" if self.workspace_dir else "",
        ]
        if self.current_phase:
            lines.append(f"**Current Phase:** {self.current_phase}")
        if self.constraints:
            lines.append(f"**Constraints:** {self.constraints}")
        if self.success_criteria:
            lines.append(f"**Success Criteria:** {self.success_criteria}")

        if self.phases:
            lines.append("\n### Phases")
            for p in self.phases:
                mark = "[x]" if p.completed else "[ ]"
                lines.append(f"- {mark} **{p.name}**: {p.description}")

        if self.artifacts:
            lines.append("\n### Artifacts")
            for a in self.artifacts:
                lines.append(f"- [{a.artifact_type.value}] `{a.path}` — {a.description}")

        if self.notes:
            lines.append("\n### Notes")
            for n in self.notes[-5:]:
                lines.append(f"- {n}")

        return "\n".join(line for line in lines if line)
