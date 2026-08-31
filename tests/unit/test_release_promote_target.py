"""Executable contract tests for exact-SHA release promotion."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.check_make_target_contract import _stanzas

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
CONTRACT = ROOT / "config" / "make_target_contract.json"


def _target_block(name: str) -> str:
    content = MAKEFILE.read_text(encoding="utf-8")
    marker = f"\n{name}:"
    start = content.index(marker)
    end = content.find("\n\n", start)
    return content[start : len(content) if end == -1 else end]


def _run_promote(*variables: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["make", "--no-print-directory", "release-promote", *variables],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_real_promotion_orders_evidence_before_the_only_ref_mutation() -> None:
    """Evidence and readiness must pass before master can move."""
    block = _target_block("release-promote")

    dual_track = block.index('DUAL_TRACK_CI_VALIDATE_ONLY=0')
    readiness = block.index('RELEASE_READINESS_VALIDATE_ONLY=0')
    merge = block.index('merge --ff-only development')
    publication = block.index('release-cut TAG="$(TAG)"')

    assert dual_track < readiness < merge < publication


def test_promotion_is_confined_to_the_canonical_main_checkout() -> None:
    """The development worktree may orchestrate but cannot own master."""
    block = _target_block("release-promote")

    assert "MAIN_PATH='/Users/shawnwilson/gludd'" in block
    assert '[ "$$CURRENT_SHA" = "$$DEV_SHA" ]' in block
    assert "worktree-guard" in block
    assert "main-worktree-guard" in block
    assert 'git -C "$$MAIN_PATH" merge-base --is-ancestor' in block
    assert 'git -C "$$MAIN_PATH" merge --ff-only development' in block
    assert "git checkout" not in block
    assert "merge --no-ff" not in block


def test_validation_mode_has_no_publication_or_ref_write_path() -> None:
    """Validate-only proves the policy while leaving refs and remotes untouched."""
    block = _target_block("release-promote")
    validate_start = block.index('if [ "$(RELEASE_PROMOTE_VALIDATE_ONLY)" = "1" ]')
    validate_end = block.index("\tfi;", validate_start)
    validate_block = block[validate_start:validate_end]

    assert "DUAL_TRACK_CI_VALIDATE_ONLY=1" in validate_block
    assert "RELEASE_READINESS_VALIDATE_ONLY=1" in validate_block
    assert "RELEASE-PROMOTE-VALIDATED" in validate_block
    assert "merge --ff-only" not in validate_block
    assert "release-cut" not in validate_block
    assert "git push" not in validate_block


def test_promotion_delegates_publication_instead_of_reimplementing_it() -> None:
    """Tagging, pushing, and artifact polling remain owned by release-cut."""
    block = _target_block("release-promote")

    assert 'release-cut TAG="$(TAG)" MSG="$(MSG)"' in block
    assert "git-tag-push" not in block
    assert "git-push-sandboxcom" not in block
    assert "gh release" not in block


def test_missing_tag_and_invalid_mode_fail_before_topology_checks() -> None:
    """Operator-input failures are fast, deterministic, and side-effect free."""
    missing = _run_promote("RELEASE_PROMOTE_VALIDATE_ONLY=1")
    invalid = _run_promote("TAG=v0.1.0-beta.4", "RELEASE_PROMOTE_VALIDATE_ONLY=maybe")

    assert missing.returncode == 2
    assert "Usage: make release-promote" in missing.stdout + missing.stderr
    assert invalid.returncode == 2
    assert "must be 0 or 1" in invalid.stdout + invalid.stderr


def test_public_help_and_make_contract_include_safe_behavior() -> None:
    """The documented command must be mechanically discoverable and runnable."""
    makefile = MAKEFILE.read_text(encoding="utf-8")
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
    entries = {entry["name"]: entry for entry in payload["targets"]}

    assert "release-promote TAG=.. MSG=.." in makefile
    assert makefile.count('@echo "  release-promote') == 1
    assert "release-promote" in entries
    assert entries["release-promote"]["make_variables"] == [
        "TAG",
        "MSG",
        "RELEASE_PROMOTE_VALIDATE_ONLY",
    ]
    assert "RELEASE_PROMOTE_VALIDATE_ONLY=1" in entries["release-promote"]["behavior"]


def test_release_deploy_is_only_a_compatibility_alias() -> None:
    """No public deployment target may bypass the promotion owner."""
    block = _target_block("release-deploy")

    assert 'release-promote TAG="$(TAG)" MSG="$(MSG)"' in block
    for forbidden in (
        "development-merge-to-master",
        "git-push-sandboxcom",
        "git-tag-push",
        "ci-await",
        "verify-release-artifact",
        "verify-release-completeness",
    ):
        assert forbidden not in block


def test_make_contract_parser_keeps_the_final_target_at_eof() -> None:
    """A public target at EOF must not disappear from static contract checks."""
    makefile = "first:\n\t@echo first\n\nrelease-promote:\n\t@echo promote\n"

    stanzas = _stanzas(makefile)

    assert "release-promote" in stanzas
    assert "@echo promote" in stanzas["release-promote"]


def test_promote_carries_source_bound_evidence_across_fast_forward() -> None:
    """Publication must retain the already-validated development evidence identity."""
    block = _target_block("release-promote")

    evidence = block.index('LOCAL_ATTESTATION=')
    merge = block.index('merge --ff-only development')
    publication = block.index('release-cut TAG="$(TAG)"')

    assert evidence < merge < publication
    assert 'RELEASE_CANDIDATE_SHA="$$DEV_SHA"' in block
    assert 'RELEASE_CI_BRANCH=development' in block
    assert 'RELEASE_LOCAL_ATTESTATION="$$LOCAL_ATTESTATION"' in block


def test_release_cut_revalidates_explicit_source_bound_evidence() -> None:
    """The cut must bind its repeated check to the promoted SHA and source evidence."""
    block = _target_block("release-cut")

    assert "RELEASE_CANDIDATE_SHA" in block
    assert "RELEASE_CI_BRANCH" in block
    assert "RELEASE_LOCAL_ATTESTATION" in block
    assert '[ "$$HEAD_SHA" = "$$SHA_TO_VERIFY" ]' in block
    assert 'CI_BRANCH="$(RELEASE_CI_BRANCH)"' in block
    assert 'DUAL_TRACK_CI_LOCAL_ATTESTATION="$(RELEASE_LOCAL_ATTESTATION)"' in block


def test_dual_track_gate_accepts_explicit_branch_and_local_attestation() -> None:
    """Exact evidence lookup must not silently switch namespaces after promotion."""
    ci_block = _target_block("require-ci-green")
    dual_block = _target_block("require-dual-track-green")

    assert '"$(CI_BRANCH)"' in ci_block
    assert 'CI_BRANCH="$(CI_BRANCH)"' in dual_block
    assert '--local-attestation "$(DUAL_TRACK_CI_LOCAL_ATTESTATION)"' in dual_block
