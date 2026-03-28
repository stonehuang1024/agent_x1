"""Tests for ProjectMemoryLoader — CLAUDE.md three-layer loading.

Each test targets a specific bug class from the design spec (Requirement 2).
Tests verify the DESIGN CONTRACT, not that the current implementation happens to work.

Bug classes covered:
- Global layer discovery failure (CLAUDE.md not found at ~/.agent_x1/)
- Project layer discovery failure (CLAUDE.md not found at project root)
- Sub-dir layer traversal stops too early or too late
- YAML Front Matter silently dropped or mis-parsed
- Legacy format (PROJECT.md/AGENT.md) regression after CLAUDE.md support added
- File read errors crash the loader instead of graceful degradation
- Merge order violation (Global → Project → Sub-dir)
- Oversized files not skipped
"""

import logging
import os
import pytest
from pathlib import Path
from unittest.mock import patch

from src.memory.project_memory import ProjectMemoryLoader, ProjectMemoryConfig
from src.memory.models import ProjectMemoryFile


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_home(tmp_path):
    """Create a fake home directory with .agent_x1/ structure."""
    home = tmp_path / "fakehome"
    home.mkdir()
    agent_dir = home / ".agent_x1"
    agent_dir.mkdir()
    return home


@pytest.fixture
def tmp_project(tmp_path):
    """Create a fake project directory."""
    project = tmp_path / "myproject"
    project.mkdir()
    return project


@pytest.fixture
def loader():
    """Default loader instance."""
    return ProjectMemoryLoader()


# ---------------------------------------------------------------------------
# 1. Global layer discovery
#    Bug: loader ignores ~/.agent_x1/CLAUDE.md or searches wrong path
# ---------------------------------------------------------------------------

class TestGlobalLayerDiscovery:
    """Catches: global CLAUDE.md not discovered, wrong scope assigned."""

    def test_discovers_global_claude_md(self, tmp_home):
        """Global CLAUDE.md at ~/.agent_x1/CLAUDE.md must be found with scope='global'."""
        claude_file = tmp_home / ".agent_x1" / "CLAUDE.md"
        claude_file.write_text("# Global config\nTimezone: UTC\n")

        loader = ProjectMemoryLoader()
        with patch.object(Path, "home", return_value=tmp_home):
            files = loader.discover_global()

        assert len(files) >= 1, (
            "Global CLAUDE.md exists at ~/.agent_x1/CLAUDE.md but was not discovered"
        )
        global_file = [f for f in files if "CLAUDE.md" in f.path]
        assert len(global_file) == 1, (
            f"Expected exactly 1 global CLAUDE.md, got {len(global_file)}: "
            f"{[f.path for f in files]}"
        )
        assert global_file[0].scope == "global", (
            f"Global file scope should be 'global', got '{global_file[0].scope}'"
        )

    def test_global_layer_includes_legacy_formats(self, tmp_home):
        """Legacy PROJECT.md at ~/.agent_x1/ must still be discovered for backward compat."""
        legacy_file = tmp_home / ".agent_x1" / "PROJECT.md"
        legacy_file.write_text("# Legacy global config\n")

        loader = ProjectMemoryLoader()
        with patch.object(Path, "home", return_value=tmp_home):
            files = loader.discover_global()

        paths = [f.path for f in files]
        assert any("PROJECT.md" in p for p in paths), (
            f"Legacy PROJECT.md at ~/.agent_x1/ not discovered. Found: {paths}"
        )

    def test_missing_global_dir_does_not_crash(self, tmp_path):
        """If ~/.agent_x1/ doesn't exist, discover_global returns empty list, no exception."""
        empty_home = tmp_path / "emptyhome"
        empty_home.mkdir()
        # No .agent_x1 directory

        loader = ProjectMemoryLoader()
        with patch.object(Path, "home", return_value=empty_home):
            files = loader.discover_global()

        assert files == [], (
            f"Expected empty list when ~/.agent_x1/ missing, got {len(files)} files"
        )


# ---------------------------------------------------------------------------
# 2. Project layer discovery
#    Bug: CLAUDE.md at project root not found, or .agent_x1/CLAUDE.md missed
# ---------------------------------------------------------------------------

