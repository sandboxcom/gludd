#!/usr/bin/env python3
"""Emit cross-terminal evidence for active local work."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK_ID_RE = re.compile(r"^\s*-\s*\[ \]\s+([^ —|]+)", re.MULTILINE)


def _task_label(command: str) -> str:
    if "audit_coverage.py" in command:
        return "coverage-audit"
    if "detect-secrets" in command or "multiprocessing" in command:
        return "coverage-audit-support"
    if "test_hook_runtime.py" in command:
        return "hook-runtime"
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


def _processes() -> list[dict[str, str]]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,command="],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    processes: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3:
            continue
        pid, ppid, command = fields
        tracked_tokens = (
            "audit_coverage.py",
            "pytest",
            "make gate",
            "test_hook_runtime.py",
            "mypy",
            "general_ludd.cli daemon",
            "gunicorn",
            "detect-secrets",
            "multiprocessing",
        )
        if any(token in command for token in tracked_tokens):
            processes.append({"pid": pid, "ppid": ppid, "command": command, "task": _task_label(command)})
    return processes


def _workstreams(processes: list[dict[str, str]]) -> dict[str, dict[str, object]]:
    streams: dict[str, dict[str, object]] = {}
    for process in processes:
        task = process["task"]
        stream = streams.setdefault(task, {"process_count": 0, "pids": []})
        stream["process_count"] = int(stream["process_count"]) + 1
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
        "git": _git(),
        "open_task_ids": TASK_ID_RE.findall(tasks),
        "audit_contract": {
            "ps_command": "make ps",
            "agent_pids": False,
            "note": "Model-agent execution is reported by agent name/status, never as an OS PID.",
        },
    }


if __name__ == "__main__":
    print(json.dumps(collect_status(), sort_keys=True))
