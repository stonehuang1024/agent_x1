"""Importance scoring algorithm for context messages.

Computes a relevance score for each message based on:
  - Role-based base score (system > user > assistant > tool)
  - Time decay (exponential decay based on turns ago)
  - Explicit importance override

Formula:
    score = base_score[role] × time_decay × explicit_importance

Where:
    base_score:  system=1.0, user=0.8, assistant=0.6, tool=0.4
    time_decay:  exp(-0.1 × turns_ago)
    explicit_importance:  Message.importance if != 0.5, else 1.0
"""

import math
from typing import Dict, List

from src.core.models import Message, Role

# Base scores by role — system messages are always most important
BASE_SCORES: Dict[str, float] = {
    Role.SYSTEM.value: 1.0,
    Role.USER.value: 0.8,
    Role.ASSISTANT.value: 0.6,
    Role.TOOL.value: 0.4,
}

# Decay rate for the exponential time decay
_DECAY_RATE = 0.1

# Default importance value (indicates "not explicitly set")
_DEFAULT_IMPORTANCE = 0.5


class ImportanceScorer:
    """Scores messages by importance for context compression decisions.

    Usage::

        scorer = ImportanceScorer()
        score = scorer.score(message, turns_ago=5)
        # or score a whole conversation:
        scorer.score_conversation(messages)
    """

    def __init__(
        self,
        base_scores: Dict[str, float] | None = None,
        decay_rate: float = _DECAY_RATE,
    ):
        self.base_scores = base_scores or dict(BASE_SCORES)
        self.decay_rate = decay_rate

    def score(self, message: Message, turns_ago: int = 0) -> float:
        """Compute importance score for a single message.

        Args:
            message: The message to score.
            turns_ago: How many turns ago this message occurred (0 = most recent).

        Returns:
            Float score in range (0, 1.0+]. Higher = more important.
        """
        # 1. Base score by role
        base = self.base_scores.get(message.role, 0.5)

        # 2. Time decay: exp(-decay_rate × turns_ago)
        time_decay = math.exp(-self.decay_rate * turns_ago)

        # 3. Explicit importance multiplier
        #    If importance == 0.5 (default), treat as "not set" → multiplier = 1.0
        #    Otherwise use the explicit value as a multiplier
        if message.importance != _DEFAULT_IMPORTANCE:
            explicit = message.importance
        else:
            explicit = 1.0

        return base * time_decay * explicit

    def score_conversation(self, messages: List[Message]) -> List[float]:
        """Score all messages in a conversation.

        Messages are assumed to be in chronological order (oldest first).
        ``turns_ago`` is computed relative to the last message.

        Returns:
            List of scores, same length as *messages*.
        """
        total = len(messages)
        scores = []
        for i, msg in enumerate(messages):
            turns_ago = total - 1 - i
            scores.append(self.score(msg, turns_ago=turns_ago))
        return scores

    def apply_scores(self, messages: List[Message]) -> None:
        """Score all messages and write the computed importance back.

        Only overwrites ``Message.importance`` when it is still at the
        default value (0.5).  Explicitly-set values are left untouched
        (they were already used as a multiplier in the formula).
        """
        total = len(messages)
        for i, msg in enumerate(messages):
            turns_ago = total - 1 - i
            computed = self.score(msg, turns_ago=turns_ago)
            if msg.importance == _DEFAULT_IMPORTANCE:
                msg.importance = computed
