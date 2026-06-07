"""Unified CLI entrypoint for General Ludd Agent."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import signal
import sys
import time
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from rich.table import Table

MAN_PAGE = """\
NAME
    gludd — General Ludd Agent — autonomous coding system

SYNOPSIS
    gludd <command> [<subcommand>] [options...]

DESCRIPTION
    General Ludd Agent is an autonomous coding system with Ansible runners
    and multi-model AI agents. It coordinates AI models and local automation
    to complete software work.

COMMANDS
    daemon              Start the daemon (server + event loop)
      --host HOST         Bind address (default: 0.0.0.0)
      --port PORT         Port (default: 8000)
      --log-level LEVEL   debug|info|warning|error (default: info)
      --tick-interval N   Event loop tick interval in seconds (default: 1.0)
      --workers N         Gunicorn workers (default: 1)
      --config-dir PATH   Configuration directory
      --templates-dir PATH  Prompt templates directory
      --playbooks-dir PATH  Ansible playbooks directory

    add                 Add a todo to the queue
      TITLE               Task title (required)
      --description TEXT  Detailed description
      --queue NAME        Target queue (default: core)
      --priority INT      Priority (default: 100)
      --work-type TYPE    code|test|review|refactor|docs|etc
      --project ID        Project identifier
      --daemon-url URL    Daemon URL (default: http://localhost:8000)

    status              Show todo or system status
      [TODO_ID]           Optional todo ID for details
      --project ID        Filter by project
      --daemon-url URL    Daemon URL

    list                List todos
      --queue NAME        Filter by queue
      --status STATUS     Filter by status
      --project ID        Filter by project
      --daemon-url URL    Daemon URL

    log-level           Change daemon log level at runtime
      LEVEL               debug|info|warning|error
      --daemon-url URL    Daemon URL

    deployments         List active deployments
      --daemon-url URL    Daemon URL

    version             Show version

    health              Check daemon health
      --daemon-url URL    Daemon URL

    models              Model management commands
      search              Search HuggingFace models
        [QUERY]             Search query
        --limit N           Max results (default: 20)
        --daemon-url URL    Daemon URL
      downloaded          List downloaded models
        --daemon-url URL    Daemon URL
      discover            Discover free models from providers
        --provider NAME      Provider (default: openrouter)
        --daemon-url URL     Daemon URL
      discovered          List auto-discovered model profiles
        --daemon-url URL     Daemon URL

    local-serve         Start a local inference server
      --engine ENGINE     vllm|llamacpp (default: vllm)
      --model MODEL       Model name or path (required)
      --host HOST         Host (default: localhost)
      --port PORT         Port (default: 8001)
      --gpu-layers N      GPU layers (default: -1)
      --context-size N    Context size (default: 4096)
      --daemon-url URL    Daemon URL

    worktree            Worktree monitor commands
      scan                Scan for abandoned worktrees with AGENTS.md
        --path PATHS        Comma-separated paths to scan
        --daemon-url URL    Daemon URL
      status              Show tracked worktrees
        --daemon-url URL    Daemon URL

    mcp                 MCP server catalog commands
      search              Search MCP catalog
        [QUERY]             Search query
        --daemon-url URL    Daemon URL
      list                List known MCP servers
        --daemon-url URL    Daemon URL
      info                Show MCP server details
        NAME                Server name
        --daemon-url URL    Daemon URL

    skills              Skills catalog commands
      search              Search skills catalog
        [QUERY]             Search query
        --daemon-url URL    Daemon URL
      list                List all skills
        --daemon-url URL    Daemon URL
      install             Install a skill
        NAME                Skill name
        --daemon-url URL    Daemon URL

    compute             Compute endpoint commands
      endpoints           List compute endpoints
        --daemon-url URL    Daemon URL
      register            Register a compute endpoint
        --id ID             Endpoint ID
        --url URL           Endpoint URL
        --model MODEL       Model name
        --daemon-url URL    Daemon URL
      unregister          Remove a compute endpoint
        ENDPOINT_ID         Endpoint to remove
        --daemon-url URL    Daemon URL

    scores              View benchmark scores
      --task-type TYPE    Filter by task type
      --daemon-url URL    Daemon URL

    leaderboard         View prompt+model leaderboard
      --task-type TYPE    Filter by task type
      --daemon-url URL    Daemon URL

    help                Show this manual

    filestore           Filestore management commands
      list [PATH]         List filestore contents (default: /)
        --daemon-url URL    Daemon URL
      cat PATH             Read a file from filestore
        --daemon-url URL    Daemon URL
      bootstrap            Download binaries into filestore
        --binary NAME        Binary to download (default: openbao)
        --daemon-url URL     Daemon URL
      binaries             List stored binaries
        --daemon-url URL     Daemon URL

EXAMPLES
    gludd daemon
    gludd add "Fix login bug" --work-type bug_fix
    gludd status
    gludd list --queue core
    gludd models discover --provider openrouter
    gludd worktree scan --path ~/projects
    gludd mcp search github
    gludd scores --task-type code
    gludd help

ENVIRONMENT
    OPENROUTER_API_KEY   OpenRouter API key for model discovery
    OPENAI_API_KEY       OpenAI API key
    ANTHROPIC_API_KEY    Anthropic API key
    ZAI_API_KEY          Z.AI API key

FILES
    ~/.config/general-ludd/general-ludd.yml   User configuration
    ~/.cache/general-ludd/                    Cache directory

SEE ALSO
    gludd daemon --help    Daemon-specific options
    docs/quickstart.md     Getting started guide
    docs/configuration.md  Full configuration reference
"""


def _handle_connection_error(exc: Exception, daemon_url: str) -> None:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        print(
            f"Error: Cannot connect to daemon at {daemon_url}. "
            f"Is the daemon running? Start it with: gludd daemon",
            file=sys.stderr,
        )
    elif isinstance(exc, httpx.TimeoutException):
        print(f"Error: Request to daemon at {daemon_url} timed out.", file=sys.stderr)
    else:
        print(f"Error: {exc}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="gludd",
        description="General Ludd Agent — the black swan agentic coding system",
    )
    parser.set_defaults(func=None)
    sub = parser.add_subparsers(dest="command")

    daemon_parser = sub.add_parser("daemon", help="Start the daemon (server + event loop)")
    daemon_parser.add_argument("--host", default="0.0.0.0")
    daemon_parser.add_argument("--port", type=int, default=8000)
    daemon_parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    daemon_parser.add_argument("--tick-interval", type=float, default=1.0)
    daemon_parser.add_argument("--workers", type=int, default=1)
    daemon_parser.add_argument("--project", default=None, help="Default project for daemon operations")
    daemon_parser.add_argument("--config-dir", default=None, help="Path to config directory")
    daemon_parser.set_defaults(func=_cmd_daemon)

    add_parser = sub.add_parser("add", help="Add a todo to the queue")
    add_parser.add_argument("title", help="Todo title")
    add_parser.add_argument("--queue", default="core")
    add_parser.add_argument("--priority", default="medium")
    add_parser.add_argument("--work-type", default="code")
    add_parser.add_argument("--description", default="")
    add_parser.add_argument("--project", default=None, help="Project ID to add the todo to")
    add_parser.add_argument("--daemon-url", default="http://localhost:8000")
    add_parser.set_defaults(func=_cmd_add)

    status_parser = sub.add_parser("status", help="Show todo or system status")
    status_parser.add_argument("todo_id", nargs="?", default=None)
    status_parser.add_argument("--project", default=None, help="Project ID to filter by")
    status_parser.add_argument("--daemon-url", default="http://localhost:8000")
    status_parser.set_defaults(func=_cmd_status)

    list_parser = sub.add_parser("list", help="List todos")
    list_parser.add_argument("--queue", default=None)
    list_parser.add_argument("--status", default=None)
    list_parser.add_argument("--project", default=None, help="Project ID to filter by")
    list_parser.add_argument("--daemon-url", default="http://localhost:8000")
    list_parser.set_defaults(func=_cmd_list)

    log_parser = sub.add_parser("log-level", help="Change daemon log level at runtime")
    log_parser.add_argument("level", choices=["debug", "info", "warning", "error"])
    log_parser.add_argument("--daemon-url", default="http://localhost:8000")
    log_parser.set_defaults(func=_cmd_log_level)

    dep_parser = sub.add_parser("deployments", help="List active deployments")
    dep_parser.add_argument("--daemon-url", default="http://localhost:8000")
    dep_parser.set_defaults(func=_cmd_deployments)

    ver_parser = sub.add_parser("version", help="Show version")
    ver_parser.set_defaults(func=_cmd_version)

    health_parser = sub.add_parser("health", help="Check daemon health")
    health_parser.add_argument("--daemon-url", default="http://localhost:8000")
    health_parser.set_defaults(func=_cmd_health)

    models_parser = sub.add_parser("models", help="Model management commands")
    models_parser.set_defaults(func=None)
    models_sub = models_parser.add_subparsers(dest="models_command")

    models_search = models_sub.add_parser("search", help="Search HuggingFace models")
    models_search.add_argument("query", nargs="?", default="", help="Search query")
    models_search.add_argument("--limit", type=int, default=20)
    models_search.add_argument("--daemon-url", default="http://localhost:8000")
    models_search.set_defaults(func=_cmd_models_search)

    models_downloaded = models_sub.add_parser("downloaded", help="List downloaded models")
    models_downloaded.add_argument("--daemon-url", default="http://localhost:8000")
    models_downloaded.set_defaults(func=_cmd_models_downloaded)

    models_discover = models_sub.add_parser("discover", help="Discover free models from OpenRouter")
    models_discover.add_argument("--provider", default="openrouter", help="Provider to discover from")
    models_discover.add_argument("--daemon-url", default="http://localhost:8000")
    models_discover.set_defaults(func=_cmd_models_discover)

    models_list_discovered = models_sub.add_parser("discovered", help="List auto-discovered model profiles")
    models_list_discovered.add_argument("--daemon-url", default="http://localhost:8000")
    models_list_discovered.set_defaults(func=_cmd_models_discovered)

    local_serve_parser = sub.add_parser("local-serve", help="Start a local inference server")
    local_serve_parser.add_argument("--engine", default="vllm", choices=["vllm", "llamacpp"])
    local_serve_parser.add_argument("--model", required=True, help="Model name or path")
    local_serve_parser.add_argument("--host", default="localhost")
    local_serve_parser.add_argument("--port", type=int, default=8001)
    local_serve_parser.add_argument("--gpu-layers", type=int, default=-1)
    local_serve_parser.add_argument("--context-size", type=int, default=4096)
    local_serve_parser.add_argument("--daemon-url", default="http://localhost:8000")
    local_serve_parser.set_defaults(func=_cmd_local_serve)

    worktree_parser = sub.add_parser("worktree", help="Worktree monitor commands")
    worktree_parser.set_defaults(func=None)
    wt_sub = worktree_parser.add_subparsers(dest="worktree_command")

    wt_scan = wt_sub.add_parser("scan", help="Scan for abandoned worktrees with AGENTS.md")
    wt_scan.add_argument("--path", default=None, help="Comma-separated paths to scan")
    wt_scan.add_argument("--daemon-url", default="http://localhost:8000")
    wt_scan.set_defaults(func=_cmd_worktree_scan)

    wt_status = wt_sub.add_parser("status", help="Show tracked worktrees")
    wt_status.add_argument("--daemon-url", default="http://localhost:8000")
    wt_status.set_defaults(func=_cmd_worktree_status)

    project_parser = sub.add_parser("project", help="Project management commands")
    project_parser.set_defaults(func=None)
    proj_sub = project_parser.add_subparsers(dest="project_command")

    proj_add = proj_sub.add_parser("add", help="Add a project to the daemon")
    proj_add.add_argument("name", help="Project name")
    proj_add.add_argument("--repo-url", default="", help="Git repository URL")
    proj_add.add_argument("--workspace-path", default="", help="Local workspace path")
    proj_add.add_argument("--weight", type=float, default=30.0, help="Allocation weight (0-100)")
    proj_add.add_argument("--description", default="", help="Project description")
    proj_add.add_argument("--dispatch-mode", default="active",
                          choices=["active", "passive_external", "worktree_monitor"],
                          help="Dispatch: active, passive_external, or worktree_monitor")
    proj_add.add_argument("--daemon-url", default="http://localhost:8000")
    proj_add.set_defaults(func=_cmd_project_add)

    proj_list = proj_sub.add_parser("list", help="List registered projects")
    proj_list.add_argument("--daemon-url", default="http://localhost:8000")
    proj_list.set_defaults(func=_cmd_project_list)

    proj_remove = proj_sub.add_parser("remove", help="Remove a project")
    proj_remove.add_argument("project_id", help="Project ID to remove")
    proj_remove.add_argument("--daemon-url", default="http://localhost:8000")
    proj_remove.set_defaults(func=_cmd_project_remove)

    mcp_parser = sub.add_parser("mcp", help="MCP server catalog commands")
    mcp_parser.set_defaults(func=None)
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_command")

    mcp_search = mcp_sub.add_parser("search", help="Search MCP catalog")
    mcp_search.add_argument("query", nargs="?", default="", help="Search query")
    mcp_search.add_argument("--daemon-url", default="http://localhost:8000")
    mcp_search.set_defaults(func=_cmd_mcp_search)

    mcp_list = mcp_sub.add_parser("list", help="List known MCP servers")
    mcp_list.add_argument("--daemon-url", default="http://localhost:8000")
    mcp_list.set_defaults(func=_cmd_mcp_list)

    mcp_info = mcp_sub.add_parser("info", help="Show MCP server details")
    mcp_info.add_argument("name", help="Server name")
    mcp_info.add_argument("--daemon-url", default="http://localhost:8000")
    mcp_info.set_defaults(func=_cmd_mcp_info)

    skills_parser = sub.add_parser("skills", help="Skills catalog commands")
    skills_parser.set_defaults(func=None)
    skills_sub = skills_parser.add_subparsers(dest="skills_command")

    skills_search = skills_sub.add_parser("search", help="Search skills catalog")
    skills_search.add_argument("query", nargs="?", default="", help="Search query")
    skills_search.add_argument("--daemon-url", default="http://localhost:8000")
    skills_search.set_defaults(func=_cmd_skills_search)

    skills_list = skills_sub.add_parser("list", help="List all skills")
    skills_list.add_argument("--daemon-url", default="http://localhost:8000")
    skills_list.set_defaults(func=_cmd_skills_list)

    skills_install = skills_sub.add_parser("install", help="Install a skill")
    skills_install.add_argument("name", help="Skill name")
    skills_install.add_argument("--daemon-url", default="http://localhost:8000")
    skills_install.set_defaults(func=_cmd_skills_install)

    compute_parser = sub.add_parser("compute", help="Compute endpoint commands")
    compute_parser.set_defaults(func=None)
    compute_sub = compute_parser.add_subparsers(dest="compute_command")

    compute_endpoints = compute_sub.add_parser("endpoints", help="List compute endpoints")
    compute_endpoints.add_argument("--daemon-url", default="http://localhost:8000")
    compute_endpoints.set_defaults(func=_cmd_compute_endpoints)

    compute_register = compute_sub.add_parser("register", help="Register a compute endpoint")
    compute_register.add_argument("--id", required=True, help="Endpoint ID")
    compute_register.add_argument("--url", required=True, help="Endpoint URL")
    compute_register.add_argument("--model", required=True, help="Model name")
    compute_register.add_argument("--max-concurrent", type=int, default=1, help="Max concurrent requests")
    compute_register.add_argument("--daemon-url", default="http://localhost:8000")
    compute_register.set_defaults(func=_cmd_compute_register)

    compute_unregister = compute_sub.add_parser("unregister", help="Remove a compute endpoint")
    compute_unregister.add_argument("endpoint_id", help="Endpoint ID to remove")
    compute_unregister.add_argument("--daemon-url", default="http://localhost:8000")
    compute_unregister.set_defaults(func=_cmd_compute_unregister)

    scores_parser = sub.add_parser("scores", help="View benchmark scores")
    scores_parser.add_argument("--task-type", default=None, help="Filter by task type")
    scores_parser.add_argument("--daemon-url", default="http://localhost:8000")
    scores_parser.set_defaults(func=_cmd_scores)

    leaderboard_parser = sub.add_parser("leaderboard", help="View prompt+model leaderboard")
    leaderboard_parser.add_argument("--task-type", default=None, help="Filter by task type")
    leaderboard_parser.add_argument("--daemon-url", default="http://localhost:8000")
    leaderboard_parser.set_defaults(func=_cmd_leaderboard)

    help_p = sub.add_parser("help", help="Show full manual")
    help_p.set_defaults(func=_cmd_help)

    filestore_parser = sub.add_parser("filestore", help="Filestore management commands")
    filestore_parser.set_defaults(func=None)
    fs_sub = filestore_parser.add_subparsers(dest="filestore_command")

    fs_list = fs_sub.add_parser("list", help="List filestore contents")
    fs_list.add_argument("path", nargs="?", default="/", help="Path to list")
    fs_list.add_argument("--daemon-url", default="http://localhost:8000")
    fs_list.set_defaults(func=_cmd_filestore_list)

    fs_read = fs_sub.add_parser("cat", help="Read a file from filestore")
    fs_read.add_argument("path", help="Path to read")
    fs_read.add_argument("--daemon-url", default="http://localhost:8000")
    fs_read.set_defaults(func=_cmd_filestore_cat)

    fs_bootstrap = fs_sub.add_parser("bootstrap", help="Download binaries into filestore")
    fs_bootstrap.add_argument("--binary", default="openbao", help="Binary to download")
    fs_bootstrap.add_argument("--daemon-url", default="http://localhost:8000")
    fs_bootstrap.set_defaults(func=_cmd_filestore_bootstrap)

    fs_bins = fs_sub.add_parser("binaries", help="List stored binaries")
    fs_bins.add_argument("--daemon-url", default="http://localhost:8000")
    fs_bins.set_defaults(func=_cmd_filestore_binaries)

    selftest_p = sub.add_parser("selftest", help="Run self-tests via molecule scenarios")
    selftest_p.add_argument("--daemon-url", default="http://localhost:8000")
    selftest_p.set_defaults(func=_cmd_selftest)

    tui_parser = sub.add_parser("tui", help="Launch the interactive TUI dashboard")
    tui_parser.add_argument("--daemon-url", default="http://localhost:8000")
    tui_parser.set_defaults(func=_cmd_tui)

    integrity_parser = sub.add_parser("integrity", help="File integrity monitoring commands")
    int_sub = integrity_parser.add_subparsers(dest="integrity_command")

    ansible_parser = sub.add_parser("ansible", help="Ansible Galaxy and builtin module commands")
    ansible_sub = ansible_parser.add_subparsers(dest="ansible_command")
    ansible_search = ansible_sub.add_parser("search", help="Search Ansible Galaxy")
    ansible_search.add_argument("query", help="Search query")
    ansible_search.add_argument("--type", default="role", choices=["role", "collection"])
    ansible_search.add_argument("--daemon-url", default="http://localhost:8000")
    ansible_search.set_defaults(func=_cmd_ansible_search)
    ansible_install = ansible_sub.add_parser("install", help="Install from Ansible Galaxy")
    ansible_install.add_argument("name", help="Role or collection name")
    ansible_install.add_argument("--type", default="role", choices=["role", "collection"])
    ansible_install.add_argument("--daemon-url", default="http://localhost:8000")
    ansible_install.set_defaults(func=_cmd_ansible_install)
    ansible_builtins = ansible_sub.add_parser("builtins", help="List ansible.builtin modules")
    ansible_builtins.add_argument("--daemon-url", default="http://localhost:8000")
    ansible_builtins.set_defaults(func=_cmd_ansible_builtins)

    int_scan = int_sub.add_parser("scan", help="Scan files for changes")
    int_scan.add_argument("--daemon-url", default="http://localhost:8000")
    int_scan.add_argument("--paths", nargs="*", default=None, help="Paths to scan")
    int_scan.set_defaults(func=_cmd_integrity_scan)
    int_report = int_sub.add_parser("report", help="Show integrity change report")
    int_report.add_argument("--daemon-url", default="http://localhost:8000")
    int_report.set_defaults(func=_cmd_integrity_report)
    int_approve = int_sub.add_parser("approve", help="Approve an integrity change")
    int_approve.add_argument("change_id", help="File path of the change to approve")
    int_approve.add_argument("--reason", required=True, help="Reason for approval")
    int_approve.add_argument("--signer", default="admin", help="Who is signing")
    int_approve.add_argument("--daemon-url", default="http://localhost:8000")
    int_approve.set_defaults(func=_cmd_integrity_approve)
    int_reject = int_sub.add_parser("reject", help="Reject an integrity change")
    int_reject.add_argument("change_id", help="File path of the change to reject")
    int_reject.add_argument("--reason", default="Rejected", help="Reason for rejection")
    int_reject.add_argument("--daemon-url", default="http://localhost:8000")
    int_reject.set_defaults(func=_cmd_integrity_reject)
    int_log = int_sub.add_parser("log", help="Show approval/rejection log")
    int_log.add_argument("--daemon-url", default="http://localhost:8000")
    int_log.set_defaults(func=_cmd_integrity_log)

    args = parser.parse_args()
    if args.func is None:
        subcommand_map = {
            "models": models_parser,
            "mcp": mcp_parser,
            "skills": skills_parser,
            "compute": compute_parser,
            "worktree": worktree_parser,
            "filestore": filestore_parser,
            "project": project_parser,
        }
        if args.command in subcommand_map:
            subcommand_map[args.command].print_help()
            sys.exit(0)
        else:
            parser.print_help()
            sys.exit(1)
    args.func(args)


def _cmd_daemon(args: argparse.Namespace) -> None:
    import subprocess

    log_level = args.log_level.upper()
    logging.basicConfig(level=getattr(logging, log_level), format="%(asctime)s %(levelname)s %(name)s %(message)s")

    config_dir = getattr(args, "config_dir", None)

    from general_ludd.daemon import create_daemon_app

    create_daemon_app(tick_interval=args.tick_interval, log_level=args.log_level, config_dir=config_dir)

    cmd = [
        "gunicorn",
        "general_ludd.daemon:create_daemon_app()",
        "--factory",
        "--worker-class",
        "uvicorn_worker.UvicornWorker",
        "--workers",
        str(args.workers),
        "--bind",
        f"{args.host}:{args.port}",
    ]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    proc.wait()
    sys.exit(proc.returncode)


def _cmd_add(args: argparse.Namespace) -> None:
    payload: dict[str, Any] = {
        "title": args.title,
        "description": args.description,
        "queue": args.queue,
        "priority": args.priority,
        "work_type": args.work_type,
    }
    if getattr(args, "project", None):
        payload["project_id"] = args.project
    try:
        resp = httpx.post(f"{args.daemon_url}/api/todos", json=payload, timeout=10.0)
        if resp.status_code in (200, 201):
            data = resp.json()
            print(json.dumps(data, indent=2))
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _gather_offline_status(config_dir: str | None = None) -> dict[str, Any]:
    import os
    import platform
    import sys
    from pathlib import Path

    from general_ludd import __version__

    cdir = config_dir or os.environ.get("GL_CONFIG_DIR")
    if not cdir:
        home = os.path.expanduser("~")
        cdir = os.path.join(home, ".config", "gludd")
    info: dict[str, Any] = {
        "version": __version__,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "cwd": os.getcwd(),
    }
    cfiles: list[dict[str, Any]] = []
    if cdir and os.path.isdir(cdir):
        for f in sorted(os.listdir(cdir)):
            if f.endswith(".yml") or f.endswith(".yaml"):
                fp = os.path.join(cdir, f)
                try:
                    st = os.stat(fp)
                    cfiles.append({
                        "name": f,
                        "path": fp,
                        "size_bytes": st.st_size,
                        "modified": st.st_mtime,
                    })
                except OSError:
                    cfiles.append({"name": f, "path": fp, "size_bytes": 0, "modified": 0})
    info["config_dir"] = cdir
    info["config_files"] = cfiles

    from general_ludd.filestore.bootstrap import BinaryBootstrapper
    from general_ludd.filestore.store import FileStore

    store = FileStore()
    boot = BinaryBootstrapper(store=store)
    fs_root = store.root_path
    fs_exists = os.path.isdir(fs_root) if fs_root else False
    fs_size = 0
    fs_file_count = 0
    if fs_exists and fs_root:
        import contextlib
        for dirpath, _dirnames, filenames in os.walk(fs_root):
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                with contextlib.suppress(OSError):
                    fs_size += os.path.getsize(fp)
                fs_file_count += 1
    info["filestore_root"] = fs_root
    info["filestore_exists"] = fs_exists
    info["filestore_size_bytes"] = fs_size
    info["filestore_file_count"] = fs_file_count
    info["filestore_binaries"] = [b["name"] for b in boot.list_binaries()]

    from general_ludd.db.session import get_default_db_url, is_sqlite_url

    db_url = get_default_db_url()
    db_is_sqlite = is_sqlite_url(db_url)
    db_path = db_url.replace("sqlite+aiosqlite:///", "") if db_is_sqlite else db_url
    db_exists = False
    db_size = 0
    if db_is_sqlite:
        expanded = Path(db_path).expanduser()
        if expanded.exists():
            db_exists = True
            db_size = expanded.stat().st_size
    info["db_path"] = str(db_path)
    info["db_exists"] = db_exists
    info["db_size_bytes"] = db_size
    info["db_engine"] = "sqlite" if db_is_sqlite else "postgresql"

    from general_ludd.config.binary_paths import BinaryPathResolver
    resolver = BinaryPathResolver()
    info["binary_paths"] = {}
    for bname in ("podman", "docker", "ansible-playbook", "openbao"):
        label = bname.replace("-playbook", "")
        info["binary_paths"][label] = resolver.resolve(bname) if resolver.is_available(bname) else None

    info["binary_versions"] = boot.get_known_versions()
    stored = boot.list_binaries_with_versions()
    info["filestore_binaries"] = [{"name": b["binary_name"], "version": b.get("version", "?")} for b in stored]
    return info


def _format_offline_status(info: dict[str, Any]) -> None:
    print(f"General Ludd Agent v{info['version']}  (python {info['python_version']}, {info['platform']})")
    print("\u2500" * 72)
    print(f"CWD:         {info['cwd']}")
    print(f"Config dir:  {info['config_dir']}")
    for cf in info.get("config_files", []):
        s = _fmt_size(cf["size_bytes"])
        print(f"  \u251c\u2500 {cf['name']}  ({s})")
    print()
    print(f"Filestore:   {info['filestore_root']}")
    if info["filestore_exists"]:
        s = _fmt_size(info["filestore_size_bytes"])
        print(f"  Files:     {info['filestore_file_count']}  ({s})")
    else:
        print("  (not created)")
    bins = info.get("filestore_binaries", [])
    if bins:
        print("  Binaries:")
        for b in bins:
            print(f"    \u251c\u2500 {b['name']:<12} v{b.get('version', '?')}")
    versions = info.get("binary_versions", {})
    if versions:
        print("  Available versions:")
        for name, ver in sorted(versions.items()):
            stored = any(b.get("name") == name for b in bins)
            status = "stored" if stored else "not downloaded"
            print(f"    \u251c\u2500 {name:<12} v{ver:<8} [{status}]")
    print()
    print(f"Database:    {info['db_path']}")
    if info["db_exists"]:
        s = _fmt_size(info["db_size_bytes"])
        print(f"  Engine:    {info['db_engine']}  ({s})")
    else:
        print(f"  Engine:    {info['db_engine']}  (not yet created)")
    print()
    print("Binary detection:")
    for name, path in info.get("binary_paths", {}).items():
        found = "found" if path else "not found"
        print(f"  {name:<12} {found:<12} {path or ''}")


def _fmt_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / 1024 / 1024:.1f} MB"


def _cmd_status(args: argparse.Namespace) -> None:
    try:
        if args.todo_id:
            params = ""
            if getattr(args, "project", None):
                params = f"?project_id={args.project}"
            resp = httpx.get(
                f"{args.daemon_url}/api/todos/{args.todo_id}{params}", timeout=10.0,
            )
            if resp.status_code == 200:
                print(json.dumps(resp.json(), indent=2))
            else:
                print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
                sys.exit(1)
            return
        params = ""
        if getattr(args, "project", None):
            params = f"?project_id={args.project}"
        resp = httpx.get(f"{args.daemon_url}/api/status{params}", timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            print(f"General Ludd Agent v{data.get('version', 'unknown')}  [daemon running]")
            print("\u2500" * 72)
            print(f"Config dir:  {data.get('config_dir', 'not set')}")
            for cf in data.get("config_files", []):
                print(f"  \u251c\u2500 {cf}")
            print(f"Filestore:   {data.get('filestore_root', '')}")
            bins = data.get("filestore_binaries", [])
            versions = data.get("binary_versions", {})
            if versions:
                for name, ver in sorted(versions.items()):
                    stored = any(
                        (b.get("name") if isinstance(b, dict) else b) == name
                        for b in bins
                    )
                    status = "stored" if stored else "not downloaded"
                    print(f"  \u251c\u2500 {name} v{ver} [{status}]")
            print(f"DB engine:   {data.get('db_engine', 'sqlite')}")
            print(f"DB URL:      {data.get('db_url', '')}")
            print(f"Uptime:      {data.get('uptime_ticks', 0)} ticks")
            print(f"Todos:       {data.get('todos_total', 0)} total")
            print("Queue depths:")
            for q, d in sorted(data.get("queue_depths", {}).items()):
                print(f"  {q:<20} {d}")
            metrics = data.get("tick_metrics", {})
            if metrics:
                print(f"Dispatch:    {metrics.get('todos_dispatched', 0)} dispatched")
                print(f"Leases:      {metrics.get('leases_reclaimed', 0)} reclaimed")
            qg = data.get("quality_gate", {})
            overall = qg.get("overall", "not_run")
            passed = qg.get("passed_count", 0)
            total = qg.get("total_count", 0)
            print(f"\nQuality Gate: {overall} ({passed}/{total} checks)")
            for check in qg.get("checks", []):
                status_icon = "\u2713" if check.get("passed") else "\u2717"
                print(f"  {status_icon} {check['name']}")
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception:
        if getattr(args, "todo_id", None):
            _handle_connection_error(Exception("Cannot connect"), args.daemon_url)
            return
        info = _gather_offline_status()
        _format_offline_status(info)


def _cmd_list(args: argparse.Namespace) -> None:
    params: dict[str, str] = {}
    if args.queue:
        params["queue"] = args.queue
    if args.status:
        params["status"] = args.status
    if getattr(args, "project", None):
        params["project_id"] = args.project
    try:
        resp = httpx.get(f"{args.daemon_url}/api/todos", params=params, timeout=10.0)
        if resp.status_code == 200:
            print(json.dumps(resp.json(), indent=2))
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_log_level(args: argparse.Namespace) -> None:
    try:
        resp = httpx.post(f"{args.daemon_url}/admin/log-level", json={"level": args.level}, timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            print(f"Log level changed to {data['level']}")
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_deployments(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(f"{args.daemon_url}/api/deployments", timeout=10.0)
        if resp.status_code == 200:
            print(json.dumps(resp.json(), indent=2))
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_version(args: argparse.Namespace) -> None:
    from general_ludd import __version__

    print(f"general-ludd-agent {__version__}")


def _cmd_health(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(f"{args.daemon_url}/healthz", timeout=10.0)
        if resp.status_code == 200:
            print(json.dumps(resp.json(), indent=2))
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_project_add(args: argparse.Namespace) -> None:
    import json

    try:
        resp = httpx.post(
            f"{args.daemon_url}/admin/projects",
            content=json.dumps({
                "name": args.name,
                "weight": args.weight,
                "description": args.description,
                "repo_url": args.repo_url,
                "workspace_path": args.workspace_path,
                "dispatch_mode": args.dispatch_mode,
            }),
            headers={"Content-Type": "application/json"},
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"Project added: {data['project_id']} ({data['name']})")
            print(f"  Weight: {data['weight']}%  Mode: {data.get('dispatch_mode', 'active')}")
            print(f"  Repo: {data.get('repo_url', '')}")
            print(f"  Workspace: {data.get('workspace_path', '')}")
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except httpx.ConnectError as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_project_list(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(f"{args.daemon_url}/admin/projects", timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            projects = data.get("projects", [])
            if not projects:
                print("No projects registered.")
                print("Add one with: gludd project add <name> [--repo-url URL] [--workspace-path PATH]")
                print("Or configure in config/general-ludd.yml under 'projects:'")
                return
            print(f"Projects: {len(projects)}")
            for p in projects:
                mode = p.get("dispatch_mode", "active")
                active_marker = "[active]" if p.get("active") else "[inactive]"
                print(f"  {p['project_id']}  {p['name']}  {p['weight']}%  {mode}  {active_marker}")
                if p.get("repo_url"):
                    print(f"    Repo: {p['repo_url']}")
                if p.get("workspace_path"):
                    print(f"    Workspace: {p['workspace_path']}")
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except httpx.ConnectError as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_project_remove(args: argparse.Namespace) -> None:
    try:
        resp = httpx.delete(
            f"{args.daemon_url}/admin/projects/{args.project_id}", timeout=10.0,
        )
        if resp.status_code == 200:
            print(f"Project removed: {args.project_id}")
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except httpx.ConnectError as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_models_search(args: argparse.Namespace) -> None:
    try:
        resp = httpx.post(
            f"{args.daemon_url}/admin/models/search",
            json={"query": args.query, "limit": args.limit},
            timeout=30.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            if not results:
                print("No models found.")
                return
            for r in results:
                print(f"  {r['model_id']}")
                if r.get("pipeline_tag"):
                    print(f"    Task: {r['pipeline_tag']}")
                if r.get("downloads") is not None:
                    print(f"    Downloads: {r['downloads']:,}")
                print()
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_models_downloaded(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(f"{args.daemon_url}/admin/models/downloaded", timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            models = data.get("models", [])
            if not models:
                print("No models downloaded.")
                return
            for m in models:
                print(f"  {m['model_id']}")
                print(f"    Path: {m.get('local_path', 'N/A')}")
                print(f"    Engine: {m.get('engine', 'N/A')}")
                print()
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_models_discover(args: argparse.Namespace) -> None:
    try:
        resp = httpx.post(
            f"{args.daemon_url}/admin/models/discover",
            params={"provider": args.provider},
            timeout=60.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            if not data.get("success"):
                print(f"Discovery failed: {data.get('error', 'unknown')}")
                if data.get("configured"):
                    print(f"Configured providers: {', '.join(data['configured'])}")
                sys.exit(1)
            models = data.get("models", [])
            print(f"Provider: {data['provider']}")
            print(f"Discovered: {data['discovered_count']} models")
            print(f"Generated: {data['generated_profiles']} profiles")
            print(f"Free models: {sum(1 for m in models if m['is_free'])}")
            print()
            for m in models:
                free_tag = " [FREE]" if m["is_free"] else ""
                print(f"  {m['display_name']} ({m['model_name']}){free_tag}")
                cost = f"${m['cost_per_input_token']:.8f}/${m['cost_per_output_token']:.8f}"
                print(f"    Cost: {cost} | Context: {m['context_window']:,} | Quality: {m['quality_class']}")
                print(f"    Roles: {', '.join(m['role_names'])}")
                print()
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_models_discovered(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(
            f"{args.daemon_url}/admin/models/discovered",
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            profiles = data.get("profiles", [])
            if not profiles:
                print("No auto-discovered models. Run 'gludd models discover' first.")
                return
            print(f"Discovered profiles: {len(profiles)}")
            for p in profiles:
                enabled = "[enabled]" if p.get("enabled", True) else "[disabled]"
                print(f"  {p['display_name']} ({p['model_profile_id']}) {enabled}")
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_local_serve(args: argparse.Namespace) -> None:
    payload = {
        "engine": args.engine,
        "model_path": args.model,
        "model_name": args.model,
        "host": args.host,
        "port": args.port,
        "gpu_layers": args.gpu_layers,
        "context_size": args.context_size,
    }
    try:
        resp = httpx.post(
            f"{args.daemon_url}/admin/local-inference/start",
            json=payload,
            timeout=30.0,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            print(json.dumps(data, indent=2))
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_worktree_scan(args: argparse.Namespace) -> None:
    try:
        url = f"{args.daemon_url}/admin/worktree/scan"
        params: dict[str, str] = {}
        if args.path:
            params["watch_paths"] = args.path
        resp = httpx.post(url, params=params, timeout=30.0)
        if resp.status_code == 200:
            data = resp.json()
            todos = data.get("todos", [])
            tracked = data.get("tracked_count", 0)
            print(f"Tracked worktrees: {tracked}")
            print(f"Abandoned worktrees with todos: {len(todos)}")
            for todo in todos:
                print(f"  - {todo['title']} ({todo['queue']})")
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_worktree_status(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(
            f"{args.daemon_url}/admin/worktree/status",
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            wts = data.get("tracked_worktrees", [])
            print(f"Tracked worktrees: {len(wts)}")
            for wt in wts:
                status_line = f"  {wt['path']}"
                if wt["todo_id"]:
                    status_line += f" [todo: {wt['todo_id']}]"
                if wt["has_agents_md"]:
                    status_line += " [AGENTS.md]"
                print(status_line)
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_mcp_search(args: argparse.Namespace) -> None:
    try:
        resp = httpx.post(
            f"{args.daemon_url}/admin/mcp/catalog/search",
            json={"query": args.query, "limit": 20},
            timeout=30.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            if not results:
                print("No MCP servers found.")
                return
            print(f"{'name':<30} {'description':<50} {'source':<20}")
            print("-" * 100)
            for r in results:
                name = r.get("server_name", "N/A")[:29]
                description = r.get("description", "")[:49]
                source = r.get("source", "")[:19]
                print(f"{name:<30} {description:<50} {source:<20}")
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_mcp_list(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(f"{args.daemon_url}/admin/mcp/catalog/servers", timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            servers = data.get("servers", [])
            if not servers:
                print("No MCP servers known.")
                return
            for s in servers:
                print(f"  {s}")
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_mcp_info(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(
            f"{args.daemon_url}/admin/mcp/catalog/servers/{args.name}",
            timeout=10.0,
        )
        if resp.status_code == 200:
            print(json.dumps(resp.json(), indent=2))
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_skills_search(args: argparse.Namespace) -> None:
    try:
        resp = httpx.post(
            f"{args.daemon_url}/admin/skills/catalog/search",
            json={"query": args.query, "limit": 20},
            timeout=30.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            if not results:
                print("No skills found.")
                return
            print(f"{'name':<25} {'description':<40} {'category':<15} {'tags':<30}")
            print("-" * 110)
            for r in results:
                name = r.get("name", "N/A")[:24]
                description = r.get("description", "")[:39]
                category = r.get("category", "")[:14]
                tags = ", ".join(r.get("tags", []))[:29]
                print(f"{name:<25} {description:<40} {category:<15} {tags:<30}")
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_skills_list(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(f"{args.daemon_url}/admin/skills/catalog", timeout=10.0)
        if resp.status_code == 200:
            print(json.dumps(resp.json(), indent=2))
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_skills_install(args: argparse.Namespace) -> None:
    try:
        resp = httpx.post(
            f"{args.daemon_url}/admin/skills/catalog/install",
            json={"name": args.name},
            timeout=30.0,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            print(f"Installed to: {data.get('installed', 'N/A')}")
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_compute_endpoints(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(f"{args.daemon_url}/admin/compute/endpoints", timeout=10.0)
        if resp.status_code == 200:
            print(json.dumps(resp.json(), indent=2))
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_compute_register(args: argparse.Namespace) -> None:
    payload = {
        "id": args.id,
        "url": args.url,
        "model": args.model,
        "max_concurrent": args.max_concurrent,
    }
    try:
        resp = httpx.post(
            f"{args.daemon_url}/admin/compute/endpoints",
            json=payload,
            timeout=10.0,
        )
        if resp.status_code in (200, 201):
            print(json.dumps(resp.json(), indent=2))
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_compute_unregister(args: argparse.Namespace) -> None:
    try:
        resp = httpx.delete(
            f"{args.daemon_url}/admin/compute/endpoints/{args.endpoint_id}",
            timeout=10.0,
        )
        if resp.status_code in (200, 204):
            if resp.status_code == 204:
                print(f"Endpoint {args.endpoint_id} removed.")
            else:
                print(json.dumps(resp.json(), indent=2))
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_scores(args: argparse.Namespace) -> None:
    try:
        params = {}
        if args.task_type:
            params["task_type"] = args.task_type
        resp = httpx.get(
            f"{args.daemon_url}/admin/benchmark/scores",
            params=params,
            timeout=10.0,
        )
        if resp.status_code == 200:
            print(json.dumps(resp.json(), indent=2))
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_leaderboard(args: argparse.Namespace) -> None:
    try:
        params = {}
        if args.task_type:
            params["task_type"] = args.task_type
        resp = httpx.get(
            f"{args.daemon_url}/admin/benchmark/leaderboard",
            params=params,
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            entries = data.get("leaderboard", [])
            if not entries:
                print("No benchmark data yet. Run tasks to accumulate scores.")
                return
            print(
                f"{'rank':<5} {'prompt':<25} {'model':<20} "
                f"{'score':<8} {'cost':<10} {'samples':<8} {'task_type':<15}"
            )
            print("-" * 100)
            for i, e in enumerate(entries, 1):
                prompt = (e.get("prompt_profile_id") or "default")[:24]
                model = e.get("model_profile_id", "")[:19]
                score = f"{e.get('composite_score', 0):.3f}"
                cost = f"${e.get('avg_cost_usd', 0):.4f}"
                samples = str(e.get("sample_count", 0))
                tt = e.get("task_type", "")[:14]
                print(f"{i:<5} {prompt:<25} {model:<20} {score:<8} {cost:<10} {samples:<8} {tt:<15}")
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_help(args: argparse.Namespace) -> None:
    print(MAN_PAGE)
    sys.exit(0)


def _cmd_filestore_list(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(
            f"{args.daemon_url}/admin/filestore/list",
            params={"path": args.path},
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"Path: {data['path']} ({data['count']} entries)")
            for e in data["entries"]:
                tag = "[DIR]" if e["is_dir"] else f"[{e['size']}B]"
                print(f"  {tag} {e['name']}")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_filestore_cat(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(
            f"{args.daemon_url}/admin/filestore/read",
            params={"path": args.path},
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("error"):
                print(f"Error: {data['error']}", file=sys.stderr)
                sys.exit(1)
            if data.get("binary"):
                print(f"[Binary file: {data['path']}]")
            else:
                print(data.get("content", ""))
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_filestore_bootstrap(args: argparse.Namespace) -> None:
    try:
        resp = httpx.post(
            f"{args.daemon_url}/admin/filestore/bootstrap",
            params={"binary": args.binary},
            timeout=300.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data["success"]:
                print(f"Downloaded {data['binary']} to filestore")
            else:
                print(f"Failed: {data.get('error', 'unknown')}")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_filestore_binaries(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(
            f"{args.daemon_url}/admin/filestore/binaries",
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"Stored binaries: {data['count']}")
            for b in data["binaries"]:
                print(f"  {b['name']} ({b['size']}B)")
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_selftest(args: argparse.Namespace) -> None:
    try:
        resp = httpx.post(
            f"{args.daemon_url}/admin/selftest",
            timeout=120.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("podman_available"):
                print("Container runtime: podman (available)")
            else:
                print("Container runtime: podman NOT available — some tests skipped")
            print(f"Scenarios run:    {data.get('scenarios_run', 0)}")
            print(f"Scenarios passed: {data.get('scenarios_passed', 0)}")
            if data.get("errors"):
                print(f"Errors:           {len(data['errors'])}")
                for e in data["errors"]:
                    print(f"  {e}")
            for r in data.get("results", []):
                status = "PASS" if r.get("passed") else "FAIL"
                print(f"  [{status}] {r.get('scenario', 'unknown')}")
            if not data.get("success"):
                sys.exit(1)
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _build_model_table(
    servers: list[Any],
    downloaded: list[Any],
) -> Table:
    from rich.table import Table

    title = "Model Services"
    if servers and downloaded:
        title = "Model Services — Servers + Downloads"
    elif servers:
        title = "Model Services — Local Servers"
    elif downloaded:
        title = "Model Services — Downloaded Models"

    t = Table(title=title, show_header=True)
    t.add_column("ID", style="cyan")
    t.add_column("Engine", style="green")
    t.add_column("Model", style="yellow")
    t.add_column("Status / Size", style="bold")
    t.add_column("Path", style="dim")

    for s in servers:
        status_color = "green" if getattr(s, "is_running", False) else "red"
        status_text = getattr(s, "status", "stopped")
        t.add_row(
            f"[s]{s.server_id}",
            s.config.engine,
            s.config.model_name or s.config.model_path or "?",
            f"[{status_color}]{status_text}[/]",
            "",
        )

    for dm in downloaded:
        size_str = _fmt_size(dm.size_bytes) if dm.size_bytes else "?"
        model_label = dm.model_id
        if getattr(dm, "filename", None):
            model_label = f"{dm.model_id} ({dm.filename})"
        t.add_row(
            f"[d]{dm.model_id[:30]}",
            dm.engine,
            model_label,
            f"[dim]{size_str}[/]",
            dm.local_path,
        )
    return t


def _build_model_status_msg(servers: list[Any], downloaded: list[Any]) -> str:
    parts: list[str] = []
    if servers:
        parts.append(f"{len(servers)} configured")
    if downloaded:
        parts.append(f"{len(downloaded)} downloaded")
    if not parts:
        return "Model services: no servers or downloads"
    return f"Model services: {', '.join(parts)}"


_DAEMON_PID_DIR = os.path.expanduser("~/.local/share/general-ludd")
_DAEMON_PID_FILE = os.path.join(_DAEMON_PID_DIR, "daemon.pid")


def _get_daemon_pid_dir() -> str:
    os.makedirs(_DAEMON_PID_DIR, exist_ok=True)
    return _DAEMON_PID_DIR


def _write_daemon_pid_file(pid_file: str, pid: int, daemon_url: str) -> None:
    os.makedirs(os.path.dirname(pid_file), exist_ok=True)
    data = {"pid": pid, "daemon_url": daemon_url}
    with open(pid_file, "w") as f:
        json.dump(data, f)


def _read_daemon_pid_file(pid_file: str) -> dict[str, Any] | None:
    try:
        with open(pid_file) as f:
            data = json.load(f)
            if isinstance(data, dict) and "pid" in data:
                return data
    except (json.JSONDecodeError, FileNotFoundError, OSError):
        return None
    return None


def _is_daemon_pid_alive(pid_file: str) -> bool:
    data = _read_daemon_pid_file(pid_file)
    if data is None:
        return False
    try:
        os.kill(data["pid"], 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _stop_daemon_via_pid_file(pid_file: str) -> bool:
    data = _read_daemon_pid_file(pid_file)
    if data is None:
        return False
    pid = data["pid"]
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except (OSError, ProcessLookupError):
                break
        else:
            os.kill(pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    with contextlib.suppress(OSError):
        os.unlink(pid_file)
    return True


def _cmd_tui(args: argparse.Namespace) -> None:
    import os
    import select
    import subprocess
    import termios
    import tty

    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table

    daemon_proc: subprocess.Popen[bytes] | None = None
    daemon_running = False
    current_view = "main"
    status_msg = "Press q to quit, s to start daemon"

    def detect_daemon() -> bool:
        if _is_daemon_pid_alive(_DAEMON_PID_FILE):
            return True
        try:
            import httpx
            resp = httpx.get(f"{args.daemon_url}/healthz", timeout=1.0)
            return resp.status_code == 200
        except Exception:
            return False

    daemon_running = detect_daemon()
    if daemon_running:
        pid_data = _read_daemon_pid_file(_DAEMON_PID_FILE)
        if pid_data:
            args.daemon_url = pid_data.get("daemon_url", args.daemon_url)
    config_nav = _load_config_editor()
    from general_ludd.infra.local_inference import LocalInferenceManager, LocalServerConfig
    from general_ludd.models.model_registry import ModelRegistry

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
            daemon_proc = subprocess.Popen(
                [sys.executable, "-m", "general_ludd.cli", "daemon"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
            daemon_running = True
            _get_daemon_pid_dir()
            _write_daemon_pid_file(_DAEMON_PID_FILE, daemon_proc.pid, args.daemon_url)
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
                os.unlink(_DAEMON_PID_FILE)
            status_msg = "Daemon stopped"
        elif _is_daemon_pid_alive(_DAEMON_PID_FILE):
            if _stop_daemon_via_pid_file(_DAEMON_PID_FILE):
                daemon_running = False
                status_msg = "Daemon stopped via PID"
            else:
                status_msg = "Failed to stop daemon"
        elif daemon_running:
            daemon_running = False
            status_msg = "Daemon status cleared (not running)"
        else:
            status_msg = "No daemon to stop"

    def build_controls_table() -> Table:
        t = Table(title="Controls")
        t.add_column("Key", style="yellow", width=3)
        t.add_column("Action", style="cyan")
        t.add_column("Status", style="green")
        t.add_row("s", "Start daemon", "running" if daemon_running else "stopped")
        t.add_row("k", "Kill daemon", "")
        t.add_row("p", "Preflight + Projects", "")
        t.add_row("i", "Integrity scan", "")
        t.add_row("v", "Config files", current_view)
        t.add_row("r", "Refresh", "")
        t.add_row("q", "Quit", "")
        if status_msg:
            t.add_row("", f"[bold yellow]{status_msg}[/]", "")
        return t

    def build_daemon_table() -> Table:
        t = Table(title="Daemon")
        t.add_column("Key", style="cyan")
        t.add_column("Value", style="green")
        t.add_row("Status", "running" if daemon_running else "stopped")
        t.add_row("URL", args.daemon_url)
        return t

    def build_info_table(info: dict[str, Any]) -> Table:
        t = Table(title="System Info")
        t.add_column("Key", style="cyan")
        t.add_column("Value", style="green")
        t.add_row("Version", str(info.get("version", "?")))
        t.add_row("Python", str(info.get("python_version", "?")))
        t.add_row("Platform", str(info.get("platform", "?")))
        t.add_row("CWD", str(info.get("cwd", "?")))
        t.add_row("Config Dir", str(info.get("config_dir", "?")))
        t.add_row("Config Files", str(len(info.get("config_files", []))))
        t.add_row("Filestore", str(info.get("filestore_root", "?")))
        t.add_row("Filestore Size", _fmt_size(info.get("filestore_size_bytes", 0)))
        t.add_row("Filestore Files", str(info.get("filestore_file_count", 0)))
        t.add_row("DB Engine", str(info.get("db_engine", "?")))
        t.add_row("DB Path", str(info.get("db_path", "?")))
        t.add_row("DB Exists", "yes" if info.get("db_exists") else "no")
        if info.get("db_exists"):
            t.add_row("DB Size", _fmt_size(info.get("db_size_bytes", 0)))
        return t

    def build_binary_table(info: dict[str, Any]) -> Table:
        t = Table(title="Binaries")
        t.add_column("Binary", style="cyan")
        t.add_column("Found", style="green")
        t.add_column("Path", style="dim")
        for name, path in info.get("binary_paths", {}).items():
            t.add_row(name, "yes" if path else "no", str(path or ""))
        if info.get("binary_versions"):
            for name, ver in sorted(info["binary_versions"].items()):
                stored = any(b.get("name") == name for b in info.get("filestore_binaries", []))
                label = "stored" if stored else "not downloaded"
                t.add_row(name, f"v{ver} [{label}]", "")
        return t

    def build_config_table(info: dict[str, Any]) -> Table:
        t = Table(title="Config Files")
        t.add_column("File", style="cyan")
        t.add_column("Size", style="green")
        for cf in info.get("config_files", []):
            t.add_row(cf.get("name", "?"), _fmt_size(cf.get("size_bytes", 0)))
        return t

    def make_layout(info: dict[str, Any]) -> Layout:
        layout = Layout()
        layout.split(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=12),
        )
        layout["body"].split_row(
            Layout(name="left"),
            Layout(name="right"),
        )
        body = layout["body"]
        if current_view == "edit":
            body.split_row(
                Layout(name="left"),
                Layout(name="right"),
            )
            _editor_table = Table(title="Config Editor", show_header=True)
            _editor_table.add_column("Option", style="cyan", no_wrap=True)
            _editor_table.add_column("Value", style="green")
            _editor_table.add_column("Help", style="dim")
            items = config_nav["current_items"]
            sel = config_nav["selected_cat"]
            if config_nav["depth"] == 0:
                for i, cat in enumerate(items):
                    prefix = "\u25b6" if i == sel else " "
                    _editor_table.add_row(f"{prefix} [bold]{cat.name}[/]", "", "")
            else:
                for i, item in enumerate(items):
                    prefix = "\u25b6" if i == sel else " "
                    _editor_table.add_row(f"{prefix} {item.label}", str(item.value), item.help_text)
            body["left"].split(
                Layout(build_daemon_table(), name="daemon"),
            )
            body["right"].split(
                Layout(_editor_table, name="editor"),
            )
        else:
            body["left"].split(
                Layout(build_daemon_table(), name="daemon"),
                Layout(build_binary_table(info), name="binaries"),
            )
            if current_view == "config":
                body["right"].split(
                    Layout(build_config_table(info), name="config"),
                )
            elif current_view == "models":
                servers = model_mgr.list_servers()
                _model_table = _build_model_table(servers, downloaded_models)
                body["right"].split(
                    Layout(_model_table, name="models"),
                )
            elif current_view == "worktrees":
                _wt_table = Table(title="Projects & Worktrees", show_header=True)
                _wt_table.add_column("Type", style="cyan")
                _wt_table.add_column("ID/Name", style="green")
                _wt_table.add_column("Path", style="yellow")
                _wt_table.add_column("Status", style="bold")
                import os as _os
                home = _os.path.expanduser("~")
                wt_dirs = [
                    d for d in _os.listdir(home)
                    if _os.path.isdir(_os.path.join(home, d)) and not d.startswith(".")
                ]
                for d in sorted(wt_dirs)[:20]:
                    full = _os.path.join(home, d)
                    agents = _os.path.join(full, "AGENTS.md")
                    is_worktree = _os.path.isfile(agents)
                    status = "has AGENTS.md" if is_worktree else "directory"
                    _wt_table.add_row("dir", d, full, f"[{'green' if is_worktree else 'dim'}]{status}[/]")
                body["right"].split(
                    Layout(_wt_table, name="worktrees"),
                )
            elif current_view == "projects":
                _proj_table = Table(title="Registered Projects", show_header=True)
                _proj_table.add_column("ID", style="cyan")
                _proj_table.add_column("Name", style="green")
                _proj_table.add_column("Weight", style="yellow")
                _proj_table.add_column("Mode", style="bold")
                _proj_table.add_column("Repo / Workspace", style="dim")
                try:
                    resp = httpx.get(f"{args.daemon_url}/admin/projects", timeout=3.0)
                    if resp.status_code == 200:
                        data = resp.json()
                        for p in data.get("projects", []):
                            mode = p.get("dispatch_mode", "active")
                            mode_color = "green" if mode == "active" else "yellow"
                            repo = p.get("repo_url", "") or p.get("workspace_path", "") or ""
                            _proj_table.add_row(
                                p.get("project_id", "?")[:12],
                                p.get("name", "?"),
                                f"{p.get('weight', 0)}%",
                                f"[{mode_color}]{mode}[/]",
                                repo[:40],
                            )
                except Exception:
                    _proj_table.add_row("?", "Daemon not running", "", "", "Start with [s]")
                body["right"].split(
                    Layout(_proj_table, name="projects"),
                )
            else:
                body["right"].split(
                    Layout(build_info_table(info), name="info"),
                )
        if current_view == "edit":
            header_text = "Config Editor — [c] exit  [q] quit"
        elif current_view == "models":
            header_text = "Model Services — [m] exit  [q] quit"
        elif current_view == "worktrees":
            header_text = "Projects & Worktrees — [w] exit  [q] quit"
        elif current_view == "projects":
            header_text = "Registered Projects — [p] exit  [a]dd  [d]elete  [q] quit"
        elif current_view == "config":
            header_text = (
                "General Ludd Agent — TUI | [s]tart [k]ill [p]reflight [i]ntegrity"
                " [v]main [c]edit [r]efresh [q]uit"
            )
        else:
            header_text = (
                "General Ludd Agent — TUI | [s]tart [k]ill [p]reflight [i]ntegrity"
                " [v]config [c]edit [m]odels [w]trees [r]efresh [q]uit"
            )
        layout["header"].update(Panel(header_text, style="bold white on blue"))
        layout["footer"].update(build_controls_table())
        return layout

    def handle_key(info: dict[str, Any], ch: str) -> bool:
        nonlocal current_view, daemon_running, status_msg, config_nav, model_mgr
        if current_view == "edit":
            if ch in ("\t", " ", "\r", "\n"):
                ch = "\r"
            cats = config_nav["current_items"]
            if ch == "\x1b[A" and isinstance(cats, list) and len(cats) > 0:
                config_nav["selected_cat"] = max(0, config_nav["selected_cat"] - 1)
            elif ch == "\x1b[B" and isinstance(cats, list) and len(cats) > 0:
                config_nav["selected_cat"] = min(len(cats) - 1, config_nav["selected_cat"] + 1)
            elif ch in ("\t", " ", "\r", "\n"):
                if isinstance(cats, list) and 0 <= config_nav["selected_cat"] < len(cats):
                    cat = cats[config_nav["selected_cat"]]
                    if hasattr(cat, "menu_items"):
                        config_nav["current_items"] = cat.menu_items
                        config_nav["depth"] = 1
                        config_nav["selected_item"] = 0
                        config_nav["selected_cat"] = 0
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
        elif ch == "s":
            status_msg = "Starting daemon..."
            start_daemon()
        elif ch == "k":
            status_msg = "Stopping daemon..."
            stop_daemon()
        elif ch == "p":
            try:
                from general_ludd.quality.preflight import run_preflight
                result = run_preflight()
                status_msg = f"Preflight: {result['overall']} ({result['passed_count']}/{result['total_count']})"
            except Exception as exc:
                status_msg = f"Preflight error: {exc}"
            current_view = "projects" if current_view != "projects" else "main"
            if current_view == "projects":
                status_msg = "Projects — [a]dd  [d]elete  [p] exit"
        elif ch == "i":
            try:
                from general_ludd.integrity.scanner import FileIntegrityScanner
                scanner = FileIntegrityScanner()
                paths = [info.get("config_dir", ""), info.get("filestore_root", "")]
                paths = [p for p in paths if p]
                result = scanner.scan(paths) if paths else {"scanned": 0, "changes": []}
                changes = len(result["changes"])
                status_msg = f"Integrity: {result['scanned']} scanned, {changes} changes"
            except Exception as exc:
                status_msg = f"Integrity error: {exc}"
        elif ch == "v":
            current_view = "config" if current_view != "config" else "main"
        elif ch == "c":
            current_view = "edit" if current_view != "edit" else "main"
        if len(ch) == 1:
            ch = ch.lower()
        if current_view == "projects" and ch == "a":
            try:
                import json as _json
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
                _handle_connection_error(exc, args.daemon_url)
            return True
        if current_view == "projects" and ch == "d":
            try:
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
        if current_view == "edit":
                config_nav = _load_config_editor()
                status_msg = "Config editor: arrows navigate  Enter select  Esc back"
        elif ch == "m":
            current_view = "models" if current_view != "models" else "main"
            if current_view == "models":
                nonlocal downloaded_models
                downloaded_models = model_registry.list_downloaded()
                status_msg = _build_model_status_msg(model_mgr.list_servers(), downloaded_models)
        elif ch == "w":
            current_view = "worktrees" if current_view != "worktrees" else "main"
            if current_view == "worktrees":
                status_msg = "Projects & Worktrees — [w] exit  [q] quit"
        elif ch == "r":
            daemon_running = detect_daemon()
            status_msg = "Refreshed"
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
                return "\x1b"
            return data.decode("utf-8", errors="ignore") or ""
        return ""

    info = _gather_offline_status()
    console = Console()
    stdin_fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(stdin_fd)

    layout = make_layout(info)
    try:
        tty.setcbreak(stdin_fd)
        with Live(layout, console=console, refresh_per_second=4, screen=True) as live:
            while True:
                ch = getch(stdin_fd, 0.3)
                if ch:
                    if ch in ("\x03", "\x1b"):
                        break
                    if not handle_key(info, ch):
                        break
                info = _gather_offline_status()
                live.update(make_layout(info))
    finally:
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_settings)
    print("TUI exited.")


def _cmd_integrity_scan(args: argparse.Namespace) -> None:
    try:
        payload: dict[str, Any] = {}
        if args.paths:
            payload["paths"] = args.paths
        resp = httpx.post(f"{args.daemon_url}/admin/integrity/scan", json=payload, timeout=60.0)
        if resp.status_code == 200:
            data = resp.json()
            print(f"Scanned: {data.get('scanned', 0)} files")
            changes = data.get("changes", [])
            if changes:
                print(f"\nChanges detected: {len(changes)}")
                for c in changes:
                    icon = {"new": "+", "modified": "~", "removed": "-"}.get(c.get("type", ""), "?")
                    status = "approved" if c.get("approved") else "pending"
                    print(f"  {icon} {c['file']}  [{c.get('type')}] [{status}]")
            else:
                print("No changes detected.")
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception:
        info = _gather_offline_status()
        scanner = _scan_local_integrity(info)
        print(f"Local scan: {scanner['scanned']} files")
        changes = scanner.get("changes", [])
        if changes:
            print(f"Changes detected: {len(changes)}")
            for c in changes:
                icon = {"new": "+", "modified": "~", "removed": "-"}.get(c.get("type", ""), "?")
                print(f"  {icon} {c['file']}  [{c.get('type')}] [pending]")
        else:
            print("No changes detected.")


def _scan_local_integrity(info: dict[str, Any]) -> dict[str, Any]:
    import os

    from general_ludd.integrity.scanner import FileIntegrityScanner

    paths = [
        info.get("config_dir", ""),
        info.get("filestore_root", ""),
        os.path.expanduser("~/.config/gludd"),
        os.path.expanduser("~/.local/share/general-ludd"),
    ]
    paths = [p for p in paths if p and os.path.isdir(p)]
    scanner = FileIntegrityScanner()
    return scanner.scan(paths, exclude_patterns=[r"\.pyc$", r"__pycache__", r"\.git/", r"\.db$"])


def _cmd_integrity_report(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(f"{args.daemon_url}/admin/integrity/report", timeout=10.0)
        if resp.status_code == 200:
            print(json.dumps(resp.json(), indent=2))
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception:
        info = _gather_offline_status()
        scanner = _scan_local_integrity(info)
        print(json.dumps(scanner, indent=2))


def _cmd_integrity_approve(args: argparse.Namespace) -> None:
    try:
        resp = httpx.post(
            f"{args.daemon_url}/admin/integrity/approve",
            json={"path": args.change_id, "reason": args.reason, "signer": args.signer},
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"Approved: {data.get('path')}")
            print(f"Signature: {data.get('signature', '')[:16]}...")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception:
        from general_ludd.integrity.scanner import sign_change_openbao
        result = sign_change_openbao(args.change_id, args.signer, args.reason)
        print(json.dumps(result, indent=2))


def _cmd_integrity_reject(args: argparse.Namespace) -> None:
    try:
        resp = httpx.post(
            f"{args.daemon_url}/admin/integrity/reject",
            json={"path": args.change_id, "reason": args.reason},
            timeout=10.0,
        )
        if resp.status_code == 200:
            print(f"Rejected: {resp.json().get('path')}")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_integrity_log(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(f"{args.daemon_url}/admin/integrity/log", timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            for entry in data.get("entries", []):
                print(f"[{entry.get('timestamp','?')}] {entry.get('action')}: {entry.get('path')}")
                print(f"  Reason: {entry.get('reason')}  Signer: {entry.get('signer')}")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _load_config_editor() -> dict[str, Any]:
    from general_ludd.tui.config_editor import ConfigEditor

    editor = ConfigEditor()
    cats = editor.get_categories()
    return {
        "editor": editor,
        "categories": cats,
        "selected_cat": 0,
        "selected_item": 0,
        "depth": 0,
        "editing_value": False,
        "current_items": cats,
    }


def _cmd_ansible_search(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(
            f"{args.daemon_url}/admin/ansible/search",
            params={"query": args.query, "type": args.type},
            timeout=30.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            if results:
                for r in results:
                    print(f"  {r['name']:<40} {r.get('description', '')}")
            else:
                print(f"No results found for '{args.query}'")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception:
        from general_ludd.ansible.galaxy import search_galaxy
        results = search_galaxy(args.query, args.type)
        if results:
            for r in results:
                print(f"  {r['name']:<40} {r.get('description', '')}")
        else:
            print(f"No results for '{args.query}' (offline)")


def _cmd_ansible_install(args: argparse.Namespace) -> None:
    try:
        resp = httpx.post(
            f"{args.daemon_url}/admin/ansible/install",
            json={"name": args.name, "type": args.type},
            timeout=120.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            status = "OK" if data.get("success") else "FAILED"
            print(f"[{status}] {args.name}")
            print(data.get("output", ""))
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception:
        from general_ludd.ansible.galaxy import install_galaxy
        result = install_galaxy(args.name, args.type)
        status = "OK" if result.get("success") else "FAILED"
        print(f"[{status}] {args.name}")
        print(result.get("output", ""))


def _cmd_ansible_builtins(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(f"{args.daemon_url}/admin/ansible/builtins", timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            for m in data.get("modules", []):
                print(f"  {m}")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception:
        from general_ludd.ansible.galaxy import get_builtin_modules
        for m in get_builtin_modules():
            print(f"  {m}")


if __name__ == "__main__":
    main()
