"""
Unit Tests for arXiv Tools.

Tests for arXiv search and download functionality.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.tools.arxiv_tools import (
    search_arxiv_papers,
    get_arxiv_paper_details,
    download_arxiv_pdf,
    _parse_atom_response,
    _parse_atom_entry,
    SEARCH_ARXIV_TOOL,
    GET_ARXIV_PAPER_DETAILS_TOOL,
    DOWNLOAD_ARXIV_PDF_TOOL,
    ARXIV_TOOLS,
    ARXIV_API_URL,
    ARXIV_PDF_URL,
)


class TestArXivAPIParsing(unittest.TestCase):
    """Test parsing of arXiv API responses."""

    def test_parse_empty_response(self):
        """Test parsing empty Atom feed."""
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <title>Search Results</title>
            <opensearch:totalResults xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">0</opensearch:totalResults>
            <opensearch:startIndex xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">0</opensearch:startIndex>
            <opensearch:itemsPerPage xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">10</opensearch:itemsPerPage>
        </feed>
        """
        result = _parse_atom_response(xml)
        self.assertEqual(result['total_results'], 0)
        self.assertEqual(len(result['papers']), 0)

    def test_parse_single_entry(self):
        """Test parsing single paper entry."""
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom" 
              xmlns:arxiv="http://arxiv.org/schemas/atom">
            <title>Search Results</title>
            <opensearch:totalResults xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">1</opensearch:totalResults>
            <opensearch:startIndex xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">0</opensearch:startIndex>
            <opensearch:itemsPerPage xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">10</opensearch:itemsPerPage>
            <entry>
                <id>http://arxiv.org/abs/2401.12345</id>
                <title>Test Paper Title</title>
                <summary>This is a test abstract.</summary>
                <published>2024-01-15T00:00:00Z</published>
                <updated>2024-01-20T00:00:00Z</updated>
                <author>
                    <name>John Doe</name>
                </author>
                <author>
                    <name>Jane Smith</name>
                </author>
                <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="cs.AI"/>
                <category term="cs.AI"/>
                <category term="cs.LG"/>
                <link rel="alternate" href="https://arxiv.org/abs/2401.12345"/>
                <link title="pdf" href="https://arxiv.org/pdf/2401.12345.pdf"/>
            </entry>
        </feed>
        """
        result = _parse_atom_response(xml)
        self.assertEqual(result['total_results'], 1)
        self.assertEqual(len(result['papers']), 1)
        
        paper = result['papers'][0]
        self.assertEqual(paper['id'], '2401.12345')
        self.assertEqual(paper['title'], 'Test Paper Title')
        self.assertEqual(paper['summary'], 'This is a test abstract.')
        self.assertEqual(paper['authors'], ['John Doe', 'Jane Smith'])
        self.assertEqual(paper['categories'], ['cs.AI', 'cs.LG'])

    def test_parse_paper_id_extraction(self):
        """Test various ID formats."""
        test_cases = [
            ('http://arxiv.org/abs/2401.12345', '2401.12345'),
            ('https://arxiv.org/abs/2401.12345', '2401.12345'),
            ('http://arxiv.org/abs/quant-ph/0102001', 'quant-ph/0102001'),
        ]
        
        for id_url, expected_id in test_cases:
            with self.subTest(id_url=id_url):
                xml = f"""<?xml version="1.0" encoding="utf-8"?>
                <feed xmlns="http://www.w3.org/2005/Atom">
                    <entry>
                        <id>{id_url}</id>
                        <title>Test</title>
                        <summary>Test</summary>
                    </entry>
                </feed>
                """
                result = _parse_atom_response(xml)
                self.assertEqual(len(result['papers']), 1)
                self.assertEqual(result['papers'][0]['id'], expected_id)


class TestSearchTool(unittest.TestCase):
    """Test search_arxiv_papers function."""

    @patch('src.tools.arxiv_tools._make_api_request')
    def test_successful_search(self, mock_request):
        """Test successful search returns formatted results."""
        mock_xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
            <opensearch:totalResults>100</opensearch:totalResults>
            <opensearch:startIndex>0</opensearch:startIndex>
            <opensearch:itemsPerPage>10</opensearch:itemsPerPage>
            <entry>
                <id>http://arxiv.org/abs/2401.12345</id>
                <title>Machine Learning Advances</title>
                <summary>New advances in ML</summary>
                <published>2024-01-15T00:00:00Z</published>
                <author><name>Alice Researcher</name></author>
                <arxiv:primary_category xmlns:arxiv="http://arxiv.org/schemas/atom" term="cs.AI"/>
                <link title="pdf" href="https://arxiv.org/pdf/2401.12345.pdf"/>
            </entry>
        </feed>
        """
        mock_request.return_value = mock_xml
        
        result_json = search_arxiv_papers("machine learning", max_results=5)
        result = json.loads(result_json)
        
        self.assertTrue(result['success'])
        self.assertEqual(result['total_results'], 100)
        self.assertEqual(len(result['papers']), 1)
        self.assertEqual(result['papers'][0]['title'], 'Machine Learning Advances')
        
        # Verify API was called with correct query
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        self.assertIn('search_query', call_args.kwargs)
        self.assertIn('machine learning', call_args.kwargs['search_query'])

    @patch('src.tools.arxiv_tools._make_api_request')
    def test_search_with_field(self, mock_request):
        """Test search with specific field."""
        mock_request.return_value = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
            <opensearch:totalResults>10</opensearch:totalResults>
            <opensearch:startIndex>0</opensearch:startIndex>
            <opensearch:itemsPerPage>10</opensearch:itemsPerPage>
        </feed>
        """
        
        result_json = search_arxiv_papers("John Doe", search_field="au")
        result = json.loads(result_json)
        
        self.assertTrue(result['success'])
        call_args = mock_request.call_args
        self.assertEqual(call_args.kwargs['search_query'], 'au:John Doe')

    @patch('src.tools.arxiv_tools._make_api_request')
    def test_search_with_category_filter(self, mock_request):
        """Test search with category filter."""
        mock_request.return_value = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
            <opensearch:totalResults>5</opensearch:totalResults>
            <opensearch:startIndex>0</opensearch:startIndex>
            <opensearch:itemsPerPage>10</opensearch:itemsPerPage>
        </feed>
        """
        
        result_json = search_arxiv_papers("quantum", category="physics.quant-ph")
        result = json.loads(result_json)
        
        self.assertTrue(result['success'])
        call_args = mock_request.call_args
        self.assertIn('AND', call_args.kwargs['search_query'])
        self.assertIn('cat:physics.quant-ph', call_args.kwargs['search_query'])

    @patch('src.tools.arxiv_tools._make_api_request')
    def test_failed_search(self, mock_request):
        """Test handling of API failure."""
        mock_request.return_value = None
        
        result_json = search_arxiv_papers("test query")
        result = json.loads(result_json)
        
        self.assertFalse(result['success'])
        self.assertIn('error', result)

    @patch('src.tools.arxiv_tools._make_api_request')
    def test_search_result_pagination(self, mock_request):
        """Test pagination parameters."""
        mock_request.return_value = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
            <opensearch:totalResults>50</opensearch:totalResults>
            <opensearch:startIndex>10</opensearch:startIndex>
            <opensearch:itemsPerPage>10</opensearch:itemsPerPage>
        </feed>
        """
        
        result_json = search_arxiv_papers("test", start=10, max_results=10)
        result = json.loads(result_json)
        
        self.assertTrue(result['success'])
        self.assertEqual(result['start'], 10)
        call_args = mock_request.call_args
        self.assertEqual(call_args.kwargs['start'], 10)
        self.assertEqual(call_args.kwargs['max_results'], 10)


class TestPaperDetailsTool(unittest.TestCase):
    """Test get_arxiv_paper_details function."""

    @patch('src.tools.arxiv_tools._make_api_request')
    def test_get_details_success(self, mock_request):
        """Test successful paper details fetch."""
        mock_xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
            <entry>
                <id>http://arxiv.org/abs/2401.12345</id>
                <title>Detailed Paper Title</title>
                <summary>Full abstract here with details.</summary>
                <published>2024-01-15T00:00:00Z</published>
                <updated>2024-01-20T00:00:00Z</updated>
                <author><name>Author One</name></author>
                <author><name>Author Two</name></author>
                <arxiv:comment>15 pages, 5 figures</arxiv:comment>
                <arxiv:journal_ref>Nature 123, 456 (2024)</arxiv:journal_ref>
                <arxiv:doi>10.1234/example.doi</arxiv:doi>
                <arxiv:primary_category term="cs.AI"/>
                <link rel="alternate" href="https://arxiv.org/abs/2401.12345"/>
            </entry>
        </feed>
        """
        mock_request.return_value = mock_xml
        
        result_json = get_arxiv_paper_details("2401.12345")
        result = json.loads(result_json)
        
        self.assertTrue(result['success'])
        paper = result['paper']
        self.assertEqual(paper['id'], '2401.12345')
        self.assertEqual(paper['title'], 'Detailed Paper Title')
        self.assertEqual(paper['summary'], 'Full abstract here with details.')
        self.assertEqual(len(paper['authors']), 2)
        self.assertEqual(paper['comment'], '15 pages, 5 figures')
        self.assertEqual(paper['journal'], 'Nature 123, 456 (2024)')
        self.assertEqual(paper['doi'], '10.1234/example.doi')

    @patch('src.tools.arxiv_tools._make_api_request')
    def test_get_details_not_found(self, mock_request):
        """Test paper not found."""
        mock_xml = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
        </feed>
        """
        mock_request.return_value = mock_xml
        
        result_json = get_arxiv_paper_details("9999.99999")
        result = json.loads(result_json)
        
        self.assertFalse(result['success'])
        self.assertIn('error', result)

    def test_get_details_id_formats(self):
        """Test various ID format inputs."""
        test_ids = [
            ('2401.12345', '2401.12345'),
            ('arxiv:2401.12345', '2401.12345'),
            ('  2401.12345  ', '2401.12345'),
        ]
        
        for input_id, expected_id in test_ids:
            with self.subTest(input_id=input_id):
                with patch('src.tools.arxiv_tools._make_api_request') as mock_request:
                    mock_xml = f"""<?xml version="1.0" encoding="utf-8"?>
                    <feed xmlns="http://www.w3.org/2005/Atom">
                        <entry>
                            <id>http://arxiv.org/abs/{expected_id}</id>
                            <title>Test</title>
                            <summary>Test</summary>
                        </entry>
                    </feed>
                    """
                    mock_request.return_value = mock_xml
                    
                    get_arxiv_paper_details(input_id)
                    
                    # Verify API called with cleaned ID
                    call_args = mock_request.call_args
                    self.assertEqual(call_args.kwargs['id_list'], [expected_id])


class TestDownloadTool(unittest.TestCase):
    """Test download_arxiv_pdf function."""

    @patch('src.tools.arxiv_tools.http_download')
    def test_download_success(self, mock_http_download):
        """Test successful PDF download."""
        pdf_bytes = b'%PDF-1.4 fake content here'
        
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = str(Path(tmpdir) / '2401.12345.pdf')
            
            def fake_download(url, output_path, headers=None, timeout=120):
                # Write fake PDF to the output path
                Path(output_path).write_bytes(pdf_bytes)
                return {
                    "success": True,
                    "file_path": output_path,
                    "size_bytes": len(pdf_bytes),
                    "status_code": 200,
                    "error": "",
                    "method": "requests",
                }
            
            mock_http_download.side_effect = fake_download
            
            result_json = download_arxiv_pdf("2401.12345", output_dir=tmpdir)
            result = json.loads(result_json)
            
            self.assertTrue(result['success'])
            self.assertEqual(result['arxiv_id'], '2401.12345')
            self.assertEqual(result['filename'], '2401.12345.pdf')
            self.assertFalse(result['already_exists'])
            
            # Verify file was created
            downloaded_file = Path(tmpdir) / '2401.12345.pdf'
            self.assertTrue(downloaded_file.exists())
            self.assertEqual(downloaded_file.read_bytes(), pdf_bytes)

    def test_download_already_exists(self):
        """Test download when file already exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create existing file
            existing_file = Path(tmpdir) / '2401.12345.pdf'
            existing_file.write_bytes(b'Existing content')
            
            result_json = download_arxiv_pdf("2401.12345", output_dir=tmpdir)
            result = json.loads(result_json)
            
            self.assertTrue(result['success'])
            self.assertTrue(result['already_exists'])
            self.assertEqual(result['size_bytes'], len(b'Existing content'))

    @patch('src.tools.arxiv_tools.http_download')
    def test_download_404(self, mock_http_download):
        """Test download when paper not found."""
        mock_http_download.return_value = {
            "success": False,
            "status_code": 404,
            "error": "HTTP 404",
            "method": "requests",
        }
        
        with tempfile.TemporaryDirectory() as tmpdir:
            result_json = download_arxiv_pdf("9999.99999", output_dir=tmpdir)
            result = json.loads(result_json)
            
            self.assertFalse(result['success'])
            self.assertIn('not found', result['error'].lower())

    @patch('src.tools.arxiv_tools.http_download')
    def test_download_custom_filename(self, mock_http_download):
        """Test download with custom filename."""
        pdf_bytes = b'%PDF-1.4 custom filename test'
        
        def fake_download(url, output_path, headers=None, timeout=120):
            Path(output_path).write_bytes(pdf_bytes)
            return {
                "success": True,
                "file_path": output_path,
                "size_bytes": len(pdf_bytes),
                "status_code": 200,
                "error": "",
                "method": "requests",
            }
        
        mock_http_download.side_effect = fake_download
        
        with tempfile.TemporaryDirectory() as tmpdir:
            result_json = download_arxiv_pdf(
                "2401.12345",
                output_dir=tmpdir,
                filename="my_paper.pdf"
            )
            result = json.loads(result_json)
            
            self.assertTrue(result['success'])
            self.assertEqual(result['filename'], 'my_paper.pdf')
            
            # Verify file was created with custom name
            downloaded_file = Path(tmpdir) / 'my_paper.pdf'
            self.assertTrue(downloaded_file.exists())


