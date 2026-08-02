"""
quantum_mechanics -- Quantum mechanics, quantum computing, and particle physics.

Exposes wave functions, Dirac notation, perturbation theory, qubit operations,
Standard Model particle data, and Feynman diagram construction.

Data tables:
    STANDARD_MODEL_PARTICLES -- dict[name] -> particle properties
    PERTURBATION_ORDERS        -- dict[order] -> correction formulas
    QUANTUM_GATES              -- dict[name] -> gate matrices and descriptions
    ELEMENTARY_PARTICLE_DATA   -- PDG-derived masses, charges, lifetimes, decay modes

Functions:
    compute_energy_level(system, n)           -> energy in eV
    wave_function_harmonic_oscillator(n, x)   -> psi_n(x)
    wave_function_hydrogen(n, l, m, r, theta, phi) -> psi_{nlm}(r, theta, phi)
    dirac_bra(psi)                            -> bra vector
    dirac_ket(psi)                            -> ket vector
    inner_product(bra, ket)                   -> complex scalar
    apply_perturbation(H0, V, order)          -> perturbed eigenvalues
    qubit_apply_gate(state, gate_name)        -> transformed state vector
    qubit_entangle(state1, state2)            -> entangled state
    get_particle_data(name)                   -> particle properties dict
    get_decay_modes(particle)                 -> list of decay channels
    feynman_diagram(process)                  -> diagram vertices and propagators
    compute_cross_section(process, energy)    -> approximate cross-section in barns
"""
from __future__ import annotations

import cmath
import math
from typing import Any

# ═══════════════════════════════════════════════════════════════════
# Physical constants
# ═══════════════════════════════════════════════════════════════════

HBAR: float = 1.054571817e-34  # J*s
HBAR_EVS: float = 6.582119569e-16  # eV*s
M_E: float = 9.1093837015e-31  # kg
M_E_MEV: float = 0.510998950  # MeV/c^2
E_CHARGE: float = 1.602176634e-19  # C
ALPHA: float = 1.0 / 137.035999084  # fine-structure constant
K_B: float = 8.617333262145e-5  # eV/K
C_LIGHT: float = 2.99792458e8  # m/s

# ═══════════════════════════════════════════════════════════════════
# Quantum mechanical systems -- energy levels
# ═══════════════════════════════════════════════════════════════════


def compute_energy_level(system: str, n: int, **kwargs: float) -> float:
    """Return the n-th energy level (eV) for a given quantum system."""
    if system == "harmonic_oscillator":
        omega = kwargs.get("omega", 1.0)
        return HBAR_EVS * omega * (n + 0.5)
    if system == "infinite_well":
        L = kwargs.get("L", 1e-9)
        m = kwargs.get("m", M_E)
        return (n * n * math.pi * math.pi * HBAR * HBAR) / (2.0 * m * L * L) / E_CHARGE
    if system == "hydrogen":
        Z = kwargs.get("Z", 1.0)
        return -13.6 * Z * Z * (1.0 / (n * n))
    if system == "harmonic_oscillator_3d":
        omega = kwargs.get("omega", 1.0)
        l = int(kwargs.get("l", 0))
        return HBAR_EVS * omega * (n + l + 1.5)
    raise ValueError(f"Unknown system: {system}")


# ═══════════════════════════════════════════════════════════════════
# Wave functions
# ═══════════════════════════════════════════════════════════════════


def wave_function_harmonic_oscillator(n: int, x: float, m: float = M_E, omega: float = 1.0) -> float:
    """Return psi_n(x) for 1D harmonic oscillator (normalized)."""
    alpha = m * omega / HBAR
    prefactor = 1.0 / math.sqrt((2 ** n) * math.factorial(n)) * (alpha / math.pi) ** 0.25
    xi = math.sqrt(alpha) * x
    hermite = _hermite_polynomial(n, xi)
    return prefactor * hermite * math.exp(-0.5 * xi * xi)


def _hermite_polynomial(n: int, x: float) -> float:
    """Compute the physicist's Hermite polynomial H_n(x)."""
    if n == 0:
        return 1.0
    if n == 1:
        return 2.0 * x
    h0, h1 = 1.0, 2.0 * x
    for k in range(2, n + 1):
        h0, h1 = h1, 2.0 * x * h1 - 2.0 * (k - 1) * h0
    return h1


def wave_function_hydrogen(
    n: int, l: int, m_val: int, r: float, theta: float, phi: float, Z: float = 1.0
) -> complex:
    """Return psi_{nlm}(r, theta, phi) for hydrogen-like atom (SI units)."""
    a0 = 4.0 * math.pi * 8.8541878128e-12 * HBAR * HBAR / (M_E * E_CHARGE * E_CHARGE)
    rho = 2.0 * Z * r / (n * a0)
    radial_part = _hydrogen_radial(n, l, rho) * (2.0 * Z / (n * a0)) ** 1.5
    spherical = _spherical_harmonic(l, m_val, theta, phi)
    return radial_part * spherical


