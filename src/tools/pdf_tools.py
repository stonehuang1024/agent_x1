"""
PDF Tools Module - PDF file operations.

Provides tools for:
- Reading PDF text content
- Merging multiple PDFs
- Splitting PDF pages
- Extracting PDF metadata
- Creating simple PDFs from text
"""

import logging
import os
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

from ..core.tool import Tool

logger = logging.getLogger(__name__)


def read_pdf(path: str, pages: Optional[List[int]] = None, max_chars: int = 50000) -> Dict[str, Any]:
    """
    Extract text content from a PDF file.

    Args:
        path: Path to PDF file
        pages: List of page numbers to extract (1-indexed). None = all pages.
        max_chars: Maximum total characters to return

    Returns:
        Dictionary with extracted text per page
    """
    try:
        import pymupdf as fitz
    except ImportError:
        return {"error": "pymupdf not installed. Run: pip install pymupdf"}

    try:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            return {"error": f"File not found: {path}"}

        doc = fitz.open(str(resolved))
        total_pages = len(doc)
        page_indices = [p - 1 for p in pages] if pages else list(range(total_pages))
        page_indices = [i for i in page_indices if 0 <= i < total_pages]

        extracted = {}
        total_chars = 0
        for idx in page_indices:
            page = doc[idx]
            text = page.get_text()
            if total_chars + len(text) > max_chars:
                text = text[:max_chars - total_chars]
                extracted[idx + 1] = text
                total_chars = max_chars
                break
            extracted[idx + 1] = text
            total_chars += len(text)

        doc.close()
        return {
            "path": str(resolved),
            "total_pages": total_pages,
            "extracted_pages": list(extracted.keys()),
            "total_chars": total_chars,
            "truncated": total_chars >= max_chars,
            "pages": extracted
        }
    except Exception as e:
        logger.exception("[ReadPDF] Failed")
        return {"error": str(e), "path": path}


def get_pdf_metadata(path: str) -> Dict[str, Any]:
    """
    Extract metadata from a PDF file.

    Args:
        path: Path to PDF file

    Returns:
        Dictionary with metadata fields
    """
    try:
        import pymupdf as fitz
    except ImportError:
        return {"error": "pymupdf not installed. Run: pip install pymupdf"}

    try:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            return {"error": f"File not found: {path}"}

        doc = fitz.open(str(resolved))
        meta = doc.metadata
        page_count = len(doc)
        doc.close()

        return {
            "path": str(resolved),
            "title": meta.get("title"),
            "author": meta.get("author"),
            "subject": meta.get("subject"),
            "keywords": meta.get("keywords"),
            "creator": meta.get("creator"),
            "producer": meta.get("producer"),
            "creation_date": meta.get("creationDate"),
            "modification_date": meta.get("modDate"),
            "total_pages": page_count,
            "file_size_bytes": resolved.stat().st_size
        }
    except Exception as e:
        logger.exception("[GetPDFMetadata] Failed")
        return {"error": str(e), "path": path}


def merge_pdfs(input_paths: List[str], output_path: str) -> Dict[str, Any]:
    """
    Merge multiple PDF files into one.

    Args:
        input_paths: List of PDF file paths to merge (in order)
        output_path: Output PDF file path

    Returns:
        Dictionary with result
    """
    try:
        import pymupdf as fitz
    except ImportError:
        return {"error": "pymupdf not installed. Run: pip install pymupdf"}

    try:
        result_doc = fitz.open()
        total_pages = 0

        for pdf_path in input_paths:
            resolved = Path(pdf_path).expanduser().resolve()
            if not resolved.exists():
                return {"error": f"Input file not found: {pdf_path}"}
            src = fitz.open(str(resolved))
            result_doc.insert_pdf(src)
            total_pages += len(src)
            src.close()

        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        result_doc.save(str(out))
        result_doc.close()

        return {
            "output_path": str(out),
            "input_files": len(input_paths),
            "total_pages": total_pages,
            "file_size_bytes": out.stat().st_size,
            "success": True
        }
    except Exception as e:
        logger.exception("[MergePDFs] Failed")
        return {"error": str(e)}


