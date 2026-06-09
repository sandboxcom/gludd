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

    models_list = models_sub.add_parser("list", help="List registered models")
    models_list.add_argument("--daemon-url", default="http://localhost:8000")
    models_list.set_defaults(func=_cmd_models_list)

    models_add = models_sub.add_parser("add", help="Add a model profile")
    models_add.add_argument("--model-id", required=True, help="Model ID")
    models_add.add_argument("--provider", default="openai", help="Provider name")
    models_add.add_argument("--model", default="", help="Model name")
    models_add.add_argument("--api-key-env", default=None, help="API key environment variable")
    models_add.add_argument("--daemon-url", default="http://localhost:8000")
    models_add.set_defaults(func=_cmd_models_add)

    models_remove = models_sub.add_parser("remove", help="Remove a model profile")
    models_remove.add_argument("model_id", help="Model ID to remove")
    models_remove.add_argument("--daemon-url", default="http://localhost:8000")
    models_remove.set_defaults(func=_cmd_models_remove)

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

    hooks_parser = sub.add_parser("hooks", help="Hook management commands")
    hooks_parser.set_defaults(func=None)
    hooks_sub = hooks_parser.add_subparsers(dest="hooks_command")
    hooks_list = hooks_sub.add_parser("list", help="List registered hooks")
    hooks_list.add_argument("--daemon-url", default="http://localhost:8000")
    hooks_list.set_defaults(func=_cmd_hooks_list)
    hooks_register = hooks_sub.add_parser("register", help="Register a hook")
    hooks_register.add_argument("--event", required=True, help="Event type")
    hooks_register.add_argument("--handler", required=True, help="Handler module path")
    hooks_register.add_argument("--daemon-url", default="http://localhost:8000")
    hooks_register.set_defaults(func=_cmd_hooks_register)
    hooks_delete = hooks_sub.add_parser("delete", help="Delete a hook")
    hooks_delete.add_argument("hook_id", help="Hook ID to delete")
    hooks_delete.add_argument("--daemon-url", default="http://localhost:8000")
    hooks_delete.set_defaults(func=_cmd_hooks_delete)

    workers_parser = sub.add_parser("workers", help="Worker management commands")
    workers_parser.set_defaults(func=None)
    workers_sub = workers_parser.add_subparsers(dest="workers_command")
    workers_list = workers_sub.add_parser("list", help="List workers")
    workers_list.add_argument("--daemon-url", default="http://localhost:8000")
    workers_list.set_defaults(func=_cmd_workers_list)
    workers_ping = workers_sub.add_parser("ping", help="Ping workers")
    workers_ping.add_argument("--daemon-url", default="http://localhost:8000")
    workers_ping.set_defaults(func=_cmd_workers_ping)

    agents_parser = sub.add_parser("agents", help="Agent management commands")
    agents_parser.set_defaults(func=None)
    agents_sub = agents_parser.add_subparsers(dest="agents_command")
    agents_list = agents_sub.add_parser("list", help="List agents")
    agents_list.add_argument("--daemon-url", default="http://localhost:8000")
    agents_list.set_defaults(func=_cmd_agents_list)

    metrics_parser = sub.add_parser("metrics", help="Metrics commands")
    metrics_parser.set_defaults(func=None)
    metrics_sub = metrics_parser.add_subparsers(dest="metrics_command")
    metrics_cost = metrics_sub.add_parser("cost", help="Show cost metrics")
    metrics_cost.add_argument("--daemon-url", default="http://localhost:8000")
    metrics_cost.set_defaults(func=_cmd_metrics_cost)
    metrics_report = metrics_sub.add_parser("report", help="Show full metrics report")
    metrics_report.add_argument("--daemon-url", default="http://localhost:8000")
    metrics_report.set_defaults(func=_cmd_metrics_report)

    reload_parser = sub.add_parser("reload", help="Hot-reload daemon configuration")
    reload_parser.add_argument("--scope", default="all", help="Reload scope (all, config, templates, playbooks)")
    reload_parser.add_argument("--daemon-url", default="http://localhost:8000")
    reload_parser.set_defaults(func=_cmd_reload)

    templates_parser = sub.add_parser("templates", help="Template management commands")
    templates_parser.set_defaults(func=None)
    templates_sub = templates_parser.add_subparsers(dest="templates_command")
    templates_list = templates_sub.add_parser("list", help="List templates")
    templates_list.add_argument("--daemon-url", default="http://localhost:8000")
    templates_list.set_defaults(func=_cmd_templates_list)
    templates_refresh = templates_sub.add_parser("refresh", help="Refresh template cache")
    templates_refresh.add_argument("--daemon-url", default="http://localhost:8000")
    templates_refresh.set_defaults(func=_cmd_templates_refresh)

    playbooks_parser = sub.add_parser("playbooks", help="Playbook management commands")
    playbooks_parser.set_defaults(func=None)
    playbooks_sub = playbooks_parser.add_subparsers(dest="playbooks_command")
    playbooks_list = playbooks_sub.add_parser("list", help="List playbooks")
    playbooks_list.add_argument("--daemon-url", default="http://localhost:8000")
    playbooks_list.set_defaults(func=_cmd_playbooks_list)
    playbooks_refresh = playbooks_sub.add_parser("refresh", help="Refresh playbook cache")
    playbooks_refresh.add_argument("--daemon-url", default="http://localhost:8000")
    playbooks_refresh.set_defaults(func=_cmd_playbooks_refresh)

    codeintel_parser = sub.add_parser("code", help="Code intelligence commands")
    codeintel_parser.set_defaults(func=None)
    codeintel_sub = codeintel_parser.add_subparsers(dest="code_command")
    codeintel_graph = codeintel_sub.add_parser("graph", help="Show call graph")
    codeintel_graph.add_argument("--source", default="", help="Source file")
    codeintel_graph.add_argument("--language", default="python", help="Language")
    codeintel_graph.add_argument("--daemon-url", default="http://localhost:8000")
    codeintel_graph.set_defaults(func=_cmd_code_graph)
    codeintel_search = codeintel_sub.add_parser("search", help="Search code")
    codeintel_search.add_argument("query", help="Search query")
    codeintel_search.add_argument("--language", default="python", help="Language")
    codeintel_search.add_argument("--daemon-url", default="http://localhost:8000")
    codeintel_search.set_defaults(func=_cmd_code_search)

    quant_parser = sub.add_parser("quantization", help="Model quantization detection")
    quant_parser.set_defaults(func=None)
    quant_sub = quant_parser.add_subparsers(dest="quantization_command")
    quant_list = quant_sub.add_parser("list", help="List known quantization info")
    quant_list.add_argument("--daemon-url", default="http://localhost:8000")
    quant_list.set_defaults(func=_cmd_quantization_list)
    quant_detect = quant_sub.add_parser("detect", help="Detect quantization for a model")
    quant_detect.add_argument("--model-id", required=True, help="Model ID to detect")
    quant_detect.add_argument("--daemon-url", default="http://localhost:8000")
    quant_detect.set_defaults(func=_cmd_quantization_detect)
    quant_drift = quant_sub.add_parser("drift-check", help="Check for quantization drift")
    quant_drift.add_argument("--daemon-url", default="http://localhost:8000")
    quant_drift.set_defaults(func=_cmd_quantization_drift_check)

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
            "hooks": hooks_parser,
            "workers": workers_parser,
            "agents": agents_parser,
            "metrics": metrics_parser,
            "templates": templates_parser,
            "playbooks": playbooks_parser,
            "code": codeintel_parser,
            "quantization": quant_parser,
        }
        if args.command in subcommand_map:
            subcommand_map[args.command].print_help()
            sys.exit(0)
        else:
            parser.print_help()
            sys.exit(1)
    args.func(args)


