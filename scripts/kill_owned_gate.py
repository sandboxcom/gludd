#!/usr/bin/env python3
"""Terminate only this checkout's adaptive full-gate process groups."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    ppid: int
    elapsed_seconds: int
    command: str
    pgid: int | None = None


def _is_owned_adaptive_gate(record: ProcessRecord, project_root: Path) -> bool:
    command = record.command
    return (
        "adaptive_test.py" in command
        and " tests/" in command
        and "--cov-fail-under=85" in command
        and str(project_root.resolve()) in command
    )


def owned_adaptive_gate_records(
    records: list[ProcessRecord], *, project_root: Path
) -> list[ProcessRecord]:
    """Return adaptive full-gate workers, excluding coverage/E2E invocations."""
    return [record for record in records if _is_owned_adaptive_gate(record, project_root)]


def _records(project_root: Path) -> list[ProcessRecord]:
    output = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,pgid=,etime=,command="],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    records: list[ProcessRecord] = []
    for line in output.splitlines():
        fields = line.strip().split(None, 4)
        if len(fields) != 5:
            continue
        try:
            records.append(
                ProcessRecord(
                    pid=int(fields[0]),
                    ppid=int(fields[1]),
                    elapsed_seconds=0,
                    command=fields[4],
                    pgid=int(fields[2]),
                )
            )
        except ValueError:
            continue
    return records


def kill_owned_gates(project_root: Path, *, apply: bool) -> int:
    records = _records(project_root)
    candidates = owned_adaptive_gate_records(records, project_root=project_root)
    groups = {record.pgid or record.pid for record in candidates}
    for group in sorted(groups):
        action = "KILL" if apply else "WOULD-KILL"
        print(f"{action} adaptive gate process-group={group}")
        if apply:
            with contextlib.suppress(OSError, ProcessLookupError):
                os.killpg(group, signal.SIGTERM)
    print(f"adaptive-gate candidates={len(candidates)} apply={apply}")
    return 0


if __name__ == "__main__":
    root = Path(os.environ.get("GLUDD_PROJECT_ROOT", Path.cwd())).resolve()
    raise SystemExit(kill_owned_gates(root, apply=os.environ.get("APPLY") == "1"))
