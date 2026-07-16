"""Behavioral unit tests for the physics astronomy knowledge module."""

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
    / "astronomy.py"
)

MODULE_NAME = "_astronomy_under_test"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def astro() -> ModuleType:
    return _load_module()


class TestConstants:
    def test_G_defined(self, astro):
        assert hasattr(astro, "G")
        assert isinstance(astro.G, float)
        assert astro.G > 0

    def test_c_defined(self, astro):
        assert hasattr(astro, "C_LIGHT")
        assert isinstance(astro.C_LIGHT, float)
        assert math.isclose(astro.C_LIGHT, 299792458.0)

    def test_solar_constants(self, astro):
        assert hasattr(astro, "M_SUN")
        assert hasattr(astro, "R_SUN")
        assert hasattr(astro, "L_SUN")
        assert astro.M_SUN > 0
        assert astro.R_SUN > 0
        assert astro.L_SUN > 0

    def test_parsec(self, astro):
        assert hasattr(astro, "PARSEC")
        assert astro.PARSEC > 1e15

    def test_H0_defined(self, astro):
        assert hasattr(astro, "H0")
        assert astro.H0 > 0


class TestDataTables:
    def test_solar_system_bodies(self, astro):
        body_names = ["mercury", "venus", "earth", "mars", "jupiter", "saturn", "uranus", "neptune"]
        for name in body_names:
            assert name in astro.SOLAR_SYSTEM_BODIES
            body = astro.SOLAR_SYSTEM_BODIES[name]
            assert "semi_major_axis_AU" in body
            assert "eccentricity" in body
            assert "mass_kg" in body

    def test_spectral_classes(self, astro):
        classes = ["O", "B", "A", "F", "G", "K", "M", "L", "T", "Y"]
        found = [s["class"] for s in astro.SPECTRAL_CLASSES]
        for c in classes:
            assert c in found, f"missing spectral class {c}"
        for sc in astro.SPECTRAL_CLASSES:
            assert "temp_k" in sc
            assert "color" in sc
            assert "mass_msun" in sc

    def test_stellar_evolution_stages(self, astro):
        stages = [s["stage"] for s in astro.STELLAR_EVOLUTION_STAGES]
        assert "protostar" in stages
        assert "main_sequence" in stages
        assert "white_dwarf" in stages
        assert "neutron_star" in stages
        assert "black_hole" in stages
        assert "supernova_II" in stages

    def test_hubble_sequence(self, astro):
        types_found = [g["type"] for g in astro.HUBBLE_SEQUENCE]
        assert "E0" in types_found
        assert "E7" in types_found
        assert "S0" in types_found
        assert "Sa" in types_found
        assert "Irr" in types_found

    def test_dark_matter_evidence(self, astro):
        observations = [d["observation"] for d in astro.DARK_MATTER_EVIDENCE]
        assert "galaxy_rotation_curves" in observations
        assert "gravitational_lensing" in observations
        assert "cmb_anisotropies" in observations

    def test_lambda_cdm_params(self, astro):
        params = astro.LAMBDA_CDM_PARAMETERS
        assert "Omega_m" in params
        assert "Omega_Lambda" in params
        omega_total = params["Omega_m"] + params["Omega_Lambda"]
        assert 0.95 < omega_total < 1.05

    def test_cmb_data(self, astro):
        cmb = astro.COSMIC_MICROWAVE_BACKGROUND
        assert "temperature_k" in cmb
        assert math.isclose(cmb["temperature_k"], 2.72548, rel_tol=0.01)
        assert "redshift_decoupling" in cmb

    def test_exoplanet_methods(self, astro):
        methods = [m["method"] for m in astro.EXOPLANET_DETECTION_METHODS]
        assert "transit" in methods
        assert "radial_velocity" in methods
        assert "direct_imaging" in methods
        assert "microlensing" in methods

    def test_observatories(self, astro):
        names = [o["name"] for o in astro.OBSERVATORIES]
        assert "HST" in names
        assert "JWST" in names
        assert "Chandra" in names
        assert "ALMA" in names
        assert "Keck" in names
        assert "LIGO" in names

    def test_inflationary_models(self, astro):
        models = [m["model"] for m in astro.INFLATIONARY_MODELS]
        assert "slow_roll" in models
        assert "Starobinsky_R2" in models

    def test_nucleosynthesis(self, astro):
        processes = [p["process"] for p in astro.NUCLEOSYNTHESIS_PROCESSES]
        assert "big_bang" in processes
        assert "pp_chain" in processes
        assert "r_process" in processes

    def test_habitable_zone_factors(self, astro):
        factors = [f["factor"] for f in astro.HABITABLE_ZONE_FACTORS]
        assert "stellar_flux" in factors
        assert "greenhouse_effect" in factors
        assert "tidal_locking" in factors


