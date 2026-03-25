"""
Unit tests for the Skill Framework.

Tests cover:
- models.py: dataclasses, enums, rendering
- loader.py: SKILL.md parsing
- registry.py: discovery, search, catalog
- workspace.py: directory creation, listing
- context_manager.py: activation, prompt building, tool filtering
- Integration with engine base classes
"""

import sys
import os
import tempfile
import shutil

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


# ---------------------------------------------------------------------------
# Model Tests
# ---------------------------------------------------------------------------

class TestSkillModels:

    def test_skill_status_enum(self):
        from src.skills.models import SkillStatus
        assert SkillStatus.INACTIVE.value == "inactive"
        assert SkillStatus.ACTIVATED.value == "activated"
        assert SkillStatus.RUNNING.value == "running"

    def test_artifact_type_enum(self):
        from src.skills.models import ArtifactType
        assert ArtifactType.PAPER_PDF.value == "paper_pdf"
        assert ArtifactType.CODE.value == "code"

    def test_skill_metadata(self):
        from src.skills.models import SkillMetadata
        meta = SkillMetadata(name="test_skill", description="A test skill", tags=["test"])
        assert meta.name == "test_skill"
        assert meta.version == "1.0.0"

    def test_skill_summary_to_catalog_text(self):
        from src.skills.models import SkillSummary
        summary = SkillSummary(
            name="my_skill",
            description="Does something useful",
            tags=["ml", "research"],
            when_to_use="When you need ML research",
        )
        text = summary.to_catalog_text()
        assert "### my_skill" in text
        assert "Does something useful" in text
        assert "ml, research" in text
        assert "When you need ML research" in text

    def test_skill_phase(self):
        from src.skills.models import SkillPhase
        phase = SkillPhase(name="setup", description="Set up environment", order=0)
        assert phase.completed is False

    def test_skill_runtime_state_summary(self):
        from src.skills.models import SkillRuntimeState, SkillStatus, SkillPhase
        state = SkillRuntimeState(
            skill_name="test",
            status=SkillStatus.RUNNING,
            goal="Reproduce DeepFM",
            current_phase="implementation",
            phases=[
                SkillPhase(name="setup", description="Setup env", completed=True),
                SkillPhase(name="implementation", description="Implement model"),
            ],
        )
        summary = state.get_runtime_summary()
        assert "test" in summary
        assert "running" in summary
        assert "Reproduce DeepFM" in summary
        assert "[x]" in summary
        assert "[ ]" in summary


# ---------------------------------------------------------------------------
# Loader Tests
# ---------------------------------------------------------------------------

SAMPLE_SKILL_MD = """# Test Research Skill

## Purpose

A test skill for unit testing the skill framework.

## Description

This skill tests the loader's ability to parse SKILL.md files correctly.

## Tags

- test
- unit-test

## When to use

When running unit tests for the skill framework.

## When NOT to use

In production environments.

## Inputs expected

A test input string.

## Output requirements

Parsed SkillSpec object with correct fields.

## Available tools

- search_arxiv — Search papers
- read_file — Read files
- category: file

## Workflow

### Phase 1: Setup

1. Create workspace
2. Verify environment

### Phase 2: Execute

1. Run the test
2. Check results
"""


class TestSkillLoader:

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.skill_dir = os.path.join(self.tmpdir, "test_skill")
        os.makedirs(self.skill_dir)
        with open(os.path.join(self.skill_dir, "SKILL.md"), "w") as f:
            f.write(SAMPLE_SKILL_MD)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir)

    def test_load_skill_spec(self):
        from src.skills.loader import load_skill_spec
        spec = load_skill_spec(self.skill_dir)
        assert spec.name == "test_skill"
        assert spec.metadata.description != ""
        assert len(spec.sections) > 0

    def test_summary_parsed(self):
        from src.skills.loader import load_skill_spec
        spec = load_skill_spec(self.skill_dir)
        assert spec.summary.name == "test_skill"
        assert "test" in spec.summary.tags or "unit-test" in spec.summary.tags
        assert spec.summary.when_to_use != ""

    def test_tool_policy_parsed(self):
        from src.skills.loader import load_skill_spec
        spec = load_skill_spec(self.skill_dir)
        assert isinstance(spec.tool_policy.preferred_tools, list)

    def test_get_full_context(self):
        from src.skills.loader import load_skill_spec
        spec = load_skill_spec(self.skill_dir)
        ctx = spec.get_full_context()
        assert "# Test Research Skill" in ctx

    def test_get_section(self):
        from src.skills.loader import load_skill_spec
        spec = load_skill_spec(self.skill_dir)
        purpose = spec.get_section("Purpose")
        assert purpose is not None
        assert "test skill" in purpose.lower()

    def test_missing_skill_md_raises(self):
        from src.skills.loader import load_skill_spec
        import pytest
        empty_dir = os.path.join(self.tmpdir, "empty_skill")
        os.makedirs(empty_dir)
        with pytest.raises(FileNotFoundError):
            load_skill_spec(empty_dir)

    def test_empty_skill_md_raises(self):
        from src.skills.loader import load_skill_spec
        import pytest
        empty_skill_dir = os.path.join(self.tmpdir, "empty_md_skill")
        os.makedirs(empty_skill_dir)
        with open(os.path.join(empty_skill_dir, "SKILL.md"), "w") as f:
            f.write("")
        with pytest.raises(ValueError):
            load_skill_spec(empty_skill_dir)


