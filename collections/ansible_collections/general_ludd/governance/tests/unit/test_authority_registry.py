"""Tests for authority_registry module — issuing authorities, instrument lookups."""

from __future__ import annotations

import pytest

from plugins.module_utils.authority_registry import (
    AUTHORITY_INSTRUMENTS,
    PASSPORT_AUTHORITIES,
    LICENSE_AUTHORITIES,
    TREATY_DEPOSITARIES,
    EXPORT_CONTROL_AUTHORITIES,
    get_authority,
    get_passport_authority,
    get_license_authority,
    get_treaty_depositary,
    get_export_control_authority,
    authorities_by_instrument,
)


class TestAuthorityInstruments:
    def test_has_core_authorities(self):
        codes = {
            "US-DOS",
            "US-DHS",
            "UK-HMPO",
            "UK-HOME",
            "CA-IRCC",
            "AU-DFAT",
            "FR-ANTS",
            "DE-BVA",
            "EU-COM",
            "UN-TREATY",
            "WIPO",
            "ICAO",
        }
        assert codes <= set(AUTHORITY_INSTRUMENTS.keys())

    def test_all_entries_have_required_keys(self):
        for code, auth in AUTHORITY_INSTRUMENTS.items():
            assert "name" in auth
            assert "jurisdiction" in auth
            assert "instruments" in auth
            assert isinstance(auth["instruments"], list)
            assert len(auth["instruments"]) > 0


class TestGetAuthority:
    def test_known_authority(self):
        result = get_authority("US-DOS")
        assert result is not None
        assert result["name"] == "U.S. Department of State"
        assert result["jurisdiction"] == "US"

    def test_case_insensitive(self):
        assert get_authority("us-dos") is not None
        assert get_authority("  uk-hmpo  ") is not None

    def test_unknown_is_none(self):
        assert get_authority("ZZ-XXX") is None


class TestPassportAuthorities:
    def test_has_major_countries(self):
        expected = {"US", "GB", "CA", "AU", "FR", "DE"}
        assert expected <= set(PASSPORT_AUTHORITIES.keys())

    def test_all_have_required_keys(self):
        for cc, pa in PASSPORT_AUTHORITIES.items():
            assert "authority" in pa
            assert "name" in pa
            assert "document" in pa
            assert "biometric_since" in pa
            assert pa["authority"] in AUTHORITY_INSTRUMENTS, f"{cc} authority {pa['authority']} not in registry"


class TestGetPassportAuthority:
    def test_us(self):
        result = get_passport_authority("US")
        assert result is not None
        assert result["document"] == "US Passport"

    def test_gb(self):
        result = get_passport_authority("GB")
        assert result is not None
        assert result["authority"] == "UK-HMPO"

    def test_case_insensitive(self):
        assert get_passport_authority("us") is not None

    def test_unknown_is_none(self):
        assert get_passport_authority("ZZ") is None


class TestLicenseAuthorities:
    def test_license_types(self):
        types = {"driving", "export", "business", "building"}
        assert set(LICENSE_AUTHORITIES.keys()) == types

    def test_driving_has_us_gb_fr(self):
        assert {"US", "GB", "FR"} <= set(LICENSE_AUTHORITIES["driving"].keys())

    def test_export_has_us_gb_de(self):
        assert {"US", "GB", "DE"} <= set(LICENSE_AUTHORITIES["export"].keys())


class TestGetLicenseAuthority:
    def test_us_driving(self):
        result = get_license_authority("driving", "US")
        assert result is not None
        assert result["authority"] == "state_dmv"

    def test_gb_export(self):
        result = get_license_authority("export", "GB")
        assert result is not None
        assert result["authority"] == "UK-ECJU"

    def test_unknown_type_is_none(self):
        assert get_license_authority("fishing", "US") is None

    def test_unknown_country_is_none(self):
        assert get_license_authority("driving", "ZZ") is None


class TestTreatyDepositaries:
    def test_has_un_treaty_section(self):
        assert "UN Treaty Section" in TREATY_DEPOSITARIES

    def test_has_swiss_federal_council(self):
        assert "Swiss Federal Council" in TREATY_DEPOSITARIES

    def test_all_have_required_keys(self):
        for name, rec in TREATY_DEPOSITARIES.items():
            assert "institution" in rec
            assert "jurisdiction" in rec
            assert "role" in rec


class TestGetTreatyDepositary:
    def test_exact_match(self):
        result = get_treaty_depositary("UN Treaty Section")
        assert result is not None
        assert result["institution"] == "United Nations Treaty Section"

    def test_case_insensitive(self):
        result = get_treaty_depositary("un treaty section")
        assert result is not None

    def test_unknown_is_none(self):
        assert get_treaty_depositary("Ministry of Magic") is None

    def test_returns_copy(self):
        a = get_treaty_depositary("UN Treaty Section")
        b = get_treaty_depositary("UN Treaty Section")
        assert a is not None
        assert b is not None
        assert a is not b


class TestExportControlAuthorities:
    def test_has_major_countries(self):
        expected = {"US", "GB", "DE", "AU", "CA"}
        assert expected <= set(EXPORT_CONTROL_AUTHORITIES.keys())

    def test_us_has_co_regulators(self):
        assert len(EXPORT_CONTROL_AUTHORITIES["US"]["co_regulators"]) > 0


class TestGetExportControlAuthority:
    def test_us(self):
        result = get_export_control_authority("US")
        assert result is not None
        assert result["regime"] == "EAR (Export Administration Regulations)"

    def test_gb(self):
        result = get_export_control_authority("GB")
        assert result is not None
        assert result["authority"] == "UK-ECJU"

    def test_case_insensitive(self):
        assert get_export_control_authority("us") is not None

    def test_unknown_is_none(self):
        assert get_export_control_authority("ZZ") is None


class TestAuthoritiesByInstrument:
    def test_passport(self):
        results = authorities_by_instrument("passport")
        auth_codes = {r["code"] for r in results}
        assert "US-DOS" in auth_codes
        assert "UK-HMPO" in auth_codes

    def test_export_license(self):
        results = authorities_by_instrument("export_license")
        auth_codes = {r["code"] for r in results}
        assert "US-COMMERCE-BIS" in auth_codes

    def test_driving_license(self):
        results = authorities_by_instrument("driving_license")
        assert len(results) > 0

    def test_variant_spacing(self):
        results1 = authorities_by_instrument("export_license")
        results2 = authorities_by_instrument("export license")
        assert len(results1) == len(results2)

    def test_unknown_instrument_empty(self):
        assert authorities_by_instrument("unicorn_permit") == []
