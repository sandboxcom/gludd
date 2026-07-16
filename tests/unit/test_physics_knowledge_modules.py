"""Tests for physics knowledge modules: solid_state, nuclear_physics, thermodynamics."""

from __future__ import annotations

import importlib
import math
import os
import sys

_COLLECTION_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "collections",
        "ansible_collections",
        "general_ludd",
        "physics",
        "plugins",
        "module_utils",
    )
)
if _COLLECTION_DIR not in sys.path:
    sys.path.insert(0, _COLLECTION_DIR)

solid_state = importlib.import_module("solid_state")
nuclear_physics = importlib.import_module("nuclear_physics")
thermodynamics = importlib.import_module("thermodynamics")

CRYSTAL_STRUCTURES = solid_state.CRYSTAL_STRUCTURES
SEMICONDUCTOR_DATA = solid_state.SEMICONDUCTOR_DATA
SUPERCONDUCTOR_DATA = solid_state.SUPERCONDUCTOR_DATA
get_crystal_structure = solid_state.get_crystal_structure
compute_band_gap = solid_state.compute_band_gap
classify_material = solid_state.classify_material
get_superconductor_data = solid_state.get_superconductor_data
bcs_energy_gap_estimate = solid_state.bcs_energy_gap_estimate
critical_field_at_temperature = solid_state.critical_field_at_temperature
all_materials_by_structure = solid_state.all_materials_by_structure
all_semiconductors = solid_state.all_semiconductors
all_superconductors = solid_state.all_superconductors
highest_tc_superconductor = solid_state.highest_tc_superconductor

ISOTOPE_DATA = nuclear_physics.ISOTOPE_DATA
DECAY_CHAINS = nuclear_physics.DECAY_CHAINS
get_isotope_data = nuclear_physics.get_isotope_data
compute_decay_chain = nuclear_physics.compute_decay_chain
compute_activity = nuclear_physics.compute_activity
compute_criticality = nuclear_physics.compute_criticality
q_value = nuclear_physics.q_value
decay_constant_from_half_life = nuclear_physics.decay_constant_from_half_life
half_life_from_decay_constant = nuclear_physics.half_life_from_decay_constant
remaining_fraction = nuclear_physics.remaining_fraction
list_fissile_isotopes = nuclear_physics.list_fissile_isotopes
list_fertile_isotopes = nuclear_physics.list_fertile_isotopes
liquid_drop_binding_energy = nuclear_physics.liquid_drop_binding_energy
nuclear_shell_gaps = nuclear_physics.nuclear_shell_gaps
is_magic_nucleus = nuclear_physics.is_magic_nucleus
classify_magic = nuclear_physics.classify_magic
six_factor_formula = nuclear_physics.six_factor_formula

K_B = thermodynamics.K_B
N_A = thermodynamics.N_A
R = thermodynamics.R
compute_carnot_efficiency = thermodynamics.compute_carnot_efficiency
compute_partition_function = thermodynamics.compute_partition_function
compute_entropy = thermodynamics.compute_entropy
boltzmann_factor = thermodynamics.boltzmann_factor
boltzmann_distribution = thermodynamics.boltzmann_distribution
average_energy_canonical = thermodynamics.average_energy_canonical
heat_capacity_canonical = thermodynamics.heat_capacity_canonical
free_energy = thermodynamics.free_energy
ideal_gas_pressure = thermodynamics.ideal_gas_pressure
ideal_gas_internal_energy = thermodynamics.ideal_gas_internal_energy
van_der_waals_pressure = thermodynamics.van_der_waals_pressure
otto_efficiency = thermodynamics.otto_efficiency
diesel_efficiency = thermodynamics.diesel_efficiency
brayton_efficiency = thermodynamics.brayton_efficiency
maxwell_boltzmann_speed_distribution = thermodynamics.maxwell_boltzmann_speed_distribution
most_probable_speed = thermodynamics.most_probable_speed
mean_speed = thermodynamics.mean_speed
rms_speed = thermodynamics.rms_speed
ising_mean_field_magnetization = thermodynamics.ising_mean_field_magnetization
landau_free_energy = thermodynamics.landau_free_energy
get_phase_transition_data = thermodynamics.get_phase_transition_data
get_engine_cycle = thermodynamics.get_engine_cycle
all_engine_cycles = thermodynamics.all_engine_cycles
get_ensemble = thermodynamics.get_ensemble
clausius_clapeyron = thermodynamics.clausius_clapeyron
LAWS_OF_THERMODYNAMICS = thermodynamics.LAWS_OF_THERMODYNAMICS
HEAT_ENGINE_CYCLES = thermodynamics.HEAT_ENGINE_CYCLES
PHASE_TRANSITIONS = thermodynamics.PHASE_TRANSITIONS
ENSEMBLE_TYPES = thermodynamics.ENSEMBLE_TYPES