# ---------------------------------------------------------------------------
# Registry Tests
# ---------------------------------------------------------------------------

class TestSkillRegistry:

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        # Create two skill dirs
        for name in ["skill_a", "skill_b"]:
            skill_dir = os.path.join(self.tmpdir, name)
            os.makedirs(skill_dir)
            with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
                f.write(f"# {name}\n\n## Purpose\n\nSkill {name} for testing.\n\n## Description\n\n{name} description.\n\n## Tags\n\n- test\n- {name}\n")

    def teardown_method(self):
        shutil.rmtree(self.tmpdir)

    def test_discover(self):
        from src.skills.registry import SkillRegistry
        reg = SkillRegistry(self.tmpdir)
        count = reg.discover()
        assert count == 2

    def test_list_names(self):
        from src.skills.registry import SkillRegistry
        reg = SkillRegistry(self.tmpdir)
        reg.discover()
        names = reg.list_names()
        assert "skill_a" in names
        assert "skill_b" in names

    def test_get_summary(self):
        from src.skills.registry import SkillRegistry
        reg = SkillRegistry(self.tmpdir)
        reg.discover()
        summary = reg.get_summary("skill_a")
        assert summary is not None
        assert summary.name == "skill_a"

    def test_get_spec(self):
        from src.skills.registry import SkillRegistry
        reg = SkillRegistry(self.tmpdir)
        reg.discover()
        spec = reg.get_spec("skill_a")
        assert spec is not None
        assert spec.name == "skill_a"

    def test_search(self):
        from src.skills.registry import SkillRegistry
        reg = SkillRegistry(self.tmpdir)
        reg.discover()
        results = reg.search("skill_a")
        assert len(results) >= 1
        assert results[0].name == "skill_a"

    def test_catalog_text(self):
        from src.skills.registry import SkillRegistry
        reg = SkillRegistry(self.tmpdir)
        reg.discover()
        catalog = reg.get_catalog_text()
        assert "# Available Skills" in catalog
        assert "skill_a" in catalog
        assert "skill_b" in catalog

    def test_contains(self):
        from src.skills.registry import SkillRegistry
        reg = SkillRegistry(self.tmpdir)
        reg.discover()
        assert "skill_a" in reg
        assert "nonexistent" not in reg

    def test_len(self):
        from src.skills.registry import SkillRegistry
        reg = SkillRegistry(self.tmpdir)
        reg.discover()
        assert len(reg) == 2

    def test_empty_dir(self):
        from src.skills.registry import SkillRegistry
        empty = os.path.join(self.tmpdir, "empty")
        os.makedirs(empty)
        reg = SkillRegistry(empty)
        assert reg.discover() == 0
        assert reg.get_catalog_text() == ""

    def test_nonexistent_dir(self):
        from src.skills.registry import SkillRegistry
        reg = SkillRegistry("/nonexistent/path")
        assert reg.discover() == 0


# ---------------------------------------------------------------------------
# Workspace Tests
# ---------------------------------------------------------------------------

class TestSkillWorkspace:

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir)

    def test_ensure_workspace(self):
        from src.skills.workspace import SkillWorkspaceManager
        ws = SkillWorkspaceManager(self.tmpdir, "test_skill")
        path = ws.ensure_workspace()
        assert os.path.isdir(path)
        assert os.path.isdir(os.path.join(path, "papers"))
        assert os.path.isdir(os.path.join(path, "src"))
        assert os.path.isdir(os.path.join(path, "datasets"))

    def test_custom_subdirs(self):
        from src.skills.workspace import SkillWorkspaceManager
        ws = SkillWorkspaceManager(self.tmpdir, "custom")
        path = ws.ensure_workspace(subdirs=["alpha", "beta"])
        assert os.path.isdir(os.path.join(path, "alpha"))
        assert os.path.isdir(os.path.join(path, "beta"))

    def test_get_subdir(self):
        from src.skills.workspace import SkillWorkspaceManager
        ws = SkillWorkspaceManager(self.tmpdir, "test")
        ws.ensure_workspace()
        sub = ws.get_subdir("custom_sub")
        assert os.path.isdir(sub)

    def test_list_artifacts(self):
        from src.skills.workspace import SkillWorkspaceManager
        ws = SkillWorkspaceManager(self.tmpdir, "test")
        ws.ensure_workspace()
        # Create a file in papers/
        papers_dir = os.path.join(str(ws.workspace_dir), "papers")
        with open(os.path.join(papers_dir, "test.pdf"), "w") as f:
            f.write("fake pdf")
        artifacts = ws.list_artifacts("papers")
        assert len(artifacts) == 1
        assert artifacts[0]["name"] == "test.pdf"

    def test_workspace_summary(self):
        from src.skills.workspace import SkillWorkspaceManager
        ws = SkillWorkspaceManager(self.tmpdir, "test")
        ws.ensure_workspace()
        summary = ws.get_workspace_summary()
        assert "Skill Workspace" in summary
        assert "papers/" in summary

    def test_not_initialized_summary(self):
        from src.skills.workspace import SkillWorkspaceManager
        ws = SkillWorkspaceManager(self.tmpdir, "test")
        summary = ws.get_workspace_summary()
        assert "not yet initialized" in summary


