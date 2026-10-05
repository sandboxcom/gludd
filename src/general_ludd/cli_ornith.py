"""CLI subcommand: ``gludd ornith``.

Operator-facing commands for the Ornith self-improving coding-agent
integration. Mirrors the daemon's ``/admin/ornith/*`` endpoints. All
HTTP-backed calls send the ``GLUDD_AUTH_PSK`` env var as a Bearer token
(admin PSK required on /admin paths).

Subcommands (all support ``--json`` for scripting):
  status         Show MCP server / endpoint status.
  solve          Dispatch a code-gen sub-task (interactive patch).
  improve        Submit a (scaffold, outcome) pair for the RL trainer.
  pairs          List training pairs.
  export         JSONL export of training pairs.
  stats          Success rate + token consumption.
  set-outcome    Manual override of a pair's outcome.
  enable         Set ornith_enabled=true in <config_dir>/gludd/config.yml.
  disable        Set ornith_enabled=false in <config_dir>/gludd/config.yml.
  train          Trigger a self-improvement training cycle (alias: self-improve).
  history        Show past self-improvement training cycles.
  config get     View Ornith configuration.
  config set     Update Ornith configuration (--model-sha).
  doctor         Diagnose: binary, model_sha, daemon endpoint, perms, sandbox.
"""

from __future__ import annotations

import argparse
import json as _json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import httpx
import yaml

_ORNITH_CONFIG_FILENAME = "config.yml"
_ORNITH_CONFIG_DIR_DEFAULT = "~/.config/gludd"


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
    timeout: float = 30.0,
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


