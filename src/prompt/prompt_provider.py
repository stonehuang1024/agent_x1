"""Main prompt assembly system."""

import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field

from src.core.tool import Tool
from src.skills.models import SkillSummary
from src.skills.loader import SkillSpec

from . import sections

logger = logging.getLogger(__name__)


@dataclass
class PromptContext:
    """Context for prompt rendering."""
    mode: str = "interactive"
    model_name: str = ""
    max_tokens: int = 128000
    tools: List[Tool] = field(default_factory=list)
    skills: List[SkillSummary] = field(default_factory=list)
    active_skill: Optional[SkillSpec] = None
    project_memory: str = ""
    user_preferences: Dict[str, str] = field(default_factory=dict)
    runtime_state: str = "idle"
    iteration_count: int = 0
    is_recovery: bool = False


class PromptProvider:
    """Assembles system prompts from modular sections."""
    
    def __init__(self, template_dir: Optional[str] = None):
        self.template_dir = template_dir
    
    def build_system_prompt(self, context: Optional[PromptContext] = None) -> str:
        """Build complete system prompt from sections.
        
        Args:
            context: Optional PromptContext. If None, uses default PromptContext.
        
        Returns:
            Complete system prompt string assembled from all applicable sections.
        """
        context = context or PromptContext()
        result = []
        
        result.append(sections.render_preamble(context))
        result.append(sections.render_mandates(context))
        result.append(sections.render_tools(context))
        
        if context.skills and not context.active_skill:
            result.append(sections.render_skills_catalog(context))
        
        if context.active_skill:
            result.append(sections.render_active_skill(context))
        
        if context.project_memory:
            result.append(sections.render_project_context(context))
        
        if context.is_recovery:
            result.append(sections.render_error_recovery(context))
        
        result.append(sections.render_guidelines(context))
        
        non_empty = [s for s in result if s.strip()]
        return "\n\n---\n\n".join(non_empty)
