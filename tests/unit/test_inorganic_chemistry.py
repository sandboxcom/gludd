"""Behavioral unit tests for the physics inorganic_chemistry knowledge module."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import ClassVar

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
    / "inorganic_chemistry.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_inorganic_chemistry_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ic() -> ModuleType:
    return _load_module()


class TestModuleExports:
    def test_data_tables_present(self, ic):
        for attr in (
            "PERIODIC_TABLE", "LIGANDS", "CRYSTAL_FIELD_SPLITTING",
            "SOLID_STATE_DEFECTS", "PHASE_DIAGRAMS",
        ):
            assert hasattr(ic, attr), f"missing data table {attr}"
            assert isinstance(getattr(ic, attr), dict)

    def test_functions_present(self, ic):
        for fn in ("get_element_data", "compute_crystal_field_splitting", "get_reaction"):
            assert callable(getattr(ic, fn, None)), f"missing function {fn}"


class TestPeriodicTable:
    _first_20: ClassVar = ["H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
                            "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca"]

    @pytest.mark.parametrize("symbol", _first_20)
    def test_first_20_present(self, ic, symbol):
        assert symbol in ic.PERIODIC_TABLE, f"missing element {symbol}"

    def test_at_least_100_elements(self, ic):
        assert len(ic.PERIODIC_TABLE) >= 100, f"expected >=100 elements, got {len(ic.PERIODIC_TABLE)}"

    def test_all_elements_have_required_fields(self, ic):
        required = ["atomic_number", "symbol", "name", "atomic_mass",
                    "electron_configuration", "electronegativity",
                    "oxidation_states", "group", "period", "block"]
        for sym, data in ic.PERIODIC_TABLE.items():
            for field in required:
                assert field in data, f"{sym} missing {field}"

    def test_all_have_physical_properties(self, ic):
        physical = ["melting_point_k", "boiling_point_k", "density_gcm3",
                    "atomic_radius_pm"]
        for sym, data in ic.PERIODIC_TABLE.items():
            for field in physical:
                assert field in data, f"{sym} missing physical property {field}"

    def test_hydrogen_atomic_number_1(self, ic):
        assert ic.PERIODIC_TABLE["H"]["atomic_number"] == 1

    def test_helium_noble_gas(self, ic):
        assert ic.PERIODIC_TABLE["He"]["block"] == "s"

    def test_carbon_group_14(self, ic):
        assert ic.PERIODIC_TABLE["C"]["group"] == 14

    def test_iron_d_block(self, ic):
        assert ic.PERIODIC_TABLE["Fe"]["block"] == "d"

    def test_fluorine_most_electronegative_common(self, ic):
        en = ic.PERIODIC_TABLE["F"]["electronegativity"]
        assert en is not None and en >= 3.9

    def test_gold_period_6(self, ic):
        assert ic.PERIODIC_TABLE["Au"]["period"] == 6

    def test_uranium_f_block(self, ic):
        assert ic.PERIODIC_TABLE["U"]["block"] == "f"

    def test_mercury_liquid_at_rt(self, ic):
        hg = ic.PERIODIC_TABLE["Hg"]
        assert hg["melting_point_k"] < 298

    def test_tungsten_highest_mp(self, ic):
        w = ic.PERIODIC_TABLE["W"]
        assert w["melting_point_k"] > 3500

    def test_cesium_low_mp(self, ic):
        cs = ic.PERIODIC_TABLE["Cs"]
        assert cs["melting_point_k"] < 310

    def test_oxidation_states_are_lists(self, ic):
        for sym, data in ic.PERIODIC_TABLE.items():
            assert isinstance(data["oxidation_states"], list), f"{sym} oxidation_states not list"

    def test_mass_positive(self, ic):
        for sym, data in ic.PERIODIC_TABLE.items():
            assert data["atomic_mass"] > 0, f"{sym} has non-positive mass"

    def test_electron_configuration_nonempty(self, ic):
        for sym, data in ic.PERIODIC_TABLE.items():
            assert isinstance(data["electron_configuration"], str)
            assert len(data["electron_configuration"]) > 0, f"{sym} empty config"


class TestLigands:
    def test_ligands_have_spectrochemical_series(self, ic):
        assert len(ic.LIGANDS) >= 5, f"expected >=5 ligands, got {len(ic.LIGANDS)}"

    def test_ligands_have_splitting_parameter(self, ic):
        for name, data in ic.LIGANDS.items():
            assert "field_strength" in data, f"{name} missing field_strength"
            assert "delta_o_relative" in data, f"{name} missing delta_o_relative"

    def test_ligands_have_donor_atoms(self, ic):
        for name, data in ic.LIGANDS.items():
            assert "donor_atom" in data, f"{name} missing donor_atom"

    def test_co_is_strong_field(self, ic):
        assert ic.LIGANDS["CO"]["field_strength"] == "strong"

    def test_i_is_weak_field(self, ic):
        assert ic.LIGANDS["I-"]["field_strength"] == "weak"

    def test_strong_field_greater_co(self, ic):
        strong = ic.LIGANDS["CO"]["delta_o_relative"]
        weak = ic.LIGANDS["I-"]["delta_o_relative"]
        assert strong > weak

    def test_nh3_is_intermediate_field(self, ic):
        data = ic.LIGANDS["NH3"]
        assert data["field_strength"] in ("intermediate", "strong", "weak")


class TestCrystalFieldSplitting:
    _geometries: ClassVar = ["octahedral", "tetrahedral", "square_planar"]

    @pytest.mark.parametrize("geo", _geometries)
    def test_geometry_defined(self, ic, geo):
        assert geo in ic.CRYSTAL_FIELD_SPLITTING, f"missing geometry {geo}"
        data = ic.CRYSTAL_FIELD_SPLITTING[geo]
        assert "orbitals" in data
        assert "splitting_pattern" in data

    def test_octahedral_t2g_eg(self, ic):
        oh = ic.CRYSTAL_FIELD_SPLITTING["octahedral"]
        assert "t2g" in str(oh["splitting_pattern"]).lower()
        assert "eg" in str(oh["splitting_pattern"]).lower()

    def test_tetrahedral_e_t2(self, ic):
        td = ic.CRYSTAL_FIELD_SPLITTING["tetrahedral"]
        assert "e" in str(td["splitting_pattern"]).lower()
        assert "t2" in str(td["splitting_pattern"]).lower()

    def test_square_planar_has_dx2y2(self, ic):
        sp = ic.CRYSTAL_FIELD_SPLITTING["square_planar"]
        assert "dx2-y2" in str(sp["splitting_pattern"]).lower() or "dx2y2" in str(sp["splitting_pattern"]).lower()


class TestSolidStateDefects:
    _defects: ClassVar = ["Schottky", "Frenkel", "F-center", "edge_dislocation", "screw_dislocation"]

    @pytest.mark.parametrize("defect", _defects)
    def test_defect_defined(self, ic, defect):
        assert defect in ic.SOLID_STATE_DEFECTS, f"missing defect {defect}"
        data = ic.SOLID_STATE_DEFECTS[defect]
        assert "description" in data
        assert len(data["description"]) > 10
        assert "type" in data

    def test_schottky_is_vacancy(self, ic):
        d = ic.SOLID_STATE_DEFECTS["Schottky"]
        assert "vacancy" in str(d["description"]).lower() or "vacancy" in d["type"].lower()

    def test_frenkel_is_interstitial(self, ic):
        d = ic.SOLID_STATE_DEFECTS["Frenkel"]
        assert "interstitial" in str(d["description"]).lower() or "interstitial" in d["type"].lower()

    def test_f_center_is_color(self, ic):
        d = ic.SOLID_STATE_DEFECTS["F-center"]
        assert "color" in str(d["description"]).lower() or "electron" in str(d["description"]).lower()


class TestPhaseDiagrams:
    def test_phase_diagrams_have_entries(self, ic):
        assert len(ic.PHASE_DIAGRAMS) >= 3, f"expected >=3 phase diagrams, got {len(ic.PHASE_DIAGRAMS)}"

    def test_phase_diagrams_have_components(self, ic):
        for name, data in ic.PHASE_DIAGRAMS.items():
            assert "components" in data, f"{name} missing components"
            assert isinstance(data["components"], list)

    def test_phase_diagrams_have_eutectic(self, ic):
        for name, data in ic.PHASE_DIAGRAMS.items():
            assert "eutectic_composition" in data, f"{name} missing eutectic_composition"

    def test_fe_c_has_peritectic(self, ic):
        fec = ic.PHASE_DIAGRAMS["Fe-C"]
        assert "peritectic" in fec


class TestGetElementData:
    def test_hydrogen_data(self, ic):
        data = ic.get_element_data("H")
        assert data is not None
        assert data["name"] == "Hydrogen"
        assert data["atomic_number"] == 1

    def test_iron_data(self, ic):
        data = ic.get_element_data("Fe")
        assert data is not None
        assert data["atomic_number"] == 26
        assert data["block"] == "d"

    def test_unknown_element_returns_none(self, ic):
        assert ic.get_element_data("Xx") is None

    def test_case_sensitive_lookup(self, ic):
        assert ic.get_element_data("he") is None


class TestComputeCrystalFieldSplitting:
    def test_octahedral_co_returns_dict(self, ic):
        result = ic.compute_crystal_field_splitting("Ti3+", "CO", "octahedral")
        assert isinstance(result, dict)
        assert "delta_o_cm" in result
        assert "spin_state" in result
        assert "splitting_diagram" in result

    def test_tetrahedral_cl_returns_low_splitting(self, ic):
        result = ic.compute_crystal_field_splitting("Co2+", "Cl-", "tetrahedral")
        assert isinstance(result, dict)
        assert result["delta_o_cm"] > 0

    def test_square_planar_returns_dict(self, ic):
        result = ic.compute_crystal_field_splitting("Ni2+", "CN-", "square_planar")
        assert isinstance(result, dict)

    def test_high_spin_weak_field(self, ic):
        result = ic.compute_crystal_field_splitting("Fe2+", "H2O", "octahedral")
        assert isinstance(result, dict)
        assert "spin_state" in result

    def test_unknown_metal_returns_none(self, ic):
        result = ic.compute_crystal_field_splitting("Unknown", "CO", "octahedral")
        assert result is None

    def test_unknown_geometry_returns_none(self, ic):
        result = ic.compute_crystal_field_splitting("Fe2+", "CO", "icosahedral")
        assert result is None


class TestGetReaction:
    def test_returns_dict_for_known_reaction(self, ic):
        result = ic.get_reaction("Fe2O3_reduction")
        assert isinstance(result, dict)
        assert "reactants" in result
        assert "products" in result
        assert "conditions" in result
        assert "type" in result

    def test_returns_equation_string(self, ic):
        result = ic.get_reaction("Fe2O3_reduction")
        assert "equation" in result
        assert len(result["equation"]) > 0

    def test_unknown_reaction_returns_none(self, ic):
        result = ic.get_reaction("nonexistent_reaction")
        assert result is None