def _hydrogen_radial(n: int, l: int, rho: float) -> float:
    """Normalized hydrogen radial wavefunction R_{nl}(rho)."""
    norm = math.sqrt(
        (2.0 / (n * a0)) ** 3 * math.factorial(n - l - 1) / (2.0 * n * math.factorial(n + l))
    )
    assoc = _associated_laguerre(2 * l + 1, n - l - 1, rho)
    return norm * (rho ** l) * math.exp(-rho / 2.0) * assoc


def _associated_laguerre(k: int, p: int, x: float) -> float:
    """Associated Laguerre polynomial L_p^k(x)."""
    if p == 0:
        return 1.0
    if p == 1:
        return 1.0 + k - x
    l0, l1 = 1.0, 1.0 + k - x
    for j in range(2, p + 1):
        l0, l1 = l1, (
            (2.0 * j + k - 1.0 - x) * l1 - (j + k - 1.0) * l0
        ) / j
    return l1


def _spherical_harmonic(l: int, m_val: int, theta: float, phi: float) -> complex:
    """Spherical harmonic Y_l^m(theta, phi)."""
    if l == 0 and m_val == 0:
        return 1.0 / math.sqrt(4.0 * math.pi)
    if l == 1 and m_val == 0:
        return math.sqrt(3.0 / (4.0 * math.pi)) * math.cos(theta)
    if l == 1 and m_val == 1:
        return -math.sqrt(3.0 / (8.0 * math.pi)) * math.sin(theta) * cmath.exp(1j * phi)
    if l == 1 and m_val == -1:
        return math.sqrt(3.0 / (8.0 * math.pi)) * math.sin(theta) * cmath.exp(-1j * phi)
    return 0.0 + 0.0j


a0: float = 5.29177210903e-11  # Bohr radius (m)


# ═══════════════════════════════════════════════════════════════════
# Dirac notation
# ═══════════════════════════════════════════════════════════════════


def dirac_ket(components: list[complex]) -> dict[int, complex]:
    """Return |psi> as a dictionary index -> amplitude."""
    return dict(enumerate(components))


def dirac_bra(components: list[complex]) -> dict[int, complex]:
    """Return <psi| as a dictionary index -> complex conjugate."""
    return dict(enumerate(c.conjugate() for c in components))


def inner_product(bra: dict[int, complex], ket: dict[int, complex]) -> complex:
    """Compute <phi|psi> = sum_i conj(phi_i) * psi_i."""
    result: complex = 0 + 0j
    for i in ket:
        result += bra.get(i, 0 + 0j).conjugate() * ket[i]
    return result


def outer_product(bra: dict[int, complex], ket: dict[int, complex]) -> dict[tuple[int, int], complex]:
    """Compute |psi><phi| as a matrix."""
    result: dict[tuple[int, int], complex] = {}
    for i in bra:
        for j in ket:
            result[(i, j)] = bra[i] * ket[j].conjugate()
    return result


def expectation_value(operator: list[list[complex]], state: list[complex]) -> float:
    """Compute <psi|O|psi> for a Hermitian operator."""
    n = len(state)
    total: complex = 0 + 0j
    for i in range(n):
        for j in range(n):
            total += state[i].conjugate() * operator[i][j] * state[j]
    return total.real


def normalize_state(state: list[complex]) -> list[complex]:
    """Normalize a quantum state vector to unit norm."""
    norm = math.sqrt(sum(abs(c) ** 2 for c in state))
    if norm == 0.0:
        return state
    return [c / norm for c in state]


# ═══════════════════════════════════════════════════════════════════
# Perturbation theory
# ═══════════════════════════════════════════════════════════════════


PERTURBATION_ORDERS: dict[str, str] = {
    "first_order_energy": "E_n^(1) = <n|V|n>",
    "first_order_state": "|n>^(1) = sum_{m!=n} |m><m|V|n> / (E_n^(0) - E_m^(0))",
    "second_order_energy": "E_n^(2) = sum_{m!=n} |<m|V|n>|^2 / (E_n^(0) - E_m^(0))",
    "second_order_state": "|n>^(2) = sum_{m!=n,k!=n} ...",
}


