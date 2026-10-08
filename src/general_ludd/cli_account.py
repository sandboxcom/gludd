"""CLI subcommand: ``gludd account``.

Provides three operations against the daemon's account router:

    gludd account backup USER_ID [--json] [--daemon-url URL]
    gludd account delete  USER_ID [--confirm] [--json] [--daemon-url URL]
    gludd account policy  SERVICE [--json] [--daemon-url URL]

``backup`` triggers ``POST /api/account/backup`` and prints the returned path
(or full JSON payload with --json).
``delete`` triggers ``DELETE /api/account`` and is gated by ``--confirm`` —
without it the command refuses to run locally (and the router refuses anyway).
``policy`` queries ``GET /api/account/policy?service=...`` for the cloud
service's data retention notice.
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
    timeout: float = 30.0,
    ok_codes: tuple[int, ...] = (200, 201, 204),
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
        print(
            f"Error: {resp.status_code} {resp.text}",
            file=sys.stderr,
        )
        sys.exit(1)
    if resp.status_code == 204 or not resp.content:
        return None
    try:
        return resp.json()
    except Exception:
        return resp.text


def _print_json(obj: Any) -> None:
    print(_json.dumps(obj, indent=2, default=str))


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_backup(args: argparse.Namespace) -> None:
    body = _http(
        "POST",
        f"{args.daemon_url}/api/account/backup",
        json_body={"user_id": args.user_id},
    )
    if args.json:
        _print_json(body)
        return
    # body: {user_id, exported_at, todos[], ...}
    if isinstance(body, dict):
        print(f"user_id:     {body.get('user_id')}")
        print(f"exported_at: {body.get('exported_at')}")
        print(f"todos:       {len(body.get('todos', []))}")
        print(f"returns:     {len(body.get('returns', []))}")
        print(f"memory:      {len(body.get('memory', []))}")
        print(f"settings:    {len(body.get('settings', []))}")
    else:
        print(body)


def _cmd_delete(args: argparse.Namespace) -> None:
    if not args.confirm:
        print(
            "Refusing to delete without --confirm. This operation is irreversible.",
            file=sys.stderr,
        )
        sys.exit(2)
    body = _http(
        "DELETE",
        f"{args.daemon_url}/api/account",
        json_body={"user_id": args.user_id, "confirm": True},
    )
    if args.json:
        _print_json(body)
        return
    if isinstance(body, dict):
        print(f"Deleted account for user_id={body.get('user_id')} at {body.get('deleted_at')}")
        print(f"  todos_deleted:                {body.get('todos_deleted')}")
        print(f"  returns_deleted:              {body.get('returns_deleted')}")
        print(f"  memory_deleted:               {body.get('memory_deleted')}")
        print(f"  settings_namespaces_deleted:  {body.get('settings_namespaces_deleted')}")
    else:
        print("account deleted")


def _cmd_policy(args: argparse.Namespace) -> None:
    body = _http(
        "GET",
        f"{args.daemon_url}/api/account/policy",
        params={"service": args.service},
    )
    if args.json:
        _print_json(body)
        return
    if isinstance(body, dict):
        print(f"{body.get('service')}:")
        print(body.get("policy", ""))
    else:
        print(body)


def _cmd_create(args: argparse.Namespace) -> None:
    """``gludd account create`` — provision a (optionally ephemeral) cloud account.

    When ``--ephemeral`` is set, the account is created via the daemon's
    :class:`EphemeralAccountManager` and recorded for auto-cleanup; without
    ``--ephemeral`` the command is a thin alias that POSTs a manual provision
    request to the daemon.
    """
    body = _http(
        "POST",
        f"{args.daemon_url}/api/account/create",
        json_body={
            "provider": args.provider,
            "budget": args.budget,
            "ephemeral": args.ephemeral,
        },
    )
    if args.json:
        _print_json(body)
        return
    if isinstance(body, dict):
        print(f"account_id:    {body.get('account_id')}")
        print(f"provider:      {body.get('provider')}")
        print(f"access_key_id: {body.get('access_key_id')}")
        print(f"budget_limit:  {body.get('budget_limit')}")
        print(f"ephemeral:     {body.get('ephemeral')}")
    else:
        print(body)


def _cmd_cleanup(args: argparse.Namespace) -> None:
    """``gludd account cleanup`` — sweep all ephemeral accounts past retention."""
    body = _http(
        "POST",
        f"{args.daemon_url}/api/account/cleanup",
        json_body={},
    )
    if args.json:
        _print_json(body)
        return
    if isinstance(body, dict):
        deleted = body.get("deleted", [])
        kept = body.get("kept", [])
        print(f"Deleted {len(deleted)} ephemeral account(s):")
        for entry in deleted:
            print(f"  - {entry.get('provider')}/{entry.get('account_id')} deleted={entry.get('deleted')}")
        print(f"Kept {len(kept)} account(s) (within retention).")
    else:
        print(body)


# ---------------------------------------------------------------------------
# Subparser registration
# ---------------------------------------------------------------------------


def add_account_subparser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register the ``account`` command tree (backup/delete/retention) and return its parser."""
    p = sub.add_parser(
        "account",
        help="Account backup, deletion, and cloud-service retention policy",
    )
    p.set_defaults(func=None)
    asub = p.add_subparsers(dest="account_command")

    backup = asub.add_parser("backup", help="Export all user data as a JSON backup")
    backup.add_argument("user_id", help="Account identifier to back up")
    backup.add_argument("--daemon-url", default="http://localhost:8000")
    backup.add_argument("--json", action="store_true")
    backup.set_defaults(func=_cmd_backup)

    delete = asub.add_parser("delete", help="Permanently delete all user data")
    delete.add_argument("user_id", help="Account identifier to delete")
    delete.add_argument(
        "--confirm",
        action="store_true",
        help="Required flag — deletion is irreversible.",
    )
    delete.add_argument("--daemon-url", default="http://localhost:8000")
    delete.add_argument("--json", action="store_true")
    delete.set_defaults(func=_cmd_delete)

    policy = asub.add_parser(
        "policy",
        help="Show a cloud service's data-retention / deletion policy",
    )
    policy.add_argument(
        "service",
        help="Cloud service name (deepseek|openai|zai|aws|gcp|azure)",
    )
    policy.add_argument("--daemon-url", default="http://localhost:8000")
    policy.add_argument("--json", action="store_true")
    policy.set_defaults(func=_cmd_policy)

    create = asub.add_parser(
        "create",
        help="Provision a cloud account (optionally ephemeral)",
    )
    create.add_argument(
        "--provider",
        required=True,
        help="Cloud provider (aws|gcp|azure)",
    )
    create.add_argument(
        "--budget",
        type=float,
        default=10.0,
        help="Budget cap in USD (default 10.0)",
    )
    create.add_argument(
        "--ephemeral",
        action="store_true",
        help="Mark the account ephemeral — auto-deleted after use / retention.",
    )
    create.add_argument("--daemon-url", default="http://localhost:8000")
    create.add_argument("--json", action="store_true")
    create.set_defaults(func=_cmd_create)

    cleanup = asub.add_parser(
        "cleanup",
        help="Sweep all ephemeral accounts past their retention window",
    )
    cleanup.add_argument("--daemon-url", default="http://localhost:8000")
    cleanup.add_argument("--json", action="store_true")
    cleanup.set_defaults(func=_cmd_cleanup)

    return p
