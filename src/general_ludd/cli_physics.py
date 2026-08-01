"""CLI subcommand: ``gludd physics`` — computational physics, chemistry, and math toolkit.

``gludd physics quantum solve``
    Solve the Schrodinger equation for a quantum mechanical problem.

``gludd physics particle analyze``
    Analyze particle collision data: cross-sections and decay chains.

``gludd physics spectroscopy simulate``
    Simulate and analyze spectroscopic data with peak detection.

``gludd physics thermo compute``
    Compute heat transfer, phase changes, and entropy.

``gludd physics synthesis plan``
    Plan organic synthesis routes and predict yields.

``gludd physics math solve``
    Solve ODEs, perform regression, and compute statistics.

``gludd physics latex generate``
    Generate LaTeX documents and render equations.

``gludd physics review analyze``
    Analyze research papers and score scientific rigor.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import no_type_check

from general_ludd.security.state import project_state

_COLLECTIONS_PARENT = Path(__file__).resolve().parents[2] / "collections"
_COLLECTIONS_ROOT = _COLLECTIONS_PARENT / "ansible_collections"
_PHYSICS_PLUGINS = _COLLECTIONS_ROOT / "general_ludd" / "physics" / "plugins"
for _path in (str(_COLLECTIONS_PARENT), str(_PHYSICS_PLUGINS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _physics_output_dir(name: str) -> str:
    return str(project_state().directory("physics", name))


def _run_quantum(args: argparse.Namespace) -> None:
    from ansible_collections.general_ludd.physics.plugins.module_utils.quantum_computer import (
        QuantumConfig,
        solve_schrodinger,
        write_quantum_result,
    )

    config = QuantumConfig(
        problem=args.problem,
        well_width_nm=args.well_width_nm,
        particle=args.particle,
        potential=args.potential,
        dimensions=args.dimensions,
        num_states=args.num_states,
        solver=args.solver,
    )
    result = solve_schrodinger(config)
    output_path = write_quantum_result(result, args.output_dir)
    print(json.dumps({
        "status": "success",
        "path": str(output_path),
        "ground_state_eV": round(result["energies_eV"][0], 6),
        "n_states": len(result["energies_eV"]),
    }, indent=2))


def _run_particle(args: argparse.Namespace) -> None:
    from ansible_collections.general_ludd.physics.plugins.module_utils.particle_experiment import (
        ParticleConfig,
        analyze_decay_chain,
        compute_cross_section,
        write_particle_result,
    )

    config = ParticleConfig(
        beam_energy_GeV=args.beam_energy_GeV,
        target=args.target,
        beam=args.beam,
        detector=args.detector,
        luminosity_inv_fb=args.luminosity_inv_fb,
        analysis_channel=args.channel,
    )
    xs_result = compute_cross_section(config)
    decay = analyze_decay_chain(
        args.decay_particle, args.decay_lifetime_s,
        json.loads(args.branching_ratios) if args.branching_ratios else {},
    )
    result = {"cross_section": xs_result, "decay_chain": decay}
    output_path = write_particle_result(result, args.output_dir)
    print(json.dumps({
        "status": "success",
        "path": str(output_path),
        "expected_events": xs_result["expected_events"],
        "cross_section_pb": xs_result["cross_section_pb"],
    }, indent=2))


def _run_spectroscopy(args: argparse.Namespace) -> None:
    from ansible_collections.general_ludd.physics.plugins.module_utils.spectroscopy import (
        SpectroscopyConfig,
        find_peaks,
        simulate_spectrum,
        write_spectroscopy_result,
    )

    peaks = json.loads(args.peaks) if args.peaks else [
        {"center_nm": 280, "amplitude": 1.0, "sigma_nm": 5.0},
        {"center_nm": 380, "amplitude": 0.5, "sigma_nm": 8.0},
        {"center_nm": 550, "amplitude": 0.3, "sigma_nm": 10.0},
    ]
    config = SpectroscopyConfig(
        technique=args.technique,
        wavelength_range_nm=(args.wl_min, args.wl_max),
        resolution_nm=args.resolution,
        solvent=args.solvent,
        temperature_C=args.temperature,
        peak_detection_threshold=args.peak_threshold,
        peaks=peaks,
    )
    spectrum = simulate_spectrum(config)
    detected = find_peaks(spectrum["wavelengths_nm"], spectrum["intensities"],
                          config.peak_detection_threshold)
    result = {"spectrum": spectrum, "detected_peaks": detected}
    output_path = write_spectroscopy_result(result, args.output_dir)
    print(json.dumps({
        "status": "success",
        "path": str(output_path),
        "n_peaks_detected": len(detected),
    }, indent=2))


def _run_thermo(args: argparse.Namespace) -> None:
    from ansible_collections.general_ludd.physics.plugins.module_utils.thermodynamics import (
        ThermoConfig,
        compute_entropy_change,
        compute_heat_transfer,
        compute_phase_change,
        write_thermo_result,
    )

    config = ThermoConfig(
        substance=args.substance,
        mass_kg=args.mass,
        initial_temp_C=args.initial_temp,
        final_temp_C=args.final_temp,
        pressure_atm=args.pressure,
    )
    result = {
        "heat_transfer": compute_heat_transfer(config),
        "phase_change": compute_phase_change(config),
        "entropy_change": compute_entropy_change(config),
    }
    output_path = write_thermo_result(result, args.output_dir)
    print(json.dumps({
        "status": "success",
        "path": str(output_path),
        "heat_kJ": result["heat_transfer"]["heat_transfer_kJ"],
        "entropy_J_K": result["entropy_change"]["entropy_change_J_K"],
    }, indent=2))


def _run_synthesis(args: argparse.Namespace) -> None:
    from ansible_collections.general_ludd.physics.plugins.module_utils.organic_synthesis import (
        SynthesisConfig,
        lookup_molecule,
        predict_yield,
        write_synthesis_result,
    )

    config = SynthesisConfig(
        target_molecule=args.molecule,
        starting_material=args.starting_material,
        solvent=args.solvent,
        catalyst=args.catalyst,
        temperature_C=args.temperature,
        reaction_time_min=args.reaction_time,
    )
    molecule = lookup_molecule(config.target_molecule)
    yield_data = predict_yield(config)
    result = {"molecule": molecule, "yield_prediction": yield_data}
    output_path = write_synthesis_result(result, args.output_dir)
    yield_pct = yield_data.get("adjusted_yield_pct", 0)
    print(json.dumps({
        "status": "success",
        "path": str(output_path),
        "molecule": config.target_molecule,
        "expected_yield_pct": yield_pct,
    }, indent=2))


def _run_math(args: argparse.Namespace) -> None:
    from ansible_collections.general_ludd.physics.plugins.module_utils.math_modeler import (
        MathModelConfig,
        compute_statistics,
        solve_ode_exponential_decay,
        write_math_result,
    )

    config = MathModelConfig(
        model_type=args.model_type,
        equation=args.equation,
        initial_conditions={"y0": args.y0},
        parameters={"k": args.rate_k},
        time_range=(args.time_start, args.time_end),
        time_steps=args.time_steps,
    )
    ode_result = solve_ode_exponential_decay(config)
    stats = compute_statistics(ode_result["y_values"])
    result = {"ode_solution": ode_result, "statistics": stats}
    output_path = write_math_result(result, args.output_dir)
    print(json.dumps({
        "status": "success",
        "path": str(output_path),
        "half_life": ode_result["half_life"],
        "final_y": ode_result["y_values"][-1],
    }, indent=2))


def _latex_output_stem(args: argparse.Namespace) -> str:
    parts = [args.document_class, args.font_size, args.title, args.author]
    raw = "-".join(str(part).strip().lower() for part in parts if str(part).strip())
    stem = "".join(ch if ch.isalnum() else "-" for ch in raw).strip("-")
    return "-".join(part for part in stem.split("-") if part) or "paper"


def _run_latex(args: argparse.Namespace) -> None:
    from ansible_collections.general_ludd.physics.plugins.module_utils.latex_expert import (
        LatexConfig,
        generate_paper,
        render_equation,
        write_latex_output,
    )

    config = LatexConfig(
        document_class=args.document_class,
        font_size=args.font_size,
        title=args.title,
        author=args.author,
    )
    doc = generate_paper(config)
    output_stem = _latex_output_stem(args)
    doc_path = write_latex_output(doc, args.output_dir, f"{output_stem}.tex")
    eqn = render_equation(args.equation, "eq:rendered")
    eq_path = write_latex_output(eqn, args.output_dir, f"{output_stem}-equation.tex")
    print(json.dumps({
        "status": "success",
        "doc_path": str(doc_path),
        "doc_lines": len(doc.splitlines()),
        "eq_path": str(eq_path),
    }, indent=2))


def _run_review(args: argparse.Namespace) -> None:
    paper_text = args.text
    if args.file:
        paper_text = Path(args.file).read_text()

    if not paper_text.strip():
        print("error: no paper text provided (use --text or --file)", file=sys.stderr)
        raise SystemExit(2)

    from ansible_collections.general_ludd.physics.plugins.module_utils.paper_reviewer import (
        ReviewConfig,
        extract_findings,
        extract_sections,
        score_rigor,
        write_review_result,
    )

    ReviewConfig(
        paper_title=args.title,
        paper_text=paper_text,
        review_depth=args.depth,
    )
    sections = extract_sections(paper_text)
    rigor = score_rigor(sections, paper_text)
    findings = extract_findings(paper_text)
    result = {
        "sections": list(sections.keys()),
        "rigor_scores": rigor,
        "findings_count": len(findings),
    }
    output_path = write_review_result(result, args.output_dir)
    print(json.dumps({
        "status": "success",
        "path": str(output_path),
        "n_sections": len(sections),
        "rigor": rigor.get("overall_rigor", 0),
        "n_findings": len(findings),
    }, indent=2))


def _check_collection_import() -> None:
    _required = [
        "latex_expert",
        "math_modeler",
        "organic_synthesis",
        "paper_reviewer",
        "particle_experiment",
        "quantum_computer",
        "spectroscopy",
        "thermodynamics",
    ]
    missing: list[str] = []
    for _m in _required:
        try:
            __import__(
                f"ansible_collections.general_ludd.physics.plugins.module_utils.{_m}",
            )
        except ImportError:
            missing.append(_m)
    if missing:
        print(
            f"error: physics collection modules missing: {', '.join(missing)}\n"
            "Ensure PYTHONPATH includes the project root or run from the gludd workspace.",
            file=sys.stderr,
        )
        raise SystemExit(1)


@no_type_check
def add_physics_subparser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    physics_parser = sub.add_parser(
        "physics", help="Computational physics, chemistry, and math toolkit"
    )
    physics_parser.set_defaults(func=None)
    phys_sub = physics_parser.add_subparsers(dest="physics_command")

    # --- quantum ---
    quantum_p = phys_sub.add_parser("quantum", help="Quantum mechanics solver")
    quantum_p.add_argument("--problem", default="infinite_square_well",
                           choices=["infinite_square_well", "finite_square_well",
                                    "harmonic_oscillator", "hydrogen_atom", "double_well"])
    quantum_p.add_argument("--well-width-nm", type=float, default=1.0)
    quantum_p.add_argument("--particle", default="electron",
                           choices=["electron", "proton", "neutron"])
    quantum_p.add_argument("--potential", default="square_well",
                           choices=["square_well", "harmonic", "coulomb", "kronig_penney"])
    quantum_p.add_argument("--dimensions", type=int, default=1, choices=[1, 2, 3])
    quantum_p.add_argument("--num-states", type=int, default=5)
    quantum_p.add_argument("--solver", default="numpy",
                           choices=["numpy", "scipy", "analytical"])
    quantum_p.add_argument("--output-dir", default=_physics_output_dir("quantum"))
    quantum_p.set_defaults(func=_run_quantum)

    # --- particle ---
    particle_p = phys_sub.add_parser("particle", help="Particle experiment analysis")
    particle_p.add_argument("--beam-energy-GeV", type=float, default=13.6)
    particle_p.add_argument("--target", default="proton")
    particle_p.add_argument("--beam", default="proton")
    particle_p.add_argument("--detector", default="generic_4pi",
                            choices=["generic_4pi", "atlas", "cms", "alice", "lhcb"])
    particle_p.add_argument("--luminosity-inv-fb", type=float, default=139.0)
    particle_p.add_argument("--channel", default="H_to_ZZ_to_4l")
    particle_p.add_argument("--decay-particle", default="Higgs")
    particle_p.add_argument("--decay-lifetime-s", type=float, default=1.56e-22)
    particle_p.add_argument("--branching-ratios", default=None,
                            help="JSON dict of branching ratios")
    particle_p.add_argument("--output-dir", default=_physics_output_dir("particle"))
    particle_p.set_defaults(func=_run_particle)

    # --- spectroscopy ---
    spectro_p = phys_sub.add_parser("spectroscopy", help="Spectroscopy analysis")
    spectro_p.add_argument("--technique", default="uv_vis",
                           choices=["uv_vis", "ir", "raman", "nmr", "mass_spec",
                                    "fluorescence", "xrd"])
    spectro_p.add_argument("--wl-min", type=float, default=200.0)
    spectro_p.add_argument("--wl-max", type=float, default=800.0)
    spectro_p.add_argument("--resolution", type=float, default=1.0)
    spectro_p.add_argument("--solvent", default="water")
    spectro_p.add_argument("--temperature", type=float, default=25.0)
    spectro_p.add_argument("--peak-threshold", type=float, default=0.1)
    spectro_p.add_argument("--peaks", default=None,
                           help="JSON list of peak dicts")
    spectro_p.add_argument("--output-dir", default=_physics_output_dir("spectroscopy"))
    spectro_p.set_defaults(func=_run_spectroscopy)

    # --- thermo ---
    thermo_p = phys_sub.add_parser("thermo", help="Thermodynamics computation")
    thermo_p.add_argument("--substance", default="water",
                          choices=["water", "ethanol", "iron", "aluminum",
                                   "copper", "air", "helium", "nitrogen"])
    thermo_p.add_argument("--mass", type=float, default=1.0, help="Mass in kg")
    thermo_p.add_argument("--initial-temp", type=float, default=25.0,
                          help="Initial temperature (C)")
    thermo_p.add_argument("--final-temp", type=float, default=100.0,
                          help="Final temperature (C)")
    thermo_p.add_argument("--pressure", type=float, default=1.0, help="Pressure (atm)")
    thermo_p.add_argument("--output-dir", default=_physics_output_dir("thermo"))
    thermo_p.set_defaults(func=_run_thermo)

    # --- synthesis ---
    synth_p = phys_sub.add_parser("synthesis", help="Organic synthesis planner")
    synth_p.add_argument("--molecule", default="aspirin",
                         choices=["aspirin", "paracetamol"])
    synth_p.add_argument("--starting-material", default="salicylic_acid")
    synth_p.add_argument("--solvent", default="acetic_anhydride")
    synth_p.add_argument("--catalyst", default="sulfuric_acid")
    synth_p.add_argument("--temperature", type=float, default=85.0,
                         help="Reaction temperature (C)")
    synth_p.add_argument("--reaction-time", type=float, default=15.0,
                         help="Reaction time (min)")
    synth_p.add_argument("--output-dir", default=_physics_output_dir("synthesis"))
    synth_p.set_defaults(func=_run_synthesis)

    # --- math ---
    math_p = phys_sub.add_parser("math", help="Mathematical modeling and statistics")
    math_p.add_argument("--model-type", default="ode_first_order")
    math_p.add_argument("--equation", default="dy/dt = -k * y")
    math_p.add_argument("--y0", type=float, default=1.0)
    math_p.add_argument("--rate-k", type=float, default=0.5, help="Rate constant k")
    math_p.add_argument("--time-start", type=float, default=0.0)
    math_p.add_argument("--time-end", type=float, default=10.0)
    math_p.add_argument("--time-steps", type=int, default=100)
    math_p.add_argument("--output-dir", default=_physics_output_dir("math"))
    math_p.set_defaults(func=_run_math)

    # --- latex ---
    latex_p = phys_sub.add_parser("latex", help="LaTeX document generation")
    latex_p.add_argument("--document-class", default="article",
                         choices=["article", "report", "book", "beamer", "letter"])
    latex_p.add_argument("--font-size", default="11pt",
                         choices=["10pt", "11pt", "12pt"])
    latex_p.add_argument("--title", default="Generated Document")
    latex_p.add_argument("--author", default="Agentic Harness")
    latex_p.add_argument("--equation", default=r"E = mc^2",
                         help="LaTeX equation to render")
    latex_p.add_argument("--output-dir", default=_physics_output_dir("latex"))
    latex_p.set_defaults(func=_run_latex)

    # --- review ---
    review_p = phys_sub.add_parser("review", help="Research paper reviewer")
    review_p.add_argument("--title", default="", help="Paper title")
    review_p.add_argument("--text", default="", help="Paper text to review")
    review_p.add_argument("--file", default=None,
                          help="File containing paper text")
    review_p.add_argument("--depth", default="standard",
                          choices=["quick", "standard", "deep", "meta_review"])
    review_p.add_argument("--output-dir", default=_physics_output_dir("review"))
    review_p.set_defaults(func=_run_review)


__all__ = [
    "add_physics_subparser",
]
