"""Regression tests for namespaced process cleanup and stale lock recovery."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from scripts import process_cleanup
from scripts.process_cleanup import (
    ProcessInfo,
    _parse_elapsed,
    descendant_processes,
    load_lock_owner,
    namespace_matches,
    parse_process_table,
    snapshot_processes,
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


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1-02:03:04", 93784), ("12:34", 754), ("17", 17), ("", 0)],
)
def test_elapsed_parser_supports_ps_formats(value: str, expected: float) -> None:
    assert _parse_elapsed(value) == expected


def test_elapsed_parser_rejects_malformed_values() -> None:
    assert _parse_elapsed("not-a-duration") == 0
    assert _parse_elapsed("1:2:3:4") == 0


def test_parse_process_table_skips_headers_short_and_bad_rows() -> None:
    table = parse_process_table(
        "PID PPID ELAPSED COMMAND\n"
        "short\n"
        "bad parent 00:01 command\n"
    )
    assert table == {}


def test_snapshot_processes_handles_ps_failure() -> None:
    with patch("scripts.process_cleanup.subprocess.run", side_effect=OSError):
        assert snapshot_processes() == {}


def test_snapshot_processes_parses_ps_output() -> None:
    result = type("Result", (), {"stdout": "1 0 00:01 /tmp/gludd-a/run\n"})()
    with patch("scripts.process_cleanup.subprocess.run", return_value=result):
        assert snapshot_processes()[1].command.endswith("/tmp/gludd-a/run")


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


def test_load_lock_owner_accepts_matching_owner(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    lock.write_text(json.dumps({"pid": 123, "namespace": "gludd-alpha"}))
    assert load_lock_owner(lock, namespace="gludd-alpha") == 123


def test_load_lock_owner_rejects_nonpositive_pid(tmp_path: Path) -> None:
    lock = tmp_path / "lock"
    lock.write_text(json.dumps({"pid": 0, "namespace": "gludd-alpha"}))
    assert load_lock_owner(lock, namespace="gludd-alpha") is None


def test_terminate_tree_checks_identity_and_kills_children_first() -> None:
    table = {
        10: ProcessInfo(10, 1, 900, "/tmp/gludd-alpha/run"),
        11: ProcessInfo(11, 10, 800, "/tmp/gludd-alpha/worker"),
    }
    with patch("scripts.process_cleanup.os.kill") as kill:
        assert terminate_tree(table, 10, namespace="/tmp/gludd-alpha") == [11, 10]
    assert [call.args[0] for call in kill.call_args_list] == [11, 10]


def test_terminate_tree_skips_other_namespace_and_missing_root() -> None:
    table = {10: ProcessInfo(10, 1, 900, "/tmp/gludd-beta/run")}
    with patch("scripts.process_cleanup.os.kill") as kill:
        assert terminate_tree(table, 10, namespace="/tmp/gludd-alpha") == []
        assert terminate_tree(table, 99, namespace="/tmp/gludd-alpha") == []
    kill.assert_not_called()


def test_terminate_tree_skips_mixed_namespace_child() -> None:
    table = {
        10: ProcessInfo(10, 1, 900, "/tmp/gludd-alpha/run"),
        11: ProcessInfo(11, 10, 800, "/tmp/gludd-beta/worker"),
    }
    with patch("scripts.process_cleanup.os.kill") as kill:
        assert terminate_tree(table, 10, namespace="/tmp/gludd-alpha") == [10]
    assert [call.args[0] for call in kill.call_args_list] == [10]


def test_terminate_tree_is_fail_open_on_signal_errors() -> None:
    table = {10: ProcessInfo(10, 1, 900, "/tmp/gludd-alpha/run")}
    with patch("scripts.process_cleanup.os.kill", side_effect=PermissionError):
        assert terminate_tree(table, 10, namespace="/tmp/gludd-alpha") == []


def test_cli_validate_only_checks_config_without_process_table(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch("scripts.process_cleanup.snapshot_processes") as snapshot,
        patch("scripts.process_cleanup.os.kill") as kill,
    ):
        result = process_cleanup.main(
            [
                "--root-pid",
                "1",
                "--namespace",
                "/tmp/gludd-contract",
                "--validate-only",
            ]
        )

    assert result == 0
    snapshot.assert_not_called()
    kill.assert_not_called()
    assert "PROCESS-CLEANUP-VALIDATION PASS" in capsys.readouterr().out


def test_cli_dry_run_proves_the_same_identity_as_apply(
    capsys: pytest.CaptureFixture[str],
) -> None:
    table = {10: ProcessInfo(10, 1, 900, "/tmp/gludd-alpha/run")}
    with (
        patch("scripts.process_cleanup.snapshot_processes", return_value=table) as snapshot,
        patch("scripts.process_cleanup.os.kill") as kill,
    ):
        result = process_cleanup.main(
            ["--root-pid", "10", "--namespace", "/tmp/gludd-alpha"]
        )

    assert result == 0
    snapshot.assert_called_once_with()
    kill.assert_not_called()
    assert "PROCESS-CLEANUP-DRY-RUN" in capsys.readouterr().out


def test_cli_apply_terminates_only_matching_tree(
    capsys: pytest.CaptureFixture[str],
) -> None:
    table = {
        10: ProcessInfo(10, 1, 900, "/tmp/gludd-alpha/run"),
        11: ProcessInfo(11, 10, 800, "/tmp/gludd-alpha/worker"),
        20: ProcessInfo(20, 1, 900, "/tmp/gludd-beta/other"),
    }
    with (
        patch("scripts.process_cleanup.snapshot_processes", return_value=table),
        patch("scripts.process_cleanup.os.kill") as kill,
    ):
        result = process_cleanup.main(
            [
                "--root-pid",
                "10",
                "--namespace",
                "/tmp/gludd-alpha",
                "--apply",
            ]
        )

    assert result == 0
    assert [call.args[0] for call in kill.call_args_list] == [11, 10]
    assert "PROCESS-CLEANUP-APPLIED killed=11,10" in capsys.readouterr().out


def test_cli_dry_run_rejects_namespace_mismatch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    table = {10: ProcessInfo(10, 1, 900, "/tmp/gludd-beta/run")}
    with (
        patch("scripts.process_cleanup.snapshot_processes", return_value=table),
        patch("scripts.process_cleanup.os.kill") as kill,
    ):
        result = process_cleanup.main(
            ["--root-pid", "10", "--namespace", "/tmp/gludd-alpha"]
        )

    assert result == 2
    kill.assert_not_called()
    assert "namespace mismatch" in capsys.readouterr().err
