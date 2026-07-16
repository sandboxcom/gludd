"""Behavioral unit tests for the physics quantum_mechanics knowledge module.

Tests the quantum_mechanics module_utils: wave functions, Dirac notation,
perturbation theory, quantum computing, and Standard Model particle data.
"""
from __future__ import annotations

import importlib.util
import math
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
    / "quantum_mechanics.py"
)


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_quantum_mechanics_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def qm() -> ModuleType:
    return _load_module()


# ═══════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════


class TestConstants:
    def test_hbar_present(self, qm):
        assert hasattr(qm, "HBAR")
        assert qm.HBAR > 0

    def test_hbar_evs_present(self, qm):
        assert hasattr(qm, "HBAR_EVS")
        assert qm.HBAR_EVS > 0

    def test_mass_electron_present(self, qm):
        assert hasattr(qm, "M_E")
        assert qm.M_E > 0

    def test_alpha_present(self, qm):
        assert hasattr(qm, "ALPHA")
        assert 0.001 < qm.ALPHA < 0.01  # ~1/137

    def test_speed_of_light_present(self, qm):
        assert hasattr(qm, "C_LIGHT")
        assert qm.C_LIGHT > 1e8


# ═══════════════════════════════════════════════════════════════════
# Energy levels
# ═══════════════════════════════════════════════════════════════════


class TestComputeEnergyLevel:
    def test_harmonic_oscillator_ground(self, qm):
        e = qm.compute_energy_level("harmonic_oscillator", 0, omega=1.0)
        assert e > 0  # zero-point energy

    def test_harmonic_oscillator_levels_increase(self, qm):
        e0 = qm.compute_energy_level("harmonic_oscillator", 0, omega=1.0)
        e1 = qm.compute_energy_level("harmonic_oscillator", 1, omega=1.0)
        assert e1 > e0

    def test_harmonic_oscillator_spacing(self, qm):
        e0 = qm.compute_energy_level("harmonic_oscillator", 0, omega=2.0)
        e1 = qm.compute_energy_level("harmonic_oscillator", 1, omega=2.0)
        e2 = qm.compute_energy_level("harmonic_oscillator", 2, omega=2.0)
        spacing = e1 - e0
        assert abs((e2 - e1) - spacing) < 1e-9

    def test_infinite_well(self, qm):
        e1 = qm.compute_energy_level("infinite_well", 1, L=1e-9)
        e2 = qm.compute_energy_level("infinite_well", 2, L=1e-9)
        assert e2 > e1
        assert abs(e2 / e1 - 4.0) < 0.01  # n^2 scaling

    def test_hydrogen_ground_state(self, qm):
        e = qm.compute_energy_level("hydrogen", 1)
        assert abs(e + 13.6) < 0.01

    def test_hydrogen_n2(self, qm):
        e = qm.compute_energy_level("hydrogen", 2)
        assert abs(e + 3.4) < 0.01  # -13.6 / 4

    def test_hydrogen_z_dependence(self, qm):
        e_h = qm.compute_energy_level("hydrogen", 1, Z=1.0)
        e_he = qm.compute_energy_level("hydrogen", 1, Z=2.0)
        assert abs(e_he / e_h - 4.0) < 0.01

    def test_3d_harmonic_oscillator(self, qm):
        e = qm.compute_energy_level("harmonic_oscillator_3d", 0, omega=1.0, l=0)
        assert e > 0

    def test_unknown_system_raises(self, qm):
        with pytest.raises(ValueError):
            qm.compute_energy_level("nonexistent", 0)


# ═══════════════════════════════════════════════════════════════════
# Wave functions
# ═══════════════════════════════════════════════════════════════════


class TestWaveFunctionHarmonicOscillator:
    def test_ground_state_at_origin(self, qm):
        psi = qm.wave_function_harmonic_oscillator(0, 0.0)
        assert psi > 0

    def test_ground_state_symmetric(self, qm):
        psi_plus = qm.wave_function_harmonic_oscillator(0, 1.0)
        psi_minus = qm.wave_function_harmonic_oscillator(0, -1.0)
        assert abs(psi_plus - psi_minus) < 1e-9

    def test_first_excited_antisymmetric(self, qm):
        psi_plus = qm.wave_function_harmonic_oscillator(1, 1.0)
        psi_minus = qm.wave_function_harmonic_oscillator(1, -1.0)
        assert abs(psi_plus + psi_minus) < 1e-9

    def test_first_excited_zero_at_origin(self, qm):
        psi = qm.wave_function_harmonic_oscillator(1, 0.0)
        assert abs(psi) < 1e-9

    def test_higher_n_decays_at_infinity(self, qm):
        m = 9.109e-31
        omega = 1e12
        psi_near = qm.wave_function_harmonic_oscillator(3, 1e-8, m=m, omega=omega)
        psi_far = qm.wave_function_harmonic_oscillator(3, 1e-7, m=m, omega=omega)
        assert abs(psi_far) < abs(psi_near)


