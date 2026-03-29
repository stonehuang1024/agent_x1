"""
Unit Tests for Reader Tools.

Tests for HTML/PDF to markdown conversion functionality.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.tools.reader_tools import (
    convert_html_to_markdown,
    convert_file_to_markdown,
    _fallback_html_to_markdown,
    _fallback_pdf_to_markdown,
    CONVERT_URL_TO_MARKDOWN_TOOL,
    CONVERT_PDF_TO_MARKDOWN_TOOL,
    CONVERT_HTML_TO_MARKDOWN_TOOL,
    CONVERT_FILE_TO_MARKDOWN_TOOL,
)


class TestHTMLToMarkdown(unittest.TestCase):
    """Test HTML to markdown conversion."""

    def test_simple_html_conversion(self):
        """Test basic HTML to markdown conversion."""
        html = "<h1>Title</h1><p>This is a paragraph.</p>"
        result = _fallback_html_to_markdown(html, "test")
        
        self.assertTrue(result["success"])
        self.assertIn("# Title", result["markdown"])
        self.assertIn("This is a paragraph.", result["markdown"])
        self.assertEqual(result["format"], "markdown")
        self.assertTrue(result.get("fallback", False))

    def test_html_with_headings(self):
        """Test HTML with multiple headings."""
        html = """
        <h1>Main Title</h1>
        <h2>Section 1</h2>
        <h3>Subsection</h3>
        <p>Content here.</p>
        """
        result = _fallback_html_to_markdown(html, "test")
        
        self.assertTrue(result["success"])
        markdown = result["markdown"]
        self.assertIn("# Main Title", markdown)
        self.assertIn("## Section 1", markdown)
        self.assertIn("### Subsection", markdown)

    def test_html_with_lists(self):
        """Test HTML with lists."""
        html = """
        <ul>
            <li>Item 1</li>
            <li>Item 2</li>
        </ul>
        <ol>
            <li>First</li>
            <li>Second</li>
        </ol>
        """
        result = _fallback_html_to_markdown(html, "test")
        
        self.assertTrue(result["success"])
        markdown = result["markdown"]
        self.assertIn("- Item 1", markdown)
        self.assertIn("- Item 2", markdown)
        self.assertIn("1. First", markdown)
        self.assertIn("2. Second", markdown)

    def test_html_with_links(self):
        """Test HTML with anchor tags."""
        html = '<a href="https://example.com">Click here</a>'
        result = _fallback_html_to_markdown(html, "test")
        
        self.assertTrue(result["success"])
        self.assertIn("[Click here](https://example.com)", result["markdown"])

    def test_html_with_formatting(self):
        """Test HTML with formatting tags."""
        html = "<strong>Bold</strong> <em>Italic</em> <code>code</code>"
        result = _fallback_html_to_markdown(html, "test")
        
        self.assertTrue(result["success"])
        markdown = result["markdown"]
        self.assertIn("**Bold**", markdown)
        self.assertIn("*Italic*", markdown)
        self.assertIn("`code`", markdown)

    def test_empty_html(self):
        """Test empty HTML handling."""
        html = ""
        result = _fallback_html_to_markdown(html, "test")
        self.assertTrue(result["success"])

    def test_html_with_title(self):
        """Test HTML with title tag."""
        html = """
        <html>
        <head><title>Page Title</title></head>
        <body><h1>Header</h1><p>Content.</p></body>
        </html>
        """
        result = _fallback_html_to_markdown(html, "test")
        
        self.assertTrue(result["success"])
        self.assertEqual(result.get("title"), "Page Title")


class TestToolFunctions(unittest.TestCase):
    """Test tool function wrappers."""

    def test_convert_html_to_markdown_tool(self):
        """Test the HTML conversion tool function."""
        html = "<h1>Test</h1><p>Content</p>"
        result_json = convert_html_to_markdown(html, "test")
        result = json.loads(result_json)
        
        self.assertTrue(result["success"])
        self.assertIn("markdown", result)
        self.assertIn("# Test", result["markdown"])

    def test_convert_file_to_markdown_nonexistent(self):
        """Test file conversion with non-existent file."""
        result_json = convert_file_to_markdown("/nonexistent/file.pdf")
        result = json.loads(result_json)
        
        self.assertFalse(result["success"])
        self.assertIn("error", result)
        self.assertIn("not found", result["error"].lower())

    def test_convert_file_to_markdown_txt(self):
        """Test file conversion with text file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("Test content")
            temp_path = f.name
        
        try:
            result_json = convert_file_to_markdown(temp_path)
            result = json.loads(result_json)
            
            self.assertTrue(result["success"])
            self.assertEqual(result["markdown"], "Test content")
            self.assertEqual(result["format"], "text")
        finally:
            os.unlink(temp_path)

    def test_convert_file_to_markdown_md(self):
        """Test file conversion with markdown file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# Markdown Content")
            temp_path = f.name
        
        try:
            result_json = convert_file_to_markdown(temp_path)
            result = json.loads(result_json)
            
            self.assertTrue(result["success"])
            self.assertEqual(result["markdown"], "# Markdown Content")
            self.assertEqual(result["format"], "markdown")
        finally:
            os.unlink(temp_path)

    def test_convert_file_to_markdown_html(self):
        """Test file conversion with HTML file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
            f.write("<h1>HTML Title</h1><p>Content</p>")
            temp_path = f.name
        
        try:
            result_json = convert_file_to_markdown(temp_path)
            result = json.loads(result_json)
            
            self.assertTrue(result["success"])
            self.assertIn("markdown", result)
            self.assertIn("# HTML Title", result["markdown"])
        finally:
            os.unlink(temp_path)

    def test_convert_file_to_markdown_unsupported(self):
        """Test file conversion with unsupported extension."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.xyz', delete=False) as f:
            f.write("content")
            temp_path = f.name
        
        try:
            result_json = convert_file_to_markdown(temp_path)
            result = json.loads(result_json)
            
            self.assertFalse(result["success"])
            self.assertIn("error", result)
            self.assertIn("unsupported", result["error"].lower())
        finally:
            os.unlink(temp_path)


class TestPDFToMarkdown(unittest.TestCase):
    """Test PDF to markdown conversion."""

    def test_pdf_conversion_without_pymupdf(self):
        """Test PDF conversion when PyMuPDF is not available."""
        # This test checks that the error handling works
        # when PDF libraries are not available
        try:
            import fitz
            # If fitz is available, we can't really test the fallback
            # So we'll just create a minimal test
            self.skipTest("PyMuPDF is available, skipping fallback test")
        except ImportError:
            result = _fallback_pdf_to_markdown("/fake/path.pdf")
            self.assertFalse(result["success"])
            self.assertIn("error", result)


class TestToolDefinitions(unittest.TestCase):
    """Test tool definition objects."""

    def test_url_tool_definition(self):
        """Test URL conversion tool is properly defined."""
        self.assertEqual(CONVERT_URL_TO_MARKDOWN_TOOL.name, "convert_url_to_markdown")
        self.assertIsNotNone(CONVERT_URL_TO_MARKDOWN_TOOL.description)
        self.assertIn("url", CONVERT_URL_TO_MARKDOWN_TOOL.parameters.get("properties", {}))

    def test_pdf_tool_definition(self):
        """Test PDF conversion tool is properly defined."""
        self.assertEqual(CONVERT_PDF_TO_MARKDOWN_TOOL.name, "convert_pdf_to_markdown")
        self.assertIsNotNone(CONVERT_PDF_TO_MARKDOWN_TOOL.description)
        self.assertIn("file_path", CONVERT_PDF_TO_MARKDOWN_TOOL.parameters.get("properties", {}))

    def test_html_tool_definition(self):
        """Test HTML conversion tool is properly defined."""
        self.assertEqual(CONVERT_HTML_TO_MARKDOWN_TOOL.name, "convert_html_to_markdown")
        self.assertIsNotNone(CONVERT_HTML_TO_MARKDOWN_TOOL.description)
        self.assertIn("html", CONVERT_HTML_TO_MARKDOWN_TOOL.parameters.get("properties", {}))

    def test_file_tool_definition(self):
        """Test file conversion tool is properly defined."""
        self.assertEqual(CONVERT_FILE_TO_MARKDOWN_TOOL.name, "convert_file_to_markdown")
        self.assertIsNotNone(CONVERT_FILE_TO_MARKDOWN_TOOL.description)
        self.assertIn("file_path", CONVERT_FILE_TO_MARKDOWN_TOOL.parameters.get("properties", {}))


class TestToolExecution(unittest.TestCase):
    """Test actual tool execution via Tool objects."""

    def test_html_tool_execution(self):
        """Test executing HTML tool."""
        result = CONVERT_HTML_TO_MARKDOWN_TOOL.execute(
            '{"html": "<h1>Test</h1><p>Content</p>"}'
        )
        # Tool.execute() wraps result in json.dumps; reader funcs already return JSON strings,
        # so we need to double-decode
        decoded = json.loads(result)
        if isinstance(decoded, str):
            decoded = json.loads(decoded)
        self.assertTrue(decoded["success"])
        self.assertIn("# Test", decoded["markdown"])

    def test_file_tool_execution_with_txt(self):
        """Test executing file tool with text file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("Tool test content")
            temp_path = f.name
        
        try:
            result = CONVERT_FILE_TO_MARKDOWN_TOOL.execute(
                f'{{"file_path": "{temp_path}"}}'
            )
            decoded = json.loads(result)
            if isinstance(decoded, str):
                decoded = json.loads(decoded)
            self.assertTrue(decoded["success"])
            self.assertEqual(decoded["markdown"], "Tool test content")
        finally:
            os.unlink(temp_path)

    def test_html_tool_with_source(self):
        """Test HTML tool with source parameter."""
        result = CONVERT_HTML_TO_MARKDOWN_TOOL.execute(
            '{"html": "<p>Test</p>", "source": "test-source"}'
        )
        decoded = json.loads(result)
        if isinstance(decoded, str):
            decoded = json.loads(decoded)
        self.assertTrue(decoded["success"])


def run_tests():
    """Run all reader tool tests."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestHTMLToMarkdown))
    suite.addTests(loader.loadTestsFromTestCase(TestToolFunctions))
    suite.addTests(loader.loadTestsFromTestCase(TestPDFToMarkdown))
    suite.addTests(loader.loadTestsFromTestCase(TestToolDefinitions))
    suite.addTests(loader.loadTestsFromTestCase(TestToolExecution))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
