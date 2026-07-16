"""Behavioral unit tests for the physics astrophysics knowledge module."""

from __future__ import annotations

import importlib.util
import math
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
    / "physics"
    / "plugins"
    / "module_utils"
    / "astrophysics.py"
)

MODULE_NAME = "_astrophysics_under_test"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ap() -> ModuleType:
    return _load_module()


class TestConstants:
    def test_G_defined(self, ap):
        assert hasattr(ap, "G")
        assert ap.G > 0

    def test_c_light(self, ap):
        assert math.isclose(ap.C_LIGHT, 299792458.0)

    def test_solar_mass(self, ap):
        assert ap.M_SUN > 1e30
        assert ap.MSUN_KG == ap.M_SUN

    def test_planck(self, ap):
        assert ap.H_PLANCK > 0
        assert ap.K_B > 0

    def test_derived_units(self, ap):
        assert ap.YEAR_SEC > 3e7
        assert ap.PARSEC > 3e15


class TestCompactObjects:
    def test_data_table_present(self, ap):
        assert hasattr(ap, "COMPACT_OBJECTS")
        assert isinstance(ap.COMPACT_OBJECTS, list)
        assert len(ap.COMPACT_OBJECTS) >= 5

    def test_white_dwarf(self, ap):
        wd = ap.get_compact_object("white_dwarf")
        assert wd is not None
        assert "electron degeneracy" in wd["support"]
        assert "Sirius B" in wd["examples"]

    def test_neutron_star(self, ap):
        ns = ap.get_compact_object("neutron_star")
        assert ns is not None
        assert 1.4 <= float(str(ns["mass_range_msun"]).split("-")[0].strip()) <= 2.0

    def test_magnetar(self, ap):
        mg = ap.get_compact_object("magnetar")
        assert mg is not None
        assert mg["magnetic_field_G"] == 1e15

    def test_stellar_black_hole(self, ap):
        bh = ap.get_compact_object("stellar_black_hole")
        assert bh is not None
        assert "Cygnus X-1" in str(bh["examples"])

    def test_supermassive_black_hole(self, ap):
        smbh = ap.get_compact_object("supermassive_black_hole")
        assert smbh is not None
        assert "Sgr A*" in str(smbh["examples"])


class TestGRBAndAGN:
    def test_grb_data_present(self, ap):
        assert hasattr(ap, "GAMMA_RAY_BURSTS")
        assert len(ap.GAMMA_RAY_BURSTS) >= 3

    def test_short_grb(self, ap):
        grb = ap.get_grb_class("short")
        assert grb is not None
        assert grb["duration_s"] == "< 2"

    def test_long_grb(self, ap):
        grb = ap.get_grb_class("long")
        assert grb is not None
        assert grb["duration_s"] == "> 2"

    def test_agn_data_present(self, ap):
        assert hasattr(ap, "ACTIVE_GALACTIC_NUCLEI")
        assert len(ap.ACTIVE_GALACTIC_NUCLEI) >= 4

    def test_agn_types(self, ap):
        types = [a["type"] for a in ap.ACTIVE_GALACTIC_NUCLEI]
        assert "quasar" in types
        assert "blazar" in types
        assert "seyfert_1" in types
        assert "seyfert_2" in types

    def test_supernova_data(self, ap):
        assert hasattr(ap, "SUPERNOVA_TYPES")
        sn_types = [s["type"] for s in ap.SUPERNOVA_TYPES]
        assert "Ia" in sn_types
        assert "II-P" in sn_types
        assert "Ib" in sn_types
        assert "Ic" in sn_types


class TestGravitationalWaves:
    def test_gw_sources(self, ap):
        assert hasattr(ap, "GRAVITATIONAL_WAVE_SOURCES")
        assert len(ap.GRAVITATIONAL_WAVE_SOURCES) >= 6

    def test_binary_black_hole(self, ap):
        bbh = ap.get_gw_source("binary_black_hole")
        assert bbh is not None
        assert "GW150914" in str(bbh["examples"])

    def test_binary_neutron_star(self, ap):
        bns = ap.get_gw_source("binary_neutron_star")
        assert bns is not None
        assert "GW170817" in str(bns["examples"])

    def test_stochastic_background(self, ap):
        sgwb = ap.get_gw_source("stochastic_background")
        assert sgwb is not None
        assert "NANOGrav" in str(sgwb["examples"])


class TestGWFormulae:
    def test_compute_schwarzschild_radius(self, ap):
        rs = ap.compute_schwarzschild_radius(ap.M_SUN)
        assert 2000 < rs < 4000

    def test_isco(self, ap):
        r_isco = ap.compute_innermost_stable_circular_orbit(ap.M_SUN)
        rs = ap.compute_schwarzschild_radius(ap.M_SUN)
        assert math.isclose(r_isco, 3.0 * rs)

    def test_photon_sphere(self, ap):
        r_ph = ap.compute_photon_sphere_radius(ap.M_SUN)
        rs = ap.compute_schwarzschild_radius(ap.M_SUN)
        assert math.isclose(r_ph, 1.5 * rs)

    def test_chirp_mass(self, ap):
        m1 = 30 * ap.M_SUN
        m2 = 30 * ap.M_SUN
        chirp = ap.compute_gravitational_wave_chirp_mass(m1, m2)
        assert chirp > 0

    def test_gw_strain(self, ap):
        mass = 30 * ap.M_SUN
        distance = 400 * 1e6 * ap.PARSEC
        strain = ap.compute_gravitational_wave_strain(mass, distance, 100.0)
        assert strain > 0

    def test_compute_gw_strain_increases_with_frequency(self, ap):
        mass = 30 * ap.M_SUN
        distance = 400 * 1e6 * ap.PARSEC
        s1 = ap.compute_gravitational_wave_strain(mass, distance, 50.0)
        s2 = ap.compute_gravitational_wave_strain(mass, distance, 100.0)
        assert s2 > s1


