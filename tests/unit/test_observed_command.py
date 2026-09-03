"""Behavioral tests for parent-readable observed command execution."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from scripts import stream_command

ROOT = Path(__file__).resolve().parents[2]
STREAM_RUNNER = ROOT / "scripts" / "stream_command.py"
STATUS_FIELDS = {
    "schema_version",
    "kind",
    "label",
    "run_id",
    "state",
    "owner_pid",
    "child_pid",
    "started_at",
    "updated_at",
    "heartbeat_seq",
    "elapsed_seconds",
    "last_output_at",
    "quiet_seconds",
    "bytes_written",
    "lines_written",
    "exit_code",
    "termination_reason",
    "log_path",
    "trace_path",
}


def _command(
    tmp_path: Path,
    child: str,
    *observer_args: str,
    run_id: str = "run-1",
) -> list[str]:
    return [
        sys.executable,
        str(STREAM_RUNNER),
        "--root",
        str(tmp_path / "observed"),
        "--label",
        "demo",
        "--run-id",
        run_id,
        *observer_args,
        "--",
        sys.executable,
        "-c",
        child,
    ]


def _current_status(tmp_path: Path) -> dict[str, Any]:
    path = tmp_path / "observed" / "demo" / "current.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_observed_command_publishes_atomic_terminal_status_and_log(
    tmp_path: Path,
) -> None:
    completed = subprocess.run(
        _command(tmp_path, "print('visible child output', flush=True)"),
        capture_output=True,
        check=False,
    )

    status = _current_status(tmp_path)
    run_status = tmp_path / "observed" / "demo" / "run-1.json"

    assert completed.returncode == 0
    assert b"visible child output\n" in completed.stdout
    assert completed.stderr == b""
    assert set(status) == STATUS_FIELDS
    assert status["schema_version"] == 1
    assert status["kind"] == "observed_command"
    assert status["label"] == "demo"
    assert status["run_id"] == "run-1"
    assert status["state"] == "passed"
    assert status["exit_code"] == 0
    assert status["termination_reason"] is None
    assert status["bytes_written"] == len(b"visible child output\n")
    assert status["lines_written"] == 1
    assert Path(status["log_path"]).read_bytes() == b"visible child output\n"
    assert status["trace_path"] is None
    assert json.loads(run_status.read_text(encoding="utf-8")) == status
    assert not list(run_status.parent.glob(".*.tmp"))


def test_atomic_status_writer_fsyncs_then_replaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "status.json"
    replacements: list[tuple[Path, Path]] = []
    original_replace = os.replace

    def record_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        replacements.append((Path(source), Path(destination)))
        original_replace(source, destination)

    monkeypatch.setattr(stream_command.os, "replace", record_replace)

    stream_command._atomic_write_json(target, {"state": "running"})

    assert json.loads(target.read_text(encoding="utf-8")) == {"state": "running"}
    assert len(replacements) == 1
    temporary, destination = replacements[0]
    assert temporary.parent == target.parent
    assert temporary != target
    assert destination == target
    assert not temporary.exists()


def test_quiet_child_emits_heartbeats_and_updates_status(tmp_path: Path) -> None:
    completed = subprocess.run(
        _command(
            tmp_path,
            "import time; time.sleep(0.22)",
            "--heartbeat-secs",
            "0.03",
            "--max-secs",
            "2",
        ),
        capture_output=True,
        check=False,
        timeout=5,
    )

    status = _current_status(tmp_path)
    assert completed.returncode == 0
    assert completed.stdout.count(b"[observed demo] heartbeat") >= 2
    assert status["heartbeat_seq"] >= 2
    assert status["quiet_seconds"] >= 0.1


def test_observer_keeps_monitoring_after_child_closes_output(tmp_path: Path) -> None:
    completed = subprocess.run(
        _command(
            tmp_path,
            "import os,time; os.close(1); os.close(2); time.sleep(0.15)",
            "--heartbeat-secs",
            "0.03",
            "--max-secs",
            "2",
        ),
        capture_output=True,
        check=False,
        timeout=5,
    )

    status = _current_status(tmp_path)
    assert completed.returncode == 0
    assert completed.stdout.count(b"[observed demo] heartbeat") >= 2
    assert status["state"] == "passed"


def test_quiet_mode_captures_without_streaming_child_output(tmp_path: Path) -> None:
    completed = subprocess.run(
        _command(tmp_path, "print('captured only', flush=True)", "--quiet"),
        capture_output=True,
        check=False,
    )

    status = _current_status(tmp_path)
    assert completed.returncode == 0
    assert b"captured only" not in completed.stdout
    assert Path(status["log_path"]).read_text(encoding="utf-8") == "captured only\n"


def test_observer_preserves_child_failure_code(tmp_path: Path) -> None:
    completed = subprocess.run(
        _command(tmp_path, "import sys; sys.exit(7)"),
        capture_output=True,
        check=False,
    )

    status = _current_status(tmp_path)
    assert completed.returncode == 7
    assert status["state"] == "failed"
    assert status["exit_code"] == 7
    assert status["termination_reason"] is None


def test_observer_records_child_signal_with_shell_exit_semantics(tmp_path: Path) -> None:
    completed = subprocess.run(
        _command(
            tmp_path,
            "import os,signal; os.kill(os.getpid(), signal.SIGTERM)",
        ),
        capture_output=True,
        check=False,
    )

    status = _current_status(tmp_path)
    assert completed.returncode == 128 + signal.SIGTERM
    assert status["state"] == "interrupted"
    assert status["termination_reason"] == "child-signal:SIGTERM"


def test_observer_child_start_failure_is_fail_closed(tmp_path: Path) -> None:
    returncode = stream_command.stream_command(
        [str(tmp_path / "missing-executable")],
        tmp_path / "observed" / "demo" / "run-1.log",
        observed_root=tmp_path / "observed",
        label="demo",
        run_id="run-1",
        quiet=True,
    )

    status = _current_status(tmp_path)
    assert returncode == 125
    assert status["state"] == "failed"
    assert status["termination_reason"] == "observer-child-start-failed"


def test_observer_rejects_unsafe_names_and_invalid_durations(tmp_path: Path) -> None:
    for unsafe in ("", ".hidden", "-option", "../escape", "has space"):
        with pytest.raises(ValueError):
            stream_command._safe_name(unsafe, field="run_id")

    with pytest.raises(ValueError, match="command"):
        stream_command.stream_command([], tmp_path / "unused.log")
    with pytest.raises(ValueError, match="durations"):
        stream_command.stream_command(
            [sys.executable, "-c", "pass"],
            tmp_path / "unused.log",
            max_seconds=-1,
        )
    with pytest.raises(ValueError, match="grace"):
        stream_command.stream_command(
            [sys.executable, "-c", "pass"],
            tmp_path / "unused.log",
            termination_grace_seconds=0,
        )
    with pytest.raises(ValueError, match="retain_runs"):
        stream_command.stream_command(
            [sys.executable, "-c", "pass"],
            tmp_path / "unused.log",
            retain_runs=0,
        )


@pytest.mark.parametrize(
    ("limit_args", "reason"),
    [
        (("--max-secs", "0.1"), "max-runtime-timeout"),
        (("--quiet-secs", "0.1"), "quiet-output-timeout"),
    ],
)
def test_observer_times_out_the_owned_process_group(
    tmp_path: Path, limit_args: tuple[str, str], reason: str
) -> None:
    completed = subprocess.run(
        _command(
            tmp_path,
            "import time; time.sleep(10)",
            "--heartbeat-secs",
            "0.02",
            *limit_args,
        ),
        capture_output=True,
        check=False,
        timeout=5,
    )

    status = _current_status(tmp_path)
    assert completed.returncode == 124
    assert status["state"] == "timed_out"
    assert status["exit_code"] == 124
    assert status["termination_reason"] == reason


def test_observer_forwards_signals_and_records_interruption(tmp_path: Path) -> None:
    process = subprocess.Popen(
        _command(
            tmp_path,
            "import time; time.sleep(10)",
            "--heartbeat-secs",
            "0.03",
            "--max-secs",
            "30",
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    current = tmp_path / "observed" / "demo" / "current.json"
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if current.exists():
            status = json.loads(current.read_text(encoding="utf-8"))
            if status["state"] == "running":
                break
        time.sleep(0.01)
    else:
        process.kill()
        raise AssertionError("observer never published running state")

    os.kill(process.pid, signal.SIGTERM)
    process.communicate(timeout=5)
    returncode = process.returncode

    status = _current_status(tmp_path)
    assert returncode == 128 + signal.SIGTERM
    assert status["state"] == "interrupted"
    assert status["exit_code"] == 128 + signal.SIGTERM
    assert status["termination_reason"] == "observer-signal:SIGTERM"


def test_observer_log_open_failure_is_fail_closed(tmp_path: Path) -> None:
    blocked_log = tmp_path / "blocked.log"
    blocked_log.mkdir()
    completed = subprocess.run(
        _command(tmp_path, "print('must not run')", "--log", str(blocked_log)),
        capture_output=True,
        check=False,
    )

    status = _current_status(tmp_path)
    assert completed.returncode == 125
    assert status["state"] == "failed"
    assert status["exit_code"] == 125
    assert status["termination_reason"] == "observer-log-open-failed"


def test_observer_status_write_failure_terminates_child_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_publish = stream_command._StatusPublisher.publish
    publish_count = 0

    def fail_heartbeat_publish(
        publisher: stream_command._StatusPublisher, payload: dict[str, object]
    ) -> None:
        nonlocal publish_count
        publish_count += 1
        if publish_count >= 3:
            raise OSError("status volume unavailable")
        original_publish(publisher, payload)

    monkeypatch.setattr(
        stream_command._StatusPublisher, "publish", fail_heartbeat_publish
    )

    returncode = stream_command.stream_command(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        tmp_path / "observed" / "demo" / "run-1.log",
        observed_root=tmp_path / "observed",
        label="demo",
        run_id="run-1",
        heartbeat_seconds=0.02,
        quiet=True,
    )

    assert returncode == 125
    assert publish_count >= 3


def test_observer_log_write_failure_terminates_child_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_write(_chunk: bytes, _log_file: object, *, quiet: bool) -> None:
        raise OSError("log volume unavailable")

    monkeypatch.setattr(stream_command, "_write_chunk", fail_write)

    returncode = stream_command.stream_command(
        [
            sys.executable,
            "-c",
            "import time; print('output', flush=True); time.sleep(10)",
        ],
        tmp_path / "observed" / "demo" / "run-1.log",
        observed_root=tmp_path / "observed",
        label="demo",
        run_id="run-1",
        heartbeat_seconds=0.02,
        quiet=True,
    )

    status = _current_status(tmp_path)
    assert returncode == 125
    assert status["state"] == "failed"
    assert status["exit_code"] == 125
    assert status["termination_reason"] == "observer-output-write-failed"


def test_pytest_trace_path_and_run_id_are_exported_to_child(tmp_path: Path) -> None:
    child = (
        "import os,pathlib; "
        "pathlib.Path(os.environ['GLUDD_XDIST_TRACE_LOG']).write_text("
        "os.environ['GLUDD_XDIST_TRACE_RUN_ID'], encoding='utf-8')"
    )
    completed = subprocess.run(
        _command(tmp_path, child, "--pytest-trace"),
        capture_output=True,
        check=False,
    )

    status = _current_status(tmp_path)
    assert completed.returncode == 0
    assert status["trace_path"] is not None
    assert Path(status["trace_path"]).read_text(encoding="utf-8") == "run-1"


def test_status_infers_orphaned_running_owner(tmp_path: Path) -> None:
    label_dir = tmp_path / "observed" / "demo"
    label_dir.mkdir(parents=True)
    payload = {
        "state": "running",
        "owner_pid": 999_999_999,
        "updated_at": "2000-01-01T00:00:00+00:00",
        "log_path": str(label_dir / "run.log"),
    }
    (label_dir / "current.json").write_text(json.dumps(payload), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(STREAM_RUNNER),
            "--status",
            "--root",
            str(tmp_path / "observed"),
            "--label",
            "demo",
            "--stale-secs",
            "0.01",
        ],
        capture_output=True,
        check=False,
    )

    status = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert status["state"] == "running"
    assert status["effective_state"] == "orphaned"
    assert status["active"] is False
    assert status["stale"] is True


def test_status_distinguishes_live_fresh_and_live_stale_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    label_dir = tmp_path / "observed" / "demo"
    label_dir.mkdir(parents=True)
    current = label_dir / "current.json"
    payload = {
        "state": "running",
        "owner_pid": os.getpid(),
        "updated_at": "2000-01-01T00:00:00",
        "log_path": str(label_dir / "run.log"),
    }
    current.write_text(json.dumps(payload), encoding="utf-8")

    assert (
        stream_command.observed_status(
            tmp_path / "observed", "demo", stale_seconds=0.01
        )
        == 3
    )
    stale = json.loads(capsys.readouterr().out)
    assert stale["effective_state"] == "stale"
    assert stale["active"] is False

    payload["updated_at"] = stream_command._utc_now()
    current.write_text(json.dumps(payload), encoding="utf-8")
    assert (
        stream_command.observed_status(
            tmp_path / "observed", "demo", stale_seconds=90
        )
        == 0
    )
    fresh = json.loads(capsys.readouterr().out)
    assert fresh["effective_state"] == "running"
    assert fresh["active"] is True


def test_status_and_tail_helpers_reject_invalid_inputs(tmp_path: Path) -> None:
    assert stream_command._pid_alive(None) is False
    assert stream_command._age_seconds(None) == float("inf")
    assert stream_command._age_seconds("not-a-timestamp") == float("inf")

    label_dir = tmp_path / "observed" / "demo"
    label_dir.mkdir(parents=True)
    (label_dir / "current.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="between 1 and 1000"):
        stream_command.observed_tail(tmp_path / "observed", "demo", lines=0)
    with pytest.raises(ValueError, match="log_path"):
        stream_command.observed_tail(tmp_path / "observed", "demo", lines=1)


def test_retention_prunes_only_superseded_terminal_run_artifacts(
    tmp_path: Path,
) -> None:
    label_dir = tmp_path / "observed" / "demo"
    label_dir.mkdir(parents=True)
    (label_dir / "current.json").write_text("{}", encoding="utf-8")

    for index, state in enumerate(("passed", "failed", "running"), start=1):
        run_id = f"run-{index}"
        log_path = label_dir / f"{run_id}.log"
        trace_path = label_dir / f"{run_id}.pytest.jsonl"
        log_path.write_text("log", encoding="utf-8")
        trace_path.write_text("trace", encoding="utf-8")
        (label_dir / f"{run_id}.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "observed_command",
                    "label": "demo",
                    "run_id": run_id,
                    "state": state,
                    "started_at": f"2026-09-02T00:00:0{index}+00:00",
                    "log_path": str(log_path),
                    "trace_path": str(trace_path),
                }
            ),
            encoding="utf-8",
        )

    malformed_log = label_dir / "run-malformed.log"
    malformed_log.write_text("preserve", encoding="utf-8")
    (label_dir / "run-malformed.json").write_text(
        json.dumps(
            {
                "run_id": "run-malformed",
                "state": "passed",
                "started_at": "2000-01-01T00:00:00+00:00",
                "log_path": str(malformed_log),
            }
        ),
        encoding="utf-8",
    )

    stream_command._prune_terminal_runs(label_dir, retain_runs=1)

    assert not (label_dir / "run-1.json").exists()
    assert not (label_dir / "run-1.log").exists()
    assert not (label_dir / "run-1.pytest.jsonl").exists()
    for run_id in ("run-2", "run-3"):
        assert (label_dir / f"{run_id}.json").exists()
        assert (label_dir / f"{run_id}.log").exists()
        assert (label_dir / f"{run_id}.pytest.jsonl").exists()
    assert (label_dir / "run-malformed.json").exists()
    assert malformed_log.exists()
    assert (label_dir / "current.json").exists()


def test_observer_prunes_to_retention_bound_after_terminal_publish(
    tmp_path: Path,
) -> None:
    for index in range(1, 4):
        completed = subprocess.run(
            _command(
                tmp_path,
                f"print('run-{index}', flush=True)",
                "--retain-runs",
                "2",
                run_id=f"run-{index}",
            ),
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0

    label_dir = tmp_path / "observed" / "demo"
    assert {path.name for path in label_dir.glob("run-*.json")} == {
        "run-2.json",
        "run-3.json",
    }
    assert {path.name for path in label_dir.glob("run-*.log")} == {
        "run-2.log",
        "run-3.log",
    }
    assert _current_status(tmp_path)["run_id"] == "run-3"


def test_status_and_tail_address_retained_terminal_run_after_current_advances(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An immutable run ID keeps terminal evidence queryable until rotation."""

    for run_id in ("run-1", "run-2"):
        log_path = (
            tmp_path / "custom-retained.log"
            if run_id == "run-1"
            else tmp_path / "observed" / "demo" / f"{run_id}.log"
        )
        returncode = stream_command.stream_command(
            [sys.executable, "-c", f"print('{run_id}', flush=True)"],
            log_path,
            observed_root=tmp_path / "observed",
            label="demo",
            run_id=run_id,
            quiet=True,
            retain_runs=2,
        )
        assert returncode == 0

    capsys.readouterr()
    assert (
        stream_command.observed_status(
            tmp_path / "observed",
            "demo",
            run_id="run-1",
            stale_seconds=90,
        )
        == 0
    )
    retained = json.loads(capsys.readouterr().out)
    assert retained["run_id"] == "run-1"
    assert retained["state"] == "passed"

    assert (
        stream_command.observed_tail(
            tmp_path / "observed", "demo", run_id="run-1", lines=1
        )
        == 0
    )
    assert capsys.readouterr().out == "run-1\n"