def split_pdf(path: str, output_dir: str, pages_per_file: int = 1) -> Dict[str, Any]:
    """
    Split a PDF into multiple files.

    Args:
        path: Source PDF file path
        output_dir: Directory to save split files
        pages_per_file: Number of pages per output file (default: 1)

    Returns:
        Dictionary with list of created files
    """
    try:
        import pymupdf as fitz
    except ImportError:
        return {"error": "pymupdf not installed. Run: pip install pymupdf"}

    try:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            return {"error": f"File not found: {path}"}

        out_dir = Path(output_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        doc = fitz.open(str(resolved))
        total = len(doc)
        created_files = []

        for start in range(0, total, pages_per_file):
            end = min(start + pages_per_file, total)
            sub = fitz.open()
            sub.insert_pdf(doc, from_page=start, to_page=end - 1)
            fname = f"{resolved.stem}_p{start + 1}-{end}.pdf"
            out_path = out_dir / fname
            sub.save(str(out_path))
            sub.close()
            created_files.append(str(out_path))

        doc.close()
        return {
            "source": str(resolved),
            "output_dir": str(out_dir),
            "total_pages": total,
            "files_created": len(created_files),
            "files": created_files
        }
    except Exception as e:
        logger.exception("[SplitPDF] Failed")
        return {"error": str(e)}


def create_pdf_from_text(text: str, output_path: str, title: str = "", font_size: int = 11) -> Dict[str, Any]:
    """
    Create a PDF document from plain text content. Supports multi-page for long text.

    Args:
        text: Text content to put in the PDF
        output_path: Output PDF file path
        title: Optional document title
        font_size: Font size (default: 11)

    Returns:
        Dictionary with result
    """
    try:
        import pymupdf as fitz
    except ImportError:
        return {"error": "pymupdf not installed. Run: pip install pymupdf"}

    try:
        doc = fitz.open()
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)

        # Page setup (A4)
        margin = 50
        page_width = 595
        page_height = 842
        content_width = page_width - 2 * margin
        content_height = page_height - 2 * margin
        
        # Title setup
        title_height = 40 if title else 0
        available_height = content_height - title_height
        
        # Font metrics
        line_height = font_size * 1.3
        chars_per_line = int(content_width / (font_size * 0.55))

        def wrap_text(text_line: str) -> list:
            """Wrap a line of text to fit page width."""
            if not text_line.strip():
                return [""]  # Empty line for paragraph break
            
            words = text_line.split()
            lines = []
            current_line = []
            current_len = 0
            
            for word in words:
                word_len = len(word)
                if current_len + word_len + (1 if current_line else 0) <= chars_per_line:
                    current_line.append(word)
                    current_len += word_len + (1 if current_line else 0)
                else:
                    if current_line:
                        lines.append(" ".join(current_line))
                    current_line = [word]
                    current_len = word_len
            
            if current_line:
                lines.append(" ".join(current_line))
            
            return lines if lines else [""]

        # Process all input lines with word wrapping
        all_lines = []
        for raw_line in text.split('\n'):
            wrapped = wrap_text(raw_line)
            all_lines.extend(wrapped)

        # Calculate lines per page
        lines_per_page = max(1, int(available_height / line_height))

        # Create pages
        total_pages = 0
        for page_start in range(0, len(all_lines), lines_per_page):
            page = doc.new_page(width=page_width, height=page_height)
            page_lines = all_lines[page_start:page_start + lines_per_page]
            
            # Add title on first page
            y_pos = margin
            if title and total_pages == 0:
                page.insert_text(
                    (margin, margin + font_size + 4),
                    title,
                    fontsize=font_size + 4,
                    fontname="helv"
                )
                y_pos = margin + title_height
            
            # Add content line by line
            for line in page_lines:
                y_pos += line_height
                if y_pos > page_height - margin:
                    break  # Don't overflow page
                    
                if line.strip():
                    page.insert_text(
                        (margin, y_pos),
                        line,
                        fontsize=font_size,
                        fontname="helv"
                    )
            
            total_pages += 1

        if title:
            doc.set_metadata({"title": title})

        doc.save(str(out))
        doc.close()

        return {
            "output_path": str(out),
            "file_size_bytes": out.stat().st_size,
            "pages": total_pages,
            "success": True
        }
    except Exception as e:
        logger.exception("[CreatePDFFromText] Failed")
        return {"error": str(e)}


