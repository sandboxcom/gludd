"""CLI subcommand: ``gludd self-improve``.

The human approval gate for self-authored self-improve todos. Admitted
self-improve todos are always parked in ``APPROVAL_REQUIRED`` (auto_queue was
removed as a C13 bypass). These commands are the operator-facing release path —
they call the daemon's ``/admin/self-improve/approvals`` routes (which persist
through the ``SelfImproveApprovalManager`` + ``TodoRepository``):

* ``gludd self-improve pending``           — list todos awaiting review
* ``gludd self-improve approve <todo_id>``  — release APPROVAL_REQUIRED -> QUEUED
* ``gludd self-improve reject <todo_id>``   — retire  APPROVAL_REQUIRED -> CANCELLED

Write operations send ``GLUDD_AUTH_PSK`` as a Bearer token so the daemon's PSK
middleware authenticates them.
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


def _cmd_pending(args: argparse.Namespace) -> None:
    data = _http("GET", f"{args.daemon_url}/admin/self-improve/approvals") or {}
    rows = data.get("pending", []) if isinstance(data, dict) else []
    if args.json:
        _print_json(data)
        return
    if not rows:
        print("(no self-improve todos awaiting approval)")
        return
    print(f"{'TODO_ID':<16} {'PRIORITY':<9} {'TITLE'}")
    print(f"{'-' * 16} {'-' * 9} {'-' * 40}")
    for r in rows:
        print(f"{r.get('todo_id', '')!s:<16} {r.get('priority', '')!s:<9} {str(r.get('title', ''))[:60]}")


def _cmd_approve(args: argparse.Namespace) -> None:
    data = _http(
        "POST",
        f"{args.daemon_url}/admin/self-improve/approvals/{args.todo_id}/approve",
    )
    if args.json:
        _print_json(data)
    else:
        print(f"Approved (queued): {args.todo_id}")


def _cmd_reject(args: argparse.Namespace) -> None:
    data = _http(
        "POST",
        f"{args.daemon_url}/admin/self-improve/approvals/{args.todo_id}/reject",
        json_body={"reason": args.reason} if args.reason else {},
    )
    if args.json:
        _print_json(data)
    else:
        print(f"Rejected (cancelled): {args.todo_id}")


def add_self_improve_subparser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``self-improve`` subcommand tree for reviewing self-improve todos."""
    p = sub.add_parser("self-improve", help="Review + release self-authored self-improve todos")
    p.set_defaults(func=None)
    ssub = p.add_subparsers(dest="self_improve_command")

    pend = ssub.add_parser("pending", help="List self-improve todos awaiting approval")
    pend.add_argument("--daemon-url", default="http://localhost:8000")
    pend.add_argument("--json", action="store_true")
    pend.set_defaults(func=_cmd_pending)

    appr = ssub.add_parser("approve", help="Approve a held self-improve todo (-> queued)")
    appr.add_argument("todo_id")
    appr.add_argument("--daemon-url", default="http://localhost:8000")
    appr.add_argument("--json", action="store_true")
    appr.set_defaults(func=_cmd_approve)

    rej = ssub.add_parser("reject", help="Reject a held self-improve todo (-> cancelled)")
    rej.add_argument("todo_id")
    rej.add_argument("--reason", default=None)
    rej.add_argument("--daemon-url", default="http://localhost:8000")
    rej.add_argument("--json", action="store_true")
    rej.set_defaults(func=_cmd_reject)
