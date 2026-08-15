"""CLI subcommand: ``gludd remediation``.

Operator-facing commands for the remediation system. Mirrors the daemon's
``/admin/remediation/*`` endpoints. All calls send the ``GLUDD_AUTH_PSK`` env
var as a Bearer token (admin PSK required on /admin paths).
"""

from __future__ import annotations

import argparse
import json as _json
import os
import sys
from typing import Any

import httpx


def _psk_headers() -> dict[str, str]:
    psk = os.environ.get("GLUDD_AUTH_PSK", "").strip()
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if psk:
        headers["Authorization"] = "Bearer " + psk
    return headers


def _http(
    method: str,
    url: str,
    *,
    json_body: Any = None,
    params: Any = None,
    timeout: float = 10.0,
    ok_codes: tuple[int, ...] = (200, 201),
) -> Any:
    try:
        resp = httpx.request(
            method,
            url,
            json=json_body,
            params=params,
            headers=_psk_headers(),
            timeout=timeout,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    if resp.status_code not in ok_codes:
        print(f"Error: {resp.status_code} {resp.text}", file=sys.stderr)
        sys.exit(1)
    try:
        return resp.json()
    except Exception:
        return None


def _print_json(obj: Any) -> None:
    print(_json.dumps(obj, indent=2, default=str))


def _cmd_scan(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {}
    if args.project:
        params["project_id"] = args.project
    res = _http(
        "GET",
        f"{args.daemon_url}/admin/remediation/scan",
        params=params or None,
    )
    if args.json:
        _print_json(res)
        return
    blocked = (res or {}).get("blocked_tasks", []) if isinstance(res, dict) else []
    if not blocked:
        print("(no blocked tasks)")
        return
    print(f"Found {len(blocked)} blocked task(s):")
    for b in blocked:
        print(
            f"  [{b.get('blocker_kind')}] {b.get('todo_id')} "
            f"(remediation={b.get('suggested_remediation')}) "
            f"{int(b.get('blocked_duration_seconds', 0) // 3600)}h: "
            f"{b.get('blocker_summary', '')[:80]}"
        )


def _cmd_chronic(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {}
    if args.project:
        params["project_id"] = args.project
    if args.lookback_days is not None:
        params["lookback_days"] = args.lookback_days
    res = _http(
        "GET",
        f"{args.daemon_url}/admin/remediation/chronic-blockers",
        params=params or None,
    )
    if args.json:
        _print_json(res)
        return
    blockers = (res or {}).get("chronic_blockers", []) if isinstance(res, dict) else []
    if not blockers:
        print("(no chronic blockers)")
        return
    print(f"Found {len(blockers)} chronic blocker pair(s):")
    for b in blockers:
        print(
            f"  task_type={b.get('task_type') or '(none)'} "
            f"kind={b.get('blocker_kind')} count={b.get('incident_count')} "
            f"last={b.get('last_seen')}"
        )


def _cmd_history(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {"limit": args.limit}
    if args.project:
        params["project_id"] = args.project
    if args.since:
        params["since"] = args.since
    res = _http(
        "GET",
        f"{args.daemon_url}/admin/remediation/history",
        params=params,
    )
    if args.json:
        _print_json(res)
        return
    actions = (res or {}).get("actions", []) if isinstance(res, dict) else []
    if not actions:
        print("(no remediation history)")
        return
    print(f"Found {len(actions)} remediation action(s):")
    for a in actions:
        flag = "OK" if a.get("ok") else "FAIL"
        print(
            f"  [{a.get('action_kind')}/{flag}] {a.get('blocked_todo_id')} "
            f"@ {a.get('created_at')}: {a.get('summary', '')[:80]}"
        )


def _cmd_config_show(args: argparse.Namespace) -> None:
    res = _http("GET", f"{args.daemon_url}/admin/remediation/config")
    if args.json:
        _print_json(res)
        return
    if not isinstance(res, dict):
        return
    print("Remediation config (current thresholds):")
    print(f"  human_input_block_hours        = {res.get('human_input_block_hours')}")
    print(f"  permission_escalation_block_hours = {res.get('permission_escalation_block_hours')}")
    print(f"  max_requeues_before_chronic    = {res.get('max_requeues_before_chronic')}")
    print(f"  chronic_lookback_days          = {res.get('chronic_lookback_days')}")
    print(f"  min_chronic_incidents          = {res.get('min_chronic_incidents')}")
    print(f"  retry_delay_hours              = {res.get('retry_delay_hours')}")


def _cmd_config_edit(args: argparse.Namespace) -> None:
    """Open the operator-editable remediation config in $EDITOR.

    The runtime thresholds live in ``config/remediation.yml`` (loaded into
    daemon_state at startup). Editing the file live requires a daemon
    reload; this command just opens the editor and validates the YAML on
    save.
    """
    from pathlib import Path

    cfg_path = Path("config/remediation.yml")
    if not cfg_path.is_file():
        cfg_path.write_text(
            "# Remediation system thresholds (operator-editable).\n"
            "# Daemon reload required after edit (POST /admin/config/reload).\n\n"
            "human_input_block_hours: 24\n"
            "permission_escalation_block_hours: 4\n"
            "max_requeues_before_chronic: 3\n"
            "chronic_lookback_days: 7\n"
            "min_chronic_incidents: 5\n"
            "retry_delay_hours: 4\n"
        )
        print(f"Created default config at {cfg_path}")
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    import subprocess

    try:
        subprocess.run([editor, str(cfg_path)], check=True)
    except subprocess.CalledProcessError as exc:
        print(f"Editor exited with error: {exc}", file=sys.stderr)
        sys.exit(1)
    # Validate YAML.
    try:
        import yaml

        with cfg_path.open() as f:
            yaml.safe_load(f)
    except ImportError:
        # PyYAML not installed in this env — skip validation.
        print("(yaml not installed; skipping validation)")
    except Exception as exc:
        print(f"YAML parse error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Validated {cfg_path}. Restart the daemon (or reload) to apply.")


def add_remediation_subparser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``remediation`` subcommand tree for blocker detection and repair."""
    p = sub.add_parser(
        "remediation",
        help="Detect blocked agents/tasks and apply remediation.",
    )
    p.set_defaults(func=None)
    rsub = p.add_subparsers(dest="remediation_command")

    scan = rsub.add_parser("scan", help="Run the blocker detector once and print findings.")
    scan.add_argument("--project", default=None, help="Project ID filter")
    scan.add_argument("--daemon-url", default="http://localhost:8000")
    scan.add_argument("--json", action="store_true")
    scan.set_defaults(func=_cmd_scan)

    chronic = rsub.add_parser(
        "chronic-blockers",
        help="Print the chronic-blocker report.",
    )
    chronic.add_argument("--project", default=None, help="Project ID filter")
    chronic.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help="Override the chronic lookback window (days).",
    )
    chronic.add_argument("--daemon-url", default="http://localhost:8000")
    chronic.add_argument("--json", action="store_true")
    chronic.set_defaults(func=_cmd_chronic)

    history = rsub.add_parser(
        "history",
        help="Audit trail of past remediation actions.",
    )
    history.add_argument("--project", default=None, help="Project ID filter")
    history.add_argument(
        "--since",
        default=None,
        help="ISO datetime; only show actions on/after this timestamp.",
    )
    history.add_argument("--limit", type=int, default=100)
    history.add_argument("--daemon-url", default="http://localhost:8000")
    history.add_argument("--json", action="store_true")
    history.set_defaults(func=_cmd_history)

    cfg = rsub.add_parser("config", help="View/edit RemediationConfig thresholds.")
    csub = cfg.add_subparsers(dest="remediation_config_command")

    cfg_show = csub.add_parser("show", help="Print current thresholds.")
    cfg_show.add_argument("--daemon-url", default="http://localhost:8000")
    cfg_show.add_argument("--json", action="store_true")
    cfg_show.set_defaults(func=_cmd_config_show)

    cfg_edit = csub.add_parser(
        "edit",
        help="Open config/remediation.yml in $EDITOR; validate on save.",
    )
    cfg_edit.set_defaults(func=_cmd_config_edit)
