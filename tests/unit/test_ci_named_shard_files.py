"""Parity and release-gate tests for the local named CI shards."""

from __future__ import annotations

import importlib.util
import io
import signal
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from coverage import Coverage

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
EXPECTED_SHARDS = (
    "unit-1a1",
    "unit-1a2",
    "unit-1b",
    "unit-1d",
    "unit-2",
    "unit-3",
    "other",
)


def _load_script(name: str) -> Any:
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(f"gludd_{name}", SCRIPTS / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


def test_local_shard_names_match_beta3_ci_matrix() -> None:
    module = _load_script("ci_named_shard_files")

    assert tuple(module.SHARDS) == EXPECTED_SHARDS


def test_local_shard_patterns_match_workflow_matrix() -> None:
    module = _load_script("ci_named_shard_files")
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "build.yml").read_text())
    include = workflow["jobs"]["test-shard"]["strategy"]["matrix"]["include"]
    workflow_shards = {
        item["shard"]: (
            tuple(item["testpaths"].split()),
            tuple(item.get("exclude", "").split()),
        )
        for item in include
        if "testpaths" in item
    }

    assert workflow_shards == module.SHARDS


def test_local_unit_1a1_excludes_isolated_node_runtime_suite() -> None:
    module = _load_script("ci_named_shard_files")
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "build.yml").read_text())
    unit_1a1 = next(
        item
        for item in workflow["jobs"]["test-shard"]["strategy"]["matrix"]["include"]
        if item["shard"] == "unit-1a1"
    )

    assert "tests/unit/test_all_plugins_runtime.py" not in module.expand_shard("unit-1a1")
    assert "*/test_all_plugins_runtime.py" in module.SHARDS["unit-1a1"][1]
    assert module.ISOLATED_TESTS == ("tests/unit/test_all_plugins_runtime.py",)
    assert tuple(str(unit_1a1["isolated_testpaths"]).split()) == module.ISOLATED_TESTS


def test_every_unit_test_file_has_exactly_one_execution_lane() -> None:
    module = _load_script("ci_named_shard_files")
    selected: dict[str, set[str]] = {}
    for shard in EXPECTED_SHARDS:
        files: set[str] = set()
        for token in module.expand_shard(shard):
            path = ROOT / token
            if path.is_dir():
                files.update(item.relative_to(ROOT).as_posix() for item in path.rglob("test_*.py"))
            else:
                files.add(token)
        selected[shard] = files
    selected["isolated"] = set(module.ISOLATED_TESTS)

    for path in sorted((ROOT / "tests" / "unit").rglob("test_*.py")):
        relative = path.relative_to(ROOT).as_posix()
        owners = [shard for shard, files in selected.items() if relative in files]
        assert len(owners) == 1, f"{relative} belongs to {owners}, expected exactly one shard"


def test_shard_slice_supports_inclusive_boundaries() -> None:
    module = _load_script("ci_named_shard_files")
    paths = ["a.py", "b.py", "c.py", "d.py"]

    assert module.slice_paths(paths, from_path="b.py", to_path="c.py") == [
        "b.py",
        "c.py",
    ]


def test_shard_slice_supports_exclusive_boundaries() -> None:
    module = _load_script("ci_named_shard_files")
    paths = ["a.py", "b.py", "c.py", "d.py"]

    assert module.slice_paths(paths, after_path="a.py", before_path="d.py") == [
        "b.py",
        "c.py",
    ]


def test_shard_slice_fails_closed_for_unknown_boundary() -> None:
    module = _load_script("ci_named_shard_files")

    with pytest.raises(SystemExit, match="not present"):
        module.slice_paths(["a.py"], from_path="missing.py")


def test_serial_gate_runner_is_fresh_process_and_coverage_complete() -> None:
    runner = SCRIPTS / "run_ci_shards_serial.py"
    assert runner.is_file()
    source = runner.read_text(encoding="utf-8")

    for token in (
        "MAX_FILES_PER_BATCH",
        "--max-worker-restart=0",
        "start_new_session=True",
        "COVERAGE_FILE",
        "coverage combine",
        "--cov=general_ludd",
        "--cov-fail-under=0",
        "coverage report",
        "--fail-under=85",
        "audit_coverage.py",
        "--threshold=75",
    ):
        assert token in source