# ─── solid_state: crystal structures ───

class TestCrystalStructures:
    def test_all_entries_have_required_fields(self):
        required = {"material", "structure", "atoms_per_unit_cell", "coordination_number"}
        for entry in CRYSTAL_STRUCTURES:
            for field in required:
                assert field in entry, f"Missing {field} in {entry.get('material', '?')}"

    def test_packing_efficiency_in_range(self):
        for entry in CRYSTAL_STRUCTURES:
            if "packing_efficiency" not in entry:
                continue
            pe = entry["packing_efficiency"]
            assert 0 < pe <= 0.75, f"Invalid packing efficiency {pe} for {entry['material']}"

    def test_atoms_per_unit_cell_positive_int(self):
        for entry in CRYSTAL_STRUCTURES:
            assert isinstance(entry["atoms_per_unit_cell"], int)
            assert entry["atoms_per_unit_cell"] > 0

    def test_get_crystal_structure_found(self):
        result = get_crystal_structure("silicon")
        assert result is not None
        assert result["structure"] == "diamond"
        assert result["lattice_constant_a"] == 5.431

    def test_get_crystal_structure_not_found(self):
        assert get_crystal_structure("unobtainium") is None

    def test_bcc_packing(self):
        bcc = get_crystal_structure("tungsten")
        assert bcc is not None
        assert bcc["structure"] == "BCC"
        assert bcc["packing_efficiency"] == 0.68

    def test_fcc_packing(self):
        fcc = get_crystal_structure("copper")
        assert fcc is not None
        assert fcc["structure"] == "FCC"
        assert fcc["packing_efficiency"] == 0.74

    def test_diamond_crystal(self):
        dia = get_crystal_structure("diamond_carbon")
        assert dia is not None
        assert dia["atoms_per_unit_cell"] == 8
        assert dia["coordination_number"] == 4

    def test_perovskite(self):
        per = get_crystal_structure("barium_titanate")
        assert per is not None
        assert per["structure"] == "perovskite"
        assert "curie_temperature_k" in per

    def test_hcp_c_a_ratio_present(self):
        mg = get_crystal_structure("magnesium")
        assert mg is not None
        assert "c_a_ratio" in mg
        assert mg["structure"] == "HCP"

    def test_all_materials_by_structure_fcc(self):
        fcc_mats = all_materials_by_structure("FCC")
        assert "aluminum" in fcc_mats
        assert "copper" in fcc_mats
        assert len(fcc_mats) >= 2

    def test_all_materials_by_structure_unknown(self):
        assert all_materials_by_structure("hexagonal_prismatic") == []


# ─── solid_state: semiconductors ───

class TestSemiconductors:
    def test_semiconductor_data_has_band_gap(self):
        for entry in SEMICONDUCTOR_DATA:
            assert "band_gap_ev" in entry
            assert entry["band_gap_ev"] > 0

    def test_compute_band_gap_silicon(self):
        assert compute_band_gap("silicon") == 1.12

    def test_compute_band_gap_gan(self):
        assert compute_band_gap("gallium_nitride") == 3.4

    def test_compute_band_gap_germanium(self):
        assert compute_band_gap("germanium") == 0.67

    def test_compute_band_gap_unknown(self):
        assert compute_band_gap("wood") is None

    def test_compute_band_gap_from_crystal(self):
        assert compute_band_gap("diamond_carbon") == 5.47

    def test_all_semiconductors(self):
        semis = all_semiconductors()
        assert "silicon" in semis
        assert "gallium_arsenide" in semis
        assert len(semis) >= 6

    def test_classify_material_conductor(self):
        result = classify_material("copper")
        assert "conductor" in result["classification"]

    def test_classify_material_semiconductor(self):
        result = classify_material("silicon")
        assert "semiconductor" in result["classification"]

    def test_classify_material_insulator(self):
        result = classify_material("diamond_carbon")
        assert result["classification"] == "insulator"
        assert result["band_gap_ev"] == 5.47

    def test_classify_material_unknown(self):
        result = classify_material("unicorn_horn")
        assert result["classification"] == "unknown"


