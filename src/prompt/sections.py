"""Independent section renderers for prompt assembly.

Each function renders a specific section of the system prompt.
These are extracted from PromptProvider to enable independent
testing and extension.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .prompt_provider import PromptContext


def render_preamble(ctx: "PromptContext") -> str:
    """Render the agent identity preamble section."""
    lines = [
        "# Agent X1",
        "",
        "You are Agent X1, an autonomous AI assistant specializing in research,",
        "analysis, and software engineering tasks.",
        "",
        f"**Current Mode**: {ctx.mode}",
        f"**Model**: {ctx.model_name}",
    ]
    if ctx.iteration_count > 0:
        lines.append(f"**Iteration**: {ctx.iteration_count}")
    if ctx.is_recovery:
        lines.append("**Status**: Recovering from error")
    return "\n".join(lines)


def render_mandates(ctx: "PromptContext") -> str:
    """Render the core mandates section."""
    return """## Core Mandates

1. **Verify before acting** - Always read files before editing them
2. **Minimal changes** - Prefer small, targeted edits over large rewrites
3. **Explain reasoning** - Briefly explain your approach before executing tools
4. **Follow conventions** - Respect project conventions from PROJECT.md
5. **Be concise** - Avoid verbose output unless specifically requested
6. **Handle errors gracefully** - If a tool fails, analyze why and try alternatives
"""


def render_tools(ctx: "PromptContext") -> str:
    """Render the available tools section."""
    lines = ["## Available Tools", ""]
    if not ctx.tools:
        lines.append("No tools available.")
        return "\n".join(lines)

    for tool in ctx.tools:
        desc = tool.description[:100]
        if len(tool.description) > 100:
            desc += "..."
        lines.append(f"- **{tool.name}**: {desc}")

    return "\n".join(lines)


def render_skills_catalog(ctx: "PromptContext") -> str:
    """Render the available skills catalog section."""
    if not ctx.skills:
        return ""

    lines = ["## Available Skills", ""]
    for skill in ctx.skills:
        lines.append(f"- **{skill.name}**: {skill.description}")
    return "\n".join(lines)


def render_active_skill(ctx: "PromptContext") -> str:
    """Render the active skill section."""
    if not ctx.active_skill:
        return ""

    skill = ctx.active_skill
    lines = [
        "## Active Skill",
        "",
        f"**Name**: {skill.metadata.name}",
        f"**Description**: {skill.metadata.description}",
    ]
    return "\n".join(lines)


def render_project_context(ctx: "PromptContext") -> str:
    """Render the project context section."""
    if not ctx.project_memory:
        return ""
    return f"## Project Context\n\n{ctx.project_memory}"


def render_guidelines(ctx: "PromptContext") -> str:
    """Render the operational guidelines section."""
    guidelines = ["## Operational Guidelines", ""]

    if ctx.mode == "interactive":
        guidelines.extend([
            "- This is an interactive session. You can ask clarifying questions.",
            "- Use `ask_user` tool if you need more information.",
        ])
    elif ctx.mode == "single":
        guidelines.extend([
            "- This is a single-query mode. Provide complete response without questions.",
            "- Make reasonable assumptions if information is missing.",
        ])

    guidelines.extend([
        "",
        "### Tool Usage",
        "- Check tool parameters carefully before calling",
        "- Handle timeouts gracefully",
        "",
        "### File Operations",
        "- Use Glob to find files efficiently",
        "- Use Grep to search content",
        "- Read files before editing",
        "- Use Edit for precise changes",
    ])

    return "\n".join(guidelines)


def render_compression_instructions(ctx: "PromptContext") -> str:
    """Render instructions for context compression.

    Used by ContextCompressor when summarizing conversation history.
    """
    return """## Compression Instructions

You are summarizing a conversation between a user and an AI assistant.
Preserve the following:
1. **Key decisions** - What was decided and why
2. **File changes** - Which files were modified and how
3. **Open issues** - Unresolved problems or pending tasks
4. **User preferences** - Any stated preferences or constraints

Omit:
- Verbose tool outputs (keep only results/conclusions)
- Redundant back-and-forth clarifications
- Failed attempts that were superseded by successful ones

Output a concise summary in bullet-point format.
"""


def render_loop_warning(ctx: "PromptContext", warning_count: int = 1) -> str:
    """Render a warning when loop detection triggers.

    Args:
        ctx: The prompt context.
        warning_count: How many times the loop warning has been issued.
    """
    lines = [
        "## ⚠️ Loop Detection Warning",
        "",
        f"Repetitive tool call patterns detected (warning #{warning_count}).",
        "",
        "Please:",
        "1. **Stop** and reassess your current approach",
        "2. **Explain** what you're trying to achieve",
        "3. **Try a different strategy** - the current one is not making progress",
    ]
    if warning_count >= 2:
        lines.extend([
            "",
            "**This is your final warning.** If the loop continues, execution will be halted.",
        ])
    return "\n".join(lines)


def render_error_recovery(ctx: "PromptContext") -> str:
    """Render instructions for error recovery mode.

    Used when the agent is recovering from a previous error.
    """
    return """## Error Recovery Mode

A previous operation encountered an error. Please:
1. **Analyze** the error message carefully
2. **Identify** the root cause (wrong parameters, missing file, permission issue, etc.)
3. **Fix** the issue before retrying - do NOT repeat the same failing operation
4. **Verify** the fix by reading relevant files or checking state before proceeding

If you cannot resolve the error after 2 attempts, explain the issue to the user
and ask for guidance.
"""
