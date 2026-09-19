"""Direct tests for the immutable model-runner launch contracts."""

from __future__ import annotations

from general_ludd.hardware import model_runner_launch
from general_ludd.hardware.model_runner_launch_contracts import (
    LaunchTarget,
    LaunchValue,
    RunnerLaunchBinding,
    RunnerLaunchError,
    RunnerLaunchPlan,
    RunnerLaunchProfile,
)


def test_launch_facade_reexports_canonical_contract_types() -> None:
    """Keep one canonical contract identity across old and direct imports."""
    assert model_runner_launch.LaunchTarget is LaunchTarget
    assert model_runner_launch.LaunchValue is LaunchValue
    assert model_runner_launch.RunnerLaunchBinding is RunnerLaunchBinding
    assert model_runner_launch.RunnerLaunchError is RunnerLaunchError
    assert model_runner_launch.RunnerLaunchPlan is RunnerLaunchPlan
    assert model_runner_launch.RunnerLaunchProfile is RunnerLaunchProfile
