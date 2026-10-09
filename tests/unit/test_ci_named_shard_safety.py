"""Tests split from :mod:`tests.unit.test_ci_named_shard_files` by coherent behavior."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import signal
import sys
from pathlib import Path

import pytest
from coverage import CoverageData

from tests.unit.test_ci_named_shard_files import (
    ROOT,
    _load_script,
)


@pytest.mark.parametrize(
    ("scenario", "later_rc", "later_phase"),
    [
        ("disk", 73, "unit-1a1:batch-002"),
        ("interpreter", 78, "unit-1a1:batch-002"),
        ("worker", 70, "unit-1a1:batch-002"),
        ("worker-cleanup", 70, "unit-1a1:batch-002"),
        ("no-progress", 124, "unit-1a1:batch-002"),
        ("cancellation", 130, "unit-1a1:batch-002"),
    ],
)
def test_first_batch_failure_prevents_every_later_safety_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scenario: str,
    later_rc: int,
    later_phase: str,
) -> None:
    module = _load_script("run_ci_shards_serial")
    resources = module.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "attestation.json",
    )
    module.COVERAGE_SHARDS = resources.coverage_shards
    module.COVERAGE_JSON = resources.coverage_json
    module.COVERAGE_AUDIT = resources.coverage_audit
    launched: list[str] = []
    disk_checks = 0
    interpreter_checks = 0
    cleanup_calls = 0
    temp_index = 0

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        nonlocal temp_index
        del dir
        temp_index += 1
        path = tmp_path / f"{prefix}{temp_index}"
        path.mkdir()
        return str(path)

    def disk_available(*_args: object, **_kwargs: object) -> bool:
        nonlocal disk_checks
        disk_checks += 1
        return scenario != "disk" or disk_checks != 2

    def interpreter_unchanged(*_args: object, **_kwargs: object) -> bool:
        nonlocal interpreter_checks
        interpreter_checks += 1
        return scenario != "interpreter" or interpreter_checks != 3

    def run_owned(*_args: object, label: str, **_kwargs: object) -> int:
        launched.append(label)
        if len(launched) == 1:
            return 6
        return int(
            {
                "worker": module.WORKER_DEATH_EXIT_CODE,
                "worker-cleanup": module.WORKER_DEATH_EXIT_CODE,
                "no-progress": module.NO_PROGRESS_EXIT_CODE,
                "cancellation": 128 + signal.SIGINT,
            }.get(scenario, 0)
        )

    def cleanup(_path: Path) -> int:
        nonlocal cleanup_calls
        cleanup_calls += 1
        if cleanup_calls == 2 and scenario in {
            "cleanup",
            "cancellation",
            "worker-cleanup",
        }:
            return 9
        return 0

    monkeypatch.setattr(module, "_resource_paths", lambda: resources)
    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(
        module,
        "expand_shard",
        lambda shard: [f"tests/{shard}-{index}.py" for index in range(3)],
    )
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_disk_headroom_available", disk_available)
    monkeypatch.setattr(module, "_interpreter_is_unchanged", interpreter_unchanged)
    monkeypatch.setattr(module, "_run_owned_pytest", run_owned)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: True)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", cleanup)

    result = module.run(
        ["unit-1a1", "unit-1a2"],
        [],
        max_files_per_batch=1,
        run_isolated=False,
        aggregate_coverage=False,
    )

    output = capsys.readouterr().out
    assert result == 6
    assert launched == ["unit-1a1:batch-001"]
    assert "'unit-1a1:batch-001': 6" in output
    assert f"'{later_phase}': {later_rc}" not in output
    assert "later-batches=not-started" in output


def test_failed_batch_without_coverage_fragment_stops_before_later_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    launched: list[str] = []
    saved: list[int] = []
    aggregate_called = False
    temp_index = 0

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        nonlocal temp_index
        del dir
        temp_index += 1
        path = tmp_path / f"{prefix}{temp_index}"
        path.mkdir()
        return str(path)

    def run_owned(*_args: object, label: str, **_kwargs: object) -> int:
        launched.append(label)
        return 2 if label.endswith("batch-001") else 0

    def save_coverage(_shard: str, batch: int, *_args: object) -> bool:
        saved.append(batch)
        return batch != 1

    def aggregate() -> int:
        nonlocal aggregate_called
        aggregate_called = True
        return 0

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", lambda _path: 0)
    monkeypatch.setattr(
        module,
        "expand_shard",
        lambda _shard: ["tests/a.py", "tests/b.py"],
    )
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_run_owned_pytest", run_owned)
    monkeypatch.setattr(module, "_save_shard_coverage", save_coverage)
    monkeypatch.setattr(module, "_aggregate_coverage", aggregate)

    result = module.run(
        ["unit-1a1"],
        [],
        max_files_per_batch=1,
        run_isolated=False,
    )

    assert result == 2
    assert launched == ["unit-1a1:batch-001"]
    assert saved == [1]
    assert aggregate_called is False


def test_serial_runner_collects_shards_after_isolated_test_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    launched: list[str] = []

    def fake_run_owned(command: list[str], *, label: str, **_kwargs: object) -> int:
        del command
        launched.append(label)
        return 1 if label == "isolated" else 0

    monkeypatch.setattr(module, "expand_shard", lambda shard: [f"tests/{shard}.py"])
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_run_owned_pytest", fake_run_owned)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: True)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", lambda _path: None)
    monkeypatch.setattr(module, "_aggregate_coverage", lambda: 0)

    result = module.run(["unit-1a1", "unit-1a2"], [])

    assert result == 1
    assert launched == ["isolated", "unit-1a1:batch-001", "unit-1a2:batch-001"]


def test_serial_runner_stops_on_empty_shard_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    launched: list[str] = []
    identity = {
        "implementation": "cpython",
        "version": "3.11.14",
        "executable": "/opt/python/3.11/bin/python3.11",
    }

    monkeypatch.setattr(module, "_interpreter_identity", lambda: identity)
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        module,
        "expand_shard",
        lambda shard: [] if shard == "unit-1a1" else ["tests/later.py"],
    )

    def run_owned(*_args: object, label: str, **_kwargs: object) -> int:
        launched.append(label)
        return 0

    monkeypatch.setattr(
        module,
        "_run_owned_pytest",
        run_owned,
    )

    result = module.run(
        ["unit-1a1", "unit-1a2"],
        [],
        run_isolated=False,
        aggregate_coverage=False,
    )

    assert result == 2
    assert launched == []


def test_serial_runner_rejects_a_completely_empty_plan_before_setup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    setup_calls: list[str] = []

    def record_interpreter_setup() -> dict[str, object]:
        setup_calls.append("interpreter")
        return {}

    def record_command_setup(*_args: object, **_kwargs: object) -> int:
        setup_calls.append("command")
        return 0

    monkeypatch.setattr(
        module,
        "_interpreter_identity",
        record_interpreter_setup,
    )
    monkeypatch.setattr(
        module,
        "_run_command",
        record_command_setup,
    )

    assert module.run([], [], run_isolated=False, aggregate_coverage=False) == 2
    assert setup_calls == []
    output = capsys.readouterr().out
    assert "SERIAL-SHARD-PLAN-EMPTY rc=2" in output
    assert 'phases={"plan": 2}' in output


def test_serial_runner_validate_only_rejects_a_completely_empty_plan(
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")

    assert (
        module._validate_only_plan(
            [],
            [],
            max_files_per_batch=1,
            attestation_output=None,
        )
        == 2
    )
    assert "SERIAL-SHARD-VALIDATE-FAIL empty=<plan>" in capsys.readouterr().out


def test_serial_runner_classifies_workspace_creation_io_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    resources = module.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "attestation.json",
    )
    module.COVERAGE_SHARDS = resources.coverage_shards
    module.COVERAGE_AUDIT = resources.coverage_audit
    monkeypatch.setattr(module, "_resource_paths", lambda: resources)
    monkeypatch.setattr(module, "expand_shard", lambda _shard: ["tests/test_one.py"])
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)

    def fail_workspace(*_args: object, **_kwargs: object) -> str:
        raise OSError("workspace volume is unavailable")

    monkeypatch.setattr(module.tempfile, "mkdtemp", fail_workspace)

    assert (
        module.run(
            ["unit-1a1"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
        )
        == module.CLEANUP_FAILURE_EXIT_CODE
    )
    output = capsys.readouterr().out
    assert "SHARD-RESOURCE-SETUP-FAIL" in output
    assert f'"unit-1a1:workspace-setup": {module.CLEANUP_FAILURE_EXIT_CODE}' in output


def test_serial_runner_classifies_batch_workspace_io_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    resources = module.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "attestation.json",
    )
    module.COVERAGE_SHARDS = resources.coverage_shards
    module.COVERAGE_AUDIT = resources.coverage_audit
    monkeypatch.setattr(module, "_resource_paths", lambda: resources)
    monkeypatch.setattr(module, "expand_shard", lambda _shard: ["tests/test_one.py"])
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)

    def occupied_batch(*, prefix: str, dir: str | Path) -> str:
        workspace = Path(dir) / f"{prefix}owned"
        workspace.mkdir()
        (workspace / "batch-001").write_text("not a directory")
        return str(workspace)

    monkeypatch.setattr(module.tempfile, "mkdtemp", occupied_batch)

    assert (
        module.run(
            ["unit-1a1"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
        )
        == module.CLEANUP_FAILURE_EXIT_CODE
    )
    output = capsys.readouterr().out
    assert "SHARD-RESOURCE-SETUP-FAIL" in output
    assert f'"unit-1a1:batch-001:setup": {module.CLEANUP_FAILURE_EXIT_CODE}' in output


def test_serial_runner_classifies_socket_tmpdir_io_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    resources = module.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "attestation.json",
    )
    module.COVERAGE_SHARDS = resources.coverage_shards
    module.COVERAGE_AUDIT = resources.coverage_audit
    monkeypatch.setattr(module, "_resource_paths", lambda: resources)
    monkeypatch.setattr(module, "expand_shard", lambda _shard: ["tests/test_one.py"])
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_disk_headroom_available", lambda *_args, **_kwargs: True)

    def fail_socket_root(*, prefix: str, dir: str | Path) -> str:
        if prefix.startswith("gludd-gate-"):
            workspace = Path(dir) / f"{prefix}owned"
            workspace.mkdir()
            return str(workspace)
        raise OSError("socket temp volume is unavailable")

    monkeypatch.setattr(module.tempfile, "mkdtemp", fail_socket_root)

    assert (
        module.run(
            ["unit-1a1"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
        )
        == module.CLEANUP_FAILURE_EXIT_CODE
    )
    output = capsys.readouterr().out
    assert "SHARD-RESOURCE-SETUP-FAIL" in output
    assert f'"unit-1a1:batch-001:tmpdir-setup": {module.CLEANUP_FAILURE_EXIT_CODE}' in output


def test_coverage_setup_failure_cleans_partially_created_fragments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-fragments"
    occupied_parent = tmp_path / "occupied-audit-parent"
    occupied_parent.write_text("not a directory")
    module.COVERAGE_AUDIT = occupied_parent / "coverage.json"
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)

    assert (
        module.run(
            ["unit-1a1"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
        )
        == module.CLEANUP_FAILURE_EXIT_CODE
    )
    assert not module.COVERAGE_SHARDS.exists()
    output = capsys.readouterr().out
    assert f'"coverage:setup": {module.CLEANUP_FAILURE_EXIT_CODE}' in output
    assert '"coverage:fragments-cleanup": 0' in output


def test_coverage_erase_failure_yields_to_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-fragments"
    module.COVERAGE_AUDIT = tmp_path / "coverage-audit.json"
    cleanup_results = iter((0, module.CLEANUP_FAILURE_EXIT_CODE))
    monkeypatch.setattr(
        module,
        "_remove_owned_tree",
        lambda *_args, **_kwargs: next(cleanup_results),
    )
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 5)

    assert (
        module.run(
            ["unit-1a1"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
        )
        == module.CLEANUP_FAILURE_EXIT_CODE
    )
    output = capsys.readouterr().out
    assert '"coverage:erase": 5' in output
    assert f'"coverage:fragments-cleanup": {module.CLEANUP_FAILURE_EXIT_CODE}' in output


def test_serial_runner_converts_cleanup_io_error_without_masking_test_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    temp_index = 0

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        nonlocal temp_index
        del dir
        temp_index += 1
        path = tmp_path / f"{prefix}{temp_index}"
        path.mkdir()
        return str(path)

    def cleanup_failure(_path: Path) -> int:
        raise OSError("owned cleanup denied")

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(module, "expand_shard", lambda _shard: ["tests/failing.py"])
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_run_owned_pytest", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: True)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", cleanup_failure)

    result = module.run(
        ["unit-1a1"],
        [],
        run_isolated=False,
        aggregate_coverage=False,
    )

    assert result == module.CLEANUP_FAILURE_EXIT_CODE
    output = capsys.readouterr().out
    assert "SHARD-CLEANUP-FAIL" in output
    assert '"unit-1a1:batch-001": 1' in output
    assert f'"unit-1a1:batch-001:cleanup": {module.CLEANUP_FAILURE_EXIT_CODE}' in output


@pytest.mark.parametrize(
    ("scenario", "expected_rc", "expected_batches"),
    [
        ("disk", 73, []),
        ("interpreter", 78, []),
        ("coverage", 1, ["unit-1a1:batch-001"]),
    ],
)
def test_serial_runner_preserves_immediate_resource_safety_stops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    expected_rc: int,
    expected_batches: list[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    resources = module.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "attestation.json",
    )
    module.COVERAGE_SHARDS = resources.coverage_shards
    module.COVERAGE_JSON = resources.coverage_json
    module.COVERAGE_AUDIT = resources.coverage_audit
    identity = {
        "implementation": "cpython",
        "version": "3.11.14",
        "executable": "/opt/python/3.11/bin/python3.11",
    }
    launched: list[str] = []
    temp_index = 0

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        nonlocal temp_index
        del dir
        temp_index += 1
        path = tmp_path / f"{prefix}{temp_index}"
        path.mkdir(parents=True)
        return str(path)

    def cleanup(path: Path) -> int:
        shutil.rmtree(path, ignore_errors=True)
        return 0

    def run_owned(*_args: object, label: str, **_kwargs: object) -> int:
        launched.append(label)
        return 0

    monkeypatch.setattr(module, "_resource_paths", lambda: resources)
    monkeypatch.setattr(module, "_interpreter_identity", lambda: identity)
    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(module, "expand_shard", lambda shard: [f"tests/{shard}.py"])
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        module,
        "_disk_headroom_available",
        lambda *_args, **_kwargs: scenario != "disk",
    )
    monkeypatch.setattr(
        module,
        "_interpreter_is_unchanged",
        lambda *_args, **_kwargs: scenario != "interpreter",
    )
    monkeypatch.setattr(module, "_run_owned_pytest", run_owned)
    monkeypatch.setattr(
        module,
        "_save_shard_coverage",
        lambda *_args: scenario != "coverage",
    )
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", cleanup)

    result = module.run(
        ["unit-1a1", "unit-1a2"],
        [],
        run_isolated=False,
        aggregate_coverage=False,
    )

    assert result == expected_rc
    assert launched == expected_batches


def test_serial_runner_records_isolated_failure_and_stops_before_shards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    shard_launched = False

    def fake_run(command: list[str], *, env: dict[str, str] | None = None) -> int:
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
    assert shard_launched is False


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
    assert expanded is True


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
    source = str(ROOT / "src" / "general_ludd" / "__init__.py")
    data = CoverageData(basename=str(coverage_file))
    data.add_lines({source: {1}})
    data.write()
    expected_coverage = coverage_file.read_bytes()
    env = {"COVERAGE_FILE": str(coverage_file)}

    assert module._save_shard_coverage("unit-3", 2, batchtemp, env) is True
    assert (module.COVERAGE_SHARDS / ".coverage.unit-3.batch-002").read_bytes() == (expected_coverage)

    commands: list[list[str]] = []

    def run_command(command: list[str], *, env: dict[str, str] | None = None) -> int:
        commands.append(command)
        return 3 if "xml" in command else 0

    monkeypatch.setattr(module, "_run_command", run_command)

    assert module._aggregate_coverage() == 3
    assert len(commands) == 5
    assert "--max-worker-restart=0" not in " ".join(argument for command in commands for argument in command)
    assert any("--threshold=85" in command for command in commands)
    assert any("--per-file-threshold=75" in command for command in commands)


def test_save_shard_coverage_rejects_corrupt_nonempty_database(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "fragments"
    module.COVERAGE_SHARDS.mkdir()
    batchtemp = tmp_path / "batch"
    batchtemp.mkdir()
    coverage_file = batchtemp / ".coverage"
    coverage_file.write_bytes(b"not-a-coverage-sqlite-database")

    assert (
        module._save_shard_coverage(
            "unit-1a1",
            1,
            batchtemp,
            {"COVERAGE_FILE": str(coverage_file)},
        )
        is False
    )
    assert not (module.COVERAGE_SHARDS / ".coverage.unit-1a1.batch-001").exists()
    assert "SHARD-COVERAGE-INVALID" in capsys.readouterr().out


def test_coverage_database_validator_rejects_symlink_and_missing_file(
    tmp_path: Path,
) -> None:
    module = _load_script("run_ci_shards_serial")
    missing = tmp_path / "missing.coverage"
    empty = tmp_path / "empty.coverage"
    empty.write_bytes(b"")
    symlink = tmp_path / "symlink.coverage"
    symlink.symlink_to(missing)

    assert module._coverage_data_error(symlink) == "symbolic links are not accepted"
    assert module._coverage_data_error(missing).startswith("FileNotFoundError:")
    assert module._coverage_data_error(empty) == "file is missing or empty"


def test_coverage_database_validator_classifies_hash_io_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    coverage_file = tmp_path / ".coverage"
    coverage_file.write_bytes(b"nonempty")

    def fail_hash(_path: Path) -> str:
        raise OSError("coverage read failed")

    monkeypatch.setattr(module, "_file_sha256", fail_hash)

    assert module._coverage_data_error(coverage_file).startswith("OSError:")


def test_save_shard_coverage_classifies_copy_io_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "fragments"
    module.COVERAGE_SHARDS.mkdir()
    batchtemp = tmp_path / "batch"
    batchtemp.mkdir()
    coverage_file = batchtemp / ".coverage"
    data = CoverageData(basename=str(coverage_file))
    data.add_lines({str(ROOT / "src" / "general_ludd" / "__init__.py"): {1}})
    data.write()

    def fail_copy(_source: Path, _destination: Path) -> None:
        raise OSError("disk became read-only")

    monkeypatch.setattr(module.shutil, "copy2", fail_copy)

    assert (
        module._save_shard_coverage(
            "unit-1a1",
            1,
            batchtemp,
            {"COVERAGE_FILE": str(coverage_file)},
        )
        is False
    )
    assert not (module.COVERAGE_SHARDS / ".coverage.unit-1a1.batch-001").exists()
    assert "SHARD-COVERAGE-TRANSFER-FAIL" in capsys.readouterr().out


def test_save_shard_coverage_classifies_post_validation_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "fragments"
    module.COVERAGE_SHARDS.mkdir()
    batchtemp = tmp_path / "batch"
    batchtemp.mkdir()
    coverage_file = batchtemp / ".coverage"
    data = CoverageData(basename=str(coverage_file))
    data.add_lines({str(ROOT / "src" / "general_ludd" / "__init__.py"): {1}})
    data.write()
    real_hash = module._file_sha256
    hash_calls = 0

    def fail_third_hash(path: Path) -> str:
        nonlocal hash_calls
        hash_calls += 1
        if hash_calls == 3:
            raise OSError("coverage source vanished")
        digest = real_hash(path)
        assert isinstance(digest, str)
        return digest

    monkeypatch.setattr(module, "_file_sha256", fail_third_hash)

    assert (
        module._save_shard_coverage(
            "unit-1a1",
            1,
            batchtemp,
            {"COVERAGE_FILE": str(coverage_file)},
        )
        is False
    )
    assert not (module.COVERAGE_SHARDS / ".coverage.unit-1a1.batch-001").exists()
    assert "SHARD-COVERAGE-TRANSFER-FAIL" in capsys.readouterr().out


def test_save_shard_coverage_unions_controller_and_worker_data(
    tmp_path: Path,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "durable" / "coverage-fragments"
    module.COVERAGE_SHARDS.mkdir(parents=True)
    batchtemp = tmp_path / "batch"
    batchtemp.mkdir()
    coverage_file = batchtemp / ".coverage"
    source = str(ROOT / "src" / "general_ludd" / "__init__.py")

    controller = CoverageData(basename=str(coverage_file))
    controller.add_lines({source: {1}})
    controller.write()
    worker = CoverageData(basename=str(batchtemp / ".coverage.worker"))
    worker.add_lines({source: {2}})
    worker.write()

    assert module._save_shard_coverage(
        "unit-1b",
        6,
        batchtemp,
        {"COVERAGE_FILE": str(coverage_file)},
    )

    destination = module.COVERAGE_SHARDS / ".coverage.unit-1b.batch-006"
    combined = CoverageData(basename=str(destination))
    combined.read()
    assert set(combined.lines(source) or ()) == {1, 2}


def test_hosted_coverage_transfer_survives_workspace_and_python_suffix(
    tmp_path: Path,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "durable" / "coverage-fragments"
    module.COVERAGE_SHARDS.mkdir(parents=True)
    workspace = tmp_path / "ephemeral" / "batch-006"
    workspace.mkdir(parents=True)
    batch_coverage = workspace / ".coverage"
    source = str(ROOT / "src" / "general_ludd" / "__init__.py")
    data = CoverageData(basename=str(batch_coverage))
    data.add_lines({source: {1, 2}})
    data.write()

    assert module._save_shard_coverage(
        "unit-1a2",
        6,
        workspace,
        {"COVERAGE_FILE": str(batch_coverage)},
    )
    shutil.rmtree(workspace.parent)
    destination = tmp_path / "checkout" / ".coverage.unit-1a2-3.11"

    assert module._combine_coverage_output(destination) == 0

    combined = CoverageData(basename=str(destination))
    combined.read()
    assert source in combined.measured_files()
    fragment = module.COVERAGE_SHARDS / ".coverage.unit-1a2.batch-006"
    assert fragment.is_file()
    assert not list(module.COVERAGE_SHARDS.glob(f"{destination.name}.fragment-*"))


@pytest.mark.parametrize("create_directory", [False, True])
def test_coverage_output_fails_before_combine_when_no_fragments_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    create_directory: bool,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-fragments"
    if create_directory:
        module.COVERAGE_SHARDS.mkdir()
    commands: list[list[str]] = []

    def record_command(command: list[str], **_kwargs: object) -> int:
        commands.append(command)
        return 0

    monkeypatch.setattr(module, "_run_command", record_command)

    assert module._combine_coverage_output(tmp_path / ".coverage.unit-1a2-3.11") == 1
    assert commands == []
    assert "SHARD-COVERAGE-FRAGMENTS-MISSING" in capsys.readouterr().out


def test_coverage_transfer_mismatch_removes_owned_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-fragments"
    module.COVERAGE_SHARDS.mkdir()
    fragment = module.COVERAGE_SHARDS / ".coverage.unit-1a2.batch-006"
    source = str(ROOT / "src" / "general_ludd" / "__init__.py")
    data = CoverageData(basename=str(fragment))
    data.add_lines({source: {1}})
    data.write()

    def truncated_copy(_source: Path, destination: Path) -> None:
        destination.write_bytes(b"")

    monkeypatch.setattr(module.shutil, "copy2", truncated_copy)

    assert module._combine_coverage_output(tmp_path / ".coverage.unit-1a2-3.11") == 1
    assert not list(module.COVERAGE_SHARDS.glob(".coverage.unit-1a2-3.11.fragment-*"))


def test_coverage_output_rejects_an_invalid_fragment_before_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-fragments"
    module.COVERAGE_SHARDS.mkdir()
    fragment = module.COVERAGE_SHARDS / ".coverage.unit-1a2.batch-006"
    fragment.write_bytes(b"corrupt coverage database")
    commands: list[list[str]] = []

    def record_command(command: list[str], **_kwargs: object) -> int:
        commands.append(command)
        return 0

    monkeypatch.setattr(
        module,
        "_run_command",
        record_command,
    )

    assert module._combine_coverage_output(tmp_path / ".coverage.unit-1a2-3.11") == 1
    assert commands == []
    assert "SHARD-COVERAGE-FRAGMENT-INVALID" in capsys.readouterr().out


def test_coverage_output_classifies_destination_setup_io_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-fragments"
    module.COVERAGE_SHARDS.mkdir()
    fragment = module.COVERAGE_SHARDS / ".coverage.unit-1a2.batch-006"
    data = CoverageData(basename=str(fragment))
    data.add_lines({str(ROOT / "src" / "general_ludd" / "__init__.py"): {1}})
    data.write()
    occupied_parent = tmp_path / "occupied-parent"
    occupied_parent.write_text("not a directory")

    assert module._combine_coverage_output(occupied_parent / ".coverage") == 1
    assert "SHARD-COVERAGE-OUTPUT-SETUP-FAIL" in capsys.readouterr().out


def test_coverage_output_classifies_fragment_copy_io_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-fragments"
    module.COVERAGE_SHARDS.mkdir()
    fragment = module.COVERAGE_SHARDS / ".coverage.unit-1a2.batch-006"
    data = CoverageData(basename=str(fragment))
    data.add_lines({str(ROOT / "src" / "general_ludd" / "__init__.py"): {1}})
    data.write()

    def fail_copy(_source: Path, _destination: Path) -> None:
        raise OSError("coverage destination is read-only")

    monkeypatch.setattr(module.shutil, "copy2", fail_copy)

    assert module._combine_coverage_output(tmp_path / ".coverage.unit-1a2-3.11") == 1
    assert not list(module.COVERAGE_SHARDS.glob(".coverage.unit-1a2-3.11.fragment-*"))
    assert "SHARD-COVERAGE-TRANSFER-FAIL" in capsys.readouterr().out


def test_coverage_output_requires_combine_to_create_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-fragments"
    module.COVERAGE_SHARDS.mkdir()
    fragment = module.COVERAGE_SHARDS / ".coverage.unit-1a2.batch-006"
    data = CoverageData(basename=str(fragment))
    data.add_lines({str(ROOT / "src" / "general_ludd" / "__init__.py"): {1}})
    data.write()
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)

    assert module._combine_coverage_output(tmp_path / ".coverage.unit-1a2-3.11") == 1
    assert not list(module.COVERAGE_SHARDS.glob(".coverage.unit-1a2-3.11.fragment-*"))
    assert "SHARD-COVERAGE-OUTPUT-MISSING" in capsys.readouterr().out


def test_coverage_transfer_rejects_same_size_content_corruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-fragments"
    module.COVERAGE_SHARDS.mkdir()
    fragment = module.COVERAGE_SHARDS / ".coverage.unit-1a2.batch-006"
    source = str(ROOT / "src" / "general_ludd" / "__init__.py")
    data = CoverageData(basename=str(fragment))
    data.add_lines({source: {1}})
    data.write()
    commands: list[list[str]] = []

    def corrupt_copy(source_path: Path, destination: Path) -> None:
        payload = bytearray(source_path.read_bytes())
        payload[-1] ^= 1
        destination.write_bytes(payload)

    def record_command(command: list[str], **_kwargs: object) -> int:
        commands.append(command)
        return 0

    monkeypatch.setattr(module.shutil, "copy2", corrupt_copy)
    monkeypatch.setattr(
        module,
        "_run_command",
        record_command,
    )

    assert module._combine_coverage_output(tmp_path / ".coverage.unit-1a2-3.11") == 1
    assert commands == []
    assert not list(module.COVERAGE_SHARDS.glob(".coverage.unit-1a2-3.11.fragment-*"))
    assert "SHARD-COVERAGE-TRANSFER-MISMATCH" in capsys.readouterr().out


def test_coverage_output_evidence_is_hash_bound_and_python_specific(
    tmp_path: Path,
) -> None:
    module = _load_script("run_ci_shards_serial")
    destination = tmp_path / ".coverage.unit-1a2-3.11"
    destination.write_bytes(b"durable-coverage")

    evidence = module._coverage_output_evidence(destination)

    assert evidence == {
        "artifact": destination.name,
        "bytes": len(b"durable-coverage"),
        "sha256": hashlib.sha256(b"durable-coverage").hexdigest(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def test_cli_binds_hosted_coverage_output_into_terminal_attestation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    destination = tmp_path / ".coverage.unit-1a2-3.11"
    attestation = tmp_path / "unit-1a2-attestation.json"
    resource_paths = module.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "default-attestation.json",
    )

    def run(*_args: object, **_kwargs: object) -> int:
        destination.write_bytes(b"hosted-coverage")
        return 0

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(module, "_resource_paths", lambda: resource_paths)
    monkeypatch.setattr(module, "run", run)
    monkeypatch.setattr(
        module,
        "_repository_identity",
        lambda **_kwargs: {
            "head_sha": "abc123",
            "expected_sha": "abc123",
            "branch": "feature",
            "clean": True,
            "exact_sha": True,
            "queries_ok": True,
        },
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "run_ci_shards_serial.py",
            "--shards=unit-1a2",
            "--skip-isolated",
            "--skip-aggregate",
            f"--coverage-output={destination}",
            f"--attestation-output={attestation}",
        ],
    )

    assert module.main() == 0
    payload = json.loads(attestation.read_text(encoding="utf-8"))
    assert payload["coverage"] == module._coverage_output_evidence(destination)


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
        [
            sys.executable,
            "-m",
            "coverage",
            "combine",
            "--append",
            "--keep",
            f"--data-file={batchtemp / '.coverage'}",
            str(batchtemp),
        ]
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
        module,
        "_repository_identity",
        lambda **_kwargs: {
            "head_sha": "abc123",
            "expected_sha": "abc123",
            "branch": "feature",
            "clean": True,
            "exact_sha": True,
        },
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "run_ci_shards_serial.py",
            "--shards=unit-2,unit-3a",
            "--pytest-args=-q -W error",
            "--max-files-per-batch=17",
            "--heartbeat-seconds=4",
            "--no-progress-seconds=23",
        ],
    )

    assert module.main() == 9
    assert received == {
        "shards": ["unit-2", "unit-3a"],
        "pytest_args": ["-q", "-W", "error"],
        "max_files_per_batch": 17,
        "heartbeat_seconds": 4.0,
        "no_progress_seconds": 23.0,
        "run_isolated": True,
        "aggregate_coverage": True,
        "coverage_output": None,
        "resume_path": None,
        "watchdog_owned_gate": False,
        "execution_summary": {},
        "batch_workers": 1,
    }


def test_serial_runner_cli_forwards_hosted_single_shard_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    received: dict[str, object] = {}
    coverage_output = tmp_path / ".coverage.unit-2-3.11"

    def run(
        shards: list[str],
        pytest_args: list[str],
        **kwargs: object,
    ) -> int:
        received.update(shards=shards, pytest_args=pytest_args, **kwargs)
        coverage_output.write_bytes(b"hosted coverage")
        return 0

    monkeypatch.setattr(module, "run", run)
    monkeypatch.setattr(
        module,
        "_repository_identity",
        lambda **_kwargs: {
            "head_sha": "abc123",
            "expected_sha": "abc123",
            "branch": "feature",
            "clean": True,
            "exact_sha": True,
        },
    )
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "run_ci_shards_serial.py",
            "--shards=unit-2",
            "--pytest-args=-W error",
            "--skip-isolated",
            "--skip-aggregate",
            f"--coverage-output={coverage_output}",
        ],
    )

    assert module.main() == 0
    assert received["run_isolated"] is False
    assert received["aggregate_coverage"] is False
    assert received["coverage_output"] == coverage_output


def test_release_execution_policy_binds_python_runtime() -> None:
    """Local and hosted evidence must identify the same Python runtime family."""
    module = _load_script("run_ci_shards_serial")

    policy = module.execution_policy(["-W", "error"])

    assert policy["python_version"] == (f"{sys.version_info.major}.{sys.version_info.minor}")
    assert policy["python_implementation"] == sys.implementation.name


def test_release_execution_policy_uses_canonical_hosted_runtime() -> None:
    """Release policy must not depend on the interpreter running the verifier."""
    module = _load_script("run_ci_shards_serial")

    policy = module.release_execution_policy()

    assert policy["python_version"] == "3.11"
    assert policy["python_implementation"] == "cpython"
    assert policy["pytest_args"] == ["-W", "error"]
    assert policy["xdist_workers"] == 0
    assert policy["distribution"] == "none"
    assert policy["max_worker_restart"] is None


def test_serial_runner_fails_closed_after_batch_mutates_interpreter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A shared-venv mutation must stop the exact-SHA lane before another batch."""
    module = _load_script("run_ci_shards_serial")
    resources = module.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "attestation.json",
    )
    expected = {
        "implementation": "cpython",
        "version": "3.11.14",
        "executable": "/opt/python/3.11/bin/python3.11",
    }
    changed = {
        "implementation": "cpython",
        "version": "3.14.0",
        "executable": "/opt/python/3.14/bin/python3.14",
    }
    identities = iter((expected, expected, changed))
    executed: list[str] = []

    def run_owned(*_args: object, label: str, **_kwargs: object) -> int:
        executed.append(label)
        return 0

    monkeypatch.setattr(module, "_resource_paths", lambda: resources)
    monkeypatch.setattr(module, "COVERAGE_SHARDS", resources.coverage_shards)
    monkeypatch.setattr(module, "expand_shard", lambda _shard: ["a.py", "b.py"])
    monkeypatch.setattr(
        module,
        "_interpreter_identity",
        lambda: next(identities),
    )
    monkeypatch.setattr(
        module,
        "_run_owned_pytest",
        run_owned,
    )
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *_args: True)
    monkeypatch.setattr(module, "_cleanup_owned_tmpdir", lambda _path: 0)

    result = module.run(
        ["unit-1a1"],
        [],
        max_files_per_batch=1,
        run_isolated=False,
        aggregate_coverage=False,
    )

    assert result == module.INTERPRETER_DRIFT_EXIT_CODE
    assert executed == ["unit-1a1:batch-001"]
    output = capsys.readouterr().out
    assert "SHARD-INTERPRETER-DRIFT" in output
    assert "3.11.14" in output
    assert "3.14.0" in output


