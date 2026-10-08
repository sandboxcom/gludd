"""Behavior tests for the bounded git-release operation adapter."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from general_ludd.git_release.operations import (
    GIT_RELEASE_OPERATIONS,
    GitReleaseRequestError,
    dispatch_git_release_operation,
)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide deterministic repository evidence without invoking git."""
    monkeypatch.setattr(
        "general_ludd.git_release.operations.collect_repo_evidence",
        lambda path: SimpleNamespace(
            path=str(Path(path).resolve()),
            head_sha="a" * 40,
            branch="development",
            is_dirty=False,
            is_detached=False,
        ),
    )
    (tmp_path / "Makefile").write_text("test:\n\t@true\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "operation",
    [
        "work_recover",
        "conflict_resolve",
        "release_plan",
        "pipeline_triage",
        "release_recover",
    ],
)
def test_planning_operations_return_observed_non_mutating_proposals(
    operation: str,
    repo: Path,
) -> None:
    result = dispatch_git_release_operation(operation, {"path": str(repo)})

    assert result["operation"] == operation
    assert result["state"] == "proposal"
    assert result["mutation_performed"] is False
    assert result["evidence"]["head_sha"] == "a" * 40
    assert result["proposed_actions"]


def test_helper_operations_run_existing_discovery_and_ranking(repo: Path) -> None:
    discovered = dispatch_git_release_operation("helper_discover", {"path": str(repo)})
    selected = dispatch_git_release_operation(
        "helper_select",
        {"path": str(repo), "kind": "build", "min_score": 1},
    )
    build = dispatch_git_release_operation(
        "helper_build",
        {"path": str(repo), "kind": "build", "min_score": 1},
    )

    assert any(candidate["source_path"] == "Makefile" for candidate in discovered["candidates"])
    assert selected["selected"]["source_path"] == "Makefile"
    assert build["file_changes"] == []
    assert build["mutation_performed"] is False


def test_artifact_build_uses_existing_provenance_engine(repo: Path) -> None:
    artifact = repo / "bundle.whl"
    lock = repo / "lock.json"
    artifact.write_bytes(b"immutable-build")
    lock.write_text(json.dumps({"packages": {"gludd": "1.0"}}), encoding="utf-8")

    result = dispatch_git_release_operation(
        "artifact_build",
        {
            "path": str(repo),
            "artifact_path": "bundle.whl",
            "dependency_lock_path": "lock.json",
            "builder_identity": "fixture-builder",
            "release_id": "release-1",
            "source_sha": "b" * 40,
        },
    )

    assert result["state"] == "observed"
    assert result["mutation_performed"] is False
    assert result["provenance"]["subject"] == "bundle.whl"
    assert len(result["provenance"]["artifact_digest"]) == 64


def test_artifact_verify_compares_independent_bytes(repo: Path) -> None:
    artifact = repo / "bundle.whl"
    lock = repo / "lock.json"
    artifact.write_bytes(b"immutable-build")
    lock.write_text(json.dumps({"packages": {"gludd": "1.0"}}), encoding="utf-8")
    built = dispatch_git_release_operation(
        "artifact_build",
        {
            "path": str(repo),
            "artifact_path": "bundle.whl",
            "dependency_lock_path": "lock.json",
            "builder_identity": "fixture-builder",
            "release_id": "release-1",
            "source_sha": "b" * 40,
        },
    )

    result = dispatch_git_release_operation(
        "artifact_verify",
        {
            "path": str(repo),
            "artifact_path": "bundle.whl",
            "dependency_lock_path": "lock.json",
            "provenance": built["provenance"],
        },
    )

    assert result == {
        "operation": "artifact_verify",
        "state": "verified",
        "mutation_performed": False,
        "ok": True,
        "reasons": [],
    }