def extract_pdf_images(path: str, output_dir: str) -> Dict[str, Any]:
    """
    Extract all images from a PDF file.

    Args:
        path: Path to PDF file
        output_dir: Directory to save extracted images

    Returns:
        Dictionary with extracted image paths
    """
    try:
        import pymupdf as fitz
    except ImportError:
        return {"error": "pymupdf not installed. Run: pip install pymupdf"}

    try:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            return {"error": f"File not found: {path}"}

        out_dir = Path(output_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        doc = fitz.open(str(resolved))
        saved_images = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            for img_index, img in enumerate(page.get_images(full=True)):
                xref = img[0]
                base_image = doc.extract_image(xref)
                img_bytes = base_image["image"]
                ext = base_image["ext"]
                img_name = f"page{page_num + 1}_img{img_index + 1}.{ext}"
                img_path = out_dir / img_name
                with open(img_path, "wb") as f:
                    f.write(img_bytes)
                saved_images.append(str(img_path))

        doc.close()
        return {
            "source": str(resolved),
            "output_dir": str(out_dir),
            "images_extracted": len(saved_images),
            "images": saved_images
        }
    except Exception as e:
        logger.exception("[ExtractPDFImages] Failed")
        return {"error": str(e)}


def markdown_to_pdf(markdown_path: str, output_path: str, title: str = "") -> Dict[str, Any]:
    """
    Convert a Markdown file to PDF, preserving formatting (headers, tables, lists).

    Args:
        markdown_path: Path to the Markdown file
        output_path: Output PDF file path
        title: Optional document title (if not specified, uses first H1 from markdown)

    Returns:
        Dictionary with result including detected elements
    """
    try:
        import pymupdf as fitz
    except ImportError:
        return {"error": "pymupdf not installed. Run: pip install pymupdf"}

    try:
        md_file = Path(markdown_path).expanduser().resolve()
        if not md_file.exists():
            return {"error": f"Markdown file not found: {markdown_path}"}

        # Read markdown content
        with open(md_file, 'r', encoding='utf-8') as f:
            md_content = f.read()

        doc = fitz.open()
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)

        # Page setup (A4)
        margin = 50
        page_width = 595
        page_height = 842
        content_width = page_width - 2 * margin
        content_height = page_height - 2 * margin

        # Font settings
        base_font_size = 11
        header_sizes = {1: 20, 2: 16, 3: 14, 4: 12, 5: 11, 6: 10}
        line_height_base = base_font_size * 1.3

        # Track detected elements
        elements_found = {
            "headers": [],
            "tables": 0,
            "lists": 0,
            "code_blocks": 0
        }

        # Parse markdown lines
        lines = md_content.split('\n')
        parsed_elements = []
        in_code_block = False
        in_table = False
        table_rows = []

        for line in lines:
            stripped = line.strip()

            # Code blocks
            if stripped.startswith('```'):
                in_code_block = not in_code_block
                if not in_code_block:
                    elements_found["code_blocks"] += 1
                continue

            if in_code_block:
                parsed_elements.append(("code", stripped))
                continue

            # Tables
            if '|' in stripped and not in_table:
                in_table = True
                table_rows = []
            if in_table:
                if '|' in stripped:
                    cells = [cell.strip() for cell in stripped.split('|') if cell.strip()]
                    if cells and not all(c.replace('-', '').replace(':', '') == '' for c in cells):
                        table_rows.append(cells)
                else:
                    if table_rows:
                        parsed_elements.append(("table", table_rows))
                        elements_found["tables"] += 1
                    in_table = False
                    table_rows = []
                continue
            elif table_rows:
                parsed_elements.append(("table", table_rows))
                elements_found["tables"] += 1
                table_rows = []

            # Headers
            if stripped.startswith('#'):
                level = 0
                for char in stripped:
                    if char == '#':
                        level += 1
                    else:
                        break
                if level <= 6 and level < len(stripped) and stripped[level] == ' ':
                    header_text = stripped[level:].strip()
                    parsed_elements.append(("header", level, header_text))
                    elements_found["headers"].append((level, header_text))
                    if not title and level == 1:
                        title = header_text
                    continue

            # Horizontal rule
            if stripped == '---' or stripped == '***' or stripped == '___':
                parsed_elements.append(("hr",))
                continue

            # Lists
            if stripped.startswith('- ') or stripped.startswith('* ') or stripped.startswith('+ '):
                parsed_elements.append(("list_item", 0, stripped[2:]))
                elements_found["lists"] += 1
                continue
            if re.match(r'^\d+\.\s', stripped):
                parsed_elements.append(("list_item", 0, re.sub(r'^\d+\.\s', '', stripped)))
                elements_found["lists"] += 1
                continue

            # Normal paragraph
            if stripped:
                parsed_elements.append(("paragraph", stripped))

        # Create PDF pages
        page = doc.new_page(width=page_width, height=page_height)
        y_pos = margin

        def new_page_if_needed(required_height):
            nonlocal page, y_pos
            if y_pos + required_height > page_height - margin:
                page = doc.new_page(width=page_width, height=page_height)
                y_pos = margin
                return True
            return False

        # Add title if specified
        if title:
            new_page_if_needed(40)
            page.insert_text(
                (margin, margin + 20),
                title,
                fontsize=22,
                fontname="helv"
            )
            y_pos = margin + 45

        # Render elements
        for element in parsed_elements:
            elem_type = element[0]

            if elem_type == "header":
                _, level, text = element
                font_size = header_sizes.get(level, base_font_size)
                new_page_if_needed(font_size * 1.5)
                page.insert_text(
                    (margin, y_pos + font_size),
                    text,
                    fontsize=font_size,
                    fontname="helv"
                )
                y_pos += font_size * 1.8

            elif elem_type == "paragraph":
                _, text = element
                wrapped_lines = wrap_text_to_width(text, content_width, base_font_size)
                for wrapped_line in wrapped_lines:
                    new_page_if_needed(line_height_base)
                    page.insert_text(
                        (margin, y_pos + base_font_size),
                        wrapped_line,
                        fontsize=base_font_size,
                        fontname="helv"
                    )
                    y_pos += line_height_base

            elif elem_type == "list_item":
                _, _, text = element
                wrapped_lines = wrap_text_to_width("• " + text, content_width - 15, base_font_size)
                for i, wrapped_line in enumerate(wrapped_lines):
                    new_page_if_needed(line_height_base)
                    indent = 15 if i > 0 else 0
                    page.insert_text(
                        (margin + indent, y_pos + base_font_size),
                        wrapped_line,
                        fontsize=base_font_size,
                        fontname="helv"
                    )
                    y_pos += line_height_base

            elif elem_type == "table":
                _, rows = element
                if rows:
                    # Calculate column widths
                    num_cols = max(len(row) for row in rows)
                    col_width = content_width / num_cols
                    cell_padding = 5

                    # Draw table
                    for row in rows:
                        row_height = base_font_size * 1.8
                        new_page_if_needed(row_height)

                        # Draw cell borders and text
                        for col_idx, cell in enumerate(row[:num_cols]):
                            x = margin + col_idx * col_width
                            cell_rect = fitz.Rect(x, y_pos, x + col_width, y_pos + row_height)
                            # Border
                            page.draw_rect(cell_rect, width=0.5)
                            # Text
                            page.insert_textbox(
                                fitz.Rect(x + cell_padding, y_pos + 2, x + col_width - cell_padding, y_pos + row_height - 2),
                                str(cell)[:50],  # Limit cell text
                                fontsize=base_font_size - 1,
                                fontname="helv",
                                align=0
                            )
                        y_pos += row_height
                    y_pos += 10  # Space after table

            elif elem_type == "code":
                _, text = element
                new_page_if_needed(base_font_size * 1.2)
                page.insert_text(
                    (margin + 10, y_pos + base_font_size),
                    text[:100],  # Limit code line length
                    fontsize=base_font_size - 1,
                    fontname="courier",
                    color=(0.3, 0.3, 0.3)
                )
                y_pos += base_font_size * 1.2

            elif elem_type == "hr":
                new_page_if_needed(15)
                page.draw_line(
                    fitz.Point(margin, y_pos + 7),
                    fitz.Point(page_width - margin, y_pos + 7),
                    width=1
                )
                y_pos += 15

        if title:
            doc.set_metadata({"title": title})

        doc.save(str(out))
        page_count = len(doc)
        doc.close()

        return {
            "output_path": str(out),
            "file_size_bytes": out.stat().st_size,
            "pages": page_count,
            "title": title,
            "elements_found": elements_found,
            "success": True
        }

    except Exception as e:
        logger.exception("[MarkdownToPDF] Failed")
        return {"error": str(e)}


