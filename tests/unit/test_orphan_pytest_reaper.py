"""Tests for the guarded orphaned-pytest cleanup helper."""

from __future__ import annotations

import os
from pathlib import Path

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


def test_owner_claims_are_scoped_to_gate_state_and_resource_namespace(tmp_path, monkeypatch) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    resource_root = tmp_path / "resources"
    monkeypatch.setenv("GLUDD_RESOURCE_ROOT", str(resource_root))
    from scripts.resource_arbiter import resource_path

    resource_path("gate", root).parent.mkdir(parents=True)
    resource_path("gate", root).write_text("41\n", encoding="utf-8")
    (root / ".gate-status").write_text("RUNNING 123 42\n", encoding="utf-8")

    assert owner_pids(root) == {41, 42}
