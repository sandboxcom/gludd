"""Resource-ownership regressions for the canonical serial CI shard runner."""

from __future__ import annotations

import importlib.util
import sys
from collections import namedtuple
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def _load_runner() -> Any:
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(
            "gludd_ci_shard_resource_runner",
            SCRIPTS / "run_ci_shards_serial.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


def test_owned_terraform_cache_is_namespaced_and_created(tmp_path: Path) -> None:
    runner = _load_runner()
    workspace = tmp_path / "owned-workspace"
    workspace.mkdir()
    inherited = {"TF_PLUGIN_CACHE_DIR": "/Users/example/shared-cache", "KEEP": "yes"}

    child = runner._owned_terraform_environment(inherited, workspace)

    expected = workspace / "terraform-plugin-cache"
    assert child == {
        "TF_PLUGIN_CACHE_DIR": str(expected),
        "KEEP": "yes",
    }
    assert expected.is_dir()
    assert inherited["TF_PLUGIN_CACHE_DIR"] == "/Users/example/shared-cache"


def test_disk_headroom_reports_exact_free_and_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _load_runner()
    Usage = namedtuple("Usage", "total used free")
    usage = Usage(total=10_000, used=9_500, free=500)

    assert not runner._disk_headroom_available(
        tmp_path,
        minimum_free_bytes=1_000,
        disk_usage=lambda _path: usage,
        context="unit-3b:batch-007:before",
    )

    output = capsys.readouterr().out
    assert "SHARD-DISK-PREFLIGHT" in output
    assert "status=insufficient" in output
    assert "free_bytes=500" in output
    assert "minimum_free_bytes=1000" in output
    assert "context=unit-3b:batch-007:before" in output


def test_serial_runner_cleans_each_batch_before_starting_the_next(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    resources = runner.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "attestation.json",
    )
    workspace = resources.root / "workspaces" / "unit-3b"
    events: list[str] = []

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        del prefix, dir
        workspace.mkdir(parents=True, exist_ok=True)
        return str(workspace)

    def owned_tmpdir(label: str) -> Path:
        path = tmp_path / label
        path.mkdir()
        return path

    def run_pytest(*_args: object, label: str, **_kwargs: object) -> int:
        events.append(f"run:{label}")
        return 0

    def cleanup(path: Path) -> int:
        events.append(f"cleanup:{path.name}")
        path.rmdir()
        return 0

    monkeypatch.setattr(runner, "_resource_paths", lambda: resources)
    monkeypatch.setattr(runner, "COVERAGE_SHARDS", resources.coverage_shards)
    monkeypatch.setattr(runner, "COVERAGE_JSON", resources.coverage_json)
    monkeypatch.setattr(runner, "COVERAGE_AUDIT", resources.coverage_audit)
    monkeypatch.setattr(runner.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(
        runner,
        "expand_shard",
        lambda _shard: ["tests/unit/test_a.py", "tests/unit/test_b.py"],
    )
    monkeypatch.setattr(runner, "_owned_socket_safe_tmpdir", owned_tmpdir)
    monkeypatch.setattr(runner, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(runner, "_run_owned_pytest", run_pytest)
    monkeypatch.setattr(runner, "_save_shard_coverage", lambda *_args: True)
    monkeypatch.setattr(runner, "_interpreter_is_unchanged", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runner, "_disk_headroom_available", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runner, "_cleanup_owned_tmpdir", cleanup)

    assert (
        runner.run(
            ["unit-3b"],
            [],
            max_files_per_batch=1,
            run_isolated=False,
            aggregate_coverage=False,
        )
        == 0
    )
    assert events == [
        "run:unit-3b:batch-001",
        "cleanup:unit-3b-batch-001",
        "run:unit-3b:batch-002",
        "cleanup:unit-3b-batch-002",
    ]


def test_disk_headroom_reports_available_and_observation_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _load_runner()
    Usage = namedtuple("Usage", "total used free")
    usage = Usage(total=10_000, used=8_000, free=2_000)

    assert runner._disk_headroom_available(
        tmp_path,
        minimum_free_bytes=1_000,
        disk_usage=lambda _path: usage,
        context="unit-3b:batch-001:before",
    )

    def broken_usage(_path: Path) -> Any:
        raise OSError("disk unavailable")

    assert not runner._disk_headroom_available(
        tmp_path,
        minimum_free_bytes=1_000,
        disk_usage=broken_usage,
        context="unit-3b:batch-002:before",
    )
    output = capsys.readouterr().out
    assert "status=available" in output
    assert "status=error" in output
    assert "OSError:disk unavailable" in output


def test_serial_runner_stops_before_pytest_when_disk_is_insufficient(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    resources = runner.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "attestation.json",
    )
    workspace = resources.root / "workspaces" / "unit-3b"
    started: list[str] = []

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        del prefix, dir
        workspace.mkdir(parents=True, exist_ok=True)
        return str(workspace)

    def run_pytest(*_args: object, label: str, **_kwargs: object) -> int:
        started.append(label)
        return 0

    monkeypatch.setattr(runner, "_resource_paths", lambda: resources)
    monkeypatch.setattr(runner, "COVERAGE_SHARDS", resources.coverage_shards)
    monkeypatch.setattr(runner, "COVERAGE_JSON", resources.coverage_json)
    monkeypatch.setattr(runner, "COVERAGE_AUDIT", resources.coverage_audit)
    monkeypatch.setattr(runner.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(runner, "expand_shard", lambda _shard: ["tests/unit/test_a.py"])
    monkeypatch.setattr(runner, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(runner, "_run_owned_pytest", run_pytest)
    monkeypatch.setattr(runner, "_disk_headroom_available", lambda *_args, **_kwargs: False)

    assert (
        runner.run(
            ["unit-3b"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
        )
        == runner.DISK_HEADROOM_EXIT_CODE
    )
    assert started == []


@pytest.mark.parametrize("cleanup_rc", [9, 130])
def test_serial_runner_stops_after_immediate_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_rc: int,
) -> None:
    runner = _load_runner()
    resources = runner.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "attestation.json",
    )
    workspace = resources.root / "workspaces" / "unit-3b"
    started: list[str] = []

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        del prefix, dir
        workspace.mkdir(parents=True, exist_ok=True)
        return str(workspace)

    def owned_tmpdir(label: str) -> Path:
        path = tmp_path / label
        path.mkdir()
        return path

    def run_pytest(*_args: object, label: str, **_kwargs: object) -> int:
        started.append(label)
        return 0

    monkeypatch.setattr(runner, "_resource_paths", lambda: resources)
    monkeypatch.setattr(runner, "COVERAGE_SHARDS", resources.coverage_shards)
    monkeypatch.setattr(runner, "COVERAGE_JSON", resources.coverage_json)
    monkeypatch.setattr(runner, "COVERAGE_AUDIT", resources.coverage_audit)
    monkeypatch.setattr(runner.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(
        runner,
        "expand_shard",
        lambda _shard: ["tests/unit/test_a.py", "tests/unit/test_b.py"],
    )
    monkeypatch.setattr(runner, "_owned_socket_safe_tmpdir", owned_tmpdir)
    monkeypatch.setattr(runner, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(runner, "_run_owned_pytest", run_pytest)
    monkeypatch.setattr(runner, "_save_shard_coverage", lambda *_args: True)
    monkeypatch.setattr(runner, "_interpreter_is_unchanged", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runner, "_disk_headroom_available", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        runner,
        "_cleanup_owned_tmpdir",
        lambda _path: cleanup_rc,
    )

    assert (
        runner.run(
            ["unit-3b"],
            [],
            max_files_per_batch=1,
            run_isolated=False,
            aggregate_coverage=False,
        )
        == cleanup_rc
    )
    assert started == ["unit-3b:batch-001"]


def test_serial_runner_fails_closed_on_empty_shard_expansion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    resources = runner.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "attestation.json",
    )
    monkeypatch.setattr(runner, "_resource_paths", lambda: resources)
    monkeypatch.setattr(runner, "COVERAGE_SHARDS", resources.coverage_shards)
    monkeypatch.setattr(runner, "COVERAGE_JSON", resources.coverage_json)
    monkeypatch.setattr(runner, "COVERAGE_AUDIT", resources.coverage_audit)
    monkeypatch.setattr(runner, "expand_shard", lambda _shard: [])
    monkeypatch.setattr(runner, "_run_command", lambda *_args, **_kwargs: 0)

    assert (
        runner.run(
            ["unit-3b"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
        )
        == 2
    )


def test_serial_runner_stops_before_child_on_interpreter_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    resources = runner.ResourcePaths(
        root=tmp_path / "resources",
        coverage_shards=tmp_path / "resources" / "coverage-fragments",
        coverage_json=tmp_path / "resources" / "coverage.json",
        coverage_audit=tmp_path / "resources" / "coverage-audit.json",
        attestation=tmp_path / "resources" / "attestation.json",
    )
    workspace = resources.root / "workspaces" / "unit-3b"
    started: list[str] = []
    cleaned: list[str] = []

    def fake_mkdtemp(*, prefix: str, dir: str | Path) -> str:
        del prefix, dir
        workspace.mkdir(parents=True, exist_ok=True)
        return str(workspace)

    def owned_tmpdir(label: str) -> Path:
        path = tmp_path / label
        path.mkdir()
        return path

    def run_pytest(*_args: object, label: str, **_kwargs: object) -> int:
        started.append(label)
        return 0

    def cleanup(path: Path) -> int:
        cleaned.append(path.name)
        path.rmdir()
        return 0

    monkeypatch.setattr(runner, "_resource_paths", lambda: resources)
    monkeypatch.setattr(runner, "COVERAGE_SHARDS", resources.coverage_shards)
    monkeypatch.setattr(runner, "COVERAGE_JSON", resources.coverage_json)
    monkeypatch.setattr(runner, "COVERAGE_AUDIT", resources.coverage_audit)
    monkeypatch.setattr(runner.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(runner, "expand_shard", lambda _shard: ["tests/unit/test_a.py"])
    monkeypatch.setattr(runner, "_owned_socket_safe_tmpdir", owned_tmpdir)
    monkeypatch.setattr(runner, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(runner, "_run_owned_pytest", run_pytest)
    monkeypatch.setattr(runner, "_disk_headroom_available", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(runner, "_interpreter_is_unchanged", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(runner, "_cleanup_owned_tmpdir", cleanup)

    assert (
        runner.run(
            ["unit-3b"],
            [],
            run_isolated=False,
            aggregate_coverage=False,
        )
        == runner.INTERPRETER_DRIFT_EXIT_CODE
    )
    assert started == []
    assert cleaned == ["unit-3b-batch-001"]


def test_interpreter_identity_rejects_failed_and_malformed_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _load_runner()
    failed = runner.subprocess.CompletedProcess(
        args=["python"],
        returncode=1,
        stdout="",
        stderr="probe failed visibly",
    )
    monkeypatch.setattr(runner.subprocess, "run", lambda *_args, **_kwargs: failed)
    with pytest.raises(RuntimeError, match="probe failed visibly"):
        runner._interpreter_identity()

    malformed = runner.subprocess.CompletedProcess(
        args=["python"],
        returncode=0,
        stdout="[]",
        stderr="",
    )
    monkeypatch.setattr(runner.subprocess, "run", lambda *_args, **_kwargs: malformed)
    with pytest.raises(RuntimeError, match="malformed evidence"):
        runner._interpreter_identity()


def test_interpreter_change_check_reports_probe_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _load_runner()

    def failed_probe() -> dict[str, str]:
        raise RuntimeError("identity unavailable")

    monkeypatch.setattr(runner, "_interpreter_identity", failed_probe)

    assert not runner._interpreter_is_unchanged(
        {
            "implementation": "cpython",
            "version": "3.11.14",
            "executable": "/opt/python",
        },
        context="unit-3b:batch-001:before",
    )
    output = capsys.readouterr().out
    assert "SHARD-INTERPRETER-PROBE-FAIL" in output
    assert "identity unavailable" in output
