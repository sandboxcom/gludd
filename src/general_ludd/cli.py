"""Unified CLI entrypoint for General Ludd Agent."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

import httpx


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

    local_serve_parser = sub.add_parser("local-serve", help="Start a local inference server")
    local_serve_parser.add_argument("--engine", default="vllm", choices=["vllm", "llamacpp"])
    local_serve_parser.add_argument("--model", required=True, help="Model name or path")
    local_serve_parser.add_argument("--host", default="localhost")
    local_serve_parser.add_argument("--port", type=int, default=8001)
    local_serve_parser.add_argument("--gpu-layers", type=int, default=-1)
    local_serve_parser.add_argument("--context-size", type=int, default=4096)
    local_serve_parser.add_argument("--daemon-url", default="http://localhost:8000")
    local_serve_parser.set_defaults(func=_cmd_local_serve)

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

    args = parser.parse_args()
    if args.func is None:
        subcommand_map = {
            "models": models_parser,
            "mcp": mcp_parser,
            "skills": skills_parser,
            "compute": compute_parser,
        }
        if args.command in subcommand_map:
            subcommand_map[args.command].print_help()
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
    sys.exit(subprocess.call(cmd))


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


def _cmd_status(args: argparse.Namespace) -> None:
    try:
        if args.todo_id:
            resp = httpx.get(f"{args.daemon_url}/api/todos/{args.todo_id}", timeout=10.0)
        else:
            resp = httpx.get(f"{args.daemon_url}/api/status", timeout=10.0)
        if resp.status_code == 200:
            print(json.dumps(resp.json(), indent=2))
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        _handle_connection_error(exc, args.daemon_url)


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


if __name__ == "__main__":
    main()
