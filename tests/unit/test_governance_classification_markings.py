"""Unit tests for the governance classification_markings knowledge module."""

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
    / "classification_markings.py"
)

MODULE_NAME = "_classification_markings_under_test"


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


class TestBannerFormats:
    def test_all_systems_have_caveated(self, mod):
        for system in mod.BANNER_FORMATS:
            assert "caveated" in mod.BANNER_FORMATS[system], f"{system} missing caveated level"

    def test_us_banner_lines(self, mod):
        assert mod.BANNER_FORMATS["US"]["unclassified"] == "UNCLASSIFIED"
        assert mod.BANNER_FORMATS["US"]["secret"] == "SECRET"
        assert mod.BANNER_FORMATS["US"]["top_secret"] == "TOP SECRET"

    def test_uk_banner_lines(self, mod):
        assert mod.BANNER_FORMATS["UK"]["restricted"] == "OFFICIAL-SENSITIVE"
        assert mod.BANNER_FORMATS["UK"]["secret"] == "UK SECRET"
        assert mod.BANNER_FORMATS["UK"]["top_secret"] == "UK TOP SECRET"

    def test_nato_banner_lines(self, mod):
        assert mod.BANNER_FORMATS["NATO"]["unclassified"] == "NATO UNCLASSIFIED"
        assert mod.BANNER_FORMATS["NATO"]["secret"] == "NATO SECRET"

    def test_nato_caveated_has_placeholder(self, mod):
        assert "{caveats}" in mod.BANNER_FORMATS["NATO"]["caveated"]


class TestGetBannerLine:
    def test_us_secret(self, mod):
        result = mod.get_banner_line("US", "secret")
        assert result == "SECRET"

    def test_us_top_secret_with_caveats(self, mod):
        result = mod.get_banner_line("US", "caveated", caveats=["SI", "NOFORN"])
        assert result == "TOP SECRET//SI//NOFORN"

    def test_nato_cosmic_with_caveat(self, mod):
        result = mod.get_banner_line("NATO", "caveated", caveats=["ATOMAL"])
        assert result == "COSMIC TOP SECRET//ATOMAL"

    def test_unknown_system(self, mod):
        assert mod.get_banner_line("XX", "secret") is None

    def test_unknown_level(self, mod):
        assert mod.get_banner_line("US", "nonexistent") is None

    def test_lowercase_input(self, mod):
        result = mod.get_banner_line("us", "secret")
        assert result == "SECRET"


class TestGetPortionMarking:
    def test_us_secret_portion(self, mod):
        result = mod.get_portion_marking("US", "secret")
        assert "SECRET" in result

    def test_us_secret_with_caveats(self, mod):
        result = mod.get_portion_marking("US", "secret", caveats=["SI"])
        assert "SI" in result
        assert "SECRET" in result

    def test_unknown_system(self, mod):
        assert mod.get_portion_marking("XX", "secret") is None


class TestCaveatCodes:
    def test_noforn_exists(self, mod):
        cav = mod.resolve_caveat("NOFORN")
        assert cav is not None
        assert cav["description"] is not None

    def test_si_exists(self, mod):
        cav = mod.resolve_caveat("SI")
        assert cav is not None
        assert "US" in cav["systems"]

    def test_cosmic_nato_only(self, mod):
        cav = mod.resolve_caveat("COSMIC")
        assert cav is not None
        assert cav["systems"] == ["NATO"]

    def test_strap_uk_only(self, mod):
        cav = mod.resolve_caveat("STRAP")
        assert cav is not None
        assert "UK" in cav["systems"]

    def test_unknown_caveat(self, mod):
        assert mod.resolve_caveat("NONEXISTENT") is None

    def test_case_insensitive(self, mod):
        cav = mod.resolve_caveat("noforn")
        assert cav is not None

    def test_rel_to_usa_caveat(self, mod):
        cav = mod.resolve_caveat("REL TO USA")
        assert cav is not None
        assert "US" in cav["systems"]


class TestDissemControls:
    def test_limdis_exists(self, mod):
        dc = mod.get_dissem_control("LIMDIS")
        assert dc is not None

    def test_uk_eyes_only_exists(self, mod):
        dc = mod.get_dissem_control("UK EYES ONLY")
        assert dc is not None
        assert "UK" in dc["systems"]

    def test_unknown_control(self, mod):
        assert mod.get_dissem_control("NONEXISTENT") is None


class TestDeclassSchedules:
    def test_us_schedule(self, mod):
        sched = mod.get_declass_schedule("US")
        assert sched is not None
        assert sched["default_years"] == 10
        assert sched["max_years"] == 25

    def test_uk_schedule(self, mod):
        sched = mod.get_declass_schedule("UK")
        assert sched is not None
        assert sched["default_years"] == 20

    def test_nato_schedule(self, mod):
        sched = mod.get_declass_schedule("NATO")
        assert sched is not None
        assert sched["default_years"] == 30

    def test_unknown_system(self, mod):
        assert mod.get_declass_schedule("XX") is None

    def test_case_insensitive(self, mod):
        sched = mod.get_declass_schedule("us")
        assert sched is not None


class TestPortionMarkingsData:
    def test_us_has_prefix_convention(self, mod):
        assert mod.PORTION_MARKINGS["US"]["prefix"] is True

    def test_us_examples_exist(self, mod):
        examples = mod.PORTION_MARKINGS["US"]["examples"]
        assert len(examples) > 0

    def test_eu_bilingual_format(self, mod):
        assert "UE" in mod.PORTION_MARKINGS["EU"]["convention"]


class TestListFunctions:
    def test_list_systems(self, mod):
        systems = mod.list_systems()
        assert "US" in systems
        assert "UK" in systems
        assert "NATO" in systems

    def test_list_caveats_all(self, mod):
        caveats = mod.list_caveats()
        assert len(caveats) > 0
        assert "NOFORN" in caveats

    def test_list_caveats_us(self, mod):
        caveats = mod.list_caveats(system="US")
        assert "SI" in caveats
        assert "NOFORN" in caveats

    def test_list_caveats_nato(self, mod):
        caveats = mod.list_caveats(system="NATO")
        assert "ATOMAL" in caveats
        assert "COSMIC" in caveats

    def test_list_caveats_uk(self, mod):
        caveats = mod.list_caveats(system="UK")
        assert "STRAP" in caveats
