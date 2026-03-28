"""Tests for ImportanceScorer — message importance scoring algorithm.

Bug classes covered:
- Base scores wrong for a role (e.g. system != 1.0)
- Time decay formula incorrect (wrong rate, wrong direction)
- Explicit importance ignored or misapplied
- Default importance (0.5) treated as explicit instead of "not set"
- score_conversation() computes turns_ago incorrectly
- apply_scores() overwrites explicitly-set importance values
- Role not in BASE_SCORES causes crash instead of fallback
"""

import math
import pytest

from src.core.models import Message, Role
from src.context.importance_scorer import ImportanceScorer, BASE_SCORES, _DECAY_RATE, _DEFAULT_IMPORTANCE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scorer():
    return ImportanceScorer()


# ---------------------------------------------------------------------------
# 1. Base scores by role
#    Bug: wrong base score for a role breaks compression priority ordering
# ---------------------------------------------------------------------------

class TestBaseScores:
    """Catches: base score values deviating from design spec."""

    def test_system_base_score_is_highest(self):
        """system messages must have base_score=1.0 (highest).
        Bug: if system < 1.0, system prompts get compressed before user messages."""
        scorer = _scorer()
        msg = Message.system("You are helpful")
        score = scorer.score(msg, turns_ago=0)
        # At turns_ago=0, decay=1.0, default importance → multiplier=1.0
        assert score == 1.0, (
            f"system message at turns_ago=0 should score 1.0, got {score}"
        )

    def test_user_base_score(self):
        """user messages must have base_score=0.8."""
        scorer = _scorer()
        msg = Message.user("Hello")
        score = scorer.score(msg, turns_ago=0)
        assert score == 0.8, f"user message at turns_ago=0 should score 0.8, got {score}"

    def test_assistant_base_score(self):
        """assistant messages must have base_score=0.6."""
        scorer = _scorer()
        msg = Message.assistant("Response")
        score = scorer.score(msg, turns_ago=0)
        assert score == 0.6, f"assistant message at turns_ago=0 should score 0.6, got {score}"

    def test_tool_base_score(self):
        """tool messages must have base_score=0.4."""
        scorer = _scorer()
        msg = Message.tool("result", tool_call_id="tc1", name="read_file")
        score = scorer.score(msg, turns_ago=0)
        assert score == 0.4, f"tool message at turns_ago=0 should score 0.4, got {score}"

    def test_role_ordering_system_gt_user_gt_assistant_gt_tool(self):
        """Base scores must maintain strict ordering: system > user > assistant > tool.
        Bug: if ordering is wrong, compression removes the wrong messages."""
        scorer = _scorer()
        scores = {
            "system": scorer.score(Message.system("s"), turns_ago=0),
            "user": scorer.score(Message.user("u"), turns_ago=0),
            "assistant": scorer.score(Message.assistant("a"), turns_ago=0),
            "tool": scorer.score(Message.tool("t", "tc1", "fn"), turns_ago=0),
        }
        assert scores["system"] > scores["user"] > scores["assistant"] > scores["tool"], (
            f"Role ordering violated: {scores}"
        )


# ---------------------------------------------------------------------------
# 2. Time decay
#    Bug: decay formula wrong → old messages scored too high or too low
# ---------------------------------------------------------------------------

class TestTimeDecay:
    """Catches: time decay formula bugs."""

    def test_turns_ago_0_decay_is_1(self):
        """At turns_ago=0, time_decay must be exactly 1.0 (no decay).
        Bug: decay applied even to most recent message."""
        scorer = _scorer()
        msg = Message.user("recent")
        score = scorer.score(msg, turns_ago=0)
        expected = BASE_SCORES[Role.USER.value] * 1.0 * 1.0  # base * decay * explicit
        assert score == expected, (
            f"At turns_ago=0, score should be {expected}, got {score}"
        )

    def test_turns_ago_10_decay_approximately_0_37(self):
        """At turns_ago=10, time_decay ≈ exp(-1.0) ≈ 0.3679.
        Bug: wrong decay rate constant."""
        scorer = _scorer()
        msg = Message.user("old message")
        score = scorer.score(msg, turns_ago=10)

        expected_decay = math.exp(-_DECAY_RATE * 10)  # exp(-1.0) ≈ 0.3679
        expected = BASE_SCORES[Role.USER.value] * expected_decay * 1.0
        assert abs(score - expected) < 1e-10, (
            f"At turns_ago=10, score should be {expected:.6f}, got {score:.6f}. "
            f"Decay rate may be wrong (expected {_DECAY_RATE})."
        )

    def test_older_messages_score_lower(self):
        """Messages further in the past must score strictly lower.
        Bug: decay direction inverted (older = higher score)."""
        scorer = _scorer()
        msg = Message.user("content")
        score_recent = scorer.score(msg, turns_ago=0)
        score_mid = scorer.score(msg, turns_ago=5)
        score_old = scorer.score(msg, turns_ago=20)

        assert score_recent > score_mid > score_old, (
            f"Scores should decrease with age: recent={score_recent:.4f}, "
            f"mid={score_mid:.4f}, old={score_old:.4f}"
        )

    def test_very_old_message_approaches_zero(self):
        """At turns_ago=100, score should be very close to 0.
        Bug: decay doesn't actually reduce score over time."""
        scorer = _scorer()
        msg = Message.user("ancient")
        score = scorer.score(msg, turns_ago=100)
        assert score < 0.001, (
            f"Score at turns_ago=100 should be near 0, got {score:.6f}"
        )


