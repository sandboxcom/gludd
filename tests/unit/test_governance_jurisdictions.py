"""Unit tests for the governance jurisdictions knowledge module."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "governance"
    / "plugins"
    / "module_utils"
    / "jurisdictions.py"
)

MODULE_NAME = "_jurisdictions_under_test"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod() -> ModuleType:
    return _load_module()


class TestJurisdictionCodes:
    def test_us_in_codes(self, mod):
        assert "US" in mod.JURISDICTION_CODES
        assert mod.JURISDICTION_CODES["US"]["alpha_3"] == "USA"
        assert mod.JURISDICTION_CODES["US"]["numeric"] == "840"
        assert mod.JURISDICTION_CODES["US"]["sovereignty"] == "sovereign"

    def test_partial_sovereignty(self, mod):
        assert mod.JURISDICTION_CODES["TW"]["sovereignty"] == "partial"
        assert mod.JURISDICTION_CODES["PS"]["sovereignty"] == "partial"

    def test_supranational_eu(self, mod):
        assert mod.JURISDICTION_CODES["EU"]["sovereignty"] == "supranational"
        assert mod.JURISDICTION_CODES["EU"]["alpha_3"] is None

    def test_fips_codes_present(self, mod):
        assert mod.JURISDICTION_CODES["US"]["fips"] == "US"
        assert mod.JURISDICTION_CODES["CA"]["fips"] == "CA"
        assert mod.JURISDICTION_CODES["GB"]["fips"] == "UK"


class TestSubdivisionCodes:
    def test_us_state_subdivisions(self, mod):
        assert mod.SUBDIVISION_CODES["US-CA"]["name"] == "California"
        assert mod.SUBDIVISION_CODES["US-CA"]["parent"] == "US"
        assert mod.SUBDIVISION_CODES["US-CA"]["category"] == "state"

    def test_gb_country_subdivisions(self, mod):
        assert mod.SUBDIVISION_CODES["GB-ENG"]["name"] == "England"
        assert mod.SUBDIVISION_CODES["GB-ENG"]["parent"] == "GB"
        assert mod.SUBDIVISION_CODES["GB-SCT"]["category"] == "country"

    def test_de_state_subdivisions(self, mod):
        assert mod.SUBDIVISION_CODES["DE-BE"]["name"] == "Berlin"


class TestSovereigntyStatuses:
    def test_frozenset_contains_all_statuses(self, mod):
        assert "sovereign" in mod.SOVEREIGNTY_STATUSES
        assert "partial" in mod.SOVEREIGNTY_STATUSES
        assert "disputed" in mod.SOVEREIGNTY_STATUSES
        assert "unrecognised" in mod.SOVEREIGNTY_STATUSES
        assert "supranational" in mod.SOVEREIGNTY_STATUSES
        assert "dependent_territory" in mod.SOVEREIGNTY_STATUSES


class TestListJurisdictions:
    def test_returns_all_by_default(self, mod):
        results = mod.list_jurisdictions()
        assert len(results) >= 25
        names = [r["name"] for r in results]
        assert "United States" in names
        assert "France" in names

    def test_filter_by_sovereignty(self, mod):
        results = mod.list_jurisdictions(sovereignty="sovereign")
        assert all(r["sovereignty"] == "sovereign" for r in results)
        assert len(results) > 0

    def test_filter_by_region(self, mod):
        results = mod.list_jurisdictions(region="Europe")
        assert all(r["region"] == "Europe" for r in results)
        assert any(r["name"] == "France" for r in results)

    def test_combined_filters(self, mod):
        results = mod.list_jurisdictions(sovereignty="sovereign", region="Europe")
        for r in results:
            assert r["sovereignty"] == "sovereign"
            assert r["region"] == "Europe"


class TestGetJurisdiction:
    def test_by_alpha2(self, mod):
        rec = mod.get_jurisdiction("US")
        assert rec is not None
        assert rec["name"] == "United States"

    def test_by_alpha3(self, mod):
        rec = mod.get_jurisdiction("USA")
        assert rec is not None
        assert rec["alpha_2"] == "US"

    def test_by_numeric(self, mod):
        rec = mod.get_jurisdiction("840")
        assert rec is not None
        assert rec["alpha_2"] == "US"

    def test_unknown_code(self, mod):
        assert mod.get_jurisdiction("XX") is None

    def test_case_insensitive(self, mod):
        rec = mod.get_jurisdiction("us")
        assert rec is not None
        assert rec["name"] == "United States"


class TestGetSubdivisions:
    def test_us_subdivisions(self, mod):
        subs = mod.get_subdivisions("US")
        names = [s["name"] for s in subs]
        assert "California" in names
        assert "Texas" in names

    def test_uk_subdivisions(self, mod):
        subs = mod.get_subdivisions("GB")
        names = [s["name"] for s in subs]
        assert "England" in names
        assert "Scotland" in names

    def test_no_subdivisions_for_unknown(self, mod):
        assert mod.get_subdivisions("XX") == []


class TestSovereigntyHelpers:
    def test_is_sovereign_true(self, mod):
        assert mod.is_sovereign("US") is True
        assert mod.is_sovereign("FR") is True

    def test_is_sovereign_false(self, mod):
        assert mod.is_sovereign("TW") is False
        assert mod.is_sovereign("EU") is False
        assert mod.is_sovereign("XX") is False

    def test_get_sovereignty_status(self, mod):
        assert mod.get_sovereignty_status("US") == "sovereign"
        assert mod.get_sovereignty_status("TW") == "partial"
        assert mod.get_sovereignty_status("EU") == "supranational"
        assert mod.get_sovereignty_status("XX") is None


class TestResolveCodes:
    def test_resolve_fips(self, mod):
        assert mod.resolve_fips("US") == "United States"
        assert mod.resolve_fips("UK") == "United Kingdom"
        assert mod.resolve_fips("GM") == "Germany"
        assert mod.resolve_fips("XX") is None

    def test_resolve_gleif(self, mod):
        assert mod.resolve_gleif("US") == "United States"
        assert mod.resolve_gleif("GB") == "United Kingdom"
        assert mod.resolve_gleif("XX") is None


class TestJurisdictionParents:
    def test_parents_exist(self, mod):
        parents = mod.get_parents("US-CA")
        assert parents is not None
        assert "US" in parents

    def test_parents_for_state(self, mod):
        parents = mod.get_parents("US-TX")
        assert parents is not None
        assert "US" in parents

    def test_unknown_parents(self, mod):
        assert mod.get_parents("XX-YY") is None
