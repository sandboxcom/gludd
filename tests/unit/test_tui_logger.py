"""TDD tests for TUI verbose logging.

The TUI must:
1. Log all user actions (key presses, view changes) to a log file
2. Log to the application database so other agent daemons can see sessions
3. Support a verbose mode toggle (press `V` to toggle)
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock, patch


class TestTUILogger:
    def test_logger_creates_log_file(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td)
            logger.log_key_press("projects", "p")
            logger.close()
            log_file = os.path.join(td, "tui.log")
            assert os.path.exists(log_file)

    def test_logger_writes_jsonl_format(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td)
            logger.log_key_press("projects", "p")
            logger.close()
            log_file = os.path.join(td, "tui.log")
            with open(log_file) as f:
                line = f.readline().strip()
                entry = json.loads(line)
                assert "timestamp" in entry
                assert entry["event"] == "key_press"
                assert entry["view"] == "projects"
                assert entry["key"] == "p"

    def test_logger_logs_view_change(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td)
            logger.log_view_change("main", "projects")
            logger.close()
            log_file = os.path.join(td, "tui.log")
            with open(log_file) as f:
                entry = json.loads(f.readline().strip())
                assert entry["event"] == "view_change"
                assert entry["from_view"] == "main"
                assert entry["to_view"] == "projects"

    def test_logger_logs_daemon_action(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td)
            logger.log_daemon_action("start", {"pid": 12345})
            logger.close()
            log_file = os.path.join(td, "tui.log")
            with open(log_file) as f:
                entry = json.loads(f.readline().strip())
                assert entry["event"] == "daemon_action"
                assert entry["action"] == "start"
                assert entry["details"]["pid"] == 12345

    def test_logger_logs_selection_change(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td)
            logger.log_selection("projects", 2, "p3")
            logger.close()
            log_file = os.path.join(td, "tui.log")
            with open(log_file) as f:
                entry = json.loads(f.readline().strip())
                assert entry["event"] == "selection_change"
                assert entry["view"] == "projects"
                assert entry["index"] == 2
                assert entry["item_id"] == "p3"

    def test_logger_verbose_toggle(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td, verbose=False)
            assert not logger.verbose
            logger.toggle_verbose()
            assert logger.verbose

    def test_logger_non_verbose_skips_key_presses(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td, verbose=False)
            logger.log_key_press("main", "r")
            logger.close()
            log_file = os.path.join(td, "tui.log")
            assert not os.path.exists(log_file)

    def test_logger_verbose_logs_key_presses(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td, verbose=True)
            logger.log_key_press("main", "r")
            logger.close()
            log_file = os.path.join(td, "tui.log")
            assert os.path.exists(log_file)

    def test_logger_flushes_to_database(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td, daemon_url="http://localhost:8000")
            with patch("httpx.post") as mock_post:
                mock_post.return_value = MagicMock(status_code=200)
                logger.log_view_change("main", "projects")
                logger.flush_to_database()
                mock_post.assert_called_once()
                call_args = mock_post.call_args
                assert "/admin/tui-log" in call_args[0][0]
                payload = call_args[1]["json"]
                assert "entries" in payload
                assert len(payload["entries"]) >= 1
                assert payload["entries"][0]["event"] == "view_change"

    def test_logger_includes_session_id(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td)
            logger.log_key_press("main", "r")
            logger.close()
            log_file = os.path.join(td, "tui.log")
            with open(log_file) as f:
                entry = json.loads(f.readline().strip())
                assert "session_id" in entry
                assert len(entry["session_id"]) > 0

    def test_logger_status_msg_logs_on_change(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td, verbose=True)
            logger.log_status_msg("Daemon started PID=1234")
            logger.close()
            log_file = os.path.join(td, "tui.log")
            with open(log_file) as f:
                entry = json.loads(f.readline().strip())
                assert entry["event"] == "status_msg"
                assert entry["message"] == "Daemon started PID=1234"
