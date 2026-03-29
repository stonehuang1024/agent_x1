"""
Tests for the unified logging infrastructure (src/util/logger.py).

Bug targets:
- loguru removal: importing loguru should fail / not be used
- get_logger() returns a properly configured standard logging.Logger
- Environment variable AGENT_X1_LOG_LEVEL overrides programmatic level
- Log level changes propagate to all handlers
- File rotation works at the configured size limit
- Error-only log file captures errors but not info/debug
"""

import io
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from logging.handlers import TimedRotatingFileHandler

import pytest

# Fixed timestamp for deterministic log filename tests
_FIXED_TS = datetime(2026, 3, 29, 14, 30, 0)
_FIXED_TS_STR = "20260329_143000"


class TestLoguruRemoval:
    """Verify loguru is completely removed from the logging module."""

    def test_logger_module_does_not_import_loguru(self):
        """Bug: loguru import left behind after refactoring.
        If loguru is still imported, the module will fail on systems
        without loguru installed."""
        import importlib
        import src.util.logger as logger_module

        source = importlib.util.find_spec("src.util.logger")
        source_path = source.origin
        with open(source_path, "r") as f:
            content = f.read()

        assert "from loguru" not in content, (
            "loguru import still present in logger.py — "
            "this will crash on systems without loguru installed"
        )
        assert "import loguru" not in content, (
            "loguru import still present in logger.py"
        )

    def test_loguru_not_in_requirements(self):
        """Bug: loguru left in requirements.txt causes unnecessary dependency."""
        req_path = Path(__file__).parent.parent.parent / "requirements.txt"
        if req_path.exists():
            content = req_path.read_text()
            assert "loguru" not in content.lower(), (
                "loguru still listed in requirements.txt — "
                "should be removed as part of the migration"
            )


class TestGetLogger:
    """Verify get_logger() returns properly configured loggers."""

    def setup_method(self):
        """Reset logging state before each test."""
        from src.util.logger import reset_logging
        reset_logging()

    def test_get_logger_returns_standard_logger(self):
        """Bug: get_logger might return a loguru logger or wrapper instead
        of a standard logging.Logger."""
        from src.util.logger import get_logger
        logger = get_logger("test.module")
        assert isinstance(logger, logging.Logger), (
            f"get_logger returned {type(logger).__name__}, "
            f"expected logging.Logger — loguru wrapper may still be active"
        )

    def test_get_logger_auto_configures_on_first_call(self):
        """Bug: get_logger called before setup_logging might return
        an unconfigured logger with no handlers."""
        from src.util.logger import get_logger, is_configured
        assert not is_configured(), "Logging should not be configured yet"

        logger = get_logger("test.auto")
        assert is_configured(), (
            "get_logger did not auto-configure logging — "
            "messages will be lost to the void"
        )
        assert len(logging.getLogger().handlers) > 0, (
            "Root logger has no handlers after auto-configure"
        )

    def test_get_logger_name_propagation(self):
        """Bug: logger name not passed through, all loggers share same name."""
        from src.util.logger import get_logger
        logger_a = get_logger("module.a")
        logger_b = get_logger("module.b")
        assert logger_a.name == "module.a"
        assert logger_b.name == "module.b"
        assert logger_a is not logger_b, (
            "Different module names returned the same logger instance"
        )

    def test_xlog_backward_compatibility(self):
        """Bug: xlog removed or broken, existing code using xlog crashes."""
        from src.util.logger import xlog
        assert xlog is not None, "xlog is None — backward compatibility broken"
        assert isinstance(xlog, logging.Logger), (
            f"xlog is {type(xlog).__name__}, not logging.Logger"
        )