class TestOrbitalMechanics:
    def test_compute_orbital_period_earth(self, astro):
        a = astro.AU
        T = astro.compute_orbital_period(a, astro.M_SUN)
        assert 3.1e7 < T < 3.2e7

    def test_compute_orbital_period_scaling(self, astro):
        T1 = astro.compute_orbital_period(1.0, 1.0)
        T2 = astro.compute_orbital_period(8.0, 1.0)
        assert T2 > T1

    def test_compute_vis_viva_circular(self, astro):
        a = astro.AU
        v = astro.compute_vis_viva(a, a, astro.M_SUN)
        assert 28000 < v < 31000

    def test_compute_escape_velocity_earth(self, astro):
        body = astro.SOLAR_SYSTEM_BODIES["earth"]
        r = body["radius_km"] * 1000
        v = astro.compute_escape_velocity(r, body["mass_kg"])
        assert 10000 < v < 12000

    def test_compute_orbital_elements_earth(self, astro):
        result = astro.compute_orbital_elements("earth")
        assert result is not None
        assert result["body"] == "earth"
        assert result["eccentricity"] == 0.0167
        assert result["orbital_period_yr"] == 1.000

    def test_compute_orbital_elements_jupiter(self, astro):
        result = astro.compute_orbital_elements("jupiter")
        assert result is not None
        assert result["body"] == "jupiter"
        assert 11 < result["orbital_period_yr"] < 12

    def test_compute_orbital_elements_unknown(self, astro):
        result = astro.compute_orbital_elements("planet_nine")
        assert result is None

    def test_compute_hill_sphere(self, astro):
        r_h = astro.compute_hill_sphere(astro.AU, 5.972e24, astro.M_SUN)
        assert r_h > 1e8

    def test_compute_roche_limit(self, astro):
        r_earth = 6371000.0
        d = astro.compute_roche_limit(r_earth, 5513.0, 1000.0)
        assert d > r_earth

    def test_compute_orbital_energy(self, astro):
        e = astro.compute_orbital_energy(astro.AU, astro.M_SUN, 5.972e24)
        assert e < 0


class TestStellarPhysics:
    def test_classify_star_g_type(self, astro):
        result = astro.classify_star("G2V")
        assert result is not None
        assert result["class"] == "G"
        assert result["color"] == "yellow"

    def test_classify_star_o_type(self, astro):
        result = astro.classify_star("O5")
        assert result is not None
        assert result["class"] == "O"
        assert result["temp_k"] == 40000.0

    def test_classify_star_unknown(self, astro):
        result = astro.classify_star("X1")
        assert result is None

    def test_compute_luminosity_from_mass(self, astro):
        L = astro.compute_luminosity_from_mass(10.0)
        assert L > 1000

    def test_compute_main_sequence_lifetime_sun(self, astro):
        t = astro.compute_main_sequence_lifetime(1.0)
        assert 9e9 < t < 1.1e10

    def test_compute_main_sequence_lifetime_massive(self, astro):
        t = astro.compute_main_sequence_lifetime(10.0)
        assert t < 1e8

    def test_compute_schwarzschild_radius(self, astro):
        rs = astro.compute_schwarzschild_radius(astro.M_SUN)
        assert 2500 < rs < 3500

    def test_compute_effective_temperature(self, astro):
        T = astro.compute_effective_temperature(astro.L_SUN, astro.R_SUN)
        assert 5000 < T < 6000


