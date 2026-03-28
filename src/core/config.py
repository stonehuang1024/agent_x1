"""
Core Configuration Module - Unified application settings.

Provides centralized configuration management with support for:
- YAML/JSON configuration files
- Environment variables
- Programmatic configuration
- Multiple LLM providers
- Path configuration for outputs
"""

import os
import json
from typing import Dict, Any, Optional
from dataclasses import dataclass, asdict, field
from pathlib import Path
from enum import Enum


class ProviderType(Enum):
    """Supported LLM provider types."""
    KIMI = "kimi"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"


@dataclass
class LLMConfig:
    """
    LLM provider configuration.
    
    Attributes:
        provider: Provider type (kimi, anthropic, openai, etc.)
        api_key: Authentication key
        base_url: API endpoint URL
        model: Model identifier
        temperature: Sampling temperature (0.0 - 2.0)
        max_tokens: Maximum response tokens
        timeout: Request timeout in seconds
        max_iterations: Max tool call rounds per query
    """
    provider: str = "kimi"
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout: int = 600
    max_iterations: int = 10
    system_prompt: str = (
        "You are a helpful AI assistant with access to tools. "
        "Use the available tools when needed to provide accurate information. "
        "Always respond in the same language as the user's query."
    )
    
    def __post_init__(self):
        """Set defaults based on provider if not specified."""
        if not self.base_url:
            if self.provider == "kimi":
                self.base_url = "https://api.moonshot.cn/v1"
            elif self.provider == "anthropic":
                self.base_url = "https://api.kimi.com/coding/"
            elif self.provider == "openai":
                self.base_url = "https://api.openai.com/v1"
        
        if not self.model:
            if self.provider == "kimi":
                self.model = "kimi-latest"
            elif self.provider == "anthropic":
                self.model = "kimi-k2.5"
            elif self.provider == "openai":
                self.model = "gpt-4"


@dataclass
class PathConfig:
    """
    Path configuration for outputs and logs.
    
    Attributes:
        log_dir: Directory for log files
        result_dir: Directory for results
        data_dir: Directory for data files
        temp_dir: Directory for temporary files
    """
    log_dir: str = "logs"
    result_dir: str = "results"
    data_dir: str = "data"
    temp_dir: str = "tmp"
    
    def ensure_dirs(self) -> None:
        """Create all configured directories."""
        for path_attr in [self.log_dir, self.result_dir, self.data_dir, self.temp_dir]:
            Path(path_attr).mkdir(parents=True, exist_ok=True)


@dataclass
class ToolSafetyConfig:
    """
    Tool execution safety configuration.

    Global defaults for timeout and output limits.  Individual tools
    can override these with their own per-tool values.

    Attributes:
        default_timeout: Default tool execution timeout in seconds
        default_max_output: Default max output chars (~12K tokens at 50K)
        subprocess_timeout: Subprocess timeout for CLI tools (grep, fd, etc.)
    """
    default_timeout: int = 120          # 2 minutes
    default_max_output: int = 50000     # ~50K chars ≈ 12K tokens
    subprocess_timeout: int = 55        # slightly below tool-level timeout


