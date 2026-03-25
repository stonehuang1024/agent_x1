"""
Agent X1 - LLM Agent System.

A modular, multi-provider LLM agent system supporting:
- Multiple LLM providers (Kimi, Anthropic, OpenAI)
- Tool calling with extensive tool library
- Configurable architecture
"""

__version__ = "1.0.0"
__author__ = "Agent X1 Team"

import os
import logging

from src.core import AppConfig, load_config
from src.util.logger import get_logger
from src.engine import create_engine, ProviderType
from src.tools import ALL_TOOLS, TOOL_CATEGORIES_MAP
from src.skills import SkillRegistry, SkillContextManager

_logger = logging.getLogger(__name__)


def create_agent(config_path: str = None, **kwargs):
    """Factory function to create a fully configured agent with skill support."""
    config = load_config(config_path) if config_path else load_config()
    
    for key, value in kwargs.items():
        if hasattr(config.llm, key):
            setattr(config.llm, key, value)
    
    config.validate()
    
    engine = create_engine(
        provider=ProviderType(config.llm.provider),
        api_key=config.llm.api_key,
        base_url=config.llm.base_url,
        model=config.llm.model,
        temperature=config.llm.temperature,
        max_tokens=config.llm.max_tokens,
        timeout=config.llm.timeout,
        max_iterations=config.llm.max_iterations,
        system_prompt=config.llm.system_prompt
    )
    
    for tool in ALL_TOOLS:
        try:
            engine.register_tool(tool)
        except ValueError:
            pass
    
    # --- Skill framework bootstrap ---
    skills_root = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills")
    registry = SkillRegistry(skills_root)
    skill_count = registry.discover()
    _logger.info(f"[create_agent] Discovered {skill_count} skill(s) from {skills_root}")

    ctx = SkillContextManager(registry)
    engine.set_skill_context(ctx)
    engine.set_tool_categories(TOOL_CATEGORIES_MAP)
    
    return engine


__all__ = [
    "create_agent",
    "AppConfig",
    "load_config",
    "get_logger",
    "create_engine",
    "ProviderType",
    "ALL_TOOLS",
    "TOOL_CATEGORIES_MAP",
    "SkillRegistry",
    "SkillContextManager",
]
