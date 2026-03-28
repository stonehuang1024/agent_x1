"""Tests for SystemReminderBuilder — system-reminder injection mechanism.

Bug classes covered:
- Date not injected or wrong format (not YYYY-MM-DD)
- <system-reminder> tags malformed (missing opening/closing)
- Original user input lost or mangled after injection
- Git failure crashes builder instead of graceful degradation
- Git info included when project_path is None
- Empty user input causes crash
- Git branch/changes fields missing from output when git succeeds
"""

import re
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock
import subprocess

import pytest

from src.context.system_reminder import SystemReminderBuilder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _builder():
    return SystemReminderBuilder()


def _mock_git_success(branch="main", files=None):
    """Create mock subprocess.run that simulates successful git commands."""
    files = files or ["src/main.py", "tests/test_main.py"]

    def side_effect(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 0
        if "rev-parse" in cmd:
            result.stdout = f"{branch}\n"
        elif "diff" in cmd and "HEAD~1" in cmd:
            result.stdout = "\n".join(files) + "\n"
        elif "diff" in cmd:
            result.stdout = ""  # no unstaged
        else:
            result.stdout = ""
        result.stderr = ""
        return result

    return side_effect


def _mock_git_failure():
    """Create mock subprocess.run that simulates git failure."""
    def side_effect(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 128
        result.stdout = ""
        result.stderr = "fatal: not a git repository\n"
        return result
    return side_effect


# ---------------------------------------------------------------------------
# 1. Date injection
#    Bug: date missing, wrong format, or stale
# ---------------------------------------------------------------------------

class TestDateInjection:
    """Catches: date not injected or wrong format."""

    def test_contains_today_date_in_iso_format(self):
        """Output must contain 'Today's date is YYYY-MM-DD' with today's actual date.
        Bug: date hardcoded or format wrong (e.g. MM/DD/YYYY)."""
        builder = _builder()
        result = builder.build("Hello")

        today = date.today().isoformat()
        assert f"Today's date is {today}" in result, (
            f"Output should contain 'Today's date is {today}'. Got:\n{result}"
        )

    def test_date_format_is_iso(self):
        """Date must be in YYYY-MM-DD format (ISO 8601).
        Bug: uses locale-dependent format like 'March 28, 2026'."""
        builder = _builder()
        result = builder.build("test")

        # Extract date from the output
        match = re.search(r"Today's date is (\S+)\.", result)
        assert match is not None, f"Could not find date pattern in output:\n{result}"

        date_str = match.group(1)
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", date_str), (
            f"Date should be YYYY-MM-DD format, got '{date_str}'"
        )


# ---------------------------------------------------------------------------
# 2. <system-reminder> tag format
#    Bug: tags malformed, missing, or wrong nesting
# ---------------------------------------------------------------------------

class TestTagFormat:
    """Catches: malformed <system-reminder> tags."""

    def test_starts_with_opening_tag(self):
        """Output must start with '<system-reminder>'.
        Bug: tag missing or has extra whitespace/content before it."""
        builder = _builder()
        result = builder.build("Hello")

        assert result.startswith("<system-reminder>"), (
            f"Output should start with '<system-reminder>', starts with: "
            f"'{result[:50]}'"
        )

    def test_contains_closing_tag(self):
        """Output must contain '</system-reminder>'.
        Bug: closing tag missing → downstream XML parsing fails."""
        builder = _builder()
        result = builder.build("Hello")

        assert "</system-reminder>" in result, (
            f"Output should contain '</system-reminder>'. Got:\n{result}"
        )

    def test_closing_tag_before_user_input(self):
        """'</system-reminder>' must appear BEFORE the user input.
        Bug: user input inside the reminder tags."""
        builder = _builder()
        user_input = "Fix the login bug"
        result = builder.build(user_input)

        closing_pos = result.index("</system-reminder>")
        input_pos = result.index(user_input)
        assert closing_pos < input_pos, (
            f"Closing tag (pos={closing_pos}) must appear before user input "
            f"(pos={input_pos})"
        )

    def test_tags_properly_nested(self):
        """Opening tag must come before closing tag (no reversed nesting).
        Bug: tags in wrong order."""
        builder = _builder()
        result = builder.build("test")

        open_pos = result.index("<system-reminder>")
        close_pos = result.index("</system-reminder>")
        assert open_pos < close_pos, (
            f"Opening tag (pos={open_pos}) must come before closing tag "
            f"(pos={close_pos})"
        )


# ---------------------------------------------------------------------------
# 3. User input preservation
#    Bug: original user input lost, truncated, or modified
# ---------------------------------------------------------------------------

class TestUserInputPreservation:
    """Catches: user input mangled during injection."""

    def test_user_input_present_in_output(self):
        """Original user input must appear in the output unchanged.
        Bug: user input dropped or modified."""
        builder = _builder()
        user_input = "Please refactor the authentication module"
        result = builder.build(user_input)

        assert user_input in result, (
            f"User input not found in output. Input: '{user_input}'\n"
            f"Output:\n{result}"
        )

    def test_user_input_after_closing_tag(self):
        """User input must appear after '</system-reminder>' tag.
        Bug: user input injected inside the reminder block."""
        builder = _builder()
        user_input = "Deploy to production"
        result = builder.build(user_input)

        closing_idx = result.index("</system-reminder>")
        input_idx = result.index(user_input)
        assert input_idx > closing_idx, (
            "User input should appear after </system-reminder> tag"
        )

    def test_empty_user_input(self):
        """Empty user input must not crash the builder.
        Bug: empty string causes IndexError or produces malformed output."""
        builder = _builder()
        result = builder.build("")

        assert "<system-reminder>" in result
        assert "</system-reminder>" in result
        # Should still be valid even with empty input
        assert isinstance(result, str)

    def test_special_characters_in_input_preserved(self):
        """Special characters (XML-like, newlines, unicode) must be preserved.
        Bug: XML escaping mangles user input."""
        builder = _builder()
        user_input = '<code>print("hello")</code>\n\nLine 2 🔥'
        result = builder.build(user_input)

        assert user_input in result, (
            f"Special characters in user input were modified"
        )


# ---------------------------------------------------------------------------
# 4. Git status — success path
#    Bug: git info missing when git is available
# ---------------------------------------------------------------------------

class TestGitStatusSuccess:
    """Catches: git info not included when git commands succeed."""

    def test_branch_included_when_git_available(self):
        """When git succeeds, output must contain 'Branch: {name}'.
        Bug: git info silently dropped even on success."""
        builder = _builder()
        with patch("subprocess.run", side_effect=_mock_git_success(branch="feature/auth")):
            result = builder.build("test", project_path=Path("."))

        assert "Branch: feature/auth" in result, (
            f"Branch info should be in output when git succeeds. Got:\n{result}"
        )

    def test_recent_changes_included(self):
        """When git succeeds, output must contain 'Recent changes: {files}'.
        Bug: file list not extracted from git diff."""
        builder = _builder()
        with patch("subprocess.run", side_effect=_mock_git_success(
            files=["src/auth.py", "tests/test_auth.py"]
        )):
            result = builder.build("test", project_path=Path("."))

        assert "Recent changes:" in result, (
            f"Recent changes should be in output. Got:\n{result}"
        )
        assert "src/auth.py" in result, "Changed file should be listed"

    def test_git_status_section_header(self):
        """Git section must have '# gitStatus' header.
        Bug: header missing, breaking structured parsing."""
        builder = _builder()
        with patch("subprocess.run", side_effect=_mock_git_success()):
            result = builder.build("test", project_path=Path("."))

        assert "# gitStatus" in result, (
            f"Git section should have '# gitStatus' header. Got:\n{result}"
        )


# ---------------------------------------------------------------------------
# 5. Git status — failure/degradation
#    Bug: git failure crashes builder instead of graceful degradation
# ---------------------------------------------------------------------------

class TestGitStatusDegradation:
    """Catches: git failure propagating as exception."""

    def test_git_failure_does_not_crash(self):
        """When git commands fail, build() must still return valid output.
        Bug: subprocess error propagates, crashing the builder."""
        builder = _builder()
        with patch("subprocess.run", side_effect=_mock_git_failure()):
            result = builder.build("test", project_path=Path("."))

        assert "<system-reminder>" in result
        assert "</system-reminder>" in result
        assert "test" in result

    def test_git_failure_omits_git_section(self):
        """When git fails, output must NOT contain gitStatus section.
        Bug: empty/error git section included, confusing the LLM."""
        builder = _builder()
        with patch("subprocess.run", side_effect=_mock_git_failure()):
            result = builder.build("test", project_path=Path("."))

        assert "# gitStatus" not in result, (
            f"Git section should be omitted on failure. Got:\n{result}"
        )

    def test_git_failure_still_has_date(self):
        """When git fails, date must still be present.
        Bug: git failure causes entire reminder to be skipped."""
        builder = _builder()
        with patch("subprocess.run", side_effect=_mock_git_failure()):
            result = builder.build("test", project_path=Path("."))

        assert "Today's date is" in result, (
            "Date should still be present even when git fails"
        )

    def test_git_not_found_on_path(self):
        """When git binary is not found, must degrade gracefully.
        Bug: FileNotFoundError propagates."""
        builder = _builder()
        with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
            result = builder.build("test", project_path=Path("."))

        assert "<system-reminder>" in result
        assert "Today's date is" in result
        assert "# gitStatus" not in result

    def test_git_timeout(self):
        """When git command times out, must degrade gracefully.
        Bug: TimeoutExpired propagates."""
        builder = _builder()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 5)):
            result = builder.build("test", project_path=Path("."))

        assert "<system-reminder>" in result
        assert "Today's date is" in result

    def test_no_project_path_skips_git(self):
        """When project_path is None, git must not be called at all.
        Bug: git called with None path, causing crash."""
        builder = _builder()
        with patch("subprocess.run") as mock_run:
            result = builder.build("test", project_path=None)

        mock_run.assert_not_called()
        assert "# gitStatus" not in result, (
            "Git section should not appear when project_path is None"
        )
