"""Tests for CompressionLevel, ContextBudget.from_context_config, and dynamic keep_recent.

Focus: boundary conditions for threshold-based level selection and
keep_recent adjustment under pressure.
"""

import pytest

from src.core.config import ContextConfig
from src.context.context_window import (
    CompressionLevel,
    ContextBudget,
    ContextWindow,
)
from src.core.models import Message


# -----------------------------------------------------------------------
# CompressionLevel enum
# -----------------------------------------------------------------------

class TestCompressionLevelEnum:

    def test_values(self):
        assert CompressionLevel.NONE.value == "none"
        assert CompressionLevel.SOFT.value == "soft"
        assert CompressionLevel.WARNING.value == "warning"
        assert CompressionLevel.CRITICAL.value == "critical"

    def test_member_count(self):
        assert len(CompressionLevel) == 4


# -----------------------------------------------------------------------
# ContextBudget.from_context_config
# -----------------------------------------------------------------------

class TestContextBudgetFromConfig:

    def test_maps_all_fields(self):
        cfg = ContextConfig(
            context_window_tokens=200000,
            reserve_tokens=8192,
            soft_threshold=0.6,
            warning_threshold=0.75,
            critical_threshold=0.9,
            keep_recent=6,
            min_keep_recent=3,
        )
        budget = ContextBudget.from_context_config(cfg)
        assert budget.max_tokens == 200000
        assert budget.reserve_tokens == 8192
        assert budget.soft_threshold == 0.6
        assert budget.warning_threshold == 0.75
        assert budget.critical_threshold == 0.9
        assert budget.keep_recent == 6
        assert budget.min_keep_recent == 3

    def test_default_config_produces_default_budget(self):
        budget = ContextBudget.from_context_config(ContextConfig())
        assert budget.max_tokens == 128000
        assert budget.soft_threshold == 0.7


# -----------------------------------------------------------------------
# ContextWindow — backward compatibility
# -----------------------------------------------------------------------

class TestContextWindowBackwardCompat:

    def test_old_style_init_still_works(self):
        budget = ContextBudget(max_tokens=64000)
        win = ContextWindow(budget)
        assert win.budget.max_tokens == 64000

    def test_context_config_init(self):
        cfg = ContextConfig(context_window_tokens=200000)
        win = ContextWindow(context_config=cfg)
        assert win.budget.max_tokens == 200000

    def test_default_init(self):
        win = ContextWindow()
        assert win.budget.max_tokens == 64000  # ContextBudget default


# -----------------------------------------------------------------------
# compression_level()
# -----------------------------------------------------------------------

class TestCompressionLevelMethod:
    """Verify level boundaries.

    Thresholds: soft=0.7, warning=0.8, critical=0.95
    available_for_context = 64000 - 4096 = 59904
    """

    def _window_at_utilization(self, util: float) -> ContextWindow:
        import math
        budget = ContextBudget(max_tokens=64000, reserve_tokens=4096)
        win = ContextWindow(budget)
        # Use ceil to guarantee utilization >= target ratio
        win._current_usage = math.ceil(budget.available_for_context * util)
        return win

    def test_zero_utilization_is_none(self):
        win = self._window_at_utilization(0.0)
        assert win.compression_level() == CompressionLevel.NONE

    def test_50_percent_is_none(self):
        win = self._window_at_utilization(0.50)
        assert win.compression_level() == CompressionLevel.NONE

    def test_69_percent_is_none(self):
        win = self._window_at_utilization(0.69)
        assert win.compression_level() == CompressionLevel.NONE

    def test_exactly_soft_threshold_is_soft(self):
        win = self._window_at_utilization(0.70)
        assert win.compression_level() == CompressionLevel.SOFT

    def test_72_percent_is_soft(self):
        win = self._window_at_utilization(0.72)
        assert win.compression_level() == CompressionLevel.SOFT

    def test_exactly_warning_threshold_is_warning(self):
        win = self._window_at_utilization(0.80)
        assert win.compression_level() == CompressionLevel.WARNING

    def test_85_percent_is_warning(self):
        win = self._window_at_utilization(0.85)
        assert win.compression_level() == CompressionLevel.WARNING

    def test_exactly_critical_threshold_is_critical(self):
        win = self._window_at_utilization(0.95)
        assert win.compression_level() == CompressionLevel.CRITICAL

    def test_96_percent_is_critical(self):
        win = self._window_at_utilization(0.96)
        assert win.compression_level() == CompressionLevel.CRITICAL


# -----------------------------------------------------------------------
# get_dynamic_keep_recent()
# -----------------------------------------------------------------------

class TestDynamicKeepRecent:

    def _window_at_utilization(
        self, util: float, keep_recent: int = 4, min_keep_recent: int = 2
    ) -> ContextWindow:
        import math
        budget = ContextBudget(
            max_tokens=64000,
            reserve_tokens=4096,
            keep_recent=keep_recent,
            min_keep_recent=min_keep_recent,
        )
        win = ContextWindow(budget)
        win._current_usage = math.ceil(budget.available_for_context * util)
        return win

    def test_none_level_returns_default(self):
        win = self._window_at_utilization(0.50)
        assert win.get_dynamic_keep_recent() == 4

    def test_soft_level_returns_default(self):
        win = self._window_at_utilization(0.72)
        assert win.get_dynamic_keep_recent() == 4

    def test_warning_level_returns_min(self):
        win = self._window_at_utilization(0.85)
        assert win.get_dynamic_keep_recent() == 2

    def test_critical_level_returns_min(self):
        win = self._window_at_utilization(0.96)
        assert win.get_dynamic_keep_recent() == 2

    def test_custom_keep_recent_values(self):
        win = self._window_at_utilization(0.50, keep_recent=8, min_keep_recent=3)
        assert win.get_dynamic_keep_recent() == 8
        win2 = self._window_at_utilization(0.85, keep_recent=8, min_keep_recent=3)
        assert win2.get_dynamic_keep_recent() == 3