@pytest.mark.parametrize(
    ("sample", "decision"),
    [
        (None, "HoldDecision"),
        (
            {"availability": 0.999, "error_rate": 0.001, "latency_p99_ms": 120.0},
            "PromoteDecision",
        ),
        (
            {"availability": 0.999, "error_rate": 0.3, "latency_p99_ms": 120.0},
            "RollbackDecision",
        ),
    ],
)
def test_deploy_orchestrate_uses_zdd_health_gate(
    sample: dict[str, float] | None,
    decision: str,
    repo: Path,
) -> None:
    request: dict[str, Any] = {
        "path": str(repo),
        "strategy": "canary",
        "prior_digest": "sha256:prior",
        "new_digest": "sha256:new",
        "health_gate": {
            "max_error_rate": 0.01,
            "min_availability": 0.99,
            "max_latency_p99_ms": 500.0,
        },
        "abort_threshold": 0.1,
        "current_percent": 0,
        "sample": sample,
    }

    result = dispatch_git_release_operation("deploy_orchestrate", request)

    assert result["decision"]["type"] == decision
    assert result["mutation_performed"] is False
    assert result["next_shift"]["next_percent"] == 25


def test_invalid_requests_fail_closed_and_are_bounded(repo: Path) -> None:
    with pytest.raises(GitReleaseRequestError, match="unsupported"):
        dispatch_git_release_operation("unknown", {"path": str(repo)})
    with pytest.raises(GitReleaseRequestError, match="bounded payload"):
        dispatch_git_release_operation(
            "release_plan",
            {f"field-{index}": index for index in range(129)},
        )
    with pytest.raises(GitReleaseRequestError, match="inside the repository"):
        dispatch_git_release_operation(
            "artifact_build",
            {
                "path": str(repo),
                "artifact_path": "../escape",
                "dependency_lock_path": "lock.json",
                "builder_identity": "builder",
                "release_id": "release",
                "source_sha": "b" * 40,
            },
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"path": "/repo", "platforms": "linux"},
        {"path": "/repo", "platforms": [""]},
        {"path": "/repo", "platforms": ["linux"] * 33},
        {"path": "/repo", "min_score": True},
        {"path": "/repo", "min_score": 101},
        {"path": "/repo", "kind": "build\nunsafe"},
    ],
)
def test_helper_selection_rejects_malformed_constraints(
    payload: dict[str, object],
    repo: Path,
) -> None:
    payload["path"] = str(repo)
    with pytest.raises(GitReleaseRequestError):
        dispatch_git_release_operation("helper_select", payload)


def test_deployment_rejects_unknown_strategy_and_malformed_sample(repo: Path) -> None:
    base: dict[str, Any] = {
        "path": str(repo),
        "strategy": "unknown",
        "prior_digest": "old",
        "new_digest": "new",
        "health_gate": {
            "max_error_rate": 0.01,
            "min_availability": 0.99,
            "max_latency_p99_ms": 500.0,
        },
        "abort_threshold": 0.1,
    }
    with pytest.raises(GitReleaseRequestError, match="strategy"):
        dispatch_git_release_operation("deploy_orchestrate", base)

    base["strategy"] = "canary"
    base["sample"] = "fresh"
    with pytest.raises(GitReleaseRequestError, match="sample"):
        dispatch_git_release_operation("deploy_orchestrate", base)

    base["sample"] = None
    health_gate = base["health_gate"]
    assert isinstance(health_gate, dict)
    health_gate["max_error_rate"] = True
    with pytest.raises(GitReleaseRequestError, match="finite number"):
        dispatch_git_release_operation("deploy_orchestrate", base)


def test_artifact_verification_rejects_malformed_provenance(repo: Path) -> None:
    (repo / "bundle.whl").write_bytes(b"artifact")
    (repo / "lock.json").write_text('{"packages": {}}', encoding="utf-8")

    with pytest.raises(GitReleaseRequestError, match="attestation"):
        dispatch_git_release_operation(
            "artifact_verify",
            {
                "path": str(repo),
                "artifact_path": "bundle.whl",
                "dependency_lock_path": "lock.json",
                "provenance": {},
            },
        )


def test_repository_evidence_failure_is_a_request_error(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_path: str) -> None:
        raise RuntimeError("not a git repository")

    monkeypatch.setattr(
        "general_ludd.git_release.operations.collect_repo_evidence",
        fail,
    )
    with pytest.raises(GitReleaseRequestError, match="not a git repository"):
        dispatch_git_release_operation("release_plan", {"path": str(repo)})


def test_operation_inventory_matches_all_service_roles() -> None:
    assert {
        "artifact_build",
        "artifact_verify",
        "conflict_resolve",
        "deploy_orchestrate",
        "helper_build",
        "helper_discover",
        "helper_select",
        "pipeline_triage",
        "release_plan",
        "release_recover",
        "work_recover",
    } == GIT_RELEASE_OPERATIONS
