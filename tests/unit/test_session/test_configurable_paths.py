"""Unit tests for configurable output paths.

Bug-class targets:
- PathConfig: session_dir/memory_data_dir defaults wrong when result_dir changes
- PathConfig: custom session_dir/memory_data_dir ignored, falls back to defaults
- SessionManager.get_output_dir: wrong path, not created, unsafe chars in name
- SessionLogger: memory_data_dir param ignored, history written to wrong location
- SkillWorkspace: still uses old 'research/' prefix instead of 'output_'
"""

import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from src.core.config import PathConfig, AppConfig
from src.session.session_logger import SessionLogger
from src.session.session_manager import SessionManager
from src.session.session_store import SessionStore
from src.session.models import Session, SessionStatus
from src.skills.workspace import SkillWorkspaceManager


def _make_session(**kwargs):
    defaults = dict(name="test-session", status=SessionStatus.COMPLETED, turn_count=5)
    defaults.update(kwargs)
    return Session(**defaults)


# ======================================================================
# 1 — PathConfig Defaults
# ======================================================================

class TestPathConfigDefaults:
    """Catches: session_dir/memory_data_dir not derived from result_dir."""

    def test_default_session_dir_from_result_dir(self):
        pc = PathConfig(result_dir="my_results")
        assert pc.session_dir == str(Path("my_results") / "session")

    def test_default_memory_data_dir_from_result_dir(self):
        pc = PathConfig(result_dir="my_results")
        assert pc.memory_data_dir == str(Path("my_results") / "memory_data")

    def test_custom_session_dir_overrides_default(self):
        pc = PathConfig(result_dir="results", session_dir="/custom/sessions")
        assert pc.session_dir == "/custom/sessions"

    def test_custom_memory_data_dir_overrides_default(self):
        pc = PathConfig(result_dir="results", memory_data_dir="/custom/memory")
        assert pc.memory_data_dir == "/custom/memory"

    def test_both_custom_paths(self):
        pc = PathConfig(
            result_dir="results",
            session_dir="/a/sessions",
            memory_data_dir="/b/memory",
        )
        assert pc.session_dir == "/a/sessions"
        assert pc.memory_data_dir == "/b/memory"

    def test_empty_string_triggers_default(self):
        """Empty string for session_dir/memory_data_dir should use defaults."""
        pc = PathConfig(result_dir="out", session_dir="", memory_data_dir="")
        assert pc.session_dir == str(Path("out") / "session")
        assert pc.memory_data_dir == str(Path("out") / "memory_data")

    def test_ensure_dirs_creates_session_and_memory(self, tmp_path):
        pc = PathConfig(
            log_dir=str(tmp_path / "logs"),
            result_dir=str(tmp_path / "results"),
            data_dir=str(tmp_path / "data"),
            temp_dir=str(tmp_path / "tmp"),
            session_dir=str(tmp_path / "custom_sessions"),
            memory_data_dir=str(tmp_path / "custom_memory"),
        )
        pc.ensure_dirs()
        assert (tmp_path / "custom_sessions").is_dir()
        assert (tmp_path / "custom_memory").is_dir()

    def test_default_pathconfig_values(self):
        """Default PathConfig should have sensible defaults."""
        pc = PathConfig()
        assert pc.result_dir == "results"
        assert pc.session_dir == str(Path("results") / "session")
        assert pc.memory_data_dir == str(Path("results") / "memory_data")


# ======================================================================
# 2 — SessionManager.get_output_dir
# ======================================================================

