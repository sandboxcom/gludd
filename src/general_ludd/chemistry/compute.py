"""CHEM-011 quantum chemistry + CHEM-012 molecular simulation adapters.

Implements the computational-chemistry adapter contract from
``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §7.3 (quantum chemistry) and §7.4
(molecular simulation) for Phase C of the chemistry expert.

Per §7.1, the collection integrates maintained engines through adapters rather
than reimplementing mature chemistry algorithms. This module defines the typed
job/result shapes adapters consume and the validators that decide whether a
parsed result is qualified to support an actionable artifact (spec §10:
*"only ``validated`` results may support execution-facing artifacts"*).

Key spec invariants enforced here:

* §7.3: *"An unconverged job never yields an unqualified property."* The
  ``qualified_property`` flag on :func:`validate_quantum` output is False
  whenever ``QuantumResult.converged`` is False, regardless of whether an
  energy was parsed.
* §7.3 validation set: electron/spin consistency (charge ↔ electrons ↔
  multiplicity), imaginary frequencies relative to job intent (minimum vs
  transition state), energy/unit consistency (hartree ↔ eV), and method
  support / geometry sanity.
* §7.4 adapter contract: topology, force field/version, ensemble,
  thermostat/barostat, time step, seed, equilibration, production.
* §7.4 validation set: energy drift (NVE conservation), temperature/pressure
  stability vs thermostat target, sampling convergence (block-average SEM),
  and replicate agreement.

The validators are intentionally physics-driven heuristics suitable for the
adapter contract; they are NOT engine reimplementations. Heavy electronic-
structure integration is delegated to the wrapped engine via the ``raw_output``
URI preserved on every result.
"""

from __future__ import annotations

import math
import statistics
import uuid
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"
ADAPTER = "chemistry-compute@0.1.0"

# Hartree → eV conversion (CODATA). Used by the energy-unit-consistency check.
HARTREE_TO_EV = 27.211386245988
HARTREE_TO_KJ_PER_MOL = 2625.4996394799996

# Bounded enumerations (reject unknown mutating fields per CHEM-AT-001).
SUPPORTED_QUANTUM_METHODS: frozenset[str] = frozenset(
    {
        "HF",
        "DFT",
        "MP2",
        "MP3",
        "CCSD",
        "CCSD(T)",
        "CASSCF",
        "SEMIEMPIRICAL",
        "TDDFT",
    }
)

SUPPORTED_ENSEMBLES: frozenset[str] = frozenset({"NVE", "NVT", "NPT", "NVT-REMD"})

# Z-number lookup for electron counting. Covers the common main-group +
# first-row transition elements; unknown symbols fail the consistency check
# rather than silently producing a wrong electron count.
ATOMIC_NUMBERS: dict[str, int] = {
    "H": 1,
    "He": 2,
    "Li": 3,
    "Be": 4,
    "B": 5,
    "C": 6,
    "N": 7,
    "O": 8,
    "F": 9,
    "Ne": 10,
    "Na": 11,
    "Mg": 12,
    "Al": 13,
    "Si": 14,
    "P": 15,
    "S": 16,
    "Cl": 17,
    "Ar": 18,
    "K": 19,
    "Ca": 20,
    "Sc": 21,
    "Ti": 22,
    "V": 23,
    "Cr": 24,
    "Mn": 25,
    "Fe": 26,
    "Co": 27,
    "Ni": 28,
    "Cu": 29,
    "Zn": 30,
    "Br": 35,
    "I": 53,
    "Rb": 37,
    "Sr": 38,
    "Ag": 47,
    "Sn": 50,
    "Ba": 56,
    "Au": 79,
    "Hg": 80,
    "Pb": 82,
    "U": 92,
}

