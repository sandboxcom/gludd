#!/usr/bin/env python3
"""Emit cross-terminal evidence for active local work."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
from contextlib import suppress
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from scripts.resource_arbiter import resource_path, resource_root
else:  # pragma: no cover - direct script execution
    _resource_arbiter = import_module("resource_arbiter")
    resource_path = _resource_arbiter.resource_path
    resource_root = _resource_arbiter.resource_root

ROOT = Path(__file__).resolve().parent.parent
TASK_ID_RE = re.compile(r"^\s*-\s*\[ \]\s+([^ —|]+)", re.MULTILINE)
# Keep every externally orchestrated resource visible in one snapshot.  The
# project namespace is represented explicitly so callers can audit that model,
# SearX, and Terraform work do not silently fall back to a global lease.
_RESOURCE_LEASES = ("project", "model", "searx", "terraform", "gate", "async-gate", "e2e")
_WORKER_TASKS = frozenset(
    {
        "gate-refresh",
        "unit-tests",
        "e2e-tests",
        "opencode-e2e",
        "coverage-audit",
        "hook-runtime",
        "e2e-daemon",
        "typecheck",
        "ci-shard-supervisor",
        "test-supervisor",
        "python-worker",
    }
)
_SINGLETON_WORKER_LEASES = frozenset({"gate-refresh"})
_PROCESS_DISPLAY_LIMIT = 512
_COMMAND_DISPLAY_LIMIT = 240
_TRACKED_PROCESS_TOKENS = (
    "adaptive_test.py",
    "agent_watchdog.py",
    "audit_coverage.py",
    "detect-secrets",
    "general_ludd.cli daemon",
    "gunicorn",
    "make gate",
    "multiprocessing",
    "mypy",
    "pytest",
    "run_ci_shards_parallel.py",
    "run_ci_shards_serial.py",
    "run_xdist_trace.py",
    "start_ci_shards_parallel_bg.py",
    "task_watchdog.py",
    "test_hook_runtime.py",
)


def _task_label(command: str) -> str:
    if "task_watchdog.py" in command or "agent_watchdog.py" in command:
        return "watchdog"
    if "audit_coverage.py" in command:
        return "coverage-audit"
    if "detect-secrets" in command:
        return "coverage-audit-support"
    if "multiprocessing" in command:
        return "python-worker"
    if "test_hook_runtime.py" in command:
        return "hook-runtime"
    if any(
        token in command
        for token in (
            "run_ci_shards_parallel.py",
            "run_ci_shards_serial.py",
            "start_ci_shards_parallel_bg.py",
        )
    ):
        return "ci-shard-supervisor"
    if "adaptive_test.py" in command or "run_xdist_trace.py" in command:
        return "test-supervisor"
    if "gate-refresh" in command or "make gate" in command:
        return "gate-refresh"
    if "test_opencode" in command or "tests/e2e/test_opencode" in command:
        return "opencode-e2e"
    if "tests/e2e" in command:
        return "e2e-tests"
    if "tests/unit" in command:
        return "unit-tests"
    if "pytest" in command:
        return "pytest"
    if "mypy" in command:
        return "typecheck"
    if "general_ludd.cli daemon" in command or "gunicorn" in command:
        return "e2e-daemon"
    return "other"


def _parse_worktree_roots(payload: str) -> tuple[Path, ...]:
    """Parse Git's stable porcelain worktree inventory, failing closed."""

    roots: list[Path] = []
    for line in payload.splitlines():
        if not line.startswith("worktree "):
            continue
        raw_path = line.removeprefix("worktree ")
        candidate = Path(raw_path)
        if not raw_path or not candidate.is_absolute():
            raise ValueError("git worktree list returned an invalid worktree path")
        if candidate not in roots:
            roots.append(candidate)
    if not roots:
        raise ValueError("git worktree list returned no registered worktrees")
    return tuple(roots)


def _repository_roots() -> tuple[Path, ...]:
    """Return every checkout registered in this repository's Git common dir."""

    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return _parse_worktree_roots(result.stdout)


def _path_forms(path: Path) -> frozenset[str]:
    """Return textual and canonical spellings for one ownership root."""

    expanded = path.expanduser()
    forms = {str(expanded)}
    with suppress(OSError):
        forms.add(str(expanded.resolve(strict=False)))
    return frozenset(form.rstrip("/") for form in forms if form != "/")


def _command_mentions_path(command: str, path: Path) -> bool:
    """Match a complete path component, never a checkout-name prefix."""

    for form in _path_forms(path):
        pattern = rf"(?<![A-Za-z0-9_./-]){re.escape(form)}(?=$|[\s/'\"=,:])"
        if re.search(pattern, command):
            return True
    return False


