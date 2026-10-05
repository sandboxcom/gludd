#!/usr/bin/env python3
"""Codex Stop hook: continue work while the repository has tracked work."""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path
from typing import Any

from scripts.task_scope import task_inventory


def _root_from_event(event: dict[str, Any]) -> Path:
    candidate = Path(str(event.get("cwd") or Path.cwd())).resolve()
    for root in (candidate, *candidate.parents):
        if (root / "TASKS.md").exists():
            return root
    return candidate


def _pending(root: Path) -> tuple[int, int, int, str]:
    tasks = root / "TASKS.md"
    task_count = 0
    backlog_count = 0
    scope_label = ""
    if tasks.exists():
        inventory = task_inventory(tasks.read_text(encoding="utf-8"))
        scope = inventory.get("task_scope")
        if not isinstance(scope, dict):
            raise ValueError("task inventory did not return a scope")
        open_count = scope.get("open_count")
        backlog_open_count = scope.get("backlog_open_count")
        label = scope.get("label")
        if not isinstance(open_count, int) or not isinstance(backlog_open_count, int):
            raise ValueError("task inventory returned invalid counts")
        if not isinstance(label, str):
            raise ValueError("task inventory returned an invalid label")
        task_count = open_count
        backlog_count = backlog_open_count
        scope_label = label
    ratchet = root / "config" / "ratchet.yml"
    ratchet_count = sum(
        1
        for line in ratchet.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ) if ratchet.exists() else 0
    return task_count, backlog_count, ratchet_count, scope_label


def handle(event: dict[str, Any]) -> dict[str, Any]:
    root = _root_from_event(event)
    task_count, backlog_count, ratchet_count, scope_label = _pending(root)
    if not task_count and not ratchet_count:
        return {"continue": True}
    token = secrets.token_urlsafe(18)
    active = bool(event.get("stop_hook_active"))
    attempt = "continuation stop attempt" if active else "stop attempt"
    if scope_label:
        task_summary = f"{task_count} active {scope_label} TASKS.md item(s)"
        if backlog_count:
            task_summary += (
                f", {backlog_count} backlog item(s) excluded from the stop gate"
            )
    else:
        task_summary = f"{task_count} TASKS.md item(s)"
    reason = (
        f"STOP CHALLENGE: {token}. This is Codex {attempt}; "
        f"{task_summary} and {ratchet_count} ratchet entry(ies) remain. "
        "Continue the event loop, execute the next tracked task, and re-check the gate."
    )
    return {"decision": "block", "reason": reason}


def main() -> int:
    try:
        event = json.load(sys.stdin)
        response = handle(event)
    except (OSError, TypeError, ValueError) as exc:
        print(json.dumps({"decision": "block", "reason": f"Codex stop hook error: {exc}"}))
        return 0
    print(json.dumps(response, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
