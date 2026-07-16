"""Behavioral unit tests for the physics organic_chemistry knowledge module."""

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
    / "organic_chemistry.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_organic_chemistry_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def oc() -> ModuleType:
    return _load_module()


class TestModuleExports:
    def test_data_tables_present(self, oc):
        for attr in (
            "FUNCTIONAL_GROUPS", "REACTION_MECHANISMS", "NAMED_REACTIONS",
            "NMR_SHIFTS", "IR_BANDS", "MS_FRAGMENTATION",
        ):
            assert hasattr(oc, attr), f"missing data table {attr}"
            assert isinstance(getattr(oc, attr), dict)

    def test_functions_present(self, oc):
        for fn in ("identify_functional_groups", "predict_reaction", "look_up_named_reaction"):
            assert callable(getattr(oc, fn, None)), f"missing function {fn}"


class TestFunctionalGroups:
    _groups = ["alkane", "alkene", "alkyne", "aromatic", "alcohol",
               "ether", "aldehyde", "ketone", "carboxylic_acid", "amine", "amide"]

    @pytest.mark.parametrize("group", _groups)
    def test_group_defined(self, oc, group):
        assert group in oc.FUNCTIONAL_GROUPS, f"missing group {group}"
        data = oc.FUNCTIONAL_GROUPS[group]
        assert "formula_pattern" in data
        assert "functional_atom" in data
        assert "polarity" in data
        assert "reactivity" in data
        assert "ir_signature" in data
        assert "nmr_proton_shift" in data

    def test_alkane_pattern(self, oc):
        assert "C_nH_(2n+2)" in oc.FUNCTIONAL_GROUPS["alkane"]["formula_pattern"]

    def test_alkene_pattern(self, oc):
        assert "C=C" in oc.FUNCTIONAL_GROUPS["alkene"]["formula_pattern"]

    def test_alkyne_pattern(self, oc):
        assert "C#C" in oc.FUNCTIONAL_GROUPS["alkyne"]["formula_pattern"]

    def test_carboxylic_acid_contains_carboxyl(self, oc):
        assert "COOH" in oc.FUNCTIONAL_GROUPS["carboxylic_acid"]["formula_pattern"]

    def test_amine_contains_nitrogen(self, oc):
        data = oc.FUNCTIONAL_GROUPS["amine"]
        assert "N" in data["functional_atom"]

    def test_all_groups_have_polarity_string(self, oc):
        for name, data in oc.FUNCTIONAL_GROUPS.items():
            assert isinstance(data["polarity"], str), f"{name} polarity not string"
            assert len(data["polarity"]) > 0

    def test_all_groups_have_reactivity_description(self, oc):
        for name, data in oc.FUNCTIONAL_GROUPS.items():
            assert len(data["reactivity"]) > 10, f"{name} reactivity too short"

    def test_all_groups_have_ir_signature(self, oc):
        for name, data in oc.FUNCTIONAL_GROUPS.items():
            assert isinstance(data["ir_signature"], (str, dict)), f"{name} ir_signature wrong type"

    def test_alcohol_is_polar_protic(self, oc):
        assert "polar" in oc.FUNCTIONAL_GROUPS["alcohol"]["polarity"].lower()


class TestReactionMechanisms:
    _mechanisms = ["SN1", "SN2", "E1", "E2", "electrophilic_addition",
                   "nucleophilic_addition", "elimination", "pericyclic"]

    @pytest.mark.parametrize("mech", _mechanisms)
    def test_mechanism_defined(self, oc, mech):
        assert mech in oc.REACTION_MECHANISMS, f"missing mechanism {mech}"
        data = oc.REACTION_MECHANISMS[mech]
        assert "substrate_type" in data
        assert "rate_determining_step" in data
        assert "stereochemistry" in data
        assert "typical_solvent" in data

    def test_sn2_is_bimolecular(self, oc):
        sn2 = oc.REACTION_MECHANISMS["SN2"]
        assert "bimolecular" in sn2["rate_determining_step"].lower()

    def test_sn1_is_unimolecular(self, oc):
        sn1 = oc.REACTION_MECHANISMS["SN1"]
        assert "unimolecular" in sn1["rate_determining_step"].lower()

    def test_e2_antiperiplanar(self, oc):
        e2 = oc.REACTION_MECHANISMS["E2"]
        assert "antiperiplanar" in e2["stereochemistry"].lower()

    def test_sn2_inversion(self, oc):
        sn2 = oc.REACTION_MECHANISMS["SN2"]
        assert "inversion" in sn2["stereochemistry"].lower()

    def test_e1_carbocation(self, oc):
        e1 = oc.REACTION_MECHANISMS["E1"]
        assert "carbocation" in e1["rate_determining_step"].lower()

    def test_electrophilic_addition_on_alkenes(self, oc):
        add = oc.REACTION_MECHANISMS["electrophilic_addition"]
        assert "alkene" in add["substrate_type"].lower()

    def test_pericyclic_is_concerted(self, oc):
        peri = oc.REACTION_MECHANISMS["pericyclic"]
        assert "concerted" in peri["rate_determining_step"].lower()


