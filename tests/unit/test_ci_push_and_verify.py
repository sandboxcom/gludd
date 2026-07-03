"""Tests for ci_push_and_verify.sh — CI push + wait + report cycle."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "ci_push_and_verify.sh"
MAKEFILE = ROOT / "Makefile"


def test_script_exists_and_is_executable() -> None:
    """Script exists at scripts/ci_push_and_verify.sh and has +x bit set."""
    assert SCRIPT.is_file(), (
        f"Regression: {SCRIPT} is missing — the ci-push-and-verify script "
        "does not exist."
    )
    st = SCRIPT.stat()
    assert st.st_mode & stat.S_IXUSR, (
        f"{SCRIPT} is not executable (owner x-bit missing). "
        "Run: chmod +x scripts/ci_push_and_verify.sh"
    )


def test_makefile_target_exists_and_callable() -> None:
    """Makefile defines ci-push-and-verify and ci-verify-wait targets."""
    content = MAKEFILE.read_text(encoding="utf-8")

    for target in ("ci-push-and-verify:", "ci-verify-wait:", "_require-gh:"):
        assert target in content, (
            f"Makefile regression: target '{target.rstrip(':')}' not found in "
            "Makefile. The ci-push-and-verify workflow requires this target."
        )

    # ci-push-and-verify must reference the script
    assert "scripts/ci_push_and_verify.sh" in content, (
        "Makefile regression: ci-push-and-verify target no longer invokes "
        "scripts/ci_push_and_verify.sh"
    )

    # ci-verify-wait must set CI_DRY_RUN=1
    assert "CI_DRY_RUN=1" in content, (
        "Makefile regression: ci-verify-wait target no longer sets "
        "CI_DRY_RUN=1 (dry-run mode must skip the push)."
    )


def test_script_handles_missing_or_unmatched_run_gracefully() -> None:
    """Script exits 2 with TIMEOUT message when gh finds no matching run.

    This covers both 'gh not available' (returns []) and 'SHA has no CI run'
    (also returns []) — both cases cause the poll loop to exhaust and exit 2
    with a TIMEOUT message.  A fake 40-char hex SHA guarantees no match.
    """
    fake_sha = "feed0000000000000000000000000000000000000"
    env = {
        **os.environ,
        "CI_DRY_RUN": "1",
        "CI_POLL_SECS": "0",
        "CI_MAX_POLLS": "1",
    }
    result = subprocess.run(
        ["bash", str(SCRIPT), fake_sha],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    combined = (result.stdout + result.stderr).lower()
    assert result.returncode == 2, (
        f"Expected exit code 2 (TIMEOUT / no-matching-run), got {result.returncode}.\n"
        f"stdout: {result.stdout[-500:]}\nstderr: {result.stderr[-500:]}"
    )
    assert "timeout" in combined, (
        f"Expected 'timeout' in output when no CI run matches. "
        f"No 'timeout' found.\nstdout: {result.stdout[-500:]}\nstderr: {result.stderr[-500:]}"
    )
