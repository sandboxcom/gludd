"""Native run-watched trace options retain literal data and existing observer ownership."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(os.environ.get("GLUDD_WATCHED_TRACE_PROJECT_ROOT", str(Path(__file__).resolve().parents[2])))
OPTION = "RUN_WATCHED_PYTEST_TRACE"
RECURSION = {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "GNUMAKEFLAGS"}
TRACE_KEYS = {"GLUDD_XDIST_TRACE_LOG", "GLUDD_XDIST_TRACE_RUN_ID", "GLUDD_XDIST_TRACE_TRUNCATE"}
RECORDER = """import json, os, sys
from pathlib import Path
payload = {"argv": sys.argv[1:], "option": os.environ.get("RUN_WATCHED_PYTEST_TRACE")}
with Path(os.environ["GLUDD_WATCHED_TRACE_CALLS"]).open("x", encoding="utf-8") as stream:
    json.dump(payload, stream)
status = int(os.environ.get("GLUDD_WATCHED_TRACE_RECORDER_EXIT", "0"))
if status:
    print("watched-recorder-error:" + str(status), file=sys.stderr)
raise SystemExit(status)
"""


@dataclass(frozen=True)
class WatchedTraceFixture:
    """Keep environment values private and every writable artifact in the fixture."""

    directory: Path
    makefile: Path
    recorder: Path
    child: Path
    environment: dict[str, str] = field(repr=False)


def _recipe() -> str:
    selected = os.environ.get("GLUDD_WATCHED_TRACE_MAKE_SOURCE")
    source = Path(selected).read_text(encoding="utf-8") if selected else compose_makefile(ROOT / "Makefile")
    marker = "\nrun-watched:\n"
    assert source.count(marker) == 1, "Expected exactly one real production run-watched target"
    return source.split(marker, 1)[1].split("\n\n", 1)[0]


def _fixture(tmp_path: Path) -> WatchedTraceFixture:
    recipe = _recipe()
    home = tmp_path / "fixture-home"
    process_tmp = tmp_path / "process-tmp"
    home.mkdir()
    process_tmp.mkdir()
    recorder = tmp_path / "recorder.py"
    recorder.write_text(RECORDER, encoding="utf-8")
    child = tmp_path / "watched child.py"
    child.write_text("print('native watched child', flush=True)\n", encoding="utf-8")
    makefile = tmp_path / "probe.mk"
    makefile.write_text("run-watched:\n" + recipe + "\n", encoding="utf-8")
    removed = RECURSION | TRACE_KEYS | {OPTION, "GLUDD_WATCHED_TRACE_RECORDER_EXIT"}
    environment = {key: value for key, value in os.environ.items() if key not in removed}
    environment.update(HOME=str(home), TMPDIR=str(process_tmp),
                       GLUDD_WATCHED_TRACE_CALLS=str(tmp_path / "calls.json"),
                       GLUDD_WATCHED_TRACE_PRIVATE="fixture-only-private-environment")
    return WatchedTraceFixture(tmp_path, makefile, recorder, child, environment)


def _make(
    fixture: WatchedTraceFixture, watched_trace_option: str | None, *,
    watched_trace_environment_overrides: bool = False,
    watched_trace_origin: str = "command-line", watched_trace_context: str = "extracted",
    watched_trace_late_value: str = "1", watched_trace_uv: Path | None = None,
    watched_trace_limits: tuple[str, str] = ("3", "5"),
) -> subprocess.CompletedProcess[str]:
    environment = dict(fixture.environment)
    goals = ["run-watched"]
    directory = fixture.directory
    if watched_trace_context != "extracted":
        text = f"include {ROOT / 'Makefile'}\n"
        if watched_trace_context == "direct-specific":
            text += f"run-watched: override {OPTION} = {watched_trace_late_value}\n"
        elif watched_trace_context in {"alias", "mixed"}:
            text += (
                ".PHONY: watched-trace-alias\n"
                f"watched-trace-alias: override {OPTION} = {watched_trace_late_value}\n"
                "watched-trace-alias: run-watched\n"
            )
            goals = ["watched-trace-alias"]
            if watched_trace_context == "mixed":
                goals.append("run-watched")
        elif watched_trace_context == "unrelated":
            text += ".PHONY: watched-trace-unrelated\nwatched-trace-unrelated:\n\t@$(UV) probe\n"
            goals = ["watched-trace-unrelated"]
        else:
            assert watched_trace_context == "bootstrap"
        fixture.makefile.write_text(text, encoding="utf-8")
        directory = ROOT
    command = ["make", "--no-print-directory"]
    if watched_trace_environment_overrides:
        command.append("-e")
    command.extend([
        "-f", str(fixture.makefile), *goals,
        "UV=" + shlex.join([sys.executable, str(watched_trace_uv or fixture.recorder)]),
        "CMD=" + shlex.join([sys.executable, str(fixture.child), "literal child argument"]),
        "OBSERVED_ROOT=" + str(fixture.directory / "observed"), "OBSERVED_LABEL=demo",
        "RUN_ID=run-1", "WORK_ITEM_ID=", "STALL_SECS=" + watched_trace_limits[0],
        "MAX_SECS=" + watched_trace_limits[1],
        "LOG=" + str(fixture.directory / "observed/demo/run-1.log"),
        "OBSERVED_HEARTBEAT_SECS=0.02", "OBSERVED_RETAIN_RUNS=2",
    ])
    if watched_trace_option is not None:
        if watched_trace_origin == "environment":
            environment[OPTION] = watched_trace_option
        else:
            assert watched_trace_origin == "command-line"
            command.append(OPTION + "=" + watched_trace_option)
    return subprocess.run(
        command, cwd=directory, env=environment, capture_output=True,
        text=True, check=False, timeout=15,
    )


def _assert_recorder_arguments(fixture: WatchedTraceFixture, watched_trace_expected: bool) -> None:
    call = cast(dict[str, object], json.loads((fixture.directory / "calls.json").read_text()))
    expected = [
        "run", "python", "scripts/stream_command.py",
        "--root", str(fixture.directory / "observed"), "--label", "demo",
        "--item-progress", "--item-task", "", "--run-id", "run-1",
        "--log", str(fixture.directory / "observed/demo/run-1.log"),
        "--heartbeat-secs", "0.02", "--quiet-secs", "3", "--max-secs", "5",
        "--retain-runs", "2",
    ]
    if watched_trace_expected:
        expected.append("--pytest-trace")
    expected.extend([
        "--", "/bin/sh", "-c",
        shlex.join([sys.executable, str(fixture.child), "literal child argument"]),
    ])
    assert call["argv"] == expected


def _real_uv(fixture: WatchedTraceFixture) -> Path:
    adapter = fixture.directory / "selected-uv.py"
    adapter.write_text(
        "import os, sys\n"
        "assert sys.argv[1:4] == ['run', 'python', 'scripts/stream_command.py']\n"
        f"os.execv(sys.executable, [sys.executable, {str(ROOT / 'scripts/stream_command.py')!r}, "
        "*sys.argv[4:]])\n",
        encoding="utf-8",
    )
    return adapter


def _native_child(
    fixture: WatchedTraceFixture, *, watched_trace_exit: int = 0, watched_trace_sleep: bool = False,
) -> None:
    fixture.child.write_text(
        "import json, os, sys, time\nfrom pathlib import Path\n"
        "trace = os.environ.get('GLUDD_XDIST_TRACE_LOG')\n"
        "payload = {'pid': os.getpid(), 'argv': sys.argv[1:], 'trace': trace,\n"
        "           'run_id': os.environ.get('GLUDD_XDIST_TRACE_RUN_ID'),\n"
        "           'truncate': os.environ.get('GLUDD_XDIST_TRACE_TRUNCATE')}\n"
        "if trace is not None:\n"
        "    payload['trace_was_empty'] = Path(trace).read_bytes() == b''\n"
        "    with Path(trace).open('a', encoding='utf-8') as stream:\n"
        "        stream.write(json.dumps({'fixture': True, 'run_id': payload['run_id']}) + '\\n')\n"
        f"Path({str(fixture.directory / 'child.json')!r}).write_text(json.dumps(payload), encoding='utf-8')\n"
        "print('native watched child', flush=True)\n"
        + ("time.sleep(30)\n" if watched_trace_sleep else "")
        + f"raise SystemExit({watched_trace_exit})\n",
        encoding="utf-8",
    )


def _status(fixture: WatchedTraceFixture) -> dict[str, object]:
    from tests.unit.test_observed_command import STATUS_FIELDS, _current_status

    status = cast(dict[str, object], _current_status(fixture.directory))
    assert set(status) == STATUS_FIELDS
    exact = fixture.directory / "observed/demo/run-1.json"
    assert json.loads(exact.read_text(encoding="utf-8")) == status
    assert not list(exact.parent.glob(".*.tmp"))
    return status


def _child_payload(fixture: WatchedTraceFixture) -> dict[str, object]:
    text = (fixture.directory / "child.json").read_text(encoding="utf-8")
    return cast(dict[str, object], json.loads(text))


@pytest.mark.parametrize(("watched_trace_option", "watched_trace_expected"), [
    pytest.param(None, False, id="default-off"),
    pytest.param("0", False, id="explicit-off"),
    pytest.param("1", True, id="enabled"),
])
def test_native_trace_option_preserves_default_off_and_exact_on_argv(
    tmp_path: Path, watched_trace_option: str | None, watched_trace_expected: bool,
) -> None:
    fixture = _fixture(tmp_path)
    result = _make(fixture, watched_trace_option)
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_recorder_arguments(fixture, watched_trace_expected)


@pytest.mark.parametrize(
    ("watched_trace_option", "watched_trace_origin", "watched_trace_environment_overrides"), [
        ("", "command-line", False), ("2", "environment", False),
        ("", "environment", True), ("01", "command-line", True),
    ],
)
def test_invalid_trace_flag_refuses_before_the_native_boundary(
    tmp_path: Path, watched_trace_option: str, watched_trace_origin: str,
    watched_trace_environment_overrides: bool,
) -> None:
    fixture = _fixture(tmp_path)
    result = _make(
        fixture, watched_trace_option, watched_trace_origin=watched_trace_origin,
        watched_trace_environment_overrides=watched_trace_environment_overrides,
        watched_trace_context="bootstrap",
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert OPTION + " must be 0 or 1" in result.stderr
    assert not (fixture.directory / "calls.json").exists()
    assert not (fixture.directory / "observed").exists()


@pytest.mark.parametrize("watched_trace_environment_overrides", [False, True])
@pytest.mark.parametrize("watched_trace_origin", ["command-line", "environment"])
@pytest.mark.parametrize("watched_trace_payload", ["make-function", "shell-substitution", "backticks"])
def test_trace_flag_is_inert_before_real_make_startup_and_native_io(
    tmp_path: Path, watched_trace_environment_overrides: bool,
    watched_trace_origin: str, watched_trace_payload: str,
) -> None:
    fixture = _fixture(tmp_path)
    marker = fixture.directory / "PUBLIC_TRIPWIRE"
    value = {
        "make-function": "$(shell touch " + str(marker) + ")",
        "shell-substitution": "$(touch " + str(marker) + ")",
        "backticks": chr(96) + "touch " + str(marker) + chr(96),
    }[watched_trace_payload]
    result = _make(
        fixture, value, watched_trace_origin=watched_trace_origin,
        watched_trace_environment_overrides=watched_trace_environment_overrides,
        watched_trace_context="bootstrap",
    )
    assert not marker.exists(), "trace-option data executed during Make or shell expansion"
    assert result.returncode == 2, result.stdout + result.stderr
    assert OPTION + " must be 0 or 1" in result.stderr
    assert not (fixture.directory / "calls.json").exists()


@pytest.mark.parametrize("watched_trace_environment_overrides", [False, True])
@pytest.mark.parametrize("watched_trace_context", ["direct-specific", "alias", "mixed"])
def test_late_literal_trace_enable_uses_the_actual_native_target_context(
    tmp_path: Path, watched_trace_environment_overrides: bool, watched_trace_context: str,
) -> None:
    fixture = _fixture(tmp_path)
    result = _make(
        fixture, "0", watched_trace_context=watched_trace_context,
        watched_trace_environment_overrides=watched_trace_environment_overrides,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_recorder_arguments(fixture, True)


def test_late_literal_off_remains_off_when_the_global_input_is_on(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _make(fixture, "1", watched_trace_context="alias", watched_trace_late_value="0")
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_recorder_arguments(fixture, False)


def test_native_boundary_failure_keeps_stderr_and_make_error_status(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.environment["GLUDD_WATCHED_TRACE_RECORDER_EXIT"] = "7"
    result = _make(fixture, "1")
    assert result.returncode == 2
    assert "watched-recorder-error:7" in result.stderr
    assert "Error 7" in result.stderr
    _assert_recorder_arguments(fixture, True)


def test_unrelated_native_goal_does_not_acquire_trace_option_validation(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _make(fixture, "unrelated-value", watched_trace_context="unrelated")
    assert result.returncode == 0, result.stdout + result.stderr
    call = cast(dict[str, object], json.loads((fixture.directory / "calls.json").read_text()))
    assert call == {"argv": ["probe"], "option": "unrelated-value"}


def test_watched_trace_fixture_repr_excludes_environment_values(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    assert "fixture-only-private-environment" not in repr(fixture)
    assert "GLUDD_WATCHED_TRACE_PRIVATE" not in repr(fixture)


@pytest.mark.parametrize("watched_trace_option", [None, "0", "1"])
def test_real_native_observer_binds_only_the_requested_new_trace(
    tmp_path: Path, watched_trace_option: str | None,
) -> None:
    fixture = _fixture(tmp_path)
    _native_child(fixture)
    result = _make(fixture, watched_trace_option, watched_trace_uv=_real_uv(fixture))
    assert result.returncode == 0, result.stdout + result.stderr
    status = _status(fixture)
    child = _child_payload(fixture)
    assert status["state"] == "passed" and status["exit_code"] == 0
    assert child["argv"] == ["literal child argument"]
    if watched_trace_option == "1":
        trace = status["trace_path"]
        assert isinstance(trace, str)
        assert Path(trace) == fixture.directory / "observed/demo/run-1.pytest.jsonl"
        assert child["trace"] == trace and child["run_id"] == "run-1" and child["truncate"] == "0"
        assert child["trace_was_empty"] is True
        assert json.loads(Path(trace).read_text()) == {"fixture": True, "run_id": "run-1"}
    else:
        assert status["trace_path"] is None
        assert child["trace"] is None and child["run_id"] is None and child["truncate"] is None
        assert not list((fixture.directory / "observed/demo").glob("*.pytest.jsonl"))


def test_generic_off_keeps_existing_inherited_native_trace_semantics(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    inherited = fixture.directory / "parent.pytest.jsonl"
    inherited.write_text("retained parent evidence\n", encoding="utf-8")
    fixture.environment.update(
        GLUDD_XDIST_TRACE_LOG=str(inherited), GLUDD_XDIST_TRACE_RUN_ID="parent-run",
        GLUDD_XDIST_TRACE_TRUNCATE="0",
    )
    _native_child(fixture)
    result = _make(fixture, "0", watched_trace_uv=_real_uv(fixture))
    assert result.returncode == 0, result.stdout + result.stderr
    assert _status(fixture)["trace_path"] is None
    child = _child_payload(fixture)
    assert child["trace"] == str(inherited) and child["run_id"] == "parent-run"
    assert child["trace_was_empty"] is False and child["truncate"] == "0"
    assert inherited.read_text().startswith("retained parent evidence\n")
    assert not list((fixture.directory / "observed/demo").glob("*.pytest.jsonl"))


def test_failed_native_child_retains_bound_trace_and_its_original_exit(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _native_child(fixture, watched_trace_exit=7)
    result = _make(fixture, "1", watched_trace_uv=_real_uv(fixture))
    assert result.returncode == 2 and "Error 7" in result.stderr
    status = _status(fixture)
    assert status["state"] == "failed" and status["exit_code"] == 7
    assert status["termination_reason"] is None
    trace = status["trace_path"]
    assert isinstance(trace, str)
    assert json.loads(Path(trace).read_text()) == {"fixture": True, "run_id": "run-1"}
    log = status["log_path"]
    assert isinstance(log, str) and "native watched child" in Path(log).read_text()


def test_owned_native_timeout_retains_its_trace_log_and_terminal_receipt(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _native_child(fixture, watched_trace_sleep=True)
    result = _make(
        fixture, "1", watched_trace_uv=_real_uv(fixture), watched_trace_limits=("0", "1"),
    )
    assert result.returncode == 2 and "Error 124" in result.stderr
    status = _status(fixture)
    assert status["state"] == "timed_out" and status["exit_code"] == 124
    assert status["termination_reason"] == "max-runtime-timeout"
    trace = status["trace_path"]
    assert isinstance(trace, str)
    assert json.loads(Path(trace).read_text()) == {"fixture": True, "run_id": "run-1"}
    log = status["log_path"]
    assert isinstance(log, str) and "native watched child" in Path(log).read_text()
    pid = _child_payload(fixture)["pid"]
    assert isinstance(pid, int) and pid > 0
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize("watched_trace_option", ["0", "1"])
def test_optional_trace_preserves_cmd_shell_positional_arguments(
    tmp_path: Path, watched_trace_option: str,
) -> None:
    fixture = _fixture(tmp_path)
    with fixture.makefile.open("a", encoding="utf-8") as stream:
        stream.write('\noverride CMD := $(value CMD) "$$@"\n')
    _native_child(fixture)
    result = _make(fixture, watched_trace_option, watched_trace_uv=_real_uv(fixture))
    assert result.returncode == 0, result.stdout + result.stderr
    status = _status(fixture)
    assert status["state"] == "passed" and status["exit_code"] == 0
    assert _child_payload(fixture)["argv"] == ["literal child argument"]