class TestToolDefinitions(unittest.TestCase):
    """Test tool definition objects."""

    def test_search_tool_definition(self):
        """Test search tool is properly defined."""
        self.assertEqual(SEARCH_ARXIV_TOOL.name, "search_arxiv_papers")
        self.assertIsNotNone(SEARCH_ARXIV_TOOL.description)
        self.assertIn("query", SEARCH_ARXIV_TOOL.parameters.get("properties", {}))

    def test_details_tool_definition(self):
        """Test details tool is properly defined."""
        self.assertEqual(GET_ARXIV_PAPER_DETAILS_TOOL.name, "get_arxiv_paper_details")
        self.assertIsNotNone(GET_ARXIV_PAPER_DETAILS_TOOL.description)
        self.assertIn("arxiv_id", GET_ARXIV_PAPER_DETAILS_TOOL.parameters.get("properties", {}))

    def test_download_tool_definition(self):
        """Test download tool is properly defined."""
        self.assertEqual(DOWNLOAD_ARXIV_PDF_TOOL.name, "download_arxiv_pdf")
        self.assertIsNotNone(DOWNLOAD_ARXIV_PDF_TOOL.description)
        self.assertIn("arxiv_id", DOWNLOAD_ARXIV_PDF_TOOL.parameters.get("properties", {}))

    def test_tool_list(self):
        """Test ARXIV_TOOLS list contains all tools."""
        self.assertEqual(len(ARXIV_TOOLS), 4)
        tool_names = [t.name for t in ARXIV_TOOLS]
        self.assertIn("search_arxiv_papers", tool_names)
        self.assertIn("get_arxiv_paper_details", tool_names)
        self.assertIn("download_arxiv_pdf", tool_names)
        self.assertIn("batch_download_arxiv_pdfs", tool_names)


