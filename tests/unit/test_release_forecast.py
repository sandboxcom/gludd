from __future__ import annotations

import json
from pathlib import Path

import pytest

from general_ludd.review import release_forecast as rf
from general_ludd.review.release_forecast import (
    Blocker,
    RunObservation,
    StagePlan,
    build_forecast,
    load_observations,
)

STAGES = (
    StagePlan("readiness_fix", "shared", 30.0),
    StagePlan("candidate_commit", "shared", 15.0, ("readiness_fix",)),
    StagePlan("local_dual_track", "local", 75.0, ("candidate_commit",)),
    StagePlan("hosted_ci", "gha", 35.0, ("candidate_commit",)),
    StagePlan(
        "promotion_and_publish",
        "shared",
        20.0,
        ("local_dual_track", "hosted_ci"),
    ),
)


def observation(
    phase: str,
    lane: str,
    duration: float,
    *,
    succeeded: bool = True,
    failure_class: str = "",
    node: str = "",
    order: int = 0,
    total: int = 0,
    platform: str = "",
    python: str = "",
) -> RunObservation:
    return RunObservation(
        run_id=f"{phase}-{lane}-{duration}-{node}",
        phase=phase,
        lane=lane,
        duration_minutes=duration,
        succeeded=succeeded,
        failure_class=failure_class,
        failing_node=node,
        node_order=order,
        total_nodes=total,
        platform=platform,
        python_version=python,
    )


def test_forecast_uses_prior_local_and_gha_phase_quantiles() -> None:
    history = (
        observation("local_dual_track", "local", 60.0),
        observation("local_dual_track", "local", 90.0),
        observation("local_dual_track", "local", 75.0),
        observation("hosted_ci", "gha", 30.0),
        observation("hosted_ci", "gha", 55.0),
        observation("hosted_ci", "gha", 35.0),
    )

    forecast = build_forecast(stages=STAGES, observations=history)

    assert forecast.p50_minutes == pytest.approx(140.0)
    assert forecast.p90_minutes == pytest.approx(174.5)
    local = next(item for item in forecast.phases if item.name == "local_dual_track")
    hosted = next(item for item in forecast.phases if item.name == "hosted_ci")
    assert (local.p50_minutes, local.p90_minutes, local.sample_count) == (75.0, 90.0, 3)
    assert (hosted.p50_minutes, hosted.p90_minutes, hosted.sample_count) == (35.0, 55.0, 3)
    assert forecast.calibration_sample_count == 6
    assert forecast.method == "empirical-critical-path-v1"


def test_current_blockers_and_artifact_dependencies_drive_priority() -> None:
    history = (
        observation(
            "hosted_ci",
            "gha",
            40.0,
            succeeded=False,
            failure_class="packaging",
            node="tests/package/test_linux.py::test_binary",
            order=800,
            total=1000,
        ),
        observation("hosted_ci", "gha", 35.0),
    )
    blockers = (
        Blocker(
            code="linux-package",
            phase="hosted_ci",
            repair_minutes=5.0,
            failure_class="packaging",
            artifacts=("linux-binary",),
        ),
        Blocker(
            code="coverage-hole",
            phase="local_dual_track",
            repair_minutes=20.0,
            failure_class="coverage",
            coverage_gap_files=2,
        ),
    )
    dependencies = {
        "linux-binary": (),
        "checksums": ("linux-binary",),
        "release-manifest": ("checksums",),
    }

    forecast = build_forecast(
        stages=STAGES,
        observations=history,
        blockers=blockers,
        artifact_dependencies=dependencies,
    )

    assert [item.code for item in forecast.priorities] == [
        "linux-package",
        "coverage-hole",
    ]
    top = forecast.priorities[0]
    assert top.artifacts_unlocked == (
        "checksums",
        "linux-binary",
        "release-manifest",
    )
    assert top.expected_risk_reduction_minutes > 0
    assert top.risk_reduction_per_minute > forecast.priorities[1].risk_reduction_per_minute
    assert any("historical packaging failure" in reason for reason in top.reasons)


