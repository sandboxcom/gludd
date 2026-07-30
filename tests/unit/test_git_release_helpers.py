"""Unit tests for helper discovery and ranking (spec GRC-001 §4.3, §5.2, GRC-P3).

Covers:
- Discovery finds Makefile / Taskfile / pyproject.toml / Dockerfile / CI workflows
- Authority classification (repository > ci-used > ecosystem > generated)
- Ranking prefers repository authority > ci-used > ecosystem > generated
- Score components recorded for every criterion
- Popularity alone does not authorize
- Adequate helper => zero file changes from helper_build
"""

from __future__ import annotations

from pathlib import Path

import pytest

from general_ludd.git_release.helper_catalog import (
    HelperCandidate,
    discover_helpers,
)
from general_ludd.git_release.helper_ranker import (
    DEFAULT_THRESHOLD,
    TaskRequirements,
    helper_build_file_changes,
    rank_helpers,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write(repo: Path, rel: str, content: str = "x\n") -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


@pytest.fixture()
def empty_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo, ".git/HEAD", "ref: refs/heads/main\n")
    return repo


@pytest.fixture()
def mixed_repo(tmp_path: Path) -> Path:
    """Repo with one of every discoverable entry point."""
    repo = tmp_path / "mixed"
    repo.mkdir()
    _write(repo, ".git/HEAD", "ref: refs/heads/main\n")
    _write(repo, "AGENTS.md", "# agent contract\n")
    _write(repo, "README.md", "# project\n")
    _write(repo, "Makefile", "test:\n\tpytest\n")
    _write(repo, "Taskfile.yml", "version: '3'\n")
    _write(repo, "justfile", "test:\n\tpytest\n")
    _write(repo, "tox.ini", "[tox]\n")
    _write(repo, "noxfile.py", "import nox\n")
    _write(repo, "pyproject.toml", "[build-system]\n")
    _write(repo, "package.json", '{"scripts": {"test": "jest"}}\n')
    _write(repo, "Cargo.toml", "[package]\n")
    _write(repo, "Dockerfile", "FROM python:3.11\n")
    _write(repo, "docker-compose.yml", "services:\n  app:\n")
    _write(repo, ".github/workflows/ci.yml", "name: ci\n")
    _write(repo, "helm/Chart.yaml", "apiVersion: v2\n")
    _write(repo, "ansible/playbook.yml", "- hosts: all\n")
    _write(repo, "terraform/main.tf", "terraform {}\n")
    return repo


# ---------------------------------------------------------------------------
# 1. Discovery finds each entry-point type
# ---------------------------------------------------------------------------


def test_discover_finds_agents_md_as_repository_authority(mixed_repo: Path) -> None:
    candidates = discover_helpers(mixed_repo)
    ids = [c.id for c in candidates]
    assert any("AGENTS.md" in i for i in ids), ids
    agents = next(c for c in candidates if "AGENTS.md" in c.id)
    assert agents.authority == "repository"


def test_discover_finds_makefile(mixed_repo: Path) -> None:
    candidates = discover_helpers(mixed_repo)
    assert any(c.source_path == "Makefile" for c in candidates), candidates


def test_discover_finds_taskfile(mixed_repo: Path) -> None:
    candidates = discover_helpers(mixed_repo)
    assert any("Taskfile" in c.source_path for c in candidates), candidates


def test_discover_finds_pyproject_toml(mixed_repo: Path) -> None:
    candidates = discover_helpers(mixed_repo)
    pyproject = [c for c in candidates if c.source_path == "pyproject.toml"]
    assert pyproject, candidates
    assert pyproject[0].authority == "ecosystem"


def test_discover_finds_package_json(mixed_repo: Path) -> None:
    candidates = discover_helpers(mixed_repo)
    assert any(c.source_path == "package.json" for c in candidates), candidates


def test_discover_finds_dockerfile(mixed_repo: Path) -> None:
    candidates = discover_helpers(mixed_repo)
    df = [c for c in candidates if c.source_path == "Dockerfile"]
    assert df, candidates
    assert df[0].kind == "deploy"


def test_discover_finds_github_workflow_as_ci_used(mixed_repo: Path) -> None:
    candidates = discover_helpers(mixed_repo)
    workflows = [c for c in candidates if c.source_path.startswith(".github/workflows/")]
    assert workflows, candidates
    assert workflows[0].authority == "ci-used"


def test_discover_finds_helm_chart(mixed_repo: Path) -> None:
    candidates = discover_helpers(mixed_repo)
    assert any("Chart.yaml" in c.source_path for c in candidates), candidates


def test_discover_finds_ansible(mixed_repo: Path) -> None:
    candidates = discover_helpers(mixed_repo)
    assert any("ansible/" in c.source_path for c in candidates), candidates


def test_discover_finds_terraform(mixed_repo: Path) -> None:
    candidates = discover_helpers(mixed_repo)
    assert any(c.source_path.endswith(".tf") for c in candidates), candidates


def test_discover_empty_repo_returns_empty(empty_repo: Path) -> None:
    assert discover_helpers(empty_repo) == []


# ---------------------------------------------------------------------------
# 2. Authority classification (spec §4.3 priority)
# ---------------------------------------------------------------------------


