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

        # \x03 tail = unconditional break in the runner loop, so a key sequence
        # that never reaches a quit path can't infinite-loop (the old [b""] tail
        # was ignored by `if ch:` and hung on headless Linux/CI). See the matching
        # note in test_tui_navigation_e2e.py.
        stack.enter_context(
            patch("os.read", side_effect=os_read_keys + [b"\x03"] * 20))
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

    @pytest.mark.parametrize("key", ["P", "R", "L", "H", "T", "D", "C"])
    def test_uppercase_commands_preserve_case_for_dispatch(self, key: str) -> None:
        dispatched: list[str] = []

        def capture_key(_handler: object, ch: str) -> bool:
            dispatched.append(ch)
            return True

        _run_tui(
            [key.encode(), b"q"],
            [
                patch(
                    "general_ludd.tui.runner.TUIKeyHandler.handle_key",
                    autospec=True,
                    side_effect=capture_key,
                ),
            ],
        )

        assert dispatched == [key]

    @pytest.mark.parametrize(
        ("key", "builder_name", "expected"),
        [
            ("y", "_build_leaderboard_table", [{"model": "gludd-test"}]),
            ("P", "_build_playbooks_table", [{"name": "deploy"}]),
            ("L", "_build_slurm_table", [{"job_id": "job-1"}]),
            (
                "H",
                "_build_health_table",
                {
                    "leaderboard": [{"model": "gludd-test"}],
                    "playbooks": [{"name": "deploy"}],
                    "jobs": [{"job_id": "job-1"}],
                    "profiles": [{"name": "local-model"}],
                    "status": "ok",
                },
            ),
            ("T", "_build_selftest_table", {}),
            (
                "0",
                "_build_version_table",
                {"version": "?", "python_version": "?", "platform": "?"},
            ),
            ("1", "_build_loglevel_table", "info"),
            ("D", "_build_discovered_table", [{"name": "local-model"}]),
            ("C", "_build_code_table", []),
        ],
    )
    def test_extended_view_render_uses_runtime_data(
        self,
        key: str,
        builder_name: str,
        expected: object,
    ) -> None:
        payload = {
            "leaderboard": [{"model": "gludd-test"}],
            "playbooks": [{"name": "deploy"}],
            "jobs": [{"job_id": "job-1"}],
            "profiles": [{"name": "local-model"}],
            "status": "ok",
        }
        response = MagicMock(status_code=200)
        response.json.return_value = payload
        builder = MagicMock(return_value=MagicMock())

        _run_tui(
            [key.encode(), b"q"],
            [
                patch(f"general_ludd.cli.{builder_name}", builder),
                patch("httpx.get", return_value=response),
            ],
        )

        assert builder.call_args.args[0] == expected

    @pytest.mark.parametrize(
        ("key", "builder_name", "expected"),
        [
            ("t", "_build_todos_table", [{"todo_id": "todo-1"}]),
            ("h", "_build_hooks_table", [{"hook_id": "hook-1"}]),
            ("o", "_build_workers_table", [{"worker_id": "worker-1"}]),
            (
                "x",
                "_build_metrics_table",
                {
                    "projects": [{"project_id": "project-1"}],
                    "todos": [{"todo_id": "todo-1"}],
                    "hooks": [{"hook_id": "hook-1"}],
                    "workers": [{"worker_id": "worker-1"}],
                    "agents": [{"agent_id": "agent-1"}],
                    "servers": [{"name": "mcp-1"}],
                    "skills": [{"name": "skill-1"}],
                    "endpoints": [{"name": "gpu-1"}],
                    "scores": [{"score": 1.0}],
                    "templates": [{"name": "template-1"}],
                    "entries": [{"name": "quant-1"}],
                    "files": [{"path": "artifact.bin"}],
                    "deployments": [{"deployment_id": "deploy-1"}],
                },
            ),
            ("g", "_build_agents_table", [{"agent_id": "agent-1"}]),
            ("u", "_build_mcp_table", [{"name": "mcp-1"}]),
            ("j", "_build_skills_table", [{"name": "skill-1"}]),
            ("e", "_build_compute_table", [{"name": "gpu-1"}]),
            ("b", "_build_scores_table", [{"score": 1.0}]),
            ("l", "_build_templates_table", [{"name": "template-1"}]),
            ("n", "_build_quantization_table", [{"name": "quant-1"}]),
            ("f", "_build_filestore_table", [{"path": "artifact.bin"}]),
            ("z", "_build_deployments_table", [{"deployment_id": "deploy-1"}]),
        ],
    )
    def test_network_view_render_uses_success_payload(
        self,
        key: str,
        builder_name: str,
        expected: object,
    ) -> None:
        payload = {
            "projects": [{"project_id": "project-1"}],
            "todos": [{"todo_id": "todo-1"}],
            "hooks": [{"hook_id": "hook-1"}],
            "workers": [{"worker_id": "worker-1"}],
            "agents": [{"agent_id": "agent-1"}],
            "servers": [{"name": "mcp-1"}],
            "skills": [{"name": "skill-1"}],
            "endpoints": [{"name": "gpu-1"}],
            "scores": [{"score": 1.0}],
            "templates": [{"name": "template-1"}],
            "entries": [{"name": "quant-1"}],
            "files": [{"path": "artifact.bin"}],
            "deployments": [{"deployment_id": "deploy-1"}],
        }
        response = MagicMock(status_code=200)
        response.json.return_value = payload
        builder = MagicMock(return_value=MagicMock())

        _run_tui(
            [key.encode(), b"q"],
            [
                patch(f"general_ludd.cli.{builder_name}", builder),
                patch("httpx.get", return_value=response),
            ],
        )

        assert builder.call_args.args[0] == expected

    def test_project_add_dispatches_json_to_daemon(self) -> None:
        get_response = MagicMock(status_code=200)
        get_response.json.return_value = {"projects": []}
        post_response = MagicMock(status_code=200)
        post_response.json.return_value = {"project_id": "project-new"}
        post = MagicMock(return_value=post_response)
        builder = MagicMock(return_value=MagicMock())

        def enter_projects(handler: object, _ch: str) -> bool:
            handler._state["current_view"] = "projects"  # type: ignore[attr-defined]
            return True

        _run_tui(
            [b"u", b"a", b"q"],
            [
                patch(
                    "general_ludd.tui.runner.TUIKeyHandler.handle_key",
                    autospec=True,
                    side_effect=enter_projects,
                ),
                patch("httpx.get", return_value=get_response),
                patch("httpx.post", post),
                patch("general_ludd.cli._build_projects_table", builder),
            ],
        )

        project_calls = [
            call for call in post.call_args_list
            if call.args[0].endswith("/admin/projects")
        ]
        assert len(project_calls) == 1
        assert '"name": "new-project"' in project_calls[0].kwargs["content"]
        assert builder.call_args.args[0] == []

    def test_project_delete_dispatches_selected_project(self) -> None:
        get_response = MagicMock(status_code=200)
        get_response.json.return_value = {
            "projects": [{"project_id": "project-old"}],
        }
        delete_response = MagicMock(status_code=200)
        delete = MagicMock(return_value=delete_response)
        builder = MagicMock(return_value=MagicMock())

        def enter_projects(handler: object, _ch: str) -> bool:
            handler._state["current_view"] = "projects"  # type: ignore[attr-defined]
            return True

        _run_tui(
            [b"u", b"d", b"q"],
            [
                patch(
                    "general_ludd.tui.runner.TUIKeyHandler.handle_key",
                    autospec=True,
                    side_effect=enter_projects,
                ),
                patch("httpx.get", return_value=get_response),
                patch("httpx.delete", delete),
                patch("general_ludd.cli._build_projects_table", builder),
            ],
        )

        assert delete.call_args.args[0].endswith("/admin/projects/project-old")
        assert builder.call_args.args[0] == [{"project_id": "project-old"}]

    def test_mouse_drag_sequence_is_parsed_without_terminal_side_effects(self) -> None:
        press = bytes((32, 72, 40))
        release = bytes((35, 72, 40))

        _run_tui([b"\x1b", b"[M", press, b"\x1b", b"[M", release, b"q"])

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
        _run_tui([b"v", b"\x1b", b"q"])

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
