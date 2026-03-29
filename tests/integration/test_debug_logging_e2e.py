"""
End-to-end integration tests for the DEBUG logging enhancement.

Bug targets:
- Missing DEBUG log points: critical agent actions not logged
- Session ID not propagated: logs cannot be filtered by session
- Log format broken: session_id field missing or malformed
- Truncation broken: long parameters logged verbatim, bloating log files
- Sensitive data leaked: API keys logged in plaintext
- Daily rotation broken: TimedRotatingFileHandler not configured correctly
- EventBus logs missing: events emitted without DEBUG trace
- Memory logs missing: store/retrieve operations invisible
- Tool logs missing: scheduling/execution not traced
- Prompt logs missing: system prompt assembly not traced
"""

import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from src.util.logger import (
    reset_logging, setup_logging, set_session_id, clear_session_id,
    truncate_for_log, mask_sensitive,
)


@pytest.fixture(autouse=True)
def clean_logging():
    """Reset logging before and after each test."""
    reset_logging()
    yield
    reset_logging()


@pytest.fixture
def log_capture(tmp_path):
    """Set up logging to a temp directory and return a helper to read logs."""
    setup_logging(level=logging.DEBUG, log_dir=str(tmp_path))

    class LogCapture:
        def __init__(self, log_dir):
            self.log_dir = Path(log_dir)

        def flush(self):
            for h in logging.getLogger().handlers:
                h.flush()

        def read_all(self) -> str:
            self.flush()
            log_file = self.log_dir / "agent_x1.log"
            if log_file.exists():
                return log_file.read_text()
            return ""

        def lines_containing(self, substring: str) -> list:
            return [l for l in self.read_all().split("\n") if substring in l]

        def has_log(self, substring: str) -> bool:
            return len(self.lines_containing(substring)) > 0

    return LogCapture(tmp_path)


# ============================================================
# EventBus DEBUG logs
# ============================================================

class TestEventBusDebugLogs:
    """Verify EventBus emits DEBUG logs for subscribe and emit."""

    def test_subscribe_logs_event_and_handler(self, log_capture):
        """Bug: event subscription invisible, can't verify wiring."""
        from src.core.events import EventBus, AgentEvent

        bus = EventBus()
        handler = MagicMock()
        handler.__name__ = "mock_handler"
        bus.subscribe(AgentEvent.LLM_CALL_STARTED, handler)

        assert log_capture.has_log("[EventBus] Subscribe"), (
            "EventBus.subscribe did not produce a DEBUG log"
        )
        assert log_capture.has_log("LLM_CALL_STARTED"), (
            "Subscribe log missing event type name"
        )

    def test_emit_logs_event_and_subscriber_count(self, log_capture):
        """Bug: event emission invisible, can't trace event flow."""
        from src.core.events import EventBus, AgentEvent

        bus = EventBus()
        handler = MagicMock()
        bus.subscribe(AgentEvent.TOOL_CALLED, handler)
        bus.emit(AgentEvent.TOOL_CALLED, tool_name="read_file")

        assert log_capture.has_log("[EventBus] Emit"), (
            "EventBus.emit did not produce a DEBUG log"
        )
        assert log_capture.has_log("subscriber_count=1"), (
            "Emit log missing subscriber count"
        )

    def test_handler_error_logged(self, log_capture):
        """Bug: handler exception swallowed silently."""
        from src.core.events import EventBus, AgentEvent

        bus = EventBus()

        def bad_handler(payload):
            raise ValueError("handler boom")

        bus.subscribe(AgentEvent.TOOL_CALLED, bad_handler)
        bus.emit(AgentEvent.TOOL_CALLED)

        assert log_capture.has_log("[EventBus] Handler error"), (
            "EventBus handler error not logged"
        )
        assert log_capture.has_log("handler boom"), (
            "Handler error message not included in log"
        )


# ============================================================
# ToolRegistry DEBUG logs
# ============================================================

