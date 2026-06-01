"""Unified CLI entrypoint for General Ludd Agent."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any


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
    models_sub = models_parser.add_subparsers(dest="models_command")
    models_sub.metavar = ""

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

    args = parser.parse_args()
    if args.func is None:
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
    import httpx

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
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_status(args: argparse.Namespace) -> None:
    import httpx

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
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_list(args: argparse.Namespace) -> None:
    import httpx

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
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_log_level(args: argparse.Namespace) -> None:
    import httpx

    try:
        resp = httpx.post(f"{args.daemon_url}/admin/log-level", json={"level": args.level}, timeout=10.0)
        if resp.status_code == 200:
            data = resp.json()
            print(f"Log level changed to {data['level']}")
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_deployments(args: argparse.Namespace) -> None:
    import httpx

    try:
        resp = httpx.get(f"{args.daemon_url}/api/deployments", timeout=10.0)
        if resp.status_code == 200:
            print(json.dumps(resp.json(), indent=2))
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_version(args: argparse.Namespace) -> None:
    from general_ludd import __version__

    print(f"general-ludd-agent {__version__}")


def _cmd_health(args: argparse.Namespace) -> None:
    import httpx

    try:
        resp = httpx.get(f"{args.daemon_url}/healthz", timeout=10.0)
        if resp.status_code == 200:
            print(json.dumps(resp.json(), indent=2))
        else:
            print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
            sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_models_search(args: argparse.Namespace) -> None:
    import httpx

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
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_models_downloaded(args: argparse.Namespace) -> None:
    import httpx

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
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_local_serve(args: argparse.Namespace) -> None:
    import httpx

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
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
