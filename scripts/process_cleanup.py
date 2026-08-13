"""Safe, project-namespaced process discovery and teardown helpers.

The gate, E2E runners, and watchdog can all create process trees.  Cleanup must
not rely on a bare PID because a stale PID file can point at an unrelated
project after PID reuse.  This module keeps the policy small and testable:
parse one process snapshot, verify the namespace, and terminate descendants
before their parent.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessInfo:
    """A process row from ``ps``."""

    pid: int
    ppid: int
    elapsed_secs: float
    command: str


def _parse_elapsed(value: str) -> float:
    value = value.strip()
    try:
        days = 0
        if "-" in value:
            day_text, value = value.split("-", 1)
            days = int(day_text)
        parts = [int(part) for part in value.split(":")]
        if len(parts) == 3:
            hours, minutes, seconds = parts
        elif len(parts) == 2:
            hours, minutes, seconds = 0, *parts
        elif len(parts) == 1:
            return float(parts[0])
        else:
            return 0.0
        return float(days * 86400 + hours * 3600 + minutes * 60 + seconds)
    except (TypeError, ValueError):
        return 0.0


def parse_process_table(output: str) -> dict[int, ProcessInfo]:
    """Parse ``ps -eo pid,ppid,etime,command`` output into process records."""
    records: dict[int, ProcessInfo] = {}
    for line in output.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        records[pid] = ProcessInfo(pid, ppid, _parse_elapsed(parts[2]), parts[3])
    return records


def snapshot_processes() -> dict[int, ProcessInfo]:
    """Return one bounded process snapshot; an unavailable ``ps`` is empty."""
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,ppid,etime,command"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_process_table(result.stdout)


def descendant_processes(
    table: Mapping[int, ProcessInfo], root_pid: int
) -> list[ProcessInfo]:
    """Return descendants in child-before-parent order, excluding the root."""
    children: dict[int, list[ProcessInfo]] = {}
    for process in table.values():
        children.setdefault(process.ppid, []).append(process)
    for items in children.values():
        items.sort(key=lambda item: item.pid)

    ordered: list[ProcessInfo] = []

    def visit(pid: int) -> None:
        for child in children.get(pid, []):
            visit(child.pid)
            ordered.append(child)

    visit(root_pid)
    return ordered


def namespace_matches(command: str, namespace: str) -> bool:
    """Return whether a command belongs to this exact project namespace."""
    marker = str(namespace).strip()
    return bool(marker) and marker in command


def load_lock_owner(path: str | Path, namespace: str) -> int | None:
    """Read a JSON lock owner only when its PID and namespace are valid.

    Legacy/plain PID files intentionally return ``None``.  Treating them as
    stale lets the caller recover instead of trusting a potentially reused PID.
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        pid = payload.get("pid")
        owner_namespace = payload.get("namespace")
        if not isinstance(pid, int) or pid <= 0:
            return None
        if owner_namespace != namespace:
            return None
        return pid
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def terminate_tree(
    table: Mapping[int, ProcessInfo],
    root_pid: int,
    *,
    namespace: str,
    sig: signal.Signals = signal.SIGTERM,
) -> list[int]:
    """Terminate a namespaced process tree, children first.

    Every candidate is checked against the namespace immediately before the
    signal.  A reused PID from another project is skipped and never killed.
    """
    root = table.get(root_pid)
    if root is None or not namespace_matches(root.command, namespace):
        return []
    candidates = [*descendant_processes(table, root_pid), root]
    killed: list[int] = []
    for process in candidates:
        if not namespace_matches(process.command, namespace):
            continue
        try:
            os.kill(process.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            continue
        killed.append(process.pid)
    return killed


def main(argv: list[str] | None = None) -> int:
    """Validate, preview, or apply identity-checked project-tree cleanup."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-pid", type=int, required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    namespace = args.namespace.strip()
    namespace_path = Path(namespace)
    if args.root_pid <= 0 or not namespace_path.is_absolute() or namespace_path == Path("/"):
        parser.error("root PID must be positive and namespace must be a non-root absolute path")
    if args.validate_only:
        print(
            "PROCESS-CLEANUP-VALIDATION PASS "
            f"pid={args.root_pid} namespace={namespace} apply={int(args.apply)}"
        )
        return 0

    table = snapshot_processes()
    root = table.get(args.root_pid)
    if root is None:
        print(f"process not found: pid={args.root_pid}", file=sys.stderr)
        return 2
    if not namespace_matches(root.command, namespace):
        print(
            "namespace mismatch: "
            f"pid={args.root_pid} namespace={namespace} command={root.command}",
            file=sys.stderr,
        )
        return 2

    candidates = [
        process.pid
        for process in [*descendant_processes(table, args.root_pid), root]
        if namespace_matches(process.command, namespace)
    ]
    if not args.apply:
        print(
            "PROCESS-CLEANUP-DRY-RUN "
            f"pid={args.root_pid} namespace={namespace} candidates="
            + ",".join(str(pid) for pid in candidates)
        )
        return 0

    killed = terminate_tree(table, args.root_pid, namespace=namespace)
    print("PROCESS-CLEANUP-APPLIED killed=" + ",".join(str(pid) for pid in killed))
    return 0 if args.root_pid in killed else 1


if __name__ == "__main__":
    raise SystemExit(main())
