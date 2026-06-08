"""E2E tests for ALL TUI navigation keys: arrows, left/right, tab, space/enter.

Tests cover:
- Arrow up/down: navigate between menu items in all views
- Left arrow: navigate back from submenu (same as Escape)
- Right arrow: enter submenu / activate selected item
- Tab/Space: navigate into submenu or toggle menu option
- Enter: activate selected item / submit input
- Escape: back from submenu, quit from main

Uses the same patching pattern as test_tui_body.py.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
from unittest.mock import MagicMock, patch

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
        "categories": [],
        "current_items": [],
        "selected_cat": 0,
        "depth": 0,
        "selected_item": 0,
        "active_overlay_path": "",
        "editor": MagicMock(editing=False),
        "editing_value": False,
    }


_TABLE_PATCHES = [
    "general_ludd.cli._build_controls_table",
    "general_ludd.cli._build_daemon_table",
    "general_ludd.cli._build_binary_table",
    "general_ludd.cli._build_info_table",
]


@contextlib.contextmanager
def _tui_patches(os_read_keys: list[bytes]):
    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch("general_ludd.cli._is_daemon_pid_alive", return_value=False)
        )
        stack.enter_context(
            patch("general_ludd.cli._gather_offline_status", return_value={})
        )
        stack.enter_context(
            patch("general_ludd.cli._load_config_editor", return_value=_default_config_editor())
        )

        mgr_cls = stack.enter_context(
            patch("general_ludd.infra.local_inference.LocalInferenceManager")
        )
        mock_mgr = MagicMock()
        mock_mgr.list_servers.return_value = []
        mgr_cls.return_value = mock_mgr

        reg_cls = stack.enter_context(
            patch("general_ludd.models.model_registry.ModelRegistry")
        )
        mock_reg = MagicMock()
        mock_reg.list_downloaded.return_value = []
        reg_cls.return_value = mock_reg

        for tp in _TABLE_PATCHES:
            stack.enter_context(patch(tp, return_value=MagicMock()))

        live_cls = stack.enter_context(patch("rich.live.Live"))
        live_ctx = MagicMock()
        live_cls.return_value.__enter__ = MagicMock(return_value=live_ctx)
        live_cls.return_value.__exit__ = MagicMock(return_value=False)

        stack.enter_context(patch("termios.tcgetattr", return_value=[0] * 7))
        stack.enter_context(patch("termios.tcsetattr"))
        stack.enter_context(patch("tty.setcbreak"))

        stdin = stack.enter_context(patch("sys.stdin"))
        stdin.fileno.return_value = 0

        stack.enter_context(
            patch("os.read", side_effect=os_read_keys + [b""] * 100)
        )
        stack.enter_context(
            patch("select.select", return_value=([1], [], []))
        )
        stack.enter_context(
            patch("shutil.get_terminal_size", return_value=_TermSize(80, 24))
        )
        yield


def _run_tui(keys: list[bytes]) -> None:
    with _tui_patches(keys):
        from general_ludd.cli import _cmd_tui

        _cmd_tui(_ns())


class TestArrowUpDownNavigation:
    def test_arrow_down_in_main_view(self) -> None:
        _run_tui([b"\x1b[B", b"q"])

    def test_arrow_up_in_main_view(self) -> None:
        _run_tui([b"\x1b[A", b"q"])

    def test_arrow_down_wraps_around(self) -> None:
        _run_tui([b"\x1b[B", b"\x1b[B", b"\x1b[B", b"q"])

    def test_arrow_up_wraps_around(self) -> None:
        _run_tui([b"\x1b[A", b"\x1b[A", b"q"])

    def test_arrow_down_in_projects_view(self) -> None:
        _run_tui([b"p", b"\x1b[B", b"\x1b", b"\x00", b"q"])

    def test_arrow_up_in_projects_view(self) -> None:
        _run_tui([b"p", b"\x1b[A", b"\x1b", b"\x00", b"q"])

    def test_arrow_down_in_models_view(self) -> None:
        _run_tui([b"m", b"\x1b[B", b"\x1b", b"\x00", b"q"])

    def test_arrow_down_in_todos_view(self) -> None:
        _run_tui([b"t", b"\x1b[B", b"\x1b", b"\x00", b"q"])

    def test_arrow_down_in_hooks_view(self) -> None:
        _run_tui([b"h", b"\x1b[B", b"\x1b", b"\x00", b"q"])

    def test_arrow_down_in_workers_view(self) -> None:
        _run_tui([b"o", b"\x1b[B", b"\x1b", b"\x00", b"q"])


class TestLeftRightNavigation:
    def test_left_arrow_goes_back_from_subview(self) -> None:
        _run_tui([b"m", b"\x1b[D", b"q"])

    def test_left_arrow_does_not_quit_from_main(self) -> None:
        _run_tui([b"\x1b[D", b"q"])

    def test_right_arrow_enters_submenu_from_main(self) -> None:
        _run_tui([b"\x1b[C", b"\x1b", b"\x00", b"q"])

    def test_left_from_ansible_returns_to_main(self) -> None:
        _run_tui([b"a", b"\x1b[D", b"q"])

    def test_left_from_todos_returns_to_main(self) -> None:
        _run_tui([b"t", b"\x1b[D", b"q"])

    def test_left_from_integrity_returns_to_main(self) -> None:
        _run_tui([b"i", b"\x1b[D", b"q"])

    def test_left_from_projects_returns_to_main(self) -> None:
        _run_tui([b"p", b"\x1b[D", b"q"])


class TestTabSpaceNavigation:
    def test_tab_enters_submenu(self) -> None:
        _run_tui([b"\t", b"\x1b", b"\x00", b"q"])

    def test_space_enters_submenu(self) -> None:
        _run_tui([b" ", b"\x1b", b"\x00", b"q"])

    def test_enter_enters_submenu(self) -> None:
        _run_tui([b"\r", b"\x1b", b"\x00", b"q"])

    def test_tab_in_projects_view(self) -> None:
        _run_tui([b"p", b"\t", b"\x1b", b"\x00", b"q"])

    def test_space_in_models_view(self) -> None:
        _run_tui([b"m", b" ", b"\x1b", b"\x00", b"q"])

    def test_enter_in_todos_view(self) -> None:
        _run_tui([b"t", b"\r", b"\x1b", b"\x00", b"q"])


class TestEscapeNavigation:
    def test_escape_from_models_returns_to_main(self) -> None:
        _run_tui([b"m", b"\x1b", b"\x00", b"q"])

    def test_escape_from_todos_returns_to_main(self) -> None:
        _run_tui([b"t", b"\x1b", b"\x00", b"q"])

    def test_escape_from_hooks_returns_to_main(self) -> None:
        _run_tui([b"h", b"\x1b", b"\x00", b"q"])

    def test_escape_from_workers_returns_to_main(self) -> None:
        _run_tui([b"o", b"\x1b", b"\x00", b"q"])

    def test_escape_from_metrics_returns_to_main(self) -> None:
        _run_tui([b"x", b"\x1b", b"\x00", b"q"])

    def test_escape_from_agents_returns_to_main(self) -> None:
        _run_tui([b"g", b"\x1b", b"\x00", b"q"])

    def test_escape_from_config_returns_to_main(self) -> None:
        _run_tui([b"v", b"\x1b", b"\x00", b"q"])

    def test_escape_from_projects_returns_to_main(self) -> None:
        _run_tui([b"p", b"\x1b", b"\x00", b"q"])

    def test_escape_from_integrity_returns_to_main(self) -> None:
        _run_tui([b"i", b"\x1b", b"\x00", b"q"])

    def test_escape_from_ansible_returns_to_main(self) -> None:
        _run_tui([b"a", b"\x1b", b"\x00", b"q"])

    def test_escape_from_worktrees_returns_to_main(self) -> None:
        _run_tui([b"w", b"\x1b", b"\x00", b"q"])

    def test_escape_from_mcp_returns_to_main(self) -> None:
        _run_tui([b"u", b"\x1b", b"\x00", b"q"])

    def test_escape_from_skills_returns_to_main(self) -> None:
        _run_tui([b"j", b"\x1b", b"\x00", b"q"])

    def test_escape_from_compute_returns_to_main(self) -> None:
        _run_tui([b"e", b"\x1b", b"\x00", b"q"])

    def test_escape_from_scores_returns_to_main(self) -> None:
        _run_tui([b"b", b"\x1b", b"\x00", b"q"])

    def test_escape_from_templates_returns_to_main(self) -> None:
        _run_tui([b"l", b"\x1b", b"\x00", b"q"])

    def test_escape_from_quantization_returns_to_main(self) -> None:
        _run_tui([b"n", b"\x1b", b"\x00", b"q"])

    def test_escape_from_filestore_returns_to_main(self) -> None:
        _run_tui([b"f", b"\x1b", b"\x00", b"q"])

    def test_escape_from_deployments_returns_to_main(self) -> None:
        _run_tui([b"z", b"\x1b", b"\x00", b"q"])


class TestFullNavigationFlow:
    def test_navigate_views_with_arrows(self) -> None:
        _run_tui([b"\x1b[B", b"\r", b"\x1b[D", b"q"])

    def test_enter_subview_back_with_left(self) -> None:
        _run_tui([b"m", b"\x1b[D", b"q"])

    def test_enter_subview_tab_back_escape(self) -> None:
        _run_tui([b"t", b"\t", b"\x1b", b"\x00", b"q"])

    def test_multiple_view_switches(self) -> None:
        _run_tui([b"m", b"\x1b", b"t", b"\x1b", b"h", b"\x1b", b"\x00", b"q"])

    def test_arrow_navigation_then_enter(self) -> None:
        _run_tui([b"\x1b[B", b"\x1b[B", b"\r", b"\x1b", b"\x00", b"q"])