class TestWaveFunctionHydrogen:
    def test_ground_state_real_at_origin(self, qm):
        psi = qm.wave_function_hydrogen(1, 0, 0, qm.a0, 0.0, 0.0)
        assert abs(psi.imag) < 1e-12
        assert psi.real > 0

    def test_ground_state_decays_with_r(self, qm):
        psi_near = qm.wave_function_hydrogen(1, 0, 0, qm.a0, 0.0, 0.0)
        psi_far = qm.wave_function_hydrogen(1, 0, 0, 5 * qm.a0, 0.0, 0.0)
        assert abs(psi_far) < abs(psi_near)

    def test_n2_l0_m0_real(self, qm):
        psi = qm.wave_function_hydrogen(2, 0, 0, 2 * qm.a0, 0.0, 0.0)
        assert abs(psi.imag) < 1e-12

    def test_n2_l1_m1_complex(self, qm):
        psi = qm.wave_function_hydrogen(2, 1, 1, 3 * qm.a0, math.pi / 2, 0.0)
        assert abs(psi.imag) > 1e-12 or abs(psi.real) > 1e-12

    def test_n1_l0_m0_spherically_symmetric(self, qm):
        psi1 = qm.wave_function_hydrogen(1, 0, 0, qm.a0, 0.0, 0.0)
        psi2 = qm.wave_function_hydrogen(1, 0, 0, qm.a0, math.pi / 2, 0.0)
        assert abs(psi1 - psi2) < 1e-9


# ═══════════════════════════════════════════════════════════════════
# Dirac notation
# ═══════════════════════════════════════════════════════════════════


class TestDiracNotation:
    def test_ket_indexed(self, qm):
        ket = qm.dirac_ket([1 + 0j, 0 + 0j])
        assert ket[0] == 1 + 0j
        assert ket[1] == 0 + 0j

    def test_bra_conjugate(self, qm):
        bra = qm.dirac_bra([1 + 1j, 0 + 0j])
        assert abs(bra[0] - (1 - 1j)) < 1e-9

    def test_inner_product_normalized(self, qm):
        ket = qm.dirac_ket([1.0, 0.0])
        bra = qm.dirac_bra([1.0, 0.0])
        ip = qm.inner_product(bra, ket)
        assert abs(ip - 1.0) < 1e-9

    def test_inner_product_orthogonal(self, qm):
        ket = qm.dirac_ket([1.0, 0.0])
        bra = qm.dirac_bra([0.0, 1.0])
        ip = qm.inner_product(bra, ket)
        assert abs(ip) < 1e-9

    def test_outer_product_shape(self, qm):
        bra = qm.dirac_bra([1.0, 0.0])
        ket = qm.dirac_ket([1.0, 0.0])
        outer = qm.outer_product(bra, ket)
        assert (0, 0) in outer
        assert abs(outer[(0, 0)] - 1.0) < 1e-9

    def test_expectation_value_identity(self, qm):
        identity_op = [[1 + 0j, 0 + 0j], [0 + 0j, 1 + 0j]]
        state = [1.0 + 0j, 0.0 + 0j]
        ev = qm.expectation_value(identity_op, state)
        assert abs(ev - 1.0) < 1e-9

    def test_expectation_value_pauli_z(self, qm):
        Z = [[1 + 0j, 0 + 0j], [0 + 0j, -1 + 0j]]
        up = [1.0 + 0j, 0.0 + 0j]
        assert abs(qm.expectation_value(Z, up) - 1.0) < 1e-9
        down = [0.0 + 0j, 1.0 + 0j]
        assert abs(qm.expectation_value(Z, down) + 1.0) < 1e-9

    def test_normalize_state(self, qm):
        state = [3.0 + 0j, 4.0 + 0j]
        normed = qm.normalize_state(state)
        assert abs(abs(normed[0]) ** 2 + abs(normed[1]) ** 2 - 1.0) < 1e-9

    def test_normalize_zero_state(self, qm):
        state = [0.0, 0.0]
        normed = qm.normalize_state(state)
        assert normed == state


# ═══════════════════════════════════════════════════════════════════
# Perturbation theory
# ═══════════════════════════════════════════════════════════════════