def test_run_gate_delegates_to_serial_named_shards() -> None:
    source = (SCRIPTS / "run_gate.sh").read_text(encoding="utf-8")

    assert "run_ci_shards_serial.py" in source


def test_serial_pytest_command_uses_one_fail_closed_worker_and_isolated_basetemp(
    tmp_path: Path,
) -> None:
    module = _load_script("run_ci_shards_serial")

    command = module._pytest_command(
        "unit-2", ["tests/unit/test_alpha.py"], tmp_path, ["-q"]
    )
    greenlet_command = module._pytest_command(
        "unit-3", ["tests/unit/test_zeta.py"], tmp_path, ["-q"]
    )

    assert command[0] == sys.executable
    assert command[1:3] == ["-m", "pytest"]
    assert "tests/unit/test_alpha.py" in command
    assert command[command.index("-n") + 1] == "1"
    assert command[command.index("--maxprocesses") + 1] == "1"
    assert "--max-worker-restart=0" in command
    assert "--cov=general_ludd" in command
    assert (
        "--cov=collections/ansible_collections/general_ludd/governance/plugins/module_utils"
        in command
    )
    coverage_config = module.ROOT / ".coveragerc-greenlet"
    assert f"--cov-config={coverage_config}" in command
    assert f"--cov-config={coverage_config}" in greenlet_command
    assert Coverage(config_file=str(coverage_config)).get_option("run:branch") is True
    assert f"--basetemp={tmp_path / 'pytest'}" in command


def test_serial_partition_expands_directories_deduplicates_and_bounds_batches(
    tmp_path: Path,
) -> None:
    module = _load_script("run_ci_shards_serial")
    suite = tmp_path / "tests" / "suite"
    nested = suite / "nested"
    nested.mkdir(parents=True)
    for relative in ("test_a.py", "test_b.py", "nested/test_c.py", "nested/sample_test.py"):
        path = suite / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_ok(): pass\n", encoding="utf-8")

    batches = module._partition_test_paths(
        ["tests/suite", "tests/suite/test_b.py", "tests/test_z.py"],
        max_files=2,
        root=tmp_path,
    )

    assert batches == [
        ["tests/suite/nested/sample_test.py", "tests/suite/nested/test_c.py"],
        ["tests/suite/test_a.py", "tests/suite/test_b.py"],
        ["tests/test_z.py"],
    ]
    assert all(1 <= len(batch) <= 2 for batch in batches)


def test_owned_pytest_runner_fails_closed_on_worker_death(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")

    class FinishedProcess:
        pid = 42420
        returncode = 0
        stdout = io.StringIO("[gw0] node down: Not properly terminated\n")

        def poll(self) -> int:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

    process = FinishedProcess()
    popen_kwargs: dict[str, object] = {}

    def fake_popen(_command: list[str], **kwargs: object) -> object:
        popen_kwargs.update(kwargs)
        return process

    terminated: list[object] = []
    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        module,
        "_terminate_owned_process",
        lambda owned, **_kwargs: terminated.append(owned),
    )

    rc = module._run_owned_pytest(
        ["pytest"],
        env={},
        label="unit-3:batch-1",
        heartbeat_seconds=1.0,
        no_progress_seconds=5.0,
    )

    assert rc == module.WORKER_DEATH_EXIT_CODE
    assert terminated == [process]
    assert popen_kwargs["start_new_session"] is True
    assert "WORKER-DEATH" in capsys.readouterr().out


@pytest.mark.parametrize(
    "line",
    [
        "[gw2] node down: Not properly terminated",
        "worker gw2 crashed and worker restarting disabled",
        "maximum crashed workers reached: 0",
        "===== xdist: maximum crashed workers reached: 0 =====",
    ],
)
def test_xdist_worker_death_parser_accepts_complete_control_lines(line: str) -> None:
    module = _load_script("run_ci_shards_serial")

    assert module._is_xdist_worker_death_line(line) is True


