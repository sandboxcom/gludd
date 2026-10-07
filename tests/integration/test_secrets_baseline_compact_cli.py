"""Real detect-secrets compatibility checks for the compact baseline."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from scripts.manage_secrets_baseline import (
    _scan_fresh,
    finding_counts,
    load_policy,
    validate_payload,
)

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / ".secrets.baseline"
POLICY = ROOT / "config" / "detect_secrets_baseline_policy.json"
HOOK = shutil.which("detect-secrets-hook")
AUDIT = shutil.which("detect-secrets")


@pytest.mark.skipif(AUDIT is None, reason="detect-secrets is unavailable")
def test_pinned_scanner_accepts_policy_filter_and_exclusions(tmp_path: Path) -> None:
    """The wrapper uses supported detect-secrets CLI/configuration surfaces."""
    (tmp_path / "benign.py").write_text("answer = 42\n", encoding="utf-8")
    destination = tmp_path / "scan.json"
    policy = load_policy(POLICY)

    payload = _scan_fresh(
        executable=str(AUDIT),
        policy=policy,
        repository=tmp_path,
        seed=BASELINE,
        destination=destination,
    )

    validate_payload(payload, policy)
    assert finding_counts(payload) == {}


@pytest.mark.skipif(HOOK is None, reason="detect-secrets-hook is unavailable")
def test_compact_baseline_detects_new_credential_fixture(tmp_path: Path) -> None:
    """A dynamically assembled credential remains a new, blocking finding."""
    disposable = tmp_path / ".secrets.baseline"
    shutil.copyfile(BASELINE, disposable)
    candidate = tmp_path / "candidate.py"
    candidate.write_text("token = '" + "ghp_" + ("A1b2" * 9) + "'\n", encoding="utf-8")

    completed = subprocess.run(
        [str(HOOK), "--baseline", str(disposable), str(candidate)],
        capture_output=True,
        check=False,
    )
    assert completed.returncode in {1, 3}, (
        "new credential fixture did not produce the upstream blocking status: "
        f"status={completed.returncode}"
    )


@pytest.mark.skipif(AUDIT is None, reason="detect-secrets is unavailable")
def test_compact_baseline_is_accepted_by_upstream_audit(tmp_path: Path) -> None:
    """Canonical compaction must not switch to upstream's non-auditable slim form."""
    disposable = tmp_path / ".secrets.baseline"
    shutil.copyfile(BASELINE, disposable)

    completed = subprocess.run(
        [str(AUDIT), "audit", "--stats", str(disposable)],
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, (
        f"detect-secrets audit rejected compact JSON with status {completed.returncode}"
    )
