"""
Skills Framework - Pluggable professional skill system for Agent X1.

Provides:
- SkillSpec / SkillSummary / SkillRuntimeState: data models
- SkillRegistry: discovery and indexing
- SkillContextManager: prompt assembly, tool filtering, lifecycle
- SkillWorkspaceManager: session-scoped working directories
- load_skill_spec: SKILL.md parser

Usage:
    from src.skills import SkillRegistry, SkillContextManager

    registry = SkillRegistry("/path/to/skills")
    ctx = SkillContextManager(registry, session_dir="/path/to/session")
    ctx.discover_skills()

    # Build system prompt with skill catalog
    prompt = ctx.build_system_prompt(base_prompt)

    # Activate a skill
    ctx.activate_skill("recommendation_research", goal="Reproduce DeepFM")
"""

from .models import (
    SkillStatus,
    ArtifactType,
    SkillMetadata,
    SkillToolPolicy,
    SkillSummary,
    SkillPhase,
    SkillArtifact,
    SkillRuntimeState,
)
from .loader import SkillSpec, load_skill_spec
from .registry import SkillRegistry
from .context_manager import SkillContextManager
from .workspace import SkillWorkspaceManager

__all__ = [
    # Models
    "SkillStatus",
    "ArtifactType",
    "SkillMetadata",
    "SkillToolPolicy",
    "SkillSummary",
    "SkillPhase",
    "SkillArtifact",
    "SkillRuntimeState",
    # Loader
    "SkillSpec",
    "load_skill_spec",
    # Registry
    "SkillRegistry",
    # Context Manager
    "SkillContextManager",
    # Workspace
    "SkillWorkspaceManager",
]
