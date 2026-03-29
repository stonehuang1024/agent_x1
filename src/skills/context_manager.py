"""
Skill Context Manager - Orchestrates skill lifecycle, prompt assembly, and tool filtering.

Responsibilities:
- Maintain skill discovery / activation / deactivation lifecycle
- Build layered system prompt (base → catalog → active skill → runtime)
- Filter tools based on active skill's ToolPolicy
- Manage SkillRuntimeState for the active skill
- Coordinate with SkillWorkspaceManager for session directories
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING

from .models import (
    SkillRuntimeState,
    SkillStatus,
    SkillPhase,
    SkillArtifact,
    ArtifactType,
)
from .registry import SkillRegistry
from .loader import SkillSpec
from .workspace import SkillWorkspaceManager

if TYPE_CHECKING:
    from ..core.tool import Tool

logger = logging.getLogger(__name__)


class SkillContextManager:
    """
    Central coordinator between skills, engine prompt, and tools.

    Usage:
        ctx = SkillContextManager(registry, session_dir)
        ctx.discover_skills()

        # Phase 1: inject catalog into system prompt
        prompt = ctx.build_system_prompt(base_prompt)

        # Phase 2: user wants a specific skill
        ctx.activate_skill("recommendation_research", goal="Reproduce DeepFM on Criteo")

        # Get tools filtered for active skill
        tools = ctx.filter_tools(all_tools)

        # Get runtime context for ongoing injection
        runtime_ctx = ctx.get_runtime_context()
    """

    def __init__(self, registry: SkillRegistry, session_dir: Optional[str] = None):
        """
        Args:
            registry: Initialized SkillRegistry (may not yet be discovered).
            session_dir: Current session directory path (for workspace creation).
        """
        self._registry = registry
        self._session_dir = session_dir
        self._active_spec: Optional[SkillSpec] = None
        self._runtime: Optional[SkillRuntimeState] = None
        self._workspace: Optional[SkillWorkspaceManager] = None

    @property
    def registry(self) -> SkillRegistry:
        return self._registry

    @property
    def active_skill_name(self) -> Optional[str]:
        return self._runtime.skill_name if self._runtime else None

    @property
    def is_skill_active(self) -> bool:
        return self._runtime is not None and self._runtime.status in (
            SkillStatus.ACTIVATED,
            SkillStatus.RUNNING,
        )

    @property
    def workspace(self) -> Optional[SkillWorkspaceManager]:
        return self._workspace

    @property
    def runtime(self) -> Optional[SkillRuntimeState]:
        return self._runtime

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover_skills(self) -> int:
        """Scan and register all skills (phase-1 lightweight load)."""
        return self._registry.discover()

    # ------------------------------------------------------------------
    # Activation / Deactivation
    # ------------------------------------------------------------------

    def activate_skill(
        self,
        skill_name: str,
        goal: str = "",
        constraints: str = "",
        success_criteria: str = "",
    ) -> bool:
        """
        Activate a skill by name (phase-2 full load).

        Args:
            skill_name: Must match a discovered skill name.
            goal: User's goal for this skill activation.
            constraints: Resource or scope constraints.
            success_criteria: How to measure success.

        Returns:
            True if activation succeeded.
        """
        spec = self._registry.get_spec(skill_name)
        if spec is None:
            logger.error(f"[SkillCtx] Skill '{skill_name}' not found in registry")
            return False

        self._active_spec = spec

        # Build runtime state
        self._runtime = SkillRuntimeState(
            skill_name=skill_name,
            status=SkillStatus.ACTIVATED,
            goal=goal,
            constraints=constraints,
            success_criteria=success_criteria,
        )

        # Create workspace if session dir is available
        if self._session_dir:
            self._workspace = SkillWorkspaceManager(self._session_dir, skill_name)
            workspace_path = self._workspace.ensure_workspace()
            self._runtime.workspace_dir = workspace_path
            logger.info(f"[SkillCtx] Workspace created at {workspace_path}")

        logger.info(f"[SkillCtx] Skill '{skill_name}' activated with goal: {goal[:80]}")
        logger.debug(
            "[SkillContext] Activated | name=%s | goal=%s | workspace=%s",
            skill_name, goal[:200] if goal else 'N/A',
            self._runtime.workspace_dir or 'N/A'
        )
        return True

    def deactivate_skill(self) -> None:
        """Deactivate the current skill."""
        if self._runtime:
            logger.info(f"[SkillCtx] Deactivating skill '{self._runtime.skill_name}'")
            self._runtime.status = SkillStatus.COMPLETED
        self._active_spec = None
        self._runtime = None
        self._workspace = None

    # ------------------------------------------------------------------
    # Phase / Artifact tracking
    # ------------------------------------------------------------------

    def set_current_phase(self, phase_name: str) -> None:
        """Update the current execution phase."""
        if self._runtime:
            self._runtime.current_phase = phase_name
            # Mark as running once a phase starts
            if self._runtime.status == SkillStatus.ACTIVATED:
                self._runtime.status = SkillStatus.RUNNING

    def complete_phase(self, phase_name: str) -> None:
        """Mark a phase as completed."""
        if self._runtime:
            for p in self._runtime.phases:
                if p.name == phase_name:
                    p.completed = True
                    return
            # Phase not in list yet; add and mark completed
            self._runtime.phases.append(
                SkillPhase(name=phase_name, description="", completed=True)
            )

    def add_artifact(
        self,
        artifact_type: ArtifactType,
        path: str,
        description: str = "",
    ) -> None:
        """Record a produced artifact."""
        if self._runtime:
            self._runtime.artifacts.append(
                SkillArtifact(artifact_type=artifact_type, path=path, description=description)
            )

    def add_note(self, note: str) -> None:
        """Add a runtime note."""
        if self._runtime:
            self._runtime.notes.append(note)

    # ------------------------------------------------------------------
    # Prompt Building (layered)
    # ------------------------------------------------------------------

    def build_system_prompt(self, base_prompt: str) -> str:
        """
        Assemble the full system prompt with skill context layers.

        Layer 1: base_prompt (from config)
        Layer 2: skill catalog summary (always, if skills exist)
        Layer 3: active skill full context (only if a skill is active)
        Layer 4: active skill runtime state (only if running)

        Args:
            base_prompt: The original system prompt from engine config.

        Returns:
            Assembled system prompt string.
        """
        parts: List[str] = [base_prompt]

        # Layer 2: catalog
        catalog_text = self._registry.get_catalog_text()
        if catalog_text:
            parts.append(
                "\n\n---\n\n"
                "You have access to specialized **skills** that provide professional workflows "
                "for complex tasks. When the user's request matches a skill, you should activate "
                "it by stating which skill you are using and why.\n\n"
                f"{catalog_text}"
            )

        # Layer 3: active skill full context
        if self._active_spec and self.is_skill_active:
            parts.append(
                "\n\n---\n\n"
                f"# ACTIVE SKILL: {self._active_spec.name}\n\n"
                "You have activated the skill below. Follow its workflow, conventions, "
                "and validation standards strictly.\n\n"
                f"{self._active_spec.get_full_context()}"
            )

        # Layer 4: runtime state
        if self._runtime and self.is_skill_active:
            runtime_text = self._runtime.get_runtime_summary()
            parts.append(f"\n\n---\n\n{runtime_text}")

        # Layer: workspace info
        if self._workspace and self._workspace.is_initialized:
            parts.append(f"\n\n{self._workspace.get_workspace_summary()}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Tool Filtering
    # ------------------------------------------------------------------

    def filter_tools(
        self,
        all_tools: Dict[str, "Tool"],
        tool_categories: Optional[Dict[str, str]] = None,
    ) -> Dict[str, "Tool"]:
        """
        Return the subset of tools appropriate for the current state.

        If no skill is active, returns all tools unchanged.
        If a skill is active, returns only:
        - Tools in preferred_categories
        - Tools in preferred_tools list
        - Minus any tools in blocked_tools

        Args:
            all_tools: Complete tool dict (name -> Tool).
            tool_categories: Optional mapping of tool_name -> category_key.

        Returns:
            Filtered tool dict.
        """
        if not self.is_skill_active or not self._active_spec:
            return all_tools

        policy = self._active_spec.tool_policy
        blocked = set(policy.blocked_tools)

        # If no preferred tools/categories defined, return all minus blocked
        if not policy.preferred_tools and not policy.preferred_categories:
            return {
                name: tool
                for name, tool in all_tools.items()
                if name not in blocked
            }

        preferred_names = set(policy.preferred_tools)
        preferred_cats = set(policy.preferred_categories)

        result: Dict[str, "Tool"] = {}
        for name, tool in all_tools.items():
            if name in blocked:
                continue
            if name in preferred_names:
                result[name] = tool
                continue
            if tool_categories and tool_categories.get(name) in preferred_cats:
                result[name] = tool
                continue

        return result

    # ------------------------------------------------------------------
    # Runtime Context (for mid-conversation injection)
    # ------------------------------------------------------------------

    def get_runtime_context(self) -> str:
        """
        Get the current runtime context string for mid-conversation injection.

        Returns:
            Markdown-formatted runtime state, or empty string.
        """
        if not self._runtime or not self.is_skill_active:
            return ""
        return self._runtime.get_runtime_summary()

    def set_session_dir(self, session_dir: str) -> None:
        """Update session directory (e.g. when session starts after construction)."""
        self._session_dir = session_dir