def wrap_text_to_width(text: str, max_width: float, font_size: float) -> List[str]:
    """Wrap text to fit within specified width."""
    chars_per_line = int(max_width / (font_size * 0.55))
    words = text.split()
    lines = []
    current_line = []
    current_len = 0

    for word in words:
        word_len = len(word)
        if current_len + word_len + (1 if current_line else 0) <= chars_per_line:
            current_line.append(word)
            current_len += word_len + (1 if current_line else 0)
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]
            current_len = word_len

    if current_line:
        lines.append(" ".join(current_line))

    return lines if lines else [""]


# Tool Definitions
READ_PDF_TOOL = Tool(
    name="read_pdf",
    description=(
        "Extract text content from a PDF file. Returns text per page. "
        "Optionally specify page numbers (1-indexed). Truncates at max_chars."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to PDF file"},
            "pages": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Page numbers to extract (1-indexed). Default: all pages."
            },
            "max_chars": {"type": "integer", "description": "Max characters to return (default: 50000)"}
        },
        "required": ["path"]
    },
    func=read_pdf,
    timeout_seconds=60,
    max_output_chars=60000,
    is_readonly=True,
)

GET_PDF_METADATA_TOOL = Tool(
    name="get_pdf_metadata",
    description="Extract metadata from a PDF: title, author, creation date, page count, file size.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to PDF file"}
        },
        "required": ["path"]
    },
    func=get_pdf_metadata,
    timeout_seconds=15,
    max_output_chars=5000,
    is_readonly=True,
)