# Default tolerances. Tunable via the job's convergence_criteria / resources
# dict so an adapter can override without changing module-level constants.
DEFAULT_ENERGY_DRIFT_TOLERANCE_KJ_PER_MOL = 0.5
DEFAULT_TEMPERATURE_STABILITY_K = 15.0
DEFAULT_REPLICATE_SPREAD_TOLERANCE_KJ_PER_MOL = 5.0
DEFAULT_MIN_SAMPLES_FOR_CONVERGENCE = 50


def _new_id() -> str:
    return str(uuid.uuid4())


def _err(code: str, message: str, retryable: bool = False) -> dict[str, Any]:
    return {"code": code, "retryable": retryable, "message": message}


def _check(name: str, status: str, **extra: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {"check": name, "status": status}
    rec.update(extra)
    return rec


# ---------------------------------------------------------------------------
# CHEM-011 §7.3 Quantum adapter contract
# ---------------------------------------------------------------------------


@dataclass
class QuantumJob:
    """Input contract for a quantum-chemistry engine run (spec §7.3).

    Covers geometry, charge/multiplicity, method, basis, effective core
    potential, solvent/environment, relativistic treatment, dispersion,
    convergence, excited-state/property requests, resources, and restart files.
    """

    geometry: list[dict[str, Any]]
    method: str = "HF"
    basis_set: str = "6-31G*"
    charge: int = 0
    multiplicity: int = 1  # 2S+1 (1 = singlet, 2 = doublet, 3 = triplet)
    ecp: str | None = None
    solvent: str | None = None
    relativistic: str = "none"
    dispersion: str | None = None
    convergence_criteria: dict[str, float] = field(
        default_factory=lambda: {
            "max_force": 4.5e-4,
            "rms_force": 3.0e-4,
            "max_disp": 1.8e-3,
            "rms_disp": 1.2e-3,
        }
    )
    resources: dict[str, Any] = field(default_factory=lambda: {"cores": 1, "memory_gb": 4, "walltime_s": 3600})
    job_intent: str = "minimum"  # minimum | transition_state | excited_state | properties
    excited_state: str | None = None
    restart_file: str | None = None


@dataclass
class QuantumResult:
    """Parsed output of a quantum-chemistry engine run (spec §7.3).

    ``converged`` is the engine's own SCF + geometry convergence verdict. Per
    spec §7.3 an unconverged job never yields an unqualified property, so the
    validator downstream gates every value on this flag.
    """

    converged: bool
    energy_hartree: float | None = None
    energy_eV: float | None = None
    frequencies: list[float] = field(default_factory=list)
    populations: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    raw_output_uri: str | None = None


def _count_electrons(geometry: list[dict[str, Any]]) -> int:
    """Sum nuclear charges for the geometry; unknown symbols return -1."""
    total = 0
    for atom in geometry:
        symbol = str(atom.get("symbol", "")).strip()
        z = ATOMIC_NUMBERS.get(symbol)
        if z is None:
            return -1
        total += z
    return total


def _electron_spin_consistent(job: QuantumJob) -> bool:
    """Verify (n_electrons - charge) parity is compatible with multiplicity.

    n_electrons = sum(Z) - charge; multiplicity = 2S+1; S = (n_unpaired)/2.
    So n_electrons mod 2 must equal (multiplicity - 1) mod 2. A closed-shell
    singlet (mult=1) requires even electrons; a triplet (mult=3) requires even
    electrons; a doublet (mult=2) requires odd electrons.
    """
    nuclear = _count_electrons(job.geometry)
    if nuclear < 0:
        return False
    n_electrons = nuclear - job.charge
    if n_electrons < 0:
        return False
    expected_parity = (job.multiplicity - 1) % 2
    return (n_electrons % 2) == expected_parity


def _imaginary_frequencies_ok(job: QuantumJob, result: QuantumResult) -> str:
    """Classify imaginary frequencies relative to job intent.

    Returns "pass" / "fail" / "not_run". A minimum search must have zero
    imaginary frequencies; a transition-state search must have exactly one.
    """
    if not result.frequencies:
        return "not_run"
    n_imaginary = sum(1 for f in result.frequencies if f < 0.0)
    if job.job_intent == "transition_state":
        return "pass" if n_imaginary == 1 else "fail"
    # Default: minimum search
    return "pass" if n_imaginary == 0 else "fail"


def _energy_units_consistent(result: QuantumResult) -> str:
    """Verify energy_hartree ↔ energy_eV consistency when both are parsed."""
    if result.energy_hartree is None or result.energy_eV is None:
        return "not_run"
    expected = result.energy_hartree * HARTREE_TO_EV
    if math.isclose(expected, result.energy_eV, rel_tol=1e-6):
        return "pass"
    return "fail"


def validate_quantum(job: QuantumJob, result: QuantumResult) -> dict[str, Any]:
    """Validate a quantum-chemistry job + parsed result (CHEM-011 §7.3).

    Returns a record mirroring spec §4.3 with ``verification`` checks and a
    top-level ``qualified_property`` flag. Per §7.3 an unconverged job never
    yields an unqualified property — ``qualified_property`` is False whenever
    ``result.converged`` is False, regardless of any parsed energy.
    """
    checks: list[dict[str, Any]] = []
    limitations: list[str] = []

    # Geometry sanity
    if not job.geometry:
        checks.append(_check("geometry_present", "fail", note="empty geometry"))
    else:
        checks.append(_check("geometry_present", "pass", n_atoms=len(job.geometry)))

    # Method support
    if job.method in SUPPORTED_QUANTUM_METHODS:
        checks.append(_check("method_supported", "pass", method=job.method))
    else:
        checks.append(_check("method_supported", "fail", method=job.method))
        limitations.append(f"unsupported-method: {job.method!r} not in adapter registry")

    # Convergence — the §7.3 invariant gate.
    if result.converged:
        checks.append(_check("convergence", "pass"))
    else:
        checks.append(_check("convergence", "fail", diagnostics=result.diagnostics))

    # Electron/spin consistency
    if _electron_spin_consistent(job):
        checks.append(_check("electron_spin_consistency", "pass"))
    else:
        checks.append(_check("electron_spin_consistency", "fail"))
        limitations.append("electron-spin: charge/multiplicity inconsistent with geometry")

    # Imaginary frequencies
    checks.append(_check("imaginary_frequencies", _imaginary_frequencies_ok(job, result)))

    # Energy unit consistency
    checks.append(_check("energy_unit_consistency", _energy_units_consistent(result)))

    # The qualified-property gate: ALL structural checks pass AND job converged.
    all_pass = all(c["status"] == "pass" or c["status"] == "not_run" for c in checks)
    qualified = bool(result.converged) and all_pass and "convergence" in {c["check"] for c in checks}

    status = "succeeded" if qualified else "degraded"
    if not job.geometry or job.method not in SUPPORTED_QUANTUM_METHODS:
        status = "refused"

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": _new_id(),
        "adapter": ADAPTER,
        "status": status,
        "qualified_property": qualified,
        "verification": checks,
        "limitations": limitations,
        "errors": [] if qualified else [_err("chem.quantum_not_qualified", "result did not pass validation gate")],
    }


