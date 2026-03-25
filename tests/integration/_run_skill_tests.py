#!/usr/bin/env python3
"""Standalone test runner for skill framework - writes results to file."""
import sys
import os
import tempfile
import shutil
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RESULTS = []

def run_test(name, fn):
    try:
        fn()
        RESULTS.append(f"PASS: {name}")
    except Exception as e:
        RESULTS.append(f"FAIL: {name} -> {e}")
        traceback.print_exc()

# ---- Model Tests ----
def test_models_import():
    from src.skills.models import SkillStatus, ArtifactType, SkillMetadata, SkillSummary, SkillRuntimeState, SkillPhase
    assert SkillStatus.ACTIVATED.value == "activated"
    s = SkillSummary(name="x", description="d", tags=["t"])
    assert "### x" in s.to_catalog_text()

def test_runtime_state():
    from src.skills.models import SkillRuntimeState, SkillStatus, SkillPhase
    state = SkillRuntimeState(skill_name="test", status=SkillStatus.RUNNING, goal="G")
    state.phases.append(SkillPhase(name="p1", description="d", completed=True))
    summary = state.get_runtime_summary()
    assert "test" in summary and "[x]" in summary

# ---- Loader Tests ----
def test_loader():
    from src.skills.loader import load_skill_spec
    tmpdir = tempfile.mkdtemp()
    try:
        sd = os.path.join(tmpdir, "test_skill")
        os.makedirs(sd)
        with open(os.path.join(sd, "SKILL.md"), "w") as f:
            f.write("# Test\n\n## Purpose\n\nTesting.\n\n## Description\n\nA test.\n\n## Tags\n\n- test\n")
        spec = load_skill_spec(sd)
        assert spec.name == "test_skill"
        assert spec.metadata.description != ""
        assert len(spec.sections) > 0
    finally:
        shutil.rmtree(tmpdir)

def test_loader_missing():
    from src.skills.loader import load_skill_spec
    tmpdir = tempfile.mkdtemp()
    try:
        try:
            load_skill_spec(tmpdir)
            assert False, "Should have raised FileNotFoundError"
        except FileNotFoundError:
            pass
    finally:
        shutil.rmtree(tmpdir)

# ---- Registry Tests ----
def test_registry():
    from src.skills.registry import SkillRegistry
    tmpdir = tempfile.mkdtemp()
    try:
        for n in ["sa", "sb"]:
            sd = os.path.join(tmpdir, n)
            os.makedirs(sd)
            with open(os.path.join(sd, "SKILL.md"), "w") as f:
                f.write(f"# {n}\n\n## Purpose\n\n{n} purpose.\n\n## Description\n\n{n} desc.\n\n## Tags\n\n- test\n")
        reg = SkillRegistry(tmpdir)
        assert reg.discover() == 2
        assert "sa" in reg
        assert len(reg) == 2
        assert "Available Skills" in reg.get_catalog_text()
        assert len(reg.search("sa")) >= 1
    finally:
        shutil.rmtree(tmpdir)

# ---- Workspace Tests ----
def test_workspace():
    from src.skills.workspace import SkillWorkspaceManager
    tmpdir = tempfile.mkdtemp()
    try:
        ws = SkillWorkspaceManager(tmpdir, "test")
        path = ws.ensure_workspace()
        assert os.path.isdir(os.path.join(path, "papers"))
        assert os.path.isdir(os.path.join(path, "src"))
        assert "Skill Workspace" in ws.get_workspace_summary()
    finally:
        shutil.rmtree(tmpdir)

# ---- Context Manager Tests ----
def test_context_manager():
    from src.skills.registry import SkillRegistry
    from src.skills.context_manager import SkillContextManager
    tmpdir = tempfile.mkdtemp()
    try:
        sd = os.path.join(tmpdir, "skills", "ts")
        os.makedirs(sd)
        with open(os.path.join(sd, "SKILL.md"), "w") as f:
            f.write("# TS\n\n## Purpose\n\nTest.\n\n## Description\n\nTest desc.\n\n## Tags\n\n- test\n\n## When to use\n\nDuring tests.\n")
        session = os.path.join(tmpdir, "session")
        os.makedirs(session)
        
        reg = SkillRegistry(os.path.join(tmpdir, "skills"))
        reg.discover()
        ctx = SkillContextManager(reg, session)
        
        # No active skill
        prompt = ctx.build_system_prompt("Base.")
        assert "Base." in prompt
        assert "Available Skills" in prompt
        
        # Activate
        assert ctx.activate_skill("ts", goal="Test goal")
        assert ctx.is_skill_active
        assert ctx.workspace is not None
        
        # With active skill
        prompt2 = ctx.build_system_prompt("Base.")
        assert "ACTIVE SKILL" in prompt2
        
        # Phase tracking
        ctx.set_current_phase("setup")
        assert ctx.runtime.current_phase == "setup"
        
        # Deactivate
        ctx.deactivate_skill()
        assert not ctx.is_skill_active
    finally:
        shutil.rmtree(tmpdir)

# ---- Engine Integration ----
def test_engine_integration():
    from src.engine.base import BaseEngine, EngineConfig, ProviderType
    from src.skills.context_manager import SkillContextManager
    from src.skills.registry import SkillRegistry
    
    class DummyEngine(BaseEngine):
        def register_tool(self, tool): pass
        def chat(self, user_input): return ""
        def _call_llm(self): return {}
        def _parse_response(self, response): return None
        def _execute_tools(self, tool_calls): return []
    
    config = EngineConfig(provider=ProviderType.KIMI, api_key="test")
    engine = DummyEngine(config)
    assert engine.skill_context is None
    
    tmpdir = tempfile.mkdtemp()
    try:
        reg = SkillRegistry(tmpdir)
        ctx = SkillContextManager(reg)
        engine.set_skill_context(ctx)
        assert engine.skill_context is ctx
        prompt = engine.get_effective_system_prompt()
        assert config.system_prompt in prompt
    finally:
        shutil.rmtree(tmpdir)

# ---- Real SKILL.md ----
def test_real_skill():
    from src.skills.loader import load_skill_spec
    skill_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills", "recommendation_research")
    if not os.path.isdir(skill_dir):
        RESULTS.append("SKIP: test_real_skill (dir not found)")
        return
    spec = load_skill_spec(skill_dir)
    assert spec.name == "recommendation_research"
    assert len(spec.summary.tags) > 0

# ---- Run All ----
if __name__ == "__main__":
    tests = [
        ("models_import", test_models_import),
        ("runtime_state", test_runtime_state),
        ("loader", test_loader),
        ("loader_missing", test_loader_missing),
        ("registry", test_registry),
        ("workspace", test_workspace),
        ("context_manager", test_context_manager),
        ("engine_integration", test_engine_integration),
        ("real_skill", test_real_skill),
    ]
    
    for name, fn in tests:
        run_test(name, fn)
    
    output = "\n".join(RESULTS)
    # Write to file
    with open("/tmp/skill_test_results.txt", "w") as f:
        f.write(output + "\n")
        passed = sum(1 for r in RESULTS if r.startswith("PASS"))
        failed = sum(1 for r in RESULTS if r.startswith("FAIL"))
        skipped = sum(1 for r in RESULTS if r.startswith("SKIP"))
        f.write(f"\nTotal: {len(RESULTS)} | Passed: {passed} | Failed: {failed} | Skipped: {skipped}\n")
    
    # Also print
    print(output)
    print(f"\nTotal: {len(RESULTS)} | Passed: {passed} | Failed: {failed} | Skipped: {skipped}")
    sys.exit(1 if failed > 0 else 0)
