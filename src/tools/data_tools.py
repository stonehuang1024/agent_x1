"""
Data Tools Module - Data file processing and analysis.

Provides tools for:
- Reading CSV, JSON, Excel files
- Basic statistical analysis
- Data filtering and transformation
- Saving processed data
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, List, Optional, Union

from ..core.tool import Tool

logger = logging.getLogger(__name__)


def read_csv(
    path: str,
    delimiter: str = ",",
    max_rows: int = 100,
    encoding: str = "utf-8"
) -> Dict[str, Any]:
    """
    Read a CSV file and return its contents as structured data.

    Args:
        path: Path to CSV file
        delimiter: Field delimiter (default: ',')
        max_rows: Maximum rows to return (default: 100)
        encoding: File encoding (default: utf-8)

    Returns:
        Dictionary with columns, rows, and summary stats
    """
    try:
        import pandas as pd
    except ImportError:
        return {"error": "pandas not installed. Run: pip install pandas"}

    try:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            return {"error": f"File not found: {path}"}

        df = pd.read_csv(str(resolved), delimiter=delimiter, encoding=encoding, nrows=max_rows)
        total_rows = sum(1 for _ in open(str(resolved), encoding=encoding)) - 1

        return {
            "path": str(resolved),
            "total_rows": total_rows,
            "displayed_rows": len(df),
            "columns": list(df.columns),
            "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
            "data": df.to_dict(orient="records"),
            "truncated": total_rows > max_rows
        }
    except Exception as e:
        logger.exception("[ReadCSV] Failed")
        return {"error": str(e), "path": path}


def read_json_file(path: str, max_chars: int = 50000) -> Dict[str, Any]:
    """
    Read and parse a JSON file.

    Args:
        path: Path to JSON file
        max_chars: Max characters to return (default: 50000)

    Returns:
        Dictionary with parsed JSON data
    """
    try:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            return {"error": f"File not found: {path}"}

        with open(resolved, "r", encoding="utf-8") as f:
            raw = f.read(max_chars)

        data = json.loads(raw)
        size = resolved.stat().st_size

        summary: Dict[str, Any] = {
            "path": str(resolved),
            "file_size_bytes": size,
            "truncated": size > max_chars,
        }

        if isinstance(data, list):
            summary["type"] = "array"
            summary["length"] = len(data)
            summary["data"] = data
        elif isinstance(data, dict):
            summary["type"] = "object"
            summary["keys"] = list(data.keys())
            summary["data"] = data
        else:
            summary["type"] = type(data).__name__
            summary["data"] = data

        return summary
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse error: {e}", "path": path}
    except Exception as e:
        logger.exception("[ReadJSON] Failed")
        return {"error": str(e), "path": path}


def read_excel(
    path: str,
    sheet_name: Optional[Union[str, int]] = 0,
    max_rows: int = 100
) -> Dict[str, Any]:
    """
    Read an Excel (.xlsx/.xls) file.

    Args:
        path: Path to Excel file
        sheet_name: Sheet name or index (default: 0 = first sheet)
        max_rows: Maximum rows to return per sheet (default: 100)

    Returns:
        Dictionary with sheet data and metadata
    """
    try:
        import pandas as pd
        import openpyxl
    except ImportError:
        return {"error": "pandas and openpyxl not installed. Run: pip install pandas openpyxl"}

    try:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            return {"error": f"File not found: {path}"}

        xl = pd.ExcelFile(str(resolved))
        sheet_names = xl.sheet_names

        target = sheet_name if sheet_name is not None else 0
        df = xl.parse(target, nrows=max_rows)

        return {
            "path": str(resolved),
            "all_sheets": sheet_names,
            "active_sheet": str(target),
            "columns": list(df.columns),
            "total_displayed_rows": len(df),
            "dtypes": {col: str(dtype) for col, dtype in df.dtypes.items()},
            "data": df.to_dict(orient="records")
        }
    except Exception as e:
        logger.exception("[ReadExcel] Failed")
        return {"error": str(e), "path": path}


def analyze_dataframe(path: str, file_type: str = "csv", delimiter: str = ",") -> Dict[str, Any]:
    """
    Perform statistical analysis on a tabular data file (CSV or Excel).

    Returns summary statistics (count, mean, std, min, max, percentiles) for numeric columns,
    and value counts for categorical columns.

    Args:
        path: Path to data file
        file_type: 'csv' or 'excel' (default: csv)
        delimiter: CSV delimiter (default: ',')

    Returns:
        Dictionary with statistical summary
    """
    try:
        import pandas as pd
    except ImportError:
        return {"error": "pandas not installed. Run: pip install pandas"}

    try:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            return {"error": f"File not found: {path}"}

        if file_type.lower() == "excel":
            df = pd.read_excel(str(resolved))
        else:
            df = pd.read_csv(str(resolved), delimiter=delimiter)

        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        categorical_cols = df.select_dtypes(exclude="number").columns.tolist()

        stats: Dict[str, Any] = {}
        for col in numeric_cols:
            s = df[col].describe()
            stats[col] = {
                "type": "numeric",
                "count": int(s["count"]),
                "mean": round(float(s["mean"]), 4),
                "std": round(float(s["std"]), 4),
                "min": round(float(s["min"]), 4),
                "25%": round(float(s["25%"]), 4),
                "50%": round(float(s["50%"]), 4),
                "75%": round(float(s["75%"]), 4),
                "max": round(float(s["max"]), 4),
                "null_count": int(df[col].isna().sum())
            }

        for col in categorical_cols[:10]:
            vc = df[col].value_counts().head(10)
            stats[col] = {
                "type": "categorical",
                "unique_count": int(df[col].nunique()),
                "null_count": int(df[col].isna().sum()),
                "top_values": {str(k): int(v) for k, v in vc.items()}
            }

        return {
            "path": str(resolved),
            "total_rows": len(df),
            "total_columns": len(df.columns),
            "columns": list(df.columns),
            "null_counts": {col: int(df[col].isna().sum()) for col in df.columns},
            "statistics": stats
        }
    except Exception as e:
        logger.exception("[AnalyzeDataframe] Failed")
        return {"error": str(e), "path": path}


def filter_csv(
    path: str,
    filters: List[Dict[str, Any]],
    output_path: Optional[str] = None,
    max_rows: int = 1000
) -> Dict[str, Any]:
    """
    Filter rows in a CSV file based on column conditions.

    Each filter dict:
      - column (str): Column name
      - operator (str): '==', '!=', '>', '<', '>=', '<=', 'contains', 'startswith'
      - value: Value to compare against

    Args:
        path: Path to CSV file
        filters: List of filter conditions (ANDed together)
        output_path: Optional path to save filtered results
        max_rows: Max rows to return (default: 1000)

    Returns:
        Dictionary with filtered data
    """
    try:
        import pandas as pd
    except ImportError:
        return {"error": "pandas not installed"}

    try:
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            return {"error": f"File not found: {path}"}

        df = pd.read_csv(str(resolved))
        original_count = len(df)

        for f in filters:
            col = f.get("column")
            op = f.get("operator", "==")
            val = f.get("value")

            if col not in df.columns:
                return {"error": f"Column not found: {col}"}

            if op == "==":
                df = df[df[col] == val]
            elif op == "!=":
                df = df[df[col] != val]
            elif op == ">":
                df = df[df[col] > val]
            elif op == "<":
                df = df[df[col] < val]
            elif op == ">=":
                df = df[df[col] >= val]
            elif op == "<=":
                df = df[df[col] <= val]
            elif op == "contains":
                df = df[df[col].astype(str).str.contains(str(val), case=False, na=False)]
            elif op == "startswith":
                df = df[df[col].astype(str).str.startswith(str(val), na=False)]
            else:
                return {"error": f"Unknown operator: {op}"}

        result: Dict[str, Any] = {
            "path": str(resolved),
            "original_rows": original_count,
            "filtered_rows": len(df),
            "filters_applied": filters,
            "data": df.head(max_rows).to_dict(orient="records")
        }

        if output_path:
            out = Path(output_path).expanduser().resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(str(out), index=False)
            result["saved_to"] = str(out)

        return result
    except Exception as e:
        logger.exception("[FilterCSV] Failed")
        return {"error": str(e)}


def save_as_csv(data: List[Dict[str, Any]], output_path: str) -> Dict[str, Any]:
    """
    Save a list of dictionaries as a CSV file.

    Args:
        data: List of row dicts (all dicts should share same keys)
        output_path: Output CSV file path

    Returns:
        Dictionary with result
    """
    try:
        import pandas as pd
    except ImportError:
        return {"error": "pandas not installed"}

    try:
        if not data:
            return {"error": "No data provided"}

        df = pd.DataFrame(data)
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(str(out), index=False)

        return {
            "output_path": str(out),
            "rows": len(df),
            "columns": list(df.columns),
            "file_size_bytes": out.stat().st_size,
            "success": True
        }
    except Exception as e:
        logger.exception("[SaveAsCSV] Failed")
        return {"error": str(e)}


def convert_data_format(
    input_path: str,
    output_path: str,
    input_format: str = "csv",
    output_format: str = "json"
) -> Dict[str, Any]:
    """
    Convert a data file between formats (csv, json, excel).

    Args:
        input_path: Source file path
        output_path: Destination file path
        input_format: Source format: 'csv', 'json', 'excel' (default: csv)
        output_format: Target format: 'csv', 'json', 'excel' (default: json)

    Returns:
        Dictionary with result
    """
    try:
        import pandas as pd
    except ImportError:
        return {"error": "pandas not installed"}

    try:
        resolved_in = Path(input_path).expanduser().resolve()
        if not resolved_in.exists():
            return {"error": f"Input file not found: {input_path}"}

        if input_format == "csv":
            df = pd.read_csv(str(resolved_in))
        elif input_format == "json":
            df = pd.read_json(str(resolved_in))
        elif input_format == "excel":
            df = pd.read_excel(str(resolved_in))
        else:
            return {"error": f"Unsupported input format: {input_format}"}

        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)

        if output_format == "csv":
            df.to_csv(str(out), index=False)
        elif output_format == "json":
            df.to_json(str(out), orient="records", indent=2)
        elif output_format == "excel":
            df.to_excel(str(out), index=False)
        else:
            return {"error": f"Unsupported output format: {output_format}"}

        return {
            "input_path": str(resolved_in),
            "output_path": str(out),
            "input_format": input_format,
            "output_format": output_format,
            "rows": len(df),
            "columns": len(df.columns),
            "file_size_bytes": out.stat().st_size,
            "success": True
        }
    except Exception as e:
        logger.exception("[ConvertDataFormat] Failed")
        return {"error": str(e)}


# Tool Definitions
READ_CSV_TOOL = Tool(
    name="read_csv",
    description=(
        "Read a CSV file and return columns, data rows, and dtype information. "
        "Controls max_rows to avoid large payloads."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to CSV file"},
            "delimiter": {"type": "string", "description": "Field delimiter (default: ',')"},
            "max_rows": {"type": "integer", "description": "Max rows to return (default: 100)"},
            "encoding": {"type": "string", "description": "File encoding (default: utf-8)"}
        },
        "required": ["path"]
    },
    func=read_csv
)

READ_JSON_FILE_TOOL = Tool(
    name="read_json_file",
    description="Read and parse a JSON file. Returns type (array/object), keys, and data.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to JSON file"},
            "max_chars": {"type": "integer", "description": "Max characters to parse (default: 50000)"}
        },
        "required": ["path"]
    },
    func=read_json_file
)

READ_EXCEL_TOOL = Tool(
    name="read_excel",
    description=(
        "Read an Excel (.xlsx/.xls) file. Lists all sheet names and reads a specified sheet. "
        "Requires openpyxl."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to Excel file"},
            "sheet_name": {"description": "Sheet name or 0-based index (default: 0)"},
            "max_rows": {"type": "integer", "description": "Max rows to return (default: 100)"}
        },
        "required": ["path"]
    },
    func=read_excel
)

ANALYZE_DATAFRAME_TOOL = Tool(
    name="analyze_dataframe",
    description=(
        "Perform statistical analysis on a CSV or Excel file. "
        "Returns count, mean, std, min, max, percentiles for numeric columns "
        "and top value counts for categorical columns."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to data file"},
            "file_type": {"type": "string", "enum": ["csv", "excel"], "description": "File type (default: csv)"},
            "delimiter": {"type": "string", "description": "CSV delimiter (default: ',')"}
        },
        "required": ["path"]
    },
    func=analyze_dataframe
)

FILTER_CSV_TOOL = Tool(
    name="filter_csv",
    description=(
        "Filter rows in a CSV file using column conditions. "
        "Operators: ==, !=, >, <, >=, <=, contains, startswith. "
        "All filters are ANDed. Optionally save result to output_path."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to CSV file"},
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "column": {"type": "string"},
                        "operator": {"type": "string"},
                        "value": {"description": "Filter value"}
                    },
                    "required": ["column", "operator", "value"]
                },
                "description": "List of filter conditions"
            },
            "output_path": {"type": "string", "description": "Optional output CSV path"},
            "max_rows": {"type": "integer", "description": "Max rows to return (default: 1000)"}
        },
        "required": ["path", "filters"]
    },
    func=filter_csv
)

SAVE_AS_CSV_TOOL = Tool(
    name="save_as_csv",
    description="Save a list of record dicts as a CSV file.",
    parameters={
        "type": "object",
        "properties": {
            "data": {
                "type": "array",
                "items": {"type": "object"},
                "description": "List of row dictionaries"
            },
            "output_path": {"type": "string", "description": "Output CSV file path"}
        },
        "required": ["data", "output_path"]
    },
    func=save_as_csv
)

CONVERT_DATA_FORMAT_TOOL = Tool(
    name="convert_data_format",
    description=(
        "Convert a data file between formats: csv, json, excel. "
        "E.g. CSV → JSON, Excel → CSV, JSON → Excel."
    ),
    parameters={
        "type": "object",
        "properties": {
            "input_path": {"type": "string", "description": "Source file path"},
            "output_path": {"type": "string", "description": "Destination file path"},
            "input_format": {"type": "string", "enum": ["csv", "json", "excel"], "description": "Input format (default: csv)"},
            "output_format": {"type": "string", "enum": ["csv", "json", "excel"], "description": "Output format (default: json)"}
        },
        "required": ["input_path", "output_path"]
    },
    func=convert_data_format
)

DATA_TOOLS = [
    READ_CSV_TOOL,
    READ_JSON_FILE_TOOL,
    READ_EXCEL_TOOL,
    ANALYZE_DATAFRAME_TOOL,
    FILTER_CSV_TOOL,
    SAVE_AS_CSV_TOOL,
    CONVERT_DATA_FORMAT_TOOL,
]