class TestToolRegistryDebugLogs:
    """Verify ToolRegistry logs registration and lookup."""

    def _make_tool(self, name="test_tool", description="A test tool"):
        tool = MagicMock()
        tool.name = name
        tool.description = description
        tool.parameters = {"properties": {"arg1": {}, "arg2": {}}}
        return tool

    def test_register_logs_tool_info(self, log_capture):
        """Bug: tool registration invisible, can't verify tool catalog."""
        from src.tools.tool_registry import CategorizedToolRegistry, TOOL_CATEGORIES

        registry = CategorizedToolRegistry()
        tool = self._make_tool()
        registry.register(tool, "utility")

        assert log_capture.has_log("[ToolRegistry] Registered"), (
            "Tool registration not logged"
        )
        assert log_capture.has_log("name=test_tool"), (
            "Registered log missing tool name"
        )

    def test_lookup_logs_found_status(self, log_capture):
        """Bug: tool lookup invisible, can't trace tool resolution."""
        from src.tools.tool_registry import CategorizedToolRegistry

        registry = CategorizedToolRegistry()
        tool = self._make_tool()
        registry.register(tool, "utility")

        registry.get("test_tool")
        registry.get("nonexistent_tool")

        found_lines = log_capture.lines_containing("[ToolRegistry] Lookup")
        assert len(found_lines) >= 2, (
            f"Expected at least 2 lookup logs, got {len(found_lines)}"
        )
        assert any("found=True" in l for l in found_lines), (
            "Lookup log missing found=True for existing tool"
        )
        assert any("found=False" in l for l in found_lines), (
            "Lookup log missing found=False for missing tool"
        )


# ============================================================
# LoopDetector DEBUG logs
# ============================================================

class TestLoopDetectorDebugLogs:
    """Verify LoopDetector logs when a loop is detected."""

    def test_loop_detected_warning(self, log_capture):
        """Bug: loop detection invisible, agent loops forever without trace."""
        from src.runtime.loop_detector import LoopDetector
        from src.runtime.models import ToolCallRecord

        detector = LoopDetector(window_size=2, threshold=0.5, max_repetitions=1)

        # Create identical tool call records
        for _ in range(6):
            records = [ToolCallRecord(tool_name="read_file", arguments={"path": "/a"})]
            detector.record(records)

        detected, _ = detector.detect()
        if detected:
            assert log_capture.has_log("[LoopDetector] Loop detected"), (
                "Loop detection not logged"
            )


# ============================================================
# PromptProvider DEBUG logs
# ============================================================

class TestPromptProviderDebugLogs:
    """Verify PromptProvider logs system prompt assembly."""

    def test_build_system_prompt_logs_sections(self, log_capture):
        """Bug: prompt assembly invisible, can't debug prompt issues."""
        from src.prompt.prompt_provider import PromptProvider, PromptContext

        provider = PromptProvider()
        context = PromptContext(mode="interactive")
        provider.build_system_prompt(context)

        assert log_capture.has_log("[PromptProvider] Building system prompt"), (
            "System prompt build not logged"
        )
        assert log_capture.has_log("sections="), (
            "Section count missing from prompt build log"
        )
        assert log_capture.has_log("[PromptProvider] Section added"), (
            "Individual section additions not logged"
        )


# ============================================================
# Memory system DEBUG logs
# ============================================================

class TestMemoryDebugLogs:
    """Verify Memory system logs store and retrieve operations."""

    def test_episodic_store_logged(self, log_capture):
        """Bug: memory storage invisible, can't trace what agent remembers."""
        from src.memory.memory_controller import MemoryController
        from src.memory.memory_store import MemoryStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_memory.db")
            store = MemoryStore(db_path=db_path)
            controller = MemoryController(store=store)
            controller.record_decision(
                session_id="test-session-123",
                decision="Use Python for this task",
                importance=0.9,
            )

            assert log_capture.has_log("[Memory] Episodic stored"), (
                "Episodic memory storage not logged"
            )
            assert log_capture.has_log("type=decision"), (
                "Episodic log missing memory type"
            )

    def test_semantic_store_logged(self, log_capture):
        """Bug: semantic memory storage invisible."""
        from src.memory.memory_controller import MemoryController
        from src.memory.memory_store import MemoryStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_memory.db")
            store = MemoryStore(db_path=db_path)
            controller = MemoryController(store=store)
            controller.store_preference(
                key="language",
                value="Python",
                confidence=0.95,
            )

            assert log_capture.has_log("[Memory] Semantic stored"), (
                "Semantic memory storage not logged"
            )
            assert log_capture.has_log("category=preference"), (
                "Semantic log missing category"
            )

    def test_memory_store_sql_logged(self, log_capture):
        """Bug: SQLite operations invisible, can't diagnose DB issues."""
        from src.memory.memory_controller import MemoryController
        from src.memory.memory_store import MemoryStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_memory.db")
            store = MemoryStore(db_path=db_path)
            controller = MemoryController(store=store)
            controller.record_action(
                session_id="test-session-123",
                action="Read file /src/main.py",
            )

            assert log_capture.has_log("[MemoryStore] SQL"), (
                "MemoryStore SQL operation not logged"
            )
            assert log_capture.has_log("operation=INSERT"), (
                "SQL log missing operation type"
            )
            assert log_capture.has_log("duration="), (
                "SQL log missing duration"
            )


