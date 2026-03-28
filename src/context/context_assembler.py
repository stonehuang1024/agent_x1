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

from .context_window import ContextWindow, ContextBudget
from .context_compressor import ContextCompressor
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
    ):
        self.session_manager = session_manager
        self.memory_controller = memory_controller
        self.prompt_provider = prompt_provider
        self.compressor = compressor or ContextCompressor()
        self.window = ContextWindow(ContextBudget(max_tokens=max_tokens))
        self.event_bus = event_bus
        self.project_path = project_path
        self._reminder_builder = SystemReminderBuilder()
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
                    continue

            tokens_before = self.window._current_usage
            self.window.add(layer.messages)
            tokens_used = self.window._current_usage - tokens_before
            layer_tokens[layer.name] = tokens_used

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
        """Reassemble context for subsequent LLM calls within the same turn.

        Instead of rebuilding all 8 layers from scratch, this method:
        1. Reuses the cached static prefix (layers 1-5).
        2. **Proactively** truncates large tool outputs in older messages
           to eliminate redundant token usage.
        3. Checks token budget and applies full compression if still needed.

        The key optimization: older tool_result messages (beyond the recent
        window) have their content truncated via head+tail preservation.
        This prevents the pattern where a 48KB PDF result is sent verbatim
        on every subsequent LLM call.

        Args:
            turn_messages: The accumulated messages for the current turn
                (user message, assistant responses, tool results).

        Returns:
            Ordered message list: static prefix → turn messages.
        """
        self.window.reset()

        # Account for static prefix tokens
        if self._static_prefix:
            self.window.add(self._static_prefix)

        # --- Proactive compression of old tool outputs ---
        # Always truncate large tool outputs in older messages to avoid
        # sending e.g. a 48KB PDF result on every subsequent LLM call.
        # Keep the most recent `keep_recent` messages intact so the LLM
        # has full context for the current step.
        optimized = self._truncate_old_tool_outputs(turn_messages)

        # Estimate dynamic portion
        dynamic_tokens = self.window.estimate_tokens(optimized)
        original_tokens = self.window.estimate_tokens(turn_messages)

        if dynamic_tokens < original_tokens:
            logger.info(
                f"[rebuild] Truncated old tool outputs: "
                f"{original_tokens} → {dynamic_tokens} tokens "
                f"(saved {original_tokens - dynamic_tokens})"
            )

        total_needed = self._static_tokens + dynamic_tokens

        logger.debug(
            f"[rebuild] static={self._static_tokens} tokens, "
            f"dynamic={dynamic_tokens} tokens, "
            f"total={total_needed}, "
            f"budget={self.window.budget.available_for_context}"
        )

        # If within budget after proactive truncation, combine
        if self.window.fits(optimized):
            self.window.add(optimized)
            result = self._static_prefix + optimized
        else:
            # Still over budget — apply full compression pipeline
            logger.info(
                f"[rebuild] Compressing turn messages: "
                f"{dynamic_tokens} tokens exceeds remaining "
                f"{self.window.remaining()} tokens"
            )
            compressed, _ = self.compressor.compress_history(
                optimized,
                target_tokens=self.window.remaining(),
            )
            self.window.add(compressed)
            result = self._static_prefix + compressed

            compressed_tokens = self.window.estimate_tokens(compressed)
            self._emit_compressed_event(dynamic_tokens, compressed)
            logger.info(
                f"[rebuild] Compressed: {dynamic_tokens} → {compressed_tokens} tokens"
            )

        return result

    def _truncate_old_tool_outputs(
        self,
        turn_messages: List[Message],
    ) -> List[Message]:
        """Truncate large tool outputs in older messages.

        Strategy:
        - The most recent ``compressor.keep_recent`` messages are kept
          intact (the LLM needs full context for the current step).
        - Older messages with role='tool' or containing tool_result have
          their content truncated using head+tail preservation.
        - Assistant messages and the original user message are kept as-is
          (they are typically small).

        This is applied **proactively** on every rebuild(), not just when
        the token budget is exceeded.  It mutates nothing — returns a new
        list with truncated copies where needed.
        """
        n = len(turn_messages)
        keep_recent = self.compressor.keep_recent

        # If few enough messages, no truncation needed
        if n <= keep_recent:
            return list(turn_messages)

        boundary = n - keep_recent
        result: List[Message] = []

        for i, msg in enumerate(turn_messages):
            if i < boundary:
                # Old message — truncate if it's a tool result with large content
                result.append(self._maybe_truncate_tool_msg(msg))
            else:
                # Recent message — keep intact
                result.append(msg)

        return result

    def _maybe_truncate_tool_msg(self, msg: Message) -> Message:
        """Truncate a tool-result message if its content is large.

        Uses the compressor's ``max_tool_output_length`` as the threshold.
        Non-tool messages and small tool messages are returned as-is.
        """
        content = msg.content or ""
        max_len = self.compressor.max_tool_output_length

        # Only truncate tool-role messages or messages with tool_call_id
        is_tool_msg = (
            msg.role == Role.TOOL.value
            or msg.tool_call_id is not None
            or (msg.role == Role.USER.value and isinstance(msg.content, str)
                and "tool_result" in msg.content[:50])
        )

        if not is_tool_msg or len(content) <= max_len:
            return msg

        # Head + tail truncation
        half = max_len // 2
        truncated_chars = len(content) - max_len
        head = content[:half]
        tail = content[-half:]
        truncated_content = (
            f"{head}\n"
            f"[... {truncated_chars} chars truncated for context efficiency ...]\n"
            f"{tail}"
        )

        return Message(
            role=msg.role,
            content=truncated_content,
            tool_calls=msg.tool_calls,
            tool_call_id=msg.tool_call_id,
            name=msg.name,
            token_count=msg.token_count,
            importance=msg.importance,
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
            user_input, project_path=self.project_path
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
        """Load recent conversation history from session manager."""
        try:
            session = self.session_manager.active_session
            if not session:
                return []

            turns = self.session_manager.get_history(recent_n=10)
            messages = []
            for turn in turns:
                if turn.role == "assistant" and turn.tool_calls:
                    messages.append(Message(
                        role=Role.ASSISTANT.value,
                        content=turn.content,
                        tool_calls=turn.tool_calls,
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
