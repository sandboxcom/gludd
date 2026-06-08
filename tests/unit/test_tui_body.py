"""Tests for _cmd_tui body — inner functions via mocked terminal I/O."""

from __future__ import annotations

import argparse
import collections
import contextlib
from unittest.mock import MagicMock, patch

import pytest

_TermSize = collections.namedtuple("terminal_size", ["columns", "lines"])


def _ns(**kwargs: object) -> argparse.Namespace:
    defaults = {
        "daemon_url": "http://localhost:8000",
        "host": "0.0.0.0",
        "port": 8000,
        "workers": 1,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _default_config_editor() -> dict:
    return {
        "categories": [], "current_items": [], "selected_cat": 0,
        "depth": 0, "selected_item": 0, "active_overlay_path": "",
        "editor": MagicMock(editing=False), "editing_value": False,
    }


_MOCKS = {}


@contextlib.contextmanager
def _tui_patches(os_read_keys: list[bytes], extra: list | None = None):
    with contextlib.ExitStack() as stack:
        _MOCKS["pid"] = stack.enter_context(
            patch("general_ludd.cli._is_daemon_pid_alive", return_value=False))
        _MOCKS["status"] = stack.enter_context(
            patch("general_ludd.cli._gather_offline_status", return_value={}))
        stack.enter_context(
            patch("general_ludd.cli._load_config_editor",
                  return_value=_default_config_editor()))

        mgr_cls = stack.enter_context(
            patch("general_ludd.infra.local_inference.LocalInferenceManager"))
        mock_mgr = MagicMock()
        mock_mgr.list_servers.return_value = []
        mgr_cls.return_value = mock_mgr

        reg_cls = stack.enter_context(
            patch("general_ludd.models.model_registry.ModelRegistry"))
        mock_reg = MagicMock()
        mock_reg.list_downloaded.return_value = []
        reg_cls.return_value = mock_reg

        stack.enter_context(
            patch("general_ludd.cli._build_controls_table", return_value=MagicMock()))
        stack.enter_context(
            patch("general_ludd.cli._build_daemon_table", return_value=MagicMock()))
        stack.enter_context(
            patch("general_ludd.cli._build_binary_table", return_value=MagicMock()))
        stack.enter_context(
            patch("general_ludd.cli._build_info_table", return_value=MagicMock()))

        live_cls = stack.enter_context(patch("rich.live.Live"))
        live_ctx = MagicMock()
        live_cls.return_value.__enter__ = MagicMock(return_value=live_ctx)
        live_cls.return_value.__exit__ = MagicMock(return_value=False)

        stack.enter_context(
            patch("termios.tcgetattr", return_value=[0] * 7))
        stack.enter_context(patch("termios.tcsetattr"))
        stack.enter_context(patch("tty.setcbreak"))

        stdin = stack.enter_context(patch("sys.stdin"))
        stdin.fileno.return_value = 0

        stack.enter_context(
            patch("os.read", side_effect=os_read_keys + [b""] * 20))
        stack.enter_context(
            patch("select.select", return_value=([1], [], [])))
        stack.enter_context(
            patch("shutil.get_terminal_size",
                  return_value=_TermSize(80, 24)))

        if extra:
            for e in extra:
                stack.enter_context(e)

        yield _MOCKS


def _run_tui(keys: list[bytes], extra: list | None = None) -> dict:
    with _tui_patches(keys, extra):
        from general_ludd.cli import _cmd_tui
        _cmd_tui(_ns())
    return _MOCKS


class TestCmdTUIBody:

    def test_quit_immediately(self) -> None:
        _run_tui([b"q"])

    def test_ctrl_c_exits(self) -> None:
        _run_tui([b"\x03"])

    def test_view_toggle_models(self) -> None:
        extra = [
            patch("general_ludd.cli._build_model_status_msg", return_value="ok"),
            patch("general_ludd.cli._build_model_table", return_value=MagicMock()),
        ]
        _run_tui([b"m", b"q"], extra)

    def test_view_toggle_todos(self) -> None:
        _run_tui([b"t", b"q"],
                  [patch("general_ludd.cli._build_todos_table",
                         return_value=MagicMock())])

    def test_view_toggle_hooks(self) -> None:
        _run_tui([b"h", b"q"],
                  [patch("general_ludd.cli._build_hooks_table",
                         return_value=MagicMock())])

    def test_view_toggle_workers(self) -> None:
        _run_tui([b"o", b"q"],
                  [patch("general_ludd.cli._build_workers_table",
                         return_value=MagicMock())])

    def test_view_toggle_metrics(self) -> None:
        _run_tui([b"x", b"q"],
                  [patch("general_ludd.cli._build_metrics_table",
                         return_value=MagicMock())])

    def test_view_toggle_agents(self) -> None:
        _run_tui([b"g", b"q"],
                  [patch("general_ludd.cli._build_agents_table",
                         return_value=MagicMock())])

    def test_view_toggle_config(self) -> None:
        _run_tui([b"v", b"q"])

    def test_view_toggle_worktrees(self) -> None:
        _run_tui([b"w", b"q"],
                  [patch("general_ludd.cli._build_worktrees_table",
                         return_value=MagicMock())])

    def test_view_toggle_projects(self) -> None:
        _run_tui([b"p", b"q"],
                  [patch("general_ludd.cli._build_projects_table",
                         return_value=MagicMock())])

    def test_edit_view_toggle(self) -> None:
        _run_tui([b"c", b"c", b"q"],
                  [patch("general_ludd.cli._build_config_editor_table",
                         return_value=MagicMock())])

    def test_refresh_key(self) -> None:
        _run_tui([b"r", b"q"])

    def test_start_daemon_already_running(self) -> None:
        with _tui_patches([b"q"]) as mocks:
            mocks["pid"].return_value = True
            with patch("general_ludd.cli._read_daemon_pid_file",
                       return_value={"daemon_url": "http://localhost:8000"}):
                from general_ludd.cli import _cmd_tui
                _cmd_tui(_ns())

    def test_stop_daemon_via_pid(self) -> None:
        with _tui_patches([b"k", b"q"]) as mocks:
            mocks["pid"].side_effect = [False, True, False]
            with patch("general_ludd.cli._stop_daemon_via_pid_file",
                       return_value=True):
                from general_ludd.cli import _cmd_tui
                _cmd_tui(_ns())

    def test_view_toggle_integrity(self) -> None:
        _run_tui([b"i", b"q"],
                  [patch("general_ludd.cli._build_integrity_table",
                         return_value=MagicMock()),
                   patch("general_ludd.integrity.scanner.FileIntegrityScanner")])

    def test_view_toggle_ansible(self) -> None:
        _run_tui([b"a", b"q"],
                  [patch("general_ludd.cli._build_ansible_table",
                         return_value=MagicMock())])

    def test_view_toggle_mcp(self) -> None:
        _run_tui([b"u", b"q"],
                  [patch("general_ludd.cli._build_mcp_table",
                         return_value=MagicMock())])

    def test_view_toggle_skills(self) -> None:
        _run_tui([b"j", b"q"],
                  [patch("general_ludd.cli._build_skills_table",
                         return_value=MagicMock())])

    def test_view_toggle_compute(self) -> None:
        _run_tui([b"e", b"q"],
                  [patch("general_ludd.cli._build_compute_table",
                         return_value=MagicMock())])

    def test_view_toggle_scores(self) -> None:
        _run_tui([b"b", b"q"],
                  [patch("general_ludd.cli._build_scores_table",
                         return_value=MagicMock())])

    def test_view_toggle_templates(self) -> None:
        _run_tui([b"l", b"q"],
                  [patch("general_ludd.cli._build_templates_table",
                         return_value=MagicMock())])

    def test_view_toggle_quantization(self) -> None:
        _run_tui([b"n", b"q"],
                  [patch("general_ludd.cli._build_quantization_table",
                         return_value=MagicMock())])

    def test_view_toggle_filestore(self) -> None:
        _run_tui([b"f", b"q"],
                  [patch("general_ludd.cli._build_filestore_table",
                         return_value=MagicMock())])

    def test_view_toggle_deployments(self) -> None:
        _run_tui([b"z", b"q"],
                  [patch("general_ludd.cli._build_deployments_table",
                         return_value=MagicMock())])

    def test_escape_from_subview_returns_to_main(self) -> None:
        pytest.skip("Escape sequence requires complex select.select mock")

    def test_start_daemon_starts_process(self) -> None:
        _run_tui([b"S", b"q"], [
            patch("general_ludd.tui.keybindings.TUIKeyHandler._start_daemon"),
        ])

    def test_start_daemon_exits_immediately(self) -> None:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        mock_proc.returncode = 1
        extra = [
            patch("general_ludd.cli._build_daemon_start_cmd",
                  return_value=["gunicorn", "test"]),
            patch("subprocess.Popen", return_value=mock_proc),
        ]
        _run_tui([b"S", b"q"], extra)

    def test_no_daemon_to_stop(self) -> None:
        with _tui_patches([b"K", b"q"]) as mocks:
            mocks["pid"].return_value = False
            from general_ludd.cli import _cmd_tui
            _cmd_tui(_ns())

    def test_stop_daemon_with_live_proc(self) -> None:
        _run_tui([b"K", b"q"], [
            patch("general_ludd.tui.keybindings.TUIKeyHandler._stop_daemon"),
        ])
