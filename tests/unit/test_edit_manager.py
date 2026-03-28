"""
Unit tests for src/core/edit_manager.py

Tests cover:
- DiffParser: parse single/multi blocks, code fences, marker variants, errors
- SearchEngine: exact match, no match with suggestions, multiple matches
- EditApplier: single/multi block, order-invariant, overlapping, replace_all, delete
- FileEditingGuard: record/validate, reject unread, cache expiry, freshness
"""

import time
import unittest

from src.core.edit_manager import (
    DiffParser,
    DiffParseError,
    EditApplier,
    FileEditingGuard,
    ReplaceBlock,
    SearchEngine,
    get_edit_guard,
    reset_edit_guard,
)


class TestDiffParser(unittest.TestCase):
    """Tests for DiffParser.parse()."""

    def test_single_block(self):
        diff = (
            "------- SEARCH\n"
            "hello world\n"
            "=======\n"
            "hello universe\n"
            "+++++++ REPLACE"
        )
        blocks = DiffParser.parse(diff)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].search, "hello world")
        self.assertEqual(blocks[0].replace, "hello universe")

    def test_multi_block(self):
        diff = (
            "------- SEARCH\n"
            "line A\n"
            "=======\n"
            "line A modified\n"
            "+++++++ REPLACE\n"
            "------- SEARCH\n"
            "line B\n"
            "=======\n"
            "line B modified\n"
            "+++++++ REPLACE"
        )
        blocks = DiffParser.parse(diff)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0].search, "line A")
        self.assertEqual(blocks[0].replace, "line A modified")
        self.assertEqual(blocks[1].search, "line B")
        self.assertEqual(blocks[1].replace, "line B modified")

    def test_strip_code_fence(self):
        diff = (
            "```diff\n"
            "------- SEARCH\n"
            "old code\n"
            "=======\n"
            "new code\n"
            "+++++++ REPLACE\n"
            "```"
        )
        blocks = DiffParser.parse(diff)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].search, "old code")
        self.assertEqual(blocks[0].replace, "new code")

    def test_unclosed_block_error(self):
        diff = (
            "------- SEARCH\n"
            "some code\n"
            "=======\n"
            "new code"
            # Missing +++++++ REPLACE
        )
        with self.assertRaises(DiffParseError) as ctx:
            DiffParser.parse(diff)
        self.assertIn("Unclosed", str(ctx.exception))

    def test_empty_search_block(self):
        """Empty SEARCH block is valid — represents create-new-file semantics."""
        diff = (
            "------- SEARCH\n"
            "=======\n"
            "brand new content\n"
            "+++++++ REPLACE"
        )
        blocks = DiffParser.parse(diff)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].search, "")
        self.assertEqual(blocks[0].replace, "brand new content")

    def test_variant_markers(self):
        """Support <<<<<<< SEARCH and >>>>>>> REPLACE variants."""
        diff = (
            "<<<<<<< SEARCH\n"
            "old text\n"
            "=======\n"
            "new text\n"
            ">>>>>>> REPLACE"
        )
        blocks = DiffParser.parse(diff)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].search, "old text")
        self.assertEqual(blocks[0].replace, "new text")

    def test_empty_diff_error(self):
        with self.assertRaises(DiffParseError):
            DiffParser.parse("")

    def test_no_blocks_error(self):
        with self.assertRaises(DiffParseError):
            DiffParser.parse("just some random text\nwithout markers")

    def test_multiline_content(self):
        diff = (
            "------- SEARCH\n"
            "def foo():\n"
            "    return 1\n"
            "=======\n"
            "def foo():\n"
            "    return 2\n"
            "+++++++ REPLACE"
        )
        blocks = DiffParser.parse(diff)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].search, "def foo():\n    return 1")
        self.assertEqual(blocks[0].replace, "def foo():\n    return 2")