@pytest.mark.parametrize(
    "line",
    [
        "tests/unit/test_adaptive_test.py::test_is_oom_exit_output_markers"
        "[[gw2] node down: Not properly terminated]",
        "payload=[gw2] node down: Not properly terminated",
        "stdout says worker gw2 crashed and worker restarting disabled",
        "assert 'maximum crashed workers reached: 0' in output",
    ],
)
def test_xdist_worker_death_parser_rejects_test_ids_payload_and_stdout(
    line: str,
) -> None:
    module = _load_script("run_ci_shards_serial")

    assert module._is_xdist_worker_death_line(line) is False


def test_owned_pytest_runner_ignores_marker_inside_parameterized_node_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")

    class FinishedProcess:
        pid = 42423
        returncode = 0
        stdout = io.StringIO(
            "tests/unit/test_adaptive_test.py::test_is_oom_exit_output_markers"
            "[[gw2] node down: Not properly terminated] PASSED\n"
        )

        def poll(self) -> int:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            return self.returncode

    process = FinishedProcess()
    terminated: list[object] = []
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        module,
        "_terminate_owned_process",
        lambda owned, **_kwargs: terminated.append(owned),
    )
    monkeypatch.setattr(module, "_owned_process_group_alive", lambda _process: False)

    rc = module._run_owned_pytest(
        ["pytest"],
        env={},
        label="unit-3:batch-node-id",
        heartbeat_seconds=1.0,
        no_progress_seconds=5.0,
    )

    assert rc == 0
    assert terminated == []


def test_owned_cleanup_escalates_term_to_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")

    class RetainedProcess:
        pid = 42421

        @staticmethod
        def wait(timeout: float | None = None) -> int:
            return 0

    signals: list[signal.Signals] = []
    monkeypatch.setattr(module, "_owned_process_group_alive", lambda _process: True)
    monkeypatch.setattr(
        module,
        "_signal_owned_process_group",
        lambda _process, signum: signals.append(signum),
    )

    module._terminate_owned_process(RetainedProcess(), grace_seconds=0.0)

    assert signals == [signal.SIGTERM, signal.SIGKILL]


@pytest.mark.parametrize("error_type", [ProcessLookupError, PermissionError])
def test_owned_cleanup_is_idempotent_when_group_is_gone_or_inaccessible(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[OSError],
) -> None:
    module = _load_script("run_ci_shards_serial")

    class ExitedProcess:
        pid = 42424
        returncode = 0
        wait_calls = 0

        def wait(self, timeout: float | None = None) -> int:
            self.wait_calls += 1
            return self.returncode

    process = ExitedProcess()

    def missing_group(_pid: int, _signum: signal.Signals | int) -> None:
        raise error_type

    monkeypatch.setattr(module.os, "killpg", missing_group)

    module._terminate_owned_process(process, grace_seconds=0.0)
    module._terminate_owned_process(process, grace_seconds=0.0)

    assert process.wait_calls == 2


def test_owned_cleanup_does_not_signal_an_already_exited_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")

    class ExitedProcess:
        pid = 42425

        @staticmethod
        def wait(timeout: float | None = None) -> int:
            return 0

    signals: list[signal.Signals] = []
    monkeypatch.setattr(module, "_owned_process_group_alive", lambda _process: False)
    monkeypatch.setattr(
        module,
        "_signal_owned_process_group",
        lambda _process, signum: signals.append(signum),
    )

    module._terminate_owned_process(ExitedProcess(), grace_seconds=0.0)
    module._terminate_owned_process(ExitedProcess(), grace_seconds=0.0)

    assert signals == []


def test_owned_cleanup_contains_permission_race_during_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")

    class Process:
        pid = 42426

        @staticmethod
        def wait(timeout: float | None = None) -> int:
            return 0

    monkeypatch.setattr(module, "_owned_process_group_alive", lambda _process: True)

    def inaccessible(_process: object, _signum: signal.Signals) -> None:
        raise PermissionError

    monkeypatch.setattr(module, "_signal_owned_process_group", inaccessible)

    module._terminate_owned_process(Process(), grace_seconds=0.0)


