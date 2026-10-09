"""Behavioral contracts for fail-fast gate admission and terminal evidence."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from scripts.gate_status_attestation import (
    repository_state_id,
    verify_terminal_status,
)
from scripts.makefile_layout import compose_makefile

_MAKEFILE = Path(__file__).resolve().parents[2] / "Makefile"


def _makefile() -> str:
    return compose_makefile(_MAKEFILE)


def test_gate_preflights_are_recipe_work_not_fail_fast_prerequisites() -> None:
    makefile = _makefile()
    gate_header = makefile.split("\ngate:", 1)[1].splitlines()[0]

    assert gate_header.strip() == "_gate-run-lock-acquire"
    assert "GATE_PREFLIGHT_TARGETS :=" in makefile
    assert "for target in $(GATE_PREFLIGHT_TARGETS)" in makefile


def test_gate_terminalizes_the_first_preflight_failure() -> None:
    makefile = _makefile()

    assert ".gate-logs/gate-preflights.status" in makefile
    assert 'touch "$(GATE_PREFLIGHT_FAILED_FILE)"' in makefile
    assert "terminating admission immediately" in makefile
    assert "scripts/gate_status_attestation.py sign-terminal .gate-status.next" in makefile
    gate_recipe = makefile.split("\ngate:", 1)[1].split("\n\ngate-fast:", 1)[0]
    release = 'scripts/gate_run_lock.py release "$(GATE_RUN_LOCK)" "$$PPID"'
    assert gate_recipe.index(release) < gate_recipe.index("sign-terminal")
    assert gate_recipe.index("sign-terminal") < gate_recipe.index("GATE PHASE: lint")


def test_gate_preflight_runner_does_not_execute_later_checks_after_failure(
    tmp_path: Path,
) -> None:
    status_path = tmp_path / "preflights.status"
    gate_status_path = tmp_path / "gate.status"
    failed_path = tmp_path / "gate.failed"

    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "_gate-preflights",
            "GATE_PREFLIGHT_TARGETS=_gate-preflight-fixture-pass-one "
            "_gate-preflight-fixture-fail _gate-preflight-fixture-pass-two",
            f"GATE_PREFLIGHT_STATUS={status_path}",
            f"GATE_PREFLIGHT_GATE_STATUS={gate_status_path}",
            f"GATE_PREFLIGHT_FAILED_FILE={failed_path}",
        ],
        cwd=_MAKEFILE.parent,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert status_path.read_text(encoding="utf-8").splitlines() == [
        "_gate-preflight-fixture-pass-one PASS",
        "_gate-preflight-fixture-fail FAIL 2",
    ]
    assert gate_status_path.read_text(encoding="utf-8").strip().startswith(
        "FAIL 1 first=_gate-preflight-fixture-fail"
    )
    assert failed_path.is_file()
    assert "terminating admission immediately" in result.stdout


def test_validation_failure_is_authenticated_before_test_admission(
    tmp_path: Path,
) -> None:
    private_status = tmp_path / "gate.status.next"
    final_status = tmp_path / "gate.status"
    failed_path = tmp_path / "gate.failed"
    key_path = tmp_path / "gate.key"
    private_status.write_text(
        "=== GATE test ===\nlint FAIL 1\ntypecheck PASS 0\ncollect PASS 0\n",
        encoding="utf-8",
    )
    failed_path.touch()

    result = subprocess.run(
        [
            "make",
            "--no-print-directory",
            "_gate-validation-admission",
            f"GATE_ADMISSION_STATUS={private_status}",
            f"GATE_ADMISSION_FINAL_STATUS={final_status}",
            f"GATE_ADMISSION_FAILED_FILE={failed_path}",
            "GATE_RUN_LOCK=",
            "GATE_RUN_LOCK_OWNER_PID=0",
        ],
        cwd=_MAKEFILE.parent,
        env={**os.environ, "GLUDD_GATE_KEY_PATH": str(key_path)},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert not private_status.exists()
    assert not failed_path.exists()
    content = final_status.read_text(encoding="utf-8")
    assert "test NOT-STARTED validation-failed" in content
    assert "smoke NOT-STARTED validation-failed" in content
    assert content.count("=== GATE: FAILED ===") == 1
    key = bytes.fromhex(key_path.read_text(encoding="ascii").strip())
    authenticated = verify_terminal_status(
        final_status,
        state_id=repository_state_id(_MAKEFILE.parent),
        key=key,
    )
    assert authenticated.ok, authenticated.reason