class TestEnvironmentVariableOverride:
    """Verify AGENT_X1_LOG_LEVEL environment variable works."""

    def setup_method(self):
        from src.util.logger import reset_logging
        reset_logging()

    def test_env_var_overrides_programmatic_level(self):
        """Bug: env var ignored, programmatic level always wins.
        This means ops cannot change log level without code changes."""
        from src.util.logger import setup_logging

        with patch.dict(os.environ, {"AGENT_X1_LOG_LEVEL": "DEBUG"}):
            setup_logging(level=logging.WARNING)

        root = logging.getLogger()
        assert root.level == logging.DEBUG, (
            f"Root logger level is {logging.getLevelName(root.level)}, "
            f"expected DEBUG — AGENT_X1_LOG_LEVEL env var was ignored"
        )

    def test_invalid_env_var_falls_back_to_programmatic(self):
        """Bug: invalid env var value causes crash instead of fallback."""
        from src.util.logger import setup_logging

        with patch.dict(os.environ, {"AGENT_X1_LOG_LEVEL": "INVALID_LEVEL"}):
            setup_logging(level=logging.INFO)

        root = logging.getLogger()
        assert root.level == logging.INFO, (
            f"Invalid env var should fall back to programmatic level INFO, "
            f"got {logging.getLevelName(root.level)}"
        )

    def test_empty_env_var_falls_back(self):
        """Bug: empty string env var treated as valid level."""
        from src.util.logger import setup_logging

        with patch.dict(os.environ, {"AGENT_X1_LOG_LEVEL": ""}):
            setup_logging(level=logging.WARNING)

        root = logging.getLogger()
        assert root.level == logging.WARNING


class TestSetLogLevel:
    """Verify runtime log level changes propagate correctly."""

    def setup_method(self):
        from src.util.logger import reset_logging
        reset_logging()

    def test_set_log_level_changes_root_and_handlers(self):
        """Bug: set_log_level changes root but not handlers,
        so messages still get filtered at handler level."""
        from src.util.logger import setup_logging, set_log_level

        setup_logging(level=logging.INFO, log_to_file=False)
        set_log_level(logging.DEBUG)

        root = logging.getLogger()
        assert root.level == logging.DEBUG

        # Check non-error handlers were updated
        for handler in root.handlers:
            if handler.level != logging.ERROR:
                assert handler.level == logging.DEBUG, (
                    f"Handler {handler} still at {logging.getLevelName(handler.level)} "
                    f"after set_log_level(DEBUG) — messages will be silently dropped"
                )

    def test_set_log_level_preserves_error_handler(self):
        """Bug: set_log_level changes error handler level,
        causing non-error messages to appear in error log."""
        from src.util.logger import setup_logging, set_log_level

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.INFO, log_dir=tmpdir)
            set_log_level(logging.DEBUG)

            root = logging.getLogger()
            error_handlers = [h for h in root.handlers if h.level == logging.ERROR]
            assert len(error_handlers) > 0, (
                "Error handler disappeared after set_log_level"
            )
            for h in error_handlers:
                assert h.level == logging.ERROR, (
                    "Error handler level was changed — "
                    "debug/info messages will pollute the error log"
                )


class TestFileLogging:
    """Verify file logging with rotation."""

    def setup_method(self):
        from src.util.logger import reset_logging
        reset_logging()

    def test_log_files_created_in_specified_directory(self):
        """Bug: log files created in wrong directory or not at all."""
        from src.util.logger import setup_logging

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.INFO, log_dir=tmpdir)

            log_file = Path(tmpdir) / "agent_x1.log"
            error_file = Path(tmpdir) / "agent_x1_error.log"

            # Write a message to trigger file creation
            logger = logging.getLogger("test.file")
            logger.info("test message")
            logger.error("test error")

            # Flush handlers
            for h in logging.getLogger().handlers:
                h.flush()

            assert log_file.exists(), (
                f"Main log file not created at {log_file}"
            )
            assert error_file.exists(), (
                f"Error log file not created at {error_file}"
            )

    def test_error_log_only_contains_errors(self):
        """Bug: error log contains info/debug messages, making it
        useless for quick error scanning."""
        from src.util.logger import setup_logging

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)

            logger = logging.getLogger("test.error_filter")
            logger.debug("debug message")
            logger.info("info message")
            logger.error("error message")

            for h in logging.getLogger().handlers:
                h.flush()

            error_file = Path(tmpdir) / "agent_x1_error.log"
            content = error_file.read_text()

            assert "error message" in content, "Error message missing from error log"
            assert "debug message" not in content, (
                "Debug message leaked into error log — "
                "error handler level filter is broken"
            )
            assert "info message" not in content, (
                "Info message leaked into error log"
            )

    def test_setup_logging_idempotent(self):
        """Bug: calling setup_logging twice adds duplicate handlers,
        causing every message to be logged multiple times."""
        from src.util.logger import setup_logging

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.INFO, log_dir=tmpdir)
            handler_count_1 = len(logging.getLogger().handlers)

            setup_logging(level=logging.DEBUG, log_dir=tmpdir)
            handler_count_2 = len(logging.getLogger().handlers)

            assert handler_count_1 == handler_count_2, (
                f"Handler count changed from {handler_count_1} to {handler_count_2} "
                f"on second setup_logging call — messages will be duplicated"
            )


