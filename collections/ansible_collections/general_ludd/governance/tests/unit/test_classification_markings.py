"""Tests for classification_markings module — banners, portion markings, caveats."""

from __future__ import annotations

from plugins.module_utils.classification_markings import (
    BANNER_FORMATS,
    CAVEAT_CODES,
    DECLASS_SCHEDULES,
    DISSEM_CONTROLS,
    PORTION_MARKINGS,
    get_banner_line,
    get_declass_schedule,
    get_dissem_control,
    get_portion_marking,
    list_caveats,
    list_systems,
    resolve_caveat,
)


class TestBannerFormats:
    def test_all_major_systems_present(self):
        expected = {"US", "UK", "NATO", "EU", "FR", "DE", "CA", "AU"}
        assert set(BANNER_FORMATS.keys()) == expected

    def test_us_has_all_levels(self):
        levels = {"public", "unclassified", "restricted", "confidential", "secret", "top_secret", "caveated"}
        assert set(BANNER_FORMATS["US"].keys()) == levels

    def test_each_system_has_caveated_template(self):
        for system, levels in BANNER_FORMATS.items():
            assert "caveated" in levels, f"{system} missing caveated template"


class TestGetBannerLine:
    def test_us_top_secret(self):
        result = get_banner_line("US", "top_secret")
        assert result == "TOP SECRET"

    def test_us_unclassified(self):
        result = get_banner_line("US", "unclassified")
        assert result == "UNCLASSIFIED"

    def test_uk_secret(self):
        result = get_banner_line("UK", "secret")
        assert result == "UK SECRET"

    def test_nato_secret(self):
        result = get_banner_line("NATO", "secret")
        assert result == "NATO SECRET"

    def test_eu_restricted(self):
        result = get_banner_line("EU", "restricted")
        assert result == "RESTREINT UE/EU RESTRICTED"

    def test_de_top_secret(self):
        result = get_banner_line("DE", "top_secret")
        assert result == "STRENG GEHEIM"

    def test_with_caveats(self):
        result = get_banner_line("US", "caveated", caveats=["SI", "NOFORN"])
        assert result == "TOP SECRET//SI//NOFORN"

    def test_caveats_ignored_when_no_placeholder(self):
        result = get_banner_line("US", "secret", caveats=["SI"])
        assert result == "SECRET"

    def test_unknown_system_is_none(self):
        assert get_banner_line("XX", "secret") is None

    def test_unknown_level_is_none(self):
        assert get_banner_line("US", "foobar") is None

    def test_system_case_insensitive(self):
        assert get_banner_line("us", "secret") == "SECRET"
        assert get_banner_line("uk", "secret") == "UK SECRET"


class TestPortionMarkings:
    def test_us_present(self):
        assert "US" in PORTION_MARKINGS
        assert PORTION_MARKINGS["US"]["prefix"] is True
        assert PORTION_MARKINGS["US"]["delimiter"] == "//"

    def test_uk_present(self):
        assert "UK" in PORTION_MARKINGS

    def test_nato_present(self):
        assert "NATO" in PORTION_MARKINGS

    def test_eu_present(self):
        assert "EU" in PORTION_MARKINGS


class TestGetPortionMarking:
    def test_us_secret_no_caveat(self):
        result = get_portion_marking("US", "secret")
        assert result == "(SECRET)"

    def test_us_secret_with_si(self):
        result = get_portion_marking("US", "secret", caveats=["SI"])
        assert result == "SECRET//SI"

    def test_us_top_secret_with_multiple(self):
        result = get_portion_marking("US", "top_secret", caveats=["SI", "NOFORN"])
        assert result == "TOP SECRET//SI//NOFORN"

    def test_unknown_system_is_none(self):
        assert get_portion_marking("XX", "secret") is None

    def test_unknown_level_is_none(self):
        assert get_portion_marking("US", "foobar") is None


class TestCaveatCodes:
    def test_core_caveats_exist(self):
        codes = {"NOFORN", "ORCON", "SI", "HCS", "TK", "FOUO", "COSMIC", "ATOMAL", "STRAP"}
        assert codes <= set(CAVEAT_CODES.keys())

    def test_each_caveat_has_required_keys(self):
        for code, rec in CAVEAT_CODES.items():
            assert "description" in rec
            assert "systems" in rec
            assert isinstance(rec["systems"], list)

    def test_si_is_us_only(self):
        assert CAVEAT_CODES["SI"]["systems"] == ["US"]

    def test_cosmic_is_nato_only(self):
        assert CAVEAT_CODES["COSMIC"]["systems"] == ["NATO"]

    def test_strap_is_uk_only(self):
        assert CAVEAT_CODES["STRAP"]["systems"] == ["UK"]


class TestResolveCaveat:
    def test_known_code(self):
        result = resolve_caveat("SI")
        assert result is not None
        assert result["full_name"] == "Special Intelligence (SIGINT)"

    def test_case_insensitive(self):
        assert resolve_caveat("si") is not None
        assert resolve_caveat("  NOFORN  ") is not None

    def test_unknown_is_none(self):
        assert resolve_caveat("BOGUS") is None

    def test_returns_copy(self):
        a = resolve_caveat("SI")
        b = resolve_caveat("SI")
        assert a is not None
        assert b is not None
        assert a is not b


class TestDissemControls:
    def test_core_controls_exist(self):
        codes = {"LIMDIS", "NOCONTRACT", "PROPIN", "WNINTEL", "UK EYES ONLY", "ACCM"}
        assert codes <= set(DISSEM_CONTROLS.keys())


class TestGetDissemControl:
    def test_known_code(self):
        result = get_dissem_control("LIMDIS")
        assert result is not None
        assert "Limited Distribution" in result["description"]

    def test_uk_eyes_only(self):
        result = get_dissem_control("UK EYES ONLY")
        assert result is not None
        assert result["systems"] == ["UK"]

    def test_case_insensitive(self):
        assert get_dissem_control("limdis") is not None

    def test_unknown_is_none(self):
        assert get_dissem_control("BOGUS") is None


class TestDeclassSchedules:
    def test_all_systems_present(self):
        expected = {"US", "UK", "NATO", "EU"}
        assert set(DECLASS_SCHEDULES.keys()) == expected


class TestGetDeclassSchedule:
    def test_us_schedule(self):
        sched = get_declass_schedule("US")
        assert sched is not None
        assert sched["default_years"] == 10
        assert sched["max_years"] == 25

    def test_uk_schedule(self):
        sched = get_declass_schedule("UK")
        assert sched is not None
        assert sched["default_years"] == 20

    def test_unknown_system_is_none(self):
        assert get_declass_schedule("ZZ") is None

    def test_case_insensitive(self):
        assert get_declass_schedule("us") is not None


class TestListSystems:
    def test_returns_sorted(self):
        systems = list_systems()
        assert systems == sorted(systems)
        assert "US" in systems
        assert len(systems) == 8


class TestListCaveats:
    def test_all_returns_all(self):
        all_caveats = list_caveats()
        assert len(all_caveats) == len(CAVEAT_CODES)

    def test_filter_by_us(self):
        us_caveats = list_caveats(system="US")
        assert all("US" in CAVEAT_CODES[c]["systems"] for c in us_caveats)
        assert "SI" in us_caveats

    def test_filter_by_nato(self):
        nato_caveats = list_caveats(system="NATO")
        assert "COSMIC" in nato_caveats
        assert "ATOMAL" in nato_caveats

    def test_filter_is_case_insensitive(self):
        assert list_caveats(system="us") == list_caveats(system="US")