class TestPerturbationTheory:
    def test_data_table_present(self, qm):
        assert hasattr(qm, "PERTURBATION_ORDERS")
        assert isinstance(qm.PERTURBATION_ORDERS, dict)
        assert len(qm.PERTURBATION_ORDERS) >= 3

    def test_first_order_energy_shift(self, qm):
        H0 = [[1.0 + 0j, 0.0 + 0j], [0.0 + 0j, 3.0 + 0j]]
        V = [[0.5 + 0j, 0.1 + 0j], [0.1 + 0j, 0.3 + 0j]]
        evals = qm.apply_perturbation(H0, V, order=1)
        assert abs(evals[0] - 1.5) < 0.01
        assert abs(evals[1] - 3.3) < 0.01

    def test_second_order_energy_shift(self, qm):
        H0 = [[1.0 + 0j, 0.0 + 0j], [0.0 + 0j, 3.0 + 0j]]
        V = [[0.0 + 0j, 0.1 + 0j], [0.1 + 0j, 0.0 + 0j]]
        evals = qm.apply_perturbation(H0, V, order=2)
        assert evals[0] < 1.0  # E0^(2) = -|V01|^2/(E1-E0) < 0
        assert evals[1] > 3.0  # E1^(2) = |V10|^2/(E1-E0) > 0

    def test_zero_order_returns_unperturbed(self, qm):
        H0 = [[1.0 + 0j, 0.0 + 0j], [0.0 + 0j, 3.0 + 0j]]
        V = [[5.0 + 0j, 5.0 + 0j], [5.0 + 0j, 5.0 + 0j]]
        evals = qm.apply_perturbation(H0, V, order=0)
        assert abs(evals[0] - 1.0) < 0.01
        assert abs(evals[1] - 3.0) < 0.01


# ═══════════════════════════════════════════════════════════════════
# Quantum computing
# ═══════════════════════════════════════════════════════════════════


class TestQuantumGates:
    def test_gate_table_present(self, qm):
        assert hasattr(qm, "QUANTUM_GATES")
        assert isinstance(qm.QUANTUM_GATES, dict)
        assert len(qm.QUANTUM_GATES) >= 8

    def test_each_gate_has_matrix(self, qm):
        for name, gate in qm.QUANTUM_GATES.items():
            assert "matrix" in gate, f"{name} missing matrix"
            assert "description" in gate, f"{name} missing description"
            assert "qubits" in gate, f"{name} missing qubits count"

    def test_hadamard_creates_superposition(self, qm):
        state = [1 + 0j, 0 + 0j]
        result = qm.qubit_apply_gate(state, "H")
        assert abs(abs(result[0]) ** 2 - 0.5) < 1e-9
        assert abs(abs(result[1]) ** 2 - 0.5) < 1e-9

    def test_pauli_x_flips(self, qm):
        state = [1 + 0j, 0 + 0j]
        result = qm.qubit_apply_gate(state, "X")
        assert abs(abs(result[0]) ** 2) < 1e-9
        assert abs(abs(result[1]) ** 2 - 1.0) < 1e-9

    def test_pauli_x_roundtrip(self, qm):
        state = [1 + 0j, 0 + 0j]
        result = qm.qubit_apply_gate(qm.qubit_apply_gate(state, "X"), "X")
        assert abs(abs(result[0]) ** 2 - 1.0) < 1e-9

    def test_pauli_z_phase(self, qm):
        state = [0 + 0j, 1 + 0j]
        result = qm.qubit_apply_gate(state, "Z")
        assert abs(result[0]) < 1e-9
        assert abs(abs(result[1]) - 1.0) < 1e-9

    def test_identity_preserves(self, qm):
        state = [0.6 + 0j, 0.8 + 0j]
        result = qm.qubit_apply_gate(state, "I")
        assert abs(abs(result[0]) ** 2 + abs(result[1]) ** 2 - 1.0) < 1e-9

    def test_bell_states_present(self, qm):
        assert hasattr(qm, "BELL_STATES")
        assert len(qm.BELL_STATES) == 4
        for name in ["Phi+", "Phi-", "Psi+", "Psi-"]:
            assert name in qm.BELL_STATES

    def test_entangle_bell_pair(self, qm):
        s1 = [1 + 0j, 0 + 0j]
        s2 = [1 + 0j, 0 + 0j]
        entangled = qm.qubit_entangle(s1, s2)
        assert len(entangled) == 4
        assert abs(abs(entangled[0]) ** 2 - 0.5) < 1e-9
        assert abs(abs(entangled[3]) ** 2 - 0.5) < 1e-9

    def test_measure_returns_outcome(self, qm):
        state = [1.0 + 0j, 0.0 + 0j]
        outcome, collapsed = qm.measure_qubit(state)
        assert outcome in (0, 1)
        assert len(collapsed) == 2

    def test_algorithms_table_present(self, qm):
        assert hasattr(qm, "QUANTUM_ALGORITHMS")
        assert isinstance(qm.QUANTUM_ALGORITHMS, dict)
        assert "shors" in qm.QUANTUM_ALGORITHMS
        assert "grovers" in qm.QUANTUM_ALGORITHMS


