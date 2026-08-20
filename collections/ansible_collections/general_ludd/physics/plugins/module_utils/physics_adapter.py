"""Typed dispatch adapter for the collection's physics Ansible module."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .latex_expert import LatexConfig, generate_paper, render_equation, write_latex_output
from .math_modeler import MathModelConfig, compute_statistics, solve_ode_exponential_decay, write_math_result
from .organic_synthesis import SynthesisConfig, lookup_molecule, predict_yield, write_synthesis_result
from .paper_reviewer import ReviewConfig, extract_findings, extract_sections, score_rigor, write_review_result
from .particle_experiment import ParticleConfig, analyze_decay_chain, compute_cross_section, write_particle_result
from .quantum_computer import QuantumConfig, solve_schrodinger, write_quantum_result
from .spectroscopy import SpectroscopyConfig, find_peaks, simulate_spectrum, write_spectroscopy_result
from .thermodynamics import (
    ThermoConfig,
    compute_entropy_change,
    compute_heat_transfer,
    compute_phase_change,
    write_thermo_result,
)

AnalysisResult = dict[str, Any]
AnalysisHandler = Callable[[Mapping[str, object], str], AnalysisResult]

_DEFAULT_PAPER_TEXT = """Abstract
We present a reproducible method for solving a physical system.

Methods
We benchmark the method against an exact baseline and report uncertainty.

Results
The method achieves 99% agreement with the reference result.

