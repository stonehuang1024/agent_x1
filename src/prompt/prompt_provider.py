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
        section_names = []
        
        preamble = sections.render_preamble(context)
        result.append(preamble)
        section_names.append(("preamble", len(preamble)))
        
        mandates = sections.render_mandates(context)
        result.append(mandates)
        section_names.append(("mandates", len(mandates)))
        
        tools_section = sections.render_tools(context)
        result.append(tools_section)
        section_names.append(("tools", len(tools_section)))
        
        if context.skills and not context.active_skill:
            catalog = sections.render_skills_catalog(context)
            result.append(catalog)
            section_names.append(("skills_catalog", len(catalog)))
        
        if context.active_skill:
            active = sections.render_active_skill(context)
            result.append(active)
            section_names.append(("active_skill", len(active)))
        
        if context.project_memory:
            proj = sections.render_project_context(context)
            result.append(proj)
            section_names.append(("project_context", len(proj)))
        
        if context.is_recovery:
            recovery = sections.render_error_recovery(context)
            result.append(recovery)
            section_names.append(("error_recovery", len(recovery)))
        
        guidelines = sections.render_guidelines(context)
        result.append(guidelines)
        section_names.append(("guidelines", len(guidelines)))
        
        non_empty = [s for s in result if s.strip()]
        final = "\n\n---\n\n".join(non_empty)
        
        # DEBUG: Building system prompt
        logger.debug(
            "[PromptProvider] Building system prompt | sections=%d | total_length=%d",
            len(section_names), len(final)
        )
        for name, length in section_names:
            if length > 0:
                logger.debug(
                    "[PromptProvider] Section added | name=%s | length=%d",
                    name, length
                )
        
        return final
