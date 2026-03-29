"""Tests for session-scoped skill output directory routing.

Bug-class targets:
- SKILL.md still tells LLM to create directories outside session workspace
- Runtime summary doesn't include workspace output constraint, so LLM ignores it
- Skill workspace created at wrong path (old research/ prefix instead of output_)
- Skill activation doesn't propagate session_dir to workspace
- LLM prompt doesn't contain workspace path, so LLM downloads to project root
"""

import os
import sys
import tempfile
import shutil
import pytest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from src.skills.models import SkillRuntimeState, SkillStatus
from src.skills.workspace import SkillWorkspaceManager
from src.skills.registry import SkillRegistry
from src.skills.context_manager import SkillContextManager


# ======================================================================
# 1 — SKILL.md Content Validation
# ======================================================================

class TestSkillMdWorkspaceInstructions:
    """Catches: SKILL.md still tells LLM to create directories outside workspace."""

    @pytest.fixture
    def skill_md_content(self):
        skill_path = Path(__file__).parent.parent.parent / "skills" / "recommendation_research" / "SKILL.md"
        return skill_path.read_text()

    def test_no_old_research_path_pattern(self, skill_md_content):
        """SKILL.md must NOT reference the old {session_dir}/research/ path."""
        assert "{session_dir}/research/" not in skill_md_content

    def test_workspace_is_system_managed(self, skill_md_content):
        """SKILL.md must state that workspace is system-managed."""
        assert "system automatically creates a workspace" in skill_md_content.lower() or \
               "system-managed" in skill_md_content.lower()

    def test_never_download_to_project_root(self, skill_md_content):
        """SKILL.md must instruct to never download files outside workspace."""
        content_lower = skill_md_content.lower()
        assert "never download files to the project root" in content_lower or \
               "never save files outside the workspace" in content_lower

    def test_never_create_dirs_outside_workspace(self, skill_md_content):
        """SKILL.md must instruct to never create directories outside workspace."""
        content_lower = skill_md_content.lower()
        assert "never create directories outside" in content_lower or \
               "do not create your own directories outside" in content_lower

    def test_workspace_placeholder_uses_generic_name(self, skill_md_content):
        """SKILL.md workspace structure should use {workspace}/ not a hardcoded path."""
        assert "{workspace}/" in skill_md_content

    def test_phase1_does_not_say_create_workspace(self, skill_md_content):
        """Phase 1 should NOT tell LLM to create the workspace directory structure."""
        # Find Phase 1 section
        phase1_start = skill_md_content.find("### Phase 1")
        phase2_start = skill_md_content.find("### Phase 2")
        if phase1_start >= 0 and phase2_start >= 0:
            phase1_text = skill_md_content[phase1_start:phase2_start].lower()
            assert "create the workspace directory structure" not in phase1_text

    def test_download_uses_workspace_papers_dir(self, skill_md_content):
        """Phase 2 should instruct to download PDFs into {workspace}/papers/."""
        phase2_start = skill_md_content.find("### Phase 2")
        phase3_start = skill_md_content.find("### Phase 3")
        if phase2_start >= 0 and phase3_start >= 0:
            phase2_text = skill_md_content[phase2_start:phase3_start]
            assert "{workspace}/papers/" in phase2_text

    def test_constraints_reference_workspace(self, skill_md_content):
        """Constraints section must reference workspace directory."""
        constraints_start = skill_md_content.find("## Constraints")
        if constraints_start >= 0:
            constraints_text = skill_md_content[constraints_start:].lower()
            assert "workspace" in constraints_text


# ======================================================================
# 2 — Runtime Summary Workspace Instructions
# ======================================================================

class TestRuntimeSummaryWorkspaceInstructions:
    """Catches: Runtime summary doesn't include workspace output constraint."""

    def test_runtime_summary_includes_workspace_warning(self):
        """Runtime summary must include a warning about saving files in workspace."""
        state = SkillRuntimeState(
            skill_name="test_skill",
            status=SkillStatus.RUNNING,
            workspace_dir="/tmp/session_123/output_test_skill",
            goal="Test goal",
        )
        summary = state.get_runtime_summary()
        assert "MUST be saved inside this workspace" in summary or \
               "ALL file outputs" in summary

    def test_runtime_summary_includes_workspace_path(self):
        """Runtime summary must include the workspace path."""
        ws_path = "/tmp/session_123/output_test_skill"
        state = SkillRuntimeState(
            skill_name="test_skill",
            status=SkillStatus.RUNNING,
            workspace_dir=ws_path,
        )
        summary = state.get_runtime_summary()
        assert ws_path in summary

    def test_runtime_summary_no_workspace_no_warning(self):
        """Without workspace_dir, no workspace warning should appear."""
        state = SkillRuntimeState(
            skill_name="test_skill",
            status=SkillStatus.RUNNING,
        )
        summary = state.get_runtime_summary()
        assert "MUST be saved" not in summary

    def test_runtime_summary_workspace_in_backticks(self):
        """Workspace path should be in backticks for clarity."""
        ws_path = "/tmp/session_123/output_test_skill"
        state = SkillRuntimeState(
            skill_name="test_skill",
            status=SkillStatus.RUNNING,
            workspace_dir=ws_path,
        )
        summary = state.get_runtime_summary()
        assert f"`{ws_path}`" in summary