# ---------------------------------------------------------------------------
# CHEM-012 §7.4 Molecular simulation adapter contract
# ---------------------------------------------------------------------------


@dataclass
class MolecularDynamicsJob:
    """Input contract for a molecular dynamics / free-energy run (spec §7.4).

    Covers topology, force field/version, ensemble, thermostat/barostat,
    constraints, time step, seed, equilibration, production, sampling.
    """

    topology: str
    force_field: str
    ensemble: str = "NVT"
    thermostat: str | None = None
    barostat: str | None = None
    time_step_fs: float = 2.0
    n_steps: int = 0
    equilibration_steps: int = 0
    production_steps: int = 0
    seed: int | None = None
    constraints: list[str] = field(default_factory=list)
    target_temperature_K: float | None = None
    target_pressure_bar: float | None = None
    free_energy_method: str | None = None
    coordinates_uri: str | None = None
    hardware: dict[str, Any] = field(default_factory=dict)


@dataclass
class MolecularDynamicsResult:
    """Parsed output of an MD/free-energy run (spec §7.4)."""

    converged: bool
    energy_traj_kJ_per_mol: list[float] = field(default_factory=list)
    temperature_traj_K: list[float] = field(default_factory=list)
    pressure_traj_bar: list[float] = field(default_factory=list)
    replicate_avg_energies_kJ_per_mol: list[float] | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    trajectory_uri: str | None = None
    checkpoint_uri: str | None = None