def test_serial_runner_uses_unique_batches_and_stops_after_worker_death(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    workspace = tmp_path / "unit-3-workspace"
    batch_runs: list[tuple[str, str]] = []

    def fake_run_owned(
        command: list[str],
        *,
        env: dict[str, str],
        label: str,
        **_kwargs: object,
    ) -> int:
        if "tests/unit/test_all_plugins_runtime.py" in command:
            return 0
        basetemp = next(arg for arg in command if arg.startswith("--basetemp="))
        batch_runs.append((basetemp, env["GLUDD_SHARD_STATE_DIR"]))
        return 0 if len(batch_runs) == 1 else module.WORKER_DEATH_EXIT_CODE

    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda **_kwargs: str(workspace))
    monkeypatch.setattr(
        module,
        "expand_shard",
        lambda _shard: [f"tests/unit/test_{index}.py" for index in range(5)],
    )
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_run_owned_pytest", fake_run_owned)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: True)
    monkeypatch.setattr(module, "_aggregate_coverage", lambda: 0)

    result = module.run(["unit-3"], [], max_files_per_batch=2)

    assert result == module.WORKER_DEATH_EXIT_CODE
    assert len(batch_runs) == 2, "worker death must not retry or launch later batches"
    assert len({basetemp for basetemp, _state in batch_runs}) == 2
    assert len({state for _basetemp, state in batch_runs}) == 2


def test_serial_runner_uses_a_fresh_non_coverage_process_for_isolated_tests() -> None:
    module = _load_script("run_ci_shards_serial")

    command = module._isolated_pytest_command([])

    assert command == [
        sys.executable,
        "-m",
        "pytest",
        "tests/unit/test_all_plugins_runtime.py",
        "-v",
    ]
    assert all(not argument.startswith("--cov") for argument in command)


def test_serial_runner_continues_after_a_failed_shard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    launched: list[str] = []
    temp_index = 0

    def fake_mkdtemp(*, prefix: str, dir: str) -> str:
        nonlocal temp_index
        temp_index += 1
        path = tmp_path / f"{prefix}{temp_index}"
        path.mkdir()
        return str(path)

    def fake_run(
        command: list[str], *, env: dict[str, str] | None = None
    ) -> int:
        return 0

    def fake_run_owned(command: list[str], **_kwargs: object) -> int:
        joined = " ".join(command)
        if "tests/unit/test_all_plugins_runtime.py" in command:
            return 0
        shard = "unit-1a1" if "unit-1a1.py" in joined else "unit-1a2"
        launched.append(shard)
        return 1 if shard == "unit-1a1" else 0

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(module, "expand_shard", lambda shard: [f"tests/{shard}.py"])
    monkeypatch.setattr(
        module,
        "_env_for_shard",
        lambda shard, basetemp: {"COVERAGE_FILE": str(basetemp / ".coverage")},
    )
    monkeypatch.setattr(module, "_run_command", fake_run)
    monkeypatch.setattr(module, "_run_owned_pytest", fake_run_owned)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *args: True)
    monkeypatch.setattr(module, "_aggregate_coverage", lambda: 0)

    result = module.run(["unit-1a1", "unit-1a2"], [])

    assert result == 1
    assert launched == ["unit-1a1", "unit-1a2"]


def test_serial_runner_records_isolated_failure_and_continues_shards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    shard_launched = False

    def fake_run(
        command: list[str], *, env: dict[str, str] | None = None
    ) -> int:
        return 0

    def fake_run_owned(command: list[str], **_kwargs: object) -> int:
        nonlocal shard_launched
        if "tests/unit/test_all_plugins_runtime.py" in command:
            return 7
        if "-m" in command and "pytest" in command:
            shard_launched = True
        return 0

    monkeypatch.setattr(module, "_run_command", fake_run)
    monkeypatch.setattr(module, "_run_owned_pytest", fake_run_owned)
    monkeypatch.setattr(module, "expand_shard", lambda shard: [f"tests/{shard}.py"])
    monkeypatch.setattr(
        module,
        "_env_for_shard",
        lambda shard, basetemp: {"COVERAGE_FILE": str(basetemp / ".coverage")},
    )
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *args: True)
    monkeypatch.setattr(module, "_aggregate_coverage", lambda: 0)

    result = module.run(["unit-1a1"], [])

    assert result == 7
    assert shard_launched is True


