"""Regression coverage for bounded gate log inspection targets."""

from __future__ import annotations

import subprocess
from pathlib import Path

import psutil

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"


def _target_body(name: str) -> str:
    content = MAKEFILE.read_text(encoding="utf-8")
    start = content.index(f"{name}:")
    return content[start:].split("\n\n", 1)[0]


def _gate_tail_pids() -> set[int]:
    matching: set[int] = set()
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            command = " ".join(process.info["cmdline"] or ())
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        if "tail -f" in command and ".gate-logs/gate-" in command:
            matching.add(int(process.info["pid"]))
    return matching


def test_gate_tail_targets_are_bounded_snapshots() -> None:
    for target in ("gate-tail", "gate-lite-tail"):
        body = _target_body(target)
        assert "tail -f" not in body
        assert 'tail -n "$(GATE_TAIL_LINES)"' in body


def test_gate_tail_returns_without_leaking_a_watcher() -> None:
    before = _gate_tail_pids()
    result = subprocess.run(
        ["make", "gate-tail", "GATE_TAIL_LINES=5"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    after = _gate_tail_pids()

    assert result.returncode == 0, result.stdout + result.stderr
    assert after <= before