# ============================================================
# Session lifecycle DEBUG logs
# ============================================================

class TestSessionIndexDebugLogs:
    """Verify SessionIndex logs index updates."""

    def test_index_update_logged(self, log_capture):
        """Bug: session index updates invisible."""
        from src.session.session_index import SessionIndex
        from src.session.models import SessionIndexEntry

        with tempfile.TemporaryDirectory() as tmpdir:
            index = SessionIndex(index_path=os.path.join(tmpdir, "index.json"))
            entry = SessionIndexEntry(
                session_id="test-session-12345678",
                name="Test Session",
                status="active",
                turn_count=5,
                updated_at=1234567890.0,
            )
            index.update(entry)

            assert log_capture.has_log("[Session] Index updated"), (
                "Session index update not logged"
            )
            assert log_capture.has_log("session_id=test-ses"), (
                "Index update log missing session_id"
            )


# ============================================================
# Transcript DEBUG logs
# ============================================================

class TestTranscriptDebugLogs:
    """Verify TranscriptWriter logs write operations."""

    def test_transcript_write_logged(self, log_capture):
        """Bug: transcript writes invisible, can't trace persistence."""
        from src.session.transcript import TranscriptWriter

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "transcript.jsonl")
            writer = TranscriptWriter(path)
            writer.append({"type": "llm_interaction", "iteration": 1})
            writer.close()

            assert log_capture.has_log("[Session] Transcript write"), (
                "Transcript write not logged"
            )
            assert log_capture.has_log("entry_type=llm_interaction"), (
                "Transcript log missing entry type"
            )


# ============================================================
# Truncation and sensitive data
# ============================================================

class TestTruncationInLogs:
    """Verify long parameters are truncated in log output."""

    def test_truncate_for_log_adds_annotation(self):
        """Bug: long text logged verbatim, log files grow unbounded."""
        long_text = "x" * 2000
        result = truncate_for_log(long_text, max_len=500)
        assert len(result) < 2000, "Text not truncated"
        assert "2000 chars total" in result, (
            "Truncated text missing total length annotation"
        )

    def test_mask_sensitive_hides_api_key(self):
        """Bug: API key logged in plaintext, security risk."""
        key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz"
        masked = mask_sensitive(key)
        assert "abcdefghijklmnopqrstuvwxyz" not in masked, (
            "API key not masked — security risk"
        )
        assert masked.startswith("sk-"), "Prefix not preserved in mask"
        assert "..." in masked, "Mask separator missing"


# ============================================================
# Session ID consistency across log chain
# ============================================================

class TestSessionIdConsistency:
    """Verify session_id is consistent across the entire log chain."""

    def test_session_id_appears_in_all_module_logs(self, log_capture):
        """Bug: session_id missing from some module logs, can't correlate."""
        set_session_id("e2e-test-session-id-12345")

        # Trigger logs from multiple modules
        from src.core.events import EventBus, AgentEvent
        bus = EventBus()
        handler = MagicMock()
        bus.subscribe(AgentEvent.TOOL_CALLED, handler)
        bus.emit(AgentEvent.TOOL_CALLED, tool_name="test")

        from src.prompt.prompt_provider import PromptProvider, PromptContext
        provider = PromptProvider()
        provider.build_system_prompt(PromptContext())

        # Check that all log lines with our modules contain the session_id
        all_lines = log_capture.read_all().split("\n")
        module_lines = [
            l for l in all_lines
            if "[EventBus]" in l or "[PromptProvider]" in l
        ]
        for line in module_lines:
            assert "[sid:e2e-test" in line, (
                f"session_id missing from log line: {line[:200]}"
            )

    def test_clear_session_id_removes_from_subsequent_logs(self, log_capture):
        """Bug: session_id leaks after clear, wrong session attributed."""
        set_session_id("session-to-clear")
        logger = logging.getLogger("test.clear_check")
        logger.info("before clear")

        clear_session_id()
        logger.info("after clear")

        lines = log_capture.lines_containing("after clear")
        assert len(lines) >= 1, "After-clear message not found"
        assert "[sid:--------]" in lines[0], (
            f"session_id not cleared: {lines[0][:200]}"
        )