class TestTimedRotatingFileHandler:
    """Verify TimedRotatingFileHandler is used for daily log rotation."""

    def setup_method(self):
        from src.util.logger import reset_logging
        reset_logging()

    def test_file_handler_is_timed_rotating(self):
        """Bug: RotatingFileHandler used instead of TimedRotatingFileHandler,
        logs won't rotate daily."""
        from src.util.logger import setup_logging
        from logging.handlers import TimedRotatingFileHandler

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)

            root = logging.getLogger()
            timed_handlers = [
                h for h in root.handlers
                if isinstance(h, TimedRotatingFileHandler)
            ]
            assert len(timed_handlers) >= 2, (
                f"Expected at least 2 TimedRotatingFileHandler (main + error), "
                f"found {len(timed_handlers)} — daily rotation is broken"
            )

    def test_backup_count_is_30(self):
        """Bug: backupCount not set to 30, old logs not cleaned up or cleaned too early."""
        from src.util.logger import setup_logging
        from logging.handlers import TimedRotatingFileHandler

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)

            root = logging.getLogger()
            for h in root.handlers:
                if isinstance(h, TimedRotatingFileHandler):
                    assert h.backupCount == 30, (
                        f"TimedRotatingFileHandler backupCount is {h.backupCount}, "
                        f"expected 30 — log retention policy is wrong"
                    )

    def test_error_log_also_timed_rotating(self):
        """Bug: error log uses different handler type, won't rotate daily."""
        from src.util.logger import setup_logging
        from logging.handlers import TimedRotatingFileHandler

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)

            root = logging.getLogger()
            error_timed_handlers = [
                h for h in root.handlers
                if isinstance(h, TimedRotatingFileHandler) and h.level == logging.ERROR
            ]
            assert len(error_timed_handlers) == 1, (
                f"Expected 1 error TimedRotatingFileHandler, "
                f"found {len(error_timed_handlers)} — error log daily rotation broken"
            )


class TestDefaultDebugLevel:
    """Verify default log level is DEBUG."""

    def setup_method(self):
        from src.util.logger import reset_logging
        reset_logging()

    def test_default_level_is_debug(self):
        """Bug: default level is INFO, DEBUG messages silently dropped."""
        from src.util.logger import setup_logging

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(log_dir=tmpdir)

            root = logging.getLogger()
            assert root.level == logging.DEBUG, (
                f"Default root level is {logging.getLevelName(root.level)}, "
                f"expected DEBUG — detailed logs will be lost"
            )

    def test_console_handler_is_info(self):
        """Bug: console handler at DEBUG level floods terminal with noise."""
        from src.util.logger import setup_logging

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(log_dir=tmpdir)

            root = logging.getLogger()
            console_handlers = [
                h for h in root.handlers
                if isinstance(h, logging.StreamHandler) and not isinstance(h, TimedRotatingFileHandler)
            ]
            for h in console_handlers:
                assert h.level == logging.INFO, (
                    f"Console handler level is {logging.getLevelName(h.level)}, "
                    f"expected INFO — terminal will be flooded with DEBUG messages"
                )

    def test_file_handler_is_debug(self):
        """Bug: file handler not at DEBUG level, detailed logs not written to file."""
        from src.util.logger import setup_logging
        from logging.handlers import TimedRotatingFileHandler

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(log_dir=tmpdir)

            root = logging.getLogger()
            file_handlers = [
                h for h in root.handlers
                if isinstance(h, TimedRotatingFileHandler) and h.level != logging.ERROR
            ]
            for h in file_handlers:
                assert h.level == logging.DEBUG, (
                    f"File handler level is {logging.getLevelName(h.level)}, "
                    f"expected DEBUG — detailed logs won't be written to file"
                )


