"""System-reminder injection mechanism.

Injects volatile information (date, git status) into User Messages via
``<system-reminder>`` tags, keeping the System Prompt unchanged so that
Prompt Caching is not invalidated.

Format::

    <system-reminder>
    # currentDate
    Today's date is 2026-03-28.

    # gitStatus (optional)
    Branch: main
    Recent changes: src/main.py, tests/test_main.py
    </system-reminder>

    {original_user_input}
"""

import logging
import subprocess
from datetime import date
from pathlib import Path
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


class SystemReminderBuilder:
    """Builds ``<system-reminder>``-prefixed user messages.

    Usage::

        builder = SystemReminderBuilder()
        enriched = builder.build("Fix the login bug", project_path=Path("."))
    """

    def build(
        self,
        user_input: str,
        project_path: Optional[Path] = None,
        has_compression: bool = False,
    ) -> str:
        """Construct a user message with ``<system-reminder>`` prefix.

        Args:
            user_input: Original user input text.
            project_path: Project root for git status lookup. If None,
                          git status is skipped.
            has_compression: Whether compressed messages exist in the session.

        Returns:
            String with ``<system-reminder>`` block followed by user input.
        """
        current_date = self._get_current_date()
        git_info = None
        if project_path is not None:
            git_info = self._get_git_status(project_path)

        reminder = self._format_reminder(current_date, git_info, has_compression)
        return f"{reminder}\n\n{user_input}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_current_date() -> str:
        """Return today's date in YYYY-MM-DD format."""
        return date.today().isoformat()

    @staticmethod
    def _get_git_status(project_path: Path) -> Optional[Dict[str, str]]:
        """Get current branch name and recently modified files.

        Returns None if git is unavailable or the path is not a git repo.
        """
        try:
            # Get current branch
            branch_result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                cwd=str(project_path),
                timeout=5,
            )
            if branch_result.returncode != 0:
                logger.debug(f"git branch detection failed: {branch_result.stderr.strip()}")
                return None

            branch = branch_result.stdout.strip()

            # Get recently modified files (last commit + unstaged)
            diff_result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD~1", "--", "."],
                capture_output=True,
                text=True,
                cwd=str(project_path),
                timeout=5,
            )
            files: List[str] = []
            if diff_result.returncode == 0 and diff_result.stdout.strip():
                files = [f.strip() for f in diff_result.stdout.strip().split("\n") if f.strip()]

            # Also include unstaged changes
            unstaged_result = subprocess.run(
                ["git", "diff", "--name-only"],
                capture_output=True,
                text=True,
                cwd=str(project_path),
                timeout=5,
            )
            if unstaged_result.returncode == 0 and unstaged_result.stdout.strip():
                unstaged = [f.strip() for f in unstaged_result.stdout.strip().split("\n") if f.strip()]
                for f in unstaged:
                    if f not in files:
                        files.append(f)

            return {
                "branch": branch,
                "recent_changes": ", ".join(files[:20]) if files else "(none)",
            }

        except FileNotFoundError:
            logger.debug("git not found on PATH")
            return None
        except subprocess.TimeoutExpired:
            logger.debug("git command timed out")
            return None
        except Exception as e:
            logger.debug(f"git status failed: {e}")
            return None

    @staticmethod
    def _format_reminder(
        current_date: str,
        git_info: Optional[Dict[str, str]],
        has_compression: bool = False,
    ) -> str:
        """Format the ``<system-reminder>`` block."""
        lines = ["<system-reminder>"]
        lines.append("# currentDate")
        lines.append(f"Today's date is {current_date}.")

        if git_info is not None:
            lines.append("")
            lines.append("# gitStatus")
            lines.append(f"Branch: {git_info['branch']}")
            lines.append(f"Recent changes: {git_info['recent_changes']}")

        if has_compression:
            lines.append("")
            lines.append("# compressedContext")
            lines.append(
                "Some earlier messages in this conversation have been compressed to save context space."
            )
            lines.append(
                "Compressed sections are marked with <compression_metadata> tags or "
                "[... truncated ... | archive_id=<id>] markers."
            )
            lines.append(
                "If you need the full original content of a compressed section, "
                "use the recall_compressed_messages tool with the corresponding archive_id."
            )
            lines.append(
                "Only recall when the compressed summary is insufficient for your current task."
            )

        lines.append("</system-reminder>")
        return "\n".join(lines)
