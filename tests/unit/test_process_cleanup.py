"""Regression tests for namespaced process cleanup and stale lock recovery."""

from __future__ import annotations

import json
import signal
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


def test_snapshot_processes_adds_cwd_only_to_requested_tree() -> None:
    result = type(
        "Result",
        (),
        {
            "stdout": (
                "10 1 00:03 /usr/bin/make gate\n"
                "11 10 00:02 python worker.py\n"
                "20 1 00:01 python unrelated.py\n"
            )
        },
    )()
    with (
        patch("scripts.process_cleanup.subprocess.run", return_value=result),
        patch(
            "scripts.process_cleanup._snapshot_process_cwds",
            return_value={10: "/tmp/gludd-alpha", 11: "/tmp/gludd-alpha"},
        ) as snapshot_cwds,
    ):
        table = snapshot_processes(10)

    snapshot_cwds.assert_called_once_with([11, 10])
    assert table[10].cwd == "/tmp/gludd-alpha"
    assert table[11].cwd == "/tmp/gludd-alpha"
    assert table[20].cwd is None


def test_snapshot_process_cwds_uses_proc_without_fallback() -> None:
    with (
        patch(
            "scripts.process_cleanup.os.readlink",
            side_effect=["/tmp/gludd-alpha", "/tmp/gludd-alpha/worker"],
        ),
        patch("scripts.process_cleanup.subprocess.run") as run,
    ):
        assert process_cleanup._snapshot_process_cwds([10, 11]) == {
            10: "/tmp/gludd-alpha",
            11: "/tmp/gludd-alpha/worker",
        }
    run.assert_not_called()


def test_snapshot_process_cwds_parses_one_bounded_lsof_fallback() -> None:
    result = type(
        "Result",
        (),
        {
            "stdout": (
                "p10\nfcwd\nn/tmp/gludd-alpha\n"
                "pnot-a-pid\nn/tmp/ignored\n"
                "p11\nfcwd\nnrelative/path\n"
            )
        },
    )()
    with (
        patch("scripts.process_cleanup.os.readlink", side_effect=OSError),
        patch("scripts.process_cleanup.subprocess.run", return_value=result) as run,
    ):
        assert process_cleanup._snapshot_process_cwds([0, 10, 10, 11]) == {
            10: "/tmp/gludd-alpha"
        }

    run.assert_called_once()
    assert run.call_args.args[0] == [
        "lsof",
        "-a",
        "-p",
        "10,11",
        "-d",
        "cwd",
        "-Fn",
    ]


def test_snapshot_process_cwds_fails_closed_when_lsof_is_unavailable() -> None:
    with (
        patch("scripts.process_cleanup.os.readlink", side_effect=OSError),
        patch("scripts.process_cleanup.subprocess.run", side_effect=OSError),
    ):
        assert process_cleanup._snapshot_process_cwds([10]) == {}


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
    assert not namespace_matches(
        "python /tmp/gludd-alpha-other/test.py", "/tmp/gludd-alpha"
    )
    assert not namespace_matches("python worker.py", "")


def test_malformed_make_command_cannot_use_descendant_proof() -> None:
    namespace = "/tmp/gludd-alpha"
    table = {
        10: ProcessInfo(10, 1, 900, "make '"),
        11: ProcessInfo(11, 10, 800, f"python {namespace}/worker.py"),
    }
    assert process_cleanup.namespaced_process_tree(
        table, 10, namespace=namespace
    ) == []


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