# ─── solid_state: superconductivity ───

class TestSuperconductivity:
    def test_superconductor_data_has_tc(self):
        for entry in SUPERCONDUCTOR_DATA:
            assert "tc_k" in entry
            assert entry["tc_k"] > 0

    def test_get_superconductor_data_ybco(self):
        ybco = get_superconductor_data("ybco")
        assert ybco is not None
        assert ybco["tc_k"] == 92.0
        assert ybco["type"] == "type-II"

    def test_get_superconductor_data_mercury(self):
        hg = get_superconductor_data("mercury")
        assert hg is not None
        assert hg["tc_k"] == 4.15
        assert hg["type"] == "type-I"
        assert hg["year_discovered"] == 1911

    def test_get_superconductor_data_not_found(self):
        assert get_superconductor_data("wood") is None

    def test_all_superconductors_count(self):
        all_sc = all_superconductors()
        assert len(all_sc) >= 10

    def test_highest_tc(self):
        best = highest_tc_superconductor()
        assert best["tc_k"] == 250.0
        assert best["material"] == "lanthanum_decahydride"

    def test_bcs_energy_gap_niobium(self):
        delta = bcs_energy_gap_estimate(9.26)
        assert 1.3 < delta < 1.5

    def test_bcs_energy_gap_zero(self):
        assert bcs_energy_gap_estimate(0.0) == 0.0

    def test_critical_field_niobium_below_tc(self):
        result = critical_field_at_temperature("niobium", 4.0)
        assert result["superconducting"] is True
        assert result["hc2_t"] > 0

    def test_critical_field_niobium_above_tc(self):
        result = critical_field_at_temperature("niobium", 10.0)
        assert result["superconducting"] is False
        assert result["hc_t"] == 0.0

    def test_critical_field_mercury_type1(self):
        result = critical_field_at_temperature("mercury", 2.0)
        assert result["superconducting"] is True
        assert "hc_t" in result

    def test_critical_field_unknown(self):
        result = critical_field_at_temperature("glass", 4.0)
        assert "error" in result


# ─── nuclear_physics: isotope data ───

class TestIsotopeData:
    def test_isotope_data_integrity(self):
        for entry in ISOTOPE_DATA:
            assert "nuclide" in entry
            assert isinstance(entry["z"], int)
            assert isinstance(entry["a"], int)
            assert entry["a"] == entry["z"] + entry["n"]

    def test_get_isotope_data_u235(self):
        data = get_isotope_data("U-235")
        assert data is not None
        assert data["z"] == 92
        assert data["a"] == 235
        assert data["fissile"] is True

    def test_get_isotope_data_u238(self):
        data = get_isotope_data("U-238")
        assert data is not None
        assert data["fertile"] is True
        assert data["fissile"] is False

    def test_get_isotope_data_th232(self):
        data = get_isotope_data("Th-232")
        assert data is not None
        assert data["fertile"] is True
        assert data["z"] == 90

    def test_get_isotope_data_not_found(self):
        assert get_isotope_data("Xy-999") is None

    def test_list_fissile(self):
        fissile = list_fissile_isotopes()
        assert "U-235" in fissile
        assert "Pu-239" in fissile
        assert "U-233" in fissile

    def test_list_fertile(self):
        fertile = list_fertile_isotopes()
        assert "U-238" in fertile
        assert "Th-232" in fertile


# ─── nuclear_physics: decay chains ───