# ═══════════════════════════════════════════════════════════════════
# Standard Model particle data
# ═══════════════════════════════════════════════════════════════════


class TestStandardModelParticles:
    def test_table_present(self, qm):
        assert hasattr(qm, "STANDARD_MODEL_PARTICLES")
        assert isinstance(qm.STANDARD_MODEL_PARTICLES, dict)
        assert len(qm.STANDARD_MODEL_PARTICLES) >= 17  # 6 quarks + 6 leptons + 5 bosons

    def test_quarks_present(self, qm):
        for quark in ("up", "down", "charm", "strange", "top", "bottom"):
            assert quark in qm.STANDARD_MODEL_PARTICLES, f"missing quark {quark}"

    def test_leptons_present(self, qm):
        for lepton in ("electron", "muon", "tau", "electron_neutrino", "muon_neutrino", "tau_neutrino"):
            assert lepton in qm.STANDARD_MODEL_PARTICLES, f"missing lepton {lepton}"

    def test_bosons_present(self, qm):
        for boson in ("photon", "gluon", "w_boson", "z_boson", "higgs"):
            assert boson in qm.STANDARD_MODEL_PARTICLES, f"missing boson {boson}"

    def test_quarks_have_charge_and_mass(self, qm):
        for name in ("up", "down", "charm", "strange", "top", "bottom"):
            p = qm.STANDARD_MODEL_PARTICLES[name]
            assert "charge" in p
            assert "mass_mev" in p
            assert "generation" in p
            assert p["type"] == "quark"

    def test_particle_types(self, qm):
        types = {p["type"] for p in qm.STANDARD_MODEL_PARTICLES.values()}
        assert "quark" in types
        assert "lepton" in types
        assert "boson" in types

    def test_generations_1_to_3(self, qm):
        for name, p in qm.STANDARD_MODEL_PARTICLES.items():
            if p["type"] in ("quark", "lepton"):
                assert 1 <= p["generation"] <= 3, f"{name} generation out of range"


class TestCompositeParticles:
    def test_table_present(self, qm):
        assert hasattr(qm, "COMPOSITE_PARTICLES")
        assert len(qm.COMPOSITE_PARTICLES) >= 5

    def test_proton_neutron_pions_kaons(self, qm):
        for particle in ("proton", "neutron", "pion_plus", "pion_minus", "pion_zero", "kaon_plus", "kaon_zero"):
            assert particle in qm.COMPOSITE_PARTICLES, f"missing {particle}"

    def test_proton_is_stable(self, qm):
        p = qm.COMPOSITE_PARTICLES["proton"]
        assert p["lifetime_s"] == float("inf")

    def test_neutron_decays(self, qm):
        n = qm.COMPOSITE_PARTICLES["neutron"]
        assert n["lifetime_s"] > 0
        assert n["lifetime_s"] < 10_000


class TestGetParticleData:
    def test_returns_fundamental_particle(self, qm):
        data = qm.get_particle_data("electron")
        assert data is not None
        assert data["mass_mev"] == pytest.approx(0.511, 0.01)

    def test_returns_composite_particle(self, qm):
        data = qm.get_particle_data("proton")
        assert data is not None
        assert data["charge"] == 1.0

    def test_returns_none_unknown(self, qm):
        assert qm.get_particle_data("nonexistent_particle") is None

    def test_returns_copy_not_reference(self, qm):
        data1 = qm.get_particle_data("electron")
        data2 = qm.get_particle_data("electron")
        assert data1 is not data2  # different objects


class TestGetDecayModes:
    def test_neutron_decay(self, qm):
        modes = qm.get_decay_modes("neutron")
        assert len(modes) == 1
        assert modes[0]["interaction"] == "weak"

    def test_muon_decay(self, qm):
        modes = qm.get_decay_modes("muon")
        assert len(modes) >= 1

    def test_higgs_decay(self, qm):
        modes = qm.get_decay_modes("higgs")
        assert len(modes) >= 5
        assert modes[0]["branching_ratio"] > 0.5  # H -> bb dominates

    def test_empty_for_stable(self, qm):
        modes = qm.get_decay_modes("proton")
        assert modes == []

    def test_empty_for_unknown(self, qm):
        modes = qm.get_decay_modes("nonexistent")
        assert modes == []


