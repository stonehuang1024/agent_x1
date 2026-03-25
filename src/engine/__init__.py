"""
Engine Factory Module - Unified interface for creating LLM engines.

Provides factory functions and the EngineRegistry for creating and
managing different LLM engine implementations based on configuration.

Usage:
    from src.engine import create_engine, EngineRegistry
    
    # Create engine from config
    config = load_config()
    engine = create_engine(config)
    
    # Or use factory directly
    engine = EngineRegistry.create(
        provider=ProviderType.ANTHROPIC,
        api_key="your-key",
        model="kimi-k2.5"
    )
"""

import os
import logging
from typing import Optional, Dict, Type

from .base import BaseEngine, EngineConfig, ProviderType
from .kimi_engine import KimiEngine
from .anthropic_engine import AnthropicEngine

logger = logging.getLogger(__name__)


class EngineRegistry:
    """
    Registry for LLM engine implementations.
    
    This registry maps provider types to their corresponding engine
    implementations, enabling dynamic engine creation.
    
    Class Attributes:
        _engines: Dictionary mapping ProviderType to engine class
    """
    
    _engines: Dict[ProviderType, Type[BaseEngine]] = {
        ProviderType.KIMI: KimiEngine,
        ProviderType.ANTHROPIC: AnthropicEngine,
    }
    
    @classmethod
    def register(cls, provider: ProviderType, engine_class: Type[BaseEngine]) -> None:
        """
        Register a new engine implementation.
        
        Args:
            provider: The provider type to register
            engine_class: The engine class implementing BaseEngine
        """
        cls._engines[provider] = engine_class
        logger.info(f"[EngineRegistry] Registered {engine_class.__name__} for provider {provider.value}")
    
    @classmethod
    def create(cls, config: EngineConfig) -> BaseEngine:
        """
        Create an engine instance from configuration.
        
        Args:
            config: Engine configuration with provider type
            
        Returns:
            Configured engine instance
            
        Raises:
            ValueError: If provider is not supported
        """
        engine_class = cls._engines.get(config.provider)
        if not engine_class:
            supported = [p.value for p in cls._engines.keys()]
            raise ValueError(
                f"Unsupported provider: {config.provider.value}. "
                f"Supported providers: {supported}"
            )
        
        return engine_class(config)
    
    @classmethod
    def get_supported_providers(cls) -> list:
        """
        Get list of supported provider types.
        
        Returns:
            List of supported ProviderType values
        """
        return list(cls._engines.keys())