class TestCosmology:
    def test_compute_redshift_positive(self, astro):
        z = astro.compute_redshift(656.3, 656.3)
        assert z == 0.0
        z2 = astro.compute_redshift(1312.6, 656.3)
        assert z2 == 1.0

    def test_compute_redshift_negative(self, astro):
        z = astro.compute_redshift(500.0, 656.3)
        assert z < 0

    def test_compute_recession_velocity(self, astro):
        v = astro.compute_recession_velocity(0.1)
        assert v > 0
        assert v < astro.C_LIGHT

    def test_compute_hubble_distance(self, astro):
        d = astro.compute_hubble_distance(0.1)
        assert d > 0

    def test_compute_lookback_time(self, astro):
        t = astro.compute_lookback_time(1.0)
        assert t > 1e9

    def test_compute_comoving_distance(self, astro):
        d = astro.compute_comoving_distance(0.5)
        assert d > 0

    def test_compute_angular_diameter_distance(self, astro):
        d_a = astro.compute_angular_diameter_distance(1.0)
        d_c = astro.compute_comoving_distance(1.0)
        assert math.isclose(d_a, d_c / 2.0, rel_tol=0.01)

    def test_compute_luminosity_distance(self, astro):
        d_l = astro.compute_luminosity_distance(1.0)
        d_c = astro.compute_comoving_distance(1.0)
        assert d_l > d_c

    def test_friedmann_density_params(self, astro):
        result = astro.compute_friedmann_density_parameters()
        assert "Omega_k" in result
        assert result["Omega_k"] < 0.01
        assert result["Omega_total"] > 0.99


class TestExoplanetsAndTelescopes:
    def test_compute_transit_depth(self, astro):
        depth = astro.compute_transit_depth(astro.R_SUN * 0.1, astro.R_SUN)
        assert math.isclose(depth, 0.01)

    def test_compute_radial_velocity_amplitude(self, astro):
        m_planet = 1.898e27
        period = 11.86 * astro.YEAR_SEC
        K = astro.compute_radial_velocity_semi_amplitude(m_planet, astro.M_SUN, period, 90.0)
        assert K > 0

    def test_compute_habitable_zone(self, astro):
        hz = astro.compute_habitable_zone_boundaries(1.0)
        assert hz["inner_AU"] < hz["outer_AU"]
        assert 0.9 < hz["inner_AU"] < 1.1

    def test_compute_equilibrium_temperature(self, astro):
        T = astro.compute_equilibrium_temperature(astro.L_SUN, astro.AU)
        assert 200 < T < 300

    def test_compute_angular_resolution(self, astro):
        theta = astro.compute_angular_resolution(550e-9, 2.4)
        assert theta > 2e-7

    def test_compute_light_gathering_power(self, astro):
        lgp = astro.compute_light_gathering_power(10.0)
        assert lgp > 1e6

    def test_compute_magnitude_limit(self, astro):
        m_lim = astro.compute_magnitude_limit(10.0, 3600.0)
        assert m_lim > 20


class TestHelpers:
    def test_solar_system_body_list(self, astro):
        bodies = astro.get_solar_system_body_list()
        assert "earth" in bodies
        assert len(bodies) == 8

    def test_spectral_class_list(self, astro):
        classes = astro.get_spectral_class_list()
        assert "O" in classes
        assert "M" in classes
        assert len(classes) == 10

    def test_get_observatory_jwst(self, astro):
        obs = astro.get_observatory("JWST")
        assert obs is not None
        assert obs["name"] == "JWST"
        assert obs["aperture_m"] == 6.5

    def test_get_observatory_unknown(self, astro):
        obs = astro.get_observatory("non_existent")
        assert obs is None

    def test_get_all_observatories(self, astro):
        names = astro.get_all_observatories()
        assert "HST" in names
        assert "JWST" in names
        assert len(names) == 10
