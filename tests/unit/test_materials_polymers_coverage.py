"""Fail-closed branch coverage for polymer process advice."""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.materials import polymers
from general_ludd.materials.core import INSUFFICIENT_DATA


@pytest.fixture
def advisor() -> polymers.PolymerProcessAdvisor:
    """Return a stateless polymer advisor."""
    return polymers.PolymerProcessAdvisor()


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("classify", ("missing",)),
        ("check_regrind", ("missing",)),
        ("estimate_shrinkage", ("missing",)),
        ("estimate_warpage", ("missing",)),
        ("fiber_orientation_effect", ("missing",)),
        ("drying_requirement", ("missing",)),
        ("cure_schedule", ("missing",)),
        ("check_process_compatibility", ("missing", "injection_molding")),
    ],
)
def test_unknown_materials_fail_closed(
    advisor: polymers.PolymerProcessAdvisor,
    method: str,
    args: tuple[str, ...],
) -> None:
    """Return explicit insufficient-data results for every material lookup."""
    result = getattr(advisor, method)(*args)
    assert result["state"] == INSUFFICIENT_DATA


def test_unfilled_nonhygroscopic_default_material_paths(
    advisor: polymers.PolymerProcessAdvisor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover unreinforced, default shrinkage, and non-hygroscopic decisions."""
    material: dict[str, Any] = {
        "material_id": "custom_polymer",
        "family": "polymer",
        "class": "commodity",
        "polymer_class": "thermoplastic",
    }
    monkeypatch.setattr(polymers, "lookup_material", lambda _material_id: material)

    shrinkage = advisor.estimate_shrinkage("custom_polymer")
    warpage = advisor.estimate_warpage("custom_polymer")
    orientation = advisor.fiber_orientation_effect("custom_polymer")
    drying = advisor.drying_requirement("custom_polymer")

    assert shrinkage["range_pct"] == list(polymers._DEFAULT_SHRINKAGE_PCT)
    assert warpage["warpage_risk"] == "medium"
    assert orientation["anisotropic"] is False
    assert drying["drying_required"] is False


def test_thermoset_without_schedule_and_invalid_process_fail_closed(
    advisor: polymers.PolymerProcessAdvisor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject unknown processes and missing thermoset cure schedules."""
    material: dict[str, Any] = {
        "material_id": "custom_thermoset",
        "family": "polymer",
        "class": "resin",
        "polymer_class": "thermoset",
    }
    monkeypatch.setattr(polymers, "lookup_material", lambda _material_id: material)

    schedule = advisor.cure_schedule("custom_thermoset")
    invalid = advisor.check_process_compatibility("custom_thermoset", "laser_sintering")

    assert schedule["state"] == INSUFFICIENT_DATA
    assert "no cure schedule" in schedule["reason"]
    assert invalid["state"] == INSUFFICIENT_DATA
    assert "unrecognized polymer process" in invalid["reason"]
