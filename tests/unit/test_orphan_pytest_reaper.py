"""Tests for the guarded orphaned-pytest cleanup helper."""

from __future__ import annotations

import os
import signal
from pathlib import Path

import pytest
import scripts.reap_orphan_pytest as reaper
from scripts.reap_orphan_pytest import (
    ProcessRecord,
    is_reapable,
    owner_pids,
    parse_elapsed_seconds,
    reapable_with_descendants,
    select_reapable_records,
)


def test_elapsed_parser_handles_hh_mm_ss_and_days() -> None:
    assert parse_elapsed_seconds("31:11") == 31 * 60 + 11
    assert parse_elapsed_seconds("01:02:03") == 3723
    assert parse_elapsed_seconds("2-03:04:05") == 2 * 86400 + 3 * 3600 + 4 * 60 + 5


def test_reaper_requires_orphan_project_owned_pytest_and_age() -> None:
    record = ProcessRecord(
        pid=os.getpid(),
        ppid=1,
        elapsed_seconds=1900,
        command=f"{Path.cwd()}/.venv/bin/python3 -m pytest tests/unit/ -q",
    )
    assert is_reapable(record, project_root=Path.cwd(), min_age_seconds=1800)


def test_reaper_preserves_live_parent_young_and_unrelated_processes() -> None:
    root = Path.cwd()
    assert not is_reapable(
        ProcessRecord(1, 42, 1900, f"{root}/.venv/bin/python3 -m pytest tests/unit/ -q"),
        project_root=root,
        min_age_seconds=1800,
    )
    assert not is_reapable(
        ProcessRecord(1, 1, 60, f"{root}/.venv/bin/python3 -m pytest tests/unit/ -q"),
        project_root=root,
        min_age_seconds=1800,
    )
    assert not is_reapable(
        ProcessRecord(1, 1, 1900, "/usr/bin/python3 -m pytest tests/unit/ -q"),
        project_root=root,
        min_age_seconds=1800,
    )


def test_orphan_launcher_is_owned_when_project_child_is_present() -> None:
    root = Path.cwd()
    records = [
        ProcessRecord(10, 1, 1900, "uv run python -m pytest tests/unit/ -q"),
        ProcessRecord(11, 10, 1900, f"{root}/.venv/bin/python3 -m pytest tests/unit/ -q"),
    ]
    assert reapable_with_descendants(records[0], records, project_root=root, min_age_seconds=1800)


def test_descendants_are_selected_leaf_first_across_process_groups() -> None:
    root = Path.cwd()
    records = [
        ProcessRecord(10, 1, 1900, f"{root}/.venv/bin/python -m pytest tests/unit/", 10),
        ProcessRecord(11, 10, 1890, f"{root}/.venv/bin/python xdist-worker", 10),
        ProcessRecord(12, 11, 1880, f"{root}/.venv/bin/gunicorn general_ludd.daemon", 12),
        ProcessRecord(13, 12, 1870, f"{root}/.venv/bin/gunicorn worker", 12),
        ProcessRecord(14, 12, 1870, f"{root}/.venv/bin/gunicorn worker", 12),
    ]

    selected = reaper.descendant_records(records[0], records)

    assert [record.pid for record in selected] == [13, 14, 12, 11, 10]