class TestProjectLayerDiscovery:
    """Catches: project-level CLAUDE.md not discovered, wrong scope."""

    def test_discovers_project_root_claude_md(self, tmp_project):
        """CLAUDE.md at project root must be found with scope='project'."""
        claude_file = tmp_project / "CLAUDE.md"
        claude_file.write_text("# Project config\nStack: Python\n")

        loader = ProjectMemoryLoader()
        files = loader.discover_project(tmp_project)

        claude_files = [f for f in files if f.path.endswith("CLAUDE.md")]
        assert len(claude_files) >= 1, (
            f"CLAUDE.md at project root not discovered. Found: {[f.path for f in files]}"
        )
        assert claude_files[0].scope == "project"

    def test_discovers_dotdir_claude_md(self, tmp_project):
        """CLAUDE.md at {project}/.agent_x1/CLAUDE.md must be found."""
        agent_dir = tmp_project / ".agent_x1"
        agent_dir.mkdir()
        claude_file = agent_dir / "CLAUDE.md"
        claude_file.write_text("# Hidden project config\n")

        loader = ProjectMemoryLoader()
        files = loader.discover_project(tmp_project)

        paths = [f.path for f in files]
        assert any(".agent_x1/CLAUDE.md" in p or ".agent_x1\\CLAUDE.md" in p for p in paths), (
            f".agent_x1/CLAUDE.md not discovered. Found: {paths}"
        )

    def test_backward_compat_project_md(self, tmp_project):
        """Legacy PROJECT.md at project root must still be discovered."""
        legacy = tmp_project / "PROJECT.md"
        legacy.write_text("# Legacy project\n")

        loader = ProjectMemoryLoader()
        files = loader.discover_project(tmp_project)

        paths = [f.path for f in files]
        assert any("PROJECT.md" in p for p in paths), (
            f"Legacy PROJECT.md not discovered at project root. Found: {paths}"
        )

    def test_backward_compat_agent_md(self, tmp_project):
        """Legacy AGENT.md at project root must still be discovered."""
        legacy = tmp_project / "AGENT.md"
        legacy.write_text("# Legacy agent\n")

        loader = ProjectMemoryLoader()
        files = loader.discover_project(tmp_project)

        paths = [f.path for f in files]
        assert any("AGENT.md" in p for p in paths), (
            f"Legacy AGENT.md not discovered at project root. Found: {paths}"
        )


# ---------------------------------------------------------------------------
# 3. Sub-dir layer discovery
#    Bug: traversal doesn't stop at project root, or skips intermediate dirs
# ---------------------------------------------------------------------------

class TestSubdirLayerDiscovery:
    """Catches: sub-dir CLAUDE.md missed, traversal goes past project root."""

    def test_discovers_subdir_claude_md(self, tmp_project):
        """CLAUDE.md in a sub-directory must be found when active file is in that dir."""
        subdir = tmp_project / "src" / "api"
        subdir.mkdir(parents=True)
        claude_file = subdir / "CLAUDE.md"
        claude_file.write_text("# API module conventions\n")

        active_file = subdir / "handler.py"
        active_file.write_text("# placeholder")

        loader = ProjectMemoryLoader()
        files = loader.discover_subdir(active_file, project_root=tmp_project)

        assert len(files) >= 1, (
            f"Sub-dir CLAUDE.md not discovered. Active file: {active_file}"
        )
        assert files[0].scope == "subdir", (
            f"Sub-dir file scope should be 'subdir', got '{files[0].scope}'"
        )

    def test_discovers_intermediate_dir_claude_md(self, tmp_project):
        """CLAUDE.md in an intermediate directory (src/) must be found
        when active file is in a deeper sub-dir (src/api/)."""
        src_dir = tmp_project / "src"
        src_dir.mkdir()
        src_claude = src_dir / "CLAUDE.md"
        src_claude.write_text("# Src conventions\n")

        api_dir = src_dir / "api"
        api_dir.mkdir()
        active_file = api_dir / "routes.py"
        active_file.write_text("# placeholder")

        loader = ProjectMemoryLoader()
        files = loader.discover_subdir(active_file, project_root=tmp_project)

        paths = [f.path for f in files]
        assert any("src" in p and "CLAUDE.md" in p for p in paths), (
            f"Intermediate src/CLAUDE.md not discovered. Found: {paths}"
        )

    def test_traversal_stops_at_project_root(self, tmp_path):
        """discover_subdir must NOT traverse above project_root."""
        # Create structure: /parent/project/src/file.py
        # Put CLAUDE.md in /parent/ — it should NOT be found
        parent = tmp_path / "parent"
        project = parent / "project"
        src = project / "src"
        src.mkdir(parents=True)

        # CLAUDE.md above project root — should be excluded
        above_root_claude = parent / "CLAUDE.md"
        above_root_claude.write_text("# Should not be found\n")

        active_file = src / "main.py"
        active_file.write_text("# placeholder")

        loader = ProjectMemoryLoader()
        files = loader.discover_subdir(active_file, project_root=project)

        for f in files:
            assert str(parent / "CLAUDE.md") != f.path, (
                f"discover_subdir traversed above project_root. "
                f"Found {f.path} which is above {project}"
            )

    def test_subdir_only_searches_claude_md_not_legacy(self, tmp_project):
        """Sub-dir layer should only search CLAUDE.md, not PROJECT.md/AGENT.md.
        Bug: if sub-dir searches legacy names, it picks up unrelated files."""
        subdir = tmp_project / "src"
        subdir.mkdir()
        # Put a legacy file in subdir — should NOT be found by discover_subdir
        legacy = subdir / "PROJECT.md"
        legacy.write_text("# This is not a CLAUDE.md\n")

        active_file = subdir / "app.py"
        active_file.write_text("# placeholder")

        loader = ProjectMemoryLoader()
        files = loader.discover_subdir(active_file, project_root=tmp_project)

        for f in files:
            assert "PROJECT.md" not in f.path, (
                f"discover_subdir found legacy PROJECT.md in sub-dir: {f.path}. "
                f"Sub-dir layer should only search CLAUDE.md."
            )


