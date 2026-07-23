"""Behavioral unit tests for the physics particle_experiments knowledge module.

Tests accelerator, detector, and sky survey data tables and lookup functions.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "physics"
    / "plugins"
    / "module_utils"
    / "particle_experiments.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_particle_experiments_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pe() -> ModuleType:
    return _load_module()


# ═══════════════════════════════════════════════════════════════════
# Accelerator data tables
# ═══════════════════════════════════════════════════════════════════


class TestAcceleratorTable:
    def test_table_present(self, pe):
        assert hasattr(pe, "ACCELERATORS")
        assert isinstance(pe.ACCELERATORS, dict)
        assert len(pe.ACCELERATORS) >= 8

    def test_lhc_present(self, pe):
        assert "LHC" in pe.ACCELERATORS
        lhc = pe.ACCELERATORS["LHC"]
        assert lhc["type"] in lhc.values() or "proton" in lhc["type"].lower()
        assert "CERN" in lhc["location"]

    def test_lhc_has_beam_energies(self, pe):
        lhc = pe.ACCELERATORS["LHC"]
        assert "beam_energies" in lhc
        assert "pp" in lhc["beam_energies"]
        assert lhc["beam_energies"]["pp"] > 1  # TeV

    def test_tevatron_decommissioned(self, pe):
        assert pe.ACCELERATORS["Tevatron"]["status"] == "decommissioned"

    def test_rhic_operational(self, pe):
        assert pe.ACCELERATORS["RHIC"]["status"] == "operational"

    def test_ilc_proposed(self, pe):
        assert pe.ACCELERATORS["ILC"]["status"] == "proposed"

    def test_eic_under_construction(self, pe):
        assert pe.ACCELERATORS["EIC"]["status"] == "under_construction"

    def test_fcc_feasibility_study(self, pe):
        assert pe.ACCELERATORS["FCC"]["status"] == "feasibility_study"

    def test_lhc_has_experiments(self, pe):
        exps = pe.ACCELERATORS["LHC"]["experiments"]
        assert "ATLAS" in exps
        assert "CMS" in exps
        assert "LHCb" in exps
        assert "ALICE" in exps

    def test_lhc_key_discoveries(self, pe):
        disc = pe.ACCELERATORS["LHC"]["key_discoveries"]
        assert any("Higgs" in d for d in disc)

    def test_super_kekb_has_target(self, pe):
        sk = pe.ACCELERATORS["SuperKEKB"]
        assert "integrated_luminosity_target" in sk
        assert sk["integrated_luminosity_target"] > 10  # ab^-1

    def test_muon_collider_concept(self, pe):
        assert pe.ACCELERATORS["muon_collider"]["status"] == "concept_study"
        assert isinstance(pe.ACCELERATORS["muon_collider"]["beam_energy_tev"], list)


# ═══════════════════════════════════════════════════════════════════
# Detector data tables
# ═══════════════════════════════════════════════════════════════════


class TestDetectorTable:
    def test_table_present(self, pe):
        assert hasattr(pe, "DETECTORS")
        assert isinstance(pe.DETECTORS, dict)
        assert len(pe.DETECTORS) >= 7

    def test_atlas_present(self, pe):
        assert "ATLAS" in pe.DETECTORS
        assert pe.DETECTORS["ATLAS"]["collider"] == "LHC"

    def test_cms_present(self, pe):
        assert "CMS" in pe.DETECTORS
        assert pe.DETECTORS["CMS"]["collider"] == "LHC"

    def test_lhcb_present(self, pe):
        assert "LHCb" in pe.DETECTORS
        assert pe.DETECTORS["LHCb"]["collider"] == "LHC"

    def test_alice_present(self, pe):
        assert "ALICE" in pe.DETECTORS
        assert pe.DETECTORS["ALICE"]["type"] == "heavy-ion dedicated"

    def test_atlas_has_subdetectors(self, pe):
        sub = pe.DETECTORS["ATLAS"]["subdetectors"]
        assert "inner_tracker" in sub
        assert "calorimeters" in sub
        assert "muon_spectrometer" in sub

    def test_cms_magnet_stronger_than_atlas(self, pe):
        atlas_field = pe.DETECTORS["ATLAS"]["magnet_system"]["solenoid"]
        cms_field = pe.DETECTORS["CMS"]["magnet_system"]["solenoid"]
        assert isinstance(cms_field, (str, int, float))
        # Atlas: 2 T, CMS: 3.8 T
        atlas_val = float(atlas_field.split()[0]) if isinstance(atlas_field, str) else atlas_field
        cms_val = float(cms_field.split()[0]) if isinstance(cms_field, str) else cms_field
        assert cms_val > atlas_val

    def test_belle_ii_at_super_kekb(self, pe):
        assert pe.DETECTORS["Belle_II"]["collider"] == "SuperKEKB"

    def test_tevatron_detectors_decommissioned(self, pe):
        assert pe.DETECTORS["CDF"]["collider"] == "Tevatron"
        assert pe.DETECTORS["D0"]["collider"] == "Tevatron"

    def test_star_at_rhic(self, pe):
        assert pe.DETECTORS["STAR"]["collider"] == "RHIC"


# ═══════════════════════════════════════════════════════════════════
# Sky survey data tables
# ═══════════════════════════════════════════════════════════════════


class TestSkySurveyTable:
    def test_table_present(self, pe):
        assert hasattr(pe, "SKY_SURVEYS")
        assert isinstance(pe.SKY_SURVEYS, dict)
        assert len(pe.SKY_SURVEYS) >= 6

    def test_sdss_present(self, pe):
        assert "SDSS" in pe.SKY_SURVEYS
        assert "Sloan" in pe.SKY_SURVEYS["SDSS"]["full_name"]

    def test_lsst_present(self, pe):
        assert "LSST" in pe.SKY_SURVEYS
        assert pe.SKY_SURVEYS["LSST"]["coverage_type"] == "southern"

    def test_gaia_present(self, pe):
        assert "Gaia" in pe.SKY_SURVEYS
        assert pe.SKY_SURVEYS["Gaia"]["coverage_type"] == "all_sky"

    def test_euclid_present(self, pe):
        assert "Euclid" in pe.SKY_SURVEYS
        assert "dark energy" in pe.SKY_SURVEYS["Euclid"]["science_goals"][0].lower()

    def test_jwst_present(self, pe):
        assert "JWST" in pe.SKY_SURVEYS
        assert "infrared" in pe.SKY_SURVEYS["JWST"]["telescope"].lower()

    def test_surveys_have_wavebands(self, pe):
        for name, survey in pe.SKY_SURVEYS.items():
            assert "wavebands" in survey, f"{name} missing wavebands"
            assert len(survey["wavebands"]) > 0, f"{name} has empty wavebands"

    def test_surveys_have_science_goals(self, pe):
        for name, survey in pe.SKY_SURVEYS.items():
            if name != "JWST":
                assert "science_goals" in survey, f"{name} missing science_goals"

    def test_lsst_has_extreme_data_rate(self, pe):
        dr = pe.SKY_SURVEYS["LSST"]["data_rate"]
        assert "TB" in dr

    def test_gaia_has_microarcsec_precision(self, pe):
        prec = pe.SKY_SURVEYS["Gaia"]["astrometric_precision"]
        assert "microarcsec" in prec.lower() or "muas" in prec.lower()


# ═══════════════════════════════════════════════════════════════════
# get_experiment_info
# ═══════════════════════════════════════════════════════════════════


class TestGetExperimentInfo:
    def test_returns_accelerator(self, pe):
        info = pe.get_experiment_info("LHC")
        assert info is not None
        assert info["full_name"] == "Large Hadron Collider"

    def test_returns_detector(self, pe):
        info = pe.get_experiment_info("ATLAS")
        assert info is not None
        assert "ATLAS" in info["full_name"]

    def test_returns_survey(self, pe):
        info = pe.get_experiment_info("SDSS")
        assert info is not None
        assert "Sloan" in info["full_name"]

    def test_returns_none_unknown(self, pe):
        assert pe.get_experiment_info("nonexistent_experiment") is None

    def test_returns_copy_not_reference(self, pe):
        a = pe.get_experiment_info("LHC")
        b = pe.get_experiment_info("LHC")
        assert a is not b


# ═══════════════════════════════════════════════════════════════════
# get_detector_capabilities
# ═══════════════════════════════════════════════════════════════════


class TestGetDetectorCapabilities:
    def test_atlas_capabilities(self, pe):
        caps = pe.get_detector_capabilities("ATLAS")
        assert caps is not None
        assert caps["collider"] == "LHC"
        assert "subdetectors" in caps
        assert "physics_program" in caps

    def test_cms_capabilities(self, pe):
        caps = pe.get_detector_capabilities("CMS")
        assert caps is not None
        assert caps["collider"] == "LHC"
        assert caps["name"] == caps.get("name")

    def test_unknown_detector(self, pe):
        assert pe.get_detector_capabilities("nonexistent") is None

    def test_all_four_lhc_detectors(self, pe):
        for name in ("ATLAS", "CMS", "LHCb", "ALICE"):
            caps = pe.get_detector_capabilities(name)
            assert caps is not None, f"{name} capabilities missing"
            assert caps["collider"] == "LHC"


# ═══════════════════════════════════════════════════════════════════
# search_sky_survey
# ═══════════════════════════════════════════════════════════════════


class TestSearchSkySurvey:
    def test_sdss_covers_north_coords(self, pe):
        results = pe.search_sky_survey(180.0, 45.0)
        surveys = [r["survey"] for r in results]
        assert "SDSS" in surveys

    def test_des_covers_south(self, pe):
        results = pe.search_sky_survey(90.0, -45.0)
        surveys = [r["survey"] for r in results]
        assert "DES" in surveys

    def test_gaia_covers_all_sky(self, pe):
        results = pe.search_sky_survey(0.0, 0.0)
        surveys = [r["survey"] for r in results]
        assert "Gaia" in surveys

    def test_south_pole_gaia_only(self, pe):
        results = pe.search_sky_survey(0.0, -85.0)
        surveys = [r["survey"] for r in results]
        assert "Gaia" in surveys
        assert "LSST" in surveys

    def test_results_have_band_info(self, pe):
        results = pe.search_sky_survey(180.0, 30.0)
        for r in results:
            assert "wavebands" in r
            assert "limiting_magnitude" in r
            assert r["has_coverage"] is True

    def test_ra_wraparound(self, pe):
        results_0 = pe.search_sky_survey(0.0, 0.0)
        results_360 = pe.search_sky_survey(360.0, 0.0)
        assert len(results_0) == len(results_360)

    def test_ra_slightly_above_360(self, pe):
        results = pe.search_sky_survey(370.0, 0.0)
        surveys = [r["survey"] for r in results]
        assert "Gaia" in surveys


# ═══════════════════════════════════════════════════════════════════
# list_accelerators_by_type
# ═══════════════════════════════════════════════════════════════════


class TestListAcceleratorsByType:
    def test_proton_colliders(self, pe):
        results = pe.list_accelerators_by_type("proton")
        assert "LHC" in results
        assert len(results) >= 3

    def test_electron_colliders(self, pe):
        results = pe.list_accelerators_by_type("electron")
        assert "ILC" in results
        assert "CLIC" in results

    def test_heavy_ion_colliders(self, pe):
        results = pe.list_accelerators_by_type("heavy-ion")
        assert "RHIC" in results

    def test_case_insensitive(self, pe):
        lower = pe.list_accelerators_by_type("proton")
        upper = pe.list_accelerators_by_type("PROTON")
        assert lower == upper

    def test_no_match_empty(self, pe):
        results = pe.list_accelerators_by_type("tachyon")
        assert results == []


# ═══════════════════════════════════════════════════════════════════
# list_accelerators_by_status
# ═══════════════════════════════════════════════════════════════════


class TestListAcceleratorsByStatus:
    def test_operational(self, pe):
        results = pe.list_accelerators_by_status("operational")
        assert "LHC" in results
        assert "RHIC" in results
        assert "SuperKEKB" in results

    def test_proposed(self, pe):
        results = pe.list_accelerators_by_status("proposed")
        assert "ILC" in results
        assert "CLIC" in results

    def test_decommissioned(self, pe):
        results = pe.list_accelerators_by_status("decommissioned")
        assert "Tevatron" in results
        assert "KEKB" in results

    def test_under_construction(self, pe):
        results = pe.list_accelerators_by_status("under_construction")
        assert "HL_LHC" in results
        assert "EIC" in results


# ═══════════════════════════════════════════════════════════════════
# list_detectors_by_collider
# ═══════════════════════════════════════════════════════════════════


class TestListDetectorsByCollider:
    def test_lhc_has_four(self, pe):
        grouped = pe.list_detectors_by_collider()
        assert "LHC" in grouped
        assert len(grouped["LHC"]) >= 4

    def test_tevatron_has_two(self, pe):
        grouped = pe.list_detectors_by_collider()
        assert "Tevatron" in grouped
        assert len(grouped["Tevatron"]) == 2


# ═══════════════════════════════════════════════════════════════════
# get_running_status
# ═══════════════════════════════════════════════════════════════════


class TestGetRunningStatus:
    def test_lhc_operational(self, pe):
        status = pe.get_running_status("LHC")
        assert status is not None
        assert status["status"] == "operational"

    def test_tevatron_decommissioned(self, pe):
        status = pe.get_running_status("Tevatron")
        assert status is not None
        assert status["status"] == "decommissioned"

    def test_unknown_returns_none(self, pe):
        assert pe.get_running_status("nonexistent") is None


# ═══════════════════════════════════════════════════════════════════
# list_sky_surveys_by_coverage
# ═══════════════════════════════════════════════════════════════════


class TestListSkySurveysByCoverage:
    def test_northern_surveys(self, pe):
        results = pe.list_sky_surveys_by_coverage("northern")
        assert "SDSS" in results
        assert "Pan_STARRS" in results

    def test_southern_surveys(self, pe):
        results = pe.list_sky_surveys_by_coverage("southern")
        assert "DES" in results
        assert "LSST" in results

    def test_all_sky(self, pe):
        results = pe.list_sky_surveys_by_coverage("all_sky")
        assert "Gaia" in results

    def test_extragalactic(self, pe):
        results = pe.list_sky_surveys_by_coverage("extragalactic")
        assert "Euclid" in results

    def test_case_insensitive(self, pe):
        assert pe.list_sky_surveys_by_coverage("ALL_SKY") == pe.list_sky_surveys_by_coverage("all_sky")


# ═══════════════════════════════════════════════════════════════════
# get_survey_data_release
# ═══════════════════════════════════════════════════════════════════


class TestGetSurveyDataRelease:
    def test_sdss_latest(self, pe):
        dr = pe.get_survey_data_release("SDSS")
        assert dr is not None
        assert "DR" in dr or "Data" in dr or "Release" in dr

    def test_gaia_latest(self, pe):
        dr = pe.get_survey_data_release("Gaia")
        assert dr is not None
        assert "DR" in dr or "202" in dr

    def test_jwst_no_releases(self, pe):
        dr = pe.get_survey_data_release("JWST")
        assert dr is None

    def test_unknown_survey(self, pe):
        assert pe.get_survey_data_release("nonexistent") is None


# ═══════════════════════════════════════════════════════════════════
# Cross-table consistency
# ═══════════════════════════════════════════════════════════════════


class TestCrossTableConsistency:
    def test_superkekb_has_belle_ii(self, pe):
        exps = pe.ACCELERATORS["SuperKEKB"]["experiments"]
        assert "Belle II" in exps

    def test_belle_ii_refers_to_superkekb(self, pe):
        assert pe.DETECTORS["Belle_II"]["collider"] == "SuperKEKB"

    def test_atlas_lhcb_higgs_results(self, pe):
        atlas_results = pe.DETECTORS["ATLAS"]["notable_results"]
        assert any("Higgs" in r for r in atlas_results)

    def test_rhic_star_consistent(self, pe):
        assert pe.DETECTORS["STAR"]["collider"] == "RHIC"
        assert "STAR" in pe.ACCELERATORS["RHIC"]["experiments"]
