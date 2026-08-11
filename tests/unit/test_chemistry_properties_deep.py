"""Deep unit tests for ``general_ludd.chemistry.properties``.

Covers CHEM-004 normalisation edges, formula-to-name aliases, condition-filter
boundary cases, and registry shape invariants that the existing property lookup
tests in ``test_chemistry_safety.py`` do not exercise.
"""

from __future__ import annotations

import importlib.util
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_PROPERTIES_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "properties.py")


def _load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"{name} spec failed"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


props = _load_module(_PROPERTIES_PATH, "chemistry_properties_deep_under_test")


# ---------------------------------------------------------------------------
# _normalize_entity — formula-to-common-name aliases
# ---------------------------------------------------------------------------


class TestNormalizeEntityAliases:
    def test_h2o_aliased_to_water(self):
        assert props._normalize_entity("H2O") == "water"

    def test_ice_aliased_to_water(self):
        assert props._normalize_entity("ice") == "water"

    def test_c2h5oh_aliased_to_ethanol(self):
        assert props._normalize_entity("C2H5OH") == "ethanol"

    def test_c2h6o_aliased_to_ethanol(self):
        assert props._normalize_entity("C2H6O") == "ethanol"

    def test_ch4_aliased_to_methane(self):
        assert props._normalize_entity("CH4") == "methane"

    def test_c6h6_aliased_to_benzene(self):
        assert props._normalize_entity("C6H6") == "benzene"

    def test_ch3oh_aliased_to_methanol(self):
        assert props._normalize_entity("CH3OH") == "methanol"

    def test_unknown_formula_passed_through(self):
        result = props._normalize_entity("C10H22")
        assert result == "c10h22"

    def test_whitespace_stripped(self):
        assert props._normalize_entity("  water  ") == "water"

    def test_uppercase_mixed_passed_through_and_lowered(self):
        result = props._normalize_entity("Ethanol")
        assert result == "ethanol"


# ---------------------------------------------------------------------------
# look_property — property name case normalisation
# ---------------------------------------------------------------------------


class TestLookupPropertyNameNormalisation:
    def test_lowercase_succeeds(self):
        result = props.lookup_property("water", "boiling_point")
        assert result["status"] == "succeeded"

    def test_uppercase_normalised(self):
        result = props.lookup_property("water", "Boiling_Point")
        assert result["status"] == "succeeded"
        assert result["observations"]

    def test_mixed_case_with_spaces_normalised(self):
        result = props.lookup_property("water", " Boiling_POINT ")
        assert result["status"] == "succeeded"
        assert result["observations"]


# ---------------------------------------------------------------------------
# lookup_property — multiple properties per entity
# ---------------------------------------------------------------------------


class TestLookupPropertyMultipleProperties:
    def test_water_density_exists(self):
        result = props.lookup_property("water", "density")
        assert result["status"] == "succeeded"
        assert result["observations"]
        assert result["observations"][0]["unit"] == "kg/m^3"

    def test_water_melting_point_exists(self):
        result = props.lookup_property("water", "melting_point")
        assert result["status"] == "succeeded"
        assert result["observations"]
        assert result["observations"][0]["value"] == 273.15

    def test_ethanol_flash_point_exists(self):
        result = props.lookup_property("ethanol", "flash_point")
        assert result["status"] == "succeeded"
        assert result["observations"]

    def test_benzene_boiling_point_exists(self):
        result = props.lookup_property("benzene", "boiling_point")
        assert result["status"] == "succeeded"
        assert result["observations"]
        assert result["observations"][0]["value"] == 353.23


# ---------------------------------------------------------------------------
# lookup_property — unknown entity
# ---------------------------------------------------------------------------


class TestLookupPropertyUnknownEntity:
    def test_unknown_entity_returns_degraded(self):
        result = props.lookup_property("nonsense_xyz", "boiling_point")
        assert result["status"] == "degraded"
        assert result["observations"] == []
        assert any("no-evidence" in lim.lower() for lim in result["limitations"])

    def test_empty_entity_returns_degraded(self):
        result = props.lookup_property("", "boiling_point")
        assert result["status"] == "degraded"
        assert result["observations"] == []


# ---------------------------------------------------------------------------
# lookup_property — condition filter edge cases
# ---------------------------------------------------------------------------


class TestLookupPropertyConditionFilter:
    def test_no_match_condition_returns_refused(self):
        result = props.lookup_property("water", "boiling_point", conditions={"pressure": "999 atm"})
        assert result["status"] == "refused"
        assert result["observations"] == []
        assert any("condition-mismatch" in lim.lower() for lim in result["limitations"])

    def test_condition_filter_preserves_entity_and_property_in_response(self):
        result = props.lookup_property("ethanol", "boiling_point", conditions={"pressure": "1 atm"})
        assert result["entity"] == "ethanol"
        assert result["property"] == "boiling_point"

    def test_unknown_entity_with_conditions_returns_degraded(self):
        result = props.lookup_property("nonexistent_substance", "density", conditions={"pressure": "1 atm"})
        assert result["status"] == "degraded"
        assert result["observations"] == []


# ---------------------------------------------------------------------------
# Schema and response shape invariants
# ---------------------------------------------------------------------------


class TestResponseShapeInvariants:
    def test_schema_version_present(self):
        result = props.lookup_property("water", "boiling_point")
        assert result["schema_version"] == props.SCHEMA_VERSION

    def test_errors_key_present(self):
        result = props.lookup_property("water", "boiling_point")
        assert "errors" in result

    def test_limitations_key_present(self):
        result = props.lookup_property("water", "boiling_point")
        assert "limitations" in result

    def test_success_result_has_empty_errors(self):
        result = props.lookup_property("water", "density")
        assert result["errors"] == []

    def test_observations_are_deep_copies(self):
        r1 = props.lookup_property("water", "boiling_point")
        r2 = props.lookup_property("water", "boiling_point")
        assert r1["observations"] is not r2["observations"]
        assert r1["observations"] == r2["observations"]


# ---------------------------------------------------------------------------
# PROPERTY_REGISTRY — fixture shape invariants
# ---------------------------------------------------------------------------


class TestPropertyRegistryShape:
    def test_keys_are_two_tuples(self):
        for key in props.PROPERTY_REGISTRY:
            assert isinstance(key, tuple) and len(key) == 2

    def test_values_are_lists(self):
        for value in props.PROPERTY_REGISTRY.values():
            assert isinstance(value, list)

    def test_registry_contains_five_entities(self):
        entities = {k[0] for k in props.PROPERTY_REGISTRY}
        assert entities == {"water", "ethanol", "methane", "benzene"}

    def test_every_observation_has_provenance_source_id(self):
        for obs_list in props.PROPERTY_REGISTRY.values():
            for obs in obs_list:
                assert "source_id" in obs["provenance"]
                assert len(obs["provenance"]["source_id"]) >= 8

    def test_methane_has_both_measured_and_predicted_boiling_point(self):
        key = ("methane", "boiling_point")
        assert key in props.PROPERTY_REGISTRY
        methods = {o["method"] for o in props.PROPERTY_REGISTRY[key]}
        assert methods >= {"measured", "predicted"}


# ---------------------------------------------------------------------------
# Export completeness
# ---------------------------------------------------------------------------


class TestExports:
    def test_all_expected(self):
        assert hasattr(props, "PROPERTY_REGISTRY")
        assert hasattr(props, "lookup_property")
        assert hasattr(props, "SCHEMA_VERSION")
