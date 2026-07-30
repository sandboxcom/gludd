"""E2E tests for the general_ludd.git_release expert collection.

Verifies the public API surface end-to-end: package imports, RepoEvidence
collection via assess_repo, and the contract shapes for release planning.
"""

from __future__ import annotations

import os

import pytest


class TestGitReleasePackageImports:
    """Verify the git_release package imports cleanly and exposes its API."""

    def test_package_imports_cleanly(self) -> None:
        import general_ludd.git_release as git_release

        assert git_release is not None

    def test_package_exports_assess_repo(self) -> None:
        from general_ludd.git_release import assess_repo

        assert callable(assess_repo)

    def test_package_exports_repo_evidence(self) -> None:
        from general_ludd.git_release import RepoEvidence

        assert RepoEvidence is not None


class TestAssessRepo:
    """Verify assess_repo() returns a structured RepoEvidence on a real repo."""

    def test_assess_repo_on_project_root(self) -> None:
        from general_ludd.git_release import assess_repo
        from general_ludd.git_release.contracts import RepoEvidence

        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        evidence = assess_repo(project_root)
        assert isinstance(evidence, RepoEvidence)
        assert evidence.repo_root == os.path.abspath(project_root)
        assert len(evidence.head_sha) == 40
        assert all(c in "0123456789abcdef" for c in evidence.head_sha)

    def test_assess_repo_evidence_has_timestamp(self) -> None:
        from general_ludd.git_release import assess_repo

        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        evidence = assess_repo(project_root)
        assert evidence.evidence_time
        assert "T" in evidence.evidence_time

    def test_assess_repo_collects_dirty_paths(self) -> None:
        from general_ludd.git_release import assess_repo

        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        evidence = assess_repo(project_root)
        assert isinstance(evidence.dirty_paths, list)

    def test_assess_repo_collects_worktrees(self) -> None:
        from general_ludd.git_release import assess_repo

        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        evidence = assess_repo(project_root)
        assert isinstance(evidence.worktrees, list)
        assert isinstance(evidence.operations, list)
        assert isinstance(evidence.policies, list)


class TestRepoEvidenceContract:
    """Verify RepoEvidence carries the required structured fields."""

    def test_repo_evidence_rejects_short_sha(self) -> None:
        from general_ludd.git_release.contracts import RepoEvidence
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RepoEvidence(
                repo_root="/tmp/repo",
                head_sha="abc123",
                evidence_time="2024-01-01T00:00:00Z",
            )

    def test_repo_evidence_rejects_missing_timestamp(self) -> None:
        from general_ludd.git_release import RepoEvidence
        from pydantic import ValidationError

        with pytest.raises((ValidationError, TypeError)):
            RepoEvidence(
                repo_root="/tmp/repo",
                head_sha="0" * 40,
            )


class TestReleaseContractsImport:
    """Verify the release plan and verdict contracts are importable."""

    def test_release_plan_importable(self) -> None:
        from general_ludd.git_release import ReleasePlan

        assert ReleasePlan is not None

    def test_release_verdict_importable(self) -> None:
        from general_ludd.git_release import ReleaseVerdict, ReleaseVerdictState

        assert ReleaseVerdict is not None
        assert ReleaseVerdictState.RELEASED.value == "released"