def _cmd_pairs(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {"limit": args.limit}
    if args.status:
        params["status"] = args.status
    # The /admin/ornith/pending endpoint only returns pending pairs; for
    # other statuses we hit the stats endpoint and report counts. For
    # listing arbitrary statuses, we use the pending endpoint when
    # status is "pending" or unset; otherwise we fall back to stats.
    if args.status and args.status != "pending":
        res = _http("GET", f"{args.daemon_url}/admin/ornith/stats")
        if args.json:
            _print_json(res)
            return
        counts = (res or {}).get("counts_by_status", {}) if isinstance(res, dict) else {}
        print("Counts by outcome status:")
        for status, count in sorted(counts.items()):
            print(f"  {status:<22} {count}")
        return
    res = _http(
        "GET",
        f"{args.daemon_url}/admin/ornith/pending",
        params={"limit": params["limit"]},
    )
    if args.json:
        _print_json(res)
        return
    pending = (res or {}).get("pending", []) if isinstance(res, dict) else []
    if not pending:
        print("(no pending ornith pairs)")
        return
    print(f"Found {len(pending)} pending pair(s):")
    for p in pending:
        print(
            f"  [{p.get('scaffold_kind')}] {p.get('id')} "
            f"tokens={p.get('tokens_consumed')} "
            f"@ {p.get('invoked_at')}: "
            f"{(p.get('task_description') or '')[:80]}"
        )


def _cmd_export(args: argparse.Namespace) -> None:
    params: dict[str, Any] = {}
    if args.since:
        params["since"] = args.since
    if args.project:
        params["project_id"] = args.project
    if args.out:
        params["out_path"] = args.out
    res = _http(
        "GET",
        f"{args.daemon_url}/admin/ornith/export",
        params=params or None,
        timeout=120.0,
    )
    if args.json:
        _print_json(res)
        return
    if isinstance(res, dict):
        print(f"Exported {res.get('row_count', 0)} row(s) to {res.get('path')}")
    else:
        print("Export failed: no response body")


def _cmd_stats(args: argparse.Namespace) -> None:
    res = _http("GET", f"{args.daemon_url}/admin/ornith/stats")
    if args.json:
        _print_json(res)
        return
    if not isinstance(res, dict):
        return
    counts = res.get("counts_by_status", {}) or {}
    print("Ornith training-pair stats:")
    print(f"  total pairs:      {res.get('total', 0)}")
    print(f"  success rate:     {res.get('success_rate', 0.0):.2%}")
    print(f"  avg tokens/call:  {res.get('avg_tokens_per_call', 0.0):.1f}")
    print("  counts by status:")
    for status in sorted(counts):
        print(f"    {status:<22} {counts[status]}")


def _cmd_set_outcome(args: argparse.Namespace) -> None:
    details: dict[str, Any] = {}
    if args.details:
        details["note"] = args.details
    body = {"status": args.status, "details": details}
    res = _http(
        "PATCH",
        f"{args.daemon_url}/admin/ornith/{args.pair_id}/outcome",
        json_body=body,
    )
    if args.json:
        _print_json(res)
        return
    if isinstance(res, dict):
        print(f"Set outcome for {res.get('id')} -> {res.get('outcome_status')} @ {res.get('outcome_set_at')}")
    else:
        print("set-outcome failed")


# ---------------------------------------------------------------------------
# status (MCP server / endpoint)
# ---------------------------------------------------------------------------


def _cmd_status(args: argparse.Namespace) -> None:
    res = _http("GET", f"{args.daemon_url}/admin/ornith/status")
    if args.json:
        _print_json(res)
        return
    if not isinstance(res, dict):
        print("(no status)")
        return
    installed = bool(res.get("installed"))
    print(f"installed:      {'yes' if installed else 'no'}")
    if res.get("version") is not None:
        print(f"version:        {res.get('version')}")
    if res.get("model_sha") is not None:
        print(f"model_sha:      {res.get('model_sha')}")
    if res.get("last_call_at") is not None:
        print(f"last_call_at:   {res.get('last_call_at')}")
    if res.get("total_calls") is not None:
        print(f"total_calls:    {res.get('total_calls')}")
    if res.get("success_rate") is not None:
        print(f"success_rate:   {res.get('success_rate')}")


# ---------------------------------------------------------------------------
# solve (interactive code-gen dispatch)
# ---------------------------------------------------------------------------


def _cmd_solve(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {
        "task": args.task,
        "target_files": list(args.target_files or []),
    }
    if args.max_iter is not None:
        body["max_iterations"] = args.max_iter
    res = _http(
        "POST",
        f"{args.daemon_url}/admin/ornith/solve",
        json_body=body,
        timeout=120.0,
    )
    if args.json:
        _print_json(res)
        return
    if not isinstance(res, dict):
        print("(no response)")
        return
    if res.get("patch"):
        print("patch:")
        print(res["patch"])
    if res.get("summary"):
        print(f"\nsummary: {res['summary']}")
    if res.get("iterations") is not None:
        print(f"iterations: {res.get('iterations')}")
    if res.get("tokens") is not None:
        print(f"tokens:     {res.get('tokens')}")
    if res.get("pair_id"):
        print(f"pair_id:    {res.get('pair_id')}")


# ---------------------------------------------------------------------------
# improve (scaffold / outcome submission)
# ---------------------------------------------------------------------------


def _cmd_improve(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {
        "artifact_path": args.artifact_path,
        "kind": args.kind,
    }
    if args.feedback is not None:
        body["feedback"] = args.feedback
    res = _http(
        "POST",
        f"{args.daemon_url}/admin/ornith/improve",
        json_body=body,
    )
    if args.json:
        _print_json(res)
        return
    if isinstance(res, dict):
        print(f"submitted: {res.get('status', 'ok')}")
        if res.get("improve_id"):
            print(f"improve_id: {res.get('improve_id')}")
    else:
        print("(no response)")


# ---------------------------------------------------------------------------
# enable / disable (config-file backed)
# ---------------------------------------------------------------------------


def _resolve_config_path(args: argparse.Namespace) -> Path:
    cfg_dir = getattr(args, "config_dir", None) or os.path.expanduser(_ORNITH_CONFIG_DIR_DEFAULT)
    return Path(cfg_dir) / _ORNITH_CONFIG_FILENAME


def _write_ornith_enabled(cfg_path: Path, enabled: bool) -> None:
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if cfg_path.exists():
        try:
            with cfg_path.open() as f:
                loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    data["ornith_enabled"] = bool(enabled)
    with cfg_path.open("w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)


def _cmd_enable(args: argparse.Namespace) -> None:
    cfg_path = _resolve_config_path(args)
    _write_ornith_enabled(cfg_path, True)
    if args.json:
        _print_json({"ornith_enabled": True, "path": str(cfg_path)})
        return
    print(f"ornith_enabled: true (written to {cfg_path})")


def _cmd_disable(args: argparse.Namespace) -> None:
    cfg_path = _resolve_config_path(args)
    _write_ornith_enabled(cfg_path, False)
    if args.json:
        _print_json({"ornith_enabled": False, "path": str(cfg_path)})
        return
    print(f"ornith_enabled: false (written to {cfg_path})")


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def _perm_spec_has_ornith(spec_path: Path) -> bool:
    """Best-effort check: does the permissions dir include an agent:ornith spec?

    Looks for either a file named ``agent-ornith.yml`` OR any YAML file
    whose ``principal``/``actor`` field mentions ``agent:ornith``.
    """
    if not spec_path.is_dir():
        return False
    for yf in spec_path.glob("*.yml"):
        try:
            with yf.open() as f:
                data = yaml.safe_load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        principal = str(data.get("principal") or data.get("actor") or "")
        if "agent:ornith" in principal:
            return True
        perms = data.get("permissions") or data.get("capabilities")
        if isinstance(perms, list):
            for entry in perms:
                if isinstance(entry, dict):
                    p = str(entry.get("principal") or entry.get("actor") or "")
                    if "agent:ornith" in p:
                        return True
    return (spec_path / "agent-ornith.yml").is_file()


def _cmd_doctor(args: argparse.Namespace) -> None:
    """Diagnose ornith readiness. Exits 0 when healthy, 1 on any failure."""
    daemon_url = args.daemon_url
    perms_dir = Path(args.perms_dir) if args.perms_dir else Path("config/permissions")

    findings: list[dict[str, Any]] = []

    # 1. Binary on PATH.
    bin_name = args.binary_name
    binary_path = shutil.which(bin_name)
    findings.append(
        {
            "check": "binary_on_path",
            "ok": binary_path is not None,
            "detail": binary_path or f"{bin_name} not found on PATH",
        }
    )

    # 2. Daemon endpoint reachable.
    status_payload: dict[str, Any] | None = None
    daemon_ok: bool
    try:
        status_payload = _http(
            "GET",
            f"{daemon_url}/admin/ornith/status",
            timeout=5.0,
        )
        daemon_ok = isinstance(status_payload, dict)
    except SystemExit:
        daemon_ok = False
        status_payload = None
    findings.append(
        {
            "check": "daemon_endpoint",
            "ok": daemon_ok,
            "detail": (
                f"{daemon_url}/admin/ornith/status reachable"
                if daemon_ok
                else f"{daemon_url}/admin/ornith/status unreachable"
            ),
        }
    )

    # 3. model_sha matches config (if expected sha is known).
    expected_sha = os.environ.get("ORNITH_MODEL_SHA") or args.expected_model_sha
    actual_sha = status_payload.get("model_sha") if isinstance(status_payload, dict) else None
    if expected_sha and actual_sha:
        sha_ok = expected_sha == actual_sha
        findings.append(
            {
                "check": "model_sha_matches",
                "ok": sha_ok,
                "detail": (
                    f"model_sha matches ({actual_sha})"
                    if sha_ok
                    else f"expected {expected_sha}, daemon reports {actual_sha}"
                ),
            }
        )
    elif actual_sha:
        findings.append(
            {
                "check": "model_sha_matches",
                "ok": True,
                "detail": (f"daemon reports model_sha={actual_sha} (no expected sha configured)"),
            }
        )
    else:
        findings.append(
            {
                "check": "model_sha_matches",
                "ok": False,
                "detail": "no model_sha reported by daemon",
            }
        )

    # 4. Permission spec includes agent:ornith.
    perm_ok = _perm_spec_has_ornith(perms_dir)
    findings.append(
        {
            "check": "permission_spec_includes_agent_ornith",
            "ok": perm_ok,
            "detail": (
                f"found agent:ornith entry in {perms_dir}" if perm_ok else f"no agent:ornith entry under {perms_dir}"
            ),
        }
    )

    # 5. Sandbox backend available (read from status if present).
    sandbox_backend = status_payload.get("sandbox_backend") if isinstance(status_payload, dict) else None
    sandbox_ok = bool(sandbox_backend) and sandbox_backend != "none"
    findings.append(
        {
            "check": "sandbox_backend_available",
            "ok": sandbox_ok,
            "detail": (
                f"sandbox backend: {sandbox_backend}" if sandbox_backend else "no sandbox backend reported by daemon"
            ),
        }
    )

    healthy = all(f["ok"] for f in findings)
    if args.json:
        _print_json({"findings": findings, "healthy": healthy})
    else:
        for f in findings:
            mark = "OK  " if f["ok"] else "FAIL"
            print(f"[{mark}] {f['check']}: {f['detail']}")
        if healthy:
            print("\nornith doctor: all checks passed")
        else:
            failed = [f["check"] for f in findings if not f["ok"]]
            print(f"\nornith doctor: {len(failed)} check(s) failed: {', '.join(failed)}")
    if not healthy:
        sys.exit(1)


# ---------------------------------------------------------------------------
# train / self-improve
# ---------------------------------------------------------------------------


def _cmd_train(args: argparse.Namespace) -> None:
    res = _http(
        "POST",
        f"{args.daemon_url}/admin/ornith/self-improve",
        timeout=args.timeout,
    )
    if args.json:
        _print_json(res)
        return
    if isinstance(res, dict):
        cycle = res.get("cycle", {})
        triggered_at = cycle.get("triggered_at", "?")
        result = cycle.get("result", {})
        if result.get("findings_count") is not None:
            print(f"Training cycle triggered at {triggered_at}")
            print(f"  findings: {result.get('findings_count', 0)}")
            print(f"  todos:    {result.get('todos_enqueued', 0)}")
        else:
            print(f"Training cycle triggered at {triggered_at}")
            print(f"  result:   {result}")
    else:
        print("(no response)")


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


def _cmd_history(args: argparse.Namespace) -> None:
    res = _http(
        "GET",
        f"{args.daemon_url}/admin/ornith/history",
        params={"limit": args.limit},
    )
    if args.json:
        _print_json(res)
        return
    if not isinstance(res, dict):
        print("(no history)")
        return
    cycles = res.get("cycles", [])
    if not cycles:
        print("(no self-improvement cycles yet)")
        return
    print(f"Self-improvement cycles ({len(cycles)}):")
    for i, cycle in enumerate(cycles, 1):
        ts = cycle.get("triggered_at", "?")
        result = cycle.get("result", {})
        fc = result.get("findings_count", "?")
        te = result.get("todos_enqueued", "?")
        print(f"  {i}. [{ts}] findings={fc} todos={te}")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


def _cmd_config_get(args: argparse.Namespace) -> None:
    res = _http("GET", f"{args.daemon_url}/admin/ornith/config")
    if args.json:
        _print_json(res)
        return
    if not isinstance(res, dict):
        print("(no config)")
        return
    print("Ornith configuration:")
    print(f"  enabled:      {res.get('ornith_enabled', False)}")
    print(f"  model_sha:    {res.get('model_sha', '(none)')}")
    print(f"  binary_path:  {res.get('binary_path', 'ornith')}")
    env_enabled = res.get("env_ornith_enabled", False)
    print(f"  env_enabled:  {env_enabled}")


def _cmd_config_set(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {}
    if args.model_sha is not None:
        body["model_sha"] = args.model_sha
    res = _http(
        "PUT",
        f"{args.daemon_url}/admin/ornith/config",
        json_body=body,
    )
    if args.json:
        _print_json(res)
        return
    if isinstance(res, dict):
        print("Config updated:")
        print(f"  model_sha:    {res.get('model_sha', '(none)')}")


def add_ornith_subparser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``ornith`` subcommand tree for the training-data collector."""
    p = sub.add_parser(
        "ornith",
        help="Ornith training-data collector (scaffold/outcome pairs).",
    )
    p.set_defaults(func=None)
    osub = p.add_subparsers(dest="ornith_command")

    status = osub.add_parser("status", help="Show Ornith MCP server / endpoint status.")
    status.add_argument("--daemon-url", default="http://localhost:8000")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=_cmd_status)

    solve = osub.add_parser("solve", help="Dispatch a code-gen sub-task.")
    solve.add_argument("--task", required=True, help="Task description.")
    solve.add_argument(
        "--target-files",
        nargs="+",
        required=True,
        dest="target_files",
        help="One or more target file paths.",
    )
    solve.add_argument("--max-iter", type=int, default=None, help="Max iterations.")
    solve.add_argument("--daemon-url", default="http://localhost:8000")
    solve.add_argument("--json", action="store_true")
    solve.set_defaults(func=_cmd_solve)

    improve = osub.add_parser("improve", help="Submit a (scaffold, outcome) pair to the RL trainer.")
    improve.add_argument("--artifact-path", required=True, dest="artifact_path")
    improve.add_argument(
        "--kind",
        required=True,
        choices=["playbook", "module", "plugin", "rego"],
    )
    improve.add_argument("--feedback", default=None)
    improve.add_argument("--daemon-url", default="http://localhost:8000")
    improve.add_argument("--json", action="store_true")
    improve.set_defaults(func=_cmd_improve)

    pairs = osub.add_parser("pairs", help="List Ornith training pairs.")
    pairs.add_argument("--status", default=None, help="Filter by outcome status.")
    pairs.add_argument("--limit", type=int, default=100)
    pairs.add_argument("--daemon-url", default="http://localhost:8000")
    pairs.add_argument("--json", action="store_true")
    pairs.set_defaults(func=_cmd_pairs)

    export = osub.add_parser("export", help="Export JSONL dataset for RL trainer.")
    export.add_argument("--since", default=None, help="ISO datetime lower bound.")
    export.add_argument("--project", default=None, help="Project ID filter.")
    export.add_argument("--out", default=None, help="Output path (defaults to CWD).")
    export.add_argument("--daemon-url", default="http://localhost:8000")
    export.add_argument("--json", action="store_true")
    export.set_defaults(func=_cmd_export)

    stats = osub.add_parser("stats", help="Show success rate + token consumption.")
    stats.add_argument("--daemon-url", default="http://localhost:8000")
    stats.add_argument("--json", action="store_true")
    stats.set_defaults(func=_cmd_stats)

    set_out = osub.add_parser("set-outcome", help="Manually set an outcome (operator override).")
    set_out.add_argument("pair_id", help="Pair ID (ORN-...).")
    set_out.add_argument(
        "--status",
        required=True,
        help="One of: pending, applied, succeeded, rejected_by_review, rejected_by_gate, reverted.",
    )
    set_out.add_argument("--details", default=None, help="Free-text note.")
    set_out.add_argument("--daemon-url", default="http://localhost:8000")
    set_out.add_argument("--json", action="store_true")
    set_out.set_defaults(func=_cmd_set_outcome)

    enable = osub.add_parser("enable", help="Set ornith_enabled: true in config.")
    enable.add_argument("--config-dir", default=None)
    enable.add_argument("--json", action="store_true")
    enable.set_defaults(func=_cmd_enable)

    disable = osub.add_parser("disable", help="Set ornith_enabled: false in config.")
    disable.add_argument("--config-dir", default=None)
    disable.add_argument("--json", action="store_true")
    disable.set_defaults(func=_cmd_disable)

    train = osub.add_parser(
        "train",
        aliases=["self-improve"],
        help="Trigger a self-improvement training cycle.",
    )
    train.add_argument("--timeout", type=float, default=300.0)
    train.add_argument("--daemon-url", default="http://localhost:8000")
    train.add_argument("--json", action="store_true")
    train.set_defaults(func=_cmd_train)

    history = osub.add_parser("history", help="Show past self-improvement training cycles.")
    history.add_argument("--limit", type=int, default=20)
    history.add_argument("--daemon-url", default="http://localhost:8000")
    history.add_argument("--json", action="store_true")
    history.set_defaults(func=_cmd_history)

    config_p = osub.add_parser("config", help="View or set Ornith configuration.")
    config_p.set_defaults(func=None)
    config_sub = config_p.add_subparsers(dest="config_command")

    config_get = config_sub.add_parser("get", help="View Ornith configuration.")
    config_get.add_argument("--daemon-url", default="http://localhost:8000")
    config_get.add_argument("--json", action="store_true")
    config_get.set_defaults(func=_cmd_config_get)

    config_set = config_sub.add_parser("set", help="Update Ornith configuration.")
    config_set.add_argument("--model-sha", default=None, dest="model_sha")
    config_set.add_argument("--daemon-url", default="http://localhost:8000")
    config_set.add_argument("--json", action="store_true")
    config_set.set_defaults(func=_cmd_config_set)

    doctor = osub.add_parser("doctor", help="Diagnose ornith readiness.")
    doctor.add_argument("--daemon-url", default="http://localhost:8000")
    doctor.add_argument(
        "--binary-name",
        default="ornith",
        help="Expected binary name on PATH (default: ornith).",
    )
    doctor.add_argument(
        "--expected-model-sha",
        default=None,
        help="Expected model_sha (or set ORNITH_MODEL_SHA env var).",
    )
    doctor.add_argument(
        "--perms-dir",
        default=None,
        help=("Permissions directory to scan for agent:ornith (default: config/permissions)."),
    )
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=_cmd_doctor)