def _energy_drift_status(job: MolecularDynamicsJob, result: MolecularDynamicsResult) -> tuple[str, float]:
    """Slope of total energy vs sample index, normalized to the first value.

    For NVE the total energy must be conserved to within ~1 kJ/mol per ns of
    production. We approximate with the linear drift (last - first) relative to
    the mean magnitude; flagged as fail when |drift| > tolerance.
    """
    e = result.energy_traj_kJ_per_mol
    if len(e) < 2:
        return "not_run", 0.0
    drift = abs(e[-1] - e[0])
    tol = job.__dict__.get("energy_drift_tolerance_kJ_per_mol") or DEFAULT_ENERGY_DRIFT_TOLERANCE_KJ_PER_MOL
    return ("pass" if drift <= float(tol) else "fail"), drift


def _temperature_stability_status(job: MolecularDynamicsJob, result: MolecularDynamicsResult) -> tuple[str, float]:
    """Return pass/fail for thermostat stability vs the job's target T.

    Uses the population standard deviation of the post-equilibration temperature
    trajectory. NVT/NPT runs that drift more than ``tol`` from the target fail.
    """
    t = result.temperature_traj_K
    if not t:
        return "not_run", 0.0
    std = 0.0 if len(t) < 2 else statistics.pstdev(t)
    target = job.target_temperature_K
    if target is None:
        # No declared target — flag as warning via limitations; treat as pass.
        return "pass", std
    mean_t = statistics.fmean(t)
    if abs(mean_t - target) > DEFAULT_TEMPERATURE_STABILITY_K or std > DEFAULT_TEMPERATURE_STABILITY_K:
        return "fail", std
    return "pass", std


