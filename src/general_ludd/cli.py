"""Unified CLI entrypoint for General Ludd Agent."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx

from general_ludd.config.binary_paths import BinaryPathResolver
from general_ludd.db.session import get_default_db_url, is_sqlite_url
from general_ludd.filestore.bootstrap import BinaryBootstrapper
from general_ludd.filestore.store import FileStore
from general_ludd.integrity.fim_excludes import FIM_EXCLUDE_PATTERNS
from general_ludd.integrity.scanner import FileIntegrityScanner
from general_ludd.models.performance_router import DEFAULT_STRATEGIES
from general_ludd.tui.config_editor import ConfigEditor
from general_ludd.tui.runner import run_tui
from general_ludd.tui.tables import _make_table

if TYPE_CHECKING:
    from rich.table import Table

_DAEMON_SHUTDOWN_TIMEOUT_SECONDS = 5.0

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
      --host HOST         Bind address (default: 127.0.0.1)
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

    smoke              Run low-cost provider/service smoke tests
      list               List all registered smoke tests
      PROVIDER TEST      Run a smoke test, e.g. aws ec2-a100
      --live             Allow cheap live metadata probes
      --json             Emit logs, metrics, and events as JSON

    model               Local model management (download, quantize, serve, evaluate)
      download NAME        Download a model from HuggingFace
        --revision REV       Model revision/tag
        --cache-dir DIR      Override cache directory
      quantize NAME         Quantize a downloaded model
        --method METHOD      q4_k_m (default), q4_0, q5_k_m, q8_0
        --output-dir DIR     Output directory for quantized model
      serve NAME            Start a local inference server
        --engine ENGINE      llamacpp (default), vllm, mlx
        --host HOST          Bind address (default: 127.0.0.1)
        --port PORT          Port (default: 8080)
        --gpu-layers N       GPU layers to offload
        --context-size N     Context window size (default: 4096)
      evaluate NAME          Run evaluation benchmarks
        --benchmark NAME     Specific benchmark
        --limit N            Sample limit per benchmark
      recommend              Recommend models for a task
        --task TASK           Task description (required)
        --max-params N        Max parameter count in billions
      radar NAME             Show capability radar for a model

    models              Model management commands
      search              Search HuggingFace models
        [QUERY]             Search query
        --limit N           Max results (default: 20)
        --daemon-url URL    Daemon URL
      searx-search        Search models via SearXNG
        [QUERY]             Search query
        --source SRC        Source: huggingface, github, web (default: huggingface)
        --searx-url URL     SearXNG instance URL
      deploy              Deploy a model found via SearXNG
        NAME                Model name to find and deploy
        --provider P        Cloud provider (default: aws)
        --engine E          Engine: vllm or llamacpp (default: vllm)
        --workload-type W   Workload type (default: realtime_api)
        --searx-url URL     SearXNG instance URL
        --region REGION     Cloud region
        --gpu-count N       Number of GPUs (default: 1)
        --max-cost N        Max cost in USD (default: 10.0)
      downloaded          List downloaded models
        --daemon-url URL    Daemon URL
      discover            Discover free models from providers
        --provider NAME      Provider (default: openrouter)
        --daemon-url URL     Daemon URL
      discovered          List auto-discovered model profiles
        --daemon-url URL     Daemon URL
      performance         Show model performance data
        --service S          Filter by service
        --task-type T        Filter by task type
        --daemon-url URL     Daemon URL
      ranking             Show model rankings for a task type
        --task-type T        Task type (required)
        --strategy S         Ranking strategy (balanced|quality|cheapest|fastest)
        --daemon-url URL     Daemon URL
      router-status       Show current router configuration
        --daemon-url URL     Daemon URL
      router-set          Set routing strategy for a task type
        --task-type T        Task type (required)
        --strategy S         Routing strategy (balanced|quality|cheapest|fastest)
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
      launch              Launch a GPU compute instance
        --provider NAME     Cloud provider (aws, azure, gcp, runpod, etc.)
        --gpu TYPE          GPU type (t4, a100_80, h100, etc.)
        --model MODEL       Model name to serve
        --daemon-url URL    Daemon URL
      destroy             Destroy a GPU compute instance
        INSTANCE_ID         Instance ID to destroy
        --daemon-url URL    Daemon URL

    scores              View benchmark scores
      --task-type TYPE    Filter by task type
      --daemon-url URL    Daemon URL

    leaderboard         View prompt+model leaderboard
      --task-type TYPE    Filter by task type
      --daemon-url URL    Daemon URL

    login               Browser-based OAuth2 / API key login for services
      <service>            Service to log into (github, openai, deepseek, zai, anthropic, gemini, openrouter)
      --list               List available services
      --timeout N          OAuth2 callback timeout in seconds (default: 120)
      --store {env,openbao}  Credential storage backend (default: env)

    help                Show this manual

    test self           Run daemon self-tests (canonical command)
      --daemon-url URL    Daemon URL (default: http://localhost:8000)
    selftest            Backward-compatible alias for ``test self``
      --daemon-url URL    Daemon URL (default: http://localhost:8000)

    Pause state is managed via tasks/agents/infra API endpoints:
      POST /api/tasks/{task_id}/pause
      POST /api/tasks/{task_id}/resume
      POST /api/agents/{agent_id}/pause
      POST /api/infra/{deployment_id}/pause
      GET  /api/pause/status

    test-bg             Background test runner commands
      launch              Launch a test in the background
        TESTFILE            Test file path (required)
        --wait              Block until test completes
      status              Check status of a background test
        TESTFILE            Test file path (required)
      poll-all            Status for all tracked background tests
      kill                Kill a background test
        TESTFILE            Test file path (required)
        --force             Force SIGKILL after SIGTERM
      results             Get final results for a completed test
        TESTFILE            Test file path (required)

    searx               SearXNG meta-search engine commands
      start               Start the local SearXNG server
      stop                Stop the local SearXNG server
      status              Check if SearXNG is running
      config              Show/generate SearXNG configuration

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

    payment             PCI-DSS payment card vault (envelope-encrypted in OpenBao)
      add                 Store a card under a label
        --card-number NUM    Card number (prompted via getpass if omitted)
        --expiry-month MM    Expiry month 01-12 (required)
        --expiry-year YY     Expiry year 2-digit (required)
        --cvc CVC            CVC (prompted via getpass if omitted)
        --holder-name NAME   Cardholder name (required)
        --label NAME         Storage label (default: default)
        --processor NAME     Payment processor (default: stripe)
      list                List stored cards (masked only)
      show LABEL          Show masked metadata for one card
      delete LABEL        Delete a stored card (-y to skip confirm)
      provision SERVICE   Simulate 1-click provisioning using a stored card
        --label NAME         Card label to use (default: default)

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
            f"Error: Cannot connect to daemon at {daemon_url}. Is the daemon running? Start it with: gludd daemon",
            file=sys.stderr,
        )
    elif isinstance(exc, httpx.TimeoutException):
        print(f"Error: Request to daemon at {daemon_url} timed out.", file=sys.stderr)
    else:
        print(f"Error: {exc}", file=sys.stderr)
    sys.exit(1)


def _http_call(
    method: str,
    url: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    timeout: float = 10.0,
    ok_codes: tuple[int, ...] = (200,),
) -> Any:
    try:
        m = method.upper()
        if m == "GET":
            resp = httpx.get(url, params=params, timeout=timeout)
        elif m == "POST":
            resp = httpx.post(url, json=json, params=params, timeout=timeout)
        elif m == "DELETE":
            resp = httpx.delete(url, params=params, timeout=timeout)
        elif m == "PUT":
            resp = httpx.put(url, json=json, params=params, timeout=timeout)
        elif m == "PATCH":
            resp = httpx.patch(url, json=json, params=params, timeout=timeout)
        else:
            resp = httpx.request(method, url, json=json, params=params, timeout=timeout)
        if resp.status_code in ok_codes:
            return resp.json()
        print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, url)
    return None


def _add_smoke_arguments(parser: argparse.ArgumentParser) -> None:
    """Attach the shared smoke-check command surface to a parser."""
    parser.add_argument("provider", nargs="?", default=None, help="Provider or service slug, or 'list'")
    parser.add_argument("test", nargs="?", default=None, help="Smoke test name, e.g. metadata or ec2-a100")
    parser.add_argument("--list", action="store_true", help="List available smoke tests")
    parser.add_argument("--live", action="store_true", help="Allow cheap live metadata probes")
    parser.add_argument(
        "--provisioned",
        action="store_true",
        help="Provision a real resource, run a model task, and tear it down",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--output", default=None, help="Write the rendered diagnostic bundle to this file")
    parser.add_argument(
        "--output-template",
        default=None,
        help="Compiled output template for smoke list/report rendering",
    )
    parser.add_argument("--timeout", type=float, default=2.0, help="HTTP probe timeout in seconds")
    parser.add_argument("--max-cost-usd", type=float, default=10.0, help="Fail if estimated cost exceeds this")
    parser.add_argument("--base-url", default=None, help="Override endpoint base URL for this run")
    parser.add_argument("--model", default=None, help="Override model identifier for this run")
    parser.add_argument("--region", default=None, help="Provider region for provisioned smoke tests")
    parser.add_argument("--gpu-count", type=int, default=1, help="GPU count for provisioned smoke tests")
    parser.add_argument(
        "--engine",
        default="vllm",
        choices=["vllm", "llamacpp"],
        help="Inference engine for provisioned smoke tests",
    )


def _configure_selftest_parser(parser: argparse.ArgumentParser) -> None:
    """Keep canonical and compatibility self-test commands behaviorally identical."""
    parser.add_argument("--daemon-url", default="http://localhost:8000")
    parser.set_defaults(func=_cmd_selftest)


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = argparse.ArgumentParser(
        prog="gludd",
        description="General Ludd Agent — the black swan agentic coding system",
    )
    from general_ludd import __version__

    parser.add_argument(
        "--version",
        action="version",
        version=__version__,
        help="Show the installed General Ludd version and exit",
    )
    parser.set_defaults(func=None)
    sub = parser.add_subparsers(dest="command")

    daemon_parser = sub.add_parser("daemon", help="Start the daemon (server + event loop)")
    daemon_parser.add_argument("--host", default="127.0.0.1")
    daemon_parser.add_argument("--port", type=int, default=8000)
    daemon_parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    daemon_parser.add_argument("--tick-interval", type=float, default=1.0)
    daemon_parser.add_argument("--workers", type=int, default=1)
    daemon_parser.add_argument("--project", default=None, help="Default project for daemon operations")
    daemon_parser.add_argument("--config-dir", default=None, help="Path to config directory")
    daemon_parser.add_argument("--templates-dir", default=None, help="Path to prompt templates directory")
    daemon_parser.add_argument("--playbooks-dir", default=None, help="Path to Ansible playbooks directory")
    daemon_parser.set_defaults(func=_cmd_daemon)

    add_parser = sub.add_parser("add", help="Add a todo to the queue")
    add_parser.add_argument("title", metavar="TITLE", help="Todo title")
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

    selftest_parser = sub.add_parser(
        "selftest",
        help="Backward-compatible alias for 'test self'",
    )
    _configure_selftest_parser(selftest_parser)

    models_parser = sub.add_parser("models", help="Model management commands")
    models_parser.set_defaults(func=None)
    models_sub = models_parser.add_subparsers(dest="models_command")

    models_search = models_sub.add_parser("search", help="Search HuggingFace models")
    models_search.add_argument("query", nargs="?", default="", help="Search query")
    models_search.add_argument("--limit", type=int, default=20)
    models_search.add_argument("--daemon-url", default="http://localhost:8000")
    models_search.set_defaults(func=_cmd_models_search)

    models_searx_search = models_sub.add_parser("searx-search", help="Search models via SearXNG")
    models_searx_search.add_argument("query", nargs="?", default="", help="Search query")
    models_searx_search.add_argument(
        "--source",
        default="huggingface",
        choices=["huggingface", "github", "web"],
        help="Search source (default: huggingface)",
    )
    models_searx_search.add_argument("--searx-url", default=None, help="SearXNG instance URL")
    models_searx_search.set_defaults(func=_cmd_models_searx_search)

    models_deploy = models_sub.add_parser("deploy", help="Deploy a model found via SearXNG")
    models_deploy.add_argument("name", help="Model name to find and deploy")
    models_deploy.add_argument("--provider", default="aws", help="Cloud provider (aws, gcp, azure)")
    models_deploy.add_argument("--engine", default="vllm", choices=["vllm", "llamacpp"], help="Inference engine")
    models_deploy.add_argument(
        "--workload-type",
        default="realtime_api",
        choices=["batch_inference", "realtime_api", "fine_tuning", "speculative_decoding", "embedding_generation"],
        help="Workload pattern",
    )
    models_deploy.add_argument("--searx-url", default=None, help="SearXNG instance URL")
    models_deploy.add_argument("--region", default=None, help="Cloud region")
    models_deploy.add_argument("--gpu-count", type=int, default=1, help="Number of GPUs")
    models_deploy.add_argument("--max-cost", type=float, default=10.0, help="Max cost in USD")
    models_deploy.set_defaults(func=_cmd_models_deploy)

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

    models_perf = models_sub.add_parser("performance", help="Show model performance data")
    models_perf.add_argument("--service", default=None, help="Filter by service")
    models_perf.add_argument("--task-type", default=None, help="Filter by task type")
    models_perf.add_argument("--daemon-url", default="http://localhost:8000")
    models_perf.set_defaults(func=_cmd_model_performance)

    models_ranking = models_sub.add_parser("ranking", help="Show model rankings for a task type")
    models_ranking.add_argument("--task-type", required=True, help="Task type to rank")
    models_ranking.add_argument(
        "--strategy", default="balanced", choices=list(DEFAULT_STRATEGIES.keys()), help="Ranking strategy"
    )
    models_ranking.add_argument("--daemon-url", default="http://localhost:8000")
    models_ranking.set_defaults(func=_cmd_model_ranking)

    models_router_status = models_sub.add_parser("router-status", help="Show current router configuration")
    models_router_status.add_argument("--daemon-url", default="http://localhost:8000")
    models_router_status.set_defaults(func=_cmd_model_router_status)

    models_router_set = models_sub.add_parser("router-set", help="Set routing strategy for a task type")
    models_router_set.add_argument("--task-type", required=True, help="Task type")
    models_router_set.add_argument(
        "--strategy", required=True, choices=list(DEFAULT_STRATEGIES.keys()), help="Routing strategy"
    )
    models_router_set.add_argument("--daemon-url", default="http://localhost:8000")
    models_router_set.set_defaults(func=_cmd_model_router_set)

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
    proj_add.add_argument(
        "--dispatch-mode",
        default="active",
        choices=["active", "passive_external", "worktree_monitor"],
        help="Dispatch: active, passive_external, or worktree_monitor",
    )
    proj_add.add_argument("--daemon-url", default="http://localhost:8000")
    proj_add.set_defaults(func=_cmd_project_add)

    proj_list = proj_sub.add_parser("list", help="List registered projects")
    proj_list.add_argument("--daemon-url", default="http://localhost:8000")
    proj_list.set_defaults(func=_cmd_project_list)

    proj_remove = proj_sub.add_parser("remove", help="Remove a project")
    proj_remove.add_argument("project_id", help="Project ID to remove")
    proj_remove.add_argument("--daemon-url", default="http://localhost:8000")
    proj_remove.set_defaults(func=_cmd_project_remove)

    from general_ludd.cli_project_init import add_project_init_subparser

    add_project_init_subparser(proj_sub)

    from general_ludd.cli_project_paths import add_project_paths_subparser

    add_project_paths_subparser(proj_sub)

    config_parser = sub.add_parser("config", help="User configuration commands")
    config_parser.set_defaults(func=None)
    config_sub = config_parser.add_subparsers(dest="config_command")

    tf_parser = config_sub.add_parser("terraform", help="Terraform variable defaults")
    tf_sub = tf_parser.add_subparsers(dest="terraform_command")

    tf_get = tf_sub.add_parser("get", help="Show terraform variable defaults")
    tf_get.add_argument("--field", default=None, help="Specific field to show")
    tf_get.set_defaults(func=_cmd_config_terraform_get)

    tf_set = tf_sub.add_parser("set", help="Set a terraform variable default")
    tf_set.add_argument("field", help="Field name (e.g. region, instance_type, gpu_count)")
    tf_set.add_argument("value", help="New value")
    tf_set.set_defaults(func=_cmd_config_terraform_set)

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

    compute_azure_preflight = compute_sub.add_parser(
        "azure-preflight",
        help="Read-only Azure accelerator SKU and quota preflight",
    )
    compute_azure_preflight.add_argument(
        "--gpu",
        required=True,
        help="Azure accelerator type (a100_40, a100_80, h100, or t4)",
    )
    compute_azure_preflight.add_argument(
        "--gpu-count",
        type=int,
        default=1,
        help="Exact accelerator count requested",
    )
    compute_azure_preflight.add_argument(
        "--region",
        default="eastus",
        help="Azure region used for SKU and quota checks",
    )
    compute_azure_preflight.add_argument(
        "--daemon-url",
        default="http://localhost:8000",
    )
    compute_azure_preflight.set_defaults(func=_cmd_compute_azure_preflight)

    compute_launch = compute_sub.add_parser("launch", help="Launch a GPU compute instance")
    compute_launch.add_argument("--provider", required=True, help="Cloud provider (aws, azure, gcp, runpod, etc.)")
    compute_launch.add_argument("--gpu", required=True, help="GPU type (t4, a100_80, h100, etc.)")
    compute_launch.add_argument("--model", required=True, help="Model name to serve")
    compute_launch.add_argument("--region", default=None, help="Cloud region")
    compute_launch.add_argument("--deploy-type", default="vm", help="Deploy type (vm or containerapp)")
    compute_launch.add_argument("--gpu-count", type=int, default=1, help="Number of GPUs")
    compute_launch.add_argument("--max-cost", type=float, default=10.0, help="Max cost in USD")
    compute_launch.add_argument(
        "--timeout-minutes",
        type=float,
        default=60.0,
        help="Hard deployment lifetime before automatic teardown",
    )
    compute_launch.add_argument(
        "--disk-size-gb",
        type=int,
        default=100,
        help="OS disk size for the inference worker",
    )
    compute_launch.add_argument(
        "--container-image",
        default=None,
        help="Optional serving image override",
    )
    compute_launch.add_argument(
        "--hourly-rate",
        type=float,
        default=None,
        help="Known USD/hour rate used to shorten the hard TTL to the spend ceiling",
    )
    compute_launch.add_argument("--no-spot", action="store_true", help="Disable spot instances")
    compute_launch.add_argument(
        "--allowed-cidr",
        default="127.0.0.1/32",
        help="CIDR allowed to reach SSH and inference (secure default: loopback only)",
    )
    compute_launch.add_argument(
        "--ssh-public-key-path",
        default="~/.ssh/id_ed25519.pub",
        help="Public SSH key used by Azure VM provisioning",
    )
    compute_launch.add_argument(
        "--max-concurrent",
        type=int,
        default=4,
        help="Scheduler concurrency registered for the new endpoint",
    )
    compute_launch.add_argument("--engine", default="vllm", help="Inference engine (vllm or llamacpp)")
    compute_launch.add_argument(
        "--workload-type",
        default="",
        choices=["batch_inference", "realtime_api", "fine_tuning", "speculative_decoding", "embedding_generation"],
        help="Workload pattern to optimize deployment for",
    )
    compute_launch.add_argument("--daemon-url", default="http://localhost:8000")
    compute_launch.set_defaults(func=_cmd_compute_launch)

    compute_destroy = compute_sub.add_parser("destroy", help="Destroy a GPU compute instance")
    compute_destroy.add_argument("instance_id", help="Instance ID to destroy")
    compute_destroy.add_argument("--daemon-url", default="http://localhost:8000")
    compute_destroy.set_defaults(func=_cmd_compute_destroy)

    scores_parser = sub.add_parser("scores", help="View benchmark scores")
    scores_parser.add_argument("--task-type", default=None, help="Filter by task type")
    scores_parser.add_argument("--daemon-url", default="http://localhost:8000")
    scores_parser.set_defaults(func=_cmd_scores)

    leaderboard_parser = sub.add_parser("leaderboard", help="View prompt+model leaderboard")
    leaderboard_parser.add_argument("--task-type", default=None, help="Filter by task type")
    leaderboard_parser.add_argument("--daemon-url", default="http://localhost:8000")
    leaderboard_parser.set_defaults(func=_cmd_leaderboard)

    chat_parser = sub.add_parser("chat", help="Interactive AI chat REPL")
    chat_parser.add_argument(
        "--eval", type=str, default=None, metavar="PROMPT", help="Single-turn evaluation (non-interactive)"
    )
    chat_parser.add_argument(
        "--model", default="default", help="Model profile (e.g. openai/gpt-4o, deepseek/deepseek-chat)"
    )
    chat_parser.add_argument("--system-prompt", default=None, help="Override system prompt")
    chat_parser.add_argument("--history", default=None, metavar="FILE", help="JSON-lines conversation history file")
    chat_parser.add_argument("--resume", action="store_true", help="Resume the most recent chat session")
    chat_parser.add_argument("--list-sessions", action="store_true", help="List saved chat sessions and exit")
    chat_parser.add_argument(
        "--save-interval", type=int, default=5, help="Auto-save history every N turns (default: 5)"
    )
    chat_parser.add_argument(
        "--api-base", default=os.environ.get("OPENAI_BASE_URL"), help="Override API base URL (env: OPENAI_BASE_URL)"
    )
    chat_parser.add_argument(
        "--api-key", default=os.environ.get("OPENAI_API_KEY"), help="Override API key (env: OPENAI_API_KEY)"
    )
    chat_parser.add_argument(
        "--project-dir", default=None, metavar="PATH", help="Project directory for ansible/terraform context injection"
    )
    chat_parser.add_argument(
        "--export",
        default=None,
        metavar="FORMAT",
        choices=["md", "json", "html"],
        help="Export a saved session to md/json/html and exit",
    )
    chat_parser.add_argument(
        "--export-output", default=None, metavar="FILE", help="Write export output to FILE (default: stdout)"
    )
    chat_parser.add_argument(
        "--stream", action="store_true", default=False, help="Stream model response tokens in real-time (--eval mode)"
    )
    chat_parser.add_argument(
        "--max-context",
        type=int,
        default=None,
        metavar="TOKENS",
        help="Maximum context window size in tokens (enables sliding-window trimming)",
    )
    chat_parser.add_argument(
        "--daemon-url", default=None, metavar="URL", help="Delegate session list/search to daemon at URL"
    )
    chat_parser.add_argument(
        "--search", default=None, metavar="QUERY", help="Search chat sessions by content (requires --daemon-url)"
    )
    chat_parser.set_defaults(func=_cmd_chat)

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

    preflight_p = sub.add_parser("preflight", help="Run the preflight quality gate")
    preflight_p.add_argument(
        "--strict-terraform-import",
        action="store_true",
        help="Elevate terraform-collection importer warnings to failures (release readiness)",
    )
    preflight_p.set_defaults(func=_cmd_preflight)

    tui_parser = sub.add_parser("tui", help="Launch the interactive TUI dashboard")
    tui_parser.add_argument("--daemon-url", default="http://localhost:8000")
    tui_parser.set_defaults(func=_cmd_tui)

    # `gludd audit-plugins` — plugin-health audit playbook wrapper.
    from general_ludd.cli_audit_plugins import add_audit_plugins_subparser

    add_audit_plugins_subparser(sub)
    audit_plugins_parser = sub.choices["audit-plugins"]

    # `gludd collection` — multi-version collection management.
    from general_ludd.cli_collection import add_collection_subparser

    add_collection_subparser(sub)
    collection_parser = sub.choices["collection"]

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

    # quantization removed from CLI — should be a tunable daemon subsystem.
    # Code retained below for programmatic use.
    # quant_parser = sub.add_parser("quantization", help="Model quantization detection")
    # quant_parser.set_defaults(func=None)
    # quant_sub = quant_parser.add_subparsers(dest="quantization_command")
    # quant_list = quant_sub.add_parser("list", help="List known quantization info")
    # quant_list.add_argument("--daemon-url", default="http://localhost:8000")
    # quant_list.set_defaults(func=_cmd_quantization_list)
    # quant_detect = quant_sub.add_parser("detect", help="Detect quantization for a model")
    # quant_detect.add_argument("--model-id", required=True, help="Model ID to detect")
    # quant_detect.add_argument("--daemon-url", default="http://localhost:8000")
    # quant_detect.set_defaults(func=_cmd_quantization_detect)
    # quant_drift = quant_sub.add_parser("drift-check", help="Check for quantization drift")
    # quant_drift.add_argument("--daemon-url", default="http://localhost:8000")
    # quant_drift.set_defaults(func=_cmd_quantization_drift_check)

    slurm_parser = sub.add_parser("slurm", help="Slurm job management")
    slurm_parser.set_defaults(func=None)
    slurm_sub = slurm_parser.add_subparsers(dest="slurm_command")

    slurm_status = slurm_sub.add_parser("status", help="Check if Slurm is available")
    slurm_status.add_argument("--daemon-url", default="http://localhost:8000")
    slurm_status.set_defaults(func=_cmd_slurm_status)

    slurm_submit = slurm_sub.add_parser("submit", help="Submit a Slurm job")
    slurm_submit.add_argument("--command", required=True, help="Job invocation string")
    slurm_submit.add_argument("--job-name", default=None, help="Job name")
    slurm_submit.add_argument("--partition", default=None, help="Partition")
    slurm_submit.add_argument("--cpus-per-task", type=int, default=None, help="CPUs per task")
    slurm_submit.add_argument("--gpus", default=None, help="GPU count or type")
    slurm_submit.add_argument("--memory", default=None, help="Memory e.g. 16G")
    slurm_submit.add_argument("--time-limit", default=None, help="Time limit e.g. 02:00:00")
    slurm_submit.add_argument("--daemon-url", default="http://localhost:8000")
    slurm_submit.set_defaults(func=_cmd_slurm_submit)

    slurm_job = slurm_sub.add_parser("job", help="Check Slurm job status")
    slurm_job.add_argument("job_id", help="Job ID")
    slurm_job.add_argument("--daemon-url", default="http://localhost:8000")
    slurm_job.set_defaults(func=_cmd_slurm_job)

    slurm_cancel = slurm_sub.add_parser("cancel", help="Cancel a Slurm job")
    slurm_cancel.add_argument("job_id", help="Job ID to cancel")
    slurm_cancel.add_argument("--daemon-url", default="http://localhost:8000")
    slurm_cancel.set_defaults(func=_cmd_slurm_cancel)

    slurm_list = slurm_sub.add_parser("list", help="List Slurm jobs")
    slurm_list.add_argument("--daemon-url", default="http://localhost:8000")
    slurm_list.set_defaults(func=_cmd_slurm_list)

    connectors_parser = sub.add_parser("connectors", help="Observability connector commands")
    connectors_parser.set_defaults(func=None)
    connectors_sub = connectors_parser.add_subparsers(dest="connectors_command")

    connectors_list = connectors_sub.add_parser("list", help="List registered observability sources")
    connectors_list.add_argument("--daemon-url", default="http://localhost:8000")
    connectors_list.set_defaults(func=_cmd_connectors_list)

    connectors_health = connectors_sub.add_parser("health", help="Probe health across registered sources")
    connectors_health.add_argument("--daemon-url", default="http://localhost:8000")
    connectors_health.set_defaults(func=_cmd_connectors_health)

    connectors_query = connectors_sub.add_parser("query", help="Run a query against a named observability source")
    connectors_query.add_argument("source", help="Registered source name to query")
    connectors_query.add_argument("--spec", default="{}", help="Query spec as a JSON string (default: {})")
    connectors_query.add_argument("--daemon-url", default="http://localhost:8000")
    connectors_query.set_defaults(func=_cmd_connectors_query)

    login_parser = sub.add_parser("login", help="Browser-based OAuth2 / API key login for services")
    login_parser.add_argument(
        "service",
        nargs="?",
        default=None,
        help="Service to log into (github, openai, deepseek, zai, anthropic, gemini, openrouter). "
        "Use '--list' to see available services.",
    )
    login_parser.add_argument("--list", action="store_true", help="List available login services and exit")
    login_parser.add_argument(
        "--timeout", type=float, default=120.0, help="OAuth2 callback timeout in seconds (default: 120)"
    )
    login_parser.add_argument(
        "--store", default="env", choices=["env", "openbao"], help="Credential storage backend (default: env)"
    )
    login_parser.set_defaults(func=_cmd_login)

    onboard_parser = sub.add_parser(
        "onboard",
        help="Interactively set up the IAM role + API token gludd needs to run "
        "Terraform-managed compute (least privilege).",
        description=(
            "Walk through IAM role creation, token acquisition guidance, token "
            "input, and end-to-end validation for the requested cloud provider.\n\n"
            "Phases:\n"
            "  Phase 1: IAM role creation guidance\n"
            "  Phase 2: token acquisition guidance\n"
            "  Phase 3: token input (prompt or --token)\n"
            "  Phase 4: token + role validation\n\n"
            "Supported providers: aws, gcp, azure"
        ),
    )
    onboard_parser.add_argument(
        "provider",
        nargs="?",
        default=None,
        help="Cloud provider to onboard (aws, gcp, azure).",
    )
    onboard_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Walk through every phase without invoking any cloud API "
        "(validation is skipped; canned responses are used).",
    )
    onboard_parser.add_argument("--token", default=None, help="API token (non-interactive).")
    onboard_parser.add_argument("--role-arn", default=None, help="IAM role ARN (non-interactive).")
    onboard_parser.add_argument("--region", default=None, help="Cloud region (e.g. us-east-1).")
    onboard_parser.add_argument("--project", default=None, help="GCP project ID.")
    onboard_parser.add_argument("--subscription", default=None, help="Azure subscription ID.")
    onboard_parser.add_argument(
        "--config-dir",
        default=None,
        help="Where to write the onboarded-provider.json config (default: ~/.config/gludd).",
    )
    onboard_parser.set_defaults(func=_cmd_onboard)

    # `gludd perm` — permission system visibility + editing.
    from general_ludd.cli_perm import register as _register_perm

    perm_parser = _register_perm(sub)

    # Keep the documented vault available from the CLI. Its handler defaults to
    # non-echoing interactive entry and explicitly warns on cleartext flags.
    from general_ludd.cli_payment import register as _register_payment

    payment_parser = _register_payment(sub)

    # `gludd human-todo` — bot→human task requests.
    from general_ludd.cli_human_todos import add_human_todo_subparser

    add_human_todo_subparser(sub)
    human_todo_parser = sub.choices["human-todo"]

    # `gludd model` — local model management (download, quantize, serve, evaluate).
    from general_ludd.cli_model import add_model_subparser

    add_model_subparser(sub)
    model_parser = sub.choices["model"]

    # `gludd self-improve` — human approval gate for self-authored todos.
    from general_ludd.cli_self_improve import add_self_improve_subparser

    add_self_improve_subparser(sub)
    self_improve_parser = sub.choices["self-improve"]

    # `gludd remediation` — blocked-task detection + remediation.
    from general_ludd.cli_remediation import add_remediation_subparser

    add_remediation_subparser(sub)
    remediation_parser = sub.choices["remediation"]

    # `gludd ornith` — Ornith self-improving coding-agent integration.
    from general_ludd.cli_ornith import add_ornith_subparser

    add_ornith_subparser(sub)
    ornith_parser = sub.choices["ornith"]

    # `gludd searx` — SearXNG meta-search engine management.
    searx_parser = sub.add_parser("searx", help="SearXNG meta-search engine commands")
    searx_sub = searx_parser.add_subparsers(dest="searx_command")
    searx_start = searx_sub.add_parser("start", help="Start the local SearXNG server")
    searx_start.set_defaults(func=_cmd_searx)
    searx_stop = searx_sub.add_parser("stop", help="Stop the local SearXNG server")
    searx_stop.set_defaults(func=_cmd_searx)
    searx_status = searx_sub.add_parser("status", help="Check if SearXNG is running")
    searx_status.set_defaults(func=_cmd_searx)
    searx_config = searx_sub.add_parser("config", help="Show/generate SearXNG configuration")
    searx_config.set_defaults(func=_cmd_searx)

    # `gludd service` — service discovery and catalog browsing.
    from general_ludd.cli_service_commands import add_service_subparser

    add_service_subparser(sub)
    sub.choices["service"]

    # `gludd deploy-check` — static model-deployment misconfig detector.
    from general_ludd.cli_deploy_check import add_deploy_check_subparser

    add_deploy_check_subparser(sub)
    deploy_check_parser = sub.choices["deploy-check"]

    # `gludd core-changes` — render agentic change log as core/user diffs.
    from general_ludd.cli_core_changes import add_core_changes_subparser

    add_core_changes_subparser(sub)
    core_changes_parser = sub.choices["core-changes"]

    # `gludd spec-quality` — behavioral spec quality audit.
    from general_ludd.cli_spec_quality import add_spec_quality_subparser

    add_spec_quality_subparser(sub)
    sub.choices["spec-quality"]

    make_parser = sub.add_parser("make", help="Run a make target via MakeRunner")
    make_parser.add_argument("target", help="Make target to run (e.g. test, lint, gate)")
    make_parser.add_argument("--cwd", default=None, help="Working directory for make")
    make_parser.add_argument("--timeout", type=int, default=None, help="Timeout in seconds")
    make_parser.add_argument("--env", nargs="*", default=None, help="Extra env vars (KEY=VALUE ...)")
    make_parser.add_argument("--stream", action="store_true", help="Stream phase markers")
    make_parser.set_defaults(func=_cmd_make)

    # `gludd cloud` — cloud IAM and infrastructure management.
    cloud_parser = sub.add_parser("cloud", help="Cloud IAM and infrastructure commands")
    cloud_parser.set_defaults(func=None)
    cloud_sub = cloud_parser.add_subparsers(dest="cloud_command")

    iam_parser = cloud_sub.add_parser("iam", help="Cross-provider IAM role generation and validation")
    iam_parser.set_defaults(func=None)
    iam_sub = iam_parser.add_subparsers(dest="iam_command")

    iam_generate = iam_sub.add_parser("generate", help="Generate a least-privilege IAM role")
    iam_generate.add_argument("--provider", required=True, choices=["azure", "aws", "gcp"], help="Cloud provider")
    iam_generate.add_argument(
        "--persona",
        default="monitor",
        choices=["terraform_deploy", "runtime_execution", "model_inference", "monitor"],
        help="Role persona (default: monitor)",
    )
    iam_generate.set_defaults(func=_cmd_cloud_iam_generate)

    iam_validate = iam_sub.add_parser("validate", help="Validate an existing IAM role definition")
    iam_validate.add_argument("--provider", required=True, choices=["azure", "aws", "gcp"], help="Cloud provider")
    iam_validate.add_argument("--file", required=True, help="Path to JSON file containing the role definition")
    iam_validate.set_defaults(func=_cmd_cloud_iam_validate)

    game_parser = cloud_sub.add_parser("game", help="Multi-model game generation")
    game_parser.set_defaults(func=None)
    game_sub = game_parser.add_subparsers(dest="game_command")

    game_gen = game_sub.add_parser(
        "generate-multi",
        help=(
            "Generate game code via PLANNER→CODER→REVIEWER pipeline "
            "(delegates to `gludd cloud generate create --type game`)"
        ),
    )
    game_gen.add_argument("--description", required=True, help="Game description for the planner")
    game_gen.add_argument("--planner", default="default", help="Planner model ID")
    game_gen.add_argument("--coder", default="default", help="Coder model ID")
    game_gen.add_argument("--reviewer", default="default", help="Reviewer model ID")
    game_gen.add_argument("--review-rounds", type=int, default=3, help="Max review/fix rounds")
    game_gen.add_argument("--daemon-url", default="http://localhost:8000")
    game_gen.set_defaults(func=_cmd_cloud_game_generate_multi)

    # `gludd cloud generate` — generic project generation for any registered type.
    gen_parser = cloud_sub.add_parser("generate", help="Generic project generation for any registered type")
    gen_parser.set_defaults(func=None)
    gen_sub = gen_parser.add_subparsers(dest="generate_command")

    gen_list = gen_sub.add_parser("list-types", help="List all registered project types")
    gen_list.add_argument("--daemon-url", default="http://localhost:8000")
    gen_list.set_defaults(func=_cmd_cloud_generate_list_types)

    gen_create = gen_sub.add_parser("create", help="Generate a project via PLANNER→CODER→REVIEWER pipeline")
    gen_create.add_argument(
        "--type", required=True, dest="project_type", help="Project type (e.g. game, cli_tool, website)"
    )
    gen_create.add_argument("--description", required=True, help="Project description for the planner")
    gen_create.add_argument("--planner", default="default", help="Planner model ID")
    gen_create.add_argument("--coder", default="default", help="Coder model ID")
    gen_create.add_argument("--reviewer", default="default", help="Reviewer model ID")
    gen_create.add_argument("--review-rounds", type=int, default=3, help="Max review/fix rounds")
    gen_create.add_argument("--daemon-url", default="http://localhost:8000")
    gen_create.set_defaults(func=_cmd_cloud_generate_create)

    gen_validate = gen_sub.add_parser("validate", help="Validate a generated project against type rules")
    gen_validate.add_argument("path", help="Path to the generated project directory")
    gen_validate.add_argument("--type", required=True, dest="project_type", help="Project type to validate against")
    gen_validate.add_argument("--daemon-url", default="http://localhost:8000")
    gen_validate.set_defaults(func=_cmd_cloud_generate_validate)

    # account removed from CLI — access via prompting. Code retained in cli_account.py for programmatic use.
    # from general_ludd.cli_account import add_account_subparser
    # add_account_subparser(sub)
    # account_parser = sub.choices["account"]

    # physics removed from CLI — access via prompting/collection. Code retained in cli_physics.py for programmatic use.
    # from general_ludd.cli_physics import add_physics_subparser
    # add_physics_subparser(sub)
    # physics_parser = sub.choices["physics"]

    # test-bg removed from standalone CLI — moved under `test background` below.
    # Code retained for programmatic use.
    # testbg_parser = sub.add_parser("test-bg", help="Background test runner commands")
    # testbg_parser.set_defaults(func=None)
    # tbg_sub = testbg_parser.add_subparsers(dest="testbg_command")
    # tbg_launch = tbg_sub.add_parser("launch", help="Launch a test in the background")
    # tbg_launch.add_argument("testfile", help="Test file path")
    # tbg_launch.add_argument("--wait", action="store_true", help="Block until test completes")
    # tbg_launch.set_defaults(func=_cmd_testbg_launch)
    # tbg_status = tbg_sub.add_parser("status", help="Check status of a background test")
    # tbg_status.add_argument("testfile", help="Test file path")
    # tbg_status.set_defaults(func=_cmd_testbg_status)
    # tbg_poll = tbg_sub.add_parser("poll-all", help="Status for all tracked background tests")
    # tbg_poll.set_defaults(func=_cmd_testbg_poll_all)
    # tbg_kill = tbg_sub.add_parser("kill", help="Kill a background test")
    # tbg_kill.add_argument("testfile", help="Test file path")
    # tbg_kill.add_argument("--force", action="store_true", help="Force SIGKILL after SIGTERM")
    # tbg_kill.set_defaults(func=_cmd_testbg_kill)
    # tbg_results = tbg_sub.add_parser("results", help="Get final results for a completed test")
    # tbg_results.add_argument("testfile", help="Test file path")
    # tbg_results.set_defaults(func=_cmd_testbg_results)

    test_parser = sub.add_parser("test", help="Test runner commands")
    test_parser.set_defaults(func=None)
    test_sub = test_parser.add_subparsers(dest="test_command")

    test_bg_parser = test_sub.add_parser("background", help="Background test runner commands")
    test_bg_parser.set_defaults(func=None)
    testbg2_sub = test_bg_parser.add_subparsers(dest="testbg_command")

    tbg2_launch = testbg2_sub.add_parser("launch", help="Launch a test in the background")
    tbg2_launch.add_argument("testfile", help="Test file path")
    tbg2_launch.add_argument("--wait", action="store_true", help="Block until test completes")
    tbg2_launch.set_defaults(func=_cmd_testbg_launch)

    tbg2_status = testbg2_sub.add_parser("status", help="Check status of a background test")
    tbg2_status.add_argument("testfile", help="Test file path")
    tbg2_status.set_defaults(func=_cmd_testbg_status)

    tbg2_poll = testbg2_sub.add_parser("poll-all", help="Status for all tracked background tests")
    tbg2_poll.set_defaults(func=_cmd_testbg_poll_all)

    tbg2_kill = testbg2_sub.add_parser("kill", help="Kill a background test")
    tbg2_kill.add_argument("testfile", help="Test file path")
    tbg2_kill.add_argument("--force", action="store_true", help="Force SIGKILL after SIGTERM")
    tbg2_kill.set_defaults(func=_cmd_testbg_kill)

    tbg2_results = testbg2_sub.add_parser("results", help="Get final results for a completed test")
    tbg2_results.add_argument("testfile", help="Test file path")
    tbg2_results.set_defaults(func=_cmd_testbg_results)

    test_self_parser = test_sub.add_parser("self", help="Run self-tests via molecule scenarios")
    _configure_selftest_parser(test_self_parser)

    test_smoke_parser = test_sub.add_parser("smoke", help="Run provider/service smoke checks")
    _add_smoke_arguments(test_smoke_parser)
    test_smoke_parser.set_defaults(func=_cmd_smoke)

    pause_parser = sub.add_parser("pause", help="Pause project or model execution")
    pause_parser.set_defaults(func=None)
    pause_sub = pause_parser.add_subparsers(dest="pause_command")
    pause_list = pause_sub.add_parser("list", help="List paused entities")
    pause_list.add_argument("--daemon-url", default="http://localhost:8000")
    pause_list.set_defaults(func=_cmd_pause_list)
    for kind, handler in (("project", _cmd_pause_project), ("model", _cmd_pause_model)):
        command = pause_sub.add_parser(kind, help=f"Pause a {kind}")
        command.add_argument("target_id", help=f"{kind.capitalize()} identifier")
        command.add_argument("--reason", default="", help="Reason for pausing")
        command.add_argument("--daemon-url", default="http://localhost:8000")
        command.set_defaults(func=handler)

    resume_parser = sub.add_parser("resume", help="Resume project or model execution")
    resume_parser.set_defaults(func=None)
    resume_sub = resume_parser.add_subparsers(dest="resume_command")
    for kind, handler in (("project", _cmd_resume_project), ("model", _cmd_resume_model)):
        command = resume_sub.add_parser(kind, help=f"Resume a {kind}")
        command.add_argument("target_id", help=f"{kind.capitalize()} identifier")
        command.add_argument("--daemon-url", default="http://localhost:8000")
        command.set_defaults(func=handler)

    subcommand_map = {
        "login": login_parser,
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
        "slurm": slurm_parser,
        "connectors": connectors_parser,
        "perm": perm_parser,
        "payment": payment_parser,
        "model": model_parser,
        "human-todo": human_todo_parser,
        "self-improve": self_improve_parser,
        "remediation": remediation_parser,
        "ornith": ornith_parser,
        "deploy-check": deploy_check_parser,
        "core-changes": core_changes_parser,
        "make": make_parser,
        "collection": collection_parser,
        "config": config_parser,
        "searx": searx_parser,
        "cloud": cloud_parser,
        "test-bg": test_bg_parser,
        "test": test_parser,
        "chat": chat_parser,
        "audit-plugins": audit_plugins_parser,
        "pause": pause_parser,
        "resume": resume_parser,
    }

    return parser, subcommand_map


def _cmd_pause_list(args: argparse.Namespace) -> None:
    """List paused projects and model profiles from the daemon."""
    data = _http_call("GET", f"{args.daemon_url}/api/pause")
    if data is not None:
        print(json.dumps(data, indent=2))


def _cmd_pause_project(args: argparse.Namespace) -> None:
    """Pause a project through the daemon's durable pause controller."""
    data = _http_call(
        "POST",
        f"{args.daemon_url}/api/pause/project",
        json={"target_id": args.target_id, "reason": args.reason},
    )
    if data is not None:
        print(json.dumps(data, indent=2))


