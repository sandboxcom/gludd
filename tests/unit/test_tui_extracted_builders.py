"""Tests for TUI table builder functions extracted from _cmd_tui closures.

TDD: These tests call module-level functions that will be extracted from the
closures nested inside _cmd_tui.  They MUST fail until the extraction is done.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from rich.table import Table


def _render_table(t: Table) -> str:
    buf = StringIO()
    Console(file=buf, width=120).print(t)
    return buf.getvalue()


class TestBuildControlsTable:
    def test_returns_table_with_15_keybinding_rows(self):
        from general_ludd.cli import _build_controls_table

        t = _build_controls_table(daemon_running=False, status_msg="ready")
        assert isinstance(t, Table)
        assert t.title == "Controls"
        assert t.row_count >= 15

    def test_status_msg_row_when_provided(self):
        from general_ludd.cli import _build_controls_table

        t = _build_controls_table(daemon_running=False, status_msg="hello world")
        assert t.row_count >= 16

    def test_daemon_running_shows_in_status(self):
        from general_ludd.cli import _build_controls_table

        t = _build_controls_table(daemon_running=True, status_msg="")
        assert "running" in _render_table(t)

    def test_daemon_stopped_shows_in_status(self):
        from general_ludd.cli import _build_controls_table

        t = _build_controls_table(daemon_running=False, status_msg="")
        assert "stopped" in _render_table(t)


class TestBuildDaemonTable:
    def test_returns_table_with_daemon_info(self):
        from general_ludd.cli import _build_daemon_table

        t = _build_daemon_table(daemon_running=True, daemon_url="http://localhost:8000", current_view="main")
        assert isinstance(t, Table)
        assert t.title == "Daemon"
        assert t.row_count >= 3

    def test_long_url_truncated(self):
        from general_ludd.cli import _build_daemon_table

        long_url = "http://very-long-hostname-that-exceeds-limit.example.com:8000"
        t = _build_daemon_table(daemon_running=True, daemon_url=long_url, current_view="main")
        assert "..." in _render_table(t)

    def test_shows_current_view(self):
        from general_ludd.cli import _build_daemon_table

        t = _build_daemon_table(daemon_running=False, daemon_url="http://x:8000", current_view="models")
        assert "models" in _render_table(t)


class TestBuildInfoTable:
    def test_returns_table_with_system_info(self):
        from general_ludd.cli import _build_info_table

        info = {
            "version": "1.0",
            "python_version": "3.11",
            "platform": "darwin",
            "cwd": "/home/user",
            "config_dir": "/etc/gludd",
            "config_files": [{"name": "a.yaml"}, {"name": "b.yaml"}],
            "filestore_root": "/var/gludd",
            "filestore_size_bytes": 1024,
            "db_engine": "postgresql",
            "db_exists": True,
            "db_size_bytes": 2048,
        }
        t = _build_info_table(info)
        assert isinstance(t, Table)
        assert t.title == "System Info"
        assert t.row_count >= 10

    def test_db_size_row_when_db_exists(self):
        from general_ludd.cli import _build_info_table

        info = {"db_exists": True, "db_size_bytes": 4096}
        t = _build_info_table(info)
        assert t.row_count >= 11

    def test_no_db_size_row_when_db_missing(self):
        from general_ludd.cli import _build_info_table

        info = {"db_exists": False}
        t = _build_info_table(info)
        assert t.row_count == 10

    def test_defaults_for_missing_keys(self):
        from general_ludd.cli import _build_info_table

        t = _build_info_table({})
        assert isinstance(t, Table)
        assert "?" in _render_table(t)


class TestBuildBinaryTable:
    def test_returns_table_with_binaries(self):
        from general_ludd.cli import _build_binary_table

        info = {
            "binary_paths": {
                "ansible": "/usr/bin/ansible",
                "podman": "",
                "terraform": "/usr/local/bin/tf",
            }
        }
        t = _build_binary_table(info)
        assert isinstance(t, Table)
        assert t.title == "Binaries"
        assert t.row_count == 3

    def test_found_yes_no(self):
        from general_ludd.cli import _build_binary_table

        info = {"binary_paths": {"found_bin": "/path", "missing_bin": ""}}
        t = _build_binary_table(info)
        rendered = _render_table(t)
        assert "yes" in rendered
        assert "no" in rendered

    def test_empty_binary_paths(self):
        from general_ludd.cli import _build_binary_table

        t = _build_binary_table({"binary_paths": {}})
        assert t.row_count == 0


class TestBuildConfigTable:
    def test_returns_table_with_config_files(self):
        from general_ludd.cli import _build_config_table

        info = {
            "config_files": [
                {"name": "config.yaml", "size_bytes": 1024},
                {"name": "secrets.yaml", "size_bytes": 512},
            ]
        }
        t = _build_config_table(info)
        assert isinstance(t, Table)
        assert t.title == "Config Files"
        assert t.row_count == 2
        assert t.show_header is True

    def test_empty_config_files(self):
        from general_ludd.cli import _build_config_table

        t = _build_config_table({"config_files": []})
        assert t.row_count == 0

    def test_missing_name_defaults(self):
        from general_ludd.cli import _build_config_table

        info = {"config_files": [{"size_bytes": 100}]}
        t = _build_config_table(info)
        assert t.row_count == 1


class TestCmdHelp:
    def test_prints_man_page_and_exits(self, capsys):
        from general_ludd.cli import MAN_PAGE, _cmd_help

        args = MagicMock()
        with pytest.raises(SystemExit) as exc_info:
            _cmd_help(args)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert MAN_PAGE in captured.out


class TestCmdFilestoreList:
    @patch("general_ludd.cli.httpx.get")
    def test_success_prints_entries(self, mock_get, capsys):
        from general_ludd.cli import _cmd_filestore_list

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "path": "/data",
                "count": 2,
                "entries": [
                    {"name": "file.txt", "is_dir": False, "size": 100},
                    {"name": "subdir", "is_dir": True, "size": 0},
                ],
            },
        )
        args = MagicMock(daemon_url="http://localhost:8000", path="/data")
        _cmd_filestore_list(args)
        out = capsys.readouterr().out
        assert "2 entries" in out
        assert "file.txt" in out
        assert "subdir" in out
        assert "[DIR]" in out

    @patch("general_ludd.cli._handle_connection_error")
    @patch("general_ludd.cli.httpx.get", side_effect=Exception("conn fail"))
    def test_connection_error(self, mock_get, mock_handler, capsys):
        from general_ludd.cli import _cmd_filestore_list

        args = MagicMock(daemon_url="http://localhost:8000", path="/data")
        _cmd_filestore_list(args)
        mock_handler.assert_called_once()


class TestCmdFilestoreCat:
    @patch("general_ludd.cli.httpx.get")
    def test_success_text_file(self, mock_get, capsys):
        from general_ludd.cli import _cmd_filestore_cat

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"error": None, "binary": False, "content": "hello world", "path": "/f.txt"},
        )
        args = MagicMock(daemon_url="http://localhost:8000", path="/f.txt")
        _cmd_filestore_cat(args)
        assert "hello world" in capsys.readouterr().out

    @patch("general_ludd.cli.httpx.get")
    def test_binary_file(self, mock_get, capsys):
        from general_ludd.cli import _cmd_filestore_cat

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"error": None, "binary": True, "path": "/img.png"},
        )
        args = MagicMock(daemon_url="http://localhost:8000", path="/img.png")
        _cmd_filestore_cat(args)
        assert "Binary file" in capsys.readouterr().out

    @patch("general_ludd.cli.httpx.get")
    def test_error_response(self, mock_get, capsys):
        from general_ludd.cli import _cmd_filestore_cat

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"error": "not found", "binary": False},
        )
        args = MagicMock(daemon_url="http://localhost:8000", path="/x")
        with pytest.raises(SystemExit):
            _cmd_filestore_cat(args)


class TestCmdFilestoreBootstrap:
    @patch("general_ludd.cli.httpx.post")
    def test_success(self, mock_post, capsys):
        from general_ludd.cli import _cmd_filestore_bootstrap

        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"success": True, "binary": "ansible"},
        )
        args = MagicMock(daemon_url="http://localhost:8000", binary="ansible")
        _cmd_filestore_bootstrap(args)
        assert "Downloaded" in capsys.readouterr().out

    @patch("general_ludd.cli.httpx.post")
    def test_failure(self, mock_post, capsys):
        from general_ludd.cli import _cmd_filestore_bootstrap

        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"success": False, "error": "disk full"},
        )
        args = MagicMock(daemon_url="http://localhost:8000", binary="ansible")
        _cmd_filestore_bootstrap(args)
        assert "Failed" in capsys.readouterr().out


class TestCmdFilestoreBinaries:
    @patch("general_ludd.cli.httpx.get")
    def test_success(self, mock_get, capsys):
        from general_ludd.cli import _cmd_filestore_binaries

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"count": 2, "binaries": [{"name": "a", "size": 100}, {"name": "b", "size": 200}]},
        )
        args = MagicMock(daemon_url="http://localhost:8000")
        _cmd_filestore_binaries(args)
        out = capsys.readouterr().out
        assert "2" in out
        assert "a" in out


class TestCmdIntegrityScan:
    @patch("general_ludd.cli.httpx.post")
    def test_success_with_changes(self, mock_post, capsys):
        from general_ludd.cli import _cmd_integrity_scan

        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "scanned": 10,
                "changes": [
                    {"file": "a.yaml", "type": "new", "approved": False},
                    {"file": "b.yaml", "type": "modified", "approved": True},
                ],
            },
        )
        args = MagicMock(daemon_url="http://localhost:8000", paths=None)
        _cmd_integrity_scan(args)
        out = capsys.readouterr().out
        assert "10 files" in out
        assert "+" in out
        assert "~" in out

    @patch("general_ludd.cli.httpx.post")
    def test_success_no_changes(self, mock_post, capsys):
        from general_ludd.cli import _cmd_integrity_scan

        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"scanned": 5, "changes": []},
        )
        args = MagicMock(daemon_url="http://localhost:8000", paths=None)
        _cmd_integrity_scan(args)
        assert "No changes" in capsys.readouterr().out


class TestCmdIntegrityReport:
    @patch("general_ludd.cli.httpx.get")
    def test_success(self, mock_get, capsys):
        from general_ludd.cli import _cmd_integrity_report

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"scanned": 3, "changes": []},
        )
        args = MagicMock(daemon_url="http://localhost:8000")
        _cmd_integrity_report(args)
        assert "scanned" in capsys.readouterr().out


class TestCmdIntegrityApprove:
    @patch("general_ludd.cli.httpx.post")
    def test_success(self, mock_post, capsys):
        from general_ludd.cli import _cmd_integrity_approve

        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"path": "a.yaml", "signature": "abc123def456"},
        )
        args = MagicMock(daemon_url="http://localhost:8000", change_id="a.yaml", reason="ok", signer="admin")
        _cmd_integrity_approve(args)
        assert "Approved" in capsys.readouterr().out


class TestCmdIntegrityReject:
    @patch("general_ludd.cli.httpx.post")
    def test_success(self, mock_post, capsys):
        from general_ludd.cli import _cmd_integrity_reject

        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"path": "b.yaml", "status": "rejected"},
        )
        args = MagicMock(daemon_url="http://localhost:8000", change_id="b.yaml", reason="bad")
        _cmd_integrity_reject(args)
        assert "rejected" in capsys.readouterr().out.lower()


class TestCmdIntegrityLog:
    @patch("general_ludd.cli.httpx.get")
    def test_success(self, mock_get, capsys):
        from general_ludd.cli import _cmd_integrity_log

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "entries": [
                    {"path": "a.yaml", "action": "approved", "timestamp": "2026-01-01T00:00:00", "signer": "admin"},
                ],
            },
        )
        args = MagicMock(daemon_url="http://localhost:8000")
        _cmd_integrity_log(args)
        out = capsys.readouterr().out
        assert "approved" in out
        assert "a.yaml" in out


class TestCmdAnsibleSearch:
    @patch("general_ludd.cli.httpx.get")
    def test_success(self, mock_get, capsys):
        from general_ludd.cli import _cmd_ansible_search

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"results": [{"name": "nginx", "description": "web server"}]},
        )
        args = MagicMock(daemon_url="http://localhost:8000", query="nginx", type="role")
        _cmd_ansible_search(args)
        assert "nginx" in capsys.readouterr().out

    @patch("general_ludd.cli.httpx.get", side_effect=Exception("down"))
    @patch("general_ludd.ansible.galaxy.search_galaxy")
    def test_fallback_offline(self, mock_galaxy, mock_get, capsys):
        from general_ludd.cli import _cmd_ansible_search

        mock_galaxy.return_value = [{"name": "nginx-offline", "description": "cached"}]
        args = MagicMock(daemon_url="http://localhost:8000", query="nginx", type="role")
        _cmd_ansible_search(args)
        assert "nginx-offline" in capsys.readouterr().out


class TestCmdAnsibleInstall:
    @patch("general_ludd.cli.httpx.post")
    def test_success(self, mock_post, capsys):
        from general_ludd.cli import _cmd_ansible_install

        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"success": True, "output": "installed"},
        )
        args = MagicMock(daemon_url="http://localhost:8000", name="nginx", type="role")
        _cmd_ansible_install(args)
        assert "OK" in capsys.readouterr().out


class TestCmdAnsibleBuiltins:
    @patch("general_ludd.cli.httpx.get")
    def test_success(self, mock_get, capsys):
        from general_ludd.cli import _cmd_ansible_builtins

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"modules": ["copy", "file", "shell"]},
        )
        args = MagicMock(daemon_url="http://localhost:8000")
        _cmd_ansible_builtins(args)
        out = capsys.readouterr().out
        assert "copy" in out
        assert "shell" in out


class TestScanLocalIntegrity:
    @patch("general_ludd.integrity.scanner.FileIntegrityScanner")
    def test_calls_scanner_with_valid_paths(self, mock_scanner_cls):
        from general_ludd.cli import _scan_local_integrity

        mock_instance = MagicMock()
        mock_instance.scan.return_value = {"scanned": 5, "changes": []}
        mock_scanner_cls.return_value = mock_instance
        info = {"config_dir": "/etc/gludd", "filestore_root": "/var/gludd"}
        with patch("os.path.isdir", return_value=True), patch("os.path.expanduser", side_effect=lambda x: x):
            result = _scan_local_integrity(info)
        assert result["scanned"] == 5

    @patch("general_ludd.integrity.scanner.FileIntegrityScanner")
    def test_no_valid_paths(self, mock_scanner_cls):
        from general_ludd.cli import _scan_local_integrity

        mock_instance = MagicMock()
        mock_instance.scan.return_value = {"scanned": 0, "changes": []}
        mock_scanner_cls.return_value = mock_instance
        info = {"config_dir": "", "filestore_root": ""}
        with patch("os.path.isdir", return_value=False), patch("os.path.expanduser", side_effect=lambda x: x):
            result = _scan_local_integrity(info)
        assert result["scanned"] == 0


class TestLoadConfigEditor:
    @patch("general_ludd.tui.config_editor.ConfigEditor")
    def test_returns_nav_dict(self, mock_editor_cls):
        from general_ludd.cli import _load_config_editor

        mock_cats = [MagicMock(name="cat1"), MagicMock(name="cat2")]
        mock_editor_cls.return_value.get_categories.return_value = mock_cats
        result = _load_config_editor()
        assert result["selected_cat"] == 0
        assert result["depth"] == 0
        assert result["current_items"] == mock_cats
        assert result["categories"] == mock_cats
        assert "editor" in result