def apply_perturbation(
    H0: list[list[complex]], V: list[list[complex]], order: int = 1
) -> list[float]:
    """Compute eigenvalues of H0 + V using Rayleigh-Schrodinger perturbation theory."""
    n = len(H0)
    eigenvalues: list[float] = []
    for i in range(n):
        e0 = H0[i][i].real
        if order >= 1:
            e1 = V[i][i].real
            if order == 1:
                eigenvalues.append(e0 + e1)
                continue
        if order >= 2:
            e2 = 0.0
            for j in range(n):
                if j != i and abs(H0[i][i] - H0[j][j]) > 1e-12:
                    denom = H0[i][i].real - H0[j][j].real
                    e2 += abs(V[j][i]) ** 2 / denom
            eigenvalues.append(e0 + V[i][i].real + e2)
            continue
        eigenvalues.append(e0)
    return eigenvalues


# ═══════════════════════════════════════════════════════════════════
# Quantum computing -- qubits and gates
# ═══════════════════════════════════════════════════════════════════

QUANTUM_GATES: dict[str, dict[str, Any]] = {
    "I": {
        "matrix": [[1, 0], [0, 1]],
        "description": "Identity gate",
        "qubits": 1,
    },
    "X": {
        "matrix": [[0, 1], [1, 0]],
        "description": "Pauli-X / NOT gate -- flips |0> to |1> and vice versa",
        "qubits": 1,
    },
    "Y": {
        "matrix": [[0, complex(0, -1)], [complex(0, 1), 0]],
        "description": "Pauli-Y gate -- rotation around Y-axis",
        "qubits": 1,
    },
    "Z": {
        "matrix": [[1, 0], [0, -1]],
        "description": "Pauli-Z gate -- phase flip on |1>",
        "qubits": 1,
    },
    "H": {
        "matrix": [[1.0 / math.sqrt(2), 1.0 / math.sqrt(2)], [1.0 / math.sqrt(2), -1.0 / math.sqrt(2)]],
        "description": "Hadamard gate -- creates superposition",
        "qubits": 1,
    },
    "S": {
        "matrix": [[1, 0], [0, complex(0, 1)]],
        "description": "Phase gate (sqrt of Z)",
        "qubits": 1,
    },
    "T": {
        "matrix": [[1, 0], [0, cmath.exp(complex(0, math.pi / 4))]],
        "description": "T gate (pi/8 gate)",
        "qubits": 1,
    },
    "CNOT": {
        "matrix": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1], [0, 0, 1, 0]],
        "description": "Controlled-NOT -- flips target if control is |1>",
        "qubits": 2,
    },
    "SWAP": {
        "matrix": [[1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1]],
        "description": "SWAP gate -- exchanges two qubit states",
        "qubits": 2,
    },
    "CZ": {
        "matrix": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, -1]],
        "description": "Controlled-Z gate",
        "qubits": 2,
    },
}

BELL_STATES: dict[str, list[complex]] = {
    "Phi+": [1.0 / math.sqrt(2), 0.0, 0.0, 1.0 / math.sqrt(2)],
    "Phi-": [1.0 / math.sqrt(2), 0.0, 0.0, -1.0 / math.sqrt(2)],
    "Psi+": [0.0, 1.0 / math.sqrt(2), 1.0 / math.sqrt(2), 0.0],
    "Psi-": [0.0, 1.0 / math.sqrt(2), -1.0 / math.sqrt(2), 0.0],
}

QUANTUM_ALGORITHMS: dict[str, dict[str, Any]] = {
    "shors": {
        "description": "Shor's algorithm for integer factorization",
        "complexity": "O((log N)^3)",
        "classical_best": "sub-exponential via general number field sieve",
        "gates_required": ["H", "QFT", "controlled-U"],
        "applications": ["breaking RSA encryption", "discrete logarithm"],
    },
    "grovers": {
        "description": "Grover's search algorithm for unstructured search",
        "complexity": "O(sqrt(N))",
        "classical_best": "O(N)",
        "gates_required": ["H", "oracle", "diffusion"],
        "applications": ["database search", "collision finding", "NP-complete speedup"],
    },
    "qft": {
        "description": "Quantum Fourier Transform",
        "complexity": "O((log N)^2)",
        "classical_best": "O(N log N) via FFT",
        "gates_required": ["H", "controlled-R_k"],
        "applications": ["phase estimation", "period finding", "Shor's subroutine"],
    },
    "vqe": {
        "description": "Variational Quantum Eigensolver for near-term chemistry",
        "complexity": "heuristic (NISQ-era)",
        "classical_best": "exact diagonalization O(2^N)",
        "gates_required": ["Ry", "Rz", "CNOT", "parameterized"],
        "applications": ["molecular energies", "material science", "optimization"],
    },
}


def qubit_apply_gate(state: list[complex], gate_name: str) -> list[complex]:
    """Apply a single-qubit gate to state."""
    gate = QUANTUM_GATES[gate_name]
    matrix = gate["matrix"]
    return _apply_matrix_1q(state, matrix)


