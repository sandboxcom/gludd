"""Tests for the guarded orphaned-pytest cleanup helper."""

from __future__ import annotations

import os
from pathlib import Path

from scripts.reap_orphan_pytest import (
    ProcessRecord,
    is_reapable,
    parse_elapsed_seconds,
    reapable_with_descendants,
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
