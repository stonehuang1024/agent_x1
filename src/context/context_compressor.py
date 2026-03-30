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
from typing import List, Optional, Tuple, Callable, TYPE_CHECKING

from src.core.models import Message, Role

if TYPE_CHECKING:
    from src.context.compression_archive import CompressionArchive
    from src.core.config import ContextConfig

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
        *,
        # Task 4: Prune parameters
        prune_minimum_tokens: int = 5000,
        prune_protect_window: int = 8,
        prune_preview_chars: int = 200,
        # Task 5: Enhanced truncation
        max_assistant_output_length: int = 3000,
        high_importance_threshold: float = 0.7,
        # Task 6: LLM summary
        llm_caller: Optional[Callable] = None,
        min_summary_tokens: int = 2000,
        min_summary_interval: int = 6,
        # Config-based init
        context_config: Optional["ContextConfig"] = None,
    ):
        # If context_config is provided, use it for all parameters
        if context_config is not None:
            self.max_tool_output_length = context_config.max_tool_output_length
            self.summary_threshold = context_config.summary_threshold
            self.keep_recent = context_config.keep_recent
            self.low_importance_threshold = context_config.low_importance_threshold
            self.prune_minimum_tokens = context_config.prune_minimum_tokens
            self.prune_protect_window = context_config.prune_protect_window
            self.prune_preview_chars = context_config.prune_preview_chars
            self.max_assistant_output_length = context_config.max_assistant_output_length
            self.high_importance_threshold = context_config.high_importance_threshold
            self.min_summary_tokens = context_config.min_summary_tokens
            self.min_summary_interval = context_config.min_summary_interval
        else:
            self.max_tool_output_length = max_tool_output_length
            self.summary_threshold = summary_threshold
            self.keep_recent = keep_recent
            self.low_importance_threshold = low_importance_threshold
            self.prune_minimum_tokens = prune_minimum_tokens
            self.prune_protect_window = prune_protect_window
            self.prune_preview_chars = prune_preview_chars
            self.max_assistant_output_length = max_assistant_output_length
            self.high_importance_threshold = high_importance_threshold
            self.min_summary_tokens = min_summary_tokens
            self.min_summary_interval = min_summary_interval

        self.token_estimator = token_estimator or _default_token_estimator
        self.llm_caller = llm_caller

        # LLM summary state tracking
        self._last_summary_msg_count: int = 0
        self._summary_executed_this_rebuild: bool = False

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

    # ------------------------------------------------------------------
    # Prune: pre-trim large tool outputs (Task 4)
    # ------------------------------------------------------------------

    def _prune_large_outputs(
        self,
        messages: List[Message],
        keep_recent: int,
        archive: Optional["CompressionArchive"] = None,
    ) -> List[Message]:
        """Replace oversized tool outputs outside the protect window with head+tail previews."""
        n = len(messages)
        result: List[Message] = []

        for i, msg in enumerate(messages):
            # Protect recent messages
            if n - i <= self.prune_protect_window:
                result.append(msg)
                continue

            # Skip already-summarized messages
            if getattr(msg, "compression_state", "original") == "summarized":
                result.append(msg)
                continue

            # Only prune tool messages
            if msg.role != Role.TOOL.value:
                result.append(msg)
                continue

            content = msg.content or ""
            est_tokens = math.ceil(len(content) / _DEFAULT_CHARS_PER_TOKEN)
            if est_tokens <= self.prune_minimum_tokens:
                result.append(msg)
                continue

            # Archive original before pruning
            archive_id = ""
            if archive is not None:
                archive_id = archive.archive([msg], "prune", (i, i))

            pc = self.prune_preview_chars
            head = content[:pc]
            tail = content[-pc:] if len(content) > pc else ""
            marker = f"[... pruned {est_tokens} tokens for context efficiency ... | archive_id={archive_id}]"
            pruned_content = f"{head}\n{marker}\n{tail}"

            pruned_msg = Message(
                role=msg.role,
                content=pruned_content,
                tool_calls=msg.tool_calls,
                tool_call_id=msg.tool_call_id,
                name=msg.name,
                token_count=msg.token_count,
                importance=msg.importance,
                compression_state="pruned",
            )
            result.append(pruned_msg)
            logger.debug(
                "[Prune] Pruned message %d: %d tokens -> preview", i, est_tokens
            )

        return result

    # ------------------------------------------------------------------
    # LLM Summary (Task 6)
    # ------------------------------------------------------------------

    def reset_rebuild_state(self):
        """Reset per-rebuild flags.  Call at the start of each rebuild()."""
        self._summary_executed_this_rebuild = False

    def _build_summary_prompt(self, messages: List[Message]) -> str:
        """Build the prompt that instructs the LLM to produce a structured summary."""
        conversation = []
        for msg in messages:
            role = msg.role
            content = (msg.content or "")[:2000]  # cap per-message length
            conversation.append(f"[{role}]: {content}")

        conversation_text = "\n\n".join(conversation)

        return (
            "You are a conversation compressor. Summarize the following conversation "
            "history into a structured state snapshot. Preserve:\n"
            "1. Key decisions made\n"
            "2. Actions completed (files created/modified/deleted)\n"
            "3. Current task state and progress\n"
            "4. Important context and constraints\n"
            "5. User preferences expressed\n\n"
            "Output format:\n"
            "<state_snapshot>\n"
            "## Decisions\n- [decision 1]\n\n"
            "## Completed Actions\n- [action 1]\n\n"
            "## Current State\n[description]\n\n"
            "## File Changes\n- [file change 1]\n\n"
            "## User Preferences\n- [preference 1]\n"
            "</state_snapshot>\n\n"
            "--- CONVERSATION TO SUMMARIZE ---\n\n"
            f"{conversation_text}"
        )

    def _should_llm_summarize(
        self,
        messages: List[Message],
        utilization: float,
        warning_threshold: float,
    ) -> bool:
        """Check all four conditions for LLM summary trigger."""
        if utilization < warning_threshold:
            logger.debug("[LLM Summary] Skip: utilization %.2f < threshold %.2f", utilization, warning_threshold)
            return False

        msg_since_last = len(messages) - self._last_summary_msg_count
        if msg_since_last < self.min_summary_interval:
            logger.debug("[LLM Summary] Skip: only %d msgs since last summary (need %d)", msg_since_last, self.min_summary_interval)
            return False

        # Count summarisable tokens
        summarisable_tokens = 0
        for msg in messages:
            state = getattr(msg, "compression_state", "original")
            if state in ("original", "truncated", "pruned"):
                summarisable_tokens += self.token_estimator([msg])
        if summarisable_tokens < self.min_summary_tokens:
            logger.debug("[LLM Summary] Skip: summarisable tokens %d < min %d", summarisable_tokens, self.min_summary_tokens)
            return False

        if self._summary_executed_this_rebuild:
            logger.debug("[LLM Summary] Skip: already executed this rebuild")
            return False

        return True

    def _llm_summarize(
        self,
        messages: List[Message],
        keep_recent: int,
        archive: Optional["CompressionArchive"] = None,
        utilization: float = 0.0,
        warning_threshold: float = 0.8,
    ) -> List[Message]:
        """Attempt LLM-based semantic summary of old messages."""
        if not self._should_llm_summarize(messages, utilization, warning_threshold):
            return messages

        if self.llm_caller is None:
            logger.debug("[LLM Summary] No llm_caller configured — falling back to placeholder")
            return messages

        # Separate system / old / recent
        system_msgs = [m for m in messages if m.role == Role.SYSTEM.value]
        non_system = [m for m in messages if m.role != Role.SYSTEM.value]

        if len(non_system) <= keep_recent:
            return messages

        recent_msgs = non_system[-keep_recent:]
        old_msgs = non_system[:-keep_recent]

        # Partition old messages by compression state
        summarisable = []
        preserved = []  # already SUMMARIZED — keep as-is
        for msg in old_msgs:
            state = getattr(msg, "compression_state", "original")
            if state == "summarized":
                preserved.append(msg)
            else:
                summarisable.append(msg)

        if not summarisable:
            return messages

        tokens_before = self.token_estimator(summarisable)
        prompt = self._build_summary_prompt(summarisable)

        try:
            summary_text = self.llm_caller(prompt)
        except Exception as exc:
            logger.warning("[LLM Summary] LLM call failed (%s) — falling back to placeholder", exc)
            # Fallback: use existing _summarize_old_assistants
            fallback_remaining, _ = self._summarize_old_assistants(old_msgs)
            return system_msgs + fallback_remaining + recent_msgs

        # Archive originals
        archive_id = ""
        if archive is not None:
            start_idx = len(system_msgs)
            end_idx = start_idx + len(summarisable) - 1
            archive_id = archive.archive(summarisable, "llm_summary", (start_idx, end_idx))

        # Build summary message with metadata
        tokens_after_est = math.ceil(len(summary_text) / _DEFAULT_CHARS_PER_TOKEN) + _DEFAULT_MSG_OVERHEAD
        metadata = (
            f"<compression_metadata>\n"
            f"  compressed_messages: {len(summarisable)}\n"
            f"  tokens_before: {tokens_before}\n"
            f"  tokens_after: {tokens_after_est}\n"
            f"  archive_id: {archive_id}\n"
            f"</compression_metadata>\n\n"
        )
        recall_hint = (
            f"\n\nNote: The above is a compressed summary of earlier conversation. "
            f"If you need the full original messages, use the 'recall_compressed_messages' "
            f"tool with archive_id='{archive_id}'."
        )
        summary_msg = Message(
            role=Role.SYSTEM.value,
            content=metadata + summary_text + recall_hint,
            compression_state="summarized",
        )

        self._summary_executed_this_rebuild = True
        self._last_summary_msg_count = len(messages)

        logger.info(
            "[LLM Summary] Compressed %d messages: %d -> %d tokens (ratio=%.1f%%)",
            len(summarisable), tokens_before, tokens_after_est,
            (tokens_after_est / max(tokens_before, 1)) * 100,
        )

        return system_msgs + preserved + [summary_msg] + recent_msgs

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