# ---------------------------------------------------------------------------
# 3. Explicit importance handling
#    Bug: default 0.5 treated as explicit, or explicit values ignored
# ---------------------------------------------------------------------------

class TestExplicitImportance:
    """Catches: explicit importance multiplier bugs."""

    def test_default_importance_treated_as_not_set(self):
        """When importance=0.5 (default), multiplier must be 1.0, not 0.5.
        Bug: using 0.5 as multiplier halves all default scores."""
        scorer = _scorer()
        msg = Message.user("content")
        assert msg.importance == 0.5, "Precondition: default importance is 0.5"

        score = scorer.score(msg, turns_ago=0)
        # Should be base(0.8) * decay(1.0) * explicit(1.0) = 0.8
        assert score == 0.8, (
            f"Default importance (0.5) should use multiplier 1.0, "
            f"but score is {score} (expected 0.8). "
            f"Is 0.5 being used as the multiplier?"
        )

    def test_explicit_high_importance_boosts_score(self):
        """importance=1.5 must boost score above default (multiplier > 1.0).
        Bug: explicit importance ignored.
        Note: importance is used as a MULTIPLIER, so values > 1.0 boost."""
        scorer = _scorer()
        msg_default = Message.user("content")
        msg_high = Message.user("content")
        msg_high.importance = 1.5  # > 1.0 → boosts score

        score_default = scorer.score(msg_default, turns_ago=0)
        score_high = scorer.score(msg_high, turns_ago=0)

        assert score_high > score_default, (
            f"importance=1.5 (multiplier > 1.0) should score higher than default. "
            f"default={score_default}, high={score_high}"
        )

    def test_explicit_low_importance_reduces_score(self):
        """importance=0.2 must reduce score below default.
        Bug: explicit importance always boosts."""
        scorer = _scorer()
        msg_default = Message.user("content")
        msg_low = Message.user("content")
        msg_low.importance = 0.2

        score_default = scorer.score(msg_default, turns_ago=0)
        score_low = scorer.score(msg_low, turns_ago=0)

        assert score_low < score_default, (
            f"importance=0.2 should score lower than default. "
            f"default={score_default}, low={score_low}"
        )

    def test_explicit_importance_used_as_multiplier(self):
        """Explicit importance value must be used as the multiplier directly.
        Bug: importance added instead of multiplied."""
        scorer = _scorer()
        msg = Message.user("content")
        msg.importance = 0.9

        score = scorer.score(msg, turns_ago=0)
        expected = BASE_SCORES[Role.USER.value] * 1.0 * 0.9  # 0.8 * 1.0 * 0.9 = 0.72
        assert abs(score - expected) < 1e-10, (
            f"Score should be base(0.8) * decay(1.0) * explicit(0.9) = {expected}, "
            f"got {score}"
        )


# ---------------------------------------------------------------------------
# 4. score_conversation() turns_ago computation
#    Bug: turns_ago computed from wrong end, or off-by-one
# ---------------------------------------------------------------------------

class TestScoreConversation:
    """Catches: turns_ago computation bugs in batch scoring."""

    def test_last_message_has_turns_ago_0(self):
        """The last message in the list must have turns_ago=0 (most recent).
        Bug: turns_ago computed from start instead of end."""
        scorer = _scorer()
        msgs = [
            Message.system("sys"),
            Message.user("old"),
            Message.user("recent"),
        ]
        scores = scorer.score_conversation(msgs)

        # Last message (index 2) should have highest score for its role
        # because turns_ago=0 → decay=1.0
        last_score = scores[-1]
        expected_last = BASE_SCORES[Role.USER.value] * 1.0 * 1.0
        assert abs(last_score - expected_last) < 1e-10, (
            f"Last message should have turns_ago=0 (score={expected_last}), "
            f"got {last_score}"
        )

    def test_first_message_has_highest_turns_ago(self):
        """The first message must have turns_ago = len(messages) - 1.
        Bug: off-by-one in turns_ago calculation."""
        scorer = _scorer()
        msgs = [Message.user(f"msg{i}") for i in range(5)]
        scores = scorer.score_conversation(msgs)

        # First message: turns_ago = 4
        expected_first = BASE_SCORES[Role.USER.value] * math.exp(-_DECAY_RATE * 4)
        assert abs(scores[0] - expected_first) < 1e-10, (
            f"First of 5 messages should have turns_ago=4 (score={expected_first:.6f}), "
            f"got {scores[0]:.6f}"
        )

    def test_scores_length_matches_messages(self):
        """score_conversation must return exactly len(messages) scores."""
        scorer = _scorer()
        msgs = [Message.user(f"m{i}") for i in range(7)]
        scores = scorer.score_conversation(msgs)
        assert len(scores) == 7, f"Expected 7 scores, got {len(scores)}"

    def test_empty_conversation(self):
        """score_conversation on empty list must return empty list."""
        scorer = _scorer()
        scores = scorer.score_conversation([])
        assert scores == []