# ============================================================
# Daily log file naming
# ============================================================

class TestDailyLogFileNaming:
    """Verify log files use daily naming convention."""

    def test_base_log_files_exist(self, log_capture):
        """Bug: log files not created at all."""
        logger = logging.getLogger("test.file_naming")
        logger.info("trigger file creation")
        logger.error("trigger error file creation")
        log_capture.flush()

        log_dir = log_capture.log_dir
        main_log = log_dir / "agent_x1.log"
        error_log = log_dir / "agent_x1_error.log"

        assert main_log.exists(), f"Main log file not found at {main_log}"
        assert error_log.exists(), f"Error log file not found at {error_log}"

    def test_timed_rotating_handler_configured(self, log_capture):
        """Bug: handler not TimedRotatingFileHandler, won't rotate daily."""
        from logging.handlers import TimedRotatingFileHandler

        root = logging.getLogger()
        timed_handlers = [
            h for h in root.handlers
            if isinstance(h, TimedRotatingFileHandler)
        ]
        assert len(timed_handlers) >= 2, (
            f"Expected >= 2 TimedRotatingFileHandler, found {len(timed_handlers)}"
        )
        for h in timed_handlers:
            assert h.when == "MIDNIGHT" or h.when == "midnight", (
                f"Handler rotation not set to midnight: {h.when}"
            )


# ============================================================
# Per-session log file isolation
# ============================================================

