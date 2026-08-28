"""Contracts for the canonical local dual-track CI evidence producer."""

from __future__ import annotations

import importlib.util
import inspect
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_ci_shards_serial.py"
EXPECTED_SHARDS = (
    "unit-1a1",
    "unit-1a2",
    "unit-1b",
    "unit-1d",
    "unit-2",
    "unit-3a",
    "unit-3b",
    "other",
)


def _load_runner() -> ModuleType:
    scripts = str(ROOT / "scripts")
    sys.path.insert(0, scripts)
    try:
        spec = importlib.util.spec_from_file_location("gludd_local_dual_track", RUNNER)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(scripts)


def _target_block(name: str, next_name: str) -> str:
    source = (ROOT / "Makefile").read_text(encoding="utf-8")
    return source.split(f"{name}:", 1)[1].split(f"{next_name}:", 1)[0]


def test_local_dual_track_target_delegates_canonical_bounded_run() -> None:
    block = _target_block("test-ci-dual-track-local", "test-ci-shard")
    runner = _load_runner()

    assert "$(MAKE) node-deps-sync" in block
    assert block.index("$(MAKE) node-deps-sync") < block.index("scripts/run_ci_shards_serial.py")
    assert "scripts/run_ci_shards_serial.py" in block
    assert "--shards" not in block
    assert "--skip-isolated" not in block
    assert "--skip-aggregate" not in block
    assert "--max-files-per-batch" in block
    assert "$(or $(MAX_FILES_PER_BATCH),16)" in block
    assert '--attestation-output "$$RESOURCE_ROOT/ci-shards/attestation.json"' in block
    assert "DUAL_TRACK_LOCAL_VALIDATE_ONLY" in block
    assert "--validate-only" in block
    assert '"-n",\n        "1"' in inspect.getsource(runner._pytest_command)


def test_runner_validate_only_is_empty_selector_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _load_runner()
    attestation = tmp_path / "attestation.json"

    def unexpected(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("validate-only must not execute or inspect release state")

    monkeypatch.setattr(runner, "run", unexpected)
    monkeypatch.setattr(runner, "_repository_identity", unexpected)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_ci_shards_serial.py",
            "--validate-only",
            "--pytest-args=-k __gludd_contract_empty__",
            "--max-files-per-batch=16",
            f"--attestation-output={attestation}",
        ],
    )

    assert runner.main() == 0
    output = capsys.readouterr().out
    assert "SERIAL-SHARD-VALIDATE" in output
    assert f"shards={','.join(EXPECTED_SHARDS)}" in output
    assert "max_files_per_batch=16" in output
    assert "__gludd_contract_empty__" in output
    assert not attestation.exists()


def test_local_dual_track_make_example_is_safe_and_observable() -> None:
    result = subprocess.run(
        [
            "make",
            "test-ci-dual-track-local",
            "DUAL_TRACK_LOCAL_VALIDATE_ONLY=1",
            "PYTEST_ARGS=-k __gludd_contract_empty__",
            "MAX_FILES_PER_BATCH=16",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "SERIAL-SHARD-VALIDATE" in result.stdout
    assert "__gludd_contract_empty__" in result.stdout