class TestSearchEngine(unittest.TestCase):
    """Tests for SearchEngine."""

    def test_exact_match(self):
        content = "line1\nline2\nline3\n"
        result = SearchEngine.find_exact(content, "line2")
        self.assertTrue(result.found)
        self.assertEqual(result.position, 6)
        self.assertEqual(result.end_position, 11)
        self.assertEqual(result.match_count, 1)

    def test_no_match(self):
        content = "line1\nline2\nline3\n"
        result = SearchEngine.find_exact(content, "line99")
        self.assertFalse(result.found)
        self.assertEqual(result.position, -1)
        self.assertIsNotNone(result.suggestions)

    def test_multiple_matches(self):
        content = "foo\nbar\nfoo\nbaz\nfoo\n"
        result = SearchEngine.find_exact(content, "foo")
        self.assertTrue(result.found)
        self.assertEqual(result.match_count, 3)

    def test_count_matches(self):
        content = "aaa bbb aaa ccc aaa"
        self.assertEqual(SearchEngine.count_matches(content, "aaa"), 3)
        self.assertEqual(SearchEngine.count_matches(content, "zzz"), 0)

    def test_find_near_matches_returns_suggestions(self):
        content = "def hello_world():\n    return 42\n\ndef goodbye_world():\n    return 0\n"
        suggestions = SearchEngine.find_near_matches(content, "def hello_word():\n    return 42")
        # Should find a near match for the typo 'hello_word' vs 'hello_world'
        self.assertTrue(len(suggestions) > 0)
        self.assertIn("similar", suggestions[0])

    def test_empty_search(self):
        result = SearchEngine.find_exact("some content", "")
        self.assertFalse(result.found)
        self.assertEqual(result.match_count, 0)


class TestEditApplier(unittest.TestCase):
    """Tests for EditApplier."""

    def test_single_block(self):
        content = "line1\nline2\nline3\n"
        blocks = [ReplaceBlock(search="line2", replace="LINE_TWO")]
        result = EditApplier.apply(content, blocks)
        self.assertTrue(result.success)
        self.assertEqual(result.new_content, "line1\nLINE_TWO\nline3\n")
        self.assertEqual(result.applied_count, 1)

    def test_multi_block_order_invariant(self):
        """Blocks provided in reverse order should still apply correctly."""
        content = "aaa\nbbb\nccc\nddd\n"
        # Blocks given in reverse file order
        blocks = [
            ReplaceBlock(search="ccc", replace="CCC"),
            ReplaceBlock(search="aaa", replace="AAA"),
        ]
        result = EditApplier.apply(content, blocks)
        self.assertTrue(result.success)
        self.assertEqual(result.new_content, "AAA\nbbb\nCCC\nddd\n")
        self.assertEqual(result.applied_count, 2)

    def test_overlapping_error(self):
        content = "abcdefgh"
        blocks = [
            ReplaceBlock(search="bcde", replace="BCDE"),
            ReplaceBlock(search="defg", replace="DEFG"),
        ]
        result = EditApplier.apply(content, blocks)
        self.assertFalse(result.success)
        self.assertIsNotNone(result.failed_edits)
        overlap_reasons = [fe.reason for fe in result.failed_edits]
        self.assertTrue(any("OVERLAPPING" in r for r in overlap_reasons))

    def test_replace_all(self):
        content = "foo bar foo baz foo"
        blocks = [ReplaceBlock(search="foo", replace="FOO")]
        result = EditApplier.apply(content, blocks, replace_all=True)
        self.assertTrue(result.success)
        self.assertEqual(result.new_content, "FOO bar FOO baz FOO")

    def test_delete_text(self):
        """Replace with empty string = delete."""
        content = "keep\nremove_me\nkeep_too\n"
        blocks = [ReplaceBlock(search="remove_me\n", replace="")]
        result = EditApplier.apply(content, blocks)
        self.assertTrue(result.success)
        self.assertEqual(result.new_content, "keep\nkeep_too\n")

    def test_no_match_returns_failure(self):
        content = "line1\nline2\n"
        blocks = [ReplaceBlock(search="nonexistent", replace="replacement")]
        result = EditApplier.apply(content, blocks)
        self.assertFalse(result.success)
        self.assertIsNotNone(result.failed_edits)
        self.assertEqual(len(result.failed_edits), 1)
        self.assertIn("SEARCH_NOT_FOUND", result.failed_edits[0].reason)

    def test_multiple_matches_returns_failure(self):
        content = "dup\nother\ndup\n"
        blocks = [ReplaceBlock(search="dup", replace="unique")]
        result = EditApplier.apply(content, blocks)
        self.assertFalse(result.success)
        self.assertIsNotNone(result.failed_edits)
        self.assertIn("MULTIPLE_MATCHES", result.failed_edits[0].reason)

    def test_empty_blocks(self):
        content = "unchanged"
        result = EditApplier.apply(content, [])
        self.assertTrue(result.success)
        self.assertEqual(result.new_content, "unchanged")
        self.assertEqual(result.applied_count, 0)

    def test_create_new_file_semantics(self):
        """Empty search + empty content = create new file."""
        blocks = [ReplaceBlock(search="", replace="new file content")]
        result = EditApplier.apply("", blocks)
        self.assertTrue(result.success)
        self.assertEqual(result.new_content, "new file content")

    def test_snippet_after_present(self):
        content = "line1\nline2\nline3\nline4\nline5\n"
        blocks = [ReplaceBlock(search="line3", replace="LINE_THREE")]
        result = EditApplier.apply(content, blocks)
        self.assertTrue(result.success)
        self.assertIsNotNone(result.snippet_after)