def _cmd_daemon(args: argparse.Namespace) -> None:
    import secrets
    import subprocess

    log_level = args.log_level.upper()
    logging.basicConfig(level=getattr(logging, log_level), format="%(asctime)s %(levelname)s %(name)s %(message)s")

    config_dir = getattr(args, "config_dir", None)

    from general_ludd.daemon import create_daemon_app

    create_daemon_app(tick_interval=args.tick_interval, log_level=args.log_level, config_dir=config_dir)

    bind_host = args.host
    psk = ""
    if bind_host not in ("127.0.0.1", "localhost", "::1"):
        psk = secrets.token_urlsafe(32)
        print(f"\n  Daemon binding to external interface: {bind_host}:{args.port}")
        print(f"  Pre-shared key (PSK): {psk}")
        print(f"  Clients must send: Authorization: Bearer {psk}\n")

    cmd = _build_daemon_start_cmd(host=bind_host, port=args.port, workers=args.workers)
    env = os.environ.copy()
    if psk:
        env["GLUDD_PSK"] = psk
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=env,
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


def _cmd_models_list(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(f"{args.daemon_url}/admin/models", timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            models = data.get("models", [])
            if models:
                for m in models:
                    print(f"  {m.get('model_id', '?'):<30} {m.get('provider', '?'):<12} {m.get('model', '?')}")
            else:
                print("No models registered.")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_models_add(args: argparse.Namespace) -> None:
    try:
        payload: dict[str, Any] = {
            "model_id": args.model_id,
            "provider": args.provider,
            "model": args.model,
        }
        if args.api_key_env:
            payload["api_key_env"] = args.api_key_env
        resp = httpx.post(f"{args.daemon_url}/admin/models", json=payload, timeout=10.0)
        if resp.status_code in (200, 201):
            print(f"Model added: {args.model_id}")
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_models_remove(args: argparse.Namespace) -> None:
    try:
        resp = httpx.delete(f"{args.daemon_url}/admin/models/{args.model_id}", timeout=10.0)
        if resp.status_code == 200:
            print(f"Model removed: {args.model_id}")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
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
    daemon_running: bool, status_msg: str,
    *, term_width: int = 80, selected_idx: int = -1,
) -> Table:
    from rich.table import Table

    t = Table(title="Controls", show_header=False, expand=True, title_justify="left")
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


def _build_daemon_table(daemon_running: bool, daemon_url: str, current_view: str, *, term_width: int = 80) -> Table:
    from rich.table import Table

    t = Table(title="Daemon", show_header=False, expand=True, title_justify="left")
    t.add_column("Key", style="cyan", no_wrap=True, ratio=1, min_width=6)
    val_w = max(10, term_width - _table_overhead(2) - 6)
    t.add_column("Value", style="green", no_wrap=True, ratio=3, min_width=10)
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


def _build_info_table(info: dict[str, Any], *, term_width: int = 80) -> Table:
    from rich.table import Table

    t = Table(title="System Info", show_header=False, expand=True, title_justify="left")
    t.add_column("Key", style="cyan", no_wrap=True, ratio=1, min_width=6)
    val_w = max(10, term_width - _table_overhead(2) - 6)
    t.add_column("Value", style="green", no_wrap=True, ratio=3, min_width=10)
    rows = [
        ("Version", str(info.get("version", "?"))),
        ("Python", str(info.get("python_version", "?"))),
        ("Platform", str(info.get("platform", "?"))),
        ("CWD", str(info.get("cwd", "?"))[: val_w]),
        ("Config Dir", str(info.get("config_dir", "?"))[: val_w]),
        ("Config Files", str(len(info.get("config_files", [])))),
        ("Filestore", str(info.get("filestore_root", "?"))[: val_w]),
        ("Filestore Size", _fmt_size(info.get("filestore_size_bytes", 0))),
        ("DB Engine", str(info.get("db_engine", "?"))),
        ("DB Exists", "yes" if info.get("db_exists") else "no"),
    ]
    if info.get("db_exists"):
        rows.append(("DB Size", _fmt_size(info.get("db_size_bytes", 0))))
    for k, v in rows:
        t.add_row(k, v)
    return t


def _build_binary_table(info: dict[str, Any], *, term_width: int = 80) -> Table:
    from rich.table import Table

    t = Table(title="Binaries", show_header=True, expand=True, title_justify="left")
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


def _build_config_table(info: dict[str, Any], *, term_width: int = 80) -> Table:
    from rich.table import Table

    t = Table(title="Config Files", show_header=True, expand=True, title_justify="left")
    t.add_column("File", style="cyan", no_wrap=True, ratio=3, min_width=8)
    t.add_column("Size", style="green", no_wrap=True, ratio=1, min_width=4)
    for cf in info.get("config_files", []):
        t.add_row(cf.get("name", "?"), _fmt_size(cf.get("size_bytes", 0)))
    return t


def _build_todos_table(todos: list[dict[str, Any]], *, term_width: int = 80, selected_idx: int | None = None) -> Table:
    from rich.table import Table

    t = Table(title="Todos", show_header=True, expand=True, title_justify="left")
    t.add_column("ID", style="cyan", no_wrap=True, ratio=1, min_width=4)
    t.add_column("Title", style="green", no_wrap=True, ratio=3, min_width=6)
    t.add_column("Status", style="yellow", no_wrap=True, ratio=2, min_width=4)
    t.add_column("Pri", style="bold", no_wrap=True, ratio=1, min_width=3)
    for i, todo in enumerate(todos):
        status = todo.get("status", "?")
        status_color = {
            "pending": "yellow",
            "in_progress": "cyan",
            "completed": "green",
            "cancelled": "dim",
        }.get(status, "white")
        sel_marker = "▶ " if selected_idx is not None and i == selected_idx else "  "
        style = "bold reverse" if selected_idx is not None and i == selected_idx else None
        t.add_row(
            sel_marker + str(todo.get("todo_id", "?")),
            str(todo.get("title", "")),
            f"[{status_color}]{status}[/]",
            str(todo.get("priority", "")),
            style=style,
        )
    return t


def _build_hooks_table(hooks: list[dict[str, Any]], *, term_width: int = 80, selected_idx: int | None = None) -> Table:
    from rich.table import Table

    t = Table(title="Hooks", show_header=True, expand=True, title_justify="left")
    t.add_column("ID", style="cyan", no_wrap=True, ratio=2, min_width=6)
    t.add_column("Event", style="green", no_wrap=True, ratio=2, min_width=6)
    t.add_column("Type", style="yellow", no_wrap=True, ratio=1, min_width=4)
    for i, h in enumerate(hooks):
        sel_marker = "▶ " if selected_idx is not None and i == selected_idx else "  "
        style = "bold reverse" if selected_idx is not None and i == selected_idx else None
        t.add_row(
            sel_marker + str(h.get("hook_id", "?")),
            str(h.get("event_name", h.get("event_type", "?"))),
            str(h.get("hook_type", "?")),
            style=style,
        )
    return t


def _build_workers_table(
    workers: list[dict[str, Any]],
    *,
    term_width: int = 80,
    selected_idx: int | None = None,
) -> Table:
    from rich.table import Table

    t = Table(title="Workers", show_header=True, expand=True, title_justify="left")
    t.add_column("ID", style="cyan", no_wrap=True, ratio=2, min_width=6)
    t.add_column("Address", style="green", no_wrap=True, ratio=3, min_width=8)
    for i, w in enumerate(workers):
        sel_marker = "▶ " if selected_idx is not None and i == selected_idx else "  "
        style = "bold reverse" if selected_idx is not None and i == selected_idx else None
        t.add_row(
            sel_marker + str(w.get("worker_id", "?")),
            str(w.get("address", "?")),
            style=style,
        )
    return t


def _build_metrics_table(cost_data: dict[str, Any], *, term_width: int = 80) -> Table:
    from rich.table import Table

    t = Table(title="Metrics", show_header=False, expand=True, title_justify="left")
    t.add_column("Metric", style="cyan", no_wrap=True, ratio=2, min_width=6)
    t.add_column("Value", style="green", no_wrap=True, ratio=2, min_width=6)
    labels = [
        ("Total Cost", "total_cost_usd", "${:.2f}"),
        ("Subscription", "subscription_name", "{}"),
        ("Sub Cost/Mo", "subscription_cost_usd_per_month", "${:.2f}"),
        ("Tokens Used", "tokens_used", "{:,}"),
        ("Tokens Left", "tokens_remaining_this_week", "{:,}"),
        ("Cost % Sub", "cost_as_pct_of_subscription", "{:.1f}%"),
        ("Tokens % Wk", "tokens_as_pct_of_weekly", "{:.1f}%"),
    ]
    for label, key, fmt in labels:
        val = cost_data.get(key)
        if val is not None:
            if isinstance(val, (int, float)):
                try:
                    t.add_row(label, fmt.format(val))
                except (ValueError, TypeError):
                    t.add_row(label, str(val))
            else:
                t.add_row(label, str(val))
    return t


def _build_agents_table(agents: list[dict[str, Any]], *, term_width: int = 80) -> Table:
    from rich.table import Table

    t = Table(title="Agents", show_header=True, expand=True, title_justify="left")
    t.add_column("ID", style="cyan", no_wrap=True, ratio=1, min_width=4)
    t.add_column("Name", style="green", no_wrap=True, ratio=2, min_width=5)
    t.add_column("Status", style="yellow", no_wrap=True, ratio=1, min_width=4)
    t.add_column("Project", style="bold", no_wrap=True, ratio=1, min_width=5)
    t.add_column("Up", style="dim", no_wrap=True, ratio=1, min_width=4)
    for a in agents:
        status = a.get("status", "?")
        status_color = "green" if status == "running" else "yellow" if status == "idle" else "red"
        uptime_s = a.get("uptime_seconds", 0)
        uptime_h = uptime_s // 3600
        uptime_m = (uptime_s % 3600) // 60
        t.add_row(
            str(a.get("agent_id", "?")),
            str(a.get("agent_name", a.get("name", "?"))),
            f"[{status_color}]{status}[/]",
            str(a.get("project", "")),
            f"{uptime_h}h{uptime_m}m",
        )
    return t


def _build_model_table(
    servers: list[Any],
    downloaded: list[Any],
    *,
    term_width: int = 80,
    selected_idx: int | None = None,
) -> Table:
    from rich.table import Table

    t = Table(title="Models", show_header=True, expand=True, title_justify="left")
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
    term_width: int = 80,
) -> Table:
    from rich.table import Table

    t = Table(title="Config Editor", show_header=True, expand=True, title_justify="left")
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


