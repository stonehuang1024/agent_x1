"""Token budget management for context window."""

import json
import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Callable, TYPE_CHECKING

from src.core.models import Message

if TYPE_CHECKING:
    from src.core.config import ContextConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional tiktoken integration
# ---------------------------------------------------------------------------

_tiktoken_encoder = None
_tiktoken_checked = False


def _try_tiktoken():
    """Try to load tiktoken for precise token counting. Returns encoder or None."""
    global _tiktoken_encoder, _tiktoken_checked
    if _tiktoken_checked:
        return _tiktoken_encoder
    _tiktoken_checked = True
    try:
        import tiktoken
        _tiktoken_encoder = tiktoken.encoding_for_model("gpt-4")
        logger.debug("tiktoken available — using precise token counting")
    except Exception:
        _tiktoken_encoder = None
        logger.debug("tiktoken not available — using estimation algorithm")
    return _tiktoken_encoder


# ---------------------------------------------------------------------------
# Budget configuration
# ---------------------------------------------------------------------------

class CompressionLevel(Enum):
    """Compression urgency level based on context utilization."""
    NONE = "none"          # utilization < soft_threshold
    SOFT = "soft"          # soft_threshold <= utilization < warning_threshold
    WARNING = "warning"    # warning_threshold <= utilization < critical_threshold
    CRITICAL = "critical"  # utilization >= critical_threshold


@dataclass
class ContextBudget:
    """Token budget configuration.
    
    Aligned with Kimi K2.5 context window (64K tokens).
    reserve_tokens ensures headroom for response generation.
    """
    max_tokens: int = 64000
    reserve_tokens: int = 4096
    soft_threshold: float = 0.7
    warning_threshold: float = 0.8
    critical_threshold: float = 0.95
    keep_recent: int = 4
    min_keep_recent: int = 2
    
    @property
    def available_for_context(self) -> int:
        return self.max_tokens - self.reserve_tokens

    @classmethod
    def from_context_config(cls, ctx_config: "ContextConfig") -> "ContextBudget":
        """Build a ContextBudget from a ContextConfig object."""
        return cls(
            max_tokens=ctx_config.context_window_tokens,
            reserve_tokens=ctx_config.reserve_tokens,
            soft_threshold=ctx_config.soft_threshold,
            warning_threshold=ctx_config.warning_threshold,
            critical_threshold=ctx_config.critical_threshold,
            keep_recent=ctx_config.keep_recent,
            min_keep_recent=ctx_config.min_keep_recent,
        )


# ---------------------------------------------------------------------------
# Context window manager
# ---------------------------------------------------------------------------

# Per-message overhead: role tag + formatting delimiters ≈ 4 tokens
_MSG_OVERHEAD = 4
# Conversation-level priming overhead ≈ 3 tokens
_PRIMING_OVERHEAD = 3
# Characters-per-token ratio for estimation (closer to real tokenizer)
_CHARS_PER_TOKEN = 3.5


