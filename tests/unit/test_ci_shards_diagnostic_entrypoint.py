"""Behavioral contracts for the serial collect-all diagnostic Make entry point."""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
SHARDS_VARIABLE = "CI_SHARDS_DIAGNOSTIC_SHARDS"
VALIDATE_VARIABLE = "CI_SHARDS_DIAGNOSTIC_VALIDATE_ONLY"


def _make(
    tmp_path: Path,
    *assignments: str,
) -> subprocess.CompletedProcess[str]:
    """Invoke the actual target with an argv-only UV probe in a disposable root."""
    probe = tmp_path / "uv_probe.py"
    probe.write_text(
        "import json, os, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], 'raw_exports': "
        "{name: os.environ.get(name) for name in "
        "('CI_SHARDS_DIAGNOSTIC_SHARDS', 'CI_SHARDS_DIAGNOSTIC_VALIDATE_ONLY')}}))\n"
        "raise SystemExit(int(os.environ.get('GLUDD_DIAGNOSTIC_PROBE_EXIT_CODE', '0')))\n",
        encoding="utf-8",
    )
    recipe = tmp_path / "Makefile"
    recipe.write_text(
        (ROOT / "make/10-observability-and-tests.mk").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    for name in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "GNUMAKEFLAGS"):
        environment.pop(name, None)
    return subprocess.run(
        [
            "make",
            "--no-print-directory",
            "-f",
            str(recipe),
            "ci-shards-diagnostic",
            f"UV={shlex.join([sys.executable, str(probe)])}",
            *assignments,
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _arguments(result: subprocess.CompletedProcess[str]) -> list[str]:
    """Read one silent Make recipe's exact forwarded argument vector."""
    assert result.returncode == 0, result.stdout + result.stderr
    payload: object = json.loads(result.stdout)
    assert isinstance(payload, dict)
    assert payload["raw_exports"] == {SHARDS_VARIABLE: None, VALIDATE_VARIABLE: None}
    raw_arguments: object = payload["argv"]
    assert isinstance(raw_arguments, list)
    arguments: list[str] = []
    for item in raw_arguments:
        assert isinstance(item, str)
        arguments.append(item)
    return arguments


def test_defaults_pin_the_original_policy_and_only_print_a_plan(tmp_path: Path) -> None:
    arguments = _arguments(_make(tmp_path, f"{SHARDS_VARIABLE}=unit-2 unit-3a"))

    assert arguments == [
        "run",
        "--no-sync",
        "--python",
        "3.11",
        "python",
        "scripts/run_ci_shards_serial.py",
        "--shards",
        "unit-2 unit-3a",
        "--pytest-args=-W error",
        "--batch-workers",
        "1",
        "--max-files-per-batch",
        "16",
        "--collect-all-failures",
        "--validate-only",
    ]


def test_live_diagnostics_use_the_same_bounded_runner_without_release_flags(tmp_path: Path) -> None:
    arguments = _arguments(
        _make(tmp_path, f"{SHARDS_VARIABLE}=unit-2", f"{VALIDATE_VARIABLE}=0")
    )

    assert "--collect-all-failures" in arguments
    assert "--validate-only" not in arguments
    assert "--require-release-policy" not in arguments
    assert "--skip-isolated" not in arguments
    assert "--skip-aggregate" not in arguments
    assert "--resume" not in arguments
    assert "--no-progress-seconds" not in arguments
    assert "--heartbeat-seconds" not in arguments


@pytest.mark.parametrize("shards", [None, "", "   ", "\t"])
def test_missing_or_whitespace_only_shards_fail_before_runner_work(
    tmp_path: Path,
    shards: str | None,
) -> None:
    assignments = [] if shards is None else [f"{SHARDS_VARIABLE}={shards}"]
    result = _make(tmp_path, *assignments)

    assert result.returncode != 0
    assert "CI_SHARDS_DIAGNOSTIC_SHARDS must name explicit shards" in result.stdout
    assert '"argv"' not in result.stdout


@pytest.mark.parametrize("validate", ["", "2", "-1", "true", "1 0"])
def test_invalid_validate_modes_fail_before_runner_work(tmp_path: Path, validate: str) -> None:
    result = _make(tmp_path, f"{SHARDS_VARIABLE}=unit-2", f"{VALIDATE_VARIABLE}={validate}")

    assert result.returncode != 0
    assert "CI_SHARDS_DIAGNOSTIC_VALIDATE_ONLY must be 0 or 1" in result.stdout
    assert '"argv"' not in result.stdout


@pytest.mark.parametrize(
    "payload",
    [
        "unit-2; touch {sentinel}",
        "unit-2 $$(touch {sentinel})",
        "unit-2 `touch {sentinel}`",
        "unit-2 $(shell touch {sentinel})",
        "unit-2 \"' --require-release-policy",
    ],
)
def test_shard_values_are_literal_and_never_execute_make_or_shell_input(
    tmp_path: Path,
    payload: str,
) -> None:
    sentinel = tmp_path / "unexpected-evaluation"
    raw = payload.format(sentinel=sentinel)
    arguments = _arguments(_make(tmp_path, f"{SHARDS_VARIABLE}={raw}"))

    assert arguments[arguments.index("--shards") + 1] == raw
    assert not sentinel.exists()


def test_validate_values_never_evaluate_make_expressions(tmp_path: Path) -> None:
    sentinel = tmp_path / "unexpected-evaluation"
    result = _make(
        tmp_path,
        f"{SHARDS_VARIABLE}=unit-2",
        f"{VALIDATE_VARIABLE}=$(shell touch {sentinel})",
    )

    assert result.returncode != 0
    assert "CI_SHARDS_DIAGNOSTIC_VALIDATE_ONLY must be 0 or 1" in result.stdout
    assert not sentinel.exists()


def test_general_test_variables_cannot_weaken_diagnostic_policy(tmp_path: Path) -> None:
    arguments = _arguments(
        _make(
            tmp_path,
            f"{SHARDS_VARIABLE}=unit-2",
            "PYTEST_ARGS=--disable-warnings -W ignore -n 8",
            "MAX_FILES_PER_BATCH=128",
            "NPROC=8",
            "GLUDD_XDIST_WORKERS=8",
        )
    )

    assert arguments[arguments.index("--batch-workers") + 1] == "1"
    assert arguments[arguments.index("--max-files-per-batch") + 1] == "16"
    assert "--pytest-args=-W error" in arguments
    assert "--disable-warnings" not in arguments


def test_runner_failure_is_not_reported_as_make_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GLUDD_DIAGNOSTIC_PROBE_EXIT_CODE", "37")
    result = _make(tmp_path, f"{SHARDS_VARIABLE}=unit-2", f"{VALIDATE_VARIABLE}=0")

    assert result.returncode != 0
    assert "Error 37" in result.stderr


@pytest.fixture
def native_runner(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Load the existing runner; the overlay changes only the Make interface."""
    scripts = Path(compose_makefile.__code__.co_filename).resolve().parent
    monkeypatch.setattr(sys, "path", [str(scripts), *sys.path])
    spec = importlib.util.spec_from_file_location(
        "gludd_diagnostic_entrypoint_native_runner",
        scripts / "run_ci_shards_serial.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_native_validate_plan_cannot_execute_tests_or_publish_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    native_runner: ModuleType,
) -> None:
    arguments = _arguments(_make(tmp_path, f"{SHARDS_VARIABLE}=unit-2"))

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("diagnostic validation must not execute or write evidence")

    for name in ("run", "_repository_identity", "_write_terminal_attestation", "_configure_shadow_receipts"):
        monkeypatch.setattr(native_runner, name, unexpected)
    monkeypatch.setattr(sys, "argv", arguments[5:])

    assert native_runner.main() == 0
    output = capsys.readouterr().out
    assert "SERIAL-SHARD-VALIDATE shards=unit-2" in output
    assert "worker=1 max_files_per_batch=16 pytest_args=-W error" in output


def test_native_diagnostic_and_release_policy_remain_incompatible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    native_runner: ModuleType,
) -> None:
    arguments = _arguments(_make(tmp_path, f"{SHARDS_VARIABLE}=unit-2"))
    monkeypatch.setattr(sys, "argv", [*arguments[5:], "--require-release-policy"])

    assert native_runner.main() == 2
    assert "diagnostic collect-all cannot produce release evidence" in capsys.readouterr().out


def test_make_help_and_contract_document_a_safe_plan_example() -> None:
    foundation = (ROOT / "make/00-foundation.mk").read_text(encoding="utf-8")
    contract = json.loads((ROOT / "config/make_target_contract.json").read_text(encoding="utf-8"))
    entry = next(item for item in contract["targets"] if item["name"] == "ci-shards-diagnostic")

    assert "  ci-shards-diagnostic " in foundation
    assert entry["make_variables"] == [SHARDS_VARIABLE, VALIDATE_VARIABLE]
    assert shlex.split(entry["behavior"]) == [
        "make",
        "ci-shards-diagnostic",
        f"{SHARDS_VARIABLE}=unit-2",
        f"{VALIDATE_VARIABLE}=1",
    ]