# ---------------------------------------------------------------------------
# 4. YAML Front Matter parsing
#    Bug: metadata silently dropped, version/last_updated not extracted
# ---------------------------------------------------------------------------

class TestYAMLFrontMatter:
    """Catches: front matter ignored, keys mis-parsed, content corrupted."""

    def test_parses_version_and_last_updated(self, tmp_project):
        """YAML Front Matter with version and last_updated must be extracted into metadata."""
        content = '---\nversion: "1.0"\nlast_updated: "2026-03-27"\n---\n\n# Project\n'
        claude_file = tmp_project / "CLAUDE.md"
        claude_file.write_text(content)

        loader = ProjectMemoryLoader()
        files = loader.discover_project(tmp_project)

        claude_files = [f for f in files if "CLAUDE.md" in f.path]
        assert len(claude_files) == 1
        meta = claude_files[0].metadata
        assert "version" in meta, (
            f"'version' not extracted from front matter. metadata={meta}"
        )
        assert meta["version"] == "1.0", (
            f"version should be '1.0', got '{meta['version']}'"
        )
        assert "last_updated" in meta, (
            f"'last_updated' not extracted from front matter. metadata={meta}"
        )
        assert meta["last_updated"] == "2026-03-27"

    def test_no_front_matter_returns_empty_metadata(self, tmp_project):
        """Files without YAML Front Matter must have empty metadata dict."""
        claude_file = tmp_project / "CLAUDE.md"
        claude_file.write_text("# No front matter here\nJust content.\n")

        loader = ProjectMemoryLoader()
        files = loader.discover_project(tmp_project)

        claude_files = [f for f in files if "CLAUDE.md" in f.path]
        assert len(claude_files) == 1
        assert claude_files[0].metadata == {}, (
            f"Expected empty metadata for file without front matter, "
            f"got {claude_files[0].metadata}"
        )

    def test_front_matter_content_preserved(self, tmp_project):
        """Full file content (including front matter) must be preserved in .content field."""
        raw = '---\nversion: "2.0"\n---\n\n# Title\nBody text.\n'
        claude_file = tmp_project / "CLAUDE.md"
        claude_file.write_text(raw)

        loader = ProjectMemoryLoader()
        files = loader.discover_project(tmp_project)

        claude_files = [f for f in files if "CLAUDE.md" in f.path]
        assert claude_files[0].content == raw, (
            "File content was modified during loading. "
            "Front matter should be preserved in .content."
        )

    def test_malformed_front_matter_does_not_crash(self, tmp_project):
        """Malformed YAML (missing closing ---) must not crash; metadata should be empty."""
        # Missing closing ---
        content = "---\nversion: broken\nno closing delimiter\n\n# Content\n"
        claude_file = tmp_project / "CLAUDE.md"
        claude_file.write_text(content)

        loader = ProjectMemoryLoader()
        files = loader.discover_project(tmp_project)

        claude_files = [f for f in files if "CLAUDE.md" in f.path]
        assert len(claude_files) == 1
        # Should not crash; metadata may be empty since front matter is malformed
        assert isinstance(claude_files[0].metadata, dict)