class ContextWindow:
    """Manages token budget for context assembly.

    Improvements over the naive estimator:
    - Uses len(content) / 3.5 instead of len(content) // 4
    - Accounts for tool_calls JSON serialization
    - Adds per-message overhead (4 tokens) and priming overhead (3 tokens)
    - Writes estimated token_count back to each Message
    - Optionally uses tiktoken for precise counting
    """
    
    def __init__(
        self,
        budget: Optional[ContextBudget] = None,
        *,
        context_config: Optional["ContextConfig"] = None,
    ):
        if context_config is not None:
            self.budget = ContextBudget.from_context_config(context_config)
        elif budget is not None:
            self.budget = budget
        else:
            self.budget = ContextBudget()
        self._current_usage = 0
        self._message_counts: List[int] = []
    
    def reset(self):
        """Reset token usage for a new build cycle."""
        self._current_usage = 0
        self._message_counts.clear()

    def estimate_tokens(self, messages: List[Message]) -> int:
        """Estimate token count for a list of messages.

        Algorithm per message:
          - text content:  len(content) / 3.5
          - tool_calls:    len(json.dumps(tool_calls)) / 3.5
          - per-message:   +4 tokens (role + formatting)
        Plus 3 tokens priming overhead for the whole conversation.
        """
        encoder = _try_tiktoken()
        total = _PRIMING_OVERHEAD

        for msg in messages:
            msg_tokens = _MSG_OVERHEAD  # role + formatting

            # Content tokens
            content = msg.content or ""
            if encoder is not None:
                msg_tokens += len(encoder.encode(content))
            else:
                msg_tokens += math.ceil(len(content) / _CHARS_PER_TOKEN)

            # tool_calls tokens
            if msg.tool_calls:
                try:
                    tc_json = json.dumps(msg.tool_calls)
                except (TypeError, ValueError):
                    tc_json = str(msg.tool_calls)
                if encoder is not None:
                    msg_tokens += len(encoder.encode(tc_json))
                else:
                    msg_tokens += math.ceil(len(tc_json) / _CHARS_PER_TOKEN)

            total += msg_tokens

        return total

    def estimate_single(self, msg: Message) -> int:
        """Estimate token count for a single message (no priming overhead)."""
        encoder = _try_tiktoken()
        msg_tokens = _MSG_OVERHEAD

        content = msg.content or ""
        if encoder is not None:
            msg_tokens += len(encoder.encode(content))
        else:
            msg_tokens += math.ceil(len(content) / _CHARS_PER_TOKEN)

        if msg.tool_calls:
            try:
                tc_json = json.dumps(msg.tool_calls)
            except (TypeError, ValueError):
                tc_json = str(msg.tool_calls)
            if encoder is not None:
                msg_tokens += len(encoder.encode(tc_json))
            else:
                msg_tokens += math.ceil(len(tc_json) / _CHARS_PER_TOKEN)

        return msg_tokens
    
    def fits(self, messages: List[Message]) -> bool:
        """Check if messages fit within remaining budget."""
        needed = self.estimate_tokens(messages)
        fits = (self._current_usage + needed) <= self.budget.available_for_context
        # DEBUG: Budget check
        logger.debug(
            "[ContextWindow] Budget check | max=%d | used=%d | available=%d | needed=%d | fits=%s | should_compress=%s",
            self.budget.available_for_context, self._current_usage,
            self.budget.available_for_context - self._current_usage,
            needed, fits, self.should_compress()
        )
        return fits
    
    def remaining(self) -> int:
        """Remaining token budget."""
        return self.budget.available_for_context - self._current_usage
    
    def utilization(self) -> float:
        """Current utilization ratio (0.0 – 1.0)."""
        avail = self.budget.available_for_context
        if avail <= 0:
            return 1.0
        return self._current_usage / avail
    
    def should_warn(self) -> bool:
        """True when utilization exceeds warning threshold."""
        return self.utilization() >= self.budget.warning_threshold
    
    def should_compress(self) -> bool:
        """True when utilization exceeds critical threshold."""
        return self.utilization() >= self.budget.critical_threshold

    def compression_level(self) -> CompressionLevel:
        """Return the current compression urgency level."""
        util = self.utilization()
        if util >= self.budget.critical_threshold:
            return CompressionLevel.CRITICAL
        if util >= self.budget.warning_threshold:
            return CompressionLevel.WARNING
        if util >= self.budget.soft_threshold:
            return CompressionLevel.SOFT
        return CompressionLevel.NONE

    def get_dynamic_keep_recent(self) -> int:
        """Return keep_recent adjusted for current compression pressure."""
        level = self.compression_level()
        default = self.budget.keep_recent
        if level in (CompressionLevel.WARNING, CompressionLevel.CRITICAL):
            adjusted = self.budget.min_keep_recent
            if adjusted != default:
                logger.info(
                    "Dynamic keep_recent adjusted: %d -> %d (level=%s)",
                    default, adjusted, level.value,
                )
            return adjusted
        return default
    
    def add(self, messages: List[Message]) -> bool:
        """Add messages to the window, updating token counts.

        Returns False (and does NOT modify state) if messages exceed budget.
        On success, writes estimated token_count into each Message.
        """
        needed = self.estimate_tokens(messages)
        if self._current_usage + needed > self.budget.available_for_context:
            return False

        # Write token_count into each message
        for msg in messages:
            msg.token_count = self.estimate_single(msg)

        self._current_usage += needed
        self._message_counts.append(needed)
        return True
    
    def remove(self, count: int = 1) -> int:
        """Remove the last *count* add() batches, freeing their tokens."""
        if count > len(self._message_counts):
            count = len(self._message_counts)
        freed = sum(self._message_counts[-count:])
        self._message_counts = self._message_counts[:-count]
        self._current_usage -= freed
        return freed
