"""
Integration Tests for Agent X1 Tools Library

Tests that verify tools work end-to-end together, including:
- Multi-tool workflows (write file → read file → analyze)
- PDF creation → read back → metadata
- PPT creation → read back → add slide
- Data pipeline: generate data → save CSV → filter → analyze → convert
- Bash + file pipeline: run command → capture output → write to file
- Tool registry completeness
"""

import sys
import os
import json
import tempfile
import shutil
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestFileToolsPipeline(unittest.TestCase):
    """Integration: multi-step file operation workflows."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_search_read_delete_pipeline(self):
        """Write files → search for content → read match → delete."""
        from src.tools.file_tools import write_file, search_in_files, read_file, delete_file

        # Write several files
        files = {
            "report_q1.txt": "Q1 revenue: $1.2M. Growth rate 15%.",
            "report_q2.txt": "Q2 revenue: $1.5M. Growth rate 20%.",
            "notes.txt": "Meeting notes - no financial data here.",
        }
        for fname, content in files.items():
            write_file(os.path.join(self.tmp, fname), content)

        # Search for 'revenue' across all .txt files
        search_result = search_in_files(self.tmp, "revenue", pattern="*.txt")
        self.assertNotIn("error", search_result)
        self.assertEqual(search_result["total_matches"], 2)

        # Read the first match
        match_file = search_result["results"][0]["file"]
        read_result = read_file(match_file)
        self.assertNotIn("error", read_result)
        self.assertIn("revenue", read_result["content"])

        # Delete one file
        del_result = delete_file(os.path.join(self.tmp, "notes.txt"))
        self.assertTrue(del_result["success"])

        # List dir - should have 2 remaining
        from src.tools.file_tools import list_directory
        list_result = list_directory(self.tmp, pattern="*.txt")
        self.assertEqual(list_result["count"], 2)

    def test_directory_tree_and_recursive_search(self):
        """Create nested directory tree and recursively search."""
        from src.tools.file_tools import create_directory, write_file, search_in_files

        subdir = os.path.join(self.tmp, "subdir")
        create_directory(subdir)
        write_file(os.path.join(self.tmp, "top.txt"), "target_keyword at top")
        write_file(os.path.join(subdir, "sub.txt"), "target_keyword in subdir")
        write_file(os.path.join(subdir, "other.txt"), "unrelated content")

        result = search_in_files(self.tmp, "target_keyword", pattern="*.txt", recursive=True)
        self.assertEqual(result["total_matches"], 2)

    def test_copy_and_move_pipeline(self):
        """Write → copy → move → verify locations."""
        from src.tools.file_tools import write_file, copy_file, move_file, get_file_info

        original = os.path.join(self.tmp, "original.txt")
        copied = os.path.join(self.tmp, "copied.txt")
        moved = os.path.join(self.tmp, "subdir", "moved.txt")

        write_file(original, "file content for pipeline test")

        copy_result = copy_file(original, copied)
        self.assertTrue(copy_result["success"])
        self.assertTrue(os.path.exists(original))
        self.assertTrue(os.path.exists(copied))

        move_result = move_file(copied, moved)
        self.assertTrue(move_result["success"])
        self.assertFalse(os.path.exists(copied))
        self.assertTrue(os.path.exists(moved))

        info = get_file_info(moved)
        self.assertEqual(info["type"], "file")
        self.assertIn("md5", info)


class TestBashFilePipeline(unittest.TestCase):
    """Integration: bash commands combined with file tools."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_command_output_to_file(self):
        """Run a shell command, capture stdout, write to file, read back."""
        from src.tools.bash_tools import run_command
        from src.tools.file_tools import write_file, read_file

        cmd_result = run_command("echo 'pipeline_result_line1' && echo 'pipeline_result_line2'")
        self.assertTrue(cmd_result["success"])

        out_file = os.path.join(self.tmp, "cmd_output.txt")
        write_result = write_file(out_file, cmd_result["stdout"])
        self.assertTrue(write_result["success"])

        read_result = read_file(out_file)
        self.assertIn("pipeline_result_line1", read_result["content"])
        self.assertIn("pipeline_result_line2", read_result["content"])

    def test_python_script_generated_and_executed(self):
        """Generate a Python script file, then execute it."""
        from src.tools.file_tools import write_file
        from src.tools.bash_tools import run_python_script

        script_content = (
            "import json\n"
            "data = {'result': 42, 'status': 'ok'}\n"
            "print(json.dumps(data))\n"
        )
        script_path = os.path.join(self.tmp, "generated_script.py")
        write_file(script_path, script_content)

        exec_result = run_python_script(script_path)
        self.assertTrue(exec_result["success"])
        output = json.loads(exec_result["stdout"].strip())
        self.assertEqual(output["result"], 42)

    def test_system_info_fields(self):
        """Verify system info returns expected structure."""
        from src.tools.bash_tools import get_system_info
        info = get_system_info()
        self.assertIn("os", info)
        self.assertIn("python_version", info)
        self.assertIn("cwd", info)
        self.assertIsNotNone(info["cpu_count"])