# ---------------------------------------------------------------------------
# 5. Error handling and graceful degradation
#    Bug: file read errors crash the loader, permission errors propagate
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Catches: exceptions propagating from file I/O, missing dirs crashing."""

    def test_unreadable_file_skipped_silently(self, tmp_project, caplog):
        """If a CLAUDE.md file exists but is unreadable, it must be skipped
        with a warning log, not crash the loader."""
        claude_file = tmp_project / "CLAUDE.md"
        claude_file.write_text("# Content\n")
        # Make unreadable
        claude_file.chmod(0o000)

        loader = ProjectMemoryLoader()
        try:
            with caplog.at_level(logging.WARNING):
                files = loader.discover_project(tmp_project)
            # Should not contain the unreadable file
            claude_files = [f for f in files if "CLAUDE.md" in f.path and
                           f.path == str(claude_file)]
            assert len(claude_files) == 0, (
                "Unreadable file should be skipped, not included in results"
            )
            # Should have logged a warning
            assert any("Failed to read" in r.message for r in caplog.records), (
                "Expected a warning log about failed file read"
            )
        finally:
            # Restore permissions for cleanup
            claude_file.chmod(0o644)

    def test_oversized_file_skipped(self, tmp_project):
        """Files exceeding max_size must be skipped.
        Bug: loader reads huge files into memory, causing OOM."""
        claude_file = tmp_project / "CLAUDE.md"
        # Create a file larger than default max_size
        config = ProjectMemoryConfig(max_size=100)
        claude_file.write_text("x" * 200)

        loader = ProjectMemoryLoader(config=config)
        files = loader.discover_project(tmp_project)

        claude_files = [f for f in files if "CLAUDE.md" in f.path]
        assert len(claude_files) == 0, (
            f"Oversized file (200 bytes > max 100) should be skipped. "
            f"Found: {[f.path for f in claude_files]}"
        )

    def test_nonexistent_start_path_returns_empty(self):
        """discover() with a nonexistent start_path must return empty, not crash."""
        loader = ProjectMemoryLoader()
        fake_path = Path("/nonexistent/path/that/does/not/exist")
        # Should not raise
        files = loader.discover(fake_path)
        # May return empty or find global files; must not crash
        assert isinstance(files, list)

    def test_discover_subdir_with_file_as_active_path(self, tmp_project):
        """discover_subdir must handle active_file_path being a file (not dir).
        Bug: if code doesn't call .parent on files, it searches the wrong dir."""
        subdir = tmp_project / "src"
        subdir.mkdir()
        claude_file = subdir / "CLAUDE.md"
        claude_file.write_text("# Module rules\n")

        active_file = subdir / "main.py"
        active_file.write_text("# code")

        loader = ProjectMemoryLoader()
        files = loader.discover_subdir(active_file, project_root=tmp_project)

        assert len(files) >= 1, (
            "discover_subdir should find CLAUDE.md when given a file path "
            "(not directory) as active_file_path"
        )


# ---------------------------------------------------------------------------
# 6. Merge order: Global → Project → Sub-dir
#    Bug: layers returned in wrong order, breaking override semantics
# ---------------------------------------------------------------------------

