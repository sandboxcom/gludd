#!/usr/bin/env python3
"""Repository guard for Codex stop claims.

This is a workflow/CI invariant, not a Codex host hook.  The Codex host cannot
be modified by repository code, so this guard fails closed when tracked work
remains and emits a fresh challenge token for an external runner to audit.
"""

from __future__ import annotations

import json
import secrets
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from scripts.task_scope import task_inventory as _task_inventory
else:
    _task_scope = import_module("task" + "_scope")
    _task_inventory = _task_scope.task_inventory

AUDIT_PATH = Path("/tmp/gludd-codex-stop-guard.jsonl")
STATE_PATH = Path("/tmp/gludd-codex-stop-guard.state")


def pending_tasks(path: Path) -> int:
    """Return only tasks owned by the declared active milestone."""
    inventory = _task_inventory(path.read_text(encoding="utf-8"))
    return inventory["task_scope"]["open_count"]


def pending_task_scope(path: Path) -> tuple[int, int, str]:
    """Return active, excluded-backlog, and milestone ownership values."""
    inventory = _task_inventory(path.read_text(encoding="utf-8"))
    scope = inventory["task_scope"]
    return scope["open_count"], scope["backlog_open_count"], scope["label"]


def pending_ratchet(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(
        1
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def run(
    tasks_path: Path,
    ratchet_path: Path,
    audit_path: Path = AUDIT_PATH,
    state_path: Path = STATE_PATH,
) -> int:
    task_count, backlog_count, scope_label = pending_task_scope(tasks_path)
    ratchet_count = pending_ratchet(ratchet_path)
    token = secrets.token_urlsafe(18)
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "challenge": token,
        "pending_tasks": task_count,
        "excluded_backlog_tasks": backlog_count,
        "task_scope": scope_label or "repository",
        "pending_ratchet": ratchet_count,
        "codex_host_boundary": "repository guard cannot control Codex host stopping",
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
    state_path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    print(f"STOP CHALLENGE: {token}")
    if scope_label:
        print(
            f"active {scope_label} TASKS.md items: {task_count}; "
            f"backlog items excluded from stop gate: {backlog_count}; "
            f"pending ratchet entries: {ratchet_count}"
        )
    else:
        print(
            f"pending TASKS.md items: {task_count}; "
            f"pending ratchet entries: {ratchet_count}"
        )
    print("Codex host boundary: repository guard cannot control Codex host stopping")
    return 1 if task_count or ratchet_count else 0


def confirm(token: str, state_path: Path = STATE_PATH, audit_path: Path = AUDIT_PATH) -> int:
    """Accept exactly one freshly issued token, only when the ledger is clean."""
    if not token or not state_path.exists():
        print("STOP CONFIRMATION: rejected (missing challenge)")
        return 1
    try:
        record = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        print("STOP CONFIRMATION: rejected (corrupt challenge state)")
        return 1
    accepted = secrets.compare_digest(token, str(record.get("challenge", ""))) and not (
        record.get("pending_tasks") or record.get("pending_ratchet")
    )
    event = {
        "timestamp": datetime.now(UTC).isoformat(),
        "challenge": token,
        "confirmed": accepted,
    }
    with audit_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, sort_keys=True) + "\n")
    if accepted:
        state_path.unlink(missing_ok=True)
        print("STOP CONFIRMATION: accepted")
        return 0
    print("STOP CONFIRMATION: rejected")
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    root = Path(__file__).resolve().parents[1]
    if argv[:1] == ["--confirm"]:
        return confirm(argv[1] if len(argv) > 1 else "")
    return run(root / "TASKS.md", root / "config" / "ratchet.yml")


if __name__ == "__main__":
    raise SystemExit(main())
