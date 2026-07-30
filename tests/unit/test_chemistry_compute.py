"""Unit tests for ``general_ludd.chemistry.compute`` (CHEM Phase C).

Covers CHEM-011 (quantum chemistry) and CHEM-012 (molecular simulation)
adapter contracts from ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §7.3 and §7.4.
Maps to acceptance criteria CHEM-AT-012 (quantum reference cases verify parsed
units, convergence, and suite-pinned energies/geometries) and CHEM-AT-013
(molecular simulation fixtures verify topology, stability, restart, replicate,
and sampling diagnostics).

Key spec invariants under test:

* §7.3: "An unconverged job never yields an unqualified property."
* §7.3: validation covers electron/spin consistency, imaginary frequencies
  relative to job intent, energy/unit consistency.
* §7.4: workflow records topology, force field, ensemble, thermostat/barostat,
  time step, seed, equilibration, production.
* §7.4: validation covers energy drift, sampling/convergence, replicate
  agreement, temperature/pressure stability.

Module is loaded by file path (mirrors ``test_chemistry_thermo.py``) so the
suite is robust to ``sys.path`` variations inside worktrees.
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_COMPUTE_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "compute.py")


def _load_compute():
    spec = importlib.util.spec_from_file_location("chemistry_compute_under_test", _COMPUTE_PATH)
    assert spec is not None and spec.loader is not None, "compute spec failed"
    mod = importlib.util.module_from_spec(spec)
    # Python 3.14's @dataclass decorator inspects sys.modules[cls.__module__],
    # so the module must be registered before exec_module runs.
    sys.modules["chemistry_compute_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


compute = _load_compute()


# Minimal water geometry used across quantum fixtures (angstroms).
WATER_GEOMETRY = [
    {"symbol": "O", "x": 0.0, "y": 0.0, "z": 0.0},
    {"symbol": "H", "x": 0.0, "y": 0.0, "z": 0.96},
    {"symbol": "H", "x": 0.0, "y": 0.757, "z": -0.24},
]


# ---------------------------------------------------------------------------
# CHEM-011 QuantumJob contract — §7.3 adapter shape
# ---------------------------------------------------------------------------


class TestQuantumJobShape:
    def test_job_carries_geometry_charge_multiplicity_method_basis(self):
        job = compute.QuantumJob(
            geometry=WATER_GEOMETRY,
            charge=0,
            multiplicity=1,
            method="DFT",
            basis_set="6-31G*",
        )
        assert len(job.geometry) == 3
        assert job.charge == 0
        assert job.multiplicity == 1
        assert job.method == "DFT"
        assert job.basis_set == "6-31G*"

    def test_job_records_solvent_ecp_dispersion_resources(self):
        job = compute.QuantumJob(
            geometry=WATER_GEOMETRY,
            method="DFT",
            basis_set="def2-TZVP",
            solvent="water",
            ecp="def2-ECP",
            dispersion="D3",
            resources={"cores": 8, "memory_gb": 16, "walltime_s": 3600},
        )
        assert job.solvent == "water"
        assert job.ecp == "def2-ECP"
        assert job.dispersion == "D3"
        assert job.resources["cores"] == 8
        assert job.resources["walltime_s"] == 3600

    def test_job_rejects_unsupported_method(self):
        # Spec §10: methods are bounded; unknown method is a validation fail.
        report = compute.validate_quantum(
            compute.QuantumJob(
                geometry=WATER_GEOMETRY,
                method="NOT_A_METHOD",
                basis_set="6-31G*",
            ),
            compute.QuantumResult(converged=True, energy_hartree=-76.0),
        )
        checks = {c["check"]: c["status"] for c in report["verification"]}
        assert checks.get("method_supported") == "fail"

    def test_job_rejects_empty_geometry(self):
        # Empty geometry is a structural refusal (no atoms to compute on).
        report = compute.validate_quantum(
            compute.QuantumJob(geometry=[], method="HF", basis_set="6-31G*"),
            compute.QuantumResult(converged=True, energy_hartree=0.0),
        )
        assert report["status"] == "refused"
        checks = {c["check"]: c["status"] for c in report["verification"]}
        assert checks.get("geometry_present") == "fail"


# ---------------------------------------------------------------------------
# CHEM-011 §7.3: "An unconverged job never yields an unqualified property."
# ---------------------------------------------------------------------------


class TestUnconvergedJobNoQualifiedProperty:
    def test_unconverged_job_blocks_qualified_property(self):
        job = compute.QuantumJob(geometry=WATER_GEOMETRY, method="DFT", basis_set="6-31G*")
        result = compute.QuantumResult(
            converged=False,
            energy_hartree=-76.0,
            frequencies=[3650.0, 3756.0, 1595.0],
            diagnostics={"max_force": 1.5e-2, "iterations": 64},
        )
        report = compute.validate_quantum(job, result)
        # Spec: an unconverged job never yields an unqualified property.
        assert report["status"] != "succeeded"
        checks = {c["check"]: c["status"] for c in report["verification"]}
        assert checks.get("convergence") == "fail"
        # The qualified_property flag must be False even though an energy exists.
        assert report.get("qualified_property") is False

    def test_converged_job_can_yield_qualified_property(self):
        job = compute.QuantumJob(geometry=WATER_GEOMETRY, method="DFT", basis_set="6-31G*")
        result = compute.QuantumResult(
            converged=True,
            energy_hartree=-76.42,
            frequencies=[3650.0, 3756.0, 1595.0],
        )
        report = compute.validate_quantum(job, result)
        assert report.get("qualified_property") is True


# ---------------------------------------------------------------------------
# CHEM-011 §7.3 validation: electron/spin consistency
# ---------------------------------------------------------------------------


class TestElectronSpinConsistency:
    def test_neutral_closed_shell_water_passes(self):
        # Water: 10 electrons (8 O + 2 H), all paired -> multiplicity 1.
        job = compute.QuantumJob(
            geometry=WATER_GEOMETRY,
            charge=0,
            multiplicity=1,
            method="HF",
            basis_set="6-31G*",
        )
        result = compute.QuantumResult(converged=True, energy_hartree=-76.0)
        report = compute.validate_quantum(job, result)
        checks = {c["check"]: c["status"] for c in report["verification"]}
        assert checks.get("electron_spin_consistency") == "pass"

    def test_cation_water_radical_passes(self):
        # H2O+ : 9 electrons (one removed), one unpaired -> doublet, multiplicity=2.
        job = compute.QuantumJob(
            geometry=WATER_GEOMETRY,
            charge=+1,
            multiplicity=2,
            method="HF",
            basis_set="6-31G*",
        )
        result = compute.QuantumResult(converged=True, energy_hartree=-75.5)
        report = compute.validate_quantum(job, result)
        checks = {c["check"]: c["status"] for c in report["verification"]}
        assert checks.get("electron_spin_consistency") == "pass"

    def test_inconsistent_charge_spin_fails(self):
        # Water cation: nuclear=10, electrons=9 (odd) -> must be doublet.
        # Claiming singlet (mult=1) is parity-inconsistent.
        job = compute.QuantumJob(
            geometry=WATER_GEOMETRY,
            charge=+1,
            multiplicity=1,
            method="HF",
            basis_set="6-31G*",
        )
        result = compute.QuantumResult(converged=True, energy_hartree=-75.0)
        report = compute.validate_quantum(job, result)
        checks = {c["check"]: c["status"] for c in report["verification"]}
        assert checks.get("electron_spin_consistency") == "fail"


# ---------------------------------------------------------------------------
# CHEM-011 §7.3 validation: imaginary frequencies relative to job intent
# ---------------------------------------------------------------------------


class TestImaginaryFrequencies:
    def test_minimum_with_imaginary_freq_flagged(self):
        # A geometry optimization to a minimum should have NO imaginary freqs.
        job = compute.QuantumJob(
            geometry=WATER_GEOMETRY,
            method="DFT",
            basis_set="6-31G*",
            job_intent="minimum",
        )
        result = compute.QuantumResult(
            converged=True,
            energy_hartree=-76.42,
            frequencies=[-250.0, 3650.0, 3756.0],  # one imaginary
        )
        report = compute.validate_quantum(job, result)
        checks = {c["check"]: c["status"] for c in report["verification"]}
        assert checks.get("imaginary_frequencies") == "fail"

    def test_transition_state_requires_one_imaginary(self):
        # A transition-state search should have EXACTLY one imaginary freq.
        ts_geometry = [
            {"symbol": "H", "x": 0.0, "y": 0.0, "z": 0.0},
            {"symbol": "H", "x": 0.0, "y": 0.0, "z": 1.2},
        ]
        job = compute.QuantumJob(
            geometry=ts_geometry,
            method="MP2",
            basis_set="cc-pVDZ",
            job_intent="transition_state",
        )
        result = compute.QuantumResult(
            converged=True,
            energy_hartree=-1.15,
            frequencies=[-1200.0, 4400.0],  # one imaginary, one real
        )
        report = compute.validate_quantum(job, result)
        checks = {c["check"]: c["status"] for c in report["verification"]}
        assert checks.get("imaginary_frequencies") == "pass"


# ---------------------------------------------------------------------------
# CHEM-011 §7.3 validation: energy/unit consistency
# ---------------------------------------------------------------------------


class TestEnergyUnitConsistency:
    def test_hartree_to_ev_conversion_within_tolerance(self):
        # The parsed eV value must equal hartree * 27.2114 within tolerance.
        job = compute.QuantumJob(geometry=WATER_GEOMETRY, method="HF", basis_set="6-31G*")
        hartree = -76.0
        result = compute.QuantumResult(
            converged=True,
            energy_hartree=hartree,
            energy_eV=hartree * compute.HARTREE_TO_EV,
        )
        report = compute.validate_quantum(job, result)
        checks = {c["check"]: c["status"] for c in report["verification"]}
        assert checks.get("energy_unit_consistency") == "pass"

    def test_inconsistent_hartree_ev_pair_fails(self):
        job = compute.QuantumJob(geometry=WATER_GEOMETRY, method="HF", basis_set="6-31G*")
        result = compute.QuantumResult(
            converged=True,
            energy_hartree=-76.0,
            energy_eV=-999.0,  # clearly wrong
        )
        report = compute.validate_quantum(job, result)
        checks = {c["check"]: c["status"] for c in report["verification"]}
        assert checks.get("energy_unit_consistency") == "fail"


# ---------------------------------------------------------------------------
# CHEM-012 MolecularDynamicsJob contract — §7.4 adapter shape
# ---------------------------------------------------------------------------


class TestMolecularDynamicsJobShape:
    def test_job_carries_topology_force_field_ensemble_timestep(self):
        job = compute.MolecularDynamicsJob(
            topology="water_tip3p.top",
            force_field="TIP3P",
            ensemble="NVT",
            time_step_fs=2.0,
            n_steps=10000,
        )
        assert job.topology == "water_tip3p.top"
        assert job.force_field == "TIP3P"
        assert job.ensemble == "NVT"
        assert job.time_step_fs == 2.0

    def test_job_records_thermostat_barostat_seed_equilibration_production(self):
        job = compute.MolecularDynamicsJob(
            topology="peo.top",
            force_field="OPLS-AA",
            ensemble="NPT",
            thermostat="V-rescale",
            barostat="Parrinello-Rahman",
            time_step_fs=2.0,
            seed=42,
            equilibration_steps=1000,
            production_steps=10000,
        )
        assert job.thermostat == "V-rescale"
        assert job.barostat == "Parrinello-Rahman"
        assert job.seed == 42
        assert job.equilibration_steps == 1000
        assert job.production_steps == 10000

    def test_job_rejects_unsupported_ensemble(self):
        report = compute.validate_md(
            compute.MolecularDynamicsJob(
                topology="t.top",
                force_field="FF",
                ensemble="NVE-FAKE",
                time_step_fs=2.0,
            ),
            compute.MolecularDynamicsResult(
                converged=True,
                energy_traj_kJ_per_mol=[1.0, 1.0, 1.0],
                temperature_traj_K=[300.0, 300.0, 300.0],
            ),
        )
        checks = {c["check"]: c["status"] for c in report["verification"]}
        assert checks.get("ensemble_supported") == "fail"


# ---------------------------------------------------------------------------
# CHEM-012 §7.4 validation: energy drift
# ---------------------------------------------------------------------------


class TestMDEnergyDrift:
    def test_stable_energy_passes_drift_check(self):
        # NVE: total energy must not drift more than tolerance.
        job = compute.MolecularDynamicsJob(
            topology="water.top",
            force_field="TIP3P",
            ensemble="NVE",
            time_step_fs=1.0,
        )
        result = compute.MolecularDynamicsResult(
            converged=True,
            energy_traj_kJ_per_mol=[-100.0, -100.001, -100.002, -100.003],
            temperature_traj_K=[298.0, 298.0, 298.0, 298.0],
        )
        report = compute.validate_md(job, result)
        checks = {c["check"]: c["status"] for c in report["verification"]}
        assert checks.get("energy_drift") == "pass"

    def test_large_energy_drift_detected(self):
        job = compute.MolecularDynamicsJob(
            topology="water.top",
            force_field="TIP3P",
            ensemble="NVE",
            time_step_fs=1.0,
        )
        result = compute.MolecularDynamicsResult(
            converged=True,
            energy_traj_kJ_per_mol=[-100.0, -95.0, -88.0, -80.0],  # drifting badly
            temperature_traj_K=[298.0, 298.0, 298.0, 298.0],
        )
        report = compute.validate_md(job, result)
        checks = {c["check"]: c["status"] for c in report["verification"]}
        assert checks.get("energy_drift") == "fail"


# ---------------------------------------------------------------------------
# CHEM-012 §7.4 validation: temperature/pressure stability
# ---------------------------------------------------------------------------


class TestMDTemperaturePressureStability:
    def test_stable_temperature_passes(self):
        job = compute.MolecularDynamicsJob(
            topology="water.top",
            force_field="TIP3P",
            ensemble="NVT",
            time_step_fs=2.0,
            target_temperature_K=300.0,
        )
        result = compute.MolecularDynamicsResult(
            converged=True,
            energy_traj_kJ_per_mol=[-10.0, -10.0, -10.0],
            temperature_traj_K=[300.0, 299.5, 300.5, 300.1, 299.9],
        )
        report = compute.validate_md(job, result)
        checks = {c["check"]: c["status"] for c in report["verification"]}
        assert checks.get("temperature_stability") == "pass"

    def test_unstable_temperature_fails(self):
        job = compute.MolecularDynamicsJob(
            topology="water.top",
            force_field="TIP3P",
            ensemble="NVT",
            time_step_fs=2.0,
            target_temperature_K=300.0,
        )
        result = compute.MolecularDynamicsResult(
            converged=True,
            energy_traj_kJ_per_mol=[-10.0, -10.0, -10.0],
            temperature_traj_K=[300.0, 450.0, 200.0, 380.0, 250.0],
        )
        report = compute.validate_md(job, result)
        checks = {c["check"]: c["status"] for c in report["verification"]}
        assert checks.get("temperature_stability") == "fail"


# ---------------------------------------------------------------------------
# CHEM-012 §7.4 validation: sampling convergence
# ---------------------------------------------------------------------------


class TestMDSamplingConvergence:
    def test_converged_sampling_passes(self):
        # Block average over many samples with small variance -> converged.
        job = compute.MolecularDynamicsJob(
            topology="peo.top",
            force_field="OPLS-AA",
            ensemble="NPT",
            time_step_fs=2.0,
            production_steps=50000,
        )
        # Stable trajectory: small std -> block SEM small -> converged.
        temps = [300.0 + 0.1 * math.sin(i / 10.0) for i in range(200)]
        result = compute.MolecularDynamicsResult(
            converged=True,
            energy_traj_kJ_per_mol=[-100.0 + 0.01 * i for i in range(200)],
            temperature_traj_K=temps,
        )
        report = compute.validate_md(job, result)
        checks = {c["check"]: c["status"] for c in report["verification"]}
        assert checks.get("sampling_convergence") == "pass"

    def test_short_undersampled_run_fails_sampling(self):
        # Only 10 samples: statistically undersampled.
        job = compute.MolecularDynamicsJob(
            topology="peo.top",
            force_field="OPLS-AA",
            ensemble="NPT",
            time_step_fs=2.0,
            production_steps=10,
        )
        result = compute.MolecularDynamicsResult(
            converged=True,
            energy_traj_kJ_per_mol=[-100.0 + i for i in range(10)],
            temperature_traj_K=[300.0 + i for i in range(10)],
        )
        report = compute.validate_md(job, result)
        checks = {c["check"]: c["status"] for c in report["verification"]}
        assert checks.get("sampling_convergence") == "fail"


# ---------------------------------------------------------------------------
# CHEM-012 §7.4 validation: replicate agreement
# ---------------------------------------------------------------------------


class TestMDReplicateAgreement:
    def test_replicates_in_agreement_pass(self):
        job = compute.MolecularDynamicsJob(
            topology="peo.top",
            force_field="OPLS-AA",
            ensemble="NPT",
            time_step_fs=2.0,
        )
        # Replicate average energies all within 1 kJ/mol.
        result = compute.MolecularDynamicsResult(
            converged=True,
            energy_traj_kJ_per_mol=[-100.0, -100.0, -100.0],
            temperature_traj_K=[300.0, 300.0, 300.0],
            replicate_avg_energies_kJ_per_mol=[-100.0, -100.05, -99.98],
        )
        report = compute.validate_md(job, result)
        checks = {c["check"]: c["status"] for c in report["verification"]}
        assert checks.get("replicate_agreement") == "pass"

    def test_disagreeing_replicates_fail(self):
        job = compute.MolecularDynamicsJob(
            topology="peo.top",
            force_field="OPLS-AA",
            ensemble="NPT",
            time_step_fs=2.0,
        )
        result = compute.MolecularDynamicsResult(
            converged=True,
            energy_traj_kJ_per_mol=[-100.0, -100.0, -100.0],
            temperature_traj_K=[300.0, 300.0, 300.0],
            replicate_avg_energies_kJ_per_mol=[-100.0, -75.0, -120.0],
        )
        report = compute.validate_md(job, result)
        checks = {c["check"]: c["status"] for c in report["verification"]}
        assert checks.get("replicate_agreement") == "fail"
