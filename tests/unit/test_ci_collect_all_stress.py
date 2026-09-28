"""Adversarial orchestration regressions for the serial collect-all runner."""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import signal
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from coverage import CoverageData

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def _load_runner() -> Any:
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(
            "gludd_collect_all_stress_runner",
            SCRIPTS / "run_ci_shards_serial.py",
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


@pytest.fixture
def runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Any, Any]]:
    module = _load_runner()
    resources = module.ResourcePaths(
        root=tmp_path / "runner-resources",
        coverage_shards=tmp_path / "runner-resources" / "coverage-fragments",
        coverage_json=tmp_path / "runner-resources" / "coverage.json",
        coverage_audit=tmp_path / "runner-resources" / "coverage-audit.json",
        attestation=tmp_path / "runner-resources" / "attestation.json",
    )
    monkeypatch.setattr(module, "_resource_paths", lambda: resources)
    monkeypatch.setattr(module, "COVERAGE_SHARDS", resources.coverage_shards)
    monkeypatch.setattr(module, "COVERAGE_JSON", resources.coverage_json)
    monkeypatch.setattr(module, "COVERAGE_AUDIT", resources.coverage_audit)
    monkeypatch.setattr(module, "_disk_headroom_available", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        module,
        "_interpreter_is_unchanged",
        lambda *_args, **_kwargs: True,
    )
    yield module, resources