# ---------------------------------------------------------------------------
# Context Manager Tests
# ---------------------------------------------------------------------------

class TestSkillContextManager:

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.session_dir = os.path.join(self.tmpdir, "session")
        os.makedirs(self.session_dir)
        # Create a skill
        skill_dir = os.path.join(self.tmpdir, "skills", "test_skill")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
            f.write("# Test Skill\n\n## Purpose\n\nFor testing.\n\n## Description\n\nTest skill description.\n\n## Tags\n\n- test\n\n## When to use\n\nDuring tests.\n")
        self.skills_root = os.path.join(self.tmpdir, "skills")

    def teardown_method(self):
        shutil.rmtree(self.tmpdir)

    def _make_ctx(self):
        from src.skills.registry import SkillRegistry
        from src.skills.context_manager import SkillContextManager
        reg = SkillRegistry(self.skills_root)
        reg.discover()
        return SkillContextManager(reg, self.session_dir)

    def test_discover_skills(self):
        ctx = self._make_ctx()
        assert ctx.registry.list_names() == ["test_skill"]

    def test_activate_skill(self):
        ctx = self._make_ctx()
        assert ctx.activate_skill("test_skill", goal="Run tests")
        assert ctx.is_skill_active
        assert ctx.active_skill_name == "test_skill"

    def test_activate_nonexistent(self):
        ctx = self._make_ctx()
        assert not ctx.activate_skill("nonexistent")
        assert not ctx.is_skill_active

    def test_deactivate_skill(self):
        ctx = self._make_ctx()
        ctx.activate_skill("test_skill")
        ctx.deactivate_skill()
        assert not ctx.is_skill_active
        assert ctx.active_skill_name is None

    def test_workspace_created_on_activate(self):
        ctx = self._make_ctx()
        ctx.activate_skill("test_skill")
        assert ctx.workspace is not None
        assert ctx.workspace.is_initialized

    def test_build_system_prompt_no_skill(self):
        ctx = self._make_ctx()
        prompt = ctx.build_system_prompt("Base prompt.")
        assert "Base prompt." in prompt
        assert "Available Skills" in prompt

    def test_build_system_prompt_with_active_skill(self):
        ctx = self._make_ctx()
        ctx.activate_skill("test_skill", goal="Testing")
        prompt = ctx.build_system_prompt("Base prompt.")
        assert "ACTIVE SKILL" in prompt
        assert "test_skill" in prompt

    def test_set_phase(self):
        from src.skills.models import SkillStatus
        ctx = self._make_ctx()
        ctx.activate_skill("test_skill")
        ctx.set_current_phase("setup")
        assert ctx.runtime.current_phase == "setup"
        assert ctx.runtime.status == SkillStatus.RUNNING

    def test_complete_phase(self):
        ctx = self._make_ctx()
        ctx.activate_skill("test_skill")
        ctx.set_current_phase("setup")
        ctx.complete_phase("setup")
        assert ctx.runtime.phases[0].completed

    def test_add_artifact(self):
        from src.skills.models import ArtifactType
        ctx = self._make_ctx()
        ctx.activate_skill("test_skill")
        ctx.add_artifact(ArtifactType.CODE, "/path/to/model.py", "Model impl")
        assert len(ctx.runtime.artifacts) == 1

    def test_add_note(self):
        ctx = self._make_ctx()
        ctx.activate_skill("test_skill")
        ctx.add_note("First observation")
        assert "First observation" in ctx.runtime.notes

    def test_filter_tools_no_skill(self):
        ctx = self._make_ctx()
        tools = {"a": "tool_a", "b": "tool_b"}
        result = ctx.filter_tools(tools)
        assert result == tools

    def test_runtime_context(self):
        ctx = self._make_ctx()
        ctx.activate_skill("test_skill", goal="Run tests")
        ctx.set_current_phase("setup")
        runtime_ctx = ctx.get_runtime_context()
        assert "test_skill" in runtime_ctx
        assert "setup" in runtime_ctx

    def test_set_session_dir(self):
        from src.skills.registry import SkillRegistry
        from src.skills.context_manager import SkillContextManager
        reg = SkillRegistry(self.skills_root)
        reg.discover()
        ctx = SkillContextManager(reg)
        new_dir = os.path.join(self.tmpdir, "new_session")
        os.makedirs(new_dir)
        ctx.set_session_dir(new_dir)
        ctx.activate_skill("test_skill")
        assert ctx.workspace is not None