class TestSessionIdInjection:
    """Verify session_id is correctly injected into log records."""

    def setup_method(self):
        from src.util.logger import reset_logging
        reset_logging()

    def test_session_id_in_log_output(self):
        """Bug: session_id not injected, logs can't be filtered by session."""
        from src.util.logger import setup_logging, set_session_id

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)

            # set_session_id must be called AFTER setup_logging
            set_session_id("abcdef1234567890")

            logger = logging.getLogger("test.session_id")
            logger.info("test with session")

            for h in logging.getLogger().handlers:
                h.flush()

            log_file = Path(tmpdir) / "agent_x1.log"
            content = log_file.read_text()
            # Find the line with our test message specifically
            test_lines = [l for l in content.split("\n") if "test with session" in l]
            assert len(test_lines) >= 1, (
                f"Test message not found in log output. Content: {content[:300]}"
            )
            assert "[sid:abcdef12]" in test_lines[0], (
                f"session_id not found in log line — "
                f"logs cannot be filtered by session. Line: {test_lines[0][:200]}"
            )

    def test_no_session_id_shows_placeholder(self):
        """Bug: missing session_id causes format error instead of placeholder."""
        from src.util.logger import setup_logging, clear_session_id

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)
            clear_session_id()

            logger = logging.getLogger("test.no_session")
            logger.info("test without session")

            for h in logging.getLogger().handlers:
                h.flush()

            log_file = Path(tmpdir) / "agent_x1.log"
            content = log_file.read_text()
            assert "[sid:--------]" in content, (
                f"Placeholder '--------' not found when no session_id set. "
                f"Content: {content[:200]}"
            )

    def test_clear_session_id_restores_placeholder(self):
        """Bug: clear_session_id doesn't actually clear, old session_id leaks."""
        from src.util.logger import setup_logging, set_session_id, clear_session_id

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)

            set_session_id("session_aaa")
            logger = logging.getLogger("test.clear")
            logger.info("before clear")

            clear_session_id()
            logger.info("after clear")

            for h in logging.getLogger().handlers:
                h.flush()

            log_file = Path(tmpdir) / "agent_x1.log"
            content = log_file.read_text()
            lines = [l for l in content.strip().split("\n") if "after clear" in l]
            assert len(lines) == 1, "Expected exactly one 'after clear' line"
            assert "[sid:--------]" in lines[0], (
                f"session_id not cleared after clear_session_id(). "
                f"Line: {lines[0][:200]}"
            )

    def test_json_format_includes_session_id(self):
        """Bug: JSON formatter missing session_id field."""
        from src.util.logger import setup_logging, set_session_id
        import json as json_mod

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir, use_json=True)
            set_session_id("json_test_session_id")

            logger = logging.getLogger("test.json_session")
            logger.info("json session test")

            for h in logging.getLogger().handlers:
                h.flush()

            log_file = Path(tmpdir) / "agent_x1.log"
            content = log_file.read_text().strip()
            # Find the line with our test message
            for line in content.split("\n"):
                if "json session test" in line:
                    data = json_mod.loads(line)
                    assert "session_id" in data, (
                        "JSON log entry missing 'session_id' field"
                    )
                    assert data["session_id"] == "json_tes", (
                        f"JSON session_id is '{data['session_id']}', "
                        f"expected 'json_tes' (first 8 chars)"
                    )
                    break
            else:
                pytest.fail("Test message not found in JSON log output")


