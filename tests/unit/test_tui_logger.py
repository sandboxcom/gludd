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


class TestTUILoggerDeep:
    def test_flush_sends_only_last_50_entries(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td, daemon_url="http://localhost:8000")
            for i in range(100):
                logger.log_key_press("main", str(i))
            with patch("httpx.post") as mock_post:
                mock_post.return_value = MagicMock(status_code=200)
                logger.flush_to_database()
                payload = mock_post.call_args[1]["json"]
                assert len(payload["entries"]) == 50
                assert payload["entries"][0]["key"] == "50"
                assert payload["entries"][-1]["key"] == "99"

    def test_flush_noop_without_daemon_url(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td, daemon_url="")
            logger.log_key_press("main", "x")
            with patch("httpx.post") as mock_post:
                logger.flush_to_database()
                mock_post.assert_not_called()

    def test_flush_noop_with_empty_entries(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td, daemon_url="http://localhost:8000")
            with patch("httpx.post") as mock_post:
                logger.flush_to_database()
                mock_post.assert_not_called()

    def test_flush_survives_httpx_error(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td, daemon_url="http://localhost:8000")
            logger.log_key_press("main", "x")
            with patch("httpx.post", side_effect=Exception("network down")):
                logger.flush_to_database()

    def test_close_without_entries_does_not_flush(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td, daemon_url="http://localhost:8000")
            with patch.object(logger, "flush_to_database") as mock_flush:
                logger.close()
                mock_flush.assert_not_called()

    def test_close_with_entries_flushes(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td, daemon_url="http://localhost:8000")
            logger.log_key_press("main", "x")
            with patch.object(logger, "flush_to_database") as mock_flush:
                logger.close()
                mock_flush.assert_called_once()

    def test_write_without_log_dir_stores_memory_only(self):
        from general_ludd.tui.logger import TUILogger

        logger = TUILogger(log_dir="")
        logger.log_key_press("main", "q")
        logger.log_view_change("main", "projects")
        assert len(logger._entries) == 2
        assert logger._log_path == ""

    def test_two_loggers_have_different_session_ids(self):
        from general_ludd.tui.logger import TUILogger

        a = TUILogger()
        b = TUILogger()
        assert a._session_id != b._session_id
        assert len(a._session_id) == 12

    def test_daemon_action_defaults_empty_details(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td)
            logger.log_daemon_action("restart")
            logger.close()
            log_file = os.path.join(td, "tui.log")
            with open(log_file) as f:
                entry = json.loads(f.readline().strip())
                assert entry["event"] == "daemon_action"
                assert entry["details"] == {}

    def test_selection_with_zero_index_and_empty_id(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td)
            logger.log_selection("main", 0, "")
            logger.close()
            log_file = os.path.join(td, "tui.log")
            with open(log_file) as f:
                entry = json.loads(f.readline().strip())
                assert entry["event"] == "selection_change"
                assert entry["index"] == 0
                assert entry["item_id"] == ""

    def test_entries_accumulate_in_memory_list(self):
        from general_ludd.tui.logger import TUILogger

        logger = TUILogger(log_dir="")
        assert logger._entries == []
        logger.log_key_press("a", "1")
        logger.log_key_press("b", "2")
        logger.log_key_press("c", "3")
        assert len(logger._entries) == 3

    def test_toggle_verbose_multiple_times(self):
        from general_ludd.tui.logger import TUILogger

        logger = TUILogger(verbose=False)
        assert not logger.verbose
        logger.toggle_verbose()
        assert logger.verbose
        logger.toggle_verbose()
        assert not logger.verbose
        logger.toggle_verbose()
        assert logger.verbose

    def test_multiple_view_changes_in_log_file(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td)
            logger.log_view_change("main", "projects")
            logger.log_view_change("projects", "models")
            logger.log_view_change("models", "config")
            logger.close()
            log_file = os.path.join(td, "tui.log")
            with open(log_file) as f:
                lines = [json.loads(line) for line in f]
                assert len(lines) == 3
                events = [e["event"] for e in lines]
                assert events == ["view_change", "view_change", "view_change"]
                assert lines[0]["from_view"] == "main"
                assert lines[2]["to_view"] == "config"

    def test_timestamps_are_monotonically_increasing(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td)
            logger.log_key_press("a", "1")
            logger.log_key_press("b", "2")
            logger.log_key_press("c", "3")
            logger.close()
            log_file = os.path.join(td, "tui.log")
            with open(log_file) as f:
                entries = [json.loads(line) for line in f]
                timestamps = [e["timestamp"] for e in entries]
                assert timestamps == sorted(timestamps)
                assert len(set(timestamps)) >= 1

    def test_session_id_consistent_across_entries(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td)
            logger.log_key_press("main", "x")
            logger.log_view_change("main", "projects")
            logger.log_status_msg("hello")
            logger.close()
            log_file = os.path.join(td, "tui.log")
            with open(log_file) as f:
                entries = [json.loads(line) for line in f]
                session_ids = {e["session_id"] for e in entries}
                assert len(session_ids) == 1
                assert len(next(iter(session_ids))) == 12

    def test_makedirs_called_when_log_dir_provided(self, tmp_path):
        from general_ludd.tui.logger import TUILogger

        log_dir = str(tmp_path / "new_log_dir")
        TUILogger(log_dir=log_dir)
        assert os.path.isdir(log_dir)

    def test_verbose_only_affects_key_presses_not_view_changes(self):
        from general_ludd.tui.logger import TUILogger

        with tempfile.TemporaryDirectory() as td:
            logger = TUILogger(log_dir=td, verbose=False)
            logger.log_key_press("main", "r")
            logger.log_view_change("main", "projects")
            logger.close()
            log_file = os.path.join(td, "tui.log")
            with open(log_file) as f:
                entries = [json.loads(line) for line in f]
                events = {e["event"] for e in entries}
                assert "key_press" not in events
                assert "view_change" in events