# ======================================================================
# 3 — Skill Activation Creates Workspace Under Session Dir
# ======================================================================

class TestSkillActivationWorkspacePath:
    """Catches: Skill workspace created at wrong path or outside session dir."""

    @pytest.fixture
    def skill_env(self):
        tmpdir = tempfile.mkdtemp()
        # Create a minimal skill
        skill_dir = Path(tmpdir) / "skills" / "test_skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "# Test Skill\n\n## Purpose\nTest\n\n## Description\nTest skill\n\n"
            "## Tags\n- test\n\n## When to use\nTesting\n\n## When NOT to use\nNever\n"
        )
        session_dir = Path(tmpdir) / "sessions" / "session_20260329_100000"
        session_dir.mkdir(parents=True)
        yield tmpdir, str(skill_dir.parent), str(session_dir)
        shutil.rmtree(tmpdir)

    def test_workspace_under_session_dir(self, skill_env):
        """Workspace must be created under the session directory."""
        tmpdir, skills_root, session_dir = skill_env
        registry = SkillRegistry(skills_root)
        registry.discover()
        ctx = SkillContextManager(registry, session_dir=session_dir)
        ctx.activate_skill("test_skill", goal="Test")

        assert ctx.workspace is not None
        ws_path = str(ctx.workspace.workspace_dir)
        assert ws_path.startswith(session_dir)

    def test_workspace_uses_output_prefix(self, skill_env):
        """Workspace directory name must use output_ prefix."""
        tmpdir, skills_root, session_dir = skill_env
        registry = SkillRegistry(skills_root)
        registry.discover()
        ctx = SkillContextManager(registry, session_dir=session_dir)
        ctx.activate_skill("test_skill", goal="Test")

        ws_dir_name = ctx.workspace.workspace_dir.name
        assert ws_dir_name == "output_test_skill"

    def test_workspace_not_under_research(self, skill_env):
        """Workspace must NOT be under a 'research' subdirectory."""
        tmpdir, skills_root, session_dir = skill_env
        registry = SkillRegistry(skills_root)
        registry.discover()
        ctx = SkillContextManager(registry, session_dir=session_dir)
        ctx.activate_skill("test_skill", goal="Test")

        ws_path = str(ctx.workspace.workspace_dir)
        assert "/research/" not in ws_path

    def test_workspace_subdirs_created(self, skill_env):
        """Standard subdirectories must be created inside workspace."""
        tmpdir, skills_root, session_dir = skill_env
        registry = SkillRegistry(skills_root)
        registry.discover()
        ctx = SkillContextManager(registry, session_dir=session_dir)
        ctx.activate_skill("test_skill", goal="Test")

        ws = ctx.workspace.workspace_dir
        assert (ws / "papers").is_dir()
        assert (ws / "src").is_dir()
        assert (ws / "datasets").is_dir()

    def test_runtime_state_has_workspace_dir(self, skill_env):
        """Runtime state must have workspace_dir set after activation."""
        tmpdir, skills_root, session_dir = skill_env
        registry = SkillRegistry(skills_root)
        registry.discover()
        ctx = SkillContextManager(registry, session_dir=session_dir)
        ctx.activate_skill("test_skill", goal="Test")

        assert ctx.runtime is not None
        assert ctx.runtime.workspace_dir is not None
        assert "output_test_skill" in ctx.runtime.workspace_dir


# ======================================================================
# 4 — System Prompt Contains Workspace Path
# ======================================================================

