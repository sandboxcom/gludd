"""Unit tests for the governance authority_registry knowledge module."""

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
    / "authority_registry.py"
)

MODULE_NAME = "_authority_registry_under_test"


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


class TestAuthorityInstruments:
    def test_us_dos_issues_passport(self, mod):
        auth = mod.get_authority("US-DOS")
        assert auth is not None
        assert "passport" in auth["instruments"]
        assert auth["jurisdiction"] == "US"

    def test_us_bis_issues_export_license(self, mod):
        auth = mod.get_authority("US-COMMERCE-BIS")
        assert auth is not None
        assert "export_license" in auth["instruments"]

    def test_unknown_authority(self, mod):
        assert mod.get_authority("NONEXISTENT") is None

    def test_case_insensitive_lookup(self, mod):
        auth = mod.get_authority("us-dos")
        assert auth is not None
        assert auth["instruments"] is not None

    def test_un_treaty_depositary(self, mod):
        auth = mod.get_authority("UN-TREATY")
        assert auth is not None
        assert "treaty_depositary" in auth["instruments"]
        assert auth["jurisdiction"] == "international"

    def test_eu_commission(self, mod):
        auth = mod.get_authority("EU-COM")
        assert auth is not None
        assert "competition_clearance" in auth["instruments"]


class TestPassportAuthorities:
    def test_us_passport_authority(self, mod):
        auth = mod.get_passport_authority("US")
        assert auth is not None
        assert auth["authority"] == "US-DOS"

    def test_uk_passport_authority(self, mod):
        auth = mod.get_passport_authority("GB")
        assert auth is not None
        assert auth["document"] == "British Passport"

    def test_unknown_country(self, mod):
        assert mod.get_passport_authority("XX") is None

    def test_all_have_biometric_year(self, mod):
        for country in mod.PASSPORT_AUTHORITIES:
            auth = mod.get_passport_authority(country)
            assert auth is not None
            assert "biometric_since" in auth


class TestLicenseAuthorities:
    def test_driving_license_us(self, mod):
        auth = mod.get_license_authority("driving", "US")
        assert auth is not None
        assert "state_dmv" in auth["authority"]

    def test_export_license_gb(self, mod):
        auth = mod.get_license_authority("export", "GB")
        assert auth is not None
        assert auth["authority"] == "UK-ECJU"

    def test_business_license_de(self, mod):
        auth = mod.get_license_authority("business", "DE")
        assert auth is not None
        assert "handelsregister" in auth["authority"]

    def test_building_permit_fr(self, mod):
        auth = mod.get_license_authority("building", "FR")
        assert auth is not None
        assert "mairie" in auth["name"].lower()

    def test_unknown_license_type(self, mod):
        assert mod.get_license_authority("nonexistent", "US") is None

    def test_unknown_country_for_license(self, mod):
        assert mod.get_license_authority("driving", "XX") is None


class TestTreatyDepositaries:
    def test_un_treaty_section(self, mod):
        dep = mod.get_treaty_depositary("UN Treaty Section")
        assert dep is not None
        assert dep["institution"] == "United Nations Treaty Section"
        assert dep["certified_true_copies"] is True

    def test_swiss_federal_council(self, mod):
        dep = mod.get_treaty_depositary("Swiss Federal Council")
        assert dep is not None
        assert dep["jurisdiction"] == "CH"

    def test_case_insensitive(self, mod):
        dep = mod.get_treaty_depositary("un treaty section")
        assert dep is not None

    def test_unknown_depositary(self, mod):
        assert mod.get_treaty_depositary("Nonexistent") is None


class TestExportControlAuthorities:
    def test_us_export_control(self, mod):
        auth = mod.get_export_control_authority("US")
        assert auth is not None
        assert auth["regime"] == "EAR (Export Administration Regulations)"

    def test_gb_export_control(self, mod):
        auth = mod.get_export_control_authority("GB")
        assert auth is not None
        assert auth["authority"] == "UK-ECJU"

    def test_de_export_control(self, mod):
        auth = mod.get_export_control_authority("DE")
        assert auth is not None
        assert auth["authority"] == "DE-BAFA"

    def test_unknown_country(self, mod):
        assert mod.get_export_control_authority("XX") is None


class TestAuthoritiesByInstrument:
    def test_passport_instrument(self, mod):
        authorities = mod.authorities_by_instrument("passport")
        assert len(authorities) > 0
        codes = [a["code"] for a in authorities]
        assert "US-DOS" in codes

    def test_export_license_instrument(self, mod):
        authorities = mod.authorities_by_instrument("export_license")
        assert len(authorities) > 0
        codes = [a["code"] for a in authorities]
        assert "US-COMMERCE-BIS" in codes

    def test_driving_license_instrument(self, mod):
        authorities = mod.authorities_by_instrument("driving_license")
        assert len(authorities) > 0

    def test_visa_instrument(self, mod):
        authorities = mod.authorities_by_instrument("visa")
        assert len(authorities) > 0
        codes = [a["code"] for a in authorities]
        assert "US-DOS" in codes

    def test_nonexistent_instrument(self, mod):
        authorities = mod.authorities_by_instrument("nonexistent")
        assert authorities == []

    def test_result_includes_code(self, mod):
        authorities = mod.authorities_by_instrument("passport")
        for a in authorities:
            assert "code" in a
            assert "name" in a
            assert "jurisdiction" in a

    def test_case_insensitive_instrument(self, mod):
        authorities = mod.authorities_by_instrument("PASSPORT")
        assert len(authorities) > 0