MERGE_PDFS_TOOL = Tool(
    name="merge_pdfs",
    description="Merge multiple PDF files into a single PDF in the specified order.",
    parameters={
        "type": "object",
        "properties": {
            "input_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of input PDF paths (merged in order)"
            },
            "output_path": {"type": "string", "description": "Output PDF file path"}
        },
        "required": ["input_paths", "output_path"]
    },
    func=merge_pdfs,
    timeout_seconds=120,
    max_output_chars=5000,
)

SPLIT_PDF_TOOL = Tool(
    name="split_pdf",
    description="Split a PDF into multiple files. Control pages_per_file (default: 1 page each).",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Source PDF file path"},
            "output_dir": {"type": "string", "description": "Directory to save split files"},
            "pages_per_file": {"type": "integer", "description": "Pages per output file (default: 1)"}
        },
        "required": ["path", "output_dir"]
    },
    func=split_pdf,
    timeout_seconds=120,
    max_output_chars=5000,
)

CREATE_PDF_FROM_TEXT_TOOL = Tool(
    name="create_pdf_from_text",
    description="Create a PDF document from plain text content. Optionally set title and font size.",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text content for the PDF"},
            "output_path": {"type": "string", "description": "Output PDF file path"},
            "title": {"type": "string", "description": "Optional document title"},
            "font_size": {"type": "integer", "description": "Font size (default: 11)"}
        },
        "required": ["text", "output_path"]
    },
    func=create_pdf_from_text,
    timeout_seconds=60,
    max_output_chars=5000,
)

EXTRACT_PDF_IMAGES_TOOL = Tool(
    name="extract_pdf_images",
    description="Extract all images embedded in a PDF file to a directory.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to PDF file"},
            "output_dir": {"type": "string", "description": "Directory to save extracted images"}
        },
        "required": ["path", "output_dir"]
    },
    func=extract_pdf_images,
    timeout_seconds=120,
    max_output_chars=10000,
)

MARKDOWN_TO_PDF_TOOL = Tool(
    name="markdown_to_pdf",
    description="Convert a Markdown file to PDF, preserving formatting including headers, tables, lists, and code blocks.",
    parameters={
        "type": "object",
        "properties": {
            "markdown_path": {"type": "string", "description": "Path to the Markdown file"},
            "output_path": {"type": "string", "description": "Output PDF file path"},
            "title": {"type": "string", "description": "Optional document title (uses H1 if not specified)"}
        },
        "required": ["markdown_path", "output_path"]
    },
    func=markdown_to_pdf,
    timeout_seconds=60,
    max_output_chars=5000,
)

PDF_TOOLS = [
    READ_PDF_TOOL,
    GET_PDF_METADATA_TOOL,
    MERGE_PDFS_TOOL,
    SPLIT_PDF_TOOL,
    CREATE_PDF_FROM_TEXT_TOOL,
    EXTRACT_PDF_IMAGES_TOOL,
    MARKDOWN_TO_PDF_TOOL,
]
