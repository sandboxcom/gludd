"""Tests for jurisdictions module — ISO codes, subdivisions, sovereignty, lookups."""

from __future__ import annotations

from plugins.module_utils.jurisdictions import (
    JURISDICTION_CODES,
    SOVEREIGNTY_STATUSES,
    SUBDIVISION_CODES,
    get_jurisdiction,
    get_parents,
    get_sovereignty_status,
    get_subdivisions,
    is_sovereign,
    list_jurisdictions,
    resolve_fips,
    resolve_gleif,
)


class TestJurisdictionCodes:
    def test_has_major_countries(self):
        assert "US" in JURISDICTION_CODES
        assert "GB" in JURISDICTION_CODES
        assert "DE" in JURISDICTION_CODES
        assert "CN" in JURISDICTION_CODES
        assert "JP" in JURISDICTION_CODES

    def test_each_entry_has_required_fields(self):
        required = {"alpha_2", "alpha_3", "numeric", "name", "fips", "gleif", "sovereignty", "region"}
        for code, rec in JURISDICTION_CODES.items():
            assert rec["alpha_2"] == code
            assert set(rec.keys()) >= required, f"{code} missing fields"
            assert rec["sovereignty"] in SOVEREIGNTY_STATUSES, f"{code} unknown sovereignty"

    def test_sovereign_states_have_numeric_code(self):
        for code, rec in JURISDICTION_CODES.items():
            if rec["sovereignty"] == "sovereign":
                assert rec["numeric"] is not None, f"Sovereign {code} missing numeric"

    def test_supranational_entity_eu(self):
        eu = JURISDICTION_CODES["EU"]
        assert eu["sovereignty"] == "supranational"
        assert eu["alpha_3"] is None
        assert eu["numeric"] is None

    def test_partial_recognition_entries(self):
        for code in ("XK", "TW", "PS"):
            assert JURISDICTION_CODES[code]["sovereignty"] == "partial"


class TestSubdivisionCodes:
    def test_us_states_present(self):
        for sub in ("US-CA", "US-TX", "US-NY"):
            assert sub in SUBDIVISION_CODES
            assert SUBDIVISION_CODES[sub]["parent"] == "US"

    def test_all_subdivisions_have_parent(self):
        for code, rec in SUBDIVISION_CODES.items():
            assert "parent" in rec, f"{code} missing parent"
            assert rec["parent"] in JURISDICTION_CODES, f"{code} has unknown parent {rec['parent']}"
            assert rec["parent"].upper() == rec["parent"]

    def test_subdivisions_have_category(self):
        for code, rec in SUBDIVISION_CODES.items():
            assert "category" in rec
            assert rec["category"] in {
                "state",
                "province",
                "country",
                "district",
                "region",
                "union territory",
                "prefecture",
            }


class TestSovereigntyStatuses:
    def test_expected_statuses(self):
        expected = {"sovereign", "partial", "disputed", "unrecognised", "supranational", "dependent_territory"}
        assert set(SOVEREIGNTY_STATUSES) == expected


class TestJurisdictionParents:
    def test_us_subdivisions_under_us(self):
        assert get_parents("US-CA") == frozenset({"US"})
        assert get_parents("US-TX") == frozenset({"US"})

    def test_parents_non_subdivision_is_none(self):
        assert get_parents("US") is None
        assert get_parents("XX") is None

    def test_de_subdivision_under_de(self):
        assert get_parents("DE-BE") == frozenset({"DE"})


class TestListJurisdictions:
    def test_all_returns_non_empty(self):
        result = list_jurisdictions()
        assert len(result) >= 25
        assert all("code" in r for r in result)
        assert result == sorted(result, key=lambda r: r["name"])

    def test_filter_by_sovereignty(self):
        sovereign = list_jurisdictions(sovereignty="sovereign")
        assert len(sovereign) > 0
        assert all(r["sovereignty"] == "sovereign" for r in sovereign)

    def test_filter_by_region(self):
        europe = list_jurisdictions(region="Europe")
        assert len(europe) > 0
        assert all(r["region"] == "Europe" for r in europe)

    def test_filter_by_nonexistent_sovereignty_returns_empty(self):
        assert list_jurisdictions(sovereignty="nonexistent") == []


class TestGetJurisdiction:
    def test_alpha2_lookup(self):
        us = get_jurisdiction("US")
        assert us is not None
        assert us["name"] == "United States"

    def test_alpha3_lookup(self):
        gbr = get_jurisdiction("GBR")
        assert gbr is not None
        assert gbr["name"] == "United Kingdom"

    def test_numeric_lookup(self):
        jp = get_jurisdiction("392")
        assert jp is not None
        assert jp["name"] == "Japan"

    def test_case_insensitive(self):
        assert get_jurisdiction("us") is not None
        assert get_jurisdiction("  US  ") is not None

    def test_unknown_is_none(self):
        assert get_jurisdiction("ZZ") is None

    def test_each_code_returns_copy_not_reference(self):
        a = get_jurisdiction("US")
        b = get_jurisdiction("US")
        assert a is not None
        assert b is not None
        assert a is not b


class TestGetSubdivisions:
    def test_us_subdivisions(self):
        subs = get_subdivisions("US")
        assert len(subs) >= 5
        codes = {s["code"] for s in subs}
        assert "US-CA" in codes
        assert "US-TX" in codes

    def test_gb_subdivisions(self):
        subs = get_subdivisions("GB")
        assert len(subs) >= 2
        codes = {s["code"] for s in subs}
        assert "GB-ENG" in codes

    def test_unknown_parent_empty(self):
        assert get_subdivisions("ZZ") == []


class TestIsSovereign:
    def test_sovereigns(self):
        for code in ("US", "GB", "DE", "FR", "JP", "CN"):
            assert is_sovereign(code)

    def test_non_sovereigns(self):
        assert not is_sovereign("EU")
        assert not is_sovereign("TW")
        assert not is_sovereign("XK")

    def test_unknown_is_false(self):
        assert not is_sovereign("ZZ")


class TestGetSovereigntyStatus:
    def test_known_codes(self):
        assert get_sovereignty_status("US") == "sovereign"
        assert get_sovereignty_status("EU") == "supranational"
        assert get_sovereignty_status("XK") == "partial"

    def test_unknown_is_none(self):
        assert get_sovereignty_status("ZZ") is None


class TestResolveFips:
    def test_known_fips(self):
        assert resolve_fips("UK") == "United Kingdom"
        assert resolve_fips("GM") == "Germany"

    def test_case_insensitive(self):
        assert resolve_fips("uk") == "United Kingdom"

    def test_unknown_is_none(self):
        assert resolve_fips("ZZ") is None


class TestResolveGleif:
    def test_known_gleif(self):
        assert resolve_gleif("US") == "United States"
        assert resolve_gleif("DE") == "Germany"

    def test_unknown_is_none(self):
        assert resolve_gleif("ZZ") is None


class TestGetParents:
    def test_subdivision_has_parent(self):
        assert get_parents("CA-ON") == frozenset({"CA"})
        assert get_parents("IN-MH") == frozenset({"IN"})

    def test_nonexistent_is_none(self):
        assert get_parents("ZZ") is None
        assert get_parents("") is None