def _apply_matrix_1q(state: list[complex], matrix: list[list[complex]]) -> list[complex]:
    result: list[complex] = []
    for row in matrix:
        val: complex = 0 + 0j
        for j, c in enumerate(state):
            val += row[j] * c
        result.append(val)
    return result


def qubit_entangle(state1: list[complex], state2: list[complex]) -> list[complex]:
    """Create entangled state: H on state1, then CNOT with state2 as target."""
    superposition = qubit_apply_gate(state1, "H")
    tensor: list[complex] = []
    for a in superposition:
        for b in state2:
            tensor.append(a * b)
    return _apply_matrix_2q(tensor, QUANTUM_GATES["CNOT"]["matrix"])


def _apply_matrix_2q(state: list[complex], matrix: list[list[complex]]) -> list[complex]:
    n = len(matrix)
    result: list[complex] = []
    for row in matrix:
        val: complex = 0 + 0j
        for j in range(n):
            val += row[j] * state[j]
        result.append(val)
    return result


def measure_qubit(state: list[complex]) -> tuple[int, list[complex]]:
    """Simulate measurement -- collapse to |0> or |1> with Born rule probabilities."""
    p0 = abs(state[0]) ** 2
    import random
    outcome = 0 if random.random() < p0 else 1
    collapsed = [0.0 + 0.0j] * len(state)
    collapsed[outcome] = 1.0 + 0.0j
    return outcome, collapsed


# ═══════════════════════════════════════════════════════════════════
# Standard Model particle physics
# ═══════════════════════════════════════════════════════════════════

STANDARD_MODEL_PARTICLES: dict[str, dict[str, Any]] = {
    # Quarks
    "up": {
        "symbol": "u",
        "type": "quark",
        "generation": 1,
        "charge": 2.0 / 3.0,
        "mass_mev": 2.2,
        "spin": 0.5,
        "color_charge": "triplet",
        "baryon_number": 1.0 / 3.0,
        "antiparticle": "anti-up",
    },
    "down": {
        "symbol": "d",
        "type": "quark",
        "generation": 1,
        "charge": -1.0 / 3.0,
        "mass_mev": 4.7,
        "spin": 0.5,
        "color_charge": "triplet",
        "baryon_number": 1.0 / 3.0,
        "antiparticle": "anti-down",
    },
    "charm": {
        "symbol": "c",
        "type": "quark",
        "generation": 2,
        "charge": 2.0 / 3.0,
        "mass_mev": 1275.0,
        "spin": 0.5,
        "color_charge": "triplet",
        "baryon_number": 1.0 / 3.0,
        "antiparticle": "anti-charm",
    },
    "strange": {
        "symbol": "s",
        "type": "quark",
        "generation": 2,
        "charge": -1.0 / 3.0,
        "mass_mev": 93.0,
        "spin": 0.5,
        "color_charge": "triplet",
        "baryon_number": 1.0 / 3.0,
        "antiparticle": "anti-strange",
    },
    "top": {
        "symbol": "t",
        "type": "quark",
        "generation": 3,
        "charge": 2.0 / 3.0,
        "mass_mev": 173.1e3,  # 173.1 GeV
        "spin": 0.5,
        "color_charge": "triplet",
        "baryon_number": 1.0 / 3.0,
        "antiparticle": "anti-top",
    },
    "bottom": {
        "symbol": "b",
        "type": "quark",
        "generation": 3,
        "charge": -1.0 / 3.0,
        "mass_mev": 4180.0,
        "spin": 0.5,
        "color_charge": "triplet",
        "baryon_number": 1.0 / 3.0,
        "antiparticle": "anti-bottom",
    },
    # Leptons
    "electron": {
        "symbol": "e",
        "type": "lepton",
        "generation": 1,
        "charge": -1.0,
        "mass_mev": 0.511,
        "spin": 0.5,
        "lepton_number": 1.0,
        "antiparticle": "positron",
    },
    "electron_neutrino": {
        "symbol": "nu_e",
        "type": "lepton",
        "generation": 1,
        "charge": 0.0,
        "mass_mev": 0.0,  # effectively massless (< 2 eV)
        "spin": 0.5,
        "lepton_number": 1.0,
        "antiparticle": "electron_antineutrino",
    },
    "muon": {
        "symbol": "mu",
        "type": "lepton",
        "generation": 2,
        "charge": -1.0,
        "mass_mev": 105.66,
        "spin": 0.5,
        "lepton_number": 1.0,
        "antiparticle": "antimuon",
    },
    "muon_neutrino": {
        "symbol": "nu_mu",
        "type": "lepton",
        "generation": 2,
        "charge": 0.0,
        "mass_mev": 0.0,
        "spin": 0.5,
        "lepton_number": 1.0,
        "antiparticle": "muon_antineutrino",
    },
    "tau": {
        "symbol": "tau",
        "type": "lepton",
        "generation": 3,
        "charge": -1.0,
        "mass_mev": 1776.86,
        "spin": 0.5,
        "lepton_number": 1.0,
        "antiparticle": "antitau",
    },
    "tau_neutrino": {
        "symbol": "nu_tau",
        "type": "lepton",
        "generation": 3,
        "charge": 0.0,
        "mass_mev": 0.0,
        "spin": 0.5,
        "lepton_number": 1.0,
        "antiparticle": "tau_antineutrino",
    },
    # Gauge bosons
    "photon": {
        "symbol": "gamma",
        "type": "boson",
        "charge": 0.0,
        "mass_mev": 0.0,
        "spin": 1.0,
        "force": "electromagnetic",
        "mediator_of": "electromagnetic interaction",
    },
    "gluon": {
        "symbol": "g",
        "type": "boson",
        "charge": 0.0,
        "mass_mev": 0.0,
        "spin": 1.0,
        "force": "strong",
        "mediator_of": "strong interaction",
        "color_states": 8,
    },
    "w_boson": {
        "symbol": "W+/-",
        "type": "boson",
        "charge": 1.0,
        "mass_mev": 80.377e3,
        "spin": 1.0,
        "force": "weak",
        "mediator_of": "charged-current weak interaction",
    },
    "z_boson": {
        "symbol": "Z",
        "type": "boson",
        "charge": 0.0,
        "mass_mev": 91.1876e3,
        "spin": 1.0,
        "force": "weak",
        "mediator_of": "neutral-current weak interaction",
    },
    # Higgs boson
    "higgs": {
        "symbol": "H",
        "type": "boson",
        "charge": 0.0,
        "mass_mev": 125.25e3,
        "spin": 0.0,
        "force": "higgs",
        "mediator_of": "mass generation via electroweak symmetry breaking",
    },
}