def _build_worktrees_table(entries: list[tuple[str, str]], *, term_width: int = 80) -> Table:
    from rich.table import Table

    t = Table(title="Projects & Worktrees", show_header=True, expand=True, title_justify="left")
    t.add_column("Name", style="green", no_wrap=True, ratio=3, min_width=6)
    t.add_column("Status", style="bold", no_wrap=True, ratio=2, min_width=6)
    for name, status in entries:
        is_wt = "AGENTS.md" in status
        color = "green" if is_wt else "dim"
        t.add_row(name, f"[{color}]{status}[/]")
    return t


def _build_projects_table(
    projects: list[dict[str, Any]],
    *,
    term_width: int = 80,
    selected_idx: int | None = None,
) -> Table:
    from rich.table import Table

    t = Table(title="Projects", show_header=True, expand=True, title_justify="left")
    t.add_column("ID", style="cyan", no_wrap=True, ratio=1, min_width=5)
    t.add_column("Name", style="green", no_wrap=True, ratio=2, min_width=6)
    t.add_column("Wt", style="yellow", no_wrap=True, ratio=1, min_width=3)
    t.add_column("Mode", style="bold", no_wrap=True, ratio=1, min_width=4)
    for i, p in enumerate(projects):
        mode = str(p.get("dispatch_mode", "active"))
        mode_color = "green" if mode == "active" else "yellow"
        sel_marker = "▶ " if selected_idx is not None and i == selected_idx else "  "
        style = "bold reverse" if selected_idx is not None and i == selected_idx else None
        t.add_row(
            sel_marker + str(p.get("project_id", "?")),
            str(p.get("name", "?")),
            f"{p.get('weight', 0)}%",
            f"[{mode_color}]{mode}[/]",
            style=style,
        )
    return t