@dataclass
class AppConfig:
    """
    Main application configuration.
    
    Centralized configuration for the entire agent system.
    
    Attributes:
        llm: LLM provider configuration
        paths: Path configuration for outputs
        tool_safety: Tool execution safety configuration
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    llm: LLMConfig = field(default_factory=LLMConfig)
    paths: PathConfig = field(default_factory=PathConfig)
    tool_safety: ToolSafetyConfig = field(default_factory=ToolSafetyConfig)
    log_level: str = "INFO"
    
    def validate(self) -> None:
        """
        Validate configuration.
        
        Raises:
            ValueError: If required settings are missing
        """
        # Check API key based on provider
        env_var_map = {
            "kimi": "MOONSHOT_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
        }
        
        # Prioritize environment variable over config file
        env_var = env_var_map.get(self.llm.provider, f"{self.llm.provider.upper()}_API_KEY")
        env_api_key = os.getenv(env_var, "")
        if env_api_key:
            self.llm.api_key = env_api_key
        elif not self.llm.api_key or self.llm.api_key == "your-api-key-here":
            self.llm.api_key = ""
        
        if not self.llm.api_key:
            raise ValueError(
                f"API key required for provider '{self.llm.provider}'. "
                f"Set via: 1) Config file 'llm.api_key', "
                f"2) {env_var_map.get(self.llm.provider, 'PROVIDER_API_KEY')} env var, or "
                f"3) --api-key command line argument"
            )
        
        # Validate temperature
        if not 0.0 <= self.llm.temperature <= 2.0:
            raise ValueError("Temperature must be between 0.0 and 2.0")
        
        # Validate timeout and iterations
        if self.llm.timeout < 1:
            raise ValueError("Timeout must be at least 1 second")
        if self.llm.max_iterations < 1:
            raise ValueError("Max iterations must be at least 1")
        
        # Validate log level
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self.log_level.upper() not in valid_levels:
            raise ValueError(f"Invalid log level: {self.log_level}. Must be one of {valid_levels}")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        """Create from dictionary with support for new provider-specific config."""
        provider = data.get("provider", "kimi")
        providers_data = data.get("providers", {})
        
        # Get provider-specific config
        provider_config = providers_data.get(provider, {})
        
        # Build LLMConfig from provider-specific settings
        llm_data = {
            "provider": provider,
            "api_key": provider_config.get("api_key", ""),
            "base_url": provider_config.get("base_url", ""),
            "model": provider_config.get("model", ""),
            "temperature": provider_config.get("temperature", 0.7),
            "max_tokens": provider_config.get("max_tokens", 4096),
            "timeout": data.get("timeout", 30),
            "max_iterations": data.get("max_iterations", 10),
            "system_prompt": data.get("system_prompt", (
                "You are a helpful AI assistant with access to tools. "
                "Use the available tools when needed to provide accurate information. "
                "Always respond in the same language as the user's query."
            ))
        }
        
        # Override with legacy llm config if present (for backward compatibility)
        legacy_llm = data.get("llm", {})
        if legacy_llm:
            if legacy_llm.get("provider"):
                llm_data["provider"] = legacy_llm["provider"]
            if legacy_llm.get("api_key"):
                llm_data["api_key"] = legacy_llm["api_key"]
            if legacy_llm.get("base_url"):
                llm_data["base_url"] = legacy_llm["base_url"]
            if legacy_llm.get("model"):
                llm_data["model"] = legacy_llm["model"]
            if "temperature" in legacy_llm:
                llm_data["temperature"] = legacy_llm["temperature"]
            if "max_tokens" in legacy_llm:
                llm_data["max_tokens"] = legacy_llm["max_tokens"]
            if "timeout" in legacy_llm:
                llm_data["timeout"] = legacy_llm["timeout"]
            if "max_iterations" in legacy_llm:
                llm_data["max_iterations"] = legacy_llm["max_iterations"]
            if "system_prompt" in legacy_llm:
                llm_data["system_prompt"] = legacy_llm["system_prompt"]
        
        paths_data = data.get("paths", {})
        tool_safety_data = data.get("tool_safety", {})
        
        return cls(
            llm=LLMConfig(**llm_data),
            paths=PathConfig(**paths_data),
            tool_safety=ToolSafetyConfig(**tool_safety_data),
            log_level=data.get("log_level", "INFO")
        )


def load_yaml_config(filepath: str) -> Dict[str, Any]:
    """
    Load configuration from YAML file.
    
    Args:
        filepath: Path to YAML file
        
    Returns:
        Configuration dictionary
        
    Raises:
        ImportError: If PyYAML not installed
        FileNotFoundError: If file doesn't exist
    """
    try:
        import yaml
    except ImportError:
        raise ImportError(
            "PyYAML required. Install with: pip install pyyaml"
        )
    
    with open(filepath, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def load_json_config(filepath: str) -> Dict[str, Any]:
    """
    Load configuration from JSON file.
    
    Args:
        filepath: Path to JSON file
        
    Returns:
        Configuration dictionary
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_from_env() -> Dict[str, Any]:
    """
    Load configuration from environment variables.
    
    Environment variables:
    - LLM_PROVIDER -> llm.provider
    - KIMI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY -> llm.api_key
    - KIMI_BASE_URL / ANTHROPIC_BASE_URL -> llm.base_url
    - KIMI_MODEL / ANTHROPIC_MODEL -> llm.model
    - LLM_TEMPERATURE -> llm.temperature
    - LLM_TIMEOUT -> llm.timeout
    - LLM_MAX_ITERATIONS -> llm.max_iterations
    - LOG_LEVEL -> log_level
    - LOG_DIR -> paths.log_dir
    - RESULT_DIR -> paths.result_dir
    
    Returns:
        Configuration dictionary
    """
    config: Dict[str, Any] = {"llm": {}, "paths": {}}
    
    # Provider
    provider = os.getenv("LLM_PROVIDER")
    if provider:
        config["llm"]["provider"] = provider
    
    # API keys based on provider
    provider_key_map = {
        "kimi": ("KIMI_API_KEY", "KIMI_BASE_URL", "KIMI_MODEL"),
        "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL"),
        "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"),
    }
    
    effective_provider = provider or config.get("llm", {}).get("provider", "kimi")
    
    api_key_var, base_url_var, model_var = provider_key_map.get(
        effective_provider, 
        (f"{effective_provider.upper()}_API_KEY", 
         f"{effective_provider.upper()}_BASE_URL",
         f"{effective_provider.upper()}_MODEL")
    )
    
    if os.getenv(api_key_var):
        config["llm"]["api_key"] = os.getenv(api_key_var)
    if os.getenv(base_url_var):
        config["llm"]["base_url"] = os.getenv(base_url_var)
    if os.getenv(model_var):
        config["llm"]["model"] = os.getenv(model_var)
    
    # Generic LLM settings
    if os.getenv("LLM_TEMPERATURE"):
        config["llm"]["temperature"] = float(os.getenv("LLM_TEMPERATURE"))
    if os.getenv("LLM_TIMEOUT"):
        config["llm"]["timeout"] = int(os.getenv("LLM_TIMEOUT"))
    if os.getenv("LLM_MAX_ITERATIONS"):
        config["llm"]["max_iterations"] = int(os.getenv("LLM_MAX_ITERATIONS"))
    if os.getenv("LLM_SYSTEM_PROMPT"):
        config["llm"]["system_prompt"] = os.getenv("LLM_SYSTEM_PROMPT")
    
    # Logging and paths
    if os.getenv("LOG_LEVEL"):
        config["log_level"] = os.getenv("LOG_LEVEL")
    if os.getenv("LOG_DIR"):
        config["paths"]["log_dir"] = os.getenv("LOG_DIR")
    if os.getenv("RESULT_DIR"):
        config["paths"]["result_dir"] = os.getenv("RESULT_DIR")
    if os.getenv("DATA_DIR"):
        config["paths"]["data_dir"] = os.getenv("DATA_DIR")
    
    return config