def _owned_resource_roots(repository_roots: tuple[Path, ...]) -> tuple[Path, ...]:
    """Derive the resource namespace for each registered checkout."""

    roots: list[Path] = []
    for repository_root in repository_roots:
        candidate = resource_root(repository_root)
        if candidate not in roots:
            roots.append(candidate)
    return tuple(roots)


def _owned_processes_from_output(
    output: str,
    *,
    repository_roots: tuple[Path, ...],
    resource_roots: tuple[Path, ...],
) -> list[dict[str, str]]:
    """Filter a process table to tracked commands with repository ownership."""

    candidates: list[dict[str, str]] = []
    for line in output.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3:
            continue
        pid, ppid, command = fields
        if not any(token in command for token in _TRACKED_PROCESS_TOKENS):
            continue
        candidates.append(
            {"pid": pid, "ppid": ppid, "command": command, "task": _task_label(command)}
        )

    ownership_roots = (*repository_roots, *resource_roots)
    owned_pids = {
        process["pid"]
        for process in candidates
        if any(_command_mentions_path(process["command"], root) for root in ownership_roots)
    }

    # A controller may have a relative argv while its interpreter-backed child
    # carries the checkout path. Keep tracked ancestors and descendants in the
    # same process tree, but never bridge to an unrelated untracked process.
    changed = True
    while changed:
        changed = False
        for process in candidates:
            if process["pid"] in owned_pids:
                continue
            if process["ppid"] in owned_pids or any(
                child["ppid"] == process["pid"] and child["pid"] in owned_pids
                for child in candidates
            ):
                owned_pids.add(process["pid"])
                changed = True

    return [process for process in candidates if process["pid"] in owned_pids]


def _processes() -> list[dict[str, str]]:
    repository_roots = _repository_roots()
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,command="],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return _owned_processes_from_output(
        result.stdout,
        repository_roots=repository_roots,
        resource_roots=_owned_resource_roots(repository_roots),
    )


def _render_process_table(processes: list[dict[str, str]]) -> str:
    """Render a bounded, auditable human-readable process inventory."""

    if not processes:
        return "No matching project processes\n"
    lines = [f"{'PID':>7} {'PPID':>7} {'TASK':<22} COMMAND"]
    displayed = processes[:_PROCESS_DISPLAY_LIMIT]
    for process in displayed:
        command = process["command"]
        if len(command) > _COMMAND_DISPLAY_LIMIT:
            command = f"{command[: _COMMAND_DISPLAY_LIMIT - 3]}..."
        lines.append(
            f"{process['pid']:>7} {process['ppid']:>7} "
            f"{process['task']:<22} {command}"
        )
    remaining = len(processes) - len(displayed)
    if remaining:
        lines.append(f"... {remaining} additional owned processes not displayed")
    return "\n".join(lines) + "\n"


def _workstreams(processes: list[dict[str, str]]) -> dict[str, dict[str, object]]:
    streams: dict[str, dict[str, object]] = {}
    for process in processes:
        task = process["task"]
        stream = streams.setdefault(task, {"process_count": 0, "pids": []})
        process_count = stream["process_count"]
        if not isinstance(process_count, int):
            raise TypeError("workstream process_count must be an integer")
        stream["process_count"] = process_count + 1
        pids = stream["pids"]
        if isinstance(pids, list):
            pids.append(process["pid"])
    return streams


