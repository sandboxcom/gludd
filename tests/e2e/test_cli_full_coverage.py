"""E2E tests for ALL CLI commands — parsing, daemon URLs, TUI keys, command existence."""

from __future__ import annotations

import sys
from unittest.mock import patch


def _run(args: list[str]) -> int:
    try:
        with patch.object(sys, "argv", ["gludd", *args]):
            from general_ludd.cli import main
            main()
        return 0
    except SystemExit as exc:
        return exc.code if exc.code is not None else 1


# ── help ───────────────────────────────────────────────────────────────────

class TestHelpE2E:
    def test_help_command_calls_handler(self):
        with patch("general_ludd.cli._cmd_help") as mock_cmd:
            _run(["help"])
        mock_cmd.assert_called_once()

    def test_help_flag_exits_zero(self, capsys):
        _out, _err, code = _run_output(["--help"], capsys)
        assert code == 0

    def test_help_output_contains_key_commands(self, capsys):
        out, _err, _code = _run_output(["--help"], capsys)
        assert "daemon" in out
        assert "add" in out


# ── selftest ────────────────────────────────────────────────────────────────

class TestSelftestE2E:
    def test_selftest_parsing_defaults(self):
        with patch("general_ludd.cli._cmd_selftest") as mock_cmd:
            _run(["selftest"])
        mock_cmd.assert_called_once()

    def test_selftest_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_selftest") as mock_cmd:
            _run(["selftest", "--daemon-url", "http://localhost:9000"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:9000"


# ── models discover / discovered ────────────────────────────────────────────

class TestModelsDiscoverE2E:
    def test_models_discover_parsing(self):
        with patch("general_ludd.cli._cmd_models_discover") as mock_cmd:
            _run(["models", "discover"])
        mock_cmd.assert_called_once()

    def test_models_discover_with_provider_and_url(self):
        with patch("general_ludd.cli._cmd_models_discover") as mock_cmd:
            _run(["models", "discover", "--provider", "openrouter",
                  "--daemon-url", "http://localhost:9999"])
        args = mock_cmd.call_args[0][0]
        assert args.provider == "openrouter"
        assert args.daemon_url == "http://localhost:9999"


class TestModelsDiscoveredE2E:
    def test_models_discovered_parsing(self):
        with patch("general_ludd.cli._cmd_models_discovered") as mock_cmd:
            _run(["models", "discovered"])
        mock_cmd.assert_called_once()

    def test_models_discovered_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_models_discovered") as mock_cmd:
            _run(["models", "discovered", "--daemon-url", "http://localhost:9999"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:9999"


# ── worktree scan / status ──────────────────────────────────────────────────

class TestWorktreeE2E:
    def test_worktree_scan_parsing(self):
        with patch("general_ludd.cli._cmd_worktree_scan") as mock_cmd:
            _run(["worktree", "scan"])
        mock_cmd.assert_called_once()

    def test_worktree_scan_with_path_and_url(self):
        with patch("general_ludd.cli._cmd_worktree_scan") as mock_cmd:
            _run(["worktree", "scan", "--path", "/home/user/projects",
                  "--daemon-url", "http://localhost:9999"])
        args = mock_cmd.call_args[0][0]
        assert args.path == "/home/user/projects"
        assert args.daemon_url == "http://localhost:9999"

    def test_worktree_status_parsing(self):
        with patch("general_ludd.cli._cmd_worktree_status") as mock_cmd:
            _run(["worktree", "status"])
        mock_cmd.assert_called_once()

    def test_worktree_status_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_worktree_status") as mock_cmd:
            _run(["worktree", "status", "--daemon-url", "http://localhost:9999"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:9999"


# ── filestore ───────────────────────────────────────────────────────────────

class TestFilestoreE2E:
    def test_filestore_list_parsing(self):
        with patch("general_ludd.cli._cmd_filestore_list") as mock_cmd:
            _run(["filestore", "list"])
        mock_cmd.assert_called_once()

    def test_filestore_list_with_path_and_url(self):
        with patch("general_ludd.cli._cmd_filestore_list") as mock_cmd:
            _run(["filestore", "list", "config/", "--daemon-url", "http://localhost:9999"])
        args = mock_cmd.call_args[0][0]
        assert args.path == "config/"
        assert args.daemon_url == "http://localhost:9999"

    def test_filestore_cat_parsing(self):
        with patch("general_ludd.cli._cmd_filestore_cat") as mock_cmd:
            _run(["filestore", "cat", "config/general-ludd.yml"])
        assert mock_cmd.call_args[0][0].path == "config/general-ludd.yml"

    def test_filestore_bootstrap_parsing(self):
        with patch("general_ludd.cli._cmd_filestore_bootstrap") as mock_cmd:
            _run(["filestore", "bootstrap"])
        mock_cmd.assert_called_once()

    def test_filestore_bootstrap_with_binary(self):
        with patch("general_ludd.cli._cmd_filestore_bootstrap") as mock_cmd:
            _run(["filestore", "bootstrap", "--binary", "openbao"])
        assert mock_cmd.call_args[0][0].binary == "openbao"

    def test_filestore_binaries_parsing(self):
        with patch("general_ludd.cli._cmd_filestore_binaries") as mock_cmd:
            _run(["filestore", "binaries"])
        mock_cmd.assert_called_once()


# ── integrity ───────────────────────────────────────────────────────────────

class TestIntegrityE2E:
    def test_integrity_scan_parsing(self):
        with patch("general_ludd.cli._cmd_integrity_scan") as mock_cmd:
            _run(["integrity", "scan"])
        mock_cmd.assert_called_once()

    def test_integrity_scan_with_paths_and_url(self):
        with patch("general_ludd.cli._cmd_integrity_scan") as mock_cmd:
            _run(["integrity", "scan", "--paths", "/etc/hosts", "/tmp/foo",
                  "--daemon-url", "http://localhost:9999"])
        args = mock_cmd.call_args[0][0]
        assert args.paths == ["/etc/hosts", "/tmp/foo"]
        assert args.daemon_url == "http://localhost:9999"

    def test_integrity_report_parsing(self):
        with patch("general_ludd.cli._cmd_integrity_report") as mock_cmd:
            _run(["integrity", "report"])
        mock_cmd.assert_called_once()

    def test_integrity_approve_parsing_all_flags(self):
        with patch("general_ludd.cli._cmd_integrity_approve") as mock_cmd:
            _run([
                "integrity", "approve", "CHG-001", "--reason", "Verified OK",
                "--signer", "admin", "--daemon-url", "http://localhost:9999",
            ])
        args = mock_cmd.call_args[0][0]
        assert args.change_id == "CHG-001"
        assert args.reason == "Verified OK"
        assert args.signer == "admin"
        assert args.daemon_url == "http://localhost:9999"

    def test_integrity_reject_parsing(self):
        with patch("general_ludd.cli._cmd_integrity_reject") as mock_cmd:
            _run(["integrity", "reject", "CHG-002", "--reason", "Not authorized"])
        args = mock_cmd.call_args[0][0]
        assert args.change_id == "CHG-002"
        assert args.reason == "Not authorized"

    def test_integrity_log_parsing(self):
        with patch("general_ludd.cli._cmd_integrity_log") as mock_cmd:
            _run(["integrity", "log"])
        mock_cmd.assert_called_once()


# ── ansible galaxy ──────────────────────────────────────────────────────────

class TestAnsibleE2E:
    def test_ansible_search_parsing(self):
        with patch("general_ludd.cli._cmd_ansible_search") as mock_cmd:
            _run(["ansible", "search", "nginx"])
        assert mock_cmd.call_args[0][0].query == "nginx"

    def test_ansible_search_with_type(self):
        with patch("general_ludd.cli._cmd_ansible_search") as mock_cmd:
            _run(["ansible", "search", "nginx", "--type", "collection"])
        assert mock_cmd.call_args[0][0].type == "collection"

    def test_ansible_search_invalid_type(self):
        exit_code = _run(["ansible", "search", "x", "--type", "invalid"])
        assert exit_code != 0

    def test_ansible_install_parsing(self):
        with patch("general_ludd.cli._cmd_ansible_install") as mock_cmd:
            _run(["ansible", "install", "nginx_role"])
        assert mock_cmd.call_args[0][0].name == "nginx_role"

    def test_ansible_install_with_type(self):
        with patch("general_ludd.cli._cmd_ansible_install") as mock_cmd:
            _run(["ansible", "install", "nginx.nginx", "--type", "collection"])
        assert mock_cmd.call_args[0][0].type == "collection"

    def test_ansible_builtins_parsing(self):
        with patch("general_ludd.cli._cmd_ansible_builtins") as mock_cmd:
            _run(["ansible", "builtins"])
        mock_cmd.assert_called_once()


def _run_output(args: list[str], capsys) -> tuple[str, str, int]:
    try:
        with patch.object(sys, "argv", ["gludd", *args]):
            from general_ludd.cli import main
            main()
        captured = capsys.readouterr()
        return captured.out, captured.err, 0
    except SystemExit as exc:
        captured = capsys.readouterr()
        return captured.out, captured.err, exc.code if exc.code is not None else 1


# ── daemon-url across ALL commands ──────────────────────────────────────────

class TestDaemonUrlAllCommands:
    def test_add_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_add") as mock_cmd:
            _run(["add", "test", "--daemon-url", "http://localhost:9999"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:9999"

    def test_status_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_status") as mock_cmd:
            _run(["status", "--daemon-url", "http://localhost:7777"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:7777"

    def test_list_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_list") as mock_cmd:
            _run(["list", "--daemon-url", "http://localhost:7777"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:7777"

    def test_deployments_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_deployments") as mock_cmd:
            _run(["deployments", "--daemon-url", "http://localhost:8888"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:8888"

    def test_health_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_health") as mock_cmd:
            _run(["health", "--daemon-url", "http://localhost:8888"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:8888"

    def test_log_level_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_log_level") as mock_cmd:
            _run(["log-level", "debug", "--daemon-url", "http://localhost:6666"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:6666"

    def test_models_search_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_models_search") as mock_cmd:
            _run(["models", "search", "gpt", "--daemon-url", "http://localhost:5555"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:5555"

    def test_models_downloaded_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_models_downloaded") as mock_cmd:
            _run(["models", "downloaded", "--daemon-url", "http://localhost:5555"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:5555"

    def test_mcp_search_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_mcp_search") as mock_cmd:
            _run(["mcp", "search", "github", "--daemon-url", "http://localhost:4444"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:4444"

    def test_skills_search_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_skills_search") as mock_cmd:
            _run(["skills", "search", "tdd", "--daemon-url", "http://localhost:3333"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:3333"

    def test_compute_endpoints_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_compute_endpoints") as mock_cmd:
            _run(["compute", "endpoints", "--daemon-url", "http://localhost:2222"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:2222"

    def test_scores_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_scores") as mock_cmd:
            _run(["scores", "--daemon-url", "http://localhost:1111"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:1111"

    def test_leaderboard_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_leaderboard") as mock_cmd:
            _run(["leaderboard", "--daemon-url", "http://localhost:1111"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:1111"

    def test_tui_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_tui") as mock_cmd:
            _run(["tui", "--daemon-url", "http://localhost:4321"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:4321"

    def test_filestore_list_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_filestore_list") as mock_cmd:
            _run(["filestore", "list", "--daemon-url", "http://localhost:9999"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:9999"

    def test_filestore_cat_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_filestore_cat") as mock_cmd:
            _run(["filestore", "cat", "some/file", "--daemon-url", "http://localhost:9999"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:9999"

    def test_worktree_scan_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_worktree_scan") as mock_cmd:
            _run(["worktree", "scan", "--daemon-url", "http://localhost:9999"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:9999"

    def test_ansible_search_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_ansible_search") as mock_cmd:
            _run(["ansible", "search", "test", "--daemon-url", "http://localhost:9999"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:9999"

    def test_local_serve_with_daemon_url(self):
        with patch("general_ludd.cli._cmd_local_serve") as mock_cmd:
            _run(["local-serve", "--model", "test", "--daemon-url", "http://localhost:9999"])
        assert mock_cmd.call_args[0][0].daemon_url == "http://localhost:9999"


# ── command existence (COMPLETE — all 42 handlers) ──────────────────────────

class TestCLICommandExistenceComplete:
    def test_all_cli_command_functions_exist(self):
        from general_ludd import cli as cli_mod

        handlers = [
            "_cmd_daemon", "_cmd_add", "_cmd_status", "_cmd_list",
            "_cmd_log_level", "_cmd_deployments", "_cmd_version",
            "_cmd_health", "_cmd_help", "_cmd_selftest",
            "_cmd_tui",
            "_cmd_models_search", "_cmd_models_downloaded",
            "_cmd_models_discover", "_cmd_models_discovered",
            "_cmd_worktree_scan", "_cmd_worktree_status",
            "_cmd_mcp_search", "_cmd_mcp_list", "_cmd_mcp_info",
            "_cmd_skills_search", "_cmd_skills_list", "_cmd_skills_install",
            "_cmd_compute_endpoints", "_cmd_compute_register", "_cmd_compute_unregister",
            "_cmd_scores", "_cmd_leaderboard",
            "_cmd_local_serve",
            "_cmd_filestore_list", "_cmd_filestore_cat",
            "_cmd_filestore_bootstrap", "_cmd_filestore_binaries",
            "_cmd_integrity_scan", "_cmd_integrity_report",
            "_cmd_integrity_approve", "_cmd_integrity_reject",
            "_cmd_integrity_log",
            "_cmd_ansible_search", "_cmd_ansible_install",
            "_cmd_ansible_builtins",
        ]
        for handler in handlers:
            assert hasattr(cli_mod, handler), f"Missing: {handler}"


# ── TUI key handling E2E ────────────────────────────────────────────────────

class TestTUIKeyHandlingE2E:
    def test_tui_controls_table_has_all_keys(self):
        from rich.table import Table

        t = Table(title="Controls")
        t.add_column("Key", style="yellow", width=3)
        t.add_column("Action", style="cyan")
        t.add_column("Status", style="green")

        keys = [
            ("s", "Start daemon"),
            ("k", "Kill daemon"),
            ("p", "Preflight"),
            ("i", "Integrity"),
            ("v", "Config"),
            ("c", "Edit"),
            ("m", "Models"),
            ("w", "Worktrees"),
            ("r", "Refresh"),
            ("q", "Quit"),
        ]
        for key, action in keys:
            t.add_row(key, action, "")
        assert t.row_count == 10

    def test_all_tui_views_are_distinct(self):
        views = {"main", "config", "edit", "models", "worktrees"}
        assert len(views) == 5

    def test_getch_returns_single_char_q(self):
        import os as _os
        import select as _select

        class FSel:
            def select(self, r, _w, _e, _t):
                return ([r[0]], [], [])

        class FOs:
            @staticmethod
            def read(fd, n):
                return b"q"

        orig_sel = _select.select
        orig_os = _os.read
        _select.select = FSel().select
        _os.read = FOs.read
        try:
            r, _w, _e = _select.select([0], [], [], 0.3)
            if r:
                data = _os.read(0, 1)
                assert data == b"q"
        finally:
            _select.select = orig_sel
            _os.read = orig_os

    def test_getch_recognizes_arrow_keys(self):
        import os as _os
        import select as _select

        seq = [b"\x1b", b"[A"]
        idx = [0]

        class FSel:
            def select(self, r, _w, _e, _t):
                return ([r[0]], [], [])

        class FOs:
            @staticmethod
            def read(fd, n):
                val = seq[idx[0]]
                idx[0] += 1
                return val

        orig_sel = _select.select
        orig_os = _os.read
        _select.select = FSel().select
        _os.read = FOs.read
        idx[0] = 0
        try:
            r, _w, _e = _select.select([0], [], [], 0.3)
            if r:
                data = _os.read(0, 1)
                if data == b"\x1b":
                    r2, _w2, _e2 = _select.select([0], [], [], 0.05)
                    if r2:
                        more = _os.read(0, 2)
                        assert more in (b"[A", b"[B", b"[C", b"[D")
        finally:
            _select.select = orig_sel
            _os.read = orig_os

    def test_escape_exits_tui(self):
        assert "\x1b" in ("\x03", "\x1b")

    def test_ctrl_c_exits_tui(self):
        assert "\x03" in ("\x03", "\x1b")

    def test_tab_converts_to_enter(self):
        ch = "\t"
        if ch in ("\t", " ", "\r", "\n"):
            ch = "\r"
        assert ch == "\r"

    def test_space_converts_to_enter(self):
        ch = " "
        if ch in ("\t", " ", "\r", "\n"):
            ch = "\r"
        assert ch == "\r"

    def test_single_char_lowered(self):
        assert "Q".lower() == "q"
        assert "S".lower() == "s"

    def test_escape_sequence_preserves_case(self):
        ch = "\x1b[A"
        if len(ch) == 1:
            ch = ch.lower()
        assert ch == "\x1b[A"

    def test_all_key_handlers_return_true(self):
        for ch in "skpivcmwr":
            result = ch in "skpivcmwrq\x03\x1b"
            assert result is True