def find_config_file(config_dir: str = "config") -> Optional[str]:
    """
    Find configuration file in directory.
    
    Searches for: config.yaml, config.yml, config.json
    
    Args:
        config_dir: Directory to search
        
    Returns:
        Path to found file or None
    """
    dir_path = Path(config_dir)
    
    for filename in ['config.yaml', 'config.yml', 'config.json']:
        filepath = dir_path / filename
        if filepath.exists():
            return str(filepath)
    
    return None


def load_config(
    config_file: Optional[str] = None,
    use_env: bool = True
) -> AppConfig:
    """
    Load configuration from all sources.
    
    Priority (highest to lowest):
    1. Environment variables
    2. Configuration file
    3. Default values
    
    Args:
        config_file: Explicit config file path
        use_env: Whether to load from environment
        
    Returns:
        Validated AppConfig instance
    """
    # Start with defaults
    config = AppConfig()
    
    # Load from file
    file_config = {}
    if config_file:
        filepath = Path(config_file)
        if not filepath.exists():
            raise FileNotFoundError(f"Config file not found: {config_file}")
        
        if filepath.suffix in ['.yaml', '.yml']:
            file_config = load_yaml_config(str(filepath))
        elif filepath.suffix == '.json':
            file_config = load_json_config(str(filepath))
    else:
        found = find_config_file()
        if found:
            if found.endswith(('.yaml', '.yml')):
                file_config = load_yaml_config(found)
            else:
                file_config = load_json_config(found)
    
    # Apply file config with support for new structure
    if file_config:
        # Check for new provider-specific structure
        if "providers" in file_config:
            provider = file_config.get("provider", config.llm.provider)
            providers_data = file_config.get("providers", {})
            provider_config = providers_data.get(provider, {})
            
            # Apply provider-specific settings
            config.llm.provider = provider
            if provider_config.get("api_key"):
                config.llm.api_key = provider_config["api_key"]
            if provider_config.get("base_url"):
                config.llm.base_url = provider_config["base_url"]
            if provider_config.get("model"):
                config.llm.model = provider_config["model"]
            if "temperature" in provider_config:
                config.llm.temperature = provider_config["temperature"]
            if "max_tokens" in provider_config:
                config.llm.max_tokens = provider_config["max_tokens"]
            
            # Apply global settings
            if "timeout" in file_config:
                config.llm.timeout = file_config["timeout"]
            if "max_iterations" in file_config:
                config.llm.max_iterations = file_config["max_iterations"]
            if "system_prompt" in file_config:
                config.llm.system_prompt = file_config["system_prompt"]
            if "log_level" in file_config:
                config.log_level = file_config["log_level"]
        
        # Apply tool_safety config
        if "tool_safety" in file_config:
            for key, value in file_config["tool_safety"].items():
                if hasattr(config.tool_safety, key):
                    setattr(config.tool_safety, key, value)
        
        # Legacy config structure (backward compatibility)
        if "llm" in file_config:
            for key, value in file_config["llm"].items():
                if hasattr(config.llm, key):
                    setattr(config.llm, key, value)
        if "paths" in file_config:
            for key, value in file_config["paths"].items():
                if hasattr(config.paths, key):
                    setattr(config.paths, key, value)
    
    # Apply environment variables (highest priority)
    if use_env:
        env_config = load_from_env()
        if "llm" in env_config:
            for key, value in env_config["llm"].items():
                if hasattr(config.llm, key):
                    setattr(config.llm, key, value)
        if "paths" in env_config:
            for key, value in env_config["paths"].items():
                if hasattr(config.paths, key):
                    setattr(config.paths, key, value)
        if "log_level" in env_config:
            config.log_level = env_config["log_level"]
    
    # Apply tool_safety env vars (highest priority)
    if os.getenv("TOOL_DEFAULT_TIMEOUT"):
        config.tool_safety.default_timeout = int(os.getenv("TOOL_DEFAULT_TIMEOUT"))
    if os.getenv("TOOL_DEFAULT_MAX_OUTPUT"):
        config.tool_safety.default_max_output = int(os.getenv("TOOL_DEFAULT_MAX_OUTPUT"))
    if os.getenv("TOOL_SUBPROCESS_TIMEOUT"):
        config.tool_safety.subprocess_timeout = int(os.getenv("TOOL_SUBPROCESS_TIMEOUT"))
    
    # Validate
    config.validate()
    
    return config


