#!/usr/bin/env python3
"""Safely identify and optionally reap stale orphaned project pytest trees."""

from __future__ import annotations

import contextlib
import os
import re
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


def parse_elapsed_seconds(value: str) -> int:
    value = value.strip()
    days = 0
    if "-" in value:
        day_text, value = value.split("-", 1)
        days = int(day_text)
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        hours, minutes, seconds = 0, *parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise ValueError(f"unsupported elapsed time: {value}")
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def is_reapable(record: ProcessRecord, *, project_root: Path, min_age_seconds: int) -> bool:
    root = str(project_root.resolve())
    return (
        record.ppid == 1
        and record.elapsed_seconds >= min_age_seconds
        and root in record.command
        and bool(re.search(r"pytest\s+tests/", record.command))
    )


def reapable_with_descendants(
    record: ProcessRecord,
    records: list[ProcessRecord],
    *,
    project_root: Path,
    min_age_seconds: int,
) -> bool:
    """Accept an orphan launcher when a descendant proves project ownership."""
    if is_reapable(record, project_root=project_root, min_age_seconds=min_age_seconds):
        return True
    children = [child for child in records if child.ppid == record.pid]
    return (
        record.ppid == 1
        and record.elapsed_seconds >= min_age_seconds
        and bool(re.search(r"pytest\s+tests/", record.command))
        and any(
            str(project_root.resolve()) in child.command
            or reapable_with_descendants(
                child, records, project_root=project_root, min_age_seconds=min_age_seconds
            )
            for child in children
        )
    )


def _records(project_root: Path) -> list[ProcessRecord]:
    output = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,etime=,command="],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    records: list[ProcessRecord] = []
    for line in output.splitlines():
        fields = line.strip().split(None, 3)
        if len(fields) != 4:
            continue
        try:
            records.append(ProcessRecord(int(fields[0]), int(fields[1]), parse_elapsed_seconds(fields[2]), fields[3]))
        except (ValueError, IndexError):
            continue
    return records


def reap(project_root: Path, *, min_age_seconds: int, apply: bool) -> int:
    records = _records(project_root)
    candidates = [
        record
        for record in records
        if reapable_with_descendants(
            record, records, project_root=project_root, min_age_seconds=min_age_seconds
        )
    ]
    for record in candidates:
          action = "REAP" if apply else "WOULD-REAP"
          print(f"{action} pid={record.pid} age={record.elapsed_seconds}s command={record.command}")
          if apply:
            with contextlib.suppress(OSError, ProcessLookupError):
                os.killpg(os.getpgid(record.pid), signal.SIGTERM)
    print(f"orphan-pytest candidates={len(candidates)} apply={apply}")
    return 0


if __name__ == "__main__":
    root = Path(os.environ.get("GLUDD_PROJECT_ROOT", Path.cwd())).resolve()
    age = int(os.environ.get("GLUDD_ORPHAN_PYTEST_MIN_SECONDS", "1800"))
    raise SystemExit(reap(root, min_age_seconds=age, apply=os.environ.get("APPLY") == "1"))
