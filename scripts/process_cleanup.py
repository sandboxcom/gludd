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
import re
import shlex
import signal
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

_MAX_CWD_SNAPSHOT_PIDS = 256


@dataclass(frozen=True)
class ProcessInfo:
    """A process row from ``ps``."""

    pid: int
    ppid: int
    elapsed_secs: float
    command: str
    cwd: str | None = None


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


def snapshot_processes(root_pid: int | None = None) -> dict[int, ProcessInfo]:
    """Return one bounded process snapshot, optionally including one tree's cwd."""
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
    table = parse_process_table(result.stdout)
    if root_pid is None or root_pid not in table:
        return table

    tree = [*descendant_processes(table, root_pid), table[root_pid]]
    cwds = _snapshot_process_cwds([process.pid for process in tree])
    for pid, cwd in cwds.items():
        process = table.get(pid)
        if process is not None:
            table[pid] = replace(process, cwd=cwd)
    return table


def _snapshot_process_cwds(pids: list[int]) -> dict[int, str]:
    """Capture cwd evidence for a bounded PID set, failing closed per PID."""
    unique_pids = list(dict.fromkeys(pid for pid in pids if pid > 0))
    bounded_pids = unique_pids[-_MAX_CWD_SNAPSHOT_PIDS:]
    cwds: dict[int, str] = {}
    unresolved: list[int] = []
    for pid in bounded_pids:
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            unresolved.append(pid)
            continue
        if Path(cwd).is_absolute():
            cwds[pid] = cwd

    if not unresolved:
        return cwds
    try:
        result = subprocess.run(
            [
                "lsof",
                "-a",
                "-p",
                ",".join(str(pid) for pid in unresolved),
                "-d",
                "cwd",
                "-Fn",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return cwds

    current_pid: int | None = None
    for line in result.stdout.splitlines():
        if line.startswith("p"):
            try:
                current_pid = int(line[1:])
            except ValueError:
                current_pid = None
        elif line.startswith("n") and current_pid in unresolved:
            cwd = line[1:]
            if Path(cwd).is_absolute():
                cwds[current_pid] = cwd
    return cwds


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
    if not marker:
        return False
    try:
        resolved_marker = str(Path(marker).resolve(strict=False))
    except (OSError, RuntimeError):
        resolved_marker = marker
    for candidate in {marker, resolved_marker}:
        pattern = rf"(?<![A-Za-z0-9_.-]){re.escape(candidate)}(?=$|[/\s'\"=:,;)])"
        if re.search(pattern, command):
            return True
    return False


def _cwd_matches_namespace(cwd: str | None, namespace: str) -> bool:
    """Return whether cwd is the namespace or one of its descendants."""
    if cwd is None:
        return False
    try:
        cwd_path = Path(cwd).resolve(strict=False)
        namespace_path = Path(namespace).resolve(strict=False)
        cwd_path.relative_to(namespace_path)
    except (OSError, RuntimeError, ValueError):
        return False
    return True


def _process_matches_namespace(process: ProcessInfo, namespace: str) -> bool:
    """Match a process using command-path or live-cwd namespace evidence."""
    return namespace_matches(process.command, namespace) or _cwd_matches_namespace(
        process.cwd, namespace
    )


def _is_make_process(command: str) -> bool:
    """Recognize the narrow parent launcher allowed to use descendant proof."""
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    return bool(words) and Path(words[0]).name in {"make", "gmake"}


def process_group_alive(process_group_id: int) -> bool:
    """Return whether an owned POSIX process group still has a live member."""
    if process_group_id <= 1 or os.name != "posix":
        return False
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # The group exists, even though this owner can no longer signal it.
        return True
    except OSError:
        return False
    return True


def signal_process_group(process_group_id: int, sig: signal.Signals) -> bool:
    """Signal one explicitly owned POSIX process group, containing races."""
    if process_group_id <= 1 or os.name != "posix":
        return False
    try:
        os.killpg(process_group_id, sig)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


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

    The supplied table is the bounded identity snapshot used for admission.
    A reused PID whose current identity belongs elsewhere is never selected.
    """
    candidates = namespaced_process_tree(table, root_pid, namespace=namespace)
    return _terminate_processes(candidates, sig=sig)


def namespaced_process_tree(
    table: Mapping[int, ProcessInfo], root_pid: int, *, namespace: str
) -> list[ProcessInfo]:
    """Select one tree only when its root has fail-closed ownership proof."""
    root = table.get(root_pid)
    if root is None:
        return []
    descendants = descendant_processes(table, root_pid)
    matching_descendants = [
        process
        for process in descendants
        if _process_matches_namespace(process, namespace)
    ]
    root_command_matches = namespace_matches(root.command, namespace)
    make_identity_proof = _is_make_process(root.command) and (
        _cwd_matches_namespace(root.cwd, namespace) or bool(matching_descendants)
    )
    if not root_command_matches and not make_identity_proof:
        return []
    return [*matching_descendants, root]


def _terminate_processes(
    candidates: list[ProcessInfo], *, sig: signal.Signals
) -> list[int]:
    """Signal a previously admitted child-before-parent process selection."""
    killed: list[int] = []
    for process in candidates:
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

    table = snapshot_processes(args.root_pid)
    root = table.get(args.root_pid)
    if root is None:
        print(f"process not found: pid={args.root_pid}", file=sys.stderr)
        return 2
    candidates = namespaced_process_tree(table, args.root_pid, namespace=namespace)
    if not candidates:
        print(
            "namespace mismatch: "
            f"pid={args.root_pid} namespace={namespace} command={root.command}",
            file=sys.stderr,
        )
        return 2

    candidate_pids = [process.pid for process in candidates]
    if args.validate_only:
        print(
            "PROCESS-CLEANUP-VALIDATION PASS "
            f"pid={args.root_pid} namespace={namespace} apply={int(args.apply)} "
            "candidates=" + ",".join(str(pid) for pid in candidate_pids)
        )
        return 0

    if not args.apply:
        print(
            "PROCESS-CLEANUP-DRY-RUN "
            f"pid={args.root_pid} namespace={namespace} candidates="
            + ",".join(str(pid) for pid in candidate_pids)
        )
        return 0

    killed = _terminate_processes(candidates, sig=signal.SIGTERM)
    print("PROCESS-CLEANUP-APPLIED killed=" + ",".join(str(pid) for pid in killed))
    return 0 if args.root_pid in killed else 1


if __name__ == "__main__":
    raise SystemExit(main())