def _build_integrity_table(changes: list[dict[str, Any]], *, term_width: int = 80) -> Table:
    from rich.table import Table

    t = Table(title="Integrity", show_header=True, expand=True, title_justify="left")
    t.add_column("File", style="cyan", no_wrap=True, ratio=3, min_width=6)
    t.add_column("Type", style="yellow", no_wrap=True, ratio=1, min_width=4)
    t.add_column("Status", style="bold", no_wrap=True, ratio=1, min_width=4)
    if not changes:
        t.add_row("No changes", "", "")
    else:
        for ch in changes:
            icon = {"new": "+", "modified": "~", "removed": "-"}.get(ch.get("type", ""), "?")
            approved = "approved" if ch.get("approved") else "pending"
            t.add_row(
                str(ch.get("file", "?")),
                f"{icon} {ch.get('type', '?')}",
                approved,
            )
    return t


def _build_ansible_table(results: list[dict[str, Any]], *, term_width: int = 80) -> Table:
    from rich.table import Table

    t = Table(title="Ansible Galaxy", show_header=True, expand=True, title_justify="left")
    t.add_column("Name", style="cyan", no_wrap=True, ratio=2, min_width=6)
    t.add_column("Description", style="green", no_wrap=True, ratio=3, min_width=8)
    if not results:
        t.add_row("Press [s] to search", "")
    else:
        for r in results:
            t.add_row(
                str(r.get("name", "?")),
                str(r.get("description", "")),
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


def _build_mcp_table(servers: list[dict[str, Any]], *, term_width: int = 80) -> Table:
    from rich.table import Table

    t = Table(title="MCP Servers", show_header=True, expand=True, title_justify="left")
    t.add_column("Name", style="cyan", no_wrap=True, ratio=2, min_width=6)
    t.add_column("Transport", style="green", no_wrap=True, ratio=1, min_width=4)
    t.add_column("Status", style="yellow", no_wrap=True, ratio=1, min_width=4)
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


def _build_skills_table(skills: list[dict[str, Any]], *, term_width: int = 80) -> Table:
    from rich.table import Table

    t = Table(title="Skills", show_header=True, expand=True, title_justify="left")
    t.add_column("Name", style="cyan", no_wrap=True, ratio=2, min_width=6)
    t.add_column("Category", style="green", no_wrap=True, ratio=1, min_width=4)
    t.add_column("Installed", style="yellow", no_wrap=True, ratio=1, min_width=3)
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


def _build_compute_table(endpoints: list[dict[str, Any]], *, term_width: int = 80) -> Table:
    from rich.table import Table

    t = Table(title="Compute Endpoints", show_header=True, expand=True, title_justify="left")
    t.add_column("ID", style="cyan", no_wrap=True, ratio=2, min_width=6)
    t.add_column("Provider", style="green", no_wrap=True, ratio=1, min_width=4)
    t.add_column("Status", style="yellow", no_wrap=True, ratio=1, min_width=4)
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


def _build_scores_table(scores: list[dict[str, Any]], *, term_width: int = 80) -> Table:
    from rich.table import Table

    t = Table(title="Benchmark Scores", show_header=True, expand=True, title_justify="left")
    t.add_column("Prompt", style="cyan", no_wrap=True, ratio=2, min_width=6)
    t.add_column("Model", style="green", no_wrap=True, ratio=2, min_width=6)
    t.add_column("Task", style="yellow", no_wrap=True, ratio=1, min_width=4)
    t.add_column("Score", style="bold", no_wrap=True, ratio=1, min_width=3)
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


def _build_leaderboard_table(entries: list[dict[str, Any]], *, term_width: int = 80) -> Table:
    from rich.table import Table

    t = Table(title="Leaderboard", show_header=True, expand=True, title_justify="left")
    t.add_column("#", style="bold", no_wrap=True, ratio=1, min_width=3)
    t.add_column("Prompt", style="cyan", no_wrap=True, ratio=2, min_width=6)
    t.add_column("Model", style="green", no_wrap=True, ratio=2, min_width=6)
    t.add_column("Score", style="yellow", no_wrap=True, ratio=1, min_width=3)
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


def _build_templates_table(templates: list[dict[str, Any]], *, term_width: int = 80) -> Table:
    from rich.table import Table

    t = Table(title="Templates", show_header=True, expand=True, title_justify="left")
    t.add_column("Name", style="cyan", no_wrap=True, ratio=2, min_width=6)
    t.add_column("Task Types", style="green", no_wrap=True, ratio=3, min_width=6)
    t.add_column("Source", style="yellow", no_wrap=True, ratio=1, min_width=4)
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


def _build_playbooks_table(playbooks: list[dict[str, Any]], *, term_width: int = 80) -> Table:
    from rich.table import Table

    t = Table(title="Playbooks", show_header=True, expand=True, title_justify="left")
    t.add_column("Name", style="cyan", no_wrap=True, ratio=3, min_width=6)
    t.add_column("Tasks", style="green", no_wrap=True, ratio=1, min_width=3)
    t.add_column("Status", style="yellow", no_wrap=True, ratio=1, min_width=4)
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


def _build_quantization_table(entries: list[dict[str, Any]], *, term_width: int = 80) -> Table:
    from rich.table import Table

    t = Table(title="Quantization", show_header=True, expand=True, title_justify="left")
    t.add_column("Model", style="cyan", no_wrap=True, ratio=3, min_width=6)
    t.add_column("Precision", style="green", no_wrap=True, ratio=1, min_width=4)
    t.add_column("Conf", style="yellow", no_wrap=True, ratio=1, min_width=3)
    t.add_column("Source", style="dim", no_wrap=True, ratio=1, min_width=4)
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


def _build_filestore_table(files: list[dict[str, Any]], *, term_width: int = 80) -> Table:
    from rich.table import Table

    t = Table(title="Filestore", show_header=True, expand=True, title_justify="left")
    t.add_column("Name", style="cyan", no_wrap=True, ratio=3, min_width=6)
    t.add_column("Size", style="green", no_wrap=True, ratio=1, min_width=4)
    t.add_column("Type", style="yellow", no_wrap=True, ratio=1, min_width=4)
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


def _build_deployments_table(deployments: list[dict[str, Any]], *, term_width: int = 80) -> Table:
    from rich.table import Table

    t = Table(title="Deployments", show_header=True, expand=True, title_justify="left")
    t.add_column("Name", style="cyan", no_wrap=True, ratio=2, min_width=6)
    t.add_column("Provider", style="green", no_wrap=True, ratio=1, min_width=4)
    t.add_column("Status", style="yellow", no_wrap=True, ratio=1, min_width=4)
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


def _build_daemon_start_cmd(
    host: str = "0.0.0.0",
    port: int = 8000,
    workers: int = 1,
) -> list[str]:
    return [
        "gunicorn",
        "general_ludd.daemon:create_daemon_app()",
        "--worker-class",
        "uvicorn_worker.UvicornWorker",
        "--workers",
        str(workers),
        "--bind",
        f"{host}:{port}",
    ]


def _cmd_tui(args: argparse.Namespace) -> None:
    import os
    import select
    import subprocess
    import termios
    import time
    import tty

    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel

    from general_ludd.tui.keybindings import TUIKeyHandler

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

    from general_ludd.tui.breadcrumb import pop_breadcrumb, push_breadcrumb, render_breadcrumb
    from general_ludd.tui.logger import TUILogger
    _tui_log_dir = os.path.join(_get_daemon_pid_dir(), "tui_logs")
    tui_logger = TUILogger(log_dir=_tui_log_dir, daemon_url=args.daemon_url, verbose=False)

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
            cmd = _build_daemon_start_cmd(
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
            _get_daemon_pid_dir()
            _write_daemon_pid_file(_DAEMON_PID_FILE, daemon_proc.pid, args.daemon_url)
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
        import shutil as _shutil2
        _tw, _ = _shutil2.get_terminal_size((80, 24))
        sel_idx = tui_state.get("selected_main_idx", -1) if current_view == "main" else -1
        return _build_controls_table(daemon_running, status_msg, term_width=_tw, selected_idx=sel_idx)

    def build_daemon_table(*, term_width: int = 80) -> Table:
        return _build_daemon_table(daemon_running, args.daemon_url, current_view, term_width=term_width)

    def build_info_table(info: dict[str, Any], *, term_width: int = 80) -> Table:
        return _build_info_table(info, term_width=term_width)

    def build_binary_table(info: dict[str, Any], *, term_width: int = 80) -> Table:
        return _build_binary_table(info, term_width=term_width)

    def build_config_table(info: dict[str, Any], *, term_width: int = 80) -> Table:
        return _build_config_table(info, term_width=term_width)

    def make_layout(info: dict[str, Any]) -> Layout:
        import shutil as _shutil

        _term_w, _term_h = _shutil.get_terminal_size((80, 24))
        footer_rows = _compute_footer_rows(_term_h)
        header_rows = 1
        left_width, right_width = _compute_panel_widths(_term_w, tui_state)

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
            _editor_table = _build_config_editor_table(dict_items, sel, depth, term_width=right_width)
            body["left"].split(
                Layout(_wrap_table(build_daemon_table(term_width=left_width)), name="daemon"),
            )
            body["right"].split(
                Layout(_wrap_table(_editor_table), name="editor"),
            )
        else:
            body["left"].split(
                Layout(_wrap_table(build_daemon_table(term_width=left_width)), name="daemon"),
                Layout(_wrap_table(build_binary_table(info, term_width=left_width)), name="binaries"),
            )
            if current_view == "config":
                body["right"].split(
                    Layout(_wrap_table(build_config_table(info, term_width=right_width)), name="config"),
                )
            elif current_view == "models":
                servers = model_mgr.list_servers()
                _model_table = _build_model_table(
                    servers, downloaded_models,
                    selected_idx=tui_state.get("selected_model_idx", 0),
                    term_width=right_width,
                )
                body["right"].split(
                    Layout(_wrap_table(_model_table), name="models"),
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
                _wt_table = _build_worktrees_table(wt_entries, term_width=right_width)
                body["right"].split(
                    Layout(_wrap_table(_wt_table), name="worktrees"),
                )
            elif current_view == "projects":
                _proj_data: list[dict[str, Any]] = []
                try:
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
                _proj_table = _build_projects_table(_proj_data, selected_idx=_proj_sel, term_width=right_width)
                body["right"].split(
                    Layout(_wrap_table(_proj_table), name="projects"),
                )
            elif current_view == "todos":
                _todos_data: list[dict[str, Any]] = []
                try:
                    resp = httpx.get(f"{args.daemon_url}/admin/todos", timeout=3.0)
                    if resp.status_code == 200:
                        _todos_data = resp.json().get("todos", [])
                except Exception:
                    pass
                tui_state["todos_data"] = _todos_data
                _todos_sel = tui_state.get("selected_todo_idx", 0)
                body["right"].split(
                    Layout(_wrap_table(_build_todos_table(_todos_data, selected_idx=_todos_sel)), name="todos"),
                )
            elif current_view == "hooks":
                _hooks_data: list[dict[str, Any]] = []
                try:
                    resp = httpx.get(f"{args.daemon_url}/admin/hooks", timeout=3.0)
                    if resp.status_code == 200:
                        _hooks_data = resp.json().get("hooks", [])
                except Exception:
                    pass
                tui_state["hooks_data"] = _hooks_data
                _hooks_sel = tui_state.get("selected_hook_idx", 0)
                body["right"].split(
                    Layout(_wrap_table(_build_hooks_table(_hooks_data, selected_idx=_hooks_sel)), name="hooks"),
                )
            elif current_view == "workers":
                _workers_data: list[dict[str, Any]] = []
                try:
                    resp = httpx.get(f"{args.daemon_url}/admin/workers", timeout=3.0)
                    if resp.status_code == 200:
                        _workers_data = resp.json().get("workers", [])
                except Exception:
                    pass
                tui_state["workers_data"] = _workers_data
                _workers_sel = tui_state.get("selected_worker_idx", 0)
                body["right"].split(
                    Layout(_wrap_table(_build_workers_table(_workers_data, selected_idx=_workers_sel)), name="workers"),
                )
            elif current_view == "metrics":
                _cost_data: dict[str, Any] = {}
                try:
                    resp = httpx.get(f"{args.daemon_url}/admin/metrics/cost", timeout=3.0)
                    if resp.status_code == 200:
                        _cost_data = resp.json()
                except Exception:
                    pass
                body["right"].split(
                    Layout(_wrap_table(_build_metrics_table(_cost_data)), name="metrics"),
                )
            elif current_view == "agents":
                _agents_data: list[dict[str, Any]] = []
                try:
                    resp = httpx.get(f"{args.daemon_url}/admin/agents", timeout=3.0)
                    if resp.status_code == 200:
                        _agents_data = resp.json().get("agents", [])
                except Exception:
                    pass
                body["right"].split(
                    Layout(_wrap_table(_build_agents_table(_agents_data)), name="agents"),
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
                _int_table = _build_integrity_table(_int_changes, term_width=right_width)
                body["right"].split(
                    Layout(_wrap_table(_int_table), name="integrity"),
                )
            elif current_view == "ansible":
                _ans_results = tui_state.get("ansible_search_results", [])
                _ans_table = _build_ansible_table(_ans_results, term_width=right_width)
                body["right"].split(
                    Layout(_wrap_table(_ans_table), name="ansible"),
                )
            elif current_view == "mcp":
                _mcp_data: list[dict[str, Any]] = []
                try:
                    resp = httpx.get(f"{args.daemon_url}/admin/mcp/list", timeout=3.0)
                    if resp.status_code == 200:
                        _mcp_data = resp.json().get("servers", resp.json().get("results", []))
                except Exception:
                    pass
                body["right"].split(
                    Layout(_wrap_table(_build_mcp_table(_mcp_data, term_width=right_width)), name="mcp"),
                )
            elif current_view == "skills":
                _skills_data: list[dict[str, Any]] = []
                try:
                    resp = httpx.get(f"{args.daemon_url}/admin/skills/catalog", timeout=3.0)
                    if resp.status_code == 200:
                        _skills_data = resp.json().get("skills", resp.json().get("results", []))
                except Exception:
                    pass
                body["right"].split(
                    Layout(_wrap_table(_build_skills_table(_skills_data, term_width=right_width)), name="skills"),
                )
            elif current_view == "compute":
                _compute_data: list[dict[str, Any]] = []
                try:
                    resp = httpx.get(f"{args.daemon_url}/admin/compute/endpoints", timeout=3.0)
                    if resp.status_code == 200:
                        _compute_data = resp.json().get("endpoints", [])
                except Exception:
                    pass
                body["right"].split(
                    Layout(_wrap_table(_build_compute_table(_compute_data, term_width=right_width)), name="compute"),
                )
            elif current_view == "scores":
                _scores_data: list[dict[str, Any]] = []
                try:
                    resp = httpx.get(f"{args.daemon_url}/admin/benchmark/scores", timeout=3.0)
                    if resp.status_code == 200:
                        _scores_data = resp.json().get("scores", resp.json().get("results", []))
                except Exception:
                    pass
                body["right"].split(
                    Layout(_wrap_table(_build_scores_table(_scores_data, term_width=right_width)), name="scores"),
                )
            elif current_view == "templates":
                _templates_data: list[dict[str, Any]] = []
                try:
                    resp = httpx.get(f"{args.daemon_url}/admin/templates", timeout=3.0)
                    if resp.status_code == 200:
                        _templates_data = resp.json().get("templates", resp.json().get("profiles", []))
                except Exception:
                    pass
                body["right"].split(
                    Layout(
                        _wrap_table(_build_templates_table(
                            _templates_data, term_width=right_width,
                        )),
                        name="templates",
                    ),
                )
            elif current_view == "quantization":
                _quant_data: list[dict[str, Any]] = []
                try:
                    resp = httpx.get(f"{args.daemon_url}/admin/quantization", timeout=3.0)
                    if resp.status_code == 200:
                        _quant_data = resp.json().get("entries", resp.json().get("results", []))
                except Exception:
                    pass
                body["right"].split(
                    Layout(
                        _wrap_table(_build_quantization_table(
                            _quant_data, term_width=right_width,
                        )),
                        name="quantization",
                    ),
                )
            elif current_view == "filestore":
                _fs_data: list[dict[str, Any]] = []
                try:
                    resp = httpx.get(f"{args.daemon_url}/admin/filestore/list", timeout=3.0)
                    if resp.status_code == 200:
                        _fs_data = resp.json().get("files", resp.json().get("entries", []))
                except Exception:
                    pass
                body["right"].split(
                    Layout(_wrap_table(_build_filestore_table(_fs_data, term_width=right_width)), name="filestore"),
                )
            elif current_view == "deployments":
                _deploy_data: list[dict[str, Any]] = []
                try:
                    resp = httpx.get(f"{args.daemon_url}/admin/deployments", timeout=3.0)
                    if resp.status_code == 200:
                        _deploy_data = resp.json().get("deployments", [])
                except Exception:
                    pass
                body["right"].split(
                    Layout(
                        _wrap_table(_build_deployments_table(
                            _deploy_data, term_width=right_width,
                        )),
                        name="deployments",
                    ),
                )
            elif current_view == "leaderboard":
                _lb_data: list[dict[str, Any]] = []
                try:
                    resp = httpx.get(f"{args.daemon_url}/admin/benchmark/leaderboard", timeout=3.0)
                    if resp.status_code == 200:
                        _lb_data = resp.json().get("leaderboard", resp.json().get("entries", []))
                except Exception:
                    pass
                body["right"].split(
                    Layout(_wrap_table(_build_leaderboard_table(_lb_data, term_width=right_width)), name="leaderboard"),
                )
            elif current_view == "playbooks":
                _pb_data: list[dict[str, Any]] = []
                try:
                    resp = httpx.get(f"{args.daemon_url}/admin/playbooks", timeout=3.0)
                    if resp.status_code == 200:
                        _pb_data = resp.json().get("playbooks", resp.json().get("entries", []))
                except Exception:
                    pass
                body["right"].split(
                    Layout(_wrap_table(_build_playbooks_table(_pb_data, term_width=right_width)), name="playbooks"),
                )
            else:
                body["right"].split(
                    Layout(_wrap_table(build_info_table(info, term_width=right_width)), name="info"),
                )
        if current_view == "edit":
            header_text = "Config Editor — [c] exit  [q] quit"
        elif current_view == "models":
            if tui_state.get("input_mode") == "models_add":
                _idx = tui_state.get("input_field_index", 0)
                _fields = tui_state.get("input_fields", [])
                _label = _fields[_idx]["label"] if _idx < len(_fields) else "?"
                header_text = (
                    f"Add Model — enter {_label}: "
                    f"{tui_state.get('input_buffer', '')}_ "
                    "— [Enter] next [Esc] cancel"
                )
            else:
                header_text = "Model Services — [m] exit  [a]dd  [q] quit"
        elif current_view == "worktrees":
            header_text = "Projects & Worktrees — [w] exit  [q] quit"
        elif current_view == "projects":
            header_text = "Registered Projects — [p] exit  [a]dd  [d]elete  [q] quit"
        elif current_view == "todos":
            header_text = "Todos — [t] exit  [q] quit"
        elif current_view == "hooks":
            header_text = "Hooks — [h] exit  [q] quit"
        elif current_view == "workers":
            header_text = "Workers — [o] exit  [q] quit"
        elif current_view == "metrics":
            header_text = "Metrics — [x] exit  [q] quit"
        elif current_view == "agents":
            header_text = "Agents — [g] exit  [q] quit"
        elif current_view == "integrity":
            header_text = "Integrity — [i] exit  [q] quit"
        elif current_view == "ansible":
            if tui_state.get("input_mode") == "ansible_search":
                header_text = f"Search Galaxy: {tui_state.get('input_buffer', '')}_ — [Enter] search [Esc] cancel"
            else:
                header_text = "Ansible Galaxy — [a] exit  [s]earch  [q] quit"
        elif current_view == "mcp":
            if tui_state.get("input_mode") == "mcp_search":
                header_text = f"Search MCP: {tui_state.get('input_buffer', '')}_ — [Enter] search [Esc] cancel"
            else:
                header_text = "MCP Servers — [u] exit  [s]earch  [q] quit"
        elif current_view == "skills":
            if tui_state.get("input_mode") == "skills_search":
                header_text = f"Search Skills: {tui_state.get('input_buffer', '')}_ — [Enter] search [Esc] cancel"
            else:
                header_text = "Skills — [j] exit  [s]earch  [i]nstall  [q] quit"
        elif current_view == "compute":
            if tui_state.get("input_mode") == "compute_register":
                header_text = f"Register: {tui_state.get('input_buffer', '')}_ — [Enter] next [Esc] cancel"
            else:
                header_text = "Compute — [e] exit  [a]dd  [q] quit"
        elif current_view == "scores":
            header_text = "Scores — [b] exit  [q] quit"
        elif current_view == "templates":
            header_text = "Templates — [l] exit  [r]efresh  [q] quit"
        elif current_view == "quantization":
            header_text = "Quantization — [n] exit  [d]etect  [q] quit"
        elif current_view == "filestore":
            header_text = "Filestore — [f] exit  [q] quit"
        elif current_view == "deployments":
            header_text = "Deployments — [z] exit  [q] quit"
        elif current_view == "leaderboard":
            header_text = "Leaderboard — [y] exit  [q] quit"
        elif current_view == "playbooks":
            header_text = "Playbooks — [r]efresh  [P] exit  [q] quit"
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
                status_msg = "Projects — [a]dd  [d]elete  [p] exit"
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
                status_msg = _build_model_status_msg(model_mgr.list_servers(), downloaded_models)
            else:
                current_view = pop_breadcrumb(tui_state)
        elif ch == "w":
            if current_view != "worktrees":
                current_view = "worktrees"
                push_breadcrumb(tui_state, "worktrees")
                status_msg = "Projects & Worktrees — [w] exit  [q] quit"
            else:
                current_view = pop_breadcrumb(tui_state)
        elif ch == "t":
            if current_view != "todos":
                current_view = "todos"
                push_breadcrumb(tui_state, "todos")
                status_msg = "Todos — [t] exit  [a]dd  [q] quit"
            else:
                current_view = pop_breadcrumb(tui_state)
        elif ch == "h":
            if current_view != "hooks":
                current_view = "hooks"
                push_breadcrumb(tui_state, "hooks")
                status_msg = "Hooks — [h] exit  [q] quit"
            else:
                current_view = pop_breadcrumb(tui_state)
        elif ch == "o":
            if current_view != "workers":
                current_view = "workers"
                push_breadcrumb(tui_state, "workers")
                status_msg = "Workers — [o] exit  [p]ing  [q] quit"
            else:
                current_view = pop_breadcrumb(tui_state)
        elif ch == "x":
            if current_view != "metrics":
                current_view = "metrics"
                push_breadcrumb(tui_state, "metrics")
                status_msg = "Metrics — [x] exit  [q] quit"
            else:
                current_view = pop_breadcrumb(tui_state)
        elif ch == "g":
            if current_view != "agents":
                current_view = "agents"
                push_breadcrumb(tui_state, "agents")
                status_msg = "Agents — [g] exit  [q] quit"
            else:
                current_view = pop_breadcrumb(tui_state)
        elif ch == "r":
            daemon_running = detect_daemon()
            status_msg = "Refreshed"
        elif ch in ("u", "j", "e", "b", "l", "n", "f", "z", "y", "P", "R") or current_view in (
            "todos", "workers", "models", "mcp", "skills", "compute",
            "projects", "hooks", "integrity", "agents",
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

    info = _gather_offline_status()
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
                            info = _gather_offline_status()
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
                info = _gather_offline_status()
                live.update(make_layout(info))
    finally:
        sys.stdout.write("\x1b[?1002l")
        sys.stdout.flush()
        tui_logger.close()
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_settings)
    print("TUI exited.")


def _cmd_hooks_list(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(f"{args.daemon_url}/admin/hooks", timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            hooks = data.get("hooks", [])
            if hooks:
                for h in hooks:
                    print(f"  {h.get('hook_id', '?'):<20} {h.get('event', '?'):<20} {h.get('handler', '?')}")
            else:
                print("No hooks registered.")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_hooks_register(args: argparse.Namespace) -> None:
    try:
        resp = httpx.post(
            f"{args.daemon_url}/admin/hooks",
            json={"event": args.event, "handler": args.handler},
            timeout=10.0,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            print(f"Hook registered: {data.get('hook_id', '?')}")
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_hooks_delete(args: argparse.Namespace) -> None:
    try:
        resp = httpx.delete(f"{args.daemon_url}/admin/hooks/{args.hook_id}", timeout=10.0)
        if resp.status_code == 200:
            print(f"Hook deleted: {args.hook_id}")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_workers_list(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(f"{args.daemon_url}/admin/workers", timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            workers = data.get("workers", [])
            if workers:
                for w in workers:
                    print(f"  {w.get('worker_id', '?'):<20} {w.get('status', '?'):<12} {w.get('url', '?')}")
            else:
                print("No workers registered.")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_workers_ping(args: argparse.Namespace) -> None:
    try:
        resp = httpx.post(f"{args.daemon_url}/admin/workers/ping", timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            print(json.dumps(data, indent=2))
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_agents_list(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(f"{args.daemon_url}/admin/agents", timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            agents = data.get("agents", [])
            if agents:
                for a in agents:
                    print(f"  {a.get('agent_id', '?'):<20} {a.get('status', '?'):<12} {a.get('model', '?')}")
            else:
                print("No agents configured.")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_metrics_cost(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(f"{args.daemon_url}/admin/metrics/cost", timeout=10.0)
        if resp.status_code == 200:
            print(json.dumps(resp.json(), indent=2))
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_metrics_report(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(f"{args.daemon_url}/admin/metrics/report", timeout=10.0)
        if resp.status_code == 200:
            print(json.dumps(resp.json(), indent=2))
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_reload(args: argparse.Namespace) -> None:
    try:
        resp = httpx.post(
            f"{args.daemon_url}/admin/reload",
            json={"scope": args.scope},
            timeout=30.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"Reloaded: {data.get('scope', args.scope)}")
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_templates_list(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(f"{args.daemon_url}/admin/templates", timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            templates = data.get("templates", [])
            if templates:
                for t in templates:
                    print(f"  {t}")
            else:
                print("No templates found.")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_templates_refresh(args: argparse.Namespace) -> None:
    try:
        resp = httpx.post(f"{args.daemon_url}/admin/templates/refresh", timeout=30.0)
        if resp.status_code == 200:
            data = resp.json()
            print(f"Refreshed: {data.get('count', 0)} templates")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_playbooks_list(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(f"{args.daemon_url}/admin/playbooks", timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            playbooks = data.get("playbooks", [])
            if playbooks:
                for p in playbooks:
                    print(f"  {p}")
            else:
                print("No playbooks found.")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_playbooks_refresh(args: argparse.Namespace) -> None:
    try:
        resp = httpx.post(f"{args.daemon_url}/admin/playbooks/refresh", timeout=30.0)
        if resp.status_code == 200:
            data = resp.json()
            print(f"Refreshed: {data.get('count', 0)} playbooks")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_code_graph(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(
            f"{args.daemon_url}/admin/code/graph",
            params={"source": args.source, "language": args.language},
            timeout=30.0,
        )
        if resp.status_code == 200:
            print(json.dumps(resp.json(), indent=2))
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_code_search(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(
            f"{args.daemon_url}/admin/code/search",
            params={"query": args.query, "language": args.language},
            timeout=30.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            if results:
                for r in results:
                    print(f"  {r.get('file', '?')}:{r.get('line', '?')} {r.get('text', '')[:80]}")
            else:
                print(f"No results for '{args.query}'")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_quantization_list(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(f"{args.daemon_url}/admin/quantization", timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            models = data.get("models", [])
            if models:
                for m in models:
                    prec = m.get("precision", "unknown")
                    conf = m.get("confidence", 0)
                    print(f"  {m.get('model_id', '?')}  prec={prec}  conf={conf:.2f}")
            else:
                print("No quantization data available. Use 'detect' to scan models.")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_quantization_detect(args: argparse.Namespace) -> None:
    try:
        resp = httpx.post(
            f"{args.daemon_url}/admin/quantization/detect",
            json={"model_id": args.model_id},
            timeout=30.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            mid = data.get("model_id", "?")
            prec = data.get("precision", "unknown")
            conf = data.get("confidence", 0)
            print(f"  {mid}  prec={prec}  conf={conf:.2f}")
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_quantization_drift_check(args: argparse.Namespace) -> None:
    try:
        resp = httpx.post(f"{args.daemon_url}/admin/quantization/drift-check", timeout=30.0)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("drift_detected"):
                print(f"Drift detected in {len(data.get('drifted_models', []))} model(s)")
                for m in data.get("drifted_models", []):
                    print(f"  {m.get('model_id')}: {m.get('old_precision')} -> {m.get('new_precision')}")
            else:
                print("No drift detected.")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


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
        "active_overlay_path": "",
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