# Composite particles
COMPOSITE_PARTICLES: dict[str, dict[str, Any]] = {
    "proton": {
        "symbol": "p",
        "type": "baryon",
        "quark_content": "uud",
        "charge": 1.0,
        "mass_mev": 938.272,
        "spin": 0.5,
        "lifetime_s": float("inf"),  # stable (or > 1e34 yr)
        "baryon_number": 1.0,
    },
    "neutron": {
        "symbol": "n",
        "type": "baryon",
        "quark_content": "udd",
        "charge": 0.0,
        "mass_mev": 939.565,
        "spin": 0.5,
        "lifetime_s": 879.4,
        "baryon_number": 1.0,
    },
    "pion_plus": {
        "symbol": "pi+",
        "type": "meson",
        "quark_content": "u anti-d",
        "charge": 1.0,
        "mass_mev": 139.570,
        "spin": 0.0,
        "lifetime_s": 2.603e-8,
    },
    "pion_minus": {
        "symbol": "pi-",
        "type": "meson",
        "quark_content": "d anti-u",
        "charge": -1.0,
        "mass_mev": 139.570,
        "spin": 0.0,
        "lifetime_s": 2.603e-8,
    },
    "pion_zero": {
        "symbol": "pi0",
        "type": "meson",
        "quark_content": "(u anti-u - d anti-d)/sqrt(2)",
        "charge": 0.0,
        "mass_mev": 134.977,
        "spin": 0.0,
        "lifetime_s": 8.52e-17,
    },
    "kaon_plus": {
        "symbol": "K+",
        "type": "meson",
        "quark_content": "u anti-s",
        "charge": 1.0,
        "mass_mev": 493.677,
        "spin": 0.0,
        "lifetime_s": 1.238e-8,
    },
    "kaon_zero": {
        "symbol": "K0",
        "type": "meson",
        "quark_content": "d anti-s",
        "charge": 0.0,
        "mass_mev": 497.614,
        "spin": 0.0,
        "lifetime_s": 8.954e-11,
    },
}

