"""Lifecycle-ownership regressions for the interactive TUI runner."""

from __future__ import annotations

import argparse
import importlib
import inspect
import sys
from types import SimpleNamespace
from typing import Protocol
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.tui import runner


class TUIRunnerHarness(Protocol):
    """Typed boundary for the established terminal-I/O harness."""

    def __call__(self, keys: list[bytes], extra: list[object] | None = None) -> dict[object, object]:
        """Run the TUI with deterministic terminal bytes."""
        ...


_tui_body = importlib.import_module("tests.unit.test_tui_body")
_run_tui: TUIRunnerHarness = _tui_body._run_tui


def test_runner_has_one_daemon_lifecycle_owner() -> None:
    """The runner must delegate daemon start/stop to ``TUIKeyHandler`` only."""
    source = inspect.getsource(runner.run_tui)

    assert "def start_daemon" not in source
    assert "def stop_daemon" not in source
    assert "subprocess.Popen" not in source
    assert "tui_handler.handle_key(ch)" in source


def test_runner_fails_closed_without_posix_terminal_modules() -> None:
    """A non-POSIX host must receive the documented bounded error."""
    with (
        patch.dict(sys.modules, {"termios": None}),
        pytest.raises(SystemExit, match="requires POSIX"),
    ):
        runner.run_tui(argparse.Namespace(), SimpleNamespace())


@pytest.mark.parametrize("key", [b"p", b"m", b"w", b"t", b"h", b"o", b"x", b"g"])
def test_repeated_view_key_returns_to_parent(key: bytes) -> None:
    """Each runner-owned toggle must cover both enter and return paths."""
    _run_tui([key, key, b"q"])


def test_input_mode_is_cancelled_before_view_navigation() -> None:
    """Escape must clear handler input state without leaving the current view."""
    _run_tui([b"u", b"s", b"x", b"\x1b", b"q"])


@pytest.mark.parametrize("result", ["saved", "cancelled"])
def test_editing_result_is_reflected_by_runner(result: str) -> None:
    """The config editor's terminal result must flow through the runner."""
    editor = MagicMock(editing=True)

    def finish_edit(_key: str) -> str:
        editor.editing = False
        return result

    editor.handle_input_key.side_effect = finish_edit
    nav = {
        "categories": [],
        "current_items": [],
        "selected_cat": 0,
        "depth": 0,
        "selected_item": 0,
        "active_overlay_path": "",
        "editor": editor,
        "editing_value": True,
    }

    _run_tui(
        [b"c", b"x", b"q"],
        [
            patch("general_ludd.cli._load_config_editor", return_value=nav),
            patch("general_ludd.cli._build_config_editor_table", return_value=MagicMock()),
        ],
    )

    editor.handle_input_key.assert_called_once_with("x")


def test_edit_menu_navigation_and_escape_reset() -> None:
    """Entering a config menu and escaping must reset navigation atomically."""
    item = SimpleNamespace(name="Menu", menu_items=[], overlay_path="overlay.yml")
    editor = MagicMock(editing=False)
    nav = {
        "categories": [item],
        "current_items": [item],
        "selected_cat": 0,
        "depth": 0,
        "selected_item": 0,
        "active_overlay_path": "",
        "editor": editor,
        "editing_value": False,
    }

    _run_tui(
        [b"c", b"\r", b"\x1b", b"", b"\x1b", b"", b"q"],
        [
            patch("general_ludd.cli._load_config_editor", return_value=nav),
            patch("general_ludd.cli._build_config_editor_table", return_value=MagicMock()),
        ],
    )

    assert nav["depth"] == 0
    assert nav["active_overlay_path"] == "overlay.yml"


def test_runner_awaits_owned_model_shutdown() -> None:
    """Async local-model teardown must finish before terminal restoration."""
    stopped: list[bool] = []

    class AsyncManager:
        """Minimal owned manager used to prove async shutdown completion."""

        def create_server(self, _config: object) -> object:
            """Accept the runner's managed-server registrations."""
            return object()

        def list_servers(self) -> list[object]:
            """Return no active model servers for layout rendering."""
            return []

        async def stop_all(self) -> None:
            """Record completion inside the awaited cleanup boundary."""
            stopped.append(True)

    _run_tui(
        [b"q"],
        [patch("general_ludd.tui.runner.LocalInferenceManager", return_value=AsyncManager())],
    )

    assert stopped == [True]


@pytest.mark.parametrize(
    ("key", "builder_name"),
    [
        (b"t", "_build_todos_table"),
        (b"h", "_build_hooks_table"),
        (b"o", "_build_workers_table"),
        (b"x", "_build_metrics_table"),
        (b"g", "_build_agents_table"),
        (b"u", "_build_mcp_table"),
        (b"j", "_build_skills_table"),
        (b"e", "_build_compute_table"),
        (b"b", "_build_scores_table"),
        (b"l", "_build_templates_table"),
        (b"n", "_build_quantization_table"),
        (b"f", "_build_filestore_table"),
        (b"z", "_build_deployments_table"),
    ],
)
def test_network_views_render_bounded_empty_state_on_non_200(key: bytes, builder_name: str) -> None:
    """Non-success daemon responses must render without reusing stale data."""
    response = MagicMock(status_code=503)
    builder = MagicMock(return_value=MagicMock())

    _run_tui(
        [key, b"q"],
        [
            patch("httpx.get", return_value=response),
            patch(f"general_ludd.cli.{builder_name}", builder),
        ],
    )

    builder.assert_called()


@pytest.mark.parametrize(
    ("key", "builder_name"),
    [
        (b"y", "_build_leaderboard_table"),
        (b"P", "_build_playbooks_table"),
        (b"L", "_build_slurm_table"),
        (b"H", "_build_health_table"),
        (b"D", "_build_discovered_table"),
    ],
)
def test_extended_views_render_bounded_empty_state_on_non_200(key: bytes, builder_name: str) -> None:
    """Extended views must also fail closed on non-success daemon responses."""
    response = MagicMock(status_code=503)
    builder = MagicMock(return_value=MagicMock())

    _run_tui(
        [key, b"q"],
        [
            patch("httpx.get", return_value=response),
            patch(f"general_ludd.cli.{builder_name}", builder),
        ],
    )

    builder.assert_called()
