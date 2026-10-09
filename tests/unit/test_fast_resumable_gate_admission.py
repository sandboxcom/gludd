"""Acceptance contracts for S47 fast, resumable local gate admission."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from scripts import run_ci_shards_serial as serial_runner
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
RUN_GATE = ROOT / "scripts" / "run_gate.sh"
MAKEFILE = ROOT / "Makefile"
SHA = "a" * 40


@pytest.mark.parametrize(
    ("argument", "expected"),
    (("--exact-sha-resume", True), ("--no-exact-sha-resume", False)),
)
def test_serial_runner_cli_wires_explicit_receipt_admission_mode(
    argument: str,
    expected: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = {
        "head_sha": SHA,
        "expected_sha": SHA,
        "branch": "feature",
        "clean": True,
        "exact_sha": True,
        "queries_ok": True,
    }
    received: list[bool] = []
    session = object()

    def create_session(**kwargs: object) -> object:
        admission_enabled = kwargs["receipt_admission_enabled"]
        assert isinstance(admission_enabled, bool)
        received.append(admission_enabled)
        return session

    monkeypatch.setattr(serial_runner, "_repository_identity", lambda **_kwargs: identity)
    monkeypatch.setattr(serial_runner, "_attestation_pairing", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(serial_runner, "_create_shadow_receipt_session", create_session)
    monkeypatch.setattr(serial_runner, "run", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        serial_runner,
        "_write_terminal_attestation",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        serial_runner,
        "_resource_paths",
        lambda: serial_runner.ResourcePaths(
            root=tmp_path,
            coverage_shards=tmp_path / "coverage-fragments",
            coverage_json=tmp_path / "coverage.json",
            coverage_audit=tmp_path / "coverage-audit.json",
            attestation=tmp_path / "attestation.json",
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_ci_shards_serial.py",
            "--shards=unit-1a1",
            "--skip-isolated",
            "--skip-aggregate",
            argument,
        ],
    )

    assert serial_runner.main() == 0
    assert received == [expected]


def test_run_gate_defaults_to_exact_resume_and_two_bounded_isolated_workers() -> None:
    source = RUN_GATE.read_text(encoding="utf-8")

    assert 'GLUDD_GATE_EXACT_SHA_RESUME:-1' in source
    assert 'GLUDD_GATE_MAX_FILES_PER_BATCH:-32' in source
    assert 'GLUDD_GATE_BATCH_WORKERS:-2' in source
    assert "--exact-sha-resume" in source
    assert "--no-exact-sha-resume" in source
    assert "--max-files-per-batch" in source
    assert "--max-files-per-batch 32" not in source
    assert "scripts/run_ci_shards_serial.py" in source
    assert "--batch-workers" in source
    assert '"${GATE_BATCH_WORKERS}" -gt 2' in source


def test_run_gate_rejects_unbounded_batch_size_before_test_execution(
    tmp_path: Path,
) -> None:
    marker = "S47_TEST_MUST_NOT_RUN"
    environment = {
        **os.environ,
        "GLUDD_GATE_MAX_FILES_PER_BATCH": "33",
        "PYTEST_CMD": f'python3 -c "print(\"{marker}\")"',
        "GATE_LOCK_FILE": str(tmp_path / "gate.lock"),
        "GATE_STATUS_FILE": str(tmp_path / "gate.status"),
        "GATE_FAILED_FILE": str(tmp_path / "gate.failed"),
    }

    result = subprocess.run(
        ["bash", str(RUN_GATE)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode != 0
    assert marker not in result.stdout + result.stderr
    assert "1..32" in result.stdout + result.stderr


def test_run_gate_rejects_more_than_two_workers_before_test_execution(
    tmp_path: Path,
) -> None:
    marker = "S47_WORKER_TEST_MUST_NOT_RUN"
    environment = {
        **os.environ,
        "GLUDD_GATE_BATCH_WORKERS": "3",
        "PYTEST_CMD": f'python3 -c "print(\"{marker}\")"',
        "GATE_LOCK_FILE": str(tmp_path / "gate.lock"),
        "GATE_STATUS_FILE": str(tmp_path / "gate.status"),
        "GATE_FAILED_FILE": str(tmp_path / "gate.failed"),
    }

    result = subprocess.run(
        ["bash", str(RUN_GATE)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode != 0
    assert marker not in result.stdout + result.stderr
    assert "1..2" in result.stdout + result.stderr


def test_batch_summary_measures_resume_savings_under_two_worker_ceiling(
    capsys: pytest.CaptureFixture[str],
) -> None:
    summary = serial_runner._print_batch_execution_summary(
        planned=4,
        executed=1,
        resumed=2,
        time_saved_seconds=61.25,
        max_files_per_batch=32,
        batch_workers=2,
    )

    assert summary == {
        "planned": 4,
        "executed": 1,
        "resumed": 2,
        "not_started": 1,
        "time_saved_seconds": 61.25,
        "max_files_per_batch": 32,
        "workers": 2,
        "reconciled": True,
    }
    output = capsys.readouterr().out
    assert "executed=1 resumed=2 not_started=1 time_saved_seconds=61.250" in output
    assert "workers=2" in output


def test_gate_make_policy_has_safe_behavioral_example_and_unchanged_floors() -> None:
    makefile = compose_makefile(MAKEFILE)
    runner = (ROOT / "scripts" / "run_ci_shards_serial.py").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")

    assert "GATE_EXACT_SHA_RESUME ?= 1" in makefile
    assert "GATE_MAX_FILES_PER_BATCH ?= 32" in makefile
    assert "GATE_BATCH_WORKERS ?= 2" in makefile
    assert "gate-admission-config:" in makefile
    assert "gate-admission-config" in makefile.split("GATE_PREFLIGHT_TARGETS :=", 1)[1]
    assert "--fail-under=85" in runner
    assert '"--per-file-threshold=75"' in runner
    assert "--exact-sha-resume" not in workflow
