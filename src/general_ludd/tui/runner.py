"""TUI runner extracted from cli._cmd_tui."""

from __future__ import annotations

import argparse
import contextlib
import os
import select
import subprocess
import sys
import termios
import time
import tty
from types import SimpleNamespace
from typing import Any

from general_ludd.infra.local_inference import LocalInferenceManager, LocalServerConfig
from general_ludd.models.model_registry import ModelRegistry
from general_ludd.tui.breadcrumb import pop_breadcrumb, push_breadcrumb, render_breadcrumb
from general_ludd.tui.keybindings import TUIKeyHandler
from general_ludd.tui.logger import TUILogger


def run_tui(args: argparse.Namespace, h: SimpleNamespace) -> None:
    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel

    daemon_proc: subprocess.Popen[bytes] | None = None
    daemon_running = False
    current_view = "main"
    status_msg = "Press q to quit, s to start daemon"
    tui_state: dict[str, Any] = {
        "current_view": "main",
        "daemon_running": False,
        "status_msg": "",
        "daemon_url": args.daemon_url,
        "input_mode": None,
        "input_buffer": "",
        "input_field_index": 0,
        "input_fields": [],
        "dispatch_mode": "active",
        "ansible_search_results": [],
        "verbose_logging": False,
    }
    tui_handler = TUIKeyHandler(tui_state)

    _tui_log_dir = os.path.join(h._get_daemon_pid_dir(), "tui_logs")
    tui_logger = TUILogger(log_dir=_tui_log_dir, daemon_url=args.daemon_url, verbose=False)

    def detect_daemon() -> bool:
        if h._is_daemon_pid_alive(h._DAEMON_PID_FILE):
            return True
        try:
            import httpx
            resp = httpx.get(f"{args.daemon_url}/healthz", timeout=1.0)
            return resp.status_code == 200
        except Exception:
            return False

    daemon_running = detect_daemon()
    if daemon_running:
        pid_data = h._read_daemon_pid_file(h._DAEMON_PID_FILE)
        if pid_data:
            args.daemon_url = pid_data.get("daemon_url", args.daemon_url)
    config_nav = h._load_config_editor()

    model_mgr = LocalInferenceManager()
    model_mgr.create_server(LocalServerConfig(engine="llamacpp", model_path="/models/llama-7b.gguf", port=8081))
    model_mgr.create_server(LocalServerConfig(engine="vllm", model_name="meta-llama/Llama-3.2-1B", port=8000))
    model_registry = ModelRegistry()
    downloaded_models = model_registry.list_downloaded()

    def start_daemon() -> None:
        nonlocal daemon_proc, daemon_running, status_msg
        if daemon_running or detect_daemon():
            status_msg = "Daemon already running"
            daemon_running = True
            return
        try:
            cmd = h._build_daemon_start_cmd(
                host=getattr(args, "host", "0.0.0.0"),
                port=getattr(args, "port", 8000),
                workers=getattr(args, "workers", 1),
            )
            daemon_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
                close_fds=True,
            )
            alive = False
            for _ in range(20):
                time.sleep(0.25)
                if daemon_proc.poll() is not None:
                    stderr_out = daemon_proc.stderr.read().decode(errors="replace") if daemon_proc.stderr else ""
                    status_msg = f"Daemon exited (rc={daemon_proc.returncode}): {stderr_out[:200]}"
                    daemon_proc = None
                    return
                try:
                    import httpx
                    resp = httpx.get(f"{args.daemon_url}/healthz", timeout=1.0)
                    if resp.status_code == 200:
                        alive = True
                        break
                except Exception:
                    pass
            if not alive and daemon_proc.poll() is not None:
                status_msg = "Daemon failed to start"
                daemon_proc = None
                return
            h._get_daemon_pid_dir()
            h._write_daemon_pid_file(h._DAEMON_PID_FILE, daemon_proc.pid, args.daemon_url)
            daemon_running = True
            status_msg = f"Daemon started PID={daemon_proc.pid}"
        except Exception as exc:
            status_msg = f"Start failed: {exc}"

    def stop_daemon() -> None:
        nonlocal daemon_proc, daemon_running, status_msg
        if daemon_proc is not None:
            daemon_proc.terminate()
            try:
                daemon_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                daemon_proc.kill()
            daemon_proc = None
            daemon_running = False
            with contextlib.suppress(OSError):
                os.unlink(h._DAEMON_PID_FILE)
            status_msg = "Daemon stopped"
        elif h._is_daemon_pid_alive(h._DAEMON_PID_FILE):
            if h._stop_daemon_via_pid_file(h._DAEMON_PID_FILE):
                daemon_running = False
                status_msg = "Daemon stopped via PID"
            else:
                status_msg = "Failed to stop daemon"
        elif daemon_running:
            daemon_running = False
            status_msg = "Daemon status cleared (not running)"
        else:
            status_msg = "No daemon to stop"

    def build_controls_table() -> Any:
        import shutil as _shutil2
        _tw, _ = _shutil2.get_terminal_size((80, 24))
        sel_idx = tui_state.get("selected_main_idx", -1) if current_view == "main" else -1
        return h._build_controls_table(daemon_running, status_msg, term_width=_tw, selected_idx=sel_idx)

    def build_daemon_table(*, term_width: int = 80) -> Any:
        return h._build_daemon_table(daemon_running, args.daemon_url, current_view, term_width=term_width)

    def build_info_table(info: dict[str, Any], *, term_width: int = 80) -> Any:
        return h._build_info_table(info, term_width=term_width)

    def build_binary_table(info: dict[str, Any], *, term_width: int = 80) -> Any:
        return h._build_binary_table(info, term_width=term_width)

    def build_config_table(info: dict[str, Any], *, term_width: int = 80) -> Any:
        return h._build_config_table(info, term_width=term_width)

    def make_layout(info: dict[str, Any]) -> Layout:
        import shutil as _shutil

        _term_w, _term_h = _shutil.get_terminal_size((80, 24))
        footer_rows = h._compute_footer_rows(_term_h)
        header_rows = 1
        left_width, right_width = h._compute_panel_widths(_term_w, tui_state)

        layout = Layout()
        layout.split(
            Layout(name="header", size=header_rows),
            Layout(name="body"),
            Layout(name="footer", size=footer_rows),
        )
        layout["body"].split_row(
            Layout(name="left", size=left_width),
            Layout(name="right", size=right_width),
        )
        body = layout["body"]
        if current_view == "edit":
            body.split_row(
                Layout(name="left", size=left_width),
                Layout(name="right", size=right_width),
            )
            items = config_nav["current_items"]
            sel = config_nav["selected_cat"]
            depth = config_nav["depth"]
            dict_items = []
            if depth == 0:
                for cat in items:
                    dict_items.append({"label": cat.name, "value": "", "help_text": ""})
            else:
                for item in items:
                    dict_items.append({"label": item.label, "value": str(item.value), "help_text": item.help_text})
            _editor_table = h._build_config_editor_table(dict_items, sel, depth, term_width=right_width)
            body["left"].split(
                Layout(h._wrap_table(build_daemon_table(term_width=left_width)), name="daemon"),
            )
            body["right"].split(
                Layout(h._wrap_table(_editor_table), name="editor"),
            )
        else:
            body["left"].split(
                Layout(h._wrap_table(build_daemon_table(term_width=left_width)), name="daemon"),
                Layout(h._wrap_table(build_binary_table(info, term_width=left_width)), name="binaries"),
            )
            if current_view == "config":
                body["right"].split(
                    Layout(h._wrap_table(build_config_table(info, term_width=right_width)), name="config"),
                )
            elif current_view == "models":
                servers = model_mgr.list_servers()
                _model_table = h._build_model_table(
                    servers, downloaded_models,
                    selected_idx=tui_state.get("selected_model_idx", 0),
                    term_width=right_width,
                )
                body["right"].split(
                    Layout(h._wrap_table(_model_table), name="models"),
                )
            elif current_view == "worktrees":
                import os as _os
                home = _os.path.expanduser("~")
                wt_dirs = [
                    d for d in _os.listdir(home)
                    if _os.path.isdir(_os.path.join(home, d)) and not d.startswith(".")
                ]
                wt_entries = []
                for d in sorted(wt_dirs)[:15]:
                    full = _os.path.join(home, d)
                    agents_path = _os.path.join(full, "AGENTS.md")
                    is_worktree = _os.path.isfile(agents_path)
                    status = "has AGENTS.md" if is_worktree else "directory"
                    wt_entries.append((d, status))
                _wt_table = h._build_worktrees_table(wt_entries, term_width=right_width)
                body["right"].split(
                    Layout(h._wrap_table(_wt_table), name="worktrees"),
                )
            elif current_view == "projects":
                _proj_data: list[dict[str, Any]] = []
                try:
                    import httpx
                    resp = httpx.get(f"{args.daemon_url}/admin/projects", timeout=3.0)
                    if resp.status_code == 200:
                        _proj_data = resp.json().get("projects", [])
                except Exception:
                    _proj_data = [
                        {
                            "project_id": "?",
                            "name": "Daemon not running",
                            "weight": 0,
                            "dispatch_mode": "Start [s]",
                        }
                    ]
                tui_state["projects_data"] = _proj_data
                _proj_sel = tui_state.get("selected_project_idx", 0)
                _proj_table = h._build_projects_table(_proj_data, selected_idx=_proj_sel, term_width=right_width)
                body["right"].split(
                    Layout(h._wrap_table(_proj_table), name="projects"),
                )
            elif current_view == "todos":
                _todos_data: list[dict[str, Any]] = []
                try:
                    import httpx
                    resp = httpx.get(f"{args.daemon_url}/admin/todos", timeout=3.0)
                    if resp.status_code == 200:
                        _todos_data = resp.json().get("todos", [])
                except Exception:
                    pass
                tui_state["todos_data"] = _todos_data
                _todos_sel = tui_state.get("selected_todo_idx", 0)
                body["right"].split(
                    Layout(h._wrap_table(h._build_todos_table(_todos_data, selected_idx=_todos_sel)), name="todos"),
                )
            elif current_view == "hooks":
                _hooks_data: list[dict[str, Any]] = []
                try:
                    import httpx
                    resp = httpx.get(f"{args.daemon_url}/admin/hooks", timeout=3.0)
                    if resp.status_code == 200:
                        _hooks_data = resp.json().get("hooks", [])
                except Exception:
                    pass
                tui_state["hooks_data"] = _hooks_data
                _hooks_sel = tui_state.get("selected_hook_idx", 0)
                body["right"].split(
                    Layout(h._wrap_table(h._build_hooks_table(_hooks_data, selected_idx=_hooks_sel)), name="hooks"),
                )
            elif current_view == "workers":
                _workers_data: list[dict[str, Any]] = []
                try:
                    import httpx
                    resp = httpx.get(f"{args.daemon_url}/admin/workers", timeout=3.0)
                    if resp.status_code == 200:
                        _workers_data = resp.json().get("workers", [])
                except Exception:
                    pass
                tui_state["workers_data"] = _workers_data
                _workers_sel = tui_state.get("selected_worker_idx", 0)
                _wt = h._build_workers_table(
                    _workers_data, selected_idx=_workers_sel,
                )
                body["right"].split(
                    Layout(h._wrap_table(_wt), name="workers"),
                )
            elif current_view == "metrics":
                _cost_data: dict[str, Any] = {}
                try:
                    import httpx
                    resp = httpx.get(f"{args.daemon_url}/admin/metrics/cost", timeout=3.0)
                    if resp.status_code == 200:
                        _cost_data = resp.json()
                except Exception:
                    pass
                body["right"].split(
                    Layout(h._wrap_table(h._build_metrics_table(_cost_data)), name="metrics"),
                )
            elif current_view == "agents":
                _agents_data: list[dict[str, Any]] = []
                try:
                    import httpx
                    resp = httpx.get(f"{args.daemon_url}/admin/agents", timeout=3.0)
                    if resp.status_code == 200:
                        _agents_data = resp.json().get("agents", [])
                except Exception:
                    pass
                body["right"].split(
                    Layout(h._wrap_table(h._build_agents_table(_agents_data)), name="agents"),
                )
            elif current_view == "integrity":
                _int_changes: list[dict[str, Any]] = []
                try:
                    from general_ludd.integrity.scanner import FileIntegrityScanner as _FIS
                    _scanner = _FIS()
                    _paths = [info.get("config_dir", ""), info.get("filestore_root", "")]
                    _paths = [p for p in _paths if p]
                    _iresult: dict[str, Any] = _scanner.scan(_paths) if _paths else {"scanned": 0, "changes": []}
                    _int_changes = _iresult.get("changes", [])
                except Exception:
                    _int_changes = [{"file": "Scan failed", "type": "error", "approved": False}]
                _int_table = h._build_integrity_table(_int_changes, term_width=right_width)
                body["right"].split(
                    Layout(h._wrap_table(_int_table), name="integrity"),
                )
            elif current_view == "ansible":
                _ans_results = tui_state.get("ansible_search_results", [])
                _ans_table = h._build_ansible_table(_ans_results, term_width=right_width)
                body["right"].split(
                    Layout(h._wrap_table(_ans_table), name="ansible"),
                )
            elif current_view == "mcp":
                _mcp_data: list[dict[str, Any]] = []
                try:
                    import httpx
                    resp = httpx.get(f"{args.daemon_url}/admin/mcp/list", timeout=3.0)
                    if resp.status_code == 200:
                        _mcp_data = resp.json().get("servers", resp.json().get("results", []))
                except Exception:
                    pass
                body["right"].split(
                    Layout(h._wrap_table(h._build_mcp_table(_mcp_data, term_width=right_width)), name="mcp"),
                )
            elif current_view == "skills":
                _skills_data: list[dict[str, Any]] = []
                try:
                    import httpx
                    resp = httpx.get(f"{args.daemon_url}/admin/skills/catalog", timeout=3.0)
                    if resp.status_code == 200:
                        _skills_data = resp.json().get("skills", resp.json().get("results", []))
                except Exception:
                    pass
                body["right"].split(
                    Layout(h._wrap_table(h._build_skills_table(_skills_data, term_width=right_width)), name="skills"),
                )
            elif current_view == "compute":
                _compute_data: list[dict[str, Any]] = []
                try:
                    import httpx
                    resp = httpx.get(f"{args.daemon_url}/admin/compute/endpoints", timeout=3.0)
                    if resp.status_code == 200:
                        _compute_data = resp.json().get("endpoints", [])
                except Exception:
                    pass
                body["right"].split(
                    Layout(
                        h._wrap_table(h._build_compute_table(
                            _compute_data, term_width=right_width,
                        )),
                        name="compute",
                    ),
                )
            elif current_view == "scores":
                _scores_data: list[dict[str, Any]] = []
                try:
                    import httpx
                    resp = httpx.get(f"{args.daemon_url}/admin/benchmark/scores", timeout=3.0)
                    if resp.status_code == 200:
                        _scores_data = resp.json().get("scores", resp.json().get("results", []))
                except Exception:
                    pass
                body["right"].split(
                    Layout(h._wrap_table(h._build_scores_table(_scores_data, term_width=right_width)), name="scores"),
                )
            elif current_view == "templates":
                _templates_data: list[dict[str, Any]] = []
                try:
                    import httpx
                    resp = httpx.get(f"{args.daemon_url}/admin/templates", timeout=3.0)
                    if resp.status_code == 200:
                        _templates_data = resp.json().get("templates", resp.json().get("profiles", []))
                except Exception:
                    pass
                body["right"].split(
                    Layout(
                        h._wrap_table(h._build_templates_table(
                            _templates_data, term_width=right_width,
                        )),
                        name="templates",
                    ),
                )
            elif current_view == "quantization":
                _quant_data: list[dict[str, Any]] = []
                try:
                    import httpx
                    resp = httpx.get(f"{args.daemon_url}/admin/quantization", timeout=3.0)
                    if resp.status_code == 200:
                        _quant_data = resp.json().get("entries", resp.json().get("results", []))
                except Exception:
                    pass
                body["right"].split(
                    Layout(
                        h._wrap_table(h._build_quantization_table(
                            _quant_data, term_width=right_width,
                        )),
                        name="quantization",
                    ),
                )
            elif current_view == "filestore":
                _fs_data: list[dict[str, Any]] = []
                try:
                    import httpx
                    resp = httpx.get(f"{args.daemon_url}/admin/filestore/list", timeout=3.0)
                    if resp.status_code == 200:
                        _fs_data = resp.json().get("files", resp.json().get("entries", []))
                except Exception:
                    pass
                body["right"].split(
                    Layout(h._wrap_table(h._build_filestore_table(_fs_data, term_width=right_width)), name="filestore"),
                )
            elif current_view == "deployments":
                _deploy_data: list[dict[str, Any]] = []
                try:
                    import httpx
                    resp = httpx.get(f"{args.daemon_url}/admin/deployments", timeout=3.0)
                    if resp.status_code == 200:
                        _deploy_data = resp.json().get("deployments", [])
                except Exception:
                    pass
                body["right"].split(
                    Layout(
                        h._wrap_table(h._build_deployments_table(
                            _deploy_data, term_width=right_width,
                        )),
                        name="deployments",
                    ),
                )
            elif current_view == "leaderboard":
                _lb_data: list[dict[str, Any]] = []
                try:
                    import httpx
                    resp = httpx.get(f"{args.daemon_url}/admin/benchmark/leaderboard", timeout=3.0)
                    if resp.status_code == 200:
                        _lb_data = resp.json().get("leaderboard", resp.json().get("entries", []))
                except Exception:
                    pass
                body["right"].split(
                    Layout(
                        h._wrap_table(h._build_leaderboard_table(
                            _lb_data, term_width=right_width,
                        )),
                        name="leaderboard",
                    ),
                )
            elif current_view == "playbooks":
                _pb_data: list[dict[str, Any]] = []
                try:
                    import httpx
                    resp = httpx.get(f"{args.daemon_url}/admin/playbooks", timeout=3.0)
                    if resp.status_code == 200:
                        _pb_data = resp.json().get("playbooks", resp.json().get("entries", []))
                except Exception:
                    pass
                body["right"].split(
                    Layout(h._wrap_table(h._build_playbooks_table(_pb_data, term_width=right_width)), name="playbooks"),
                )
            elif current_view == "slurm":
                _slurm_data: list[dict[str, Any]] = []
                try:
                    import httpx
                    resp = httpx.get(f"{args.daemon_url}/admin/slurm/jobs", timeout=3.0)
                    if resp.status_code == 200:
                        _slurm_data = resp.json().get("jobs", [])
                except Exception:
                    pass
                body["right"].split(
                    Layout(h._wrap_table(h._build_slurm_table(_slurm_data, term_width=right_width)), name="slurm"),
                )
            else:
                body["right"].split(
                    Layout(h._wrap_table(build_info_table(info, term_width=right_width)), name="info"),
                )
        if current_view == "edit":
            header_text = "Config Editor \u2014 [c] exit  [q] quit"
        elif current_view == "models":
            if tui_state.get("input_mode") == "models_add":
                _idx = tui_state.get("input_field_index", 0)
                _fields = tui_state.get("input_fields", [])
                _label = _fields[_idx]["label"] if _idx < len(_fields) else "?"
                header_text = (
                    f"Add Model \u2014 enter {_label}: "
                    f"{tui_state.get('input_buffer', '')}_ "
                    "\u2014 [Enter] next [Esc] cancel"
                )
            else:
                header_text = "Model Services \u2014 [m] exit  [a]dd  [q] quit"
        elif current_view == "worktrees":
            header_text = "Projects & Worktrees \u2014 [w] exit  [q] quit"
        elif current_view == "projects":
            header_text = "Registered Projects \u2014 [p] exit  [a]dd  [d]elete  [q] quit"
        elif current_view == "todos":
            header_text = "Todos \u2014 [t] exit  [q] quit"
        elif current_view == "hooks":
            header_text = "Hooks \u2014 [h] exit  [q] quit"
        elif current_view == "workers":
            header_text = "Workers \u2014 [o] exit  [q] quit"
        elif current_view == "metrics":
            header_text = "Metrics \u2014 [x] exit  [q] quit"
        elif current_view == "agents":
            header_text = "Agents \u2014 [g] exit  [q] quit"
        elif current_view == "integrity":
            header_text = "Integrity \u2014 [i] exit  [q] quit"
        elif current_view == "ansible":
            if tui_state.get("input_mode") == "ansible_search":
                header_text = f"Search Galaxy: {tui_state.get('input_buffer', '')}_ \u2014 [Enter] search [Esc] cancel"
            else:
                header_text = "Ansible Galaxy \u2014 [a] exit  [s]earch  [q] quit"
        elif current_view == "mcp":
            if tui_state.get("input_mode") == "mcp_search":
                header_text = f"Search MCP: {tui_state.get('input_buffer', '')}_ \u2014 [Enter] search [Esc] cancel"
            else:
                header_text = "MCP Servers \u2014 [u] exit  [s]earch  [q] quit"
        elif current_view == "skills":
            if tui_state.get("input_mode") == "skills_search":
                header_text = f"Search Skills: {tui_state.get('input_buffer', '')}_ \u2014 [Enter] search [Esc] cancel"
            else:
                header_text = "Skills \u2014 [j] exit  [s]earch  [i]nstall  [q] quit"
        elif current_view == "compute":
            if tui_state.get("input_mode") == "compute_register":
                header_text = f"Register: {tui_state.get('input_buffer', '')}_ \u2014 [Enter] next [Esc] cancel"
            else:
                header_text = "Compute \u2014 [e] exit  [a]dd  [q] quit"
        elif current_view == "scores":
            header_text = "Scores \u2014 [b] exit  [q] quit"
        elif current_view == "templates":
            header_text = "Templates \u2014 [l] exit  [r]efresh  [q] quit"
        elif current_view == "quantization":
            header_text = "Quantization \u2014 [n] exit  [d]etect  [q] quit"
        elif current_view == "filestore":
            header_text = "Filestore \u2014 [f] exit  [q] quit"
        elif current_view == "deployments":
            header_text = "Deployments \u2014 [z] exit  [q] quit"
        elif current_view == "leaderboard":
            header_text = "Leaderboard \u2014 [y] exit  [q] quit"
        elif current_view == "playbooks":
            header_text = "Playbooks \u2014 [r]efresh  [P] exit  [q] quit"
        elif current_view == "slurm":
            header_text = "Slurm \u2014 [L] exit  [q] quit"
        elif current_view == "config":
            header_text = "TUI | s:k:p:i:r:q | v:main c:edit"
        else:
            header_text = "TUI | s:k:r:i:c:v | a:d:m:w:p:t:h:o:x:g | u:j:e:b:l:n:f:z:y:P"
        _bc = render_breadcrumb(tui_state.get("breadcrumb", ["main"]))
        header_text = f"{_bc}  |  {status_msg}" if status_msg else _bc
        layout["header"].update(Panel(header_text, style="bold white on blue"))
        layout["footer"].update(build_controls_table())
        return layout

    def handle_key(info: dict[str, Any], ch: str) -> bool:
        nonlocal current_view, daemon_running, status_msg, config_nav, model_mgr
        if tui_state.get("input_mode") in (
            "models_add", "models_search", "ansible_search",
            "projects_add", "projects_set_weight",
            "mcp_search", "skills_search", "compute_register",
            "todos_add",
        ):
            tui_state["current_view"] = current_view
            tui_state["status_msg"] = status_msg
            tui_handler.handle_key(ch)
            current_view = tui_state["current_view"]
            status_msg = tui_state["status_msg"]
            return True
        if current_view == "edit":
            editor = config_nav["editor"]
            if editor.editing:
                edit_result = editor.handle_input_key(ch)
                config_nav["editing_value"] = editor.editing
                if edit_result == "saved":
                    status_msg = "Value saved"
                elif edit_result == "cancelled":
                    status_msg = "Edit cancelled"
                return True
            if ch in ("\t", " ", "\r", "\n"):
                ch = "\r"
            cats = config_nav["current_items"]
            if ch == "\x1b[A" and isinstance(cats, list) and len(cats) > 0:
                config_nav["selected_cat"] = max(0, config_nav["selected_cat"] - 1)
            elif ch == "\x1b[B" and isinstance(cats, list) and len(cats) > 0:
                config_nav["selected_cat"] = min(len(cats) - 1, config_nav["selected_cat"] + 1)
            elif ch == "\r":
                if isinstance(cats, list) and 0 <= config_nav["selected_cat"] < len(cats):
                    item = cats[config_nav["selected_cat"]]
                    if hasattr(item, "menu_items"):
                        config_nav["current_items"] = item.menu_items
                        config_nav["depth"] += 1
                        config_nav["selected_item"] = 0
                        config_nav["selected_cat"] = 0
                        if hasattr(item, "overlay_path") and item.overlay_path:
                            config_nav["active_overlay_path"] = item.overlay_path
                    elif hasattr(item, "is_menu") and item.is_menu:
                        config_nav["current_items"] = item.submenu
                        config_nav["depth"] += 1
                        config_nav["selected_item"] = 0
                        config_nav["selected_cat"] = 0
                    elif hasattr(item, "is_menu") and not item.is_menu:
                        editor.start_editing(item, config_nav["active_overlay_path"])
                        config_nav["editing_value"] = True
                        status_msg = f"Editing {item.label}"
            elif ch == "\x1b":
                if config_nav["depth"] > 0:
                    config_nav["depth"] = 0
                    config_nav["current_items"] = config_nav["categories"]
                    config_nav["selected_item"] = 0
                    config_nav["selected_cat"] = 0
                else:
                    current_view = "main"
                    status_msg = ""
            elif ch in ("c", "q"):
                current_view = "main"
                status_msg = ""
            return True
        if ch == "q":
            return False
        if ch in ("S", "K"):
            tui_state["current_view"] = current_view
            tui_state["status_msg"] = status_msg
            tui_state["daemon_running"] = daemon_running
            tui_handler.handle_key(ch)
            status_msg = tui_state["status_msg"]
            daemon_running = tui_state.get("daemon_running", daemon_running)
            return True
        if ch == "V":
            tui_state["current_view"] = current_view
            tui_state["status_msg"] = status_msg
            tui_handler.handle_key(ch)
            status_msg = tui_state["status_msg"]
            return True
        if ch == "\t":
            tui_state["current_view"] = current_view
            tui_handler.handle_key(ch)
            return True
        if len(ch) == 1:
            ch = ch.lower()
        if ch == "p":
            if current_view != "projects":
                current_view = "projects"
                push_breadcrumb(tui_state, "projects")
                status_msg = "Projects \u2014 [a]dd  [d]elete  [p] exit"
            else:
                current_view = pop_breadcrumb(tui_state)
                status_msg = ""
        elif ch == "i":
            if current_view != "integrity":
                current_view = "integrity"
                push_breadcrumb(tui_state, "integrity")
                try:
                    from general_ludd.integrity.scanner import FileIntegrityScanner
                    scanner = FileIntegrityScanner()
                    paths = [info.get("config_dir", ""), info.get("filestore_root", "")]
                    paths = [p for p in paths if p]
                    result: dict[str, Any] = scanner.scan(paths) if paths else {"scanned": 0, "changes": []}
                    changes: list[Any] = result["changes"]
                    status_msg = f"Integrity: {result['scanned']} scanned, {len(changes)} changes"
                except Exception as exc:
                    status_msg = f"Integrity error: {exc}"
            else:
                current_view = pop_breadcrumb(tui_state)
                status_msg = ""
        elif ch == "v":
            if current_view != "config":
                current_view = "config"
                push_breadcrumb(tui_state, "config")
            else:
                current_view = pop_breadcrumb(tui_state)
        elif ch == "c":
            if current_view != "edit":
                current_view = "edit"
                push_breadcrumb(tui_state, "edit")
            else:
                current_view = pop_breadcrumb(tui_state)
        if len(ch) == 1:
            ch = ch.lower()
        if current_view == "projects" and ch == "a":
            try:
                import json as _json

                import httpx
                resp = httpx.post(
                    f"{args.daemon_url}/admin/projects",
                    content=_json.dumps({"name": "new-project", "weight": 10}),
                    headers={"Content-Type": "application/json"},
                    timeout=5.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    status_msg = f"Added project: {data.get('project_id', '?')}"
                else:
                    status_msg = f"Add failed: {resp.status_code}"
            except Exception as exc:
                status_msg = f"Add error: {exc}"
                h._handle_connection_error(exc, args.daemon_url)
            return True
        if current_view == "projects" and ch == "d":
            try:
                import httpx
                resp = httpx.get(f"{args.daemon_url}/admin/projects", timeout=3.0)
                if resp.status_code == 200:
                    projects = resp.json().get("projects", [])
                    if projects:
                        pid = projects[0].get("project_id", "")
                        resp2 = httpx.delete(
                            f"{args.daemon_url}/admin/projects/{pid}", timeout=5.0,
                        )
                        status_msg = (
                            f"Removed {pid}"
                            if resp2.status_code == 200
                            else f"Remove failed: {resp2.status_code}"
                        )
                    else:
                        status_msg = "No projects to remove"
            except Exception as exc:
                status_msg = f"Remove error: {exc}"
            return True
        if current_view == "models" and ch == "a":
            tui_state["current_view"] = current_view
            tui_state["status_msg"] = status_msg
            tui_handler.handle_key(ch)
            status_msg = tui_state["status_msg"]
            return True
        if current_view == "ansible" and ch in ("s", "a", "\x1b"):
            tui_state["current_view"] = current_view
            tui_state["status_msg"] = status_msg
            tui_handler.handle_key(ch)
            current_view = tui_state["current_view"]
            status_msg = tui_state["status_msg"]
            return True
        if current_view == "main" and ch == "a":
            tui_state["current_view"] = current_view
            tui_state["status_msg"] = status_msg
            tui_handler.handle_key(ch)
            current_view = tui_state["current_view"]
            status_msg = tui_state["status_msg"]
            return True
        if current_view == "main" and ch == "d":
            tui_state["current_view"] = current_view
            tui_state["status_msg"] = status_msg
            tui_handler.handle_key(ch)
            status_msg = tui_state["status_msg"]
            return True
        if ch == "m":
            if current_view != "models":
                current_view = "models"
                push_breadcrumb(tui_state, "models")
                nonlocal downloaded_models
                downloaded_models = model_registry.list_downloaded()
                status_msg = h._build_model_status_msg(model_mgr.list_servers(), downloaded_models)
            else:
                current_view = pop_breadcrumb(tui_state)
        elif ch == "w":
            if current_view != "worktrees":
                current_view = "worktrees"
                push_breadcrumb(tui_state, "worktrees")
                status_msg = "Projects & Worktrees \u2014 [w] exit  [q] quit"
            else:
                current_view = pop_breadcrumb(tui_state)
        elif ch == "t":
            if current_view != "todos":
                current_view = "todos"
                push_breadcrumb(tui_state, "todos")
                status_msg = "Todos \u2014 [t] exit  [a]dd  [q] quit"
            else:
                current_view = pop_breadcrumb(tui_state)
        elif ch == "h":
            if current_view != "hooks":
                current_view = "hooks"
                push_breadcrumb(tui_state, "hooks")
                status_msg = "Hooks \u2014 [h] exit  [q] quit"
            else:
                current_view = pop_breadcrumb(tui_state)
        elif ch == "o":
            if current_view != "workers":
                current_view = "workers"
                push_breadcrumb(tui_state, "workers")
                status_msg = "Workers \u2014 [o] exit  [p]ing  [q] quit"
            else:
                current_view = pop_breadcrumb(tui_state)
        elif ch == "x":
            if current_view != "metrics":
                current_view = "metrics"
                push_breadcrumb(tui_state, "metrics")
                status_msg = "Metrics \u2014 [x] exit  [q] quit"
            else:
                current_view = pop_breadcrumb(tui_state)
        elif ch == "g":
            if current_view != "agents":
                current_view = "agents"
                push_breadcrumb(tui_state, "agents")
                status_msg = "Agents \u2014 [g] exit  [q] quit"
            else:
                current_view = pop_breadcrumb(tui_state)
        elif ch == "r":
            daemon_running = detect_daemon()
            status_msg = "Refreshed"
        elif ch in ("u", "j", "e", "b", "l", "n", "f", "z", "y", "P", "R", "L") or current_view in (
            "todos", "workers", "models", "mcp", "skills", "compute",
            "projects", "hooks", "integrity", "agents", "slurm",
        ) or ch in ("\x1b[B", "\x1b[A", "\r"):
            tui_state["current_view"] = current_view
            tui_state["status_msg"] = status_msg
            tui_handler.handle_key(ch)
            current_view = tui_state["current_view"]
            status_msg = tui_state["status_msg"]
        return True

    def getch(fd: int, timeout: float = 0.3) -> str:
        r, _w, _e = select.select([fd], [], [], timeout)
        if r:
            data = os.read(fd, 1)
            if data == b"\x1b":
                r2, _w2, _e2 = select.select([fd], [], [], 0.05)
                if r2:
                    more = os.read(fd, 2)
                    if more in (b"[A", b"[B", b"[C", b"[D", b"OH", b"OF"):
                        return data.decode() + more.decode()
                    if more == b"[M":
                        r3, _w3, _e3 = select.select([fd], [], [], 0.05)
                        if r3:
                            mouse_data = os.read(fd, 3)
                            if len(mouse_data) == 3:
                                btn = mouse_data[0] - 32
                                col = mouse_data[1] - 32
                                row = mouse_data[2] - 32
                                return f"\x1b[M{btn}:{col}:{row}"
                return "\x1b"
            return data.decode("utf-8", errors="ignore") or ""
        return ""

    info = h._gather_offline_status()
    console = Console()
    stdin_fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(stdin_fd)

    layout = make_layout(info)
    _mouse_dragging = False
    try:
        tty.setcbreak(stdin_fd)
        sys.stdout.write("\x1b[?1002h")
        sys.stdout.flush()
        with Live(layout, console=console, refresh_per_second=4, screen=True) as live:
            while True:
                ch = getch(stdin_fd, 0.3)
                if ch:
                    tui_logger.verbose = tui_state.get("verbose_logging", False)
                    tui_logger.log_key_press(current_view, repr(ch))

                    if ch.startswith("\x1b[M") and ":" in ch:
                        parts = ch[3:].split(":")
                        btn_code = int(parts[0])
                        col = int(parts[1])
                        is_release = btn_code == 3
                        if _mouse_dragging and is_release:
                            _mouse_dragging = False
                        elif not is_release and btn_code in (0, 1, 2, 32, 33, 34):
                            _mouse_dragging = True
                            import shutil as _shutil_mouse
                            tw, _th = _shutil_mouse.get_terminal_size((80, 24))
                            new_w = max(20, min(col, tw - 20))
                            tui_state["left_panel_width"] = new_w
                        continue

                    if ch == "\x03":
                        break
                    if ch == "\x1b":
                        if tui_state.get("input_mode") is not None:
                            tui_state["input_mode"] = None
                            tui_state["input_buffer"] = ""
                            status_msg = "Cancelled"
                        elif current_view != "main":
                            old_view = current_view
                            current_view = "main"
                            status_msg = ""
                            pop_breadcrumb(tui_state)
                            info = h._gather_offline_status()
                            live.update(make_layout(info))
                            tui_logger.log_view_change(old_view, "main")
                            continue
                        break
                    old_view = current_view
                    if not handle_key(info, ch):
                        break
                    if current_view != old_view:
                        tui_logger.log_view_change(old_view, current_view)
                    tui_logger.log_status_msg(status_msg)
                info = h._gather_offline_status()
                live.update(make_layout(info))
    finally:
        sys.stdout.write("\x1b[?1002l")
        sys.stdout.flush()
        tui_logger.close()
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_settings)
    print("TUI exited.")