def test_authority_repository_beats_ci_used_beats_ecosystem_beats_generated(
    mixed_repo: Path,
) -> None:
    authorities = {c.id: c.authority for c in discover_helpers(mixed_repo)}
    rank = {"repository": 0, "ci-used": 1, "ecosystem": 2, "generated": 3}
    seen = set(authorities.values())
    assert seen <= set(rank), seen
    # AGENTS.md is repository, .github/workflows/* is ci-used, pyproject.toml is ecosystem.
    assert any(a == "repository" for a in authorities.values())
    assert any(a == "ci-used" for a in authorities.values())
    assert any(a == "ecosystem" for a in authorities.values())


# ---------------------------------------------------------------------------
# 3. Ranking prefers the spec priority order
# ---------------------------------------------------------------------------


def _candidate(authority: str, score: int = 60) -> HelperCandidate:
    return HelperCandidate(
        id=f"c-{authority}",
        kind="build",
        source_path=f"src-{authority}",
        authority=authority,
        score=score,
    )


def test_rank_repository_beats_ci_used() -> None:
    candidates = [_candidate("ci-used", score=80), _candidate("repository", score=80)]
    ranked = rank_helpers(candidates, TaskRequirements(kind="build"))
    assert ranked[0].authority == "repository"


def test_rank_ci_used_beats_ecosystem() -> None:
    candidates = [_candidate("ecosystem", score=80), _candidate("ci-used", score=80)]
    ranked = rank_helpers(candidates, TaskRequirements(kind="build"))
    assert ranked[0].authority == "ci-used"


def test_rank_ecosystem_beats_generated() -> None:
    candidates = [_candidate("generated", score=80), _candidate("ecosystem", score=80)]
    ranked = rank_helpers(candidates, TaskRequirements(kind="build"))
    assert ranked[0].authority == "ecosystem"


def test_rank_filters_below_threshold() -> None:
    # Package URL with no docs/dry-run/rollback scores below threshold
    # (popularity alone does not authorize — spec §4.3).
    low = HelperCandidate(
        id="low",
        kind="build",
        source_path="pkg:npm/undocumented-tool",
        authority="ecosystem",
    )
    # Repo-owned Makefile with matching kind scores well above threshold.
    high = HelperCandidate(
        id="high",
        kind="build",
        source_path="Makefile",
        authority="repository",
    )
    ranked = rank_helpers([low, high], TaskRequirements(kind="build"))
    assert len(ranked) == 1
    assert ranked[0].id == "high"


def test_rank_records_score_evidence_for_every_criterion() -> None:
    candidate = _candidate("repository", score=0)
    ranked = rank_helpers([candidate], TaskRequirements(kind="build"))
    assert ranked, "candidate should pass threshold when requirements are met"
    criteria = {e.criterion for e in ranked[0].score_evidence}
    expected = {
        "capability_fit",
        "documentation",
        "maintenance",
        "license",
        "platform_support",
        "determinism",
        "security_posture",
        "observability",
        "reversibility",
        "adoption_cost",
    }
    assert expected <= criteria, criteria - expected


def test_rank_score_in_range_0_to_100() -> None:
    candidate = _candidate("repository", score=0)
    ranked = rank_helpers([candidate], TaskRequirements(kind="build"))
    assert 0 <= ranked[0].score <= 100


# ---------------------------------------------------------------------------
# 4. Popularity alone does not authorize (spec §4.3)
# ---------------------------------------------------------------------------


def test_popularity_alone_does_not_authorize() -> None:
    """A popular but undocumented, non-deterministic helper scores below threshold."""
    popular = HelperCandidate(
        id="popular-tool",
        kind="build",
        source_path="package:url",
        authority="ecosystem",
        # popularity is implicit via source_path package URL
        supports_dry_run=False,
        supports_rollback=False,
    )
    ranked = rank_helpers([popular], TaskRequirements(kind="build"))
    assert not ranked or ranked[0].score < DEFAULT_THRESHOLD


# ---------------------------------------------------------------------------
# 5. helper_build: adequate helper => zero file changes (GRC-AT-005)
# ---------------------------------------------------------------------------


def test_adequate_helper_yields_zero_file_changes(mixed_repo: Path) -> None:
    candidates = discover_helpers(mixed_repo)
    ranked = rank_helpers(candidates, TaskRequirements(kind="build"))
    assert ranked, "mixed_repo should yield at least one adequate helper"
    changes = helper_build_file_changes(ranked, repo_root=mixed_repo)
    assert changes == [], changes


def test_no_adequate_helper_signals_generation_needed(empty_repo: Path) -> None:
    candidates = discover_helpers(empty_repo)
    ranked = rank_helpers(candidates, TaskRequirements(kind="build"))
    changes = helper_build_file_changes(ranked, repo_root=empty_repo)
    # No adequate helper => generation is required => a non-empty plan is returned.
    assert changes, "expected generation plan when no adequate helper exists"
    assert any("generated" in str(c).lower() or "helper" in str(c).lower() for c in changes)


def test_rank_with_kind_filter_only_returns_matching_kind() -> None:
    candidates = [
        HelperCandidate(
            id="deploy-1",
            kind="deploy",
            source_path="Dockerfile",
            authority="repository",
        ),
        HelperCandidate(
            id="build-1",
            kind="build",
            source_path="Makefile",
            authority="repository",
        ),
    ]
    ranked = rank_helpers(candidates, TaskRequirements(kind="deploy"))
    assert all(c.kind == "deploy" for c in ranked), ranked