class TestSessionManagerGetOutputDir:
    """Catches: output dir not created, wrong path, unsafe chars not sanitized."""

    @pytest.fixture
    def env(self, tmp_path):
        (tmp_path / "data").mkdir()
        (tmp_path / "sessions").mkdir()
        (tmp_path / "memory").mkdir()
        return tmp_path

    @pytest.fixture
    def manager(self, env):
        store = SessionStore(str(env / "data" / "test.db"))
        cfg = MagicMock()
        cfg.llm.provider = "anthropic"
        cfg.llm.model = "test-model"
        cfg.llm.temperature = 0.7
        cfg.llm.max_tokens = 4096
        cfg.paths.data_dir = str(env / "data")
        cfg.paths.session_dir = str(env / "sessions")
        cfg.paths.memory_data_dir = str(env / "memory")
        return SessionManager(
            store=store, config=cfg,
            index_path=str(env / "data" / "index.json"),
        )

    def test_get_output_dir_creates_directory(self, manager):
        session = manager.create_session(name="test")
        manager._active_session = session
        output_dir = manager.get_output_dir("rankmixer")
        assert Path(output_dir).is_dir()
        assert "output_rankmixer" in output_dir

    def test_get_output_dir_under_session_dir(self, manager):
        session = manager.create_session(name="test")
        manager._active_session = session
        output_dir = manager.get_output_dir("my_task")
        assert output_dir.startswith(session.session_dir)
        assert output_dir == str(Path(session.session_dir) / "output_my_task")

    def test_get_output_dir_sanitizes_name(self, manager):
        session = manager.create_session(name="test")
        manager._active_session = session
        output_dir = manager.get_output_dir("my task/with spaces!")
        # Should not contain spaces or slashes
        dir_name = Path(output_dir).name
        assert " " not in dir_name
        assert "/" not in dir_name
        assert dir_name.startswith("output_")

    def test_get_output_dir_idempotent(self, manager):
        session = manager.create_session(name="test")
        manager._active_session = session
        dir1 = manager.get_output_dir("task")
        dir2 = manager.get_output_dir("task")
        assert dir1 == dir2

    def test_get_output_dir_different_names(self, manager):
        session = manager.create_session(name="test")
        manager._active_session = session
        dir1 = manager.get_output_dir("task_a")
        dir2 = manager.get_output_dir("task_b")
        assert dir1 != dir2
        assert Path(dir1).is_dir()
        assert Path(dir2).is_dir()

    def test_get_output_dir_no_active_session_raises(self, manager):
        with pytest.raises(ValueError, match="No active session"):
            manager.get_output_dir("task")

    def test_get_output_dir_by_session_id(self, manager):
        session = manager.create_session(name="test")
        output_dir = manager.get_output_dir("task", session_id=session.id)
        assert Path(output_dir).is_dir()


# ======================================================================
# 3 — SessionLogger with Configurable memory_data_dir
# ======================================================================

class TestSessionLoggerMemoryDataDir:
    """Catches: memory_data_dir param ignored, history written to wrong place."""

    def test_custom_memory_data_dir_used(self, tmp_path):
        sess_dir = tmp_path / "sessions" / "sess_001"
        sess_dir.mkdir(parents=True)
        custom_memory = tmp_path / "custom_memory"
        custom_memory.mkdir()

        logger = SessionLogger(
            session_dir=sess_dir,
            session_id="test-id",
            memory_data_dir=str(custom_memory),
        )
        session = _make_session()
        logger.generate_summary(session)
        logger.close()

        # History should be in custom_memory, NOT in sess_dir.parent.parent/memory_data
        history_file = custom_memory / "history_session.md"
        assert history_file.exists()
        content = history_file.read_text()
        assert "## Session: test-id" in content

    def test_no_memory_data_dir_falls_back(self, tmp_path):
        """Without memory_data_dir, falls back to session_dir.parent.parent/memory_data."""
        sess_dir = tmp_path / "results" / "session" / "sess_001"
        sess_dir.mkdir(parents=True)
        fallback_dir = tmp_path / "results" / "memory_data"
        fallback_dir.mkdir(parents=True)

        logger = SessionLogger(
            session_dir=sess_dir,
            session_id="test-id",
            # No memory_data_dir
        )
        session = _make_session()
        logger.generate_summary(session)
        logger.close()

        history_file = fallback_dir / "history_session.md"
        assert history_file.exists()

    def test_memory_data_dir_created_if_missing(self, tmp_path):
        sess_dir = tmp_path / "sessions" / "sess_001"
        sess_dir.mkdir(parents=True)
        custom_memory = tmp_path / "nonexistent_memory"

        logger = SessionLogger(
            session_dir=sess_dir,
            session_id="test-id",
            memory_data_dir=str(custom_memory),
        )
        session = _make_session()
        logger.generate_summary(session)
        logger.close()

        assert custom_memory.is_dir()
        assert (custom_memory / "history_session.md").exists()


