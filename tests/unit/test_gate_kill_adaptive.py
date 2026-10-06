"""Regression tests for namespace-safe adaptive full-gate termination."""

from __future__ import annotations

import json
import os as os_module
import signal
import subprocess as subprocess_module
from dataclasses import replace
from datetime import datetime
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


def _legacy_lock(root: Path, *, pid: int, started_at: float) -> Path:
    lock = root / ".gate-logs" / "gate-run.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(
        json.dumps({"pid": pid, "started_at": started_at}) + "\n",
        encoding="utf-8",
    )
    (root / ".gate-background.pid").write_text(f"{pid}\n", encoding="utf-8")
    (root / ".gate-status").write_text(f"RUNNING 1 {pid}\n", encoding="utf-8")
    return lock


_LEGACY_OWNER_STARTED_AT = "Mon Oct  5 12:34:56 2026"
_LEGACY_LOCK_STARTED_AT = (
    datetime.strptime(_LEGACY_OWNER_STARTED_AT, "%a %b %d %H:%M:%S %Y").timestamp()
    + 1.0
)


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
        wait=clock.sleep,
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


def test_gate_kill_terminates_exact_legacy_owner_for_same_namespace(
    tmp_path: Path,
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    lock = _legacy_lock(root, pid=100, started_at=_LEGACY_LOCK_STARTED_AT)
    namespace = project_namespace(root)
    live = {
        100: ProcessRecord(
            100,
            1,
            10,
            "make --no-print-directory gate gludd_watchdog_owned_gate=1",
            100,
            _LEGACY_OWNER_STARTED_AT,
            str(root.resolve()),
            namespace,
        ),
        200: ProcessRecord(
            200,
            100,
            9,
            "python -m pytest tests/unit",
            200,
            "Mon Oct  5 12:34:57 2026",
        ),
        999: ProcessRecord(
            999,
            1,
            99,
            "make gate",
            999,
            _LEGACY_OWNER_STARTED_AT,
            str(tmp_path / "other-checkout"),
            "other-project-deadbeef0000",
        ),
    }
    signals: list[tuple[int, signal.Signals]] = []
    clock = _Clock()

    def records() -> list[ProcessRecord]:
        return list(live.values())

    def send(pid: int, signum: signal.Signals) -> None:
        signals.append((pid, signum))
        if signum == signal.SIGTERM and pid == 100:
            live.pop(pid)
        if signum == signal.SIGKILL:
            live.pop(pid)

    result = terminate_owned_gate(
        root,
        apply=True,
        grace_seconds=0.1,
        kill_wait_seconds=0.1,
        poll_seconds=0.05,
        records_reader=records,
        signal_sender=send,
        monotonic=clock.monotonic,
        wait=clock.sleep,
    )

    assert result.success
    assert result.term_pids == (200, 100)
    assert result.kill_pids == (200,)
    assert all(pid != 999 for pid, _signum in signals)
    assert not lock.exists()
    assert "=== GATE: ABORTED ===" in (root / ".gate-status").read_text()
    evidence = json.loads(
        (root / ".gate-logs" / "gate-kill-evidence.json").read_text()
    )
    assert evidence["lock_schema"] == "legacy"
    assert evidence["project_root"] == str(root.resolve())
    assert evidence["project_namespace"] == namespace


@pytest.mark.parametrize(
    ("violation", "reason"),
    [
        ("dead", "legacy gate owner is no longer running"),
        ("start", "legacy gate owner start time mismatch"),
        ("command", "legacy gate owner command mismatch"),
        ("root", "legacy gate owner project root mismatch"),
        ("namespace", "legacy gate owner project namespace mismatch"),
    ],
)
def test_gate_kill_legacy_owner_refuses_every_unproven_identity_dimension(
    tmp_path: Path, violation: str, reason: str
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    lock = _legacy_lock(root, pid=100, started_at=_LEGACY_LOCK_STARTED_AT)
    namespace = project_namespace(root)
    owner = ProcessRecord(
        100,
        1,
        10,
        "make gate",
        100,
        _LEGACY_OWNER_STARTED_AT,
        str(root.resolve()),
        namespace,
    )
    if violation == "dead":
        records: list[ProcessRecord] = []
    else:
        if violation == "start":
            owner = replace(owner, started_at="Tue Oct  6 12:34:56 2026")
        elif violation == "command":
            owner = replace(owner, command="make test gate")
        elif violation == "root":
            owner = replace(owner, cwd=str(tmp_path / "other-checkout"))
        elif violation == "namespace":
            owner = replace(
                owner, project_namespace="other-project-deadbeef0000"
            )
        records = [owner]
    signals: list[tuple[int, signal.Signals]] = []

    result = terminate_owned_gate(
        root,
        apply=True,
        records_reader=lambda: records,
        signal_sender=lambda pid, signum: signals.append((pid, signum)),
    )

    assert not result.success
    assert result.refusal_reason == reason
    assert signals == []
    assert json.loads(lock.read_text()) == {
        "pid": 100,
        "started_at": _LEGACY_LOCK_STARTED_AT,
    }


def test_gate_kill_legacy_schema_fails_closed_when_ambiguous(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    lock = _legacy_lock(root, pid=100, started_at=_LEGACY_LOCK_STARTED_AT)
    payload = json.loads(lock.read_text())
    payload["unexpected"] = "field"
    lock.write_text(json.dumps(payload), encoding="utf-8")
    signals: list[tuple[int, signal.Signals]] = []

    result = terminate_owned_gate(
        root,
        apply=True,
        records_reader=lambda: [
            ProcessRecord(
                100,
                1,
                10,
                "make gate",
                100,
                _LEGACY_OWNER_STARTED_AT,
                str(root.resolve()),
                project_namespace(root),
            )
        ],
        signal_sender=lambda pid, signum: signals.append((pid, signum)),
    )

    assert not result.success
    assert result.refusal_reason == "gate lock marker mismatch"
    assert signals == []


def test_gate_kill_legacy_failure_promotes_retryable_fail_closed_lock(
    tmp_path: Path,
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    lock = _legacy_lock(root, pid=100, started_at=_LEGACY_LOCK_STARTED_AT)
    namespace = project_namespace(root)
    live = {
        100: ProcessRecord(
            100,
            1,
            10,
            "make gate",
            100,
            _LEGACY_OWNER_STARTED_AT,
            str(root.resolve()),
            namespace,
        ),
        200: ProcessRecord(
            200,
            100,
            9,
            "python -m pytest tests/unit",
            200,
            "Mon Oct  5 12:34:57 2026",
        ),
    }
    signals: list[tuple[int, signal.Signals]] = []

    failed = terminate_owned_gate(
        root,
        apply=True,
        grace_seconds=0.0,
        kill_wait_seconds=0.0,
        records_reader=lambda: list(live.values()),
        signal_sender=lambda pid, signum: signals.append((pid, signum)),
        monotonic=lambda: 0.0,
        wait=lambda _seconds: None,
    )

    assert not failed.success
    assert failed.survivor_pids == (100, 200)
    assert signals == [
        (200, signal.SIGTERM),
        (100, signal.SIGTERM),
        (200, signal.SIGKILL),
        (100, signal.SIGKILL),
    ]
    terminal = json.loads(lock.read_text())
    assert terminal["marker"] == GATE_LOCK_MARKER
    assert terminal["state"] == "termination_failed"
    assert terminal["pid_started_at"] == _LEGACY_OWNER_STARTED_AT
    assert terminal["project_root"] == str(root.resolve())
    assert terminal["project_namespace"] == namespace
    assert terminal["survivor_pids"] == [100, 200]
    assert "=== GATE: ABORTED ===" in (root / ".gate-status").read_text()
    evidence = json.loads(
        (root / ".gate-logs" / "gate-kill-evidence.json").read_text()
    )
    assert evidence["outcome"] == "termination_failed"
    assert evidence["lock_schema"] == "legacy"

    def terminate_on_retry(pid: int, _signum: signal.Signals) -> None:
        live.pop(pid)

    retried = terminate_owned_gate(
        root,
        apply=True,
        records_reader=lambda: list(live.values()),
        signal_sender=terminate_on_retry,
    )

    assert retried.success
    assert not lock.exists()


def test_gate_kill_legacy_lock_change_refuses_before_first_signal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    lock = _legacy_lock(root, pid=100, started_at=_LEGACY_LOCK_STARTED_AT)
    owner = ProcessRecord(
        100,
        1,
        10,
        "make gate",
        100,
        _LEGACY_OWNER_STARTED_AT,
        str(root.resolve()),
        project_namespace(root),
    )
    reads = 0
    signals: list[tuple[int, signal.Signals]] = []

    def records() -> list[ProcessRecord]:
        nonlocal reads
        reads += 1
        if reads == 2:
            lock.write_text(
                json.dumps(
                    {"pid": 100, "started_at": _LEGACY_LOCK_STARTED_AT + 0.5}
                ),
                encoding="utf-8",
            )
        return [owner]

    result = terminate_owned_gate(
        root,
        apply=True,
        records_reader=records,
        signal_sender=lambda pid, signum: signals.append((pid, signum)),
    )

    assert not result.success
    assert result.refusal_reason == "legacy gate lock changed during validation"
    assert signals == []


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/usr/bin/make --no-print-directory gate NAME=value", True),
        ("gmake -s gate-refresh", True),
        ("make -C /checkout gate", True),
        ("make -C/checkout gate", True),
        ("make --directory=/checkout gate", True),
        ("make -C", False),
        ("make --directory= gate", False),
        ("make 'unterminated", False),
        ("make test gate", False),
        ("make gate lint", False),
        ("make --file=/tmp/other.mk gate", False),
        ("sh -c 'make gate'", False),
    ],
)
def test_legacy_gate_command_requires_one_exact_gate_target(
    command: str, expected: bool
) -> None:
    assert gate_kill._is_exact_legacy_gate_command(command) is expected


def test_legacy_process_cwd_uses_stable_os_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    assert gate_kill._process_cwd(1) is None

    monkeypatch.setattr(os_module, "readlink", lambda _path: str(root))
    assert gate_kill._process_cwd(100) == root.resolve()

    def missing_proc(_path: str) -> str:
        raise FileNotFoundError

    monkeypatch.setattr(os_module, "readlink", missing_proc)
    monkeypatch.setattr(Path, "is_file", lambda _path: True)
    attempts = iter(
        [
            SimpleNamespace(returncode=1, stdout=""),
            SimpleNamespace(returncode=0, stdout=f"p100\nfcwd\nn{root}\n"),
        ]
    )
    monkeypatch.setattr(
        subprocess_module,
        "run",
        lambda *_args, **_kwargs: next(attempts),
    )

    assert gate_kill._process_cwd(100) == root.resolve()


def test_legacy_process_namespace_reads_proc_and_ps_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _path: b"A=1\0GLUDD_PROJECT_NAMESPACE=checkout-abcd1234\0",
    )
    assert gate_kill._process_namespace_override(100) == (
        True,
        "checkout-abcd1234",
    )

    monkeypatch.setattr(Path, "read_bytes", lambda _path: b"A=1\0")
    assert gate_kill._process_namespace_override(100) == (True, None)

    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _path: (
            b"GLUDD_PROJECT_NAMESPACE=one\0GLUDD_PROJECT_NAMESPACE=two\0"
        ),
    )
    assert gate_kill._process_namespace_override(100) == (False, None)

    def missing_environ(_path: Path) -> bytes:
        raise FileNotFoundError

    monkeypatch.setattr(Path, "read_bytes", missing_environ)
    outputs = iter(
        [
            SimpleNamespace(
                returncode=0,
                stdout=(
                    "make gate PATH=/usr/bin "
                    "GLUDD_PROJECT_NAMESPACE=checkout-abcd1234 A=1"
                ),
            ),
            SimpleNamespace(returncode=0, stdout="make gate PATH=/usr/bin A=1"),
            SimpleNamespace(returncode=0, stdout="make gate"),
            SimpleNamespace(returncode=1, stdout=""),
        ]
    )
    monkeypatch.setattr(
        subprocess_module,
        "run",
        lambda *_args, **_kwargs: next(outputs),
    )

    assert gate_kill._process_namespace_override(100) == (
        True,
        "checkout-abcd1234",
    )
    assert gate_kill._process_namespace_override(100) == (True, None)
    assert gate_kill._process_namespace_override(100) == (False, None)
    assert gate_kill._process_namespace_override(100) == (False, None)