# ---------------------------------------------------------------------------
# 5. apply_scores() — writes back to Message.importance
#    Bug: overwrites explicitly-set importance, or doesn't write at all
# ---------------------------------------------------------------------------

class TestApplyScores:
    """Catches: apply_scores() overwriting explicit values or not writing defaults."""

    def test_overwrites_default_importance(self):
        """apply_scores must overwrite importance=0.5 (default) with computed score.
        Bug: importance never updated, stays at 0.5 forever."""
        scorer = _scorer()
        msg = Message.user("content")
        assert msg.importance == 0.5

        scorer.apply_scores([msg])

        assert msg.importance != 0.5, (
            f"apply_scores should overwrite default importance (0.5), "
            f"but it's still {msg.importance}"
        )

    def test_preserves_explicit_importance(self):
        """apply_scores must NOT overwrite importance that was explicitly set (!=0.5).
        Bug: apply_scores blindly overwrites all importance values."""
        scorer = _scorer()
        msg = Message.user("content")
        msg.importance = 0.95  # Explicitly set

        scorer.apply_scores([msg])

        assert msg.importance == 0.95, (
            f"apply_scores should preserve explicit importance (0.95), "
            f"but changed it to {msg.importance}"
        )

    def test_apply_scores_uses_correct_turns_ago(self):
        """apply_scores must compute turns_ago correctly (last msg = 0).
        Bug: all messages get turns_ago=0."""
        scorer = _scorer()
        msgs = [Message.user(f"m{i}") for i in range(3)]

        scorer.apply_scores(msgs)

        # Last message should have highest importance (turns_ago=0)
        assert msgs[-1].importance > msgs[0].importance, (
            f"Last message importance ({msgs[-1].importance}) should be > "
            f"first message importance ({msgs[0].importance})"
        )


# ---------------------------------------------------------------------------
# 6. Edge cases and robustness
#    Bug: unknown role crashes, negative turns_ago, etc.
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Catches: crashes on unexpected inputs."""

    def test_unknown_role_uses_fallback(self):
        """Messages with unknown role must not crash; should use fallback score.
        Bug: KeyError on unknown role."""
        scorer = _scorer()
        msg = Message(role="unknown_role", content="test")
        # Should not raise
        score = scorer.score(msg, turns_ago=0)
        assert isinstance(score, float)
        assert score > 0, "Unknown role should still produce a positive score"

    def test_negative_turns_ago_does_not_crash(self):
        """Negative turns_ago (invalid) must not crash.
        Bug: math.exp with large positive exponent overflows."""
        scorer = _scorer()
        msg = Message.user("test")
        # Should not raise
        score = scorer.score(msg, turns_ago=-1)
        assert isinstance(score, float)

    def test_zero_importance_as_explicit(self):
        """importance=0.0 must be treated as explicit (not default).
        Bug: 0.0 treated as falsy → uses default multiplier 1.0."""
        scorer = _scorer()
        msg = Message.user("content")
        msg.importance = 0.0

        score = scorer.score(msg, turns_ago=0)
        assert score == 0.0, (
            f"importance=0.0 should produce score=0.0, got {score}. "
            f"Is 0.0 being treated as 'not set'?"
        )

    def test_custom_base_scores(self):
        """ImportanceScorer must accept custom base_scores dict.
        Bug: custom scores ignored, always uses defaults."""
        custom = {"system": 0.5, "user": 0.5, "assistant": 0.5, "tool": 0.5}
        scorer = ImportanceScorer(base_scores=custom)

        sys_score = scorer.score(Message.system("s"), turns_ago=0)
        user_score = scorer.score(Message.user("u"), turns_ago=0)

        assert sys_score == user_score == 0.5, (
            f"Custom base_scores should make all roles equal. "
            f"system={sys_score}, user={user_score}"
        )
