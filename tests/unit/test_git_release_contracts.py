"""Unit tests for general_ludd.git_release.contracts + topology (GRC-001 §5).

Covers the GRC-P1 read-only Git evidence surface:
- RepoEvidence data contract (JSON round-trip, validation, fail-closed fields)
- HelperCandidate authority ranking and scoring
- ReleasePlan version normalization
- ReleaseVerdict state machine
- assess_repo() read-only topology collection
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from general_ludd.git_release.contracts import (
    HelperAuthority,
    HelperCandidate,
    ReleasePlan,
    ReleaseVerdict,
    ReleaseVerdictState,
    RepoEvidence,
)
from general_ludd.git_release.topology import assess_repo

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _sha(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


_VALID_EVIDENCE_KWARGS: dict[str, object] = {
    "schema_version": "1",
    "repo_root": "/abs/path",
    "head_sha": "0" * 40,
    "branch": "main",
    "upstreams": [],
    "worktrees": [],
    "operations": [],
    "dirty_paths": [],
    "policies": [],
    "evidence_time": "2026-07-30T00:00:00Z",
}


# ---------------------------------------------------------------------------
# RepoEvidence
# ---------------------------------------------------------------------------


def test_repo_evidence_json_round_trip() -> None:
    evidence = RepoEvidence(**_VALID_EVIDENCE_KWARGS)
    raw = evidence.model_dump_json()
    restored = RepoEvidence.model_validate_json(raw)
    assert restored == evidence


def test_repo_evidence_rejects_missing_required_field() -> None:
    # head_sha + evidence_time missing → required-field validation fails.
    with pytest.raises(ValidationError):
        RepoEvidence.model_validate({"schema_version": "1", "repo_root": "/x"})


def test_repo_evidence_rejects_malformed_head_sha() -> None:
    kwargs = dict(_VALID_EVIDENCE_KWARGS)
    kwargs["head_sha"] = "not-a-sha"
    with pytest.raises(ValidationError):
        RepoEvidence(**kwargs)


def test_repo_evidence_head_sha_requires_40_chars() -> None:
    kwargs = dict(_VALID_EVIDENCE_KWARGS)
    kwargs["head_sha"] = "abc123"
    with pytest.raises(ValidationError):
        RepoEvidence(**kwargs)


def test_repo_evidence_upstreams_carries_ahead_behind() -> None:
    kwargs = dict(_VALID_EVIDENCE_KWARGS)
    kwargs["upstreams"] = [
        {"local_ref": "main", "remote_ref": "origin/main", "ahead": 2, "behind": 0},
    ]
    evidence = RepoEvidence(**kwargs)
    assert evidence.upstreams[0].ahead == 2
    assert evidence.upstreams[0].behind == 0


def test_repo_evidence_worktrees_carries_dirty_flag() -> None:
    kwargs = dict(_VALID_EVIDENCE_KWARGS)
    kwargs["worktrees"] = [
        {"path": "/repo", "branch": "main", "head_sha": "1" * 40, "dirty": True},
    ]
    evidence = RepoEvidence(**kwargs)
    assert evidence.worktrees[0].dirty is True


def test_repo_evidence_dirty_paths_classifies_states() -> None:
    kwargs = dict(_VALID_EVIDENCE_KWARGS)
    kwargs["dirty_paths"] = [
        {"path": "src/x.py", "index_state": "modified", "worktree_state": "modified", "untracked": False},
        {"path": "scratch.txt", "index_state": None, "worktree_state": None, "untracked": True},
    ]
    evidence = RepoEvidence(**kwargs)
    assert evidence.dirty_paths[1].untracked is True
    assert evidence.dirty_paths[0].worktree_state == "modified"


def test_repo_evidence_policies_carry_digest() -> None:
    kwargs = dict(_VALID_EVIDENCE_KWARGS)
    kwargs["policies"] = [
        {"source": ".gitattributes", "rule_id": "*.txt", "text_digest": "sha256:abc"},
    ]
    evidence = RepoEvidence(**kwargs)
    assert evidence.policies[0].source == ".gitattributes"
    assert evidence.policies[0].text_digest.startswith("sha256:")


def test_repo_evidence_evidence_time_must_be_rfc3339() -> None:
    kwargs = dict(_VALID_EVIDENCE_KWARGS)
    kwargs["evidence_time"] = "30/7/2026"
    with pytest.raises(ValidationError):
        RepoEvidence(**kwargs)


def test_repo_evidence_extra_fields_ignored_for_forward_compat() -> None:
    raw = dict(_VALID_EVIDENCE_KWARGS)
    raw["future_additive_field"] = "ignored"
    evidence = RepoEvidence.model_validate(raw)
    assert not hasattr(evidence, "future_additive_field")


# ---------------------------------------------------------------------------
# HelperCandidate
# ---------------------------------------------------------------------------


def test_helper_authority_rank_repository_is_highest() -> None:
    assert HelperAuthority.REPOSITORY.rank() < HelperAuthority.CI_USED.rank()
    assert HelperAuthority.CI_USED.rank() < HelperAuthority.ECOSYSTEM.rank()
    assert HelperAuthority.ECOSYSTEM.rank() < HelperAuthority.GENERATED.rank()


def test_helper_candidate_score_bounded_0_to_100() -> None:
    base = {
        "id": "x",
        "kind": "build",
        "source_path": "Makefile",
        "authority": "repository",
        "invocation_id": "cmd.1",
        "score": 50,
    }
    HelperCandidate(**base)  # 50 is valid.
    base["score"] = 101
    with pytest.raises(ValidationError):
        HelperCandidate(**base)
    base["score"] = -1
    with pytest.raises(ValidationError):
        HelperCandidate(**base)


def test_helper_candidate_serializes_ci_used_with_dash() -> None:
    cand = HelperCandidate(
        id="x",
        kind="test",
        source_path=".github/workflows/test.yml",
        authority="ci-used",
        invocation_id="cmd.2",
        score=80,
        score_evidence=[{"criterion": "fitness", "value": 0.8, "source": "ci-trace"}],
    )
    dumped = json.loads(cand.model_dump_json())
    assert dumped["authority"] == "ci-used"
    assert dumped["score_evidence"][0]["criterion"] == "fitness"


# ---------------------------------------------------------------------------
# ReleasePlan
# ---------------------------------------------------------------------------


_VALID_PLAN_KWARGS: dict[str, object] = {
    "release_id": "123e4567-e89b-12d3-a456-426614174000",
    "source_sha": "0" * 40,
    "version": "1.0.0",
    "change_set": [],
    "required_gates": [],
    "artifacts": [],
    "provenance": {
        "sbom": None,
        "signature": None,
        "attestation": None,
        "builder_identity": "ci-builder",
    },
    "deployment": {"strategy": "rolling", "stages": [], "health_gates": [], "pause_points": []},
    "rollback": {
        "trigger": "health",
        "target": "0" * 40,
        "data_compatibility": "backward",
        "command_id": "cmd.r",
    },
    "approvals": [],
}


def test_release_plan_version_normalizes_leading_v() -> None:
    kwargs = dict(_VALID_PLAN_KWARGS)
    kwargs["version"] = "v1.2.3"
    plan = ReleasePlan(**kwargs)
    assert plan.version == "1.2.3"


def test_release_plan_rejects_invalid_version() -> None:
    kwargs = dict(_VALID_PLAN_KWARGS)
    kwargs["version"] = "not-a-version"
    with pytest.raises(ValidationError):
        ReleasePlan(**kwargs)


def test_release_plan_provenance_builder_identity_required() -> None:
    kwargs = dict(_VALID_PLAN_KWARGS)
    kwargs["provenance"] = {
        "sbom": None,
        "signature": None,
        "attestation": None,
        "builder_identity": None,
    }
    with pytest.raises(ValidationError):
        ReleasePlan(**kwargs)


# ---------------------------------------------------------------------------
# ReleaseVerdict
# ---------------------------------------------------------------------------


_VALID_VERDICT_KWARGS: dict[str, object] = {
    "release_id": "123e4567-e89b-12d3-a456-426614174000",
    "source_sha": "0" * 40,
    "tag_target_sha": "0" * 40,
    "gate_results": [],
    "artifact_results": [],
    "deployment_results": [],
    "release_page": {"url": "", "asset_names": [], "asset_digests": {}},
    "state": "blocked",
    "reasons": [],
}


def test_release_verdict_rejects_unknown_state() -> None:
    kwargs = dict(_VALID_VERDICT_KWARGS)
    kwargs["state"] = "frozen"
    with pytest.raises(ValidationError):
        ReleaseVerdict(**kwargs)


def test_release_verdict_state_machine_blocked_to_ready_allowed() -> None:
    v = ReleaseVerdict(**_VALID_VERDICT_KWARGS)
    assert v.state == ReleaseVerdictState.BLOCKED
    assert v.can_transition_to(ReleaseVerdictState.READY) is True
    assert v.can_transition_to(ReleaseVerdictState.RELEASED) is False


def test_release_verdict_state_machine_deploying_branches() -> None:
    kwargs = dict(_VALID_VERDICT_KWARGS)
    kwargs["state"] = "deploying"
    deploying = ReleaseVerdict(**kwargs)
    assert deploying.can_transition_to(ReleaseVerdictState.RELEASED) is True
    assert deploying.can_transition_to(ReleaseVerdictState.ROLLED_BACK) is True
    assert deploying.can_transition_to(ReleaseVerdictState.READY) is False


def test_release_verdict_released_is_terminal() -> None:
    kwargs = dict(_VALID_VERDICT_KWARGS)
    kwargs["state"] = "released"
    released = ReleaseVerdict(**kwargs)
    assert released.can_transition_to(ReleaseVerdictState.READY) is False
    assert released.can_transition_to(ReleaseVerdictState.ROLLED_BACK) is False


def test_release_verdict_blocked_carries_reason_codes() -> None:
    kwargs = dict(_VALID_VERDICT_KWARGS)
    kwargs["reasons"] = ["GRC-SEC-004", "GRC-AT-008"]
    v = ReleaseVerdict(**kwargs)
    assert "GRC-SEC-004" in v.reasons


# ---------------------------------------------------------------------------
# topology.assess_repo()
# ---------------------------------------------------------------------------


def test_assess_repo_clean_tree(tmp_repo: Path) -> None:
    evidence = assess_repo(str(tmp_repo))
    assert evidence.head_sha == _sha(tmp_repo)
    assert evidence.branch == "main"
    assert evidence.dirty_paths == []
    assert evidence.operations == []


def test_assess_repo_collects_dirty_path(tmp_repo: Path) -> None:
    (tmp_repo / "README.md").write_text("changed\n")
    evidence = assess_repo(str(tmp_repo))
    paths = {d.path for d in evidence.dirty_paths}
    assert "README.md" in paths


def test_assess_repo_collects_untracked_path(tmp_repo: Path) -> None:
    (tmp_repo / "scratch.txt").write_text("noise\n")
    evidence = assess_repo(str(tmp_repo))
    untracked = {d.path for d in evidence.dirty_paths if d.untracked}
    assert "scratch.txt" in untracked


def test_assess_repo_records_gitattributes_policy(tmp_repo: Path) -> None:
    (tmp_repo / ".gitattributes").write_text("*.txt text\n")
    _git(tmp_repo, "add", ".gitattributes")
    _git(tmp_repo, "commit", "-q", "-m", "attrs")
    evidence = assess_repo(str(tmp_repo))
    sources = {p.source for p in evidence.policies}
    assert ".gitattributes" in sources


def test_assess_repo_fail_closed_on_inprogress_rebase(tmp_repo: Path) -> None:
    # Create a conflict that leaves a rebase in progress.
    _git(tmp_repo, "checkout", "-q", "-b", "feature")
    (tmp_repo / "README.md").write_text("feature\n")
    _git(tmp_repo, "commit", "-q", "-am", "feature")
    _git(tmp_repo, "checkout", "-q", "main")
    (tmp_repo / "README.md").write_text("main\n")
    _git(tmp_repo, "commit", "-q", "-am", "main")
    _git(tmp_repo, "checkout", "-q", "feature")
    subprocess.run(
        ["git", "-C", str(tmp_repo), "rebase", "main"],
        capture_output=True,
        text=True,
        check=False,
    )
    evidence = assess_repo(str(tmp_repo))
    kinds = {op.kind for op in evidence.operations}
    assert "rebase" in kinds


def test_assess_repo_missing_path_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        assess_repo(str(tmp_path / "nope"))


def test_assess_repo_non_git_dir_raises_runtime_error(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    (not_a_repo / "f.txt").write_text("x\n")
    with pytest.raises(RuntimeError, match="not a git repository"):
        assess_repo(str(not_a_repo))