class TestThirdPartyLogLevels:
    """Verify third-party library log levels are suppressed."""

    def setup_method(self):
        from src.util.logger import reset_logging
        reset_logging()

    def test_third_party_loggers_at_warning(self):
        """Bug: third-party DEBUG/INFO messages flood the log file."""
        from src.util.logger import setup_logging

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)

            for lib_name in ["urllib3", "httpx", "httpcore", "asyncio"]:
                lib_logger = logging.getLogger(lib_name)
                assert lib_logger.level >= logging.WARNING, (
                    f"Third-party logger '{lib_name}' level is "
                    f"{logging.getLevelName(lib_logger.level)}, "
                    f"expected WARNING or higher — "
                    f"noisy third-party logs will flood the file"
                )


class TestTruncateForLog:
    """Verify truncate_for_log utility function."""

    def test_short_text_unchanged(self):
        """Bug: short text incorrectly truncated."""
        from src.util.logger import truncate_for_log
        text = "short text"
        assert truncate_for_log(text) == text

    def test_long_text_truncated_with_annotation(self):
        """Bug: long text not truncated, log files grow unbounded."""
        from src.util.logger import truncate_for_log
        text = "x" * 1000
        result = truncate_for_log(text, max_len=500)
        assert len(result) < 1000, "Text was not truncated"
        assert "1000 chars total" in result, (
            "Truncated text missing total length annotation"
        )
        assert result.startswith("x" * 500), "Truncation cut too early"

    def test_empty_text(self):
        """Bug: empty string causes error."""
        from src.util.logger import truncate_for_log
        assert truncate_for_log("") == ""
        assert truncate_for_log(None) == ""


class TestMaskSensitive:
    """Verify mask_sensitive utility function."""

    def test_api_key_masked(self):
        """Bug: API key logged in plaintext."""
        from src.util.logger import mask_sensitive
        result = mask_sensitive("sk-ant-api03-abcdefghijklmnop")
        assert "abcdefghijklmnop" not in result, "API key not masked"
        assert result.startswith("sk-"), "Prefix not preserved"
        assert "..." in result, "Mask separator missing"

    def test_short_value_fully_masked(self):
        """Bug: short sensitive value partially visible."""
        from src.util.logger import mask_sensitive
        result = mask_sensitive("ab")
        assert result == "***", f"Short value not fully masked: {result}"


