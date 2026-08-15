"""CLI subcommand: ``gludd deploy-check``.

Operator-facing wrapper over the daemon's static model-deployment
misconfiguration detector. It loads a serving-config file (JSON or YAML),
POSTs it to ``/admin/deployments/misconfig-check``, and pretty-prints the
findings plus each finding's remediation ``config_patch``.

* ``gludd deploy-check run --config deployment.yaml``
* ``gludd deploy-check run --config d.json --gpu-type h100 --gpu-count 8``

Requests carry ``GLUDD_AUTH_PSK`` as a Bearer token so the daemon's PSK middleware
authenticates them.
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
    headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
    if psk:
        headers["Authorization"] = "Bearer " + psk
    return headers


def _http(
    method: str,
    url: str,
    *,
    json_body: Any = None,
    timeout: float = 10.0,
    ok_codes: tuple[int, ...] = (200, 201),
) -> Any:
    try:
        resp = httpx.request(method, url, json=json_body, headers=_psk_headers(), timeout=timeout)
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


def _load_deployment(path: str) -> Any:
    """Load a deployment config from a JSON or YAML file.

    ``.json`` parses as JSON; anything else is parsed as YAML (falling back to
    JSON when PyYAML is unavailable). Load errors exit non-zero.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except Exception as exc:
        print(f"Error: cannot read {path}: {exc}", file=sys.stderr)
        sys.exit(1)

    lowered = path.lower()
    try:
        if lowered.endswith(".json"):
            return _json.loads(raw)
        try:
            import yaml
        except Exception:
            return _json.loads(raw)
        return yaml.safe_load(raw)
    except Exception as exc:
        print(f"Error: cannot parse {path}: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_run(args: argparse.Namespace) -> None:
    deployment = _load_deployment(args.config)
    body: dict[str, Any] = {"deployment": deployment}
    if args.gpu_type:
        body["gpu_type"] = args.gpu_type
        body["gpu_count"] = args.gpu_count
    data = (
        _http(
            "POST",
            f"{args.daemon_url}/admin/deployments/misconfig-check",
            json_body=body,
        )
        or {}
    )
    if args.json:
        _print_json(data)
        return

    findings = data.get("findings", []) if isinstance(data, dict) else []
    remediations = data.get("remediations", []) if isinstance(data, dict) else []
    has_critical = bool(data.get("has_critical")) if isinstance(data, dict) else False

    if not findings:
        print("No misconfigurations detected.")
        return

    patch_by_rule: dict[str, Any] = {}
    for rem in remediations:
        if isinstance(rem, dict):
            patch_by_rule[str(rem.get("rule_id", ""))] = rem.get("config_patch", {})

    print(f"Found {len(findings)} finding(s) (has_critical={has_critical}):")
    for f in findings:
        if not isinstance(f, dict):
            continue
        rule_id = str(f.get("rule_id", ""))
        severity = str(f.get("severity", ""))
        engine = str(f.get("engine", ""))
        message = str(f.get("message", ""))
        print(f"  [{severity.upper():<8}] rule {rule_id} ({engine}): {message}")
        print(f"      remediation: {f.get('remediation', '')}")
        patch = patch_by_rule.get(rule_id)
        if patch:
            print(f"      config_patch: {_json.dumps(patch, default=str)}")

    if has_critical:
        sys.exit(2)


def _cmd_suggest_fix(args: argparse.Namespace) -> None:
    """POST a deployment to /suggest-fix and print the proposed patch + source."""
    deployment = _load_deployment(args.config)
    body: dict[str, Any] = {"deployment": deployment}
    if args.gpu_type:
        body["gpu_type"] = args.gpu_type
        body["gpu_count"] = args.gpu_count
    data = (
        _http(
            "POST",
            f"{args.daemon_url}/admin/deployments/suggest-fix",
            json_body=body,
        )
        or {}
    )
    if args.json:
        _print_json(data)
        return
    if not isinstance(data, dict):
        print("No fix suggestion returned.")
        return
    print(f"fix_id: {data.get('fix_id', '')}")
    print(f"source: {data.get('source', '')}")
    patch = data.get("patch", {})
    if patch:
        print(f"patch: {_json.dumps(patch, default=str)}")
    else:
        print("patch: (empty — no config change proposed)")


def _cmd_approve(args: argparse.Namespace) -> None:
    """POST /fixes/{fix_id}/approve (optionally with retry) and print the result."""
    data = (
        _http(
            "POST",
            f"{args.daemon_url}/admin/deployments/fixes/{args.fix_id}/approve",
            json_body={"retry": bool(args.retry)},
        )
        or {}
    )
    if args.json:
        _print_json(data)
        return
    if not isinstance(data, dict):
        print("No response.")
        return
    print(f"status: {data.get('status', '')}")
    merged = data.get("merged_config", {})
    if merged:
        print(f"merged_config: {_json.dumps(merged, default=str)}")
    if "retried" in data:
        print(f"retried: {data.get('retried')}")
    if data.get("note"):
        print(f"note: {data['note']}")


def _cmd_reject(args: argparse.Namespace) -> None:
    """POST /fixes/{fix_id}/reject with an optional reason and print the result."""
    data = (
        _http(
            "POST",
            f"{args.daemon_url}/admin/deployments/fixes/{args.fix_id}/reject",
            json_body={"reason": args.reason or ""},
        )
        or {}
    )
    if args.json:
        _print_json(data)
        return
    if not isinstance(data, dict):
        print("No response.")
        return
    print(f"status: {data.get('status', '')}")
    if data.get("reason"):
        print(f"reason: {data['reason']}")


def add_deploy_check_subparser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``deploy-check`` subcommand tree for serving-config linting."""
    p = sub.add_parser("deploy-check", help="Statically lint a model serving config for misconfigs")
    p.set_defaults(func=None)
    dsub = p.add_subparsers(dest="deploy_check_command")

    run = dsub.add_parser("run", help="Lint a deployment config file via the daemon detector")
    run.add_argument("--config", required=True, help="Path to a deployment config (.yaml/.yml/.json)")
    run.add_argument("--gpu-type", default=None, help="GPU type (e.g. h100, a100_80, l40s)")
    run.add_argument("--gpu-count", type=int, default=1, help="Number of GPUs (default 1)")
    run.add_argument("--daemon-url", default="http://localhost:8000")
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=_cmd_run)

    suggest = dsub.add_parser(
        "suggest-fix",
        help="Propose a config-patch fix for a deployment's misconfigs (SLM + deterministic)",
    )
    suggest.add_argument("--config", required=True, help="Path to a deployment config (.yaml/.yml/.json)")
    suggest.add_argument("--gpu-type", default=None, help="GPU type (e.g. h100, a100_80, l40s)")
    suggest.add_argument("--gpu-count", type=int, default=1, help="Number of GPUs (default 1)")
    suggest.add_argument("--daemon-url", default="http://localhost:8000")
    suggest.add_argument("--json", action="store_true")
    suggest.set_defaults(func=_cmd_suggest_fix)

    approve = dsub.add_parser("approve", help="Approve a parked fix proposal and print its merged config")
    approve.add_argument("fix_id", help="The fix_id returned by suggest-fix")
    approve.add_argument("--retry", action="store_true", help="Re-run the deploy with the merged config")
    approve.add_argument("--daemon-url", default="http://localhost:8000")
    approve.add_argument("--json", action="store_true")
    approve.set_defaults(func=_cmd_approve)

    reject = dsub.add_parser("reject", help="Reject a parked fix proposal")
    reject.add_argument("fix_id", help="The fix_id returned by suggest-fix")
    reject.add_argument("--reason", default="", help="Reason recorded on the proposal")
    reject.add_argument("--daemon-url", default="http://localhost:8000")
    reject.add_argument("--json", action="store_true")
    reject.set_defaults(func=_cmd_reject)