class TestDecayChains:
    def test_u238_chain_starts_with_parent(self):
        chain = compute_decay_chain("U-238")
        assert len(chain) > 10
        assert chain[0]["nuclide"] == "U-238"

    def test_u238_chain_ends_with_pb206(self):
        chain = compute_decay_chain("U-238")
        assert chain[-1]["nuclide"] == "Pb-206"

    def test_u235_chain_ends_with_pb207(self):
        chain = compute_decay_chain("U-235")
        assert chain[-1]["nuclide"] == "Pb-207"

    def test_th232_chain_ends_with_pb208(self):
        chain = compute_decay_chain("Th-232")
        assert chain[-1]["nuclide"] == "Pb-208"

    def test_unknown_chain_empty(self):
        assert compute_decay_chain("Fe-56") == []

    def test_decay_chains_have_all_three_natural(self):
        assert "U-238" in DECAY_CHAINS
        assert "U-235" in DECAY_CHAINS
        assert "Th-232" in DECAY_CHAINS
        assert len(DECAY_CHAINS) == 3


# ─── nuclear_physics: activity ───

class TestActivity:
    def test_activity_u235_one_gram(self):
        result = compute_activity("U-235", 1.0)
        assert result["nuclide"] == "U-235"
        assert result["mass_g"] == 1.0
        assert 70000 < result["activity_bq"] < 90000

    def test_activity_cs137_one_gram(self):
        result = compute_activity("Cs-137", 1.0)
        assert result["activity_bq"] > 1e12

    def test_activity_unknown(self):
        result = compute_activity("Xy-999", 1.0)
        assert "error" in result


# ─── nuclear_physics: criticality ───

class TestCriticality:
    def test_criticality_pure_u235(self):
        result = compute_criticality({"U-235": 1.0})
        assert "k_eff" in result
        assert result["k_eff"] > 1.0

    def test_criticality_4pct_enriched(self):
        result = compute_criticality({"U-235": 0.04, "U-238": 0.96})
        assert result["state"] in ("subcritical", "critical", "supercritical")

    def test_criticality_pure_u238(self):
        result = compute_criticality({"U-238": 1.0})
        assert "error" in result

    def test_criticality_pure_th232(self):
        result = compute_criticality({"Th-232": 1.0})
        assert "error" in result

    def test_criticality_empty(self):
        result = compute_criticality({})
        assert "error" in result

    def test_criticality_zero_sum(self):
        result = compute_criticality({"U-235": 0.0, "U-238": 0.0})
        assert "error" in result

    def test_k_eff_in_range(self):
        result = compute_criticality({"U-235": 0.05, "U-238": 0.95})
        assert 0.0 < result["k_eff"] < 2.0


# ─── nuclear_physics: Q-value ───

class TestQValue:
    def test_q_value_u235_to_th231(self):
        result = q_value("U-235", "Th-231")
        if "error" in result:
            pass
        else:
            assert result["q_value_mev"] > 0

    def test_q_value_missing_parent(self):
        result = q_value("Xy-999", "Th-231")
        assert "error" in result


# ─── nuclear_physics: decay math ───

class TestDecayMath:
    def test_decay_constant_30y_half_life(self):
        half_life = 30.17 * 365.25 * 86400
        lam = decay_constant_from_half_life(half_life)
        assert abs(half_life_from_decay_constant(lam) - half_life) < 1.0

    def test_remaining_fraction_one_half_life(self):
        half_life = 100.0
        assert abs(remaining_fraction(half_life, half_life) - 0.5) < 1e-10

    def test_remaining_fraction_two_half_lives(self):
        half_life = 100.0
        assert abs(remaining_fraction(2 * half_life, half_life) - 0.25) < 1e-10

    def test_remaining_fraction_zero_time(self):
        assert remaining_fraction(0.0, 100.0) == 1.0


# ─── nuclear_physics: liquid drop model ───

class TestLiquidDrop:
    def test_iron56_binding(self):
        result = liquid_drop_binding_energy(56, 26)
        assert result["a"] == 56
        assert result["z"] == 26
        assert 8.0 < result["binding_energy_per_nucleon_mev"] < 9.0

    def test_u238_binding(self):
        result = liquid_drop_binding_energy(238, 92)
        assert 7.0 < result["binding_energy_per_nucleon_mev"] < 8.0

    def test_terms_sum_to_total(self):
        result = liquid_drop_binding_energy(56, 26)
        computed = result["volume_term"] - result["surface_term"] - result["coulomb_term"] - result["asymmetry_term"] + result["pairing_term"]
        delta = abs(computed - result["binding_energy_mev"])
        assert delta < 0.01

    def test_magic_nucleus_pairing(self):
        result = liquid_drop_binding_energy(40, 20)
        assert result["pairing_term"] > 0