class TestGetParticlesByType:
    def test_quarks(self, qm):
        quarks = qm.get_particles_by_type("quark")
        assert len(quarks) == 6

    def test_leptons(self, qm):
        leptons = qm.get_particles_by_type("lepton")
        assert len(leptons) == 6

    def test_bosons(self, qm):
        bosons = qm.get_particles_by_type("boson")
        assert len(bosons) >= 5

    def test_baryons(self, qm):
        baryons = qm.get_particles_by_type("baryon")
        assert "proton" in baryons
        assert "neutron" in baryons


# ═══════════════════════════════════════════════════════════════════
# Feynman diagrams
# ═══════════════════════════════════════════════════════════════════


class TestFeynmanDiagram:
    def test_vertices_table_present(self, qm):
        assert hasattr(qm, "FEYNMAN_VERTICES")
        assert len(qm.FEYNMAN_VERTICES) >= 5

    def test_propagators_table_present(self, qm):
        assert hasattr(qm, "FEYNMAN_PROPAGATORS")
        assert len(qm.FEYNMAN_PROPAGATORS) >= 4

    def test_ee_to_mumu(self, qm):
        diagram = qm.feynman_diagram("ee_to_mumu")
        assert diagram is not None
        assert diagram["interaction"] == "electromagnetic (s-channel)"
        assert "vertices" in diagram

    def test_gg_to_H(self, qm):
        diagram = qm.feynman_diagram("gg_to_H")
        assert diagram is not None
        assert "top quark" in diagram.get("dominant_loop", "").lower()

    def test_H_to_ZZ(self, qm):
        diagram = qm.feynman_diagram("H_to_ZZ")
        assert diagram is not None
        assert "initial_state" in diagram
        assert diagram["initial_state"] == ["H"]

    def test_H_to_gammagamma(self, qm):
        diagram = qm.feynman_diagram("H_to_gammagamma")
        assert diagram is not None
        assert diagram["initial_state"] == ["H"]
        assert diagram["final_state"] == ["gamma", "gamma"]

    def test_top_decay(self, qm):
        diagram = qm.feynman_diagram("top_decay")
        assert diagram is not None
        assert "W" in diagram["initial_state"][0] or "t" in diagram["initial_state"][0]

    def test_Z_to_ll(self, qm):
        diagram = qm.feynman_diagram("Z_to_ll")
        assert diagram is not None

    def test_W_to_lnu(self, qm):
        diagram = qm.feynman_diagram("W_to_lnu")
        assert diagram is not None

    def test_unknown_process(self, qm):
        assert qm.feynman_diagram("nonexistent_process") is None


class TestCrossSections:
    def test_cs_table_present(self, qm):
        assert hasattr(qm, "CROSS_SECTIONS")
        assert len(qm.CROSS_SECTIONS) >= 4

    def test_get_cross_section(self, qm):
        cs = qm.get_cross_section("pp_total_13TeV")
        assert cs is not None
        assert cs["cross_section_mb"] > 10

    def test_get_cross_section_missing(self, qm):
        assert qm.get_cross_section("nonexistent") is None

    def test_compute_ee_to_mumu(self, qm):
        result = qm.compute_cross_section("ee_to_mumu", 200.0)
        assert result is not None
        assert result["cross_section_pb"] > 0

    def test_compute_pp_total(self, qm):
        result = qm.compute_cross_section("pp_total", 13000.0)
        assert result is not None
        assert result["cross_section_mb"] > 10

    def test_compute_unknown_calls_lookup(self, qm):
        result = qm.compute_cross_section("pp_ttbar_13TeV", 13000.0)
        assert result is not None
        assert result["cross_section_pb"] > 100


# ═══════════════════════════════════════════════════════════════════
# Hermite polynomial edge cases (indirect, via wavefunction)
# ═══════════════════════════════════════════════════════════════════


class TestHermitePolynomial:
    def test_H0(self, qm):
        h = qm._hermite_polynomial(0, 0.0)
        assert abs(h - 1.0) < 1e-9

    def test_H1(self, qm):
        h = qm._hermite_polynomial(1, 3.0)
        assert abs(h - 6.0) < 1e-9

    def test_H2(self, qm):
        h = qm._hermite_polynomial(2, 1.0)
        assert abs(h - 2.0) < 1e-9  # H2(x) = 4x^2 - 2, so H2(1) = 2