class TestHighEnergy:
    def test_eddington_luminosity(self, ap):
        L = ap.compute_eddington_luminosity(ap.M_SUN)
        assert L > 1e31

    def test_chandrasekhar_mass(self, ap):
        m_ch = ap.compute_chandrasekhar_mass()
        assert 1.3 * ap.M_SUN < m_ch < 1.5 * ap.M_SUN

    def test_chandrasekhar_mass_mu_e_effect(self, ap):
        m1 = ap.compute_chandrasekhar_mass(mu_e=2.0)
        m2 = ap.compute_chandrasekhar_mass(mu_e=3.0)
        assert m1 > m2

    def test_tov_limit(self, ap):
        tov = ap.compute_tov_limit()
        assert "mass_msun" in tov
        assert tov["mass_msun"] > 1.9

    def test_accretion_luminosity(self, ap):
        L = ap.compute_accretion_luminosity(1e14, 0.1)
        assert L > 8e29

    def test_bondi_accretion(self, ap):
        rate = ap.compute_bondi_accretion_rate(ap.M_SUN, 1e-15, 1e5)
        assert rate > 0


class TestPulsarAndSynchrotron:
    def test_spin_down_luminosity(self, ap):
        moment_of_inertia = 1e38
        P = 0.033
        Pdot = 4.2e-13
        L = ap.compute_spin_down_luminosity(moment_of_inertia, P, Pdot)
        assert L > 1e30

    def test_magnetic_dipole_braking(self, ap):
        n = ap.compute_magnetic_dipole_braking_index(0.033, 4.2e-13)
        assert n == 3.0

    def test_crab_pulsar(self, ap):
        crab = ap.compute_crab_pulsar_parameters()
        assert crab["period_ms"] == 33.0
        assert crab["age_yr"] == 960

    def test_synchrotron_frequency(self, ap):
        B = 1e-4
        E = 1e-10
        nu = ap.compute_synchrotron_critical_frequency(B, E)
        assert nu > 0

    def test_inverse_compton(self, ap):
        E_ic = ap.compute_ic_energy(1e-19, 1e-11)
        assert E_ic > 1e-19


class TestAstroparticle:
    def test_cosmic_ray_data(self, ap):
        assert hasattr(ap, "COSMIC_RAYS")
        components = [c["component"] for c in ap.COSMIC_RAYS]
        assert "galactic_cosmic_rays" in components
        assert "ultra_high_energy" in components

    def test_neutrino_data(self, ap):
        assert hasattr(ap, "NEUTRINO_ASTROPHYSICS")
        sources = [n["source"] for n in ap.NEUTRINO_ASTROPHYSICS]
        assert "solar_neutrinos" in sources
        assert "high_energy_astrophysical" in sources

    def test_detectors(self, ap):
        assert hasattr(ap, "ASTROPARTICLE_DETECTORS")
        names = [d["name"] for d in ap.ASTROPARTICLE_DETECTORS]
        assert "IceCube" in names
        assert "Pierre_Auger" in names
        assert "Fermi_LAT" in names
        assert "LIGO" in names

    def test_neutrino_cross_section(self, ap):
        sigma = ap.compute_neutrino_cross_section_approx(1.0)
        assert sigma > 1e-44

    def test_gzk_threshold(self, ap):
        gzk = ap.compute_greisen_zatsepin_kuzmin_threshold()
        assert gzk["energy_threshold_eV"] == 5e19


class TestHelpers:
    def test_list_compact_objects(self, ap):
        objs = ap.list_compact_objects()
        assert "white_dwarf" in objs
        assert "neutron_star" in objs
        assert len(objs) == 5

    def test_list_grb_classes(self, ap):
        classes = ap.list_grb_classes()
        assert "short" in classes
        assert "long" in classes

    def test_list_gw_sources(self, ap):
        sources = ap.list_gw_sources()
        assert "binary_black_hole" in sources
        assert "binary_neutron_star" in sources

    def test_get_compact_object_unknown(self, ap):
        assert ap.get_compact_object("warp_drive") is None

    def test_get_grb_class_unknown(self, ap):
        assert ap.get_grb_class("medium") is None

    def test_get_gw_source_unknown(self, ap):
        assert ap.get_gw_source("alien_signal") is None

    def test_agn_unified_model(self, ap):
        model = ap.compute_agn_unified_model()
        assert "components" in model
        assert "viewing_angle_dependence" in model
        assert "supermassive black hole" in str(model["components"])