# ─── nuclear_physics: shell model ───

class TestShellModel:
    def test_magic_numbers(self):
        magic = nuclear_shell_gaps()
        assert magic == [2, 8, 20, 28, 50, 82, 126]

    def test_doubly_magic_lead208(self):
        result = classify_magic(82, 126)
        assert result["category"] == "doubly-magic"

    def test_doubly_magic_oxygen16(self):
        result = classify_magic(8, 8)
        assert result["category"] == "doubly-magic"

    def test_singly_magic_tin120(self):
        result = classify_magic(50, 70)
        assert result["category"] == "singly-magic"

    def test_non_magic_tungsten(self):
        result = classify_magic(74, 110)
        assert result["category"] == "non-magic"

    def test_is_magic_nucleus_returns_bool(self):
        assert is_magic_nucleus(8, 8) is True
        assert is_magic_nucleus(82, 126) is True
        assert is_magic_nucleus(6, 6) is False


# ─── nuclear_physics: six-factor formula ───

class TestSixFactorFormula:
    def test_critical_k_inf_1(self):
        k_eff = six_factor_formula(1.5, epsilon=1.02, p=0.9, f=0.85, p_fnl=0.97, p_tnl=0.95)
        assert k_eff > 1.0

    def test_k_inf_less_than_1(self):
        k_eff = six_factor_formula(0.8)
        assert k_eff < 1.0


# ─── thermodynamics: laws ───

class TestThermodynamicsLaws:
    def test_four_laws_exist(self):
        assert len(LAWS_OF_THERMODYNAMICS) == 4

    def test_all_laws_have_statements(self):
        for law in LAWS_OF_THERMODYNAMICS:
            assert "law" in law
            assert "statement" in law
            assert "significance" in law

    def test_law_names(self):
        names = {law["law"] for law in LAWS_OF_THERMODYNAMICS}
        assert names == {"zeroth", "first", "second", "third"}


# ─── thermodynamics: heat engines ───

class TestHeatEngines:
    def test_engine_cycles_count(self):
        assert len(HEAT_ENGINE_CYCLES) >= 6

    def test_get_carnot_cycle(self):
        cycle = get_engine_cycle("carnot")
        assert cycle is not None
        assert cycle["type"] == "theoretical_upper_bound"

    def test_get_otto_cycle(self):
        cycle = get_engine_cycle("otto")
        assert cycle is not None
        assert cycle["type"] == "IC_engine"

    def test_get_unknown_cycle(self):
        assert get_engine_cycle("perpetual_motion") is None

    def test_all_engine_cycles(self):
        cycles = all_engine_cycles()
        assert "carnot" in cycles
        assert "diesel" in cycles
        assert "brayton" in cycles

    def test_carnot_efficiency_basic(self):
        eta = compute_carnot_efficiency(600.0, 300.0)
        assert eta == 0.5

    def test_carnot_efficiency_high_ratio(self):
        eta = compute_carnot_efficiency(1000.0, 100.0)
        assert abs(eta - 0.9) < 1e-10

    def test_carnot_efficiency_hot_must_exceed_cold(self):
        try:
            compute_carnot_efficiency(300.0, 600.0)
            raise AssertionError("Should have raised")
        except ValueError:
            pass

    def test_carnot_efficiency_positive(self):
        try:
            compute_carnot_efficiency(0.0, 100.0)
            raise AssertionError("Should have raised")
        except ValueError:
            pass

    def test_otto_efficiency_r8(self):
        eta = otto_efficiency(8.0, gamma=1.4)
        assert 0.5 < eta < 0.6

    def test_diesel_efficiency(self):
        eta = diesel_efficiency(18.0, 2.0, gamma=1.4)
        assert 0.5 < eta < 0.7

    def test_brayton_efficiency(self):
        eta = brayton_efficiency(10.0, gamma=1.4)
        assert 0.4 < eta < 0.6


