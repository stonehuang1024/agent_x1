"""Session logger for LLM interaction logging and activity recording.

Replaces the legacy ``src/core/session_manager.py`` logging functionality
with a unified, transcript-aware logger.
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from src.session.models import Session
from src.session.transcript import TranscriptWriter

logger = logging.getLogger(__name__)


class SessionLogger:
    """Records LLM interactions, activity steps, and generates session summaries.

    All structured data is also forwarded to the JSONL transcript (if a
    ``TranscriptWriter`` is provided), ensuring a single source of truth.
    """

    def __init__(
        self,
        session_dir: Union[str, Path],
        session_id: str,
        transcript_writer: Optional[TranscriptWriter] = None,
        memory_data_dir: Optional[Union[str, Path]] = None,
    ) -> None:
        self._session_dir = Path(session_dir)
        self._session_id = session_id
        self._transcript_writer = transcript_writer
        self._closed = False
        self._memory_data_dir: Optional[Path] = Path(memory_data_dir) if memory_data_dir else None

        # Internal accumulators
        self._llm_calls: List[Dict[str, Any]] = []
        self._operation_steps: List[str] = []
        self._user_queries: List[str] = []
        self._start_time = time.time()
        self._start_datetime = datetime.now()

        # Open log files
        self._session_dir.mkdir(parents=True, exist_ok=True)

        self._llm_file = open(
            self._session_dir / "session_llm.md", "a", encoding="utf-8"
        )
        self._activity_file = open(
            self._session_dir / "session_activity.md", "a", encoding="utf-8"
        )

        # Write headers if files are empty
        if self._llm_file.tell() == 0:
            self._llm_file.write(f"# Session LLM Log\n\n")
            self._llm_file.write(f"**Session:** {session_id}  \n")
            self._llm_file.write(f"**Started:** {datetime.now().strftime('%Y%m%d_%H%M%S')}\n\n")
            self._llm_file.write(f"---\n\n")
            self._llm_file.flush()
        if self._activity_file.tell() == 0:
            self._activity_file.write(f"# Activity Log — Session {session_id[:8]}\n\n")
            self._activity_file.flush()

    # ------------------------------------------------------------------
    # LLM interaction logging
    # ------------------------------------------------------------------

    # Maximum characters for tool result output in session_llm.md
    TOOL_RESULT_MAX_CHARS = 2048

    def log_llm_interaction(
        self,
        iteration: int,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        response: Dict[str, Any],
        usage: Dict[str, Any],
        duration_ms: float,
        stop_reason: str,
    ) -> None:
        """Record a single LLM call in both Markdown and JSONL formats.

        The Markdown output mirrors the legacy ``session_llm.md`` format:
        full request messages JSON, tool list, response content JSON, and
        detailed usage statistics.
        """
        if self._closed:
            return

        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        total_tokens = usage.get("total_tokens", input_tokens + output_tokens)

        # Extract tool call names from response
        tool_call_names: List[str] = []
        resp_tool_calls = response.get("tool_calls") or []
        for tc in resp_tool_calls:
            func_info = tc.get("function", {})
            name = func_info.get("name", "")
            if name:
                tool_call_names.append(name)

        record = {
            "iteration": iteration,
            "messages_count": len(messages),
            "tools_count": len(tools),
            "usage": usage,
            "duration_ms": duration_ms,
            "stop_reason": stop_reason,
            "timestamp": time.time(),
            "time_str": datetime.now().strftime("%H:%M:%S"),
            "tool_call_names": tool_call_names,
            "tool_calls_count": len(tool_call_names),
        }
        self._llm_calls.append(record)

        # --- Markdown (full format aligned with legacy session_llm.md) ---
        ts = datetime.now().strftime("%H:%M:%S")
        tool_names = ", ".join(
            t.get("name", "unknown") for t in tools[:10]
        )
        if len(tools) > 10:
            tool_names += f" ... (+{len(tools) - 10} more)"

        # Build response content JSON for display
        response_json = self._build_response_json(response, usage, stop_reason)

        log_entry = (
            f"\n\n## LLM Call {iteration} [{ts}]\n\n"
            f"### Request\n\n"
            f"**Messages ({len(messages)}):**\n\n"
            f"```json\n{json.dumps(messages, indent=2, ensure_ascii=False)}\n```\n\n"
            f"**Tools ({len(tools)}):** {tool_names}\n\n"
            f"### Response\n\n"
            f"**Stop Reason:** {stop_reason}\n\n"
            f"**Usage:**\n"
            f"- Input Tokens: {input_tokens}\n"
            f"- Output Tokens: {output_tokens}\n"
            f"- Total Tokens: {total_tokens}\n"
            f"- Duration: {duration_ms:.2f}ms\n\n"
            f"**Content:**\n\n"
            f"```json\n{response_json}\n```\n\n"
            f"---\n\n"
        )
        self._llm_file.write(log_entry)
        self._llm_file.flush()

        # --- JSONL transcript ---
        if self._transcript_writer and not self._transcript_writer.closed:
            self._transcript_writer.append({
                "type": "llm_interaction",
                "iteration": iteration,
                "messages_count": len(messages),
                "tools_count": len(tools),
                "usage": usage,
                "duration_ms": duration_ms,
                "stop_reason": stop_reason,
            })

    # ------------------------------------------------------------------
    # Tool result logging
    # ------------------------------------------------------------------

    def log_tool_results(
        self,
        iteration: int,
        tool_results: List[Dict[str, Any]],
    ) -> None:
        """Record tool call results after execution.

        Each tool result dict should contain:
        - ``tool_name``: name of the tool
        - ``tool_call_id``: unique call id
        - ``arguments``: dict of arguments passed to the tool
        - ``result``: string result (will be truncated to *TOOL_RESULT_MAX_CHARS*)
        - ``duration_ms``: execution time in milliseconds
        - ``success``: bool indicating success/failure
        - ``error``: optional error message
        """
        if self._closed or not tool_results:
            return

        ts = datetime.now().strftime("%H:%M:%S")
        parts: List[str] = [
            f"\n### Tool Calls — Step {iteration} [{ts}]\n\n",
        ]

        for idx, tr in enumerate(tool_results, 1):
            tool_name = tr.get("tool_name", "unknown")
            tool_call_id = tr.get("tool_call_id", "")
            arguments = tr.get("arguments", {})
            result_text = tr.get("result", "")
            duration = tr.get("duration_ms", 0)
            success = tr.get("success", True)
            error = tr.get("error", "")

            # Format arguments
            try:
                args_str = json.dumps(arguments, indent=2, ensure_ascii=False)
            except (TypeError, ValueError):
                args_str = str(arguments)

            # Truncate result
            if len(result_text) > self.TOOL_RESULT_MAX_CHARS:
                result_text = (
                    result_text[: self.TOOL_RESULT_MAX_CHARS]
                    + f"\n... [truncated, total {len(tr.get('result', ''))} chars]"
                )

            status = "✅ Success" if success else f"❌ Error: {error}"
            header = f"**Tool {idx}: `{tool_name}`**"
            if len(tool_results) == 1:
                header = f"**Tool: `{tool_name}`**"

            parts.append(
                f"{header}  \n"
                f"ID: `{tool_call_id}`  \n"
                f"Duration: {duration:.0f}ms | Status: {status}\n\n"
                f"**Arguments:**\n\n"
                f"```json\n{args_str}\n```\n\n"
                f"**Result:**\n\n"
                f"```\n{result_text}\n```\n\n"
            )

        parts.append("---\n\n")
        self._llm_file.write("".join(parts))
        self._llm_file.flush()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_response_json(
        response: Dict[str, Any],
        usage: Dict[str, Any],
        stop_reason: str,
    ) -> str:
        """Build a JSON string for the response content block."""
        try:
            display = {
                "usage": usage,
                "stop_reason": stop_reason,
                "content": response.get("content", ""),
            }
            # Include tool_calls if present
            tool_calls = response.get("tool_calls")
            if tool_calls:
                # Convert to a more readable format
                tc_display = []
                for tc in tool_calls:
                    func_info = tc.get("function", {})
                    tc_display.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": func_info.get("name", ""),
                        "input": func_info.get("arguments", "{}"),
                    })
                display["tool_calls"] = tc_display
            return json.dumps(display, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(response)

    # ------------------------------------------------------------------
    # Activity recording
    # ------------------------------------------------------------------

    def log_user_query(self, query: str) -> None:
        """Record a user query (full prompt, not truncated)."""
        if self._closed:
            return
        self._user_queries.append(query)
        ts = datetime.now().strftime("%H:%M:%S")
        self._operation_steps.append(f"[{ts}] User query: {query[:80]}...")

    def record_activity(self, step: str) -> None:
        """Record an activity step."""
        if self._closed:
            return

        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {step}\n"
        self._operation_steps.append(f"[{ts}] {step}")
        self._activity_file.write(line)
        self._activity_file.flush()

    # ------------------------------------------------------------------
    # Summary generation
    # ------------------------------------------------------------------

    def generate_summary(self, session: Session) -> str:
        """Generate a Markdown summary and persist it.

        The output format matches the legacy ``history_session.md`` layout:
        Session header, Period, Duration, Summary, User Prompt, Operation
        Steps, LLM Statistics, and LLM Call Details table (with
        ``tool_call_name_list`` column).
        """
        end_time = datetime.now()
        total_duration = time.time() - self._start_time
        duration_minutes = total_duration / 60.0
        total_input = sum(c.get("usage", {}).get("input_tokens", 0) for c in self._llm_calls)
        total_output = sum(c.get("usage", {}).get("output_tokens", 0) for c in self._llm_calls)
        total_tokens = total_input + total_output

        start_str = self._start_datetime.strftime("%Y-%m-%d %H:%M:%S")
        end_str = end_time.strftime("%Y-%m-%d %H:%M:%S")

        # Determine summary text
        status = session.status.value if session.status else "unknown"
        summary_text = self._derive_summary_text(status)

        # --- Build full user prompt section ---
        user_prompt_section = ""
        if self._user_queries:
            user_prompt_section = "### User Prompt\n\n"
            for idx, q in enumerate(self._user_queries, 1):
                if len(self._user_queries) > 1:
                    user_prompt_section += f"**Query {idx}:**\n\n"
                user_prompt_section += f"{q}\n\n"

        # --- Build operation steps ---
        steps_text = ""
        display_steps = self._operation_steps[:20]
        for i, step in enumerate(display_steps, 1):
            steps_text += f"{i}. {step}\n"
        if len(self._operation_steps) > 20:
            steps_text += f"... ({len(self._operation_steps) - 20} more steps)\n"

        # --- Build LLM Call Details table ---
        calls_table = self._build_llm_calls_table()

        # --- Compose the full summary (matching legacy format) ---
        summary = (
            f"## Session: {self._session_id}\n\n"
            f"**Period:** {start_str} \u2192 {end_str}  \n"
            f"**Duration:** {duration_minutes:.2f} minutes  \n"
            f"**Summary:** {summary_text}\n\n"
            f"{user_prompt_section}"
            f"### Operation Steps\n\n"
            f"{steps_text}\n"
            f"### LLM Statistics\n\n"
            f"- **Total LLM Calls:** {len(self._llm_calls)}\n"
            f"- **Total Input Tokens:** {total_input}\n"
            f"- **Total Output Tokens:** {total_output}\n"
            f"- **Total Tokens:** {total_tokens}\n\n"
            f"### LLM Call Details\n\n"
            f"{calls_table}\n"
            f"---\n\n"
        )

        # Write to session directory
        summary_path = self._session_dir / "session_summary.md"
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write(summary)

        # Append summary to session_llm.md
        if not self._closed:
            self._llm_file.write(f"\n\n## Session Summary\n\n")
            self._llm_file.write(f"**Status:** {status}\n")
            self._llm_file.write(f"**Total LLM Calls:** {len(self._llm_calls)}\n")
            self._llm_file.write(f"**Total Tokens:** {total_tokens}\n")
            self._llm_file.write(f"**Duration:** {duration_minutes:.2f} minutes\n")
            self._llm_file.flush()

        # Append to global history
        try:
            if self._memory_data_dir:
                history_dir = self._memory_data_dir
            else:
                history_dir = self._session_dir.parent.parent / "memory_data"
            history_dir.mkdir(parents=True, exist_ok=True)
            history_path = history_dir / "history_session.md"
            with open(history_path, "a", encoding="utf-8") as f:
                f.write(f"\n---\n\n")
                f.write(summary)
        except OSError as e:
            logger.warning("Failed to append to global history: %s", e)

        return summary

    def _derive_summary_text(self, status: str) -> str:
        """Derive a human-readable summary text from session status."""
        mapping = {
            "completed": "User exited normally",
            "paused": "Session paused",
            "failed": "Session failed",
            "created": "Session created but not started",
            "active": "Session still active",
        }
        return mapping.get(status, f"Session ended ({status})")

    def _build_llm_calls_table(self) -> str:
        """Build the LLM Call Details Markdown table with tool_call_name_list."""
        header = (
            "| Iteration | Time | Input | Output | Total | Duration "
            "| Stop Reason | Tools | Tool Names |\n"
            "|-----------|------|-------|--------|-------|----------"
            "|-------------|-------|------------|\n"
        )
        rows = ""
        for call in self._llm_calls:
            usage = call.get("usage", {})
            input_t = usage.get("input_tokens", 0)
            output_t = usage.get("output_tokens", 0)
            total_t = usage.get("total_tokens", input_t + output_t)
            duration = call.get("duration_ms", 0)
            stop = call.get("stop_reason", "unknown")
            time_str = call.get("time_str", "")
            tc_count = call.get("tool_calls_count", 0)
            tc_names = call.get("tool_call_names", [])
            names_str = ", ".join(tc_names) if tc_names else ""
            rows += (
                f"| {call.get('iteration', 0)} | {time_str} | {input_t} "
                f"| {output_t} | {total_t} | {duration:.0f}ms "
                f"| {stop} | {tc_count} | {names_str} |\n"
            )
        return header + rows

    # ------------------------------------------------------------------
    # Resource management
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close all file handles."""
        if self._closed:
            return
        self._closed = True
        try:
            self._llm_file.close()
        except Exception:
            pass
        try:
            self._activity_file.close()
        except Exception:
            pass

    @property
    def closed(self) -> bool:
        return self._closed

    def __enter__(self) -> "SessionLogger":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
