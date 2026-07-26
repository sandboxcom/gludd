#!/usr/bin/env python3
"""Repository guard for Codex stop claims.

This is a workflow/CI invariant, not a Codex host hook.  The Codex host cannot
be modified by repository code, so this guard fails closed when tracked work
remains and emits a fresh challenge token for an external runner to audit.
"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

TASK_RE = re.compile(r"^\s*[-*]\s*\[\s\]\s+")
AUDIT_PATH = Path("/tmp/gludd-codex-stop-guard.jsonl")


def pending_tasks(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if TASK_RE.match(line))


def pending_ratchet(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(
        1
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def run(tasks_path: Path, ratchet_path: Path, audit_path: Path = AUDIT_PATH) -> int:
    task_count = pending_tasks(tasks_path)
    ratchet_count = pending_ratchet(ratchet_path)
    token = secrets.token_urlsafe(18)
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "challenge": token,
        "pending_tasks": task_count,
        "pending_ratchet": ratchet_count,
        "codex_host_boundary": "repository guard cannot control Codex host stopping",
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
    print(f"STOP CHALLENGE: {token}")
    print(f"pending TASKS.md items: {task_count}; pending ratchet entries: {ratchet_count}")
    print("Codex host boundary: repository guard cannot control Codex host stopping")
    return 1 if task_count or ratchet_count else 0


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    root = Path(__file__).resolve().parents[1]
    return run(root / "TASKS.md", root / "config" / "ratchet.yml")


if __name__ == "__main__":
    raise SystemExit(main())