class TestPDFWorkflow(unittest.TestCase):
    """Integration: PDF create → merge → split → read."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_merge_split_read(self):
        """Full PDF lifecycle: create 3 PDFs → merge → split → read each."""
        from src.tools.pdf_tools import (
            create_pdf_from_text, merge_pdfs, split_pdf, read_pdf, get_pdf_metadata
        )

        pdf_paths = []
        for i in range(3):
            p = os.path.join(self.tmp, f"src_{i}.pdf")
            r = create_pdf_from_text(f"Content of page {i+1}", p, title=f"Page {i+1}")
            self.assertTrue(r["success"])
            pdf_paths.append(p)

        merged_path = os.path.join(self.tmp, "merged.pdf")
        merge_result = merge_pdfs(pdf_paths, merged_path)
        self.assertTrue(merge_result["success"])
        self.assertEqual(merge_result["total_pages"], 3)

        meta = get_pdf_metadata(merged_path)
        self.assertNotIn("error", meta)
        self.assertEqual(meta["total_pages"], 3)

        split_dir = os.path.join(self.tmp, "split")
        split_result = split_pdf(merged_path, split_dir, pages_per_file=1)
        self.assertNotIn("error", split_result)
        self.assertEqual(split_result["files_created"], 3)

        for split_file in split_result["files"]:
            read_result = read_pdf(split_file)
            self.assertNotIn("error", read_result)
            self.assertEqual(read_result["total_pages"], 1)


class TestPPTWorkflow(unittest.TestCase):
    """Integration: PPT create → read → add slide → verify."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_read_add_slide_pipeline(self):
        """Create presentation → read → add slide → verify count."""
        from src.tools.ppt_tools import create_presentation, read_presentation, add_slide

        slides = [
            {"title": "Introduction", "content": "This is the intro slide"},
            {"title": "Analysis", "content": ["Point 1", "Point 2", "Point 3"]},
            {"title": "Conclusion", "content": "Final summary here", "notes": "Speaker notes"},
        ]
        pptx_path = os.path.join(self.tmp, "report.pptx")
        create_result = create_presentation(slides, pptx_path, title="Integration Test", theme="blue")
        self.assertTrue(create_result["success"])
        self.assertEqual(create_result["slide_count"], 3)

        read_result = read_presentation(pptx_path)
        self.assertNotIn("error", read_result)
        self.assertEqual(read_result["slide_count"], 3)
        self.assertEqual(read_result["title"], "Integration Test")

        add_result = add_slide(pptx_path, "Appendix", "Additional data for appendix")
        self.assertTrue(add_result["success"])
        self.assertEqual(add_result["total_slides"], 4)

        final_read = read_presentation(pptx_path)
        self.assertEqual(final_read["slide_count"], 4)

    def test_all_themes_create_readable(self):
        """Each theme produces a readable .pptx."""
        from src.tools.ppt_tools import create_presentation, read_presentation

        for theme in ["default", "dark", "blue"]:
            path = os.path.join(self.tmp, f"{theme}.pptx")
            create_presentation(
                [{"title": f"{theme} theme", "content": "Body text"}],
                path, theme=theme
            )
            read_result = read_presentation(path)
            self.assertNotIn("error", read_result, f"Failed to read {theme} theme")
            self.assertEqual(read_result["slide_count"], 1)