def test_cli_validate_only_checks_same_live_identity_as_apply(
    capsys: pytest.CaptureFixture[str],
) -> None:
    table = {10: ProcessInfo(10, 1, 900, "/tmp/gludd-contract/run")}
    with (
        patch(
            "scripts.process_cleanup.snapshot_processes", return_value=table
        ) as snapshot,
        patch("scripts.process_cleanup.os.kill") as kill,
    ):
        result = process_cleanup.main(
            [
                "--root-pid",
                "10",
                "--namespace",
                "/tmp/gludd-contract",
                "--validate-only",
            ]
        )

    assert result == 0
    snapshot.assert_called_once_with(10)
    kill.assert_not_called()
    assert "PROCESS-CLEANUP-VALIDATION PASS" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("table", "error"),
    [
        ({}, "process not found"),
        (
            {10: ProcessInfo(10, 1, 900, "/tmp/gludd-other/run")},
            "namespace mismatch",
        ),
    ],
)
def test_cli_validate_only_rejects_the_same_identity_failures_as_apply(
    table: dict[int, ProcessInfo],
    error: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with (
        patch("scripts.process_cleanup.snapshot_processes", return_value=table),
        patch("scripts.process_cleanup.os.kill") as kill,
    ):
        result = process_cleanup.main(
            [
                "--root-pid",
                "10",
                "--namespace",
                "/tmp/gludd-contract",
                "--validate-only",
            ]
        )

    assert result == 2
    kill.assert_not_called()
    assert error in capsys.readouterr().err


def test_process_group_liveness_and_signal_helpers_are_bounded() -> None:
    with patch("scripts.process_cleanup.os.killpg") as kill_group:
        assert process_cleanup.process_group_alive(42) is True
        assert process_cleanup.signal_process_group(42, signal.SIGTERM) is True
    assert [call.args for call in kill_group.call_args_list] == [
        (42, 0),
        (42, signal.SIGTERM),
    ]

    with patch(
        "scripts.process_cleanup.os.killpg", side_effect=ProcessLookupError
    ) as kill_group:
        assert process_cleanup.process_group_alive(42) is False
        assert process_cleanup.signal_process_group(42, signal.SIGKILL) is False
    assert kill_group.call_count == 2
    assert process_cleanup.process_group_alive(1) is False


@pytest.mark.parametrize("error", [PermissionError, OSError])
def test_process_group_liveness_handles_non_lookup_errors(
    error: type[OSError],
) -> None:
    with patch("scripts.process_cleanup.os.killpg", side_effect=error):
        assert process_cleanup.process_group_alive(42) is (error is PermissionError)


def test_signal_process_group_rejects_reserved_group() -> None:
    with patch("scripts.process_cleanup.os.killpg") as kill_group:
        assert process_cleanup.signal_process_group(1, signal.SIGTERM) is False
    kill_group.assert_not_called()


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
    snapshot.assert_called_once_with(10)
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


@pytest.mark.parametrize("mode", ["--validate-only", "--apply"])
def test_cli_modes_admit_make_root_from_the_same_cwd_identity_snapshot(
    mode: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    namespace = "/tmp/gludd-worktrees/feature/cleanup-parity"
    table = {
        10: ProcessInfo(10, 1, 900, "/usr/bin/make gate", cwd=namespace),
        11: ProcessInfo(11, 10, 800, "python worker.py", cwd=namespace),
    }
    with (
        patch("scripts.process_cleanup.snapshot_processes", return_value=table),
        patch("scripts.process_cleanup.os.kill") as kill,
    ):
        result = process_cleanup.main(
            ["--root-pid", "10", "--namespace", namespace, mode]
        )

    assert result == 0
    if mode == "--apply":
        assert [call.args[0] for call in kill.call_args_list] == [11, 10]
        assert "PROCESS-CLEANUP-APPLIED killed=11,10" in capsys.readouterr().out
    else:
        kill.assert_not_called()
        assert "PROCESS-CLEANUP-VALIDATION PASS" in capsys.readouterr().out


@pytest.mark.parametrize("mode", ["--validate-only", "--apply"])
def test_cli_modes_admit_make_root_from_namespaced_descendant_proof(
    mode: str,
) -> None:
    namespace = "/tmp/gludd-worktrees/feature/cleanup-parity"
    table = {
        10: ProcessInfo(10, 1, 900, "/usr/bin/make gate"),
        11: ProcessInfo(11, 10, 800, f"python {namespace}/worker.py"),
    }
    with (
        patch("scripts.process_cleanup.snapshot_processes", return_value=table),
        patch("scripts.process_cleanup.os.kill") as kill,
    ):
        result = process_cleanup.main(
            ["--root-pid", "10", "--namespace", namespace, mode]
        )

    assert result == 0
    if mode == "--apply":
        assert [call.args[0] for call in kill.call_args_list] == [11, 10]
    else:
        kill.assert_not_called()


@pytest.mark.parametrize("mode", ["--validate-only", "--apply"])
def test_cli_modes_reject_unrelated_parent_even_with_namespaced_descendant(
    mode: str,
) -> None:
    namespace = "/tmp/gludd-worktrees/feature/cleanup-parity"
    table = {
        10: ProcessInfo(10, 1, 900, "/usr/bin/sleep 600", cwd=namespace),
        11: ProcessInfo(11, 10, 800, f"python {namespace}/worker.py"),
    }
    with (
        patch("scripts.process_cleanup.snapshot_processes", return_value=table),
        patch("scripts.process_cleanup.os.kill") as kill,
    ):
        result = process_cleanup.main(
            ["--root-pid", "10", "--namespace", namespace, mode]
        )

    assert result == 2
    kill.assert_not_called()


def test_apply_rejects_reused_make_pid_after_validation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    namespace = "/tmp/gludd-worktrees/feature/cleanup-parity"
    admitted = {
        10: ProcessInfo(10, 1, 900, "/usr/bin/make gate", cwd=namespace)
    }
    reused = {
        10: ProcessInfo(
            10,
            1,
            1,
            "/usr/bin/make gate",
            cwd=f"{namespace}-unrelated",
        )
    }
    with (
        patch(
            "scripts.process_cleanup.snapshot_processes",
            side_effect=[admitted, reused],
        ),
        patch("scripts.process_cleanup.os.kill") as kill,
    ):
        validation_result = process_cleanup.main(
            ["--root-pid", "10", "--namespace", namespace, "--validate-only"]
        )
        apply_result = process_cleanup.main(
            ["--root-pid", "10", "--namespace", namespace, "--apply"]
        )

    assert validation_result == 0
    assert apply_result == 2
    kill.assert_not_called()
    assert "namespace mismatch" in capsys.readouterr().err


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