def test_reap_terminates_each_descendant_not_only_the_root_group(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    records = [
        ProcessRecord(10, 1, 1900, f"{root}/.venv/bin/python -m pytest tests/unit/", 10),
        ProcessRecord(11, 10, 1890, f"{root}/.venv/bin/python xdist-worker", 10),
        ProcessRecord(12, 11, 1880, f"{root}/.venv/bin/gunicorn general_ludd.daemon", 12),
        ProcessRecord(13, 12, 1870, f"{root}/.venv/bin/gunicorn worker", 12),
    ]
    terminated: list[int] = []
    monkeypatch.setattr(reaper, "_records", lambda _root: records)
    monkeypatch.setattr(reaper, "owner_pids", lambda _root: set())
    monkeypatch.setattr(reaper, "_alive_pids", lambda _pids: set())
    monkeypatch.setattr("scripts.reap_orphan_pytest.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "scripts.reap_orphan_pytest.os.kill",
        lambda pid, _signal: terminated.append(pid),
    )

    assert reaper.reap(root, min_age_seconds=1800, apply=True) == 0

    assert terminated == [13, 12, 11, 10]
    assert "orphan-pytest candidates=1 apply=True" in capsys.readouterr().out


def test_reap_escalates_term_resistant_descendant_and_verifies_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A stale collection worker that ignores SIGTERM must not survive cleanup."""
    root = tmp_path / "checkout"
    root.mkdir()
    records = [
        ProcessRecord(20, 1, 1900, f"{root}/.venv/bin/python -m pytest tests/ --co -q", 20),
        ProcessRecord(21, 20, 1890, f"{root}/.venv/bin/python -m pytest tests/ --co -q", 20),
    ]
    sent: list[tuple[int, int]] = []
    alive_checks = iter(({20, 21}, set()))

    monkeypatch.setattr(reaper, "_records", lambda _root: records)
    monkeypatch.setattr(reaper, "owner_pids", lambda _root: set())
    monkeypatch.setattr(reaper, "_alive_pids", lambda _pids: next(alive_checks))
    monkeypatch.setattr("scripts.reap_orphan_pytest.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "scripts.reap_orphan_pytest.os.kill",
        lambda pid, sig: sent.append((pid, sig)),
    )

    assert reaper.reap(root, min_age_seconds=1800, apply=True) == 0

    assert sent == [
        (21, signal.SIGTERM),
        (20, signal.SIGTERM),
        (21, signal.SIGKILL),
        (20, signal.SIGKILL),
    ]
    output = capsys.readouterr().out
    assert "orphan-pytest escalated=2" in output
    assert "orphan-pytest survivors=0" in output


def test_reaper_preserves_orphans_while_live_gate_owner_exists() -> None:
    """A live namespaced gate owner protects its orphaned pytest descendants."""
    root = Path.cwd()
    records = [
        ProcessRecord(10, 1, 1320, f"{root}/scripts/run_gate.sh"),
        ProcessRecord(11, 1, 600, f"{root}/.venv/bin/python -m pytest tests/e2e/ -q"),
        ProcessRecord(12, 11, 500, f"{root}/.venv/bin/python -m pytest tests/e2e/ -q"),
    ]

    selected = select_reapable_records(
        records,
        project_root=root,
        min_age_seconds=1800,
        owner_pids={10},
    )

    assert selected == []


def test_reaper_ignores_stale_status_pid_with_unrelated_command() -> None:
    """A reused status PID must not suppress cleanup of an orphan tree."""
    root = Path.cwd()
    records = [
        ProcessRecord(99, 1, 60, "/usr/bin/python -m unrelated_worker"),
        ProcessRecord(100, 1, 3600, f"{root}/.venv/bin/python -m pytest tests/e2e/ -q"),
    ]

    selected = select_reapable_records(
        records,
        project_root=root,
        min_age_seconds=1800,
        owner_pids={99},
    )

    assert [record.pid for record in selected] == [100]


def test_reaper_selects_old_unrelated_tree_with_recent_live_gate() -> None:
    """A live gate protects only orphan trees that could be its descendants."""
    root = Path.cwd()
    records = [
        ProcessRecord(10, 1, 1320, f"{root}/scripts/run_gate.sh"),
        ProcessRecord(11, 1, 3600, f"{root}/.venv/bin/python -m pytest tests/unit/ -q"),
    ]

    selected = select_reapable_records(
        records,
        project_root=root,
        min_age_seconds=1800,
        owner_pids={10},
    )

    assert [record.pid for record in selected] == [11]


def test_owner_claims_are_scoped_to_gate_state_and_resource_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    resource_root = tmp_path / "resources"
    monkeypatch.setenv("GLUDD_RESOURCE_ROOT", str(resource_root))
    from scripts.resource_arbiter import resource_path

    resource_path("gate", root).parent.mkdir(parents=True)
    resource_path("gate", root).write_text("41\n", encoding="utf-8")
    (root / ".gate-status").write_text("RUNNING 123 42\n", encoding="utf-8")

    assert owner_pids(root) == {41, 42}
