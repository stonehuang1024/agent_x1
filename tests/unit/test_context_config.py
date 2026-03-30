"""Tests for ContextConfig — validation, loading, and env-var overrides.

These tests verify the *contract* of ContextConfig, not its implementation
details.  They focus on boundary conditions, invalid inputs, and the
configuration priority chain (env > yaml > defaults).
"""

import os
import logging
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from src.core.config import ContextConfig, AppConfig, load_config


# -----------------------------------------------------------------------
# Defaults
# -----------------------------------------------------------------------

class TestContextConfigDefaults:
    """ContextConfig() with no arguments must produce the documented defaults."""

    def test_token_budget_defaults(self):
        cfg = ContextConfig()
        assert cfg.context_window_tokens == 128000
        assert cfg.reserve_tokens == 4096

    def test_threshold_defaults(self):
        cfg = ContextConfig()
        assert cfg.soft_threshold == 0.7
        assert cfg.warning_threshold == 0.8
        assert cfg.critical_threshold == 0.95

    def test_truncation_defaults(self):
        cfg = ContextConfig()
        assert cfg.max_tool_output_length == 1000
        assert cfg.max_assistant_output_length == 3000

    def test_keep_recent_defaults(self):
        cfg = ContextConfig()
        assert cfg.keep_recent == 4
        assert cfg.min_keep_recent == 2

    def test_llm_summary_defaults(self):
        cfg = ContextConfig()
        assert cfg.min_summary_tokens == 2000
        assert cfg.min_summary_interval == 6
        assert cfg.summary_threshold == 20

    def test_importance_defaults(self):
        cfg = ContextConfig()
        assert cfg.low_importance_threshold == 0.3
        assert cfg.high_importance_threshold == 0.7

    def test_prune_defaults(self):
        cfg = ContextConfig()
        assert cfg.prune_minimum_tokens == 5000
        assert cfg.prune_protect_window == 8
        assert cfg.prune_preview_chars == 200

    def test_archive_defaults(self):
        cfg = ContextConfig()
        assert cfg.recall_max_tokens == 4000

    def test_adaptive_defaults(self):
        cfg = ContextConfig()
        assert cfg.frequent_summary_warning_count == 3


# -----------------------------------------------------------------------
# Custom values
# -----------------------------------------------------------------------

class TestContextConfigCustomValues:
    """Loading custom values via keyword arguments."""

    def test_custom_token_budget(self):
        cfg = ContextConfig(context_window_tokens=200000, reserve_tokens=8192)
        assert cfg.context_window_tokens == 200000
        assert cfg.reserve_tokens == 8192

    def test_custom_thresholds(self):
        cfg = ContextConfig(
            soft_threshold=0.5, warning_threshold=0.6, critical_threshold=0.8
        )
        assert cfg.soft_threshold == 0.5
        assert cfg.warning_threshold == 0.6
        assert cfg.critical_threshold == 0.8


# -----------------------------------------------------------------------
# Validation — thresholds
# -----------------------------------------------------------------------

class TestContextConfigThresholdValidation:
    """Threshold ordering and range constraints."""

    def test_soft_greater_than_warning_raises(self):
        with pytest.raises(ValueError, match="soft < warning < critical"):
            ContextConfig(soft_threshold=0.85, warning_threshold=0.8)

    def test_warning_greater_than_critical_raises(self):
        with pytest.raises(ValueError, match="soft < warning < critical"):
            ContextConfig(warning_threshold=0.96, critical_threshold=0.95)

    def test_soft_equals_warning_raises(self):
        with pytest.raises(ValueError, match="soft < warning < critical"):
            ContextConfig(soft_threshold=0.8, warning_threshold=0.8)

    def test_critical_at_boundary_1_raises(self):
        with pytest.raises(ValueError, match="critical_threshold must be in"):
            ContextConfig(critical_threshold=1.0)

    def test_critical_above_1_raises(self):
        with pytest.raises(ValueError, match="critical_threshold must be in"):
            ContextConfig(critical_threshold=1.5)

    def test_soft_at_zero_raises(self):
        with pytest.raises(ValueError, match="soft_threshold must be in"):
            ContextConfig(soft_threshold=0.0)

    def test_negative_soft_raises(self):
        with pytest.raises(ValueError, match="soft_threshold must be in"):
            ContextConfig(soft_threshold=-0.1)


