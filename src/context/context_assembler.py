"""Assembles layered context for LLM calls.

Implements the 8-layer Context Stack as specified in the design document:

  Layer 1: Built-in System Prompt   (priority=100, required=True,  cacheable=True)
  Layer 2: Global CLAUDE.md         (priority=95,  required=False, cacheable=True)
  Layer 3: Project CLAUDE.md        (priority=90,  required=True,  cacheable=True)
  Layer 4: Sub-dir CLAUDE.md        (priority=85,  required=False, cacheable=True)
  Layer 5: Skills                   (priority=80,  required=False, cacheable=True)
  Layer 6: Session Memory           (priority=70,  required=False, cacheable=False)
  Layer 7: Conversation History     (priority=40,  required=False, cacheable=False)
  Layer 8: User Message             (priority=98,  required=True,  cacheable=False)

Static layers (1-5) are marked with cache_control for Prompt Caching.
Dynamic layers (6-8) are not cached.
"""

import logging
from pathlib import Path
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field

from src.core.models import Message, Role
from src.core.events import EventBus, AgentEvent
from src.session.session_manager import SessionManager
from src.memory.memory_controller import MemoryController
from src.prompt.prompt_provider import PromptProvider

from .context_window import ContextWindow, ContextBudget, CompressionLevel
from .context_compressor import ContextCompressor
from .compression_archive import CompressionArchive
from .importance_scorer import ImportanceScorer
from .system_reminder import SystemReminderBuilder

logger = logging.getLogger(__name__)


@dataclass
class ContextLayer:
    """Single layer of context."""
    name: str
    priority: int
    messages: List[Message] = field(default_factory=list)
    required: bool = False
    cacheable: bool = False