def test_coverage_and_platform_replay_gaps_raise_tail_not_p50() -> None:
    history = (
        observation(
            "hosted_ci",
            "gha",
            42.0,
            succeeded=False,
            failure_class="platform",
            node="tests/unit/test_windows.py::test_paths",
            order=700,
            total=1000,
            platform="windows",
            python="3.11",
        ),
        observation(
            "local_dual_track",
            "local",
            70.0,
            platform="macos",
            python="3.14",
        ),
    )

    plain = build_forecast(stages=STAGES, observations=history)
    risky = build_forecast(
        stages=STAGES,
        observations=history,
        coverage_gap_modules=("general_ludd.cloud.model_pipeline",),
    )

    assert risky.p50_minutes == plain.p50_minutes
    assert risky.p90_minutes > plain.p90_minutes
    assert risky.replay_gaps == ("windows/python-3.11",)
    assert risky.coverage_gaps == ("general_ludd.cloud.model_pipeline",)


def test_historical_late_unit_1b_failure_is_front_loaded_in_bounded_canary() -> None:
    history = (
        observation(
            "hosted_ci",
            "gha",
            44.0,
            succeeded=False,
            failure_class="unit-regression",
            node="tests/unit/test_cloud.py::test_late_unit_1b",
            order=930,
            total=1000,
            platform="linux",
            python="3.11",
        ),
        observation(
            "hosted_ci",
            "gha",
            7.0,
            succeeded=False,
            failure_class="unit-regression",
            node="tests/unit/test_cli.py::test_early_unit_1a",
            order=20,
            total=1000,
            platform="linux",
            python="3.11",
        ),
        observation("hosted_ci", "gha", 36.0, platform="linux", python="3.11"),
    )

    forecast = build_forecast(
        stages=STAGES,
        observations=history,
        canary_limit=1,
    )

    assert len(forecast.hosted_canary) == 1
    canary = forecast.hosted_canary[0]
    assert canary.node == "tests/unit/test_cloud.py::test_late_unit_1b"
    assert canary.original_node_order == 930
    assert canary.expected_minutes_saved > 10.0
    assert canary.canary_order == 1


def test_canary_deduplicates_nodes_and_is_deterministic() -> None:
    duplicate = observation(
        "hosted_ci",
        "gha",
        30.0,
        succeeded=False,
        failure_class="flake",
        node="tests/unit/test_a.py::test_a",
        order=500,
        total=600,
        platform="linux",
        python="3.12",
    )
    history = (
        duplicate,
        observation(
            "hosted_ci",
            "gha",
            32.0,
            succeeded=False,
            failure_class="flake",
            node=duplicate.failing_node,
            order=550,
            total=600,
            platform="linux",
            python="3.12",
        ),
    )

    forecast = build_forecast(stages=STAGES, observations=history, canary_limit=5)

    assert len(forecast.hosted_canary) == 1
    assert forecast.hosted_canary[0].historical_failures == 2


def test_forecast_documentation_pins_evidence_zdd_and_resource_bounds() -> None:
    text = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "features"
        / "RELEASE_FORECASTING.md"
    ).read_text(encoding="utf-8")

    for required in (
        "GitHub Community",
        "github.com/orgs/community/discussions/59051",
        "github.com/orgs/community/discussions/161557",
        "github.com/orgs/community/discussions/194567",
        "github.com/orgs/community/discussions/73156",
        "pytest",
        "ZDD",
        "Rollback",
        "bounded",
        "85%",
        "75%",
        "canary",
    ):
        assert required in text