Conclusion
The measured evidence supports the stated result.
"""


def _string(parameters: Mapping[str, object], key: str, *, allow_empty: bool = False) -> str:
    value = parameters.get(key)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise ValueError(f"{key} must be {qualifier}")
    return value


def _float(parameters: Mapping[str, object], key: str) -> float:
    value = parameters.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be numeric")
    return float(value)


def _int(parameters: Mapping[str, object], key: str) -> int:
    value = parameters.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _latex(parameters: Mapping[str, object], output_dir: str) -> AnalysisResult:
    config = LatexConfig(
        document_class=_string(parameters, "document_class"),
        font_size=_string(parameters, "font_size"),
        title=_string(parameters, "title", allow_empty=True),
        author=_string(parameters, "author", allow_empty=True),
        output_format=_string(parameters, "output_format"),
    )
    document = generate_paper(config)
    document_path = write_latex_output(document, output_dir)
    equation = render_equation(r"E = mc^2", "eq:einstein")
    equation_path = write_latex_output(equation, output_dir, "equation.tex")
    return {
        "status": "success",
        "doc_path": str(document_path),
        "doc_lines": len(document.splitlines()),
        "eq_path": str(equation_path),
    }


def _math(parameters: Mapping[str, object], output_dir: str) -> AnalysisResult:
    config = MathModelConfig(
        model_type=_string(parameters, "model_type"),
        equation=_string(parameters, "equation"),
        initial_conditions={"y0": _float(parameters, "initial_y0")},
        parameters={"k": _float(parameters, "param_k")},
        time_range=(_float(parameters, "time_start"), _float(parameters, "time_end")),
        time_steps=_int(parameters, "time_steps"),
    )
    ode_result = solve_ode_exponential_decay(config)
    statistics = compute_statistics(ode_result["y_values"])
    path = write_math_result({"ode_solution": ode_result, "statistics": statistics}, output_dir)
    return {
        "status": "success",
        "path": str(path),
        "half_life": ode_result["half_life"],
        "mean_y": statistics.get("mean", 0),
    }


def _organic_synthesis(parameters: Mapping[str, object], output_dir: str) -> AnalysisResult:
    config = SynthesisConfig(
        target_molecule=_string(parameters, "target_molecule"),
        starting_material=_string(parameters, "starting_material"),
        solvent=_string(parameters, "solvent"),
        catalyst=_string(parameters, "catalyst", allow_empty=True),
        temperature_C=_float(parameters, "temperature_c"),
        reaction_time_min=_float(parameters, "reaction_time_min"),
    )
    molecule = lookup_molecule(config.target_molecule)
    yield_data = predict_yield(config)
    path = write_synthesis_result({"molecule": molecule, "yield_prediction": yield_data}, output_dir)
    return {
        "status": "success",
        "path": str(path),
        "molecule": config.target_molecule,
        "expected_yield_pct": yield_data.get("adjusted_yield_pct", 0),
    }


def _paper_review(parameters: Mapping[str, object], output_dir: str) -> AnalysisResult:
    paper_text = _string(parameters, "paper_text", allow_empty=True).strip() or _DEFAULT_PAPER_TEXT
    config = ReviewConfig(
        paper_title=_string(parameters, "paper_title", allow_empty=True),
        paper_text=paper_text,
        review_depth=_string(parameters, "review_depth"),
    )
    sections = extract_sections(config.paper_text)
    rigor = score_rigor(sections, config.paper_text)
    findings = extract_findings(config.paper_text)
    review = {
        "sections": {key: value[:100] for key, value in sections.items()},
        "rigor_scores": rigor,
        "findings_count": len(findings),
    }
    path = write_review_result(review, output_dir)
    return {
        "status": "success",
        "path": str(path),
        "n_sections": len(sections),
        "rigor": rigor.get("overall_rigor", 0),
        "n_findings": len(findings),
    }


def _particle_experiment(parameters: Mapping[str, object], output_dir: str) -> AnalysisResult:
    config = ParticleConfig(
        beam_energy_GeV=_float(parameters, "beam_energy_gev"),
        target=_string(parameters, "target"),
        beam=_string(parameters, "beam"),
        detector=_string(parameters, "detector"),
        luminosity_inv_fb=_float(parameters, "luminosity_inv_fb"),
        analysis_channel=_string(parameters, "analysis_channel"),
    )
    cross_section = compute_cross_section(config)
    decay = analyze_decay_chain(
        "Higgs",
        1.56e-22,
        {"ZZ": 0.0264, "WW": 0.215, "gamma_gamma": 0.00227, "bb": 0.582, "tautau": 0.0627},
    )
    path = write_particle_result({"cross_section": cross_section, "decay_chain": decay}, output_dir)
    return {"status": "success", "path": str(path), "expected_events": cross_section["expected_events"]}


def _quantum(parameters: Mapping[str, object], output_dir: str) -> AnalysisResult:
    config = QuantumConfig(
        problem=_string(parameters, "problem"),
        well_width_nm=_float(parameters, "well_width_nm"),
        particle=_string(parameters, "particle"),
        potential=_string(parameters, "potential"),
        dimensions=_int(parameters, "dimensions"),
        num_states=_int(parameters, "num_states"),
        solver=_string(parameters, "solver"),
    )
    result = solve_schrodinger(config)
    path = write_quantum_result(result, output_dir)
    energies = result["energies_eV"]
    return {
        "status": "success",
        "path": str(path),
        "n_states": len(energies),
        "ground_state_eV": round(float(energies[0]), 6),
    }


def _spectroscopy(parameters: Mapping[str, object], output_dir: str) -> AnalysisResult:
    config = SpectroscopyConfig(
        technique=_string(parameters, "technique"),
        wavelength_range_nm=(_float(parameters, "wl_min_nm"), _float(parameters, "wl_max_nm")),
        resolution_nm=_float(parameters, "resolution_nm"),
        solvent=_string(parameters, "solvent"),
        temperature_C=_float(parameters, "temperature_c"),
        peak_detection_threshold=_float(parameters, "peak_threshold"),
        peaks=[
            {"center_nm": 280, "amplitude": 1.0, "sigma_nm": 5.0},
            {"center_nm": 380, "amplitude": 0.5, "sigma_nm": 8.0},
            {"center_nm": 550, "amplitude": 0.3, "sigma_nm": 10.0},
        ],
    )
    spectrum = simulate_spectrum(config)
    peaks = find_peaks(
        spectrum["wavelengths_nm"],
        spectrum["intensities"],
        config.peak_detection_threshold,
    )
    path = write_spectroscopy_result({"spectrum": spectrum, "detected_peaks": peaks}, output_dir)
    return {"status": "success", "path": str(path), "n_peaks_detected": len(peaks)}


def _thermodynamics(parameters: Mapping[str, object], output_dir: str) -> AnalysisResult:
    config = ThermoConfig(
        substance=_string(parameters, "substance"),
        mass_kg=_float(parameters, "mass_kg"),
        initial_temp_C=_float(parameters, "initial_temp_c"),
        final_temp_C=_float(parameters, "final_temp_c"),
        pressure_atm=_float(parameters, "pressure_atm"),
    )
    result = {
        "heat_transfer": compute_heat_transfer(config),
        "phase_change": compute_phase_change(config),
        "entropy_change": compute_entropy_change(config),
    }
    path = write_thermo_result(result, output_dir)
    return {
        "status": "success",
        "path": str(path),
        "heat_kJ": result["heat_transfer"]["heat_transfer_kJ"],
        "entropy_J_K": result["entropy_change"]["entropy_change_J_K"],
    }


_HANDLERS: dict[str, AnalysisHandler] = {
    "latex": _latex,
    "math": _math,
    "organic_synthesis": _organic_synthesis,
    "paper_review": _paper_review,
    "particle_experiment": _particle_experiment,
    "quantum": _quantum,
    "spectroscopy": _spectroscopy,
    "thermodynamics": _thermodynamics,
}


def run_analysis(operation: str, parameters: Mapping[str, object], output_dir: str) -> AnalysisResult:
    """Run one packaged physics operation and return its stable role contract."""
    if not output_dir:
        raise ValueError("output_dir must be a non-empty path")
    handler = _HANDLERS.get(operation)
    if handler is None:
        raise ValueError(f"unsupported physics operation: {operation}")
    return handler(parameters, output_dir)


__all__ = ["run_analysis"]
