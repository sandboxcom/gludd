"""Regression tests for namespaced process cleanup and stale lock recovery."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from scripts.process_cleanup import (
    ProcessInfo,
    descendant_processes,
    load_lock_owner,
    namespace_matches,
    parse_process_table,
    terminate_tree,
)


def test_parse_process_table_keeps_command_with_spaces() -> None:
    table = parse_process_table(
        "  PID  PPID ELAPSED COMMAND\n"
        "101  99 00:30 /tmp/gludd-alpha/pytest -q tests\n"
    )
    assert table[101] == ProcessInfo(
        pid=101,
        ppid=99,
        elapsed_secs=30,
        command="/tmp/gludd-alpha/pytest -q tests",
    )


def test_descendant_processes_are_ordered_children_first() -> None:
    table = {
        10: ProcessInfo(10, 1, 900, "/tmp/gludd-a/run"),
        11: ProcessInfo(11, 10, 800, "/tmp/gludd-a/worker"),
        12: ProcessInfo(12, 11, 700, "/tmp/gludd-a/leaf"),
        20: ProcessInfo(20, 1, 900, "/tmp/gludd-b/other"),
    }
    assert [item.pid for item in descendant_processes(table, 10)] == [12, 11]


def test_namespace_matches_rejects_other_projects() -> None:
    assert namespace_matches("python /tmp/gludd-alpha/test.py", "/tmp/gludd-alpha")
    assert not namespace_matches("python /tmp/gludd-beta/test.py", "/tmp/gludd-alpha")


def test_load_lock_owner_rejects_malformed_or_wrong_namespace(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    lock.write_text(json.dumps({"pid": 123, "namespace": "gludd-beta"}))
    assert load_lock_owner(lock, namespace="gludd-alpha") is None
    lock.write_text("not-json")
    assert load_lock_owner(lock, namespace="gludd-alpha") is None


def test_terminate_tree_checks_identity_and_kills_children_first() -> None:
    table = {
        10: ProcessInfo(10, 1, 900, "/tmp/gludd-alpha/run"),
        11: ProcessInfo(11, 10, 800, "/tmp/gludd-alpha/worker"),
    }
    with patch("scripts.process_cleanup.os.kill") as kill:
        assert terminate_tree(table, 10, namespace="/tmp/gludd-alpha") == [11, 10]
    assert [call.args[0] for call in kill.call_args_list] == [11, 10]

