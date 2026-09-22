"""Contracts for exact-source FreeLLMAPI upstream build evidence."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

import pytest

from general_ludd.models.freellmapi_upstream_build import (
    FREELLMAPI_UPSTREAM_BUILD_GATE,
    FreeLLMAPIUpstreamBuildError,
    FreeLLMAPIUpstreamBuildFault,
    build_upstream_evidence,
    validate_upstream_build_plan,
)

_ROOT = Path(__file__).resolve().parents[2]


def _candidate() -> dict[str, object]:
    value: object = json.loads(
        (_ROOT / "config/freellmapi/upstream_candidate.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _plan() -> dict[str, object]:
    value: object = json.loads(
        (_ROOT / "config/freellmapi/upstream_build_plan.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _mapping(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping)
    return value


def _results(*, failed: str | None = None) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    stopped = False
    for step_id in (
        "install",
        "migrations",
        "root_tests",
        "lint",
        "workspace_build",
        "server_coverage",
    ):
        status = "not_run" if stopped else "failed" if step_id == failed else "passed"
        exit_code = None if stopped else 1 if step_id == failed else 0
        results.append(
            {"step_id": step_id, "status": status, "exit_code": exit_code}
        )
        stopped = stopped or step_id == failed
    return results


def test_tracked_plan_binds_exact_candidate_archive_and_upstream_commands() -> None:
    validated = validate_upstream_build_plan(_candidate(), _plan())
    candidate_archive = _mapping(_candidate()["archive"])

    assert validated["candidate_id"] == _candidate()["candidate_id"]
    assert validated["archive_sha256"] == candidate_archive["sha256"]
    assert validated["toolchains"] == (
        ("20.20.2", "10.8.2"),
        ("22.23.2", "10.9.8"),
    )
    assert validated["install"] == ("npm", "ci", "--no-audit", "--no-fund")
    assert validated["steps"] == (
        ("migrations", ("npm", "run", "test:migrations")),
        ("root_tests", ("npm", "test")),
        ("lint", ("npm", "run", "lint")),
        ("workspace_build", ("npm", "run", "build")),
        ("server_coverage", ("npm", "run", "test:coverage", "-w", "server")),
    )
    assert validated["plan_id"].startswith("sha256:")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda plan: plan.__setitem__("schema_version", 2),
        lambda plan: plan.__setitem__("gate", "another-gate"),
        lambda plan: plan.__setitem__("candidate_id", "sha256:" + "0" * 64),
        lambda plan: plan.__setitem__("archive_sha256", "0" * 64),
        lambda plan: plan.__setitem__("toolchains", [{"node": "22.23.2"}]),
        lambda plan: plan.__setitem__("install", ["npm", "install"]),
        lambda plan: plan["steps"][0].__setitem__("argv", ["sh", "-c", "echo bad"]),
        lambda plan: plan["steps"].reverse(),
        lambda plan: plan.__setitem__("step_timeout_seconds", 0),
        lambda plan: plan.__setitem__("owner", ""),
        lambda plan: plan.__setitem__("source_retention", "vendored"),
    ],
)
def test_plan_tampering_fails_with_one_content_free_fault(
    mutation: Callable[[dict[str, object]], None],
) -> None:
    plan = copy.deepcopy(_plan())
    mutation(plan)

    with pytest.raises(FreeLLMAPIUpstreamBuildError) as caught:
        validate_upstream_build_plan(_candidate(), plan)

    assert caught.value.fault is FreeLLMAPIUpstreamBuildFault.PLAN_INVALID
    assert str(caught.value) == "plan_invalid"


def test_candidate_tampering_is_rejected_before_plan_use() -> None:
    candidate = _candidate()
    archive = candidate["archive"]
    assert isinstance(archive, dict)
    archive["sha256"] = "0" * 64

    with pytest.raises(FreeLLMAPIUpstreamBuildError) as caught:
        validate_upstream_build_plan(candidate, _plan())

    assert caught.value.fault is FreeLLMAPIUpstreamBuildFault.CANDIDATE_INVALID


def test_success_evidence_is_deterministic_non_runnable_and_content_free() -> None:
    first = build_upstream_evidence(
        candidate_lock=_candidate(),
        plan=_plan(),
        node_version="v20.20.2",
        npm_version="10.8.2",
        results=_results(),
    )
    second = build_upstream_evidence(
        candidate_lock=_candidate(),
        plan=_plan(),
        node_version="v20.20.2",
        npm_version="10.8.2",
        results=_results(),
    )

    assert first == second
    assert first["gate"] == FREELLMAPI_UPSTREAM_BUILD_GATE
    assert first["decision"] == "upstream_build_verified"
    assert first["runtime_admitted"] is False
    assert first["node_major"] == 20
    assert first["step_count"] == 6
    evidence_id = first["evidence_id"]
    assert isinstance(evidence_id, str)
    assert evidence_id.startswith("sha256:")
    assert "prompt" not in json.dumps(first, sort_keys=True)


def test_failure_evidence_is_a_rejection_and_never_an_admission() -> None:
    evidence = build_upstream_evidence(
        candidate_lock=_candidate(),
        plan=_plan(),
        node_version="v22.23.2",
        npm_version="10.9.8",
        results=_results(failed="lint"),
    )

    assert evidence["decision"] == "rejected_upstream_build"
    assert evidence["failed_step"] == "lint"
    assert evidence["runtime_admitted"] is False


@pytest.mark.parametrize(
    ("node_version", "npm_version", "fault"),
    [
        ("v26.0.0", "11.0.0", FreeLLMAPIUpstreamBuildFault.TOOLCHAIN_INVALID),
        ("twenty", "10.8.2", FreeLLMAPIUpstreamBuildFault.TOOLCHAIN_INVALID),
        ("v20.20.2", "nine", FreeLLMAPIUpstreamBuildFault.TOOLCHAIN_INVALID),
    ],
)
def test_unplanned_or_malformed_toolchains_fail_closed(
    node_version: str,
    npm_version: str,
    fault: FreeLLMAPIUpstreamBuildFault,
) -> None:
    with pytest.raises(FreeLLMAPIUpstreamBuildError) as caught:
        build_upstream_evidence(
            candidate_lock=_candidate(),
            plan=_plan(),
            node_version=node_version,
            npm_version=npm_version,
            results=_results(),
        )

    assert caught.value.fault is fault


def test_node_and_npm_versions_must_match_one_exact_planned_pair() -> None:
    with pytest.raises(FreeLLMAPIUpstreamBuildError) as caught:
        build_upstream_evidence(
            candidate_lock=_candidate(),
            plan=_plan(),
            node_version="v20.20.2",
            npm_version="10.9.8",
            results=_results(),
        )

    assert caught.value.fault is FreeLLMAPIUpstreamBuildFault.TOOLCHAIN_INVALID


def test_result_set_must_be_complete_ordered_and_exit_consistent() -> None:
    invalid_sets = [
        _results()[:-1],
        list(reversed(_results())),
        [{**row, "exit_code": 1} for row in _results()],
        [{**row, "status": "failed"} for row in _results()],
    ]

    for rows in invalid_sets:
        with pytest.raises(FreeLLMAPIUpstreamBuildError) as caught:
            build_upstream_evidence(
                candidate_lock=_candidate(),
                plan=_plan(),
                node_version="v20.20.2",
                npm_version="10.8.2",
                results=rows,
            )
        assert caught.value.fault is FreeLLMAPIUpstreamBuildFault.RESULTS_INVALID


def test_plan_module_stays_universal_and_does_not_own_runtime_promotion() -> None:
    import general_ludd.models.freellmapi_upstream_build as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "general_ludd.self_improve" not in source
    assert "runtime_admitted" in source
    assert "runtime_admitted\": True" not in source
    assert module.__all__ == [
        "FREELLMAPI_UPSTREAM_BUILD_GATE",
        "FreeLLMAPIUpstreamBuildError",
        "FreeLLMAPIUpstreamBuildFault",
        "build_upstream_evidence",
        "validate_upstream_build_plan",
        "validate_upstream_toolchain",
    ]


def test_tracked_plan_digest_is_bound_to_real_candidate_archive() -> None:
    candidate = _candidate()
    plan = _plan()
    canonical = json.dumps(plan, separators=(",", ":"), sort_keys=True).encode()
    candidate_archive = _mapping(candidate["archive"])

    assert plan["candidate_id"] == candidate["candidate_id"]
    assert plan["archive_sha256"] == candidate_archive["sha256"]
    assert hashlib.sha256(canonical).hexdigest()
