#!/usr/bin/env python3
"""Codex Stop hook: continue work while the repository has tracked work."""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path
from typing import Any


def _root_from_event(event: dict[str, Any]) -> Path:
    candidate = Path(str(event.get("cwd") or Path.cwd())).resolve()
    for root in (candidate, *candidate.parents):
        if (root / "TASKS.md").exists():
            return root
    return candidate


def _pending(root: Path) -> tuple[int, int]:
    tasks = root / "TASKS.md"
    task_count = sum(
        1
        for line in tasks.read_text(encoding="utf-8").splitlines()
        if line.lstrip().startswith(("- [ ]", "* [ ]"))
    ) if tasks.exists() else 0
    ratchet = root / "config" / "ratchet.yml"
    ratchet_count = sum(
        1
        for line in ratchet.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ) if ratchet.exists() else 0
    return task_count, ratchet_count


def handle(event: dict[str, Any]) -> dict[str, Any]:
    root = _root_from_event(event)
    task_count, ratchet_count = _pending(root)
    if not task_count and not ratchet_count:
        return {"continue": True}
    token = secrets.token_urlsafe(18)
    active = bool(event.get("stop_hook_active"))
    attempt = "continuation stop attempt" if active else "stop attempt"
    reason = (
        f"STOP CHALLENGE: {token}. This is Codex {attempt}; "
        f"{task_count} TASKS.md item(s) and {ratchet_count} ratchet entry(ies) remain. "
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
