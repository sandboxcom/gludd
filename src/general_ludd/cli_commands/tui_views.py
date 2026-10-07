"""Pure Rich renderers used by the terminal interface."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from general_ludd.cli_commands.platform import format_size as _fmt_size
from general_ludd.tui.tables import _make_table

if TYPE_CHECKING:
    from rich.table import Table


def _scale_col(term_width: int, fraction: float, min_w: int = 4) -> int:
    return max(min_w, int(term_width * fraction))


def _compute_panel_widths(term_w: int, tui_state: dict[str, Any]) -> tuple[int, int]:
    left = tui_state.get("left_panel_width") or max(30, term_w * 2 // 5)
    left = max(20, min(left, term_w - 20))
    right = term_w - left
    return left, right


def _table_overhead(ncols: int) -> int:
    return 2 + (ncols - 1) + ncols * 2


def _wrap_table(renderable: Any) -> Any:
    from rich.panel import Panel

    return Panel(renderable, padding=0, expand=True)


def _compute_footer_rows(term_height: int) -> int:
    return min(18, max(6, term_height - 20))


def _build_controls_table(
    daemon_running: bool,
    status_msg: str,
    *,
    term_width: int = 60,
    selected_idx: int = -1,
) -> Table:
    from rich.table import Table

    t = Table(
        title="Controls",
        show_header=False,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("Key", style="yellow", width=3, no_wrap=True)
    t.add_column("Action", style="cyan", no_wrap=True, ratio=2, min_width=6)
    t.add_column("Status", style="green", no_wrap=True, ratio=1, min_width=6)
    rows = [
        ("s", "Start daemon", "running" if daemon_running else "stopped"),
        ("k", "Kill daemon", ""),
        ("r", "Refresh", ""),
        ("i", "Integrity scan", ""),
        ("v", "Config files", ""),
        ("c", "Config editor", ""),
        ("m", "Models", ""),
        ("a", "Ansible", ""),
        ("w", "Worktrees", ""),
        ("p", "Projects", ""),
        ("t", "Todos", ""),
        ("h", "Hooks", ""),
        ("o", "Workers", ""),
        ("x", "Metrics", ""),
        ("g", "Agents", ""),
        ("d", "Dispatch", ""),
        ("u", "MCP", ""),
        ("j", "Skills", ""),
        ("e", "Compute", ""),
        ("b", "Scores", ""),
        ("l", "Templates", ""),
        ("n", "Quantize", ""),
        ("f", "Filestore", ""),
        ("z", "Deploys", ""),
        ("R", "Reload", ""),
        ("H", "Health", ""),
        ("T", "Selftest", ""),
        ("0", "Version", ""),
        ("1", "LogLevel", ""),
        ("D", "Discovered", ""),
        ("C", "Code", ""),
        ("q", "Quit", ""),
    ]
    for i, (key, action, status) in enumerate(rows):
        if i == selected_idx:
            prefix = "▶ "
            style = "bold reverse"
            t.add_row(f"{prefix}{key}", f"[{style}]{action}[/{style}]", status)
        else:
            t.add_row(key, action, status)
    if status_msg:
        t.add_row("", f"[bold yellow]{status_msg[:50]}[/]", "")
    return t


def _build_daemon_table(daemon_running: bool, daemon_url: str, current_view: str, *, term_width: int = 60) -> Table:
    from rich.table import Table

    t = Table(
        title="Daemon",
        show_header=False,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("Key", style="cyan", no_wrap=True, ratio=1, min_width=6, max_width=20)
    _available = term_width - _table_overhead(2)
    val_w = max(10, _available * 3 // 4)
    t.add_column("Value", style="green", no_wrap=True, ratio=3, min_width=10, max_width=60)
    t.add_row("Status", "running" if daemon_running else "stopped")
    url_display = daemon_url
    if len(url_display) > val_w - 2:
        url_display = url_display[: val_w - 5] + "..."
    t.add_row("URL", url_display)
    t.add_row("View", current_view)
    if daemon_running:
        try:
            resp = httpx.get(f"{daemon_url}/admin/daemon/stats", timeout=2.0)
            if resp.status_code == 200:
                stats = resp.json()
                pid = stats.get("pid", "?")
                t.add_row("PID", str(pid))
                reqs = stats.get("requests_total", 0)
                resps = stats.get("responses_total", 0)
                t.add_row("Requests", f"{reqs} req / {resps} resp")
                mem = stats.get("memory_mb", 0)
                t.add_row("Memory", f"{mem:.1f} MB")
                uptime = stats.get("uptime_s", 0)
                t.add_row("Uptime", f"{uptime:.0f}s")
        except Exception:
            pass
    return t


def _build_info_table(info: dict[str, Any], *, term_width: int = 60) -> Table:

    val_w = max(10, term_width - _table_overhead(2) - 6)
    rows = [
        ("Version", str(info.get("version", "?"))),
        ("Python", str(info.get("python_version", "?"))),
        ("Platform", str(info.get("platform", "?"))),
        ("CWD", str(info.get("cwd", "?"))[:val_w]),
        ("Config Dir", str(info.get("config_dir", "?"))[:val_w]),
        ("Config Files", str(len(info.get("config_files", [])))),
        ("Filestore", str(info.get("filestore_root", "?"))[:val_w]),
        ("Filestore Size", _fmt_size(info.get("filestore_size_bytes", 0))),
        ("DB Engine", str(info.get("db_engine", "?"))),
        ("DB Exists", "yes" if info.get("db_exists") else "no"),
    ]
    if info.get("db_exists"):
        rows.append(("DB Size", _fmt_size(info.get("db_size_bytes", 0))))
    return _make_table(
        title="System Info",
        columns=[("Key", "cyan", 1, 6), ("Value", "green", 3, 10)],
        rows=rows,
        show_header=False,
        term_width=term_width,
    )


def _build_binary_table(info: dict[str, Any], *, term_width: int = 60) -> Table:
    from rich.table import Table

    t = Table(
        title="Binaries",
        show_header=True,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("Binary", style="cyan", no_wrap=True, ratio=2, min_width=6)
    t.add_column("Found", style="green", no_wrap=True, ratio=1, min_width=3)
    t.add_column("Version", style="yellow", no_wrap=True, ratio=2, min_width=4)
    versions: dict[str, str] = info.get("binary_versions", {})
    for name, path in info.get("binary_paths", {}).items():
        ver = versions.get(name, versions.get(name.replace("-", ""), ""))
        t.add_row(name, "yes" if path else "no", ver if ver else "?")
    fs_bins: list[dict[str, Any]] = info.get("filestore_binaries", [])
    for b in fs_bins:
        bname = b.get("name", b.get("binary_name", "?"))
        bver = b.get("version", "?")
        t.add_row(f"[fs]{bname}", "bundled", bver)
    return t


def _build_config_table(info: dict[str, Any], *, term_width: int = 60) -> Table:

    rows = [(cf.get("name", "?"), _fmt_size(cf.get("size_bytes", 0))) for cf in info.get("config_files", [])]
    return _make_table(
        title="Config Files",
        columns=[("File", "cyan", 3, 8), ("Size", "green", 1, 4)],
        rows=rows,
        term_width=term_width,
    )


def _build_todos_table(todos: list[dict[str, Any]], *, term_width: int = 60, selected_idx: int | None = None) -> Table:

    _status_colors = {"pending": "yellow", "in_progress": "cyan", "completed": "green", "cancelled": "dim"}
    rows = [
        (
            str(todo.get("todo_id", "?")),
            str(todo.get("title", "")),
            f"[{_status_colors.get(todo.get('status', '?'), 'white')}]{todo.get('status', '?')}[/]",
            str(todo.get("priority", "")),
        )
        for todo in todos
    ]
    return _make_table(
        title="Todos",
        columns=[("ID", "cyan", 1, 4), ("Title", "green", 3, 6), ("Status", "yellow", 2, 4), ("Pri", "bold", 1, 3)],
        rows=rows,
        selected_idx=selected_idx,
        term_width=term_width,
    )


def _build_hooks_table(hooks: list[dict[str, Any]], *, term_width: int = 60, selected_idx: int | None = None) -> Table:

    rows = [
        (
            str(h.get("hook_id", "?")),
            str(h.get("event_name", h.get("event_type", "?"))),
            str(h.get("hook_type", "?")),
        )
        for h in hooks
    ]
    return _make_table(
        title="Hooks",
        columns=[("ID", "cyan", 2, 6), ("Event", "green", 2, 6), ("Type", "yellow", 1, 4)],
        rows=rows,
        selected_idx=selected_idx,
        term_width=term_width,
    )


def _build_workers_table(
    workers: list[dict[str, Any]],
    *,
    term_width: int = 60,
    selected_idx: int | None = None,
) -> Table:

    rows = [(str(w.get("worker_id", "?")), str(w.get("address", "?"))) for w in workers]
    return _make_table(
        title="Workers",
        columns=[("ID", "cyan", 2, 6), ("Address", "green", 3, 8)],
        rows=rows,
        selected_idx=selected_idx,
        term_width=term_width,
    )


def _build_metrics_table(cost_data: dict[str, Any], *, term_width: int = 60) -> Table:

    labels = [
        ("Total Cost", "total_cost_usd", "${:.2f}"),
        ("Subscription", "subscription_name", "{}"),
        ("Sub Cost/Mo", "subscription_cost_usd_per_month", "${:.2f}"),
        ("Tokens Used", "tokens_used", "{:,}"),
        ("Tokens Left", "tokens_remaining_this_week", "{:,}"),
        ("Cost % Sub", "cost_as_pct_of_subscription", "{:.1f}%"),
        ("Tokens % Wk", "tokens_as_pct_of_weekly", "{:.1f}%"),
    ]
    rows = []
    for label, key, fmt in labels:
        val = cost_data.get(key)
        if val is not None:
            if isinstance(val, (int, float)):
                try:
                    rows.append((label, fmt.format(val)))
                except (ValueError, TypeError):
                    rows.append((label, str(val)))
            else:
                rows.append((label, str(val)))
    return _make_table(
        title="Metrics",
        columns=[("Metric", "cyan", 2, 6), ("Value", "green", 2, 6)],
        rows=rows,
        show_header=False,
        term_width=term_width,
    )


def _build_agents_table(agents: list[dict[str, Any]], *, term_width: int = 60) -> Table:

    rows = []
    for a in agents:
        status = a.get("status", "?")
        status_color = "green" if status == "running" else "yellow" if status == "idle" else "red"
        uptime_s = a.get("uptime_seconds", 0)
        uptime_h = uptime_s // 3600
        uptime_m = (uptime_s % 3600) // 60
        rows.append(
            (
                str(a.get("agent_id", "?")),
                str(a.get("agent_name", a.get("name", "?"))),
                f"[{status_color}]{status}[/]",
                str(a.get("project", "")),
                f"{uptime_h}h{uptime_m}m",
            )
        )
    return _make_table(
        title="Agents",
        columns=[
            ("ID", "cyan", 1, 4),
            ("Name", "green", 2, 5),
            ("Status", "yellow", 1, 4),
            ("Project", "bold", 1, 5),
            ("Up", "dim", 1, 4),
        ],
        rows=rows,
        term_width=term_width,
    )


def _build_model_table(
    servers: list[Any],
    downloaded: list[Any],
    *,
    term_width: int = 60,
    selected_idx: int | None = None,
) -> Table:
    from rich.table import Table

    t = Table(
        title="Models",
        show_header=True,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("ID", style="cyan", no_wrap=True, ratio=2, min_width=5)
    t.add_column("Engine", style="green", no_wrap=True, ratio=1, min_width=4)
    t.add_column("Model", style="yellow", no_wrap=True, ratio=3, min_width=6)
    t.add_column("Status", style="bold", no_wrap=True, ratio=1, min_width=4)

    row_idx = 0
    for s in servers:
        sel_marker = "▶ " if selected_idx is not None and row_idx == selected_idx else "  "
        style = "bold reverse" if selected_idx is not None and row_idx == selected_idx else None
        if isinstance(s, dict):
            sid = str(s.get("id", s.get("server_id", "?")))
            engine = str(s.get("engine", "?"))
            model_name = str(s.get("model", "?"))
            status_text = str(s.get("status", "stopped"))
            status_color = "green" if status_text == "running" else "red"
            t.add_row(
                sel_marker + f"[s]{sid}",
                engine,
                model_name,
                f"[{status_color}]{status_text}[/]",
                style=style,
            )
        else:
            status_color = "green" if getattr(s, "is_running", False) else "red"
            status_text = getattr(s, "status", "stopped")
            t.add_row(
                sel_marker + f"[s]{s.server_id}",
                s.config.engine,
                (s.config.model_name or s.config.model_path or "?"),
                f"[{status_color}]{status_text}[/]",
                style=style,
            )
        row_idx += 1

    for dm in downloaded:
        sel_marker = "▶ " if selected_idx is not None and row_idx == selected_idx else "  "
        style = "bold reverse" if selected_idx is not None and row_idx == selected_idx else None
        if isinstance(dm, dict):
            size_str = _fmt_size(dm.get("size_bytes", 0)) if dm.get("size_bytes") else "?"
            mid = str(dm.get("model_id", "?"))
            t.add_row(
                sel_marker + f"[d]{mid[:12]}",
                str(dm.get("engine", "?")),
                mid,
                f"[dim]{size_str}[/]",
                style=style,
            )
        else:
            size_str = _fmt_size(dm.size_bytes) if dm.size_bytes else "?"
            t.add_row(
                sel_marker + f"[d]{dm.model_id[:12]}",
                dm.engine,
                dm.model_id,
                f"[dim]{size_str}[/]",
                style=style,
            )
        row_idx += 1
    return t


def _build_config_editor_table(
    items: list[dict[str, Any]],
    selected: int,
    depth: int,
    *,
    term_width: int = 60,
) -> Table:
    from rich.table import Table

    t = Table(
        title="Config Editor",
        show_header=True,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("Option", style="cyan", no_wrap=True, ratio=3, min_width=6)
    t.add_column("Value", style="green", no_wrap=True, ratio=3, min_width=6)
    t.add_column("Help", style="dim", no_wrap=True, ratio=3, min_width=6)
    if depth == 0:
        for i, item in enumerate(items):
            prefix = "\u25b6" if i == selected else " "
            label = str(item.get("label", ""))
            t.add_row(f"{prefix} [bold]{label}[/]", "", "")
    else:
        for i, item in enumerate(items):
            prefix = "\u25b6" if i == selected else " "
            label = str(item.get("label", ""))
            value = str(item.get("value", ""))
            help_text = str(item.get("help_text", ""))
            t.add_row(f"{prefix} {label}", value, help_text)
    return t


def _build_worktrees_table(entries: list[tuple[str, str]], *, term_width: int = 60) -> Table:

    rows = [(name, f"[{'green' if 'AGENTS.md' in status else 'dim'}]{status}[/]") for name, status in entries]
    return _make_table(
        title="Projects & Worktrees",
        columns=[("Name", "green", 3, 6), ("Status", "bold", 2, 6)],
        rows=rows,
        term_width=term_width,
    )


def _build_projects_table(
    projects: list[dict[str, Any]],
    *,
    term_width: int = 60,
    selected_idx: int | None = None,
) -> Table:

    rows = [
        (
            str(p.get("project_id", "?")),
            str(p.get("name", "?")),
            f"{p.get('weight', 0)}%",
            f"[{'green' if str(p.get('dispatch_mode', 'active')) == 'active' else 'yellow'}]"
            f"{p.get('dispatch_mode', 'active')}[/]",
        )
        for p in projects
    ]
    return _make_table(
        title="Projects",
        columns=[("ID", "cyan", 1, 5), ("Name", "green", 2, 6), ("Wt", "yellow", 1, 3), ("Mode", "bold", 1, 4)],
        rows=rows,
        selected_idx=selected_idx,
        term_width=term_width,
    )


def _build_integrity_table(changes: list[dict[str, Any]], *, term_width: int = 60) -> Table:

    _icons = {"new": "+", "modified": "~", "removed": "-"}
    if not changes:
        rows = [("No changes", "", "")]
    else:
        rows = [
            (
                str(ch.get("file", "?")),
                f"{_icons.get(ch.get('type', ''), '?')} {ch.get('type', '?')}",
                "approved" if ch.get("approved") else "pending",
            )
            for ch in changes
        ]
    return _make_table(
        title="Integrity",
        columns=[("File", "cyan", 3, 6), ("Type", "yellow", 1, 4), ("Status", "bold", 1, 4)],
        rows=rows,
        term_width=term_width,
    )


def _build_ansible_table(results: list[dict[str, Any]], *, term_width: int = 60) -> Table:

    if not results:
        rows = [("Press [s] to search", "")]
    else:
        rows = [(str(r.get("name", "?")), str(r.get("description", ""))) for r in results]
    return _make_table(
        title="Ansible Galaxy",
        columns=[("Name", "cyan", 2, 6), ("Description", "green", 3, 8)],
        rows=rows,
        term_width=term_width,
    )


def _build_model_status_msg(servers: list[Any], downloaded: list[Any]) -> str:
    parts: list[str] = []
    if servers:
        parts.append(f"{len(servers)} configured")
    if downloaded:
        parts.append(f"{len(downloaded)} downloaded")
    if not parts:
        return "Model services: no servers or downloads"
    return f"Model services: {', '.join(parts)}"


def _build_mcp_table(servers: list[dict[str, Any]], *, term_width: int = 60) -> Table:
    from rich.table import Table

    t = Table(
        title="MCP Servers",
        show_header=True,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("Name", style="cyan", no_wrap=True, ratio=2, min_width=6, max_width=40)
    t.add_column("Transport", style="green", no_wrap=True, ratio=1, min_width=4, max_width=20)
    t.add_column("Status", style="yellow", no_wrap=True, ratio=1, min_width=4, max_width=20)
    if not servers:
        t.add_row("No MCP servers", "", "")
    else:
        for s in servers:
            status = str(s.get("status", "?"))
            color = "green" if status == "active" else "red"
            t.add_row(
                str(s.get("name", "?")),
                str(s.get("transport", "?")),
                f"[{color}]{status}[/]",
            )
    return t


def _build_skills_table(skills: list[dict[str, Any]], *, term_width: int = 60) -> Table:
    from rich.table import Table

    t = Table(
        title="Skills",
        show_header=True,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("Name", style="cyan", no_wrap=True, ratio=2, min_width=6, max_width=40)
    t.add_column("Category", style="green", no_wrap=True, ratio=1, min_width=4, max_width=20)
    t.add_column("Installed", style="yellow", no_wrap=True, ratio=1, min_width=3, max_width=20)
    if not skills:
        t.add_row("No skills", "", "")
    else:
        for sk in skills:
            installed = "yes" if sk.get("installed") else "no"
            color = "green" if sk.get("installed") else "dim"
            t.add_row(
                str(sk.get("name", "?")),
                str(sk.get("category", "")),
                f"[{color}]{installed}[/]",
            )
    return t


def _build_compute_table(endpoints: list[dict[str, Any]], *, term_width: int = 60) -> Table:
    from rich.table import Table

    t = Table(
        title="Compute Endpoints",
        show_header=True,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("ID", style="cyan", no_wrap=True, ratio=2, min_width=6, max_width=40)
    t.add_column("Provider", style="green", no_wrap=True, ratio=1, min_width=4, max_width=20)
    t.add_column("Status", style="yellow", no_wrap=True, ratio=1, min_width=4, max_width=20)
    if not endpoints:
        t.add_row("No endpoints", "", "")
    else:
        for ep in endpoints:
            status = str(ep.get("status", "?"))
            color = "green" if status == "active" else "red"
            t.add_row(
                str(ep.get("endpoint_id", "?")),
                str(ep.get("provider", "?")),
                f"[{color}]{status}[/]",
            )
    return t


def _build_scores_table(scores: list[dict[str, Any]], *, term_width: int = 60) -> Table:
    from rich.table import Table

    t = Table(
        title="Benchmark Scores",
        show_header=True,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("Prompt", style="cyan", no_wrap=True, ratio=2, min_width=6, max_width=30)
    t.add_column("Model", style="green", no_wrap=True, ratio=2, min_width=6, max_width=30)
    t.add_column("Task", style="yellow", no_wrap=True, ratio=1, min_width=4, max_width=20)
    t.add_column("Score", style="bold", no_wrap=True, ratio=1, min_width=3, max_width=20)
    if not scores:
        t.add_row("No scores", "", "", "")
    else:
        for s in scores:
            score_val = s.get("composite_score", 0)
            color = "green" if score_val >= 0.8 else "yellow" if score_val >= 0.6 else "red"
            t.add_row(
                str(s.get("prompt_profile", "?")),
                str(s.get("model_profile", "?")),
                str(s.get("task_type", "?")),
                f"[{color}]{score_val:.2f}[/]",
            )
    return t


def _build_leaderboard_table(entries: list[dict[str, Any]], *, term_width: int = 60) -> Table:
    from rich.table import Table

    t = Table(
        title="Leaderboard",
        show_header=True,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("#", style="bold", no_wrap=True, ratio=1, min_width=3, max_width=20)
    t.add_column("Prompt", style="cyan", no_wrap=True, ratio=2, min_width=6, max_width=30)
    t.add_column("Model", style="green", no_wrap=True, ratio=2, min_width=6, max_width=30)
    t.add_column("Score", style="yellow", no_wrap=True, ratio=1, min_width=3, max_width=20)
    if not entries:
        t.add_row("", "No entries", "", "")
    else:
        for e in entries:
            score_val = e.get("score", 0)
            color = "green" if score_val >= 0.8 else "yellow" if score_val >= 0.6 else "red"
            t.add_row(
                str(e.get("rank", "")),
                str(e.get("prompt", "?")),
                str(e.get("model", "?")),
                f"[{color}]{score_val:.2f}[/]",
            )
    return t


def _build_templates_table(templates: list[dict[str, Any]], *, term_width: int = 60) -> Table:
    from rich.table import Table

    t = Table(
        title="Templates",
        show_header=True,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("Name", style="cyan", no_wrap=True, ratio=2, min_width=6, max_width=30)
    t.add_column("Task Types", style="green", no_wrap=True, ratio=3, min_width=6, max_width=40)
    t.add_column("Source", style="yellow", no_wrap=True, ratio=1, min_width=4, max_width=20)
    if not templates:
        t.add_row("No templates", "", "")
    else:
        for tp in templates:
            task_types = tp.get("task_types", [])
            types_str = ", ".join(str(t) for t in task_types) if isinstance(task_types, list) else str(task_types)
            t.add_row(
                str(tp.get("name", "?")),
                types_str,
                str(tp.get("source", "")),
            )
    return t


def _build_playbooks_table(playbooks: list[dict[str, Any]], *, term_width: int = 60) -> Table:
    from rich.table import Table

    t = Table(
        title="Playbooks",
        show_header=True,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("Name", style="cyan", no_wrap=True, ratio=3, min_width=6, max_width=50)
    t.add_column("Tasks", style="green", no_wrap=True, ratio=1, min_width=3, max_width=20)
    t.add_column("Status", style="yellow", no_wrap=True, ratio=1, min_width=4, max_width=20)
    if not playbooks:
        t.add_row("No playbooks", "", "")
    else:
        for pb in playbooks:
            status = str(pb.get("status", "?"))
            color = "green" if status == "ready" else "yellow"
            t.add_row(
                str(pb.get("name", "?")),
                str(pb.get("tasks", 0)),
                f"[{color}]{status}[/]",
            )
    return t


def _build_quantization_table(entries: list[dict[str, Any]], *, term_width: int = 60) -> Table:
    from rich.table import Table

    t = Table(
        title="Quantization",
        show_header=True,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("Model", style="cyan", no_wrap=True, ratio=3, min_width=6, max_width=40)
    t.add_column("Precision", style="green", no_wrap=True, ratio=1, min_width=4, max_width=20)
    t.add_column("Conf", style="yellow", no_wrap=True, ratio=1, min_width=3, max_width=20)
    t.add_column("Source", style="dim", no_wrap=True, ratio=1, min_width=4, max_width=20)
    if not entries:
        t.add_row("No data", "", "", "")
    else:
        for e in entries:
            conf = e.get("confidence", 0)
            color = "green" if conf >= 0.8 else "yellow" if conf >= 0.5 else "red"
            t.add_row(
                str(e.get("model_id", "?")),
                str(e.get("precision", "?")),
                f"[{color}]{conf:.2f}[/]",
                str(e.get("source", "")),
            )
    return t


def _build_filestore_table(files: list[dict[str, Any]], *, term_width: int = 60) -> Table:
    from rich.table import Table

    t = Table(
        title="Filestore",
        show_header=True,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("Name", style="cyan", no_wrap=True, ratio=3, min_width=6, max_width=50)
    t.add_column("Size", style="green", no_wrap=True, ratio=1, min_width=4, max_width=20)
    t.add_column("Type", style="yellow", no_wrap=True, ratio=1, min_width=4, max_width=20)
    if not files:
        t.add_row("No files", "", "")
    else:
        for f in files:
            size_bytes = f.get("size_bytes", 0)
            t.add_row(
                str(f.get("name", "?")),
                _fmt_size(size_bytes),
                str(f.get("type", "")),
            )
    return t


def _build_deployments_table(deployments: list[dict[str, Any]], *, term_width: int = 60) -> Table:
    from rich.table import Table

    t = Table(
        title="Deployments",
        show_header=True,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("Name", style="cyan", no_wrap=True, ratio=2, min_width=6, max_width=40)
    t.add_column("Provider", style="green", no_wrap=True, ratio=1, min_width=4, max_width=20)
    t.add_column("Status", style="yellow", no_wrap=True, ratio=1, min_width=4, max_width=20)
    if not deployments:
        t.add_row("No deployments", "", "")
    else:
        for d in deployments:
            status = str(d.get("status", "?"))
            color = "green" if status == "running" else "red" if status == "stopped" else "yellow"
            t.add_row(
                str(d.get("name", "?")),
                str(d.get("provider", "?")),
                f"[{color}]{status}[/]",
            )
    return t


def _build_slurm_table(jobs: list[dict[str, Any]], *, term_width: int = 60) -> Table:
    from rich.table import Table

    t = Table(
        title="Slurm Jobs",
        show_header=True,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("Job ID", style="cyan", no_wrap=True, ratio=2, min_width=6, max_width=30)
    t.add_column("State", style="green", no_wrap=True, ratio=1, min_width=4, max_width=20)
    t.add_column("Exit Code", style="yellow", no_wrap=True, ratio=1, min_width=4, max_width=15)
    if not jobs:
        t.add_row("No jobs", "", "")
    else:
        for j in jobs:
            state = str(j.get("state", "?"))
            state_colors = {
                "COMPLETED": "green",
                "RUNNING": "cyan",
                "PENDING": "yellow",
            }
            color = state_colors.get(state, "red")
            exit_code = str(j.get("exit_code", "")) if j.get("exit_code") is not None else ""
            t.add_row(
                str(j.get("job_id", "?")),
                f"[{color}]{state}[/]",
                exit_code,
            )
    return t


def _build_health_table(data: dict[str, Any], *, term_width: int = 60) -> Table:
    from rich.table import Table

    t = Table(
        title="Health",
        show_header=False,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("Key", style="cyan", no_wrap=True, ratio=1, min_width=6, max_width=20)
    t.add_column("Value", style="green", no_wrap=True, ratio=2, min_width=10, max_width=50)
    if not data:
        t.add_row("Status", "no data — press [r] to refresh")
    else:
        for key, val in data.items():
            val_str = str(val)
            if len(val_str) > 48:
                val_str = val_str[:45] + "..."
            t.add_row(str(key), val_str)
    return t


def _build_selftest_table(data: dict[str, Any], *, term_width: int = 60) -> Table:
    from rich.table import Table

    t = Table(
        title="Selftest",
        show_header=True,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("Scenario", style="cyan", no_wrap=True, ratio=3, min_width=6, max_width=40)
    t.add_column("Result", style="green", no_wrap=True, ratio=1, min_width=4, max_width=20)
    if not data:
        t.add_row("Press [r] to run", "")
        return t
    results = data.get("results", [])
    if not results:
        run = data.get("scenarios_run", 0)
        passed = data.get("scenarios_passed", 0)
        t.add_row(f"Summary: {passed}/{run} passed", "OK" if passed == run else "FAIL")
    else:
        for r in results:
            status = "PASS" if r.get("passed") else "FAIL"
            color = "green" if r.get("passed") else "red"
            t.add_row(
                str(r.get("scenario", "unknown")),
                f"[{color}]{status}[/]",
            )
    return t


def _build_version_table(info: dict[str, Any], *, term_width: int = 60) -> Table:
    from rich.table import Table

    t = Table(
        title="Version",
        show_header=False,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("Key", style="cyan", no_wrap=True, ratio=1, min_width=6, max_width=20)
    t.add_column("Value", style="green", no_wrap=True, ratio=2, min_width=10, max_width=50)
    rows = [
        ("Version", str(info.get("version", "?"))),
        ("Python", str(info.get("python_version", "?"))),
        ("Platform", str(info.get("platform", "?"))),
    ]
    for key, val in rows:
        t.add_row(key, val)
    return t


def _build_loglevel_table(current_level: str, *, term_width: int = 60) -> Table:
    from rich.table import Table

    t = Table(
        title="Log Level",
        show_header=True,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("Level", style="cyan", no_wrap=True, ratio=1, min_width=6, max_width=20)
    t.add_column("Active", style="green", no_wrap=True, ratio=1, min_width=4, max_width=10)
    for level in ("debug", "info", "warning", "error"):
        marker = "[bold green]◄[/]" if level == current_level else ""
        t.add_row(level, marker)
    return t


def _build_discovered_table(profiles: list[dict[str, Any]], *, term_width: int = 60) -> Table:
    from rich.table import Table

    t = Table(
        title="Discovered Models",
        show_header=True,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("Profile ID", style="cyan", no_wrap=True, ratio=2, min_width=6, max_width=40)
    t.add_column("Display Name", style="green", no_wrap=True, ratio=2, min_width=6, max_width=30)
    t.add_column("Enabled", style="yellow", no_wrap=True, ratio=1, min_width=4, max_width=10)
    if not profiles:
        t.add_row("No profiles", "", "")
    else:
        for p in profiles:
            enabled = p.get("enabled", True)
            color = "green" if enabled else "red"
            t.add_row(
                str(p.get("model_profile_id", "?")),
                str(p.get("display_name", "?")),
                f"[{color}]{'yes' if enabled else 'no'}[/]",
            )
    return t


def _build_code_table(results: list[dict[str, Any]], *, term_width: int = 60) -> Table:
    from rich.table import Table

    t = Table(
        title="Code Intel",
        show_header=True,
        expand=True,
        width=term_width,
        title_justify="left",
    )
    t.add_column("File", style="cyan", no_wrap=True, ratio=2, min_width=6, max_width=40)
    t.add_column("Line", style="green", no_wrap=True, ratio=1, min_width=3, max_width=8)
    t.add_column("Text", style="yellow", no_wrap=True, ratio=3, min_width=6, max_width=40)
    if not results:
        t.add_row("Press [s] to search", "", "")
    else:
        for r in results:
            text = str(r.get("text", ""))[:38]
            t.add_row(
                str(r.get("file", "?")),
                str(r.get("line", "")),
                text,
            )
    return t

build_controls_table = _build_controls_table
build_daemon_table = _build_daemon_table
build_info_table = _build_info_table
build_binary_table = _build_binary_table
build_config_table = _build_config_table
build_todos_table = _build_todos_table
build_hooks_table = _build_hooks_table
build_workers_table = _build_workers_table
build_metrics_table = _build_metrics_table
build_agents_table = _build_agents_table
build_model_table = _build_model_table
build_config_editor_table = _build_config_editor_table
build_worktrees_table = _build_worktrees_table
build_projects_table = _build_projects_table
build_integrity_table = _build_integrity_table
build_ansible_table = _build_ansible_table
build_model_status_msg = _build_model_status_msg
build_mcp_table = _build_mcp_table
build_skills_table = _build_skills_table
build_compute_table = _build_compute_table
build_scores_table = _build_scores_table
build_leaderboard_table = _build_leaderboard_table
build_templates_table = _build_templates_table
build_playbooks_table = _build_playbooks_table
build_quantization_table = _build_quantization_table
build_filestore_table = _build_filestore_table
build_deployments_table = _build_deployments_table
build_slurm_table = _build_slurm_table
build_health_table = _build_health_table
build_selftest_table = _build_selftest_table
build_version_table = _build_version_table
build_loglevel_table = _build_loglevel_table
build_discovered_table = _build_discovered_table
build_code_table = _build_code_table