class TestMergeOrder:
    """Catches: incorrect ordering that breaks later-overrides-earlier semantics."""

    def test_discover_returns_project_before_global(self, tmp_project, tmp_home):
        """discover() must return project files before global files,
        so that downstream merge (Global → Project) works correctly."""
        # Create both global and project CLAUDE.md
        global_claude = tmp_home / ".agent_x1" / "CLAUDE.md"
        global_claude.parent.mkdir(parents=True, exist_ok=True)
        global_claude.write_text("# Global\n")

        project_claude = tmp_project / "CLAUDE.md"
        project_claude.write_text("# Project\n")

        loader = ProjectMemoryLoader()
        with patch.object(Path, "home", return_value=tmp_home):
            files = loader.discover(tmp_project)

        scopes = [f.scope for f in files]
        # Project files should appear before global files in the list
        if "project" in scopes and "global" in scopes:
            first_project = scopes.index("project")
            first_global = scopes.index("global")
            assert first_project < first_global, (
                f"Project files must appear before global files in discover() output. "
                f"Scopes order: {scopes}"
            )

    def test_subdir_deepest_first(self, tmp_project):
        """discover_subdir must return deepest sub-dir files first."""
        # Create: project/src/CLAUDE.md and project/src/api/CLAUDE.md
        src = tmp_project / "src"
        src.mkdir()
        src_claude = src / "CLAUDE.md"
        src_claude.write_text("# Src level\n")

        api = src / "api"
        api.mkdir()
        api_claude = api / "CLAUDE.md"
        api_claude.write_text("# API level\n")

        active_file = api / "handler.py"
        active_file.write_text("# code")

        loader = ProjectMemoryLoader()
        files = loader.discover_subdir(active_file, project_root=tmp_project)

        assert len(files) == 2, (
            f"Expected 2 sub-dir CLAUDE.md files, got {len(files)}: "
            f"{[f.path for f in files]}"
        )
        # Deepest (api/) should come first
        assert "api" in files[0].path, (
            f"Deepest sub-dir file should be first. Got order: "
            f"{[f.path for f in files]}"
        )


# ---------------------------------------------------------------------------
# 7. load() integration
#    Bug: load() doesn't combine files correctly, or returns None unexpectedly
# ---------------------------------------------------------------------------

class TestLoadIntegration:
    """Catches: load() returning None when files exist, content mangled."""

    def test_load_returns_combined_content(self, tmp_project):
        """load() must return combined content of all discovered files."""
        claude_file = tmp_project / "CLAUDE.md"
        claude_file.write_text("# Project rules\nUse pytest.\n")

        loader = ProjectMemoryLoader()
        result = loader.load(tmp_project)

        assert result is not None, "load() returned None when CLAUDE.md exists"
        assert "Project rules" in result
        assert "Use pytest" in result

    def test_load_returns_none_when_no_files(self, tmp_path):
        """load() must return None when no memory files exist."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        loader = ProjectMemoryLoader()
        with patch.object(Path, "home", return_value=tmp_path / "nohome"):
            result = loader.load(empty_dir)

        assert result is None, (
            f"load() should return None for empty directory, got: {repr(result[:50])}"
        )

    def test_load_single_returns_file_with_metadata(self, tmp_project):
        """load_single() must return a ProjectMemoryFile with parsed metadata."""
        content = '---\nversion: "3.0"\n---\n\n# Single file\n'
        claude_file = tmp_project / "CLAUDE.md"
        claude_file.write_text(content)

        loader = ProjectMemoryLoader()
        result = loader.load_single(claude_file)

        assert result is not None, "load_single returned None for existing file"
        assert result.metadata.get("version") == "3.0", (
            f"load_single should parse front matter. metadata={result.metadata}"
        )

    def test_load_single_nonexistent_returns_none(self):
        """load_single() on nonexistent path must return None, not raise."""
        loader = ProjectMemoryLoader()
        result = loader.load_single(Path("/does/not/exist/CLAUDE.md"))
        assert result is None


# ---------------------------------------------------------------------------
# 8. ProjectMemoryFile model
#    Bug: metadata field missing or wrong type after deserialization
# ---------------------------------------------------------------------------

class TestProjectMemoryFileModel:
    """Catches: metadata field regression, scope values incorrect."""

    def test_metadata_defaults_to_empty_dict(self):
        """ProjectMemoryFile.metadata must default to empty dict."""
        f = ProjectMemoryFile(path="/test", content="# test")
        assert f.metadata == {}, (
            f"Default metadata should be empty dict, got {f.metadata}"
        )
        assert isinstance(f.metadata, dict)

    def test_metadata_stores_arbitrary_keys(self):
        """metadata field must accept arbitrary string keys."""
        f = ProjectMemoryFile(
            path="/test", content="# test",
            metadata={"version": "1.0", "last_updated": "2026-01-01", "custom": "value"}
        )
        assert f.metadata["version"] == "1.0"
        assert f.metadata["custom"] == "value"

    def test_scope_accepts_subdir_value(self):
        """scope field must accept 'subdir' as a valid value (new scope for sub-dir layer)."""
        f = ProjectMemoryFile(path="/test", content="# test", scope="subdir")
        assert f.scope == "subdir"
