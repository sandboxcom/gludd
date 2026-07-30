"""Tests for polymer process experts (spec MATE-001 section 4.2).

Covers thermoplastic vs thermoset distinction, shrinkage/warpage prediction,
fiber orientation effects, drying requirements, cure kinetics basics, and
incompatible-process rejection per MATE-AT-003 (negative fixtures prove
thermosets are not planned as remeltable).
"""

from __future__ import annotations

import pytest

from general_ludd.materials.polymers import PolymerProcessAdvisor


@pytest.fixture
def advisor() -> PolymerProcessAdvisor:
    return PolymerProcessAdvisor()


# ---------------------------------------------------------------------------
# Thermoplastic vs thermoset distinction
# ---------------------------------------------------------------------------


class TestThermoplasticThermosetDistinction:
    def test_thermoplastic_is_remeltable(self, advisor: PolymerProcessAdvisor) -> None:
        info = advisor.classify("pa66_gf30")
        assert info["polymer_class"] == "thermoplastic"
        assert info["remeltable"] is True

    def test_thermoset_is_not_remeltable(self, advisor: PolymerProcessAdvisor) -> None:
        info = advisor.classify("epoxy_cast")
        assert info["polymer_class"] == "thermoset"
        assert info["remeltable"] is False

    def test_thermoset_cannot_regrind(self, advisor: PolymerProcessAdvisor) -> None:
        verdict = advisor.check_regrind("epoxy_cast")
        assert verdict["allowed"] is False
        assert "thermoset" in verdict["reason"].lower()

    def test_thermoplastic_can_regrind(self, advisor: PolymerProcessAdvisor) -> None:
        verdict = advisor.check_regrind("abs")
        assert verdict["allowed"] is True


# ---------------------------------------------------------------------------
# Shrinkage / warpage prediction
# ---------------------------------------------------------------------------


class TestShrinkageWarpage:
    def test_shrinkage_estimate_within_range(self, advisor: PolymerProcessAdvisor) -> None:
        result = advisor.estimate_shrinkage("abs")
        assert 0.001 <= result["mold_shrinkage_pct"] <= 0.010
        assert result["unit"] == "percent"
        assert "basis" in result

    def test_reinforced_polymer_lower_shrinkage(self, advisor: PolymerProcessAdvisor) -> None:
        plain = advisor.estimate_shrinkage("abs")
        reinforced = advisor.estimate_shrinkage("pa66_gf30")
        assert reinforced["mold_shrinkage_pct"] < plain["mold_shrinkage_pct"]

    def test_warpage_flagged_for_reinforced(self, advisor: PolymerProcessAdvisor) -> None:
        result = advisor.estimate_warpage("pa66_gf30")
        assert result["warpage_risk"] in ("low", "medium", "high")
        assert result["warpage_risk"] in ("medium", "high")  # fiber orientation anisotropy


# ---------------------------------------------------------------------------
# Fiber orientation effect
# ---------------------------------------------------------------------------


class TestFiberOrientation:
    def test_reinforced_material_reports_anisotropy(self, advisor: PolymerProcessAdvisor) -> None:
        result = advisor.fiber_orientation_effect("pa66_gf30")
        assert result["anisotropic"] is True
        assert 0.0 < result["strength_ratio_parallel_to_flow"] <= 1.0

    def test_unfilled_polymer_isotropic(self, advisor: PolymerProcessAdvisor) -> None:
        result = advisor.fiber_orientation_effect("abs")
        assert result["anisotropic"] is False


# ---------------------------------------------------------------------------
# Drying requirements
# ---------------------------------------------------------------------------


class TestDryingRequirements:
    def test_polyamide_requires_drying(self, advisor: PolymerProcessAdvisor) -> None:
        result = advisor.drying_requirement("pa66_gf30")
        assert result["drying_required"] is True
        assert result["temperature_C"] >= 70
        assert result["duration_hours"] >= 2

    def test_abs_requires_drying(self, advisor: PolymerProcessAdvisor) -> None:
        result = advisor.drying_requirement("abs")
        assert result["drying_required"] is True

    def test_unknown_material_drying_fail_closed(self, advisor: PolymerProcessAdvisor) -> None:
        result = advisor.drying_requirement("unobtanium_polymer")
        assert result["drying_required"] is None
        assert result["state"] == "insufficient_data"


# ---------------------------------------------------------------------------
# Cure kinetics basics (thermoset-only)
# ---------------------------------------------------------------------------


class TestCureKinetics:
    def test_thermoset_has_cure_schedule(self, advisor: PolymerProcessAdvisor) -> None:
        result = advisor.cure_schedule("epoxy_cast")
        assert result["gel_time_min"] > 0
        assert result["cure_temperature_C"] > 0
        assert result["post_cure_recommended"] in (True, False)

    def test_thermoplastic_has_no_cure_schedule(self, advisor: PolymerProcessAdvisor) -> None:
        result = advisor.cure_schedule("abs")
        assert result["state"] == "insufficient_data"
        assert "thermoplastic" in result["reason"].lower()


# ---------------------------------------------------------------------------
# Incompatible process rejection (MATE-AT-003 negative fixture)
# ---------------------------------------------------------------------------


class TestIncompatibleProcessRejection:
    def test_thermoset_injection_molding_rejected(self, advisor: PolymerProcessAdvisor) -> None:
        verdict = advisor.check_process_compatibility("epoxy_cast", "injection_molding")
        assert verdict["compatible"] is False
        assert "thermoset" in verdict["reason"].lower()

    def test_thermoset_compression_molding_compatible(self, advisor: PolymerProcessAdvisor) -> None:
        verdict = advisor.check_process_compatibility("epoxy_cast", "compression_molding")
        assert verdict["compatible"] is True

    def test_thermoplastic_injection_molding_compatible(self, advisor: PolymerProcessAdvisor) -> None:
        verdict = advisor.check_process_compatibility("abs", "injection_molding")
        assert verdict["compatible"] is True