# ======================================================================
# 4 — SkillWorkspace Uses output_ Prefix
# ======================================================================

class TestSkillWorkspaceOutputPrefix:
    """Catches: workspace still uses old 'research/' prefix."""

    def test_workspace_dir_uses_output_prefix(self, tmp_path):
        ws = SkillWorkspaceManager(str(tmp_path), "my_skill")
        assert ws.workspace_dir == tmp_path / "output_my_skill"

    def test_workspace_dir_not_under_research(self, tmp_path):
        ws = SkillWorkspaceManager(str(tmp_path), "my_skill")
        ws.ensure_workspace()
        assert not (tmp_path / "research").exists()
        assert (tmp_path / "output_my_skill").is_dir()

    def test_workspace_subdirs_created_under_output(self, tmp_path):
        ws = SkillWorkspaceManager(str(tmp_path), "test_skill")
        path = ws.ensure_workspace()
        assert Path(path).parent == tmp_path
        assert Path(path).name == "output_test_skill"
        assert (Path(path) / "papers").is_dir()
        assert (Path(path) / "src").is_dir()


# ======================================================================
# 5 — SessionManager Creates Session Under Configured session_dir
# ======================================================================

class TestSessionManagerConfiguredPaths:
    """Catches: session created under old result_dir/session instead of config.paths.session_dir."""

    @pytest.fixture
    def env(self, tmp_path):
        (tmp_path / "data").mkdir()
        (tmp_path / "custom_sessions").mkdir()
        (tmp_path / "custom_memory").mkdir()
        return tmp_path

    @pytest.fixture
    def manager(self, env):
        store = SessionStore(str(env / "data" / "test.db"))
        cfg = MagicMock()
        cfg.llm.provider = "anthropic"
        cfg.llm.model = "test-model"
        cfg.llm.temperature = 0.7
        cfg.llm.max_tokens = 4096
        cfg.paths.data_dir = str(env / "data")
        cfg.paths.session_dir = str(env / "custom_sessions")
        cfg.paths.memory_data_dir = str(env / "custom_memory")
        return SessionManager(
            store=store, config=cfg,
            index_path=str(env / "data" / "index.json"),
        )

    def test_session_created_under_custom_session_dir(self, manager, env):
        session = manager.create_session(name="test")
        assert str(env / "custom_sessions") in session.session_dir

    def test_session_not_under_old_results_session(self, manager, env):
        session = manager.create_session(name="test")
        # Should NOT be under results/session
        assert "results/session" not in session.session_dir

    def test_history_written_to_custom_memory_dir(self, manager, env):
        session = manager.create_session(name="test")
        manager._active_session = session
        manager.begin_turn()

        # Get session logger and generate summary
        sl = manager.get_session_logger()
        assert sl is not None
        sl.log_llm_interaction(
            iteration=1, messages=[], tools=[], response={},
            usage={"input_tokens": 10, "output_tokens": 5},
            duration_ms=100.0, stop_reason="end_turn",
        )

        manager.complete_session()

        history_file = env / "custom_memory" / "history_session.md"
        assert history_file.exists()
        content = history_file.read_text()
        assert "## Session:" in content

    def test_session_with_explicit_session_dir(self, manager, env):
        """When session_dir is explicitly passed, it should be used as-is."""
        explicit_dir = str(env / "explicit_session")
        session = manager.create_session(name="test", session_dir=explicit_dir)
        assert session.session_dir == explicit_dir
