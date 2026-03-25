"""
Bash Tools Module - Shell command execution.

Provides tools for:
- Running shell commands
- Running Python scripts
- Running bash scripts
- Getting environment info
"""

import os
import subprocess
import logging
import sys
from typing import Dict, Any, Optional, List

from ..core.tool import Tool

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30
_BLOCKED_PATTERNS = [
    "rm -rf /",
    "rm -rf ~",
    "mkfs",
    "dd if=/dev/zero",
    ":(){:|:&};:",
    "fork bomb",
]


def _check_command_safety(command: str) -> Optional[str]:
    """Return a block reason if the command matches a dangerous pattern."""
    cmd_lower = command.lower().strip()
    for pattern in _BLOCKED_PATTERNS:
        if pattern in cmd_lower:
            return f"Command blocked for safety: matches pattern '{pattern}'"
    return None


def run_command(
    command: str,
    working_dir: Optional[str] = None,
    timeout: int = _DEFAULT_TIMEOUT,
    env_vars: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Execute a shell command and return stdout/stderr.

    Args:
        command: Shell command to execute
        working_dir: Working directory (default: current directory)
        timeout: Timeout in seconds (default: 30)
        env_vars: Additional environment variables to set

    Returns:
        Dictionary with stdout, stderr, return_code
    """
    block_reason = _check_command_safety(command)
    if block_reason:
        return {"error": block_reason, "command": command}

    cwd = working_dir or os.getcwd()
    env = os.environ.copy()
    if env_vars:
        env.update(env_vars)

    logger.info(f"[RunCommand] Executing: {command[:200]}")

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
            env=env
        )
        return {
            "command": command,
            "return_code": result.returncode,
            "stdout": result.stdout[:10000],
            "stderr": result.stderr[:5000],
            "success": result.returncode == 0,
            "working_dir": cwd
        }
    except subprocess.TimeoutExpired:
        return {
            "error": f"Command timed out after {timeout} seconds",
            "command": command,
            "return_code": -1,
            "success": False
        }
    except Exception as e:
        logger.exception("[RunCommand] Failed")
        return {"error": str(e), "command": command, "success": False}


def run_python_script(
    script_path: str,
    args: Optional[List[str]] = None,
    working_dir: Optional[str] = None,
    timeout: int = 60
) -> Dict[str, Any]:
    """
    Execute a Python script file.

    Args:
        script_path: Path to the Python (.py) script
        args: Command-line arguments to pass
        working_dir: Working directory
        timeout: Timeout in seconds (default: 60)

    Returns:
        Dictionary with stdout, stderr, return_code
    """
    from pathlib import Path
    resolved = Path(script_path).expanduser().resolve()
    if not resolved.exists():
        return {"error": f"Script not found: {script_path}"}
    if not resolved.suffix == ".py":
        return {"error": f"Not a Python file: {script_path}"}

    cmd_parts = [sys.executable, str(resolved)]
    if args:
        cmd_parts.extend(args)

    cwd = working_dir or str(resolved.parent)
    logger.info(f"[RunPython] Executing: {' '.join(cmd_parts)}")

    try:
        result = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout
        )
        return {
            "script": str(resolved),
            "args": args or [],
            "return_code": result.returncode,
            "stdout": result.stdout[:10000],
            "stderr": result.stderr[:5000],
            "success": result.returncode == 0
        }
    except subprocess.TimeoutExpired:
        return {
            "error": f"Script timed out after {timeout} seconds",
            "script": script_path,
            "success": False
        }
    except Exception as e:
        logger.exception("[RunPython] Failed")
        return {"error": str(e), "script": script_path, "success": False}


def run_bash_script(
    script_content: str,
    working_dir: Optional[str] = None,
    timeout: int = 60
) -> Dict[str, Any]:
    """
    Execute inline bash script content.

    Args:
        script_content: Bash script content as string
        working_dir: Working directory
        timeout: Timeout in seconds (default: 60)

    Returns:
        Dictionary with stdout, stderr, return_code
    """
    import tempfile
    from pathlib import Path

    block_reason = _check_command_safety(script_content)
    if block_reason:
        return {"error": block_reason}

    cwd = working_dir or os.getcwd()
    logger.info(f"[RunBash] Executing script ({len(script_content)} chars)")

    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", delete=False, prefix="agent_x1_"
        ) as tmp:
            tmp.write(script_content)
            tmp_path = tmp.name

        os.chmod(tmp_path, 0o700)

        result = subprocess.run(
            ["bash", tmp_path],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout
        )
        return {
            "return_code": result.returncode,
            "stdout": result.stdout[:10000],
            "stderr": result.stderr[:5000],
            "success": result.returncode == 0
        }
    except subprocess.TimeoutExpired:
        return {
            "error": f"Script timed out after {timeout} seconds",
            "success": False
        }
    except Exception as e:
        logger.exception("[RunBash] Failed")
        return {"error": str(e), "success": False}
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def get_system_info() -> Dict[str, Any]:
    """
    Get system environment information.

    Returns:
        Dictionary with OS, Python version, CPU, memory, disk info
    """
    import platform
    try:
        import psutil
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        cpu_count = psutil.cpu_count()
        cpu_percent = psutil.cpu_percent(interval=0.5)
        mem_info = {
            "total_gb": round(mem.total / 1e9, 2),
            "available_gb": round(mem.available / 1e9, 2),
            "used_percent": mem.percent
        }
        disk_info = {
            "total_gb": round(disk.total / 1e9, 2),
            "free_gb": round(disk.free / 1e9, 2),
            "used_percent": disk.percent
        }
    except ImportError:
        cpu_count = os.cpu_count()
        cpu_percent = None
        mem_info = {"note": "Install psutil for memory info"}
        disk_info = {"note": "Install psutil for disk info"}

    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "machine": platform.machine(),
        "python_version": sys.version,
        "cpu_count": cpu_count,
        "cpu_percent": cpu_percent,
        "memory": mem_info,
        "disk": disk_info,
        "cwd": os.getcwd(),
        "hostname": platform.node()
    }


def get_environment_variable(name: str) -> Dict[str, Any]:
    """
    Get the value of an environment variable.

    Args:
        name: Environment variable name

    Returns:
        Dictionary with variable name and value
    """
    value = os.environ.get(name)
    if value is None:
        return {"name": name, "value": None, "exists": False}
    masked = "***" if any(k in name.upper() for k in ["KEY", "SECRET", "TOKEN", "PASSWORD", "PASS"]) else value
    return {"name": name, "value": masked, "exists": True}


# Tool Definitions
RUN_COMMAND_TOOL = Tool(
    name="run_command",
    description=(
        "Execute a shell command and return stdout/stderr/return_code. "
        "Use for system commands, file operations, package checks, etc. "
        "Timeout defaults to 30 seconds. Dangerous commands like 'rm -rf /' are blocked."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "working_dir": {"type": "string", "description": "Working directory (default: cwd)"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default: 30)"},
            "env_vars": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Extra environment variables"
            }
        },
        "required": ["command"]
    },
    func=run_command
)

RUN_PYTHON_SCRIPT_TOOL = Tool(
    name="run_python_script",
    description="Execute a Python script file and return its output. Pass optional command-line args.",
    parameters={
        "type": "object",
        "properties": {
            "script_path": {"type": "string", "description": "Path to the .py script file"},
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Command-line arguments"
            },
            "working_dir": {"type": "string", "description": "Working directory"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default: 60)"}
        },
        "required": ["script_path"]
    },
    func=run_python_script
)

RUN_BASH_SCRIPT_TOOL = Tool(
    name="run_bash_script",
    description=(
        "Execute an inline bash script (multi-line commands). "
        "Useful for complex shell workflows. Returns stdout/stderr."
    ),
    parameters={
        "type": "object",
        "properties": {
            "script_content": {"type": "string", "description": "Bash script content"},
            "working_dir": {"type": "string", "description": "Working directory"},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default: 60)"}
        },
        "required": ["script_content"]
    },
    func=run_bash_script
)

GET_SYSTEM_INFO_TOOL = Tool(
    name="get_system_info",
    description="Get system information: OS, Python version, CPU count, memory, disk usage.",
    parameters={
        "type": "object",
        "properties": {},
        "required": []
    },
    func=get_system_info
)

GET_ENV_VAR_TOOL = Tool(
    name="get_environment_variable",
    description="Get the value of an environment variable. Sensitive values (keys, secrets) are masked.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Environment variable name"}
        },
        "required": ["name"]
    },
    func=get_environment_variable
)

BASH_TOOLS = [
    RUN_COMMAND_TOOL,
    RUN_PYTHON_SCRIPT_TOOL,
    RUN_BASH_SCRIPT_TOOL,
    GET_SYSTEM_INFO_TOOL,
    GET_ENV_VAR_TOOL,
]