# Decay modes from PDG
PARTICLE_DECAY_MODES: dict[str, list[dict[str, Any]]] = {
    "neutron": [
        {"mode": "p + e- + nu_e_bar", "branching_ratio": 1.0, "interaction": "weak"},
    ],
    "muon": [
        {"mode": "e- + nu_e_bar + nu_mu", "branching_ratio": 1.0, "interaction": "weak"},
    ],
    "tau": [
        {"mode": "mu- + nu_mu_bar + nu_tau", "branching_ratio": 0.174, "interaction": "weak"},
        {"mode": "e- + nu_e_bar + nu_tau", "branching_ratio": 0.178, "interaction": "weak"},
        {"mode": "hadrons + nu_tau", "branching_ratio": 0.648, "interaction": "weak"},
    ],
    "pion_plus": [
        {"mode": "mu+ + nu_mu", "branching_ratio": 0.999877, "interaction": "weak"},
        {"mode": "e+ + nu_e", "branching_ratio": 1.23e-4, "interaction": "weak"},
    ],
    "pion_zero": [
        {"mode": "gamma + gamma", "branching_ratio": 0.98823, "interaction": "electromagnetic"},
        {"mode": "gamma + e+ + e-", "branching_ratio": 0.01174, "interaction": "electromagnetic"},
    ],
    "kaon_plus": [
        {"mode": "mu+ + nu_mu", "branching_ratio": 0.6356, "interaction": "weak"},
        {"mode": "pi+ + pi0", "branching_ratio": 0.2067, "interaction": "weak"},
        {"mode": "pi+ + pi+ + pi-", "branching_ratio": 0.0559, "interaction": "weak"},
        {"mode": "pi0 + e+ + nu_e", "branching_ratio": 0.0507, "interaction": "weak"},
    ],
    "w_boson": [
        {"mode": "lepton + neutrino (each)", "branching_ratio": 0.1086, "interaction": "weak"},
        {"mode": "hadrons", "branching_ratio": 0.6741, "interaction": "weak"},
    ],
    "z_boson": [
        {"mode": "charged leptons (e+e-, mu+mu-, tau+tau-)", "branching_ratio": 0.03366, "interaction": "weak"},
        {"mode": "neutrinos (invisible)", "branching_ratio": 0.2000, "interaction": "weak"},
        {"mode": "hadrons", "branching_ratio": 0.6991, "interaction": "weak"},
    ],
    "top": [
        {"mode": "W+ + b", "branching_ratio": 1.0, "interaction": "weak"},
    ],
    "higgs": [
        {"mode": "b + anti-b", "branching_ratio": 0.584, "interaction": "higgs"},
        {"mode": "W+ + W-", "branching_ratio": 0.215, "interaction": "higgs"},
        {"mode": "gluon + gluon", "branching_ratio": 0.086, "interaction": "higgs"},
        {"mode": "tau+ + tau-", "branching_ratio": 0.063, "interaction": "higgs"},
        {"mode": "Z + Z", "branching_ratio": 0.026, "interaction": "higgs"},
        {"mode": "gamma + gamma", "branching_ratio": 0.0023, "interaction": "higgs"},
    ],
}

# Approximate cross-sections for common processes (in barns, 1 barn = 1e-28 m^2)
CROSS_SECTIONS: dict[str, dict[str, Any]] = {
    "pp_total_13TeV": {
        "process": "p + p -> X at sqrt(s) = 13 TeV",
        "cross_section_mb": 110.0,
        "notes": "Total inelastic pp cross-section at LHC Run 2",
    },
    "pp_H_13TeV": {
        "process": "p + p -> H + X at sqrt(s) = 13 TeV",
        "cross_section_pb": 55.6e3,  # 55.6 nb
        "notes": "Gluon-gluon fusion dominates",
    },
    "pp_ttbar_13TeV": {
        "process": "p + p -> t + tbar + X at sqrt(s) = 13 TeV",
        "cross_section_pb": 832.0,  # 832 pb
        "notes": "NNLO+NNLL prediction",
    },
    "ee_Z_91GeV": {
        "process": "e+ + e- -> Z at sqrt(s) = 91.2 GeV",
        "cross_section_nb": 30.5,  # 30.5 nb at Z pole
        "notes": "LEP/SLC Z-pole measurement",
    },
    "pp_ZH_13TeV": {
        "process": "p + p -> Z + H at sqrt(s) = 13 TeV",
        "cross_section_pb": 0.88,
        "notes": "Associated Higgs production (ZH)",
    },
    "nu_e": {
        "process": "nu + e -> nu + e",
        "cross_section_cm2_per_GeV": 1.7e-41,
        "notes": "Neutrino-electron elastic scattering",
    },
}


def get_particle_data(name: str) -> dict[str, Any] | None:
    """Get Standard Model or composite particle data by name."""
    if name in STANDARD_MODEL_PARTICLES:
        return dict(STANDARD_MODEL_PARTICLES[name])
    if name in COMPOSITE_PARTICLES:
        return dict(COMPOSITE_PARTICLES[name])
    return None


def get_decay_modes(particle: str) -> list[dict[str, Any]]:
    """Get known decay modes for a particle."""
    return PARTICLE_DECAY_MODES.get(particle, [])


def get_particles_by_type(particle_type: str) -> list[str]:
    """List all particles of a given type (quark, lepton, boson, baryon, meson)."""
    result: list[str] = []
    for name, data in STANDARD_MODEL_PARTICLES.items():
        if data.get("type") == particle_type:
            result.append(name)
    for name, data in COMPOSITE_PARTICLES.items():
        if data.get("type") == particle_type:
            result.append(name)
    return result


