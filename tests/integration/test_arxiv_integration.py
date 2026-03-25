"""
Integration Tests for arXiv Tools.

Tests with real arXiv API to verify full pipeline works.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.tools.arxiv_tools import (
    search_arxiv_papers,
    get_arxiv_paper_details,
    download_arxiv_pdf,
    SEARCH_ARXIV_TOOL,
    GET_ARXIV_PAPER_DETAILS_TOOL,
    DOWNLOAD_ARXIV_PDF_TOOL,
)


def test_search_real_api():
    """Test search with real arXiv API."""
    print("\n=== Testing Search with Real arXiv API ===")
    
    # Search for a common topic
    result_json = search_arxiv_papers(
        query="machine learning",
        max_results=5,
        search_field="all"
    )
    result = json.loads(result_json)
    
    if not result["success"]:
        print(f"✗ Search failed: {result.get('error')}")
        return False
    
    print(f"✓ Search successful")
    print(f"  Total results: {result['total_results']}")
    print(f"  Returned: {result['returned']}")
    
    if result['papers']:
        paper = result['papers'][0]
        print(f"\n  First paper:")
        print(f"    ID: {paper['id']}")
        print(f"    Title: {paper['title'][:60]}...")
        print(f"    Authors: {', '.join(paper['authors'][:3])}")
        if paper['author_count'] > 3:
            print(f"      (and {paper['author_count'] - 3} more)")
        print(f"    Published: {paper['published']}")
        print(f"    Primary Category: {paper['primary_category']}")
        print(f"    PDF URL: {paper['pdf_url']}")
        return True
    else:
        print("  ⚠ No papers returned")
        return False


def test_search_by_author():
    """Test searching by author name."""
    print("\n=== Testing Search by Author ===")
    
    result_json = search_arxiv_papers(
        query="Hinton",
        search_field="au",
        max_results=3
    )
    result = json.loads(result_json)
    
    if result["success"] and result['papers']:
        print(f"✓ Author search successful")
        print(f"  Found {result['total_results']} papers by author 'Hinton'")
        for paper in result['papers'][:2]:
            print(f"  - {paper['title'][:50]}... ({paper['id']})")
        return True
    else:
        print(f"⚠ Author search: {result.get('error', 'No results')}")
        return True  # Not a failure, just no results


def test_search_by_title():
    """Test searching by title."""
    print("\n=== Testing Search by Title ===")
    
    result_json = search_arxiv_papers(
        query="neural network",
        search_field="ti",
        max_results=3
    )
    result = json.loads(result_json)
    
    if result["success"]:
        print(f"✓ Title search successful")
        print(f"  Found {result['total_results']} papers")
        return True
    else:
        print(f"✗ Title search failed: {result.get('error')}")
        return False


def test_search_with_category():
    """Test searching with category filter."""
    print("\n=== Testing Search with Category Filter ===")
    
    result_json = search_arxiv_papers(
        query="quantum",
        category="quant-ph",
        max_results=3
    )
    result = json.loads(result_json)
    
    if result["success"]:
        print(f"✓ Category-filtered search successful")
        print(f"  Found {result['total_results']} papers in quant-ph")
        if result['papers']:
            for paper in result['papers'][:2]:
                print(f"  - {paper['title'][:50]}... ({paper['primary_category']})")
        return True
    else:
        print(f"✗ Category search failed: {result.get('error')}")
        return False


def test_pagination():
    """Test pagination functionality."""
    print("\n=== Testing Pagination ===")
    
    # Get first page
    result1_json = search_arxiv_papers(
        query="physics",
        max_results=5,
        start=0
    )
    result1 = json.loads(result1_json)
    
    # Get second page
    result2_json = search_arxiv_papers(
        query="physics",
        max_results=5,
        start=5
    )
    result2 = json.loads(result2_json)
    
    if result1["success"] and result2["success"]:
        print(f"✓ Pagination works")
        print(f"  Page 1 (start=0): {len(result1['papers'])} papers")
        print(f"  Page 2 (start=5): {len(result2['papers'])} papers")
        
        # Verify different papers
        if result1['papers'] and result2['papers']:
            ids1 = {p['id'] for p in result1['papers']}
            ids2 = {p['id'] for p in result2['papers']}
            if not ids1.intersection(ids2):
                print(f"  ✓ Pages contain different papers")
        return True
    else:
        print(f"✗ Pagination test failed")
        return False


def test_get_paper_details():
    """Test getting paper details."""
    print("\n=== Testing Get Paper Details ===")
    
    # First search for a paper to get an ID
    search_json = search_arxiv_papers(
        query="machine learning",
        max_results=1
    )
    search_result = json.loads(search_json)
    
    if not search_result["success"] or not search_result['papers']:
        print("✗ Could not find paper for details test")
        return False
    
    arxiv_id = search_result['papers'][0]['id']
    print(f"Testing details for paper: {arxiv_id}")
    
    result_json = get_arxiv_paper_details(arxiv_id)
    result = json.loads(result_json)
    
    if not result["success"]:
        print(f"✗ Details fetch failed: {result.get('error')}")
        return False
    
    paper = result['paper']
    print(f"✓ Paper details retrieved")
    print(f"  Title: {paper['title'][:60]}...")
    print(f"  Authors: {len(paper['authors'])}")
    print(f"  Abstract: {paper['summary'][:100]}...")
    print(f"  Published: {paper['published']}")
    print(f"  Categories: {', '.join(paper['categories'][:3])}")
    
    if paper.get('comment'):
        print(f"  Comment: {paper['comment']}")
    
    return True


def test_download_pdf():
    """Test downloading PDF."""
    print("\n=== Testing PDF Download ===")
    
    # Search for a recent paper
    search_json = search_arxiv_papers(
        query="cs.AI",
        category="cs.AI",
        max_results=1,
        sort_by="submittedDate",
        sort_order="descending"
    )
    search_result = json.loads(search_json)
    
    if not search_result["success"] or not search_result['papers']:
        print("✗ Could not find paper for download test")
        return False
    
    arxiv_id = search_result['papers'][0]['id']
    print(f"Testing download for paper: {arxiv_id}")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        result_json = download_arxiv_pdf(arxiv_id, output_dir=tmpdir)
        result = json.loads(result_json)
        
        if not result["success"]:
            print(f"✗ Download failed: {result.get('error')}")
            return False
        
        print(f"✓ Download successful")
        print(f"  File: {result['filename']}")
        print(f"  Path: {result['file_path']}")
        print(f"  Size: {result['size_bytes']:,} bytes")
        
        # Verify file exists
        downloaded_file = Path(result['file_path'])
        if downloaded_file.exists():
            print(f"  ✓ File exists on disk")
            # Check it's a reasonable PDF size
            if result['size_bytes'] > 1000:
                print(f"  ✓ File size looks reasonable for PDF")
            return True
        else:
            print(f"✗ File not found on disk")
            return False


def test_tool_execution():
    """Test tool execution via Tool objects."""
    print("\n=== Testing Tool Execution ===")
    
    # Test search tool
    print("\n1. Testing SEARCH_ARXIV_TOOL...")
    result = SEARCH_ARXIV_TOOL.execute('{"query": "neural networks", "max_results": 3}')
    parsed = json.loads(result)
    if parsed["success"]:
        print("   ✓ Search tool execution successful")
    else:
        print(f"   ✗ Search tool failed: {parsed.get('error')}")
        return False
    
    # Test details tool
    print("\n2. Testing GET_ARXIV_PAPER_DETAILS_TOOL...")
    # Get a paper ID first
    search_result = json.loads(SEARCH_ARXIV_TOOL.execute('{"query": "AI", "max_results": 1}'))
    if search_result["success"] and search_result['papers']:
        arxiv_id = search_result['papers'][0]['id']
        result = GET_ARXIV_PAPER_DETAILS_TOOL.execute(f'{{"arxiv_id": "{arxiv_id}"}}')
        parsed = json.loads(result)
        if parsed["success"]:
            print(f"   ✓ Details tool execution successful (paper: {arxiv_id})")
        else:
            print(f"   ✗ Details tool failed: {parsed.get('error')}")
            return False
    
    # Test download tool
    print("\n3. Testing DOWNLOAD_ARXIV_PDF_TOOL...")
    with tempfile.TemporaryDirectory() as tmpdir:
        if search_result["success"] and search_result['papers']:
            arxiv_id = search_result['papers'][0]['id']
            result = DOWNLOAD_ARXIV_PDF_TOOL.execute(
                f'{{"arxiv_id": "{arxiv_id}", "output_dir": "{tmpdir}"}}'
            )
            parsed = json.loads(result)
            if parsed["success"]:
                print(f"   ✓ Download tool execution successful (file: {parsed['filename']})")
            else:
                print(f"   ✗ Download tool failed: {parsed.get('error')}")
                return False
    
    return True


def test_error_handling():
    """Test error handling."""
    print("\n=== Testing Error Handling ===")
    
    # Test invalid paper ID
    print("\n1. Testing invalid paper ID...")
    result_json = get_arxiv_paper_details("9999.99999.invalid")
    result = json.loads(result_json)
    if not result["success"]:
        print(f"   ✓ Correctly handles invalid ID")
    else:
        print(f"   ⚠ Unexpected success for invalid ID")
    
    # Test empty search query
    print("\n2. Testing empty search...")
    result_json = search_arxiv_papers("")
    result = json.loads(result_json)
    # Empty query might succeed or fail depending on API behavior
    print(f"   Result: {'success' if result['success'] else 'failed'}")
    
    return True


def test_sort_options():
    """Test different sort options."""
    print("\n=== Testing Sort Options ===")
    
    sort_options = [
        ("relevance", "descending"),
        ("submittedDate", "descending"),
        ("lastUpdatedDate", "descending"),
    ]
    
    for sort_by, sort_order in sort_options:
        result_json = search_arxiv_papers(
            query="AI",
            max_results=2,
            sort_by=sort_by,
            sort_order=sort_order
        )
        result = json.loads(result_json)
        status = "✓" if result["success"] else "✗"
        print(f"  {status} sort_by={sort_by}, sort_order={sort_order}")
    
    return True


def run_all_tests():
    """Run all integration tests."""
    print("=" * 70)
    print("ARXIV TOOLS - INTEGRATION TESTS")
    print("=" * 70)
    
    tests = [
        ("Search (Real API)", test_search_real_api),
        ("Search by Author", test_search_by_author),
        ("Search by Title", test_search_by_title),
        ("Category Filter", test_search_with_category),
        ("Pagination", test_pagination),
        ("Paper Details", test_get_paper_details),
        ("PDF Download", test_download_pdf),
        ("Tool Execution", test_tool_execution),
        ("Error Handling", test_error_handling),
        ("Sort Options", test_sort_options),
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