def create_engine(
    provider: Optional[ProviderType] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: int = 600,
    max_iterations: int = 5,
    system_prompt: Optional[str] = None,
    config: Optional[EngineConfig] = None
) -> BaseEngine:
    """
    Factory function to create an LLM engine.
    
    This is the primary way to create an engine instance. It supports
    both passing individual parameters or a pre-built config object.
    
    Args:
        provider: LLM provider type (defaults to KIMI from env or KIMI)
        api_key: API authentication key (defaults to env var based on provider)
        base_url: API base URL (defaults to provider-specific URL)
        model: Model identifier (defaults to provider-specific default)
        temperature: Sampling temperature (0.0 - 2.0)
        max_tokens: Maximum tokens in response
        timeout: Request timeout in seconds
        max_iterations: Max tool call rounds per query
        system_prompt: Default system prompt
        config: Pre-built EngineConfig (if provided, other args are ignored)
        
    Returns:
        Configured engine instance ready for use
        
    Example:
        # Using environment variables
        engine = create_engine()
        
        # Explicit configuration
        engine = create_engine(
            provider=ProviderType.ANTHROPIC,
            api_key="your-key",
            model="kimi-k2.5"
        )
        
        # From config object
        config = EngineConfig(provider=ProviderType.KIMI, api_key="key")
        engine = create_engine(config=config)
    """
    if config is not None:
        return EngineRegistry.create(config)
    
    # Determine provider
    if provider is None:
        provider_str = os.getenv("LLM_PROVIDER", "kimi").lower()
        try:
            provider = ProviderType(provider_str)
        except ValueError:
            logger.warning(f"[EngineFactory] Unknown provider '{provider_str}', defaulting to KIMI")
            provider = ProviderType.KIMI
    
    # Get API key from environment if not provided
    if api_key is None:
        if provider == ProviderType.KIMI:
            api_key = os.getenv("KIMI_API_KEY", "")
        elif provider == ProviderType.ANTHROPIC:
            api_key = os.getenv("ANTHROPIC_API_KEY", "")
        elif provider == ProviderType.OPENAI:
            api_key = os.getenv("OPENAI_API_KEY", "")
    
    # Get base URL from environment if not provided
    if base_url is None:
        if provider == ProviderType.KIMI:
            base_url = os.getenv("KIMI_BASE_URL", "")
        elif provider == ProviderType.ANTHROPIC:
            base_url = os.getenv("ANTHROPIC_BASE_URL", "")
        elif provider == ProviderType.OPENAI:
            base_url = os.getenv("OPENAI_BASE_URL", "")
    
    # Get model from environment if not provided
    if model is None:
        if provider == ProviderType.KIMI:
            model = os.getenv("KIMI_MODEL", "kimi-latest")
        elif provider == ProviderType.ANTHROPIC:
            model = os.getenv("ANTHROPIC_MODEL", "kimi-k2.5")
        elif provider == ProviderType.OPENAI:
            model = os.getenv("OPENAI_MODEL", "gpt-4")
    
    # Build config
    engine_config = EngineConfig(
        provider=provider,
        api_key=api_key or "",
        base_url=base_url or "",
        model=model or "",
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        max_iterations=max_iterations,
        system_prompt=system_prompt or (
            "You are a helpful AI assistant with access to tools. "
            "Use the available tools when needed to provide accurate information. "
            "Always respond in the same language as the user's query."
        )
    )
    
    return EngineRegistry.create(engine_config)


def create_kimi_engine(
    api_key: Optional[str] = None,
    model: str = "kimi-latest",
    **kwargs
) -> KimiEngine:
    """
    Convenience factory for Kimi engine.
    
    Args:
        api_key: Kimi API key (defaults to KIMI_API_KEY env var)
        model: Model identifier
        **kwargs: Additional engine configuration options
        
    Returns:
        Configured KimiEngine instance
    """
    api_key = api_key or os.getenv("KIMI_API_KEY", "")
    base_url = kwargs.get("base_url") or os.getenv("KIMI_BASE_URL", "")
    
    config = EngineConfig(
        provider=ProviderType.KIMI,
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=kwargs.get("temperature", 0.7),
        max_tokens=kwargs.get("max_tokens", 4096),
        timeout=kwargs.get("timeout", 600),
        max_iterations=kwargs.get("max_iterations", 5),
        system_prompt=kwargs.get("system_prompt")
    )
    
    return KimiEngine(config)


def create_anthropic_engine(
    api_key: Optional[str] = None,
    model: str = "kimi-k2.5",
    **kwargs
) -> AnthropicEngine:
    """
    Convenience factory for Anthropic-style engine.
    
    Args:
        api_key: API key (defaults to ANTHROPIC_API_KEY env var)
        model: Model identifier
        **kwargs: Additional engine configuration options
        
    Returns:
        Configured AnthropicEngine instance
    """
    api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
    base_url = kwargs.get("base_url") or os.getenv("ANTHROPIC_BASE_URL", "")
    
    config = EngineConfig(
        provider=ProviderType.ANTHROPIC,
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=kwargs.get("temperature", 0.7),
        max_tokens=kwargs.get("max_tokens", 4096),
        timeout=kwargs.get("timeout", 600),
        max_iterations=kwargs.get("max_iterations", 5),
        system_prompt=kwargs.get("system_prompt")
    )
    
    return AnthropicEngine(config)