def get_cross_section(process: str) -> dict[str, Any] | None:
    """Get approximate cross-section data for a named process."""
    return CROSS_SECTIONS.get(process)


# ═══════════════════════════════════════════════════════════════════
# Feynman diagram construction
# ═══════════════════════════════════════════════════════════════════

FEYNMAN_VERTICES: dict[str, dict[str, Any]] = {
    "QED_vertex": {
        "particles": ["fermion", "fermion", "photon"],
        "coupling": "e (electric charge)",
        "strength": ALPHA,
        "description": "Electron-photon vertex: -ie * gamma^mu",
    },
    "gluon_vertex": {
        "particles": ["quark", "quark", "gluon"],
        "coupling": "g_s (strong coupling)",
        "strength": 0.118,
        "description": "Quark-gluon vertex: -ig_s * T^a * gamma^mu",
    },
    "triple_gluon": {
        "particles": ["gluon", "gluon", "gluon"],
        "coupling": "g_s (strong coupling)",
        "strength": 0.118,
        "description": "Triple-gluon vertex (non-Abelian QCD)",
    },
    "W_fermion": {
        "particles": ["fermion", "fermion'", "W"],
        "coupling": "g_W (weak coupling)",
        "strength": ALPHA / (0.23122),
        "description": "Charged-current weak vertex: -ig_W/2 * gamma^mu * (1-gamma^5) * V_ij",
    },
    "Z_fermion": {
        "particles": ["fermion", "fermion", "Z"],
        "coupling": "g_Z (weak neutral coupling)",
        "strength": ALPHA / (0.23122 * 0.76878),
        "description": "Neutral-current weak vertex",
    },
    "higgs_fermion": {
        "particles": ["fermion", "fermion", "higgs"],
        "coupling": "m_f / v (Yukawa)",
        "strength": "proportional to fermion mass",
        "description": "Yukawa coupling: fermion mass / Higgs vev",
    },
    "higgs_WW": {
        "particles": ["W", "W", "higgs"],
        "coupling": "g * M_W",
        "strength": "proportional to W mass",
        "description": "Higgs-WW vertex from electroweak symmetry breaking",
    },
}

FEYNMAN_PROPAGATORS: dict[str, dict[str, str]] = {
    "fermion": "i * (gamma^mu * p_mu + m) / (p^2 - m^2 + i*epsilon)",
    "photon": "-i * g^{mu nu} / (p^2 + i*epsilon)",
    "massive_boson": "-i * (g^{mu nu} - p^mu * p^nu / M^2) / (p^2 - M^2 + i*epsilon)",
    "gluon": "-i * delta^{ab} * g^{mu nu} / (p^2 + i*epsilon)",
    "higgs": "i / (p^2 - m_H^2 + i*epsilon)",
}


