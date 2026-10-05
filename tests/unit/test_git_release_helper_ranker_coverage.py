"""Policy-branch coverage for Git release helper ranking."""

from __future__ import annotations

from general_ludd.git_release import helper_ranker
from general_ludd.git_release.helper_catalog import HelperCandidate


def _candidate(
    *,
    kind: str = "build",
    source_path: str = "Makefile",
    authority: str = "repository",
    dry_run: bool = False,
    rollback: bool = False,
    observable: bool = False,
) -> HelperCandidate:
    """Build one explicit ranking candidate."""
    return HelperCandidate(
        id=f"{authority}-{kind}-{source_path}",
        kind=kind,
        source_path=source_path,
        authority=authority,
        supports_dry_run=dry_run,
        supports_rollback=rollback,
        observability=("progress",) if observable else (),
    )


def test_capability_fit_distinguishes_exact_other_and_adjacent_kinds() -> None:
    """Fitness grants full, zero, or partial credit according to helper kind."""
    requirements = helper_ranker.TaskRequirements(kind="build")

    assert helper_ranker._score_capability_fit(_candidate(), requirements) == 10
    assert helper_ranker._score_capability_fit(_candidate(kind="other"), requirements) == 0
    assert helper_ranker._score_capability_fit(_candidate(kind="test"), requirements) == 3


def test_documentation_and_platform_scoring_cover_explicit_contracts() -> None:
    """Canonical documentation and constrained platforms retain policy credit."""
    documented = _candidate(source_path="README.md")
    constrained = helper_ranker.TaskRequirements(platforms=("linux", "windows"))

    assert helper_ranker._score_documentation(documented) == 10
    assert helper_ranker._score_platform_support(documented, constrained) == 7


def test_lifecycle_capabilities_improve_security_and_reversibility_scores() -> None:
    """Dry-run, rollback, and observability signals affect their exact criteria."""
    both = _candidate(dry_run=True, rollback=True, observable=True)
    dry_only = _candidate(dry_run=True)
    rollback_only = _candidate(rollback=True)

    assert helper_ranker._score_determinism(both) == 10
    assert helper_ranker._score_security_posture(both) == 10
    assert helper_ranker._score_security_posture(dry_only) == 6
    assert helper_ranker._score_security_posture(rollback_only) == 6
    assert helper_ranker._score_observability(both) == 10
    assert helper_ranker._score_reversibility(both) == 10
    assert helper_ranker._score_reversibility(dry_only) == 5


def test_generation_plan_without_repo_root_uses_relative_script_path() -> None:
    """A missing adequate helper yields one narrow repository-relative plan."""
    changes = helper_ranker.helper_build_file_changes(
        [],
        task_requirements=helper_ranker.TaskRequirements(kind="deploy", min_score=75),
    )

    assert changes == [
        "generated helper needed: no candidate of kind='deploy' cleared threshold 75; "
        "generate the narrowest missing adapter "
        "(target=scripts/generated_deploy_helper.sh, kind=deploy)"
    ]