class TestToolExecution(unittest.TestCase):
    """Test actual tool execution via Tool objects."""

    @patch('src.tools.arxiv_tools._make_api_request')
    def test_search_tool_execution(self, mock_request):
        """Test executing search tool."""
        mock_request.return_value = """<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
            <opensearch:totalResults>1</opensearch:totalResults>
            <entry>
                <id>http://arxiv.org/abs/2401.12345</id>
                <title>Test</title>
                <summary>Test abstract</summary>
                <published>2024-01-15T00:00:00Z</published>
            </entry>
        </feed>
        """
        
        result = SEARCH_ARXIV_TOOL.execute('{"query": "test"}')
        decoded = json.loads(result)
        if isinstance(decoded, str):
            decoded = json.loads(decoded)
        self.assertTrue(decoded["success"])
        self.assertEqual(decoded["total_results"], 1)

    def test_search_tool_invalid_json(self):
        """Test search tool with invalid JSON."""
        result = SEARCH_ARXIV_TOOL.execute('invalid json')
        # Tool should handle JSON parsing error
        self.assertIsNotNone(result)


class TestURLConstruction(unittest.TestCase):
    """Test API URL construction."""

    def test_api_url_constants(self):
        """Test API URLs are correct."""
        self.assertEqual(ARXIV_API_URL, "https://export.arxiv.org/api/query")
        self.assertEqual(ARXIV_PDF_URL, "https://arxiv.org/pdf")

    @patch('src.tools.arxiv_tools.urllib_get')
    def test_request_headers(self, mock_urllib_get):
        """Test request has proper headers."""
        mock_urllib_get.return_value = {
            "success": True,
            "status_code": 200,
            "data": "<feed/>",
            "error": "",
            "method": "urllib",
        }
        
        from src.tools.arxiv_tools import _make_api_request
        _make_api_request(search_query="test", max_results=1)
        
        # Verify urllib_get was called with headers
        mock_urllib_get.assert_called_once()
        call_args = mock_urllib_get.call_args
        headers = call_args.kwargs.get('headers', call_args[1].get('headers', {}))
        self.assertIn('User-Agent', headers)


def run_tests():
    """Run all arXiv tool unit tests."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    test_classes = [
        TestArXivAPIParsing,
        TestSearchTool,
        TestPaperDetailsTool,
        TestDownloadTool,
        TestToolDefinitions,
        TestToolExecution,
        TestURLConstruction,
    ]
    
    for test_class in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(test_class))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