def feynman_diagram(process: str) -> dict[str, Any] | None:
    """Construct a Feynman diagram description for a given process.

    Supported processes:
        ee_to_mumu, ee_to_qq, gg_to_H, qq_to_ZH, H_to_ZZ, H_to_gammagamma,
        H_to_bb, top_decay, Z_to_ll, W_to_lnu, gg_to_ttbar
    """
    DIAGRAMS: dict[str, dict[str, Any]] = {
        "ee_to_mumu": {
            "initial_state": ["e-", "e+"],
            "final_state": ["mu-", "mu+"],
            "vertices": [
                {"vertex": "QED_vertex", "particles_in": ["e-"], "particles_out": ["mu-"], "mediator": "photon"},
            ],
            "interaction": "electromagnetic (s-channel)",
            "propagator": "photon",
            "amplitude": "proportional to -i * e^2 / s",
            "cross_section_approx": "sigma ~ 4*pi*alpha^2 / (3*s) at high energy",
        },
        "ee_to_qq": {
            "initial_state": ["e-", "e+"],
            "final_state": ["q", "qbar"],
            "vertices": [
                {"vertex": "QED_vertex", "particles_in": ["e-", "e+"], "particles_out": ["q", "qbar"], "mediator": "photon"},
            ],
            "interaction": "electromagnetic (s-channel)",
            "propagator": "photon",
            "amplitude": "proportional to -i * e^2 * e_q / s",
        },
        "gg_to_H": {
            "initial_state": ["g", "g"],
            "final_state": ["H"],
            "vertices": [
                {"vertex": "higgs_fermion", "particles_in": ["t"], "particles_out": ["t"], "mediator": "higgs"},
                {"vertex": "gluon_vertex", "particles_in": ["t"], "particles_out": ["t"], "mediator": "gluon"},
            ],
            "interaction": "strong + Yukawa (top-quark loop)",
            "propagator": "top quark (loop)",
            "dominant_loop": "top quark",
            "notes": "Dominant Higgs production mode at the LHC",
        },
        "qq_to_ZH": {
            "initial_state": ["q", "qbar"],
            "final_state": ["Z", "H"],
            "vertices": [
                {"vertex": "Z_fermion", "particles_in": ["q", "qbar"], "particles_out": ["Z*"], "mediator": "Z"},
            ],
            "interaction": "weak neutral current (s-channel, Higgs-strahlung)",
            "propagator": "Z boson",
        },
        "H_to_ZZ": {
            "initial_state": ["H"],
            "final_state": ["Z", "Z"],
            "vertices": [
                {"vertex": "higgs_WW", "particles_in": ["H"], "particles_out": ["Z*"], "mediator": "Z"},
            ],
            "interaction": "higgs",
            "propagator": "Z boson (one off-shell at m_H = 125 GeV)",
            "notes": "Golden channel for Higgs discovery at LHC (H -> ZZ -> 4l)",
        },
        "H_to_gammagamma": {
            "initial_state": ["H"],
            "final_state": ["gamma", "gamma"],
            "vertices": [
                {"vertex": "higgs_fermion", "particles_in": ["t", "W"], "particles_out": ["t", "W"], "mediator": "higgs"},
            ],
            "interaction": "electromagnetic (loop-induced, effectively)",
            "propagator": "fermion loop and W loop",
            "notes": "Rare decay (BR ~ 0.23%) but clean signature; discovery channel",
        },
        "H_to_bb": {
            "initial_state": ["H"],
            "final_state": ["b", "bbar"],
            "vertices": [
                {"vertex": "higgs_fermion", "particles_in": ["H"], "particles_out": ["b"], "mediator": "higgs"},
            ],
            "interaction": "higgs (Yukawa)",
            "propagator": "higgs",
            "notes": "Dominant decay mode (BR ~ 58%)",
        },
        "top_decay": {
            "initial_state": ["t"],
            "final_state": ["W+", "b"],
            "vertices": [
                {"vertex": "W_fermion", "particles_in": ["t"], "particles_out": ["b"], "mediator": "W+"},
            ],
            "interaction": "weak charged current",
            "propagator": "W boson",
            "notes": "Top decays to Wb ~100% of the time (|V_tb| ~ 1)",
        },
        "Z_to_ll": {
            "initial_state": ["Z"],
            "final_state": ["l-", "l+"],
            "vertices": [
                {"vertex": "Z_fermion", "particles_in": ["Z"], "particles_out": ["l-", "l+"], "mediator": "Z"},
            ],
            "interaction": "weak neutral current",
            "propagator": "Z boson",
        },
        "W_to_lnu": {
            "initial_state": ["W+"],
            "final_state": ["l+", "nu_l"],
            "vertices": [
                {"vertex": "W_fermion", "particles_in": ["W+"], "particles_out": ["l+", "nu_l"], "mediator": "W"},
            ],
            "interaction": "weak charged current",
            "propagator": "W boson",
        },
        "gg_to_ttbar": {
            "initial_state": ["g", "g"],
            "final_state": ["t", "tbar"],
            "vertices": [
                {"vertex": "gluon_vertex", "particles_in": ["g"], "particles_out": ["t"], "mediator": "gluon"},
            ],
            "interaction": "strong (s/t/u-channel, and s-channel gluon)",
            "propagator": "gluon",
        },
    }
    return DIAGRAMS.get(process)


def compute_cross_section(process: str, energy_gev: float) -> dict[str, Any] | None:
    """Compute approximate cross-section for a process at given center-of-mass energy.

    Returns dict with cross_section_pb, units, notes.
    """
    if process == "ee_to_mumu":
        s = energy_gev * energy_gev
        sigma_pb = 4.0 * math.pi * (ALPHA ** 2) / (3.0 * s) * 3.894e8
        return {
            "process": "e+e- -> mu+mu-",
            "sqrt_s_gev": energy_gev,
            "cross_section_pb": round(sigma_pb, 3),
            "units": "pb",
            "notes": "Leading-order QED; Z exchange dominates near Z pole",
        }
    if process == "pp_total":
        s_tev = energy_gev / 1000.0
        sigma_mb = 35.5 + 0.308 * (math.log(s_tev) ** 2)
        return {
            "process": "pp total inelastic",
            "sqrt_s_tev": s_tev,
            "cross_section_mb": round(sigma_mb, 1),
            "units": "mb",
            "notes": "Donnachie-Landshoff fit to pp/pbar total cross-sections",
        }
    return get_cross_section(process)
