"""Context management tools — recall compressed conversation history.

Provides the ``recall_compressed_messages`` tool that allows the LLM to
retrieve the full original content of messages that were compressed during
the 5-Phase compression pipeline.
"""

import json
import logging
import math
from typing import Optional, List

from src.core.tool import Tool
from src.context.compression_archive import CompressionArchive

logger = logging.getLogger(__name__)

# Module-level archive instance — injected at startup via set_archive_instance()
_archive_instance: Optional[CompressionArchive] = None


def set_archive_instance(archive: CompressionArchive) -> None:
    """Inject the CompressionArchive instance used by the recall tool."""
    global _archive_instance
    _archive_instance = archive
    logger.debug("[context_tools] Archive instance set")


def recall_compressed_messages(
    archive_id: str,
    max_tokens: int = 4000,
) -> str:
    """Recall the full original messages from a compression archive.

    Args:
        archive_id: The archive identifier from a compression marker.
        max_tokens: Maximum tokens to return (default 4000).

    Returns:
        Formatted string with archive metadata header and message contents.
    """
    if _archive_instance is None:
        return json.dumps({
            "error": "Compression archive not available. No messages have been compressed yet."
        })

    messages = _archive_instance.recall(archive_id, max_tokens)

    if not messages:
        return json.dumps({
            "error": f"Archive '{archive_id}' not found. "
                     "It may have been from a previous session or already expired."
        })

    # Format messages for display
    lines = []
    total_chars = 0
    for msg in messages:
        content = msg.content or ""
        total_chars += len(content)
        lines.append(f"[{msg.role}]{' (' + msg.name + ')' if msg.name else ''}: {content}")

    original_tokens = math.ceil(total_chars / 3.5)
    showing_tokens = min(original_tokens, max_tokens)

    header = (
        f"[Archive Recall] archive_id={archive_id} | "
        f"messages={len(messages)} | "
        f"original_tokens={original_tokens} | "
        f"showing_tokens={showing_tokens}"
    )

    return header + "\n\n" + "\n\n".join(lines)


# Tool definition
RECALL_COMPRESSED_MESSAGES_TOOL = Tool(
    name="recall_compressed_messages",
    description=(
        "Retrieve the full original content of compressed conversation messages. "
        "Use this when a compressed summary (marked with <compression_metadata> or "
        "[... truncated ... | archive_id=<id>]) is insufficient for your current task. "
        "Provide the archive_id from the compression marker."
    ),
    parameters={
        "type": "object",
        "properties": {
            "archive_id": {
                "type": "string",
                "description": "The archive identifier from a compression marker.",
            },
            "max_tokens": {
                "type": "integer",
                "description": "Maximum tokens to return. Default 4000.",
                "default": 4000,
            },
        },
        "required": ["archive_id"],
    },
    func=recall_compressed_messages,
    is_readonly=True,
)

# List for batch registration
CONTEXT_TOOLS: List[Tool] = [RECALL_COMPRESSED_MESSAGES_TOOL]