def _cmd_pause_model(args: argparse.Namespace) -> None:
    """Pause a model profile through the daemon's durable pause controller."""
    data = _http_call(
        "POST",
        f"{args.daemon_url}/api/pause/model",
        json={"target_id": args.target_id, "reason": args.reason},
    )
    if data is not None:
        print(json.dumps(data, indent=2))


def _cmd_resume_project(args: argparse.Namespace) -> None:
    """Resume a project through the daemon's durable pause controller."""
    data = _http_call("POST", f"{args.daemon_url}/api/resume/project", json={"target_id": args.target_id})
    if data is not None:
        print(json.dumps(data, indent=2))


def _cmd_resume_model(args: argparse.Namespace) -> None:
    """Resume a model profile through the daemon's durable pause controller."""
    data = _http_call("POST", f"{args.daemon_url}/api/resume/model", json={"target_id": args.target_id})
    if data is not None:
        print(json.dumps(data, indent=2))


def _cmd_login(args: argparse.Namespace) -> None:
    from general_ludd.auth.browser_login import (
        SERVICE_PRESETS,
        BrowserLoginFlow,
        EnvCredentialStore,
        OpenBaoCredentialStore,
        list_services,
    )
    from general_ludd.secrets.manager import SecretsManager

    store: EnvCredentialStore | OpenBaoCredentialStore

    if getattr(args, "list", False):
        services = list_services()
        print("Available login services:")
        for svc in services:
            cfg = SERVICE_PRESETS[svc]
            kind = "OAuth2" if cfg.token_url else "API key"
            print(f"  {svc:14}  {cfg.display_name:18}  {kind}")
        return

    service = getattr(args, "service", None)
    if not service:
        print("Usage: gludd login <service>", file=sys.stderr)
        print("Use --list to see available services.", file=sys.stderr)
        sys.exit(2)

    service_lower = service.lower()
    if service_lower not in SERVICE_PRESETS:
        print(f"Unknown service: {service!r}", file=sys.stderr)
        print(f"Available: {', '.join(list_services())}", file=sys.stderr)
        sys.exit(2)

    store_kind = getattr(args, "store", "env")
    timeout = getattr(args, "timeout", 120.0)

    if store_kind == "openbao":
        try:
            sm = SecretsManager()
            sm.connect()
            store = OpenBaoCredentialStore(sm)
        except Exception as exc:
            print(
                f"OpenBao not available: {exc}\nUse --store=env or start the OpenBao container first.",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        store = EnvCredentialStore()

    flow = BrowserLoginFlow(service_lower, store=store)
    token = flow.run(timeout=timeout)
    if token is None:
        sys.exit(1)


def _cmd_onboard(args: argparse.Namespace) -> None:
    import datetime as _dt
    from pathlib import Path

    from general_ludd.onboard import SUPPORTED_PROVIDERS, get_provider

    supported_str = ", ".join(sorted(SUPPORTED_PROVIDERS))

    provider_name = getattr(args, "provider", None)
    if not provider_name:
        print(
            "Error: onboard requires a provider argument.\n"
            f"Supported providers: {supported_str}\n"
            "Example: gludd onboard aws",
            file=sys.stderr,
        )
        sys.exit(2)

    if provider_name not in SUPPORTED_PROVIDERS:
        print(
            f"Error: unknown provider '{provider_name}'.\nSupported providers: {supported_str}",
            file=sys.stderr,
        )
        sys.exit(2)

    provider = get_provider(
        provider_name,
        project_id=getattr(args, "project", None),
        subscription_id=getattr(args, "subscription", None),
    )
    dry_run = bool(getattr(args, "dry_run", False))
    role_arn = getattr(args, "role_arn", None)
    region = getattr(args, "region", None) or "us-east-1"
    token = getattr(args, "token", None)

    print(f"Phase 1: IAM role creation guidance ({provider_name})")
    if role_arn:
        print(f"  Role ARN supplied via --role-arn: {role_arn} (skipping guide)")
    elif dry_run:
        print(f"  [dry-run] Would call {provider_name}.create_role_instructions(). Using canned IAM role guidance.")
        role_arn = f"arn:{provider_name}:iam::000000000000:role/gludd-dry-run"
    else:
        try:
            guide = provider.create_role_instructions()
        except NotImplementedError as exc:
            print(f"  Provider not yet implemented: {exc}", file=sys.stderr)
            sys.exit(3)
        print(guide)
        try:
            role_arn = input("Paste the created role ARN and press Enter: ").strip()
        except EOFError:
            role_arn = ""
        if not role_arn:
            print("Error: a role ARN is required.", file=sys.stderr)
            sys.exit(2)

    print(f"Phase 2: token acquisition guidance ({provider_name})")
    if dry_run:
        print(
            f"  [dry-run] Would call {provider_name}.token_acquisition_guide(). "
            "Using canned token acquisition guidance."
        )
    else:
        try:
            token_guide = provider.token_acquisition_guide()
        except NotImplementedError as exc:
            print(f"  Provider not yet implemented: {exc}", file=sys.stderr)
            sys.exit(3)
        print(token_guide)

    print(f"Phase 3: token input ({provider_name})")
    if token:
        print("  Token supplied via --token (skipping prompt).")
    elif dry_run:
        token = "dry-run-canned-token"
        print("  --dry-run set: using canned token (no prompt).")
    else:
        try:
            token = input("Paste the API token (input hidden): ").strip()
        except EOFError:
            token = ""
        if not token:
            print("Error: a token is required.", file=sys.stderr)
            sys.exit(2)

    print(f"Phase 4: token + role validation ({provider_name})")
    config_dir_arg = getattr(args, "config_dir", None)
    config_dir = Path(config_dir_arg) if config_dir_arg else Path.home() / ".config" / "gludd"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "onboarded-provider.json"

    if dry_run:
        print("  --dry-run set: skipping live validation (canned success).")
        ok, details = True, {"role_arn": role_arn, "dry_run": True}
    else:
        try:
            ok, details = provider.validate_token_and_role(token, role_arn, region)
        except NotImplementedError as exc:
            print(f"  Provider not yet implemented: {exc}", file=sys.stderr)
            sys.exit(3)

    if not ok:
        reason = details.get("reason", "unknown") if isinstance(details, dict) else "unknown"
        print(
            f"Validation FAILED: {reason}\n"
            "Remediation: re-check the token, role ARN, and region; recreate the "
            "token if it may have expired; verify the IAM trust policy.",
            file=sys.stderr,
        )
        sys.exit(1)

    validated_at = _dt.datetime.now(_dt.UTC).isoformat()
    config_payload = {
        "provider": provider_name,
        "role_arn": role_arn,
        "region": region,
        "token_validated_at": validated_at,
    }
    config_path.write_text(json.dumps(config_payload, indent=2))
    print(
        f"\nOnboard complete: provider={provider_name} region={region} role={role_arn}\nConfig written: {config_path}"
    )
    sys.exit(0)


def _cmd_cloud_iam_generate(args: argparse.Namespace) -> None:
    from general_ludd.cloud.core import generate_cloud_role

    generated = generate_cloud_role(args.provider, args.persona)
    print(json.dumps(generated, indent=2, default=str))
    if generated["status"] == "error":
        sys.exit(1)


def _cmd_cloud_iam_validate(args: argparse.Namespace) -> None:
    from pathlib import Path

    from general_ludd.cloud.core import validate_cloud_role

    role_path = Path(args.file)
    if not role_path.is_file():
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    try:
        role_definition = json.loads(role_path.read_text())
    except json.JSONDecodeError as exc:
        print(f"Error: invalid JSON in {args.file}: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(role_definition, dict):
        print(f"Error: role definition must be a JSON object, got {type(role_definition).__name__}", file=sys.stderr)
        sys.exit(1)

    validated = validate_cloud_role(args.provider, role_definition)
    print(json.dumps(validated, indent=2, default=str))
    if validated["status"] == "invalid" or validated["status"] == "error":
        sys.exit(1)


def _cmd_cloud_game_generate_multi(args: argparse.Namespace) -> None:
    """Delegates to /api/generate/create with --type game."""
    data = _http_call(
        "POST",
        f"{args.daemon_url}/api/generate/create",
        json={
            "project_type": "game",
            "description": args.description,
            "planner_model": args.planner,
            "coder_model": args.coder,
            "reviewer_model": args.reviewer,
            "max_review_rounds": args.review_rounds,
        },
        timeout=120.0,
    )
    print(json.dumps(data, indent=2, default=str))
    sys.exit(0)


def _cmd_cloud_generate_list_types(args: argparse.Namespace) -> None:
    data = _http_call(
        "POST",
        f"{args.daemon_url}/api/generate/list-types",
        timeout=10.0,
    )
    print(json.dumps(data, indent=2, default=str))
    sys.exit(0)


def _cmd_cloud_generate_create(args: argparse.Namespace) -> None:
    data = _http_call(
        "POST",
        f"{args.daemon_url}/api/generate/create",
        json={
            "project_type": args.project_type,
            "description": args.description,
            "planner_model": args.planner,
            "coder_model": args.coder,
            "reviewer_model": args.reviewer,
            "max_review_rounds": args.review_rounds,
        },
        timeout=120.0,
    )
    print(json.dumps(data, indent=2, default=str))
    sys.exit(0)


def _cmd_cloud_generate_validate(args: argparse.Namespace) -> None:
    data = _http_call(
        "POST",
        f"{args.daemon_url}/api/generate/validate",
        json={
            "project_type": args.project_type,
            "project_dir": args.path,
        },
        timeout=10.0,
    )
    print(json.dumps(data, indent=2, default=str))
    if not data.get("valid", False):
        sys.exit(1)
    sys.exit(0)


def _cmd_searx(args: argparse.Namespace) -> None:
    from general_ludd.searx.config import SearXConfig
    from general_ludd.searx.install import ensure_searx_initialized, ensure_searx_installed
    from general_ludd.searx.server import SearXServer

    cmd = getattr(args, "searx_command", None)
    if cmd == "start":
        ensure_searx_installed()
        ensure_searx_initialized()
        server = SearXServer()
        if server.ensure_started():
            print(f"SearXNG running at {server.get_instance_url()}")
        else:
            print("ERROR: SearXNG failed to start", file=sys.stderr)
            sys.exit(1)
    elif cmd == "stop":
        server = SearXServer()
        server.stop()
        print("SearXNG stopped")
    elif cmd == "status":
        server = SearXServer(external_url=None)
        if server.is_running():
            print(f"SearXNG running at {server.get_instance_url()}")
        else:
            print("SearXNG not running")
            sys.exit(1)
    elif cmd == "config":
        path = SearXConfig().generate()
        print(f"Settings written to {path}")
        import yaml

        with open(path) as f:
            print(yaml.safe_dump(yaml.safe_load(f), default_flow_style=False))


def _cmd_config_terraform_get(args: argparse.Namespace) -> None:
    from pathlib import Path

    from general_ludd.config.user_config import TerraformConfig, UserConfig

    config_path = Path.home() / ".config" / "general-ludd" / "user.yml"
    tc: TerraformConfig
    if config_path.exists():
        user_cfg = UserConfig.from_yaml(config_path)
        tc = user_cfg.terraform
    else:
        tc = TerraformConfig()

    if args.field:
        val = getattr(tc, args.field, None)
        if val is None:
            print(f"Unknown terraform field: {args.field}")
            sys.exit(1)
        print(f"{args.field} = {val}")
    else:
        data = tc.model_dump()
        for k, v in sorted(data.items()):
            if isinstance(v, str):
                print(f'{k:28} = "{v}"')
            else:
                print(f"{k:28} = {v}")


def _cmd_config_terraform_set(args: argparse.Namespace) -> None:
    from pathlib import Path

    from general_ludd.config.user_config import TerraformConfig

    valid_fields = set(TerraformConfig.model_fields.keys())
    if args.field not in valid_fields:
        print(f"Unknown terraform field: {args.field}")
        print(f"Valid fields: {', '.join(sorted(valid_fields))}")
        sys.exit(1)

    config_path = Path.home() / ".config" / "general-ludd" / "user.yml"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    import yaml

    data: dict[str, object] = {}
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

    raw_terraform: object = data.get("terraform", {})
    tf_data: dict[str, object] = cast(dict[str, object], raw_terraform) if isinstance(raw_terraform, dict) else {}
    tf_data[args.field] = args.value

    try:
        TerraformConfig.model_validate(tf_data)
    except Exception as exc:
        print(f"Validation error for {args.field}={args.value!r}: {exc}", file=sys.stderr)
        sys.exit(1)

    data["terraform"] = tf_data
    with open(config_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    print(f"terraform.{args.field} = {args.value}")
    print(f"Written to {config_path}")


def _cmd_smoke(args: argparse.Namespace) -> None:
    from general_ludd.output_templates import render_smoke_list, render_smoke_report
    from general_ludd.smoke import list_smoke_tests, run_smoke

    wants_list = bool(getattr(args, "list", False)) or getattr(args, "provider", None) in (None, "list")

    provider = None if getattr(args, "provider", None) == "list" else getattr(args, "provider", None)
    if wants_list:
        tests = list_smoke_tests(provider=provider)
        print(render_smoke_list(tests, json_output=bool(args.json), template_name=args.output_template))
        return

    if not args.test:
        print("Usage: gludd smoke <provider> <test> [--live|--provisioned] [--json]", file=sys.stderr)
        sys.exit(1)

    try:
        report = run_smoke(
            str(args.provider),
            str(args.test),
            live=bool(args.live),
            timeout=float(args.timeout),
            max_cost_usd=float(args.max_cost_usd),
            base_url=args.base_url,
            model=args.model,
            provisioned=bool(args.provisioned),
            region=args.region,
            gpu_count=int(args.gpu_count),
            engine=str(args.engine),
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(2)

    rendered_report = render_smoke_report(report, json_output=bool(args.json), template_name=args.output_template)
    if args.output:
        output_path = Path(str(args.output))
        output_path.write_text(rendered_report + chr(10), encoding="utf-8")
    print(rendered_report)

    if report["status"] != "pass":
        sys.exit(1)


def main() -> None:
    parser, subcommand_map = build_parser()
    args = parser.parse_args()
    if args.func is None:
        if args.command in subcommand_map:
            subcommand_map[args.command].print_help()
            sys.exit(0)
        else:
            parser.print_help()
            sys.exit(1)
    args.func(args)


def _cmd_daemon(args: argparse.Namespace) -> None:
    import secrets
    import signal
    import subprocess

    log_level = args.log_level.upper()
    logging.basicConfig(level=getattr(logging, log_level), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # Ensure every record carries a project_id attribute (None at the daemon
    # level) so per-project log lines and formatters work uniformly.
    from general_ludd.logging.project_log import install_project_log_filter

    install_project_log_filter()

    config_dir = getattr(args, "config_dir", None)
    templates_dir = getattr(args, "templates_dir", None)
    playbooks_dir = getattr(args, "playbooks_dir", None)

    bind_host = args.host

    psk = os.environ.get("GLUDD_PSK", "")
    if bind_host not in ("127.0.0.1", "localhost", "::1"):
        if not psk:
            psk = secrets.token_urlsafe(32)
        print(f"\n  Daemon binding to external interface: {bind_host}:{args.port}")
        print(f"  Pre-shared key (PSK): {psk}")
        print(f"  Clients must send: Authorization: Bearer {psk}\n")

    # W3.5 (M8): SQLite-only — clamp to a single worker (no hardware-based
    # multi-worker default; multiple workers race on one SQLite file).
    cmd = _build_daemon_start_cmd(
        host=bind_host,
        port=args.port,
        workers=_clamp_workers_for_sqlite(args.workers),
    )
    cmd_env = _build_daemon_env(
        config_dir=config_dir,
        templates_dir=templates_dir,
        playbooks_dir=playbooks_dir,
        tick_interval=args.tick_interval,
        log_level=args.log_level,
        psk=psk,
    )
    env = os.environ.copy()
    env.update(cmd_env)
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=env,
    )

    shutdown_signum: int | None = None
    shutdown_complete = threading.Event()
    watchdog_started = False

    def _kill_after_timeout() -> None:
        if shutdown_complete.wait(_DAEMON_SHUTDOWN_TIMEOUT_SECONDS):
            return
        try:
            proc.kill()
        except ProcessLookupError:
            return

    def _forward_signal(signum: int, frame: Any) -> None:
        nonlocal shutdown_signum, watchdog_started
        if shutdown_signum is not None:
            return
        shutdown_signum = signum
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        if not watchdog_started:
            watchdog_started = True
            namespace = os.environ.get("GLUDD_PROJECT_NAMESPACE", "gludd")
            threading.Thread(
                target=_kill_after_timeout,
                name=f"{namespace}-daemon-shutdown-watchdog",
                daemon=True,
            ).start()

    signal.signal(signal.SIGTERM, _forward_signal)
    signal.signal(signal.SIGINT, _forward_signal)

    try:
        proc.wait()
    except KeyboardInterrupt:
        _forward_signal(signal.SIGINT, None)
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
    finally:
        shutdown_complete.set()
    if shutdown_signum is not None:
        sys.exit(128 + shutdown_signum)
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
    data = _http_call("POST", f"{args.daemon_url}/api/todos", json=payload, timeout=10.0, ok_codes=(200, 201))
    if data is None:
        return
    print(json.dumps(data, indent=2))


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
                    cfiles.append(
                        {
                            "name": f,
                            "path": fp,
                            "size_bytes": st.st_size,
                            "modified": st.st_mtime,
                        }
                    )
                except OSError:
                    cfiles.append({"name": f, "path": fp, "size_bytes": 0, "modified": 0})
    info["config_dir"] = cdir
    info["config_files"] = cfiles

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
                f"{args.daemon_url}/api/todos/{args.todo_id}{params}",
                timeout=10.0,
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
            cfg_count = data.get("config_file_count")
            print(f"Config files: {cfg_count if cfg_count is not None else 'unknown'}")
            fs_avail = data.get("filestore_available")
            print(f"Filestore:   {'available' if fs_avail else 'unavailable'}")
            bins = data.get("filestore_binaries", [])
            versions = data.get("binary_versions", {})
            if versions:
                for name, ver in sorted(versions.items()):
                    stored = any((b.get("name") if isinstance(b, dict) else b) == name for b in bins)
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
    data = _http_call("GET", f"{args.daemon_url}/api/todos", params=params, timeout=10.0)
    if data is None:
        return
    print(json.dumps(data, indent=2))


def _cmd_log_level(args: argparse.Namespace) -> None:
    data = _http_call("POST", f"{args.daemon_url}/admin/log-level", json={"level": args.level}, timeout=10.0)
    if data is None:
        return
    print(f"Log level changed to {data['level']}")


def _cmd_deployments(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/api/deployments", timeout=10.0)
    if data is None:
        return
    print(json.dumps(data, indent=2))


def _cmd_version(args: argparse.Namespace) -> None:
    from general_ludd import __version__

    print(f"general-ludd-agent {__version__}")


def _cmd_health(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/healthz", timeout=10.0)
    if data is None:
        return
    print(json.dumps(data, indent=2))


def _cmd_project_add(args: argparse.Namespace) -> None:
    import json

    try:
        resp = httpx.post(
            f"{args.daemon_url}/admin/projects",
            content=json.dumps(
                {
                    "name": args.name,
                    "weight": args.weight,
                    "description": args.description,
                    "repo_url": args.repo_url,
                    "workspace_path": args.workspace_path,
                    "dispatch_mode": args.dispatch_mode,
                }
            ),
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
            f"{args.daemon_url}/admin/projects/{args.project_id}",
            timeout=10.0,
        )
        if resp.status_code == 200:
            print(f"Project removed: {args.project_id}")
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except httpx.ConnectError as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_models_search(args: argparse.Namespace) -> None:
    data = _http_call(
        "POST",
        f"{args.daemon_url}/admin/models/search",
        json={"query": args.query, "limit": args.limit},
        timeout=30.0,
    )
    if data is None:
        return
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


def _cmd_models_searx_search(args: argparse.Namespace) -> None:
    """Search for models via SearXNG meta-search engine."""
    from general_ludd.infra.model_search import SearXModelSearch

    searcher = SearXModelSearch(base_url=args.searx_url)
    results = searcher.search_models(args.query, source=args.source)
    if not results:
        print("No models found via SearXNG.")
        return
    print(f"Found {len(results)} model(s) for query: {args.query!r}")
    for r in results:
        print(f"\n  {r.name}")
        if r.params_count:
            print(f"    Params: {r.params_count}B")
        if r.license:
            print(f"    License: {r.license}")
        if r.quantizations_available:
            print(f"    Quants: {', '.join(r.quantizations_available)}")
        print(f"    URL: {r.source_url}")


def _cmd_models_deploy(args: argparse.Namespace) -> None:
    """Deploy a model found via SearXNG search."""
    import json

    from general_ludd.infra.model_deploy import deploy_from_search

    try:
        result = deploy_from_search(
            args.name,
            provider=args.provider,
            engine=args.engine,
            workload_type=args.workload_type,
            searx_url=args.searx_url,
            region=args.region,
            gpu_count=args.gpu_count,
            max_cost=args.max_cost,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, indent=2, default=str))


def _cmd_models_downloaded(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/admin/models/downloaded", timeout=10.0)
    if data is None:
        return
    models = data.get("profiles", data.get("models", []))
    if not models:
        print("No models downloaded.")
        return
    for m in models:
        print(f"  {m['model_id']}")
        print(f"    Path: {m.get('local_path', 'N/A')}")
        print(f"    Engine: {m.get('engine', 'N/A')}")
        print()


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
            models = data.get("profiles", data.get("models", []))
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
    data = _http_call("GET", f"{args.daemon_url}/admin/models/discovered", timeout=10.0)
    if data is None:
        return
    profiles = data.get("profiles", [])
    if not profiles:
        print("No auto-discovered models. Run 'gludd models discover' first.")
        return
    print(f"Discovered profiles: {len(profiles)}")
    for p in profiles:
        enabled = "[enabled]" if p.get("enabled", True) else "[disabled]"
        print(f"  {p['display_name']} ({p['model_profile_id']}) {enabled}")


def _cmd_models_list(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/admin/models", timeout=10.0)
    if data is None:
        return
    models = data.get("profiles", data.get("models", []))
    if models:
        for m in models:
            print(f"  {m.get('model_id', '?'):<30} {m.get('provider', '?'):<12} {m.get('model', '?')}")
    else:
        print("No models registered.")


def _cmd_models_add(args: argparse.Namespace) -> None:
    payload: dict[str, Any] = {
        "model_id": args.model_id,
        "provider": args.provider,
        "model": args.model,
    }
    if args.api_key_env:
        payload["api_key_env"] = args.api_key_env
    _http_call("POST", f"{args.daemon_url}/admin/models", json=payload, timeout=10.0, ok_codes=(200, 201))
    print(f"Model added: {args.model_id}")


def _cmd_models_remove(args: argparse.Namespace) -> None:
    _http_call("DELETE", f"{args.daemon_url}/admin/models/{args.model_id}", timeout=10.0)
    print(f"Model removed: {args.model_id}")


def _cmd_model_performance(args: argparse.Namespace) -> None:
    """Show model performance data."""
    params: dict[str, str] = {}
    if args.service:
        params["service"] = args.service
    if args.task_type:
        params["task_type"] = args.task_type
    data = _http_call("GET", f"{args.daemon_url}/admin/models/performance", params=params, timeout=10.0)
    if data is None:
        return
    rows = data.get("performance", [])
    if not rows:
        print("No performance data available.")
        return
    print(f"{'service':<20} {'model':<25} {'task_type':<15} {'success':<8} {'latency':<10} {'cost':<12} {'calls':<8}")
    print("-" * 100)
    for r in rows:
        svc = r.get("service", "")[:19]
        mdl = r.get("model_name", "")[:24]
        tt = r.get("task_type", "")[:14]
        succ = f"{r.get('success_rate', 0):.2f}"
        lat = f"{r.get('avg_latency_ms', 0):.0f}ms"
        cost = f"${r.get('avg_cost_usd', 0):.6f}"
        calls = str(r.get("sample_count", 0))
        print(f"{svc:<20} {mdl:<25} {tt:<15} {succ:<8} {lat:<10} {cost:<12} {calls:<8}")


def _cmd_model_ranking(args: argparse.Namespace) -> None:
    """Show model rankings for a specific task type."""
    params = {"task_type": args.task_type, "strategy": args.strategy}
    data = _http_call("GET", f"{args.daemon_url}/admin/models/ranking", params=params, timeout=10.0)
    if data is None:
        return
    ranking = data.get("ranking", [])
    if not ranking:
        print(f"No ranking data for task_type={args.task_type!r}.")
        return
    print(f"Task type: {data.get('task_type', '?')}  Strategy: {data.get('strategy', '?')}")
    print(
        f"{'rank':<5} {'service':<20} {'model':<25} "
        f"{'score':<8} {'success':<8} {'latency':<10} {'cost':<12} {'calls':<8}"
    )
    print("-" * 100)
    for i, r in enumerate(ranking, 1):
        svc = r.get("service", "")[:19]
        mdl = r.get("model_name", "")[:24]
        score = f"{r.get('score', 0):.4f}"
        succ = f"{r.get('success_rate', 0):.2f}"
        lat = f"{r.get('avg_latency_ms', 0):.0f}ms"
        cost = f"${r.get('avg_cost_usd', 0):.6f}"
        calls = str(r.get("sample_count", 0))
        print(f"{i:<5} {svc:<20} {mdl:<25} {score:<8} {succ:<8} {lat:<10} {cost:<12} {calls:<8}")


def _cmd_model_router_status(args: argparse.Namespace) -> None:
    """Show current router configuration and active model selections."""
    data = _http_call("GET", f"{args.daemon_url}/admin/models/router/status", timeout=10.0)
    if data is None:
        return
    if data.get("status") == "not_initialized":
        print("Model performance router is not initialized.")
        return
    config = data.get("config", {})
    strategies = config.get("strategies", {})
    defaults = config.get("defaults", {})
    print("Model Performance Router")
    print(f"  Status: {data.get('status', '?')}")
    if strategies:
        print("  Per-task strategies:")
        for tt, strat in sorted(strategies.items()):
            print(f"    {tt:<20} {strat}")
    else:
        print("  Per-task strategies: (none set)")
    print(f"  Min calls: {defaults.get('min_calls', '?')}")
    print(f"  Default fallback: {defaults.get('default_fallback', '?')}")


def _cmd_model_router_set(args: argparse.Namespace) -> None:
    """Set routing strategy for a task type."""
    data = _http_call(
        "PUT",
        f"{args.daemon_url}/admin/models/router/config",
        json={"task_type": args.task_type, "strategy": args.strategy},
        timeout=10.0,
    )
    if data is None:
        return
    print(f"Strategy set: task_type={data.get('task_type', '?')} strategy={data.get('strategy', '?')}")


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
    data = _http_call(
        "POST",
        f"{args.daemon_url}/admin/models/local/serve",
        json=payload,
        timeout=30.0,
        ok_codes=(200, 201),
    )
    if data is None:
        return
    print(json.dumps(data, indent=2))


def _cmd_worktree_scan(args: argparse.Namespace) -> None:
    params: dict[str, str] = {}
    if args.path:
        params["watch_paths"] = args.path
    data = _http_call("POST", f"{args.daemon_url}/admin/worktree/scan", params=params, timeout=30.0)
    if data is None:
        return
    todos = data.get("todos", [])
    tracked = data.get("tracked_count", 0)
    print(f"Tracked worktrees: {tracked}")
    print(f"Abandoned worktrees with todos: {len(todos)}")
    for todo in todos:
        print(f"  - {todo['title']} ({todo['queue']})")


def _cmd_worktree_status(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/admin/worktree/status", timeout=10.0)
    if data is None:
        return
    wts = data.get("tracked_worktrees", [])
    print(f"Tracked worktrees: {len(wts)}")
    for wt in wts:
        status_line = f"  {wt['path']}"
        if wt["todo_id"]:
            status_line += f" [todo: {wt['todo_id']}]"
        if wt["has_agents_md"]:
            status_line += " [AGENTS.md]"
        print(status_line)


def _cmd_mcp_search(args: argparse.Namespace) -> None:
    data = _http_call(
        "POST",
        f"{args.daemon_url}/admin/mcp/catalog/search",
        json={"query": args.query, "limit": 20},
        timeout=30.0,
    )
    if data is None:
        return
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


def _cmd_mcp_list(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/admin/mcp/catalog/servers", timeout=10.0)
    if data is None:
        return
    servers = data.get("servers", [])
    if not servers:
        print("No MCP servers known.")
        return
    for s in servers:
        print(f"  {s}")


def _cmd_mcp_info(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/admin/mcp/catalog/servers/{args.name}", timeout=10.0)
    if data is None:
        return
    print(json.dumps(data, indent=2))


def _cmd_skills_search(args: argparse.Namespace) -> None:
    data = _http_call(
        "POST",
        f"{args.daemon_url}/admin/skills/catalog/search",
        json={"query": args.query, "limit": 20},
        timeout=30.0,
    )
    if data is None:
        return
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


def _cmd_skills_list(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/admin/skills/catalog", timeout=10.0)
    if data is None:
        return
    print(json.dumps(data, indent=2))


def _cmd_skills_install(args: argparse.Namespace) -> None:
    data = _http_call(
        "POST",
        f"{args.daemon_url}/admin/skills/catalog/install",
        json={"name": args.name},
        timeout=30.0,
        ok_codes=(200, 201),
    )
    if data is None:
        return
    print(f"Installed to: {data.get('installed', 'N/A')}")


def _cmd_compute_endpoints(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/admin/compute/endpoints", timeout=10.0)
    if data is None:
        return
    print(json.dumps(data, indent=2))


def _cmd_compute_register(args: argparse.Namespace) -> None:
    payload = {
        "id": args.id,
        "url": args.url,
        "model": args.model,
        "max_concurrent": args.max_concurrent,
    }
    data = _http_call(
        "POST",
        f"{args.daemon_url}/admin/compute/endpoints",
        json=payload,
        timeout=10.0,
        ok_codes=(200, 201),
    )
    if data is None:
        return
    print(json.dumps(data, indent=2))


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


def _cmd_compute_azure_preflight(args: argparse.Namespace) -> None:
    data = _http_call(
        "POST",
        f"{args.daemon_url}/admin/compute/azure/preflight",
        json={
            "gpu_type": args.gpu,
            "gpu_count": args.gpu_count,
            "region": args.region,
        },
        timeout=60.0,
        ok_codes=(200,),
    )
    if data is not None:
        print(json.dumps(data, indent=2))


def _cmd_compute_launch(args: argparse.Namespace) -> None:
    payload: dict[str, Any] = {
        "provider": args.provider,
        "gpu_type": args.gpu,
        "model_name": args.model,
        "deploy_type": args.deploy_type,
        "gpu_count": args.gpu_count,
        "max_cost_usd": args.max_cost,
        "timeout_minutes": args.timeout_minutes,
        "disk_size_gb": args.disk_size_gb,
        "container_image": args.container_image,
        "hourly_rate_usd": args.hourly_rate,
        "spot": not args.no_spot,
        "allowed_cidr": args.allowed_cidr,
        "ssh_public_key_path": args.ssh_public_key_path,
        "max_concurrent": args.max_concurrent,
        "engine": args.engine,
        "workload_type": args.workload_type,
    }
    if args.region:
        payload["region"] = args.region
    data = _http_call(
        "POST",
        f"{args.daemon_url}/admin/compute/deploy",
        json=payload,
        timeout=300.0,
        ok_codes=(200, 201),
    )
    if data is None:
        return
    print(json.dumps(data, indent=2))


def _cmd_compute_destroy(args: argparse.Namespace) -> None:
    data = _http_call(
        "DELETE",
        f"{args.daemon_url}/admin/compute/destroy/{args.instance_id}",
        timeout=300.0,
        ok_codes=(200,),
    )
    if data is None:
        return
    print(f"Destroyed: {data.get('destroyed', args.instance_id)}")


def _cmd_scores(args: argparse.Namespace) -> None:
    params = {}
    if args.task_type:
        params["task_type"] = args.task_type
    data = _http_call("GET", f"{args.daemon_url}/admin/benchmark/scores", params=params, timeout=10.0)
    if data is None:
        return
    print(json.dumps(data, indent=2))


def _cmd_leaderboard(args: argparse.Namespace) -> None:
    params = {}
    if args.task_type:
        params["task_type"] = args.task_type
    data = _http_call("GET", f"{args.daemon_url}/admin/benchmark/leaderboard", params=params, timeout=10.0)
    if data is None:
        return
    entries = data.get("leaderboard", [])
    if not entries:
        print("No benchmark data yet. Run tasks to accumulate scores.")
        return
    print(f"{'rank':<5} {'prompt':<25} {'model':<20} {'score':<8} {'cost':<10} {'samples':<8} {'task_type':<15}")
    print("-" * 100)
    for i, e in enumerate(entries, 1):
        prompt = (e.get("prompt_profile_id") or "default")[:24]
        model = e.get("model_profile_id", "")[:19]
        score = f"{e.get('composite_score', 0):.3f}"
        cost = f"${e.get('avg_cost_usd', 0):.4f}"
        samples = str(e.get("sample_count", 0))
        tt = e.get("task_type", "")[:14]
        print(f"{i:<5} {prompt:<25} {model:<20} {score:<8} {cost:<10} {samples:<8} {tt:<15}")


def _cmd_help(args: argparse.Namespace) -> None:
    print(MAN_PAGE)
    sys.exit(0)


def _cmd_chat(args: argparse.Namespace) -> None:
    """Interactive chat REPL or --eval single-turn mode."""
    import asyncio

    from general_ludd.chat import ChatSession

    daemon_url = getattr(args, "daemon_url", None)

    if getattr(args, "search", None) is not None:
        if not daemon_url:
            print("Error: --search requires --daemon-url", file=sys.stderr)
            sys.exit(1)
        try:
            resp = httpx.post(
                f"{daemon_url}/api/chat/sessions/search",
                json={"query": args.search, "limit": 20},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if not results:
                print(f"No sessions matching {args.search!r}.")
                return
            print(f"Search results for {args.search!r} ({len(results)}):")
            for s in results:
                ts = str(s.get("timestamp", "?"))
                model = str(s.get("model", "?"))
                count = s.get("message_count", 0)
                preview = str(s.get("preview", ""))[:72]
                file_path = str(s.get("file", "?"))
                match = str(s.get("match_source", "preview"))
                print(f"  {ts}  model={model}  messages={count}  match={match}")
                print(f"    file: {file_path}")
                if preview:
                    print(f"    preview: {preview}")
                print()
        except Exception as exc:
            print(f"Daemon error: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    if getattr(args, "list_sessions", False):
        if daemon_url:
            try:
                resp = httpx.get(
                    f"{daemon_url}/api/chat/sessions",
                    timeout=10.0,
                )
                resp.raise_for_status()
                data = resp.json()
                sessions = data.get("sessions", [])
            except Exception as exc:
                print(f"Daemon error: {exc}", file=sys.stderr)
                sys.exit(1)
        else:
            sessions = ChatSession.list_sessions()
        if not sessions:
            print("No saved chat sessions.")
            return
        source = " (daemon)" if daemon_url else ""
        print(f"Saved sessions{source} ({len(sessions)}):")
        for s in sessions:
            ts = s.get("timestamp", "?")
            model = s.get("model", "?")
            count = s.get("message_count", 0)
            preview = str(s.get("preview", ""))
            file_path = s.get("file", "?")
            print(f"  {ts}  model={model}  messages={count}")
            print(f"    file: {file_path}")
            if preview:
                preview_str = preview[:72] + ("..." if len(preview) > 72 else "")
                print(f"    preview: {preview_str}")
            print()
        return

    if daemon_url and not getattr(args, "eval", None):
        print("Error: --daemon-url requires --list-sessions, --search, or --eval", file=sys.stderr)
        sys.exit(1)
        return

    history_file = getattr(args, "history", None)
    resume = getattr(args, "resume", False)
    save_interval = getattr(args, "save_interval", 5)

    export_format = getattr(args, "export", None)
    if export_format:
        from general_ludd.chat.session import export_session

        source_file = history_file
        if not source_file:
            dummy = ChatSession(model=args.model)
            latest = dummy._find_latest_session()
            if latest is None:
                print("No saved session to export.", file=sys.stderr)
                sys.exit(1)
            source_file = str(latest)
        out_arg = getattr(args, "export_output", None)
        result = export_session(
            Path(source_file),
            format=export_format,
            output_file=Path(out_arg) if out_arg else None,
        )
        if isinstance(result, Path):
            print(f"Wrote {export_format} export to {result}")
        else:
            print(result)
        return

    session = ChatSession(
        model=args.model,
        system_prompt=args.system_prompt,
        eval_mode=args.eval is not None,
        api_base_url=args.api_base,
        api_key=args.api_key,
        project_dir=getattr(args, "project_dir", None),
        history_file=history_file,
        save_interval=save_interval,
        resume=resume,
        max_context=getattr(args, "max_context", None),
    )

    if args.eval:
        if getattr(args, "stream", False):
            asyncio.run(session.stream_response(args.eval))
        else:
            result = asyncio.run(session.run_once(args.eval))
            print(result)
    else:
        asyncio.run(session.start_repl())


def _cmd_filestore_list(args: argparse.Namespace) -> None:
    try:
        resp = httpx.get(
            f"{args.daemon_url}/admin/filestore/list",
            params={"path": args.path},
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"Path: {data.get('path', '?')} ({data.get('count', '?')} entries)")
            for e in data.get("entries", []):
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
                print(f"[Binary file: {data.get('path', '?')}]")
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
            if data.get("success"):
                print(f"Downloaded {data.get('binary', '?')} to filestore")
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
            print(f"Stored binaries: {data.get('count', 0)}")
            for b in data.get("binaries", []):
                print(f"  {b['name']} ({b['size']}B)")
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_selftest(args: argparse.Namespace) -> None:
    data = _http_call("POST", f"{args.daemon_url}/admin/selftest", timeout=120.0)
    if data is None:
        return
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


def _cmd_preflight(args: argparse.Namespace) -> None:
    """Run the preflight quality gate locally (no daemon required)."""
    from general_ludd.quality.preflight import run_preflight

    strict_tf = bool(getattr(args, "strict_terraform_import", False))
    result = run_preflight(strict_terraform_import=strict_tf)
    overall = result.get("overall", "FAIL")
    print(f"Preflight: {overall}")
    print(f"Passed:    {result.get('passed_count', 0)}/{result.get('total_count', 0)}")
    for chk in cast("list[dict[str, object]]", result.get("checks", [])):
        name = chk.get("name", "?")
        passed = chk.get("passed", False)
        marker = "PASS" if passed else "FAIL"
        line = f"  [{marker}] {name}"
        if name == "terraform_collection_import_audit":
            issues = cast("list[dict[str, object]]", chk.get("issues", []) or [])
            line += f"  ({len(issues)} importer issue(s))"
            for issue in issues:
                line += f"\n        {issue.get('severity', '?')}: {issue.get('message', '')}"
        elif not passed and chk.get("violations"):
            for v in cast("list[object]", chk["violations"])[:3]:
                line += f"\n        - {v}"
        print(line)
    if overall != "PASS":
        sys.exit(1)


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


# --------------------------------------------------------------------------- #
# Daemon-spawn input hardening
#
# _cmd_daemon spawns the daemon via subprocess.Popen(cmd, start_new_session=True,
# close_fds=True). Even though Popen is given a *list* argv (no shell), the host
# and port flow into the "--bind HOST:PORT" token and the log-level / path args
# flow into the child's environment. None of those are trusted (they come from
# CLI args), so each is validated against a strict whitelist BEFORE it can reach
# the spawned process. A bad value fails closed with ValueError rather than
# smuggling shell metacharacters, NUL bytes, extra argv flags, or out-of-range
# values into the daemon.
# --------------------------------------------------------------------------- #

_LOG_LEVEL_ALLOWLIST = frozenset({"debug", "info", "warning", "error"})

# A hostname label per RFC 952/1123: alphanumerics and hyphens, not starting or
# ending with a hyphen, 1-63 chars; labels joined by dots. We also accept bare
# IPv4 / IPv6 literals (validated via ipaddress below).
_HOSTNAME_LABEL_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")


def _validate_daemon_host(host: str) -> str:
    """Return ``host`` if it is a safe hostname / IP literal, else raise.

    Rejects anything that is not a plain hostname or IP: shell metacharacters,
    whitespace, embedded argv flags, NUL bytes, etc. cannot pass.
    """
    import ipaddress

    if not isinstance(host, str) or not host:
        raise ValueError("daemon host must be a non-empty string")
    if len(host) > 255:
        raise ValueError("daemon host is too long")
    # IPv4 / IPv6 literal?
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    # Otherwise must be a dotted hostname of valid labels. This forbids spaces,
    # ';', '&', '|', '$', '`', '(', ')', newlines, leading '-' (argv flag), etc.
    labels = host.split(".")
    if all(_HOSTNAME_LABEL_RE.match(label) for label in labels):
        return host
    raise ValueError(f"invalid daemon host: {host!r}")


def _validate_daemon_port(port: int) -> int:
    """Return ``port`` as an int in the TCP range 1-65535, else raise.

    A bool, a non-numeric string, or an out-of-range value fails closed.
    """
    # bool is an int subclass; reject it explicitly so True/False can't slip in.
    if isinstance(port, bool):
        raise ValueError(f"invalid daemon port: {port!r}")
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        raise ValueError(f"invalid daemon port: {port!r}") from None
    # int("80a0") already raises; but int(8000.9) would truncate, so require the
    # original to be an int when it isn't a clean decimal string.
    if isinstance(port, str) and not port.isdigit():
        raise ValueError(f"invalid daemon port: {port!r}")
    if not (1 <= port_int <= 65535):
        raise ValueError(f"daemon port out of range (1-65535): {port_int}")
    return port_int


def _validate_daemon_log_level(log_level: str) -> str:
    """Return the normalized (lowercase) log level if allowlisted, else raise."""
    if not isinstance(log_level, str):
        raise ValueError(f"invalid daemon log-level: {log_level!r}")
    normalized = log_level.lower()
    if normalized not in _LOG_LEVEL_ALLOWLIST:
        raise ValueError(
            f"invalid daemon log-level: {log_level!r} (allowed: {', '.join(sorted(_LOG_LEVEL_ALLOWLIST))})"
        )
    return normalized


def _validate_daemon_path(value: str, *, name: str) -> str:
    """Return ``value`` if it is a safe path arg, else raise.

    The path is passed to the child via an environment variable, so it must not
    contain NUL bytes, newlines/carriage returns (which could forge additional
    env entries), or shell-command-substitution metacharacters. The path is not
    required to exist, but it must be a single confined token.
    """
    if not isinstance(value, str):
        raise ValueError(f"invalid {name} path: {value!r}")
    if "\x00" in value:
        raise ValueError(f"{name} path contains a NUL byte")
    if any(ch in value for ch in ("\n", "\r")):
        raise ValueError(f"{name} path contains a newline")
    if any(ch in value for ch in (";", "`", "$", "|", "&")):
        raise ValueError(f"{name} path contains a forbidden metacharacter: {value!r}")
    return value


def _build_daemon_env(
    config_dir: str | None = None,
    templates_dir: str | None = None,
    playbooks_dir: str | None = None,
    tick_interval: float = 1.0,
    log_level: str = "info",
    psk: str = "",
) -> dict[str, str]:
    env: dict[str, str] = {}
    if config_dir:
        env["GLUDD_CONFIG_DIR"] = _validate_daemon_path(config_dir, name="config-dir")
    if templates_dir:
        env["GLUDD_TEMPLATES_DIR"] = _validate_daemon_path(templates_dir, name="templates-dir")
    if playbooks_dir:
        env["GLUDD_PLAYBOOKS_DIR"] = _validate_daemon_path(playbooks_dir, name="playbooks-dir")
    if tick_interval != 1.0:
        env["GLUDD_TICK_INTERVAL"] = str(tick_interval)
    normalized_level = _validate_daemon_log_level(log_level)
    if normalized_level != "info":
        env["GLUDD_LOG_LEVEL"] = normalized_level
    env["GLUDD_PSK"] = psk
    return env


def _clamp_workers_for_sqlite(
    workers: int | None,
    *,
    database_url: str | None = None,
) -> int:
    """Clamp SQLite to one worker and permit bounded PostgreSQL concurrency.

    Each gunicorn worker spawns its own event loop + in-memory stores; with a
    single SQLite file there is no cross-process claim coordination, so N>1 is
    dishonest (duplicate dispatch, racing writers). PostgreSQL claims use row
    locking plus guarded updates, so explicit worker counts are preserved and
    the default is bounded to four workers to keep connection usage predictable.
    """
    resolved_url = database_url or os.environ.get("DATABASE_URL") or get_default_db_url()
    if not is_sqlite_url(resolved_url):
        requested = workers if workers is not None else min(4, os.cpu_count() or 1)
        return max(1, requested)
    if workers is None:
        return 1
    if workers > 1:
        logging.getLogger(__name__).warning(
            "Requested %d workers but general_ludd is SQLite-only "
            "(no cross-process claim coordination); clamping to 1 worker.",
            workers,
        )
        return 1
    return max(1, workers)


def _build_daemon_start_cmd(
    host: str = "127.0.0.1",
    port: int = 8000,
    workers: int | None = None,
) -> list[str]:
    # Harden every CLI-derived token before it can reach the spawned process.
    # host/port feed the "--bind HOST:PORT" argv token; validate them so a
    # malicious --host/--port cannot inject shell metacharacters or extra argv
    # flags (Popen gets a list, but a value like "1.2.3.4 --bind 0.0.0.0:80"
    # would still split into rogue tokens via the bind string otherwise).
    safe_host = _validate_daemon_host(host)
    safe_port = _validate_daemon_port(port)
    workers = _clamp_workers_for_sqlite(workers)
    argv: list[str] = [
        "gunicorn",
        "general_ludd.daemon:create_daemon_app()",
        "--worker-class",
        "uvicorn_worker.UvicornWorker",
        "--workers",
        str(workers),
        "--bind",
        f"{safe_host}:{safe_port}",
    ]
    return argv


def _cmd_tui(args: argparse.Namespace) -> None:
    from types import SimpleNamespace

    helpers = SimpleNamespace(
        _is_daemon_pid_alive=_is_daemon_pid_alive,
        _DAEMON_PID_FILE=_DAEMON_PID_FILE,
        _get_daemon_pid_dir=_get_daemon_pid_dir,
        _read_daemon_pid_file=_read_daemon_pid_file,
        _write_daemon_pid_file=_write_daemon_pid_file,
        _stop_daemon_via_pid_file=_stop_daemon_via_pid_file,
        _build_controls_table=_build_controls_table,
        _build_daemon_table=_build_daemon_table,
        _build_info_table=_build_info_table,
        _build_binary_table=_build_binary_table,
        _build_config_table=_build_config_table,
        _build_todos_table=_build_todos_table,
        _build_hooks_table=_build_hooks_table,
        _build_workers_table=_build_workers_table,
        _build_metrics_table=_build_metrics_table,
        _build_agents_table=_build_agents_table,
        _build_model_table=_build_model_table,
        _build_config_editor_table=_build_config_editor_table,
        _build_worktrees_table=_build_worktrees_table,
        _build_projects_table=_build_projects_table,
        _build_integrity_table=_build_integrity_table,
        _build_ansible_table=_build_ansible_table,
        _build_mcp_table=_build_mcp_table,
        _build_skills_table=_build_skills_table,
        _build_compute_table=_build_compute_table,
        _build_scores_table=_build_scores_table,
        _build_leaderboard_table=_build_leaderboard_table,
        _build_templates_table=_build_templates_table,
        _build_playbooks_table=_build_playbooks_table,
        _build_quantization_table=_build_quantization_table,
        _build_filestore_table=_build_filestore_table,
        _build_deployments_table=_build_deployments_table,
        _build_slurm_table=_build_slurm_table,
        _build_health_table=_build_health_table,
        _build_selftest_table=_build_selftest_table,
        _build_version_table=_build_version_table,
        _build_loglevel_table=_build_loglevel_table,
        _build_discovered_table=_build_discovered_table,
        _build_code_table=_build_code_table,
        _wrap_table=_wrap_table,
        _compute_panel_widths=_compute_panel_widths,
        _compute_footer_rows=_compute_footer_rows,
        _gather_offline_status=_gather_offline_status,
        _load_config_editor=_load_config_editor,
        _build_daemon_start_cmd=_build_daemon_start_cmd,
        _build_model_status_msg=_build_model_status_msg,
        _handle_connection_error=_handle_connection_error,
    )
    run_tui(args, helpers)


def _cmd_hooks_list(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/admin/hooks", timeout=10.0)
    if data is None:
        return
    hooks = data.get("hooks", [])
    if hooks:
        for h in hooks:
            print(f"  {h.get('hook_id', '?'):<20} {h.get('event_name', '?'):<20} {h.get('url', '?')}")
    else:
        print("No hooks registered.")


def _cmd_hooks_register(args: argparse.Namespace) -> None:
    data = _http_call(
        "POST",
        f"{args.daemon_url}/admin/hooks",
        json={"event_name": args.event, "url": args.handler},
        timeout=10.0,
        ok_codes=(200, 201),
    )
    if data is None:
        return
    print(f"Hook registered: {data.get('hook_id', '?')}")


def _cmd_hooks_delete(args: argparse.Namespace) -> None:
    _http_call("DELETE", f"{args.daemon_url}/admin/hooks/{args.hook_id}", timeout=10.0)
    print(f"Hook deleted: {args.hook_id}")


def _cmd_workers_list(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/admin/workers", timeout=10.0)
    if data is None:
        return
    workers = data.get("workers", [])
    if workers:
        for w in workers:
            print(f"  {w.get('worker_id', '?'):<20} {w.get('address', '?'):<30} {w.get('last_seen', '?')}")
    else:
        print("No workers registered.")


def _cmd_workers_ping(args: argparse.Namespace) -> None:
    data = _http_call("POST", f"{args.daemon_url}/admin/workers/ping", timeout=10.0)
    if data is None:
        return
    print(json.dumps(data, indent=2))


def _cmd_agents_list(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/admin/agents", timeout=10.0)
    if data is None:
        return
    agents = data.get("agents", [])
    if agents:
        for a in agents:
            print(f"  {a.get('agent_id', '?'):<20} {a.get('status', '?'):<12} {a.get('model', '?')}")
    else:
        print("No agents configured.")


def _cmd_metrics_cost(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/admin/metrics/cost", timeout=10.0)
    if data is None:
        return
    print(json.dumps(data, indent=2))


def _cmd_metrics_report(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/admin/metrics/report", timeout=10.0)
    if data is None:
        return
    print(json.dumps(data, indent=2))


def _cmd_reload(args: argparse.Namespace) -> None:
    data = _http_call("POST", f"{args.daemon_url}/admin/reload", json={"scope": args.scope}, timeout=30.0)
    if data is None:
        return
    print(f"Reloaded: {data.get('scope', args.scope)}")


def _cmd_templates_list(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/admin/templates", timeout=10.0)
    if data is None:
        return
    templates = data.get("templates", [])
    if templates:
        for t in templates:
            print(f"  {t}")
    else:
        print("No templates found.")


def _cmd_templates_refresh(args: argparse.Namespace) -> None:
    data = _http_call("POST", f"{args.daemon_url}/admin/templates/refresh", timeout=30.0)
    if data is None:
        return
    tmpls = data.get("templates", [])
    print(f"Refreshed: {len(tmpls)} templates")


def _cmd_playbooks_list(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/admin/playbooks", timeout=10.0)
    if data is None:
        return
    playbooks = data.get("playbooks", [])
    if playbooks:
        for p in playbooks:
            print(f"  {p}")
    else:
        print("No playbooks found.")


def _cmd_playbooks_refresh(args: argparse.Namespace) -> None:
    data = _http_call("POST", f"{args.daemon_url}/admin/playbooks/refresh", timeout=30.0)
    if data is None:
        return
    pbs = data.get("playbooks", [])
    print(f"Refreshed: {len(pbs)} playbooks")


def _cmd_code_graph(args: argparse.Namespace) -> None:
    # M11 (W3.13): hit /admin/code/graph (not /admin/code-graph), and
    # read file contents when --source is a file path.
    try:
        params: dict[str, str] = {}
        source_arg = getattr(args, "source", None) or ""
        if source_arg:
            import os as _os

            if _os.path.isfile(source_arg):
                try:
                    with open(source_arg) as _f:
                        source_arg = _f.read()
                except OSError as e:
                    print(f"Cannot read source file: {e}", file=sys.stderr)
                    sys.exit(1)
            params["source"] = source_arg
        if getattr(args, "language", None):
            params["language"] = str(args.language)
        resp = httpx.get(f"{args.daemon_url}/admin/code/graph", params=params, timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            nodes = data.get("nodes", [])
            print(json.dumps({"nodes": nodes}, indent=2))
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_code_search(args: argparse.Namespace) -> None:
    # M11 (W3.13): hit /admin/code/search (not /admin/code-search), and
    # read file contents when --source is a file path.
    try:
        params: dict[str, str] = {}
        source_arg = getattr(args, "source", None) or ""
        if source_arg:
            import os as _os

            if _os.path.isfile(source_arg):
                try:
                    with open(source_arg) as _f:
                        source_arg = _f.read()
                except OSError as e:
                    print(f"Cannot read source file: {e}", file=sys.stderr)
                    sys.exit(1)
            params["source"] = source_arg
        if getattr(args, "query", None):
            params["query"] = str(args.query)
        if getattr(args, "language", None):
            params["language"] = str(args.language)
        resp = httpx.get(f"{args.daemon_url}/admin/code/search", params=params, timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            if results:
                for r in results:
                    print(f"  {r.get('file', '?')}:{r.get('line', 0)} {r.get('text', '')}")
            else:
                print("No results found.")
        else:
            print(f"Error: {resp.status_code}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


def _cmd_quantization_list(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/admin/quantization", timeout=10.0)
    if data is None:
        return
    models = data.get("profiles", data.get("models", []))
    if models:
        for m in models:
            prec = m.get("precision", "unknown")
            conf = m.get("confidence", 0)
            print(f"  {m.get('model_id', '?')}  prec={prec}  conf={conf:.2f}")
    else:
        print("No quantization data available. Use 'detect' to scan models.")


def _cmd_quantization_detect(args: argparse.Namespace) -> None:
    data = _http_call(
        "POST",
        f"{args.daemon_url}/admin/quantization/detect",
        json={"model_id": args.model_id},
        timeout=30.0,
    )
    if data is None:
        return
    mid = data.get("model_id", "?")
    prec = data.get("precision", "unknown")
    conf = data.get("confidence", 0)
    print(f"  {mid}  prec={prec}  conf={conf:.2f}")


def _cmd_quantization_drift_check(args: argparse.Namespace) -> None:
    data = _http_call("POST", f"{args.daemon_url}/admin/quantization/drift-check", timeout=30.0)
    if data is None:
        return
    if data.get("drift_detected"):
        print(f"Drift detected in {len(data.get('drifted_models', []))} model(s)")
        for m in data.get("drifted_models", []):
            print(f"  {m.get('model_id')}: {m.get('old_precision')} -> {m.get('new_precision')}")
    else:
        print("No drift detected.")


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

    paths = [
        info.get("config_dir", ""),
        info.get("filestore_root", ""),
        os.path.expanduser("~/.config/gludd"),
        os.path.expanduser("~/.local/share/general-ludd"),
    ]
    paths = [p for p in paths if p and os.path.isdir(p)]
    # Shared canonical exclude set (see cli_core_changes._excluded); imported
    # from one place so the two scan sites cannot drift apart.
    exclude_patterns = list(FIM_EXCLUDE_PATTERNS)

    # Safety: if self-improve is enabled but a config OVERLAY (project .gludd/
    # or user ~/.config/gludd) is outside FIM's scope, agent-authored changes
    # land there untracked — warn the operator. Reads self-improve from the
    # user config file in the resolved config dir (interval>0 == enabled;
    # absent config defaults to ON, matching the daemon).
    from pathlib import Path as _Path

    from general_ludd.config.user_config import UserConfig
    from general_ludd.integrity.overlay_guard import (
        resolve_self_improve_enabled,
        warn_if_overlay_unmonitored,
    )

    si_cfg: dict[str, Any] = {}
    cdir = info.get("config_dir", "")
    if cdir:
        try:
            uc = UserConfig.from_yaml(_Path(cdir) / "general-ludd.yml")
            si_cfg = uc.self_improve or {}
        except Exception:
            si_cfg = {}
    warn_if_overlay_unmonitored(paths, exclude_patterns, resolve_self_improve_enabled(si_cfg))

    scanner = FileIntegrityScanner()
    return scanner.scan(paths, exclude_patterns=exclude_patterns)


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
    data = _http_call("GET", f"{args.daemon_url}/admin/integrity/log", timeout=10.0)
    if data is None:
        return
    for entry in data.get("entries", []):
        print(f"[{entry.get('timestamp', '?')}] {entry.get('action')}: {entry.get('path')}")
        print(f"  Reason: {entry.get('reason')}  Signer: {entry.get('signer')}")


def _load_config_editor() -> dict[str, Any]:

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


def _cmd_slurm_status(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/admin/slurm/status", timeout=10.0)
    if data is None:
        return
    available = data.get("available", False)
    print(f"Slurm available: {available}")


def _cmd_slurm_submit(args: argparse.Namespace) -> None:
    payload: dict[str, Any] = {"command": args.command}
    if args.job_name:
        payload["job_name"] = args.job_name
    if args.partition:
        payload["partition"] = args.partition
    if args.cpus_per_task:
        payload["cpus_per_task"] = args.cpus_per_task
    if args.gpus:
        payload["gpus"] = args.gpus
    if args.memory:
        payload["memory"] = args.memory
    if args.time_limit:
        payload["time_limit"] = args.time_limit
    data = _http_call("POST", f"{args.daemon_url}/admin/slurm/submit", json=payload, timeout=30.0)
    if data is None:
        return
    print(f"Submitted job: {data['job_id']}")


def _cmd_slurm_job(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/admin/slurm/jobs/{args.job_id}", timeout=10.0)
    if data is None:
        return
    print(f"Job ID:    {data['job_id']}")
    print(f"State:     {data['state']}")
    exit_code = data.get("exit_code")
    if exit_code is not None:
        print(f"Exit code: {exit_code}")


def _cmd_slurm_cancel(args: argparse.Namespace) -> None:
    _http_call("DELETE", f"{args.daemon_url}/admin/slurm/jobs/{args.job_id}", timeout=10.0)
    print(f"Cancelled job: {args.job_id}")


def _cmd_slurm_list(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/admin/slurm/jobs", timeout=10.0)
    if data is None:
        return
    jobs = data.get("jobs", [])
    if jobs:
        for j in jobs:
            print(f"  {j.get('job_id', '?'):<12} {j.get('state', '?'):<15} {j.get('exit_code', '')}")
    else:
        print("No Slurm jobs found.")


def _cmd_connectors_list(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/api/observe/sources", timeout=10.0)
    if data is None:
        return
    sources = data.get("sources", [])
    if not sources:
        print("No observability sources registered.")
        print("Configure connectors in config/connectors.yml and restart the daemon.")
        return
    print(f"Observability sources: {len(sources)}")
    for s in sources:
        name = s.get("name", "?")
        kind = s.get("kind", "?")
        family = s.get("family", "?")
        print(f"  {name:<24} {kind:<12} {family}")


def _cmd_connectors_health(args: argparse.Namespace) -> None:
    data = _http_call("GET", f"{args.daemon_url}/api/observe/health", timeout=10.0)
    if data is None:
        return
    health = data.get("health", {})
    if not health:
        print("No observability sources registered.")
        return
    print(f"Connector health: {len(health)} source(s)")
    for name, status in health.items():
        ok = status.get("ok", False) if isinstance(status, dict) else False
        icon = "OK" if ok else "FAIL"
        detail = ""
        if isinstance(status, dict):
            if not ok and status.get("error"):
                detail = f"  {status['error']}"
            elif ok and status.get("latency_ms") is not None:
                detail = f"  {status['latency_ms']}ms"
        print(f"  [{icon}] {name}{detail}")


def _cmd_connectors_query(args: argparse.Namespace) -> None:
    try:
        spec = json.loads(args.spec) if args.spec else {}
    except (json.JSONDecodeError, TypeError) as exc:
        print(f"Error: --spec is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)
    data = _http_call(
        "POST",
        f"{args.daemon_url}/api/observe/query",
        json={"source": args.source, "spec": spec},
        timeout=10.0,
    )
    if data is None:
        return
    records = data.get("records", [])
    count = data.get("count", len(records))
    source = data.get("source", args.source)
    if not records:
        print(f"No records from source '{source}'.")
        return
    print(f"Source '{source}': {count} record(s)")
    for r in records:
        print(f"  {r}")


def _cmd_testbg_launch(args: argparse.Namespace) -> None:
    from general_ludd.runner.background_test_runner import BackgroundTestRunner

    runner = BackgroundTestRunner()
    result = runner.launch(args.testfile, wait=args.wait)
    print(json.dumps(result, indent=2))
    if result.get("phase") == "timeout":
        sys.exit(1)


def _cmd_testbg_status(args: argparse.Namespace) -> None:
    from general_ludd.runner.background_test_runner import BackgroundTestRunner

    runner = BackgroundTestRunner()
    result = runner.status(args.testfile)
    print(json.dumps(result, indent=2))


def _cmd_testbg_poll_all(args: argparse.Namespace) -> None:
    from general_ludd.runner.background_test_runner import BackgroundTestRunner

    runner = BackgroundTestRunner()
    results = runner.poll_all()
    print(json.dumps(results, indent=2))


def _cmd_testbg_kill(args: argparse.Namespace) -> None:
    from general_ludd.runner.background_test_runner import BackgroundTestRunner

    runner = BackgroundTestRunner()
    result = runner.kill(args.testfile, force=args.force)
    print(json.dumps(result, indent=2))


def _cmd_testbg_results(args: argparse.Namespace) -> None:
    from general_ludd.runner.background_test_runner import BackgroundTestRunner

    runner = BackgroundTestRunner()
    result = runner.results(args.testfile)
    print(json.dumps(result, indent=2))


def _cmd_make(args: argparse.Namespace) -> None:
    from general_ludd.commands.make import MakeRunner

    env_extra: dict[str, str] | None = None
    if args.env:
        env_extra = {}
        for pair in args.env:
            if "=" in pair:
                k, v = pair.split("=", 1)
                env_extra[k] = v

    runner = MakeRunner(cwd=args.cwd)
    if args.stream:
        phases_seen: list[str] = []

        def _cb(phase: str) -> None:
            phases_seen.append(phase)
            print(f"[PHASE] {phase}")

        result = runner.run(
            args.target,
            timeout_s=args.timeout,
            env_extra=env_extra,
            stream=True,
            stream_callback=_cb,
        )
    else:
        result = runner.run(
            args.target,
            timeout_s=args.timeout,
            env_extra=env_extra,
        )

    print(
        json.dumps(
            {
                "target": result.target,
                "exit_code": result.exit_code,
                "success": result.success,
                "duration_s": result.duration_s,
                "timed_out": result.timed_out,
                "phases": result.phases,
            },
            indent=2,
        )
    )
    sys.exit(0 if result.success else 1)


if __name__ == "__main__":
    # Keep the module invocation contract aligned with the standalone binary
    # tests: version is exposed by the ``gludd`` console entry point, while
    # ``python -m general_ludd.cli --version`` remains an invalid top-level
    # invocation.  In-process callers still exercise ``main()`` directly.
    if "--version" in sys.argv[1:]:
        print("error: unrecognized arguments: --version", file=sys.stderr)
        sys.exit(2)
    main()