def test_resume_state_round_trip_and_invalid_payload(tmp_path: Path) -> None:
    module = _load_script("run_ci_shards_serial")
    resume = tmp_path / "nested" / "resume.json"

    assert module._load_resume_state(resume) == {}
    resume.parent.mkdir()
    resume.write_text("not json", encoding="utf-8")
    assert module._load_resume_state(resume) == {}
    resume.write_text("[]", encoding="utf-8")
    assert module._load_resume_state(resume) == {}

    expected = {"batch": {"returncode": 0}}
    module._save_resume_state(resume, expected)

    assert module._load_resume_state(resume) == expected


def test_disk_headroom_fails_closed_when_observation_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")

    def fail_observation(_path: Path) -> object:
        raise OSError("disk unavailable")

    assert not module._disk_headroom_available(
        tmp_path,
        minimum_free_bytes=1,
        disk_usage=fail_observation,
        context="coverage-test",
    )
    assert "SHARD-DISK-PREFLIGHT status=error" in capsys.readouterr().out


def test_interpreter_probe_rejects_failure_and_malformed_evidence(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")

    class Result:
        returncode = 1
        stdout = ""
        stderr = "probe exploded"

    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: Result())
    with pytest.raises(RuntimeError, match="probe exploded"):
        module._interpreter_identity()

    Result.returncode = 0
    Result.stdout = "[]"
    Result.stderr = ""
    with pytest.raises(RuntimeError, match="malformed evidence"):
        module._interpreter_identity()

    monkeypatch.setattr(
        module,
        "_interpreter_identity",
        lambda: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    assert not module._interpreter_is_unchanged({}, context="coverage-test")
    assert "SHARD-INTERPRETER-PROBE-FAIL" in capsys.readouterr().out


def test_validate_only_plan_rejects_an_empty_expansion(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    monkeypatch.setattr(module, "_plan_shards", lambda *_args, **_kwargs: [("unit-2", [])])

    assert (
        module._validate_only_plan(
            ["unit-2"],
            [],
            max_files_per_batch=4,
            attestation_output=None,
        )
        == 2
    )
    assert "SERIAL-SHARD-VALIDATE-FAIL empty=unit-2" in capsys.readouterr().out


def test_non_posix_process_group_helpers_use_direct_child_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")

    class Process:
        pid = 42
        terminated = False
        killed = False

        @classmethod
        def poll(cls) -> None:
            return None

        @classmethod
        def terminate(cls) -> None:
            cls.terminated = True

        @classmethod
        def kill(cls) -> None:
            cls.killed = True

    class NonPosixOS:
        name = "nt"

    monkeypatch.setattr(module, "os", NonPosixOS())

    assert module._owned_process_group_alive(Process()) is True
    module._signal_owned_process_group(Process(), signal.SIGTERM)
    module._signal_owned_process_group(Process(), signal.SIGKILL)
    assert Process.terminated is True
    assert Process.killed is True


def test_validate_only_plan_reports_successful_bounded_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")
    monkeypatch.setattr(
        module,
        "_plan_shards",
        lambda *_args, **_kwargs: [("unit-2", [["a.py"], ["b.py"]])],
    )

    assert (
        module._validate_only_plan(
            ["unit-2"],
            ["-q"],
            max_files_per_batch=1,
            attestation_output=tmp_path / "attestation.json",
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "SERIAL-SHARD-VALIDATE shards=unit-2 files=2 batches=2" in output
    assert f"attestation={tmp_path / 'attestation.json'}" in output


def test_plan_shards_rejects_nonpositive_batch_size() -> None:
    module = _load_script("run_ci_shards_serial")

    with pytest.raises(ValueError, match="max_files_per_batch must be positive"):
        module._plan_shards(["unit-2"], max_files_per_batch=0)


def test_owned_tree_cleanup_classifies_io_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_script("run_ci_shards_serial")

    def fail_remove(_path: Path) -> None:
        raise OSError("busy")

    monkeypatch.setattr(module.shutil, "rmtree", fail_remove)

    assert (
        module._remove_owned_tree(tmp_path / "owned", context="coverage-test")
        == module.CLEANUP_FAILURE_EXIT_CODE
    )
    assert "SHARD-CLEANUP-FAIL context=coverage-test" in capsys.readouterr().out


def test_owned_tmpdir_cleanup_rejects_unowned_path(tmp_path: Path) -> None:
    module = _load_script("run_ci_shards_serial")

    with pytest.raises(ValueError, match="refusing to remove unowned shard temp root"):
        module._cleanup_owned_tmpdir(tmp_path)


def test_partition_and_resume_boundaries_fail_closed(tmp_path: Path) -> None:
    module = _load_script("run_ci_shards_serial")

    with pytest.raises(ValueError, match="max_files must be positive"):
        module._partition_test_paths([], max_files=0)

    shard = "unit-2"
    files = ["tests/unit/test_example.py"]
    key = module._batch_key(shard, 1, files)
    coverage_shards = tmp_path / "coverage"
    assert not module._resume_skip_batch({}, shard, 1, files, coverage_shards, tmp_path)
    assert not module._resume_skip_batch(
        {key: {"rc": 1}},
        shard,
        1,
        files,
        coverage_shards,
        tmp_path,
    )
    assert not module._resume_skip_batch(
        {key: {"rc": 0, "coverage_fragment": None}},
        shard,
        1,
        files,
        coverage_shards,
        tmp_path,
    )


def test_posix_process_group_helpers_observe_and_signal_owned_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    observed: list[tuple[int, object]] = []

    class PosixOS:
        name = "posix"

        @staticmethod
        def killpg(pid: int, signum: object) -> None:
            observed.append((pid, signum))

    class Process:
        pid = 73

    monkeypatch.setattr(module, "os", PosixOS())

    assert module._owned_process_group_alive(Process()) is True
    module._signal_owned_process_group(Process(), signal.SIGTERM)
    assert observed == [(73, 0), (73, signal.SIGTERM)]