class TestSystemPromptContainsWorkspace:
    """Catches: LLM prompt doesn't contain workspace path."""

    @pytest.fixture
    def skill_env(self):
        tmpdir = tempfile.mkdtemp()
        skill_dir = Path(tmpdir) / "skills" / "test_skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "# Test Skill\n\n## Purpose\nTest\n\n## Description\nTest skill\n\n"
            "## Tags\n- test\n\n## When to use\nTesting\n\n## When NOT to use\nNever\n"
        )
        session_dir = Path(tmpdir) / "sessions" / "session_20260329_100000"
        session_dir.mkdir(parents=True)
        yield tmpdir, str(skill_dir.parent), str(session_dir)
        shutil.rmtree(tmpdir)

    def test_system_prompt_includes_workspace_path(self, skill_env):
        """Built system prompt must include the workspace absolute path."""
        tmpdir, skills_root, session_dir = skill_env
        registry = SkillRegistry(skills_root)
        registry.discover()
        ctx = SkillContextManager(registry, session_dir=session_dir)
        ctx.activate_skill("test_skill", goal="Test")

        prompt = ctx.build_system_prompt("Base prompt")
        ws_path = str(ctx.workspace.workspace_dir)
        assert ws_path in prompt

    def test_system_prompt_includes_output_constraint(self, skill_env):
        """Built system prompt must include the file output constraint."""
        tmpdir, skills_root, session_dir = skill_env
        registry = SkillRegistry(skills_root)
        registry.discover()
        ctx = SkillContextManager(registry, session_dir=session_dir)
        ctx.activate_skill("test_skill", goal="Test")

        prompt = ctx.build_system_prompt("Base prompt")
        assert "MUST be saved inside this workspace" in prompt or \
               "ALL file outputs" in prompt

    def test_system_prompt_includes_workspace_summary(self, skill_env):
        """Built system prompt must include workspace summary with subdirs."""
        tmpdir, skills_root, session_dir = skill_env
        registry = SkillRegistry(skills_root)
        registry.discover()
        ctx = SkillContextManager(registry, session_dir=session_dir)
        ctx.activate_skill("test_skill", goal="Test")

        prompt = ctx.build_system_prompt("Base prompt")
        assert "Skill Workspace:" in prompt
        assert "papers/" in prompt


# ======================================================================
# 5 — Real Recommendation Research Skill Integration
# ======================================================================

class TestRecommendationResearchSkillWorkspace:
    """Integration test: real recommendation_research skill with session workspace."""

    @pytest.fixture
    def session_dir(self):
        tmpdir = tempfile.mkdtemp()
        yield tmpdir
        shutil.rmtree(tmpdir)

    def test_real_skill_workspace_under_session(self, session_dir):
        """Real recommendation_research skill creates workspace under session dir."""
        project_root = Path(__file__).parent.parent.parent
        skills_root = str(project_root / "skills")
        registry = SkillRegistry(skills_root)
        registry.discover()

        ctx = SkillContextManager(registry, session_dir=session_dir)
        result = ctx.activate_skill("recommendation_research", goal="Test RankMixer")

        assert result is True
        assert ctx.workspace is not None
        ws_path = str(ctx.workspace.workspace_dir)
        assert ws_path.startswith(session_dir)
        assert "output_recommendation_research" in ws_path
        assert "/research/" not in ws_path

    def test_real_skill_prompt_has_workspace(self, session_dir):
        """Real skill's system prompt includes workspace path and constraints."""
        project_root = Path(__file__).parent.parent.parent
        skills_root = str(project_root / "skills")
        registry = SkillRegistry(skills_root)
        registry.discover()

        ctx = SkillContextManager(registry, session_dir=session_dir)
        ctx.activate_skill("recommendation_research", goal="Test RankMixer")

        prompt = ctx.build_system_prompt("You are a helpful assistant.")
        ws_path = str(ctx.workspace.workspace_dir)

        # Workspace path must appear in prompt
        assert ws_path in prompt
        # Output constraint must appear
        assert "MUST be saved" in prompt
        # SKILL.md content must reference {workspace}/ not old path
        assert "system-managed" in prompt.lower() or "system automatically" in prompt.lower()

    def test_real_skill_workspace_has_standard_subdirs(self, session_dir):
        """Real skill workspace has all standard research subdirectories."""
        project_root = Path(__file__).parent.parent.parent
        skills_root = str(project_root / "skills")
        registry = SkillRegistry(skills_root)
        registry.discover()

        ctx = SkillContextManager(registry, session_dir=session_dir)
        ctx.activate_skill("recommendation_research", goal="Test")

        ws = ctx.workspace.workspace_dir
        expected_dirs = ["papers", "notes", "datasets", "src", "configs",
                         "scripts", "tests", "runs", "reports", "artifacts"]
        for d in expected_dirs:
            assert (ws / d).is_dir(), f"Missing subdirectory: {d}"