def test_legacy_owner_context_fails_closed_without_root_or_namespace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    owner = ProcessRecord(100, 1, 0, "make gate", 100, _LEGACY_OWNER_STARTED_AT)

    monkeypatch.setattr(gate_kill, "_process_cwd", lambda _pid: root)
    monkeypatch.setattr(
        gate_kill, "_process_namespace_override", lambda _pid: (True, None)
    )
    assert gate_kill._legacy_owner_context(owner) == (
        root.resolve(),
        gate_kill._default_project_namespace(root),
    )

    monkeypatch.setattr(
        gate_kill,
        "_process_namespace_override",
        lambda _pid: (True, "explicit-namespace"),
    )
    assert gate_kill._legacy_owner_context(owner) == (
        root.resolve(),
        "explicit-namespace",
    )

    monkeypatch.setattr(
        gate_kill, "_process_namespace_override", lambda _pid: (False, None)
    )
    assert gate_kill._legacy_owner_context(owner) == (root.resolve(), None)

    invalid = replace(owner, cwd=str(root), project_namespace="../unsafe")
    assert gate_kill._legacy_owner_context(invalid) == (root.resolve(), None)

    monkeypatch.setattr(gate_kill, "_process_cwd", lambda _pid: None)
    assert gate_kill._legacy_owner_context(owner) == (None, None)