def test_serial_runner_fails_closed_when_coverage_erase_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    expanded = False

    def fake_expand(shard: str) -> list[str]:
        nonlocal expanded
        expanded = True
        return [f"tests/{shard}.py"]

    monkeypatch.setattr(module, "expand_shard", fake_expand)
    monkeypatch.setattr(module, "_run_command", lambda *args, **kwargs: 2)

    result = module.run(["unit-1a1"], [])

    assert result == 2
    assert expanded is False


def test_owned_pytest_runner_times_out_silent_worker_and_emits_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")

    class SilentProcess:
        pid = 42422
        returncode: int | None = None
        stdout = io.StringIO("")

        def poll(self) -> int | None:
            return self.returncode

        def wait(self, timeout: float | None = None) -> int:
            assert self.returncode is not None
            return self.returncode

    process = SilentProcess()

    def terminate(owned: SilentProcess, **_kwargs: object) -> None:
        assert owned is process
        owned.returncode = -signal.SIGTERM

    clock = iter((0.0, 2.0))
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(module, "_terminate_owned_process", terminate)
    monkeypatch.setattr(module, "_owned_process_group_alive", lambda _process: False)

    rc = module._run_owned_pytest(
        ["pytest"],
        env={},
        label="unit-3:batch-quiet",
        heartbeat_seconds=1.0,
        no_progress_seconds=1.0,
    )

    output = capsys.readouterr().out
    assert rc == module.NO_PROGRESS_EXIT_CODE
    assert "SHARD-HEARTBEAT" in output
    assert "SHARD-NO-PROGRESS" in output


def test_shard_coverage_fragment_and_aggregate_preserve_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "fragments"
    module.COVERAGE_SHARDS.mkdir()
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "audit.json"
    batchtemp = tmp_path / "batch"
    batchtemp.mkdir()
    coverage_file = batchtemp / ".coverage"
    coverage_file.write_bytes(b"coverage-data")
    env = {"COVERAGE_FILE": str(coverage_file)}

    assert module._save_shard_coverage("unit-3", 2, batchtemp, env) is True
    assert (module.COVERAGE_SHARDS / ".coverage.unit-3.batch-002").read_bytes() == (
        b"coverage-data"
    )

    commands: list[list[str]] = []

    def run_command(
        command: list[str], *, env: dict[str, str] | None = None
    ) -> int:
        commands.append(command)
        return 3 if "xml" in command else 0

    monkeypatch.setattr(module, "_run_command", run_command)

    assert module._aggregate_coverage() == 3
    assert len(commands) == 5
    assert "--max-worker-restart=0" not in " ".join(
        argument for command in commands for argument in command
    )
    assert any("--threshold=75" in command for command in commands)


def test_missing_shard_coverage_attempts_combine_then_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "fragments"
    module.COVERAGE_SHARDS.mkdir()
    batchtemp = tmp_path / "batch"
    batchtemp.mkdir()
    commands: list[list[str]] = []

    def run_command(command: list[str], **_kwargs: object) -> int:
        commands.append(command)
        return 0

    monkeypatch.setattr(module, "_run_command", run_command)

    assert (
        module._save_shard_coverage(
            "unit-3",
            1,
            batchtemp,
            {"COVERAGE_FILE": str(batchtemp / ".coverage")},
        )
        is False
    )
    assert commands == [
        [sys.executable, "-m", "coverage", "combine", str(batchtemp)]
    ]


def test_serial_runner_cli_forwards_explicit_resource_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    received: dict[str, object] = {}

    def run(
        shards: list[str],
        pytest_args: list[str],
        **kwargs: object,
    ) -> int:
        received.update(shards=shards, pytest_args=pytest_args, **kwargs)
        return 9

    monkeypatch.setattr(module, "run", run)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "run_ci_shards_serial.py",
            "--shards=unit-2,unit-3",
            "--pytest-args=-q -W error",
            "--max-files-per-batch=17",
            "--heartbeat-seconds=4",
            "--no-progress-seconds=23",
        ],
    )

    assert module.main() == 9
    assert received == {
        "shards": ["unit-2", "unit-3"],
        "pytest_args": ["-q", "-W", "error"],
        "max_files_per_batch": 17,
        "heartbeat_seconds": 4.0,
        "no_progress_seconds": 23.0,
    }