def _git() -> dict[str, str]:
    def run(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()

    return {"branch": run("branch", "--show-current"), "head": run("rev-parse", "HEAD")}


def _gate() -> dict[str, str | bool]:
    status_path = ROOT / ".gate-status"
    status = status_path.read_text(encoding="utf-8") if status_path.is_file() else ""
    if "=== GATE: PASSED ===" in status:
        state = "PASS"
    elif "=== GATE: FAILED ===" in status:
        state = "FAIL"
    else:
        state = "UNKNOWN"
    running_pid = ""
    pid_path = ROOT / ".gate-background.pid"
    if pid_path.is_file():
        candidate = pid_path.read_text(encoding="utf-8").strip()
        try:
            os.kill(int(candidate), 0)
        except (ValueError, OSError):
            # A stale PID file is not evidence of a running gate.
            pass
        else:
            running_pid = candidate
    return {
        "status_file": str(status_path),
        "state": state,
        "running_pid": running_pid,
    }


def _worker_limit() -> int:
    """Read a safe worker ceiling without allowing an unbounded value."""
    raw = os.environ.get("GLUDD_WORKER_LIMIT", "8").strip()
    try:
        configured = int(raw)
    except ValueError:
        configured = 8
    return min(max(configured, 1), 128)


def _active_gate_refresh_owner(_namespace: str) -> str | None:
    """Return the PID holding this project's gate-refresh lease, if any."""

    lock_path = resource_path("gate-refresh", ROOT)
    try:
        with lock_path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.seek(0)
                owner = handle.read().strip()
                if owner.startswith("pid="):
                    owner = owner[4:].strip()
                return owner if owner.isdigit() else None
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        return None
    return None


def _worker_accounting(
    processes: list[dict[str, str]], namespace: str
) -> dict[str, object]:
    """Separate top-level leases from tracked child processes.

    ``ps`` reports every descendant (the gate shell, pytest launcher, and
    xdist workers), but only top-level processes represent an admitted worker
    lease.  Gate refresh is a singleton lease per project; duplicate roots are
    reported and collapsed so a process-tree fan-out cannot inflate admission.
    """

    tracked = [process for process in processes if process["task"] in _WORKER_TASKS]
    tracked_pids = {process["pid"] for process in tracked}
    top_level = [process for process in tracked if process["ppid"] not in tracked_pids]
    gate_roots = [process for process in top_level if process["task"] == "gate-refresh"]
    gate_owner = _active_gate_refresh_owner(namespace)
    reclaimed = [
        process["pid"]
        for process in gate_roots
        if gate_owner is None or process["pid"] != gate_owner
    ]
    top_level = [process for process in top_level if process["pid"] not in reclaimed]
    descendants = len(tracked) - len(top_level) - len(reclaimed)

    leased: list[dict[str, str]] = []
    seen_singletons: set[str] = set()
    duplicates: set[str] = set()
    for process in top_level:
        task = process["task"]
        if task in _SINGLETON_WORKER_LEASES:
            if task in seen_singletons:
                duplicates.add(task)
                continue
            seen_singletons.add(task)
            lease = f"{namespace}:{task}"
        else:
            lease = f"{namespace}:{task}:{process['pid']}"
        leased.append({"pid": process["pid"], "task": task, "lease": lease})

    return {
        "observed_worker_count": len(tracked),
        "top_level_worker_count": len(top_level),
        "descendant_process_count": descendants,
        "leased_worker_count": len(leased),
        "duplicate_worker_leases": sorted(duplicates),
        "reclaimed_worker_pids": sorted(reclaimed),
        "leased_workers": leased,
    }


def _resource_observability(processes: list[dict[str, str]]) -> dict[str, object]:
    """Return project-scoped lease evidence and a bounded worker snapshot."""
    limit = _worker_limit()
    root = resource_root(ROOT)
    accounting = _worker_accounting(processes, root.name)
    leased_worker_count = accounting["leased_worker_count"]
    if not isinstance(leased_worker_count, int):
        raise TypeError("leased_worker_count must be an integer")
    lease_owner = f"pid:{os.getpid()}"
    lease_inventory = [
        {
            "resource": name,
            "path": str(resource_path(name, ROOT)),
            "owner": lease_owner,
        }
        for name in _RESOURCE_LEASES
    ]
    return {
        "project_namespace": root.name,
        "resource_root": str(root),
        "lease_owner": lease_owner,
        "leases": [str(resource_path(name, ROOT)) for name in _RESOURCE_LEASES],
        "lease_inventory": lease_inventory,
        "worker_count": min(leased_worker_count, limit),
        "worker_limit": limit,
        **accounting,
    }


def collect_status() -> dict[str, object]:
    tasks = (ROOT / "TASKS.md").read_text(encoding="utf-8")
    processes = _processes()
    gate = _gate()
    if not gate["running_pid"]:
        live_gate = next((process["pid"] for process in processes if process["task"] == "gate-refresh"), "")
        if live_gate:
            gate["running_pid"] = live_gate
            gate["state"] = "RUNNING"
    return {
        "pid": os.getpid(),
        "processes": processes,
        "workstreams": _workstreams(processes),
        "gate": gate,
        "resource_observability": _resource_observability(processes),
        "git": _git(),
        "open_task_ids": TASK_ID_RE.findall(tasks),
        "audit_contract": {
            "ps_command": "make ps",
            "agent_pids": False,
            "note": "Model-agent execution is reported by agent name/status, never as an OS PID.",
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--process-table",
        action="store_true",
        help="print the repository-owned process inventory instead of JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Emit the JSON status or the shared human-readable process view."""

    args = _parser().parse_args(argv)
    if args.process_table:
        print(_render_process_table(_processes()), end="")
    else:
        print(json.dumps(collect_status(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
