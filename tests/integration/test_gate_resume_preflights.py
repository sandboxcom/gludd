"""Behavioral integration test for exact-SHA gate preflight resume.

Verifies that `make _gate-preflights` re-runs a small "resume-always" subset of
working-tree preflights when a prior receipt for the same candidate SHA exists
and `GATE_EXACT_SHA_RESUME=1`.  The heavy preflight loop is still skipped.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_SHA = "aabbccdd11223344556677889900aabbccdd1122"  # pragma: allowlist secret


@pytest.fixture
def resume_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Provide temp status paths and a known candidate SHA."""
    env = {
        "GATE_CANDIDATE_SHA": CANDIDATE_SHA,
        "GATE_PREFLIGHT_STATUS": str(tmp_path / "preflights.status"),
        "GATE_PREFLIGHT_GATE_STATUS": str(tmp_path / "gate-status.next"),
        "GATE_PREFLIGHT_FAILED_FILE": str(tmp_path / ".gate-failed"),
        "GATE_PREFLIGHT_TARGETS": "_gate-preflight-fixture-fail",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return env


def _run_preflights(extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(extra_env)
    return subprocess.run(
        [
            "make",
            "_gate-preflights",
            f"GATE_CANDIDATE_SHA={extra_env['GATE_CANDIDATE_SHA']}",
            f"GATE_PREFLIGHT_STATUS={extra_env['GATE_PREFLIGHT_STATUS']}",
            f"GATE_PREFLIGHT_GATE_STATUS={extra_env['GATE_PREFLIGHT_GATE_STATUS']}",
            f"GATE_PREFLIGHT_FAILED_FILE={extra_env['GATE_PREFLIGHT_FAILED_FILE']}",
            f"GATE_PREFLIGHT_TARGETS={extra_env['GATE_PREFLIGHT_TARGETS']}",
            f"GATE_EXACT_SHA_RESUME={extra_env.get('GATE_EXACT_SHA_RESUME', '1')}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


def test_preflights_skip_when_exact_sha_resume_receipt_exists(resume_env: dict[str, str]) -> None:
    """With a valid receipt and GATE_EXACT_SHA_RESUME=1, full preflights skip but the resume-always subset runs."""
    receipt_dir = ROOT / ".gate-logs"
    receipt_dir.mkdir(exist_ok=True)
    receipt = receipt_dir / f"gate-resume-prior-{CANDIDATE_SHA}.status"
    receipt.write_text("preflights PASS 35\n")

    try:
        env = {**resume_env, "GATE_EXACT_SHA_RESUME": "1"}
        result = _run_preflights(env)

        assert result.returncode == 0, result.stderr + result.stdout
        assert "PREFLIGHT-RESUME: re-ran" in result.stdout
        assert "=== GATE PREFLIGHT (resume-always):" in result.stdout
        assert "_gate-preflight-fixture-fail" not in result.stdout
        status = Path(resume_env["GATE_PREFLIGHT_STATUS"]).read_text()
        assert "check-task-registration PASS" in status
        assert "check-file-line-limits PASS" in status
    finally:
        receipt.unlink(missing_ok=True)


def test_preflights_run_when_no_resume_receipt(resume_env: dict[str, str]) -> None:
    """Without a receipt the configured fixture target must run and fail."""
    # Use a distinct SHA so parallel workers do not see a receipt left by the
    # skip-when-receipt-exists test while it is still running.
    env = {
        **resume_env,
        "GATE_CANDIDATE_SHA": "00112233445566778899aabbccddeeff00112233",  # pragma: allowlist secret
        "GATE_EXACT_SHA_RESUME": "1",
    }
    result = _run_preflights(env)

    # Make reports the recipe's exit code in output but returns 2 for a failed target.
    assert result.returncode == 2, result.stderr + result.stdout
    assert "_gate-preflight-fixture-fail FAIL" in result.stdout


def test_preflights_run_when_resume_disabled_even_with_receipt(resume_env: dict[str, str]) -> None:
    """A receipt without GATE_EXACT_SHA_RESUME must run the full configured preflights."""
    disabled_sha = "deadbeef11223344556677889900aabbccdd1122"  # pragma: allowlist secret
    receipt_dir = ROOT / ".gate-logs"
    receipt_dir.mkdir(exist_ok=True)
    receipt = receipt_dir / f"gate-resume-prior-{disabled_sha}.status"
    receipt.write_text("preflights PASS 35\n")

    try:
        env = {
            **resume_env,
            "GATE_CANDIDATE_SHA": disabled_sha,
            "GATE_EXACT_SHA_RESUME": "0",
        }
        result = _run_preflights(env)

        assert result.returncode == 2, result.stderr + result.stdout
        assert "_gate-preflight-fixture-fail FAIL" in result.stdout
        assert "PREFLIGHT-RESUME: re-ran" not in result.stdout
    finally:
        receipt.unlink(missing_ok=True)
