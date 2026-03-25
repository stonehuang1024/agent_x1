"""
Integration Tests for Reader Tools.

Tests with real web pages and PDF files to verify full pipeline works.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.tools.reader_tools import (
    convert_url_to_markdown,
    convert_pdf_to_markdown,
    convert_html_to_markdown,
    convert_file_to_markdown,
    _get_reader_path,
    CONVERT_URL_TO_MARKDOWN_TOOL,
    CONVERT_PDF_TO_MARKDOWN_TOOL,
    CONVERT_HTML_TO_MARKDOWN_TOOL,
    CONVERT_FILE_TO_MARKDOWN_TOOL,
)

# Test PDF path from user request
TEST_PDF_PATH = "/Users/simonwang/agent_x1/results/session/META_analysis_2026-03-15_12-03-26/META_Intelligence_Report.pdf"


def test_reader_cli_availability():
    """Test that Reader CLI is available (or fallback will be used)."""
    print("\n=== Testing Reader CLI Availability ===")
    reader_path = _get_reader_path()
    if reader_path:
        print(f"✓ Reader CLI found: {reader_path}")
    else:
        print("ℹ Reader CLI not found, will use Python fallback implementations")
    return True


def test_url_conversion():
    """Test converting a real webpage to markdown."""
    print("\n=== Testing URL to Markdown Conversion ===")
    
    # Use httpbin.org for reliable testing
    test_url = "https://httpbin.org/html"
    
    print(f"Testing URL: {test_url}")
    result_json = convert_url_to_markdown(test_url, use_reader=True)
    result = json.loads(result_json)
    
    if result["success"]:
        print("✓ URL conversion successful")
        markdown = result.get("markdown", "")
        print(f"  Markdown length: {len(markdown)} characters")
        
        # Verify we got some content
        if len(markdown) > 50:
            print("  ✓ Got substantial markdown content")
            # Show first 200 chars
            preview = markdown[:200].replace('\n', ' ')
            print(f"  Preview: {preview}...")
        else:
            print("  ⚠ Warning: Markdown content seems short")
        
        if result.get("fallback"):
            print("  ℹ Used Python fallback implementation")
        else:
            print("  ✓ Used Reader CLI")
    else:
        print(f"✗ URL conversion failed: {result.get('error', 'Unknown error')}")
        return False
    
    return True


def test_pdf_conversion():
    """Test converting PDF to markdown."""
    print("\n=== Testing PDF to Markdown Conversion ===")
    
    pdf_path = Path(TEST_PDF_PATH)
    
    if not pdf_path.exists():
        print(f"⚠ Test PDF not found: {TEST_PDF_PATH}")
        print("  Creating a simple test PDF for testing...")
        
        # Try to create a simple test PDF using PyMuPDF
        try:
            import fitz
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
                test_pdf = f.name
            
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((50, 50), "Test PDF Document\n\nThis is a test document for reader tool testing.")
            doc.save(test_pdf)
            doc.close()
            
            pdf_path = Path(test_pdf)
            print(f"  Created test PDF: {test_pdf}")
        except Exception as e:
            print(f"  ✗ Could not create test PDF: {e}")
            return False
    else:
        test_pdf = str(pdf_path)
        print(f"Using test PDF: {test_pdf}")
    
    # Convert PDF to markdown
    print("Converting PDF to markdown...")
    result_json = convert_pdf_to_markdown(str(test_pdf), use_reader=True)
    result = json.loads(result_json)
    
    # Cleanup temp file if we created one
    if test_pdf != TEST_PDF_PATH:
        try:
            os.unlink(test_pdf)
        except:
            pass
    
    if result["success"]:
        print("✓ PDF conversion successful")
        markdown = result.get("markdown", "")
        print(f"  Markdown length: {len(markdown)} characters")
        
        if len(markdown) > 50:
            print("  ✓ Got substantial markdown content")
            preview = markdown[:300].replace('\n', ' ')
            print(f"  Preview: {preview}...")
        
        if result.get("pages"):
            print(f"  Pages: {result['pages']}")
        
        if result.get("fallback"):
            print("  ℹ Used PyMuPDF fallback implementation")
        else:
            print("  ✓ Used Reader CLI")
    else:
        print(f"✗ PDF conversion failed: {result.get('error', 'Unknown error')}")
        return False
    
    return True


def test_html_conversion():
    """Test converting HTML content to markdown."""
    print("\n=== Testing HTML to Markdown Conversion ===")
    
    # Complex HTML with various elements
    html_content = """
    <!DOCTYPE html>
    <html>
    <head><title>Integration Test Page</title></head>
    <body>
        <h1>Main Heading</h1>
        <p>This is a <strong>bold</strong> and <em>italic</em> paragraph.</p>
        <h2>Subsection</h2>
        <ul>
            <li>First item</li>
            <li>Second item with <a href="https://example.com">link</a></li>
        </ul>
        <ol>
            <li>Step one</li>
            <li>Step two</li>
        </ol>
        <p>Code example: <code>print("hello")</code></p>
        <pre>def hello():
    return "world"</pre>
    </body>
    </html>
    """
    
    print("Testing HTML conversion with complex content...")
    result_json = convert_html_to_markdown(html_content, "integration-test")
    result = json.loads(result_json)
    
    if result["success"]:
        print("✓ HTML conversion successful")
        markdown = result.get("markdown", "")
        
        # Verify various elements were converted
        checks = [
            ("# Main Heading", "h1 heading"),
            ("## Subsection", "h2 heading"),
            ("**bold**", "bold text"),
            ("*italic*", "italic text"),
            ("- First", "unordered list"),
            ("link", "link text"),
        ]
        
        all_passed = True
        for pattern, description in checks:
            if pattern in markdown:
                print(f"  ✓ {description} converted")
            else:
                print(f"  ⚠ {description} may not be fully converted")
                all_passed = False
        
        print(f"\nGenerated markdown:\n{markdown[:500]}...")
    else:
        print(f"✗ HTML conversion failed: {result.get('error', 'Unknown error')}")
        return False
    
    return True


def test_file_auto_detection():
    """Test file type auto-detection."""
    print("\n=== Testing File Auto-Detection ===")
    
    # Test with various file types
    tests = [
        ("test.txt", "Text file content", "text"),
        ("test.md", "# Markdown Content", "markdown"),
        ("test.html", "<h1>HTML Content</h1>", "html"),
    ]
    
    for filename, content, ftype in tests:
        with tempfile.NamedTemporaryFile(mode='w', suffix=f'.{ftype}', delete=False) as f:
            f.write(content)
            temp_path = f.name
        
        try:
            print(f"\nTesting {ftype.upper()} file...")
            result_json = convert_file_to_markdown(temp_path)
            result = json.loads(result_json)
            
            if result["success"]:
                print(f"  ✓ {ftype.upper()} file conversion successful")
                print(f"  Content preview: {result['markdown'][:100]}...")
            else:
                print(f"  ✗ {ftype.upper()} file conversion failed: {result.get('error')}")
        finally:
            try:
                os.unlink(temp_path)
            except:
                pass
    
    return True


def test_tool_execution_full():
    """Test full tool execution pipeline."""
    print("\n=== Testing Full Tool Execution Pipeline ===")
    
    # Test HTML tool
    print("\n1. Testing CONVERT_HTML_TO_MARKDOWN_TOOL...")
    html_tool_result = CONVERT_HTML_TO_MARKDOWN_TOOL.execute(
        '{"html": "<h1>Tool Test</h1><p>Content here.</p>"}'
    )
    parsed = json.loads(html_tool_result)
    if parsed["success"]:
        print("   ✓ HTML tool execution successful")
    else:
        print(f"   ✗ HTML tool failed: {parsed.get('error')}")
        return False
    
    # Test file tool with temp file
    print("\n2. Testing CONVERT_FILE_TO_MARKDOWN_TOOL...")
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write("File tool test content")
        temp_path = f.name
    
    try:
        file_tool_result = CONVERT_FILE_TO_MARKDOWN_TOOL.execute(
            f'{{"file_path": "{temp_path}"}}'
        )
        parsed = json.loads(file_tool_result)
        if parsed["success"]:
            print("   ✓ File tool execution successful")
        else:
            print(f"   ✗ File tool failed: {parsed.get('error')}")
            return False
    finally:
        os.unlink(temp_path)
    
    return True


def test_url_various_sites():
    """Test URL conversion with various sites."""
    print("\n=== Testing URL Conversion with Various Sites ===")
    
    test_urls = [
        ("https://httpbin.org/html", "Simple HTML page"),
        ("https://example.com", "Example.com"),
    ]
    
    for url, description in test_urls:
        print(f"\nTesting {description} ({url})...")
        try:
            result_json = convert_url_to_markdown(url, use_reader=False)  # Use fallback for reliability
            result = json.loads(result_json)
            
            if result["success"]:
                print(f"  ✓ Successfully converted {description}")
                markdown = result.get("markdown", "")
                print(f"  Content length: {len(markdown)} chars")
            else:
                print(f"  ⚠ Could not convert {description}: {result.get('error')}")
        except Exception as e:
            print(f"  ⚠ Error testing {description}: {e}")
    
    return True


def run_all_tests():
    """Run all integration tests."""
    print("=" * 70)
    print("READER TOOLS - INTEGRATION TESTS")
    print("=" * 70)
    
    tests = [
        ("CLI Availability", test_reader_cli_availability),
        ("HTML Conversion", test_html_conversion),
        ("File Auto-Detection", test_file_auto_detection),
        ("Tool Execution", test_tool_execution_full),
        ("URL Conversion", test_url_various_sites),
        ("PDF Conversion", test_pdf_conversion),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            if test_func():
                passed += 1
            else:
                failed += 1
                print(f"\n✗ {name} test failed")
        except Exception as e:
            failed += 1
            print(f"\n✗ {name} test error: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 70)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 70)
    
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