def _write_valid_coverage(env: dict[str, str]) -> None:
    destination = Path(env["COVERAGE_FILE"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = CoverageData(basename=str(destination))
    data.add_lines({str(SCRIPTS / "run_ci_shards_serial.py"): {1}})
    data.write()
    data.close()


def _install_scripted_batches(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    *,
    plans: dict[str, list[str]],
    outcomes: list[int],
) -> list[str]:
    pending = iter(outcomes)
    launched: list[str] = []

    def execute(
        _command: list[str],
        *,
        env: dict[str, str],
        label: str,
        **_kwargs: object,
    ) -> int:
        launched.append(label)
        _write_valid_coverage(env)
        return next(pending)

    monkeypatch.setattr(module, "expand_shard", lambda shard: plans[shard])
    monkeypatch.setattr(module, "_run_owned_pytest", execute)
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    return launched


def _terminal_summary(output: str) -> tuple[str, dict[str, int], dict[str, int | str]]:
    lines = [line for line in output.splitlines() if line.startswith("SERIAL-SHARD-SUMMARY")]
    assert len(lines) == 1
    match = re.fullmatch(
        r"SERIAL-SHARD-SUMMARY total=\d+ failed=\d+ "
        r"failures=(\{.*?\}) phases=(\{.*\})",
        lines[0],
    )
    assert match is not None
    failures = ast.literal_eval(match.group(1))
    phases = json.loads(match.group(2))
    assert isinstance(failures, dict)
    assert isinstance(phases, dict)
    return lines[0], failures, phases


def test_collect_all_retains_every_ordinary_failure_in_plan_order(
    runner: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module, _resources = runner
    plans = {
        "unit-1b": ["tests/synthetic/a.py", "tests/synthetic/b.py"],
        "unit-1d": ["tests/synthetic/c.py", "tests/synthetic/d.py"],
    }
    expected_labels = [
        "unit-1b:batch-001",
        "unit-1b:batch-002",
        "unit-1d:batch-001",
        "unit-1d:batch-002",
    ]
    launched = _install_scripted_batches(
        module,
        monkeypatch,
        plans=plans,
        outcomes=[1, 5, 2, 6],
    )

    assert module.run(
        list(plans),
        [],
        max_files_per_batch=1,
        run_isolated=False,
        aggregate_coverage=False,
    ) == 6

    first_summary, failures, phases = _terminal_summary(capsys.readouterr().out)
    assert launched == expected_labels
    assert list(failures.items()) == list(zip(expected_labels, [1, 5, 2, 6], strict=True))
    assert list(phases) == sorted(phases)
    for label in expected_labels:
        assert phases[label] in {1, 2, 5, 6}
        assert phases[f"{label}:coverage"] == 0
        assert phases[f"{label}:cleanup"] == 0
        assert phases[f"{label}:setup"] == 0
        assert phases[f"{label}:tmpdir-setup"] == 0
    assert phases["unit-1b:workspace-cleanup"] == 0
    assert phases["unit-1d:workspace-cleanup"] == 0

    launched.clear()
    _install_scripted_batches(
        module,
        monkeypatch,
        plans=plans,
        outcomes=[1, 5, 2, 6],
    )
    assert module.run(
        list(plans),
        [],
        max_files_per_batch=1,
        run_isolated=False,
        aggregate_coverage=False,
    ) == 6
    second_summary, _, _ = _terminal_summary(capsys.readouterr().out)
    assert second_summary == first_summary


def test_ordinary_failure_is_preserved_before_terminal_safety_stop(
    runner: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    module, _resources = runner
    plans = {
        "unit-1b": ["tests/synthetic/ordinary.py"],
        "unit-1d": ["tests/synthetic/safety.py", "tests/synthetic/unreached.py"],
    }
    launched = _install_scripted_batches(
        module,
        monkeypatch,
        plans=plans,
        outcomes=[1, module.WORKER_DEATH_EXIT_CODE],
    )
    coverage_output = tmp_path / "combined.coverage"

    assert module.run(
        list(plans),
        [],
        max_files_per_batch=1,
        run_isolated=False,
        aggregate_coverage=False,
        coverage_output=coverage_output,
    ) == module.WORKER_DEATH_EXIT_CODE

    _summary, failures, phases = _terminal_summary(capsys.readouterr().out)
    assert launched == ["unit-1b:batch-001", "unit-1d:batch-001"]
    assert failures == {
        "unit-1b:batch-001": 1,
        "unit-1d:batch-001": module.WORKER_DEATH_EXIT_CODE,
    }
    assert phases["coverage:combine"] == "not-started"
    assert not coverage_output.exists()


@pytest.mark.parametrize(
    ("batch_rc", "expected_rc"),
    [
        (1, 74),
        (70, 70),
        (128 + signal.SIGINT, 128 + signal.SIGINT),
    ],
)
def test_cleanup_failure_precedence_is_semantic_not_numeric(
    runner: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    batch_rc: int,
    expected_rc: int,
) -> None:
    module, _resources = runner
    plans = {
        "unit-1b": ["tests/synthetic/first.py"],
        "unit-1d": ["tests/synthetic/unreached.py"],
    }
    launched = _install_scripted_batches(
        module,
        monkeypatch,
        plans=plans,
        outcomes=[batch_rc],
    )
    real_cleanup = module._cleanup_owned_tmpdir_safely

    def fail_after_removal(path: Path, *, context: str) -> int:
        cleanup_rc = real_cleanup(path, context=context)
        assert cleanup_rc == 0
        return int(module.CLEANUP_FAILURE_EXIT_CODE)

    monkeypatch.setattr(module, "_cleanup_owned_tmpdir_safely", fail_after_removal)

    assert module.run(
        list(plans),
        [],
        run_isolated=False,
        aggregate_coverage=False,
    ) == expected_rc

    _summary, failures, phases = _terminal_summary(capsys.readouterr().out)
    assert launched == ["unit-1b:batch-001"]
    assert failures["unit-1b:batch-001"] == batch_rc
    assert failures["unit-1b:cleanup"] == module.CLEANUP_FAILURE_EXIT_CODE
    assert phases["unit-1b:batch-001:cleanup"] == module.CLEANUP_FAILURE_EXIT_CODE


@pytest.mark.parametrize("coverage_kind", ["missing", "corrupt"])
def test_missing_or_corrupt_batch_coverage_suppresses_release_merge(
    runner: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    coverage_kind: str,
) -> None:
    module, resources = runner
    launched: list[str] = []
    merge_calls: list[Path] = []

    def execute(
        _command: list[str],
        *,
        env: dict[str, str],
        label: str,
        **_kwargs: object,
    ) -> int:
        launched.append(label)
        if coverage_kind == "corrupt":
            Path(env["COVERAGE_FILE"]).write_bytes(b"not coverage sqlite")
        return 0

    def forbidden_merge(destination: Path) -> int:
        merge_calls.append(destination)
        return 0

    monkeypatch.setattr(module, "expand_shard", lambda _shard: ["tests/synthetic/a.py"])
    monkeypatch.setattr(module, "_run_owned_pytest", execute)
    monkeypatch.setattr(module, "_run_command", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(module, "_combine_coverage_output", forbidden_merge)
    destination = tmp_path / f"{coverage_kind}.coverage"

    assert module.run(
        ["unit-1b"],
        [],
        run_isolated=False,
        aggregate_coverage=False,
        coverage_output=destination,
    ) == 1

    _summary, failures, phases = _terminal_summary(capsys.readouterr().out)
    assert launched == ["unit-1b:batch-001"]
    assert failures["unit-1b:batch-001:coverage"] == 1
    assert phases["coverage:combine"] == "not-started"
    assert merge_calls == []
    assert not destination.exists()
    assert not resources.coverage_shards.exists()


def test_real_failed_children_leave_no_workers_threads_or_owned_roots(
    runner: tuple[Any, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module, resources = runner
    suite = tmp_path / "child-suite"
    suite.mkdir()
    first = suite / "test_first_failure.py"
    second = suite / "test_second_failure.py"
    failing_test = (
        "from general_ludd import __version__\n\n"
        "def test_failure():\n"
        "    assert __version__\n"
        "    assert False\n"
    )
    first.write_text(failing_test, encoding="utf-8")
    second.write_text(failing_test, encoding="utf-8")
    plans = {"unit-1b": [str(first)], "unit-1d": [str(second)]}
    monkeypatch.setattr(module, "expand_shard", lambda shard: plans[shard])

    real_popen = module.subprocess.Popen
    processes: list[Any] = []

    def tracked_popen(*args: object, **kwargs: object) -> Any:
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    real_mkdtemp: Callable[..., str] = module.tempfile.mkdtemp
    owned_roots: list[Path] = []

    def tracked_mkdtemp(*args: object, **kwargs: object) -> str:
        created = real_mkdtemp(*args, **kwargs)
        owned_roots.append(Path(created))
        return created

    monkeypatch.setattr(module.subprocess, "Popen", tracked_popen)
    monkeypatch.setattr(module.tempfile, "mkdtemp", tracked_mkdtemp)

    assert module.run(
        list(plans),
        [],
        max_files_per_batch=1,
        heartbeat_seconds=0.2,
        no_progress_seconds=30.0,
        run_isolated=False,
        aggregate_coverage=False,
    ) == 1

    pytest_processes = [
        process
        for process in processes
        if isinstance(process.args, list) and process.args[1:3] == ["-m", "pytest"]
    ]
    assert len(pytest_processes) == 2
    assert all(process.poll() is not None for process in processes)
    reader_names = {f"gludd-shard-output-{process.pid}" for process in pytest_processes}
    assert reader_names.isdisjoint(thread.name for thread in threading.enumerate())
    assert owned_roots
    assert all(not path.exists() for path in owned_roots)
    assert not resources.coverage_shards.exists()
    workspaces = resources.root / "workspaces"
    assert not workspaces.exists() or not any(workspaces.iterdir())