# -----------------------------------------------------------------------
# Validation — token budget
# -----------------------------------------------------------------------

class TestContextConfigTokenBudgetValidation:

    def test_negative_context_window_tokens_raises(self):
        with pytest.raises(ValueError, match="context_window_tokens must be > 0"):
            ContextConfig(context_window_tokens=-1)

    def test_zero_context_window_tokens_raises(self):
        with pytest.raises(ValueError, match="context_window_tokens must be > 0"):
            ContextConfig(context_window_tokens=0)

    def test_reserve_tokens_zero_raises(self):
        with pytest.raises(ValueError, match="reserve_tokens must be in"):
            ContextConfig(reserve_tokens=0)

    def test_reserve_tokens_exceeds_window_raises(self):
        with pytest.raises(ValueError, match="reserve_tokens must be in"):
            ContextConfig(context_window_tokens=1000, reserve_tokens=1000)

    def test_reserve_tokens_greater_than_window_raises(self):
        with pytest.raises(ValueError, match="reserve_tokens must be in"):
            ContextConfig(context_window_tokens=1000, reserve_tokens=2000)


# -----------------------------------------------------------------------
# Validation — keep_recent
# -----------------------------------------------------------------------

class TestContextConfigKeepRecentValidation:

    def test_keep_recent_less_than_min_raises(self):
        with pytest.raises(ValueError, match="keep_recent.*must be >= min_keep_recent"):
            ContextConfig(keep_recent=1, min_keep_recent=2)

    def test_min_keep_recent_below_2_forced_to_2(self):
        """min_keep_recent < 2 is silently clamped to 2."""
        cfg = ContextConfig(keep_recent=4, min_keep_recent=1)
        assert cfg.min_keep_recent == 2

    def test_min_keep_recent_zero_forced_to_2(self):
        cfg = ContextConfig(keep_recent=4, min_keep_recent=0)
        assert cfg.min_keep_recent == 2

    def test_min_keep_recent_negative_forced_to_2(self):
        cfg = ContextConfig(keep_recent=4, min_keep_recent=-5)
        assert cfg.min_keep_recent == 2


# -----------------------------------------------------------------------
# Validation — other parameters
# -----------------------------------------------------------------------

class TestContextConfigOtherValidation:

    def test_max_tool_output_length_zero_raises(self):
        with pytest.raises(ValueError, match="max_tool_output_length must be > 0"):
            ContextConfig(max_tool_output_length=0)

    def test_max_tool_output_length_negative_raises(self):
        with pytest.raises(ValueError, match="max_tool_output_length must be > 0"):
            ContextConfig(max_tool_output_length=-100)

    def test_min_summary_tokens_zero_raises(self):
        with pytest.raises(ValueError, match="min_summary_tokens must be > 0"):
            ContextConfig(min_summary_tokens=0)

    def test_min_summary_interval_zero_raises(self):
        with pytest.raises(ValueError, match="min_summary_interval must be >= 1"):
            ContextConfig(min_summary_interval=0)

    def test_max_assistant_output_length_zero_is_allowed(self):
        """0 means disabled — should NOT raise."""
        cfg = ContextConfig(max_assistant_output_length=0)
        assert cfg.max_assistant_output_length == 0

    def test_max_assistant_output_length_negative_is_allowed(self):
        """Negative means disabled — should NOT raise."""
        cfg = ContextConfig(max_assistant_output_length=-1)
        assert cfg.max_assistant_output_length == -1


# -----------------------------------------------------------------------
# AppConfig integration
# -----------------------------------------------------------------------

class TestAppConfigContextIntegration:

    def test_appconfig_has_context_field(self):
        app = AppConfig()
        assert isinstance(app.context, ContextConfig)

    def test_appconfig_context_defaults_match(self):
        app = AppConfig()
        assert app.context.context_window_tokens == 128000
        assert app.context.soft_threshold == 0.7

    def test_appconfig_validate_catches_bad_context(self):
        """AppConfig.validate() must propagate ContextConfig errors."""
        app = AppConfig()
        app.llm.api_key = "test-key"  # satisfy LLM validation
        app.context.soft_threshold = 0.99  # violates ordering
        app.context.warning_threshold = 0.8
        with pytest.raises(ValueError):
            app.validate()