@pytest.mark.parametrize(
    ("lock_started_at", "owner_started_at"),
    [
        (True, _LEGACY_OWNER_STARTED_AT),
        ("not-an-epoch", _LEGACY_OWNER_STARTED_AT),
        (float("nan"), _LEGACY_OWNER_STARTED_AT),
        (-1.0, _LEGACY_OWNER_STARTED_AT),
        (_LEGACY_LOCK_STARTED_AT, ""),
        (_LEGACY_LOCK_STARTED_AT, "not-a-ps-start-token"),
        (_LEGACY_LOCK_STARTED_AT + 1_000.0, _LEGACY_OWNER_STARTED_AT),
    ],
)
def test_legacy_start_identity_rejects_ambiguous_values(
    lock_started_at: object, owner_started_at: str
) -> None:
    assert not gate_kill._legacy_started_at_matches(
        lock_started_at, owner_started_at
    )


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
        wait=lambda _seconds: None,
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
        subprocess_module,
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

    def send(pid: int, _signum: signal.Signals) -> None:
        live.pop(pid)

    result = terminate_owned_gate(
        root,
        apply=True,
        records_reader=lambda: list(live.values()),
        signal_sender=send,
    )

    assert result.success
    assert result.term_pids == (100,)
    assert result.kill_pids == ()
    assert not lock.exists()


def test_gate_owner_command_rejects_unparseable_command() -> None:
    assert gate_kill._is_gate_owner_command("'") is False
    assert gate_kill._is_gate_owner_command("python worker.py") is False
    assert gate_kill._is_gate_owner_command("/usr/bin/gmake -s gate-refresh") is True
