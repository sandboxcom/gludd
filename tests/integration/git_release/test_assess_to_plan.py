"""Integration tests: assess_repo -> discover -> rank -> release_plan (GRC-AT-001/004).

Exercises the read-only evidence -> helper intelligence -> release planning
pipeline end-to-end against a real (fixture) git repository. Verifies:

- ``assess_repo`` returns a spec-shaped ``RepoEvidence`` (GRC-AT-001).
- ``discover_helpers`` finds every CI-invoked entry point (GRC-AT-004).
- ``rank_helpers`` orders candidates by the spec authority chain
  (repository > ci-used > ecosystem > generated).
- A derived ``ReleasePlan`` carries the required gates, artifacts, deployment,
  and rollback sub-records (spec §5.3).
- ``helper_build_file_changes`` makes ZERO changes when an adequate helper
  exists (GRC-AT-005).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from general_ludd.git_release.contracts import ReleasePlan, RepoEvidence
from general_ludd.git_release.helper_catalog import HelperCandidate, discover_helpers
from general_ludd.git_release.helper_ranker import (
    TaskRequirements,
    helper_build_file_changes,
    rank_helpers,
)
from general_ludd.git_release.topology import assess_repo


# ---------------------------------------------------------------------------
# Fixture: a real git repo on disk with the kinds of files discovery scans for.
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture()
def fixture_repo(tmp_path: Path) -> Path:
    """A repo with Makefile, pyproject, CI workflow, README, and Dockerfile."""
    repo = tmp_path / "fixture"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")

    (repo / "README.md").write_text("# fixture\n")
    (repo / "AGENTS.md").write_text("# agents\n")
    (repo / "Makefile").write_text("build:\n\t@echo build\n")
    (repo / "pyproject.toml").write_text("[project]\nname = 'fixture'\n")
    (repo / "Dockerfile").write_text("FROM python:3.12\n")

    ci_dir = repo / ".github" / "workflows"
    ci_dir.mkdir(parents=True)
    (ci_dir / "ci.yml").write_text("name: ci\non: [push]\n")

    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial fixture state")
    return repo


# ---------------------------------------------------------------------------
# GRC-AT-001: assess_repo returns a spec-shaped RepoEvidence (read-only).
# ---------------------------------------------------------------------------


def test_assess_repo_returns_spec_shaped_evidence(fixture_repo: Path) -> None:
    evidence = assess_repo(str(fixture_repo))

    assert isinstance(evidence, RepoEvidence)
    assert evidence.repo_root == str(fixture_repo.resolve())
    assert len(evidence.head_sha) == 40
    assert all(c in "0123456789abcdef" for c in evidence.head_sha)
    assert evidence.branch == "main"
    assert evidence.dirty_paths == []
    assert evidence.operations == []
    # evidence_time is RFC3339 (YYYY-MM-DDTHH:MM:SS...).
    assert evidence.evidence_time.startswith("202")
    assert "T" in evidence.evidence_time


def test_assess_repo_is_read_only_and_idempotent(fixture_repo: Path) -> None:
    head_before = _git(fixture_repo, "rev-parse", "HEAD")
    evidence_a = assess_repo(str(fixture_repo))
    evidence_b = assess_repo(str(fixture_repo))
    head_after = _git(fixture_repo, "rev-parse", "HEAD")

    assert head_before == head_after, "assess_repo mutated the repository"
    assert evidence_a.head_sha == evidence_b.head_sha
    assert evidence_a == evidence_b


def test_assess_repo_records_dirty_paths_when_tree_modified(fixture_repo: Path) -> None:
    (fixture_repo / "README.md").write_text("changed\n")
    (fixture_repo / "untracked.txt").write_text("new\n")
    evidence = assess_repo(str(fixture_repo))

    paths = {d.path for d in evidence.dirty_paths}
    assert "README.md" in paths
    untracked = {d.path for d in evidence.dirty_paths if d.untracked}
    assert "untracked.txt" in untracked


# ---------------------------------------------------------------------------
# GRC-AT-004: discover_helpers finds every CI-invoked + repo-native entry point.
# ---------------------------------------------------------------------------


def test_discover_helpers_finds_repo_native_and_ci_entry_points(fixture_repo: Path) -> None:
    candidates = discover_helpers(str(fixture_repo))
    paths = {c.source_path for c in candidates}
    authorities = {c.source_path: c.authority for c in candidates}

    # Repo-native build/test files.
    assert "Makefile" in paths
    assert authorities["Makefile"] == "repository"
    # Repo docs.
    assert "README.md" in paths
    assert "AGENTS.md" in paths
    # Ecosystem manifest.
    assert "pyproject.toml" in paths
    assert authorities["pyproject.toml"] == "ecosystem"
    # Deploy file.
    assert "Dockerfile" in paths
    # CI workflow file (authoritative for build selection per spec §4.3).
    ci_path = ".github/workflows/ci.yml"
    assert ci_path in paths
    assert authorities[ci_path] == "ci-used"


# ---------------------------------------------------------------------------
# Spec §4.3 priority chain: repository > ci-used > ecosystem > generated.
# ---------------------------------------------------------------------------


def test_rank_helpers_orders_by_authority_then_score(fixture_repo: Path) -> None:
    candidates = discover_helpers(str(fixture_repo))
    ranked = rank_helpers(candidates, TaskRequirements(kind="build", min_score=0))

    assert ranked, "expected at least one ranked build helper"
    authorities = [c.authority for c in ranked]
    # Repository-authority helpers must appear before ci-used and ecosystem.
    repo_idx = authorities.index("repository") if "repository" in authorities else len(authorities)
    ci_idx = authorities.index("ci-used") if "ci-used" in authorities else len(authorities)
    eco_idx = authorities.index("ecosystem") if "ecosystem" in authorities else len(authorities)
    assert repo_idx < ci_idx, "repository helper must outrank ci-used"
    assert ci_idx < eco_idx, "ci-used helper must outrank ecosystem"


def test_rank_helpers_filters_below_threshold_and_records_evidence(fixture_repo: Path) -> None:
    candidates = discover_helpers(str(fixture_repo))
    # An unreachably high threshold filters everything out.
    ranked = rank_helpers(candidates, TaskRequirements(kind="build", min_score=100))
    assert ranked == []

    # A reachable threshold keeps helpers and each carries score_evidence.
    ranked = rank_helpers(candidates, TaskRequirements(kind="build", min_score=0))
    assert ranked
    for cand in ranked:
        assert 0 <= cand.score <= 100
        assert len(cand.score_evidence) == 10, "spec §4.3 requires ten scoring criteria"


# ---------------------------------------------------------------------------
# GRC-AT-005: adequate helper => zero file changes from helper_build.
# ---------------------------------------------------------------------------


def test_helper_build_makes_zero_changes_when_adequate_helper_exists(fixture_repo: Path) -> None:
    candidates = discover_helpers(str(fixture_repo))
    ranked = rank_helpers(candidates, TaskRequirements(kind="build", min_score=0))
    changes = helper_build_file_changes(ranked, repo_root=str(fixture_repo))
    assert changes == [], "helper_build must not generate when an adequate helper exists"


def test_helper_build_emits_plan_when_no_adequate_helper(fixture_repo: Path) -> None:
    candidates = discover_helpers(str(fixture_repo))
    # Filter to nothing via threshold; helper_build must emit a generation plan.
    ranked = rank_helpers(candidates, TaskRequirements(kind="build", min_score=100))
    changes = helper_build_file_changes(ranked, repo_root=str(fixture_repo))
    assert len(changes) == 1
    assert "generate" in changes[0].lower()


# ---------------------------------------------------------------------------
# ReleasePlan derivation: gates + artifacts + deployment + rollback present.
# ---------------------------------------------------------------------------


def test_release_plan_carries_required_subrecords(fixture_repo: Path) -> None:
    """A plan derived from evidence + ranked helpers has every spec §5.3 field."""
    evidence = assess_repo(str(fixture_repo))
    candidates = discover_helpers(str(fixture_repo))
    ranked = rank_helpers(candidates, TaskRequirements(kind="build", min_score=0))
    assert ranked, "fixture repo must yield at least one ranked build helper"

    # The repo-native build helper is the chosen entry point (highest authority).
    chosen = ranked[0]
    plan = ReleasePlan.model_validate(
        {
            "release_id": str(uuid4()),
            "source_sha": evidence.head_sha,
            "version": "v1.2.3",
            "change_set": [evidence.head_sha],
            "required_gates": [
                {
                    "id": "unit",
                    "command_id": chosen.invocation_id or "make.test",
                    "timeout_s": 600,
                    "success_contract": "exit_zero",
                },
            ],
            "artifacts": [
                {
                    "id": "wheel",
                    "platform": "linux/amd64",
                    "format": "wheel",
                    "expected_name": "fixture-1.2.3-py3-none-any.whl",
                    "verification": "sha256",
                },
            ],
            "provenance": {
                "sbom": "sbom.json",
                "signature": "sigstore",
                "attestation": "provenance.json",
                "builder_identity": "ci-builder",
            },
            "deployment": {
                "strategy": "canary",
                "stages": ["5pct", "25pct", "50pct", "100pct"],
                "health_gates": ["error_rate<0.01"],
                "pause_points": ["25pct"],
            },
            "rollback": {
                "trigger": "health_regression",
                "target": "0" * 40,
                "data_compatibility": "backward",
                "command_id": "deploy.rollback",
            },
            "approvals": [
                {"scope": "production", "approver_class": "release-captain", "state": "approved"},
            ],
        }
    )

    # Version is normalized (leading v stripped).
    assert plan.version == "1.2.3"
    # Required gates are non-empty and carry a command_id + timeout.
    assert plan.required_gates
    assert plan.required_gates[0].command_id
    assert plan.required_gates[0].timeout_s > 0
    # Artifacts are non-empty and carry platform + format + verification.
    assert plan.artifacts
    assert plan.artifacts[0].platform
    assert plan.artifacts[0].verification == "sha256"
    # Deployment strategy + stages + health gates + pause points.
    assert plan.deployment.strategy == "canary"
    assert plan.deployment.stages
    assert plan.deployment.health_gates
    # Rollback carries trigger + target + command_id.
    assert plan.rollback.trigger
    assert plan.rollback.command_id == "deploy.rollback"
    # Provenance builder_identity is required and non-null.
    assert plan.provenance.builder_identity == "ci-builder"
