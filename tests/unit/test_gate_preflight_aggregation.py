"""Structural contract for complete, non-short-circuit gate preflights."""

from __future__ import annotations

import subprocess
from pathlib import Path

_MAKEFILE = Path(__file__).resolve().parents[2] / "Makefile"


def _makefile() -> str:
    return _MAKEFILE.read_text(encoding="utf-8")


def test_gate_preflights_are_recipe_work_not_fail_fast_prerequisites() -> None:
    makefile = _makefile()
    gate_header = makefile.split("\ngate:", 1)[1].splitlines()[0]

    assert gate_header.strip() == "_gate-run-lock-acquire"
    assert "GATE_PREFLIGHT_TARGETS :=" in makefile
    assert "for target in $(GATE_PREFLIGHT_TARGETS)" in makefile


def test_gate_retains_every_preflight_result_and_continues_after_failure() -> None:
    makefile = _makefile()

    assert ".gate-logs/gate-preflights.status" in makefile
    assert 'PREFLIGHT_FAILURES=$$((PREFLIGHT_FAILURES + 1))' in makefile
    assert 'touch "$(GATE_PREFLIGHT_FAILED_FILE)"' in makefile
    assert 'preflight failures retained; continuing remaining phases' in makefile


def test_gate_preflight_runner_executes_later_checks_after_failure(
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

    assert result.returncode == 0
    assert status_path.read_text(encoding="utf-8").splitlines() == [
        "_gate-preflight-fixture-pass-one PASS",
        "_gate-preflight-fixture-fail FAIL 2",
        "_gate-preflight-fixture-pass-two PASS",
    ]
    assert gate_status_path.read_text(encoding="utf-8").strip().startswith("FAIL 1")
    assert failed_path.is_file()
    assert "preflight failures retained; continuing remaining phases" in result.stdout