class TestPerSessionLogIsolation:
    """Verify bind_session_to_log isolates logs per session in an
    end-to-end scenario simulating the real SessionManager flow."""

    def setup_method(self):
        reset_logging()

    def test_session_manager_binds_log_on_create(self):
        """Bug: SessionManager.create_session doesn't call bind_session_to_log,
        so multiple Agent instances still share the same log file."""
        from src.session.session_manager import SessionManager
        from src.session.session_store import SessionStore
        from src.core.config import AppConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)

            config = AppConfig()
            config.paths.session_dir = os.path.join(tmpdir, "sessions")
            config.paths.data_dir = os.path.join(tmpdir, "data")
            config.paths.memory_data_dir = os.path.join(tmpdir, "memory")
            os.makedirs(config.paths.session_dir, exist_ok=True)
            os.makedirs(config.paths.data_dir, exist_ok=True)
            os.makedirs(config.paths.memory_data_dir, exist_ok=True)

            store = SessionStore(os.path.join(tmpdir, "data", "sessions.db"))
            sm = SessionManager(store=store, config=config)
            session = sm.create_session(name="test-isolation")

            prefix = session.id[:8]

            # Find the session-specific log file (has timestamp in name)
            matching_logs = sorted(Path(tmpdir).glob(f"agent_x1_*_{prefix}.log"))

            # Write a message after session creation
            test_logger = logging.getLogger("test.session_bind")
            test_logger.info("post-create message")

            for h in logging.getLogger().handlers:
                h.flush()

            # Re-glob after flush to pick up newly created files
            matching_logs = sorted(Path(tmpdir).glob(f"agent_x1_*_{prefix}.log"))
            # Filter out error logs
            matching_logs = [f for f in matching_logs if "error" not in f.name]
            assert len(matching_logs) >= 1, (
                f"Session-specific log file not created matching agent_x1_*_{prefix}.log — "
                f"SessionManager.create_session did not call bind_session_to_log"
            )
            content = matching_logs[0].read_text()
            assert "post-create message" in content, (
                "Message not written to session-specific log"
            )

    def test_session_manager_binds_log_on_resume(self):
        """Bug: SessionManager.resume_session doesn't call bind_session_to_log,
        resumed sessions write to the generic log file."""
        from src.session.session_manager import SessionManager
        from src.session.session_store import SessionStore
        from src.core.config import AppConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)

            config = AppConfig()
            config.paths.session_dir = os.path.join(tmpdir, "sessions")
            config.paths.data_dir = os.path.join(tmpdir, "data")
            config.paths.memory_data_dir = os.path.join(tmpdir, "memory")
            os.makedirs(config.paths.session_dir, exist_ok=True)
            os.makedirs(config.paths.data_dir, exist_ok=True)
            os.makedirs(config.paths.memory_data_dir, exist_ok=True)

            store = SessionStore(os.path.join(tmpdir, "data", "sessions.db"))
            sm = SessionManager(store=store, config=config)
            session = sm.create_session(name="test-resume")
            session_id = session.id
            prefix = session_id[:8]

            # Reset logging to simulate a new process
            reset_logging()
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)

            # Resume the session
            sm2 = SessionManager(store=store, config=config)
            resumed = sm2.resume_session(session_id)

            test_logger = logging.getLogger("test.resume_bind")
            test_logger.info("post-resume message")

            for h in logging.getLogger().handlers:
                h.flush()

            # Find the session-specific log file (has timestamp in name)
            matching_logs = sorted(Path(tmpdir).glob(f"agent_x1_*_{prefix}.log"))
            matching_logs = [f for f in matching_logs if "error" not in f.name]
            assert len(matching_logs) >= 1, (
                f"Session-specific log not created on resume matching agent_x1_*_{prefix}.log"
            )
            # The resumed session's log may be a different file from the create one
            # Check the latest one for the post-resume message
            found = False
            for log_file in matching_logs:
                content = log_file.read_text()
                if "post-resume message" in content:
                    found = True
                    break
            assert found, (
                "Message not written to session-specific log after resume"
            )

    def test_concurrent_sessions_isolated(self):
        """Bug: two sessions created in sequence write to each other's log files."""
        from src.util.logger import bind_session_to_log

        ts_a = datetime(2026, 3, 29, 10, 0, 0)
        ts_b = datetime(2026, 3, 29, 11, 0, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Session A
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)
            set_session_id("aaaa111122223333")
            with patch("src.util.logger.datetime") as mock_dt:
                mock_dt.now.return_value = ts_a
                mock_dt.fromtimestamp = datetime.fromtimestamp
                bind_session_to_log("aaaa111122223333")

            logger_a = logging.getLogger("test.concurrent_a")
            logger_a.info("message-from-session-A")

            for h in logging.getLogger().handlers:
                h.flush()

            # Session B (simulating a different process)
            reset_logging()
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)
            set_session_id("bbbb444455556666")
            with patch("src.util.logger.datetime") as mock_dt:
                mock_dt.now.return_value = ts_b
                mock_dt.fromtimestamp = datetime.fromtimestamp
                bind_session_to_log("bbbb444455556666")

            logger_b = logging.getLogger("test.concurrent_b")
            logger_b.info("message-from-session-B")

            for h in logging.getLogger().handlers:
                h.flush()

            log_a = Path(tmpdir) / "agent_x1_20260329_100000_aaaa1111.log"
            log_b = Path(tmpdir) / "agent_x1_20260329_110000_bbbb4444.log"

            assert log_a.exists(), "Session A log not created"
            assert log_b.exists(), "Session B log not created"

            content_a = log_a.read_text()
            content_b = log_b.read_text()

            assert "message-from-session-A" in content_a
            assert "message-from-session-B" in content_b
            assert "message-from-session-B" not in content_a, (
                "Session B message leaked into session A log — isolation broken"
            )
            assert "message-from-session-A" not in content_b, (
                "Session A message leaked into session B log — isolation broken"
            )

    def test_pre_session_logs_in_generic_file(self):
        """Bug: logs written before any session is created are lost."""
        from src.util.logger import bind_session_to_log

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)

            # Log before session binding
            logger = logging.getLogger("test.pre_session")
            logger.info("startup-message")

            for h in logging.getLogger().handlers:
                h.flush()

            generic_log = Path(tmpdir) / "agent_x1.log"
            assert generic_log.exists(), "Generic log file should exist"
            content = generic_log.read_text()
            assert "startup-message" in content, (
                "Pre-session messages should be in the generic log file"
            )