# ─── thermodynamics: entropy & partition functions ───

class TestPartitionFunction:
    def test_partition_two_level_system(self):
        z = compute_partition_function([0.0, 1.0e-20], 300.0)
        assert z > 0
        assert abs(z - (1.0 + math.exp(-1.0e-20 / (K_B * 300.0)))) < 1e-12

    def test_partition_single_state(self):
        z = compute_partition_function([0.0], 300.0)
        assert z == 1.0

    def test_partition_negative_temp_raises(self):
        try:
            compute_partition_function([0.0], -1.0)
            raise AssertionError()
        except ValueError:
            pass

    def test_entropy_uniform(self):
        s = compute_entropy([0.5, 0.5])
        assert s > 0
        expected = -K_B * (0.5 * math.log(0.5) + 0.5 * math.log(0.5))
        assert abs(s - expected) < 1e-15

    def test_entropy_certain(self):
        s = compute_entropy([1.0, 0.0])
        assert s == 0.0

    def test_entropy_unnormalized(self):
        s_raw = compute_entropy([1.0, 1.0, 1.0, 1.0])
        s_norm = compute_entropy([0.25, 0.25, 0.25, 0.25])
        assert abs(s_raw - s_norm) < 1e-15


# ─── thermodynamics: Boltzmann distribution ───

class TestBoltzmannDistribution:
    def test_boltzmann_factor(self):
        bf = boltzmann_factor(0.0, 300.0)
        assert bf == 1.0

    def test_boltzmann_factor_high_energy(self):
        bf = boltzmann_factor(1.0e-19, 300.0)
        assert 0.0 < bf < 1.0

    def test_distribution_sums_to_one(self):
        energies = [0.0, 2.0e-21, 5.0e-21]
        probs = boltzmann_distribution(energies, 300.0)
        assert abs(sum(probs) - 1.0) < 1e-12

    def test_ground_state_highest_probability(self):
        energies = [0.0, 2.0e-21, 5.0e-21]
        probs = boltzmann_distribution(energies, 300.0)
        assert probs[0] > probs[1]
        assert probs[1] > probs[2]

    def test_average_energy_canonical(self):
        energies = [0.0, 1.0e-21]
        avg = average_energy_canonical(energies, 300.0)
        assert 0.0 < avg < 1.0e-21

    def test_heat_capacity_positive(self):
        energies = [0.0, 1.0e-21, 3.0e-21]
        cv = heat_capacity_canonical(energies, 300.0)
        assert cv > 0

    def test_free_energy(self):
        energies = [0.0, 1.0e-21]
        f = free_energy(energies, 300.0)
        assert f < 0


# ─── thermodynamics: ideal gas ───

class TestIdealGas:
    def test_ideal_gas_pressure_one_mole_stp(self):
        p = ideal_gas_pressure(1.0, 273.15, 0.022414)
        assert 101000 < p < 102000

    def test_ideal_gas_pressure_doubled_moles(self):
        p1 = ideal_gas_pressure(1.0, 300.0, 0.0245)
        p2 = ideal_gas_pressure(2.0, 300.0, 0.0245)
        assert abs(p2 - 2.0 * p1) < 1e-6

    def test_ideal_gas_energy(self):
        u = ideal_gas_internal_energy(1.0, 300.0)
        assert u > 0

    def test_ideal_gas_energy_monatomic(self):
        u_mono = ideal_gas_internal_energy(1.0, 300.0, degrees_of_freedom=3.0)
        u_di = ideal_gas_internal_energy(1.0, 300.0, degrees_of_freedom=5.0)
        assert u_di > u_mono


# ─── thermodynamics: van der Waals ───

class TestVanDerWaals:
    def test_vdw_pressure_vs_ideal(self):
        a = 0.137
        b = 3.87e-5
        p_ideal = ideal_gas_pressure(1.0, 300.0, 0.001)
        p_vdw = van_der_waals_pressure(1.0, 300.0, 0.001, a, b)
        assert p_vdw != p_ideal

    def test_vdw_too_small_volume(self):
        try:
            van_der_waals_pressure(1.0, 300.0, 3.87e-5, 0.137, 3.87e-5)
            raise AssertionError()
        except ValueError:
            pass