class TestFileEditingGuard(unittest.TestCase):
    """Tests for FileEditingGuard."""

    def setUp(self):
        self.guard = FileEditingGuard()

    def test_record_and_validate(self):
        self.guard.record_read("/tmp/test.py", "content")
        allowed, reason = self.guard.validate_edit("/tmp/test.py")
        self.assertTrue(allowed)
        self.assertIsNone(reason)

    def test_reject_unread(self):
        allowed, reason = self.guard.validate_edit("/tmp/never_read.py")
        self.assertFalse(allowed)
        self.assertIn("EDIT_DENIED", reason)
        self.assertIn("read_file", reason)

    def test_cache_expiry(self):
        self.guard.CACHE_TTL = 0.1  # 100ms for testing
        self.guard.record_read("/tmp/test.py", "content")

        # Immediately should be valid
        allowed, _ = self.guard.validate_edit("/tmp/test.py")
        self.assertTrue(allowed)

        # Wait for TTL to expire
        time.sleep(0.2)
        allowed, reason = self.guard.validate_edit("/tmp/test.py")
        self.assertFalse(allowed)
        self.assertIn("CACHE_EXPIRED", reason)

        # Restore default TTL
        self.guard.CACHE_TTL = 120

    def test_freshness_check(self):
        self.guard.record_read("/tmp/test.py", "original content")

        # Same content = fresh
        self.assertTrue(
            self.guard.verify_freshness("/tmp/test.py", "original content")
        )

        # Different content = stale
        self.assertFalse(
            self.guard.verify_freshness("/tmp/test.py", "modified content")
        )

    def test_freshness_crlf_tolerance(self):
        """CRLF vs LF difference should be tolerated."""
        self.guard.record_read("/tmp/test.py", "line1\nline2\n")
        self.assertTrue(
            self.guard.verify_freshness("/tmp/test.py", "line1\r\nline2\r\n")
        )

    def test_invalidate(self):
        self.guard.record_read("/tmp/test.py", "content")
        self.guard.invalidate("/tmp/test.py")
        allowed, reason = self.guard.validate_edit("/tmp/test.py")
        self.assertFalse(allowed)

    def test_reset(self):
        self.guard.record_read("/tmp/a.py", "a")
        self.guard.record_read("/tmp/b.py", "b")
        self.guard.reset()
        allowed_a, _ = self.guard.validate_edit("/tmp/a.py")
        allowed_b, _ = self.guard.validate_edit("/tmp/b.py")
        self.assertFalse(allowed_a)
        self.assertFalse(allowed_b)

    def test_get_cached_content(self):
        self.guard.record_read("/tmp/test.py", "cached!")
        self.assertEqual(self.guard.get_cached_content("/tmp/test.py"), "cached!")
        self.assertIsNone(self.guard.get_cached_content("/tmp/nonexistent.py"))


class TestEditGuardSingleton(unittest.TestCase):
    """Tests for module-level singleton functions."""

    def setUp(self):
        reset_edit_guard()

    def tearDown(self):
        reset_edit_guard()

    def test_get_edit_guard_returns_singleton(self):
        g1 = get_edit_guard()
        g2 = get_edit_guard()
        self.assertIs(g1, g2)

    def test_reset_edit_guard(self):
        g1 = get_edit_guard()
        g1.record_read("/tmp/test.py", "data")
        reset_edit_guard()
        g2 = get_edit_guard()
        self.assertIsNot(g1, g2)
        # New guard should not have old data
        allowed, _ = g2.validate_edit("/tmp/test.py")
        self.assertFalse(allowed)


if __name__ == "__main__":
    unittest.main()