class TestDataPipeline(unittest.TestCase):
    """Integration: data generation → CSV → filter → analyze → convert."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_data_pipeline(self):
        """Generate data → save CSV → read → filter → analyze → convert to JSON."""
        from src.tools.data_tools import save_as_csv, read_csv, filter_csv, analyze_dataframe, convert_data_format

        # Generate synthetic dataset
        records = [
            {"product": "Widget A", "region": "North", "sales": 100, "profit": 20.5},
            {"product": "Widget B", "region": "South", "sales": 150, "profit": 35.0},
            {"product": "Widget A", "region": "East", "sales": 80, "profit": 15.2},
            {"product": "Widget C", "region": "West", "sales": 200, "profit": 55.0},
            {"product": "Widget B", "region": "North", "sales": 120, "profit": 28.0},
            {"product": "Widget C", "region": "South", "sales": 90, "profit": 18.5},
        ]

        csv_path = os.path.join(self.tmp, "sales.csv")
        save_result = save_as_csv(records, csv_path)
        self.assertTrue(save_result["success"])
        self.assertEqual(save_result["rows"], 6)

        read_result = read_csv(csv_path)
        self.assertEqual(read_result["total_rows"], 6)
        self.assertIn("product", read_result["columns"])

        # Filter: sales > 100
        filter_result = filter_csv(csv_path, [{"column": "sales", "operator": ">", "value": 100}])
        self.assertNotIn("error", filter_result)
        self.assertEqual(filter_result["filtered_rows"], 3)

        # Filter: region = North
        north_result = filter_csv(csv_path, [{"column": "region", "operator": "==", "value": "North"}])
        self.assertEqual(north_result["filtered_rows"], 2)

        # Analyze
        analyze_result = analyze_dataframe(csv_path)
        self.assertNotIn("error", analyze_result)
        self.assertEqual(analyze_result["total_rows"], 6)
        self.assertIn("sales", analyze_result["statistics"])
        sales_stats = analyze_result["statistics"]["sales"]
        self.assertEqual(sales_stats["min"], 80)
        self.assertEqual(sales_stats["max"], 200)

        # Convert CSV → JSON
        json_path = os.path.join(self.tmp, "sales.json")
        convert_result = convert_data_format(csv_path, json_path, "csv", "json")
        self.assertTrue(convert_result["success"])
        self.assertTrue(os.path.exists(json_path))

        # Read back JSON
        from src.tools.data_tools import read_json_file
        json_result = read_json_file(json_path)
        self.assertNotIn("error", json_result)
        self.assertEqual(json_result["type"], "array")
        self.assertEqual(json_result["length"], 6)

    def test_filter_and_save(self):
        """Filter CSV and save results to a new file."""
        from src.tools.data_tools import save_as_csv, filter_csv, read_csv

        data = [{"val": i, "label": f"item_{i}"} for i in range(20)]
        in_path = os.path.join(self.tmp, "input.csv")
        out_path = os.path.join(self.tmp, "filtered.csv")
        save_as_csv(data, in_path)

        filter_csv(in_path, [{"column": "val", "operator": ">=", "value": 10}], output_path=out_path)
        self.assertTrue(os.path.exists(out_path))

        read_result = read_csv(out_path)
        self.assertEqual(read_result["total_rows"], 10)


class TestToolRegistryCatalog(unittest.TestCase):
    """Integration: registry catalog structure and tool discoverability."""

    def test_catalog_structure(self):
        from src.tools import TOOL_REGISTRY
        catalog = TOOL_REGISTRY.get_catalog()

        for cat_key, cat_data in catalog.items():
            self.assertIn("label", cat_data)
            self.assertIn("description", cat_data)
            self.assertIn("tool_count", cat_data)
            self.assertIn("tools", cat_data)
            self.assertGreater(cat_data["tool_count"], 0)
            for tool_entry in cat_data["tools"]:
                self.assertIn("name", tool_entry)
                self.assertIn("description", tool_entry)

    def test_all_schemas_json_serializable(self):
        """All tool schemas must be JSON-serializable (required for LLM APIs)."""
        from src.tools import TOOL_REGISTRY
        schemas = TOOL_REGISTRY.get_all_schemas()
        try:
            json.dumps(schemas)
        except (TypeError, ValueError) as e:
            self.fail(f"Tool schemas not JSON-serializable: {e}")

    def test_search_returns_relevant_tools(self):
        from src.tools import TOOL_REGISTRY

        test_cases = [
            ("stock", ["get_stock_kline", "get_stock_snapshot"]),
            ("shell", ["run_command", "run_bash_script"]),
            ("excel", ["read_excel"]),
            ("search", ["search_google", "web_search_exa"]),
        ]
        for keyword, expected_tools in test_cases:
            results = TOOL_REGISTRY.search(keyword)
            found_names = [r["name"] for r in results]
            for expected in expected_tools:
                self.assertIn(expected, found_names, f"'{expected}' not found in search('{keyword}')")

    def test_list_by_category(self):
        from src.tools import TOOL_REGISTRY
        file_tools = TOOL_REGISTRY.list_by_category("file")
        self.assertGreaterEqual(len(file_tools), 8)
        pdf_tools = TOOL_REGISTRY.list_by_category("pdf")
        self.assertGreaterEqual(len(pdf_tools), 5)
        bash_tools = TOOL_REGISTRY.list_by_category("bash")
        self.assertGreaterEqual(len(bash_tools), 4)

    def test_tool_execute_returns_json(self):
        """Every tool.execute() must return valid JSON."""
        from src.tools import ALL_TOOLS
        import json

        safe_test_inputs = {
            "calculate": '{"expression": "1+1"}',
            "get_current_time": '{}',
            "get_weather": '{"location": "Tokyo"}',
            "search_knowledge": '{"query": "python"}',
            "get_system_info": '{}',
            "get_environment_variable": '{"name": "PATH"}',
            "read_file": '{"path": "/nonexistent_test_path/file.txt"}',
            "write_file": '{"path": "/tmp/agent_x1_test_exec.txt", "content": "test"}',
            "list_directory": '{"path": "/tmp"}',
            "get_file_info": '{"path": "/tmp"}',
            "create_directory": '{"path": "/tmp/agent_x1_test_dir"}',
            "run_command": '{"command": "echo ok"}',
            "check_url": '{"url": "http://localhost:19999"}',
        }
        for tool in ALL_TOOLS:
            if tool.name in safe_test_inputs:
                raw = tool.execute(safe_test_inputs[tool.name])
                try:
                    json.loads(raw)
                except json.JSONDecodeError:
                    self.fail(f"Tool '{tool.name}' returned non-JSON: {raw[:200]}")


class TestBashPDFPPTIntegration(unittest.TestCase):
    """Integration: bash generates content → stored in PDF + PPT."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_bash_output_to_pdf(self):
        """Capture system info from bash, write it to a PDF."""
        from src.tools.bash_tools import get_system_info
        from src.tools.pdf_tools import create_pdf_from_text, read_pdf

        info = get_system_info()
        report_text = (
            f"System Report\n"
            f"OS: {info['os']}\n"
            f"Python: {info['python_version'][:30]}\n"
            f"CPU Count: {info['cpu_count']}\n"
        )

        pdf_path = os.path.join(self.tmp, "system_report.pdf")
        create_result = create_pdf_from_text(report_text, pdf_path, title="System Report")
        self.assertTrue(create_result["success"])

        read_result = read_pdf(pdf_path)
        self.assertNotIn("error", read_result)
        self.assertEqual(read_result["total_pages"], 1)

    def test_data_analysis_to_ppt(self):
        """Analyze data → build PPT with results."""
        from src.tools.data_tools import save_as_csv, analyze_dataframe
        from src.tools.ppt_tools import create_presentation, read_presentation

        # Create and analyze sample data
        records = [{"item": f"Item {i}", "value": i * 10} for i in range(1, 6)]
        csv_path = os.path.join(self.tmp, "data.csv")
        from src.tools.data_tools import save_as_csv
        save_as_csv(records, csv_path)

        analysis = analyze_dataframe(csv_path)
        self.assertNotIn("error", analysis)

        stats = analysis["statistics"].get("value", {})
        slides = [
            {
                "title": "Data Analysis Report",
                "content": f"Total rows: {analysis['total_rows']}"
            },
            {
                "title": "Value Statistics",
                "content": [
                    f"Min: {stats.get('min', 'N/A')}",
                    f"Max: {stats.get('max', 'N/A')}",
                    f"Mean: {stats.get('mean', 'N/A')}",
                ]
            }
        ]

        ppt_path = os.path.join(self.tmp, "analysis_report.pptx")
        ppt_result = create_presentation(slides, ppt_path, title="Analysis Report")
        self.assertTrue(ppt_result["success"])
        self.assertEqual(ppt_result["slide_count"], 2)

        read_result = read_presentation(ppt_path)
        self.assertNotIn("error", read_result)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestFileToolsPipeline))
    suite.addTests(loader.loadTestsFromTestCase(TestBashFilePipeline))
    suite.addTests(loader.loadTestsFromTestCase(TestPDFWorkflow))
    suite.addTests(loader.loadTestsFromTestCase(TestPPTWorkflow))
    suite.addTests(loader.loadTestsFromTestCase(TestDataPipeline))
    suite.addTests(loader.loadTestsFromTestCase(TestToolRegistryCatalog))
    suite.addTests(loader.loadTestsFromTestCase(TestBashPDFPPTIntegration))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