# ─── thermodynamics: Maxwell-Boltzmann ───

class TestMaxwellBoltzmann:
    def test_speed_ordering(self):
        m = 4.65e-26
        t = 300.0
        v_mp = most_probable_speed(m, t)
        v_mean = mean_speed(m, t)
        v_rms_calc = rms_speed(m, t)
        assert v_mp < v_mean < v_rms_calc

    def test_rms_speed_n2_room_temp(self):
        m_n2 = 28.0 * 1.660539e-27
        v_rms_val = rms_speed(m_n2, 300.0)
        assert 400 < v_rms_val < 600

    def test_distribution_at_zero_speed(self):
        m = 4.65e-26
        t = 300.0
        f0 = maxwell_boltzmann_speed_distribution(0.0, m, t)
        assert f0 == 0.0

    def test_distribution_positive(self):
        m = 4.65e-26
        t = 300.0
        f = maxwell_boltzmann_speed_distribution(500.0, m, t)
        assert f > 0


# ─── thermodynamics: phase transitions ───

class TestPhaseTransitions:
    def test_phase_transitions_count(self):
        assert len(PHASE_TRANSITIONS) >= 6

    def test_get_water_transitions(self):
        water = get_phase_transition_data("water")
        assert len(water) >= 2
        transitions = {t["transition"] for t in water}
        assert "liquid_to_gas" in transitions
        assert "solid_to_liquid" in transitions

    def test_iron_curie_temperature(self):
        iron = get_phase_transition_data("iron_alpha")
        assert len(iron) == 1
        assert iron[0]["transition"] == "ferromagnetic_to_paramagnetic"
        assert iron[0]["curie_temperature_k"] == 1043.0

    def test_unknown_material_empty(self):
        assert get_phase_transition_data("nonexistent") == []

    def test_helium_lambda_point(self):
        he4 = get_phase_transition_data("helium_4")
        assert len(he4) == 1
        assert he4[0]["lambda_point_k"] == 2.17


# ─── thermodynamics: ensembles ───

class TestEnsembles:
    def test_four_ensembles(self):
        assert len(ENSEMBLE_TYPES) == 4

    def test_get_canonical_ensemble(self):
        ens = get_ensemble("canonical")
        assert ens is not None
        assert ens["abbreviation"] == "NVT"
        assert "temperature T" in ens["fixed_quantities"][2]

    def test_get_ensemble_by_abbreviation(self):
        ens = get_ensemble("NVE")
        assert ens is not None
        assert ens["ensemble"] == "microcanonical"

    def test_get_unknown_ensemble(self):
        assert get_ensemble("magic") is None

    def test_all_ensembles_have_potential(self):
        for ens in ENSEMBLE_TYPES:
            assert "thermodynamic_potential" in ens


# ─── thermodynamics: Ising & Landau ───

class TestIsingLandau:
    def test_ising_above_tc(self):
        m = ising_mean_field_magnetization(1100.0, 1043.0)
        assert m == 0.0

    def test_ising_below_tc(self):
        m = ising_mean_field_magnetization(1000.0, 1043.0)
        assert m > 0

    def test_landau_above_tc(self):
        f = landau_free_energy(0.1, 1100.0, 1000.0)
        assert f > 0

    def test_landau_below_tc_two_minima(self):
        f0 = landau_free_energy(0.0, 900.0, 1000.0)
        f1 = landau_free_energy(0.5, 900.0, 1000.0)
        assert f1 < f0


# ─── thermodynamics: Clausius-Clapeyron ───

class TestClausiusClapeyron:
    def test_latent_heat_water(self):
        lh = clausius_clapeyron(3500.0, 373.15, 1.67)
        assert lh > 0


# ─── cross-module: constants ───

class TestConstants:
    def test_boltzmann_constant_value(self):
        assert abs(K_B - 1.380649e-23) < 1e-29

    def test_avogadro_constant_value(self):
        assert abs(N_A - 6.02214076e23) < 1e15

    def test_gas_constant_value(self):
        assert abs(R - 8.314462618) < 1e-6

    def test_gas_constant_equals_kb_times_na(self):
        assert abs(R - K_B * N_A) < 1e-6
