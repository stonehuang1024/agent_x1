"""Context compression strategies.

Implements a three-level compression pipeline:
  Level 1: Summarize old assistant messages (keep recent N uncompressed)
  Level 2: Remove low-importance messages (importance < threshold)
  Level 3: Emergency truncation (keep only system + recent N messages)

Also provides smart tool-output truncation that preserves head + tail.
"""

import logging
import math
import re
from typing import List, Optional, Tuple, Callable

from src.core.models import Message, Role

logger = logging.getLogger(__name__)

# Default token estimator: len(content) / 3.5 + 4 overhead per message
_DEFAULT_CHARS_PER_TOKEN = 3.5
_DEFAULT_MSG_OVERHEAD = 4


def _default_token_estimator(messages: List[Message]) -> int:
    """Fallback token estimator when no external estimator is provided."""
    total = 3  # priming overhead
    for msg in messages:
        content_len = len(msg.content or "")
        total += math.ceil(content_len / _DEFAULT_CHARS_PER_TOKEN) + _DEFAULT_MSG_OVERHEAD
    return total


class ContextCompressor:
    """Compresses conversation history to fit token budget.

    Three-level compression strategy:
      1. Summarize old assistant messages (beyond keep_recent window)
      2. Greedy selection by importance score (drop low-importance messages)
      3. Emergency truncation (system messages + last N messages only)
    """

    def __init__(
        self,
        max_tool_output_length: int = 1000,
        summary_threshold: int = 20,
        keep_recent: int = 4,
        token_estimator: Optional[Callable[[List[Message]], int]] = None,
        low_importance_threshold: float = 0.4,
    ):
        self.max_tool_output_length = max_tool_output_length
        self.summary_threshold = summary_threshold
        self.keep_recent = keep_recent
        self.token_estimator = token_estimator or _default_token_estimator
        self.low_importance_threshold = low_importance_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compress_messages(self, messages: List[Message]) -> List[Message]:
        """Truncate long tool outputs using head+tail preservation.

        For messages whose content exceeds ``max_tool_output_length * 2``,
        keeps the first and last ``max_tool_output_length / 2`` characters
        with a truncation marker in between.
        """
        compressed = []
        for msg in messages:
            content = msg.content or ""
            if len(content) > self.max_tool_output_length * 2:
                half = self.max_tool_output_length // 2
                truncated_chars = len(content) - self.max_tool_output_length
                head = content[:half]
                tail = content[-half:]
                truncated = f"{head}\n[... {truncated_chars} chars truncated ...]\n{tail}"
                compressed.append(Message(
                    role=msg.role,
                    content=truncated,
                    tool_calls=msg.tool_calls,
                    tool_call_id=msg.tool_call_id,
                    name=msg.name,
                    token_count=msg.token_count,
                    importance=msg.importance,
                ))
            else:
                compressed.append(msg)
        return compressed

    def compress_history(
        self,
        messages: List[Message],
        target_tokens: Optional[int] = None,
    ) -> Tuple[List[Message], Optional[str]]:
        """Compress conversation history using three-level strategy.

        Args:
            messages: Full message list (system + conversation + user).
            target_tokens: Token budget. If None, uses summary_threshold
                           as a message-count heuristic.

        Returns:
            Tuple of (compressed messages, optional summary string).
            The summary is non-None when messages were dropped/summarized.
        """
        if not messages:
            return [], None

        # DEBUG: Compressing
        input_tokens = self.token_estimator(messages)
        logger.debug(
            "[ContextCompressor] Compressing | input_messages=%d | input_tokens=%d | target_tokens=%s",
            len(messages), input_tokens, target_tokens
        )

        # Short conversations don't need compression
        if target_tokens is None and len(messages) <= self.summary_threshold:
            return messages, None

        # Separate system messages (always kept) and recent messages
        system_msgs = [m for m in messages if m.role == Role.SYSTEM.value]
        non_system = [m for m in messages if m.role != Role.SYSTEM.value]

        if len(non_system) <= self.keep_recent:
            return messages, None

        recent_msgs = non_system[-self.keep_recent:]
        old_msgs = non_system[:-self.keep_recent]

        # ----- Level 1: Summarize old assistant messages -----
        summarized_old, level1_dropped = self._summarize_old_assistants(old_msgs)

        candidate = system_msgs + summarized_old + recent_msgs
        if target_tokens is not None and self.token_estimator(candidate) <= target_tokens:
            summary = self._generate_structured_summary(level1_dropped) if level1_dropped else None
            return candidate, summary

        # ----- Level 2: Greedy selection by importance -----
        selected_old, level2_dropped = self._greedy_select_by_importance(
            summarized_old, target_tokens, system_msgs, recent_msgs
        )

        all_dropped = level1_dropped + level2_dropped
        candidate = system_msgs + selected_old + recent_msgs
        if target_tokens is not None and self.token_estimator(candidate) <= target_tokens:
            summary = self._generate_structured_summary(all_dropped) if all_dropped else None
            return candidate, summary

        # ----- Level 3: Emergency — system + recent only -----
        summary = self._generate_structured_summary(old_msgs)
        summary_msg = Message.system(f"[Conversation Summary]\n{summary}")
        result = system_msgs + [summary_msg] + recent_msgs

        # DEBUG: Compressed
        output_tokens = self.token_estimator(result)
        compression_ratio = (output_tokens / max(input_tokens, 1)) * 100
        logger.debug(
            "[ContextCompressor] Compressed | output_messages=%d | output_tokens=%d | compression_ratio=%.1f%%",
            len(result), output_tokens, compression_ratio
        )

        return result, summary

    def truncate_for_emergency(
        self,
        messages: List[Message],
        max_messages: Optional[int] = None,
        max_chars: Optional[int] = None,
    ) -> List[Message]:
        """Emergency truncation — keep system messages + most recent N.

        Args:
            messages: Full message list.
            max_messages: Maximum number of non-system messages to keep.
                          Defaults to ``keep_recent``.
            max_chars: Legacy parameter — character budget (if set, overrides
                       max_messages with a char-based approach).

        Returns:
            Truncated message list.
        """
        system_msgs = [m for m in messages if m.role == Role.SYSTEM.value]
        non_system = [m for m in messages if m.role != Role.SYSTEM.value]

        if max_chars is not None:
            # Legacy char-based approach
            total = sum(len(m.content or "") for m in system_msgs)
            result = list(system_msgs)
            for msg in reversed(non_system):
                msg_len = len(msg.content or "")
                if total + msg_len <= max_chars:
                    total += msg_len
                    result.insert(len(system_msgs), msg)
            return result

        # Message-count approach
        keep = max_messages if max_messages is not None else self.keep_recent
        kept_non_system = non_system[-keep:] if len(non_system) > keep else non_system
        return system_msgs + kept_non_system

    # ------------------------------------------------------------------
    # Level 1: Summarize old assistant messages
    # ------------------------------------------------------------------

    def _summarize_old_assistants(
        self, old_msgs: List[Message]
    ) -> Tuple[List[Message], List[Message]]:
        """Replace old assistant messages with a summary placeholder.

        Returns:
            (remaining messages, dropped assistant messages)
        """
        remaining = []
        dropped = []

        for msg in old_msgs:
            if msg.role == Role.ASSISTANT.value and not msg.tool_calls:
                dropped.append(msg)
            else:
                remaining.append(msg)

        if dropped:
            placeholder = Message.system(
                f"[... {len(dropped)} earlier assistant messages summarized ...]"
            )
            remaining.insert(0, placeholder)

        return remaining, dropped

    # ------------------------------------------------------------------
    # Level 2: Greedy selection by importance
    # ------------------------------------------------------------------

    def _greedy_select_by_importance(
        self,
        old_msgs: List[Message],
        target_tokens: Optional[int],
        system_msgs: List[Message],
        recent_msgs: List[Message],
    ) -> Tuple[List[Message], List[Message]]:
        """Select high-importance messages greedily until budget is met.

        Returns:
            (selected messages in original order, dropped messages)
        """
        if not old_msgs:
            return [], []

        if target_tokens is None:
            # No token budget — drop messages below importance threshold
            selected = [m for m in old_msgs if m.importance >= self.low_importance_threshold]
            dropped = [m for m in old_msgs if m.importance < self.low_importance_threshold]
            return selected, dropped

        # Budget available for old messages
        fixed_tokens = self.token_estimator(system_msgs + recent_msgs)
        available = max(0, target_tokens - fixed_tokens)

        # Sort by importance (descending), keeping original indices for order
        indexed = [(i, msg) for i, msg in enumerate(old_msgs)]
        indexed.sort(key=lambda x: x[1].importance, reverse=True)

        selected_indices = set()
        current_tokens = 0

        for idx, msg in indexed:
            msg_tokens = self.token_estimator([msg])
            if current_tokens + msg_tokens <= available:
                selected_indices.add(idx)
                current_tokens += msg_tokens

        # Preserve original order
        selected = [msg for i, msg in enumerate(old_msgs) if i in selected_indices]
        dropped = [msg for i, msg in enumerate(old_msgs) if i not in selected_indices]

        return selected, dropped

    # ------------------------------------------------------------------
    # Structured summary generation
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_structured_summary(messages: List[Message]) -> str:
        """Generate a structured summary of dropped messages.

        Extracts:
          - Key decisions
          - File changes
          - Open issues
          - User preferences
        """
        decisions = []
        file_changes = []
        open_issues = []
        preferences = []

        for msg in messages:
            content = msg.content or ""
            content_lower = content.lower()

            # Extract decisions (heuristic: lines with decision-related keywords)
            for line in content.split("\n"):
                line_stripped = line.strip()
                line_lower = line_stripped.lower()
                if not line_stripped:
                    continue

                if any(kw in line_lower for kw in ["decided", "decision", "chose", "will use", "agreed"]):
                    decisions.append(line_stripped)
                elif any(kw in line_lower for kw in ["created", "modified", "deleted", "updated", "wrote", "edited"]):
                    # Check for file-like patterns
                    if re.search(r'[\w/\\]+\.\w+', line_stripped):
                        file_changes.append(line_stripped)
                elif any(kw in line_lower for kw in ["todo", "fixme", "bug", "issue", "problem", "broken"]):
                    open_issues.append(line_stripped)
                elif any(kw in line_lower for kw in ["prefer", "always", "never", "convention", "style"]):
                    preferences.append(line_stripped)

        sections = []
        if decisions:
            sections.append("## Decisions\n" + "\n".join(f"- {d}" for d in decisions[:10]))
        if file_changes:
            sections.append("## File Changes\n" + "\n".join(f"- {f}" for f in file_changes[:10]))
        if open_issues:
            sections.append("## Open Issues\n" + "\n".join(f"- {i}" for i in open_issues[:10]))
        if preferences:
            sections.append("## Preferences\n" + "\n".join(f"- {p}" for p in preferences[:10]))

        if not sections:
            return f"[{len(messages)} earlier messages were compressed. No structured information extracted.]"

        return "\n\n".join(sections)