class TestNamedReactions:
    _reactions = ["Diels-Alder", "Grignard", "Wittig", "Friedel-Crafts", "Suzuki Coupling"]

    @pytest.mark.parametrize("name", _reactions)
    def test_named_reaction_defined(self, oc, name):
        assert name in oc.NAMED_REACTIONS, f"missing reaction {name}"
        data = oc.NAMED_REACTIONS[name]
        assert "reactants" in data
        assert "catalyst_or_reagent" in data
        assert "mechanism_type" in data
        assert "products" in data
        assert "typical_conditions" in data

    def test_diels_alder_is_pericyclic(self, oc):
        assert "pericyclic" in oc.NAMED_REACTIONS["Diels-Alder"]["mechanism_type"].lower()

    def test_grignard_uses_magnesium(self, oc):
        assert "Mg" in oc.NAMED_REACTIONS["Grignard"]["catalyst_or_reagent"]

    def test_wittig_forms_alkene(self, oc):
        assert "alkene" in oc.NAMED_REACTIONS["Wittig"]["products"].lower()

    def test_friedel_crafts_uses_alcl3(self, oc):
        assert "AlCl3" in oc.NAMED_REACTIONS["Friedel-Crafts"]["catalyst_or_reagent"]

    def test_suzuki_uses_palladium(self, oc):
        assert "Pd" in oc.NAMED_REACTIONS["Suzuki Coupling"]["catalyst_or_reagent"]


class TestNMRSpectroscopy:
    def test_nmr_shifts_present(self, oc):
        assert len(oc.NMR_SHIFTS) >= 10, f"expected >=10 NMR shift entries, got {len(oc.NMR_SHIFTS)}"

    def test_proton_shifts_have_range(self, oc):
        for group, shift in oc.NMR_SHIFTS.items():
            assert "proton_shift_range" in shift, f"{group} missing proton_shift_range"

    def test_alkane_shift_0_to_2(self, oc):
        shift = oc.NMR_SHIFTS["alkane"]["proton_shift_range"]
        assert isinstance(shift, (list, tuple))
        assert shift[0] <= 2.0

    def test_aldehyde_shift_9_to_10(self, oc):
        shift = oc.NMR_SHIFTS["aldehyde"]["proton_shift_range"]
        assert isinstance(shift, (list, tuple))
        assert 9.0 <= shift[0] <= 10.5

    def test_carboxylic_acid_shift_10_to_13(self, oc):
        shift = oc.NMR_SHIFTS["carboxylic_acid"]["proton_shift_range"]
        assert isinstance(shift, (list, tuple))
        assert shift[0] >= 9.5


class TestIRSpectroscopy:
    def test_ir_bands_present(self, oc):
        assert len(oc.IR_BANDS) >= 8, f"expected >=8 IR band entries, got {len(oc.IR_BANDS)}"

    def test_ir_bands_have_wavenumber_range(self, oc):
        for group, band in oc.IR_BANDS.items():
            assert "wavenumber_range_cm" in band, f"{group} missing wavenumber_range_cm"

    def test_carbonyl_1700_1750(self, oc):
        wv = oc.IR_BANDS["carbonyl"]["wavenumber_range_cm"]
        assert isinstance(wv, (list, tuple))
        assert 1600 <= wv[0] <= 1800

    def test_oh_broad_3200_3600(self, oc):
        wv = oc.IR_BANDS["hydroxyl"]["wavenumber_range_cm"]
        assert isinstance(wv, (list, tuple))
        assert 3000 <= wv[0] <= 3700

    def test_nh_stretch_3300_3500(self, oc):
        wv = oc.IR_BANDS["amine_nh"]["wavenumber_range_cm"]
        assert isinstance(wv, (list, tuple))
        assert 3200 <= wv[0] <= 3600