# ---------------------------------------------------------------------------
# Engine Integration Tests
# ---------------------------------------------------------------------------

class TestEngineSkillIntegration:

    def test_base_engine_skill_context(self):
        from src.engine.base import BaseEngine, EngineConfig, ProviderType
        from src.skills.context_manager import SkillContextManager
        from src.skills.registry import SkillRegistry

        config = EngineConfig(provider=ProviderType.KIMI, api_key="test")

        # Create a minimal concrete engine for testing
        class DummyEngine(BaseEngine):
            def register_tool(self, tool): pass
            def chat(self, user_input): return ""
            def _call_llm(self): return {}
            def _parse_response(self, response): return None
            def _execute_tools(self, tool_calls): return []

        engine = DummyEngine(config)
        assert engine.skill_context is None

        tmpdir = tempfile.mkdtemp()
        try:
            reg = SkillRegistry(tmpdir)
            ctx = SkillContextManager(reg)
            engine.set_skill_context(ctx)
            assert engine.skill_context is ctx

            # Without active skill, effective prompt = base prompt
            prompt = engine.get_effective_system_prompt()
            assert config.system_prompt in prompt

            # Without active skill, effective tools = all tools
            engine.tools = {"tool_a": "fake_tool"}
            assert engine.get_effective_tools() == {"tool_a": "fake_tool"}
        finally:
            shutil.rmtree(tmpdir)

    def test_tool_categories_set(self):
        from src.engine.base import BaseEngine, EngineConfig, ProviderType

        class DummyEngine(BaseEngine):
            def register_tool(self, tool): pass
            def chat(self, user_input): return ""
            def _call_llm(self): return {}
            def _parse_response(self, response): return None
            def _execute_tools(self, tool_calls): return []

        config = EngineConfig(provider=ProviderType.KIMI, api_key="test")
        engine = DummyEngine(config)
        engine.set_tool_categories({"tool_a": "file", "tool_b": "bash"})
        assert engine._tool_categories == {"tool_a": "file", "tool_b": "bash"}


# ---------------------------------------------------------------------------
# SKILL.md Integration Test (real skill file)
# ---------------------------------------------------------------------------

class TestRecommendationResearchSkill:

    def test_load_real_skill(self):
        """Test loading the actual recommendation_research SKILL.md."""
        from src.skills.loader import load_skill_spec
        skill_dir = os.path.join(
            os.path.dirname(__file__), '..', '..',
            'skills', 'recommendation_research'
        )
        skill_dir = os.path.abspath(skill_dir)
        if not os.path.isdir(skill_dir):
            import pytest
            pytest.skip("skills/recommendation_research not found")

        spec = load_skill_spec(skill_dir)
        assert spec.name == "recommendation_research"
        assert spec.metadata.description != ""
        assert len(spec.summary.tags) > 0
        assert "recommendation" in spec.summary.description.lower() or "advertising" in spec.summary.description.lower()

    def test_real_skill_sections(self):
        """Verify key sections exist in the real skill."""
        from src.skills.loader import load_skill_spec
        skill_dir = os.path.join(
            os.path.dirname(__file__), '..', '..',
            'skills', 'recommendation_research'
        )
        skill_dir = os.path.abspath(skill_dir)
        if not os.path.isdir(skill_dir):
            import pytest
            pytest.skip("skills/recommendation_research not found")

        spec = load_skill_spec(skill_dir)
        assert spec.get_section("Purpose") is not None
        assert spec.get_section("Workflow") is not None or spec.get_section("Phase 1: Environment Setup") is not None

    def test_real_skill_in_registry(self):
        """Verify the real skill is discovered by the registry."""
        from src.skills.registry import SkillRegistry
        skills_root = os.path.join(
            os.path.dirname(__file__), '..', '..',
            'skills'
        )
        skills_root = os.path.abspath(skills_root)
        if not os.path.isdir(skills_root):
            import pytest
            pytest.skip("skills/ directory not found")

        reg = SkillRegistry(skills_root)
        count = reg.discover()
        assert count >= 1
        assert "recommendation_research" in reg
