"""Unified CLI entrypoint for General Ludd Agent."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, cast

import httpx

from general_ludd.cli_commands import daemon_control as _daemon_control
from general_ludd.cli_commands import parser as _parser_impl
from general_ludd.cli_commands import platform as _platform_impl
from general_ludd.cli_commands import tui_views as _tui_views
from general_ludd.cli_commands.manual import MAN_PAGE
from general_ludd.cli_parser_cache import CommandGraphCache
from general_ludd.integrity.fim_excludes import FIM_EXCLUDE_PATTERNS
from general_ludd.integrity.scanner import FileIntegrityScanner
from general_ludd.tui.config_editor import ConfigEditor
from general_ludd.tui.runner import run_tui

_DAEMON_SHUTDOWN_TIMEOUT_SECONDS = 5.0
_BUNDLED_GUNICORN_FLAG = "--_gludd-bundled-gunicorn"

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


_add_smoke_arguments = _parser_impl._add_smoke_arguments


def _configure_selftest_parser(parser: argparse.ArgumentParser) -> None:
    """Keep the legacy helper signature and facade-owned handler lookup."""
    parser.add_argument("--daemon-url", default="http://localhost:8000")
    parser.set_defaults(func=_cmd_selftest)


def _build_parser_uncached() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    """Build from the current facade handlers so monkeypatches remain visible."""
    registry = {
        name: value
        for name, value in globals().items()
        if name.startswith("_cmd_") and callable(value)
    }
    return _parser_impl.build_parser_uncached(registry)


_ParserBundle = tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]
_parser_cache = CommandGraphCache(_build_parser_uncached, module_prefix="general_ludd.cli")


def build_parser() -> _ParserBundle:
    """Return one immutable command graph, rebuilding only for patched handlers.

    ``argparse`` parsing stores results in a fresh namespace, so the parser
    graph is safe to reuse. Tests and embedders sometimes replace command
    handlers; their changed fingerprint receives an isolated graph instead of
    contaminating the canonical cache.
    """
    return _parser_cache.get()


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
    """Parse process arguments and dispatch the selected CLI handler."""
    if _run_bundled_gunicorn_if_requested():
        return
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

    psk = os.environ.get("GLUDD_AUTH_PSK", "").strip()
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
    child_stdout, child_stderr = _daemon_child_stdio()
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=child_stdout,
        stderr=child_stderr,
        start_new_session=True,
        close_fds=True,
        env=env,
    )

    pid_file = getattr(args, "pid_file", None)
    if pid_file:
        _write_daemon_pid_file(pid_file, proc.pid, daemon_url=f"http://{bind_host}:{args.port}")

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
        if pid_file:
            with contextlib.suppress(OSError):
                os.unlink(pid_file)
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


_fmt_size = _platform_impl._fmt_size
_format_offline_status = _platform_impl._format_offline_status
_gather_offline_status = _platform_impl.gather_offline_status


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


_scale_col = _tui_views._scale_col
_compute_panel_widths = _tui_views._compute_panel_widths
_table_overhead = _tui_views._table_overhead
_wrap_table = _tui_views._wrap_table
_compute_footer_rows = _tui_views._compute_footer_rows
_build_controls_table = _tui_views._build_controls_table
_build_daemon_table = _tui_views._build_daemon_table
_build_info_table = _tui_views._build_info_table
_build_binary_table = _tui_views._build_binary_table
_build_config_table = _tui_views._build_config_table
_build_todos_table = _tui_views._build_todos_table
_build_hooks_table = _tui_views._build_hooks_table
_build_workers_table = _tui_views._build_workers_table
_build_metrics_table = _tui_views._build_metrics_table
_build_agents_table = _tui_views._build_agents_table
_build_model_table = _tui_views._build_model_table
_build_config_editor_table = _tui_views._build_config_editor_table
_build_worktrees_table = _tui_views._build_worktrees_table
_build_projects_table = _tui_views._build_projects_table
_build_integrity_table = _tui_views._build_integrity_table
_build_ansible_table = _tui_views._build_ansible_table
_build_model_status_msg = _tui_views._build_model_status_msg
_build_mcp_table = _tui_views._build_mcp_table
_build_skills_table = _tui_views._build_skills_table
_build_compute_table = _tui_views._build_compute_table
_build_scores_table = _tui_views._build_scores_table
_build_leaderboard_table = _tui_views._build_leaderboard_table
_build_templates_table = _tui_views._build_templates_table
_build_playbooks_table = _tui_views._build_playbooks_table
_build_quantization_table = _tui_views._build_quantization_table
_build_filestore_table = _tui_views._build_filestore_table
_build_deployments_table = _tui_views._build_deployments_table
_build_slurm_table = _tui_views._build_slurm_table
_build_health_table = _tui_views._build_health_table
_build_selftest_table = _tui_views._build_selftest_table
_build_version_table = _tui_views._build_version_table
_build_loglevel_table = _tui_views._build_loglevel_table
_build_discovered_table = _tui_views._build_discovered_table
_build_code_table = _tui_views._build_code_table


_DAEMON_PID_DIR = os.path.expanduser("~/.local/share/general-ludd")
_DAEMON_PID_FILE = os.path.join(_DAEMON_PID_DIR, "daemon.pid")


def _get_daemon_pid_dir() -> str:
    os.makedirs(_DAEMON_PID_DIR, exist_ok=True)
    return _DAEMON_PID_DIR


_write_daemon_pid_file = _daemon_control._write_daemon_pid_file
_read_daemon_pid_file = _daemon_control._read_daemon_pid_file
_is_daemon_pid_alive = _daemon_control._is_daemon_pid_alive
_stop_daemon_via_pid_file = _daemon_control._stop_daemon_via_pid_file
_LOG_LEVEL_ALLOWLIST = _daemon_control._LOG_LEVEL_ALLOWLIST
_HOSTNAME_LABEL_RE = _daemon_control._HOSTNAME_LABEL_RE
_validate_daemon_host = _daemon_control._validate_daemon_host
_validate_daemon_port = _daemon_control._validate_daemon_port
_validate_daemon_log_level = _daemon_control._validate_daemon_log_level
_validate_daemon_path = _daemon_control._validate_daemon_path
_build_daemon_env = _daemon_control._build_daemon_env
_clamp_workers_for_sqlite = _daemon_control._clamp_workers_for_sqlite
_run_bundled_gunicorn_if_requested = _daemon_control._run_bundled_gunicorn_if_requested
_daemon_child_stdio = _daemon_control._daemon_child_stdio
_build_daemon_start_cmd = _daemon_control._build_daemon_start_cmd


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
    raw_models = data.get("profiles", data.get("models", []))
    if isinstance(raw_models, dict):
        models = [
            {"model_id": model_id, **profile}
            for model_id, profile in sorted(raw_models.items())
            if isinstance(profile, dict)
        ]
    elif isinstance(raw_models, list):
        models = [profile for profile in raw_models if isinstance(profile, dict)]
    else:
        models = []
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
    raw_best = data.get("best")
    best = raw_best if isinstance(raw_best, dict) else data
    prec = best.get("precision", "unknown")
    conf = best.get("confidence", 0)
    print(f"  {mid}  prec={prec}  conf={conf:.2f}")


def _cmd_quantization_drift_check(args: argparse.Namespace) -> None:
    data = _http_call("POST", f"{args.daemon_url}/admin/quantization/drift-check", timeout=30.0)
    if data is None:
        return
    if data.get("drift_detected"):
        raw_changes = data.get("changes", data.get("drifted_models", []))
        changes = raw_changes if isinstance(raw_changes, list) else []
        print(f"Drift detected in {len(changes)} model(s)")
        for m in changes:
            if not isinstance(m, dict):
                continue
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
    # The module invocation contract is aligned with the standalone binary:
    # ``python -m general_ludd.cli --version`` prints the release version and
    # exits 0 (pinned by tests/e2e/test_binary_functional.py). build_parser()
    # already declares the version action.
    main()