class ContextAssembler:
    """Builds complete context from 8 layers per the design specification.

    Layers are sorted by priority (descending) and assembled into a final
    message list.  Static layers get ``cache_control`` markers for Prompt
    Caching.  Events are emitted via the optional ``EventBus``.
    """

    def __init__(
        self,
        session_manager: SessionManager,
        memory_controller: Optional[MemoryController] = None,
        prompt_provider: Optional[PromptProvider] = None,
        compressor: Optional[ContextCompressor] = None,
        max_tokens: int = 128000,
        event_bus: Optional[EventBus] = None,
        project_path: Optional[Path] = None,
        *,
        context_config: Optional["object"] = None,  # ContextConfig
    ):
        self.session_manager = session_manager
        self.memory_controller = memory_controller
        self.prompt_provider = prompt_provider
        self.event_bus = event_bus
        self.project_path = project_path
        self._reminder_builder = SystemReminderBuilder()
        self._importance_scorer = ImportanceScorer()

        # Build from ContextConfig if provided, else backward-compatible
        if context_config is not None:
            from src.core.config import ContextConfig as _CC
            if not isinstance(context_config, _CC):
                raise TypeError(f"Expected ContextConfig, got {type(context_config)}")
            self._context_config = context_config
            self.compressor = compressor or ContextCompressor(context_config=context_config)
            self.window = ContextWindow(context_config=context_config)
            session_dir = None
            if session_manager and session_manager.active_session:
                s = session_manager.active_session
                if hasattr(s, 'session_dir') and s.session_dir:
                    session_dir = Path(s.session_dir)
            self._archive = CompressionArchive(
                session_dir=session_dir,
                recall_max_tokens=context_config.recall_max_tokens,
            )
            self._frequent_summary_warning_count = context_config.frequent_summary_warning_count
        else:
            from src.core.config import ContextConfig as _CC
            # Backward-compatible: adjust reserve_tokens if max_tokens is small
            reserve = min(4096, max(1, max_tokens // 4))
            self._context_config = _CC(context_window_tokens=max_tokens, reserve_tokens=reserve)
            self.compressor = compressor or ContextCompressor()
            self.window = ContextWindow(ContextBudget(max_tokens=max_tokens))
            self._archive = CompressionArchive()
            self._frequent_summary_warning_count = 3

        self._consecutive_summary_count: int = 0

        # Cached static prefix (layers 1-5) to avoid rebuilding every iteration
        self._static_prefix: List[Message] = []
        self._static_tokens: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        user_input: str,
        skill_context: Optional[str] = None,
    ) -> List[Message]:
        """Build complete context for the first LLM call of a turn.

        1. Resets the token window.
        2. Constructs all 8 layers.
        3. Adds layers in priority order, skipping non-required layers
           that exceed the budget.
        4. Applies ``cache_control`` to static-layer messages.
        5. Caches the static prefix (layers 1-5) for subsequent
           ``rebuild()`` calls within the same turn.
        6. Emits ``CONTEXT_ASSEMBLED`` event.
        7. If utilization is critical, compresses and emits
           ``CONTEXT_COMPRESSED``.

        Returns:
            Ordered message list: system messages → history → user message.
        """
        # Reset token usage for each build cycle
        self.window.reset()

        layers = self._build_layers(user_input, skill_context)

        # DEBUG: Build started
        session_id = 'N/A'
        if self.session_manager.active_session:
            session_id = self.session_manager.active_session.id[:8]
        logger.debug(
            "[ContextAssembler] Build started | session_id=%s | user_input_length=%d | budget_total=%d | budget_available=%d",
            session_id, len(user_input), self.window.budget.max_tokens, self.window.budget.available_for_context
        )

        # Track per-layer token usage for the event payload
        layer_tokens: Dict[str, int] = {}
        result: List[Message] = []
        static_msgs: List[Message] = []

        for layer in sorted(layers, key=lambda l: l.priority, reverse=True):
            if not layer.messages:
                continue

            if not self.window.fits(layer.messages):
                if layer.required:
                    logger.warning(
                        f"Compressing required layer '{layer.name}' "
                        f"(budget remaining: {self.window.remaining()})"
                    )
                    layer.messages = self._compress_layer(layer.messages)
                    # Force-add even if still over budget
                    if not self.window.fits(layer.messages):
                        logger.warning(
                            f"Force-including required layer '{layer.name}' "
                            f"after compression"
                        )
                else:
                    logger.debug(
                        f"Skipping optional layer '{layer.name}' "
                        f"(needs ~{self.window.estimate_tokens(layer.messages)} tokens, "
                        f"remaining: {self.window.remaining()})"
                    )
                    # DEBUG: Layer evicted
                    logger.debug(
                        "[ContextAssembler] Layer evicted | name=%s | priority=%d | token_count=%d | reason=budget_exceeded",
                        layer.name, layer.priority, self.window.estimate_tokens(layer.messages)
                    )
                    continue

            tokens_before = self.window._current_usage
            self.window.add(layer.messages)
            tokens_used = self.window._current_usage - tokens_before
            layer_tokens[layer.name] = tokens_used

            # DEBUG: Layer added
            logger.debug(
                "[ContextAssembler] Layer added | name=%s | priority=%d | token_count=%d | required=%s | cumulative_tokens=%d",
                layer.name, layer.priority, tokens_used, layer.required, self.window._current_usage
            )

            # Apply cache_control to static (cacheable) layers
            if layer.cacheable:
                for msg in layer.messages:
                    msg.cache_control = {"type": "ephemeral"}
                static_msgs.extend(layer.messages)

            result.extend(layer.messages)

        # Cache static prefix for rebuild()
        self._static_prefix = static_msgs
        self._static_tokens = self.window.estimate_tokens(static_msgs)

        # Warn if utilization is high
        if self.window.should_warn():
            logger.warning(
                f"Context window utilization high: "
                f"{self.window.utilization():.1%} "
                f"({self.window.remaining()} tokens remaining)"
            )

        # Emit CONTEXT_ASSEMBLED event
        self._emit_assembled_event(result, layer_tokens)

        # DEBUG: Build complete
        logger.debug(
            "[ContextAssembler] Build complete | total_layers=%d | total_tokens=%d | budget_utilization=%.1f%% | message_count=%d",
            len(layer_tokens), self.window._current_usage,
            self.window.utilization() * 100, len(result)
        )

        # Compress if utilization is critical
        if self.window.should_compress():
            tokens_before = self.window._current_usage
            result = self._compress_messages(result)
            self._emit_compressed_event(tokens_before, result)

        # Re-order: system messages first, then history, then user message last
        result = self._reorder_messages(result)

        return result

    def rebuild(
        self,
        turn_messages: List[Message],
    ) -> List[Message]:
        """Reassemble context using the 5-Phase compression pipeline.

        Phase 1: Prune — pre-trim oversized tool outputs
        Phase 2: Truncate — head+tail truncation of old messages
        Phase 3: Evaluate — check utilization, decide next steps
        Phase 4: LLM Summary — semantic compression (conditional)
        Phase 5: Emergency — Level 1/2/3 compression (conditional)
        """
        self.window.reset()
        self.compressor.reset_rebuild_state()

        # Account for static prefix tokens
        if self._static_prefix:
            self.window.add(self._static_prefix)

        optimized = list(turn_messages)
        original_tokens = self.window.estimate_tokens(turn_messages)

        # ---- Phase 1: Prune ----
        tokens_before_p1 = self.window.estimate_tokens(optimized)
        keep_recent = self.window.get_dynamic_keep_recent()
        optimized = self.compressor._prune_large_outputs(
            optimized, keep_recent, self._archive
        )
        tokens_after_p1 = self.window.estimate_tokens(optimized)
        logger.debug(
            "[rebuild] Phase 1 Prune: %d -> %d tokens", tokens_before_p1, tokens_after_p1
        )

        # ---- Phase 2: Truncate (always runs — proactive optimization) ----
        # Determine compression level for aggressive truncation thresholds
        self.window.add(optimized)
        compression_level = self.window.compression_level()
        self.window.remove(1)

        tokens_before_p2 = self.window.estimate_tokens(optimized)
        optimized = self._truncate_old_tool_outputs(optimized, compression_level)
        tokens_after_p2 = self.window.estimate_tokens(optimized)
        logger.debug(
            "[rebuild] Phase 2 Truncate: %d -> %d tokens", tokens_before_p2, tokens_after_p2
        )

        # Re-evaluate after Phase 2
        self.window.add(optimized)
        util_after_p2 = self.window.utilization()
        if util_after_p2 < self.window.budget.warning_threshold:
            result = self._static_prefix + optimized
            self._emit_pipeline_event(original_tokens, optimized, {
                "phase1_saved": tokens_before_p1 - tokens_after_p1,
                "phase2_saved": tokens_before_p2 - tokens_after_p2,
            })
            return result
        self.window.remove(1)

        # ---- Phase 3: Evaluate ----
        logger.debug(
            "[rebuild] Phase 3 Evaluate: utilization=%.1f%%, warning=%.1f%%",
            util_after_p2 * 100, self.window.budget.warning_threshold * 100,
        )

        # ---- Phase 4: LLM Summary (conditional) ----
        tokens_before_p4 = self.window.estimate_tokens(optimized)
        optimized = self.compressor._llm_summarize(
            optimized,
            keep_recent=keep_recent,
            archive=self._archive,
            utilization=util_after_p2,
            warning_threshold=self.window.budget.warning_threshold,
        )
        tokens_after_p4 = self.window.estimate_tokens(optimized)

        if tokens_after_p4 < tokens_before_p4:
            self._consecutive_summary_count += 1
            logger.debug(
                "[rebuild] Phase 4 LLM Summary: %d -> %d tokens",
                tokens_before_p4, tokens_after_p4,
            )
            if self._consecutive_summary_count >= self._frequent_summary_warning_count:
                logger.warning(
                    "Frequent LLM summarization detected (%d consecutive). "
                    "Consider increasing max_tokens budget or reducing task complexity.",
                    self._consecutive_summary_count,
                )
        else:
            self._consecutive_summary_count = 0
            logger.debug("[rebuild] Phase 4 LLM Summary: skipped")

        # Re-evaluate after Phase 4
        self.window.add(optimized)
        util_after_p4 = self.window.utilization()

        if util_after_p4 < self.window.budget.critical_threshold:
            result = self._static_prefix + optimized
            self._emit_pipeline_event(original_tokens, optimized, {
                "phase1_saved": tokens_before_p1 - tokens_after_p1,
                "phase2_saved": tokens_before_p2 - tokens_after_p2,
                "phase4_saved": tokens_before_p4 - tokens_after_p4,
            })
            return result
        self.window.remove(1)

        # ---- Phase 5: Emergency ----
        logger.info(
            "[rebuild] Phase 5 Emergency: utilization=%.1f%% >= critical=%.1f%%",
            util_after_p4 * 100, self.window.budget.critical_threshold * 100,
        )
        compressed, _ = self.compressor.compress_history(
            optimized,
            target_tokens=self.window.remaining(),
        )
        self.window.add(compressed)
        tokens_after_p5 = self.window.estimate_tokens(compressed)

        self._emit_compressed_event(tokens_before_p4, compressed)
        self._emit_pipeline_event(original_tokens, compressed, {
            "phase1_saved": tokens_before_p1 - tokens_after_p1,
            "phase2_saved": tokens_before_p2 - tokens_after_p2,
            "phase4_saved": tokens_before_p4 - tokens_after_p4,
            "phase5_saved": self.window.estimate_tokens(optimized) - tokens_after_p5,
        })

        logger.info(
            "[rebuild] Pipeline complete: %d -> %d tokens",
            original_tokens, tokens_after_p5,
        )

        return self._static_prefix + compressed

    def _truncate_old_tool_outputs(
        self,
        turn_messages: List[Message],
        compression_level: CompressionLevel = CompressionLevel.NONE,
    ) -> List[Message]:
        """Truncate large tool AND assistant outputs in older messages.

        Enhanced with:
        - Assistant message truncation (Task 5)
        - Importance-score-driven thresholds (Task 5)
        - SOFT-level aggressive truncation (Task 5)
        - compression_state tracking
        """
        n = len(turn_messages)
        keep_recent = self.compressor.keep_recent

        if n <= keep_recent:
            return list(turn_messages)

        boundary = n - keep_recent
        result: List[Message] = []

        # SOFT level: halve truncation thresholds
        tool_max = self.compressor.max_tool_output_length
        asst_max = self.compressor.max_assistant_output_length
        if compression_level == CompressionLevel.SOFT:
            tool_max = max(1, tool_max // 2)
            asst_max = max(1, asst_max // 2) if asst_max > 0 else asst_max

        for i, msg in enumerate(turn_messages):
            if i < boundary:
                result.append(self._maybe_truncate_msg(msg, i, boundary, tool_max, asst_max))
            else:
                result.append(msg)

        return result

    def _maybe_truncate_msg(
        self,
        msg: Message,
        index: int,
        boundary: int,
        tool_max_len: int,
        asst_max_len: int,
    ) -> Message:
        """Truncate a tool or assistant message based on importance score."""
        content = msg.content or ""

        # Skip already-summarized messages
        if getattr(msg, "compression_state", "original") == "summarized":
            return msg

        # Determine if this is a truncatable message and which threshold to use
        is_tool_msg = (
            msg.role == Role.TOOL.value
            or msg.tool_call_id is not None
        )
        is_plain_assistant = (
            msg.role == Role.ASSISTANT.value
            and msg.tool_calls is None
        )

        if is_tool_msg:
            base_max = tool_max_len
        elif is_plain_assistant and asst_max_len > 0:
            base_max = asst_max_len
        else:
            return msg

        # Importance-driven threshold adjustment
        turns_ago = boundary - index
        importance = self._importance_scorer.score(msg, turns_ago)
        if importance > self.compressor.high_importance_threshold:
            effective_max = base_max * 2
        elif importance < self.compressor.low_importance_threshold:
            effective_max = max(1, base_max // 2)
        else:
            effective_max = base_max

        if len(content) <= effective_max:
            return msg

        # Head + tail truncation
        half = effective_max // 2
        truncated_chars = len(content) - effective_max

        # Skip truncation if the marker text would negate the savings
        # (marker is ~60 chars; only truncate if we save at least 100 chars)
        if truncated_chars < 100:
            return msg

        head = content[:half]
        tail = content[-half:]

        msg_type = "tool output" if is_tool_msg else "assistant response"
        truncated_content = (
            f"{head}\n"
            f"[... {truncated_chars} chars truncated from old {msg_type} ...]\n"
            f"{tail}"
        )

        logger.debug(
            "[Truncate] msg %d: importance=%.2f, threshold=%d, truncated %d chars",
            index, importance, effective_max, truncated_chars,
        )

        return Message(
            role=msg.role,
            content=truncated_content,
            tool_calls=msg.tool_calls,
            tool_call_id=msg.tool_call_id,
            name=msg.name,
            token_count=msg.token_count,
            importance=msg.importance,
            compression_state="truncated",
        )

    # ------------------------------------------------------------------
    # Layer construction
    # ------------------------------------------------------------------

    def _build_layers(
        self,
        user_input: str,
        skill_context: Optional[str],
    ) -> List[ContextLayer]:
        """Construct all 8 context layers."""
        layers: List[ContextLayer] = []

        # Layer 1: Built-in System Prompt (priority=100, required, cacheable)
        self._add_system_prompt_layer(layers)

        # Layer 2: Global CLAUDE.md (priority=95, optional, cacheable)
        self._add_global_claude_layer(layers)

        # Layer 3: Project CLAUDE.md (priority=90, required, cacheable)
        self._add_project_claude_layer(layers)

        # Layer 4: Sub-dir CLAUDE.md (priority=85, optional, cacheable)
        self._add_subdir_claude_layer(layers)

        # Layer 5: Skills (priority=80, optional, cacheable)
        self._add_skills_layer(layers, skill_context)

        # Layer 6: Session Memory / Retrieved Memories (priority=70, optional)
        self._add_memory_layer(layers, user_input)

        # Layer 7: Conversation History (priority=40, optional)
        self._add_history_layer(layers)

        # Layer 8: User Message with system-reminder (priority=98, required)
        self._add_user_message_layer(layers, user_input)

        return layers

    def _add_system_prompt_layer(self, layers: List[ContextLayer]) -> None:
        """Layer 1: Built-in System Prompt."""
        if not self.prompt_provider:
            return
        try:
            system_prompt = self.prompt_provider.build_system_prompt()
            if system_prompt:
                layers.append(ContextLayer(
                    name="system_prompt",
                    priority=100,
                    messages=[Message.system(system_prompt)],
                    required=True,
                    cacheable=True,
                ))
        except Exception as e:
            logger.error(f"Failed to build system prompt: {e}")

    def _add_global_claude_layer(self, layers: List[ContextLayer]) -> None:
        """Layer 2: Global CLAUDE.md (~/.agent_x1/CLAUDE.md)."""
        try:
            from src.memory.project_memory import ProjectMemoryLoader
            loader = ProjectMemoryLoader()
            files = loader.discover_global()
            if files:
                content = "\n\n---\n\n".join(
                    f"<!-- From: {f.path} -->\n{f.content}" for f in files
                )
                layers.append(ContextLayer(
                    name="global_claude",
                    priority=95,
                    messages=[Message.system(f"## Global Context\n\n{content}")],
                    required=False,
                    cacheable=True,
                ))
        except Exception as e:
            logger.debug(f"Failed to load global CLAUDE.md: {e}")

    def _add_project_claude_layer(self, layers: List[ContextLayer]) -> None:
        """Layer 3: Project CLAUDE.md."""
        try:
            from src.memory.project_memory import ProjectMemoryLoader
            loader = ProjectMemoryLoader()
            files = loader.discover_project(self.project_path)
            if files:
                content = "\n\n---\n\n".join(
                    f"<!-- From: {f.path} -->\n{f.content}" for f in files
                )
                layers.append(ContextLayer(
                    name="project_claude",
                    priority=90,
                    messages=[Message.system(f"## Project Context\n\n{content}")],
                    required=True,
                    cacheable=True,
                ))
        except Exception as e:
            logger.debug(f"Failed to load project CLAUDE.md: {e}")

    def _add_subdir_claude_layer(self, layers: List[ContextLayer]) -> None:
        """Layer 4: Sub-dir CLAUDE.md (loaded when project_path is set)."""
        if not self.project_path:
            return
        try:
            from src.memory.project_memory import ProjectMemoryLoader
            loader = ProjectMemoryLoader()
            # Use project_path as the active file path for sub-dir discovery
            files = loader.discover_subdir(self.project_path, project_root=self.project_path)
            if files:
                content = "\n\n---\n\n".join(
                    f"<!-- From: {f.path} -->\n{f.content}" for f in files
                )
                layers.append(ContextLayer(
                    name="subdir_claude",
                    priority=85,
                    messages=[Message.system(f"## Sub-directory Context\n\n{content}")],
                    required=False,
                    cacheable=True,
                ))
        except Exception as e:
            logger.debug(f"Failed to load sub-dir CLAUDE.md: {e}")

    def _add_skills_layer(
        self, layers: List[ContextLayer], skill_context: Optional[str]
    ) -> None:
        """Layer 5: Active Skill."""
        if not skill_context:
            return
        layers.append(ContextLayer(
            name="skills",
            priority=80,
            messages=[Message.system(f"## Active Skill\n\n{skill_context}")],
            required=False,
            cacheable=True,
        ))

    def _add_memory_layer(
        self, layers: List[ContextLayer], user_input: str
    ) -> None:
        """Layer 6: Session Memory / Retrieved Memories."""
        if not self.memory_controller:
            return
        try:
            memories = self.memory_controller.retrieve_relevant(user_input, top_k=3)
            if memories:
                memory_text = "\n\n".join(m.content for m in memories)
                layers.append(ContextLayer(
                    name="memory",
                    priority=70,
                    messages=[Message.system(f"## Relevant Memories\n\n{memory_text}")],
                    required=False,
                    cacheable=False,
                ))
        except Exception as e:
            logger.debug(f"Failed to retrieve memories: {e}")

    def _add_history_layer(self, layers: List[ContextLayer]) -> None:
        """Layer 7: Conversation History."""
        history = self._load_history()
        if history:
            layers.append(ContextLayer(
                name="history",
                priority=40,
                messages=history,
                required=False,
                cacheable=False,
            ))

    def _add_user_message_layer(
        self, layers: List[ContextLayer], user_input: str
    ) -> None:
        """Layer 8: User Message with system-reminder injection."""
        enriched_input = self._reminder_builder.build(
            user_input,
            project_path=self.project_path,
            has_compression=self._archive.has_archives(),
        )
        layers.append(ContextLayer(
            name="user_message",
            priority=98,
            messages=[Message.user(enriched_input)],
            required=True,
            cacheable=False,
        ))

    # ------------------------------------------------------------------
    # History loading
    # ------------------------------------------------------------------

    def _load_history(self) -> List[Message]:
        """Load recent conversation history from session manager.

        Converts ``Turn`` objects (from the session store) back into
        ``Message`` objects, preserving all fields required by the LLM
        API format:

        - **assistant + tool_calls**: ``tool_calls`` must be present so
          the engine can emit ``tool_use`` blocks.
        - **tool**: ``tool_call_id`` and ``name`` must be present so the
          engine can emit ``tool_result`` blocks with the correct
          ``tool_use_id``.

        **Tool-call pairing integrity**: The loaded history must never
        contain a ``tool_result`` without its corresponding
        ``assistant(tool_use)`` message.  If the ``recent_n`` boundary
        falls in the middle of a tool-call group, we extend the window
        backwards to include the assistant message that initiated the
        tool calls.
        """
        try:
            session = self.session_manager.active_session
            if not session:
                return []

            turns = self.session_manager.get_history(recent_n=10)
            turns = self._ensure_tool_call_pairing(turns)
            messages = []
            for turn in turns:
                if turn.role == "assistant" and turn.tool_calls:
                    messages.append(Message(
                        role=Role.ASSISTANT.value,
                        content=turn.content,
                        tool_calls=turn.tool_calls,
                    ))
                elif turn.role == "tool":
                    # Preserve tool_call_id and name — required by
                    # Anthropic API for tool_result blocks.
                    messages.append(Message(
                        role=Role.TOOL.value,
                        content=turn.content,
                        tool_call_id=turn.tool_call_id,
                        name=getattr(turn, 'name', None),
                    ))
                else:
                    role_value = (
                        turn.role
                        if turn.role in [r.value for r in Role]
                        else Role.USER.value
                    )
                    messages.append(Message(role=role_value, content=turn.content))

            return messages
        except Exception as e:
            logger.debug(f"Failed to load history: {e}")
            return []

    def _ensure_tool_call_pairing(self, turns) -> list:
        """Ensure tool_result turns have their corresponding assistant(tool_use) turn.

        If the first turn(s) in the loaded window are ``tool`` messages
        whose ``tool_call_id`` has no matching ``assistant(tool_calls)``
        in the window, those orphaned tool messages are dropped.

        This prevents the Anthropic API 400 error:
        ``"tool_call_id <id> is not found"``
        which occurs when a ``tool_result`` block references a
        ``tool_use_id`` that doesn't exist in the conversation.
        """
        if not turns:
            return turns

        # Collect all tool_use ids from assistant messages in the window
        available_tool_use_ids: set = set()
        for turn in turns:
            if turn.role == "assistant" and turn.tool_calls:
                for tc in turn.tool_calls:
                    tc_id = tc.get("id", "")
                    if tc_id:
                        available_tool_use_ids.add(tc_id)

        # Drop orphaned tool messages (tool_call_id not in any assistant's tool_calls)
        result = []
        dropped_count = 0
        for turn in turns:
            if turn.role == "tool":
                tc_id = turn.tool_call_id
                if not tc_id or tc_id not in available_tool_use_ids:
                    dropped_count += 1
                    continue
            result.append(turn)

        if dropped_count > 0:
            logger.warning(
                "[_load_history] Dropped %d orphaned tool_result turn(s) "
                "whose assistant(tool_use) was outside the history window",
                dropped_count,
            )

        return result

    # ------------------------------------------------------------------
    # Compression helpers
    # ------------------------------------------------------------------

    def _compress_layer(self, messages: List[Message]) -> List[Message]:
        """Compress a single layer's messages (truncate long outputs)."""
        return self.compressor.compress_messages(messages)

    def _compress_messages(self, messages: List[Message]) -> List[Message]:
        """Compress the full message list using three-level strategy."""
        result, _ = self.compressor.compress_history(
            messages,
            target_tokens=self.window.budget.available_for_context,
        )
        return result

    # ------------------------------------------------------------------
    # Message reordering
    # ------------------------------------------------------------------

    @staticmethod
    def _reorder_messages(messages: List[Message]) -> List[Message]:
        """Reorder messages: system first, then history, then user last.

        Ensures the message list conforms to LLM API requirements:
        system messages → conversation history → user message.
        """
        system_msgs = []
        user_msgs = []
        other_msgs = []

        for msg in messages:
            if msg.role == Role.SYSTEM.value:
                system_msgs.append(msg)
            elif msg.role == Role.USER.value:
                # The last user message (with system-reminder) goes at the end
                user_msgs.append(msg)
            else:
                other_msgs.append(msg)

        # The final user message (most recent) should be last
        # Earlier user messages from history go with other_msgs
        if len(user_msgs) > 1:
            history_users = user_msgs[:-1]
            final_user = [user_msgs[-1]]
            # Merge history user messages back into other_msgs preserving order
            combined_ids = set(id(m) for m in other_msgs) | set(id(m) for m in history_users)
            other_msgs = [m for m in messages if id(m) in combined_ids]
        elif user_msgs:
            final_user = user_msgs
        else:
            final_user = []

        return system_msgs + other_msgs + final_user

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    def _emit_assembled_event(
        self,
        messages: List[Message],
        layer_tokens: Dict[str, int],
    ) -> None:
        """Emit CONTEXT_ASSEMBLED event."""
        if not self.event_bus:
            return
        total_tokens = self.window._current_usage
        total_for_ratio = max(total_tokens, 1)
        layer_ratios = {
            name: tokens / total_for_ratio
            for name, tokens in layer_tokens.items()
        }
        try:
            self.event_bus.emit(
                AgentEvent.CONTEXT_ASSEMBLED,
                total_tokens=total_tokens,
                layer_count=len(layer_tokens),
                layer_token_ratios=layer_ratios,
                message_count=len(messages),
            )
        except Exception as e:
            logger.debug(f"Failed to emit CONTEXT_ASSEMBLED: {e}")

    def _emit_compressed_event(
        self,
        tokens_before: int,
        messages_after: List[Message],
    ) -> None:
        """Emit CONTEXT_COMPRESSED event."""
        if not self.event_bus:
            return
        tokens_after = self.window.estimate_tokens(messages_after)
        ratio = tokens_after / max(tokens_before, 1)
        try:
            self.event_bus.emit(
                AgentEvent.CONTEXT_COMPRESSED,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                compression_ratio=ratio,
            )
        except Exception as e:
            logger.debug(f"Failed to emit CONTEXT_COMPRESSED: {e}")

    def _emit_pipeline_event(
        self,
        original_tokens: int,
        messages_after: List[Message],
        phase_stats: Dict[str, Any],
    ) -> None:
        """Emit COMPRESSION_PIPELINE_COMPLETED event."""
        if not self.event_bus:
            return
        tokens_after = self.window.estimate_tokens(messages_after)
        try:
            self.event_bus.emit(
                AgentEvent.COMPRESSION_PIPELINE_COMPLETED,
                original_tokens=original_tokens,
                final_tokens=tokens_after,
                compression_ratio=tokens_after / max(original_tokens, 1),
                phase_stats=phase_stats,
                archive_count=self._archive.get_archive_count(),
            )
        except Exception as e:
            logger.debug(f"Failed to emit COMPRESSION_PIPELINE_COMPLETED: {e}")
