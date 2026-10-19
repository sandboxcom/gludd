"""Behavioral integration tests for authenticated exact-state preflight resume.

The actual Make recipe verifies a native signed failed status in a committed
fixture repository. Its resume-always targets still execute the repository's
real working-tree checks; the heavy configured preflight must remain skipped.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest
from scripts.gate_status_attestation import (
    repository_state_id,
    sign_terminal_status,
    verify_resume_status,
    verify_status,
    verify_terminal_status,
)
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_SHA = "aabbccdd11223344556677889900aabbccdd1122"  # pragma: allowlist secret
_KEY = b"r" * 32


@pytest.fixture
def resume_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Commit a private native repository before any status/key writes."""
    source = compose_makefile(ROOT / "Makefile")
    recipe = source.split("\n_gate-preflights:\n", 1)[1].split("\n\n", 1)[0]
    recipe = recipe.replace(
        "scripts/gate_status_attestation.py",
        str(ROOT / "scripts" / "gate_status_attestation.py"),
    )
    resume_always = source.split("GATE_PREFLIGHT_RESUME_ALWAYS ?= ", 1)[1].splitlines()[0]
    uv = tmp_path / "selected-uv"
    uv.write_text(
        '#!/bin/sh\n[ "$1" = run ] || exit 91\nshift\n'
        '[ "$1" = python ] || exit 92\nshift\n'
        f'exec {shlex.quote(sys.executable)} "$@"\n',
        encoding="utf-8",
    )
    uv.chmod(0o700)
    (tmp_path / ".gitignore").write_text(
        "*.status\ngate-status.next\n.gate-failed\nfixture.key\n", encoding="utf-8",
    )
    (tmp_path / "Makefile").write_text(
        f"UV := {shlex.quote(str(uv))}\n"
        f"GATE_PREFLIGHT_RESUME_ALWAYS := {resume_always}\n"
        "_gate-preflights:\n" + recipe
        + "\n\n_gate-preflight-fixture-fail:\n\t@exit 7\n"
        + f"\n{resume_always}:\n"
        + f'\t@$(MAKE) --no-print-directory -C {shlex.quote(str(ROOT))} "$@" '
        + f"{shlex.quote('UV=' + shlex.quote(str(uv)))}\n",
        encoding="utf-8",
    )
    for arguments in (
        ("init", "--quiet"), ("config", "user.name", "Gate fixture"),
        ("config", "user.email", "gate@example.invalid"), ("add", "."),
        ("commit", "--quiet", "-m", "fixture"),
    ):
        subprocess.run(
            ["git", "-C", str(tmp_path), *arguments], check=True, capture_output=True,
        )
    env = {
        "UV": shlex.quote(str(uv)),
        "GLUDD_GATE_KEY_PATH": str(tmp_path / "fixture.key"),
        "GATE_CANDIDATE_SHA": CANDIDATE_SHA,
        "GATE_TEST_PROFILE": "native",
        "GATE_MAX_FILES_PER_BATCH": "16",
        "GATE_BATCH_WORKERS": "1",
        "GATE_PREFLIGHT_STATUS": str(tmp_path / "preflights.status"),
        "GATE_PREFLIGHT_GATE_STATUS": str(tmp_path / "gate-status.next"),
        "GATE_PREFLIGHT_FAILED_FILE": str(tmp_path / ".gate-failed"),
        "GATE_PREFLIGHT_RESUME_RECEIPT": str(tmp_path / "resume-receipt.status"),
        "GATE_PREFLIGHT_TARGETS": "_gate-preflight-fixture-fail",
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return env


def _resume_receipt(resume_env: dict[str, str], *, receipt_case: str = "signed") -> Path:
    """Reuse the native terminal signer, preserving failed-gate authority."""
    receipt = Path(resume_env["GATE_PREFLIGHT_RESUME_RECEIPT"])
    Path(resume_env["GLUDD_GATE_KEY_PATH"]).write_text(_KEY.hex(), encoding="ascii")
    profile = "release" if receipt_case == "policy" else resume_env["GATE_TEST_PROFILE"]
    receipt.write_text(
        "=== GATE fixture ===\n"
        f"gate-policy profile={profile} max_files_per_batch=16 workers=1\n"
        "preflights PASS 35\nlint FAIL 1\n=== GATE: FAILED ===\n",
        encoding="utf-8",
    )
    if receipt_case != "unsigned":
        epoch = int(time.time()) - (3600 if receipt_case == "stale" else 0)
        state = repository_state_id(receipt.parent)
        assert state == repository_state_id(receipt.parent, source="index")
        sign_terminal_status(receipt, state_id=state, key=_KEY, now=epoch)
    if receipt_case == "tampered":
        receipt.write_text(
            receipt.read_text(encoding="utf-8").replace("preflights PASS 35", "preflights PASS 36"),
            encoding="utf-8",
        )
    return receipt


def _run_preflights(extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(extra_env)
    return subprocess.run(
        [
            "make",
            "_gate-preflights",
            *(f"{name}={extra_env[name]}" for name in (
                "UV", "GATE_CANDIDATE_SHA", "GATE_TEST_PROFILE",
                "GATE_MAX_FILES_PER_BATCH", "GATE_BATCH_WORKERS",
                "GATE_PREFLIGHT_STATUS", "GATE_PREFLIGHT_GATE_STATUS",
                "GATE_PREFLIGHT_FAILED_FILE", "GATE_PREFLIGHT_RESUME_RECEIPT",
                "GATE_PREFLIGHT_TARGETS",
            )),
            f"GATE_EXACT_SHA_RESUME={extra_env.get('GATE_EXACT_SHA_RESUME', '1')}",
        ],
        cwd=Path(extra_env["GATE_PREFLIGHT_STATUS"]).parent,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_preflights_skip_when_exact_sha_resume_receipt_exists(resume_env: dict[str, str]) -> None:
    """A valid native receipt skips full preflights but reruns the working-tree subset."""
    receipt = _resume_receipt(resume_env)
    original = receipt.read_bytes()

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
        assert "_dead-code-baseline-refresh PASS" in status
        assert receipt.read_bytes() == original
        assert not Path(resume_env["GATE_PREFLIGHT_FAILED_FILE"]).exists()
        assert "=== GATE: PASSED ===" not in Path(
            resume_env["GATE_PREFLIGHT_GATE_STATUS"],
        ).read_text(encoding="utf-8")
    finally:
        receipt.unlink(missing_ok=True)


def test_preflights_run_when_no_resume_receipt(resume_env: dict[str, str]) -> None:
    """Without a receipt the configured fixture target must run and fail."""
    # Keep the original distinct candidate input as well as a private receipt path.
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
    """A valid receipt cannot bypass an explicitly disabled resume."""
    disabled_sha = "deadbeef11223344556677889900aabbccdd1122"  # pragma: allowlist secret
    receipt = _resume_receipt(resume_env)

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


@pytest.mark.parametrize(
    ("receipt_case", "reason"),
    (
        ("unsigned", "gate attestation is missing"),
        ("tampered", "gate status digest mismatch"),
        ("stale", "gate attestation is stale"),
        ("policy", "resume gate policy is missing, duplicate or changed"),
    ),
)
def test_preflights_refuse_unauthenticated_stale_or_changed_policy(
    resume_env: dict[str, str], receipt_case: str, reason: str,
) -> None:
    receipt = _resume_receipt(resume_env, receipt_case=receipt_case)
    original = receipt.read_bytes()
    result = _run_preflights(resume_env)

    assert result.returncode == 2, result.stdout + result.stderr
    assert reason in result.stderr
    assert "PREFLIGHT-COLD: prior status failed current-state/policy authentication" in result.stdout
    assert "PREFLIGHT-RESUME:" not in result.stdout
    assert "=== GATE PREFLIGHT (resume-always):" not in result.stdout
    assert "_gate-preflight-fixture-fail FAIL" in result.stdout
    assert Path(resume_env["GATE_PREFLIGHT_STATUS"]).read_text().splitlines() == [
        "_gate-preflight-fixture-fail FAIL 2",
    ]
    assert Path(resume_env["GATE_PREFLIGHT_FAILED_FILE"]).is_file()
    assert receipt.read_bytes() == original


def test_signed_resume_receipt_does_not_authorize_a_green_gate(resume_env: dict[str, str]) -> None:
    receipt = _resume_receipt(resume_env)
    state = repository_state_id(receipt.parent)

    assert verify_terminal_status(receipt, state_id=state, key=_KEY).ok
    resume = verify_resume_status(
        receipt, state_id=state, key=_KEY, profile="native", max_files_per_batch=16, workers=1,
    )
    assert resume.ok, resume.reason
    full_gate = verify_status(receipt, state_id=state, key=_KEY)
    assert not full_gate.ok
    assert "not a uniquely completed passed gate" in full_gate.reason