def create_default_config_file(filepath: str = "config/config.yaml") -> None:
    """Create default configuration file with provider-specific settings."""
    default_config = """# Agent X1 Configuration
# Main configuration file for the agent system

# LLM Provider Configuration
# Set which provider to use: kimi, anthropic, openai
provider: "anthropic"

# Provider-specific configurations
providers:
  kimi:
    api_key: ""  # Set via KIMI_API_KEY env var or provide here
    base_url: "https://api.moonshot.cn/v1"
    model: "moonshot-v1-32k"
    temperature: 0.7
    max_tokens: 4096
    
  anthropic:
    api_key: ""  # Set via ANTHROPIC_API_KEY env var or provide here
    base_url: "https://api.kimi.com/coding/"
    model: "kimi-k2.5"
    temperature: 0.7
    max_tokens: 4096
    
  openai:
    api_key: ""  # Set via OPENAI_API_KEY env var or provide here
    base_url: "https://api.openai.com/v1"
    model: "gpt-4"
    temperature: 0.7
    max_tokens: 4096

# Global engine settings (can be overridden by provider-specific settings above)
timeout: 600
max_iterations: 10

# Tool Safety Configuration
# Global defaults for tool execution safety limits.
# Individual tools can override these with their own values.
tool_safety:
  default_timeout: 120          # Default tool execution timeout in seconds
  default_max_output: 50000     # Default max output chars (~12K tokens)
  subprocess_timeout: 55        # Subprocess timeout for CLI tools (grep, fd, etc.)

# System prompt
system_prompt: |
  You are a helpful AI assistant with access to tools.
  Use the available tools when needed to provide accurate information.
  Always respond in the same language as the user's query.

# Path Configuration
paths:
  log_dir: "logs"
  result_dir: "results"
  data_dir: "data"
  temp_dir: "tmp"

# Logging
log_level: "INFO"
"""
    
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(default_config)
