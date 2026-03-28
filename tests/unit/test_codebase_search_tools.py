"""
Tests for codebase search tools: grep_search, glob_search, ls_directory.

Covers:
- Basic functionality for each tool
- Subprocess (ripgrep/fd) and Python fallback paths
- Result truncation / max_results
- Case sensitivity
- Exclude patterns
- Tool registry integration
"""

import json
import os
import tempfile
import shutil
import pytest

from src.tools.codebase_search_tools import (
    grep_search,
    glob_search,
    ls_directory,
    GREP_SEARCH_TOOL,
    GLOB_SEARCH_TOOL,
    LS_DIRECTORY_TOOL,
    CODEBASE_TOOLS,
    _grep_python,
    _glob_python,
)


# ---------------------------------------------------------------------------
# Fixtures — temp directory with sample files
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_dir(tmp_path):
    """Create a temporary directory with sample files for testing."""
    # Create some Python files
    (tmp_path / "main.py").write_text("def hello():\n    print('Hello World')\n\nhello()\n")
    (tmp_path / "utils.py").write_text("def add(a, b):\n    return a + b\n\ndef multiply(a, b):\n    return a * b\n")
    
    # Create a subdirectory with more files
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "test_main.py").write_text("import pytest\n\ndef test_hello():\n    assert True\n")
    (sub / "data.json").write_text('{"key": "value"}\n')
    
    # Create a hidden file
    (tmp_path / ".hidden").write_text("secret\n")
    
    # Create a directory that should be excluded
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "cached.pyc").write_text("bytecode")
    
    return tmp_path


# ---------------------------------------------------------------------------
# Tests — grep_search
# ---------------------------------------------------------------------------

class TestGrepSearch:
    def test_basic_search(self, sample_dir):
        result = grep_search("hello", str(sample_dir))
        assert result["match_count"] > 0
        assert result["query"] == "hello"
        assert not result.get("error")

    def test_search_nonexistent_path(self):
        result = grep_search("test", "/nonexistent/path/xyz")
        assert "error" in result

    def test_case_insensitive_default(self, sample_dir):
        result = grep_search("Hello", str(sample_dir), case_sensitive=False)
        assert result["match_count"] > 0

    def test_case_sensitive(self, sample_dir):
        result_sensitive = grep_search("hello", str(sample_dir), case_sensitive=True)
        result_insensitive = grep_search("hello", str(sample_dir), case_sensitive=False)
        # Case-insensitive should find >= case-sensitive matches
        assert result_insensitive["match_count"] >= result_sensitive["match_count"]

    def test_includes_filter(self, sample_dir):
        result = grep_search("def", str(sample_dir), includes=["*.py"])
        assert result["match_count"] > 0
        for m in result["matches"]:
            assert m["file"].endswith(".py")

    def test_fixed_strings(self, sample_dir):
        result = grep_search("a + b", str(sample_dir), fixed_strings=True)
        assert result["match_count"] > 0

    def test_max_results(self, sample_dir):
        result = grep_search("a", str(sample_dir), max_results=2)
        assert result["match_count"] <= 2
        assert result["truncated"] is True or result["match_count"] <= 2

    def test_excludes_pycache(self, sample_dir):
        result = grep_search("bytecode", str(sample_dir))
        # __pycache__ should be excluded by default
        for m in result.get("matches", []):
            assert "__pycache__" not in m["file"]

    def test_python_fallback_basic(self, sample_dir):
        """Directly test the Python fallback path."""
        result = _grep_python("hello", str(sample_dir), None, False, 50, 0, False)
        assert result["backend"] == "python-re"
        assert result["match_count"] > 0

    def test_python_fallback_invalid_regex(self, sample_dir):
        result = _grep_python("[invalid", str(sample_dir), None, False, 50, 0, False)
        assert "error" in result


# ---------------------------------------------------------------------------
# Tests — glob_search
# ---------------------------------------------------------------------------