# -----------------------------------------------------------------------
# YAML loading
# -----------------------------------------------------------------------

class TestLoadConfigYaml:

    def _write_yaml(self, tmp_path: Path, content: str) -> str:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(content)
        return str(cfg_file)

    def test_context_section_loaded(self, tmp_path):
        yaml_content = """\
llm:
  provider: kimi
  api_key: test-key
context:
  context_window_tokens: 200000
  soft_threshold: 0.6
  warning_threshold: 0.75
  critical_threshold: 0.9
"""
        cfg_file = self._write_yaml(tmp_path, yaml_content)
        config = load_config(config_file=cfg_file, use_env=False)
        assert config.context.context_window_tokens == 200000
        assert config.context.soft_threshold == 0.6
        assert config.context.warning_threshold == 0.75
        assert config.context.critical_threshold == 0.9

    def test_missing_context_section_uses_defaults(self, tmp_path):
        yaml_content = """\
llm:
  provider: kimi
  api_key: test-key
"""
        cfg_file = self._write_yaml(tmp_path, yaml_content)
        config = load_config(config_file=cfg_file, use_env=False)
        assert config.context.context_window_tokens == 128000
        assert config.context.soft_threshold == 0.7

    def test_partial_context_section_merges_with_defaults(self, tmp_path):
        yaml_content = """\
llm:
  provider: kimi
  api_key: test-key
context:
  keep_recent: 8
"""
        cfg_file = self._write_yaml(tmp_path, yaml_content)
        config = load_config(config_file=cfg_file, use_env=False)
        assert config.context.keep_recent == 8
        # Other fields remain default
        assert config.context.context_window_tokens == 128000

    def test_unknown_context_key_ignored(self, tmp_path):
        yaml_content = """\
llm:
  provider: kimi
  api_key: test-key
context:
  nonexistent_field: 42
"""
        cfg_file = self._write_yaml(tmp_path, yaml_content)
        config = load_config(config_file=cfg_file, use_env=False)
        assert not hasattr(config.context, "nonexistent_field")


# -----------------------------------------------------------------------
# Environment variable overrides
# -----------------------------------------------------------------------

class TestContextEnvVarOverrides:

    def _base_yaml(self, tmp_path: Path) -> str:
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("""\
llm:
  provider: kimi
  api_key: test-key
context:
  context_window_tokens: 64000
""")
        return str(cfg_file)

    def test_env_overrides_yaml(self, tmp_path):
        cfg_file = self._base_yaml(tmp_path)
        with mock.patch.dict(os.environ, {"CONTEXT_WINDOW_TOKENS": "256000"}):
            config = load_config(config_file=cfg_file, use_env=True)
        assert config.context.context_window_tokens == 256000

    def test_env_float_override(self, tmp_path):
        cfg_file = self._base_yaml(tmp_path)
        with mock.patch.dict(os.environ, {"CONTEXT_SOFT_THRESHOLD": "0.65"}):
            config = load_config(config_file=cfg_file, use_env=True)
        assert config.context.soft_threshold == 0.65

    def test_invalid_env_value_ignored(self, tmp_path, caplog):
        cfg_file = self._base_yaml(tmp_path)
        with mock.patch.dict(os.environ, {"CONTEXT_WINDOW_TOKENS": "not_a_number"}):
            with caplog.at_level(logging.WARNING):
                config = load_config(config_file=cfg_file, use_env=True)
        # Should fall back to yaml value
        assert config.context.context_window_tokens == 64000
        assert "Ignoring invalid env var CONTEXT_WINDOW_TOKENS" in caplog.text

    def test_multiple_env_overrides(self, tmp_path):
        cfg_file = self._base_yaml(tmp_path)
        env = {
            "CONTEXT_KEEP_RECENT": "6",
            "CONTEXT_MIN_KEEP_RECENT": "3",
            "CONTEXT_MIN_SUMMARY_INTERVAL": "10",
        }
        with mock.patch.dict(os.environ, env):
            config = load_config(config_file=cfg_file, use_env=True)
        assert config.context.keep_recent == 6
        assert config.context.min_keep_recent == 3
        assert config.context.min_summary_interval == 10