class TestMassSpectrometry:
    def test_ms_fragmentation_has_groups(self, oc):
        assert len(oc.MS_FRAGMENTATION) >= 5, f"expected >=5 MS fragmentation entries, got {len(oc.MS_FRAGMENTATION)}"

    def test_ms_has_common_losses(self, oc):
        for group, data in oc.MS_FRAGMENTATION.items():
            assert "common_losses" in data, f"{group} missing common_losses"
            assert isinstance(data["common_losses"], (list, dict))

    def test_ms_has_diagnostic_ions(self, oc):
        for group, data in oc.MS_FRAGMENTATION.items():
            assert "diagnostic_ions" in data, f"{group} missing diagnostic_ions"


class TestIdentifyFunctionalGroups:
    def test_ethanol_returns_alcohol(self, oc):
        result = oc.identify_functional_groups("C2H5OH")
        assert isinstance(result, list)
        found_names = [g["group"] if isinstance(g, dict) else g for g in result]
        assert any("alcohol" in str(f).lower() for f in found_names)

    def test_formaldehyde_returns_aldehyde(self, oc):
        result = oc.identify_functional_groups("CH2O")
        found_names = [g["group"] if isinstance(g, dict) else g for g in result]
        assert any("aldehyde" in str(f).lower() for f in found_names)

    def test_methane_returns_alkane(self, oc):
        result = oc.identify_functional_groups("CH4")
        found_names = [g["group"] if isinstance(g, dict) else g for g in result]
        assert any("alkane" in str(f).lower() for f in found_names)

    def test_ethene_returns_alkene(self, oc):
        result = oc.identify_functional_groups("C2H4")
        found_names = [g["group"] if isinstance(g, dict) else g for g in result]
        assert any("alkene" in str(f).lower() for f in found_names)

    def test_ethyne_returns_alkyne(self, oc):
        result = oc.identify_functional_groups("C2H2")
        found_names = [g["group"] if isinstance(g, dict) else g for g in result]
        assert any("alkyne" in str(f).lower() for f in found_names)

    def test_acetone_returns_ketone(self, oc):
        result = oc.identify_functional_groups("CH3COCH3")
        found_names = [g["group"] if isinstance(g, dict) else g for g in result]
        assert any("ketone" in str(f).lower() for f in found_names)

    def test_unknown_formula_returns_empty_list(self, oc):
        result = oc.identify_functional_groups("")
        assert result == []

    def test_methylamine_returns_amine(self, oc):
        result = oc.identify_functional_groups("CH3NH2")
        found_names = [g["group"] if isinstance(g, dict) else g for g in result]
        assert any("amine" in str(f).lower() for f in found_names)

    def test_benzene_returns_aromatic(self, oc):
        result = oc.identify_functional_groups("C6H6")
        found_names = [g["group"] if isinstance(g, dict) else g for g in result]
        assert any("aromatic" in str(f).lower() for f in found_names)


class TestPredictReaction:
    def test_alkene_hcl_addition(self, oc):
        result = oc.predict_reaction("alkene", "HCl")
        assert isinstance(result, dict)
        assert "mechanism" in result
        assert "product_type" in result

    def test_alcohol_hbr_substitution(self, oc):
        result = oc.predict_reaction("alcohol", "HBr")
        assert isinstance(result, dict)
        assert "mechanism" in result

    def test_unknown_substrate_returns_none(self, oc):
        result = oc.predict_reaction("unobtanium", "HCl")
        assert result is None

    def test_ketone_nabh4_reduction(self, oc):
        result = oc.predict_reaction("ketone", "NaBH4")
        assert isinstance(result, dict)
        assert "mechanism" in result


class TestLookUpNamedReaction:
    def test_diels_alder_lookup(self, oc):
        result = oc.look_up_named_reaction("Diels-Alder")
        assert isinstance(result, dict)
        assert "pericyclic" in result["mechanism_type"].lower()

    def test_grignard_lookup(self, oc):
        result = oc.look_up_named_reaction("Grignard")
        assert isinstance(result, dict)

    def test_case_insensitive_lookup(self, oc):
        result = oc.look_up_named_reaction("wittig")
        assert isinstance(result, dict)
        assert "alkene" in result["products"].lower()

    def test_unknown_reaction_returns_none(self, oc):
        result = oc.look_up_named_reaction("Nonexistent Reaction")
        assert result is None