class TestGlobSearch:
    def test_basic_glob(self, sample_dir):
        result = glob_search("*.py", str(sample_dir))
        assert result["count"] > 0
        assert not result.get("error")
        for path in result["results"]:
            assert path.endswith(".py")

    def test_glob_nonexistent_dir(self):
        result = glob_search("*.py", "/nonexistent/path/xyz")
        assert "error" in result

    def test_type_filter_file(self, sample_dir):
        result = glob_search("*", str(sample_dir), type_filter="file")
        assert result["count"] > 0

    def test_type_filter_directory(self, sample_dir):
        result = glob_search("sub", str(sample_dir), type_filter="directory")
        assert result["count"] >= 1

    def test_max_results(self, sample_dir):
        result = glob_search("*", str(sample_dir), max_results=2)
        assert result["count"] <= 2

    def test_excludes(self, sample_dir):
        result = glob_search("*", str(sample_dir))
        for path in result["results"]:
            assert "__pycache__" not in path

    def test_custom_excludes(self, sample_dir):
        result = glob_search("*", str(sample_dir), excludes=["sub"])
        for path in result["results"]:
            assert "/sub/" not in path and not path.endswith("/sub")

    def test_python_fallback_basic(self, sample_dir):
        result = _glob_python("*.py", str(sample_dir), "any", None, None, 200)
        assert result["backend"] == "python-pathlib"
        assert result["count"] > 0

    def test_max_depth(self, sample_dir):
        result = _glob_python("*.py", str(sample_dir), "file", 0, None, 200)
        # depth 0 means only root directory files
        for path in result["results"]:
            # Files should be directly under sample_dir
            rel = os.path.relpath(path, str(sample_dir))
            assert os.sep not in rel


# ---------------------------------------------------------------------------
# Tests — ls_directory
# ---------------------------------------------------------------------------

class TestLsDirectory:
    def test_basic_ls(self, sample_dir):
        result = ls_directory(str(sample_dir))
        assert result["count"] > 0
        assert not result.get("error")
        names = [e["name"] for e in result["entries"]]
        assert "main.py" in names
        assert "sub" in names

    def test_ls_nonexistent(self):
        result = ls_directory("/nonexistent/path/xyz")
        assert "error" in result

    def test_hidden_excluded_by_default(self, sample_dir):
        result = ls_directory(str(sample_dir), show_hidden=False)
        names = [e["name"] for e in result["entries"]]
        assert ".hidden" not in names

    def test_show_hidden(self, sample_dir):
        result = ls_directory(str(sample_dir), show_hidden=True)
        names = [e["name"] for e in result["entries"]]
        assert ".hidden" in names

    def test_max_entries(self, sample_dir):
        result = ls_directory(str(sample_dir), max_entries=2)
        assert result["count"] <= 2
        assert result["truncated"] is True

    def test_entry_types(self, sample_dir):
        result = ls_directory(str(sample_dir), show_hidden=True)
        type_map = {e["name"]: e["type"] for e in result["entries"]}
        assert type_map.get("main.py") == "file"
        assert type_map.get("sub") == "dir"

    def test_file_size_present(self, sample_dir):
        result = ls_directory(str(sample_dir))
        for entry in result["entries"]:
            if entry["type"] == "file":
                assert entry["size"] is not None and entry["size"] >= 0


# ---------------------------------------------------------------------------
# Tests — Tool definitions and registry
# ---------------------------------------------------------------------------

class TestToolDefinitions:
    def test_grep_tool_has_safety_params(self):
        assert GREP_SEARCH_TOOL.timeout_seconds == 60
        assert GREP_SEARCH_TOOL.max_output_chars == 30000
        assert GREP_SEARCH_TOOL.is_readonly is True

    def test_glob_tool_has_safety_params(self):
        assert GLOB_SEARCH_TOOL.timeout_seconds == 30
        assert GLOB_SEARCH_TOOL.max_output_chars == 20000
        assert GLOB_SEARCH_TOOL.is_readonly is True

    def test_ls_tool_has_safety_params(self):
        assert LS_DIRECTORY_TOOL.timeout_seconds == 10
        assert LS_DIRECTORY_TOOL.max_output_chars == 10000
        assert LS_DIRECTORY_TOOL.is_readonly is True

    def test_codebase_tools_list(self):
        assert len(CODEBASE_TOOLS) == 3
        names = {t.name for t in CODEBASE_TOOLS}
        assert names == {"grep_search", "glob_search", "ls_directory"}

    def test_tool_execute_returns_json(self, sample_dir):
        """Ensure Tool.execute() wraps the function and returns valid JSON."""
        output = LS_DIRECTORY_TOOL.execute(json.dumps({"path": str(sample_dir)}))
        result = json.loads(output)
        assert "entries" in result or "error" in result

    def test_registry_codebase_category(self):
        """Codebase tools are registered under the 'codebase' category."""
        from src.tools import TOOL_REGISTRY
        catalog = TOOL_REGISTRY.get_catalog()
        assert "codebase" in catalog
        codebase_cat = catalog["codebase"]
        tool_names = {t["name"] for t in codebase_cat["tools"]}
        assert "grep_search" in tool_names
        assert "glob_search" in tool_names
        assert "ls_directory" in tool_names