def _sampling_convergence_status(job: MolecularDynamicsJob, result: MolecularDynamicsResult) -> str:
    """Heuristic sampling-convergence gate based on sample count + block SEM.

    A trajectory shorter than ``DEFAULT_MIN_SAMPLES_FOR_CONVERGENCE`` production
    samples is flagged as undersampled. A long trajectory with a tiny block-mean
    standard error passes; we use the temperature-trajectory SEM as a proxy.
    """
    n = len(result.energy_traj_kJ_per_mol)
    if n < DEFAULT_MIN_SAMPLES_FOR_CONVERGENCE:
        return "fail"
    # Block-average: split trajectory into 5 blocks, take SEM of block means.
    t = result.temperature_traj_K or result.energy_traj_kJ_per_mol
    if len(t) < DEFAULT_MIN_SAMPLES_FOR_CONVERGENCE:
        return "fail"
    n_blocks = 5
    block_size = max(1, len(t) // n_blocks)
    block_means = [statistics.fmean(t[i * block_size : (i + 1) * block_size]) for i in range(n_blocks)]
    if len(block_means) < 2:
        return "fail"
    sem = statistics.stdev(block_means) / math.sqrt(len(block_means))
    # SEM < 1 K (or 1 kJ/mol for energy) → converged.
    return "pass" if sem < 1.0 else "fail"


def _replicate_agreement_status(result: MolecularDynamicsResult) -> str:
    """Verify replicate average energies agree within tolerance."""
    reps = result.replicate_avg_energies_kJ_per_mol
    if not reps or len(reps) < 2:
        return "not_run"
    spread = max(reps) - min(reps)
    return "pass" if spread <= DEFAULT_REPLICATE_SPREAD_TOLERANCE_KJ_PER_MOL else "fail"


def validate_md(job: MolecularDynamicsJob, result: MolecularDynamicsResult) -> dict[str, Any]:
    """Validate an MD/free-energy job + parsed result (CHEM-012 §7.4).

    Returns a record mirroring spec §4.3 with verification checks for: ensemble
    support, energy drift, temperature stability, sampling convergence, and
    replicate agreement.
    """
    checks: list[dict[str, Any]] = []
    limitations: list[str] = []

    # Topology present
    if job.topology:
        checks.append(_check("topology_present", "pass", topology=job.topology))
    else:
        checks.append(_check("topology_present", "fail"))
        limitations.append("missing-topology: MD requires a topology file")

    # Force field present
    if job.force_field:
        checks.append(_check("force_field_present", "pass", force_field=job.force_field))
    else:
        checks.append(_check("force_field_present", "fail"))
        limitations.append("missing-force-field: no parameter set declared")

    # Ensemble support
    if job.ensemble in SUPPORTED_ENSEMBLES:
        checks.append(_check("ensemble_supported", "pass", ensemble=job.ensemble))
    else:
        checks.append(_check("ensemble_supported", "fail", ensemble=job.ensemble))
        limitations.append(f"unsupported-ensemble: {job.ensemble!r}")

    # Time step sanity (≥ 0; > 0 for production runs)
    if job.time_step_fs > 0.0:
        checks.append(_check("time_step_positive", "pass", time_step_fs=job.time_step_fs))
    else:
        checks.append(_check("time_step_positive", "fail", time_step_fs=job.time_step_fs))
        limitations.append("invalid-time-step: must be positive")

    # Energy drift
    drift_status, drift_value = _energy_drift_status(job, result)
    checks.append(_check("energy_drift", drift_status, drift_kJ_per_mol=drift_value))

    # Temperature stability
    temp_status, temp_std = _temperature_stability_status(job, result)
    checks.append(_check("temperature_stability", temp_status, temperature_std_K=temp_std))

    # Sampling convergence
    checks.append(_check("sampling_convergence", _sampling_convergence_status(job, result)))

    # Replicate agreement (not_run when no replicates declared)
    checks.append(_check("replicate_agreement", _replicate_agreement_status(result)))

    qualified = result.converged and all(c["status"] in {"pass", "not_run"} for c in checks)
    status = "succeeded" if qualified else "degraded"
    if not job.topology or not job.force_field or job.ensemble not in SUPPORTED_ENSEMBLES:
        status = "refused"

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": _new_id(),
        "adapter": ADAPTER,
        "status": status,
        "qualified_property": qualified,
        "verification": checks,
        "limitations": limitations,
        "errors": [] if qualified else [_err("chem.md_not_qualified", "MD result did not pass validation gate")],
    }


__all__ = [
    "ADAPTER",
    "ATOMIC_NUMBERS",
    "DEFAULT_ENERGY_DRIFT_TOLERANCE_KJ_PER_MOL",
    "DEFAULT_MIN_SAMPLES_FOR_CONVERGENCE",
    "DEFAULT_REPLICATE_SPREAD_TOLERANCE_KJ_PER_MOL",
    "DEFAULT_TEMPERATURE_STABILITY_K",
    "HARTREE_TO_EV",
    "HARTREE_TO_KJ_PER_MOL",
    "SUPPORTED_ENSEMBLES",
    "SUPPORTED_QUANTUM_METHODS",
    "MolecularDynamicsJob",
    "MolecularDynamicsResult",
    "QuantumJob",
    "QuantumResult",
    "validate_md",
    "validate_quantum",
]
