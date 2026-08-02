"""Physics collection module_utils — all computational physics, chemistry, and math utilities."""

from .latex_expert import (
    LatexConfig,
    generate_paper,
    render_align,
    render_equation,
    render_table,
    write_latex_output,
)
from .math_modeler import (
    MathModelConfig,
    compute_statistics,
    solve_linear_regression,
    solve_ode_exponential_decay,
    write_math_result,
)
from .organic_synthesis import (
    SynthesisConfig,
    lookup_molecule,
    predict_yield,
    retrosynthesis_analysis,
    write_synthesis_result,
)
from .paper_reviewer import (
    ReviewConfig,
    count_equations,
    extract_findings,
    extract_sections,
    score_rigor,
    write_review_result,
)
from .particle_experiment import (
    ParticleConfig,
    analyze_decay_chain,
    compute_cross_section,
    write_particle_result,
)
from .quantum_computer import (
    QuantumConfig,
    solve_schrodinger,
    write_quantum_result,
)
from .spectroscopy import (
    SpectroscopyConfig,
    find_peaks,
    simulate_spectrum,
    write_spectroscopy_result,
)
from .thermodynamics import (
    ThermoConfig,
    compute_entropy_change,
    compute_heat_transfer,
    compute_phase_change,
    write_thermo_result,
)

__all__ = [
    "LatexConfig",
    "MathModelConfig",
    "ParticleConfig",
    "QuantumConfig",
    "ReviewConfig",
    "SpectroscopyConfig",
    "SynthesisConfig",
    "ThermoConfig",
    "analyze_decay_chain",
    "compute_cross_section",
    "compute_entropy_change",
    "compute_heat_transfer",
    "compute_phase_change",
    "compute_statistics",
    "count_equations",
    "extract_findings",
    "extract_sections",
    "find_peaks",
    "generate_paper",
    "lookup_molecule",
    "predict_yield",
    "render_align",
    "render_equation",
    "render_table",
    "retrosynthesis_analysis",
    "score_rigor",
    "simulate_spectrum",
    "solve_linear_regression",
    "solve_ode_exponential_decay",
    "solve_schrodinger",
    "write_latex_output",
    "write_math_result",
    "write_particle_result",
    "write_quantum_result",
    "write_review_result",
    "write_spectroscopy_result",
    "write_synthesis_result",
    "write_thermo_result",
]
