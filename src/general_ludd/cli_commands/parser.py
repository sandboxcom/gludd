"""Argument parser construction for the General Ludd CLI."""

from __future__ import annotations

import argparse
import os
import threading
from collections.abc import Mapping
from typing import Any

from general_ludd.models.performance_router import DEFAULT_STRATEGIES

_REGISTRY_LOCK = threading.RLock()
_ACTIVE_REGISTRY: Mapping[str, Any] = {}


def _handler(name: str) -> Any:
    return _ACTIVE_REGISTRY[name]


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
    parser.set_defaults(func=_handler("_cmd_selftest"))


def _build_parser_impl() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    """Build the CLI parser and the parser map used for help dispatch."""
    parser = argparse.ArgumentParser(
        prog="gludd",
        description="General Ludd Agent — the black swan agentic coding system",
    )
    from general_ludd import __version__

    parser.add_argument(
        "--version",
        action="version",
        version=f"gludd {__version__}",
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
    daemon_parser.add_argument(
        "--pid-file",
        default=None,
        help="Write the daemon PID to this file; unlinked on graceful shutdown",
    )
    daemon_parser.set_defaults(func=_handler("_cmd_daemon"))

    add_parser = sub.add_parser("add", help="Add a todo to the queue")
    add_parser.add_argument("title", metavar="TITLE", help="Todo title")
    add_parser.add_argument("--queue", default="core")
    add_parser.add_argument("--priority", default="medium")
    add_parser.add_argument("--work-type", default="code")
    add_parser.add_argument("--description", default="")
    add_parser.add_argument("--project", default=None, help="Project ID to add the todo to")
    add_parser.add_argument("--daemon-url", default="http://localhost:8000")
    add_parser.set_defaults(func=_handler("_cmd_add"))

    status_parser = sub.add_parser("status", help="Show todo or system status")
    status_parser.add_argument("todo_id", nargs="?", default=None)
    status_parser.add_argument("--project", default=None, help="Project ID to filter by")
    status_parser.add_argument("--daemon-url", default="http://localhost:8000")
    status_parser.set_defaults(func=_handler("_cmd_status"))

    list_parser = sub.add_parser("list", help="List todos")
    list_parser.add_argument("--queue", default=None)
    list_parser.add_argument("--status", default=None)
    list_parser.add_argument("--project", default=None, help="Project ID to filter by")
    list_parser.add_argument("--daemon-url", default="http://localhost:8000")
    list_parser.set_defaults(func=_handler("_cmd_list"))

    log_parser = sub.add_parser("log-level", help="Change daemon log level at runtime")
    log_parser.add_argument("level", choices=["debug", "info", "warning", "error"])
    log_parser.add_argument("--daemon-url", default="http://localhost:8000")
    log_parser.set_defaults(func=_handler("_cmd_log_level"))

    dep_parser = sub.add_parser("deployments", help="List active deployments")
    dep_parser.add_argument("--daemon-url", default="http://localhost:8000")
    dep_parser.set_defaults(func=_handler("_cmd_deployments"))

    ver_parser = sub.add_parser("version", help="Show version")
    ver_parser.set_defaults(func=_handler("_cmd_version"))

    health_parser = sub.add_parser("health", help="Check daemon health")
    health_parser.add_argument("--daemon-url", default="http://localhost:8000")
    health_parser.set_defaults(func=_handler("_cmd_health"))

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
    models_search.set_defaults(func=_handler("_cmd_models_search"))

    models_searx_search = models_sub.add_parser("searx-search", help="Search models via SearXNG")
    models_searx_search.add_argument("query", nargs="?", default="", help="Search query")
    models_searx_search.add_argument(
        "--source",
        default="huggingface",
        choices=["huggingface", "github", "web"],
        help="Search source (default: huggingface)",
    )
    models_searx_search.add_argument("--searx-url", default=None, help="SearXNG instance URL")
    models_searx_search.set_defaults(func=_handler("_cmd_models_searx_search"))

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
    models_deploy.set_defaults(func=_handler("_cmd_models_deploy"))

    models_downloaded = models_sub.add_parser("downloaded", help="List downloaded models")
    models_downloaded.add_argument("--daemon-url", default="http://localhost:8000")
    models_downloaded.set_defaults(func=_handler("_cmd_models_downloaded"))

    models_discover = models_sub.add_parser("discover", help="Discover free models from OpenRouter")
    models_discover.add_argument("--provider", default="openrouter", help="Provider to discover from")
    models_discover.add_argument("--daemon-url", default="http://localhost:8000")
    models_discover.set_defaults(func=_handler("_cmd_models_discover"))

    models_list_discovered = models_sub.add_parser("discovered", help="List auto-discovered model profiles")
    models_list_discovered.add_argument("--daemon-url", default="http://localhost:8000")
    models_list_discovered.set_defaults(func=_handler("_cmd_models_discovered"))

    models_list = models_sub.add_parser("list", help="List registered models")
    models_list.add_argument("--daemon-url", default="http://localhost:8000")
    models_list.set_defaults(func=_handler("_cmd_models_list"))

    models_add = models_sub.add_parser("add", help="Add a model profile")
    models_add.add_argument("--model-id", required=True, help="Model ID")
    models_add.add_argument("--provider", default="openai", help="Provider name")
    models_add.add_argument("--model", default="", help="Model name")
    models_add.add_argument("--api-key-env", default=None, help="API key environment variable")
    models_add.add_argument("--daemon-url", default="http://localhost:8000")
    models_add.set_defaults(func=_handler("_cmd_models_add"))

    models_remove = models_sub.add_parser("remove", help="Remove a model profile")
    models_remove.add_argument("model_id", help="Model ID to remove")
    models_remove.add_argument("--daemon-url", default="http://localhost:8000")
    models_remove.set_defaults(func=_handler("_cmd_models_remove"))

    models_perf = models_sub.add_parser("performance", help="Show model performance data")
    models_perf.add_argument("--service", default=None, help="Filter by service")
    models_perf.add_argument("--task-type", default=None, help="Filter by task type")
    models_perf.add_argument("--daemon-url", default="http://localhost:8000")
    models_perf.set_defaults(func=_handler("_cmd_model_performance"))

    models_ranking = models_sub.add_parser("ranking", help="Show model rankings for a task type")
    models_ranking.add_argument("--task-type", required=True, help="Task type to rank")
    models_ranking.add_argument(
        "--strategy", default="balanced", choices=list(DEFAULT_STRATEGIES.keys()), help="Ranking strategy"
    )
    models_ranking.add_argument("--daemon-url", default="http://localhost:8000")
    models_ranking.set_defaults(func=_handler("_cmd_model_ranking"))

    models_router_status = models_sub.add_parser("router-status", help="Show current router configuration")
    models_router_status.add_argument("--daemon-url", default="http://localhost:8000")
    models_router_status.set_defaults(func=_handler("_cmd_model_router_status"))

    models_router_set = models_sub.add_parser("router-set", help="Set routing strategy for a task type")
    models_router_set.add_argument("--task-type", required=True, help="Task type")
    models_router_set.add_argument(
        "--strategy", required=True, choices=list(DEFAULT_STRATEGIES.keys()), help="Routing strategy"
    )
    models_router_set.add_argument("--daemon-url", default="http://localhost:8000")
    models_router_set.set_defaults(func=_handler("_cmd_model_router_set"))

    local_serve_parser = sub.add_parser("local-serve", help="Start a local inference server")
    local_serve_parser.add_argument("--engine", default="vllm", choices=["vllm", "llamacpp"])
    local_serve_parser.add_argument("--model", required=True, help="Model name or path")
    local_serve_parser.add_argument("--host", default="localhost")
    local_serve_parser.add_argument("--port", type=int, default=8001)
    local_serve_parser.add_argument("--gpu-layers", type=int, default=-1)
    local_serve_parser.add_argument("--context-size", type=int, default=4096)
    local_serve_parser.add_argument("--daemon-url", default="http://localhost:8000")
    local_serve_parser.set_defaults(func=_handler("_cmd_local_serve"))

    worktree_parser = sub.add_parser("worktree", help="Worktree monitor commands")
    worktree_parser.set_defaults(func=None)
    wt_sub = worktree_parser.add_subparsers(dest="worktree_command")

    wt_scan = wt_sub.add_parser("scan", help="Scan for abandoned worktrees with AGENTS.md")
    wt_scan.add_argument("--path", default=None, help="Comma-separated paths to scan")
    wt_scan.add_argument("--daemon-url", default="http://localhost:8000")
    wt_scan.set_defaults(func=_handler("_cmd_worktree_scan"))

    wt_status = wt_sub.add_parser("status", help="Show tracked worktrees")
    wt_status.add_argument("--daemon-url", default="http://localhost:8000")
    wt_status.set_defaults(func=_handler("_cmd_worktree_status"))

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
    proj_add.set_defaults(func=_handler("_cmd_project_add"))

    proj_list = proj_sub.add_parser("list", help="List registered projects")
    proj_list.add_argument("--daemon-url", default="http://localhost:8000")
    proj_list.set_defaults(func=_handler("_cmd_project_list"))

    proj_remove = proj_sub.add_parser("remove", help="Remove a project")
    proj_remove.add_argument("project_id", help="Project ID to remove")
    proj_remove.add_argument("--daemon-url", default="http://localhost:8000")
    proj_remove.set_defaults(func=_handler("_cmd_project_remove"))

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
    tf_get.set_defaults(func=_handler("_cmd_config_terraform_get"))

    tf_set = tf_sub.add_parser("set", help="Set a terraform variable default")
    tf_set.add_argument("field", help="Field name (e.g. region, instance_type, gpu_count)")
    tf_set.add_argument("value", help="New value")
    tf_set.set_defaults(func=_handler("_cmd_config_terraform_set"))

    mcp_parser = sub.add_parser("mcp", help="MCP server catalog commands")
    mcp_parser.set_defaults(func=None)
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_command")

    mcp_search = mcp_sub.add_parser("search", help="Search MCP catalog")
    mcp_search.add_argument("query", nargs="?", default="", help="Search query")
    mcp_search.add_argument("--daemon-url", default="http://localhost:8000")
    mcp_search.set_defaults(func=_handler("_cmd_mcp_search"))

    mcp_list = mcp_sub.add_parser("list", help="List known MCP servers")
    mcp_list.add_argument("--daemon-url", default="http://localhost:8000")
    mcp_list.set_defaults(func=_handler("_cmd_mcp_list"))

    mcp_info = mcp_sub.add_parser("info", help="Show MCP server details")
    mcp_info.add_argument("name", help="Server name")
    mcp_info.add_argument("--daemon-url", default="http://localhost:8000")
    mcp_info.set_defaults(func=_handler("_cmd_mcp_info"))

    skills_parser = sub.add_parser("skills", help="Skills catalog commands")
    skills_parser.set_defaults(func=None)
    skills_sub = skills_parser.add_subparsers(dest="skills_command")

    skills_search = skills_sub.add_parser("search", help="Search skills catalog")
    skills_search.add_argument("query", nargs="?", default="", help="Search query")
    skills_search.add_argument("--daemon-url", default="http://localhost:8000")
    skills_search.set_defaults(func=_handler("_cmd_skills_search"))

    skills_list = skills_sub.add_parser("list", help="List all skills")
    skills_list.add_argument("--daemon-url", default="http://localhost:8000")
    skills_list.set_defaults(func=_handler("_cmd_skills_list"))

    skills_install = skills_sub.add_parser("install", help="Install a skill")
    skills_install.add_argument("name", help="Skill name")
    skills_install.add_argument("--daemon-url", default="http://localhost:8000")
    skills_install.set_defaults(func=_handler("_cmd_skills_install"))

    compute_parser = sub.add_parser("compute", help="Compute endpoint commands")
    compute_parser.set_defaults(func=None)
    compute_sub = compute_parser.add_subparsers(dest="compute_command")

    compute_endpoints = compute_sub.add_parser("endpoints", help="List compute endpoints")
    compute_endpoints.add_argument("--daemon-url", default="http://localhost:8000")
    compute_endpoints.set_defaults(func=_handler("_cmd_compute_endpoints"))

    compute_register = compute_sub.add_parser("register", help="Register a compute endpoint")
    compute_register.add_argument("--id", required=True, help="Endpoint ID")
    compute_register.add_argument("--url", required=True, help="Endpoint URL")
    compute_register.add_argument("--model", required=True, help="Model name")
    compute_register.add_argument("--max-concurrent", type=int, default=1, help="Max concurrent requests")
    compute_register.add_argument("--daemon-url", default="http://localhost:8000")
    compute_register.set_defaults(func=_handler("_cmd_compute_register"))

    compute_unregister = compute_sub.add_parser("unregister", help="Remove a compute endpoint")
    compute_unregister.add_argument("endpoint_id", help="Endpoint ID to remove")
    compute_unregister.add_argument("--daemon-url", default="http://localhost:8000")
    compute_unregister.set_defaults(func=_handler("_cmd_compute_unregister"))

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
    compute_azure_preflight.set_defaults(func=_handler("_cmd_compute_azure_preflight"))

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
    compute_launch.set_defaults(func=_handler("_cmd_compute_launch"))

    compute_destroy = compute_sub.add_parser("destroy", help="Destroy a GPU compute instance")
    compute_destroy.add_argument("instance_id", help="Instance ID to destroy")
    compute_destroy.add_argument("--daemon-url", default="http://localhost:8000")
    compute_destroy.set_defaults(func=_handler("_cmd_compute_destroy"))

    scores_parser = sub.add_parser("scores", help="View benchmark scores")
    scores_parser.add_argument("--task-type", default=None, help="Filter by task type")
    scores_parser.add_argument("--daemon-url", default="http://localhost:8000")
    scores_parser.set_defaults(func=_handler("_cmd_scores"))

    leaderboard_parser = sub.add_parser("leaderboard", help="View prompt+model leaderboard")
    leaderboard_parser.add_argument("--task-type", default=None, help="Filter by task type")
    leaderboard_parser.add_argument("--daemon-url", default="http://localhost:8000")
    leaderboard_parser.set_defaults(func=_handler("_cmd_leaderboard"))

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
    chat_parser.set_defaults(func=_handler("_cmd_chat"))

    help_p = sub.add_parser("help", help="Show full manual")
    help_p.set_defaults(func=_handler("_cmd_help"))

    filestore_parser = sub.add_parser("filestore", help="Filestore management commands")
    filestore_parser.set_defaults(func=None)
    fs_sub = filestore_parser.add_subparsers(dest="filestore_command")

    fs_list = fs_sub.add_parser("list", help="List filestore contents")
    fs_list.add_argument("path", nargs="?", default="/", help="Path to list")
    fs_list.add_argument("--daemon-url", default="http://localhost:8000")
    fs_list.set_defaults(func=_handler("_cmd_filestore_list"))

    fs_read = fs_sub.add_parser("cat", help="Read a file from filestore")
    fs_read.add_argument("path", help="Path to read")
    fs_read.add_argument("--daemon-url", default="http://localhost:8000")
    fs_read.set_defaults(func=_handler("_cmd_filestore_cat"))

    fs_bootstrap = fs_sub.add_parser("bootstrap", help="Download binaries into filestore")
    fs_bootstrap.add_argument("--binary", default="openbao", help="Binary to download")
    fs_bootstrap.add_argument("--daemon-url", default="http://localhost:8000")
    fs_bootstrap.set_defaults(func=_handler("_cmd_filestore_bootstrap"))

    fs_bins = fs_sub.add_parser("binaries", help="List stored binaries")
    fs_bins.add_argument("--daemon-url", default="http://localhost:8000")
    fs_bins.set_defaults(func=_handler("_cmd_filestore_binaries"))

    preflight_p = sub.add_parser("preflight", help="Run the preflight quality gate")
    preflight_p.add_argument(
        "--strict-terraform-import",
        action="store_true",
        help="Elevate terraform-collection importer warnings to failures (release readiness)",
    )
    preflight_p.set_defaults(func=_handler("_cmd_preflight"))

    tui_parser = sub.add_parser("tui", help="Launch the interactive TUI dashboard")
    tui_parser.add_argument("--daemon-url", default="http://localhost:8000")
    tui_parser.set_defaults(func=_handler("_cmd_tui"))

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
    ansible_search.set_defaults(func=_handler("_cmd_ansible_search"))
    ansible_install = ansible_sub.add_parser("install", help="Install from Ansible Galaxy")
    ansible_install.add_argument("name", help="Role or collection name")
    ansible_install.add_argument("--type", default="role", choices=["role", "collection"])
    ansible_install.add_argument("--daemon-url", default="http://localhost:8000")
    ansible_install.set_defaults(func=_handler("_cmd_ansible_install"))
    ansible_builtins = ansible_sub.add_parser("builtins", help="List ansible.builtin modules")
    ansible_builtins.add_argument("--daemon-url", default="http://localhost:8000")
    ansible_builtins.set_defaults(func=_handler("_cmd_ansible_builtins"))

    int_scan = int_sub.add_parser("scan", help="Scan files for changes")
    int_scan.add_argument("--daemon-url", default="http://localhost:8000")
    int_scan.add_argument("--paths", nargs="*", default=None, help="Paths to scan")
    int_scan.set_defaults(func=_handler("_cmd_integrity_scan"))
    int_report = int_sub.add_parser("report", help="Show integrity change report")
    int_report.add_argument("--daemon-url", default="http://localhost:8000")
    int_report.set_defaults(func=_handler("_cmd_integrity_report"))
    int_approve = int_sub.add_parser("approve", help="Approve an integrity change")
    int_approve.add_argument("change_id", help="File path of the change to approve")
    int_approve.add_argument("--reason", required=True, help="Reason for approval")
    int_approve.add_argument("--signer", default="admin", help="Who is signing")
    int_approve.add_argument("--daemon-url", default="http://localhost:8000")
    int_approve.set_defaults(func=_handler("_cmd_integrity_approve"))
    int_reject = int_sub.add_parser("reject", help="Reject an integrity change")
    int_reject.add_argument("change_id", help="File path of the change to reject")
    int_reject.add_argument("--reason", default="Rejected", help="Reason for rejection")
    int_reject.add_argument("--daemon-url", default="http://localhost:8000")
    int_reject.set_defaults(func=_handler("_cmd_integrity_reject"))
    int_log = int_sub.add_parser("log", help="Show approval/rejection log")
    int_log.add_argument("--daemon-url", default="http://localhost:8000")
    int_log.set_defaults(func=_handler("_cmd_integrity_log"))

    hooks_parser = sub.add_parser("hooks", help="Hook management commands")
    hooks_parser.set_defaults(func=None)
    hooks_sub = hooks_parser.add_subparsers(dest="hooks_command")
    hooks_list = hooks_sub.add_parser("list", help="List registered hooks")
    hooks_list.add_argument("--daemon-url", default="http://localhost:8000")
    hooks_list.set_defaults(func=_handler("_cmd_hooks_list"))
    hooks_register = hooks_sub.add_parser("register", help="Register a hook")
    hooks_register.add_argument("--event", required=True, help="Event type")
    hooks_register.add_argument("--handler", required=True, help="Handler module path")
    hooks_register.add_argument("--daemon-url", default="http://localhost:8000")
    hooks_register.set_defaults(func=_handler("_cmd_hooks_register"))
    hooks_delete = hooks_sub.add_parser("delete", help="Delete a hook")
    hooks_delete.add_argument("hook_id", help="Hook ID to delete")
    hooks_delete.add_argument("--daemon-url", default="http://localhost:8000")
    hooks_delete.set_defaults(func=_handler("_cmd_hooks_delete"))

    workers_parser = sub.add_parser("workers", help="Worker management commands")
    workers_parser.set_defaults(func=None)
    workers_sub = workers_parser.add_subparsers(dest="workers_command")
    workers_list = workers_sub.add_parser("list", help="List workers")
    workers_list.add_argument("--daemon-url", default="http://localhost:8000")
    workers_list.set_defaults(func=_handler("_cmd_workers_list"))
    workers_ping = workers_sub.add_parser("ping", help="Ping workers")
    workers_ping.add_argument("--daemon-url", default="http://localhost:8000")
    workers_ping.set_defaults(func=_handler("_cmd_workers_ping"))

    agents_parser = sub.add_parser("agents", help="Agent management commands")
    agents_parser.set_defaults(func=None)
    agents_sub = agents_parser.add_subparsers(dest="agents_command")
    agents_list = agents_sub.add_parser("list", help="List agents")
    agents_list.add_argument("--daemon-url", default="http://localhost:8000")
    agents_list.set_defaults(func=_handler("_cmd_agents_list"))

    metrics_parser = sub.add_parser("metrics", help="Metrics commands")
    metrics_parser.set_defaults(func=None)
    metrics_sub = metrics_parser.add_subparsers(dest="metrics_command")
    metrics_cost = metrics_sub.add_parser("cost", help="Show cost metrics")
    metrics_cost.add_argument("--daemon-url", default="http://localhost:8000")
    metrics_cost.set_defaults(func=_handler("_cmd_metrics_cost"))
    metrics_report = metrics_sub.add_parser("report", help="Show full metrics report")
    metrics_report.add_argument("--daemon-url", default="http://localhost:8000")
    metrics_report.set_defaults(func=_handler("_cmd_metrics_report"))

    reload_parser = sub.add_parser("reload", help="Hot-reload daemon configuration")
    reload_parser.add_argument("--scope", default="all", help="Reload scope (all, config, templates, playbooks)")
    reload_parser.add_argument("--daemon-url", default="http://localhost:8000")
    reload_parser.set_defaults(func=_handler("_cmd_reload"))

    templates_parser = sub.add_parser("templates", help="Template management commands")
    templates_parser.set_defaults(func=None)
    templates_sub = templates_parser.add_subparsers(dest="templates_command")
    templates_list = templates_sub.add_parser("list", help="List templates")
    templates_list.add_argument("--daemon-url", default="http://localhost:8000")
    templates_list.set_defaults(func=_handler("_cmd_templates_list"))
    templates_refresh = templates_sub.add_parser("refresh", help="Refresh template cache")
    templates_refresh.add_argument("--daemon-url", default="http://localhost:8000")
    templates_refresh.set_defaults(func=_handler("_cmd_templates_refresh"))

    playbooks_parser = sub.add_parser("playbooks", help="Playbook management commands")
    playbooks_parser.set_defaults(func=None)
    playbooks_sub = playbooks_parser.add_subparsers(dest="playbooks_command")
    playbooks_list = playbooks_sub.add_parser("list", help="List playbooks")
    playbooks_list.add_argument("--daemon-url", default="http://localhost:8000")
    playbooks_list.set_defaults(func=_handler("_cmd_playbooks_list"))
    playbooks_refresh = playbooks_sub.add_parser("refresh", help="Refresh playbook cache")
    playbooks_refresh.add_argument("--daemon-url", default="http://localhost:8000")
    playbooks_refresh.set_defaults(func=_handler("_cmd_playbooks_refresh"))

    codeintel_parser = sub.add_parser("code", help="Code intelligence commands")
    codeintel_parser.set_defaults(func=None)
    codeintel_sub = codeintel_parser.add_subparsers(dest="code_command")
    codeintel_graph = codeintel_sub.add_parser("graph", help="Show call graph")
    codeintel_graph.add_argument("--source", default="", help="Source file")
    codeintel_graph.add_argument("--language", default="python", help="Language")
    codeintel_graph.add_argument("--daemon-url", default="http://localhost:8000")
    codeintel_graph.set_defaults(func=_handler("_cmd_code_graph"))
    codeintel_search = codeintel_sub.add_parser("search", help="Search code")
    codeintel_search.add_argument("query", help="Search query")
    codeintel_search.add_argument("--language", default="python", help="Language")
    codeintel_search.add_argument("--daemon-url", default="http://localhost:8000")
    codeintel_search.set_defaults(func=_handler("_cmd_code_search"))

    quant_parser = sub.add_parser("quantization", help="Model quantization detection")
    quant_parser.set_defaults(func=None)
    quant_sub = quant_parser.add_subparsers(dest="quantization_command")
    quant_list = quant_sub.add_parser("list", help="List known quantization info")
    quant_list.add_argument("--daemon-url", default="http://localhost:8000")
    quant_list.set_defaults(func=_handler("_cmd_quantization_list"))
    quant_detect = quant_sub.add_parser("detect", help="Detect quantization for a model")
    quant_detect.add_argument("--model-id", required=True, help="Model ID to detect")
    quant_detect.add_argument("--daemon-url", default="http://localhost:8000")
    quant_detect.set_defaults(func=_handler("_cmd_quantization_detect"))
    quant_drift = quant_sub.add_parser("drift-check", help="Check for quantization drift")
    quant_drift.add_argument("--daemon-url", default="http://localhost:8000")
    quant_drift.set_defaults(func=_handler("_cmd_quantization_drift_check"))

    slurm_parser = sub.add_parser("slurm", help="Slurm job management")
    slurm_parser.set_defaults(func=None)
    slurm_sub = slurm_parser.add_subparsers(dest="slurm_command")

    slurm_status = slurm_sub.add_parser("status", help="Check if Slurm is available")
    slurm_status.add_argument("--daemon-url", default="http://localhost:8000")
    slurm_status.set_defaults(func=_handler("_cmd_slurm_status"))

    slurm_submit = slurm_sub.add_parser("submit", help="Submit a Slurm job")
    slurm_submit.add_argument("--command", required=True, help="Job invocation string")
    slurm_submit.add_argument("--job-name", default=None, help="Job name")
    slurm_submit.add_argument("--partition", default=None, help="Partition")
    slurm_submit.add_argument("--cpus-per-task", type=int, default=None, help="CPUs per task")
    slurm_submit.add_argument("--gpus", default=None, help="GPU count or type")
    slurm_submit.add_argument("--memory", default=None, help="Memory e.g. 16G")
    slurm_submit.add_argument("--time-limit", default=None, help="Time limit e.g. 02:00:00")
    slurm_submit.add_argument("--daemon-url", default="http://localhost:8000")
    slurm_submit.set_defaults(func=_handler("_cmd_slurm_submit"))

    slurm_job = slurm_sub.add_parser("job", help="Check Slurm job status")
    slurm_job.add_argument("job_id", help="Job ID")
    slurm_job.add_argument("--daemon-url", default="http://localhost:8000")
    slurm_job.set_defaults(func=_handler("_cmd_slurm_job"))

    slurm_cancel = slurm_sub.add_parser("cancel", help="Cancel a Slurm job")
    slurm_cancel.add_argument("job_id", help="Job ID to cancel")
    slurm_cancel.add_argument("--daemon-url", default="http://localhost:8000")
    slurm_cancel.set_defaults(func=_handler("_cmd_slurm_cancel"))

    slurm_list = slurm_sub.add_parser("list", help="List Slurm jobs")
    slurm_list.add_argument("--daemon-url", default="http://localhost:8000")
    slurm_list.set_defaults(func=_handler("_cmd_slurm_list"))

    connectors_parser = sub.add_parser("connectors", help="Observability connector commands")
    connectors_parser.set_defaults(func=None)
    connectors_sub = connectors_parser.add_subparsers(dest="connectors_command")

    connectors_list = connectors_sub.add_parser("list", help="List registered observability sources")
    connectors_list.add_argument("--daemon-url", default="http://localhost:8000")
    connectors_list.set_defaults(func=_handler("_cmd_connectors_list"))

    connectors_health = connectors_sub.add_parser("health", help="Probe health across registered sources")
    connectors_health.add_argument("--daemon-url", default="http://localhost:8000")
    connectors_health.set_defaults(func=_handler("_cmd_connectors_health"))

    connectors_query = connectors_sub.add_parser("query", help="Run a query against a named observability source")
    connectors_query.add_argument("source", help="Registered source name to query")
    connectors_query.add_argument("--spec", default="{}", help="Query spec as a JSON string (default: {})")
    connectors_query.add_argument("--daemon-url", default="http://localhost:8000")
    connectors_query.set_defaults(func=_handler("_cmd_connectors_query"))

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
    login_parser.set_defaults(func=_handler("_cmd_login"))

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
    onboard_parser.set_defaults(func=_handler("_cmd_onboard"))

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

    # `gludd decision-codification` — bounded, proposal-only decision analysis.
    from general_ludd.cli_decision_codification import add_decision_codification_subparser

    add_decision_codification_subparser(sub)
    decision_codification_parser = sub.choices["decision-codification"]

    # `gludd ornith` — Ornith self-improving coding-agent integration.
    from general_ludd.cli_ornith import add_ornith_subparser

    add_ornith_subparser(sub)
    ornith_parser = sub.choices["ornith"]

    # `gludd searx` — SearXNG meta-search engine management.
    searx_parser = sub.add_parser("searx", help="SearXNG meta-search engine commands")
    searx_sub = searx_parser.add_subparsers(dest="searx_command")
    searx_start = searx_sub.add_parser("start", help="Start the local SearXNG server")
    searx_start.set_defaults(func=_handler("_cmd_searx"))
    searx_stop = searx_sub.add_parser("stop", help="Stop the local SearXNG server")
    searx_stop.set_defaults(func=_handler("_cmd_searx"))
    searx_status = searx_sub.add_parser("status", help="Check if SearXNG is running")
    searx_status.set_defaults(func=_handler("_cmd_searx"))
    searx_config = searx_sub.add_parser("config", help="Show/generate SearXNG configuration")
    searx_config.set_defaults(func=_handler("_cmd_searx"))

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
    make_parser.set_defaults(func=_handler("_cmd_make"))

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
    iam_generate.set_defaults(func=_handler("_cmd_cloud_iam_generate"))

    iam_validate = iam_sub.add_parser("validate", help="Validate an existing IAM role definition")
    iam_validate.add_argument("--provider", required=True, choices=["azure", "aws", "gcp"], help="Cloud provider")
    iam_validate.add_argument("--file", required=True, help="Path to JSON file containing the role definition")
    iam_validate.set_defaults(func=_handler("_cmd_cloud_iam_validate"))

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
    game_gen.set_defaults(func=_handler("_cmd_cloud_game_generate_multi"))

    # `gludd cloud generate` — generic project generation for any registered type.
    gen_parser = cloud_sub.add_parser("generate", help="Generic project generation for any registered type")
    gen_parser.set_defaults(func=None)
    gen_sub = gen_parser.add_subparsers(dest="generate_command")

    gen_list = gen_sub.add_parser("list-types", help="List all registered project types")
    gen_list.add_argument("--daemon-url", default="http://localhost:8000")
    gen_list.set_defaults(func=_handler("_cmd_cloud_generate_list_types"))

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
    gen_create.set_defaults(func=_handler("_cmd_cloud_generate_create"))

    gen_validate = gen_sub.add_parser("validate", help="Validate a generated project against type rules")
    gen_validate.add_argument("path", help="Path to the generated project directory")
    gen_validate.add_argument("--type", required=True, dest="project_type", help="Project type to validate against")
    gen_validate.add_argument("--daemon-url", default="http://localhost:8000")
    gen_validate.set_defaults(func=_handler("_cmd_cloud_generate_validate"))

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
    # tbg_launch.set_defaults(func=_handler("_cmd_testbg_launch"))
    # tbg_status = tbg_sub.add_parser("status", help="Check status of a background test")
    # tbg_status.add_argument("testfile", help="Test file path")
    # tbg_status.set_defaults(func=_handler("_cmd_testbg_status"))
    # tbg_poll = tbg_sub.add_parser("poll-all", help="Status for all tracked background tests")
    # tbg_poll.set_defaults(func=_handler("_cmd_testbg_poll_all"))
    # tbg_kill = tbg_sub.add_parser("kill", help="Kill a background test")
    # tbg_kill.add_argument("testfile", help="Test file path")
    # tbg_kill.add_argument("--force", action="store_true", help="Force SIGKILL after SIGTERM")
    # tbg_kill.set_defaults(func=_handler("_cmd_testbg_kill"))
    # tbg_results = tbg_sub.add_parser("results", help="Get final results for a completed test")
    # tbg_results.add_argument("testfile", help="Test file path")
    # tbg_results.set_defaults(func=_handler("_cmd_testbg_results"))

    smoke_parser = sub.add_parser("smoke", help="Run provider/service smoke checks")
    _add_smoke_arguments(smoke_parser)
    smoke_parser.set_defaults(func=_handler("_cmd_smoke"))

    test_parser = sub.add_parser("test", help="Test runner commands")
    test_parser.set_defaults(func=None)
    test_sub = test_parser.add_subparsers(dest="test_command")

    test_bg_parser = test_sub.add_parser("background", help="Background test runner commands")
    test_bg_parser.set_defaults(func=None)
    testbg2_sub = test_bg_parser.add_subparsers(dest="testbg_command")

    tbg2_launch = testbg2_sub.add_parser("launch", help="Launch a test in the background")
    tbg2_launch.add_argument("testfile", help="Test file path")
    tbg2_launch.add_argument("--wait", action="store_true", help="Block until test completes")
    tbg2_launch.set_defaults(func=_handler("_cmd_testbg_launch"))

    tbg2_status = testbg2_sub.add_parser("status", help="Check status of a background test")
    tbg2_status.add_argument("testfile", help="Test file path")
    tbg2_status.set_defaults(func=_handler("_cmd_testbg_status"))

    tbg2_poll = testbg2_sub.add_parser("poll-all", help="Status for all tracked background tests")
    tbg2_poll.set_defaults(func=_handler("_cmd_testbg_poll_all"))

    tbg2_kill = testbg2_sub.add_parser("kill", help="Kill a background test")
    tbg2_kill.add_argument("testfile", help="Test file path")
    tbg2_kill.add_argument("--force", action="store_true", help="Force SIGKILL after SIGTERM")
    tbg2_kill.set_defaults(func=_handler("_cmd_testbg_kill"))

    tbg2_results = testbg2_sub.add_parser("results", help="Get final results for a completed test")
    tbg2_results.add_argument("testfile", help="Test file path")
    tbg2_results.set_defaults(func=_handler("_cmd_testbg_results"))

    test_self_parser = test_sub.add_parser("self", help="Run self-tests via molecule scenarios")
    _configure_selftest_parser(test_self_parser)

    test_smoke_parser = test_sub.add_parser("smoke", help="Run provider/service smoke checks")
    _add_smoke_arguments(test_smoke_parser)
    test_smoke_parser.set_defaults(func=_handler("_cmd_smoke"))

    pause_parser = sub.add_parser("pause", help="Pause project or model execution")
    pause_parser.set_defaults(func=None)
    pause_sub = pause_parser.add_subparsers(dest="pause_command")
    pause_list = pause_sub.add_parser("list", help="List paused entities")
    pause_list.add_argument("--daemon-url", default="http://localhost:8000")
    pause_list.set_defaults(func=_handler("_cmd_pause_list"))
    for kind, handler in (("project", _handler("_cmd_pause_project")), ("model", _handler("_cmd_pause_model"))):
        command = pause_sub.add_parser(kind, help=f"Pause a {kind}")
        command.add_argument("target_id", help=f"{kind.capitalize()} identifier")
        command.add_argument("--reason", default="", help="Reason for pausing")
        command.add_argument("--daemon-url", default="http://localhost:8000")
        command.set_defaults(func=handler)

    resume_parser = sub.add_parser("resume", help="Resume project or model execution")
    resume_parser.set_defaults(func=None)
    resume_sub = resume_parser.add_subparsers(dest="resume_command")
    for kind, handler in (("project", _handler("_cmd_resume_project")), ("model", _handler("_cmd_resume_model"))):
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
        "decision-codification": decision_codification_parser,
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
        "smoke": smoke_parser,
        "chat": chat_parser,
        "audit-plugins": audit_plugins_parser,
        "pause": pause_parser,
        "resume": resume_parser,
    }

    return parser, subcommand_map


def build_parser_uncached(
    command_registry: Mapping[str, Any],
) -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    """Build a graph using callbacks supplied by the compatibility facade."""
    callbacks = {
        name: value
        for name, value in command_registry.items()
        if name.startswith("_cmd_") and callable(value)
    }
    global _ACTIVE_REGISTRY
    with _REGISTRY_LOCK:
        previous_registry = _ACTIVE_REGISTRY
        _ACTIVE_REGISTRY = callbacks
        try:
            return _build_parser_impl()
        finally:
            _ACTIVE_REGISTRY = previous_registry
