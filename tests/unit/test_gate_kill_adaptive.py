"""Regression tests for namespace-safe adaptive full-gate termination."""

from __future__ import annotations

import json
import signal
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import kill_owned_gate as gate_kill
from scripts.kill_owned_gate import (
    GATE_LOCK_MARKER,
    ProcessRecord,
    owned_adaptive_gate_records,
    terminate_owned_gate,
)
from scripts.resource_arbiter import project_namespace


def test_gate_kill_selects_adaptive_full_gate_only() -> None:
    root = Path("/Users/shawnwilson/gludd")
    records = [
        ProcessRecord(
            12327,
            12322,
            42,
            f"{root}/.venv/bin/python3 -m pytest tests/ -q "
            "--cov=general_ludd --cov-report=xml --cov-fail-under=85 "
            f"--basetemp={root}/tmp/gate-JMaR1F -n 1",
        ),
        ProcessRecord(
            43919,
            43913,
            3600,
            f"{root}/.venv/bin/python scripts/audit_coverage.py --source=src/general_ludd",
        ),
        ProcessRecord(
            54293,
            43919,
            120,
            f"{root}/.venv/bin/python -m pytest {root}/tests/e2e/test_config_workflows.py "
            "--cov=src/general_ludd --cov-append",
        ),
    ]

    selected = owned_adaptive_gate_records(records, project_root=root)

    assert [record.pid for record in selected] == [12327]


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _owned_lock(root: Path, *, pid: int, started_at: str) -> Path:
    lock = root / ".gate-logs" / "gate-run.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(
        json.dumps(
            {
                "marker": GATE_LOCK_MARKER,
                "state": "active",
                "pid": pid,
                "pid_started_at": started_at,
                "project_root": str(root.resolve()),
                "project_namespace": project_namespace(root),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (root / ".gate-background.pid").write_text(f"{pid}\n", encoding="utf-8")
    (root / ".gate-status").write_text(f"RUNNING 1 {pid}\n", encoding="utf-8")
    return lock


def test_gate_kill_terminates_owned_tree_across_process_groups(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    lock = _owned_lock(root, pid=100, started_at="root-start")
    live = {
        100: ProcessRecord(100, 1, 10, "make gate gludd_watchdog_owned_gate=1", 100, "root-start"),
        200: ProcessRecord(200, 100, 9, "bash scripts/run_gate.sh", 100, "shell-start"),
        300: ProcessRecord(300, 200, 8, "python scripts/run_ci_shards_serial.py", 300, "runner-start"),
        400: ProcessRecord(400, 300, 7, "python -m pytest tests/unit", 400, "pytest-start"),
        999: ProcessRecord(999, 1, 99, "python unrelated.py", 999, "unrelated-start"),
    }
    signals: list[tuple[int, signal.Signals]] = []
    clock = _Clock()

    def records() -> list[ProcessRecord]:
        return list(live.values())

    def send(pid: int, signum: signal.Signals) -> None:
        signals.append((pid, signum))
        if signum == signal.SIGTERM and pid in {100, 200}:
            live.pop(pid)
        if signum == signal.SIGKILL:
            live.pop(pid)

    result = terminate_owned_gate(
        root,
        apply=True,
        grace_seconds=0.2,
        poll_seconds=0.05,
        records_reader=records,
        signal_sender=send,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    term_pids = [pid for pid, signum in signals if signum == signal.SIGTERM]
    kill_pids = [pid for pid, signum in signals if signum == signal.SIGKILL]
    assert term_pids == [400, 300, 200, 100]
    assert kill_pids == [400, 300]
    assert 999 not in term_pids + kill_pids
    assert result.success
    assert clock.now <= 0.25
    assert not lock.exists()
    assert not (root / ".gate-background.pid").exists()
    assert "=== GATE: ABORTED ===" in (root / ".gate-status").read_text()
    evidence = json.loads((root / ".gate-logs" / "gate-kill-evidence.json").read_text())
    assert evidence["outcome"] == "terminated"
    assert evidence["term_pids"] == [400, 300, 200, 100]
    assert evidence["kill_pids"] == [400, 300]


def test_gate_kill_revalidates_start_time_and_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    lock = _owned_lock(root, pid=100, started_at="root-start")
    initial = [
        ProcessRecord(100, 1, 10, "make gate", 100, "root-start"),
        ProcessRecord(200, 100, 9, "python -m pytest", 200, "old-child-start"),
    ]
    reused = [
        initial[0],
        ProcessRecord(200, 1, 0, "python unrelated.py", 200, "new-child-start"),
    ]
    reads = 0
    signals: list[tuple[int, signal.Signals]] = []

    def records() -> list[ProcessRecord]:
        nonlocal reads
        reads += 1
        return initial if reads == 1 else reused

    result = terminate_owned_gate(
        root,
        apply=True,
        grace_seconds=0.0,
        records_reader=records,
        signal_sender=lambda pid, signum: signals.append((pid, signum)),
        monotonic=lambda: 0.0,
        sleep=lambda _seconds: None,
    )

    assert all(pid != 200 for pid, _signum in signals)
    assert not result.success
    assert result.skipped_pids == (200,)
    terminal_lock = json.loads(lock.read_text(encoding="utf-8"))
    assert terminal_lock["state"] == "termination_failed"
    assert terminal_lock["survivor_pids"] == [100]
    assert "=== GATE: ABORTED ===" in (root / ".gate-status").read_text()


def test_gate_kill_refuses_wrong_project_namespace(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    lock = _owned_lock(root, pid=100, started_at="root-start")
    payload = json.loads(lock.read_text(encoding="utf-8"))
    payload["project_namespace"] = "another-project-deadbeef0000"
    lock.write_text(json.dumps(payload), encoding="utf-8")
    signals: list[tuple[int, signal.Signals]] = []

    result = terminate_owned_gate(
        root,
        apply=True,
        records_reader=lambda: [
            ProcessRecord(100, 1, 10, "make gate", 100, "root-start")
        ],
        signal_sender=lambda pid, signum: signals.append((pid, signum)),
    )

    assert not result.success
    assert result.refusal_reason == "project namespace mismatch"
    assert signals == []


def test_process_snapshot_parses_stable_start_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = (
        " 123 1 123 Mon Oct  5 12:34:56 2026 make gate\n"
        " malformed process row\n"
        " abc 1 2 Mon Oct 5 12:34:56 2026 ignored\n"
    )
    monkeypatch.setattr(
        gate_kill.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=output),
    )

    records = gate_kill._records(tmp_path)

    assert records == [
        ProcessRecord(
            123,
            1,
            0,
            "make gate",
            123,
            "Mon Oct 5 12:34:56 2026",
        )
    ]


@pytest.mark.parametrize(
    ("changes", "owner", "reason"),
    [
        ({"marker": "wrong"}, None, "gate lock marker mismatch"),
        ({"project_root": "/other"}, None, "project root mismatch"),
        ({"state": "idle"}, None, "gate lock state is not active"),
        ({"pid": 101}, None, "gate owner is no longer running"),
        ({"pid_started_at": "wrong"}, None, "gate owner start time mismatch"),
        (
            {},
            ProcessRecord(100, 1, 0, "python unrelated.py", 100, "root-start"),
            "gate owner command mismatch",
        ),
        (
            {"state": "termination_failed"},
            None,
            "terminal gate lock has no revalidated targets",
        ),
    ],
)
def test_gate_ownership_refusal_matrix(
    tmp_path: Path,
    changes: dict[str, object],
    owner: ProcessRecord | None,
    reason: str,
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    lock = _owned_lock(root, pid=100, started_at="root-start")
    payload = json.loads(lock.read_text(encoding="utf-8"))
    payload.update(changes)
    default_owner = ProcessRecord(100, 1, 0, "make gate", 100, "root-start")

    targets, refusal = gate_kill._validate_ownership(
        root, payload, [owner or default_owner]
    )

    assert targets is None
    assert refusal == reason


def test_gate_kill_missing_lock_is_a_safe_noop(tmp_path: Path) -> None:
    result = terminate_owned_gate(
        tmp_path,
        apply=True,
        records_reader=lambda: pytest.fail("process table must not be read"),
    )

    assert result.success
    assert result.refusal_reason == "no gate lock"


def test_gate_kill_dry_run_plans_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    lock = _owned_lock(root, pid=100, started_at="root-start")
    owner = ProcessRecord(100, 1, 0, "make gate", 100, "root-start")

    result = terminate_owned_gate(
        root,
        apply=False,
        records_reader=lambda: [owner],
        signal_sender=lambda _pid, _signum: pytest.fail("dry-run signaled a process"),
    )

    assert result.success
    assert result.term_pids == (100,)
    assert lock.exists()
    assert (root / ".gate-status").read_text() == "RUNNING 1 100\n"


def test_gate_kill_retries_fail_closed_lock_with_exact_identities(
    tmp_path: Path,
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    lock = _owned_lock(root, pid=100, started_at="root-start")
    payload = json.loads(lock.read_text(encoding="utf-8"))
    payload.update(
        {
            "state": "termination_failed",
            "survivor_pids": [100],
            "targets": [
                {
                    "pid": 100,
                    "ppid": 1,
                    "pgid": 100,
                    "started_at": "root-start",
                    "command": "make gate",
                    "depth": 0,
                }
            ],
        }
    )
    lock.write_text(json.dumps(payload), encoding="utf-8")
    live = {100: ProcessRecord(100, 1, 0, "make gate", 100, "root-start")}

    result = terminate_owned_gate(
        root,
        apply=True,
        records_reader=lambda: list(live.values()),
        signal_sender=lambda pid, _signum: live.pop(pid),
    )

    assert result.success
    assert result.term_pids == (100,)
    assert result.kill_pids == ()
    assert not lock.exists()


def test_gate_owner_command_rejects_unparseable_command() -> None:
    assert gate_kill._is_gate_owner_command("'") is False
    assert gate_kill._is_gate_owner_command("python worker.py") is False
    assert gate_kill._is_gate_owner_command("/usr/bin/gmake -s gate-refresh") is True