def test_current_run_id_alias_resolves_payload_identity_and_preserves_exact_lookup(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The explicit current alias resolves the real run without weakening exact IDs."""

    for run_id in ("run-1", "run-2"):
        assert (
            stream_command.stream_command(
                [sys.executable, "-c", "pass"],
                tmp_path / "observed" / "demo" / f"{run_id}.log",
                observed_root=tmp_path / "observed",
                label="demo",
                run_id=run_id,
                quiet=True,
                retain_runs=2,
            )
            == 0
        )

    capsys.readouterr()
    for requested_run_id, expected_run_id in (("current", "run-2"), ("run-1", "run-1")):
        assert (
            stream_command.main(
                [
                    "--status",
                    "--root",
                    str(tmp_path / "observed"),
                    "--label",
                    "demo",
                    "--run-id",
                    requested_run_id,
                    "--stale-secs",
                    "90",
                ]
            )
            == 0
        )
        status = json.loads(capsys.readouterr().out)
        assert status["run_id"] == expected_run_id


def test_current_run_id_alias_rejects_unsafe_payload_run_identity(
    tmp_path: Path,
) -> None:
    """A current pointer cannot use its payload to escape retained-run identity."""

    label_dir = tmp_path / "observed" / "demo"
    label_dir.mkdir(parents=True)
    (label_dir / "current.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "observed_command",
                "label": "demo",
                "run_id": "../escape",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="run_id must be a safe"):
        stream_command._load_status(
            tmp_path / "observed", "demo", run_id="current"
        )


def test_exact_terminal_lookup_obeys_retention_rotation(tmp_path: Path) -> None:
    """Exact-run readers cannot resurrect artifacts pruned by the bounded policy."""

    for run_id in ("run-1", "run-2"):
        assert (
            stream_command.stream_command(
                [sys.executable, "-c", "pass"],
                tmp_path / "observed" / "demo" / f"{run_id}.log",
                observed_root=tmp_path / "observed",
                label="demo",
                run_id=run_id,
                quiet=True,
                retain_runs=1,
            )
            == 0
        )

    with pytest.raises(FileNotFoundError):
        stream_command.observed_status(
            tmp_path / "observed",
            "demo",
            run_id="run-1",
            stale_seconds=90,
        )


def test_retention_failure_after_child_exit_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_retention(_directory: Path, *, retain_runs: int) -> None:
        raise OSError(f"retention volume unavailable ({retain_runs})")

    monkeypatch.setattr(stream_command, "_prune_terminal_runs", fail_retention)

    returncode = stream_command.stream_command(
        [sys.executable, "-c", "print('child completed', flush=True)"],
        tmp_path / "observed" / "demo" / "run-1.log",
        observed_root=tmp_path / "observed",
        label="demo",
        run_id="run-1",
        quiet=True,
        retain_runs=2,
    )

    status = _current_status(tmp_path)
    assert Path(status["log_path"]).read_text(encoding="utf-8") == "child completed\n"
    assert returncode == 125
    assert status["state"] == "failed"
    assert status["exit_code"] == 125
    assert status["termination_reason"] == "observer-retention-failed"


def test_tail_reads_only_a_bounded_snapshot_from_current_run(tmp_path: Path) -> None:
    label_dir = tmp_path / "observed" / "demo"
    label_dir.mkdir(parents=True)
    log_path = label_dir / "run.log"
    log_path.write_text("".join(f"line-{index}\n" for index in range(100)), encoding="utf-8")
    (label_dir / "current.json").write_text(
        json.dumps({"log_path": str(log_path)}), encoding="utf-8"
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(STREAM_RUNNER),
            "--tail",
            "4",
            "--root",
            str(tmp_path / "observed"),
            "--label",
            "demo",
        ],
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.decode().splitlines() == [
        "line-96",
        "line-97",
        "line-98",
        "line-99",
    ]