class TestBindSessionToLog:
    """Verify bind_session_to_log isolates log files per session."""

    def setup_method(self):
        from src.util.logger import reset_logging
        reset_logging()

    def test_bind_creates_session_specific_log_files(self):
        """Bug: bind_session_to_log doesn't create session-specific files,
        multiple Agent instances still write to the same log file."""
        from src.util.logger import setup_logging, bind_session_to_log

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)
            with patch("src.util.logger.datetime") as mock_dt:
                mock_dt.now.return_value = _FIXED_TS
                mock_dt.fromtimestamp = datetime.fromtimestamp
                result = bind_session_to_log("abcdef1234567890")

            expected = Path(tmpdir) / f"agent_x1_{_FIXED_TS_STR}_abcdef12.log"
            assert result is not None, "bind_session_to_log returned None"
            assert result == expected, (
                f"Expected {expected.name}, got {result}"
            )

            # Write a log message and verify it goes to the new file
            logger = logging.getLogger("test.bind_session")
            logger.info("session-bound message")

            for h in logging.getLogger().handlers:
                h.flush()

            assert expected.exists(), (
                f"Session-specific log file not created at {expected}"
            )
            content = expected.read_text()
            assert "session-bound message" in content, (
                "Message not written to session-specific log file"
            )

    def test_bind_creates_session_specific_error_log(self):
        """Bug: error log not bound to session, errors from different
        sessions still mix in the same error log file."""
        from src.util.logger import setup_logging, bind_session_to_log

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)
            with patch("src.util.logger.datetime") as mock_dt:
                mock_dt.now.return_value = _FIXED_TS
                mock_dt.fromtimestamp = datetime.fromtimestamp
                bind_session_to_log("abcdef1234567890")

            logger = logging.getLogger("test.bind_error")
            logger.error("session-bound error")

            for h in logging.getLogger().handlers:
                h.flush()

            error_log = Path(tmpdir) / f"agent_x1_error_{_FIXED_TS_STR}_abcdef12.log"
            assert error_log.exists(), (
                f"Session-specific error log not created at {error_log}"
            )
            content = error_log.read_text()
            assert "session-bound error" in content, (
                "Error not written to session-specific error log"
            )

    def test_bind_is_idempotent(self):
        """Bug: calling bind_session_to_log twice with same session_id
        creates duplicate handlers or corrupts file handles."""
        from src.util.logger import setup_logging, bind_session_to_log

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)

            with patch("src.util.logger.datetime") as mock_dt:
                mock_dt.now.return_value = _FIXED_TS
                mock_dt.fromtimestamp = datetime.fromtimestamp
                result1 = bind_session_to_log("abcdef1234567890")
                handler_count_1 = len(logging.getLogger().handlers)

                result2 = bind_session_to_log("abcdef1234567890")
                handler_count_2 = len(logging.getLogger().handlers)

            assert result1 == result2, (
                "Idempotent calls returned different paths"
            )
            assert handler_count_1 == handler_count_2, (
                f"Handler count changed from {handler_count_1} to {handler_count_2} "
                f"on second bind call — handlers may be duplicated"
            )

    def test_bind_before_setup_returns_none(self):
        """Bug: bind_session_to_log called before setup_logging crashes
        instead of returning None gracefully."""
        from src.util.logger import bind_session_to_log
        # reset_logging already called in setup_method, so logging is not configured
        result = bind_session_to_log("abcdef1234567890")
        assert result is None, (
            "bind_session_to_log should return None when logging not configured"
        )

    def test_bind_with_empty_session_id_returns_none(self):
        """Bug: empty session_id causes crash or creates malformed filename."""
        from src.util.logger import setup_logging, bind_session_to_log

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)
            assert bind_session_to_log("") is None, (
                "Empty session_id should return None"
            )
            assert bind_session_to_log(None) is None, (
                "None session_id should return None"
            )

    def test_messages_not_in_old_log_after_bind(self):
        """Bug: after binding, messages still written to the old generic
        log file, defeating the purpose of per-session isolation."""
        from src.util.logger import setup_logging, bind_session_to_log

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)

            # Write a message before binding
            logger = logging.getLogger("test.before_bind")
            logger.info("before-bind message")

            for h in logging.getLogger().handlers:
                h.flush()

            old_log = Path(tmpdir) / "agent_x1.log"
            old_content_before = old_log.read_text() if old_log.exists() else ""
            assert "before-bind message" in old_content_before, (
                "Pre-bind message should be in the generic log"
            )

            # Now bind
            with patch("src.util.logger.datetime") as mock_dt:
                mock_dt.now.return_value = _FIXED_TS
                mock_dt.fromtimestamp = datetime.fromtimestamp
                bind_session_to_log("session123456789")

            # Write a message after binding
            logger.info("after-bind message")
            for h in logging.getLogger().handlers:
                h.flush()

            # The new message should NOT appear in the old generic log
            old_content_after = old_log.read_text() if old_log.exists() else ""
            new_lines = old_content_after.replace(old_content_before, "")
            assert "after-bind message" not in new_lines, (
                "Post-bind message leaked to old generic log file — "
                "log isolation is broken"
            )

            # The new message SHOULD appear in the session-specific log
            session_log = Path(tmpdir) / f"agent_x1_{_FIXED_TS_STR}_session1.log"
            assert session_log.exists(), "Session log file not created"
            session_content = session_log.read_text()
            assert "after-bind message" in session_content, (
                "Post-bind message not in session-specific log"
            )

    def test_two_sessions_write_to_different_files(self):
        """Bug: two sequential sessions write to the same file,
        logs from different sessions are mixed."""
        from src.util.logger import setup_logging, bind_session_to_log, reset_logging

        ts_a = datetime(2026, 3, 29, 10, 0, 0)
        ts_b = datetime(2026, 3, 29, 11, 0, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Session 1
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)
            with patch("src.util.logger.datetime") as mock_dt:
                mock_dt.now.return_value = ts_a
                mock_dt.fromtimestamp = datetime.fromtimestamp
                bind_session_to_log("aaaa111122223333")

            logger = logging.getLogger("test.multi_session")
            logger.info("session-A message")
            for h in logging.getLogger().handlers:
                h.flush()

            # Reset and start session 2
            reset_logging()
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)
            with patch("src.util.logger.datetime") as mock_dt:
                mock_dt.now.return_value = ts_b
                mock_dt.fromtimestamp = datetime.fromtimestamp
                bind_session_to_log("bbbb444455556666")

            logger2 = logging.getLogger("test.multi_session")
            logger2.info("session-B message")
            for h in logging.getLogger().handlers:
                h.flush()

            # Verify isolation
            log_a = Path(tmpdir) / "agent_x1_20260329_100000_aaaa1111.log"
            log_b = Path(tmpdir) / "agent_x1_20260329_110000_bbbb4444.log"

            assert log_a.exists(), f"Session A log not found: {log_a}"
            assert log_b.exists(), f"Session B log not found: {log_b}"

            content_a = log_a.read_text()
            content_b = log_b.read_text()

            assert "session-A message" in content_a, (
                "Session A message not in session A log"
            )
            assert "session-B message" in content_b, (
                "Session B message not in session B log"
            )
            assert "session-B message" not in content_a, (
                "Session B message leaked into session A log"
            )
            assert "session-A message" not in content_b, (
                "Session A message leaked into session B log"
            )

    def test_reset_logging_clears_bound_session(self):
        """Bug: reset_logging doesn't clear _bound_session_prefix,
        next session reuses stale binding."""
        from src.util.logger import (
            setup_logging, bind_session_to_log, reset_logging,
            _bound_session_prefix,
        )
        import src.util.logger as logger_module

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)
            bind_session_to_log("abcdef1234567890")
            assert logger_module._bound_session_prefix == "abcdef12"

            reset_logging()
            assert logger_module._bound_session_prefix is None, (
                "_bound_session_prefix not cleared after reset_logging — "
                "next session will skip binding"
            )

    def test_bind_log_message_confirms_binding(self):
        """Bug: no confirmation log after binding, hard to verify in production."""
        from src.util.logger import setup_logging, bind_session_to_log

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)
            with patch("src.util.logger.datetime") as mock_dt:
                mock_dt.now.return_value = _FIXED_TS
                mock_dt.fromtimestamp = datetime.fromtimestamp
                bind_session_to_log("abcdef1234567890")

            for h in logging.getLogger().handlers:
                h.flush()

            session_log = Path(tmpdir) / f"agent_x1_{_FIXED_TS_STR}_abcdef12.log"
            content = session_log.read_text()
            assert "[Logger] Log files bound to session abcdef12" in content, (
                "Binding confirmation log message not found"
            )

    def test_timed_rotating_handler_preserved_after_bind(self):
        """Bug: bind_session_to_log replaces TimedRotatingFileHandler with
        a plain FileHandler, breaking daily rotation."""
        from src.util.logger import setup_logging, bind_session_to_log

        with tempfile.TemporaryDirectory() as tmpdir:
            setup_logging(level=logging.DEBUG, log_dir=tmpdir)
            bind_session_to_log("abcdef1234567890")

            root = logging.getLogger()
            timed_handlers = [
                h for h in root.handlers
                if isinstance(h, TimedRotatingFileHandler)
            ]
            assert len(timed_handlers) >= 2, (
                f"Expected >= 2 TimedRotatingFileHandler after bind, "
                f"found {len(timed_handlers)} — daily rotation is broken"
            )
