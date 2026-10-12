"""Behavioral contracts for cheap, head-independent final-proof admission."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
PHASES = (
    "disk-cleanup-preflight", "gate-admission-config", "check-task-registration",
    "check-task-integrity", "check-gate-failure-promotions",
    "check-resource-ownership", "_pytest-runtime-compatibility-admission",
)


def _recipe() -> str:
    source = compose_makefile(ROOT / "Makefile")
    marker = "\nrelease-proof-admission:\n"
    assert marker in source, "Missing final-proof admission owner"
    return source.split(marker, 1)[1].split("\n\n", 1)[0]


def _run(
    tmp_path: Path, *, mode: str = "0", failed_phase: str = "",
    claimed_head: str = "a" * 40,
) -> subprocess.CompletedProcess[str]:
    targets = "".join(
        f"{phase}:\n\t@printf '%s\\n' '{phase}' >> phases.log\n"
        f"\t@test '{phase}' != '$(FAILED_PHASE)'\n"
        for phase in PHASES
    )
    (tmp_path / "Makefile").write_text(
        "RELEASE_PROOF_ADMISSION_VALIDATE_ONLY ?= 0\n"
        + "release-proof-admission:\n" + _recipe() + "\n\n" + targets,
        encoding="utf-8",
    )
    return subprocess.run(
        ["make", "--no-print-directory", "release-proof-admission",
         f"RELEASE_PROOF_ADMISSION_VALIDATE_ONLY={mode}",
         f"FAILED_PHASE={failed_phase}", f"CLAIMED_HEAD={claimed_head}"],
        cwd=tmp_path, capture_output=True, text=True, check=False, timeout=30,
    )


def test_plan_mode_does_not_run_owners_or_claim_admission(tmp_path: Path) -> None:
    result = _run(tmp_path, mode="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROOF-ADMISSION-PLAN" in result.stdout
    assert "PROOF-ADMISSION-PASS" not in result.stdout
    assert not (tmp_path / "phases.log").exists()


def test_invalid_mode_stops_before_any_owner(tmp_path: Path) -> None:
    result = _run(tmp_path, mode="yes")
    assert result.returncode != 0
    assert not (tmp_path / "phases.log").exists()


@pytest.mark.parametrize("failed_phase", PHASES)
def test_failed_owner_stops_later_work_without_proof_or_success(
    tmp_path: Path, failed_phase: str,
) -> None:
    result = _run(tmp_path, failed_phase=failed_phase)
    assert result.returncode != 0
    assert (tmp_path / "phases.log").read_text().splitlines() == list(
        PHASES[:PHASES.index(failed_phase) + 1]
    )
    assert f"PROOF-ADMISSION-BLOCKED phase={failed_phase}" in result.stdout
    assert "PROOF-ADMISSION-PASS" not in result.stdout
    assert not (tmp_path / ".gate-status").exists()
    assert not (tmp_path / "attestation.json").exists()


def test_changed_head_does_not_clear_an_unchanged_capacity_blocker(tmp_path: Path) -> None:
    for head in ("a" * 40, "b" * 40):
        result = _run(tmp_path, failed_phase=PHASES[0], claimed_head=head)
        assert result.returncode != 0
        assert "PROOF-ADMISSION-BLOCKED phase=disk-cleanup-preflight" in result.stdout
    assert (tmp_path / "phases.log").read_text().splitlines() == [PHASES[0], PHASES[0]]


def test_pass_runs_all_owners_in_order_but_is_not_release_proof(tmp_path: Path) -> None:
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "phases.log").read_text().splitlines() == list(PHASES)
    assert "PROOF-ADMISSION-PASS admitted_for_proof=true release_ready=false gate_evidence=false" in result.stdout
    assert not (tmp_path / ".gate-status").exists()
    assert not (tmp_path / "attestation.json").exists()


def test_real_owner_keeps_live_capacity_read_only_and_release_policy_fixed() -> None:
    owner = _recipe()
    assert "DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0" in owner
    assert "DISK_CLEANUP_PREFLIGHT_DRY_RUN=1" in owner
    assert "DISK_CLEANUP_RECEIPT_GRACE_SECONDS=1800" in owner
    assert "GATE_TEST_PROFILE=release" in owner
    assert "GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1" in owner
    assert "RESOURCE_OWNERSHIP_WRITE=0" in owner
    assert "release-promote" not in owner and "git " not in owner
