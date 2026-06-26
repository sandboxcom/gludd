"""Regression tests: ProjectManager must reject non-finite (NaN/inf) weights.

`NaN < 0` and `NaN > 100` are both False in Python, so a NaN weight would slip
past the `0..100` range check, get stored, and then poison select_project's
weighted-random math (total becomes NaN, every `r <= cumulative` is False) —
silently handing ALL work to the last project and starving every other one.
set_weight and rebalance now reject non-finite weights up front.
"""

from __future__ import annotations

import math

import pytest

from general_ludd.projects.manager import ProjectAllocationError, ProjectManager


def _mgr_with_two() -> tuple[ProjectManager, str, str]:
    mgr = ProjectManager()
    a = mgr.add_project(name="alpha", weight=50.0)
    b = mgr.add_project(name="beta", weight=50.0)
    return mgr, a.project_id, b.project_id


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_set_weight_rejects_non_finite(bad: float) -> None:
    mgr, pid_a, _pid_b = _mgr_with_two()
    with pytest.raises(ProjectAllocationError):
        mgr.set_weight(pid_a, bad)
    # The stored weight is unchanged (still finite).
    assert math.isfinite(mgr.get_project(pid_a).weight)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_rebalance_rejects_non_finite(bad: float) -> None:
    mgr, pid_a, pid_b = _mgr_with_two()
    with pytest.raises(ProjectAllocationError):
        mgr.rebalance({pid_a: bad, pid_b: 50.0})
    # No weight was mutated to a non-finite value.
    assert math.isfinite(mgr.get_project(pid_a).weight)
    assert math.isfinite(mgr.get_project(pid_b).weight)


def test_set_weight_still_accepts_valid_finite_weight() -> None:
    mgr, pid_a, _pid_b = _mgr_with_two()
    # beta holds 50, so alpha can go to at most 50 (others_total + new <= 100).
    mgr.set_weight(pid_a, 40.0)
    assert mgr.get_project(pid_a).weight == pytest.approx(40.0)


def test_rebalance_still_accepts_valid_finite_weights() -> None:
    mgr, pid_a, pid_b = _mgr_with_two()
    mgr.rebalance({pid_a: 30.0, pid_b: 70.0})
    assert mgr.get_project(pid_a).weight == pytest.approx(30.0)
    assert mgr.get_project(pid_b).weight == pytest.approx(70.0)