@pytest.mark.parametrize(
    "kwargs",
    [
        {"run_id": "", "phase": "hosted_ci", "lane": "gha", "duration_minutes": 1, "succeeded": True},
        {"run_id": "x", "phase": "hosted_ci", "lane": "remote", "duration_minutes": 1, "succeeded": True},
        {"run_id": "x", "phase": "hosted_ci", "lane": "gha", "duration_minutes": 0, "succeeded": True},
        {
            "run_id": "x",
            "phase": "hosted_ci",
            "lane": "gha",
            "duration_minutes": 1,
            "succeeded": False,
            "failing_node": "test_x",
            "node_order": 5,
            "total_nodes": 4,
        },
    ],
)
def test_observation_validation_is_fail_closed(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RunObservation(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: StagePlan("", "shared", 1.0),
        lambda: StagePlan("x", "remote", 1.0),
        lambda: StagePlan("x", "shared", 0.0),
        lambda: StagePlan("x", "shared", 1.0, ("x",)),
        lambda: Blocker("", "hosted_ci", 1.0, "failure"),
        lambda: Blocker("x", "hosted_ci", 0.0, "failure"),
        lambda: Blocker("x", "hosted_ci", 1.0, "failure", coverage_gap_files=-1),
    ],
)
def test_stage_and_blocker_models_reject_ambiguous_evidence(factory: object) -> None:
    with pytest.raises(ValueError):
        factory()  # type: ignore[operator]


@pytest.mark.parametrize(
    "stages",
    [
        (),
        (StagePlan("a", "shared", 1.0), StagePlan("a", "shared", 2.0)),
        (StagePlan("a", "shared", 1.0, ("missing",)),),
        (
            StagePlan("a", "shared", 1.0, ("b",)),
            StagePlan("b", "shared", 1.0, ("a",)),
        ),
    ],
)
def test_stage_graph_validation_rejects_empty_duplicate_missing_and_cycle(
    stages: tuple[StagePlan, ...],
) -> None:
    with pytest.raises(ValueError):
        build_forecast(stages=stages)


def test_forecast_rejects_unknown_completion_observation_lane_blocker_and_limit() -> None:
    with pytest.raises(ValueError, match="unknown completed"):
        build_forecast(stages=STAGES, completed_stages={"missing"})
    with pytest.raises(ValueError, match="unknown phase"):
        build_forecast(
            stages=STAGES,
            observations=(observation("missing", "gha", 1.0),),
        )
    with pytest.raises(ValueError, match="does not match"):
        build_forecast(
            stages=STAGES,
            observations=(observation("hosted_ci", "local", 1.0),),
        )
    with pytest.raises(ValueError, match="unknown phase"):
        build_forecast(
            stages=STAGES,
            blockers=(Blocker("bad", "missing", 1.0, "failure"),),
        )
    with pytest.raises(ValueError, match="canary_limit"):
        build_forecast(stages=STAGES, canary_limit=21)
    with pytest.raises(ValueError, match="no values"):
        rf._nearest_rank((), 0.9)


def test_history_loader_rejects_invalid_json_shape_and_entries(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    cases = (
        "not-json",
        '{"schema_version": 1, "observations": {}}',
        '{"schema_version": 1, "observations": [1]}',
        (
            '{"schema_version": 1, "observations": '
            '[{"run_id": "x", "phase": "hosted_ci", "lane": "bad", '
            '"duration_minutes": 1, "succeeded": true}]}'
        ),
    )
    for raw in cases:
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(ValueError):
            load_observations(path)
    with pytest.raises(ValueError, match="limit"):
        load_observations(path, limit=0)
    with pytest.raises(ValueError, match="cannot load"):
        load_observations(tmp_path / "missing.json")


def test_history_loader_is_schema_checked_and_bounded(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "observations": [
                    {
                        "run_id": f"run-{index}",
                        "phase": "hosted_ci",
                        "lane": "gha",
                        "duration_minutes": 10 + index,
                        "succeeded": True,
                    }
                    for index in range(4)
                ],
            }
        ),
        encoding="utf-8",
    )

    loaded = load_observations(path, limit=2)

    assert [item.run_id for item in loaded] == ["run-2", "run-3"]
    path.write_text('{"schema_version": 2, "observations": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        load_observations(path)
